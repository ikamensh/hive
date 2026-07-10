"""Per-project testability contract (wiki/testability-contract.md).

The chief owns the deterministic parts: mirror the spec home's
`testability.md` into a TestabilityContract record, turn the draft agent's
open decisions into deduped Questions, chain draft -> probe, and compute the
health verdict with Hive's standing offer. Agents do the exploring and the
proving; the human only answers decision questions.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from hive._control.allowances import resolve_agent
from hive.llm.prompts import load as load_prompt
from hive.models import (
    Project,
    ProjectWorkstream,
    Question,
    QuestionStatus,
    Task,
    TaskKind,
    TaskStatus,
    TestabilityContract,
    TestabilityStatus,
)

CONTRACT_FILE = "testability.md"
FIDELITY_HEADING = re.compile(r"^###\s*(local|docker)\b", re.I | re.M)
DECISION_KEY = re.compile(r"[^a-z0-9._-]+")
# Contracts are meant to be read in one glance; a bigger one needs distillation,
# and embedding it whole into every sweep would tax each task's context.
EMBED_MAX_CHARS = 8_000

CONTRACT_TASK_KINDS = (TaskKind.testability_draft, TaskKind.testability_probe)


def now_s() -> float:
    return time.time()


def contract_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def parse_fidelities(text: str) -> list[str]:
    """Declared run fidelities: the `### local` / `### docker` subsections."""
    return list(dict.fromkeys(m.group(1).lower() for m in FIDELITY_HEADING.finditer(text)))


def load_contract_text(spec_path: Path) -> str:
    path = spec_path / CONTRACT_FILE
    return path.read_text() if path.is_file() else ""


@dataclass(frozen=True)
class DecisionDraft:
    """One choice the draft agent could not make alone, ready to ask a human."""

    key: str
    question: str
    options: tuple[str, ...]
    recommendation: str


@dataclass(frozen=True)
class DraftResultSummary:
    decisions: tuple[DecisionDraft, ...] = ()
    changed_files: tuple[str, ...] = ()
    commit_sha: str = ""
    fidelities: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict | None) -> "DraftResultSummary":
        payload = payload or {}
        decisions = []
        raw = payload.get("decisions")
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            key = DECISION_KEY.sub("-", str(item.get("key") or "").strip().lower()).strip("-")
            question = str(item.get("question") or "").strip()
            if not key or not question:
                continue
            options = tuple(
                str(o).strip() for o in (item.get("options") or []) if str(o).strip()
            )
            decisions.append(
                DecisionDraft(
                    key=key,
                    question=question,
                    options=options,
                    recommendation=str(item.get("recommendation") or "").strip(),
                )
            )
        return cls(
            decisions=tuple(decisions),
            changed_files=tuple(str(f).strip() for f in payload.get("changed_files") or [] if str(f).strip()),
            commit_sha=str(payload.get("commit_sha") or "").strip(),
            fidelities=tuple(str(f).strip().lower() for f in payload.get("fidelities") or [] if str(f).strip()),
        )


def get_contract(store, project: Project, workstream: ProjectWorkstream) -> TestabilityContract | None:
    found = store.list(
        TestabilityContract,
        workspace_id=project.workspace_id,
        project_id=project.id,
        workstream_id=workstream.id,
    )
    return found[0] if found else None


def reconcile_contract(
    store, project: Project, workstream: ProjectWorkstream, spec_path: Path
) -> TestabilityContract:
    """Mirror `testability.md` into the contract record.

    Probe evidence is only kept while it matches the file: an edit moves a
    verified/broken contract back to `draft` (re-prove, don't re-trust).
    """
    text = load_contract_text(spec_path)
    contract = get_contract(store, project, workstream) or TestabilityContract(
        workspace_id=project.workspace_id,
        project_id=project.id,
        workstream_id=workstream.id,
        repo=workstream.repo,
    )
    contract.repo = workstream.repo
    contract.content = text
    contract.baseline = contract_digest(text) if text else ""
    contract.fidelities = parse_fidelities(text)
    if not text:
        contract.status = TestabilityStatus.missing
    elif contract.baseline == contract.probed_baseline and contract.status in (
        TestabilityStatus.verified,
        TestabilityStatus.broken,
    ):
        pass  # probe evidence still speaks for this exact content
    else:
        contract.status = TestabilityStatus.draft
    contract.updated_at = now_s()
    return store.put(contract)


def record_probe_result(
    store,
    contract: TestabilityContract,
    *,
    ok: bool,
    fidelity: str = "",
    problems: list[str] | None = None,
    task_id: str = "",
) -> TestabilityContract:
    def apply(saved: TestabilityContract) -> None:
        saved.status = TestabilityStatus.verified if ok else TestabilityStatus.broken
        saved.probed_baseline = saved.baseline
        saved.probed_fidelity = fidelity if ok else ""
        saved.probe_problems = list(problems or [])
        saved.probe_task_id = task_id
        saved.probed_at = now_s()
        saved.updated_at = now_s()

    return store.update(TestabilityContract, contract.id, apply) or contract


# --- decisions -> questions -------------------------------------------------


def decision_dedup_key(workstream_id: str, decision_key: str) -> str:
    return f"testability:{workstream_id}:{decision_key}"


def is_decision_question(question: Question) -> bool:
    return question.dedup_key.startswith("testability:")


def _decision_text(repo: str, decision: DecisionDraft) -> str:
    parts = [
        f"Hive needs a decision to finish the testability contract for `{repo}`.",
        "",
        f"**{decision.question}**",
    ]
    if decision.options:
        parts += ["", "Options:", *[f"- {option}" for option in decision.options]]
    if decision.recommendation:
        parts += ["", f"Hive recommends: {decision.recommendation}"]
    parts += ["", "Answer here — Hive updates `testability.md` itself and re-proves it."]
    return "\n".join(parts)


def create_decision_questions(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    decisions: tuple[DecisionDraft, ...] | list[DecisionDraft],
) -> list[Question]:
    """File one Question per new decision. A key that was ever asked — open,
    answered, or dismissed — is never re-asked; the human's word stands."""
    asked = {
        q.dedup_key
        for q in store.list(
            Question,
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
        )
        if q.dedup_key
    }
    created: list[Question] = []
    for decision in decisions:
        key = decision_dedup_key(workstream.id, decision.key)
        if key in asked:
            continue
        asked.add(key)
        created.append(
            store.put(
                Question(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    workstream_id=workstream.id,
                    dedup_key=key,
                    text=_decision_text(workstream.repo, decision),
                )
            )
        )
    return created


def decision_questions(store, project: Project, workstream: ProjectWorkstream) -> list[Question]:
    return [
        q
        for q in store.list(
            Question,
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
        )
        if is_decision_question(q)
    ]


def settled_decision_lines(questions: list[Question]) -> list[str]:
    """Answered decisions, rendered for a draft task's instructions."""
    lines = []
    for q in questions:
        if q.status != QuestionStatus.answered or not q.answer.strip():
            continue
        asked = q.text.strip().splitlines()
        headline = next((line.strip("* ") for line in asked if line.startswith("**")), q.dedup_key)
        lines.append(f"- {headline}: {q.answer.strip()}")
    return lines


# --- tasks -------------------------------------------------------------------


def active_contract_task(store, project: Project, workstream: ProjectWorkstream) -> Task | None:
    for kind in CONTRACT_TASK_KINDS:
        for task in store.list(
            Task,
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
            kind=kind,
        ):
            if task.status in (TaskStatus.pending, TaskStatus.running):
                return task
    return None


def draft_task_instructions(
    project: Project,
    workstream: ProjectWorkstream,
    contract: TestabilityContract | None,
    settled: list[str],
    probe_problems: list[str],
) -> tuple[str, dict[str, str]]:
    prompt, version = load_prompt("testability_draft")
    parts = [f"Project: {project.name}"]
    if contract and contract.content:
        parts += ["", f"Current `{CONTRACT_FILE}` (repair/extend rather than rewrite):", contract.content]
    else:
        parts += ["", f"There is no `{CONTRACT_FILE}` yet — write the first one."]
    if probe_problems:
        parts += ["", "The last probe failed on:", *[f"- {p}" for p in probe_problems]]
    if settled:
        parts += ["", "Decisions the human already settled (fold these in, do not re-ask):", *settled]
    return "\n".join([*parts, "", prompt]), {"testability_draft": version}


def probe_task_instructions(contract: TestabilityContract) -> tuple[str, dict[str, str]]:
    prompt, version = load_prompt("testability_probe")
    header = "\n".join(
        [
            f"The testability contract (`{CONTRACT_FILE}`, also in the repo):",
            "",
            contract.content,
        ]
    )
    return f"{header}\n\n{prompt}", {"testability_probe": version}


def _probe_capabilities(contract: TestabilityContract) -> list[str]:
    # Docker is only a hard requirement when the contract offers no other way
    # to stand the app up; otherwise the probe achieves the best it can.
    return ["docker"] if contract.fidelities == ["docker"] else []


def queue_draft_task(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    *,
    backend: str,
    model: str = "",
    probe_problems: list[str] | None = None,
) -> Task:
    contract = get_contract(store, project, workstream)
    settled = settled_decision_lines(decision_questions(store, project, workstream))
    instructions, versions = draft_task_instructions(
        project, workstream, contract, settled, list(probe_problems or [])
    )
    backend, model = resolve_agent(project.agent_grants, backend, model)
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
            work_item_id=workstream.id,
            repo=workstream.repo,
            kind=TaskKind.testability_draft,
            instructions=instructions,
            backend=backend,
            model=model,
            prompt_versions=versions,
        )
    )


