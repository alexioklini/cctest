# Brain Agent Refactoring Plan

## Guiding Principle

Every feature should be explainable end-to-end in 2-3 sentences. If it can't, it's a candidate for refactoring. The goal is a simple, centralized codebase where each concept has one home.

---

## What is Brain Agent? (The simple model)

A user sends a message → the server builds a context (who is asking, what agent, what project, what mode) → runs an agentic loop (LLM + tools, N rounds) → streams the result back. That's it. Everything else is configuration of that loop.

The three modes (chat, scheduled, project) are the **same loop** with different context. This is not yet reflected in the code.

---

## Problem Areas

### P1 — Three agentic loops that should be one

**Current state:**
- `send_message()` (brain.py:21840) — interactive chat loop
- `_execute_scheduled()` (brain.py:12687) — calls `_run_delegate()` with hardcoded tools=`"memory_only"`
- Project chat — same as send_message but with `_thread_local.project` flag activating scattered conditionals

**Why it's a problem:** Adding a feature (e.g. a new tool, a new inference param) requires touching 2-3 places. The scheduled path has independent error handling, artifact naming, and context setup.

**Fix:** One `ExecutionContext` dataclass — `{mode, agent_id, session_id, project, user_id, inference_params, tools, ...}` — built upfront and passed into a single `run_agentic_loop(ctx, messages)`. Mode-specific behaviour becomes small hooks (pre/post), not branching throughout the loop.

**Explainability after fix:** "Build an ExecutionContext for the request, pass it to run_agentic_loop. Scheduled tasks, chat, and project chat all use the same loop with different contexts."

---

### P2 — Three system prompt builders that should be one

**Current state:**
- `_build_system_prompt()` (brain.py:21381) — cached, builds base prompt for chat
- Project preamble (`_project_preamble_text`, brain.py:21767) — injected separately at round 0
- Scheduled task system prompt — rebuilt from scratch inside `_execute_scheduled`, not cached, independent string

**Why it's a problem:** The prompt for a scheduled task cannot be explained without understanding three separate code paths. Cache applies only to chat, not scheduled.

**Fix:** `_build_system_prompt(ctx: ExecutionContext)` — one function, accepts the context object, returns the full prompt. Mode-specific blocks (noninteractive directive, project preamble, user profile) are assembled inside it based on `ctx.mode`. Cache key includes mode.

**Explainability after fix:** "`_build_system_prompt(ctx)` builds the system prompt for any mode. It has a base layer (soul, tools) shared by all, and optional blocks gated on ctx fields."

---

### P3 — Thread-local initialization scattered everywhere

**Current state:**
- `send_message()` sets `_thread_local.current_session_id`, `current_agent`, `current_user_id`
- `_execute_scheduled()` sets `current_agent`, `mcp_manager`, `current_user_id`, `project`, `caveman_chat`
- `_run_delegate()` sets another subset
- Each call site has its own try/finally cleanup

**Why it's a problem:** Miss one thread-local in one path and you get a subtle cross-request bleed bug (happened before with user_id). Adding a new thread-local requires touching every entry point.

**Fix:** One `init_thread_context(ctx: ExecutionContext)` + `clear_thread_context()`. Called at the top of every execution entry point. All thread-locals set in one place, cleared in one finally block.

---

### P4 — MemPalace has four direct call sites with no shared layer

**Current state:**
1. `_mempalace_chat_sync_loop()` (server.py:4643) — daemon, mirrors chat turns
2. `_mempalace_miner_loop()` (server.py:4412) — daemon, indexes project files
3. `tool_mempalace_query()` (brain.py:3762) — on-demand tool call during chat
4. Project-sync stale-path purge (server.py:5348) — inline in the sync cycle

Each imports mempalace directly, resolves palace_path independently, has its own error handling.

**Why it's a problem:** Can't explain MemPalace access in 2-3 sentences because there's no single access layer. Adding a new MemPalace operation means choosing which pattern to copy.

**Fix:** `MemPalaceClient` singleton in `server.py` — initialized once at startup with palace_path. Exposes `add_drawer()`, `query()`, `purge_by_prefix()`, `get_collection()`. All four call sites use it. Import and path resolution happen once.

**Explainability after fix:** "All MemPalace access goes through MemPalaceClient. It's initialized at startup and passed to whoever needs it."

---

### P5 — Composer duplicated 3× in HTML

**Current state:**
- Three full HTML composer trees: welcome screen, chat view, project-detail view
- `_composerToggleEls(suffix)` (utils.js:78) — queries all 3 and returns matching buttons
- Called from 7+ places in init.js and panels.js every time a toggle changes
- Model selector: 3× independent `.model-selector-btn` elements

**Why it's a problem:** Adding a new composer option requires adding HTML in 3 places + adding a `_composerToggleEls` call. The abstraction (`_composerToggleEls`) proves the 3 copies know they're duplicates.

**Fix:** One composer HTML component rendered once, positioned via CSS into the right view. State stays in `state.js` (already centralized). Toggle handlers work on one element, not three.

---

### P6 — Chat rendering has no unified event pipeline

**Current state:**
- SSE events hit 5 different render paths:
  - `thinking_delta` → `renderStreamingMessage()`
  - `text_delta` → `renderStreamingMessage()`
  - `tool_result` → `renderMessages()` (full rebuild)
  - `artifact_updated` → `renderArtifactPanel()`
  - `done` → multiple calls: `renderMessages()`, `updateStatusBar()`, sidebar update
- 29+ explicit render call sites across the codebase

