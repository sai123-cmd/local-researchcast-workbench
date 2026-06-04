from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.services.ingest import IngestionService  # noqa: E402
from app.services.wx_adapter import WxResult  # noqa: E402


class FakeWx:
    def __init__(self) -> None:
        self.history_calls: list[tuple[int, int]] = []
        self.attachment_calls: list[tuple[int, int]] = []

    def history(self, chat: str, since: str, until: str | None = None, limit: int = 120, offset: int = 0) -> WxResult:
        self.history_calls.append((limit, offset))
        count = limit if offset == 0 else 5
        return WxResult(True, [{"id": str(offset + index)} for index in range(count)], {}, raw={"chat": chat})

    def attachments(self, chat: str, since: str, until: str | None = None, limit: int = 20, offset: int = 0) -> WxResult:
        self.attachment_calls.append((limit, offset))
        count = limit if offset == 0 else 2
        return WxResult(True, [{"attachment_id": str(offset + index)} for index in range(count)], {}, raw={"chat": chat})


class WechatUnlimitedLimitTest(unittest.TestCase):
    def test_zero_history_limit_pages_until_short_page(self) -> None:
        service = IngestionService(get_settings())
        fake = FakeWx()
        service.wx = fake  # type: ignore[assignment]
        pages = list(service._history_pages("chat", "2026-06-02", "2026-06-03", limit=0))
        self.assertEqual(len(pages), 2)
        self.assertEqual(fake.history_calls, [(200, 0), (200, 200)])

    def test_zero_attachment_limit_pages_until_short_page(self) -> None:
        service = IngestionService(get_settings())
        fake = FakeWx()
        service.wx = fake  # type: ignore[assignment]
        pages = list(service._attachment_pages("chat", "2026-06-02", "2026-06-03", limit=0))
        self.assertEqual(len(pages), 2)
        self.assertEqual(fake.attachment_calls, [(100, 0), (100, 100)])


if __name__ == "__main__":
    unittest.main()

