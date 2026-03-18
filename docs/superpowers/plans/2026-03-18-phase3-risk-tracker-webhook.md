# Phase 3: Subscriber Risk Tracker + RC Webhook Integration

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reactive risk layer that processes RevenueCat webhook events to update subscriber risk state, bypassing stale entitlement caches.

**Architecture:** SQLite-backed RiskTracker stores per-subscriber risk state (CLEAN/SUSPECTED/BLOCKED). RCWebhookHandler maps RC webhook event types to risk marks. BillingStack integrates risk checks into entitlement flow — BLOCKED skips API, SUSPECTED bypasses cache.

**Tech Stack:** Python 3.11+, SQLite (stdlib), FastAPI APIRouter (optional), HMAC-SHA256 for webhook auth, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `rc_agent_ops/risk.py` | Create | SubscriberRisk enum, RiskEvent dataclass, RiskTracker (SQLite) |
| `rc_agent_ops/webhook.py` | Create | RCWebhookHandler, make_webhook_router (FastAPI) |
| `rc_agent_ops/config.py` | Modify (lines 27, 52-54) | Add `risk_db_path` field + env var |
| `rc_agent_ops/stack.py` | Modify (lines 9-14, 48-53) | Integrate RiskTracker into BillingStack |
| `rc_agent_ops/cli.py` | Modify (add risk subcommand group) | `rcops risk show/list/mark` commands |
| `rc_agent_ops/__init__.py` | Modify | Export new public API |
| `pyproject.toml` | Modify (line 7) | Version bump to 0.3.0 |
| `README.md` | Modify (append) | Phase 3 documentation |
| `tests/test_risk.py` | Create | 7 tests for RiskTracker |
| `tests/test_webhook.py` | Create | 10 tests for RCWebhookHandler |
| `tests/test_stack_risk.py` | Create | 2 tests for BillingStack + risk integration |

---

### Task 1: RiskTracker core — enum, dataclass, SQLite store

**Files:**
- Create: `rc_agent_ops/risk.py`
- Create: `tests/test_risk.py`

- [ ] **Step 1: Write test file with all 7 risk tests**

```python
# tests/test_risk.py
import time

import pytest

from rc_agent_ops.risk import RiskTracker, SubscriberRisk, RiskEvent


@pytest.fixture
def tracker():
    return RiskTracker(db_path=":memory:")


def test_risk_default_clean(tracker):
    assert tracker.get("unknown_user") == SubscriberRisk.CLEAN


def test_risk_mark_suspected(tracker):
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")
    assert tracker.get("user_1") == SubscriberRisk.SUSPECTED


def test_risk_mark_blocked(tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    assert tracker.get("user_1") == SubscriberRisk.BLOCKED


def test_risk_mark_clean_clears(tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    tracker.mark("user_1", SubscriberRisk.CLEAN, "renewed")
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_risk_history(tracker):
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    tracker.mark("user_1", SubscriberRisk.CLEAN, "renewed")
    history = tracker.history("user_1", limit=20)
    assert len(history) == 3
    # Reverse chronological order
    assert history[0].risk == SubscriberRisk.CLEAN
    assert history[1].risk == SubscriberRisk.BLOCKED
    assert history[2].risk == SubscriberRisk.SUSPECTED


def test_risk_list_at_risk(tracker):
    tracker.mark("user_1", SubscriberRisk.SUSPECTED, "billing issue")
    tracker.mark("user_2", SubscriberRisk.BLOCKED, "expired")
    at_risk = tracker.list_at_risk()
    ids = {sub_id for sub_id, _ in at_risk}
    assert ids == {"user_1", "user_2"}


def test_risk_list_at_risk_excludes_clean(tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    tracker.mark("user_1", SubscriberRisk.CLEAN, "renewed")
    at_risk = tracker.list_at_risk()
    ids = {sub_id for sub_id, _ in at_risk}
    assert "user_1" not in ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_risk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rc_agent_ops.risk'`

- [ ] **Step 3: Implement risk.py**

