"""Proxied, fingerprinted HTTP transport for Threads API calls.

Every request an account makes to the Threads Graph API must leave through that
account's own proxy and carry that account's own stable client headers. This
module builds the :class:`aiohttp.ClientSession` that enforces both:

- **Proxy at the connector level** (via ``aiohttp_socks``). Because the proxy is
  bound to the connector — not passed per-request — *all* traffic on the session
  (auth, publish, replies, insights, health) goes through it, and if the proxy
  is unreachable the connection simply fails: no request ever falls back to the
  machine's real IP. This is the fail-closed guarantee.
- **Fingerprint headers** as session defaults (User-Agent, Accept-Language).

Supported proxy schemes: ``http`` and ``socks5`` (both handled by
``aiohttp_socks``'s ``ProxyConnector``). A parallel sync helper covers the OAuth
token exchange, which pythreads performs with the blocking ``requests`` library.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from mobile_e2e.core.exceptions import ProxyRequiredError

# Default per-request timeout (seconds). Proxied calls are a little slower, and a
# dead proxy should fail fast rather than hang the scheduler.
DEFAULT_TIMEOUT = 30

# Dedicated request logger. Writes every Threads request (account, proxy, target,
# status) to console AND to a file you can tail. NEVER logs the query string,
# which carries the access_token — only method + host + path.
_REQ_LOG = logging.getLogger("threads.requests")
_REQ_LOG_READY = False


def _ensure_request_log() -> None:
    global _REQ_LOG_READY
    if _REQ_LOG_READY:
        return
    _REQ_LOG_READY = True
    _REQ_LOG.setLevel(logging.INFO)
    try:
        log_dir = os.getenv("E2E_LOG_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "web", "data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(log_dir, "proxy_requests.log"),
                                 encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        _REQ_LOG.addHandler(fh)
    except Exception:  # noqa: BLE001 - file logging is best-effort
        pass


def mask_proxy_url(proxy_url: Optional[str]) -> str:
    """Render a proxy URL without its password, for safe logging."""
    if not proxy_url:
        return "DIRECT"
    try:
        s = urlsplit(proxy_url)
        host = s.hostname or ""
        port = f":{s.port}" if s.port else ""
        user = f"{s.username}@" if s.username else ""
        return f"{s.scheme}://{user}{host}{port}"
    except Exception:  # noqa: BLE001
        return "proxy"


def _trace_config(label: str) -> aiohttp.TraceConfig:
    """Build an aiohttp trace that logs each request's outcome with its proxy."""
    tc = aiohttp.TraceConfig()

    async def _on_start(session, ctx, params):
        ctx.start = time.monotonic()

    async def _on_end(session, ctx, params):
        ms = int((time.monotonic() - getattr(ctx, "start", time.monotonic())) * 1000)
        # host + path only — the query string holds the access_token.
        target = f"{params.url.host}{params.url.path}"
        _REQ_LOG.info("%s | %s %s -> %s (%dms)", label, params.method, target,
                      params.response.status, ms)

    async def _on_exc(session, ctx, params):
        target = params.url.host if params.url else "?"
        _REQ_LOG.warning("%s | %s %s -> FAILED %s: %s", label, params.method, target,
                         type(params.exception).__name__, str(params.exception)[:120])

    tc.on_request_start.append(_on_start)
    tc.on_request_end.append(_on_end)
    tc.on_request_exception.append(_on_exc)
    return tc


