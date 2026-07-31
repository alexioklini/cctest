---
name: SDK sidecar must NEVER import claude_cli
description: Critical constraint — importing claude_cli in the sidecar process breaks anyio subprocess streaming completely
type: feedback
related_to: [feedback_sdk_streaming, project_sdk_gap_plan, project_summary, project_token_fixes]
---

The SDK sidecar (sdk_sidecar.py) must NEVER import claude_cli, directly or indirectly.

**Why:** claude_cli has module-level side effects that break anyio's subprocess I/O. When imported in the same process as asyncio.run(query(...)), all streaming events arrive batched instead of in real-time. This was the root cause of hours of debugging — the fix was isolating the SDK in a clean process.

**How to apply:** Any code that runs in the sidecar process must not touch claude_cli. Brain Agent tools (memory, gmail, etc.) must be exposed via HTTP MCP server on the main server — the sidecar calls them via network, never by importing the module. All system prompt building, provider env resolution, and tool dispatch happens in the main server process BEFORE handing off to the sidecar.
