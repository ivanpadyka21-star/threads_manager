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

import os
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from pathlib import Path


def _resolve_tz():
    """Local timezone for the dashboard (default Kyiv). Falls back gracefully."""
    name = os.getenv("E2E_TZ", "Europe/Kyiv")
    try:
        from zoneinfo import ZoneInfo
        for candidate in (name, "Europe/Kiev"):
            try:
                return ZoneInfo(candidate)
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
    except Exception:  # noqa: BLE001 - zoneinfo/tzdata unavailable
        pass
    return timezone(timedelta(hours=3))  # Kyiv summer offset fallback


_TZ = _resolve_tz()

# Task lifecycle states. "scheduled" tasks are auto-published by the scheduler
# when their scheduled_for time arrives.
TASK_STATES = ("pending", "approved", "scheduled", "done", "failed", "rejected")
# States that count against an account's daily limit.
_COUNTS_AGAINST_LIMIT = ("approved", "done")

# Severity levels attached to audit events, used by the analytics layer.
# ok = a good, completed action; info = neutral; warn = a soft problem;
# error = a bug / failed or problematic action.
LEVELS = ("ok", "info", "warn", "error")
# How each action maps to a severity for effectiveness scoring.
_ACTION_LEVEL = {
    "task.approve": "ok", "task.done": "ok", "task.create": "info",
    "task.reject": "warn", "task.rejected": "warn", "task.failed": "error",
    "task.delete": "info",
    "account.create": "info", "account.update": "info", "account.delete": "warn",
    "run.ok": "ok", "run.error": "error", "ai.generate": "ok", "ai.error": "error",
}


def _now() -> str:
    """Current local (Kyiv) time as a naive ISO string."""
    return datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(_TZ).date().isoformat()


