---
name: Gemma-4-e4b unsuitable for tool-calling agentic work
description: Original native-loop finding superseded twice — sidecar restored basic tool-calling (2026-05-15), and `<eos>`-token strip + empty-round nudge (2026-05-16) cured the residual reply=5c failure mode on chat turns. Model is viable.
metadata:
  type: feedback
originSessionId: fe3309b9-f4b8-4c17-9cce-94197e4eface
---
**Status: RESOLVED.** Original finding kept for context.

**Original (2026-05-13, native loop)**: gemma-4-e4b-it-4bit could not reliably run tool-calling agentic loops. Run 805 + post-disable retest both failed: calls a tool, gets the result, emits EOS with no narration → loop terminates → no useful output.

**2026-05-15 retest under sidecar (Phase 5 gate-2)**: scheduled tasks worked — 3/3 clean Mistral AI News runs (5.5–5.8 KB reports). But chat turns kept showing `reply=5c` in `[sidecar-proxy]` logs — model emitted the literal string `<eos>` as text in the final round, sidecar accepted it as the answer.

**2026-05-16 root-cause fix**: e4b on oMLX emits `<eos>` (and similar end-of-sequence tokens) **verbatim as plain text** instead of using them as a stop signal. Sidecar's `_visible_text` now strips known EOS tokens before the empty-round check; combined with the empty-round nudge loop, the model gets a retry prompt and produces real content on the second attempt. Verified on F1_geldwaesche refusal canary: clean 1132-char refusal with proper "kein Treffer im Korpus" framing. See [[project_sidecar_eos_token_strip]].

**How to apply:**
- gemma-4-e4b-it-4bit is **viable for chat AND scheduled tool-calling** under sidecar ≥ this fix.
- If a future local model shows the same `reply=Nc` pattern where N matches a known token length, add the token string to `_EOS_TOKENS` in `sidecar/sidecar.py`.
- For higher-quality tool calling, still prefer gemma-4-26B (see [[project_local_model_tool_quality]]); e4b is the cheaper viable alternative.
