"""Tests for the Flask routes using the test client and a fake JobManager."""

import time
from unittest.mock import MagicMock

import pytest

from mobile_e2e.web.app import create_app
from mobile_e2e.web.jobs import Job, JobManager
from mobile_e2e.web.service import WorkflowRequest


@pytest.fixture
def client_and_jobs():
    jobs = MagicMock(spec=JobManager)
    app = create_app(job_manager=jobs)
    app.config.update(TESTING=True)
    return app.test_client(), jobs


def test_index_serves_page(client_and_jobs):
    client, _ = client_and_jobs
    res = client.get("/")
    assert res.status_code == 200
    assert b"Control Panel" in res.data


def test_strategies_endpoint(client_and_jobs):
    client, _ = client_and_jobs
    res = client.get("/api/strategies")
    assert res.status_code == 200
    assert res.get_json()["Accessibility ID"] == "accessibility id"


def test_preview_proxy_ok(client_and_jobs):
    client, _ = client_and_jobs
    res = client.post("/api/preview-proxy", json={"proxy_string": "1.2.3.4:8080:u:p"})
    assert res.status_code == 200
    assert "***" in res.get_json()["proxy"]


def test_preview_proxy_invalid(client_and_jobs):
    client, _ = client_and_jobs
    res = client.post("/api/preview-proxy", json={"proxy_string": "nope"})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_run_starts_job(client_and_jobs):
    client, jobs = client_and_jobs
    jobs.submit.return_value = Job(id="abc123")
    res = client.post("/api/run", json={"system_prompt": "x", "read_value": "a", "input_value": "b"})
    assert res.status_code == 202
    assert res.get_json()["job_id"] == "abc123"
    jobs.submit.assert_called_once()
    assert isinstance(jobs.submit.call_args.args[0], WorkflowRequest)


def test_run_validation_error(client_and_jobs):
    client, jobs = client_and_jobs
    jobs.submit.side_effect = ValueError("System prompt is required.")
    res = client.post("/api/run", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "System prompt is required."


def test_job_status_found(client_and_jobs):
    client, jobs = client_and_jobs
    job = Job(id="j1", status="done", logs=["a", "b"], result={"ok": True})
    jobs.get.return_value = job
    res = client.get("/api/jobs/j1")
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "done"
    assert body["logs"] == ["a", "b"]


def test_job_status_not_found(client_and_jobs):
    client, jobs = client_and_jobs
    jobs.get.return_value = None
    res = client.get("/api/jobs/missing")
    assert res.status_code == 404


# -- JobManager itself (real, with a stubbed service) -----------------------
def test_job_manager_runs_to_completion():
    service = MagicMock()
    service.run.return_value = {"ok": True, "response": "r", "logs": ["x"]}
    manager = JobManager(service=service)

    job = manager.submit(WorkflowRequest(system_prompt="x", read_value="a", input_value="b"))

    for _ in range(50):  # wait for the daemon thread (<=0.5s)
        if manager.get(job.id).status != "running":
            break
        time.sleep(0.01)

    finished = manager.get(job.id)
    assert finished.status == "done"
    assert finished.result["ok"] is True


def test_job_manager_validation_raises_before_thread():
    service = MagicMock()
    service.validate.side_effect = ValueError("bad")
    manager = JobManager(service=service)
    with pytest.raises(ValueError):
        manager.submit(WorkflowRequest())
    service.run.assert_not_called()
