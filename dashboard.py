"""Web dashboard -- same shape as tennis_predictor.py.

Pure standard library (no flask). Serves ``dashboard.html`` at ``/`` and the
portfolio as JSON at ``/api/data``.

    python main.py dashboard    ->  http://127.0.0.1:8802

This is a *presentation* module, exactly like report.py: it reads the same
PricedHolding objects and knows nothing about HTTP price sources, caching or
currency conversion. Adding it required no change to the business logic.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import fx
import auth
from cache import JsonCache
from cli import _parse_idr, _looks_like_card_code
from portfolio import (
    CollectionChanged,
    Holding,
    append_holding,
    compute_totals,
    load_collection,
    price_collection,
    remove_holding,
    update_holding,
)
from providers.base import BASE_VARIANT, ProviderPool, get_provider_for

HTML_PATH = config.BASE_DIR / "dashboard.html"

# --- Free site -> paid tracker funnel ---------------------------------------
#: The free pages (home, database, meta) are public and ad/affiliate supported;
#: they exist to send visitors to the paid tracker. Both links are
#: env-overridable so a deploy can retarget them without a code change.
TRACKER_URL = os.environ.get("TRACKER_URL", "https://optcg-app.up.railway.app")
WHOP_STORE_URL = os.environ.get("WHOP_STORE_URL", "https://whop.com/grand-line-store")

#: Pages anyone may read without a Whop membership. Everything else (the
#: tracker itself and the collection APIs) stays behind the gate.
PUBLIC_PAGES = {"/", "/database", "/meta", "/news", "/market"}
PUBLIC_API = {"/api/database", "/api/meta", "/api/news", "/api/market"}

#: Rebuilding hits the price cache, not the network, but there is no reason to
#: redo it for every browser poll.
PAYLOAD_TTL_SECONDS = 60

#: Refuse absurd request bodies outright rather than reading them into memory.
MAX_BODY_BYTES = 64 * 1024

_LOCK = threading.Lock()
#: Payload cache, keyed by user (so one customer never sees another's data).
#: In single-user mode there is one key, "".
_CACHE: dict[str, dict] = {}
#: Serialises writes to a collection file (ThreadingHTTPServer handles requests
#: concurrently, and read-modify-write on a CSV is not atomic).
_WRITE_LOCK = threading.Lock()

#: Per-request context: which user's data this request operates on. Set by the
#: Handler from the session cookie before any endpoint runs.
_ctx = threading.local()


from database import get_db

def _current_user_key() -> str:
    """Cache/identity key for the request: the user id, or "" single-user."""
    return getattr(_ctx, "user_key", "") or ""


def _invalidate() -> None:
    """Drop the cached payload for the current user (after they change data)."""
    with _LOCK:
        _CACHE.pop(_current_user_key(), None)


def _read_user_currency(user_id: str) -> str | None:
    db = get_db()
    row = db.execute("SELECT display_currency FROM users WHERE id = ?", (user_id,)).fetchone()
    return (row["display_currency"] or "").upper() if row and row["display_currency"] else None


def _write_user_currency(user_id: str, code: str) -> None:
    db = get_db()
    db.execute("INSERT INTO users (id, display_currency) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET display_currency=excluded.display_currency", (user_id, code))
    db.commit()


def _current_display_currency() -> str:
    """Currency for the request in flight -- the logged-in user's own choice in
    multi-user mode, or the site-wide setting locally."""
    return getattr(_ctx, "display_currency", None) or config.DISPLAY_CURRENCY


def _meta() -> dict:
    """Settings the page needs regardless of whether there are any cards yet
    (currency list, grades, regions) -- so the pickers work even when empty."""
    cur = _current_display_currency()
    return {
        "regions": sorted(config.PROVIDER_BY_REGION),
        "default_region": config.DEFAULT_REGION,
        "grades": list(config.GRADE_CHOICES),
        "display_currency": cur,
        "currencies": sorted(config.CURRENCY_FORMAT),
        "currency_symbol": config.currency_format(cur)[0],
        "currency_decimals": config.currency_format(cur)[1],
        "multi_user": auth.WHOP_ENABLED,
        "username": getattr(_ctx, "username", "") or "",
    }


def _static(name: str) -> bytes:
    """Read a file from disk."""
    return (config.BASE_DIR / name).read_bytes()


def _page(name: str) -> bytes:
    """Read an HTML page, filling in the site-wide funnel links.

    Keeps the Whop/tracker URLs in one place (and env-overridable) instead of
    hard-coded into three templates.
    """
    html = (config.BASE_DIR / name).read_text(encoding="utf-8")
    return (html.replace("{{TRACKER_URL}}", TRACKER_URL)
                .replace("{{WHOP_URL}}", WHOP_STORE_URL)).encode("utf-8")


def build_payload() -> dict:
    """Price the collection and return everything the page needs."""
    try:
        holdings = load_collection(_current_user_key())
    except Exception as exc:
        return {"error": str(exc), "rows": [], "totals": None, "empty": True, **_meta()}

    if not holdings:
        return {"error": "No cards yet — click “+ Add card” to add your first one.",
                "rows": [], "totals": None, "empty": True, **_meta()}

    cur = _current_display_currency()
    cache = JsonCache(config.CACHE_FILE, config.CACHE_TTL_HOURS)
    providers, rates = ProviderPool(cache), fx.RateBook(cache, cur)

    try:
        priced = price_collection(holdings, providers, rates, display_currency=cur)
    except fx.FxError as exc:
        return {"error": str(exc), "rows": [], "totals": None}
    totals = compute_totals(priced, display_currency=cur)

    rows = []
    for i, p in enumerate(priced):
        h = p.holding
        rows.append({
            # Position in collection.csv; edits send it back with the card id
            # and variant so a stale tab cannot modify the wrong row.
            "index": i,
            "variant_key": h.variant,
            "name": h.name,
            "card_id": h.card_id,
            "region": h.region,
            "grade": "" if h.grade == "raw" else h.grade,
            "condition": h.condition,
            "variant": "" if h.variant == BASE_VARIANT else h.variant,
            "qty": h.quantity,
            "buy": round(p.invested, 2),
            "buy_price": round(h.buy_price, 2),
            "buy_currency": h.buy_currency,
            "ok": p.ok,
            "currency": p.currency,
            "market_price": None if not p.ok else round(p.market_native, 2),
            "market_value": None if not p.ok else round(p.value, 2),
            "pl": None if not p.ok else round(p.pl, 2),
            "pl_pct": None if p.pl_pct is None else round(p.pl_pct, 2),
            # Trade-in (買取) value in the display currency, and the source's
            # stock flag (False = sold out = a demand signal). Both may be None.
            "buyback": None if p.buyback_value is None else round(p.buyback_value, 2),
            "buyback_native": None if p.buyback_native is None else round(p.buyback_native, 2),
            "in_stock": p.in_stock,
            "buy_url": p.buy_url,
        })

    return {
        "error": None,
        "rows": rows,
        **_meta(),
        "totals": {
            "invested": round(totals.invested, 2),
            "value": round(totals.value, 2),
            "pl": round(totals.pl, 2),
            "pl_pct": None if totals.pl_pct is None else round(totals.pl_pct, 2),
            "cards": sum(r["qty"] for r in rows if r["ok"]),
            "errors": totals.error_count,
            # Realistic "sell it all today" figure: sum of trade-in values for
            # the cards that have one (0 when no source offers a buy side).
            "sell_value": round(totals.sell_value, 2),
        },
        # Keep small rates meaningful: IDR->USD is ~0.0000589, which rounds to
        # zero at 4dp. Significant figures preserve it whatever the pair.
        "rates": {c: float(f"{r:.6g}") for c, r in rates.rates.items()},
        "fx_source": ", ".join(sorted(set(rates.sources.values()))) or "n/a",
        "provider": ", ".join(sorted({p.name for p in providers._by_region.values()})),
    }


def cached_payload(force: bool = False) -> dict:
    key = _current_user_key()
    with _LOCK:
        entry = _CACHE.get(key)
        if entry and not force and (time.time() - entry["built_at"]) < PAYLOAD_TTL_SECONDS:
            return dict(entry["payload"], built_at=entry["built_at"])

    fresh = build_payload()  # built outside the lock: pricing can be slow
    with _LOCK:
        _CACHE[key] = {"payload": fresh, "built_at": time.time()}
        return dict(fresh, built_at=_CACHE[key]["built_at"])


def _provider(region: str | None = None):
    """A provider for lookups, sharing the same on-disk price cache."""
    return get_provider_for(region or config.DEFAULT_REGION,
                            JsonCache(config.CACHE_FILE, config.CACHE_TTL_HOURS))


def _grade(payload: dict) -> str:
    """Read and validate a grade from a request, defaulting to raw."""
    grade = str(payload.get("grade") or "raw").strip().lower()
    if grade not in config.GRADE_CHOICES:
        raise ValueError(f"Unknown grade {grade!r}.")
    return grade


def _condition(payload: dict) -> str:
    cond = str(payload.get("condition") or "nm").strip().lower()
    if cond not in ("nm", "played"):
        raise ValueError(f"Unknown condition {cond!r}.")
    return cond


def _int(payload: dict, key: str, default: int | None = None) -> int | None:
    value = payload.get(key, default)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a whole number.") from None


def api_lookup(payload: dict) -> dict:
    """Return every printing of a card, so the browser can offer the choice."""
    code = str(payload.get("card_id", "")).strip().upper()
    if not _looks_like_card_code(code):
        raise ValueError("That doesn't look like a card code. They look like OP15-118.")

    region = str(payload.get("region") or config.DEFAULT_REGION).lower()
    provider = _provider(region)
    printings = provider.list_printings(code)
    if not printings:
        raise ValueError(f"No {region.upper()} card found for {code}.")

    rate = fx.RateBook(JsonCache(config.CACHE_FILE, config.CACHE_TTL_HOURS))(provider.currency)
    return {
        "card_id": code,
        "region": region,
        "currency": provider.currency,
        "rate": rate,
        "printings": [
            {"variant": p.variant, "label": p.label, "price": p.price_usd, "name": p.name}
            for p in printings
        ],
    }


def api_add(payload: dict) -> dict:
    """Add a card. The variant must be one the provider actually lists."""
    code = str(payload.get("card_id", "")).strip().upper()
    if not _looks_like_card_code(code):
        raise ValueError("That doesn't look like a card code. They look like OP15-118.")

    buy = _parse_idr(str(payload.get("buy_price_idr") or payload.get("buy_price") or ""))
    if buy is None:
        raise ValueError("Enter what you paid, e.g. 220000 or 220k.")
    quantity = _int(payload, "quantity", 1)
    if quantity is None or quantity < 1:
        raise ValueError("Quantity must be at least 1.")

    region = str(payload.get("region") or config.DEFAULT_REGION).lower()
    if region not in config.PROVIDER_BY_REGION:
        raise ValueError(f"Unknown region {region!r}.")

    variant = str(payload.get("variant") or BASE_VARIANT).strip()
    printings = {p.variant: p for p in _provider(region).list_printings(code)}
    if printings and variant not in printings:
        raise ValueError("Pick which version of the card you own.")
    name = printings[variant].name if variant in printings else code
    grade = _grade(payload)
    condition = _condition(payload)

    with _WRITE_LOCK:
        append_holding(_current_user_key(),
                       Holding(name, code, buy, quantity, variant, region, grade, condition, config.DISPLAY_CURRENCY))
    _invalidate()
    return {"added": {"card_id": code, "variant": variant, "region": region, "grade": grade, "condition": condition, "quantity": quantity}}


def api_update(payload: dict) -> dict:
    """Change the buy price and/or quantity of an existing row."""
    index = _int(payload, "index")
    if index is None:
        raise ValueError("Missing row.")

    buy_raw = payload.get("buy_price_idr") or payload.get("buy_price")
    buy = None
    if buy_raw not in (None, ""):
        buy = _parse_idr(str(buy_raw))
        if buy is None:
            raise ValueError("Enter a valid amount, e.g. 220000 or 220k.")

    # grade is optional on update: only re-validate it if the field was sent.
    grade = _grade(payload) if "grade" in payload else None
    condition = _condition(payload) if "condition" in payload else None

    with _WRITE_LOCK:
        updated = update_holding(
            _current_user_key(),
            index,
            str(payload.get("card_id", "")),
            str(payload.get("variant") or BASE_VARIANT),
            buy_price=buy,
            quantity=_int(payload, "quantity"),
            grade=grade,
            condition=condition,
        )
    _invalidate()
    return {"updated": {"quantity": updated.quantity, "buy_price": updated.buy_price, "grade": updated.grade, "condition": updated.condition}}


def api_remove(payload: dict) -> dict:
    """Sell all or part of a row."""
    index = _int(payload, "index")
    if index is None:
        raise ValueError("Missing row.")

    with _WRITE_LOCK:
        kept = remove_holding(
            _current_user_key(),
            index,
            str(payload.get("card_id", "")),
            str(payload.get("variant") or BASE_VARIANT),
            quantity=_int(payload, "quantity"),
        )
    _invalidate()
    return {"remaining": None if kept is None else kept.quantity}


def api_settings(payload: dict) -> dict:
    """Change the reporting currency. Takes effect on the next refresh.

    In multi-user mode the choice is saved per-user (each member sees their own
    currency); locally it sets the single site-wide currency."""
    code = config.normalize_currency(str(payload.get("display_currency", "")))
    if auth.WHOP_ENABLED:
        user_id = _current_user_key()
        if not user_id:
            raise ValueError("Please log in to change your currency.")
        _write_user_currency(user_id, code)
        _ctx.display_currency = code
    else:
        config.set_display_currency(code)
    _invalidate()  # every figure on the page is now in a different currency
    return {"display_currency": code}


def api_image(payload: dict) -> dict:
    """Return a card's picture URL, fetched on demand when a name is clicked.

    The URL is cached so repeat clicks don't re-hit the source. Grade doesn't
    change the art, so it's ignored -- we look up by card + variant only.
    """
    code = str(payload.get("card_id", "")).strip().upper()
    if not _looks_like_card_code(code):
        raise ValueError("That doesn't look like a card code.")
    region = str(payload.get("region") or config.DEFAULT_REGION).lower()
    variant = str(payload.get("variant") or BASE_VARIANT)

    cache = JsonCache(config.CACHE_FILE, config.CACHE_TTL_HOURS)
    provider = get_provider_for(region, cache)
    key = f"img:{provider.name}:{code}:{variant}"

    cached = cache.get(key) or cache.get_stale(key)  # image URLs rarely change
    if isinstance(cached, str):
        return {"card_id": code, "variant": variant, "image": cached}

    printings = provider.list_printings(code)
    match = next((p for p in printings if p.variant == variant), None)
    image = (match.image_url if match else None) or (printings[0].image_url if printings else None)
    if image:
        cache.set(key, image)
    return {"card_id": code, "variant": variant, "image": image}


def api_deck_cost(payload: dict) -> dict:
    """Parse a decklist and calculate the cost to finish it based on the user's collection."""
    decklist_text = str(payload.get("decklist") or "").strip()
    if not decklist_text:
        raise ValueError("Please provide a decklist.")
        
    # Accept the common decklist formats: "4x OP01-016", "4 OP01-016",
    # "OP01-016 x4", and code + quantity on SEPARATE lines (what most deck
    # exporters and onepiecetopdecks produce).
    QTY = re.compile(r"^x?(\d+)x?$", re.IGNORECASE)
    CODE = re.compile(r"^([A-Za-z]{1,4}\d{0,2}-\d{1,4})$")
    tokens = decklist_text.replace(",", " ").split()
    required: dict[str, int] = {}
    pending_qty = None
    i = 0
    while i < len(tokens):
        mc = CODE.match(tokens[i])
        mq = QTY.match(tokens[i])
        if mc:
            code = mc.group(1).upper()
            if pending_qty is not None:
                qty, pending_qty = pending_qty, None
            elif i + 1 < len(tokens) and QTY.match(tokens[i + 1]):
                qty = int(QTY.match(tokens[i + 1]).group(1)); i += 1
            else:
                qty = 1
            required[code] = required.get(code, 0) + qty
        elif mq:
            pending_qty = int(mq.group(1))
        i += 1

    if not required:
        raise ValueError("Could not find any cards in the decklist. Use format '4x OP01-016'.")

    owned: dict[str, int] = {}
    holdings = load_collection(_current_user_key())
    for h in holdings:
        owned[h.card_id] = owned.get(h.card_id, 0) + h.quantity

    missing: dict[str, int] = {}
    for code, req_qty in required.items():
        own = owned.get(code, 0)
        if own < req_qty:
            missing[code] = req_qty - own

    cur = _current_display_currency()
    cache = JsonCache(config.CACHE_FILE, config.CACHE_TTL_HOURS)
    providers, rates = ProviderPool(cache), fx.RateBook(cache, cur)
    
    region = config.DEFAULT_REGION
    provider = providers(region)
    rate = rates(provider.currency)

    def _price_card(item):
        """Best-effort price for one card; a slow/failed lookup yields no price."""
        code, qty = item
        try:
            price = provider.get_price(code)
        except Exception:
            price = None
        try:
            buy_url = provider.get_buy_url(code)
        except Exception:
            buy_url = None
        try:
            name = provider.get_card_name(code) or code
        except Exception:
            name = code
        return {
            "card_id": code,
            "name": name,
            "missing_qty": qty,
            "unit_price": None if price is None else price * rate,
            "total_cost": None if price is None else price * rate * qty,
            "buy_url": buy_url,
        }

    # Price missing cards in parallel so a full deck finishes fast instead of
    # timing out ("Failed to fetch") on sequential live scrapes.
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=8) as ex:
        missing_details = list(ex.map(_price_card, missing.items()))
    missing_details.sort(key=lambda r: r["card_id"])
    total_cost = sum(r["total_cost"] for r in missing_details if r["total_cost"])

    return {
        "required_cards": sum(required.values()),
        "owned_cards": sum(required.values()) - sum(missing.values()),
        "missing_cards_total": sum(missing.values()),
        "total_cost": total_cost,
        "missing_details": missing_details,
    }

