from __future__ import annotations

import functools
from typing import Any, Callable, Awaitable

from .errors import EntitlementDenied
from .stack import BillingStack, OperationRecord


class AgentOps:
    def __init__(self, stack: BillingStack, subscriber_id: str):
        self.stack = stack
        self.subscriber_id = subscriber_id
        self._meter = None
        self._entitlement_checked = False
        self._entitlement_granted = False
        self._ops: list[OperationRecord] = []

    async def __aenter__(self):
        self._meter = self.stack.meter_for(self.subscriber_id)
        await self._meter.__aenter__()
        # Check entitlement asynchronously on enter — avoids blocking the
        # event loop when the RC API call is needed (cache miss or SUSPECTED).
        self._entitlement_granted = await self.stack.check_entitlement_async(
            self.subscriber_id
        )
        self._entitlement_checked = True
        if not self._entitlement_granted:
            await self._meter.__aexit__(None, None, None)
            raise EntitlementDenied(
                subscriber_id=self.subscriber_id,
                entitlement_id=self.stack.config.entitlement_id,
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._meter.__aexit__(exc_type, exc_val, exc_tb)
        if exc_type is None:
            # Pass accumulated op records so churnwall gets session context
            await self.stack.sync_to_churnwall(
                self.subscriber_id, ops=self._ops or None
            )
        return False

    async def run(
        self,
        op_name: str,
        fn: Callable[[], Awaitable[Any]],
        cost: int | None = None,
    ) -> Any:
        if not self._entitlement_granted:
            raise EntitlementDenied(
                subscriber_id=self.subscriber_id,
                entitlement_id=self.stack.config.entitlement_id,
            )
        actual_cost = (
            cost if cost is not None else self.stack.config.op_costs.get(op_name, 1)
        )
        result = await fn()
        await self._meter.debit(amount=actual_cost, operation=op_name)
        self._ops.append(OperationRecord(op_name=op_name, cost=actual_cost))
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
