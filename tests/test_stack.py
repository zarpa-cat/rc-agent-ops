from unittest.mock import patch, MagicMock

import pytest
import respx
import httpx

from rc_agent_ops.config import AgentOpsConfig
from rc_agent_ops.stack import BillingStack


@pytest.fixture
def config():
    return AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
        budget_per_session=100,
        churnwall_url="https://churnwall.example.com",
    )


@pytest.fixture
def stack(config):
    return BillingStack(config)


def test_meter_for_budgeted(stack):
    from agent_billing_meter import BudgetedMeter

    meter = stack.meter_for("user_123")
    assert isinstance(meter, BudgetedMeter)


def test_meter_for_unbounded():
    from agent_billing_meter import BillingMeter, BudgetedMeter

    config = AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
    )
    stack = BillingStack(config)
    meter = stack.meter_for("user_123")
    assert isinstance(meter, BillingMeter)
    assert not isinstance(meter, BudgetedMeter)


def test_check_entitlement_granted(stack):
    mock_result = MagicMock()
    mock_result.granted = True
    with patch.object(stack.entitlement_client, "check", return_value=mock_result):
        assert stack.check_entitlement("user_123") is True


def test_check_entitlement_denied(stack):
    mock_result = MagicMock()
    mock_result.granted = False
    with patch.object(stack.entitlement_client, "check", return_value=mock_result):
        assert stack.check_entitlement("user_123") is False


@pytest.mark.asyncio
@respx.mock
async def test_sync_to_churnwall(stack):
    route = respx.post(
        "https://churnwall.example.com/api/v1/subscribers/user_123/sync"
    ).mock(return_value=httpx.Response(200))
    await stack.sync_to_churnwall("user_123")
    assert route.called


@pytest.mark.asyncio
async def test_sync_to_churnwall_no_url():
    config = AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
    )
    stack = BillingStack(config)
    # Should be a no-op, no error
    await stack.sync_to_churnwall("user_123")


@pytest.mark.asyncio
@respx.mock
async def test_sync_to_churnwall_failure_is_silent(stack):
    respx.post("https://churnwall.example.com/api/v1/subscribers/user_123/sync").mock(
        side_effect=httpx.ConnectError("down")
    )
    # Should not raise
    await stack.sync_to_churnwall("user_123")
