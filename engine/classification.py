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

# ── Image document type → classification floor ────────────────────────────
# The ARL detector is a MARKER detector: it looks for "Streng vertraulich"
# printed on the page. A photographed passport carries no such marking, so the
# detector sees nothing — and the PII regex, running on OCR output, is no help
# either: on a real passport photo it read character soup, missed the holder's
# name entirely, and raised a phantom "Czech rodné číslo" off a number it could
# not parse. Yet the file is obviously highly sensitive.
#
# Recognising the TYPE of an image is far easier than reading its characters
# (measured: 8/8 passports identified, ~1s each — including the image whose text
# was unreadable). So the type sets a FLOOR on the classification: a passport is
# strictly confidential whether or not the OCR could read a single digit.
#
# Only sensitive types appear here. Everything else (screenshot, photo, other)
# raises nothing — the floor can only ever RAISE the level, never lower one that
# a marker or the content heuristic already established.
IMAGE_TYPE_LEVEL = {
    "passport": "strict",
    "id_card": "strict",
    "drivers_license": "strict",
    "medical": "strict",
    "bank_statement": "confidential",
    "payslip": "confidential",
    "contract": "confidential",
    "certificate": "confidential",
    "invoice": "internal",
    "receipt": "internal",
    "correspondence": "internal",
}

IMAGE_TYPE_LABEL_DE = {
    "passport": "Reisepass", "id_card": "Personalausweis",
    "drivers_license": "Führerschein", "medical": "Medizinisches Dokument",
    "bank_statement": "Kontoauszug", "payslip": "Gehaltsabrechnung",
    "contract": "Vertrag", "certificate": "Urkunde/Zeugnis",
    "invoice": "Rechnung", "receipt": "Beleg/Quittung",
    "correspondence": "Korrespondenz", "screenshot": "Bildschirmfoto",
    "photo": "Foto", "other": "Sonstiges",
}


def image_type_level(doc_type: str) -> str:
    """Classification floor implied by an image's document type ('' = none)."""
    return IMAGE_TYPE_LEVEL.get((doc_type or "").strip().lower(), "")

# Plain-language (German) rationale per classification level — surfaced as the
# tooltip on a classification marker / mismatch in the document reviewer.
LEVEL_WHY_DE = {
    "public":
        "Als »Öffentlich« eingestuft — keine Vertraulichkeitsbeschränkung.",
    "internal":
        "Als »Intern« eingestuft — nur für den internen Gebrauch bestimmt, "
        "nicht für externe Weitergabe.",
    "confidential":
        "Als »Vertraulich« eingestuft — Weitergabe nur an autorisierte "
        "Empfänger; Übermittlung an externe Dienste vermeiden.",
    "strict":
        "Als »Streng Vertraulich« eingestuft — höchste Schutzstufe; darf "
        "externe Systeme nicht erreichen (WPB ARL 20.02.02.06).",
    "unmarked":
        "Keine Klassifizierungs-Kennzeichnung im Dokument gefunden.",
}


def level_why(level: str) -> str:
    """German explanation for a classification level. Used by engine.doc_review
    to annotate classification violations for the reviewer tooltip."""
    return LEVEL_WHY_DE.get(level or "unmarked",
                            LEVEL_WHY_DE["unmarked"])


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

    confidence = _classification_confidence(
        marker_level, marker_meta, filename_hint, heuristic, mismatch)

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
        # Evidence-based 0..1 confidence in `final_level` (NOT a calibrated
        # probability) — for a future threshold ladder. High when an explicit
        # per-page marker is present; low when only a filename hint or a content
        # heuristic drove it; reduced when marker and content disagree.
        "confidence": confidence,
    }


def _classification_confidence(marker_level, marker_meta, filename_hint,
                               heuristic, mismatch) -> float:
    """Derive a 0..1 confidence in the final classification from the evidence.

    An explicit document marker found on most pages is the strongest signal; a
    filename-only hint or a content-keyword heuristic is weak; a marker/content
    mismatch lowers trust (the document contradicts itself)."""
    conf_tier = (marker_meta or {}).get("confidence")
    source = (marker_meta or {}).get("source")
    coverage = (marker_meta or {}).get("coverage_pct", 0) or 0

    if marker_level and source != "filename":
        base = {"high": 0.95, "med": 0.80, "low": 0.65}.get(conf_tier, 0.70)
        # blend in coverage (per-page marker presence)
        base = base * 0.85 + (coverage / 100.0) * 0.15
    elif filename_hint:
        base = 0.45                      # filename-only hint
    elif heuristic and heuristic != "public":
        base = 0.40                      # content heuristic only, no marker
    else:
        base = 0.55                      # unmarked/public with nothing notable
    if mismatch:
        sev = (mismatch or {}).get("severity")
        base -= {"high": 0.25, "med": 0.15}.get(sev, 0.10)
    return round(max(0.05, min(base, 0.99)), 2)


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


