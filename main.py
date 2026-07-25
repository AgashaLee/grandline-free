"""Entry point.

    python main.py          show the portfolio report
    python main.py add      add cards interactively
    python main.py menu     pick from a menu (what run.bat uses)

Orchestration only. Every step delegates to a module that can be swapped:
the provider comes from a factory, FX from fx.py, output from report.py.
"""

from __future__ import annotations

import sys

import config
import fx
import report
from cache import JsonCache
from portfolio import compute_totals, load_collection, price_collection
from providers.base import ProviderPool


def _setup():
    """Build the cache, rate book and per-region providers used by every command.

    Rates are fetched lazily: a purely Japanese collection never asks for a USD
    rate, and vice versa.
    """
    cache = JsonCache(config.CACHE_FILE, config.CACHE_TTL_HOURS)
    return cache, ProviderPool(cache), fx.RateBook(cache)


def run(setup=None) -> int:
    """Show the portfolio report."""
    console = report.console

    # 1. Collection
    try:
        holdings = load_collection(config.COLLECTION_FILE)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Collection error:[/] {exc}")
        return 1
    if not holdings:
        console.print(
            f"[yellow]No cards in {config.COLLECTION_FILE.name} yet.[/] "
            "Add some first (option 2 in the menu, or: python main.py add)"
        )
        return 1

    cache, providers, rates = setup or _setup()

    # 5. Price everything, routing each card to the source for its region
    regions = sorted({h.region for h in holdings})
    console.print(
        f"Pricing [bold]{len(holdings)}[/] card(s) [dim]({', '.join(regions)})[/] "
        f"(cache TTL {config.CACHE_TTL_HOURS:g}h)..."
    )
    with console.status("[cyan]Fetching prices...") as status:
        def progress(priced_holding):
            status.update(f"[cyan]Fetched {priced_holding.holding.card_id}")

        try:
            priced = price_collection(holdings, providers, rates, on_priced=progress)
        except fx.FxError as exc:
            console.print(f"[red]FX error:[/] {exc}")
            return 1

    # 6. Report
    totals = compute_totals(priced)
    fx_source = ", ".join(sorted(set(rates.sources.values()))) or "n/a"
    report.print_table(priced, totals, rates.rates, fx_source)
    out = report.write_csv(priced, config.PORTFOLIO_FILE)
    console.print(f"Wrote [bold]{out}[/]")

    return 0


def add() -> int:
    """Add cards interactively, then show the updated report."""
    import cli

    prepared = _setup()
    _cache, providers, rates = prepared

    if cli.add_cards(providers, rates) == 0:
        return 0
    return run(setup=prepared)


def sell() -> int:
    """Remove sold cards, then show the updated report."""
    import cli

    if cli.remove_cards() == 0:
        return 0
    return run()


def web() -> int:
    """Serve the browser dashboard."""
    import dashboard

    return dashboard.serve()


def menu() -> int:
    """Simple chooser, so the whole tool works from one double-click."""
    import cli
    from rich.prompt import Prompt

    console = report.console
    actions = {"1": run, "2": add, "3": sell, "4": web}

    while True:
        console.print("\n[bold]One Piece TCG Price Tracker[/]")
        console.print("  [cyan]1[/] Show my portfolio (prices + profit/loss)")
        console.print("  [cyan]2[/] Add cards I bought")
        console.print("  [cyan]3[/] Remove cards I sold")
        console.print("  [cyan]4[/] Open the web dashboard")
        console.print("  [cyan]5[/] Quit  [dim](or press Enter / Ctrl+C)[/]")

        # No default action: pressing Enter must EXIT, never silently re-run
        # the report -- otherwise there is no obvious way out of the menu.
        choice = Prompt.ask("Choose", default="").strip().lower()
        if not choice or choice == "5" or cli._is_quit(choice):
            console.print("[dim]Bye.[/]")
            return 0

        action = actions.get(choice)
        if action is None:
            console.print("[yellow]Type 1, 2, 3, 4, or 5.[/]")
            continue
        action()


COMMANDS = {"report": run, "add": add, "sell": sell, "dashboard": web, "menu": menu}

if __name__ == "__main__":
    command = sys.argv[1].lower() if len(sys.argv) > 1 else "report"
    if command not in COMMANDS:
        report.console.print(
            f"[red]Unknown command {command!r}.[/] Use: {', '.join(COMMANDS)}"
        )
        sys.exit(2)
    try:
        sys.exit(COMMANDS[command]())
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C is a legitimate way to leave; don't inflict a traceback.
        report.console.print("\n[dim]Stopped. Your collection file is unchanged.[/]")
        sys.exit(130)
