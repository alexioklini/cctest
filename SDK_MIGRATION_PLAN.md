# Brain Agent → Anthropic SDK Sidecar Migration Plan

**Status:** Draft for review, 2026-05-13
**Reference implementation:** `eval/sdk_harness/run_sdk.py` (the SDK-based harness validated against CLIProxyAPI + oMLX with mistral-medium-3.5, gemma-4-26b, gemma-4-e4b).

---

## Goal

Replace Brain's native agentic loop with the Anthropic Python SDK running in a **separate sidecar process**. Brain becomes a "shell" that owns:
- Auth / sessions / users / quotas / GDPR routing
- System prompt assembly (project memory, disciplines, user profile, etc.)
- Tool dispatch (via HTTP MCP endpoint)
- Persistence (chats, costs, traces, artifacts)
- UI (web, telegram, tui)
- Background daemons (mempalace mining, KG extraction, project sync, etc.)

The sidecar owns:
- The actual LLM call (`anthropic.Anthropic(...).messages.create(...)`)
- The agentic loop (round 0 → tool_use → tool_result → … → end_turn)
- Streaming back to Brain via SSE

**Hard rule:** Once the loop has begun, Brain does NOT touch data flowing in or out of the SDK. No middleware, no compression, no compaction, no guards, no envelope wrapping, no result summarization. The wire bytes match the reference harness exactly.

---

## Non-negotiables

1. **No `claude_cli` / `brain.py` import in the sidecar process.** Past breakage (`feedback_sidecar_no_claude_cli`): importing the main Brain module alongside `anthropic` SDK broke anyio's subprocess streaming. The sidecar must be a clean Python venv with `anthropic` and nothing Brain-specific in its import graph.
2. **No middleware between LLM rounds.** Every variance flag and every middleware function currently between rounds gets deleted, not migrated. Specifically removed:
   - `compress_old_middleware`, `microcompact_middleware`, `tool_result_budget_middleware`, `pyexec_hint_middleware`, `read_doc_cache` middleware
   - `intent_action_guard`, `diminishing_returns_guard`, `max_output_recovery`, `truncated_tool_call_discard`
   - `proactive_round0_compaction`, `reactive_400_compaction`
   - Worker subagent envelope (`auto_isolation`, `worker_subagent`, `force_all_light`, `tool_result_summariser`)
   - Lossless Context Manager (`ContextManager`, `context.db`, `context_search`/`context_detail`/`context_recall` tools)
   - Guided execution (`run_guided_execution`, both granularities)
   - Citation validator + synchronous re-round
   - Tool dedup (`tool_dedup`)
3. **No content-modifying tool wrappers.** When the sidecar calls a Brain tool, Brain returns the raw result string. No summarization, no truncation, no cache stubs.
4. **System prompt is assembled by Brain ONCE per turn and frozen.** The sidecar gets a string and passes it verbatim to `messages.create(system=...)`. No mid-turn rewrites.
5. **Pre-loop gates are allowed** (GDPR routing, quota check, model selection). They run before the sidecar is invoked. They can swap the model or refuse the call, but they cannot alter the message list once the sidecar starts.

---

## Architecture

```
Browser / TUI / Telegram / Scheduler / Daemons
        │
        ▼
Brain server (Python, port 8420)
        │
        ├── auth, sessions, persistence, UI
        │
        ├── /v1/chat handler:
        │     1. Resolve user/session/agent/project context
        │     2. Pre-loop gates: GDPR scan, quota check, model swap if needed
        │     3. Build system prompt (project memory, disciplines, user profile)
        │     4. Build tool list (Anthropic schemas — see eval/sdk_harness/run.py:_TOOL_SCHEMAS)
        │     5. POST to sidecar /turn
        │     6. Drain SSE from sidecar; persist deltas, forward to browser SSE
        │     7. On 'done', persist final assistant message + tool events
        │
        ├── /v1/tools/call:  HTTP endpoint the sidecar calls per tool_use
        │     - Maps Anthropic tool name → existing TOOL_DISPATCH entry
        │     - Returns raw result string (no truncation, no summarization)
        │
        ▼
Sidecar process (Python, port 8421, fresh venv with `anthropic` only)
        │
        ├── /turn endpoint (POST):
        │     1. Build anthropic.Anthropic(api_key=..., base_url=...) client per request
        │     2. Open SSE response to caller
        │     3. Loop:
        │          - client.messages.create(stream=True, ...)
        │          - Forward every event verbatim as SSE to caller
        │          - On tool_use, POST to Brain /v1/tools/call and append tool_result
        │          - Repeat until stop_reason ≠ 'tool_use'
        │     4. Emit 'done' SSE event with the final message + usage
        │
        └── No persistence, no DB, no state across turns.
```

