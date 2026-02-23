---
name: reconciler
description: Auto-commit declarative stack state to the `live` branch after installing skills, MCP servers, or cron jobs. Use after any operation that changes the running stack (skill install, MCP server config, cron job creation) to keep the live branch in sync with the instance.
---

# Reconciler

After installing or removing any skill, MCP server, or cron job, reconcile the `live` branch so it reflects the current running state.

## Workflow

1. Update `stack-manifest.json` in the workspace root (see `references/manifest-schema.md` for format).
2. Regenerate `.env.template` by running `scripts/gen-env-template.sh`.
3. Stage, commit, and push to `live`:

```bash
cd ~/.nanobot/workspace
git add stack-manifest.json .env.template skills/
git pull --rebase origin live || true
git commit -m "reconcile: <short description of what changed>"
git push origin live
```

## Rules

- **Commit prefix**: Always use `reconcile:` prefix. This prevents circular deploys (GitHub Actions skips deploy for reconcile commits).
- **Never commit secrets**: No API keys, tokens, or passwords. The manifest tracks `env_keys` (names only). `.env.template` uses `<REQUIRED_SECRET>` placeholders.
- **Never commit config.json**: It contains secrets and is in `.gitignore`.
- **Rebase on conflict**: If push fails, `git pull --rebase origin live` then retry.
- **Only touch declared files**: `stack-manifest.json`, `.env.template`, and files under `skills/`. Do not commit `memory/`, `data/`, logs, or session state.

## When to Update the Manifest

| Event | Action |
|-------|--------|
| Skill installed | Add entry to `skills` array |
| Skill removed | Remove entry from `skills` array |
| MCP server added to config | Add entry to `mcp_servers` array |
| MCP server removed | Remove entry from `mcp_servers` array |
| Cron job created | Add entry to `cron_jobs` array |
| Cron job removed | Remove entry from `cron_jobs` array |

## Reference Files

- **Manifest schema**: `references/manifest-schema.md` — full schema and field descriptions for `stack-manifest.json`
