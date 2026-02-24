#!/bin/bash
# =============================================================================
# Populate the Secrets Manager secret with your real nanobot config.
# Reads the secret ARN from CDK outputs, then prompts for each credential.
# =============================================================================
set -euo pipefail

STACK_NAME="${STACK_NAME:-NanobotStack}"
REGION="${AWS_DEFAULT_REGION:-$(aws configure get region)}"

echo "Fetching secret ARN from CloudFormation outputs..."
SECRET_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='SecretArn'].OutputValue" \
  --output text)

if [ -z "$SECRET_ARN" ]; then
  echo "ERROR: Could not find SecretArn in stack $STACK_NAME outputs."
  echo "       Make sure you have run 'cdk deploy' first."
  exit 1
fi

echo "Secret ARN: $SECRET_ARN"
echo ""
echo "Enter your credentials (press Enter to skip optional fields):"
echo ""

# Read current secret
CURRENT=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ARN" \
  --query SecretString \
  --output text 2>/dev/null || echo '{}')

# ── Gateway ─────────────────────────────────────────────────────────────────
echo "=== Gateway ==="
read -rsp "Gateway API key (Bearer token for HTTP requests): " GATEWAY_KEY; echo

# ── Email (Outlook) ─────────────────────────────────────────────────────────
echo ""
echo "=== Email (Outlook) ==="
read -rp "Outlook email address: " EMAIL_ADDRESS
read -rsp "Outlook app password (generate at account.microsoft.com/security): " EMAIL_PASSWORD; echo
read -rp "Allowed sender addresses (comma-separated, empty = accept all): " EMAIL_ALLOW_FROM

# ── Telegram (optional) ────────────────────────────────────────────────────
echo ""
echo "=== Telegram (optional) ==="
read -rp "Telegram bot token (from @BotFather, optional): " TELEGRAM_TOKEN
read -rp "Telegram allowed user IDs (comma-separated): " TELEGRAM_ALLOW_FROM

# ── Web search ──────────────────────────────────────────────────────────────
echo ""
echo "=== Web Search ==="
read -rsp "Brave Search API key (optional): " BRAVE_KEY; echo

# ── MCP: Jira ───────────────────────────────────────────────────────────────
echo ""
echo "=== Jira MCP ==="
read -rp "Atlassian site name (e.g. mycompany for mycompany.atlassian.net): " JIRA_SITE
read -rp "Atlassian email: " JIRA_EMAIL
read -rsp "Atlassian API token (from id.atlassian.com/manage-profile/security/api-tokens): " JIRA_TOKEN; echo

# ── MCP: Notion ─────────────────────────────────────────────────────────────
echo ""
echo "=== Notion MCP ==="
read -rsp "Notion integration token (ntn_..., from notion.so/profile/integrations): " NOTION_TOKEN; echo

# ── MCP: Paper Search ───────────────────────────────────────────────────────
echo ""
echo "=== Paper Search MCP ==="
read -rsp "Semantic Scholar API key (optional, from semanticscholar.org/product/api): " SEMANTIC_KEY; echo

# ── MCP: X.com (cookie-based) ──────────────────────────────────────────────
echo ""
echo "=== X.com / Twitter MCP (cookie-based) ==="
echo "Extract cookies from browser DevTools → Application → Cookies → x.com"
echo "Needed: auth_token, ct0, twid values"
read -rsp "Twitter cookies JSON (e.g. [{\"name\":\"auth_token\",\"value\":\"...\"},...] ): " TWITTER_COOKIES; echo

echo ""
echo "Building config JSON..."

# Convert comma-separated allow_from lists to JSON arrays
to_json_array() {
  local input="$1"
  if [ -z "$input" ]; then
    echo "[]"
    return
  fi
  IFS=',' read -ra arr <<< "$input"
  printf '%s\n' "${arr[@]}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | jq -R . | jq -s .
}

