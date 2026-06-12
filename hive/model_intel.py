"""Model intelligence estimates, used to assign roles in multi-model flows
(e.g. spec critique: every available model proposes, the smartest adjudicates).

Scores are the Artificial Analysis Intelligence Index v4.0 (sampled 2026-06-10,
https://artificialanalysis.ai/models/) at the reasoning effort we actually run.
composer-2.5 is not benchmarked by AA — subjective estimate. Refresh
occasionally; relative order matters more than absolute values.
"""

INTELLIGENCE: dict[str, float] = {
    # codex backend (xhigh per ~/.codex/config.toml)
    "gpt-5.5": 60.2,
    "gpt-5.4": 56.8,  # kodo CodexSession default
    "gpt-5.3-codex": 53.6,
    # claude backend (adaptive reasoning, max effort)
    "claude-opus-4.8": 61.4,
    "claude-opus-4.6": 53.0,
    "claude-sonnet-4.6": 51.7,
    # gemini-cli backend
    "gemini-3.1-pro": 57.2,
    "gemini-3-pro": 48.4,
    # cursor backend — unbenchmarked, guess
    "composer-2.5": 50.0,
}


def smartest(models: list[str]) -> str:
    """The model with the highest intelligence estimate. KeyError on unknown
    models — add an estimate rather than guessing silently."""
    return max(models, key=lambda m: INTELLIGENCE[m])
