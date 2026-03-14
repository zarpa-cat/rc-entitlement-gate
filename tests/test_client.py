"""Tests for RCEntitlementClient."""

from __future__ import annotations

import httpx
import pytest
import respx

from rc_entitlement_gate.client import RC_API_BASE, RCEntitlementClient
from rc_entitlement_gate.models import EntitlementStatus

FAKE_KEY = "sk_test_fake_key"


def _subscriber_response(entitlements: dict) -> dict:
    return {
        "subscriber": {
            "entitlements": entitlements,
            "management_url": None,
            "original_purchase_date": None,
        }
    }


@respx.mock
def test_check_granted():
    respx.get(f"{RC_API_BASE}/subscribers/user1").mock(
        return_value=httpx.Response(
            200,
            json=_subscriber_response(
                {
                    "premium": {
                        "expires_date": "2027-01-01T00:00:00Z",
                        "purchase_date": "2026-01-01T00:00:00Z",
                        "product_identifier": "premium_monthly",
                    }
                }
            ),
        )
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0) as client:
        result = client.check("user1", "premium")

    assert result.status == EntitlementStatus.GRANTED
    assert result.granted is True
    assert bool(result) is True
    assert result.cached is False


@respx.mock
def test_check_denied():
    respx.get(f"{RC_API_BASE}/subscribers/user2").mock(
        return_value=httpx.Response(200, json=_subscriber_response({}))
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0) as client:
        result = client.check("user2", "premium")

    assert result.status == EntitlementStatus.DENIED
    assert result.granted is False


@respx.mock
def test_check_subscriber_not_found():
    respx.get(f"{RC_API_BASE}/subscribers/ghost").mock(
        return_value=httpx.Response(404, json={"message": "Subscriber not found"})
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0) as client:
        result = client.check("ghost", "premium")

    assert result.status == EntitlementStatus.NOT_FOUND
    assert result.granted is False


@respx.mock
def test_check_api_error():
    respx.get(f"{RC_API_BASE}/subscribers/user3").mock(
        return_value=httpx.Response(500, json={"message": "Internal error"})
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0) as client:
        result = client.check("user3", "premium")

    assert result.status == EntitlementStatus.ERROR
    assert result.granted is False
    assert result.error_message is not None


@respx.mock
def test_check_uses_cache():
    route = respx.get(f"{RC_API_BASE}/subscribers/cached_user").mock(
        return_value=httpx.Response(
            200,
            json=_subscriber_response({"premium": {"expires_date": None, "purchase_date": None}}),
        )
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=600) as client:
        r1 = client.check("cached_user", "premium")
        r2 = client.check("cached_user", "premium")

    assert route.call_count == 1  # only one real API call
    assert r1.cached is False
    assert r2.cached is True


@respx.mock
def test_invalidate_clears_cache():
    route = respx.get(f"{RC_API_BASE}/subscribers/inv_user").mock(
        return_value=httpx.Response(200, json=_subscriber_response({}))
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=600) as client:
        client.check("inv_user", "premium")
        client.invalidate("inv_user")
        client.check("inv_user", "premium")

    assert route.call_count == 2


@respx.mock
def test_subscriber_info_returns_none_on_404():
    respx.get(f"{RC_API_BASE}/subscribers/no_one").mock(return_value=httpx.Response(404, json={}))
    with RCEntitlementClient(api_key=FAKE_KEY) as client:
        info = client.subscriber_info("no_one")
    assert info is None


@respx.mock
def test_subscriber_info_returns_data():
    respx.get(f"{RC_API_BASE}/subscribers/rich_user").mock(
        return_value=httpx.Response(
            200,
            json=_subscriber_response(
                {"pro": {"expires_date": None}, "addon": {"expires_date": None}}
            ),
        )
    )
    with RCEntitlementClient(api_key=FAKE_KEY) as client:
        info = client.subscriber_info("rich_user")

    assert info is not None
    assert set(info.active_entitlements) == {"pro", "addon"}


