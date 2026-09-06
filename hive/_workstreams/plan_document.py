"""Portable plan instructions, independent of author and execution location.

The canonical file is TOML. Prose uses multiline strings, so its own headings
and code fences remain unambiguous.
Simple ``# goal`` / ``## task`` Markdown remains convenient for initial import.
"""

import json
import re
import tomllib
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class PlanDocumentItem(BaseModel):
    """Authored instructions; runtime status never belongs in this document."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = ""  # A standalone item may defer identity until it is added.
    title: str
    repo: str = ""
    story: str = ""
    constraints: str = ""
    notes: str = ""
    story_keys: list[str] = Field(default_factory=list)

    _required_text = field_validator("key", "title")(_not_blank)


class PlanDocument(BaseModel):
    """A draft, or an amendment based on a particular accepted revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(default=1, ge=1, le=1)
    plan_id: str = ""
    revision: int = Field(default=0, ge=0)
    goal: str
    context_documents: dict[str, str] = Field(default_factory=dict)
    repo_baselines: dict[str, str] = Field(default_factory=dict)
    items: list[PlanDocumentItem] = Field(default_factory=list)

    _required_text = field_validator("goal")(_not_blank)

    @field_validator("items", mode="before")
    @classmethod
    def assign_item_keys(cls, items):
        if isinstance(items, list):
            items = [item.model_dump(exclude_unset=True) if isinstance(item, PlanDocumentItem)
                     else item for item in items]
            return [{"key": f"item-{index}", **item} if isinstance(item, dict) else item
                    for index, item in enumerate(items, 1)]
        return items

    @field_validator("items")
    @classmethod
    def unique_item_keys(cls, items):
        keys = [item.key for item in items]
        if len(keys) != len(set(keys)):
            raise ValueError("Plan item keys must be unique")
        return items


def _toml_string(value: str | list[str]) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_prose(value: str) -> str:
    # Escape each line to preserve real newlines without confusing literal \n.
    escaped = "\n".join(_toml_string(line)[1:-1] for line in value.split("\n"))
    return '"""\n' + escaped + '"""'


def render_plan_document(document: PlanDocument | dict) -> str:
    """Render an editable, lossless plan file, validating all input fields."""
    document = PlanDocument.model_validate(document)
    lines = [
        "version = 1",
        f"plan_id = {_toml_string(document.plan_id)}",
        f"revision = {document.revision}",
        f"goal = {_toml_prose(document.goal)}",
    ]
    if not document.items:
        lines.append("items = []")
    for field in ("context_documents", "repo_baselines"):
        mapping = getattr(document, field)
        if mapping:
            lines.extend(["", f"[{field}]"])
            lines.extend(f"{_toml_string(key)} = {_toml_prose(value)}"
                         for key, value in mapping.items())
    for item in document.items:
        lines.extend(["", "[[items]]"])
        for field in ("key", "title", "repo"):
            lines.append(f"{field} = {_toml_string(getattr(item, field))}")
        lines.append(f"story_keys = {_toml_string(item.story_keys)}")
        for field in ("story", "constraints", "notes"):
            lines.append(f"{field} = {_toml_prose(getattr(item, field))}")
    return "\n".join(lines) + "\n"


def parse_plan_document(text: str) -> dict:
    """Read canonical instructions or the simple Markdown import convention."""
    for line in text.splitlines():
        if line.startswith("## "):
            break  # A task heading establishes the simple Markdown convention.
        statement = line.strip()
        if statement and not statement.startswith("#"):
            if re.match(r'(?:[\w"\']+\s*=|\[)', statement):
                return PlanDocument.model_validate(tomllib.loads(text)).model_dump()
            break

    goal, items, body = [], [], []
    title = None
    fence = ""

    def finish_item():
        if title is not None:
            items.append({"title": title, "notes": "\n".join(body).strip()})

    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            (body if title is not None else goal).append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            continue
        if marker:
            fence = marker[1]
        if not fence and line.startswith("## "):
            finish_item()
            title, body = line[3:].strip(), []
            if not title:
                raise ValueError("Each ## task heading needs a title")
        elif title is None:
            goal.append(line[2:] if line.startswith("# ") else line)
        else:
            body.append(line)
    finish_item()
    if not items or not "\n".join(goal).strip():
        raise ValueError("Use a # goal followed by one or more ## task headings and their instructions")
    return {"goal": "\n".join(goal).strip(), "items": items}
