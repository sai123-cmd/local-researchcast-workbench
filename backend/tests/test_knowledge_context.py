from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import content_hash, db_session, init_db, json_dumps, utc_now  # noqa: E402
from app.services.knowledge_context import get_knowledge_context  # noqa: E402


class KnowledgeContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_db_path = os.environ.get("WORKBENCH_DB_PATH")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["WORKBENCH_DB_PATH"] = str(Path(self.temp_dir.name) / "knowledge-context.sqlite3")
        init_db()

    def tearDown(self) -> None:
        if self.old_db_path is None:
            os.environ.pop("WORKBENCH_DB_PATH", None)
        else:
            os.environ["WORKBENCH_DB_PATH"] = self.old_db_path
        self.temp_dir.cleanup()

    def test_context_returns_glossary_questions_and_source_evidence(self) -> None:
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
                    "wx-knowledge-battery",
                    "message",
                    "电池规格书",
                    "供应商发来 700mAh 锂电池规格书，需要确认保护板、厚度公差和 UN38.3。",
                    "电池供应商",
                    now,
                    now,
                    content_hash("wx-knowledge-battery", "电池规格书"),
                ),
            ).lastrowid
            knowledge_id = conn.execute(
                """
                INSERT INTO knowledge_items(
                    title, domain, summary, novelty_score, urgency, work_relevance,
                    key_questions_json, followups_json, source_event_ids_json, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "电池规格新知识",
                    "电池",
                    "700mAh 电芯规格书会影响续航、结构堆叠和认证。",
                    88,
                    "high",
                    "影响 当前项目 硬件打样、样品寄送和认证路径。",
                    json_dumps(["保护板方案是什么？"]),
                    json_dumps(["样品周期和 MOQ 是多少？"]),
                    json_dumps([event_id]),
                    json_dumps({"mode": "test"}),
                    now,
                ),
            ).lastrowid

            context = get_knowledge_context(conn, int(knowledge_id))

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["confidence"], "source_linked")
        self.assertEqual(context["source_events"][0]["id"], event_id)
        self.assertIn("mAh", {term["term"] for term in context["glossary"]})
        self.assertIn("保护板方案是什么？", context["supplier_questions"])
        self.assertGreaterEqual(len(context["podcast_outline"]), 3)


if __name__ == "__main__":
    unittest.main()

