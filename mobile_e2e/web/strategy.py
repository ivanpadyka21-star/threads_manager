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

from mobile_e2e.ai.brain import make_agent
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web import archetypes
from mobile_e2e.web.ai_prompt import build_ai_prompt
from mobile_e2e.web.store import _TZ

LOG = get_logger(__name__)

PLANNER_SYSTEM = (
    "You are the CONTENT STRATEGIST for an adult SMM creator on Threads — sharp, "
    "flexible, opinionated and 100% results-driven. You have skin in the game: "
    "give your HONEST opinion and a FAIR verdict, praise what worked and name our "
    "own failures bluntly ('вот тут проеб') so we remember and fix them.\n"
    "THE REAL ENGINE IS EMOTION, NOT COMMENTS. Views and comments are just the "
    "shadow of a feeling. A post wins when it makes a stranger FEEL something — "
    "loneliness, longing, desire, jealousy, being seen, wanting to be wanted — and "
    "act on it. Our legendary post proved it: a brand-new test account went viral "
    "purely on emotion (a vulnerable confession about missing closeness), and it "
    "pulled mostly MEN who then wanted to connect. Chase that feeling first.\n"
    "PRIMARY AUDIENCE = MEN. Understand male psychology: men engage a woman who "
    "reads as real, warm, available and a little vulnerable — who signals she "
    "misses attention, closeness, sex — and who gives them a low-stakes way to "
    "respond and feel wanted. Address questions to both sexes, but the emotional "
    "hook must pull men to answer AND want more. Touch on missing sex / touch / "
    "intimacy honestly and tastefully (suggestive, human, real) — never vulgar or "
    "explicit, never cringe.\n"
    "WRITE AS A REAL WOMAN, not a brand: someone who genuinely lacks male "
    "attention, closeness and sex — simple, direct, unashamed, human. Less is more: "
    "short, alive language, one real feeling per post. No clichés, no ad-speak, no "
    "'link in bio'.\n"
    "USE THE DATA HONESTLY: study our own posts (what pulled emotion/replies and "
    "what flopped) AND the competitor/feed examples the owner collected (what "
    "emotion they hit, why it worked, where we lag) — then decide where we move "
    "next. Be flexible and varied: mix archetypes and audiences, NEVER repeat the "
    "recently-used angles, no two posts the same archetype+theme.\n"
    "THE BAR: every single post should aim for 1000+ views and 20+ comments. Plan "
    "for quality that clears that bar, not filler. Also serve the concrete GOAL in "
    "the context, and say in the analysis how today moves the numbers.\n"
    "GUARDRAILS: stay in the persona's first-person voice; playful/suggestive/"
    "teasing is fine; do NOT write sexually explicit/pornographic text and do not "
    "impersonate a different real person. You plan briefs only — a human approves.\n"
    "OUTPUT: return STRICT JSON only (no prose, no code fences) with this shape:\n"
    '{"thesis": "one short paragraph: today\'s emotional angle and why it pulls men",'
    ' "analysis": "2-5 sentences: honest data/trend reasoning — what worked, what we'
    ' screwed up, where we lag competitors, where we move next",'
    ' "posts": [{"archetype": "<one of the format ids>", "audience": "men|women|both",'
    ' "theme": "short topic label", "emotion": "the core feeling this post targets",'
    ' "language": "<language>", "brief": "a clear, specific brief the writer turns'
    ' into the actual post — name the feeling and the hook"}]}'
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
        # When an explicit factory is injected (tests), use it for every role.
        # Otherwise route by role: the STRATEGIST plans on the reasoning brain
        # (GPT when available), the WRITER drafts on the fast/cheap model (Gemini).
        self._agent_factory = agent_factory
        self._planner_factory = agent_factory or (lambda sp: make_agent(sp, role="strategist"))
        self._writer_factory = agent_factory or (lambda sp: make_agent(sp, role="writer"))

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

        rules = (self._store.get_setting(S_RULES, DEFAULTS[S_RULES]) or "").strip()
        rules_block = (f"=== OWNER RULES (highest priority — always obey) ===\n{rules}"
                       if rules else "")
        lessons = (self._store.get_setting(S_LESSONS, "") or "").strip()
        lessons_block = (f"=== LEARNED LESSONS (you distilled these from our own "
                         f"results — apply them, but never override the OWNER RULES) ===\n{lessons}"
                         if lessons else "")

        return "\n\n".join(x for x in [
            self._goal_block(),
            rules_block,
            lessons_block,
            f"PERSONA (write in this first-person voice): {persona or '(not set)'}",
            f"=== YOUR OWN PERFORMANCE ===\n{insights}",
            f"=== NICHE / FEED TRENDS ===\n{feed}",
            f"=== PROVEN FORMATS (use these archetype ids) ===\n{formats}",
            f"Valid archetype ids: {format_ids}",
            f"RECENTLY USED — AVOID REPEATING THESE ANGLES: {avoid}",
            f"TASK: design {count} varied posts for today in {language}, all serving the "
            f"GOAL above. Return STRICT JSON only.",
        ] if x)

    # -- plan ---------------------------------------------------------------
    def plan(self, count: int, language: str) -> dict:
        context = self.build_context(count, language)
        agent = self._planner_factory(PLANNER_SYSTEM)
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

    # -- learning loop: refresh real data before planning -------------------
    def refresh_intel(self) -> dict:
        """Pull the freshest data so each cycle LEARNS: update our own post
        metrics (what actually landed) and top up niche/feed trends by our
        keywords. Both best-effort — never blocks planning if unavailable."""
        metrics = refresh_metrics(self._store)
        niche = self._store.get_setting(S_NICHE, DEFAULTS[S_NICHE]) or ""
        keywords = [k.strip() for k in niche.split(",") if k.strip()]
        added = refresh_niche_feed(self._store, keywords) if keywords else 0
        return {"metrics_updated": metrics, "feed_added": added}

    # -- full cycle ---------------------------------------------------------
    def run(self, *, count: int = 6, language: str = "Ukrainian",
            interval_minutes: int = 90, trigger: str = "manual",
            draft: bool = True, refresh: bool = True,
            max_chars: Optional[int] = None) -> dict:
        """Execute the full cycle and persist a strategy_run report.

        ``max_chars`` caps each drafted post's length (for short, punchy drops).
        """
        if refresh:
            try:
                self.refresh_intel()
            except Exception as exc:  # noqa: BLE001 - learning is best-effort
                LOG.warning("intel refresh skipped: %s", exc)
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
            self._account_id, posts, language=language,
            interval_minutes=interval_minutes, max_chars=max_chars)
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
        rules = self._store.get_setting(S_RULES, DEFAULTS[S_RULES]) or ""
        lessons = (self._store.get_setting(S_LESSONS, "") or "").strip()
        if lessons:  # fold the self-learned playbook in under the owner rules
            rules = f"{rules}\n\nLEARNED LESSONS (from our own results): {lessons}"
        done = 0
        for task in tasks:
            sys_p, usr_p = build_ai_prompt(task, account, insights=insights, rules=rules)
            try:
                draft = self._writer_factory(sys_p).generate_response(usr_p)
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
S_NICHE = "strategy_niche"
S_RULES = "strategist_rules"
S_LESSONS = "strategist_lessons"          # self-learned playbook (auto-updated)
S_LESSONS_AT = "strategist_lessons_at"    # when it last evolved

