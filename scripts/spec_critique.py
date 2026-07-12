"""Run the spec critique (wiki/spec-critique.md) locally with CLI agents.

All models propose findings (every lens x every model); the smartest model
per hive/model_intel.py adjudicates. CLIs are invoked directly (subprocess)
rather than via kodo: kodo 0.5.0's codex parser predates the codex 0.139
JSON event format and drops the agent's reply. Writes a markdown report and
prints the batched inbox question. Usage: `uv run python scripts/spec_critique.py`.
"""

import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hive._workstreams.critique import critique, report_markdown
from hive.llm._model_intel import smartest
from hive._integrations.specrepo import digest_dir

# -- inputs -------------------------------------------------------------------
SPEC_DIR = Path(__file__).parent.parent  # hive is its own spec home
CODEX_MODEL = "gpt-5.5"  # what ~/.codex/config.toml runs; passed as -m
CURSOR_MODEL = "composer-2.5"
MAX_QUESTIONS = 7
AGENT_TIMEOUT_S = 900
REPORT_PATH = Path(__file__).parent / "spec_critique_report.md"
# ------------------------------------------------------------------------------

log = logging.getLogger("spec_critique")


def run_cli(cmd: list[str]) -> subprocess.CompletedProcess:
    start = time.time()
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=AGENT_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr[-1000:]}\n{proc.stdout[-1000:]}")
    log.info("%s call done in %.0fs", cmd[0], time.time() - start)
    return proc


def codex_llm(prompt: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".md") as out:
        run_cli(["codex", "exec", prompt, "--skip-git-repo-check", "--sandbox", "read-only",
                 "--cd", str(SPEC_DIR), "-m", CODEX_MODEL, "-o", out.name])
        return Path(out.name).read_text()


def cursor_llm(prompt: str) -> str:
    proc = run_cli(["cursor-agent", "-p", "-f", "--model", CURSOR_MODEL,
                    "--workspace", str(SPEC_DIR), prompt])
    return proc.stdout


LLMS = {CODEX_MODEL: codex_llm, CURSOR_MODEL: cursor_llm}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    digest = digest_dir(SPEC_DIR)
    log.info("spec digest: %d chars from %s", len(digest), SPEC_DIR)

    adjudicator = smartest(list(LLMS))
    log.info("critics: %s; adjudicator: %s", ", ".join(LLMS), adjudicator)

    start = time.time()
    report = critique(
        digest,
        critic_llms=LLMS,
        adjudicator_llm=LLMS[adjudicator],
        max_questions=MAX_QUESTIONS,
    )
    REPORT_PATH.write_text(report_markdown(report))

    actions = [v.action for v in report.verdicts]
    log.info(
        "done in %.0fs: %d findings -> %d ask / %d flag / %d drop; report: %s",
        time.time() - start, len(report.findings),
        actions.count("ask"), actions.count("flag"), actions.count("drop"), REPORT_PATH,
    )
    print("\n" + (report.inbox_markdown or "(no questions survived adjudication)"))


if __name__ == "__main__":
    main()
