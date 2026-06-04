from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ..config import Settings
from ..db import json_dumps, row_to_dict, rows_to_dicts, utc_now
from .ai import AIService


class BriefingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ai = AIService(settings)

    async def get_today(self, conn: sqlite3.Connection, regenerate: bool = False) -> dict[str, Any]:
        day = datetime.now().date().isoformat()
        if not regenerate:
            row = conn.execute("SELECT * FROM briefings WHERE day=?", (day,)).fetchone()
            if row:
                return row_to_dict(row) or {}
        return await self.generate_today(conn, day)

    async def generate_today(self, conn: sqlite3.Connection, day: str | None = None) -> dict[str, Any]:
        day = day or datetime.now().date().isoformat()
        context = self._load_context(conn)
        briefing = await self.ai.daily_briefing(context)
        now = utc_now()
        source_task_ids = [item["id"] for item in context["tasks"]]
        source_knowledge_ids = [item["id"] for item in context["knowledge"]]
        source_reminder_ids = [item["id"] for item in context["reminders"]]

        conn.execute(
            """
            INSERT INTO briefings(
                day, title, summary, focus_json, decisions_json, followups_json,
                waiting_json, learning_json, risks_json, source_task_ids_json,
                source_knowledge_ids_json, source_reminder_ids_json, provenance_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                title=excluded.title,
                summary=excluded.summary,
                focus_json=excluded.focus_json,
                decisions_json=excluded.decisions_json,
                followups_json=excluded.followups_json,
                waiting_json=excluded.waiting_json,
                learning_json=excluded.learning_json,
                risks_json=excluded.risks_json,
                source_task_ids_json=excluded.source_task_ids_json,
                source_knowledge_ids_json=excluded.source_knowledge_ids_json,
                source_reminder_ids_json=excluded.source_reminder_ids_json,
                provenance_json=excluded.provenance_json,
                updated_at=excluded.updated_at
            """,
            (
                day,
                briefing.get("title", "今日作战简报"),
                briefing.get("summary", ""),
                json_dumps(briefing.get("focus", [])),
                json_dumps(briefing.get("decisions", [])),
                json_dumps(briefing.get("followups", [])),
                json_dumps(briefing.get("waiting", [])),
                json_dumps(briefing.get("learning", [])),
                json_dumps(briefing.get("risks", [])),
                json_dumps(source_task_ids),
                json_dumps(source_knowledge_ids),
                json_dumps(source_reminder_ids),
                json_dumps(briefing.get("provenance", {"mode": "heuristic"})),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM briefings WHERE day=?", (day,)).fetchone()
        return row_to_dict(row) or {}

    def _load_context(self, conn: sqlite3.Connection) -> dict[str, Any]:
        tasks = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM tasks
                WHERE status='open'
                ORDER BY
                    priority_score DESC,
                    CASE priority_label WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                    COALESCE(due_at, updated_at) ASC
                LIMIT 60
                """
            ).fetchall()
        )
        reminders = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM reminders
                WHERE status='pending'
                ORDER BY remind_at ASC
                LIMIT 40
                """
            ).fetchall()
        )
        knowledge = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM knowledge_items
                ORDER BY novelty_score DESC, created_at DESC
                LIMIT 40
                """
            ).fetchall()
        )
        events = rows_to_dicts(
            conn.execute(
                """
                SELECT id, source, title, body, author, conversation, happened_at, received_at
                FROM raw_events
                ORDER BY received_at DESC, id DESC
                LIMIT 80
                """
            ).fetchall()
        )
        return {
            "tasks": _trim_rows(tasks, ["id", "title", "summary", "priority_score", "priority_label", "next_action", "due_at", "owner"]),
            "reminders": _trim_rows(reminders, ["id", "title", "body", "remind_at", "channel"]),
            "knowledge": _trim_rows(knowledge, ["id", "title", "domain", "summary", "novelty_score", "urgency", "work_relevance", "key_questions", "followups"]),
            "events": _trim_rows(events, ["id", "source", "title", "body", "author", "conversation", "happened_at", "received_at"], text_limit=360),
        }


def _trim_rows(rows: list[dict[str, Any]], keys: list[str], text_limit: int = 520) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for key in keys:
            value = row.get(key)
            if isinstance(value, str) and len(value) > text_limit:
                value = value[: text_limit - 1] + "…"
            item[key] = value
        result.append(item)
    return result

