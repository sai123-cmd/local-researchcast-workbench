from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import content_hash, db_session, init_db, utc_now  # noqa: E402
from app.services.ocr import append_ocr_text  # noqa: E402
from app.services.search import SearchService  # noqa: E402


class OcrIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_db_path = os.environ.get("WORKBENCH_DB_PATH")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["WORKBENCH_DB_PATH"] = str(Path(self.temp_dir.name) / "ocr.sqlite3")
        init_db()

    def tearDown(self) -> None:
        if self.old_db_path is None:
            os.environ.pop("WORKBENCH_DB_PATH", None)
        else:
            os.environ["WORKBENCH_DB_PATH"] = self.old_db_path
        self.temp_dir.cleanup()

    def test_append_ocr_text_is_idempotent(self) -> None:
        body = "[微信图片附件]\nC:/tmp/a.jpg"
        with_ocr = append_ocr_text(body, "报价截图 120 元")
        self.assertIn("[OCR]", with_ocr)
        self.assertEqual(with_ocr, append_ocr_text(with_ocr, "报价截图 120 元"))

    def test_updated_raw_event_ocr_text_is_searchable(self) -> None:
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
                    "wechat_attachment",
                    "image-1",
                    "image",
                    "微信图片：供应商报价",
                    "[微信图片附件]\nC:/tmp/a.jpg",
                    "供应商群",
                    now,
                    now,
                    content_hash("wechat_attachment", "image-1", "微信图片：供应商报价", "[微信图片附件]\nC:/tmp/a.jpg"),
                ),
            ).lastrowid
            conn.execute("UPDATE raw_events SET body=? WHERE id=?", (append_ocr_text("[微信图片附件]\nC:/tmp/a.jpg", "报价截图 120 元"), event_id))
            results = SearchService().search(conn, "报价截图", limit=5)

        self.assertTrue(any(result["id"] == event_id and result["type"] == "event" for result in results))


if __name__ == "__main__":
    unittest.main()

