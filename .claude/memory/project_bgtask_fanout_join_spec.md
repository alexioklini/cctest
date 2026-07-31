---
name: project_bgtask_fanout_join_spec
description: "SPECCED, NOT BUILT (2026-05-28) — model-driven fan-out + join for background tasks (decompose→offload N→recombine). Design + 4 locked policies + must-eval-on-local mitigation"
metadata: 
  node_type: memory
  type: project
  originSessionId: 35dafeff-89ed-4d06-ae5c-12dfcdf0a7a8
---

Follow-up to the shipped background-tasks feature (v9.45.x — `run_background_task` tool, detached same-agent run via engine/background_tasks.py, auto-delivery turn on idle completion via handlers.chat.deliver_background_results; see git changelog 9.45.0/9.45.1). User wants the GENERAL decompose→offload→recombine loop: a request like "search X and create a report" → model splits into background research task(s) + a synthesis step that uses the result(s) to finish the original goal. User's framing: "das Problem wird sein, die Aufgabe in Tasks zu zerlegen, diese in Background-Tasks zu schieben und das Ergebnis dann zu verarbeiten."

**Status: design locked, NOT implemented. No code written, no memory of partial work.**

## Decisions (locked with user)
- **Scope:** parallel fan-out + join (NOT just single offload). Model spawns N background tasks; one follow-up recombines ALL results once every task is terminal.
- **Decider:** model-driven via tool params. NO framework planner, NO DAG (Rule 5 = judgment is the model's).
- **Partial failure:** deliver-with-failures. Group fires once all members terminal (done OR error/cancelled); each shown as output-or-error; model decides how to finish.
- **Stall guard:** per-group timeout → deliver partial (mark unfinished members timed-out) so the group ALWAYS delivers.

## Build (5 pieces)
1. **Schema:** `group_id` + `follow_up` + `group_started_at` on `background_tasks` (or a `task_groups` table). Group = the join + delivery unit. Single task = group of one (current behavior unchanged).
2. **Tool:** `run_background_task(title, prompt, group_id?, follow_up?)`. Same `group_id` across the fan-out; `follow_up` = recombine instruction on the group. Description teaches: one call per independent part, same group_id, put "what to do with all results" in follow_up.
3. **Group-aware join** (replaces today's per-task delivery): `deliver_background_results` becomes group-scoped — find any group where ALL members terminal AND group unconsumed, ATOMICALLY claim it (group-scoped single-flight, transactional — two members finishing at once both ask "am I last?"), build preamble of all members' output-or-error + follow_up, fire ONE delivery turn. Idle-gate stays, group unit.
4. **Policies:** partial-failure (above); per-group timeout sweep; cancel-one → member just becomes cancelled, group delivers rest; NO nesting (exclude `run_background_task` inside background runs / depth-cap — prevents infinite regress).
5. **Eval:** real fan-out request on cloud (Mistral/Opus) AND local (gemma-4); confirm reliable recombine BEFORE declaring done.

## Risks / why this is its own focused effort
- **Atomic group-claim is the correctness core** — "last finisher delivers exactly once" must be a real transaction or you get double/missed delivery under concurrent completions. Build + test with simultaneous finishes first.
- **Local-model reliability UNPROVEN.** The prior decompose-recombine attempt was abandoned on local models ([[project_guided_execution_broken_local]] / [[project_guided_execution]]). Mitigation: keep it to N FLAT `run_background_task` calls + prose follow_up — NO structured plan/DAG the model must emit (that's where guided-execution broke: silent-after-tool, truncated JSON). But this must be MEASURED on local, not assumed.
- Not a bolt-on: schema + tool + concurrency-correct join + 4 policies + eval. Start with schema + the atomic group-join (riskiest), gated behind the eval.
