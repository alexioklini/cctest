---
name: thinking_format init upgrade requires deep-copy of existing models
description: init_models_config must deep-copy existing_models or the diff-based persist branch in server.py never saves forward-looking thinking_format upgrades
type: feedback
originSessionId: 3e800d4d-22e4-4710-962c-108168e4f129
---
`engine.init_models_config(providers, existing_models, …)` previously did `_models_config = dict(existing_models)` — a SHALLOW copy. The per-model cfg dicts (values) were aliased back to the caller's snapshot.

The forward-looking re-detect branch (claude_cli.py around line 17484) mutates `cfg["thinking_format"]` in place when stored is `"none"` and the provider-aware detector now returns a real format.

**Why:** server.py's startup persist gate compares `synced` (RAM) vs `existing_models` (raw file_config snapshot) via `_models_differ`. With aliased value dicts, both sides showed the upgraded value at compare time → no diff → no save. The upgrade lived only in RAM until the next explicit Save in the Models tab.

**How to apply:** any time init_models_config gains a new in-place upgrade path (provider-aware detector additions, KNOWN_MODELS field migrations, profile auto-pick changes), the deep copy at line 17407 is what makes the persist branch fire on next restart. Don't revert it back to `dict(existing_models)`.

This bit Gemma 4 on 2026-04-26: detector was patched to return `reasoning_field` for `gemma-4 / gemma4` on oMLX, but the three stored entries stayed `"none"` after restart until the deep-copy fix landed.
