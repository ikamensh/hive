"""Hive's structured result contracts per task kind.

The generic machinery (ResultSpec, call_agent, repair loop) lives in
`hive.agents`; this module binds it to hive's task vocabulary: which Pydantic
result model each `TaskKind` demands, and how a validated payload maps onto
hive verdicts and testing outcomes. The worker agent reports what happened;
hive's chief still owns the state transition.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hive.agents import ResultSpec
from hive.models import (
    TaskKind,
    TestReproOutcome,
    TestSweepOutcome,
    TestUxOutcome,
    Verdict,
)


class AgentResultBase(BaseModel):
    task_id: str
    summary: str = ""


class VerifyResult(AgentResultBase):
    outcome: Literal["accept", "reject"]
    acceptance_checked: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    residual_risk: str = ""


class ResolveResult(AgentResultBase):
    outcome: Literal["fixed", "blocked"]
    tests_run: list[str] = Field(default_factory=list)
    branch_pushed: bool = False
    github_comment_posted: bool = False


class ReviewResult(AgentResultBase):
    outcome: Literal["accept", "reject"]
    tests_run: list[str] = Field(default_factory=list)
    changes_pushed: bool = False
    github_comment_posted: bool = False


class TestRefreshResult(AgentResultBase):
    outcome: Literal["done"]
    active_story_count: int = Field(ge=0)
    stories_changed: list[str]
    created_story_keys: list[str]
    updated_story_keys: list[str]
    archived_story_keys: list[str]
    changed_files: list[str]
    commit_sha: str
    questions: list[str]


class SweepFindingResult(BaseModel):
    kind: Literal["bug", "ux_smell"]
    severity: str
    summary: str
    detail: str
    oracle: str
    evidence_blobs: list[str] = Field(default_factory=list)


class TestSweepResult(AgentResultBase):
    outcome: Literal["pass", "findings", "blocked"]
    fidelity: Literal["local", "docker"] = "local"
    findings: list[SweepFindingResult] = Field(default_factory=list)


class TestReproduceResult(AgentResultBase):
    outcome: Literal["confirmed", "not_reproduced"]
    evidence_blobs: list[str] = Field(default_factory=list)


class TestJudgeResult(AgentResultBase):
    outcome: Literal["improvable", "constrained", "disagree"]
    evidence_blobs: list[str] = Field(default_factory=list)


RESULT_SPECS: dict[TaskKind, ResultSpec] = {
    TaskKind.verify: ResultSpec(VerifyResult),
    TaskKind.resolve: ResultSpec(ResolveResult),
    TaskKind.review: ResultSpec(ReviewResult),
    TaskKind.test_refresh: ResultSpec(TestRefreshResult),
    TaskKind.test_sweep: ResultSpec(TestSweepResult),
    TaskKind.test_reproduce: ResultSpec(TestReproduceResult),
    TaskKind.test_judge: ResultSpec(TestJudgeResult),
}


def result_spec_for_task(kind: str | TaskKind) -> ResultSpec | None:
    try:
        task_kind = TaskKind(kind)
    except ValueError:
        return None
    return RESULT_SPECS.get(task_kind)


def verdict_from_structured(kind: str | TaskKind, payload: dict) -> Verdict:
    if not payload:
        return Verdict.none
    outcome = str(payload.get("outcome") or "").lower()
    try:
        task_kind = TaskKind(kind)
    except ValueError:
        return Verdict.none
    if task_kind in (TaskKind.verify, TaskKind.review):
        if outcome == "accept":
            return Verdict.accept
        if outcome == "reject":
            return Verdict.reject
    if task_kind == TaskKind.resolve:
        if outcome == "fixed":
            return Verdict.accept
        if outcome == "blocked":
            return Verdict.reject
    if task_kind == TaskKind.test_refresh:
        return Verdict.accept if outcome == "done" else Verdict.none
    if task_kind == TaskKind.test_sweep:
        if outcome == "pass":
            return Verdict.accept
        if outcome in {"findings", "blocked"}:
            return Verdict.reject
    if task_kind == TaskKind.test_reproduce:
        if outcome == "confirmed":
            return Verdict.accept
        if outcome == "not_reproduced":
            return Verdict.reject
    if task_kind == TaskKind.test_judge:
        if outcome == "improvable":
            return Verdict.accept
        if outcome in {"constrained", "disagree"}:
            return Verdict.reject
    return Verdict.none


def test_sweep_outcome(payload: dict) -> TestSweepOutcome:
    outcome = str(payload.get("outcome") or "").lower()
    if outcome == "pass":
        return TestSweepOutcome.passed
    if outcome == "findings":
        return TestSweepOutcome.findings
    if outcome == "blocked":
        return TestSweepOutcome.blocked
    return TestSweepOutcome.none


def test_repro_outcome(payload: dict) -> TestReproOutcome:
    outcome = str(payload.get("outcome") or "").lower()
    if outcome == "confirmed":
        return TestReproOutcome.confirmed
    if outcome == "not_reproduced":
        return TestReproOutcome.not_reproduced
    return TestReproOutcome.none


def test_ux_outcome(payload: dict) -> TestUxOutcome:
    outcome = str(payload.get("outcome") or "").lower()
    if outcome == "improvable":
        return TestUxOutcome.improvable
    if outcome == "constrained":
        return TestUxOutcome.constrained
    if outcome == "disagree":
        return TestUxOutcome.disagree
    return TestUxOutcome.none
