"""rc-entitlement-gate — lightweight RevenueCat entitlement checker for agents."""

from .client import RCEntitlementClient
from .models import CheckResult, EntitlementStatus
from .sqlite_cache import SQLiteCache

__all__ = ["RCEntitlementClient", "CheckResult", "EntitlementStatus", "SQLiteCache"]
