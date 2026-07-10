"""Testability-contract properties (wiki/testability-contract.md).

What must hold: the contract mirror is idempotent and probe evidence only
survives while the file content it proved is unchanged; decision questions are
asked exactly once per key regardless of rewordings; the draft->probe chain
and the answer->re-draft loop run without a human step beyond answering; and
sweeps see the contract instead of improvising run recipes.
"""

import subprocess

from hive.models import (
    HumanTask,
    HumanTaskStatus,
    Project,
    Question,
    QuestionStatus,
    Task,
    TaskKind,
    TaskStatus,
    TestabilityStatus as ContractStatus,
)
from hive.persistence.store import MemoryStore
from hive._control.supervisor import Supervisor
from hive._workstreams.testability import (
    DecisionDraft,
    DraftResultSummary,
    active_contract_task,
    auto_contract_action,
    contract_context,
    create_decision_questions,
    decision_dedup_key,
    get_contract,
    parse_fidelities,
    queue_draft_task,
    queue_probe_task,
    reconcile_contract,
    record_probe_result,
    testability_health as contract_health,
)
from hive._workstreams.testing import ensure_testing_workstream, queue_sweep_tasks
from tests.test_api_e2e import RUNNER_HEADERS, _pump, _register_usable_runner
from tests.test_testing import _poll, _report, app, spec_repo


CONTRACT_MD = (
    "# testability: beacon\n\n"
    "## Run\n"
    "### local\n"
    "    make run\n\n"
    "## Health\n"
    "GET http://localhost:8080/healthz returns 200 within 60s.\n"
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def write_contract(repo, text=CONTRACT_MD):
    (repo / "testability.md").write_text(text)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "testability contract"], repo)


def test_parse_fidelities_properties():
    """Fidelity detection reads the `### local`/`### docker` subsections only:
    case-insensitive, deduplicated, unknown names ignored — so a contract
    can't accidentally declare a fidelity via prose."""
    assert parse_fidelities(CONTRACT_MD) == ["local"]
    assert parse_fidelities("## Run\n### LOCAL\nx\n### docker\ny\n### local\nz\n") == ["local", "docker"]
    assert parse_fidelities("we also support docker somewhere in prose") == []
    assert parse_fidelities("### staging\nx\n") == []
    assert parse_fidelities("") == []


def test_reconcile_is_idempotent_and_probe_evidence_expires_with_content(tmp_path):
    """Property: reconciling unchanged content is a no-op for status (verified
    stays verified, one row total); editing the file demotes any probe verdict
    to `draft`, because evidence only speaks for the exact content it proved."""
    store = MemoryStore()
    repo = spec_repo(tmp_path)
    project = store.put(Project(name="p", spec_repo=str(repo)))
    stream = ensure_testing_workstream(store, project)

    # No file -> missing.
    contract = reconcile_contract(store, project, stream, repo)
    assert contract.status == ContractStatus.missing
    assert contract.content == ""

    write_contract(repo)
    contract = reconcile_contract(store, project, stream, repo)
    assert contract.status == ContractStatus.draft
    assert contract.fidelities == ["local"]

    contract = record_probe_result(store, contract, ok=True, fidelity="local", task_id="t1")
    assert contract.status == ContractStatus.verified
    assert contract.probed_fidelity == "local"

    # Unchanged content: idempotent, evidence kept, still exactly one row.
    again = reconcile_contract(store, project, stream, repo)
    assert again.id == contract.id
    assert again.status == ContractStatus.verified
    assert len(store.list(type(contract))) == 1

    # Edited content: back to draft; a broken verdict expires the same way.
    write_contract(repo, CONTRACT_MD + "\n### docker\n    docker compose up\n")
    edited = reconcile_contract(store, project, stream, repo)
    assert edited.status == ContractStatus.draft
    assert edited.fidelities == ["local", "docker"]

    broken = record_probe_result(store, edited, ok=False, problems=["make run: exit 2"], task_id="t2")
    assert broken.status == ContractStatus.broken
    assert reconcile_contract(store, project, stream, repo).status == ContractStatus.broken
    write_contract(repo, CONTRACT_MD.replace("make run", "make serve"))
    assert reconcile_contract(store, project, stream, repo).status == ContractStatus.draft

    # Deleting the file empties the mirror.
    (repo / "testability.md").unlink()
    assert reconcile_contract(store, project, stream, repo).status == ContractStatus.missing


