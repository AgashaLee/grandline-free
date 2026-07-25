"""Web dashboard -- same shape as tennis_predictor.py.

Pure standard library (no flask). Serves ``dashboard.html`` at ``/`` and the
portfolio as JSON at ``/api/data``.

    python main.py dashboard    ->  http://127.0.0.1:8802

This is a *presentation* module, exactly like report.py: it reads the same
PricedHolding objects and knows nothing about HTTP price sources, caching or
currency conversion. Adding it required no change to the business logic.
"""

from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import fx
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

#: Rebuilding hits the price cache, not the network, but there is no reason to
#: redo it for every browser poll.
PAYLOAD_TTL_SECONDS = 60

#: Refuse absurd request bodies outright rather than reading them into memory.
MAX_BODY_BYTES = 64 * 1024

_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"payload": None, "built_at": 0.0}
#: Serialises writes to collection.csv (ThreadingHTTPServer handles requests
#: concurrently, and read-modify-write on a CSV is not atomic).
_WRITE_LOCK = threading.Lock()


def _invalidate() -> None:
    with _LOCK:
        _CACHE["payload"], _CACHE["built_at"] = None, 0.0


def build_payload() -> dict:
    """Price the collection and return everything the page needs."""
    try:
        holdings = load_collection(config.COLLECTION_FILE)
    except FileNotFoundError:
        # No collection yet -- a fresh install or a fresh Railway deploy. Not an
        # error; invite the first card instead of showing a scary file path.
        holdings = []
    except ValueError as exc:
        return {"error": str(exc), "rows": [], "totals": None, "empty": True}

    if not holdings:
        return {"error": "No cards yet — click “+ Add card” to add your first one.",
                "rows": [], "totals": None, "empty": True}

    cache = JsonCache(config.CACHE_FILE, config.CACHE_TTL_HOURS)
    providers, rates = ProviderPool(cache), fx.RateBook(cache)

    try:
        priced = price_collection(holdings, providers, rates)
    except fx.FxError as exc:
        return {"error": str(exc), "rows": [], "totals": None}
    totals = compute_totals(priced)

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
        })

    return {
        "error": None,
        "rows": rows,
        "regions": sorted(config.PROVIDER_BY_REGION),
        "default_region": config.DEFAULT_REGION,
        "grades": list(config.GRADE_CHOICES),
        "display_currency": config.DISPLAY_CURRENCY,
        "currencies": sorted(config.CURRENCY_FORMAT),
        "currency_symbol": config.currency_format(config.DISPLAY_CURRENCY)[0],
        "currency_decimals": config.currency_format(config.DISPLAY_CURRENCY)[1],
        "totals": {
            "invested": round(totals.invested, 2),
            "value": round(totals.value, 2),
            "pl": round(totals.pl, 2),
            "pl_pct": None if totals.pl_pct is None else round(totals.pl_pct, 2),
            "cards": sum(r["qty"] for r in rows if r["ok"]),
            "errors": totals.error_count,
        },
        # Keep small rates meaningful: IDR->USD is ~0.0000589, which rounds to
        # zero at 4dp. Significant figures preserve it whatever the pair.
        "rates": {c: float(f"{r:.6g}") for c, r in rates.rates.items()},
        "fx_source": ", ".join(sorted(set(rates.sources.values()))) or "n/a",
        "provider": ", ".join(sorted({p.name for p in providers._by_region.values()})),
    }


def cached_payload(force: bool = False) -> dict:
    with _LOCK:
        payload, built = _CACHE["payload"], float(_CACHE["built_at"])
        if payload is not None and not force and (time.time() - built) < PAYLOAD_TTL_SECONDS:
            return dict(payload, built_at=built)

    fresh = build_payload()  # built outside the lock: pricing can be slow
    with _LOCK:
        _CACHE["payload"], _CACHE["built_at"] = fresh, time.time()
        return dict(fresh, built_at=float(_CACHE["built_at"]))


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

    with _WRITE_LOCK:
        append_holding(config.COLLECTION_FILE,
                       Holding(name, code, buy, quantity, variant, region, grade, config.DISPLAY_CURRENCY))
    _invalidate()
    return {"added": {"card_id": code, "variant": variant, "region": region, "grade": grade, "quantity": quantity}}


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

    with _WRITE_LOCK:
        updated = update_holding(
            config.COLLECTION_FILE,
            index,
            str(payload.get("card_id", "")),
            str(payload.get("variant") or BASE_VARIANT),
            buy_price=buy,
            quantity=_int(payload, "quantity"),
            grade=grade,
        )
    _invalidate()
    return {"updated": {"quantity": updated.quantity, "buy_price": updated.buy_price, "grade": updated.grade}}


def api_remove(payload: dict) -> dict:
    """Sell all or part of a row."""
    index = _int(payload, "index")
    if index is None:
        raise ValueError("Missing row.")

    with _WRITE_LOCK:
        kept = remove_holding(
            config.COLLECTION_FILE,
            index,
            str(payload.get("card_id", "")),
            str(payload.get("variant") or BASE_VARIANT),
            quantity=_int(payload, "quantity"),
        )
    _invalidate()
    return {"remaining": None if kept is None else kept.quantity}


def api_settings(payload: dict) -> dict:
    """Change the reporting currency. Takes effect on the next refresh."""
    code = config.set_display_currency(str(payload.get("display_currency", "")))
    _invalidate()  # every figure on the page is now in a different currency
    return {"display_currency": code}


ROUTES = {
    "/api/lookup": api_lookup,
    "/api/add": api_add,
    "/api/update": api_update,
    "/api/remove": api_remove,
    "/api/settings": api_settings,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path, _, query = self.path.partition("?")

        if path.startswith("/api/data"):
            payload = cached_payload(force="refresh=1" in query)
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
            return

        try:
            body = HTML_PATH.read_bytes()
        except FileNotFoundError:
            body = b"<h1>dashboard.html missing</h1>"
        self._send(200, body, "text/html; charset=utf-8")

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

    if open_browser and not os.environ.get("PORT"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0
