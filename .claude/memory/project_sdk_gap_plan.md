---
name: SDK migration gap analysis and implementation plan
description: Complete plan to close all gaps after switching to Agent SDK — 7 phases, prioritized
type: project
related_to: [project_summary, infra_deployment, feedback_sdk_streaming, feedback_direct_execution, feedback_sidecar_no_claude_cli, project_token_fixes, infra_inferencer]
sdk_relationships:
  - depends_on: project_sdk_gap_plan
  - overlaps: feedback_sdk_streaming
  - explains: bug_thinking_sidecar
---

## Phase 1: Core Functionality (agents crippled without this)

### 1.1 — HTTP MCP Server for Custom Tools
- `/v1/tools/call` endpoint: accepts `{name, args, agent_id, session_id}`, dispatches to `TOOL_DISPATCH`
- `/v1/tools/list` endpoint: returns tool schemas for MCP discovery
- Register as HTTP MCP server in sidecar's `mcp_servers` config
- 24 tools: memory (4), gmail (5), documents (3), code graph (4), delegation (3), scheduling (2), nodes (1), skills (1), exa_search (1)
- Effort: Large

### 1.2 — Chat Summary Generation for SDK Path
- `_generate_chat_summary()` not triggered after SDK responses
- Add call in server.py SDK post-response block
- Effort: Small

### 1.3 — Transcript Indexing for SDK Path
- `_index_chat_transcript()` not triggered after SDK responses
- Add call in server.py SDK post-response block
- Effort: Small

## Phase 2: Data Integrity (knowledge graph decays without this)

### 2.1 — File Watcher for _after_file_write
- SDK file writes bypass QMD reindex, entity extraction, KG updates
- Option A: watchdog filesystem watcher on agent memory dirs
- Option B: SDK PostToolUse hook for Write/Edit → call server
- Effort: Medium

### 2.2 — Code Graph Incremental Updates
- Piggyback on 2.1 file watcher for source files
- Effort: Small

## Phase 3: Guardrails & Safety

### 3.1 — Rate Limiting Pre-Check
- Check `_rate_limiter.check(agent_id)` before sidecar call
- Effort: Small

### 3.2 — Model Fallback
- Retry with fallback model/provider on sidecar error
- Effort: Medium

### 3.3 — Plan Mode Enforcement
- Pass `allowed_tools` to SDK restricting to read-only tools
- Effort: Small

### 3.4 — Workflow Tool Restrictions
- Pass `allowed_tools` from workflow config to SDK
- Effort: Small

## Phase 4: Observability

### 4.1 — Tracing Spans
- Re-add request + sdk_query spans around sidecar proxy
- Effort: Small

### 4.2 — Audit Logging
- Log tool_call/tool_result SSE events to audit.db
- Effort: Small

### 4.3 — Cost Per Tool Round
- Accept limitation: SDK reports aggregate only
- No action needed

## Phase 5: Migrate Background Tasks to SDK

### 5.1 — Add query_sync Helper
- `sdk_backend.query_sync(prompt, model, system_prompt)` — sends to sidecar, returns text
- Effort: Medium

### 5.2 — Migrate Simple LLM Calls (no tools, 11 call sites)
Replace `_run_delegate(tools=False)` with `query_sync`:
1. `_auto_memory_extract` (line 5645)
2. `trigger_relationship_discovery` (line 5877)
3. `_autodream_dedup` (line 6036)
4. `_autodream_conflicts` (line 6192)
5. `_autodream_skill_candidates` (line 6257)
6. `promote_memory_to_skill` (line 6376)
7. `classify_task_purpose` (line 11735)
8. `_generate_chat_summary` (server.py line 5486)
9. `generate_summaries` / code graph (line 9859)
10. `summarize_chunk` (line 10591)
11. `condense` (line 10654)
- Effort: Medium

### 5.3 — Migrate Tool-Using Tasks (needs Phase 1.1 MCP server)
Replace `_run_delegate(tools=True)` with `query_sync`:
1. `_execute_scheduled` (line 7927) — scheduled tasks
2. `recall` (line 10927) — deep context recall
- Effort: Medium

### 5.4 — Migrate TUI and CLI
- TUI (tui.py line 14515) → route through SDK sidecar with cancel support and sdk_session_id resume
- CLI one-shot (line 13768) → same
- Effort: Large

## Phase 6: Hook Migration

### 6.1 — SDK Hook Integration
- Map HookRunner scripts to SDK PreToolUse/PostToolUse callbacks
- Hooks call server via HTTP (can't import claude_cli in sidecar)
- Effort: Large

## Phase 7: Nice to Have

- 7.1 Inference params (temperature/top_p) → pass to SDK
- 7.2 Tool output streaming → surface SDK tool execution
- 7.3 Richer message persistence → save tool_use/tool_result from events
- 7.4 Tool dedup verification → confirm SDK has its own

## Completed (v4.5.0 commit 42f5c75)
- Phase 1.1: HTTP MCP server — 24 tools via /v1/tools/call + /v1/tools/list
- Phase 1.2+1.3: Chat summary + transcript indexing in SDK post-response
- Phase 2: File change watcher (10s poll, triggers _after_file_write)
- Phase 3: Rate limiting pre-check, model fallback, plan mode allowed_tools, workflow restrictions
- Phase 4: Trace spans + audit logging for SDK path
- Phase 5.1-5.3: _run_delegate auto-routes through SDK sidecar, query_sync helper
- Phase 6: /v1/hooks/run endpoint for hook callbacks
- Phase 7: Accepted SDK limitations (temperature, tool output streaming)

## Completed (2026-03-28)
- D1: TUI interactive routes through SDK sidecar with cancel support and sdk_session_id resume
- D2: CLI one-shot routes through SDK sidecar with tool defs, falls back to direct API
- D3: SDK PreToolUse/PostToolUse hooks wired to server /v1/hooks/run (sidecar _build_sdk_hooks, server passes hooks_enabled)
- D4: query_sync extended with tool_defs/server_url/agent_id/session_id; _run_delegate routes tools=True through SDK

## All SDK migration gaps now closed.
