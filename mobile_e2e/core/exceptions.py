"""Framework-specific exception hierarchy.

A single base class (:class:`E2EFrameworkError`) lets callers catch every error
raised by the framework, while the specialised subclasses allow fine-grained
handling (e.g. retry only on :class:`SessionStartupError`).
"""

from __future__ import annotations


class E2EFrameworkError(Exception):
    """Base class for every error raised by the framework."""


class ProxyParseError(E2EFrameworkError, ValueError):
    """Raised when a proxy string cannot be parsed into a :class:`ProxyConfig`.

    Also a :class:`ValueError` so existing ``except ValueError`` handlers keep
    working.
    """


class SessionError(E2EFrameworkError):
    """Base class for anything that goes wrong with a driver session."""


class SessionStartupError(SessionError):
    """Raised when the Appium WebDriver session fails to initialise."""


class SessionNotFoundError(SessionError, KeyError):
    """Raised when a session id is requested but is not tracked by the manager."""