```python
# rc_agent_ops/risk.py
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum


class SubscriberRisk(Enum):
    CLEAN = "CLEAN"
    SUSPECTED = "SUSPECTED"
    BLOCKED = "BLOCKED"


@dataclass
class RiskEvent:
    subscriber_id: str
    risk: SubscriberRisk
    reason: str
    ts: float


class RiskTracker:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS risk_events("
            "subscriber_id TEXT, risk TEXT, reason TEXT, ts REAL)"
        )
        self._conn.commit()

    def mark(self, subscriber_id: str, risk: SubscriberRisk, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO risk_events(subscriber_id, risk, reason, ts) VALUES(?,?,?,?)",
            (subscriber_id, risk.value, reason, time.time()),
        )
        self._conn.commit()

    def get(self, subscriber_id: str) -> SubscriberRisk:
        row = self._conn.execute(
            "SELECT risk FROM risk_events WHERE subscriber_id=? ORDER BY ts DESC LIMIT 1",
            (subscriber_id,),
        ).fetchone()
        if row is None:
            return SubscriberRisk.CLEAN
        return SubscriberRisk(row[0])

    def history(self, subscriber_id: str, limit: int = 20) -> list[RiskEvent]:
        rows = self._conn.execute(
            "SELECT subscriber_id, risk, reason, ts FROM risk_events "
            "WHERE subscriber_id=? ORDER BY ts DESC LIMIT ?",
            (subscriber_id, limit),
        ).fetchall()
        return [
            RiskEvent(r[0], SubscriberRisk(r[1]), r[2], r[3]) for r in rows
        ]

    def list_at_risk(self) -> list[tuple[str, SubscriberRisk]]:
        rows = self._conn.execute(
            "SELECT subscriber_id, risk FROM risk_events "
            "WHERE ts = (SELECT MAX(ts) FROM risk_events e2 "
            "WHERE e2.subscriber_id = risk_events.subscriber_id) "
            "AND risk != ?",
            (SubscriberRisk.CLEAN.value,),
        ).fetchall()
        return [(r[0], SubscriberRisk(r[1])) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_risk.py -v`
Expected: 7 passed

- [ ] **Step 5: Run ruff**

Run: `ruff check rc_agent_ops/risk.py tests/test_risk.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add rc_agent_ops/risk.py tests/test_risk.py
git commit -m "feat: add RiskTracker with SQLite-backed subscriber risk state"
```

---

### Task 2: RCWebhookHandler — event mapping + HMAC auth

**Files:**
- Create: `rc_agent_ops/webhook.py`
- Create: `tests/test_webhook.py`

- [ ] **Step 1: Write test file with all 10 webhook tests**

```python
# tests/test_webhook.py
import hashlib
import hmac
import json

import pytest

from rc_agent_ops.risk import RiskTracker, SubscriberRisk
from rc_agent_ops.webhook import RCWebhookHandler


@pytest.fixture
def tracker():
    return RiskTracker(db_path=":memory:")


@pytest.fixture
def handler(tracker):
    return RCWebhookHandler(risk_tracker=tracker)


def _payload(event_type: str, app_user_id: str = "user_1") -> dict:
    return {"event": {"type": event_type, "app_user_id": app_user_id}}


def test_webhook_billing_issue(handler, tracker):
    result = handler.handle(_payload("BILLING_ISSUE_DETECTED_FOR_CUSTOMER"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.SUSPECTED


def test_webhook_cancellation(handler, tracker):
    result = handler.handle(_payload("CANCELLATION"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.BLOCKED


def test_webhook_expiration(handler, tracker):
    result = handler.handle(_payload("EXPIRATION"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.BLOCKED


def test_webhook_renewal(handler, tracker):
    # First block, then renew
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "expired")
    result = handler.handle(_payload("RENEWAL"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_webhook_uncancellation(handler, tracker):
    tracker.mark("user_1", SubscriberRisk.BLOCKED, "cancelled")
    result = handler.handle(_payload("UNCANCELLATION"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_webhook_initial_purchase(handler, tracker):
    result = handler.handle(_payload("INITIAL_PURCHASE"))
    assert result["processed"] is True
    assert tracker.get("user_1") == SubscriberRisk.CLEAN


def test_webhook_unknown_event(handler):
    result = handler.handle(_payload("SOME_UNKNOWN_EVENT"))
    assert result["processed"] is False
    assert "unhandled" in result["reason"].lower()


def test_webhook_missing_event_type(handler):
    result = handler.handle({"event": {}})
    assert result["processed"] is False


def test_webhook_auth_valid(tracker):
    auth_key = "secret123"
    handler = RCWebhookHandler(risk_tracker=tracker, auth_key=auth_key)
    body = json.dumps(_payload("RENEWAL")).encode()
    sig = hmac.new(auth_key.encode(), body, hashlib.sha256).hexdigest()
    result = handler.handle_with_auth(_payload("RENEWAL"), signature=sig, raw_body=body)
    assert result["processed"] is True


def test_webhook_auth_invalid(tracker):
    auth_key = "secret123"
    handler = RCWebhookHandler(risk_tracker=tracker, auth_key=auth_key)
    body = json.dumps(_payload("RENEWAL")).encode()
    result = handler.handle_with_auth(_payload("RENEWAL"), signature="badsig", raw_body=body)
    assert result["processed"] is False
    assert "signature" in result.get("reason", "").lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_webhook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rc_agent_ops.webhook'`

