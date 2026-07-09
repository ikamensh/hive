from pathlib import Path

from hive._control.decisions import ASSUMED_SOURCE_TYPES, parse_decision_ledger


def test_checked_in_hive_assumptions_have_expiry():
    """The checked-in spec ledger feeds the UI. Hive-made assumptions need an
    expiry condition so they are visibly reversible instead of looking like
    unresolved questions or durable human decisions."""
    ledger = parse_decision_ledger(Path("wiki/decisions.md").read_text())

    missing = [
        decision.id
        for decision in ledger.decisions
        if decision.source_type in ASSUMED_SOURCE_TYPES and not decision.expires_when.strip()
    ]

    assert missing == []
    assert ledger.error == ""


def test_assumed_decision_without_expiry_is_reported():
    """Projection should not silently normalize a Hive assumption that lacks the
    provenance fields required by the decision-authority story."""
    ledger = parse_decision_ledger(
        "\n".join(
            [
                "## D-001 · Retry policy",
                "source_type: agent_proposed",
                "impact: medium · reversibility: high · status: accepted_for_iteration",
                "expires_when:",
                "",
                "Retry for 24 hours.",
            ]
        )
    )

    assert ledger.decisions[0].expires_when == ""
    assert ledger.error == "Hive assumption D-001 is missing expires_when"
