# Deploying the tracker to Railway

This puts the dashboard on a public HTTPS URL you can open from any phone or
computer. You do these steps in **your own Railway account** — the code is
already prepared for it.

## What's already done for you

- **`Procfile`** — tells Railway to run `python main.py dashboard`.
- **`runtime.txt`** — pins Python 3.12.
- **Public binding** — when Railway sets its `PORT`, the app automatically
  listens on `0.0.0.0` (reachable from the internet). Locally it stays private.
- **Fresh-start friendly** — with no collection yet, it shows "add your first
  card" instead of an error.

## Step 1 — put the code on GitHub

Railway deploys from a GitHub repo. From the tracker folder:

```bash
git remote add origin https://github.com/<you>/tcg-tracker.git
git push -u origin main
```

Your personal `collection.csv` is git-ignored, so it does **not** get uploaded
— the deployed app starts empty and you add cards through the web UI.

## Step 2 — create the Railway project

1. Railway → **New Project → Deploy from GitHub repo** → pick the repo.
2. Railway auto-detects Python, installs `requirements.txt`, and runs the
   `Procfile`. No start command to type.
3. When it's live, **Settings → Networking → Generate Domain**. That URL
   (`https://something.up.railway.app`) is your link — it works on mobile.

## Step 3 (important) — make your cards survive redeploys

Railway's disk is **ephemeral**: every redeploy wipes files, so cards you add
would vanish. Fix it with a **Volume**:

1. Railway → your service → **Variables**, add:
   ```
   COLLECTION_FILE=/data/collection.csv
   CACHE_FILE=/data/cache.json
   SETTINGS_FILE=/data/settings.json
   ```
2. Railway → **Volumes → New Volume**, mount path **`/data`**.

Now your collection lives on the persistent volume and survives redeploys. (The
app already reads these paths from the environment — no code change needed.)

## Step 4 — pick your currency

Open the URL, use the currency dropdown in the header (or set a default with a
`DISPLAY_CURRENCY=USD` variable). The choice is saved to `settings.json` on the
volume.

---

## Read this before you share the link

- **No login.** Anyone with the URL can view *and edit* the collection (add,
  sell, delete). Fine while it's just you — **keep the link private.** Before
  you hand it to testers or customers, it needs per-user accounts / a login
  gate (the Whop step we discussed). Don't post this URL publicly yet.

- **Yuyu-tei from a datacenter IP.** Prices are scraped from Yuyu-tei. Railway
  runs on datacenter IPs, which sites sometimes rate-limit or block. The 24h
  cache keeps requests low, but if JP prices start showing ERROR on Railway
  while they work on your PC, that's the cause — not a bug in the code.

- **First load is slow.** The very first pricing run fetches live data; after
  that the 24h cache makes it instant.
