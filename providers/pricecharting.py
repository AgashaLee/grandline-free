"""PriceCharting provider -- graded (PSA/BGS/CGC) and US-market raw prices.

STATUS: ready but not wired. It is registered so switching it on is a config
change, but it is nobody's default and does nothing without an API token. It
has not been run against the live API (that needs a paid token), so treat the
field mapping below as documented-but-unverified until you test it with a key.

Why an API and not scraping: PriceCharting sits behind a Cloudflare bot
challenge, so its pages can't be read the way Yuyu-tei's can. It offers a paid
JSON API instead:

    GET https://www.pricecharting.com/api/product?t=<TOKEN>&q=<query>
    GET https://www.pricecharting.com/api/product?t=<TOKEN>&id=<product_id>

Set the token in config as PROVIDER_API_KEY (never hard-code it here).

Two gotchas this file exists to contain:

1. Prices are integers of PENNIES: 1732 means $17.32.

2. For trading cards, PriceCharting REUSES its old video-game field names for
   grades. This is the mapping (per their card docs); it is easy to get wrong,
   so it lives in one dict:

       loose-price        -> Ungraded (raw)
       cib-price          -> Grade 7
       new-price          -> Grade 8
       graded-price       -> Grade 9  (~PSA 9)
       box-only-price     -> Grade 9.5
       manual-only-price  -> PSA 10

3. PriceCharting keys on its OWN product ids, not One Piece card codes, so a
   card_id has to be resolved via search first. That match is fuzzy; for real
   use you likely want to resolve+store each card's product_id once rather than
   trust a live search. See resolve_product_id().
"""

from __future__ import annotations

import urllib.parse

import requests

from providers.base import BASE_VARIANT, RAW_GRADE, PriceProvider, Printing

#: Our grade key -> PriceCharting's (mislabelled) JSON field. Several keys can
#: map to one field (psa-9 and grade-9 are both graded-price); order matters
#: only for list_printings, which shows the FIRST key per field -- so the
#: user-facing PSA labels are listed ahead of the generic grade-N aliases.
GRADE_FIELD: dict[str, str] = {
    RAW_GRADE: "loose-price",
    "ungraded": "loose-price",
    "grade-7": "cib-price",
    "grade-8": "new-price",
    "psa-9": "graded-price",
    "grade-9": "graded-price",
    "grade-9.5": "box-only-price",
    "psa-10": "manual-only-price",
    "grade-10": "manual-only-price",
}


class PriceChartingProvider(PriceProvider):
    """US-market prices including graded slabs, via the PriceCharting API."""

    name = "pricecharting"
    currency = "USD"
    region = "en"
    grades = True

    def __init__(self, api_key: str | None, base_url: str = "https://www.pricecharting.com", timeout: float = 15.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json", "User-Agent": "tcg-tracker/1.0"})
        self._products: dict[str, dict | None] = {}

    # --- helpers --------------------------------------------------------
    @staticmethod
    def _usd(pennies) -> float | None:
        """PriceCharting quotes integer pennies; convert to dollars."""
        try:
            value = int(pennies)
        except (TypeError, ValueError):
            return None
        return value / 100.0 if value > 0 else None

    def _get(self, path: str, **params) -> dict | None:
        if not self.api_key:
            # No token -> behave like any unavailable source: None, never raise.
            return None
        params["t"] = self.api_key
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            response = self._session.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def resolve_product(self, card_id: str) -> dict | None:
        """Find the PriceCharting product for a card code (fuzzy search).

        Cached per run. In production you would resolve once and store the
        product-id, since search matching is not guaranteed exact.
        """
        if card_id in self._products:
            return self._products[card_id]
        # 'one piece OP01-073' narrows the search to the right game+card.
        product = self._get("/api/product", q=f"one piece {card_id}")
        self._products[card_id] = product
        return product

    # --- PriceProvider --------------------------------------------------
    def get_price(self, card_id: str, variant: str = BASE_VARIANT, grade: str = RAW_GRADE) -> float | None:
        """Return the USD price for the requested grade, or ``None``.

        Note: PriceCharting's product model does not distinguish One Piece
        variants (parallel/SP) the way the card game does, so ``variant`` is
        currently ignored here -- a limitation to resolve before trusting it
        for alt-art valuations.
        """
        field = GRADE_FIELD.get(grade)
        if field is None:
            return None  # a grade we don't have a mapping for

        product = self.resolve_product(card_id)
        if not product:
            return None
        return self._usd(product.get(field))

    def list_printings(self, card_id: str) -> list[Printing]:
        """One entry per grade PriceCharting reports for the card."""
        product = self.resolve_product(card_id)
        if not product:
            return []
        seen: set[str] = set()
        out: list[Printing] = []
        for grade, field in GRADE_FIELD.items():
            if field in seen:
                continue
            seen.add(field)
            price = self._usd(product.get(field))
            if price is not None:
                label = "Ungraded" if grade == RAW_GRADE else grade.upper()
                out.append(Printing(variant=grade, label=label, price_usd=price,
                                    name=str(product.get("product-name") or card_id)))
        return out

    def get_buy_url(self, card_id: str, variant: str = BASE_VARIANT, grade: str = RAW_GRADE, condition: str = "nm") -> str | None:
        """Return a PriceCharting affiliate search URL for this card."""
        query = urllib.parse.quote_plus(f"one piece {card_id}")
        return f"{self.base_url}/search-products?q={query}&affiliate_id=YOUR_ID_HERE"
