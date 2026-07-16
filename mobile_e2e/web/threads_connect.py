"""One-command Threads OAuth connector.

Run once, after filling the ``THREADS_*`` values in ``.env`` and generating the
SSL cert/key:

    python -m mobile_e2e.web.threads_connect

It opens the Threads authorization page in your browser, catches the redirect
on a local HTTPS server (``THREADS_REDIRECT_URI``), completes the OAuth exchange
and saves the long-lived credentials to ``threads_credentials.json`` — no manual
copy-pasting of URLs or state.

``pythreads`` validates SSL env vars at import time, so it is imported lazily
inside :func:`main` (after ``.env`` is loaded and validated).
"""

from __future__ import annotations

import http.server
import os
import ssl
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_OUTPUT = "threads_credentials.json"


def _check_env() -> tuple[str, str, str]:
    from dotenv import load_dotenv

    load_dotenv()
    redirect = os.getenv("THREADS_REDIRECT_URI")
    cert = os.getenv("THREADS_SSL_CERT_FILEPATH")
    key = os.getenv("THREADS_SSL_KEY_FILEPATH")

    missing = [
        k for k in ("THREADS_APP_ID", "THREADS_API_SECRET", "THREADS_REDIRECT_URI",
                    "THREADS_SSL_CERT_FILEPATH", "THREADS_SSL_KEY_FILEPATH")
        if not os.getenv(k)
    ]
    if missing:
        raise SystemExit("Missing in .env: " + ", ".join(missing))
    for label, path in (("cert", cert), ("key", key)):
        if not Path(path).is_file():
            raise SystemExit(f"SSL {label} file not found: {path} (run the openssl steps)")
    return redirect, cert, key


def _connect_proxy_url() -> str | None:
    """Build a proxy URL from E2E_CONNECT_PROXY for the OAuth exchange, or None."""
    raw = os.getenv("E2E_CONNECT_PROXY", "").strip()
    if not raw:
        return None
    from mobile_e2e.core.proxy import ProxyConfig
    scheme = os.getenv("E2E_CONNECT_PROXY_SCHEME", "http").strip().lower()
    return ProxyConfig.from_string(raw, scheme=scheme).url


def _connect_identity(output_file: str) -> tuple[str | None, str | None]:
    """Resolve (proxy_url, user_agent) to use for authorizing this account.

    Priority: the dashboard account whose ``credentials_file`` == ``output_file``
    (so the account's assigned proxy + frozen fingerprint are used automatically);
    the ``E2E_CONNECT_PROXY`` env var overrides the proxy if set.
    """
    env_proxy = _connect_proxy_url()
    proxy_url, user_agent = env_proxy, None
    try:
        from mobile_e2e.web.store import Store
        db_path = os.getenv("E2E_WEB_DB") or os.path.join(
            os.path.dirname(__file__), "data", "dashboard.db")
        if os.path.isfile(db_path):
            db = Store(db_path)
            for a in db.list_accounts():
                if a.get("credentials_file") == output_file:
                    if not proxy_url and a.get("proxy"):
                        proxy_url = Store.proxy_url(a["proxy"])
                    user_agent = (a.get("fingerprint") or {}).get("user_agent")
                    break
    except Exception:  # noqa: BLE001 - fall back to env-only proxy, no UA
        pass
    return proxy_url, user_agent


def main() -> None:
    redirect, cert, key = _check_env()

    # Imported here: pythreads reads SSL env at import time.
    from pythreads.threads import Threads

    # Only request scopes the app actually has, or Threads returns invalid_scope.
    # threads_keyword_search powers the warm-up agent's live niche feed search —
    # the app must have that permission/use-case enabled in the Meta dashboard,
    # otherwise drop it here (search then falls back to manual samples).
    # threads_read_replies lets us READ the replies/comments on our OWN posts
    # (threads_manage_replies only *creates/hides* them) — this unlocks replying
    # to real people's comments. It is first-party data ("owned by the app user"),
    # available at Standard Access like threads_manage_insights.
    scopes_env = os.getenv(
        "E2E_THREADS_SCOPES",
        "threads_basic,threads_content_publish,threads_manage_replies,"
        "threads_read_replies,threads_manage_insights,threads_keyword_search",
    )
    scopes = [s.strip() for s in scopes_env.split(",") if s.strip()]
    config = Threads.load_configuration(scopes=scopes)
    auth_url, state = Threads.authorization_url(config=config)

    parsed = urlparse(redirect)
    host = parsed.hostname or "localhost"
    port = parsed.port or 443
    expected_path = parsed.path or "/callback"
    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path == expected_path:
                captured["url"] = f"{parsed.scheme}://{host}:{port}{self.path}"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<h2>✓ Authorized. You can close this tab and return to the terminal.</h2>".encode()
                )
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args) -> None:  # silence default logging
            pass

    httpd = http.server.HTTPServer((host, port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert, keyfile=key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

    print("\n>>> Opening the Threads authorization page in your browser...")
    print("    If it doesn't open, paste this URL manually:\n")
    print("   ", auth_url, "\n")
    print(f">>> Waiting for the redirect on {redirect}")
    print("    (Your browser may warn about the self-signed localhost cert - accept it.)\n")
    # Skip auto-opening the system browser when E2E_NO_BROWSER=1 — for antidetect
    # setups where the URL must be pasted into a specific profile, not the default
    # browser (which would leak the real IP / open the wrong session).
    if os.getenv("E2E_NO_BROWSER") != "1":
        try:
            webbrowser.open(auth_url)
        except Exception:  # noqa: BLE001
            pass

    httpd.serve_forever()  # blocks until the handler calls shutdown()

    callback_url = captured.get("url")
    if not callback_url:
        raise SystemExit("No callback captured — authorization was not completed.")

    # Output file (computed up front so we can bind the account's proxy + UA):
    # CLI arg wins (for a second account), else env, else default.
    # e.g. `python -m mobile_e2e.web.threads_connect threads_credentials_2.json`
    output = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
              else os.getenv("THREADS_CREDENTIALS_FILE", DEFAULT_OUTPUT))

    print(">>> Exchanging the code for a long-lived token...")
    # Route the OAuth token exchange (blocking `requests` inside pythreads) through
    # the SAME identity the runtime session uses: the account's proxy IP and its
    # frozen fingerprint User-Agent. So the very first authorization call already
    # matches every later request — never this machine's real IP or a stray UA.
    # The account is matched by credentials_file; E2E_CONNECT_PROXY overrides the
    # proxy. Fail-closed when E2E_CONNECT_PROXY_REQUIRED=1.
    from mobile_e2e.core.http_session import requests_identity
    proxy_url, user_agent = _connect_identity(output)
    if proxy_url:
        print(f">>> Authorizing through proxy: {proxy_url.split('@')[-1]}")
    if user_agent:
        print(f">>> Using account fingerprint UA: {user_agent[:48]}…")
    with requests_identity(proxy_url, user_agent,
                           require_proxy=os.getenv("E2E_CONNECT_PROXY_REQUIRED") == "1"):
        credentials = Threads.complete_authorization(callback_url, state, config=config)

    with open(output, "w", encoding="utf-8") as f:
        f.write(credentials.to_json())
    print(f"\n[OK] Credentials saved to {output}")
    if output == DEFAULT_OUTPUT:
        print("     The dashboard Settings tab should now show 'Connected'.")
    else:
        print(f"     Now create a dashboard account whose credentials_file = {output}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
