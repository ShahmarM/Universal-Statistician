"""Local TTL cache, keyed by normalized query.

Backed by SQLite rather than an in-process dict so a future file-backed
instance survives process restarts without extra code — for MVP (personal,
local tool) the default is in-memory, matching Catalog. No Redis: per the
plan, that's only worth the operational cost once there's real
multi-user load.
"""

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
        # See Catalog's __init__ for why: MCP tool calls run in a worker
        # thread, not the thread that built this engine, and a fresh
        # connection per thread would each see an empty separate
        # ":memory:" database, so the single connection must be shared and
        # explicitly locked instead of relying on sqlite3's default
        # same-thread guard.
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
