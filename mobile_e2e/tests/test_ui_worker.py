"""Tests for UIWorker: safe reads, char-by-char typing, submit and retries."""

from unittest.mock import MagicMock, call, patch

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys

from mobile_e2e.core.exceptions import RetryExhaustedError
from mobile_e2e.workers.ui_worker import UIWorker

FIELD = ("accessibility id", "username")
LABEL = ("accessibility id", "greeting")
SUBMIT = ("accessibility id", "send")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Zero out both typing delays and retry back-off."""
    monkeypatch.setattr("mobile_e2e.utils.retry.time.sleep", lambda *_: None)
    monkeypatch.setattr("mobile_e2e.workers.ui_worker.time.sleep", lambda *_: None)


def _worker(**kw) -> UIWorker:
    return UIWorker(driver=MagicMock(), timeout=1, **kw)


# -- read_screen_text -------------------------------------------------------
def test_read_screen_text_returns_stripped_text():
    worker = _worker()
    element = MagicMock()
    element.text = "  Hello  "
    with patch.object(worker, "find", return_value=element):
        assert worker.read_screen_text(LABEL) == "Hello"


def test_read_screen_text_falls_back_to_attribute():
    worker = _worker()
    element = MagicMock()
    element.text = ""
    element.get_attribute.return_value = "from-attr"
    with patch.object(worker, "find", return_value=element):
        assert worker.read_screen_text(LABEL) == "from-attr"
        element.get_attribute.assert_called_once_with("text")


def test_read_screen_text_empty_is_safe():
    worker = _worker()
    element = MagicMock()
    element.text = None
    element.get_attribute.return_value = None
    with patch.object(worker, "find", return_value=element):
        assert worker.read_screen_text(LABEL) == ""


def test_read_screen_text_retries_then_succeeds():
    worker = _worker(retry_attempts=3)
    element = MagicMock()
    element.text = "ok"
    with patch.object(
        worker, "find", side_effect=[TimeoutException(), element]
    ) as find:
        assert worker.read_screen_text(LABEL) == "ok"
        assert find.call_count == 2


def test_read_screen_text_exhausts_retries():
    worker = _worker(retry_attempts=2)
    with patch.object(worker, "find", side_effect=TimeoutException()):
        with pytest.raises(RetryExhaustedError):
            worker.read_screen_text(LABEL)


# -- type_and_submit --------------------------------------------------------
def test_type_and_submit_types_each_char_and_presses_enter():
    worker = _worker(char_delay=0)
    field = MagicMock()
    with patch.object(worker, "find", return_value=field):
        worker.type_and_submit(FIELD, "abc")

    field.clear.assert_called_once()
    # One send_keys per character, plus the ENTER submit.
    assert field.send_keys.call_args_list == [
        call("a"),
        call("b"),
        call("c"),
        call(Keys.ENTER),
    ]


def test_type_and_submit_uses_submit_locator_when_given():
    worker = _worker(char_delay=0)
    field = MagicMock()
    with patch.object(worker, "find", return_value=field), patch.object(
        worker, "tap"
    ) as tap:
        worker.type_and_submit(FIELD, "hi", submit_locator=SUBMIT)

    tap.assert_called_once_with(SUBMIT)
    # ENTER must NOT be sent when an explicit submit button is used.
    assert call(Keys.ENTER) not in field.send_keys.call_args_list
    assert field.send_keys.call_args_list == [call("h"), call("i")]


def test_type_and_submit_honours_char_delay():
    worker = _worker(char_delay=0.02)
    field = MagicMock()
    sleeps = []
    with patch.object(worker, "find", return_value=field), patch(
        "mobile_e2e.workers.ui_worker.time.sleep", side_effect=sleeps.append
    ):
        worker.type_and_submit(FIELD, "ab", char_delay=0.2)

    # Per-call override wins over the constructor default; one sleep per char.
    assert sleeps == [0.2, 0.2]


def test_type_and_submit_retries_relocate_element():
    worker = _worker(retry_attempts=2, char_delay=0)
    good_field = MagicMock()
    with patch.object(
        worker, "find", side_effect=[TimeoutException(), good_field]
    ) as find:
        worker.type_and_submit(FIELD, "x")
    assert find.call_count == 2
    good_field.send_keys.assert_any_call("x")
