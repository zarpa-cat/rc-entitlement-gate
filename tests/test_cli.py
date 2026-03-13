"""Tests for the entgate CLI."""

from __future__ import annotations

import httpx
import respx
from typer.testing import CliRunner

from rc_entitlement_gate.cli import app
from rc_entitlement_gate.client import RC_API_BASE

runner = CliRunner()
FAKE_KEY = "sk_test_cli"


def _sub_resp(entitlements: dict) -> dict:
    return {
        "subscriber": {
            "entitlements": entitlements,
            "management_url": "https://example.com/manage",
            "original_purchase_date": "2025-06-01T00:00:00Z",
        }
    }


@respx.mock
def test_check_granted_exits_0():
    respx.get(f"{RC_API_BASE}/subscribers/u1").mock(
        return_value=httpx.Response(
            200,
            json=_sub_resp(
                {"premium": {"expires_date": "2027-01-01T00:00:00Z", "purchase_date": None}}
            ),
        )
    )
    result = runner.invoke(app, ["check", "u1", "premium", "--api-key", FAKE_KEY])
    assert result.exit_code == 0
    assert "✅" in result.output


@respx.mock
def test_check_denied_exits_1():
    respx.get(f"{RC_API_BASE}/subscribers/u2").mock(
        return_value=httpx.Response(200, json=_sub_resp({}))
    )
    result = runner.invoke(app, ["check", "u2", "premium", "--api-key", FAKE_KEY])
    assert result.exit_code == 1
    assert "❌" in result.output


@respx.mock
def test_check_json_output():
    respx.get(f"{RC_API_BASE}/subscribers/u3").mock(
        return_value=httpx.Response(
            200, json=_sub_resp({"pro": {"expires_date": None, "purchase_date": None}})
        )
    )
    import json

    result = runner.invoke(app, ["check", "u3", "pro", "--api-key", FAKE_KEY, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["granted"] is True
    assert data["status"] == "granted"


@respx.mock
def test_info_command():
    respx.get(f"{RC_API_BASE}/subscribers/rich").mock(
        return_value=httpx.Response(200, json=_sub_resp({"premium": {}, "addon": {}}))
    )
    result = runner.invoke(app, ["info", "rich", "--api-key", FAKE_KEY])
    assert result.exit_code == 0
    assert "premium" in result.output
    assert "addon" in result.output


@respx.mock
def test_info_not_found():
    respx.get(f"{RC_API_BASE}/subscribers/ghost").mock(return_value=httpx.Response(404, json={}))
    result = runner.invoke(app, ["info", "ghost", "--api-key", FAKE_KEY])
    assert result.exit_code == 1


@respx.mock
def test_batch_all_granted_exits_0():
    for uid in ["a", "b"]:
        respx.get(f"{RC_API_BASE}/subscribers/{uid}").mock(
            return_value=httpx.Response(200, json=_sub_resp({"pro": {}}))
        )
    result = runner.invoke(app, ["batch", "pro", "--ids", "a,b", "--api-key", FAKE_KEY])
    assert result.exit_code == 0
    assert result.output.count("✅") == 2


@respx.mock
def test_batch_partial_denied_exits_1():
    respx.get(f"{RC_API_BASE}/subscribers/yes").mock(
        return_value=httpx.Response(200, json=_sub_resp({"pro": {}}))
    )
    respx.get(f"{RC_API_BASE}/subscribers/no").mock(
        return_value=httpx.Response(200, json=_sub_resp({}))
    )
    result = runner.invoke(app, ["batch", "pro", "--ids", "yes,no", "--api-key", FAKE_KEY])
    assert result.exit_code == 1


def test_missing_api_key_exits_1():
    import os

    old = os.environ.pop("REVENUECAT_API_KEY", None)
    try:
        result = runner.invoke(app, ["check", "u", "e"])
        assert result.exit_code == 1
    finally:
        if old:
            os.environ["REVENUECAT_API_KEY"] = old
