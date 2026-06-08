"""Unified per-document GDPR + classification review core.

This is the single analysis + anonymisation brain shared by every surface that
reviews ONE document in detail:

  - the Data view "Prüfen" reviewer (uploaded files),
  - the project ingest-tree right-click reviewer (files on disk, in place),
  - the right-panel attachment reviewer.

It normalises the two existing span-aware scanners — `engine._pii_scan_text`
(GDPR/PII) and `engine.classification.detect_with_pii` — into ONE `violations`
list with character offsets + a plain-language `why` for each, so the front-end
can highlight, navigate, and tooltip them uniformly.

Anonymisation reuses the existing shape-preserving, reversible `pseudonymizer`
(fake-but-realistic values + an encrypted, persistable de-anonymisation map).

Nothing here persists — the handler owns the DB + metadata round-trip. This
module is pure (no request context), so it is safe to call from the chat
worker, a handler thread, or a test.
"""

from __future__ import annotations

import hashlib


# Char budget for a single document review — large enough for real policy
# documents, bounded so a pathological paste can't wedge the scanner. Matches
# the classification handler's per-file text cap (1 MB).
MAX_REVIEW_CHARS = 1 * 1024 * 1024


def content_hash(text: str) -> str:
    """Stable identity for a document's *text* (post-extraction). Used to
    detect "we already reviewed this file" on re-upload / re-open. Hashing the
    extracted text (not the raw bytes) means a docx and its anonymised copy
    differ, while two byte-identical uploads collapse to one review."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:32]


def analyze(text: str, *, filename: str = "", cfg: dict | None = None) -> dict:
    """Run both scanners over `text` and return a unified review.

    Returns:
        {
          "filename": str,
          "char_count": int,
          "content_hash": str,
          "classification": {final_level, marker_level, heuristic_level,
                             mismatch, level_label_de},
          "violations": [
            {id, kind: "pii"|"classification", start, end, label,
             rule_id|level, category, action|severity, why, excerpt}, ...
          ],
          "counts": {pii: int, classification: int, by_action: {...}},
        }

    Violations are sorted by `start` so the UI can render + navigate in reading
    order. Each gets a stable `id` (`"v{n}"`) for overrule addressing.
    """
    from engine import classification as cls
    import brain as _brain

    text = (text or "")[:MAX_REVIEW_CHARS]
    if cfg is None:
        try:
            cfg = _brain._get_gdpr_scanner_config()
        except Exception:
            cfg = {}

    violations: list[dict] = []

    # ── GDPR / PII ────────────────────────────────────────────────────────
    pii_findings: list[dict] = []
    try:
        pii_findings = _brain._pii_scan_text(text, cfg=cfg, max_findings=500)
    except Exception as e:
        print(f"[doc_review] pii scan failed: {e}", flush=True)
    from engine.pii_ner import pii_finding_why
    for f in pii_findings:
        s, e = int(f.get("start", 0)), int(f.get("end", 0))
        if not (0 <= s < e <= len(text)):
            continue
        rid = f.get("rule_id") or "?"
        cat = f.get("category") or "personal"
        violations.append({
            "kind": "pii",
            "start": s,
            "end": e,
            "label": f.get("label") or rid,
            "rule_id": rid,
            "category": cat,
            "action": f.get("action") or "warn",
            "why": pii_finding_why(rid, cat),
            "excerpt": text[s:e],
        })

    # ── Classification ────────────────────────────────────────────────────
    cls_result: dict = {}
    try:
        cls_result = cls.detect_with_pii(text, filename=filename, cfg=cfg)
    except Exception as e:
        print(f"[doc_review] classification failed: {e}", flush=True)
        cls_result = {}

    # Marker evidence carries spans (start/end/excerpt/level/pattern_id).
    for ev in (cls_result.get("marker_evidence") or []):
        s, e = int(ev.get("start", 0)), int(ev.get("end", 0))
        lvl = ev.get("level") or "unmarked"
        if not (0 <= s < e <= len(text)):
            # A filename-only / footer-fallback hit may lack valid spans;
            # skip the inline highlight but it still shapes final_level below.
            continue
        violations.append({
            "kind": "classification",
            "start": s,
            "end": e,
            "label": cls.LEVEL_LABEL_DE.get(lvl, lvl),
            "level": lvl,
            "category": "classification",
            "severity": (cls_result.get("mismatch") or {}).get("severity") or "info",
            "why": cls.level_why(lvl),
            "excerpt": ev.get("excerpt") or text[s:e],
        })

    violations.sort(key=lambda v: (v["start"], v["end"]))
    for i, v in enumerate(violations):
        v["id"] = f"v{i}"

    by_action: dict[str, int] = {}
    for v in violations:
        key = v.get("action") if v["kind"] == "pii" else "classification"
        by_action[key] = by_action.get(key, 0) + 1

    final_level = cls_result.get("final_level") or "unmarked"
    return {
        "filename": filename,
        "char_count": len(text),
        "content_hash": content_hash(text),
        "classification": {
            "final_level": final_level,
            "marker_level": cls_result.get("marker_level"),
            "heuristic_level": (cls_result.get("content_signals") or {}).get(
                "heuristic_level") or "public",
            "mismatch": cls_result.get("mismatch"),
            "level_label_de": cls.LEVEL_LABEL_DE.get(final_level, final_level),
        },
        "violations": violations,
        "counts": {
            "pii": sum(1 for v in violations if v["kind"] == "pii"),
            "classification": sum(1 for v in violations
                                  if v["kind"] == "classification"),
            "by_action": by_action,
        },
    }


def anonymise(text: str, violations: list[dict], *,
              overruled_ids: set | None = None,
              cfg: dict | None = None,
              mapping=None,
              source: str = "data_review") -> tuple[str, object, int]:
    """Pseudonymise the PII violations in `text` (shape-preserving, reversible).

    Only `kind == "pii"` violations are replaced (classification markers are
    NOT anonymised — they describe the document, they are not PII). Violations
    whose id is in `overruled_ids` are kept as-is (the user accepted the risk).

    Returns `(anon_text, mapping, replaced_count)`. The `mapping` is the
    de-anonymisation index — the caller persists it (encrypted) and/or embeds
    it in the downloaded file's metadata so the change is reversible.

    A fresh mapping is created when `mapping is None`. Pass an existing mapping
    to extend it (re-anonymise reusing prior tokens).
    """
    import pseudonymizer as _ps

    overruled_ids = overruled_ids or set()
    if mapping is None:
        mapping = _ps.new_mapping()

    # pseudonymize_text consumes {rule_id, start, end, ...}; feed it only the
    # PII violations the user has NOT overruled.
    findings = [
        {"rule_id": v.get("rule_id") or "?",
         "label": v.get("label") or "",
         "start": int(v["start"]), "end": int(v["end"])}
        for v in violations
        if v.get("kind") == "pii" and v.get("id") not in overruled_ids
    ]
    if not findings:
        return text, mapping, 0

    before = len(mapping.forward)
    anon = _ps.pseudonymize_text(text, findings, mapping=mapping, source=source)
    replaced = len(mapping.forward) - before
    return anon, mapping, replaced


def deanonymise(text: str, mapping) -> tuple[str, int]:
    """Restore original values via the de-anonymisation index. Thin pass-through
    to the pseudonymizer so callers don't import it directly."""
    import pseudonymizer as _ps
    if mapping is None:
        return text, 0
    return _ps.deanonymize_text(text, mapping=mapping)


