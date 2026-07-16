#!/bin/bash
# Android environment pack for a Linux runner machine — installs everything an
# agent needs to build and unit-test an Android app: JDK 17, a system Gradle
# (for greenfield `gradle init`; app repos bring their own wrapper), and the
# Android SDK (platform-tools/adb, one platform, build-tools) under
# ANDROID_ROOT below. Idempotent; safe to re-run on every boot.
#
# No emulator/system image: generic cloud VMs have no /dev/kvm, so the pack
# targets the device-free verification tier (gradle build, lint, JVM unit
# tests, Robolectric). The runner advertises the `android` capability when
# $ANDROID_HOME/platform-tools/adb answers `adb version` (see
# hive/runner/_daemon.py detect_capabilities) — export ANDROID_HOME for the
# runner process; this script also drops /etc/profile.d/android-sdk.sh for
# interactive shells.
set -euo pipefail

ANDROID_ROOT=/opt/android-sdk
GRADLE_VERSION=8.14
CMDLINE_TOOLS_BUILD=11076708  # pinned upstream zip; bump deliberately
PLATFORM=android-35
BUILD_TOOLS=35.0.0

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y openjdk-17-jdk-headless unzip curl ca-certificates

# --- system gradle (bootstrap only; wrappers self-provision after that) ---
if [ ! -x /opt/gradle/bin/gradle ]; then
  curl -fsSL "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
    -o /tmp/gradle.zip
  rm -rf /opt/gradle /opt/gradle-${GRADLE_VERSION}
  unzip -q /tmp/gradle.zip -d /opt
  mv /opt/gradle-${GRADLE_VERSION} /opt/gradle
  rm /tmp/gradle.zip
fi
ln -sf /opt/gradle/bin/gradle /usr/local/bin/gradle

# --- android cmdline-tools ---
SDKMANAGER="$ANDROID_ROOT/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDKMANAGER" ]; then
  curl -fsSL "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_BUILD}_latest.zip" \
    -o /tmp/cmdline-tools.zip
  mkdir -p "$ANDROID_ROOT/cmdline-tools"
  rm -rf "$ANDROID_ROOT/cmdline-tools/latest" /tmp/cmdline-tools
  unzip -q /tmp/cmdline-tools.zip -d /tmp/cmdline-tools
  mv /tmp/cmdline-tools/cmdline-tools "$ANDROID_ROOT/cmdline-tools/latest"
  rm -rf /tmp/cmdline-tools.zip /tmp/cmdline-tools
fi

# --- SDK packages (licenses accepted once, quietly) ---
export ANDROID_HOME="$ANDROID_ROOT"
yes | "$SDKMANAGER" --licenses >/dev/null || true
"$SDKMANAGER" --install "platform-tools" "platforms;${PLATFORM}" "build-tools;${BUILD_TOOLS}" >/dev/null

cat > /etc/profile.d/android-sdk.sh <<EOF
export ANDROID_HOME=$ANDROID_ROOT
export PATH=\$PATH:$ANDROID_ROOT/platform-tools:$ANDROID_ROOT/cmdline-tools/latest/bin
EOF

"$ANDROID_ROOT/platform-tools/adb" version
echo "android pack ready: ANDROID_HOME=$ANDROID_ROOT (export it for the hive runner)"
