"""A Markdown plan: one # goal, ordered ## tasks, task bodies preserved verbatim."""

import re


def parse_plan_document(text: str) -> dict:
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