def test_missing_api_key_raises():
    import os

    env_backup = os.environ.pop("REVENUECAT_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="API key required"):
            RCEntitlementClient(api_key=None)
    finally:
        if env_backup:
            os.environ["REVENUECAT_API_KEY"] = env_backup


@respx.mock
def test_cache_size_increments():
    respx.get(f"{RC_API_BASE}/subscribers/s1").mock(
        return_value=httpx.Response(200, json=_subscriber_response({}))
    )
    respx.get(f"{RC_API_BASE}/subscribers/s2").mock(
        return_value=httpx.Response(200, json=_subscriber_response({}))
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=600) as client:
        client.check("s1", "x")
        client.check("s2", "x")
        assert client.cache_size() == 2


# ------------------------------------------------------------------
# Grace period passthrough (Phase 3b)
# ------------------------------------------------------------------


def _subscriber_response_with_grace(entitlements: dict, subscriptions: dict) -> dict:
    return {
        "subscriber": {
            "entitlements": entitlements,
            "subscriptions": subscriptions,
            "management_url": None,
            "original_purchase_date": None,
        }
    }


@respx.mock
def test_grace_period_detected():
    respx.get(f"{RC_API_BASE}/subscribers/grace_user").mock(
        return_value=httpx.Response(
            200,
            json=_subscriber_response_with_grace(
                entitlements={
                    "premium": {
                        "expires_date": "2027-01-01T00:00:00Z",
                        "purchase_date": "2026-01-01T00:00:00Z",
                        "product_identifier": "premium_monthly",
                    }
                },
                subscriptions={
                    "premium_monthly": {
                        "expires_date": "2026-03-14T18:00:00Z",
                        "grace_period_expires_date": "2026-03-21T18:00:00Z",
                        "billing_issues_detected_at": "2026-03-14T18:01:00Z",
                    }
                },
            ),
        )
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0) as client:
        result = client.check("grace_user", "premium")

    assert result.status == EntitlementStatus.GRANTED
    assert result.granted is True
    assert result.in_grace_period is True
    assert result.grace_period_expires_date is not None
    assert result.billing_issues_detected_at is not None


@respx.mock
def test_no_grace_period_when_not_present():
    respx.get(f"{RC_API_BASE}/subscribers/normal_user").mock(
        return_value=httpx.Response(
            200,
            json=_subscriber_response_with_grace(
                entitlements={
                    "premium": {
                        "expires_date": "2027-01-01T00:00:00Z",
                        "purchase_date": "2026-01-01T00:00:00Z",
                        "product_identifier": "premium_monthly",
                    }
                },
                subscriptions={
                    "premium_monthly": {
                        "expires_date": "2027-01-01T00:00:00Z",
                    }
                },
            ),
        )
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0) as client:
        result = client.check("normal_user", "premium")

    assert result.status == EntitlementStatus.GRANTED
    assert result.in_grace_period is False
    assert result.grace_period_expires_date is None


@respx.mock
def test_grace_period_expired_not_flagged():
    """grace_period_expires_date in the past → not in_grace_period."""
    respx.get(f"{RC_API_BASE}/subscribers/past_grace").mock(
        return_value=httpx.Response(
            200,
            json=_subscriber_response_with_grace(
                entitlements={
                    "premium": {
                        "expires_date": "2027-01-01T00:00:00Z",
                        "purchase_date": "2026-01-01T00:00:00Z",
                        "product_identifier": "premium_monthly",
                    }
                },
                subscriptions={
                    "premium_monthly": {
                        "expires_date": "2025-01-01T00:00:00Z",
                        "grace_period_expires_date": "2024-12-01T00:00:00Z",
                        "billing_issues_detected_at": "2024-11-30T12:00:00Z",
                    }
                },
            ),
        )
    )
    with RCEntitlementClient(api_key=FAKE_KEY, cache_ttl=0) as client:
        result = client.check("past_grace", "premium")

    assert result.in_grace_period is False
    assert result.grace_period_expires_date is not None  # still populated for reference
