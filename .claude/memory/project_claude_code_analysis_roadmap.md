---
name: Claude Code Leaked-Source Analysis Roadmap
description: Three-phase adoption plan from the Apr 2026 leaked Claude Code source analysis
type: project
originSessionId: 420945d9-094d-4db9-a3c0-9e8fa2afa788
---
Full analysis at `/Users/alexander/Documents/dev/cctest/leaked-source-analysis-2026-04.md` (616 lines).

## Phase 1 — quick wins + stop-hook refactor
1. Token-budget diminishing-returns guard (stop if <500 tokens added over 2 rounds)
2. Verify forked-agent prompt-cache reuse in `execution.py` (investigation)
3. Streaming tool progress messages (extend `worker.progress` SSE to non-heavy tools)
4. Stop-hook lifecycle refactor (move post-response work out of middleware into explicit phase)
8. Prompt suggestion via forked agent (first consumer of #4)
9. Snip-boundary history compression
12. MCP tool-result collapse classifier

## Phase 2 — architecture
7. QueryEngine class extraction (do BEFORE #5 — coordinator mode layers more cleanly onto a class-based engine)
5. Coordinator mode (formal coordinator/worker split + shared scratchpad dir)
11. Job classification routing

## Phase 3 — memory (deferred until after 1+2)
Decision point: llmwiki (from yesterday's analysis) vs md-file dual-path memory (items 6 + 10 from report).
User direction: llmwiki should surface as md-file or task, so phase 3 is really "figure out integration mechanism" not a feature choice.

**Why:** User wants to resume this roadmap across sessions without re-deriving it from the report. Sequencing reflects dependencies (#4 unblocks #8; #7 unblocks #5) and risk (cheap self-contained items first).

**How to apply:** When user asks about Claude Code adoption work, start from phase 1 item 1 unless they specify otherwise. Don't suggest items from skipped set (React/Ink TUI, Keybindings, Voice, Vim, native-ts, bootstrap, structured output, per-session memory-mode persistence which already shipped in v7.7.0).
