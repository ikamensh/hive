#!/bin/bash
# Laptop runner: connects to the public hive endpoint and serves
# subscription-bound backends (claude, cursor) plus whatever else is installed.
set -euo pipefail
PROJECT=hive-ikamen

TOKEN=$(gcloud secrets versions access latest --secret=hive-runner-token \
  --project=$PROJECT --account=ikamenshchikov@gmail.com)
WEB_PASS=$(gcloud secrets versions access latest --secret=hive-web-password \
  --project=$PROJECT --account=ikamenshchikov@gmail.com)

cd "$(dirname "$0")/.."
HIVE_URL=https://hive.34-62-218-54.sslip.io \
HIVE_BASIC_AUTH="ilya:$WEB_PASS" \
HIVE_RUNNER_TOKEN="$TOKEN" \
HIVE_RUNNER_NAME="laptop-$(hostname -s)" \
uv run python -m hive.runner
