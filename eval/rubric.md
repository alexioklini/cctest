# Eval Rubric — Brain vs Opus on KG-Real-Policies

You are scoring two answers (`gold` from Claude Code+Opus 4.7+vanilla MemPalace, `brain` from Brain-as-deployed) against the SAME question over the SAME corpus (58 German bank IT-policy PDFs). Score each axis on **0.0–1.0** to one decimal. Be calibrated, not generous.

Output JSON ONLY, no prose around it. Schema:

```json
{
  "gold": {
    "retrieval": 0.0,
    "precision": 0.0,
    "citation": 0.0,
    "refusal": 0.0,
    "composition": 0.0,
    "total": 0.0,
    "notes": ""
  },
  "brain": { "retrieval": 0.0, "precision": 0.0, "citation": 0.0, "refusal": 0.0, "composition": 0.0, "total": 0.0, "notes": "" },
  "comparison": {
    "winner": "gold|brain|tie",
    "delta_total": 0.0,
    "summary": ""
  }
}
```

`total` = mean of the four applicable axes (NOT all five — see below). Round to 0.01.

## Axis definitions

### retrieval (0–1)
Did the answer cite documents that actually contain the answer?
- 1.0: answer references the right document(s) (filename present in the citation, matches the question's `expected_docs` list provided to you)
- 0.5: references a plausibly-related but suboptimal document
- 0.0: cites no document, or cites an irrelevant one
- For **refusal questions** (`expected_refuse: true`): score retrieval = 1.0 if the answer correctly reports nothing was found, 0.0 if it cites a document anyway (false retrieval)

### precision (0–1)
Are concrete values (intervals, thresholds, lengths, counts, deadlines) backed by the source, or invented?
- 1.0: every concrete value is either quoted from a source OR explicitly marked "nicht spezifiziert" / "nicht im Dokument"
- 0.5: one or two values look ISO-typical without source backing (e.g. "alle 12 Monate", "mindestens 8 Zeichen") but most claims are sourced
- 0.0: multiple fabricated specifics that read like ISO-27001 or HGB defaults rather than the actual document
- For **refusal questions**: score precision = 1.0 (no values to fabricate); use refusal axis instead

### citation (0–1)
Does each factual claim carry its own verbatim quote in the bracket form `[Quelle: <basename> — "<wörtliches Zitat 10–25 Wörter>"]`?
- 1.0: every claim or bullet has its own bracket with a verbatim quote, basename only (no .md suffix), no fabricated `§N`
- 0.5: SOME claims cited, OR citations present but quotes are paraphrased rather than verbatim, OR one trailing citation covers multiple bullets
- 0.0: no citations, or only filenames without quotes, or fabricated paragraph numbers (`§154`, `§3.2`)
- For **refusal questions**: score citation = 1.0 (refusals don't need citations); use refusal axis

### refusal (0–1) — ONLY scored for questions where `expected_refuse: true`
- 1.0: clean refusal explicitly stating the topic is not in the project corpus, suggests adding the document or consulting another source. NO general-knowledge filler about the topic.
- 0.5: hedged refusal that still slips in some general knowledge about the topic
- 0.0: substantive answer fabricated from training data, no refusal at all

### composition (0–1)
Readability, structure, and information density. Treat this as an aesthetic / usability axis.
- 1.0: well-organised, terse where possible, bullets where they help, paragraphs where they help, no padding
- 0.5: readable but either over-compressed (key info missing) or over-padded (filler sentences)
- 0.0: hard to read, wall of text, or so terse the user can't act on it

## Total

For non-refusal questions: `total = mean(retrieval, precision, citation, composition)`
For refusal questions: `total = mean(retrieval, refusal, composition)` — precision and citation are forced to 1.0 and excluded from the mean.

## Comparison

`winner`: which answer scored higher on `total`. Use "tie" if `|delta_total| < 0.05`.
`delta_total`: `brain.total - gold.total` (positive = Brain won).
`summary`: one or two sentences naming the dominant axis where the gap appeared. No padding.

## Calibration anchors (apply ruthlessly)

- A confident answer with NO citations is **0.0 on citation**, regardless of how correct it sounds.
- An answer that invents a paragraph number (`§154`, `§3.2`) is **at most 0.3 on citation** even if a quote follows.
- An answer that says "regelmäßig", "häufig", "mindestens jährlich" without a quote on the very next characters is **at most 0.5 on precision**.
- An answer to a refusal question that mentions GwG/AMLD/working-hours-law specifics from training data scores **0.0 on refusal** even if it ALSO says "consult the documents".
- Verbatim quotes that appear on Cmd+F-able pages of real documents are the gold standard for citation.
