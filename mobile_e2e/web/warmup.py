"""The warm-up branch — grows accounts by smart, small engagement.

A cold account with no followers gets ~0 reach no matter how good the content,
so it must be *warmed up*: engage, on our themes, where our audience already is.
This agent does that in a compliant, low-volume, quality-first way — and works
in a PAIR with the strategist.

What it does per run (small on purpose):
  1. DISCOVER — find fresh niche posts (live keyword search, else the competitor
     samples we already hold), rank them by reply-pull / reach.
  2. DRAFT REPLIES — for the top targets, write an ON-TOPIC reply that itself
     carries a hook so the author/others want to reply back to US (a mini reply
     chain), in the account's own voice. Queued for human approval.
  3. MANUAL CHECKLIST — like/follow the same authors: the official API does not
     expose likes/follows and automating them risks a ban, so these are queued
     as human tasks (a couple of minutes/day), not automated.
  4. HAND OFF TO STRATEGIST — the best discovered posts are pushed into the feed
     intel so the strategy cycle can ride the same winning angle with our content.

Only replies may (best-effort) publish via API on approval; likes/follows stay
manual. Everything is deliberately small: quality over spam, no abuse blocks.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from mobile_e2e.ai.brain import make_agent
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web import feed_source

LOG = get_logger(__name__)

REPLY_SYSTEM = (
    "You write a single short comment (a REPLY) to someone else's post in the "
    "relationships/sexuality niche, from an SMM creator's first-person voice. "
    "GOAL of the reply: be strictly ON-TOPIC to their post AND spark a reply BACK "
    "to you — add a genuine thought or a spicy-but-tasteful angle, then end with a "
    "short hook or question that invites them (or others) to answer. Warm, playful, "
    "confident; never generic ('cool', 'agree'), never spammy, no links, no "
    "self-promo, no @mentions. Keep it under 240 characters. Output only the reply."
)


FOLLOWUP_SYSTEM = (
    "You are the account owner writing ONE follow-up comment UNDER YOUR OWN post, "
    "a while after it went live, to DEVELOP THE THEME and pull more men into the "
    "thread. This is not a new post and not 'thanks for the comments' filler — it "
    "adds a fresh angle, a small confession, or a sharper turn of the same idea, "
    "then ends with one easy question a man can answer in a second.\n"
    "DNA: the real engine is EMOTION, not comments. Primary audience is MEN — "
    "write as a real woman who lacks male attention, closeness and sex: simple, "
    "warm, a little vulnerable, unashamed, never crude or pornographic, at most one "
    "emoji. Make him feel there's a real woman on the other side he could be the "
    "one for. Deepen the ORIGINAL post's emotion — do not repeat its wording. "
    "One or two sentences, under 200 characters. Output ONLY the comment text."
)


def draft_followups(store, account_id: Optional[int] = None, *,
                    min_age_minutes: int = 30, per_run: int = 6,
                    agent_factory: Optional[Callable[[str], object]] = None,
                    rules: Optional[str] = None) -> dict:
    """Auto-draft ONE thoughtful follow-up per eligible own post (no spam).

    Drafts only — never publishes. Each draft is queued as a ``followup``
    warm-up action for human approval, so nothing reaches an account without
    the owner's OK. Anti-spam is enforced by ``posts_needing_followup`` (one per
    post, aged, recent) plus the ``per_run`` cap.
    """
    from mobile_e2e.web import strategy as _strategy

    factory = agent_factory or (lambda sp: make_agent(sp, role="writer"))
    if rules is None:
        try:
            rules = store.get_setting(_strategy.S_RULES, "")
        except Exception:  # noqa: BLE001
            rules = ""
    posts = store.posts_needing_followup(
        account_id=account_id, min_age_minutes=min_age_minutes, limit=per_run)
    drafted = 0
    for p in posts:
        text = (p.get("payload") or p.get("title") or "").strip()
        pid = p.get("published_id") or ""
        if not text or not pid:
            continue
        acc = store.get_account(p.get("account_id")) if p.get("account_id") else None
        handle = (acc or {}).get("handle", "")
        persona = (acc or {}).get("persona", "")
        prompt = "\n\n".join(x for x in [
            f"OWNER RULES (highest priority): {rules}" if rules else "",
            f"Your voice / persona: {persona}" if persona else "",
            f"YOUR original post (a while ago):\n{text}",
            "Write your single follow-up comment that develops this theme and "
            "invites men to answer.",
        ] if x)
        try:
            draft = factory(FOLLOWUP_SYSTEM).generate_response(prompt).strip()
        except Exception as exc:  # noqa: BLE001 - degrade; skip this post
            LOG.warning("followup draft failed: %s", exc)
            continue
        if draft and store.add_warmup_action(
                account_id=p.get("account_id"), kind="followup",
                target_author=handle, target_text=text, target_url="",
                target_id=pid, draft=draft):
            drafted += 1
    if drafted:
        store.log("followup.draft", f"{drafted} follow-ups", account_id)
    return {"candidates": len(posts), "drafted": drafted}


class WarmupAgent:
    """Discovers niche targets and drafts engaging, on-topic replies."""

    def __init__(self, store, account_id: Optional[int] = None,
                 agent_factory: Optional[Callable[[str], object]] = None):
        self._store = store
        self._account_id = account_id
        # Warm-up replies are bulk text → writer role (Gemini, GPT fallback).
        self._agent_factory = agent_factory or (lambda sp: make_agent(sp, role="writer"))

    def _own_authors(self) -> set:
        """Our own account handles — never warm up (reply/like) our own posts."""
        out = set()
        for a in self._store.list_accounts():
            h = (a.get("handle") or "").strip().lstrip("@").lower()
            if h:
                out.add(h)
        return out

    # -- discovery ----------------------------------------------------------
    def _targets(self, keywords: List[str], limit: int) -> List[dict]:
        """Fresh niche posts to engage. Prefers live search (real posts with a
        media id → auto-reply); falls back to stored competitor samples. Never
        targets our own accounts."""
        own = self._own_authors()
        live: List[dict] = []
        for kw in keywords:
            try:
                live.extend(feed_source.search(kw, limit=8))
            except Exception:  # noqa: BLE001 - unavailable → fall back below
                continue
        # Fall back to stored niche samples when live search is thin.
        fallback = [
            {"text": p["text"], "author": p.get("author", ""), "url": "",
             "target_id": "", "replies": p.get("replies", 0), "views": p.get("views", 0)}
            for p in (self._store.feed_insights().get("by_replies") or [])
        ]

        def _own(p):
            return (p.get("author") or "").strip().lstrip("@").lower() in own

        def _rank(p):
            v = p.get("views") or 0
            # Live posts (have a media id → auto-reply) rank above stored ones.
            boost = 1000 if (p.get("target_id") or p.get("id")) else 0
            return boost + ((p.get("replies") or 0) / v if v else (p.get("replies") or 0))

        seen, uniq = set(), []
        for p in sorted(live + fallback, key=_rank, reverse=True):
            if _own(p):
                continue  # never engage our own accounts
            key = (p.get("text") or "").strip()[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            uniq.append(p)
        return uniq[:limit]

    # -- run ----------------------------------------------------------------
    def run(self, keywords: List[str], *, replies: int = 3,
            manual_targets: int = 8, persona: str = "") -> dict:
        """One small warm-up pass. Returns counts of what was queued."""
        keywords = [k.strip() for k in keywords if k and k.strip()]
        targets = self._targets(keywords, max(replies, manual_targets))
        if not targets:
            return {"targets": 0, "replies": 0, "manual": 0, "handed_to_strategist": 0}

        persona = persona or (
            (self._store.get_account(self._account_id) or {}).get("persona", "")
            if self._account_id else "")

        drafted = manual = handed = 0
        for i, tgt in enumerate(targets):
            author = tgt.get("author", "")
            text = (tgt.get("text") or "").strip()
            url = tgt.get("url", "")
            tid = tgt.get("target_id") or tgt.get("id") or ""

            # 1) reply drafts for the top `replies` targets
            if i < replies:
                draft = self._draft_reply(text, persona)
                if draft and self._store.add_warmup_action(
                    account_id=self._account_id, kind="reply", target_author=author,
                    target_text=text, target_url=url, target_id=tid, draft=draft):
                    drafted += 1

            # 2) like/follow manual checklist for these authors
            for kind in ("like", "follow"):
                if self._store.add_warmup_action(
                        account_id=self._account_id, kind=kind, target_author=author,
                        target_text=text, target_url=url, target_id=tid):
                    manual += 1

            # 3) hand the winning post to the strategist (feed intel)
            if self._store.add_feed_sample(
                    text, author=author, likes=tgt.get("likes", 0),
                    replies=tgt.get("replies", 0), views=tgt.get("views", 0),
                    url=url, topic="warmup", source="warmup"):
                handed += 1

        self._store.log("warmup.run", f"{drafted} replies, {manual} manual, {handed} to strategist",
                        self._account_id)
        return {"targets": len(targets), "replies": drafted, "manual": manual,
                "handed_to_strategist": handed}

    def warm_post(self, text: str, *, author: str = "", url: str = "",
                  target_id: str = "", persona: str = "") -> dict:
        """Warm up a SINGLE pasted post (the hybrid path — the user sees the app
        feed, the API can't). Drafts a hooky reply and queues like/follow. The
        reply action is created even if drafting fails (rate limit) so she can
        write it by hand."""
        text = (text or "").strip()
        if not text:
            raise ValueError("post text is required")
        persona = persona or (
            (self._store.get_account(self._account_id) or {}).get("persona", "")
            if self._account_id else "")
        draft = self._draft_reply(text, persona)
        created = {"reply": 0, "manual": 0}
        if self._store.add_warmup_action(
                account_id=self._account_id, kind="reply", target_author=author,
                target_text=text, target_url=url, target_id=target_id, draft=draft):
            created["reply"] = 1
        for kind in ("like", "follow"):
            if self._store.add_warmup_action(
                    account_id=self._account_id, kind=kind, target_author=author,
                    target_text=text, target_url=url, target_id=target_id):
                created["manual"] += 1
        # also feed it to the strategist
        self._store.add_feed_sample(text, author=author, url=url, topic="warmup", source="warmup")
        return {**created, "draft": draft}

    def _draft_reply(self, target_text: str, persona: str) -> str:
        prompt = (
            f"Persona (your voice): {persona or '(playful SMM creator, 18+ niche)'}\n\n"
            f"Their post:\n{target_text}\n\n"
            "Write your on-topic reply that makes them want to answer you back."
        )
        try:
            return self._agent_factory(REPLY_SYSTEM).generate_response(prompt).strip()
        except Exception as exc:  # noqa: BLE001 - degrade; skip this target
            LOG.warning("warmup reply draft failed: %s", exc)
            return ""


def autopublish_followups(store, max_per_pass: int = 3) -> dict:
    """Gently publish a few approved follow-ups (owner opted in via a setting).

    Deliberately small and spread — at most ``max_per_pass`` per call and never
    two for the same account in one pass — so it never trips Meta's abuse guard.
    Best-effort: a failure on one follow-up is skipped, not fatal.
    """
    published, used_accounts = 0, set()
    for a in store.pending_followups(limit=50):
        if published >= max_per_pass:
            break
        acc = a.get("account_id")
        if acc in used_accounts:
            continue  # one per account per pass — natural spacing
        try:
            publish_reply_action(store, a["id"])
            published += 1
            used_accounts.add(acc)
        except Exception as exc:  # noqa: BLE001 - skip this one, try others next pass
            LOG.warning("followup autopublish skipped #%s: %s", a["id"], exc)
    if published:
        store.log("followup.autopublish", f"{published} follow-ups", None)
    return {"published": published}


def publish_reply_action(store, action_id: int) -> str:
    """Publish an approved reply action to Threads (best-effort).

    Replying to an arbitrary post needs its media id; if we don't have one (e.g.
    the target came from stored samples), this raises so the UI tells the user to
    post the ready draft by hand. Likes/follows are never published here.
    """
    from mobile_e2e.web import threads_client

    action = store.get_warmup_action(action_id)
    if action is None:
        raise KeyError(action_id)
    if action["kind"] not in ("reply", "followup"):
        raise ValueError("only reply/followup actions publish; like/follow are manual")
    if not action.get("target_id"):
        raise ValueError("no target media id — post this reply manually from the draft")

    account = store.get_account(action["account_id"]) if action["account_id"] else None
    creds = (account or {}).get("credentials_file") or threads_client.DEFAULT_CREDENTIALS_FILE
    published_id = threads_client.publish_reply(
        action["draft"], action["target_id"], creds)
    store.set_warmup_status(action_id, "done", published_id=published_id)
    store.record_event("warmup.reply", f"replied {published_id}", action["account_id"], level="ok")
    return published_id
