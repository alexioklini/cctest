---
name: project_omlx_blockindex_gap_indexerror
description: "oMLX's Anthropic endpoint skips content-block indices (thinking@0 → tool_use@2); SDK .stream() accumulator crashed with IndexError; sidecar now uses raw create(stream=True) + own gap-tolerant accumulator"
metadata: 
  node_type: memory
  type: project
  originSessionId: 94888652-c9bc-4cd7-b8da-e14934da5ed4
---

2026-05-22: Fixed `(Sidecar error: IndexError: list index out of range)` that hit oMLX/gemma-4 (e.g. chat 6f906071) after tool calls but never Mistral-via-CLIProxyAPI.

**Root cause** (reproduced against real oMLX): oMLX serves the Anthropic `/v1/messages` endpoint directly. On a tool round it emits `content_block_start index=0` (thinking), `content_block_stop index=0`, then `content_block_start index=2` (tool_use) — **index 1 is never opened** (oMLX reserves a slot for an empty text block between thinking and tool_use but omits its start event). The Anthropic SDK 0.101.0 `client.messages.stream()` accumulator does `message_snapshot.content[event.index]` (`anthropic/lib/streaming/_messages.py:465`) and raises IndexError because `content` only has the index-0 block. Mistral/CLIProxyAPI emits contiguous indices → never trips. Symptom in logs: `[sidecar-proxy] ... reply=0c rounds=0 ... error=IndexError: list index out of range`.

**Fix** (`sidecar/sidecar.py:run_turn_streaming`): replaced `client.messages.stream()` + `get_final_message()` with the RAW `client.messages.create(stream=True)` iterator (which has no accumulator, so it never indexes by `event.index`) + a local `_AccumulatedMessage`/`_AccumulatedBlock` that keys blocks by their own index in an insertion-ordered dict — gaps collapse harmlessly. Every event is still emitted verbatim (Brain translator + replay unchanged). Builds `.content` blocks (text/thinking/tool_use w/ input_json_delta reassembly), `.usage`, `.stop_reason`. Verified end-to-end on gemma-4-26B: 2 rounds, tool dispatched with parsed input, stop_reason=end_turn.

Also: proxy now logs `data["traceback"]` on sidecar `error` events (`handlers/sidecar_proxy.py`) — previously discarded, which is why the message alone was uninformative.

Topology correction to remember: oMLX (`Lokal` provider, localhost:8000) speaks Anthropic directly; CLIProxyAPI (:8317) is the Mistral path. See [[feedback_omlx_anthropic]], [[project_sidecar_eos_token_strip]].
