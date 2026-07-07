"""`python -m hive.agents` — survey this machine: installed agent CLIs and
what each provider's own gauge says about remaining quota."""

import json

from hive.agents import discover_backends
from hive.agents.usage import collect_usage

for discovery in discover_backends():
    state = discovery.version if discovery.installed else "not installed"
    line = f"{discovery.name:<11} {discovery.status:<8} {state}"
    if discovery.message:
        line += f"  ({discovery.message})"
    print(line)
    if discovery.installed and (snapshot := collect_usage(discovery.name)):
        print(json.dumps(snapshot, indent=1))
