---
name: Profile overlay vs per-model resource knobs
description: MODEL_PROFILES overlays must not include resource-allocation knobs (warmup, GPU pinning, etc.) — only request-handling style
type: feedback
originSessionId: 5804133c-764d-4360-9ef9-f66e7b98b218
---
`MODEL_PROFILES` in `claude_cli.py` is a sparse overlay that fills in defaults when the raw model config doesn't specify a value. Two classes of knobs exist:

- **Request-handling style** (correct in profiles): `caveman_system`, `parallel_tool_calls`, `deferred_tool_groups`, `compact_threshold`, `tool_result_char_limit`, `include_tools_guide`. These describe how to talk to the model on every request — they apply uniformly to any model the profile is attached to.
- **Resource allocation** (NOT correct in profiles): `warmup`, `warmup_mode` (debatable), and any future knob that consumes a finite host resource (GPU RAM, KV cache slot, file handles). Each model competes for the same pool.

**Why:** the speed profile is auto-attached to every local model (`init_models_config` line ~17077). If `warmup: true` lives in the overlay, every newly-discovered local model gets warmup enabled by default, even when the user only wants one of three to stay hot. UI checkbox toggling the field "off" used to `delete` the field, which let the overlay re-fill `True` — silent re-enablement. Fixed 2026-04-25 by removing `warmup` from the speed overlay and making the UI write explicit `false`.

**How to apply:** when adding a new field to a profile, ask "if I had three models on this profile sharing a GPU, would I want this field set the same way on all three?" If no, it's a per-model decision and belongs on the raw model config, not the profile overlay. Backfill missing fields explicitly during migrations rather than relying on overlay defaults to fill them in.
