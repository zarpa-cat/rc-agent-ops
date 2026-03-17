from dataclasses import dataclass, field


@dataclass
class AgentOpsConfig:
    rc_api_key: str
    entitlement_id: str
    currency: str = "AI_CREDITS"
    op_costs: dict[str, int] = field(default_factory=dict)
    budget_per_session: int | None = None
    churnwall_url: str | None = None
    entitlement_cache_ttl: int = 300

    def __post_init__(self):
        if not self.rc_api_key:
            raise ValueError("rc_api_key is required")
        if not self.entitlement_id:
            raise ValueError("entitlement_id is required")
