"""In-dashboard, background Threads OAuth connector.

Automates everything around the one step a human must do (log into the Threads
account in a browser and press *Allow* — that's Meta's password/2FA, no API can
do it for you). The dashboard "Connect" button drives this manager:

1. :meth:`ConnectManager.start` picks a free credentials filename, ensures the
   account's fingerprint, resolves its proxy, builds the Threads authorization
   URL and spins up the local HTTPS callback server in a background thread.
2. The UI shows the URL; the user opens it in the antidetect browser bound to
   the account's proxy, logs in and approves.
3. The callback fires; this manager exchanges the code for a long-lived token
   **through the account's proxy + fingerprint User-Agent**, writes the token
   file and links it to the account.
4. :meth:`ConnectManager.status` lets the UI poll until ``done``/``error``.

Only one flow runs at a time (a single callback port is bound). ``pythreads`` is
imported lazily because it validates the SSL env vars at import time.
"""

from __future__ import annotations

import http.server
import os
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from mobile_e2e.core.http_session import requests_identity
from mobile_e2e.utils.logger import get_logger

LOG = get_logger(__name__)

# How long to keep the callback server open waiting for the user to finish the
# browser login before giving up.
FLOW_TIMEOUT_SECONDS = int(os.getenv("E2E_CONNECT_TIMEOUT", "600"))

REQUIRED_ENV = [
    "THREADS_APP_ID", "THREADS_API_SECRET", "THREADS_REDIRECT_URI",
    "THREADS_SSL_CERT_FILEPATH", "THREADS_SSL_KEY_FILEPATH",
]

DEFAULT_SCOPES = (
    "threads_basic,threads_content_publish,threads_manage_replies,"
    "threads_read_replies,threads_manage_insights,threads_keyword_search"
)


@dataclass
class ConnectFlow:
    id: str
    account_id: int
    credentials_file: str
    auth_url: str
    state: str
    redirect_uri: str
    proxy_label: str = ""
    has_proxy: bool = False
    app_ref: Optional[int] = None
    app_label: str = ""
    status: str = "awaiting_login"  # awaiting_login|exchanging|done|error|timeout|cancelled
    error: str = ""
    username: str = ""
    callback_url: Optional[str] = None
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict:
        return {
            "flow_id": self.id, "account_id": self.account_id,
            "credentials_file": self.credentials_file, "auth_url": self.auth_url,
            "status": self.status, "error": self.error, "username": self.username,
            "proxy_label": self.proxy_label, "has_proxy": self.has_proxy,
            "app_label": self.app_label, "app_ref": self.app_ref,
        }


class ConnectError(Exception):
    """Raised when a connect flow cannot even be started (bad setup)."""


