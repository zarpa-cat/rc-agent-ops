import httpx
from rc_entitlement_gate import RCEntitlementClient, CheckResult
from agent_billing_meter import BillingMeter, BudgetedMeter

from .config import AgentOpsConfig


class BillingStack:
    def __init__(self, config: AgentOpsConfig):
        self.config = config
        self.entitlement_client = RCEntitlementClient(
            api_key=config.rc_api_key,
            cache_ttl=config.entitlement_cache_ttl,
        )

    def meter_for(self, subscriber_id: str) -> BillingMeter | BudgetedMeter:
        if self.config.budget_per_session is not None:
            return BudgetedMeter(
                api_key=self.config.rc_api_key,
                app_user_id=subscriber_id,
                currency=self.config.currency,
                budget=self.config.budget_per_session,
            )
        return BillingMeter(
            api_key=self.config.rc_api_key,
            app_user_id=subscriber_id,
            currency=self.config.currency,
        )

    def check_entitlement(self, subscriber_id: str) -> bool:
        result: CheckResult = self.entitlement_client.check(
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
