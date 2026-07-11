"""The fleet-wide pause switch: one Workspace flag, two chokepoint gates.

While paused, hive starts nothing new anywhere — the supervisor's step does
only bookkeeping (orphan recovery, dark-machine checks, todo resolution) and
`dispatch` hands out no tasks, so queued work and orchestrator events simply
wait. Tasks already on runners finish and their results are recorded normally.
This is the "hive is eating quota I need right now" switch; the per-machine
counterpart is the runner pause flag (hive/runner/control.py).
"""

from __future__ import annotations

import time

from hive.models import Workspace


def fleet_paused(store, workspace_id: str) -> bool:
    workspace = store.get(Workspace, workspace_id)
    return bool(workspace and workspace.paused)


def set_fleet_paused(store, workspace_id: str, paused: bool) -> Workspace:
    """Flip the switch, creating the workspace row on first use."""
    workspace = store.get(Workspace, workspace_id) or Workspace(id=workspace_id)
    workspace.paused = paused
    workspace.paused_at = time.time() if paused else 0.0
    return store.put(workspace)
