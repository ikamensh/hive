#!/bin/bash
# Operate the remote hive VM (chief + runner). One place for the Scaleway
# coordinates so ops are short and repeatable. Override with HIVE_VM* env vars.
#
#   deploy/vm.sh status              # chief + runner health
#   deploy/vm.sh logs [chief|runner] [N]     # journalctl tail (default: chief, 50)
#   deploy/vm.sh restart             # restart both services
#   deploy/vm.sh tunnel [port]       # forward localhost:PORT -> chief (bypasses Caddy auth)
#   deploy/vm.sh ssh [cmd...]        # ssh in, or run a one-off command
set -euo pipefail

VM=${HIVE_VM:-hive-vm}
ZONE=${HIVE_VM_ZONE:-fr-par-1}

IP=$(scw instance server list zone=$ZONE name=$VM -o json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["public_ip"]["address"])')
vssh() { ssh -o StrictHostKeyChecking=accept-new "root@$IP" "$@"; }

cmd=${1:-status}; shift || true
case "$cmd" in
  status)
    vssh 'echo "chief: $(systemctl is-active hive-chief) | runner: $(systemctl is-active hive-runner)"; curl -s -o /dev/null -w "health -> %{http_code}\n" localhost:8000' ;;
  logs)
    svc=${1:-chief}; n=${2:-50}; vssh "journalctl -u hive-$svc --no-pager -n $n" ;;
  restart)
    vssh 'systemctl restart hive-chief hive-runner && echo restarted' ;;
  tunnel)
    port=${1:-8000}; echo "-> http://localhost:$port (Ctrl-C to stop)"; vssh -L "$port:localhost:8000" -N ;;
  ssh)
    if [ "$#" -gt 0 ]; then vssh "$*"; else vssh; fi ;;
  *)
    echo "usage: deploy/vm.sh {status|logs|restart|tunnel|ssh}" >&2; exit 2 ;;
esac
