"""Tests for the dashboard API routes (accounts, tasks, approval, stats)."""

from unittest.mock import MagicMock

import pytest

from mobile_e2e.web.app import create_app
from mobile_e2e.web.jobs import JobManager
from mobile_e2e.web.store import Store


@pytest.fixture
def client():
    store = Store(":memory:")
    app = create_app(job_manager=MagicMock(spec=JobManager), store=store)
    app.config.update(TESTING=True)
    yield app.test_client()
    store.close()


def _add_account(client, name="Client A", daily_limit=20):
    res = client.post("/api/accounts", json={"name": name, "daily_limit": daily_limit})
    assert res.status_code == 201
    return res.get_json()


def test_create_and_list_accounts(client):
    _add_account(client)
    res = client.get("/api/accounts")
    assert res.status_code == 200
    assert len(res.get_json()) == 1


def test_create_account_requires_name(client):
    res = client.post("/api/accounts", json={"name": "  "})
    assert res.status_code == 400


def test_update_and_delete_account(client):
    acc = _add_account(client)
    res = client.patch(f"/api/accounts/{acc['id']}", json={"tone": "formal"})
    assert res.status_code == 200 and res.get_json()["tone"] == "formal"
    res = client.delete(f"/api/accounts/{acc['id']}")
    assert res.status_code == 200


def test_update_missing_account_404(client):
    res = client.patch("/api/accounts/999", json={"tone": "x"})
    assert res.status_code == 404


def test_task_create_and_approve(client):
    acc = _add_account(client)
    res = client.post("/api/tasks", json={"account_id": acc["id"], "title": "Hi"})
    assert res.status_code == 201
    task = res.get_json()
    assert task["status"] == "pending"

    res = client.post(f"/api/tasks/{task['id']}/approve")
    assert res.status_code == 200
    assert res.get_json()["status"] == "approved"


def test_approve_beyond_limit_returns_409(client):
    acc = _add_account(client, daily_limit=1)
    t1 = client.post("/api/tasks", json={"account_id": acc["id"], "title": "a"}).get_json()
    t2 = client.post("/api/tasks", json={"account_id": acc["id"], "title": "b"}).get_json()
    assert client.post(f"/api/tasks/{t1['id']}/approve").status_code == 200
    res = client.post(f"/api/tasks/{t2['id']}/approve")
    assert res.status_code == 409
    assert "limit" in res.get_json()["error"].lower()


def test_approve_missing_task_404(client):
    assert client.post("/api/tasks/999/approve").status_code == 404


def test_task_status_endpoint(client):
    acc = _add_account(client)
    task = client.post("/api/tasks", json={"account_id": acc["id"], "title": "x"}).get_json()
    res = client.post(f"/api/tasks/{task['id']}/status", json={"status": "rejected"})
    assert res.status_code == 200 and res.get_json()["status"] == "rejected"


def test_task_status_invalid(client):
    acc = _add_account(client)
    task = client.post("/api/tasks", json={"account_id": acc["id"], "title": "x"}).get_json()
    res = client.post(f"/api/tasks/{task['id']}/status", json={"status": "bogus"})
    assert res.status_code == 400


def test_stats_and_audit(client):
    acc = _add_account(client)
    client.post("/api/tasks", json={"account_id": acc["id"], "title": "x"})
    stats = client.get("/api/stats").get_json()
    assert stats["accounts"] == 1 and stats["pending"] == 1

    per = client.get("/api/stats/accounts").get_json()
    assert per[0]["total_tasks"] == 1

    audit = client.get("/api/audit").get_json()
    assert any(a["action"] == "account.create" for a in audit)


def test_run_uses_account_proxy(client):
    """POST /api/run should fall back to the account's stored proxy."""
    store = Store(":memory:")
    jobs = MagicMock(spec=JobManager)
    jobs.submit.return_value = MagicMock(id="job1")
    app = create_app(job_manager=jobs, store=store)
    app.config.update(TESTING=True)
    c = app.test_client()

    acc = store.add_account(name="A", proxy_string="10.0.0.1:8080:u:p")
    res = c.post("/api/run", json={
        "account_id": acc["id"], "system_prompt": "x",
        "read_value": "a", "input_value": "b",
    })
    assert res.status_code == 202
    submitted = jobs.submit.call_args.args[0]
    assert submitted.proxy_string == "10.0.0.1:8080:u:p"
    store.close()
