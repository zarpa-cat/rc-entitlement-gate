"""Data models for rc-entitlement-gate."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EntitlementStatus(str, Enum):  # noqa: UP042 — StrEnum not available in 3.10
    """Result of an entitlement check."""

    GRANTED = "granted"
    DENIED = "denied"
    NOT_FOUND = "not_found"  # subscriber not found in RC
    ERROR = "error"  # upstream failure
    STALE = "stale"  # served from stale cache during offline fallback


class Entitlement(BaseModel):
    """A single RC entitlement from the subscriber object."""

    expires_date: datetime | None = None
    purchase_date: datetime | None = None
    product_identifier: str | None = None
    is_active: bool = False

    @classmethod
    def from_rc_dict(cls, data: dict[str, Any]) -> Entitlement:
        expires_raw = data.get("expires_date")
        purchase_raw = data.get("purchase_date")
        return cls(
            expires_date=datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if expires_raw
            else None,
            purchase_date=datetime.fromisoformat(purchase_raw.replace("Z", "+00:00"))
            if purchase_raw
            else None,
            product_identifier=data.get("product_identifier"),
            is_active=True,  # RC only returns active entitlements in active_subscriptions
        )


class CheckResult(BaseModel):
    """Result of checking entitlement access."""

    status: EntitlementStatus
    subscriber_id: str
    entitlement: str
    granted: bool
    entitlement_detail: Entitlement | None = None
    cached: bool = False
    stale: bool = False  # True when served from stale cache (offline fallback)
    error_message: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Expiry warning fields (populated when expires_soon_threshold_seconds is set on client)
    expires_soon: bool = False
    expires_in_seconds: int | None = None
    # Grace period fields (RC billing issues — entitlement still granted but payment failed)
    in_grace_period: bool = False
    grace_period_expires_date: datetime | None = None
    billing_issues_detected_at: datetime | None = None

    def __bool__(self) -> bool:
        return self.granted


class SubscriberInfo(BaseModel):
    """Minimal subscriber summary from RC."""

    subscriber_id: str
    active_entitlements: list[str] = Field(default_factory=list)
    management_url: str | None = None
    original_purchase_date: datetime | None = None

    @classmethod
    def from_rc_response(cls, subscriber_id: str, data: dict[str, Any]) -> SubscriberInfo:
        subscriber = data.get("subscriber", {})
        entitlements = subscriber.get("entitlements", {})
        management_url = subscriber.get("management_url")
        original_raw = subscriber.get("original_purchase_date")

        return cls(
            subscriber_id=subscriber_id,
            active_entitlements=list(entitlements.keys()),
            management_url=management_url,
            original_purchase_date=datetime.fromisoformat(original_raw.replace("Z", "+00:00"))
            if original_raw
            else None,
        )
