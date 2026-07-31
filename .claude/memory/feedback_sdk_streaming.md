---
name: SDK streaming and hooks discovery
description: SDK hook registration causes streaming buffering — never enable hooks_enabled in sidecar payload. REST sidecar architecture required for streaming.
type: feedback
related_to: [feedback_sidecar_no_claude_cli, project_sdk_gap_plan, project_summary]
sdk_relationships:
  - extends: project_sdk_gap_plan
---

SDK hook registration (PreToolUse/PostToolUse callbacks) causes the SDK's query() to buffer ALL StreamEvent objects until the turn completes. This was the root cause of streaming not working.

**Why:** Confirmed by testing identical payloads — only `hooks_enabled` differed. True=batched (0.00s spread), False=streams (9.45s spread). The hooks mechanism in the SDK blocks the async event yield pipeline.

**How to apply:**
- NEVER pass `hooks_enabled: true` to the sidecar
- Run hooks server-side in the `/mcp` tools/call handler instead
- Sidecar must be a REST API (POST /query → GET /events/{id}) — socket-based SSE streaming fails inside the server process due to wfile buffering
- Keepalive SSE comments (`: keepalive\n\n`) needed to keep HTTP/1.0 stream active
- `query_sync` must also use REST polling, not raw sockets
- Custom slash commands must be expanded client-side (web UI sendMessage) before sending to LLM
