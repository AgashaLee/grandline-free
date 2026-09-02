"""JAPAN tournament decks from tcg-portal.jp (the JP meta source).

tcg-portal.jp exposes a clean JSON API of Japanese tournament results WITH full
50-card decklists -- unblocked from any IP (unlike onepiecetopdecks). This is our
primary JP source; it REPLACES the older Cardrush pull (`seed_meta_cardrush_jp`),
which this seeder also removes so /meta shows one consistent JP set.

APIs used (both public, GET, JSON):
  * /api/onepiece/cards            -> maps their internal card id -> real code
                                      (`groupKey`, e.g. OP17-022) + cardType.
  * /api/onepiece/tournament-results?hasDeckData=true
                                   -> tournaments with date, rank (優勝…), shop,
                                      deckGuide (JP archetype name) and deckData.

Notes / limitations:
  * A OP deck is 50 cards + a separate leader; tcg-portal stores the leader only
    as a Japanese archetype label (deckGuide.name, e.g. 緑ミホーク), not a leader
    card code. So event_name = that JP archetype and leader_id is left blank.
  * Card codes are the JP printing codes; ones our EN catalog lacks simply won't
    have an image (the code is still stored).

The 5k-card id->code map is cached to tcgportal_cardmap.json (committed so the
first run needs no rebuild); rebuilt automatically when missing or >14 days old.

Run:  python seed_meta_tcgportal_jp.py
"""

import json
import time
import urllib.request
from pathlib import Path

import config
from database import get_db

BASE = "https://tcg-portal.jp/api/onepiece"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
           "Accept": "application/json", "Accept-Language": "ja,en"}

CARD_MAP_FILE = config.BASE_DIR / "tcgportal_cardmap.json"
CARD_MAP_MAX_AGE = 14 * 86400  # rebuild the map if older than this
MAX_DECKS = 60                  # most-recent JP decks to import
PAGE_DELAY = 0.2               # polite gap between paged requests

# 優勝 = win, 準優勝 = runner-up, ベストN = Top N, N位 = Nth.
_RANK = {"優勝": "Winner", "準優勝": "Runner-up", "ベスト4": "Top 4",
         "ベスト8": "Top 8", "ベスト16": "Top 16", "ベスト32": "Top 32"}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _build_card_map() -> dict:
    """Page the cards API into {internal_id: [code, cardType]} and cache it."""
    cmap: dict[str, list] = {}
    page = 1
    while True:
        d = _get(f"{BASE}/cards?limit=60&page={page}")
        for c in d.get("data", []):
            cmap[c["id"]] = [c.get("groupKey"), c.get("cardType")]
        pg = d.get("pagination", {})
        if page >= pg.get("totalPages", page):
            break
        page += 1
        time.sleep(PAGE_DELAY)
    CARD_MAP_FILE.write_text(json.dumps(cmap, ensure_ascii=False), encoding="utf-8")
    return cmap


def load_card_map(force: bool = False) -> dict:
    if (not force and CARD_MAP_FILE.exists()
            and time.time() - CARD_MAP_FILE.stat().st_mtime < CARD_MAP_MAX_AGE):
        try:
            return json.loads(CARD_MAP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    print("building tcg-portal card map (one-time, ~85 pages)...")
    return _build_card_map()


def _placement(rank: str | None) -> str:
    rank = (rank or "").strip()
    if rank in _RANK:
        return _RANK[rank]
    if rank.endswith("位") and rank[:-1].isdigit():
        return rank[:-1] + ("st" if rank[:-1] == "1" else "nd" if rank[:-1] == "2"
                            else "rd" if rank[:-1] == "3" else "th")
    return rank


def _fetch_recent_decks(limit: int) -> list[dict]:
    decks: list[dict] = []
    page = 1
    while len(decks) < limit:
        d = _get(f"{BASE}/tournament-results?hasDeckData=true&page={page}&limit=20")
        batch = d.get("tournamentDecks", [])
        if not batch:
            break
        decks.extend(batch)
        pg = d.get("pagination", {})
        if page >= pg.get("totalPages", page):
            break
        page += 1
        time.sleep(PAGE_DELAY)
    return decks[:limit]


def seed(max_decks: int = MAX_DECKS) -> int:
    cmap = load_card_map()
    db = get_db()

    raw = _fetch_recent_decks(max_decks)
    print(f"fetched {len(raw)} JP tournament decks from tcg-portal.")

    # This source replaces the older JP pulls -> clear both, keep West (limitless).
    for prefix in ("tcgportal-", "cardrush-"):
        old = [r[0] for r in db.execute("SELECT id FROM meta_decks WHERE id LIKE ?", (prefix + "%",))]
        for did in old:
            db.execute("DELETE FROM meta_deck_cards WHERE deck_id=?", (did,))
            db.execute("DELETE FROM meta_decks WHERE id=?", (did,))

    added = 0
    for t in raw:
        main = (t.get("deckData") or {}).get("mainDeck") or []
        counts: dict[str, int] = {}
        for e in main:
            m = cmap.get(e.get("cardId"))
            if not m or not m[0]:
                continue
            counts[m[0]] = counts.get(m[0], 0) + int(e.get("quantity", 1))
        if not counts:
            continue

        guide = t.get("deckGuide") or {}
        archetype = (guide.get("name") or "").strip() or "Unknown"
        # The source has no leader card code, but does give the archetype's
        # representative card art -> use it as the leader thumbnail on /meta.
        leader_image = guide.get("representativeCardImage") or ""
        date = (t.get("date") or "")[:10]  # YYYY-MM-DD
        deck_id = f"tcgportal-{t['id']}"
        db.execute(
            """INSERT OR REPLACE INTO meta_decks
               (id, event_date, country, event_name, event_type, players, winner,
                leader_id, leader_image)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (deck_id, date, "JP", archetype,
             (t.get("tournamentName") or "Japan event").strip(),
             _placement(t.get("rank")), "", "", leader_image),
        )
        db.execute("DELETE FROM meta_deck_cards WHERE deck_id=?", (deck_id,))
        for code, qty in counts.items():
            db.execute("INSERT INTO meta_deck_cards (deck_id, card_id, quantity) VALUES (?,?,?)",
                       (deck_id, code, qty))
        added += 1

    db.commit()
    jp = db.execute("SELECT COUNT(*) FROM meta_decks WHERE country='JP'").fetchone()[0]
    print(f"Seeded {added} JP tournament decks from tcg-portal. Total JP decks now: {jp}.")
    return added


if __name__ == "__main__":
    seed()
