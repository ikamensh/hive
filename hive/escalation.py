"""The 'ask a human for help' primitive.

When Hive hits something only the operator can fix (a broken orchestrator
credential, a failed spec-repo write, a stale runner login) it files a
`HumanTask`. `escalate` makes that idempotent by (title, scope) so a condition
that recurs every tick produces one todo, not a flood.
"""

from __future__ import annotations

from hive.models import DEFAULT_WORKSPACE_ID, HumanTask, HumanTaskStatus


def escalate(
    store,
    title: str,
    instructions: str,
    project_id: str = "",
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> HumanTask | None:
    """File an operator todo unless an open one with the same title and scope
    already exists. `project_id=""` is an org-wide todo. Returns the task it
    filed, or None if a matching one was already open."""
    for task in store.list(HumanTask, workspace_id=workspace_id):
        if (
            task.status == HumanTaskStatus.open
            and task.title == title
            and task.project_id == project_id
        ):
            return None
    return store.put(
        HumanTask(
            workspace_id=workspace_id,
            project_id=project_id,
            title=title,
            instructions=instructions,
        )
    )
