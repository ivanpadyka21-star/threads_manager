"""Tests for the Threads integration (status + publish gating).

No network and no pythreads import: the integration is designed to report its
configuration state and gate actions before any credentials exist.
"""

from unittest.mock import MagicMock

import pytest

from mobile_e2e.web import threads_client
from mobile_e2e.web.app import create_app
from mobile_e2e.web.jobs import JobManager
from mobile_e2e.web.store import Store


@pytest.fixture
def client():
    store = Store(":memory:")
    app = create_app(job_manager=MagicMock(spec=JobManager), store=store)
    app.config.update(TESTING=True)
    yield app.test_client(), store
    store.close()


def test_status_not_configured(monkeypatch):
    for k in threads_client.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    st = threads_client.status("does-not-exist.json")
    assert st["configured"] is False
    assert set(st["missing_env"]) == set(threads_client.REQUIRED_ENV)
    assert st["has_credentials"] is False


def test_publish_text_raises_when_unconfigured(monkeypatch):
    for k in threads_client.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(threads_client.ThreadsNotConfigured):
        threads_client.publish_text("hello", "nope.json")


def test_publish_text_rejects_empty():
    with pytest.raises(ValueError):
        threads_client.publish_text("   ")


def test_threads_status_route(client, monkeypatch):
    c, _ = client
    for k in threads_client.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    res = c.get("/api/threads/status")
    assert res.status_code == 200
    assert res.get_json()["configured"] is False


def test_publish_route_requires_approval(client):
    c, store = client
    acc = store.add_account(name="T")
    task = store.add_task(account_id=acc["id"], title="Hi", payload="Hello world")
    res = c.post(f"/api/tasks/{task['id']}/publish")  # still pending
    assert res.status_code == 409


def test_publish_route_reports_not_configured(client, monkeypatch):
    c, store = client
    for k in threads_client.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    acc = store.add_account(name="T")
    task = store.add_task(account_id=acc["id"], title="Hi", payload="Hello world")
    store.approve_task(task["id"])
    res = c.post(f"/api/tasks/{task['id']}/publish")
    assert res.status_code == 409
    assert res.get_json()["configured"] is False


def test_account_credentials_file_stored(client):
    c, store = client
    acc = store.add_account(name="T", credentials_file="creds/acc1.json")
    assert store.get_account(acc["id"])["credentials_file"] == "creds/acc1.json"


def test_parse_insights():
    raw = {"data": [
        {"name": "views", "values": [{"value": 1200}]},
        {"name": "likes", "values": [{"value": 34}]},
        {"name": "replies", "values": [{"value": 7}]},
        {"name": "reposts", "total_value": {"value": 2}},
    ]}
    m = threads_client._parse_insights(raw)
    assert m["views"] == 1200 and m["likes"] == 34 and m["replies"] == 7
    assert m["reposts"] == 2


def test_top_posts_route(client):
    c, store = client
    acc = store.add_account(name="A")
    t = store.add_task(account_id=acc["id"], title="hi", payload="hello")
    store.set_task_published(t["id"], "999")
    store.set_task_metrics(t["id"], views=500, likes=40, replies=6)
    res = c.get("/api/analytics/top-posts?by=views")
    assert res.status_code == 200
    body = res.get_json()
    assert body[0]["views"] == 500 and body[0]["likes"] == 40


def test_refresh_insights_route(client):
    from unittest.mock import patch
    c, store = client
    acc = store.add_account(name="A")
    t = store.add_task(account_id=acc["id"], title="hi")
    store.set_task_published(t["id"], "999")
    with patch("mobile_e2e.web.threads_client.fetch_insights",
               return_value={"views": 300, "likes": 25, "replies": 3, "reposts": 0, "quotes": 0}):
        res = c.post("/api/threads/refresh-insights")
    assert res.status_code == 200 and res.get_json()["updated"] == 1
    assert store.get_task(t["id"])["views"] == 300
