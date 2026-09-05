# OpenCode as the planner and triage model

Set `HIVE_ORCH_PROVIDER=opencode` on the chief. It uses the installed OpenCode
CLI with `opencode/muse-spark-1.3-contributor-free` by default, without an API key.
`HIVE_ORCH_MODEL` overrides the model using OpenCode's `provider/model` format.
With provider `auto`, an `opencode/` model prefix also selects this adapter.
OpenCode is never silently added to an API provider's fallback list.

The chief needs `opencode` on PATH and whatever login the selected provider
requires. Workers can independently select OpenCode or subscription agents.
The planner proposes drafts through the existing Hive tools; approving and
executing tasks remain separate state transitions. Included-only projects
still depend on the chief's planning policy admitting the selected free model.

Each existing Hive tool-loop round launches a short-lived OpenCode process in
an isolated temporary directory. A custom agent receives the planner prompt
and conversation, with native tools denied, external plugins disabled, and
personal/project configuration excluded. Explicit session titles and a
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
and planner, including draft persistence, validation-before-side-effects,
raw JSON triage, timeout cleanup, and provider errors. A live Muse smoke also
completed a two-round tool/result exchange and a separate JSON triage call.
That establishes transport behavior, not general planning quality.

The adapter uses OpenCode's documented [CLI](https://opencode.ai/docs/cli/)
and [permission configuration](https://opencode.ai/docs/permissions/).
Its server also offers native [structured output](https://opencode.ai/docs/sdk/#structured-output);
the CLI path validates returned JSON locally so Hive needs no resident server.
