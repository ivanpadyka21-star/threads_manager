"""SQLite-backed store for the SMM dashboard.

Holds the accounts a specialist manages, the tasks queued against them, and an
audit trail. Uses the stdlib ``sqlite3`` (no extra dependency) and is safe to
share across Flask's threaded requests and background job threads.

Design guardrails baked in:
- Every account action is a *task* that starts in ``pending`` and must be
  explicitly approved by the user before it is considered actionable.
- Each account carries a ``daily_limit``; approving beyond it is refused.
- Approvals/rejections are written to the audit trail.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

# Task lifecycle states.
TASK_STATES = ("pending", "approved", "done", "failed", "rejected")
# States that count against an account's daily limit.
_COUNTS_AGAINST_LIMIT = ("approved", "done")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RateLimitError(Exception):
    """Raised when approving a task would exceed the account's daily limit."""


class Store:
    """Thread-safe SQLite repository for accounts, tasks and audit records."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + our own lock so Flask threads can share it.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    # -- schema -------------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    handle TEXT DEFAULT '',
                    platform TEXT DEFAULT 'Threads',
                    proxy_string TEXT DEFAULT '',
                    daily_limit INTEGER DEFAULT 20,
                    tone TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    kind TEXT NOT NULL DEFAULT 'post',
                    title TEXT DEFAULT '',
                    payload TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    deadline TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    account_id INTEGER,
                    action TEXT NOT NULL,
                    detail TEXT DEFAULT ''
                );
                """
            )

    # -- accounts -----------------------------------------------------------
    def add_account(self, **fields) -> dict:
        cols = {
            "name": fields.get("name", "").strip(),
            "handle": fields.get("handle", "").strip(),
            "platform": fields.get("platform", "Threads"),
            "proxy_string": fields.get("proxy_string", "").strip(),
            "daily_limit": int(fields.get("daily_limit", 20) or 20),
            "tone": fields.get("tone", "").strip(),
            "status": fields.get("status", "active"),
            "notes": fields.get("notes", "").strip(),
            "created_at": _now(),
        }
        if not cols["name"]:
            raise ValueError("Account name is required.")
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO accounts
                   (name, handle, platform, proxy_string, daily_limit, tone,
                    status, notes, created_at)
                   VALUES (:name, :handle, :platform, :proxy_string, :daily_limit,
                           :tone, :status, :notes, :created_at)""",
                cols,
            )
            account_id = cur.lastrowid
        self.log("account.create", cols["name"], account_id)
        return self.get_account(account_id)

    def list_accounts(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accounts ORDER BY created_at DESC"
            ).fetchall()
        return [self._account_dict(r) for r in rows]

    def get_account(self, account_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return self._account_dict(row) if row else None

    def update_account(self, account_id: int, **fields) -> Optional[dict]:
        allowed = {
            "name", "handle", "platform", "proxy_string",
            "daily_limit", "tone", "status", "notes",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_account(account_id)
        sets = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = account_id
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE accounts SET {sets} WHERE id = :id", updates
            )
        self.log("account.update", str(list(fields.keys())), account_id)
        return self.get_account(account_id)

    def delete_account(self, account_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM tasks WHERE account_id = ?", (account_id,))
            self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self.log("account.delete", "", account_id)

    def _account_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["used_today"] = self._used_today(d["id"])
        d["remaining_today"] = max(0, d["daily_limit"] - d["used_today"])
        return d

    # -- tasks --------------------------------------------------------------
    def add_task(self, **fields) -> dict:
        cols = {
            "account_id": fields.get("account_id"),
            "kind": fields.get("kind", "post"),
            "title": fields.get("title", "").strip(),
            "payload": fields.get("payload", "").strip(),
            "status": "pending",
            "deadline": (fields.get("deadline") or None),
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO tasks
                   (account_id, kind, title, payload, status, deadline,
                    created_at, updated_at)
                   VALUES (:account_id, :kind, :title, :payload, :status,
                           :deadline, :created_at, :updated_at)""",
                cols,
            )
            task_id = cur.lastrowid
        self.log("task.create", f"{cols['kind']}: {cols['title']}", cols["account_id"])
        return self.get_task(task_id)

    def list_tasks(
        self, account_id: Optional[int] = None, status: Optional[str] = None
    ) -> List[dict]:
        query = "SELECT * FROM tasks"
        clauses, params = [], []
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY (deadline IS NULL), deadline ASC, created_at DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def approve_task(self, task_id: int) -> dict:
        """Approve a task, enforcing the account's daily limit.

        Raises:
            KeyError: If the task does not exist.
            RateLimitError: If the account is at its daily limit.
        """
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        account_id = task["account_id"]
        if account_id is not None:
            account = self.get_account(account_id)
            if account and account["used_today"] >= account["daily_limit"]:
                raise RateLimitError(
                    f"Account #{account_id} is at its daily limit "
                    f"({account['daily_limit']})."
                )
        self._set_status(task_id, "approved")
        self.log("task.approve", task["title"], account_id)
        return self.get_task(task_id)

    def set_task_status(self, task_id: int, status: str) -> dict:
        if status not in TASK_STATES:
            raise ValueError(f"Unknown task status: {status!r}")
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        self._set_status(task_id, status)
        self.log(f"task.{status}", task["title"], task["account_id"])
        return self.get_task(task_id)

    def _set_status(self, task_id: int, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), task_id),
            )

    def _used_today(self, account_id: int) -> int:
        today = date.today().isoformat()
        placeholders = ",".join("?" for _ in _COUNTS_AGAINST_LIMIT)
        with self._lock:
            row = self._conn.execute(
                f"""SELECT COUNT(*) AS n FROM tasks
                    WHERE account_id = ? AND status IN ({placeholders})
                      AND substr(updated_at, 1, 10) = ?""",
                (account_id, *_COUNTS_AGAINST_LIMIT, today),
            ).fetchone()
        return int(row["n"]) if row else 0

    # -- audit & stats ------------------------------------------------------
    def log(self, action: str, detail: str = "", account_id: Optional[int] = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit (ts, account_id, action, detail) VALUES (?, ?, ?, ?)",
                (_now(), account_id, action, detail),
            )

    def list_audit(self, limit: int = 100) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Aggregate counts for the dashboard overview."""
        with self._lock:
            accounts = self._conn.execute(
                "SELECT COUNT(*) AS n FROM accounts"
            ).fetchone()["n"]
            by_status = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
        status_counts = {s: 0 for s in TASK_STATES}
        for r in by_status:
            status_counts[r["status"]] = r["n"]
        return {
            "accounts": accounts,
            "tasks": status_counts,
            "pending": status_counts.get("pending", 0),
        }

    def per_account_stats(self) -> List[dict]:
        """One row per account with its task counts and usage."""
        result = []
        for acc in self.list_accounts():
            tasks = self.list_tasks(account_id=acc["id"])
            counts = {s: 0 for s in TASK_STATES}
            for t in tasks:
                counts[t["status"]] = counts.get(t["status"], 0) + 1
            result.append(
                {
                    "id": acc["id"],
                    "name": acc["name"],
                    "handle": acc["handle"],
                    "daily_limit": acc["daily_limit"],
                    "used_today": acc["used_today"],
                    "remaining_today": acc["remaining_today"],
                    "total_tasks": len(tasks),
                    "counts": counts,
                }
            )
        return result

    def close(self) -> None:
        with self._lock:
            self._conn.close()
