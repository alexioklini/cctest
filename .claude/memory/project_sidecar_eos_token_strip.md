---
name: sidecar-empty-round-nudge-eos-token-strip
description: "2026-05-16 — sidecar handles \"model returned no usable text\" with 3 retry nudges + give-up message; EOS-token strip catches local models that emit `<eos>` verbatim. Fixes silent-empty-reply class of failures."
metadata: 
  node_type: memory
  type: project
  originSessionId: 2a9491cc-644e-4089-8fa5-2ddd12d061b1
---

**Shipped 2026-05-16** in `sidecar/sidecar.py`. Two related fixes for the silent-empty-reply class of failures (model finishes a turn without producing a usable assistant reply, system silently swallows it).

**The problem chain that triggered the work**:
1. Chat `83fef7b7` — gemma-4-26B-A4B (Mistral Small 4 family local), 2 rounds 1 tool, `reply=0c`. Model produced no text after the tool result. Chat reload showed nothing (the empty branch at `handlers/chat.py:1487` rolled back intermediate messages without persisting anything).
2. Chat `aeb92a82` — gemma-4-e4b-it-4bit, `reply=5c`. Model emitted literal `<eos>` as text. The nudge fix from step 1 didn't help because `"<eos>".strip()` is truthy.

**The fix** (single file: `sidecar/sidecar.py`):

1. `_EOS_TOKENS` tuple + `_visible_text(text)` helper. Strips known end-of-sequence tokens (`<eos>`, `<end_of_turn>`, `<|endoftext|>`, `<|im_end|>`, `<|eot_id|>`, `<|end|>`, `</s>`) from both ends of the round's text, repeatedly, until stable. Returns `""` for whitespace-only / EOS-only / empty input.
2. `empty_nudges` counter + `EMPTY_NUDGE_MAX = 3` + `EMPTY_GIVEUP_TEXT = "No response was returned. Please modify your request or change the model."` declared at turn start.
3. Mid-loop `final_text` update uses `_visible_text` (so `<eos>` in a non-final round doesn't clobber an earlier real answer).
4. Terminating `if not tool_uses:` branch:
   - If `_visible_text(round_text)` is non-empty → that's the answer, break.
   - Else if `empty_nudges < 3` → emit `empty_round_nudge` SSE event, append a synthetic user message ("Please provide your answer now based on the information gathered so far."), `continue` (don't break) — empty round still counts toward `max_rounds`.
   - Else → set `final_text = EMPTY_GIVEUP_TEXT`, `final_stop_reason = "empty_after_nudges"`, break.
5. `max_rounds` exhausted with empty `final_text` → also surface `EMPTY_GIVEUP_TEXT` (same persistence invariant).
6. `serialised_blocks` padding: if a round produces literally nothing (no text, no tool_use, no thinking), pad with a single-space text block before appending to `messages` — Anthropic API rejects empty content blocks on subsequent rounds.

**Why this makes everything persist**: chat.py's persistence is gated on `if reply:` at line 1272. By guaranteeing `final_text` is always non-empty when a turn ended (real answer or give-up text), the existing happy path persists the assistant message with `_partial_tools` metadata. The empty-reply rollback at line 1487 still exists as a safety net but no longer triggers for this class. No chat.py change needed.

**How to apply:**
- If a future local model produces `reply=Nc` where N matches a small constant (5, 13, etc.) → likely a new EOS-token variant. Add the literal string to `_EOS_TOKENS` in `sidecar/sidecar.py`.
- The nudge text is plain English; consider matching system-prompt language for non-English models if false-positive give-ups appear.
- Verified on F1_geldwaesche refusal canary across gemma-4-26B + gemma-4-e4b. See [[feedback_gemma_e4b_unsuitable_for_tools]].
