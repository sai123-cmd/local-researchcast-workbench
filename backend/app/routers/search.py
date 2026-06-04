from __future__ import annotations

from fastapi import APIRouter, Query

from ..db import db_session
from ..services.search import SearchService

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
def search(q: str = Query("", max_length=200), limit: int = Query(60, ge=1, le=100)):
    service = SearchService()
    with db_session() as conn:
        return service.search(conn, q, limit=limit)

