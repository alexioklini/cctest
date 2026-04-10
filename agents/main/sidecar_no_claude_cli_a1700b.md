---
name: Sidecar_No_Claude_CLI
description: Critical process isolation constraint to preserve anyio streaming functionality
type: reference
agent: main
last_recalled: 2026-04-09
related:
  - file: feedback_sdk_streaming.md
    type: depends_on
    detail: SDK streaming issues require sidecar process isolation to prevent anyio streaming breakage
  - file: bug_thinking_sidecar.md
    type: depends_on
    detail: Thinking parameter side effects require sidecar isolation to prevent SSE stream hangs
---

Memory 'feedback_sidecar_no_claude_cli.md' establishes that the SDK sidecar (sdk_sidecar.py) must NEVER import claude_cli, as it breaks anyio subprocess streaming by causing event batching. This is a critical constraint that directly shapes Phase 1.1 (HTTP MCP server approach) and Phase 6.1 (hook integration must use network calls, not module imports).

- **References:** project_sdk_gap_plan.md (all phases requiring clean sidecar process)
- **Extends:** Prior architectural decisions by imposing process-level hygiene requirements
- **Depends on:** feedback_sdk_streaming.md, bug_thinking_sidecar.md (both problems require process isolation solution)
- **Same topic:** Process isolation, sidecar best practices