async def build_session(
    proxy_url: Optional[str],
    fingerprint_headers: Optional[Dict[str, str]] = None,
    *,
    require_proxy: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    log_label: Optional[str] = None,
) -> aiohttp.ClientSession:
    """Build an aiohttp session bound to ``proxy_url`` with fingerprint headers.

    Args:
        proxy_url: Full proxy URL (``http://…`` or ``socks5://…``, with optional
            ``user:pass@``). ``None`` means a direct connection.
        fingerprint_headers: Default headers applied to every request on the
            session (User-Agent, Accept-Language).
        require_proxy: When ``True`` and ``proxy_url`` is falsy, raise
            :class:`ProxyRequiredError` instead of connecting directly. This is
            the fail-closed switch — no request goes out without a proxy.
        timeout: Total per-request timeout in seconds.

    Returns:
        An open :class:`aiohttp.ClientSession`. The caller owns it and must
        close it.

    Raises:
        ProxyRequiredError: If ``require_proxy`` is set but no proxy is given.
    """
    if not proxy_url:
        if require_proxy:
            raise ProxyRequiredError(
                "This account must connect through a proxy, but none is "
                "assigned/available. Refusing to send over the direct IP."
            )
        connector = None
    else:
        # Imported lazily so the framework does not hard-depend on aiohttp_socks
        # unless a proxy is actually used. ProxyConnector must be created inside
        # a running loop (this coroutine provides one).
        from aiohttp_socks import ProxyConnector

        connector = ProxyConnector.from_url(proxy_url)

    trace_configs = None
    if log_label is not None:
        _ensure_request_log()
        trace_configs = [_trace_config(log_label)]

    return aiohttp.ClientSession(
        connector=connector,
        headers=fingerprint_headers or {},
        timeout=aiohttp.ClientTimeout(total=timeout),
        trace_configs=trace_configs,
    )


async def _egress_ip_async(proxy_url: Optional[str], timeout: float) -> str:
    session = await build_session(proxy_url, timeout=timeout)
    try:
        async with session.get("https://api.ipify.org") as r:
            return (await r.text()).strip()
    finally:
        await session.close()


def probe(proxy_url: Optional[str], timeout: float = 15) -> Dict[str, object]:
    """Test a proxy by fetching our egress IP through it.

    Returns ``{"ok": bool, "ip": str, "error": str}``. Never raises — a dead
    proxy comes back as ``ok=False`` with the error, which is exactly the
    fail-closed signal the UI shows next to the proxy.
    """
    import asyncio
    try:
        ip = asyncio.run(_egress_ip_async(proxy_url, timeout))
        return {"ok": True, "ip": ip, "error": ""}
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        return {"ok": False, "ip": "", "error": f"{type(exc).__name__}: {exc}"[:160]}


@contextmanager
def requests_identity(proxy_url: Optional[str], user_agent: Optional[str] = None,
                      *, require_proxy: bool = False) -> Iterator[None]:
    """Give blocking ``requests`` calls the account's proxy AND its User-Agent.

    Used for the OAuth token exchange so the *first* authorization call already
    presents the same identity the runtime session uses: the account's proxy IP
    and its frozen fingerprint User-Agent. ``requests``/``requests_oauthlib``
    build the UA from :func:`requests.utils.default_user_agent` at session
    creation, so we patch that for the duration (and restore it afterwards).
    """
    import requests.utils as _ru

    original = _ru.default_user_agent
    try:
        if user_agent:
            _ru.default_user_agent = lambda name="python-requests": user_agent
        with requests_proxy_env(proxy_url, require_proxy=require_proxy):
            yield
    finally:
        _ru.default_user_agent = original


@contextmanager
def requests_proxy_env(proxy_url: Optional[str], *, require_proxy: bool = False) -> Iterator[None]:
    """Route blocking ``requests`` calls (the OAuth exchange) through a proxy.

    pythreads performs the OAuth code→token exchange with the ``requests``
    library, which honours the ``HTTP_PROXY`` / ``HTTPS_PROXY`` environment
    variables. This context manager sets them for the duration and restores the
    previous values afterwards, so even the very first authorization request
    leaves through the account's proxy.

    Args:
        proxy_url: Full proxy URL, or ``None`` for a direct connection.
        require_proxy: When ``True`` and no proxy is given, raise
            :class:`ProxyRequiredError` (fail-closed) rather than authorizing
            over the direct IP.
    """
    if not proxy_url:
        if require_proxy:
            raise ProxyRequiredError(
                "Authorization must go through a proxy, but none is available."
            )
        yield
        return

    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ[k] = proxy_url
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
