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
    "You are the content strategist reviewing one 'drop'. You have skin in the game "
    "and you are BRUTALLY HONEST — not afraid to say 'вот тут проеб'. Our VECTOR: a "
    "post must make a MAN WANT HER as a woman → visit the profile → LIKE, FOLLOW, "
    "click the bio link. So judge by these PRIORITIES in order: "
    "(1) FOLLOWERS gained, (2) LIKES, (3) link CLICKS, (4) views, (5) comments. "
    "Likes+follows beat comments — a debate that gets comments but no likes/follows "
    "is a FAIL of the new vector. Given the goal, the result and the per-account/"
    "per-post breakdown, give a SHORT sharp verdict in Russian: (1) did we grow "
    "(follows/likes/clicks) or just farm comments; (2) which posts pulled real "
    "DESIRE (likes/attraction) and why; (3) what flopped and WHY; (4) 2-3 concrete "
    "fixes toward follows+likes+clicks. No fluff. 6-10 sentences."
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
    # (Self-learning is now folded into the once-a-day analysis — no auto-evolve
    # here, so GPT-5.5 runs deliberately once a day, not on every verdict.)
    return verdict


EVOLVE_SYSTEM = (
    "You are the content strategist EVOLVING YOUR OWN PLAYBOOK from real results. "
    "OUR VECTOR: make a man WANT HER as a woman → profile → LIKE, FOLLOW, click "
    "the bio link. Judge 'winning' by that — FOLLOWERS, LIKES, clicks — NOT by "
    "comments. A post that farmed comments with a debate question but got few "
    "likes/follows is a FAIL to LEARN FROM, not a win to copy. Given our posts, "
    "flops and drop verdicts, distil a tight, durable playbook of 6-9 concrete "
    "lessons in Russian — imperative, specific, no fluff (e.g. 'лёгкая пошлість + "
    "натяк на нестачу близькості даёт лайки и подписки'; 'фото + короткий чуттєвий "
    "текст бьёт сильнее вопроса'; 'НЕ задавать дебаты-вопросы мужчинам'). Focus on "
    "DESIRE + a woman's personality/photo; audience is men who must want HER. This "
    "playbook is injected into every future post — keep it sharp and general. Do "
    "NOT contradict the owner's rules; refine tactics under them. Output ONLY the "
    "numbered lessons."
)


DAILY_ANALYSIS_SYSTEM = (
    "You are the HEAD content strategist AND a second pair of eyes working as a "
    "PAIR with Claude — improve each other, catch what the owner would never "
    "notice, push each other to be sharper. This is the ONCE-A-DAY deep pass for "
    "an SMM creator (adult, private opted-in 18+, her own real identity). "
    "OUR VECTOR: make a man WANT HER as a woman → profile → LIKE, FOLLOW, click "
    "the bio link. Priorities: followers > likes > clicks > views > comments.\n"
    "LOOK THE NUMBERS IN THE EYE — HONESTLY, every single day. How to read them:\n"
    "• If today has NO numbers, say it plainly ('сегодня цифр нет, день провальный') "
    "— a flat day means nobody carries tomorrow unless we get BETTER right now. Name "
    "the day: провал / средне / есть рост.\n"
    "• Beware the LEGEND-CARRY: if ONE legendary post inflates the totals, judge the "
    "REST of the posts separately — the median post may be dying while the average lies.\n"
    "• Read trajectory & mismatch SIGNALS: a post that only STARTED climbing now "
    "(watch it, maybe boost it); clicks coming but no views (or views but no "
    "likes/clicks — wrong vector); a dead account; photo vs text conversion.\n"
    "• Propose NEW testable HYPOTHESES the owner can approve so we test them — you "
    "are allowed to push your own bets.\n"
    "• Set a HARD goal for TOMORROW. Nietzsche: 'ставьте цели, которых сложно "
    "достичь — тогда начинаете расти.' A stretch ABOVE today, never below.\n"
    "Write a THOROUGH analysis (NOT 2 sentences — cite real numbers), the day's "
    "sharp STRATEGY, and the day's POSTS. Posts DNA: light lewdness + note of "
    "lack/longing for a man's hands + her personality (bold, cute, a little "
    "under-loved); first person; ≤1 emoji; NEVER debate questions to men; reads "
    "like a caption to a sexy photo (fantasy, not porn); each post a legend "
    "candidate. Return STRICT JSON only: {"
    "\"verdict\": \"1-2 RU sentences: есть цифры или нет и почему; назови день\", "
    "\"analysis\": \"8-12 RU sentences, concrete, cite real numbers, separate the "
    "legend from the rest\", "
    "\"signals\": [\"short RU number-signals you noticed (клики без просмотров, "
    "пост только начал расти, мёртвый аккаунт, ...)\"], "
    "\"hypotheses\": [\"short RU testable idea to propose to the owner\"], "
    "\"strategy\": \"3-4 RU sentences\", "
    "\"tomorrow_goal\": {\"followers\": int, \"likes\": int, \"clicks\": int, "
    "\"views\": int}, "
    "\"posts\": [{\"account\": \"handle or ''\", \"text\": \"post in Ukrainian\"}]}. "
    "No prose outside JSON."
)


