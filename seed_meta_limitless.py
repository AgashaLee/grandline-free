"""Rebuild the West (English) Meta Decks from play.limitlesstcg.com.

Limitless moved its live tournaments off onepiece.limitlesstcg.com (that page is
now a frozen archive ending ~Jul 2026) onto play.limitlesstcg.com. This seed
pulls the current *completed* OP tournaments, their top decklists and cards,
then rebuilds the country='West' meta_decks. JP decks (seeded separately by
seed_meta_tcgportal_jp) are left untouched.

Run:  python seed_meta_limitless.py
"""

import concurrent.futures as cf
import datetime as _dt
import logging

from providers.limitless import (
    play_completed_tournaments, play_standings, play_decklist, _ordinal)
from database import get_db
from meta_format import format_of

logging.basicConfig(level=logging.ERROR)


def _human_date(iso: str) -> str:
    """'2026-11-18T18:00:00Z' -> '18th November 2026' (matches the West style
    that dashboard._event_date_key parses)."""
    try:
        d = _dt.date.fromisoformat(iso[:10])
        return f"{_ordinal(d.day)} {d.strftime('%B %Y')}"
    except Exception:
        return iso[:10]


def _collect_one(t: dict, per_tournament: int) -> list[dict]:
    """Top decklists for one tournament -> list of deck dicts (best-effort)."""
    out = []
    for s in play_standings(t["hash"], top=per_tournament):
        dl = play_decklist(t["hash"], s["slug"])
        if not dl or not dl.get("cards"):
            continue
        out.append({
            "id": f"play-{t['hash']}-{s['slug']}",
            "event_date": _human_date(t["date"]),
            "leader_name": dl["leader_name"],          # deck profile (chart grouping)
            "event_name": t["name"],                   # tournament name
            "placement": f"{_ordinal(s['placing'])} Place",
            "player": s["name"],
            "leader_id": dl["leader_id"],
            "cards": dl["cards"],
        })
    return out


def seed(max_tournaments: int = 30, per_tournament: int = 8, total_cap: int = 300,
         min_players: int = 8) -> int:
    db = get_db()
    tournaments = play_completed_tournaments(limit=max_tournaments, min_players=min_players)
    if not tournaments:
        print("No completed tournaments found — leaving the existing Meta data untouched.")
        return 0
    print(f"Found {len(tournaments)} completed tournaments; fetching decklists ...")

    # Collect top decklists across tournaments (modest parallelism, best-effort).
    decks: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(lambda t: _collect_one(t, per_tournament), tournaments):
            decks.extend(res)
            if len(decks) >= total_cap:
                break
    decks = decks[:total_cap]
    if not decks:
        print("No decks fetched — leaving the existing Meta data untouched.")
        return 0

    # Rebuild ONLY the West decks; JP (country='JP') must survive this refresh.
    db.execute("DELETE FROM meta_deck_cards WHERE deck_id IN "
               "(SELECT id FROM meta_decks WHERE country = 'West')")
    db.execute("DELETE FROM meta_decks WHERE country = 'West'")
    for d in decks:
        db.execute(
            """INSERT OR REPLACE INTO meta_decks
               (id, event_date, country, event_name, event_type, players, winner,
                leader_id, set_format)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (d["id"], d["event_date"], "West",
             d["leader_name"] or "",       # event_name col = deck profile / chart grouping
             d["event_name"],              # event_type col = tournament name
             d["placement"], d["player"], d["leader_id"],
             format_of(c["card_id"] for c in d["cards"])),
        )
        for c in d["cards"]:
            db.execute(
                "INSERT INTO meta_deck_cards (deck_id, card_id, quantity) VALUES (?,?,?)",
                (d["id"], c["card_id"], c.get("quantity", 1)),
            )
    db.commit()
    print(f"Rebuilt West Meta Decks: {len(decks)} decklists from play.limitlesstcg.com.")
    return len(decks)


if __name__ == "__main__":
    seed()
