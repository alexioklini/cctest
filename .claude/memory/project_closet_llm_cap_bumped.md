---
name: MemPalace closet_llm MAX_CONTENT_CHARS bumped 30K→80K (Brain-side patch)
description: 2026-04-29 — patched the venv'd closet_llm.py cap so LLM closets cover the full ISMS Handbuch + biggest 64K source; will be clobbered on mempalace pip upgrade
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
On 2026-04-29 the user re-enabled `regenerate_closets: true` with `mistral-experimental/mistral-small-2603` as the extraction model. The 30K char cap in MemPalace's `closet_llm.py:_call_llm` was the documented reason yesterday's LLM-closet experiment was reverted (back-of-document content in the 41K ISMS Handbuch had no closet representation).

**Patch**: `/Users/alexander/.mempalace/venv/lib/python3.14/site-packages/mempalace/closet_llm.py:57` `MAX_CONTENT_CHARS = 30000` → `80000`. Comment explains the rationale.

**Corpus context** (kg-real-policies, 58 source files):
- Biggest: 64K (`4_8a_PB_CHECK24.pdf.md`)
- 4 files >40K (Datenschutzhandbuch 44K, Löschkonzept 43K, ISMS Handbuch 41K, InfoSec Org 38K)
- Most others <30K

80K covers the biggest file with ~25% headroom; Mistral Small / Magistral both have 128K context windows so they handle this fine.

**How to apply**:

- The patch lives in the venv site-packages — **will be lost on `pip install --upgrade mempalace`** or any venv recreate. After every upgrade, re-apply the same one-line change.
- The closet regen cursor (`closet_regen_progress` in chats.db) gates wing-wide regen on per-source (mtime, size) change. To force a regen with the new cap, either: (a) delete cursor rows for the wing, OR (b) clear closets directly via Chroma `where={"wing": ...}` — both done on this run.
- Server restart needed after editing the venv module (closet_llm is imported at startup; cap is module-level constant).
- 58 sources × 1 LLM call × up to 80K input chars = ~10-20min runtime + cost via Mistral API. To dry-run cheaper, lower the cap to e.g. 50K (covers only the 4 problem files; small docs use less anyway since the cap is a max).

**To kick a fresh KG-only or KG+closet regen** (after this patch is in place):

```
# 1. Enable KG (model + regen flag stay set in config)
POST /v1/mempalace/kg/config {"enabled": true}

# 2. Reextract = purge old triples + cursor + queue sync
POST /v1/mempalace/kg/reextract {"agent_id": "main", "project": "kg-real-policies"}
```

The next project-sync cycle (kicked immediately by the reextract endpoint) will:
1. Re-extract triples per chunk via Mistral Small 2603
2. End-of-cycle: regenerate closets via the same model with the 80K cap
3. Update `kg_extraction_log` rows in chats.db (id, source_prefix, drawers_processed, triples_extracted)

**Tradeoff with the open-then-close pattern (KG → test → KG → test)**: the user is iterating, so it's fine to leave `regenerate_closets: true` baseline-on now that the cap is fixed. If the IT-Risk Score query still hallucinates after the next run completes, the regression is something other than closet truncation (we ruled that out by inspection: section 2.13 sits at char 5402 in the 41K ISMS Handbuch, well within even the old 30K cap — the failure mode was different from what the cap-bump fixes).

**Upstream upstream**: this should land in MemPalace itself as a config option, not a magic constant. Not Brain's repo to own, but the patch comment hints at the rationale so future-Alex doesn't re-litigate.