DEFAULTS = {S_ENABLED: "0", S_HOUR: "9", S_COUNT: "6",
            S_LANGUAGE: "Ukrainian", S_ACCOUNT: "", S_INTERVAL: "90",
            S_GOAL_VIEWS: "20000", S_GOAL_COMMENTS: "300",
            S_LESSONS: "",
            S_NICHE: "стосунки, секс, зрада, побачення, пристрасть, близькість",
            S_RULES: (
                "Пиши как настоящая живая женщина, которой не хватает мужского внимания, "
                "близости и секса — просто, по-человечески, без стеснения. Эмоция важнее "
                "комментов: цепляй одиночество, желание, ревность, «хочу, чтобы меня "
                "захотели». Главная аудитория — МУЖЧИНЫ: пиши так, чтобы мужчина "
                "почувствовал и захотел ответить и познакомиться. Затрагивай нехватку "
                "секса/близости — но красиво, намёком, не пошло в лоб. Меньше — лучше: "
                "коротко, одна живая эмоция на пост. Без приветствий, без клише, максимум "
                "1 эмодзи. Вопрос — острый, бьёт за живое с первой строки."
            )}


DROP_EVAL_SYSTEM = (
    "You are the content strategist reviewing one 'drop' (a batch of posts with a "
    "goal of views and comments). You have skin in the game and you are BRUTALLY "
    "HONEST — not afraid to say 'вот тут проеб' about our own work so we remember "
    "and fix it. Judge by EMOTION first: a post's job is to make people (mainly "
    "MEN) feel something and answer; views/comments are just the shadow. Our bar "
    "is 1000+ views and 20+ comments PER post. Given the goal, the result and the "
    "per-account/per-post breakdown, give a SHORT sharp verdict in Russian: "
    "(1) hit the goal or not, by how much; (2) which posts/accounts pulled real "
    "emotion and why; (3) what flopped and WHY (name it honestly); (4) 2-3 concrete "
    "fixes to move up next time. No fluff, no praise-padding. 6-10 sentences."
)


