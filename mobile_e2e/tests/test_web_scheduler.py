"""Tests for the background PostScheduler and batch routes."""

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


def test_create_batch_generates_drafts(client):
    c, store = client
    acc = store.add_account(name="A")
    fake = MagicMock()
    fake.generate_response.side_effect = ["draft one", "draft two"]
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = c.post("/api/batches", json={
            "account_id": acc["id"], "briefs": ["t1", "t2"],
            "interval_minutes": 15, "language": "English",
        })
    assert res.status_code == 201
    tasks = res.get_json()["tasks"]
    assert len(tasks) == 2
    assert {t["result"] for t in tasks} == {"draft one", "draft two"}
    assert all(t["status"] == "pending" for t in tasks)


def test_create_batch_from_topic_count(client):
    c, store = client
    acc = store.add_account(name="A")
    fake = MagicMock(); fake.generate_response.return_value = "d"
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        res = c.post("/api/batches", json={"account_id": acc["id"], "topic": "love", "count": 4})
    assert res.status_code == 201
    assert len(res.get_json()["tasks"]) == 4


def test_create_batch_requires_input(client):
    c, _ = client
    assert c.post("/api/batches", json={}).status_code == 400


def test_approve_batch_route(client):
    c, store = client
    acc = store.add_account(name="A")
    fake = MagicMock(); fake.generate_response.return_value = "d"
    with patch("mobile_e2e.web.app.AIAgent", return_value=fake):
        batch = c.post("/api/batches", json={"account_id": acc["id"], "briefs": ["a", "b", "c"]}).get_json()
    res = c.post(f"/api/batches/{batch['batch_id']}/approve")
    assert res.status_code == 200 and res.get_json()["scheduled"] == 3
    # tasks now scheduled
    assert all(t["status"] == "scheduled" for t in c.get(f"/api/batches/{batch['batch_id']}").get_json())
