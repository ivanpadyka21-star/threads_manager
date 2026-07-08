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


def test_account_persona_stored(store):
    acc = store.add_account(name="A", persona="sexologist, first person, playful, 18+")
    assert store.get_account(acc["id"])["persona"] == "sexologist, first person, playful, 18+"
    store.update_account(acc["id"], persona="updated persona")
    assert store.get_account(acc["id"])["persona"] == "updated persona"


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


def test_ai_usage(store):
    store.record_event("ai.generate", "a")
    store.record_event("ai.generate", "b")
    store.record_event("ai.error", "boom")
    u = store.ai_usage(rpm=5, rpd=20)
    assert u["used_today"] == 2
    assert u["remaining_today"] == 18
    assert u["used_minute"] == 2
    assert u["remaining_minute"] == 3
    assert u["errors_today"] == 1


def test_ai_stats(store):
    store.record_event("ai.generate", "q")
    store.record_event("ai.error", "fail")
    ai = store.ai_stats(hours=24)
    assert ai["generations"] == 1 and ai["errors"] == 1
    assert ai["success_rate"] == 50.0


def test_add_batch_creates_scheduled_tasks(store):
    acc = store.add_account(name="A")
    batch = store.add_batch(
        account_id=acc["id"], briefs=["Post 1", "Post 2", "Post 3"],
        language="English", style="friendly", interval_minutes=30,
    )
    assert len(batch["tasks"]) == 3
    tasks = store.list_batch(batch["batch_id"])
    assert all(t["status"] == "pending" for t in tasks)
    assert all(t["batch_id"] == batch["batch_id"] for t in tasks)
    # spaced 30 min apart, in order
    times = [t["scheduled_for"] for t in tasks]
    assert times == sorted(times) and all(times)


def test_add_batch_caps_at_10(store):
    acc = store.add_account(name="A")
    batch = store.add_batch(account_id=acc["id"], briefs=[f"P{i}" for i in range(15)])
    assert len(batch["tasks"]) == 10


def test_add_batch_requires_briefs(store):
    with pytest.raises(ValueError):
        store.add_batch(account_id=None, briefs=["  ", ""])


def test_approve_batch_and_due(store):
    acc = store.add_account(name="A")
    batch = store.add_batch(
        account_id=acc["id"], briefs=["a", "b"], interval_minutes=1,
        start_at="2000-01-01T00:00",  # in the past -> due
    )
    n = store.approve_batch(batch["batch_id"])
    assert n == 2
    due = store.due_scheduled_tasks()
    assert len(due) == 2
    assert all(t["status"] == "scheduled" for t in due)


def test_saved_prompts(store):
    p = store.add_prompt("Weekly plan", "7 posts about X")
    assert p["id"] > 0 and p["name"] == "Weekly plan"
    assert len(store.list_prompts()) == 1
    store.delete_prompt(p["id"])
    assert store.list_prompts() == []


def test_prompt_requires_name_and_text(store):
    with pytest.raises(ValueError):
        store.add_prompt("", "text")
    with pytest.raises(ValueError):
        store.add_prompt("name", "  ")


def test_update_task_fields(store):
    acc = store.add_account(name="A")
    task = store.add_task(account_id=acc["id"], title="x", payload="old")
    updated = store.update_task(task["id"], result="edited draft", max_chars=200)
    assert updated["result"] == "edited draft"
    assert updated["max_chars"] == 200
    # unknown fields are ignored
    store.update_task(task["id"], bogus="nope")
    assert store.get_task(task["id"])["result"] == "edited draft"


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


def test_backfill_published_ids(store):
    acc = store.add_account(name="A")
    t = store.add_task(account_id=acc["id"], title="x")
    store.set_task_result(t["id"], "[published 12345] hello world")
    store.set_task_status(t["id"], "done")
    assert store.backfill_published_ids() == 1
    assert store.get_task(t["id"])["published_id"] == "12345"


