"""Session lifecycle management for Appium WebDriver sessions.

:class:`SessionManager` owns the creation, tracking and teardown of Appium
sessions. Each session can be given its own network (proxy) configuration,
passed either as a raw ``IP:Port:Login:Password`` string or a parsed
:class:`ProxyConfig`.
"""

from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING, Any, Dict, Optional, Type, Union

from mobile_e2e.config.capabilities import build_capabilities, build_options
from mobile_e2e.config.settings import AppiumSettings, get_settings
from mobile_e2e.core.exceptions import (
    SessionNotFoundError,
    SessionStartupError,
)
from mobile_e2e.core.proxy import ProxyConfig
from mobile_e2e.utils.logger import get_logger

if TYPE_CHECKING:
    from appium.webdriver.webdriver import WebDriver

LOG = get_logger(__name__)

# A proxy may be supplied as a raw string, an already-parsed config, or omitted.
ProxyLike = Union[str, ProxyConfig, None]


class SessionManager:
    """Create, track and tear down Appium WebDriver sessions.

    The manager keeps a registry of live sessions keyed by their Appium
    ``session_id`` so callers can look them up or close them individually, and
    can be used as a context manager to guarantee cleanup::

        with SessionManager() as manager:
            driver = manager.create_session(proxy="10.0.0.1:8080:user:pass")
            ...
    """

    def __init__(self, settings: Optional[AppiumSettings] = None) -> None:
        self._settings: AppiumSettings = settings or get_settings()
        self._sessions: Dict[str, "WebDriver"] = {}

    # -- lifecycle ----------------------------------------------------------
    def create_session(
        self,
        proxy: ProxyLike = None,
        extra_capabilities: Optional[Dict[str, Any]] = None,
    ) -> "WebDriver":
        """Initialise a new Appium WebDriver session.

        Args:
            proxy: Optional proxy as a raw ``IP:Port:Login:Password`` string or a
                :class:`ProxyConfig`. When ``None``, no proxy is configured.
            extra_capabilities: Optional capabilities merged over the defaults.

        Returns:
            The started :class:`WebDriver`.

        Raises:
            ProxyParseError: If ``proxy`` is a string that cannot be parsed.
            SessionStartupError: If the Appium session fails to initialise.
        """
        proxy_config = self._coerce_proxy(proxy)
        capabilities = build_capabilities(
            self._settings, proxy=proxy_config, extra=extra_capabilities
        )

        if proxy_config is not None:
            LOG.info("Starting session via proxy %s", proxy_config)
        else:
            LOG.info("Starting session without proxy")

        driver = self._start_driver(capabilities)

        session_id = driver.session_id
        self._sessions[session_id] = driver
        LOG.info("Session %s started", session_id)
        return driver

    def _start_driver(self, capabilities: Dict[str, Any]) -> "WebDriver":
        """Instantiate the Appium ``Remote`` driver, wrapping failures.

        Import is local so parsing/config code does not require the Appium
        client to be installed.
        """
        try:
            from appium import webdriver
        except ImportError as exc:  # pragma: no cover - environment guard
            raise SessionStartupError(
                "Appium-Python-Client is not installed; run "
                "`pip install Appium-Python-Client`."
            ) from exc

        options = build_options(capabilities)
        try:
            return webdriver.Remote(
                command_executor=self._settings.remote_url,
                options=options,
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            # WebDriverException, connection errors and malformed-capability
            # errors are all normalised into one framework error so callers can
            # retry on a single, meaningful exception type.
            raise SessionStartupError(
                f"Failed to start Appium session at "
                f"{self._settings.remote_url}: {exc}"
            ) from exc

    def quit_session(self, session_id: str) -> None:
        """Quit a tracked session and drop it from the registry.

        Raises:
            SessionNotFoundError: If ``session_id`` is not tracked.
        """
        driver = self._sessions.pop(session_id, None)
        if driver is None:
            raise SessionNotFoundError(session_id)
        self._safe_quit(session_id, driver)

    def quit_all(self) -> None:
        """Quit every tracked session, best-effort."""
        for session_id, driver in list(self._sessions.items()):
            self._safe_quit(session_id, driver)
        self._sessions.clear()

    @staticmethod
    def _safe_quit(session_id: str, driver: "WebDriver") -> None:
        try:
            driver.quit()
            LOG.info("Session %s closed", session_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not raise
            LOG.warning("Error while closing session %s: %s", session_id, exc)

    # -- accessors ----------------------------------------------------------
    def get_session(self, session_id: str) -> "WebDriver":
        """Return a tracked session by id.

        Raises:
            SessionNotFoundError: If ``session_id`` is not tracked.
        """
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(session_id) from exc

    @property
    def active_sessions(self) -> int:
        """Number of currently tracked sessions."""
        return len(self._sessions)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _coerce_proxy(proxy: ProxyLike) -> Optional[ProxyConfig]:
        if proxy is None or isinstance(proxy, ProxyConfig):
            return proxy
        return ProxyConfig.from_string(proxy)

    # -- context manager ----------------------------------------------------
    def __enter__(self) -> "SessionManager":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.quit_all()
