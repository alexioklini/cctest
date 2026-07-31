---
name: project_tool_result_use_id_pairing
description: 2026-05-22 (ac25e91) live tool calls stuck on spinner / output invisible until reload — tool_result SSE event must carry tool_use_id to pair with tool_call
metadata: 
  node_type: memory
  type: project
  originSessionId: b58d2981-c459-408c-9922-911f4e87cf36
---

Live tool calls showed a spinner that never flipped to ✓ and tool output was invisible until page reload. Root cause: the `tool_result` SSE event omitted `tool_use_id` while `tool_call` carried one.

Client pairing in `web/js/chat.js` `renderToolCall` lookahead:
- `idMatch = call.tool_use_id && result.tool_use_id && equal` — needs id on BOTH
- `nameMatch = !call.tool_use_id && result.name === call.name` — name fallback ONLY fires when the call row has NO id

So a live `tool_call` with an id + `tool_result` without one fails idMatch AND disables nameMatch → result never attaches. Reload worked because rows rebuilt from assistant `metadata.tools` (sessions.js emitTools) are id-less on both sides → nameMatch pairs them.

Fix: `handlers/sidecar_proxy.py` `tool_dispatch_done` → `tool_result` translation now forwards `tool_use_id` (was already used to build the result-key lookup, just not in the payload). Single choke point; also fixes parallel batches that mis-paired identically-named calls via name fallback.

**Invariant**: any future `tool_result` emit must keep `tool_use_id` in the payload, and it must equal the matching `tool_call`'s id. Verified by capturing raw SSE: both events shared the same `call_xxxx` id after the fix. See [[project_chat_incremental_render]] (same live-render path).