def api_database(payload: dict) -> dict:
    """Return the global card catalog for the public Database page.

    A single ``set=OP-01`` filter narrows it to one set (lighter payload).

    ``card_text`` is included for the click-to-zoom detail view. Prices are NOT
    sent here: the free site hides them (the Buy button goes to Indonesian
    marketplaces), and market prices stay a paid-tracker feature only.
    """
    db = get_db()
    set_id = (payload.get("set") or "").strip() if isinstance(payload, dict) else ""
    cols = ("card_id, name, set_id, set_name, rarity, card_type, card_color, "
            "card_cost, card_power, card_text, attribute, counter, sub_types, "
            "life, image_url")
    if set_id:
        rows = db.execute(f"SELECT {cols} FROM cards WHERE set_id=? ORDER BY card_id", (set_id,)).fetchall()
    else:
        rows = db.execute(f"SELECT {cols} FROM cards ORDER BY set_id, card_id").fetchall()
    cards = [dict(r) for r in rows]

    # Attach event/bonus alternate artwork (shown in the card's zoom, not the grid).
    # Table may not exist yet if seed_alt_arts.py hasn't been run -- treat as none.
    try:
        alt: dict[str, list[str]] = {}
        for cid, url in db.execute("SELECT card_id, image_url FROM card_alt_arts"):
            alt.setdefault(cid, []).append(url)
        for c in cards:
            arts = alt.get(c["card_id"])
            if arts:
                c["alt_arts"] = arts
    except Exception:
        pass

    return {"cards": cards}

