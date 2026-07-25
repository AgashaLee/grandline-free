"""Collection loading and profit/loss maths.

Pure business logic: it accepts a :class:`PriceProvider` and a rate, and knows
nothing about HTTP, vendors, or terminal output. That is what makes both the
provider swap and the (later) Pokemon support drop-in changes.
"""

from __future__ import annotations

import csv
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path

import config
from providers.base import BASE_VARIANT, PriceProvider

REQUIRED_COLUMNS = ("name", "card_id", "quantity")
#: Older files call the price column ``buy_price_idr`` and imply rupiah; newer
#: ones use ``buy_price`` + ``buy_currency``. Both are read; the new pair is
#: always written.
LEGACY_PRICE_COLUMN = "buy_price_idr"
WRITE_COLUMNS = ("name", "card_id", "region", "variant", "grade",
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
    #: What the buyer actually paid in. Stored per row so that changing the
    #: display currency later never rewrites purchase history.
    buy_currency: str = config.DISPLAY_CURRENCY

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
    display_currency: str = config.DISPLAY_CURRENCY

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
    display_currency: str = config.DISPLAY_CURRENCY

    @property
    def pl(self) -> float:
        return self.value - self.invested

    @property
    def pl_pct(self) -> float | None:
        if self.invested <= 0:
            return None
        return self.pl / self.invested * 100.0


def load_collection(path: Path | str) -> list[Holding]:
    """Read collection.csv into :class:`Holding` objects.

    Raises:
        FileNotFoundError: if the file is missing.
        ValueError: if required columns are absent.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Collection file not found: {path}")

    holdings: list[Holding] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
        missing = [c for c in REQUIRED_COLUMNS if c not in fields]
        if "buy_price" not in fields and LEGACY_PRICE_COLUMN not in fields:
            missing.append("buy_price")
        if missing:
            raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")

        for line_no, row in enumerate(reader, start=2):
            clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            card_id = clean.get("card_id", "")
            if not card_id:
                continue  # skip blank/padding rows
            # A file written before multi-currency used the legacy column and
            # meant rupiah; honour that rather than silently relabelling it as
            # whatever the current display currency happens to be.
            if clean.get("buy_price"):
                price = clean["buy_price"]
                paid_in = (clean.get("buy_currency") or config.DISPLAY_CURRENCY).upper()
            else:
                price = clean.get(LEGACY_PRICE_COLUMN) or "0"
                paid_in = (clean.get("buy_currency") or "IDR").upper()

            try:
                holdings.append(
                    Holding(
                        name=clean.get("name") or card_id,
                        card_id=card_id.upper(),
                        buy_price=float(price or 0),
                        buy_currency=paid_in,
                        quantity=int(float(clean.get("quantity") or 0)),
                        # Files written before variants existed mean "normal print".
                        variant=clean.get("variant") or BASE_VARIANT,
                        # ...and before regions existed, they mean the default.
                        region=(clean.get("region") or config.DEFAULT_REGION).lower(),
                        # ...and before grading, every card is raw.
                        grade=(clean.get("grade") or RAW_GRADE).lower(),
                    )
                )
            except ValueError as exc:
                raise ValueError(f"{path} line {line_no}: invalid number ({exc})") from None

    return holdings


#: How many timestamped backups of collection.csv to keep.
BACKUP_KEEP = 10


def _backup(path: Path) -> None:
    """Copy the current collection aside before it is overwritten.

    Every write rewrites the whole file, so a bug (or a second process) could
    otherwise drop rows with no way back. Keeping the last few versions makes
    that recoverable. Backups live in a ``backups/`` subfolder next to the file.
    """
    if not path.exists() or path.stat().st_size == 0:
        return
    backups = path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        shutil.copy2(path, backups / f"{path.stem}-{stamp}{path.suffix}")
    except OSError:
        return  # a failed backup must never block the actual save

    # Prune to the newest BACKUP_KEEP so the folder can't grow without bound.
    saved = sorted(backups.glob(f"{path.stem}-*{path.suffix}"))
    for old in saved[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def save_collection(path: Path | str, holdings: list[Holding]) -> None:
    """Rewrite collection.csv from scratch (used when selling/removing cards)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)  # snapshot the previous version before replacing it
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(WRITE_COLUMNS)
        for h in holdings:
            # Currencies with no minor unit (IDR/JPY) round to whole numbers;
            # dollars and euros keep their cents.
            decimals = config.currency_format(h.buy_currency)[1]
            price = round(h.buy_price, decimals) if decimals else round(h.buy_price)
            writer.writerow([h.name, h.card_id, h.region, h.variant, h.grade,
                             price, h.buy_currency, h.quantity])


