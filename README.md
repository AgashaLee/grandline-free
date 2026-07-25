# TCG Collection Price Tracker — Phase 1

Tracks the value of a One Piece TCG collection in IDR. Reads `collection.csv`,
fetches USD market prices, converts to IDR at a live rate, and reports per-card
and total profit/loss to the terminal and to `portfolio.csv`.

Phase 1 is deliberately small: single user, local files, free no-key APIs.
The architecture — not the feature set — is the deliverable.

---

## Quick start

```bash
pip install -r requirements.txt
```

On Windows, **double-click `run.bat`** for a menu. Otherwise:

```bash
python main.py add
```

Type a card code, confirm it's the right card, type what you paid, and it's
saved. Then:

```bash
python main.py
```

| Command | What it does |
|---|---|
| `python main.py` | Show the portfolio report |
| `python main.py add` | Add cards interactively, then show the report |
| `python main.py sell` | Remove sold cards (whole lot or part of one) |
| `python main.py dashboard` | Web dashboard at http://127.0.0.1:8802 |
| `python main.py menu` | Menu (what `run.bat` uses) |

At any prompt, **Enter** or **Q** goes back; Ctrl+C exits cleanly without
touching `collection.csv`. Card codes are validated against the shape
`OP15-118` before anything is saved, so a mistyped menu number can't become a
card.

### Editing from the dashboard

The dashboard is fully editable — **+ Add card** in the header, and **Edit** /
**Sold** on every row. The terminal and the browser write the same
`collection.csv`, so use whichever you prefer.

Because a browser can be left open on stale data, writes are guarded:

| Guard | Why |
|---|---|
| Row identity re-checked (index **plus** card id **plus** variant) | A stale tab can't edit or delete the wrong card; it gets `409` and a "refresh" message |
| `Origin` header must match the server | Any website you visit can POST to `127.0.0.1`; this blocks cross-site writes |
| Bound to `127.0.0.1` unless `$PORT` is set | A local run isn't exposed to your network |
| Variant must be one the provider lists | You can't invent a printing that has no price |
| Buy price / quantity validated server-side | The browser is not trusted to have done it |
| Single write lock | `ThreadingHTTPServer` is concurrent; CSV read-modify-write is not atomic |

Buy prices accept `220000`, `220.000`, `220,000`, `Rp 220k`, `1.5jt`, `150rb`.

First run hits the network; re-runs within 24 hours are served entirely from
`cache.json`.

Run the tests (no network needed):

```bash
python -m pytest tests -q
```

---

## How it fits together

```
main.py            entry point: load -> price -> convert -> report (orchestration only)
providers/
  base.py          PriceProvider ABC + CachedPriceProvider + get_provider() factory
  optcg.py         OPTCGProvider — the only file that knows optcgapi.com exists
cache.py           JsonCache: file-backed key/value store with a TTL
fx.py              USD->IDR fetch, parser registry, 24h cache, stale fallback
portfolio.py       Holding/PricedHolding dataclasses, CSV loading, P/L maths
report.py          rich table + portfolio.csv writer
cli.py             interactive add / sell, variant picker
dashboard.py       stdlib HTTP server: serves dashboard.html + /api/data JSON
dashboard.html     dark dashboard page (same shape as tennis_predictor.py)
config.py          every tunable, overridable via .env
tests/             77 tests covering cache TTL, provider swap, P/L, FX, variants
```

The dependency rule: **everything depends on the `PriceProvider` interface, and
nothing outside `providers/` knows a vendor name, URL, or field name.** This is
the broker-adapter pattern from a multi-exchange trading bot — the strategy
talks to `Broker`, never to Binance.

```python
class PriceProvider(ABC):
    name: str

    @abstractmethod
    def get_price_usd(self, card_id: str) -> float | None: ...
```

Implementations must be **total**: any failure — network, timeout, HTTP error,
unknown card, malformed JSON — returns `None`, never raises. That single rule is
what keeps one bad card from killing a run.

### Caching is a layer, not a provider concern

`CachedPriceProvider` wraps *any* provider, so a future paid provider inherits
the full caching and failure policy without writing a line of cache code:

