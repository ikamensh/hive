# Standalone subsystems

Hive's infrastructure concerns are packaged so each **works in isolation**: you
can `pip install` hive and use any of these packages without touching a chief,
a store, or the rest of the codebase. Hive itself consumes the same public
APIs — there is no privileged internal path.

## The rules (enforced by `tests/test_isolation.py`)

1. A standalone package imports **nothing else from hive** — not even
   `hive.models`. They are leaves of the dependency graph; the app layers
   (`api`, `cli`, `_control`, `_workstreams`, `runner`, `config`,
   `_integrations`, `models`) depend on them, never the reverse.
2. The package `__init__` is the facade: it declares `__all__`, and everything
   a caller needs is importable from it.
3. Every package ships **two runnable demo tasks** under `demos/<package>/`.
   Demos are the API's executable documentation: each demo may import only its
   own subsystem, so a demo that needs a second package means the public API
   has a hole. All offline-safe demos run in CI (`tests/test_demos.py`) and
   carry their own assertions.

## Inventory

| package | one job | demo tasks |
|---|---|---|
| `hive.fleet` | who is this machine (`machine_metadata`, `stable_machine_id`) and is it with us (`LivenessPolicy.assess` → online/quiet/dark/retired) | `identify.py` — enroll-time identity report; `liveness_timeline.py` — silence timeline → verdicts per device class |
| `hive.agents` | the coding-agent CLIs on a machine: registry & discovery, `run_agent` one-call execution with structured pydantic results + repair loop, usability probes, license-usage gauges | `survey.py` — quota-free machine survey with login hints; `one_shot.py` — real agent run returning validated JSON (spends one turn) |
| `hive.worker` | the worker lifecycle against a hive-style chief: persisted `ChiefRoster`, `WorkerLoop` (register/heartbeat/poll/execute/report-with-retries, failover, `between_tasks` exit hook) | `echo_worker.py` — shell-command worker vs a ~40-line toy chief; `failover.py` — live chief-relocation drill |
| `hive.persistence` | typed documents over *your* pydantic models (Memory/File/Firestore + write-through `CachedStore`), plus per-scope org-context blob and TTL leader lease | `notes_store.py` — own-model CRUD, 200-thread atomic updates, disk reload; `leader_election.py` — lease claim/expiry/handover |
| `hive.llm` | provider-agnostic tool-loop LLM access: `ToolSet` schemas from plain callables, `ToolLoop`, the 3-method `LLMAdapter` protocol, pricing | `calculator_agent.py` — tool-calling agent (real provider when a key exists, scripted otherwise); `custom_adapter.py` — bring-your-own-model via the adapter protocol |

`python -m hive.fleet` and `python -m hive.agents` are quick machine surveys
built from the same APIs.

## How hive composes them

- The **runner daemon** (`hive/runner/_daemon.py`) is `WorkerLoop` +
  `run_agent` + checkout/upload glue: `fleet` names the machine in the
  register payload, `agents` discovers/executes/probes and reads usage
  gauges, `worker` owns the protocol lifecycle.
- The **chief** keeps its state in `persistence` (leader lease = the
  single-writer guarantee `CachedStore` relies on), assesses machines with
  `fleet`'s liveness policy (supervisor dark-machine escalation, `hive show
  machines`), and plans with `llm`'s tool loop (orchestrator).

## Future candidates

Not yet held to the isolation contract, but shaped for it:

- `hive/_integrations/github_repos.py` — already imports nothing from hive;
  needs a facade + demos to be promoted.
- `hive/_integrations/specrepo.py` — shallow-clone + digest of a spec repo;
  same situation.
- `hive/_control/escalation.py` — "ask a human, self-close on evidence" is a
  general primitive, but it is currently welded to `hive.models.HumanTask`
  and store facts; isolating it means extracting its own todo vocabulary.