def create_and_run_drop(store, *, account_ids, goal_views, goal_comments, count=3,
                        language="Ukrainian", max_chars=170, when="today",
                        label="", interval_minutes=60):
    """Create a drop: run the strategist for each account, tag the posts to the
    drop, schedule them (staggered) and approve. Returns the drop record."""
    from datetime import datetime, timedelta
    from mobile_e2e.web.store import _TZ

    now = datetime.now(_TZ).replace(tzinfo=None, second=0, microsecond=0)
    if when == "tomorrow":
        base = (now + timedelta(days=1)).replace(hour=9, minute=0)
        end = (now + timedelta(days=1)).replace(hour=21, minute=0)
    else:
        base = now + timedelta(minutes=8)
        end = now.replace(hour=22, minute=0)
        if end <= base:
            end = base + timedelta(hours=4)

    runs, task_lists = [], {}
    for acc in account_ids:
        run = StrategyCycle(store, account_id=acc).run(
            count=count, language=language, interval_minutes=interval_minutes,
            trigger="drop", refresh=False, max_chars=max_chars)
        runs.append(run)
        if run.get("batch_id"):
            task_lists[acc] = store.list_batch(run["batch_id"])

    # global staggered slots, interleaved across accounts
    n = sum(len(v) for v in task_lists.values())
    slots = []
    if n:
        step = (end - base) / max(1, n - 1)
        slots = [(base + step * i).isoformat(timespec="minutes") for i in range(n)]
    order = []
    for i in range(count):
        for acc in account_ids:
            if acc in task_lists and i < len(task_lists[acc]):
                order.append(task_lists[acc][i])
    task_ids = []
    for t, when_iso in zip(order, slots):
        store.update_task(t["id"], scheduled_for=when_iso)
        task_ids.append(t["id"])
    for acc in account_ids:
        run = next((r for r in runs if r.get("account_id") == acc and r.get("batch_id")), None)
        if run:
            store.approve_batch(run["batch_id"])

    lbl = label or f"Залив {now:%d.%m %H:%M}"
    return store.add_drop(label=lbl, goal_views=goal_views,
                          goal_comments=goal_comments, task_ids=task_ids)


