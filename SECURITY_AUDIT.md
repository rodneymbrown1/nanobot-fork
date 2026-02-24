# Nanobot Security Audit & Architecture Reference

**Date:** 2026-02-23
**Branch:** `main` (rebased on upstream HKUDS/nanobot + 10 security/infra commits)

---

## Network Architecture Diagram

```
                         INTERNET
                            |
                            v
                    +---------------+
                    |   GitHub      |
                    |   Actions     |
                    | (deploy-live) |
                    +-------+-------+
                            | SSH (port 22)
                            v
  +=========================================================+
  |              AWS LIGHTSAIL INSTANCE                      |
  |                                                         |
  |  +---------------------------------------------------+  |
  |  |  nginx (ports 80/443 - TLS termination)           |  |
  |  |  - Let's Encrypt / certbot                        |  |
  |  |  - proxy_pass -> 127.0.0.1:18790                  |  |
  |  +---------------------------+-----------------------+  |
  |                              |                          |
  |            127.0.0.1 only    v                          |
  |  +---------------------------------------------------+  |
  |  |  Docker: nanobot-gateway                           |  |
  |  |  - Binds 127.0.0.1:18790 (NOT 0.0.0.0)           |  |
  |  |  - CPU: 1 core / Memory: 1GB limit                |  |
  |  |                                                    |  |
  |  |  +------------------+  +------------------------+  |  |
  |  |  |  Agent Loop      |  |  Session Manager       |  |  |
  |  |  |  - LLM calls     |  |  - 30-day TTL          |  |  |
  |  |  |  - Tool dispatch  |  |  - JSONL on disk       |  |  |
  |  |  +------------------+  +------------------------+  |  |
  |  |                                                    |  |
  |  |  +------------------+  +------------------------+  |  |
  |  |  |  Built-in Tools  |  |  MCP Tool Servers      |  |  |
  |  |  |  - shell (exec)  |  |  - stdio or HTTP       |  |  |
  |  |  |  - filesystem    |  |  - 30s timeout         |  |  |
  |  |  |  - web search    |  |  - per-server config   |  |  |
  |  |  |  - cron          |  +------------------------+  |  |
  |  |  |  - message       |                              |  |
  |  |  +------------------+                              |  |
  |  +---------------------------------------------------+  |
  |                                                         |
  |  +---------------------------------------------------+  |
  |  |  Docker: nanobot-bridge (optional)                 |  |
  |  |  - WhatsApp bridge                                 |  |
  |  |  - Binds 127.0.0.1:3001 (WebSocket)               |  |
  |  |  - Optional BRIDGE_TOKEN auth                      |  |
  |  +---------------------------------------------------+  |
  |                                                         |
  |  /data/.nanobot/                                        |
  |  +---------------------------------------------------+  |
  |  |  config.json    (0600 perms, from Secrets Manager) |  |
  |  |  workspace/     (skills, memory, manifest)         |  |
  |  |  sessions/      (JSONL conversation logs)          |  |
  |  +---------------------------------------------------+  |
  |                                                         |
  |  Firewall: OPEN ports 22, 80, 443 only                 |
  |  Port 18790: NOT open (nginx proxied)                   |
  +==========================================================+
           |                    |                    |
           v                    v                    v
  +-------------+    +------------------+   +-----------------+
  |  Channels   |    |  LLM Providers   |   |  External APIs  |
  | (outbound)  |    |  (outbound)      |   |  (outbound)     |
  |-------------|    |------------------|   |-----------------|
  | Discord     |    | Anthropic        |   | Brave Search    |
  | Slack       |    | OpenAI           |   | MCP servers     |
  | Telegram    |    | OpenAI Codex     |   | (HTTP mode)     |
  | WhatsApp    |    | DeepSeek         |   |                 |
  | Feishu      |    | Gemini           |   |                 |
  | DingTalk    |    | OpenRouter       |   |                 |
  | Email       |    | + 10 more        |   |                 |
  | Mochat      |    |                  |   |                 |
  | QQ          |    |                  |   |                 |
  +-------------+    +------------------+   +-----------------+
```

### Data Flow

