"""Tests for the warm-up (engagement) branch."""

import pytest

from mobile_e2e.web.store import Store
from mobile_e2e.web.warmup import WarmupAgent


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


class FakeReplyAgent:
    def __init__(self, system_prompt):
        pass

    def generate_response(self, ctx):
        return "А для тебе що головніше — щоб доганяли чи щоб не тікали? 😉"


def _factory(sp):
    return FakeReplyAgent(sp)


def _seed_niche(store):
    # stored competitor samples act as the fallback target source
    store.add_feed_sample("Чоловіки, зраджували колись?", author="a", views=3000, likes=5, replies=40)
    store.add_feed_sample("Норм чи стрьомно робити перший крок?", author="b", views=800, likes=2, replies=9)
    store.add_feed_sample("Овуляція vs лютеїнова фаза", author="c", views=2000, likes=30, replies=3)


# -- store --------------------------------------------------------------------
def test_warmup_action_crud_and_dedupe(store):
    a = store.add_warmup_action(account_id=1, kind="reply", target_text="post X", draft="hi")
    assert a["id"] > 0 and a["status"] == "pending"
    # same target + kind is deduped
    assert store.add_warmup_action(account_id=1, kind="reply", target_text="post X", draft="hi2") is None
    # a like on the same target is a different kind -> allowed
    assert store.add_warmup_action(account_id=1, kind="like", target_text="post X") is not None
    store.set_warmup_status(a["id"], "done", published_id="99")
    assert store.get_warmup_action(a["id"])["status"] == "done"


# -- agent --------------------------------------------------------------------
def test_warmup_run_drafts_replies_and_hands_to_strategist(store):
    _seed_niche(store)
    acc = store.add_account(name="A", persona="playful sexologist")
    before = len(store.list_feed_samples())
    res = WarmupAgent(store, account_id=acc["id"], agent_factory=_factory).run(
        ["зрада", "секс"], replies=2, manual_targets=3)
    assert res["replies"] == 2                    # top-2 targets got reply drafts
    assert res["manual"] >= 2                     # like/follow queued
    replies = store.list_warmup_actions(status="pending", account_id=acc["id"])
    reply_drafts = [a for a in replies if a["kind"] == "reply"]
    assert reply_drafts and all("?" in a["draft"] for a in reply_drafts)  # hooky question


def test_warmup_hands_fresh_live_posts_to_strategist(store, monkeypatch):
    from mobile_e2e.web import feed_source
    acc = store.add_account(name="A")

    def fake_search(kw, limit=8, **kw2):
        return [{"text": f"fresh viral post about {kw}", "author": "z", "url": "",
                 "target_id": "", "likes": 2, "replies": 30, "views": 1000}]

    monkeypatch.setattr(feed_source, "search", fake_search)
    res = WarmupAgent(store, account_id=acc["id"], agent_factory=_factory).run(
        ["зрада"], replies=1, manual_targets=1)
    # a brand-new discovered post is pushed into feed intel for the strategist
    assert res["handed_to_strategist"] >= 1
    assert any(f["source"] == "warmup" for f in store.list_feed_samples())


def test_warmup_run_ranks_by_reply_pull(store):
    _seed_niche(store)
    acc = store.add_account(name="A")
    WarmupAgent(store, account_id=acc["id"], agent_factory=_factory).run(["x"], replies=1, manual_targets=1)
    top_reply = [a for a in store.list_warmup_actions(account_id=acc["id"]) if a["kind"] == "reply"][0]
    # highest reply-rate sample (40 replies / 3000 views) should be the reply target
    assert "зраджували" in top_reply["target_text"]


def test_warmup_run_noop_without_targets(store):
    acc = store.add_account(name="A")
    res = WarmupAgent(store, account_id=acc["id"], agent_factory=_factory).run([], replies=3)
    assert res["targets"] == 0 and res["replies"] == 0
