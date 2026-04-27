# Project Knowledge Graph — Test Plan

A structured walkthrough for shaking down the KG step-1 functionality (v8.20.0)
end-to-end against real-world bank-policy content. Each section has a goal,
the file mix to drop in, the questions to ask the agent, and what a passing
result looks like.

This is a manual test plan: the daemon runs in the background, you drop files
into a project's input folder + use the chat UI to ask questions. Most
sections take 5-15 minutes; the full plan is 1-2 hours.

---

## 0. Pre-flight

Verify the system is in a clean baseline before you start. If something is
already wrong here, stop and fix it — every later test will inherit the
brokenness.

| Check | How | Pass criteria |
|---|---|---|
| Brain server running | `curl -sf http://localhost:8420/v1/health -H "Authorization: Bearer ..."` returns 200 | Server alive |
| oMLX running | `curl -s http://localhost:8000/v1/models -H "Authorization: Bearer ..."` returns model list | Local LLM gateway alive |
| cliproxyapi running | `curl -s http://localhost:8317/v1/models -H "Authorization: Bearer ..."` | Cloud-proxy gateway alive |
| MemPalace package | `/Users/alexander/.mempalace/venv/bin/pip show mempalace` | Version 3.3.3 |
| KG schema migrated | `sqlite3 ~/.mempalace/brain/knowledge_graph.sqlite3 "PRAGMA table_info(triples)"` | 12 columns including `adapter_name`, `source_drawer_id` |
| Brain version | log shows `VERSION = "8.20.0"` | 8.20.0 active |

---

## 1. Smoke test — fresh project + 1 small PDF

**Goal**: prove the full pipeline works on a single trivial document before
you scale up.

**Setup**:

1. Create a new project in the UI: name "KG-test-1", any icon
2. Add an input folder: pick an empty directory you control,
   e.g. `/tmp/kg-test-1/`
3. Drop **1 PDF** in that folder — pick a small policy or spec document
   (1-5 pages, German or English, contains explicit `must`/`shall`
   /`muss`/`ist verpflichtet` style claims)
4. Click **Sync now** in the project panel

**Watch for** (in `~/.brain-agent/server.log`):
- `[project-sync.conv] /tmp/kg-test-1: converted=1 unchanged=0 failed=0`
- `[project-sync.kg] wing=project__<id> prefix=/private/tmp/kg-test-1 seen=N new=M skip=0 triples=K errors=0`

**Then ask the agent**, in a chat scoped to KG-test-1:

| Question | Expected |
|---|---|
| "Welche verpflichtenden Aussagen enthält das Dokument?" / "What does the document require?" | Agent calls `mempalace_kg_search(predicate='requires')`, returns 5+ specific obligations from the PDF, cites the PDF filename in `source_file` |
| "List the main topics — what's this document about?" | Agent calls `mempalace_query` (vector), returns 3-5 drawer snippets, summarises in 3-4 sentences |
| "Open the original PDF and read page 2." | Agent uses `read_document` on the absolute PDF path (NOT the `.brain-extracted/.md`), returns text |

**Pass criteria**:
- Daemon log shows `triples > 0` for the policy PDF
- All 3 agent queries get plausible answers grounded in the PDF
- `mempalace_query` answers cite real source_files
- Agent opens the original `.pdf` (not the `.brain-extracted/.pdf.md`)

**Common failures**:
- `triples=0` → the PDF is mostly tables/scans/non-prose; try a different PDF first
- Agent says "I don't have access to project documents" → not actually in a project chat; check project breadcrumb
- Agent reads `.brain-extracted/...md` instead of original → system prompt nudge missing; restart Brain

---

## 2. Format coverage — one of each

**Goal**: prove every supported binary format actually converts and produces
extractable content.

**Setup**: in the same KG-test-1 project, add a second input folder
`/tmp/kg-format-test/` and drop one of each:

| File | Source idea | What we test |
|---|---|---|
| `policy.pdf` | a real bank/IT policy PDF | PDF text extraction + page anchors |
| `nda.docx` | NDA, employment contract template, or any obligation-heavy DOCX | DOCX heading hierarchy + body |
| `training.pptx` | a presentation deck with bullet-point rules + speaker notes | PPTX slides + speaker notes |
| `retention.xlsx` | a spreadsheet of e.g. document retention periods (Doc type / Years / Legal basis) | XLSX rows-as-data, all rows preserved |
| `escalation.eml` | an email setting an escalation policy or process step | EML headers + body extraction |

(`.msg` is supported but needs `pip install extract-msg` first; skip if not
installed — the converter will log a clear `extract-msg not installed` and
move on.)

**Watch the log**:
- `[project-sync.conv] /tmp/kg-format-test: converted=N unchanged=0 failed=0 seen=N`
- N should equal the number of files dropped

**Verify on disk**:
```bash
ls /tmp/kg-format-test/.brain-extracted/
# Should show: policy.pdf.md, nda.docx.md, training.pptx.md, retention.xlsx.md, escalation.eml.md
```

**Then ask the agent** (project chat):

| Question | Expected source |
|---|---|
| "What's the document retention period for contracts?" | `retention.xlsx` row(s) |
| "Who is the escalation contact for security incidents?" | `escalation.eml` body |
| "What does the NDA forbid?" | `nda.docx` |
| "What's covered in the training slides?" | `training.pptx` (incl. speaker notes if questions require detail) |
| "Show me all the requires triples about retention" | `mempalace_kg_search(predicate=requires, subject_contains=retention)` returns table-derived + policy-derived triples |

**Pass criteria**:
- All 5 (or 4) `.brain-extracted/*.md` files exist
- Each format produces drawers (`mempalace_query` finds them)
- Each format produces triples for normative content (`xlsx` rows often
  yield table-derived triples)
- Speaker notes from PPTX appear when asked detail questions

**Idempotency check**: hit "Sync now" again. Log should show
`[project-sync.conv] ... converted=0 unchanged=N` — no re-conversion of
unchanged files.

---

## 3. Bulk ingestion — 50+ documents

**Goal**: validate steady-state behavior at non-trivial volume. Catches
performance/memory issues before you go to 400.

**Setup**: gather 50-100 mixed-format documents (real policies if you
have them, or assemble from public sources: GDPR text, BaFin guidances,
ISO snippets, internal SOPs you can share). Drop them in
`/tmp/kg-bulk-test/`. Add as input folder.

**Trigger**: Sync now. Then **walk away** for ~30-60 minutes — gemini-flash
processes 1 PDF in ~10-15 minutes for an average-sized policy doc.

**Watch for**:

```
[project-sync.conv] /tmp/kg-bulk-test: converted=N
[project-sync.kg] wing=... seen=X new=Y triples=Z errors=E elapsed=Ts
[project-sync.cycle] filed=N projects=1
```

**Pass criteria after one full cycle**:
- `triples > 5 * file_count` (i.e. ~250+ triples for 50 files)
- `errors / chunks_processed < 5%`
- Median triples-per-file in the 5-15 range (predicate distribution sane)
- Memory chip in the project panel shows `Memory: N indexed · M triples`
- Settings → Knowledge Graph tab shows the project at the top with sane numbers

**Then ask** (these are the queries that should actually become useful):

| Question | What it's stress-testing |
|---|---|
| "Show me every retention period mentioned across all documents" | `mempalace_kg_search(predicate='retention_period')` (or similar) — does the controlled vocab generalise across documents? |
| "Which laws are most cited in our policies?" | `mempalace_kg_search(predicate='cites')` — citation graph |
| "Are there contradictions about data retention?" | Compares `requires` triples about retention across documents — needs the agent to reason across results |
| "Which documents define the term 'Mitarbeiter' / 'employee'?" | `mempalace_kg_search(predicate='defines', object_contains='Mitarbeiter')` |
| "What does our process say about [specific topic from one of your real docs]?" | Vector retrieval + citation back to the actual doc |

**Pass criteria for queries**:
- Each answer cites at least one real `source_file`
- Answers are specific (quote actual phrases) not vague summaries
- The contradiction query surfaces 2+ documents (or honestly says "no contradictions found in the indexed content")
- Citations point to the original binary, agent reads it via `read_document`
  when the question demands fidelity

