"""Invite token management for multi-instance nanobot onboarding.

Commands:
  nanobot invite create --instance nano-beta --expires 24h
  nanobot invite list
  nanobot invite revoke <id>
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import typer
from rich.console import Console
from rich.table import Table

invite_app = typer.Typer(
    name="invite",
    help="Manage invite tokens for onboarding new nanobot instances.",
    no_args_is_help=True,
)

console = Console()

INVITE_PREFIX = "nanobot/invites/"


def _parse_duration(s: str) -> timedelta:
    """Parse a human duration like '24h', '7d', '30m' into a timedelta."""
    s = s.strip().lower()
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    if s.endswith("m"):
        return timedelta(minutes=int(s[:-1]))
    raise typer.BadParameter(f"Invalid duration: {s}. Use e.g. '24h', '7d', '30m'.")


@invite_app.command()
def create(
    instance: str = typer.Option(..., help="Instance name for the new team member (e.g. nano-beta)"),
    expires: str = typer.Option("24h", help="Token validity duration (e.g. 24h, 7d)"),
    region: str = typer.Option("us-east-1", help="AWS region"),
) -> None:
    """Generate an invite token and print the onboarding one-liner."""
    import boto3

    sm = boto3.client("secretsmanager", region_name=region)
    sts = boto3.client("sts", region_name=region)

    account_id = sts.get_caller_identity()["Account"]
    duration = _parse_duration(expires)
    expires_at = datetime.now(timezone.utc) + duration

    invite_id = str(uuid.uuid4())[:8]
    token = secrets.token_urlsafe(33)  # 44-char base64url

    # Resolve org secret ARN
    try:
        org_resp = sm.describe_secret(SecretId="nanobot/org")
        org_secret_arn = org_resp["ARN"]
    except Exception:
        console.print("[red]Org secret 'nanobot/org' not found. Deploy shared stack first.[/red]")
        raise typer.Exit(1)

    # Resolve ECR repo URI
    ecr = boto3.client("ecr", region_name=region)
    try:
        ecr_resp = ecr.describe_repositories(repositoryNames=["nanobot"])
        ecr_repo_uri = ecr_resp["repositories"][0]["repositoryUri"]
    except Exception:
        ecr_repo_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/nanobot"

    # Resolve agent bucket from context (check CloudFormation)
    agent_bucket = ""
    try:
        cf = boto3.client("cloudformation", region_name=region)
        resp = cf.describe_stacks(StackName="NanobotSharedStack")
        outputs = {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0].get("Outputs", [])}
        agent_bucket = outputs.get("AgentBucketName", "")
    except Exception:
        pass

    invite_data = {
        "token": token,
        "instanceName": instance,
        "orgSecretArn": org_secret_arn,
        "ecrRepoUri": ecr_repo_uri,
        "agentBucket": agent_bucket,
        "region": region,
        "expiresAt": expires_at.isoformat(),
        "used": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    secret_name = f"{INVITE_PREFIX}{invite_id}"
    sm.create_secret(
        Name=secret_name,
        Description=f"Nanobot invite for instance '{instance}' (expires {expires_at.isoformat()})",
        SecretString=json.dumps(invite_data),
    )

    console.print(f"\n[green]Invite created![/green]")
    console.print(f"  ID:       [cyan]{invite_id}[/cyan]")
    console.print(f"  Instance: [cyan]{instance}[/cyan]")
    console.print(f"  Expires:  [cyan]{expires_at.strftime('%Y-%m-%d %H:%M UTC')}[/cyan]")
    console.print(f"\n[bold]Send this to your team member:[/bold]\n")
    console.print(
        f'  curl -sL https://raw.githubusercontent.com/YOUR_ORG/nanobot/main/scripts/join.sh | \\\n'
        f'    INVITE_TOKEN={token} INVITE_ID={invite_id} bash\n'
    )


@invite_app.command("list")
def list_invites(
    region: str = typer.Option("us-east-1", help="AWS region"),
) -> None:
    """List all invites with their status."""
    import boto3

    sm = boto3.client("secretsmanager", region_name=region)

    # List all secrets with the invite prefix
    paginator = sm.get_paginator("list_secrets")
    invites: list[dict] = []

    for page in paginator.paginate(
        Filters=[{"Key": "name", "Values": [INVITE_PREFIX]}],
    ):
        for entry in page.get("SecretList", []):
            secret_name = entry["Name"]
            invite_id = secret_name.removeprefix(INVITE_PREFIX)
            try:
                resp = sm.get_secret_value(SecretId=secret_name)
                data = json.loads(resp["SecretString"])
                invites.append({"id": invite_id, **data})
            except Exception:
                invites.append({"id": invite_id, "error": True})

    if not invites:
        console.print("[dim]No invites found.[/dim]")
        return

    table = Table(title="Nanobot Invites")
    table.add_column("ID", style="cyan")
    table.add_column("Instance")
    table.add_column("Status")
    table.add_column("Expires")
    table.add_column("Created")

    now = datetime.now(timezone.utc)
    for inv in invites:
        if inv.get("error"):
            table.add_row(inv["id"], "?", "[red]error reading[/red]", "", "")
            continue

        expires_at = datetime.fromisoformat(inv.get("expiresAt", "2000-01-01T00:00:00+00:00"))
        used = inv.get("used", False)

        if used:
            status = "[dim]used[/dim]"
        elif expires_at < now:
            status = "[yellow]expired[/yellow]"
        else:
            status = "[green]active[/green]"

        table.add_row(
            inv["id"],
            inv.get("instanceName", "?"),
            status,
            expires_at.strftime("%Y-%m-%d %H:%M"),
            inv.get("createdAt", "?")[:16],
        )

    console.print(table)


@invite_app.command()
def revoke(
    invite_id: str = typer.Argument(help="Invite ID to revoke"),
    region: str = typer.Option("us-east-1", help="AWS region"),
) -> None:
    """Delete an invite token."""
    import boto3

    sm = boto3.client("secretsmanager", region_name=region)
    secret_name = f"{INVITE_PREFIX}{invite_id}"

    try:
        sm.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
        console.print(f"[green]Invite {invite_id} revoked.[/green]")
    except sm.exceptions.ResourceNotFoundException:
        console.print(f"[red]Invite {invite_id} not found.[/red]")
        raise typer.Exit(1)
