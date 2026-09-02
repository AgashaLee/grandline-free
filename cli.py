"""Interactive card entry.

Type a card code, see what the site says it is worth right now, type what you
paid, and it is saved to collection.csv. Confirming the card against the live
lookup means a typo is caught while you are typing rather than showing up as an
ERROR row in the report later.

Presentation only: this module asks a :class:`PriceProvider` for data and hands
Holdings to portfolio.py. It knows nothing about any particular price source.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from rich.prompt import Confirm, IntPrompt, Prompt

import config
from portfolio import Holding, append_holding, load_collection, remove_holding
from providers.base import BASE_VARIANT, PriceProvider, Printing
from report import _money, console

#: Anything a user might reasonably type to mean "I'm done here".
QUIT_WORDS = {"q", "quit", "exit", "done", "x", "0", "keluar", "selesai"}

#: Card codes look like OP15-118, ST01-001, EB04-061, P-001, PRB01-041.
#: Checked before any lookup so a stray keystroke (a menu number, say) can
#: never be saved as a card named "3".
CARD_CODE_RE = re.compile(r"^[A-Z]{1,4}\d{0,2}-\d{2,4}$")


def _is_quit(text: str) -> bool:
    return text.strip().lower() in QUIT_WORDS


def _looks_like_card_code(text: str) -> bool:
    return bool(CARD_CODE_RE.match(text.strip().upper()))


#: Shorthand suffixes, longest first so "juta" is matched before "jt".
_SUFFIXES = (("juta", 1_000_000), ("jt", 1_000_000), ("ribu", 1_000), ("rb", 1_000),
             ("m", 1_000_000), ("k", 1_000))


def _parse_idr(raw: str) -> float | None:
    """Accept the ways people actually type rupiah.

    ``220000``, ``220.000``, ``220,000``, ``Rp 220.000``, ``220k``, ``1.5jt``.

    The separator is ambiguous: in ``220.000`` the dot means thousands, but in
    ``1.5jt`` it means a decimal point. So the suffix is resolved first -- with
    a suffix, separators are decimal points; without one, they are thousands
    separators and simply dropped.
    """
    text = raw.strip().lower().replace("rp", "").replace(" ", "").replace("_", "")
    if not text:
        return None

    multiplier = 1
    for suffix, factor in _SUFFIXES:
        if text.endswith(suffix):
            text, multiplier = text[: -len(suffix)], factor
            break

    if multiplier == 1:
        text = text.replace(".", "").replace(",", "")  # thousands separators
    else:
        text = text.replace(",", ".")  # Indonesian decimal comma: 1,5jt

    try:
        value = float(text) * multiplier
    except ValueError:
        return None
    return value if value >= 0 else None


def _ask_buy_price() -> float | None:
    """Prompt until a valid rupiah amount is given. Blank cancels."""
    while True:
        raw = Prompt.ask(f"  [cyan]Price you paid per card ({config.DISPLAY_CURRENCY})[/]")
        if not raw.strip():
            return None
        value = _parse_idr(raw)
        if value is not None:
            return value
        console.print("  [yellow]Enter a number, e.g. 220000 or 220k.[/]")


def _choose_region(default: str) -> str:
    """Japanese and English cards are different products with different prices."""
    other = "en" if default == "jp" else "jp"
    labels = {"jp": "Japanese (prices in yen, from Yuyu-tei)",
              "en": "English (prices in USD, from optcgapi)"}
    console.print(f"  [cyan]1[/] {labels[default]}  [dim](default)[/]")
    console.print(f"  [cyan]2[/] {labels[other]}")
    answer = Prompt.ask("  [cyan]Which version?[/]", choices=["1", "2"], default="1")
    return default if answer == "1" else other


def _choose_printing(code: str, printings: list[Printing], rate: float, currency: str) -> Printing | None:
    """Ask which printing the user owns. Returns ``None`` to skip the card.

    The same code covers the normal print and every parallel / alternate art,
    and the price gap between them is enormous, so this cannot be guessed --
    it has to be asked.
    """
    if not printings:
        console.print(f"  [yellow]No price found for {code}.[/] Check the code, or try again.")
        if not Confirm.ask("  Add it anyway (it will show as ERROR until priced)?", default=False):
            return None
        return Printing(BASE_VARIANT, "Normal", None, code)

    if len(printings) == 1:
        only = printings[0]
        console.print(f"  [green]{only.name}[/] - {_price_text(only.price_usd, rate, currency)}")
        return only

    console.print(f"  [green]{code}[/] has [bold]{len(printings)}[/] versions - which one do you own?")
    for i, p in enumerate(printings, start=1):
        marker = "[dim](normal print)[/]" if p.variant == BASE_VARIANT else ""
        console.print(f"    [cyan]{i:>2}[/]  {p.label:<20} {_price_text(p.price_usd, rate, currency)} {marker}")

    answer = Prompt.ask("  [cyan]Which version?[/] [dim](number, or Enter to skip)[/]", default="").strip()
    if not answer or _is_quit(answer):
        console.print("  [dim]Skipped.[/]")
        return None
    try:
        index = int(answer)
    except ValueError:
        console.print("  [yellow]Type the number next to the version.[/]")
        return None
    if not 1 <= index <= len(printings):
        console.print(f"  [yellow]Pick a number between 1 and {len(printings)}.[/]")
        return None

    return printings[index - 1]


def _price_text(price: float | None, rate: float, currency: str) -> str:
    if price is None:
        return "[yellow]no price[/]"
    return f"[bold]{_money(price, currency)}[/] = [bold]{_money(price * rate, config.DISPLAY_CURRENCY)}[/]"


def add_cards(provider_for, rate_for, path: Path | str | None = None) -> int:
    """Interactive add loop. Returns how many cards were added."""
    try:
        existing = {h.card_id for h in load_collection("")}
    except (FileNotFoundError, ValueError):
        existing = set()

    console.print()
    console.print("[bold]Add cards to your collection[/]")
    console.print("[dim]Type a card code, or press Enter (or type Q) to go back to the menu.[/]")
    added = 0

    while True:
        code = Prompt.ask("\n[cyan]Card code[/] [dim](e.g. OP15-118, or Q to finish)[/]", default="").strip().upper()
        if not code or _is_quit(code):
            break

        # Reject anything that isn't shaped like a card code before going near
        # the network or asking for a price -- a stray "3" is a mistyped menu
        # choice, not a card, and must never reach collection.csv.
        if not _looks_like_card_code(code):
            console.print(
                f"  [yellow]'{code}' doesn't look like a card code.[/] "
                "They look like OP15-118 or ST01-001, printed at the bottom-left of the card."
            )
            console.print("  [dim]Press Enter or type Q to go back to the menu.[/]")
            continue

        # Japanese and English printings are priced by different sources.
        region = _choose_region(config.DEFAULT_REGION)
        provider = provider_for(region)
        try:
            rate = rate_for(provider.currency)
        except Exception as exc:
            console.print(f"  [red]Cannot convert {provider.currency} to {config.DISPLAY_CURRENCY}:[/] {exc}")
            continue

        # Confirm the code is real before asking for anything else.
        with console.status("[cyan]Looking it up..."):
            printings = provider.list_printings(code)

        printing = _choose_printing(code, printings, rate, provider.currency)
        if printing is None:
            continue
        name, variant, price_native = printing.name, printing.variant, printing.price_usd

        if code in existing:
            console.print(f"  [yellow]{code} is already in your collection[/] - adding a second lot.")

        buy_amount = _ask_buy_price()
        if buy_amount is None:
            console.print("  [dim]Skipped.[/]")
            continue

        quantity = IntPrompt.ask("  [cyan]How many do you own[/]", default=1)
        if quantity < 1:
            console.print("  [dim]Skipped.[/]")
            continue

        append_holding(
            "",
            Holding(name=name or code, card_id=code, buy_price=buy_amount,
                    quantity=quantity, variant=variant, region=region),
        )
        existing.add(code)
        added += 1

        # Immediate feedback: what this card is doing right now.
        if price_native is not None:
            pl = (price_native * rate - buy_amount) * quantity
            colour = "green" if pl >= 0 else "red"
            console.print(
                f"  [bold]Saved.[/] P/L on this card: "
                f"[{colour}]{'+' if pl >= 0 else ''}{_money(pl, config.DISPLAY_CURRENCY)}[/]"
            )
        else:
            console.print("  [bold]Saved.[/]")

    console.print(f"\n[bold]{added}[/] card(s) added.")
    return added


def _show_collection(holdings: list[Holding]) -> None:
    """Numbered listing, so a card can be picked without retyping its code."""
    console.print()
    for i, h in enumerate(holdings, start=1):
        variant = "" if h.variant == BASE_VARIANT else f"  [magenta]{h.variant}[/]"
        console.print(
            f"  [cyan]{i:>2}[/]  {h.name}  [dim]{h.card_id} {h.region}[/]{variant}  "
            f"x{h.quantity}  [dim]bought {_money(h.buy_price, h.buy_currency)} each[/]"
        )


def remove_cards(path: Path | str | None = None) -> int:
    """Remove sold cards. Returns how many entries were changed."""
    try:
        holdings = load_collection("")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Could not read your collection:[/] {exc}")
        return 0

    if not holdings:
        console.print("[yellow]Your collection is empty - nothing to remove.[/]")
        return 0

    console.print()
    console.print("[bold]Remove cards you have sold[/]")
    console.print("[dim]Pick a number, or press Enter (or type Q) to go back to the menu.[/]")
    changed = 0

    while holdings:
        _show_collection(holdings)
        answer = Prompt.ask("\n[cyan]Which card did you sell?[/] [dim](number, or Q)[/]", default="").strip()
        if not answer or _is_quit(answer):
            break

        try:
            index = int(answer)
        except ValueError:
            console.print("  [yellow]Type the number shown next to the card.[/]")
            continue
        if not 1 <= index <= len(holdings):
            console.print(f"  [yellow]Pick a number between 1 and {len(holdings)}.[/]")
            continue

        holding = holdings[index - 1]
        sold = IntPrompt.ask(
            f"  [cyan]How many did you sell?[/] [dim](you own {holding.quantity})[/]",
            default=holding.quantity,
        )
        if sold < 1:
            console.print("  [dim]Nothing removed.[/]")
            continue
        sold = min(sold, holding.quantity)

        remaining = holding.quantity - sold
        what = f"{holding.name} ({holding.card_id}) x{sold}"
        if not Confirm.ask(f"  Remove [bold]{what}[/]?", default=True):
            console.print("  [dim]Nothing removed.[/]")
            continue

        if remaining:
            console.print(f"  [bold]Removed.[/] {holding.quantity} -> {remaining} left.")
        else:
            console.print("  [bold]Removed.[/]")

        remove_holding("", index - 1, holding.card_id, holding.variant, sold)
        
        # reload for next loop iteration to keep indexes synced
        holdings = load_collection("")
        changed += 1

    if not holdings:
        console.print("\n[dim]Your collection is now empty.[/]")
    console.print(f"[bold]{changed}[/] change(s) saved.")
    return changed
