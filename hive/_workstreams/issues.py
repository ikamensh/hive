"""Issue solving: resolve a repo's open GitHub issues with a deterministic
per-issue pipeline (see wiki/issue-solving.md).

Two concerns, kept separate so the store logic is testable without network:
- GitHub I/O (`fetch_open_issues_full`, `merge_branch`, `resolve_issue_on_github`)
  — thin httpx calls authed with the chief token, like `github_repos.py`.
- Store logic (`reconcile`, `advance_issues`, `create_review_task`) — pure ops
  mapping issues to work items and queuing the codex resolve/review tasks.

An issue becomes an `IssueItem` work item;
the lifecycle is
queued → resolving → (blocked_clarity | reviewing) → (rejected | done).
Sequencing is **strict per GitHub-issues workstream**: `advance_issues` keeps at
most one issue in the resolve→review pipeline at a time, so each issue branches
from a default branch that already includes prior landed fixes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from hive._control.allowances import resolve_agent
from hive._integrations.github_repos import _GH_HEADERS, parse_repo_ref
from hive.models import (
    Directive,
    DirectiveStatus,
    ISSUE_BLOCKED,
    IssueRun,
    IssueRunScope,
    IssueRunStatus,
    Project,
    ProjectWorkstream,
    ProjectWorkstreamKind,
    ProjectWorkstreamStatus,
    Task,
    TaskKind,
    TaskStatus,
    IssueItem,
    IssueItemStatus,
)
from hive.llm.prompts import load as load_prompt

log = logging.getLogger("hive._workstreams.issues")

ISSUE_DIR = ".hive/issue-{n}"
RESOLVE_BACKEND = "codex"
DEFAULT_ISSUE_MODEL = ""
LANDING_FAILED_PREFIX = "accepted but landing failed"
# How long a just-created issue may be missing from the (eventually consistent)
# GitHub list API before its absence counts as an external close.

_IMG_MD = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_IMG_HTML = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


class MergeConflictError(RuntimeError):
    """The issue branch could not be mechanically merged into the default branch."""

    def __init__(self, head: str, base: str) -> None:
        self.head = head
        self.base = base
        super().__init__(f"merge conflict landing {head} into {base}")


def _headers(token: str) -> dict:
    return {**_GH_HEADERS, "Authorization": f"Bearer {token}"} if token else dict(_GH_HEADERS)


def list_open_issues(repo_ref: str, token: str) -> list[dict]:
    """Open issues (not pull requests) for *repo_ref*. number/title/body/url/labels."""
    owner_repo = parse_repo_ref(repo_ref)
    headers = _headers(token)
    issues: list[dict] = []
    page = 1
    while page <= 5:
        response = httpx.get(
            f"https://api.github.com/repos/{owner_repo}/issues",
            params={"state": "open", "per_page": 100, "page": page},
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        for raw in batch:
            if raw.get("pull_request"):
                continue  # the issues endpoint also returns PRs; skip them
            issues.append(
                {
                    "number": int(raw["number"]),
                    "title": str(raw.get("title") or ""),
                    "body": str(raw.get("body") or ""),
                    "url": str(raw.get("html_url") or ""),
                    "labels": [str(lbl.get("name", "")) for lbl in raw.get("labels", [])],
                }
            )
        if len(batch) < 100:
            break
        page += 1
    return issues


def create_issue(repo_ref: str, title: str, body: str, token: str) -> dict:
    """Create a GitHub issue; returns {"number", "html_url"}. Used by flows
    whose record belongs on GitHub (CI auto-fix, testing findings)."""
    owner_repo = parse_repo_ref(repo_ref)
    response = httpx.post(
        f"https://api.github.com/repos/{owner_repo}/issues",
        json={"title": title, "body": body},
        headers=_headers(token),
        timeout=30.0,
    )
    if response.status_code >= 300:
        raise _github_error(response, "create issue")
    data = response.json()
    return {"number": int(data["number"]), "html_url": str(data.get("html_url") or "")}


def is_directive_item(item: IssueItem) -> bool:
    """Directive-born work items run the same resolve→review→merge pipeline but
    have no GitHub issue behind them — GitHub is a source of work in, never
    hive's internal ledger."""
    return item.external_ref.get("origin") == "directive"


