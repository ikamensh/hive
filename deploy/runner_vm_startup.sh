#!/bin/bash
# Runner-only VM provisioner: turns a fresh Linux VM into a hive runner that
# long-polls a remote chief, with optional environment packs (android, ...).
# The runner-VM counterpart of vm_startup.sh (which provisions chief+runner).
# Idempotent — re-run by deploy/create_runner_vm.sh and by
# hive-startup.service on every boot. Logs: /var/log/hive-startup.log
#
# Needs two files placed once by deploy/create_runner_vm.sh:
#   /etc/hive/scw.env        SCW_SECRET_KEY, SCW_PROJECT_ID, SCW_REGION
#   /etc/hive/runner-vm.env  HIVE_CHIEF_URL, HIVE_RUNNER_NAME,
#                            HIVE_PACKS (space-separated, may be empty),
#                            HIVE_RUNNER_BACKENDS (optional csv filter)
set -euxo pipefail
exec >>/var/log/hive-startup.log 2>&1
echo "=== hive runner-vm startup $(date -Is) ==="
export HOME=/root

source /etc/hive/scw.env
source /etc/hive/runner-vm.env

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl ca-certificates gnupg docker.io rsync unzip

# --- swap: gradle + agent CLI peaks ---
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon -a

# --- gh CLI ---
if ! command -v gh >/dev/null; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update && apt-get install -y gh
fi

# --- node 22 + agent CLIs ---
if ! command -v node >/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
npm install -g @openai/codex @google/gemini-cli @anthropic-ai/claude-code || true
curl -fsS https://cursor.com/install | bash || true
ln -sf /root/.local/bin/cursor-agent /usr/local/bin/cursor-agent 2>/dev/null || true

# --- uv ---
if ! command -v /usr/local/bin/uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi

# --- secrets (Scaleway Secret Manager) -> /etc/hive/env ---
SM="https://api.scaleway.com/secret-manager/v1beta1/regions/${SCW_REGION}/secrets-by-path/versions/latest/access?project_id=${SCW_PROJECT_ID}&secret_path=/"
secret() {
  curl -fsS -H "X-Auth-Token: ${SCW_SECRET_KEY}" "${SM}&secret_name=$1" \
    | python3 -c 'import json,sys,base64; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)["data"]))'
}
secret_optional() { secret "$1" 2>/dev/null || true; }
mkdir -p /etc/hive

cat > /etc/hive/env <<EOF
HIVE_URL=${HIVE_CHIEF_URL}
HIVE_BASIC_AUTH=ilya:$(secret hive-web-password)
HIVE_RUNNER_TOKEN=$(secret hive-runner-token)
HIVE_RUNNER_NAME=${HIVE_RUNNER_NAME}
HIVE_RUNNER_BACKENDS=${HIVE_RUNNER_BACKENDS:-}
GEMINI_API_KEY=$(secret_optional hive-gemini-api-key)
OPENAI_API_KEY=$(secret_optional hive-openai-api-key)
CLAUDE_CODE_OAUTH_TOKEN=$(secret_optional hive-claude-oauth-token)
CURSOR_API_KEY=$(secret_optional hive-cursor-api-key)
HIVE_GH_TOKEN=$(secret hive-gh-token)
GH_TOKEN=$(secret hive-gh-token)
EOF
chmod 600 /etc/hive/env

# --- repo checkout (the runner runs from the tracked ref) ---
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

# --- agent CLI auth/config ---
mkdir -p /root/.gemini
cat > /root/.gemini/settings.json <<'EOF'
{"security": {"folderTrust": {"enabled": false}}}
EOF
codex login status || printf '%s' "$OPENAI_API_KEY" | codex login --with-api-key || true

# --- python env ---
/usr/local/bin/uv sync --directory /opt/hive --frozen --no-dev

# --- environment packs ---
for pack in ${HIVE_PACKS:-}; do
  bash "/opt/hive/deploy/install_${pack}_env.sh"
done
if [[ " ${HIVE_PACKS:-} " == *" android "* ]]; then
  grep -q '^ANDROID_HOME=' /etc/hive/env || cat >> /etc/hive/env <<'EOF'
ANDROID_HOME=/opt/android-sdk
PATH=/opt/android-sdk/platform-tools:/opt/android-sdk/cmdline-tools/latest/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EOF
fi

# --- runner (bare, systemd) ---
cat > /etc/systemd/system/hive-runner.service <<EOF
[Unit]
Description=hive runner
After=network-online.target docker.service

[Service]
EnvironmentFile=/etc/hive/env
Environment=HIVE_RUNNER_WORKDIR=/var/lib/hive-work
Environment=HOME=/root
# Dedicated, disposable agent box (rebuilt from this script): assert the
# sandbox signal so Claude Code accepts --dangerously-skip-permissions as root.
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

echo "=== hive runner-vm startup done $(date -Is) ==="
