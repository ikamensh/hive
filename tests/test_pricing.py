"""Token-pricing estimates that turn orchestrator usage into budget dollars."""

from hive.llm._pricing import estimate_cost


def test_longest_prefix_match_and_per_million_rate():
    # gpt-5.5: $1.25/Mtok in, $10/Mtok out.
    assert estimate_cost("gpt-5.5", 1_000_000, 1_000_000) == 1.25 + 10.0
    # A suffixed variant resolves to its family's prefix.
    assert estimate_cost("gpt-5.5-codex", 2_000_000, 0) == 2.5


def test_current_claude_rates_resolve_via_dotted_prefix():
    # The orchestrator's claude ids are dotted (claude-opus-4.8); they must
    # resolve to the "claude-opus-4" prefix at the current $5/$25 per Mtok.
    assert estimate_cost("claude-opus-4.8", 1_000_000, 1_000_000) == 5.0 + 25.0
    assert estimate_cost("claude-opus-4.6", 1_000_000, 0) == 5.0
    assert estimate_cost("claude-sonnet-4.6", 0, 1_000_000) == 15.0


def test_unknown_model_is_free_not_an_error():
    assert estimate_cost("some-future-model", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost("", 100, 100) == 0.0


def test_router_vendor_prefix_is_priced():
    """OpenRouter-style ids (vendor/model) price as the underlying model —
    a bridged planner must not report $0 spend all day (seen live)."""
    assert estimate_cost("openai/gpt-5.1", 1_000_000, 0) == estimate_cost("gpt-5.1", 1_000_000, 0) > 0
