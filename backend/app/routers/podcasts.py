from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from ..config import get_settings
from ..db import db_session, log_run, row_to_dict
from ..models import ApiResponse
from ..services.podcast import PodcastService, list_learning_packs
from ..services.researchcast import ResearchCastService

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])


@router.get("")
def list_podcasts():
    with db_session() as conn:
        return list_learning_packs(conn)


@router.post("/today-learning-pack", response_model=ApiResponse)
async def create_today_learning_pack(regenerate: bool = False, use_notebooklm: bool = False):
    service = ResearchCastService(get_settings())
    with db_session() as conn:
        try:
            pack = await service.create_today_learning_pack(conn, regenerate=regenerate)
        except ValueError as exc:
            return ApiResponse(ok=False, message=str(exc))
        log_run(
            conn,
            "researchcast_daily",
            "ok",
            "generated ResearchCast learning pack",
            {
                "pack_id": pack.get("id"),
                "status": pack.get("status"),
                "audio_status": pack.get("audio_status"),
                "knowledge_item_ids": pack.get("knowledge_item_ids"),
            },
        )
        return ApiResponse(data=pack, message="今日 ResearchCast 已生成")


@router.post("/researchcast-audio", response_model=ApiResponse)
async def continue_researchcast_audio(limit: int = 3):
    service = ResearchCastService(get_settings())
    with db_session() as conn:
        result = await service.continue_pending_audio(conn, limit=limit, wait_seconds=60)
        log_run(
            conn,
            "researchcast_audio",
            "ok",
            "checked pending ResearchCast audio",
            result,
        )
        return ApiResponse(data=result, message="ResearchCast pending audio checked")


@router.post("/{pack_id}/regenerate-researchcast", response_model=ApiResponse)
async def regenerate_researchcast(pack_id: int):
    service = ResearchCastService(get_settings())
    with db_session() as conn:
        pack = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone())
        if not pack:
            return ApiResponse(ok=False, message="Learning pack not found")
        item_ids = pack.get("knowledge_item_ids") or []
        try:
            result = await service.create_learning_pack(
                conn,
                knowledge_item_ids=[int(item_id) for item_id in item_ids],
                title=pack.get("title"),
                regenerate=True,
            )
        except ValueError as exc:
            return ApiResponse(ok=False, message=str(exc))
        return ApiResponse(data=result, message="ResearchCast regenerated")


@router.post("/sync-notebooklm", response_model=ApiResponse)
def sync_notebooklm(limit: int = 3, force: bool = False):
    service = PodcastService(get_settings())
    with db_session() as conn:
        try:
            result = service.sync_pending_notebooklm(conn, limit=limit, force=force)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            return ApiResponse(
                ok=False,
                data={"pending": 0, "attempted": 0, "updated": 0, "ready": 0, "auth_required": False},
                message="Database is busy; retry NotebookLM sync later",
            )
        log_run(
            conn,
            "notebooklm_sync",
            "warning" if result.get("auth_required") else "ok",
            result.get("message", "NotebookLM sync checked pending packs"),
            {
                "pending": result.get("pending"),
                "attempted": result.get("attempted"),
                "updated": result.get("updated"),
                "ready": result.get("ready"),
                "auth_required": result.get("auth_required"),
                "failed": len(result.get("failed") or []),
            },
        )
        return ApiResponse(data=result, message=result.get("message", "NotebookLM sync checked pending packs"))


@router.post("/{pack_id}/notebooklm", response_model=ApiResponse)
def generate_notebooklm(pack_id: int):
    settings = get_settings()
    service = PodcastService(settings)
    with db_session() as conn:
        pack = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone())
        if not pack:
            return ApiResponse(ok=False, message="Learning pack not found")
        result = service.sync_notebooklm_pack(conn, pack)
        ok = result.get("status") == "notebooklm_audio_ready"
        return ApiResponse(ok=ok, data=result, message=result.get("status") or "")