def _stable_review_id(source_kind: str, source_ref: str, user_id: str) -> str:
    """Deterministic review id for a file identified by (kind, ref, user). A
    re-mine of the same path resolves to the SAME review row so we refresh it in
    place (and keep its overrules) rather than spawning duplicates. Uploads /
    attachments without a stable ref fall back to a content-derived id."""
    import hashlib as _h
    seed = f"{source_kind}\x00{source_ref}\x00{user_id}".encode("utf-8", "replace")
    return "dr_" + _h.sha256(seed).hexdigest()[:20]


def review_file_to_db(path: str, *, user_id: str, source_kind: str,
                      source_ref: str = "", filename: str = "",
                      cfg: dict | None = None, force: bool = False) -> dict | None:
    """Extract, analyze, and persist a review for a file on disk. Returns the
    stored row dict (or None if the file can't be read / has no text).

    Called from TWO places with the SAME identity (`source_ref` = the file's
    real path), so they converge on one `data_reviews` row:
      - the project add-handler, synchronously, for instant badges;
      - the project-sync daemon, on (re-)mine, to refresh after content change.

    Re-mine refresh: the row is keyed by a stable id derived from
    (source_kind, source_ref, user_id). When the content hash is unchanged and
    `force` is False, this is a no-op (skip re-scan). When the content changed,
    the review is re-run and overwritten — but any overrules whose violation
    still exists (matched by kind+excerpt) are carried forward so the user's
    decisions survive a content edit.

    INVARIANT (no re-work on unchanged files): the disk file is ALWAYS the
    original — anonymisation is applied in-flight to LLM-bound consumers, never
    by overwriting the file. The hash is computed over the original on-disk
    text, so an unchanged file always hashes the same → skip → the saved review
    (overrules + anon_mapping_id) is reused verbatim and the user is never
    re-prompted. A content edit moves the hash → re-scan, with surviving
    overrules carried forward. This function records review STATE only; it
    never mutates the file and never anonymises (that happens at the LLM seams,
    reusing this review's saved mapping).
    """
    import json as _json
    import os as _os
    import brain as _brain
    from server_lib.db import DataReviewDB

    ref = source_ref or path
    try:
        if not _os.path.isfile(path):
            return None
        text, kind = _brain.extract_attachment_text(path)
    except Exception as e:
        print(f"[doc_review] extract failed for {path}: {e}", flush=True)
        return None
    if kind != "text" or not (text or "").strip():
        return None

    review_id = _stable_review_id(source_kind, ref, user_id)
    chash = content_hash(text)

    prior = DataReviewDB.get(review_id, user_id, admin=True)
    if prior and not force and prior.get("content_hash") == chash:
        return prior  # unchanged — keep the existing review (+ overrules)

    result = analyze(text, filename=filename or _os.path.basename(path), cfg=cfg)

    # Carry forward still-applicable overrules across a content change.
    overrules = []
    if prior:
        try:
            prior_ov = _json.loads(prior.get("overrules_json") or "[]")
        except Exception:
            prior_ov = []
        live_keys = {(v["kind"], v["excerpt"]) for v in result["violations"]}
        # Re-point each surviving overrule to the violation's NEW id.
        new_id_by_key = {(v["kind"], v["excerpt"]): v["id"]
                         for v in result["violations"]}
        for ov in prior_ov:
            key = (ov.get("kind"), ov.get("excerpt"))
            if key in live_keys:
                ov = dict(ov)
                ov["id"] = new_id_by_key[key]
                overrules.append(ov)

    # We only reach here when the content CHANGED (the unchanged case returned
    # above). A content change invalidates any prior anonymisation: the saved
    # de-anon mapping was built against the old offsets/values, so it no longer
    # applies. Drop it and reset status to 'reviewed' — the user re-anonymises
    # the new content if they wish. Surviving overrules were carried forward.
    DataReviewDB.upsert(
        review_id=review_id, user_id=user_id, content_hash=chash,
        source_kind=source_kind, source_ref=ref,
        filename=filename or _os.path.basename(path), status="reviewed",
        text=text, violations_json=_json.dumps(result["violations"]),
        overrules_json=_json.dumps(overrules),
        anon_mapping_id="",
    )
    return DataReviewDB.get(review_id, user_id, admin=True)


__all__ = [
    "MAX_REVIEW_CHARS",
    "content_hash",
    "analyze",
    "anonymise",
    "deanonymise",
    "review_file_to_db",
    "_stable_review_id",
]
