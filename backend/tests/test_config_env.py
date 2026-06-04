from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


class EnvUpdateTest(unittest.TestCase):
    def test_update_env_file_preserves_comments_and_updates_process_env(self) -> None:
        old_value = os.environ.get("LLM_MODEL")
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("# local settings\nLLM_BASE_URL=https://old.example/v1\n", encoding="utf-8")
            with patch.object(config, "ENV_PATH", env_path):
                config.update_env_file(
                    {
                        "LLM_BASE_URL": "https://new.example/v1",
                        "LLM_MODEL": "gpt-4o-mini",
                        "UNSUPPORTED_KEY": "ignored",
                    }
                )
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("# local settings", text)
            self.assertIn("LLM_BASE_URL=https://new.example/v1", text)
            self.assertIn("LLM_MODEL=gpt-4o-mini", text)
            self.assertNotIn("UNSUPPORTED_KEY", text)
            self.assertEqual(os.environ["LLM_MODEL"], "gpt-4o-mini")
        if old_value is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = old_value

    def test_env_bool_parses_false_values(self) -> None:
        old_value = os.environ.get("AUTO_STARTUP_SYNC")
        os.environ["AUTO_STARTUP_SYNC"] = "false"
        self.assertFalse(config._env_bool("AUTO_STARTUP_SYNC", True))
        os.environ["AUTO_STARTUP_SYNC"] = "true"
        self.assertTrue(config._env_bool("AUTO_STARTUP_SYNC", False))
        if old_value is None:
            os.environ.pop("AUTO_STARTUP_SYNC", None)
        else:
            os.environ["AUTO_STARTUP_SYNC"] = old_value

    def test_env_int_allows_zero_for_unlimited_limits(self) -> None:
        old_value = os.environ.get("WX_BACKFILL_SESSION_LIMIT")
        os.environ["WX_BACKFILL_SESSION_LIMIT"] = "0"
        self.assertEqual(config._env_int("WX_BACKFILL_SESSION_LIMIT", 30, minimum=0), 0)
        os.environ["WX_BACKFILL_SESSION_LIMIT"] = "-5"
        self.assertEqual(config._env_int("WX_BACKFILL_SESSION_LIMIT", 30, minimum=0), 0)
        os.environ["WX_BACKFILL_SESSION_LIMIT"] = "invalid"
        self.assertEqual(config._env_int("WX_BACKFILL_SESSION_LIMIT", 30, minimum=0), 30)
        if old_value is None:
            os.environ.pop("WX_BACKFILL_SESSION_LIMIT", None)
        else:
            os.environ["WX_BACKFILL_SESSION_LIMIT"] = old_value


if __name__ == "__main__":
    unittest.main()

