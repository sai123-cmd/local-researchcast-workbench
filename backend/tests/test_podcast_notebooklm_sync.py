from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import json_dumps, json_loads, row_to_dict, utc_now  # noqa: E402
from app.services.podcast import NOTEBOOKLM_READY_STATUS, PodcastService  # noqa: E402


class FakeSettings:
    def __init__(self, podcasts_dir: Path) -> None:
        self.podcasts_dir = podcasts_dir
        self.notebooklm_bin = podcasts_dir / "missing-notebooklm.exe"
        self.notebooklm_profile = "work"
        self.tts_provider = "windows"


class FakePodcastService(PodcastService):
    def __init__(self, settings: FakeSettings, auth_ok: bool) -> None:
        super().__init__(settings)  # type: ignore[arg-type]
        self.auth_ok = auth_ok
        self.generated: list[tuple[int, bool]] = []

    def check_notebooklm_auth(self) -> tuple[bool, str]:
        return self.auth_ok, "ok" if self.auth_ok else "login expired"

    def generate_notebooklm_audio(
        self,
        pack_id: int,
        title: str,
        script: str,
        check_auth: bool = True,
        source_text: str | None = None,
    ) -> tuple[str, Path | None]:
        self.generated.append((pack_id, check_auth))
        audio_path = self.settings.podcasts_dir / f"{pack_id}-notebooklm.mp3"
        audio_path.write_bytes(b"mp3")
        return NOTEBOOKLM_READY_STATUS, audio_path


