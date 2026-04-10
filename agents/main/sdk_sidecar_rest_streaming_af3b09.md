---
name: SDK_Sidecar_REST_Streaming
description: "SDK hook registration breaks streaming; REST sidecar architecture required with hooks server-side"
type: reference
agent: main
last_recalled: 2026-04-09
related:
  - file: feedback_omlx_anthropic.md
    type: contradicts
    detail: "oMLX's anthropic API type creates provider-specific streaming behaviors that influence SDK streaming architecture choices"
---

Memory 'feedback_sdk_streaming.md' documents that SDK hook registration (PreToolUse/PostToolUse callbacks) causes streaming buffering. The solution is a REST sidecar architecture where hooks run server-side via /v1/hooks/run endpoint, never enabling hooks_enabled in sidecar payload. This directly relates to SDK gap plan Phase 6.1 (SDK Hook Integration) and Phase 1.1 (HTTP MCP Server).

- **References:** project_sdk_gap_plan.md (hook integration planning), feedback_sidecar_no_claude_cli.md (sidecar process constraints)
- **Contradicts:** Standard expectations where SDK streaming and provider API usage are orthogonal concerns; oMLX anthropic API type creates coupling
- **Extends:** SDK migration gap analysis by documenting the actual streaming failure mechanism and architectural fix
- **Same topic:** SDK streaming architecture, sidecar constraints
