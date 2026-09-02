"""Add JAPAN deck recipes from Cardrush media (cardrush.media/onepiece).

Cardrush is a major Japanese card-shop media site. Its /onepiece/decks pages are
Japanese tournament results — mostly Flagship Battle winners — with the tournament
name, placement (優勝 / ベスト8 …) and date, but NO individual player name (JP posts
credit the event, not the player). A real, scrapable JP meta source (chosen because
onepiecetopdecks blocks us and OnePiece.gg / OnePieceDB are JS-only).

It exposes ~10 recent decks in plain HTML. This ADDS them to meta_decks alongside
the Limitless Western pull — tagged country="JP", with the real tournament +
placement. Re-runnable.

Run:  python seed_meta_cardrush_jp.py
"""

import concurrent.futures as cf
import re

import requests

from database import get_db

LIST_URL = "https://cardrush.media/onepiece/decks"
BASE = "https://cardrush.media"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
           "Accept-Language": "ja,en"}

# <p ...>4<a ... href="/onepiece/cards/123">名前</a>(<!-- -->OP09-002<!-- -->)</p>
_CARD = re.compile(
    r'>(\d+)<a [^>]*href="/onepiece/cards/\d+"[^>]*>[^<]*</a>'
    r'\(<!--\s*-->\s*([A-Z]{2,4}\d{2}-\d{3})\s*<!--\s*-->\)')


def deck_ids() -> list[str]:
    t = requests.get(LIST_URL, timeout=25, headers=HEADERS).text
    return list(dict.fromkeys(re.findall(r"/onepiece/decks/(\d+)", t)))


def scrape_deck(deck_id: str) -> dict | None:
    try:
        t = requests.get(f"{BASE}/onepiece/decks/{deck_id}", timeout=25, headers=HEADERS).text
    except Exception:
        return None
    cards = [(int(q), c) for q, c in _CARD.findall(t)]
    if not cards:
        return None
    # The <h1> reads "<deck> | <date> <tournament> <placement>", e.g.
    # "... | 2026/6/26 フラッグシップバトル 優勝". No individual player is credited.
    date, tournament, placement = "", "", ""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.DOTALL)
    if h1:
        txt = re.sub(r"<[^>]+>", "", h1.group(1)).strip()
        md = re.search(r"(20\d{2})/(\d{1,2})/(\d{1,2})[\s　]*(.*)$", txt)
        if md:
            date = f"{md.group(2)}/{md.group(3)}/{md.group(1)}"
            after = md.group(4).strip()
            mp = re.search(r"(優勝|準優勝|ベスト\s*\d+|入賞|\d+位)\s*$", after)
            if mp:
                p = mp.group(1)
                tournament = after[: mp.start()].strip()
                placement = ("Winner" if p == "優勝" else "Runner-up" if p == "準優勝"
                             else "Top " + re.sub(r"\D", "", p) if "ベスト" in p else p)
            else:
                tournament = after
            tournament = tournament.replace("フラッグシップバトル", "Flagship Battle").strip()
    if not date:
        m = re.search(r"(20\d{2})[/年.-](\d{1,2})[/月.-](\d{1,2})", t)
        if m:
            date = f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
    return {"id": f"cardrush-{deck_id}", "date": date,
            "tournament": tournament, "placement": placement, "cards": cards}


def seed() -> int:
    db = get_db()

    def leader_of(codes):
        for c in codes:
            row = db.execute("SELECT card_type, name FROM cards WHERE card_id=?", (c,)).fetchone()
            if row and row[0] == "Leader":
                return c, row[1]
        return (codes[0], None) if codes else ("", None)

    ids = deck_ids()
    print(f"fetching {len(ids)} JP deck recipes from Cardrush ...")
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        decks = [d for d in ex.map(scrape_deck, ids) if d]

    added = 0
    for d in decks:
        codes = [c for _, c in d["cards"]]
        leader_id, leader_name = leader_of(codes)
        # Clean leader name so it groups with the Western data in the chart:
        # drop anything from the first " -" or " (" onward ("Enel (OP15-058)"->"Enel").
        profile = leader_name or leader_id or "Unknown"
        profile = re.sub(r"\s*[-(].*$", "", profile).strip() or (leader_id or "Unknown")
        db.execute(
            """INSERT OR REPLACE INTO meta_decks
               (id, event_date, country, event_name, event_type, players, winner, leader_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            # Real tournament + placement; no individual player is credited by the source.
            (d["id"], d["date"], "JP", profile,
             d.get("tournament") or "Japan event", d.get("placement", ""), "", leader_id),
        )
        db.execute("DELETE FROM meta_deck_cards WHERE deck_id=?", (d["id"],))
        for qty, code in d["cards"]:
            db.execute("INSERT INTO meta_deck_cards (deck_id, card_id, quantity) VALUES (?,?,?)",
                       (d["id"], code, qty))
        added += 1

    db.commit()
    print(f"Added {added} Japanese deck recipes from Cardrush.")
    return added


if __name__ == "__main__":
    seed()
