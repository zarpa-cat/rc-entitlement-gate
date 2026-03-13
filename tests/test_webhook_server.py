"""Tests for the webhook invalidation server (Phase 2)."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from rc_entitlement_gate.webhook_server import create_app

WEBHOOK_SECRET = "test_webhook_secret_abc"


def _make_app(client=None, auth_token=None):
    if client is None:
        client = MagicMock()
        client.invalidate.return_value = True
    return create_app(rc_client=client, auth_token=auth_token)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def test_health_check():
    app = _make_app()
    tc = TestClient(app)
    resp = tc.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Webhook — no auth
# ---------------------------------------------------------------------------


def test_webhook_initial_purchase_invalidates_cache():
    mock_client = MagicMock()
    mock_client.invalidate.return_value = True
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    payload = {
        "event": {
            "type": "INITIAL_PURCHASE",
            "app_user_id": "user123",
        }
    }
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 200
    mock_client.invalidate.assert_called_once_with("user123")


def test_webhook_renewal_invalidates_cache():
    mock_client = MagicMock()
    mock_client.invalidate.return_value = True
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    payload = {"event": {"type": "RENEWAL", "app_user_id": "user456"}}
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 200
    mock_client.invalidate.assert_called_once_with("user456")


def test_webhook_cancellation_invalidates_cache():
    mock_client = MagicMock()
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    payload = {"event": {"type": "CANCELLATION", "app_user_id": "user789"}}
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 200
    mock_client.invalidate.assert_called_once_with("user789")


def test_webhook_billing_issue_invalidates_cache():
    mock_client = MagicMock()
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    payload = {"event": {"type": "BILLING_ISSUE", "app_user_id": "billing_user"}}
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 200
    mock_client.invalidate.assert_called_once_with("billing_user")


def test_webhook_unknown_event_type_still_invalidates():
    """Unknown event types invalidate as a safe default."""
    mock_client = MagicMock()
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    payload = {"event": {"type": "SOME_FUTURE_EVENT", "app_user_id": "u1"}}
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 200
    mock_client.invalidate.assert_called_once_with("u1")


def test_webhook_missing_app_user_id_returns_400():
    mock_client = MagicMock()
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    payload = {"event": {"type": "RENEWAL"}}  # no app_user_id
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 400
    mock_client.invalidate.assert_not_called()


def test_webhook_malformed_payload_returns_error():
    mock_client = MagicMock()
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    # no "event" key — FastAPI returns 422 Unprocessable Entity for schema validation failures
    resp = tc.post("/webhook", json={"something": "wrong"})
    assert resp.status_code in (400, 422)
    mock_client.invalidate.assert_not_called()


# ---------------------------------------------------------------------------
# Webhook — with auth token
# ---------------------------------------------------------------------------


def test_webhook_auth_accepted_with_correct_token():
    mock_client = MagicMock()
    mock_client.invalidate.return_value = True
    app = _make_app(client=mock_client, auth_token=WEBHOOK_SECRET)
    tc = TestClient(app)

    payload = {"event": {"type": "RENEWAL", "app_user_id": "authed_user"}}
    resp = tc.post(
        "/webhook",
        json=payload,
        headers={"Authorization": f"Bearer {WEBHOOK_SECRET}"},
    )
    assert resp.status_code == 200
    mock_client.invalidate.assert_called_once_with("authed_user")


def test_webhook_auth_rejected_with_wrong_token():
    mock_client = MagicMock()
    app = _make_app(client=mock_client, auth_token=WEBHOOK_SECRET)
    tc = TestClient(app)

    payload = {"event": {"type": "RENEWAL", "app_user_id": "sneaky"}}
    resp = tc.post(
        "/webhook",
        json=payload,
        headers={"Authorization": "Bearer wrong_token"},
    )
    assert resp.status_code == 401
    mock_client.invalidate.assert_not_called()


def test_webhook_auth_rejected_when_no_token_provided():
    mock_client = MagicMock()
    app = _make_app(client=mock_client, auth_token=WEBHOOK_SECRET)
    tc = TestClient(app)

    payload = {"event": {"type": "RENEWAL", "app_user_id": "sneaky"}}
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 401


def test_webhook_no_auth_required_when_no_token_configured():
    """When no auth_token is configured, all requests pass."""
    mock_client = MagicMock()
    mock_client.invalidate.return_value = True
    app = _make_app(client=mock_client, auth_token=None)
    tc = TestClient(app)

    payload = {"event": {"type": "RENEWAL", "app_user_id": "open_user"}}
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 200
    mock_client.invalidate.assert_called_once_with("open_user")


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_webhook_response_includes_subscriber_id_and_event_type():
    mock_client = MagicMock()
    mock_client.invalidate.return_value = True
    app = _make_app(client=mock_client)
    tc = TestClient(app)

    payload = {"event": {"type": "EXPIRATION", "app_user_id": "resp_user"}}
    resp = tc.post("/webhook", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["subscriber_id"] == "resp_user"
    assert data["event_type"] == "EXPIRATION"
    assert data["invalidated"] is True