```
User Message (e.g. Telegram)
    |
    v
Channel.start() --> is_allowed(sender_id) --> allow_from check
    |
    v
_handle_message() --> Bus (InboundMessage)
    |
    v
Agent Loop --> LLM Provider (chat completion)
    |
    v
Tool Calls (shell, filesystem, web, mcp, cron, message)
    |
    v
Response --> Bus (OutboundMessage) --> Channel.send()
    |
    v
User sees reply
```

### CI/CD Pipeline Flow

```
Developer pushes to `live` branch
    |
    v
GitHub Actions (deploy-live.yml)
    |
    +-- Commit starts with "reconcile:" ? --> SKIP (prevents loops)
    |
    v
SSH into Lightsail (LIGHTSAIL_HOST + LIGHTSAIL_SSH_KEY)
    |
    v
/opt/nanobot/apply-live.sh
    |
    +-- git fetch origin live
    +-- git reset --hard origin/live
    +-- Merge mcp_servers from stack-manifest.json into config.json
    +-- systemctl restart nanobot
    |
    v
Nanobot reloads with updated skills/MCP/cron
```

---

## Security Audit Findings

### Summary

| Severity | Count | Areas |
|----------|-------|-------|
| CRITICAL | 5 | Web SSRF, Filesystem sandbox, Gateway auth, Channel defaults, File type restrictions |
| HIGH | 6 | SSH open to all, Shell workspace default, Plaintext credentials, Symlink escape, MCP sandboxing, HTTP auth leak |
| MEDIUM | 7 | No rate limiting, Deny-list approach, Session TTL, No credential rotation, Docker root, MCP SSRF, Container port exposure |
| LOW | 4 | Bridge token optional, No cert pinning, No healthcheck, Stderr leakage |

---

### CRITICAL Findings

#### 1. ~~Web Tool - SSRF (No Private IP Blocklist)~~ FIXED
**File:** `nanobot/agent/tools/web.py`

~~The `web_fetch` tool validates URL scheme (http/https) but does NOT block private IPs.~~

**Fixed in `9886b8b`**: `_is_private_ip()` resolves hostnames via DNS and blocks private, loopback, link-local, reserved, and multicast IPs before connecting. Also strips `user:pass@` credentials from URLs in responses.

#### 2. Filesystem Tool - No Default Sandbox
**File:** `nanobot/agent/tools/filesystem.py`

`allowed_dir` defaults to `None`. When unset, the agent can read/write ANY path on the filesystem: `/etc/passwd`, `/root/.ssh/authorized_keys`, device files.

**Fix:** Default `allowed_dir` to the workspace directory. Require explicit opt-out for unrestricted access.

#### 3. ~~Filesystem Tool - No File Type Restrictions~~ FIXED
**File:** `nanobot/agent/tools/filesystem.py`

~~No restrictions on file types. Agent can read/write device files, named pipes.~~

**Fixed in `e6a70d4`**: `_resolve_path()` now blocks non-regular files (device files, named pipes, sockets). Symlinks are resolved before sandbox check, preventing symlink escape.

#### 4. Gateway - No API Authentication
**File:** `nanobot/cli/commands.py`

The gateway HTTP server has zero authentication. Protected only by localhost binding (127.0.0.1). If binding is changed to 0.0.0.0 or nginx misconfigured, anyone can send messages.

**Fix:** Add optional API key or bearer token authentication at the gateway level.

#### 5. Channels - Default Open Access
**File:** `nanobot/channels/base.py`

When `allow_from` is empty (the default), ALL users on any channel can interact with the bot. A warning is logged once but the bot remains fully operational.

**Fix:** Consider requiring explicit `allow_from` configuration. Or default to deny-all with an explicit `allow_from: ["*"]` for open access.

---

### HIGH Findings

#### 6. SSH Open to 0.0.0.0/0
**File:** `infra/lib/nanobot-stack.ts` (line 142)

Port 22 accepts connections from any IP. Comment says "restrict cidrs to your office/home IP for hardening" but the default is wide open.

**Fix:** Require CIDR configuration parameter with no default. Or default to a narrow range.

#### 7. Shell Tool - Default restrict_to_workspace=False
**File:** `nanobot/config/schema.py`

The shell tool defaults to unrestricted execution. Combined with the deny-list approach (not allowlist), sophisticated command obfuscation could bypass protections.

