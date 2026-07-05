"""Tests for the retry_on_ui_error decorator."""

import pytest
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)

from mobile_e2e.core.exceptions import RetryExhaustedError
from mobile_e2e.utils.retry import retry_on_ui_error


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make retries instant."""
    monkeypatch.setattr("mobile_e2e.utils.retry.time.sleep", lambda *_: None)


def test_succeeds_first_try():
    calls = []

    @retry_on_ui_error(attempts=3)
    def action():
        calls.append(1)
        return "ok"

    assert action() == "ok"
    assert len(calls) == 1


def test_retries_then_succeeds():
    calls = []

    @retry_on_ui_error(attempts=3)
    def action():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutException("not yet")
        return "ok"

    assert action() == "ok"
    assert len(calls) == 3


def test_retries_on_stale_element():
    calls = []

    @retry_on_ui_error(attempts=2)
    def action():
        calls.append(1)
        if len(calls) < 2:
            raise StaleElementReferenceException("re-rendered")
        return "ok"

    assert action() == "ok"
    assert len(calls) == 2


def test_exhausts_and_raises_retry_error():
    @retry_on_ui_error(attempts=3)
    def action():
        raise TimeoutException("always")

    with pytest.raises(RetryExhaustedError) as exc:
        action()
    # Original cause preserved.
    assert isinstance(exc.value.__cause__, TimeoutException)


def test_non_retryable_propagates_immediately():
    calls = []

    @retry_on_ui_error(attempts=3)
    def action():
        calls.append(1)
        raise WebDriverException("fatal")

    with pytest.raises(WebDriverException):
        action()
    assert len(calls) == 1  # not retried


def test_backoff_grows(monkeypatch):
    sleeps = []
    monkeypatch.setattr(
        "mobile_e2e.utils.retry.time.sleep", lambda s: sleeps.append(s)
    )

    @retry_on_ui_error(attempts=3, delay=0.5, backoff=2.0)
    def action():
        raise TimeoutException("x")

    with pytest.raises(RetryExhaustedError):
        action()
    assert sleeps == [0.5, 1.0]  # two sleeps between three attempts


def test_invalid_attempts_rejected():
    with pytest.raises(ValueError):
        retry_on_ui_error(attempts=0)
