"""Tests for the SQLite dashboard store (in-memory)."""

import pytest

from mobile_e2e.web.store import RateLimitError, Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_add_and_list_account(store):
    acc = store.add_account(name="Client A", handle="@a", daily_limit=5)
    assert acc["id"] > 0
    assert acc["name"] == "Client A"
    assert acc["remaining_today"] == 5
    assert len(store.list_accounts()) == 1


def test_account_name_required(store):
    with pytest.raises(ValueError):
        store.add_account(name="   ")


def test_update_and_delete_account(store):
    acc = store.add_account(name="X")
    store.update_account(acc["id"], daily_limit=99, tone="formal")
    updated = store.get_account(acc["id"])
    assert updated["daily_limit"] == 99
    assert updated["tone"] == "formal"
    store.delete_account(acc["id"])
    assert store.get_account(acc["id"]) is None


def test_task_lifecycle(store):
    acc = store.add_account(name="A", daily_limit=10)
    task = store.add_task(account_id=acc["id"], kind="post", title="Hello", payload="hi")
    assert task["status"] == "pending"

    approved = store.approve_task(task["id"])
    assert approved["status"] == "approved"

    done = store.set_task_status(task["id"], "done")
    assert done["status"] == "done"


def test_approve_missing_task_raises(store):
    with pytest.raises(KeyError):
        store.approve_task(999)


def test_daily_limit_enforced(store):
    acc = store.add_account(name="A", daily_limit=2)
    ids = [store.add_task(account_id=acc["id"], title=f"t{i}")["id"] for i in range(3)]
    store.approve_task(ids[0])
    store.approve_task(ids[1])
    # Third approval exceeds the daily limit of 2.
    with pytest.raises(RateLimitError):
        store.approve_task(ids[2])
    # Usage is reflected on the account.
    assert store.get_account(acc["id"])["used_today"] == 2
    assert store.get_account(acc["id"])["remaining_today"] == 0


def test_list_tasks_filters(store):
    acc = store.add_account(name="A")
    t1 = store.add_task(account_id=acc["id"], title="one")
    store.add_task(account_id=acc["id"], title="two")
    store.approve_task(t1["id"])
    assert len(store.list_tasks(status="pending")) == 1
    assert len(store.list_tasks(status="approved")) == 1
    assert len(store.list_tasks(account_id=acc["id"])) == 2


def test_audit_records_actions(store):
    acc = store.add_account(name="A")
    store.add_task(account_id=acc["id"], title="t")
    actions = [a["action"] for a in store.list_audit()]
    assert "account.create" in actions
    assert "task.create" in actions


def test_reminder_stored_and_listed(store):
    acc = store.add_account(name="A")
    store.add_task(account_id=acc["id"], title="with reminder", reminder="2026-07-10T09:00")
    store.add_task(account_id=acc["id"], title="no reminder")
    reminders = store.list_reminders()
    assert len(reminders) == 1
    assert reminders[0]["title"] == "with reminder"
    assert reminders[0]["account_name"] == "A"
    assert reminders[0]["status"] == "pending"


def test_reminder_dropped_when_task_finished(store):
    acc = store.add_account(name="A")
    task = store.add_task(account_id=acc["id"], title="r", reminder="2026-07-10T09:00")
    assert len(store.list_reminders()) == 1
    store.set_task_status(task["id"], "done")
    # Finished tasks no longer surface as active reminders.
    assert store.list_reminders() == []


def test_stats_and_per_account(store):
    acc = store.add_account(name="A")
    store.add_task(account_id=acc["id"], title="t")
    stats = store.stats()
    assert stats["accounts"] == 1
    assert stats["pending"] == 1
    per = store.per_account_stats()
    assert per[0]["total_tasks"] == 1


# -- analytics --------------------------------------------------------------
def test_effectiveness_score(store):
    acc = store.add_account(name="A", daily_limit=99)
    t1 = store.add_task(account_id=acc["id"], title="a")
    store.approve_task(t1["id"])          # ok
    store.record_event("run.error", "boom", acc["id"])  # error (level from map)
    eff = store.effectiveness(hours=24)
    # There are ok/info events and one error -> a score strictly between 0 and 100.
    assert eff["score"] is not None
    assert 0 < eff["score"] < 100
    assert eff["problems"] == 1
    assert len(eff["recent_errors"]) == 1


def test_effectiveness_none_without_activity(store):
    assert store.effectiveness(hours=24)["score"] is None


def test_activity_daily_buckets(store):
    acc = store.add_account(name="A")
    store.add_task(account_id=acc["id"], title="x")
    buckets = store.activity_daily(account_id=acc["id"], days=14)
    assert len(buckets) == 14
    assert buckets[-1]["total"] >= 1  # today has activity


def test_accounts_effectiveness_state(store):
    acc = store.add_account(name="A")  # account.create logs an event -> active
    rows = store.accounts_effectiveness(hours=24)
    assert rows[0]["state"] == "active"
    assert isinstance(rows[0]["sparkline"], list)


def test_ai_stats(store):
    store.record_event("ai.generate", "q")
    store.record_event("ai.error", "fail")
    ai = store.ai_stats(hours=24)
    assert ai["generations"] == 1 and ai["errors"] == 1
    assert ai["success_rate"] == 50.0


def test_notifications(store):
    acc = store.add_account(name="A")
    # a past reminder (due) + a recorded problem
    store.add_task(account_id=acc["id"], title="Ping", reminder="2000-01-01T00:00")
    store.record_event("run.error", "boom", acc["id"])
    notif = store.notifications()
    types = {i["type"] for i in notif["items"]}
    assert "reminder" in types and "problem" in types
    assert notif["count"] >= 2  # due reminder + problem both count


def test_effectiveness_trend(store):
    acc = store.add_account(name="A")
    task = store.add_task(account_id=acc["id"], title="x")
    store.approve_task(task["id"])
    trend = store.effectiveness_trend(days=30)
    assert len(trend) == 30
    # today should have a computed score from the events above
    assert trend[-1]["score"] is not None
    # a day with no events has score None
    assert trend[0]["score"] is None


def test_task_structured_fields_stored(store):
    acc = store.add_account(name="A")
    task = store.add_task(
        account_id=acc["id"], kind="comment", title="c",
        language="Ukrainian", style="witty", target="http://x/post/1",
    )
    got = store.get_task(task["id"])
    assert got["language"] == "Ukrainian"
    assert got["style"] == "witty"
    assert got["target"] == "http://x/post/1"


def test_task_result_stored(store):
    acc = store.add_account(name="A")
    task = store.add_task(account_id=acc["id"], title="x")
    store.set_task_result(task["id"], "generated draft")
    assert store.get_task(task["id"])["result"] == "generated draft"


def test_analytics_overview_and_summary(store):
    acc = store.add_account(name="Alpha")
    store.add_task(account_id=acc["id"], title="x")
    overview = store.analytics_overview(hours=24)
    assert "project" in overview and "accounts" in overview
    text = store.analytics_summary_text([acc["id"]], hours=24)
    assert "Alpha" in text and "effectiveness" in text.lower()
