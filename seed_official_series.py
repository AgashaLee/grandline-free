"""Seed one card set from Bandai's OFFICIAL card list, for sets not yet in
optcgapi (e.g. a just-revealed set like EB-05 before release).

Unlike seed_cards.py (which DROPs and rebuilds the whole catalog from optcgapi),
this UPSERTS one set's rows into the existing ``cards`` table keyed by card_id,
so it sits alongside the optcgapi catalog. When optcgapi later lists the set,
re-running ``seed_cards.py`` rebuilds from scratch and these provisional rows are
replaced with the full data (market prices + clean, non-SAMPLE images) — the
card_id is identical across both sources, so nothing duplicates.

Caveats for a just-revealed set (both auto-corrected on the later optcgapi reseed):
  - Images are the official SAMPLE-watermarked pre-release art.
  - No market prices (Bandai is the publisher, not a marketplace).

Usage:
  # Fetch from Bandai, seed the DB, and also save a JSON snapshot:
  python seed_official_series.py <series_id> "<set name>" [out.json]
      e.g. python seed_official_series.py 569205 "ONE PIECE Heroines Edition Vol.2" data/eb05_official.json

  # Seed the DB from a saved JSON (no network needed — use this on Railway):
  python seed_official_series.py --json data/eb05_official.json
"""

import html as _html
import io
import json
import os
import re
import sys

import requests

from database import get_db

IMG_DIR = "assets/cards"   # self-hosted card art (Bandai hotlink-protects theirs)

BASE = "https://en.onepiece-cardgame.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/cardlist/",
}

CARD_COLS = ("card_id", "name", "set_id", "set_name", "rarity", "card_type",
             "card_color", "card_cost", "card_power", "card_text", "region",
             "image_url", "market_price", "attribute", "counter", "sub_types", "life")


def _txt(s):
    """HTML fragment -> clean single-line text (<br> becomes a space)."""
    if s is None:
        return None
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).strip()
    s = re.sub(r"\s+", " ", s)
    return s or None


def _field(block, cls):
    """Inner text of <div class="cls"><h3>Label</h3>VALUE</div> (label dropped)."""
    m = re.search(rf'<div class="{cls}">(.*?)</div>', block, re.S)
    if not m:
        return None
    return _txt(re.sub(r"<h3>.*?</h3>", "", m.group(1), flags=re.S))


def parse_cards(html_text, set_name):
    """Parse every card from an official /cardlist series page.

    Keeps one row per card code (the base print, whose <dl> id equals the code);
    a parallel/alt print (id like EB05-001_p1) only fills a gap.
    """
    cards = {}
    for block in re.findall(r'<dl class="modalCol".*?</dl>', html_text, re.S):
        id_m = re.search(r'id="([^"]+)"', block)
        dl_id = id_m.group(1) if id_m else ""
        info = re.search(r'<div class="infoCol">(.*?)</div>', block, re.S)
        spans = re.findall(r"<span>(.*?)</span>", info.group(1), re.S) if info else []
        code = _txt(spans[0]) if spans else None
        if not code:
            continue
        name_m = re.search(r'<div class="cardName">(.*?)</div>', block, re.S)
        attr_m = re.search(r'<div class="attribute">.*?<i>(.*?)</i>', block, re.S)
        text_m = re.search(r'<div class="text">(.*?)</div>', block, re.S)

        counter = _field(block, "counter")
        if counter in ("-", ""):
            counter = None

        # The "cost" div holds Cost for most cards but LIFE for Leaders (same
        # CSS class, different <h3> label). Route by the label.
        card_cost = life = None
        cost_m = re.search(r'<div class="cost">(.*?)</div>', block, re.S)
        if cost_m:
            label_m = re.search(r"<h3>(.*?)</h3>", cost_m.group(1), re.S)
            label = _txt(label_m.group(1)) if label_m else ""
            value = _txt(re.sub(r"<h3>.*?</h3>", "", cost_m.group(1), flags=re.S))
            if (label or "").lower().startswith("life"):
                life = value
            else:
                card_cost = value

        row = {
            "card_id": code,
            "name": _txt(name_m.group(1)) if name_m else None,
            "set_id": code.split("-")[0],                 # e.g. "EB05" (prefix the DB groups on)
            "set_name": set_name,
            "rarity": _txt(spans[1]) if len(spans) > 1 else None,
            # Bandai lists types UPPERCASE; optcgapi (the rest of the catalog) uses
            # Title case. Match it so filters don't show CHARACTER + Character twice.
            "card_type": ((_txt(spans[2]) or "").title() or None) if len(spans) > 2 else None,
            "card_color": _field(block, "color"),
            "card_cost": card_cost,
            "card_power": _field(block, "power"),
            "card_text": _txt(re.sub(r"<h3>.*?</h3>", "", text_m.group(1), flags=re.S)) if text_m else None,
            "region": "en",
            "image_url": f"{BASE}/images/cardlist/card/{code}.png",
            "market_price": None,
            "attribute": _txt(attr_m.group(1)) if attr_m else None,
            "counter": counter,
            "sub_types": _field(block, "feature"),
            "life": life,
        }
        if code not in cards or dl_id == code:   # prefer the base print
            cards[code] = row
    return list(cards.values())


