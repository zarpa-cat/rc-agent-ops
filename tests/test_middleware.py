"""Tests for the FastAPI/Starlette AgentOpsMiddleware."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rc_agent_ops.config import AgentOpsConfig
from rc_agent_ops.middleware import AgentOpsMiddleware
from rc_agent_ops.stack import BillingStack


def make_app(config: AgentOpsConfig, **middleware_kwargs) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AgentOpsMiddleware, config=config, **middleware_kwargs)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/data")
    def data():
        return {"value": 42}

    @app.get("/api/error")
    def error_route():
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="boom")

    return app


@pytest.fixture
def config():
    return AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro_access")


@pytest.fixture
def app(config):
    return make_app(config)


def test_health_path_bypasses_middleware(app):
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200


def test_no_subscriber_header_passes_through(app):
    with TestClient(app) as client:
        r = client.get("/api/data")
    assert r.status_code == 200


def test_denied_entitlement_returns_402(app, config):
    with patch.object(BillingStack, "check_entitlement", return_value=False):
        with TestClient(app) as client:
            r = client.get("/api/data", headers={"X-Subscriber-Id": "user_123"})
    assert r.status_code == 402
    assert r.json()["error"] == "entitlement_required"


def test_granted_entitlement_passes(app, config):
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)
    mock_meter.debit = AsyncMock()

    with (
        patch.object(BillingStack, "check_entitlement", return_value=True),
        patch.object(BillingStack, "meter_for", return_value=mock_meter),
        patch.object(BillingStack, "sync_to_churnwall", new_callable=AsyncMock),
    ):
        with TestClient(app) as client:
            r = client.get("/api/data", headers={"X-Subscriber-Id": "user_123"})
    assert r.status_code == 200
    assert r.json() == {"value": 42}


def test_billing_not_called_on_5xx(app, config):
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)
    mock_meter.debit = AsyncMock()

    with (
        patch.object(BillingStack, "check_entitlement", return_value=True),
        patch.object(BillingStack, "meter_for", return_value=mock_meter),
        patch.object(
            BillingStack, "sync_to_churnwall", new_callable=AsyncMock
        ) as mock_sync,
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/api/error", headers={"X-Subscriber-Id": "user_123"})
    # 500 should not trigger billing or churnwall sync
    mock_meter.debit.assert_not_called()
    mock_sync.assert_not_called()


def test_custom_subscriber_header(config):
    app = make_app(config, subscriber_id_header="X-User")
    with (
        patch.object(BillingStack, "check_entitlement", return_value=False),
    ):
        with TestClient(app) as client:
            r = client.get("/api/data", headers={"X-User": "user_abc"})
    assert r.status_code == 402


def test_custom_skip_paths(config):
    app = make_app(config, skip_paths=["/api/data"])
    with TestClient(app) as client:
        r = client.get("/api/data")
    assert r.status_code == 200


def test_op_name_fn(config):
    """The middleware uses the custom op_name_fn to name the billing operation."""
    captured_op: dict = {}

    def make_meter(subscriber_id):
        m = AsyncMock()
        m.__aenter__ = AsyncMock(return_value=m)
        m.__aexit__ = AsyncMock(return_value=False)

        async def capture_debit(**kwargs):
            captured_op["name"] = kwargs.get("operation")

        m.debit = capture_debit
        return m

    def custom_op_fn(req):
        return "custom_op"

    app = make_app(config, op_name_fn=custom_op_fn)

    with (
        patch.object(BillingStack, "check_entitlement", return_value=True),
        patch.object(BillingStack, "meter_for", side_effect=make_meter),
        patch.object(BillingStack, "sync_to_churnwall", new_callable=AsyncMock),
    ):
        with TestClient(app) as client:
            client.get("/api/data", headers={"X-Subscriber-Id": "user_123"})

    assert captured_op.get("name") == "custom_op"
