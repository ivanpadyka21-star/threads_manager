"""Tests for AppiumSettings (env-driven typed config)."""

import pytest

from mobile_e2e.config.settings import AppiumSettings, get_settings


def test_defaults():
    s = AppiumSettings()
    assert s.platform_name == "Android"
    assert s.automation_name == "UiAutomator2"
    assert s.server_url == "http://127.0.0.1:4723"
    assert s.remote_url == "http://127.0.0.1:4723"


def test_remote_url_strips_trailing_slash():
    s = AppiumSettings(server_url="http://appium:4723/")
    assert s.remote_url == "http://appium:4723"


def test_env_override(monkeypatch):
    monkeypatch.setenv("E2E_SERVER_URL", "http://grid:4444")
    monkeypatch.setenv("E2E_PLATFORM_NAME", "iOS")
    monkeypatch.setenv("E2E_DEVICE_NAME", "iPhone 15")
    s = AppiumSettings()
    assert s.server_url == "http://grid:4444"
    assert s.platform_name == "iOS"
    assert s.device_name == "iPhone 15"


def test_invalid_platform_rejected(monkeypatch):
    monkeypatch.setenv("E2E_PLATFORM_NAME", "Symbian")
    with pytest.raises(Exception):  # pydantic ValidationError
        AppiumSettings()


def test_negative_timeout_rejected():
    with pytest.raises(Exception):
        AppiumSettings(command_timeout=0)


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
