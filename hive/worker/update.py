"""Self-update trigger for workers that run from a git service clone."""

from __future__ import annotations

import subprocess
from pathlib import Path


def update_available(repo_root: Path) -> bool:
    """True when origin/main can fast-forward this checkout to newer code.

    A locally upgraded or divergent checkout must not restart into a downgrade.
    Git or network trouble still leaves a working worker running.
    """
    try:
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", "main"],
            cwd=repo_root, check=True, capture_output=True, timeout=60,
        )
        revs = subprocess.run(
            ["git", "rev-parse", "HEAD", "FETCH_HEAD"],
            cwd=repo_root, check=True, capture_output=True, text=True, timeout=10,
        ).stdout.split()
        if len(revs) != 2 or revs[0] == revs[1]:
            return False
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor", *revs],
            cwd=repo_root, capture_output=True, timeout=10,
        ).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False
