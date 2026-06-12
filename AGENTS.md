# Implementation notes

Architecture rationale lives in `wiki/architecture.md`; this file is the code map.

## Layout

- `hive/models.py` — pydantic domain models; persisted as dicts via the store. `Subscription` (AI plans the user owns) and `HumanTask` (operator todos with markdown instructions, e.g. CLI logins on remote runners) are org-level, not project-scoped.
- `hive/store.py` — `MemoryStore` (tests) / `FirestoreStore` (prod), same duck-typed API.
- `hive/supervisor.py` — deterministic layer: `compute_state` (pure), dispatch (serialized per repo), orphan failing, asyncio loop waking the orchestrator on events.
- `hive/orchestrator.py` — Gemini tool-loop (`google-genai` automatic function calling). `Tools` methods are the tool surface; docstrings are what the model sees. No `from __future__ import annotations` in this file — stringified annotations break genai schema inference. Conversation history persists to the blob store; cold start is always safe because every invocation gets a full state snapshot + spec digest.
- `hive/specrepo.py` — shallow clone of the project's spec-home repo; digest for context, small commits for wiki/input-log distillation.
- `hive/api.py` — FastAPI: web API (unauthenticated; sits behind a tunnel/Tailscale) + runner protocol (shared token via `X-Hive-Token`). `production_app()` wires env config; SPA fallback serves `web/dist`.
- `hive/runner.py` — daemon: register → long-poll → checkout repo → run kodo `Agent` → report. Reuses kodo sessions (claude/cursor/codex/gemini-cli) for cost parsing, timeouts, error classification.
- `hive/prompts/` — versioned base prompts (hash recorded on tasks for future GEPA).
- `web/` — React+Vite+TS SPA, polls the API every 4s (`usePoll` in `src/api.ts`; no state library). Pages in `src/pages/` (Projects, Project, Resources); markdown via `marked`; `VITE_MOCK=1` swaps the API client for in-memory fixtures (`src/mocks.ts`). Dev proxy `/api` → `:8000`; `npm run build` = `tsc --noEmit` + vite → `web/dist`.
- `deploy/` — Dockerfile (control plane), compose, GCE VM creation + startup script.
- `scripts/` — `smoke_orchestrator.py` (real-LLM smoke), `laptop_runner.sh`.

## Conventions

- Tests use `MemoryStore` + scripted orchestrator; no network, no LLM. Real-LLM behavior is checked with `scripts/smoke_orchestrator.py` (needs `GEMINI_API_KEY`).
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
