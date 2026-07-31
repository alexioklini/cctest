---
name: reround-uncited-only
description: Citation re-round fires only on uncited_claims now — unverified-quote trigger removed (false positives from PDF→markdown drift wasted ~20s per turn)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 6a44543c-73d9-47db-bace-21bca96ec48d
---

After-turn delay in project chats (10-20s of "still working" after reply
rendered) traced to `citation_reround_needed` firing on the
unverified-quote trigger. Validator does byte-exact substring match
against the source file; PDF→markdown extraction routinely breaks that
(whitespace, soft hyphens, smart quotes, OCR drift) so quotes the model
actually pulled from `read_document` output show up as "not found in
source." False-positive rate was high enough that re-round fired 11/15
on the eval canary and 73% on real chats — most of those produced an
equivalent reply, just rephrased.

Disabled the unverified-quote trigger in `engine.citation_reround_needed`
(brain.py:23181); re-round now fires only when `uncited_claims /
claim_total > 0.30` (bullets with no `[Quelle: …]` bracket at all).
`unverified_threshold` kept in the signature for back-compat but unused.

**Why:** user observed pin transformation worked correctly on the
finished reply (frontend regex doesn't verify quotes, just matches
bracket shape), then watched a 20s delay while the backend validator —
which DOES verify — fired re-round on "11/11 unverified." Both reply
and re-round had the same content with the same citations; the second
call was wasted. User confirmed: "there werent not much uncited claims
— the end result is more or less the same."

**How to apply:** if the unverified-quote signal turns out to be useful
later, gate it behind a whitespace-normalized substring match (collapse
whitespace + normalize dashes + normalize smart-quotes on both sides
before `in` test), or upgrade to fuzzy match (token-set ratio ≥ 0.85)
before re-enabling. Don't restore the strict byte-exact trigger without
that. See [[project_eval_citation_reround_phase2.md]] for the original
phase-2 design and the false-positive on R2 Morgencheck that already
hinted at this.
