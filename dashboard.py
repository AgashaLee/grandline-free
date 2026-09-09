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
PUBLIC_API = {"/api/database", "/api/meta", "/api/news", "/api/market", "/api/price_history"}

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


# Cloudflare Web Analytics beacon — injected into every page so pageviews are
# tracked even though the apex is DNS-only (grey cloud), where Cloudflare's
# automatic injection can't reach. Cookieless/privacy-friendly; loads from
# Cloudflare's CDN.
_CF_ANALYTICS = (
    "<!-- Cloudflare Web Analytics -->"
    "<script type=\"module\" src=\"https://static.cloudflareinsights.com/beacon.min.js\" "
    "data-cf-beacon='{\"token\": \"0d429c31874d480896ab0579c4160e42\"}'></script>"
    "<!-- End Cloudflare Web Analytics -->"
)


def _page(name: str) -> bytes:
    """Read an HTML page, filling in the site-wide funnel links.

    Keeps the Whop/tracker URLs in one place (and env-overridable) instead of
    hard-coded into three templates. Also injects the Cloudflare Web Analytics
    beacon before </body> so every page is tracked from one spot.
    """
    html = (config.BASE_DIR / name).read_text(encoding="utf-8")
    html = (html.replace("{{TRACKER_URL}}", TRACKER_URL)
                .replace("{{WHOP_URL}}", WHOP_STORE_URL))
    html = html.replace("</body>", _CF_ANALYTICS + "</body>")
    return html.encode("utf-8")


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

#: Redundant collector-number / card-code OPTCGAPI puts in card names, in either
#: format it uses -- parenthesised "(OP02-093)"/"(112)" or dash-prefixed
#: " - OP14-041"/" - P-075" -- anywhere in the string (a variant word can follow
#: it). The card code is shown separately, so strip it. Word parentheticals like
#: "(Alternate Art)"/"(Manga)"/"(SP)" are kept.
_NAME_SUFFIX_RE = re.compile(
    r"\s*(?:-\s*[A-Za-z]{1,4}\d{0,2}-\d{1,4}"
    r"|\(\s*(?:[A-Za-z]{1,4}\d{0,2}-\d{1,4}|\d{1,4})\s*\))")
#: The errata note OPTCGAPI tacks on ("This card has been officially errata'd.").
#: Removed entirely from the effect text.
_ERRATA_SENTENCE_RE = re.compile(
    r"\s*This card has (?:been officially errata['’]?d|received an official errata)\.?",
    re.IGNORECASE)

#: A trailing "Disclaimer: ..." (reprint/border/copyright notes the data source
#: appends) is not part of the card's effect and isn't printed on the card, so
#: strip everything from that word to the end.
_DISCLAIMER_RE = re.compile(r"\s*Disclaimer\s*:.*$", re.IGNORECASE | re.DOTALL)


#: Bare rarity tag some names carry, e.g. "Enel (SPR)" -- not a descriptive
#: variant word, and the rarity is shown separately, so strip it from the name.
_NAME_RARITY_RE = re.compile(r"\s*\(\s*SPR\s*\)", re.IGNORECASE)

#: Printing/variant descriptor a card name carries, e.g. "Belo Betty (Alternate
#: Art)" or "Crocodile (Parallel)". The printing is shown by its own tag/badge,
#: so strip the parenthetical from the display name to leave the base name.
_NAME_VARIANT_RE = re.compile(
    r"\s*\(\s*(?:Alternate Art|Super Alternate Art|Super Leader Alternate Art"
    r"|Full Art|Manga|Parallel|Sp|Box Topper|Wanted Poster|Jolly Roger Foil"
    r"|Textured Foil)\s*\)", re.IGNORECASE)


def _clean_card_name(name: str | None) -> str:
    if not name:
        return name or ""
    name = _NAME_VARIANT_RE.sub(
        "", _NAME_RARITY_RE.sub("", _NAME_SUFFIX_RE.sub("", name)))
    return re.sub(r"\s{2,}", " ", name).strip()


