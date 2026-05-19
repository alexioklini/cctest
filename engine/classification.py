"""Document classification detection per WPB ARL 20.02.02.06.

Detects whether a document is marked öffentlich/intern/vertraulich/streng
vertraulich (or English/TLP equivalents), and cross-checks the marker against
a content heuristic (PII findings + keyword lists) to flag mismatches.

Phase A: pure detector + audit surface. Enforcement (block, force_local) is
Phase B and will plug into `_gdpr_anon_tool_text` + `/v1/attachments/scan`
using `detect_classification()` as the building block.

The detector is intentionally regex + heuristic only — no LLM. The ARL
mandates marking; detection is therefore primarily a structural check, not
a semantic one. Content analysis is a secondary signal for mismatch
detection (e.g. "marked Öffentlich but full of IBANs").
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable


# ── Classification levels (canonical IDs) ─────────────────────────────────

LEVELS = ("public", "internal", "confidential", "strict")
LEVEL_LABEL_DE = {
    "public": "Öffentlich",
    "internal": "Intern",
    "confidential": "Vertraulich",
    "strict": "Streng Vertraulich",
    "unmarked": "Unmarked",
}
LEVEL_RANK = {"public": 0, "internal": 1, "confidential": 2, "strict": 3}


# ── Marker patterns ───────────────────────────────────────────────────────
# Each entry: (level, compiled regex, pattern_id). First-match-wins per text;
# we still scan all so per-page coverage can be reported.

def _build_marker_patterns() -> list[tuple[str, re.Pattern, str]]:
    pats: list[tuple[str, re.Pattern, str]] = []

    # German — primary, per WPB ARL §1.7
    # Each body matches the level word at a word boundary. The anchoring
    # keyword in front (Dokumentenklassifizierung, etc.) provides the
    # context; we don't need a tight word-end lookahead.
    de = [
        ("strict",       r"streng\s+vertraulich\b",                  "de_strict"),
        ("confidential", r"vertraulich\b",                            "de_confidential"),
        ("internal",     r"intern\b",                                 "de_internal"),
        ("public",       r"öffentlich\b",                             "de_public"),
    ]
    for level, body, pid in de:
        # Anchor either to the "Dokumentenklassifizierung" / "Klassifizierung"
        # keyword OR to a footer/header context. The lone word "intern" is
        # too noisy without an anchor.
        anchored = (
            r"(?:dokumentenklassifizierung|klassifizierung|classification|"
            r"vertraulichkeitsstufe|sicherheitsstufe|einstufung)"
            r"\s*[:\-–]?\s*"
            + body
        )
        pats.append((level, re.compile(anchored, re.IGNORECASE), pid))

    # English equivalents
    en = [
        ("strict",       r"top\s+secret|strictly\s+confidential",   "en_strict"),
        ("confidential", r"confidential(?!\s+\w)",                   "en_confidential"),
        ("internal",     r"internal\s+use\s+only|restricted",        "en_internal"),
        ("public",       r"public|unclassified",                     "en_public"),
    ]
    for level, body, pid in en:
        anchored = (
            r"(?:classification|sensitivity|confidentiality)"
            r"\s*[:\-–]?\s*"
            + body
        )
        pats.append((level, re.compile(anchored, re.IGNORECASE), pid))

    # TLP (Traffic Light Protocol)
    tlp = [
        ("strict",       "RED",    "tlp_red"),
        ("confidential", "AMBER",  "tlp_amber"),
        ("internal",     "GREEN",  "tlp_green"),
        ("public",       "WHITE|CLEAR", "tlp_white"),
    ]
    for level, color, pid in tlp:
        pats.append((level, re.compile(r"TLP\s*[:\-]\s*(?:" + color + r")\b", re.IGNORECASE), pid))

    return pats


_MARKER_PATTERNS = _build_marker_patterns()


# ── Filename hints ────────────────────────────────────────────────────────
# Lower-confidence than content markers, but useful when extraction fails.

_FILENAME_HINTS = [
    ("strict",       re.compile(r"streng[_\- ]?vertraulich|top[_\- ]?secret", re.IGNORECASE), "fn_strict"),
    ("confidential", re.compile(r"vertraulich|confidential|restricted", re.IGNORECASE),       "fn_confidential"),
    ("internal",     re.compile(r"\b(intern|internal)\b", re.IGNORECASE),                     "fn_internal"),
    # WPB ARL number pattern — these are by-definition at least internal
    ("internal",     re.compile(r"\b20\.\d{2}\.\d{2}(?:\.\d{2})?\b"),                         "fn_wpb_arl"),
]


# ── Default keyword seeds (admin-editable, persisted in config.json) ─────

DEFAULT_KEYWORDS = {
    # Triggers → confidential heuristic level
    "confidential": [
        "Vorstand",
        "Aufsichtsrat",
        "interner Bericht",
        "Strategie",
        "M&A",
        "Personalakt",
        "Gehaltsliste",
        "Bonus",
        "Geschäftsgeheimnis",
        "Kundenakte",
        "Kundendaten",
        "Compliance-Vorfall",
        "Risikobericht",
        "Vorstandssitzung",
        "Vorstandsbeschluss",
    ],
    # Triggers → strict heuristic level (very high sensitivity)
    "strict": [
        "Vorstandsprotokoll",
        "Aufsichtsratsprotokoll",
        "Ad-hoc",
        "Disziplinarverfahren",
        "Kündigungsgrund",
    ],
    # Triggers → internal heuristic level (presence of these alone is fine)
    "internal": [
        "CISO",
        "Hauspost",
        "CRYPTSHARE",
        "Mitarbeiter",
        "Bereichsleiter",
        "Informationseigentümer",
        "IT-Sicherheitsbeauftragter",
        "Arbeitsrichtlinie",
    ],
}


def get_keywords(cfg: dict | None = None) -> dict[str, list[str]]:
    """Resolve the keyword config — merges admin overrides over defaults."""
    out = {k: list(v) for k, v in DEFAULT_KEYWORDS.items()}
    if cfg:
        custom = (cfg.get("classification") or {}).get("keywords") or {}
        for level, words in custom.items():
            if level in out and isinstance(words, list):
                out[level] = [str(w) for w in words if isinstance(w, str) and w.strip()]
    return out


def get_extra_marker_patterns(cfg: dict | None = None) -> list[tuple[str, re.Pattern, str]]:
    """Admin-editable extra marker regexes. Each entry: {level, pattern}."""
    extra: list[tuple[str, re.Pattern, str]] = []
    if not cfg:
        return extra
    raw = (cfg.get("classification") or {}).get("extra_patterns") or []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        level = item.get("level")
        pattern = item.get("pattern")
        if level not in LEVELS or not isinstance(pattern, str) or not pattern.strip():
            continue
        try:
            extra.append((level, re.compile(pattern, re.IGNORECASE), f"custom_{i}"))
        except re.error:
            continue
    return extra


# ── Marker scan ──────────────────────────────────────────────────────────

def _scan_markers(text: str, *, extra: Iterable[tuple[str, re.Pattern, str]] = ()) -> list[dict]:
    """Find every marker hit in text. Returns list ordered by position.

    Each finding: {level, pattern_id, start, end, excerpt}.
    """
    findings: list[dict] = []
    if not text:
        return findings
    all_pats: list[tuple[str, re.Pattern, str]] = list(_MARKER_PATTERNS) + list(extra)
    for level, pat, pid in all_pats:
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            ctx_start = max(0, s - 30)
            ctx_end = min(len(text), e + 30)
            excerpt = text[ctx_start:ctx_end].replace("\n", " ").strip()
            findings.append({
                "level": level,
                "pattern_id": pid,
                "start": s,
                "end": e,
                "excerpt": excerpt[:160],
            })
    findings.sort(key=lambda f: f["start"])
    return findings


def _pick_marker_level(findings: list[dict], page_count: int) -> tuple[str | None, dict]:
    """From all marker hits, pick the document-level marker.

    Rules:
      - If any 'strict' finding → strict (highest wins, defensive)
      - Else: take the most frequent level above 'public'.
      - Coverage: % of pages with at least one finding of that level.
      - Confidence:
          'high'  → marker on ≥80% of pages (ARL §1.10/§1.11 require per-page)
          'med'   → marker present but on <80% of pages
          'low'   → only filename hint, no body match
          None    → no marker at all (caller treats as 'unmarked')
    """
    if not findings:
        return None, {"confidence": None, "coverage_pct": 0, "counts": {}}

    counts: dict[str, int] = {}
    pages_with_level: dict[str, set] = {}
    for f in findings:
        lvl = f["level"]
        counts[lvl] = counts.get(lvl, 0) + 1
        page = f.get("page", 0)
        pages_with_level.setdefault(lvl, set()).add(page)

    if counts.get("strict"):
        level = "strict"
    else:
        non_public = {k: v for k, v in counts.items() if k != "public"}
        if non_public:
            level = max(non_public, key=non_public.get)
        else:
            level = "public"

    coverage_pct = 0
    if page_count > 0:
        coverage_pct = round(100 * len(pages_with_level.get(level, set())) / page_count)

    if page_count > 1 and coverage_pct >= 80:
        confidence = "high"
    elif counts.get(level, 0) >= 2:
        confidence = "med"
    else:
        confidence = "med" if page_count == 1 else "low"

    return level, {
        "confidence": confidence,
        "coverage_pct": coverage_pct,
        "counts": counts,
    }


def _scan_filename(filename: str) -> dict | None:
    if not filename:
        return None
    base = os.path.basename(filename)
    for level, pat, pid in _FILENAME_HINTS:
        m = pat.search(base)
        if m:
            return {
                "level": level,
                "pattern_id": pid,
                "excerpt": base,
                "source": "filename",
            }
    return None


# ── Content heuristic (PII + keywords) ───────────────────────────────────

def _scan_keywords(text: str, keywords: dict[str, list[str]]) -> dict[str, list[str]]:
    """Returns {level: [matched_keyword, ...]} for each level with hits."""
    hits: dict[str, list[str]] = {}
    if not text:
        return hits
    text_lc = text.lower()
    for level, words in keywords.items():
        matched: list[str] = []
        for w in words:
            if not w:
                continue
            if w.lower() in text_lc:
                matched.append(w)
        if matched:
            hits[level] = matched
    return hits


def _heuristic_level(pii_findings: list[dict], keyword_hits: dict[str, list[str]]) -> str:
    """Combine PII findings + keyword hits → suggested level."""
    # Strict keywords are the strongest signal
    if keyword_hits.get("strict"):
        return "strict"
    # PII or confidential keywords → confidential
    has_personal_pii = any(
        f.get("category") in ("personal", "financial", "bare_id", "contact")
        for f in (pii_findings or [])
    )
    if has_personal_pii or keyword_hits.get("confidential"):
        return "confidential"
    if keyword_hits.get("internal"):
        return "internal"
    return "public"


def _evaluate_mismatch(marker_level: str | None,
                       heuristic: str,
                       pii_findings: list[dict],
                       keyword_hits: dict[str, list[str]]) -> dict | None:
    """Mismatch policy:
      - marker=public + (PII OR confidential keywords)  → HIGH
      - marker=internal + multiple confidential/strict signals  → MED
      - marker present but heuristic is HIGHER than marker → LOW (under-classified)
      - marker LOWER than heuristic by 2+ ranks → HIGH (e.g. public vs confidential)
      - marker HIGHER than heuristic → no mismatch (over-classification is fine per ARL §1.5)
      - marker=None → not a mismatch, it's the 'unmarked' state (caller surfaces separately)
    """
    if marker_level is None:
        return None

    marker_rank = LEVEL_RANK.get(marker_level, 0)
    heur_rank = LEVEL_RANK.get(heuristic, 0)

    if heur_rank <= marker_rank:
        return None  # marker meets or exceeds heuristic — fine

    delta = heur_rank - marker_rank
    if delta >= 2:
        severity = "high"
    elif marker_level == "public" and (pii_findings or keyword_hits.get("confidential")):
        severity = "high"
    else:
        severity = "med" if delta >= 1 else "low"

    reasons: list[str] = []
    if pii_findings:
        n = len(pii_findings)
        reasons.append(f"{n} PII finding(s)")
    for lvl, words in keyword_hits.items():
        if lvl in ("confidential", "strict"):
            reasons.append(f"{lvl} keywords: {', '.join(words[:3])}")

    return {
        "severity": severity,
        "marker_level": marker_level,
        "heuristic_level": heuristic,
        "reasons": reasons,
    }


# ── Public entry point ───────────────────────────────────────────────────

def detect_classification(text: str,
                           *,
                           filename: str = "",
                           page_texts: list[str] | None = None,
                           cfg: dict | None = None,
                           pii_findings: list[dict] | None = None) -> dict:
    """Classify a document. Pure function; no I/O.

    Args:
      text: full document text (markdown or plain)
      filename: optional original filename (used for hints when text is empty)
      page_texts: optional per-page text list (improves coverage detection)
      cfg: full config dict (for `classification.keywords` + `extra_patterns`)
      pii_findings: optional pre-computed PII findings (else caller can pass
                    None and content_signals.pii_findings stays empty —
                    the detector does NOT call brain._pii_scan_text itself
                    to keep this module dependency-light)

    Returns:
      {
        marker_level: 'public'|'internal'|'confidential'|'strict'|None,
        marker_meta:  {confidence, coverage_pct, counts},
        marker_evidence: [{level, pattern_id, page, excerpt}, ...],
        filename_hint: {level, pattern_id, excerpt} | None,
        content_signals: {
            pii_findings: [...],          # passed through from caller
            keyword_hits: {level: [words]},
            heuristic_level: 'public'|...
        },
        mismatch: {severity, marker_level, heuristic_level, reasons} | None,
        final_level: 'public'|'internal'|'confidential'|'strict'|'unmarked',
      }
    """
    extra = get_extra_marker_patterns(cfg)
    keywords = get_keywords(cfg)
    pii_findings = pii_findings or []

    # Per-page scan when available — gives accurate coverage_pct
    findings: list[dict] = []
    if page_texts:
        for i, ptext in enumerate(page_texts, start=1):
            for f in _scan_markers(ptext, extra=extra):
                f["page"] = i
                findings.append(f)
        page_count = len(page_texts)
    else:
        findings = _scan_markers(text, extra=extra)
        for f in findings:
            f["page"] = 1
        page_count = 1

    marker_level, marker_meta = _pick_marker_level(findings, page_count)
    filename_hint = _scan_filename(filename)

    # Filename hint promotes only when body has nothing
    if marker_level is None and filename_hint:
        marker_level = filename_hint["level"]
        marker_meta = {"confidence": "low", "coverage_pct": 0, "counts": {}, "source": "filename"}

    keyword_hits = _scan_keywords(text, keywords)
    heuristic = _heuristic_level(pii_findings, keyword_hits)
    mismatch = _evaluate_mismatch(marker_level, heuristic, pii_findings, keyword_hits)

    final_level: str
    if marker_level is None:
        final_level = "unmarked"
    else:
        final_level = marker_level

    # Trim marker_evidence — keep top 5 hits, sorted by (page, position)
    marker_evidence = [
        {"level": f["level"], "pattern_id": f["pattern_id"],
         "page": f.get("page", 1), "excerpt": f["excerpt"]}
        for f in findings[:5]
    ]

    return {
        "marker_level": marker_level,
        "marker_meta": marker_meta,
        "marker_evidence": marker_evidence,
        "filename_hint": filename_hint,
        "content_signals": {
            "pii_findings": pii_findings,
            "keyword_hits": keyword_hits,
            "heuristic_level": heuristic,
        },
        "mismatch": mismatch,
        "final_level": final_level,
    }


# ── PDF footer-fallback (markitdown drops repeating page footers) ────────

def extract_pdf_page_texts(pdf_path: str, max_pages: int = 40) -> list[str]:
    """Cheap pypdf-style per-page text via fitz (already a Brain dep).

    Used ONLY for marker scan when the main markdown extraction returned
    no marker — markitdown silently drops repeating page footers, so a
    document marked `Dokumentenklassifizierung intern` on every page can
    end up looking unmarked in the extracted .md. This raw extraction
    preserves the footer. Returns [] on any error (caller falls back to
    'unmarked' state).
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        return []
    try:
        import fitz  # type: ignore
    except ImportError:
        return []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []
    pages: list[str] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            try:
                pages.append(page.get_text() or "")
            except Exception:
                pages.append("")
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return pages


