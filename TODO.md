# Backlog

Deferred gap-closing work. Captured, not yet started — see wiki/ideal-ux.md
for the gap list these tie back to.

## Round 3 structural simplifications (validated priorities, 2026-07-13)

Ordered by the 2026-07-13 lab-tally live run. Each is a deep refactor that
wants a fresh session:

1. **One execution pipeline over work docs** — issues.py + plans.py are the
   same resolve→review→merge machine with different feeders; _task_results.py
   lands each separately. Extract the shared pipeline; GitHub issues, plan
   items, and directives become feeders. Fold in the Task per-kind payload
   diet (issue_doc/conversation_turn/fresh_branch/...) while task kinds are
   renegotiated.
2. **Delete the ProjectWorkstream layer if it only routes ids** — the
   iteration kind is a stub row; issues/testing key by repo. Removing it also
   removes the CLI workstream-id resolution and the Task.workstream_id /
   work_item_id dual foreign key. Do after (1).
3. **Shrink the testing state space** — live evidence: one episode on a
   4-command CLI fanned out to 9+ agent sessions (refresh + 5 sweeps + 3
   repros); Finding has 7 states tracking what the filed issue already
   records. Fold repro/judge into confirm; a confirmed finding IS the issue.
4. **One ask-the-human ledger** — Questions, testability decision-questions,
   and the wiki decision ledger are three shapes of "hive needs a ruling".
   One decision record, mirrored to the spec repo, answered from the inbox.
5. **Small**: HIVE_RUNNER_STATE_DIR should default under HIVE_DATA_DIR when
   set (a second runner on one box currently inherits the fleet pause flag,
   observed in the lab).

## Scheduled scan: eligibility on big external backlogs
The unattended issue scan advances the lowest-order queued issue — on a
freshly adopted repo with a large pre-existing backlog that means hive
auto-starts external issue #1 nobody asked it to work. Safe default worth
building: auto-advance only hive-originated issues (bodies carrying
`<!-- hive-` markers: directives, CI autofix, testing findings) or a `hive`
label; the external backlog stays a deliberate human `hive scan`/issue-run.
(Not urgent for the current three projects — kodo had exactly one open issue —
but required before adopting a repo with a real backlog.)

## Dispatch: toolchain + warm-checkout awareness
First-fit dispatch is toolchain-blind and checkout-blind: a Rust task can land
on a machine without cargo (agent then installs or fails), and a machine with
a warm checkout of the repo has no preference over one that must clone cold
(observed 2026-07-05: rust-td landed on the only cargo-equipped machine by
list-order luck). The pieces exist — `Task.required_capabilities`,
`Resource.supports`, runner `detect_capabilities` (browser today), and
`Checkout` rows per (machine, repo). Design: advertise common toolchains
(cargo, node, go, uv) as capabilities; let the orchestrator set
`required_capabilities` from the project's language; prefer (don't require)
machines whose Checkout of the task repo exists. Keep it a preference, not a
hard constraint, to avoid capacity deadlocks.

(Gap 11 episode traces and gap 9 iteration.md ownership are done — see the
runner trace upload / `hive trace`, and the iterate path in `api.py` +
`wiki/architecture.md`.)

## Gap 10 — Spec critique in the loop
`hive/_workstreams/critique.py` is currently reachable only via `scripts/spec_critique.py`.
Wire it into the running system:
- Orchestrator opens a new project's workstream 0 with a critique run; its
  findings seed the first batch of clarification questions.
- API + CLI + UI action to re-run critique on demand, with staleness tracking
  ("spec changed since last critique").

## Gap 11 follow-up — trace viewer in the web UI
Traces are uploaded and exposed via `GET /api/tasks/{id}/trace` + `hive trace`.
Still TODO: surface them in the web UI (reuse kodo's JSONL viewer) and capture
the `conversations/` gz files, not just `log.jsonl`.

## Gap 6 — Durable-memory enforcement
`commit_to_spec` distillation is voluntary today; if the model skips it, an
answer survives only in loseable history. Options: detect answered questions
not reflected in a spec commit and nudge/require it; or have the chief
append raw answers to `input-log/` deterministically.

## Gap 9 — iteration.md editing ownership
Story 10 says editing the iteration goal clears completion and wakes the
orchestrator, but `iterate` only sends a free-text note while `iteration.md`
lives in the spec repo. Decide and implement the canonical path: who writes
`iteration.md`, and how hive notices a direct git edit (until GitHub webhooks
land, an idle project gets no heartbeat).

## Subscription recovery flow — consult subscriptions on blocked_resources
The data model now distinguishes durable `Subscription`s (with `licensing_mode`)
from live per-machine agents, and `/api/resources` surfaces
`subscription_candidates` (`hive/_control/capacity.py`). Not yet wired into the
control loop: when work is `blocked_resources` (no online usable agent for a
needed backend), the supervisor/orchestrator should consult subscriptions and
act on the licensing mode — self-serve a `portable` credential onto an online
machine, or file a `HumanTask` login for a `machine_bound` one — instead of just
going quiet. This is "subscriptions as a recovery source, not baseline
capacity"; worth an ADR when built (the genuine trade-off vs counting owned
capacity as always-available). See CONTEXT.md (Subscription / Licensing Mode)
and wiki/architecture.md (provider rulebook, user resource policy).

