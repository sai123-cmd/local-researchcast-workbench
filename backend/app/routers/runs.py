from __future__ import annotations

from fastapi import APIRouter

from ..db import db_session, rows_to_dicts

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs(limit: int = 80):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?",
            (min(max(limit, 1), 300),),
        ).fetchall()
        return rows_to_dicts(rows)

