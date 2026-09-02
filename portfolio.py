"""Collection loading and profit/loss maths.

Pure business logic: it accepts a :class:`PriceProvider` and a rate, and knows
nothing about HTTP, vendors, or terminal output. That is what makes both the
provider swap and the (later) Pokemon support drop-in changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import config
from database import get_db
from providers.base import BASE_VARIANT, PriceProvider

REQUIRED_COLUMNS = ("name", "card_id", "quantity")
#: Older files call the price column ``buy_price_idr`` and imply rupiah; newer
#: ones use ``buy_price`` + ``buy_currency``. Both are read; the new pair is
#: always written.
LEGACY_PRICE_COLUMN = "buy_price_idr"
WRITE_COLUMNS = ("name", "card_id", "region", "variant", "grade", "condition",
                 "buy_price", "buy_currency", "quantity")

#: A card that has not been professionally graded. Anything else ("psa-10",
#: "psa-9", "bgs-10"...) is a graded slab whose value is unrelated to the raw
#: card and only a grade-aware source (e.g. PriceCharting) can price.
RAW_GRADE = "raw"


@dataclass(frozen=True)
class Holding:
    """One row of collection.csv.

    ``variant`` distinguishes printings that share a card code -- a parallel
    can be worth ten times the normal print of the same card.

    ``region`` picks which printing of the game this is: "jp" cards are priced
    in yen from a Japanese shop, "en" cards in dollars from an English source.
    They are different products and their prices are unrelated.
    """

    name: str
    card_id: str
    buy_price: float  # per single card, not per lot, in ``buy_currency``
    quantity: int
    variant: str = BASE_VARIANT
    region: str = config.DEFAULT_REGION
    grade: str = RAW_GRADE
    condition: str = "nm"
    #: What the buyer actually paid in. Stored per row so that changing the
    #: display currency later never rewrites purchase history. Resolved when the
    #: Holding is built (not frozen at import), so it tracks the live setting.
    buy_currency: str = field(default_factory=lambda: config.DISPLAY_CURRENCY)

    @property
    def total_buy(self) -> float:
        """Cost of the whole lot, in ``buy_currency``."""
        return self.buy_price * self.quantity


@dataclass(frozen=True)
class PricedHolding:
    """A holding valued at the current market price, in the display currency.

    Three currencies can be in play at once and are kept distinct:
      * ``currency``       -- what the price source quotes (JPY, USD)
      * ``buy_currency``   -- what the owner paid in (on the holding)
      * ``display_currency`` -- what the report is rendered in

    ``market_native is None`` means the price could not be obtained; the row is
    reported as ERROR and excluded from the totals.
    """

    holding: Holding
    market_native: float | None
    currency: str
    market_rate: float          # source currency -> display currency
    buy_rate: float = 1.0       # buy currency -> display currency
    display_currency: str = field(default_factory=lambda: config.DISPLAY_CURRENCY)
    #: Trade-in / buyback price per card in the source currency (JP only), and
    #: whether the source currently stocks it. Both optional (None = unknown).
    buyback_native: float | None = None
    in_stock: bool | None = None
    buy_url: str | None = None

    @property
    def ok(self) -> bool:
        return self.market_native is not None

    @property
    def invested(self) -> float:
        """What the lot cost, converted into the display currency."""
        return self.holding.total_buy * self.buy_rate

    @property
    def market_unit(self) -> float | None:
        """Current market value of ONE card, in the display currency."""
        return None if self.market_native is None else self.market_native * self.market_rate

    @property
    def value(self) -> float | None:
        """Current market value of the whole lot, in the display currency."""
        unit = self.market_unit
        return None if unit is None else unit * self.holding.quantity

    @property
    def buyback_value(self) -> float | None:
        """Realistic trade-in value of the whole lot, in the display currency
        (source buyback price -> display, via the market rate). None if the
        source has no buy side for this printing."""
        if self.buyback_native is None:
            return None
        return self.buyback_native * self.market_rate * self.holding.quantity

    @property
    def pl(self) -> float | None:
        value = self.value
        return None if value is None else value - self.invested

    @property
    def pl_pct(self) -> float | None:
        pl = self.pl
        cost = self.invested
        if pl is None or cost <= 0:
            return None
        return pl / cost * 100.0


@dataclass(frozen=True)
class PortfolioTotals:
    invested: float
    value: float
    error_count: int
    display_currency: str = field(default_factory=lambda: config.DISPLAY_CURRENCY)
    #: Sum of trade-in (buyback) values for the cards that have one -- the
    #: realistic "sell it all today" figure. 0 when no source has a buy side.
    sell_value: float = 0.0

    @property
    def pl(self) -> float:
        return self.value - self.invested

    @property
    def pl_pct(self) -> float | None:
        if self.invested <= 0:
            return None
        return self.pl / self.invested * 100.0


def load_collection(user_id: str) -> list[Holding]:
    """Read holdings from the database."""
    db = get_db()
    # Order by ID so the array index is stable
    rows = db.execute("SELECT * FROM collections WHERE user_id = ? ORDER BY id ASC", (user_id,)).fetchall()
    
    holdings: list[Holding] = []
    for row in rows:
        holdings.append(
            Holding(
                name=row["name"],
                card_id=row["card_id"],
                buy_price=row["buy_price"],
                buy_currency=row["buy_currency"],
                quantity=row["quantity"],
                variant=row["variant"],
                region=row["region"],
                grade=row["grade"],
                condition=row["condition"],
            )
        )
    return holdings


def append_holding(user_id: str, holding: Holding) -> None:
    """Append one card to the database."""
    db = get_db()
    db.execute("""
        INSERT INTO collections (user_id, card_id, name, region, variant, grade, condition, buy_price, buy_currency, quantity)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, holding.card_id, holding.name, holding.region, holding.variant, holding.grade, holding.condition, holding.buy_price, holding.buy_currency, holding.quantity))
    db.commit()


