# SDK Migration — Session Handover (2026-05-14 → next session)

This is the working document for the SDK sidecar migration. Read this first if you're picking up the work in a new session.

The full plan is in **`SDK_MIGRATION_PLAN.md`** — don't restate it, just reference it.

---

## TL;DR — where we are right now

**Phase 0 (sign-off):** done.

**Phase 1 (standalone sidecar):** done — see prior handover; the standalone scenario produced cited reports on both `mistral-medium-3.5` (CLIProxyAPI) and `gemma-4-26B` (oMLX).

**Phase 3 (scheduler):** done — `brain.py:_execute_scheduled` routes through `sidecar_proxy.run_turn` when `sidecar_enabled()`, with full GDPR pre-flight, per-schedule `tool_profile`, and Mistral's `disable_parallel_tool_use` plumbing. Falls back to `_run_delegate` only when the sidecar is disabled. Gate-2 (manual "Run now" against three models) was not formally run — defer to next session.

**Phase 4 (background tasks): DONE.** 13 sites migrated, one commit each (`feat(sdk-phase4): …` series 2026-05-14):

| # | Site | Notes |
|---|---|---|
| 1 | `/v1/refine` (admin.py) | Also added the `sidecar_proxy.background_call(...)` helper used by every subsequent site. |
| 2 | `/v1/agents/<id>/soul-chat` (admin.py) | |
| 3 | `_generate_chat_summary` (server.py) | Sidebar title autogen. |
| 4 | `_user_profile_run_llm` (server.py) | User profile daemon. Also pins `current_user_id=uid` thread-local. |
| 5 | `generate_next_prompt_suggestion` (brain.py) | Dropped the now-unused inline provider resolution. |
| 6 | `classify_chat_for_memory` (brain.py) | Lifted OpenAI `system` message into Anthropic `system_prompt`. |
| 7 | `_describe_image_with_vision` (brain.py) | Image blocks pass through unchanged. |
| 8 | `tool_ask_llm` (brain.py) | Workflow node, `tools=False`. |
| 9 | `_auto_memory_extract` (brain.py) | JSON extractor; `MemoryStore(agent_id)` still used downstream. |
| 10 | `promote_memory_to_skill` (brain.py) | |
| 11 | `kg_extract` (engine/kg_extract.py) | Preserved 2-retry-on-connection-refused loop. |
| 12 | `CodeGraph.generate_summaries` (brain.py) | |
| 13 | `run_citation_reround` (brain.py) | Last raw-OpenAI-POST site flagged in the Phase 4 caveat. |

**Decision baked into Phase 4 (option B):** the admin picks the model for each site via the existing per-site config slots (`tool_config.refinement.model`, `attachment_image_model`, etc.). Brain does NOT filter the dropdowns to Anthropic-shape providers. If the admin picks a non-Anthropic model (Mistral direct, oMLX OpenAI-shape), the sidecar returns an empty reply and the site falls back per its own behavior (refine echoes input, chat-summary keeps old title, etc.). This is intentional — the alternative (sniff provider type and add an OpenAI-shape transport) was rejected.

**Phase 2 (chat handler migration): DONE and gate-1 PASSED.**

The Phase 2 sidecar path is wired end-to-end. The browser/eval/HTTP chat flow goes:

```
client → POST /v1/chat → handlers/chat.py worker
       → handlers/sidecar_proxy.run_turn()
       → POST http://127.0.0.1:8421/turn (SSE)
       → sidecar runs anthropic SDK loop
       → on each tool_use: sidecar POSTs /v1/tools/call back to Brain
       → server_lib/tool_mcp.handle_tools_call → engine.TOOL_DISPATCH
       → sidecar streams SSE back → proxy translates to Brain's event_callback
       → LiveStream emits to subscribers → client receives done event
```

**Gate-1 result (15-question policy eval, mistral-medium-3.5 via CLIProxyAPI):**

| Run | Brain mean | Δ vs gold | Notes |
|---|---|---|---|
| SDK-full standalone harness (reference) | 0.893 | — | Hand-tuned eval/sdk_harness/run.py |
| v8.37.0 native loop (pre-Phase-2) | 0.873 | — | |
| Phase 2 + harness tools + harness prompt | 0.85 | 0.00 | Full parity diagnostic |
| **Phase 2 + Brain tools, clean defaults** | **0.82** | **−0.07** | **Gate-1 PASS — shipping defaults** |

