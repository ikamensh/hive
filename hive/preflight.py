"""Issues-mode preflight: turn the things that break a real run into checked
preconditions, so misconfiguration surfaces before a big run rather than as a
half-finished pipeline that's hard to debug.

Two layers:
- `preflight_checks` — control-plane checks (project config, the GitHub token's
  read/write on the repo, a usable codex runner). Pure-ish (one GitHub GET);
  unit-tested with the network mocked.
- a runner self-check task (`TaskKind.preflight`, executed by `runner.run_preflight`)
  — the agent-facing bits the control plane can't see: that the runner host can
  `git push` to the repo and that `gh` is authenticated for comments.

`hive preflight <project>` runs the control-plane checks and (if they pass and a
codex runner is online) queues the runner self-check and reports its result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import httpx

from hive.github_repos import _GH_HEADERS, parse_repo_ref
from hive.issues import RESOLVE_BACKEND
from hive.models import Resource, Runner, Task, TaskKind, WorkSource


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hard: bool = True  # hard checks must pass to launch a run; soft ones are warnings


def repo_permissions(repo_ref: str, token: str) -> dict:
    """The token's permissions on the repo (the `permissions` block GitHub returns
    for an authenticated GET /repos/{owner}/{repo})."""
    full = parse_repo_ref(repo_ref)
    response = httpx.get(
        f"https://api.github.com/repos/{full}",
        headers={**_GH_HEADERS, "Authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    response.raise_for_status()
    data = response.json()
    perms = data.get("permissions") or {}
    return {
        "full_name": data.get("full_name", full),
        "push": bool(perms.get("push") or perms.get("maintain") or perms.get("admin")),
        "has_issues": bool(data.get("has_issues", True)),
        "default_branch": str(data.get("default_branch") or "main"),
    }


def codex_runner_usable(store, workspace_id: str, backend: str = RESOLVE_BACKEND) -> bool:
    online = {r.id for r in store.list(Runner, workspace_id=workspace_id) if r.online()}
    return any(
        res.backend == backend and res.available() and res.runner_id in online
        for res in store.list(Resource, workspace_id=workspace_id)
    )


def preflight_checks(store, config, project) -> list[Check]:
    checks: list[Check] = []
    is_issues = project.work_source == WorkSource.issues
    checks.append(
        Check("issues_mode", is_issues, f"work_source={project.work_source}"
              + ("" if is_issues else " (set it to 'issues')"))
    )
    has_repo = bool(project.spec_repo.strip())
    checks.append(Check("spec_repo_set", has_repo, project.spec_repo or "no spec_repo configured"))
    token = config.gh_token
    checks.append(
        Check(
            "gh_token_present",
            bool(token),
            "control-plane GitHub token present"
            if token
            else "HIVE_GH_TOKEN not set — needed to fetch issues, download images, merge, and close",
        )
    )

    if token and has_repo:
        try:
            perms = repo_permissions(project.spec_repo, token)
        except (httpx.HTTPError, LookupError, PermissionError) as exc:
            checks.append(Check("repo_write_access", False, f"could not read the repo with this token: {exc}"))
        else:
            checks.append(
                Check(
                    "repo_write_access",
                    perms["push"],
                    f"token can push/merge to {perms['full_name']} (default branch {perms['default_branch']})"
                    if perms["push"]
                    else f"token lacks write access to {perms['full_name']} — merge-on-accept and issue close will fail",
                )
            )
            checks.append(
                Check(
                    "issues_enabled",
                    perms["has_issues"],
                    "GitHub Issues enabled" if perms["has_issues"] else "GitHub Issues disabled on the repo",
                    hard=False,
                )
            )

    issue_backend = config.issue_backend or RESOLVE_BACKEND
    runner_ok = codex_runner_usable(store, project.workspace_id, backend=issue_backend)
    checks.append(
        Check(
            "codex_runner_usable",
            runner_ok,
            f"an online runner offers a usable '{issue_backend}' resource"
            if runner_ok
            else f"no online runner with a probed-usable '{issue_backend}' resource — tasks will wait",
            hard=False,  # a runner can come online after scanning; don't block, warn
        )
    )
    return checks


def create_preflight_task(store, project, backend: str = RESOLVE_BACKEND, model: str = "") -> Task:
    """Queue the runner self-check (push + gh auth) against the spec repo."""
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id="",
            repo=project.spec_repo,
            kind=TaskKind.preflight,
            backend=backend,
            model=model,
            instructions="Hive runner self-check: verify git push and gh auth against the spec repo.",
        )
    )


def checks_payload(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
