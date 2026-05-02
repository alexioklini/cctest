# Leaked Claude Code Source Analysis (Apr 5, 2026)

## 1. Summary

The leaked TypeScript source reveals a sophisticated engine architecture with strong parallels to Brain Agent but several areas where Claude Code has gone further:

- **QueryEngine class** (46.6KB): Comprehensive conversation lifecycle management with message filtering, partial response recovery, and budget tracking. Brain's `send_message()` handles similar concerns but lacks explicit QueryEngine abstraction.

- **Streaming tool execution** with per-tool concurrency classification: Claude Code routes concurrent-safe tools (read_file, exa_search, web_fetch, etc.) through parallel ThreadPoolExecutor; Brain does the same in `_execute_tools_batch()` but the leaked source shows more explicit coordination via `StreamingToolExecutor` class.

- **Memory directory system** (memdir/): Files-based auto-memory with `MEMORY.md` entrypoint, memory type classification (facts, decisions, preferences), and truncation guardrails. Brain uses MemPalace directly; Claude Code has a dual-path: auto-memory (filesystem) + MemPalace query for conversation augmentation.

- **Stop hooks + budget continuation** (stopHooks.ts, tokenBudget.ts): Multi-phase lifecycle hooks (extract memories, prompt suggestions, job classification, task completion) + intelligent continuation logic that stops on diminishing returns. Brain has middleware compaction; Claude Code extends this with explicit token-budget auto-continue, stop-hook orchestration, and dream/memory consolidation.

- **Coordinator mode** (coordinatorMode.ts): Multi-agent orchestration where one agent (coordinator) spawns and manages worker agents via the `agent_tool`. Workers have separate tool constraints (e.g., `ASYNC_AGENT_ALLOWED_TOOLS`), scraped context, and dedicated memory paths. Brain has agent delegation but not this systematic orchestration.

---

## 2. Concepts We Already Have

| Their concept | Our equivalent | Notes |
|---|---|---|
| `QueryEngine` lifecycle | `send_message()` + `_handle_openai_response()` in claude_cli.py:9–250+ | Monolithic function; not extracted into class |
| Tool streaming execution | `StreamingToolExecutor` equivalent in `_execute_tools_batch()` | claude_cli.py:16000+; runs concurrently-safe tools in parallel via ThreadPoolExecutor |
| Message compaction | `_compact_conversation()` + middleware pipeline | claude_cli.py:13374–13458 + 15809+ ; three-level escalation (summaries, condensation, truncation) |
| Token budget tracking | `getCurrentTurnTokenBudget()` + `checkTokenBudget()` bootstrap state | claude_cli.py:13057–13090; estimated token usage per turn |
| MemPalace integration | `mempalace_query` tool + daemon sync (`_mempalace_chat_sync_loop`, `_mempalace_miner_loop`) | server.py:8826–8953; direct in-process library, no MCP wrapper |
| Structured output hooks | `registerStructuredOutputEnforcement` in utils/hooks | query.ts:60; gated feature (STRUCTURED_OUTPUT) |
| Memory classification | Auto-memory file writing (facts, decisions, preferences) | claude_cli.py not explicitly gated; MemPalace classifier gate in v7.7.0 |
| Multi-agent delegation | `delegate_task()`, agent teams (hierarchical) | claude_cli.py:3500+, agent.json team structure; delegation scoping in `_run_delegate()` |
| Worker subagent routing | Heavy tool wrapper (search, fetch, python_exec) → artifact + summary | execution.py:50–200; raw output to artifact, LLM summary to context |
| Deferred tools | `tool_groups` with `deferred` flag per agent | agent.json token_config; MemPalace query excludes deferred tools from system prompt |
| Context file intelligence | Code graph building (Tree-sitter AST, 14 languages) + `code_graph_*` tools | claude_cli.py:5500+; SQLite storage, incremental builds, impact analysis |
| Model display format | `display_name` field in models config, shown as "name (provider)" everywhere | server.py:3000+, web UI selector/dropdown/status bar |
| MCP tool registration | Dynamic tool list from connected MCP servers + per-agent MCP filtering | claude_cli.py:2500+ (MCPManager); agent.json `mcp_tool_filter`/`mcp_tool_exclude` |
| Permission system | `CanUseToolFn` hook with tool-specific rules + interactive prompts | hooks/useCanUseTool.ts (React/REPL); Brain has hooks/toolPermission/ equivalent |
| Session memory sync | Chat sync classifier (fact/decision/reference/generic) + drawer filing | server.py:8953+, mempalace_chat_sync_loop; cursor table tracks last_message_id |
| Auto-summary generation | Generate next-prompt suggestions via small LLM call | server.py `/v1/sessions/<id>/next-prompt`; Brain has same in web UI (NextPrompt module) |
| Extended thinking support | `thinking` blocks preserved in conversation (Anthropic native) | claude_cli.py:12000+; model-aware budget levels (low/med/high) |
| Project-scoped agents | Projects with instructions + file upload ingestion | server.py:4000+, ProjectManager CRUD, IngestManager multipart parsing |
| Artifact versioning | Conversation-scoped artifact folders with version history (SQLite blob) | claude_cli.py:5200+, artifact_versions table; SSE `artifact_updated` event |
| Session cost tracking | `CostTracker` logs every LLM call; status bar shows `$X.XX` + warnings at 70%/90% | server.py:9100+, costs.db; per-session cost soft limits configurable |
| Rate limiting | Per-agent limits (requests/min, tokens/hr, cost/day) from agent.json | claude_cli.py:4100+ RateLimiter; checked before each request |

