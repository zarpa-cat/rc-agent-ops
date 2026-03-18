from unittest.mock import patch, MagicMock

import pytest

from rc_agent_ops.config import AgentOpsConfig
from rc_agent_ops.risk import RiskTracker, SubscriberRisk
from rc_agent_ops.stack import BillingStack


@pytest.fixture
def config():
    return AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro_access",
    )


@pytest.fixture
def tracker():
    return RiskTracker(db_path=":memory:")


def test_stack_blocked_denies_without_api(config, tracker):
    stack = BillingStack(config)
    stack.risk_tracker = tracker
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")

    with patch.object(stack.entitlement_client, "check") as mock_check:
        result = stack.check_entitlement("user_1")

    assert result is False
    mock_check.assert_not_called()


def test_stack_clean_uses_normal_path(config, tracker):
    stack = BillingStack(config)
    stack.risk_tracker = tracker
    tracker.mark("user_1", SubscriberRisk.CLEAN, "renewed")

    mock_result = MagicMock()
    mock_result.granted = True
    with patch.object(
        stack.entitlement_client, "check", return_value=mock_result
    ) as mock_check:
        result = stack.check_entitlement("user_1")

    assert result is True
    mock_check.assert_called_once()
