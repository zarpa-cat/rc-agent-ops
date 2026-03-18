import hashlib
import hmac
import json

import pytest

from rc_agent_ops.risk import RiskTracker, SubscriberRisk
from rc_agent_ops.webhook import RCWebhookHandler


@pytest.fixture
def tracker():
    return RiskTracker(db_path=":memory:")


@pytest.fixture
def handler(tracker):
    return RCWebhookHandler(risk_tracker=tracker)


def _payload(event_type: str, app_user_id: str = "user_1") -> dict:
    return {"event": {"type": event_type, "app_user_id": app_user_id}}


def test_webhook_billing_issue(handler, tracker):
    result = handler.handle(_payload("BILLING_ISSUE_DETECTED_FOR_CUSTOMER"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.SUSPECTED


def test_webhook_cancellation(handler, tracker):
    result = handler.handle(_payload("CANCELLATION"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.BLOCKED


def test_webhook_expiration(handler, tracker):
    result = handler.handle(_payload("EXPIRATION"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.BLOCKED


def test_webhook_renewal(handler, tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    result = handler.handle(_payload("RENEWAL"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_webhook_uncancellation(handler, tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "cancelled")
    result = handler.handle(_payload("UNCANCELLATION"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_webhook_initial_purchase(handler, tracker):
    result = handler.handle(_payload("INITIAL_PURCHASE"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_webhook_unknown_event(handler):
    result = handler.handle(_payload("SOME_UNKNOWN_EVENT"))
    assert result["processed"] is False
    assert "unhandled" in result["reason"].lower()


def test_webhook_missing_event_type(handler):
    result = handler.handle({"event": {}})
    assert result["processed"] is False


def test_webhook_auth_valid(tracker):
    auth_key = "secret123"
    handler = RCWebhookHandler(risk_tracker=tracker, auth_key=auth_key)
    body = json.dumps(_payload("RENEWAL")).encode()
    sig = hmac.new(auth_key.encode(), body, hashlib.sha256).hexdigest()
    result = handler.handle_with_auth(_payload("RENEWAL"), signature=sig, raw_body=body)
    assert result["processed"] is True


def test_webhook_auth_invalid(tracker):
    auth_key = "secret123"
    handler = RCWebhookHandler(risk_tracker=tracker, auth_key=auth_key)
    body = json.dumps(_payload("RENEWAL")).encode()
    result = handler.handle_with_auth(
        _payload("RENEWAL"), signature="badsig", raw_body=body
    )
    assert result["processed"] is False
    assert "signature" in result.get("reason", "").lower()
