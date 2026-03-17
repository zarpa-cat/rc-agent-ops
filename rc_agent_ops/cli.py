import os

import typer
from rich import print as rprint

from .config import AgentOpsConfig
from .stack import BillingStack

app = typer.Typer()


def _get_stack() -> BillingStack:
    config = AgentOpsConfig(
        rc_api_key=os.environ.get("RC_API_KEY", ""),
        entitlement_id=os.environ.get("RC_ENTITLEMENT_ID", "pro_access"),
        currency=os.environ.get("RC_CURRENCY", "AI_CREDITS"),
        churnwall_url=os.environ.get("CHURNWALL_URL"),
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
        f"{'active' if active else 'inactive'}"
    )


@app.command()
def check(subscriber_id: str):
    """Check if a subscriber has an active entitlement."""
    stack = _get_stack()
    active = stack.check_entitlement(subscriber_id)
    if active:
        rprint("[green]Entitlement active[/green]")
        raise typer.Exit(0)
    else:
        rprint("[red]Entitlement inactive[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
