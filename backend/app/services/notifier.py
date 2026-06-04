from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ..db import utc_now


def send_toast(title: str, body: str) -> tuple[bool, str]:
    try:
        from winotify import Notification

        toast = Notification(app_id="Local ResearchCast Workbench", title=title, msg=body)
        toast.show()
        return True, "sent with winotify"
    except Exception as exc:
        return False, f"toast unavailable: {exc}"


def send_due_reminders(conn: sqlite3.Connection) -> dict[str, Any]:
    now = datetime.now().isoformat()
    rows = conn.execute(
        """
        SELECT * FROM reminders
        WHERE status='pending' AND remind_at <= ?
        ORDER BY remind_at ASC
        LIMIT 20
        """,
        (now,),
    ).fetchall()
    sent = 0
    errors: list[str] = []
    for row in rows:
        ok, message = send_toast(row["title"], row["body"])
        status = "sent" if ok else "failed"
        conn.execute(
            "UPDATE reminders SET status=?, sent_at=? WHERE id=?",
            (status, utc_now(), row["id"]),
        )
        if ok:
            sent += 1
        else:
            errors.append(message)
    return {"sent": sent, "failed": len(errors), "errors": errors[:5]}

