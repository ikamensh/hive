#!/bin/bash
# Wrapper launchd invokes to run the Hive menu bar control (hive.runner.menubar).
# Sources the runner env (for HIVE_URL and any state-dir override) and execs
# the app in the service clone. No self-update here: the menu app is a thin
# shell over local files; re-run install_mac_runner.sh to refresh it.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${HIVE_RUNNER_ENV:-$HOME/.config/hive/runner.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

cd "$REPO"
exec uv run python -m hive.runner.menubar
