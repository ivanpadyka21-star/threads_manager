"""High-level, flaky-resistant UI interactions.

:class:`UIWorker` wraps a live Appium driver (as produced by
``SessionManager.create_session``) and exposes robust, retrying actions. Every
public action re-runs on transient failures (:class:`TimeoutException`,
:class:`StaleElementReferenceException`) so slow emulators or dynamic
re-renders do not turn into flaky tests.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional, TypeVar

from selenium.webdriver.common.keys import Keys

from mobile_e2e.utils.logger import get_logger
from mobile_e2e.utils.retry import retry_on_ui_error
from mobile_e2e.workers.base_worker import BaseWorker, Locator

if TYPE_CHECKING:
    from appium.webdriver.webdriver import WebDriver

LOG = get_logger(__name__)

T = TypeVar("T")


class UIWorker(BaseWorker):
    """Robust UI actions on top of a WebDriver session.

    Args:
        driver: A live Appium WebDriver from :class:`SessionManager`.
        timeout: Explicit-wait timeout (seconds) for locating elements.
        char_delay: Default per-character delay when typing (seconds).
        retry_attempts: How many times a transient action is retried.
        retry_delay: Initial back-off between retries (seconds).
    """

    def __init__(
        self,
        driver: "WebDriver",
        timeout: int = 15,
        char_delay: float = 0.05,
        retry_attempts: int = 3,
        retry_delay: float = 0.5,
    ) -> None:
        super().__init__(driver, timeout=timeout)
        self._char_delay = char_delay
        self._retry_attempts = retry_attempts
        self._retry_delay = retry_delay

    # -- retry plumbing -----------------------------------------------------
    def _with_retry(self, func: Callable[..., T], *args: object, **kwargs: object) -> T:
        """Run ``func`` under this worker's configured retry policy.

        Reuses the reusable :func:`retry_on_ui_error` decorator but binds it to
        the per-instance ``retry_attempts`` / ``retry_delay`` settings.
        """
        wrapped = retry_on_ui_error(
            attempts=self._retry_attempts,
            delay=self._retry_delay,
        )(func)
        return wrapped(*args, **kwargs)

    # -- public actions -----------------------------------------------------
    def read_screen_text(self, locator: Locator) -> str:
        """Locate an element and safely return its text.

        Retries on transient lookup failures. Never raises on empty/missing
        text — returns an empty string instead.

        Args:
            locator: The Selenium/Appium locator ``(by, value)``.

        Returns:
            The element's trimmed text (``""`` if it has none).

        Raises:
            RetryExhaustedError: If the element cannot be located within the
                configured attempts.
        """
        return self._with_retry(self._read_screen_text_once, locator)

    def type_and_submit(
        self,
        locator: Locator,
        text: str,
        submit_locator: Optional[Locator] = None,
        char_delay: Optional[float] = None,
    ) -> None:
        """Type ``text`` into a field character by character, then submit.

        The element is re-located on every retry so a mid-interaction re-render
        cannot leave us holding a stale reference.

        Args:
            locator: Locator of the input field.
            text: Text to type.
            submit_locator: Optional locator of a submit/send button. When
                omitted, an ``ENTER`` key press is sent to the field instead.
            char_delay: Optional per-character delay overriding the worker
                default.

        Raises:
            RetryExhaustedError: If the interaction keeps failing transiently.
        """
        delay = self._char_delay if char_delay is None else char_delay
        self._with_retry(
            self._type_and_submit_once, locator, text, submit_locator, delay
        )

    # -- single-attempt implementations ------------------------------------
    def _read_screen_text_once(self, locator: Locator) -> str:
        element = self.find(locator)
        text = element.text
        if not text:
            # Android often exposes label text via the "text" attribute when
            # the WebDriver ``.text`` property comes back empty.
            try:
                text = element.get_attribute("text") or ""
            except Exception:  # noqa: BLE001 - never fail a read on attr lookup
                text = ""
        return text.strip()

    def _type_and_submit_once(
        self,
        locator: Locator,
        text: str,
        submit_locator: Optional[Locator],
        delay: float,
    ) -> None:
        field = self.find(locator)
        field.clear()
        self._type_chars(field, text, delay)

        if submit_locator is not None:
            self.tap(submit_locator)
        else:
            # Portable default: submit the field via an ENTER key event.
            field.send_keys(Keys.ENTER)

    def _type_chars(self, field: object, text: str, delay: float) -> None:
        """Send ``text`` one character at a time with a delay between them."""
        for char in text:
            field.send_keys(char)  # type: ignore[attr-defined]
            if delay > 0:
                time.sleep(delay)
