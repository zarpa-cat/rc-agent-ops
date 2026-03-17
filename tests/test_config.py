import pytest
from rc_agent_ops.config import AgentOpsConfig


def test_valid_config():
    cfg = AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro")
    assert cfg.rc_api_key == "sk_test"
    assert cfg.currency == "AI_CREDITS"
    assert cfg.op_costs == {}
    assert cfg.budget_per_session is None


def test_missing_api_key():
    with pytest.raises(ValueError, match="rc_api_key"):
        AgentOpsConfig(rc_api_key="", entitlement_id="pro")


def test_missing_entitlement_id():
    with pytest.raises(ValueError, match="entitlement_id"):
        AgentOpsConfig(rc_api_key="sk_test", entitlement_id="")


def test_custom_op_costs():
    cfg = AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro",
        op_costs={"summarize": 5, "translate": 3},
    )
    assert cfg.op_costs["summarize"] == 5