**Fix:** Default `restrict_to_workspace=True`.

#### 8. Credentials in Plaintext config.json
**File:** `infra/lib/scripts/user-data.sh`

After retrieval from AWS Secrets Manager, all credentials are written to `/data/.nanobot/config.json` as plaintext JSON. File perms are 0600 but any root/container process can read it.

**Fix:** Keep credentials in environment variables only. Or encrypt config.json at rest.

#### 9. ~~Symlink Escape in Filesystem Tool~~ FIXED
**File:** `nanobot/agent/tools/filesystem.py`

~~`Path.resolve()` follows symlinks, enabling sandbox escape via crafted symlinks.~~

**Fixed in `e6a70d4`**: `_resolve_path()` already called `.resolve()` (which follows symlinks) before `.relative_to()` (sandbox check). This was correct behavior — symlinks are resolved first, then the resolved path is verified within `allowed_dir`. Added explicit documentation of this security property.

#### 10. MCP Tools - No Process Sandboxing
**File:** `nanobot/agent/tools/mcp.py`

MCP servers run with the same privileges as nanobot. Config-specified commands are executed directly without validation. Compromised config = arbitrary code execution.

**Fix:** Implement MCP server allowlist. Run MCP servers in isolated containers or with reduced privileges.

#### 11. ~~Web Tool - HTTP Auth Credentials Leaked~~ FIXED
**File:** `nanobot/agent/tools/web.py`

~~URLs with embedded credentials (`http://user:pass@host.com`) appear in response metadata.~~

**Fixed in `9886b8b`**: `_strip_userinfo()` removes `user:pass@` from URLs before including them in response JSON. Both `url` and `finalUrl` fields are sanitized.

---

### MEDIUM Findings

#### 12. No Rate Limiting at nginx
nginx config has no `limit_req` or `limit_conn` directives. Public endpoints are vulnerable to abuse.

#### 13. Shell Deny-List Pattern Matching
Blocks specific patterns (`rm -rf`, `dd`, `shutdown`) but shell variable substitution, encoding, or aliases could bypass the list.

#### 14. 30-Day Session TTL
Sessions persist in memory for 30 days. No periodic cleanup task - stale sessions only evicted on next access.

#### 15. No Credential Rotation Policy
Secrets Manager credentials are never rotated by default.

#### 16. Docker Container Runs as Root
Dockerfile has no `USER` directive. Container processes run as UID 0.

#### 17. MCP HTTP Servers - No SSRF Protection
HTTP-mode MCP servers accept arbitrary URLs with no private IP checks.

#### 18. Docker Port Published in docker-compose
Port 18790 published in docker-compose.yml. Safe in production (nginx proxies) but exposed in local dev.

---

### Your Security Hardening (Applied)

These commits are on your `main` branch:

| Commit | What It Fixed |
|--------|---------------|
| `f1a4c67` Harden default security posture | Gateway default `0.0.0.0` -> `127.0.0.1`, Bridge bound to localhost, `allow_from` warning logged, Mochat retry cap `0` -> `10` |
| `600e43b` Fix shell tool | Process group kill on timeout (`os.killpg`), URL-decode commands before traversal check |
| `51b330b` Remove Codex SSL bypass | Removed `verify=False` fallback, Added 30-day session TTL eviction |
| `b129147` Live branch GitOps | Secrets Manager integration, deploy-key SSH, IAM least-privilege, `reconcile:` loop prevention |
| `dddf552` Bootstrap workspace repo | `ensure-workspace-repo.sh` — idempotent git init, orphan live branch, manifest creation |
| `1d875f3` Harden reconciler skill | Bootstrap call, dirty-check before staging, explicit push error handling |
| `23dc68f` Reconciler hooks | clawhub, cron, skill-creator now remind agent to reconcile after installs |
| `f845cd1` Fix gen-env-template.sh | Creates `stack-manifest.json` if missing instead of skipping |
| `9886b8b` Block SSRF + strip credentials | Private IP blocklist in web tool, strip userinfo from response URLs |
| `e6a70d4` Filesystem hardening | Block device files/pipes/sockets, document symlink-safe resolve |

On the `live` branch:

| Commit | What It Fixed |
|--------|---------------|
| `33e8b0a` Seed built-in skills | Manifest populated with all 9 built-in skills for complete stack snapshot |