def test_health_states_cover_contract_lifecycle():
    """Every lifecycle stage yields a distinct state with a one-step offer;
    open decisions dominate (they are the only human-required step), and a
    verified contract offers nothing."""
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="x"))
    stream = ensure_testing_workstream(store, project, repo="x")

    assert contract_health(None).state == "missing"
    assert contract_health(None).action == "draft"
    assert contract_health(None, draft_active=True).state == "drafting"

    contract = reconcile_contract(store, project, stream, spec_path=_missing_dir())
    assert contract_health(contract).action == "draft"

    contract.content, contract.status = CONTRACT_MD, ContractStatus.draft
    assert contract_health(contract).state == "draft"
    assert contract_health(contract).action == "probe"
    assert contract_health(contract, probe_active=True).state == "probing"
    assert contract_health(contract, open_decisions=2).state == "decisions"
    assert contract_health(contract, open_decisions=2).action == "decide"

    contract.status = ContractStatus.broken
    contract.probe_problems = ["make run: exit 2"]
    health = contract_health(contract)
    assert health.state == "broken"
    assert health.action == "draft"
    assert "make run: exit 2" in health.summary

    contract.status = ContractStatus.verified
    contract.probed_fidelity = "local"
    contract.probed_at = 1.0
    verified = contract_health(contract)
    assert verified.state == "verified"
    assert verified.action == "" and verified.offer == ""


def _missing_dir():
    from pathlib import Path

    return Path("/nonexistent-spec-home-for-tests")


def test_decision_questions_are_asked_exactly_once_per_key():
    """A decision key that was ever asked — open, answered, or dismissed — is
    never re-asked, so re-drafts can't nag; malformed decisions are dropped;
    keys are normalized so agent formatting can't split one decision in two."""
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="x"))
    stream = ensure_testing_workstream(store, project, repo="x")

    summary = DraftResultSummary.from_payload(
        {
            "decisions": [
                {
                    "key": "Stripe Sandbox!",
                    "question": "May tests exercise Stripe?",
                    "options": ["A) sandbox key", "B) skip payment stories"],
                    "recommendation": "A",
                },
                {"key": "", "question": "no key -> dropped"},
                {"key": "no-question", "question": "  "},
                "not-a-dict",
            ]
        }
    )
    assert [d.key for d in summary.decisions] == ["stripe-sandbox"]

    created = create_decision_questions(store, project, stream, summary.decisions)
    assert len(created) == 1
    q = created[0]
    assert q.dedup_key == decision_dedup_key(stream.id, "stripe-sandbox")
    assert "May tests exercise Stripe?" in q.text
    assert "A) sandbox key" in q.text and "Hive recommends: A" in q.text

    # Re-drafting with the same decision (any wording) files nothing new.
    reworded = (DecisionDraft(key="stripe-sandbox", question="Different words?", options=(), recommendation=""),)
    assert create_decision_questions(store, project, stream, reworded) == []
    for status in (QuestionStatus.answered, QuestionStatus.dismissed):
        q.status = status
        store.put(q)
        assert create_decision_questions(store, project, stream, reworded) == []
    assert len(store.list(Question)) == 1


def test_draft_result_chains_probe_and_files_decisions(tmp_path):
    """The guided-setup loop, end to end over the runner protocol: a manual
    draft request -> agent pushes testability.md + reports decisions -> the
    chief mirrors the contract, files the decision question, and auto-queues
    the probe (no human step); a green probe verifies the contract."""
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = client.post("/api/projects", json={"name": "beacon"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"spec_repo": str(repo)})
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(
        w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing"
    )

    queued = client.post(f"/api/projects/{pid}/workstreams/{stream['id']}/testability-draft", json={})
    assert queued.status_code == 200
    _pump(client, store)
    draft = _poll(client, rid)
    assert draft["kind"] == "testability_draft"
    assert "testability.md" in draft["instructions"]

    write_contract(repo)  # what the agent's push did
    _report(
        client,
        draft["id"],
        "Explored the repo and wrote the contract.\nTESTABILITY: DONE",
        structured_result={
            "task_id": draft["id"],
            "outcome": "done",
            "changed_files": ["testability.md"],
            "commit_sha": "abc123",
            "fidelities": ["local"],
            "decisions": [
                {
                    "key": "stripe-sandbox",
                    "question": "May tests exercise Stripe?",
                    "options": ["A) sandbox key", "B) skip payment stories"],
                    "recommendation": "A",
                }
            ],
        },
    )

    project = store.get(Project, pid)
    workstream = [w for w in store.list(type(ensure_testing_workstream(store, project)), project_id=pid) if w.id == stream["id"]][0]
    contract = get_contract(store, project, workstream)
    assert contract is not None and contract.status == ContractStatus.draft
    assert "make run" in contract.content
    decision = [q for q in store.list(Question, project_id=pid) if q.dedup_key]
    assert len(decision) == 1 and decision[0].dedup_key.endswith(":stripe-sandbox")

    # The probe was chained automatically and carries the contract text.
    _pump(client, store)
    probe = _poll(client, rid)
    assert probe["kind"] == "testability_probe"
    assert "make run" in probe["instructions"]
    _report(
        client,
        probe["id"],
        "Stood the app up per the contract.\nTESTABILITY_PROBE: OK",
        structured_result={"task_id": probe["id"], "outcome": "ok", "fidelity": "local", "problems": []},
    )
    contract = get_contract(store, project, workstream)
    assert contract.status == ContractStatus.verified
    assert contract.probed_fidelity == "local"

    # The project payload carries the whole story for the UI/CLI.
    view = client.get(f"/api/projects/{pid}").json()["testability"][stream["id"]]
    assert view["health"]["state"] == "decisions"  # the one open decision needs the human
    assert view["open_decisions"] == 1
    assert view["contract"]["status"] == "verified"


