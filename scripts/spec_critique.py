"""Run the spec critique (wiki/spec-critique.md) locally with kodo CLI agents.

Critics run in parallel on one backend; the adjudicator runs cross-model to
counter self-bias. Writes a markdown report and prints the batched inbox
question. Usage: `uv run python scripts/spec_critique.py`.
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hive.critique import critique, report_markdown
from hive.runner import make_session
from hive.specrepo import digest_dir

# -- inputs -------------------------------------------------------------------
SPEC_DIR = Path(__file__).parent.parent  # hive is its own spec home
CRITIC_BACKEND, CRITIC_MODEL = "codex", ""  # backend default model
ADJUDICATOR_BACKEND, ADJUDICATOR_MODEL = "cursor", "composer-2.5"
GUESS_PROPENSITY = "sometimes"
MAX_QUESTIONS = 7
AGENT_TIMEOUT_S = 900
REPORT_PATH = Path(__file__).parent / "spec_critique_report.md"
# ------------------------------------------------------------------------------

log = logging.getLogger("spec_critique")


def make_llm(backend: str, model: str):
    from kodo.agent import Agent

    def run(prompt: str) -> str:
        with Agent(make_session(backend, model), max_turns=20, timeout_s=AGENT_TIMEOUT_S) as agent:
            result = agent.run(prompt, SPEC_DIR, agent_name=f"critique-{backend}")
        if result.is_error:
            raise RuntimeError(f"{backend} agent failed: {result.text[:1000]}")
        log.info("%s call done (%.2f USD)", backend, result.query.cost_usd or 0.0)
        return result.text

    return run


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    digest = digest_dir(SPEC_DIR)
    log.info("spec digest: %d chars from %s", len(digest), SPEC_DIR)

    start = time.time()
    report = critique(
        digest,
        critic_llm=make_llm(CRITIC_BACKEND, CRITIC_MODEL),
        adjudicator_llm=make_llm(ADJUDICATOR_BACKEND, ADJUDICATOR_MODEL),
        guess_propensity=GUESS_PROPENSITY,
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
