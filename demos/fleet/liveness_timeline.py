"""Demo: fleet liveness verdicts over a silence timeline — `hive.fleet` standalone.

Task: you run a fleet dashboard and machines stop heartbeating. When is that
normal (a laptop in a backpack), when should a human be paged (a dark server),
and when should the row go quiet forever (a retired host)? One policy answers
for every machine class:

    uv run python demos/fleet/liveness_timeline.py

Fully offline and deterministic — time is injected, never slept on.
"""

from hive.fleet import DEFAULT_LIVENESS, LivenessPolicy

NOW = 1_800_000_000.0  # any fixed epoch; verdicts depend only on the delta
HOUR = 3600.0

SILENCES = [
    ("30 seconds", 30.0),
    ("5 minutes", 300.0),
    ("1 hour", HOUR),
    ("5 hours", 5 * HOUR),
    ("1 day + 1h", 25 * HOUR),
    ("8 days", 8 * 24 * HOUR),
]

print(f"{'silent for':<12} {'laptop':<10} {'server':<10} {'unknown':<10}")
for label, silence in SILENCES:
    verdicts = [
        DEFAULT_LIVENESS.assess(NOW - silence, kind, now=NOW)
        for kind in ("laptop", "server", "unknown")
    ]
    print(f"{label:<12} " + " ".join(f"{v:<10}" for v in verdicts))

print()
print("Same timeline under a stricter custom policy (dark after 1h for everyone):")
strict = LivenessPolicy(dark_after_s={}, dark_default_s=HOUR)
for label, silence in SILENCES:
    verdict = strict.assess(NOW - silence, "laptop", now=NOW)
    print(f"{label:<12} {verdict}")