### GitOps Reliability Fixes (2026-02-23)

| # | Finding | Severity | Fix |
|---|---------|----------|-----|
| A | Workspace never git-init'd — all reconciler git commands fail on first use | CRITICAL | `ensure-workspace-repo.sh` bootstraps git repo, orphan branch, .gitignore, manifest |
| B | `stack-manifest.json` doesn't exist on first run — jq operations fail | CRITICAL | Both `ensure-workspace-repo.sh` and `gen-env-template.sh` create it if missing |
| C | No hook between skill install and reconciler — state silently diverges | CRITICAL | Added reconciler reminders to clawhub, cron, skill-creator SKILL.md |
| D | Git push failures silently swallowed — agent reports success when push fails | HIGH | Reconciler now checks push exit code and reports auth/network/conflict errors |
| E | No check for unexpected uncommitted changes before staging | HIGH | Reconciler runs `git status --short` and warns before proceeding |
| F | `live` branch doesn't exist on first push | HIGH | `ensure-workspace-repo.sh` creates orphan branch; also pre-created on fork |
| G | Built-in skills not tracked in manifest — incomplete snapshot | MEDIUM | Seeded all 9 built-in skills on live branch |

---

## Default Features

### Channels (9)

| Channel | Protocol | Auth Method | Config Key |
|---------|----------|-------------|------------|
| Discord | WebSocket gateway | Bot token + intents | `channels.discord` |
| Slack | Socket Mode | App token + bot token | `channels.slack` |
| Telegram | HTTP long-polling | Bot token (BotFather) | `channels.telegram` |
| WhatsApp | WebSocket bridge | Optional BRIDGE_TOKEN | `channels.whatsapp` |
| Feishu/Lark | WebSocket | App ID + secret | `channels.feishu` |
| DingTalk | Stream mode | Client ID + secret | `channels.dingtalk` |
| Email | IMAP/SMTP | Username + password | `channels.email` |
| Mochat | Socket.IO (msgpack) | API key | `channels.mochat` |
| QQ | botpy SDK | App ID + secret | `channels.qq` |

### LLM Providers (16)

| Provider | Auth | Key Feature |
|----------|------|-------------|
| Anthropic | API key | Prompt caching support |
| OpenAI | API key | Prompt caching support |
| **OpenAI Codex** | **OAuth** | Uses `gpt-5.1-codex`, SSE streaming, tool calls |
| GitHub Copilot | OAuth | No API key needed |
| DeepSeek | API key | Standard |
| Gemini | API key | Standard |
| OpenRouter | API key | Gateway - routes any model |
| AiHubMix | API key | Gateway |
| SiliconFlow | API key | Gateway |
| VolcEngine | API key | Gateway |
| Zhipu/GLM | API key | Standard |
| DashScope/Qwen | API key | Standard |
| Moonshot/Kimi | API key | Enforces temperature >= 1.0 |
| MiniMax | API key | Standard |
| Groq | API key | Primarily for Whisper transcription |
| vLLM / Custom | API key | Local OpenAI-compatible server |

### Built-in Tools (9 + MCP)

| Tool | Function | Key Restrictions |
|------|----------|------------------|
| `read_file` | Read file contents | Optional `allowed_dir` sandbox |
| `write_file` | Create/overwrite files | Optional `allowed_dir` sandbox |
| `edit_file` | String-replace editing | Optional `allowed_dir` sandbox |
| `list_dir` | List directory contents | Optional `allowed_dir` sandbox |
| `exec` | Run shell commands | Deny-list, optional workspace restrict, 60s timeout |
| `web_search` | Brave Search API | Max 5 results default |
| `web_fetch` | Fetch and parse URLs | 50KB output truncation, 5 redirect max |
| `cron` | Schedule recurring tasks | at/every/cron expressions |
| `message` | Send to channels | Routes through bus |
| MCP tools | Dynamic from config | 30s timeout, stdio or HTTP mode |

### Built-in Skills (10)

