"""Tests for the daily strategy cycle (research → plan → queue → draft → report)."""

import json

import pytest

from mobile_e2e.web.store import Store
from mobile_e2e.web.strategy import (
    StrategyCycle, StrategyScheduler, get_strategy_settings, _extract_json,
    S_ENABLED, S_HOUR, S_LAST_DATE,
)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


class FakeAgent:
    """Stand-in for AIAgent: returns the plan JSON for the planner call, and a
    short draft for any writer call (distinguished by system prompt)."""

    PLAN = {
        "thesis": "Ride intimacy + a reply-chain question to men today.",
        "analysis": "Reply-drivers skew to gender questions; medium intimacy posts win reach.",
        "posts": [
            {"archetype": "chain_question_men", "audience": "men", "theme": "измена",
             "language": "Ukrainian", "brief": "Ask men about cheating, be honest."},
            {"archetype": "longing_intimacy", "audience": "both", "theme": "близость",
             "language": "Ukrainian", "brief": "A vulnerable confession about wanting closeness."},
            # duplicate archetype+theme should be de-duplicated away
            {"archetype": "chain_question_men", "audience": "men", "theme": "измена",
             "language": "Ukrainian", "brief": "Duplicate angle."},
        ],
    }

    def __init__(self, system_prompt):
        self._planner = "STRATEGIST" in system_prompt

    def generate_response(self, ctx):
        if self._planner:
            return "```json\n" + json.dumps(FakeAgent.PLAN, ensure_ascii=False) + "\n```"
        return "Чоловіки, зраджували колись? Тільки чесно."


def _factory(sp):
    return FakeAgent(sp)


def test_extract_json_tolerates_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('prose {"b": 2} trailing') == {"b": 2}
    with pytest.raises(ValueError):
        _extract_json("no json here")


def test_plan_dedupes_archetype_theme(store):
    cycle = StrategyCycle(store, agent_factory=_factory)
    plan = cycle.plan(count=6, language="Ukrainian")
    assert len(plan["posts"]) == 2  # duplicate dropped
    assert plan["thesis"]


def test_full_cycle_creates_tagged_drafted_tasks_and_report(store):
    acc = store.add_account(name="A", persona="sexologist, playful")
    cycle = StrategyCycle(store, account_id=acc["id"], agent_factory=_factory)
    run = cycle.run(count=6, language="Ukrainian", trigger="manual")
    assert run["status"] == "drafted"
    assert run["tasks_created"] == 2
    tasks = store.list_batch(run["batch_id"])
    assert {t["archetype"] for t in tasks} == {"chain_question_men", "longing_intimacy"}
    assert all(t["status"] == "pending" for t in tasks)       # human approves
    assert all((t["result"] or "").strip() for t in tasks)     # drafts written
    # the report is retrievable with its analysis
    got = store.get_strategy_run(run["id"])
    assert "reply-drivers" in got["analysis"].lower() or got["analysis"]


def test_recent_angles_feed_anti_repetition(store):
    acc = store.add_account(name="A")
    store.add_task(account_id=acc["id"], title="x", archetype="participatory", theme="фото")
    angles = store.recent_angles(days=7, account_id=acc["id"])
    assert {"archetype": "participatory", "theme": "фото"} in angles


def test_cycle_records_failure_when_planner_returns_no_posts(store):
    class Empty:
        def __init__(self, sp): pass
        def generate_response(self, ctx): return '{"thesis": "t", "posts": []}'
    run = StrategyCycle(store, agent_factory=lambda sp: Empty(sp)).run(trigger="manual")
    assert run["status"] == "failed"


# -- scheduler --------------------------------------------------------------
def test_settings_roundtrip_and_defaults(store):
    cfg = get_strategy_settings(store)
    assert cfg["enabled"] is False and cfg["hour"] == 9
    store.set_setting(S_ENABLED, "1"); store.set_setting(S_HOUR, "7")
    cfg = get_strategy_settings(store)
    assert cfg["enabled"] is True and cfg["hour"] == 7


def test_scheduler_skips_when_disabled(store):
    ran = []
    sched = StrategyScheduler(store, cycle_factory=lambda acc: _Rec(ran))
    assert sched.tick() is None and not ran


def test_scheduler_runs_once_per_day_when_due(store):
    ran = []
    store.set_setting(S_ENABLED, "1")
    store.set_setting(S_HOUR, "0")  # any hour qualifies
    sched = StrategyScheduler(store, cycle_factory=lambda acc: _Rec(ran))
    sched.tick()
    assert len(ran) == 1  # no accounts -> single global run
    # second tick same day is a no-op (claimed via last_date)
    sched.tick()
    assert len(ran) == 1
    assert store.get_setting(S_LAST_DATE)


def test_scheduler_runs_for_every_active_account(store):
    seen = []
    store.add_account(name="A")
    store.add_account(name="B")
    store.set_setting(S_ENABLED, "1")
    store.set_setting(S_HOUR, "0")

    def factory(acc):
        seen.append(acc)
        return _Rec([])

    StrategyScheduler(store, cycle_factory=factory).tick()
    assert len(seen) == 2  # one cycle per active account


def test_goal_is_injected_into_context(store):
    acc = store.add_account(name="A", persona="p")
    store.set_setting("strategy_goal_views", "20000")
    store.set_setting("strategy_goal_comments", "300")
    ctx = StrategyCycle(store, account_id=acc["id"], agent_factory=_factory).build_context(6, "Ukrainian")
    assert "20000 views and 300 comments" in ctx
    assert "GOAL" in ctx


def test_refresh_niche_feed_pulls_and_stores(store, monkeypatch):
    from mobile_e2e.web import feed_source
    from mobile_e2e.web import strategy as st

    def fake_search(kw, limit=10, **kw2):
        return [{"text": f"trending about {kw}", "author": "x", "likes": 3,
                 "replies": 9, "views": 0, "url": ""}]

    monkeypatch.setattr(feed_source, "search", fake_search)
    added = st.refresh_niche_feed(store, ["зрада", "секс"])
    assert added == 2
    assert len(store.list_feed_samples()) == 2


def test_refresh_metrics_noop_when_threads_unconfigured(store):
    # No creds/env in tests → refresh must be a safe no-op, not raise.
    from mobile_e2e.web import strategy as st
    acc = store.add_account(name="A")
    t = store.add_task(account_id=acc["id"], title="x")
    store.set_task_published(t["id"], "123")
    assert st.refresh_metrics(store) == 0


def test_cycle_run_still_works_with_refresh(store):
    # refresh_intel is best-effort and must not break a normal run.
    acc = store.add_account(name="A", persona="p")
    run = StrategyCycle(store, account_id=acc["id"], agent_factory=_factory).run(trigger="manual")
    assert run["status"] == "drafted" and run["tasks_created"] == 2


def test_goal_progress_sums_published_metrics(store):
    acc = store.add_account(name="A")
    t = store.add_task(account_id=acc["id"], title="x")
    store.set_task_published(t["id"], "1")
    store.set_task_metrics(t["id"], views=500, likes=10, replies=25)
    prog = store.goal_progress(account_id=acc["id"], days=30)
    assert prog["views"] == 500 and prog["replies"] == 25


class _Rec:
    def __init__(self, sink): self._sink = sink
    def run(self, **kw): self._sink.append(kw); return {"id": 1, "status": "drafted"}
