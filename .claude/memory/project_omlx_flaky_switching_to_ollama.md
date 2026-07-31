---
name: omlx-flaky-switching-to-ollama
description: "2026-05-15 — oMLX + gemma-4-26B too flaky for tool-loop work; switching local inference to Ollama. Infra fixes from today's debugging session stay relevant."
metadata: 
  node_type: memory
  type: project
  originSessionId: 6a44543c-73d9-47db-bace-21bca96ec48d
---

## Decision
After a full day of debugging gemma-4-26B failure modes on the policy eval,
we hit a wall: even with every infra and config knob tuned, the model
intermittently emits empty content or spins on tool retries on simple
refusal questions. **Switching local inference backend to Ollama.**

## What the day's debugging produced (still useful)
Even if oMLX gets dropped, these all shipped to main and are worth keeping:

1. **Warmup keeper load-aware backoff** — `LocalProviderQueue.provider_busy(name, grace=15s)`; keeper defers when target provider has live user load. See [[project_warmup_load_aware_backoff.md]].
2. **`skip_warmup: true` on session create** — eval/batch clients opt out of per-session `_trigger_warmup`. Body flag honored in `handlers/chat.py:_handle_create_session`.
3. **Citation re-round: drop unverified-quote trigger** — fires only on `uncited_claims/claim_total > 0.30` now; the unverified-quote signal was false-positive-prone on PDF→md extraction. See [[feedback_reround_uncited_only.md]].
4. **Citation re-round spinner event** — `citation_reround_start` / `citation_reround_done` SSE events surface the 10-20s re-round latency in the chat spinner.
5. **Per-provider `supports_chat_template_kwargs` flag** — config.json field decides whether the gateway accepts the oMLX/vLLM `chat_template_kwargs` extension. Replaces the buggy `provider.lower() == "omlx"` heuristic that broke when the user renamed their provider to "Lokal". `_provider_supports_chat_template_kwargs()` in brain.py is the resolver. Anthropic-shape `omlx` legacy id is grandfathered.
6. **Sidecar forwards `chat_template_kwargs` via `extra_body`** — `handlers/sidecar_proxy.py:_chat_template_kwargs()` plus the sidecar's `sampling_kwargs["extra_body"]`. Works on single-round calls.
7. **Sidecar whitespace guard on `final_text`** — gemma emits `\n` placeholders before tool_use blocks; the guard prevents those ghost newlines from becoming Brain's "final assistant reply." Failures now surface as empty (visibly broken) instead of silent `\n` ghost replies.
8. **Detector: gemma-4 / qwen3 / magistral are reasoning models regardless of provider** — `_detect_thinking_format` dropped the `p == "omlx"` gate. Auto-upgraded gemma-4-26B/e2b/e4b from `thinking_format: none` → `reasoning_field` on startup.

## What did NOT solve gemma-4-26B's empty-reply quirk
- Per-request `extra_body.chat_template_kwargs.enable_thinking=false` — works on single-shot calls, doesn't reliably suppress reasoning channel in multi-round tool loops on oMLX wire.
- oMLX-side model config `enable_thinking: false` — fixed simple cases (1252-char real answer on canary chat) but F2/F3/C3 still failed intermittently.
- Turboquant KV cache disabled — F3 went from 0c → 0.85; F2 still timed out; C3 still bailed empty. Real but partial improvement.

**Operational note:** if you go back to oMLX, **leave Turboquant KV off** for gemma-4-26B during tool work. The quality degradation in tool-loop decision-making is measurable.

## Eval baseline for Ollama setup
The 15Q policy canary + Opus golds (reused from `eval/results/20260515T075806_disc-none_gemma26b-test/`) is the baseline to beat. The 4 problem questions where oMLX failed (F1/F2/F3 refusal + C3 citation) are the canary for whether Ollama is more reliable on this corpus.

## Run commands once Ollama is set up
1. Add an Ollama provider entry to `config.json → providers` (`type: openai`, `base_url: http://localhost:11434/v1`, `supports_chat_template_kwargs: false` until verified otherwise).
2. Add `gemma-4-26b` (or whatever Ollama tag) under `config.json → models` with the provider set.
3. Re-run: `BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --skip-gold --reuse-results eval/results/20260515T075806_disc-none_gemma26b-test --brain-model <ollama-model-id> --only F1_geldwaesche,F2_kreditvergabe,F3_arbeitszeit,C3_isms_ziele --label ollama-canary`