Gate-1 bar: ≥0.82 brain mean. We hit exactly 0.82 with all reverts in place. Within Mistral judge noise (±0.09) of both prior baselines.

---

## What's in the repo now (changes since the prior handover)

### New files
- `handlers/sidecar_proxy.py` (~430 LOC) — proxy: builds Anthropic-shape payload, mints per-turn nonce, POSTs `/turn`, drains SSE, translates Anthropic events → Brain `event_callback` vocabulary, handles cancel via `POST /cancel/<turn_id>`. Also exposes `run_turn_blocking()` for Phase 3/4.
- `server_lib/tool_mcp.py` (~270 LOC) — `handle_tools_call` reconstitutes thread-locals from the sidecar's `context` payload, validates nonce, dispatches to `engine.TOOL_DISPATCH` (+ MCP fallback), captures result for the proxy's downstream `tool_result` SSE event.

### Modified files
- `sidecar/sidecar.py` — dropped baked-in `temperature=0.2/top_p=0.85` defaults; added `top_k`/`stop_sequences`/`thinking` passthrough; added `X-Turn-Id` header + `POST /cancel/<turn_id>` with per-turn cancel events; threads `tool_context` and `tool_use_id` through dispatch; **forces `Connection: close` on stream end** (critical: HTTP/1.0 + keep-alive made the proxy's `urlopen` iterator block on socket read after `done`).
- `handlers/chat.py` — `worker()` now branches on `sidecar_proxy.sidecar_enabled()`. Sidecar path skips guided execution + `send_message_with_fallback`. Middleware, citation validator, persistence all unchanged. Sidecar errors are surfaced AS an assistant reply (`*(Sidecar error: …)*`) so the `done` event still fires for HTTP clients that only listen for `done`.
- `server.py` — imports `tool_mcp`, exposes `POST /v1/tools/call` (auth-exempt, nonce-protected, localhost-only), adds subprocess supervisor with 3-in-60s circuit breaker. Pulls `sidecar` block from `config.json` into `server_config`.
- `brain.py`:
  - `tool_mempalace_query`: chunk-substitution pass is now gated by `mempalace.chunk_substitute.enabled` (default false).
  - `tool_mempalace_kg_search`: accepts either `predicate` (structured mode) OR `query` (free-text substring mode across subject/predicate/object). Was `predicate`-only before.
  - Tool schema for `mempalace_kg_search` updated; `predicate` removed from `required`.
- `config.json`:
  - `providers.CLIProxyAPI` — new provider, `base_url: "http://127.0.0.1:8317/v1"`, `type: "anthropic"`, `api_key: "brain-agent"`.
  - `models.CLIProxyAPI/mistral-medium-3.5` — scoped model entry, `base_model_id: "mistral-medium-3.5"`, `enabled: true`.
  - `sidecar: { enabled: true, auto_start: true, url: "http://127.0.0.1:8421", venv_python: ".venv_sdk/bin/python", tool_endpoint_internal: "http://127.0.0.1:8420/v1/tools/call", tool_call_timeout_s: 120 }`.
  - `mempalace.reranker.enabled: false` (was true) — BAAI/bge-reranker-v2-m3 was demoting filename-matched documents.
  - `mempalace.chunk_substitute.enabled: false` (new gate).
- `eval/config.json` — `brain.model: "CLIProxyAPI/mistral-medium-3.5"` (was `Mistral/mistral-medium-3.5`). Mistral direct API does NOT speak Anthropic; the sidecar requires CLIProxyAPI's `/v1/messages` translation.

### Reverted (do not bring back)
- `agents/main/agent.json` token_config — was tightened to 7 harness-style tools during the H1 experiment; reverted to the pre-H1 39-tool loadout. The lean loadout did not improve the eval.
- `handlers/sidecar_proxy.py` harness schema overlay + `use_harness_prompt()` / `harness_system_prompt()` helpers — were added for gate-1 parity diagnostic, reverted after the diagnostic confirmed the gap was tool implementations (reranker + chunk-sub) and not loop transport.
- `server_lib/tool_mcp.py` `_HARNESS_NAMES` routing block — diagnostic only, reverted. Brain's own tool implementations are now the dispatch target.
- `handlers/chat.py` `if sidecar_proxy.use_harness_prompt(): _system_prompt = harness...` branch — reverted.
- `config.json` `sidecar.use_harness_prompt` — removed.

---

## Phase 2 known issues / decisions deferred

1. **`PydanticSerializationUnexpectedValue` warning** from `anthropic 0.101.0` when CLIProxyAPI returns `ParsedTextBlock` content for Mistral. Cosmetic — data flows through fine. Log noise only.
2. **Diagnostic prints** in `handlers/sidecar_proxy.py:run_turn` finally-block emit a one-line summary per turn (`[sidecar-proxy] turn=XXX model=… reply=Nc rounds=N tools=N`). Useful, keep for now.
3. **Tool-result capture race window**: `tool_mcp.handle_tools_call` stores the result via `sidecar_proxy.capture_tool_result(turn_id, tool_use_id, …)` BEFORE returning to the sidecar. The proxy then drains it when translating the sidecar's `tool_dispatch_done` event. Ordering is safe because the sidecar's dispatch is synchronous — the `tool_dispatch_done` event is emitted only after `dispatch_tool_via_http` returns. Don't make tool dispatch async without rethinking this.
4. **`base_url` normalisation**: Brain stores OpenAI-style `http://localhost:8000/v1`; the Anthropic SDK appends `/v1/messages` itself. `sidecar_proxy._normalise_anthropic_base_url` strips one trailing `/v1`. CLIProxyAPI provider is stored with the `/v1` suffix so that `engine.run_citation_reround` (which POSTs to `<base_url>/chat/completions`) also works.
5. **`final_text` accumulation**: `sidecar/sidecar.py:run_turn_streaming` now tracks the most-recent-non-empty round text as `final_text` (not only the no-tool-uses round). Without this, gemma-style models that finish on a tool_use round at max_rounds would return reply=`""`.
6. **Sidecar venv path** (`.venv_sdk/bin/python`) — Phase 1's venv reused. Contains anthropic 0.101.0 only. If the supervisor fails to find this, Brain logs `[sidecar] FATAL: venv python not found at …` and refuses to launch the subprocess. No silent fallback to old loop.

---

## Phase 3 — Scheduler migration (DONE)

**Goal:** `Scheduler._execute_scheduled` calls the sidecar instead of `_run_delegate`.

**Acceptance:** running "Mistral AI News" schedule produces a real report on `gemma-4-26B-A4B-it-MLX-4bit` (oMLX) AND on `CLIProxyAPI/mistral-medium-3.5`. Matches Phase 1's standalone result.

### Files to modify

```
brain.py                     # _execute_scheduled (~line 15693):
                             # Build payload from task_row + system_prompt + tools.
                             # Call sidecar_proxy.run_turn_blocking(...) instead of _run_delegate.
                             # Persist to schedule_history.result + traces + cost_log + artifacts as today.

handlers/sidecar_proxy.py    # run_turn_blocking already exists. Verify it surfaces the same
                             # shape _run_delegate returned (final_text, tool_calls_total, usage_total).
```

### Constraints

- The scheduler-side "report.md is written via write_file inside the artifact session folder" behavior must survive. Sidecar dispatches `write_file` → `/v1/tools/call` → Brain's `tool_write_file` → file lands in `agents/main/artifacts/<date>_sched-<runid>/`. The artifact-session-folder path the user already fixed for run-id truncation must continue to be used.
- `schedule_history.tool_calls` column must populate from sidecar's `tool_calls_total`.
- `schedule_history.trace_id` — sidecar accepts a `trace_id` request field and echoes it. Brain mints `trace_id` before calling sidecar, persists into `schedule_history`, and the sidecar SSE events get logged into traces.db under that id (also wires Phase 4 background tasks).
- **`thread_local.project` MUST be set** before calling `run_turn_blocking` for project-scoped scheduled tasks. Without it, `tool_mempalace_query` falls back to `user__<uid>` and won't find project documents.

### Phase 3 testing path

Gate-2 acceptance run: edit the "Mistral AI News" schedule's `model` field to each of these in turn, hit "Run now":

| # | Model | Expected outcome |
|---|---|---|
| 1 | `CLIProxyAPI/mistral-medium-3.5` | Cited report ~6–9KB, ~60s. |
| 2 | `gemma-4-26B-A4B-it-MLX-4bit` | Cited report ~6KB, 6–10 rounds. |
| 3 | `gemma-4-e4b` (oMLX) | **Expected to fail at the model level** per `feedback_gemma_e4b_unsuitable_for_tools.md`. Pass = clean max_rounds exit, not a hang. Substitute `gemini-2.5-flash` if you want 3-of-3 clean. |

---

## Phase 4 — Background tasks migration (DONE)

All non-interactive LLM calls now route through `sidecar_proxy.background_call(...)` (a thin wrapper around `run_turn_blocking`). Remaining `_run_delegate` / `send_message_with_fallback` callers in the tree are all Phase 5 deletion targets (`_run_delegate_with_fallback`, `_run_guided_execution_impl`, `TaskRunner` worker subagents, the sidecar-disabled fallback branches in `_handle_chat` + `_execute_scheduled`, and TUI / CLI one-shots).

Audit grep before starting:
```bash
grep -rn '_run_delegate\|send_message_with_fallback\|send_message(' brain.py server.py engine/ handlers/ \
    | grep -v changelog | grep -v _summarise_tool_result | grep -v 'send_message,' \
    | grep -v 'def send_message' | grep -v 'def _run_delegate'
```

Expected migrate list (from prior handover, still current):
- `_auto_memory_extract`, `trigger_relationship_discovery`, `_autodream_*` (3 sites), `promote_memory_to_skill`, `classify_task_purpose`, `_generate_chat_summary`, `generate_summaries` (code graph), `summarize_chunk` (code graph), `condense` (LCM — going away in Phase 5 regardless), `tool_ask_llm` (workflows), `generate_next_prompt_suggestion`, `_user_profile_run_llm`, `kg_extract` LLM call, `refine` endpoint, `delegate_task` tool, TUI / CLI one-shots.

Worker subagent calls and guided-execution callers DO NOT migrate — they get deleted in Phase 5.

### Phase 4 caveat — `run_citation_reround`

`engine.run_citation_reround()` in `brain.py:23280` is a raw POST to `<base_url>/chat/completions` (OpenAI-shape). Currently it works because the CLIProxyAPI provider's `base_url` is stored with `/v1` and CLIProxyAPI serves `/v1/chat/completions`. **It does NOT go through the sidecar.** This is the only post-loop LLM call that bypasses Phase 2.

Decision needed in Phase 4: either migrate this to `run_turn_blocking` (consistent) or leave it as a raw OpenAI POST (simpler, but creates a provider-routing surprise for anyone debugging). Recommend: migrate.

---

## Phase 5 — Deletion pass

Unchanged from prior handover. After Phase 4 lands and nothing calls them:

### From `brain.py`
- `send_message`, `send_message_with_fallback`, `_handle_openai_response`
- `_run_delegate` (entire function)
- `run_guided_execution`, `_GUIDED_DECOMPOSE_SYSTEM*`, `_GUIDED_*` constants, `_run_guided_execution_impl`, `_build_guided_prior_results_block`, `_should_guide`, `_extract_user_text`, `_guided_file_task_to_mempalace`
- All `_middleware_*` functions and `_PRE_LLM_MIDDLEWARE` / `_POST_LLM_MIDDLEWARE` lists
- `_VARIANCE_DEFAULTS`, `_variance_flag`, `_variance_normalize`, `_VARIANCE_TAB_SPEC`, related GET/POST `/v1/variance` handlers
- `_compress_old_tool_results`, `_apply_tool_result_budget`, `_microcompact`
- `_summarise_tool_result`, worker-envelope wrap, `auto_isolation` / `worker_subagent` logic
- `_InlineThinkingSplitter`, `_parse_gemma_tool_calls`, `_mistral_thinking_open` handling
- **LCM is NOT deleted** — keep `ContextManager`, `context.db`, `_compact_conversation`, `context_search`/`context_detail`/`context_recall` tools, `compacting`/`compacted` SSE events. **Unwire** them from the agentic loop (no automatic trigger). Add a status-bar button in the context-window display that fires `_compact_conversation` on demand.
- Citation validator + `_citation_reround` code path (research_mode now relies on disciplines in the system prompt; no post-hoc validator)
- `_max_output_recovery_count`, `_intent_action_recovery_count`, `_INTENT_ACTION_PATTERNS`, intent-action guard
- Truncated-tool-call discard, diminishing-returns guard, pyexec hint middleware

### From `server.py`
- `/v1/variance` endpoints (GET/POST)
- LiveStream + streaming_text persistence — **DECIDE in Phase 3 or 5**: preserve reload-during-turn via a sidecar-side replay buffer, or accept the regression. Recommendation: accept for v9.0.0, add buffer in v9.1.0 if needed.

### From `web/js`
- Variance switches tab (`settings.js`)
- Guided execution UI
- Citation re-round visual indicator (if any)
- **KEEP** LCM SSE handlers (`compacting`, `compacted`) — still used by the manual LCM trigger button.

### From `engine/`
File walk needed — at least `engine/loop.py`, possibly `engine/provider.py` (LocalProviderQueue is dead if sidecar owns provider routing). Confirm by grepping for callers before deletion.

### From `config.json`
- Entire `variance_kill_switches` block
- Any model-level `guided_execution` / `guided_execution_granularity` fields

### From `CLAUDE.md`
- Rewrite "Agentic Loop" section to describe the sidecar architecture
- Delete "Format-Aware Thinking Level" section (SDK handles reasoning natively)
- Delete "Guided Prompt Execution" section
- Rewrite "Lossless Context Manager" section (no automatic trigger)
- Rewrite "Tools" section for HTTP MCP dispatch
- Update "Key Invariants"

Tag the commit `v9.0.0`.

---

## Open architectural questions for Phase 3+

1. **Cancel turn_id propagation**: Brain mints `turn_id` and passes via `X-Turn-Id` header. Phase 2's `_watch_cancel` thread polls `session.cancel_token` and POSTs `/cancel/<turn_id>` to sidecar when triggered. For Phase 3 (scheduler) and Phase 4 (background tasks), there's no Session.cancel_token — they have their own cancellation paths. Decide a uniform API.

2. **Reload-during-turn (gate-1 deferred)**: Brain currently supports closing the browser tab mid-stream and reopening to re-attach to the live turn via `GET /v1/chat/stream?session_id=X`. Phase 2 preserves this through the existing LiveStream pattern (proxy emits to LiveStream just like the old worker did). When Phase 5 deletes the worker plumbing, this either survives (LiveStream stays) or doesn't (LiveStream goes with the rest). Decide before Phase 5.

3. **MCP tool dispatch**: `server_lib/tool_mcp._dispatch` calls `engine._mcp_manager.call_tool` for names not in `TOOL_DISPATCH`. The chat worker's thread-local `mcp_manager` is set; the request-handler thread that handles `/v1/tools/call` has it re-set via `_apply_context`. Test this on a chat with an active MCP server before Phase 5 — there's a thread-local-leakage risk.

4. **CLIProxyAPI quota** (`feedback_cliproxy_quota`): CLIProxyAPI shares Claude's 5-hour quota. Heavy eval/scheduler use against it can exhaust it. Phase 3/4 should respect rate-limiting hints from the sidecar.

5. **`tool_mempalace_query` defaults**: shipping with `reranker.enabled: false` + `chunk_substitute.enabled: false`. These are NOT permanent decisions — they were proven net-neutral-to-slight-improvement on this 15Q eval (n=1 run, ±0.09 noise). A future run might justify re-enabling, especially the reranker. Memory note worth writing: keep flags off as default; re-evaluate on the next major retrieval change.

6. **Per-project system prompt scope**: Brain's `_build_system_prompt` is ~20KB; the harness's `system_prompt_full.md` is ~3.2KB. Gate-1 parity used the harness prompt and hit 0.85. Brain's prompt at 0.82 was −0.03. A prompt-tightening pass (remove KG block when KG tools aren't in the active list, remove tool-guidance for inactive tools, factor out the BINARY DOCUMENTS block into project-mode only) could close that gap without losing scaffolding. Backlog item.

