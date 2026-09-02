"""Download booster / set pack images from onepiecetopdecks.com into assets/.

Every set tile on the Card Database page uses ``assets/<SET>.jpg``. Images are
fetched and re-encoded to JPEG so png/webp sources still serve cleanly. To add
new sets later, grab the set->image map again from the site's /cards/ page and
extend SET_IMAGES below, then re-run.

Run:  python scrape_boxes.py
"""

import io
import json
import os

import requests
from PIL import Image

HEADERS = {"User-Agent": "Mozilla/5.0"}

# set code -> pack image URL, scraped from https://onepiecetopdecks.com/cards/
SET_IMAGES = json.loads(r"""
{"OP01":"https://onepiecetopdecks.com/wp-content/uploads/2022/05/OP-01.jpg",
 "OP02":"https://onepiecetopdecks.com/wp-content/uploads/2022/09/op02pack-min.jpg",
 "OP03":"https://onepiecetopdecks.com/wp-content/uploads/2023/02/op03pack.jpg",
 "OP04":"https://onepiecetopdecks.com/wp-content/uploads/2023/02/op04pack.jpg",
 "OP05":"https://onepiecetopdecks.com/wp-content/uploads/2023/08/op5pack.jpg",
 "OP06":"https://onepiecetopdecks.com/wp-content/uploads/2024/02/Screenshot-2024-02-07-at-11.55.01-AM.jpg",
 "OP07":"https://onepiecetopdecks.com/wp-content/uploads/2024/01/Screenshot-2024-01-05-at-11.38.08-AM.jpg",
 "OP08":"https://onepiecetopdecks.com/wp-content/uploads/2024/05/op08pack.jpg",
 "OP09":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/op9pack.jpg",
 "OP10":"https://onepiecetopdecks.com/wp-content/uploads/2024/11/op10pack.jpg",
 "OP11":"https://onepiecetopdecks.com/wp-content/uploads/2025/01/Screenshot-2025-01-10-at-8.48.55%E2%80%AFPM.jpg",
 "OP12":"https://onepiecetopdecks.com/wp-content/uploads/2025/05/op12pack.jpg",
 "OP13":"https://onepiecetopdecks.com/wp-content/uploads/2025/06/op13-pack.jpg",
 "OP14":"https://onepiecetopdecks.com/wp-content/uploads/2025/11/op14pack.jpg",
 "OP15":"https://onepiecetopdecks.com/wp-content/uploads/2026/02/op15pack.jpg",
 "OP16":"https://onepiecetopdecks.com/wp-content/uploads/2026/03/OP16PACK.jpg",
 "OP17":"https://onepiecetopdecks.com/wp-content/uploads/2026/07/op17pcks.jpg",
 "OP18":"https://onepiecetopdecks.com/wp-content/uploads/2026/08/Screenshot-2026-08-24-at-1.03.53-PM.jpg",
 "EB01":"https://onepiecetopdecks.com/wp-content/uploads/2024/01/eb01pax.jpg",
 "EB02":"https://onepiecetopdecks.com/wp-content/uploads/2025/01/EB02pack.jpg",
 "EB03":"https://onepiecetopdecks.com/wp-content/uploads/2025/10/eb03pack.jpg",
 "EB04":"https://onepiecetopdecks.com/wp-content/uploads/2026/01/eb04pack.jpg",
 "EB05":"https://onepiecetopdecks.com/wp-content/uploads/2026/08/EB05-1.jpg",
 "PRB01":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/prb01pack.jpg",
 "PRB02":"https://onepiecetopdecks.com/wp-content/uploads/2025/06/prb02pack.jpg",
 "P":"https://onepiecetopdecks.com/wp-content/uploads/2022/06/promopack.jpg",
 "ST01":"https://onepiecetopdecks.com/wp-content/uploads/2022/05/st1-min.jpg",
 "ST02":"https://onepiecetopdecks.com/wp-content/uploads/2022/05/st2-min.jpg",
 "ST03":"https://onepiecetopdecks.com/wp-content/uploads/2022/05/st3-min.jpg",
 "ST04":"https://onepiecetopdecks.com/wp-content/uploads/2022/05/st4-min.jpg",
 "ST05":"https://onepiecetopdecks.com/wp-content/uploads/2022/07/st05box.jpg",
 "ST06":"https://onepiecetopdecks.com/wp-content/uploads/2022/08/ST6box-min.jpg",
 "ST07":"https://onepiecetopdecks.com/wp-content/uploads/2022/10/st07.jpg",
 "ST08":"https://onepiecetopdecks.com/wp-content/uploads/2022/12/st08box-min.jpg",
 "ST09":"https://onepiecetopdecks.com/wp-content/uploads/2022/12/st09box-min.jpg",
 "ST10":"https://onepiecetopdecks.com/wp-content/uploads/2023/07/ST10.jpg",
 "ST11":"https://onepiecetopdecks.com/wp-content/uploads/2023/10/Uta-side-deck.jpg",
 "ST12":"https://onepiecetopdecks.com/wp-content/uploads/2023/11/st12bix.jpg",
 "ST13":"https://onepiecetopdecks.com/wp-content/uploads/2023/12/3brothers.jpg",
 "ST14":"https://onepiecetopdecks.com/wp-content/uploads/2024/04/st14pack.jpg",
 "ST15":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/img_thumbnail_st15.png",
 "ST16":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/img_thumbnail_st16.png",
 "ST17":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/img_thumbnail_st17.png",
 "ST18":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/img_thumbnail_st18.png",
 "ST19":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/img_thumbnail_st19.png",
 "ST20":"https://onepiecetopdecks.com/wp-content/uploads/2024/06/img_thumbnail_st20.png",
 "ST21":"https://onepiecetopdecks.com/wp-content/uploads/2024/12/st21box.jpg",
 "ST22":"https://onepiecetopdecks.com/wp-content/uploads/2025/04/ST22.jpg",
 "ST23":"https://onepiecetopdecks.com/wp-content/uploads/2025/05/img_thumbnail_st23.png",
 "ST24":"https://onepiecetopdecks.com/wp-content/uploads/2025/05/img_thumbnail_st24.png",
 "ST25":"https://onepiecetopdecks.com/wp-content/uploads/2025/05/img_thumbnail_st25.png",
 "ST26":"https://onepiecetopdecks.com/wp-content/uploads/2025/05/img_thumbnail_st26.png",
 "ST27":"https://onepiecetopdecks.com/wp-content/uploads/2025/05/img_thumbnail_st27.png",
 "ST28":"https://onepiecetopdecks.com/wp-content/uploads/2025/05/img_thumbnail_st28.png",
 "ST29":"https://onepiecetopdecks.com/wp-content/uploads/2025/12/img_item01.webp",
 "ST30":"https://onepiecetopdecks.com/wp-content/uploads/2026/04/ST30.jpg",
 "ST31":"https://onepiecetopdecks.com/wp-content/uploads/2026/06/ST31.jpg",
 "ST32":"https://onepiecetopdecks.com/wp-content/uploads/2026/06/ST32.jpg",
 "ST33":"https://onepiecetopdecks.com/wp-content/uploads/2026/06/ST33.jpg",
 "ST34":"https://onepiecetopdecks.com/wp-content/uploads/2026/06/ST34.jpg",
 "ST35":"https://onepiecetopdecks.com/wp-content/uploads/2026/06/ST35.jpg",
 "ST36":"https://onepiecetopdecks.com/wp-content/uploads/2026/06/ST36.jpg"}
""")


def _flatten(img: Image.Image) -> Image.Image:
    """Composite onto white before dropping the alpha channel.

    Some sources (e.g. ST29's .webp) are transparent PNG/webp with a drop
    shadow. A bare ``.convert("RGB")`` fills transparent pixels with *black*,
    which shows up as an ugly dark rectangle behind the box on the tile.
    Pasting onto a white canvas keeps the tidy edge the artwork was drawn for.
    """
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (28, 28, 30))  # dark set-tile colour, not white
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def scrape():
    os.makedirs("assets", exist_ok=True)
    ok = 0
    for set_id, url in SET_IMAGES.items():
        try:
            r = requests.get(url, timeout=30, headers=HEADERS)
            r.raise_for_status()
            _flatten(Image.open(io.BytesIO(r.content))).save(
                f"assets/{set_id}.jpg", "JPEG", quality=88)
            ok += 1
        except Exception as exc:
            print(f"  {set_id}: FAILED ({exc})")
    print(f"Saved {ok}/{len(SET_IMAGES)} set/pack images into assets/.")


if __name__ == "__main__":
    scrape()
