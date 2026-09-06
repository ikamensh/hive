"""H8/H9 controlled capacity drill. `prepare` is read-only; apply/restore are offline.

Synthetic cooldowns are separate from provider telemetry and quota exhaustion
history. This script never pauses/resumes Hive or changes a running task.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import time
from urllib.parse import quote, urlparse
from urllib.request import urlopen
import uuid

from hive._control.agent_choice import agent_candidates
from hive._control.limits import apply_snapshot
from hive._control.supervisor import Supervisor
from hive.models import LimitEvent, Project, Resource, Runner, Task, TaskStatus, Workspace
from hive.persistence.store import FileStore, MemoryStore


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def local_address(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("the drill only accepts an uncredentialed localhost HTTP chief")
    return parsed.hostname, parsed.port or 80


def capture(url: str, project_id: str) -> dict:
    local_address(url)

    def get(route):
        with urlopen(url.rstrip("/") + route, timeout=10) as response:
            return json.load(response)

    return {"at": time.time(), "project": get(f"/api/projects/{quote(project_id, safe='')}"),
            "resources": get("/api/resources"), "workspace": get("/api/workspace"),
            "storage": get("/api/storage"), "limits": get("/api/show").get("limits", [])}


def capture_evidence(path: Path) -> Path:
    """Annotate observed expiry without clearing or changing any Hive state."""
    manifest = json.loads(path.read_text())
    snapshot = capture(manifest["url"], manifest["project_id"])
    resources = {r["id"]: r for r in snapshot["resources"]["resources"]}
    now = time.time()
    entries = []
    for operation in manifest["operations"]:
        current = resources[operation["resource_id"]]["model_cooldowns"].get(operation["model"])
        entries.append({"resource_id": operation["resource_id"], "model": operation["model"],
                        "current_until": current,
                        "active_injected": operation.get("changed", False) and current is not None
                        and current == operation.get("injected") and current > now})
    snapshot["drill"] = {"id": manifest["id"], "label": manifest["label"],
                         "planned_until": manifest.get("until"), "entries": entries,
                         "planned_duration_expired": "until" in manifest and now >= manifest["until"]}
    evidence = path.parent / f"live-{time.time_ns()}.json"
    write_json(evidence, snapshot)
    return evidence


def state_path(snapshot: dict) -> Path:
    storage = snapshot["storage"]
    path = Path(storage["store_path"]).resolve()
    if storage["backend"] != "file" or path.parts[-3:] != ("experiments", "production-simulator", "store"):
        raise ValueError("the drill requires the isolated experiments/production-simulator/store")
    return path


def selection_matrix(snapshot: dict, candidates: list) -> dict:
    """Exercise real dispatch with synthetic windows in quiescent scratch memory."""
    claude = [c.model for c in candidates if c.backend == "claude"]
    if len(claude) != 2:
        raise ValueError("the scope drill requires two explicit Claude model preferences")
    cases = {
        "baseline": {},
        "codex_unavailable": {"codex": [""]},
        "first_claude_model_capped": {"codex": [""], "claude": [claude[0]]},
        "second_claude_model_capped": {"codex": [""], "claude": [claude[1]]},
        "claude_shared_cap": {"codex": [""], "claude": [""]},
        "all_subscription_models_capped": {"codex": [""], "claude": claude},
    }
    rows = []
    for name, caps in cases.items():
        store = MemoryStore()
        project = store.put(Project.model_validate(snapshot["project"]["project"]))
        project.paused = False
        for raw in snapshot["resources"]["runners"]:
            store.put(Runner.model_validate(raw))
        for raw in snapshot["resources"]["resources"]:
            resource = Resource.model_validate(raw)
            if scopes := caps.get(resource.backend):
                windows = [w.model_dump() for w in resource.usage_windows]
                windows += [{"kind": f"synthetic_{i}", "model_scope": scope, "used_percent": 100,
                             "resets_at": time.time()+3600} for i, scope in enumerate(scopes)]
                apply_snapshot(resource, {"source": "synthetic_h8h9", "captured_at": max(time.time(), resource.usage_captured_at+1),
                                          "windows": windows})
            store.put(resource)
        detail = snapshot["project"]
        for raw in [*detail.get("tasks", []), *(detail.get("plan") or {}).get("tasks", [])]:
            if raw["status"] not in {"pending", "running"}:
                store.put(Task.model_validate(raw))
        task = store.put(Task(project_id=project.id, workstream_id="", repo=project.spec_repo,
                              instructions="Synthetic dispatch only; no runner executes this task",
                              backend=candidates[0].backend, model=candidates[0].model))
        Supervisor(store, lambda *_: None, workspace_id=project.workspace_id).dispatch(project)
        result = store.get(Task, task.id)
        rows.append({"scenario": name, "synthetic_windows": caps, "status": result.status,
                     "backend": result.backend, "model": result.model, "dispatch_reason": result.dispatch_reason})
    return {"label": "SYNTHETIC in-memory window scenarios; no provider calls or real tasks",
            "assumption": "Current capacity and grants, with running/pending work omitted to model the next idle boundary",
            "scenarios": rows}


def prepare(snapshot: dict, url: str, output: Path) -> Path:
    """Record natural telemetry and the proposed changes without touching Hive."""
    local_address(url)
    path = state_path(snapshot)
    project = Project.model_validate(snapshot["project"]["project"])
    task = Task(project_id=project.id, workstream_id="", repo=project.spec_repo, instructions="Capacity drill candidate",
                backend=project.build_backend or "codex", model=project.build_model)
    candidates = agent_candidates(project, task)
    if (not project.included_only or project.daily_budget_usd != 0 or not candidates
            or candidates[-1].backend != "opencode" or not candidates[-1].model.startswith("opencode/")
            or not candidates[-1].model.endswith("-free")
            or {c.backend for c in candidates[:-1]} != {"codex", "claude"}
            or any(not c.model for c in candidates)):
        raise ValueError("configure explicit subscription model preferences ending in free OpenCode at $0 included-only")
    operations = []
    for candidate in candidates[:-1]:
        matching = [r for r in snapshot["resources"]["resources"]
                    if r["backend"] == candidate.backend and r["workspace_id"] == project.workspace_id]
        if not matching:
            raise ValueError(f"no resource for configured {candidate.backend}")
        for resource in matching:
            operations.append({"resource_id": resource["id"], "backend": candidate.backend,
                               "model": candidate.model,
                               "before": resource.get("model_cooldowns", {}).get(candidate.model)})
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "natural-before.json", snapshot)
    write_json(output / "selection-matrix.json", selection_matrix(snapshot, candidates))
    manifest = output / "manifest.json"
    write_json(manifest, {"id": uuid.uuid4().hex[:12], "status": "prepared", "url": url,
                         "store_path": str(path), "workspace_id": project.workspace_id,
                         "project_id": project.id, "prepared_at": time.time(),
                         "label": "SYNTHETIC H8/H9 capacity; not provider-reported quota exhaustion",
                         "candidates": [c.model_dump() for c in candidates], "operations": operations})
    return manifest


@contextmanager
def offline_store(manifest: dict):
    """Fence out a chief and refuse to alter capacity while any work runs."""
    address = local_address(manifest["url"])
    try:
        connection = socket.create_connection(address, timeout=2)
    except ConnectionRefusedError:
        pass
    else:
        connection.close()
        raise RuntimeError("stop the local chief before applying or restoring capacity")
    path = state_path({"storage": {"backend": "file", "store_path": manifest["store_path"]}})
    store = FileStore(path)
    holder = f"capacity-drill:{os.getpid()}:{uuid.uuid4().hex}"
    workspace = manifest["workspace_id"]
    if store.claim_leader(holder, 60, workspace) != holder:
        raise RuntimeError("a chief still holds the leader lease; wait for graceful shutdown")
    try:
        if not (row := store.get(Workspace, workspace)) or not row.paused:
            raise RuntimeError("the isolated workspace must already be paused")
        if store.list(Task, workspace_id=workspace, status=TaskStatus.running):
            raise RuntimeError("wait for every running task and probe to finish before this drill")
        projects = [p for p in store.list(Project, workspace_id=workspace) if not p.archived]
        if len(projects) != 1 or projects[0].id != manifest["project_id"]:
            raise RuntimeError("the drill requires exactly its isolated experiment project")
        for operation in manifest["operations"]:
            resource = store.get(Resource, operation["resource_id"])
            if resource is None or resource.workspace_id != workspace or resource.backend != operation["backend"]:
                raise RuntimeError("a prepared resource no longer matches the experiment")
        yield store
    finally:
        store.release_leader(holder, workspace)


def audit(store, manifest: dict, operation: dict, kind: str) -> None:
    resource = store.get(Resource, operation["resource_id"])
    event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{manifest['id']}:{operation['resource_id']}:{operation['model']}:{kind}").hex[:12]
    if store.get(LimitEvent, event_id) is None:
        store.put(LimitEvent(id=event_id, workspace_id=resource.workspace_id, machine_id=resource.machine_id,
                            runner_id=resource.runner_id, backend=resource.backend, model=operation["model"],
                            model_scope=operation["model"], kind=kind, source="synthetic_h8h9",
                            text=f"{manifest['label']}; drill {manifest['id']}",
                            reset_at_hint=manifest["until"]))


def apply(path: Path, *, duration_s: float = 3600) -> dict:
    """Apply finite scoped cooldowns; a persisted journal supports crash recovery."""
    if not 60 <= duration_s <= 7200:
        raise ValueError("duration must be between 60 and 7200 seconds")
    manifest = json.loads(path.read_text())
    if manifest["status"] not in {"prepared", "applying", "applied"}:
        raise ValueError("this drill has already been restored")
    with offline_store(manifest) as store:
        project = store.get(Project, manifest["project_id"])
        candidate = Task(project_id=project.id, workstream_id="", repo=project.spec_repo,
                         instructions="Check prepared candidate chain", backend=project.build_backend or "codex", model=project.build_model)
        if [c.model_dump() for c in agent_candidates(project, candidate)] != manifest["candidates"]:
            raise RuntimeError("project preferences changed since preparation; prepare a fresh drill")
        if manifest["status"] == "prepared":
            manifest.update(status="applying", until=time.time()+duration_s)
            for operation in manifest["operations"]:
                operation["injected"] = max(operation["before"] or 0, manifest["until"])
                operation["changed"] = operation["injected"] != operation["before"]
            write_json(path, manifest)  # journal before the first capacity mutation
        if manifest["until"] <= time.time():
            raise ValueError("the prepared cooldown has expired; restore and prepare a new drill")
        for operation in manifest["operations"]:
            resource = store.get(Resource, operation["resource_id"])
            current = resource.model_cooldowns.get(operation["model"])
            if current not in (operation["before"], operation["injected"]):
                raise RuntimeError("capacity changed since preparation; restore this drill and prepare again")
        if not (path.parent / "store-before-apply.json").exists():
            write_json(path.parent / "store-before-apply.json", [r.model_dump() for r in store.list(Resource)])
        for operation in manifest["operations"]:
            store.update(Resource, operation["resource_id"],
                         lambda r, op=operation: r.model_cooldowns.__setitem__(op["model"], op["injected"]))
            if operation["changed"]:
                audit(store, manifest, operation, "synthetic_capacity")
        manifest.update(status="applied", applied_at=time.time())
        write_json(path, manifest)
    return manifest


def restore(path: Path) -> dict:
    """Restore only still-owned entries; keep fresher real quota state intact."""
    manifest = json.loads(path.read_text())
    if manifest["status"] not in {"applying", "applied", "restored", "restored_with_conflicts"}:
        raise ValueError("this drill has not been applied")
    conflicts = []
    with offline_store(manifest) as store:
        write_json(path.parent / "store-before-restore.json", [r.model_dump() for r in store.list(Resource)])
        for operation in manifest["operations"]:
            resource = store.get(Resource, operation["resource_id"])
            current = resource.model_cooldowns.get(operation["model"])
            if current == operation["injected"]:
                if operation["before"] is None:
                    resource.model_cooldowns.pop(operation["model"])
                else:
                    resource.model_cooldowns[operation["model"]] = operation["before"]
                store.put(resource)
            elif current != operation["before"]:
                conflicts.append({**operation, "current": current})
                audit(store, manifest, operation, "synthetic_capacity_restore_conflict")
                continue
            if operation["changed"]:
                audit(store, manifest, operation, "synthetic_capacity_restored")
        manifest.update(status="restored_with_conflicts" if conflicts else "restored",
                        restored_at=time.time(), restore_conflicts=conflicts)
        write_json(path.parent / "store-after-restore.json", [r.model_dump() for r in store.list(Resource)])
        write_json(path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="dry run: capture telemetry and proposed changes")
    prep.add_argument("project", help="project ID")
    prep.add_argument("--url", default="http://127.0.0.1:8000")
    prep.add_argument("--output", required=True, type=Path, help="new evidence directory")
    for command in ("apply", "restore", "capture"):
        cmd = commands.add_parser(command)
        cmd.add_argument("manifest", type=Path)
        if command == "apply":
            cmd.add_argument("--duration", type=float, default=3600, help="cooldown seconds, at most two hours")
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare(capture(args.url, args.project), args.url, args.output))
    elif args.command == "capture":
        print(capture_evidence(args.manifest))
    else:
        result = apply(args.manifest, duration_s=args.duration) if args.command == "apply" else restore(args.manifest)
        print(json.dumps({"status": result["status"], "label": result["label"],
                          "restore_conflicts": result.get("restore_conflicts", [])}, indent=2))
        if result.get("restore_conflicts"):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
