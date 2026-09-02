"""One-shot: strip personal data from tracker.db before it ships as the public
free-site seed. The free site never uses `collections`/`users` (those are
paid-tracker features), so emptying them prevents publishing the owner's
collection + buy prices. A backup is taken in backups/ first.
"""
import shutil
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "tracker.db"
BAK = DB.parent / "backups" / "tracker.db.sanitize.bak"
BAK.parent.mkdir(exist_ok=True)
shutil.copy2(DB, BAK)

c = sqlite3.connect(DB)
before = c.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
c.execute("DELETE FROM collections")
c.execute("DELETE FROM users")
c.commit()
c.execute("VACUUM")
print(f"backup -> {BAK.name}")
print(f"collections: {before} -> {c.execute('SELECT COUNT(*) FROM collections').fetchone()[0]}")
print(f"users:       {c.execute('SELECT COUNT(*) FROM users').fetchone()[0]}")
print(f"kept: cards={c.execute('SELECT COUNT(*) FROM cards').fetchone()[0]} "
      f"meta={c.execute('SELECT COUNT(*) FROM meta_decks').fetchone()[0]} "
      f"price_history={c.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]}")
