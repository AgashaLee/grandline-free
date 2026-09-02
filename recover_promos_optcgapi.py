"""SAFE promo recovery — run this WHEN optcgapi's promo endpoint is back.

Background: optcgapi's `allPromoCards` endpoint is currently 404, so promos in our
catalog come from two places — 32 from an earlier optcgapi run and 112 scraped from
onepiecetopdecks (`seed_promos_topdecks.py`), the latter with image + name only.

This is "Option B": it FILLS IN the missing gameplay fields on the promos we already
have, and ADDS any promo optcgapi lists that we don't — WITHOUT ever dropping rows or
creating duplicates. It is the safe alternative to a full `seed_cards.py` rebuild
(which would delete the scraped promos and revert to watermarked images).

Why no duplicates: every card is keyed on `card_id` (a primary key). optcgapi's
`P-001` updates the SAME row as our `P-001`; it cannot become a second copy. Alt-art
printings optcgapi lists under distinct ids (e.g. `P-029_R1`) are separate cards, not
duplicates — by design (the user is OK with these).

What it does NOT touch: `card_id`, `name`, `image_url` (so the cleaner scraped images
are kept). Only blank/gameplay fields are filled.

Run:  python recover_promos_optcgapi.py
"""

import requests

from database import get_db

URL = "https://optcgapi.com/api/allPromoCards/"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _price(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def recover() -> int:
    # 1) only proceed if the endpoint genuinely returns promo JSON.
    try:
        r = requests.get(URL, timeout=60, headers=HEADERS)
    except Exception as exc:
        print(f"Fetch failed ({exc}). Nothing changed.")
        return 0
    if r.status_code != 200:
        print(f"allPromoCards still returns HTTP {r.status_code} — endpoint not back yet. "
              "Nothing changed.")
        return 0
    try:
        data = r.json()
    except Exception:
        print("Response was not JSON (endpoint likely still broken). Nothing changed.")
        return 0
    if not isinstance(data, list) or not data:
        print("Empty/unexpected promo response. Nothing changed.")
        return 0

    db = get_db()
    have = {row[0] for row in db.execute("SELECT card_id FROM cards").fetchall()}

    filled = added = 0
    for c in data:
        cid = str(c.get("card_set_id") or c.get("card_id") or "").strip().upper()
        if not cid:
            continue
        fields = (
            c.get("rarity"), c.get("card_type"), c.get("card_color"),
            c.get("card_cost"), c.get("card_power"), c.get("card_text"),
            c.get("attribute"), c.get("counter_amount"), c.get("sub_types"),
            c.get("life"), c.get("set_name"), _price(c.get("market_price")),
        )
        if cid in have:
            # Fill in gameplay fields on the promo we already have. Keep our
            # card_id / name / image_url (the scraped image is cleaner).
            db.execute(
                """UPDATE cards SET rarity=?, card_type=?, card_color=?, card_cost=?,
                       card_power=?, card_text=?, attribute=?, counter=?, sub_types=?,
                       life=?, set_name=?, market_price=? WHERE card_id=?""",
                (*fields, cid),
            )
            filled += 1
        else:
            # A promo optcgapi has that we don't — add it (with its image).
            db.execute(
                """INSERT OR IGNORE INTO cards
                   (card_id, name, set_id, set_name, rarity, card_type, card_color,
                    card_cost, card_power, card_text, region, image_url, market_price,
                    attribute, counter, sub_types, life)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, c.get("card_name"), "P", c.get("set_name"), c.get("rarity"),
                 c.get("card_type"), c.get("card_color"), c.get("card_cost"),
                 c.get("card_power"), c.get("card_text"), "en", c.get("card_image"),
                 _price(c.get("market_price")), c.get("attribute"),
                 c.get("counter_amount"), c.get("sub_types"), c.get("life")),
            )
            added += 1

    db.commit()
    dups = db.execute(
        "SELECT card_id, COUNT(*) n FROM cards GROUP BY card_id HAVING n>1"
    ).fetchall()
    print(f"Recovered promos: filled details on {filled}, added {added} new. "
          f"Duplicate card_ids after run: {len(dups)} (must be 0).")
    return filled + added


if __name__ == "__main__":
    recover()
