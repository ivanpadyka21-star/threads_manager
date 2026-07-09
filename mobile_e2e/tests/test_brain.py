"""Tests for the multi-provider brain (role routing + cross-provider fallback)."""

import pytest

from mobile_e2e.ai import brain


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
              "E2E_AI_STRATEGIST", "E2E_AI_WRITER"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_gemini_only_when_no_openai_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert brain.has_openai() is False
    assert brain.role_providers("strategist") == ["gemini"]
    assert brain.role_providers("writer") == ["gemini"]


def test_strategist_prefers_gpt_writer_prefers_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert brain.has_openai() is True
    assert brain.role_providers("strategist") == ["openai", "gemini"]  # reasoning first
    assert brain.role_providers("writer") == ["gemini", "openai"]      # cheap volume first


def test_env_override_of_provider_chain(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("E2E_AI_STRATEGIST", "gemini,openai")
    assert brain.role_providers("strategist") == ["gemini", "openai"]


def test_provider_settings_pick_right_backend(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("E2E_AI_OPENAI_MODEL", "gpt-4o")
    s_open = brain.provider_settings("openai")
    assert s_open.provider == "openai" and s_open.api_key == "sk-o" and s_open.model == "gpt-4o"
    s_gem = brain.provider_settings("gemini")
    assert s_gem.provider == "gemini" and "generativelanguage" in s_gem.base_url


def test_brain_agent_falls_over_to_next_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    calls = []

    class FakeAgent:
        def __init__(self, sp, settings=None):
            self._provider = settings.provider
        def generate_response(self, ctx):
            calls.append(self._provider)
            if self._provider == "openai":
                raise RuntimeError("429 rate limited")
            return "written by gemini"

    monkeypatch.setattr(brain, "AIAgent", FakeAgent)
    out = brain.BrainAgent("sys", role="strategist").generate_response("hi")
    assert out == "written by gemini"
    assert calls == ["openai", "gemini"]  # tried GPT first, fell back to Gemini
