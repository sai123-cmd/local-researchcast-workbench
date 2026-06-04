from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.podcast import SettingsLike, enrich_learning_pack  # noqa: E402


class PodcastMediaTest(unittest.TestCase):
    def test_enrich_learning_pack_exposes_only_podcast_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "podcasts"
            root.mkdir()
            audio = root / "daily.mp3"
            script = root / "daily.txt"
            audio.write_bytes(b"audio")
            script.write_text("script", encoding="utf-8")
            outside = Path(tmp) / "outside.mp3"
            outside.write_bytes(b"outside")

            row = enrich_learning_pack(
                {
                    "id": 1,
                    "title": "daily",
                    "local_audio_path": str(audio),
                    "notebooklm_audio_path": str(outside),
                    "knowledge_item_ids_json": "[1]",
                },
                SettingsLike(root),
            )
            self.assertEqual(row["local_audio_url"], "/media/podcasts/daily.mp3")
            self.assertEqual(row["script_url"], "/media/podcasts/daily.txt")
            self.assertIsNone(row["notebooklm_audio_url"])


if __name__ == "__main__":
    unittest.main()

