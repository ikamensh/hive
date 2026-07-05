"""Spec-home repo access for the chief.

The orchestrator reads the spec digest (mission, iteration, wiki) from a local
shallow clone and writes distilled knowledge back via small commits. Auth uses
a GitHub token injected into the https URL; with no token, plain URLs work for
public repos and local paths work for tests.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DIGEST_FILES = ("mission.md", "iteration.md")
DIGEST_DIRS = ("wiki", "input-log")
REQUIRED_INTAKE_FILES = ("mission.md", "iteration.md")
# ~50k tokens. Not a context limit (models take far more) but an anti-bloat
# tripwire: a spec home this big needs distillation, not silent acceptance.
MAX_DIGEST_CHARS = 200_000


@dataclass(frozen=True)
class SpecStatus:
    required_files: tuple[str, ...]
    present_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    ready: bool
    error: str = ""

    def model_dump(self) -> dict:
        return {
            "required_files": list(self.required_files),
            "present_files": list(self.present_files),
            "missing_files": list(self.missing_files),
            "ready": self.ready,
            "error": self.error,
        }


def digest_dir(path: Path) -> str:
    """The whole spec (mission, iteration, wiki/*.md) concatenated — the
    canonical projection of the spec home for direct LLM calls."""
    parts: list[str] = []
    files = [path / f for f in DIGEST_FILES]
    for d in DIGEST_DIRS:
        if (path / d).is_dir():
            files.extend(sorted((path / d).glob("*.md")))
    for f in files:
        if f.exists():
            parts.append(f"=== {f.relative_to(path)} ===\n{f.read_text()}")
    text = "\n\n".join(parts)
    if len(text) > MAX_DIGEST_CHARS:
        raise RuntimeError(
            f"spec digest is {len(text)} chars (limit {MAX_DIGEST_CHARS}): "
            f"distill the wiki or add selective spec reading before growing further"
        )
    return text or "(spec repo is empty — no mission.md/iteration.md yet)"


def spec_status_dir(path: Path, required_files: tuple[str, ...] = REQUIRED_INTAKE_FILES) -> SpecStatus:
    present = tuple(
        rel
        for rel in required_files
        if (path / rel).is_file() and (path / rel).read_text().strip()
    )
    missing = tuple(rel for rel in required_files if rel not in present)
    return SpecStatus(
        required_files=required_files,
        present_files=present,
        missing_files=missing,
        ready=not missing,
    )


def _run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def authed_url(url: str, token: str) -> str:
    if url.startswith("git@github.com:"):
        # The fleet authenticates to GitHub with tokens over https; normalize an
        # ssh remote so a repo wired via its ssh_url still works on the chief.
        url = "https://github.com/" + url.removeprefix("git@github.com:")
    if token and url.startswith("https://"):
        return url.replace("https://", f"https://x-access-token:{token}@", 1)
    return url


class SpecRepo:
    def __init__(self, url: str, workdir: Path, token: str = "") -> None:
        self.url = url
        self.token = token
        slug = url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
        self.path = Path(workdir) / slug

    def sync(self) -> None:
        if self.path.exists():
            _run(["git", "fetch", "--depth", "1", "origin"], cwd=self.path)
            _run(["git", "reset", "--hard", "origin/HEAD"], cwd=self.path)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _run(["git", "clone", "--depth", "1", authed_url(self.url, self.token), str(self.path)])

    def digest(self) -> str:
        """Concatenated spec content for orchestrator context, size-capped."""
        return digest_dir(self.path)

    def commit_files(self, files: dict[str, str], message: str) -> str:
        """Write files (path -> content), commit, push. Returns commit sha."""
        self.sync()
        for rel_path, content in files.items():
            target = self.path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        _run(["git", "add", "-A"], cwd=self.path)
        _run(
            ["git", "-c", "user.name=hive", "-c", "user.email=hive@localhost",
             "commit", "-m", message],
            cwd=self.path,
        )
        _run(["git", "push", authed_url(self.url, self.token), "HEAD"], cwd=self.path)
        return _run(["git", "rev-parse", "HEAD"], cwd=self.path).strip()
