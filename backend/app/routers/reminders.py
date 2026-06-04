from __future__ import annotations

from fastapi import APIRouter

from ..db import db_session, rows_to_dicts
from ..models import ApiResponse
from ..services.notifier import send_due_reminders

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.get("")
def list_reminders(status: str | None = None):
    where = ""
    params: list[object] = []
    if status:
        where = "WHERE status=?"
        params.append(status)
    with db_session() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM reminders
            {where}
            ORDER BY
                CASE status WHEN 'pending' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END,
                remind_at ASC
            LIMIT 200
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


@router.post("/dispatch", response_model=ApiResponse)
def dispatch_due():
    with db_session() as conn:
        return ApiResponse(data=send_due_reminders(conn))

