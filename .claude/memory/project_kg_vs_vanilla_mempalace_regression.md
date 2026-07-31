---
name: KG-augmented retrieval regressed vs vanilla MemPalace on policy corpus
description: 2026-04-29 — vanilla MemPalace (markitdown→mine, no KG) gave correct IT-Risk Score answer; Brain's KG-augmented setup hallucinates on the same query
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
On 2026-04-29 the user A/B-tested two setups against the same 58-PDF policy corpus (kg-real-policies, ARL Datenschutz/InfoSec + IT/Core Banking docs):

**Setup A — vanilla MemPalace, plain Claude Code session:** convert PDFs with Microsoft `markitdown`, `mempalace init`, `mempalace mine`. **No KG, no project-sync daemon, no closet regen, no Brain orchestration.** Asked about IT-Risk Score calculation → "wonderful, very good structured output, correct."

**Setup B — Brain's full stack at v8.21.6:** mp_miner via project-sync daemon, KG extraction with mistral-vibe-cli-fast (716 triples after morning's reextract), regex closets (LLM closets reverted 2026-04-28). Same IT-Risk Score query in Brain chat session `4b1c401a` → "complete wrong and misleading," significant hallucination.

**Why this matters:**
- Same source documents, same retrieval substrate (Chroma), same MemPalace upstream library version. Only Brain's add-ons differ.
- The vanilla-MemPalace baseline IS the ground truth. Anything Brain adds on top is supposed to *help*, not regress.
- Yesterday's LLM-closet revert was framed as "back to working baseline" — but that baseline assumed gemini KG triples + regex closets. We swapped triples to vibe-fast today, which is now the suspected regression source.

**How to apply:**

This is a **strong stop-the-line signal** for the KG-augmented retrieval path. Before adding more features, isolate which Brain-specific element causes the regression. Likely suspects in priority order:

1. **vibe-fast triples polluting `mempalace_query` ranking.** Drawer search may join against KG entity hints; vibe-fast's sparser triples (12/source vs gemini's 56/source) means fewer hint joins, possibly worse ranking on long-tail queries. Test: temporarily disable KG entirely (`mempalace.kg.enabled: false`), re-query, see if it matches vanilla.
2. **Document-conversion difference.** Vanilla used Microsoft `markitdown`; Brain's `doc_convert.py` uses fitz/python-docx/python-pptx/openpyxl. If markitdown produces materially better markdown (table structure, heading hierarchy, OCR quality), the upstream miner gets cleaner input → better drawers → better answers. **This is the cheapest thing to test.** Convert one ground-truth source (the ISMS Handbuch with the Risk Score) with both converters, diff the markdown side-by-side. If markitdown wins decisively, swap `doc_convert.py`'s backend or fork it.
3. **System prompt contamination.** Brain's PROJECT MEMORY block in `_build_system_prompt` injects KG-aware instructions ("call `mempalace_query` BEFORE answering project-knowledge questions") + per-folder file lists + worked example for `read_file`. Vanilla Claude Code has none of this. The model may be over-eager to call tools or biased by the framing. Test: strip the PROJECT MEMORY preamble for a single turn and re-ask.
4. **Per-turn drawer suffix `#turn/<id>` writes** (chat-sync daemon) interfering with retrieval scoping. Vanilla had no chat-sync running.

**Don't do this until isolation is done:**
- Don't re-fire `kg_reextract` with gemini hoping to revert — yesterday's note shows even gemini-extracted KG had query-quality issues (LLM closets had to be reverted anyway). The regression may not be in the KG model choice at all.
- Don't add any new KG features (closet regen, tunneling, code-graph fold-in) — `backlog_retrieval_eval_harness.md` already flags that we're flying blind without an eval harness; this incident is exactly why.

**Reproduction artefacts:**
- Brain chat session: `4b1c401a` (the wrong/hallucinated answer)
- Vanilla MemPalace: ad-hoc Claude Code session, no transcript saved (user-side)
- Source document for canary: `20_2_1_2_ARL_ISMS Risikomanagement Handbuch.pdf` — the IT-Risk Score formula appears on page 23+ of this PDF (per yesterday's note)
- KG state at time of failure: 716 triples vibe-fast, regex closets, 58 source files mined into `wing=project__f201b24ff6a2`

**The retrieval eval harness backlog is now P0**, not "nice to have." Without it, the next config flip flies blind again.
