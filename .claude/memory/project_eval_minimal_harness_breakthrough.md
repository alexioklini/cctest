---
name: Minimal harness breakthrough — Mistral Medium 3.5 cites perfectly with a 1219-char system prompt
description: 2026-05-01 — built eval/harness/run.py (standalone agentic loop, 3 tools, no Brain dependency); on the SAME 3 questions where Brain failed (P2 cite, F1 refusal, C2 cite), Mistral Medium 3.5 produced gold-quality answers with proper per-claim verbatim citations
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
## What was built

`eval/harness/run.py` — minimal standalone agentic loop:
- Single OpenAI-compatible HTTP call per round, no streaming
- 3 tools only: `mempalace_query` (chroma-direct + filename-token boost, no closet, no reranker, no user/team scoping), `read_document` (plain file read, 60K cap), `read_file` (line-range)
- `system_prompt.md` — 1219 chars, no DEFAULT_PROJECT_INSTRUCTIONS, no v8.22.0 disciplines, no 3-step flow lecturing
- Reads `mistral-vibe` provider creds from `<repo>/config.json`
- CLI flags for model / base-url / api-key / temperature / top-p / max-rounds / system-prompt / wing
- Output: full JSON transcript with per-round tool calls + usage

## What it found

Three test questions, Brain previously failed all three:

| q | Brain (medium-3.5) | Harness (medium-3.5) |
|---|---|---|
| **P2 Archivierung** | brain 0.5 — "lacks verbatim quotes, fabricates RPO/RTO and Office365 specifics" | **Perfect.** 3 rounds, 6s, correct file (`ARL_4_4_Archivierung und Datensicherung.pdf`), verbatim quote with exact retention tiers (Tagessicherung 2 Wo, Wochensicherung 5 Wo, Monatssicherung 12-18 Mo, Jahressicherung 7 Jahre). No fabrication. |
| **F1 Geldwäsche** | brain 0.27 — fabricates FM-GwG/AMLD specifics from training data | **Almost perfect.** Clean refusal opener + truthful corpus-adjacent info (goAML, Compliance & Geldwäscherei, FM-GwG § 21 retention) with verbatim citations. Minor footnote mentions `§ 11 FM-GwG (Bargeld ab 10.000 €)` as external recommendation but explicitly marks it "nicht in indexierten Dokumenten". |
| **C2 Passwort/Bildschirmsperre** | brain 0.0 citation — "cites wrong document, fabricates concrete security thresholds" | **Perfect.** 3 rounds, 17s, every section has per-claim verbatim citations from `20_2_2_4_ARL_Arbeitsplatz Richtlinie.pdf`. Format `[Quelle: <basename> — "<verbatim>"]` consistently applied. |

## What this proves

**Mistral Medium 3.5 is fully capable of reliable per-claim citation discipline. It is NOT a model limitation. The failure mode is Brain's prompt complexity actively making the model worse.**

Brain's full system prompt is ~30K chars covering: agent soul, "use tools proactively" framing, 3-step retrieval lecture, KG instructions (currently disabled), binary/.md preference rules, project preamble, project Instructions block (~5K), DEFAULT_PROJECT_INSTRUCTIONS (REFUSAL/PRECISION/CITATION disciplines another ~4K), tools.md global guide, MCP server descriptions, and several pages of context-block boilerplate. All of that is fighting the model's natural inclinations.

The minimal harness has 1219 chars total, basically just: "you have these 3 tools, use them, cite each claim with `[Quelle: basename — "verbatim"]`, refuse if not in corpus".

## Implication

Three structural Brain problems that prompt-only experiments could not fix:
1. Citation discipline (5/15 user-validated failures)
2. F1-style fabrication after correct refusal opener
3. P2-style adjacent-document misciting

All three may be fixable by **drastically simplifying Brain's system prompt** rather than adding more rules to it. The harness validates that Mistral can do what we want; we just need to stop drowning it.

## Next experiments to try with the harness

1. Run all 15 eval canary questions through the harness (no Brain), measure mean against the eval rubric.
2. Compare harness mean vs Brain's no-proactive run — if harness wins materially, the answer is to strip Brain's prompt down toward the harness shape, not to add server-side citation enforcement.
3. Bisect: what is the minimal addition to the harness prompt that breaks citation discipline? Add Brain's blocks one at a time until Mistral starts dropping citations again, identify the specific block that hurts.

## Files

- `eval/harness/run.py` — agentic loop + tools + CLI
- `eval/harness/system_prompt.md` — minimal 1219-char system prompt
- Sample transcripts: `/tmp/harness_p2.json`, `/tmp/harness_f1.json`, `/tmp/harness_c2.json`

## How to use

```bash
python3 eval/harness/run.py \
  --question "..." \
  --output /tmp/run.json
# defaults read mistral-vibe credentials from config.json automatically
```
