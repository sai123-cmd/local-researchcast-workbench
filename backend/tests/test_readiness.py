from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.readiness import build_readiness  # noqa: E402


class ReadinessTest(unittest.TestCase):
    def test_readiness_marks_missing_external_dependencies_as_blockers(self) -> None:
        health = {
            "wx": {"ok": True, "message": "wx CLI ready"},
            "llm": {"configured": False, "model": "gpt-4o-mini"},
            "notebooklm": {"ok": False, "message": "login required"},
            "ocr": {"ok": False, "message": "tesseract not found"},
            "startup": {"installed": False, "message": "not installed"},
            "wechat_limits": {
                "backfill_session_limit": 0,
                "backfill_history_limit": 0,
                "attachment_session_limit": 0,
                "attachment_limit": 0,
                "file_scan_limit": 0,
            },
            "counts": {"tasks": 3, "knowledge_items": 2, "learning_packs": 1},
        }
        result = build_readiness(health)
        blocker_ids = {item["id"] for item in result["blockers"]}
        self.assertFalse(result["complete"])
        self.assertIn("llm", blocker_ids)
        self.assertNotIn("notebooklm", blocker_ids)
        self.assertIn("ocr", blocker_ids)
        self.assertNotIn("startup", blocker_ids)
        self.assertNotIn("full_wechat_scope", blocker_ids)

    def test_readiness_complete_when_required_items_are_done(self) -> None:
        health = {
            "wx": {"ok": True, "message": "wx CLI ready"},
            "llm": {"configured": True, "model": "gpt-4o-mini"},
            "notebooklm": {"ok": True, "message": "ok"},
            "ocr": {"ok": True, "message": "tesseract 5"},
            "startup": {"installed": False, "message": "not installed"},
            "wechat_limits": {
                "backfill_session_limit": 0,
                "backfill_history_limit": 0,
                "attachment_session_limit": 0,
                "attachment_limit": 0,
                "file_scan_limit": 0,
            },
            "counts": {"tasks": 3, "knowledge_items": 2, "learning_packs": 1},
        }
        result = build_readiness(health)
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["score"], 100)

    def test_notebooklm_auth_is_optional_for_researchcast_readiness(self) -> None:
        health = {
            "wx": {"ok": True, "message": "wx CLI ready"},
            "llm": {"configured": True, "model": "MiniMax-M2.7-highspeed"},
            "notebooklm": {"ok": False, "message": "login expired"},
            "ocr": {"ok": True, "message": "tesseract 5"},
            "startup": {"installed": False, "message": "not installed"},
            "wechat_limits": {
                "backfill_session_limit": 0,
                "backfill_history_limit": 0,
                "attachment_session_limit": 0,
                "attachment_limit": 0,
                "file_scan_limit": 0,
            },
            "counts": {"tasks": 3, "knowledge_items": 2, "learning_packs": 1},
        }
        result = build_readiness(health)
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["score"], 100)
        self.assertNotIn("notebooklm", {item["id"] for item in result["blockers"]})


if __name__ == "__main__":
    unittest.main()

