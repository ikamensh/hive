"""Runner-side quota / rate-limit detection."""

from hive.agents.backends import EXHAUSTION_PATTERNS
from hive.agents import classify_failure

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

# Captured live from a codex run on the hive-vm runner when the account lapsed.
# It tripped neither pattern, so the resource stayed "usable" and every issue
# task re-dispatched onto the dead credential and failed — a human-fix block, not
# a self-healing quota window.
CODEX_BILLING_BLOCK = "codex: Subscription/billing issue — check your account status."

# Captured live 3x during the droid-tally demo (2026-07-16): gemini-cli's flash
# stream dying mid-turn. A one-off flake, not a credential or quota problem.
GEMINI_INVALID_STREAM = (
    "Invalid stream: The model returned an empty response or malformed tool call."
)


def _resource_exhausted(text: str, *, is_error: bool = True) -> bool:
    """Mirror the flag the runner sets on task results."""
    return bool(is_error and EXHAUSTION_PATTERNS.search(text))


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


# `classify_failure` is the single decision the runner makes about a failed
# result: a login/policy block needs a human (auth), a spent quota window heals
# on its own (exhausted), everything else is just a failure.


def test_claude_subscription_block_classifies_as_auth():
    # The exact message that wedged the hive intake: a policy block, not a quota.
    assert classify_failure(CLAUDE_SUBSCRIPTION_DISABLED, is_error=True) == "auth"


def test_codex_quota_classifies_as_exhausted_not_auth():
    assert classify_failure(CODEX_QUOTA_ERROR, is_error=True) == "exhausted"
    assert classify_failure(CODEX_RATE_LIMIT_JSON, is_error=True) == "exhausted"


def test_billing_block_classifies_as_auth_not_exhausted():
    # A lapsed account needs a human to fix billing; it must not be cooled down
    # and retried like a quota window, or work loops forever on a dead credential.
    assert classify_failure(CODEX_BILLING_BLOCK, is_error=True) == "auth"
    assert classify_failure("Payment required: your card was declined.", is_error=True) == "auth"
    assert classify_failure("Account suspended — past due balance.", is_error=True) == "auth"


def test_billing_block_is_not_a_temporary_quota():
    # Guards the original gap: it must not silently fall through to "" (no-op).
    assert _resource_exhausted(CODEX_BILLING_BLOCK) is False
    assert classify_failure(CODEX_BILLING_BLOCK, is_error=True) != ""


def test_auth_wins_when_message_trips_both():
    # A throttle note that also asks to re-login must escalate, not silently cool down.
    assert classify_failure("rate limited; please re-login to continue", is_error=True) == "auth"


def test_login_required_classifies_as_auth():
    assert classify_failure("codex login required", is_error=True) == "auth"
    assert classify_failure("Error: not authenticated. Run `claude login`.", is_error=True) == "auth"


def test_plain_failure_is_neither():
    assert classify_failure("AssertionError: expected 2 got 3", is_error=True) == ""


def test_success_is_never_classified():
    assert classify_failure(CLAUDE_SUBSCRIPTION_DISABLED, is_error=False) == ""


# "transient" marks one-off backend flakes the chief may requeue automatically.
# Property: it must only ever claim errors that neither auth nor exhaustion
# claims — a retry loop on a dead credential or a spent window would spin.


def test_backend_flakes_classify_as_transient():
    for text in (
        GEMINI_INVALID_STREAM,
        "stream disconnected before completion: retry later",
        "500 Internal error encountered.",
        "API returned 529: overloaded_error",
        "upstream connect error: connection reset by peer",
        "503 Service Unavailable",
    ):
        assert classify_failure(text, is_error=True) == "transient", text


def test_transient_never_outranks_auth_or_exhaustion():
    # The same stream error plus a login demand is an auth block; plus a quota
    # note it is exhaustion — both need their own handling, never a blind retry.
    assert classify_failure(GEMINI_INVALID_STREAM + " Please log in again.", is_error=True) == "auth"
    assert classify_failure(GEMINI_INVALID_STREAM + " (429 Too Many Requests)", is_error=True) == "exhausted"


def test_transient_requires_an_error():
    assert classify_failure(GEMINI_INVALID_STREAM, is_error=False) == ""
