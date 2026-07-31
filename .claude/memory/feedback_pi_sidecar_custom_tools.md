---
name: feedback_pi_sidecar_custom_tools
description: PI SDK createAgentSession requires Brain custom tools in customTools param, not tools
type: feedback
originSessionId: f5306daa-8770-4c87-a55c-8dd96143177d
---
In `pi_sidecar.ts`, Brain Agent custom tools (bridged via `/v1/tools/call`) must be passed to `createAgentSession` as `customTools: [...]`, NOT folded into `tools: [...]` alongside built-ins.

**Why:** PI SDK's `sdk.js:128-130` filters `options.tools` through `.filter((n) => n in allTools)`, silently dropping any tool whose name isn't a built-in (`read`/`bash`/`edit`/`write`/`grep`/`find`/`ls`). Custom tools end up registered but never active. `customTools` is the correct channel — AgentSession auto-activates them via `includeAllExtensionTools: true`.

**How to apply:** When touching the PI sidecar's tool wiring, keep built-ins in `tools:` and Brain tools in `customTools:`. Never merge them into one list.