def test_task_metrics_and_top_posts(store):
    acc = store.add_account(name="A")
    t1 = store.add_task(account_id=acc["id"], title="p1")
    t2 = store.add_task(account_id=acc["id"], title="p2")
    store.set_task_published(t1["id"], "111")
    store.set_task_published(t2["id"], "222")
    store.set_task_metrics(t1["id"], views=50, likes=5, replies=1)
    store.set_task_metrics(t2["id"], views=200, likes=20, replies=4)
    assert len(store.published_tasks()) == 2
    top = store.top_posts(by="views", limit=10)
    assert [t["id"] for t in top[:2]] == [t2["id"], t1["id"]]
    top_likes = store.top_posts(by="likes", limit=1)
    assert top_likes[0]["id"] == t2["id"]


def test_content_insights_learns_patterns(store):
    acc = store.add_account(name="A")
    # A big winner that asks a direct question, plus a weak greeting-opener.
    win = store.add_task(account_id=acc["id"], title="w",
                         payload="Чого вам не вистачає найбільше? 😉")
    store.set_task_result(win["id"], "Чого вам не вистачає найбільше? 😉")
    store.set_task_published(win["id"], "111")
    store.set_task_metrics(win["id"], views=4000, likes=240, replies=74)

    weak = store.add_task(account_id=acc["id"], title="g")
    store.set_task_result(weak["id"], "Привіт усім, гарного дня")
    store.set_task_published(weak["id"], "222")
    store.set_task_metrics(weak["id"], views=100, likes=1, replies=0)

    ins = store.content_insights()
    assert ins["sample_count"] == 2
    assert ins["top"][0]["views"] == 4000  # winner ranked first
    q = ins["patterns"]["question"]
    assert q["with_avg_views"] > q["without_avg_views"]  # questions win here
    g = ins["patterns"]["greeting"]
    assert g["without_avg_views"] > g["with_avg_views"]  # greeting underperforms
    assert "REPLY CHAINS" in ins["text"]
    # reply-rate = replies per 1000 views; the winner drives the most replies
    assert ins["top"][0]["reply_rate"] == round(74 / 4000 * 1000, 1)
    assert ins["reply_drivers"][0]["replies"] == 74
    assert ins["patterns"]["avg_reply_rate"] is not None


def test_content_insights_empty_without_metrics(store):
    ins = store.content_insights()
    assert ins["sample_count"] == 0
    assert ins["top"] == []
    assert "No published posts" in ins["text"]


def test_feed_samples_add_dedupe_and_delete(store):
    row = store.add_feed_sample("Мужчины, изменяли ли вы?", author="magiclisaa",
                                views=227000, likes=252, replies=227)
    assert row["id"] > 0 and row["author"] == "magiclisaa"
    # identical text is deduped
    assert store.add_feed_sample("Мужчины, изменяли ли вы?") is None
    assert len(store.list_feed_samples()) == 1
    store.delete_feed_sample(row["id"])
    assert store.list_feed_samples() == []


def test_feed_sample_requires_text(store):
    with pytest.raises(ValueError):
        store.add_feed_sample("   ")


def test_feed_insights_ranks_by_reply_pull(store):
    # High reach but few replies vs lower reach with a strong reply chain.
    store.add_feed_sample("big reach few replies", views=100000, likes=10, replies=5)
    store.add_feed_sample("reply chain monster", views=10000, likes=50, replies=400)
    ins = store.feed_insights()
    assert ins["sample_count"] == 2
    assert ins["by_reach"][0]["views"] == 100000            # reach ranking
    assert ins["by_replies"][0]["text"] == "reply chain monster"  # reply-pull ranking
    assert "TREND INTELLIGENCE" in ins["text"]


def test_feed_insights_empty(store):
    ins = store.feed_insights()
    assert ins["sample_count"] == 0
    assert "No competitor" in ins["text"]


def test_task_max_chars_stored(store):
    acc = store.add_account(name="A")
    task = store.add_task(account_id=acc["id"], title="x", max_chars=150)
    assert store.get_task(task["id"])["max_chars"] == 150
    # empty/absent -> None
    t2 = store.add_task(account_id=acc["id"], title="y")
    assert store.get_task(t2["id"])["max_chars"] is None


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
