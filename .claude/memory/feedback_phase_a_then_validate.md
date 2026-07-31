---
name: phase-a-then-validate-pacing
description: "Land Phase A's core mechanism, validate end-to-end (Gate-PT-2), THEN add Phase B's UI/config scope — splitting catches regressions before they compound"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 4e63f2da-da82-4e5e-a538-a10664d35872
---

Land the core mechanism first, validate end-to-end against the load-bearing gate, THEN add downstream scope (UI, config fields, edge cases). Don't bundle them in one pass.

**Why:** During the prompt-tools unification (2026-05-14, commit `c8ab71c`), the plan's original "scheduled tasks get the full interactive surface" decision regressed gemma-4-e4b from 100% to 20% on the canonical research task. The user's "Phase A first, then validate" pacing turned this into a discoverable, fixable regression — a `research_minimal` purpose was added with a leaner prompt, validated against the same Gate-PT-2, and only then did Phase B (`schedules.tool_profile` field + UI dropdown) get layered on. Had Phase A + Phase B shipped together, the e4b regression would have surfaced alongside UI behavior questions, conflating "did we fix the prompt?" with "does the dropdown work?" and slowing both.

**How to apply:**
- On any multi-phase plan, identify the "load-bearing test" up front (Gate-PT-2 in the unification plan). It's the gate the work exists for. Pass it before adding adjacent scope.
- Be willing to course-correct between phases. The original plan's "scheduled = interactive surface" was the right shape at the design table and the wrong shape at the keyboard; the fix was a new purpose, not an apology for the design.
- "I want UI scope and core mechanism in the same PR for atomicity" is a smell. UI scope is downstream of "the mechanism works"; merging them couples the timing.
- Particularly applies when the work touches token-cost-shaping surfaces (system prompts, tool lists, KV-prefix invariants). Failures there compound silently — a 10% regression in eval mean is invisible until you run the eval; a Gate-PT-2 failure shows up the same evening.
