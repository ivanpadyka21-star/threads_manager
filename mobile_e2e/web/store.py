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
import re
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
                CREATE TABLE IF NOT EXISTS feed_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT DEFAULT 'manual',
                    author TEXT DEFAULT '',
                    text TEXT NOT NULL,
                    views INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    replies INTEGER DEFAULT 0,
                    topic TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    captured_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    account_id INTEGER,
                    status TEXT DEFAULT 'planned',
                    trigger TEXT DEFAULT 'manual',
                    thesis TEXT DEFAULT '',
                    analysis TEXT DEFAULT '',
                    plan_json TEXT DEFAULT '',
                    tasks_created INTEGER DEFAULT 0,
                    batch_id TEXT DEFAULT '',
                    error TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS drops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    label TEXT DEFAULT '',
                    goal_views INTEGER DEFAULT 0,
                    goal_comments INTEGER DEFAULT 0,
                    task_ids TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'running',
                    views INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    verdict TEXT DEFAULT '',
                    measured_at TEXT
                );
                CREATE TABLE IF NOT EXISTS warmup_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    kind TEXT NOT NULL DEFAULT 'reply',
                    target_author TEXT DEFAULT '',
                    target_text TEXT DEFAULT '',
                    target_url TEXT DEFAULT '',
                    target_id TEXT DEFAULT '',
                    draft TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    published_id TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
        self._ensure_column("tasks", "published_id", "TEXT DEFAULT ''")
        self._ensure_column("tasks", "views", "INTEGER DEFAULT 0")
        self._ensure_column("tasks", "likes", "INTEGER DEFAULT 0")
        self._ensure_column("tasks", "replies", "INTEGER DEFAULT 0")
        self._ensure_column("tasks", "archetype", "TEXT DEFAULT ''")
        self._ensure_column("tasks", "theme", "TEXT DEFAULT ''")
        self._ensure_column("accounts", "credentials_file", "TEXT DEFAULT ''")
        self._ensure_column("accounts", "persona", "TEXT DEFAULT ''")
        self._ensure_column("audit", "level", "TEXT DEFAULT 'info'")
        # Did the person reply back to OUR published reply? (engagement tracking)
        self._ensure_column("warmup_actions", "got_reply", "INTEGER DEFAULT 0")

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
            "archetype": fields.get("archetype", "").strip(),
            "theme": fields.get("theme", "").strip(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO tasks
                   (account_id, kind, title, payload, language, style, target,
                    status, deadline, reminder, scheduled_for, batch_id, max_chars,
                    archetype, theme, created_at, updated_at)
                   VALUES (:account_id, :kind, :title, :payload, :language, :style,
                           :target, :status, :deadline, :reminder, :scheduled_for,
                           :batch_id, :max_chars, :archetype, :theme,
                           :created_at, :updated_at)""",
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

    def add_strategy_batch(
        self,
        account_id: Optional[int],
        posts: List[dict],
        language: str = "",
        interval_minutes: int = 90,
        start_at: Optional[str] = None,
        max_chars: Optional[int] = None,
    ) -> dict:
        """Create up to 10 pending, archetype-tagged posts for a strategy cycle.

        ``posts`` is a list of dicts: ``{brief, archetype, theme, audience,
        language}``. Tags let anti-repetition and analytics track variety.
        Returns ``{batch_id, tasks}``.
        """
        posts = [p for p in posts if isinstance(p, dict) and str(p.get("brief", "")).strip()][:10]
        if not posts:
            raise ValueError("Provide at least one planned post.")
        interval = max(1, int(interval_minutes))
        base = (
            datetime.fromisoformat(start_at)
            if start_at
            else datetime.now(_TZ).replace(tzinfo=None)
        )
        batch_id = uuid.uuid4().hex
        tasks = []
        for i, p in enumerate(posts):
            brief = str(p.get("brief", "")).strip()
            when = (base + timedelta(minutes=interval * i)).isoformat(timespec="minutes")
            style = " · ".join(x for x in (str(p.get("archetype", "")).strip(),
                                           str(p.get("audience", "")).strip()) if x)
            tasks.append(self.add_task(
                account_id=account_id, kind="post",
                title=f"{i + 1}/{len(posts)}: {brief[:40]}",
                payload=brief, language=str(p.get("language") or language).strip(),
                style=style, archetype=str(p.get("archetype", "")).strip(),
                theme=str(p.get("theme", "")).strip(),
                scheduled_for=when, batch_id=batch_id, max_chars=max_chars,
            ))
        self.log("strategy.batch", f"{len(posts)} tagged posts", account_id)
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

    def claim_scheduled_task(self, task_id: int) -> bool:
        """Atomically claim a scheduled task for publishing.

        Flips ``scheduled`` -> ``publishing`` in a single UPDATE; returns True
        only for the caller that won the race, so concurrent workers can never
        publish the same task twice.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'publishing', updated_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (_now(), task_id),
            )
            return cur.rowcount > 0

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

    def set_task_published(self, task_id: int, published_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET published_id = ?, updated_at = ? WHERE id = ?",
                (str(published_id), _now(), task_id),
            )

    def set_task_metrics(self, task_id: int, views: int, likes: int, replies: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET views = ?, likes = ?, replies = ? WHERE id = ?",
                (int(views), int(likes), int(replies), task_id),
            )

    def backfill_published_ids(self) -> int:
        """Populate published_id from the '[published <id>] ...' result prefix.

        Covers posts published before the dedicated column existed. Returns the
        number of tasks backfilled.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, result FROM tasks WHERE status = 'done' "
                "AND (published_id IS NULL OR published_id = '') "
                "AND result LIKE '[published %'"
            ).fetchall()
        n = 0
        for r in rows:
            m = re.match(r"\[published (\S+)\]", r["result"] or "")
            if m:
                self.set_task_published(r["id"], m.group(1))
                n += 1
        return n

    def published_tasks(self, limit: int = 100) -> List[dict]:
        """Done tasks that have a Threads media id (for refreshing insights)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE published_id != '' AND published_id IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def top_posts(self, by: str = "views", limit: int = 10) -> List[dict]:
        """Published posts ranked by a metric (views/likes/replies)."""
        col = by if by in ("views", "likes", "replies") else "views"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM tasks WHERE published_id != '' AND published_id IS NOT NULL "
                f"ORDER BY {col} DESC, updated_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def content_insights(self, account_id: Optional[int] = None, limit: int = 60) -> dict:
        """Learn what works from real Threads metrics on published posts.

        Analyses the text of published posts against their view/like/reply
        numbers and derives simple, honest patterns: which length wins, whether
        a direct question helps, whether opening with a greeting helps. Returns
        the top posts (verbatim, so the writer can study *why* they landed) plus
        a compact ``text`` summary ready to drop into a prompt.

        This is the data half of the quality loop: the agent reads it and steers
        the writer toward what the audience actually rewards.
        """
        with self._lock:
            if account_id:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE published_id != '' AND published_id IS NOT NULL "
                    "AND account_id = ? ORDER BY views DESC LIMIT ?", (account_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE published_id != '' AND published_id IS NOT NULL "
                    "ORDER BY views DESC LIMIT ?", (limit,),
                ).fetchall()
        posts = [dict(r) for r in rows]
        # Only posts that actually carry metrics are informative.
        posts = [p for p in posts if (p.get("views") or 0) > 0]

        def _text(p: dict) -> str:
            raw = (p.get("result") or p.get("title") or "").strip()
            return re.sub(r"^\[published [^\]]*\]\s*", "", raw)

        def _avg(group: List[dict]) -> Optional[float]:
            return round(sum(p["views"] for p in group) / len(group), 1) if group else None

        def _reply_rate(p: dict) -> float:
            # Replies per 1000 views — the closest proxy for the reply-chain
            # engine that actually drives reach on Threads.
            v = p.get("views") or 0
            return round((p.get("replies") or 0) / v * 1000, 1) if v else 0.0

        def _entry(p: dict) -> dict:
            return {"views": p.get("views") or 0, "likes": p.get("likes") or 0,
                    "replies": p.get("replies") or 0, "reply_rate": _reply_rate(p),
                    "text": _text(p)}

        top = [_entry(p) for p in
               sorted(posts, key=lambda p: p.get("views") or 0, reverse=True)[:5]]
        # Reply drivers: the real virality signal. Require a floor of views so a
        # single reply on a tiny post doesn't top the chart.
        floor = 50
        eligible = [p for p in posts if (p.get("views") or 0) >= floor] or posts
        reply_drivers = [_entry(p) for p in
                         sorted(eligible, key=_reply_rate, reverse=True)[:5]
                         if _reply_rate(p) > 0]

        # -- length buckets -------------------------------------------------
        buckets = {"short (<140)": [], "medium (140–320)": [], "long (>320)": []}
        greet_re = re.compile(
            r"^\s*(прив[іе]т|доброго|добрий|доброї|хай|hi|hey|hello|друз[іи]|"
            r"дівчат|дівчатка|хлопц|коханий|котик|привітики)", re.IGNORECASE)
        with_q, without_q, with_g, without_g = [], [], [], []
        for p in posts:
            t = _text(p)
            n = len(t)
            (buckets["short (<140)"] if n < 140 else
             buckets["medium (140–320)"] if n <= 320 else buckets["long (>320)"]).append(p)
            (with_q if "?" in t else without_q).append(p)
            (with_g if greet_re.search(t) else without_g).append(p)

        # Average replies-per-1000-views across the sample (the reply-chain signal).
        avg_reply_rate = (round(sum(_reply_rate(p) for p in posts) / len(posts), 1)
                          if posts else None)
        patterns = {
            "by_length": {k: {"count": len(v), "avg_views": _avg(v)} for k, v in buckets.items()},
            "question": {"with_avg_views": _avg(with_q), "with_count": len(with_q),
                         "without_avg_views": _avg(without_q), "without_count": len(without_q)},
            "greeting": {"with_avg_views": _avg(with_g), "with_count": len(with_g),
                         "without_avg_views": _avg(without_g), "without_count": len(without_g)},
            "avg_reply_rate": avg_reply_rate,
        }
        return {
            "sample_count": len(posts),
            "top": top,
            "reply_drivers": reply_drivers,
            "patterns": patterns,
            "text": self._insights_text(len(posts), top, reply_drivers, patterns),
        }

    @staticmethod
    def _insights_text(n: int, top: List[dict], reply_drivers: List[dict], patterns: dict) -> str:
        """Render content_insights into a compact block for prompts / the agent."""
        if not n:
            return "No published posts with metrics yet — no performance data to learn from."

        def _snip(txt: str) -> str:
            s = txt.replace("\n", " ")
            return (s[:160] + "…") if len(s) > 160 else s

        lines = [f"Learnings from {n} of your published posts (real Threads metrics).",
                 "REPLY CHAINS DRIVE REACH — replies matter more than likes; prioritise "
                 "posts that pull answers. Your best reply-drivers (replies per 1000 views):"]
        for i, p in enumerate(reply_drivers or top, 1):
            lines.append(f"  {i}. {p['reply_rate']}‰ ({p['replies']}r on {p['views']}v) — «{_snip(p['text'])}»")
        lines.append("TOP BY REACH (views / likes / replies) — study why these landed:")
        for i, p in enumerate(top, 1):
            lines.append(f"  {i}. {p['views']}v / {p['likes']}l / {p['replies']}r — «{_snip(p['text'])}»")

        def _cmp(label: str, a, b, a_name: str, b_name: str) -> Optional[str]:
            if a is None or b is None:
                return None
            if a > b:
                return f"{label}: {a_name} average {a} views vs {b_name} {b} → favour {a_name}."
            if b > a:
                return f"{label}: {b_name} average {b} views vs {a_name} {a} → favour {b_name}."
            return None

        by_len = [(k, v["avg_views"]) for k, v in patterns["by_length"].items() if v["avg_views"]]
        if by_len:
            best = max(by_len, key=lambda kv: kv[1])
            lines.append(f"Length: best-performing bucket is {best[0]} ({best[1]} avg views).")
        q = patterns["question"]
        c = _cmp("Direct question", q["with_avg_views"], q["without_avg_views"], "posts with a ?", "posts without")
        if c:
            lines.append(c)
        g = patterns["greeting"]
        c = _cmp("Opening greeting", g["with_avg_views"], g["without_avg_views"], "posts that greet", "posts without a greeting")
        if c:
            lines.append(c)
        return "\n".join(lines)

    # -- feed / competitor intelligence -------------------------------------
    def add_feed_sample(self, text: str, *, author: str = "", views: int = 0,
                        likes: int = 0, replies: int = 0, topic: str = "",
                        url: str = "", source: str = "manual") -> Optional[dict]:
        """Record a competitor / trending post to learn from (deduped by text)."""
        text = (text or "").strip()
        if not text:
            raise ValueError("Feed sample text is required.")
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT id FROM feed_samples WHERE text = ?", (text,)
            ).fetchone()
            if exists:
                return None
            cur = self._conn.execute(
                """INSERT INTO feed_samples
                   (source, author, text, views, likes, replies, topic, url, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, author.strip(), text, int(views or 0), int(likes or 0),
                 int(replies or 0), topic.strip(), url.strip(), _now()),
            )
            sid = cur.lastrowid
        self.log("feed.sample", f"{source}: {text[:60]}")
        return self.get_feed_sample(sid)

    def get_feed_sample(self, sample_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM feed_samples WHERE id = ?", (sample_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_feed_samples(self, limit: int = 100) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM feed_samples ORDER BY captured_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_feed_sample(self, sample_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM feed_samples WHERE id = ?", (sample_id,))

    def feed_insights(self, limit: int = 80) -> dict:
        """Analyse stored competitor/trend samples: the best examples by reach and
        by reply-pull, so the strategist can ride fresh niche trends and beat them.
        """
        samples = self.list_feed_samples(limit=limit)

        def _reply_rate(s: dict) -> float:
            v = s.get("views") or 0
            return round((s.get("replies") or 0) / v * 1000, 1) if v else 0.0

        def _entry(s: dict) -> dict:
            txt = (s.get("text") or "").replace("\n", " ")
            return {"author": s.get("author") or "", "views": s.get("views") or 0,
                    "likes": s.get("likes") or 0, "replies": s.get("replies") or 0,
                    "reply_rate": _reply_rate(s), "topic": s.get("topic") or "",
                    "text": (txt[:200] + "…") if len(txt) > 200 else txt}

        by_reach = [_entry(s) for s in
                    sorted(samples, key=lambda s: s.get("views") or 0, reverse=True)[:8]]
        by_replies = [_entry(s) for s in
                      sorted(samples, key=_reply_rate, reverse=True)[:8]
                      if _reply_rate(s) > 0]
        return {
            "sample_count": len(samples),
            "by_reach": by_reach,
            "by_replies": by_replies,
            "text": self._feed_text(len(samples), by_reach, by_replies),
        }

    @staticmethod
    def _feed_text(n: int, by_reach: List[dict], by_replies: List[dict]) -> str:
        if not n:
            return ("No competitor/feed samples captured yet — add trending posts "
                    "from your niche to learn what is working right now.")
        lines = [f"NICHE TREND INTELLIGENCE from {n} competitor/feed posts. Study the "
                 "angle and format, then write something in the same vein but BETTER "
                 "and in the account's own voice (never copy):",
                 "Highest REPLY-PULL (replies per 1000 views — the viral engine):"]
        for i, s in enumerate(by_replies or by_reach, 1):
            who = f"@{s['author']} " if s["author"] else ""
            lines.append(f"  {i}. {s['reply_rate']}‰ ({s['replies']}r/{s['views']}v) {who}— «{s['text']}»")
        lines.append("Highest REACH:")
        for i, s in enumerate(by_reach, 1):
            who = f"@{s['author']} " if s["author"] else ""
            lines.append(f"  {i}. {s['views']}v/{s['likes']}l/{s['replies']}r {who}— «{s['text']}»")
        return "\n".join(lines)

    # -- daily strategy cycle -----------------------------------------------
    def goal_progress(self, account_id: Optional[int] = None, days: int = 30) -> dict:
        """Total views/replies/likes on published posts in a window (goal tracking)."""
        cutoff = _cutoff(days * 24)
        sql = ("SELECT COALESCE(SUM(views),0) v, COALESCE(SUM(replies),0) r, "
               "COALESCE(SUM(likes),0) l FROM tasks "
               "WHERE published_id != '' AND published_id IS NOT NULL AND updated_at >= ?")
        params: list = [cutoff]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return {"views": row["v"] or 0, "replies": row["r"] or 0, "likes": row["l"] or 0}

    def recent_angles(self, days: int = 7, account_id: Optional[int] = None) -> List[dict]:
        """Recently used (archetype, theme) pairs — so the planner avoids repeats."""
        cutoff = _cutoff(days * 24)
        sql = ("SELECT archetype, theme FROM tasks WHERE created_at >= ? "
               "AND (archetype != '' OR theme != '')")
        params: list = [cutoff]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        sql += " ORDER BY created_at DESC LIMIT 60"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{"archetype": r["archetype"] or "", "theme": r["theme"] or ""} for r in rows]

    def add_strategy_run(self, *, account_id: Optional[int] = None, trigger: str = "manual",
                         thesis: str = "", analysis: str = "", plan_json: str = "",
                         tasks_created: int = 0, batch_id: str = "", status: str = "planned",
                         error: str = "") -> dict:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO strategy_runs
                   (created_at, account_id, status, trigger, thesis, analysis,
                    plan_json, tasks_created, batch_id, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_now(), account_id, status, trigger, thesis, analysis,
                 plan_json, int(tasks_created), batch_id, error),
            )
            run_id = cur.lastrowid
        self.log("strategy.run", f"{trigger}: {tasks_created} posts, {status}", account_id)
        return self.get_strategy_run(run_id)

    def update_strategy_run(self, run_id: int, **fields) -> Optional[dict]:
        allowed = {"status", "thesis", "analysis", "plan_json", "tasks_created", "batch_id", "error"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            with self._lock, self._conn:
                self._conn.execute(
                    f"UPDATE strategy_runs SET {sets} WHERE id = ?",
                    (*updates.values(), run_id),
                )
        return self.get_strategy_run(run_id)

    def get_strategy_run(self, run_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM strategy_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_strategy_runs(self, limit: int = 30) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM strategy_runs ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def last_strategy_run(self) -> Optional[dict]:
        runs = self.list_strategy_runs(limit=1)
        return runs[0] if runs else None

    # -- warmup (engagement) actions ----------------------------------------
    def add_warmup_action(self, *, account_id: Optional[int], kind: str = "reply",
                          target_author: str = "", target_text: str = "",
                          target_url: str = "", target_id: str = "",
                          draft: str = "") -> Optional[dict]:
        """Queue an engagement action (reply/like/follow). Deduped per account by
        target so we don't warm the same post twice."""
        target_text = (target_text or "").strip()
        # Dedupe on the most specific identifier we actually have (an empty
        # field must NOT match other empty-field rows).
        ident_col, ident_val = None, None
        if (target_id or "").strip():
            ident_col, ident_val = "target_id", target_id.strip()
        elif (target_url or "").strip():
            ident_col, ident_val = "target_url", target_url.strip()
        elif target_text:
            ident_col, ident_val = "target_text", target_text
        with self._lock, self._conn:
            if ident_col:
                dupe = self._conn.execute(
                    f"SELECT id FROM warmup_actions WHERE kind = ? AND account_id IS ? "
                    f"AND {ident_col} = ?",
                    (kind, account_id, ident_val),
                ).fetchone()
                if dupe:
                    return None
            cur = self._conn.execute(
                """INSERT INTO warmup_actions
                   (account_id, kind, target_author, target_text, target_url,
                    target_id, draft, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (account_id, kind, target_author.strip(), target_text,
                 target_url.strip(), target_id.strip(), draft.strip(), _now(), _now()),
            )
            aid = cur.lastrowid
        return self.get_warmup_action(aid)

    def get_warmup_action(self, action_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM warmup_actions WHERE id = ?", (action_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_warmup_actions(self, status: Optional[str] = None,
                           account_id: Optional[int] = None, limit: int = 100) -> List[dict]:
        sql = "SELECT * FROM warmup_actions"
        clauses, params = [], []
        if status:
            clauses.append("status = ?"); params.append(status)
        if account_id:
            clauses.append("account_id = ?"); params.append(account_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def posts_needing_followup(self, account_id: Optional[int] = None,
                               min_age_minutes: int = 30, since_hours: int = 24,
                               limit: int = 8) -> List[dict]:
        """Own published posts that deserve ONE thoughtful follow-up comment.

        Anti-spam by construction: a post qualifies only if it is at least
        ``min_age_minutes`` old (never reply instantly — that reads as a bot),
        no older than ``since_hours`` (don't resurrect stale threads), and does
        NOT already have a follow-up queued/published against it (one per post).
        Ranked by views so we develop the threads that actually have life.
        """
        newest = (datetime.now(_TZ).replace(tzinfo=None)
                  - timedelta(minutes=min_age_minutes)).isoformat(timespec="seconds")
        oldest = _cutoff(since_hours)
        sql = ("SELECT * FROM tasks WHERE kind = 'post' "
               "AND published_id != '' AND published_id IS NOT NULL "
               "AND updated_at <= ? AND updated_at >= ? "
               "AND published_id NOT IN (SELECT target_id FROM warmup_actions "
               "WHERE kind = 'followup' AND target_id != '') ")
        params: list = [newest, oldest]
        if account_id:
            sql += "AND account_id = ? "; params.append(account_id)
        sql += "ORDER BY views DESC, updated_at DESC LIMIT ?"; params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def own_reply_counts(self) -> dict:
        """{parent_post_published_id: how many of OUR OWN replies we published in it}.

        Keeps the comment stats honest: our own auto-replies must never pad the
        reply/comment numbers — only real people count. Counts BOTH follow-ups
        under our post (parent id in ``target_id``) and replies to commenters
        (parent post id kept in ``target_url``).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT parent, COUNT(*) c FROM ("
                "  SELECT target_id AS parent FROM warmup_actions "
                "    WHERE kind = 'followup' AND status = 'done' "
                "    AND target_id != '' AND target_id IS NOT NULL "
                "  UNION ALL "
                "  SELECT target_url AS parent FROM warmup_actions "
                "    WHERE kind = 'comment_reply' AND status = 'done' "
                "    AND target_url != '' AND target_url IS NOT NULL "
                ") GROUP BY parent"
            ).fetchall()
        return {r["parent"]: r["c"] for r in rows}

    # Back-compat alias (older callers/tests).
    def own_followup_counts(self) -> dict:
        return self.own_reply_counts()

    def set_reply_engagement(self, published_id: str, got_reply: bool) -> None:
        """Mark whether a person replied back to OUR published reply."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE warmup_actions SET got_reply = ? WHERE published_id = ?",
                (1 if got_reply else 0, str(published_id)),
            )

    def done_reply_pids(self, limit: int = 200) -> List[dict]:
        """Our published auto-replies (for engagement refresh): id, kind, pid, parent post."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, account_id, published_id, target_url, target_id "
                "FROM warmup_actions WHERE kind IN ('followup','comment_reply') "
                "AND status = 'done' AND published_id != '' AND published_id IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def reply_stats(self, days: int = 14) -> dict:
        """Rich stats for the auto-reply engine (follow-ups + replies to people)."""
        kinds = ("followup", "comment_reply")
        marks = ",".join("?" for _ in kinds)
        today = _today()
        cutoff = (datetime.now(_TZ).replace(tzinfo=None)
                  - timedelta(days=days - 1)).date().isoformat()
        with self._lock:
            def one(sql, params=()):
                return self._conn.execute(sql, params).fetchone()[0] or 0

            published = one(
                f"SELECT COUNT(*) FROM warmup_actions WHERE kind IN ({marks}) AND status='done'",
                kinds)
            to_people = one(
                "SELECT COUNT(*) FROM warmup_actions WHERE kind='comment_reply' AND status='done'")
            followups = one(
                "SELECT COUNT(*) FROM warmup_actions WHERE kind='followup' AND status='done'")
            pending = one(
                f"SELECT COUNT(*) FROM warmup_actions WHERE kind IN ({marks}) AND status='pending'",
                kinds)
            skipped = one(
                f"SELECT COUNT(*) FROM warmup_actions WHERE kind IN ({marks}) AND status='skipped'",
                kinds)
            dialogs = one(
                f"SELECT COUNT(*) FROM warmup_actions WHERE kind IN ({marks}) AND status='done' "
                "AND got_reply=1", kinds)
            today_cnt = one(
                f"SELECT COUNT(*) FROM warmup_actions WHERE kind IN ({marks}) AND status='done' "
                "AND substr(updated_at,1,10)=?", (*kinds, today))
            people_reached = one(
                "SELECT COUNT(DISTINCT lower(target_author)) FROM warmup_actions "
                "WHERE kind='comment_reply' AND status='done' AND target_author!=''")
            day_rows = self._conn.execute(
                f"SELECT substr(updated_at,1,10) d, "
                f"  SUM(CASE WHEN kind='comment_reply' THEN 1 ELSE 0 END) people, "
                f"  COUNT(*) total "
                f"FROM warmup_actions WHERE kind IN ({marks}) AND status='done' "
                f"AND substr(updated_at,1,10) >= ? GROUP BY d ORDER BY d",
                (*kinds, cutoff)).fetchall()
            acc_rows = self._conn.execute(
                f"SELECT account_id, COUNT(*) published, "
                f"  SUM(CASE WHEN kind='comment_reply' THEN 1 ELSE 0 END) to_people, "
                f"  SUM(CASE WHEN got_reply=1 THEN 1 ELSE 0 END) dialogs "
                f"FROM warmup_actions WHERE kind IN ({marks}) AND status='done' "
                f"GROUP BY account_id", kinds).fetchall()

        names = {a["id"]: (a.get("handle") or a.get("name") or f"acc {a['id']}")
                 for a in self.list_accounts()}
        by_day_map = {r["d"]: {"people": r["people"] or 0, "total": r["total"] or 0}
                      for r in day_rows}
        by_day = []
        for i in range(days):
            d = (datetime.now(_TZ).replace(tzinfo=None)
                 - timedelta(days=days - 1 - i)).date().isoformat()
            m = by_day_map.get(d, {"people": 0, "total": 0})
            by_day.append({"date": d, "count": m["total"], "people": m["people"]})
        by_account = [{
            "account": names.get(r["account_id"], f"acc {r['account_id']}"),
            "published": r["published"] or 0, "to_people": r["to_people"] or 0,
            "dialogs": r["dialogs"] or 0,
        } for r in acc_rows]
        by_account.sort(key=lambda x: x["published"], reverse=True)
        return {
            "published": published, "to_people": to_people, "followups": followups,
            "pending": pending, "skipped": skipped, "dialogs": dialogs,
            "no_reply_back": max(0, published - dialogs),
            "reply_back_rate": round(dialogs * 100 / published, 1) if published else 0.0,
            "people_reached": people_reached, "today": today_cnt,
            "by_day": by_day, "by_account": by_account,
        }

    def answered_comment_ids(self) -> set:
        """Comment ids we've already drafted/queued/published a reply for (dedupe)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT target_id FROM warmup_actions WHERE kind = 'comment_reply' "
                "AND target_id != '' AND target_id IS NOT NULL"
            ).fetchall()
        return {r["target_id"] for r in rows}

    def pending_followups(self, limit: int = 50,
                          kinds: tuple = ("followup", "comment_reply")) -> List[dict]:
        """Approved-but-unpublished auto-replies, oldest first (gentle auto-publish).

        Covers both follow-ups under our own posts and replies to commenters.
        """
        marks = ",".join("?" for _ in kinds)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM warmup_actions WHERE kind IN ({marks}) AND status = 'pending' "
                "AND target_id != '' AND target_id IS NOT NULL "
                "ORDER BY created_at ASC LIMIT ?", (*kinds, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_warmup_draft(self, action_id: int, draft: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE warmup_actions SET draft = ?, updated_at = ? WHERE id = ?",
                (draft, _now(), action_id),
            )

    def set_warmup_status(self, action_id: int, status: str,
                          published_id: str = "") -> Optional[dict]:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE warmup_actions SET status = ?, published_id = ?, updated_at = ? WHERE id = ?",
                (status, published_id, _now(), action_id),
            )
        self.log("warmup.action", f"{status} #{action_id}")
        return self.get_warmup_action(action_id)

    def warmup_stats(self, days: int = 1) -> dict:
        cutoff = _cutoff(days * 24)
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, status, COUNT(*) c FROM warmup_actions "
                "WHERE created_at >= ? GROUP BY kind, status", (cutoff,),
            ).fetchall()
        out = {"reply": {}, "like": {}, "follow": {}, "pending": 0, "done": 0}
        for r in rows:
            out.setdefault(r["kind"], {})[r["status"]] = r["c"]
            if r["status"] in out:
                out[r["status"]] += r["c"]
        return out

    # -- drops (campaigns with a goal + evaluation) -------------------------
    def add_drop(self, *, label: str = "", goal_views: int = 0, goal_comments: int = 0,
                 task_ids: Optional[List[int]] = None) -> dict:
        import json as _json
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO drops (created_at, label, goal_views, goal_comments,
                   task_ids, status) VALUES (?, ?, ?, ?, ?, 'running')""",
                (_now(), label.strip(), int(goal_views), int(goal_comments),
                 _json.dumps(task_ids or [])),
            )
            did = cur.lastrowid
        self.log("drop.create", f"{label}: goal {goal_views}v/{goal_comments}c")
        return self.get_drop(did)

    def get_drop(self, drop_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone()
        return dict(row) if row else None

    def list_drops(self, limit: int = 40) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM drops ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update_drop(self, drop_id: int, **fields) -> Optional[dict]:
        allowed = {"label", "goal_views", "goal_comments", "task_ids", "status",
                   "views", "comments", "likes", "verdict", "measured_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            with self._lock, self._conn:
                self._conn.execute(f"UPDATE drops SET {sets} WHERE id = ?",
                                   (*updates.values(), drop_id))
        return self.get_drop(drop_id)

    def measure_drop(self, drop_id: int) -> Optional[dict]:
        """Recompute a drop's totals from its tasks' current metrics."""
        import json as _json
        drop = self.get_drop(drop_id)
        if not drop:
            return None
        ids = _json.loads(drop.get("task_ids") or "[]")
        v = c = l = 0
        for i in ids:
            t = self.get_task(i)
            if t:
                v += t.get("views") or 0
                c += t.get("replies") or 0
                l += t.get("likes") or 0
        status = "measured" if drop["status"] == "running" else drop["status"]
        return self.update_drop(drop_id, views=v, comments=c, likes=l,
                                measured_at=_now(), status=status)

    def drop_detail(self, drop_id: int) -> Optional[dict]:
        """Full drop stats: per-post, per-account, and goal completion."""
        import json as _json
        drop = self.measure_drop(drop_id)
        if not drop:
            return None
        ids = _json.loads(drop.get("task_ids") or "[]")
        hands = {a["id"]: a["handle"] for a in self.list_accounts()}
        posts, per = [], {}
        for i in ids:
            t = self.get_task(i)
            if not t:
                continue
            v = t.get("views") or 0
            rp = t.get("replies") or 0
            txt = re.sub(r"^\[published [^\]]*\]\s*", "", (t.get("result") or t.get("title") or "")).replace("\n", " ")
            posts.append({"views": v, "replies": rp, "likes": t.get("likes") or 0,
                          "reply_rate": round(rp / v * 1000, 1) if v else 0.0,
                          "account": hands.get(t.get("account_id"), ""), "published": bool(t.get("published_id")),
                          "when": (t.get("scheduled_for") or "")[11:16], "text": txt[:120]})
            a = per.setdefault(hands.get(t.get("account_id"), "?"), {"views": 0, "comments": 0, "posts": 0})
            a["views"] += v; a["comments"] += t.get("replies") or 0; a["posts"] += 1
        posts.sort(key=lambda p: p["views"], reverse=True)
        gv, gc = drop["goal_views"], drop["goal_comments"]
        pv = round(drop["views"] / gv * 100) if gv else None
        pc = round(drop["comments"] / gc * 100) if gc else None
        avg = round(drop["views"] / len(posts), 1) if posts else 0
        rr = round(drop["comments"] / drop["views"] * 1000, 1) if drop["views"] else 0.0
        return {
            **drop,
            "pct_views": pv, "pct_comments": pc,
            "met": (gv and drop["views"] >= gv) and (gc and drop["comments"] >= gc),
            "avg_views": avg, "reply_rate": rr,
            "best": posts[0] if posts else None,
            "per_account": [{"account": k, **v} for k, v in
                            sorted(per.items(), key=lambda kv: kv[1]["views"], reverse=True)],
            "posts": posts,
        }

    # -- settings (key/value meta) ------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
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

    def stats_full(self, days: int = 30, goal_views: int = 20000,
                   goal_comments: int = 300) -> dict:
        """Everything the deep-stats department needs in one call: per-account
        metrics, overall totals, top posts, a daily views trend, and goal
        progress (main + today's drop)."""
        import json as _json

        def _clean(txt: str) -> str:
            return re.sub(r"^\[published [^\]]*\]\s*", "", (txt or "")).replace("\n", " ")

        pub = self.published_tasks(limit=500)
        pub = [p for p in pub if (p.get("views") or 0) >= 0]
        by_acc: Dict[int, dict] = {}
        daily: Dict[str, int] = {}
        for t in pub:
            aid = t.get("account_id")
            d = by_acc.setdefault(aid, {"views": 0, "likes": 0, "replies": 0, "posts": []})
            d["views"] += t.get("views") or 0
            d["likes"] += t.get("likes") or 0
            d["replies"] += t.get("replies") or 0
            d["posts"].append(t)
            day = (t.get("updated_at") or "")[:10]
            if day:
                daily[day] = daily.get(day, 0) + (t.get("views") or 0)

        accounts, tot = [], {"views": 0, "likes": 0, "replies": 0, "published": 0}
        for a in self.list_accounts():
            d = by_acc.get(a["id"], {"views": 0, "likes": 0, "replies": 0, "posts": []})
            v, r = d["views"], d["replies"]
            rr = round(r / v * 1000, 1) if v else 0.0
            spark = [p.get("views") or 0 for p in
                     sorted(d["posts"], key=lambda x: x.get("updated_at") or "")[-12:]]
            best = max(d["posts"], key=lambda x: x.get("views") or 0, default=None)
            accounts.append({
                "id": a["id"], "name": a["name"], "handle": a["handle"],
                "views": v, "likes": d["likes"], "replies": r, "reply_rate": rr,
                "published": len(d["posts"]),
                "state": "warm" if v >= 500 else ("warming" if v >= 50 else "cold"),
                "spark": spark,
                "best": _clean(best.get("result") or best.get("title"))[:60] if best else "",
            })
            tot["views"] += v; tot["likes"] += d["likes"]
            tot["replies"] += r; tot["published"] += len(d["posts"])
        accounts.sort(key=lambda x: x["views"], reverse=True)

        today = datetime.now(_TZ).date()
        trend = [{"date": (today - timedelta(days=i)).isoformat(),
                  "views": daily.get((today - timedelta(days=i)).isoformat(), 0)}
                 for i in range(13, -1, -1)]

        top = [{"views": t.get("views") or 0, "likes": t.get("likes") or 0,
                "replies": t.get("replies") or 0, "account_id": t.get("account_id"),
                "reply_rate": round((t.get("replies") or 0) / (t.get("views") or 1) * 1000, 1),
                "text": _clean(t.get("result") or t.get("title"))[:90]}
               for t in sorted(pub, key=lambda x: x.get("views") or 0, reverse=True)[:8]]

        drop_ids = _json.loads(self.get_setting("drop_task_ids") or "[]")
        dv = dr = 0
        for i in drop_ids:
            tk = self.get_task(i)
            if tk:
                dv += tk.get("views") or 0
                dr += tk.get("replies") or 0

        return {
            "overall": {**tot, "accounts": len(accounts),
                        "avg_reply_rate": round(tot["replies"] / tot["views"] * 1000, 1)
                        if tot["views"] else 0.0},
            "accounts": accounts, "top": top, "trend": trend,
            "goal": {"views": tot["views"], "comments": tot["replies"], "likes": tot["likes"],
                     "target_views": goal_views, "target_comments": goal_comments},
            "drop": {"views": dv, "comments": dr, "posts": len(drop_ids),
                     "target_views": int(self.get_setting("drop_goal_views") or 0),
                     "target_comments": int(self.get_setting("drop_goal_comments") or 0)},
        }

    def legends(self, min_views: int = 1000, limit: int = 12) -> List[dict]:
        """Legendary posts — those that cleared the quality bar (views >= min).
        Each carries its metrics and a stored strategist note (why it's a legend)."""
        def _clean(txt: str) -> str:
            return re.sub(r"^\[published [^\]]*\]\s*", "", (txt or "")).replace("\n", " ")

        hands = {a["id"]: a["handle"] for a in self.list_accounts()}
        out = []
        for t in sorted(self.published_tasks(limit=500), key=lambda x: x.get("views") or 0, reverse=True):
            v = t.get("views") or 0
            if v < min_views:
                break
            pid = t.get("published_id") or ""
            out.append({
                "published_id": pid, "task_id": t["id"], "views": v,
                "replies": t.get("replies") or 0, "likes": t.get("likes") or 0,
                "reply_rate": round((t.get("replies") or 0) / v * 1000, 1) if v else 0.0,
                "account": hands.get(t.get("account_id"), ""),
                "when": (t.get("updated_at") or "")[:10],
                "text": _clean(t.get("result") or t.get("title")),
                "note": self.get_setting(f"legend_note:{pid}", "") if pid else "",
            })
            if len(out) >= limit:
                break
        return out

    def daily_reports(self, days: int = 14) -> List[dict]:
        """Per-day close-out: posts published that day and their metrics, per
        account, best post, drops created that day, and warm-up actions.

        Each day ends at 23:59 — today is shown as in-progress."""
        import json as _json

        def _clean(txt: str) -> str:
            return re.sub(r"^\[published [^\]]*\]\s*", "", (txt or "")).replace("\n", " ")

        pub = self.published_tasks(limit=1000)
        hands = {a["id"]: a["handle"] for a in self.list_accounts()}
        by_day: Dict[str, dict] = {}
        for t in pub:
            day = (t.get("updated_at") or "")[:10]
            if not day:
                continue
            d = by_day.setdefault(day, {"views": 0, "comments": 0, "likes": 0, "posts": 0, "per": {}, "best": None})
            v = t.get("views") or 0
            d["views"] += v; d["comments"] += t.get("replies") or 0
            d["likes"] += t.get("likes") or 0; d["posts"] += 1
            h = hands.get(t.get("account_id"), "?")
            pa = d["per"].setdefault(h, {"views": 0, "comments": 0, "posts": 0})
            pa["views"] += v; pa["comments"] += t.get("replies") or 0; pa["posts"] += 1
            if not d["best"] or v > d["best"]["views"]:
                d["best"] = {"views": v, "replies": t.get("replies") or 0, "account": h,
                             "text": _clean(t.get("result") or t.get("title"))[:80]}

        drops_by_day: Dict[str, list] = {}
        for dr in self.list_drops():
            day = (dr.get("created_at") or "")[:10]
            met = bool(dr["goal_views"] and dr["views"] >= dr["goal_views"]
                       and dr["goal_comments"] and dr["comments"] >= dr["goal_comments"])
            drops_by_day.setdefault(day, []).append({
                "label": dr["label"], "goal_views": dr["goal_views"], "goal_comments": dr["goal_comments"],
                "views": dr["views"], "comments": dr["comments"], "met": met})

        warm_by_day: Dict[str, int] = {}
        for w in self.list_warmup_actions(limit=1000):
            day = (w.get("created_at") or "")[:10]
            warm_by_day[day] = warm_by_day.get(day, 0) + 1

        today = datetime.now(_TZ).date().isoformat()
        all_days = sorted(set(list(by_day) + list(drops_by_day)), reverse=True)[:days]
        out = []
        for day in all_days:
            d = by_day.get(day, {"views": 0, "comments": 0, "likes": 0, "posts": 0, "per": {}, "best": None})
            rr = round(d["comments"] / d["views"] * 1000, 1) if d["views"] else 0.0
            out.append({
                "date": day, "is_today": day == today,
                "views": d["views"], "comments": d["comments"], "likes": d["likes"],
                "posts": d["posts"], "reply_rate": rr, "best": d["best"],
                "per_account": [{"account": k, **v} for k, v in
                                sorted(d["per"].items(), key=lambda kv: kv[1]["views"], reverse=True)],
                "drops": drops_by_day.get(day, []), "warmup": warm_by_day.get(day, 0),
            })
        return out

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
