#!/bin/bash
# Run locally: creates or refreshes a runner-only VM on Scaleway and points it
# at an existing chief. This is the substrate contract: anyone with scw
# credentials and the hive secrets gets a fresh machine that self-registers
# and starts taking the fleet's work — with environment packs for software
# that can't build on a generic machine (android, ...).
#
#   bash deploy/create_runner_vm.sh hive-droid-1 --packs android
#   HIVE_RUNNER_VM_TYPE=PRO2-XS bash deploy/create_runner_vm.sh big-runner
#
# Re-runnable: re-uploads the provisioner and re-runs it on the existing VM.
set -euo pipefail

NAME=${1:?usage: create_runner_vm.sh <name> [--packs "android ..."] [--backends csv]}
shift
PACKS=""
BACKENDS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --packs) PACKS="$2"; shift 2 ;;
    --backends) BACKENDS="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

ZONE=${HIVE_RUNNER_VM_ZONE:-fr-par-1}
REGION=${HIVE_RUNNER_VM_REGION:-fr-par}
TYPE=${HIVE_RUNNER_VM_TYPE:-PLAY2-MICRO}  # 4 vCPU / 8GB: gradle+kotlin headroom
CHIEF_URL=${HIVE_CHIEF_URL:-https://hive.51-15-203-117.sslip.io}
cd "$(cd "$(dirname "$0")/.." && pwd)"

json() { python3 -c "import json,sys; print($1)"; }

ID=$(scw instance server list zone=$ZONE name=$NAME -o json \
  | json 's[0]["id"] if (s:=json.load(sys.stdin)) else ""')
if [ -z "$ID" ]; then
  scw instance server create type=$TYPE zone=$ZONE image=ubuntu_noble \
    root-volume=block:40GB name=$NAME ip=new >/dev/null
  ID=$(scw instance server list zone=$ZONE name=$NAME -o json | json 'json.load(sys.stdin)[0]["id"]')
fi
scw instance server start $ID zone=$ZONE --wait >/dev/null 2>&1 || true  # no-op if running
IP=$(scw instance server get $ID zone=$ZONE -o json | json 'json.load(sys.stdin)["public_ips"][0]["address"]')
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 root@$IP"

# A freshly created instance reports "running" before sshd answers.
for i in $(seq 1 24); do
  $SSH true 2>/dev/null && break
  [ "$i" = 24 ] && { echo "!! $NAME ($IP) not reachable over SSH" >&2; exit 1; }
  sleep 5
done

# Scaleway credentials for boot-time secret fetches.
$SSH "mkdir -p /etc/hive && umask 077 && cat > /etc/hive/scw.env" <<EOF
SCW_SECRET_KEY=$(scw config get secret-key)
SCW_PROJECT_ID=$(scw config get default-project-id)
SCW_REGION=$REGION
EOF

# The runner's own coordinates (stable name => stable machine id on the chief).
$SSH "umask 077 && cat > /etc/hive/runner-vm.env" <<EOF
HIVE_CHIEF_URL=$CHIEF_URL
HIVE_RUNNER_NAME=$NAME
HIVE_PACKS="$PACKS"
HIVE_RUNNER_BACKENDS=$BACKENDS
EOF

scp -o StrictHostKeyChecking=accept-new deploy/runner_vm_startup.sh \
  root@$IP:/usr/local/lib/hive-vm-startup.sh
$SSH "cat > /etc/systemd/system/hive-startup.service" <<'EOF'
[Unit]
Description=hive provision-on-boot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /usr/local/lib/hive-vm-startup.sh

[Install]
WantedBy=multi-user.target
EOF

echo "-> provisioning runner $NAME ($IP); log: /var/log/hive-startup.log"
$SSH "systemctl daemon-reload && systemctl enable hive-startup && bash /usr/local/lib/hive-vm-startup.sh"
echo "runner VM ready: $NAME ($IP) -> $CHIEF_URL  packs: ${PACKS:-none}"
