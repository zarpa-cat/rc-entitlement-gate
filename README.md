# rc-entitlement-gate

**Lightweight RevenueCat entitlement checker for agents and backend services.**

RC's SDKs are mobile-first. `rc-entitlement-gate` gives server-side code and AI agents a simple, fast way to check subscriber entitlements — with caching, offline fallback, and scriptable exit codes.

## Install

```bash
pip install rc-entitlement-gate
```

## Usage

### CLI

```bash
# Single check (exit 0=granted, 1=denied, 2=error)
entgate check user_123 premium --api-key sk_...

# Subscriber summary
entgate info user_123

# Batch check — exits 0 only if all granted
entgate batch premium --ids user_1,user_2,user_3

# JSON output for agent pipelines
entgate check user_123 premium --json

# Webhook invalidation server
entgate webhook-server --port 8080 --auth-token $WEBHOOK_SECRET
```

### Python

```python
from rc_entitlement_gate import RCEntitlementClient

with RCEntitlementClient(
    api_key="sk_...",
    cache_ttl=60,                        # cache for 60s
    offline_fallback=True,               # serve stale cache if RC is down
    stale_window_seconds=300,            # keep stale entries for 5min
    expires_soon_threshold_seconds=86400 # flag entitlements expiring within 24h
) as client:
    result = client.check("user_123", "premium")

    if result:
        print(f"Granted: {result.status}")

        if result.expires_soon:
            print(f"Expiring in {result.expires_in_seconds}s — consider proactive renewal")

        if result.stale:
            print("Warning: served from stale cache (RC offline fallback)")
```

### Webhook server (Python)

Keep your entitlement cache fresh by hooking into RC's webhook events:

```python
from rc_entitlement_gate import RCEntitlementClient
from rc_entitlement_gate.webhook_server import create_app
import uvicorn

client = RCEntitlementClient(api_key="sk_...", cache_ttl=300)
app = create_app(rc_client=client, auth_token="your_webhook_secret")
uvicorn.run(app, host="0.0.0.0", port=8080)
```

Set `POST https://your-server:8080/webhook` as your RC webhook endpoint.
Add `Authorization: Bearer your_webhook_secret` in the RC dashboard.

## Features

- **Caching**: RC API calls cached in-memory (configurable TTL, default 60s)
- **Exit codes**: `0` granted / `1` denied / `2` error — scriptable in CI and agent loops
- **JSON output**: machine-readable for agent pipelines
- **Batch checking**: check multiple subscribers in one command
- **Offline fallback**: serve last-known-good from stale cache when RC API is unreachable
- **Expiry warnings**: flag entitlements expiring within a configurable threshold
- **Webhook server**: FastAPI endpoint that auto-invalidates cache on RC subscription events
- **Cache invalidation**: call `client.invalidate(subscriber_id)` manually after events

## Configuration

```bash
export REVENUECAT_API_KEY="sk_..."
export WEBHOOK_AUTH_TOKEN="your_webhook_secret"   # optional, for webhook-server
```

Or pass `--api-key` / `api_key=` directly.

## Status codes

| `result.status` | `bool(result)` | Meaning |
|---|---|---|
| `granted` | `True` | Entitlement is active |
| `denied` | `False` | Subscriber exists but doesn't have this entitlement |
| `not_found` | `False` | Subscriber not found in RC |
| `stale` | `True` | Served from stale cache (offline fallback — RC was unreachable) |
| `error` | `False` | Upstream error, no fallback available |

## License

MIT
