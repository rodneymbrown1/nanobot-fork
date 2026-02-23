# Nanobot — AWS Lightsail CDK Infrastructure

Deploys nanobot to a single AWS Lightsail instance using AWS CDK (TypeScript).

## What gets created

| Resource | Details |
|----------|---------|
| **Lightsail Instance** | Ubuntu 22.04, `small_3_0` ($10/mo, 2 GB RAM) |
| **Lightsail Disk** | 20 GB persistent disk mounted at `/data` |
| **Lightsail Static IP** | Fixed public IP, survives instance restarts |
| **ECR Repository** | `nanobot` — stores your Docker image |
| **Secrets Manager Secret** | `nanobot/config` — stores all credentials |
| **IAM User + Access Key** | Scoped to ECR pull + config secret read only |
| **Auto Snapshot** | Daily snapshot at 06:00 UTC |

nginx reverse-proxies public traffic (80/443) to the gateway on `127.0.0.1:18790`.
Port 18790 is **not** open to the internet.

---

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) configured (`aws configure`)
- [AWS CDK v2](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html) installed (`npm install -g aws-cdk`)
- [Docker](https://www.docker.com/) running locally (to build the image)
- `jq` installed (used by `put-secret.sh`)
- CDK bootstrapped in your account/region (one-time):

```bash
cdk bootstrap aws://YOUR_ACCOUNT_ID/us-east-1
```

---

## Deploy

### Step 1 — Install dependencies

```bash
cd infra
npm install
```

### Step 2 — (Optional) Customize sizing

Edit `cdk.json` context values:

```json
{
  "context": {
    "availabilityZone": "us-east-1a",
    "bundleId": "small_3_0",
    "diskSizeGb": 20
  }
}
```

**Available `bundleId` options:**

| ID | RAM | vCPU | Price |
|----|-----|------|-------|
| `nano_3_0` | 512 MB | 2 | ~$3.50/mo |
| `micro_3_0` | 1 GB | 2 | ~$5/mo |
| `small_3_0` | 2 GB | 2 | ~$10/mo ✓ recommended |
| `medium_3_0` | 4 GB | 2 | ~$20/mo |

### Step 3 — Deploy the infrastructure

```bash
npm run deploy
```

Save the outputs — you'll need them:

```
NanobotStack.PublicIp         = 1.2.3.4
NanobotStack.EcrRepoUri       = 123456789.dkr.ecr.us-east-1.amazonaws.com/nanobot
NanobotStack.SecretArn        = arn:aws:secretsmanager:us-east-1:...
NanobotStack.SSHCommand       = ssh ubuntu@1.2.3.4
NanobotStack.SetupLogCommand  = ssh ubuntu@1.2.3.4 "sudo tail -f /var/log/nanobot-setup.log"
```

### Step 4 — Build and push the Docker image

```bash
./scripts/push-image.sh
```

This builds the image for `linux/amd64`, authenticates with ECR, and pushes it.

### Step 5 — Set your API keys and credentials

```bash
./scripts/put-secret.sh
```

This walks you through entering your API keys (Anthropic, Telegram token, etc.)
and saves them to Secrets Manager. You can re-run it at any time to update.

> **Manual alternative:** Edit the secret directly in the AWS console under
> Secrets Manager → `nanobot/config`, or use:
> ```bash
> aws secretsmanager put-secret-value \
>   --secret-id nanobot/config \
>   --secret-string file://my-config.json
> ```

### Step 6 — Start nanobot

```bash
ssh ubuntu@<PublicIp> 'sudo systemctl restart nanobot'
```

Watch the logs:

```bash
ssh ubuntu@<PublicIp> 'sudo journalctl -u nanobot -f'
```

---

## Enable HTTPS (recommended)

Point your domain's A record at the static IP, then SSH in and run:

```bash
sudo certbot --nginx -d yourdomain.com
```

Certbot auto-edits the nginx config to add TLS and redirect HTTP → HTTPS.
It also installs a cron job to auto-renew the certificate.

---

## Update the application

```bash
# Rebuild and push a new image
./scripts/push-image.sh

# Pull and restart on the instance
ssh ubuntu@<PublicIp> 'sudo systemctl restart nanobot'
```

---

## Update credentials

```bash
./scripts/put-secret.sh

# Restart to pick up the new config
ssh ubuntu@<PublicIp> 'sudo systemctl restart nanobot'
```

---

## Useful SSH commands

```bash
# View setup log (runs once on first boot)
sudo tail -f /var/log/nanobot-setup.log

# View gateway logs
sudo journalctl -u nanobot -f

# View start script logs
sudo tail -f /var/log/nanobot-start.log

# View nginx logs
sudo tail -f /var/log/nginx/error.log

# Manually start / stop / restart
sudo systemctl restart nanobot
sudo systemctl stop nanobot

# Check running containers
sudo docker ps

# Check nanobot config on disk (contains API keys — handle carefully)
sudo cat /data/.nanobot/config.json
```

---

## Architecture

```
Internet
   │
   ▼  443 / 80
┌──────────────────────────────────┐
│  Lightsail Instance (Ubuntu)     │
│                                  │
│  nginx (:80/:443)                │
│    └─► proxy_pass 127.0.0.1:18790│
│                                  │
│  Docker (nanobot-gateway)        │
│    └─► binds 127.0.0.1:18790     │
│                                  │
│  /data/.nanobot  ◄── Lightsail   │
│  (sessions, memory, config)       │    Disk (20 GB)
└──────────────────────────────────┘
         │                 │
         ▼                 ▼
   ECR (image)     Secrets Manager
                   (nanobot/config)
```

---

## Tear down

```bash
npm run destroy
```

> **Note:** The ECR repository has `RETAIN` removal policy — images are preserved.
> Delete it manually if you no longer need it:
> ```bash
> aws ecr delete-repository --repository-name nanobot --force
> ```
