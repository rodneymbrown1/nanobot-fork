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
echo "Enter your credentials (press Enter to keep current value):"
echo ""

# Read current secret
CURRENT=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ARN" \
  --query SecretString \
  --output text 2>/dev/null || echo '{}')

# ── LLM provider ─────────────────────────────────────────────────────────────
read -rsp "Anthropic API key (sk-ant-...): " ANTHROPIC_KEY; echo
read -rsp "OpenAI API key (sk-..., optional): " OPENAI_KEY; echo
read -rsp "OpenRouter API key (sk-or-v1-..., optional): " OPENROUTER_KEY; echo

# ── Telegram ──────────────────────────────────────────────────────────────────
echo ""
read -rp "Telegram bot token (from @BotFather, optional): " TELEGRAM_TOKEN
read -rp "Telegram allowed user IDs (comma-separated, e.g. 123456,789012): " TELEGRAM_ALLOW_FROM

# ── Web search ────────────────────────────────────────────────────────────────
echo ""
read -rsp "Brave Search API key (optional): " BRAVE_KEY; echo

echo ""
echo "Building config JSON..."

# Convert comma-separated allow_from to JSON array
IFS=',' read -ra ALLOW_ARRAY <<< "${TELEGRAM_ALLOW_FROM:-}"
ALLOW_JSON="[]"
if [ ${#ALLOW_ARRAY[@]} -gt 0 ] && [ -n "${ALLOW_ARRAY[0]}" ]; then
  ALLOW_JSON=$(printf '%s\n' "${ALLOW_ARRAY[@]}" | \
    sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | \
    jq -R . | jq -s .)
fi

TELEGRAM_ENABLED="false"
if [ -n "${TELEGRAM_TOKEN:-}" ] && [ "$TELEGRAM_TOKEN" != "REPLACE_ME" ]; then
  TELEGRAM_ENABLED="true"
fi

CONFIG=$(jq -n \
  --arg anthropic_key "${ANTHROPIC_KEY:-REPLACE_ME}" \
  --arg openai_key "${OPENAI_KEY:-}" \
  --arg openrouter_key "${OPENROUTER_KEY:-}" \
  --arg telegram_token "${TELEGRAM_TOKEN:-REPLACE_ME}" \
  --argjson telegram_enabled "$TELEGRAM_ENABLED" \
  --argjson telegram_allow_from "$ALLOW_JSON" \
  --arg brave_key "${BRAVE_KEY:-}" \
  '{
    agents: {
      defaults: {
        model: "anthropic/claude-opus-4-5",
        maxTokens: 8192,
        temperature: 0.7,
        maxToolIterations: 20,
        memoryWindow: 50
      }
    },
    providers: {
      anthropic: { apiKey: $anthropic_key },
      openai: (if $openai_key != "" then { apiKey: $openai_key } else { apiKey: "" } end),
      openrouter: (if $openrouter_key != "" then { apiKey: $openrouter_key } else { apiKey: "" } end)
    },
    gateway: {
      host: "127.0.0.1",
      port: 18790
    },
    channels: {
      telegram: {
        enabled: $telegram_enabled,
        token: $telegram_token,
        allowFrom: $telegram_allow_from
      }
    },
    tools: {
      restrictToWorkspace: false,
      web: {
        search: { apiKey: $brave_key }
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
echo "  ssh ubuntu@<IP> 'sudo systemctl restart nanobot'"
