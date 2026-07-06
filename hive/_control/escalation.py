"""The 'ask a human for help' primitive.

When Hive hits something only the operator can fix (a broken orchestrator
credential, a failed spec-repo write, a stale runner login) it files a
`HumanTask`. `escalate` makes that idempotent by (title, scope) so a condition
that recurs every tick produces one todo, not a flood.
"""

from __future__ import annotations

from hive.models import DEFAULT_WORKSPACE_ID, HumanTask, HumanTaskStatus, Machine


def machine_owner(store, machine_id: str) -> str:
    """The user who has hands on this machine — where its auth todos go."""
    machine = store.get(Machine, machine_id) if machine_id else None
    return machine.owner_user_id if machine else ""


def runner_machine_owner(store, runner) -> str:
    return machine_owner(store, runner.machine_id) if runner else ""


def escalate(
    store,
    title: str,
    instructions: str,
    project_id: str = "",
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    assignee_user_id: str = "",
) -> HumanTask | None:
    """File an operator todo unless an open one with the same title and scope
    already exists. `project_id=""` is an org-wide todo; `assignee_user_id`
    names the one user who can act (e.g. the machine owner for a login fix),
    empty means any admin. Returns the task it filed, or None if a matching
    one was already open (re-pointing its assignee if ownership changed)."""
    for task in store.list(HumanTask, workspace_id=workspace_id):
        if (
            task.status == HumanTaskStatus.open
            and task.title == title
            and task.project_id == project_id
        ):
            if task.assignee_user_id != assignee_user_id:
                task.assignee_user_id = assignee_user_id
                store.put(task)
            return None
    return store.put(
        HumanTask(
            workspace_id=workspace_id,
            project_id=project_id,
            assignee_user_id=assignee_user_id,
            title=title,
            instructions=instructions,
        )
    )