def _clean_effect_text(text: str | None) -> str | None:
    if not text:
        return text
    t = _ERRATA_SENTENCE_RE.sub("", text)
    t = _DISCLAIMER_RE.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip()


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
        seen_alt: dict[str, set] = {}
        for cid, url in db.execute("SELECT card_id, image_url FROM card_alt_arts"):
            # De-dupe case-insensitively: the same art saved under two casings
            # (e.g. ...EB24.png / ...eb24.png) 404s on the case-sensitive server.
            key = (url or "").lower()
            if not url or key in seen_alt.setdefault(cid, set()):
                continue
            seen_alt[cid].add(key)
            alt.setdefault(cid, []).append(url)
        for c in cards:
            arts = alt.get(c["card_id"])
            if arts:
                c["alt_arts"] = arts
    except Exception:
        pass

    # Attach per-printing prices (base + alt-art/parallel) so the detail modal can
    # show a price for each version. Table may not exist yet -> treat as none.
    try:
        vmap: dict[str, list[dict]] = {}
        base_img: dict[str, str] = {}   # correct plain-base image per card
        for r in db.execute(
                """SELECT card_id, variant_label, rarity, market_price, is_base, image_url
                     FROM card_variants WHERE market_price IS NOT NULL"""):
            vmap.setdefault(r["card_id"], []).append({
                "label": r["variant_label"] or "Base", "rarity": r["rarity"],
                "price": r["market_price"], "is_base": r["is_base"],
                "image": r["image_url"],
            })
            if r["is_base"] and r["image_url"]:
                base_img[r["card_id"]] = r["image_url"]
        for c in cards:
            # The catalog's image_url can be a parallel by mistake; prefer the
            # plain-base image from card_variants so the grid + popup base art
            # matches what Market Watch shows.
            if base_img.get(c["card_id"]):
                c["image_url"] = base_img[c["card_id"]]
            vs = vmap.get(c["card_id"])
            if vs:
                # Base first, then priciest variants.
                vs.sort(key=lambda v: (0 if v["is_base"] else 1, -(v["price"] or 0)))
                # Number repeated labels so identical-looking rows are
                # distinguishable (e.g. two "Alternate Art" -> "Alternate Art" +
                # "Alternate Art 2").
                counts: dict = {}
                for v in vs:
                    counts[v["label"]] = counts.get(v["label"], 0) + 1
                seen: dict = {}
                for v in vs:
                    if counts[v["label"]] > 1:
                        seen[v["label"]] = seen.get(v["label"], 0) + 1
                        if seen[v["label"]] > 1:
                            v["label"] = f'{v["label"]} {seen[v["label"]]}'
                c["variants"] = vs
    except Exception:
        pass

    # Tidy the display name (drop the redundant "(OP02-093)"/"(112)" suffix) and
    # remove OPTCGAPI's errata sentence from the effect text.
    for c in cards:
        c["name"] = _clean_card_name(c.get("name"))
        if "card_text" in c:
            c["card_text"] = _clean_effect_text(c.get("card_text"))

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

#: Sanity ceiling on a single window's move, expressed as a PRICE RATIO
#: (max(old,new) / min(old,new)) because prices move multiplicatively. OPTCGAPI
#: occasionally emits a garbage price for a card on one snapshot (e.g. a common
#: that reads ~$1075 one day, $0.43 the next), which then surfaces as a +700%
#: gainer or a -100% loser -- pure data noise that swamps the real signal. A
#: ratio guard catches BOTH directions symmetrically (a plain percentage ceiling
#: misses the loss side, since a bogus drop is bounded at -100% however corrupt
#: the baseline was). 5x  = +400% up or -80% down; the observed glitches are 7-8x
#: (gains) and 100-2500x (losses), while real card moves stay under ~3x, so this
#: cleanly separates them. The corrupt snapshot still ages out of the window on
#: its own; this just stops it polluting movers meanwhile and guards future ones.
_MOVER_MAX_RATIO = 5.0


