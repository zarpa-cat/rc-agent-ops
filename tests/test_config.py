import pytest
from rc_agent_ops.config import AgentOpsConfig, SpendPolicyConfig


def test_config_requires_api_key():
    with pytest.raises(ValueError, match="rc_api_key"):
        AgentOpsConfig(rc_api_key="", entitlement_id="pro")


def test_config_requires_entitlement_id():
    with pytest.raises(ValueError, match="entitlement_id"):
        AgentOpsConfig(rc_api_key="sk_test", entitlement_id="")


def test_config_defaults():
    c = AgentOpsConfig(rc_api_key="sk_test", entitlement_id="pro")
    assert c.currency == "AI_CREDITS"
    assert c.budget_per_session is None
    assert c.churnwall_url is None
    assert c.entitlement_cache_ttl == 300
    assert c.spend_policy is None


def test_spend_policy_config_defaults():
    p = SpendPolicyConfig()
    assert p.blocked_ops == []
    assert p.allowed_ops is None
    assert p.max_per_hour is None
    assert p.max_per_day is None


def test_config_with_spend_policy():
    c = AgentOpsConfig(
        rc_api_key="sk_test",
        entitlement_id="pro",
        spend_policy=SpendPolicyConfig(max_per_hour=100),
    )
    assert c.spend_policy.max_per_hour == 100


def test_from_env(monkeypatch):
    monkeypatch.setenv("RC_API_KEY", "sk_env")
    monkeypatch.setenv("RC_ENTITLEMENT_ID", "premium")
    monkeypatch.setenv("RC_CURRENCY", "TOKENS")
    monkeypatch.setenv("CHURNWALL_URL", "http://cw.local")
    c = AgentOpsConfig.from_env()
    assert c.rc_api_key == "sk_env"
    assert c.entitlement_id == "premium"
    assert c.currency == "TOKENS"
    assert c.churnwall_url == "http://cw.local"


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("RC_API_KEY", "sk_env")
    c = AgentOpsConfig.from_env(entitlement_id="override_ent")
    assert c.entitlement_id == "override_ent"
