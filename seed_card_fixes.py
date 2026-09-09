"""Apply targeted corrections for cards the data source scrambled.

A handful of cards came through OPTCGAPI with fields shuffled among each other
(e.g. OP10-103 had its traits in the power field and its counter in the traits
field) or with the wrong image (OP10-109 pointed at OP10-103's art, so the two
looked like the same card). Some of these are still wrong at the source, so we
can't just reseed -- the correct values in card_fixes.json were read off the
actual card faces.

Targeted + safe: only the card_ids listed in card_fixes.json are touched, and
only the fields named there. Idempotent -- re-running is a no-op once applied.

Run:  python seed_card_fixes.py
"""
from __future__ import annotations

import json

import config
from database import get_db

FIXES_FILE = config.BASE_DIR / "card_fixes.json"

# JSON key -> cards column (only these may be corrected).
_FIELDS = {
    "card_power": "card_power", "sub_types": "sub_types", "counter": "counter",
    "attribute": "attribute", "life": "life", "image_url": "image_url",
    "card_text": "card_text", "card_cost": "card_cost", "card_color": "card_color",
    "name": "name", "card_type": "card_type",
}


def seed() -> int:
    db = get_db()
    data = json.loads(FIXES_FILE.read_text(encoding="utf-8"))
    fixed = 0
    for code, fields in data.items():
        if code.startswith("_"):
            continue  # skip the _comment key
        row = db.execute("SELECT 1 FROM cards WHERE card_id=?", (code,)).fetchone()
        if row is None:
            continue
        sets, vals = [], []
        for key, col in _FIELDS.items():
            if key in fields:
                sets.append(f"{col}=?")
                vals.append(fields[key])
        if not sets:
            continue
        vals.append(code)
        db.execute(f"UPDATE cards SET {', '.join(sets)} WHERE card_id=?", vals)
        # The grid/popup base image comes from card_variants (it overrides
        # cards.image_url), so keep the base variant's image in sync too.
        if "image_url" in fields:
            try:
                db.execute(
                    "UPDATE card_variants SET image_url=? WHERE card_id=? AND is_base=1",
                    (fields["image_url"], code))
            except Exception:
                pass
        fixed += 1
    db.commit()
    return fixed


if __name__ == "__main__":
    n = seed()
    print(f"Applied corrections to {n} cards.")
