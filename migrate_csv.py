import csv
from pathlib import Path
import config
from database import get_db

def migrate():
    path = config.COLLECTION_FILE
    if not path.exists():
        print("No CSV found.")
        return
        
    db = get_db()
    # Check if already migrated
    existing = db.execute("SELECT COUNT(*) as c FROM collections WHERE user_id = ''").fetchone()["c"]
    if existing > 0:
        print(f"Database already has {existing} records. Skipping migration.")
        return

    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            card_id = clean.get("card_id", "")
            if not card_id:
                continue
            
            db.execute("""
                INSERT INTO collections (user_id, card_id, name, region, variant, grade, condition, buy_price, buy_currency, quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "",
                card_id.upper(),
                clean.get("name") or card_id,
                (clean.get("region") or config.DEFAULT_REGION).lower(),
                clean.get("variant") or "Normal",
                (clean.get("grade") or "raw").lower(),
                (clean.get("condition") or "nm").lower(),
                float(clean.get("buy_price") or 0),
                (clean.get("buy_currency") or "IDR").upper(),
                int(float(clean.get("quantity") or 0))
            ))
            count += 1
    db.commit()
    print(f"Migrated {count} records from collection.csv to database!")

if __name__ == "__main__":
    migrate()
