"""Self-update trigger for workers that run from a git service clone."""

from __future__ import annotations

import subprocess
from pathlib import Path


def update_available(repo_root: Path) -> bool:
    """True when origin/main has commits this checkout lacks. Any git or
    network trouble reads as 'no update' — never take a working worker down
    over a fetch hiccup."""
    try:
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", "main"],
            cwd=repo_root, check=True, capture_output=True, timeout=60,
        )
        revs = subprocess.run(
            ["git", "rev-parse", "HEAD", "FETCH_HEAD"],
            cwd=repo_root, check=True, capture_output=True, text=True, timeout=10,
        ).stdout.split()
        return len(revs) == 2 and revs[0] != revs[1]
    except (subprocess.SubprocessError, OSError):
        return False
