"""CI auto-fix: when a repo's default-branch CI is red, file a GitHub issue and
let the existing issue-solving pipeline fix it.

This is the testing-workstream pattern applied to CI (see wiki/ci-autofix.md):
a deterministic check **produces a GitHub issue**, and `issues.py`'s
resolve→review→land pipeline does the actual fixing — no new fixer. Two concerns
are kept apart so the store logic is testable without network:

- GitHub I/O (`fetch_ci_status`, `file_ci_issue`) — thin httpx calls authed with
  the chief token, like `github_repos.py`/`issues.py`.
- Orchestration (`check_and_autofix`) — pure store ops: file when red+new, then
  reuse `reconcile`/`advance_issues` to ingest the issue and queue the fix.

Idempotency: each CI issue body embeds `<!-- hive-ci sha=<sha> -->`. A re-check
of the same red commit finds that marker on an open issue and does not file a
duplicate; a new red commit (new sha) files a fresh issue.
"""

from __future__ import annotations

import logging
from enum import StrEnum

import httpx
from pydantic import BaseModel

from hive.integrations.github_repos import _GH_HEADERS, parse_repo_ref
from hive.models import Project, ProjectWorkstream
from hive.workstreams.issues import (
    DEFAULT_ISSUE_MODEL,
    RESOLVE_BACKEND,
    advance_issues,
    default_branch,
    fetch_open_issues_full,
    reconcile,
)

log = logging.getLogger("hive.workstreams.ci")

CI_LABEL = "hive-ci"
# GitHub check-run conclusions that mean the build is broken (vs. success /
# neutral / skipped which we treat as not-failing).
FAILING_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "stale", "startup_failure"}
PENDING_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}


class CiConclusion(StrEnum):
    passing = "passing"
    failing = "failing"
    pending = "pending"  # checks still running; verdict not final
    none = "none"  # no CI configured on the default branch


class CiStatus(BaseModel):
    repo: str
    branch: str = ""
    sha: str = ""
    conclusion: CiConclusion = CiConclusion.none
    failing_checks: list[dict] = []  # [{"name", "url"}]
    html_url: str = ""  # branch commits page, for the operator


class CiCheckResult(BaseModel):
    """What a check-ci run did, for the API/UI and CLI."""

    repo: str
    branch: str = ""
    sha: str = ""
    conclusion: CiConclusion = CiConclusion.none
    failing_checks: list[dict] = []
    html_url: str = ""
    filed_issue: int = 0  # the CI issue number (newly filed or the matched open one)
    filed_issue_url: str = ""
    already_filed: bool = False  # red, but an open hive-ci issue already covers this sha
    open_issues: int = 0
    resolve_queued: int = 0


def _headers(token: str) -> dict:
    return {**_GH_HEADERS, "Authorization": f"Bearer {token}"} if token else dict(_GH_HEADERS)


def ci_marker(sha: str) -> str:
    return f"hive-ci sha={sha}"


# -- GitHub I/O --------------------------------------------------------------


def fetch_ci_status(repo_ref: str, token: str) -> CiStatus:
    """Read the default branch's head-commit CI: check-runs (GitHub Actions and
    other apps) plus the legacy commit-status API, combined into one verdict.

    `failing` if any check/status failed; `pending` if some are still running and
    none failed; `passing` if there are checks and all succeeded; `none` if the
    default branch has no CI at all (so we never file noise on repos without CI).
    """
    owner_repo = parse_repo_ref(repo_ref)
    headers = _headers(token)
    branch = default_branch(repo_ref, token)

    commit = httpx.get(
        f"https://api.github.com/repos/{owner_repo}/commits/{branch}",
        headers=headers,
        timeout=30.0,
    )
    commit.raise_for_status()
    sha = str(commit.json().get("sha") or "")
    status = CiStatus(repo=repo_ref, branch=branch, sha=sha,
                      html_url=f"https://github.com/{owner_repo}/commits/{branch}")
    if not sha:
        return status

    runs = httpx.get(
        f"https://api.github.com/repos/{owner_repo}/commits/{sha}/check-runs",
        params={"per_page": 100},
        headers=headers,
        timeout=30.0,
    )
    runs.raise_for_status()
    check_runs = runs.json().get("check_runs", [])

    combined = httpx.get(
        f"https://api.github.com/repos/{owner_repo}/commits/{sha}/status",
        headers=headers,
        timeout=30.0,
    )
    combined.raise_for_status()
    legacy = combined.json()

    failing: list[dict] = []
    pending = False
    seen = False
    for run in check_runs:
        seen = True
        state = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if conclusion in FAILING_CONCLUSIONS:
            failing.append({"name": str(run.get("name") or "check"), "url": str(run.get("html_url") or "")})
        elif state in PENDING_STATUSES or (state == "completed" and not conclusion):
            pending = True
    for ctx in legacy.get("statuses", []):
        seen = True
        state = str(ctx.get("state") or "")
        if state in ("failure", "error"):
            failing.append({"name": str(ctx.get("context") or "status"), "url": str(ctx.get("target_url") or "")})
        elif state == "pending":
            pending = True

    status.failing_checks = failing
    if failing:
        status.conclusion = CiConclusion.failing
    elif pending:
        status.conclusion = CiConclusion.pending
    elif seen:
        status.conclusion = CiConclusion.passing
    else:
        status.conclusion = CiConclusion.none
    return status


