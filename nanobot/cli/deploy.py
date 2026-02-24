"""Unified deploy flow for nanobot on AWS Lightsail.

Orchestrates: prerequisites check, secrets collection, CDK deploy,
Docker image push, secrets upload, container start, and workspace upload.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()

STATE_DIR = Path.home() / ".nanobot"
STATE_FILE = STATE_DIR / "deploy-state.json"
STACK_NAME = "NanobotStack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _run(cmd: list[str], *, check: bool = True, capture: bool = False, input: bytes | None = None, **kw) -> subprocess.CompletedProcess:
    """Run a subprocess, streaming output unless capture=True."""
    if capture:
        return subprocess.run(cmd, check=check, capture_output=True, text=True, input=input, **kw)
    return subprocess.run(cmd, check=check, input=input, **kw)


def _prompt_optional(label: str, *, password: bool = False, existing: str | None = None) -> str:
    """Prompt for an optional value. Empty string = skip."""
    hint = ""
    if existing:
        masked = existing[:4] + "****" if len(existing) > 4 else "****"
        hint = f" [dim](current: {masked}, Enter to keep)[/dim]"
    val = Prompt.ask(f"  {label}{hint}", password=password, default="")
    if not val and existing:
        return existing
    return val


def _comma_to_list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


def _get_cf_outputs(region: str) -> dict[str, str]:
    """Fetch CloudFormation stack outputs as {key: value}."""
    import boto3

    cf = boto3.client("cloudformation", region_name=region)
    resp = cf.describe_stacks(StackName=STACK_NAME)
    outputs = resp["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


# ---------------------------------------------------------------------------
# DeployFlow
# ---------------------------------------------------------------------------


class DeployFlow:
    def __init__(
        self,
        *,
        region: str = "us-east-1",
        secrets_only: bool = False,
        image_only: bool = False,
        restart_only: bool = False,
        skip_cdk: bool = False,
        skip_image: bool = False,
        with_workspace: bool = False,
    ):
        self.region = region
        self.secrets_only = secrets_only
        self.image_only = image_only
        self.restart_only = restart_only
        self.skip_cdk = skip_cdk
        self.skip_image = skip_image
        self.with_workspace = with_workspace

        self.state = _load_state()
        self.collected_secrets: dict = {}

        # Project root = repo root (where Dockerfile lives)
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.infra_dir = self.project_root / "infra"

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def run(self) -> None:
        console.print("\n[bold cyan]nanobot deploy[/bold cyan]\n")

        if self.restart_only:
            self._phase5_start_container()
            return

        if self.image_only:
            self._phase0_prerequisites()
            self._phase3_push_image()
            return

        if self.secrets_only:
            self._phase0_prerequisites()
            self._phase1_collect_secrets()
            self._phase4_upload_secrets()
            return

        # Full flow
        self._phase0_prerequisites()
        self._phase1_collect_secrets()
        if not self.skip_cdk:
            self._phase2_cdk_deploy()
        if not self.skip_image:
            self._phase3_push_image()
        self._phase4_upload_secrets()
        self._phase5_start_container()
        self._phase6_upload_workspace()
        self._phase7_print_github_secrets()

    # ------------------------------------------------------------------
    # Phase 0: Prerequisites
    # ------------------------------------------------------------------

    def _phase0_prerequisites(self) -> None:
        console.rule("[bold]Phase 0: Prerequisites")

        table = Table(show_header=True)
        table.add_column("Tool", style="cyan")
        table.add_column("Status")
        table.add_column("Note", style="dim")

        has_node = shutil.which("node") is not None
        has_npx = shutil.which("npx") is not None
        has_docker = shutil.which("docker") is not None
        has_gh = shutil.which("gh") is not None

        aws_ok = False
        aws_note = ""
        try:
            import boto3

            sts = boto3.client("sts", region_name=self.region)
            identity = sts.get_caller_identity()
            aws_ok = True
            aws_note = identity["Account"]
        except Exception as exc:
            aws_note = str(exc)[:60]

        def _status(ok: bool) -> str:
            return "[green]found[/green]" if ok else "[red]missing[/red]"

        table.add_row("node / npx", _status(has_node and has_npx), "Required for CDK")
        table.add_row("docker", _status(has_docker), "Required for image build")
        table.add_row("AWS credentials", _status(aws_ok), aws_note)
        table.add_row("gh CLI", _status(has_gh), "Optional — for GitHub secrets")

        console.print(table)

        # Hard-fail on essentials
        missing = []
        if not (has_node and has_npx):
            missing.append("node/npx")
        if not has_docker:
            missing.append("docker")
        if not aws_ok:
            missing.append("AWS credentials")
        if missing:
            console.print(f"\n[red]Missing required tools: {', '.join(missing)}[/red]")
            raise SystemExit(1)

        console.print()

    # ------------------------------------------------------------------
    # Phase 1: Collect Secrets
    # ------------------------------------------------------------------

    def _phase1_collect_secrets(self) -> None:
        console.rule("[bold]Phase 1: Collect Secrets")

        # Try loading existing secret from AWS for re-run defaults
        existing: dict = {}
        secret_arn = self.state.get("secret_arn")
        if secret_arn:
            try:
                import boto3

                sm = boto3.client("secretsmanager", region_name=self.region)
                resp = sm.get_secret_value(SecretId=secret_arn)
                existing = json.loads(resp["SecretString"])
                console.print("[dim]Loaded existing secrets for defaults.[/dim]\n")
            except Exception:
                pass

        # -- Gateway --
        console.print("[bold]Gateway[/bold]")
        gw_existing = existing.get("gateway", {}).get("apiKey", "")
        api_key = _prompt_optional("API key (Bearer token)", password=True, existing=gw_existing)

        # -- Email (Outlook) --
        console.print("\n[bold]Email (Outlook)[/bold]  [dim]leave blank to skip[/dim]")
        em_existing = existing.get("channels", {}).get("email", {})
        email_addr = _prompt_optional("Email address", existing=em_existing.get("imapUsername", ""))
        email_pass = _prompt_optional("App password", password=True, existing=em_existing.get("imapPassword", ""))
        email_allow = _prompt_optional(
            "Allow-from (comma-separated emails)",
            existing=",".join(em_existing.get("allowFrom", [])),
        )

        # -- Telegram --
        console.print("\n[bold]Telegram[/bold]  [dim]leave blank to skip[/dim]")
        tg_existing = existing.get("channels", {}).get("telegram", {})
        tg_token = _prompt_optional("Bot token", password=True, existing=tg_existing.get("token", ""))
        tg_allow = _prompt_optional(
            "Allow-from (comma-separated user IDs)",
            existing=",".join(str(x) for x in tg_existing.get("allowFrom", [])),
        )

        # -- Brave Search --
        console.print("\n[bold]Web Search[/bold]  [dim]leave blank to skip[/dim]")
        brave_existing = existing.get("tools", {}).get("web", {}).get("search", {}).get("apiKey", "")
        brave_key = _prompt_optional("Brave API key", password=True, existing=brave_existing)

        # -- MCP: Jira --
        console.print("\n[bold]MCP: Jira[/bold]  [dim]leave blank to skip[/dim]")
        jira_existing = existing.get("tools", {}).get("mcpServers", {}).get("jira", {}).get("env", {})
        jira_site = _prompt_optional("Site name (e.g. myteam.atlassian.net)", existing=jira_existing.get("ATLASSIAN_SITE_NAME", ""))
        jira_email = _prompt_optional("Email", existing=jira_existing.get("ATLASSIAN_USER_EMAIL", ""))
        jira_token = _prompt_optional("API token", password=True, existing=jira_existing.get("ATLASSIAN_API_TOKEN", ""))

        # -- MCP: Notion --
        console.print("\n[bold]MCP: Notion[/bold]  [dim]leave blank to skip[/dim]")
        notion_existing = existing.get("tools", {}).get("mcpServers", {}).get("notion", {}).get("env", {})
        notion_token = _prompt_optional("Integration token", password=True, existing=notion_existing.get("NOTION_TOKEN", ""))

        # -- MCP: Paper Search --
        console.print("\n[bold]MCP: Paper Search[/bold]  [dim]leave blank to skip[/dim]")
        paper_existing = existing.get("tools", {}).get("mcpServers", {}).get("paper_search", {}).get("env", {})
        paper_key = _prompt_optional("Semantic Scholar key", password=True, existing=paper_existing.get("SEMANTIC_SCHOLAR_API_KEY", ""))

        # -- MCP: X.com --
        console.print("\n[bold]MCP: X.com[/bold]  [dim]leave blank to skip[/dim]")
        twitter_existing = existing.get("tools", {}).get("mcpServers", {}).get("twitter", {}).get("env", {})
        cookies_json = _prompt_optional("Cookies JSON", existing=twitter_existing.get("TWITTER_COOKIES", ""))

        # Build config JSON (matches put-secret.sh structure)
        config: dict = {
            "agents": {
                "defaults": {
                    "model": "openai-codex/gpt-5.1-codex",
                    "maxTokens": 8192,
                    "temperature": 0.1,
                    "maxToolIterations": 40,
                    "memoryWindow": 100,
                }
            },
            "providers": {},
            "gateway": {
                "host": "127.0.0.1",
                "port": 18790,
                "apiKey": api_key or "",
            },
            "channels": {},
            "tools": {
                "restrictToWorkspace": True,
                "web": {"search": {"apiKey": brave_key or ""}},
                "mcpAllowedCommands": ["npx", "uvx"],
                "mcpServers": {},
            },
        }

        # Email
        if email_addr and email_pass:
            config["channels"]["email"] = {
                "enabled": True,
                "consentGranted": True,
                "imapHost": "outlook.office365.com",
                "imapPort": 993,
                "imapUsername": email_addr,
                "imapPassword": email_pass,
                "imapUseSSL": True,
                "smtpHost": "smtp.office365.com",
                "smtpPort": 587,
                "smtpUsername": email_addr,
                "smtpPassword": email_pass,
                "smtpUseTls": True,
                "fromAddress": email_addr,
                "allowFrom": _comma_to_list(email_allow),
            }

        # Telegram
        if tg_token:
            config["channels"]["telegram"] = {
                "enabled": True,
                "token": tg_token,
                "allowFrom": _comma_to_list(tg_allow),
            }

        # MCP servers
        mcp = config["tools"]["mcpServers"]
        if jira_site and jira_email and jira_token:
            mcp["jira"] = {
                "command": "npx",
                "args": ["-y", "@aashari/mcp-server-atlassian-jira"],
                "env": {
                    "ATLASSIAN_SITE_NAME": jira_site,
                    "ATLASSIAN_USER_EMAIL": jira_email,
                    "ATLASSIAN_API_TOKEN": jira_token,
                },
            }
        if notion_token:
            mcp["notion"] = {
                "command": "npx",
                "args": ["-y", "@notionhq/notion-mcp-server"],
                "env": {"NOTION_TOKEN": notion_token},
            }
        if paper_key:
            mcp["paper_search"] = {
                "command": "uvx",
                "args": ["paper-search-mcp"],
                "env": {"SEMANTIC_SCHOLAR_API_KEY": paper_key},
            }
        if cookies_json:
            mcp["twitter"] = {
                "command": "npx",
                "args": ["-y", "agent-twitter-client-mcp"],
                "env": {
                    "AUTH_METHOD": "cookies",
                    "TWITTER_COOKIES": cookies_json,
                },
            }

        self.collected_secrets = config
        console.print("\n[green]Secrets collected.[/green]\n")

    # ------------------------------------------------------------------
    # Phase 2: CDK Deploy
    # ------------------------------------------------------------------

    def _phase2_cdk_deploy(self) -> None:
        console.rule("[bold]Phase 2: CDK Deploy")

        # Detect public IP
        try:
            resp = httpx.get("https://checkip.amazonaws.com", timeout=5)
            my_ip = resp.text.strip()
        except Exception:
            my_ip = ""

        if my_ip:
            console.print(f"Detected public IP: [cyan]{my_ip}[/cyan]")
        ip_input = Prompt.ask("SSH CIDR (your IP)", default=f"{my_ip}/32" if my_ip else "")
        if not ip_input:
            console.print("[red]SSH CIDR is required for CDK deploy.[/red]")
            raise SystemExit(1)

        # Check if stack exists
        import boto3

        cf = boto3.client("cloudformation", region_name=self.region)
        stack_exists = False
        try:
            cf.describe_stacks(StackName=STACK_NAME)
            stack_exists = True
        except cf.exceptions.ClientError:
            pass

        if stack_exists:
            if not Confirm.ask("Stack already exists. Update it?", default=True):
                console.print("[dim]Skipping CDK deploy.[/dim]")
                # Still load outputs
                outputs = _get_cf_outputs(self.region)
                self._save_outputs(outputs)
                return

        # Run CDK deploy
        cidrs_json = json.dumps([ip_input])
        cmd = [
            "npx", "cdk", "deploy",
            "--require-approval", "never",
            "--context", f"sshCidrs={cidrs_json}",
        ]
        console.print(f"[dim]Running: {' '.join(cmd)}[/dim]\n")

        result = _run(cmd, cwd=self.infra_dir, check=False)
        if result.returncode != 0:
            console.print("[red]CDK deploy failed.[/red]")
            raise SystemExit(1)

        # Parse outputs
        outputs = _get_cf_outputs(self.region)
        self._save_outputs(outputs)
        console.print(f"\n[green]CDK deploy complete.[/green]  IP: {self.state.get('public_ip', '?')}\n")

    def _save_outputs(self, outputs: dict[str, str]) -> None:
        self.state.update({
            "stack_name": STACK_NAME,
            "region": self.region,
            "public_ip": outputs.get("PublicIp", ""),
            "ecr_repo_uri": outputs.get("EcrRepoUri", ""),
            "secret_arn": outputs.get("SecretArn", ""),
            "last_deploy": datetime.now(timezone.utc).isoformat(),
        })
        _save_state(self.state)

    # ------------------------------------------------------------------
    # Phase 3: Push Docker Image
    # ------------------------------------------------------------------

    def _phase3_push_image(self) -> None:
        console.rule("[bold]Phase 3: Push Docker Image")

        ecr_uri = self.state.get("ecr_repo_uri")
        if not ecr_uri:
            # Try loading from CloudFormation
            outputs = _get_cf_outputs(self.region)
            self._save_outputs(outputs)
            ecr_uri = self.state.get("ecr_repo_uri")
        if not ecr_uri:
            console.print("[red]ECR repo URI not found. Run CDK deploy first.[/red]")
            raise SystemExit(1)

        registry = ecr_uri.split("/")[0]

        # ECR auth
        import boto3

        ecr = boto3.client("ecr", region_name=self.region)
        token_resp = ecr.get_authorization_token()
        auth = token_resp["authorizationData"][0]
        token = base64.b64decode(auth["authorizationToken"]).decode()
        username, password = token.split(":", 1)

        console.print("Logging into ECR...")
        _run(
            ["docker", "login", "--username", username, "--password-stdin", registry],
            input=password.encode(),
            check=True,
        )

        console.print("Building image (linux/amd64)...")
        _run(
            ["docker", "build", "--platform", "linux/amd64", "-t", f"{ecr_uri}:latest", "."],
            cwd=self.project_root,
        )

        console.print("Pushing image...")
        _run(["docker", "push", f"{ecr_uri}:latest"])

        console.print("[green]Image pushed.[/green]\n")

    # ------------------------------------------------------------------
    # Phase 4: Upload Secrets
    # ------------------------------------------------------------------

    def _phase4_upload_secrets(self) -> None:
        console.rule("[bold]Phase 4: Upload Secrets")

        secret_arn = self.state.get("secret_arn")
        if not secret_arn:
            outputs = _get_cf_outputs(self.region)
            self._save_outputs(outputs)
            secret_arn = self.state.get("secret_arn")
        if not secret_arn:
            console.print("[red]Secret ARN not found. Run CDK deploy first.[/red]")
            raise SystemExit(1)

        if not self.collected_secrets:
            console.print("[yellow]No secrets collected — skipping.[/yellow]")
            return

        import boto3

        sm = boto3.client("secretsmanager", region_name=self.region)
        sm.put_secret_value(
            SecretId=secret_arn,
            SecretString=json.dumps(self.collected_secrets),
        )
        console.print("[green]Secrets uploaded to Secrets Manager.[/green]\n")

    # ------------------------------------------------------------------
    # Phase 5: Wait for Bootstrap + Start Container
    # ------------------------------------------------------------------

    def _phase5_start_container(self) -> None:
        console.rule("[bold]Phase 5: Start Container")

        ip = self.state.get("public_ip")
        if not ip:
            outputs = _get_cf_outputs(self.region)
            self._save_outputs(outputs)
            ip = self.state.get("public_ip")
        if not ip:
            console.print("[red]Public IP not found. Run CDK deploy first.[/red]")
            raise SystemExit(1)

        ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{ip}"]

        # Wait for bootstrap (up to 5 min)
        console.print("Waiting for instance bootstrap...")
        for attempt in range(30):
            try:
                result = _run(
                    [*ssh_base, "grep -q 'Bootstrap complete' /var/log/nanobot-setup.log"],
                    capture=True,
                    check=False,
                )
                if result.returncode == 0:
                    console.print("[green]Bootstrap complete.[/green]")
                    break
            except Exception:
                pass
            if attempt < 29:
                time.sleep(10)
        else:
            console.print("[yellow]Bootstrap not confirmed after 5 min — proceeding anyway.[/yellow]")

        # Restart nanobot service
        console.print("Restarting nanobot service...")
        _run([*ssh_base, "sudo systemctl restart nanobot"], check=False)

        # Wait for health
        console.print("Waiting for container health...")
        for attempt in range(15):
            try:
                result = _run(
                    [*ssh_base, "sudo docker inspect --format='{{.State.Health.Status}}' nanobot-gateway"],
                    capture=True,
                    check=False,
                )
                status = result.stdout.strip() if result.stdout else ""
                if status == "healthy":
                    console.print("[green]Container is healthy![/green]\n")
                    return
                console.print(f"  [dim]Health: {status or 'waiting'}[/dim]")
            except Exception:
                pass
            time.sleep(2)

        console.print("[yellow]Container not healthy after 30s. Check logs with: ssh ubuntu@{ip} sudo journalctl -u nanobot[/yellow]\n")

    # ------------------------------------------------------------------
    # Phase 6: Upload Workspace
    # ------------------------------------------------------------------

    def _phase6_upload_workspace(self) -> None:
        if not self.with_workspace:
            if not Confirm.ask("Upload SOUL.md, USER.md, AGENTS.md to instance?", default=False):
                return

        console.rule("[bold]Phase 6: Upload Workspace Files")

        ip = self.state.get("public_ip", "")
        if not ip:
            console.print("[red]Public IP not found.[/red]")
            return

        templates_dir = Path(__file__).resolve().parent.parent / "templates"
        remote_dir = "/data/.nanobot/workspace"
        ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no", f"ubuntu@{ip}"]

        # Ensure remote dir exists
        _run([*ssh_base, f"sudo mkdir -p {remote_dir}"], check=False)

        for filename in ("SOUL.md", "USER.md", "AGENTS.md"):
            local = templates_dir / filename
            if not local.exists():
                console.print(f"  [dim]{filename} not found locally, skipping[/dim]")
                continue

            # Optionally open in editor
            editor = os.environ.get("EDITOR", "")
            if editor and Confirm.ask(f"  Edit {filename} in $EDITOR before upload?", default=False):
                subprocess.run([editor, str(local)])

            _run(["scp", "-o", "StrictHostKeyChecking=no", str(local), f"ubuntu@{ip}:/tmp/{filename}"])
            _run([*ssh_base, f"sudo mv /tmp/{filename} {remote_dir}/{filename} && sudo chown root:root {remote_dir}/{filename}"])
            console.print(f"  [green]Uploaded {filename}[/green]")

        console.print()

    # ------------------------------------------------------------------
    # Phase 7: Print GitHub Secrets
    # ------------------------------------------------------------------

    def _phase7_print_github_secrets(self) -> None:
        console.rule("[bold]Phase 7: GitHub Actions Secrets")

        ip = self.state.get("public_ip", "<IP>")

        console.print(
            "\n"
            "[bold]Copy these to your GitHub repo → Settings → Secrets → Actions:[/bold]\n"
            "\n"
            f"  [cyan]AWS_ROLE_ARN[/cyan]      = <create OIDC role — see docs>\n"
            f"  [cyan]AWS_REGION[/cyan]        = {self.region}\n"
            f"  [cyan]LIGHTSAIL_HOST[/cyan]    = {ip}\n"
            f"  [cyan]LIGHTSAIL_SSH_KEY[/cyan]  = <paste Lightsail default SSH private key>\n"
        )

        # Auto-set via gh CLI if available
        if shutil.which("gh"):
            if Confirm.ask("Set secrets automatically via `gh secret set`?", default=False):
                secrets_to_set = {
                    "AWS_REGION": self.region,
                    "LIGHTSAIL_HOST": ip,
                }
                for name, value in secrets_to_set.items():
                    result = _run(
                        ["gh", "secret", "set", name, "--body", value],
                        check=False,
                        capture=True,
                    )
                    if result.returncode == 0:
                        console.print(f"  [green]Set {name}[/green]")
                    else:
                        console.print(f"  [red]Failed to set {name}[/red]")

                console.print(
                    "\n[yellow]Note:[/yellow] You still need to manually set "
                    "[cyan]AWS_ROLE_ARN[/cyan] and [cyan]LIGHTSAIL_SSH_KEY[/cyan].\n"
                )

        console.print("[bold green]Deploy complete![/bold green]\n")
