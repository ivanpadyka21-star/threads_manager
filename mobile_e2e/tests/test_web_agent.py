"""Tests for the autonomous planning agent (tool dispatch + loop)."""

import json
from unittest.mock import MagicMock

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


def test_dispatch_unknown_tool(store):
    r = AgentRunner(store, client=MagicMock(), model="m")
    assert "error" in r._dispatch("nope", {})


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
