#!/bin/bash
# Fast in-place deploy of the local working tree to the hive VM — no docker, no
# git push. Rsyncs sources and restarts the bare systemd services (~seconds),
# for the tight edit -> test loop. (On reboot the source of truth is still git
# via deploy/vm_startup.sh; this is for between-reboot iteration.)
#
#   deploy/push.sh          # ship sources + restart chief & runner
#   deploy/push.sh --deps   # also `uv sync` (after a pyproject/uv.lock change)
#   deploy/push.sh --web    # also rebuild web/dist locally and ship it
set -euo pipefail

VM=${HIVE_VM:-hive-vm}
ZONE=${HIVE_VM_ZONE:-fr-par-1}
cd "$(cd "$(dirname "$0")/.." && pwd)"

IP=$(scw instance server list zone=$ZONE name=$VM -o json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["public_ip"]["address"])')
SSH="ssh -o StrictHostKeyChecking=accept-new"
REMOTE="root@$IP"
VERSION=$(uv run python -m hive.version)

# Excluded paths are also protected from --delete, so the VM's .venv /
# web/dist / .git survive.
echo "-> rsync sources to $VM ($IP):/opt/hive (version $VERSION)"
rsync -az --delete -e "$SSH" \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude 'web/dist' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '.idea' \
  ./ "$REMOTE:/opt/hive/"

if [[ " $* " == *" --deps "* ]]; then
  echo "-> uv sync (deps changed)"
  $SSH "$REMOTE" "cd /opt/hive && /usr/local/bin/uv sync --frozen --no-dev"
fi

echo "-> stamp version fallback ($VERSION)"
$SSH "$REMOTE" "cd /opt/hive && HIVE_VERSION='$VERSION' /opt/hive/.venv/bin/python -m hive.version --write-fallback >/dev/null"

if [[ " $* " == *" --web "* ]]; then
  echo "-> build + ship web/dist"
  (cd web && npm run build)
  rsync -az --delete -e "$SSH" web/dist/ "$REMOTE:/opt/hive/web/dist/"
fi

echo "-> restart chief + runner"
$SSH "$REMOTE" "systemctl restart hive-chief hive-runner"
echo "OK: deployed in-place. Chief: http://$IP:8000 (behind Caddy basic-auth)"
