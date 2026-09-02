"""Seed meta_decks from cached onepiecetopdecks.com HTML data.

Since the site blocks live scraping on sub-pages, this script reads
from the previously cached OP17-JP page to parse real decklists.
"""

import sys
import os
import re
sys.path.insert(0, os.path.dirname(__file__))

import portfolio
from database import get_db
from providers.topdecks import parse_decklist_string

# Cached OP17-JP page from onepiecetopdecks.com
CACHED_FILE = r'C:\Users\lang_\.gemini\antigravity-ide\brain\d78beea8-049e-464f-9bac-7d28daf91b42\.system_generated\steps\657\content.md'

def seed_from_cache(max_decks: int = 30):
    """Parse cached HTML and seed the database."""
    
    if not os.path.exists(CACHED_FILE):
        print(f"Cached file not found: {CACHED_FILE}")
        print("Falling back to Limitless TCG...")
        seed_from_limitless()
        return
    
    with open(CACHED_FILE, 'r', encoding='utf-8') as f:
        html = f.read()
    
    # Parse all table rows
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    # Clear existing meta decks
    db = get_db()
    db.execute("DELETE FROM meta_deck_cards")
    db.execute("DELETE FROM meta_decks")
    db.commit()
    
    count = 0
    for row in rows:
        if count >= max_decks:
            break
            
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) < 10:
            continue
        
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
        
        cards = parse_decklist_string(encoded_decklist)
        if not cards:
            continue
        
        leader_id = cards[0]["card_id"] if cards else ""
        total_cards = sum(c["quantity"] for c in cards)
        
        deck_id = f"topdecks-{deck_profile}-{author}-{date}".replace(" ", "-").replace("/", "-")
        
        # Map to our DB schema:
        # event_name -> deck_name (e.g. "Red Ace")
        # event_type -> tournament type (e.g. "SB", "ShopEvent")
        # players -> placement (e.g. "1st (4-0)")
        db_deck = {
            "id": deck_id,
            "event_date": date,
            "country": country,
            "event_name": deck_name,
            "event_type": tournament,
            "players": placement,
            "winner": author,
            "leader_id": leader_id,
            "cards": [{"card_id": c["card_id"], "quantity": c["quantity"]} for c in cards],
        }
        
        try:
            portfolio.save_meta_deck(db_deck)
            safe_name = deck_name.encode('ascii', 'replace').decode()
            safe_author = author.encode('ascii', 'replace').decode()
            print(f"  Saved: {safe_name:20s} by {safe_author:15s} | {date} | {country} | {placement} | {total_cards} cards | Leader: {leader_id}")
            count += 1
        except Exception as e:
            print(f"  Error: {e}")
    
    print(f"\nDone! Seeded {count} real tournament decklists from onepiecetopdecks.com (OP17-JP).")


def seed_from_limitless(max_decks: int = 8):
    """Fallback: Seed from Limitless TCG."""
    from providers.limitless import fetch_recent_decklists
    
    db = get_db()
    db.execute("DELETE FROM meta_deck_cards")
    db.execute("DELETE FROM meta_decks")
    db.commit()
    
    print("Fetching decklists from Limitless TCG...")
    decks = fetch_recent_decklists(max_decks=max_decks)
    
    for d in decks:
        db_deck = {
            "id": d["id"],
            "event_date": d.get("event_date", ""),
            "country": "",
            "event_name": d.get("deck_name", ""),
            "event_type": d.get("placement", ""),
            "players": "",
            "winner": d.get("player", "Unknown"),
            "leader_id": d.get("leader_id", ""),
            "cards": [{"card_id": c["card_id"], "quantity": c["quantity"]} for c in d["cards"]],
        }
        portfolio.save_meta_deck(db_deck)
        print(f"  Saved: {d.get('deck_name','?')} by {d.get('player','?')}")
    
    print(f"\nDone! Seeded {len(decks)} decklists from Limitless TCG.")


if __name__ == "__main__":
    seed_from_cache()
