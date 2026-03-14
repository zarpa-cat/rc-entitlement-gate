"""CLI interface for rc-entitlement-gate.

Commands:
  entgate check <subscriber_id> <entitlement>   Check single entitlement
  entgate info <subscriber_id>                  Show subscriber summary
  entgate batch <entitlement> --ids a,b,c       Check multiple subscribers
  entgate webhook-server                         Run webhook invalidation server
"""

from __future__ import annotations

import json
import os
from typing import Annotated

import typer

from .client import RCEntitlementClient
from .models import EntitlementStatus
from .webhook_server import create_app

app = typer.Typer(
    name="entgate",
    help="RevenueCat entitlement checker for agents and backend services.",
    add_completion=False,
)

_API_KEY_OPT = Annotated[
    str | None,
    typer.Option("--api-key", "-k", envvar="REVENUECAT_API_KEY", help="RC API key"),
]
_JSON_OPT = Annotated[bool, typer.Option("--json", help="Output JSON")]
_CACHE_TTL_OPT = Annotated[
    int, typer.Option("--cache-ttl", help="Cache TTL in seconds", show_default=True)
]
_CACHE_BACKEND_OPT = Annotated[
    str,
    typer.Option(
        "--cache-backend",
        help="Cache backend: 'memory' (default) or 'sqlite' for persistent cache",
        show_default=True,
    ),
]
_CACHE_DB_OPT = Annotated[
    str,
    typer.Option(
        "--cache-db",
        envvar="ENTGATE_CACHE_DB",
        help="SQLite database path (only used when --cache-backend=sqlite)",
        show_default=True,
    ),
]


def _make_client(
    api_key: str | None,
    cache_ttl: int,
    cache_backend: str = "memory",
    cache_db: str = "entgate_cache.db",
) -> RCEntitlementClient:
    key = api_key or os.environ.get("REVENUECAT_API_KEY", "")
    if not key:
        typer.echo(
            "Error: REVENUECAT_API_KEY not set. Pass --api-key or set the env var.", err=True
        )
        raise typer.Exit(1)
    if cache_backend not in ("memory", "sqlite"):
        typer.echo(
            f"Error: --cache-backend must be 'memory' or 'sqlite', got '{cache_backend}'",
            err=True,
        )
        raise typer.Exit(2)
    return RCEntitlementClient(
        api_key=key,
        cache_ttl=cache_ttl,
        cache_backend=cache_backend,
        cache_db_path=cache_db,
    )


@app.command("check")
def cmd_check(
    subscriber_id: Annotated[str, typer.Argument(help="Subscriber / app user ID")],
    entitlement: Annotated[str, typer.Argument(help="Entitlement identifier to check")],
    api_key: _API_KEY_OPT = None,
    json_out: _JSON_OPT = False,
    cache_ttl: _CACHE_TTL_OPT = 60,
    cache_backend: _CACHE_BACKEND_OPT = "memory",
    cache_db: _CACHE_DB_OPT = "entgate_cache.db",
) -> None:
    """Check if a subscriber has an entitlement. Exits 0 if granted, 1 if denied, 2 on error."""
    with _make_client(api_key, cache_ttl, cache_backend, cache_db) as client:
        result = client.check(subscriber_id, entitlement)

    if json_out:
        typer.echo(result.model_dump_json(indent=2))
    else:
        icon = "✅" if result.granted else "❌"
        typer.echo(f"{icon} {result.subscriber_id} / {result.entitlement}: {result.status.value}")
        if result.cached:
            typer.echo("   (cached)")
        if result.entitlement_detail and result.entitlement_detail.expires_date:
            typer.echo(f"   expires: {result.entitlement_detail.expires_date.isoformat()}")
        if result.error_message:
            typer.echo(f"   error: {result.error_message}", err=True)

    # Exit codes: 0=granted, 1=denied/not-found, 2=error
    if result.status == EntitlementStatus.GRANTED:
        raise typer.Exit(0)
    elif result.status == EntitlementStatus.ERROR:
        raise typer.Exit(2)
    else:
        raise typer.Exit(1)


