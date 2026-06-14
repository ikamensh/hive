"""Token pricing estimates, used to turn the orchestrator's own LLM usage into
dollars so its spend counts against a project's daily budget.

USD per 1M tokens, (input, output), sampled 2026-06-10 from provider pricing
pages. Refresh occasionally. Matched by longest model-name prefix, so
`gpt-5.5-codex` resolves to the `gpt-5.5` entry. Unknown models cost 0 — the
tokens are still recorded, so a missing entry under-counts rather than crashes;
add an entry when a new orchestrator model appears.
"""

from __future__ import annotations

PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.5": (1.25, 10.0),
    "gpt-5.4": (1.25, 10.0),
    "gpt-5": (1.25, 10.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "gemini-3": (2.0, 12.0),
    "gemini-2": (1.25, 10.0),
    # cursor backend bills the subscription, not per-token.
    "composer-2.5": (0.0, 0.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD for a call. Longest-prefix match against PRICING; 0 for
    unknown models (tokens are recorded regardless)."""
    name = (model or "").lower()
    match = max((p for p in PRICING if name.startswith(p)), key=len, default="")
    if not match:
        return 0.0
    in_rate, out_rate = PRICING[match]
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
