"""Top up MISSING promo cards from onepiecetopdecks.com.

optcgapi's promo endpoint is a 404 right now, so our catalog has only the promos
from an earlier good run. onepiecetopdecks publishes a promo image gallery with
~135 cards; this adds the ones we don't have yet.

What we get from that page: card code, name and image ONLY -- it does not list
the gameplay fields (cost/power/effect/trigger/...), so newly-added promos show
image + name in the grid/zoom and blank detail chips until optcgapi recovers.

Safe + additive:
  * INSERT OR IGNORE keyed on card_id -> existing rows are never overwritten.
  * images are downloaded into assets/cards/ (served at /assets/cards/<code>.jpg).

Run:  python seed_promos_topdecks.py
"""

import html
import io
import os
import re

import requests
from PIL import Image

from database import get_db

URL = "https://onepiecetopdecks.com/cards/promo-cards/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
IMG_DIR = "assets/cards"

# One gallery item: data-src="<gallery img>" ... alt="Name (P-001) ...".
_ITEM = re.compile(
    r'data-src="(https://onepiecetopdecks\.com/wp-content/gallery/[^"]+\.(?:jpg|jpeg|png))"'
    r'[^>]*alt="([^"]*?)\s*\((P-\d+)\)',
    re.I,
)


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def scrape_gallery() -> dict:
    """Return {code: (name, image_url)} for every promo in the gallery."""
    t = requests.get(URL, timeout=30, headers=HEADERS).text
    out = {}
    for img_url, name, code in _ITEM.findall(t):
        code = code.upper()
        if code not in out:                       # first (base print) wins
            out[code] = (html.unescape(name).strip(), img_url)
    return out


def seed() -> int:
    db = get_db()
    have = {r[0] for r in db.execute("SELECT card_id FROM cards").fetchall()}
    gallery = scrape_gallery()
    missing = {c: v for c, v in gallery.items() if c not in have}
    print(f"gallery promos: {len(gallery)} | already have: {len(gallery) - len(missing)} "
          f"| to add: {len(missing)}")

    os.makedirs(IMG_DIR, exist_ok=True)
    added = 0
    for code, (name, img_url) in sorted(missing.items()):
        try:
            r = requests.get(img_url, timeout=30, headers=HEADERS)
            r.raise_for_status()
            _flatten(Image.open(io.BytesIO(r.content))).save(
                f"{IMG_DIR}/{code}.jpg", "JPEG", quality=88)
        except Exception as exc:
            print(f"  {code}: image failed ({exc}) -- skipped")
            continue
        db.execute(
            """INSERT OR IGNORE INTO cards
               (card_id, name, set_id, set_name, card_type, region, image_url)
               VALUES (?,?,?,?,?,?,?)""",
            (code, name, "P", "Promo", "", "en", f"/assets/cards/{code}.jpg"),
        )
        added += 1

    db.commit()
    print(f"Added {added} promo cards (image + name only).")
    return added


if __name__ == "__main__":
    seed()