def item_branch(item: IssueItem) -> str:
    """The work branch for one pipeline item."""
    if is_directive_item(item):
        return f"hive/ask-{item.id[:8]}"
    return f"hive/issue-{item.issue_number}"


def seed_directive_item(
    store, project: Project, workstream: ProjectWorkstream, directive: Directive
) -> IssueItem:
    """Turn an operator directive into a queued pipeline work item, ahead of
    any mirrored GitHub backlog (a direct ask outranks ambient issues)."""
    first = directive.text.strip().splitlines()[0].strip()
    title = first if len(first) <= 80 else first[:77] + "..."
    existing = _issue_workstreams(store, project, workstream)
    front = min((w.order for w in existing), default=1) - 1
    return store.put(
        IssueItem(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=workstream.id,
            repo=workstream.repo,
            title=title,
            description=directive.text.strip(),
            status=IssueItemStatus.queued,
            external_ref={"origin": "directive", "directive_id": directive.id},
            order=front,
        )
    )


DIRECTIVE_NOTES = {
    IssueItemStatus.queued: (DirectiveStatus.working, "queued; runs when the pipeline frees up"),
    IssueItemStatus.resolving: (DirectiveStatus.working, "an agent is working the ask"),
    IssueItemStatus.reviewing: (DirectiveStatus.working, "fix ready; an independent reviewer is checking it"),
    IssueItemStatus.blocked_clarity: (DirectiveStatus.working, "needs you: the agent could not proceed"),
    IssueItemStatus.rejected: (DirectiveStatus.working, "needs you: the review rejected the fix"),
    IssueItemStatus.done: (DirectiveStatus.done, "landed on the default branch"),
    IssueItemStatus.cancelled: (DirectiveStatus.cancelled, "cancelled"),
}


def sync_directive_for_item(store, item: IssueItem) -> None:
    """Reflect a directive-born item's pipeline state onto its directive."""
    directive_id = item.external_ref.get("directive_id", "")
    if not directive_id:
        return
    directive = store.get(Directive, directive_id)
    if directive is None or directive.status in (DirectiveStatus.done, DirectiveStatus.cancelled):
        return
    status, note = DIRECTIVE_NOTES.get(item.status, (DirectiveStatus.working, str(item.status)))
    if item.parked_reason and item.status in ISSUE_BLOCKED:
        note = f"{note}: {item.parked_reason[:200]}"
    directive.status = status
    directive.routing_note = note
    directive.updated_at = now_s()
    store.put(directive)


def fetch_issue_comments(repo_ref: str, number: int, token: str) -> list[dict]:
    owner_repo = parse_repo_ref(repo_ref)
    response = httpx.get(
        f"https://api.github.com/repos/{owner_repo}/issues/{number}/comments",
        params={"per_page": 100},
        headers=_headers(token),
        timeout=30.0,
    )
    response.raise_for_status()
    return [
        {"author": (c.get("user") or {}).get("login", ""), "body": str(c.get("body") or "")}
        for c in response.json()
    ]


def extract_image_urls(markdown: str) -> list[str]:
    """Embedded image URLs from issue/comment markdown (markdown + <img>)."""
    return _IMG_MD.findall(markdown or "") + _IMG_HTML.findall(markdown or "")


def build_issue_doc(title: str, body: str, comments: list[dict]) -> str:
    parts = [f"# {title}", "", body or "(no description)"]
    for c in comments:
        parts += ["", f"## Comment by @{c['author']}", c["body"]]
    return "\n".join(parts)


