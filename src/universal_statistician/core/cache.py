"""Local TTL cache, keyed by normalized query. SQLite-backed so a
file-backed instance survives restarts; in-memory by default."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
)
"""


class Cache:
    def __init__(
        self,
        connection: sqlite3.Connection | None = None,
        time_func: Callable[[], float] = time.time,
    ) -> None:
        # Shared connection + own lock; see Catalog.__init__ for why.
        self._conn = connection or sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        self._time = time_func
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM cache_entries WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None

            value, expires_at = row
            if expires_at <= self._time():
                self._conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                self._conn.commit()
                return None

            return json.loads(value)

    def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache_entries (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), self._time() + ttl_seconds),
            )
            self._conn.commit()

    @staticmethod
    def make_key(*parts: str | None) -> str:
        return "|".join("" if p is None else p for p in parts)
