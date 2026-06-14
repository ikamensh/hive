"""Issues mode: resolve a repo's open GitHub issues with a deterministic
per-issue pipeline (see wiki/issues-mode.md).

Two concerns, kept separate so the store logic is testable without network:
- GitHub I/O (`fetch_open_issues_full`, `merge_branch`, `resolve_issue_on_github`)
  — thin httpx calls authed with the control-plane token, like `github_repos.py`.
- Store logic (`reconcile`, `create_resolve_tasks`, `create_review_task`) — pure
  ops mapping issues to workstreams and queuing the codex resolve/review tasks.

An issue becomes a `Workstream` (source=issue); the lifecycle is
resolving → (blocked_clarity | reviewing) → (rejected | done). `order`/
`activate_next` are the dormant ordered variant, kept for future reuse.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from hive.github_repos import _GH_HEADERS, parse_repo_ref
from hive.models import (
    Project,
    Task,
    TaskKind,
    TaskStatus,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)
from hive.prompts import load as load_prompt

log = logging.getLogger("hive.issues")

# Dormant ordered variant: an issue-workstream is "in flight" (no other may be
# activated) while in these statuses. Unused by the active per-issue pipeline.
IN_FLIGHT = (WorkstreamStatus.active, WorkstreamStatus.parked)

ISSUE_DIR = ".hive/issue-{n}"
RESOLVE_BACKEND = "codex"
RESOLVE_MODEL = "gpt-5.5"

_IMG_MD = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_IMG_HTML = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


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
        raise RuntimeError(f"merge conflict landing {head} into {base}")
    response.raise_for_status()  # 201 merged, 204 nothing to merge


def attachment_key(workspace_id: str, project_id: str, issue_number: int, name: str) -> str:
    return f"workspaces/{workspace_id}/issue-attachments/{project_id}/{issue_number}/{name}"


def _safe_name(url: str, index: int) -> str:
    name = Path(url.split("?")[0]).name
    return name if name and "." in name else f"image-{index}"


def download_issue_attachments(store, blobs, project: Project, token: str) -> tuple[int, int]:
    """Download every issue-workstream's embedded images on the control plane —
    which is authed to the repo — into the blob store, and replace the URL list on
    each workstream with the stored filenames. Runners fetch the bytes back from
    the control plane (`GET /api/tasks/{id}/attachments/{name}`), so a worker
    never needs GitHub credentials of its own. Returns (downloaded, failed).

    An issue is worth nothing without its screenshots, so attachments are part of
    the task context, not a best-effort extra. A failed download is logged and the
    filename dropped (the agent then sees the gap rather than a broken path); the
    failed count is surfaced on the scan so the operator notices."""
    headers = {**_headers(token), "Accept": "application/octet-stream"}
    downloaded = failed = 0
    for ws in _issue_workstreams(store, project):
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
    httpx.patch(
        f"https://api.github.com/repos/{owner_repo}/issues/{number}",
        json={"state": "closed"},
        headers=headers,
        timeout=30.0,
    ).raise_for_status()


# -- store reconciliation + task creation ------------------------------------


def _issue_workstreams(store, project: Project) -> list[Workstream]:
    return [
        w
        for w in store.list(Workstream, project_id=project.id)
        if w.source == WorkstreamSource.issue
    ]


def issue_branch(number: int) -> str:
    return f"hive/issue-{number}"


def reconcile(store, project: Project, issues: list[dict]) -> list[str]:
    """Sync issue-workstreams to the repo's open issues (full dicts from
    `fetch_open_issues_full`). New issues, and previously blocked/rejected/closed
    ones that are still open, (re-)enter as `resolving`; externally-closed ones
    are cancelled; in-flight content is refreshed. Returns change notes."""
    by_number = {w.issue_number: w for w in _issue_workstreams(store, project)}
    open_numbers = {i["number"] for i in issues}
    notes: list[str] = []

    for issue in issues:
        ws = by_number.get(issue["number"])
        if ws is None:
            store.put(
                Workstream(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    title=f"#{issue['number']} {issue['title']}",
                    description=issue["doc"],
                    status=WorkstreamStatus.resolving,
                    source=WorkstreamSource.issue,
                    issue_number=issue["number"],
                    issue_url=issue["url"],
                    issue_attachments=issue["attachments"],
                    order=issue["number"],
                )
            )
            notes.append(f"ingested issue #{issue['number']} '{issue['title']}'")
            continue
        ws.description = issue["doc"]  # pick up new comments/images
        ws.issue_attachments = issue["attachments"]
        if ws.status in (
            WorkstreamStatus.blocked_clarity,
            WorkstreamStatus.rejected,
            WorkstreamStatus.cancelled,
        ):
            ws.status = WorkstreamStatus.resolving  # clarified/reopened: retry
            ws.parked_reason = ""
            notes.append(f"re-opening issue #{issue['number']} for another attempt")
        store.put(ws)

    for number, ws in by_number.items():
        if number in open_numbers or ws.status in (
            WorkstreamStatus.done,
            WorkstreamStatus.cancelled,
        ):
            continue
        ws.status = WorkstreamStatus.cancelled
        ws.parked_reason = "issue closed on GitHub"
        store.put(ws)
        notes.append(f"cancelled #{number}: issue closed on GitHub")

    return notes


def _has_open_task(store, project: Project, workstream_id: str, kind: TaskKind) -> bool:
    return any(
        t.kind == kind and t.status in (TaskStatus.pending, TaskStatus.running)
        for t in store.list(Task, project_id=project.id, workstream_id=workstream_id)
    )


def _instructions(ws: Workstream, prompt_name: str) -> tuple[str, dict]:
    prompt, version = load_prompt(prompt_name)
    path = ISSUE_DIR.format(n=ws.issue_number)
    branch = issue_branch(ws.issue_number)
    header = (
        f"GitHub issue #{ws.issue_number} ({ws.issue_url}).\n"
        f"The full issue (title, body, comments) is in `{path}/ISSUE.md`; "
        f"image attachments, if any, are in `{path}/attachments/`.\n"
        f"You are on git branch `{branch}` (already checked out).\n"
    )
    return f"{header}\n{prompt}", {prompt_name: version}


def _make_issue_task(store, project: Project, ws: Workstream, kind: TaskKind, backend: str) -> Task:
    prompt_name = "resolve" if kind == TaskKind.resolve else "review"
    instructions, versions = _instructions(ws, prompt_name)
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=ws.id,
            repo=project.spec_repo,
            branch=issue_branch(ws.issue_number),
            kind=kind,
            instructions=instructions,
            backend=backend,
            model=RESOLVE_MODEL,
            issue_number=ws.issue_number,
            issue_doc=ws.description,
            issue_attachments=ws.issue_attachments,
            prompt_versions=versions,
        )
    )


def create_resolve_tasks(store, project: Project, backend: str = RESOLVE_BACKEND) -> int:
    """Queue a resolve task (clarify→fix) for every `resolving` issue that has
    none in flight. Deterministic — invoked by the scan action."""
    created = 0
    for ws in _issue_workstreams(store, project):
        if ws.status != WorkstreamStatus.resolving:
            continue
        if _has_open_task(store, project, ws.id, TaskKind.resolve):
            continue
        _make_issue_task(store, project, ws, TaskKind.resolve, backend)
        created += 1
    return created


def create_review_task(store, project: Project, ws: Workstream, backend: str = RESOLVE_BACKEND) -> Task:
    """Queue the independent review task for a fixed issue."""
    return _make_issue_task(store, project, ws, TaskKind.review, backend)


def activate_next(store, project: Project) -> Workstream | None:
    """Dormant ordered variant: promote the lowest-`order` queued issue when
    none is in flight. Unused by the active pipeline; kept for future reuse."""
    existing = _issue_workstreams(store, project)
    if any(w.status in IN_FLIGHT for w in existing):
        return None
    queued = sorted(
        (w for w in existing if w.status == WorkstreamStatus.queued),
        key=lambda w: (w.order, w.issue_number),
    )
    if not queued:
        return None
    nxt = queued[0]
    nxt.status = WorkstreamStatus.active
    store.put(nxt)
    return nxt