def fetch_open_issues_full(repo_ref: str, token: str) -> list[dict]:
    """Each open issue with its comments folded into a markdown `doc` and the
    `attachments` (embedded image URLs) collected from body + comments."""
    full: list[dict] = []
    for issue in list_open_issues(repo_ref, token):
        comments = fetch_issue_comments(repo_ref, issue["number"], token)
        images = extract_image_urls(issue["body"])
        for c in comments:
            images += extract_image_urls(c["body"])
        full.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "url": issue["url"],
                "doc": build_issue_doc(issue["title"], issue["body"], comments),
                "attachments": list(dict.fromkeys(images)),  # de-dup, keep order
            }
        )
    return full


# -- merge / close (no PR) ---------------------------------------------------


def default_branch(repo_ref: str, token: str) -> str:
    owner_repo = parse_repo_ref(repo_ref)
    response = httpx.get(
        f"https://api.github.com/repos/{owner_repo}",
        headers=_headers(token),
        timeout=15.0,
    )
    response.raise_for_status()
    return str(response.json().get("default_branch") or "main")


def merge_branch(repo_ref: str, head: str, token: str, message: str = "") -> None:
    """Merge *head* into the repo's default branch via the merges API — a real
    merge commit, no PR. Raises on conflict (409) so the caller can escalate."""
    owner_repo = parse_repo_ref(repo_ref)
    base = default_branch(repo_ref, token)
    response = httpx.post(
        f"https://api.github.com/repos/{owner_repo}/merges",
        json={"base": base, "head": head, "commit_message": message or f"Merge {head}"},
        headers=_headers(token),
        timeout=30.0,
    )
    if response.status_code == 409:
        raise MergeConflictError(head, base)
    response.raise_for_status()  # 201 merged, 204 nothing to merge


def delete_branch(repo_ref: str, branch: str, token: str) -> None:
    """Delete a now-merged work branch via the git refs API. Idempotent: a 404/422
    (already gone / not deletable) is treated as success. The caller lands the
    issue first and deletes best-effort — a leftover branch is only cruft, never a
    reason to fail a merge that already succeeded."""
    owner_repo = parse_repo_ref(repo_ref)
    response = httpx.delete(
        f"https://api.github.com/repos/{owner_repo}/git/refs/heads/{branch}",
        headers=_headers(token),
        timeout=30.0,
    )
    if response.status_code in (204, 404, 422):
        return
    raise _github_error(response, f"delete branch {branch}")


def _github_error(response: httpx.Response, action: str) -> RuntimeError:
    detail = response.text.strip()
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        bits = [str(payload.get("message") or "").strip()]
        errors = payload.get("errors")
        if errors:
            bits.append(str(errors))
        detail = "; ".join(bit for bit in bits if bit) or detail
    return RuntimeError(f"{action} failed: HTTP {response.status_code} {detail}".strip())


def attachment_key(workspace_id: str, project_id: str, issue_number: int, name: str) -> str:
    return f"workspaces/{workspace_id}/issue-attachments/{project_id}/{issue_number}/{name}"


def _safe_name(url: str, index: int) -> str:
    name = Path(url.split("?")[0]).name
    return name if name and "." in name else f"image-{index}"


