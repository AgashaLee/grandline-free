"""File-backed JSON cache with a TTL.

Deliberately dependency-free and provider-agnostic: it stores
``{key: {"value": ..., "timestamp": <unix seconds>}}`` and knows nothing about
cards, prices or currencies. Both the price layer and the FX layer use it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config import CACHE_FILE, CACHE_TTL_HOURS


class JsonCache:
    """A small persistent key/value store with per-read TTL checks.

    Reads the file once on construction and writes it back after each ``set``.
    That is plenty for a few hundred cards and keeps the file readable/editable
    by hand, which matters while debugging price data.
    """

    def __init__(self, path: Path | str = CACHE_FILE, ttl_hours: float = CACHE_TTL_HOURS):
        self.path = Path(path)
        self.ttl_seconds = ttl_hours * 3600.0
        self._data: dict[str, dict[str, Any]] = self._load()

    # --- persistence ----------------------------------------------------
    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            # A corrupt cache is never fatal -- we just start fresh.
            return {}

    def _flush(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            tmp.replace(self.path)  # atomic-ish: never leaves a half-written cache
        except OSError:
            # Losing the cache degrades performance, not correctness.
            pass

    # --- api ------------------------------------------------------------
    def get(self, key: str) -> Any | None:
        """Return the cached value if present and younger than the TTL."""
        entry = self._data.get(key)
        if not entry:
            return None
        if self.age_seconds(key) is None or self.age_seconds(key) > self.ttl_seconds:
            return None
        return entry.get("value")

    def get_stale(self, key: str) -> Any | None:
        """Return the cached value regardless of age (used on network failure)."""
        entry = self._data.get(key)
        return entry.get("value") if entry else None

    def set(self, key: str, value: Any) -> None:
        self._data[key] = {"value": value, "timestamp": time.time()}
        self._flush()

    def age_seconds(self, key: str) -> float | None:
        """Age of an entry in seconds, or ``None`` if missing/malformed."""
        entry = self._data.get(key)
        if not entry:
            return None
        ts = entry.get("timestamp")
        if not isinstance(ts, (int, float)):
            return None
        return max(0.0, time.time() - ts)

    def age_hours(self, key: str) -> float | None:
        age = self.age_seconds(key)
        return None if age is None else age / 3600.0
