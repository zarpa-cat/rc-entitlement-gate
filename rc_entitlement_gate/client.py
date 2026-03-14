"""RevenueCat API client for entitlement gate.

Thin wrapper around the RC REST API v1. Only fetches subscriber data —
no write operations. Cache-aware: results are stored in TTLCache to
minimize RC API calls.

Phase 2 additions:
- offline_fallback: serve stale cache when RC API is unreachable
- expires_soon_threshold_seconds: flag entitlements expiring soon
- stale_window_seconds: how long to keep expired cache for fallback

Phase 3 additions:
- cache_backend: "memory" (default) or "sqlite" for persistent cache
- cache_db_path: path to SQLite database file (only used when cache_backend="sqlite")

Phase 3b additions:
- Grace period passthrough: GRANTED results include in_grace_period, grace_period_expires_date,
  billing_issues_detected_at when RC returns billing-issue subscription data.
- Distributed invalidation: SQLiteCache supports poll_invalidations() for multi-process setups.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from .cache import TTLCache
from .models import (
    CheckResult,
    Entitlement,
    EntitlementStatus,
    SubscriberInfo,
)
from .sqlite_cache import SQLiteCache

CacheBackend = TTLCache | SQLiteCache

RC_API_BASE = "https://api.revenuecat.com/v1"
DEFAULT_TIMEOUT = 10.0


class RCEntitlementClient:
    """Checks RevenueCat entitlements with caching.

    Usage:
        client = RCEntitlementClient(api_key="sk_...")
        result = client.check("user_123", "premium")
        if result:
            # access granted
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_ttl: int = 60,
        timeout: float = DEFAULT_TIMEOUT,
        base_url: str = RC_API_BASE,
        offline_fallback: bool = False,
        stale_window_seconds: int = 300,
        expires_soon_threshold_seconds: int = 0,
        cache_backend: str = "memory",
        cache_db_path: str = "entgate_cache.db",
    ) -> None:
        self.api_key = api_key or os.environ.get("REVENUECAT_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "RevenueCat API key required. Pass api_key= or set REVENUECAT_API_KEY env var."
            )
        if cache_backend == "sqlite":
            self.cache: CacheBackend = SQLiteCache(
                db_path=cache_db_path,
                ttl_seconds=cache_ttl,
                stale_window_seconds=stale_window_seconds,
            )
        else:
            self.cache = TTLCache(ttl_seconds=cache_ttl, stale_window_seconds=stale_window_seconds)
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.offline_fallback = offline_fallback
        self.expires_soon_threshold_seconds = expires_soon_threshold_seconds
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-Platform": "stripe",  # required by RC for server-side calls
            },
            timeout=self.timeout,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, subscriber_id: str, entitlement: str) -> CheckResult:
        """Check if a subscriber has an entitlement. Returns CheckResult (truthy if granted)."""
        cache_key = self.cache._cache_key(subscriber_id)
        cached_data: dict[str, Any] | None = self.cache.get(cache_key)

        if cached_data is not None:
            return self._build_result(
                subscriber_id=subscriber_id,
                entitlement=entitlement,
                rc_data=cached_data,
                cached=True,
            )

        try:
            rc_data = self._fetch_subscriber(subscriber_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return CheckResult(
                    status=EntitlementStatus.NOT_FOUND,
                    subscriber_id=subscriber_id,
                    entitlement=entitlement,
                    granted=False,
                    error_message="Subscriber not found in RevenueCat",
                )
            return self._handle_fetch_failure(
                subscriber_id=subscriber_id,
                entitlement=entitlement,
                cache_key=cache_key,
                error_message=f"RC API error {e.response.status_code}: {e.response.text[:200]}",
            )
        except Exception as e:  # noqa: BLE001
            return self._handle_fetch_failure(
                subscriber_id=subscriber_id,
                entitlement=entitlement,
                cache_key=cache_key,
                error_message=str(e),
            )

        self.cache.set(cache_key, rc_data)
        return self._build_result(
            subscriber_id=subscriber_id,
            entitlement=entitlement,
            rc_data=rc_data,
            cached=False,
        )

    def subscriber_info(self, subscriber_id: str) -> SubscriberInfo | None:
        """Fetch full subscriber info (cached). Returns None on 404."""
        cache_key = self.cache._cache_key(subscriber_id)
        cached = self.cache.get(cache_key)

        try:
            data = cached or self._fetch_subscriber(subscriber_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        if not cached:
            self.cache.set(cache_key, data)

        return SubscriberInfo.from_rc_response(subscriber_id, data)

    def invalidate(self, subscriber_id: str) -> bool:
        """Remove a subscriber from cache (call after webhook events)."""
        return self.cache.invalidate(self.cache._cache_key(subscriber_id))

    def cache_size(self) -> int:
        return self.cache.size()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_subscriber(self, subscriber_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/subscribers/{subscriber_id}"
        resp = self._http.get(url)
        resp.raise_for_status()
        return resp.json()

    def _handle_fetch_failure(
        self,
        subscriber_id: str,
        entitlement: str,
        cache_key: str,
        error_message: str,
    ) -> CheckResult:
        """On fetch failure, try stale fallback if configured; else return ERROR."""
        if self.offline_fallback:
            stale_data = self.cache.get_stale(cache_key)
            if stale_data is not None:
                result = self._build_result(
                    subscriber_id=subscriber_id,
                    entitlement=entitlement,
                    rc_data=stale_data,
                    cached=True,
                    stale=True,
                )
                result.status = EntitlementStatus.STALE
                result.error_message = f"Stale cache (offline fallback): {error_message}"
                return result

        return CheckResult(
            status=EntitlementStatus.ERROR,
            subscriber_id=subscriber_id,
            entitlement=entitlement,
            granted=False,
            error_message=error_message,
        )

    def _build_result(
        self,
        subscriber_id: str,
        entitlement: str,
        rc_data: dict[str, Any],
        cached: bool,
        stale: bool = False,
    ) -> CheckResult:
        subscriber = rc_data.get("subscriber", {})
        entitlements: dict[str, Any] = subscriber.get("entitlements", {})

        if entitlement in entitlements:
            ent_detail = Entitlement.from_rc_dict(entitlements[entitlement])
            expires_soon, expires_in_seconds = self._check_expiry(ent_detail)
            in_grace, grace_expires, billing_issues_at = self._check_grace_period(
                ent_detail, subscriber
            )
            return CheckResult(
                status=EntitlementStatus.GRANTED,
                subscriber_id=subscriber_id,
                entitlement=entitlement,
                granted=True,
                entitlement_detail=ent_detail,
                cached=cached,
                stale=stale,
                expires_soon=expires_soon,
                expires_in_seconds=expires_in_seconds,
                in_grace_period=in_grace,
                grace_period_expires_date=grace_expires,
                billing_issues_detected_at=billing_issues_at,
            )

        return CheckResult(
            status=EntitlementStatus.DENIED,
            subscriber_id=subscriber_id,
            entitlement=entitlement,
            granted=False,
            cached=cached,
            stale=stale,
        )

    def _check_expiry(self, ent: Entitlement) -> tuple[bool, int | None]:
        """Returns (expires_soon, expires_in_seconds) based on threshold config."""
        if not self.expires_soon_threshold_seconds or ent.expires_date is None:
            return False, None
        now = datetime.now(UTC)
        delta = (ent.expires_date - now).total_seconds()
        if delta < 0:
            return False, None  # already expired (shouldn't happen with active entitlements)
        if delta < self.expires_soon_threshold_seconds:
            return True, int(delta)
        return False, None

    def _check_grace_period(
        self,
        ent: Entitlement,
        subscriber: dict[str, Any],
    ) -> tuple[bool, datetime | None, datetime | None]:
        """Check if the subscription backing this entitlement is in a billing grace period.

        RC keeps entitlements GRANTED during grace periods (billing failed but not yet lapsed).
        Returns: (in_grace_period, grace_period_expires_date, billing_issues_detected_at).
        """
        if not ent.product_identifier:
            return False, None, None

        subscriptions: dict[str, Any] = subscriber.get("subscriptions", {})
        sub = subscriptions.get(ent.product_identifier)
        if not sub:
            return False, None, None

        grace_raw = sub.get("grace_period_expires_date")
        billing_raw = sub.get("billing_issues_detected_at")

        if not grace_raw:
            return False, None, None

        grace_dt = datetime.fromisoformat(grace_raw.replace("Z", "+00:00"))
        billing_dt = (
            datetime.fromisoformat(billing_raw.replace("Z", "+00:00")) if billing_raw else None
        )
        now = datetime.now(UTC)
        in_grace = grace_dt > now
        return in_grace, grace_dt, billing_dt

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RCEntitlementClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
