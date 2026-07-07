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


def test_delete_task(client):
    acc = _add_account(client)
    task = client.post("/api/tasks", json={"account_id": acc["id"], "title": "junk"}).get_json()
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 200
    assert client.get("/api/tasks").get_json() == []


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


def test_reminders_endpoint(client):
    acc = _add_account(client)
    client.post("/api/tasks", json={
        "account_id": acc["id"], "title": "Ping", "reminder": "2026-07-10T09:00",
    })
    client.post("/api/tasks", json={"account_id": acc["id"], "title": "No reminder"})
    res = client.get("/api/reminders")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data) == 1
    assert data[0]["title"] == "Ping"
    assert data[0]["status"] == "pending"


def test_analytics_overview_route(client):
    acc = _add_account(client)
    task = client.post("/api/tasks", json={"account_id": acc["id"], "title": "x"}).get_json()
    client.post(f"/api/tasks/{task['id']}/approve")
    res = client.get("/api/analytics")
    assert res.status_code == 200
    body = res.get_json()
    assert "project" in body and "accounts" in body and "ai" in body


def test_account_activity_route(client):
    acc = _add_account(client)
    res = client.get(f"/api/accounts/{acc['id']}/activity?days=7")
    assert res.status_code == 200
    assert len(res.get_json()["activity"]) == 7


def test_analytics_summary_route(client):
    acc = _add_account(client, name="Beta")
    res = client.post("/api/analytics/summary", json={"account_ids": [acc["id"]]})
    assert res.status_code == 200
    assert "Beta" in res.get_json()["data"]


