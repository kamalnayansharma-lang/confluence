#!/usr/bin/env bash
# Automates everything that CAN be automated once your accounts already exist:
# venv + deps, .env scaffolding, and sanity-checking the credentials you paste in.
# It does NOT create any accounts, tokens, or the Confluence webhook itself --
# see SETUP.md for exactly which steps are manual and why.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Creating virtualenv (.venv) if missing"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
pip install --upgrade pip -q
pip install -e ".[dev]" -q

echo "==> Scaffolding .env"
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "    Created .env from .env.example -- fill in the blank values before running the service."
else
  echo "    .env already exists, leaving it alone."
fi

echo "==> Checking required CLI tools"
for tool in git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "    MISSING: $tool -- required, please install it."
    exit 1
  fi
done
if command -v gh >/dev/null 2>&1; then
  echo "    gh CLI found ($(gh --version | head -n1))"
else
  echo "    gh CLI not found (optional -- only needed if you want to create the GitHub repo for THIS project via 'gh repo create')."
fi

echo "==> Checking for the configured change-engine CLI"
# Each engine (agent/engines/) shells out to its own CLI -- none of them are
# bundled with their Python/pip dependency. See docs/change-engines.md.
CHANGE_AGENT_ENGINE="$( { grep -E '^CHANGE_AGENT_ENGINE=' .env || true; } 2>/dev/null | tail -n1 | cut -d= -f2-)"
CHANGE_AGENT_ENGINE="${CHANGE_AGENT_ENGINE:-claude_code}"

case "$CHANGE_AGENT_ENGINE" in
  claude_code) ENGINE_BINARY=claude;  ENGINE_INSTALL="npm install -g @anthropic-ai/claude-code" ;;
  cursor)      ENGINE_BINARY=agent;   ENGINE_INSTALL="curl https://cursor.com/install -fsS | bash" ;;
  copilot)     ENGINE_BINARY=copilot; ENGINE_INSTALL="npm install -g @github/copilot" ;;
  codex)       ENGINE_BINARY=codex;   ENGINE_INSTALL="npm install -g @openai/codex" ;;
  gemini)      ENGINE_BINARY=gemini;  ENGINE_INSTALL="npm install -g @google/gemini-cli" ;;
  kiro)        ENGINE_BINARY=kiro-cli; ENGINE_INSTALL="curl -fsSL https://cli.kiro.dev/install | bash" ;;
  antigravity)
    ENGINE_BINARY=agy
    ENGINE_INSTALL=""
    echo "    antigravity is OAuth-only -- install per https://antigravity.google/docs/cli/overview,"
    echo "    then run 'agy login' interactively once (headless mode uses cached credentials)."
    ;;
  *) ENGINE_BINARY=""; echo "    Unknown CHANGE_AGENT_ENGINE '$CHANGE_AGENT_ENGINE' -- skipping CLI check." ;;
esac

if [ -n "$ENGINE_BINARY" ]; then
  if command -v "$ENGINE_BINARY" >/dev/null 2>&1; then
    echo "    $ENGINE_BINARY CLI found ($(command -v "$ENGINE_BINARY"))"
  elif [ -z "$ENGINE_INSTALL" ]; then
    echo "    MISSING: $ENGINE_BINARY CLI for engine '$CHANGE_AGENT_ENGINE' (see install note above)."
  elif [[ "$ENGINE_INSTALL" == npm* ]] && ! command -v npm >/dev/null 2>&1; then
    echo "    MISSING: $ENGINE_BINARY CLI for engine '$CHANGE_AGENT_ENGINE', and npm is not available."
    echo "    Install Node.js, then run: $ENGINE_INSTALL"
  else
    echo "    $ENGINE_BINARY CLI not found -- installing via: $ENGINE_INSTALL"
    eval "$ENGINE_INSTALL"
  fi
fi

echo "==> Validating credentials in .env (best-effort, does not fail the script)"
python3 scripts/check_credentials.py || true

cat <<'EOF'

==> Bootstrap complete.

Still manual (see SETUP.md for details):
  1. Create/confirm the Confluence Cloud API token.
  2. Create/confirm the GitHub PAT with access to your target repo.
  3. Create/confirm the SendGrid API key + verified sender.
  4. Register the Confluence webhook (Space Settings -> Webhooks) pointing at
     this service's /webhook/confluence endpoint -- Confluence Cloud has no
     supported public API for this, so it must be done in the UI.

To run the service locally once .env is filled in:
  source .venv/bin/activate
  uvicorn confluence_pr_agent.webhook.app:app --reload --port 8000

EOF