### Process model

- Sidecar is a long-lived process. One per Brain instance.
- Launched by Brain at startup (subprocess.Popen + watchdog thread, same pattern Brain currently uses for mempalace daemons).
- Crash → Brain restarts it. Crash 3× in 60s → Brain surfaces an error and stops auto-restarting.
- Sidecar listens on `127.0.0.1:8421` (localhost only).

### Why not in-process?

- `feedback_sidecar_no_claude_cli` (35-day-old memory): when claude_cli was imported in the same process as `anyio.run(query(...))`, streaming broke. The Anthropic SDK uses anyio under the hood. Until we have a Brain process that doesn't import any of the legacy module's side-effect-heavy code, sidecar is the safe bet.
- Even if today's `anthropic 0.101.0` is fine in-process, isolating it future-proofs against SDK upgrades introducing async machinery again.
- Sidecar isolation also means a runaway loop / OOM in the SDK can't crash Brain.

---

## File-level plan

### NEW files

```
sidecar/                         # fresh, isolated venv lives here
  pyproject.toml                 # only anthropic + uvicorn + fastapi
  sidecar.py                     # the entire sidecar — ~250 LOC
  README.md
.venv_sidecar/                   # gitignored, created at install time

handlers/sidecar_proxy.py        # new Brain handler that proxies /v1/chat → sidecar /turn
                                 # streams sidecar SSE back to browser SSE
                                 # ~150 LOC

server_lib/tool_mcp.py           # new — exposes /v1/tools/call + /v1/tools/list
                                 # wraps existing TOOL_DISPATCH; no result mutation
                                 # ~200 LOC
```

### MODIFIED files

```
handlers/chat.py                 # rewrite _handle_chat to:
                                 #   - keep pre-loop gates
                                 #   - call sidecar_proxy.run_turn() instead of send_message
                                 #   - persist messages + tool events from sidecar SSE
                                 # ~50% of the file deleted, ~30% rewritten

brain.py                         # large deletions:
                                 #   - send_message / _handle_openai_response (entire native loop)
                                 #   - run_guided_execution + decomposer
                                 #   - all _middleware_* functions
                                 #   - LCM ContextManager + context.db code
                                 #   - worker subagent wrapping
                                 #   - tool result summariser cascade
                                 #   - citation validator
                                 #   - _build_system_prompt: keep, it's pre-loop
                                 #   - resolve_provider_for_model: keep
                                 #   - GDPR / quota gates: keep but call them BEFORE the sidecar
                                 # Estimate: -8000 to -10000 LOC

server.py                        # mount the new /v1/tools/call + /v1/tools/list endpoints
                                 # remove /v1/chat/stream re-attach logic (moves into sidecar_proxy)
                                 # remove all variance-flag code paths

config.json                      # remove `variance` section entirely
                                 # add `sidecar` section: { url, api_key, timeout }
```

### DELETED files / modules

```
engine/loop.py                   # if it exists — the entire extracted loop logic
engine/provider.py               # LocalProviderQueue, etc.
engine/tasks.py                  # if related to the loop
eval/citation_validator.*        # all citation re-round machinery
                                 # (eval harness retains; only the production server-side validator goes)
```

(Exact list of `engine/` deletions needs a separate file walk; calling that out as a TODO in step 1.)

---

## Sidecar wire protocol (Brain ↔ sidecar)

### POST /turn

Request body:
```json
{
  "model": "gemma-4-26B-A4B-it-MLX-4bit",
  "base_url": "http://localhost:8000",
  "api_key": "brain",
  "system": "...assembled system prompt...",
  "messages": [
    {"role": "user", "content": "..."}
  ],
  "tools": [
    {"name": "mempalace_query", "description": "...", "input_schema": {...}},
    ...
  ],
  "temperature": 0.2,
  "top_p": 0.85,
  "max_tokens": 16000,
  "max_rounds": 25,
  "tool_endpoint": "http://127.0.0.1:8420/v1/tools/call",
  "tool_endpoint_auth": "Bearer <session-token>",
  "trace_id": "..."
}
```

Response: SSE stream. Events are forwarded verbatim from the SDK:
- `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`
- Plus Brain-overlay events for tool dispatch:
  - `tool_call_started` (tool name + args)
  - `tool_call_completed` (tool name + result_chars + elapsed_s)
  - `done` (final assistant message + total usage)
  - `error`

The sidecar passes Anthropic SSE events through unchanged. Brain's chat handler interprets them and persists.

### POST /v1/tools/call (Brain endpoint, sidecar calls into Brain)

