"""USD -> IDR exchange rate, fetched once per run and cached like prices.

The URL and the response parser are kept behind small functions so swapping
sources is a config change plus (at most) one parser function.
"""

from __future__ import annotations

from typing import Any, Callable

import requests

import config
from cache import JsonCache


class FxError(RuntimeError):
    """Raised when no rate could be fetched and nothing is cached."""


def _parse_er_api(payload: Any, target: str) -> float | None:
    """open.er-api.com: ``{"result": "success", "rates": {"IDR": 16345.2, ...}}``"""
    if not isinstance(payload, dict) or payload.get("result") not in (None, "success"):
        return None
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return None
    return _as_positive_float(rates.get(target))


def _parse_exchangerate_host(payload: Any, target: str) -> float | None:
    """exchangerate.host / fawazahmed-style: ``{"rates": {...}}`` or ``{"IDR": ...}``"""
    if not isinstance(payload, dict):
        return None
    rates = payload.get("rates") if isinstance(payload.get("rates"), dict) else payload
    return _as_positive_float(rates.get(target))


def _as_positive_float(value: Any) -> float | None:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


#: Registered FX sources. Add a parser here and point ``FX_SOURCE`` at it.
FX_PARSERS: dict[str, Callable[[Any, str], float | None]] = {
    "er_api": _parse_er_api,
    "exchangerate_host": _parse_exchangerate_host,
}


def _url_for(base: str) -> str:
    """Build the endpoint for one base currency.

    ``FX_API_URL`` may contain ``{base}``; a URL without it is left alone so a
    fixed single-currency endpoint still works.
    """
    url = config.FX_API_URL
    return url.format(base=base.upper()) if "{base}" in url else url


def fetch_rate(base: str = "USD", target: str | None = None, timeout: float | None = None) -> float | None:
    """Fetch the live base->target rate. Returns ``None`` on any failure."""
    target = target or config.FX_TARGET_CURRENCY
    timeout = timeout if timeout is not None else config.HTTP_TIMEOUT_SECONDS
    parser = FX_PARSERS.get(config.FX_SOURCE, _parse_er_api)

    try:
        response = requests.get(_url_for(base), timeout=timeout, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    return parser(payload, target)


def get_rate(cache: JsonCache, base: str = "USD", target: str | None = None) -> tuple[float, str]:
    """Return ``(rate, source)`` converting ``base`` into the target currency.

    ``target`` defaults to the site-wide :data:`config.FX_TARGET_CURRENCY`; a
    caller (e.g. a logged-in user viewing in their own currency) may override it.
    ``source`` is ``cache``, ``live`` or ``stale``. Applies the same TTL rules
    as prices and falls back to the last known rate if the network is down.
    Raises :class:`FxError` only when there is no rate at all.
    """
    base = base.upper()
    target = (target or config.FX_TARGET_CURRENCY).upper()
    if base == target:
        return 1.0, "identity"

    key = config.fx_cache_key(base, target)

    cached = cache.get(key)
    if isinstance(cached, (int, float)) and cached > 0:
        return float(cached), "cache"

    rate = fetch_rate(base, target)
    if rate is not None:
        cache.set(key, rate)
        return rate, "live"

    stale = cache.get_stale(key)
    if isinstance(stale, (int, float)) and stale > 0:
        return float(stale), "stale"

    raise FxError(
        f"Could not fetch {base}->{target} from {_url_for(base)} and no cached rate is available."
    )


def get_usd_idr(cache: JsonCache) -> tuple[float, str]:
    """Backwards-compatible shorthand for the USD rate."""
    return get_rate(cache, "USD")


class RateBook:
    """Lazily fetches and remembers one rate per currency, per run."""

    def __init__(self, cache: JsonCache, target: str | None = None):
        self.cache = cache
        self.target = target            # None -> site-wide FX_TARGET_CURRENCY
        self.rates: dict[str, float] = {}
        self.sources: dict[str, str] = {}

    def __call__(self, currency: str) -> float:
        currency = currency.upper()
        if currency not in self.rates:
            rate, source = get_rate(self.cache, currency, self.target)
            self.rates[currency], self.sources[currency] = rate, source
        return self.rates[currency]
