"""Scrape real tournament decklists from Limitless TCG (onepiece.limitlesstcg.com).

This scraper fetches:
1. The list of recent tournaments
2. Individual decklist pages for top-placing players
3. Parses the HTML data-id and data-count attributes to extract real card lists
"""

import re
import urllib.request
import urllib.error
import json
import hashlib
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def _fetch(url: str) -> str:
    """Fetch a URL and return the HTML as a string."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def scrape_decklist(list_id: int) -> dict | None:
    """Scrape a single decklist page from Limitless TCG.
    
    Example URL: https://onepiece.limitlesstcg.com/decks/list/6391
    
    Returns a dict with:
      - leader_id: str (e.g. "EB04-001")
      - leader_name: str
      - player: str
      - event: str
      - placement: str
      - cards: list of {card_id, name, quantity}
    """
    url = f"https://onepiece.limitlesstcg.com/decks/list/{list_id}"
    try:
        html = _fetch(url)
    except urllib.error.HTTPError as e:
        logger.warning(f"Failed to fetch decklist {list_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error fetching decklist {list_id}: {e}")
        return None
    
    # Extract title for deck name and player
    title_m = re.search(r'<title>(.*?)</title>', html)
    title = title_m.group(1) if title_m else ""
    
    # Extract meta description for event info
    desc_m = re.search(r'<meta name="description" content="(.*?)"', html)
    description = desc_m.group(1) if desc_m else ""
    
    # Parse player name from title (e.g. "Red/Yellow Bonney by AkemiTCG – Limitless One Piece")
    player_m = re.search(r'by\s+(.+?)\s+[–—-]\s+Limitless', title)
    player = player_m.group(1).strip() if player_m else "Unknown"
    
    # Parse deck archetype from title
    deck_name_m = re.search(r'^(.*?)\s+by\s+', title)
    deck_name = deck_name_m.group(1).strip() if deck_name_m else title.split('–')[0].strip()
    
    # Parse event info from description (e.g. "Red/Yellow Bonney decklist by AkemiTCG - 11th Place Treasure Cup Toronto - 17th May 2026")
    event_m = re.search(r'(\d+\w*\s+Place)\s+(.*?)\s*-\s*(\d+\w*\s+\w+\s+\d+)', description)
    placement = event_m.group(1) if event_m else ""
    event_name = event_m.group(2) if event_m else ""
    event_date = event_m.group(3) if event_m else ""
    
    # Parse all cards using data-count and data-id attributes
    card_pattern = re.compile(
        r'data-count="(\d+)"\s+data-id="([A-Za-z0-9-]+)".*?'
        r'<span class="card-name">(.*?)</span>',
        re.DOTALL
    )
    
    cards = []
    leader_id = None
    leader_name = None
    
    # Find the Leader section
    leader_section = re.search(
        r'<div class="decklist-column-heading">Leader</div>(.*?)</div>\s*<div class="decklist-column">',
        html, re.DOTALL
    )
    
    if leader_section:
        leader_m = re.search(
            r'data-count="(\d+)"\s+data-id="([A-Za-z0-9-]+)".*?<span class="card-name">(.*?)</span>',
            leader_section.group(1), re.DOTALL
        )
        if leader_m:
            leader_id = leader_m.group(2)
            leader_name = re.sub(r'\s*\([A-Za-z0-9-]+\)\s*$', '', leader_m.group(3)).strip()
            cards.append({
                "card_id": leader_id,
                "name": leader_name,
                "quantity": 1
            })
    
    # Find all cards (Characters + Events + Stages)
    for m in card_pattern.finditer(html):
        count = int(m.group(1))
        card_id = m.group(2)
        name = re.sub(r'\s*\([A-Za-z0-9-]+\)\s*$', '', m.group(3)).strip()
        
        # Skip if this is the leader (already added)
        if card_id == leader_id:
            continue
            
        cards.append({
            "card_id": card_id,
            "name": name,
            "quantity": count
        })
    
    if not cards:
        return None
    
    total_cards = sum(c["quantity"] for c in cards)
    
    return {
        "id": f"limitless-{list_id}",
        "deck_name": deck_name,
        "player": player,
        "event_name": event_name,
        "event_date": event_date,
        "placement": placement,
        "leader_id": leader_id or (cards[0]["card_id"] if cards else ""),
        "leader_name": leader_name or deck_name,
        "total_cards": total_cards,
        "cards": cards,
        "source_url": f"https://onepiece.limitlesstcg.com/decks/list/{list_id}",
    }


def scrape_tournament_results(tournament_id: int) -> list[dict]:
    """Scrape tournament results page to get list of decklists.
    
    Returns list of {player, placement, list_id, deck_name}
    """
    url = f"https://onepiece.limitlesstcg.com/tournaments/{tournament_id}"
    try:
        html = _fetch(url)
    except Exception as e:
        logger.warning(f"Failed to fetch tournament {tournament_id}: {e}")
        return []
    
    # Extract tournament name
    title_m = re.search(r'<title>(.*?)</title>', html)
    tournament_name = title_m.group(1).split('–')[0].strip() if title_m else f"Tournament {tournament_id}"
    
    # Find all decklist links in the results
    results = []
    # Pattern: links like /decks/list/XXXX
    list_pattern = re.compile(r'href="/decks/list/(\d+)"')
    
    for m in list_pattern.finditer(html):
        list_id = int(m.group(1))
        results.append({
            "list_id": list_id,
            "tournament_name": tournament_name,
        })
    
    # De-duplicate
    seen = set()
    unique = []
    for r in results:
        if r["list_id"] not in seen:
            seen.add(r["list_id"])
            unique.append(r)
    
    return unique


def fetch_recent_decklists(max_decks: int = 8) -> list[dict]:
    """Fetch recent tournament-winning decklists from Limitless TCG.
    
    Scrapes the tournaments page to find recent events, then fetches
    individual decklists from the top placing players.
    """
    # First get the recent tournaments
    url = "https://onepiece.limitlesstcg.com/tournaments"
    try:
        html = _fetch(url)
    except Exception as e:
        logger.error(f"Failed to fetch tournaments page: {e}")
        return []
    
    # Find tournament links
    tournament_pattern = re.compile(r'href="/tournaments/(\d+)"')
    tournament_ids = []
    seen = set()
    for m in tournament_pattern.finditer(html):
        tid = int(m.group(1))
        if tid not in seen:
            seen.add(tid)
            tournament_ids.append(tid)
    
    logger.info(f"Found {len(tournament_ids)} tournaments")
    
    # Collect decklists from the most recent tournaments
    all_list_ids = []
    for tid in tournament_ids[:5]:  # Check last 5 tournaments
        results = scrape_tournament_results(tid)
        logger.info(f"Tournament {tid}: found {len(results)} decklists")
        for r in results:
            all_list_ids.append(r["list_id"])
        if len(all_list_ids) >= max_decks * 2:
            break
    
    # Fetch individual decklists
    decks = []
    for list_id in all_list_ids[:max_decks]:
        logger.info(f"Fetching decklist {list_id}...")
        deck = scrape_decklist(list_id)
        if deck and deck["total_cards"] >= 40:
            decks.append(deck)
            logger.info(f"  -> {deck['deck_name']} by {deck['player']} ({deck['total_cards']} cards)")
    
    return decks


# --------------------------------------------------------------------------
# play.limitlesstcg.com — the CURRENT tournament platform.
#
# As of late 2026 Limitless moved live tournaments off onepiece.limitlesstcg.com
# (now a frozen archive ending ~Jul 2026) onto play.limitlesstcg.com, which uses
# hash tournament ids and server-rendered decklists. These functions scrape the
# current West/English meta from there:
#   /tournaments/completed?game=OP        -> completed tournaments
#   /tournament/<hash>/standings?game=OP  -> players + placements
#   /tournament/<hash>/player/<slug>/decklist -> the decklist (leader + cards)
# --------------------------------------------------------------------------
PLAY_BASE = "https://play.limitlesstcg.com"


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th', ..."""
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def play_completed_tournaments(limit: int = 30, min_players: int = 8) -> list[dict]:
    """Recent *completed* OP tournaments from play.limitlesstcg.com.

    Returns [{hash, date (ISO), name, players}], newest first, filtered to
    events with at least ``min_players`` real entrants.
    """
    try:
        html = _fetch(f"{PLAY_BASE}/tournaments/completed?game=OP")
    except Exception as e:
        logger.error(f"Failed to fetch completed tournaments: {e}")
        return []
    out = []
    for attrs, inner in re.findall(r'<tr ([^>]*data-date=[^>]*)>(.*?)</tr>', html, re.DOTALL):
        d = dict(re.findall(r'data-(\w+)="([^"]*)"', attrs))
        m = re.search(r'/tournament/([a-f0-9]+)/', inner)
        if not m:
            continue
        try:
            players = int(d.get("players", "0"))
        except ValueError:
            players = 0
        if players < min_players:
            continue
        out.append({"hash": m.group(1), "date": d.get("date", ""),
                    "name": d.get("name", ""), "players": players})
    out.sort(key=lambda t: t["date"], reverse=True)
    return out[:limit]


