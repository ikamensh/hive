#!/bin/bash
# Install the Hive runner as a launchd service on a Mac — once. After this the
# chief can dispatch to this machine's subscription-bound backends (Claude Max,
# Cursor) whenever the user is logged in; it auto-starts on login, restarts on
# crash, and reconnects after sleep. Re-running is idempotent (reloads).
#
#   bash deploy/install_mac_runner.sh
#   HIVE_RUNNER_NAME=studio bash deploy/install_mac_runner.sh   # override the name
#
# A LaunchAgent (not a LaunchDaemon) on purpose: it runs in the user's GUI
# session so the agent CLIs can read the login Keychain where `claude login` /
# `cursor` store their credentials. macOS may prompt once to allow Keychain
# access — approve "Always Allow".
set -euo pipefail

LABEL="com.hive.runner"
MENUBAR_LABEL="com.hive.menubar"
SERVICE_REPO="${HIVE_SERVICE_REPO:-$HOME/.local/share/hive-runner}"
ENV_FILE="$HOME/.config/hive/runner.env"
PAUSE_FILE="$HOME/.config/hive/runner.paused"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
MENUBAR_PLIST="$HOME/Library/LaunchAgents/$MENUBAR_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/hive"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
GIT_REMOTE="github.com/ikamensh/hive.git"

# Defaults match the working laptop_runner.sh (sslip.io avoids any DNS dependency).
PROJECT="${HIVE_GCP_PROJECT:-hive-ikamen}"
ACCOUNT="${HIVE_GCLOUD_ACCOUNT:-ikamenshchikov@gmail.com}"
HIVE_URL="${HIVE_URL:-https://hive.34-62-218-54.sslip.io}"
RUNNER_NAME="${HIVE_RUNNER_NAME:-$(hostname -s)}"

echo "-> service repo: $SERVICE_REPO  (dedicated clone; self-updates from origin/main)"
echo "-> runner name:  $RUNNER_NAME  (stable name => stable machine id on the chief)"
echo "-> chief seed:   $HIVE_URL  (more candidates learned from the chief itself)"

# --- credentials, materialized once (no gcloud dependency at runtime) ---------
# Two sources: env vars (the `hive enroll` path — any member, no GCP access
# needed) or GCP Secret Manager (the admin path). Basic auth is optional; a
# chief without a Caddy perimeter has none.
mkdir -p "$(dirname "$ENV_FILE")" "$LOG_DIR"
if [ -n "${HIVE_RUNNER_TOKEN:-}" ] && [ -n "${HIVE_GH_TOKEN:-}" ]; then
  TOKEN="$HIVE_RUNNER_TOKEN"; BASIC_AUTH="${HIVE_BASIC_AUTH:-}"; GH_TOKEN="$HIVE_GH_TOKEN"
else
  echo "-> fetching runner + gh tokens and web password from GCP Secret Manager ($PROJECT)"
  TOKEN="$(gcloud secrets versions access latest --secret=hive-runner-token --project="$PROJECT" --account="$ACCOUNT")"
  WEB_PASS="$(gcloud secrets versions access latest --secret=hive-web-password --project="$PROJECT" --account="$ACCOUNT")"
  GH_TOKEN="$(gcloud secrets versions access latest --secret=hive-gh-token --project="$PROJECT" --account="$ACCOUNT")"
  BASIC_AUTH="ilya:$WEB_PASS"
fi

umask 177
cat > "$ENV_FILE" <<EOF
HIVE_URL=$HIVE_URL
HIVE_RUNNER_TOKEN=$TOKEN
HIVE_RUNNER_NAME=$RUNNER_NAME
HIVE_GH_TOKEN=$GH_TOKEN
HIVE_RUNNER_SELF_UPDATE=1
EOF
if [ -n "$BASIC_AUTH" ]; then
  echo "HIVE_BASIC_AUTH=$BASIC_AUTH" >> "$ENV_FILE"
fi
if [ -n "${HIVE_RUNNER_OWNER:-}" ]; then
  # The member this machine belongs to (set by `hive enroll`); the chief
  # claims the machine for them on first register.
  echo "HIVE_RUNNER_OWNER=$HIVE_RUNNER_OWNER" >> "$ENV_FILE"
fi
umask 022
echo "-> wrote $ENV_FILE (chmod 600)"

# --- dedicated service clone ---------------------------------------------------
# The service never runs from a dev checkout: a tree you rebase, move, or
# refactor takes the runner down with it (that outage happened). The clone's
# remote embeds the GH token so unattended fetch/reset works; the directory is
# user-private like the env file.
AUTH_REMOTE="https://x-access-token:${GH_TOKEN}@${GIT_REMOTE}"
if [ -d "$SERVICE_REPO/.git" ]; then
  git -C "$SERVICE_REPO" remote set-url origin "$AUTH_REMOTE"
  git -C "$SERVICE_REPO" fetch --quiet origin main
  git -C "$SERVICE_REPO" reset --hard --quiet FETCH_HEAD
else
  mkdir -p "$(dirname "$SERVICE_REPO")"
  git clone --quiet "$AUTH_REMOTE" "$SERVICE_REPO"
fi
chmod 700 "$SERVICE_REPO"
"$UV" sync --directory "$SERVICE_REPO" --frozen --no-dev --extra mac
echo "-> service clone at $(git -C "$SERVICE_REPO" rev-parse --short HEAD)"

# --- the LaunchAgent ----------------------------------------------------------
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SERVICE_REPO/deploy/mac_runner.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$SERVICE_REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$UV"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <!-- Respawn only while the pause flag is absent: the menu bar toggle writes
       $PAUSE_FILE, the daemon drains and exits, launchd leaves it down.
       Removing the flag makes launchd start it again. -->
  <key>KeepAlive</key>
  <dict>
    <key>PathState</key>
    <dict><key>$PAUSE_FILE</key><false/></dict>
  </dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/runner.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/runner.log</string>
</dict>
</plist>
EOF
echo "-> wrote $PLIST"

# --- the menu bar control (its own agent: pausing the runner must not kill the switch)
cat > "$MENUBAR_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$MENUBAR_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SERVICE_REPO/deploy/mac_menubar.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$SERVICE_REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$UV"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/menubar.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/menubar.log</string>
</dict>
</plist>
EOF
echo "-> wrote $MENUBAR_PLIST"

# --- (re)load -----------------------------------------------------------------
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl bootout "gui/$(id -u)/$MENUBAR_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$MENUBAR_PLIST"
launchctl enable "gui/$(id -u)/$MENUBAR_LABEL"
echo "-> loaded. logs: $LOG_DIR/runner.log ($LOG_DIR/menubar.log for the menu bar)"
echo "   the 🐝 in the menu bar pauses/resumes this runner"
echo "   status:    launchctl print gui/$(id -u)/$LABEL | grep -E 'state|pid'"
echo "   uninstall: launchctl bootout gui/$(id -u)/$LABEL && rm '$PLIST'"
echo "              launchctl bootout gui/$(id -u)/$MENUBAR_LABEL && rm '$MENUBAR_PLIST'"
