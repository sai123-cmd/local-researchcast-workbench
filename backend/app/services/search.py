from __future__ import annotations

import re
import sqlite3
from typing import Any


class SearchService:
    def search(self, conn: sqlite3.Connection, query: str, limit: int = 60) -> list[dict[str, Any]]:
        clean_query = " ".join(query.split())
        if not clean_query:
            return []

        limit = max(1, min(limit, 100))
        per_source = max(8, min(limit, 40))
        seen: set[tuple[str, int]] = set()
        results: list[dict[str, Any]] = []

        for item in self._raw_events(conn, clean_query, per_source):
            self._append(results, seen, item)
        for item in self._documents(conn, clean_query, per_source):
            self._append(results, seen, item)
        for item in self._tasks(conn, clean_query, per_source):
            self._append(results, seen, item)
        for item in self._knowledge(conn, clean_query, per_source):
            self._append(results, seen, item)
        for item in self._learning_packs(conn, clean_query, per_source):
            self._append(results, seen, item)

        results.sort(key=lambda item: (item["score"], item.get("happened_at") or ""), reverse=True)
        return results[:limit]

    def _append(self, results: list[dict[str, Any]], seen: set[tuple[str, int]], item: dict[str, Any]) -> None:
        key = (item["type"], int(item["id"]))
        if key in seen:
            return
        seen.add(key)
        results.append(item)

    def _raw_events(self, conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        fts_query = _fts_query(query)
        if fts_query:
            try:
                rows = conn.execute(
                    """
                    SELECT raw_events.*, bm25(raw_event_fts) AS rank
                    FROM raw_event_fts
                    JOIN raw_events ON raw_events.id = raw_event_fts.rowid
                    WHERE raw_event_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
                for row in rows:
                    items.append(
                        _result(
                            type_="event",
                            row=row,
                            title=row["title"],
                            body=row["body"],
                            query=query,
                            source=row["source"],
                            conversation=row["conversation"],
                            happened_at=row["happened_at"] or row["received_at"],
                            score=95,
                        )
                    )
            except sqlite3.Error:
                items = []

        existing_ids = {int(item["id"]) for item in items}
        rows = conn.execute(
            """
            SELECT *
            FROM raw_events
            WHERE title LIKE ? OR body LIKE ? OR source LIKE ? OR COALESCE(conversation, '') LIKE ?
            ORDER BY COALESCE(happened_at, received_at) DESC
            LIMIT ?
            """,
            (_like(query), _like(query), _like(query), _like(query), limit),
        ).fetchall()
        for row in rows:
            if int(row["id"]) in existing_ids:
                continue
            items.append(
                _result(
                    type_="event",
                    row=row,
                    title=row["title"],
                    body=row["body"],
                    query=query,
                    source=row["source"],
                    conversation=row["conversation"],
                    happened_at=row["happened_at"] or row["received_at"],
                    score=86,
                )
            )
        return items

    def _documents(self, conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM documents
            WHERE title LIKE ? OR text LIKE ? OR path LIKE ? OR kind LIKE ?
            ORDER BY indexed_at DESC
            LIMIT ?
            """,
            (_like(query), _like(query), _like(query), _like(query), limit),
        ).fetchall()
        return [
            _result(
                type_="document",
                row=row,
                title=row["title"],
                body=row["text"],
                query=query,
                source=f"{row['kind']} / {row['path']}",
                conversation=None,
                happened_at=row["indexed_at"],
                score=84,
            )
            for row in rows
        ]

    def _tasks(self, conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM tasks
            WHERE title LIKE ?
               OR summary LIKE ?
               OR next_action LIKE ?
               OR project LIKE ?
               OR owner LIKE ?
               OR priority_label LIKE ?
            ORDER BY priority_score DESC, updated_at DESC
            LIMIT ?
            """,
            (_like(query), _like(query), _like(query), _like(query), _like(query), _like(query), limit),
        ).fetchall()
        return [
            _result(
                type_="task",
                row=row,
                title=row["title"],
                body=f"{row['next_action']}\n{row['summary']}",
                query=query,
                source=f"{row['project']} / {row['priority_label']} / {row['status']}",
                conversation=row["owner"],
                happened_at=row["due_at"] or row["updated_at"],
                score=88 + min(int(row["priority_score"] or 0), 100) / 100,
            )
            for row in rows
        ]

    def _knowledge(self, conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM knowledge_items
            WHERE title LIKE ?
               OR domain LIKE ?
               OR summary LIKE ?
               OR work_relevance LIKE ?
               OR key_questions_json LIKE ?
               OR followups_json LIKE ?
            ORDER BY novelty_score DESC, created_at DESC
            LIMIT ?
            """,
            (_like(query), _like(query), _like(query), _like(query), _like(query), _like(query), limit),
        ).fetchall()
        return [
            _result(
                type_="knowledge",
                row=row,
                title=row["title"],
                body=f"{row['work_relevance']}\n{row['summary']}",
                query=query,
                source=f"{row['domain']} / {row['urgency']}",
                conversation=None,
                happened_at=row["created_at"],
                score=87 + min(int(row["novelty_score"] or 0), 100) / 100,
            )
            for row in rows
        ]

    def _learning_packs(self, conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM learning_packs
            WHERE title LIKE ? OR script_text LIKE ? OR status LIKE ? OR notebooklm_status LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (_like(query), _like(query), _like(query), _like(query), limit),
        ).fetchall()
        return [
            _result(
                type_="podcast",
                row=row,
                title=row["title"],
                body=row["script_text"],
                query=query,
                source=f"{row['status']} / NotebookLM: {row['notebooklm_status']}",
                conversation=None,
                happened_at=row["updated_at"] or row["created_at"],
                score=82,
            )
            for row in rows
        ]


def _like(query: str) -> str:
    return f"%{query}%"


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
    quoted = [f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:8] if token]
    return " OR ".join(quoted)


def _result(
    *,
    type_: str,
    row: sqlite3.Row,
    title: str,
    body: str,
    query: str,
    source: str,
    conversation: str | None,
    happened_at: str | None,
    score: float,
) -> dict[str, Any]:
    return {
        "type": type_,
        "id": int(row["id"]),
        "title": title,
        "snippet": _snippet(body or title, query),
        "source": source,
        "conversation": conversation or "",
        "happened_at": happened_at or "",
        "score": round(score, 3),
    }


def _snippet(text: str, query: str, max_chars: int = 220) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_chars:
        return clean
    index = clean.lower().find(query.lower())
    if index < 0:
        return clean[: max_chars - 1] + "…"
    start = max(0, index - 70)
    end = min(len(clean), start + max_chars)
    start = max(0, end - max_chars)
    prefix = "…" if start else ""
    suffix = "…" if end < len(clean) else ""
    return f"{prefix}{clean[start:end]}{suffix}"

