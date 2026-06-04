from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import db_session, row_to_dict, rows_to_dicts, utc_now
from ..models import ApiResponse, TaskPatch
from ..services.task_context import get_task_context

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks(status: str | None = None):
    where = ""
    params: list[object] = []
    if status:
        where = "WHERE status=?"
        params.append(status)
    with db_session() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM tasks
            {where}
            ORDER BY
                CASE priority_label WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                priority_score DESC,
                COALESCE(due_at, updated_at) ASC
            LIMIT 200
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


@router.get("/{task_id}/context")
def task_context(task_id: int):
    with db_session() as conn:
        context = get_task_context(conn, task_id)
        if not context:
            raise HTTPException(status_code=404, detail="Task not found")
        return context


@router.patch("/{task_id}", response_model=ApiResponse)
def patch_task(task_id: int, patch: TaskPatch):
    allowed = patch.model_dump(exclude_unset=True)
    if not allowed:
        return ApiResponse(data=None)
    fields = []
    values: list[object] = []
    for key, value in allowed.items():
        fields.append(f"{key}=?")
        values.append(value)
    fields.append("updated_at=?")
    values.append(utc_now())
    values.append(task_id)
    with db_session() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            _sync_task_reminder(conn, row)
        return ApiResponse(data=row_to_dict(row) if row else None)


def _sync_task_reminder(conn, task) -> None:
    now = utc_now()
    task_id = int(task["id"])
    if task["status"] != "open":
        conn.execute(
            """
            UPDATE reminders
            SET status='dismissed', sent_at=COALESCE(sent_at, ?)
            WHERE target_type='task' AND target_id=? AND status='pending'
            """,
            (now, task_id),
        )
        return

    if not task["due_at"] and int(task["priority_score"] or 0) < 70:
        return

    dedup_key = f"task:{task_id}:priority"
    remind_at = task["due_at"] or now
    conn.execute(
        """
        INSERT INTO reminders(
            target_type, target_id, title, body, channel, status, remind_at, dedup_key, created_at
        ) VALUES (?, ?, ?, ?, 'toast', 'pending', ?, ?, ?)
        ON CONFLICT(dedup_key) DO UPDATE SET
            title=excluded.title,
            body=excluded.body,
            remind_at=excluded.remind_at,
            status='pending',
            sent_at=NULL
        """,
        (
            "task",
            task_id,
            f"{task['priority_label']} {task['title']}",
            task["next_action"],
            remind_at,
            dedup_key,
            now,
        ),
    )

