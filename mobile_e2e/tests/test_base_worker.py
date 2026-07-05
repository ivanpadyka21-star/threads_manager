"""Tests for BaseWorker, mocking the WebDriver and element interactions."""

from unittest.mock import MagicMock, patch

from selenium.common.exceptions import TimeoutException

from mobile_e2e.workers.base_worker import BaseWorker

LOCATOR = ("accessibility id", "login")


def _worker():
    return BaseWorker(driver=MagicMock(), timeout=1)


def test_driver_property():
    driver = MagicMock()
    worker = BaseWorker(driver=driver, timeout=1)
    assert worker.driver is driver


def test_find_returns_element():
    worker = _worker()
    element = MagicMock()
    with patch.object(worker._wait, "until", return_value=element) as until:
        assert worker.find(LOCATOR) is element
        until.assert_called_once()


def test_tap_clicks_element():
    worker = _worker()
    element = MagicMock()
    with patch.object(worker._wait, "until", return_value=element):
        worker.tap(LOCATOR)
        element.click.assert_called_once()


def test_type_text_sends_keys():
    worker = _worker()
    element = MagicMock()
    with patch.object(worker._wait, "until", return_value=element):
        worker.type_text(LOCATOR, "hello")
        element.send_keys.assert_called_once_with("hello")


def test_is_visible_true():
    worker = _worker()
    with patch.object(worker._wait, "until", return_value=MagicMock()):
        assert worker.is_visible(LOCATOR) is True


def test_is_visible_false_on_timeout():
    worker = _worker()
    with patch.object(worker._wait, "until", side_effect=TimeoutException()):
        assert worker.is_visible(LOCATOR) is False
