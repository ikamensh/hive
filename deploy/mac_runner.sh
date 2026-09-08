#!/bin/bash
# Wrapper launchd invokes to run the Hive runner as a managed service.
# Sources the once-materialized credentials, then execs the daemon in the repo.
#
# When HIVE_RUNNER_SELF_UPDATE=1 (set by install_mac_runner.sh for dedicated
# service clones — never for dev checkouts), every start first fast-forwards
# to origin/main when possible, preserving unpublished local upgrades. The
# daemon exits between tasks when origin/main moves ahead; launchd's KeepAlive
# respawns it. Crash recovery and code updates use the same path.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${HIVE_RUNNER_ENV:-$HOME/.config/hive/runner.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# Selection survives login/restart, even if someone clears the pause flag.
SELECTION_FILE="$(dirname "${HIVE_CONFIG_FILE:-$HOME/.config/hive/config.env}")/selected-chief"
if [ -f "$SELECTION_FILE" ] && [ "$(cat "$SELECTION_FILE")" = "local" ]; then
  echo "local chief selected — remote runner stays disconnected"
  exit 0
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
  # Offline or divergent history keeps the installed code running. An older
  # origin/main is already up to date from this checkout's perspective.
  if git -C "$REPO" fetch --quiet origin main 2>/dev/null \
      && git -C "$REPO" merge --ff-only --quiet FETCH_HEAD; then
    uv sync --directory "$REPO" --frozen --no-dev --extra mac || true
  fi
fi

cd "$REPO"
exec uv run python -m hive.runner
