"""Portable plan files preserve the complete instructions accepted by Hive."""

import tomllib

import pytest
from pydantic import ValidationError

from hive._workstreams.plan_document import (
    PlanDocument,
    PlanDocumentItem,
    parse_plan_document,
    render_plan_document,
)


def test_plan_file_round_trip_preserves_structured_instructions():
    """Both planning authors can exchange all fields without flattening prose."""
    document = PlanDocument.model_validate({
        "version": 1,
        "plan_id": "plan-42",
        "revision": 7,
        "goal": "Deliver exports\n\nKeep the existing API.",
        "context_documents": {"decisions.md": "# Decisions\nUse UTF-8.\n"},
        "repo_baselines": {"https://github.com/example/api.git": "abc123"},
        "items": [{
            "key": "csv-export",
            "title": "Add CSV export",
            "repo": "https://github.com/example/api.git",
            "story": "As a user, I can export records.",
            "constraints": "Use the standard library.\nNo new service.",
            "notes": "## Design\n\nPreserve Unicode: café.\n\n```python\nprint('ok')\n```\n",
            "story_keys": ["export-headers", "export-unicode"],
        }],
    })

    encoded = render_plan_document(document)

    assert PlanDocument.model_validate(parse_plan_document(encoded)) == document
    assert tomllib.loads(encoded) == document.model_dump()
    assert "## Design\n\nPreserve Unicode: café." in encoded


def test_plain_markdown_normalizes_to_stable_item_keys():
    """A convenient initial draft becomes canonical without losing item order."""
    text = "# Goal\n\n## First\nDo A.\n\n## Second\nDo B.\n"
    document = PlanDocument.model_validate(parse_plan_document(text))

    assert [item.key for item in document.items] == ["item-1", "item-2"]
    assert PlanDocument.model_validate(parse_plan_document(render_plan_document(document))) == document


@pytest.mark.parametrize("changes", [
    {"version": 2},
    {"version": True},
    {"version": 1.0},
    {"surprise": "must not disappear"},
    {"goal": " \n "},
    {"revision": -1},
    {"items": [{"key": "same", "title": "A"}, {"key": "same", "title": "B"}]},
    {"items": [{"key": "", "title": "A"}]},
    {"items": [{"key": "a", "title": " "}]},
    {"items": [{"key": "a", "title": "A", "surprise": "must not disappear"}]},
    {"revision": "7"},
    {"items": [{"key": "a", "title": "A", "notes": 7}]},
])
def test_invalid_instructions_are_rejected(changes):
    """Unsafe identities and misspelled fields cannot silently change the handoff."""
    with pytest.raises(ValidationError):
        render_plan_document({"goal": "Goal", "items": [{"title": "A"}], **changes})


@pytest.mark.parametrize("prose", [
    "",
    "\nLeading and trailing newlines\n\n",
    '"""literal triple quotes""" and backslash \\n',
    "Backslash at end of line\\\nnext line",
    "Windows\r\nnewlines\tremain exact\r",
    "Control characters: \0\b\f\x1f\x7f",
    "~~~toml\nversion = 99\n~~~\n```\n## not an item\n```",
])
def test_arbitrary_prose_round_trips_exactly(prose):
    """TOML quoting must preserve code examples and significant whitespace."""
    document = PlanDocument(goal="Goal", items=[{"title": "A", "notes": prose}])
    decoded = PlanDocument.model_validate(parse_plan_document(render_plan_document(document)))

    assert decoded.items[0].notes == prose


def test_empty_draft_can_be_exported_before_items_are_written():
    """A planner or editor can exchange a draft while assembling its work list."""
    document = PlanDocument(goal="Goal", items=[])

    assert PlanDocument.model_validate(parse_plan_document(render_plan_document(document))) == document


@pytest.mark.parametrize("notes", ["[ ] Add tests", 'setting = "value"'])
def test_plain_markdown_instructions_are_not_mistaken_for_toml(notes):
    """Initial Markdown task bodies may naturally begin with TOML-like text."""
    document = parse_plan_document(f"# Goal\n\n## Task\n{notes}\n")

    assert document["items"][0]["notes"] == notes


def test_item_builder_can_defer_its_key_to_the_containing_plan():
    """Appending a single item lets the plan assign the next durable identity."""
    item = PlanDocumentItem(title="New work")

    assert item.key == ""
    assert PlanDocument(goal="Goal", items=[item]).items[0].key == "item-1"
