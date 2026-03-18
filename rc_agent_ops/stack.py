import httpx
from rc_entitlement_gate import RCEntitlementClient
from agent_billing_meter import BillingMeter, BudgetedMeter, PolicyMeter, SpendPolicy

from .config import AgentOpsConfig
from .risk import RiskTracker, SubscriberRisk


class BillingStack:
    def __init__(self, config: AgentOpsConfig):
        self.config = config
        self.entitlement_client = RCEntitlementClient(
            api_key=config.rc_api_key,
            cache_ttl=config.entitlement_cache_ttl,
        )
        if config.risk_db_path:
            self.risk_tracker: RiskTracker | None = RiskTracker(
                config.risk_db_path
            )
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
        common_kwargs = dict(
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

    def check_entitlement(self, subscriber_id: str) -> bool:
        if self.risk_tracker is not None:
            risk = self.risk_tracker.get(subscriber_id)
            if risk == SubscriberRisk.BLOCKED:
                return False
            if risk == SubscriberRisk.SUSPECTED:
                fresh = self.entitlement_client.check(
                    subscriber_id=subscriber_id,
                    entitlement=self.config.entitlement_id,
                    use_cache=False,
                )
                return fresh.granted
        result = self.entitlement_client.check(
            subscriber_id=subscriber_id,
            entitlement=self.config.entitlement_id,
        )
        return result.granted

    async def sync_to_churnwall(self, subscriber_id: str) -> None:
        if not self.config.churnwall_url:
            return
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"{self.config.churnwall_url.rstrip('/')}/api/v1/subscribers/{subscriber_id}/sync",
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
