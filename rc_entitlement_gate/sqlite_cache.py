"""Persistent SQLite-backed TTL cache for entitlement results.

Drop-in replacement for TTLCache. Uses wall-clock timestamps (time.time())
so cache entries survive process restarts. Thread-safe via threading.Lock +
SQLite WAL mode.

Usage:
    from rc_entitlement_gate.sqlite_cache import SQLiteCache
    cache = SQLiteCache(db_path="entgate_cache.db", ttl_seconds=60)

Phase 3 feature.

Distributed invalidation (Phase 3b):
    Multiple processes sharing the same SQLite file can use poll_invalidations()
    to discover keys invalidated by peer processes and evict them locally:

        bus = SQLiteCache(db_path="/shared/entgate.db")
        last_sync = time.time()
        # ... later, on a timer or before each check:
        for key in bus.poll_invalidations(since_ts=last_sync):
            local_cache.invalidate(key)
        last_sync = time.time()
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

# Distributed invalidation log — records cache-key invalidations across processes.
# Other processes sharing the same DB can call poll_invalidations(since_ts) to pick
# up keys they should evict from their own (potentially in-memory) caches.
_CREATE_INVAL_TABLE = """
CREATE TABLE IF NOT EXISTS entgate_invalidations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key           TEXT    NOT NULL,
    invalidated_at REAL   NOT NULL
)
"""

_CREATE_INVAL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_inval_at ON entgate_invalidations (invalidated_at)
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
        conn.execute(_CREATE_INVAL_TABLE)
        conn.execute(_CREATE_INVAL_INDEX)
        conn.commit()

    # ------------------------------------------------------------------
    # Public interface (matches TTLCache)
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Return value if within TTL, else None."""
        now = time.time()
        with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT value FROM entgate_cache WHERE key = ? AND expires_at > ?",
                    (key, now),
                )
                .fetchone()
            )
        if row is None:
            return None
        return json.loads(row[0])

    def get_stale(self, key: str) -> Any | None:
        """Return value even if TTL expired, within stale window. Prunes beyond window."""
        now = time.time()
        with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT value, expires_at FROM entgate_cache WHERE key = ?",
                    (key,),
                )
                .fetchone()
            )
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
        """Remove key and log the invalidation for distributed sync. Returns True if it existed."""
        now = time.time()
        with self._lock:
            cursor = self._conn().execute("DELETE FROM entgate_cache WHERE key = ?", (key,))
            # Always log the invalidation — even if not present locally, other processes
            # sharing this DB may have the key cached and need to evict it.
            self._conn().execute(
                "INSERT INTO entgate_invalidations (key, invalidated_at) VALUES (?, ?)",
                (key, now),
            )
            self._conn().commit()
            return cursor.rowcount > 0

    def poll_invalidations(self, since_ts: float) -> list[str]:
        """Return keys invalidated after since_ts (Unix timestamp).

        Use this for distributed invalidation: processes sharing the same SQLite
        file can poll for keys they should evict from their own local caches.

        Example (multi-process):
            bus = SQLiteCache(db_path="/shared/entgate.db")
            last_sync = time.time()
            ...
            keys = bus.poll_invalidations(since_ts=last_sync)
            last_sync = time.time()
            for key in keys:
                local_memory_cache.invalidate(key)
        """
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    "SELECT DISTINCT key FROM entgate_invalidations WHERE invalidated_at > ?",
                    (since_ts,),
                )
                .fetchall()
            )
        return [row[0] for row in rows]

    def prune_invalidation_log(self, older_than_seconds: int = 3600) -> int:
        """Remove old invalidation log entries. Returns count deleted."""
        cutoff = time.time() - older_than_seconds
        with self._lock:
            cursor = self._conn().execute(
                "DELETE FROM entgate_invalidations WHERE invalidated_at < ?",
                (cutoff,),
            )
            self._conn().commit()
        return cursor.rowcount

    def clear(self) -> None:
        with self._lock:
            self._conn().execute("DELETE FROM entgate_cache")
            self._conn().commit()

    def size(self) -> int:
        """Count of non-expired (fresh) entries."""
        now = time.time()
        with self._lock:
            row = (
                self._conn()
                .execute("SELECT COUNT(*) FROM entgate_cache WHERE expires_at > ?", (now,))
                .fetchone()
            )
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
