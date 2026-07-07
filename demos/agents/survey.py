"""Demo: survey this machine's coding agents — `hive.agents` standalone.

Task: you got a new machine (or borrowed a teammate's) and want to know, before
wiring it into anything: which agent CLIs are installed, which look runnable,
what to do about the ones that aren't, and how much subscription quota the
installed ones have left — all without spending a single agent turn.

    uv run python demos/agents/survey.py

Read-only and free: discovery shells out to `--version`, usage reads the
provider's own gauge. To *prove* a backend end-to-end (spends one tiny turn):

    python -c "from pathlib import Path; from hive.agents import probe_backend; \
print(probe_backend('claude', Path('/tmp/agent-demo')).text)"
"""

from hive.agents import REGISTRY, discover_backends
from hive.agents.usage import collect_usage

for discovery in discover_backends():
    backend = REGISTRY[discovery.name]
    print(f"== {discovery.name} ({backend.licensing} license)")
    if not discovery.installed:
        print(f"   not installed — {discovery.message}")
        print(f"   to fix: {backend.login_hint}")
        continue
    print(f"   {discovery.status}: {discovery.version}  [{discovery.path}]")
    if discovery.message:
        print(f"   note: {discovery.message}")
    snapshot = collect_usage(discovery.name)
    if not snapshot:
        print("   usage: no native gauge for this backend")
        continue
    print(f"   usage (plan: {snapshot.get('plan') or 'unknown'}, source: {snapshot['source']}):")
    for window in snapshot.get("windows", []):
        print(f"     {window['kind']:<8} {window['used_percent']:>5.1f}% used")
