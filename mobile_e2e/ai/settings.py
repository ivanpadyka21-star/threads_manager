"""Typed settings for the AI agent.

Configuration is env-driven (prefix ``E2E_AI_``). The agent talks to any
OpenAI-compatible chat endpoint, which lets one code path target:

- **Gemini** (default) via Google's OpenAI-compatible endpoint,
- OpenAI,
- a local server (Ollama / llama.cpp),

by only changing ``E2E_AI_PROVIDER`` (or ``E2E_AI_BASE_URL`` / ``E2E_AI_MODEL``).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Google's OpenAI-compatible Gemini endpoint (works with the openai SDK).
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

Provider = Literal["gemini", "openai"]


class AISettings(BaseSettings):
    """Runtime configuration for :class:`~mobile_e2e.ai.agent.AIAgent`."""

    model_config = SettingsConfigDict(
        env_prefix="E2E_AI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow constructing AISettings(api_key=...) in code, not only via the
        # env aliases below.
        populate_by_name=True,
    )

    provider: Provider = Field(
        default="gemini",
        description="Which OpenAI-compatible backend to use.",
    )
    # Accept the conventional provider keys as well as E2E_AI_API_KEY.
    api_key: Optional[str] = Field(
        default_factory=lambda: (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        ),
        validation_alias=AliasChoices(
            "E2E_AI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"
        ),
        description="API key for the selected provider.",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Override the endpoint (defaults per provider).",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model id (defaults per provider).",
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    request_timeout: float = Field(default=30.0, gt=0.0)

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> "AISettings":
        """Fill base_url/model from the provider when not set explicitly."""
        if self.provider == "gemini":
            if not self.base_url:
                self.base_url = GEMINI_OPENAI_BASE
            if not self.model:
                self.model = DEFAULT_GEMINI_MODEL
        else:  # openai (or any explicit base_url)
            if not self.model:
                self.model = DEFAULT_OPENAI_MODEL
        return self


@lru_cache(maxsize=1)
def get_ai_settings() -> AISettings:
    """Return a cached :class:`AISettings` instance."""
    return AISettings()