| Situation | Behaviour |
|---|---|
| Cache entry < 24h old | Return it, no network call |
| Miss/expired, fetch succeeds | Store and return the fresh price |
| Miss/expired, fetch fails | Fall back to the **stale** cached price |
| Nothing cached, fetch fails | Return `None` → row shows `ERROR` |

`ERROR` rows are excluded from the totals so a single failure never silently
distorts the portfolio value.

Cache keys are namespaced by provider (`optcg:OP15-118`), so switching to a paid
source never serves prices scraped from the free one. The FX rate lives in the
same file under the reserved key `__fx_usd_idr__`.

---

## Adding a paid provider later

Switching from the free OPTCGAPI to a paid API is **one new file and one config
line**. No business logic changes.

**1. Add `providers/tcgapi.py`** — implement the one abstract method:

```python
from providers.base import PriceProvider


class TCGApiProvider(PriceProvider):
    name = "tcgapi"          # also the cache namespace — keep it unique

    def __init__(self, api_key: str, base_url: str, timeout: float = 15.0):
        self.api_key = api_key
        ...

    def get_price_usd(self, card_id: str) -> float | None:
        # Return the USD market price, or None on ANY failure. Never raise.
        ...
```

**2. Register it in `providers/base.py`** — add one entry to `PROVIDERS`:

```python
def _build_tcgapi() -> PriceProvider:
    from providers.tcgapi import TCGApiProvider
    return TCGApiProvider(
        api_key=config.PROVIDER_API_KEY,
        base_url=config.TCGAPI_BASE_URL,
        timeout=config.HTTP_TIMEOUT_SECONDS,
    )

PROVIDERS = {
    "optcg": _build_optcg,
    "tcgapi": _build_tcgapi,   # <- new
}
```

**3. Flip the config line** — in `config.py` or `.env`:

```
PRICE_PROVIDER=tcgapi
PROVIDER_API_KEY=your-key-here
```

That's it. `main.py`, `portfolio.py`, `report.py`, `cache.py` and `fx.py` are
untouched. Caching, stale fallback and `ERROR` handling apply automatically
because they live in the wrapper, not the provider.

> The API key is read in `config.py` and injected by the factory, so no
> business-logic module ever sees a credential.

### Swapping the FX source

Change `FX_API_URL`. If the new response shape differs, add a parser to
`FX_PARSERS` in `fx.py` and point `FX_SOURCE` at it — two small functions,
nothing else moves.

---

## Currency

The portfolio is reported in whatever currency you choose. Pick it from the
**currency dropdown in the dashboard header** — the change is instant and is
remembered in `settings.json`. Or set the starting default in `.env`:

```
DISPLAY_CURRENCY=IDR    # or USD, EUR, SGD, MYR, PHP, GBP, AUD, JPY...
```

Prices arrive in each source's own currency (¥ from Yuyu-tei, $ from
optcgapi) and are converted into the display currency, one rate per currency
actually used. Symbols and decimal places follow the currency: rupiah and yen
show whole numbers, dollars and euros show cents.

**Buy prices are stored with the currency they were paid in** (`buy_price` +
`buy_currency` in `collection.csv`). That is the important bit: switching your
display currency re-values the portfolio but never rewrites what you actually
paid. Someone who bought in rupiah and now reports in USD still has an honest
purchase history.

Files written before this used a `buy_price_idr` column; they are read as
rupiah automatically and upgraded on the next save, so nothing needs migrating
by hand.

> One caveat: the rate for each currency is fetched independently, so a
> JPY→USD and an IDR→USD rate come from separate quotes. Cross-rate rounding
> means a P/L percentage can differ by a couple of tenths depending on the
> display currency. The underlying money is right; only the last decimal moves.

## Regions: Japanese vs English cards

A Japanese card and its English counterpart are **different products with
unrelated prices** — the ST01 Luffy leader is ¥1,980 in Japan and $81.99 in the
English market. So every row carries a `region`, and each region has its own
source and its own currency:

