"""Tests for SQLiteCache — persistent TTL cache."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rc_entitlement_gate.sqlite_cache import SQLiteCache


@pytest.fixture()
def tmp_cache(tmp_path: Path) -> SQLiteCache:
    return SQLiteCache(db_path=tmp_path / "test_cache.db", ttl_seconds=2, stale_window_seconds=5)


@pytest.fixture()
def tmp_cache_no_stale(tmp_path: Path) -> SQLiteCache:
    return SQLiteCache(
        db_path=tmp_path / "test_cache_no_stale.db", ttl_seconds=2, stale_window_seconds=0
    )


# ------------------------------------------------------------------
# Basic get/set
# ------------------------------------------------------------------


def test_set_and_get(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("key1", {"foo": "bar"})
    result = tmp_cache.get("key1")
    assert result == {"foo": "bar"}


def test_get_missing_key(tmp_cache: SQLiteCache) -> None:
    assert tmp_cache.get("nonexistent") is None


def test_get_expired_returns_none(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("expkey", "value")
    time.sleep(2.1)
    assert tmp_cache.get("expkey") is None


def test_set_overwrites(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("k", "v1")
    tmp_cache.set("k", "v2")
    assert tmp_cache.get("k") == "v2"


# ------------------------------------------------------------------
# Stale reads
# ------------------------------------------------------------------


def test_get_stale_returns_fresh_normally(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("k", "v")
    assert tmp_cache.get_stale("k") == "v"


def test_get_stale_serves_within_window(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("k", "stale_val")
    time.sleep(2.1)  # beyond TTL but within stale_window (5s)
    assert tmp_cache.get("k") is None  # normal get: expired
    assert tmp_cache.get_stale("k") == "stale_val"  # stale get: still served


def test_get_stale_returns_none_beyond_window(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("k", "v")
    time.sleep(7.2)  # beyond TTL + stale_window
    assert tmp_cache.get_stale("k") is None


def test_get_stale_no_stale_window(tmp_cache_no_stale: SQLiteCache) -> None:
    tmp_cache_no_stale.set("k", "v")
    time.sleep(2.1)
    assert tmp_cache_no_stale.get_stale("k") is None


# ------------------------------------------------------------------
# Invalidate
# ------------------------------------------------------------------


def test_invalidate_existing(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("k", "v")
    existed = tmp_cache.invalidate("k")
    assert existed is True
    assert tmp_cache.get("k") is None


def test_invalidate_missing(tmp_cache: SQLiteCache) -> None:
    assert tmp_cache.invalidate("ghost") is False


# ------------------------------------------------------------------
# Size and clear
# ------------------------------------------------------------------


def test_size_counts_fresh_only(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("k1", "v1")
    tmp_cache.set("k2", "v2")
    assert tmp_cache.size() == 2
    time.sleep(2.1)
    assert tmp_cache.size() == 0


def test_clear(tmp_cache: SQLiteCache) -> None:
    tmp_cache.set("k1", "v1")
    tmp_cache.set("k2", "v2")
    tmp_cache.clear()
    assert tmp_cache.size() == 0
    assert tmp_cache.get("k1") is None


# ------------------------------------------------------------------
# Vacuum
# ------------------------------------------------------------------


def test_vacuum_prunes_expired(tmp_cache: SQLiteCache) -> None:
    # ttl=2, stale_window=5 → entries pruneable after 7s from creation
    tmp_cache.set("k1", "v1")
    tmp_cache.set("k2", "v2")
    time.sleep(7.2)
    pruned = tmp_cache.vacuum()
    assert pruned == 2


# ------------------------------------------------------------------
# Persistence across instances
# ------------------------------------------------------------------


def test_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "persist.db"
    c1 = SQLiteCache(db_path=db, ttl_seconds=10)
    c1.set("durable", "hello")
    c1.close()

    c2 = SQLiteCache(db_path=db, ttl_seconds=10)
    assert c2.get("durable") == "hello"
    c2.close()


# ------------------------------------------------------------------
# cache_key helper
# ------------------------------------------------------------------


def test_cache_key(tmp_cache: SQLiteCache) -> None:
    key = tmp_cache._cache_key("user_42")
    assert key == "sub:user_42"


# ------------------------------------------------------------------
# Complex values (dicts, lists)
# ------------------------------------------------------------------


def test_stores_nested_dict(tmp_cache: SQLiteCache) -> None:
    data = {"subscriber": {"entitlements": {"premium": {"expires_date": "2027-01-01T00:00:00Z"}}}}
    tmp_cache.set("sub:user1", data)
    result = tmp_cache.get("sub:user1")
    assert result == data


# ------------------------------------------------------------------
# Distributed invalidation
# ------------------------------------------------------------------


def test_poll_invalidations_empty(tmp_cache: SQLiteCache) -> None:
    since = time.time()
    assert tmp_cache.poll_invalidations(since) == []


def test_poll_invalidations_after_invalidate(tmp_path: Path) -> None:
    db = tmp_path / "shared.db"
    c1 = SQLiteCache(db_path=db, ttl_seconds=60)
    c2 = SQLiteCache(db_path=db, ttl_seconds=60)

    since = time.time() - 0.001  # slightly before
    c1.set("sub:user1", {"data": 1})
    c1.invalidate("sub:user1")

    keys = c2.poll_invalidations(since)
    assert "sub:user1" in keys

    c1.close()
    c2.close()


def test_poll_invalidations_only_returns_since(tmp_path: Path) -> None:
    db = tmp_path / "shared2.db"
    c = SQLiteCache(db_path=db, ttl_seconds=60)

    c.set("sub:a", {"x": 1})
    c.invalidate("sub:a")

    since = time.time()
    time.sleep(0.01)

    c.set("sub:b", {"x": 2})
    c.invalidate("sub:b")

    keys = c.poll_invalidations(since)
    assert "sub:b" in keys
    assert "sub:a" not in keys

    c.close()


def test_poll_invalidations_missing_key_still_logged(tmp_path: Path) -> None:
    """Invalidating a key not in cache still logs it for other processes."""
    db = tmp_path / "shared3.db"
    c = SQLiteCache(db_path=db, ttl_seconds=60)

    since = time.time() - 0.001
    existed = c.invalidate("sub:ghost")
    assert existed is False  # was not in cache

    keys = c.poll_invalidations(since)
    assert "sub:ghost" in keys
    c.close()


def test_prune_invalidation_log(tmp_path: Path) -> None:
    db = tmp_path / "prune.db"
    c = SQLiteCache(db_path=db, ttl_seconds=60)

    since = time.time() - 0.001
    c.invalidate("sub:old")
    keys_before = c.poll_invalidations(since)
    assert "sub:old" in keys_before

    # Prune everything older than 0 seconds (i.e., everything)
    pruned = c.prune_invalidation_log(older_than_seconds=0)
    assert pruned > 0

    keys_after = c.poll_invalidations(since)
    assert keys_after == []
    c.close()