def _cutoff(hours: float) -> str:
    return (
        datetime.now(_TZ).replace(tzinfo=None) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")


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
                    reminder TEXT,
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
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
        # Migrate older databases that predate newer columns.
        self._ensure_column("tasks", "reminder", "TEXT")
        self._ensure_column("tasks", "result", "TEXT")
        self._ensure_column("tasks", "language", "TEXT DEFAULT ''")
        self._ensure_column("tasks", "style", "TEXT DEFAULT ''")
        self._ensure_column("tasks", "target", "TEXT DEFAULT ''")
        self._ensure_column("tasks", "scheduled_for", "TEXT")
        self._ensure_column("tasks", "batch_id", "TEXT DEFAULT ''")
        self._ensure_column("tasks", "max_chars", "INTEGER")
        self._ensure_column("accounts", "credentials_file", "TEXT DEFAULT ''")
        self._ensure_column("accounts", "persona", "TEXT DEFAULT ''")
        self._ensure_column("audit", "level", "TEXT DEFAULT 'info'")

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        with self._lock, self._conn:
            cols = {
                r["name"]
                for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {decl}"
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
            "credentials_file": fields.get("credentials_file", "").strip(),
            "persona": fields.get("persona", "").strip(),
            "created_at": _now(),
        }
        if not cols["name"]:
            raise ValueError("Account name is required.")
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO accounts
                   (name, handle, platform, proxy_string, daily_limit, tone,
                    status, notes, credentials_file, persona, created_at)
                   VALUES (:name, :handle, :platform, :proxy_string, :daily_limit,
                           :tone, :status, :notes, :credentials_file, :persona,
                           :created_at)""",
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
            "daily_limit", "tone", "status", "notes", "credentials_file", "persona",
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
            "language": fields.get("language", "").strip(),
            "style": fields.get("style", "").strip(),
            "target": fields.get("target", "").strip(),
            "status": fields.get("status", "pending"),
            "deadline": (fields.get("deadline") or None),
            "reminder": (fields.get("reminder") or None),
            "scheduled_for": (fields.get("scheduled_for") or None),
            "batch_id": fields.get("batch_id", ""),
            "max_chars": (int(fields["max_chars"]) if fields.get("max_chars") else None),
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO tasks
                   (account_id, kind, title, payload, language, style, target,
                    status, deadline, reminder, scheduled_for, batch_id, max_chars,
                    created_at, updated_at)
                   VALUES (:account_id, :kind, :title, :payload, :language, :style,
                           :target, :status, :deadline, :reminder, :scheduled_for,
                           :batch_id, :max_chars, :created_at, :updated_at)""",
                cols,
            )
            task_id = cur.lastrowid
        self.log("task.create", f"{cols['kind']}: {cols['title']}", cols["account_id"])
        return self.get_task(task_id)

    # -- batches (multiple scheduled posts) ---------------------------------
    def add_batch(
        self,
        account_id: Optional[int],
        briefs: List[str],
        language: str = "",
        style: str = "",
        interval_minutes: int = 60,
        start_at: Optional[str] = None,
        kind: str = "post",
        max_chars: Optional[int] = None,
    ) -> dict:
        """Create up to 10 pending tasks spaced ``interval_minutes`` apart.

        Each task starts as ``pending`` (for draft review) and carries a shared
        ``batch_id`` and a computed ``scheduled_for``. Returns
        ``{batch_id, tasks}``.
        """
        briefs = [b.strip() for b in briefs if b and b.strip()][:10]
        if not briefs:
            raise ValueError("Provide at least one post brief.")
        interval = max(1, int(interval_minutes))
        base = (
            datetime.fromisoformat(start_at)
            if start_at
            else datetime.now(_TZ).replace(tzinfo=None)
        )
        batch_id = uuid.uuid4().hex
        tasks = []
        for i, brief in enumerate(briefs):
            when = (base + timedelta(minutes=interval * i)).isoformat(timespec="minutes")
            tasks.append(self.add_task(
                account_id=account_id, kind=kind,
                title=f"{i + 1}/{len(briefs)}: {brief[:40]}",
                payload=brief, language=language, style=style,
                scheduled_for=when, batch_id=batch_id, max_chars=max_chars,
            ))
        self.log("batch.create", f"{len(briefs)} posts", account_id)
        return {"batch_id": batch_id, "tasks": tasks}

    def list_batch(self, batch_id: str) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE batch_id = ? ORDER BY scheduled_for ASC",
                (batch_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def approve_batch(self, batch_id: str) -> int:
        """Move a batch's pending tasks to ``scheduled``. Returns the count."""
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'scheduled', updated_at = ? "
                "WHERE batch_id = ? AND status = 'pending'",
                (now, batch_id),
            )
            count = cur.rowcount
        self.log("batch.approve", f"{count} posts scheduled", None)
        return count

    def due_scheduled_tasks(self) -> List[dict]:
        """Scheduled tasks whose time has arrived (for the publisher)."""
        now = datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="minutes")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'scheduled' "
                "AND (scheduled_for IS NULL OR scheduled_for <= ?) "
                "ORDER BY scheduled_for ASC", (now,),
            ).fetchall()
        return [dict(r) for r in rows]

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

    def list_reminders(self) -> List[dict]:
        """Active reminders (tasks with a reminder that are not finished).

        Each row carries the task's current ``status`` so the UI can show which
        stage the task is at, plus the account name.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT t.*, a.name AS account_name
                   FROM tasks t LEFT JOIN accounts a ON a.id = t.account_id
                   WHERE t.reminder IS NOT NULL
                     AND t.status NOT IN ('done', 'rejected')
                   ORDER BY t.reminder ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def notifications(self, within_hours: float = 24) -> dict:
        """Aggregate actionable alerts for the notification bell.

        Combines due/active reminders, imminent task deadlines and recent
        problems (error events), newest first, with a badge ``count`` of the
        items that need attention.
        """
        now_s = datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
        soon_s = (datetime.now(_TZ).replace(tzinfo=None)
                  + timedelta(hours=within_hours)).isoformat(timespec="seconds")
        items: List[dict] = []

        for r in self.list_reminders():
            due = bool(r.get("reminder") and r["reminder"] <= now_s)
            items.append({
                "type": "reminder", "level": "warn" if due else "info",
                "title": r.get("title") or "Reminder", "detail": r.get("kind", ""),
                "account_id": r.get("account_id"), "account": r.get("account_name"),
                "when": r.get("reminder"), "due": due,
            })

        with self._lock:
            deadlines = self._conn.execute(
                "SELECT t.*, a.name AS account_name FROM tasks t "
                "LEFT JOIN accounts a ON a.id = t.account_id "
                "WHERE t.deadline IS NOT NULL AND t.status IN ('pending','approved') "
                "AND t.deadline <= ? ORDER BY t.deadline ASC", (soon_s,)
            ).fetchall()
            problems = self._conn.execute(
                "SELECT au.*, a.name AS account_name FROM audit au "
                "LEFT JOIN accounts a ON a.id = au.account_id "
                "WHERE au.level = 'error' AND au.ts >= ? ORDER BY au.id DESC LIMIT 20",
                (_cutoff(within_hours),),
            ).fetchall()
        for t in deadlines:
            items.append({
                "type": "deadline", "level": "warn", "title": t["title"] or "Task",
                "detail": t["kind"], "account_id": t["account_id"],
                "account": t["account_name"], "when": t["deadline"],
            })
        for e in problems:
            items.append({
                "type": "problem", "level": "error", "title": e["action"],
                "detail": e["detail"], "account_id": e["account_id"],
                "account": e["account_name"], "when": e["ts"],
            })

        items.sort(key=lambda x: x.get("when") or "", reverse=True)
        count = sum(1 for i in items if i["type"] in ("deadline", "problem") or i.get("due"))
        return {"count": count, "items": items}

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

    def delete_task(self, task_id: int) -> None:
        task = self.get_task(task_id)
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if task is not None:
            self.log("task.delete", task.get("title", ""), task.get("account_id"))

    def _set_status(self, task_id: int, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), task_id),
            )

    def set_task_result(self, task_id: int, result: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET result = ?, updated_at = ? WHERE id = ?",
                (result, _now(), task_id),
            )

    def update_task(self, task_id: int, **fields) -> Optional[dict]:
        """Update editable task fields (text, schedule, length, etc.)."""
        allowed = {
            "title", "payload", "result", "language", "style", "target",
            "max_chars", "deadline", "reminder", "scheduled_for",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_task(task_id)
        updates["updated_at"] = _now()
        sets = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = task_id
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE tasks SET {sets} WHERE id = :id", updates)
        return self.get_task(task_id)

    # -- saved prompts ------------------------------------------------------
    def add_prompt(self, name: str, text: str) -> dict:
        name = (name or "").strip()
        text = (text or "").strip()
        if not name or not text:
            raise ValueError("Prompt name and text are required.")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO prompts (name, text, created_at) VALUES (?, ?, ?)",
                (name, text, _now()),
            )
            pid = cur.lastrowid
        return self.get_prompt(pid)

    def list_prompts(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM prompts ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_prompt(self, prompt_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM prompts WHERE id = ?", (prompt_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_prompt(self, prompt_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))

    def _used_today(self, account_id: int) -> int:
        today = datetime.now(_TZ).date().isoformat()
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
    def log(
        self,
        action: str,
        detail: str = "",
        account_id: Optional[int] = None,
        level: Optional[str] = None,
    ) -> None:
        """Append an audit event. ``level`` defaults from the action name."""
        lvl = level or _ACTION_LEVEL.get(action, "info")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit (ts, account_id, action, detail, level) VALUES (?, ?, ?, ?, ?)",
                (_now(), account_id, action, detail, lvl),
            )

    # Public alias for recording an analytics event from the app layer.
    record_event = log

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

    # -- analytics ----------------------------------------------------------
    def activity_daily(self, account_id: Optional[int] = None, days: int = 14) -> List[dict]:
        """Daily activity buckets for the last ``days`` (for charts/sparklines).

        Returns one entry per day (oldest first) with total events and error
        count, so a sparkline can show load and problems over time.
        """
        start = (datetime.now(_TZ).date() - timedelta(days=days - 1))
        buckets: Dict[str, dict] = {}
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            buckets[d] = {"date": d, "total": 0, "errors": 0}
        clause = "substr(ts,1,10) >= ?"
        params: list = [start.isoformat()]
        if account_id is not None:
            clause += " AND account_id = ?"
            params.append(account_id)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT substr(ts,1,10) AS d, level, COUNT(*) AS n "
                f"FROM audit WHERE {clause} GROUP BY d, level", params
            ).fetchall()
        for r in rows:
            b = buckets.get(r["d"])
            if b is None:
                continue
            b["total"] += r["n"]
            if r["level"] == "error":
                b["errors"] += r["n"]
        return list(buckets.values())

    def effectiveness_trend(self, days: int = 30, account_id: Optional[int] = None) -> List[dict]:
        """Daily effectiveness score for the last ``days`` (a trend, not just 24h).

        Each day's score is computed from that day's audit events, so it is a
        real historical trend derived from the source of truth.
        """
        start = datetime.now(_TZ).date() - timedelta(days=days - 1)
        buckets: Dict[str, Dict[str, int]] = {}
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            buckets[d] = {lvl: 0 for lvl in LEVELS}
        clause = "substr(ts,1,10) >= ?"
        params: list = [start.isoformat()]
        if account_id is not None:
            clause += " AND account_id = ?"
            params.append(account_id)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT substr(ts,1,10) AS d, level, COUNT(*) AS n "
                f"FROM audit WHERE {clause} GROUP BY d, level", params
            ).fetchall()
        for r in rows:
            if r["d"] in buckets:
                buckets[r["d"]][r["level"]] = r["n"]
        out = []
        for d, c in buckets.items():
            succ = c["ok"] + c["info"]
            prob = c["error"] * 1.0 + c["warn"] * 0.4
            score = round(100 * succ / (succ + prob), 1) if (succ + prob) > 0 else None
            out.append({"date": d, "score": score, "total": sum(c.values()), "errors": c["error"]})
        return out

    def effectiveness(self, hours: float = 24, account_id: Optional[int] = None) -> dict:
        """Compute an explainable effectiveness score over a rolling window.

        Score = 100 * successes / (successes + weighted_problems), where
        successes are ``ok``/``info`` events and weighted_problems weigh errors
        fully and warnings partially. ``None`` when there is no activity.
        """
        cutoff = _cutoff(hours)
        clause = "ts >= ?"
        params: list = [cutoff]
        if account_id is not None:
            clause += " AND account_id = ?"
            params.append(account_id)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT level, COUNT(*) AS n FROM audit WHERE {clause} GROUP BY level",
                params,
            ).fetchall()
            recent_errors = self._conn.execute(
                f"SELECT ts, action, detail, account_id FROM audit "
                f"WHERE {clause} AND level = 'error' ORDER BY id DESC LIMIT 10",
                params,
            ).fetchall()
        counts = {lvl: 0 for lvl in LEVELS}
        for r in rows:
            counts[r["level"]] = r["n"]
        successes = counts["ok"] + counts["info"]
        problems = counts["error"] * 1.0 + counts["warn"] * 0.4
        total = successes + counts["warn"] + counts["error"]
        score = None
        if successes + problems > 0:
            score = round(100 * successes / (successes + problems), 1)
        return {
            "window_hours": hours,
            "score": score,
            "counts": counts,
            "total_events": total,
            "problems": counts["error"],
            "warnings": counts["warn"],
            "recent_errors": [dict(r) for r in recent_errors],
        }

    def accounts_effectiveness(self, hours: float = 24) -> List[dict]:
        """Per-account effectiveness + activity classification."""
        cutoff = _cutoff(hours)
        result = []
        for acc in self.list_accounts():
            eff = self.effectiveness(hours=hours, account_id=acc["id"])
            with self._lock:
                ever = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM audit WHERE account_id = ?", (acc["id"],)
                ).fetchone()["n"]
            active = eff["total_events"] > 0
            result.append({
                "id": acc["id"], "name": acc["name"], "handle": acc["handle"],
                "tone": acc["tone"], "has_proxy": bool(acc["proxy_string"]),
                "used_today": acc["used_today"], "daily_limit": acc["daily_limit"],
                "score": eff["score"], "problems": eff["problems"],
                "events": eff["total_events"],
                "state": "active" if active else ("idle" if ever else "new"),
                "sparkline": [b["total"] for b in self.activity_daily(acc["id"], days=14)],
            })
        return result

    def ai_stats(self, hours: float = 24) -> dict:
        """How the AI agent is doing over the window."""
        cutoff = _cutoff(hours)
        with self._lock:
            rows = self._conn.execute(
                "SELECT action, COUNT(*) AS n FROM audit "
                "WHERE ts >= ? AND action LIKE 'ai.%' GROUP BY action", (cutoff,)
            ).fetchall()
        counts = {r["action"]: r["n"] for r in rows}
        gen = counts.get("ai.generate", 0)
        err = counts.get("ai.error", 0)
        rate = round(100 * gen / (gen + err), 1) if (gen + err) else None
        return {"generations": gen, "errors": err, "success_rate": rate}

    def ai_usage(self, rpm: int = 5, rpd: int = 20) -> dict:
        """Estimate AI request usage from our own audit log.

        Counts successful ``ai.generate`` calls today (vs the daily limit) and
        in the last 60 seconds (vs the per-minute limit). This is a *local*
        estimate of what this app sent — it cannot see calls made elsewhere with
        the same key, and Google does not expose remaining quota via this
        endpoint.
        """
        today = datetime.now(_TZ).date().isoformat()
        minute_cutoff = (datetime.now(_TZ).replace(tzinfo=None) - timedelta(seconds=60)).isoformat(timespec="seconds")
        with self._lock:
            used_today = self._conn.execute(
                "SELECT COUNT(*) AS n FROM audit WHERE action = 'ai.generate' "
                "AND substr(ts,1,10) = ?", (today,),
            ).fetchone()["n"]
            used_minute = self._conn.execute(
                "SELECT COUNT(*) AS n FROM audit WHERE action = 'ai.generate' "
                "AND ts >= ?", (minute_cutoff,),
            ).fetchone()["n"]
            errors_today = self._conn.execute(
                "SELECT COUNT(*) AS n FROM audit WHERE action = 'ai.error' "
                "AND substr(ts,1,10) = ?", (today,),
            ).fetchone()["n"]
        return {
            "used_today": used_today, "rpd": rpd,
            "remaining_today": max(0, rpd - used_today),
            "used_minute": used_minute, "rpm": rpm,
            "remaining_minute": max(0, rpm - used_minute),
            "errors_today": errors_today,
        }

    def analytics_overview(self, hours: float = 24) -> dict:
        """Everything the Analytics tab needs in one call (real-time friendly)."""
        accounts = self.accounts_effectiveness(hours=hours)
        return {
            "window_hours": hours,
            "project": self.effectiveness(hours=hours),
            "accounts": accounts,
            "active": [a for a in accounts if a["state"] == "active"],
            "idle": [a for a in accounts if a["state"] == "idle"],
            "ai": self.ai_stats(hours=hours),
            "activity": self.activity_daily(days=14),
        }

    def analytics_summary_text(self, account_ids: Optional[List[int]], hours: float = 24) -> str:
        """A compact textual data dump for feeding to the AI analyst."""
        lines = [f"Analytics window: last {hours}h.", ""]
        proj = self.effectiveness(hours=hours)
        lines.append(f"Project effectiveness: {proj['score']}% "
                     f"(errors={proj['problems']}, warnings={proj['warnings']}, "
                     f"total events={proj['total_events']}).")
        ai = self.ai_stats(hours=hours)
        lines.append(f"AI agent: {ai['generations']} generations, {ai['errors']} errors, "
                     f"success rate {ai['success_rate']}%.")
        lines.append("")
        for acc in self.accounts_effectiveness(hours=hours):
            if account_ids and acc["id"] not in account_ids:
                continue
            lines.append(
                f"- {acc['name']} ({acc['handle'] or 'no handle'}): state={acc['state']}, "
                f"score={acc['score']}, problems={acc['problems']}, events={acc['events']}, "
                f"used_today={acc['used_today']}/{acc['daily_limit']}, "
                f"proxy={'yes' if acc['has_proxy'] else 'no'}."
            )
        if proj["recent_errors"]:
            lines.append("")
            lines.append("Recent problems:")
            for e in proj["recent_errors"][:8]:
                lines.append(f"  * {e['ts']} [{e['action']}] {e['detail']}")
        return "\n".join(lines)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
