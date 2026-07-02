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
  _control/            the brain: supervisor, orchestrator, escalation
  _workstreams/        the work pipelines: issues, testing, critique, preflight
  runner/              the execution side: daemon, local, backends, *_results, machine
  _integrations/       external services: github_repos, specrepo, auth
```

For what each module does and how the pipelines wire together, see `wiki/code-map.md`.

## Conventions

- Tests use `MemoryStore` + scripted orchestrator; no network, no LLM. Real-LLM behavior is checked with `scripts/smoke_orchestrator.py` (needs `OPENAI_API_KEY` or `GEMINI_API_KEY`).
- Secrets live in GCP Secret Manager (`hive-gemini-api-key`, `hive-gh-token`, `hive-runner-token`, `hive-openai-api-key`); the VM startup script materializes `/etc/hive/env`.
- GCP project `hive-ikamen`, Firestore `(default)` + bucket `hive-ikamen-blobs`, both `europe-west1`.

## Running

```bash
uv run pytest tests/                  # unit + mocked e2e
uvicorn --factory hive.api:production_app   # chief (env: HIVE_GCP_PROJECT etc.)
python -m hive.runner                 # runner (env: HIVE_URL, HIVE_RUNNER_TOKEN)
bash deploy/install_mac_runner.sh     # install a Mac as a launchd runner (once); serves Claude Max/Cursor
```

Runners are install-once services that self-register and long-poll the chief: the
VM runs `hive-runner` via systemd; a Mac/laptop runs `deploy/install_mac_runner.sh`
(launchd LaunchAgent — user session so the agent CLIs reach the login Keychain,
clean env, auto-start on login, KeepAlive restart, reconnect after sleep). Stable
runner name → deterministic machine id, so restarts reuse the same machine row.
Dispatch is backend-aware, so subscription-bound backends (Claude Max on the
laptop) are only ever assigned to the machine where they probed usable.

## Remote VM (chief + runner on GCE)

The remote install runs both the chief (`hive-chief`) and the runner
(`hive-runner`) **bare via systemd** — no Docker in the deploy loop (the image
rebuild was the tax; `deploy/Dockerfile`/`compose.yaml` are kept for a future
stability mode). Coordinates default to VM `hive-vm` / `hive-ikamen`; override
with `HIVE_VM*` env vars.

```bash
bash deploy/create_vm.sh         # create or refresh the VM (resets -> re-runs vm_startup.sh)
deploy/push.sh                   # fast iterate: rsync working tree + restart services (~3s)
deploy/push.sh --deps            #   ...also `uv sync` after a pyproject/uv.lock change
deploy/push.sh --web             #   ...also rebuild + ship web/dist
deploy/vm.sh status              # health of chief + runner
deploy/vm.sh logs runner 80      # journalctl tail (chief|runner)
deploy/vm.sh tunnel              # localhost:8000 -> chief (bypasses Caddy basic-auth)
```

`deploy/push.sh` is the edit->test loop (ships local state in-place, no commit).
On reboot the source of truth is git: `deploy/vm_startup.sh` pulls the tracked
ref, `uv sync`s, builds `web/dist`, and starts the systemd units. A change is
reboot-safe only once pushed to that ref. Requires `gcloud` auth for the VM's
project (see `wiki/code-map.md` / memory for access details).
