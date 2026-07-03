#!/bin/bash
# GCE startup script: provisions the hive VM (chief in docker + bare runner).
# Idempotent — safe to re-run on every boot. Logs: /var/log/hive-startup.log
set -euxo pipefail
exec >>/var/log/hive-startup.log 2>&1
echo "=== hive startup $(date -Is) ==="
export HOME=/root  # startup-script env has no HOME; git/gh need it

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl ca-certificates gnupg docker.io rsync

# --- swap: backstop for npm builds + agent CLI peaks on a 4GB machine ---
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon -a

# docker.io stays installed for the runner's `docker` capability, but the
# chief runs bare (systemd) below — no image build in the deploy loop.
# `deploy/Dockerfile` + `deploy/compose.yaml` remain for a future stability mode.

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
npm install -g @openai/codex @google/gemini-cli @anthropic-ai/claude-code || true
# cursor-agent (subscription/API-key backend) — official installer drops it in ~/.local/bin
curl -fsS https://cursor.com/install | bash || true
ln -sf /root/.local/bin/cursor-agent /usr/local/bin/cursor-agent 2>/dev/null || true

# --- uv ---
if ! command -v /usr/local/bin/uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi

# --- secrets -> /etc/hive/env ---
secret() { gcloud secrets versions access latest --secret="$1"; }
secret_optional() { gcloud secrets versions access latest --secret="$1" 2>/dev/null || true; }
mkdir -p /etc/hive
# CLAUDE_CODE_OAUTH_TOKEN / CURSOR_API_KEY are the headless tokens for the
# subscription backends (mint with `claude setup-token` / a Cursor dashboard API
# key, then `gcloud secrets create …`). Empty until those secrets exist, which
# just leaves the backend non-usable — the runner's probe gates it.
cat > /etc/hive/env <<EOF
HIVE_GCP_PROJECT=hive-ikamen
HIVE_GCS_BUCKET=hive-ikamen-blobs
HIVE_ORCH_PROVIDER=auto
GEMINI_API_KEY=$(secret_optional hive-gemini-api-key)
OPENAI_API_KEY=$(secret_optional hive-openai-api-key)
CLAUDE_CODE_OAUTH_TOKEN=$(secret_optional hive-claude-oauth-token)
CURSOR_API_KEY=$(secret_optional hive-cursor-api-key)
HIVE_GH_TOKEN=$(secret hive-gh-token)
GH_TOKEN=$(secret hive-gh-token)
HIVE_RUNNER_TOKEN=$(secret hive-runner-token)
HIVE_GITHUB_WEBHOOK_SECRET=$(secret_optional hive-github-webhook-secret)
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

# --- agent CLI auth/config for the runner ---
mkdir -p /root/.gemini
cat > /root/.gemini/settings.json <<'EOF'
{"security": {"folderTrust": {"enabled": false}}}
EOF
# API-key login only as a fallback — never overwrite an existing (subscription) login.
codex login status || printf '%s' "$OPENAI_API_KEY" | codex login --with-api-key || true
# claude and cursor-agent authenticate purely from the env tokens above
# (CLAUDE_CODE_OAUTH_TOKEN / CURSOR_API_KEY) — no interactive login step, and no
# copied desktop credential (those expire/rotate; see wiki note on backend auth).

# --- caddy: public HTTPS with basic auth (web UI + laptop runners) ---
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi
WEB_PASS=$(secret hive-web-password)
WEB_HASH=$(caddy hash-password --plaintext "$WEB_PASS")
cat > /etc/caddy/Caddyfile <<EOF
hive.tachyon-ai.eu, hive.34-62-218-54.sslip.io {
    # The CI webhook is its own bearer-authed endpoint (HIVE_GITHUB_WEBHOOK_SECRET),
    # so GitHub can reach it without the site's basic-auth password.
    @protected not path /api/ci/webhook
    basic_auth @protected {
        ilya $WEB_HASH
    }
    reverse_proxy localhost:8000
}
EOF
systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy

# --- python env (shared by the chief + runner) ---
/usr/local/bin/uv sync --directory /opt/hive --frozen --no-dev

# --- web UI bundle (served statically by the bare chief) ---
(cd /opt/hive/web && npm ci && npm run build)

# --- chief (bare, systemd) ---
cat > /etc/systemd/system/hive-chief.service <<EOF
[Unit]
Description=hive chief
After=network-online.target
[Service]
EnvironmentFile=/etc/hive/env
Environment=HOME=/root
Environment=HIVE_WEB_DIST=/opt/hive/web/dist
WorkingDirectory=/opt/hive
ExecStart=/opt/hive/.venv/bin/uvicorn --factory hive.api:production_app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now hive-chief
systemctl restart hive-chief

# --- runner (bare, systemd) ---
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
# The runner runs as root, but Claude Code refuses --dangerously-skip-permissions
# under root unless it believes it is sandboxed. This VM is a dedicated, disposable
# agent box (rebuilt from this script), so we assert the sandbox signal; the cleaner
# alternative is to run the runner as a non-root user. Other backends ignore it.
Environment=IS_SANDBOX=1
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
