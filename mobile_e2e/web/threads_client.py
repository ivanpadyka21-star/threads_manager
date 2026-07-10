"""Thin, lazily-imported wrapper around the official Threads API (pythreads).

``pythreads`` insists on SSL env vars *at import time*, so nothing here imports
it at module load. :func:`status` reports whether the integration is configured
(and what is missing) without importing anything, and :func:`publish_text` only
imports pythreads when an actual, configured publish is requested.

This keeps the whole dashboard usable before any Threads credentials exist, and
makes "connect a test account" an explicit, auditable step.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List, Optional

# Environment variables required for the official OAuth + publish flow.
REQUIRED_ENV = [
    "THREADS_APP_ID",
    "THREADS_API_SECRET",
    "THREADS_REDIRECT_URI",
    "THREADS_SSL_CERT_FILEPATH",
    "THREADS_SSL_KEY_FILEPATH",
]

DEFAULT_CREDENTIALS_FILE = "threads_credentials.json"


class ThreadsNotConfigured(Exception):
    """Raised when a Threads action is attempted before setup is complete."""


def status(credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> dict:
    """Report configuration state without importing pythreads.

    Returns:
        ``{configured, missing_env, ssl_ok, has_credentials, credentials_file}``.
    """
    missing_env = [k for k in REQUIRED_ENV if not os.getenv(k)]

    ssl_ok = True
    for key in ("THREADS_SSL_CERT_FILEPATH", "THREADS_SSL_KEY_FILEPATH"):
        path = os.getenv(key)
        if not path or not Path(path).is_file():
            ssl_ok = False

    has_credentials = bool(credentials_file) and Path(credentials_file).is_file()

    configured = not missing_env and ssl_ok and has_credentials
    return {
        "configured": configured,
        "missing_env": missing_env,
        "ssl_ok": ssl_ok,
        "has_credentials": has_credentials,
        "credentials_file": credentials_file,
    }


def _ensure_ready(credentials_file: str) -> None:
    st = status(credentials_file)
    if st["configured"]:
        return
    problems: List[str] = []
    if st["missing_env"]:
        problems.append("missing env: " + ", ".join(st["missing_env"]))
    if not st["ssl_ok"]:
        problems.append("SSL cert/key files not found")
    if not st["has_credentials"]:
        problems.append(f"no credentials file ({credentials_file}) — run OAuth first")
    raise ThreadsNotConfigured("; ".join(problems))


async def _publish_async(text: str, credentials_file: str) -> str:
    # Imported lazily: pythreads validates SSL env vars at import time.
    from pythreads.api import API
    from pythreads.credentials import Credentials

    with open(credentials_file, "r", encoding="utf-8") as f:
        credentials = Credentials.from_json(f.read())

    async with API(credentials=credentials) as api:
        container_id = await api.create_container(text=text)
        published_id = await api.publish_container(container_id)
        return str(published_id)


def _parse_insights(raw: dict) -> dict:
    """Flatten the Threads insights response into {views, likes, replies, ...}."""
    out = {"views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0}
    for item in (raw or {}).get("data", []):
        name = item.get("name")
        if name not in out:
            continue
        value = None
        values = item.get("values")
        if values:
            value = values[0].get("value")
        elif isinstance(item.get("total_value"), dict):
            value = item["total_value"].get("value")
        if value is not None:
            out[name] = int(value)
    return out


async def _insights_async(media_id: str, credentials_file: str) -> dict:
    from pythreads.api import API
    from pythreads.credentials import Credentials
    from pythreads.threads import Threads

    with open(credentials_file, "r", encoding="utf-8") as f:
        credentials = Credentials.from_json(f.read())
    async with API(credentials=credentials) as api:
        # The Threads media-insights endpoint requires a `metric=` parameter
        # (pythreads' own insights() sends `fields=`, which the API rejects).
        url = Threads.build_graph_api_url(
            f"{media_id}/insights",
            {"metric": "views,likes,replies,reposts,quotes"},
            api._access_token(),
        )
        return await api._get(url)


def fetch_insights(media_id: str, credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> dict:
    """Fetch a published post's metrics (views/likes/replies/…).

    Requires the ``threads_manage_insights`` permission on the token.

    Raises:
        ThreadsNotConfigured: If setup is incomplete.
        RuntimeError: If the Threads API returns an error.
    """
    _ensure_ready(credentials_file)
    raw = asyncio.run(_insights_async(media_id, credentials_file))
    if isinstance(raw, dict) and raw.get("error"):
        raise RuntimeError(str(raw["error"].get("message", raw["error"])))
    return _parse_insights(raw)


async def _replies_async(media_id: str, credentials_file: str) -> dict:
    from pythreads.api import API
    from pythreads.credentials import Credentials
    from pythreads.threads import Threads

    with open(credentials_file, "r", encoding="utf-8") as f:
        credentials = Credentials.from_json(f.read())
    async with API(credentials=credentials) as api:
        url = Threads.build_graph_api_url(
            f"{media_id}/replies",
            {"fields": "id,text,username,timestamp,is_reply_owned_by_me,"
                       "hide_status,has_replies", "reverse": "false"},
            api._access_token(),
        )
        return await api._get(url)


def fetch_replies(media_id: str, credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> list:
    """Fetch the top-level replies (comments) on one of OUR OWN posts.

    Needs the ``threads_read_replies`` permission on the token. Returns a list of
    reply dicts (id, text, username, timestamp, is_reply_owned_by_me, hide_status,
    has_replies). Best-effort: raises RuntimeError on an API error so the caller
    can degrade.
    """
    _ensure_ready(credentials_file)
    raw = asyncio.run(_replies_async(media_id, credentials_file))
    if isinstance(raw, dict) and raw.get("error"):
        raise RuntimeError(str(raw["error"].get("message", raw["error"])))
    return list((raw or {}).get("data", [])) if isinstance(raw, dict) else []


async def _username_async(credentials_file: str) -> str:
    from pythreads.api import API
    from pythreads.credentials import Credentials
    from pythreads.threads import Threads

    with open(credentials_file, "r", encoding="utf-8") as f:
        credentials = Credentials.from_json(f.read())
    async with API(credentials=credentials) as api:
        url = Threads.build_graph_api_url("me", {"fields": "username"}, api._access_token())
        r = await api._get(url)
        return str(r.get("username", "")) if isinstance(r, dict) else ""


def account_username(credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> str:
    """The Threads @username the token authenticates as (to catch crossed creds)."""
    _ensure_ready(credentials_file)
    return asyncio.run(_username_async(credentials_file))


def publish_text(text: str, credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> str:
    """Publish a text thread via the official API.

    Args:
        text: The post body.
        credentials_file: Path to the account's OAuth credentials JSON.

    Returns:
        The published container id.

    Raises:
        ThreadsNotConfigured: If setup is incomplete (clear, actionable message).
        Exception: Any error raised by the Threads API during publishing.
    """
    if not text or not text.strip():
        raise ValueError("Post text must not be empty.")
    _ensure_ready(credentials_file)
    return asyncio.run(_publish_async(text, credentials_file))


async def _publish_reply_async(text: str, reply_to_id: str, credentials_file: str) -> str:
    from pythreads.api import API
    from pythreads.credentials import Credentials

    with open(credentials_file, "r", encoding="utf-8") as f:
        credentials = Credentials.from_json(f.read())
    async with API(credentials=credentials) as api:
        # reply_to_id is supported by create_container in recent pythreads; fall
        # back to a plain container if the running version lacks the kwarg.
        try:
            container_id = await api.create_container(text=text, reply_to_id=reply_to_id)
        except TypeError:
            container_id = await api.create_container(text=text)
        published_id = await api.publish_container(container_id)
        return str(published_id)


def publish_reply(text: str, reply_to_id: str,
                  credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> str:
    """Publish a reply to another post (best-effort).

    Replying to an arbitrary post needs its Threads media id. If the API rejects
    it, the caller degrades to a manual reply (the draft + link are shown).

    Raises:
        ThreadsNotConfigured: If setup is incomplete.
        Exception: Any Threads API error during publishing.
    """
    if not text or not text.strip():
        raise ValueError("Reply text must not be empty.")
    if not reply_to_id:
        raise ValueError("reply_to_id is required.")
    _ensure_ready(credentials_file)
    return asyncio.run(_publish_reply_async(text, reply_to_id, credentials_file))
