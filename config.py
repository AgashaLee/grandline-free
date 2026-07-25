"""Central configuration.

Every tunable lives here. Business logic imports values from this module and
never reads ``os.environ`` directly, so adding a paid provider (and its API key)
later means editing this file plus one new provider module -- nothing else.

Values may be overridden with environment variables or a ``.env`` file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:  # python-dotenv is optional at runtime; env vars still work without it.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - trivial fallback
    pass


BASE_DIR = Path(__file__).resolve().parent


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _path(key: str, default: str) -> Path:
    """Resolve a configured path relative to the project dir if not absolute."""
    raw = Path(_env(key, default))
    return raw if raw.is_absolute() else BASE_DIR / raw


# --- Provider selection -------------------------------------------------
# Japanese and English printings are different products with different prices,
# so each region has its own source. A card's region is stored per row in
# collection.csv; this is only the default for newly added cards.
DEFAULT_REGION: str = _env("DEFAULT_REGION", "jp")

#: region -> provider name registered in providers/base.py (see PROVIDERS).
#: THESE ARE THE LINES TO CHANGE when swapping data sources.
PROVIDER_BY_REGION: dict[str, str] = {
    "jp": _env("JP_PRICE_PROVIDER", "yuyutei"),
    "en": _env("EN_PRICE_PROVIDER", "optcg"),
}

#: Fallback when a row has no region (files written before regions existed).
PRICE_PROVIDER: str = _env("PRICE_PROVIDER", PROVIDER_BY_REGION["en"])

#: Grades a card can be tagged with. "raw" = ungraded. The rest are slabs that
#: only a grade-aware source (PriceCharting) can price automatically.
GRADE_CHOICES: tuple[str, ...] = (
    "raw", "psa-10", "psa-9", "psa-8", "bgs-10", "bgs-9.5", "cgc-10", "sgc-10",
)

# Credentials for providers that need them. Phase 1 uses a free, key-less API,
# so this is empty -- it exists so a paid provider can be dropped in without
# touching any business logic.
PROVIDER_API_KEY: str | None = os.getenv("PROVIDER_API_KEY") or None

# Passed to every provider constructor.
HTTP_TIMEOUT_SECONDS: float = float(_env("HTTP_TIMEOUT_SECONDS", "15"))

# Base URL of the free OPTCG API. Configurable so a mirror can be used.
OPTCG_BASE_URL: str = _env("OPTCG_BASE_URL", "https://optcgapi.com/api")

# Yuyu-tei (遊々亭), a Japanese card shop. Prices are per-set HTML pages.
YUYUTEI_BASE_URL: str = _env("YUYUTEI_BASE_URL", "https://yuyu-tei.jp")
# "sell" = 販売価格, the shop's asking price (comparable to a market price).
# "buy"  = 買取価格, what the shop pays YOU -- lower, but closer to what you
# would actually realise if you sold. Set YUYUTEI_PRICE_MODE=buy for that.
YUYUTEI_PRICE_MODE: str = _env("YUYUTEI_PRICE_MODE", "sell")
# Be a polite scraper: seconds to wait between set-page fetches.
YUYUTEI_DELAY_SECONDS: float = float(_env("YUYUTEI_DELAY_SECONDS", "1.0"))


# --- Caching ------------------------------------------------------------
CACHE_TTL_HOURS: float = float(_env("CACHE_TTL_HOURS", "24"))
CACHE_FILE: Path = _path("CACHE_FILE", "cache.json")

# Reserved cache-key prefix for FX rates (card ids never collide with it).
FX_CACHE_KEY: str = "__fx_usd_idr__"  # legacy USD key, kept for old caches


def fx_cache_key(base: str, target: str | None = None) -> str:
    return f"__fx_{base.lower()}_{(target or FX_TARGET_CURRENCY).lower()}__"


# --- Exchange rate ------------------------------------------------------
# Free, no-key source. Swap by changing this URL and, if the JSON shape
# differs, the parser registered in fx.py. {base} is filled per currency --
# JPY prices from Yuyu-tei and USD prices from optcgapi need different rates.
FX_SOURCE: str = _env("FX_SOURCE", "er_api")
FX_API_URL: str = _env("FX_API_URL", "https://open.er-api.com/v6/latest/{base}")

#: User choices that outlive a restart (currently just the display currency).
#: Kept separate from .env so the dashboard can change them without editing
#: config files.
SETTINGS_FILE: Path = _path("SETTINGS_FILE", "settings.json")


def _load_settings() -> dict:
    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_SETTINGS = _load_settings()

#: The currency everything is REPORTED in -- totals, P/L, the whole portfolio.
#: Prices arrive in the source's own currency (JPY/USD) and are converted here,
#: so a user anywhere can read their collection in their own money.
#: Precedence: saved setting > DISPLAY_CURRENCY env > FX_TARGET_CURRENCY env.
DISPLAY_CURRENCY: str = str(
    _SETTINGS.get("display_currency") or _env("DISPLAY_CURRENCY", _env("FX_TARGET_CURRENCY", "IDR"))
).upper()
FX_TARGET_CURRENCY: str = DISPLAY_CURRENCY


def set_display_currency(code: str) -> str:
    """Switch the reporting currency at runtime and remember the choice.

    Everything downstream reads ``config.DISPLAY_CURRENCY`` when it runs, so a
    change takes effect on the next report without a restart.
    """
    global DISPLAY_CURRENCY, FX_TARGET_CURRENCY

    code = str(code or "").strip().upper()
    if not code.isalpha() or len(code) != 3:
        raise ValueError(f"{code!r} is not a 3-letter currency code.")

    DISPLAY_CURRENCY = FX_TARGET_CURRENCY = code
    _SETTINGS["display_currency"] = code
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS_FILE.open("w", encoding="utf-8") as fh:
            json.dump(_SETTINGS, fh, indent=2)
    except OSError:
        pass  # failing to persist must not break the switch itself
    return code

#: How to render each currency: (symbol, decimal places). Currencies without a
#: minor unit in practice (rupiah, yen, won) show no decimals; the rest show 2.
#: Unknown currencies fall back to "CODE 1,234.56".
CURRENCY_FORMAT: dict[str, tuple[str, int]] = {
    "IDR": ("Rp ", 0), "JPY": ("¥", 0), "KRW": ("₩", 0), "VND": ("₫", 0),
    "USD": ("$", 2), "EUR": ("€", 2), "GBP": ("£", 2), "AUD": ("A$", 2),
    "CAD": ("C$", 2), "SGD": ("S$", 2), "MYR": ("RM", 2), "PHP": ("₱", 2),
    "THB": ("฿", 2), "NZD": ("NZ$", 2), "CHF": ("CHF ", 2), "CNY": ("¥", 2),
    "HKD": ("HK$", 2), "TWD": ("NT$", 2), "INR": ("₹", 2), "BRL": ("R$", 2),
}


def currency_format(code: str) -> tuple[str, int]:
    """Return ``(symbol, decimals)`` for a currency, with a safe fallback."""
    return CURRENCY_FORMAT.get((code or "").upper(), (f"{code} ", 2))


# --- Dashboard ----------------------------------------------------------
# 8801 is the tennis predictor's port; keep them clear of each other.
DASHBOARD_PORT: int = int(_env("DASHBOARD_PORT", "8802"))


# --- Files --------------------------------------------------------------
COLLECTION_FILE: Path = _path("COLLECTION_FILE", "collection.csv")
PORTFOLIO_FILE: Path = _path("PORTFOLIO_FILE", "portfolio.csv")