---

## Acceptance gates — running them

### Gate 1 — DONE
- Run: `BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --skip-gold --reuse-results eval/results/20260513T154403_disc-none_sdk-full --label <name>`
- Bar: brain mean ≥ 0.82.
- **Latest result: 0.82** at `eval/results/20260514T112200_disc-none_phase2-braintools-clean/`.

### Gate 2 — Run at end of Phase 3
- "Mistral AI News" schedule via UI's "Run now" button against three model configs above.
- Pass = each produces a cited report (or clean max_rounds exit for e4b).

### Gate 3 — Run at end of Phase 4, before Phase 5
- Eval re-run (same command as gate 1) + exercise each background-task family (`_user_profile_run_llm`, `_generate_chat_summary`, `tool_ask_llm`, `kg_extract`, `refine`).
- Bar: same as gate 1, plus each family produces structurally-identical output to today.

---

## Files to read on session start

1. **`CLAUDE.md`** — repo invariants (especially "Resumable Streaming", "Multi-Provider Routing", "Tools", "Concurrency & Thread Safety")
2. **`SDK_MIGRATION_PLAN.md`** — the master plan
3. **This file** — current state
4. **`handlers/sidecar_proxy.py`** — Phase 2's proxy, the integration point for Phase 3/4
5. **`server_lib/tool_mcp.py`** — the tool-dispatch entry point the sidecar calls back into
6. **`sidecar/sidecar.py`** — the actual sidecar process

