# 🐝 hive

Continuous autonomous software development. You give hive a project — a mission and a concrete iteration goal — and it keeps AI coding agents (Claude Code, Cursor, Codex, Gemini CLI) productively working toward that goal: planning, building, and verifying each change with a second agent, **asking you when the spec is genuinely ambiguous**, and never making a mess.

The point isn't just "an agent writes code." It's the loop around it: hive decomposes the goal into workstreams, runs at most one agent per repo so there are no merge conflicts, gates every change behind independent verification (with an anti-bloat check), and parks work to batch up questions for you instead of guessing on things that are expensive to get wrong. Your answers accumulate into an ever-sharper spec.

**Good for:** medium-running, spec-driven work you'd otherwise babysit — building out an iteration of a side project, a greenfield service from a written spec, multi-repo features. **Not (yet) for:** one-off "fix this line" edits (just use the agent CLI directly), or work with no written goal to aim at.

---

## Quickstart: run hive on your laptop

This runs the control-plane process and a runner on one machine, but runtime state still lives in managed services: Firestore for documents and GCS for blobs. Local file persistence is only for tests and one-time migration of old data.

### 0. Prerequisites

- **[uv](https://docs.astral.sh/uv/)** (Python runner; everything below is `uv run …`).
- **At least one agent CLI installed and logged in** — `claude`, `cursor`, `codex`, or `gemini-cli`. This is what actually writes the code.
- **An orchestrator API key** — `OPENAI_API_KEY` *or* `GEMINI_API_KEY`. This is the "brain" that plans and decides (separate from the agent CLIs above).
- **`gh` logged in** (`gh auth login`) — hive pushes commits/PRs using your GitHub credentials.
- **GCP application credentials** (`gcloud auth application-default login`) with access to the Firestore project and GCS bucket.
- **A GitHub repo to point at** — the project's *spec home* (holds `mission.md` / `iteration.md`). For a quick test, any repo you can push to works; hive will write the goal into it.

### 1. Clone and set up the environment

```bash
git clone https://github.com/ikamensh/hive.git
cd hive
uv sync          # creates .venv (Python 3.13, pinned in .python-version) and installs from uv.lock
```

`uv sync` is optional in practice — every `uv run …` below auto-creates and updates the venv on first use — but running it once up front gives you a ready `.venv` and surfaces install errors immediately. Activate it (`source .venv/bin/activate`) if you'd rather drop the `uv run` prefix.

### 2. Start the control plane

```bash
uv run hive doctor storage
uv run hive run
```

`hive run` resolves its tokens, rebuilds the React UI into `web/dist`, points the
server at that bundle, and prints what it found (and from where) before booting:

```
  github: token from `gh auth token`
  orchestrator: OPENAI_API_KEY from environment (provider=auto)
  store: Firestore (hive-ikamen, from stored config)
  blobs: GCS (hive-ikamen-blobs, from stored config)
  workspace: default (ikamen)
  public url: http://127.0.0.1:8000
hive control plane → http://127.0.0.1:8000
```

If `HIVE_GCP_PROJECT` or `HIVE_GCS_BUCKET` is missing, `hive run` refuses to start and prints the exact variables to set. Leave it running. (Flags: `--host`, `--port`, `--reload`, `--no-web-build`.)

**Giving hive its own tokens.** Autodetected tokens (the `gh` token, `OPENAI_API_KEY`/`GEMINI_API_KEY` from your shell) are just the starting point. To have hive use *separate* keys — e.g. so its spend is billed/tracked on their own account — store them in hive's own config (`~/.config/hive/config.env`, `chmod 600`); stored values take precedence over the ambient environment on `hive run`:

```bash
uv run hive config import                              # seed the store from gh + current env
uv run hive config set OPENAI_API_KEY sk-hive-only-…   # override with a hive-specific key
uv run hive config show                                # stored values, secrets masked
```

### 3. Register your laptop as a runner

In a second terminal:

```bash
uv run python -m hive.runner --list-backends   # sanity check: which agent CLIs do I have?
uv run python -m hive.runner                    # registers this machine, then long-polls for work
```

The runner defaults already point at `localhost:8000`. It advertises each detected backend as a resource.

### 4. Make a backend usable

A freshly registered backend is `unknown` until proven. Probe it (cheap smoke run against a temp repo):

```bash
uv run hive resources                 # find the resource id for the backend you want
uv run hive probe <resource_id>       # marks it usable, or surfaces an auth/quota/login fix
```

Only `usable` backends get real work. A failed probe stays non-dispatchable and files a human todo telling you exactly what to fix on the CLI.

### 5. Create your first project and set the goal

```bash
uv run hive create myproj
uv run hive set <project_id> --spec-repo https://github.com/me/spec.git
uv run hive start <project_id> \
  --mission "A small CLI that counts word frequencies in a file." \
  --iteration-goal "Read a text file path from argv, print the top 10 words by count."
uv run hive projects                  # see it appear with a live state badge
```

Creating a project makes a draft. `set --spec-repo` tells hive where the spec home lives, and `start` wakes the orchestrator: it runs a spec critique, decomposes the goal into workstreams, and starts dispatching work to your runner.

### 6. Watch it work and answer its questions

```bash
uv run hive show <project_id>         # workstreams, tasks, and any open questions
uv run hive answer <question_id> "yes, read UTF-8 and ignore punctuation"
```

The project moves through states you can watch on either surface: `working` → `blocked: questions` (it needs you) → `blocked: resources` (out of agent capacity, resumes itself) → `idle: goal complete`. When it's done, set the next goal with `uv run hive iterate <project_id> "…"`.

Set a `--daily-budget` and hive stays under it: spend counts both the runner agents' cost *and* the orchestrator's own LLM calls, and once a project is over budget it stops dispatching and stops planning (`blocked: budget`) until spend rolls over at UTC midnight. Work that keeps failing — verification rejects or runner/execution errors — is capped after a few rounds so a broken workstream parks and asks you instead of burning the budget on retries.

Prefer clicking? Run the web UI (below) and do steps 5–6 there instead.

---

## The two surfaces

### CLI

`hive` (or `uv run python -m hive.cli`) covers the full web API — everything the UI can do, with JSON output — so coding agents and scripts can drive hive exactly like you do. Targets `HIVE_URL` (default `http://localhost:8000`; set `HIVE_BASIC_AUTH=user:pass` for an authenticated endpoint).

```bash
hive create myproj
hive set <project_id> --spec-repo https://github.com/me/spec.git --member-repos https://github.com/me/app.git
hive start <project_id> --mission "ship the first useful slice" --iteration-goal "..."
hive projects                      # list with live states
hive show <project_id>             # workstreams, tasks, questions
hive answer <question_id> "yes, add B"
hive dismiss <question_id>         # discard a stale question without answering
hive iterate <project_id> "next goal: ..."
hive set <project_id> --paused true --autonomy pr --daily-budget 25
hive cancel <task_id>              # dequeue if pending, stop the agent if running
hive trace <task_id>              # raw kodo JSONL run trace (pipe into jq / kodo's viewer)
hive agents                       # local supported agent CLIs detected on this machine
hive resources | hive probe <resource_id>
hive subs | hive todos | hive org-context
```

`tests/test_cli.py` replays the full project loop (plan → work → verify → question → answer → goal complete) with the CLI playing the user — the scripted-test template for new flows.

### Web UI

`web/` is the control-plane SPA (React + Vite + TypeScript): a project list with live state badges, a project page (workstream board, question inbox, activity feed, policy toggles), and a resources page (runners, backend cooldowns, human todos, subscriptions, org context). It polls the API every 4s and shows a clear "control plane unreachable" banner when the backend is down.

```bash
cd web
npm install
npm run dev              # dev server, proxies /api → http://localhost:8000
VITE_MOCK=1 npm run dev  # canned fixtures, no backend needed
npm run build            # tsc --noEmit + vite build → web/dist (served by the control plane)
```

For normal local launches, prefer `uv run hive run`: it runs the production web
build for you before starting the API, so the served UI matches `web/src`.

### Local checks

CI runs the same gates as pre-commit: Ruff, the backend pytest suite, and the web production build.

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

---

## Configuration

The iteration goal is always set *through hive* (`hive iterate` / the UI), which is authoritative; the orchestrator distills it into the spec home's `iteration.md`. Don't hand-edit `iteration.md` via git — that path isn't observed yet (no webhook).

**Orchestrator (the planner):**

- `HIVE_ORCH_PROVIDER=auto|openai|gemini` (default `auto`).
- `HIVE_ORCH_MODEL=...` optionally pins a specific model.
- `OPENAI_API_KEY` uses OpenAI's API; `HIVE_OPENAI_BASE_URL` can point at an OpenAI-compatible endpoint. `GEMINI_API_KEY` uses Gemini.
- In `auto`, an explicit model prefix picks the provider; otherwise OpenAI is used when `OPENAI_API_KEY` exists, then Gemini.

**Persisting state across restarts.** Runtime state requires Firestore and GCS:

```bash
HIVE_GCP_PROJECT=<gcp-project> HIVE_GCS_BUCKET=<bucket> uv run hive run
```

For a legacy local file store, migrate it explicitly while control planes are stopped:

```bash
uv run hive migrate-local-state \
  --data-dir ~/.hive-data \
  --gcp-project <gcp-project> \
  --gcs-bucket <bucket>
```

The Settings page shows whether the active runtime is fully managed. Firestore without GCS is not a supported runtime mode.

A leader lease in Firestore (`settings/leader_lease`) makes a *second* control plane on the same database refuse to start — so if a deployed instance owns that project, stop it first.

---

## How it works (the short version)

Two deliberately separated layers: a **supervisor** (plain deterministic code) owns the project state machine and dispatches work — serialized per repo, never faking progress when no usable backend exists — and an **orchestrator** (a stateful high-intelligence model session) plans, decomposes goals, picks tasks/backends, and decides when to ask you. Work is distributed to **runners** (this is your laptop in the quickstart; in production, a VM + your laptop) over long-poll, so it works behind NAT. Every change is verified by a *different* agent session than the one that wrote it.

### Project work: iteration goal and GitHub issues

Every project has normal iteration work, and can also run GitHub issue solving
from inside the project.

- **Iteration work** — you set an iteration goal and the orchestrator decomposes it into work items.
- **GitHub issues** — from the project Issues view, run **Preflight** first (`hive preflight <project>`) to check the run's preconditions: the GitHub token's write access, and a runner self-check that it can `git push` and use `gh`. Then sync or run issues. Hive pulls each open issue with its comments and embedded images and runs a deterministic per-issue pipeline (no planner in the path). Each issue gets one warm agent session that **clarifies then fixes**: bug reports are doable by default (investigate → reproduce → fix); an underspecified feature is held back and the agent posts a GitHub comment saying exactly what must be decided first. A fix lands on a per-issue branch and is then handed to a **fresh, independent reviewer** agent that can correct small problems on the spot and decides accept or reject. On accept, hive merges the branch into the default branch and closes the issue; on reject, the agent comments with what went wrong and the branch is kept for debugging. Re-run after you've clarified a blocked issue or addressed a rejection. See [`wiki/issue-solving.md`](wiki/issue-solving.md) for the pipeline and [`wiki/unified-project-work.md`](wiki/unified-project-work.md) for the project model.

- [mission.md](mission.md) — what hive is and its principles.
- [iteration.md](iteration.md) — the current iteration goal.
- [wiki/architecture.md](wiki/architecture.md) — full system design.
- [wiki/proactive-autonomy.md](wiki/proactive-autonomy.md) — decision ledger and agent authority: the human/AI boundary.
- [AGENTS.md](AGENTS.md) — code map and how to run each component.

Built on primitives from [kodo](https://github.com/ikamensh/kodo) (agent/session wrappers for Claude Code, Cursor, Codex, Gemini CLI), with hive's own distributed orchestration layer on top.

---

## The deployed instance (maintainer notes)

This repo is also hive's own **spec home** (dogfooding the spec format it defines). The MVP is deployed and demo-verified: control plane (FastAPI + Firestore + GCS) and a runner live on a GCE VM (`hive-vm`, project `hive-ikamen`); a greenfield demo project ([wordfreq-demo](https://github.com/ikamensh/wordfreq-demo)) was planned, built, verified, and completed autonomously end-to-end.

- Web UI: https://hive.34-62-218-54.sslip.io, user `ilya`, password in Secret Manager `hive-web-password`. (`hive.ilyakamen.com` awaits a manual GoDaddy A record → 34.62.218.54; Caddy already serves both names.)
- Attach your laptop as a runner to the deployed instance: `bash scripts/laptop_runner.sh`.
- Secrets live in GCP Secret Manager (`hive-gemini-api-key`, `hive-gh-token`, `hive-runner-token`, `hive-openai-api-key`, `hive-web-password`); the VM startup script materializes `/etc/hive/env`. GCP project `hive-ikamen`, Firestore `(default)` + bucket `hive-ikamen-blobs`, both `europe-west1`.
