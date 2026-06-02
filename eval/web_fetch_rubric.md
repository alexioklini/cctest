# web_fetch optimization eval — judge rubric

You are scoring whether a `web_fetch` **optimization** behaved well, given two
answers to the SAME question:

- **GOLD answer** — written from the COMPLETE page content (optimizations off,
  full body fetched). Treat its facts as ground truth.
- **OPT answer** — written from the OPTIMIZED fetch.

You are NOT judging writing quality. **Read the `mode` field below and apply the
matching rubric — abstract mode is scored differently from the rest.**

---

## Mode = `abstract` → score TRIAGE SUFFICIENCY (NOT completeness)

The optimization's PURPOSE is to summarize a page into a ~1500-char survey so the
caller can judge relevance and answer gist-level questions WITHOUT paying for the
full page. The question here is gist/relevance-level. The right outcome is that
the survey is SUFFICIENT — i.e. the full fetch was unnecessary.

- **sufficiency** (the OPT/survey answer): does it answer the gist-level question
  correctly, matching the GOLD (full-page) answer at the level asked?
  - 1.0 — the survey answer conveys the same gist as the full-page answer; the
    full fetch would have added nothing for this question. Matching the TOPIC and
    PURPOSE is sufficient — do NOT require exact titles, document names, labels,
    or numbers the question didn't explicitly ask for (e.g. if the survey says
    "coding conventions for Python" that is sufficient even if it doesn't say the
    literal string "PEP 8").
  - 0.5 — partially captures the gist; a reader might still want the full page.
  - 0.0 — too thin to support even a relevance decision (e.g. the survey is page
    navigation / menus / a table of contents with no actual prose), OR it
    fabricates a gist not supported by the page.
- For the GOLD answer in abstract mode, score `completeness` = 1.0 if it answers
  the gist correctly (it has the whole page, so it should).

Map to the output object: set `opt.completeness` = sufficiency,
`opt.faithfulness` = 1.0 unless the survey fabricated (then 0.0). `gold.*` as
usual. `opt.total = round(0.5*completeness + 0.5*faithfulness, 2)`.

**Winner / content_loss for abstract:** `winner="tie"` when the survey is
sufficient (full fetch not needed) — THIS IS THE GOOD OUTCOME, the optimization
paid off. `winner="gold"` ONLY when the survey was too thin or wrong for even the
gist-level question. `content_loss=true` ONLY in that gold-wins case — for
abstract, dropping deep detail while still answering the gist is NOT content_loss.

---

## Mode = `academic` | `brain_code` | `conversion` → score COMPLETENESS + FIDELITY

These optimizations must NOT lose answer-critical content vs the complete page.

- **completeness** — does the answer contain the specific facts the question asks
  for? 1.0 all present+correct · 0.5 some missing/vague · 0.0 key facts absent.
- **faithfulness** — are stated facts true (not fabricated)? An answer that
  honestly says "the content does not include X" is FAITHFUL (1.0) even if
  incomplete. Inventing a plausible-but-wrong value is 0.0.

`total = round(0.5*completeness + 0.5*faithfulness, 2)` for each answer.

**Winner / content_loss:** `winner="tie"` when OPT is as complete+faithful as GOLD
(optimization lost nothing). `winner="gold"` when OPT misses facts GOLD has.
`content_loss=true` iff the optimization caused OPT to miss facts GOLD correctly
provided.

---

## Output format (both modes)

Output ONLY this JSON object — no prose, no markdown fences:

```
{
  "gold":  {"completeness": 0.0, "faithfulness": 0.0, "total": 0.0},
  "opt":   {"completeness": 0.0, "faithfulness": 0.0, "total": 0.0},
  "comparison": {"winner": "gold|opt|tie", "content_loss": false, "summary": "…"}
}
```
