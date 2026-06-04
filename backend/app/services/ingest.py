from __future__ import annotations

import sqlite3
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db import (
    content_hash,
    file_sha256,
    json_dumps,
    json_loads,
    log_run,
    row_to_dict,
    rows_to_dicts,
    utc_now,
)
from .ai import AIService
from .documents import extract_text, supported_document
from .ocr import OcrService, append_ocr_text
from .wx_adapter import WxCliAdapter, normalize_attachment, normalize_message

WX_HISTORY_PAGE_SIZE = 200
WX_ATTACHMENT_PAGE_SIZE = 100


class IngestionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.wx = WxCliAdapter()
        self.ai = AIService(settings)
        self.ocr = OcrService(settings)

    def ingest_manual_text(
        self,
        conn: sqlite3.Connection,
        title: str,
        body: str,
        source: str = "manual",
        conversation: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        event = self._insert_raw_event(
            conn,
            source=source,
            source_id=content_hash(source, title, body),
            source_type="text",
            title=title,
            body=body,
            author=author,
            conversation=conversation,
            happened_at=utc_now(),
            payload={"manual": True},
            provenance={"adapter": "manual"},
        )
        return event

    def poll_wechat(self, conn: sqlite3.Connection) -> dict[str, Any]:
        run_id = log_run(conn, "wechat_poll", "running", "polling wx new-messages")
        conn.commit()
        result = self.wx.new_messages()
        if not result.ok:
            log_run(conn, "wechat_poll", "failed", result.error, {"meta": result.meta}, run_id)
            return {"ok": False, "message": result.error, "inserted": 0, "meta": result.meta}
        inserted = 0
        for item in result.results:
            if self._insert_wechat_message(conn, item, {"adapter": "wx-cli", "mode": "new_messages", "meta": result.meta}):
                inserted += 1
        status = "ok" if result.meta.get("status") in (None, "ok", "windowed") else "warning"
        log_run(conn, "wechat_poll", status, f"inserted {inserted} message events", {"meta": result.meta}, run_id)
        return {"ok": True, "inserted": inserted, "meta": result.meta}

    def backfill_wechat_today(self, conn: sqlite3.Connection) -> dict[str, Any]:
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        session_limit = self.settings.wx_backfill_session_limit
        history_limit = self.settings.wx_backfill_history_limit
        session_label = "all" if session_limit <= 0 else str(session_limit)
        history_label = "all" if history_limit <= 0 else str(history_limit)
        run_id = log_run(conn, "wechat_today_backfill", "running", f"backfill {session_label} sessions, {history_label} messages/chat since {today}")
        conn.commit()
        sessions = self.wx.sessions(limit=session_limit)
        if not sessions.ok:
            log_run(conn, "wechat_today_backfill", "failed", sessions.error, {"meta": sessions.meta}, run_id)
            return {"ok": False, "message": sessions.error, "inserted": 0}

        inserted = 0
        failed: list[dict[str, str]] = []
        session_rows = sessions.results if session_limit <= 0 else sessions.results[:session_limit]
        chats_scanned = 0
        for session in session_rows:
            chat = session.get("chat") or session.get("username")
            if not chat:
                continue
            chats_scanned += 1
            for history in self._history_pages(str(chat), since=today, until=tomorrow, limit=history_limit):
                if not history.ok:
                    failed.append({"chat": str(chat), "error": history.error[:200]})
                    break
                context = {
                    "adapter": "wx-cli",
                    "mode": "history_today",
                    "chat": history.raw.get("chat") if isinstance(history.raw, dict) else chat,
                    "chat_type": history.raw.get("chat_type") if isinstance(history.raw, dict) else session.get("chat_type"),
                    "username": history.raw.get("username") if isinstance(history.raw, dict) else session.get("username"),
                    "meta": history.meta,
                }
                for item in history.results:
                    if self._insert_wechat_message(conn, item, context):
                        inserted += 1
                conn.commit()
        status = "ok" if not failed else "warning"
        log_run(
            conn,
            "wechat_today_backfill",
            status,
            f"inserted {inserted}, scanned {chats_scanned} chats, failed {len(failed)} chats",
            {"failed": failed[:10], "since": today, "until": tomorrow, "chats_scanned": chats_scanned, "session_limit": session_limit, "history_limit": history_limit},
            run_id,
        )
        return {"ok": True, "inserted": inserted, "failed": failed, "since": today, "until": tomorrow, "chats_scanned": chats_scanned}

    def ingest_wechat_attachments(self, conn: sqlite3.Connection) -> dict[str, Any]:
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        session_limit = self.settings.wx_attachment_session_limit
        attachment_limit = self.settings.wx_attachment_limit
        session_label = "all" if session_limit <= 0 else str(session_limit)
        attachment_label = "all" if attachment_limit <= 0 else str(attachment_limit)
        run_id = log_run(conn, "wechat_attachment_scan", "running", f"scan images from {session_label} sessions, {attachment_label} images/chat")
        conn.commit()
        sessions = self.wx.sessions(limit=session_limit)
        if not sessions.ok:
            log_run(conn, "wechat_attachment_scan", "failed", sessions.error, {"meta": sessions.meta}, run_id)
            return {"ok": False, "message": sessions.error, "inserted": 0, "extracted": 0}

        inserted = 0
        extracted = 0
        failed: list[dict[str, str]] = []
        session_rows = sessions.results if session_limit <= 0 else sessions.results[:session_limit]
        chats_scanned = 0
        for session in session_rows:
            chat = session.get("chat") or session.get("username")
            if not chat:
                continue
            chats_scanned += 1
            for result in self._attachment_pages(str(chat), since=today, until=tomorrow, limit=attachment_limit):
                if not result.ok:
                    failed.append({"chat": str(chat), "error": result.error[:200]})
                    break
                context = {
                    "adapter": "wx-cli",
                    "mode": "attachments_today",
                    "chat": result.raw.get("chat") if isinstance(result.raw, dict) else chat,
                    "chat_type": result.raw.get("chat_type") if isinstance(result.raw, dict) else session.get("chat_type"),
                    "username": result.raw.get("username") if isinstance(result.raw, dict) else session.get("username"),
                    "meta": result.meta,
                }
                for item in result.results:
                    normalized = normalize_attachment(item, context)
                    if not normalized["attachment_id"]:
                        continue
                    output_path = self._attachment_output_path(normalized)
                    extract_result = self.wx.extract_attachment(normalized["attachment_id"], output_path)
                    ocr_result = None
                    if extract_result.ok:
                        extracted += 1
                        ocr_result = self.ocr.extract_text(output_path)
                    else:
                        failed.append({"chat": str(chat), "error": extract_result.error[:200]})
                    body = f"[微信图片附件] {normalized['conversation']} / {normalized['sender']} / {normalized['message_time']}\n{output_path}"
                    if ocr_result and ocr_result.text:
                        body = append_ocr_text(body, ocr_result.text)
                    event = self._upsert_attachment_event(
                        conn,
                        normalized=normalized,
                        body=body,
                        output_path=output_path,
                        extract_ok=extract_result.ok,
                        ocr_payload=_ocr_payload(ocr_result),
                        provenance=context,
                    )
                    if event.get("created"):
                        inserted += 1
                        conn.execute(
                            """
                            INSERT INTO attachments(raw_event_id, path, kind, title, text, payload_json, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                event["id"],
                                str(output_path),
                                normalized["kind"],
                                event["title"],
                                body,
                                json_dumps(
                                    {
                                        **normalized["payload"],
                                        "extract": extract_result.raw,
                                        "extract_error": extract_result.error,
                                        "ocr": _ocr_payload(ocr_result),
                                    }
                                ),
                                utc_now(),
                            ),
                        )
                conn.commit()
        status = "ok" if not failed else "warning"
        log_run(
            conn,
            "wechat_attachment_scan",
            status,
            f"inserted {inserted}, extracted {extracted}, scanned {chats_scanned} chats, failed {len(failed)}",
            {"failed": failed[:10], "since": today, "until": tomorrow, "chats_scanned": chats_scanned, "session_limit": session_limit, "attachment_limit": attachment_limit},
            run_id,
        )
        return {"ok": True, "inserted": inserted, "extracted": extracted, "failed": failed[:10], "since": today, "until": tomorrow, "chats_scanned": chats_scanned}

    def _history_pages(self, chat: str, since: str, until: str, limit: int):
        if limit > 0:
            yield self.wx.history(chat, since=since, until=until, limit=limit)
            return
        offset = 0
        for _ in range(500):
            result = self.wx.history(chat, since=since, until=until, limit=WX_HISTORY_PAGE_SIZE, offset=offset)
            yield result
            if not result.ok or len(result.results) < WX_HISTORY_PAGE_SIZE:
                break
            offset += WX_HISTORY_PAGE_SIZE

    def _attachment_pages(self, chat: str, since: str, until: str, limit: int):
        if limit > 0:
            yield self.wx.attachments(chat, since=since, until=until, limit=limit)
            return
        offset = 0
        for _ in range(500):
            result = self.wx.attachments(chat, since=since, until=until, limit=WX_ATTACHMENT_PAGE_SIZE, offset=offset)
            yield result
            if not result.ok or len(result.results) < WX_ATTACHMENT_PAGE_SIZE:
                break
            offset += WX_ATTACHMENT_PAGE_SIZE

    def ocr_existing_attachments(self, conn: sqlite3.Connection, limit: int = 80) -> dict[str, Any]:
        run_id = log_run(conn, "attachment_ocr", "running", f"ocr up to {limit} image attachments")
        conn.commit()
        health = self.ocr.health()
        if not health.get("ok"):
            log_run(conn, "attachment_ocr", "warning", health.get("message", "OCR unavailable"), {"health": health}, run_id)
            return {"ok": False, "processed": 0, "updated": 0, "message": health.get("message", "OCR unavailable"), "health": health}

        rows = conn.execute(
            """
            SELECT *
            FROM attachments
            WHERE path IS NOT NULL
              AND kind IN ('image', 'jpg', 'jpeg', 'png')
              AND text NOT LIKE '%[OCR]%'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        processed = 0
        updated = 0
        failed: list[dict[str, str]] = []
        for row in rows:
            processed += 1
            path = Path(row["path"])
            result = self.ocr.extract_text(path)
            payload = json_loads(row["payload_json"], {})
            payload["ocr"] = _ocr_payload(result)
            if not result.text:
                failed.append({"path": str(path), "error": result.message[:160]})
                conn.execute("UPDATE attachments SET payload_json=? WHERE id=?", (json_dumps(payload), row["id"]))
                conn.commit()
                continue

            new_text = append_ocr_text(row["text"] or "", result.text)
            conn.execute(
                "UPDATE attachments SET text=?, payload_json=? WHERE id=?",
                (new_text, json_dumps(payload), row["id"]),
            )
            if row["raw_event_id"]:
                event_payload = payload
                event_row = conn.execute("SELECT body, payload_json FROM raw_events WHERE id=?", (row["raw_event_id"],)).fetchone()
                if event_row:
                    event_payload = {**json_loads(event_row["payload_json"], {}), "ocr": _ocr_payload(result)}
                    conn.execute(
                        """
                        UPDATE raw_events
                        SET body=?, payload_json=?, processed_at=NULL
                        WHERE id=?
                        """,
                        (append_ocr_text(event_row["body"] or "", result.text), json_dumps(event_payload), row["raw_event_id"]),
                    )
            updated += 1
            conn.commit()

        status = "ok" if not failed else "warning"
        log_run(
            conn,
            "attachment_ocr",
            status,
            f"processed {processed}, updated {updated}, failed {len(failed)}",
            {"failed": failed[:10], "ocr": health},
            run_id,
        )
        return {"ok": True, "processed": processed, "updated": updated, "failed": failed[:10], "health": health}

    def scan_documents(self, conn: sqlite3.Connection, scope: str = "inbox") -> dict[str, Any]:
        run_id = log_run(conn, "document_scan", "running", f"scan {scope}")
        conn.commit()
        if scope == "inbox":
            root = self.settings.inbox_dir
            recent_cutoff = None
            max_files = None
        elif scope == "wechat":
            root = (self.settings.wechat_files_root / "msg" / "file") if self.settings.wechat_files_root else None
            recent_cutoff = time.time() - 14 * 24 * 60 * 60
            max_files = self.settings.wx_file_scan_limit or None
        else:
            root = self.settings.workspace_root
            recent_cutoff = None
            max_files = None
        if root is None or not root.exists():
            log_run(conn, "document_scan", "failed", f"{scope} root not found", {"scope": scope}, run_id)
            return {"ok": False, "inserted": 0, "skipped": 0, "root": None, "message": f"{scope} root not found"}
        inserted = 0
        skipped = 0
        candidates = sorted(
            _iter_documents(root, self.settings.workbench_root),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        processed = 0
        max_bytes = self.settings.document_max_mb * 1024 * 1024
        for path in candidates:
            try:
                stat = path.stat()
                if recent_cutoff is not None and stat.st_mtime < recent_cutoff:
                    skipped += 1
                    continue
                if stat.st_size > max_bytes:
                    skipped += 1
                    continue
                if max_files is not None and processed >= max_files:
                    skipped += 1
                    continue
                processed += 1
                sha = file_sha256(path)
                existing = conn.execute("SELECT id, sha256 FROM documents WHERE path=?", (str(path),)).fetchone()
                if existing and existing["sha256"] == sha:
                    skipped += 1
                    continue
                text = extract_text(path)
                if not text.strip():
                    skipped += 1
                    continue
                now = utc_now()
                conn.execute(
                    """
                    INSERT INTO documents(path, title, text, kind, mtime, sha256, provenance_json, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        title=excluded.title,
                        text=excluded.text,
                        kind=excluded.kind,
                        mtime=excluded.mtime,
                        sha256=excluded.sha256,
                        provenance_json=excluded.provenance_json,
                        indexed_at=excluded.indexed_at
                    """,
                    (
                        str(path),
                        path.name,
                        text,
                        path.suffix.lower().lstrip("."),
                        stat.st_mtime,
                        sha,
                        json_dumps({"scope": scope}),
                        now,
                    ),
                )
                event = self._insert_raw_event(
                    conn,
                    source="document",
                    source_id=sha,
                    source_type=path.suffix.lower().lstrip("."),
                    title=path.name,
                    body=text[:20_000],
                    author=None,
                    conversation=None,
                    happened_at=now,
                    payload={"path": str(path), "sha256": sha},
                    provenance={"adapter": "document_scan", "scope": scope},
                )
                if event.get("created"):
                    inserted += 1
                conn.commit()
            except Exception as exc:
                skipped += 1
                conn.execute(
                    "INSERT INTO attachments(kind, title, text, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("scan_error", path.name, str(exc), json_dumps({"path": str(path)}), utc_now()),
                )
                conn.commit()
        log_run(
            conn,
            "document_scan",
            "ok",
            f"inserted {inserted}, skipped {skipped}, processed {processed}",
            {"scope": scope, "max_files": max_files, "max_mb": self.settings.document_max_mb},
            run_id,
        )
        return {"ok": True, "inserted": inserted, "skipped": skipped, "processed": processed, "root": str(root)}

    async def analyze_unprocessed(self, conn: sqlite3.Connection, limit: int = 40) -> dict[str, Any]:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM raw_events
                WHERE processed_at IS NULL
                ORDER BY COALESCE(happened_at, received_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )
        if not rows:
            return {"tasks": 0, "knowledge_items": 0, "events": 0}
        run_id = log_run(conn, "ai_analyze", "running", f"analyzing {len(rows)} events")
        conn.commit()
        result = await self.ai.analyze_events(rows)
        task_count = self._insert_tasks(conn, result.get("tasks", []))
        knowledge_count = self._insert_knowledge(conn, result.get("knowledge_items", []))
        event_ids = [row["id"] for row in rows]
        conn.executemany("UPDATE raw_events SET processed_at=? WHERE id=?", [(utc_now(), event_id) for event_id in event_ids])
        log_run(
            conn,
            "ai_analyze",
            "ok",
            f"tasks {task_count}, knowledge {knowledge_count}",
            {"event_ids": event_ids},
            run_id,
        )
        return {"tasks": task_count, "knowledge_items": knowledge_count, "events": len(rows)}

    def _insert_raw_event(
        self,
        conn: sqlite3.Connection,
        source: str,
        source_id: str,
        source_type: str,
        title: str,
        body: str,
        author: str | None,
        conversation: str | None,
        happened_at: str | None,
        payload: dict[str, Any],
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        digest = content_hash(source, source_id, title, body)
        now = utc_now()
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO raw_events(
                source, source_id, source_type, title, body, author, conversation,
                happened_at, received_at, payload_json, provenance_json, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                source_id,
                source_type,
                title or source,
                body,
                author,
                conversation,
                happened_at,
                now,
                json_dumps(payload),
                json_dumps(provenance),
                digest,
            ),
        )
        created = cur.rowcount > 0
        row = conn.execute("SELECT * FROM raw_events WHERE content_hash=?", (digest,)).fetchone()
        event = row_to_dict(row) or {}
        event["created"] = created
        return event

    def _upsert_attachment_event(
        self,
        conn: sqlite3.Connection,
        *,
        normalized: dict[str, Any],
        body: str,
        output_path: Path,
        extract_ok: bool,
        ocr_payload: dict[str, Any],
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        title = f"微信图片：{normalized['conversation'] or '微信'} / {normalized['sender'] or '未知发送者'}"
        payload = {
            **normalized["payload"],
            "extracted_path": str(output_path),
            "extract_ok": extract_ok,
            "ocr": ocr_payload,
        }
        existing = conn.execute(
            "SELECT * FROM raw_events WHERE source='wechat_attachment' AND source_id=? ORDER BY id DESC LIMIT 1",
            (normalized["attachment_id"],),
        ).fetchone()
        if existing:
            existing_body = existing["body"] or ""
            should_update = body != existing_body or ocr_payload.get("ok")
            if should_update:
                conn.execute(
                    """
                    UPDATE raw_events
                    SET title=?, body=?, payload_json=?, provenance_json=?, processed_at=NULL
                    WHERE id=?
                    """,
                    (title, body, json_dumps(payload), json_dumps(provenance), existing["id"]),
                )
            row = conn.execute("SELECT * FROM raw_events WHERE id=?", (existing["id"],)).fetchone()
            event = row_to_dict(row) or {}
            event["created"] = False
            return event

        return self._insert_raw_event(
            conn,
            source="wechat_attachment",
            source_id=normalized["attachment_id"],
            source_type=normalized["kind"],
            title=title,
            body=body,
            author=normalized["sender"],
            conversation=normalized["conversation"],
            happened_at=normalized["message_time"],
            payload=payload,
            provenance=provenance,
        )

    def _insert_wechat_message(self, conn: sqlite3.Connection, item: dict[str, Any], context: dict[str, Any]) -> bool:
        normalized = normalize_message(item, context)
        if not normalized["content"]:
            return False
        source_id = (
            normalized["wx_msg_id"]
            or content_hash(normalized["username"], normalized["conversation"], normalized["message_time"], normalized["content"])
        )
        event = self._insert_raw_event(
            conn,
            source="wechat",
            source_id=source_id,
            source_type="message",
            title=f"{normalized['conversation'] or '微信'} / {normalized['sender'] or '未知发送者'}",
            body=normalized["content"],
            author=normalized["sender"],
            conversation=normalized["conversation"],
            happened_at=normalized["message_time"],
            payload={**normalized["payload"], "username": normalized["username"]},
            provenance=context,
        )
        if not event.get("created"):
            return False
        conn.execute(
            """
            INSERT OR IGNORE INTO messages(
                raw_event_id, wx_msg_id, conversation, chat_type, sender,
                sender_username, sender_contact_display, sender_group_nickname,
                content, message_time, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                normalized["wx_msg_id"],
                normalized["conversation"],
                normalized["chat_type"],
                normalized["sender"],
                normalized["sender_username"],
                normalized["sender_contact_display"],
                normalized["sender_group_nickname"],
                normalized["content"],
                normalized["message_time"],
                json_dumps(normalized["payload"]),
            ),
        )
        return True

    def _attachment_output_path(self, normalized: dict[str, Any]) -> Path:
        day = _day_from_timestamp(normalized.get("message_time"))
        chat = _safe_filename(normalized.get("conversation") or normalized.get("username") or "wechat")
        local_id = normalized.get("local_id") or content_hash(normalized.get("attachment_id"))[:12]
        return self.settings.attachments_dir / day / f"{chat}-{local_id}.jpg"

    def _insert_tasks(self, conn: sqlite3.Connection, tasks: list[dict[str, Any]]) -> int:
        count = 0
        for task in tasks:
            now = utc_now()
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO tasks(
                    title, summary, status, priority_score, priority_label, project, owner,
                    next_action, due_at, source_event_ids_json, provenance_json, created_at, updated_at
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.get("title", "未命名任务"),
                    task.get("summary", ""),
                    int(task.get("priority_score", 50)),
                    task.get("priority_label", "P2"),
                    task.get("project", "当前项目"),
                    task.get("owner", "me"),
                    task.get("next_action", "确认下一步。"),
                    task.get("due_at"),
                    json_dumps(task.get("source_event_ids", [])),
                    json_dumps(task.get("provenance", {"mode": "llm_or_heuristic"})),
                    now,
                    now,
                ),
            )
            if cur.rowcount:
                count += 1
            row = conn.execute(
                "SELECT * FROM tasks WHERE title=? AND next_action=?",
                (task.get("title", "未命名任务"), task.get("next_action", "确认下一步。")),
            ).fetchone()
            if row and int(row["priority_score"]) >= 70:
                self._upsert_reminder_for_task(conn, row)
        return count

    def _insert_knowledge(self, conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
        count = 0
        for item in items:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_items(
                    title, domain, summary, novelty_score, urgency, work_relevance,
                    key_questions_json, followups_json, source_event_ids_json, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("title", "未命名知识项"),
                    item.get("domain", "其他"),
                    item.get("summary", ""),
                    int(item.get("novelty_score", 60)),
                    item.get("urgency", "medium"),
                    item.get("work_relevance", "需要判断其对当前项目的影响。"),
                    json_dumps(item.get("key_questions", [])),
                    json_dumps(item.get("followups", [])),
                    json_dumps(item.get("source_event_ids", [])),
                    json_dumps(item.get("provenance", {"mode": "llm_or_heuristic"})),
                    utc_now(),
                ),
            )
            if cur.rowcount:
                count += 1
        return count

    def _upsert_reminder_for_task(self, conn: sqlite3.Connection, task: sqlite3.Row) -> None:
        remind_at = task["due_at"] or utc_now()
        dedup_key = f"task:{task['id']}:priority"
        conn.execute(
            """
            INSERT OR IGNORE INTO reminders(
                target_type, target_id, title, body, channel, status, remind_at, dedup_key, created_at
            ) VALUES (?, ?, ?, ?, 'toast', 'pending', ?, ?, ?)
            """,
            (
                "task",
                task["id"],
                f"{task['priority_label']} {task['title']}",
                task["next_action"],
                remind_at,
                dedup_key,
                utc_now(),
            ),
        )


def _iter_documents(root: Path, workbench_root: Path):
    if not root.exists():
        return
    excluded = {".git", "node_modules", ".venv", "__pycache__", "dist", "data", ".codex_tmp"}
    for path in root.rglob("*"):
        if any(part in excluded for part in path.parts):
            continue
        if workbench_root in path.parents and "data" in path.parts:
            continue
        if supported_document(path):
            yield path


def _safe_filename(value: Any) -> str:
    text = str(value or "untitled")
    text = re.sub(r"[^\w\-.一-龥]+", "_", text, flags=re.U)
    return text.strip("._")[:80] or "untitled"


def _day_from_timestamp(value: Any) -> str:
    text = str(value or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text)).date().isoformat()
        except (OverflowError, ValueError, OSError):
            pass
    return date.today().isoformat()


def _ocr_payload(result: Any) -> dict[str, Any]:
    if result is None:
        return {"ok": False, "message": "not attempted"}
    return {
        "ok": bool(result.ok),
        "provider": result.provider,
        "message": result.message,
        "text_chars": len(result.text or ""),
        "meta": result.meta or {},
    }

