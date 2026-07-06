"""AI second opinion on the human-todo board (the G23/G24 backstop).

The deterministic layer already dedups filings by key and closes todos whose
resolution predicate flips. What it cannot see: differently-worded todos for
one root cause filed without matching keys, and predicate-less todos whose
condition the store facts show is already gone (the live audit found 6 such
zombies among 16 open todos). A cheap LLM pass reviews the open board against
fleet facts and proposes verdicts.

Mandate is close-only and code-enforced: the model may mark a todo a
*duplicate* of another open todo or *stale* (condition provably gone). It can
never act on the human's behalf beyond closing with an audit note — no
answering questions, no config changes — and a todo that carries a resolution
predicate is left to the deterministic sweep. When unsure, keep.

LLM transport is an injectable `prompt -> text` callable (tests script it;
production builds one from the configured adapter).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from hive.llm import extract_json
from hive.models import (
    DEFAULT_WORKSPACE_ID,
    HumanTask,
    HumanTaskStatus,
    Machine,
    Project,
    Resource,
    Runner,
    Task,
    TaskStatus,
)
from hive._control.escalation import close_todo

log = logging.getLogger("hive._control.todo_triage")

Transport = Callable[[str], str]

PROMPT = """You review the operator-todo board of hive, an autonomous software system.
A deterministic sweep already closes todos whose recorded resolution condition flipped;
you judge only what it cannot:

- duplicate: two open todos describe the same root cause in different words
  (e.g. "Fix cursor login on vm" and "Register cursor plan" both mean the cursor
  subscription is broken). Point `of` at the todo that should stay open.
- stale: the fleet facts below prove the todo's condition is already gone
  (its task finished, its machine reconnected, the project moved on). Cite the
  fact in `reason`.
- keep: the default for everything else. When unsure, keep. Never judge whether
  the human *should* do something — only whether the condition still exists.

Open todos:
{todos}

Fleet facts (authoritative, current):
{facts}

Answer JSON only:
{{"decisions": [{{"todo_id": "...", "verdict": "keep|duplicate|stale", "of": "", "reason": "..."}}]}}
"""


@dataclass
class TriageDecision:
    todo_id: str
    verdict: str  # "duplicate" | "stale"
    reason: str


def _age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _todo_lines(todos: list[HumanTask]) -> str:
    now = time.time()
    lines = []
    for t in todos:
        auto = "auto-closes on its own" if t.resolution else "no auto-close condition"
        scope = f"project {t.project_id}" if t.project_id else "org-wide"
        lines.append(
            f"- {t.id} [{t.kind}, {scope}, filed {_age(now - t.created_at)} ago, {auto}] "
            f"{t.title}\n  {t.instructions[:300].replace(chr(10), ' ')}"
        )
    return "\n".join(lines)


def _fact_lines(store, workspace_id: str) -> str:
    now = time.time()
    runner_names = {
        r.id: r.name for r in store.list(Runner, workspace_id=workspace_id)
    }
    lines = ["Machines:"]
    for m in store.list(Machine, workspace_id=workspace_id):
        silence = now - m.last_seen
        state = "online" if silence < 120 else f"silent for {_age(silence)}"
        lines.append(f"- {m.name} ({m.device_kind}): {state}")
    lines.append("Agent resources (runner/backend):")
    for res in store.list(Resource, workspace_id=workspace_id):
        name = runner_names.get(res.runner_id, res.runner_id)
        cooldown = ", cooling down" if res.cooldown_until > now else ""
        enabled = "" if res.enabled else ", disabled by operator"
        lines.append(f"- {name}/{res.backend}: {res.usability_status}{cooldown}{enabled}")
    lines.append("Projects:")
    for p in store.list(Project, workspace_id=workspace_id):
        goal = ", goal complete" if p.goal_complete else ""
        lines.append(f"- {p.name} ({p.id}): state={p.state}{goal}")
    lines.append("Active tasks (everything else has finished):")
    active = [
        t
        for t in store.list(Task, workspace_id=workspace_id)
        if t.status in (TaskStatus.pending, TaskStatus.running)
    ]
    for t in active:
        lines.append(f"- {t.id}: {t.kind} on {t.backend}, {t.status}")
    if not active:
        lines.append("- none")
    return "\n".join(lines)


def triage_open_todos(
    store, transport: Transport, workspace_id: str = DEFAULT_WORKSPACE_ID
) -> list[TriageDecision]:
    """One review pass over the open board; returns the close decisions it
    applied. Guards live here, not in the prompt: unknown ids and self/closed
    references are ignored, `stale` only touches predicate-less todos, and a
    self-closing todo is never sacrificed as a duplicate of a manual one."""
    todos = store.list(HumanTask, workspace_id=workspace_id, status=HumanTaskStatus.open)
    if len(todos) < 2 and all(t.resolution for t in todos):
        return []
    by_id = {t.id: t for t in todos}
    raw = transport(PROMPT.format(todos=_todo_lines(todos), facts=_fact_lines(store, workspace_id)))
    try:
        decisions = extract_json(raw).get("decisions", [])
    except Exception:
        log.warning("todo triage returned unparseable output: %s", raw[:500])
        return []

    applied: list[TriageDecision] = []
    closed: set[str] = set()
    for d in decisions:
        todo = by_id.get(str(d.get("todo_id", "")))
        verdict = str(d.get("verdict", "keep"))
        reason = str(d.get("reason", "")).strip()
        if todo is None or todo.id in closed or verdict == "keep":
            continue
        if verdict == "duplicate":
            keeper = by_id.get(str(d.get("of", "")))
            if keeper is None or keeper.id == todo.id or keeper.id in closed:
                continue
            if todo.resolution and not keeper.resolution:
                continue  # never trade a self-closing todo for a manual one
            close_todo(store, todo, f"triage: duplicate of {keeper.id} '{keeper.title}'")
            applied.append(TriageDecision(todo.id, "duplicate", f"duplicate of {keeper.id}"))
            closed.add(todo.id)
        elif verdict == "stale" and reason:
            if todo.resolution:
                continue  # predicated todos are the deterministic sweep's call
            close_todo(store, todo, f"triage: {reason}")
            applied.append(TriageDecision(todo.id, "stale", reason))
            closed.add(todo.id)
    return applied