# ── Convenience for callers that have only raw text + brain ──────────────

def detect_with_pii(text: str,
                     *,
                     filename: str = "",
                     page_texts: list[str] | None = None,
                     pdf_path: str = "",
                     cfg: dict | None = None) -> dict:
    """Like detect_classification but pulls PII findings via brain._pii_scan_text.

    `pdf_path` enables the footer-fallback path: if the primary marker
    scan finds nothing and `pdf_path` is a .pdf, we re-scan the raw
    per-page text from fitz. Caller passes the source PDF path (not the
    converted .md).

    Kept separate so detect_classification stays pure / testable without
    importing brain.
    """
    try:
        from brain import _pii_scan_text  # lazy — avoid circular import at module load
        pii = _pii_scan_text(text, max_findings=50) if text else []
    except Exception:
        pii = []
    result = detect_classification(text, filename=filename,
                                    page_texts=page_texts,
                                    cfg=cfg, pii_findings=pii)
    # Footer-fallback: only when primary scan said unmarked + we have a PDF.
    # Skip for filename-only hits (those have marker_level set already).
    if (result.get("marker_level") is None and pdf_path
            and pdf_path.lower().endswith(".pdf")):
        pdf_pages = extract_pdf_page_texts(pdf_path)
        if pdf_pages:
            raw_text = "\n".join(pdf_pages)
            fallback = detect_classification(raw_text, filename=filename,
                                              page_texts=pdf_pages,
                                              cfg=cfg, pii_findings=pii)
            if fallback.get("marker_level") is not None:
                # Promote — keep heuristic from the main extraction (closer
                # to what callers will actually feed into the LLM).
                result["marker_level"] = fallback["marker_level"]
                result["marker_meta"] = dict(fallback["marker_meta"])
                result["marker_meta"]["source"] = "pdf_footer_fallback"
                result["marker_evidence"] = fallback["marker_evidence"]
                result["final_level"] = fallback["marker_level"]
                # Re-evaluate mismatch with the recovered marker
                heur = result["content_signals"]["heuristic_level"]
                result["mismatch"] = _evaluate_mismatch(
                    fallback["marker_level"], heur, pii,
                    result["content_signals"]["keyword_hits"],
                )
    return result