def queue_probe_task(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    contract: TestabilityContract,
    *,
    backend: str,
    model: str = "",
) -> Task:
    if not contract.content:
        raise ValueError("cannot probe a missing testability contract")
    instructions, versions = probe_task_instructions(contract)
    backend, model = resolve_agent(project.agent_grants, backend, model)
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
            work_item_id=contract.id,
            repo=workstream.repo,
            kind=TaskKind.testability_probe,
            instructions=instructions,
            backend=backend,
            model=model,
            required_capabilities=_probe_capabilities(contract),
            prompt_versions=versions,
        )
    )


def contract_context(contract: TestabilityContract | None) -> str:
    """The contract block sweep/confirm/probe instructions embed, size-capped."""
    if not contract or not contract.content:
        return ""
    content = contract.content
    if len(content) > EMBED_MAX_CHARS:
        content = content[:EMBED_MAX_CHARS] + "\n(... contract truncated — read testability.md in the repo)"
    proven = (
        f"proven at `{contract.probed_fidelity}` fidelity"
        if contract.status == TestabilityStatus.verified
        else f"unproven ({contract.status})"
    )
    return "\n".join(
        [
            f"How to stand the app up — the testability contract ({proven}):",
            "",
            content,
        ]
    )


# --- health ------------------------------------------------------------------


