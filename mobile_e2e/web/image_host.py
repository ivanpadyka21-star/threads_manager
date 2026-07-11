"""Stage a local photo at a public URL so Threads can fetch it.

Threads only publishes images by ``image_url`` — it downloads the file at
container-creation time, then serves its own copy. So we only need the image
public for a few seconds. We use litterbox (catbox's TEMPORARY host): the upload
auto-expires after ``expiry`` (default 1h), so the owner's photo never lingers on
a third-party host. No account/key required.
"""

from __future__ import annotations

import os

import requests

LITTERBOX = "https://litterbox.catbox.moe/resources/internals/api.php"


def stage(path: str, expiry: str = "1h") -> str:
    """Upload a local image, return a public URL that expires after ``expiry``.

    ``expiry`` is one of "1h", "12h", "24h", "72h". Raises on failure.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "rb") as f:
        r = requests.post(
            LITTERBOX,
            data={"reqtype": "fileupload", "time": expiry},
            files={"fileToUpload": (os.path.basename(path), f)},
            timeout=90,
        )
    r.raise_for_status()
    url = (r.text or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"image host rejected the upload: {url[:160]}")
    return url