Request:
```json
{
  "name": "mempalace_query",
  "args": {"query": "Multilogin"},
  "session_id": "abc123",
  "agent_id": "main",
  "user_id": "u_xxx",
  "trace_id": "..."
}
```

Response:
```json
{
  "result": "<raw tool result string, JSON-stringified if dict>",
  "is_error": false,
  "elapsed_ms": 234
}
```

Brain's TOOL_DISPATCH runs in-process. Auth comes from a one-time token in the `tool_endpoint_auth` header that Brain mints per turn (so the sidecar can't be tricked into calling Brain tools out of band).

---

## Migration phases

### Phase 0 — Sign-off (you, today)

- Read this plan.
- Confirm the deletion list is acceptable. Concretely: are you OK losing citation re-round, the LCM compaction system, and guided execution? They're load-bearing for the 0.873 policy eval mean. Eval will need re-baselining.
- Confirm the sidecar process model (separate venv, supervisor-style auto-restart) is fine.

### Phase 1 — Standalone sidecar, no Brain wiring (1-2 days)

Build `sidecar/sidecar.py` and the tool-MCP endpoint, run both standalone. Hit them with curl and verify:
1. POST /turn → SSE event stream matches what the SDK harness produces (same events, same order, same body shapes)
2. POST /v1/tools/call → returns Brain tool output unchanged
3. Replay the scheduled task end-to-end through the sidecar (curl-driven), confirm it produces the same kind of report as `eval/sdk_harness/run_sdk.py` does today

Acceptance: scheduled task replay via sidecar produces a real report on gemma-4-26b AND mistral-medium-3.5, end-to-end, no Brain integration yet.

### Phase 2 — Chat handler migration (2-3 days)

Rewrite `handlers/chat.py`:
- Keep: auth check, session resolution, model resolution, GDPR pre-scan, quota gate, project research-mode toggle, system prompt assembly, tool list assembly.
- Replace: `send_message_with_fallback(...)` call → `sidecar_proxy.run_turn(...)`
- Add: SSE relay from sidecar to browser

Smoke test on a single chat session before touching anything else.

Acceptance: a chat in the browser works end-to-end through the sidecar. Streaming text appears. Tool calls fire. Persistence works.

### Phase 3 — Scheduler migration (1 day)

`engine/scheduler` (`Scheduler._execute_scheduled`) currently calls `_run_delegate`. Replace with `sidecar_proxy.run_turn(...)`.

Replay the "Mistral AI News" schedule task. Compare to today's gemma-4-26b SDK harness result (run 807-equivalent). Should match.

Acceptance: scheduled task on gemma-4-26b produces a real report. Same on mistral-medium.

### Phase 4 — Background tasks migration (2-3 days)

All other `_run_delegate` and `send_message` call sites:
- Chat summary generation
- Next-prompt suggestion
- Memory classifier (`classify_chat_for_memory`)
- User profile maintenance (`_user_profile_run_llm`)
- KG extraction
- Tool result summariser (now obsolete — DELETE this entire call path)
- Refine endpoint
- Soul chat
- delegate_task (agent-to-agent)
- TUI / CLI one-shot

Each becomes a sidecar /turn call. Tools=False ones (transform-only callers) still call /turn but with an empty tools list.

Acceptance: every existing LLM-call code path resolves through the sidecar. No remaining `send_message` or `_run_delegate` calls.

### Phase 5 — Deletion pass (1 day)

After Phase 4 lands and nothing calls them:
- Delete `send_message`, `_handle_openai_response`, all `_middleware_*` functions
- Delete `run_guided_execution`, `_GUIDED_*` constants, all guided-execution UI in web/js
- Delete LCM (`ContextManager`, `context.db` schema, all `context_*` tools)
- Delete worker subagent wrapping + `_summarise_tool_result` cascade
- Delete `_VARIANCE_DEFAULTS` + all `_variance_flag` call sites
- Delete citation validator + re-round
- Delete `engine/loop.py`, `engine/provider.py` etc. (per the file walk done in Phase 1)
- Delete `/v1/variance` admin endpoints + the "Variance switches" Settings tab
- Delete the Lossless Context Manager UI surface (`compacting`/`compacted` SSE events, `/v1/context/*` endpoints)

Tag the commit `v9.0.0` per the existing changelog convention.

Acceptance: `git grep -E 'send_message|_run_delegate|_handle_openai|run_guided_execution|_middleware_|_variance_flag|ContextManager|_compact_conversation|_microcompact' brain.py server.py engine/` returns nothing.

### Phase 6 — Re-baseline eval (half-day)

The 15Q policy eval needs a fresh baseline because:
- Citation validator is gone (the re-round was lifting brain mean ~0.05)
- Guided execution is gone
- LCM is gone (probably negligible impact on the eval, but unproven)
- The disciplines stay in the system prompt — they still work

