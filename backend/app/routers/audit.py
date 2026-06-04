from __future__ import annotations

from fastapi import APIRouter

from ..db import db_session, rows_to_dicts

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/external-calls")
def list_external_calls(limit: int = 80):
    limit = max(1, min(limit, 200))
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM external_calls
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)

