# GrandLine free website — build state & plan

Handoff note so a new chat can continue without losing context.

## ⭐ LATEST SNAPSHOT (2026-09-02) — read this first
The free site now has 5 pages, all **dark-themed & consistent**: `/` (home), `/database`,
`/market`, `/meta`, `/news`. Run locally: `python main.py dashboard` → http://127.0.0.1:8802 .
Everything below is DONE & tested locally; the free site is **NOT deployed yet**.

- **Market Watch** (`/market`, `market.html`, `api_market`): biggest price **gainers/losers**
  as **% change only** (raw $ stays a paid-tracker feature — the upsell). Two panels, 24h/7d/30d
  window toggle, rows click into the shared `carddetail.js` modal. Fed by a new **daily snapshot
  job** `snapshot_prices.py`: additive (never DROPs), APPENDs one row/card/day into a new
  `price_history` table AND refreshes `cards.market_price` in place — safe to run daily even while
  `allPromoCards` is 404. **First snapshot already written (2026-09-02, 2,711 cards).** The page
  shows a "collecting data" state until ≥2 snapshot days exist (no historical backfill — the API
  only gives today's price), so the movers fill in from the 2nd daily run onward. A $0.25 price
  floor keeps penny-card noise out of the movers. Same `price_history` log also powers future
  portfolio history/charts in the paid tracker. **Needs a daily Railway cron on `snapshot_prices.py`
  once deployed.** onepiecetopdecks re-checked 2026-09-02: **still host-level 403** (nginx block
  page, not a WAF challenge) — only a different egress IP would help; not needed (Limitless+Cardrush
  cover meta).

- **Card Database** (`/database`): dark theme; set→card grid→click-to-zoom modal; **search
  (code/name, press Enter/Search) + filters** (Color/Type/Cost/Power/Rarity friendly names,
  "Promo" merges P/PR/blank); prices hidden; alt-art thumbnails in zoom.
- **Card detail modal is a shared module** `carddetail.js` (used by /database AND /meta).
  Buy buttons **auto-detect region** (browser timezone/lang): 🇮🇩 ID→Shopee/Tokopedia,
  🌍 else→TCGplayer/eBay, with a manual switch (localStorage `gl_region`). **All 4 affiliate
  IDs are placeholders** in `carddetail.js` — plug in after signup.
- **News** (`/news`): auto-pulls OP TCG headlines via **Google News RSS** (`api_news`, cached
  30min), category icon+gradient per card, + your own posts from editable `own_posts.json`.
- **Meta** (`/meta`): **two live pipelines** feeding meta_decks (re-runnable):
  `seed_meta_limitless.py` = 🌍 West tournaments (Limitless, country="West"), and
  `seed_meta_cardrush_jp.py` = 🇯🇵 JP Flagship Battle winners (Cardrush, country="JP", no
  player names — JP posts don't credit them). Country filter JP/West; meta-share chart.
- **Box covers** repainted to dark bg (were white/cream); **all card fields** (attribute/
  counter/sub_types/life) added; **112 promos** topped up (assets/cards/); alt-arts
  (assets/alt/, 1180 arts).
- Price shown on site = **$1.99/mo** (also update the real price on Whop).

### PAID TRACKER (separate repo AgashaLee/tcg-tracker, deploys Railway→Whop)
- **Deck Builder was BUILT + PUSHED LIVE** this session (commits on main): paste decklist →
  missing cards + Yuyu-tei cost estimate + Shopee/Tokopedia buttons. Parallel pricing +
  thread-safe cache (cache.py) so big decks don't time out. Also pushed an **iOS mobile fix**
  (text-size-adjust + overflow-x). Built in a clean clone, not the free-site folder.

### ✅ DEPLOYED (2026-09-02) — the free site is LIVE
- **Live URL**: https://grandline.up.railway.app (Railway project `faithful-passion`,
  service `web`, GitHub repo `AgashaLee/grandline-free` branch `main`). All 5 pages verified live.
- **Volume `web-volume` mounted at `/data`**, `DB_PATH=/data/tracker.db` → catalog seeded onto it,
  price_history persists across redeploys. Day 1 (2026-09-02) snapshot confirmed on the volume.
- **Build fix**: Railway's `mise` builder failed verifying Python attestations → fixed with env var
  `MISE_PYTHON_GITHUB_ATTESTATIONS=false`. Other env vars set: DB_PATH, TRACKER_URL, WHOP_STORE_URL,
  PYTHONUNBUFFERED=1. No WHOP_* creds set → fully public.
- **Daily jobs run in-process** (`scheduler.py`) — Market Watch fills in from the 2nd day (2026-09-03).
- Repo has the paid tracker's git history tagged along (harmless); `git remote freesite` points at it.

### PENDING / NEXT
1. ~~Deploy free site to Railway~~ ✅ DONE (above). Original prep notes: see
   **`DEPLOY_FREE_SITE.md`** for the exact runbook. Done in code: `DB_PATH` env
   override + first-boot volume seed (`database.py`), in-process daily-jobs thread
   (`scheduler.py`, runs `snapshot_prices.py` daily + meta on Mondays — no Railway
   cron needed, avoids the volume-sharing problem), Whop stays OFF when its env
   vars are unset (fully public), and `tracker.db` was **sanitized** (collections/
   users emptied via `sanitize_for_deploy.py`, backups in `backups/`) so no personal
   data ships. REMAINING (user, needs GitHub+Railway accounts): push to a NEW repo
   (NOT the paid `AgashaLee/tcg-tracker` remote), create the service, add a `/data`
   volume + `DB_PATH=/data/tracker.db`, generate a domain. First push is ~236 MB
   (assets + db seed).
2. **Schedule daily jobs** on Railway once deployed: the meta pipelines AND `snapshot_prices.py`
   (the latter is what makes Market Watch fill in — it needs to run every day).
3. **Affiliate IDs**: Shopee+Tokopedia (Involve Asia), TCGplayer (Impact), eBay (EPN) →
   set in `carddetail.js`; and the tracker's buy links.
4. **Buy domain** (grandline.gg) + AdSense (needs live domain).
6. **JP meta source is now tcg-portal.jp** (2026-09-02) — REPLACED Cardrush. `seed_meta_tcgportal_jp.py`
   pulls ~60 recent JP tournament decks from its clean JSON API (`/api/onepiece/tournament-results`
   + `/api/onepiece/cards` for the internal-id→code map, cached in `tcgportal_cardmap.json`).
   Unblocked from any IP. Gives full 50-card lists + JP archetype (deckGuide.name, kept Japanese) +
   placement + tournament; leader_id blank (source stores leader only as an archetype label). 99%
   of card codes match our catalog. The seeder deletes old `cardrush-%`/`tcgportal-%` rows so JP is
   one consistent set (West/limitless untouched). Scheduler now runs limitless + tcgportal (was
   cardrush). To refresh the LIVE volume DB immediately: run `python seed_meta_tcgportal_jp.py` in
   Railway Console (weekly scheduler maintains it after).
5. onepiecetopdecks now **IP-blocks us from BOTH local AND Railway** (host-firewall 403 confirmed
   2026-09-02 via `scrape_topdecks_jp.py` run in Railway Console — the old "works from Railway's
   IP" note is stale; cloud IP ranges are blocked too). Do NOT keep retrying / don't bypass the
   block. JP meta already covered by Cardrush (`seed_meta_cardrush_jp.py`). `scrape_topdecks_jp.py`
   is left as a safe no-op (self-aborts on 403) in case their firewall ever loosens.


## The business model (decided)
Freemium funnel: a **FREE public website** (card database, meta decks, news) earns
**ads + affiliate**, and funnels visitors to the **PAID tracker on Whop** ($3.99/mo).
Modeled on gumgum.gg (data/market-watch) + onepiecetopdecks.com (content), which are
free/ad-supported. We keep the paid tracker as the premium tier.
**Decision: keep the free site SEPARATE from the paid Railway tracker** (faster, lower risk).

- Brand: **Grand Line**. Domain to buy: **grandline.gg** (or grandlinetcg.com / grandline.cards).
- This folder (`Documents/Gravity/tcg_tracker`) is the Gemini-started fork = the FREE-SITE codebase
  (SQLite `tracker.db`, pages `/database` `/meta`, run: `python main.py dashboard`).
  It has DIVERGED from the live Railway tracker (CSV + Whop gate + buyback/currency features).

## Done ✅
- **Card Database** (`/database`, `database.html`, `api_database`): full catalog **2,711 cards /
  60 sets** seeded from OPTCGAPI bulk endpoints via `seed_cards.py` (base print kept canonical).
  Grid shows image + price + rarity/type + a TCGplayer affiliate Buy button. Sorted OP01→OP18,
  then EB/PRB/ST/P. Set tiles use real booster pack art: **59/60** downloaded from
  onepiecetopdecks.com via `scrape_boxes.py` (OP11 404s → card-image fallback).
- **Meta Decks** (`/meta`): 30 tournament decks + 488 deck-cards seeded (via `providers/topdecks.py`,
  Limitless fallback in `providers/limitless.py`). NOTE: `seed_meta.py` currently reads a one-time
  cache file — not yet a repeatable pipeline.
- **Homepage + Whop funnel** (`home.html`, served at `/`): dark Grand Line landing page —
  hero with live counters (reads `/api/database` + `/api/meta`, currently 2,711 / 60 / 30),
  three feature cards, a $3.99/mo upsell block, and the Bandai disclaimer. Every free page now
  carries a gold **⭐ Get the Tracker** nav button + a footer CTA/disclaimer strip.
  - **Routing changed**: `/` = free homepage, the paid tracker moved to **`/tracker`**, and the
    Whop gate now applies only to non-free paths (`PUBLIC_PAGES` / `PUBLIC_API` in `dashboard.py`).
    `/api/database` and `/api/meta` answer without a session; `/api/data` + all write APIs stay gated.
    Post-login redirect goes to `/tracker`.
  - Funnel links live in one place: `TRACKER_URL` / `WHOP_STORE_URL` in `dashboard.py`
    (env-overridable), injected into pages as `{{TRACKER_URL}}` / `{{WHOP_URL}}` by `_page()`.
    Defaults: `https://optcg-app.up.railway.app` and `https://whop.com/grand-line-store`
    — **verify the Whop store slug is right before launch.**
- **Database UX reworked for the Indonesian market** (`database.html`): set tile → card grid
  (image + name + Buy, **no prices**) → **click any card to zoom** into a detail modal
  (big image, chips for rarity/type/color/cost/power, effect text, two Buy buttons).
  - Buy buttons open a **marketplace SEARCH** wrapped in an affiliate link, for **Shopee**
    (`shopee.co.id/search?keyword=`) and **Tokopedia** (`tokopedia.com/search?q=`). Query =
    `one piece card <code> <name>`. Search-page landings still earn (click-based attribution).
  - Affiliate config is a placeholder block atop `database.html`'s script:
    `AFFILIATE = {shopee:{id,on}, tokopedia:{id,on}}`. Buttons link direct until you set
    `id` + `on:true`; the `affiliate()` wrapper defaults to an Involve Asia deep-link shape
    (`invol.co/aff_m?...&url=<dest>`) — update to your real program format after signup.
  - Backend: `api_database` now returns `card_text` and **no `market_price`** (prices hidden on
    the free site; they stay a paid-tracker feature). The free-page TCGplayer buy-url was removed.
  - Detail view mirrors the printed card: effect text is formatted (`formatCardText()`) so keyword
    tags become badges — **[Blocker]/[Rush]/[Double Attack]/[Banish] orange**, [Trigger] gold,
    [Counter] red, timing keywords ([On Play] etc.) blue — and the Trigger clause breaks onto its
    own line. Also shows **Attribute, Counter (+N), Life, and Traits** chips/line.
  - New card fields **attribute / counter / sub_types / life** were added via a NON-destructive
    migration (ALTER + backfill from `allSetCards`+`allSTCards`) — all 2,711 rows kept, incl. the
    32 promos (optcgapi's `allPromoCards` endpoint is currently **404**, so a full `seed_cards.py`
    re-run right now would DROP the promos — avoid until that endpoint is back). `seed_cards.py`
    was updated to capture these four fields on future full re-seeds.
- **Promo coverage topped up from onepiecetopdecks** (`seed_promos_topdecks.py`): optcgapi's promo
  endpoint is down, so we scraped the promo image gallery and **added 112 missing promos** (32 → 144;
  catalog 2,711 → 2,823). These carry **card_id + name + image only** (that page has no gameplay
  fields), so their zoom shows the card image (details are printed on the card) + Buy buttons, with
  blank detail chips. Images downloaded to `assets/cards/<code>.jpg` (served at `/assets/cards/…`);
  many are cleaner than optcgapi's SAMPLE-watermarked scans. Safe/additive (`INSERT OR IGNORE`), so
  the original 32 promos kept their optcgapi data. **When `allPromoCards` returns, run
  `recover_promos_optcgapi.py`** (NOT a full `seed_cards.py` rebuild): it fills the missing
  gameplay fields on promos we already have and adds any optcgapi-only ones, keyed on `card_id`
  so **no duplicates and no lost coverage** (keeps the cleaner scraped images). It self-guards —
  refuses to run while the endpoint is 404 — so it's safe to try anytime. onepiecetopdecks is a
  stopgap, not the source.
  (OP18/EB05 card lists on that site are JS-rendered tables, not an extractable gallery — skipped.)
- **Event/bonus ALTERNATE ART** (`seed_alt_arts.py`, new table `card_alt_arts`): the 2023 +
  2024/2025 event galleries are alt-art of cards we already own (same codes, 0 new cards). Chose
  "Way 2" — grid stays **one tile per card**; the zoom shows a **thumbnail strip** (base art first,
  then event/bonus arts) and clicking a thumb swaps the big image. Captured **310 arts across 236
  cards**, images downloaded to `assets/alt/` (served at `/assets/alt/…`). `api_database` attaches
  an `alt_arts` list per card (table-absent-safe). Cards with a single art show no strip.
  Re-runnable/idempotent. (Note: Windows' case-insensitive FS can collapse two source names that
  differ only in case — ~1 in 310, harmless.)
- **Set-page ALTERNATE ART** (`seed_set_alt_arts.py`): extended the same alt-art system to the
  regular OP/EB/PRB/ST set pages (parallels, SP, manga rare, **leader alts** like OP11-001). Adds
  only the suffixed variants (base art already shown from optcgapi). Added **870 arts** → now
  **1,180 alt-arts across 782 cards**, images in `assets/alt/`. Reuses `card_alt_arts` + the zoom
  thumbnail strip — no frontend change beyond the note wording ("includes alternate art"). A few
  files keep a `.png` name but hold JPEG bytes (served as image/jpeg — browsers sniff, renders fine).
  OP15/OP18/EB05 set pages are partially JS-rendered so their alt coverage is thin.
- **Search + filters on `/database`** (`database.html`): a bar above the grid — text search (matches card **code or name**, e.g. OP17-001 / Luffy) plus dropdowns for **Color / Type / Cost / Power / Rarity** (options built live from the data). Any active filter shows a flat results grid across ALL cards; clearing returns to the set-tile browse. All client-side over `globalDataCards`. Card tile markup factored into `cardTileHTML()`.
- **Card detail modal is now shared** (`carddetail.js`, served at `/carddetail.js`, public): the
  zoom modal (image + alt-art thumbnails, chips, formatted effect text, Shopee/Tokopedia buy
  buttons) was extracted out of `database.html` into one self-styled module used by BOTH
  `/database` and `/meta`. **The affiliate config now lives in ONE place** (top of `carddetail.js`)
  — edit it once after signup. `database.html` keeps only `openCard(id)=CardDetail.open(...)`.
- **Meta-share chart on `/meta`** (`metaShareChart()`): full-width bar chart of decks per leader/archetype (from `event_name`), below the 3 stat boxes — replaced the small 4th hint box (a narrow box can't show ~13 legible bars). Note: current meta = 30 decks from a ONE-TIME cached OP17-JP scrape (`seed_meta.py`), so the chart is small; a repeatable re-scrape of onepiecetopdecks deck-list pages (plan item: meta pipeline) would enrich it.
- **Meta deck cards now clickable** (`meta.html`): the page loads the card catalog in the
  background (`cardsById`) and each card in a decklist opens the same detail modal on click.
  (The data-source badge that briefly replaced the 📊 emoji was later removed entirely — see below.)
- **Removed the data-source attribution** on `/meta` (the onepiecetopdecks + Limitless links) so
  visitors aren't funnelled to those sites; the 4th stat card now just hints "Click any row...".
- **Star icons made visible**: the gold Get-the-Tracker buttons used the ⭐ emoji (fixed yellow →
  invisible on gold). Swapped for the `★` glyph, which inherits the button's dark text colour.


## To do (priority order)
1. **Plug in Shopee + Tokopedia affiliate IDs** (user signing up after the site is ready) — in
   `database.html` set `AFFILIATE.shopee.id` / `AFFILIATE.tokopedia.id` and `on:true`, and update
   the `affiliate()` wrapper to the real deep-link format (Involve Asia or direct). ← waiting on signup.
   (Free site now targets ID marketplaces; the free-page TCGplayer buy-url was dropped. The paid
   tracker's `providers/optcg.py get_buy_url` still uses TCGplayer — left unchanged.)
2. **Market Watch (price movers)** — ✅ BUILT (`/market` + `snapshot_prices.py` + `price_history`).
   Remaining: schedule `snapshot_prices.py` as a **daily Railway cron** once deployed so history
   accumulates (page is live but shows "collecting data" until ≥2 days exist). Same log will feed
   portfolio history/charts in the paid tracker later.
3. **Meta pipeline**: switch to a repeatable source (Limitless TCG) + schedule weekly.
4. **Ads** on the FREE pages only (Google AdSense — needs the live domain first). NEVER on the paid tracker.
5. **Buy domain + deploy** the free site (Railway), point the domain.

## Watch-outs
- Ads only on free pages; the paid tracker stays clean.
- Bandai disclaimer ("not endorsed by Bandai Namco…") needed once card images/data are public.
- PriceCharting API ($4.99/mo Premium) = graded EN prices (drop-in via `providers/pricecharting.py`);
  it has NO historic data, so price history still needs our own snapshots.