class CollectionChanged(RuntimeError):
    """The targeted row is gone or no longer what the caller thought it was."""


def get_market_data(card_id: str, provider: str = "tcgplayer") -> dict:
    """Return latest market data for a card, or None if not found."""
    db = get_db()
    row = db.execute("SELECT * FROM market_data WHERE card_id = ? AND provider = ? ORDER BY date DESC LIMIT 1", (card_id, provider)).fetchone()
    if not row:
        return None
    return dict(row)

# ---------------------------------------------------------
# META DECKS
# ---------------------------------------------------------

def save_meta_deck(deck: dict):
    """Save a meta deck and its cards to the database."""
    db = get_db()
    # Insert deck
    db.execute('''
        INSERT OR REPLACE INTO meta_decks (id, event_date, country, event_name, event_type, players, winner, leader_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (deck["id"], deck.get("event_date", ""), deck.get("country", ""), deck.get("event_name", ""), 
          deck.get("event_type", ""), deck.get("players", ""), deck.get("winner", ""), deck.get("leader_id", "")))
    
    # Delete old cards for this deck
    db.execute('DELETE FROM meta_deck_cards WHERE deck_id = ?', (deck["id"],))
    
    # Insert new cards
    for card in deck.get("cards", []):
        db.execute('''
            INSERT INTO meta_deck_cards (deck_id, card_id, quantity)
            VALUES (?, ?, ?)
        ''', (deck["id"], card["card_id"], card["quantity"]))
    
    db.commit()

def get_meta_decks() -> list:
    """Fetch all meta decks with their card lists."""
    db = get_db()
    deck_rows = db.execute("SELECT * FROM meta_decks").fetchall()
    
    decks = []
    for r in deck_rows:
        deck = dict(r)
        card_rows = db.execute("SELECT card_id, quantity FROM meta_deck_cards WHERE deck_id = ?", (deck["id"],)).fetchall()
        deck["cards"] = [dict(cr) for cr in card_rows]
        decks.append(deck)
        
    return decks


def _verify_db(user_id: str, index: int, card_id: str, variant: str) -> dict:
    db = get_db()
    rows = db.execute("SELECT * FROM collections WHERE user_id = ? ORDER BY id ASC", (user_id,)).fetchall()
    if not 0 <= index < len(rows):
        raise CollectionChanged("That card is no longer in your collection - refresh the page.")
    
    row = rows[index]
    if row["card_id"] != card_id.upper() or row["variant"] != variant:
        raise CollectionChanged("Your collection changed since this page loaded - refresh it.")
    return row


def update_holding(
    user_id: str,
    index: int,
    card_id: str,
    variant: str,
    buy_price: float | None = None,
    quantity: int | None = None,
    grade: str | None = None,
    condition: str | None = None,
    buy_currency: str | None = None,
) -> Holding:
    """Change the buy price, quantity, grade, condition and/or buy currency of one row."""
    row = _verify_db(user_id, index, card_id, variant)

    new_price = row["buy_price"] if buy_price is None else float(buy_price)
    new_qty = row["quantity"] if quantity is None else int(quantity)
    new_grade = row["grade"] if grade is None else str(grade).lower()
    new_cond = row["condition"] if condition is None else str(condition).lower()
    new_curr = row["buy_currency"] if buy_currency is None else str(buy_currency).upper()

    if new_price < 0:
        raise ValueError("Buy price cannot be negative.")
    if new_qty < 1:
        raise ValueError("Quantity must be at least 1. Remove the card instead.")

    db = get_db()
    db.execute("""
        UPDATE collections
        SET buy_price = ?, quantity = ?, grade = ?, condition = ?, buy_currency = ?
        WHERE id = ?
    """, (new_price, new_qty, new_grade, new_cond, new_curr, row["id"]))
    db.commit()

    return Holding(row["name"], row["card_id"], new_price, new_qty, variant, row["region"], new_grade, new_cond, new_curr)


def remove_holding(
    user_id: str,
    index: int,
    card_id: str,
    variant: str,
    quantity: int | None = None,
) -> Holding | None:
    """Sell all or part of a row. Returns what remains, or ``None`` if gone."""
    row = _verify_db(user_id, index, card_id, variant)

    sold = row["quantity"] if quantity is None else int(quantity)
    if sold < 1:
        raise ValueError("Sell at least one card.")
    sold = min(sold, row["quantity"])

    remaining = row["quantity"] - sold
    db = get_db()
    
    if remaining > 0:
        db.execute("UPDATE collections SET quantity = ? WHERE id = ?", (remaining, row["id"]))
        db.commit()
        return Holding(row["name"], row["card_id"], row["buy_price"], remaining, variant, row["region"], row["grade"], row["condition"], row["buy_currency"])
    else:
        db.execute("DELETE FROM collections WHERE id = ?", (row["id"],))
        db.commit()
        return None


def price_collection(
    holdings: list[Holding],
    provider_for,
    rate_for,
    on_priced=None,
    display_currency: str | None = None,
) -> list[PricedHolding]:
    """Value every holding, routing each to the source for its region.

    Args:
        provider_for: ``(region) -> PriceProvider``.
        rate_for: ``(currency) -> float`` giving that currency's rate to IDR.
        on_priced: optional callback ``(PricedHolding) -> None`` for progress UI.
    """
    dc = display_currency or config.DISPLAY_CURRENCY
    priced: list[PricedHolding] = []
    for holding in holdings:
        try:
            provider = provider_for(holding.region)
            # A graded card must not be priced by a raw-only source -- that would
            # report a raw price as if it were the slab's value. Leave it ERROR
            # until a grade-aware provider (e.g. PriceCharting) is configured.
            if holding.grade != RAW_GRADE and not getattr(provider, "grades", False):
                price = None
            else:
                price = provider.get_price(holding.card_id, holding.variant, holding.grade, holding.condition)
            currency, rate = provider.currency, rate_for(provider.currency)
        except Exception:
            # An unknown region or an unavailable rate marks the row ERROR
            # rather than killing the whole run.
            price, currency, rate = None, "?", 0.0

        # The purchase price converts separately: it was paid in its own
        # currency, which may differ from both the source's and the display's.
        try:
            buy_rate = rate_for(holding.buy_currency)
        except Exception:
            buy_rate = 0.0

        # Trade-in (買取) price and in-stock flag, when the source has a buy side
        # (Yuyu-tei does; the EN source doesn't). Guarded so it can never break
        # the run, and only attempted for a card that actually priced.
        buyback = in_stock = None
        if price is not None:
            try:
                buyback = provider.get_buyback(holding.card_id, holding.variant, holding.grade, holding.condition)
                in_stock = provider.get_stock(holding.card_id, holding.variant, holding.grade, holding.condition)
            except Exception:
                buyback = in_stock = None

        try:
            buy_url = provider.get_buy_url(holding.card_id, holding.variant, holding.grade, holding.condition)
        except Exception:
            buy_url = None

        result = PricedHolding(
            holding=holding, market_native=price, currency=currency,
            market_rate=rate, buy_rate=buy_rate, display_currency=dc,
            buyback_native=buyback, in_stock=in_stock, buy_url=buy_url,
        )
        priced.append(result)
        if on_priced:
            on_priced(result)
    return priced


def compute_totals(priced: list[PricedHolding], display_currency: str | None = None) -> PortfolioTotals:
    """Sum successfully priced rows; ERROR rows are excluded from both sides."""
    invested = sum(p.invested for p in priced if p.ok)
    value = sum(p.value or 0.0 for p in priced if p.ok)
    errors = sum(1 for p in priced if not p.ok)
    sell_value = sum(p.buyback_value for p in priced if p.ok and p.buyback_value is not None)
    dc = display_currency or (priced[0].display_currency if priced else config.DISPLAY_CURRENCY)
    return PortfolioTotals(invested=invested, value=value, error_count=errors,
                           display_currency=dc, sell_value=sell_value)
