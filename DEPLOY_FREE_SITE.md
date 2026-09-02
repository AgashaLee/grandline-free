# Deploy the Grand Line FREE site to Railway

This deploys the **free public site** (`/`, `/database`, `/market`, `/meta`,
`/news`) as its **own** Railway service — separate from the paid tracker at
optcg-app.up.railway.app. Do the account steps in **your** GitHub + Railway.

## What's already prepped (done in code — no action needed)
- **Public web binding**: the server listens on `0.0.0.0:$PORT` when Railway
  sets `$PORT` (`dashboard.serve()`), private locally.
- **DB persistence**: `database.py` reads `DB_PATH` from the env. Point it at a
  mounted volume and the catalog/meta/**price history** survive redeploys. On an
  empty volume it auto-copies the `tracker.db` shipped in the repo (first-boot
  seed), then never overwrites it.
- **Daily price snapshot runs itself**: `scheduler.py` starts an in-process
  daily-jobs thread when hosted (`$PORT` set). Once a day it runs
  `snapshot_prices.py` (and, on Mondays, refreshes the meta pipelines). This is
  why **no Railway cron is needed** — a separate cron service couldn't share this
  service's volume. Set env `DAILY_JOBS=0` to disable it.
- **Fully public**: leave all `WHOP_*` env vars UNSET → `auth.WHOP_ENABLED` is
  False → no login gate.
- **No personal data ships**: `tracker.db` was sanitized (collections/users
  emptied) so your collection + buy prices are not published.

## Step 1 — commit + push to a NEW GitHub repo
The current git remote is the *paid* tracker (`AgashaLee/tcg-tracker`) — do NOT
push there. Create a new empty repo (e.g. `grandline-free`), then:

```bash
cd /c/Users/lang_/Documents/Gravity/tcg_tracker
git config user.name  "Your Name"          # if not already set globally
git config user.email "you@example.com"
git checkout -b free-site                   # keep it off the paid 'main'
git add -A
git commit -m "Grand Line free site: home/database/market/meta/news + daily price snapshot"
git remote add freesite https://github.com/<you>/grandline-free.git
git push -u freesite free-site
```
Note: the first push is **~236 MB** (the `assets/` card images + `tracker.db`
seed). That's fine for GitHub; just let it finish.

## Step 2 — create the Railway service
1. Railway → **New Project → Deploy from GitHub repo** → pick `grandline-free`,
   branch `free-site`. It auto-detects Python (requirements.txt + runtime.txt)
   and runs the `Procfile` (`web: python main.py dashboard`).
2. **Settings → Networking → Generate Domain** → gives an
   `https://…up.railway.app` URL.

## Step 3 — add the persistent volume + env vars (IMPORTANT)
1. Railway → service → **Variables**, add:
   ```
   DB_PATH=/data/tracker.db
   TRACKER_URL=https://optcg-app.up.railway.app
   WHOP_STORE_URL=https://whop.com/grand-line-store   # verify this slug!
   PYTHONUNBUFFERED=1                                  # so logs show live
   ```
   Do **NOT** set any `WHOP_CLIENT_ID/SECRET/API_KEY` — those would gate the site.
2. Railway → **Volumes → New Volume**, mount path **`/data`**. Redeploy.

On first boot the app copies the shipped catalog to `/data/tracker.db`; the
daily snapshot then APPENDS to it there, surviving every future redeploy.

## Step 4 — verify
- Open the domain → all 5 pages load.
- `/market` shows "🌱 Collecting price data…" (only 1 seeded day so far).
- **Tomorrow**, after the daily thread has logged a 2nd day, `/market` shows real
  gainers/losers. (To check the job ran: Railway **Logs** → look for
  `[scheduler] running daily jobs`.)

## Later / gotchas
- **Updating the catalog after launch**: the volume DB is not overwritten by a
  redeploy. To refresh cards/meta, run the seed scripts against the volume DB
  (Railway shell, `DB_PATH=/data/tracker.db python seed_cards.py`) or delete the
  volume to re-seed from the repo.
- **`/tracker` on the free service**: with Whop off, this leftover route would
  serve the paid dashboard UI (empty). Harmless, but consider removing it from
  `dashboard.py` do_GET for a clean free-only deploy.
- **Affiliate IDs + AdSense**: still pending (see WEBSITE_PLAN.md) — plug in once
  you have a live domain + signups.
