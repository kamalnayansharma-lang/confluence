#!/usr/bin/env bash
# Sets up the top-level .env (process-level config: DATA_DIR, DOMAIN,
# INTERNAL_SHARED_SECRET, DEFAULT_USER -- see config.py's module docstring
# for what these mean). Never overwrites an existing .env's values -- safe
# to re-run.
#
# Non-interactive use: export DOMAIN / DEFAULT_USER before running this
# script to skip the prompts (useful for scripted/repeatable deploys).
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

if [ ! -f .env ]; then
  echo "[01] No .env found -- copying from .env.example."
  cp .env.example .env
else
  echo "[01] .env already exists -- leaving your values alone."
fi

# --- INTERNAL_SHARED_SECRET -------------------------------------------------
# Required before the public (caddy) profile will even start (see
# podman-compose.yml) -- proves a request claiming an X-Auth-User identity
# actually came through Caddy. Generated once, never touched again.
if ! grep -qE '^INTERNAL_SHARED_SECRET=.+' .env 2>/dev/null; then
  SECRET="$(openssl rand -hex 32)"
  if grep -qE '^INTERNAL_SHARED_SECRET=' .env; then
    sed -i.bak "s|^INTERNAL_SHARED_SECRET=.*|INTERNAL_SHARED_SECRET=${SECRET}|" .env && rm -f .env.bak
  else
    printf '\nINTERNAL_SHARED_SECRET=%s\n' "$SECRET" >> .env
  fi
  echo "[01] Generated a random INTERNAL_SHARED_SECRET."
else
  echo "[01] INTERNAL_SHARED_SECRET already set -- leaving it alone."
fi

# --- DOMAIN ------------------------------------------------------------------
# The real public domain/subdomain this instance answers on -- Caddy uses
# this to request a Let's Encrypt cert automatically. Must already resolve
# (via DNS) to this box's public IP before 03-deploy.sh runs, or the
# certificate request will fail.
if ! grep -qE '^DOMAIN=.+' .env 2>/dev/null; then
  if [ -z "${DOMAIN:-}" ]; then
    if [ -t 0 ]; then
      read -r -p "[01] Public domain for this deployment (e.g. confluence-pr-agent.yourcompany.com): " DOMAIN
    else
      echo "[01] ERROR: DOMAIN is not set and this shell isn't interactive." >&2
      echo "     Either export DOMAIN=... before running this script, or run it interactively." >&2
      exit 1
    fi
  fi
  if grep -qE '^DOMAIN=' .env; then
    sed -i.bak "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" .env && rm -f .env.bak
  else
    printf '\nDOMAIN=%s\n' "$DOMAIN" >> .env
  fi
  echo "[01] Set DOMAIN=${DOMAIN}"
else
  echo "[01] DOMAIN already set -- leaving it alone."
fi

# --- DEFAULT_USER --------------------------------------------------------
# Which per-user config /webhook/confluence resolves to (it has no
# Caddy-authenticated identity attached to it) -- see config.py. Should
# match whichever username you're about to configure as the primary login
# in 02-configure-caddy.sh.
if ! grep -qE '^DEFAULT_USER=.+' .env 2>/dev/null; then
  if [ -z "${DEFAULT_USER:-}" ]; then
    if [ -t 0 ]; then
      read -r -p "[01] Primary/default username for this deployment: " DEFAULT_USER
    else
      DEFAULT_USER="admin"
      echo "[01] Non-interactive and DEFAULT_USER not set -- defaulting to 'admin'."
    fi
  fi
  if grep -qE '^DEFAULT_USER=' .env; then
    sed -i.bak "s|^DEFAULT_USER=.*|DEFAULT_USER=${DEFAULT_USER}|" .env && rm -f .env.bak
  else
    printf '\nDEFAULT_USER=%s\n' "$DEFAULT_USER" >> .env
  fi
  echo "[01] Set DEFAULT_USER=${DEFAULT_USER}"
else
  echo "[01] DEFAULT_USER already set -- leaving it alone."
fi

echo "[01] .env configured. Real Confluence/GitHub/Jira/email credentials still"
echo "     need to be filled in for DEFAULT_USER's own config -- either edit"
echo "     data/users/\$DEFAULT_USER/.env directly after first startup, or (easier)"
echo "     finish it through /ui/config in the browser once the app is up."
