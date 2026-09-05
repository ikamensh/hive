# OpenCode as the planner and triage model

Set `HIVE_ORCH_PROVIDER=opencode` on the chief. It uses the installed OpenCode
CLI with `opencode/muse-spark-1.3-contributor-free` by default, without an API key.
`HIVE_ORCH_MODEL` overrides the model using OpenCode's `provider/model` format.
With provider `auto`, an `opencode/` model prefix also selects this adapter.
OpenCode is never silently added to an API provider's fallback list.

The chief needs `opencode` on PATH and whatever login the selected provider
requires. Workers can independently select OpenCode or subscription agents.
The planner proposes drafts through the existing Hive tools; approving and
executing tasks remain separate state transitions. Included-only projects can
request plans and run todo triage at a $0 daily budget when the selected model
is explicitly `opencode/*-free` (including the default). The API, scheduler and
direct orchestrator entrypoint share the same admission rule. Paid OpenCode
models and API providers remain blocked; a free-provider failure cannot fall
back to configured API credentials. Unpinned `auto` selection does not qualify.

Each existing Hive tool-loop round launches a short-lived OpenCode process in
an isolated temporary directory. A custom agent receives the planner prompt
and conversation, with native tools denied, external plugins disabled, and
personal/project configuration excluded. The CLI's explicit `--dir` and its
`PWD` both name that directory; setting only subprocess `cwd` can still load
the caller's repository context. Explicit session titles and a
`small_model` matching the requested model prevent a separate paid title model.
Timeout or cancellation kills the process group, including descendants.

For tool-bearing calls, a strict Pydantic schema is generated from Hive's actual
tool names and argument signatures. OpenCode returns a JSON request envelope;
the complete envelope is validated before Hive executes any request. Unknown
tools, extra fields, wrong argument types, and malformed output fail explicitly.
Only Hive's `ToolSet` performs side effects. For an empty tool set, model text
passes through unchanged, including JSON requested by todo triage.

OpenCode's input, cache, output, and reasoning token counts feed Hive's usage
record. These are model-reported token counts; subscription/free billing is
determined by the selected provider. This implementation keeps the whole
bounded conversation in Hive and supplies it each round, trading some input
tokens and CLI startup time for no long-running OpenCode server to supervise.

Integration tests exercise executable CLI fixtures through the real tool loop
and planner, including included-only/$0 API-to-scheduler draft persistence,
paid-provider rejection, validation-before-side-effects, raw JSON triage,
timeout cleanup, and provider errors without paid fallback. A live Muse smoke also
completed a two-round tool/result exchange and a separate JSON triage call.
`uv run python scripts/smoke_opencode_planner.py` exercises the real
`Orchestrator.invoke`, initial state snapshot, and seven-tool planner surface
with an ordinary arithmetic-package goal. It uses an included-only/$0 in-memory
project and a throwaway local Git spec remote; any spec commits stay there.
It requires one small draft, a final response within three rounds, and no
execution tasks. The live check completed in two rounds and 16.1 seconds:
Muse committed the iteration goal, proposed one item, and stopped for approval
(7,441 input and 1,514 output tokens; $0 recorded cost).

Earlier checks caught two integration problems: the initial snapshot was
treated as permanently authoritative, causing repeated successful calls, and
OpenCode could inherit the caller's repository context. Later tool results now
explicitly update the invocation's state, and the CLI directory is pinned.
Malformed JSON or wrong argument types still fail explicitly before dispatch;
there is no automatic repair or provider fallback for invalid tool requests.
These checks establish transport behavior, not general planning quality.

The adapter uses OpenCode's documented [CLI](https://opencode.ai/docs/cli/)
and [permission configuration](https://opencode.ai/docs/permissions/).
Its server also offers native [structured output](https://opencode.ai/docs/sdk/#structured-output);
the CLI path validates returned JSON locally so Hive needs no resident server.
