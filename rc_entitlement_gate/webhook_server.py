"""Webhook invalidation server for rc-entitlement-gate.

Lightweight FastAPI server that accepts RevenueCat webhook events and
automatically invalidates the affected subscriber from the entitlement cache.

Usage:
    from rc_entitlement_gate import RCEntitlementClient
    from rc_entitlement_gate.webhook_server import create_app
    import uvicorn

    client = RCEntitlementClient(api_key="sk_...", cache_ttl=300)
    app = create_app(rc_client=client, auth_token="your_webhook_secret")
    uvicorn.run(app, host="0.0.0.0", port=8080)

Or via CLI:
    entgate webhook-server --port 8080 --auth-token $WEBHOOK_SECRET

RC webhook setup:
    Dashboard → Project → Integrations → Webhooks → add endpoint URL
    Set Authorization header to: Bearer <auth_token>
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel


class WebhookEvent(BaseModel):
    """Parsed RC webhook event."""

    type: str
    app_user_id: str


class WebhookPayload(BaseModel):
    """Top-level RC webhook payload."""

    event: dict[str, Any]


class WebhookResponse(BaseModel):
    subscriber_id: str
    event_type: str
    invalidated: bool


def create_app(
    rc_client: Any,
    auth_token: str | None = None,
) -> FastAPI:
    """Create the webhook FastAPI app.

    Args:
        rc_client: An RCEntitlementClient instance. The app calls
            rc_client.invalidate(subscriber_id) on each event.
        auth_token: Optional Bearer token. When set, requests without a
            matching Authorization header are rejected with 401.
    """
    app = FastAPI(
        title="rc-entitlement-gate webhook",
        description="Invalidates RC entitlement cache on subscription events",
        version="0.2.0",
    )

    def _check_auth(request: Request) -> None:
        if auth_token is None:
            return
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
        provided = auth_header[len("Bearer ") :]
        if not secrets.compare_digest(provided, auth_token):
            raise HTTPException(status_code=401, detail="Invalid auth token")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "rc-entitlement-gate-webhook"}

    @app.post("/webhook", response_model=WebhookResponse)
    async def handle_webhook(request: Request, payload: WebhookPayload) -> WebhookResponse:
        _check_auth(request)

        event_data = payload.event
        event_type = event_data.get("type")
        subscriber_id = event_data.get("app_user_id")

        if not subscriber_id:
            raise HTTPException(
                status_code=400,
                detail="Missing app_user_id in event payload",
            )

        invalidated = rc_client.invalidate(subscriber_id)

        return WebhookResponse(
            subscriber_id=subscriber_id,
            event_type=event_type or "UNKNOWN",
            invalidated=invalidated,
        )

    return app
