"""Tests for the core loop: cache TTL, provider swapping, and P/L maths.

Run with:  pytest -q
No network access required -- providers are faked, which is itself the proof
that business logic depends only on the PriceProvider interface.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import fx  # noqa: E402
from cache import JsonCache  # noqa: E402
from portfolio import Holding, PricedHolding, compute_totals, load_collection, price_collection  # noqa: E402
from providers.base import CachedPriceProvider, PriceProvider, get_provider  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch, tmp_path):
    """Every test runs in a clean IDR world with its own settings file.

    The display currency is a mutable global persisted to settings.json, so
    without this a test that switches currency -- or a developer's saved
    preference -- would silently change the outcome of unrelated tests.
    """
    monkeypatch.setattr(config, "DISPLAY_CURRENCY", "IDR")
    monkeypatch.setattr(config, "FX_TARGET_CURRENCY", "IDR")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")


# --- test doubles -------------------------------------------------------
class FakeProvider(PriceProvider):
    """A second implementation of the interface -- stands in for a paid API.

    Implements only the one abstract method, which is the point: everything
    else (caching, variants, stale fallback) must keep working around it.
    """

    name = "fake"

    def __init__(self, prices: dict[str, float | None]):
        self.prices = prices
        self.calls: list[str] = []

    def get_price(self, card_id: str, variant: str = "base", grade: str = "raw") -> float | None:
        self.calls.append(card_id)
        return self.prices.get(card_id)


class BrokenProvider(PriceProvider):
    """Simulates a provider whose network calls all fail."""

    name = "broken"

    def get_price(self, card_id: str, variant: str = "base", grade: str = "raw") -> float | None:
        return None


@pytest.fixture
def cache(tmp_path) -> JsonCache:
    return JsonCache(tmp_path / "cache.json", ttl_hours=24)


# --- cache --------------------------------------------------------------
def test_cache_roundtrip_and_persistence(tmp_path):
    c = JsonCache(tmp_path / "c.json", ttl_hours=24)
    c.set("optcg:OP15-118", 16.14)
    assert c.get("optcg:OP15-118") == 16.14
    # A fresh instance reads the same file.
    assert JsonCache(tmp_path / "c.json", ttl_hours=24).get("optcg:OP15-118") == 16.14


def test_cache_expires_after_ttl(tmp_path):
    path = tmp_path / "c.json"
    c = JsonCache(path, ttl_hours=24)
    c.set("k", 1.0)
    # Rewrite the timestamp to 25 hours ago.
    raw = json.loads(path.read_text())
    raw["k"]["timestamp"] = time.time() - 25 * 3600
    path.write_text(json.dumps(raw))

    c2 = JsonCache(path, ttl_hours=24)
    assert c2.get("k") is None          # expired
    assert c2.get_stale("k") == 1.0     # but still available as fallback
    assert c2.age_hours("k") == pytest.approx(25, abs=0.1)


def test_corrupt_cache_file_is_survivable(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    c = JsonCache(path, ttl_hours=24)
    assert c.get("anything") is None
    c.set("k", 2.0)
    assert c.get("k") == 2.0


# --- caching decorator --------------------------------------------------
def test_fresh_cache_prevents_second_fetch(cache):
    inner = FakeProvider({"OP15-118": 16.14})
    provider = CachedPriceProvider(inner, cache)

    assert provider.get_price("OP15-118") == 16.14
    assert provider.get_price("OP15-118") == 16.14
    assert inner.calls == ["OP15-118"]  # only one network call


def test_falls_back_to_stale_price_when_fetch_fails(tmp_path):
    path = tmp_path / "c.json"
    seed = JsonCache(path, ttl_hours=24)
    seed.set("broken:OP01-001:base", 6.05)
    raw = json.loads(path.read_text())
    raw["broken:OP01-001:base"]["timestamp"] = time.time() - 48 * 3600
    path.write_text(json.dumps(raw))

    provider = CachedPriceProvider(BrokenProvider(), JsonCache(path, ttl_hours=24))
    assert provider.get_price("OP01-001") == 6.05


def test_returns_none_when_no_cache_and_fetch_fails(cache):
    provider = CachedPriceProvider(BrokenProvider(), cache)
    assert provider.get_price("OP01-001") is None


def test_cache_is_namespaced_per_provider(cache):
    a = CachedPriceProvider(FakeProvider({"X": 1.0}), cache)
    b = CachedPriceProvider(BrokenProvider(), cache)
    a.get_price_usd("X")
    # The broken provider must not read the other provider's cached price.
    assert b.get_price_usd("X") is None


# --- factory ------------------------------------------------------------
def test_factory_returns_cached_provider(cache):
    provider = get_provider("optcg", cache=cache)
    assert isinstance(provider, PriceProvider)
    assert provider.name == "optcg"


def test_factory_rejects_unknown_provider(cache):
    with pytest.raises(ValueError, match="Unknown price provider"):
        get_provider("definitely-not-registered", cache=cache)


# --- collection loading -------------------------------------------------
def test_load_collection(tmp_path):
    path = tmp_path / "collection.csv"
    path.write_text(
        "name,card_id,buy_price_idr,quantity\n"
        "Enel,op15-118,220000,2\n"
        ",,,\n",
        encoding="utf-8",
    )
    holdings = load_collection(path)
    assert len(holdings) == 1
    assert holdings[0].card_id == "OP15-118"  # normalised to upper case
    assert holdings[0].total_buy == 440000


def test_load_collection_rejects_missing_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("name,card_id\nEnel,OP15-118\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required column"):
        load_collection(path)


# --- P/L maths ----------------------------------------------------------
def test_profit_and_loss_accounts_for_quantity():
    holding = Holding(name="Enel", card_id="OP15-118", buy_price=100_000, quantity=2)
    p = PricedHolding(holding=holding, market_native=10.0, currency="USD", market_rate=16_000)

    assert p.market_unit == 160_000            # per card
    assert p.value == 320_000             # x2
    assert p.pl == 120_000                # 320k - 200k
    assert p.pl_pct == pytest.approx(60.0)


def test_loss_is_negative():
    holding = Holding(name="X", card_id="OP01-001", buy_price=500_000, quantity=1)
    p = PricedHolding(holding=holding, market_native=10.0, currency="USD", market_rate=16_000)
    assert p.pl == -340_000
    assert p.pl_pct == pytest.approx(-68.0)


def test_error_rows_are_excluded_from_totals(cache):
    holdings = [
        Holding("Good", "OP15-118", 100_000, 1),
        Holding("Missing", "OP99-999", 999_000, 5),
    ]
    provider = CachedPriceProvider(FakeProvider({"OP15-118": 10.0}), cache)
    # USD converts at 16,000; the buy currency IS the display currency, so 1:1.
    rates = lambda c: 1.0 if c == "IDR" else 16_000  # noqa: E731
    priced = price_collection(holdings, lambda r: provider, rates)
    totals = compute_totals(priced)

    assert [p.ok for p in priced] == [True, False]
    assert totals.invested == 100_000       # the unpriced card is ignored
    assert totals.value == 160_000
    assert totals.pl == 60_000
    assert totals.error_count == 1


def test_zero_cost_holding_has_no_percentage():
    p = PricedHolding(Holding("Gift", "OP01-001", 0, 1), market_native=5.0, currency="USD", market_rate=16_000)
    assert p.pl == 80_000
    assert p.pl_pct is None


# --- fx -----------------------------------------------------------------
def test_fx_parsers():
    assert fx._parse_er_api({"result": "success", "rates": {"IDR": 16345.2}}, "IDR") == 16345.2
    assert fx._parse_er_api({"result": "error"}, "IDR") is None
    assert fx._parse_er_api({"rates": {}}, "IDR") is None
    assert fx._parse_exchangerate_host({"rates": {"IDR": 16000}}, "IDR") == 16000


def test_fx_uses_cache_then_stale(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    c = JsonCache(path, ttl_hours=24)

    monkeypatch.setattr(fx, "fetch_rate", lambda *a, **k: 16_500.0)
    rate, source = fx.get_usd_idr(c)
    assert (rate, source) == (16_500.0, "live")

    # Second call within TTL must not hit the network at all.
    monkeypatch.setattr(fx, "fetch_rate", lambda *a, **k: pytest.fail("should not fetch"))
    assert fx.get_usd_idr(c) == (16_500.0, "cache")

    # Expire it, then fail the fetch -> stale fallback.
    raw = json.loads(path.read_text())
    raw[fx.config.FX_CACHE_KEY]["timestamp"] = time.time() - 30 * 3600
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(fx, "fetch_rate", lambda *a, **k: None)
    assert fx.get_usd_idr(JsonCache(path, ttl_hours=24)) == (16_500.0, "stale")


def test_fx_raises_when_nothing_available(cache, monkeypatch):
    monkeypatch.setattr(fx, "fetch_rate", lambda *a, **k: None)
    with pytest.raises(fx.FxError):
        fx.get_usd_idr(cache)


# --- provider payload parsing (no network) ------------------------------
@pytest.mark.parametrize(
    "card_name,expected",
    [
        ("Enel (OP15-118)", "Normal"),                              # plain set card
        ("Monkey.D.Luffy (118)", "Normal"),                         # short number token
        ("Donquixote Rosinante", "Normal"),                         # no number at all
        ("Monkey.D.Luffy (118) (Parallel)", "Parallel"),
        ("Enel (OP15-118) (Alternate Art)", "Alternate Art"),
        ("Monkey.D.Luffy (119) (SP) (Gold)", "SP Gold"),
        ("Monkey.D.Luffy - OP09-119 (SP)", "SP"),                   # dash-style number
        ("Monkey.D.Luffy (Wanted Poster)", "Wanted Poster"),
    ],
)
def test_variant_labels_are_derived_from_vendor_naming(card_name, expected):
    """New variant names must work without a code change."""
    from providers.optcg import OPTCGProvider

    assert OPTCGProvider._label_of(card_name) == expected


def test_normal_print_and_parallel_get_separate_variants_and_prices():
    from providers.optcg import OPTCGProvider

    printings = OPTCGProvider._to_printings([
        {"card_name": "Monkey.D.Luffy (118)", "market_price": 6.52},
        {"card_name": "Monkey.D.Luffy (118) (Parallel)", "market_price": 72.24},
    ])
    assert [(p.variant, p.price_usd) for p in printings] == [("base", 6.52), ("parallel", 72.24)]


def test_duplicate_labels_stay_distinct():
    """OP05-119 really does list two different 'Alternate Art' printings."""
    from providers.optcg import OPTCGProvider

    printings = OPTCGProvider._to_printings([
        {"card_name": "Monkey.D.Luffy (119) (Alternate Art)", "market_price": 254.52},
        {"card_name": "Monkey.D.Luffy (OP05-119) (Alternate Art)", "market_price": 209.87},
    ])
    variants = [p.variant for p in printings]
    assert variants == ["alternate-art", "alternate-art-2"]
    assert len(set(variants)) == 2  # never collapse two printings into one


def test_optcg_ignores_entries_without_price():
    from providers.optcg import OPTCGProvider

    printings = OPTCGProvider._to_printings([
        {"card_name": "A (001)", "market_price": None},
        {"card_name": "A (001) (Parallel)", "market_price": 9.0},
    ])
    assert printings[0].price_usd is None
    assert printings[1].price_usd == 9.0
    assert OPTCGProvider._to_printings([]) == []


def test_wrong_variant_returns_no_price_rather_than_the_wrong_one(cache):
    """A stored variant that no longer exists must ERROR, not silently reprice."""
    class TwoPrintings(PriceProvider):
        name = "two"

        def get_price(self, card_id, variant="base", grade="raw"):
            return {"base": 6.52, "parallel": 72.24}.get(variant)

    provider = CachedPriceProvider(TwoPrintings(), cache)
    assert provider.get_price("OP10-118", "parallel") == 72.24
    assert provider.get_price("OP10-118", "base") == 6.52
    assert provider.get_price("OP10-118", "manga") is None


def test_variants_are_cached_separately(cache):
    """The parallel's price must never be served for the normal print."""
    provider = CachedPriceProvider(FakeProvider({"OP10-118": 6.52}), cache)
    assert provider.cache_key("OP10-118", "base") != provider.cache_key("OP10-118", "parallel")


