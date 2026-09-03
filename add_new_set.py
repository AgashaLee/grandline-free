"""Safely ADD newly-released cards (e.g. a new set like EB05) to the catalog.

Unlike ``seed_cards.py`` -- which DROPs and rebuilds the whole ``cards`` table
(risky: if a source is momentarily broken, e.g. the promo endpoint, cards can be
lost) -- this is purely additive: it inserts cards we don't have yet and never
touches or removes existing ones. Safe to run anytime, even unattended.

Steps:
  1. Pull OPTCGAPI's bulk endpoints.
  2. INSERT OR IGNORE any card_id not already in ``cards`` (base print preferred,
     else a variant print if that's all that exists).
  3. Rebuild ``card_variants`` (prices + per-printing images) via seed_variants --
     that table is fully derived from OPTCGAPI, so a rebuild loses nothing.

Not covered (do these separately if wanted for a new set):
  * Nicer alt-art gallery images (`seed_alt_arts.py` / `seed_set_alt_arts.py`).
  * The booster-box tile art (`scrape_boxes.py` -- source is currently IP-blocked).
  New cards still show with OPTCGAPI images + prices without those.

Run:  python add_new_set.py
"""

import requests

from database import get_db, init_db

BASE = "https://optcgapi.com/api"
ENDPOINTS = ["allSetCards", "allSTCards", "allPromoCards"]
HEADERS = {"User-Agent": "Mozilla/5.0"}

# A printing whose name carries one of these is a variant, not the base card.
_VARIANT_MARK = ("(parallel)", "(sp)", "(alternate", "(super", "(manga",
                 "(special", "(box", "(pre-release", "(championship", "(winner",
                 "(judge", "(serial", "(reprint", "(textured", "(full art",
                 "(promo", "(gift", "(anime")


def _price(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_variant(name: str) -> bool:
    low = (name or "").lower()
    return any(m in low for m in _VARIANT_MARK)


def _insert(db, c, cid):
    db.execute(
        """INSERT OR IGNORE INTO cards
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


def add_new() -> int:
    init_db()
    db = get_db()
    have = {r[0] for r in db.execute("SELECT card_id FROM cards").fetchall()}

    batches = []
    for ep in ENDPOINTS:
        try:
            data = requests.get(f"{BASE}/{ep}/", timeout=60, headers=HEADERS).json()
        except Exception as exc:
            print(f"  {ep}: fetch failed ({exc}) -- skipped, existing cards untouched")
            continue
        if isinstance(data, list):
            batches.append(data)
            print(f"  {ep}: {len(data)} entries")
        else:
            print(f"  {ep}: unexpected response, skipped")

    added = 0
    # Pass 1: base prints define a card. Pass 2: fill any card that only exists
    # as a variant printing. Existing card_ids are never overwritten.
    for want_variant in (False, True):
        for data in batches:
            for c in data:
                cid = str(c.get("card_set_id") or c.get("card_id") or "").strip().upper()
                if not cid or cid in have:
                    continue
                if _is_variant(c.get("card_name")) != want_variant:
                    continue
                _insert(db, c, cid)
                have.add(cid)
                added += 1
    db.commit()
    print(f"Added {added} new card(s). Catalog now has {len(have)}.")

    # Refresh prices + per-printing data (safe full rebuild of the derived table).
    try:
        import seed_variants
        seed_variants.seed()
    except Exception as exc:
        print(f"card_variants refresh failed ({exc}) -- run seed_variants.py later.")
    return added


if __name__ == "__main__":
    add_new()