# ── Phase B enforcement glue ──────────────────────────────────────────────
# These three functions are the runtime enforcement layer that sits next to
# the pure detector above. They reach back into brain.py's namespace lazily
# (brain IS the live `engine` alias — `import brain as engine`), so the
# config helpers (`_get_classification_config`, `_classification_scan_text`,
# `_classification_action_level`), the runtime globals (`_thread_local`,
# `_current_agent`, `_audit_log`, `_models_config`), `is_model_local`,
# `_identity_deanon`, and `ClassificationBlockedError` are all read fresh
# off the brain module on each call. The lazy import keeps this module's
# top-level free of any brain dependency (one-way DAG).


def _classification_effective_action(level: str, cfg: dict | None = None) -> str:
    """Resolve per-level action with strict-always-block invariant.

    Returns: 'ignore' | 'warn' | 'force_local' | 'block'

    Invariants:
    - strict ALWAYS resolves to 'block' regardless of admin config (ARL §1.11
      "ohne Zustimmung des Vorstands ausnahmslos untersagt").
    - When server_block is OFF, 'block' downgrades to 'force_local' so the
      master switch behaves like gdpr_scanner.server_block.
    """
    import brain as _brain
    if cfg is None:
        cfg = _brain._get_classification_config()
    if not cfg.get("enabled", True):
        return "ignore"
    if level == "strict":
        return "block" if cfg.get("server_block", True) else "force_local"
    actions = cfg.get("per_level_action", {}) or {}
    action = actions.get(level, "ignore")
    if action == "block" and not cfg.get("server_block", True):
        action = "force_local"
    return action


def _classification_gate_tool_text(text: str, source: str) -> None:
    """Classification gate for tool-read content. Raises
    ClassificationBlockedError when the current chat's model is non-local
    AND the detected classification level resolves to a `block` action
    (strict, or confidential with `server_block` on and no usable local
    fallback). Force_local is left to the chat worker's pre-flight — by
    the time we reach this seam the model is locked, so the only options
    here are pass-through (warn) or hard-block.

    No-op when scanner disabled, content unclassified, or model is local.
    Never raises on its own internal errors — fail-open.
    """
    import brain as _brain
    if not text:
        return
    try:
        cfg = _brain._get_classification_config()
        if not cfg.get("enabled", True):
            return
        # Resolve current model — sidecar tool dispatch sets current_session_id
        # in thread-locals; we look up the session model from there.
        sid = _brain.get_request_context().current_session_id or ""
        model = ""
        if sid:
            try:
                from server_lib.db import ChatDB as _ChatDB
                info = _ChatDB.get_session_info(sid)
                model = (info or {}).get("model") or ""
            except Exception:
                model = ""
        if not model:
            return
        try:
            if _brain.is_model_local(model):
                return
        except Exception:
            return
        # Derive a filename hint from `source` (e.g. "file:report.pdf" → "report.pdf")
        fn_hint = ""
        if isinstance(source, str) and ":" in source:
            fn_hint = source.split(":", 1)[1]
        result = _brain._classification_scan_text(text, filename=fn_hint)
        if not result:
            return
        # Action level = max(marker, heuristic). The audit log still gets
        # the raw final_level for context but the gate decision follows
        # the higher of the two signals.
        level = _brain._classification_action_level(result)
        action = _classification_effective_action(level, cfg=cfg)
        if action != "block":
            return
        # Audit + raise
        _agent = _brain.get_request_context().current_agent or _brain._current_agent
        _agent_id = _agent.agent_id if _agent else "main"
        if cfg.get("server_log", True) and _brain._audit_log:
            try:
                _brain._audit_log.log_action(
                    agent=_agent_id,
                    action_type="classification_blocked",
                    tool_name="classification_scanner",
                    args_summary=f"source={source} level={level}",
                    result_summary=f"model={model} reason=tool_read policy=block",
                    result_status="blocked",
                    session_id=sid or None,
                    source="tool",
                )
            except Exception:
                pass
        raise _brain.ClassificationBlockedError(
            f"[Classification block] Refusing to return '{level}' content "
            f"to a non-local model (source={source}). "
            f"Switch to a local model to access this document."
        )
    except _brain.ClassificationBlockedError:
        raise  # re-raise — caller turns into a tool error
    except Exception as e:
        try:
            print(f"[classification] gate error (fail-open): {e}", flush=True)
        except Exception:
            pass
        return


