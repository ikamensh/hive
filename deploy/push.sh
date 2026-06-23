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
ZONE=${HIVE_VM_ZONE:-europe-west1-b}
PROJECT=${HIVE_VM_PROJECT:-hive-ikamen}
ACCOUNT=${HIVE_VM_ACCOUNT:-ikamenshchikov@gmail.com}
cd "$(cd "$(dirname "$0")/.." && pwd)"

IP=$(gcloud compute instances describe "$VM" --zone="$ZONE" --project="$PROJECT" \
  --account="$ACCOUNT" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
SSH="ssh -i $HOME/.ssh/google_compute_engine -o IdentitiesOnly=yes \
  -o UserKnownHostsFile=$HOME/.ssh/google_compute_known_hosts -o StrictHostKeyChecking=accept-new"
REMOTE="ikamen@$IP"

# --rsync-path=sudo rsync: /opt/hive is root-owned. Excluded paths are also
# protected from --delete, so the VM's .venv / web/dist / .git survive.
echo "-> rsync sources to $VM ($IP):/opt/hive"
rsync -az --delete --rsync-path="sudo rsync" -e "$SSH" \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude 'web/dist' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '.idea' \
  ./ "$REMOTE:/opt/hive/"

if [[ " $* " == *" --deps "* ]]; then
  echo "-> uv sync (deps changed)"
  $SSH "$REMOTE" "cd /opt/hive && sudo /usr/local/bin/uv sync --frozen --no-dev"
fi

if [[ " $* " == *" --web "* ]]; then
  echo "-> build + ship web/dist"
  (cd web && npm run build)
  rsync -az --delete --rsync-path="sudo rsync" -e "$SSH" web/dist/ "$REMOTE:/opt/hive/web/dist/"
fi

echo "-> restart chief + runner"
$SSH "$REMOTE" "sudo systemctl restart hive-chief hive-runner"
echo "OK: deployed in-place. Chief: http://$IP:8000 (behind Caddy basic-auth)"
