"""Base class for UI workers (page objects).

A worker wraps a live :class:`WebDriver` and exposes reusable, typed helpers so
concrete screen objects stay small and readable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from mobile_e2e.utils.logger import get_logger

if TYPE_CHECKING:
    from appium.webdriver.webdriver import WebDriver
    from appium.webdriver.webelement import WebElement

# A Selenium locator: (by, value), e.g. (AppiumBy.ACCESSIBILITY_ID, "login").
Locator = Tuple[str, str]

LOG = get_logger(__name__)


class BaseWorker:
    """Common behaviour shared by all screen/page workers."""

    def __init__(self, driver: "WebDriver", timeout: int = 15) -> None:
        self._driver = driver
        self._wait = WebDriverWait(driver, timeout)

    @property
    def driver(self) -> "WebDriver":
        """The underlying WebDriver."""
        return self._driver

    def find(self, locator: Locator) -> "WebElement":
        """Wait for and return a single element."""
        return self._wait.until(EC.presence_of_element_located(locator))

    def tap(self, locator: Locator) -> None:
        """Wait for an element to be clickable and tap it."""
        self._wait.until(EC.element_to_be_clickable(locator)).click()

    def type_text(self, locator: Locator, text: str) -> None:
        """Type ``text`` into the located element."""
        self.find(locator).send_keys(text)

    def is_visible(self, locator: Locator) -> bool:
        """Return whether the element is currently present, without raising."""
        from selenium.common.exceptions import TimeoutException

        try:
            self._wait.until(EC.presence_of_element_located(locator))
            return True
        except TimeoutException:
            return False