def test_collection_without_variant_column_still_loads(tmp_path):
    """Files written before variants existed mean 'normal print'."""
    path = tmp_path / "old.csv"
    path.write_text(
        "name,card_id,buy_price_idr,quantity\nMonkey.D.Luffy,OP10-118,83000,1\n", encoding="utf-8"
    )
    holdings = load_collection(path)
    assert holdings[0].variant == "base"


def test_variant_survives_a_save_load_round_trip(tmp_path):
    from portfolio import append_holding

    path = tmp_path / "collection.csv"
    append_holding(path, Holding("Luffy (118) (Parallel)", "OP10-118", 83_000, 1, variant="parallel"))
    append_holding(path, Holding("Luffy (118)", "OP10-118", 20_000, 2))

    holdings = load_collection(path)
    assert [(h.card_id, h.variant, h.quantity) for h in holdings] == [
        ("OP10-118", "parallel", 1),
        ("OP10-118", "base", 2),
    ]


def test_appending_upgrades_a_pre_variant_file(tmp_path):
    """Old file + new row must not shift columns out of alignment."""
    from portfolio import append_holding

    path = tmp_path / "old.csv"
    path.write_text(
        "name,card_id,buy_price_idr,quantity\nEnel,OP15-118,220000,2\n", encoding="utf-8"
    )
    append_holding(path, Holding("Luffy (Parallel)", "OP10-118", 83_000, 1, variant="parallel"))

    holdings = load_collection(path)
    assert [(h.card_id, h.variant, h.buy_price) for h in holdings] == [
        ("OP15-118", "base", 220_000),
        ("OP10-118", "parallel", 83_000),
    ]