| Region | Source | Currency |
|---|---|---|
| `jp` | [Yuyu-tei](https://yuyu-tei.jp) (遊々亭), scraped | JPY |
| `en` | [optcgapi](https://optcgapi.com), JSON API | USD |

Set your default in `config.py` / `.env`:

```
DEFAULT_REGION=jp
```

Conversion to IDR happens **above** the provider — a provider states its
`currency` and never knows about rupiah — so `fx.py` fetches one rate per
currency actually used, lazily. An all-Japanese collection never requests a USD
rate.

### Yuyu-tei notes

Yuyu-tei has no public API, so `providers/yuyutei.py` parses their
server-rendered **search** pages, one per card
(`/sell/opc/s/search?search_word=OP01-073`). The per-set page was tried first
but omits sold-out printings and files SP art outside the rarity sections, so a
card like the OP01-073 SP (¥5,980, out of stock) was invisible; the search page
lists every printing of a code. Results are cached 24h, so a run costs one
request per distinct card, spaced by `YUYUTEI_DELAY_SECONDS` (default 1s).

Variants are keyed on the **rarity code** (read from each card's image alt),
not the name — two OP01-073 printings are both "ドフラミンゴ(パラレル)" and only
the rarity (P-R ¥1,480 vs SP ¥5,980) tells them apart. The keys line up with
the English source: `base`, `parallel`, `sp`, `no-holo`, `manga`.

Two different prices are available:

```
YUYUTEI_PRICE_MODE=sell   # 販売価格 — the shop's asking price (default)
YUYUTEI_PRICE_MODE=buy    # 買取価格 — what the shop pays YOU
```

`sell` is the comparable "market price"; `buy` is closer to what you would
actually realise. They are cached separately.

**Being a scraper, this is more fragile than an API** — if Yuyu-tei restyles
their pages, the selectors in `providers/yuyutei.py` need updating. Nothing
else in the program is affected.

Variant keys are normalised across regions: `パラレル` and `(Parallel)` both
become `parallel`, so the two sources describe printings the same way.

## Graded cards (PSA / BGS) — ready, not yet wired

A raw card and a PSA-10 slab of the same card are different line items with
unrelated values, so every row carries a **`grade`** (default `raw`).

**Tagging a card graded:** in the dashboard, pick a grade from the **Grade**
dropdown in the *Add* form, or hit **Edit** on any row and change its grade
there (so you can also un-tag it). The grade shows as a badge after the card
name, e.g. `OP15-118  JP  PSA-10`. Grades offered: PSA 10/9/8, BGS 10/9.5,
CGC 10, SGC 10.

The free sources (Yuyu-tei, optcgapi) price **raw** cards only. A graded row
under a raw-only source shows `ERROR` on purpose — the tracker refuses to pass a
raw price off as a slab's value. To actually price graded cards you point it at
a **grade-aware provider**: `providers/pricecharting.py` is written and
registered for exactly this, but it is **inert** — it is nobody's default and
does nothing without an API token.

### Turning on PriceCharting (when you have graded cards)

PriceCharting is behind a bot-wall, so it can't be scraped; it has a **paid
JSON API**. You supply the token — I can't buy or create the account for you.

1. Get an API token from PriceCharting (paid tier).
2. Put it in `.env`: `PROVIDER_API_KEY=your-token`.
3. Point a region at it, e.g. price English graded cards with it:
   `EN_PRICE_PROVIDER=pricecharting`.

The provider already handles PriceCharting's two traps — prices are integer
**pennies**, and for cards it reuses old video-game field names for grades
(`loose-price`=ungraded, `graded-price`≈PSA 9, `manual-only-price`=PSA 10).
Two things still need verifying against the live API before you trust it: the
`card_id → product_id` lookup (their search is fuzzy) and per-variant graded
prices. Both are flagged in the file.

## Data source (Phase 1)

[OPTCGAPI](https://optcgapi.com) — free, no authentication, GET only.

| Card kind | Endpoint |
|---|---|
| Booster set cards (`OP15-118`) | `GET /api/sets/card/{card_id}/` |
| Starter deck cards (`ST01-001`) | `GET /api/decks/card/{card_id}/` |

`OPTCGProvider` routes `ST*` ids to the decks endpoint automatically.

### Variants (parallel / alternate art)

A card code covers **every printing of that card**, and the price gap is huge —
`OP10-118` is $6.52 normal and $72.24 parallel; `OP05-119` runs from $9.97 to
$5,749.97 across nine printings. So the printing cannot be guessed; the add flow
lists them and asks which one you own, and the answer is stored in the
`variant` column of `collection.csv`.

Variant labels are **derived** from the vendor's `card_name` (strip the card
number, keep the remaining parentheses) rather than enumerated, so new variant
names work without a code change:

| `card_name` | variant |
|---|---|
| `Monkey.D.Luffy (118)` | `base` |
| `Monkey.D.Luffy (118) (Parallel)` | `parallel` |
| `Enel (OP15-118) (Alternate Art)` | `alternate-art` |
| `Monkey.D.Luffy (119) (SP) (Gold)` | `sp-gold` |

Each variant is priced and cached independently (`optcg:OP10-118:parallel`).
A row whose variant no longer exists upstream shows `ERROR` rather than
silently falling back to the normal print's price.

Both return a JSON **array** with one object per printing, each carrying
`market_price` in USD. **optcgapi does not document where those prices come
from** — the `market_price` / `inventory_price` naming and the English-only set
coverage suggest TCGplayer, but that is inference. Each record carries a
`date_scraped` (one day behind on every run tested), so this is a daily
snapshot of a US-market price, not a live quote, and not an Indonesian
local-market price. A card like Enel OP15-118 has a
base printing at ~$16 and an Alternate Art at ~$31; some cards have nine
printings ranging from $10 to $3,900. The provider selects the **base printing**
(the shortest `card_name`, since variants append suffixes like
`(Alternate Art)`, `(Manga)`, `(SP)`).

> If you hold alt arts, their value is understated. Phase 2 idea: add a
> `variant` column to `collection.csv` and match on it — a change confined to
> `providers/optcg.py` and `portfolio.py`.

---

## Files

**`collection.csv`** (input) — `name, card_id, buy_price_idr, quantity`, where
`buy_price_idr` is the price paid for **one** card:

```csv
name,card_id,buy_price_idr,quantity
Enel (SEC),OP15-118,220000,2
```

**`portfolio.csv`** (output) — `name, card_id, qty, buy_idr, market_usd,
market_idr, pl_idr, pl_pct`. `buy_idr`, `market_idr` and `pl_idr` are
**lot** totals (× quantity). Unpriced cards get `ERROR` in the value columns.

**`cache.json`** — `{key: {"value": ..., "timestamp": <unix>}}`. Delete it to
force a full refresh; it is safe to hand-edit, and a corrupt file is discarded
rather than fatal.

---

## Configuration

Every value has a working default; `.env` (see `.env.example`) overrides.

| Setting | Default | Purpose |
|---|---|---|
| `PRICE_PROVIDER` | `optcg` | **The data-source switch** |
| `PROVIDER_API_KEY` | *(none)* | For paid providers; unused in Phase 1 |
| `CACHE_TTL_HOURS` | `24` | Applies to prices *and* the FX rate |
| `CACHE_FILE` | `cache.json` | |
| `FX_SOURCE` / `FX_API_URL` | `er_api` / open.er-api.com | Parser + endpoint |
| `FX_TARGET_CURRENCY` | `IDR` | |
| `COLLECTION_FILE` / `PORTFOLIO_FILE` | `collection.csv` / `portfolio.csv` | |
| `HTTP_TIMEOUT_SECONDS` | `15` | |

---

## Terminal output notes

P/L cells are green at ≥ 0 and red below. All eight columns need about 105
characters; on a narrower terminal the layout drops `Mkt IDR` (then `Mkt USD`)
rather than truncating figures into `+137,9…`. Nothing is lost —
`portfolio.csv` always contains every column.

---

## Deliberately not built yet

User accounts, a web server or database, Whop licensing, and Pokémon support.
Pokémon in particular needs no rewrite: add a provider that resolves Pokémon
card ids and give `collection.csv` a `game` column — `portfolio.py`,
`cache.py`, `fx.py` and `report.py` are already game-agnostic.
