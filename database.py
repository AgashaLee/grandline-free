"""Database setup and connection management."""

import os
import shutil
import sqlite3
import threading
from pathlib import Path
import config

#: The catalog/meta/price-history DB. On Railway we point ``DB_PATH`` at a
#: mounted volume (e.g. ``/data/tracker.db``) so the data -- including the daily
#: ``price_history`` snapshots -- survives redeploys (the container disk is
#: ephemeral). Locally the env var is unset and it stays next to the code.
_BUNDLED_DB = config.BASE_DIR / "tracker.db"
_env_db = os.getenv("DB_PATH")
DB_PATH = Path(_env_db) if _env_db else _BUNDLED_DB

# First boot on an empty volume: seed it with the catalog/meta shipped in the
# repo, so the site has cards immediately. Once the volume DB exists we never
# overwrite it (that would wipe accumulated price history) -- refresh the
# catalog by re-running the seed scripts against the volume instead.
if _env_db and not DB_PATH.exists() and _BUNDLED_DB.exists():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_BUNDLED_DB, DB_PATH)

_local = threading.local()

def get_db() -> sqlite3.Connection:
    if not hasattr(_local, "db"):
        _local.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.db.row_factory = sqlite3.Row
    return _local.db

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT,
        display_currency TEXT DEFAULT 'USD'
    );
    
    CREATE TABLE IF NOT EXISTS cards (
        card_id TEXT PRIMARY KEY,   -- e.g. OP01-077
        name TEXT,
        set_id TEXT,                -- e.g. OP-01
        set_name TEXT,              -- e.g. Romance Dawn
        rarity TEXT,                -- C, UC, R, SR, SEC, L, SP...
        card_type TEXT,             -- Leader, Character, Event, Stage
        card_color TEXT,
        card_cost TEXT,
        card_power TEXT,
        card_text TEXT,
        region TEXT,                -- 'en'
        image_url TEXT,
        market_price REAL
    );
    
    CREATE TABLE IF NOT EXISTS collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        card_id TEXT NOT NULL,
        name TEXT,
        region TEXT,
        variant TEXT,
        grade TEXT,
        condition TEXT,
        buy_price REAL,
        buy_currency TEXT,
        quantity INTEGER
    );
    
    CREATE TABLE IF NOT EXISTS meta_decks (
        id TEXT PRIMARY KEY,
        event_date TEXT,
        country TEXT,
        event_name TEXT,
        event_type TEXT,
        players TEXT,
        winner TEXT,
        leader_id TEXT
    );
    
    CREATE TABLE IF NOT EXISTS meta_deck_cards (
        deck_id TEXT,
        card_id TEXT,
        quantity INTEGER,
        PRIMARY KEY(deck_id, card_id)
    );
    """)
    # Non-destructive migrations: add columns introduced after the initial schema.
    for ddl in ["ALTER TABLE meta_decks ADD COLUMN leader_image TEXT"]:
        try:
            db.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    db.commit()

# Initialize DB on first import
init_db()