TELEGRAM_ALLOW_JSON=$(to_json_array "${TELEGRAM_ALLOW_FROM:-}")
EMAIL_ALLOW_JSON=$(to_json_array "${EMAIL_ALLOW_FROM:-}")

TELEGRAM_ENABLED="false"
if [ -n "${TELEGRAM_TOKEN:-}" ] && [ "$TELEGRAM_TOKEN" != "REPLACE_ME" ]; then
  TELEGRAM_ENABLED="true"
fi

CONFIG=$(jq -n \
  --arg gateway_key "${GATEWAY_KEY:-}" \
  --arg email_addr "${EMAIL_ADDRESS:-}" \
  --arg email_pass "${EMAIL_PASSWORD:-}" \
  --argjson email_allow "$EMAIL_ALLOW_JSON" \
  --arg telegram_token "${TELEGRAM_TOKEN:-}" \
  --argjson telegram_enabled "$TELEGRAM_ENABLED" \
  --argjson telegram_allow "$TELEGRAM_ALLOW_JSON" \
  --arg brave_key "${BRAVE_KEY:-}" \
  --arg jira_site "${JIRA_SITE:-}" \
  --arg jira_email "${JIRA_EMAIL:-}" \
  --arg jira_token "${JIRA_TOKEN:-}" \
  --arg notion_token "${NOTION_TOKEN:-}" \
  --arg semantic_key "${SEMANTIC_KEY:-}" \
  --arg twitter_cookies "${TWITTER_COOKIES:-}" \
  '{
    agents: {
      defaults: {
        model: "openai-codex/gpt-5.1-codex",
        maxTokens: 8192,
        temperature: 0.1,
        maxToolIterations: 40,
        memoryWindow: 100
      }
    },
    providers: {},
    gateway: {
      host: "127.0.0.1",
      port: 18790,
      apiKey: $gateway_key
    },
    channels: {
      telegram: {
        enabled: $telegram_enabled,
        token: $telegram_token,
        allowFrom: $telegram_allow
      },
      email: {
        enabled: true,
        consentGranted: true,
        imapHost: "outlook.office365.com",
        imapPort: 993,
        imapUsername: $email_addr,
        imapPassword: $email_pass,
        imapUseSSL: true,
        smtpHost: "smtp.office365.com",
        smtpPort: 587,
        smtpUsername: $email_addr,
        smtpPassword: $email_pass,
        smtpUseTls: true,
        fromAddress: $email_addr,
        allowFrom: $email_allow
      }
    },
    tools: {
      restrictToWorkspace: true,
      web: {
        search: { apiKey: $brave_key }
      },
      mcpAllowedCommands: ["npx", "uvx"],
      mcpServers: {
        jira: {
          command: "npx",
          args: ["-y", "@aashari/mcp-server-atlassian-jira"],
          env: {
            ATLASSIAN_SITE_NAME: $jira_site,
            ATLASSIAN_USER_EMAIL: $jira_email,
            ATLASSIAN_API_TOKEN: $jira_token
          }
        },
        notion: {
          command: "npx",
          args: ["-y", "@notionhq/notion-mcp-server"],
          env: {
            NOTION_TOKEN: $notion_token
          }
        },
        paper_search: {
          command: "uvx",
          args: ["paper-search-mcp"],
          env: {
            SEMANTIC_SCHOLAR_API_KEY: $semantic_key
          }
        },
        twitter: {
          command: "npx",
          args: ["-y", "agent-twitter-client-mcp"],
          env: {
            AUTH_METHOD: "cookies",
            TWITTER_COOKIES: $twitter_cookies
          }
        }
      }
    }
  }')

echo ""
echo "Uploading config to Secrets Manager..."
aws secretsmanager put-secret-value \
  --secret-id "$SECRET_ARN" \
  --secret-string "$CONFIG" \
  --region "$REGION"

echo ""
echo "✓ Config updated in Secrets Manager."
echo ""
echo "To apply on the instance:"
echo "  ssh root@<IP> 'systemctl restart nanobot'"
