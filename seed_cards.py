"""Seed the global card catalog from OPTCGAPI's bulk endpoints.

One call each for booster cards, starter-deck cards and promos gives the whole
English catalog (~4,000+ cards) with names, sets, rarity, type, image and the
current market price -- everything the public Card Database page needs.

Run:  python seed_cards.py
"""

import requests

from database import get_db

BASE = "https://optcgapi.com/api"
# endpoint -> region label. All three are the English catalog (JP prices come
# from Yuyu-tei on demand in the tracker; the catalog itself is region-agnostic).
BULK_ENDPOINTS = ["allSetCards", "allSTCards", "allPromoCards"]
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _price(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# The API lists a base print AND its alt printings under the same card_set_id.
# For a one-row-per-card catalog we keep the BASE print as canonical (its image
# and price are what people mean by "OP01-001"), falling back to a variant only
# when a card has no plain print.
_VARIANT_MARK = ("(parallel)", "(sp)", "(alternate", "(super", "(manga",
                 "(special", "(box", "(pre-release", "(championship", "(winner",
                 "(judge", "(serial", "(reprint", "(textured", "(full art",
                 "(promo", "(gift", "(anime")


def _is_variant(name: str) -> bool:
    low = (name or "").lower()
    return any(m in low for m in _VARIANT_MARK)


def seed():
    db = get_db()
    # The catalog is derived data -- safe to rebuild from scratch each run.
    db.execute("DROP TABLE IF EXISTS cards")
    db.execute(
        """CREATE TABLE cards (
            card_id TEXT PRIMARY KEY, name TEXT, set_id TEXT, set_name TEXT,
            rarity TEXT, card_type TEXT, card_color TEXT, card_cost TEXT,
            card_power TEXT, card_text TEXT, region TEXT, image_url TEXT,
            market_price REAL, attribute TEXT, counter TEXT, sub_types TEXT,
            life TEXT)"""
    )

    total = 0
    for ep in BULK_ENDPOINTS:
        try:
            data = requests.get(f"{BASE}/{ep}/", timeout=60, headers=HEADERS).json()
        except Exception as exc:  # network / JSON -- skip this batch, keep going
            print(f"  {ep}: fetch failed ({exc})")
            continue
        if not isinstance(data, list):
            print(f"  {ep}: unexpected response, skipped")
            continue

        for c in data:
            cid = str(c.get("card_set_id") or c.get("card_id") or "").strip().upper()
            if not cid:
                continue
            # Base print overwrites (REPLACE); a variant only fills a gap (IGNORE),
            # so the canonical row ends up being the plain print whenever one exists.
            verb = "INSERT OR IGNORE" if _is_variant(c.get("card_name")) else "INSERT OR REPLACE"
            db.execute(
                f"""{verb} INTO cards
                   (card_id, name, set_id, set_name, rarity, card_type, card_color,
                    card_cost, card_power, card_text, region, image_url, market_price,
                    attribute, counter, sub_types, life)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, c.get("card_name"), c.get("set_id"), c.get("set_name"),
                 c.get("rarity"), c.get("card_type"), c.get("card_color"),
                 c.get("card_cost"), c.get("card_power"), c.get("card_text"),
                 "en", c.get("card_image"), _price(c.get("market_price")),
                 c.get("attribute"), c.get("counter_amount"), c.get("sub_types"),
                 c.get("life")),
            )
            total += 1
        print(f"  {ep}: {len(data)} cards")

    db.commit()
    print(f"Seeded {total} cards into the catalog.")
    return total


if __name__ == "__main__":
    seed()
