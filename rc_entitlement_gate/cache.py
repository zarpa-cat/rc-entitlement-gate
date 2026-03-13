"""Simple in-memory TTL cache for entitlement results.

Keeps API calls minimal — RC subscriber data is relatively stable.
Default TTL: 60 seconds (configurable). Thread-safe via threading.Lock.
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
    """Thread-safe in-memory TTL cache."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl = ttl_seconds
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            return entry.value

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
        with self._lock:
            now = time.monotonic()
            return sum(1 for e in self._store.values() if e.expires_at > now)

    def _cache_key(self, subscriber_id: str) -> str:
        return f"sub:{subscriber_id}"
