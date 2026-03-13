"""rc-entitlement-gate — lightweight RevenueCat entitlement checker for agents."""

from .client import RCEntitlementClient
from .models import CheckResult, EntitlementStatus

__all__ = ["RCEntitlementClient", "CheckResult", "EntitlementStatus"]
