"""Demo: leader election over a shared store — `hive.persistence` standalone.

Task: several copies of a service share one database, but exactly one may act
as the writer. The store's TTL leader lease solves it without any extra
infrastructure: claim to lead, renew while alive, and a crashed leader is
superseded the moment its lease lapses — no operator in the loop.

    uv run python demos/persistence/leader_election.py

Offline and fast: two in-process "chiefs" contend over a FileStore.
"""

import tempfile
import time
from pathlib import Path

from hive.persistence import FileStore

TTL = 0.5

with tempfile.TemporaryDirectory() as tmp:
    store = FileStore(Path(tmp) / "shared-data")

    print(f"alpha claims:              -> {store.claim_leader('alpha', TTL)}")
    print(f"beta contends:             -> {store.claim_leader('beta', TTL)} (alpha holds)")
    print(f"alpha renews:              -> {store.claim_leader('alpha', TTL)}")

    print(f"\n'alpha' crashes (stops renewing); lease TTL is {TTL}s ...")
    time.sleep(TTL + 0.05)
    winner = store.claim_leader("beta", TTL)
    print(f"beta contends again:       -> {winner} (took over)")
    assert winner == "beta"

    print(f"alpha (rebooted) contends: -> {store.claim_leader('alpha', TTL)} (beta holds now)")

    released = store.release_leader("beta")
    print(f"\nbeta shuts down gracefully: released={released}")
    print(f"alpha claims instantly:    -> {store.claim_leader('alpha', TTL)}")

    # Scopes are independent: a lease per workspace/tenant.
    assert store.claim_leader("gamma", TTL, workspace_id="tenant-2") == "gamma"
    print("gamma leads tenant-2 concurrently — scopes don't contend.")
