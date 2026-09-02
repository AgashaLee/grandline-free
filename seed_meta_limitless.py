"""Rebuild the Meta Decks from Limitless TCG — the authoritative, unblocked
tournament + decklist source.

Pulls the most recent tournaments, their top decklists, and each deck's cards,
then rebuilds meta_decks / meta_deck_cards. Repeatable: run it any time to
refresh the Meta page and the meta-share chart with current data. Polite by
design (a handful of recent tournaments, capped decks, modest parallelism).

Run:  python seed_meta_limitless.py
"""

import concurrent.futures as cf
import logging
import re

from providers.limitless import _fetch, scrape_tournament_results, scrape_decklist
from database import get_db

logging.basicConfig(level=logging.ERROR)

TOURN_URL = "https://onepiece.limitlesstcg.com/tournaments"


def recent_tournament_ids(limit: int) -> list[int]:
    html = _fetch(TOURN_URL)
    ids = {int(i) for i in re.findall(r"/tournaments/(\d+)", html)}
    return sorted(ids, reverse=True)[:limit]


def seed(max_tournaments: int = 10, per_tournament: int = 8, total_cap: int = 70) -> int:
    db = get_db()

    # 1) collect decklist ids from recent tournaments
    list_ids: list[int] = []
    for tid in recent_tournament_ids(max_tournaments):
        try:
            for r in scrape_tournament_results(tid)[:per_tournament]:
                if r.get("list_id"):
                    list_ids.append(r["list_id"])
        except Exception as exc:
            print(f"  tournament {tid}: {exc}")
        if len(list_ids) >= total_cap:
            break
    list_ids = list(dict.fromkeys(list_ids))[:total_cap]
    print(f"fetching {len(list_ids)} decklists ...")

    # 2) scrape each decklist (parallel, best-effort)
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        decks = [d for d in ex.map(scrape_decklist, list_ids) if d and d.get("cards")]
    if not decks:
        print("No decks fetched — leaving the existing Meta data untouched.")
        return 0

    # 3) rebuild the meta tables from the fresh pull
    db.execute("DELETE FROM meta_deck_cards")
    db.execute("DELETE FROM meta_decks")
    for d in decks:
        db.execute(
            """INSERT OR REPLACE INTO meta_decks
               (id, event_date, country, event_name, event_type, players, winner, leader_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (d["id"], d.get("event_date", ""), "West",
             d.get("leader_name") or d.get("deck_name") or "",   # deck profile / chart grouping
             d.get("event_name", ""),                             # tournament name
             d.get("placement", ""), d.get("player", ""), d.get("leader_id", "")),
        )
        for c in d["cards"]:
            db.execute(
                "INSERT INTO meta_deck_cards (deck_id, card_id, quantity) VALUES (?,?,?)",
                (d["id"], c["card_id"], c.get("quantity", 1)),
            )
    db.commit()
    print(f"Rebuilt Meta Decks: {len(decks)} decklists from Limitless.")
    return len(decks)


if __name__ == "__main__":
    seed()
