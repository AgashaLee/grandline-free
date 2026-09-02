"""Scrape real tournament decklists from onepiecetopdecks.com.

This site stores decklists in TablePress tables with encoded card data.
Each row contains: decklist_encoded, image, color, deck_profile, deck_name,
date, country, author, placement, tournament, host.

The decklist encoding format is: 1nOP16-001a4nOP13-016a...
  - Delimiter between entries: 'a'
  - Delimiter between quantity and card_id: 'n'
  - First number is qty, second part is card_id
"""

import re
import urllib.request
import urllib.error
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Available format pages on onepiecetopdecks.com
FORMAT_PAGES = {
    "OP17-JP": "https://onepiecetopdecks.com/deck-list/japan-op17-deck-list-the-worlds-strongest-warriors/",
    "OP16-JP": "https://onepiecetopdecks.com/deck-list/japan-op16-deck-list-the-time-of-battle/",
    "OP15-JP": "https://onepiecetopdecks.com/deck-list/japan-op-15-deck-list-adventure-on-kamis-island/",
    "OP17-EN": "https://onepiecetopdecks.com/deck-list/english-op17-deck-list-the-worlds-strongest-warriors/",
    "OP16-EN": "https://onepiecetopdecks.com/deck-list/english-op16-deck-list-the-time-of-battle/",
    "OP15-EN": "https://onepiecetopdecks.com/deck-list/english-op15-eb04-deck-list-adventure-on-kamis-island/",
}


def _fetch(url: str) -> str:
    """Fetch a URL and return the HTML."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_decklist_string(encoded: str) -> list[dict]:
    """Parse the encoded decklist format used by onepiecetopdecks.com.
    
    Format: '1nOP16-001a4nOP13-016a4nOP16-015a...'
    - 'a' separates card entries
    - 'n' separates quantity from card_id
    """
    cards = []
    if not encoded or not encoded.strip():
        return cards
    
    entries = encoded.strip().split('a')
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split('n', 1)
        if len(parts) == 2:
            try:
                qty = int(parts[0])
                card_id = parts[1].strip().upper()
                if card_id and qty > 0:
                    cards.append({"card_id": card_id, "quantity": qty})
            except ValueError:
                continue
    
    return cards


def scrape_format_page(url: str) -> list[dict]:
    """Scrape a format page and extract all decklists.
    
    Returns a list of deck dicts with:
        - id, deck_name, deck_profile, color, date, country, 
          author, placement, tournament, host, leader_id, cards
    """
    try:
        html = _fetch(url)
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return []
    
    # Parse all table rows
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    decks = []
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) < 10:
            continue
        
        # Clean cell content (strip HTML tags)
        def clean(cell):
            return re.sub(r'<[^>]+>', '', cell).strip()
        
        encoded_decklist = clean(cells[0])
        color = clean(cells[2])
        deck_profile = clean(cells[3])
        deck_name = clean(cells[4])
        date = clean(cells[5])
        country = clean(cells[6])
        author = clean(cells[7])
        placement = clean(cells[8])
        tournament = clean(cells[9])
        host = clean(cells[10]) if len(cells) > 10 else ""
        
        # Parse the encoded decklist
        cards = parse_decklist_string(encoded_decklist)
        if not cards:
            continue
        
        # First card is always the leader
        leader_id = cards[0]["card_id"] if cards else ""
        
        total_cards = sum(c["quantity"] for c in cards)
        
        # Generate a stable ID based on content
        deck_id = f"topdecks-{deck_profile}-{author}-{date}".replace(" ", "-").replace("/", "-")
        
        decks.append({
            "id": deck_id,
            "deck_name": deck_name,
            "deck_profile": deck_profile,
            "color": color,
            "event_date": date,
            "country": country,
            "winner": author,
            "placement": placement,
            "tournament": tournament,
            "host": host,
            "leader_id": leader_id,
            "total_cards": total_cards,
            "cards": cards,
        })
    
    return decks


def fetch_all_decklists(formats: list[str] | None = None, max_per_format: int = 30) -> list[dict]:
    """Fetch decklists from specified format pages.
    
    Args:
        formats: List of format keys (e.g. ["OP17-JP", "OP16-JP"])
                 If None, fetches OP17-JP (latest).
        max_per_format: Maximum decklists to keep per format page.
    """
    if formats is None:
        formats = ["OP17-JP"]
    
    all_decks = []
    for fmt in formats:
        url = FORMAT_PAGES.get(fmt)
        if not url:
            logger.warning(f"Unknown format: {fmt}")
            continue
        
        logger.info(f"Fetching {fmt} from {url}")
        decks = scrape_format_page(url)
        logger.info(f"  Found {len(decks)} decklists")
        all_decks.extend(decks[:max_per_format])
    
    return all_decks


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    formats = sys.argv[1:] if len(sys.argv) > 1 else ["OP17-JP"]
    decks = fetch_all_decklists(formats)
    
    print(f"\nFetched {len(decks)} decklists:")
    for d in decks[:10]:
        name = d['deck_name'].encode('ascii', 'replace').decode()
        author = d['winner'].encode('ascii', 'replace').decode()
        print(f"  {d['event_date']} | {d['country']} | {name:20s} | {author:15s} | {d['placement']:12s} | {d['tournament']:12s} | {d['total_cards']} cards | Leader: {d['leader_id']}")
