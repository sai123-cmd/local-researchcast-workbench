from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import content_hash, db_session, init_db, utc_now  # noqa: E402
from app.services.search import SearchService  # noqa: E402


class SearchServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_db_path = os.environ.get("WORKBENCH_DB_PATH")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["WORKBENCH_DB_PATH"] = str(Path(self.temp_dir.name) / "search.sqlite3")
        init_db()

    def tearDown(self) -> None:
        if self.old_db_path is None:
            os.environ.pop("WORKBENCH_DB_PATH", None)
        else:
            os.environ["WORKBENCH_DB_PATH"] = self.old_db_path
        self.temp_dir.cleanup()

    def test_searches_all_workbench_surfaces(self) -> None:
        now = utc_now()
        with db_session() as conn:
            conn.execute(
                """
                INSERT INTO raw_events(
                    source, source_id, source_type, title, body, conversation,
                    happened_at, received_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "wechat",
                    "wx-1",
                    "message",
                    "电池供应商问题",
                    "供应商问 当前项目 电池容量和认证风险怎么确认。",
                    "供应商群",
                    now,
                    now,
                    content_hash("wx-1", "电池供应商问题"),
                ),
            )
            conn.execute(
                """
                INSERT INTO documents(path, title, text, kind, mtime, sha256, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(Path(self.temp_dir.name) / "battery.md"),
                    "电池规格书",
                    "电池规格书包含容量、倍率、保护板和 UN38.3 认证信息。",
                    "md",
                    1.0,
                    "sha-doc",
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO tasks(title, summary, priority_score, priority_label, next_action, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "追问电池供应商",
                    "需要确认电池认证资料。",
                    88,
                    "P1",
                    "整理电池追问清单发给供应商。",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO knowledge_items(title, domain, summary, novelty_score, urgency, work_relevance, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "电池规格新知识",
                    "电池",
                    "需要理解容量、尺寸、认证和发热边界。",
                    82,
                    "high",
                    "影响 当前项目 硬件打样和认证路径。",
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO learning_packs(title, status, script_text, notebooklm_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "电池学习音频",
                    "local_audio_ready",
                    "这期音频解释电池容量、保护板、认证和供应商追问方式。",
                    "not_requested",
                    now,
                    now,
                ),
            )

            results = SearchService().search(conn, "电池", limit=20)

        result_types = {item["type"] for item in results}
        self.assertTrue({"event", "document", "task", "knowledge", "podcast"}.issubset(result_types))
        self.assertTrue(all(len(item["snippet"]) <= 221 for item in results))


if __name__ == "__main__":
    unittest.main()

