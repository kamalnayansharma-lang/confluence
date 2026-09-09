#!/usr/bin/env bash
# Single entrypoint: installs Docker if needed, configures .env + Caddyfile
# (prompting for whatever isn't already set), builds, and starts the full
# stack -- everything scripts/ec2/00-03 do, in order. This is the ONE
# script meant to be run directly after checking out the repo on a fresh
# EC2 instance.
#
#   git clone <this-repo> && cd confluence-to-pr-agent
#   ./scripts/ec2/deploy.sh
#
# Safe to re-run for updates: `git pull && ./scripts/ec2/deploy.sh` skips
# every step that's already done and just rebuilds+restarts the containers.
#
# To add another person's login afterward (no need to re-run this script):
#   ./scripts/add-caddy-user.sh <username>
set -euo pipefail
cd "$(dirname "$0")"

./00-install-docker.sh
./01-configure-env.sh
./02-configure-caddy.sh
./03-deploy.sh

cat <<'EOF'

==> Deploy complete.

Still worth doing, if you haven't already:
  - Fill in real Confluence/GitHub/Jira/email credentials for the default
    user, either through /ui/config in the browser, or by editing
    data/users/<DEFAULT_USER>/.env directly (see .env's DEFAULT_USER value).
  - Confirm DNS for DOMAIN (in .env) actually points at this box's public
    IP -- Caddy needs that to get a real HTTPS certificate.
  - Add teammates: ./scripts/add-caddy-user.sh <username>
EOF