def play_standings(thash: str, top: int = 8) -> list[dict]:
    """Top-placing players of a tournament: [{placing, name, slug}]."""
    try:
        html = _fetch(f"{PLAY_BASE}/tournament/{thash}/standings?game=OP")
    except Exception as e:
        logger.warning(f"standings {thash}: {e}")
        return []
    out = []
    for placing, name, inner in re.findall(
            r'<tr data-placing="(\d+)" data-name="([^"]*)"[^>]*>(.*?)</tr>', html, re.DOTALL):
        slug = re.search(r'/player/([^"/?]+)"', inner)
        if slug:
            out.append({"placing": int(placing), "name": name.strip(), "slug": slug.group(1)})
    out.sort(key=lambda r: r["placing"])
    return out[:top]


def play_decklist(thash: str, slug: str) -> dict | None:
    """Scrape one player's decklist. Returns {leader_id, leader_name, cards:[{card_id,quantity}]}."""
    try:
        html = _fetch(f"{PLAY_BASE}/tournament/{thash}/player/{slug}/decklist")
    except Exception as e:
        logger.warning(f"decklist {thash}/{slug}: {e}")
        return None
    # Each card: <a href=".../cards/<CODE>">N Name</a>; the leader has no qty.
    leader_id, leader_name = "", ""
    qty: dict[str, int] = {}
    for code, n, nm in re.findall(r'/cards/([A-Z0-9-]+)"[^>]*>\s*(\d*)\s*([^<]*)</a>', html):
        if n == "":                      # no quantity -> the leader card
            if not leader_id:
                leader_id = code
                leader_name = re.sub(r'\s*\([A-Za-z0-9-]+\)\s*$', '', nm).strip()
        else:
            qty[code] = qty.get(code, 0) + int(n)
    cards = [{"card_id": k, "quantity": v} for k, v in qty.items()]
    if not cards:
        return None
    return {"leader_id": leader_id, "leader_name": leader_name, "cards": cards}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) > 1:
        # Scrape a specific decklist by ID
        list_id = int(sys.argv[1])
        deck = scrape_decklist(list_id)
        if deck:
            print(f"\n{'='*60}")
            print(f"Deck: {deck['deck_name']}")
            print(f"Player: {deck['player']}")
            print(f"Event: {deck['event_name']} ({deck['event_date']})")
            print(f"Placement: {deck['placement']}")
            print(f"Leader: {deck['leader_id']} ({deck['leader_name']})")
            print(f"Total Cards: {deck['total_cards']}")
            print(f"\nCard List:")
            for c in deck['cards']:
                print(f"  {c['quantity']}x {c['card_id']} ({c['name']})")
        else:
            print("Failed to scrape decklist.")
    else:
        # Fetch recent decklists
        decks = fetch_recent_decklists(max_decks=6)
        print(f"\nFetched {len(decks)} decklists:")
        for d in decks:
            print(f"  - {d['deck_name']} by {d['player'].encode('ascii', 'replace').decode()} ({d['total_cards']} cards) from {d['event_name']}")
