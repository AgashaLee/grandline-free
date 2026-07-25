"""Presentation layer: rich terminal table + portfolio.csv writer."""

from __future__ import annotations

import csv
from pathlib import Path

from rich import box
from rich.console import Console
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text

import config
from portfolio import PortfolioTotals, PricedHolding

CSV_COLUMNS = ["name", "card_id", "region", "variant", "grade", "qty",
               "buy_price", "buy_currency", "market_price", "market_currency",
               "market_value", "pl", "pl_pct", "display_currency"]

console = Console()


# --- formatting helpers -------------------------------------------------
def _amount(value: float | None, currency: str | None = None) -> str:
    """Format a bare amount in the display currency (no symbol).

    Decimals follow the currency: rupiah and yen have no minor unit in
    practice, dollars and euros do.
    """
    if value is None:
        return "-"
    decimals = config.currency_format(currency or config.DISPLAY_CURRENCY)[1]
    return f"{value:,.{decimals}f}"


def _money(value: float | None, currency: str | None = None) -> str:
    """Format a price with its currency symbol, e.g. ¥6,980 / $16.14 / Rp 440,000."""
    if value is None:
        return "-"
    symbol, decimals = config.currency_format(currency or config.DISPLAY_CURRENCY)
    return f"{symbol}{value:,.{decimals}f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.1f}%"


def _pl_text(value: float | None, formatter=_amount, signed: bool = True) -> Text:
    """Green when >= 0, red when < 0. ``signed`` adds a leading ``+`` on gains.

    Percentages are already signed by their own formatter, so they pass
    ``signed=False`` to avoid a doubled ``++``.
    """
    if value is None:
        return Text("ERROR", style="yellow")
    style = "green" if value >= 0 else "red"
    prefix = "+" if signed and value >= 0 else ""
    return Text(f"{prefix}{formatter(value)}", style=style)


# --- terminal report ----------------------------------------------------
def numeric_headers() -> tuple[str, ...]:
    """Column headers, labelled with whatever currency the user reports in."""
    code = config.DISPLAY_CURRENCY
    return ("ID", "Qty", f"Buy {code}", "Market", f"Value {code}", f"P/L {code}", "P/L %")


#: Snapshot for callers that want the default labels; the table itself calls
#: numeric_headers() at render time so a currency switch is picked up live.
NUMERIC_HEADERS = numeric_headers()

# The card-name column is the only elastic one; it absorbs any width shortfall.
MAX_NAME_WIDTH = 30
MIN_NAME_WIDTH = 8

# All eight columns need ~105 chars. On a narrower terminal, hide these (in
# order) rather than truncating figures into "+137,9...". Both are recoverable:
# Mkt IDR is Mkt USD x the rate shown in the title, and portfolio.csv always
# carries every column regardless of terminal width.
OPTIONAL_COLUMNS = (5, 4)  # Mkt IDR, then Mkt USD


def _build_rows(priced: list[PricedHolding], totals: PortfolioTotals) -> list[tuple]:
    """Format every cell up front, so column widths can be measured exactly."""
    err = lambda: Text("ERROR", style="yellow")  # noqa: E731
    rows: list[tuple] = []

    for p in priced:
        h = p.holding
        if not p.ok:
            rows.append((h.name, h.card_id, str(h.quantity), _amount(p.invested),
                         err(), err(), err(), Text("-", style="yellow")))
            continue
        rows.append((
            h.name,
            h.card_id,
            str(h.quantity),
            _amount(p.invested),
            _money(p.market_native, p.currency),   # source currency (¥/$)
            _amount(p.value),                      # display currency
            _pl_text(p.pl),
            _pl_text(p.pl_pct, _pct, signed=False),
        ))

    rows.append((
        Text("TOTAL", style="bold"),
        "",
        Text(str(sum(p.holding.quantity for p in priced if p.ok)), style="bold"),
        Text(_amount(totals.invested), style="bold"),
        "",
        Text(_amount(totals.value), style="bold"),
        _pl_text(totals.pl),
        _pl_text(totals.pl_pct, _pct, signed=False),
    ))
    return rows


