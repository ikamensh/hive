"""Token-pricing estimates that turn orchestrator usage into budget dollars."""

from hive.pricing import estimate_cost


def test_longest_prefix_match_and_per_million_rate():
    # gpt-5.5: $1.25/Mtok in, $10/Mtok out.
    assert estimate_cost("gpt-5.5", 1_000_000, 1_000_000) == 1.25 + 10.0
    # A suffixed variant resolves to its family's prefix.
    assert estimate_cost("gpt-5.5-codex", 2_000_000, 0) == 2.5


def test_unknown_model_is_free_not_an_error():
    assert estimate_cost("some-future-model", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost("", 100, 100) == 0.0
