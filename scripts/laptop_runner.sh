#!/bin/bash
# Laptop runner: connects to the hive VM through an SSH tunnel and serves
# subscription-bound backends (claude, cursor) plus whatever else is installed.
set -euo pipefail
PROJECT=hive-ikamen
ZONE=europe-west1-b

TOKEN=$(gcloud secrets versions access latest --secret=hive-runner-token \
  --project=$PROJECT --account=ikamenshchikov@gmail.com)

# Tunnel: localhost:18000 -> VM:8000 (background, dies with this script)
gcloud compute ssh hive-vm --zone=$ZONE --project=$PROJECT \
  --account=ikamenshchikov@gmail.com -- -N -L 18000:localhost:8000 &
TUNNEL_PID=$!
trap "kill $TUNNEL_PID 2>/dev/null" EXIT
sleep 5

cd "$(dirname "$0")/.."
HIVE_URL=http://localhost:18000 \
HIVE_RUNNER_TOKEN="$TOKEN" \
HIVE_RUNNER_NAME="laptop-$(hostname -s)" \
uv run python -m hive.runner
