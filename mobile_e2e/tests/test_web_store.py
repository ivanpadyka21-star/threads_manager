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


def test_stats_and_per_account(store):
    acc = store.add_account(name="A")
    store.add_task(account_id=acc["id"], title="t")
    stats = store.stats()
    assert stats["accounts"] == 1
    assert stats["pending"] == 1
    per = store.per_account_stats()
    assert per[0]["total_tasks"] == 1