def _market_jp(db, window: int) -> dict:
    """Japan Market Watch: yen movers from the Yuyu-tei snapshots
    (``price_history_jp``, one base price per card). Mirrors the West logic but
    joins the catalog directly (no per-printing variants in v1) and returns JPY."""
    try:
        dates = [r[0] for r in db.execute(
            "SELECT DISTINCT date FROM price_history_jp ORDER BY date").fetchall()]
    except Exception:
        return {"ready": False, "reason": "no-history", "market": "jp", "currency": "JPY"}
    if len(dates) < 2:
        return {"ready": False, "reason": "collecting", "market": "jp", "currency": "JPY",
                "days": len(dates), "latest": dates[-1] if dates else None}

    latest = dates[-1]
    cutoff = (_dt.date.fromisoformat(latest) - _dt.timedelta(days=window)).isoformat()
    baseline = next((d for d in reversed(dates) if d <= cutoff), dates[0])
    if baseline == latest:
        baseline = dates[0]

    rows = db.execute(
        """SELECT n.card_id AS card_id, c.name AS name, c.set_name AS set_name,
                  c.rarity AS rarity, c.image_url AS image_url,
                  o.price AS old_price, n.price AS new_price
             FROM price_history_jp n
             JOIN price_history_jp o ON o.card_id = n.card_id AND o.date = ?
             JOIN cards c            ON c.card_id = n.card_id
            WHERE n.date = ? AND o.price >= 50 AND o.price > 0""",
        (baseline, latest),
    ).fetchall()

    movers = []
    for r in rows:
        old, new = r["old_price"], r["new_price"]
        if not old:
            continue
        pct = round((new - old) / old * 100, 1)
        if pct == 0:
            continue
        if new <= 0 or max(old, new) / min(old, new) > _MOVER_MAX_RATIO:
            continue
        movers.append({
            "card_id": r["card_id"], "name": _clean_card_name(r["name"]),
            "set_name": r["set_name"],
            "rarity": r["rarity"], "image_url": r["image_url"],
            "pct": pct, "price": round(new, 2), "diff": round(new - old, 2),
        })
    movers.sort(key=lambda m: m["pct"], reverse=True)
    gainers = [m for m in movers if m["pct"] > 0][:50]
    losers = sorted([m for m in movers if m["pct"] < 0], key=lambda m: m["pct"])[:50]
    return {
        "ready": True, "latest": latest, "baseline": baseline, "window": window,
        "market": "jp", "currency": "JPY", "compared": len(movers),
        "gainers": gainers, "losers": losers,
    }


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

    # Japan market (Yuyu-tei, ¥) is a separate snapshot table -- handled apart
    # from the West (OPTCGAPI, $) logic below.
    if str((payload or {}).get("market", "")).lower() == "jp":
        return _market_jp(db, window)

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

    # Prefer per-printing prices (base + alt-arts) so pricey alt-arts show up as
    # their own movers; fall back to the base-only catalog if variants aren't seeded.
    try:
        has_variants = db.execute("SELECT 1 FROM card_variants LIMIT 1").fetchone() is not None
    except Exception:
        has_variants = False

    if has_variants:
        rows = db.execute(
            """SELECT v.card_id AS card_id, v.name AS name, c.set_name AS set_name,
                      v.rarity AS rarity, v.image_url AS image_url,
                      v.variant_label AS variant_label, v.is_base AS is_base,
                      o.price AS old_price, n.price AS new_price
                 FROM price_history n
                 JOIN price_history o  ON o.card_id = n.card_id AND o.date = ?
                 JOIN card_variants v  ON v.variant_id = n.card_id
                 LEFT JOIN cards c     ON c.card_id = v.card_id
                WHERE n.date = ? AND o.price >= ? AND o.price > 0""",
            (baseline, latest, min_price),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT n.card_id AS card_id, c.name AS name, c.set_name AS set_name,
                      c.rarity AS rarity, c.image_url AS image_url,
                      '' AS variant_label, 1 AS is_base,
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
        # Drop physically-implausible single-window moves (both directions):
        # these are OPTCGAPI snapshot glitches, not real price action. Compared
        # by price ratio so a corrupt high baseline (-100%-ish loser) is caught
        # too, not just a corrupt low one (huge gainer). See _MOVER_MAX_RATIO.
        if new <= 0 or max(old, new) / min(old, new) > _MOVER_MAX_RATIO:
            continue
        movers.append({
            "card_id": r["card_id"], "name": _clean_card_name(r["name"]),
            "set_name": r["set_name"],
            "rarity": r["rarity"], "image_url": r["image_url"],
            "variant_label": r["variant_label"] or "", "is_base": r["is_base"],
            "pct": pct, "price": round(new, 2), "diff": round(new - old, 2),
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
        "market": "west",
        "currency": "USD",
        "compared": len(movers),
        "gainers": gainers,
        "losers": losers,
    }


def api_price_history(payload: dict) -> dict:
    """Daily price points for a card's printings, for the popup's history chart.

    Returns one series per printing (base + priced variants) that has any logged
    history, each a list of {date, price} from the ``price_history`` snapshots.
    """
    db = get_db()
    card_id = (payload.get("card_id") or "").strip().upper() if isinstance(payload, dict) else ""
    if not card_id:
        return {"series": []}
    try:
        vids = db.execute(
            """SELECT variant_id, variant_label, is_base FROM card_variants
                WHERE card_id=? ORDER BY is_base DESC, market_price DESC""",
            (card_id,)).fetchall()
    except Exception:
        vids = []
    if not vids:  # no variant table -> just the base, keyed by card_id
        vids = [{"variant_id": card_id, "variant_label": "", "is_base": 1}]

    series = []
    for v in vids:
        try:
            pts = db.execute(
                "SELECT date, price FROM price_history WHERE card_id=? ORDER BY date",
                (v["variant_id"],)).fetchall()
        except Exception:
            pts = []
        if not pts:
            continue
        series.append({
            "label": v["variant_label"] or "Base", "is_base": v["is_base"],
            "points": [{"date": p["date"], "price": p["price"]} for p in pts],
        })
    return {"series": series}


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


# A "From Grand Line" post generated from our OWN auto-updating data (Market
# Watch movers) so News stays fresh even when external news is quiet. Cached so
# we don't recompute the market join on every page load.
_DD_CACHE: dict = {"at": 0.0, "post": None}
_DD_TTL = 1800


def _movers_post() -> dict | None:
    now = time.time()
    if now - _DD_CACHE["at"] < _DD_TTL:
        return _DD_CACHE["post"]
    post = None
    try:
        m = api_market({"window": 7})
        gainers = m.get("gainers") if isinstance(m, dict) else None
        if m.get("ready") and gainers:
            top = gainers[:4]
            parts = [f'{(g.get("name") or g.get("card_id"))} +{int(round(g["pct"]))}%'
                     for g in top]
            post = {
                "title": "📈 This week's biggest price movers",
                "body": "Biggest 7-day gainers: " + ", ".join(parts)
                        + ". See the full list on Market Watch.",
                "date": m.get("latest") or "",
                "link": "/market",
            }
    except Exception:
        post = None
    _DD_CACHE["post"], _DD_CACHE["at"] = post, now
    return post


# Active English One Piece TCG YouTube channels (the official channels are stale
# / multi-game, so we use community channels). Pulled via each channel's Atom feed.
_YT_CHANNELS = [
    ("Joy Boys", "UC4H1zHvU2Z2YLo4MC42Flqg"),
    ("StrawHatBrother", "UCdjjbk1udeQAV6EASIrCN0Q"),
]
_YT_CACHE: dict = {"at": 0.0, "items": []}
_YT_TTL = 1800
_YT_MAX_PER = 6


def _youtube_items() -> list:
    """Recent videos from our OP TCG YouTube channels, shaped as news items."""
    now = time.time()
    if _YT_CACHE["items"] and now - _YT_CACHE["at"] < _YT_TTL:
        return _YT_CACHE["items"]
    import urllib.request
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
    out = []
    for name, cid in _YT_CHANNELS:
        try:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            req = urllib.request.Request(url, headers={"User-Agent": _NEWS_UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                root = ET.fromstring(resp.read())
        except Exception:
            continue
        for e in root.findall("a:entry", ns)[:_YT_MAX_PER]:
            title = (e.findtext("a:title", namespaces=ns) or "").strip()
            link_el = e.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            pub = (e.findtext("a:published", namespaces=ns) or "").strip()
            try:
                ts = _dt.datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp() if pub else 0.0
            except Exception:
                ts = 0.0
            thumb_el = e.find("media:group/media:thumbnail", ns)
            out.append({
                "title": title,
                "link": link,
                "source": f"YouTube · {name}",
                "date": pub,
                "image": thumb_el.get("url") if thumb_el is not None else "",
                "_ts": ts,
            })
    if out:
        _YT_CACHE["items"], _YT_CACHE["at"] = out, now
    return out if out else _YT_CACHE["items"]


def _google_news_items() -> list:
    """Recent OP TCG headlines from Google News RSS (cached), each with a _ts."""
    now = time.time()
    if _NEWS_CACHE["items"] and now - _NEWS_CACHE["at"] < _NEWS_TTL:
        return _NEWS_CACHE["items"]
    import email.utils
    import urllib.request
    import xml.etree.ElementTree as ET
    try:
        req = urllib.request.Request(_NEWS_URL, headers={"User-Agent": _NEWS_UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            root = ET.fromstring(resp.read())
    except Exception:
        return _NEWS_CACHE["items"]  # serve last-good on failure
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
    items.sort(key=lambda x: x["_ts"], reverse=True)
    items = items[:_NEWS_MAX]
    if items:
        _NEWS_CACHE["items"], _NEWS_CACHE["at"] = items, now
    return items


def api_news(payload: dict | None = None) -> dict:
    """Return the site's own featured posts (incl. a data-driven movers recap)
    plus recent OP TCG news — Google News headlines + our YouTube channels —
    merged newest-first."""
    featured = _own_posts()
    mv = _movers_post()
    if mv:
        featured = featured + [mv]

    merged = list(_google_news_items()) + list(_youtube_items())
    merged.sort(key=lambda x: x.get("_ts", 0.0), reverse=True)
    merged = merged[:_NEWS_MAX]
    items = [{k: v for k, v in x.items() if k != "_ts"} for x in merged]

    if not items and not featured:
        return {"featured": featured, "items": [],
                "error": "News is unavailable right now — try again soon."}
    return {"featured": featured, "items": items}


# ===========================================================================
# Server-rendered, crawlable SEO pages: /card/<code> and /leader/<code>.
# These turn data already in the DB into real URLs Google can index (unlike the
# JS popups on /database and /meta), and cross-link to build an internal graph.
# ===========================================================================
_SITE_URL = os.environ.get("SITE_URL", "https://grandline.id").rstrip("/")


def _h(s) -> str:
    return (str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt_effect_html(text: str | None) -> str:
    """Plain, readable effect text for SEO pages: strip errata, restore the
    minus signs the source drops, escape, and turn newlines into <br>."""
    if not text or str(text).strip().upper() == "NULL":
        return ""
    t = _clean_effect_text(text) or ""
    if not t.strip():
        return ""
    t = re.sub(r"DON!!\s*(\d+)\s*:", r"DON!! -\1:", t)
    # Restore the minus the source drops on opponent power DEBUFFS (e.g. "give ...
    # -2000 power"), but NOT on THRESHOLDS like "3000 power or less" (which target
    # characters whose power is at/under that value and are correctly positive).
    t = re.sub(r"(opponent['’]?s?\s+[Cc]haracters?\b[^.\n]*?)(\d+)(\s*power)"
               r"(?!\s+or\s+(?:less|more|higher|lower|greater))", r"\1-\2\3", t)
    return _h(t).replace("\n", "<br>")


def _buy_query(code: str, name: str | None) -> str:
    name = re.sub(r"\s*\(\d+\)\s*$", "", str(name or "")).strip()
    return f"one piece card {code} {name}".strip()


def _buy_buttons_html(code: str, name: str | None) -> str:
    import urllib.parse
    q = urllib.parse.quote(_buy_query(code, name))
    shops = [
        ("Shopee", "#ee4d2d", f"https://shopee.co.id/search?keyword={q}"),
        ("Tokopedia", "#03ac0e", f"https://www.tokopedia.com/search?q={q}"),
        ("TCGplayer", "#f8991d", f"https://www.tcgplayer.com/search/all/product?q={q}"),
        ("eBay", "#0064d2", f"https://www.ebay.com/sch/i.html?_nkw={q}"),
    ]
    btns = "".join(
        f'<a class="buybtn" style="background:{c}" href="{_h(u)}" target="_blank" '
        f'rel="nofollow sponsored noopener">🛒 Buy on {_h(n)}</a>' for n, c, u in shops)
    return f'<div class="buyrow">{btns}</div>'


_SEO_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#09090b;--surface:#18181b;--line:#27272a;--ink:#f4f4f5;--muted:#a1a1aa;--gold:#f59e0b;--sea:#0ea5e9;--up:#10b981;--down:#ef4444}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.5;padding-bottom:48px}
h1,h2,h3{font-family:'Fredoka',sans-serif;letter-spacing:-.01em}
a{color:inherit;text-decoration:none}
.nav{background:rgba(9,9,11,.85);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;position:sticky;top:0;z-index:50}
.nav .brand{font-family:'Fredoka';font-weight:700;color:#fff;font-size:18px;margin-right:8px}
.nav a{padding:7px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px;font-weight:600;color:var(--ink)}
.nav a.cta{background:var(--gold);color:#4a2f10;border-color:var(--gold)}
.wrap{max-width:1000px;margin:28px auto;padding:0 20px}
.crumb{color:var(--muted);font-size:12px;margin-bottom:18px}
.crumb a:hover{color:var(--ink)}
.top{display:grid;grid-template-columns:300px 1fr;gap:28px}
@media(max-width:720px){.top{grid-template-columns:1fr}}
.cardimg{width:100%;max-width:300px;border-radius:14px;border:1px solid var(--line);background:var(--surface);aspect-ratio:5/7;object-fit:contain}
h1{font-size:28px;color:#fff;margin-bottom:4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:16px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.chip{background:var(--surface);border:1px solid var(--line);border-radius:7px;padding:6px 11px;font-size:12px}
.chip b{color:var(--muted);font-weight:600;margin-right:4px}
.traits{color:var(--muted);font-size:13px;margin-bottom:16px}
.traits b{color:var(--ink)}
.price{font-size:22px;font-weight:800;color:#fff;margin-bottom:2px}
.price small{font-size:12px;color:var(--muted);font-weight:500;margin-left:8px}
.jpline{color:var(--muted);font-size:13px;font-weight:600;margin-bottom:14px}
.jpline b{color:var(--ink);font-weight:700}
.vthumbs{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;max-width:300px}
.vthumb{width:52px;height:73px;object-fit:cover;border-radius:6px;border:2px solid transparent;cursor:pointer;background:var(--surface)}
.vthumb:hover{border-color:var(--muted)}
.vthumb.sel{border-color:var(--sea)}
.box{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:22px 0}
.box h2{font-size:16px;color:#fff;margin-bottom:12px}
.effect{line-height:1.7;font-size:13px}
.buyrow{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
.buybtn{flex:1;min-width:150px;text-align:center;color:#fff;font-weight:700;font-size:13px;padding:12px;border-radius:9px}
.buynote{color:var(--muted);font-size:11px;margin-top:8px;font-style:italic}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:14px}
.mini{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px;text-align:center}
.mini img{width:100%;aspect-ratio:5/7;object-fit:contain;border-radius:6px}
.mini .nm{font-size:11px;color:var(--muted);margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mini .u{font-size:11px;color:var(--gold);font-weight:700}
.statrow{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 18px;min-width:120px}
.stat .v{font-size:24px;font-weight:800;color:#fff;font-family:'Fredoka'}
.stat .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid var(--line)}
.pill{display:inline-block;background:rgba(245,158,11,.14);color:var(--gold);font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px}
.foot{border-top:1px solid var(--line);margin-top:48px;padding:30px 20px;text-align:center;color:var(--muted);font-size:12px;line-height:1.6}
.foot a{color:var(--gold)}
"""


def _seo_shell(title: str, description: str, canonical: str, body: str) -> bytes:
    nav = (
        '<header class="nav"><a class="brand" href="/">🏴‍☠️ Grand Line</a>'
        '<a href="/database">Card Database</a><a href="/market">Market Watch</a>'
        '<a href="/meta">Meta Decks</a><a href="/news">News</a>'
        f'<a class="cta" href="{_h(WHOP_STORE_URL)}" target="_blank" rel="noopener">★ Get the Tracker</a></header>')
    foot = (
        '<footer class="foot">Questions or partnerships? '
        '<a href="mailto:contact@grandline.id">contact@grandline.id</a><br>'
        'Grand Line is a fan-made project, not endorsed by or affiliated with Bandai Namco or Toei '
        'Animation. Card images and names are the property of their respective owners.<br>'
        'Some links are affiliate links — buying through them supports the site at no extra cost.</footer>')
    doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<title>{_h(title)}</title><meta name="description" content="{_h(description)}">'
        f'<link rel="canonical" href="{_h(canonical)}">'
        '<link href="https://fonts.googleapis.com/css2?family=Fredoka:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
        f'<style>{_SEO_CSS}</style></head><body>{nav}<main class="wrap">{body}</main>{foot}{_CF_ANALYTICS}</body></html>')
    return doc.encode("utf-8")


def _printings(db, code: str, c: dict) -> list[dict]:
    """All priced printings for a card (base first, then dearest), each with its
    own label, image and price — so the page can show a price per version
    instead of one number that may belong to a different printing than the art.
    """
    rows: list[dict] = []
    try:
        vs = db.execute(
            "SELECT name, rarity, variant_label, image_url, market_price, is_base "
            "FROM card_variants WHERE card_id=? ORDER BY is_base DESC, market_price DESC",
            (code,)).fetchall()
    except Exception:
        vs = []
    for v in vs:
        lbl = (v["variant_label"] or "").strip()
        if not lbl:
            lbl = "SPR" if "(SPR)" in (v["name"] or "").upper() else "Base"
        rows.append({"label": lbl, "rarity": v["rarity"] or "",
                     "image": v["image_url"] or "", "price": v["market_price"],
                     "is_base": v["is_base"]})
    if not rows:
        rows.append({"label": "Base", "rarity": c.get("rarity") or "",
                     "image": c.get("image_url") or "", "price": c.get("market_price"),
                     "is_base": 1})
    return rows


def render_card_page(code: str) -> bytes | None:
    db = get_db()
    c = db.execute(
        "SELECT card_id,name,set_id,set_name,rarity,card_type,card_color,card_cost,"
        "card_power,card_text,attribute,counter,sub_types,life,image_url,market_price "
        "FROM cards WHERE card_id=?", (code,)).fetchone()
    if not c:
        return None
    c = dict(c)
    name = _clean_card_name(c["name"])
    # Base price (prefer the plain-base variant, else the catalog value).
    price = None
    try:
        r = db.execute("SELECT market_price FROM card_variants WHERE card_id=? AND is_base=1 "
                       "AND market_price IS NOT NULL LIMIT 1", (code,)).fetchone()
        price = r[0] if r else c.get("market_price")
    except Exception:
        price = c.get("market_price")
    # Latest Japan price, if any.
    jp = None
    try:
        r = db.execute("SELECT price FROM price_history_jp WHERE card_id=? ORDER BY date DESC LIMIT 1",
                       (code,)).fetchone()
        jp = r[0] if r else None
    except Exception:
        jp = None
    # How many meta decks use this card, and the top leaders that run it.
    deck_n, leaders = 0, []
    try:
        deck_n = db.execute("SELECT COUNT(DISTINCT deck_id) FROM meta_deck_cards WHERE card_id=?",
                            (code,)).fetchone()[0]
        leaders = db.execute(
            "SELECT d.leader_id AS lid, ca.name AS lname, COUNT(DISTINCT d.id) AS n "
            "FROM meta_deck_cards mdc JOIN meta_decks d ON d.id=mdc.deck_id "
            "LEFT JOIN cards ca ON ca.card_id=d.leader_id "
            "WHERE mdc.card_id=? AND d.leader_id<>'' GROUP BY d.leader_id "
            "ORDER BY n DESC LIMIT 6", (code,)).fetchall()
    except Exception:
        pass

    chips = []
    for lbl, key in [("Rarity", "rarity"), ("Type", "card_type"), ("Color", "card_color"),
                     ("Cost", "card_cost"), ("Power", "card_power"), ("Attribute", "attribute"),
                     ("Life", "life")]:
        v = c.get(key)
        if v not in (None, ""):
            chips.append(f'<span class="chip"><b>{lbl}</b>{_h(v)}</span>')
    if c.get("counter") not in (None, "") and str(c["counter"]).strip("+").isdigit() and int(str(c["counter"]).strip("+")) > 0:
        chips.append(f'<span class="chip"><b>Counter</b>+{_h(int(str(c["counter"]).strip("+")))}</span>')
    _rawtr = (c.get("sub_types") or "").strip()
    traits = [t.strip() for t in _rawtr.split("/") if t.strip()] if "/" in _rawtr else ([_rawtr] if _rawtr else [])
    traits_html = (f'<div class="traits"><b>Traits:</b> {" / ".join(_h(t) for t in traits)}</div>'
                   if traits else "")
    effect = _fmt_effect_html(c.get("card_text"))
    effect_html = f'<div class="box"><h2>Effect</h2><div class="effect">{effect}</div></div>' if effect else ""

    # Per-printing prices: pick a featured version for the headline, but keep
    # each version's own price so switching art switches the price too.
    prints = _printings(db, code, c)
    featured = next((p for p in prints if p["is_base"]), prints[0])
    if featured["price"] is not None:
        price = featured["price"]  # keep title/description in sync with the headline
    price_html = ""
    if featured["price"] is not None or jp:
        us = ""
        if featured["price"] is not None:
            us = (f'<div class="price"><span id="usPrice">${float(featured["price"]):.2f}</span>'
                  f'<small id="usLabel">US market · {_h(featured["label"])}</small></div>')
        # Japan is a separate market (one Yuyu-tei price per card), NOT a
        # conversion of the US price — so label it plainly, no "≈".
        jp_line = f'<div class="jpline">Japan market · regular <b>¥{int(jp):,}</b></div>' if jp else ""
        price_html = f'{us}{jp_line}'

    decks_html = ""
    if deck_n:
        chips2 = "".join(
            f'<a class="chip" href="/leader/{_h(l["lid"])}"><b>{l["n"]}×</b>{_h(_clean_card_name(l["lname"]) or l["lid"])}</a>'
            for l in leaders)
        decks_html = (f'<div class="box"><h2>Used in {deck_n} meta deck{"s" if deck_n!=1 else ""}</h2>'
                      f'<div class="chips">{chips2}</div></div>')

    # Main image = featured printing's art; thumbnails let visitors switch
    # between printings (art + price update together via a tiny inline script).
    main_src = featured["image"] or c.get("image_url") or ""
    img = (f'<img id="cardMainImg" class="cardimg" src="{_h(main_src)}" '
           f'alt="{_h(name)} {_h(code)} One Piece card" loading="lazy">' if main_src else "")
    with_img = [p for p in prints if p["image"]]
    if len(with_img) > 1:
        thumbs = "".join(
            f'<img class="vthumb{" sel" if p is featured else ""}" src="{_h(p["image"])}" '
            f'data-img="{_h(p["image"])}" '
            + (f'data-price="{float(p["price"]):.2f}" ' if p["price"] is not None else "")
            + f'data-label="{_h(p["label"])}" loading="lazy" onclick="_pick(this)" '
            f'alt="{_h(p["label"])} printing" onerror="this.style.display=\'none\'">'
            for p in with_img)
        img += f'<div class="vthumbs">{thumbs}</div>'
    canonical = f"{_SITE_URL}/card/{code}"
    setline = f' · {_h(c["set_name"])}' if c.get("set_name") else ""
    body = (
        f'<div class="crumb"><a href="/">Home</a> / <a href="/database">Card Database</a> / {_h(code)}</div>'
        f'<div class="top"><div>{img}</div><div>'
        f'<h1>{_h(name)}</h1><div class="sub">{_h(code)}{setline}</div>'
        f'{price_html}<div class="chips">{"".join(chips)}</div>{traits_html}'
        f'{_buy_buttons_html(code, name)}'
        '<div class="buynote">Opens a marketplace search for this card. Prices vary by seller.</div>'
        f'</div></div>{effect_html}{decks_html}'
        '<p style="color:var(--muted);font-size:12px;margin-top:20px">'
        f'<a href="/database#{_h(code.split("-")[0])}" style="color:var(--gold)">'
        '← Back to the full card database</a></p>'
        '<script>function _pick(el){'
        "document.querySelectorAll('.vthumb').forEach(function(t){t.classList.remove('sel')});"
        "el.classList.add('sel');"
        "var m=document.getElementById('cardMainImg');if(m)m.src=el.getAttribute('data-img')||el.src;"
        "var p=document.getElementById('usPrice'),l=document.getElementById('usLabel');"
        "var pr=el.getAttribute('data-price'),lb=el.getAttribute('data-label');"
        "if(p)p.textContent=pr?('$'+parseFloat(pr).toFixed(2)):'—';"
        "if(l&&lb)l.textContent=pr?('US market · '+lb):(lb+' · no US price');"
        '}</script>')
    title = f"{name} ({code}) — One Piece Card Price & Decks | Grand Line"
    desc = (f"{name} ({code}) One Piece Card Game price, stats and the meta decks that use it. "
            + (f"Market price ${float(price):.2f}. " if price else "")
            + "Compare prices and buy on Shopee, Tokopedia, TCGplayer & eBay.")
    return _seo_shell(title, desc, canonical, body)


_ORD_RE = re.compile(r"(\d{1,2})(st|nd|rd|th)", re.IGNORECASE)


def _event_date_key(s) -> _dt.date:
    """Sort key for event dates stored as text like '3rd May 2026'.

    They are TEXT, so ORDER BY sorts them alphabetically (March lands between
    May and June). Parse to a real date; unparseable values sink to the bottom.
    """
    if not s:
        return _dt.date.min
    txt = _ORD_RE.sub(r"\1", str(s)).strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):  # West "3rd May 2026" + JP "2026-09-02"
        try:
            return _dt.datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return _dt.date.min


def render_leader_page(code: str) -> bytes | None:
    db = get_db()
    c = db.execute("SELECT card_id,name,set_name,card_color,image_url,card_text FROM cards WHERE card_id=?",
                   (code,)).fetchone()
    total = db.execute("SELECT COUNT(*) FROM meta_decks").fetchone()[0] or 1
    n = db.execute("SELECT COUNT(*) FROM meta_decks WHERE leader_id=?", (code,)).fetchone()[0]
    if not c and not n:
        return None
    name = _clean_card_name(c["name"]) if c else code
    color = (c["card_color"] if c else "") or ""
    img = c["image_url"] if c else db.execute(
        "SELECT leader_image FROM meta_decks WHERE leader_id=? AND leader_image<>'' LIMIT 1",
        (code,)).fetchone()
    img = (c["image_url"] if c and c["image_url"] else (img[0] if img else ""))
    wins = db.execute("SELECT COUNT(*) FROM meta_decks WHERE leader_id=? AND "
                      "(players LIKE '%1st%' OR players LIKE '%Winner%' OR players LIKE '%Champion%')",
                      (code,)).fetchone()[0]
    share = round(n / total * 100, 1)

    _all_recent = db.execute(
        "SELECT event_name,event_date,country,players,winner FROM meta_decks WHERE leader_id=?",
        (code,)).fetchall()
    recent = sorted(_all_recent, key=lambda r: _event_date_key(r["event_date"]), reverse=True)[:10]
    rows = "".join(
        f'<tr><td>{_h(r["event_name"] or "-")}</td><td>{_h(r["event_date"] or "-")}</td>'
        f'<td>{_h(r["country"] or "-")}</td><td><span class="pill">{_h(r["players"] or "-")}</span></td>'
        f'<td>{_h(r["winner"] or "-")}</td></tr>' for r in recent)
    recent_html = (f'<div class="box"><h2>Recent tournament decks</h2>'
                   f'<div style="overflow-x:auto"><table><thead><tr><th>Event</th><th>Date</th>'
                   f'<th>Region</th><th>Place</th><th>Player</th></tr></thead><tbody>{rows}</tbody></table></div></div>'
                   ) if recent else ""

    used = db.execute(
        "SELECT mdc.card_id AS cid, ca.name AS nm, ca.image_url AS img, "
        "COUNT(DISTINCT mdc.deck_id) AS decks FROM meta_deck_cards mdc "
        "JOIN meta_decks d ON d.id=mdc.deck_id LEFT JOIN cards ca ON ca.card_id=mdc.card_id "
        "WHERE d.leader_id=? AND mdc.card_id<>? GROUP BY mdc.card_id ORDER BY decks DESC LIMIT 12",
        (code, code)).fetchall()
    minis = "".join(
        f'<a class="mini" href="/card/{_h(u["cid"])}">'
        f'<img src="{_h(u["img"])}" alt="{_h(_clean_card_name(u["nm"]) or u["cid"])}" loading="lazy">'
        f'<div class="nm">{_h(_clean_card_name(u["nm"]) or u["cid"])}</div>'
        f'<div class="u">{u["decks"]}/{n} decks</div></a>' for u in used if u["img"])
    used_html = (f'<div class="box"><h2>Most-used cards in {_h(name)} decks</h2>'
                 f'<div class="grid">{minis}</div></div>') if minis else ""

    imgtag = (f'<img class="cardimg" src="{_h(img)}" alt="{_h(name)} leader One Piece deck" loading="lazy">'
              if img else "")
    canonical = f"{_SITE_URL}/leader/{code}"
    body = (
        f'<div class="crumb"><a href="/">Home</a> / <a href="/meta">Meta Decks</a> / {_h(name)}</div>'
        f'<div class="top"><div>{imgtag}</div><div>'
        f'<h1>{_h(name)} Deck</h1><div class="sub">{_h(code)}{(" · "+_h(color)) if color else ""} · One Piece TCG meta</div>'
        '<div class="statrow">'
        f'<div class="stat"><div class="v">{n}</div><div class="l">Tournament decks</div></div>'
        f'<div class="stat"><div class="v">{wins}</div><div class="l">1st-place finishes</div></div>'
        f'<div class="stat"><div class="v">{share}%</div><div class="l">Meta share</div></div>'
        '</div>'
        f'{_buy_buttons_html(code, name)}'
        '<div class="buynote">Buy the leader card. Opens a marketplace search.</div>'
        f'</div></div>{recent_html}{used_html}'
        '<p style="color:var(--muted);font-size:12px;margin-top:20px">'
        f'<a href="/meta#leader={_h(code)}" style="color:var(--gold)">← See all {_h(name)} decks</a></p>')
    title = f"{name} Deck — One Piece TCG Meta, Decklists & Cards | Grand Line"
    desc = (f"{name} ({code}) One Piece Card Game deck: {n} tournament decks, {wins} wins, "
            f"{share}% meta share. Winning decklists, most-used cards and prices.")
    return _seo_shell(title, desc, canonical, body)


def render_sitemap() -> bytes:
    """XML sitemap listing the main pages + every card and leader page, so
    Google can discover and index them all (they aren't in the nav)."""
    db = get_db()
    urls = [_SITE_URL + p for p in ("/", "/database", "/market", "/meta", "/news")]
    try:
        urls += [f"{_SITE_URL}/card/{r[0]}" for r in
                 db.execute("SELECT card_id FROM cards ORDER BY card_id")]
        urls += [f"{_SITE_URL}/leader/{r[0]}" for r in
                 db.execute("SELECT DISTINCT leader_id FROM meta_decks WHERE leader_id<>''")]
    except Exception:
        pass
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{_h(u)}</loc></url>" for u in urls)
            + "</urlset>")
    return body.encode("utf-8")


_ROBOTS = (f"User-agent: *\nAllow: /\n\nSitemap: {_SITE_URL}/sitemap.xml\n").encode("utf-8")


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
    "/api/price_history": api_price_history,
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
        if getattr(self, "_head_only", False):
            return  # HEAD request: headers only, no body
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

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        # Reuse do_GET's routing but emit headers only (crawlers / sitemap
        # checkers ping URLs with HEAD; without this the stdlib returns 501).
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

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
        elif path == "/sitemap.xml":
            self._send(200, render_sitemap(), "application/xml; charset=utf-8")
            return
        elif path == "/robots.txt":
            self._send(200, _ROBOTS, "text/plain; charset=utf-8")
            return
        elif path.startswith("/card/"):
            code = path[len("/card/"):].strip("/").upper()
            body = render_card_page(code) if _looks_like_card_code(code) else None
            self._send(200, body, "text/html; charset=utf-8") if body else \
                self._send(404, b"Card not found", "text/plain")
            return
        elif path.startswith("/leader/"):
            code = path[len("/leader/"):].strip("/").upper()
            body = render_leader_page(code) if _looks_like_card_code(code) else None
            self._send(200, body, "text/html; charset=utf-8") if body else \
                self._send(404, b"Leader not found", "text/plain")
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
