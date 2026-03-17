# rc-agent-ops

**Three RevenueCat libraries. One integration layer. The complete billing lifecycle for agent-native apps.**

```
subscriber_id → EntitlementGate → run() → BillingMeter → debit → ChurnSync → result
```

---

## Why

Agent billing isn't one thing. It's three distinct concerns:

- **Entitlement** — is this subscriber allowed to run this operation? ([rc-entitlement-gate](https://github.com/zarpa-cat/rc-entitlement-gate))
- **Usage accounting** — how many credits should this operation cost, and what's the balance? ([agent-billing-meter](https://github.com/zarpa-cat/agent-billing-meter))
- **Retention intelligence** — is this subscriber at risk of churning, and what should we do about it? ([churnwall](https://github.com/zarpa-cat/churnwall))

These three things operate at different layers. Entitlement is access control — sync with RevenueCat, fast TTL cache, fail closed. Billing is usage accounting — debit after success, never on exception, audit log everything. Churn is business intelligence — score risk, trigger retention offers.

Most agent implementations collapse all three or skip two of them. This library is the glue code that makes them compose.

---

## Quick Start

```python
import asyncio
from rc_agent_ops import AgentOpsConfig, BillingStack, AgentOps

config = AgentOpsConfig(
    rc_api_key="your_rc_api_key",
    entitlement_id="pro_access",
    currency="AI_CREDITS",
    op_costs={"summarize": 10, "search": 5},
    budget_per_session=100,
    churnwall_url="http://localhost:8000",  # optional
)

stack = BillingStack(config)

async def main():
    async with AgentOps(stack, subscriber_id="user_123") as ops:
        # Entitlement is checked once on first op, cached for the session
        result = await ops.run("summarize", my_summarize_fn)
        result2 = await ops.run("search", my_search_fn)
    # On clean exit: churnwall sync fires (if configured)

asyncio.run(main())
```

Or use the decorator:

```python
from rc_agent_ops import agent_op

@agent_op(stack=stack, subscriber_id="user_123", op_name="summarize", cost=10)
async def summarize(text: str) -> str:
    # Your actual agent logic here
    return f"Summary of: {text}"
```

---

## Architecture

```
AgentOpsConfig
    rc_api_key, entitlement_id, currency,
    op_costs, budget_per_session, churnwall_url

BillingStack
    ├── RCEntitlementClient  (rc-entitlement-gate)
    │   └── check(subscriber_id, entitlement)  → bool
    ├── BillingMeter | BudgetedMeter  (agent-billing-meter)
    │   └── debit(amount, operation)  → fires AFTER fn success
    └── httpx (churnwall sync)
        └── POST /api/v1/subscribers/{id}/sync  → best-effort

AgentOps
    __aenter__: create meter, enter meter context
    run(op_name, fn): check entitlement → await fn() → debit
    __aexit__: exit meter context → sync churnwall (if clean)
```

### Key design decisions

**Entitlement is checked once per session, not per op.** If you're in a session with a subscriber, you trust that check for the duration. Per-op entitlement checks would double your API call count for no safety gain.

**Billing fires after fn(), never on exception.** A failed operation didn't consume a resource. Don't charge for it. This is the same philosophy as agent-billing-meter.

**Churnwall sync is best-effort.** If the churnwall is down, the agent operation still completes. Retention intelligence is a background concern, not a hard dependency.

**Budget cap is per-session.** `BudgetedMeter` enforces a hard credit cap for the entire `AgentOps` context. Across sessions, budget enforcement is the caller's responsibility.

---

## API Reference

### `AgentOpsConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `rc_api_key` | `str` | required | RevenueCat API key |
| `entitlement_id` | `str` | required | Entitlement to check (e.g. `"pro_access"`) |
| `currency` | `str` | `"AI_CREDITS"` | Virtual currency for billing |
| `op_costs` | `dict[str, int]` | `{}` | Credit cost per operation name |
| `budget_per_session` | `int \| None` | `None` | Hard session credit cap |
| `churnwall_url` | `str \| None` | `None` | Churnwall base URL for subscriber sync |
| `entitlement_cache_ttl` | `int` | `300` | Entitlement cache TTL in seconds |

### `BillingStack`

```python
stack = BillingStack(config)
stack.check_entitlement(subscriber_id)  # → bool (sync)
stack.meter_for(subscriber_id)  # → BillingMeter | BudgetedMeter
await stack.sync_to_churnwall(subscriber_id)  # → None (best-effort)
```

### `AgentOps`

```python
async with AgentOps(stack, subscriber_id) as ops:
    result = await ops.run(op_name, fn)           # cost from config
    result = await ops.run(op_name, fn, cost=25)  # explicit cost
```

### `agent_op` decorator

```python
@agent_op(stack=stack, subscriber_id="user_123", op_name="infer", cost=10)
async def infer(prompt: str) -> str: ...
```

---

## CLI

```bash
# Install
uv add rc-agent-ops

# Set env
export RC_API_KEY=your_key
export RC_ENTITLEMENT_ID=pro_access
export RC_CURRENCY=AI_CREDITS
export CHURNWALL_URL=http://localhost:8000  # optional

# Check a subscriber's entitlement status
rcops status user_123

# Run a demo op (check + bill, no-op function)
rcops check user_123 summarize --cost 10

# Show billing history
# (uses agent-billing-meter's audit log)
```

---

## Installation

```bash
uv add "rc-agent-ops @ git+https://github.com/zarpa-cat/rc-agent-ops.git"
```

Dependencies:
- [rc-entitlement-gate](https://github.com/zarpa-cat/rc-entitlement-gate) ≥ 0.4.0
- [agent-billing-meter](https://github.com/zarpa-cat/agent-billing-meter) ≥ 0.3.0
- httpx ≥ 0.27
- typer ≥ 0.12

---

## The Stack

```
┌─────────────────────────────────────────────┐
│              agent-billing-meter             │  usage accounting
├─────────────────────────────────────────────┤
│              rc-entitlement-gate             │  access control
├─────────────────────────────────────────────┤
│                  churnwall                   │  retention intelligence
└─────────────────────────────────────────────┘
         ↑ rc-agent-ops wires all three ↑
```

Part of the [zarpa-cat](https://github.com/zarpa-cat) agent billing toolkit.

---

## License

MIT