def _make_table(rows: list[tuple], title: str, hidden: set[int]) -> Table:
    """Build the table with ``hidden`` column indexes omitted."""
    table = Table(
        title=title,
        title_style="bold",
        header_style="bold cyan",
        box=box.SIMPLE_HEAD,  # no vertical rules: buys back ~9 columns of width
        padding=(0, 1),
        show_lines=False,
    )
    table.add_column("Card", overflow="ellipsis", no_wrap=True, width=MAX_NAME_WIDTH)
    for i, header in enumerate(numeric_headers(), start=1):
        if i not in hidden:
            table.add_column(header, justify="right", no_wrap=True)

    for row in rows[:-1]:
        table.add_row(*(c for i, c in enumerate(row) if i not in hidden))
    table.add_section()
    table.add_row(*(c for i, c in enumerate(rows[-1]) if i not in hidden))
    return table


def _excess_width(table: Table) -> int:
    """How many columns too wide the table is for the current console.

    Measured rather than estimated, because the box style is not fixed: a
    non-tty console falls back to an ASCII box whose vertical rules cost extra.
    """
    wide = console.options.update_width(1000)
    return Measurement.get(console, wide, table).maximum - console.width


def _fit_table(rows: list[tuple], title: str) -> Table:
    """Return the widest table that fits, shrinking the name column first.

    rich responds to an oversized table by squeezing *every* column, which
    truncates figures into things like "+137,9...". So instead we take the
    shortfall out of the card-name column, and only if that is not enough do we
    drop an optional column.
    """
    hidden: set[int] = set()
    for candidate in (*OPTIONAL_COLUMNS, None):
        table = _make_table(rows, title, hidden)
        excess = _excess_width(table)
        if MAX_NAME_WIDTH - excess >= MIN_NAME_WIDTH or candidate is None:
            # excess < 0 means spare room; never grow past MAX_NAME_WIDTH.
            width = min(MAX_NAME_WIDTH, MAX_NAME_WIDTH - excess)
            table.columns[0].width = max(MIN_NAME_WIDTH, width)
            return table
        hidden.add(candidate)
    raise AssertionError("unreachable")  # pragma: no cover


def print_table(priced: list[PricedHolding], totals: PortfolioTotals, rates: dict[str, float], fx_source: str) -> None:
    """Print the per-card table followed by a TOTAL summary.

    ``rates`` maps each currency actually used to its rate into the display
    currency, so a mixed JP/EN collection shows both.
    """
    display = totals.display_currency
    rows = _build_rows(priced, totals)
    shown = "  ".join(f"{c}/{display} {r:,.2f}" for c, r in sorted(rates.items())
                      if c != "?" and c != display)
    title = f"One Piece TCG Portfolio  ({shown or display} - {fx_source})"

    console.print()
    console.print(_fit_table(rows, title))

    console.print(
        f"Invested: [bold]{_money(totals.invested, display)}[/]   "
        f"Current value: [bold]{_money(totals.value, display)}[/]   "
        f"P/L: [{'green' if totals.pl >= 0 else 'red'}][bold]"
        f"{'+' if totals.pl >= 0 else ''}{_money(totals.pl, display)} ({_pct(totals.pl_pct)})[/][/]"
    )
    if totals.error_count:
        console.print(
            f"[yellow]{totals.error_count} card(s) could not be priced and are excluded from totals.[/]"
        )


# --- csv report ---------------------------------------------------------
def write_csv(priced: list[PricedHolding], path: Path | str) -> Path:
    """Write portfolio.csv. Unpriced cards get ``ERROR`` in the value columns."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for p in priced:
            h = p.holding
            # Buy price stays in the currency it was actually paid in; the
            # valuation columns are all in the display currency.
            lead = [h.name, h.card_id, h.region, h.variant, h.grade, h.quantity,
                    round(h.total_buy, 2), h.buy_currency]
            if not p.ok:
                writer.writerow([*lead, "ERROR", p.currency, "ERROR", "ERROR", "ERROR",
                                 p.display_currency])
                continue
            writer.writerow([
                *lead,
                round(p.market_native, 2),
                p.currency,
                round(p.value, 2),
                round(p.pl, 2),
                None if p.pl_pct is None else round(p.pl_pct, 2),
                p.display_currency,
            ])
    return path
