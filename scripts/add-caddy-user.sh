#!/usr/bin/env bash
# Adds one more Basic Auth login to Caddyfile and reloads Caddy with zero
# downtime (Caddy's admin API reload, not a container restart). Run this
# any time after the initial deploy to give a new teammate access -- they
# get their own isolated config/run-history automatically on first login
# (see config.py -- no separate "create account" step needed beyond this).
# Not EC2-specific -- works for any deployment of this app that uses the
# Caddyfile + podman-compose.yml public profile (see Caddyfile.example).
#
# Usage: scripts/add-caddy-user.sh <username> [password]
# If password is omitted and the shell is interactive, you'll be prompted
# (hidden input). Safe to re-run for the same username -- replaces their
# existing line rather than duplicating it.
#
# Uses python3 (not sed) to edit Caddyfile: sed's multi-line "a\" append
# syntax differs between GNU sed (Linux, the real deploy target) and BSD
# sed (macOS) badly enough to corrupt the file -- confirmed the hard way
# during testing. python3 behaves identically on both and is already a
# hard dependency of this project.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

# Detects which of docker/podman is actually USABLE (binary present AND its
# daemon reachable), and only reaches for sudo if neither works unprivileged
# -- neither the binary choice nor the sudo prefix is safe to hardcode.
# A fresh Linux user not yet in the `docker` group needs `sudo docker`; a
# local macOS dev machine typically has both CLIs on PATH but only one with
# a running daemon behind it (e.g. the `docker` binary left over from a
# Docker Desktop install that isn't currently running, while `podman
# machine` is what's actually up) -- picking the first binary found and
# assuming a failure means "needs sudo" gets this wrong in exactly that
# case: sudo doesn't fix an unreachable daemon, it just prompts for (and
# rejects) your login password for no reason, which is what happened here.
CONTAINER_CMD=()
for bin in docker podman; do
  if command -v "$bin" >/dev/null 2>&1 && "$bin" version >/dev/null 2>&1; then
    CONTAINER_CMD=("$bin")
    break
  fi
done
if [ ${#CONTAINER_CMD[@]} -eq 0 ]; then
  for bin in docker podman; do
    if command -v "$bin" >/dev/null 2>&1 && sudo "$bin" version >/dev/null 2>&1; then
      CONTAINER_CMD=(sudo "$bin")
      break
    fi
  done
fi
if [ ${#CONTAINER_CMD[@]} -eq 0 ]; then
  echo "ERROR: neither docker nor podman is usable (checked both with and without sudo)." >&2
  echo "       Make sure Docker Desktop / podman machine is actually running." >&2
  exit 1
fi
echo "[add-caddy-user] Using: ${CONTAINER_CMD[*]}"

CADDY_NEW_USER="${1:-}"
if [ -z "$CADDY_NEW_USER" ]; then
  echo "Usage: $0 <username> [password]" >&2
  exit 1
fi

CADDY_NEW_PASSWORD="${2:-}"
if [ -z "$CADDY_NEW_PASSWORD" ]; then
  if [ -t 0 ]; then
    read -r -s -p "Password for ${CADDY_NEW_USER}: " CADDY_NEW_PASSWORD
    echo
  else
    echo "ERROR: no password given and this shell isn't interactive." >&2
    exit 1
  fi
fi

if [ ! -f Caddyfile ]; then
  echo "ERROR: Caddyfile not found -- run scripts/ec2/02-configure-caddy.sh (or copy" >&2
  echo "       Caddyfile.example to Caddyfile yourself) first." >&2
  exit 1
fi

CADDY_NEW_HASH="$("${CONTAINER_CMD[@]}" run --rm docker.io/library/caddy:2 caddy hash-password --plaintext "$CADDY_NEW_PASSWORD")"
unset CADDY_NEW_PASSWORD

export CADDY_NEW_USER CADDY_NEW_HASH
python3 <<'PYEOF'
import os
import re

username = os.environ["CADDY_NEW_USER"]
password_hash = os.environ["CADDY_NEW_HASH"]
path = "Caddyfile"

text = open(path).read()
lines = text.splitlines(keepends=True)

# Match an existing "<username> <bcrypt-hash>" line for this exact username,
# regardless of leading whitespace -- replace in place if found.
existing_pattern = re.compile(rf"^(\s*){re.escape(username)}\s+\$2[ab]\$\S+\s*$")
for i, line in enumerate(lines):
    m = existing_pattern.match(line)
    if m:
        lines[i] = f"{m.group(1)}{username} {password_hash}\n"
        open(path, "w").writelines(lines)
        print(f"[add-caddy-user] '{username}' already had a login -- replaced it.")
        raise SystemExit(0)

# Otherwise insert a new line right after "basic_auth {", matching the
# indentation of the line that follows it (falls back to 3 tabs, this
# project's convention, if basic_auth's block is empty).
for i, line in enumerate(lines):
    if re.search(r"basic_auth\s*{", line):
        indent = "\t\t\t"
        if i + 1 < len(lines):
            m = re.match(r"^(\s*)\S", lines[i + 1])
            if m:
                indent = m.group(1)
        lines.insert(i + 1, f"{indent}{username} {password_hash}\n")
        open(path, "w").writelines(lines)
        print(f"[add-caddy-user] Added a new login for '{username}'.")
        raise SystemExit(0)

raise SystemExit("ERROR: could not find a 'basic_auth {' block in Caddyfile")
PYEOF
unset CADDY_NEW_HASH  # the hash is the only sensitive value here, not the username

echo "[add-caddy-user] Reloading Caddy (zero downtime)..."
if ! "${CONTAINER_CMD[@]}" compose -f podman-compose.yml exec caddy caddy reload --config /etc/caddy/Caddyfile; then
  # Most likely cause: the running container's bind-mounted Caddyfile went
  # stale -- any edit that replaces the file at that path (an editor doing
  # write-new-temp-file-then-rename instead of writing in place, e.g.) can
  # sever a single-file bind mount, confirmed the hard way earlier this
  # session for the analogous .env case. `caddy reload` can't recover from
  # that on its own since the container's view of the file never updates;
  # recreating the container gets it a fresh, correct mount instead.
  echo "[add-caddy-user] Reload failed -- most likely a stale bind mount, not a bad" >&2
  echo "                 Caddyfile. Recreating the caddy container instead..." >&2
  "${CONTAINER_CMD[@]}" compose -f podman-compose.yml --profile public up -d --force-recreate caddy
fi

echo "[add-caddy-user] Done. '${CADDY_NEW_USER}' can log in now -- their own config/run"
echo "                 history gets created automatically on first request."
