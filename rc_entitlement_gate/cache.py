"""Simple in-memory TTL cache for entitlement results.

Keeps API calls minimal — RC subscriber data is relatively stable.
Default TTL: 60 seconds (configurable). Thread-safe via threading.Lock.

Phase 2: stale_window_seconds allows serving expired entries as a fallback
when the upstream API is unreachable (offline fallback).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    expires_at: float  # monotonic time


class TTLCache:
    """Thread-safe in-memory TTL cache with optional stale-read window.

    Normal reads (get) return None when TTL is exceeded.
    Stale reads (get_stale) return expired entries within stale_window_seconds.
    This enables offline fallback: serve last-known-good when the upstream is down.
    """

    def __init__(self, ttl_seconds: int = 60, stale_window_seconds: int = 0) -> None:
        self.ttl = ttl_seconds
        self.stale_window = stale_window_seconds
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Return value if within TTL, else None. Expired entries are NOT removed
        (they may still be needed for stale reads)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                return None
            return entry.value

    def get_stale(self, key: str) -> Any | None:
        """Return value even if TTL expired, as long as within stale window.
        Returns None if key is missing or beyond stale window. Prunes on access."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            now = time.monotonic()
            # Fresh — serve normally
            if now <= entry.expires_at:
                return entry.value
            # Stale but within stale window
            if self.stale_window > 0 and now <= entry.expires_at + self.stale_window:
                return entry.value
            # Beyond stale window — prune and return None
            del self._store[key]
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = CacheEntry(
                value=value,
                expires_at=time.monotonic() + self.ttl,
            )

    def invalidate(self, key: str) -> bool:
        with self._lock:
            existed = key in self._store
            self._store.pop(key, None)
            return existed

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        """Count of non-expired (fresh) entries."""
        with self._lock:
            now = time.monotonic()
            return sum(1 for e in self._store.values() if e.expires_at > now)

    def _cache_key(self, subscriber_id: str) -> str:
        return f"sub:{subscriber_id}"
