"""Machine identity & liveness — usable on its own, no other hive imports.

Answers two questions about machines:
- identity: what host is this, and what stable id does a named machine get
  (`machine_metadata`, `stable_machine_id`);
- liveness: given a last-heartbeat timestamp and availability class, is the
  machine online, quiet, dark, or retired (`LivenessPolicy.assess`).

`python -m hive.fleet` prints this host's identity. Demos: `demos/fleet/`.
"""

from hive.fleet.identity import (
    detect_machine_arch,
    detect_machine_os,
    infer_device_kind,
    infer_machine_type,
    machine_metadata,
    stable_machine_id,
)
from hive.fleet.liveness import DEFAULT_LIVENESS, Liveness, LivenessPolicy

__all__ = [
    "DEFAULT_LIVENESS",
    "Liveness",
    "LivenessPolicy",
    "detect_machine_arch",
    "detect_machine_os",
    "infer_device_kind",
    "infer_machine_type",
    "machine_metadata",
    "stable_machine_id",
]
