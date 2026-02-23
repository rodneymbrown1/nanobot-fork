# stack-manifest.json Schema

The manifest is a JSON file at the workspace root that declares the full running stack.

## Top-Level Structure

```json
{
  "version": 1,
  "skills": [],
  "mcp_servers": [],
  "cron_jobs": [],
  "env_shape": {}
}
```

## Fields

### `version` (integer, required)

Schema version. Currently `1`.

### `skills` (array)

Each entry:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Skill directory name (e.g. `weather`) |
| `source` | string | Origin: `builtin`, `clawhub:<id>`, or `custom` |
| `installed_at` | string | ISO 8601 timestamp |

Example:

```json
{
  "name": "hello-world",
  "source": "custom",
  "installed_at": "2026-02-23T12:00:00Z"
}
```

### `mcp_servers` (array)

Each entry:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Server key in `config.json` tools.mcp_servers |
| `command` | string | Stdio command (e.g. `npx`) |
| `args` | array | Command arguments |
| `url` | string | HTTP endpoint (if HTTP mode) |
| `env_keys` | array | Environment variable **names** required (never values) |
| `installed_at` | string | ISO 8601 timestamp |

Example:

```json
{
  "name": "filesystem",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
  "env_keys": [],
  "installed_at": "2026-02-23T12:00:00Z"
}
```

### `cron_jobs` (array)

Each entry:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Cron job ID from the cron system |
| `message` | string | Task description or reminder text |
| `schedule` | string | Human-readable schedule (e.g. `every 1h`, `0 9 * * 1-5`) |
| `installed_at` | string | ISO 8601 timestamp |

Example:

```json
{
  "id": "abc123",
  "message": "Check GitHub stars and report",
  "schedule": "every 600s",
  "installed_at": "2026-02-23T12:00:00Z"
}
```

### `env_shape` (object)

Auto-generated map of flattened config paths to their placeholder type. Keys use `NANOBOT_` prefix with `__` as nested delimiter (matching Pydantic `env_prefix` and `env_nested_delimiter` in `config/schema.py`).

Secret-like values (keys containing `KEY`, `TOKEN`, `SECRET`, or `PASSWORD`) get `<REQUIRED_SECRET>` placeholder. Others get `<VALUE>`.

Example:

```json
{
  "NANOBOT_PROVIDERS__ANTHROPIC__API_KEY": "<REQUIRED_SECRET>",
  "NANOBOT_CHANNELS__TELEGRAM__TOKEN": "<REQUIRED_SECRET>",
  "NANOBOT_GATEWAY__PORT": "<VALUE>"
}
```

This map is used by `scripts/gen-env-template.sh` to produce the `.env.template` file.
