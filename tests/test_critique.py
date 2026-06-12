"""Spec critique engine with scripted LLMs (no network, no CLI agents)."""

import json

import pytest

from hive.critique import LENSES, CritiqueReport, _extract_json, critique, report_markdown

CRITIC_REPLY = """Here is my analysis.
```json
[{"title": "No latency target", "evidence": "iteration.md says 'fast'",
  "artifact": "acceptance check for 'fast'", "severity": 3,
  "reversibility": "expensive", "question": "What does fast mean?"}]
```"""

ADJUDICATOR_REPLY = """```json
{"verdicts": [
   {"title": "No latency target", "action": "ask", "reason": "unverifiable as written"},
   {"title": "No latency target", "action": "drop", "reason": "duplicate of No latency target"},
   {"title": "No latency target", "action": "flag", "reason": "third copy, guess p95<1s"}],
 "inbox_markdown": "1. What does fast mean? Options: p95<1s (recommended) or best-effort.",
 "flags_markdown": "- assuming p95 < 1s"}
```"""


def test_critique_end_to_end():
    critic_prompts: list[str] = []

    def critic(prompt: str) -> str:
        critic_prompts.append(prompt)
        return CRITIC_REPLY

    adjudicator_prompts: list[str] = []

    def adjudicator(prompt: str) -> str:
        adjudicator_prompts.append(prompt)
        return ADJUDICATOR_REPLY

    report = critique("THE-DIGEST", critic, adjudicator, guess_propensity="often")

    # one critic call per lens, each carrying the digest and its lens instruction
    assert len(critic_prompts) == len(LENSES)
    assert all("THE-DIGEST" in p for p in critic_prompts)
    assert {lens for lens in LENSES if any(lens in p for p in critic_prompts)} == set(LENSES)

    # findings parsed and tagged with their lens
    assert len(report.findings) == len(LENSES)
    assert sorted(f.lens for f in report.findings) == sorted(LENSES)
    assert report.findings[0].severity == 3

    # adjudicator saw the findings, the digest, and the propensity
    [adj_prompt] = adjudicator_prompts
    assert "No latency target" in adj_prompt and "THE-DIGEST" in adj_prompt
    assert "often" in adj_prompt

    assert [v.action for v in report.verdicts] == ["ask", "drop", "flag"]
    assert "p95<1s" in report.inbox_markdown
    assert report.prompt_versions.keys() == {"critic", "adjudicator"}

    md = report_markdown(report)
    assert "**ask**" in md and "lens=" in md and "p95<1s" in md


def test_no_findings_skips_adjudicator():
    def empty_critic(prompt: str) -> str:
        return "```json\n[]\n```"

    def boom(prompt: str) -> str:
        raise AssertionError("adjudicator must not run")

    report = critique("d", empty_critic, boom)
    assert report.findings == [] and report.verdicts == [] and report.inbox_markdown == ""


def test_extract_json_variants():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    # last fence wins (models sometimes echo examples first)
    assert _extract_json('```json\n[1]\n``` then ```json\n[2]\n```') == [2]
    # bare JSON with chatter around it
    assert _extract_json('Sure! [{"title": "x"}] hope this helps') == [{"title": "x"}]
    with pytest.raises(ValueError):
        _extract_json("no json here at all")


def test_malformed_critic_output_raises():
    def bad_critic(prompt: str) -> str:
        return "```json\n[{broken\n```"

    with pytest.raises(json.JSONDecodeError):
        critique("d", bad_critic, bad_critic)


def test_report_roundtrips_as_model():
    report = CritiqueReport(
        findings=[], verdicts=[], inbox_markdown="", flags_markdown="", prompt_versions={}
    )
    assert CritiqueReport(**json.loads(report.model_dump_json())) == report