def test_probe_failure_marks_broken_and_escalates_until_green(tmp_path):
    """A failed probe records the problems, marks the contract broken, and
    files one env todo; a later green probe closes it — evidence-based, no
    manual done."""
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = client.post("/api/projects", json={"name": "beacon"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"spec_repo": str(repo)})
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(
        w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing"
    )
    write_contract(repo)

    client.post(f"/api/projects/{pid}/workstreams/{stream['id']}/testability-probe", json={})
    _pump(client, store)
    probe = _poll(client, rid)
    assert probe["kind"] == "testability_probe"
    _report(
        client,
        probe["id"],
        "Could not stand it up.\nTESTABILITY_PROBE: FAIL",
        structured_result={
            "task_id": probe["id"],
            "outcome": "fail",
            "fidelity": "local",
            "problems": ["make run failed: no rule to make target 'run'"],
        },
    )
    project = store.get(Project, pid)
    workstream = next(w for w in store.list(type(ensure_testing_workstream(store, project)), project_id=pid) if w.id == stream["id"])
    contract = get_contract(store, project, workstream)
    assert contract.status == ContractStatus.broken
    assert contract.probe_problems == ["make run failed: no rule to make target 'run'"]
    todo = next(t for t in store.list(HumanTask) if t.dedup_key == f"env:testability:{stream['id']}")
    assert todo.status == HumanTaskStatus.open
    assert "no rule to make target" in todo.instructions

    client.post(f"/api/projects/{pid}/workstreams/{stream['id']}/testability-probe", json={})
    _pump(client, store)
    probe2 = _poll(client, rid)
    _report(
        client,
        probe2["id"],
        "Healthy this time.\nTESTABILITY_PROBE: OK",
        structured_result={"task_id": probe2["id"], "outcome": "ok", "fidelity": "local", "problems": []},
    )
    assert get_contract(store, project, workstream).status == ContractStatus.verified
    assert store.get(HumanTask, todo.id).status == HumanTaskStatus.done


def test_answered_decision_queues_redraft_unless_one_is_in_flight(tmp_path):
    """Answering a testability decision is the user's only job: the chief
    queues a draft task that folds the answer in — unless a contract task is
    already in flight (the settled answer rides the next draft instead)."""
    from hive.config.settings import Config
    from hive._control.clarifications import apply_answer

    store = MemoryStore()
    repo = spec_repo(tmp_path)
    project = store.put(Project(name="p", spec_repo=str(repo)))
    stream = ensure_testing_workstream(store, project)
    supervisor = Supervisor(store, lambda pid, events: None)
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="t", data_dir=tmp_path / "data",
    )
    (question,) = create_decision_questions(
        store,
        project,
        stream,
        (DecisionDraft(key="stripe-sandbox", question="May tests exercise Stripe?", options=(), recommendation=""),),
    )

    apply_answer(store, supervisor, config, project, question, "A) sandbox key")
    drafts = store.list(Task, project_id=project.id, kind=TaskKind.testability_draft)
    assert len(drafts) == 1
    assert "stripe" in drafts[0].instructions.lower()
    assert "A) sandbox key" in drafts[0].instructions

    # Second answered decision while the draft is pending: no duplicate task.
    (question2,) = create_decision_questions(
        store,
        project,
        stream,
        (DecisionDraft(key="reset-policy", question="Wipe ./data between sweeps?", options=(), recommendation=""),),
    )
    apply_answer(store, supervisor, config, project, question2, "yes")
    assert len(store.list(Task, project_id=project.id, kind=TaskKind.testability_draft)) == 1

    # An ordinary (non-decision) question never queues contract work.
    plain = store.put(Question(project_id=project.id, text="what colour?"))
    drafts[0].status = TaskStatus.done
    store.put(drafts[0])
    apply_answer(store, supervisor, config, project, plain, "blue")
    assert len(store.list(Task, project_id=project.id, kind=TaskKind.testability_draft)) == 1