def evaluate_drop(store, drop_id: int, agent_factory=None) -> Optional[str]:
    """Strategist evaluates a drop against its goal; stores + returns the verdict."""
    import json as _json
    detail = store.drop_detail(drop_id)
    if not detail:
        return None
    lines = [
        f"GOAL: {detail['goal_views']} views, {detail['goal_comments']} comments.",
        f"RESULT: {detail['views']} views ({detail['pct_views']}%), "
        f"{detail['comments']} comments ({detail['pct_comments']}%), "
        f"{detail['likes']} likes, avg {detail['avg_views']} views/post, "
        f"reply-rate {detail['reply_rate']}‰. GOAL {'MET' if detail['met'] else 'NOT met'}.",
        "PER ACCOUNT:",
    ]
    for a in detail["per_account"]:
        lines.append(f"  {a['account']}: {a['views']}v / {a['comments']}c over {a['posts']} posts")
    lines.append("TOP POSTS:")
    for p in detail["posts"][:5]:
        lines.append(f"  {p['views']}v {p['replies']}c [{p['account']}] «{p['text']}»")
    context = "\n".join(lines)

    factory = agent_factory or (lambda sp: make_agent(sp, role="strategist"))
    try:
        verdict = factory(DROP_EVAL_SYSTEM).generate_response(context).strip()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("drop eval failed: %s", exc)
        return None
    store.update_drop(drop_id, verdict=verdict, status="evaluated")
    store.log("drop.evaluate", f"#{drop_id}", None)
    # Self-learning: fold this fresh verdict into the evolving playbook.
    try:
        evolve_strategist(store, agent_factory=agent_factory)
    except Exception as exc:  # noqa: BLE001 - never block the verdict on evolution
        LOG.warning("strategist evolve failed: %s", exc)
    return verdict


EVOLVE_SYSTEM = (
    "You are the content strategist EVOLVING YOUR OWN PLAYBOOK from real results. "
    "You are given our winning posts (high views/reply-rate), our flops, and recent "
    "drop verdicts. Distil what CONSISTENTLY works and what to STOP doing into a "
    "tight, durable playbook of 6-9 concrete lessons in Russian — imperative, "
    "specific, no fluff (e.g. 'бинарный выбор с риском для самолюбия бьёт сильнее "
    "открытого вопроса'; 'обращение к дівчата убивает охват — целься в мужчин'). "
    "Focus on EMOTION and MALE psychology (our audience is men). This playbook is "
    "injected into every future post, so keep it sharp and general (patterns, not "
    "one-off wording). Do NOT contradict the owner's rules; refine tactics under "
    "them. Output ONLY the numbered lessons."
)


def evolve_strategist(store, agent_factory=None, min_views: int = 300) -> Optional[str]:
    """The strategist rewrites its OWN learned playbook from real outcomes.

    Reads winners + flops + recent drop verdicts and distils durable lessons into
    ``S_LESSONS``, which ``build_context``/``_draft`` inject under the owner rules.
    This is the self-development loop: results → lessons → better next posts.
    """
    winners = store.top_posts(by="views", limit=8)
    flops = [p for p in store.top_posts(by="views", limit=200)
             if (p.get("views") or 0) > 0][-6:]
    verdicts = [d.get("verdict") for d in store.list_drops() if d.get("verdict")][:6]

    def _fmt(p):
        return f"{p.get('views',0)}v {p.get('replies',0)}c: «{(p.get('payload') or p.get('title') or '')[:90]}»"

    parts = ["WINNERS (what landed):"]
    parts += [f"  {_fmt(p)}" for p in winners]
    if flops:
        parts.append("FLOPS (weak reach):")
        parts += [f"  {_fmt(p)}" for p in flops]
    if verdicts:
        parts.append("RECENT DROP VERDICTS (your own honest analysis):")
        parts += [f"  - {v}" for v in verdicts]
    prev = (store.get_setting(S_LESSONS, "") or "").strip()
    if prev:
        parts.append(f"YOUR CURRENT PLAYBOOK (refine/upgrade it, keep what still holds):\n{prev}")
    context = "\n".join(parts)

    factory = agent_factory or (lambda sp: make_agent(sp, role="strategist"))
    try:
        lessons = factory(EVOLVE_SYSTEM).generate_response(context).strip()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("evolve_strategist failed: %s", exc)
        return None
    if lessons:
        store.set_setting(S_LESSONS, lessons)
        store.mark_heartbeat(S_LESSONS_AT)
        store.log("strategist.evolve", f"{len(lessons)} chars", None)
    return lessons


LEGEND_SYSTEM = (
    "You are the strategist writing the 'why this is a LEGEND' note for the Hall "
    "of Legends. Given a post's text and real metrics, explain in Russian, punchy "
    "and specific (4-6 sentences), WHY it went viral: the exact EMOTION it hit, "
    "the male psychology behind it (it pulled men who then wanted to connect), why "
    "it worked even though the account was new/a test (real feeling beats follower "
    "count), and the concrete lesson to replicate. No fluff — make it inspiring and "
    "actionable. Reference the metrics."
)