---

## 3. Concepts They Do Better Than Us

### A. QueryEngine as First-Class Abstraction

**Their approach** (QueryEngine.ts:184–400):
- Class-based lifecycle encapsulation: constructor→submitMessage() → state machine → generators
- Explicit config injection (tools, commands, MCP clients, agents, canUseTool, getAppState/setAppState)
- Mutable message store + abort controller per engine
- Partial response recovery via `_rollback_messages()` on cancel/error
- Snip-boundary replay for long headless sessions (feature-gated history truncation)
- SDK-only truncation at `snipReplay` boundary (preserves REPL UI scrollback)

**Our approach** (claude_cli.py:9–250):
- `send_message()` + `_handle_openai_response()` as monolithic stateless functions
- Per-call state passed inline; no persistent engine object
- Partial response saved via `_rollback_messages()` but in a function, not method

**What we'd change:**
```
Extract QueryEngine class from claude_cli.py, move:
- send_message/send_message_with_fallback → submitMessage(message: str) → AsyncGenerator
- Tool-round loop logic → private method
- Config struct (tools, model, budget, agent) → constructor(config)
- Message store + abort controller → instance fields
- Snip replay support → depends on our compaction strategy (we use ContextManager DAG instead)

Impact: Testability (inject fake tools/models), multi-session reuse (one engine per chat), explicit lifecycle.
```

**Rank: HIGH** — Better testability, simpler to reason about state flow, enables concurrent session isolation.

---

### B. Stop Hooks + Multi-Phase Lifecycle

**Their approach** (stopHooks.ts:65–150):
- Sequential post-response hook pipeline: extract memories → prompt suggestions → job classification → task completion
- `handleStopHooks()` as async generator yielding system messages, attachment messages, hook results
- Prevents continuation on blocking errors (`preventContinuation` flag)
- Hooks run on a perfect fork of conversation (via `runForkedAgent` + shared prompt cache)
- Classifier gate on memory extraction (classifies into fact/preference/decision/reference/generic)
- Per-session memory-mode storage (off/on/auto) persisted across session reopens

**Our approach** (server.py:_handle_openai_response middleware):
- `_middleware_compaction`, `_middleware_microcompact` run in-band during tool rounds
- `_generate_chat_summary` spawned async thread after response; sidebar polls for completion
- No explicit post-response hook registry; memory saving is classifier-gated but not explicitly sequenced

**What we'd change:**
```
1. Move post-response hooks to explicit phases:
   - After assistant message completes + model has no tool calls
   - Run hooks in order: pre-compact → compact → extract-memories → prompt-suggestions → post-compact
   - Hook result messages (MemorySavedMessage, PromptSuggestionMessage) buffered, yielded to client
   - Prevent continuation if hook raises BlockingError

2. Per-session memory-mode persistence:
   - Chat memory toggle button saves state to session.save_to_memory (tri-state: off/on/auto)
   - On session reopen, restore toggle from DB instead of using default classifier mode
   - Currently toggle is lost on session reload (CLAUDE.md line 660 notes this was fixed in v7.7.0)

3. ForkedAgent for hook execution:
   - Perfect fork of conv via existing forked agent plumbing (execution.py)
   - Hooks share prompt cache with main conv (cheaper LLM calls)
```

