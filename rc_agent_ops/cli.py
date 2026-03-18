import asyncio
import os
import time
from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.table import Table
from rich import box
from rich.console import Console

from .config import AgentOpsConfig
from .stack import BillingStack

app = typer.Typer(help="rc-agent-ops: entitlement + billing + churnwall in one CLI")
console = Console()


def _get_stack() -> BillingStack:
    config = AgentOpsConfig(
        rc_api_key=os.environ.get("RC_API_KEY", ""),
        entitlement_id=os.environ.get("RC_ENTITLEMENT_ID", "pro_access"),
        currency=os.environ.get("RC_CURRENCY", "AI_CREDITS"),
        churnwall_url=os.environ.get("CHURNWALL_URL"),
        audit_db_path=os.environ.get("RCOPS_AUDIT_DB"),
    )
    return BillingStack(config)


@app.command()
def status(subscriber_id: str):
    """Show entitlement + billing status for a subscriber."""
    stack = _get_stack()
    active = stack.check_entitlement(subscriber_id)
    rprint(f"[bold]Subscriber:[/bold] {subscriber_id}")
    rprint(
        f"[bold]Entitlement ({stack.config.entitlement_id}):[/bold] "
        f"{'[green]active[/green]' if active else '[red]inactive[/red]'}"
    )


@app.command()
def check(subscriber_id: str):
    """Check if a subscriber has an active entitlement (exit 0=yes, 1=no)."""
    stack = _get_stack()
    active = stack.check_entitlement(subscriber_id)
    if active:
        rprint("[green]Entitlement active[/green]")
        raise typer.Exit(0)
    else:
        rprint("[red]Entitlement inactive[/red]")
        raise typer.Exit(1)


@app.command()
def health():
    """Check connectivity to RevenueCat, entitlement gate, and churnwall."""
    stack = _get_stack()
    results = asyncio.run(stack.health())

    table = Table(title="Stack Health", box=box.ROUNDED)
    table.add_column("Component", style="bold")
    table.add_column("Status")

    def fmt(v: bool | None) -> str:
        if v is True:
            return "[green]✓ ok[/green]"
        if v is False:
            return "[red]✗ unreachable[/red]"
        return "[dim]not configured[/dim]"

    table.add_row("RevenueCat API", fmt(results["rc_api"]))
    table.add_row("Entitlement Gate", fmt(results["entitlement_gate"]))
    table.add_row("Churnwall", fmt(results["churnwall"]))

    console.print(table)

    all_ok = all(v is not False for v in results.values())
    raise typer.Exit(0 if all_ok else 1)


@app.command()
def audit(
    subscriber_id: str = typer.Argument(None, help="Filter by subscriber id"),
    operation: str = typer.Option(None, "--op", help="Filter by operation name"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows"),
    hours: float = typer.Option(None, "--hours", "-H", help="Only show last N hours"),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show recent billing audit log entries."""
    from agent_billing_meter.audit_log import AuditLog, DEFAULT_DB

    db_path = os.environ.get("RCOPS_AUDIT_DB", DEFAULT_DB)
    log = AuditLog(db_path=db_path)

    since: float | None = None
    if hours is not None:
        since = time.time() - hours * 3600

    results = log.query(
        app_user_id=subscriber_id,
        operation=operation,
        since=since,
        limit=limit,
    )

    if json_out:
        import json

        rows = []
        for r in results:
            rows.append(
                {
                    "app_user_id": r.app_user_id,
                    "operation": r.operation,
                    "amount_debited": r.amount_debited,
                    "success": r.success,
                    "balance_before": r.balance_before,
                    "balance_after": r.balance_after,
                    "error": r.error,
                    "timestamp": r.timestamp,
                }
            )
        rprint(json.dumps(rows, indent=2))
        return

    if not results:
        rprint("[dim]No audit log entries found.[/dim]")
        return

    table = Table(title="Billing Audit Log", box=box.ROUNDED)
    table.add_column("Time", style="dim")
    table.add_column("Subscriber")
    table.add_column("Operation")
    table.add_column("Amount", justify="right")
    table.add_column("Balance After", justify="right")
    table.add_column("Status")

    for r in results:
        ts = datetime.fromtimestamp(r.timestamp, tz=timezone.utc).strftime(
            "%m-%d %H:%M"
        )
        status_cell = (
            "[green]✓[/green]" if r.success else f"[red]✗ {r.error or ''}[/red]"
        )
        balance_after = str(r.balance_after) if r.balance_after is not None else "—"
        table.add_row(
            ts,
            r.app_user_id,
            r.operation,
            str(r.amount_debited),
            balance_after,
            status_cell,
        )

    console.print(table)


if __name__ == "__main__":
    app()
