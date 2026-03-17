import functools
from typing import Any, Callable, Awaitable

from .errors import EntitlementDenied
from .stack import BillingStack


class AgentOps:
    def __init__(self, stack: BillingStack, subscriber_id: str):
        self.stack = stack
        self.subscriber_id = subscriber_id
        self._meter = None
        self._entitlement_checked = False
        self._entitlement_granted = False

    async def __aenter__(self):
        self._meter = self.stack.meter_for(self.subscriber_id)
        await self._meter.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._meter.__aexit__(exc_type, exc_val, exc_tb)
        if exc_type is None:
            await self.stack.sync_to_churnwall(self.subscriber_id)
        return False

    def _ensure_entitlement(self):
        if not self._entitlement_checked:
            self._entitlement_granted = self.stack.check_entitlement(self.subscriber_id)
            self._entitlement_checked = True
        if not self._entitlement_granted:
            raise EntitlementDenied(
                subscriber_id=self.subscriber_id,
                entitlement_id=self.stack.config.entitlement_id,
            )

    async def run(
        self,
        op_name: str,
        fn: Callable[[], Awaitable[Any]],
        cost: int | None = None,
    ) -> Any:
        self._ensure_entitlement()
        actual_cost = (
            cost if cost is not None else self.stack.config.op_costs.get(op_name, 1)
        )
        result = await fn()
        await self._meter.debit(amount=actual_cost, operation=op_name)
        return result


def agent_op(stack: BillingStack, subscriber_id: str, op_name: str, cost: int = 1):
    """Decorator that wraps an async function as a billed agent operation."""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            async with AgentOps(stack, subscriber_id) as ops:
                return await ops.run(op_name, lambda: fn(*args, **kwargs), cost=cost)

        return wrapper

    return decorator