**Rank: HIGH** — Cleaner lifecycle, testability, per-session memory mode (users want to control whether to save).

---

### C. Intelligent Token Budget Continuation

**Their approach** (tokenBudget.ts:1–94):
- Track: continuationCount, lastDeltaTokens, lastGlobalTurnTokens, startedAt
- Decision logic:
  - Continue if turnTokens < budget * 0.9 (90% threshold)
  - Diminishing returns if: continuationCount >= 3 AND delta < 500 tokens in last 2 checks
  - Stop if diminishing or continuationCount > 0 (prevents runaway)
- Nudge message injected per continuation (shows pct, turn tokens, budget)
- Fires up to 500k token budget auto-continue on non-agent, non-delegated queries
- Completion event logged on stop with: continuationCount, pct, budget, diminishing flag, durationMs

**Our approach** (claude_cli.py:13057–13090):
- Simple check: if estimated tokens + margin < max_context, allow next round
- No diminishing returns detection; no auto-continue nudge message
- Global flag at query level; no per-session budget

**What we'd change:**
```
1. Add BudgetTracker type to session state:
   - continuationCount, lastDeltaTokens, lastGlobalTurnTokens, startedAt

2. In send_message after each response:
   decision = checkTokenBudget(tracker, agentId, budget, globalTurnTokens)
   if decision.action == 'continue':
     Inject user message with nudgeMessage
     Increment continuationCount
   else:
     Log analytics event with decision.completionEvent

3. Diminishing returns heuristic:
   - After 3 continuations, check delta between last two turns
   - If < 500 tokens added, stop (model is repeating)
   - Prevents infinite loops on models that plateau

4. Optional: expose to user via config (disable auto-continue for agents).
```

**Rank: MEDIUM** — Better UX (nudge messages), prevents runaway, analytics-friendly. But requires careful tuning of thresholds.

---

### D. StreamingToolExecutor with Concurrency Classification

**Their approach** (StreamingToolExecutor.ts:40–150):
- Class tracks tools via TrackedTool[] with: id, block, assistantMessage, status, isConcurrencySafe, promise, results, pendingProgress
- `addTool()` parses input against schema, calls `tool.isConcurrencySafe(parsed)` to decide parallelism
- Status machine: queued → executing → completed; progress messages yielded immediately
- Sibling abort controller: when Bash errors, aborts other subprocesses without canceling the main turn
- Result buffering + ordered emission (tools execute out-of-order, results emitted in submission order)

**Our approach** (claude_cli.py:16000+):
- `_execute_tools_batch()` partitions into concurrent-safe (read_file, exa_search, web_fetch) vs sequential
- Runs safe tools in ThreadPoolExecutor; unsafe sequentially
- No progress message support; results returned once all complete

**What we'd change:**
```
1. Add tool.isConcurrencySafe(input) method to Tool base class:
   - Query tool schema + input constraints
   - Example: read_file(path) is safe unless path is a shell glob

2. Streaming progress support:
   - Yield progress messages (e.g., "file read 50%") as we receive them
   - Requires tool implementations to emit progress callbacks
   - Stream to client via SSE (already have ToolProgressData types)

3. Sibling abort controller:
   - Bash tool errors trigger abort on other bash processes
   - Doesn't abort main turn (continue with other tool results)
   - Requires process tracking in BashTool

4. Result ordering:
   - Buffer results, emit in submission order (not completion order)
   - Simplifies model reasoning about tool sequences
```

**Rank: MEDIUM** — Mostly already have concurrency; streaming progress is the valuable gap.

---

### E. Memory Directory System (Dual-Path Auto-Memory)

**Their approach** (memdir/memdir.ts + memoryTypes.ts):
- Filesystem-based auto-memory: `~/.claude/projects/<slug>/memory/` with MEMORY.md entrypoint + topic files
- Entrypoint: 200-line, 25KB limit; warning if truncated (guides index density)
- Memory types classified: WHEN_TO_ACCESS, TRUSTING_RECALL, TYPES_SECTION_INDIVIDUAL (facts/decisions/preferences), WHAT_NOT_TO_SAVE
- Auto-memory enabled gate + per-project override
- Truncation: line-truncate first, then byte-truncate at last newline (guards against long-line overflow)
- `ensureMemoryDirExists()` runs once per session (idempotent mkdir)

