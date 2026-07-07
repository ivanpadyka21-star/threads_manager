"""Tests for the background PostScheduler and batch routes."""

import time
from unittest.mock import MagicMock, patch

import pytest

from mobile_e2e.web import threads_client
from mobile_e2e.web.app import create_app
from mobile_e2e.web.jobs import JobManager
from mobile_e2e.web.scheduler import PostScheduler
from mobile_e2e.web.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def _due_scheduled(store, account_id):
    batch = store.add_batch(
        account_id=account_id, briefs=["hello"], start_at="2000-01-01T00:00"
    )
    store.approve_batch(batch["batch_id"])
    return store.list_batch(batch["batch_id"])[0]


def test_tick_publishes_due(store):
    acc = store.add_account(name="A", daily_limit=10)
    task = _due_scheduled(store, acc["id"])
    with patch("mobile_e2e.web.scheduler.publish_task", return_value="pid123") as pub:
        n = PostScheduler(store).tick()
    assert n == 1
    pub.assert_called_once_with(store, task["id"])


def test_tick_respects_daily_limit(store):
    acc = store.add_account(name="A", daily_limit=1)
    # consume the daily limit
    t = store.add_task(account_id=acc["id"], title="x")
    store.approve_task(t["id"])
    _due_scheduled(store, acc["id"])
    with patch("mobile_e2e.web.scheduler.publish_task") as pub:
        n = PostScheduler(store).tick()
    assert n == 0
    pub.assert_not_called()


def test_claim_scheduled_task_is_once_only(store):
    acc = store.add_account(name="A")
    task = _due_scheduled(store, acc["id"])
    assert store.claim_scheduled_task(task["id"]) is True
    assert store.claim_scheduled_task(task["id"]) is False  # already claimed
    assert store.get_task(task["id"])["status"] == "publishing"


def test_tick_does_not_double_publish(store):
    acc = store.add_account(name="A", daily_limit=10)
    _due_scheduled(store, acc["id"])
    with patch("mobile_e2e.web.scheduler.publish_task", return_value="pid") as pub:
        s = PostScheduler(store)
        s.tick()
        s.tick()  # a second tick (or a duplicate scheduler) must not re-publish
    assert pub.call_count == 1


def test_tick_flags_when_not_configured(store):
    acc = store.add_account(name="A", daily_limit=10)
    task = _due_scheduled(store, acc["id"])
    with patch(
        "mobile_e2e.web.scheduler.publish_task",
        side_effect=threads_client.ThreadsNotConfigured("no creds"),
    ):
        PostScheduler(store).tick()
    assert store.get_task(task["id"])["status"] == "failed"


# -- batch routes -----------------------------------------------------------
@pytest.fixture
def client():
    store = Store(":memory:")
    app = create_app(job_manager=MagicMock(spec=JobManager), store=store)
    app.config.update(TESTING=True)
    yield app.test_client(), store
    store.close()


def _create_batch_and_wait(c, body, fake):
    """Create a batch and wait for background draft generation (patch stays on)."""
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = c.post("/api/batches", json=body)
        assert res.status_code == 202
        bid = res.get_json()["batch_id"]
        for _ in range(300):
            b = c.get(f"/api/batches/{bid}").get_json()
            if not b["generating"]:
                return bid, b
            time.sleep(0.01)
        return bid, b


def test_create_batch_generates_drafts(client):
    c, store = client
    acc = store.add_account(name="A")
    fake = MagicMock()
    fake.generate_response.side_effect = ["draft one", "draft two"]
    _, b = _create_batch_and_wait(c, {
        "account_id": acc["id"], "briefs": ["t1", "t2"],
        "interval_minutes": 15, "language": "English",
    }, fake)
    tasks = b["tasks"]
    assert len(tasks) == 2
    assert {t["result"] for t in tasks} == {"draft one", "draft two"}
    assert all(t["status"] == "pending" for t in tasks)


def test_create_batch_passes_max_chars(client):
    c, store = client
    acc = store.add_account(name="A")
    fake = MagicMock(); fake.generate_response.return_value = "d"
    _, b = _create_batch_and_wait(c, {"account_id": acc["id"], "briefs": ["x"], "max_chars": 150}, fake)
    assert b["tasks"][0]["max_chars"] == 150


def test_create_batch_from_topic_count(client):
    c, store = client
    acc = store.add_account(name="A")
    fake = MagicMock(); fake.generate_response.return_value = "d"
    _, b = _create_batch_and_wait(c, {"account_id": acc["id"], "topic": "love", "count": 4}, fake)
    assert len(b["tasks"]) == 4


def test_create_batch_requires_input(client):
    c, _ = client
    assert c.post("/api/batches", json={}).status_code == 400


def test_approve_batch_route(client):
    c, store = client
    acc = store.add_account(name="A")
    fake = MagicMock(); fake.generate_response.return_value = "d"
    bid, _ = _create_batch_and_wait(c, {"account_id": acc["id"], "briefs": ["a", "b", "c"]}, fake)
    res = c.post(f"/api/batches/{bid}/approve")
    assert res.status_code == 200 and res.get_json()["scheduled"] == 3
    assert all(t["status"] == "scheduled" for t in c.get(f"/api/batches/{bid}").get_json()["tasks"])