def test_analytics_ai_route(client):
    from unittest.mock import MagicMock, patch
    acc = _add_account(client, name="Gamma")
    fake = MagicMock()
    fake.generate_response.return_value = "Load is fine; one error to fix."
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = client.post("/api/analytics/ai", json={"account_ids": [acc["id"]], "question": "How are we?"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["answer"] == "Load is fine; one error to fix."
    assert "Gamma" in body["data"]


def test_analytics_ai_route_degrades(client):
    """If the AI call fails, the endpoint still returns computed data."""
    from unittest.mock import MagicMock, patch
    _add_account(client, name="Delta")
    fake = MagicMock()
    fake.generate_response.side_effect = RuntimeError("no key")
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = client.post("/api/analytics/ai", json={})
    assert res.status_code == 200
    body = res.get_json()
    assert body["answer"] is None and "error" in body and body["data"]


def test_prompts_crud_routes(client):
    res = client.post("/api/prompts", json={"name": "Plan A", "text": "5 posts"})
    assert res.status_code == 201
    pid = res.get_json()["id"]
    assert any(p["id"] == pid for p in client.get("/api/prompts").get_json())
    assert client.delete(f"/api/prompts/{pid}").status_code == 200
    assert client.get("/api/prompts").get_json() == []


def test_prompt_requires_fields(client):
    assert client.post("/api/prompts", json={"name": "x"}).status_code == 400


def test_patch_task_edits_text(client):
    acc = _add_account(client)
    task = client.post("/api/tasks", json={"account_id": acc["id"], "title": "x", "payload": "p"}).get_json()
    res = client.patch(f"/api/tasks/{task['id']}", json={"result": "my edited text"})
    assert res.status_code == 200 and res.get_json()["result"] == "my edited text"


def test_patch_missing_task_404(client):
    assert client.patch("/api/tasks/999", json={"result": "x"}).status_code == 404


def test_plan_route(client):
    from unittest.mock import MagicMock, patch
    fake = MagicMock()
    fake.generate_response.return_value = '["Post about coffee", "Monday motivation", "A useful tip"]'
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = client.post("/api/plan", json={"prompt": "3 posts, friendly"})
    assert res.status_code == 200
    briefs = res.get_json()["briefs"]
    assert briefs == ["Post about coffee", "Monday motivation", "A useful tip"]


def test_plan_requires_prompt(client):
    assert client.post("/api/plan", json={}).status_code == 400


def test_agent_route_keeps_session(client):
    from unittest.mock import MagicMock, patch
    fake = MagicMock()
    fake.run.return_value = {"answer": "ok", "steps": [], "messages": [{"role": "user", "content": "x"}]}
    with patch("mobile_e2e.web.app.AgentRunner", return_value=fake):
        r1 = client.post("/api/agent", json={"instruction": "do it"}).get_json()
        sid = r1["session_id"]
        assert r1["answer"] == "ok" and sid
        r2 = client.post("/api/agent", json={"instruction": "more", "session_id": sid}).get_json()
        assert r2["session_id"] == sid
    # second call passed the stored history
    assert fake.run.call_args.kwargs.get("history") is not None


def test_parse_briefs_variants():
    from mobile_e2e.web.app import _parse_briefs
    assert _parse_briefs('["a", "b"]') == ["a", "b"]
    assert _parse_briefs("```json\n[\"x\", \"y\"]\n```") == ["x", "y"]
    assert _parse_briefs("1. First\n2. Second\n- Third") == ["First", "Second", "Third"]


def test_notifications_route(client):
    acc = _add_account(client)
    client.post("/api/tasks", json={"account_id": acc["id"], "title": "P", "reminder": "2000-01-01T00:00"})
    res = client.get("/api/notifications")
    assert res.status_code == 200
    body = res.get_json()
    assert "count" in body and "items" in body
    assert any(i["type"] == "reminder" for i in body["items"])


def test_ai_usage_route(client):
    acc = _add_account(client)
    # a generation event via the AI execute path (mock the agent)
    from unittest.mock import MagicMock, patch
    task = client.post("/api/tasks", json={"account_id": acc["id"], "title": "x", "payload": "p"}).get_json()
    client.post(f"/api/tasks/{task['id']}/approve")
    fake = MagicMock(); fake.generate_response.return_value = "draft"
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        client.post(f"/api/tasks/{task['id']}/execute")
    res = client.get("/api/ai/usage")
    assert res.status_code == 200
    body = res.get_json()
    assert body["used_today"] >= 1 and body["rpd"] == 20 and body["rpm"] == 5


def test_analytics_trend_route(client):
    res = client.get("/api/analytics/trend?days=10")
    assert res.status_code == 200
    assert len(res.get_json()) == 10


def test_execute_task_success(client):
    from unittest.mock import MagicMock, patch
    acc = _add_account(client, name="Exec")
    task = client.post("/api/tasks", json={"account_id": acc["id"], "kind": "ai_generate", "title": "T", "payload": "Write a post"}).get_json()
    client.post(f"/api/tasks/{task['id']}/approve")
    fake = MagicMock(); fake.generate_response.return_value = "Here is a lovely post!"
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = client.post(f"/api/tasks/{task['id']}/execute")
    assert res.status_code == 200
    assert res.get_json()["result"] == "Here is a lovely post!"
    # AI success is recorded for analytics.
    assert any(a["action"] == "ai.generate" for a in client.get("/api/audit").get_json())


def test_execute_task_failure_autoflags(client):
    from unittest.mock import MagicMock, patch
    acc = _add_account(client, name="Exec2")
    task = client.post("/api/tasks", json={"account_id": acc["id"], "kind": "ai_generate", "title": "T", "payload": "prompt"}).get_json()
    client.post(f"/api/tasks/{task['id']}/approve")
    fake = MagicMock(); fake.generate_response.side_effect = RuntimeError("LLM down")
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = client.post(f"/api/tasks/{task['id']}/execute")
    assert res.status_code == 502
    assert res.get_json()["flagged"] is True
    # Task auto-flagged as failed (a problem) and an ai.error recorded.
    assert client.get("/api/tasks").get_json()  # sanity
    actions = [a["action"] for a in client.get("/api/audit").get_json()]
    assert "task.failed" in actions and "ai.error" in actions


def test_execute_requires_approval(client):
    acc = _add_account(client)
    task = client.post("/api/tasks", json={"account_id": acc["id"], "title": "x", "payload": "p"}).get_json()
    res = client.post(f"/api/tasks/{task['id']}/execute")  # still pending
    assert res.status_code == 409


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
