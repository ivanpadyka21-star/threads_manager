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

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.ai.settings import get_ai_settings
from mobile_e2e.utils.logger import get_logger
from mobile_e2e.web.ai_prompt import build_ai_prompt

LOG = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are the CONTENT STRATEGIST operating a content dashboard for the "
    "account owner. Your job is quality: study real performance data, reason "
    "about WHY posts landed (the psychology — vulnerability, intimacy, a "
    "personal confession, a direct question people feel compelled to answer), "
    "and then steer the writer model toward more of what works.\n"
    "HIERARCHY & METHOD: (1) call get_content_insights FIRST to see the "
    "top-performing posts and the measured patterns (best length, whether a "
    "direct question helps, whether greeting helps). (2) Form a short thesis: "
    "what theme/tone/format the audience rewards right now. (3) Plan a VARIED "
    "set — lean into the winning direction (personal, intimacy/closeness, a "
    "little playful spice) but vary it: some short and punchy, some deep and "
    "heartfelt; some a direct question to men, some to women, some to both; "
    "avoid a greeting on every post if the data says it doesn't help. (4) "
    "create_tasks with clear, specific briefs, then generate_drafts so the "
    "writer produces the text — the insights are passed to it automatically.\n"
    "You NEVER publish; the human approves everything before it goes live. "
    "Write in the account's own stated persona (first person) for its opted-in "
    "audience; playful, suggestive, teasing content is fine when that is the "
    "account's style. Do not impersonate a different, real individual, and do "
    "not write sexually explicit/pornographic text. Finish by summarising your "
    "thesis and what you queued, in the user's language."
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
        "name": "get_content_insights",
        "description": (
            "Read what actually works from REAL post metrics: the top-performing "
            "posts (verbatim) and measured patterns (best length, whether a "
            "direct question or a greeting helps). Call this before planning."
        ),
        "parameters": {"type": "object", "properties": {
            "account_id": {"type": "integer", "description": "Optional: focus on one account."}
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
    {"type": "function", "function": {
        "name": "generate_drafts",
        "description": (
            "Direct the writer model to draft text for pending tasks that have "
            "no draft yet (by batch_id, or all pending for the account). Does "
            "NOT publish."
        ),
        "parameters": {"type": "object", "properties": {
            "batch_id": {"type": "string", "description": "Optional batch to draft."}
        }},
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
                {"id": a["id"], "name": a["name"], "handle": a["handle"],
                 "persona": a.get("persona", ""), "tone": a.get("tone", "")}
                for a in self._store.list_accounts()
            ]}
        if name == "get_analytics":
            hours = float(args.get("hours") or 24)
            eff = self._store.effectiveness(hours=hours)
            return {"effectiveness": eff["score"], "problems": eff["problems"],
                    "ai": self._store.ai_stats(hours=hours)}
        if name == "get_content_insights":
            account_id = args.get("account_id") or self._default_account_id
            ins = self._store.content_insights(
                account_id=int(account_id) if account_id else None)
            return {"sample_count": ins["sample_count"], "top": ins["top"],
                    "patterns": ins["patterns"], "summary": ins["text"]}
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
        if name == "generate_drafts":
            # The manager agent directs the writer model to produce drafts.
            batch_id = args.get("batch_id")
            if batch_id:
                tasks = self._store.list_batch(batch_id)
            else:
                tasks = [t for t in self._store.list_tasks(status="pending")
                         if self._default_account_id in (None, t["account_id"])]
            tasks = [t for t in tasks if not (t.get("result") or "").strip()][:10]
            done = 0
            for task in tasks:
                account = self._store.get_account(task["account_id"]) if task["account_id"] else None
                insights = self._store.content_insights(
                    account_id=task["account_id"] if task.get("account_id") else None)["text"]
                sys_p, usr_p = build_ai_prompt(task, account, insights=insights)
                try:
                    draft = AIAgent(sys_p).generate_response(usr_p)
                    self._store.set_task_result(task["id"], draft)
                    self._store.record_event("ai.generate", f"agent draft #{task['id']}", task["account_id"], level="ok")
                    done += 1
                except Exception as exc:  # noqa: BLE001 - degrade
                    self._store.record_event("ai.error", str(exc)[:200], task["account_id"], level="info")
            return {"generated": done}
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
    def run(self, instruction: str, history: Optional[list] = None) -> dict:
        """Run one turn. Pass prior ``history`` (messages) for a conversation;
        the returned ``messages`` can be persisted to continue it later."""
        messages = list(history) if history else [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": instruction})
        steps = []
        for _ in range(self._max_steps):
            resp = self._create(messages)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                messages.append({"role": "assistant", "content": msg.content or ""})
                self._store.log("agent.run", instruction[:120])
                return {"answer": msg.content or "", "steps": steps, "messages": messages}
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
        return {"answer": "Stopped after the step limit.", "steps": steps, "messages": messages}
