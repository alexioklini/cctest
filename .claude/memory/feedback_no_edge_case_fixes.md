---
name: Never fix edge cases — solve the real problem
description: When a bug is reported, find the general root cause and fix it there, not the specific symptom
type: feedback
originSessionId: 34125a16-aa2f-4ae7-bcd0-316b787d8db0
---
Never patch a specific edge case when there is a general fix available. Fixing `worker_*.json` by filename pattern is wrong — the real problem is that intermediate artifacts lack a role signal on the wire. Fix the role signal, then gate on that role everywhere.

**Why:** Edge-case patches rot: a future tool or filename convention bypasses the patch, the bug comes back, and there's now dead special-case code to maintain. The general fix handles all current and future cases at once.

**How to apply:** When a bug is reported, ask: "what is the general property that distinguishes the bad case from the good case?" Fix at that property. The specific reported case is then handled as a side effect, not a target.
