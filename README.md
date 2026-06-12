# 🐝 hive

Continuous autonomous software development. Hive manages a portfolio of projects, each with a mission and a current iteration goal, and keeps AI coding agents productively working on them — building when the spec is clear, asking when it isn't, and never making a mess.

This repo is structured as hive's own **spec home** (dogfooding the format it defines):

- [mission.md](mission.md) — what hive is and its principles.
- [iteration.md](iteration.md) — the current iteration goal (Iteration 1: MVP).
- [wiki/architecture.md](wiki/architecture.md) — condensed current understanding of the system design.

Status: design phase; MVP scope defined, implementation not started.

Built on primitives from [kodo](https://github.com/ikamensh/kodo) (agent/session wrappers for Claude Code, Cursor, Codex, Gemini CLI), with its own distributed orchestration layer on top.

## Web UI

`web/` holds the control-plane SPA (React + Vite + TypeScript). Three pages: project list with live state badges, a project page (workstream board, question inbox, task activity feed, policy toggles), and a resources page (runners, backend cooldowns, editable org context). It polls the API every 4s and degrades to a "control plane unreachable" banner when the backend is down.

```bash
cd web
npm install
npm run dev          # dev server, proxies /api → http://localhost:8000
VITE_MOCK=1 npm run dev   # canned fixtures, no backend needed
npm run build        # tsc --noEmit + vite build → web/dist
```
