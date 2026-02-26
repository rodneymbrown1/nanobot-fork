"""Unified deploy flow for nanobot on AWS Lightsail.

Orchestrates: prerequisites check, secrets collection, CDK deploy,
Docker image push, secrets upload, container start, and workspace upload.

Supports multi-instance deployments: shared stack (ECR, IAM, org secret)
plus per-instance stacks (Lightsail, disk, static IP, instance secret).
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
SHARED_STACK_NAME = "NanobotSharedStack"


def _state_file(instance: str) -> Path:
    return STATE_DIR / f"deploy-state-{instance}.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_state(instance: str) -> dict:
    f = _state_file(instance)
    if f.exists():
        return json.loads(f.read_text())
    return {}


def _save_state(instance: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_file(instance).write_text(json.dumps(state, indent=2))


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


def _get_cf_outputs(stack_name: str, region: str) -> dict[str, str]:
    """Fetch CloudFormation stack outputs as {key: value}."""
    import boto3

    cf = boto3.client("cloudformation", region_name=region)
    resp = cf.describe_stacks(StackName=stack_name)
    outputs = resp["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


# ---------------------------------------------------------------------------
# DeployFlow
# ---------------------------------------------------------------------------


class DeployFlow:
    def __init__(
        self,
        *,
        instance: str = "nanobot",
        region: str = "us-east-1",
        secrets_only: bool = False,
        image_only: bool = False,
        restart_only: bool = False,
        skip_cdk: bool = False,
        skip_image: bool = False,
        with_workspace: bool = False,
    ):
        self.instance = instance
        self.region = region
        self.secrets_only = secrets_only
        self.image_only = image_only
        self.restart_only = restart_only
        self.skip_cdk = skip_cdk
        self.skip_image = skip_image
        self.with_workspace = with_workspace

        self.instance_stack_name = f"Nanobot-{instance}"
        self.state = _load_state(instance)
        self.org_secrets: dict = {}
        self.instance_secrets: dict = {}

        # Project root = repo root (where Dockerfile lives)
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.infra_dir = self.project_root / "infra"

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def run(self) -> None:
        console.print(f"\n[bold cyan]nanobot deploy[/bold cyan]  instance=[cyan]{self.instance}[/cyan]\n")

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
    # Phase 1: Collect Secrets (split into org + instance)
    # ------------------------------------------------------------------

    def _phase1_collect_secrets(self) -> None:
        console.rule("[bold]Phase 1: Collect Secrets")

        # Try loading existing secrets from AWS for re-run defaults
        existing_org: dict = {}
        existing_instance: dict = {}

        import boto3
        sm = boto3.client("secretsmanager", region_name=self.region)

        # Try org secret
        try:
            resp = sm.get_secret_value(SecretId="nanobot/org")
            existing_org = json.loads(resp["SecretString"])
            console.print("[dim]Loaded existing org secrets for defaults.[/dim]")
        except Exception:
            pass

        # Try instance secret
        try:
            resp = sm.get_secret_value(SecretId=f"nanobot/instance/{self.instance}")
            existing_instance = json.loads(resp["SecretString"])
            console.print("[dim]Loaded existing instance secrets for defaults.[/dim]")
        except Exception:
            pass

        # Fall back to legacy single secret for migration ease
        existing_legacy: dict = {}
        if not existing_org and not existing_instance:
            secret_arn = self.state.get("secret_arn")
            if secret_arn:
                try:
                    resp = sm.get_secret_value(SecretId=secret_arn)
                    existing_legacy = json.loads(resp["SecretString"])
                    console.print("[dim]Loaded legacy nanobot/config for defaults.[/dim]")
                except Exception:
                    pass

        # Merge for display defaults: org < instance < legacy
        existing = {**existing_legacy, **existing_org}
        # Deep merge instance on top
        for k, v in existing_instance.items():
            if k in existing and isinstance(existing[k], dict) and isinstance(v, dict):
                existing[k] = {**existing[k], **v}
            else:
                existing[k] = v

        # ── Org-level secrets (shared across instances) ──────────────
        console.print("\n[bold underline]Org-level secrets (shared across all instances)[/bold underline]")

        # Skip org collection if org secret already exists and user wants to keep it
        skip_org = False
        if existing_org:
            skip_org = not Confirm.ask("Org secret already exists. Re-enter org keys?", default=False)

        if not skip_org:
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

            org_config: dict = {
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
                },
                "tools": {
                    "restrictToWorkspace": True,
                    "web": {"search": {"apiKey": brave_key or ""}},
                    "mcpAllowedCommands": ["npx", "uvx"],
                    "mcpServers": {},
                },
            }

            mcp = org_config["tools"]["mcpServers"]
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

            self.org_secrets = org_config

        # ── Instance-level secrets (per-instance) ────────────────────
        console.print(f"\n[bold underline]Instance secrets for '{self.instance}'[/bold underline]")

        # -- Gateway --
        console.print("\n[bold]Gateway[/bold]")
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

        instance_config: dict = {
            "gateway": {
                "apiKey": api_key or "",
            },
            "channels": {},
        }

        if email_addr and email_pass:
            instance_config["channels"]["email"] = {
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

        if tg_token:
            instance_config["channels"]["telegram"] = {
                "enabled": True,
                "token": tg_token,
                "allowFrom": _comma_to_list(tg_allow),
            }

        self.instance_secrets = instance_config
        console.print("\n[green]Secrets collected.[/green]\n")

    # ------------------------------------------------------------------
    # Phase 2: CDK Deploy (shared + instance)
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

        import boto3

        cf = boto3.client("cloudformation", region_name=self.region)

        # Deploy shared stack first (if needed)
        shared_exists = False
        try:
            cf.describe_stacks(StackName=SHARED_STACK_NAME)
            shared_exists = True
        except cf.exceptions.ClientError:
            pass

        if shared_exists:
            console.print("[dim]Shared stack already exists.[/dim]")
            if Confirm.ask("Update shared stack?", default=False):
                self._deploy_stack(SHARED_STACK_NAME, ip_input)
        else:
            console.print("Deploying shared stack...")
            self._deploy_stack(SHARED_STACK_NAME, ip_input)

        # Load shared outputs
        shared_outputs = _get_cf_outputs(SHARED_STACK_NAME, self.region)
        self.state["ecr_repo_uri"] = shared_outputs.get("EcrRepoUri", "")
        self.state["org_secret_arn"] = shared_outputs.get("OrgSecretArn", "")

        # Deploy instance stack
        instance_exists = False
        try:
            cf.describe_stacks(StackName=self.instance_stack_name)
            instance_exists = True
        except cf.exceptions.ClientError:
            pass

        if instance_exists:
            if not Confirm.ask(f"Instance stack '{self.instance_stack_name}' exists. Update?", default=True):
                console.print("[dim]Skipping instance CDK deploy.[/dim]")
                instance_outputs = _get_cf_outputs(self.instance_stack_name, self.region)
                self._save_instance_outputs(instance_outputs)
                return

        console.print(f"Deploying instance stack: {self.instance_stack_name}")
        self._deploy_stack(self.instance_stack_name, ip_input)

        instance_outputs = _get_cf_outputs(self.instance_stack_name, self.region)
        self._save_instance_outputs(instance_outputs)
        console.print(f"\n[green]CDK deploy complete.[/green]  IP: {self.state.get('public_ip', '?')}\n")

    def _deploy_stack(self, stack_name: str, ssh_cidr: str) -> None:
        """Run CDK deploy for a specific stack."""
        cidrs_json = json.dumps([ssh_cidr])
        instances_json = json.dumps([self.instance])
        cmd = [
            "npx", "cdk", "deploy", stack_name,
            "--require-approval", "never",
            "--context", f"sshCidrs={cidrs_json}",
            "--context", f"instances={instances_json}",
        ]
        console.print(f"[dim]Running: {' '.join(cmd)}[/dim]\n")

        result = _run(cmd, cwd=self.infra_dir, check=False)
        if result.returncode != 0:
            console.print(f"[red]CDK deploy of {stack_name} failed.[/red]")
            raise SystemExit(1)

    def _save_instance_outputs(self, outputs: dict[str, str]) -> None:
        self.state.update({
            "instance_stack_name": self.instance_stack_name,
            "region": self.region,
            "public_ip": outputs.get("PublicIp", ""),
            "instance_secret_arn": outputs.get("InstanceSecretArn", ""),
            "last_deploy": datetime.now(timezone.utc).isoformat(),
        })
        _save_state(self.instance, self.state)

    # ------------------------------------------------------------------
    # Phase 3: Push Docker Image
    # ------------------------------------------------------------------

    def _phase3_push_image(self) -> None:
        console.rule("[bold]Phase 3: Push Docker Image")

        ecr_uri = self.state.get("ecr_repo_uri")
        if not ecr_uri:
            # Try loading from shared CloudFormation stack
            try:
                shared_outputs = _get_cf_outputs(SHARED_STACK_NAME, self.region)
                ecr_uri = shared_outputs.get("EcrRepoUri", "")
                self.state["ecr_repo_uri"] = ecr_uri
            except Exception:
                pass
        if not ecr_uri:
            console.print("[red]ECR repo URI not found. Deploy shared stack first.[/red]")
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
    # Phase 4: Upload Secrets (org + instance)
    # ------------------------------------------------------------------

    def _phase4_upload_secrets(self) -> None:
        console.rule("[bold]Phase 4: Upload Secrets")

        import boto3
        sm = boto3.client("secretsmanager", region_name=self.region)

        # Upload org secrets (if collected)
        if self.org_secrets:
            org_arn = self.state.get("org_secret_arn")
            if not org_arn:
                try:
                    shared_outputs = _get_cf_outputs(SHARED_STACK_NAME, self.region)
                    org_arn = shared_outputs.get("OrgSecretArn", "")
                except Exception:
                    pass
            if not org_arn:
                # Try direct lookup
                try:
                    resp = sm.describe_secret(SecretId="nanobot/org")
                    org_arn = resp["ARN"]
                except Exception:
                    pass

            if org_arn:
                sm.put_secret_value(
                    SecretId=org_arn,
                    SecretString=json.dumps(self.org_secrets),
                )
                console.print("[green]Org secrets uploaded to nanobot/org.[/green]")
            else:
                console.print("[yellow]Org secret ARN not found — skipping org upload.[/yellow]")

        # Upload instance secrets
        if self.instance_secrets:
            instance_arn = self.state.get("instance_secret_arn")
            if not instance_arn:
                try:
                    instance_outputs = _get_cf_outputs(self.instance_stack_name, self.region)
                    instance_arn = instance_outputs.get("InstanceSecretArn", "")
                except Exception:
                    pass
            if not instance_arn:
                try:
                    resp = sm.describe_secret(SecretId=f"nanobot/instance/{self.instance}")
                    instance_arn = resp["ARN"]
                except Exception:
                    pass

            if instance_arn:
                sm.put_secret_value(
                    SecretId=instance_arn,
                    SecretString=json.dumps(self.instance_secrets),
                )
                console.print(f"[green]Instance secrets uploaded to nanobot/instance/{self.instance}.[/green]")
            else:
                console.print("[yellow]Instance secret ARN not found — skipping instance upload.[/yellow]")

        if not self.org_secrets and not self.instance_secrets:
            console.print("[yellow]No secrets collected — skipping.[/yellow]")

        console.print()

    # ------------------------------------------------------------------
    # Phase 5: Wait for Bootstrap + Start Container
    # ------------------------------------------------------------------

    def _phase5_start_container(self) -> None:
        console.rule("[bold]Phase 5: Start Container")

        ip = self.state.get("public_ip")
        if not ip:
            try:
                instance_outputs = _get_cf_outputs(self.instance_stack_name, self.region)
                self._save_instance_outputs(instance_outputs)
                ip = self.state.get("public_ip")
            except Exception:
                pass
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

        console.print(f"[yellow]Container not healthy after 30s. Check logs with: ssh ubuntu@{ip} sudo journalctl -u nanobot[/yellow]\n")

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

        # If S3 bucket is configured, pull identity files from S3 on the instance
        agent_bucket = os.environ.get("AGENT_BUCKET", "")
        agent_instance = os.environ.get("AGENT_INSTANCE", self.instance)

        remote_dir = "/data/.nanobot/workspace"
        ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no", f"ubuntu@{ip}"]

        # Ensure remote dir exists
        _run([*ssh_base, f"sudo mkdir -p {remote_dir}"], check=False)

        if agent_bucket and agent_instance:
            console.print(f"Syncing identity from [cyan]s3://{agent_bucket}/{agent_instance}/[/cyan]")
            s3_cmd = f'aws s3 sync "s3://{agent_bucket}/{agent_instance}/" {remote_dir}/ --include "*.md"'
            result = _run([*ssh_base, s3_cmd], check=False)
            if result.returncode == 0:
                console.print("[green]Identity files synced from S3.[/green]")
            else:
                console.print("[yellow]S3 sync failed — falling back to local scp.[/yellow]")
                self._phase6_scp_fallback(ip, ssh_base, remote_dir)
        else:
            self._phase6_scp_fallback(ip, ssh_base, remote_dir)

        console.print()

    def _phase6_scp_fallback(self, ip: str, ssh_base: list[str], remote_dir: str) -> None:
        """Upload workspace files via scp (fallback when S3 is not configured)."""
        templates_dir = Path(__file__).resolve().parent.parent / "templates"

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
