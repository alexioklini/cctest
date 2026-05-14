# Brain Sidecar — Anthropic SDK Agentic Loop

A separate Python process that owns the LLM call. Brain talks to it over HTTP.

The point: keep the `anthropic` SDK out of Brain's main process (anyio side-effects historically broke streaming when `claude_cli` was imported alongside the SDK).

This sidecar mirrors `eval/sdk_harness/run_sdk.py` exactly. **Brain does not modify the data flow in or out of this loop.** What goes in (system prompt, messages, tools) is what the SDK gets. What comes out (Anthropic SSE events) is forwarded verbatim.

## Install

```bash
cd <repo-root>
python3 -m venv .venv_sidecar
.venv_sidecar/bin/pip install anthropic
```

(A proper `pyproject.toml` install will land in Phase 2 along with a launchd
wrapper. Phase 1 just needs the `anthropic` package on the venv.)

## Run (manual, Phase 1)

```bash
# Terminal 1 — sidecar
.venv_sidecar/bin/python sidecar/sidecar.py --port 8421

# Terminal 2 — tool-server stub (port 8430 to avoid clashing with Brain on 8420)
.venv_sidecar/bin/python sidecar/tool_server_stub.py --port 8430

# Terminal 3 — replay
.venv_sidecar/bin/python sidecar/test_replay.py \
    --model gemma-4-26B-A4B-it-MLX-4bit \
    --base-url http://localhost:8000 --api-key brain \
    --out-dir /tmp/sidecar_phase1_gemma26
```

Listens on `127.0.0.1:8421`. Endpoints:

- `POST /turn` — streaming SSE response (interactive chat, scheduler)
- `POST /turn?stream=false` — single JSON response (background tasks, classifiers, summarisers)
- `GET /health` — `{"ok": true, "anthropic_version": "..."}`

## Request body (`POST /turn`)

```json
{
  "model": "gemma-4-26B-A4B-it-MLX-4bit",
  "base_url": "http://localhost:8000",
  "api_key": "brain",
  "system": "...system prompt...",
  "messages": [{"role": "user", "content": "..."}],
  "tools": [{"name": "exa_search", "description": "...", "input_schema": {...}}],
  "temperature": 0.2,
  "top_p": 0.85,
  "max_tokens": 16000,
  "max_rounds": 25,
  "tool_endpoint": "http://127.0.0.1:8420/v1/tools/call",
  "tool_endpoint_auth": "Bearer <opaque-token>",
  "trace_id": "optional, echoed back in events"
}
```

The sidecar:
1. Builds an `anthropic.Anthropic(...)` client per request.
2. Calls `client.messages.stream(...)` (or `messages.create(...)` for `?stream=false`).
3. Forwards every Anthropic SSE event verbatim. Wraps each in a JSON envelope `{type, data}` on the wire — the inner shape is exactly the Anthropic event.
4. On `content_block_start` for a `tool_use` block: collects the block until `content_block_stop`, then POSTs to `tool_endpoint` with `{name, args, session_id?, agent_id?, trace_id}`. Appends the returned `result` as a `tool_result` block and re-enters the loop.
5. Loops until `stop_reason ∈ {end_turn, max_tokens, stop_sequence}` or `max_rounds` hit.
6. Emits a final `{type: "done", data: {usage_total, rounds, final_text}}` event.

## What this sidecar does NOT do

- No persistence. No DB. No state across turns.
- No middleware between rounds. No message-list rewriting. No tool-result summarisation.
- No retries on tool errors — the model sees `is_error: true` and decides.
- No streaming back-pressure handling beyond the stdlib's. If the caller disconnects mid-stream, the loop continues to completion (so a background-mode caller can poll a future events endpoint — but Phase 1 doesn't implement that).

## Testing in isolation (Phase 1)

`sidecar/test_replay.sh` runs the "Mistral AI News" scheduled task end-to-end through the sidecar:

1. Starts a tiny tool-server stub (`sidecar/tool_server_stub.py`) that wraps the same standalone tools used by `eval/sdk_harness/run.py`.
2. POSTs `/turn` to the sidecar.
3. Streams SSE, dispatches tool calls back into the tool stub.
4. On done, prints the final report path.

Acceptance: produces a real ~5-7 KB report.md on both `gemma-4-26B-A4B-it-MLX-4bit` (oMLX) and `mistral-medium-3.5` (CLIProxyAPI).
