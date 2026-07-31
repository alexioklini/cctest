---
name: SDK sidecar migration — Phase 1 done, Phase 2+ pending (2026-05-13)
description: Brain Agent → Anthropic SDK sidecar migration. Phase 1 (standalone sidecar, no Brain wiring) shipped and validated. Next session resumes Phase 2 from SDK_MIGRATION_HANDOVER.md.
type: project
originSessionId: fe3309b9-f4b8-4c17-9cce-94197e4eface
---
Brain's native agentic loop is being replaced with an Anthropic SDK sidecar. The user's hard rule: Brain does NOT modify data flowing in or out of the loop once a turn starts — same bytes on the wire as the standalone harness at `eval/sdk_harness/run_sdk.py`.

**Two reference docs in repo root** (read both before continuing):
- `SDK_MIGRATION_PLAN.md` — the master plan: 6 phases, file-level deletions, sidecar protocol, risks.
- `SDK_MIGRATION_HANDOVER.md` — session-handover doc: Phase 1 results, exactly what Phase 2-6 do, file-by-file. **This is the source of truth for resuming.**

**Phase 1 status: done.** Built and validated:
- `sidecar/sidecar.py` (stdlib http.server, port 8421, streams via `client.messages.stream()`, blocking JSON via `?stream=false`)
- `sidecar/tool_server_stub.py` (standalone tool dispatcher, port 8430, wraps `eval/sdk_harness/run.py:_dispatch`)
- `sidecar/test_replay.py` (E2E driver for the "Mistral AI News" scheduled task)
- `.venv_sdk/` contains `anthropic 0.101.0`

Acceptance proof: both `mistral-medium-3.5` (via CLIProxyAPI :8317) and `gemma-4-26B-A4B-it-MLX-4bit` (via oMLX :8000) produced real ~6.4-8.6 KB cited reports through the sidecar. Same workload Brain's native loop failed on (run 807).

**Decisions locked (don't re-litigate):**
1. Sidecar = stdlib http.server, no FastAPI/uvicorn.
2. Sidecar config = top-level `sidecar:` block in `config.json`.
3. Variance flag system + admin tab = fully deleted in Phase 5 (not hidden).
4. Background tasks use `POST /turn?stream=false` JSON-shortcut endpoint.
5. SDK runs ONLY in the sidecar subprocess. Brain's main process does NOT import `anthropic` (preserves old `feedback_sidecar_no_claude_cli` constraint).
6. Tool dispatch goes Brain → sidecar → HTTP back to Brain `/v1/tools/call`. Brain's TOOL_DISPATCH runs in-process. No result mutation, no summarisation, no truncation.
7. System prompt assembled ONCE per turn by Brain (`_build_system_prompt`), passed verbatim to sidecar.

**What gets deleted from Brain in Phase 5** (the rule "no data-flow modification" makes all this go):
- `send_message`, `_handle_openai_response`, `_run_delegate`
- All `_middleware_*` (compress_old, microcompact, tool_result_budget, pyexec_hint, read_doc_cache)
- All guards (intent_action, max_output_recovery, diminishing_returns, truncated_tool_call_discard)
- Variance flag system (all 17 flags, `_variance_flag`, the admin tab)
- Guided execution (decomposer, `run_guided_execution`, all `_GUIDED_*`)
- Worker subagent envelope + `_summarise_tool_result` cascade
- Lossless Context Manager (`ContextManager`, `context.db`, `context_*` tools, `compacting`/`compacted` SSE)
- Citation validator + synchronous re-round
- All hand-rolled streaming parsers (`_InlineThinkingSplitter`, `_parse_gemma_tool_calls`)
- `_compact_conversation`, `_microcompact`, `_compress_old_tool_results`

**Phase 2 first step** (when resuming):
Build `handlers/sidecar_proxy.py` + `server_lib/tool_mcp.py`. Rewrite `_handle_chat` to call `sidecar_proxy.run_turn(...)` instead of `send_message_with_fallback(...)`. Keep pre-loop gates (GDPR, quota, system prompt assembly). HANDOVER.md has the file-level checklist.

**Eval baseline expectations after Phase 5:**
- Current Brain v8.37.0 baseline: 0.873 (with citation re-round + LCM + variance flags)
- SDK-full harness today: 0.893 (without re-round/LCM/variance — just the disciplines in system prompt)
- Phase 6 acceptance: result lands in 0.85-0.90 band. If below 0.83, investigate before declaring v9.0.0 done.

**Known Phase 1 cleanup deferred to Phase 2:**
- `sidecar/test_replay.py` emits `FATAL: TimeoutError` after `done` (test client keeps reading; cosmetic — work succeeded). Either sidecar closes connection on `done`, or test client breaks loop on `done`/`error` event.
- Pydantic serialization warnings from `anthropic 0.101.0` on `ParsedTextBlock` (Mistral-via-CLIProxy quirk). Cosmetic, no data impact.
- Sidecar doesn't yet handle client-disconnect-mid-stream as a cancel signal. Phase 2 wires `session.cancel_token` → `POST /cancel/{turn_id}` on sidecar.
- Reload-during-turn UX: Brain's current `LiveStream` re-attach won't survive without a replay buffer in the sidecar. Recommendation in HANDOVER.md: accept regression for v9.0.0, add buffer in v9.1.0 if users complain.

**Two open architectural decisions for next session:**
1. Sidecar cancel: turn_id mint by sidecar (returned in initial SSE event) or client-provided?
2. Reload-during-turn: accept regression (recommendation) or add replay buffer to sidecar in Phase 2?
