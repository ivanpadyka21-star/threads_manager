"""Background scheduler that auto-publishes due scheduled posts.

Once a batch of posts is approved, its tasks are ``scheduled`` with a
``scheduled_for`` time. This scheduler wakes periodically, publishes any task
whose time has arrived (respecting the account's daily limit), and records the
outcome. It is intentionally simple and single-process — for a multi-worker
deployment move this to Celery beat / APScheduler with a shared store.
"""

from __future__ import annotations

import threading

from mobile_e2e.web import threads_client
from mobile_e2e.web.publish import publish_task
from mobile_e2e.utils.logger import get_logger

LOG = get_logger(__name__)


class PostScheduler:
    """Periodically publishes due, approved-and-scheduled posts."""

    def __init__(self, store, interval_seconds: int = 30) -> None:
        self._store = store
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        LOG.info("Post scheduler started (every %ss)", self._interval)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Run one tick immediately, then every interval. Also refresh live post
        # metrics roughly every 10 minutes so views/comments climb on their own.
        # Two-tier metric refresh so the dashboard is near-real-time without
        # hammering the API: ACTIVE posts (last 24h) every ~2.5 min; a full
        # backfill of all posts every ~20 min.
        recent_every = max(1, int(150 / max(1, self._interval)))
        metrics_every = max(1, int(1200 / max(1, self._interval)))
        i = 0
        while True:
            # Auto-reply cadence depends on intensity: 'max' mode fires every
            # ~12 min and sends more per pass; 'normal' every ~30 min, gentle.
            aggressive = str(self._store.get_setting("fu_intensity", "normal")) == "max"
            followups_every = max(1, int((720 if aggressive else 1800) / max(1, self._interval)))
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                LOG.warning("scheduler tick error: %s", exc)
            # Liveness pulse every tick so the dashboard can show 'conveyor alive'.
            try:
                self._store.mark_heartbeat("scheduler_heartbeat")
            except Exception:  # noqa: BLE001
                pass
            if i % recent_every == 0:
                try:
                    from mobile_e2e.web.strategy import refresh_metrics
                    # Full sweep on the slow tick, active-only on the fast tick.
                    if i % metrics_every == 0:
                        refresh_metrics(self._store, limit=200)
                    else:
                        refresh_metrics(self._store, limit=60, recent_hours=24)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("metrics refresh error: %s", exc)
            if i % followups_every == 0:
                try:
                    if str(self._store.get_setting("followups_auto", "1")) not in ("0", "false", ""):
                        from mobile_e2e.web.warmup import (draft_followups,
                                                           draft_comment_replies,
                                                           autopublish_followups)
                        r = draft_followups(self._store, per_run=8 if aggressive else 6)
                        if r.get("drafted"):
                            LOG.info("auto-drafted %s follow-ups", r["drafted"])
                        # Read real comments on our posts and draft hooky replies —
                        # in max mode we scan more posts and answer more per post.
                        cr = draft_comment_replies(
                            self._store,
                            max_posts=12 if aggressive else 6,
                            per_post=8 if aggressive else 5)
                        if cr.get("drafted"):
                            LOG.info("auto-drafted %s replies to people", cr["drafted"])
                        # Owner opted in to auto-publish: send them, spread across accounts.
                        if str(self._store.get_setting("followups_autopublish", "0")) not in ("0", "false", ""):
                            p = autopublish_followups(
                                self._store,
                                max_per_pass=8 if aggressive else 3,
                                per_account=2 if aggressive else 1)
                            if p.get("published"):
                                LOG.info("auto-published %s replies", p["published"])
                        # Learn who replied back to our replies (engagement stats).
                        from mobile_e2e.web.warmup import refresh_reply_engagement
                        refresh_reply_engagement(self._store)
                    # Stamp the last auto-reply pass (even if nothing was drafted).
                    self._store.mark_heartbeat("followups_last_run")
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("followup auto error: %s", exc)
                # (No auto-evolve — the strategist now runs deliberately once a
                # day via the "Анализ дня" pass, to control cost + keep one voice.)
                # Refresh account KPI snapshots at most every ~3 hours.
                try:
                    age = self._store.setting_age_seconds("account_insights_last")
                    if age is None or age > 3 * 3600:
                        from mobile_e2e.web.kpi import refresh_account_insights
                        k = refresh_account_insights(self._store)
                        if k.get("accounts"):
                            LOG.info("refreshed KPI for %s accounts", k["accounts"])
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("kpi refresh error: %s", exc)
            i += 1
            if self._stop.wait(self._interval):
                break

    def tick(self) -> int:
        """Publish all due tasks that are within their account's daily limit.

        Returns the number of tasks published this tick.
        """
        published = 0
        for task in self._store.due_scheduled_tasks():
            account_id = task.get("account_id")
            if account_id is not None:
                account = self._store.get_account(account_id)
                if account and account["used_today"] >= account["daily_limit"]:
                    # Over the daily limit — leave scheduled, try again later.
                    continue
            # Atomically claim it so a duplicate scheduler can't publish it too.
            if not self._store.claim_scheduled_task(task["id"]):
                continue
            try:
                pid = publish_task(self._store, task["id"])
                LOG.info("Scheduled post #%s published: %s", task["id"], pid)
                published += 1
            except threads_client.ThreadsNotConfigured as exc:
                # Not set up yet: flag so it doesn't retry forever.
                self._store.record_event(
                    "run.error", f"threads not configured: {exc}"[:200],
                    account_id, level="error",
                )
                self._store.set_task_status(task["id"], "failed")
            except Exception as exc:  # noqa: BLE001 - publish_task already flagged
                LOG.warning("Scheduled post #%s failed: %s", task["id"], exc)
        return published