- [ ] **Step 3: Implement webhook.py**

```python
# rc_agent_ops/webhook.py
from __future__ import annotations

import hashlib
import hmac

from .risk import RiskTracker, SubscriberRisk

# RC webhook event type → risk level
_EVENT_RISK_MAP: dict[str, SubscriberRisk] = {
    "BILLING_ISSUE_DETECTED_FOR_CUSTOMER": SubscriberRisk.SUSPECTED,
    "EXPIRATION": SubscriberRisk.BLOCKED,
    "CANCELLATION": SubscriberRisk.BLOCKED,
    "RENEWAL": SubscriberRisk.CLEAN,
    "UNCANCELLATION": SubscriberRisk.CLEAN,
    "INITIAL_PURCHASE": SubscriberRisk.CLEAN,
}


class RCWebhookHandler:
    def __init__(
        self, risk_tracker: RiskTracker, auth_key: str | None = None
    ) -> None:
        self.risk_tracker = risk_tracker
        self.auth_key = auth_key

    def handle(self, payload: dict) -> dict:
        event = payload.get("event", {})
        event_type = event.get("type")
        subscriber_id = event.get("app_user_id")

        if not event_type:
            return {"processed": False, "reason": "missing event type"}

        risk = _EVENT_RISK_MAP.get(event_type)
        if risk is None:
            return {
                "processed": False,
                "reason": f"unhandled event type: {event_type}",
            }

        if not subscriber_id:
            return {"processed": False, "reason": "missing app_user_id"}

        self.risk_tracker.mark(subscriber_id, risk, event_type)
        return {
            "processed": True,
            "subscriber_id": subscriber_id,
            "action": f"marked {risk.value}",
        }

    def handle_with_auth(
        self, payload: dict, *, signature: str, raw_body: bytes
    ) -> dict:
        if self.auth_key:
            expected = hmac.new(
                self.auth_key.encode(), raw_body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return {"processed": False, "reason": "invalid signature"}
        return self.handle(payload)


def make_webhook_router(handler: RCWebhookHandler):
    """Create a FastAPI APIRouter for RC webhooks."""
    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as e:
        raise ImportError(
            "fastapi is required for make_webhook_router. "
            "Install with: pip install rc-agent-ops[fastapi]"
        ) from e

    router = APIRouter()

    @router.post("/webhook/rc")
    async def rc_webhook(request: Request):
        raw_body = await request.body()
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "malformed payload"}, status_code=400
            )

        event = payload.get("event", {})
        if not event.get("type"):
            return JSONResponse(
                {"error": "missing event.type"}, status_code=400
            )

        if handler.auth_key:
            sig = request.headers.get("RC-Billing-Signature", "")
            result = handler.handle_with_auth(
                payload, signature=sig, raw_body=raw_body
            )
            if not result["processed"] and "signature" in result.get("reason", ""):
                return JSONResponse(
                    {"error": "invalid signature"}, status_code=401
                )
            return result

        return handler.handle(payload)

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_webhook.py -v`
Expected: 10 passed

- [ ] **Step 5: Run ruff**

Run: `ruff check rc_agent_ops/webhook.py tests/test_webhook.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add rc_agent_ops/webhook.py tests/test_webhook.py
git commit -m "feat: add RCWebhookHandler with event mapping + HMAC auth"
```

---

### Task 3: Integrate RiskTracker into BillingStack + config

