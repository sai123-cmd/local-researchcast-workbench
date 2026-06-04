from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import json_dumps, row_to_dict, utc_now  # noqa: E402
from app.services import researchcast as researchcast_module  # noqa: E402
from app.services.researchcast import ResearchCastService  # noqa: E402


class FakeSettings:
    def __init__(self, podcasts_dir: Path) -> None:
        self.podcasts_dir = podcasts_dir
        self.llm_base_url = "https://api.minimaxi.com/v1"
        self.llm_api_key = ""
        self.llm_model = "MiniMax-M2.7-highspeed"
        self.minimax_tts_model = "speech-2.8-hd"
        self.minimax_tts_voice = "audiobook_male_1"

    @property
    def llm_configured(self) -> bool:
        return False


class FakeResearchCastService(ResearchCastService):
    async def synthesize_minimax_tts(self, pack, script, wait_seconds=90):
        audio_path = self.settings.podcasts_dir / f"{pack['id']}-researchcast.mp3"
        audio_path.write_bytes(b"mp3")
        return "audio_ready", audio_path, "task-1", ""


class ResearchCastTest(unittest.TestCase):
    def test_collect_sources_keeps_raw_evidence_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _connect()
            event_id = _insert_event(conn, "Battery supplier", "753028 cell 700mAh, UN38.3 pending")
            item_id = _insert_knowledge(conn, "Battery supplier threshold", [event_id])
            service = ResearchCastService(FakeSettings(Path(tmp)))  # type: ignore[arg-type]
            item = row_to_dict(conn.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone())

            manifest = service.collect_sources(conn, [item])

            self.assertEqual(manifest["knowledge_items"][0]["title"], "Battery supplier threshold")
            self.assertIn("753028 cell", manifest["raw_events"][0]["body"])
            self.assertEqual(manifest["web_research"]["enabled"], False)

    def test_create_learning_pack_outputs_blog_script_and_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _connect()
            event_id = _insert_event(conn, "Camera module", "OV sensor FOV and low-light spec question")
            item_id = _insert_knowledge(conn, "Camera module spec", [event_id], domain="camera")
            service = FakeResearchCastService(FakeSettings(Path(tmp)))  # type: ignore[arg-type]

            pack = asyncio.run(service.create_learning_pack(conn, [item_id], title="Camera ResearchCast"))

            stored = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack["id"],)).fetchone())
            self.assertEqual(stored["engine"], "researchcast")
            self.assertEqual(stored["research_status"], "blog_ready")
            self.assertEqual(stored["audio_status"], "audio_ready")
            self.assertTrue(Path(stored["blog_path"]).exists())
            self.assertTrue(Path(stored["local_audio_path"]).exists())
            self.assertIn("Camera module spec", stored["script_text"])
            self.assertIn("blog_url", pack)

    def test_minimax_async_tts_uses_official_query_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = FakeSettings(Path(tmp))
            settings.llm_api_key = "test-key"
            service = ResearchCastService(settings)  # type: ignore[arg-type]
            calls: list[dict[str, object]] = []
            original_client = researchcast_module.httpx.AsyncClient
            original_start = researchcast_module.log_external_call_start
            original_finish = researchcast_module.log_external_call_finish

            class FakeResponse:
                def __init__(self, payload: dict[str, object] | None = None, content: bytes = b"") -> None:
                    self._payload = payload or {}
                    self.content = content

                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, object]:
                    return self._payload

            class FakeAsyncClient:
                def __init__(self, *args, **kwargs) -> None:
                    return None

                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb) -> None:
                    return None

                async def post(self, url: str, **kwargs):
                    calls.append({"method": "POST", "url": url, **kwargs})
                    return FakeResponse({"task_id": "task-1"})

                async def get(self, url: str, **kwargs):
                    calls.append({"method": "GET", "url": url, **kwargs})
                    if url.endswith("/files/retrieve_content"):
                        return FakeResponse(content=b"mp3")
                    return FakeResponse({"status": "Success", "file_id": "file-1"})

            researchcast_module.httpx.AsyncClient = FakeAsyncClient  # type: ignore[assignment]
            researchcast_module.log_external_call_start = lambda **kwargs: 1  # type: ignore[assignment]
            researchcast_module.log_external_call_finish = lambda *args, **kwargs: None  # type: ignore[assignment]
            try:
                status, audio_path, task_id, error = asyncio.run(
                    service.synthesize_minimax_tts({"id": 1, "title": "MiniMax"}, "hello", wait_seconds=1)
                )
            finally:
                researchcast_module.httpx.AsyncClient = original_client  # type: ignore[assignment]
                researchcast_module.log_external_call_start = original_start  # type: ignore[assignment]
                researchcast_module.log_external_call_finish = original_finish  # type: ignore[assignment]

            self.assertEqual(status, "audio_ready")
            self.assertEqual(task_id, "task-1")
            self.assertEqual(error, "")
            self.assertTrue(audio_path and audio_path.exists())
            create_call = calls[0]
            query_call = calls[1]
            self.assertEqual(create_call["method"], "POST")
            self.assertEqual(query_call["method"], "GET")
            self.assertEqual(query_call["params"], {"task_id": "task-1"})
            self.assertIn("audio_sample_rate", create_call["json"]["audio_setting"])
            self.assertNotIn("sample_rate", create_call["json"]["audio_setting"])


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
            payload_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT,
            processed_at TEXT
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            kind TEXT NOT NULL,
            mtime REAL NOT NULL,
            sha256 TEXT NOT NULL,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_event_id INTEGER,
            path TEXT,
            kind TEXT NOT NULL,
            title TEXT,
            text TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            priority_score INTEGER NOT NULL DEFAULT 50,
            priority_label TEXT NOT NULL DEFAULT 'P2',
            project TEXT NOT NULL DEFAULT '当前项目',
            owner TEXT NOT NULL DEFAULT 'me',
            next_action TEXT NOT NULL,
            due_at TEXT,
            source_event_ids_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE knowledge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            domain TEXT NOT NULL,
            summary TEXT NOT NULL,
            novelty_score INTEGER NOT NULL DEFAULT 60,
            urgency TEXT NOT NULL DEFAULT 'medium',
            work_relevance TEXT NOT NULL,
            key_questions_json TEXT NOT NULL DEFAULT '[]',
            followups_json TEXT NOT NULL DEFAULT '[]',
            source_event_ids_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE learning_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            knowledge_item_ids_json TEXT NOT NULL DEFAULT '[]',
            script_text TEXT NOT NULL DEFAULT '',
            local_audio_path TEXT,
            notebooklm_audio_path TEXT,
            notebooklm_status TEXT NOT NULL DEFAULT 'not_requested',
            engine TEXT NOT NULL DEFAULT 'legacy',
            research_status TEXT NOT NULL DEFAULT 'not_requested',
            blog_path TEXT,
            source_manifest_json TEXT NOT NULL DEFAULT '{}',
            audio_status TEXT NOT NULL DEFAULT 'not_requested',
            tts_task_id TEXT,
            audio_error TEXT NOT NULL DEFAULT '',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    return conn


def _insert_event(conn: sqlite3.Connection, title: str, body: str) -> int:
    return int(
        conn.execute(
            """
            INSERT INTO raw_events(source, source_id, source_type, title, body, received_at)
            VALUES ('wechat', ?, 'message', ?, ?, ?)
            """,
            (title, title, body, utc_now()),
        ).lastrowid
    )


def _insert_knowledge(conn: sqlite3.Connection, title: str, event_ids: list[int], domain: str = "battery") -> int:
    return int(
        conn.execute(
            """
            INSERT INTO knowledge_items(
                title, domain, summary, novelty_score, urgency, work_relevance,
                key_questions_json, followups_json, source_event_ids_json, created_at
            ) VALUES (?, ?, ?, 80, 'high', ?, ?, ?, ?, ?)
            """,
            (
                title,
                domain,
                f"Summary for {title}",
                "Impacts 当前项目 hardware decision.",
                json_dumps(["What specs are verified?"]),
                json_dumps(["Ask supplier for proof."]),
                json_dumps(event_ids),
                utc_now(),
            ),
        ).lastrowid
    )


if __name__ == "__main__":
    unittest.main()

