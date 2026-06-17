"""First-class testing workstream support.

The control plane owns the deterministic parts: mirror `acceptance/*.md` into
Story records, snapshot a TestEpisode, queue one sweep task per selected story,
denoise suspected findings with independent confirmation, and file confirmed
findings as GitHub issues. Agents do the exploratory work; this module parses
their required markers and keeps the resulting state auditable.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx

from hive.github_repos import _GH_HEADERS, parse_repo_ref
from hive.llm.parsing import extract_json
from hive.models import (
    Finding,
    FindingKind,
    FindingStatus,
    Project,
    ProjectWorkstream,
    ProjectWorkstreamKind,
    ProjectWorkstreamStatus,
    Question,
    Story,
    StoryCentrality,
    StoryFidelity,
    StoryOracleStatus,
    StoryStatus,
    Task,
    TaskKind,
    TaskStatus,
    TestEpisode,
    TestEpisodeScope,
    TestEpisodeStatus,
)
from hive.prompts import load as load_prompt
from hive.specrepo import digest_dir

log = logging.getLogger("hive.testing")

DEFAULT_TEST_BACKEND = "codex"
DEFAULT_EPISODE_SIZE = 5
ARTIFACT_DIR = ".hive/artifacts"
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9._/-]+$")
STORY_HEADING = re.compile(r"^\s*#\s*story:\s*([A-Za-z0-9._-]+)(?:\s*\[([^\]]+)\])?", re.I | re.M)
SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)
USER_IMPACT_WORDS = {
    "block",
    "broken",
    "cannot",
    "can't",
    "crash",
    "error",
    "fail",
    "incorrect",
    "lose",
    "missing",
    "prevent",
    "unable",
    "wrong",
}
WEAK_NITPICK_WORDS = {
    "alignment",
    "color",
    "copy",
    "cosmetic",
    "font",
    "margin",
    "nit",
    "polish",
    "spacing",
    "typo",
}


@dataclass
class StoryDraft:
    key: str
    title: str
    intent: str
    acceptance: str
    spec_ref: str
    tags: list[str]
    order: int


@dataclass(frozen=True)
class RefreshResultSummary:
    active_story_count: int | None = None
    stories_changed: tuple[str, ...] = ()
    created_story_keys: tuple[str, ...] = ()
    updated_story_keys: tuple[str, ...] = ()
    archived_story_keys: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    commit_sha: str = ""
    questions: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict | None) -> "RefreshResultSummary":
        payload = payload or {}
        count = payload.get("active_story_count")
        if isinstance(count, bool) or not isinstance(count, int):
            count = None
        return cls(
            active_story_count=count,
            stories_changed=_strings(payload.get("stories_changed")),
            created_story_keys=_strings(payload.get("created_story_keys")),
            updated_story_keys=_strings(payload.get("updated_story_keys")),
            archived_story_keys=_strings(payload.get("archived_story_keys")),
            changed_files=_strings(payload.get("changed_files")),
            commit_sha=str(payload.get("commit_sha") or "").strip(),
            questions=_strings(payload.get("questions")),
        )

    @property
    def draft_keys(self) -> set[str]:
        return set(self.created_story_keys) | set(self.updated_story_keys)


@dataclass(frozen=True)
class ReconcileReport:
    notes: list[str]
    baseline: str
    active_before: int
    active_after: int
    added_keys: list[str]
    updated_keys: list[str]
    archived_keys: list[str]


@dataclass(frozen=True)
class RefreshFinalization:
    summary: RefreshResultSummary
    episode: TestEpisode | None
    report: ReconcileReport
    tasks: list[Task]
    questions: list[Question]
    blocked_reason: str = ""


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def now_s() -> float:
    return time.time()


def baseline_digest(spec_path: Path) -> str:
    return hashlib.sha256(digest_dir(spec_path).encode()).hexdigest()[:16]


def artifact_key(workspace_id: str, task_id: str, name: str) -> str:
    return f"workspaces/{workspace_id}/artifacts/{task_id}/{safe_artifact_name(name)}"


def safe_artifact_name(name: str) -> str:
    cleaned = name.strip().replace("\\", "/").lstrip("/")
    if not cleaned or ".." in cleaned.split("/") or not ARTIFACT_NAME.fullmatch(cleaned):
        raise ValueError(f"unsafe artifact name: {name!r}")
    return cleaned


def ensure_testing_workstream(store, project: Project, repo: str | None = None) -> ProjectWorkstream:
    repo = (repo or project.spec_repo).strip()
    if not repo:
        raise ValueError("repo is required for testing")
    existing = store.list(
        ProjectWorkstream,
        workspace_id=project.workspace_id,
        project_id=project.id,
        kind=ProjectWorkstreamKind.testing,
        repo=repo,
    )
    if existing:
        return existing[0]
    return store.put(
        ProjectWorkstream(
            workspace_id=project.workspace_id,
            project_id=project.id,
            kind=ProjectWorkstreamKind.testing,
            title=f"Testing: {repo.rsplit('/', 1)[-1].removesuffix('.git')}",
            repo=repo,
            source_ref={"acceptance_dir": "acceptance"},
            status=ProjectWorkstreamStatus.idle,
        )
    )


def _section(text: str, name: str) -> str:
    headings = list(SECTION_HEADING.finditer(text))
    for i, match in enumerate(headings):
        if match.group(1).strip().lower() != name.lower():
            continue
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        return text[start:end].strip()
    return ""


def _intent(text: str, heading: re.Match) -> str:
    end = SECTION_HEADING.search(text, heading.end())
    body = text[heading.end() : end.start() if end else len(text)]
    return "\n".join(line.strip() for line in body.splitlines() if line.strip()).strip()


def _title_from_key(key: str) -> str:
    return key.replace("_", "-").replace("-", " ").strip().capitalize()


def _tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [tag.strip().lower() for tag in re.split(r"[,\s]+", raw) if tag.strip()]


def parse_story_file(path: Path, root: Path, order: int) -> StoryDraft | None:
    text = path.read_text()
    match = STORY_HEADING.search(text)
    key = match.group(1).strip() if match else path.stem
    tags = _tags(match.group(2) if match else "")
    intent = _intent(text, match) if match else ""
    if not intent:
        body = text.split("\n", 1)[1] if "\n" in text else text
        intent = "\n".join(line.strip() for line in body.splitlines() if line.strip())[:800]
    rules = _section(text, "Rules")
    examples = _section(text, "Examples")
    acceptance_parts = []
    if rules:
        acceptance_parts.append(f"## Rules\n{rules}")
    if examples:
        acceptance_parts.append(f"## Examples\n{examples}")
    acceptance = "\n\n".join(acceptance_parts).strip()
    if not acceptance:
        acceptance = text.strip()
    if not key or not acceptance:
        return None
    return StoryDraft(
        key=key,
        title=_title_from_key(key),
        intent=intent,
        acceptance=acceptance,
        spec_ref=str(path.relative_to(root)),
        tags=tags,
        order=order,
    )


def load_story_drafts(spec_path: Path) -> list[StoryDraft]:
    acceptance = spec_path / "acceptance"
    if not acceptance.is_dir():
        return []
    drafts = []
    for order, path in enumerate(sorted(acceptance.glob("*.md")), start=1):
        draft = parse_story_file(path, spec_path, order)
        if draft:
            drafts.append(draft)
    return drafts


def _centrality(tags: list[str], existing: Story | None = None) -> StoryCentrality:
    if existing and existing.centrality_locked:
        return existing.centrality
    if "core" in tags:
        return StoryCentrality.core
    if "minor" in tags:
        return StoryCentrality.minor
    return StoryCentrality.major


def _status_after_reconcile(existing: Story | None, content_changed: bool, baseline: str) -> StoryStatus:
    if existing is None:
        return StoryStatus.untested
    if existing.status == StoryStatus.archived:
        return StoryStatus.untested
    if not existing.last_tested_at:
        return StoryStatus.untested
    if content_changed or existing.last_tested_baseline != baseline:
        return StoryStatus.stale
    return existing.status


def _active_story_count(stories: Iterable[Story]) -> int:
    return sum(1 for story in stories if story.status != StoryStatus.archived)


def _mark_draft(story: Story, reason: str) -> None:
    story.oracle_status = StoryOracleStatus.draft
    story.oracle_status_reason = reason
    story.blessed = False
    story.blessed_at = 0.0


def reconcile_story_backlog(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    spec_path: Path,
    *,
    refresh_summary: RefreshResultSummary | None = None,
) -> ReconcileReport:
    """Mirror `acceptance/*.md` into Story rows and report the exact delta."""
    refresh_summary = refresh_summary or RefreshResultSummary()
    baseline = baseline_digest(spec_path)
    drafts = load_story_drafts(spec_path)
    existing = {
        s.key: s
        for s in store.list(
            Story,
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
        )
    }
    active_before = _active_story_count(existing.values())
    seen = set()
    notes: list[str] = []
    added_keys: list[str] = []
    updated_keys: list[str] = []
    archived_keys: list[str] = []
    for draft in drafts:
        seen.add(draft.key)
        story = existing.get(draft.key)
        content_changed = (
            story is None
            or story.intent != draft.intent
            or story.acceptance != draft.acceptance
            or story.spec_ref != draft.spec_ref
            or story.tags != draft.tags
        )
        if story is None:
            story = Story(
                workspace_id=project.workspace_id,
                project_id=project.id,
                workstream_id=workstream.id,
                repo=workstream.repo,
                key=draft.key,
                title=draft.title,
                intent=draft.intent,
                acceptance=draft.acceptance,
                spec_ref=draft.spec_ref,
                tags=draft.tags,
                centrality=_centrality(draft.tags),
                spec_baseline=baseline,
                order=draft.order,
            )
            if draft.key in refresh_summary.draft_keys:
                _mark_draft(story, "created by Hive's testing refresh from spec intention")
            store.put(story)
            notes.append(f"added story {draft.key}")
            added_keys.append(draft.key)
            continue
        story.repo = workstream.repo
        story.title = draft.title
        story.intent = draft.intent
        story.acceptance = draft.acceptance
        story.spec_ref = draft.spec_ref
        story.tags = draft.tags
        story.centrality = _centrality(draft.tags, story)
        story.spec_baseline = baseline
        story.status = _status_after_reconcile(story, content_changed, baseline)
        if draft.key in refresh_summary.draft_keys:
            reason = "updated by Hive's testing refresh from spec intention" if content_changed else story.oracle_status_reason
            _mark_draft(story, reason)
        story.order = draft.order
        story.updated_at = now_s()
        store.put(story)
        if content_changed:
            notes.append(f"updated story {draft.key}")
            updated_keys.append(draft.key)
    for key, story in existing.items():
        if key in seen or story.status == StoryStatus.archived:
            continue
        story.status = StoryStatus.archived
        story.updated_at = now_s()
        store.put(story)
        notes.append(f"archived story {key}")
        archived_keys.append(key)
    active_after = _active_story_count(
        store.list(
            Story,
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
        )
    )
    return ReconcileReport(
        notes=notes,
        baseline=baseline,
        active_before=active_before,
        active_after=active_after,
        added_keys=added_keys,
        updated_keys=updated_keys,
        archived_keys=archived_keys,
    )


def reconcile_stories(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    spec_path: Path,
) -> tuple[list[str], str]:
    """Compatibility wrapper for callers that only need notes + baseline."""
    report = reconcile_story_backlog(store, project, workstream, spec_path)
    return report.notes, report.baseline


def _priority_score(story: Story, now_epoch: float) -> tuple[int, float, int]:
    score = 0
    if story.spec_baseline and story.last_tested_baseline != story.spec_baseline:
        score += 1000
    if not story.last_tested_at:
        score += 800
    if story.status == StoryStatus.failing:
        score += 600
    score += {
        StoryCentrality.core: 300,
        StoryCentrality.major: 150,
        StoryCentrality.minor: 25,
    }.get(story.centrality, 100)
    age_days = (now_epoch - story.last_tested_at) / 86400 if story.last_tested_at else 9999
    return (score, age_days, -story.order)


def select_episode_stories(
    stories: Iterable[Story],
    scope: TestEpisodeScope,
    selected_keys: list[str],
    max_stories: int,
) -> list[Story]:
    active = [s for s in stories if s.status != StoryStatus.archived]
    if scope == TestEpisodeScope.selected:
        allowed = set(selected_keys)
        return sorted((s for s in active if s.key in allowed), key=lambda s: s.order)
    if scope == TestEpisodeScope.full:
        return sorted(active, key=lambda s: s.order)
    limit = max_stories if max_stories > 0 else DEFAULT_EPISODE_SIZE
    ranked = sorted(active, key=lambda s: _priority_score(s, now_s()), reverse=True)
    return sorted(ranked[:limit], key=lambda s: s.order)


def _required_capabilities(story: Story, workstream: ProjectWorkstream) -> list[str]:
    required = set()
    tags = set(story.tags)
    if "ui" in tags or "browser" in tags:
        required.add("browser")
    fidelity = str(workstream.config.get("fidelity", "")).lower()
    if "docker" in tags or fidelity == "docker":
        required.add("docker")
    return sorted(required)


def _story_header(story: Story) -> str:
    return "\n".join(
        [
            f"Story key: {story.key}",
            f"Spec reference: {story.spec_ref}",
            f"Intent:\n{story.intent or '(none recorded)'}",
            "",
            f"Acceptance:\n{story.acceptance}",
            "",
            f"Save evidence under `{ARTIFACT_DIR}/` when possible. Hive uploads those files.",
        ]
    )


def refresh_instructions(project: Project) -> tuple[str, dict[str, str]]:
    prompt, version = load_prompt("test_refresh")
    header = (
        f"Project: {project.name}\n"
        "Refresh acceptance stories from mission.md, iteration.md, wiki/, and input-log/ only. "
        "Do not edit product code.\n"
    )
    return f"{header}\n{prompt}", {"test_refresh": version}


def story_task_instructions(story: Story, prompt_name: str) -> tuple[str, dict[str, str]]:
    prompt, version = load_prompt(prompt_name)
    return f"{_story_header(story)}\n\n{prompt}", {prompt_name: version}


def queue_refresh_task(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    *,
    episode: TestEpisode | None = None,
    backend: str = DEFAULT_TEST_BACKEND,
    model: str = "",
) -> Task:
    instructions, versions = refresh_instructions(project)
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
            work_item_id=workstream.id,
            run_id=episode.id if episode else "",
            repo=workstream.repo,
            kind=TaskKind.test_refresh,
            instructions=instructions,
            backend=backend,
            model=model,
            prompt_versions=versions,
        )
    )


def queue_sweep_tasks(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    episode: TestEpisode,
    stories: list[Story],
) -> list[Task]:
    queued = []
    for story in stories:
        instructions, versions = story_task_instructions(story, "test_sweep")
        queued.append(
            store.put(
                Task(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    workstream_id=story.id,
                    work_item_id=story.id,
                    run_id=episode.id,
                    repo=story.repo or workstream.repo,
                    kind=TaskKind.test_sweep,
                    instructions=instructions,
                    backend=episode.sweep_backend,
                    model=episode.sweep_model,
                    required_capabilities=_required_capabilities(story, workstream),
                    prompt_versions=versions,
                )
            )
        )
    return queued


def queue_confirm_task(
    store,
    project: Project,
    story: Story,
    finding: Finding,
    episode: TestEpisode,
) -> Task:
    prompt = "test_reproduce" if finding.kind == FindingKind.bug else "test_judge"
    instructions, versions = story_task_instructions(story, prompt)
    workstream = store.get(ProjectWorkstream, story.workstream_id)
    required_capabilities = _required_capabilities(story, workstream) if workstream else []
    finding_context = "\n".join(
        [
            "",
            "Finding to confirm:",
            f"Kind: {finding.kind}",
            f"Severity: {finding.severity}",
            f"Summary: {finding.summary}",
            f"Oracle: {finding.oracle}",
            f"Details:\n{finding.detail}",
        ]
    )
    task = store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=finding.id,
            work_item_id=story.id,
            run_id=episode.id,
            repo=story.repo or finding.repo,
            kind=TaskKind.test_reproduce if finding.kind == FindingKind.bug else TaskKind.test_judge,
            instructions=f"{instructions}\n{finding_context}",
            backend=episode.confirm_backend,
            model=episode.confirm_model,
            required_capabilities=required_capabilities,
            prompt_versions=versions,
        )
    )
    finding.confirm_task_id = task.id
    finding.updated_at = now_s()
    store.put(finding)
    return task


def start_episode(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    *,
    scope: TestEpisodeScope = TestEpisodeScope.priority,
    selected_story_keys: list[str] | None = None,
    max_stories: int = 0,
    refresh_backend: str = DEFAULT_TEST_BACKEND,
    refresh_model: str = "",
    sweep_backend: str = DEFAULT_TEST_BACKEND,
    sweep_model: str = "",
    confirm_backend: str = DEFAULT_TEST_BACKEND,
    confirm_model: str = "",
) -> tuple[TestEpisode, Task]:
    episode = store.put(
        TestEpisode(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
            repo=workstream.repo,
            scope=scope,
            selected_story_keys=selected_story_keys or [],
            max_stories=max_stories,
            refresh_backend=refresh_backend,
            refresh_model=refresh_model,
            sweep_backend=sweep_backend,
            sweep_model=sweep_model,
            confirm_backend=confirm_backend,
            confirm_model=confirm_model,
            status=TestEpisodeStatus.refreshing,
            started_at=now_s(),
        )
    )
    return episode, queue_refresh_task(
        store,
        project,
        workstream,
        episode=episode,
        backend=refresh_backend,
        model=refresh_model,
    )


def _allowed_refresh_path(path: str) -> bool:
    rel = path.strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.startswith("acceptance/") or rel.startswith("input-log/")


def refresh_result_problem(summary: RefreshResultSummary, report: ReconcileReport) -> str:
    if summary.active_story_count is not None and summary.active_story_count != report.active_after:
        return (
            "test refresh structured result reported "
            f"{summary.active_story_count} active stories, but Hive reconciled {report.active_after}"
        )
    invalid_paths = [path for path in summary.changed_files if not _allowed_refresh_path(path)]
    if invalid_paths:
        return f"test refresh reported out-of-scope file changes: {', '.join(invalid_paths[:5])}"
    if summary.changed_files and not summary.commit_sha:
        return "test refresh reported changed files but did not report a pushed commit SHA"
    return ""


def refresh_blocked_reason(
    summary: RefreshResultSummary,
    report: ReconcileReport,
    episode: TestEpisode | None,
    selected: list[Story],
) -> str:
    if problem := refresh_result_problem(summary, report):
        return problem
    if report.active_after == 0:
        return "test refresh produced no active acceptance stories"
    if episode and episode.scope == TestEpisodeScope.selected and not selected:
        return "none of the selected testing stories were present after refresh"
    return ""


def _refresh_counts(
    report: ReconcileReport,
    summary: RefreshResultSummary,
    selected: list[Story],
    tasks: list[Task],
    questions_created: int = 0,
) -> dict:
    counts = {
        "stories_reconciled": report.active_after,
        "stories_selected": len(selected),
        "sweeps_queued": len(tasks),
        "spec_baseline": report.baseline,
        "stories_added": len(report.added_keys),
        "stories_updated": len(report.updated_keys),
        "stories_archived": len(report.archived_keys),
        "draft_stories": len(summary.draft_keys),
        "refresh_questions": len(summary.questions),
        "refresh_questions_created": questions_created,
    }
    if summary.commit_sha:
        counts["refresh_commit_sha"] = summary.commit_sha
    return counts


def create_refresh_questions(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    summary: RefreshResultSummary,
) -> list[Question]:
    existing = {
        q.text
        for q in store.list(
            Question,
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
        )
    }
    created: list[Question] = []
    for raw in summary.questions:
        text = (
            "Hive's testing refresh could not derive acceptance confidently without "
            "this answer.\n\n"
            f"{raw}"
        )
        if text in existing:
            continue
        existing.add(text)
        created.append(
            store.put(
                Question(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    workstream_id=workstream.id,
                    text=text,
                )
            )
        )
    return created


def finalize_refresh(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    spec_path: Path,
    *,
    episode: TestEpisode | None = None,
    refresh_result: dict | None = None,
) -> RefreshFinalization:
    summary = RefreshResultSummary.from_payload(refresh_result)
    report = reconcile_story_backlog(store, project, workstream, spec_path, refresh_summary=summary)
    questions = create_refresh_questions(store, project, workstream, summary)
    stories = store.list(
        Story,
        workspace_id=project.workspace_id,
        project_id=project.id,
        workstream_id=workstream.id,
    )
    selected: list[Story] = []
    tasks: list[Task] = []
    if episode:
        selected = select_episode_stories(
            stories,
            episode.scope,
            episode.selected_story_keys,
            episode.max_stories,
        )

    blocked_reason = refresh_blocked_reason(summary, report, episode, selected)
    if blocked_reason:
        if episode:
            def fail(saved: TestEpisode) -> None:
                saved.story_keys = []
                saved.status = TestEpisodeStatus.failed
                saved.finished_at = saved.finished_at or now_s()
                saved.counts = {
                    **saved.counts,
                    **_refresh_counts(report, summary, selected, tasks, len(questions)),
                    "failure": blocked_reason[:500],
                }

            episode = store.update(TestEpisode, episode.id, fail) or episode
        return RefreshFinalization(
            summary=summary,
            episode=episode,
            report=report,
            tasks=tasks,
            questions=questions,
            blocked_reason=blocked_reason,
        )

    if episode:
        tasks = queue_sweep_tasks(store, project, workstream, episode, selected)

        def update(saved: TestEpisode) -> None:
            saved.story_keys = [s.key for s in selected]
            saved.status = TestEpisodeStatus.sweeping if tasks else TestEpisodeStatus.done
            saved.finished_at = now_s() if not tasks else 0.0
            saved.counts = {
                **saved.counts,
                **_refresh_counts(report, summary, selected, tasks, len(questions)),
            }

        episode = store.update(TestEpisode, episode.id, update) or episode
    return RefreshFinalization(summary=summary, episode=episode, report=report, tasks=tasks, questions=questions)


def finish_refresh(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    episode: TestEpisode,
    spec_path: Path,
    refresh_result: dict | None = None,
) -> tuple[TestEpisode, list[str], list[Task]]:
    """Compatibility wrapper for the episode refresh finalization path."""
    finalization = finalize_refresh(
        store,
        project,
        workstream,
        spec_path,
        episode=episode,
        refresh_result=refresh_result,
    )
    return finalization.episode or episode, finalization.report.notes, finalization.tasks


def result_payload(text: str) -> dict:
    try:
        raw = extract_json(text)
    except Exception:
        return {}
    if isinstance(raw, list):
        return {"findings": raw}
    return raw if isinstance(raw, dict) else {}


def _fidelity(payload: dict) -> StoryFidelity:
    value = str(payload.get("fidelity") or "").lower()
    if value == "docker":
        return StoryFidelity.docker
    if value == "local":
        return StoryFidelity.local
    return StoryFidelity.local


def _finding_signature(story_key: str, kind: str, summary: str, oracle: str) -> str:
    raw = f"{story_key}\n{kind}\n{summary.strip().lower()}\n{oracle.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def finding_quality_problem(item: dict) -> str:
    """Return why a sweep finding is too weak to enter the denoise funnel."""
    summary = str(item.get("summary") or "").strip()
    detail = str(item.get("detail") or item.get("repro_steps") or "").strip()
    oracle = str(item.get("oracle") or "").strip()
    if not summary:
        return "missing summary"
    if len(detail) < 30:
        return "missing concrete reproduction detail"
    if len(oracle) < 12:
        return "missing oracle"
    text = f"{summary} {detail} {oracle}".lower()
    has_impact = any(word in text for word in USER_IMPACT_WORDS)
    weak_nitpick = any(word in text for word in WEAK_NITPICK_WORDS)
    if weak_nitpick and not has_impact:
        return "cosmetic or low-impact nitpick"
    return ""


def persist_sweep_findings(
    store,
    project: Project,
    story: Story,
    task: Task,
    episode: TestEpisode,
    payload: dict,
) -> list[Finding]:
    saved: list[Finding] = []
    raw_findings = payload.get("findings") or []
    if not isinstance(raw_findings, list):
        raw_findings = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        if finding_quality_problem(item):
            continue
        kind_raw = str(item.get("kind") or "bug")
        kind = FindingKind.ux_smell if kind_raw == FindingKind.ux_smell else FindingKind.bug
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        oracle = str(item.get("oracle") or "").strip()
        signature = str(item.get("signature") or "").strip() or _finding_signature(story.key, kind, summary, oracle)
        existing = next(
            (
                f
                for f in store.list(
                    Finding,
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    workstream_id=story.workstream_id,
                    story_key=story.key,
                    signature=signature,
                )
                if f.status in (FindingStatus.suspected, FindingStatus.confirmed)
            ),
            None,
        )
        evidence = item.get("evidence_blobs") or item.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        evidence = [str(e) for e in evidence if str(e).strip()]
        finding = existing or Finding(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=story.workstream_id,
            repo=story.repo,
            episode_id=episode.id,
            story_key=story.key,
            kind=kind,
            summary=summary,
            signature=signature,
        )
        finding.repo = story.repo
        finding.episode_id = episode.id
        finding.kind = kind
        finding.severity = str(item.get("severity") or finding.severity)
        finding.summary = summary
        finding.detail = str(item.get("detail") or item.get("repro_steps") or finding.detail)
        finding.oracle = oracle
        finding.evidence_blobs = list(dict.fromkeys([*finding.evidence_blobs, *evidence, *task.artifact_blobs]))
        finding.status = FindingStatus.suspected
        finding.sweep_task_id = task.id
        finding.updated_at = now_s()
        saved.append(store.put(finding))
    return saved


def refresh_episode_counts(store, project: Project, episode: TestEpisode) -> TestEpisode:
    tasks = store.list(Task, workspace_id=project.workspace_id, project_id=project.id, run_id=episode.id)
    findings = store.list(Finding, workspace_id=project.workspace_id, project_id=project.id, episode_id=episode.id)
    stories = [
        s
        for s in store.list(Story, workspace_id=project.workspace_id, project_id=project.id, workstream_id=episode.workstream_id)
        if not episode.story_keys or s.key in episode.story_keys
    ]
    pending = [t for t in tasks if t.status in (TaskStatus.pending, TaskStatus.running)]
    suspected = [f for f in findings if f.status == FindingStatus.suspected]

    def update(saved: TestEpisode) -> None:
        saved.counts = {
            **saved.counts,
            "tasks": len(tasks),
            "pending_tasks": len(pending),
            "findings_suspected": len(suspected),
            "findings_confirmed": sum(1 for f in findings if f.status == FindingStatus.confirmed),
            "stories_passing": sum(1 for s in stories if s.status == StoryStatus.passing),
            "stories_failing": sum(1 for s in stories if s.status == StoryStatus.failing),
            "stories_blocked": sum(1 for s in stories if s.status == StoryStatus.blocked),
        }
        if saved.status in (TestEpisodeStatus.cancelled, TestEpisodeStatus.failed):
            return
        if any(t.kind == TaskKind.test_refresh and t.status in (TaskStatus.pending, TaskStatus.running) for t in tasks):
            saved.status = TestEpisodeStatus.refreshing
        elif any(t.kind == TaskKind.test_sweep and t.status in (TaskStatus.pending, TaskStatus.running) for t in tasks):
            saved.status = TestEpisodeStatus.sweeping
        elif pending or suspected:
            saved.status = TestEpisodeStatus.confirming
        else:
            saved.status = TestEpisodeStatus.done
            saved.finished_at = saved.finished_at or now_s()

    return store.update(TestEpisode, episode.id, update) or episode


def _headers(token: str) -> dict:
    return {**_GH_HEADERS, "Authorization": f"Bearer {token}"} if token else dict(_GH_HEADERS)


def _issue_body(finding: Finding, story: Story) -> str:
    labels = "bug" if finding.kind == FindingKind.bug else "UX smell"
    parts = [
        f"Hive testing confirmed a {labels} while exercising story `{story.key}`.",
        "",
        f"- Story: `{story.key}`",
        f"- Spec ref: `{story.spec_ref}`",
        f"- Severity: {finding.severity}",
        f"- Oracle: {finding.oracle or '(not recorded)'}",
        "",
        "## Story intent",
        story.intent or "(not recorded)",
        "",
        "## Finding",
        finding.detail or finding.summary,
    ]
    if story.oracle_status == StoryOracleStatus.draft:
        parts += [
            "",
            "## Oracle status",
            story.oracle_status_reason
            or "This acceptance story was drafted by Hive's testing refresh and has not been human-blessed yet.",
        ]
    if finding.evidence_blobs:
        parts += ["", "## Evidence", "\n".join(f"- `{name}`" for name in finding.evidence_blobs)]
    return "\n".join(parts)


LABEL_DEFS = {
    "hive-test": ("5b8def", "Filed by Hive's testing workstream"),
    "ux": ("c5def5", "User experience issue found by Hive testing"),
}


def _ensure_issue_labels(owner_repo: str, labels: list[str], headers: dict) -> None:
    for label in labels:
        if label not in LABEL_DEFS:
            continue
        color, description = LABEL_DEFS[label]
        response = httpx.post(
            f"https://api.github.com/repos/{owner_repo}/labels",
            json={"name": label, "color": color, "description": description},
            headers=headers,
            timeout=30.0,
        )
        if response.status_code not in (201, 422):
            response.raise_for_status()


def file_or_update_finding_issue(repo_ref: str, finding: Finding, story: Story, token: str) -> tuple[int, str]:
    owner_repo = parse_repo_ref(repo_ref)
    labels = ["hive-test", "bug" if finding.kind == FindingKind.bug else "ux"]
    title = f"[hive-test][{story.key}] {finding.summary[:120]}"
    body = _issue_body(finding, story)
    headers = _headers(token)
    if finding.issue_number:
        response = httpx.post(
            f"https://api.github.com/repos/{owner_repo}/issues/{finding.issue_number}/comments",
            json={"body": body},
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return finding.issue_number, finding.issue_url
    _ensure_issue_labels(owner_repo, labels, headers)
    response = httpx.post(
        f"https://api.github.com/repos/{owner_repo}/issues",
        json={"title": title, "body": body, "labels": labels},
        headers=headers,
        timeout=30.0,
    )
    if response.status_code == 422:
        response = httpx.post(
            f"https://api.github.com/repos/{owner_repo}/issues",
            json={"title": title, "body": body},
            headers=headers,
            timeout=30.0,
        )
    response.raise_for_status()
    payload = response.json()
    return int(payload["number"]), str(payload.get("html_url") or "")


def close_story_issue(repo_ref: str, story: Story, token: str, comment: str) -> None:
    if not story.open_issue_number:
        return
    owner_repo = parse_repo_ref(repo_ref)
    headers = _headers(token)
    if comment.strip():
        httpx.post(
            f"https://api.github.com/repos/{owner_repo}/issues/{story.open_issue_number}/comments",
            json={"body": comment},
            headers=headers,
            timeout=30.0,
        ).raise_for_status()
    httpx.patch(
        f"https://api.github.com/repos/{owner_repo}/issues/{story.open_issue_number}",
        json={"state": "closed"},
        headers=headers,
        timeout=30.0,
    ).raise_for_status()
