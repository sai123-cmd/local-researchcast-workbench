from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.ai import AIService, _extract_json, _strip_thinking  # noqa: E402


class HeuristicAnalysisTest(unittest.TestCase):
    def test_supplier_spec_message_creates_task_and_knowledge(self) -> None:
        service = AIService(_settings_without_llm())
        events = [
            {
                "id": 1,
                "source": "wechat",
                "title": "摄像模组供应商",
                "body": "这是 OV 摄像模组规格书，帮忙确认 FOV、功耗和打样报价？",
                "author": "supplier",
                "conversation": "供应商群",
            }
        ]
        result = asyncio.run(service.analyze_events(events))
        self.assertGreaterEqual(len(result["tasks"]), 1)
        self.assertGreaterEqual(len(result["knowledge_items"]), 1)
        self.assertEqual(result["knowledge_items"][0]["domain"], "摄像模组")

    def test_cpu_agent_capacity_message_is_not_misclassified_as_battery(self) -> None:
        service = AIService(_settings_without_llm())
        events = [
            {
                "id": 2,
                "source": "document",
                "title": "互联网行业被低估的 CPU 与 Agent 时代",
                "body": "全球服务器 CPU 市场规模、Agent 推理需求、算力供给、容量规划和机架散热趋势。",
                "author": "research",
                "conversation": "inbox",
            }
        ]
        result = asyncio.run(service.analyze_events(events))
        domains = {item["domain"] for item in result["knowledge_items"]}
        self.assertNotIn("电池", domains)

    def test_battery_requires_strong_battery_signal(self) -> None:
        service = AIService(_settings_without_llm())
        events = [
            {
                "id": 3,
                "source": "wechat",
                "title": "DHDC 电池规格书",
                "body": "供应商发来 700mAh 锂电池规格书，请确认保护板和 UN38.3 资料。",
                "author": "supplier",
                "conversation": "电池供应商",
            }
        ]
        result = asyncio.run(service.analyze_events(events))
        self.assertEqual(result["knowledge_items"][0]["domain"], "电池")


    def test_minimax_thinking_tags_are_removed_before_user_visible_output(self) -> None:
        content = '<think>internal reasoning</think>{"ok": true}'
        self.assertEqual(_strip_thinking(content), '{"ok": true}')
        self.assertEqual(_extract_json(content), {"ok": True})


def _settings_without_llm():
    return replace(get_settings(), llm_api_key="")


if __name__ == "__main__":
    unittest.main()