def run_daily_analysis(store, count: int = 10, agent_factory=None) -> dict:
    """The once-a-day A-to-Z pass: ONE deep GPT-5.5 call → analysis + strategy +
    the day's posts → a scheduled drop. Replaces the scattered auto-triggers so
    the strategist runs deliberately once (cheap + coherent)."""
    import json as _json
    from collections import defaultdict
    from datetime import datetime, timedelta
    from mobile_e2e.web.store import _TZ

    # Single-run guard (DB-based so it survives restarts): mark start; the API
    # treats a run < 8 min old as "in progress" and blocks a second one.
    store.mark_heartbeat("daily_running_at")

    try:
        refresh_metrics(store, limit=200)
    except Exception:  # noqa: BLE001
        pass

    top = store.top_posts(by="likes", limit=8)
    flops = [p for p in store.top_posts(by="views", limit=120)
             if (p.get("views") or 0) > 0 and (p.get("likes") or 0) <= 2][:5]
    feed = store.feed_insights().get("text", "")
    dg = store.daily_goal()
    kpi_full = store.account_kpi()
    kpi = kpi_full.get("goals", {})
    per_acc = kpi_full.get("per_account", [])
    deltas = store.daily_deltas().get("metrics", {})
    verdicts = [d.get("verdict") for d in store.list_drops() if d.get("verdict")][:3]
    rules = store.get_setting(S_RULES, DEFAULTS[S_RULES])

    # Recent posts (last ~12h) — are they climbing or dead on arrival?
    _now_dt = datetime.now(_TZ).replace(tzinfo=None)
    climbers = []
    for p in store.published_tasks(limit=60):
        try:
            up = datetime.fromisoformat((p.get("updated_at") or "")[:19])
        except ValueError:
            continue
        if (_now_dt - up).total_seconds() <= 12 * 3600:
            climbers.append(p)
    climbers = climbers[:8]

    # Per-account clicks-vs-views mismatch signals (clicks w/o views, views w/o clicks)
    sig_lines = []
    for a in per_acc:
        v, c, f = a.get("profile_views", 0), a.get("clicks", 0), a.get("followers", 0)
        sig_lines.append(f"  {a.get('account','?')}: {f}👥 · {v}👁 профиль · {c}🔗 клики · "
                         f"+{a.get('followers_delta_day',0)} подписч/день")

    def _dl(m):
        x = deltas.get(m, {})
        return f"{x.get('prev',0)}→{x.get('cur',0)} ({x.get('delta',0):+d})"

    # per-account reach + photo-vs-text conversion (operational layer)
    hands = {a["id"]: (a.get("handle") or "") for a in store.list_accounts()}
    acc_perf = defaultdict(lambda: {"likes": 0, "views": 0, "posts": 0})
    fmt = {"photo": {"likes": 0, "views": 0, "posts": 0},
           "text": {"likes": 0, "views": 0, "posts": 0}}
    for p in store.published_tasks(limit=200):
        h = hands.get(p.get("account_id"), "?")
        lk, vw = p.get("likes") or 0, p.get("views") or 0
        acc_perf[h]["likes"] += lk; acc_perf[h]["views"] += vw; acc_perf[h]["posts"] += 1
        k = "photo" if p.get("photo") else "text"
        fmt[k]["likes"] += lk; fmt[k]["views"] += vw; fmt[k]["posts"] += 1

    def _rate(d):
        return round(d["likes"] * 100 / d["views"], 1) if d["views"] else 0.0
    acc_lines = [f"  {h}: {d['likes']}❤ / {d['views']}👁 ({_rate(d)}% like-rate) over {d['posts']} posts"
                 for h, d in sorted(acc_perf.items(), key=lambda kv: kv[1]["likes"], reverse=True) if h]
    fmt_line = (f"  PHOTO: {fmt['photo']['likes']}❤ / {fmt['photo']['views']}👁 "
                f"({_rate(fmt['photo'])}% like-rate)\n"
                f"  TEXT:  {fmt['text']['likes']}❤ / {fmt['text']['views']}👁 "
                f"({_rate(fmt['text'])}% like-rate)")

    def _p(p):
        return (f"{p.get('likes',0)}❤ {p.get('views',0)}👁 {p.get('replies',0)}💬: "
                f"«{(p.get('payload') or p.get('title') or '')[:80]}»")

    def _g(m):
        x = kpi.get(m, {})
        return f"{x.get('cur',0)}/{x.get('target',0)}"

    context = "\n".join([x for x in [
        f"OWNER RULES (obey): {rules}",
        f"TODAY'S GOAL: подписки {dg['followers']['cur']}/{dg['followers']['target']}, "
        f"лайки {dg['likes']['cur']}/{dg['likes']['target']}, клики {dg['clicks']['cur']}/{dg['clicks']['target']}",
        f"30-DAY: подписки {_g('followers')}, лайки {_g('likes')}, клики {_g('clicks')}",
        "=== TODAY'S DELTAS (было→стало за сегодня — look them in the eye) ===\n"
        f"  подписчики: {_dl('followers')}\n  лайки: {_dl('likes')}\n"
        f"  клики: {_dl('clicks')}\n  просмотры профилей: {_dl('profile_views')}",
        "=== TOP POSTS BY LIKES (what pulls desire; the 1st may be a legend that carries stats) ===",
        *[f"  {_p(p)}" for p in top],
        ("=== FLOPS (views but ~no likes — the wrong vector) ===\n"
         + "\n".join(f"  {_p(p)}" for p in flops)) if flops else "",
        ("=== RECENT POSTS last ~12h (climbing or dead on arrival?) ===\n"
         + "\n".join(f"  {_p(p)}" for p in climbers)) if climbers else "",
        "=== PER-ACCOUNT SIGNALS (clicks vs views vs followers — spot mismatches) ===\n"
        + "\n".join(sig_lines),
        "=== PER-ACCOUNT REACH (which account pulls, which is dead) ===\n"
        + "\n".join(acc_lines),
        "=== PHOTO vs TEXT (what converts views→likes) ===\n" + fmt_line,
        f"=== COMPETITOR INTEL ===\n{feed[:900]}" if feed else "",
        ("=== RECENT DROP VERDICTS ===\n" + "\n".join(f"  - {v}" for v in verdicts)) if verdicts else "",
        f"TASK: honest daily analysis (verdict+signals+hypotheses) + strategy + "
        f"tomorrow's HARD goal + {count} posts for today. STRICT JSON only.",
    ] if x])

    factory = agent_factory or (lambda sp: make_agent(sp, role="strategist"))
    raw = factory(DAILY_ANALYSIS_SYSTEM).generate_response(context)
    data = _extract_json(raw)
    verdict = str(data.get("verdict", "")).strip()
    analysis = str(data.get("analysis", "")).strip()
    strat = str(data.get("strategy", "")).strip()
    signals = [str(s).strip() for s in (data.get("signals") or []) if str(s).strip()][:6]
    hypotheses = [str(h).strip() for h in (data.get("hypotheses") or []) if str(h).strip()][:5]
    posts = [p for p in (data.get("posts") or [])
             if isinstance(p, dict) and str(p.get("text", "")).strip()][:count]

    # Nietzsche: set TOMORROW's hard goal (a stretch above today, never below the
    # current floor). These feed store.daily_goal() targets, so the day-goal bars
    # auto-tighten as we grow.
    tg = data.get("tomorrow_goal") or {}
    floors = {"followers": 30, "likes": 50, "clicks": 25, "views": 4000}
    for key, floor in floors.items():
        try:
            want = int(tg.get(key) or 0)
        except (TypeError, ValueError):
            want = 0
        target = max(want, floor)
        setting = "goal_day_views" if key == "views" else f"goal_day_{key}"
        store.set_setting(setting, str(target))

    hmap = {(a.get("handle") or "").lstrip("@").lower(): a["id"]
            for a in store.list_accounts() if a.get("handle")}
    # Only accounts in the active rotation get drops (the rest are paused). If a
    # post names a paused/unknown account, it's remapped onto an active one.
    active = store.active_account_ids()
    task_ids = []
    for i, p in enumerate(posts):
        h = str(p.get("account", "")).lstrip("@").lower()
        acc = hmap.get(h)
        if acc not in active:
            acc = active[i % len(active)] if active else acc
        text = p["text"].strip()
        t = store.add_task(account_id=acc, kind="post", title=text[:40], payload=text)
        task_ids.append(t["id"])

    now = datetime.now(_TZ).replace(tzinfo=None, second=0, microsecond=0)
    start = now + timedelta(minutes=15)
    end = now.replace(hour=23, minute=0)
    if end <= start:
        end = start + timedelta(hours=6)
    step = (end - start) / max(1, len(task_ids) - 1)
    for i, tid in enumerate(task_ids):
        store.update_task(tid, scheduled_for=(start + step * i).isoformat(timespec="minutes"))
        store.set_task_status(tid, "scheduled")

    did, label = None, ""
    if task_ids:
        label = f"Анализ дня {now:%d.%m}"
        drop = store.add_drop(label=label, goal_views=count * 400,
                              goal_comments=count * 8, task_ids=task_ids)
        did = drop["id"] if isinstance(drop, dict) else drop

    # The brief is ALWAYS linked to the exact drop it produced (drop_id + label),
    # so the dashboard never shows an analysis that doesn't match its drop.
    brief = {"verdict": verdict, "analysis": analysis, "strategy": strat,
             "signals": signals, "hypotheses": hypotheses,
             "drop_id": did, "drop_label": label, "posts": len(task_ids),
             "at": now.isoformat(timespec="minutes"), "author": "strategist"}
    store.set_setting("daily_brief", _json.dumps(brief, ensure_ascii=False))
    store.mark_heartbeat("daily_brief_at")
    store.set_setting("daily_running_at", "")  # clear the run guard
    store.log("daily.analysis", f"drop #{did}, {len(task_ids)} posts", None)
    return brief


def evolve_strategist(store, agent_factory=None, min_views: int = 300) -> Optional[str]:
    """The strategist rewrites its OWN learned playbook from real outcomes.

    Reads winners + flops + recent drop verdicts and distils durable lessons into
    ``S_LESSONS``, which ``build_context``/``_draft`` inject under the owner rules.
    This is the self-development loop: results → lessons → better next posts.
    """
    # Winners by LIKES (our priority signal), not views — the new vector rewards
    # desire (likes/follows), not comment-farming.
    winners = store.top_posts(by="likes", limit=8)
    flops = [p for p in store.top_posts(by="views", limit=200)
             if (p.get("views") or 0) > 0 and (p.get("likes") or 0) <= 2][-6:]
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
