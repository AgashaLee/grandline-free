"""Daily JAPAN price snapshot -> powers the Japan Market Watch (Yuyu-tei, ¥).

Companion to ``snapshot_prices.py`` (which snapshots English/USD prices from
OPTCGAPI). This job scrapes Japanese yen prices from yuyu-tei.jp and stores ONE
representative price per card (its base/normal printing) into ``price_history_jp``
(card_id, date, price). Alt-art/manga JP movers are a later enhancement.

Efficiency: instead of one request per card (~2,800), it searches per SET
(``search_word=OP09`` returns the whole set in one page), so the full catalog is
covered in ~60 throttled requests. Verified reachable from Railway's IP.

Purely additive: never DROPs a table, never touches the West ``price_history``.
Re-running on the same day replaces that day's JP rows (idempotent).

Run:  python snapshot_prices_jp.py
"""

import datetime as _dt
import re
import time

import requests

from database import get_db

_BASE = "https://yuyu-tei.jp"
_SEARCH = _BASE + "/sell/opc/s/search?search_word={code}&rare=&type=&kizu=0"
_DELAY = 2.0          # seconds between set requests (be polite / avoid throttling)
_RETRY_BACKOFF = 4.0  # base seconds to wait before retrying a failed set page
_MAX_ATTEMPTS = 3     # retry a set page a few times (handles transient 403/429/timeouts)
_JPY_FLOOR = 50       # ignore sub-¥50 noise
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://yuyu-tei.jp/",
    "Upgrade-Insecure-Requests": "1",
}

# One card block on a Yuyu-tei search page (ported from providers/yuyutei.py):
# image src, code+rarity (alt), the badge code (the real card_id), the name
# (may carry a （パラレル）/(コミックパラレル) variant suffix), then the price.
_CARD_BLOCK = re.compile(
    r'src="([^"]+)"[^>]*alt="([A-Z0-9\-]+)\s+([A-Z\-]+)\s+[^"]*"'
    r'.*?text-center my-2">\s*([A-Z0-9\-]+)\s*</span>'
    r'.*?<h4[^>]*>\s*([^<]+?)\s*</h4>'
    r'.*?<strong[^>]*>\s*([\d,]+)\s*円',
    re.S,
)


def ensure_table(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS price_history_jp (
            card_id TEXT NOT NULL,
            date    TEXT NOT NULL,          -- ISO date, one row per card per day
            price   REAL NOT NULL,          -- Japanese yen (JPY)
            PRIMARY KEY (card_id, date)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_history_jp_date ON price_history_jp(date)"
    )
    db.commit()


def _set_codes(db) -> list[str]:
    """Distinct set codes from the catalog (OP09-076 -> OP09, P-075 -> P)."""
    rows = db.execute(
        "SELECT DISTINCT substr(card_id,1,instr(card_id,'-')-1) AS s FROM cards "
        "WHERE instr(card_id,'-')>0 AND s<>'' ORDER BY s"
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _base_price_for_page(html: str) -> dict[str, float]:
    """Return {card_id: base yen price} for a set-search page.

    'Base' = the printing whose name has no （variant） suffix; among those we take
    the lowest price -- the accessible 'from' price for that card. Yen ints;
    commas stripped. If a card has ONLY variant (（パラレル） etc.) listings -- common
    for brand-new sets where the cheap regular isn't stocked yet -- we record NO
    price for it, rather than passing off a pricey parallel as the 'regular' price
    (which made e.g. a ¥35 regular show as its ¥2,480 parallel).
    """
    per_card: dict[str, list[tuple[bool, float]]] = {}
    for m in _CARD_BLOCK.finditer(html):
        code = (m.group(4) or "").strip().upper()
        name = m.group(5) or ""
        try:
            price = float((m.group(6) or "").replace(",", ""))
        except ValueError:
            continue
        if not code or price < _JPY_FLOOR:
            continue
        is_variant = ("（" in name) or ("(" in name)  # （パラレル） etc.
        per_card.setdefault(code, []).append((is_variant, price))
    out: dict[str, float] = {}
    for code, entries in per_card.items():
        bases = [p for v, p in entries if not v]
        if bases:                       # regular printing only; skip parallel-only
            out[code] = min(bases)
    return out


def snapshot(day: str | None = None, codes: list[str] | None = None) -> int:
    db = get_db()
    ensure_table(db)
    day = day or _dt.date.today().isoformat()
    codes = codes or _set_codes(db)
    if not codes:
        print("No set codes in catalog -- aborting.")
        return 0

    session = requests.Session()
    session.headers.update(_HEADERS)

    def _fetch(code: str) -> str | None:
        """Fetch a set page, retrying transient failures (403/429/timeout)."""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = session.get(_SEARCH.format(code=code), timeout=25)
                if resp.status_code == 200:
                    return resp.text
                last = f"HTTP {resp.status_code}"
            except Exception as exc:
                last = str(exc)
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))  # 4s, 8s backoff
        print(f"  {code}: failed after {_MAX_ATTEMPTS} tries ({last}) -- skipped")
        return None

    prices: dict[str, float] = {}
    failed = 0
    for i, code in enumerate(codes):
        html = _fetch(code)
        if html is None:
            failed += 1
        else:
            found = _base_price_for_page(html)
            prices.update(found)
            print(f"  {code}: {len(found)} cards priced")
        if i < len(codes) - 1:
            time.sleep(_DELAY)
    if failed:
        print(f"  ({failed}/{len(codes)} set pages failed)")

    if not prices:
        print("No JP prices scraped -- aborting (nothing written). "
              "If every set failed, Yuyu-tei may be blocking this IP.")
        return 0

    # Only log prices for cards we actually have in the catalog.
    known = {r[0] for r in db.execute("SELECT card_id FROM cards").fetchall()}
    db.execute("DELETE FROM price_history_jp WHERE date=?", (day,))  # idempotent per day
    written = 0
    for cid, price in prices.items():
        if cid in known:
            db.execute(
                "INSERT OR REPLACE INTO price_history_jp (card_id, date, price) VALUES (?,?,?)",
                (cid, day, price),
            )
            written += 1
    db.commit()

    days = db.execute("SELECT COUNT(DISTINCT date) FROM price_history_jp").fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM price_history_jp").fetchone()[0]
    print(f"JP snapshot {day}: wrote {written} card prices (¥).")
    print(f"price_history_jp now holds {total} rows across {days} distinct day(s).")
    return written


if __name__ == "__main__":
    snapshot()
