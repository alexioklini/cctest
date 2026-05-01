# Project disciplines (full inject mode)

This file mirrors the disciplines block from `agents/main/projects/kg-real-policies/project.json` so the Claude Code gold runs can be configured to receive the exact same instructions Brain operates under, when `claude_code_disciplines: "full"` is set in `eval/config.json`. Default is `none` — Opus runs naked.

---

**QUERY DISCIPLINE — keep mempalace_query short and content-bearing**:
Queries are matched by vector similarity. Long, verbose queries with filler tokens drag the embedding toward generic chunks and HIDE the documents that perfectly match on filename or topic. Use 2-4 content-bearing keywords — the actual subject of the question — and drop everything else. Do not write the user's full question into the query.
DROP these filler/generic tokens: 'Regelung', 'Regelungen', 'Policy', 'Richtlinie', 'Vorschrift', 'Verantwortliche', 'durchführen', 'Tätigkeiten', 'Aufgaben', 'Beschreibung', 'Übersicht', 'Definition', 'allgemein', 'bank', 'Unternehmen', 'IT-Policy', 'wie', 'was', 'welche'.
KEEP the rare, specific subject keywords — these are what discriminates documents.
Examples:
  • 'Wie ist die Datensicherung und Archivierung geregelt?' → `Datensicherung Archivierung`
  • 'Welche Tätigkeiten werden im IT-Morgencheck durchgeführt und von wem?' → `IT-Morgencheck`
  • 'Wie werden TAMBAS-Daten gesichert?' → `TAMBAS Sicherung`
If the first short query yields nothing matching, try a different rare keyword pair, NOT a longer version of the same query.

**REFUSAL DISCIPLINE**:
If `mempalace_query` returns 0 relevant drawers (and after you've read the top drawers' source files in full and confirmed they don't contain the information), the project does NOT contain it. You MUST then answer:
  'Diese Information ist im aktuellen Projektwissen nicht enthalten. Bitte fügen Sie das relevante Dokument zum Projekt hinzu oder konsultieren Sie eine andere Quelle.'
Do NOT substitute general knowledge for indexed-document knowledge. For compliance/policy/audit work, an answer that doesn't match an actual document on file is a compliance hazard.
Try at most 2-3 query rephrasings before refusing.

**PRECISION DISCIPLINE — no plausible-sounding filler**:
When the source does not give a concrete value (interval, frequency, threshold, count, deadline, length, duration), write `nicht spezifiziert` — never substitute a plausible default like 'regelmäßig', 'häufig', 'sofort', 'kürzer', 'mindestens X Zeichen', 'alle 12 Monate', 'mindestens jährlich'. If you use any qualifying adverb or comparative ('regelmäßig', 'häufiger', 'kürzer', 'sofort', 'zeitnah', 'angemessen'), the very next characters must be a wörtliches Zitat (`> "..."`) from the read_document output proving the source actually says that. ISO-27001-typical phrasing from training data is NOT a source.

**CITATION DISCIPLINE — per-claim, not per-block**:
EVERY factual sentence and EVERY bullet point that came from the project must carry its OWN [Quelle: <basename> — "<wörtliches Zitat 10-25 Wörter>"] reference right after the claim. One citation at the end of a 5-bullet list is INSUFFICIENT — bullets without an explicit citation are where paraphrase drift slips in. Treat each bullet as an independent claim that must stand on its own with its own quote.
If you cannot find a verbatim quote in the read_document output that supports a specific bullet — DELETE that bullet. A shorter, fully-cited answer is preferable to a longer answer with uncited bullets.
Use the basename only (e.g. `4_0_0_ARL_IKT Strategie.pdf`). **STRIP THE `.md` COMPANION SUFFIX**: when a drawer's `source_file` ends in `.brain-extracted/<name>.<ext>.md`, cite the ORIGINAL binary's name. **DO NOT invent paragraph numbers like `§164` or `§3.2`** — the `.md` companions do NOT preserve original paragraph numbering.
Multiple sources for one claim → repeat the bracket: [Quelle: A.pdf — "..."] [Quelle: B.docx — "..."].
