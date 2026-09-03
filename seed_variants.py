"""Build the per-printing price table (card_variants) from OPTCGAPI.

OPTCGAPI lists the base print AND each alternate printing (Alternate Art,
Parallel, SP, Manga…) as separate entries under the same card_set_id, each with
its own rarity, image and market_price. seed_cards.py keeps only the BASE print
(one clean row per card); this captures ALL printings with their prices so the
card popup can show a price per version and Market Watch can track the (often
far pricier) alt-arts as their own movers.

Base print's variant_id == card_id; a variant is card_id + '#' + a slug of its
label (e.g. 'OP16-015#alternate-art'), stable across re-runs.

Run:  python seed_variants.py
"""

import re

import requests

from database import get_db

BASE = "https://optcgapi.com/api"
ENDPOINTS = ["allSetCards", "allSTCards", "allPromoCards"]
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Markers in a card name that denote a non-base printing.
_VARIANT_RE = re.compile(
    r"\((parallel|sp|alternate[^)]*|super[^)]*|manga[^)]*|special[^)]*|box[^)]*|"
    r"pre-release[^)]*|championship[^)]*|winner[^)]*|judge[^)]*|serial[^)]*|"
    r"reprint[^)]*|textured[^)]*|full art[^)]*|promo[^)]*|gift[^)]*|anime[^)]*|"
    r"wanted[^)]*|jolly[^)]*|foil[^)]*)\)", re.IGNORECASE)


def _price(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _label(name: str) -> str:
    m = _VARIANT_RE.search(name or "")
    return m.group(1).strip().title() if m else ""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "alt"


def seed():
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS card_variants (
            variant_id TEXT PRIMARY KEY, card_id TEXT, name TEXT, rarity TEXT,
            variant_label TEXT, image_url TEXT, market_price REAL, is_base INTEGER DEFAULT 0)"""
    )
    db.execute("DELETE FROM card_variants")   # derived data -- rebuild cleanly

    seen: dict[str, int] = {}
    total = base_n = var_n = 0
    for ep in ENDPOINTS:
        try:
            data = requests.get(f"{BASE}/{ep}/", timeout=60, headers=HEADERS).json()
        except Exception as exc:
            print(f"  {ep}: fetch failed ({exc})")
            continue
        if not isinstance(data, list):
            print(f"  {ep}: unexpected response, skipped")
            continue
        for c in data:
            cid = str(c.get("card_set_id") or c.get("card_id") or "").strip().upper()
            if not cid:
                continue
            name = c.get("card_name") or ""
            label = _label(name)
            if label:
                vid = f"{cid}#{_slug(label)}"
                if vid in seen:                # rare: same label twice -> index it
                    seen[vid] += 1
                    vid = f"{vid}-{seen[vid]}"
                else:
                    seen[vid] = 1
                is_base = 0
                var_n += 1
            else:
                vid = cid
                is_base = 1
                base_n += 1
            db.execute(
                """INSERT OR REPLACE INTO card_variants
                   (variant_id, card_id, name, rarity, variant_label, image_url, market_price, is_base)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (vid, cid, name, c.get("rarity"), label, c.get("card_image"),
                 _price(c.get("market_price")), is_base),
            )
            total += 1
        print(f"  {ep}: {len(data)} entries")
    db.commit()
    priced = db.execute("SELECT COUNT(*) FROM card_variants WHERE market_price IS NOT NULL").fetchone()[0]
    print(f"card_variants: {total} printings ({base_n} base, {var_n} variants), {priced} priced.")
    return total


if __name__ == "__main__":
    seed()
