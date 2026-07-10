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


# -- follow-ups (auto-comment under our own posts, no spam) -------------------
class FakeFollowupAgent:
    def __init__(self, system_prompt):
        pass

    def generate_response(self, ctx):
        return "Іноді так хочеться, щоб хтось написав першим. А ти б написав? 😉"


def _published_post(store, account_id, text, pid, *, age_minutes=90, views=500):
    """Create a published post and backdate it so it is eligible for a follow-up."""
    from datetime import datetime, timedelta
    from mobile_e2e.web.store import _TZ

    task = store.add_task(account_id=account_id, kind="post", title=text[:40], payload=text)
    store.set_task_status(task["id"], "done")
    store.set_task_published(task["id"], pid)
    store.set_task_metrics(task["id"], views, 0, 0)
    old = (datetime.now(_TZ).replace(tzinfo=None)
           - timedelta(minutes=age_minutes)).isoformat(timespec="seconds")
    with store._lock, store._conn:
        store._conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (old, task["id"]))
    return task


def test_posts_needing_followup_respects_age_and_one_per_post(store):
    acc = store.add_account(name="A", persona="p")
    _published_post(store, acc["id"], "Стара тема про близькість", "1001", age_minutes=90)
    _published_post(store, acc["id"], "Свіжий пост", "1002", age_minutes=5)  # too new
    need = store.posts_needing_followup(min_age_minutes=30)
    ids = {p["published_id"] for p in need}
    assert "1001" in ids and "1002" not in ids   # aged in, too-new out
    # once a follow-up exists for a post, it drops out (one per post = no spam)
    store.add_warmup_action(account_id=acc["id"], kind="followup",
                            target_id="1001", target_text="x", draft="y")
    assert "1001" not in {p["published_id"] for p in store.posts_needing_followup(min_age_minutes=30)}


def test_draft_followups_queues_pending_and_publishes_as_reply(store):
    from mobile_e2e.web import warmup

    acc = store.add_account(name="A", persona="playful")
    _published_post(store, acc["id"], "Про те, чого не вистачає ввечері", "2001", age_minutes=90)
    res = warmup.draft_followups(store, per_run=6, agent_factory=lambda sp: FakeFollowupAgent(sp))
    assert res["drafted"] == 1
    pend = [a for a in store.list_warmup_actions(status="pending") if a["kind"] == "followup"]
    assert len(pend) == 1 and pend[0]["target_id"] == "2001" and "?" in pend[0]["draft"]
    # a follow-up publishes via the same reply path (kind allowed)
    assert pend[0]["kind"] == "followup"


def test_own_followups_excluded_from_comment_stats(store, monkeypatch):
    """Our own auto-replies must never inflate the reply/comment numbers."""
    from mobile_e2e.web import strategy, threads_client

    acc = store.add_account(name="A")
    task = _published_post(store, acc["id"], "Пост із живими коментами", "3001",
                           age_minutes=90, views=1000)
    # a follow-up we PUBLISHED on that post (status done → counts as ours)
    fa = store.add_warmup_action(account_id=acc["id"], kind="followup",
                                 target_id="3001", target_text="x", draft="y")
    store.set_warmup_status(fa["id"], "done", published_id="9001")
    assert store.own_followup_counts() == {"3001": 1}

    # Threads reports 8 replies on the post; 1 of them is ours → 7 real.
    monkeypatch.setattr(threads_client, "fetch_insights",
                        lambda pid, creds: {"views": 1000, "likes": 5, "replies": 8})
    strategy.refresh_metrics(store)
    fresh = store.get_task(task["id"])
    assert fresh["replies"] == 7   # 8 reported − 1 of ours = only real people


class FakeCommentReplyAgent:
    def __init__(self, system_prompt):
        pass

    def generate_response(self, ctx):
        return "О, а що тебе найбільше зачаровує у жінці? 😉"


def test_draft_comment_replies_reads_real_comments_and_skips_own_and_dupes(store, monkeypatch):
    from mobile_e2e.web import warmup, threads_client

    acc = store.add_account(name="A", handle="@testacc", persona="playful")
    _published_post(store, acc["id"], "Чого вам не вистачає ввечері?", "4001",
                    age_minutes=60, views=900)
    store.set_task_metrics(  # give it real replies so the scanner picks it up
        [t for t in store.published_tasks() if t["published_id"] == "4001"][0]["id"], 900, 0, 3)

    fake_replies = [
        {"id": "c1", "text": "Обіймів", "username": "maxim", "is_reply_owned_by_me": False},
        {"id": "c2", "text": "я сам", "username": "testacc"},           # us → skip
        {"id": "c3", "text": "hidden one", "username": "x", "hide_status": "HIDDEN"},  # skip
        {"id": "c4", "text": "теж самотньо", "username": "andrii", "is_reply_owned_by_me": False},
    ]
    monkeypatch.setattr(threads_client, "fetch_replies", lambda pid, creds: fake_replies)

    res = warmup.draft_comment_replies(
        store, agent_factory=lambda sp: FakeCommentReplyAgent(sp))
    assert res["drafted"] == 2                       # only c1 + c4 (real, non-own, visible)
    made = [a for a in store.list_warmup_actions(status="pending") if a["kind"] == "comment_reply"]
    assert {a["target_id"] for a in made} == {"c1", "c4"}
    assert all(a["target_url"] == "4001" for a in made)  # parent post kept for honest stats
    assert store.answered_comment_ids() == {"c1", "c4"}

    # a second pass answers nobody new (dedupe by comment id)
    assert warmup.draft_comment_replies(
        store, agent_factory=lambda sp: FakeCommentReplyAgent(sp))["drafted"] == 0

    # our published comment-reply is excluded from the parent post's comment stat
    store.set_warmup_status(made[0]["id"], "done", published_id="r1")
    assert store.own_reply_counts().get("4001") == 1


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


def test_warm_post_single_paste(store):
    acc = store.add_account(name="A", persona="playful")
    res = WarmupAgent(store, account_id=acc["id"], agent_factory=_factory).warm_post(
        "Оце хочеться познайомитись з кимось особливим", author="veroniksaam")
    assert res["reply"] == 1 and res["manual"] == 2
    reply = [a for a in store.list_warmup_actions(account_id=acc["id"]) if a["kind"] == "reply"][0]
    assert "?" in reply["draft"] and reply["target_author"] == "veroniksaam"


def test_warmup_run_noop_without_targets(store):
    acc = store.add_account(name="A")
    res = WarmupAgent(store, account_id=acc["id"], agent_factory=_factory).run([], replies=3)
    assert res["targets"] == 0 and res["replies"] == 0
