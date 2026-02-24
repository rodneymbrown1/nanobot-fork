# nanobot

A lightweight personal AI assistant that runs 24/7 on your own infrastructure. Connects to Telegram, Email (Outlook), and more via a unified gateway.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Node.js 18+ (for WhatsApp bridge and CDK deploy)
- Docker (for cloud deploy)
- AWS account with credentials configured (`aws configure`)

## Local Development

```bash
# Install dependencies
uv sync

# First-time setup — creates config and workspace
uv run nanobot onboard

# Edit config with your API key
# Get one at https://openrouter.ai/keys
vim ~/.nanobot/config.json

# Single message
uv run nanobot agent -m "Hello!"

# Interactive chat
uv run nanobot agent

# Start the gateway (all channels)
uv run nanobot gateway
```

## Deploy to AWS

A single command provisions infrastructure (Lightsail + ECR), builds and pushes the Docker image, uploads secrets, and starts the container.

### Quick Start

```bash
uv run nanobot deploy
```

This walks you through 7 phases interactively:

1. **Prerequisites check** — verifies node, docker, AWS creds
2. **Collect secrets** — gateway key, email, Telegram, Brave, MCP servers (Jira, Notion, etc.)
3. **CDK deploy** — provisions Lightsail instance, ECR repo, Secrets Manager, static IP
4. **Push Docker image** — builds `linux/amd64` and pushes to ECR
5. **Upload secrets** — writes config JSON to Secrets Manager
6. **Start container** — waits for bootstrap, restarts service, checks health
7. **GitHub secrets** — prints (or auto-sets) values for CI/CD

### Partial Runs

```bash
# Re-deploy just secrets (e.g. after changing a token)
nanobot deploy --secrets-only

# Rebuild and push image only
nanobot deploy --image-only

# Restart the container on the instance
nanobot deploy --restart-only

# Full deploy but skip CDK (infra already exists)
nanobot deploy --skip-cdk

# Full deploy but skip image build
nanobot deploy --skip-image

# Upload SOUL.md, USER.md, AGENTS.md to the instance
nanobot deploy --with-workspace

# Deploy to a different region
nanobot deploy --region us-west-2
```

### What Gets Created

| Resource | Details |
|----------|---------|
| Lightsail instance | Ubuntu 22.04, `small_3_0` (2 vCPU, 2GB RAM) |
| Lightsail disk | 20GB persistent at `/data` |
| Static IP | Stable address for DNS |
| ECR repository | `nanobot` — stores Docker images |
| Secrets Manager | `nanobot/config` — full config JSON |
| IAM user | `nanobot-instance` — ECR pull + secrets read |

### Deploy State

State is persisted to `~/.nanobot/deploy-state.json` between runs. Re-running `nanobot deploy` detects the existing stack and offers to update or skip.

### GitHub Actions (CI/CD)

After deploy, set these secrets in your GitHub repo (Settings > Secrets > Actions):

| Secret | Value |
|--------|-------|
| `AWS_ROLE_ARN` | OIDC role ARN (see AWS docs) |
| `AWS_REGION` | `us-east-1` (or your region) |
| `LIGHTSAIL_HOST` | Instance public IP |
| `LIGHTSAIL_SSH_KEY` | Lightsail default SSH private key |

## Other Commands

```bash
# Check status
nanobot status

# Channel status
nanobot channels status

# WhatsApp QR login
nanobot channels login

# Manage scheduled jobs
nanobot cron list
nanobot cron add --name "daily-brief" --cron "0 9 * * *" --tz "America/New_York" -m "Give me a morning briefing"
nanobot cron remove <job-id>

# OAuth login (OpenAI Codex, GitHub Copilot)
nanobot provider login openai-codex
```

## Project Structure

```
nanobot/
  cli/          # CLI commands (typer)
  agent/        # Agent loop and context
  channels/     # Telegram, Email, WhatsApp, Slack, Discord
  bus/          # Message bus (inbound/outbound)
  config/       # Config schema and loader
  cron/         # Scheduled jobs
  heartbeat/    # Periodic self-check
  providers/    # LLM providers (LiteLLM, OpenAI Codex, custom)
  templates/    # SOUL.md, USER.md, AGENTS.md defaults
  tools/        # Built-in tools (exec, web, files, memory)
infra/          # CDK stack + deploy scripts
bridge/         # WhatsApp bridge (Node.js)
```

## License

MIT