def append_holding(path: Path | str, holding: Holding) -> None:
    """Append one card to collection.csv, creating it with a header if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        save_collection(path, [holding])
        return

    # An older file may lack the variant column; rewrite it so the appended row
    # lines up with the header instead of silently shifting every field.
    existing = load_collection(path)
    save_collection(path, [*existing, holding])


class CollectionChanged(RuntimeError):
    """The targeted row is gone or no longer what the caller thought it was.

    Rows are addressed by position, so a stale browser tab could otherwise
    edit or delete the wrong card. Every mutation re-checks identity first.
    """


def _verify(path: Path | str, index: int, card_id: str, variant: str) -> tuple[list[Holding], Holding]:
    holdings = load_collection(path)
    if not 0 <= index < len(holdings):
        raise CollectionChanged("That card is no longer in your collection - refresh the page.")
    holding = holdings[index]
    if holding.card_id != card_id.upper() or holding.variant != variant:
        raise CollectionChanged("Your collection changed since this page loaded - refresh it.")
    return holdings, holding


def update_holding(
    path: Path | str,
    index: int,
    card_id: str,
    variant: str,
    buy_price: float | None = None,
    quantity: int | None = None,
    grade: str | None = None,
    buy_currency: str | None = None,
) -> Holding:
    """Change the buy price, quantity, grade and/or buy currency of one row."""
    holdings, holding = _verify(path, index, card_id, variant)

    new_price = holding.buy_price if buy_price is None else float(buy_price)
    new_qty = holding.quantity if quantity is None else int(quantity)
    if new_price < 0:
        raise ValueError("Buy price cannot be negative.")
    if new_qty < 1:
        raise ValueError("Quantity must be at least 1. Remove the card instead.")

    # replace() carries every other field across, so region/grade/currency
    # can never be dropped by forgetting a positional argument.
    updated = replace(
        holding,
        buy_price=new_price,
        quantity=new_qty,
        grade=holding.grade if grade is None else str(grade).lower(),
        buy_currency=holding.buy_currency if buy_currency is None else str(buy_currency).upper(),
    )
    holdings[index] = updated
    save_collection(path, holdings)
    return updated


def remove_holding(
    path: Path | str,
    index: int,
    card_id: str,
    variant: str,
    quantity: int | None = None,
) -> Holding | None:
    """Sell all or part of a row. Returns what remains, or ``None`` if gone."""
    holdings, holding = _verify(path, index, card_id, variant)

    sold = holding.quantity if quantity is None else int(quantity)
    if sold < 1:
        raise ValueError("Sell at least one card.")
    sold = min(sold, holding.quantity)

    remaining = holding.quantity - sold
    if remaining:
        # replace() keeps region/grade/currency; building a new Holding
        # positionally here used to silently reset them.
        kept = replace(holding, quantity=remaining)
        holdings[index] = kept
    else:
        kept = None
        holdings.pop(index)

    save_collection(path, holdings)
    return kept


def price_collection(
    holdings: list[Holding],
    provider_for,
    rate_for,
    on_priced=None,
) -> list[PricedHolding]:
    """Value every holding, routing each to the source for its region.

    Args:
        provider_for: ``(region) -> PriceProvider``.
        rate_for: ``(currency) -> float`` giving that currency's rate to IDR.
        on_priced: optional callback ``(PricedHolding) -> None`` for progress UI.
    """
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
                price = provider.get_price(holding.card_id, holding.variant, holding.grade)
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

        result = PricedHolding(
            holding=holding, market_native=price, currency=currency,
            market_rate=rate, buy_rate=buy_rate, display_currency=config.DISPLAY_CURRENCY,
        )
        priced.append(result)
        if on_priced:
            on_priced(result)
    return priced


def compute_totals(priced: list[PricedHolding]) -> PortfolioTotals:
    """Sum successfully priced rows; ERROR rows are excluded from both sides."""
    invested = sum(p.invested for p in priced if p.ok)
    value = sum(p.value or 0.0 for p in priced if p.ok)
    errors = sum(1 for p in priced if not p.ok)
    return PortfolioTotals(invested=invested, value=value, error_count=errors,
                           display_currency=config.DISPLAY_CURRENCY)
