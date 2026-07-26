"""Yuyu-tei (遊々亭) provider -- Japanese printings, priced in JPY.

Yuyu-tei is a Japanese card shop with no public API, so this reads their
server-rendered pages. It uses the SEARCH endpoint keyed by card code:

    https://yuyu-tei.jp/sell/opc/s/search?search_word=OP01-073   販売 (asking)
    https://yuyu-tei.jp/buy/opc/s/search?search_word=OP01-073    買取 (what they pay)

Why search and not the per-set page: the set page omits printings that are
sold out (在庫：×) and files the SP art outside the normal rarity sections, so
an OP01-073 SP that a collector owns simply isn't there. The search page lists
*every* printing of a code, in or out of stock. It costs one request per card
instead of one per set, which for a personal collection is a handful of
requests, all cached 24h.

Crucially, two printings can share a Japanese name -- the OP01-073 P-R
"(パラレル)" (~¥1,480) and the OP01-073 SP "(パラレル)" (~¥5,980) are both
"ドフラミンゴ(パラレル)". Only the RARITY tells them apart, so variants here are
keyed on the rarity code (read from each card's image alt text), not the name.

Being a scraper, this is more fragile than a JSON API: if Yuyu-tei restyles
their pages the selectors need updating. Everything that depends on their
markup is contained in this file.
"""

from __future__ import annotations

import re
import time
import urllib.parse

import requests

from providers.base import BASE_VARIANT, PriceProvider, Printing

#: One card block on a search results page. The card <img> carries the picture
#: URL (src) then "<code> <rarity> <name>" (alt); a badge repeats the code, an
#: <h4> the name, and a <strong> the price. Anchored on src+alt of the image.
_CARD_BLOCK = re.compile(
    r'src="([^"]+)"[^>]*alt="([A-Z0-9\-]+)\s+([A-Z\-]+)\s+[^"]*"'  # image URL, code, rarity
    r'.*?text-center my-2">\s*([A-Z0-9\-]+)\s*</span>'  # badge code (must match)
    r'.*?<h4[^>]*>\s*([^<]+?)\s*</h4>'               # ドンキホーテ・ドフラミンゴ(パラレル)
    r'.*?<strong[^>]*>\s*([\d,]+)\s*円',             # 5,980 円
    re.S,
)

#: Special name suffixes that denote a distinct printing regardless of rarity.
_NAME_VARIANTS = {
    "ホロなし": "no-holo",
    "ホロ無し": "no-holo",
    "コミックパラレル": "manga",
    "マンガ": "manga",
}


class YuyuteiProvider(PriceProvider):
    """Scrapes Japanese One Piece card prices from yuyu-tei.jp."""

    name = "yuyutei"
    currency = "JPY"
    region = "jp"

    def __init__(
        self,
        base_url: str = "https://yuyu-tei.jp",
        mode: str = "sell",
        timeout: float = 15.0,
        delay_seconds: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.mode = "buy" if str(mode).lower() == "buy" else "sell"
        self.timeout = timeout
        self.delay_seconds = delay_seconds
        # Distinguish 販売 from 買取 in the cache: they are different numbers.
        self.name = f"yuyutei-{self.mode}"
        self._session = requests.Session()
        self._session.headers.update({
            # Present as a real desktop Chrome. A bot-ish UA gets 403; a datacenter
            # host (e.g. Railway) is more likely to be let through with a full,
            # browser-like header set. (If the block is purely by IP, none of this
            # helps and the requests need a residential/JP proxy instead.)
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": "https://yuyu-tei.jp/",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        })
        self._cards: dict[str, list[Printing]] = {}
        self._last_fetch = 0.0

    # --- helpers --------------------------------------------------------
    def _url(self, card_id: str) -> str:
        query = urllib.parse.urlencode({"search_word": card_id, "rare": "", "type": "", "kizu": "0"})
        return f"{self.base_url}/{self.mode}/opc/s/search?{query}"

    @staticmethod
    def _variant_of(rarity: str, name: str) -> tuple[str, str]:
        """Return ``(variant_key, label)`` from the rarity code and name.

        Rarity is authoritative because two printings can share a name: the
        P-R and the SP are both "(パラレル)". Keys are aligned with the English
        source so a parallel is ``parallel`` and an SP is ``sp`` in both regions.
        """
        rarity = rarity.upper()

        # A distinctive name suffix wins (e.g. ホロなし, which shares rarity R
        # with the normal print but is a separate, cheaper card).
        for needle, key in _NAME_VARIANTS.items():
            if needle in name:
                return key, needle

        if rarity == "SP":
            return "sp", "SP"
        if rarity.startswith("P-") or "パラレル" in name:
            return "parallel", "パラレル"
        return BASE_VARIANT, "通常 (Normal)"

    @classmethod
    def _parse(cls, html: str) -> dict[str, list[Printing]]:
        """Turn a search-results page into ``{card_id: [Printing, ...]}``."""
        cards: dict[str, list[Printing]] = {}
        seen: dict[tuple[str, str], int] = {}

        for image, alt_code, rarity, badge_code, name, price_text in _CARD_BLOCK.findall(html):
            if alt_code.upper() != badge_code.upper():
                continue  # image/badge mismatch: skip rather than mis-pair
            code = badge_code.upper()
            variant, label = cls._variant_of(rarity, name)

            count = seen.get((code, variant), 0) + 1
            seen[(code, variant)] = count
            if count > 1:  # two printings that still reduce to one key
                variant, label = f"{variant}-{count}", f"{label} #{count}"

            try:
                price = float(price_text.replace(",", ""))
            except ValueError:
                price = None

            cards.setdefault(code, []).append(
                Printing(variant=variant, label=f"{label} [{rarity}]", price_usd=price,
                         name=name, image_url=(image.strip() or None))
            )
        return cards

    def _fetch_card(self, card_id: str) -> list[Printing]:
        """Fetch every printing of one card via search. Never raises; [] on failure."""
        code = card_id.upper()
        if code in self._cards:
            return self._cards[code]

        # Be a polite scraper: space out requests.
        elapsed = time.monotonic() - self._last_fetch
        if self._last_fetch and elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

        try:
            response = self._session.get(self._url(code), timeout=self.timeout)
            self._last_fetch = time.monotonic()
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            # Search matches by substring, so a page can hold other codes too.
            printings = self._parse(response.text).get(code, [])
        except (requests.RequestException, ValueError):
            return []  # not memoised: a transient failure may be retried

        self._cards[code] = printings
        return printings

    def _printings(self, card_id: str) -> list[Printing]:
        return self._fetch_card(card_id)

    # --- PriceProvider --------------------------------------------------
    def get_price(self, card_id: str, variant: str = BASE_VARIANT, grade: str = "raw") -> float | None:
        """Return the JPY price for one printing, or ``None`` on failure."""
        if grade != "raw":
            return None  # Yuyu-tei lists raw cards only
        for printing in self._printings(card_id):
            if printing.variant == variant:
                return printing.price_usd
        return None

    def list_printings(self, card_id: str) -> list[Printing]:
        return self._printings(card_id)

    def get_card_name(self, card_id: str) -> str | None:
        printings = self._printings(card_id)
        return printings[0].name if printings else None
