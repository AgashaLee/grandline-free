"""One-time fix: reset card rows whose canonical print ended up as a FOIL.

optcgapi lists the base card plus its (Pirate Foil)/(Jolly Roger Foil), reprint
and alt-art printings under one card_set_id. Before "foil" was added to
seed_cards._VARIANT_MARK, the foil slipped past the variant filter and overwrote
the base as the canonical row (~77 cards, e.g. OP04-096 showed the 1.66 MB foil
instead of the normal card). This pulls the BASE print from optcgapi and resets
ONLY the affected codes -- it does NOT drop the table, so provisional sets added
separately (e.g. EB-05) are untouched.

Run:  python fix_foil_canonical.py
"""

import requests

from database import get_db
from seed_cards import BASE, BULK_ENDPOINTS, HEADERS, _is_variant, _price


def fix():
    db = get_db()
    bad = [r[0] for r in db.execute("SELECT card_id FROM cards WHERE lower(name) LIKE '%foil%'")]
    if not bad:
        print("No foil-canonical rows found -- nothing to do.")
        return 0
    bad_set = set(bad)
    print(f"{len(bad)} foil-canonical rows to reset; fetching base prints from optcgapi...")

    base_by_code: dict[str, dict] = {}
    for ep in BULK_ENDPOINTS:
        try:
            data = requests.get(f"{BASE}/{ep}/", timeout=60, headers=HEADERS).json()
        except Exception as exc:
            print(f"  {ep}: fetch failed ({exc})")
            continue
        if not isinstance(data, list):
            continue
        for c in data:
            cid = str(c.get("card_set_id") or c.get("card_id") or "").strip().upper()
            # keep the BASE (non-variant) entry for each affected code
            if cid in bad_set and not _is_variant(c.get("card_name")):
                base_by_code[cid] = c

    fixed = 0
    for cid in bad:
        c = base_by_code.get(cid)
        if not c:
            print(f"  {cid}: no base print found in optcgapi -- left as-is")
            continue
        db.execute(
            """UPDATE cards SET name=?, set_id=?, set_name=?, rarity=?, card_type=?,
               card_color=?, card_cost=?, card_power=?, card_text=?, region=?,
               image_url=?, market_price=?, attribute=?, counter=?, sub_types=?,
               life=? WHERE card_id=?""",
            (c.get("card_name"), c.get("set_id"), c.get("set_name"), c.get("rarity"),
             c.get("card_type"), c.get("card_color"), c.get("card_cost"),
             c.get("card_power"), c.get("card_text"), "en", c.get("card_image"),
             _price(c.get("market_price")), c.get("attribute"),
             c.get("counter_amount"), c.get("sub_types"), c.get("life"), cid),
        )
        fixed += 1
    db.commit()
    print(f"Reset {fixed}/{len(bad)} rows to their base print.")
    return fixed


if __name__ == "__main__":
    fix()