| Skill | Purpose | Always On? |
|-------|---------|------------|
| memory | Long-term memory (MEMORY.md + HISTORY.md) | Yes |
| reconciler | Update stack-manifest.json after installs | No |
| clawhub | Browse/install community skills | No |
| cron | Manage scheduled tasks | No |
| github | GitHub operations | No (requires `gh` CLI + GITHUB_TOKEN) |
| skill-creator | Create new skills | No |
| summarize | Summarize conversations | No |
| tmux | Terminal multiplexing | No (requires `tmux`) |
| weather | Weather lookups | No |

---

## When Does Nanobot Use Codex?

The OpenAI Codex provider (`nanobot/providers/openai_codex_provider.py`) is used **only when explicitly selected**:

1. **By model name** - User configures a model containing "codex" (e.g., `openai-codex/gpt-5.1-codex`)
2. **Never as fallback** - OAuth providers are excluded from automatic provider selection
3. **OAuth authentication** - Uses `oauth_cli_kit.get_token()` instead of an API key
4. **Endpoint:** `https://chatgpt.com/backend-api/codex/responses` (SSE streaming)

Codex supports:
- Parallel tool calls
- Reasoning (encrypted content in response)
- SSE streaming with message delta events
- SHA256 cache keys from message history

**It is NOT used by default.** You must explicitly set the model to a codex variant.

---

## Can Nanobot Install New Skills?

**Not at runtime.** Skills are loaded from disk at startup:

- **Builtin skills:** Bundled in `nanobot/skills/` (packaged with the code)
- **Workspace skills:** Placed in `~/.nanobot/workspace/skills/<name>/SKILL.md`
- **Workspace skills override builtins** with the same name

### To install a new skill:

1. Create `workspace/skills/my_skill/SKILL.md` with optional YAML frontmatter:
   ```yaml
   ---
   name: my_skill
   description: What it does
   always: false
   metadata: '{"nanobot": {"requires": {"bins": ["git"], "env": ["MY_TOKEN"]}}}'
   ---
   # My Skill
   Instructions for the agent...
   ```
2. Restart nanobot (or redeploy via live branch push)
3. The reconciler skill updates `stack-manifest.json` to track it

### Skill requirements

Skills can declare dependencies:
- `bins` - CLI tools that must be available (e.g., `git`, `jq`)
- `env` - Environment variables that must be set (e.g., `GITHUB_TOKEN`)

Skills with unmet requirements are filtered from the active list but still visible with a note about what's missing.

---

## Will the GitHub CI/CD Work Automated?

**Yes, with prerequisites.** Here's the checklist:

### Required GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `LIGHTSAIL_HOST` | IP or hostname of the Lightsail instance |
| `LIGHTSAIL_SSH_KEY` | SSH private key for the instance |

### Required Instance State

- [ ] `/opt/nanobot/apply-live.sh` exists and is executable
- [ ] `/data/.nanobot/config.json` exists with API keys populated
- [ ] `jq` and `git` are installed
- [ ] systemd service `nanobot` is configured
- [ ] Git credentials set up for the live branch repo (done by `setup-git-credentials.sh`)
- [ ] Docker installed and nanobot image available

### How It Works

1. Push to `live` branch triggers GitHub Actions
2. If commit starts with `reconcile:` -> **SKIP** (prevents infinite loops)
3. Otherwise -> SSH into instance -> run `apply-live.sh`
4. `apply-live.sh` pulls the latest `live` branch, merges MCP servers from `stack-manifest.json` into `config.json`, restarts nanobot

### Circular Deploy Prevention

When the nanobot agent itself updates `stack-manifest.json` (via the reconciler skill), it commits with a `reconcile:` prefix. GitHub Actions sees this prefix and skips deployment. This prevents:
```
Agent pushes -> GitHub deploys -> Agent restarts -> Agent pushes -> ...
```

### Gaps / What Could Break

1. **No health check after deploy** - `apply-live.sh` restarts the service but doesn't verify it came up healthy
2. **No rollback mechanism** - If the new config breaks nanobot, there's no automatic rollback
3. **Hard reset** - `git reset --hard` discards any local workspace changes on the instance
4. **Single instance** - No blue/green or canary deployment; downtime during restart

### Recommended Improvements

- Add `systemctl is-active nanobot` check after restart with rollback on failure
- Add a `/health` endpoint to the gateway for monitoring
- Keep previous config.json backup before merging
- Add Slack/Discord notification on deploy success/failure