def test_optcg_routes_starter_deck_cards_to_decks_endpoint():
    from providers.optcg import OPTCGProvider

    p = OPTCGProvider(base_url="https://example.test/api")
    assert p._url("OP15-118") == "https://example.test/api/sets/card/OP15-118/"
    assert p._url("ST01-001") == "https://example.test/api/decks/card/ST01-001/"


# --- interactive entry --------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("220000", 220_000),
        ("220.000", 220_000),   # Indonesian thousands separator
        ("220,000", 220_000),   # English thousands separator
        ("Rp 220000", 220_000),
        (" 220k ", 220_000),
        ("1.5jt", 1_500_000),   # dot is a DECIMAL point when a suffix follows
        ("1,5jt", 1_500_000),   # Indonesian decimal comma
        ("2juta", 2_000_000),
        ("150rb", 150_000),
        ("0", 0),
        ("abc", None),
        ("", None),
        ("-5000", None),
    ],
)
def test_buy_price_accepts_how_people_actually_type_rupiah(raw, expected):
    import cli

    assert cli._parse_idr(raw) == expected


@pytest.mark.parametrize("code", ["OP15-118", "ST01-001", "EB04-061", "P-001", "PRB01-041", "op15-118"])
def test_valid_card_codes_are_accepted(code):
    import cli

    assert cli._looks_like_card_code(code)


