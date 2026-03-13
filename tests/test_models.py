"""Tests for data models."""

from rc_entitlement_gate.models import (
    CheckResult,
    Entitlement,
    EntitlementStatus,
    SubscriberInfo,
)


def test_entitlement_from_rc_dict_full():
    data = {
        "expires_date": "2026-12-31T23:59:59Z",
        "purchase_date": "2026-01-01T00:00:00Z",
        "product_identifier": "premium_monthly",
    }
    ent = Entitlement.from_rc_dict(data)
    assert ent.is_active is True
    assert ent.product_identifier == "premium_monthly"
    assert ent.expires_date is not None
    assert ent.expires_date.year == 2026


def test_entitlement_from_rc_dict_no_expiry():
    data = {"product_identifier": "lifetime_access"}
    ent = Entitlement.from_rc_dict(data)
    assert ent.expires_date is None
    assert ent.is_active is True


def test_check_result_truthy_when_granted():
    result = CheckResult(
        status=EntitlementStatus.GRANTED,
        subscriber_id="u1",
        entitlement="premium",
        granted=True,
    )
    assert bool(result) is True


def test_check_result_falsy_when_denied():
    result = CheckResult(
        status=EntitlementStatus.DENIED,
        subscriber_id="u1",
        entitlement="premium",
        granted=False,
    )
    assert bool(result) is False


def test_check_result_falsy_on_error():
    result = CheckResult(
        status=EntitlementStatus.ERROR,
        subscriber_id="u1",
        entitlement="premium",
        granted=False,
        error_message="timeout",
    )
    assert bool(result) is False


def test_subscriber_info_from_rc_response():
    rc_data = {
        "subscriber": {
            "entitlements": {
                "premium": {"expires_date": "2026-06-01T00:00:00Z"},
                "addon_export": {"expires_date": "2026-06-01T00:00:00Z"},
            },
            "management_url": "https://apps.apple.com/...",
            "original_purchase_date": "2025-01-15T12:00:00Z",
        }
    }
    info = SubscriberInfo.from_rc_response("user_42", rc_data)
    assert info.subscriber_id == "user_42"
    assert "premium" in info.active_entitlements
    assert "addon_export" in info.active_entitlements
    assert info.management_url is not None
    assert info.original_purchase_date is not None


def test_subscriber_info_empty_entitlements():
    rc_data = {"subscriber": {"entitlements": {}}}
    info = SubscriberInfo.from_rc_response("free_user", rc_data)
    assert info.active_entitlements == []


def test_subscriber_info_missing_subscriber_key():
    info = SubscriberInfo.from_rc_response("x", {})
    assert info.active_entitlements == []
    assert info.management_url is None
