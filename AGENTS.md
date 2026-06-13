# Implementation notes

Architecture rationale lives in `wiki/architecture.md`; this file is the code map.

## Layout

- `hive/models.py` — pydantic domain models; persisted as dicts via the store. `Subscription` (AI plans the user owns) and `HumanTask` (operator todos with markdown instructions, e.g. CLI logins on remote runners) are org-level, not project-scoped.
- `hive/store.py` — `MemoryStore` (tests) / `FirestoreStore` (prod), same duck-typed API.
- `hive/supervisor.py` — deterministic layer: `compute_state` (pure, backend-aware — pending work whose backend no online runner offers is `blocked_resources`, not fake `working`), dispatch (serialized per repo; skipped when over the project's daily budget → `blocked_budget`), orphan failing, asyncio loop waking the orchestrator on events (incl. a replan nudge when work is stuck on an unavailable backend). Single-instance guard: a leader lease in the store (`claim_leader`, Firestore doc `settings/leader_lease`) is acquired at startup and renewed every tick, so a second control plane on the same Firestore refuses to boot (and a fenced-out one exits).
- `hive/orchestrator.py` — configurable LLM tool-loop (`HIVE_ORCH_PROVIDER=auto|openai|gemini`, `HIVE_ORCH_MODEL` optional override). `Tools` methods are the tool surface; docstrings are what the model sees. Gemini uses `google-genai` automatic function calling; OpenAI-compatible providers use Hive's manual tool loop over chat completions. The quality gate is enforced here, not just prompted: verdicts are parsed deterministically in `api.task_result` (`parse_verdict`), `mark_goal_complete` is rejected unless every done workstream's latest task is an accepted verify, and `create_task` refuses a new work task after `MAX_FIX_ROUNDS` verify rejects without an accept (forcing park+ask). PR-mode work goes on a per-workstream branch (`hive/<ws>`) so verify reviews exactly those changes; direct_push lands on the default branch with verify as an after-the-fact safety net. No `from __future__ import annotations` in this file — runtime tool-schema generation inspects annotations. Conversation history persists to the blob store; cold start is always safe because every invocation gets a full state snapshot + spec digest.
- `hive/specrepo.py` — shallow clone of the project's spec-home repo; digest for context (`digest_dir` works on any local dir), small commits for wiki/input-log distillation.
- `hive/critique.py` — spec critique (wiki/spec-critique.md): parallel critics (every model x tester/builder/consistency lens) + adjudicator run by the smartest model per `hive/model_intel.py` (Artificial Analysis index estimates). LLM transport is a `prompt -> text` callable, so tests script it and `scripts/spec_critique.py` runs it locally via kodo CLI agents. Prompts: `hive/prompts/critic.md`, `adjudicator.md` (the "intake" role).
- `hive/api.py` — FastAPI: web API (unauthenticated; sits behind a tunnel/Tailscale) + runner protocol (shared token via `X-Hive-Token`). `create_app` takes the blob store so the runner can upload per-task JSONL traces (`POST/GET /api/tasks/{id}/trace`). `production_app()` wires env config; SPA fallback serves `web/dist`.
- `hive/cli.py` — `hive` console script: full web-API parity, JSON output, for agents/scripts. `run(args, client)` takes any httpx-compatible client, so tests drive it with `TestClient` (`tests/test_cli.py` replays the whole loop CLI-side).
- `hive/runner.py` — daemon: register → long-poll → checkout repo (the task's `branch` when set, else default) → run kodo `Agent` → upload the kodo JSONL trace → report. A watcher thread polls the task and calls `session.terminate()` on an operator cancel. Reuses kodo sessions (claude/cursor/codex/gemini-cli) for cost parsing, timeouts, error classification.
- `hive/prompts/` — versioned base prompts (hash recorded on tasks for future GEPA).
- `web/` — React+Vite+TS SPA, polls the API every 4s (`usePoll` in `src/api.ts`; no state library). Pages in `src/pages/` (Projects, Project, Resources); markdown via `marked`; `VITE_MOCK=1` swaps the API client for in-memory fixtures (`src/mocks.ts`). Dev proxy `/api` → `:8000`; `npm run build` = `tsc --noEmit` + vite → `web/dist`.
- `deploy/` — Dockerfile (control plane), compose, GCE VM creation + startup script.
- `scripts/` — `smoke_orchestrator.py` (real-LLM smoke), `laptop_runner.sh`.

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
