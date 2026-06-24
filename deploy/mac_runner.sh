#!/bin/bash
# Wrapper launchd invokes to run the Hive runner as a managed service.
# Sources the once-materialized credentials, then execs the daemon in the repo.
# Kept tiny on purpose: all the resilience (reconnect, re-register, restart) lives
# in the daemon loop and in launchd's KeepAlive — this only wires env + cwd.
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
exec uv run python -m hive.runner
