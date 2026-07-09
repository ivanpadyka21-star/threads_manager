"""Multi-provider "brain": route each ROLE to the best LLM, with cross-provider
fallback so a single provider's rate limit never stops the line.

The system has two kinds of AI work:

- **strategist / analyst** — reasoning-heavy, low volume (a plan or analysis a
  few times a day). Best served by a strong reasoning model (GPT by default when
  an OpenAI key is present).
- **writer / warmup** — bulk text generation (posts, replies), high volume. Best
  served by a fast, cheap model (Gemini by default).

Splitting the two across providers also **doubles the effective rate-limit
ceiling** — the whole reason this matters for scaling to many accounts.

Config (env, all optional):
    OPENAI_API_KEY            enables the GPT brain (else everything is Gemini)
    E2E_AI_OPENAI_MODEL       GPT model id (default gpt-4o-mini; set gpt-4o for max quality)
    E2E_AI_STRATEGIST         provider chain, e.g. "openai,gemini"
    E2E_AI_WRITER             provider chain, e.g. "gemini,openai"
"""

from __future__ import annotations

import os
from typing import List

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.ai.settings import AISettings, DEFAULT_OPENAI_MODEL
from mobile_e2e.utils.logger import get_logger

LOG = get_logger(__name__)


def _has_key(provider: str) -> bool:
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def has_openai() -> bool:
    """True when a GPT brain is available (OpenAI key present)."""
    return _has_key("openai")


def provider_settings(provider: str) -> AISettings:
    """Build :class:`AISettings` for one provider using its own key/model."""
    if provider == "openai":
        return AISettings(
            provider="openai",
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("E2E_AI_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        )
    return AISettings(
        provider="gemini",
        api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
    )


def role_providers(role: str) -> List[str]:
    """Ordered provider fallback chain for a role (only ones with a key)."""
    env = os.getenv(f"E2E_AI_{role.upper()}")
    if env:
        chain = [p.strip().lower() for p in env.split(",") if p.strip()]
    elif role in ("strategist", "analyst"):
        chain = ["openai", "gemini"]   # reasoning first, Gemini as backup
    else:  # writer / warmup
        chain = ["gemini", "openai"]   # cheap volume first, GPT as backup
    chain = [p for p in chain if _has_key(p)]
    if chain:
        return chain
    # last resort: whatever key exists
    for p in ("gemini", "openai"):
        if _has_key(p):
            return [p]
    return ["gemini"]


class BrainAgent:
    """An :class:`AIAgent`-compatible agent that tries a role's provider chain
    in order, falling back to the next provider on failure/rate-limit."""

    def __init__(self, system_prompt: str, role: str = "writer"):
        self._system_prompt = system_prompt
        self._role = role
        self._providers = role_providers(role)

    @property
    def providers(self) -> List[str]:
        return list(self._providers)

    def generate_response(self, context_text: str) -> str:
        last_error = None
        for provider in self._providers:
            try:
                agent = AIAgent(self._system_prompt, settings=provider_settings(provider))
                return agent.generate_response(context_text)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                last_error = exc
                LOG.warning("brain[%s] provider %s failed (%s); trying next.",
                            self._role, provider, type(exc).__name__)
                continue
        if last_error:
            raise last_error
        raise RuntimeError("no LLM provider available")


def make_agent(system_prompt: str, role: str = "writer") -> BrainAgent:
    """Factory used across the app to get a role-routed, failover-capable agent."""
    return BrainAgent(system_prompt, role=role)
