---
name: mistral-disable-parallel-tool-use
description: 2026-05-14 — Mistral via CLIProxyAPI occasionally dropped write_file in parallel tool batches; fix wires Anthropic-SDK tool_choice.disable_parallel_tool_use through sidecar. Causality not fully proven.
metadata: 
  node_type: memory
  type: project
  originSessionId: 4e63f2da-da82-4e5e-a538-a10664d35872
---

Shipped as part of commit `c8ab71c` (the unification work — see [[project_research_minimal_purpose]]).

## Symptom

Canonical "Mistral AI News" research task on `mistral-medium-latest` via CLIProxyAPI in streaming mode: write_file was occasionally missing from the model's tool-call batch, leaving the schedule with no `report.md` artifact. Initial 3-run sample on `research_minimal` was 1/3 passes. Logs from passing runs (837, 838) showed "9 tool calls at +0s" — consistent with parallel batching dropping the final tool.

## Fix

Two coupled changes wire OpenAI-shape `parallel_tool_calls: false` through to the Anthropic SDK's `tool_choice.disable_parallel_tool_use=True`:

1. **`sidecar/sidecar.py`** (`~line 191`): when `req["disable_parallel_tool_use"]` is truthy AND there are tools, sidecar attaches `tool_choice = {"type": "auto", "disable_parallel_tool_use": True}` to the Anthropic SDK call.
2. **`handlers/sidecar_proxy.py`** (`run_turn` + `run_turn_blocking`, `~line 379+436`): new `disable_parallel_tool_use: bool` kwarg, forwarded into the sidecar request body.
3. **`brain.py:_execute_scheduled`** (`~line 16357`): reads the model's existing `parallel_tool_calls` flag and passes `disable_parallel = (parallel_tool_calls is False)` into `run_turn`.

Existing model config:
```json
"mistral-medium-latest": { "parallel_tool_calls": false, ... }
```

Confirmed end-to-end via sidecar debug log — the kwarg arrives in the SDK call.

Also changed in the same window: Mistral sampling pinned to `top_p=0.85` (matches the harness reference numbers from earlier eval work).

After both changes, Gate-PT-2 on the same task went 3/3 ✅.

## What's proven vs unproven

**Proven:**
- The Anthropic-shape `tool_choice.disable_parallel_tool_use=True` field reaches the SDK call on Brain's side.
- 3/3 pass rate is consistent with the fix working.

**Unproven (Open Architectural Question §4 in the plan):**
- Whether CLIProxyAPI honors the Anthropic-shape field when proxying to Mistral's actual API. CLIProxyAPI may silently drop it.
- Whether the win comes from `disable_parallel_tool_use`, `top_p=0.85`, or noise (n=3 is small for a stochastic local agent).

Three follow-up experiments to disentangle (deferred — only if firm evidence needed):
1. 10-run sample with `top_p=0.85` only (no disable_parallel) — isolates top_p.
2. 10-run sample with `disable_parallel_tool_use=True` only (no top_p change) — isolates the kwarg.
3. Wireshark / mitmproxy on the SDK → CLIProxyAPI hop to confirm forwarding.

## How to apply

- When a Mistral schedule still drops final tools intermittently: set the model's `parallel_tool_calls: false` in config. The scheduler already wires the rest.
- When debugging "did disable_parallel actually reach the wire?": sidecar debug log prints the SDK request body — look for `tool_choice` block.
- Don't claim the fix is causal until the three experiments run. Strongest defensible claim: the wiring is correct on Brain's side; provider behavior is opaque.
- Related stochastic behavior: see [[feedback_mistral_small_stochastic_quality]] — Mistral Small was previously seen with 0/7-to-5.5/7 spread on canary. Pass/fail variance on small samples is the norm, not the exception.
