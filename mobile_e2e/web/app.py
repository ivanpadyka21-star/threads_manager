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

from flask import Flask, jsonify, render_template, request

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web import threads_client
from mobile_e2e.web.agent import AgentRunner
from mobile_e2e.web.ai_prompt import build_ai_prompt
from mobile_e2e.web.jobs import Job, JobManager
from mobile_e2e.web.publish import publish_task
from mobile_e2e.web.scheduler import PostScheduler
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
    host = os.getenv("E2E_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("E2E_WEB_PORT", "5000"))
    # Set E2E_WEB_DEBUG=1 for the "workshop" mode: the reloader restarts the
    # server on any Python change so edits go live without a manual restart.
    debug = os.getenv("E2E_WEB_DEBUG", "0") == "1"
    # threaded=True so background jobs and polling requests don't block.
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
