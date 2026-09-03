"""Derive a deck's set-format era from its card list.

A deck can only contain cards from sets released by the time it was played, so
the NEWEST OP set among its cards is a good, region-agnostic proxy for the format
era it belongs to (OP13, OP14 ... OP17). This needs no set release-date table and
works for both West (Limitless) and Japan (tcg-portal) decks. ST/EB/P/PRB codes
are ignored -- they're supplementary/reprints and don't mark the era.
"""

import re

_OP = re.compile(r"^OP(\d+)-", re.IGNORECASE)


def format_of(card_codes) -> str:
    """Return e.g. 'OP17' (the newest OP set in the deck), or '' if none."""
    best = 0
    for c in card_codes:
        m = _OP.match(str(c or ""))
        if m:
            best = max(best, int(m.group(1)))
    return f"OP{best:02d}" if best else ""
