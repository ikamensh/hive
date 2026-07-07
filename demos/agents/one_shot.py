"""Demo: one structured agent task, one call — `hive.agents` standalone.

Task: point a coding agent at a directory and get back a *machine-readable*
answer, not prose you have to regex. We build a tiny throwaway repo, ask the
agent to survey it, and demand a JSON result validating against a Pydantic
model — the agent writes `.hive/result.json` and gets validation errors back
to repair until it fits.

    uv run python demos/agents/one_shot.py [backend]

Needs that backend's CLI installed and logged in (default: first installed
one); spends one small agent turn. Everything here is the same interface
hive's runner daemon runs production tasks through: `run_agent` + `ResultSpec`.
"""

import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel

from hive.agents import detected_backend_names, run_agent


class RepoSurvey(BaseModel):
    task_id: str
    summary: str
    file_count: int
    languages: list[str]


def build_repo(root: Path) -> Path:
    repo = root / "toy-repo"
    repo.mkdir()
    (repo / "hello.py").write_text("print('hello')\n")
    (repo / "util.sh").write_text("echo hi\n")
    (repo / "README.md").write_text("# Toy repo\nTwo tiny scripts.\n")
    return repo


installed = detected_backend_names()
backend = sys.argv[1] if len(sys.argv) > 1 else (installed[0] if installed else "")
if not backend:
    raise SystemExit("no agent CLI installed — install claude, cursor, codex, or gemini-cli")

with tempfile.TemporaryDirectory() as tmp:
    repo = build_repo(Path(tmp))
    print(f"asking {backend} to survey {repo} ...")
    result = run_agent(
        backend,
        "Survey this directory: count the files and name the programming "
        "languages used. Do not modify anything.",
        repo,
        result_spec=RepoSurvey,
        task_id="demo-1",
        timeout_s=300.0,
    )

print(f"\nagent said:\n{result.text}\n")
if result.structured_result_error:
    print(f"structured result failed validation: {result.structured_result_error}")
else:
    survey = RepoSurvey.model_validate(result.structured_result)
    print(f"validated result: {survey.file_count} files, languages={survey.languages}")
    print(f"summary: {survey.summary}")
print(f"cost: ${result.cost_usd:.4f}, session handle: {result.session_handle or 'n/a'}")
