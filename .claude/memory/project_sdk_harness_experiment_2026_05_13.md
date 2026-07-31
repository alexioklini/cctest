---
name: SDK-harness eval experiment (2026-05-13)
description: Standalone Anthropic-format agentic loop via CLIProxyAPI; eval matches Brain v8.37.0 on policy-eval; scheduled-task replay produces a real 7KB report where Brain produced 1.1KB template
type: project
originSessionId: fe3309b9-f4b8-4c17-9cce-94197e4eface
---
Built `eval/sdk_harness/run.py` + `eval/sdk_harness/schedule_task.py` + `eval/run_sdk.py` — a raw-HTTP Anthropic `/v1/messages` agentic loop talking to CLIProxyAPI (localhost:8317, api_key=`brain-agent`, model `mistral-medium-3.5`). No Brain dependency, no SDK install. Tools (standalone): mempalace_query (direct ChromaDB), mempalace_kg_search (direct SQLite), read_document, read_file, exa_search, web_fetch, write_file (gated).

**Results — 15Q policy eval (Mistral-medium-3.5 judge, 2026-05-13):**
- SDK-lean (eval/harness/system_prompt.md, ~1200 chars) → mean **0.778**
- SDK-full (with REFUSAL/PRECISION/CITATION disciplines) → mean **0.893**
- Brain v8.37.0 baseline (per changelog) → mean ~0.873
- Opus gold (reused from prior run) → mean 0.877–0.909 (depending on subset)

→ SDK-full and Brain are statistically tied (Δ +0.02, within Mistral judge ±0.09 noise).

**Results — Mistral AI News schedule task replay:**
- SDK loop: 4 rounds, 10 tool calls, 40s, **6.9 KB real report with inline `[Title](URL)` citations**
- Brain run 805 (gemma-4-e4b, guided exec, same task): mostly placeholder template, "Tasks 1-5, 7-11 noted as searched but results not present"
- Brain run 807 (gemma-4-26b, no guided): round-2 LLM call hung for 200s and returned 0 tokens
- Brain run 809 (mistral-medium): worked (similar size output)
- Brain run 810 (mistral-small-2603): worked (1.1KB result + report.md)

**Why:** This is the bare minimum needed to compare a fresh Anthropic-style loop against Brain's native loop without the v6/v7 sidecar architecture. CLIProxyAPI handles the Anthropic-to-OpenAI translation; we never touched a sidecar process. The whole experiment is ~600 LOC under `eval/sdk_harness/` and `eval/run_sdk.py`.

**How to apply:**
- The SDK harness exists for ad-hoc A/B testing of loop architecture vs Brain. Re-run with `python3 eval/run_sdk.py --reuse-gold-from <prior_results_dir> --system-prompt eval/sdk_harness/system_prompt_full.md --label sdk-full`.
- Scheduled-task replay: `python3 eval/sdk_harness/schedule_task.py --schedule-id 95`.
- Don't conflate "SDK-full beats SDK-lean by 0.115" with "Anthropic SDK is better than Brain" — the win is the disciplines, not the loop. Brain v8.37.0 already has the disciplines and lands on the same number.
- The scheduled-task win **IS** real for the gemma-local failure modes (e4b silent-after-tool, 26b round-2 timeout). It is NOT a win vs Brain + Mistral-medium / Mistral-small, which also work fine.

**Honest framing**: The eval (policy Q&A) is a wash — SDK-full and Brain tie at ~0.88. The scheduled-task win is real but only for local oMLX models. Brain + Mistral cloud works fine; SDK + Mistral cloud works fine; both produce real reports.

**Apples-to-apples local-model test (added 2026-05-13 evening):**
- Brain native loop + gemma-4-26b (oMLX): run 807 → round-2 hung 200s, 0 output, empty result
- Brain native loop + gemma-4-e4b (oMLX, guided exec): run 805 → placeholder template
- **SDK harness + gemma-4-26b (oMLX /v1/messages, api_key=brain)**: 9 rounds, 8 tool calls, 113s, **6.5 KB real cited report**
- **SDK harness + gemma-4-e4b (oMLX /v1/messages)**: 8 rounds, 7 tool calls, 109s, **6.9 KB real report content** — but model produced it as final assistant text instead of calling write_file (`written files: []`). Cosmetic vs Brain's catastrophic failure.

**Conclusion**: For scheduled-task / agentic workloads on local oMLX gemma models, the Anthropic-format loop materially outperforms Brain's native OpenAI-compatible loop. Root cause not isolated — could be: payload-assembly differences (Brain inflates round-2 prompts unexpectedly per the 807 trace), Anthropic content-block tool flow vs OpenAI tool_call/tool_result flow, oMLX serving the Anthropic endpoint more cleanly than its OpenAI endpoint, or all three.

For e4b, write_file non-call is likely a system-prompt fix away (stronger imperative). Worth a retest with `system_prompt_scheduler.md` tightened before declaring e4b unusable.

**Real Anthropic SDK test (2026-05-13 evening, eval/sdk_harness/run_sdk.py + anthropic 0.101.0 in .venv_sdk):**

The raw-urllib harness only proved the wire format works; this round tested the actual `anthropic` Python SDK (`client.messages.create(...)`).
- SDK + mistral-medium-3.5 (CLIProxyAPI): worked, 3r, 2t, 12s
- **SDK + gemma-4-26b (oMLX /v1/messages, api_key=brain): 4r, 8t, 138s, 6.4 KB report saved via write_file**
- **SDK + gemma-4-e4b (oMLX): 10r, 9t, 78s, 4.9 KB report saved via write_file** (final assistant text was just `<eos>` — cosmetic, file write happened first)
- No streaming hangs, no anyio side effects (script is a fresh process with NO `claude_cli` import — the old `feedback_sidecar_no_claude_cli` constraint still applies for Brain integration)

The real SDK works in-process against both CLIProxyAPI and oMLX, and unblocks both gemma variants on the scheduled-task workload. e4b is actually better through the SDK than through the raw-HTTP harness (SDK e4b called write_file; raw-HTTP e4b emitted the report as chat text and skipped write_file).

**What's NOT yet proven**: streaming (`messages.stream`) untested; SDK import alongside `claude_cli` in the same process untested. If integrating, the sidecar architecture from v6/v7 era is the safe path — keep the SDK out of the main Brain process.