Run `eval/run_sdk.py` against the new Brain. Document the new baseline. If it drops below ~0.83 we know we lost real quality and need to debug. If it stays in the 0.85-0.90 band (where SDK-full already landed), the migration is a net wash on quality with a massive simplification win.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SDK streaming doesn't work against CLIProxyAPI Anthropic endpoint | Med | High | Test in Phase 1 with `client.messages.stream(...)` before Brain wiring. If broken, fall back to non-streaming (UX regression — collect full response then bulk-emit). |
| Sidecar process crashes mid-turn lose the user's request | Low | Med | Brain's `_handle_chat` retains the user message in DB before calling sidecar. On crash, surface error + offer retry. Sidecar SSE drain logic must handle premature disconnect. |
| Tool-MCP auth bypass (sidecar token leaks) | Low | High | Mint per-turn nonce, expire after turn ends. Sidecar listens on 127.0.0.1 only. |
| oMLX `/v1/messages` endpoint has quirks the SDK doesn't tolerate (e.g. `input_tokens: 0` reported) | Confirmed | Low | Already seen; cosmetic. Sidecar should NOT validate usage shapes — pass through whatever the model returned. |
| Loss of citation discipline lowers policy eval mean | Med | Med | Re-baseline; if drop > 0.05, consider a post-hoc validator that runs in Brain (NOT in the loop) and surfaces a warning to the user without re-rounding. Don't re-introduce loop-internal middleware. |
| Loss of LCM means long chats hit context limits | Med | Med | The SDK's native handling will surface a clear 400 error on overflow. Brain shows the error. User starts a new chat. This is the price of "no in-loop middleware." Alternative: a pre-loop summariser that runs BEFORE the sidecar call when message list is large — still respects the rule. |
| Background tasks (KG extraction, classifier) get slower because each is now a sidecar HTTP roundtrip | Low | Low | These are batched / async. Latency is dwarfed by the LLM call itself. |
| Importing `anthropic` in the sidecar process is fine; importing it in Brain's process is broken | Confirmed risk (per memory) | High | Architecture mitigates: sidecar is a separate process. Brain does NOT import `anthropic`. |
| Streaming SSE through two hops (sidecar → Brain → browser) introduces buffering | Med | Med | Use `flush=True` everywhere; line-buffered SSE; no intermediate JSON parsing that batches events. Validate with timing instrumentation in Phase 2. |

---

## What this plan does NOT include (out of scope)

- Migrating to multi-provider routing through CLIProxyAPI for everything. Mistral direct API stays as-is; oMLX stays as-is. The sidecar accepts any `base_url` + `api_key` pair and passes through.
- Re-doing the PI SDK / code mode path. Code mode is currently dead; leave it dead.
- UI changes beyond removing the variance-switches tab and guided-execution rendering.
- Migrating Workflows. They have their own `.flow` DAG and `tool_ask_llm` — out of scope for now; can use the sidecar in a follow-up.
- Hooks (PreToolUse/PostToolUse). Brain's current hooks fire from `_execute_tool` which still runs in-process. They survive untouched.

---

## Decisions confirmed 2026-05-13

1. **Sidecar HTTP framework**: stdlib `http.server` + `socketserver.ThreadingMixIn`. No FastAPI, no uvicorn. Matches Brain's existing server.py style. Sidecar venv installs ONLY `anthropic`.
2. **Sidecar config**: top-level `sidecar: { url, auto_start, ... }` block in `config.json`.
3. **Variance switches tab + `/v1/variance` endpoints + `_VARIANCE_DEFAULTS` + `_variance_flag`**: deleted entirely in Phase 5. No hide-and-keep — cleaner, prevents future "let's re-enable just this one" drift.
4. **Background tasks (chat-classifier, summariser, next-prompt suggestion, user-profile maintenance, KG extraction, refine, etc.)**: sidecar exposes `POST /turn?stream=false`. Returns a single JSON body with the final message + total usage instead of an SSE stream. Saves SSE-parsing complexity for callers that don't need incremental output. Interactive chat + scheduler use the streaming path; everything else uses the JSON shortcut.

---

## What I will NOT do without explicit sign-off

- Touch any code in this plan before you say "go on Phase 1."
- Reuse any code from the deleted v6/v7 sidecar attempts (`sdk_sidecar.py`, `pi_sidecar/`). The reference is `eval/sdk_harness/run_sdk.py`, period.
- Skip Phase 1's standalone validation. The whole point is that the sidecar matches the harness byte-for-byte; that has to be demonstrated before Brain wiring.
