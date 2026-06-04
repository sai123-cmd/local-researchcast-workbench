from __future__ import annotations

from fastapi import APIRouter, Query

from ..config import get_settings
from ..db import db_session, rows_to_dicts
from ..models import ApiResponse, ManualIngestRequest
from ..services.ingest import IngestionService

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


@router.get("")
def list_inbox(limit: int = Query(80, ge=1, le=500), source: str | None = None):
    where = ""
    params: list[object] = []
    if source:
        where = "WHERE source=?"
        params.append(source)
    with db_session() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM raw_events
            {where}
            ORDER BY COALESCE(happened_at, received_at) DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return rows_to_dicts(rows)


@router.post("/manual", response_model=ApiResponse)
async def ingest_manual(payload: ManualIngestRequest):
    settings = get_settings()
    service = IngestionService(settings)
    with db_session() as conn:
        event = service.ingest_manual_text(
            conn,
            title=payload.title,
            body=payload.body,
            source=payload.source,
            conversation=payload.conversation,
            author=payload.author,
        )
        analysis = await service.analyze_unprocessed(conn)
        return ApiResponse(data={"event": event, "analysis": analysis})


@router.post("/poll-wechat", response_model=ApiResponse)
async def poll_wechat():
    service = IngestionService(get_settings())
    with db_session() as conn:
        poll = service.poll_wechat(conn)
        analysis = await service.analyze_unprocessed(conn)
        return ApiResponse(ok=poll.get("ok", False), data={"poll": poll, "analysis": analysis}, message=poll.get("message", ""))


@router.post("/backfill-wechat-today", response_model=ApiResponse)
async def backfill_wechat_today():
    service = IngestionService(get_settings())
    with db_session() as conn:
        backfill = service.backfill_wechat_today(conn)
        analysis = await service.analyze_unprocessed(conn, limit=120)
        return ApiResponse(ok=backfill.get("ok", False), data={"backfill": backfill, "analysis": analysis}, message=backfill.get("message", ""))


@router.post("/scan-wechat-attachments", response_model=ApiResponse)
async def scan_wechat_attachments():
    service = IngestionService(get_settings())
    with db_session() as conn:
        scan = service.ingest_wechat_attachments(conn)
        analysis = await service.analyze_unprocessed(conn, limit=80)
        return ApiResponse(ok=scan.get("ok", False), data={"scan": scan, "analysis": analysis}, message=scan.get("message", ""))


@router.post("/ocr-attachments", response_model=ApiResponse)
async def ocr_attachments(limit: int = Query(80, ge=1, le=300)):
    service = IngestionService(get_settings())
    with db_session() as conn:
        scan = service.ocr_existing_attachments(conn, limit=limit)
        analysis = await service.analyze_unprocessed(conn, limit=80)
        return ApiResponse(ok=scan.get("ok", False), data={"scan": scan, "analysis": analysis}, message=scan.get("message", ""))


@router.post("/scan-documents", response_model=ApiResponse)
async def scan_documents(scope: str = Query("inbox", pattern="^(inbox|workspace|wechat)$")):
    service = IngestionService(get_settings())
    with db_session() as conn:
        scan = service.scan_documents(conn, scope=scope)
        analysis = await service.analyze_unprocessed(conn)
        return ApiResponse(data={"scan": scan, "analysis": analysis})


@router.post("/analyze", response_model=ApiResponse)
async def analyze_unprocessed():
    service = IngestionService(get_settings())
    with db_session() as conn:
        result = await service.analyze_unprocessed(conn)
        return ApiResponse(data=result)

