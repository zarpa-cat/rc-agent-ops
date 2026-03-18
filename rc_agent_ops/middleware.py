"""
FastAPI middleware for automatic entitlement + billing enforcement.

Usage:
    from rc_agent_ops.middleware import AgentOpsMiddleware, AgentOpsConfig

    app = FastAPI()
    app.add_middleware(
        AgentOpsMiddleware,
        config=AgentOpsConfig(
            rc_api_key="sk_...",
            entitlement_id="pro_access",
        ),
        subscriber_id_header="X-Subscriber-Id",
        op_name_fn=lambda req: req.url.path.strip("/").replace("/", "."),
        op_cost=1,
    )
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import AgentOpsConfig
from .errors import EntitlementDenied
from .stack import BillingStack


class AgentOpsMiddleware(BaseHTTPMiddleware):
    """
    Starlette/FastAPI middleware that:
      1. Reads subscriber_id from a request header.
      2. Checks entitlement via rc-entitlement-gate.
      3. Runs the route handler.
      4. Debits billing-meter on success (fires AFTER, never on error).
      5. Syncs to churnwall after each successful request.

    Non-subscriber routes (no header) pass through unchanged.
    """

    def __init__(
        self,
        app: Any,
        config: AgentOpsConfig,
        subscriber_id_header: str = "X-Subscriber-Id",
        op_name_fn: Callable[[Request], str] | None = None,
        op_cost: int = 1,
        skip_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.stack = BillingStack(config)
        self.subscriber_id_header = subscriber_id_header
        self.op_name_fn = op_name_fn or (
            lambda req: req.url.path.strip("/").replace("/", ".") or "request"
        )
        self.op_cost = op_cost
        self.skip_paths = set(skip_paths or ["/health", "/healthz", "/metrics"])

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # Skip health/metrics paths
        if request.url.path in self.skip_paths:
            return await call_next(request)

        subscriber_id = request.headers.get(self.subscriber_id_header)

        # No subscriber header — pass through (unauthenticated route)
        if not subscriber_id:
            return await call_next(request)

        # Entitlement check
        try:
            granted = self.stack.check_entitlement(subscriber_id)
        except Exception:
            granted = False

        if not granted:
            return JSONResponse(
                status_code=402,
                content={
                    "error": "entitlement_required",
                    "entitlement": self.stack.config.entitlement_id,
                    "subscriber_id": subscriber_id,
                },
            )

        op_name = self.op_name_fn(request)

        # Run handler
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Bill on success (2xx only)
        if 200 <= response.status_code < 300:
            try:
                meter = self.stack.meter_for(subscriber_id)
                async with meter:
                    await meter.debit(
                        amount=self.op_cost,
                        operation=op_name,
                        metadata={
                            "status_code": response.status_code,
                            "elapsed_ms": elapsed_ms,
                        },
                    )
            except EntitlementDenied:
                # Budget exceeded — still return the response but log
                pass
            except Exception:
                pass  # billing is best-effort; don't break the response

            # Churnwall sync is fire-and-forget
            await self.stack.sync_to_churnwall(subscriber_id)

        return response