def test_sweeps_embed_the_contract_instead_of_improvising(tmp_path):
    """Sweep instructions carry the contract's run recipe (and its proof
    status) when one exists, and add nothing when none does."""
    from hive.models import Story, TestEpisode
    from hive._workstreams.testing import reconcile_story_backlog

    store = MemoryStore()
    repo = spec_repo(tmp_path)
    project = store.put(Project(name="p", spec_repo=str(repo)))
    stream = ensure_testing_workstream(store, project)
    reconcile_story_backlog(store, project, stream, repo)
    stories = store.list(Story, project_id=project.id)
    episode = store.put(TestEpisode(project_id=project.id, workstream_id=stream.id, repo=str(repo)))

    bare = queue_sweep_tasks(store, project, stream, episode, stories)
    assert all("testability contract" not in t.instructions for t in bare)

    write_contract(repo)
    contract = reconcile_contract(store, project, stream, repo)
    record_probe_result(store, contract, ok=True, fidelity="local")
    informed = queue_sweep_tasks(store, project, stream, episode, stories)
    assert all("make run" in t.instructions for t in informed)
    assert all("proven at `local` fidelity" in t.instructions for t in informed)


def test_auto_contract_action_guards_and_orders(tmp_path):
    """Autonomy touches the contract one task at a time: draft when missing or
    broken, probe when drafted-unproven, nothing when verified or in flight."""
    store = MemoryStore()
    repo = spec_repo(tmp_path)
    project = store.put(Project(name="p", spec_repo=str(repo)))
    stream = ensure_testing_workstream(store, project)

    assert auto_contract_action(store, project, stream)[0] == "draft"

    task = queue_draft_task(store, project, stream, backend="codex")
    assert auto_contract_action(store, project, stream)[0] == ""
    assert active_contract_task(store, project, stream).id == task.id
    task.status = TaskStatus.done
    store.put(task)

    write_contract(repo)
    contract = reconcile_contract(store, project, stream, repo)
    assert auto_contract_action(store, project, stream)[0] == "probe"

    probe = queue_probe_task(store, project, stream, contract, backend="codex")
    assert auto_contract_action(store, project, stream)[0] == ""
    probe.status = TaskStatus.done
    store.put(probe)

    record_probe_result(store, contract, ok=False, problems=["boom"])
    assert auto_contract_action(store, project, stream)[0] == "draft"
    record_probe_result(store, get_contract(store, project, stream), ok=True, fidelity="local")
    assert auto_contract_action(store, project, stream) == ("", "contract verified")


def test_contract_context_is_size_capped():
    """A bloated contract cannot flood every sweep's context: the embed is
    truncated with a pointer to the file."""
    from hive.models import TestabilityContract
    from hive._workstreams.testability import EMBED_MAX_CHARS

    contract = TestabilityContract(
        project_id="p", workstream_id="w", content="x" * (EMBED_MAX_CHARS + 100)
    )
    block = contract_context(contract)
    assert len(block) < EMBED_MAX_CHARS + 300
    assert "truncated" in block
    assert contract_context(None) == ""


def test_cli_testability_report_shapes_the_payload():
    """The CLI view keeps only what an operator acts on: per-workstream health,
    contract facts, and the open decisions with their question ids."""
    from hive.cli import testability_report

    detail = {
        "workstreams": [
            {"id": "ws1", "kind": "testing", "repo": "acme/beacon"},
            {"id": "ws2", "kind": "github_issues", "repo": "acme/beacon"},
        ],
        "testability": {
            "ws1": {
                "contract": {
                    "status": "broken",
                    "fidelities": ["local"],
                    "probed_fidelity": "",
                    "probe_problems": ["make run: exit 2"],
                },
                "health": {"state": "broken", "action": "draft"},
                "open_decisions": 1,
            }
        },
        "questions": [
            {
                "id": "q1",
                "workstream_id": "ws1",
                "dedup_key": "testability:ws1:stripe-sandbox",
                "status": "open",
                "text": "May tests exercise Stripe?",
            },
            {"id": "q2", "workstream_id": "ws1", "dedup_key": "", "status": "open", "text": "unrelated"},
        ],
    }
    report = testability_report(detail)
    assert len(report["testability"]) == 1
    row = report["testability"][0]
    assert row["status"] == "broken"
    assert row["probe_problems"] == ["make run: exit 2"]
    assert [d["question_id"] for d in row["decisions"]] == ["q1"]