class ConnectManager:
    """Owns the single in-flight OAuth connect flow for the dashboard."""

    def __init__(self, store) -> None:
        self._store = store
        self._flow: Optional[ConnectFlow] = None
        self._httpd: Optional[http.server.HTTPServer] = None
        self._captured: dict = {}
        self._config = None
        self._lock = threading.Lock()

    # -- public API ---------------------------------------------------------
    def start(self, account_id: int, app_ref: Optional[int] = None) -> dict:
        account = self._store.get_account(account_id)
        if account is None:
            raise ConnectError("account not found")

        # SSL cert/key are always needed (local HTTPS callback). App id/secret/
        # redirect may come from a registered app OR from the global .env.
        for key in ("THREADS_SSL_CERT_FILEPATH", "THREADS_SSL_KEY_FILEPATH"):
            p = os.getenv(key)
            if not p or not Path(p).is_file():
                raise ConnectError(f"SSL file missing: {key} -> {p}")

        # Resolve which Threads app authorizes this account: explicit choice >
        # the account's assigned app > the global .env app (default).
        if app_ref is None:
            app_ref = account.get("app_ref")
        app = self._store.get_app(app_ref) if app_ref else None
        if app:
            app_id = app["app_id"]
            app_secret = self._store.get_app_secret(app_ref)
            redirect_uri = (app.get("redirect_uri") or os.getenv("THREADS_REDIRECT_URI") or "").strip()
            app_label = app.get("label") or app["app_id"]
        else:
            app_ref = None
            app_id = os.getenv("THREADS_APP_ID")
            app_secret = os.getenv("THREADS_API_SECRET")
            redirect_uri = (os.getenv("THREADS_REDIRECT_URI") or "").strip()
            app_label = ".env default"
        if not app_id or not app_secret:
            raise ConnectError("No Threads app configured — add an app or set "
                               "THREADS_APP_ID/THREADS_API_SECRET in .env.")
        if not redirect_uri:
            raise ConnectError("No redirect URI — set it on the app or in .env.")

        with self._lock:
            if self._flow and self._flow.status in ("awaiting_login", "exchanging"):
                raise ConnectError("Another connect is already in progress. "
                                   "Finish or cancel it first.")

            # Ensure the account has a credentials file + fingerprint (hands-off).
            creds_file = (account.get("credentials_file") or "").strip()
            if not creds_file:
                creds_file = self._next_credentials_file()
                self._store.update_account(account_id, credentials_file=creds_file)
            self._store.ensure_fingerprint(account_id)
            account = self._store.get_account(account_id)  # refresh
            proxy = account.get("proxy")

            # Build the authorization URL + state for THIS app (no network yet).
            from pythreads.threads import Threads
            scopes_env = os.getenv("E2E_THREADS_SCOPES", DEFAULT_SCOPES)
            scopes = [s.strip() for s in scopes_env.split(",") if s.strip()]
            config = Threads.load_configuration(
                scopes=scopes, app_id=app_id, api_secret=app_secret,
                redirect_uri=redirect_uri)
            auth_url, state = Threads.authorization_url(config=config)

            flow = ConnectFlow(
                id=uuid.uuid4().hex[:12], account_id=account_id,
                credentials_file=creds_file, auth_url=auth_url, state=state,
                redirect_uri=redirect_uri,
                proxy_label=(proxy or {}).get("masked", "") if proxy else "",
                has_proxy=bool(proxy), app_ref=app_ref, app_label=app_label,
            )
            self._flow = flow
            self._config = config  # kept for the manual-completion fallback
            # Start the callback listener + exchange in the background.
            threading.Thread(target=self._run, args=(flow, config), daemon=True).start()
        return flow.snapshot()

    def status(self) -> Optional[dict]:
        return self._flow.snapshot() if self._flow else None

    def cancel(self) -> None:
        with self._lock:
            if self._flow and self._flow.status == "awaiting_login":
                self._flow.status = "cancelled"
            if self._httpd:
                try:
                    self._httpd.shutdown()
                except Exception:  # noqa: BLE001
                    pass

    def complete_manually(self, callback_url: str) -> dict:
        """Finish an in-progress connect by pasting the redirect URL.

        For antidetect browsers that route ``localhost`` through the proxy and
        thus can't hit the local callback server: the user copies the URL the
        browser landed on (``https://localhost:8443/callback?code=…&state=…``)
        and we feed it into the same exchange path. Requires an active flow that
        is still awaiting login.

        Raises:
            ConnectError: If there is no flow to complete, or the URL is missing
                the OAuth ``code``.
        """
        callback_url = (callback_url or "").strip()
        flow = self._flow
        if not flow or flow.status not in ("awaiting_login",):
            raise ConnectError("Нет активного подключения, которое можно завершить. "
                               "Нажми «Подключить» и получи ссылку заново.")
        if "code=" not in callback_url:
            raise ConnectError("В ссылке нет параметра code=. Скопируй ПОЛНЫЙ URL из "
                               "адресной строки браузера (там, где localhost/callback?code=…).")
        # Feed the exchange and stop the (unreachable) local server so _run
        # proceeds to exchange with this URL.
        self._captured["url"] = callback_url
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
        else:
            # No server thread waiting (rare) — exchange inline.
            self._exchange(flow, self._config, callback_url)
        return flow.snapshot()

    # -- internals ----------------------------------------------------------
    def _next_credentials_file(self) -> str:
        used = {(a.get("credentials_file") or "").strip()
                for a in self._store.list_accounts()}
        for n in range(2, 1000):
            name = f"threads_credentials_{n}.json"
            if name not in used and not Path(name).exists():
                return name
        raise ConnectError("could not allocate a credentials filename")

    def _run(self, flow: ConnectFlow, config) -> None:
        redirect = flow.redirect_uri
        cert = os.getenv("THREADS_SSL_CERT_FILEPATH")
        key = os.getenv("THREADS_SSL_KEY_FILEPATH")
        parsed = urlparse(redirect)
        host = parsed.hostname or "localhost"
        port = parsed.port or 443
        expected_path = parsed.path or "/callback"
        # Feeds the exchange from EITHER the local callback hit OR a manually
        # pasted URL (antidetect browsers route localhost through the proxy and
        # can't reach this server — the user pastes the URL instead).
        self._captured = {}

        manager = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if urlparse(self.path).path == expected_path:
                    manager._captured["url"] = f"{parsed.scheme}://{host}:{port}{self.path}"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        "<h2>✓ Готово. Можно "
                        "закрыть вкладку "
                        "и вернуться в "
                        "дашборд.</h2>".encode("utf-8"))
                    threading.Thread(target=manager._httpd.shutdown, daemon=True).start()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args) -> None:  # silence
                pass

        try:
            httpd = http.server.HTTPServer((host, port), Handler)
        except OSError as exc:
            flow.status = "error"
            flow.error = (f"Порт {port} занят другим процессом (напр. Apache/httpd, "
                          f"Skype). Освободи порт {port} и попробуй снова. [{exc}]")
            return
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        self._httpd = httpd

        # Watchdog: stop waiting after the timeout if the user never finishes.
        def _watchdog() -> None:
            deadline = flow.started_at + FLOW_TIMEOUT_SECONDS
            while time.time() < deadline:
                if flow.status != "awaiting_login" or self._captured.get("url"):
                    return
                time.sleep(1)
            if flow.status == "awaiting_login" and not self._captured.get("url"):
                flow.status = "timeout"
                try:
                    httpd.shutdown()
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=_watchdog, daemon=True).start()

        try:
            httpd.serve_forever()  # returns on callback, cancel or timeout
        finally:
            try:
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._httpd = None

        callback_url = self._captured.get("url")
        flow.callback_url = callback_url
        if not callback_url:
            if flow.status == "awaiting_login":
                flow.status = "cancelled"
            return

        self._exchange(flow, config, callback_url)

    def _exchange(self, flow: ConnectFlow, config, callback_url: str) -> None:
        flow.status = "exchanging"
        import logging
        from mobile_e2e.core import http_session
        req_log = logging.getLogger("threads.requests")
        http_session._ensure_request_log()
        try:
            account = self._store.get_account(flow.account_id) or {}
            proxy_url = self._store.proxy_url(account.get("proxy"))
            user_agent = (account.get("fingerprint") or {}).get("user_agent")
            require = self._store.get_setting("proxy_enforce_all", "0") == "1"

            label = f"AUTH acc={flow.credentials_file} via {http_session.mask_proxy_url(proxy_url)}"
            # Prove, in the log, that authorization egresses through the proxy:
            # fetch the outgoing IP via the SAME proxy right before exchanging.
            egress = http_session.probe(proxy_url) if proxy_url else {"ok": True, "ip": "DIRECT"}
            req_log.info("%s | egress IP = %s%s", label, egress.get("ip") or "?",
                         "" if egress.get("ok") else f" (proxy check failed: {egress.get('error')})")

            from pythreads.threads import Threads
            with requests_identity(proxy_url, user_agent, require_proxy=require):
                credentials = Threads.complete_authorization(
                    callback_url, flow.state, config=config)
            req_log.info("%s | token exchange OK", label)

            with open(flow.credentials_file, "w", encoding="utf-8") as f:
                f.write(credentials.to_json())

            # Probe the freshly connected account (also through its proxy).
            from mobile_e2e.web import threads_client
            health = threads_client.account_health(flow.credentials_file)
            self._store.set_account_health(flow.account_id, health["status"],
                                           health.get("username", ""))
            flow.username = health.get("username", "")
            # Track which app this account is now on (for the per-app cap + view).
            if flow.app_ref:
                try:
                    self._store.assign_app(flow.account_id, flow.app_ref)
                except ValueError:  # noqa: BLE001 - capacity race; token is still valid
                    pass
            flow.status = "done"
            self._store.record_event("account.connect",
                                     f"token saved -> {flow.credentials_file} "
                                     f"(app: {flow.app_label})",
                                     flow.account_id, level="ok")
            LOG.info("connected account %s -> %s", flow.account_id, flow.credentials_file)
        except Exception as exc:  # noqa: BLE001 - report to UI, don't crash thread
            flow.status = "error"
            flow.error = str(exc)[:200]
            req_log.warning("AUTH acc=%s | token exchange FAILED: %s",
                            flow.credentials_file, str(exc)[:160])
            self._store.record_event("account.connect_error", str(exc)[:200],
                                     flow.account_id, level="error")
            LOG.warning("connect failed for account %s: %s", flow.account_id, exc)
