---
name: maybe_retroactive_isolate latent unpack bug — fixed 2026-04-29
description: execution.py:840 unpacked 2 values from _summarise_tool_result which returns 3 since it grew usage_capture; crashed every read_document call >auto_threshold_bytes including the kg-real-policies IT-Risk Score test
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
`_summarise_tool_result` in execution.py returns `(summary, sections, usage_capture)` — three values. The "auto-isolated" retroactive path `maybe_retroactive_isolate(execution.py:840)` was never updated to match and unpacked only `(summary, sections)`. Since the third value is a dict, every call through this path raised `ValueError: too many values to unpack (expected 2, got 3)` at the point of unpacking, BEFORE the result reached the agent loop.

**Symptom**: any tool that writes >auto_threshold_bytes (default 65536) AND has profile heavy=auto rather than heavy=true crashes silently. read_document on a 40K+ char PDF triggers this; smaller files don't. The model never sees the result, so it falls back to training data and hallucinates — which is exactly what showed up on session c4027c7efdba (and previously ba3b33b8) as "wrong IT-Risk Score answer."

**Fix**: line 840 now reads `summary, sections, _summariser_usage = _summarise_tool_result(...)`. The other call site at line 766 was already correct.

**Why it stayed hidden**: the only call site exercising this path is the auto-threshold retroactive isolation — heavy=true tools go through `run_worker_subagent` which has the correct unpack. If a tool's profile is heavy=auto in `execution.profiles` (read_document was) AND its output exceeds auto_threshold_bytes, it routes here. So failures only manifest on large outputs, which are precisely the cases users care about most. The error message in logs went past the agent loop's catch boundary as a normal Python ValueError; the request looked like a stalled stream from the user's side.

**How to apply**: when adding a new return value to a tuple-returning helper, search for ALL unpack sites with `grep -n "<funcname>(" *.py | grep -E '=\s*<funcname>'` plus `grep -B 1 "<funcname>(" *.py | grep -E '^\s*\w+(,\s*\w+)+\s*='`. Type hints would have caught this — execution.py uses them but the helper's signature wasn't enforced at call site (Python doesn't check assignment-side arity at type-check time without strict mode).