def api_meta(payload: dict) -> dict:
    """Return all meta decks with their cards."""
    import portfolio
    decks = portfolio.get_meta_decks()
    return {"decks": decks}


#: Cards below this baseline price are excluded from the movers list: a $0.03 ->
#: $0.06 penny card is a "+100%" mover that means nothing and would swamp the
#: real signal. Kept modest so genuine sub-$1 movers still show.
_MOVER_MIN_PRICE = 0.25


def api_market(payload: dict) -> dict:
    """Biggest price gainers / losers for the free Market Watch page.

    Reads the ``price_history`` log written by ``snapshot_prices.py`` and returns
    percentage movers over a window (default 7 days). Deliberately returns **no
    dollar prices** -- only percent change, rank and direction -- because raw
    prices stay a paid-tracker feature on the free site. Exact prices are the
    upsell.

    Until at least two distinct snapshot days exist the page has nothing to
    compare, so we return ``ready: False`` and the frontend shows a
    "collecting data" state instead of an empty table.
    """
    db = get_db()
    try:
        window = int((payload or {}).get("window", 7))
    except (TypeError, ValueError):
        window = 7
    window = max(1, min(window, 90))
    # Value tier: min baseline price to qualify. Lets visitors surface high-value
    # movers (e.g. $20+ manga/SEC/alt-art) instead of only volatile penny cards,
    # WITHOUT exposing exact prices (only the tier label is shown). Restricted to
    # a fixed set so an arbitrary value can't be probed to reveal a price.
    try:
        min_price = float((payload or {}).get("min", _MOVER_MIN_PRICE))
    except (TypeError, ValueError):
        min_price = _MOVER_MIN_PRICE
    if min_price not in (0.25, 5.0, 20.0):
        min_price = _MOVER_MIN_PRICE
    limit = 50

    # No history table yet (snapshot job never ran) -> not ready.
    try:
        dates = [r[0] for r in db.execute(
            "SELECT DISTINCT date FROM price_history ORDER BY date").fetchall()]
    except Exception:
        return {"ready": False, "reason": "no-history"}

    if len(dates) < 2:
        return {"ready": False, "reason": "collecting",
                "days": len(dates), "latest": dates[-1] if dates else None}

    latest = dates[-1]
    # Baseline = newest snapshot at or before (latest - window); if the log is
    # younger than the window, fall back to the earliest snapshot we have.
    cutoff = (_dt.date.fromisoformat(latest) - _dt.timedelta(days=window)).isoformat()
    baseline = next((d for d in reversed(dates) if d <= cutoff), dates[0])
    if baseline == latest:
        baseline = dates[0]

    rows = db.execute(
        """SELECT n.card_id, c.name, c.set_id, c.set_name, c.rarity,
                  c.card_type, c.card_color, c.image_url,
                  o.price AS old_price, n.price AS new_price
             FROM price_history n
             JOIN price_history o ON o.card_id = n.card_id AND o.date = ?
             JOIN cards c        ON c.card_id = n.card_id
            WHERE n.date = ? AND o.price >= ? AND o.price > 0""",
        (baseline, latest, min_price),
    ).fetchall()

    movers = []
    for r in rows:
        old, new = r["old_price"], r["new_price"]
        pct = round((new - old) / old * 100, 1)
        if pct == 0:
            continue
        movers.append({
            "card_id": r["card_id"], "name": r["name"],
            "set_id": r["set_id"], "set_name": r["set_name"],
            "rarity": r["rarity"], "card_type": r["card_type"],
            "card_color": r["card_color"], "image_url": r["image_url"],
            "pct": pct,
        })

    movers.sort(key=lambda m: m["pct"], reverse=True)
    gainers = [m for m in movers if m["pct"] > 0][:limit]
    losers = [m for m in movers if m["pct"] < 0]
    losers.sort(key=lambda m: m["pct"])
    losers = losers[:limit]

    return {
        "ready": True,
        "latest": latest,
        "baseline": baseline,
        "window": window,
        "min": min_price,
        "compared": len(movers),
        "gainers": gainers,
        "losers": losers,
    }


