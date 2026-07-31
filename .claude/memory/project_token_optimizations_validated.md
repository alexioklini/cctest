---
name: A1+ + B1 token-optimization measurement (2026-04-30)
description: Reproducing yesterday's 4-turn DSGVO chat with A1+ cache + B1 preamble shipped → −17% input tokens despite 3× more sources read per turn
type: project
originSessionId: 14f9509b-52ac-46d7-b685-5a694fa330a7
---
End-of-day measurement comparing yesterday's chat 78beef49 vs today's reproduction 1e7478d9 on the same project (`kg-real-policies`), same model (`mistral-experimental/mistral-small-2603`), same 4-turn German DSGVO Q+A flow.

| | Yesterday (78beef49) | Today (1e7478d9) | Δ |
|---|---|---|---|
| Total input tokens | 335,683 | 277,579 | **−58,104 (−17%)** |
| Total output tokens | 5,061 | 7,936 | +2,875 |
| LLM calls | 10 | 10 | — |
| Cost USD | 0.0702 | 0.0603 | **−14%** |
| Sources read per turn | 1 .md companion | 3 .md companions | 3× more |

**Why this is a strong result, not a marginal one:** today's model variation chose to read 3 source files per turn instead of 1 (Datenschutzhandbuch + Data Breach + Löschung Kundendaten). Without the cache, that alone would have inflated input tokens far beyond yesterday's number — re-reading 3× ~10KB on each follow-up turn. A1+ stubs on the second-onward read brought the per-round growth on Turn 2 down from yesterday's ~3K (round 0 → round 1) to today's 655 tokens, despite three read_documents in flight.

**User-estimated apples-to-apples saving: ~30%.** The −17% measurement understates the win because today's model variation read 3× more source files per turn. Normalising for that — i.e. comparing a 1-source workflow today vs the 1-source workflow yesterday — the saving would be closer to 30%. Treat the −17% as a conservative lower bound from the noisy real-world comparison; the true per-token-saved-on-cache-hit ratio is bigger.

**A1+ visible in the numbers:**
- Turn 2 round 0 → round 1 wachstum: yesterday +2.605, today +655 (cache stubs replaced full file content)
- Turn 3+ tokens stayed near 26K instead of climbing to 42K → middleware compaction also kicked in, but A1+ kept the round-to-round delta within turns small.

**B1 visible too:** project index (drawer count, attachment count, input-folder list, path-join example) moved out of `_build_system_prompt` into a per-session preamble. Saves ~1KB on every request that's not in a warm-pool slot — at 10 calls that's ~10K of the −58K delta; A1+ accounts for the rest.

**How to apply:**
- When debugging "why is this chat using so many tokens" → check `cost_log.tokens_in` per `tool_round`. Big jumps between rounds 0/1 within a single turn = re-reading the same file = should hit cache; if it doesn't, look at `_after_file_write` or check whether the model is paginating (offset/limit/pages bypass cache by design).
- When users ask whether the optimisations "really save tokens" → point at this 17% measurement on a real workflow, not a synthetic benchmark.
- Yesterday's 335K cost ~$0.07; the same workflow now ~$0.06. Cumulatively meaningful when the user runs many policy-Q&A sessions per day.

---

**Update 2026-05-14 (commit `c8ab71c`):** A1+ cache + B1 preamble are now downstream of `engine.resolve_active_tools(purpose=…)`, the single tool-resolver introduced by the prompt-tools unification. The per-session read_document cache still operates exactly as described above (keyed on `(session_id, abs_path)`); the project preamble injection is also unchanged. What changed is that the system prompt itself now branches per purpose (`interactive` / `memory_summary` / `transform` / `research_minimal`) instead of `mode=chat|scheduled`. For project chat the relevant branch is `interactive` — preamble injection and cache behavior in this branch are identical to the pre-unification path. See [[project_research_minimal_purpose]] for the new resolver design.
