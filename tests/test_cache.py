"""Tests for TTL cache."""

from rc_entitlement_gate.cache import TTLCache


def test_set_and_get():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_miss_returns_none():
    cache = TTLCache(ttl_seconds=60)
    assert cache.get("missing") is None


def test_expired_returns_none(monkeypatch):
    cache = TTLCache(ttl_seconds=1)
    import time as t

    calls = iter([0.0, 2.0])  # set time, get time (past TTL)
    monkeypatch.setattr(t, "monotonic", lambda: next(calls))
    cache.set("k", "v")
    assert cache.get("k") is None


def test_invalidate_existing():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", "v")
    assert cache.invalidate("k") is True
    assert cache.get("k") is None


def test_invalidate_missing():
    cache = TTLCache(ttl_seconds=60)
    assert cache.invalidate("nope") is False


def test_clear():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.size() == 0


def test_size_counts_live_entries():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.size() == 2


def test_size_excludes_expired(monkeypatch):
    import time as t

    cache = TTLCache(ttl_seconds=1)
    tick = 0.0

    def fake_mono():
        return tick

    monkeypatch.setattr(t, "monotonic", fake_mono)
    cache.set("a", 1)

    # Advance clock past TTL
    tick = 5.0
    assert cache.size() == 0


def test_overwrite():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", "first")
    cache.set("k", "second")
    assert cache.get("k") == "second"


def test_cache_key_format():
    cache = TTLCache()
    assert cache._cache_key("user123") == "sub:user123"