def classification_pick_model_for_background(model: str, texts,
                                               purpose: str = "",
                                               filenames: list[str] | None = None):
    """Decide model for a background call based on classification detection.

    Parallels gdpr_pick_model_for_background but with a different policy
    shape: per-level action (`ignore` / `warn` / `force_local` / `block`)
    instead of GDPR's anonymise/swap/abort. There is NO anonymise path
    for classified content — anonymisation strips PII, not classification
    markers, so it doesn't change the legal status of the document.

    Returns: (model, texts, deanon_fn).
      * `deanon_fn` is always `_identity_deanon` — kept for signature
        compatibility with the GDPR helper so callers can pipe both
        through the same scaffolding.

    Caller flow (mirror gdpr_pick_model_for_background usage):

        try:
            model, texts2, _ = engine.classification_pick_model_for_background(
                model, texts, purpose='chat_summary')
        except engine.ClassificationBlockedError:
            return None  # skip background call

    Note: classification scanning over multiple texts treats each as an
    independent sample; the worst per-text level wins.
    """
    import brain as _brain
    _identity_deanon = _brain._identity_deanon
    if isinstance(texts, str):
        samples = [texts]
        _input_was_str = True
    else:
        try:
            samples = [t if isinstance(t, str) else "" for t in texts]
        except TypeError:
            return (model, [], _identity_deanon)
        _input_was_str = False

    cfg = _brain._get_classification_config()
    if not cfg.get("enabled", True) or not any(samples):
        return (model, samples if not _input_was_str else samples[0], _identity_deanon)

    filenames = list(filenames or [])
    while len(filenames) < len(samples):
        filenames.append("")

    # Scan each sample, pick the worst level
    worst_rank = -1
    worst_level = None
    worst_evidence: list[dict] = []
    for s, fn in zip(samples, filenames):
        if not s:
            continue
        try:
            r = _brain._classification_scan_text(s, filename=fn)
        except Exception:
            continue
        if not r:
            continue
        # Action level follows the higher of marker and content
        # heuristic, so a public-marked PDF with confidential content
        # gets the confidential policy here too.
        lvl = _brain._classification_action_level(r)
        rank = LEVEL_RANK.get(lvl, -1) if lvl != "unmarked" else 0  # unmarked acts as 'internal'
        if rank > worst_rank:
            worst_rank = rank
            worst_level = lvl
            worst_evidence = r.get("marker_evidence", [])[:1]

    if worst_level is None or worst_rank < 0:
        return (model, samples if not _input_was_str else samples[0], _identity_deanon)

    action = _classification_effective_action(worst_level, cfg=cfg)
    _agent = _brain.get_request_context().current_agent or _brain._current_agent
    _agent_id = _agent.agent_id if _agent else "main"
    _sid = _brain.get_request_context().current_session_id or ""
    _log_audit = bool(cfg.get("server_log", True) and _brain._audit_log)

    if _log_audit:
        try:
            _brain._audit_log.log_action(
                agent=_agent_id,
                action_type="classification_detected",
                tool_name="classification_scanner",
                args_summary=f"level={worst_level}",
                result_summary=f"purpose={purpose or '-'} model={model} action={action}",
                result_status="warning",
                session_id=_sid or None,
                source="background",
            )
        except Exception:
            pass

    # ignore / warn → pass through (no-op routing)
    if action in ("ignore", "warn"):
        return (model, samples if not _input_was_str else samples[0], _identity_deanon)

    # Already on a local model — nothing to reroute regardless of action.
    try:
        model_is_local = _brain.is_model_local(model)
    except Exception:
        model_is_local = False
    if model_is_local:
        return (model, samples if not _input_was_str else samples[0], _identity_deanon)

    # block → raise (after audit)
    if action == "block":
        if _log_audit:
            try:
                _brain._audit_log.log_action(
                    agent=_agent_id,
                    action_type="classification_blocked",
                    tool_name="classification_scanner",
                    args_summary=f"model={model} level={worst_level}",
                    result_summary=f"purpose={purpose or '-'} policy=block",
                    result_status="blocked",
                    session_id=_sid or None,
                    source="background",
                )
            except Exception:
                pass
        raise _brain.ClassificationBlockedError(
            f"[Classification block] Background call refused (purpose={purpose or '-'}): "
            f"content classified '{worst_level}'; policy=block."
        )

    # force_local → swap to fallback if usable, else block (NOT passthrough —
    # for classified content we'd rather refuse than silently leak).
    fallback = (cfg.get("default_local_fallback_model") or "").strip()
    swap_ok = False
    if fallback and fallback != model:
        try:
            fcfg = (_brain._models_config or {}).get(fallback) or {}
            if fcfg.get("enabled") and _brain.is_model_local(fallback):
                swap_ok = True
        except Exception:
            swap_ok = False
    if swap_ok:
        if _log_audit:
            try:
                _brain._audit_log.log_action(
                    agent=_agent_id,
                    action_type="classification_auto_fallback",
                    tool_name="classification_scanner",
                    args_summary=f"{model} -> {fallback} level={worst_level}",
                    result_summary=f"purpose={purpose or '-'} policy=force_local",
                    result_status="ok",
                    session_id=_sid or None,
                    source="background",
                )
            except Exception:
                pass
        return (fallback, samples if not _input_was_str else samples[0], _identity_deanon)
    # No usable local fallback — block.
    if _log_audit:
        try:
            _brain._audit_log.log_action(
                agent=_agent_id,
                action_type="classification_blocked",
                tool_name="classification_scanner",
                args_summary=f"model={model} level={worst_level} fallback={fallback or '-'}",
                result_summary=f"purpose={purpose or '-'} policy=force_local reason=no_local_fallback",
                result_status="blocked",
                session_id=_sid or None,
                source="background",
            )
        except Exception:
            pass
    raise _brain.ClassificationBlockedError(
        f"[Classification block] No usable local fallback for '{worst_level}' "
        f"content (purpose={purpose or '-'}): fallback='{fallback or '-'}'."
    )
