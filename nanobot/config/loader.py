"""Configuration loading utilities."""

import json
import os
from pathlib import Path

from nanobot.config.schema import Config


# ---------------------------------------------------------------------------
# .env → config.json field mapping
# Keys = environment variable names expected in .env
# Values = dot-separated path into config.json (camelCase for JSON compat)
# ---------------------------------------------------------------------------
_ENV_MAP: dict[str, str] = {
    # --- Agent identity ---
    "AGENT_EMAIL":                  "agentIdentity.email",
    "AGENT_GMAIL_01":               "agentIdentity.email",
    "AGENT_GMAIL_01_APP_PASSWORD":  "agentIdentity.emailAppPassword",

    # --- Agent identity storage (S3) ---
    "AGENT_BUCKET":                 "agents.bucket",
    "AGENT_INSTANCE":               "agents.instance",

    # --- LLM providers ---
    "ANTHROPIC_API_KEY":        "providers.anthropic.apiKey",
    "ANTHROPIC_API_BASE":       "providers.anthropic.apiBase",
    "OPENAI_API_KEY":           "providers.openai.apiKey",
    "OPENAI_API_BASE":          "providers.openai.apiBase",
    "OPENROUTER_API_KEY":       "providers.openrouter.apiKey",
    "OPENROUTER_API_BASE":      "providers.openrouter.apiBase",
    "DEEPSEEK_API_KEY":         "providers.deepseek.apiKey",
    "DEEPSEEK_API_BASE":        "providers.deepseek.apiBase",
    "GEMINI_API_KEY":           "providers.gemini.apiKey",
    "GEMINI_API_BASE":          "providers.gemini.apiBase",
    "GROQ_API_KEY":             "providers.groq.apiKey",
    "GROQ_API_BASE":            "providers.groq.apiBase",
    "HOSTED_VLLM_API_KEY":      "providers.vllm.apiKey",
    "HOSTED_VLLM_API_BASE":     "providers.vllm.apiBase",
    "OPENAI_CODEX_API_KEY":     "providers.openaiCodex.apiKey",
    "OPENAI_CODEX_API_BASE":    "providers.openaiCodex.apiBase",
    "GITHUB_COPILOT_API_KEY":   "providers.githubCopilot.apiKey",
    "GITHUB_COPILOT_API_BASE":  "providers.githubCopilot.apiBase",
    "CUSTOM_API_KEY":           "providers.custom.apiKey",
    "CUSTOM_API_BASE":          "providers.custom.apiBase",

    # --- Agent defaults ---
    "NANOBOT_MODEL":                "agents.defaults.model",
    "NANOBOT_MAX_TOKENS":           "agents.defaults.maxTokens",
    "NANOBOT_TEMPERATURE":          "agents.defaults.temperature",
    "NANOBOT_MAX_TOOL_ITERATIONS":  "agents.defaults.maxToolIterations",
    "NANOBOT_MEMORY_WINDOW":        "agents.defaults.memoryWindow",
    "NANOBOT_WORKSPACE":            "agents.defaults.workspace",

    # --- Channels: global ---
    "CHANNELS_SEND_PROGRESS":   "channels.sendProgress",
    "CHANNELS_SEND_TOOL_HINTS": "channels.sendToolHints",

    # --- Channels: Telegram ---
    "TELEGRAM_BOT_TOKEN":       "channels.telegram.token",
    "TELEGRAM_ENABLED":         "channels.telegram.enabled",
    "TELEGRAM_ALLOW_FROM":      "channels.telegram.allowFrom",
    "TELEGRAM_PROXY":           "channels.telegram.proxy",
    "TELEGRAM_REPLY_TO_MESSAGE": "channels.telegram.replyToMessage",

    # --- Channels: Discord ---
    "DISCORD_BOT_TOKEN":    "channels.discord.token",
    "DISCORD_ENABLED":      "channels.discord.enabled",
    "DISCORD_ALLOW_FROM":   "channels.discord.allowFrom",
    "DISCORD_GATEWAY_URL":  "channels.discord.gatewayUrl",
    "DISCORD_INTENTS":      "channels.discord.intents",

    # --- Channels: Slack ---
    "SLACK_BOT_TOKEN":          "channels.slack.botToken",
    "SLACK_APP_TOKEN":          "channels.slack.appToken",
    "SLACK_ENABLED":            "channels.slack.enabled",
    "SLACK_REPLY_IN_THREAD":    "channels.slack.replyInThread",
    "SLACK_REACT_EMOJI":        "channels.slack.reactEmoji",
    "SLACK_GROUP_POLICY":       "channels.slack.groupPolicy",
    "SLACK_GROUP_ALLOW_FROM":   "channels.slack.groupAllowFrom",
    "SLACK_DM_ENABLED":         "channels.slack.dm.enabled",
    "SLACK_DM_POLICY":          "channels.slack.dm.policy",
    "SLACK_DM_ALLOW_FROM":      "channels.slack.dm.allowFrom",

    # --- Channels: WhatsApp ---
    "WHATSAPP_BRIDGE_TOKEN":    "channels.whatsapp.bridgeToken",
    "WHATSAPP_BRIDGE_URL":      "channels.whatsapp.bridgeUrl",
    "WHATSAPP_ENABLED":         "channels.whatsapp.enabled",
    "WHATSAPP_ALLOW_FROM":      "channels.whatsapp.allowFrom",

    # --- Channels: Email ---
    "EMAIL_ENABLED":            "channels.email.enabled",
    "EMAIL_CONSENT_GRANTED":    "channels.email.consentGranted",
    "EMAIL_IMAP_HOST":          "channels.email.imapHost",
    "EMAIL_IMAP_PORT":          "channels.email.imapPort",
    "EMAIL_IMAP_USERNAME":      "channels.email.imapUsername",
    "EMAIL_IMAP_PASSWORD":      "channels.email.imapPassword",
    "EMAIL_IMAP_MAILBOX":       "channels.email.imapMailbox",
    "EMAIL_IMAP_USE_SSL":       "channels.email.imapUseSsl",
    "EMAIL_SMTP_HOST":          "channels.email.smtpHost",
    "EMAIL_SMTP_PORT":          "channels.email.smtpPort",
    "EMAIL_SMTP_USERNAME":      "channels.email.smtpUsername",
    "EMAIL_SMTP_PASSWORD":      "channels.email.smtpPassword",
    "EMAIL_SMTP_USE_TLS":       "channels.email.smtpUseTls",
    "EMAIL_SMTP_USE_SSL":       "channels.email.smtpUseSsl",
    "EMAIL_FROM_ADDRESS":       "channels.email.fromAddress",
    "EMAIL_AUTO_REPLY_ENABLED": "channels.email.autoReplyEnabled",
    "EMAIL_POLL_INTERVAL":      "channels.email.pollIntervalSeconds",
    "EMAIL_MARK_SEEN":          "channels.email.markSeen",
    "EMAIL_MAX_BODY_CHARS":     "channels.email.maxBodyChars",
    "EMAIL_SUBJECT_PREFIX":     "channels.email.subjectPrefix",
    "EMAIL_ALLOW_FROM":         "channels.email.allowFrom",

    # --- Tools ---
    "BRAVE_API_KEY":                "tools.web.search.apiKey",
    "BRAVE_MAX_RESULTS":            "tools.web.search.maxResults",
    "EXEC_TIMEOUT":                 "tools.exec.timeout",
    "RESTRICT_TO_WORKSPACE":        "tools.restrictToWorkspace",
    "MCP_ALLOWED_COMMANDS":         "tools.mcpAllowedCommands",

    # --- Integrations: Notion ---
    "NOTION_API_KEY":               "integrations.notion.apiKey",
    "NOTION_OAUTH_CLIENT_ID":       "integrations.notion.oauthClientId",
    "NOTION_OAUTH_CLIENT_SECRET":   "integrations.notion.oauthClientSecret",
    "NOTION_AUTHORIZATION_URL":     "integrations.notion.authorizationUrl",
    "NOTION_ROOT_PAGE_ID":          "integrations.notion.rootPageId",
    "NOTION_REDIRECT_URI":          "integrations.notion.redirectUri",

    # --- Integrations: Jira ---
    "JIRA_API_TOKEN":       "integrations.jira.apiToken",
    "JIRA_EMAIL":           "integrations.jira.email",
    "JIRA_BASE_URL":        "integrations.jira.baseUrl",
    "JIRA_DEFAULT_PROJECT": "integrations.jira.defaultProject",

    # --- Gateway ---
    "NANOBOT_GATEWAY_API_KEY":  "gateway.apiKey",
    "NANOBOT_GATEWAY_HOST":     "gateway.host",
    "NANOBOT_GATEWAY_PORT":     "gateway.port",
}


