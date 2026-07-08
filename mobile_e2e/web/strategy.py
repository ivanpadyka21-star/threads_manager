"""The daily strategy cycle — the "Swiss watch" that ties everything together.

One run does the whole loop, in order, once:

  1. RESEARCH  — gather the account's own performance insights (reply-drivers,
     patterns), the captured niche/feed trends, the proven viral archetypes,
     and the *recently used angles* (so we never repeat the same thing).
  2. PLAN      — one LLM call turns that context into a structured JSON plan: a
     thesis, a short analysis (the statistics/reasoning), and a VARIED set of
     post briefs, each tagged with an archetype / audience / theme.
  3. QUEUE     — create the posts as pending, archetype-tagged, time-spaced
     tasks for human approval (never auto-publishes).
  4. WRITE     — the writer drafts each post, with performance insights injected.
  5. REPORT    — the whole run (thesis + analysis + plan) is stored so the owner
     can read exactly what the strategist concluded and queued.

Flexibility & anti-repetition are first-class: recent archetypes/themes are fed
in as an explicit avoid-list, and the plan is de-duplicated within a run.

The LLM is called through a small ``agent_factory`` so tests inject a fake.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Callable, List, Optional

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web import archetypes
from mobile_e2e.web.ai_prompt import build_ai_prompt
from mobile_e2e.web.store import _TZ

LOG = get_logger(__name__)

PLANNER_SYSTEM = (
    "You are the daily CONTENT STRATEGIST for an SMM creator's Threads account. "
    "The growth engine on Threads is the REPLY CHAIN: posts that make strangers "
    "answer or argue in the comments get amplified — comments matter more than "
    "likes, and views follow comments. Your job: read the real performance data, "
    "the niche/feed trends, and the proven formats, then design ONE day's worth "
    "of posts that beat the competition and pull replies.\n"
    "EVERYTHING serves a concrete GOAL (given in the context): a target number of "
    "views and comments. Never plan aimlessly 'into the void' — in the analysis, "
    "state how today's set moves the numbers toward that goal, and pick formats "
    "for maximum reply-pull and reach, not vanity.\n"
    "HARD RULES:\n"
    "- Stay strictly in the account's niche and persona voice (first person).\n"
    "- Be FLEXIBLE and VARIED: mix archetypes and audiences (some sharp one-line "
    "questions to men, some dilemmas to women, some participatory, some intimate "
    "confessions). NEVER repeat the recently-used angles you are given, and do "
    "not make two posts the same archetype+theme.\n"
    "- Playful, suggestive, teasing content is fine; do NOT write sexually "
    "explicit/pornographic text and do not impersonate a different real person.\n"
    "- You plan briefs only; a human approves before anything is published.\n"
    "OUTPUT: return STRICT JSON only (no prose, no code fences) with this shape:\n"
    '{"thesis": "one short paragraph: the angle for today and why",'
    ' "analysis": "2-4 sentences of the data/trend reasoning (the statistics)",'
    ' "posts": [{"archetype": "<one of the format ids>", "audience": "men|women|both",'
    ' "theme": "short topic label", "language": "<language>", "brief": "a clear,'
    ' specific brief the writer will turn into the actual post"}]}'
)


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of an LLM reply, tolerating code fences/prose."""
    if not text:
        raise ValueError("empty plan")
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        # drop a leading "json" language tag if present
        if t[:4].lower() == "json":
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in plan")
    return json.loads(t[start:end + 1])