---

## 4. Change-detection / invalidation

**Goal**: prove that editing a source file invalidates old KG triples and
re-extracts.

**Setup**: take one PDF from the bulk test and **modify it**:
- Either edit the source DOCX and re-save, or
- Replace the PDF with a slightly different version (same filename), or
- Touch the file mtime: `touch /tmp/kg-bulk-test/some-file.pdf`

**Wait for the next daemon cycle** (or click Sync now).

**Watch for**:
```
[kg-extract] invalidated source <filepath>: triples=X progress=Y
[project-sync.kg] wing=... new=N triples=K
```

**Verify with SQL**:
```bash
sqlite3 ~/.mempalace/brain/knowledge_graph.sqlite3 "
  SELECT COUNT(*) FROM triples WHERE source_file = '/abs/path/to/edited.pdf'
"
```
Should reflect the new content (the count may be similar but the triples
themselves are freshly extracted).

**Pass criteria**:
- Log shows the invalidation line
- Count of triples for the edited file is non-zero
- New cursor row in `kg_extraction_source_state` has the new (mtime, size)

**Edge case**: delete the file outright. Wait for next cycle. The drawers
should sweep stale (`doc_convert.sweep_stale`) and the KG should... well,
the daemon doesn't currently purge KG triples for vanished files (only
for changed ones). That's a gap — see `feedback_kg_invalidation_gaps.md`
in the backlog. Document the behavior; don't expect cleanup.

---

## 5. Idempotency — re-run with no changes

**Goal**: prove the daemon doesn't waste LLM calls on unchanged content.

**Setup**: in the bulk-test project, **don't change anything**. Click
Sync now twice in a row.

**Watch for** (second cycle):
```
[project-sync.conv] /tmp/kg-bulk-test: converted=0 unchanged=N
[project-sync.kg] wing=... seen=X new=0 skip=N triples=0 errors=0 elapsed=<5s
```

**Pass criteria**:
- Second cycle completes in <30 seconds (vs. minutes for first)
- `new=0` and `triples=0` (everything was already cursor-skipped)
- `unchanged=N` matches file count

If you flipped `regenerate_closets: true` after first cycle:
- Third cycle without changes: `[project-sync.closet] sources_seen=N stale_pre_call=0 ... ok` — short-circuits, no upstream LLM call

---

## 6. Multi-project isolation

**Goal**: prove triples from project A don't leak into queries scoped to
project B.

**Setup**:
1. Create project KG-test-2, add input folder `/tmp/kg-test-2/` with
   1-2 different documents
2. Wait for sync to complete
3. Open a chat in project KG-test-2
4. Ask: "What does project KG-test-1's PDF say about <topic only in
   that PDF>?"

**Pass criteria**:
- Agent's `mempalace_query` returns no hits from KG-test-1's documents
- Agent's `mempalace_kg_query/_search` refuses or returns empty results
  for entities only present in KG-test-1
- Manual SQL check confirms: when the agent runs in KG-test-2 context,
  triples from KG-test-1's `source_file` prefixes are not returned

**Negative test**: open a chat OUTSIDE any project (just `agent=main`,
no project breadcrumb). Ask the same project-knowledge questions. Agent
should refuse with "this tool requires a project context" or fall back
to general knowledge with no project citations.

---

## 7. Predicate vocabulary stress test

**Goal**: see how often the LLM invents off-vocab predicates vs sticks to
the controlled set.

**Setup**: use an existing project with extracted triples (KG-test-1
or bulk-test).

**Direct SQL**:
```bash
sqlite3 ~/.mempalace/brain/knowledge_graph.sqlite3 "
  SELECT predicate, COUNT(*) AS n
    FROM triples
   WHERE adapter_name = 'brain-project-kg'
GROUP BY predicate
ORDER BY n DESC
"
```

**Pass criteria**:
- Top 12 predicates dominated by the controlled vocabulary:
  `requires`, `forbids`, `permits`, `defines`, `cites`, `applies_to`,
  `effective_from`, `supersedes`, `responsible_party`, `condition`,
  `exception`, `penalty`
