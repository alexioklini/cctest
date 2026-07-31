---
name: Agent SDK integration status — full gap analysis
description: Complete analysis of what works, what's broken, and what still uses the old LLM loop after SDK migration
type: project
---

## Status: v4.5.0 — SDK is default for interactive chat

### Architecture
- `sdk_sidecar.py` (port 8421): lean process, no claude_cli import, real-time SSE streaming
- `sdk_backend.py`: provider env builder + sidecar SSE proxy
- Server builds system prompt + provider env via claude_cli, hands off to sidecar
- CRITICAL: sidecar must NEVER import claude_cli (breaks anyio subprocess streaming)

---

## GAP 1: Custom tools not available to SDK

Brain Agent has 24 custom tools the SDK can't access. Need HTTP MCP server.

**Tools to expose:** memory_store, memory_recall, memory_delete, memory_shared, gmail_inbox, gmail_read, gmail_search, gmail_send, gmail_reply, read_document, write_document, edit_document, delegate_task, task_status, task_cancel, use_skill, code_graph_build, code_graph_query, code_graph_impact, code_graph_enhance, schedule_list, schedule_history, list_nodes, exa_search

**Approach:** `/v1/tools/call` endpoint on main server, registered as HTTP MCP server in sidecar.

---

## GAP 2: Features broken/bypassed by SDK

### CRITICAL
| Feature | Issue |
|---------|-------|
| `_after_file_write()` | QMD reindex, entity extraction, KG updates never fire |
| Chat summary generation | `_generate_chat_summary` not triggered in SDK path |
| Transcript indexing | SDK chats not indexed for QMD search |

### HIGH
| Feature | Issue |
|---------|-------|
| Rate limiting (pre-check) | No throttle before SDK call |
| Model fallback | No retry on failure |
| Tracing spans | SDK turns invisible in trace dashboard |
| Message persistence | Only final text saved, no tool_use/tool_result |
| Inference params | Temperature/top_p not passed |
| Tool output streaming | Users can't see tool execution progress |

### MEDIUM
| Feature | Issue |
|---------|-------|
| Plan mode | Restrictions not enforced |
| Workflow tool restrictions | Not enforced |
| Tool dedup | No infinite loop detection |
| Audit logging | No tool call audit trail |
| Pre/post hooks | Custom hook scripts don't run |
| Cost per tool round | Only aggregate, not per-round |

---

## GAP 3: Background processes still on old LLM loop

These all use `_run_delegate()` or `send_message()` — NOT the Agent SDK:

### Background tasks (claude_cli.py)
1. **_auto_memory_extract** (line 5645) — Haiku call after each response
2. **trigger_relationship_discovery** (line 5877) — Haiku classification of memory relationships
3. **_autodream_dedup** (line 6036) — LLM merge of duplicate memories
4. **_autodream_conflicts** (line 6192) — LLM contradiction detection
5. **_autodream_skill_candidates** (line 6257) — LLM procedural memory detection
6. **promote_memory_to_skill** (line 6376) — LLM skill generation from memory
7. **_execute_scheduled** (line 7927) — Scheduled task execution with tools
8. **generate_summaries** (line 9859) — Code graph node summaries
9. **classify_task_purpose** (line 11735) — Auto-model selection classifier

### Context management (claude_cli.py)
10. **summarize_chunk** (line 10591) — Context compaction summaries
11. **condense** (line 10654) — Multi-summary condensation
12. **recall** (line 10927) — Deep context recall
13. **_compact_conversation** (line 11064) — Legacy flat compaction

### Server (server.py)
14. **_generate_chat_summary** (line 5486) — Sidebar chat summaries

### Entry points
15. **TUI** (line 14515) — Terminal UI uses send_message_with_fallback
16. **CLI one-shot** (line 13768) — `python claude_cli.py -m "message"` uses send_message_with_fallback

**Approach:** Add `sdk_backend.query_sync(prompt, model, provider_env)` that sends to sidecar and returns text. Replace each `_run_delegate` call.

---

## GAP 4: Hooks need migration

Our `HookRunner` (pre/post tool scripts) and `_after_file_write()` are bypassed.

**Lost:** QMD reindex on file writes, entity extraction, KG updates, external hook scripts, tool permission checks.

**Approach:** Use SDK's `PreToolUse`/`PostToolUse` hooks via `ClaudeAgentOptions.hooks`, or add filesystem watcher for agent dirs.

---

## Priority order
1. HTTP MCP server for custom tools (unblocks full agent functionality)
2. Chat summary + transcript indexing in SDK path
3. File watcher for _after_file_write (QMD reindex, KG)
4. Rate limiting pre-check
5. Migrate background tasks to SDK
6. Hook migration
7. Model fallback for SDK
8. Tracing spans

---

## Key findings
- Direct OAuth key (`sk-ant-oat01-...`) only gives Sonnet — use CLIProxyAPI for all Claude models
- `import claude_cli` breaks anyio subprocess streaming — root cause is module-level side effects
- Provider config: 3 providers (cliproxyapi, omlx, Minimax)
- SDK's built-in tools replace: read_file, write_file, edit_file, list_directory, search_files, execute_command, web_fetch, git_command, github_command
- SDK handles its own context compaction — our LCM is disabled for SDK sessions
- context_search/detail/recall dropped (would conflict with SDK internals)
- mcp_connect/disconnect/servers dropped (SDK manages MCP at startup)
