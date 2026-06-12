"""Prompt store: versioned base prompts, loaded from hive/prompts/*.md.

Each load returns (text, version) where version is a short content hash —
recorded on tasks so episodes can be tied to exact prompt versions (future
GEPA input). Per-project/per-user overlays will extend this post-MVP.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"


@cache
def load(name: str) -> tuple[str, str]:
    text = (PROMPT_DIR / f"{name}.md").read_text()
    return text, hashlib.sha256(text.encode()).hexdigest()[:8]
