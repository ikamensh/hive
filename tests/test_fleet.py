"""hive.fleet properties.

Liveness must behave like an ordered scale of silence — more silence never
makes a machine *more* alive — and identity must be a pure function of
(scope, name). Both properties are what the chief's dispatch gating and
re-registration converge on, so they are tested directly against the public
package surface rather than through the supervisor.
"""

import time

from hive.fleet import DEFAULT_LIVENESS, Liveness, LivenessPolicy, machine_metadata, stable_machine_id

ORDER = [Liveness.online, Liveness.quiet, Liveness.dark, Liveness.retired]


def test_liveness_monotonic_in_silence():
    """Sweeping silence from 0 to beyond retirement only ever moves the verdict
    forward through online -> quiet -> dark -> retired, for every device kind."""
    now = time.time()
    for kind in ("laptop", "server", "unknown", ""):
        seen = []
        for silence in range(0, int(9 * 24 * 3600), 600):
            verdict = DEFAULT_LIVENESS.assess(now - silence, kind, now=now)
            seen.append(ORDER.index(verdict))
        assert seen == sorted(seen)
        assert seen[0] == 0 and seen[-1] == ORDER.index(Liveness.retired)


def test_liveness_thresholds_are_the_boundaries():
    """The verdict flips exactly at the policy's own numbers, so any code that
    displays thresholds (`dark_after`) agrees with code that applies them."""
    policy = DEFAULT_LIVENESS
    now = 1_000_000_000.0
    for kind in ("laptop", "server", "unknown"):
        dark = policy.dark_after(kind)
        assert policy.assess(now - dark + 1, kind, now=now) is Liveness.quiet
        assert policy.assess(now - dark, kind, now=now) is Liveness.dark
    assert policy.assess(now - policy.retired_after_s, now=now) is Liveness.retired
    assert policy.assess(now - policy.online_window_s + 1, now=now) is Liveness.online


def test_custom_policy_overrides_class_thresholds():
    strict = LivenessPolicy(dark_after_s={}, dark_default_s=60.0, online_window_s=10.0)
    now = 1_000_000_000.0
    assert strict.assess(now - 30, "laptop", now=now) is Liveness.quiet
    assert strict.assess(now - 61, "laptop", now=now) is Liveness.dark


def test_stable_machine_id_is_deterministic_and_scoped():
    """Same (scope, name) -> same id across calls; different scope or name ->
    different id. This is what lets a rebooted runner reclaim its machine row."""
    a = stable_machine_id("raven", "ws1")
    assert a == stable_machine_id("raven", "ws1")
    assert a != stable_machine_id("raven", "ws2")
    assert a != stable_machine_id("crow", "ws1")
    assert a.startswith("machine-")


def test_machine_metadata_env_overrides_win():
    """Operators can pin any identity fact; detection only fills the gaps."""
    env = {"HIVE_MACHINE_OS": "beos", "HIVE_MACHINE_KIND": "server"}
    meta = machine_metadata(env)
    assert meta["machine_os"] == "beos"
    assert meta["machine_kind"] == "server"
    assert meta["machine_arch"]  # detected, still present
