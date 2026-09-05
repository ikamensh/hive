"""Launch the isolated acceptance install with subscription/free agent capacity.

Run from the Hive checkout: uv run python experiments/production-simulator/run_local.py
CLI commands in another terminal use `uv run hive --local ...`.
"""

import json
import os
from pathlib import Path


def main():
    state = Path.home() / ".local/share/hive/experiments/production-simulator"
    state.mkdir(parents=True, exist_ok=True)
    paid_credentials = {
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
    }
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("HIVE_") and key not in paid_credentials}
    free_model = "opencode/muse-spark-1.3-contributor-free"
    env.update(
        HIVE_CONFIG_FILE=str(state / "config.env"),
        HIVE_RUNNER_NAME="molding-foundry-local",
        HIVE_RUNNER_BACKENDS="codex,claude,opencode",
        HIVE_ISSUE_BACKEND="codex",
        HIVE_ISSUE_MODEL="gpt-6-astra",
        HIVE_OPENCODE_MODEL=free_model,
        # Title generation must use the same free provider too.
        OPENCODE_CONFIG_CONTENT=json.dumps({"small_model": free_model, "share": "disabled"}),
        KODO_RUNS_DIR=str(state / "agent-runs"),
        PYTHONUNBUFFERED="1",
    )
    os.execvpe("uv", ["uv", "run", "hive", "run", "--local", "--data-dir", str(state),
                        "--no-web-build"], env)


if __name__ == "__main__":
    main()
