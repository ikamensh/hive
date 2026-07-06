"""Runner registration & resource probing.

The heart of the `POST /api/runners/register` heartbeat: upsert the machine and
runner, requeue work the runner dropped on reboot, reconcile per-backend
resources from the runner's discoveries/capabilities, queue startup probes, and
record the git facts for each checkout. Plus the probe primitive shared with the
web `POST /api/resources/{id}/probe` route.

Lifted out of `hive.api` as free functions over the store, matching the
workstream modules; like `hive._integrations.auth` it raises `HTTPException`
directly so the routes stay thin. The runner-protocol request schema lives here
too, beside the logic that consumes it.
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException
from pydantic import BaseModel

from hive._integrations.auth import ensure_machine
from hive.runner._backends import probe_instructions
from hive.models import (
    Checkout,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
)

log = logging.getLogger("hive.runner.registration")

# Probe tasks carry this sentinel instead of a real repo: the runner builds its
# own local probe repo (works for remote runners, no shared filesystem).
PROBE_REPO = "probe:local"


class BackendDiscoveryInput(BaseModel):
    name: str
    installed: bool = True
    status: str = "unknown"
    path: str = ""
    version: str = ""
    message: str = ""


class CheckoutReport(BaseModel):
    """Git facts the runner observed for one repo checkout it holds."""

    repo: str  # git URL
    exists: bool = True
    head_sha: str = ""
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    dirty: bool = False


class RunnerRegister(BaseModel):
    name: str
    backends: list[str]
    machine_id: str = ""
    machine_name: str = ""
    machine_type: str = ""
    machine_os: str = ""
    machine_arch: str = ""
    machine_kind: str = ""
    boot: bool = False  # true on daemon startup (vs periodic heartbeat)
    # Set on machines onboarded via `hive enroll`: the member whose enrollment
    # token installed this runner. Claims the machine for them on first
    # register; never steals a machine someone already owns.
    owner_user_id: str = ""
    discoveries: list[BackendDiscoveryInput] = []
    capabilities: list[str] = []
    auto_probe: bool = False
    checkouts: list[CheckoutReport] = []  # git facts per repo this runner has checked out


def active_probe_task(store, resource: Resource) -> Task | None:
    if not resource.last_probe_task_id:
        return None
    task = store.get(Task, resource.last_probe_task_id)
    if (
        task
        and task.workspace_id == resource.workspace_id
        and task.kind == TaskKind.probe
        and task.status == TaskStatus.running
    ):
        return task
    return None


def queue_probe(store, resource: Resource, runner: Runner) -> tuple[Task, Resource]:
    if not resource.enabled:
        raise HTTPException(409, "resource is disabled")
    if resource.backend not in runner.backends:
        raise HTTPException(409, "runner no longer advertises this backend")
    if task := active_probe_task(store, resource):
        return task, resource

    task = store.put(
        Task(
            workspace_id=resource.workspace_id,
            project_id="",
            workstream_id="",
            repo=PROBE_REPO,  # sentinel; the runner builds its own local probe repo
            kind=TaskKind.probe,
            instructions=probe_instructions(resource.backend),
            backend=resource.backend,
            status=TaskStatus.running,
            runner_id=runner.id,
        )
    )
    resource.usability_status = ResourceUsability.probing
    resource.last_probe_at = time.time()
    resource.last_probe_task_id = task.id
    resource.last_probe_text = "Probe queued."
    store.put(resource)
    return task, resource


def should_auto_probe(
    resource: Resource,
    discovery: BackendDiscoveryInput | None,
    *,
    auto_probe: bool,
    boot: bool,
) -> bool:
    if not auto_probe or not resource.enabled:
        return False
    if resource.usability_status == ResourceUsability.probing:
        return False
    if resource.usability_status == ResourceUsability.unknown:
        return True
    if not boot:
        return False
    if not discovery or not discovery.installed or discovery.status != "ok":
        return False
    return not resource.available()


def upsert_checkouts(
    store, workspace_id: str, machine_id: str, reports: list[CheckoutReport]
) -> None:
    """Record the git facts the runner observed for each repo on this machine.
    Keyed by (machine, repo); the runner is authoritative for its own host, so a
    fresh report replaces the prior one."""
    existing = {
        c.repo: c
        for c in store.list(Checkout, workspace_id=workspace_id, machine_id=machine_id)
    }
    for report in reports:
        checkout = existing.get(report.repo) or Checkout(
            workspace_id=workspace_id, machine_id=machine_id, repo=report.repo
        )
        checkout.exists = report.exists
        checkout.head_sha = report.head_sha
        checkout.branch = report.branch
        checkout.ahead = report.ahead
        checkout.behind = report.behind
        checkout.dirty = report.dirty
        checkout.last_reported_at = time.time()
        store.put(checkout)


def register(store, body: RunnerRegister, workspace_id: str) -> dict:
    machine = ensure_machine(
        store,
        workspace_id,
        name=body.machine_name or body.name,
        machine_id=body.machine_id,
        hostname=body.name,
        kind="runner",
        machine_type=body.machine_type,
        machine_os=body.machine_os,
        machine_arch=body.machine_arch,
        device_kind=body.machine_kind,
    )
    if body.owner_user_id and not machine.owner_user_id:
        machine.owner_user_id = body.owner_user_id
        machine = store.put(machine)
    existing = next(
        (r for r in store.list(Runner, workspace_id=workspace_id) if r.name == body.name),
        None,
    )
    runner = existing or Runner(
        workspace_id=workspace_id,
        machine_id=machine.id,
        name=body.name,
    )
    runner.workspace_id = workspace_id
    runner.machine_id = machine.id
    runner.backends = body.backends
    runner.capabilities = sorted(set(body.capabilities))
    runner.last_seen = time.time()
    store.put(runner)

    if body.boot:
        _requeue_dropped_work(store, workspace_id, runner)

    discovery_by_name = {d.name: d for d in body.discoveries}
    resources_by_pair = {
        (r.machine_id or r.runner_id, r.backend): r
        for r in store.list(Resource, workspace_id=workspace_id)
    }

    def apply_discovery(resource: Resource, discovery: BackendDiscoveryInput) -> None:
        resource.discovery_status = discovery.status
        resource.discovery_text = discovery.message
        resource.discovered_at = time.time()
        resource.cli_path = discovery.path
        resource.cli_version = discovery.version

    def apply_capabilities(resource: Resource) -> None:
        caps = set(body.capabilities)
        resource.browser_status = (
            ResourceUsability.usable if "browser" in caps else ResourceUsability.unknown
        )
        resource.browser_probe_at = time.time() if "browser" in caps else resource.browser_probe_at
        resource.browser_probe_text = "Runner advertised browser capability." if "browser" in caps else resource.browser_probe_text
        resource.docker_status = (
            ResourceUsability.usable if "docker" in caps else ResourceUsability.unknown
        )
        resource.docker_probe_at = time.time() if "docker" in caps else resource.docker_probe_at
        resource.docker_probe_text = "Runner advertised docker capability." if "docker" in caps else resource.docker_probe_text

    for backend in body.backends:
        resource = (
            resources_by_pair.get((machine.id, backend))
            or resources_by_pair.get((runner.id, backend))
            or Resource(
                workspace_id=workspace_id,
                machine_id=machine.id,
                runner_id=runner.id,
                backend=backend,
            )
        )
        resource.workspace_id = workspace_id
        resource.machine_id = machine.id
        resource.runner_id = runner.id
        if discovery := discovery_by_name.get(backend):
            apply_discovery(resource, discovery)
        apply_capabilities(resource)
        store.put(resource)
        resources_by_pair[(machine.id, backend)] = resource
        if should_auto_probe(
            resource,
            discovery_by_name.get(backend),
            auto_probe=body.auto_probe,
            boot=body.boot,
        ):
            queue_probe(store, resource, runner)

    for discovery in body.discoveries:
        if discovery.installed:
            continue
        resource = resources_by_pair.get((machine.id, discovery.name))
        if resource:
            apply_discovery(resource, discovery)
            store.put(resource)

    upsert_checkouts(store, workspace_id, machine.id, body.checkouts)

    return {"runner_id": runner.id, "machine_id": machine.id}


def _requeue_dropped_work(store, workspace_id: str, runner: Runner) -> None:
    """A booting daemon executes nothing: whatever was in flight on this runner
    died with the previous process — requeue it (or fail probes) before queuing
    fresh startup probes."""

    def requeue(task: Task) -> None:
        if task.kind == TaskKind.probe:
            task.status = TaskStatus.failed
            task.is_error = True
            task.result_text = "Probe interrupted because the runner rebooted."
            task.finished_at = time.time()
        else:
            task.status = TaskStatus.pending
            task.runner_id = ""
            task.delivered = False

    for task in store.list(
        Task, workspace_id=workspace_id, status=TaskStatus.running, runner_id=runner.id
    ):
        updated = store.update(Task, task.id, requeue)
        if updated and updated.kind == TaskKind.probe:
            for resource in store.list(
                Resource,
                workspace_id=workspace_id,
                runner_id=runner.id,
                backend=updated.backend,
            ):
                if resource.last_probe_task_id == updated.id:
                    resource.usability_status = ResourceUsability.unknown
                    resource.last_probe_text = updated.result_text
                    store.put(resource)
            log.info("failed probe %s after runner %s reboot", task.id, runner.name)
        else:
            log.info("requeued task %s after runner %s reboot", task.id, runner.name)
