"""Flask application exposing the SMM dashboard + pipeline.

Run with::

    python -m mobile_e2e.web

Then open http://127.0.0.1:5000 in a browser.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web import archetypes, feed_source, threads_client, warmup
from mobile_e2e.web.warmup import WarmupAgent
from mobile_e2e.web.agent import AgentRunner
from mobile_e2e.web.ai_prompt import build_ai_prompt
from mobile_e2e.web.jobs import Job, JobManager
from mobile_e2e.web.publish import publish_task
from mobile_e2e.web.scheduler import PostScheduler
from mobile_e2e.web.strategy import (
    StrategyCycle, StrategyScheduler, get_strategy_settings,
    create_and_run_drop, evaluate_drop,
    S_ENABLED, S_HOUR, S_COUNT, S_LANGUAGE, S_ACCOUNT, S_INTERVAL,
    S_GOAL_VIEWS, S_GOAL_COMMENTS, S_NICHE, S_RULES,
)
from mobile_e2e.web.service import STRATEGIES, WorkflowRequest, parse_proxy_preview
from mobile_e2e.web.store import RateLimitError, Store

LOG = get_logger(__name__)

# Default on-disk location for the dashboard database (gitignored).
_DEFAULT_DB = os.path.join(os.path.dirname(__file__), "data", "dashboard.db")


def _parse_briefs(raw: str, limit: int = 10) -> list:
    """Extract a list of short post briefs from the AI planner's reply.

    Accepts a JSON array (optionally fenced) or plain lines with bullets/numbers.
    """
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            out = [str(x).strip() for x in data if str(x).strip()]
            if out:
                return out[:limit]
    except Exception:  # noqa: BLE001 - fall back to line parsing
        pass
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln][:limit]


def create_app(
    job_manager: JobManager | None = None, store: Store | None = None
) -> Flask:
    """Application factory (a fresh instance is convenient for tests)."""
    app = Flask(__name__)
    # Live-editing friendliness: pick up template/static changes without a
    # manual restart (Python changes are handled by the reloader in main()).
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    db = store or Store(os.getenv("E2E_WEB_DB", _DEFAULT_DB))

    def _record_run(job: Job) -> None:
        ok = job.status == "done" and (job.result or {}).get("ok")
        if ok:
            db.record_event("run.ok", "workflow completed", job.account_id, level="ok")
        else:
            detail = (job.error or (job.result or {}).get("error") or "run failed")
            db.record_event("run.error", str(detail)[:200], job.account_id, level="error")

    jobs = job_manager or JobManager(on_done=_record_run)

    # Background batch draft generation (so a 10-post request never blocks/times out).
    batch_gen: dict = {}
    batch_lock = threading.Lock()

    def _generate_batch_async(batch_id: str) -> None:
        delay = float(os.getenv("E2E_AI_BATCH_DELAY", "0"))
        tasks = db.list_batch(batch_id)
        for i, task in enumerate(tasks):
            account = db.get_account(task["account_id"]) if task["account_id"] else None
            insights = db.content_insights(
                account_id=task["account_id"] if task["account_id"] else None)["text"]
            system_prompt, user_prompt = build_ai_prompt(task, account, insights=insights)
            try:
                draft = AIAgent(system_prompt).generate_response(user_prompt)
                db.set_task_result(task["id"], draft)
                db.record_event("ai.generate", f"batch draft #{task['id']}", task["account_id"], level="ok")
                with batch_lock:
                    batch_gen[batch_id]["drafted"] += 1
            except Exception as exc:  # noqa: BLE001 - degrade; leave draft empty
                db.record_event("ai.error", str(exc)[:200], task["account_id"], level="info")
            with batch_lock:
                batch_gen[batch_id]["attempted"] += 1
            if delay and i < len(tasks) - 1:
                time.sleep(delay)
        with batch_lock:
            batch_gen[batch_id]["done"] = True

    # -- pages --------------------------------------------------------------
    @app.get("/")
    def index() -> str:
        return render_template("index.html", strategies=STRATEGIES)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/strategies")
    def strategies():
        return jsonify(STRATEGIES)

    @app.get("/api/stats")
    def stats():
        return jsonify(db.stats())

    @app.get("/api/stats/accounts")
    def account_stats():
        return jsonify(db.per_account_stats())

    @app.get("/api/stats/full")
    def stats_full():
        cfg = get_strategy_settings(db)
        data = db.stats_full(goal_views=cfg["goal_views"], goal_comments=cfg["goal_comments"])
        data["daily_goal"] = db.daily_goal()
        data["deltas"] = db.portfolio_deltas()
        return jsonify(data)

    @app.get("/api/daily")
    def daily_reports():
        return jsonify(db.daily_reports(days=int(request.args.get("days", 14))))

    @app.get("/api/legends")
    def legends():
        return jsonify(db.legends(min_views=int(request.args.get("min", 1000))))

    @app.post("/api/legends/<published_id>/analyze")
    def analyze_legend_route(published_id):
        from mobile_e2e.web.strategy import analyze_legend
        legend = next((l for l in db.legends(min_views=1) if l["published_id"] == published_id), None)
        if not legend:
            return jsonify({"error": "not found"}), 404

        def _work():
            try:
                analyze_legend(db, legend)
            except Exception as exc:  # noqa: BLE001
                db.record_event("ai.error", f"legend: {exc}"[:200], None, level="info")
        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    @app.get("/api/audit")
    def audit():
        return jsonify(db.list_audit())

    @app.get("/api/reminders")
    def reminders():
        return jsonify(db.list_reminders())

    @app.get("/api/ai/usage")
    def ai_usage():
        rpm = int(os.getenv("E2E_AI_RPM", "5"))
        rpd = int(os.getenv("E2E_AI_RPD", "20"))
        return jsonify(db.ai_usage(rpm=rpm, rpd=rpd))

    @app.get("/api/notifications")
    def notifications():
        return jsonify(db.notifications())

    # -- analytics ----------------------------------------------------------
    @app.get("/api/analytics")
    def analytics():
        hours = request.args.get("hours", default=24, type=float)
        return jsonify(db.analytics_overview(hours=hours))

    @app.get("/api/accounts/<int:account_id>/activity")
    def account_activity(account_id: int):
        days = request.args.get("days", default=30, type=int)
        return jsonify({
            "activity": db.activity_daily(account_id=account_id, days=days),
            "effectiveness": db.effectiveness(account_id=account_id),
        })

    @app.get("/api/analytics/trend")
    def analytics_trend():
        days = request.args.get("days", default=30, type=int)
        account_id = request.args.get("account_id", type=int)
        return jsonify(db.effectiveness_trend(days=days, account_id=account_id))

    @app.post("/api/analytics/summary")
    def analytics_summary():
        data = request.get_json(silent=True) or {}
        account_ids = data.get("account_ids") or None
        hours = float(data.get("hours", 24))
        return jsonify({"data": db.analytics_summary_text(account_ids, hours)})

    @app.post("/api/analytics/ai")
    def analytics_ai():
        data = request.get_json(silent=True) or {}
        account_ids = data.get("account_ids") or None
        hours = float(data.get("hours", 24))
        question = (data.get("question") or "").strip() or (
            "Summarise load, problems, what works well, and what to improve."
        )
        summary = db.analytics_summary_text(account_ids, hours)
        agent = AIAgent(
            "You are an analytics assistant for an SMM operations dashboard. "
            "Be concise and practical: highlight load, problems, what works well, "
            "and concrete suggestions on what to improve or reduce.",
        )
        try:
            answer = agent.generate_response(f"{question}\n\nData:\n{summary}")
            db.record_event("ai.generate", "analytics query", level="ok")
            return jsonify({"answer": answer, "data": summary})
        except Exception as exc:  # noqa: BLE001 - degrade gracefully to raw data
            db.record_event("ai.error", str(exc)[:200], level="error")
            return jsonify({"answer": None, "data": summary, "error": str(exc)})

    # -- accounts -----------------------------------------------------------
    @app.get("/api/accounts")
    def list_accounts():
        return jsonify(db.list_accounts())

    @app.post("/api/accounts")
    def create_account():
        data = request.get_json(silent=True) or {}
        try:
            account = db.add_account(**data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(account), 201

    @app.patch("/api/accounts/<int:account_id>")
    def update_account(account_id: int):
        data = request.get_json(silent=True) or {}
        account = db.update_account(account_id, **data)
        if account is None:
            return jsonify({"error": "account not found"}), 404
        return jsonify(account)

    @app.delete("/api/accounts/<int:account_id>")
    def delete_account(account_id: int):
        db.delete_account(account_id)
        return jsonify({"deleted": account_id})

    # -- tasks --------------------------------------------------------------
    @app.get("/api/tasks")
    def list_tasks():
        account_id = request.args.get("account_id", type=int)
        status = request.args.get("status")
        return jsonify(db.list_tasks(account_id=account_id, status=status))

    @app.post("/api/tasks")
    def create_task():
        data = request.get_json(silent=True) or {}
        task = db.add_task(**data)
        return jsonify(task), 201

    @app.post("/api/tasks/<int:task_id>/approve")
    def approve_task(task_id: int):
        try:
            task = db.approve_task(task_id)
        except KeyError:
            return jsonify({"error": "task not found"}), 404
        except RateLimitError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(task)

    @app.delete("/api/tasks/<int:task_id>")
    def delete_task(task_id: int):
        db.delete_task(task_id)
        return jsonify({"deleted": task_id})

    @app.patch("/api/tasks/<int:task_id>")
    def update_task_route(task_id: int):
        data = request.get_json(silent=True) or {}
        task = db.update_task(task_id, **data)
        if task is None:
            return jsonify({"error": "task not found"}), 404
        return jsonify(task)

    # -- saved prompts ------------------------------------------------------
    @app.get("/api/prompts")
    def list_prompts():
        return jsonify(db.list_prompts())

    @app.post("/api/prompts")
    def create_prompt():
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(db.add_prompt(data.get("name", ""), data.get("text", ""))), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.delete("/api/prompts/<int:prompt_id>")
    def delete_prompt(prompt_id: int):
        db.delete_prompt(prompt_id)
        return jsonify({"deleted": prompt_id})

    @app.get("/api/viral-formats")
    def viral_formats():
        lang = request.args.get("lang", "ru")
        return jsonify(archetypes.list_archetypes(lang))

    @app.post("/api/prompts/seed-viral")
    def seed_viral_prompts():
        lang = (request.get_json(silent=True) or {}).get("lang", "ru")
        created = archetypes.seed_prompts(db, lang)
        db.log("prompts.seed_viral", f"{created} viral templates")
        return jsonify({"created": created, "prompts": db.list_prompts()})

    # -- feed / competitor intelligence -------------------------------------
    @app.get("/api/feed/samples")
    def list_feed_samples():
        return jsonify(db.list_feed_samples())

    @app.post("/api/feed/samples")
    def add_feed_samples():
        data = request.get_json(silent=True) or {}
        items = data.get("examples") if isinstance(data.get("examples"), list) else [data]
        added = 0
        for it in items:
            if not isinstance(it, dict) or not str(it.get("text", "")).strip():
                continue
            try:
                if db.add_feed_sample(
                    str(it.get("text", "")), author=str(it.get("author", "")),
                    views=int(it.get("views") or 0), likes=int(it.get("likes") or 0),
                    replies=int(it.get("replies") or 0), topic=str(it.get("topic", "")),
                    url=str(it.get("url", "")),
                ):
                    added += 1
            except (ValueError, TypeError):
                continue
        return jsonify({"added": added, "samples": db.list_feed_samples()}), 201

    @app.delete("/api/feed/samples/<int:sample_id>")
    def delete_feed_sample(sample_id: int):
        db.delete_feed_sample(sample_id)
        return jsonify({"deleted": sample_id})

    @app.get("/api/feed/insights")
    def feed_insights():
        return jsonify(db.feed_insights())

    @app.post("/api/feed/search")
    def feed_search():
        data = request.get_json(silent=True) or {}
        keyword = str(data.get("keyword", "")).strip()
        if not keyword:
            return jsonify({"error": "keyword required"}), 400
        try:
            found = feed_source.search(keyword, limit=int(data.get("limit") or 15))
        except feed_source.FeedSearchUnavailable as exc:
            return jsonify({"available": False, "reason": str(exc)}), 200
        except threads_client.ThreadsNotConfigured as exc:
            return jsonify({"available": False, "reason": str(exc)}), 200
        topic = str(data.get("topic", "")).strip()
        stored = 0
        for post in found:
            if db.add_feed_sample(
                post["text"], author=post.get("author", ""), likes=post.get("likes", 0),
                replies=post.get("replies", 0), url=post.get("url", ""),
                topic=topic, source="keyword_search",
            ):
                stored += 1
        return jsonify({"available": True, "found": len(found), "stored_new": stored,
                        "samples": db.list_feed_samples()})

    @app.get("/api/brain")
    def brain_status():
        from mobile_e2e.ai import brain
        sp = brain.role_providers("strategist")
        return jsonify({
            "openai": brain.has_openai(),
            "strategist": sp,
            "writer": brain.role_providers("writer"),
            "strategist_model": (brain.role_model("strategist", "openai")
                                 if "openai" in sp else "gemini"),
        })

    # -- daily strategy cycle -----------------------------------------------
    @app.get("/api/strategy/settings")
    def strategy_settings():
        cfg = get_strategy_settings(db)
        cfg["progress"] = db.goal_progress(account_id=cfg["account_id"], days=30)
        return jsonify(cfg)

    @app.post("/api/strategy/settings")
    def save_strategy_settings():
        data = request.get_json(silent=True) or {}
        if "enabled" in data:
            db.set_setting(S_ENABLED, "1" if data.get("enabled") else "0")
        for key, name in ((S_HOUR, "hour"), (S_COUNT, "count"), (S_INTERVAL, "interval_minutes")):
            if name in data and data.get(name) not in (None, ""):
                db.set_setting(key, str(int(data[name])))
        if "language" in data:
            db.set_setting(S_LANGUAGE, str(data.get("language") or "Ukrainian"))
        if "account_id" in data:
            db.set_setting(S_ACCOUNT, str(data.get("account_id") or ""))
        for key, name in ((S_GOAL_VIEWS, "goal_views"), (S_GOAL_COMMENTS, "goal_comments")):
            if name in data and data.get(name) not in (None, ""):
                db.set_setting(key, str(int(data[name])))
        if "niche" in data:
            db.set_setting(S_NICHE, str(data.get("niche") or ""))
        if "rules" in data:
            db.set_setting(S_RULES, str(data.get("rules") or ""))
        return jsonify(get_strategy_settings(db))

    # -- once-a-day full analysis (A-to-Z: analysis + strategy + drop) -------
    def _daily_running():
        # DB-based so a stuck/killed run can't leave it "running" forever: a run
        # older than 8 min is treated as dead.
        age = db.setting_age_seconds("daily_running_at")
        return age is not None and age < 480

    def _post_reminder():
        """Today's post status — so the owner is always reminded where posts are."""
        import datetime as _dt
        from mobile_e2e.web.store import _TZ
        today = _dt.datetime.now(_TZ).date().isoformat()
        posts = [t for t in db.published_tasks(limit=400)
                 if (t.get("updated_at") or "").startswith(today)]
        sched = [t for t in db.list_tasks(status="scheduled") if t.get("kind") == "post"]
        pending = [t for t in db.list_tasks(status="pending") if t.get("kind") == "post"]
        return {"published_today": len(posts), "scheduled": len(sched),
                "pending_approval": len(pending)}

    @app.get("/api/daily-analysis")
    def daily_analysis_get():
        raw = db.get_setting("daily_brief", "")
        brief = json.loads(raw) if raw else None
        return jsonify({"brief": brief,
                        "age": db.setting_age_seconds("daily_brief_at"),
                        "running": _daily_running(),
                        "posts": _post_reminder()})

    @app.post("/api/daily-analysis")
    def daily_analysis_run():
        if _daily_running():
            return jsonify({"error": "already running"}), 409
        data = request.get_json(silent=True) or {}
        count = int(data.get("count") or 10)
        db.mark_heartbeat("daily_running_at")  # claim the run immediately

        def _work():
            try:
                from mobile_e2e.web import strategy as _st
                _st.run_daily_analysis(db, count=count)
            except Exception as exc:  # noqa: BLE001
                db.record_event("daily.error", str(exc)[:200], None, level="error")
                db.set_setting("daily_running_at", "")  # release the guard on failure

        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    @app.post("/api/strategy/evolve")
    def strategy_evolve():
        def _work():
            try:
                from mobile_e2e.web import strategy as _st
                _st.evolve_strategist(db)
            except Exception as exc:  # noqa: BLE001
                db.record_event("strategy.error", str(exc)[:200], None, level="info")

        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    @app.get("/api/strategy/runs")
    def strategy_runs():
        return jsonify(db.list_strategy_runs())

    @app.get("/api/strategy/runs/<int:run_id>")
    def strategy_run(run_id: int):
        run = db.get_strategy_run(run_id)
        if not run:
            return jsonify({"error": "not found"}), 404
        return jsonify(run)

    @app.post("/api/strategy/run")
    def strategy_run_now():
        data = request.get_json(silent=True) or {}
        cfg = get_strategy_settings(db)
        account_id = data.get("account_id", cfg["account_id"])
        account_id = int(account_id) if account_id else None
        count = int(data.get("count") or cfg["count"])
        language = str(data.get("language") or cfg["language"])
        interval = int(data.get("interval_minutes") or cfg["interval_minutes"])

        def _work():
            try:
                StrategyCycle(db, account_id=account_id).run(
                    count=count, language=language, interval_minutes=interval, trigger="manual")
            except Exception as exc:  # noqa: BLE001 - record and move on
                db.add_strategy_run(account_id=account_id, trigger="manual",
                                    status="failed", error=str(exc)[:300])

        # The cycle makes several LLM calls; run it off the request thread and
        # let the UI poll /api/strategy/runs for the new report.
        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    # -- drops (campaigns with a goal + evaluation) -------------------------
    @app.get("/api/drops")
    def list_drops():
        return jsonify([db.drop_detail(d["id"]) for d in db.list_drops()])

    @app.get("/api/drops/<int:drop_id>")
    def drop_detail(drop_id: int):
        d = db.drop_detail(drop_id)
        return (jsonify(d), 200) if d else (jsonify({"error": "not found"}), 404)

    @app.post("/api/drops")
    def create_drop():
        data = request.get_json(silent=True) or {}
        active = [a["id"] for a in db.list_accounts() if a.get("status", "active") == "active"]
        account_ids = data.get("account_ids") or active
        account_ids = [int(a) for a in account_ids]
        gv = int(data.get("goal_views") or 0)
        gc = int(data.get("goal_comments") or 0)
        count = int(data.get("count") or 3)
        language = str(data.get("language") or "Ukrainian")
        max_chars = int(data.get("max_chars") or 170)
        when = "tomorrow" if data.get("when") == "tomorrow" else "today"
        label = str(data.get("label") or "")

        def _work():
            try:
                create_and_run_drop(db, account_ids=account_ids, goal_views=gv,
                                    goal_comments=gc, count=count, language=language,
                                    max_chars=max_chars, when=when, label=label)
            except Exception as exc:  # noqa: BLE001
                db.record_event("run.error", f"drop: {exc}"[:200], None, level="error")

        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    @app.post("/api/drops/<int:drop_id>/measure")
    def measure_drop(drop_id: int):
        # refresh live metrics first, then recompute the drop
        try:
            from mobile_e2e.web.strategy import refresh_metrics
            refresh_metrics(db)
        except Exception:  # noqa: BLE001
            pass
        d = db.drop_detail(drop_id)
        return (jsonify(d), 200) if d else (jsonify({"error": "not found"}), 404)

    @app.post("/api/drops/<int:drop_id>/evaluate")
    def evaluate_drop_route(drop_id: int):
        def _work():
            try:
                evaluate_drop(db, drop_id)
            except Exception as exc:  # noqa: BLE001
                db.record_event("ai.error", f"drop eval: {exc}"[:200], None, level="info")
        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    # -- warmup (engagement) branch -----------------------------------------
    @app.get("/api/warmup/actions")
    def warmup_actions():
        status = request.args.get("status") or None
        account_id = request.args.get("account_id", type=int)
        return jsonify({
            "actions": db.list_warmup_actions(status=status, account_id=account_id),
            "stats": db.warmup_stats(days=1),
        })

    @app.post("/api/warmup/run")
    def warmup_run():
        data = request.get_json(silent=True) or {}
        account_id = data.get("account_id") or get_strategy_settings(db)["account_id"]
        account_id = int(account_id) if account_id else None
        niche = db.get_setting(S_NICHE, "") or ""
        keywords = [k.strip() for k in niche.split(",") if k.strip()]
        replies = int(data.get("replies") or 3)
        persona = (db.get_account(account_id) or {}).get("persona", "") if account_id else ""

        result: dict = {}
        def _work():
            try:
                r = WarmupAgent(db, account_id=account_id).run(
                    keywords, replies=replies, persona=persona)
                result.update(r)
            except Exception as exc:  # noqa: BLE001
                db.record_event("warmup.error", str(exc)[:200], account_id, level="info")

        # Draft replies via Gemini off the request thread; UI polls the list.
        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    @app.post("/api/warmup/add-target")
    def warmup_add_target():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text required"}), 400
        account_id = data.get("account_id") or get_strategy_settings(db)["account_id"]
        account_id = int(account_id) if account_id else None
        persona = (db.get_account(account_id) or {}).get("persona", "") if account_id else ""

        result: dict = {}
        def _work():
            try:
                r = WarmupAgent(db, account_id=account_id).warm_post(
                    text, author=str(data.get("author", "")), url=str(data.get("url", "")),
                    target_id=str(data.get("target_id", "")), persona=persona)
                result.update(r)
            except Exception as exc:  # noqa: BLE001
                db.record_event("warmup.error", str(exc)[:200], account_id, level="info")

        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    @app.post("/api/warmup/actions/<int:action_id>/approve")
    def warmup_approve(action_id: int):
        action = db.get_warmup_action(action_id)
        if action is None:
            return jsonify({"error": "not found"}), 404
        edited = (request.get_json(silent=True) or {}).get("draft")
        if edited is not None and str(edited).strip():
            db.set_warmup_draft(action_id, str(edited))
        if action["kind"] not in ("reply", "followup"):
            # like/follow are manual — approving just marks them done.
            return jsonify({"action": db.set_warmup_status(action_id, "done")})
        try:
            published_id = warmup.publish_reply_action(db, action_id)
            return jsonify({"published_id": published_id, "action": db.get_warmup_action(action_id)})
        except threads_client.ThreadsNotConfigured as exc:
            return jsonify({"error": str(exc), "configured": False}), 409
        except Exception as exc:  # noqa: BLE001 - tell the UI to post manually
            return jsonify({"error": str(exc), "manual": True}), 200

    @app.post("/api/warmup/actions/<int:action_id>/done")
    def warmup_done(action_id: int):
        return jsonify({"action": db.set_warmup_status(action_id, "done")})

    @app.post("/api/warmup/actions/<int:action_id>/skip")
    def warmup_skip(action_id: int):
        return jsonify({"action": db.set_warmup_status(action_id, "skipped")})

    # -- follow-ups (auto-comment under our own posts, no spam) --------------
    @app.get("/api/followups")
    def followups_list():
        account_id = request.args.get("account_id", type=int)
        kinds = ("followup", "comment_reply")
        actions = [a for a in db.list_warmup_actions(status="pending", account_id=account_id)
                   if a.get("kind") in kinds]
        published = [a for a in db.list_warmup_actions(status="done", account_id=account_id)
                     if a.get("kind") in kinds][:20]
        return jsonify({
            "pending": actions,
            "published": published,
            "candidates": len(db.posts_needing_followup(account_id=account_id, limit=50)),
            "auto": str(db.get_setting("followups_auto", "1")) != "0",
            "autopublish": str(db.get_setting("followups_autopublish", "0")) != "0",
            "heartbeat_age": db.setting_age_seconds("scheduler_heartbeat"),
            "last_pass_age": db.setting_age_seconds("followups_last_run"),
            "intensity": db.get_setting("fu_intensity", "normal"),
            "pass_interval": 1800,
        })

    @app.post("/api/followups/auto")
    def followups_auto():
        on = bool((request.get_json(silent=True) or {}).get("on", True))
        db.set_setting("followups_auto", "1" if on else "0")
        return jsonify({"auto": on})

    @app.post("/api/followups/autopublish")
    def followups_autopublish():
        on = bool((request.get_json(silent=True) or {}).get("on", True))
        db.set_setting("followups_autopublish", "1" if on else "0")
        return jsonify({"autopublish": on})

    @app.post("/api/followups/intensity")
    def followups_intensity():
        on = bool((request.get_json(silent=True) or {}).get("on", True))
        db.set_setting("fu_intensity", "max" if on else "normal")
        # Max mode is only useful if drafting + publishing are on.
        if on:
            db.set_setting("followups_auto", "1")
            db.set_setting("followups_autopublish", "1")
        return jsonify({"intensity": "max" if on else "normal"})

    @app.post("/api/followups/draft")
    def followups_draft():
        data = request.get_json(silent=True) or {}
        account_id = data.get("account_id")
        account_id = int(account_id) if account_id else None
        per_run = int(data.get("per_run") or 6)

        def _work():
            try:
                warmup.draft_followups(db, account_id=account_id, per_run=per_run)
            except Exception as exc:  # noqa: BLE001
                db.record_event("followup.error", str(exc)[:200], account_id, level="info")

        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    # -- photos (image posts) -----------------------------------------------
    from mobile_e2e.web.publish import PHOTOS_DIR
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    _IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}

    @app.get("/photos/<path:name>")
    def photos_file(name):
        return send_from_directory(PHOTOS_DIR, name)

    @app.get("/api/photos")
    def photos_list():
        items = []
        for n in sorted(os.listdir(PHOTOS_DIR), reverse=True):
            if os.path.splitext(n)[1].lower() in _IMG_EXT:
                items.append({"name": n, "url": f"/photos/{n}"})
        return jsonify({"photos": items})

    @app.post("/api/photos")
    def photos_upload():
        files = request.files.getlist("file")
        saved = []
        for f in files:
            if not f or not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in _IMG_EXT:
                continue
            name = f"{int(time.time()*1000)}_{secure_filename(f.filename)}"
            f.save(os.path.join(PHOTOS_DIR, name))
            saved.append({"name": name, "url": f"/photos/{name}"})
        return jsonify({"saved": saved}), 201

    @app.post("/api/photos/post")
    def photos_post():
        data = request.get_json(silent=True) or {}
        photo = str(data.get("photo", "")).strip()
        caption = str(data.get("caption", "")).strip()
        account_id = data.get("account_id")
        if not photo or not account_id:
            return jsonify({"error": "photo and account_id required"}), 400
        account_id = int(account_id)
        task = db.add_task(account_id=account_id, kind="post",
                           title=(caption[:40] or "photo"), payload=caption, photo=photo)
        when = str(data.get("when", "")).strip()
        if when:  # schedule for later
            db.update_task(task["id"], scheduled_for=when, status="scheduled")
        else:  # publish now, off the request thread
            def _work():
                try:
                    publish_task(db, task["id"])
                except Exception as exc:  # noqa: BLE001
                    db.record_event("run.error", str(exc)[:200], account_id, level="error")
            threading.Thread(target=_work, daemon=True).start()
        return jsonify({"task": task, "scheduled": bool(when)}), 201

    # -- account KPI (growth, profile views, clicks, demographics) ----------
    @app.get("/api/kpi")
    def kpi_get():
        days = request.args.get("days", default=30, type=int)
        return jsonify(db.account_kpi(days=days))

    @app.post("/api/kpi/refresh")
    def kpi_refresh():
        def _work():
            try:
                from mobile_e2e.web.kpi import refresh_account_insights
                refresh_account_insights(db)
            except Exception as exc:  # noqa: BLE001
                db.record_event("kpi.error", str(exc)[:200], None, level="info")

        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    @app.get("/api/followups/stats")
    def followups_stats():
        days = request.args.get("days", default=14, type=int)
        return jsonify(db.reply_stats(days=days))

    @app.post("/api/followups/reply-people")
    def followups_reply_people():
        data = request.get_json(silent=True) or {}
        account_id = data.get("account_id")
        account_id = int(account_id) if account_id else None

        def _work():
            try:
                warmup.draft_comment_replies(db, account_id=account_id)
            except Exception as exc:  # noqa: BLE001
                db.record_event("comment_reply.error", str(exc)[:200], account_id, level="info")

        threading.Thread(target=_work, daemon=True).start()
        return jsonify({"started": True}), 202

    # -- autonomous agent (Gemini function-calling) -------------------------
    # In-memory conversation sessions so the agent has back-and-forth memory.
    agent_sessions: dict = {}

    @app.post("/api/agent")
    def agent_run():
        data = request.get_json(silent=True) or {}
        instruction = (data.get("instruction") or "").strip()
        if not instruction:
            return jsonify({"error": "instruction is required"}), 400
        account_id = data.get("account_id")
        session_id = data.get("session_id")
        history = agent_sessions.get(session_id) if session_id else None
        runner = AgentRunner(
            db, default_account_id=int(account_id) if account_id else None,
        )
        try:
            result = runner.run(instruction, history=history)
            db.record_event("ai.generate", "agent run", level="ok")
        except Exception as exc:  # noqa: BLE001 - surface agent failure
            db.record_event("ai.error", str(exc)[:200], level="info")
            return jsonify({"error": str(exc)}), 502
        sid = session_id or uuid.uuid4().hex
        agent_sessions[sid] = result.get("messages", [])
        return jsonify({"session_id": sid, "answer": result["answer"], "steps": result["steps"]})

    # -- AI content planner -------------------------------------------------
    @app.post("/api/plan")
    def plan():
        data = request.get_json(silent=True) or {}
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        system_prompt = (
            "You are a social-media content planner. From the user's request, "
            "produce a plan of individual post briefs — short, concrete topics, "
            "one per intended post. Infer how many posts they want (default 5, "
            "max 10). VARY the angle and format across the plan (short & punchy, "
            "reflective/heartfelt, a question, something educational) — don't "
            "repeat one formula. Respond with ONLY a JSON array of short brief "
            "strings (no numbering, no extra text)."
        )
        try:
            raw = AIAgent(system_prompt).generate_response(prompt)
            db.record_event("ai.generate", "content plan", level="ok")
        except Exception as exc:  # noqa: BLE001 - surface planner failure
            db.record_event("ai.error", str(exc)[:200], level="info")
            return jsonify({"error": str(exc)}), 502
        briefs = _parse_briefs(raw)
        return jsonify({"briefs": briefs, "raw": raw})

    # -- Threads integration ------------------------------------------------
    @app.post("/api/threads/refresh-insights")
    def refresh_insights():
        """Fetch views/likes/replies for published posts from the Threads API."""
        db.backfill_published_ids()  # cover posts published before the id column
        updated, errors = 0, 0
        for task in db.published_tasks(limit=100):
            account = db.get_account(task["account_id"]) if task["account_id"] else None
            creds = (account or {}).get("credentials_file") or threads_client.DEFAULT_CREDENTIALS_FILE
            try:
                m = threads_client.fetch_insights(task["published_id"], creds)
                db.set_task_metrics(task["id"], m["views"], m["likes"], m["replies"])
                updated += 1
            except threads_client.ThreadsNotConfigured as exc:
                return jsonify({"error": str(exc), "configured": False}), 409
            except Exception:  # noqa: BLE001 - skip a single post that fails
                errors += 1
        return jsonify({"updated": updated, "errors": errors})

    @app.get("/api/analytics/top-posts")
    def top_posts():
        by = request.args.get("by", "views")
        limit = request.args.get("limit", default=10, type=int)
        rows = db.top_posts(by=by, limit=limit)
        return jsonify([{
            "id": r["id"], "title": r["title"], "result": r["result"],
            "views": r["views"], "likes": r["likes"], "replies": r["replies"],
            "when": r["updated_at"],
        } for r in rows])

    @app.get("/api/analytics/content-insights")
    def content_insights():
        account_id = request.args.get("account_id", type=int)
        return jsonify(db.content_insights(account_id=account_id))

    @app.get("/api/threads/status")
    def threads_status():
        account_id = request.args.get("account_id", type=int)
        creds = threads_client.DEFAULT_CREDENTIALS_FILE
        if account_id:
            acc = db.get_account(account_id)
            if acc and acc.get("credentials_file"):
                creds = acc["credentials_file"]
        return jsonify(threads_client.status(creds))

    @app.post("/api/tasks/<int:task_id>/publish")
    def publish_task_route(task_id: int):
        """Publish an approved/scheduled task's content to Threads."""
        task = db.get_task(task_id)
        if task is None:
            return jsonify({"error": "task not found"}), 404
        if task["status"] not in ("approved", "scheduled"):
            return jsonify({"error": "task must be approved first"}), 409
        try:
            published_id = publish_task(db, task_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except threads_client.ThreadsNotConfigured as exc:
            return jsonify({"error": str(exc), "configured": False}), 409
        except Exception as exc:  # noqa: BLE001 - already flagged by publish_task
            return jsonify({"error": str(exc)}), 502
        return jsonify({"published_id": published_id, "task": db.get_task(task_id)})

    # -- batches (multiple scheduled posts) ---------------------------------
    @app.post("/api/batches")
    def create_batch():
        data = request.get_json(silent=True) or {}
        briefs = data.get("briefs")
        if not briefs:
            topic = (data.get("topic") or "").strip()
            count = int(data.get("count") or 0)
            if topic and count:
                briefs = [topic] * min(count, 10)
        if not briefs:
            return jsonify({"error": "provide briefs or topic+count"}), 400
        account_id = data.get("account_id")
        max_chars = data.get("max_chars")
        try:
            batch = db.add_batch(
                account_id=int(account_id) if account_id else None,
                briefs=briefs,
                language=data.get("language", ""),
                style=data.get("style", ""),
                interval_minutes=int(data.get("interval_minutes") or 60),
                start_at=data.get("start_at") or None,
                max_chars=int(max_chars) if max_chars else None,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        # Generate drafts in the background so the request returns immediately;
        # the UI polls GET /api/batches/<id> for progress.
        with batch_lock:
            batch_gen[batch["batch_id"]] = {
                "total": len(batch["tasks"]), "drafted": 0, "attempted": 0, "done": False,
            }
        threading.Thread(
            target=_generate_batch_async, args=(batch["batch_id"],), daemon=True
        ).start()
        return jsonify({
            "batch_id": batch["batch_id"], "tasks": batch["tasks"], "generating": True,
        }), 202

    @app.get("/api/batches/<batch_id>")
    def get_batch(batch_id: str):
        with batch_lock:
            st = dict(batch_gen.get(batch_id, {}))
        return jsonify({
            "tasks": db.list_batch(batch_id),
            "status": st,
            "generating": bool(st) and not st.get("done", False),
        })

    @app.post("/api/batches/<batch_id>/approve")
    def approve_batch(batch_id: str):
        return jsonify({"scheduled": db.approve_batch(batch_id)})

    @app.post("/api/tasks/<int:task_id>/execute")
    def execute_task(task_id: int):
        """Run an approved AI task: generate a draft, or auto-flag on failure."""
        task = db.get_task(task_id)
        if task is None:
            return jsonify({"error": "task not found"}), 404
        if task["status"] != "approved":
            return jsonify({"error": "task must be approved first"}), 409
        if not (task.get("payload") or task.get("title") or task.get("target") or "").strip():
            return jsonify({"error": "task has no content/prompt"}), 400

        account_id = task.get("account_id")
        account = db.get_account(account_id) if account_id else None
        insights = db.content_insights(account_id=account_id if account_id else None)["text"]
        system_prompt, user_prompt = build_ai_prompt(task, account, insights=insights)
        agent = AIAgent(system_prompt)
        try:
            draft = agent.generate_response(user_prompt)
        except Exception as exc:  # noqa: BLE001 - auto-flag the problem
            # ai.error feeds AI stats (level info to avoid double-weighting),
            # and marking the task failed records the effectiveness problem.
            db.record_event("ai.error", str(exc)[:200], account_id, level="info")
            db.set_task_status(task_id, "failed")
            return jsonify({"error": str(exc), "flagged": True}), 502
        db.set_task_result(task_id, draft)
        db.record_event("ai.generate", f"draft for task #{task_id}", account_id, level="ok")
        return jsonify({"result": draft, "task": db.get_task(task_id)})

    @app.post("/api/tasks/<int:task_id>/status")
    def set_task_status(task_id: int):
        data = request.get_json(silent=True) or {}
        try:
            task = db.set_task_status(task_id, data.get("status", ""))
        except KeyError:
            return jsonify({"error": "task not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(task)

    # -- workflow runs ------------------------------------------------------
    @app.post("/api/preview-proxy")
    def preview_proxy():
        data = request.get_json(silent=True) or {}
        try:
            preview = parse_proxy_preview(data.get("proxy_string", ""))
            return jsonify({"proxy": preview})
        except ProxyParseError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/run")
    def run():
        data = request.get_json(silent=True) or {}
        # If an account is referenced, use its stored proxy by default.
        account_id = data.get("account_id")
        if account_id and not data.get("proxy_string"):
            account = db.get_account(int(account_id))
            if account:
                data["proxy_string"] = account.get("proxy_string", "")
        req = WorkflowRequest.from_dict(data)
        try:
            job = jobs.submit(req, account_id=int(account_id) if account_id else None)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"job_id": job.id}), 202

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404
        return jsonify(job.snapshot())

    return app


def main() -> None:
    # Populate os.environ from .env so os.getenv-based integrations (Threads)
    # see the configured values (pydantic settings read .env on their own).
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001 - .env is optional
        pass
    store = Store(os.getenv("E2E_WEB_DB", _DEFAULT_DB))
    app = create_app(store=store)
    # Auto-publish scheduled posts in the background — but only in the actual
    # serving process. With the debug reloader, main() runs in BOTH the watcher
    # parent and the worker child; starting the scheduler in both published
    # every post twice. WERKZEUG_RUN_MAIN is set only in the worker child.
    if os.getenv("E2E_WEB_DEBUG", "0") != "1" or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        PostScheduler(store, interval_seconds=30).start()
        StrategyScheduler(store, interval_seconds=300).start()
    host = os.getenv("E2E_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("E2E_WEB_PORT", "5000"))
    # Set E2E_WEB_DEBUG=1 for the "workshop" mode: the reloader restarts the
    # server on any Python change so edits go live without a manual restart.
    debug = os.getenv("E2E_WEB_DEBUG", "0") == "1"
    # threaded=True so background jobs and polling requests don't block.
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
