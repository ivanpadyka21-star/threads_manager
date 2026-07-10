"""Tests for the account KPI layer (growth snapshots + aggregation)."""

import json

import pytest

from mobile_e2e.web.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_snapshot_upsert_and_kpi_aggregate(store):
    a = store.add_account(name="A", handle="@a")
    b = store.add_account(name="B", handle="@b")
    store.snapshot_account_insight(a["id"], followers=40, profile_views=1000,
                                   likes=10, replies=5, clicks=3, reposts=1, quotes=0)
    store.snapshot_account_insight(b["id"], followers=120, profile_views=500, likes=2)

    # a second snapshot the same day just updates (upsert, not duplicate)
    store.snapshot_account_insight(a["id"], followers=45, profile_views=1200,
                                   likes=12, replies=6, clicks=4)

    k = store.account_kpi()
    assert k["totals"]["followers"] == 45 + 120
    assert k["totals"]["profile_views"] == 1200 + 500
    assert k["totals"]["clicks"] == 4
    assert k["engagement"] == (12 + 6 + 0 + 0) + (2 + 0 + 0 + 0)

    accs = {x["account"]: x for x in k["accounts"]}
    assert accs["@b"]["followers"] == 120 and accs["@b"]["demo_locked"] is False
    assert accs["@a"]["demo_locked"] is True and accs["@a"]["demo_needed"] == 55
    # sorted by followers desc → @b first
    assert k["accounts"][0]["account"] == "@b"


def test_strategist_self_evolves_lessons(store):
    """The strategist distils a learned playbook from real results into S_LESSONS."""
    from mobile_e2e.web import strategy as st

    # some published posts (winners) to learn from
    for i, v in enumerate([1200, 90]):
        t = store.add_task(account_id=None, kind="post", title=f"p{i}", payload=f"post {i}")
        store.set_task_status(t["id"], "done")
        store.set_task_published(t["id"], str(1000 + i))
        store.set_task_metrics(t["id"], v, 0, 0)

    class FakeStrategist:
        def __init__(self, sp):
            pass

        def generate_response(self, ctx):
            return "1. Целься в мужчин.\n2. Бинарный выбор с риском для эго."

    lessons = st.evolve_strategist(store, agent_factory=lambda sp: FakeStrategist(sp))
    assert "мужчин" in lessons
    assert store.get_setting(st.S_LESSONS) == lessons
    assert store.setting_age_seconds(st.S_LESSONS_AT) is not None
    # lessons flow into the planning context (under, not over, the owner rules)
    ctx = st.StrategyCycle(store).build_context(count=3, language="Ukrainian")
    assert "LEARNED LESSONS" in ctx and "Бинарный выбор" in ctx


def test_demographics_from_meta(store):
    a = store.add_account(name="A", handle="@a")
    store.snapshot_account_insight(a["id"], followers=150)
    store.set_setting(f"demographics:{a['id']}", json.dumps({"M": 80, "F": 20}))
    k = store.account_kpi()
    demo = k["accounts"][0]["demographics"]
    assert demo == {"M": 80, "F": 20} and k["accounts"][0]["demo_locked"] is False
