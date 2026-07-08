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


def main() -> None:
    redirect, cert, key = _check_env()

    # Imported here: pythreads reads SSL env at import time.
    from pythreads.threads import Threads

    # Only request scopes the app actually has, or Threads returns invalid_scope.
    scopes_env = os.getenv(
        "E2E_THREADS_SCOPES",
        "threads_basic,threads_content_publish,threads_manage_replies,"
        "threads_manage_insights",
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
    try:
        webbrowser.open(auth_url)
    except Exception:  # noqa: BLE001
        pass

    httpd.serve_forever()  # blocks until the handler calls shutdown()

    callback_url = captured.get("url")
    if not callback_url:
        raise SystemExit("No callback captured — authorization was not completed.")

    print(">>> Exchanging the code for a long-lived token...")
    credentials = Threads.complete_authorization(callback_url, state, config=config)

    output = os.getenv("THREADS_CREDENTIALS_FILE", DEFAULT_OUTPUT)
    with open(output, "w", encoding="utf-8") as f:
        f.write(credentials.to_json())
    print(f"\n[OK] Credentials saved to {output}")
    print("     The dashboard Settings tab should now show 'Connected'.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