#: Aggregated One Piece TCG news via Google News RSS (free, no key, legal to
#: syndicate headlines). Cached in memory so we hit Google at most twice an hour.
_NEWS_CACHE: dict = {"at": 0.0, "items": []}
_NEWS_TTL = 1800  # seconds
_NEWS_MAX = 60    # how many headlines to keep (was hard-capped at 18)
_NEWS_URL = ("https://news.google.com/rss/search?"
             "q=%22one+piece+card+game%22&hl=en-US&gl=US&ceid=US:en")


_NEWS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _own_posts() -> list:
    """The site's own featured posts, from an editable own_posts.json.
    Read fresh each call so edits show up immediately (no 30-min wait)."""
    try:
        p = config.BASE_DIR / "own_posts.json"
        if p.exists():
            text = (p.read_text(encoding="utf-8")
                    .replace("{{WHOP_URL}}", WHOP_STORE_URL)
                    .replace("{{TRACKER_URL}}", TRACKER_URL))
            data = json.loads(text)
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def api_news(payload: dict | None = None) -> dict:
    """Return the site's own featured posts + recent OP TCG news headlines
    (title, source, date, link, thumbnail image)."""
    featured = _own_posts()
    now = time.time()
    if _NEWS_CACHE["items"] and now - _NEWS_CACHE["at"] < _NEWS_TTL:
        return {"featured": featured, "items": _NEWS_CACHE["items"], "cached": True}

    import email.utils
    import urllib.request
    import xml.etree.ElementTree as ET
    try:
        req = urllib.request.Request(_NEWS_URL, headers={"User-Agent": _NEWS_UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            root = ET.fromstring(resp.read())
        items = []
        for it in root.findall(".//item"):
            title = (it.findtext("title") or "").strip()
            src = it.find("source")
            source = (src.text or "").strip() if src is not None else ""
            if source and title.endswith(f" - {source}"):
                title = title[: -(len(source) + 3)].strip()
            pub = (it.findtext("pubDate") or "").strip()
            try:
                ts = email.utils.parsedate_to_datetime(pub).timestamp() if pub else 0.0
            except Exception:
                ts = 0.0
            items.append({
                "title": title,
                "link": (it.findtext("link") or "").strip(),
                "source": source,
                "date": pub,
                "_ts": ts,
            })
        # Newest first, then keep a generous number (was hard-capped at 18).
        items.sort(key=lambda x: x["_ts"], reverse=True)
        items = items[:_NEWS_MAX]
        for x in items:
            x.pop("_ts", None)
        if items:
            _NEWS_CACHE["items"], _NEWS_CACHE["at"] = items, now
        return {"featured": featured, "items": items}
    except Exception:
        if _NEWS_CACHE["items"]:
            return {"featured": featured, "items": _NEWS_CACHE["items"], "stale": True}
        return {"featured": featured, "items": [], "error": "News is unavailable right now — try again soon."}


ROUTES = {
    "/api/lookup": api_lookup,
    "/api/add": api_add,
    "/api/update": api_update,
    "/api/remove": api_remove,
    "/api/settings": api_settings,
    "/api/image": api_image,
    "/api/deck_cost": api_deck_cost,
    "/api/database": api_database,
    "/api/meta": api_meta,
    "/api/market": api_market,
    "/api/news": api_news,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str, extra_headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url: str, extra_headers=None) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()

    def _cookie(self, name: str) -> str | None:
        from http.cookies import SimpleCookie
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        try:
            jar = SimpleCookie()
            jar.load(raw)
        except Exception:
            return None
        m = jar.get(name)
        return m.value if m else None

    def _bind_context(self) -> dict | None:
        """Point this request at the right user's data. Returns the session
        (or None). Resets to single-user defaults first so a reused worker
        thread never leaks the previous request's user."""
        _ctx.user_key = ""
        _ctx.username = ""
        _ctx.display_currency = None
        if not auth.WHOP_ENABLED:
            return None  # single-user / local mode
        session = auth.get_session(self._cookie(auth.COOKIE_SESSION))
        if session:
            _ctx.user_key = session["user_id"]
            _ctx.username = session.get("username", "")
            _ctx.display_currency = _read_user_currency(session["user_id"])
        return session

    def _gate(self) -> None:
        body = auth.gate_page(
            "Members only",
            "This One Piece card tracker is for active members. "
            "Subscribe on Whop to unlock it, or log in if you already have.",
            "Subscribe on Whop", auth.WHOP_PRODUCT_URL,
            "I already subscribed — log in", "/whop/login")
        self._send(200, body, "text/html; charset=utf-8")

    # --- Whop OAuth ------------------------------------------------------
    def _login(self) -> None:
        if not auth.WHOP_ENABLED:
            return self._send(503, b"Login not configured", "text/plain")
        state, _nonce, verifier, url = auth.new_login_state()
        secure = "; Secure" if os.environ.get("PORT") else ""
        self._redirect(url, extra_headers=[
            ("Set-Cookie", f"{auth.COOKIE_STATE}={state}; Path=/; HttpOnly; Max-Age=600; SameSite=Lax{secure}"),
            ("Set-Cookie", f"{auth.COOKIE_VERIFIER}={verifier}; Path=/; HttpOnly; Max-Age=600; SameSite=Lax{secure}"),
        ])

    def _callback(self, query: str) -> None:
        from urllib.parse import parse_qs
        if not auth.WHOP_ENABLED:
            return self._send(503, b"Login not configured", "text/plain")
        params = parse_qs(query)
        if params.get("error"):
            return self._send(400, f"Login error: {params.get('error')[0]}".encode(), "text/plain")
        code = (params.get("code") or [""])[0]
        state = (params.get("state") or [""])[0]
        if not code or state != self._cookie(auth.COOKIE_STATE):
            return self._send(403, b"Login check failed - please try again.", "text/plain")
        verifier = self._cookie(auth.COOKIE_VERIFIER)
        try:
            tokens = auth.exchange_code(code, verifier)
            user = auth.user_info(tokens["access_token"])
        except Exception as exc:  # surface the failure rather than a blank page
            return self._send(500, f"Login failed: {exc}".encode(), "text/plain; charset=utf-8")

        user_id, username = auth.extract_identity(user)
        if not user_id:
            return self._send(500, b"Could not read your Whop identity.", "text/plain")
        if not auth.has_active_membership(user_id, tokens.get("access_token")):
            return self._send(200, auth.gate_page(
                f"Welcome, {username}",
                "We couldn't find an active subscription on your Whop account. "
                "If you just subscribed, wait a few seconds and try again.",
                "Subscribe on Whop", auth.WHOP_PRODUCT_URL, "Try again", "/whop/login"),
                "text/html; charset=utf-8")

        sid = auth.new_session(user_id, username)
        secure = "; Secure" if os.environ.get("PORT") else ""
        # Land members on the tracker itself; "/" is now the free homepage.
        self._redirect("/tracker", extra_headers=[
            ("Set-Cookie", f"{auth.COOKIE_SESSION}={sid}; Path=/; HttpOnly; Max-Age=86400; SameSite=Lax{secure}"),
            ("Set-Cookie", f"{auth.COOKIE_STATE}=; Path=/; Max-Age=0"),
            ("Set-Cookie", f"{auth.COOKIE_VERIFIER}=; Path=/; Max-Age=0"),
        ])

    def _logout(self) -> None:
        auth.drop_session(self._cookie(auth.COOKIE_SESSION))
        self._redirect("/", extra_headers=[("Set-Cookie", f"{auth.COOKIE_SESSION}=; Path=/; Max-Age=0")])

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path, _, query = self.path.partition("?")

        if path == "/whop/login":
            return self._login()
        if path == "/whop/callback":
            return self._callback(query)
        if path == "/whop/logout":
            return self._logout()

        session = self._bind_context()
        is_public = (path in PUBLIC_PAGES or path == "/carddetail.js"
                     or path.startswith("/assets/"))
        if auth.WHOP_ENABLED and not session and not is_public:
            return self._gate()

        if path == "/":
            self._send(200, _page("home.html"), "text/html; charset=utf-8")
            return
        elif path in ("/tracker", "/tracker/"):
            self._send(200, _page("dashboard.html"), "text/html; charset=utf-8")
            return
        elif path == "/carddetail.js":
            self._send(200, _static("carddetail.js"), "application/javascript; charset=utf-8")
            return
        elif path == "/database":
            self._send(200, _page("database.html"), "text/html; charset=utf-8")
            return
        elif path == "/meta":
            self._send(200, _page("meta.html"), "text/html; charset=utf-8")
            return
        elif path == "/news":
            self._send(200, _page("news.html"), "text/html; charset=utf-8")
            return
        elif path == "/market":
            self._send(200, _page("market.html"), "text/html; charset=utf-8")
            return
        elif path.startswith("/assets/"):
            filename = path.replace("/assets/", "")
            try:
                body = _static(f"assets/{filename}")
                self._send(200, body, "image/jpeg")
            except OSError:
                self._send(404, b"404 Not Found", "text/plain")
            return
        elif path.startswith("/api/data"):
            payload = cached_payload(force="refresh=1" in query)
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
            return

        self._send(404, b"404 Not Found", "text/plain")

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _origin_is_local(self) -> bool:
        """Reject cross-site writes.

        Any page you visit can POST to 127.0.0.1 in the background. Browsers
        attach an Origin header to such requests, so requiring it to be this
        server (or absent, as for curl) blocks that without needing tokens.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # not a browser form/fetch; no ambient authority to abuse
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path, _, _ = self.path.partition("?")
        handler = ROUTES.get(path)
        if handler is None:
            return self._json(404, {"ok": False, "error": "Unknown endpoint."})

        if not self._origin_is_local():
            return self._json(403, {"ok": False, "error": "Cross-site request refused."})

        # A write only makes sense for a logged-in user (in multi-user mode).
        session = self._bind_context()
        if auth.WHOP_ENABLED and not session and path not in PUBLIC_API:
            return self._json(401, {"ok": False, "error": "Please log in.", "login": True})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(400, {"ok": False, "error": "Bad Content-Length."})
        if length > MAX_BODY_BYTES:
            return self._json(413, {"ok": False, "error": "Request too large."})

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object.")
        except ValueError:
            return self._json(400, {"ok": False, "error": "Malformed request."})

        try:
            result = handler(payload)
        except CollectionChanged as exc:
            return self._json(409, {"ok": False, "error": str(exc), "stale": True})
        except ValueError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # never take the server down over one bad request
            return self._json(500, {"ok": False, "error": f"Unexpected error: {exc}"})

        self._json(200, {"ok": True, **result})

    def log_message(self, *args) -> None:
        """Silence per-request logging; the console is for the report."""


def serve(open_browser: bool = True) -> int:
    """Run the dashboard until Ctrl+C."""
    # Railway sets $PORT; locally fall back to config. Bind 0.0.0.0 only when
    # hosted, so a local run is not exposed to the network.
    port = int(os.environ.get("PORT") or config.DASHBOARD_PORT)
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"TCG portfolio dashboard -> {url}")
    print("Ctrl+C to stop.")

    # When hosted, run the daily price snapshot in-process (see scheduler.py).
    # Skipped locally so a dev run doesn't fire network jobs on startup.
    if os.environ.get("PORT") and os.environ.get("DAILY_JOBS", "1") != "0":
        try:
            import scheduler
            scheduler.start()
        except Exception as exc:
            print(f"[scheduler] not started: {exc}")

    if open_browser and not os.environ.get("PORT"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0