**Why it's a problem:** To understand what happens when an SSE event arrives you have to trace 5 different code paths. Adding a new event type requires knowing which render function to call.

**Fix:** One `handleSSEEvent(type, data)` dispatcher in `api.js`. All SSE events go through it. It updates `state` and calls one `render()` function. Each render function is idempotent — called once regardless of what changed.

---

### P7 — Artifact pipeline is split by mode

**Current state:**
- Chat artifacts: updated via `artifact_updated` SSE event → `state.artifacts[sessionId]`
- Scheduled task artifacts: fetched separately via `API.browseArtifacts()` on view mount
- Two different browse UIs (chat artifact panel + scheduled artifacts tab)

**Why it's a problem:** An artifact is an artifact regardless of who created it. The split means the UI can't show "all artifacts from this session" in one place.

**Fix:** Unify under one artifact model. `artifact_updated` SSE fires for both chat and scheduled paths (scheduled tasks already emit SSE via the delegate callback). One `renderArtifactPanel()` handles both. The browse view filters by `source` tag if needed.

---

### P8 — Reference extraction is ad-hoc per tool

**Current state:**
- `extractReferencesFromToolResult()` (panels.js:2581) — hardcoded list of tool names: `mempalace_query`, `mempalace_kg_query`, `mempalace_kg_search`, `mempalace_kg_neighbors`, `exa_search`, `web_fetch`
- `.brain-extracted` path resolution duplicated in panels.js and handlers/admin.py
- Citation extraction from assistant text (`[Quelle: ...]`) is separate, in chat.js

**Why it's a problem:** Adding a new tool that returns references requires editing the extractor. The path resolution logic exists in two places.

**Fix:** Server-side: tool results that contain references emit a `references` SSE event alongside `tool_result`. Client-side: one handler for `references` events, no per-tool parsing. Path resolution lives only on the server.

---

## Phased Plan

### Phase 1 — Foundation (prerequisite for everything else)
**Goal:** One execution context object. No behaviour change.

1. Define `ExecutionContext` dataclass in brain.py: `{mode, agent_id, session_id, project_id, user_id, inference_params, tool_groups, caveman_chat, caveman_system, thinking_level}`
2. Implement `init_thread_context(ctx)` + `clear_thread_context()` — all thread-local sets move here
3. Wire into `send_message()`, `_run_delegate()`, `_execute_scheduled()` — each builds an `ExecutionContext` upfront and calls `init_thread_context(ctx)`
4. Verify: no behaviour change, all three modes still work

---

### Phase 2 — Unified system prompt builder
**Goal:** One `_build_system_prompt(ctx)`. No behaviour change.

1. Merge the scheduled-task system prompt string into `_build_system_prompt()`
2. Move project preamble injection into `_build_system_prompt()` (gated on `ctx.project_id`)
3. Cache key includes `ctx.mode` and `ctx.project_id`
4. Remove the separate preamble injection at round 0

---

### Phase 3 — MemPalaceClient
**Goal:** One access layer. No behaviour change.

1. Create `MemPalaceClient` class in `server.py` — initialized once at startup
2. Migrate `_mempalace_chat_sync_loop`, `_mempalace_miner_loop`, stale-path purge to use it
3. `tool_mempalace_query` in brain.py still calls mempalace directly (it runs in a different process context) — document why as the one justified exception

---

### Phase 4 — Unified SSE event pipeline (frontend)
**Goal:** One `handleSSEEvent()`. No behaviour change.

1. Route all SSE events through a single dispatcher in `api.js`
2. Dispatcher updates `state`, calls `scheduleRender()`
3. `scheduleRender()` is debounced — one DOM update per animation frame
4. Reduces 29 render call sites to one

---

### Phase 5 — Single composer component (frontend)
**Goal:** One HTML composer. No behaviour change.

1. Extract composer HTML into one `<template>` or one `<div id="composer">`
2. Position via CSS based on current view (`state.currentView`)
3. Remove `_composerToggleEls()` — no longer needed
4. All toggle handlers work on one element

---

### Phase 6 — Unified artifact pipeline
**Goal:** Artifacts from all modes surface in one place.

1. Ensure scheduled task delegate emits `artifact_updated` SSE (verify or add)
2. Merge browse UIs into one artifact panel with source filter
3. Remove separate `API.browseArtifacts()` polling path

---

### Phase 7 — Server-side reference emission
**Goal:** Tools declare their references; client doesn't parse per-tool.

1. After tool execution in brain.py, if result contains MemPalace drawers/triples, emit `references` SSE event with normalized paths
2. Remove per-tool name checks from `extractReferencesFromToolResult()`
3. Path resolution (`.brain-extracted` → original) moves to server-side only

---

## What Not to Change

- `resolve_provider_for_model()` — already centralized, works well
- `state.js` — already a clean single source of truth
- The tool execution infrastructure (`_execute_tools_batch`, `_CONCURRENT_SAFE_TOOLS`) — already well-structured
- The stale-path purge in the project-sync daemon — correct single choke point, keep as-is

---

## Priority Order

1. **Phase 1 + 2** — highest leverage, fixes the deepest architectural problem (duplicated loops + prompts), pure refactor with no user-visible change
2. **Phase 4** — second highest, eliminates the 29-render-call problem before it grows further
3. **Phase 5** — third, composer duplication is annoying to maintain
4. **Phase 3** — MemPalaceClient is clean-up, lower urgency since the 4 call sites work
5. **Phase 6 + 7** — useful features but lower risk as-is

Start with Phase 1. Agree on the `ExecutionContext` shape before writing any code.
