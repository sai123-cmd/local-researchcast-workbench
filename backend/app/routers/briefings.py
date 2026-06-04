from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..db import db_session, log_run
from ..models import ApiResponse
from ..services.briefing import BriefingService

router = APIRouter(prefix="/api/briefings", tags=["briefings"])


@router.get("/today")
async def today_briefing(regenerate: bool = False):
    service = BriefingService(get_settings())
    with db_session() as conn:
        briefing = await service.get_today(conn, regenerate=regenerate)
        return briefing


@router.post("/today/regenerate", response_model=ApiResponse)
async def regenerate_today_briefing() -> ApiResponse:
    service = BriefingService(get_settings())
    with db_session() as conn:
        briefing = await service.generate_today(conn)
        log_run(
            conn,
            "daily_briefing",
            "ok",
            "generated today briefing",
            {
                "briefing_id": briefing.get("id"),
                "mode": (briefing.get("provenance") or {}).get("mode"),
            },
        )
        return ApiResponse(data=briefing, message="今日作战简报已刷新")