@app.command("info")
def cmd_info(
    subscriber_id: Annotated[str, typer.Argument(help="Subscriber / app user ID")],
    api_key: _API_KEY_OPT = None,
    json_out: _JSON_OPT = False,
    cache_ttl: _CACHE_TTL_OPT = 60,
    cache_backend: _CACHE_BACKEND_OPT = "memory",
    cache_db: _CACHE_DB_OPT = "entgate_cache.db",
) -> None:
    """Show subscriber summary (active entitlements, management URL)."""
    with _make_client(api_key, cache_ttl, cache_backend, cache_db) as client:
        info = client.subscriber_info(subscriber_id)

    if info is None:
        typer.echo(f"Subscriber '{subscriber_id}' not found.", err=True)
        raise typer.Exit(1)

    if json_out:
        typer.echo(info.model_dump_json(indent=2))
    else:
        typer.echo(f"Subscriber: {info.subscriber_id}")
        typer.echo(f"Active entitlements: {', '.join(info.active_entitlements) or 'none'}")
        if info.management_url:
            typer.echo(f"Manage: {info.management_url}")
        if info.original_purchase_date:
            typer.echo(f"First purchase: {info.original_purchase_date.isoformat()}")


@app.command("batch")
def cmd_batch(
    entitlement: Annotated[str, typer.Argument(help="Entitlement to check for all subscribers")],
    ids: Annotated[str, typer.Option("--ids", help="Comma-separated subscriber IDs")],
    api_key: _API_KEY_OPT = None,
    json_out: _JSON_OPT = False,
    cache_ttl: _CACHE_TTL_OPT = 60,
    cache_backend: _CACHE_BACKEND_OPT = "memory",
    cache_db: _CACHE_DB_OPT = "entgate_cache.db",
) -> None:
    """Check an entitlement for multiple subscribers. Exits 0 if ALL granted."""
    subscriber_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not subscriber_ids:
        typer.echo("Error: --ids must contain at least one subscriber ID.", err=True)
        raise typer.Exit(2)

    results = []
    with _make_client(api_key, cache_ttl, cache_backend, cache_db) as client:
        for sid in subscriber_ids:
            results.append(client.check(sid, entitlement))

    if json_out:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2, default=str))
    else:
        for r in results:
            icon = "✅" if r.granted else "❌"
            typer.echo(f"{icon} {r.subscriber_id}: {r.status.value}")

    all_granted = all(r.granted for r in results)
    raise typer.Exit(0 if all_granted else 1)


@app.command("webhook-server")
def cmd_webhook_server(
    api_key: _API_KEY_OPT = None,
    cache_ttl: _CACHE_TTL_OPT = 300,
    auth_token: Annotated[
        str | None,
        typer.Option("--auth-token", envvar="WEBHOOK_AUTH_TOKEN", help="Bearer token for auth"),
    ] = None,
    host: Annotated[str, typer.Option("--host", help="Bind host")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", "-p", help="Bind port")] = 8080,
    stale_window: Annotated[
        int, typer.Option("--stale-window", help="Stale cache window in seconds")
    ] = 300,
) -> None:
    """Run a webhook server that invalidates entitlement cache on RC events.

    Set as your RC webhook endpoint URL (POST /webhook).
    Optionally pass --auth-token to require a Bearer token.
    """
    import uvicorn

    key = api_key or os.environ.get("REVENUECAT_API_KEY", "")
    if not key:
        typer.echo(
            "Error: REVENUECAT_API_KEY not set. Pass --api-key or set the env var.", err=True
        )
        raise typer.Exit(1)

    rc_client = RCEntitlementClient(
        api_key=key, cache_ttl=cache_ttl, stale_window_seconds=stale_window
    )
    fastapi_app = create_app(rc_client=rc_client, auth_token=auth_token)

    typer.echo(f"Starting webhook server on {host}:{port}")
    if auth_token:
        typer.echo("Auth: Bearer token required")
    else:
        typer.echo("Auth: none (open)")

    uvicorn.run(fastapi_app, host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