## Remote control — convenience gaps
The laptop-off workflow (README "Keep Hive working while your laptop is off") works
today, but the story has friction worth smoothing:
- **Per-user CLI auth: built** (`hive connect <url>` mints a typ:"cli" bearer and
  stores HIVE_URL + HIVE_TOKEN). Remaining ops step: switch the VM to `github`
  auth mode and drop the Caddy basic-auth password. Then `github` auth mode is
  reachable from the CLI without the browser, and the shared password can retire.
- **Backend continuity is on the operator.** Work routed to a backend only the
  (off) laptop has parks as `blocked: resources`. The VM startup now installs claude +
  cursor-agent and reads `hive-claude-oauth-token` / `hive-cursor-api-key` from Secret
  Manager (`deploy/vm_startup.sh`), so the always-on runner serves them once those
  secrets exist — the manual step is minting the tokens (`claude setup-token`, Cursor
  dashboard) and `scw secret secret create` + `scw secret version create`. Tested why *copying* the desktop login fails:
  claude's stored OAuth blob (Keychain on mac, `~/.claude/.credentials.json` on Linux)
  carries an access token that expires and a refresh token that rotates, so a copied
  blob 401s; cursor has no copyable token file and wants `CURSOR_API_KEY`. The durable
  fix is headless/minted tokens, not copied logins. Still open: the deeper automation —
  same root as the "Subscription recovery flow" item above (consult subscriptions on
  `blocked_resources` and self-serve a portable credential / file a login todo) and
  surfacing "no always-on runner offers this backend" in preflight/UI.
- **One client target, no named contexts.** `HIVE_URL` is a single stored value;
  switching local↔remote is manual. A `hive target` with named contexts (kubectl-style)
  would make running both pleasant. Lower priority for single-user MVP.
- **No scheduled issue scan.** New GitHub issues are only ingested on a human-triggered
  `hive scan`. A periodic scan (chief cron) would make issue solving truly
  unattended; until then it relies on a remote trigger from any machine.

## Remote UI/CLI access — stable address (decision deferred)
Today you reach the remote either by opening the public `sslip.io` URL (Caddy basic-auth,
serves the UI same-origin) or via `deploy/vm.sh tunnel` (SSH forward that bypasses Caddy
auth / for dev). Decision (2026-06-23): keep this for now. The friction is real though —
the address is the VM's *ephemeral* public IP encoded in `sslip.io`, so it breaks on VM
recreate, and the tunnel is an awkward per-session forward. When ready to fix:
- **Preferred: Tailscale** (this is the architecture doc's original intent — see
  `wiki/architecture.md` "Access via Tailscale"). Enroll the VM (`tailscale up` in
  `vm_startup.sh`, auth key as a GCP secret); open the stable MagicDNS `http://hive-vm:8000`
  from laptop/phone; CLI targets the same URL with no `HIVE_BASIC_AUTH`. Drops the tunnel
  *and* Caddy/basic-auth (the tailnet is the auth boundary), no domain/static-IP needed,
  not publicly exposed. Tailscale Serve gives `https://…ts.net` if HTTPS is wanted.
- **Alt: public domain + static IP.** Reserve a static external IP (currently none — see
  `create_vm.sh`), point `hive.ilyakamen.com` at it, keep Caddy auto-TLS + login. Shareable
  with non-tailnet users; publicly exposed; more moving parts.
- **Verify regardless:** confirm the GCP firewall does *not* expose chief `:8000`
  publicly (the chief binds `0.0.0.0:8000` and runs `dev` auth mode — Caddy on :443
  is meant to be the only public surface). Couldn't check from here (no `compute.firewalls.list`
  permission on `hive-ikamen` with the current account).

## Issue solving — selectable run scope
The 2026-06-14 live validation target was issues #2-#4, but scanning the repo
also ingested newly-open issue #5 and the deterministic queue started it after
#4. We cancelled #5 from the UI and left it queued. Issue runs now support
selected issues and all-open snapshots; keep validating whether the UI needs
additional stop-after controls for operator-led validation batches.
