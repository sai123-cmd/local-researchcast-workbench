from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..config import get_settings
from ..db import db_session, log_run, row_to_dict, rows_to_dicts
from ..models import ApiResponse, LearningPackRequest
from ..services.knowledge_context import get_knowledge_context
from ..services.podcast import PodcastService
from ..services.researchcast import ResearchCastService

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("")
def list_knowledge():
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM knowledge_items
            ORDER BY novelty_score DESC, created_at DESC
            LIMIT 200
            """
        ).fetchall()
        return rows_to_dicts(rows)


@router.get("/{knowledge_id}/context")
def knowledge_context(knowledge_id: int):
    with db_session() as conn:
        context = get_knowledge_context(conn, knowledge_id)
        if not context:
            raise HTTPException(status_code=404, detail="Knowledge item not found")
        return context


@router.post("/learning-pack", response_model=ApiResponse)
async def create_learning_pack(payload: LearningPackRequest, background_tasks: BackgroundTasks):
    service = ResearchCastService(get_settings())
    with db_session() as conn:
        pack = await service.create_learning_pack(
            conn,
            knowledge_item_ids=payload.knowledge_item_ids,
            title=payload.title,
        )
        return ApiResponse(data=pack)


def _sync_learning_pack_to_notebooklm(pack_id: int) -> None:
    service = PodcastService(get_settings())
    with db_session() as conn:
        pack = row_to_dict(conn.execute("SELECT * FROM learning_packs WHERE id=?", (pack_id,)).fetchone())
        if not pack:
            return
        result = service.sync_notebooklm_pack(conn, pack)
        log_run(
            conn,
            "notebooklm_sync",
            "ok" if result.get("status") == "notebooklm_audio_ready" else "warning",
            f"synced learning pack {pack_id}: {result.get('status')}",
            {"pack_id": pack_id, **result},
        )

