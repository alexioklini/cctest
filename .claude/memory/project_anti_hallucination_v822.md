---
name: Anti-hallucination v8.22.0 — three-layer prompt discipline + sampling
description: Validated stack for project-chat retrieval (Mistral Small 3): PRECISION + CITATION + REFUSAL + temp 0.2 / top_p 0.85
type: project
originSessionId: 450cfc13-9017-4090-a00b-7b245a71e62a
---
Shipped 2026-04-29 in v8.22.0 after a day of canary runs on the Wiener-Privatbank-Richtlinien corpus.

**The three discipline blocks** (in `_build_system_prompt`'s PROJECT MEMORY scope, project-pinned only):

- **PRECISION DISCIPLINE**: blocks plausible filler ('regelmäßig', 'häufig', 'sofort', 'mindestens X Zeichen', 'alle 12 Monate'). Vague adverbs require an immediately-following `> "..."` quote. Missing concrete values must be written as `nicht spezifiziert`. ISO-27001-typical phrasing from training data is NOT a source.
- **CITATION DISCIPLINE rewritten**: every claim ends with `[Quelle: <basename> — "<verbatim 10-25 word quote>"]`. The quote is mandatory and self-verifiable (Cmd+F in the PDF). `§N` numbering is BANNED — `.md` companions don't preserve original paragraph numbers, so any `§N` was fabricated. Allowed locators: `Page N` (PDF), `Slide N` (PPTX), `Sheet "Name"` (XLSX) only when actually visible.
- **REFUSAL DISCIPLINE** (was already there): canonical German "Diese Information ist nicht enthalten…" sentence; cap rephrasings at 2-3.

**Anti-room-guessing**: `mempalace_query`'s `room` arg description rewritten to enumerate the real rooms (`general`, `artifacts`, `chat`/`chat_summary`/`chat_attachment`, `reference`) and forbid invention. Old description listed speculative example rooms ('document', 'documentation') which the model used as valid vocabulary.

**Validated sampling for Mistral Small 3** (config.json, gitignored): `temperature: 0.2`, `top_p: 0.85`.
- temp=0.7 default → fabricated formulae, paragraph numbers, intervals
- temp=0.2 + top_p=1.0 → less filler, still §-numbers + plausibility-bullets
- temp=0.2 + top_p=0.85 → no formula fabrication, no §-fabrication, paraphrase bullets stay light
- temp=0 → Mistral provider rejected (HTTP 400)
- temp=0.1 → no measurable improvement over 0.2

**Canary validation** (same query "wie werden non-personal-accounts geregelt"):
- session 6ffd0598 (pre-v8.22.0, no disciplines): 4304 chars, multiple fabricated sections, §-numbering invented, "alle 12 Monate", erfundene Verantwortungstabelle
- session a82327b7 (v8.22.0, all disciplines on): 1037 chars, ONE inline blockquote, ONE verbatim citation, three light paraphrase bullets, zero fabrication

**Tradeoff**: less content, every line verifiable. Tradeoff is intentional — user explicitly preferred "weniger Output, dafür korrekt".

**Residual model limitation**: Mistral Small still has composition tendency on under-covered topics (adds "wie üblich in solchen Richtlinien…" paragraphs). Frontier models suppress this natively. For high-stakes reproduction, switch model rather than tighten prompt further — `feedback_prompt_bloat_regression.md` documented prior overcorrection.

**Open future test**: PI Coding Agent + Mistral via MemPalace as an MCP server — minimal system prompt approach to validate whether the disciplines are doing the heavy lifting or whether the retrieval backend alone is enough.
