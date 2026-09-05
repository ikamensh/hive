"""Live, bounded acceptance check for free OpenCode planning.

Run with `uv run python scripts/smoke_opencode_planner.py`. Uses free Muse by
default, an included-only/$0 in-memory project, and a throwaway local Git spec
remote. The real Orchestrator.invoke builds its own snapshot and exposes every
planner tool. No real repository or project is changed. Success means one
small draft and a final response within three rounds, with zero runnable work.
"""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

from hive._control.orchestrator import Orchestrator
from hive.config.settings import Config
from hive.llm._opencode import DEFAULT_OPENCODE_MODEL
from hive.models import HumanTask, OrchestratorRun, Plan, PlanItem, Project, Question, Task
from hive.persistence.blobstore import LocalBlobStore
from hive.persistence.store import MemoryStore


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True,
                            check=True, timeout=30)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_OPENCODE_MODEL)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="hive-muse-planner-smoke-") as directory:
        root = Path(directory)
        remote = root / "spec.git"
        git("init", "--bare", "--initial-branch=main", str(remote), cwd=root)
        checkout = root / "seed"
        git("clone", str(remote), str(checkout), cwd=root)
        (checkout / "mission.md").write_text(
            "# Mission\nA tiny deterministic Python arithmetic package for local use. "
            "Code and specifications live in this repository. Use only the standard library "
            "and unittest; there are no external services or credentials to configure.\n"
        )
        (checkout / "iteration.md").write_text("# Iteration\nNo iteration has been planned yet.\n")
        git("add", ".", cwd=checkout)
        git("-c", "user.name=Hive smoke", "-c", "user.email=smoke@localhost",
            "commit", "-m", "Initial arithmetic mission", cwd=checkout)
        git("push", "origin", "main", cwd=checkout)

        store = MemoryStore()
        project = store.put(Project(name="smoke-arithmetic", spec_repo=str(remote),
                                    included_only=True, daily_budget_usd=0))
        config = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
                        orch_model=args.model, runner_token="", data_dir=root,
                        orch_provider="opencode")
        blobs = LocalBlobStore(root / "blobs")
        planner = Orchestrator(store, blobs, config)
        started = time.monotonic()
        # The normal caller and tool loop run unchanged, with a small smoke budget.
        with patch("hive._control.orchestrator.MAX_REMOTE_CALLS", 3):
            planner.invoke(project.id, [
                "New iteration goal from the human: Ship a small Python arithmetic package "
                "with add(a, b) and multiply(a, b), regression tests, and a README example. "
                "The functions accept finite integers and floats; ordinary Python numeric "
                "behavior is sufficient."
            ])

        plans = store.list(Plan, project_id=project.id)
        items = store.list(PlanItem, project_id=project.id)
        run = store.list(OrchestratorRun, project_id=project.id)[0]
        final = planner._load_history(project.id)[-1]["text"]
        evidence = {
            "model": run.model,
            "included_only": project.included_only,
            "daily_budget_usd": project.daily_budget_usd,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "plans": [{"id": plan.id, "status": plan.status} for plan in plans],
            "items": [item.title for item in items],
            "execution_tasks": len(store.list(Task, project_id=project.id)),
            "questions": len(store.list(Question, project_id=project.id)),
            "human_tasks": len(store.list(HumanTask)),
            "spec_commits": git("log", "--format=%s", cwd=remote).splitlines(),
            "final_text": final,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "cost_usd": run.cost_usd,
        }
        print(json.dumps(evidence, indent=2), flush=True)
        assert final != "Stopped after maximum orchestrator tool-call rounds.", (
            "planner did not finish within three rounds"
        )
        assert len(plans) == 1 and plans[0].status == "draft", "planner repeated or replaced its draft"
        assert 1 <= len(items) <= 3, "small arithmetic iteration needs a focused plan"
        assert not evidence["execution_tasks"], "a draft must not execute"
        assert run.cost_usd == 0, "included-only planning must not incur API spend"


if __name__ == "__main__":
    main()
