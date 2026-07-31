---
name: project_chat_incremental_render
description: 2026-05-21 — chat.js renderMessages() now reconciles per-turn DOM blocks by key+hash instead of full innerHTML rebuild (Ebene 3 of the render-perf handover)
metadata: 
  node_type: memory
  type: project
  originSessionId: 89516f3e-1bb3-4742-83a1-64cb6e2d0190
---

2026-05-21 (commit `5b4b05d`): Implemented Ebene 3 of `CHAT_RENDER_PERF_HANDOVER.md`.

**Problem**: `renderMessages()` did `container.innerHTML = html` on every SSE event (tool_call/tool_result/thinking_done/done/worker.*), re-highlighting every prior turn's code + tool-result blocks. A 50KB tool result → multi-second main-thread block after the answer finished.

**Fix**: `renderMessages()` builds an ordered `blocks[] = {key, html, hash}` list (lcm-divider, lcm-N, turn-N) and calls new `_reconcileMessageBlocks(container, blocks)` which:
- stamps `data-render-key` + `_renderHash` on each block root
- keeps unchanged blocks (same key+hash) untouched — DOM + hljs survive
- replaces/inserts changed/new, removes stale; returns `changedRoots`
- post-render (hljs highlight, chevron-fit) runs ONLY over `changedRoots`; badges + `initTurnScrollSync` still run globally (cheap/idempotent)

`hash === html` currently (string identity is enough — full string compare is fast vs the re-highlight it avoids).

**Streaming coupling preserved**: callers still call `renderStreamingMessage(chat)` after `renderMessages()`; it removes+re-appends `.msg-streaming` into the last `.turn-body`. On `done` the assistant msg lands → last turn's body/hash changes → node replaced → stale streaming div cleared. No following-`renderStreamingMessage` needed on the `done` path for that reason.

**Verified**: JS syntax OK; `_reconcileMessageBlocks` unit-tested in-browser across init/no-change/last-changed/append/remove/order — all pass; hot path keeps prior turns untouched. **NOT yet verified**: real authenticated streaming turn + resumable-streaming reconnect regression — browser reload logged the user out (401), user must sign in and run the repro from the handover's Teststrategie. See [[project_right_panel_turn_grouping.md]].
