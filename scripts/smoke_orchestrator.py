"""Smoke test: real orchestrator invocation against a local spec repo.

Creates a throwaway local spec home (bare repo) with a tiny mission/iteration,
invokes the orchestrator twice (project created → task finished), and prints
the actions taken. Requires an orchestrator provider credential such as
OPENAI_API_KEY or GEMINI_API_KEY.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from hive.persistence.blobstore import LocalBlobStore
from hive.config.settings import Config
from hive.models import Project, Question, Task, TaskStatus, Workstream
from hive._control.orchestrator import Orchestrator
from hive.persistence.store import MemoryStore

MODEL = os.environ.get("HIVE_ORCH_MODEL", "")
PROVIDER = os.environ.get("HIVE_ORCH_PROVIDER", "auto")

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")

tmp = Path(tempfile.mkdtemp(prefix="hive-smoke-"))
origin = tmp / "spec-origin.git"
subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
seed = tmp / "seed"
subprocess.run(["git", "clone", str(origin), str(seed)], check=True, capture_output=True)
(seed / "mission.md").write_text(
    "# Mission\nA tiny CLI tool `wordfreq` that prints the top-N most frequent words in a text file."
)
(seed / "iteration.md").write_text(
    "# Iteration 1\nworking `wordfreq FILE -n 10` CLI in Python with tests; "
    "repo: https://example.com/wordfreq.git"
)
subprocess.run(["git", "add", "-A"], cwd=seed, check=True)
subprocess.run(
    ["git", "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-qm", "seed"],
    cwd=seed, check=True,
)
subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed, check=True)

store = MemoryStore()
config = Config(
    gcp_project="", gcs_bucket="", gh_token="",
    gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
    orch_model=MODEL, runner_token="", data_dir=tmp / "data",
    orch_provider=PROVIDER,
    openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
    openai_base_url=os.environ.get(
        "HIVE_OPENAI_BASE_URL",
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    ),
)
orch = Orchestrator(store, LocalBlobStore(tmp / "blobs"), config)
project = store.put(Project(name="wordfreq", spec_repo=str(origin),
                            member_repos=["https://example.com/wordfreq.git"]))

print("\n=== invocation 1: project created ===")
orch.invoke(project.id, ["Project created. Plan the opening workstreams."])
for ws in store.list(Workstream):
    print(f"WS [{ws.status}] {ws.title}: {ws.description[:80]}")
for t in store.list(Task):
    print(f"TASK [{t.status}] {t.kind} {t.backend} repo={t.repo}\n  {t.instructions[:200]}")
for q in store.list(Question):
    print(f"QUESTION: {q.text[:300]}")

tasks = store.list(Task)
if tasks:
    t = tasks[0]
    t.status = TaskStatus.done
    t.result_text = "Implemented wordfreq CLI with argparse, 5 tests pass, pushed to main."
    store.put(t)
    print("\n=== invocation 2: task finished ===")
    orch.invoke(project.id, [f"work task {t.id} finished.\nResult:\n{t.result_text}"])
    for t2 in store.list(Task):
        print(f"TASK [{t2.status}] {t2.kind} {t2.backend}\n  {t2.instructions[:200]}")
    for q in store.list(Question):
        print(f"QUESTION: {q.text[:300]}")

print("\nGoal complete:", store.get(Project, project.id).goal_complete)
