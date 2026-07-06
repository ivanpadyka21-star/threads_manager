"""Tests for the structured AI-prompt builder."""

from mobile_e2e.web.ai_prompt import build_ai_prompt


def test_comment_prompt_includes_language_style_target():
    task = {
        "kind": "comment", "payload": "Support the author, ask a question",
        "language": "Ukrainian", "style": "friendly, witty",
        "target": "https://threads.net/@x/post/123",
    }
    account = {"handle": "@brand", "tone": "warm"}
    system, user = build_ai_prompt(task, account)
    assert "comment on the target post" in system
    assert "Ukrainian" in system
    assert "warm" in system and "friendly, witty" in system  # combined voice
    assert "@brand" in system
    assert "threads.net/@x/post/123" in user
    assert "Support the author" in user


def test_post_prompt_minimal():
    system, user = build_ai_prompt({"kind": "post", "payload": "Announce launch"})
    assert "an original post" in system
    assert "Announce launch" in user


def test_no_language_no_voice_lines():
    system, _ = build_ai_prompt({"kind": "post", "payload": "hi"}, {})
    assert "language:" not in system.lower()
    assert "Voice" not in system


def test_falls_back_to_title_when_no_payload():
    _, user = build_ai_prompt({"kind": "post", "title": "Weekly digest"})
    assert "Weekly digest" in user
