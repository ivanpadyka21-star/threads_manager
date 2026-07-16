"""Per-account, immutable HTTP client fingerprint.

The official Threads Graph API (``graph.threads.net``, used by this project) is
token-authenticated HTTPS. The only client-identifying signals it can observe
are the *source IP* (handled by the proxy layer, see
:mod:`mobile_e2e.core.http_session`) and the *HTTP headers* we send. Today every
account sends the identical default ``Python/aiohttp`` User-Agent from one
machine IP, which trivially correlates them.

This module assigns each account a **stable, unique, internally-consistent HTTP
client profile** (User-Agent + Accept-Language + a declared device), generated
once and then frozen in the DB so it never changes — consistency per account is
what builds trust. Regenerating a fingerprint would itself look suspicious, so
the store only ever fills an empty one.

Self-consistency rules baked into the profiles below:
- the device model, OS version and build string in the User-Agent all agree;
- ``Accept-Language`` is derived from a single ``locale``, so it never
  contradicts the UA (a ``uk_UA`` client sending ``ru-RU`` is a classic bot tell).

Honesty note: on the *official* Graph API this is a self-declared header
identity, not a cryptographic device attestation, and the UA is a weak signal
there (Meta identifies the caller by token + app id + IP). It removes the "all
identical bots" tell and keeps each account consistent. A full device-level
fingerprint (and a UA that actually matters) only applies to the private app
API — that would be a separate Stage 2, not this official-API path.
"""

from __future__ import annotations

import json
import random
from typing import Dict, List, Tuple

# Curated, internally-consistent mobile client profiles. Each User-Agent is a
# real mobile-browser UA whose device model + OS version + build string agree
# with the ``model`` / ``os`` fields. No app-codename tokens are mixed in — on
# the official API that would be incongruent (a server call posing as the app).
_DEVICE_PROFILES: List[Dict[str, str]] = [
    {"model": "iPhone15,3", "os": "iOS 17.5.1",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                   "Mobile/15E148 Safari/604.1"},
    {"model": "iPhone15,2", "os": "iOS 17.4.1",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
                   "Mobile/15E148 Safari/604.1"},
    {"model": "iPhone14,7", "os": "iOS 17.5",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                   "Mobile/15E148 Safari/604.1"},
    {"model": "iPhone13,2", "os": "iOS 16.7.8",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_8 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                   "Mobile/15E148 Safari/604.1"},
    {"model": "iPhone12,1", "os": "iOS 16.6",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                   "Mobile/15E148 Safari/604.1"},
    {"model": "iPhone16,1", "os": "iOS 17.5.1",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                   "Mobile/15E148 Safari/604.1"},
    {"model": "SM-S918B", "os": "Android 14",
     "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.6422.72 Mobile Safari/537.36"},
    {"model": "SM-S911B", "os": "Android 14",
     "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.6367.179 Mobile Safari/537.36"},
    {"model": "SM-A546E", "os": "Android 13",
     "user_agent": "Mozilla/5.0 (Linux; Android 13; SM-A546E) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.6367.179 Mobile Safari/537.36"},
    {"model": "Pixel 8 Pro", "os": "Android 14",
     "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.6422.72 Mobile Safari/537.36"},
    {"model": "Pixel 7", "os": "Android 14",
     "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.6422.72 Mobile Safari/537.36"},
    {"model": "22111317PG", "os": "Android 13",
     "user_agent": "Mozilla/5.0 (Linux; Android 13; 22111317PG) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/123.0.6312.118 Mobile Safari/537.36"},
]

# (locale, Accept-Language) pairs. Accept-Language is ALWAYS derived from the
# chosen locale so the two can never contradict each other. Biased toward the
# accounts' audience (Ukraine) with plausible variety.
_LOCALES: List[Tuple[str, str]] = [
    ("uk_UA", "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7"),
    ("uk_UA", "uk-UA,uk;q=0.9,ru;q=0.7,en;q=0.5"),
    ("ru_RU", "ru-RU,ru;q=0.9,uk;q=0.7,en;q=0.5"),
    ("en_US", "en-US,en;q=0.9,uk;q=0.7"),
]


def generate(seed: object = None) -> Dict[str, str]:
    """Create a fresh, internally-consistent client fingerprint.

    Args:
        seed: Optional value (e.g. account id) to seed selection for
            reproducibility. When ``None``, selection is fully random.

    Returns:
        A dict with ``user_agent``, ``accept_language``, ``locale``,
        ``device_model`` and ``os`` — ready to persist verbatim and apply as
        request headers. ``accept_language`` is guaranteed to match ``locale``.
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    profile = rng.choice(_DEVICE_PROFILES)
    locale, accept_language = rng.choice(_LOCALES)
    return {
        "user_agent": profile["user_agent"],
        "accept_language": accept_language,
        "locale": locale,
        "device_model": profile["model"],
        "os": profile["os"],
    }


def headers(fingerprint: Dict[str, str]) -> Dict[str, str]:
    """Render a fingerprint dict as the HTTP headers to send on every request."""
    if not fingerprint:
        return {}
    out: Dict[str, str] = {}
    if fingerprint.get("user_agent"):
        out["User-Agent"] = fingerprint["user_agent"]
    if fingerprint.get("accept_language"):
        out["Accept-Language"] = fingerprint["accept_language"]
    return out


def loads(raw: str) -> Dict[str, str]:
    """Parse a stored fingerprint JSON string, tolerating empty/broken values."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def dumps(fingerprint: Dict[str, str]) -> str:
    """Serialize a fingerprint dict for storage."""
    return json.dumps(fingerprint, ensure_ascii=False)
