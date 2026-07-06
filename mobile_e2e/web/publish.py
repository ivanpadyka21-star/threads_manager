"""Shared publishing logic used by both the HTTP route and the scheduler.

Publishing a task means sending its content (a reviewed draft, or its payload)
to Threads via the official API, then recording the outcome in the store so it
flows into the analytics/effectiveness metrics.
"""

from __future__ import annotations

from mobile_e2e.web import threads_client

# Threads rejects posts longer than this; we truncate as a last-resort guard.
THREADS_MAX_CHARS = 500


def _clamp(text: str, limit: int = THREADS_MAX_CHARS) -> str:
    """Trim text to ``limit`` chars, preferring a word boundary + ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:  # keep it readable if there's a nearby space
        cut = cut[:space]
    return cut.rstrip() + "…"


def publish_task(db, task_id: int) -> str:
    """Publish a task's content to Threads and record the outcome.

    Returns:
        The published container id.

    Raises:
        KeyError: If the task does not exist.
        ValueError: If the task has no content to publish.
        ThreadsNotConfigured: If Threads is not set up (no side effects — the
            caller decides how to handle it).
        Exception: Any publish failure — the task is flagged ``failed`` and a
            ``run.error`` event recorded before re-raising.
    """
    task = db.get_task(task_id)
    if task is None:
        raise KeyError(task_id)

    account_id = task.get("account_id")
    account = db.get_account(account_id) if account_id else None
    creds = (account or {}).get("credentials_file") or threads_client.DEFAULT_CREDENTIALS_FILE
    text = (task.get("result") or task.get("payload") or task.get("title") or "").strip()
    if not text:
        raise ValueError("task has no content to publish")
    # Safety net: never let a too-long draft fail the whole publish.
    text = _clamp(text)

    # ThreadsNotConfigured surfaces to the caller untouched (no flagging).
    try:
        published_id = threads_client.publish_text(text, creds)
    except threads_client.ThreadsNotConfigured:
        raise
    except Exception as exc:  # noqa: BLE001 - flag a real publish failure
        db.record_event("run.error", f"threads publish: {exc}"[:200], account_id, level="error")
        db.set_task_status(task_id, "failed")
        raise

    db.record_event("run.ok", f"threads published {published_id}", account_id, level="ok")
    db.set_task_result(task_id, f"[published {published_id}] {text}")
    db.set_task_status(task_id, "done")
    return published_id
