"""Live, bounded regression for repeated OpenCode planner mutations.

Run with `uv run python scripts/smoke_opencode_planner.py`. Uses free Muse by
default and an isolated in-memory project. No repository or real project is
changed. Success means one two-item draft and a final response within three
rounds, even though the original state snapshot remains empty.
"""

import argparse
import json
from pathlib import Path
import tempfile

from hive._control.orchestrator import Orchestrator, Tools
from hive.config.settings import Config
from hive.llm import ToolSet, Usage
from hive.llm._opencode import DEFAULT_OPENCODE_MODEL, OpenCodeAdapter
from hive.models import Plan, PlanItem, Project, Task
from hive.persistence.blobstore import LocalBlobStore
from hive.persistence.store import MemoryStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_OPENCODE_MODEL)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="hive-muse-planner-smoke-") as root:
        store = MemoryStore()
        project = store.put(Project(name="smoke-arithmetic", spec_repo="https://example.invalid/smoke.git"))
        config = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="", orch_model=args.model,
                        runner_token="", data_dir=Path(root), orch_provider="opencode")
        surface = Tools(store, project, None)
        toolset = ToolSet(surface.functions())
        planner = Orchestrator(store, LocalBlobStore(Path(root) / "blobs"), config)
        adapter = OpenCodeAdapter(args.model)
        adapter.start(planner._system_prompt(project), [],
                      "The human explicitly requests a new two-item draft iteration plan: first implement "
                      "deterministic add(a,b), then deterministic multiply(a,b), both with tests. "
                      "Goal: a tiny tested arithmetic package. Call propose_plan once. Do not ask questions "
                      "or use any other tool. All state is empty and the human will approve later.", toolset)
        usage = Usage()
        for index in range(3):
            turn = adapter.step()
            usage += turn.usage
            print(json.dumps({"round": index + 1, "calls": [call.name for call in turn.tool_calls],
                              "text": turn.text}), flush=True)
            if turn.is_final:
                break
            results = [toolset.dispatch(call) for call in turn.tool_calls]
            print(json.dumps({"results": [result.content for result in results]}), flush=True)
            adapter.add_tool_results(results)
        plans = store.list(Plan, project_id=project.id)
        assert turn.is_final, "planner did not finish within three rounds"
        assert len(plans) == 1 and plans[0].status == "draft", "planner repeated or replaced its draft"
        assert len(store.list(PlanItem, project_id=project.id)) == 2
        assert not store.list(Task, project_id=project.id), "a draft must not execute"
        print(json.dumps({"model": adapter.model, "draft_items": 2, "execution_tasks": 0,
                          "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}))


if __name__ == "__main__":
    main()
