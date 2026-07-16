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


class ProxyRequiredError(E2EFrameworkError):
    """Raised when a request must go through a proxy but none is available.

    This enforces the fail-closed rule: if an account is meant to run through a
    proxy and that proxy is missing/unassigned, we raise *before* opening any
    connection, so no request ever leaks out over the machine's real IP.
    """


class SessionError(E2EFrameworkError):
    """Base class for anything that goes wrong with a driver session."""


class SessionStartupError(SessionError):
    """Raised when the Appium WebDriver session fails to initialise."""


class SessionNotFoundError(SessionError, KeyError):
    """Raised when a session id is requested but is not tracked by the manager."""


class UIActionError(E2EFrameworkError):
    """Base class for failures while interacting with the UI."""


class RetryExhaustedError(UIActionError):
    """Raised when a retried UI action keeps failing after every attempt.

    The original error is preserved on ``__cause__`` (via ``raise ... from``).
    """


class AIAgentError(E2EFrameworkError):
    """Raised when the AI agent cannot produce a response and no fallback is set.

    The underlying provider error is preserved on ``__cause__``.
    """
