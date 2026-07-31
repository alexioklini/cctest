---
name: Citation validator Phase 1 — server-side validation hook (mechanically working, no behavior change)
description: 2026-05-01 — `engine.validate_citations_in_response` shipped + wired into server.py post-reply pipeline; records metadata only, does NOT modify reply text; mistral run-to-run variance ±0.09 mean / ±0.38 max means small experiment deltas are unreliable
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
## What was built

**`claude_cli.py:validate_citations_in_response(text, session_id=None)`** — scans an assistant reply for `[Quelle: <basename> — "<verbatim quote>"]` brackets, verifies each quote against the actual source files the session has read (lazy file read, lenient quote normalization), counts uncited bullets/claims. Returns:
```python
{
  "verified": int,        # brackets where quote was found in source
  "unverified": [(basename, quote_excerpt, reason), ...],
  "uncited_claims": int,  # bulleted/sentence claims with no bracket
  "claim_total": int,     # total bullets + claim-like sentences detected
  "total_brackets": int,
  "annotation": str | None,  # markdown block (currently UNUSED — see "the regression mistake")
}
```

**`server.py` integration** — after `reply` is fully assembled in `_handle_chat`, validator runs synchronously when project context is active. Results stored in `msg_metadata["citation_validation"]` along with up to 5 unverified samples. Does **NOT** modify `reply` text (see below).

**Helper `_read_doc_cache_session_paths(sid)`** — enumerates the absolute paths the session has read via `read_document` / `read_file`. Used by the validator to know which files to grep verbatim quotes against.

## The regression mistake (and revert)

Phase 1.0 originally appended the markdown annotation to the reply text. Mistral self-judge then read the annotation as Brain confessing to bad citations and double-penalized — brain mean dropped from ~0.75 (no-proactive baseline) to 0.65. **Reverted to metadata-only** in Phase 1.1: validator results live in `msg_metadata["citation_validation"]`, NOT in the visible reply.

UI rendering of the citation_validation metadata is deferred — could be a sidebar panel showing "5/7 quotes verified, 2 bullets uncited" per assistant turn. Not blocking the eval work.

## Mistral run-to-run variance (measured)

Critical finding: TWO consecutive eval runs with identical config produced these per-question Brain score differences:

```
mean |Δ| run-to-run:   0.087
max  |Δ| run-to-run:   0.380
stdev:                 0.114
```

So a single eval run's mean differs from the "true" mean by ~0.05–0.10. The 0.75 → 0.65 → 0.67 sequence we saw across no-proactive → validator-text-injection → validator-metadata-only is mostly noise. **Reliable signal threshold for Mistral-judged eval comparisons: a config change must move brain mean by >0.10 to be distinguishable from noise.**

This means the apparent "+0.10 win" of the no-proactive change might also be partly noise. The user-validated finding (`project_eval_q5_user_findings`) is more reliable than the eval mean — the no-proactive change DID materially reduce fabrication on F2/F3, even if the mean delta was inflated by lucky variance.

## Where this leaves the citation problem

**Phase 1 conclusion:** the diagnostic metric exists and works, but does NOT change Brain's behavior. Citation discipline failures on P2/M2/C2/C3 remain stubbornly consistent across all 3 Mistral Medium 3.5 runs. This matches `project_eval_citation_v4_negative` — prompt-only edits cannot fix the citation discipline.

**Phase 2 candidates:**

1. **UI rendering of validation metadata** — small UX win, lets users see citation quality at a glance even when Brain doesn't cite well. Doesn't fix the model.

2. **Re-round on validation failure (the original Phase 2 plan).** When validator finds N>0 unverified quotes or M>0 uncited claims, the agentic loop fires one more user-message round with the validator's feedback embedded ("You wrote 8 bullet points but only 2 carry [Quelle: ... — '...'] brackets. Re-issue your last answer with per-claim citations, or delete the unciteable claims."). Risk: doubles inference cost on every project chat where Brain doesn't cite well. Could be gated by "only when uncited_claims > 4 and we have not yet retried this turn".

3. **Prefill / output-format constraint.** Hardest engineering, riskiest behavior change. Defer.

4. **Different model.** Try Mistral Large or Magistral on the same eval to see if citation discipline is model-dependent. One eval run, easy to do, gives us a data point on whether prompt+validator work could be moot if a bigger model gets it right out of the box.

## Code locations

- `claude_cli.py:_read_doc_cache_session_paths()` — new helper after `_read_doc_cache_invalidate`
- `claude_cli.py:validate_citations_in_response()` — main validator function with citation regex, normalize, validation logic
- `claude_cli.py:_CITATION_BRACKET_RE` — regex for `[Quelle: X — "Y"]`
- `claude_cli.py:_CITATION_BARE_RE` — regex for `[Quelle: X]` without the verbatim-quote part (used to count bare-only citations)
- `server.py:_handle_chat` after `reply` assembled, before `session.add_message` — validator hook recording `msg_metadata["citation_validation"]`

## Run-IDs

- 20260501T120747 — Phase 1.0 (annotation appended to text) — brain 0.65
- 20260501T121612 — Phase 1.1 (metadata only) — brain 0.66
- 20260501T122545 — Phase 1.1 rerun (variance check) — brain 0.67
