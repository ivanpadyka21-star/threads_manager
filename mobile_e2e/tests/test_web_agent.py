"""Tests for the autonomous planning agent (tool dispatch + loop)."""

import json
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from mobile_e2e.web.agent import AgentRunner
from mobile_e2e.web.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


# -- tool dispatch ----------------------------------------------------------
def test_dispatch_list_accounts(store):
    store.add_account(name="Acme", handle="@acme")
    r = AgentRunner(store, client=MagicMock(), model="m")
    out = r._dispatch("list_accounts", {})
    assert out["accounts"][0]["name"] == "Acme"


def test_dispatch_create_tasks_makes_pending(store):
    acc = store.add_account(name="A")
    r = AgentRunner(store, client=MagicMock(), model="m", default_account_id=acc["id"])
    out = r._dispatch("create_tasks", {"posts": ["p1", "p2", "p3"], "interval_minutes": 30})
    assert out["created"] == 3
    tasks = store.list_tasks(status="pending")
    assert len(tasks) == 3
    assert all(t["account_id"] == acc["id"] for t in tasks)


def test_dispatch_get_analytics(store):
    r = AgentRunner(store, client=MagicMock(), model="m")
    out = r._dispatch("get_analytics", {"hours": 24})
    assert "effectiveness" in out and "ai" in out


def test_dispatch_get_content_insights(store):
    acc = store.add_account(name="A")
    t = store.add_task(account_id=acc["id"], title="w", payload="Що вам треба?")
    store.set_task_result(t["id"], "Що вам треба?")
    store.set_task_published(t["id"], "111")
    store.set_task_metrics(t["id"], views=500, likes=30, replies=9)
    r = AgentRunner(store, client=MagicMock(), model="m")
    out = r._dispatch("get_content_insights", {})
    assert out["sample_count"] == 1
    assert out["top"][0]["views"] == 500
    assert "summary" in out


def test_dispatch_unknown_tool(store):
    r = AgentRunner(store, client=MagicMock(), model="m")
    assert "error" in r._dispatch("nope", {})


def test_dispatch_generate_drafts_directs_writer(store):
    acc = store.add_account(name="A")
    batch = store.add_batch(account_id=acc["id"], briefs=["a", "b"])
    r = AgentRunner(store, client=MagicMock(), model="m", default_account_id=acc["id"])
    fake_writer = MagicMock()
    fake_writer.generate_response.return_value = "draft text"
    with patch("mobile_e2e.web.agent.AIAgent", return_value=fake_writer):
        out = r._dispatch("generate_drafts", {"batch_id": batch["batch_id"]})
    assert out["generated"] == 2
    assert all(t["result"] == "draft text" for t in store.list_batch(batch["batch_id"]))


# -- loop -------------------------------------------------------------------
def _tool_call(name, args):
    tc = MagicMock()
    tc.id = "call_1"
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def _message(content=None, tool_calls=None):
    m = MagicMock()
    m.content = content
    m.tool_calls = tool_calls
    return m


def _response(msg):
    r = MagicMock()
    r.choices = [MagicMock(message=msg)]
    return r


def test_run_calls_tool_then_answers(store):
    acc = store.add_account(name="A")
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _response(_message(tool_calls=[_tool_call("create_tasks", {"posts": ["a", "b"], "account_id": acc["id"]})])),
        _response(_message(content="Created 2 posts for review.")),
    ]
    runner = AgentRunner(store, client=client, model="m")
    result = runner.run("make 2 posts about coffee")

    assert result["answer"] == "Created 2 posts for review."
    assert len(result["steps"]) == 1
    assert result["steps"][0]["tool"] == "create_tasks"
    assert result["steps"][0]["result"]["created"] == 2
    # tasks are pending (not published)
    assert len(store.list_tasks(status="pending")) == 2


def test_agent_switches_model_on_rate_limit(store):
    def _rl():
        return openai.RateLimitError(
            "rl", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)
    client = MagicMock()
    client.chat.completions.create.side_effect = [_rl(), _response(_message(content="ok"))]
    # model=None -> uses the provider fallback chain (flash -> lite)
    runner = AgentRunner(store, client=client, model=None)
    result = runner.run("hi")
    assert result["answer"] == "ok"
    assert client.chat.completions.create.call_count == 2  # switched to 2nd model


def test_run_history_threaded(store):
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _response(_message(content="first")),
        _response(_message(content="second")),
    ]
    r1 = AgentRunner(store, client=client, model="m").run("hi")
    assert r1["answer"] == "first"
    r2 = AgentRunner(store, client=client, model="m").run("more", history=r1["messages"])
    assert r2["answer"] == "second"
    roles = [m["role"] for m in r2["messages"]]
    assert roles.count("user") == 2  # conversation remembered


def test_run_stops_at_step_limit(store):
    client = MagicMock()
    # always returns a tool call -> never a final answer
    client.chat.completions.create.return_value = _response(
        _message(tool_calls=[_tool_call("get_analytics", {})])
    )
    runner = AgentRunner(store, client=client, model="m", max_steps=3)
    result = runner.run("loop forever")
    assert "Stopped" in result["answer"]
    assert len(result["steps"]) == 3