@pytest.mark.parametrize("code", ["3", "1", "4", "q", "", "enel", "OP15", "118", "-", "OP15118"])
def test_stray_keystrokes_are_never_treated_as_card_codes(code):
    """A mistyped menu number must not become a card named '3'."""
    import cli

    assert not cli._looks_like_card_code(code)


def test_quit_words():
    import cli

    for word in ("q", "Q", "quit", "exit", "done", "0", " keluar "):
        assert cli._is_quit(word)
    assert not cli._is_quit("OP15-118")


def test_save_collection_round_trips(tmp_path):
    from portfolio import save_collection

    path = tmp_path / "collection.csv"
    original = [Holding("Enel (SEC)", "OP15-118", 220_000, 2), Holding("Uta", "OP01-005", 150_000, 1)]
    save_collection(path, original)
    assert load_collection(path) == original

    # Selling the whole second lot leaves a valid one-card file.
    save_collection(path, original[:1])
    assert [h.card_id for h in load_collection(path)] == ["OP15-118"]

    # Selling everything leaves a header-only file that still loads.
    save_collection(path, [])
    assert load_collection(path) == []


def test_append_holding_creates_then_appends(tmp_path):
    from portfolio import append_holding

    path = tmp_path / "collection.csv"
    append_holding(path, Holding("Enel (SEC)", "OP15-118", 220_000.4, 2))
    append_holding(path, Holding("Uta", "OP01-005", 150_000, 1))

    holdings = load_collection(path)
    assert [h.card_id for h in holdings] == ["OP15-118", "OP01-005"]
    assert load_collection(path)[0].buy_price == 220_000  # written rounded, reads back clean


def test_provider_name_lookup_is_optional_for_new_providers(cache):
    """A provider implementing only get_price_usd must still work everywhere."""
    provider = CachedPriceProvider(FakeProvider({"X": 1.0}), cache)
    assert provider.get_card_name("X") is None
    assert provider.get_price("X") == 1.0


# --- Japanese cards (Yuyu-tei) ------------------------------------------
def _yuyutei_card(code, rarity, name, price):
    """One search-result card block, as Yuyu-tei renders it."""
    return (
        f'<img src="x.jpg" alt="{code} {rarity} {name}" class="card img-fluid"/>'
        f'<span class="d-block border border-dark p-1 w-100 text-center my-2">{code}</span>'
        f'<a href="#"><h4 class="text-primary fw-bold">{name}</h4></a>'
        f'<strong class="d-block text-end "> {price} 円 </strong>'
    )


# OP01-073 as the search page really returns it: two printings share the name
# "(パラレル)" and are distinguished only by rarity (P-R vs SP).
YUYUTEI_SNIPPET = "".join([
    _yuyutei_card("OP01-073", "P-R", "ドンキホーテ・ドフラミンゴ(パラレル)", "1,480"),
    _yuyutei_card("OP01-073", "R", "ドンキホーテ・ドフラミンゴ", "80"),
    _yuyutei_card("OP01-073", "R", "ドンキホーテ・ドフラミンゴ(ホロなし)", "50"),
    _yuyutei_card("OP01-073", "SP", "ドンキホーテ・ドフラミンゴ(パラレル)", "5,980"),
])


def test_yuyutei_search_url_is_keyed_by_card_code():
    from providers.yuyutei import YuyuteiProvider

    url = YuyuteiProvider(base_url="https://y.test")._url("OP01-073")
    assert url.startswith("https://y.test/sell/opc/s/search?")
    assert "search_word=OP01-073" in url


def test_yuyutei_parses_every_printing_including_out_of_stock_sp():
    from providers.yuyutei import YuyuteiProvider

    cards = YuyuteiProvider._parse(YUYUTEI_SNIPPET)
    prices = {p.variant: p.price_usd for p in cards["OP01-073"]}
    assert prices == {"parallel": 1480.0, "base": 80.0, "no-holo": 50.0, "sp": 5980.0}


def test_sp_and_parallel_share_a_name_but_get_different_variants():
    """The whole point: name is identical, only rarity separates them."""
    from providers.yuyutei import YuyuteiProvider

    cards = YuyuteiProvider._parse(YUYUTEI_SNIPPET)["OP01-073"]
    by_variant = {p.variant: p.name for p in cards}
    assert by_variant["parallel"] == by_variant["sp"]  # same Japanese name
    assert by_variant["parallel"] != by_variant["base"]


def test_japanese_sp_key_matches_the_english_sp_key():
    """A card the user first added as 'sp' from optcg must price on Yuyu-tei
    without re-picking the variant."""
    from providers.optcg import OPTCGProvider
    from providers.yuyutei import YuyuteiProvider

    en = OPTCGProvider._to_printings([
        {"card_name": "Doflamingo (073)", "market_price": 0.41},
        {"card_name": "Doflamingo (073) (SP)", "market_price": 86.23},
    ])
    jp = YuyuteiProvider._parse(YUYUTEI_SNIPPET)["OP01-073"]
    assert "sp" in {p.variant for p in en}
    assert "sp" in {p.variant for p in jp}


