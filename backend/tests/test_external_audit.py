from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import init_db, log_external_call_finish, log_external_call_start, db_session  # noqa: E402


class ExternalAuditTest(unittest.TestCase):
    def test_external_call_lifecycle(self) -> None:
        old_db = os.environ.get("WORKBENCH_DB_PATH")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["WORKBENCH_DB_PATH"] = str(Path(tmp) / "audit.sqlite3")
            init_db()
            call_id = log_external_call_start(
                provider="openai_compatible",
                purpose="测试外发",
                model="test-model",
                base_url="https://example.test/v1",
                prompt_chars=123,
                content_summary="短摘要",
                request_hash="abc",
                provenance={"test": True},
            )
            log_external_call_finish(call_id, status="ok", response_summary="完成")
            with db_session() as conn:
                row = conn.execute("SELECT * FROM external_calls WHERE id=?", (call_id,)).fetchone()
                self.assertEqual(row["status"], "ok")
                self.assertEqual(row["purpose"], "测试外发")
                self.assertEqual(row["response_summary"], "完成")
        if old_db is None:
            os.environ.pop("WORKBENCH_DB_PATH", None)
        else:
            os.environ["WORKBENCH_DB_PATH"] = old_db


if __name__ == "__main__":
    unittest.main()

