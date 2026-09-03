# Design brief for Gemini — beautify the site, do NOT touch the logic

You are helping improve the **visual appearance** of the "Grand Line" One Piece
TCG website. Your job is **styling and layout only**. The site is fully working —
data (cards, prices, meta decks, news) loads from a Python backend. **Do not
break that wiring.** If in doubt, change less.

---

## ✅ Your job (visual only)
Make it look more polished, modern and cohesive: colours, spacing, typography,
card layouts, buttons, hover states, headers/footers, empty states, mobile
responsiveness. You may add new CSS and reorganise markup **visually**.

## 🚫 NOT your job
- Do **not** edit any **`.py` file** (e.g. `dashboard.py`, `snapshot_prices.py`,
  `scheduler.py`, anything in `providers/`, the `seed_*.py` / `add_new_set.py`
  scripts). These are the backend/algorithm — off-limits.
- Do **not** change what the site *does* (filters, prices, charts, data). Only
  how it *looks*.

---

## Files you MAY edit (ONLY these)
- `home.html`
- `database.html`
- `market.html`
- `meta.html`
- `news.html`
- `carddetail.js` — **only the CSS** inside its `CSS = \`...\`` block near the top.

Leave everything else alone, including `dashboard.html` (that's a separate app).

## Files you must NEVER touch
- Any `*.py` file, the `providers/` folder, `tracker.db`, `*.json`, `Procfile`,
  `requirements.txt`, `runtime.txt`, and the other `.md` docs.

---

## 🔑 The golden rules (this is how you avoid breaking it)
Each HTML file contains **both** styling *and* the JavaScript that loads the data.
You may restyle freely, but you must **preserve every "hook" the scripts use**:

1. **Do NOT remove or rename** any `id`, `class`, `data-*` attribute, or `on...`
   handler (onclick, onchange, oninput) that already exists. Scripts find
   elements by these. Renaming `id="fmt-pills"` or `class="cardtile"` etc. breaks
   the page. You may **add** new classes; don't remove existing ones.
2. **Do NOT change the `<script>` blocks** or the JavaScript logic — including any
   `fetch('/api/...')` calls, and the field names in the data (`card_id`, `name`,
   `variants`, `price`, `pct`, `image_url`, etc.). Style around them.
3. **Do NOT change the template placeholders** `{{WHOP_URL}}` and `{{TRACKER_URL}}`
   — the server fills these in. Keep them exactly as written.
4. **Keep the class/id names** the shared card popup relies on (`cd-*`, `cdModal`,
   `cdBox`, `cdArtMain`, `cdChart`) — you may restyle them, not rename them.
5. It must stay **responsive** (looks good on phone and desktop) and **not scroll
   sideways** on mobile. Wide tables/rows should scroll inside their own box, not
   the page.

If you want to move an element visually, move the whole tag **with its existing
attributes intact**. Change CSS, not the plumbing.

---

## Current design system (build on this, or evolve it deliberately)
Dark theme. Common tokens already used:
- **Backgrounds:** page `#0f0f0f`, surface/cards `#17171b`, borders/lines `#2a2a30`
- **Text:** primary `#e4e4e7`, muted `#a1a1aa`
- **Accents:** sea-blue `#1799d6`, red `#e74c3c`, gold `#f6b93b`
- **Up/down (Market Watch):** green `#22c55e`, red `#ef4444`
- **Fonts:** headings use `Fredoka`; body uses `Inter` / system UI. (Google Fonts
  is already linked in the pages.)
- **The card popup** (`carddetail.js`) is intentionally a **light cream** panel
  (`#fffaf0`) so it stands out over the dark pages — keep it readable if restyled.

Keep the five pages visually consistent with each other (shared header nav,
footer with the Bandai disclaimer + affiliate note, and the gold "★ Get the
Tracker" CTA).

## Nice areas to improve (suggestions, optional)
- The top nav / header (make it cleaner, consistent across all pages).
- Card grid tiles and the card popup polish.
- Market Watch cards/table styling and the gainer/loser colours.
- Empty/loading states ("Collecting price data…", "Loading…").
- Homepage hero and the feature cards.

---

## How to test (please do this)
After editing, open each page and check it still **loads data** and works:
- `/` `/database` `/market` `/meta` `/news`
- Click a card → the popup opens with image, prices, chart, buy buttons.
- On `/meta`: the format pills, filters, and a deck opening still work.
- On `/market`: the Gainers/Losers, %/$ and Cards/Table toggles still work.
- Check on a **narrow phone width** — no sideways page scroll.

If any data stops showing, you changed a hook you shouldn't have — revert that
part.

## Handing it back
Return the edited files (or a diff) of only the allowed files above. Someone will
review and deploy. Do not attempt to deploy or run the backend yourself.