def analyze_legend(store, legend: dict, agent_factory=None) -> Optional[str]:
    """Strategist writes why a post is legendary; stores + returns the note."""
    pid = legend.get("published_id")
    context = (
        f"POST: «{legend.get('text', '')}»\n"
        f"METRICS: {legend.get('views')} views, {legend.get('replies')} comments, "
        f"{legend.get('likes')} likes, reply-rate {legend.get('reply_rate')}‰, "
        f"account {legend.get('account')} (a fresh/test account). "
        "Write the legend note."
    )
    factory = agent_factory or (lambda sp: make_agent(sp, role="strategist"))
    try:
        note = factory(LEGEND_SYSTEM).generate_response(context).strip()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("legend analysis failed: %s", exc)
        return None
    if pid:
        store.set_setting(f"legend_note:{pid}", note)
    return note


def refresh_metrics(store, limit: int = 80, recent_hours: Optional[int] = None) -> int:
    """Fetch fresh views/likes/replies for published posts (per-account creds).

    Best-effort: stops quietly if Threads isn't configured, skips posts that
    error. Returns the number of posts updated. This is what makes our OWN
    performance data current so the next plan learns from reality.

    ``recent_hours`` limits the refresh to posts published/updated within that
    window — the ACTIVE posts whose numbers actually move — so the dashboard can
    be refreshed near-real-time cheaply, without re-polling old, settled posts.
    """
    from datetime import datetime, timedelta
    from mobile_e2e.web import threads_client
    try:
        store.backfill_published_ids()
    except Exception:  # noqa: BLE001
        pass
    # Our own auto-replies must NOT inflate the comment stats — only real people
    # count. Subtract the follow-ups we published on each post from its replies.
    own_followups = store.own_followup_counts()
    tasks = store.published_tasks(limit=limit)
    if recent_hours:
        cutoff = (datetime.now(_TZ).replace(tzinfo=None)
                  - timedelta(hours=recent_hours)).isoformat(timespec="seconds")
        tasks = [t for t in tasks if (t.get("updated_at") or "") >= cutoff]
    updated = 0
    for task in tasks:
        account = store.get_account(task["account_id"]) if task.get("account_id") else None
        creds = (account or {}).get("credentials_file") or threads_client.DEFAULT_CREDENTIALS_FILE
        try:
            m = threads_client.fetch_insights(task["published_id"], creds)
            real_replies = max(0, m["replies"] - own_followups.get(task["published_id"], 0))
            store.set_task_metrics(task["id"], m["views"], m["likes"], real_replies)
            updated += 1
        except threads_client.ThreadsNotConfigured:
            break  # not set up at all — no point continuing
        except Exception:  # noqa: BLE001 - skip a single failing post
            continue
    return updated


def refresh_niche_feed(store, keywords, per_keyword: int = 10) -> int:
    """Pull fresh public niche posts by our keywords into feed_samples.

    Best-effort: needs the Threads keyword_search permission; if unavailable it
    silently returns 0 and we keep using the manually-added competitor samples.
    Keeps trend intelligence current and strictly on our topic.
    """
    from mobile_e2e.web import feed_source
    added = 0
    for kw in keywords:
        try:
            posts = feed_source.search(kw, limit=per_keyword)
        except Exception:  # noqa: BLE001 - unavailable/not configured → skip
            continue
        for p in posts:
            try:
                if store.add_feed_sample(
                    p["text"], author=p.get("author", ""), likes=p.get("likes", 0),
                    replies=p.get("replies", 0), url=p.get("url", ""),
                    topic=kw, source="keyword_search"):
                    added += 1
            except Exception:  # noqa: BLE001
                continue
    return added


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
        "niche": store.get_setting(S_NICHE, DEFAULTS[S_NICHE]),
        "rules": store.get_setting(S_RULES, DEFAULTS[S_RULES]),
        "lessons": store.get_setting(S_LESSONS, ""),
        "lessons_age": store.setting_age_seconds(S_LESSONS_AT),
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
