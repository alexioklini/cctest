---
name: KV-prefix stability rule
description: Any wall-clock value in _build_system_prompt must round to hour-or-coarser, or warmup becomes useless
type: feedback
originSessionId: f92bbdf3-a2fa-4a9b-af8e-db7ada336216
---
The interactive-chat system prompt (`_build_system_prompt` in `claude_cli.py`, line ~16419) must not contain minute-precision timestamps, request IDs, or any value that changes between the warmup prime and the user's first real turn.

**Why:** oMLX's prompt cache reuses the KV prefix only when the token sequence is byte-identical. A single differing character (e.g. `18:17` vs `18:23` in "Current date and time:") forces a full re-prefill on the first real turn. On gemma-4-26b this meant ~15s first-token latency even though warmup had just primed it — the ~20s of prefill work was wasted. Rounding the timestamp to `%Y-%m-%d %H:00 %Z` dropped first-response to 2-3s, near cloud-LLM speed.

**How to apply:** when editing `_build_system_prompt` or anything it composes (soul, tools.md, project context, team info), keep the output deterministic across short time windows. If you need time awareness, use hour precision or coarser; for minute/second precision, tell the agent to call a tool at runtime. Also true for anything similarly stable-by-design: working directory, agent id, OS name — those are fine because they don't drift.

Other prerequisites that also matter for cache reuse (learned during the same debug): the warmup payload's tool list must include MCP tools (not just built-ins), tools must be sorted by name, and the `.` user message must be appended — otherwise the token sequence drifts elsewhere in the prefix.
