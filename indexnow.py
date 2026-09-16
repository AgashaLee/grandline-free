"""IndexNow — instant "this URL changed" pings to Bing & Yandex.

IndexNow lets a site tell participating search engines (Bing, Yandex, Seznam,
Naver) the moment a page changes, instead of waiting for the next crawl. One
POST with a shared key notifies all of them (Google does not participate but
still uses the sitemap's <lastmod>).

How it works:
  1. We publish a key file at ``https://grandline.id/<key>.txt`` whose contents
     are the key itself. That proves we own the host (``dashboard.py`` serves it).
  2. To notify, we POST the key + a list of changed URLs to the IndexNow API.

Best-effort by design: a failed ping must never affect the site or a job, so
every call swallows its errors and is gated to the hosted deploy (``$PORT`` set)
so local runs and tests never hit the network.
"""

from __future__ import annotations

import json
import os
import urllib.request

#: 8–128 hex/alphanumeric chars. Env-overridable, but the default is committed so
#: the served key file and the pings always agree.
KEY = os.environ.get("INDEXNOW_KEY", "39f876e3dafff13c1cc02538828a63b8")

_SITE_URL = os.environ.get("SITE_URL", "https://grandline.id").rstrip("/")
_HOST = _SITE_URL.split("://", 1)[-1]
_ENDPOINT = "https://api.indexnow.org/indexnow"

#: The path the key file is served at, e.g. ``/39f8...b8.txt``.
KEY_PATH = f"/{KEY}.txt"


def key_file_body() -> bytes:
    """Contents of the verification key file (just the key)."""
    return KEY.encode("utf-8")


def _enabled() -> bool:
    # Only ping from the live deploy; never from a local run or a test.
    return bool(os.environ.get("PORT") or os.environ.get("INDEXNOW_FORCE"))


def submit(urls: list[str]) -> bool:
    """Notify IndexNow that ``urls`` changed. Returns True on a 2xx response,
    False otherwise (including when disabled). Never raises."""
    urls = [u for u in dict.fromkeys(urls) if u]  # de-dupe, keep order, drop blanks
    if not urls or not _enabled():
        return False
    payload = json.dumps({
        "host": _HOST,
        "key": KEY,
        "keyLocation": f"{_SITE_URL}{KEY_PATH}",
        "urlList": urls[:10000],  # API cap per request
    }).encode("utf-8")
    req = urllib.request.Request(
        _ENDPOINT, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            print(f"[indexnow] submitted {len(urls)} url(s) -> HTTP {resp.status}")
            return ok
    except Exception as exc:  # noqa: BLE001 - best-effort, must never propagate
        print(f"[indexnow] submit failed: {exc}")
        return False


def ping_hub_pages() -> bool:
    """Ping the daily-refreshed hub pages (home, market, meta, news, database).

    These change every day as prices, gainers/losers, meta and news update, so
    re-crawling them is worthwhile; the deep card/leader pages are discovered via
    the sitemap's per-page <lastmod>."""
    return submit([f"{_SITE_URL}{p}" for p in
                   ("/", "/market", "/meta", "/news", "/database")])
