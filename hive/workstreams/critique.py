"""Spec critique: parallel LLM critics + one adjudicator (design: wiki/spec-critique.md).

LLM transport is a callable `prompt -> response text`, so the engine runs
identically under tests (scripted), locally (kodo CLI agents, see
scripts/spec_critique.py), or later as chief tasks. Malformed model
output raises — a critique run is cheap to redo and a clear error beats a
silently empty report.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from pydantic import BaseModel

from hive.llm._parsing import extract_json
from hive.llm.prompts import load as load_prompt

LENSES = {
    "tester": (
        "Unverifiability. For each user story / 'done' criterion in the iteration goal, "
        "write the acceptance check you would run against actual behavior. Report a finding "
        "for every story where you cannot write a concrete check."
    ),
    "builder": (
        "Underspecification. Draft the first 10-60 minute task for each plausible workstream "
        "toward the iteration goal. Report a finding for every decision you would have to "
        "guess, weighted by how expensive it is to reverse."
    ),
    "consistency": (
        "Coherence. Compare the mission, the iteration goal, the wiki pages, and prior user "
        "answers against each other. Report a finding for every genuine contradiction or stale "
        "statement that would mislead a builder."
    ),
}

LLM = Callable[[str], str]


class Finding(BaseModel):
    lens: str = ""
    model: str = ""  # which critic model proposed it
    title: str
    evidence: str = ""
    artifact: str = ""
    severity: int = 2
    reversibility: str = "expensive"
    question: str = ""


class Verdict(BaseModel):
    title: str
    action: str  # ask | flag | drop
    reason: str = ""


class CritiqueReport(BaseModel):
    findings: list[Finding]
    verdicts: list[Verdict]
    inbox_markdown: str  # one batched question for the human inbox
    flags_markdown: str  # guess-and-flag assumptions
    prompt_versions: dict[str, str]


def run_critics(digest: str, llms: dict[str, LLM]) -> list[Finding]:
    """Every model critiques through every lens — diversity is cheap, the
    adjudicator dedupes."""
    template, _version = load_prompt("critic")

    def one(model: str, lens: str) -> list[Finding]:
        prompt = template.replace("<<LENS>>", f"{lens} — {LENSES[lens]}").replace(
            "<<DIGEST>>", digest
        )
        raw = extract_json(llms[model](prompt))
        return [Finding(**{**f, "lens": lens, "model": model}) for f in raw]

    jobs = [(model, lens) for model in llms for lens in LENSES]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = pool.map(lambda job: one(*job), jobs)
    return [f for batch in results for f in batch]


def run_adjudicator(
    digest: str,
    findings: list[Finding],
    llm: LLM,
    guess_propensity: str,
    max_questions: int,
) -> tuple[list[Verdict], str, str]:
    template, _version = load_prompt("adjudicator")
    prompt = (
        template.replace("<<PROPENSITY>>", guess_propensity)
        .replace("<<MAX_QUESTIONS>>", str(max_questions))
        .replace("<<FINDINGS>>", json.dumps([f.model_dump() for f in findings], indent=1))
        .replace("<<DIGEST>>", digest)
    )
    raw = extract_json(llm(prompt))
    verdicts = [Verdict(**v) for v in raw["verdicts"]]
    return verdicts, raw.get("inbox_markdown", ""), raw.get("flags_markdown", "")


def critique(
    digest: str,
    critic_llms: dict[str, LLM],
    adjudicator_llm: LLM,
    guess_propensity: str = "sometimes",
    max_questions: int = 7,
) -> CritiqueReport:
    findings = run_critics(digest, critic_llms)
    if findings:
        verdicts, inbox, flags = run_adjudicator(
            digest, findings, adjudicator_llm, guess_propensity, max_questions
        )
    else:
        verdicts, inbox, flags = [], "", ""
    return CritiqueReport(
        findings=findings,
        verdicts=verdicts,
        inbox_markdown=inbox,
        flags_markdown=flags,
        prompt_versions={name: load_prompt(name)[1] for name in ("critic", "adjudicator")},
    )


def report_markdown(report: CritiqueReport) -> str:
    """Human-readable report: verdict table, inbox question, flags, raw findings."""
    by_title = {f.title: f for f in report.findings}
    lines = ["# Spec critique report", "", "## Verdicts", ""]
    for v in report.verdicts:
        f = by_title.get(v.title)
        meta = f" (lens={f.lens}@{f.model}, sev={f.severity}, {f.reversibility})" if f else ""
        lines.append(f"- **{v.action}** — {v.title}{meta}: {v.reason}")
    if report.inbox_markdown:
        lines += ["", "## Batched inbox question", "", report.inbox_markdown]
    if report.flags_markdown:
        lines += ["", "## Guess-and-flag assumptions", "", report.flags_markdown]
    lines += ["", "## Raw findings", ""]
    for f in report.findings:
        lines += [
            f"### [{f.lens} @ {f.model}] {f.title}",
            f"- evidence: {f.evidence}",
            f"- artifact: {f.artifact}",
            f"- severity {f.severity}, {f.reversibility} to reverse",
            f"- question: {f.question}",
            "",
        ]
    return "\n".join(lines)