**Our approach** (server.py + CLAUDE.md):
- MemPalace drawers only; no filesystem indexing layer
- Chat sync classifier (fact/preference/decision/reference) gates what's filed
- No per-project memory organization
- Memory is in-process (not files); users can't directly edit

**What we'd change:**
```
1. Dual-path memory: both MemPalace (for search/retrieval) + files (for user control)
   - Add ~/.brain-agent/projects/<project_slug>/memory/ directory
   - Create MEMORY.md as entrypoint (index of topic files)
   - Topic files: facts.md, decisions.md, preferences.md, etc.

2. Auto-write after query completes (like extract memories):
   - Extract facts/decisions/preferences from session
   - Format as markdown list items (one per line)
   - Append to topic files in memory/

3. System prompt injection:
   - Load MEMORY.md content + sample of each topic file
   - Inject into system prompt (~500 tokens if well-curated)
   - Users can edit files directly; changes visible next turn

4. Truncation guardrails:
   - Line cap (200 lines) + byte cap (25KB)
   - If either exceeded, append warning explaining overflow
   - Guide users to move details to separate files

5. Team memory paths (feature-gated TEAMMEM):
   - ~team_name/memory/ for shared team memories
   - Used by coordinator mode for cross-agent instruction sharing
```

**Rank: MEDIUM-HIGH** — Filesystem memories let users directly control/share knowledge without UI; complements MemPalace.

---

### F. Coordinator Mode (Systematic Multi-Agent Orchestration)

**Their approach** (coordinatorMode.ts:36–150):
- Feature gate: COORDINATOR_MODE + CLAUDE_CODE_COORDINATOR_MODE env var
- Coordinator is the main session; spawns workers via `agent_tool`
- Worker constraints:
  - SIMPLE mode: only bash, read, edit tools + MCP
  - NORMAL mode: all ASYNC_AGENT_ALLOWED_TOOLS except internal (team_create, send_message, synthetic_output)
  - Scratchpad directory for durable cross-worker knowledge (feature-gated tengu_scratch)
- System prompt distinction: coordinator describes their role (supervise, delegate, synthesize); workers get default
- Workers have separate task records + side-chain transcripts
- `getCoordinatorUserContext()` injects tool availability description into user context

**Our approach** (claude_cli.py agent delegation):
- `delegate_task()` spawns subagent with limited tool set
- No coordinator/worker explicit distinction
- Team structure in agent.json but no systematic multi-worker scheduling
- Each delegate call is independent (no shared scratchpad)

**What we'd change:**
```
1. Add "coordinator mode" runtime flag:
   - BRAIN_COORDINATOR_MODE env var
   - Affects system prompt (describe coordinator role)
   - Gates scratchpad directory access

2. Worker tool constraints:
   - Define WORKER_ALLOWED_TOOLS set (exclude internal tools like team_create)
   - SIMPLE mode: only bash, read, edit, + MCP
   - Agent checks mode before yielding tools to model

3. Scratchpad directory:
   - ~/.brain-agent/projects/<project>/scratchpad/ (feature-gated)
   - Workers read/write without permission prompts
   - Coordinator can read/edit files between workers
   - Useful for: shared context, decision logs, code templates

4. Worker instance lifecycle:
   - Each worker gets agentId (uuid4)
   - Side-chain transcript saved to agents/<agent_name>/transcripts/workers/<session_id>/<worker_id>.txt
   - Separate flow log per worker (like current execution.py flow)
   - Parent can query worker status, pause, resume, abort

5. Communication:
   - Workers notify coordinator when complete (already have task-notification XML)
   - Coordinator can check worker status, send follow-up messages via send_message_tool
```

**Rank: HIGH** — Enables reliable multi-agent workflows; team heads can spawn parallel research/implementation workers.

---

## 4. Concepts They Have That We Don't — Adoption Candidates

### A. Snip-Boundary History Compression