def fetch_series(series_id, set_name):
    url = f"{BASE}/cardlist/?series={series_id}"
    html_text = requests.get(url, headers=HEADERS, timeout=30).text
    if not set_name:
        gm = re.search(r'<div class="getInfo">.*?</h3>(.*?)</div>', html_text, re.S)
        set_name = _txt(gm.group(1)) if gm else str(series_id)
    return parse_cards(html_text, set_name)


def download_image(code):
    """Download the official card PNG and re-encode to a compact local JPG.

    Bandai hotlink-protects the card images (they fail to load cross-origin in a
    browser), so the art must be self-hosted. Returns the site-relative URL, or
    None on failure (the card still shows via the page's placeholder)."""
    from PIL import Image  # only needed for the fetch/download path, not --json
    os.makedirs(IMG_DIR, exist_ok=True)
    out_path = f"{IMG_DIR}/{code}.jpg"
    try:
        raw = requests.get(f"{BASE}/images/cardlist/card/{code}.png",
                           headers=HEADERS, timeout=30).content
        Image.open(io.BytesIO(raw)).convert("RGB").save(
            out_path, "JPEG", quality=85, optimize=True)
        return f"/{IMG_DIR}/{code}.jpg"
    except Exception as exc:
        print(f"  image {code}: failed ({exc})")
        return None


def attach_local_images(rows):
    """Download each card's art locally and point image_url at the hosted copy."""
    for i, r in enumerate(rows, 1):
        url = download_image(r["card_id"])
        if url:
            r["image_url"] = url
        if i % 10 == 0:
            print(f"  images {i}/{len(rows)}")
    return rows


def seed_rows(rows):
    """Upsert rows into the existing cards table (never drops it)."""
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS cards (
            card_id TEXT PRIMARY KEY, name TEXT, set_id TEXT, set_name TEXT,
            rarity TEXT, card_type TEXT, card_color TEXT, card_cost TEXT,
            card_power TEXT, card_text TEXT, region TEXT, image_url TEXT,
            market_price REAL, attribute TEXT, counter TEXT, sub_types TEXT,
            life TEXT)"""
    )
    ph = ",".join("?" * len(CARD_COLS))
    cols = ",".join(CARD_COLS)
    for r in rows:
        db.execute(f"INSERT OR REPLACE INTO cards ({cols}) VALUES ({ph})",
                   tuple(r.get(c) for c in CARD_COLS))
    db.commit()
    return len(rows)


def main(argv):
    if argv and argv[0] == "--json":
        rows = json.load(open(argv[1], encoding="utf-8"))
        print(f"Seeded {seed_rows(rows)} cards from {argv[1]}.")
        return
    if not argv:
        print(__doc__)
        return
    series_id = argv[0]
    set_name = argv[1] if len(argv) > 1 else None
    out = argv[2] if len(argv) > 2 else None
    rows = fetch_series(series_id, set_name)
    print(f"Parsed {len(rows)} cards; downloading art...")
    attach_local_images(rows)
    if out:
        import os
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"Wrote {len(rows)} cards to {out}")
    print(f"Seeded {seed_rows(rows)} cards into the catalog ({rows[0]['set_name'] if rows else set_name}).")


if __name__ == "__main__":
    main(sys.argv[1:])