def ci_issue_title(status: CiStatus) -> str:
    return f"[{CI_LABEL}] CI failing on {status.branch} ({status.sha[:7]})"


def ci_issue_body(status: CiStatus) -> str:
    checks = "\n".join(
        f"- {c['name']}" + (f" — {c['url']}" if c.get("url") else "")
        for c in status.failing_checks
    ) or "- (no individual check details reported)"
    return "\n".join(
        [
            "Hive filed this automatically because the default-branch CI is red.",
            "",
            f"<!-- {ci_marker(status.sha)} -->",
            "",
            f"- Branch: `{status.branch}`",
            f"- Commit: `{status.sha}`",
            f"- Commits view: {status.html_url}",
            "",
            "## Failing checks",
            checks,
            "",
            "## What to do",
            "Reproduce the failing check(s) locally, find and fix the root cause, and make "
            "CI pass again. Open the check links above for the failing logs. If the failure "
            "is a flaky test or infra issue rather than a real regression, say so and explain.",
        ]
    )


def file_ci_issue(repo_ref: str, status: CiStatus, token: str) -> tuple[int, str]:
    """Create the CI issue, labeled `hive-ci`. Returns (number, html_url)."""
    owner_repo = parse_repo_ref(repo_ref)
    headers = _headers(token)
    httpx.post(
        f"https://api.github.com/repos/{owner_repo}/labels",
        json={"name": CI_LABEL, "color": "b60205", "description": "Filed by Hive when CI is red"},
        headers=headers,
        timeout=30.0,
    )  # 201 created or 422 already-exists; either is fine
    body = {"title": ci_issue_title(status), "body": ci_issue_body(status), "labels": [CI_LABEL]}
    response = httpx.post(
        f"https://api.github.com/repos/{owner_repo}/issues",
        json=body,
        headers=headers,
        timeout=30.0,
    )
    if response.status_code == 422:  # label may not be creatable for this token
        response = httpx.post(
            f"https://api.github.com/repos/{owner_repo}/issues",
            json={"title": body["title"], "body": body["body"]},
            headers=headers,
            timeout=30.0,
        )
    response.raise_for_status()
    payload = response.json()
    return int(payload["number"]), str(payload.get("html_url") or "")


# -- orchestration (store ops; network is the two functions above) -----------


def check_and_autofix(
    store,
    project: Project,
    workstream: ProjectWorkstream,
    token: str,
    *,
    issue_backend: str = RESOLVE_BACKEND,
    issue_model: str = DEFAULT_ISSUE_MODEL,
    advance: bool = True,
) -> CiCheckResult:
    """Check one repo's CI; when red and not already filed, open a GitHub issue
    and (when `advance`) hand it to the issue-solving pipeline. Green/pending/no-CI
    repos are left untouched — no store writes, no issue spam."""
    repo = workstream.repo
    status = fetch_ci_status(repo, token)
    result = CiCheckResult(
        repo=repo,
        branch=status.branch,
        sha=status.sha,
        conclusion=status.conclusion,
        failing_checks=status.failing_checks,
        html_url=status.html_url,
    )
    if status.conclusion != CiConclusion.failing:
        return result

    open_full = fetch_open_issues_full(repo, token)
    marker = ci_marker(status.sha)
    existing = next((i for i in open_full if marker in (i.get("doc") or "")), None)
    if existing:
        result.already_filed = True
        result.filed_issue = existing["number"]
        result.filed_issue_url = existing["url"]
    else:
        number, url = file_ci_issue(repo, status, token)
        result.filed_issue = number
        result.filed_issue_url = url
        open_full = open_full + [
            {
                "number": number,
                "title": ci_issue_title(status),
                "url": url,
                "doc": ci_issue_body(status),
                "attachments": [],
            }
        ]
        log.info("filed CI issue #%s for %s (%s)", number, repo, status.sha[:7])

    reconcile(store, project, open_full, workstream=workstream)
    result.open_issues = len(open_full)
    if advance:
        result.resolve_queued = advance_issues(
            store, project, workstream=workstream, backend=issue_backend, model=issue_model
        )
    return result
