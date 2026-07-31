---
name: KG + closet model-lineage drift after extraction_model swap
description: 2026-04-28 — switched mempalace.kg.extraction_model from gemini-2.5-flash to mistral-vibe-cli-fast; new content uses vibe, existing 3,244 triples stay gemini until re-extracted
type: project
originSessionId: 7486d080-f9df-4a1c-8a53-d9a3c60c884c
---
On 2026-04-28 the project's `mempalace.kg.extraction_model` was flipped from `gemini-2.5-flash` to `mistral-vibe-cli-fast`. `regenerate_closets` was also flipped on. Existing kg-real-policies content (3,244 triples + 58 source files of closets) was extracted by gemini; new/changed content goes to vibe-fast.

**Why:** vibe-fast outperformed gemini on entity-level recall in side-by-side test (specific person names like Joachim Kerschbaumer, exact software lists, ARL cross-references), at 1/3 the latency and ~$0 cost via Mistral subscription. Tradeoff accepted: lineage mixing within the corpus.

**How to apply:**

KG triples — **mixed-lineage, doesn't auto-heal**:
- Existing 3,244 rows stay gemini-extracted; new/edited files go vibe-fast
- `adapter_name="brain-project-kg"` for both — model name is NOT recorded per triple, so you can't filter old vs new without an `extraction_log` join
- Vocabulary drift: vibe-fast and gemini pick different surface forms for the same concept (`Lebensdauer von Zertifikaten ≤ 2 Jahre` vs `Gültigkeit Server-Zertifikate max. 2 Jahre`) — both correct, don't join in `mempalace_kg_query` because subject/object stored verbatim
- Predicate-distribution shift: vibe-fast averaged ~21 topics/file vs gemini's ~15 in the test, slightly more off-vocab leakage (~2%)
- Confidence calibration: each model has its own scale for `confidence`; the `min_confidence: 0.5` cutoff may behave inconsistently across the corpus
- **To clean up**: `POST /v1/mempalace/kg/reextract` (admin-only, audit-logged as `kg_reextract`) purges triples + cursor for the project, queues sync, re-extracts with current model. ~3-4 min wall clock, pennies via Mistral API. Reversible via the `~/.mempalace-backups/pre-closet-regen-20260428-204043/` snapshot

Closets — **REVERTED to regex on 2026-04-28 evening due to truncation regression**:
- LLM closets had a structural problem: `closet_llm._call_llm` caps input at `MAX_CONTENT_CHARS=30000` per source. The kg-real-policies' ISMS Handbuch is 46,754 chars — only the front 64% reached the LLM, so the back-half topics (IT-Risk Score formula on page 23+) had **no closet representation at all**. Regex closets are built per-drawer-batch during `mp_miner.mine()`, so every page gets indexed regardless of total document size.
- Symptom: queries about back-of-document content returned wrong-document closet matches (e.g. "IT-Risk Score Berechnung" landed on `Cybersecurity-Resilienz` because that closet listed `ISMS` as a topic, while the correct ISMS Handbuch had only generic closets like `Risikomanagement;Handbuch;Dienstleistungen`).
- Restored regex closets via in-place rebuild (`/tmp/restore_regex_closets.py` — read drawers, group by source, call upstream `build_closet_lines`, purge, upsert). 58 source files rebuilt. No re-mining needed since drawers are content-hashed and stable.
- Set `regenerate_closets: false`, cleared `closet_regen_progress` cursor for the wing.
- Verified post-revert: Kryptographie query identical to pre-revert (sim 1.034, drawer+closet), IT-Risk Score query now correctly lands on ISMS Handbuch (drawer-only match sim 0.5+ but right document — was wrong document before).

**To re-enable LLM closets safely** (post-presentation):
1. Override `closet_llm.regenerate_closets` (or fork via wrapper) to chunk long sources into 30K windows, call LLM per window, accumulate closet entries
2. OR keep LLM closets *additive* — don't `purge_file_closets` before upsert, so regex closets stay alongside (search merges both)
3. OR leave regex as the baseline and run LLM closets only on documents <30K chars (would catch ~half the corpus)
4. Track per-source-size during regen so we know which files were truncated

Hidden gotcha: `_run_closet_regen_for` in server.py:13361 wraps the wrapper in `contextlib.redirect_stdout(buf)` to suppress upstream's chatty per-file logging. The wrapper's own one-line summary `print()` ALSO goes to stdout, so it's swallowed. **Closet regen runs silently in the daemon log** — you have to verify by checking `closet_regen_progress` row counts in chats.db, not by grep'ing logs. Cosmetic bug, not blocking.
