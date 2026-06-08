"""Cheap per-file GDPR-review state lookup for badges.

Used to badge files in the project ingest tree and right-panel attachments
WITHOUT re-scanning them. The disk file is always the original — anonymisation
is applied in-flight to LLM-bound consumers, never by overwriting the file — so
review state lives in the `data_reviews` DB row keyed by the file's stable
review id (source_kind + path/ref + user). This is a pure read of that row.

State values:
  - "none"        no review recorded for this file
  - "checked"     reviewed, no violations found (clean)
  - "violations"  reviewed, has unresolved violations (not all overruled)
  - "anonymised"  a de-anon mapping is saved → anonymised in-flight to the LLM

Everything here is best-effort and never raises into the caller.
"""

from __future__ import annotations

import json


def review_state(*, source_kind: str, source_ref: str, user_id: str) -> dict:
    """Return the badge state for one file identified by (kind, ref, user).

    Returns `{state, review_id, overrule_count, violation_count,
    anonymised, status}`.
    """
    out = {
        "state": "none",
        "review_id": "",
        "overrule_count": 0,
        "violation_count": 0,
        "anonymised": False,
        "status": "",
    }
    try:
        from engine import doc_review
        from server_lib.db import DataReviewDB
    except Exception:
        return out

    try:
        review_id = doc_review._stable_review_id(source_kind, source_ref, user_id)
        row = DataReviewDB.get(review_id, user_id, admin=True)
    except Exception:
        row = None
    if not row:
        return out

    try:
        violations = json.loads(row.get("violations_json") or "[]")
    except Exception:
        violations = []
    try:
        overrules = json.loads(row.get("overrules_json") or "[]")
    except Exception:
        overrules = []

    out["review_id"] = row.get("review_id", "")
    out["status"] = row.get("status", "")
    out["overrule_count"] = len(overrules)
    out["violation_count"] = len(violations)
    out["anonymised"] = bool(row.get("anon_mapping_id"))

    overruled_ids = {o.get("id") for o in overrules}
    unresolved = [v for v in violations if v.get("id") not in overruled_ids]

    if out["anonymised"]:
        out["state"] = "anonymised"
    elif unresolved:
        out["state"] = "violations"
    else:
        out["state"] = "checked"
    return out


def review_states(items, user_id: str) -> dict:
    """Batch lookup. `items` = iterable of (source_kind, source_ref). Returns
    `{source_ref: state_dict}`. Skips dupes."""
    out: dict[str, dict] = {}
    for kind, ref in items or []:
        if ref in out:
            continue
        out[ref] = review_state(source_kind=kind, source_ref=ref,
                                 user_id=user_id)
    return out


__all__ = ["review_state", "review_states"]
