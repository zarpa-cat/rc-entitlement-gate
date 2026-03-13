# rc-entitlement-gate

**Lightweight RevenueCat entitlement checker for agents and backend services.**

RC's SDKs are mobile-first. `rc-entitlement-gate` gives server-side code and AI agents a simple, fast way to check subscriber entitlements — with caching and scriptable exit codes.

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

# JSON output for agent consumption
entgate check user_123 premium --json
```

### Python

```python
from rc_entitlement_gate import RCEntitlementClient

with RCEntitlementClient(api_key="sk_...") as client:
    result = client.check("user_123", "premium")
    if result:
        # access granted
        print(result.entitlement_detail.expires_date)
```

## Features

- **Caching**: RC API calls cached in-memory (configurable TTL, default 60s)
- **Exit codes**: `0` granted / `1` denied / `2` error — scriptable in CI and agent loops
- **JSON output**: machine-readable for agent pipelines
- **Batch checking**: check multiple subscribers in one command
- **Cache invalidation**: call `client.invalidate(subscriber_id)` after webhook events

## Configuration

```bash
export REVENUECAT_API_KEY="sk_..."
```

Or pass `--api-key` / `api_key=` directly.

## License

MIT
