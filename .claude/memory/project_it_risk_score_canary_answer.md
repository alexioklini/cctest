---
name: IT-Risk Score canary — gold-standard answer from vanilla MemPalace + Claude Code
description: 2026-04-29 — full ground-truth answer Claude Code produced from the markdown alone (no KG); use as comparison target when validating Brain's retrieval pipeline on kg-real-policies
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
The user ran "wie wird der it-risk score berechnet?" against the same kg-real-policies corpus through vanilla MemPalace + Claude Code (no KG, just `mempalace search` → `read` of the .md). The answer was correct, complete, and properly cited. This is the bar Brain's pipeline has to clear.

**Source**: `docs/20 Datenschutz & Informationssicherheit/20_2 Informationssicherheit/20_2_1_2_ARL_ISMS Risikomanagement Handbuch.md:1262` (= Section 2.13 in the PDF, char ~5400 in the markitdown-converted .md, line 1262 of the markdown). Section 2.13 + first paragraph of Section 3 (Risikoappetit).

**Required content** for a passing answer:
1. **Score-Arten** (5 types, all): Gesamt-Score, Score je Risikokategorie, Kontroll-Score, Asset-Gesamtscore, Asset-Kategorie-Score
2. **Kontroll-Score Gewichtung**: CIA-Rating Faktor 1-4
3. **Blockade-Schwellen**: Sofortmaßnahmen → 50%, Maßnahmen → 80%, Kleinmaßnahmen → 90%
4. **Richtlinien-Abzüge**: −10% (keine Richtlinie), −5% (abgelaufen)
5. **Prozent-zu-Score-Tabelle (11 Stufen)**: =100% → 1.0, ≥90% → 1.2, ≥80% → 1.5, ≥70% → 2.0, ≥60% → 2.5, ≥50% → 2.8, ≥40% → 3.0, ≥30% → 3.2, ≥20% → 3.5, ≥10% → 3.8, =0% → 4.0
6. **Risikoappetit**: Schwelle 60% = Score 2.5, Delta zur Bewertung = Gefährdungslage
7. **Saubere Quellenangabe**: file basename + line/section reference

**Failure modes seen on Brain so far** (sessions ba3b33b8, c4027c7e):
- Hallucinated ISO 27001/31000/DORA generic risk-management scaffolding instead of the ITRMP-specific 5-Score-Arten taxonomy
- No Prozent→Score-Tabelle (the most concrete, hardest-to-fake part of the answer)
- No CIA-Rating-Faktor mention
- Generic "Eintrittswahrscheinlichkeit × Schadenshöhe" framing — the actual document doesn't compute risk that way at all
- Citation either missing or wrong file

**What this proves**:
- The `.md` (markitdown-converted) contains all needed info — verified by direct `read_document` returning section 2.13 verbatim with all schwellen, weights, table.
- The retrieval substrate (Chroma + drawer search) ranks the right document — `mempalace_query("IT-Risk Score Berechnung")` returns ISMS Handbuch as drawer 0.
- The KG is **NOT load-bearing** for this question — Claude Code answered correctly without any KG. Whatever Brain's hallucination cause is, fixing it doesn't require KG triples.
- The remaining suspect is whether the model actually consumes `read_document` output before generating an answer, and whether anything truncates section 2.13 mid-flight (the .md has it at line 1262; if Brain trims raw_result before the model sees it, parts of the table could be cut).

**Side note — line numbers**: Claude Code's reader prefixes lines with their number, which is why the citation reads `…md:1262`. Brain's `tool_read_document` does NOT line-number text output; the model has nothing to cite by line. Worth adding for citation precision (cheap: enumerate(splitlines()) before serializing). Out of scope for the current regression hunt but flagged.

**How to apply**:
- After every Brain pipeline change (KG model swap, closet regen, prompt edit, etc.), run the IT-Risk Score canary in a fresh chat session and compare against this 7-point checklist.
- If Brain's answer is missing items 1-5, the failure is retrieval/consumption (the model didn't actually load the section). Items 6-7 missing → narrative consumption gap (model loaded section 2.13 but didn't read into Section 3 / didn't cite). Item 7 alone missing → cosmetic; bump to known-issue list.
- Don't trust "looks reasonable" — the document's framework is non-standard (5 distinct Score-Arten with their own blockade rules and weighting) and is easy to fill in with generic ISO-flavored prose. Always grep the answer for "1.0" "1.2" "1.5" "2.0" "2.5" "2.8" — if the model didn't reproduce the table, it didn't read the document.
