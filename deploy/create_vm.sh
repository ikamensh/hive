#!/bin/bash
# Run locally: creates or refreshes the hive VM (chief + runner) on Scaleway.
# Re-runnable: re-uploads the provisioner and re-runs it on the existing VM.
# Needs the scw CLI configured with the Hive key (~/.config/scw/config.yaml).
set -euo pipefail
VM=${HIVE_VM:-hive-vm}
ZONE=${HIVE_VM_ZONE:-fr-par-1}
REGION=${HIVE_VM_REGION:-fr-par}
TYPE=${HIVE_VM_TYPE:-PLAY2-NANO}
cd "$(cd "$(dirname "$0")/.." && pwd)"

json() { python3 -c "import json,sys; print($1)"; }

ID=$(scw instance server list zone=$ZONE name=$VM -o json \
  | json 's[0]["id"] if (s:=json.load(sys.stdin)) else ""')
if [ -z "$ID" ]; then
  # PLAY2-NANO (2 vCPU / 4GB) fits the measured load (chief+runner idle ~1GB,
  # agent CLIs spike ~1GB mid-task; swap in vm_startup.sh is the backstop).
  scw instance server create type=$TYPE zone=$ZONE image=ubuntu_noble \
    root-volume=block:30GB name=$VM ip=new >/dev/null
  ID=$(scw instance server list zone=$ZONE name=$VM -o json | json 'json.load(sys.stdin)[0]["id"]')
fi
scw instance server start $ID zone=$ZONE --wait >/dev/null 2>&1 || true  # no-op if running
IP=$(scw instance server get $ID zone=$ZONE -o json | json 'json.load(sys.stdin)["public_ips"][0]["address"]')
SSH="ssh -o StrictHostKeyChecking=accept-new root@$IP"

# Scaleway credentials for boot-time secret fetches (vm_startup.sh).
$SSH "mkdir -p /etc/hive && umask 077 && cat > /etc/hive/scw.env" <<EOF
SCW_SECRET_KEY=$(scw config get secret-key)
SCW_PROJECT_ID=$(scw config get default-project-id)
SCW_REGION=$REGION
EOF

# The provisioner runs from an uploaded copy, not the git checkout — it git-resets
# /opt/hive while running, and bash must not have its script swapped underneath.
scp -o StrictHostKeyChecking=accept-new deploy/vm_startup.sh root@$IP:/usr/local/lib/hive-vm-startup.sh
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

echo "-> provisioning $VM ($IP); log: /var/log/hive-startup.log"
$SSH "systemctl daemon-reload && systemctl enable hive-startup && bash /usr/local/lib/hive-vm-startup.sh"
echo "VM ready. Web UI: https://hive.${IP//./-}.sslip.io  (tunnel: deploy/vm.sh tunnel)"
