# Agent Output and Status Streaming

To help users know that an agent (such as the intake scout or code workstreams) is active and working, and to deliver feedback to the user faster, we investigated supporting the streaming of agent output in real-time.

Currently, **real-time streaming of agent output / status is not supported** by our technical stack. This document details why streaming is not supported under the current architecture and provides a theoretical roadmap of how it could be implemented if the underlying libraries and protocols are updated in the future.

---

## 1. Current Architectural Constraints

The current architecture has several multi-layered barriers that make agent streaming unsupported out of the box:

### A. Non-Streaming LLM Adapters
Our core LLM access module in `hive/llm/` is designed around synchronous, complete generation steps:
* **OpenAI Adapter (`hive/llm/openai.py`)**: Uses a raw `httpx` HTTP call to `/chat/completions` and waits for the entire JSON payload to return. It does not set `stream: true` or parse SSE (Server-Sent Events) chunks.
* **Gemini Adapter (`hive/llm/gemini.py`)**: Calls `self._client.models.generate_content` from the `google-genai` SDK, which blocks until the full response is generated, rather than using `generate_content_stream`.

### B. `kodo-agent` Library Constraints
Hive reuses the `kodo-agent` library (specifically `kodo.agent.Agent`) to run agent sessions in a local workspace checkout:
* The `kodo.agent.Agent.run(instructions, project_dir)` method is synchronous and blocking. It orchestrates the internal LLM interaction, tool calling, and correction loops entirely in a single call.
* `kodo` does not expose any streaming output generator or token-by-token callbacks to the host application (Hive).

### C. Polling/Pull-Based Chief-Runner Protocol
The runner daemon (`hive/runner/daemon.py`) and the chief server communicate asynchronously:
* Runners run on separate machines (or separate local processes) and pull pending tasks via HTTP long-polling (`/api/tasks/poll`).
* Once a runner picks up a task, it executes it to completion locally.
* Only after the agent completes the entire run does the runner send a single `POST` containing the final `TaskResult` (text, cost, token usage, and structured results) back to the chief.
* There is no existing real-time communication channel (like WebSockets or Server-Sent Events) from the runner back to the chief to send incremental stdout, token chunks, or step status updates during execution.

---

## 2. Future Roadmap for Streaming Support

To enable real-time streaming of agent outputs, we would need to refactor each tier of the execution flow:

```
[ LLM Provider ]
       │  (Streaming API: SSE / generate_content_stream)
       ▼
[ LLM Adapters / ToolLoop ]
       │  (Yield token chunks / stream events)
       ▼
[ kodo-agent Wrapper ]
       │  (Expose callback / generator interface)
       ▼
[ Runner Daemon ]
       │  (WebSocket / SSE / chunked HTTP upload)
       ▼
[ Chief Server / Web UI ]
```

### Step 1: Support Streaming in LLM Adapters
We must update the LLM adapters in `hive/llm/` to use streaming API endpoints:
* **OpenAI**: Set `"stream": true` and read the response as an event stream, parsing the `text` delta from each chunk.
* **Gemini**: Use `client.models.generate_content_stream` instead of `generate_content`.
* **ToolLoop**: Update `ToolLoop.run` to be an asynchronous generator yielding partial responses or events (e.g., `yield TokenChunk(text)`, `yield ToolCallStart(name)`).

### Step 2: Add Streaming to `kodo-agent`
The `kodo-agent` library needs to be updated (or subclassed/wrapped) to support a generator interface:
* Expose `Agent.run_stream(instructions, project_dir)` yielding token-by-token deltas and tool-call invocation events.

### Step 3: Upgrade Chief-Runner Protocol
Replace or augment the REST pull/post mechanism with a real-time transport:
* Establish a WebSocket connection or persistent SSE connection from the runner back to the chief for active tasks.
* As the runner consumes `Agent.run_stream`, it should serialize and transmit incremental token packets (e.g., `{"task_id": "...", "delta": "..."}`) to the chief.

### Step 4: Stream to the Frontend
* Update the chief server to expose an SSE endpoint (e.g., `/api/tasks/{task_id}/stream`) for the web browser.
* Update the React frontend (`web/src/`) to subscribe to this endpoint when a task is in `running` status, appending incoming token deltas to the displayed output box in real-time.