**Files:**
- Modify: `rc_agent_ops/config.py:27` (add risk_db_path field)
- Modify: `rc_agent_ops/config.py:52-54` (add env var)
- Modify: `rc_agent_ops/stack.py:9-14` (auto-create RiskTracker)
- Modify: `rc_agent_ops/stack.py:48-53` (risk-aware entitlement check)
- Create: `tests/test_stack_risk.py`

- [ ] **Step 1: Write test file with 2 stack-risk integration tests**

```python
# tests/test_stack_risk.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_stack_risk.py -v`
Expected: FAIL — `BillingStack` has no `risk_tracker` attribute

- [ ] **Step 3: Add `risk_db_path` to AgentOpsConfig**

In `rc_agent_ops/config.py`, add after line 27 (`audit_db_path`):
```python
    risk_db_path: str | None = None
```

In `from_env()`, add `risk_db_path` to the constructor call:
```python
            risk_db_path=overrides.get(
                "risk_db_path", os.environ.get("RCOPS_RISK_DB")
            ),
```

And add `"risk_db_path"` to the exclusion set in the `**{}` kwargs.

- [ ] **Step 4: Integrate RiskTracker into BillingStack.__init__ and check_entitlement**

In `rc_agent_ops/stack.py`:

After `self.entitlement_client = ...` in `__init__`, add:
```python
        if config.risk_db_path:
            from .risk import RiskTracker
            self.risk_tracker: RiskTracker | None = RiskTracker(config.risk_db_path)
        else:
            self.risk_tracker = None
```

Replace `check_entitlement` with:
```python
    def check_entitlement(self, subscriber_id: str) -> bool:
        if self.risk_tracker is not None:
            risk = self.risk_tracker.get(subscriber_id)
            if risk == SubscriberRisk.BLOCKED:
                return False
            if risk == SubscriberRisk.SUSPECTED:
                # Bypass cache — force fresh check
                result: CheckResult = self.entitlement_client.check(
                    subscriber_id=subscriber_id,
                    entitlement=self.config.entitlement_id,
                    use_cache=False,
                )
                return result.granted
        result: CheckResult = self.entitlement_client.check(
            subscriber_id=subscriber_id,
            entitlement=self.config.entitlement_id,
        )
        return result.granted
```

Add import at top of `check_entitlement`: `from .risk import SubscriberRisk` (or add to file-level imports conditionally).

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/test_stack_risk.py tests/test_stack.py tests/test_risk.py -v`
Expected: all pass

- [ ] **Step 6: Run ruff**

Run: `ruff check rc_agent_ops/config.py rc_agent_ops/stack.py tests/test_stack_risk.py`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add rc_agent_ops/config.py rc_agent_ops/stack.py tests/test_stack_risk.py
git commit -m "feat: integrate RiskTracker into BillingStack + config"
```

---

### Task 4: CLI risk subcommands

**Files:**
- Modify: `rc_agent_ops/cli.py` (add risk subcommand group)

- [ ] **Step 1: Add risk subcommand group to cli.py**

Add a `risk_app` Typer sub-app with three commands:

