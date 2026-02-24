# Docker Guide

Run nanobot in Docker using the provided `Dockerfile` and `docker-compose.yml`.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2+
- A configured `~/.nanobot/config.json` with at least one LLM provider API key

If you haven't configured nanobot yet, clone the fork and install from source first:

```bash
git clone https://github.com/rodneymbrown1/tim-claw.git
cd /nanobot
nanobot onboard
# Edit ~/.nanobot/config.json — add your API key under providers
```

## Quick Start

### Build the image

```bash
docker compose build
```

### Run the gateway (server mode)

This starts nanobot in gateway mode with all configured channels (WhatsApp, Telegram, Discord, Slack, Email) and the HTTP API on port 18790:

```bash
docker compose up -d nanobot-gateway
```

Check logs:

```bash
docker compose logs -f nanobot-gateway
```

Stop:

```bash
docker compose down
```

### Run a one-off CLI command

```bash
docker compose run --rm nanobot-cli agent -m "Hello, what can you do?"
```

### Interactive chat session

```bash
docker compose run --rm nanobot-cli agent
```

## Manual Docker Commands (without Compose)

### Build

```bash
docker build -t nanobot .
```

### Run the gateway

```bash
docker run -d \
  --name nanobot-gateway \
  -p 18790:18790 \
  -v ~/.nanobot:/root/.nanobot \
  --restart unless-stopped \
  nanobot gateway
```

### Run a single message

```bash
docker run --rm \
  -v ~/.nanobot:/root/.nanobot \
  nanobot agent -m "List my Jira issues"
```

### Check status

```bash
docker run --rm \
  -v ~/.nanobot:/root/.nanobot \
  nanobot status
```

### Manage cron jobs

```bash
# List scheduled jobs
docker run --rm -v ~/.nanobot:/root/.nanobot nanobot cron list

# Add a daily standup reminder (9am ET, weekdays)
docker run --rm -v ~/.nanobot:/root/.nanobot \
  nanobot cron add \
    --name "standup" \
    --message "Run the morning standup routine" \
    --cron "0 9 * * 1-5" \
    --tz "America/New_York"
```

## Volume Mounts

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `~/.nanobot` | `/root/.nanobot` | Config, workspace, sessions, memory, cron jobs |

The `~/.nanobot` directory contains:

- `config.json` — all configuration (API keys, channels, integrations)
- `workspace/` — agent workspace (soul.md, skills, memory)
- `data/` — session history and cron job state

## Environment Variables

All config can also be set via environment variables with the `NANOBOT_` prefix and `__` as the nested delimiter. This is useful for container orchestration:

```bash
docker run -d \
  -p 18790:18790 \
  -v ~/.nanobot:/root/.nanobot \
  -e NANOBOT_PROVIDERS__OPENROUTER__API_KEY=sk-or-... \
  -e NANOBOT_INTEGRATIONS__JIRA__API_TOKEN=your-token \
  -e NANOBOT_INTEGRATIONS__JIRA__EMAIL=you@example.com \
  -e NANOBOT_INTEGRATIONS__JIRA__BASE_URL=https://yourteam.atlassian.net \
  -e NANOBOT_INTEGRATIONS__NOTION__API_KEY=secret_... \
  nanobot gateway
```

## Health Check

The image includes a built-in health check that runs `nanobot status` every 30 seconds:

```bash
docker inspect --format='{{.State.Health.Status}}' nanobot-gateway
```

## Running Tests

A test script validates the Docker build end-to-end:

```bash
bash tests/test_docker.sh
```

This builds the image, runs `onboard` and `status`, and checks the output for expected fields.

## Resource Limits

The Compose file sets default resource limits for the gateway:

- **CPU**: 1 core (0.25 reserved)
- **Memory**: 1 GB (256 MB reserved)

Adjust in `docker-compose.yml` under `deploy.resources` if needed.

## Troubleshooting

**Container exits immediately**
Check logs with `docker compose logs nanobot-gateway`. The most common cause is a missing API key in `config.json`.

**WhatsApp QR code not showing**
The bridge needs an interactive terminal. Run the login flow outside Docker first:
```bash
nanobot channels login
```
The session is saved in `~/.nanobot` and persists into the container via the volume mount.

**Port conflict on 18790**
Change the host port in `docker-compose.yml` or pass a different port:
```bash
docker run -d -p 9999:18790 -v ~/.nanobot:/root/.nanobot nanobot gateway
```

**Permission issues on Linux**
If nanobot can't read `~/.nanobot` inside the container, ensure the directory is owned by root or readable by UID 0:
```bash
sudo chown -R root:root ~/.nanobot
```
