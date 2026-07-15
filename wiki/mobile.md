# Mobile targets: Android first, iOS as the second environment pack

How hive works on software that cannot run on a generic cloud VM — mobile
apps being the first case. Design decided 2026-07-15; nothing here is built
yet except the primitives it reuses.

## The general primitive: environment packs

Some projects need a machine *environment*, not just an agent CLI: an Android
SDK + emulator, Xcode + iOS simulator, someday CUDA or Windows. Hive already
handles two informal instances of this — `browser` and `docker` — end to end:

- the runner detects what its machine has (`runner/_daemon.py detect_capabilities`),
- registration reconciles it into per-(runner, backend) `Resource` records,
- probe tasks prove each capability actually works (`Resource.*_status`),
- the testability contract declares what a project needs (`### local` /
  `### docker` fidelity subsections),
- the testing workstream stamps `Task.required_capabilities`,
- the supervisor dispatches only to a `Resource` that `supports()` them, and
  escalates "Enable testing capabilities" when no capacity qualifies.

An **environment pack** makes that pattern official. One pack =

1. an idempotent install script under `deploy/`,
2. a detector line in `detect_capabilities`,
3. a probe recipe (prove the whole chain on the real machine),
4. a fidelity name the testability contract can declare,
5. env vars the runner injects into task processes that require it.

Machine affinity is already solved: the Claude-Max-on-the-laptop precedent
means dispatch only sends work where the capability probed usable. Android
rides the same rail.

## Why Android first

- **Fully headless and CLI-driven**: sdkmanager, avdmanager, gradle, adb,
  `emulator -no-window` — every step scriptable, agents need no GUI.
- **Runs on hardware we have**: Apple-silicon Macs run ARM64 system images
  near-native via Hypervisor.framework; Linux needs only `/dev/kvm`.
- **iOS constrains the machine pool**: macOS on Apple hardware (EULA), so
  scale-out means renting Macs. Same primitives, worse economics — do it
  second, as pack #2, once Android has shaken out the shared parts.

## Verification tiers — most work needs no device

- **Tier 0 (no device)**: gradle build, lint, JVM unit tests, Robolectric.
  Needs only the SDK + JDK. The bulk of sweep/confirm work lives here.
- **Tier 1 (emulator)**: instrumented tests, UI flows, screenshots.

Two ways to get Tier 1, cheapest first:

- **Gradle Managed Devices** for scripted tests: AGP boots and tears down its
  own emulator (`./gradlew pixel8Api35DebugAndroidTest`). Zero runner-side
  lifecycle code — the contract's `### android` section can be one line.
- **Runner device lease** for the agent's interactive loop (install, poke,
  screenshot, iterate): `hive/runner/devices.py` boots the golden AVD from
  its quickboot snapshot (`-no-window -no-audio`, seconds not minutes),
  exports `ANDROID_SERIAL` into the task env, restores the snapshot on
  release. Idle TTL (~10 min) keeps it warm across consecutive tasks; cost is
  ~3–4 GB RAM only while leased. Nothing runs 24/7.

The agent's eyes (the mobile analog of the screenshot rule):
`adb exec-out screencap -p > shot.png`, UI tree via
`adb shell uiautomator dump`; optionally Maestro for YAML-driven UI flows.
These recipes belong in the project's testability contract so agents never
improvise them.

## The android pack, concretely

1. `deploy/install_android_env.sh` — JDK 17, cmdline-tools, platform-tools,
   one pinned system image (ARM64 on macOS, x86_64 on Linux), accept
   licenses, create golden AVD `hive-android`, first boot, save quickboot
   snapshot. Idempotent, mirrors `install_mac_runner.sh` in spirit.
2. Detector: `android` when `ANDROID_HOME`, `adb`, `emulator`, and
   acceleration (HVF implicit on macOS; `/dev/kvm` on Linux) are present.
3. Probe: boot golden AVD headless from snapshot, `adb wait-for-device`,
   install + launch a hello-world APK, screencap non-empty, kill. Probe text
   records boot latency and image coordinates.
4. Contract: `### android` joins `### local` / `### docker` as a fidelity
   subsection; testing maps `android` / `mobile` story tags to the
   capability the same way `ui` maps to `browser`.
5. Runner injects `ANDROID_HOME` + `ANDROID_SERIAL` into tasks that require
   `android`, backed by the device lease.

Prerequisite refactor: `Resource` grew one hardcoded field-triple per
capability (`browser_status`, `docker_status`, …). Generalize to
`capability_status: dict[str, ...]` and rewrite `supports()` over it; delete
the per-capability fields (no compat, per project rules).

## Machines, by cost

- **Phase 1 — $0**: the Mac laptop runner gets the pack. Existing affinity
  dispatch sends mobile work there and nowhere else.
- **Phase 2 — scale-out**: emulators need KVM, so bare metal (Hetzner AX
  class, ~€40–55/mo, roughly 8–12 concurrent emulators). Scaleway *instances*
  (incl. hive-vm) do not expose nested virt — the chief stays out of the
  mobile business regardless. Containers (Google's emulator images) matter
  only here, for density on a shared box; they are not a phase-1 need.
- **Burst / complement**: Firebase Test Lab per-device-minute for final
  sweeps on real hardware. Not a substitute — the agent's iterate loop needs
  a local adb device.
- **Physical devices**: a phone on USB is just a lease with a fixed serial.

## iOS pack (later, same shape)

Detector: `xcodebuild` + `xcrun simctl list` on macOS. Probe: boot a
simulator, build + install a hello-world .app, `simctl io screenshot`.
`simctl` is the adb analog (boot/install/launch/screenshot/video/push);
the simulator is an ordinary process, no KVM question. Simulator builds need
no signing certs — signing enters only with physical devices, keep it out of
scope. Machine supply is the one real difference: laptop first, then rented
Apple silicon (Scaleway Mac mini ~€0.10–0.17/hr, 24h min) if demand appears.

## Phase 1 build list

1. Generalize `Resource` capability status to a dict; delete per-cap fields.
2. `android` detector + probe instructions.
3. `deploy/install_android_env.sh`.
4. Contract `### android` fidelity + story-tag mapping in testing.
5. Runner env injection with a minimal lease (start with one golden AVD per
   machine and serialize android tasks there; concurrency later).
6. Battle test: add a small Android app project to the portfolio with a real
   iteration goal and let the loop run, 2026-06-17-style.

## Risks

- **Memory pressure**: gradle daemon + emulator ≈ 6–8 GB per active task;
  cap concurrent android leases per machine in config.
- **Emulator flakiness**: boot hangs happen; lease and probe need hard
  timeouts with kill + snapshot-restore recovery, reported like agent-CLI
  probe failures.
- **Toolchain drift**: pin API level and image in the install script; bumping
  is a deliberate redeploy, never runtime magic.
