#!/bin/bash
# GCE startup script: provisions the hive VM (control plane in docker + bare runner).
# Idempotent — safe to re-run on every boot. Logs: /var/log/hive-startup.log
set -euxo pipefail
exec >>/var/log/hive-startup.log 2>&1
echo "=== hive startup $(date -Is) ==="
export HOME=/root  # startup-script env has no HOME; git/gh need it

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl ca-certificates gnupg docker.io

# docker compose v2 plugin (no Debian package)
if ! docker compose version >/dev/null 2>&1; then
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

# --- gh CLI ---
if ! command -v gh >/dev/null; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update && apt-get install -y gh
fi

# --- node 22 + agent CLIs (codex, gemini) ---
if ! command -v node >/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
npm install -g @openai/codex @google/gemini-cli || true

# --- uv ---
if ! command -v /usr/local/bin/uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi

# --- secrets -> /etc/hive/env ---
secret() { gcloud secrets versions access latest --secret="$1"; }
mkdir -p /etc/hive
cat > /etc/hive/env <<EOF
HIVE_GCP_PROJECT=hive-ikamen
HIVE_GCS_BUCKET=hive-ikamen-blobs
HIVE_ORCH_MODEL=gemini-3-flash-preview
GEMINI_API_KEY=$(secret hive-gemini-api-key)
OPENAI_API_KEY=$(secret hive-openai-api-key)
HIVE_GH_TOKEN=$(secret hive-gh-token)
GH_TOKEN=$(secret hive-gh-token)
HIVE_RUNNER_TOKEN=$(secret hive-runner-token)
EOF
chmod 600 /etc/hive/env

# --- repo checkout ---
source /etc/hive/env
if [ -d /opt/hive/.git ]; then
  git -C /opt/hive fetch "https://x-access-token:${GH_TOKEN}@github.com/ikamensh/hive.git" main
  git -C /opt/hive reset --hard FETCH_HEAD
else
  git clone "https://x-access-token:${GH_TOKEN}@github.com/ikamensh/hive.git" /opt/hive
fi

# --- git identity + credentials for the runner's agents ---
git config --global user.name "hive-bot"
git config --global user.email "hive-bot@users.noreply.github.com"
git config --global credential.helper '!gh auth git-credential'

# --- control plane (docker compose) ---
cd /opt/hive
docker compose -f deploy/compose.yaml up -d --build

# --- runner (bare, systemd) ---
/usr/local/bin/uv sync --directory /opt/hive --frozen --no-dev
cat > /etc/systemd/system/hive-runner.service <<EOF
[Unit]
Description=hive runner
After=network-online.target docker.service

[Service]
EnvironmentFile=/etc/hive/env
Environment=HIVE_URL=http://localhost:8000
Environment=HIVE_RUNNER_NAME=hive-vm
Environment=HIVE_RUNNER_WORKDIR=/var/lib/hive-work
Environment=HOME=/root
ExecStart=/opt/hive/.venv/bin/python -m hive.runner
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now hive-runner
systemctl restart hive-runner

echo "=== hive startup done $(date -Is) ==="
