#!/bin/bash
# Wrapper launchd invokes to run the Hive runner as a managed service.
# Sources the once-materialized credentials, then execs the daemon in the repo.
#
# When HIVE_RUNNER_SELF_UPDATE=1 (set by install_mac_runner.sh for dedicated
# service clones — never for dev checkouts), every start first syncs the clone
# to origin/main. Combined with the daemon exiting between tasks when
# origin/main moves ahead, and launchd's KeepAlive respawning us, the fleet
# updates itself: crash recovery and code updates are the same path.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${HIVE_RUNNER_ENV:-$HOME/.config/hive/runner.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [ "${HIVE_RUNNER_SELF_UPDATE:-}" = "1" ] && [ -d "$REPO/.git" ]; then
  # Best-effort: an offline start still runs the code we already have.
  if git -C "$REPO" fetch --quiet origin main 2>/dev/null; then
    git -C "$REPO" reset --hard --quiet FETCH_HEAD
    uv sync --directory "$REPO" --frozen --no-dev || true
  fi
fi

cd "$REPO"
exec uv run python -m hive.runner
