"""Parsing and representation of per-session proxy configuration.

The framework accepts proxies as a single compact string in the format::

    IP:Port:Login:Password

Authentication is optional, so ``IP:Port`` is also valid. The parsed
:class:`ProxyConfig` can then be rendered as a URL, as Appium capabilities or
as a Selenium :class:`~selenium.webdriver.common.proxy.Proxy` object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote

from mobile_e2e.core.exceptions import ProxyParseError

# Number of colon-separated fields for the supported formats.
_FIELDS_NO_AUTH = 2
_FIELDS_WITH_AUTH = 4

# Highest valid TCP port.
_MAX_PORT = 65535


@dataclass(frozen=True)
class ProxyConfig:
    """A parsed, immutable proxy configuration.

    Attributes:
        host: Proxy host / IP address.
        port: Proxy TCP port.
        login: Optional username for authenticated proxies.
        password: Optional password for authenticated proxies.
        scheme: URL scheme, defaults to ``http``.
    """

    host: str
    port: int
    login: Optional[str] = None
    password: Optional[str] = None
    scheme: str = "http"

    @classmethod
    def from_string(cls, raw: str, *, scheme: str = "http") -> "ProxyConfig":
        """Parse a proxy string in ``IP:Port`` or ``IP:Port:Login:Password`` form.

        Args:
            raw: The colon-separated proxy string.
            scheme: URL scheme to associate with the proxy (``http`` by default).

        Returns:
            A populated :class:`ProxyConfig`.

        Raises:
            ProxyParseError: If the string is empty, has an unexpected number of
                fields, is missing the host, or carries a non-numeric / out of
                range port.
        """
        if raw is None or not raw.strip():
            raise ProxyParseError("Proxy string is empty.")

        parts = raw.strip().split(":")
        if len(parts) not in (_FIELDS_NO_AUTH, _FIELDS_WITH_AUTH):
            raise ProxyParseError(
                "Expected 'IP:Port' or 'IP:Port:Login:Password', "
                f"got {len(parts)} field(s): {raw!r}"
            )

        host = parts[0].strip()
        if not host:
            raise ProxyParseError(f"Proxy host is missing in {raw!r}.")

        port = cls._parse_port(parts[1], raw)

        login: Optional[str] = None
        password: Optional[str] = None
        if len(parts) == _FIELDS_WITH_AUTH:
            login, password = parts[2], parts[3]
            if not login or not password:
                raise ProxyParseError(
                    f"Both login and password must be non-empty in {raw!r}."
                )

        return cls(host=host, port=port, login=login, password=password, scheme=scheme)

    @staticmethod
    def _parse_port(raw_port: str, raw: str) -> int:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ProxyParseError(f"Port must be an integer in {raw!r}.") from exc
        if not 1 <= port <= _MAX_PORT:
            raise ProxyParseError(
                f"Port {port} out of range (1-{_MAX_PORT}) in {raw!r}."
            )
        return port

    @property
    def has_auth(self) -> bool:
        """Whether this proxy carries credentials."""
        return self.login is not None and self.password is not None

    @property
    def host_port(self) -> str:
        """The ``host:port`` pair, without scheme or credentials."""
        return f"{self.host}:{self.port}"

    @property
    def url(self) -> str:
        """Full proxy URL, credentials percent-encoded when present."""
        if self.has_auth:
            # ``quote`` protects characters like ``@`` / ``:`` inside credentials.
            user = quote(self.login or "", safe="")
            secret = quote(self.password or "", safe="")
            return f"{self.scheme}://{user}:{secret}@{self.host_port}"
        return f"{self.scheme}://{self.host_port}"

    def as_capabilities(self) -> Dict[str, Any]:
        """Render the proxy as Appium/Selenium ``proxy`` capabilities."""
        return {
            "proxyType": "manual",
            "httpProxy": self.host_port,
            "sslProxy": self.host_port,
        }

    def as_selenium_proxy(self) -> Any:
        """Build a Selenium :class:`Proxy` object for this configuration.

        Imported lazily so that the framework's proxy parsing does not hard
        depend on Selenium being installed.
        """
        from selenium.webdriver.common.proxy import Proxy, ProxyType

        proxy = Proxy()
        proxy.proxy_type = ProxyType.MANUAL
        proxy.http_proxy = self.host_port
        proxy.ssl_proxy = self.host_port
        return proxy

    def __str__(self) -> str:  # pragma: no cover - trivial
        """Human-readable form that never leaks the password."""
        if self.has_auth:
            return f"{self.scheme}://{self.login}:***@{self.host_port}"
        return self.url
