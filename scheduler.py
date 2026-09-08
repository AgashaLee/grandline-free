"""In-process daily jobs for the hosted free site.

Railway crons run as a *separate* service and cannot share this web service's
volume, so the daily price snapshot (which must write to the same ``tracker.db``
the site reads) runs here instead: a background daemon thread inside the
always-on web process.

Design:
  * The loop wakes hourly and asks "has today's snapshot been written yet?".
    If not, it runs the jobs. This is self-healing -- a redeploy/restart mid-day
    just resumes and still captures today exactly once.
  * ``snapshot_prices`` is idempotent per day (PK = card_id + date), so a double
    run is harmless.
  * Meta pipelines are refreshed weekly (Mondays), best-effort -- they hit
    external sites and must never take the web service down.

Started from ``dashboard.serve()`` only when hosted (``$PORT`` set); a local run
does not spin it up (run the jobs by hand locally).
"""

import datetime as _dt
import threading
import time
import traceback

_CHECK_EVERY = 3600  # seconds between "is today done?" checks


def _today() -> str:
    return _dt.date.today().isoformat()


def _snapshot_done_today() -> bool:
    from database import get_db
    try:
        row = get_db().execute(
            "SELECT 1 FROM price_history WHERE date=? LIMIT 1", (_today(),)
        ).fetchone()
        return row is not None
    except Exception:
        return False  # table missing -> not done


#: A real JP scrape writes ~2,200 rows; anything well below that means today's
#: scrape failed or was throttled, so it should be retried (not marked done).
_JP_MIN_ROWS = 500


def _jp_snapshot_done_today() -> bool:
    from database import get_db
    try:
        n = get_db().execute(
            "SELECT COUNT(*) FROM price_history_jp WHERE date=?", (_today(),)
        ).fetchone()[0]
        return n >= _JP_MIN_ROWS
    except Exception:
        return False  # table missing / error -> not done


def _run_snapshot():
    try:
        import snapshot_prices
        snapshot_prices.snapshot()
    except Exception:
        print("[scheduler] snapshot failed:\n" + traceback.format_exc())


def _run_snapshot_jp():
    """Daily Japan price snapshot (Yuyu-tei, ¥). Best-effort -- scrapes an
    external site, so it must never take the web service down."""
    try:
        import snapshot_prices_jp
        snapshot_prices_jp.snapshot()
    except Exception:
        print("[scheduler] JP snapshot failed:\n" + traceback.format_exc())


def _run_meta_refresh():
    """Best-effort weekly meta refresh (West + JP). Never fatal."""
    for mod in ("seed_meta_limitless", "seed_meta_tcgportal_jp"):
        try:
            m = __import__(mod)
            for fn in ("main", "seed", "run"):
                if hasattr(m, fn):
                    getattr(m, fn)()
                    break
        except Exception:
            print(f"[scheduler] {mod} failed:\n" + traceback.format_exc())


def _run_new_cards():
    """Weekly: add any newly-released cards to the catalog. Safe (additive,
    never wipes) so it's fine unattended -- adds a new set (e.g. EB05) on its own."""
    try:
        import add_new_set
        add_new_set.add_new()
    except Exception:
        print("[scheduler] add_new_set failed:\n" + traceback.format_exc())


def _run_jp_leader_backfill():
    """Fill leader_id on JP decks that lack one (fast, no network, idempotent).
    Runs once at startup so existing rows on the live volume get codes without
    waiting for the weekly re-seed."""
    try:
        import jp_leader_match
        from database import get_db
        n = jp_leader_match.backfill(get_db())
        if n:
            print(f"[scheduler] JP leader backfill filled {n} decks")
    except Exception:
        print("[scheduler] JP leader backfill failed:\n" + traceback.format_exc())


def _loop():
    # Small startup delay so the web server is serving before we do network I/O.
    time.sleep(20)
    _run_jp_leader_backfill()
    while True:
        try:
            if not _snapshot_done_today():
                print(f"[scheduler] running daily jobs for {_today()}")
                _run_snapshot()
                if _dt.date.today().weekday() == 0:  # Monday
                    _run_new_cards()      # add any newly-released set
                    _run_meta_refresh()
        except Exception:
            print("[scheduler] loop error:\n" + traceback.format_exc())
        # Japan (Yuyu-tei ¥) snapshot has its OWN gate: if today's scrape failed
        # or came back partial (< _JP_MIN_ROWS), retry it on the next hourly wake
        # instead of being blocked by the West snapshot's "done today" flag.
        try:
            if not _jp_snapshot_done_today():
                print(f"[scheduler] running JP snapshot for {_today()}")
                _run_snapshot_jp()
        except Exception:
            print("[scheduler] JP loop error:\n" + traceback.format_exc())
        time.sleep(_CHECK_EVERY)


def start() -> None:
    """Launch the daily-jobs thread (idempotent-safe to call once)."""
    t = threading.Thread(target=_loop, name="daily-jobs", daemon=True)
    t.start()
    print("[scheduler] daily-jobs thread started")
