"""Price provider interface, caching decorator, and factory.

This is the seam of the whole application. Everything downstream (portfolio
maths, reporting) depends only on :class:`PriceProvider`. No module outside
``providers/`` may import a concrete provider or know a vendor's URL, field
names, rate limits, or auth scheme -- exactly like a broker adapter in a
multi-exchange trading bot, where the strategy talks to ``Broker`` and never to
Binance directly.

To add a provider: write one module in this package, register it in
``PROVIDERS`` below, and set ``PRICE_PROVIDER`` in config.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import config
from cache import JsonCache

#: The ordinary printing of a card. Anything else (parallel, alternate art,
#: manga, SP...) is a distinct variant with its own price, and often a wildly
#: different one -- a parallel can be worth 10x the normal print.
BASE_VARIANT = "base"

#: An ungraded card. A graded slab (psa-10, psa-9, bgs-10...) is a separate
#: value that only a grade-aware provider can price.
RAW_GRADE = "raw"


@dataclass(frozen=True)
class Printing:
    """One physical version of a card, as offered by a price source.

    ``variant`` is the stable key stored in collection.csv; ``label`` and
    ``name`` are for display. Providers are responsible for turning their own
    vendor naming into these, so variant vocabulary never leaks upward.
    """

    variant: str
    label: str
    price_usd: float | None
    name: str
    #: URL of the card's picture on the source's CDN, if it exposes one.
    image_url: str | None = None


class PriceProvider(ABC):
    """A source of card market prices, quoted in USD.

    Implementations must be total: any failure (network, HTTP error, unknown
    card, malformed payload) is reported as ``None``, never as an exception.
    The caller distinguishes "no price" from "crashed" only by this contract.
    """

    #: Short stable identifier. Used as the cache namespace, so changing it
    #: invalidates that provider's cached prices.
    name: str = "base"

    #: Currency this provider quotes in. Conversion to IDR happens above, so a
    #: JPY source never has to know about USD or about rupiah.
    currency: str = "USD"

    #: Which printing of the game this source prices: "en" or "jp".
    region: str = "en"

    #: True if this source can price graded slabs (PSA/BGS/...). Raw-only sources
    #: leave it False and the tracker refuses to price a graded card with them,
    #: rather than passing off a raw price as a graded one.
    grades: bool = False

    @abstractmethod
    def get_price(self, card_id: str, variant: str = BASE_VARIANT, grade: str = RAW_GRADE) -> float | None:
        """Return the market price for one printing in ``self.currency``.

        A source with no concept of variants may ignore ``variant``; a raw-only
        source (``grades = False``) is only ever called with ``grade = "raw"``.
        """
        raise NotImplementedError

    def get_price_usd(self, card_id: str, variant: str = BASE_VARIANT) -> float | None:
        """Deprecated alias kept so older code and notes keep working."""
        if self.currency != "USD":
            raise ValueError(f"{self.name} quotes in {self.currency}; use get_price().")
        return self.get_price(card_id, variant)

    def list_printings(self, card_id: str) -> list[Printing]:
        """Every printing of ``card_id``, for choosing between them at entry.

        Optional. The default treats the card as having a single printing, so a
        new provider only ever has to implement ``get_price``.
        """
        price = self.get_price(card_id)
        if price is None:
            return []
        return [Printing(BASE_VARIANT, "Normal", price, card_id)]

    def get_card_name(self, card_id: str) -> str | None:
        """Return the card's printed name, if the source exposes one.

        Optional: a convenience for confirming a card code while typing it in,
        not part of the pricing contract.
        """
        return None


class CachedPriceProvider(PriceProvider):
    """Wraps any :class:`PriceProvider` with a TTL cache and stale fallback.

    Caching lives here rather than inside each provider so that every future
    provider gets it for free and behaves identically:

    1. fresh cache hit (< TTL)      -> return cached price, no network call
    2. miss/expired, fetch succeeds -> store and return the new price
    3. miss/expired, fetch fails    -> fall back to the stale cached price
    4. nothing cached and it fails  -> ``None`` (caller marks the card ERROR)

    Cache keys are namespaced by provider name, so switching to a paid source
    never serves prices that were scraped from the free one.
    """

    def __init__(self, inner: PriceProvider, cache: JsonCache):
        self.inner = inner
        self.cache = cache
        self.name = inner.name
        self.currency = inner.currency
        self.region = inner.region
        self.grades = inner.grades

    def cache_key(self, card_id: str, variant: str = BASE_VARIANT, grade: str = RAW_GRADE) -> str:
        """Each printing+grade is priced separately, so each gets its own key."""
        base = f"{self.name}:{card_id}:{variant}"
        return base if grade == RAW_GRADE else f"{base}:{grade}"

    def get_price(self, card_id: str, variant: str = BASE_VARIANT, grade: str = RAW_GRADE) -> float | None:
        key = self.cache_key(card_id, variant, grade)

        cached = self.cache.get(key)
        if isinstance(cached, (int, float)):
            return float(cached)

        price = self.inner.get_price(card_id, variant, grade)
        if price is not None:
            self.cache.set(key, float(price))
            return float(price)

        stale = self.cache.get_stale(key)
        if isinstance(stale, (int, float)):
            return float(stale)
        return None

    def list_printings(self, card_id: str) -> list[Printing]:
        """Delegate, and warm the cache with every printing's price."""
        printings = self.inner.list_printings(card_id)
        for printing in printings:
            if printing.price_usd is not None:
                self.cache.set(self.cache_key(card_id, printing.variant), printing.price_usd)
        return printings

    def get_card_name(self, card_id: str) -> str | None:
        """Delegate to the wrapped provider (names are not worth caching)."""
        return self.inner.get_card_name(card_id)


