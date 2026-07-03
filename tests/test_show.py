"""Subsystem views behind `hive show` (machines / agents / autonomy).

Properties: the views are faithful read-only projections of the store — an
agent is launchable exactly where an online runner advertises its backend, a
license maps to the machines where its provider proved usable, and autonomy
rows mirror the supervisor's real gates (budget envelope, paused, cooldown)
without firing anything. Assertions are relationships between records and
rows, not hand-counted constants, so they survive refactors.
"""

import time
from pathlib import Path

from hive._control.show import build_show
from hive._control.supervisor import Supervisor
from hive.config.settings import Config
from hive.models import (
    Machine,
    Project,
    ProjectWorkstream,
    ProjectWorkstreamKind,
    Resource,
    ResourceUsability,
    Runner,
    Subscription,
)
from hive.persistence.store import MemoryStore

WS = "default"


def _config(**kw) -> Config:
    return Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="t", data_dir=Path("/tmp/hive-show-test"), **kw
    )


def _spend_zero(_pid: str) -> float:
    return 0.0


def _show(store, spend=_spend_zero, config=None):
    return build_show(store, WS, spend, config or _config())


def _usable_agent(store, *, backend="codex", machine_name="mini", last_seen=None):
    """One machine with an online runner offering a proven-usable backend."""
    seen = time.time() if last_seen is None else last_seen
    machine = store.put(
        Machine(workspace_id=WS, name=machine_name, hostname=f"{machine_name}.local",
                device_kind="server", last_seen=seen)
    )
    runner = store.put(
        Runner(workspace_id=WS, machine_id=machine.id, name=f"{machine_name}-runner",
               backends=[backend], last_seen=seen)
    )
    resource = store.put(
        Resource(workspace_id=WS, machine_id=machine.id, runner_id=runner.id,
                 backend=backend, usability_status=ResourceUsability.usable)
    )
    return machine, runner, resource


def test_empty_store_has_every_section():
    """Executability: an empty workspace still returns all three views, and the
    only autonomy row is the always-on org-wide dark-machine watch (idle, with
    a reason instead of a silent blank)."""
    view = _show(MemoryStore())
    assert view["machines"] == []
    assert view["agents"]["agents"] == []
    assert view["agents"]["launchable_now"] == 0
    assert view["agents"]["licenses"] == []
    [watch] = view["autonomy"]
    assert watch["job"] == "dark_machine_watch"
    assert watch["action_now"] == "" and watch["reason"]


def test_agent_launchable_exactly_where_runner_offers_backend():
    """An agent row is `available` only when its machine's runner is online AND
    advertises the backend — a usable CLI a runner stopped offering is visible
    but not launchable."""
    store = MemoryStore()
    machine, runner, _ = _usable_agent(store, backend="codex")
    # usable on disk, but the runner does not advertise claude anymore
    store.put(
        Resource(workspace_id=WS, machine_id=machine.id, runner_id=runner.id,
                 backend="claude", usability_status=ResourceUsability.usable)
    )

    view = _show(store)
    agents = {a["backend"]: a for a in view["agents"]["agents"]}
    assert agents["codex"]["available"] and agents["codex"]["machine"] == machine.name
    assert not agents["claude"]["available"]
    assert view["agents"]["launchable_now"] == 1

    [m] = view["machines"]
    assert m["online"] and m["hostname"] == "mini.local"
    assert m["runners"][0]["backends"] == ["codex"]


def test_offline_machine_goes_dark_and_out_of_dispatch():
    """A server silent past its dark threshold: nothing launchable there, the
    machines view flags it dark, and the dark-machine watch would act now."""
    store = MemoryStore()
    machine, _, _ = _usable_agent(store, last_seen=time.time() - 5 * 3600)

    view = _show(store)
    assert view["agents"]["launchable_now"] == 0
    [m] = view["machines"]
    assert not m["online"] and m["dark"] and not m["retired"]
    watch = next(j for j in view["autonomy"] if j["job"] == "dark_machine_watch")
    assert machine.name in watch["machines"] and machine.name in watch["action_now"]


def test_non_ready_agents_explain_themselves():
    """Regression (live fleet audit): the store knew *why* agents were not
    dispatchable ('Not logged in · Please run /login', quota exhaustion) but
    the view hid it. A non-ready agent must carry the one line the operator
    can act on; an expired cooldown is history and must not surface at all."""
    store = MemoryStore()
    machine, runner, _ = _usable_agent(store, backend="codex")
    runner.backends = ["codex", "claude", "cursor"]
    store.put(runner)
    store.put(Resource(
        workspace_id=WS, machine_id=machine.id, runner_id=runner.id, backend="claude",
        usability_status=ResourceUsability.failed,
        # banner noise first, the real error below — the note must find it
        last_probe_text="YOLO mode is enabled. All tool calls approved.\n"
                        "Loaded cached credentials.\n"
                        "Not logged in · Please run /login\n\nHIVE PROBE FAILED",
    ))
    store.put(Resource(
        workspace_id=WS, machine_id=machine.id, runner_id=runner.id, backend="cursor",
        usability_status=ResourceUsability.usable,
        cooldown_until=time.time() + 3600,
        last_exhaustion_text="quota exhausted until tomorrow",
    ))

    agents = {a["backend"]: a for a in _show(store)["agents"]["agents"]}
    assert agents["claude"]["status"] == "failed"
    assert agents["claude"]["note"] == "Not logged in · Please run /login"
    assert agents["cursor"]["status"] == "cooldown"
    assert agents["cursor"]["note"] == "quota exhausted until tomorrow"
    assert agents["cursor"]["cooldown_until"] > 0  # active cooldown stays visible
    assert agents["codex"]["note"] == "" and agents["codex"]["cooldown_until"] == 0.0