class StrategyCycle:
    """Runs one end-to-end daily strategy cycle against the store."""

    def __init__(self, store, account_id: Optional[int] = None,
                 agent_factory: Optional[Callable[[str], object]] = None):
        self._store = store
        self._account_id = account_id
        self._agent_factory = agent_factory or (lambda sp: AIAgent(sp))

    # -- research -----------------------------------------------------------
    def _goal_block(self) -> str:
        gv = int(self._store.get_setting(S_GOAL_VIEWS, DEFAULTS[S_GOAL_VIEWS]) or 0)
        gc = int(self._store.get_setting(S_GOAL_COMMENTS, DEFAULTS[S_GOAL_COMMENTS]) or 0)
        prog = self._store.goal_progress(account_id=self._account_id, days=30)
        return (
            f"=== GOAL (non-negotiable) ===\n"
            f"Target: {gv} views and {gc} comments. "
            f"Current 30-day progress: {prog['views']} views, {prog['replies']} comments, "
            f"{prog['likes']} likes. Every post today MUST be engineered to close this "
            f"gap — maximise replies (the reach engine) and reach. If a post idea does "
            f"not serve this goal, do not include it."
        )

    def build_context(self, count: int, language: str) -> str:
        insights = self._store.content_insights(account_id=self._account_id)["text"]
        feed = self._store.feed_insights()["text"]
        formats = archetypes.viral_formats_text("ru")
        recent = self._store.recent_angles(days=7, account_id=self._account_id)
        account = self._store.get_account(self._account_id) if self._account_id else None
        persona = (account or {}).get("persona", "") if account else ""

        avoid = "; ".join(
            f"{a['archetype'] or '?'}/{a['theme']}".strip("/")
            for a in recent if (a["archetype"] or a["theme"])
        ) or "(nothing yet)"
        format_ids = ", ".join(a["id"] for a in archetypes.list_archetypes())

        return "\n\n".join([
            self._goal_block(),
            f"PERSONA (write in this first-person voice): {persona or '(not set)'}",
            f"=== YOUR OWN PERFORMANCE ===\n{insights}",
            f"=== NICHE / FEED TRENDS ===\n{feed}",
            f"=== PROVEN FORMATS (use these archetype ids) ===\n{formats}",
            f"Valid archetype ids: {format_ids}",
            f"RECENTLY USED — AVOID REPEATING THESE ANGLES: {avoid}",
            f"TASK: design {count} varied posts for today in {language}, all serving the "
            f"GOAL above. Return STRICT JSON only.",
        ])

    # -- plan ---------------------------------------------------------------
    def plan(self, count: int, language: str) -> dict:
        context = self.build_context(count, language)
        agent = self._agent_factory(PLANNER_SYSTEM)
        raw = agent.generate_response(context)
        data = _extract_json(raw)
        posts = data.get("posts") or []
        # De-duplicate archetype+theme within the run to force variety.
        seen, unique = set(), []
        for p in posts:
            if not isinstance(p, dict) or not str(p.get("brief", "")).strip():
                continue
            key = (str(p.get("archetype", "")).lower(), str(p.get("theme", "")).lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        data["posts"] = unique[:count]
        return data

    # -- full cycle ---------------------------------------------------------
    def run(self, *, count: int = 6, language: str = "Ukrainian",
            interval_minutes: int = 90, trigger: str = "manual",
            draft: bool = True) -> dict:
        """Execute the full cycle and persist a strategy_run report."""
        try:
            plan = self.plan(count, language)
        except Exception as exc:  # noqa: BLE001 - record the failure, don't crash
            LOG.warning("strategy plan failed: %s", exc)
            return self._store.add_strategy_run(
                account_id=self._account_id, trigger=trigger, status="failed",
                error=str(exc)[:300])

        posts = plan.get("posts") or []
        if not posts:
            return self._store.add_strategy_run(
                account_id=self._account_id, trigger=trigger, status="failed",
                thesis=plan.get("thesis", ""), analysis=plan.get("analysis", ""),
                error="planner returned no posts")

        batch = self._store.add_strategy_batch(
            self._account_id, posts, language=language, interval_minutes=interval_minutes)
        tasks = batch["tasks"]

        drafted = 0
        if draft:
            drafted = self._draft(tasks)

        run = self._store.add_strategy_run(
            account_id=self._account_id, trigger=trigger,
            status="drafted" if drafted else "planned",
            thesis=plan.get("thesis", ""), analysis=plan.get("analysis", ""),
            plan_json=json.dumps(plan, ensure_ascii=False),
            tasks_created=len(tasks), batch_id=batch["batch_id"])
        return run

    def _draft(self, tasks: List[dict]) -> int:
        insights = self._store.content_insights(account_id=self._account_id)["text"]
        account = self._store.get_account(self._account_id) if self._account_id else None
        done = 0
        for task in tasks:
            sys_p, usr_p = build_ai_prompt(task, account, insights=insights)
            try:
                draft = self._agent_factory(sys_p).generate_response(usr_p)
                self._store.set_task_result(task["id"], draft)
                self._store.record_event("ai.generate", f"strategy draft #{task['id']}",
                                         task.get("account_id"), level="ok")
                done += 1
            except Exception as exc:  # noqa: BLE001 - degrade; leave draft empty
                self._store.record_event("ai.error", str(exc)[:200],
                                         task.get("account_id"), level="info")
        return done


# -- settings keys (persisted in the store's meta table) --------------------
S_ENABLED = "strategy_enabled"
S_HOUR = "strategy_hour"
S_COUNT = "strategy_count"
S_LANGUAGE = "strategy_language"
S_ACCOUNT = "strategy_account"
S_INTERVAL = "strategy_interval"
S_LAST_DATE = "strategy_last_date"
S_GOAL_VIEWS = "strategy_goal_views"
S_GOAL_COMMENTS = "strategy_goal_comments"

DEFAULTS = {S_ENABLED: "0", S_HOUR: "9", S_COUNT: "6",
            S_LANGUAGE: "Ukrainian", S_ACCOUNT: "", S_INTERVAL: "90",
            S_GOAL_VIEWS: "20000", S_GOAL_COMMENTS: "300"}


def get_strategy_settings(store) -> dict:
    return {
        "enabled": store.get_setting(S_ENABLED, DEFAULTS[S_ENABLED]) == "1",
        "hour": int(store.get_setting(S_HOUR, DEFAULTS[S_HOUR]) or 9),
        "count": int(store.get_setting(S_COUNT, DEFAULTS[S_COUNT]) or 6),
        "language": store.get_setting(S_LANGUAGE, DEFAULTS[S_LANGUAGE]),
        "account_id": (int(store.get_setting(S_ACCOUNT) or 0) or None),
        "interval_minutes": int(store.get_setting(S_INTERVAL, DEFAULTS[S_INTERVAL]) or 90),
        "goal_views": int(store.get_setting(S_GOAL_VIEWS, DEFAULTS[S_GOAL_VIEWS]) or 0),
        "goal_comments": int(store.get_setting(S_GOAL_COMMENTS, DEFAULTS[S_GOAL_COMMENTS]) or 0),
        "last_date": store.get_setting(S_LAST_DATE, ""),
    }


class StrategyScheduler:
    """Fires the strategy cycle once per day at a configured local hour.

    Checks every ``interval_seconds``; runs at most once per calendar day
    (claimed via the ``strategy_last_date`` setting before running, so a slow
    cycle or a reloader child can't double-fire). Cheap on API calls by design.
    """

    def __init__(self, store, interval_seconds: int = 300,
                 cycle_factory: Optional[Callable[[Optional[int]], StrategyCycle]] = None):
        self._store = store
        self._interval = interval_seconds
        self._cycle_factory = cycle_factory or (
            lambda acc: StrategyCycle(store, account_id=acc))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        LOG.info("Strategy scheduler started (checks every %ss)", self._interval)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                LOG.warning("strategy scheduler tick error: %s", exc)
            if self._stop.wait(self._interval):
                break

    def _target_accounts(self, cfg: dict) -> List[Optional[int]]:
        """Which accounts today's cycle runs for.

        A specific configured account wins; otherwise every active account (so
        the whole roster grows daily). Falls back to a single global run when
        there are no accounts yet.
        """
        if cfg["account_id"]:
            return [cfg["account_id"]]
        active = [a["id"] for a in self._store.list_accounts()
                  if a.get("status", "active") == "active"]
        return active or [None]

    def tick(self) -> Optional[list]:
        """Run today's cycle if it's enabled, due, and hasn't run yet today.

        Runs one cycle per target account (see :meth:`_target_accounts`).
        """
        cfg = get_strategy_settings(self._store)
        if not cfg["enabled"]:
            return None
        now = datetime.now(_TZ)
        today = now.date().isoformat()
        if now.hour < cfg["hour"] or cfg["last_date"] == today:
            return None
        # Claim the day up-front so a concurrent/duplicate scheduler won't re-run.
        self._store.set_setting(S_LAST_DATE, today)
        targets = self._target_accounts(cfg)
        LOG.info("Running daily strategy cycle for %s across %d account(s)", today, len(targets))
        runs = []
        for acc in targets:
            try:
                cycle = self._cycle_factory(acc)
                runs.append(cycle.run(count=cfg["count"], language=cfg["language"],
                                      interval_minutes=cfg["interval_minutes"], trigger="daily"))
            except Exception as exc:  # noqa: BLE001 - one account's failure mustn't stop the rest
                LOG.warning("daily cycle failed for account %s: %s", acc, exc)
        return runs
