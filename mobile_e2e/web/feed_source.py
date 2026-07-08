"""Best-effort live feed source: Threads keyword search.

Meta's Threads API exposes a ``keyword_search`` endpoint that returns public
posts matching a query. Access depends on the app's granted permissions
(``threads_keyword_search``) and may not be available for every token, so this
module is strictly *best-effort*: if the endpoint is unavailable it raises
:class:`FeedSearchUnavailable` with a clear message and the caller falls back to
manually-pasted competitor examples (which always work).

Public results do not include other users' view counts, so engagement numbers
may be partial — manual samples (read off-screen) are richer for analysis.

Like :mod:`threads_client`, pythreads is imported lazily (it validates SSL env
vars at import time), so importing this module never fails on an unconfigured box.
"""

from __future__ import annotations

import asyncio
from typing import List

from mobile_e2e.web.threads_client import DEFAULT_CREDENTIALS_FILE, _ensure_ready


class FeedSearchUnavailable(Exception):
    """Raised when live keyword search cannot be performed (permission/endpoint)."""


# Fields we try to read for each matching post. Kept small and defensive: the
# API may omit some, and never returns other users' view counts.
_SEARCH_FIELDS = "id,text,username,permalink,like_count,replies_count,reposts_count"


async def _search_async(keyword: str, limit: int, credentials_file: str) -> dict:
    from pythreads.api import API
    from pythreads.credentials import Credentials
    from pythreads.threads import Threads

    with open(credentials_file, "r", encoding="utf-8") as f:
        credentials = Credentials.from_json(f.read())
    async with API(credentials=credentials) as api:
        url = Threads.build_graph_api_url(
            "keyword_search",
            {"q": keyword, "search_type": "TOP", "fields": _SEARCH_FIELDS,
             "limit": str(limit)},
            api._access_token(),
        )
        return await api._get(url)


def _parse(raw: dict) -> List[dict]:
    out: List[dict] = []
    for item in (raw or {}).get("data", []):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "author": item.get("username") or "",
            "text": text,
            "likes": int(item.get("like_count") or 0),
            "replies": int(item.get("replies_count") or 0),
            "views": 0,  # public search does not expose others' view counts
            "url": item.get("permalink") or "",
            "source": "keyword_search",
        })
    return out


def search(keyword: str, limit: int = 15,
           credentials_file: str = DEFAULT_CREDENTIALS_FILE) -> List[dict]:
    """Return public posts matching ``keyword`` (best-effort).

    Raises:
        ThreadsNotConfigured: if the Threads integration is not set up.
        FeedSearchUnavailable: if the search endpoint/permission is not available.
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise ValueError("Search keyword must not be empty.")
    _ensure_ready(credentials_file)
    try:
        raw = asyncio.run(_search_async(keyword, limit, credentials_file))
    except Exception as exc:  # noqa: BLE001 - normalise into a clear signal
        raise FeedSearchUnavailable(
            "Live keyword search is unavailable for this token "
            f"(needs the threads_keyword_search permission): {str(exc)[:200]}"
        ) from exc
    if isinstance(raw, dict) and raw.get("error"):
        raise FeedSearchUnavailable(str(raw["error"].get("message", raw["error"])))
    return _parse(raw)
