# Nanobot

A lightweight personal AI assistant framework. Connects to multiple chat channels (Telegram, Slack, Discord, WhatsApp, etc.) and exposes an HTTP gateway for tool-augmented conversations powered by LLMs.

## Local Development

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv sync --dev
uv run nanobot onboard       # interactive config wizard
uv run nanobot gateway        # start the HTTP gateway
```

### Testing

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
```

CI runs lint and test automatically on pushes to `main` and PRs to `main`/`live`.

---

## Deployment (AWS Lightsail)

Nanobot deploys to a single Lightsail instance via AWS CDK. The stack provisions:

- **Lightsail instance** with Docker, nginx, and systemd
- **ECR repository** for the Docker image
- **Secrets Manager secret** for all API keys and credentials
- **Persistent disk** for sessions, memory, and workspace data

### Architecture

```
GitHub (live branch)
  └─ PR merge triggers GitHub Actions
       ├─ Build Docker image → push to ECR
       └─ SSH → apply-live.sh
            ├─ Pull latest live branch
            ├─ Refresh secrets from Secrets Manager → .env.nanobot
            ├─ Patch MCP servers from stack manifest
            └─ Restart container + health check

Lightsail instance
  ├─ nginx (TLS termination) → localhost:18790
  └─ Docker: nanobot-gateway
       ├─ env_file: .env.nanobot (NANOBOT_* vars)
       └─ volumes: sessions, memory, workspace on /data/.nanobot
```

Secrets never touch disk as plaintext config. Secrets Manager JSON is converted to `NANOBOT_*` environment variables written to `/opt/nanobot/.env.nanobot` (mode 0600), which Docker reads via `env_file`.

### Initial Setup

#### 1. Deploy the CDK stack

```bash
cd infra
npm install
npx cdk deploy --context sshCidrs='["YOUR_IP/32"]'
```

This provisions the Lightsail instance, ECR repo, IAM user, and Secrets Manager secret. The instance bootstraps itself via user-data (installs Docker, nginx, systemd service, etc.).

#### 2. Populate secrets

```bash
cd infra
./scripts/put-secret.sh
```

Interactive prompt for API keys (Anthropic, OpenAI, Telegram token, Brave Search, etc.). These are stored in Secrets Manager and converted to env vars at container start.

To add secrets later (e.g. for new MCP servers), update the secret and the next `apply-live.sh` run will pick them up:

```bash
# Read current secret, edit, and re-upload
aws secretsmanager get-secret-value --secret-id <ARN> --query SecretString --output text | \
  jq '.tools.web.search.apiKey = "new-key"' | \
  aws secretsmanager put-secret-value --secret-id <ARN> --secret-string file:///dev/stdin
```

#### 3. Push the Docker image

```bash
cd infra
./scripts/push-image.sh
```

Builds `linux/amd64`, tags as `latest`, and pushes to ECR. After the first deploy, this step is automated by the GitHub Actions workflow on PR merges to `live`.

#### 4. Start nanobot

```bash
ssh root@<INSTANCE_IP> 'systemctl restart nanobot'
```

#### 5. Enable HTTPS (optional)

```bash
ssh root@<INSTANCE_IP> 'certbot --nginx -d yourdomain.com'
```

### GitHub Secrets

The deploy workflow (`.github/workflows/deploy-live.yml`) requires these repository secrets:

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | IAM access key for ECR push |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key for ECR push |
| `AWS_REGION` | AWS region (e.g. `us-east-1`) |
| `LIGHTSAIL_HOST` | Instance public IP or hostname |
| `LIGHTSAIL_SSH_KEY` | SSH private key for root access |

### Deploy Workflow

Deployments are triggered by **merging a PR into the `live` branch** (not by direct pushes). The workflow:

1. Builds the Docker image (`linux/amd64`)
2. Tags with commit SHA + `latest`
3. Pushes to ECR
4. SSHs into the instance and runs `apply-live.sh`

`apply-live.sh` then:
1. Pulls the latest `live` branch into the workspace
2. Re-reads secrets from Secrets Manager (picks up any new API keys)
3. Merges MCP server declarations from `stack-manifest.json`
4. Restarts the container and waits for the health check (15 retries, 2s interval)

### Adding MCP Servers

MCP servers are declared in `stack-manifest.json` on the `live` branch. Their secrets go in Secrets Manager.

1. Add the server's API key to the Secrets Manager secret:
   ```json
   {
     "tools": {
       "mcp_servers": {
         "my_server": {
           "env": { "API_KEY": "secret-value" }
         }
       }
     }
   }
   ```

2. Add the server declaration to `stack-manifest.json`:
   ```json
   {
     "mcp_servers": [
       {
         "name": "my_server",
         "command": "npx",
         "args": ["-y", "@my/mcp-server"]
       }
     ]
   }
   ```

3. Merge a PR to `live` -- the deploy workflow handles the rest.

### Manual Operations

```bash
# Check container status
ssh root@<IP> 'docker ps'
ssh root@<IP> 'docker inspect nanobot-gateway --format="{{.State.Health.Status}}"'

# View logs
ssh root@<IP> 'docker logs nanobot-gateway --tail 50'
ssh root@<IP> 'cat /var/log/nanobot-start.log'
ssh root@<IP> 'cat /var/log/nanobot-apply.log'

# Force redeploy (without PR)
ssh root@<IP> '/opt/nanobot/apply-live.sh'

# Verify no plaintext config on disk
ssh root@<IP> 'ls -la /data/.nanobot/config.json'  # should not exist
ssh root@<IP> 'docker exec nanobot-gateway env | grep NANOBOT'  # env vars loaded
```

## Configuration

Nanobot reads configuration from (in priority order):

1. **Config file** at `~/.nanobot/config.json` (or `NANOBOT_CONFIG_PATH` env var)
2. **Environment variables** prefixed with `NANOBOT_`, using `__` as the nested delimiter

Examples:
```bash
export NANOBOT_GATEWAY__PORT=9999
export NANOBOT_PROVIDERS__ANTHROPIC__API_KEY=sk-ant-...
export NANOBOT_CHANNELS__TELEGRAM__ENABLED=true
```

In production, only env vars are used (no config file on disk).

## License

MIT