def test_expired_cooldown_is_reported_as_zero():
    """Regression: a June cooldown long expired surfaced as a raw epoch and
    read like state. Expired means gone."""
    store = MemoryStore()
    _, _, resource = _usable_agent(store)
    resource.cooldown_until = time.time() - 86400
    resource.last_exhaustion_text = "old quota message"
    store.put(resource)

    [agent] = _show(store)["agents"]["agents"]
    assert agent["status"] == "ready" and agent["available"]
    assert agent["cooldown_until"] == 0.0 and agent["note"] == ""


def test_machines_view_marks_where_the_chief_lives():
    """Regression: runner registration stamps Machine rows kind=runner, so a
    machine hosting both chief and runner looked like a plain runner. The
    chief's own config names its machine; the view must flag it."""
    store = MemoryStore()
    _usable_agent(store, machine_name="hive-vm")
    _usable_agent(store, machine_name="raven")

    view = build_show(store, WS, _spend_zero, _config(machine_name="hive-vm"))
    by_name = {m["name"]: m for m in view["machines"]}
    assert by_name["hive-vm"]["hosts_chief"]
    assert not by_name["raven"]["hosts_chief"]


def test_licenses_map_to_machines_where_provider_proved_usable():
    store = MemoryStore()
    machine, _, _ = _usable_agent(store, backend="codex")
    store.put(Subscription(workspace_id=WS, provider="codex", plan="ChatGPT Plus"))
    store.put(Subscription(workspace_id=WS, provider="claude", plan="Claude Max"))

    agents = _show(store)["agents"]
    licenses = {license_row["provider"]: license_row for license_row in agents["licenses"]}
    assert [m["machine"] for m in licenses["codex"]["machines"]] == [machine.name]
    assert licenses["claude"]["machines"] == []  # license present, no machine can serve it
    assert agents["license_candidates"] == []  # every usable backend already registered


def test_usable_backend_without_subscription_becomes_license_candidate():
    store = MemoryStore()
    _usable_agent(store, backend="cursor")
    candidates = _show(store)["agents"]["license_candidates"]
    assert [c["provider"] for c in candidates] == ["cursor"]
    # evidence names the machine (user-facing identity), not the runner process
    assert candidates[0]["evidence"] == "usable on mini"


def test_autonomy_lists_jobs_with_period_action_and_candidate_machines():
    """A budgeted ci_autofix + testing_auto project yields one ci_check job and
    one testing_check job per repo, each carrying the supervisor's real period
    and the machines its backend could dispatch to right now."""
    store = MemoryStore()
    machine, _, _ = _usable_agent(store, backend="codex")
    store.put(Project(
        workspace_id=WS, name="atlas", spec_repo="https://example.com/spec.git",
        member_repos=["https://example.com/app.git"], ci_autofix=True, daily_budget_usd=10,
    ))

    jobs = _show(store)["autonomy"]
    ci = next(j for j in jobs if j["job"] == "ci_check")
    assert ci["interval_s"] == Supervisor.CI_CHECK_INTERVAL_S
    assert ci["action_now"] and ci["machines"] == [machine.name]

    testing = [j for j in jobs if j["job"] == "testing_check"]
    # one row per distinct repo (member repo + spec repo)
    assert {j["repos"][0] for j in testing} == {
        "https://example.com/app.git", "https://example.com/spec.git",
    }
    for j in testing:
        assert j["interval_s"] == Supervisor.TESTING_CHECK_INTERVAL_S
        assert j["action_now"] == "refresh"  # empty backlog -> draft stories
        assert j["machines"] == [machine.name]
        assert j["workstream_id"] == ""  # first check will create the workstream


def test_autonomy_rows_respect_gates_and_always_explain_inaction():
    """Every gate that stops a job shows up as a named reason: paused, over
    budget, or no budget at all — action_now is blank but never unexplained."""
    store = MemoryStore()
    _usable_agent(store)
    project = store.put(Project(
        workspace_id=WS, name="atlas", spec_repo="https://example.com/spec.git",
        ci_autofix=True, daily_budget_usd=10,
    ))

    def testing_row(spend=_spend_zero):
        jobs = _show(store, spend=spend)["autonomy"]
        return next(j for j in jobs if j["job"] == "testing_check")

    over = testing_row(spend=lambda pid: 25.0)
    assert over["action_now"] == "" and "over today's budget" in over["blocked_by"]

    project.daily_budget_usd = 0.0
    store.put(project)
    unbudgeted = testing_row()
    assert unbudgeted["action_now"] == "" and "budget" in unbudgeted["reason"]

    project.daily_budget_usd = 10.0
    project.paused = True
    store.put(project)
    for job in _show(store)["autonomy"]:
        if job["job"] == "dark_machine_watch":
            continue
        assert job["action_now"] == "" and "project paused" in job["blocked_by"]

    project.archived = True
    store.put(project)
    assert [j["job"] for j in _show(store)["autonomy"]] == ["dark_machine_watch"]


def test_existing_testing_workstream_row_carries_its_id():
    store = MemoryStore()
    project = store.put(Project(
        workspace_id=WS, name="atlas", spec_repo="https://example.com/spec.git",
        daily_budget_usd=10,
    ))
    stream = store.put(ProjectWorkstream(
        workspace_id=WS, project_id=project.id, kind=ProjectWorkstreamKind.testing,
        title="Testing", repo=project.spec_repo,
    ))
    [row] = [j for j in _show(store)["autonomy"] if j["job"] == "testing_check"]
    assert row["workstream_id"] == stream.id
