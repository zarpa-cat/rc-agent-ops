from __future__ import annotations

import hashlib
import hmac

from .risk import RiskTracker, SubscriberRisk

# RC webhook event type → risk level
_EVENT_RISK_MAP: dict[str, SubscriberRisk] = {
    "BILLING_ISSUE_DETECTED_FOR_CUSTOMER": SubscriberRisk.SUSPECTED,
    "EXPIRATION": SubscriberRisk.BLOCKED,
    "CANCELLATION": SubscriberRisk.BLOCKED,
    "RENEWAL": SubscriberRisk.CLEAN,
    "UNCANCELLATION": SubscriberRisk.CLEAN,
    "INITIAL_PURCHASE": SubscriberRisk.CLEAN,
}


class RCWebhookHandler:
    def __init__(
        self, risk_tracker: RiskTracker, auth_key: str | None = None
    ) -> None:
        self.risk_tracker = risk_tracker
        self.auth_key = auth_key

    def handle(self, payload: dict) -> dict:
        event = payload.get("event", {})
        event_type = event.get("type")
        subscriber_id = event.get("app_user_id")

        if not event_type:
            return {"processed": False, "reason": "missing event type"}

        risk = _EVENT_RISK_MAP.get(event_type)
        if risk is None:
            return {
                "processed": False,
                "reason": f"unhandled event type: {event_type}",
            }

        if not subscriber_id:
            return {"processed": False, "reason": "missing app_user_id"}

        self.risk_tracker.mark(subscriber_id, risk, event_type)
        return {
            "processed": True,
            "subscriber_id": subscriber_id,
            "action": f"marked {risk.value}",
        }

    def handle_with_auth(
        self, payload: dict, *, signature: str, raw_body: bytes
    ) -> dict:
        if self.auth_key:
            expected = hmac.new(
                self.auth_key.encode(), raw_body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return {"processed": False, "reason": "invalid signature"}
        return self.handle(payload)


def make_webhook_router(handler: RCWebhookHandler):
    """Create a FastAPI APIRouter for RC webhooks."""
    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as e:
        raise ImportError(
            "fastapi is required for make_webhook_router. "
            "Install with: pip install rc-agent-ops[fastapi]"
        ) from e

    router = APIRouter()

    @router.post("/webhook/rc")
    async def rc_webhook(request: Request):
        raw_body = await request.body()
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "malformed payload"}, status_code=400
            )

        event = payload.get("event", {})
        if not event.get("type"):
            return JSONResponse(
                {"error": "missing event.type"}, status_code=400
            )

        if handler.auth_key:
            sig = request.headers.get("RC-Billing-Signature", "")
            result = handler.handle_with_auth(
                payload, signature=sig, raw_body=raw_body
            )
            if (
                not result["processed"]
                and "signature" in result.get("reason", "")
            ):
                return JSONResponse(
                    {"error": "invalid signature"}, status_code=401
                )
            return result

        return handler.handle(payload)

    return router
