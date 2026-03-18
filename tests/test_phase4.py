"""Phase 4 tests: force_refresh, async entitlement check, SUSPECTED fix, op telemetry."""

from __future__ import annotations

import asyncio
import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rc_agent_ops.config import AgentOpsConfig
from rc_agent_ops.ops import AgentOps
from rc_agent_ops.risk import RiskTracker, SubscriberRisk
from rc_agent_ops.stack import BillingStack, OperationRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro_access")


@pytest.fixture
def stack(config):
    return BillingStack(config)


# ---------------------------------------------------------------------------
# force_refresh invalidates cache before check
# ---------------------------------------------------------------------------


def test_force_refresh_invalidates_cache(stack):
    """force_refresh=True should invalidate cache then call check normally."""
    mock_result = MagicMock()
    mock_result.granted = True

    with (
        patch.object(stack.entitlement_client.cache, "invalidate") as mock_invalidate,
        patch.object(
            stack.entitlement_client, "check", return_value=mock_result
        ) as mock_check,
    ):
        result = stack.check_entitlement("user_1", force_refresh=True)

    cache_key = stack.entitlement_client.cache._cache_key("user_1")
    mock_invalidate.assert_called_once_with(cache_key)
    mock_check.assert_called_once()
    assert result is True


def test_no_force_refresh_does_not_invalidate(stack):
    """Default (force_refresh=False) should not touch the cache."""
    mock_result = MagicMock()
    mock_result.granted = True

    with (
        patch.object(stack.entitlement_client.cache, "invalidate") as mock_invalidate,
        patch.object(stack.entitlement_client, "check", return_value=mock_result),
    ):
        stack.check_entitlement("user_1")

    mock_invalidate.assert_not_called()


# ---------------------------------------------------------------------------
# SUSPECTED path — cache bypass fix
# ---------------------------------------------------------------------------


def test_suspected_subscriber_bypasses_cache(config):
    """SUSPECTED subscribers must always get a fresh entitlement check."""
    stack = BillingStack(config)
    tracker = RiskTracker(db_path=":memory:")
    stack.risk_tracker = tracker
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")

    mock_result = MagicMock()
    mock_result.granted = True

    with (
        patch.object(stack.entitlement_client.cache, "invalidate") as mock_invalidate,
        patch.object(
            stack.entitlement_client, "check", return_value=mock_result
        ) as mock_check,
    ):
        result = stack.check_entitlement("user_1")

    # Cache must be invalidated before the check (this was the Phase 3 bug)
    cache_key = stack.entitlement_client.cache._cache_key("user_1")
    mock_invalidate.assert_called_once_with(cache_key)
    mock_check.assert_called_once()
    assert result is True


def test_suspected_subscriber_denied_when_check_fails(config):
    """SUSPECTED + revoked entitlement should return False."""
    stack = BillingStack(config)
    tracker = RiskTracker(db_path=":memory:")
    stack.risk_tracker = tracker
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")

    mock_result = MagicMock()
    mock_result.granted = False

    with (
        patch.object(stack.entitlement_client.cache, "invalidate"),
        patch.object(stack.entitlement_client, "check", return_value=mock_result),
    ):
        result = stack.check_entitlement("user_1")

    assert result is False


# ---------------------------------------------------------------------------
# Async entitlement check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_entitlement_async_returns_bool(stack):
    """check_entitlement_async should delegate to sync check via to_thread."""
    mock_result = MagicMock()
    mock_result.granted = True

    with patch.object(stack.entitlement_client, "check", return_value=mock_result):
        result = await stack.check_entitlement_async("user_1")

    assert result is True


