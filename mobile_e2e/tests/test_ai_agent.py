"""Tests for AIAgent using a mocked OpenAI-compatible client (no network)."""

from unittest.mock import MagicMock

import httpx
import openai
import pytest

from mobile_e2e.ai.agent import AIAgent
from mobile_e2e.ai.settings import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_OPENAI_BASE,
    AISettings,
)
from mobile_e2e.core.exceptions import AIAgentError


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("mobile_e2e.ai.agent.time.sleep", lambda *_: None)


def _response(text: str) -> MagicMock:
    """Build an object shaped like an OpenAI chat completion."""
    message = MagicMock()
    message.content = text
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _client(create_side_effect=None, return_value=None) -> MagicMock:
    client = MagicMock()
    if create_side_effect is not None:
        client.chat.completions.create.side_effect = create_side_effect
    else:
        client.chat.completions.create.return_value = return_value
    return client


def _settings() -> AISettings:
    return AISettings(api_key="test-key", model="test-model")


def _agent(client, **kw) -> AIAgent:
    return AIAgent(
        "You generate replies.", settings=_settings(), client=client, **kw
    )


# -- construction -----------------------------------------------------------
# -- provider settings ------------------------------------------------------
def test_gemini_is_default_provider():
    s = AISettings(api_key="k")
    assert s.provider == "gemini"
    assert s.base_url == GEMINI_OPENAI_BASE
    assert s.model == DEFAULT_GEMINI_MODEL


def test_openai_provider_defaults():
    s = AISettings(provider="openai", api_key="k")
    assert s.base_url is None
    assert s.model == "gpt-4o-mini"


def test_explicit_base_url_and_model_kept():
    s = AISettings(api_key="k", base_url="http://localhost:11434/v1", model="llama3")
    assert s.base_url == "http://localhost:11434/v1"
    assert s.model == "llama3"


def test_gemini_key_from_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gm-123")
    assert AISettings().api_key == "gm-123"


def test_empty_system_prompt_rejected():
    with pytest.raises(ValueError):
        AIAgent("   ", settings=_settings(), client=MagicMock())


def test_tone_is_appended_to_system_prompt():
    client = _client(return_value=_response("hi"))
    agent = AIAgent(
        "You are a support bot.",
        tone="friendly and concise",
        settings=_settings(),
        client=client,
    )
    agent.generate_response("Hello?")
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "You are a support bot." in messages[0]["content"]
    assert "Tone of voice: friendly and concise" in messages[0]["content"]


# -- happy path -------------------------------------------------------------
def test_generate_response_returns_text():
    client = _client(return_value=_response("  Generated reply.  "))
    agent = _agent(client)
    assert agent.generate_response("Screen says X") == "Generated reply."


def test_context_passed_as_user_message():
    client = _client(return_value=_response("ok"))
    agent = _agent(client)
    agent.generate_response("The login button is disabled")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["messages"][1] == {
        "role": "user",
        "content": "The login button is disabled",
    }


def test_empty_context_rejected():
    agent = _agent(_client(return_value=_response("x")))
    with pytest.raises(ValueError):
        agent.generate_response("  ")


# -- retries / errors -------------------------------------------------------
def _timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://llm"))


def test_transient_error_retried_then_succeeds():
    client = _client(create_side_effect=[_timeout(), _response("recovered")])
    agent = _agent(client, max_retries=2)
    assert agent.generate_response("ctx") == "recovered"
    assert client.chat.completions.create.call_count == 2


def test_transient_error_exhausts_and_raises():
    client = _client(create_side_effect=_timeout())
    agent = _agent(client, max_retries=2)
    with pytest.raises(AIAgentError) as exc:
        agent.generate_response("ctx")
    assert isinstance(exc.value.__cause__, openai.APITimeoutError)
    assert client.chat.completions.create.call_count == 3  # 1 + 2 retries


def test_non_transient_error_not_retried():
    client = _client(create_side_effect=openai.OpenAIError("bad request"))
    agent = _agent(client, max_retries=3)
    with pytest.raises(AIAgentError):
        agent.generate_response("ctx")
    assert client.chat.completions.create.call_count == 1


# -- fallback ---------------------------------------------------------------
def test_fallback_returned_on_failure():
    client = _client(create_side_effect=_timeout())
    agent = _agent(client, max_retries=0, fallback_response="default answer")
    assert agent.generate_response("ctx") == "default answer"


def test_fallback_template_formatted_with_context():
    client = _client(create_side_effect=openai.OpenAIError("down"))
    agent = _agent(
        client, fallback_response="Could not process: {context}"
    )
    assert agent.generate_response("screen text") == "Could not process: screen text"


def test_empty_llm_response_triggers_fallback():
    client = _client(return_value=_response(""))
    agent = _agent(client, fallback_response="fallback")
    assert agent.generate_response("ctx") == "fallback"


def test_empty_llm_response_without_fallback_raises():
    client = _client(return_value=_response(None))
    agent = _agent(client)
    with pytest.raises(AIAgentError):
        agent.generate_response("ctx")


# -- lazy client ------------------------------------------------------------
def test_client_built_lazily_from_settings(monkeypatch):
    created = {}

    def fake_openai(**kwargs):
        created.update(kwargs)
        return _client(return_value=_response("hi"))

    monkeypatch.setattr("mobile_e2e.ai.agent.openai.OpenAI", fake_openai)
    agent = AIAgent(
        "prompt",
        settings=AISettings(
            api_key="k", base_url="http://localhost:11434/v1", model="llama3"
        ),
    )
    assert agent.generate_response("ctx") == "hi"
    assert created["api_key"] == "k"
    assert created["base_url"] == "http://localhost:11434/v1"
