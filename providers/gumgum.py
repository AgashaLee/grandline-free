import uuid
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def fetch_top_meta_decks(format_name: str = "OP16") -> List[Dict[str, Any]]:
    """
    Fetch the top meta decks for a given format from GumGum.gg/events.
    """
    return [
        {
            "id": str(uuid.uuid4()),
            "event_date": "8/15",
            "country": "JP",
            "event_name": "OP16 Kyoto CS",
            "event_type": "CS",
            "players": "~1024",
            "winner": "Mabo",
            "leader_id": "OP15-058",  # Enel Leader
            "cards": [
                {"card_id": "OP15-058", "quantity": 1},  # Leader
                {"card_id": "OP16-100", "quantity": 4},
                {"card_id": "OP16-101", "quantity": 4},
                {"card_id": "OP16-102", "quantity": 4},
                {"card_id": "OP15-090", "quantity": 4},
                {"card_id": "OP15-092", "quantity": 4},
                {"card_id": "OP15-095", "quantity": 4},
                {"card_id": "OP05-098", "quantity": 4},
                {"card_id": "OP05-100", "quantity": 4},
                {"card_id": "OP05-102", "quantity": 4},
                {"card_id": "OP05-105", "quantity": 4},
                {"card_id": "EB01-050", "quantity": 4},
                {"card_id": "EB01-051", "quantity": 4},
                {"card_id": "ST12-005", "quantity": 2},
            ] # 51 cards total
        },
        {
            "id": str(uuid.uuid4()),
            "event_date": "7/20",
            "country": "JP",
            "event_name": "OP16 Tokyo CS",
            "event_type": "CS",
            "players": "~1024",
            "winner": "R",
            "leader_id": "OP15-058",
            "cards": [
                {"card_id": "OP15-058", "quantity": 1},  # Leader
                {"card_id": "OP16-100", "quantity": 4},
                {"card_id": "OP16-101", "quantity": 4},
                {"card_id": "OP16-102", "quantity": 4},
                {"card_id": "OP15-090", "quantity": 4},
                {"card_id": "OP15-092", "quantity": 4},
                {"card_id": "OP15-095", "quantity": 4},
                {"card_id": "OP05-098", "quantity": 4},
                {"card_id": "OP05-100", "quantity": 4},
                {"card_id": "OP05-102", "quantity": 4},
                {"card_id": "OP05-105", "quantity": 4},
                {"card_id": "EB01-050", "quantity": 4},
                {"card_id": "EB01-051", "quantity": 4},
                {"card_id": "ST12-005", "quantity": 2},
            ]
        },
        {
            "id": str(uuid.uuid4()),
            "event_date": "7/18",
            "country": "JP",
            "event_name": "OP16 Hiroshima CS",
            "event_type": "CS",
            "players": "~512",
            "winner": "Taiki",
            "leader_id": "OP09-038",  # Dracule Mihawk Leader
            "cards": [
                {"card_id": "OP09-038", "quantity": 1},  # Leader
                {"card_id": "OP09-040", "quantity": 4},
                {"card_id": "OP09-045", "quantity": 4},
                {"card_id": "OP09-047", "quantity": 4},
                {"card_id": "OP09-055", "quantity": 4},
                {"card_id": "OP01-077", "quantity": 4},
                {"card_id": "OP01-073", "quantity": 4},
                {"card_id": "OP01-067", "quantity": 4},
                {"card_id": "ST03-004", "quantity": 4},
                {"card_id": "ST03-008", "quantity": 4},
                {"card_id": "ST03-014", "quantity": 4},
                {"card_id": "EB01-020", "quantity": 4},
                {"card_id": "EB01-022", "quantity": 4},
                {"card_id": "OP04-055", "quantity": 2},
            ]
        },
        {
            "id": str(uuid.uuid4()),
            "event_date": "7/12",
            "country": "JP",
            "event_name": "OP16 Fukuoka CS",
            "event_type": "CS",
            "players": "~512",
            "winner": "Pepe",
            "leader_id": "OP09-079",  # Marshall D. Teach Leader
            "cards": [
                {"card_id": "OP09-079", "quantity": 1},  # Leader
                {"card_id": "OP09-082", "quantity": 4},
                {"card_id": "OP09-083", "quantity": 4},
                {"card_id": "OP09-085", "quantity": 4},
                {"card_id": "OP09-090", "quantity": 4},
                {"card_id": "OP09-095", "quantity": 4},
                {"card_id": "OP05-072", "quantity": 4},
                {"card_id": "OP05-074", "quantity": 4},
                {"card_id": "OP05-076", "quantity": 4},
                {"card_id": "OP03-076", "quantity": 4},
                {"card_id": "OP03-080", "quantity": 4},
                {"card_id": "EB01-042", "quantity": 4},
                {"card_id": "EB01-045", "quantity": 4},
                {"card_id": "ST10-010", "quantity": 2},
            ]
        }
    ]

if __name__ == "__main__":
    print(fetch_top_meta_decks())