@pytest.mark.asyncio
async def test_check_entitlement_async_force_refresh(stack):
    """check_entitlement_async propagates force_refresh to sync check."""
    mock_result = MagicMock()
    mock_result.granted = False

    with (
        patch.object(stack.entitlement_client.cache, "invalidate") as mock_invalidate,
        patch.object(stack.entitlement_client, "check", return_value=mock_result),
    ):
        result = await stack.check_entitlement_async("user_1", force_refresh=True)

    cache_key = stack.entitlement_client.cache._cache_key("user_1")
    mock_invalidate.assert_called_once_with(cache_key)
    assert result is False


# ---------------------------------------------------------------------------
# OperationRecord dataclass
# ---------------------------------------------------------------------------


def test_operation_record_defaults():
    rec = OperationRecord(op_name="search", cost=3)
    assert rec.op_name == "search"
    assert rec.cost == 3
    assert rec.ts > 0


# ---------------------------------------------------------------------------
# AgentOps operation telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_ops_tracks_operation_records(stack):
    """AgentOps should accumulate OperationRecord entries after each run()."""
    mock_meter = AsyncMock()
    mock_meter.__aenter__ = AsyncMock(return_value=mock_meter)
    mock_meter.__aexit__ = AsyncMock(return_value=False)
    mock_meter.debit = AsyncMock()

    mock_result = MagicMock()
    mock_result.granted = True

    with (
        patch.object(stack, "meter_for", return_value=mock_meter),
        patch.object(stack.entitlement_client, "check", return_value=mock_result),
        patch.object(stack, "sync_to_churnwall", new=AsyncMock()) as mock_sync,
    ):
        async with AgentOps(stack, "user_1") as ops:
            await ops.run("search", lambda: asyncio.noop(), cost=2)
            await ops.run("summarize", lambda: asyncio.noop(), cost=5)

        # Two ops recorded
        assert len(ops._ops) == 2
        assert ops._ops[0].op_name == "search"
        assert ops._ops[0].cost == 2
        assert ops._ops[1].op_name == "summarize"
        assert ops._ops[1].cost == 5

        # ops list passed to churnwall sync
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args
        passed_ops = call_kwargs[1].get("ops") or call_kwargs[0][1]
        assert len(passed_ops) == 2


# ---------------------------------------------------------------------------
# sync_to_churnwall with ops payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_to_churnwall_sends_ops_payload(config):
    """sync_to_churnwall should include ops list in POST body when provided."""
    import respx
    import httpx as _httpx

    config_with_churnwall = AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
        churnwall_url="http://churnwall.local",
    )
    stack = BillingStack(config_with_churnwall)
    ops = [
        OperationRecord(op_name="search", cost=2),
        OperationRecord(op_name="answer", cost=1),
    ]

    with respx.mock:
        route = respx.post(
            "http://churnwall.local/api/v1/subscribers/user_1/sync"
        ).mock(return_value=_httpx.Response(200))

        await stack.sync_to_churnwall("user_1", ops=ops)

    assert route.called
    request_body = route.calls[0].request
    body = _json.loads(request_body.content)
    assert body["total_cost"] == 3
    assert len(body["ops"]) == 2
    assert body["ops"][0]["op_name"] == "search"


@pytest.mark.asyncio
async def test_sync_to_churnwall_no_body_when_no_ops(config):
    """When no ops are provided, no JSON body should be sent."""
    import respx
    import httpx as _httpx

    config_with_churnwall = AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
        churnwall_url="http://churnwall.local",
    )
    stack = BillingStack(config_with_churnwall)

    with respx.mock:
        route = respx.post(
            "http://churnwall.local/api/v1/subscribers/user_1/sync"
        ).mock(return_value=_httpx.Response(200))

        await stack.sync_to_churnwall("user_1", ops=None)

    assert route.called
    # Body should be empty (None passed as json=None)
    assert route.calls[0].request.content == b""


# ---------------------------------------------------------------------------
# asyncio.noop shim for Python < 3.14
# ---------------------------------------------------------------------------

if not hasattr(asyncio, "noop"):

    async def _noop():
        return None

    asyncio.noop = _noop  # type: ignore[attr-defined]
