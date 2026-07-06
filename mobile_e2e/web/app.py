"""Flask application exposing the SMM dashboard + pipeline.

Run with::

    python -m mobile_e2e.web

Then open http://127.0.0.1:5000 in a browser.
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web.jobs import JobManager
from mobile_e2e.web.service import STRATEGIES, WorkflowRequest, parse_proxy_preview
from mobile_e2e.web.store import RateLimitError, Store

LOG = get_logger(__name__)

# Default on-disk location for the dashboard database (gitignored).
_DEFAULT_DB = os.path.join(os.path.dirname(__file__), "data", "dashboard.db")


def create_app(
    job_manager: JobManager | None = None, store: Store | None = None
) -> Flask:
    """Application factory (a fresh instance is convenient for tests)."""
    app = Flask(__name__)
    # Live-editing friendliness: pick up template/static changes without a
    # manual restart (Python changes are handled by the reloader in main()).
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    jobs = job_manager or JobManager()
    db = store or Store(os.getenv("E2E_WEB_DB", _DEFAULT_DB))

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
            job = jobs.submit(req)
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
    app = create_app()
    host = os.getenv("E2E_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("E2E_WEB_PORT", "5000"))
    # Set E2E_WEB_DEBUG=1 for the "workshop" mode: the reloader restarts the
    # server on any Python change so edits go live without a manual restart.
    debug = os.getenv("E2E_WEB_DEBUG", "0") == "1"
    # threaded=True so background jobs and polling requests don't block.
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