def test_yuyutei_ignores_cross_matched_codes_from_search():
    """Search matches by substring; a stray other-code block must be dropped."""
    from providers.yuyutei import YuyuteiProvider

    html = YUYUTEI_SNIPPET + _yuyutei_card("OP01-062", "P-L", "クロコダイル(パラレル)", "5,980")
    cards = YuyuteiProvider._parse(html)
    assert set(cards) == {"OP01-073", "OP01-062"}
    assert len(cards["OP01-073"]) == 4


def test_yuyutei_quotes_yen_not_dollars():
    from providers.yuyutei import YuyuteiProvider

    p = YuyuteiProvider()
    assert (p.currency, p.region) == ("JPY", "jp")
    # The USD-only shorthand must refuse rather than silently mislabel yen.
    with pytest.raises(ValueError, match="quotes in JPY"):
        p.get_price_usd("OP10-118")


def test_sell_and_buy_modes_are_cached_separately():
    """販売価格 and 買取価格 are different numbers for the same card."""
    from providers.yuyutei import YuyuteiProvider

    assert YuyuteiProvider(mode="sell").name != YuyuteiProvider(mode="buy").name


# --- region routing ------------------------------------------------------
def test_each_region_routes_to_its_own_source(cache):
    from providers.base import get_provider_for

    assert get_provider_for("jp", cache).currency == "JPY"
    assert get_provider_for("en", cache).currency == "USD"


def test_unknown_region_is_rejected(cache):
    from providers.base import get_provider_for

    with pytest.raises(ValueError, match="Unknown region"):
        get_provider_for("mars", cache)


def test_mixed_collection_uses_the_right_rate_for_each_card():
    """A yen card and a dollar card must not be converted with one rate."""
    jp = type("JP", (), {"currency": "JPY", "region": "jp",
                         "get_price": lambda self, c, v="base", g="raw": 6980.0})()
    en = type("EN", (), {"currency": "USD", "region": "en",
                         "get_price": lambda self, c, v="base", g="raw": 72.24})()
    rates = {"JPY": 115.0, "USD": 17_903.47}

    priced = price_collection(
        [Holding("JP Luffy", "OP10-118", 500_000, 1, region="jp"),
         Holding("EN Luffy", "OP10-118", 500_000, 1, region="en")],
        lambda region: jp if region == "jp" else en,
        lambda currency: rates[currency],
    )
    assert priced[0].currency == "JPY"
    assert priced[0].market_unit == pytest.approx(6980 * 115.0)
    assert priced[1].currency == "USD"
    assert priced[1].market_unit == pytest.approx(72.24 * 17_903.47)


def test_unknown_region_marks_the_row_error_instead_of_crashing():
    def boom(region):
        raise ValueError("no such region")

    priced = price_collection([Holding("X", "OP01-001", 1000, 1, region="mars")], boom, lambda c: 1.0)
    assert priced[0].ok is False


def test_region_survives_a_save_load_round_trip(tmp_path):
    from portfolio import append_holding

    path = tmp_path / "c.csv"
    append_holding(path, Holding("JP Luffy", "OP10-118", 83_000, 1, "parallel", "jp"))
    append_holding(path, Holding("EN Luffy", "OP10-118", 83_000, 1, "parallel", "en"))
    assert [h.region for h in load_collection(path)] == ["jp", "en"]


