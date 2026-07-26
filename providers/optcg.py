"""OPTCGAPI provider -- free, no API key required.

Endpoints (verified against https://optcgapi.com/documentation):

    GET /api/sets/card/{card_id}/     e.g. OP15-118  (booster set cards)
    GET /api/decks/card/{card_id}/    e.g. ST01-001  (starter deck cards)

Both return a JSON *array* with one object per printing of that card, each
carrying ``market_price`` (USD) and ``inventory_price``, plus ``date_scraped``.

optcgapi does not document where its prices come from. The field names and the
English-only set coverage point at TCGplayer, but that is an inference, not a
documented fact -- treat these as US-market reference prices of unverified
provenance, refreshed roughly daily.
Everything vendor-specific -- URL shape, the sets/decks split, the array, the
field names, and how variants are spelled -- is contained in this file.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from providers.base import BASE_VARIANT, PriceProvider, Printing

#: The card-number token inside a card_name: "(118)", "(OP15-118)", "- OP09-119".
#: Stripping it leaves only the suffixes that describe the printing.
_NUMBER_TOKEN = re.compile(
    r"\(\s*(?:[A-Z]{1,4}\d{0,2}-)?\d{2,4}\s*\)|-\s*[A-Z]{1,4}\d{0,2}-\d{2,4}",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"\(([^)]+)\)")


class OPTCGProvider(PriceProvider):
    """Fetches One Piece TCG market prices from optcgapi.com."""

    name = "optcg"
    currency = "USD"
    region = "en"

    def __init__(self, base_url: str = "https://optcgapi.com/api", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json", "User-Agent": "tcg-tracker/1.0"})
        # Per-run memo: one HTTP call serves names, prices and the variant list.
        self._printings: dict[str, list[Printing]] = {}

    # --- helpers --------------------------------------------------------
    def _url(self, card_id: str) -> str:
        """Starter-deck cards (ST01-001) live under a different path than set cards."""
        group = "decks" if card_id.upper().startswith("ST") else "sets"
        return f"{self.base_url}/{group}/card/{card_id}/"

    @staticmethod
    def _label_of(card_name: str) -> str:
        """Derive a printing label from the vendor's card_name.

        ``"Monkey.D.Luffy (118)"``            -> ``"Normal"``
        ``"Monkey.D.Luffy (118) (Parallel)"`` -> ``"Parallel"``
        ``"Monkey.D.Luffy (119) (SP) (Gold)"``-> ``"SP Gold"``

        Derived rather than enumerated, so new variant names (One Piece keeps
        inventing them) work without a code change.
        """
        stripped = _NUMBER_TOKEN.sub("", card_name or "")
        suffixes = [s.strip() for s in _SUFFIX.findall(stripped) if s.strip()]
        return " ".join(suffixes) if suffixes else "Normal"

    @staticmethod
    def _slug(label: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        return slug or BASE_VARIANT

    @classmethod
    def _to_printings(cls, entries: list[dict[str, Any]]) -> list[Printing]:
        """Turn the raw array into Printings with unique variant keys."""
        printings: list[Printing] = []
        seen: dict[str, int] = {}

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("card_name") or "")
            label = cls._label_of(name)
            key = BASE_VARIANT if label == "Normal" else cls._slug(label)

            # The API can list two printings that reduce to the same label
            # (OP05-119 has both "(119) (Alternate Art)" and
            # "(OP05-119) (Alternate Art)"). Keep both; keys stay unique.
            count = seen.get(key, 0) + 1
            seen[key] = count
            variant = key if count == 1 else f"{key}-{count}"
            if count > 1:
                label = f"{label} #{count}"

            try:
                price = float(entry["market_price"])
                price = price if price > 0 else None
            except (KeyError, TypeError, ValueError):
                price = None

            image = str(entry.get("card_image") or "").strip() or None
            printings.append(Printing(variant=variant, label=label, price_usd=price,
                                      name=name or variant, image_url=image))

        return printings

    def _fetch(self, card_id: str) -> list[Printing]:
        """Fetch every printing for ``card_id``. Never raises; [] on failure."""
        if card_id in self._printings:
            return self._printings[card_id]

        try:
            response = self._session.get(self._url(card_id), timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            # Network error, timeout, non-2xx, or non-JSON body. Not memoised,
            # so a transient failure can be retried within the same run.
            return []

        if isinstance(payload, dict):  # tolerate a single-object response
            payload = [payload]
        if not isinstance(payload, list):
            return []

        printings = self._to_printings(payload)
        self._printings[card_id] = printings
        return printings

    # --- PriceProvider --------------------------------------------------
    def get_price(self, card_id: str, variant: str = BASE_VARIANT, grade: str = "raw") -> float | None:
        """Return the USD market price for one printing, or ``None`` on failure."""
        if grade != "raw":
            return None  # optcgapi prices raw cards only
        for printing in self._fetch(card_id):
            if printing.variant == variant:
                return printing.price_usd
        return None

    def list_printings(self, card_id: str) -> list[Printing]:
        """Every printing, cheapest-to-identify first (the normal print leads)."""
        return self._fetch(card_id)

    def get_card_name(self, card_id: str) -> str | None:
        """Return the printed card name, used to confirm a code while typing."""
        printings = self._fetch(card_id)
        return printings[0].name if printings else None