def _load_dotenv() -> None:
    """Load .env file if present. Searches cwd, then ~/.nanobot/."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    # Prefer .env in current working directory, then ~/.nanobot/.env
    for candidate in [Path.cwd() / ".env", Path.home() / ".nanobot" / ".env"]:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def _coerce_value(value: str) -> str | bool | int | float | list:
    """Coerce a string env var to the appropriate Python type.

    - "true"/"false" → bool
    - Comma-separated values → list of strings (e.g. "123,456" → ["123", "456"])
    - Pure integers → int
    - Pure floats → float
    - Everything else → str
    """
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False

    # Comma-separated list (but not URLs or long tokens with commas)
    if "," in value and "://" not in value and len(value) < 500:
        return [item.strip() for item in value.split(",") if item.strip()]

    # Numeric
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    return value


def _inject_env_into_config(data: dict) -> dict:
    """Conditionally merge .env / environment variables into config data.

    Only sets a value if the env var is present AND the config path is
    currently empty or missing — config.json always wins.
    """
    for env_var, dotted_path in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if not value:
            continue

        keys = dotted_path.split(".")
        # Walk to the parent dict, creating intermediate dicts as needed
        node = data
        for key in keys[:-1]:
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]

        leaf = keys[-1]
        # Only inject if the config value is absent or empty
        if not node.get(leaf):
            coerced = _coerce_value(value)
            # Fields that are always lists (e.g. allowFrom) — wrap scalars
            if leaf.endswith("From") or leaf.endswith("Commands"):
                if not isinstance(coerced, list):
                    coerced = [str(coerced)]
            node[leaf] = coerced

    return data


def get_config_path() -> Path:
    """Get the default configuration file path.

    Respects the NANOBOT_CONFIG_PATH env var if set.
    """
    env_path = os.environ.get("NANOBOT_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return Path.home() / ".nanobot" / "config.json"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Loads .env first, then config.json. Environment variables fill in any
    values not already set in config.json (config.json always wins).

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    # Step 1: load .env into os.environ (won't overwrite existing env vars)
    _load_dotenv()

    path = config_path or get_config_path()
    data: dict = {}

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    # Step 2: conditionally merge env vars into config data
    data = _inject_env_into_config(data)

    return Config.model_validate(data) if data else Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
