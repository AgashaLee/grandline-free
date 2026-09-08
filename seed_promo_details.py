"""Fill in the gameplay details for older promo (P-###) cards.

Our promo art comes from a gallery that only ships the name + image, and the
API that used to carry the promo stats retired its endpoint, so ~110 older
promos sat with a blank details panel (no type/colour/cost/power/effect). The
values in promo_details.json were read off the card faces themselves and are
applied here.

Safe + additive:
  * Only fills a field that is currently NULL/empty -- never overwrites data
    that already came from the API (so if the API's promo feed ever returns,
    its values win on the next catalog reseed).
  * Idempotent: re-running only touches rows that are still missing details.

Run:  python seed_promo_details.py
"""
from __future__ import annotations

import json

import config
from database import get_db

DETAILS_FILE = config.BASE_DIR / "promo_details.json"

# JSON key -> cards column. Only these are filled.
_FIELDS = {
    "card_type": "card_type", "card_color": "card_color", "card_cost": "card_cost",
    "card_power": "card_power", "counter": "counter", "attribute": "attribute",
    "sub_types": "sub_types", "card_text": "card_text", "life": "life",
}


def seed() -> int:
    db = get_db()
    data = json.loads(DETAILS_FILE.read_text(encoding="utf-8"))
    filled = 0
    for code, fields in data.items():
        # Only enrich a promo that is still missing its details (blank type).
        row = db.execute(
            "SELECT card_type FROM cards WHERE card_id=?", (code,)).fetchone()
        if row is None:
            continue
        if (row[0] or "").strip():
            continue  # already has details (from the API) -- leave it alone
        sets, vals = [], []
        for key, col in _FIELDS.items():
            if key in fields:
                sets.append(f"{col}=?")
                vals.append(fields[key])
        if not sets:
            continue
        vals.append(code)
        db.execute(f"UPDATE cards SET {', '.join(sets)} WHERE card_id=?", vals)
        filled += 1
    db.commit()
    return filled


if __name__ == "__main__":
    n = seed()
    print(f"Filled details for {n} promo cards.")
