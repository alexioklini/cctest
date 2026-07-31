---
name: Tool results not shown in chat UI
description: Tool results are not displayed in the chat UI due to SDK hook streaming killing real-time display
type: backlog
related_to: [feedback_sdk_streaming, project_sdk_gap_plan, backlog_provider_model_sync]
sdk_relationships:
  - same_topic: bug_thinking_sidecar
  - depends_on: feedback_sdk_streaming
---

Tool results are not shown in the chat UI because the tool results display mechanism conflicts with SDK hook streaming that kills real-time delivery.

**Current state:** Tool call blocks show arguments and persist across page reloads, but the actual tool execution results/output are missing.

**Root cause:**
- SDK hook registration (PreToolUse/PostToolUse) causes streaming buffering per feedback_sdk_streaming
- Without functioning PostToolUse hooks, SDK-native tools (Bash, Read, Write, web_fetch, etc.) never deliver results to the server/client
- The `/mcp` tools/call pathway cannot push `tool_result` SSE events to chat UI

**Dependency chain:**
1. SDK cellular hooks designed for streaming (feedback_sdk_streaming)
2. Browser cannot display tool results in real-time
3. MCP-specific tools (memory_recall, delegate_task, skills) work but SDK-native tools don't

**Possible approaches:**
1. Re-enable hybrid approach: `/mcp` endpoint pushes tool_result SSE events to shared session queue (MCP tools only)
2. Accept current limitation: MCP tool results via shared queue, SDK-native tools show args only
3. Workaround solution via fallback streaming architecture

**Relevance:** Tool result visibility affects all tool-using workflows. Related to provider/model UI sync friction tracked in backlog_provider_model_sync.

**Status:** Blocked until streaming architecture constraints are addressed or SDK provides non-buffering PostToolUse hooks.
