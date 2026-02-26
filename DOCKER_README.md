# Docker Dev Guide

## Prerequisites

- Docker Engine 20.10+ with Compose v2+
- A `.env` file in this directory with your API keys (see `.env` for all options)

## Dev Container Commands

```bash
# Build
docker compose -f docker-compose.dev.yml build

# Start the gateway (detached)
docker compose -f docker-compose.dev.yml up -d nanobot-gateway

# View logs
docker compose -f docker-compose.dev.yml logs -f nanobot-gateway

# Send a test message
docker compose -f docker-compose.dev.yml exec nanobot-gateway nanobot agent -m "hello"

# Check status
docker compose -f docker-compose.dev.yml exec nanobot-gateway nanobot status

# Stop
docker compose -f docker-compose.dev.yml down

# Rebuild after code changes
docker compose -f docker-compose.dev.yml up -d --build nanobot-gateway
```

## Volume Mounts

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `~/.nanobot` | `/root/.nanobot` | Config, workspace, sessions, memory |

## Troubleshooting

- **Container exits immediately** — check logs, usually a missing API key in `.env`
- **Port conflict on 18790** — change the host port in `docker-compose.dev.yml`
