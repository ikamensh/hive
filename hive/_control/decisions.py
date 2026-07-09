"""Decision ledger projection and re-open workflow.

The durable source of truth is `wiki/decisions.md` in the project's spec home.
This module only projects that markdown into API/UI shape and writes the
small status change needed when an operator re-opens a Hive assumption.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from hive._integrations.specrepo import SpecRepo
from hive.config.settings import Config
from hive.models import (
    Project,
    Question,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)

DECISIONS_PATH = "wiki/decisions.md"
SPEC_READ_TTL_S = 60.0

DEFAULT_MUST_ASK = (
    "who is authorized to perform an action (permission/auth model)",
    "billing, pricing, or seat behavior",
    "data retention and destructive defaults (hard vs soft delete)",
    "public API contracts and breaking changes",
    "legal/compliance wording and notices",
    "security-sensitive defaults (e.g. token handling, account-existence leaks)",
)

ASSUMED_SOURCE_TYPES = {"agent_proposed", "code_derived", "inferred"}
REOPENABLE_STATUSES = {"accepted", "accepted_for_iteration", "resolved"}


class DecisionEntry(BaseModel):
    id: str
    title: str
    source_type: str = ""
    impact: str = ""
    reversibility: str = ""
    status: str = ""
    expires_when: str = ""
    trace: str = ""
    body: str = ""
    can_reopen: bool = False


class DecisionLedger(BaseModel):
    decisions: list[DecisionEntry] = []
    counts: dict[str, int] = {}
    source_types: list[str] = []
    must_ask: list[str] = []
    error: str = ""


@dataclass(frozen=True)
class LedgerRead:
    text: str
    root: Path
    error: str = ""


def parse_decision_ledger(text: str, must_ask: list[str] | None = None, error: str = "") -> DecisionLedger:
    """Parse Hive's markdown decision ledger.

    >>> ledger = parse_decision_ledger('## INV-001 · Expiry\\nsource_type: agent_proposed\\nimpact: medium · reversibility: high · status: accepted_for_iteration\\nexpires_when: user decides\\n\\nUse 7 days.')
    >>> ledger.decisions[0].id, ledger.decisions[0].reversibility, ledger.counts["hive_assumed"]
    ('INV-001', 'high', 1)
    >>> broken = parse_decision_ledger('## INV-002 · Expiry\\nsource_type: agent_proposed\\nimpact: low · reversibility: high · status: accepted\\nexpires_when:\\n\\nUse 7 days.')
    >>> broken.error
    'Hive assumption INV-002 is missing expires_when'
    """
    decisions: list[DecisionEntry] = []
    for block in _decision_blocks(text):
        entry = _parse_block(block)
        if entry:
            decisions.append(entry)
    problems = _provenance_problems(decisions)
    source_types = sorted({d.source_type for d in decisions if d.source_type})
    operator = sum(1 for d in decisions if d.source_type == "user_provided")
    assumed = sum(1 for d in decisions if d.source_type in ASSUMED_SOURCE_TYPES)
    return DecisionLedger(
        decisions=decisions,
        counts={
            "total": len(decisions),
            "operator_specified": operator,
            "hive_assumed": assumed,
            "reopenable": sum(1 for d in decisions if d.can_reopen),
        },
        source_types=source_types,
        must_ask=must_ask or list(DEFAULT_MUST_ASK),
        error=_join_errors(error, problems),
    )


def read_decision_ledger(config: Config, project: Project) -> DecisionLedger:
    read = _read_ledger_text(config, project)
    if read.text:
        return parse_decision_ledger(read.text, must_ask=_read_must_ask(read.root), error=read.error)
    return DecisionLedger(
        decisions=[],
        counts={"total": 0, "operator_specified": 0, "hive_assumed": 0, "reopenable": 0},
        source_types=[],
        must_ask=list(DEFAULT_MUST_ASK),
        error=read.error,
    )


def reopen_decision(
    store,
    supervisor,
    config: Config,
    project: Project,
    decision_id: str,
    workstream_id: str = "",
) -> dict:
    if not project.spec_repo.strip():
        raise ValueError("spec_repo is not set")
    spec = SpecRepo(
        project.spec_repo,
        Path(config.data_dir or "/tmp/hive-data") / "specs",
        config.gh_token,
    )
    spec.sync()
    path = spec.path / DECISIONS_PATH
    if not path.is_file():
        raise ValueError(f"{DECISIONS_PATH} does not exist")
    old_text = path.read_text()
    ledger = parse_decision_ledger(old_text, must_ask=_read_must_ask(spec.path))
    decision = next((d for d in ledger.decisions if d.id == decision_id), None)
    if not decision:
        raise LookupError(f"decision {decision_id} not found")
    if not decision.can_reopen:
        raise PermissionError(f"decision {decision_id} is not a reopenable Hive assumption")

    new_text = _replace_status(old_text, decision_id, "needs_clarification")
    if new_text == old_text:
        raise ValueError(f"could not update status for decision {decision_id}")
    _remove_legacy_cache_marker(spec.path)
    sha = spec.commit_files({DECISIONS_PATH: new_text}, f"Re-open decision {decision_id}")

    parked = _park_dependent_work(store, project, workstream_id, decision_id)
    question = store.put(
        Question(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream_id if parked == [workstream_id] else "",
            text=_reopen_question(decision, sha, parked),
        )
    )
    supervisor.wake(
        project.id,
        f"Decision {decision_id} was re-opened by the operator; question {question.id} now needs an answer.",
    )
    return {
        "decision": next(
            d.model_dump()
            for d in parse_decision_ledger(new_text, must_ask=_read_must_ask(spec.path)).decisions
            if d.id == decision_id
        ),
        "question": question.model_dump(),
        "parked_workstream_ids": parked,
        "commit": sha,
    }


def _decision_blocks(text: str) -> list[str]:
    starts = [m.start() for m in re.finditer(r"(?m)^##\s+", text)]
    if not starts:
        return []
    starts.append(len(text))
    return [text[starts[i]:starts[i + 1]].strip("\n") for i in range(len(starts) - 1)]


def _parse_block(block: str) -> DecisionEntry | None:
    lines = block.splitlines()
    if not lines:
        return None
    heading = lines[0].removeprefix("##").strip()
    decision_id, title = _parse_heading(heading)
    if not decision_id:
        return None

    fields: dict[str, str] = {}
    body_start = 1
    for i, line in enumerate(lines[1:], start=1):
        if not line.strip():
            body_start = i + 1
            break
        found = _parse_meta_line(line)
        if not found:
            body_start = i
            break
        fields.update(found)
    body = "\n".join(lines[body_start:]).strip()
    source_type = fields.get("source_type", "")
    status = fields.get("status", "")
    return DecisionEntry(
        id=decision_id,
        title=title,
        source_type=source_type,
        impact=fields.get("impact", ""),
        reversibility=fields.get("reversibility", ""),
        status=status,
        expires_when=fields.get("expires_when", ""),
        trace=fields.get("trace", ""),
        body=body,
        can_reopen=source_type in ASSUMED_SOURCE_TYPES and status in REOPENABLE_STATUSES,
    )


def _provenance_problems(decisions: list[DecisionEntry]) -> list[str]:
    problems: list[str] = []
    for decision in decisions:
        if decision.source_type not in ASSUMED_SOURCE_TYPES:
            continue
        missing = [
            field
            for field in ("impact", "reversibility", "status", "expires_when")
            if not getattr(decision, field).strip()
        ]
        if missing:
            problems.append(f"Hive assumption {decision.id} is missing {', '.join(missing)}")
    return problems


def _join_errors(error: str, problems: list[str]) -> str:
    parts = [error.strip()] if error.strip() else []
    parts.extend(problems)
    return "; ".join(parts)


def _parse_heading(heading: str) -> tuple[str, str]:
    if " · " in heading:
        decision_id, title = heading.split(" · ", 1)
    elif " - " in heading:
        decision_id, title = heading.split(" - ", 1)
    else:
        parts = heading.split(maxsplit=1)
        decision_id = parts[0] if parts else ""
        title = parts[1] if len(parts) > 1 else ""
    return decision_id.strip(), title.strip()


def _parse_meta_line(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in re.split(r"\s+·\s+", line.strip()):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def _read_ledger_text(config: Config, project: Project) -> LedgerRead:
    if not project.spec_repo.strip():
        return LedgerRead("", Path(), "spec_repo is not set")
    try:
        root = _spec_root_for_read(config, project)
    except Exception as exc:
        return LedgerRead("", Path(), f"{type(exc).__name__}: {str(exc)[:500]}")
    if not root:
        return LedgerRead("", Path(), "spec repo has not been synced on this chief yet")
    path = root / DECISIONS_PATH
    if not path.is_file():
        return LedgerRead("", root, f"{DECISIONS_PATH} is not present")
    return LedgerRead(path.read_text(), root)


def _spec_root_for_read(config: Config, project: Project) -> Path | None:
    repo = project.spec_repo.strip()
    local = Path(repo)
    if _looks_like_spec_worktree(local):
        return local

    spec = SpecRepo(repo, Path(config.data_dir or "/tmp/hive-data") / "specs", config.gh_token)
    can_sync = local.exists() or repo.startswith("file://") or (
        bool(config.gh_token.strip()) and ("github.com/" in repo or repo.startswith("git@github.com:"))
    )
    if can_sync and _cache_stale(spec.path):
        spec.sync()
        _mark_cache_read(spec.path)
    if spec.path.exists():
        return spec.path
    return None


def _looks_like_spec_worktree(path: Path) -> bool:
    return path.is_dir() and (
        (path / ".git").exists()
        or (path / "mission.md").exists()
        or (path / "iteration.md").exists()
        or (path / "wiki").is_dir()
    )


def _cache_stale(path: Path) -> bool:
    marker = _cache_marker(path)
    if not path.exists() or not marker.exists():
        return True
    return time.time() - marker.stat().st_mtime > SPEC_READ_TTL_S


def _mark_cache_read(path: Path) -> None:
    try:
        marker = _cache_marker(path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(time.time()))
    except OSError:
        pass


def _cache_marker(path: Path) -> Path:
    return path / ".git" / "hive-decision-read"


def _remove_legacy_cache_marker(path: Path) -> None:
    try:
        (path / ".hive-decision-read").unlink(missing_ok=True)
    except OSError:
        pass


def _read_must_ask(root: Path) -> list[str]:
    values = list(DEFAULT_MUST_ASK)
    if not root:
        return values
    for rel in ("mission.md", "iteration.md"):
        path = root / rel
        if path.is_file():
            values.extend(_parse_must_ask(path.read_text()))
    return list(dict.fromkeys(v for v in values if v))


def _parse_must_ask(text: str) -> list[str]:
    values: list[str] = []
    active = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(also_)?must_ask\s*:", stripped):
            active = True
            continue
        if active and stripped.startswith("- "):
            values.append(stripped[2:].strip())
            continue
        if active and stripped and not line.startswith((" ", "\t")):
            active = False
    return values


def _replace_status(text: str, decision_id: str, status: str) -> str:
    for match in re.finditer(r"(?m)^##\s+", text):
        start = match.start()
        next_match = re.search(r"(?m)^##\s+", text[match.end():])
        end = match.end() + next_match.start() if next_match else len(text)
        block = text[start:end]
        entry = _parse_block(block.strip("\n"))
        if not entry or entry.id != decision_id:
            continue
        new_block = _replace_status_in_block(block, status)
        return text[:start] + new_block + text[end:]
    return text


def _replace_status_in_block(block: str, status: str) -> str:
    lines = block.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if "status:" not in line:
            continue
        lines[i] = re.sub(r"(status:\s*)[^·\n\r]+", rf"\g<1>{status}", line, count=1)
        return "".join(lines)
    insert_at = 2 if len(lines) > 1 and lines[1].startswith("source_type:") else 1
    newline = "\n" if block.endswith("\n") else ""
    lines.insert(insert_at, f"status: {status}\n")
    return "".join(lines) + newline


def _park_dependent_work(store, project: Project, workstream_id: str, decision_id: str) -> list[str]:
    candidates: list[Workstream]
    if workstream_id:
        ws = store.get(Workstream, workstream_id)
        candidates = [ws] if ws and ws.project_id == project.id else []
    else:
        candidates = [
            ws
            for ws in store.list(Workstream, workspace_id=project.workspace_id, project_id=project.id)
            if ws.source == WorkstreamSource.manual and ws.status == WorkstreamStatus.active
        ]
    parked: list[str] = []
    for ws in candidates:
        if ws.status != WorkstreamStatus.active:
            continue
        ws.status = WorkstreamStatus.parked
        ws.parked_reason = f"decision {decision_id} re-opened"
        store.put(ws)
        parked.append(ws.id)
    return parked


def _reopen_question(decision: DecisionEntry, commit_sha: str, parked: list[str]) -> str:
    parked_text = ", ".join(parked) if parked else "no active dependent workstream was found"
    return "\n\n".join(
        [
            f"## Re-open decision {decision.id}: {decision.title}",
            (
                f"**Context:** The operator re-opened this Hive assumption. "
                f"The ledger entry is now `needs_clarification` in `{DECISIONS_PATH}` "
                f"at `{commit_sha[:8]}`."
            ),
            (
                f"**Decision:** source_type `{decision.source_type}`, impact `{decision.impact or 'unknown'}`, "
                f"reversibility `{decision.reversibility or 'unknown'}`, expires_when "
                f"`{decision.expires_when or 'not recorded'}`."
            ),
            f"**Current assumption:**\n\n{decision.body or '(no body recorded)'}",
            f"**Parked work:** {parked_text}.",
            (
                "**Options:**\n\n"
                "1. Accept the assumption again for this iteration.\n"
                "2. Replace it with the operator's preferred decision.\n"
                "3. Reject it and re-plan the affected work."
            ),
            "**Recommendation:** answer with the corrected decision so Hive can distill it back into the ledger.",
        ]
    )
