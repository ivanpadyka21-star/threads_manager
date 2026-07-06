"""LLM-backed generation of styled responses from on-screen context.

:class:`AIAgent` is deliberately decoupled from Appium/Selenium: it only knows
about text in and text out. It talks to any OpenAI-compatible chat endpoint
(OpenAI cloud or a local Llama server), so tests can drive real UI-derived text
through an LLM to produce data in a configured tone of voice.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

import openai

from mobile_e2e.ai.settings import AISettings, get_ai_settings
from mobile_e2e.core.exceptions import AIAgentError
from mobile_e2e.utils.logger import get_logger

if TYPE_CHECKING:
    from openai import OpenAI

LOG = get_logger(__name__)

# Errors that should retry the SAME model (transient network/server issues).
_RETRY_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)
# Errors that should immediately try the NEXT model in the chain
# (rate limit = separate quota per model; not-found = wrong model id).
_SWITCH_ERRORS = (
    openai.RateLimitError,
    openai.NotFoundError,
)


class AIAgent:
    """Generate styled text responses from screen context via an LLM.

    Args:
        system_prompt: The role/instruction that fixes what the agent does.
        tone: Optional tone-of-voice appended to the system prompt.
        settings: Optional :class:`AISettings`; falls back to env-derived
            defaults.
        client: Optional pre-built OpenAI-compatible client (mainly for tests
            and dependency injection). Built lazily from ``settings`` otherwise.
        fallback_response: Optional template returned when generation fails. If
            it contains ``{context}`` it is formatted with the input text. When
            ``None``, failures raise :class:`AIAgentError` instead.
        max_retries: Number of extra attempts on transient provider errors.
        retry_delay: Initial back-off between retries (seconds).
    """

    def __init__(
        self,
        system_prompt: str,
        *,
        tone: Optional[str] = None,
        settings: Optional[AISettings] = None,
        client: Optional["OpenAI"] = None,
        fallback_response: Optional[str] = None,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ) -> None:
        if not system_prompt or not system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string.")

        self._settings = settings or get_ai_settings()
        self._system_prompt = self._compose_system_prompt(system_prompt, tone)
        self._client = client
        self._fallback_response = fallback_response
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    @staticmethod
    def _compose_system_prompt(system_prompt: str, tone: Optional[str]) -> str:
        prompt = system_prompt.strip()
        if tone and tone.strip():
            prompt = f"{prompt}\n\nTone of voice: {tone.strip()}"
        return prompt

    # -- lazy client --------------------------------------------------------
    @property
    def client(self) -> "OpenAI":
        """The OpenAI-compatible client, built on first use."""
        if self._client is None:
            self._client = openai.OpenAI(
                api_key=self._settings.api_key,
                base_url=self._settings.base_url,
                timeout=self._settings.request_timeout,
            )
        return self._client

    # -- public API ---------------------------------------------------------
    def generate_response(self, context_text: str) -> str:
        """Send ``context_text`` to the LLM and return the generated reply.

        Args:
            context_text: Text read from the screen to respond to.

        Returns:
            The generated response string.

        Raises:
            ValueError: If ``context_text`` is empty.
            AIAgentError: If generation fails and no ``fallback_response`` was
                configured. The provider error is chained via ``__cause__``.
        """
        if not context_text or not context_text.strip():
            raise ValueError("context_text must be a non-empty string.")

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": context_text},
        ]

        models = self._settings.model_list or [self._settings.model]
        last_error: Optional[BaseException] = None

        for model in models:
            try:
                return self._call_model(model, messages)
            except _SWITCH_ERRORS as exc:
                last_error = exc
                LOG.warning("Model %s unavailable (%s); trying next model.", model, type(exc).__name__)
                continue
            except _RETRY_ERRORS as exc:
                # Retried within _call_model and still failing -> next model.
                last_error = exc
                LOG.warning("Model %s failing (%s); trying next model.", model, type(exc).__name__)
                continue
            except AIAgentError as exc:
                # Empty response -> try the next model.
                last_error = exc
                continue
            except openai.OpenAIError as exc:
                # Auth / bad request: switching models won't help.
                last_error = exc
                break

        return self._handle_failure(context_text, last_error)

    def _call_model(self, model: str, messages: list) -> str:
        """One model, with same-model retries for transient network errors."""
        attempts = self._max_retries + 1
        delay = self._retry_delay
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=self._settings.temperature,
                    max_tokens=self._settings.max_tokens,
                )
            except _RETRY_ERRORS:
                if attempt < attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            content = self._extract_content(response)
            if content:
                return content
            raise AIAgentError("LLM returned an empty response.")
        raise AIAgentError("exhausted retries")  # pragma: no cover

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _extract_content(response: object) -> str:
        try:
            content = response.choices[0].message.content  # type: ignore[attr-defined]
        except (AttributeError, IndexError, TypeError):
            return ""
        return content.strip() if content else ""

    def _handle_failure(
        self, context_text: str, error: Optional[BaseException]
    ) -> str:
        if self._fallback_response is not None:
            LOG.error(
                "LLM generation failed (%s); returning fallback response.",
                type(error).__name__ if error else "unknown",
            )
            return self._render_fallback(context_text)
        raise AIAgentError(
            f"Failed to generate a response: "
            f"{type(error).__name__ if error else 'unknown error'}"
        ) from error

    def _render_fallback(self, context_text: str) -> str:
        template = self._fallback_response or ""
        if "{context}" in template:
            try:
                return template.format(context=context_text)
            except (KeyError, IndexError, ValueError):
                return template
        return template
