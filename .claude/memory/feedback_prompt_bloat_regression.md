---
name: System-prompt bloat regressed Mistral Small from 5.5/7 to ~1.5/7 on the same canary
description: 2026-04-29 — adding a 30-line REPRODUCTION DISCIPLINE block + DRILLDOWN TOOLS section to the project-memory system prompt made the model topic-drift to wrong sections. Trimmed back to a single-sentence rule
type: feedback
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
Today's debugging arc reached a 5.5/7 baseline on the IT-Risk Score canary with Mistral Small 4 (session 6e70de0e). Two follow-up sessions (c71b3258, fa71c05a) with the same model + same corpus + same query but a longer system prompt scored ~1.5/7 — answer composed from sections 2.1-2.12 (Methodik) instead of section 2.13 (Berechnung), with the percent-to-score table, blockade thresholds, and 5 Score-Arten taxonomy all dropped.

**What had changed**: project-memory block grew from ~9-10K chars to 14.3K chars between 6e70de0e and c71b3258. The additions:
- REPRODUCTION DISCIPLINE block (30+ lines with 4 worked examples calling out the canary's exact failure modes)
- DRILLDOWN TOOLS section advertising mempalace_get_drawer + mempalace_list_drawers (~1.6K)

**Why it backfired**: Mistral Small is in the 24B parameter class. Long instruction blocks with worked examples appear to consume "instruction-following budget" that would otherwise have gone to "load and read section 2.13". The model still calls the right tools (mempalace_query → read_document on the right .md), but its output composition shifts toward generic methodology summary instead of the requested specific section. The DRILLDOWN block also seems to invite the model to re-think its retrieval strategy, slowing it down without improving recall.

**The fix**: trimmed REPRODUCTION DISCIPLINE to one sentence ("When the source contains numbered lists, value tables, or threshold sequences relevant to the answer, reproduce every entry — do not summarise enumerations into prose."). Removed the entire DRILLDOWN TOOLS section from the system prompt. Tools still registered in TOOL_DEFINITIONS / dispatch / profiles — the model can use them via schema, but the prompt no longer advertises them.

Block size now ~11.5K, between the 5.5/7 baseline (9-10K) and the regressed bloat (14.3K).

**General lesson — applicable to any prompt update on any model under ~70B**:

A longer system prompt is NOT a stronger system prompt. Each additional rule competes for attention with every other rule and with the actual task. Worked examples can be especially distracting because the model spends capacity matching the example to the current case. Test prompt changes against a known canary BEFORE assuming "more rules = better behavior".

Specifically for Mistral Small 4 + this corpus:
- Single-sentence rules that name the failure mode work. ("Don't pass `room` filter unless verified.")
- Multi-line discipline blocks with worked examples HURT.
- Tool advertising in the prompt didn't help; tools work fine via schema alone.

**To validate**: run the IT-Risk Score canary again with this trimmed prompt. Expect 5.5/7 or better. If still <5/7, revert the rest of the recent prompt additions (room=DO-NOT-GUESS warning, include_chat_history warning) one at a time until 5.5/7 returns.

**Related**:
- `project_brain_canary_55_of_7.md` — original 5.5/7 baseline.
- `project_drilldown_tools_added.md` — context for what was added.
- `feedback_magistral_unsuitable_for_reproduction.md` — Magistral was a bad fit; Mistral Small 4 stays the default.
