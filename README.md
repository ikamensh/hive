# 🐝 hive

Continuous autonomous software development. Hive manages a portfolio of projects, each with a mission and a current iteration goal, and keeps AI coding agents productively working on them — building when the spec is clear, asking when it isn't, and never making a mess.

This repo is structured as hive's own **spec home** (dogfooding the format it defines):

- [mission.md](mission.md) — what hive is and its principles.
- [iteration.md](iteration.md) — the current iteration goal (Iteration 1: MVP).
- [wiki/architecture.md](wiki/architecture.md) — condensed current understanding of the system design.

Status: MVP deployed and demo-verified — control plane (FastAPI + Firestore + GCS) and a runner live on a GCE VM (`hive-vm`, project `hive-ikamen`), web UI included. A greenfield demo project ([wordfreq-demo](https://github.com/ikamensh/wordfreq-demo)) was planned, built, verified, and completed autonomously end-to-end. See `AGENTS.md` for the code map and how to run.

Web UI access: `https://hive.ilyakamen.com (or https://hive.34-62-218-54.sslip.io), user `ilya`, password in Secret Manager `hive-web-password`. Laptop runner: `bash scripts/laptop_runner.sh`.

Built on primitives from [kodo](https://github.com/ikamensh/kodo) (agent/session wrappers for Claude Code, Cursor, Codex, Gemini CLI), with its own distributed orchestration layer on top.

## Web UI

`web/` holds the control-plane SPA (React + Vite + TypeScript). Three pages: project list with live state badges, a project page (workstream board, question inbox, task activity feed, policy toggles), and a resources page (runners, backend cooldowns, human todos with copy-pasteable instructions, a subscriptions registry, editable org context). It polls the API every 4s and degrades to a "control plane unreachable" banner when the backend is down.

```bash
cd web
npm install
npm run dev          # dev server, proxies /api → http://localhost:8000
VITE_MOCK=1 npm run dev   # canned fixtures, no backend needed
npm run build        # tsc --noEmit + vite build → web/dist
```
