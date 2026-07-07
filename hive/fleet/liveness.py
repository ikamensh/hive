"""Is this machine with us: one liveness verdict from a heartbeat timestamp.

Every consumer (dispatch gating, dark-machine escalation, fleet views) reads
the same `LivenessPolicy` instead of scattering thresholds, so "online",
"dark", and "retired" mean the same thing everywhere. Thresholds depend on the
machine's availability class: a laptop sleeps in a backpack for a day without
that being an incident; a server silent for hours needs a human.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class Liveness(StrEnum):
    """Ordered states of silence: each one means "quieter" than the previous."""

    online = "online"  # heartbeat within the online window; dispatchable
    quiet = "quiet"  # no live heartbeat, but silence is still unremarkable
    dark = "dark"  # silent past its availability-class threshold; ask a human
    retired = "retired"  # silent so long we treat it as decommissioned; stay quiet


@dataclass(frozen=True)
class LivenessPolicy:
    """Thresholds that turn (last_seen, device_kind) into a `Liveness`."""

    online_window_s: float = 90.0
    dark_after_s: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType(
            {"laptop": 24 * 3600.0, "server": 4 * 3600.0}
        )
    )
    dark_default_s: float = 24 * 3600.0
    retired_after_s: float = 7 * 24 * 3600.0

    def dark_after(self, device_kind: str = "unknown") -> float:
        """Silence budget for one availability class before a machine is dark."""
        return self.dark_after_s.get(device_kind, self.dark_default_s)

    def assess(
        self,
        last_seen: float,
        device_kind: str = "unknown",
        *,
        now: float | None = None,
    ) -> Liveness:
        """The liveness verdict for a machine last heard from at `last_seen`."""
        silent_for = (time.time() if now is None else now) - last_seen
        if silent_for >= self.retired_after_s:
            return Liveness.retired
        if silent_for >= self.dark_after(device_kind):
            return Liveness.dark
        if silent_for >= self.online_window_s:
            return Liveness.quiet
        return Liveness.online


DEFAULT_LIVENESS = LivenessPolicy()
