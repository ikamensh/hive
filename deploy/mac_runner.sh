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

# The menu bar toggle switched this runner off. Exit before any network work;
# the plist's KeepAlive is conditioned on this file, so launchd leaves us down
# until the flag is removed (which starts us again).
PAUSE_FILE="${HIVE_RUNNER_STATE_DIR:-$HOME/.config/hive}/runner.paused"
if [ -f "$PAUSE_FILE" ]; then
  echo "runner paused ($PAUSE_FILE exists) — not starting"
  exit 0
fi

if [ "${HIVE_RUNNER_SELF_UPDATE:-}" = "1" ] && [ -d "$REPO/.git" ]; then
  # Best-effort: an offline start still runs the code we already have.
  if git -C "$REPO" fetch --quiet origin main 2>/dev/null; then
    git -C "$REPO" reset --hard --quiet FETCH_HEAD
    uv sync --directory "$REPO" --frozen --no-dev --extra mac || true
  fi
fi

cd "$REPO"
exec uv run python -m hive.runner
