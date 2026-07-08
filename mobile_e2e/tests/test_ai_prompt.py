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


def test_persona_used_in_first_person():
    system, _ = build_ai_prompt(
        {"kind": "post", "payload": "утро"},
        {"handle": "@me", "persona": "sexologist, bold and playful, 18+ audience"},
    )
    assert "first person" in system.lower()
    assert "sexologist, bold and playful" in system


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


def test_char_limit_in_prompt():
    system, _ = build_ai_prompt({"kind": "post", "payload": "hi"}, char_limit=480)
    assert "480 characters" in system


def test_task_max_chars_overrides_limit():
    system, _ = build_ai_prompt({"kind": "post", "payload": "hi", "max_chars": 150}, char_limit=480)
    assert "150 characters" in system
    assert "480 characters" not in system


def test_insights_injected_into_prompt():
    system, _ = build_ai_prompt(
        {"kind": "post", "payload": "hi"},
        insights="TOP PERFORMERS: 1. 4000v — «...»",
    )
    assert "PERFORMANCE DATA" in system
    assert "TOP PERFORMERS" in system


def test_no_insights_block_when_absent():
    system, _ = build_ai_prompt({"kind": "post", "payload": "hi"})
    assert "PERFORMANCE DATA" not in system


def test_clamp_truncates_long_text():
    from mobile_e2e.web.publish import _clamp

    short = "a short post"
    assert _clamp(short) == short
    long = "word " * 200  # 1000 chars
    out = _clamp(long)
    assert len(out) <= 500
    assert out.endswith("…")