Memory items worth recalling (these will surface via auto-memory but flagging by topic):
- `project_eval_judge_mistral_self.md` — judge noise floor ±0.09 mean, ±0.38 max
- `feedback_cliproxy_quota.md` — runaway eval can exhaust Claude's 5-hour quota
- `feedback_brain_log_file.md` — daemon prints land in `~/.brain-agent/server.error.log`, not server.log
- `feedback_server_restart.md` — use `launchctl kickstart -k gui/$UID/com.brain-agent.server`
- `feedback_server_restart_lag.md` — needs >6s before HTTP listener binds

---

## How to verify Phase 2 is healthy in a new session

```bash
# 1. Both services up
curl -sf http://127.0.0.1:8420/v1/status -o /dev/null && echo brain ok
curl -sf http://127.0.0.1:8421/health && echo

# 2. Sidecar config
python3 -c "import json; print(json.dumps(json.load(open('config.json'))['sidecar'], indent=2))"

# 3. Smoke chat through sidecar (~30s):
TOKEN=$(curl -sf -X POST http://127.0.0.1:8420/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
SID=$(curl -sf -X POST http://127.0.0.1:8420/v1/sessions \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"model":"CLIProxyAPI/mistral-medium-3.5","agent":"main","project":"KG-Real-Policies"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")
curl -N -X POST http://127.0.0.1:8420/v1/chat \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d "{\"session_id\":\"$SID\",\"message\":\"Wie ist der Umgang mit Multilogin-Berechtigungen geregelt?\"}" \
  --max-time 120 -s | grep -E "^event: (tool_call|done|error)" | head

# 4. Confirm the assistant message was persisted
sqlite3 agents/main/chats.db \
  "SELECT length(content) FROM messages WHERE session_id='$SID' AND role='assistant'"

# 5. Find the latest proxy summary line
grep "\[sidecar-proxy\]" ~/.brain-agent/server.error.log | tail -1
```

Expected: a few tool_call events, then `done`, then a multi-thousand-char assistant message, then a one-line summary like `[sidecar-proxy] turn=XXXX model=CLIProxyAPI/mistral- reply=2226c rounds=3 tools=4 error=None cancelled=False`.

---

End of handover. Next session: re-read `SDK_MIGRATION_PLAN.md`, then this file, then start Phase 3 work in `brain.py:_execute_scheduled`.
