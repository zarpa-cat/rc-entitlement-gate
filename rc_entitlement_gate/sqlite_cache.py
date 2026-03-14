"""Persistent SQLite-backed TTL cache for entitlement results.

Drop-in replacement for TTLCache. Uses wall-clock timestamps (time.time())
so cache entries survive process restarts. Thread-safe via threading.Lock +
SQLite WAL mode.

Usage:
    from rc_entitlement_gate.sqlite_cache import SQLiteCache
    cache = SQLiteCache(db_path="entgate_cache.db", ttl_seconds=60)

Phase 3 feature.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS entgate_cache (
    key       TEXT    PRIMARY KEY,
    value     TEXT    NOT NULL,
    expires_at REAL   NOT NULL,
    created_at REAL   NOT NULL
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_expires_at ON entgate_cache (expires_at)
"""


class SQLiteCache:
    """Thread-safe, persistent TTL cache backed by SQLite.

    Stores JSON-serialised values. Expired entries are pruned lazily (on
    access and on explicit vacuum()) to avoid background threads.

    stale_window_seconds: if > 0, get_stale() will serve entries that have
    expired but are within this window — enables offline fallback.
    """

    def __init__(
        self,
        db_path: str | Path = "entgate_cache.db",
        ttl_seconds: int = 60,
        stale_window_seconds: int = 0,
    ) -> None:
        self.db_path = Path(db_path)
        self.ttl = ttl_seconds
        self.stale_window = stale_window_seconds
        self._lock = threading.Lock()
        self._local = threading.local()
        # Ensure DB is initialised from the calling thread
        self._init_db()

    # ------------------------------------------------------------------
    # Private: connection management
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        """Per-thread SQLite connection (thread-local, lazy)."""
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)
        conn.commit()

    # ------------------------------------------------------------------
    # Public interface (matches TTLCache)
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Return value if within TTL, else None."""
        now = time.time()
        with self._lock:
            row = self._conn().execute(
                "SELECT value FROM entgate_cache WHERE key = ? AND expires_at > ?",
                (key, now),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def get_stale(self, key: str) -> Any | None:
        """Return value even if TTL expired, within stale window. Prunes beyond window."""
        now = time.time()
        with self._lock:
            row = self._conn().execute(
                "SELECT value, expires_at FROM entgate_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            value_json, expires_at = row
            # Fresh
            if now <= expires_at:
                return json.loads(value_json)
            # Stale but within window
            if self.stale_window > 0 and now <= expires_at + self.stale_window:
                return json.loads(value_json)
            # Beyond stale window — prune
            self._conn().execute("DELETE FROM entgate_cache WHERE key = ?", (key,))
            self._conn().commit()
            return None

    def set(self, key: str, value: Any) -> None:
        now = time.time()
        expires_at = now + self.ttl
        value_json = json.dumps(value)
        with self._lock:
            self._conn().execute(
                """
                INSERT INTO entgate_cache (key, value, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    expires_at = excluded.expires_at,
                    created_at = excluded.created_at
                """,
                (key, value_json, expires_at, now),
            )
            self._conn().commit()

    def invalidate(self, key: str) -> bool:
        """Remove key. Returns True if it existed."""
        with self._lock:
            cursor = self._conn().execute(
                "DELETE FROM entgate_cache WHERE key = ?", (key,)
            )
            self._conn().commit()
            return cursor.rowcount > 0

    def clear(self) -> None:
        with self._lock:
            self._conn().execute("DELETE FROM entgate_cache")
            self._conn().commit()

    def size(self) -> int:
        """Count of non-expired (fresh) entries."""
        now = time.time()
        with self._lock:
            row = self._conn().execute(
                "SELECT COUNT(*) FROM entgate_cache WHERE expires_at > ?", (now,)
            ).fetchone()
        return row[0] if row else 0

    def vacuum(self) -> int:
        """Prune all expired entries. Returns count deleted."""
        now = time.time()
        with self._lock:
            cursor = self._conn().execute(
                "DELETE FROM entgate_cache WHERE expires_at + ? < ?",
                (self.stale_window, now),
            )
            self._conn().commit()
        return cursor.rowcount

    def _cache_key(self, subscriber_id: str) -> str:
        return f"sub:{subscriber_id}"

    def close(self) -> None:
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn
