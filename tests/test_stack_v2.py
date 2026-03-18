"""Phase 2 stack tests: SpendPolicy integration and health check."""

import pytest
import respx
import httpx
from unittest.mock import MagicMock, patch

from rc_agent_ops.config import AgentOpsConfig, SpendPolicyConfig
from rc_agent_ops.stack import BillingStack


@pytest.fixture
def config_with_policy():
    return AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
        spend_policy=SpendPolicyConfig(max_per_hour=500, max_per_day=2000),
    )


def test_meter_for_returns_policy_meter_when_policy_set(config_with_policy):
    from agent_billing_meter import PolicyMeter

    stack = BillingStack(config_with_policy)
    meter = stack.meter_for("user_123")
    assert isinstance(meter, PolicyMeter)


def test_meter_for_returns_billing_meter_without_policy():
    from agent_billing_meter import BillingMeter, BudgetedMeter, PolicyMeter

    config = AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro")
    stack = BillingStack(config)
    meter = stack.meter_for("user_123")
    assert isinstance(meter, BillingMeter)
    assert not isinstance(meter, BudgetedMeter)
    assert not isinstance(meter, PolicyMeter)


def test_make_spend_policy_maps_config(config_with_policy):
    stack = BillingStack(config_with_policy)
    policy = stack._make_spend_policy()
    assert policy is not None
    assert policy.max_per_hour == 500
    assert policy.max_per_day == 2000


def test_make_spend_policy_none_when_no_policy():
    config = AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro")
    stack = BillingStack(config)
    assert stack._make_spend_policy() is None


@pytest.mark.asyncio
@respx.mock
async def test_health_rc_api_ok():
    config = AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro_access")
    stack = BillingStack(config)
    respx.get("https://api.revenuecat.com/v1/subscribers/__health__").mock(
        return_value=httpx.Response(404)
    )
    mock_result = MagicMock()
    mock_result.granted = False
    with patch.object(stack.entitlement_client, "check", return_value=mock_result):
        result = await stack.health()
    assert result["rc_api"] is True
    assert result["churnwall"] is None


@pytest.mark.asyncio
@respx.mock
async def test_health_rc_api_unreachable():
    config = AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro_access")
    stack = BillingStack(config)
    respx.get("https://api.revenuecat.com/v1/subscribers/__health__").mock(
        side_effect=httpx.ConnectError("down")
    )
    mock_result = MagicMock()
    mock_result.granted = False
    with patch.object(stack.entitlement_client, "check", return_value=mock_result):
        result = await stack.health()
    assert result["rc_api"] is False


@pytest.mark.asyncio
@respx.mock
async def test_health_checks_churnwall():
    config = AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
        churnwall_url="https://cw.example.com",
    )
    stack = BillingStack(config)
    respx.get("https://api.revenuecat.com/v1/subscribers/__health__").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://cw.example.com/health").mock(return_value=httpx.Response(200))
    mock_result = MagicMock()
    mock_result.granted = False
    with patch.object(stack.entitlement_client, "check", return_value=mock_result):
        result = await stack.health()
    assert result["churnwall"] is True
