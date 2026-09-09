#!/usr/bin/env bash
# Builds and starts the full stack (app + Caddy), then verifies it's
# actually reachable over the configured DOMAIN before declaring success.
# Safe to re-run -- this is also how you deploy updates after `git pull`.
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

if [ ! -f .env ]; then
  echo "[03] ERROR: .env not found -- run 01-configure-env.sh first." >&2
  exit 1
fi
if [ ! -f Caddyfile ]; then
  echo "[03] ERROR: Caddyfile not found -- run 02-configure-caddy.sh first." >&2
  exit 1
fi
if ! grep -qE '^INTERNAL_SHARED_SECRET=.+' .env; then
  echo "[03] ERROR: INTERNAL_SHARED_SECRET is not set in .env -- run 01-configure-env.sh." >&2
  exit 1
fi

DOMAIN="$(grep -E '^DOMAIN=' .env | tail -n1 | cut -d= -f2-)"
if [ -z "$DOMAIN" ]; then
  echo "[03] ERROR: DOMAIN is not set in .env -- run 01-configure-env.sh." >&2
  exit 1
fi

echo "[03] Building and starting confluence-pr-agent + caddy..."
sudo docker compose -f podman-compose.yml --profile public up -d --build

echo "[03] Waiting for the app to come up..."
for _ in $(seq 1 30); do
  if sudo docker compose -f podman-compose.yml exec -T confluence-pr-agent \
      python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" \
      >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "[03] Checking public reachability at ${DOMAIN}..."
HEALTHZ_URL="${DOMAIN%/}/healthz"
if curl -fsS --max-time 10 "$HEALTHZ_URL" >/dev/null 2>&1; then
  echo "[03] SUCCESS: ${HEALTHZ_URL} is reachable."
  echo "     Open ${DOMAIN%/}/ui/runs in a browser -- it should prompt for the"
  echo "     Basic Auth login configured in 02-configure-caddy.sh."
else
  echo "[03] WARNING: could not reach ${HEALTHZ_URL} from this box." >&2
  echo "     If DOMAIN is a real hostname, this is most likely DNS not yet" >&2
  echo "     pointing at this instance's public IP, or the security group" >&2
  echo "     not allowing inbound 80/443 -- Caddy needs 80 reachable to get" >&2
  echo "     its Let's Encrypt certificate. Check container logs:" >&2
  echo "       sudo docker compose -f podman-compose.yml logs caddy --tail 50" >&2
fi
