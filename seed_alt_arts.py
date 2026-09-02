"""Capture event/bonus ALTERNATE ART for cards we already own.

onepiecetopdecks publishes "Event/Bonus" galleries (2023 and 2024/2025) — these are
different artwork for cards that keep their original code (an event Zoro is still
OP01-001). We keep ONE tile per card in the grid; these alt-arts are shown only inside
a card's zoom, so the grid never doubles up.

Stores rows in ``card_alt_arts`` (one per extra artwork) and downloads each image to
``assets/alt/`` (served at ``/assets/alt/<file>``). Idempotent: re-running only adds
what's new, and only for codes that exist in ``cards``.

Run:  python seed_alt_arts.py
"""

import io
import os
import re

import requests
from PIL import Image

from database import get_db

PAGES = [
    "https://onepiecetopdecks.com/cards/events-bonus-others-cards",
    "https://onepiecetopdecks.com/cards/events-bonus-others-cards-2024/",
]
HEADERS = {"User-Agent": "Mozilla/5.0"}
IMG_DIR = "assets/alt"

_ITEM = re.compile(
    r'data-src="(https://onepiecetopdecks\.com/wp-content/gallery/[^"]+\.(?:jpg|jpeg|png))"'
    r'[^>]*alt="[^"]*?\(([A-Z0-9]+-\d+)\)',
    re.I,
)


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def seed() -> int:
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS card_alt_arts (
               card_id TEXT NOT NULL,
               image_url TEXT NOT NULL,
               UNIQUE(card_id, image_url))"""
    )
    have = {r[0] for r in db.execute("SELECT card_id FROM cards").fetchall()}

    # collect unique (code, source-image-url) for codes we own
    pairs = {}
    for url in PAGES:
        t = requests.get(url, timeout=30, headers=HEADERS).text
        for img_url, code in _ITEM.findall(t):
            code = code.upper()
            if code in have:
                pairs.setdefault(code, set()).add(img_url)

    os.makedirs(IMG_DIR, exist_ok=True)
    added = 0
    for code, urls in sorted(pairs.items()):
        for img_url in sorted(urls):
            base = os.path.basename(img_url.split("?")[0])          # e.g. OP01-001_b.jpg
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", base)
            local = f"/assets/alt/{fname}"
            # skip if we already recorded this artwork
            if db.execute("SELECT 1 FROM card_alt_arts WHERE card_id=? AND image_url=?",
                          (code, local)).fetchone():
                continue
            try:
                r = requests.get(img_url, timeout=30, headers=HEADERS)
                r.raise_for_status()
                _flatten(Image.open(io.BytesIO(r.content))).save(
                    f"{IMG_DIR}/{fname}", "JPEG", quality=88)
            except Exception as exc:
                print(f"  {code} {base}: image failed ({exc}) -- skipped")
                continue
            db.execute("INSERT OR IGNORE INTO card_alt_arts (card_id, image_url) VALUES (?,?)",
                       (code, local))
            added += 1

    db.commit()
    n_codes = db.execute("SELECT COUNT(DISTINCT card_id) FROM card_alt_arts").fetchone()[0]
    n_arts = db.execute("SELECT COUNT(*) FROM card_alt_arts").fetchone()[0]
    print(f"Added {added} new alt-arts. Now {n_arts} arts across {n_codes} cards.")
    return added


if __name__ == "__main__":
    seed()
