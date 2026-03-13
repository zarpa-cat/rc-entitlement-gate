"""RevenueCat API client for entitlement gate.

Thin wrapper around the RC REST API v1. Only fetches subscriber data —
no write operations. Cache-aware: results are stored in TTLCache to
minimize RC API calls.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .cache import TTLCache
from .models import (
    CheckResult,
    Entitlement,
    EntitlementStatus,
    SubscriberInfo,
)

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
    ) -> None:
        self.api_key = api_key or os.environ.get("REVENUECAT_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "RevenueCat API key required. Pass api_key= or set REVENUECAT_API_KEY env var."
            )
        self.cache = TTLCache(ttl_seconds=cache_ttl)
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
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
            return CheckResult(
                status=EntitlementStatus.ERROR,
                subscriber_id=subscriber_id,
                entitlement=entitlement,
                granted=False,
                error_message=f"RC API error {e.response.status_code}: {e.response.text[:200]}",
            )
        except Exception as e:  # noqa: BLE001
            return CheckResult(
                status=EntitlementStatus.ERROR,
                subscriber_id=subscriber_id,
                entitlement=entitlement,
                granted=False,
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

    def _build_result(
        self,
        subscriber_id: str,
        entitlement: str,
        rc_data: dict[str, Any],
        cached: bool,
    ) -> CheckResult:
        subscriber = rc_data.get("subscriber", {})
        entitlements: dict[str, Any] = subscriber.get("entitlements", {})

        if entitlement in entitlements:
            ent_detail = Entitlement.from_rc_dict(entitlements[entitlement])
            return CheckResult(
                status=EntitlementStatus.GRANTED,
                subscriber_id=subscriber_id,
                entitlement=entitlement,
                granted=True,
                entitlement_detail=ent_detail,
                cached=cached,
            )

        return CheckResult(
            status=EntitlementStatus.DENIED,
            subscriber_id=subscriber_id,
            entitlement=entitlement,
            granted=False,
            cached=cached,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RCEntitlementClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