def download_issue_attachments(
    store,
    blobs,
    project: Project,
    token: str,
    workstream: ProjectWorkstream | None = None,
) -> tuple[int, int]:
    """Download every issue-workstream's embedded images on the chief —
    which is authed to the repo — into the blob store, and replace the URL list on
    each workstream with the stored filenames. Runners fetch the bytes back from
    the chief (`GET /api/tasks/{id}/attachments/{name}`), so a worker
    never needs GitHub credentials of its own. Returns (downloaded, failed).

    An issue is worth nothing without its screenshots, so attachments are part of
    the task context, not a best-effort extra. A failed download is logged and the
    filename dropped (the agent then sees the gap rather than a broken path); the
    failed count is surfaced on the scan so the operator notices."""
    headers = {**_headers(token), "Accept": "application/octet-stream"}
    downloaded = failed = 0
    for ws in _issue_workstreams(store, project, workstream):
        if not ws.issue_attachments:
            continue
        names: list[str] = []
        for index, url in enumerate(ws.issue_attachments):
            if not url.startswith("http"):
                names.append(url)  # already a stored filename (e.g. re-run without re-fetch)
                continue
            try:
                response = httpx.get(url, headers=headers, timeout=60.0, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("attachment download failed for issue #%s (%s): %s", ws.issue_number, url, exc)
                failed += 1
                continue
            name = _safe_name(url, index)
            blobs.put(attachment_key(project.workspace_id, project.id, ws.issue_number, name), response.content)
            names.append(name)
            downloaded += 1
        ws.issue_attachments = names
        store.put(ws)
    return downloaded, failed


def resolve_issue_on_github(repo_ref: str, number: int, comment: str, token: str) -> None:
    """Comment on the issue and close it."""
    owner_repo = parse_repo_ref(repo_ref)
    headers = _headers(token)
    if comment.strip():
        httpx.post(
            f"https://api.github.com/repos/{owner_repo}/issues/{number}/comments",
            json={"body": comment},
            headers=headers,
            timeout=30.0,
        ).raise_for_status()
    response = httpx.patch(
        f"https://api.github.com/repos/{owner_repo}/issues/{number}",
        json={"state": "closed"},
        headers=headers,
        timeout=30.0,
    )
    if response.is_success:
        return
    if issue_is_closed(repo_ref, number, token):
        log.info("issue #%s close returned HTTP %s, but GitHub already reports it closed", number, response.status_code)
        return
    raise _github_error(response, f"close issue #{number}")


def issue_is_closed(repo_ref: str, number: int, token: str) -> bool:
    owner_repo = parse_repo_ref(repo_ref)
    response = httpx.get(
        f"https://api.github.com/repos/{owner_repo}/issues/{number}",
        headers=_headers(token),
        timeout=15.0,
    )
    if not response.is_success:
        raise _github_error(response, f"read issue #{number}")
    return str(response.json().get("state") or "").lower() == "closed"


# -- store reconciliation + task creation ------------------------------------


def now_s() -> float:
    import time

    return time.time()


def ensure_iteration_workstream(store, project: Project) -> ProjectWorkstream:
    existing = store.list(
        ProjectWorkstream,
        workspace_id=project.workspace_id,
        project_id=project.id,
        kind=ProjectWorkstreamKind.iteration,
    )
    if existing:
        return existing[0]
    return store.put(
        ProjectWorkstream(
            workspace_id=project.workspace_id,
            project_id=project.id,
            kind=ProjectWorkstreamKind.iteration,
            title="Iteration goal",
            status=ProjectWorkstreamStatus.active,
        )
    )


def ensure_issue_workstream(store, project: Project, repo: str | None = None) -> ProjectWorkstream:
    repo = (repo or project.spec_repo).strip()
    if not repo:
        raise ValueError("repo is required for GitHub issue solving")
    existing = store.list(
        ProjectWorkstream,
        workspace_id=project.workspace_id,
        project_id=project.id,
        kind=ProjectWorkstreamKind.github_issues,
        repo=repo,
    )
    if existing:
        return existing[0]
    return store.put(
        ProjectWorkstream(
            workspace_id=project.workspace_id,
            project_id=project.id,
            kind=ProjectWorkstreamKind.github_issues,
            title=f"GitHub issues: {parse_repo_ref(repo)}",
            repo=repo,
            source_ref={"provider": "github", "issues": True},
            status=ProjectWorkstreamStatus.idle,
        )
    )


def project_workstreams(store, project: Project) -> list[ProjectWorkstream]:
    ensure_iteration_workstream(store, project)
    issue_stream = None
    if project.spec_repo.strip():
        try:
            issue_stream = ensure_issue_workstream(store, project)
        except ValueError:
            pass
        try:
            from hive._workstreams.testing import ensure_testing_workstream

            repos = project.member_repos or [project.spec_repo]
            for repo in dict.fromkeys([r.strip() for r in repos if r.strip()]):
                ensure_testing_workstream(store, project, repo=repo)
        except ValueError:
            pass
    for item in store.list(
        IssueItem,
        workspace_id=project.workspace_id,
        project_id=project.id,
    ):
        if item.workstream_id:
            continue
        if issue_stream is not None and item.repo in ("", issue_stream.repo):
            item.workstream_id = issue_stream.id
            item.repo = item.repo or issue_stream.repo
            store.put(item)
    return store.list(
        ProjectWorkstream,
        workspace_id=project.workspace_id,
        project_id=project.id,
    )


def _item_in_workstream(item: IssueItem, workstream: ProjectWorkstream | None) -> bool:
    if workstream is None:
        return True
    if item.workstream_id:
        return item.workstream_id == workstream.id
    return item.repo in ("", workstream.repo)


def _issue_workstreams(
    store,
    project: Project,
    workstream: ProjectWorkstream | None = None,
) -> list[IssueItem]:
    return [
        w
        for w in store.list(IssueItem, project_id=project.id)
        if (w.issue_number or is_directive_item(w)) and _item_in_workstream(w, workstream)
    ]


def _issue_items_for_run(store, project: Project, run: IssueRun | None) -> list[IssueItem]:
    workstream = store.get(ProjectWorkstream, run.workstream_id) if run else None
    items = _issue_workstreams(store, project, workstream)
    if run and run.issue_numbers:
        allowed = set(run.issue_numbers)
        items = [w for w in items if w.issue_number in allowed]
    return items


def refresh_issue_run(store, project: Project, run: IssueRun) -> IssueRun:
    items = _issue_items_for_run(store, project, run)
    counts = {
        "queued": sum(1 for w in items if w.status == IssueItemStatus.queued),
        "running": sum(1 for w in items if w.status in (IssueItemStatus.resolving, IssueItemStatus.reviewing)),
        "blocked": sum(1 for w in items if w.status in (IssueItemStatus.blocked_clarity, IssueItemStatus.rejected)),
        "done": sum(1 for w in items if w.status == IssueItemStatus.done),
        "cancelled": sum(1 for w in items if w.status == IssueItemStatus.cancelled),
    }

    def update(saved: IssueRun) -> None:
        saved.counts = {**saved.counts, **counts}
        if saved.status == IssueRunStatus.cancelled:
            return
        if counts["running"]:
            saved.status = IssueRunStatus.running
            if not saved.started_at:
                saved.started_at = now_s()
        elif counts["blocked"]:
            saved.status = IssueRunStatus.blocked
        elif counts["queued"]:
            saved.status = IssueRunStatus.queued
        elif items or saved.scope == IssueRunScope.scan_only:
            saved.status = IssueRunStatus.done
            if not saved.finished_at:
                saved.finished_at = now_s()

    return store.update(IssueRun, run.id, update) or run


def reconcile(
    store,
    project: Project,
    issues: list[dict],
    workstream: ProjectWorkstream | None = None,
    requeue_stalled: bool = True,
) -> list[str]:
    """Sync issue work items to the repo's open issues (full dicts from
    `fetch_open_issues_full`). New issues enter as `queued`; any still-open issue
    that isn't `done` and has no live task is reset to `queued` (so a re-scan
    restarts blocked/rejected/reopened or errored-mid-flight issues for another
    attempt); externally-closed ones are cancelled; content is refreshed.
    `advance_issues` then starts the next one. Returns change notes.

    `requeue_stalled=False` is the unattended-poller variant: ingest new issues
    and reflect closures, but never resurrect blocked/rejected items — retrying
    a failed issue is a deliberate (human scan) act, not something a periodic
    tick should burn quota on forever."""
    workstream = workstream or ensure_issue_workstream(store, project)
    items = _issue_workstreams(store, project, workstream)
    by_number = {w.issue_number: w for w in items if w.issue_number}
    open_numbers = {i["number"] for i in issues}
    notes: list[str] = []

    # Directive-born items live outside the GitHub mirror: no external close,
    # but a stalled one (errored mid-flight) is re-queued on a deliberate scan.
    for item in items:
        if not is_directive_item(item):
            continue
        if (
            requeue_stalled
            and item.status not in (IssueItemStatus.done, IssueItemStatus.queued, IssueItemStatus.cancelled)
            and not _has_live_task(store, project, item.id)
        ):
            item.status = IssueItemStatus.queued
            item.parked_reason = ""
            store.put(item)
            sync_directive_for_item(store, item)
            notes.append(f"re-queued directive item '{item.title}' for another attempt")

    for issue in issues:
        ws = by_number.get(issue["number"])
        if ws is None:
            store.put(
                IssueItem(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    workstream_id=workstream.id,
                    repo=workstream.repo,
                    title=f"#{issue['number']} {issue['title']}",
                    description=issue["doc"],
                    status=IssueItemStatus.queued,
                            issue_number=issue["number"],
                    issue_url=issue["url"],
                    issue_attachments=issue["attachments"],
                    external_ref={"provider": "github", "issue_number": issue["number"], "url": issue["url"]},
                    order=issue["number"],
                )
            )
            notes.append(f"ingested issue #{issue['number']} '{issue['title']}'")
            continue
        ws.workstream_id = workstream.id
        ws.repo = workstream.repo
        ws.description = issue["doc"]  # pick up new comments/images
        ws.issue_attachments = issue["attachments"]
        ws.external_ref = {"provider": "github", "issue_number": issue["number"], "url": issue["url"]}
        if (
            requeue_stalled
            and ws.status not in (IssueItemStatus.done, IssueItemStatus.queued)
            and not _has_live_task(store, project, ws.id)
        ):
            ws.status = IssueItemStatus.queued  # blocked/rejected/reopened/errored: retry
            ws.parked_reason = ""
            notes.append(f"re-queued issue #{issue['number']} for another attempt")
        store.put(ws)

    for number, ws in by_number.items():
        if number in open_numbers or ws.status in (
            IssueItemStatus.done,
            IssueItemStatus.cancelled,
        ):
            continue
        if ws.status == IssueItemStatus.rejected and ws.parked_reason.startswith(LANDING_FAILED_PREFIX):
            ws.status = IssueItemStatus.done
            ws.parked_reason = ""
            store.put(ws)
            notes.append(f"marked #{number} done: issue closed on GitHub after landing retry")
            continue
        ws.status = IssueItemStatus.cancelled
        ws.parked_reason = "issue closed on GitHub"
        store.put(ws)
        notes.append(f"cancelled #{number}: issue closed on GitHub")

    return notes


def _has_live_task(store, project: Project, workstream_id: str) -> bool:
    return any(
        t.status in (TaskStatus.pending, TaskStatus.running)
        for t in store.list(Task, project_id=project.id, workstream_id=workstream_id)
    )


def _instructions(ws: IssueItem, prompt_name: str, context: str = "") -> tuple[str, dict]:
    prompt, version = load_prompt(prompt_name)
    branch = item_branch(ws)
    if is_directive_item(ws):
        header = (
            "Operator directive (a direct ask through Hive — there is NO GitHub "
            "issue for this work; where the instructions below mention commenting "
            "on the issue, put that content in your final report instead).\n"
            f"The ask:\n\n{ws.description.strip()}\n\n"
            f"You are on git branch `{branch}` (already checked out).\n"
        )
    else:
        path = ISSUE_DIR.format(n=ws.issue_number)
        header = (
            f"GitHub issue #{ws.issue_number} ({ws.issue_url}).\n"
            f"The full issue (title, body, comments) is in `{path}/ISSUE.md`; "
            f"image attachments, if any, are in `{path}/attachments/`.\n"
            f"You are on git branch `{branch}` (already checked out).\n"
        )
    extra = f"\n{context.strip()}\n" if context.strip() else ""
    return f"{header}{extra}\n{prompt}", {prompt_name: version}


def _make_issue_task(
    store,
    project: Project,
    ws: IssueItem,
    kind: TaskKind,
    backend: str,
    model: str = DEFAULT_ISSUE_MODEL,
    run: IssueRun | None = None,
    prompt_name: str = "",
    context: str = "",
) -> Task:
    prompt_name = prompt_name or ("resolve" if kind == TaskKind.resolve else "review")
    instructions, versions = _instructions(ws, prompt_name, context=context)
    # The one choke point for issue-pipeline agent choice: the configured
    # default is remapped onto whatever the project's agent grants permit.
    backend, model = resolve_agent(project.agent_grants, backend, model)
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=ws.id,
            work_item_id=ws.id,
            run_id=run.id if run else "",
            repo=project.spec_repo,
            branch=item_branch(ws),
            fresh_branch=kind == TaskKind.resolve,
            kind=kind,
            instructions=instructions,
            backend=backend,
            model=model,
            issue_number=ws.issue_number,
            issue_doc=ws.description,
            issue_attachments=ws.issue_attachments,
            prompt_versions=versions,
        )
    )


