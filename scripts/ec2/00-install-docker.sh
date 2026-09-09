#!/usr/bin/env bash
# Installs Docker + the Compose plugin if not already present. Safe to
# re-run (e.g. after `git pull`) -- exits early if docker already works.
#
# Every docker/compose call across these scripts uses `sudo` deliberately:
# a freshly `usermod -aG docker`'d user only gets that group membership in
# a NEW shell session, and this whole deploy is meant to run start-to-finish
# in one script invocation without requiring a re-login partway through.
set -euo pipefail

if sudo docker version >/dev/null 2>&1 && sudo docker compose version >/dev/null 2>&1; then
  echo "[00] Docker + compose plugin already installed -- skipping."
  exit 0
fi

echo "[00] Installing Docker (engine + compose plugin)..."
curl -fsSL https://get.docker.com | sudo sh

echo "[00] Adding $(whoami) to the docker group (takes effect next login -- these"
echo "     scripts use 'sudo docker' throughout, so this run doesn't depend on it)."
sudo usermod -aG docker "$(whoami)" || true

sudo docker version
sudo docker compose version
echo "[00] Docker installed."
