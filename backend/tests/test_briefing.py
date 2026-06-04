from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.ai import AIService  # noqa: E402


class DailyBriefingTest(unittest.TestCase):
    def test_heuristic_briefing_prioritizes_supplier_task_and_learning(self) -> None:
        service = AIService(_settings_without_llm())
        context = {
            "tasks": [
                {
                    "id": 1,
                    "title": "阅读并追问规格资料：电池供应商",
                    "summary": "供应商发来电池规格书，需要确认容量、发热和认证资料。",
                    "priority_score": 92,
                    "priority_label": "P0",
                    "next_action": "先提取关键参数，再列出供应商追问清单。",
                }
            ],
            "reminders": [],
            "knowledge": [
                {
                    "id": 2,
                    "title": "电池新信息：753028 规格书",
                    "summary": "700mAh 电芯规格书。",
                    "urgency": "high",
                    "novelty_score": 80,
                    "work_relevance": "这影响 当前硬件项目 的续航、尺寸和认证风险。",
                }
            ],
        }
        result = asyncio.run(service.daily_briefing(context))
        self.assertIn("今日", result["title"])
        self.assertGreaterEqual(len(result["focus"]), 1)
        self.assertGreaterEqual(len(result["learning"]), 1)
        self.assertEqual(result["focus"][0]["priority"], "P0")


def _settings_without_llm():
    return replace(get_settings(), llm_api_key="")


if __name__ == "__main__":
    unittest.main()