```python
risk_app = typer.Typer(help="Subscriber risk management")
app.add_typer(risk_app, name="risk")

@risk_app.command("show")
def risk_show(subscriber_id: str):
    """Show current risk state and recent history for a subscriber."""
    from .risk import RiskTracker

    db_path = os.environ.get("RCOPS_RISK_DB", ":memory:")
    tracker = RiskTracker(db_path)
    current = tracker.get(subscriber_id)
    history = tracker.history(subscriber_id)

    risk_colors = {"CLEAN": "green", "SUSPECTED": "yellow", "BLOCKED": "red"}
    color = risk_colors.get(current.value, "white")
    rprint(f"[bold]Subscriber:[/bold] {subscriber_id}")
    rprint(f"[bold]Risk:[/bold] [{color}]{current.value}[/{color}]")

    if not history:
        rprint("[dim]No risk history.[/dim]")
        return

    table = Table(title="Risk History", box=box.ROUNDED)
    table.add_column("Time", style="dim")
    table.add_column("Risk")
    table.add_column("Reason")

    for evt in history:
        ts = datetime.fromtimestamp(evt.ts, tz=timezone.utc).strftime("%m-%d %H:%M")
        c = risk_colors.get(evt.risk.value, "white")
        table.add_row(ts, f"[{c}]{evt.risk.value}[/{c}]", evt.reason)

    console.print(table)


@risk_app.command("list")
def risk_list():
    """Show all subscribers with risk != CLEAN."""
    from .risk import RiskTracker

    db_path = os.environ.get("RCOPS_RISK_DB", ":memory:")
    tracker = RiskTracker(db_path)
    at_risk = tracker.list_at_risk()

    if not at_risk:
        rprint("[dim]No subscribers at risk.[/dim]")
        return

    table = Table(title="At-Risk Subscribers", box=box.ROUNDED)
    table.add_column("Subscriber")
    table.add_column("Risk")

    risk_colors = {"CLEAN": "green", "SUSPECTED": "yellow", "BLOCKED": "red"}
    for sub_id, risk in at_risk:
        c = risk_colors.get(risk.value, "white")
        table.add_row(sub_id, f"[{c}]{risk.value}[/{c}]")

    console.print(table)


@risk_app.command("mark")
def risk_mark(
    subscriber_id: str,
    risk: str,
    reason: str = typer.Option("manual", "--reason", help="Reason for risk change"),
):
    """Manually set risk state for a subscriber."""
    from .risk import RiskTracker, SubscriberRisk

    db_path = os.environ.get("RCOPS_RISK_DB", ":memory:")
    tracker = RiskTracker(db_path)

    try:
        risk_level = SubscriberRisk(risk.upper())
    except ValueError:
        rprint(f"[red]Invalid risk level: {risk}. Use CLEAN, SUSPECTED, or BLOCKED.[/red]")
        raise typer.Exit(1)

    tracker.mark(subscriber_id, risk_level, reason)
    rprint(f"Marked [bold]{subscriber_id}[/bold] as [{risk_level.value}]{risk_level.value}[/{risk_level.value}]")
```

- [ ] **Step 2: Run full test suite to ensure no regressions**

Run: `python -m pytest -v`
Expected: all existing + new tests pass

- [ ] **Step 3: Run ruff**

Run: `ruff check rc_agent_ops/cli.py`
Expected: clean

- [ ] **Step 4: Commit**

```bash
git add rc_agent_ops/cli.py
git commit -m "feat: add rcops risk show/list/mark CLI commands"
```

---

### Task 5: Update exports, version, README

**Files:**
- Modify: `rc_agent_ops/__init__.py`
- Modify: `pyproject.toml:7`
- Modify: `README.md`

- [ ] **Step 1: Update __init__.py exports**

```python
from .config import AgentOpsConfig, SpendPolicyConfig
from .errors import EntitlementDenied
from .ops import AgentOps, agent_op
from .risk import RiskTracker, SubscriberRisk, RiskEvent
from .stack import BillingStack
from .webhook import RCWebhookHandler, make_webhook_router

__all__ = [
    "AgentOpsConfig",
    "SpendPolicyConfig",
    "BillingStack",
    "AgentOps",
    "EntitlementDenied",
    "agent_op",
    "RiskTracker",
    "SubscriberRisk",
    "RiskEvent",
    "RCWebhookHandler",
    "make_webhook_router",
]
```

- [ ] **Step 2: Bump version in pyproject.toml**

Change `version = "0.2.0"` → `version = "0.3.0"`

- [ ] **Step 3: Append Phase 3 section to README.md**

Add Phase 3 documentation section before the License section, covering:
- RiskTracker usage
- RCWebhookHandler usage
- make_webhook_router integration with FastAPI
- New CLI commands (`rcops risk show/list/mark`)

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest -v`
Expected: all tests pass (34 existing + 19 new = 53 total)

- [ ] **Step 5: Run ruff on entire package**

Run: `ruff check rc_agent_ops/ tests/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add rc_agent_ops/__init__.py pyproject.toml README.md
git commit -m "feat: Phase 3 — subscriber risk tracker + RC webhook integration (v0.3.0)"
```

---

### Task 6: Final verification + notify

- [ ] **Step 1: Run full test suite one final time**

Run: `python -m pytest -v`
Expected: all 53+ tests pass

- [ ] **Step 2: Run ruff on entire project**

Run: `ruff check .`
Expected: clean

- [ ] **Step 3: Send completion notification**

Run: `openclaw system event --text "Done: rc-agent-ops Phase 3 shipped — risk tracker + webhook handler, v0.3.0" --mode now`
