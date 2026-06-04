from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import content_hash, db_session, init_db, json_dumps, utc_now  # noqa: E402
from app.services.task_context import get_task_context  # noqa: E402


class TaskContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_db_path = os.environ.get("WORKBENCH_DB_PATH")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["WORKBENCH_DB_PATH"] = str(Path(self.temp_dir.name) / "task-context.sqlite3")
        init_db()

    def tearDown(self) -> None:
        if self.old_db_path is None:
            os.environ.pop("WORKBENCH_DB_PATH", None)
        else:
            os.environ["WORKBENCH_DB_PATH"] = self.old_db_path
        self.temp_dir.cleanup()

    def test_context_returns_source_evidence_and_battery_questions(self) -> None:
        now = utc_now()
        with db_session() as conn:
            event_id = conn.execute(
                """
                INSERT INTO raw_events(
                    source, source_id, source_type, title, body, conversation,
                    happened_at, received_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "wechat",
                    "wx-battery-1",
                    "message",
                    "DHDC 电池供应商",
                    "供应商发来 700mAh 电池规格书，问是否需要穿刺测试和认证资料。",
                    "DHDC 锂电池",
                    now,
                    now,
                    content_hash("wx-battery-1", "DHDC 电池供应商"),
                ),
            ).lastrowid
            task_id = conn.execute(
                """
                INSERT INTO tasks(
                    title, summary, priority_score, priority_label, next_action,
                    source_event_ids_json, provenance_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "追问电池供应商",
                    "确认电池规格书、测试口径和认证资料。",
                    88,
                    "P1",
                    "整理电池追问清单发给供应商。",
                    json_dumps([event_id]),
                    json_dumps({"mode": "test"}),
                    now,
                    now,
                ),
            ).lastrowid

            context = get_task_context(conn, int(task_id))

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["confidence"], "source_linked")
        self.assertEqual(context["source_events"][0]["id"], event_id)
        self.assertIn("容量", " ".join(context["next_questions"]))
        self.assertIn("整理电池追问清单", context["suggested_reply"])


if __name__ == "__main__":
    unittest.main()