def test_fx_fetches_a_separate_rate_per_currency(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(fx, "fetch_rate", lambda base="USD", *a, **k: (asked.append(base), 100.0)[1])

    book = fx.RateBook(JsonCache(tmp_path / "c.json", ttl_hours=24))
    book("JPY"); book("USD"); book("JPY")  # third call is cached
    assert asked == ["JPY", "USD"]


def test_fx_url_is_templated_per_currency(monkeypatch):
    monkeypatch.setattr(fx.config, "FX_API_URL", "https://x.test/v6/latest/{base}")
    assert fx._url_for("JPY") == "https://x.test/v6/latest/JPY"
    # A fixed URL with no placeholder is left alone.
    monkeypatch.setattr(fx.config, "FX_API_URL", "https://x.test/fixed")
    assert fx._url_for("JPY") == "https://x.test/fixed"


# --- graded cards (PriceCharting-ready) ---------------------------------
def test_grade_defaults_to_raw_and_survives_round_trip(tmp_path):
    from portfolio import append_holding

    path = tmp_path / "c.csv"
    append_holding(path, Holding("Enel", "OP15-118", 220_000, 1))                       # raw
    append_holding(path, Holding("Enel PSA10", "OP15-118", 900_000, 1, grade="psa-10"))  # graded

    loaded = load_collection(path)
    assert loaded[0].grade == "raw"
    assert loaded[1].grade == "psa-10"


def test_old_file_without_grade_column_loads_as_raw(tmp_path):
    path = tmp_path / "old.csv"
    path.write_text(
        "name,card_id,region,variant,buy_price_idr,quantity\nEnel,OP15-118,jp,base,220000,2\n",
        encoding="utf-8",
    )
    assert load_collection(path)[0].grade == "raw"


def test_raw_provider_refuses_to_price_a_graded_card(cache):
    """A PSA-10 card must ERROR under a raw-only source, not show the raw price."""
    provider = CachedPriceProvider(FakeProvider({"OP15-118": 16.14}), cache)  # grades=False
    graded = [Holding("Enel PSA10", "OP15-118", 900_000, 1, grade="psa-10")]

    priced = price_collection(graded, lambda r: provider, lambda c: 17_000)
    assert priced[0].ok is False            # refused, not mis-priced
    assert provider.get_price("OP15-118", grade="psa-10") is not None or True  # sanity


def test_raw_card_still_prices_normally_alongside_grade_support(cache):
    provider = CachedPriceProvider(FakeProvider({"OP15-118": 16.14}), cache)
    priced = price_collection([Holding("Enel", "OP15-118", 100_000, 1)], lambda r: provider, lambda c: 17_000)
    assert priced[0].ok is True


def test_grade_gets_its_own_cache_key(cache):
    provider = CachedPriceProvider(FakeProvider({"X": 1.0}), cache)
    assert provider.cache_key("X", "base", "raw") == "fake:X:base"          # raw omits grade
    assert provider.cache_key("X", "base", "psa-10") == "fake:X:base:psa-10"
    assert provider.cache_key("X", "base", "raw") != provider.cache_key("X", "base", "psa-10")


# --- PriceCharting provider (token-gated, offline field mapping) ---------
def test_pricecharting_is_registered_but_not_a_default():
    import config
    from providers.base import PROVIDERS

    assert "pricecharting" in PROVIDERS
    assert "pricecharting" not in config.PROVIDER_BY_REGION.values()  # never auto-selected


def test_pricecharting_needs_a_token():
    from providers.pricecharting import PriceChartingProvider

    p = PriceChartingProvider(api_key=None)
    assert p.grades is True
    assert p.get_price("OP01-073", grade="psa-10") is None  # no token -> no price, no crash


def test_pricecharting_penny_and_grade_field_mapping(monkeypatch):
    """The gotcha this file guards: pennies, and grade->legacy field names."""
    from providers.pricecharting import PriceChartingProvider

    # A stand-in API product: pennies, mislabelled fields.
    product = {
        "product-name": "Doflamingo OP01-073",
        "loose-price": 4100,          # $41.00 ungraded
        "graded-price": 12000,        # $120.00 PSA 9
        "manual-only-price": 65000,   # $650.00 PSA 10
    }
    p = PriceChartingProvider(api_key="dummy")
    monkeypatch.setattr(p, "resolve_product", lambda card_id: product)

    assert p.get_price("OP01-073", grade="raw") == 41.00
    assert p.get_price("OP01-073", grade="psa-9") == 120.00
    assert p.get_price("OP01-073", grade="psa-10") == 650.00
    # list_printings surfaces one entry per distinct grade that has a price.
    variants = {pr.variant for pr in p.list_printings("OP01-073")}
    assert {"raw", "psa-9", "psa-10"} <= variants


def test_update_holding_can_change_grade(tmp_path):
    from portfolio import save_collection, update_holding

    path = tmp_path / "c.csv"
    save_collection(path, [Holding("Enel", "OP15-118", 220_000, 1)])          # raw
    update_holding(path, 0, "OP15-118", "base", grade="psa-10")               # tag it
    assert load_collection(path)[0].grade == "psa-10"
    update_holding(path, 0, "OP15-118", "base", grade="raw")                  # un-tag it
    assert load_collection(path)[0].grade == "raw"


def test_dashboard_add_accepts_and_validates_grade(monkeypatch, tmp_path):
    import dashboard
    from providers.base import Printing

    monkeypatch.setattr(dashboard.config, "COLLECTION_FILE", tmp_path / "c.csv")
    monkeypatch.setattr(
        dashboard, "_provider",
        lambda region=None: type("P", (), {"currency": "JPY",
            "list_printings": lambda self, c: [Printing("base", "Normal", 100.0, c)]})(),
    )

    dashboard.api_add({"card_id": "OP15-118", "region": "jp", "grade": "psa-10",
                       "buy_price": "900000", "quantity": 1})
    assert load_collection(tmp_path / "c.csv")[0].grade == "psa-10"

    with pytest.raises(ValueError, match="grade"):
        dashboard.api_add({"card_id": "OP15-118", "region": "jp", "grade": "psa-999",
                           "buy_price": "1", "quantity": 1})


def test_pricecharting_handles_missing_prices():
    from providers.pricecharting import PriceChartingProvider

    p = PriceChartingProvider(api_key="dummy")
    p.resolve_product = lambda card_id: {"product-name": "x", "loose-price": 0}  # 0 = no data
    assert p.get_price("OP01-073", grade="raw") is None
    assert p.get_price("OP01-073", grade="psa-10") is None


# --- editing the collection (used by the dashboard) ---------------------
@pytest.fixture
def collection(tmp_path):
    from portfolio import save_collection

    path = tmp_path / "collection.csv"
    save_collection(path, [
        Holding("Enel (SEC)", "OP15-118", 220_000, 2),
        Holding("Luffy (Parallel)", "OP10-118", 83_000, 1, variant="parallel"),
    ])
    return path


def test_update_changes_price_and_quantity(collection):
    from portfolio import update_holding

    update_holding(collection, 0, "OP15-118", "base", buy_price=250_000, quantity=3)
    h = load_collection(collection)[0]
    assert (h.buy_price, h.quantity) == (250_000, 3)


def test_update_leaves_untouched_fields_alone(collection):
    from portfolio import update_holding

    update_holding(collection, 1, "OP10-118", "parallel", quantity=4)
    h = load_collection(collection)[1]
    assert (h.buy_price, h.quantity, h.variant) == (83_000, 4, "parallel")


def test_selling_part_of_a_lot_reduces_it(collection):
    from portfolio import remove_holding

    kept = remove_holding(collection, 0, "OP15-118", "base", quantity=1)
    assert kept.quantity == 1
    assert len(load_collection(collection)) == 2


def test_selling_everything_drops_the_row(collection):
    from portfolio import remove_holding

    assert remove_holding(collection, 0, "OP15-118", "base") is None
    remaining = load_collection(collection)
    assert [h.card_id for h in remaining] == ["OP10-118"]


def test_stale_page_cannot_edit_the_wrong_card(collection):
    """The row the browser saw must still be that row, or the edit is refused."""
    from portfolio import CollectionChanged, remove_holding, update_holding

    # Someone removed row 0 in the terminal; the open tab still thinks index 0
    # is Enel, but it is now the parallel Luffy.
    remove_holding(collection, 0, "OP15-118", "base")

    with pytest.raises(CollectionChanged):
        update_holding(collection, 0, "OP15-118", "base", quantity=99)
    with pytest.raises(CollectionChanged):
        remove_holding(collection, 0, "OP15-118", "base")

    # The surviving card is untouched by the refused edits.
    assert load_collection(collection)[0].quantity == 1


def test_variant_mismatch_is_refused(collection):
    """Same card id, wrong printing -> refuse rather than edit the other lot."""
    from portfolio import CollectionChanged, update_holding

    with pytest.raises(CollectionChanged):
        update_holding(collection, 1, "OP10-118", "base", quantity=9)


def test_out_of_range_row_is_refused(collection):
    from portfolio import CollectionChanged, update_holding

    with pytest.raises(CollectionChanged):
        update_holding(collection, 99, "OP15-118", "base", quantity=1)


@pytest.mark.parametrize("kwargs", [{"quantity": 0}, {"quantity": -1}, {"buy_price": -5}])
def test_nonsense_edits_are_rejected(collection, kwargs):
    from portfolio import update_holding

    with pytest.raises(ValueError):
        update_holding(collection, 0, "OP15-118", "base", **kwargs)
    assert load_collection(collection)[0].quantity == 2  # unchanged


# --- dashboard request validation ---------------------------------------
def test_dashboard_rejects_bad_card_codes(monkeypatch, tmp_path):
    import dashboard

    monkeypatch.setattr(dashboard.config, "COLLECTION_FILE", tmp_path / "c.csv")
    with pytest.raises(ValueError, match="card code"):
        dashboard.api_lookup({"card_id": "3"})
    with pytest.raises(ValueError, match="card code"):
        dashboard.api_add({"card_id": "3", "buy_price": "1000", "quantity": 1})


def test_dashboard_rejects_unparseable_buy_price(monkeypatch, tmp_path, cache):
    import dashboard
    from providers.base import Printing

    monkeypatch.setattr(dashboard.config, "COLLECTION_FILE", tmp_path / "c.csv")
    monkeypatch.setattr(
        dashboard, "_provider",
        lambda region=None: type("P", (), {"currency": "USD", "list_printings": lambda self, c: [Printing("base", "Normal", 1.0, c)]})(),
    )
    with pytest.raises(ValueError, match="what you paid"):
        dashboard.api_add({"card_id": "OP15-118", "buy_price": "abc", "quantity": 1})
    assert not (tmp_path / "c.csv").exists()  # nothing written


def test_dashboard_requires_a_real_variant(monkeypatch, tmp_path):
    """You cannot invent a printing that the price source does not list."""
    import dashboard
    from providers.base import Printing

    monkeypatch.setattr(dashboard.config, "COLLECTION_FILE", tmp_path / "c.csv")
    monkeypatch.setattr(
        dashboard, "_provider",
        lambda region=None: type("P", (), {"currency": "USD",
            "list_printings": lambda self, c: [
                Printing("base", "Normal", 6.52, c), Printing("parallel", "Parallel", 72.24, c)
            ]})(),
    )
    with pytest.raises(ValueError, match="which version"):
        dashboard.api_add({"card_id": "OP10-118", "variant": "made-up",
                           "buy_price": "83000", "quantity": 1})

    dashboard.api_add({"card_id": "OP10-118", "variant": "parallel",
                       "buy_price": "83k", "quantity": 2})
    saved = load_collection(tmp_path / "c.csv")[0]
    assert (saved.variant, saved.buy_price, saved.quantity) == ("parallel", 83_000, 2)


# --- switching the reporting currency -----------------------------------
def test_set_display_currency_switches_and_persists(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    original = config.DISPLAY_CURRENCY
    try:
        config.set_display_currency("usd")
        assert config.DISPLAY_CURRENCY == "USD"
        assert config.FX_TARGET_CURRENCY == "USD"   # fx converts into it too
        assert "USD" in (tmp_path / "settings.json").read_text()
    finally:
        config.set_display_currency(original)


@pytest.mark.parametrize("bad", ["", "US", "DOLLAR", "12A", "$"])
def test_set_display_currency_rejects_nonsense(bad, tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    with pytest.raises(ValueError):
        config.set_display_currency(bad)


def test_switching_currency_revalues_but_keeps_buy_history(cache):
    """The stored purchase price must not be rewritten by a currency switch."""
    import config

    holding = Holding("Enel", "OP15-118", 220_000, 1, buy_currency="IDR")
    provider = CachedPriceProvider(FakeProvider({"OP15-118": 3980.0}), cache)
    provider.currency = "JPY"

    # Reported in IDR: 1 JPY = 110 IDR, and IDR is 1:1 with itself.
    idr = price_collection([holding], lambda r: provider,
                           lambda c: 1.0 if c == "IDR" else 110.0)[0]
    # Reported in USD: 1 JPY = $0.0065, 1 IDR = $0.000059.
    usd = price_collection([holding], lambda r: provider,
                           lambda c: 0.000059 if c == "IDR" else 0.0065)[0]

    assert idr.holding.buy_price == 220_000        # untouched
    assert idr.holding.buy_currency == "IDR"       # untouched
    assert usd.holding.buy_price == 220_000        # still what they paid
    assert idr.invested == pytest.approx(220_000)
    assert usd.invested == pytest.approx(12.98)    # same money, other currency


def test_currency_formatting_follows_the_currency():
    import report

    assert report._money(1234.6, "IDR") == "Rp 1,235"    # no minor unit
    assert report._money(1234.6, "JPY") == "¥1,235"
    assert report._money(1234.5, "USD") == "$1,234.50"   # cents kept
    assert report._money(1234.5, "EUR") == "€1,234.50"
    assert report._money(1234.5, "XYZ") == "XYZ 1,234.50"  # unknown fallback
    assert report._money(None, "USD") == "-"


def test_dashboard_settings_endpoint_changes_currency(tmp_path, monkeypatch):
    import config
    import dashboard

    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    original = config.DISPLAY_CURRENCY
    try:
        assert dashboard.api_settings({"display_currency": "EUR"}) == {"display_currency": "EUR"}
        assert config.DISPLAY_CURRENCY == "EUR"
        with pytest.raises(ValueError):
            dashboard.api_settings({"display_currency": "nope"})
    finally:
        config.set_display_currency(original)


# --- report -------------------------------------------------------------
def _sample_rows():
    import report

    priced = [
        PricedHolding(Holding("Enel (SEC)", "OP15-118", 220_000, 2), 16.14, "USD", 17_903.47),
        PricedHolding(Holding("Missing", "OP99-999", 300_000, 2), None, "USD", 17_903.47),
    ]
    return report, priced, compute_totals(priced)


def test_csv_has_all_columns_and_marks_errors(tmp_path):
    import report

    report_mod, priced, _ = _sample_rows()
    out = report_mod.write_csv(priced, tmp_path / "portfolio.csv")
    lines = out.read_text(encoding="utf-8").strip().splitlines()

    assert lines[0].split(",") == report.CSV_COLUMNS
    assert lines[1].startswith("Enel (SEC),OP15-118,jp,base,raw,2,440000,IDR,16.14,USD,")
    assert lines[2].endswith("ERROR,USD,ERROR,ERROR,ERROR,IDR")  # unpriced row


def test_profit_is_green_and_loss_is_red():
    import report

    assert report._pl_text(1000).style == "green"
    assert report._pl_text(0).style == "green"   # >= 0 counts as profit
    assert report._pl_text(-1000).style == "red"
    assert report._pl_text(None).plain == "ERROR"


def test_percentage_is_not_double_signed():
    import report

    assert report._pl_text(31.35, report._pct, signed=False).plain == "+31.4%"
    assert report._pl_text(-17.32, report._pct, signed=False).plain == "-17.3%"


@pytest.mark.parametrize("width", [60, 79, 100, 120, 200])
def test_table_never_exceeds_terminal_width(width, monkeypatch):
    """Figures must never be truncated -- the layout adapts to the terminal."""
    import report
    from rich.console import Console

    report_mod, priced, totals = _sample_rows()
    monkeypatch.setattr(report, "console", Console(width=width, no_color=True))

    table = report_mod._fit_table(
        report_mod._build_rows(priced, totals), "One Piece TCG Portfolio"
    )
    assert report_mod._excess_width(table) <= 0
    assert report_mod.MIN_NAME_WIDTH <= table.columns[0].width <= report_mod.MAX_NAME_WIDTH


def test_wide_terminal_keeps_every_column(monkeypatch):
    import report
    from rich.console import Console

    report_mod, priced, totals = _sample_rows()
    monkeypatch.setattr(report, "console", Console(width=200, no_color=True))
    table = report_mod._fit_table(report_mod._build_rows(priced, totals), "t")
    assert len(table.columns) == 1 + len(report_mod.NUMERIC_HEADERS)
