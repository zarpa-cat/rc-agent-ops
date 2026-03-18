from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpendPolicyConfig:
    """Spend limits enforced before any RC API call."""

    blocked_ops: list[str] = field(default_factory=list)
    allowed_ops: list[str] | None = None
    op_max_per_call: dict[str, int] = field(default_factory=dict)
    op_max_per_hour: dict[str, int] = field(default_factory=dict)
    max_per_hour: int | None = None
    max_per_day: int | None = None


@dataclass
class AgentOpsConfig:
    rc_api_key: str
    entitlement_id: str
    currency: str = "AI_CREDITS"
    op_costs: dict[str, int] = field(default_factory=dict)
    budget_per_session: int | None = None
    churnwall_url: str | None = None
    entitlement_cache_ttl: int = 300
    spend_policy: SpendPolicyConfig | None = None
    audit_db_path: str | None = None
    risk_db_path: str | None = None

    def __post_init__(self):
        if not self.rc_api_key:
            raise ValueError("rc_api_key is required")
        if not self.entitlement_id:
            raise ValueError("entitlement_id is required")

    @classmethod
    def from_env(cls, **overrides: Any) -> "AgentOpsConfig":
        """Build config from environment variables, with optional overrides."""
        import os

        return cls(
            rc_api_key=overrides.get("rc_api_key", os.environ.get("RC_API_KEY", "")),
            entitlement_id=overrides.get(
                "entitlement_id",
                os.environ.get("RC_ENTITLEMENT_ID", "pro_access"),
            ),
            currency=overrides.get(
                "currency", os.environ.get("RC_CURRENCY", "AI_CREDITS")
            ),
            churnwall_url=overrides.get(
                "churnwall_url", os.environ.get("CHURNWALL_URL")
            ),
            audit_db_path=overrides.get(
                "audit_db_path", os.environ.get("RCOPS_AUDIT_DB")
            ),
            risk_db_path=overrides.get("risk_db_path", os.environ.get("RCOPS_RISK_DB")),
            **{
                k: v
                for k, v in overrides.items()
                if k
                not in (
                    "rc_api_key",
                    "entitlement_id",
                    "currency",
                    "churnwall_url",
                    "audit_db_path",
                    "risk_db_path",
                )
            },
        )
