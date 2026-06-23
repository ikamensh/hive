#!/bin/bash
# Operate the remote hive VM (chief + runner). One place for the gcloud
# coordinates so ops are short and repeatable. Override with HIVE_VM* env vars.
#
#   deploy/vm.sh status              # chief + runner health
#   deploy/vm.sh logs [chief|runner] [N]     # journalctl tail (default: chief, 50)
#   deploy/vm.sh restart             # restart both services
#   deploy/vm.sh tunnel [port]       # forward localhost:PORT -> chief (bypasses Caddy auth)
#   deploy/vm.sh ssh [cmd...]        # ssh in, or run a one-off command
set -euo pipefail

VM=${HIVE_VM:-hive-vm}
ZONE=${HIVE_VM_ZONE:-europe-west1-b}
PROJECT=${HIVE_VM_PROJECT:-hive-ikamen}
ACCOUNT=${HIVE_VM_ACCOUNT:-ikamenshchikov@gmail.com}

gssh() { gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" --account="$ACCOUNT" "$@"; }

cmd=${1:-status}; shift || true
case "$cmd" in
  status)
    gssh --command 'echo "chief: $(systemctl is-active hive-chief) | runner: $(systemctl is-active hive-runner)"; curl -s -o /dev/null -w "health -> %{http_code}\n" localhost:8000' ;;
  logs)
    svc=${1:-chief}; n=${2:-50}; gssh --command "sudo journalctl -u hive-$svc --no-pager -n $n" ;;
  restart)
    gssh --command 'sudo systemctl restart hive-chief hive-runner && echo restarted' ;;
  tunnel)
    port=${1:-8000}; echo "-> http://localhost:$port (Ctrl-C to stop)"; gssh -- -L "$port:localhost:8000" -N ;;
  ssh)
    if [ "$#" -gt 0 ]; then gssh --command "$*"; else gssh; fi ;;
  *)
    echo "usage: deploy/vm.sh {status|logs|restart|tunnel|ssh}" >&2; exit 2 ;;
esac
