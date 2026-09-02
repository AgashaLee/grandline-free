"""Capture ALTERNATE ART from the regular set card-list pages (OP / EB / PRB / ST).

Each set page on onepiecetopdecks (e.g. /cards/op-11-.../) is a NextGEN gallery that
lists every printing of a card: the plain base image (``OP11-001.jpg``) plus any
alt-arts (``OP11-001_p1.png``, ``_sp``, ``_b`` ...). Leaders included.

We already show each card's base art (from optcgapi), so this adds ONLY the suffixed
alt-art variants to ``card_alt_arts`` -- they then appear as extra thumbnails in the
card's zoom (grid stays one tile per card). Same table/feature as seed_alt_arts.py.

Safe/idempotent: only codes already in ``cards`` are touched; INSERT OR IGNORE and a
skip-if-downloaded check make re-runs cheap.

Run:  python seed_set_alt_arts.py
"""

import io
import os
import re

import requests
from PIL import Image

from database import get_db

INDEX = "https://onepiecetopdecks.com/cards/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
IMG_DIR = "assets/alt"

# A gallery image URL -> capture (code, suffix, ext). Suffix empty => base print.
_IMG = re.compile(
    r'https://onepiecetopdecks\.com/wp-content/gallery/[^"]+/'
    r'([A-Z]+\d+-\d+)(_[A-Za-z0-9]+)?\.(jpg|jpeg|png)',
    re.I,
)


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def set_pages() -> list[str]:
    t = requests.get(INDEX, timeout=30, headers=HEADERS).text
    links = dict.fromkeys(re.findall(r'href="(https://onepiecetopdecks\.com/cards/[^"]+)"', t))
    return [l for l in links
            if re.search(r"/cards/(op|eb|prb|st)[-0-9]", l, re.I)
            and "promo" not in l and "event" not in l]


def seed() -> int:
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS card_alt_arts (
               card_id TEXT NOT NULL, image_url TEXT NOT NULL,
               UNIQUE(card_id, image_url))"""
    )
    have = {r[0] for r in db.execute("SELECT card_id FROM cards").fetchall()}

    # collect {code: set(alt image urls)} across every set page
    pairs: dict[str, set] = {}
    pages = set_pages()
    print(f"scanning {len(pages)} set pages ...")
    for u in pages:
        try:
            t = requests.get(u, timeout=30, headers=HEADERS).text
        except Exception as exc:
            print(f"  {u.split('/cards/')[1][:30]}: fetch failed ({exc})")
            continue
        for m in _IMG.finditer(t):
            code, suf, ext = m.group(1).upper(), m.group(2), m.group(3)
            if not suf:                       # plain base print -> we already show it
                continue
            if code in have:
                pairs.setdefault(code, set()).add(m.group(0))

    os.makedirs(IMG_DIR, exist_ok=True)
    added = 0
    for code, urls in sorted(pairs.items()):
        for img_url in sorted(urls):
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(img_url.split("?")[0]))
            local = f"/assets/alt/{fname}"
            if db.execute("SELECT 1 FROM card_alt_arts WHERE card_id=? AND image_url=?",
                          (code, local)).fetchone():
                continue
            if not os.path.exists(f"{IMG_DIR}/{fname}"):
                try:
                    r = requests.get(img_url, timeout=30, headers=HEADERS)
                    r.raise_for_status()
                    _flatten(Image.open(io.BytesIO(r.content))).save(
                        f"{IMG_DIR}/{fname}", "JPEG", quality=88)
                except Exception as exc:
                    print(f"  {code} {fname}: image failed ({exc}) -- skipped")
                    continue
            db.execute("INSERT OR IGNORE INTO card_alt_arts (card_id, image_url) VALUES (?,?)",
                       (code, local))
            added += 1

    db.commit()
    n_codes = db.execute("SELECT COUNT(DISTINCT card_id) FROM card_alt_arts").fetchone()[0]
    n_arts = db.execute("SELECT COUNT(*) FROM card_alt_arts").fetchone()[0]
    print(f"Added {added} set alt-arts. Now {n_arts} arts across {n_codes} cards.")
    return added


if __name__ == "__main__":
    seed()
