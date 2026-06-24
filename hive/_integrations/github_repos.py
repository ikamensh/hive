"""GitHub repo catalog + validation for the logged-in Hive user."""

from __future__ import annotations

import json
import re
import subprocess
import time

import httpx

_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
_CACHE_TTL_S = 120
_cache: dict[str, tuple[float, list[dict]]] = {}


def gh_token_for(login: str = "") -> str:
    """Token for *login* if gh knows that account, else the active gh account."""
    user = login.strip()
    if user:
        try:
            proc = subprocess.run(
                ["gh", "auth", "token", "-u", user],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            return ""
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _gh_token() -> str:
    return gh_token_for()


def _gh_active_login() -> str:
    try:
        proc = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _resolve_config_token(config_token: str = "", github_login: str = "") -> str:
    if config_token.strip():
        return config_token.strip()
    return gh_token_for(github_login)


def _shape(full_name: str, ssh_url: str, private: bool, description: str) -> dict:
    owner_repo = full_name.strip()
    clone_url = f"https://github.com/{owner_repo}.git" if owner_repo else ""
    return {
        "full_name": owner_repo,
        "ssh_url": ssh_url,
        "clone_url": clone_url,
        "private": private,
        "description": description[:200],
    }


def parse_repo_ref(text: str) -> str:
    """Normalize user input to owner/repo."""
    raw = text.strip()
    if not raw:
        raise ValueError("repo reference is required")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", raw):
        return raw
    ssh = re.match(r"^git@github\.com:([\w./-]+?)(?:\.git)?$", raw, re.I)
    if ssh:
        return ssh.group(1).removesuffix(".git")
    https = re.match(r"^https?://github\.com/([\w./-]+?)(?:\.git)?/?$", raw, re.I)
    if https:
        return https.group(1).removesuffix(".git")
    raise ValueError(f"not a GitHub repo: {raw}")


def _fetch_all_via_gh_cli() -> list[dict]:
    proc = subprocess.run(
        [
            "gh",
            "repo",
            "list",
            "--limit",
            "200",
            "--json",
            "nameWithOwner,sshUrl,isPrivate,description",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "gh repo list failed")
    raw = json.loads(proc.stdout or "[]")
    return [
        _shape(
            str(item.get("nameWithOwner", "")),
            str(item.get("sshUrl", "")),
            bool(item.get("isPrivate")),
            str(item.get("description") or ""),
        )
        for item in raw
    ]


def _fetch_all_via_api(token: str) -> list[dict]:
    headers = {**_GH_HEADERS, "Authorization": f"Bearer {token}"}
    fetched: list[dict] = []
    page = 1
    while page <= 3:
        response = httpx.get(
            "https://api.github.com/user/repos",
            params={
                "per_page": 100,
                "page": page,
                "sort": "updated",
                "affiliation": "owner,collaborator,organization_member",
            },
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        for repo in batch:
            fetched.append(
                _shape(
                    str(repo.get("full_name", "")),
                    str(repo.get("ssh_url", "")),
                    bool(repo.get("private")),
                    str(repo.get("description") or ""),
                )
            )
        if len(batch) < 100:
            break
        page += 1
    return fetched


def _fetch_all_repos(
    *,
    github_login: str = "",
    user_token: str = "",
    config_token: str = "",
) -> list[dict]:
    login = github_login.strip().lower()
    if user_token.strip():
        return _fetch_all_via_api(user_token.strip())

    active = _gh_active_login()
    if login and active and active.lower() != login:
        token = _resolve_config_token(config_token, login)
        if token:
            return _fetch_all_via_api(token)
        raise RuntimeError(
            f"gh is logged in as {active} but Hive session is {login} — "
            "sign out/in to Hive (GitHub login) or set HIVE_GH_TOKEN"
        )

    try:
        return _fetch_all_via_gh_cli()
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError):
        token = _resolve_config_token(config_token, login)
        if not token:
            raise RuntimeError(
                "GitHub unavailable — run `gh auth login` or set HIVE_GH_TOKEN"
            ) from None
        return _fetch_all_via_api(token)


def all_repos(
    *,
    github_login: str = "",
    user_token: str = "",
    config_token: str = "",
    force: bool = False,
) -> list[dict]:
    """Return repos for the Hive user, cached briefly per login."""
    key = github_login.strip().lower() or "_dev_"
    now = time.time()
    if not force and key in _cache and now - _cache[key][0] < _CACHE_TTL_S:
        return _cache[key][1]
    repos = _fetch_all_repos(
        github_login=github_login,
        user_token=user_token,
        config_token=config_token,
    )
    _cache[key] = (now, repos)
    return repos


def _view_via_gh_cli(full_name: str) -> dict:
    proc = subprocess.run(
        [
            "gh",
            "repo",
            "view",
            full_name,
            "--json",
            "nameWithOwner,sshUrl,isPrivate,description",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or "gh repo view failed"
        if "Could not resolve" in err or "not found" in err.lower():
            raise LookupError(f"repo not found: {full_name}") from None
        if "HTTP 403" in err or "Resource not accessible" in err:
            raise PermissionError(f"repo not accessible: {full_name}") from None
        raise RuntimeError(err)
    item = json.loads(proc.stdout or "{}")
    return _shape(
        str(item.get("nameWithOwner", full_name)),
        str(item.get("sshUrl", "")),
        bool(item.get("isPrivate")),
        str(item.get("description") or ""),
    )


def _view_via_api(full_name: str, token: str) -> dict:
    headers = {**_GH_HEADERS, "Authorization": f"Bearer {token}"}
    response = httpx.get(
        f"https://api.github.com/repos/{full_name}",
        headers=headers,
        timeout=15.0,
    )
    if response.status_code == 404:
        raise LookupError(f"repo not found: {full_name}") from None
    if response.status_code == 403:
        raise PermissionError(f"repo not accessible: {full_name}") from None
    response.raise_for_status()
    repo = response.json()
    return _shape(
        str(repo.get("full_name", full_name)),
        str(repo.get("ssh_url", "")),
        bool(repo.get("private")),
        str(repo.get("description") or ""),
    )


def validate_repo(
    ref: str,
    *,
    github_login: str = "",
    user_token: str = "",
    config_token: str = "",
) -> dict:
    """Check that *ref* exists and is reachable for this Hive user."""
    full_name = parse_repo_ref(ref)
    login = github_login.strip().lower()
    if user_token.strip():
        return _view_via_api(full_name, user_token.strip())

    active = _gh_active_login()
    if login and active and active.lower() != login:
        token = _resolve_config_token(config_token, login)
        if token:
            return _view_via_api(full_name, token)
        raise RuntimeError(
            f"gh is logged in as {active} but Hive session is {login} — "
            "sign out/in to Hive (GitHub login) or set HIVE_GH_TOKEN"
        )

    try:
        return _view_via_gh_cli(full_name)
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError):
        token = _resolve_config_token(config_token, login)
        if not token:
            raise RuntimeError(
                "GitHub unavailable — run `gh auth login` or set HIVE_GH_TOKEN"
            ) from None
        return _view_via_api(full_name, token)


def _create_via_api(name: str, token: str, *, private: bool, description: str) -> dict:
    headers = {**_GH_HEADERS, "Authorization": f"Bearer {token}"}
    response = httpx.post(
        "https://api.github.com/user/repos",
        json={
            "name": name,
            "private": private,
            "description": description,
            "auto_init": True,
        },
        headers=headers,
        timeout=30.0,
    )
    response.raise_for_status()
    repo = response.json()
    return _shape(
        str(repo.get("full_name", "")),
        str(repo.get("ssh_url", "")),
        bool(repo.get("private")),
        str(repo.get("description") or ""),
    )


def _create_via_gh_cli(name: str, *, private: bool, description: str) -> dict:
    args = [
        "gh",
        "repo",
        "create",
        name,
        "--confirm",
        "--clone=false",
    ]
    args.append("--private" if private else "--public")
    if description.strip():
        args.extend(["--description", description.strip()])
    proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "gh repo create failed")
    created = (proc.stdout or proc.stderr).strip().splitlines()[-1].strip()
    return validate_repo(created or name)


def create_repo(
    name: str,
    *,
    private: bool = True,
    description: str = "",
    github_login: str = "",
    user_token: str = "",
    config_token: str = "",
) -> dict:
    """Create a GitHub repo for greenfield intake."""
    repo_name = name.strip()
    if not re.fullmatch(r"[\w.-]+", repo_name):
        raise ValueError("repo name may contain only letters, numbers, ., _, and -")
    login = github_login.strip().lower()
    if user_token.strip():
        return _create_via_api(repo_name, user_token.strip(), private=private, description=description)
    active = _gh_active_login()
    if login and active and active.lower() != login:
        token = _resolve_config_token(config_token, login)
        if token:
            return _create_via_api(repo_name, token, private=private, description=description)
        raise RuntimeError(
            f"gh is logged in as {active} but Hive session is {login} — "
            "sign out/in to Hive (GitHub login) or set HIVE_GH_TOKEN"
        )
    try:
        return _create_via_gh_cli(repo_name, private=private, description=description)
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError):
        token = _resolve_config_token(config_token, login)
        if not token:
            raise RuntimeError("GitHub unavailable — run `gh auth login` or set HIVE_GH_TOKEN") from None
        return _create_via_api(repo_name, token, private=private, description=description)
