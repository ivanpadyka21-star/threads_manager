"""Flask application exposing the pipeline as a small web app.

Run with::

    python -m mobile_e2e.web

Then open http://127.0.0.1:5000 in a browser.
"""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web.jobs import JobManager
from mobile_e2e.web.service import STRATEGIES, WorkflowRequest, parse_proxy_preview

LOG = get_logger(__name__)


def create_app(job_manager: JobManager | None = None) -> Flask:
    """Application factory (a fresh instance is convenient for tests)."""
    app = Flask(__name__)
    jobs = job_manager or JobManager()

    @app.get("/")
    def index() -> str:
        return render_template("index.html", strategies=STRATEGIES)

    @app.get("/api/strategies")
    def strategies():
        return jsonify(STRATEGIES)

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
    # threaded=True so background jobs and polling requests don't block.
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    main()