**What it is** (query.ts:169–173, services/compact/snipCompact.ts):
- Feature-gated (HISTORY_SNIP); when enabled, API history is truncated at "snip boundaries" (compacted sections)
- Snip replay: at session load time, fetch the compacted summary for each snipped section; replay full summary into API calls
- SDK-only: REPL keeps full conversation in UI for scrollback; API sees snipped version
- Benefit: Bounds memory usage in 100K+ message headless sessions without losing conversation context

**Why we should adopt:**
- Brain chats can grow to 10K+ messages; context manager DAG helps but snipping would be cheaper
- REPL users expect full scrollback; API calls only need the gist
- Headless/worker agents would benefit (execute for hours without context explosion)

**Implementation sketch:**
```
1. Add snip boundary detection:
   - After compaction, mark message as compacted (add flag to messages table)
   - On next session load, insert CompactBoundaryMessage in conversation

2. For SDK callers (headless):
   - Remove compacted messages from send_message() payload
   - Replace with CompactSummary (synthetic marker)
   - Model knows: "here was a long conversation, summarized as X"

3. For REPL:
   - Keep full messages in memory for UI rendering (local scrollback)
   - But send snipped version to API (save tokens)

4. Implementation:
   - Add is_snipped flag to messages table
   - In _augmented_messages(), filter snipped sections before API call
   - On load, reconstruct summaries for each snipped boundary
```

**Rank: MEDIUM** — Helps with long headless sessions; adds complexity (snip/replay logic).

---

### B. Prompt Suggestion Forked Agent (with Cache Reuse)