# --- factory ------------------------------------------------------------
# The single place where a concrete provider is named. Add new entries here.
def _build_optcg() -> PriceProvider:
    from providers.optcg import OPTCGProvider  # imported lazily to keep deps local

    return OPTCGProvider(
        base_url=config.OPTCG_BASE_URL,
        timeout=config.HTTP_TIMEOUT_SECONDS,
    )


def _build_yuyutei() -> PriceProvider:
    from providers.yuyutei import YuyuteiProvider

    return YuyuteiProvider(
        base_url=config.YUYUTEI_BASE_URL,
        mode=config.YUYUTEI_PRICE_MODE,
        timeout=config.HTTP_TIMEOUT_SECONDS,
        delay_seconds=config.YUYUTEI_DELAY_SECONDS,
    )


def _build_pricecharting() -> PriceProvider:
    from providers.pricecharting import PriceChartingProvider

    return PriceChartingProvider(api_key=config.PROVIDER_API_KEY, timeout=config.HTTP_TIMEOUT_SECONDS)


PROVIDERS: dict[str, callable] = {
    "optcg": _build_optcg,                # English raw printings, USD, free
    "yuyutei": _build_yuyutei,            # Japanese raw printings, JPY, free (scrape)
    "pricecharting": _build_pricecharting,  # US + GRADED (PSA/BGS), USD, needs a token
}


def get_provider(name: str | None = None, cache: JsonCache | None = None) -> PriceProvider:
    """Return a named provider, wrapped in the shared cache layer.

    Args:
        name: Provider key; defaults to ``config.PRICE_PROVIDER``.
        cache: Cache to use; a default :class:`JsonCache` is created if omitted.

    Raises:
        ValueError: if the configured provider name is not registered.
    """
    key = (name or config.PRICE_PROVIDER).strip().lower()
    try:
        factory = PROVIDERS[key]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS)) or "(none)"
        raise ValueError(f"Unknown price provider {key!r}. Registered providers: {known}") from None

    return CachedPriceProvider(factory(), cache or JsonCache())


def get_provider_for(region: str, cache: JsonCache | None = None) -> PriceProvider:
    """Return the provider that prices a given region ("jp" or "en")."""
    key = (region or config.DEFAULT_REGION).strip().lower()
    try:
        name = config.PROVIDER_BY_REGION[key]
    except KeyError:
        known = ", ".join(sorted(config.PROVIDER_BY_REGION))
        raise ValueError(f"Unknown region {key!r}. Known regions: {known}") from None
    return get_provider(name, cache)


class ProviderPool:
    """One cached provider per region, built on first use."""

    def __init__(self, cache: JsonCache):
        self.cache = cache
        self._by_region: dict[str, PriceProvider] = {}

    def __call__(self, region: str) -> PriceProvider:
        key = (region or config.DEFAULT_REGION).strip().lower()
        if key not in self._by_region:
            self._by_region[key] = get_provider_for(key, self.cache)
        return self._by_region[key]
