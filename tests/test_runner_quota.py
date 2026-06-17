"""Runner-side quota / rate-limit detection."""

from hive.runner import EXHAUSTED_PATTERNS

# Captured from a real `codex exec` failure when the 5h window is spent.
CODEX_QUOTA_ERROR = (
    "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
    "to purchase more credits or try again at 3:28 PM."
)

# From codex API logs (github.com/openai/codex issues).
CODEX_RATE_LIMIT_JSON = (
    '{"error": {"message": "You\'ve exceeded the rate limit, please slow down '
    'and try again after 60.616292 seconds.", "code": "rate_limit_exceeded"}}'
)

CODEX_RETRY_LIMIT = (
    "exceeded retry limit, last status: 429 Too Many Requests, "
    "request id: 9bd33f31fd269bb1-HNL"
)

CLAUDE_SUBSCRIPTION_DISABLED = (
    "Your organization has disabled Claude subscription access for Claude Code · "
    "Use an Anthropic API key instead, or ask your admin to enable access"
)


def _resource_exhausted(text: str, *, is_error: bool = True) -> bool:
    """Mirror the flag the runner sets on task results."""
    return bool(is_error and EXHAUSTED_PATTERNS.search(text))


def test_codex_usage_limit_detected():
    assert _resource_exhausted(CODEX_QUOTA_ERROR)


def test_codex_rate_limit_json_detected():
    assert _resource_exhausted(CODEX_RATE_LIMIT_JSON)


def test_codex_retry_limit_detected():
    assert _resource_exhausted(CODEX_RETRY_LIMIT)


def test_claude_subscription_access_disabled_is_not_temporary_quota():
    assert not _resource_exhausted(CLAUDE_SUBSCRIPTION_DISABLED)


def test_success_not_exhausted():
    assert not _resource_exhausted("implemented feature, tests pass", is_error=False)
