"""Hive runtime versioning.

Hive's release line is hand-written as ``MAJOR.MINOR`` and the patch number is
derived from Git history by counting commits. That gives every committed tree a
monotonic visible version without a pre-commit hook.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess

BASE_MAJOR = 0
BASE_MINOR = 1

FALLBACK_PATH = Path(__file__).with_name("_version_fallback.py")
VERSION_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<micro>\d+)"
    r"(?:\+(?P<meta>[0-9A-Za-z.-]+))?$"
)


@dataclass(frozen=True)
class VersionInfo:
    major: int
    minor: int
    micro: int
    commit: str = ""
    dirty: bool = False
    source: str = "fallback"

    @property
    def version(self) -> str:
        """Return the display/package version.

        >>> VersionInfo(0, 1, 42).version
        '0.1.42'
        >>> VersionInfo(0, 1, 42, commit="abc1234", dirty=True).version
        '0.1.42+abc1234.dirty'
        """
        base = f"{self.major}.{self.minor}.{self.micro}"
        metadata = [self.commit] if self.commit else []
        if self.dirty:
            metadata.append("dirty")
        return f"{base}+{'.'.join(metadata)}" if metadata else base

    @property
    def base_version(self) -> str:
        return f"{self.major}.{self.minor}"

    def payload(self) -> dict:
        return {
            "version": self.version,
            "base_version": self.base_version,
            "major": self.major,
            "minor": self.minor,
            "micro": self.micro,
            "commit": self.commit,
            "dirty": self.dirty,
            "source": self.source,
        }


def parse_version(value: str, *, source: str) -> VersionInfo:
    """Parse a Hive version string.

    >>> parse_version("0.1.42+abc1234.dirty", source="test").payload()["dirty"]
    True
    """
    match = VERSION_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid Hive version {value!r}")
    meta = (match.group("meta") or "").split(".") if match.group("meta") else []
    dirty = "dirty" in meta
    commit = next((part for part in meta if part and part != "dirty"), "")
    return VersionInfo(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        micro=int(match.group("micro")),
        commit=commit,
        dirty=dirty,
        source=source,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(args: list[str], root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def git_version(root: Path | None = None) -> VersionInfo | None:
    """Return the Git-derived version for *root*, or ``None`` outside Git."""
    root = root or _repo_root()
    try:
        count = int(_git(["rev-list", "--count", "HEAD"], root))
        commit = _git(["rev-parse", "--short=7", "HEAD"], root)
        dirty = bool(_git(["status", "--porcelain", "--untracked-files=no"], root))
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return None
    return VersionInfo(
        major=BASE_MAJOR,
        minor=BASE_MINOR,
        micro=count,
        commit=commit,
        dirty=dirty,
        source="git",
    )


def fallback_version() -> VersionInfo:
    from hive import _version_fallback

    info = parse_version(_version_fallback.__version__, source=getattr(_version_fallback, "SOURCE", "fallback"))
    commit = getattr(_version_fallback, "GIT_SHA", "")
    dirty = bool(getattr(_version_fallback, "DIRTY", False))
    return VersionInfo(
        major=info.major,
        minor=info.minor,
        micro=info.micro,
        commit=info.commit or commit,
        dirty=info.dirty or dirty,
        source=info.source,
    )


def select_version(git: VersionInfo | None, fallback: VersionInfo) -> VersionInfo:
    """Prefer Git, unless a generated fallback is clearly newer.

    This keeps local checkouts honest while allowing fast deploys that rsync
    source without ``.git`` to carry the version computed on the sending host.
    """
    if git is None:
        return fallback
    same_line = (git.major, git.minor) == (fallback.major, fallback.minor)
    if same_line and fallback.micro > git.micro:
        return fallback
    if same_line and fallback.micro == git.micro and fallback.dirty and not git.dirty:
        return fallback
    return git


def get_version_info(root: Path | None = None) -> VersionInfo:
    if override := os.environ.get("HIVE_VERSION", "").strip():
        return parse_version(override, source="env")
    return select_version(git_version(root), fallback_version())


def get_version(root: Path | None = None) -> str:
    return get_version_info(root).version


def version_payload(root: Path | None = None) -> dict:
    return get_version_info(root).payload()


def render_fallback(info: VersionInfo) -> str:
    return (
        '"""Generated fallback used when Hive runs without a Git checkout."""\n\n'
        f'__version__ = "{info.version}"\n'
        f'GIT_SHA = "{info.commit}"\n'
        f"DIRTY = {info.dirty!r}\n"
        'SOURCE = "generated-fallback"\n'
    )


def write_fallback(path: Path | None = None, root: Path | None = None) -> VersionInfo:
    if override := os.environ.get("HIVE_VERSION", "").strip():
        info = parse_version(override, source="env")
    else:
        info = git_version(root) or fallback_version()
    (path or FALLBACK_PATH).write_text(render_fallback(info))
    return info


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Show or write Hive's derived version")
    parser.add_argument("--write-fallback", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    info = write_fallback() if args.write_fallback else get_version_info()
    print(json.dumps(info.payload(), indent=2) if args.json else info.version)
