from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ..config import Settings
from ..db import db_session, log_run
from .briefing import BriefingService
from .ingest import IngestionService
from .notifier import send_due_reminders
from .podcast import PodcastService
from .researchcast import ResearchCastService


def start_scheduler(settings: Settings) -> Any | None:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception:
        return None

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    service = IngestionService(settings)
    briefing_service = BriefingService(settings)
    podcast_service = PodcastService(settings)
    researchcast_service = ResearchCastService(settings)

    def poll_wechat_job() -> None:
        with db_session() as conn:
            service.poll_wechat(conn)
            conn.commit()
            asyncio.run(service.analyze_unprocessed(conn))

    def scan_inbox_job() -> None:
        with db_session() as conn:
            service.scan_documents(conn, scope="inbox")
            conn.commit()
            asyncio.run(service.analyze_unprocessed(conn))

    def scan_wechat_files_job() -> None:
        with db_session() as conn:
            service.scan_documents(conn, scope="wechat")
            conn.commit()
            asyncio.run(service.analyze_unprocessed(conn, limit=120))

    def backfill_today_job() -> None:
        with db_session() as conn:
            service.backfill_wechat_today(conn)
            conn.commit()
            asyncio.run(service.analyze_unprocessed(conn, limit=120))

    def attachment_scan_job() -> None:
        with db_session() as conn:
            service.ingest_wechat_attachments(conn)
            service.ocr_existing_attachments(conn, limit=60)
            conn.commit()
            asyncio.run(service.analyze_unprocessed(conn, limit=80))

    def reminders_job() -> None:
        with db_session() as conn:
            result = send_due_reminders(conn)
            if result["sent"] or result["failed"]:
                log_run(conn, "reminder_dispatch", "ok", "dispatched reminders", result)

    def briefing_job() -> None:
        with db_session() as conn:
            briefing = asyncio.run(briefing_service.generate_today(conn))
            log_run(
                conn,
                "daily_briefing",
                "ok",
                "refreshed today briefing",
                _summarize_payload({"briefing": briefing}),
            )

    def learning_audio_job(regenerate: bool = False) -> None:
        with db_session() as conn:
            try:
                pack = asyncio.run(researchcast_service.create_today_learning_pack(conn, regenerate=regenerate))
                log_run(
                    conn,
                    "researchcast_daily",
                    "ok",
                    "ensured ResearchCast learning pack",
                    _summarize_payload({"pack": pack}),
                )
            except ValueError as exc:
                log_run(conn, "researchcast_daily", "warning", str(exc), {})

    def researchcast_audio_job() -> None:
        with db_session() as conn:
            result = asyncio.run(researchcast_service.continue_pending_audio(conn, limit=3, wait_seconds=45))
            if result.get("pending"):
                log_run(
                    conn,
                    "researchcast_audio",
                    "ok" if result.get("failed") == 0 else "warning",
                    "checked pending ResearchCast audio",
                    result,
                )

    def notebooklm_sync_job(force: bool = False) -> None:
        try:
            with db_session() as conn:
                result = podcast_service.sync_pending_notebooklm(conn, limit=2, force=force)
                if result.get("pending") or result.get("auth_required") or result.get("failed"):
                    log_run(
                        conn,
                        "notebooklm_sync",
                        "warning" if result.get("auth_required") else "ok",
                        result.get("message", "NotebookLM sync checked pending packs"),
                        _summarize_payload(result),
                    )
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise

    def startup_sync_job() -> None:
        stages: dict[str, Any] = {}
        with db_session() as conn:
            run_id = log_run(conn, "startup_sync", "running", "running startup sync pipeline")
            try:
                stages["wechat_poll"] = service.poll_wechat(conn)
                stages["wechat_today_backfill"] = service.backfill_wechat_today(conn)
                stages["wechat_file_scan"] = service.scan_documents(conn, scope="wechat")
                stages["inbox_scan"] = service.scan_documents(conn, scope="inbox")
                stages["wechat_attachment_scan"] = service.ingest_wechat_attachments(conn)
                stages["attachment_ocr"] = service.ocr_existing_attachments(conn, limit=80)
                conn.commit()
                stages["analysis"] = asyncio.run(service.analyze_unprocessed(conn, limit=240))
                if settings.auto_daily_briefing:
                    stages["briefing"] = asyncio.run(briefing_service.generate_today(conn))
                if settings.auto_daily_learning_audio:
                    stages["learning_pack"] = asyncio.run(
                        researchcast_service.create_today_learning_pack(conn, regenerate=False)
                    )
                    stages["researchcast_audio"] = asyncio.run(researchcast_service.continue_pending_audio(conn, limit=2, wait_seconds=30))
                log_run(
                    conn,
                    "startup_sync",
                    "ok",
                    "startup sync completed",
                    _summarize_payload(stages),
                    run_id,
                )
            except Exception as exc:
                log_run(
                    conn,
                    "startup_sync",
                    "failed",
                    str(exc),
                    _summarize_payload(stages),
                    run_id,
                )

    scheduler.add_job(poll_wechat_job, "interval", seconds=settings.wx_poll_seconds, id="wechat_poll", max_instances=1)
    scheduler.add_job(backfill_today_job, "interval", minutes=30, id="wechat_today_backfill", max_instances=1)
    scheduler.add_job(attachment_scan_job, "interval", minutes=45, id="wechat_attachment_scan", max_instances=1)
    scheduler.add_job(scan_inbox_job, "interval", minutes=5, id="inbox_scan", max_instances=1)
    scheduler.add_job(scan_wechat_files_job, "interval", minutes=20, id="wechat_file_scan", max_instances=1)
    scheduler.add_job(reminders_job, "interval", seconds=45, id="reminders", max_instances=1)
    if settings.auto_daily_briefing:
        scheduler.add_job(briefing_job, "interval", minutes=15, id="daily_briefing", max_instances=1)
    if settings.auto_daily_learning_audio:
        scheduler.add_job(
            learning_audio_job,
            "cron",
            hour="8,13,18",
            minute=10,
            id="daily_learning_audio",
            max_instances=1,
            kwargs={"regenerate": True},
        )
        scheduler.add_job(
            researchcast_audio_job,
            "interval",
            minutes=10,
            id="researchcast_audio",
            max_instances=1,
        )
    if settings.auto_startup_sync:
        scheduler.add_job(
            startup_sync_job,
            "date",
            run_date=datetime.now() + timedelta(seconds=3),
            id="startup_sync",
            max_instances=1,
        )
    scheduler.start()
    return scheduler


def _summarize_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [_summarize_payload(item) for item in value[:8]]
    if not isinstance(value, dict):
        return value
    keep = {
        "ok",
        "inserted",
        "processed",
        "skipped",
        "updated",
        "chats_scanned",
        "tasks",
        "knowledge_items",
        "events",
        "id",
        "title",
        "status",
        "message",
        "since",
        "until",
        "local_audio_path",
        "engine",
        "research_status",
        "audio_status",
        "blog_url",
        "audio_error",
        "notebooklm_status",
        "pending",
        "attempted",
        "updated",
        "ready",
        "auth_required",
    }
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in keep:
            result[key] = item
        elif key == "failed" and isinstance(item, list):
            result[key] = len(item)
        elif key in {"briefing", "pack", "learning_pack"} and isinstance(item, dict):
            result[key] = _summarize_payload(item)
        elif key.endswith("_scan") or key.endswith("_poll") or key.endswith("_backfill") or key in {
            "analysis",
            "briefing",
            "learning_pack",
            "wechat_file_scan",
            "inbox_scan",
            "wechat_attachment_scan",
            "attachment_ocr",
            "wechat_today_backfill",
            "wechat_poll",
            "notebooklm_sync",
        }:
            result[key] = _summarize_payload(item)
    return result

