"""Tests for SessionManager, mocking only the network call to Appium.

The real capability/option building runs; ``appium.webdriver.Remote`` is patched
so no server or device is required.
"""

from unittest.mock import MagicMock, patch

import pytest

from mobile_e2e.core.exceptions import (
    ProxyParseError,
    SessionNotFoundError,
    SessionStartupError,
)
from mobile_e2e.core.proxy import ProxyConfig
from mobile_e2e.core.session_manager import SessionManager


def _fake_driver(session_id: str) -> MagicMock:
    driver = MagicMock(name=f"driver-{session_id}")
    driver.session_id = session_id
    return driver


@patch("appium.webdriver.Remote")
def test_create_session_tracks_driver(mock_remote):
    mock_remote.return_value = _fake_driver("s1")
    manager = SessionManager()

    driver = manager.create_session()

    assert manager.active_sessions == 1
    assert manager.get_session("s1") is driver
    # Remote called with our configured server URL and an options object.
    _, kwargs = mock_remote.call_args
    assert kwargs["command_executor"] == manager._settings.remote_url
    assert kwargs["options"] is not None


@patch("appium.webdriver.Remote")
def test_create_session_with_string_proxy(mock_remote):
    mock_remote.return_value = _fake_driver("s2")
    manager = SessionManager()

    manager.create_session(proxy="10.0.0.1:8080:user:secret")

    options = mock_remote.call_args.kwargs["options"]
    caps = options.to_capabilities()
    assert caps["proxy"]["httpProxy"] == "10.0.0.1:8080"


@patch("appium.webdriver.Remote")
def test_create_session_with_proxyconfig(mock_remote):
    mock_remote.return_value = _fake_driver("s3")
    manager = SessionManager()

    manager.create_session(proxy=ProxyConfig.from_string("1.2.3.4:9000"))

    caps = mock_remote.call_args.kwargs["options"].to_capabilities()
    assert caps["proxy"]["httpProxy"] == "1.2.3.4:9000"


def test_invalid_proxy_string_raises_before_driver():
    manager = SessionManager()
    with pytest.raises(ProxyParseError):
        manager.create_session(proxy="not-a-proxy")
    assert manager.active_sessions == 0


@patch("appium.webdriver.Remote", side_effect=RuntimeError("connection refused"))
def test_startup_failure_wrapped(mock_remote):
    manager = SessionManager()
    with pytest.raises(SessionStartupError) as exc:
        manager.create_session()
    assert "connection refused" in str(exc.value)
    assert manager.active_sessions == 0


@patch("appium.webdriver.Remote")
def test_quit_session(mock_remote):
    driver = _fake_driver("s4")
    mock_remote.return_value = driver
    manager = SessionManager()
    manager.create_session()

    manager.quit_session("s4")

    driver.quit.assert_called_once()
    assert manager.active_sessions == 0


def test_quit_unknown_session_raises():
    manager = SessionManager()
    with pytest.raises(SessionNotFoundError):
        manager.quit_session("missing")


def test_get_unknown_session_raises():
    manager = SessionManager()
    with pytest.raises(SessionNotFoundError):
        manager.get_session("missing")


@patch("appium.webdriver.Remote")
def test_quit_all_and_context_manager(mock_remote):
    mock_remote.side_effect = [_fake_driver("a"), _fake_driver("b")]
    with SessionManager() as manager:
        manager.create_session()
        manager.create_session()
        assert manager.active_sessions == 2
    # context exit -> quit_all
    assert manager.active_sessions == 0


@patch("appium.webdriver.Remote")
def test_quit_all_survives_driver_error(mock_remote):
    driver = _fake_driver("s5")
    driver.quit.side_effect = RuntimeError("already dead")
    mock_remote.return_value = driver
    manager = SessionManager()
    manager.create_session()

    # Should not raise even though driver.quit() fails.
    manager.quit_all()
    assert manager.active_sessions == 0
