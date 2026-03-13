"""Tests for stale/offline fallback and expires-soon warning (Phase 2)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import httpx
import respx

from rc_entitlement_gate.cache import TTLCache
from rc_entitlement_gate.client import RC_API_BASE, RCEntitlementClient
from rc_entitlement_gate.models import EntitlementStatus

FAKE_KEY = "sk_test_fake_key"


# ---------------------------------------------------------------------------
# TTLCache — stale window
# ---------------------------------------------------------------------------


def test_cache_get_stale_returns_expired_entry():
    """get_stale should return an expired entry within the stale window."""
    cache = TTLCache(ttl_seconds=0, stale_window_seconds=60)
    cache.set("k", "value")
    time.sleep(0.01)  # let TTL expire (ttl=0 is instant)
    result = cache.get_stale("k")
    assert result == "value"


def test_cache_get_returns_none_for_expired():
    """Normal get() still returns None for expired entries."""
    cache = TTLCache(ttl_seconds=0, stale_window_seconds=60)
    cache.set("k", "value")
    time.sleep(0.01)
    assert cache.get("k") is None


def test_cache_get_stale_returns_none_when_beyond_stale_window():
    """get_stale returns None when beyond stale window."""
    cache = TTLCache(ttl_seconds=0, stale_window_seconds=0)
    cache.set("k", "value")
    time.sleep(0.01)
    assert cache.get_stale("k") is None


def test_cache_get_stale_returns_fresh_entry():
    """get_stale also works for fresh (non-expired) entries."""
    cache = TTLCache(ttl_seconds=600, stale_window_seconds=60)
    cache.set("k", "fresh")
    assert cache.get_stale("k") == "fresh"


def test_cache_get_stale_returns_none_for_missing():
    cache = TTLCache(ttl_seconds=60, stale_window_seconds=60)
    assert cache.get_stale("missing") is None


# ---------------------------------------------------------------------------
# RCEntitlementClient — offline fallback
# ---------------------------------------------------------------------------


@respx.mock
def test_offline_fallback_serves_stale_on_network_error():
    """When RC is unreachable and offline_fallback=True, serve stale cache."""
    route = respx.get(f"{RC_API_BASE}/subscribers/user_stale")
    route.side_effect = httpx.ConnectError("connection refused")

    with RCEntitlementClient(
        api_key=FAKE_KEY, cache_ttl=0, stale_window_seconds=300, offline_fallback=True
    ) as client:
        # Manually prime the stale cache
        cache_key = client.cache._cache_key("user_stale")
        client.cache.set(
            cache_key,
            {
                "subscriber": {
                    "entitlements": {
                        "premium": {
                            "expires_date": "2027-01-01T00:00:00Z",
                            "purchase_date": "2026-01-01T00:00:00Z",
                            "product_identifier": "premium_monthly",
                        }
                    },
                    "management_url": None,
                    "original_purchase_date": None,
                }
            },
        )
        time.sleep(0.01)  # expire the TTL

        result = client.check("user_stale", "premium")

    assert result.status == EntitlementStatus.STALE
    assert result.granted is True  # still grants access (offline fallback)
    assert result.stale is True
    assert result.error_message is not None


@respx.mock
def test_offline_fallback_disabled_returns_error_on_network_failure():
    """When offline_fallback=False (default), network errors return ERROR."""
    respx.get(f"{RC_API_BASE}/subscribers/user_err").side_effect = httpx.ConnectError("no route")

    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0, offline_fallback=False) as client:
        result = client.check("user_err", "premium")

    assert result.status == EntitlementStatus.ERROR
    assert result.granted is False


@respx.mock
def test_offline_fallback_no_stale_data_returns_error():
    """offline_fallback=True but no cached data → still ERROR."""
    respx.get(f"{RC_API_BASE}/subscribers/new_user").side_effect = httpx.ConnectError("timeout")

    with RCEntitlementClient(
        api_key=FAKE_KEY, cache_ttl=0, stale_window_seconds=300, offline_fallback=True
    ) as client:
        result = client.check("new_user", "premium")

    assert result.status == EntitlementStatus.ERROR


# ---------------------------------------------------------------------------
# CheckResult — expires_soon
# ---------------------------------------------------------------------------


@respx.mock
def test_expires_soon_flag_set_when_within_threshold():
    """expires_soon=True when entitlement expires within threshold."""
    soon = (datetime.now(UTC) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    respx.get(f"{RC_API_BASE}/subscribers/user_soon").mock(
        return_value=httpx.Response(
            200,
            json={
                "subscriber": {
                    "entitlements": {
                        "premium": {
                            "expires_date": soon,
                            "purchase_date": "2026-01-01T00:00:00Z",
                            "product_identifier": "premium_monthly",
                        }
                    },
                    "management_url": None,
                    "original_purchase_date": None,
                }
            },
        )
    )

    with RCEntitlementClient(
        api_key=FAKE_KEY,
        cache_ttl=0,
        expires_soon_threshold_seconds=86400,  # 24h
    ) as client:
        result = client.check("user_soon", "premium")

    assert result.status == EntitlementStatus.GRANTED
    assert result.granted is True
    assert result.expires_soon is True
    assert result.expires_in_seconds is not None
    assert result.expires_in_seconds < 86400


@respx.mock
def test_expires_soon_flag_not_set_when_far_future():
    """expires_soon=False when entitlement expires well beyond threshold."""
    far = "2028-01-01T00:00:00Z"
    respx.get(f"{RC_API_BASE}/subscribers/user_far").mock(
        return_value=httpx.Response(
            200,
            json={
                "subscriber": {
                    "entitlements": {
                        "premium": {
                            "expires_date": far,
                            "purchase_date": "2026-01-01T00:00:00Z",
                            "product_identifier": "premium_monthly",
                        }
                    },
                    "management_url": None,
                    "original_purchase_date": None,
                }
            },
        )
    )

    with RCEntitlementClient(
        api_key=FAKE_KEY, cache_ttl=0, expires_soon_threshold_seconds=86400
    ) as client:
        result = client.check("user_far", "premium")

    assert result.expires_soon is False


@respx.mock
def test_expires_soon_false_when_no_expiry_date():
    """expires_soon=False when entitlement has no expires_date (lifetime)."""
    respx.get(f"{RC_API_BASE}/subscribers/user_lifetime").mock(
        return_value=httpx.Response(
            200,
            json={
                "subscriber": {
                    "entitlements": {
                        "premium": {
                            "expires_date": None,
                            "purchase_date": "2026-01-01T00:00:00Z",
                            "product_identifier": "premium_lifetime",
                        }
                    },
                    "management_url": None,
                    "original_purchase_date": None,
                }
            },
        )
    )

    with RCEntitlementClient(
        api_key=FAKE_KEY, cache_ttl=0, expires_soon_threshold_seconds=86400
    ) as client:
        result = client.check("user_lifetime", "premium")

    assert result.expires_soon is False
    assert result.expires_in_seconds is None
