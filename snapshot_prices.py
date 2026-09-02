"""Daily price snapshot -> powers Market Watch (movers) + portfolio history.

Unlike ``seed_cards.py`` (which DROPs and rebuilds the catalog), this job is
purely additive:

  1. Pull today's market prices from OPTCGAPI's bulk endpoints.
  2. APPEND one row per card into ``price_history`` (card_id, date, price).
  3. Refresh ``cards.market_price`` in place for cards we already have.

It never drops a table and never inserts new cards, so it is safe to run daily
even while ``allPromoCards`` is 404 (promos simply keep their existing price).
Re-running on the same day overwrites that day's rows (PK = card_id + date), so
it is idempotent.

Run:  python snapshot_prices.py
Cron: point a daily Railway job at this file once deployed.
"""

import datetime as _dt

import requests

from database import get_db

BASE = "https://optcgapi.com/api"
BULK_ENDPOINTS = ["allSetCards", "allSTCards", "allPromoCards"]
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _price(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ensure_table(db):
    """Create the history table (additive, safe to call every run)."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS price_history (
            card_id TEXT NOT NULL,
            date    TEXT NOT NULL,          -- ISO date, one row per card per day
            price   REAL NOT NULL,
            PRIMARY KEY (card_id, date)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(date)"
    )
    db.commit()


def _fetch_prices():
    """Return {card_id: price} from the bulk endpoints (skips ones that fail)."""
    prices = {}
    for ep in BULK_ENDPOINTS:
        try:
            data = requests.get(f"{BASE}/{ep}/", timeout=60, headers=HEADERS).json()
        except Exception as exc:  # network / JSON -- skip this batch, keep going
            print(f"  {ep}: fetch failed ({exc})")
            continue
        if not isinstance(data, list):
            print(f"  {ep}: unexpected response, skipped")
            continue
        n = 0
        for c in data:
            cid = str(c.get("card_set_id") or c.get("card_id") or "").strip().upper()
            p = _price(c.get("market_price"))
            if cid and p is not None:
                prices[cid] = p  # base print listed first wins ties well enough
                n += 1
        print(f"  {ep}: {n} priced cards")
    return prices


def snapshot(day: str | None = None):
    db = get_db()
    ensure_table(db)

    day = day or _dt.date.today().isoformat()
    prices = _fetch_prices()
    if not prices:
        print("No prices fetched -- aborting (nothing written).")
        return 0

    # Only record/refresh cards we actually have in the catalog.
    known = {r[0] for r in db.execute("SELECT card_id FROM cards").fetchall()}

    written = 0
    for cid, p in prices.items():
        if cid not in known:
            continue
        db.execute(
            "INSERT OR REPLACE INTO price_history (card_id, date, price) VALUES (?,?,?)",
            (cid, day, p),
        )
        db.execute("UPDATE cards SET market_price=? WHERE card_id=?", (p, cid))
        written += 1
    db.commit()

    days = db.execute("SELECT COUNT(DISTINCT date) FROM price_history").fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    print(f"Snapshot {day}: wrote {written} card prices.")
    print(f"price_history now holds {total} rows across {days} distinct day(s).")
    return written


if __name__ == "__main__":
    snapshot()
