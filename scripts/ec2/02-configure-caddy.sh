#!/usr/bin/env bash
# Sets up Caddyfile from Caddyfile.example and replaces its placeholder
# login with a real one (bcrypt-hashed, never stored in plaintext). Safe to
# re-run: if Caddyfile already has a real hash in it (the placeholder is
# gone), this is a no-op -- use ../add-caddy-user.sh to add more logins
# afterward.
#
# Non-interactive use: export CADDY_ADMIN_USER / CADDY_ADMIN_PASSWORD
# before running this script to skip the prompts. (Not CADDY_USERNAME /
# CADDY_PASSWORD-style generic names -- USERNAME in particular can already
# be set in your shell environment on some systems, which would silently
# win over a same-named variable in surprising ways.)
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

if [ ! -f Caddyfile ]; then
  echo "[02] No Caddyfile found -- copying from Caddyfile.example."
  cp Caddyfile.example Caddyfile
fi

if ! grep -q "REPLACE_WITH_A_REAL_HASH_FROM_CADDY_HASH_PASSWORD" Caddyfile; then
  echo "[02] Caddyfile already has a real login configured -- skipping."
  echo "     Run scripts/add-caddy-user.sh to add another person."
  exit 0
fi

if [ -z "${CADDY_ADMIN_USER:-}" ]; then
  if [ -t 0 ]; then
    read -r -p "[02] Username for the first login: " CADDY_ADMIN_USER
  else
    echo "[02] ERROR: CADDY_ADMIN_USER is not set and this shell isn't interactive." >&2
    exit 1
  fi
fi
if [ -z "${CADDY_ADMIN_PASSWORD:-}" ]; then
  if [ -t 0 ]; then
    read -r -s -p "[02] Password for ${CADDY_ADMIN_USER}: " CADDY_ADMIN_PASSWORD
    echo
  else
    echo "[02] ERROR: CADDY_ADMIN_PASSWORD is not set and this shell isn't interactive." >&2
    exit 1
  fi
fi

CADDY_ADMIN_HASH="$(sudo docker run --rm docker.io/library/caddy:2 caddy hash-password --plaintext "$CADDY_ADMIN_PASSWORD")"
unset CADDY_ADMIN_PASSWORD

# python3, not sed: BSD sed (macOS) and GNU sed (the real Linux deploy
# target) handle multi-line edits differently enough to corrupt the file --
# confirmed the hard way during testing. python3 behaves identically on
# both and is already a hard dependency of this project.
export CADDY_ADMIN_USER CADDY_ADMIN_HASH
python3 <<'PYEOF'
import os

username = os.environ["CADDY_ADMIN_USER"]
password_hash = os.environ["CADDY_ADMIN_HASH"]
path = "Caddyfile"

text = open(path).read()
placeholder = "demo-user $2a$14$REPLACE_WITH_A_REAL_HASH_FROM_CADDY_HASH_PASSWORD"
if placeholder not in text:
    raise SystemExit("ERROR: placeholder line not found in Caddyfile (unexpected)")

text = text.replace(placeholder, f"{username} {password_hash}", 1)
open(path, "w").write(text)
print(f"[02] Configured login for '{username}'.")
PYEOF
unset CADDY_ADMIN_HASH

echo "[02] Reminder: DEFAULT_USER in .env should usually match '${CADDY_ADMIN_USER}'"
echo "     (see 01-configure-env.sh) -- that's who /webhook/confluence resolves to."
