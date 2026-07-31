---
name: Retrieval evaluation harness for MemPalace closets / KG / drawer changes
description: Measure retrieval quality before/after any change touching closets, drawers, KG, or scoring — caught LLM-closet truncation regression on 2026-04-28 only by the user noticing degraded answers
type: project
originSessionId: 7486d080-f9df-4a1c-8a53-d9a3c60c884c
---
2026-04-28 incident: enabled `mempalace.kg.regenerate_closets` with mistral-vibe-cli-fast. Verified 4 canary queries by hand, declared shipped. User then ran a real chat (session `066e3cc3c7d8`) on the policies project and noticed answers were noticeably worse — model started saying "X is mentioned but not detailed" until pushed, then delivered details. Root cause: `closet_llm._call_llm` caps input at 30K chars per source; the 46K-char ISMS Handbuch lost its back 36% (including the IT-Risk Score formula). The 4 canary queries didn't cover that failure mode. Rolled back to original regex closets via backup restore.

**Why:** any change to closets / drawers / KG / scoring can silently shift retrieval quality. Hand-picked canary queries are insufficient — they don't catch failures localized to specific document regions, document sizes, or query phrasing. We need measurable retrieval quality so future changes ship on evidence not gut-feel.

**How to apply:** when a retrieval-affecting change is on the table (closet builder swap, model swap, chunking changes, scoring tweaks, new index layer), run the harness before+after and compare. Don't ship if any axis regresses materially without an explicit reason. Reserve "verified by hand" for emergency hotfixes only.

**What the harness needs**:

1. **Frozen query set, ~30-50 questions** mixing:
   - Direct topic ("Wer ist verantwortlich für die Kryptographie-Richtlinien?")
   - Back-of-document content (the IT-Risk Score case — mid-document formulas, late-document tables)
   - Cross-document reasoning ("Wie hängen ITRMP und ARL 4.1 zusammen?")
   - Negative cases (questions whose answer ISN'T in the corpus — assert the model says so cleanly)
   - Per language (German + English variants of the same query)
   - Per document size bucket (<5K, 5-15K, 15-30K, 30K+ chars — the truncation cliff is at 30K)

2. **Per-query expected sources** — list of source_files where the answer SHOULD live. Not exact passages — that's brittle. Just "this question's answer is in one of these N source files."

3. **Three measured axes per query**:
   - **Recall@k**: was an expected source in top-k drawer hits? (k=1, 3, 5, 10)
   - **Closet boost**: did the top hit have `matched_via=drawer+closet` (closet contributed) vs `drawer` only?
   - **End-to-end answer quality**: send the full retrieved context + question to a judge LLM, score 0-3 (no answer / partial / detailed / detailed+correct). The IT-Risk Score regression was a quality drop with intact recall — recall@k alone wouldn't have caught it.

4. **Diff format** that highlights regressions. For each query: `before sim=X match=closet  | after sim=Y match=drawer-only` with a delta column. Aggregate: how many queries dropped a closet match? How many lost top-1? How many lost the right doc from top-5?

5. **Run it as a script**, not a UI. `python3 eval/retrieval_eval.py --before snapshot_a --after snapshot_b --report report.md`. Snapshots = MemPalace dir checkpoints (we already have backup tooling). Output = markdown table with deltas, optionally Slack-postable.

**What NOT to build (yet)**:
- Don't make this a daemon or auto-run on every save. Manual gate before risky changes. Frequent runs cost LLM tokens (judge calls).
- Don't tie it to CI yet — first prove the harness gives signal you trust. Adding gates before that produces noise + false reassurance.
- Don't try to measure absolute retrieval quality ("how good is our system?"). Only relative ("did this change make it better or worse?"). Cheaper, more actionable.

**Where to keep query set**: `tests/retrieval_eval/queries.yaml` — version-controlled, language-tagged, with `expected_sources` and `category` (direct / back-of-doc / cross-doc / negative). Treat the file as a living spec; add queries when new failure modes are found in production.

**First pass priority** when this gets built:
1. Capture the 4 queries we used today (they're a starting baseline)
2. Add the IT-Risk Score case (back-of-doc on a 46K-char source) — that's the regression signature we just lost a day to
3. Add 1-2 negative-case queries (model should refuse cleanly)
4. Write the diff runner against backup snapshots
5. Run it against today's `pre-closet-regen` vs `pre-closet-rollback` backups as a sanity check — should clearly show the regression we saw by hand

**Operational rule going forward** (until the harness exists): no closet/drawer/KG/scoring changes without the user running their own real-chat smoke test on the kg-real-policies project before declaring victory. Hand-verified canaries are not enough.
