"""Compose a rich, structured prompt for the AI agent from a task + account.

Instead of a single free-text prompt, a task carries structured intent — what
kind of content, in which language, in what character/style, and which post it
targets. This module turns that into a clear system + user prompt so the AI
writes exactly what the specialist meant, ready for human review.

Pure functions, no LLM calls — fully unit testable.
"""

from __future__ import annotations

from typing import Optional, Tuple

# What each task kind asks the model to produce.
_KIND_ACTION = {
    "post": "an original post",
    "reply": "a reply",
    "comment": "a comment on the target post",
    "ai_generate": "a draft post",
    "custom": "the requested content",
}


def build_ai_prompt(
    task: dict, account: Optional[dict] = None, char_limit: int = 480,
    insights: Optional[str] = None, rules: Optional[str] = None,
) -> Tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for :class:`AIAgent`.

    Args:
        task: A task row (kind, payload, language, style, target, title).
        account: Optional account row (name, handle, tone).
        char_limit: Hard cap on output length; Threads rejects posts over 500
            characters, so the default leaves a safety margin.
        insights: Optional "what works" summary from real post metrics
            (``store.content_insights()['text']``). When present it is injected
            so drafts lean into what the audience actually rewards.

    Returns:
        A tuple of the system instruction and the user message.
    """
    account = account or {}
    # A per-task max_chars overrides the default limit (0/None -> default).
    task_limit = task.get("max_chars")
    if task_limit:
        char_limit = int(task_limit)
    kind = (task.get("kind") or "post").strip()
    action = _KIND_ACTION.get(kind, "the requested content")

    handle = (account.get("handle") or account.get("name") or "").strip()
    account_tone = (account.get("tone") or "").strip()
    persona = (account.get("persona") or "").strip()
    language = (task.get("language") or "").strip()
    style = (task.get("style") or "").strip()
    target = (task.get("target") or "").strip()
    payload = (task.get("payload") or task.get("title") or "").strip()

    # --- system prompt: who the model is and the fixed constraints ---------
    sys_lines = [
        "You are an SMM assistant drafting social media content for human review.",
        f"Write {action}" + (f" for the account {handle}." if handle else "."),
    ]
    if persona:
        sys_lines.append(
            "Write AS this author, in the first person, matching their voice and "
            f"their opted-in audience: {persona}"
        )
    if language:
        sys_lines.append(f"Write strictly in this language: {language}.")
    voice = ", ".join(p for p in (account_tone, style) if p)
    if voice:
        sys_lines.append(f"Voice / character: {voice}.")
    sys_lines.append(
        "Base the content strictly on the brief in the user message. The brief "
        "may be terse (a topic, mood or wish, e.g. 'I want love') — treat it as "
        "the exact theme and write a complete post that embodies it. "
        "Do NOT invent events, dates, links, webinars or 'link in bio' calls to "
        "action that the brief did not ask for. Vary the format and length "
        "naturally to fit the brief — do not fall back on the same formula every "
        "time (e.g. not always a question addressed to men): sometimes short and "
        "punchy, sometimes deeper and heartfelt, sometimes a question to any "
        "audience, sometimes a reflection. Keep it authentic and fitting the "
        "account's persona and audience; no hashtags unless asked. "
        f"IMPORTANT: the whole post MUST be at most {char_limit} characters "
        "(it will be rejected otherwise). Be concise. "
        "Output only the content itself, no explanations."
    )
    if insights and insights.strip():
        sys_lines.append(
            "PERFORMANCE DATA — lean into what the real audience rewards, do not "
            "copy the examples verbatim but absorb why they worked (the personal, "
            "intimate, vulnerable, direct tone) and apply it here:\n" + insights.strip()
        )
    if rules and rules.strip():
        sys_lines.append(
            "OWNER RULES (highest priority — always obey these over everything else):\n"
            + rules.strip()
        )

    # --- user prompt: the concrete ask ------------------------------------
    user_lines = []
    if target:
        user_lines.append(f"Target post / context: {target}")
    user_lines.append(payload or f"Write {action}.")
    if kind == "comment" and target:
        user_lines.append("Write a comment that fits and adds to that post.")

    return "\n".join(sys_lines), "\n".join(user_lines)
