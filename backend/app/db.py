from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def content_hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        if part is None:
            continue
        if not isinstance(part, str):
            part = json_dumps(part)
        h.update(part.encode("utf-8", errors="ignore"))
        h.update(b"\0")
    return h.hexdigest()


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def db_session() -> Iterable[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for key in list(item.keys()):
        if key.endswith("_json"):
            item[key[:-5]] = json_loads(item[key], [])
    return item


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows if row is not None]


def init_db() -> None:
    with db_session() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                author TEXT,
                conversation TEXT,
                happened_at TEXT,
                received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                content_hash TEXT NOT NULL UNIQUE,
                processed_at TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS raw_event_fts USING fts5(
                title,
                body,
                source,
                conversation,
                content='raw_events',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS raw_events_ai AFTER INSERT ON raw_events BEGIN
                INSERT INTO raw_event_fts(rowid, title, body, source, conversation)
                VALUES (new.id, new.title, new.body, new.source, COALESCE(new.conversation, ''));
            END;

            CREATE TRIGGER IF NOT EXISTS raw_events_ad AFTER DELETE ON raw_events BEGIN
                INSERT INTO raw_event_fts(raw_event_fts, rowid, title, body, source, conversation)
                VALUES ('delete', old.id, old.title, old.body, old.source, COALESCE(old.conversation, ''));
            END;

            CREATE TRIGGER IF NOT EXISTS raw_events_au AFTER UPDATE ON raw_events BEGIN
                INSERT INTO raw_event_fts(raw_event_fts, rowid, title, body, source, conversation)
                VALUES ('delete', old.id, old.title, old.body, old.source, COALESCE(old.conversation, ''));
                INSERT INTO raw_event_fts(rowid, title, body, source, conversation)
                VALUES (new.id, new.title, new.body, new.source, COALESCE(new.conversation, ''));
            END;

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_event_id INTEGER REFERENCES raw_events(id) ON DELETE CASCADE,
                wx_msg_id TEXT,
                conversation TEXT,
                chat_type TEXT,
                sender TEXT,
                sender_username TEXT,
                sender_contact_display TEXT,
                sender_group_nickname TEXT,
                content TEXT,
                message_time TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(wx_msg_id, conversation, message_time)
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                kind TEXT NOT NULL,
                mtime REAL NOT NULL,
                sha256 TEXT NOT NULL,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                indexed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_event_id INTEGER REFERENCES raw_events(id) ON DELETE SET NULL,
                path TEXT,
                kind TEXT NOT NULL,
                title TEXT,
                text TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                priority_score INTEGER NOT NULL DEFAULT 50,
                priority_label TEXT NOT NULL DEFAULT 'P2',
                project TEXT NOT NULL DEFAULT '当前项目',
                owner TEXT NOT NULL DEFAULT 'me',
                next_action TEXT NOT NULL,
                due_at TEXT,
                source_event_ids_json TEXT NOT NULL DEFAULT '[]',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(title, next_action)
            );

            CREATE TABLE IF NOT EXISTS knowledge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                summary TEXT NOT NULL,
                novelty_score INTEGER NOT NULL DEFAULT 60,
                urgency TEXT NOT NULL DEFAULT 'medium',
                work_relevance TEXT NOT NULL,
                key_questions_json TEXT NOT NULL DEFAULT '[]',
                followups_json TEXT NOT NULL DEFAULT '[]',
                source_event_ids_json TEXT NOT NULL DEFAULT '[]',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(title, domain)
            );

            CREATE TABLE IF NOT EXISTS learning_packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                knowledge_item_ids_json TEXT NOT NULL DEFAULT '[]',
                script_text TEXT NOT NULL DEFAULT '',
                local_audio_path TEXT,
                notebooklm_audio_path TEXT,
                notebooklm_status TEXT NOT NULL DEFAULT 'not_requested',
                engine TEXT NOT NULL DEFAULT 'legacy',
                research_status TEXT NOT NULL DEFAULT 'not_requested',
                blog_path TEXT,
                source_manifest_json TEXT NOT NULL DEFAULT '{}',
                audio_status TEXT NOT NULL DEFAULT 'not_requested',
                tts_task_id TEXT,
                audio_error TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'toast',
                status TEXT NOT NULL DEFAULT 'pending',
                remind_at TEXT NOT NULL,
                dedup_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                sent_at TEXT
            );

            CREATE TABLE IF NOT EXISTS briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                focus_json TEXT NOT NULL DEFAULT '[]',
                decisions_json TEXT NOT NULL DEFAULT '[]',
                followups_json TEXT NOT NULL DEFAULT '[]',
                waiting_json TEXT NOT NULL DEFAULT '[]',
                learning_json TEXT NOT NULL DEFAULT '[]',
                risks_json TEXT NOT NULL DEFAULT '[]',
                source_task_ids_json TEXT NOT NULL DEFAULT '[]',
                source_knowledge_ids_json TEXT NOT NULL DEFAULT '[]',
                source_reminder_ids_json TEXT NOT NULL DEFAULT '[]',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                message TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS external_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                purpose TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt_chars INTEGER NOT NULL DEFAULT 0,
                content_summary TEXT NOT NULL DEFAULT '',
                request_hash TEXT NOT NULL,
                response_summary TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            """
        )
        _ensure_learning_pack_columns(conn)


def _ensure_learning_pack_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(learning_packs)").fetchall()}
    columns = {
        "engine": "TEXT NOT NULL DEFAULT 'legacy'",
        "research_status": "TEXT NOT NULL DEFAULT 'not_requested'",
        "blog_path": "TEXT",
        "source_manifest_json": "TEXT NOT NULL DEFAULT '{}'",
        "audio_status": "TEXT NOT NULL DEFAULT 'not_requested'",
        "tts_task_id": "TEXT",
        "audio_error": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE learning_packs ADD COLUMN {name} {ddl}")


def log_run(
    conn: sqlite3.Connection,
    kind: str,
    status: str,
    message: str = "",
    payload: dict[str, Any] | None = None,
    run_id: int | None = None,
) -> int:
    now = utc_now()
    if run_id:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, message=?, payload_json=? WHERE id=?",
            (status, now, message, json_dumps(payload or {}), run_id),
        )
        return run_id
    cur = conn.execute(
        "INSERT INTO runs(kind, status, started_at, message, payload_json) VALUES (?, ?, ?, ?, ?)",
        (kind, status, now, message, json_dumps(payload or {})),
    )
    return int(cur.lastrowid)


def log_external_call_start(
    *,
    provider: str,
    purpose: str,
    model: str,
    base_url: str,
    prompt_chars: int,
    content_summary: str,
    request_hash: str,
    provenance: dict[str, Any] | None = None,
) -> int:
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO external_calls(
                provider, purpose, model, base_url, status, prompt_chars,
                content_summary, request_hash, provenance_json, started_at
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                provider,
                purpose,
                model,
                base_url,
                prompt_chars,
                content_summary,
                request_hash,
                json_dumps(provenance or {}),
                utc_now(),
            ),
        )
        return int(cur.lastrowid)


def log_external_call_finish(
    call_id: int | None,
    *,
    status: str,
    response_summary: str = "",
    error: str = "",
) -> None:
    if not call_id:
        return
    with db_session() as conn:
        conn.execute(
            """
            UPDATE external_calls
            SET status=?, response_summary=?, error=?, finished_at=?
            WHERE id=?
            """,
            (status, response_summary, error, utc_now(), call_id),
        )


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

