from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx
from rc_entitlement_gate import RCEntitlementClient
from agent_billing_meter import BillingMeter, BudgetedMeter, PolicyMeter, SpendPolicy

from .config import AgentOpsConfig
from .risk import RiskTracker, SubscriberRisk


@dataclass
class OperationRecord:
    """Record of a single billed operation within an AgentOps session."""

    op_name: str
    cost: int
    ts: float = field(default_factory=time.time)


class BillingStack:
    def __init__(self, config: AgentOpsConfig):
        self.config = config
        self.entitlement_client = RCEntitlementClient(
            api_key=config.rc_api_key,
            cache_ttl=config.entitlement_cache_ttl,
        )
        if config.risk_db_path:
            self.risk_tracker: RiskTracker | None = RiskTracker(config.risk_db_path)
        else:
            self.risk_tracker = None

    def _make_spend_policy(self) -> SpendPolicy | None:
        if self.config.spend_policy is None:
            return None
        c = self.config.spend_policy
        return SpendPolicy(
            blocked_ops=c.blocked_ops,
            allowed_ops=c.allowed_ops,
            op_max_per_call=c.op_max_per_call,
            op_max_per_hour=c.op_max_per_hour,
            max_per_hour=c.max_per_hour,
            max_per_day=c.max_per_day,
        )

    def meter_for(
        self, subscriber_id: str
    ) -> BillingMeter | BudgetedMeter | PolicyMeter:
        common_kwargs: dict = dict(
            api_key=self.config.rc_api_key,
            app_user_id=subscriber_id,
            currency=self.config.currency,
        )
        if self.config.audit_db_path:
            common_kwargs["audit_db"] = self.config.audit_db_path

        policy = self._make_spend_policy()

        if policy is not None:
            return PolicyMeter(policy=policy, **common_kwargs)
        if self.config.budget_per_session is not None:
            return BudgetedMeter(budget=self.config.budget_per_session, **common_kwargs)
        return BillingMeter(**common_kwargs)

    def _invalidate_entitlement_cache(self, subscriber_id: str) -> None:
        """Remove subscriber's cached entitlement data, forcing a fresh RC fetch."""
        cache_key = self.entitlement_client.cache._cache_key(subscriber_id)
        self.entitlement_client.cache.invalidate(cache_key)

    def check_entitlement(
        self, subscriber_id: str, force_refresh: bool = False
    ) -> bool:
        """Check if a subscriber is entitled.

        Args:
            subscriber_id: The subscriber to check.
            force_refresh: If True, bypass the entitlement cache and fetch
                fresh data from RevenueCat. Use this when you have reason to
                believe the cached result is stale (e.g., after a webhook event).
        """
        if force_refresh:
            self._invalidate_entitlement_cache(subscriber_id)

        if self.risk_tracker is not None:
            risk = self.risk_tracker.get(subscriber_id)
            if risk == SubscriberRisk.BLOCKED:
                return False
            if risk == SubscriberRisk.SUSPECTED:
                # Always bypass cache for suspected subscribers — their billing
                # status may have changed since the cache was populated.
                self._invalidate_entitlement_cache(subscriber_id)
                result = self.entitlement_client.check(
                    subscriber_id=subscriber_id,
                    entitlement=self.config.entitlement_id,
                )
                return result.granted

        result = self.entitlement_client.check(
            subscriber_id=subscriber_id,
            entitlement=self.config.entitlement_id,
        )
        return result.granted

    async def check_entitlement_async(
        self, subscriber_id: str, force_refresh: bool = False
    ) -> bool:
        """Async variant of check_entitlement.

        Wraps the sync entitlement check in asyncio.to_thread() so callers
        in async contexts don't block the event loop during the RC API call.
        """
        return await asyncio.to_thread(
            self.check_entitlement, subscriber_id, force_refresh
        )

    async def sync_to_churnwall(
        self,
        subscriber_id: str,
        ops: list[OperationRecord] | None = None,
    ) -> None:
        """Trigger a churnwall sync for the subscriber.

        Args:
            subscriber_id: The subscriber to sync.
            ops: Optional list of operation records from this session. If
                provided, they are included in the sync payload so churnwall
                can build an event-level audit trail without a separate RC
                fetch.
        """
        if not self.config.churnwall_url:
            return
        payload: dict = {}
        if ops:
            payload["ops"] = [
                {"op_name": r.op_name, "cost": r.cost, "ts": r.ts} for r in ops
            ]
            payload["total_cost"] = sum(r.cost for r in ops)
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"{self.config.churnwall_url.rstrip('/')}/api/v1/subscribers/{subscriber_id}/sync",
                    json=payload or None,
                    timeout=5.0,
                )
            except (httpx.RequestError, httpx.HTTPStatusError):
                pass  # churnwall sync is best-effort

    async def health(self) -> dict:
        """Check connectivity to RevenueCat and return a health summary."""
        results: dict = {
            "rc_api": False,
            "entitlement_gate": False,
            "churnwall": None,
        }
        # Check RC API directly
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.revenuecat.com/v1/subscribers/__health__",
                    headers={"Authorization": f"Bearer {self.config.rc_api_key}"},
                    timeout=5.0,
                )
                # 200 or 404 both mean RC is reachable
                results["rc_api"] = r.status_code in (200, 404)
        except httpx.RequestError:
            results["rc_api"] = False

        # Check entitlement gate via a test lookup
        try:
            self.entitlement_client.check(
                subscriber_id="__health__",
                entitlement=self.config.entitlement_id,
            )
            results["entitlement_gate"] = True
        except Exception as e:
            # 404 (subscriber not found) still proves the gate works
            results["entitlement_gate"] = "not found" in str(e).lower() or "404" in str(
                e
            )

        # Check churnwall if configured
        if self.config.churnwall_url:
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(
                        f"{self.config.churnwall_url.rstrip('/')}/health",
                        timeout=5.0,
                    )
                    results["churnwall"] = r.status_code == 200
            except httpx.RequestError:
                results["churnwall"] = False

        return results
