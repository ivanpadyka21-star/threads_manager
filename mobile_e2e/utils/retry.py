"""A small retry decorator tuned for flaky mobile-UI interactions.

Slow emulators frequently raise :class:`TimeoutException` (element not ready
yet) or :class:`StaleElementReferenceException` (the layout re-rendered between
lookup and use). This decorator re-runs the wrapped call a bounded number of
times with exponential back-off before giving up with a typed
:class:`RetryExhaustedError`.
"""

from __future__ import annotations

import functools
import time
from typing import Callable, Tuple, Type, TypeVar

from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)

from mobile_e2e.core.exceptions import RetryExhaustedError
from mobile_e2e.utils.logger import get_logger

LOG = get_logger(__name__)

T = TypeVar("T")

# Errors that are worth retrying: transient timing / re-render issues.
DEFAULT_RETRY_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    TimeoutException,
    StaleElementReferenceException,
)


def retry_on_ui_error(
    attempts: int = 3,
    delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: Tuple[Type[BaseException], ...] = DEFAULT_RETRY_EXCEPTIONS,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry the decorated callable on transient UI errors.

    Args:
        attempts: Total number of tries (must be >= 1).
        delay: Initial sleep between tries, in seconds.
        backoff: Multiplier applied to ``delay`` after each failed try.
        exceptions: Exception types that trigger a retry. Anything else
            propagates immediately.

    Returns:
        A decorator wrapping the target callable.

    Raises:
        ValueError: If ``attempts`` < 1.
        RetryExhaustedError: If every attempt fails on a retryable exception.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> T:
            current_delay = delay
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < attempts:
                        LOG.warning(
                            "%s failed (attempt %d/%d): %s -- retrying in %.2fs",
                            func.__name__,
                            attempt,
                            attempts,
                            type(exc).__name__,
                            current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        LOG.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            attempts,
                            type(exc).__name__,
                        )
            raise RetryExhaustedError(
                f"{func.__name__} failed after {attempts} attempt(s)"
            ) from last_exc

        return wrapper

    return decorator