- Off-vocab predicates (e.g. `requires_pre_coding`, `is_intended_for`)
  are <5% of total triples
- No completely garbage predicates (random verbs, German verbs, gibberish)

**If off-vocab leakage exceeds 10%**: prompt-tuning is needed. Tighten
the "invent a predicate when none fits" clause in `kg_extract.py`'s
`NORMATIVE_PROMPT` — push harder for the controlled set.

---

## 8. Citation accuracy spot-check

**Goal**: prove the triples accurately reflect the source documents.

**Setup**: pull 10 random triples from the KG.

```bash
sqlite3 ~/.mempalace/brain/knowledge_graph.sqlite3 "
  SELECT subject, predicate, object, source_file
    FROM triples
   WHERE adapter_name = 'brain-project-kg'
   ORDER BY RANDOM()
   LIMIT 10
"
```

**For each triple**, manually open the cited `source_file` (or
the `.brain-extracted/<name>.md` for the converted version) and check:

| Check | Pass |
|---|---|
| Subject appears in source | Verbatim or near-verbatim |
| Object appears in source | Verbatim or near-verbatim |
| The relation expressed by the predicate is supported by the surrounding sentence | Yes — not hallucinated |
| Source language is preserved | German subject/object stays German; predicate is English |

**Pass criteria**: 9 of 10 triples are defensible from the source text
(small interpretation room is OK; outright fabrication is not). If
2+ are wrong, prompt iteration is needed.

---

## 9. Memory chip + UI feedback

**Goal**: verify the UI surfaces correctly while extraction is in flight.

**Setup**: trigger a fresh sync on a project with new content. Watch
the project panel in real time.

| Element | Expected behavior |
|---|---|
| Memory chip in project header | While syncing: pulses blue / amber. After: shows `Memory: N indexed · M triples` |
| Per-folder pill | While extracting: `KG…` purple-pulse. After: `M triples` purple |
| Per-attachment pill (for files in /ingested/) | Same lifecycle as folder pills |
| Settings → KG sub-tab | Live entity + triple counts; per-project rows show top predicates |
| Click on a project row in KG sub-tab | Opens drilldown modal with predicate-frequency bars, top entities, sample triples, recent extraction-log rows |
| Click "Re-extract everything" (admin only) | Confirms via dialog, purges, queues a re-sync |

**Pass criteria**: every UI element above behaves as listed; no flicker,
no stale numbers persisting after a sync, no errors in browser console.

---

## 10. Cost / token spend sanity

**Goal**: know what each daemon cycle actually costs you.

**Setup**: use the Costs view (Settings → Costs) or query directly.

```bash
sqlite3 ~/Documents/dev/cctest/agents/main/costs.db "
  SELECT model, COUNT(*) AS calls, SUM(tokens_in) AS tin,
         SUM(tokens_out) AS tout, SUM(estimated_cost_usd) AS cost
    FROM cost_log
   WHERE timestamp > strftime('%s', 'now', '-1 day')
GROUP BY model
ORDER BY cost DESC
"
```

**Pass criteria** (soft, calibration-only):
- Default `gemini-2.5-flash`: ~$0.001-0.002 per chunk extraction
- 50 PDFs * ~30 chunks/PDF * $0.0015 ≈ $2.25 for first full cycle
- Subsequent cycles (no changes): $0 — cursor short-circuits
- Edited single file: ~$0.05-0.15 to re-extract one document

If you see costs an order of magnitude higher, something's wrong:
- Closet regen is on and running every cycle (check
  `[project-sync.closet]` log)
- Cursor isn't taking effect (KG re-extracts every cycle — bug)
- Wrong model being picked up (verify Settings → KG)

---

## Reporting template

For each section, capture:

```
Section X — Status: PASS / FAIL / PARTIAL
Date: 2026-04-DD
Model: <extraction model>
Notes:
  - what worked
  - what surprised you
  - any errors observed in log
  - sample queries + the agent's answer (paste)
```

Open issues / observations get filed as memory backlog notes for the
next iteration.
