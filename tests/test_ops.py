from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rc_agent_ops.config import AgentOpsConfig
from rc_agent_ops.errors import EntitlementDenied
from rc_agent_ops.ops import AgentOps, agent_op
from rc_agent_ops.stack import BillingStack


@pytest.fixture
def config():
    return AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
        op_costs={"summarize": 5},
    )


@pytest.fixture
def stack(config):
    return BillingStack(config)


@pytest.mark.asyncio
async def test_agent_ops_run_success(stack):
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)
    mock_meter.debit = AsyncMock()

    with (
        patch.object(stack, "meter_for", return_value=mock_meter),
        patch.object(stack, "check_entitlement", return_value=True),
        patch.object(stack, "sync_to_churnwall", new_callable=AsyncMock),
    ):
        async with AgentOps(stack, "user_123") as ops:
            result = await ops.run("summarize", lambda: async_return("done"))
            assert result == "done"
            mock_meter.debit.assert_called_once_with(amount=5, operation="summarize")


@pytest.mark.asyncio
async def test_agent_ops_default_cost(stack):
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)
    mock_meter.debit = AsyncMock()

    with (
        patch.object(stack, "meter_for", return_value=mock_meter),
        patch.object(stack, "check_entitlement", return_value=True),
        patch.object(stack, "sync_to_churnwall", new_callable=AsyncMock),
    ):
        async with AgentOps(stack, "user_123") as ops:
            await ops.run("unknown_op", lambda: async_return("ok"))
            mock_meter.debit.assert_called_once_with(amount=1, operation="unknown_op")


@pytest.mark.asyncio
async def test_agent_ops_entitlement_denied(stack):
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(stack, "meter_for", return_value=mock_meter),
        patch.object(stack, "check_entitlement", return_value=False),
    ):
        async with AgentOps(stack, "user_123") as ops:
            with pytest.raises(EntitlementDenied):
                await ops.run("summarize", lambda: async_return("nope"))


@pytest.mark.asyncio
async def test_agent_ops_caches_entitlement(stack):
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)
    mock_meter.debit = AsyncMock()

    check_mock = MagicMock(return_value=True)
    with (
        patch.object(stack, "meter_for", return_value=mock_meter),
        patch.object(stack, "check_entitlement", check_mock),
        patch.object(stack, "sync_to_churnwall", new_callable=AsyncMock),
    ):
        async with AgentOps(stack, "user_123") as ops:
            await ops.run("op1", lambda: async_return("a"))
            await ops.run("op2", lambda: async_return("b"))
            # Entitlement should only be checked once
            assert check_mock.call_count == 1


@pytest.mark.asyncio
async def test_agent_op_decorator(stack):
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)
    mock_meter.debit = AsyncMock()

    with (
        patch.object(stack, "meter_for", return_value=mock_meter),
        patch.object(stack, "check_entitlement", return_value=True),
        patch.object(stack, "sync_to_churnwall", new_callable=AsyncMock),
    ):

        @agent_op(stack, "user_123", "translate", cost=3)
        async def translate(text: str) -> str:
            return f"translated: {text}"

        result = await translate("hello")
        assert result == "translated: hello"
        mock_meter.debit.assert_called_once_with(amount=3, operation="translate")


async def async_return(value):
    return value
