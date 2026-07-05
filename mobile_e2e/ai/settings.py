"""Typed settings for the AI agent.

Configuration is env-driven (prefix ``E2E_AI_``) so the same agent can target
the OpenAI cloud or a local, OpenAI-compatible endpoint (e.g. Ollama / llama.cpp
server) by only changing ``E2E_AI_BASE_URL`` and ``E2E_AI_MODEL``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Accept the conventional OPENAI_API_KEY as well as E2E_AI_API_KEY.
    api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY"),
        validation_alias=AliasChoices("E2E_AI_API_KEY", "OPENAI_API_KEY"),
        description="API key. For local models any non-empty placeholder works.",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Override to hit a local OpenAI-compatible server, e.g. "
        "http://localhost:11434/v1 for Ollama.",
    )
    model: str = Field(default="gpt-4o-mini")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    request_timeout: float = Field(default=30.0, gt=0.0)


@lru_cache(maxsize=1)
def get_ai_settings() -> AISettings:
    """Return a cached :class:`AISettings` instance."""
    return AISettings()