**What it is** (services/PromptSuggestion/promptSuggestion.ts):
- After each assistant response, spawn a forked subagent (shares main conv's prompt cache)
- Forked agent calls same model with instruction to predict next user message (max 15 words)
- Result shown as ghost text in composer; user tabs/arrows to accept
- Cache reuse: if fork uses same model, hits the same prompt cache (near-free)
- Optional model override: can use cheaper model for suggestions (breaks cache reuse)

**Our approach:**
- Web UI fetches `/v1/sessions/<id>/next-prompt` endpoint synchronously
- No forked agent; direct LLM call (smaller payload, fewer tokens)
- Config: default empty model (reuses session model)

**Why adopt:**
- Forked agent gives us two things: cache reuse + background execution (don't block UI)
- Could suggest next message while user reads current response
- Minimal cost if using same model (already have cache)

**Implementation sketch:**
```
1. In send_message() after response complete:
   If next_prompt_suggestions.enabled:
     Spawn forked agent with:
     - Same messages (conv up to this point)
     - System prompt: "Predict the next user message in max N words"
     - max_tokens: 30
     - model: config override or same as session
     - Tools: false

2. Forked agent shares prompt cache with main conv:
     - If same model: prompt cache hit (tokens already counted in main call)
     - If different model: new cache entry (breaks reuse, but faster model)

3. Result: PromptSuggestionMessage in stream
   - Client renders as ghost text
   - On Tab/Arrow: fills composer input
   - On Escape/Type: dismissed

4. Timing:
   - Forked agent starts after main response yields to client
   - Doesn't block (async)
   - If completes before user responds, show immediately
   - If still pending when user submits, discard
```

**Rank: LOW-MEDIUM** — Nice-to-have (improves composer UX); not essential. Requires forked agent infrastructure.

---

### C. Team Memory + Cross-Agent Instruction Sharing

**What it is** (memdir/teamMemPaths.ts, memdir/teamMemPrompts.ts):
- Feature-gated (TEAMMEM); when teams are active, auto-memory paths become shared
- Team memories: `~/.claude/projects/<slug>/memory/teams/<team_name>/`
- Team instructions: separate memory scope for team head + all members
- Scoped access: workers in Team A can't see Team B memories (isolation)
- Used by coordinator mode to give workers context about their team's charter

**Why we should adopt:**
- Current setup: agents only see their own memories + shared brain_code
- Teams need shared knowledge (requirements, decisions, previous findings)
- Coordinator workers benefit: receives team memory + scratchpad for coordination

**Implementation sketch:**
```
1. Add team path resolution:
   - If agent is in a team: memory path = ~/.brain-agent/projects/<slug>/memory/teams/<team_name>/
   - Else: memory path = ~/.brain-agent/projects/<slug>/memory/agents/<agent_name>/

2. Team memory injection:
   - System prompt loads both agent memory + team memory
   - Team memory injected with label (so model knows it's shared context)

3. Coordinator access:
   - Coordinator loads team memory for each worker it spawns
   - Injects as context ("Team context:", "Scratchpad:") before delegating
   - Workers can read/edit team memory via file ops

4. Isolation:
   - Team A memory NOT visible to Team B or standalone agents
   - Only team members + coordinator can read
```

**Rank: MEDIUM** — Helps teams coordinate; depends on having team structure.

---

### D. Job Classification (Routes to Built-In Agents)

**What it is** (services/jobs/classifier.ts, feature gated TEMPLATES):
- Post-response hook classifies user query into job type (research, implement, review, etc.)
- Routes to built-in agent (research agent, implementation agent, verification agent)
- If match detected: spawn forked subagent to handle the job
- Example: "write a function that does X" → routes to implementation agent

**Why we should adopt:**
- Improves task-specific agent selection without user interaction
- Example: user asks "verify this PR" → automatically spawns verification agent
- Works well with coordinator mode (main agent routes to specialized workers)

**Implementation sketch:**
```
1. Build a job classifier prompt:
   - Classify user's request into categories: {research, implement, review, fix, analyze, none}
   - Return structured output: {job_type, confidence, reasoning}

2. Post-response hook (after model finishes):
   - Extract user's latest message
   - Call classifier (small LLM call, max_tokens=100)
   - If job_type != 'none' AND confidence > 0.7:
     Spawn specialized subagent
     Examples:
     - research: "gather information and summarize findings"
     - implement: "write production code with tests"
     - review: "review code for quality, security, style"

3. Built-in agents:
   - research-agent: system prompt "you are a researcher"
   - implement-agent: system prompt "you are an implementer"
   - review-agent: system prompt "you are a code reviewer"
   - Each has specialized tools (research-agent has more web search; implement-agent has more code)

4. Execution:
   - Forked subagent runs with user's request + conversation context
   - Results appear as task completion message
   - Coordinator can delegate to these agents explicitly or let classifier route
```

**Rank: LOW** — Nice automation; but requires careful classifier tuning. Can always let coordinator manually route instead.

---

### E. MCP Tool Collapse (Classify Tool Results for Summarization)

**What it is** (tools/MCPTool/classifyForCollapse.ts):
- Feature: after MCP tool execution, classify result into categories (output, config, error, summary, drill-in)
- Model-visible hint: if result is "drill-in" type, model knows more detail is available via follow-up
- Example: `list_files` returns file list; classified as "drill-in" (model can call read_file for details)
- Saves tokens by not including full file content upfront

**Why we should adopt:**
- MCP tools (especially file-system facing ones) can return large results
- Classifying lets model know it can drill deeper without balloning context
- Particularly useful for tools that return structured data (JSON, lists)

**Implementation sketch:**
```
1. After MCP tool execution:
   - Classify result into: full_output | drill_in | error | summary | config
   - Append hint to result: "Type: drill-in. Use [related_tool] for details."
   - Examples:
     - list_files → "drill-in" (model can read_file)
     - read_file → "full_output" (complete content)
     - git_log → "drill-in" (model can git_show)

2. Tool result processor:
   - For MCP tools, call small classifier before appending to context
   - Helps model avoid redundant calls
   - Saves ~500 tokens per large MCP result

3. Implementation:
   - Add result_type field to tool result message
   - classifier prompt: "Is this result complete, or should model drill deeper? Type: {full_output|drill_in|error|summary|config}"
```

**Rank: LOW** — Micro-optimization; helps with large MCP results but not critical.

---

## 5. Concepts They Have That Don't Make Sense for Brain

- **React/Ink TUI components** (src/components/, src/ink/) — We're web-first; REPL via tui.py; no React needed
- **Keybindings subsystem** (src/keybindings/) — Relevant to REPL/editor context; Brain doesn't have REPL
- **Voice interface** (src/voice/) — We have Telegram; Voice is orthogonal to our transport stack
- **Native TS modules** (src/native-ts/) — Python doesn't need native bindings; Brain is pure Python + web stack
- **Vim mode** (src/vim/) — Editor feature; not applicable to our HTTP API model
- **Bootstrap/entrypoint plumbing** (src/bootstrap/, src/entrypoints/) — TypeScript build/runtime; Python equiv is main.py/server.py
- **Structured output enforcement** (utils/hooks/hookHelpers.ts) — We use Pydantic; Anthropic SDK handles JSON schema

---

## 6. Open Questions / Things to Investigate Further

1. **Snip boundary replay cost**: How much do they save by truncating history at compaction boundaries? Is it worth the replay machinery (decompress summaries on load)?

2. **ForkedAgent prompt cache reuse**: We have forked agents (execution.py); do they actually share cache with parent? Need to verify the Anthropic SDK supports cache inheritance across API calls.

3. **Coordinator mode adoption risk**: If enabled, does it change system prompts enough to break existing agents' behavior? Need staging/canary rollout.

4. **Team memory file safety**: If workers edit team memory files concurrently, risk of corruption. Need file locking or explicit orchestration.

5. **Stop hook ordering**: They run extract memories → prompt suggestions → job classification → task completion. What's the right order for us? Could some run in parallel?

6. **Token budget thresholds**: The 90% completion + 500-token diminishing threshold are empirically tuned. Do they work for our model mix (mostly Sonnet/Haiku)? Need A/B test data.

7. **Memory truncation warning UX**: If MEMORY.md gets truncated, they append a warning. How do users see this warning? Does it show in system prompt?

---

## Comparative Architecture

### Message Flow

**Claude Code** (query.ts → query.ts main loop):
```
Input: UserMessage
  ↓
[Optional: Snip boundary replay for history]
  ↓
[Permission check via useCanUseTool]
  ↓
[System prompt + user context + history] → queryModelWithStreaming()
  ↓
[Tool streaming: emit ToolProgressData as tools arrive]
  ↓
StreamingToolExecutor:
  - Concurrent-safe tools: parallel (ThreadPoolExecutor)
  - Non-safe: sequential
  - Results buffered, emitted in order
  ↓
[Auto-compact on token threshold]
  ↓
[Stop hooks: extract memories, prompt suggestions, job classification, task completion]
  ↓
Output: AssistantMessage + ToolUseSummaryMessages
```

**Brain Agent** (claude_cli.py):
```
Input: UserMessage
  ↓
[Permission check via tool_permitted()]
  ↓
[System prompt + memory (MemPalace)] → send_message()
  ↓
[Tool execution: batch by concurrency safety]
  ↓
_execute_tools_batch():
  - Concurrent-safe: parallel (ThreadPoolExecutor)
  - Non-safe: sequential
  - Results returned once all complete
  ↓
[Middleware compaction on token threshold]
  ↓
[Async: generate chat summary, MemPalace sync, scheduled tasks]
  ↓
Output: AssistantMessage
```

**Differences**:
- Claude Code has explicit QueryEngine lifecycle + generator protocol (yield per event)
- Brain is event-driven (SSE from server.py; callbacks in send_message)
- Claude Code: stop hooks run post-response; Brain: middleware runs in-band during tool loop
- Claude Code: prompt suggestions via forked agent; Brain: direct LLM call

---

## Files Referenced

### Leaked Source Key Files
- `src/QueryEngine.ts`: 46.6KB, core lifecycle
- `src/query.ts`: 68.7KB, agentic loop main
- `src/query/stopHooks.ts`: stop hook orchestration
- `src/query/tokenBudget.ts`: auto-continue logic
- `src/services/tools/StreamingToolExecutor.ts`: concurrent tool execution
- `src/memdir/memdir.ts`: filesystem memory management
- `src/coordinator/coordinatorMode.ts`: multi-agent orchestration
- `src/tools/AgentTool/runAgent.ts`: subagent lifecycle
- `src/services/compact/compact.ts`: compaction logic
- `src/services/autoDream/autoDream.ts`: memory consolidation
- `src/services/extractMemories/extractMemories.ts`: memory extraction

### Brain Equivalent Files
- `claude_cli.py`: ~18K lines; send_message, tool execution, compaction, memory tools
- `server.py`: ~425K lines; HTTP API, async task management, MemPalace daemon, cost tracking
- `execution.py`: ~30K lines; worker subagent lifecycle
- `tui.py`: ~110K lines; REPL (not comparable to React TUI)
- `web/index.html`: web UI (not comparable to Ink/React components)

