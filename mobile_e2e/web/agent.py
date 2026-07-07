"""An autonomous planning agent built on Gemini function-calling.

The agent receives a natural-language instruction and may call a small set of
dashboard *tools* (list accounts, read analytics, create pending post tasks) to
carry it out. It **never publishes** — it only proposes tasks/drafts that the
human approves. Deceptive persona content is refused at the system-prompt level.

Uses the OpenAI-compatible client (Gemini endpoint), so no extra dependency.
"""

from __future__ import annotations

import json
from typing import Optional

from mobile_e2e.ai.settings import get_ai_settings
from mobile_e2e.utils.logger import get_logger

LOG = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are an SMM planning assistant operating a content dashboard. You can "
    "plan posts, read analytics, and CREATE PENDING tasks for the user to "
    "review — you NEVER publish anything yourself; the human approves before "
    "anything goes live. Refuse to create deceptive content such as posing as a "
    "fake persona (e.g. pretending to be a real girl/person) to bait or lure "
    "others. Work step by step: use the tools to accomplish the request, keep "
    "posts within the requested length, then briefly summarise what you did."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_accounts",
        "description": "List the managed accounts (id, name, handle) to choose from.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_analytics",
        "description": "Get the effectiveness score, problems and AI stats over a window.",
        "parameters": {"type": "object", "properties": {
            "hours": {"type": "number", "description": "Window in hours (default 24)."}
        }},
    }},
    {"type": "function", "function": {
        "name": "create_tasks",
        "description": (
            "Create PENDING post tasks (for human review — does NOT publish). "
            "Provide one brief per post."
        ),
        "parameters": {"type": "object", "properties": {
            "account_id": {"type": "integer"},
            "posts": {"type": "array", "items": {"type": "string"},
                      "description": "Short brief per post (max 10)."},
            "interval_minutes": {"type": "integer"},
            "start_at": {"type": "string", "description": "ISO datetime, optional."},
            "language": {"type": "string"},
            "style": {"type": "string"},
            "max_chars": {"type": "integer"},
        }, "required": ["posts"]},
    }},
]


class AgentRunner:
    """Runs the tool-calling loop against the dashboard store."""

    def __init__(self, store, client=None, model: Optional[str] = None,
                 default_account_id: Optional[int] = None, max_steps: int = 6):
        self._store = store
        self._settings = get_ai_settings()
        self._client = client
        # Fallback chain: explicit model -> single; otherwise the provider chain
        # (e.g. gemini-2.5-flash -> gemini-2.5-flash-lite) so a rate-limited
        # model switches to the next.
        self._models = [model] if model else (self._settings.model_list or [self._settings.model])
        self._default_account_id = default_account_id
        self._max_steps = max_steps

    @property
    def client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(
                api_key=self._settings.api_key,
                base_url=self._settings.base_url,
                timeout=self._settings.request_timeout,
            )
        return self._client

    # -- tools --------------------------------------------------------------
    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "list_accounts":
            return {"accounts": [
                {"id": a["id"], "name": a["name"], "handle": a["handle"]}
                for a in self._store.list_accounts()
            ]}
        if name == "get_analytics":
            hours = float(args.get("hours") or 24)
            eff = self._store.effectiveness(hours=hours)
            return {"effectiveness": eff["score"], "problems": eff["problems"],
                    "ai": self._store.ai_stats(hours=hours)}
        if name == "create_tasks":
            posts = [str(p).strip() for p in (args.get("posts") or []) if str(p).strip()][:10]
            if not posts:
                return {"error": "no posts provided"}
            account_id = args.get("account_id") or self._default_account_id
            batch = self._store.add_batch(
                account_id=int(account_id) if account_id else None,
                briefs=posts,
                language=args.get("language", ""),
                style=args.get("style", ""),
                interval_minutes=int(args.get("interval_minutes") or 90),
                start_at=args.get("start_at") or None,
                max_chars=int(args["max_chars"]) if args.get("max_chars") else None,
            )
            self._store.log("agent.create_tasks", f"{len(posts)} pending posts", account_id)
            return {"created": len(batch["tasks"]), "batch_id": batch["batch_id"],
                    "status": "pending — awaiting your review/approval"}
        return {"error": f"unknown tool {name}"}

    def _create(self, messages):
        """Call the model with tools, switching models on rate limit/not-found."""
        import openai
        last_error = None
        for model in self._models:
            try:
                return self.client.chat.completions.create(
                    model=model, messages=messages, tools=TOOLS, temperature=0.4,
                )
            except (openai.RateLimitError, openai.NotFoundError) as exc:
                last_error = exc
                LOG.warning("Agent model %s unavailable (%s); trying next.", model, type(exc).__name__)
                continue
        raise last_error

    # -- loop ---------------------------------------------------------------
    def run(self, instruction: str) -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ]
        steps = []
        for _ in range(self._max_steps):
            resp = self._create(messages)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                self._store.log("agent.run", instruction[:120])
                return {"answer": msg.content or "", "steps": steps}
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                } for tc in tool_calls],
            })
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self._dispatch(tc.function.name, args)
                steps.append({"tool": tc.function.name, "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
        return {"answer": "Stopped after the step limit.", "steps": steps}
