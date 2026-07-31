---
name: Brain reaches 5.5/7 on IT-Risk Score canary with Mistral Small 4 — gap to Opus 4.7 is composition, not retrieval
description: 2026-04-29 — session 6e70de0e closed all infrastructure bugs; remaining gap to vanilla Claude-Code-+-Opus is model-behavior (Mistral Small compresses bullet lists; Opus reproduces verbatim)
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
End-of-day status after the full debugging arc:

**Score on the IT-Risk Score canary** (`project_it_risk_score_canary_answer.md` 7-point checklist):
- Vanilla MemPalace + Claude Code (Opus 4.7): **7/7**
- Brain at debug-start (ba3b33b8, c4027c7e, etc.): **0/7** (full hallucination)
- Brain after retrieval+read fixes (87bf6124): **5/7** (table + citation missing)
- Brain after retrieval+read+room+truncation fixes (6e70de0e, **current**): **5.5/7** with Mistral Small 4

**Where the remaining 1.5 points are**:

1. **CIA Faktor 1-4 weighting omitted** — source has it as a bullet under "Kontroll-Score = gewichteter Durchschnitt"; Mistral Small mentioned CIA categorically but dropped the 1-4 weighting numbers.
2. **Sofortmaßnahmen → 50% blockade omitted** — source lists three blockade thresholds (50/80/90); Mistral kept 80% and 90% but dropped 50%.
3. **Score-Arten taxonomy partially reproduced** — Mistral merged "Score je Risikokategorie" into the Asset-Kategorie level and dropped "Kontroll-Score" as a distinct level. Source has them as 5 distinct Score-Arten; Mistral's answer has 3 in the table + Gesamt = 4.

All three are MISSING info, not WRONG info — model compressed bullet lists rather than fabricating. Confirmed by reading the actual source: every detail Mistral DID reproduce is verbatim correct.

**Diagnosis**: This is a model-behavior gap, not an infrastructure gap. `read_document` now returns the full 1903-line .md including section 2.13 with all schwellen, weights, table, and the model has the source. The gap is composition/summarisation tendency.

**How to apply**:

If 5.5/7 is acceptable for the workflow (Mistral Small ~1/3 latency vs Opus, ~$0 via Mistral subscription) — leave it. The infrastructure is correct.

If 7/7 is needed:
- **Switch the chat model to Opus** (anthropic API key required) for policy-question sessions only. Brain's per-session model selector handles this.
- **Try Magistral** (Mistral's reasoning variant) — its anti-summarisation tendency may match the corpus better than Mistral Small.
- **Strengthen the system prompt** with an explicit "reproduce ALL bullets and ALL numbered values from the source verbatim — do NOT summarise enumerations" rule. Helps weaker models that default to compression.

**What was actually fixed during this debugging arc** (in dependency order, all 2026-04-29):
1. `bug_summarise_tool_result_unpack.md` — `_summarise_tool_result` 2-vs-3 tuple crash silently killed every read_document call >65KB; masked as hallucination
2. `project_drawer_path_resolution_fix.md` — drawers now carry `read_path` / `read_path_original` so the model gets ready-to-use absolute paths instead of guessing from basenames
3. `project_kg_disabled_markitdown_swap.md` — KG off (was poisoning answers via low-density triples), markitdown for PDF→md (better text fidelity than fitz)
4. `project_chroma_direct_search_fix.md` — **THE ROOT CAUSE** — `tool_mempalace_query` swapped from `search_memories()` (closet-boost-broken on multi-chunk title-repeating sources) to direct `col.query()` like vanilla CLI does
5. `project_read_document_truncation_fix.md` — `read_document`'s plain-text fallback was capping at 500 lines; added to `execution.profiles` as `heavy:False` so >8KB output stops getting wrapped in summary subagent
6. `feedback_room_filter_misuse.md` — model invented `room='document'` filter from schema example list, got 0 drawers, hallucinated; tightened tool schema + system prompt to refuse speculative filters

**The real lesson**: when a high-level wrapper (`search_memories()`, worker auto-isolation, summariser) magic'd up "helpful" behavior, it broke. The vanilla CLI worked because it took the simplest path: direct Chroma query, return drawers, let the caller handle them. Brain spent multiple iterations adding fixes ON TOP of the broken wrappers before realising the wrappers themselves were the problem. Lesson: **try the lowest-level primitive first when a high-level helper produces wrong results**.

**Performance**: net positive on every fix. `col.query()` is one Chroma read vs `search_memories()`'s 5+. `read_document` no longer goes through the summariser subagent for normal-size files. Tool result middleware compacts downstream as needed. The pipeline is simpler AND faster than at start of day.
