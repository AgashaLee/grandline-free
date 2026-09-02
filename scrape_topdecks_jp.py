"""Carefully scrape JAPAN tournament decklists from onepiecetopdecks.com.

onepiecetopdecks blocks our LOCAL/residential IP (host-level 403), but not
Railway's datacenter IP -- so this is meant to be run FROM the deployed Railway
service (Console tab: `python scrape_topdecks_jp.py`), writing straight to the
volume DB. Running it locally will just hit the 403 and abort cleanly.

"Careful" = don't get the IP banned:
  * Probe ONE page first; if it's not 200, abort immediately (no hammering).
  * Realistic browser headers + Accept-Language ja.
  * Only the 3 JP format pages, one at a time, with a polite delay between them.
  * Never retry a block.

Seeds into meta_decks/meta_deck_cards using the same convention as
seed_meta_cardrush_jp.py (country="JP", event_name=cleaned leader/archetype for
the meta-share chart, event_type=tournament, players=placement). Unlike Cardrush,
onepiecetopdecks credits the player, so `winner` gets a real name. Idempotent
(stable per-deck id). Re-runnable.
"""

import re
import time
import urllib.error
import urllib.request

from database import get_db
from providers import topdecks

# Just the Japan pages. Kept small on purpose.
JP_PAGES = ["OP17-JP", "OP16-JP", "OP15-JP"]
DELAY_SECONDS = 6  # polite gap between page fetches
MAX_PER_PAGE = 40

_HEADERS = {**topdecks.HEADERS, "Accept-Language": "ja,en-US;q=0.8,en;q=0.6"}


def _get(url: str):
    """Fetch one page. Returns (status, html) or (status, '') on HTTP error."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return getattr(r, "status", 200), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # DNS / timeout / TLS
        return None, str(e)


def _leader(db, codes):
    """First Leader card in the list -> (leader_id, cleaned archetype name)."""
    lid = codes[0] if codes else ""
    lname = None
    for c in codes:
        row = db.execute("SELECT card_type, name FROM cards WHERE card_id=?", (c,)).fetchone()
        if row and row[0] == "Leader":
            lid, lname = c, row[1]
            break
    profile = re.sub(r"\s*[-(].*$", "", (lname or lid or "Unknown")).strip() or (lid or "Unknown")
    return lid, profile


def _seed_page(db, html: str) -> int:
    """Parse a JP format page's HTML and upsert its decks. Returns count added."""
    # Reuse the proven TablePress parser by feeding it the already-fetched HTML.
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    added = 0
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 10:
            continue
        clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
        cards = topdecks.parse_decklist_string(clean(cells[0]))
        if not cards:
            continue
        deck_name = clean(cells[4])
        date = clean(cells[5])
        author = clean(cells[7])
        placement = clean(cells[8])
        tournament = clean(cells[9])
        codes = [c["card_id"] for c in cards]
        leader_id, profile = _leader(db, codes)
        deck_id = ("topdecks-jp-" +
                   re.sub(r"[^a-zA-Z0-9]+", "-", f"{profile}-{author}-{date}-{leader_id}").strip("-").lower())
        db.execute(
            """INSERT OR REPLACE INTO meta_decks
               (id, event_date, country, event_name, event_type, players, winner, leader_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (deck_id, date, "JP", profile, tournament or "Japan event",
             placement, author, leader_id),
        )
        db.execute("DELETE FROM meta_deck_cards WHERE deck_id=?", (deck_id,))
        for c in cards:
            db.execute("INSERT INTO meta_deck_cards (deck_id, card_id, quantity) VALUES (?,?,?)",
                       (deck_id, c["card_id"], c["quantity"]))
        added += 1
    return added


def seed() -> int:
    # 1) Probe a single page. Abort on anything but 200 -- do NOT keep hitting.
    first_url = topdecks.FORMAT_PAGES[JP_PAGES[0]]
    status, body = _get(first_url)
    if status != 200:
        print(f"ABORT: probe of {JP_PAGES[0]} returned {status!r}. "
              f"IP is likely still blocked from here -- not scraping further.")
        return 0
    print(f"probe OK: {JP_PAGES[0]} -> 200, {len(body)} bytes. Scraping JP pages politely...")

    db = get_db()
    total = 0
    # First page already fetched -> parse it, then fetch the rest with delays.
    n = _seed_page(db, body)
    print(f"  {JP_PAGES[0]}: {n} decks")
    total += n
    for fmt in JP_PAGES[1:]:
        time.sleep(DELAY_SECONDS)
        status, body = _get(topdecks.FORMAT_PAGES[fmt])
        if status != 200:
            print(f"  {fmt}: got {status!r} -- stopping (staying polite).")
            break
        n = _seed_page(db, body)
        print(f"  {fmt}: {n} decks")
        total += n
    db.commit()

    jp = db.execute("SELECT COUNT(*) FROM meta_decks WHERE country='JP'").fetchone()[0]
    print(f"Done. Upserted {total} onepiecetopdecks JP decks. Total JP decks now: {jp}.")
    return total


if __name__ == "__main__":
    seed()
