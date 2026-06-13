# 🐝 hive

Continuous autonomous software development. Hive manages a portfolio of projects, each with a mission and a current iteration goal, and keeps AI coding agents productively working on them — building when the spec is clear, asking when it isn't, and never making a mess.

This repo is structured as hive's own **spec home** (dogfooding the format it defines):

- [mission.md](mission.md) — what hive is and its principles.
- [iteration.md](iteration.md) — the current iteration goal (Iteration 1: MVP).
- [wiki/architecture.md](wiki/architecture.md) — condensed current understanding of the system design.
- [wiki/spec-critique.md](wiki/spec-critique.md) — the spec critique: parallel LLM critics + adjudicator that surface underspecified/contradictory specs before building. Run locally with `uv run python scripts/spec_critique.py` (codex critics, cursor adjudicator; writes `scripts/spec_critique_report.md`).

Status: MVP deployed and demo-verified — control plane (FastAPI + Firestore + GCS) and a runner live on a GCE VM (`hive-vm`, project `hive-ikamen`), web UI included. A greenfield demo project ([wordfreq-demo](https://github.com/ikamensh/wordfreq-demo)) was planned, built, verified, and completed autonomously end-to-end. See `AGENTS.md` for the code map and how to run.

Web UI access: https://hive.34-62-218-54.sslip.io, user `ilya`, password in Secret Manager `hive-web-password`. (`hive.ilyakamen.com` is NOT live yet — it awaits a manual GoDaddy A record → 34.62.218.54; Caddy already serves both names.) Laptop runner: `bash scripts/laptop_runner.sh`.

Built on primitives from [kodo](https://github.com/ikamensh/kodo) (agent/session wrappers for Claude Code, Cursor, Codex, Gemini CLI), with its own distributed orchestration layer on top.

## CLI

`hive` (or `python -m hive.cli`) covers the full web API — everything the UI can do, with JSON output — so coding agents and scripts can drive hive exactly like the operator. Targets `HIVE_URL` (default `http://localhost:8000`; set `HIVE_BASIC_AUTH=user:pass` for the public endpoint).

```bash
hive create myproj https://github.com/me/spec.git --member-repos https://github.com/me/app.git
hive projects                      # list with live states
hive show <project_id>             # workstreams, tasks, questions
hive answer <question_id> "yes, add B"
hive dismiss <question_id>           # discard a stale question without answering
hive iterate <project_id> "next goal: ..."
hive set <project_id> --paused true --autonomy pr --daily-budget 25
hive cancel <task_id>                # dequeue if pending, stop the agent if running
hive trace <task_id>                 # raw kodo JSONL run trace (pipe into jq / kodo's viewer)
hive resources | hive subs | hive todos | hive org-context
```

The iteration goal is set through hive (`hive iterate`), which is authoritative; the orchestrator distills it into the spec home's `iteration.md`. Don't hand-edit `iteration.md` via git — that path isn't observed yet (no webhook).

`tests/test_cli.py` replays the full project loop (plan → work → verify → question → answer → goal complete) with the CLI playing the user — the scripted-test template for new flows.

## Running locally

The whole system runs on one machine; only the control plane and your laptop swap in for the VM, everything else (GitHub, Gemini, Firestore) stays the same. A leader lease in Firestore (`settings/leader_lease`) makes a second control plane on the same database refuse to start, so stop `hive-vm` first.

```bash
gcloud compute instances stop hive-vm --zone=europe-west1-b --project=hive-ikamen
GEMINI_API_KEY=... HIVE_GCP_PROJECT=hive-ikamen HIVE_GH_TOKEN=... \
  HIVE_DATA_DIR=~/.hive-data uv run uvicorn --factory hive.api:production_app
uv run python -m hive.runner       # defaults already point at localhost:8000
```

Omit `HIVE_GCP_PROJECT` for a throwaway in-memory run.

## Web UI

`web/` holds the control-plane SPA (React + Vite + TypeScript). Three pages: project list with live state badges, a project page (workstream board, question inbox, task activity feed, policy toggles), and a resources page (runners, backend cooldowns, human todos with copy-pasteable instructions, a subscriptions registry, editable org context). It polls the API every 4s and degrades to a "control plane unreachable" banner when the backend is down.

```bash
cd web
npm install
npm run dev          # dev server, proxies /api → http://localhost:8000
VITE_MOCK=1 npm run dev   # canned fixtures, no backend needed
npm run build        # tsc --noEmit + vite build → web/dist
```