def advance_issues(
    store,
    project: Project,
    workstream: ProjectWorkstream | None = None,
    run: IssueRun | None = None,
    backend: str = RESOLVE_BACKEND,
    model: str = DEFAULT_ISSUE_MODEL,
) -> int:
    """Strict per-issue sequencing: keep at most one issue in the resolve→review
    pipeline at a time. If an issue is already in flight (`resolving`/`reviewing`)
    do nothing; otherwise promote the lowest-`order` `queued` issue to `resolving`
    and queue its resolve task. Returns 1 if it started an issue, else 0.
    Idempotent — call after every scan and every issue-task landing so the next
    issue branches from a default branch that already has the prior fixes."""
    if run and run.status == IssueRunStatus.cancelled:
        return 0
    if run and run.scope == IssueRunScope.scan_only:
        refresh_issue_run(store, project, run)
        return 0
    wss = _issue_items_for_run(store, project, run) if run else _issue_workstreams(store, project, workstream)
    if any(
        w.status in (IssueItemStatus.resolving, IssueItemStatus.reviewing) for w in wss
    ):
        if run:
            refresh_issue_run(store, project, run)
        return 0
    queued = sorted(
        (w for w in wss if w.status == IssueItemStatus.queued),
        key=lambda w: (w.order, w.issue_number),
    )
    if not queued:
        if run:
            refresh_issue_run(store, project, run)
        return 0
    nxt = queued[0]

    def promote(w: IssueItem) -> None:
        w.status = IssueItemStatus.resolving
        w.parked_reason = ""

    ws = store.update(IssueItem, nxt.id, promote)
    _make_issue_task(store, project, ws, TaskKind.resolve, backend, model=model, run=run)
    if run:
        refresh_issue_run(store, project, run)
    log.info(
        "advance: issue #%s → resolving (%d issue(s) still queued)",
        ws.issue_number, len(queued) - 1,
    )
    return 1


def create_review_task(
    store,
    project: Project,
    ws: IssueItem,
    backend: str = RESOLVE_BACKEND,
    model: str = DEFAULT_ISSUE_MODEL,
    run: IssueRun | None = None,
) -> Task:
    """Queue the independent review task for a fixed issue."""
    return _make_issue_task(store, project, ws, TaskKind.review, backend, model=model, run=run)


def create_landing_integration_task(
    store,
    project: Project,
    ws: IssueItem,
    failure: str,
    accepted_review: str = "",
    backend: str = RESOLVE_BACKEND,
    model: str = DEFAULT_ISSUE_MODEL,
    run: IssueRun | None = None,
) -> Task:
    """Queue an AI review task to integrate an accepted issue branch with the
    latest default branch after a landing-time merge conflict."""
    context = (
        "The previous review accepted this fix, but Hive could not merge the "
        f"issue branch into the default branch:\n\n{failure.strip()}"
    )
    if accepted_review.strip():
        context += f"\n\nAccepted review report:\n{accepted_review.strip()}"
    return _make_issue_task(
        store,
        project,
        ws,
        TaskKind.review,
        backend,
        model=model,
        run=run,
        prompt_name="landing_integration",
        context=context,
    )


