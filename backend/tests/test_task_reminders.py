from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.routers.tasks import _sync_task_reminder  # noqa: E402


class TaskReminderSyncTest(unittest.TestCase):
    def test_done_task_dismisses_pending_reminder_and_reopen_restores_it(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                title TEXT,
                status TEXT,
                priority_score INTEGER,
                priority_label TEXT,
                next_action TEXT,
                due_at TEXT
            );
            CREATE TABLE reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT,
                target_id INTEGER,
                title TEXT,
                body TEXT,
                channel TEXT,
                status TEXT,
                remind_at TEXT,
                dedup_key TEXT UNIQUE,
                created_at TEXT,
                sent_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO tasks(id,title,status,priority_score,priority_label,next_action,due_at) VALUES (1,'供应商追问','open',80,'P1','发追问','2026-06-02T20:00:00')"
        )
        _sync_task_reminder(conn, conn.execute("SELECT * FROM tasks WHERE id=1").fetchone())
        self.assertEqual(conn.execute("SELECT status FROM reminders").fetchone()[0], "pending")

        conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        _sync_task_reminder(conn, conn.execute("SELECT * FROM tasks WHERE id=1").fetchone())
        self.assertEqual(conn.execute("SELECT status FROM reminders").fetchone()[0], "dismissed")

        conn.execute("UPDATE tasks SET status='open', due_at='2026-06-03T09:30:00' WHERE id=1")
        _sync_task_reminder(conn, conn.execute("SELECT * FROM tasks WHERE id=1").fetchone())
        row = conn.execute("SELECT status, remind_at FROM reminders").fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["remind_at"], "2026-06-03T09:30:00")


if __name__ == "__main__":
    unittest.main()

