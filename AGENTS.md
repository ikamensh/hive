# Project state and priorities

The project is in the early design phase; all the code is subject to fundamental rework and improvement. We therefore do not care about backward compatibility or stability, we need to move fast and make the current version maximally useful, logical and simple.

# Implementation notes

This file is the lean map (always loaded). Per-file detail is in `wiki/code-map.md` — read it when navigating into a subpackage. Architecture rationale (the "why") is in `wiki/architecture.md`.

## Layout

Files are grouped into subpackages by responsibility — the folder name tells you what lives there:

```
hive/
  models.py            domain vocabulary (imported everywhere)
  api.py  cli.py       the two entrypoints
  config/              how this install is configured (settings, machine-local file)
  persistence/         where data lives (store, storage selection, blobstore)
  llm/                 talking to models (adapters, prompts/, pricing, model_intel)
  control/             the brain: supervisor, orchestrator, escalation
  workstreams/         the work pipelines: issues, testing, critique, preflight
  runner/              the execution side: daemon, local, backends, *_results, machine
  integrations/        external services: github_repos, specrepo, auth
```

For what each module does and how the pipelines wire together, see `wiki/code-map.md`.

## Conventions

- Tests use `MemoryStore` + scripted orchestrator; no network, no LLM. Real-LLM behavior is checked with `scripts/smoke_orchestrator.py` (needs `OPENAI_API_KEY` or `GEMINI_API_KEY`).
- Secrets live in GCP Secret Manager (`hive-gemini-api-key`, `hive-gh-token`, `hive-runner-token`, `hive-openai-api-key`); the VM startup script materializes `/etc/hive/env`.
- GCP project `hive-ikamen`, Firestore `(default)` + bucket `hive-ikamen-blobs`, both `europe-west1`.

## Running

```bash
uv run pytest tests/                  # unit + mocked e2e
uvicorn --factory hive.api:production_app   # control plane (env: HIVE_GCP_PROJECT etc.)
python -m hive.runner                 # runner (env: HIVE_URL, HIVE_RUNNER_TOKEN)
bash deploy/create_vm.sh              # create/refresh the VM
bash scripts/laptop_runner.sh         # laptop runner via SSH tunnel
```
