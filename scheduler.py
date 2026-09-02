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


def _run_snapshot():
    try:
        import snapshot_prices
        snapshot_prices.snapshot()
    except Exception:
        print("[scheduler] snapshot failed:\n" + traceback.format_exc())


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


def _loop():
    # Small startup delay so the web server is serving before we do network I/O.
    time.sleep(20)
    while True:
        try:
            if not _snapshot_done_today():
                print(f"[scheduler] running daily jobs for {_today()}")
                _run_snapshot()
                if _dt.date.today().weekday() == 0:  # Monday
                    _run_meta_refresh()
        except Exception:
            print("[scheduler] loop error:\n" + traceback.format_exc())
        time.sleep(_CHECK_EVERY)


def start() -> None:
    """Launch the daily-jobs thread (idempotent-safe to call once)."""
    t = threading.Thread(target=_loop, name="daily-jobs", daemon=True)
    t.start()
    print("[scheduler] daily-jobs thread started")