@dataclass(frozen=True)
class TestabilityHealth:
    """Deterministic verdict on the contract, with Hive's standing offer.

    Shared by web/CLI/API like `story_health`: `state` drives styling,
    `action` is the machine hint (draft | probe | decide | ""), and
    `summary`/`offer` are the human words.
    """

    state: str
    summary: str
    offer: str
    action: str

    def as_dict(self) -> dict:
        return {"state": self.state, "summary": self.summary, "offer": self.offer, "action": self.action}


def testability_health(
    contract: TestabilityContract | None,
    *,
    open_decisions: int = 0,
    draft_active: bool = False,
    probe_active: bool = False,
) -> TestabilityHealth:
    if draft_active:
        return TestabilityHealth(
            "drafting", "Hive is exploring the repo and drafting the testability contract.", "", ""
        )
    if probe_active:
        return TestabilityHealth(
            "probing", "Hive is proving the testability contract on a runner right now.", "", ""
        )
    if contract is None or contract.status == TestabilityStatus.missing:
        return TestabilityHealth(
            "missing",
            "No testability contract yet — sweeps would have to improvise how to run the app.",
            "Hive can explore the repo and draft `testability.md` autonomously, then prove it on a runner.",
            "draft",
        )
    if open_decisions:
        return TestabilityHealth(
            "decisions",
            f"{open_decisions} testability decision(s) need you — everything else is Hive's job.",
            "Answer them in the questions inbox; Hive folds the answers into the contract and re-proves it.",
            "decide",
        )
    if contract.status == TestabilityStatus.broken:
        problems = "; ".join(contract.probe_problems[:3]) or "unrecorded problem"
        return TestabilityHealth(
            "broken",
            f"The contract failed its last probe ({problems}).",
            "Hive can repair the contract from the probe report and try again.",
            "draft",
        )
    if contract.status == TestabilityStatus.draft:
        return TestabilityHealth(
            "draft",
            "The contract is drafted but unproven against its current content.",
            "Hive can prove it by standing the app up on a runner.",
            "probe",
        )
    age_days = (now_s() - contract.probed_at) / 86400 if contract.probed_at else 0.0
    return TestabilityHealth(
        "verified",
        f"Contract proven at `{contract.probed_fidelity or 'local'}` fidelity {age_days:.1f}d ago.",
        "",
        "",
    )


def auto_contract_action(store, project: Project, workstream: ProjectWorkstream) -> tuple[str, str]:
    """What autonomy should do about the contract right now: ("draft"|"probe", why)
    or ("", why-not). Decisions never gate autonomy — only a human can answer
    them, and drafting/probing proceed regardless."""
    if active_contract_task(store, project, workstream):
        return "", "a testability task is already in flight"
    contract = get_contract(store, project, workstream)
    health = testability_health(contract)
    if health.action in ("draft", "probe"):
        return health.action, health.summary
    if contract and contract.status == TestabilityStatus.verified:
        return "", "contract verified"
    return "", health.summary