class PodcastNotebookLMSyncTest(unittest.TestCase):
    def test_notebooklm_commands_include_configured_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = FakeSettings(Path(tmp))
            settings.notebooklm_bin.write_text("", encoding="utf-8")
            service = PodcastService(settings)  # type: ignore[arg-type]

            cmd = service.notebooklm_cmd("auth", "check")

            self.assertEqual(cmd[:3], [str(settings.notebooklm_bin), "--profile", "work"])
            self.assertEqual(cmd[-2:], ["auth", "check"])

    def test_missing_auth_marks_pending_pack_without_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _connect()
            pack_id = _insert_pack(conn, "Battery spec")
            service = FakePodcastService(FakeSettings(Path(tmp)), auth_ok=False)

            result = service.sync_pending_notebooklm(conn, limit=2)

            pack = _fetch_pack(conn, pack_id)
            provenance = json_loads(pack["provenance_json"], {})
            self.assertTrue(result["auth_required"])
            self.assertEqual(result["attempted"], 0)
            self.assertEqual(pack["notebooklm_status"], "auth_required")
            self.assertEqual(service.generated, [])
            self.assertEqual(provenance["notebooklm_sync"]["last_error"], "login expired")

    def test_valid_auth_generates_and_records_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _connect()
            pack_id = _insert_pack(conn, "Camera module")
            service = FakePodcastService(FakeSettings(Path(tmp)), auth_ok=True)

            result = service.sync_pending_notebooklm(conn, limit=2)

            pack = _fetch_pack(conn, pack_id)
            self.assertEqual(result["ready"], 1)
            self.assertEqual(pack["notebooklm_status"], NOTEBOOKLM_READY_STATUS)
            self.assertTrue(str(pack["notebooklm_audio_path"]).endswith("1-notebooklm.mp3"))
            self.assertEqual(service.generated, [(pack_id, False)])

    def test_retry_cooldown_can_be_forced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _connect()
            recent = datetime.now(timezone.utc) - timedelta(minutes=20)
            _insert_pack(
                conn,
                "Certification",
                status="failed: network",
                provenance={
                    "notebooklm_sync": {
                        "last_attempt_at": recent.isoformat(),
                        "last_status": "failed: network",
                    }
                },
            )
            service = FakePodcastService(FakeSettings(Path(tmp)), auth_ok=True)

            self.assertEqual(service.pending_notebooklm_packs(conn, limit=1), [])
            self.assertEqual(len(service.pending_notebooklm_packs(conn, limit=1, force=True)), 1)

    def test_regenerating_today_pack_requeues_existing_notebooklm_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _connect()
            now = utc_now()
            day = datetime.now().date().isoformat()
            title = f"今日学习音频 {day}"
            old_audio = Path(tmp) / "old-notebooklm.mp3"
            old_audio.write_bytes(b"old")
            conn.execute(
                """
                INSERT INTO knowledge_items(
                    domain, title, urgency, novelty_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("battery", "Battery spec", "high", 0.9, now, now),
            )
            cur = conn.execute(
                """
                INSERT INTO learning_packs(
                    title, status, knowledge_item_ids_json, script_text,
                    notebooklm_audio_path, notebooklm_status, provenance_json,
                    created_at, updated_at
                ) VALUES (?, 'local_audio_ready', '[1]', ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    "old script",
                    str(old_audio),
                    NOTEBOOKLM_READY_STATUS,
                    json_dumps({"notebooklm_sync": {"last_status": NOTEBOOKLM_READY_STATUS}}),
                    now,
                    now,
                ),
            )
            pack_id = int(cur.lastrowid)
            service = PodcastService(FakeSettings(Path(tmp)))  # type: ignore[arg-type]

            with patch("app.services.podcast.AIService.learning_script", new=AsyncMock(return_value="new script")):
                asyncio.run(service.create_today_learning_pack(conn, regenerate=True))

            pack = _fetch_pack(conn, pack_id)
            provenance = json_loads(pack["provenance_json"], {})
            pending = service.pending_notebooklm_packs(conn, limit=1)
            self.assertEqual(pack["script_text"], "new script")
            self.assertEqual(pack["notebooklm_status"], "pending")
            self.assertIsNone(pack["notebooklm_audio_path"])
            self.assertEqual(provenance["previous_notebooklm_sync"]["last_status"], NOTEBOOKLM_READY_STATUS)
            self.assertEqual([row["id"] for row in pending], [pack_id])

    def test_notebooklm_source_contains_knowledge_and_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _connect()
            now = utc_now()
            event_id = conn.execute(
                """
                INSERT INTO raw_events(
                    source, source_id, source_type, title, body, received_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("wechat", "msg-1", "message", "Battery supplier", "753028 cell spec: 700mAh and UN38.3 pending", now),
            ).lastrowid
            service = PodcastService(FakeSettings(Path(tmp)))  # type: ignore[arg-type]

            source = service.source_text_for_items(
                conn,
                [
                    {
                        "id": 1,
                        "title": "Battery pack risk",
                        "domain": "battery",
                        "summary": "Need to verify the supplier cell.",
                        "work_relevance": "Impacts 当前硬件项目 battery life and shipping.",
                        "source_event_ids": [event_id],
                        "key_questions": ["Can you provide UN38.3?"],
                        "followups": ["Ask for cycle-life data"],
                    }
                ],
                "当前项目 battery study",
                "local draft",
            )

            self.assertIn("NotebookLM Audio Overview", source)
            self.assertIn("Battery pack risk", source)
            self.assertIn("753028 cell spec", source)
            self.assertIn("Can you provide UN38.3?", source)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE learning_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            knowledge_item_ids_json TEXT NOT NULL DEFAULT '[]',
            script_text TEXT NOT NULL DEFAULT '',
            local_audio_path TEXT,
            notebooklm_audio_path TEXT,
            notebooklm_status TEXT NOT NULL DEFAULT 'not_requested',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE knowledge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            title TEXT NOT NULL,
            raw_excerpt TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            glossary_json TEXT NOT NULL DEFAULT '[]',
            key_questions_json TEXT NOT NULL DEFAULT '[]',
            impact_json TEXT NOT NULL DEFAULT '{}',
            follow_up_questions_json TEXT NOT NULL DEFAULT '[]',
            novelty_score REAL NOT NULL DEFAULT 0,
            urgency TEXT NOT NULL DEFAULT 'medium',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            author TEXT,
            conversation TEXT,
            happened_at TEXT,
            received_at TEXT NOT NULL,
            processed_at TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT
        );
        """
    )
    return conn


def _insert_pack(
    conn: sqlite3.Connection,
    title: str,
    status: str = "not_requested",
    provenance: dict[str, Any] | None = None,
) -> int:
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO learning_packs(
            title, status, script_text, notebooklm_status, provenance_json, created_at, updated_at
        ) VALUES (?, 'local_audio_ready', ?, ?, ?, ?, ?)
        """,
        (title, f"Learning script for {title}", status, json_dumps(provenance or {}), now, now),
    )
    return int(cur.lastrowid)


def _fetch_pack(conn: sqlite3.Connection, pack_id: int) -> dict[str, Any]:
    row = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone())
    assert row is not None
    return row


if __name__ == "__main__":
    unittest.main()

