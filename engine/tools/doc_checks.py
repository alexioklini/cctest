"""Deterministic document-verification toolset — mrz_verify /
doc_dates_check / identity_consistency (L1, PII_ANALYSIS_PARITY_HANDOVER.md).

WHY: a KYC/fraud chat's central checks are ARITHMETIC ON THE PROTECTED VALUES
themselves (ICAO-9303 MRZ check digits, renewal gaps, "identical personal data
across 34 years"). Under transparent anonymisation the LLM only sees fakes —
its own math then produces FALSE forgery indications ("check digit invalid!").
These tools follow the xlsx/ocr pattern ("the model supplies INTENT, the
SERVER computes"): the checks run SERVER-SIDE ON THE RAW DATA and return
PII-FREE VERDICTS — immune to any anonymisation, identical output whether the
GDPR scanner is on or off.

Inputs are primarily PATHS (raw data on disk), `text` only as a fallback — a
fake MRZ string passed as an arg would otherwise verify wrongly (design
decision #3 in the handover; robust even before the L3 args-deanonymisation
exists).

Wired per the 4-site rule (TOOL_DEFINITIONS / TOOL_GROUPS "doc_checks" / impl
here / TOOL_DISPATCH). Reaches brain runtime via lazy `import brain as _brain`.
"""

from __future__ import annotations

import datetime as _dt
import os
import re

from engine.tool_exec import _ok, _err

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
_TEXT_EXTS = {".txt", ".md", ".log", ".json", ".html", ".htm", ".csv"}

# ---------------------------------------------------------------------------
# ICAO 9303 — check digits + MRZ parsing
# ---------------------------------------------------------------------------
_MRZ_WEIGHTS = (7, 3, 1)


def mrz_char_value(c: str) -> int:
    """ICAO 9303: '0'-'9' → 0-9, 'A'-'Z' → 10-35, '<' → 0."""
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + 10
    return 0


def mrz_check_digit(s: str) -> int:
    """Weights 7,3,1 cyclic; sum mod 10."""
    return sum(mrz_char_value(c) * _MRZ_WEIGHTS[i % 3]
               for i, c in enumerate(s)) % 10


def _check(s: str, digit: str):
    """True/False for a present check digit, None when the digit is missing/
    unreadable ('<' is a LEGAL check digit only for optional empty fields)."""
    if not digit or not digit.isdigit():
        return None
    return mrz_check_digit(s) == int(digit)


# OCR confusions repaired ONLY inside numeric MRZ fields (never the name).
_OCR_NUM_FIX = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1",
                              "Z": "2", "S": "5", "B": "8", "D": "0"})


def _mrz_candidate_lines(text: str) -> list[str]:
    """Extract MRZ-looking lines: strip inner whitespace, uppercase; keep
    lines ≥ 25 chars of [A-Z0-9<] containing at least one '<' or being
    digit-heavy (line 2 of a TD3 can OCR without visible fillers)."""
    out = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", "", raw).upper()
        # OCR confusions for the filler char on phone photos.
        line = line.translate(str.maketrans({"«": "<", "»": "<",
                                             "‹": "<", "›": "<"}))
        if len(line) < 25 or not re.fullmatch(r"[A-Z0-9<]+", line):
            continue
        digits = sum(c.isdigit() for c in line)
        if "<" in line or digits >= 10:
            out.append(line)
    return out


# Filler runs ('<<<<<') on low-res photos OCR as letter salad drawn from a
# small confusion set (E/K/R/C/L/M — measured on the reference JPGs:
# 'EMCRRREREKERRERLE', 'EERREREREKKERKEE'). Trailing given-name tokens made
# ONLY of these chars are garbled fillers, not names. Trailing-only, so a
# real leading given name is never dropped.
_FILLER_GARBLE_RE = re.compile(r"^[CEKLMR]{5,}$")


def _strip_mrz_filler_garble(givens: str) -> str:
    toks = givens.split()
    while toks and _FILLER_GARBLE_RE.fullmatch(toks[-1]):
        toks.pop()
    # Intra-token trailing garble ('BONNTIMARTIERERKKEKEES' — fillers glued
    # onto the last given name when the '<' separators were lost entirely).
    if toks:
        toks[-1] = re.sub(r"[CEKLMR]{5,}[A-Z]{0,2}$", "", toks[-1]) or toks[-1]
    # Single glued token with an inner 'X' ('BONNIEXMARIE' — the single '<'
    # between given names OCR'd as X): split when both halves are name-sized.
    # A real X-name (XAVIER) sits at a chunk START, which this preserves.
    if len(toks) == 1 and len(toks[0]) >= 8 and "X" in toks[0][1:-1]:
        parts = [p for p in toks[0].split("X") if len(p) >= 3]
        if len(parts) >= 2:
            toks = parts
    return " ".join(toks)


def _parse_yymmdd(s: str, *, dob: bool):
    """MRZ 6-digit date → datetime.date or None. Century: dob years above the
    current 2-digit year are 19xx; expiry dates below ~+50y stay 20xx."""
    s = s.translate(_OCR_NUM_FIX)
    if not re.fullmatch(r"\d{6}", s):
        return None
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    now_yy = _dt.date.today().year % 100
    century = 1900 if (dob and yy > now_yy) else 2000
    try:
        return _dt.date(century + yy, mm, dd)
    except ValueError:
        return None


def parse_mrz(text: str) -> dict | None:
    """Find + parse a machine-readable zone in OCR'd/extracted text.
    Supports TD3 (passports, 2×44), TD2 (2×36) and TD1 (ID cards, 3×30) —
    tolerant of shortened lines (OCR drops trailing fillers; the v9.331
    collapse_ocr_filler caps '<'-runs). Returns the raw parsed fields —
    the caller decides what leaves the tool (verdicts only)."""
    lines = _mrz_candidate_lines(text)
    if not lines:
        return None

    # TD3/TD2: find a NAME line (starts with doc-type letter + '<' + issuer)
    # followed by a DATA line. TD1: doc-type line + numeric line + name line.
    for i, l1 in enumerate(lines):
        if not re.match(r"^[A-Z][A-Z<][A-Z<]{3}", l1) or "<<" not in l1:
            continue
        for l2 in lines[i + 1:i + 3]:
            parsed = _parse_mrz_dataline_td3(l1, l2)
            if parsed:
                return parsed
    # TD1: data is on line 1 (doc number) + line 2 (dates), name on line 3.
    for i, l1 in enumerate(lines):
        if len(l1) < 30 or not re.match(r"^[A-Z][A-Z0-9<][A-Z<]{3}", l1):
            continue
        for l2 in lines[i + 1:i + 3]:
            parsed = _parse_mrz_td1(l1, l2, lines[i + 2:i + 4])
            if parsed:
                return parsed
    # Bare data line (a pasted/quoted MRZ line 2 without the name line —
    # the checksums still verify; identity fields stay empty).
    for l2 in lines:
        parsed = _parse_mrz_dataline_td3(None, l2)
        if parsed:
            return parsed
    return None


def _parse_mrz_dataline_td3(line1: str | None, line2: str) -> dict | None:
    """TD3 (passport, 44-char lines) + TD2 (36) share the line-2 layout for
    the first 28 chars: number(9) chk(1) nat(3) dob(6) chk(1) sex(1)
    expiry(6) chk(1) [optional/personal-number + final checks].
    line1=None → bare data line (no name/doc-type available)."""
    if len(line2) < 28:
        return None
    number_raw = line2[0:9]
    if not re.match(r"^[A-Z0-9<]{9}$", number_raw):
        return None
    num_chk = line2[9]
    nat = line2[10:13].replace("<", "")
    dob_raw, dob_chk = line2[13:19], line2[19]
    sex = line2[20] if line2[20] in ("M", "F", "<", "X") else "?"
    exp_raw, exp_chk = line2[21:27], line2[27]
    dob = _parse_yymmdd(dob_raw, dob=True)
    exp = _parse_yymmdd(exp_raw, dob=False)
    # Plausibility: accept the line when the dates parse OR the document-
    # number checksum verifies (low-res phone photos often garble the date
    # fields while the number block survives — a PARTIAL verdict on a real
    # line beats discarding it; measured on the reference 10-JPG set).
    if dob is None and exp is None and not _check(number_raw, num_chk):
        return None  # not a plausible data line

    fmt = "TD3" if (line1 and len(line1) >= 40) or len(line2) >= 40 else "TD2"
    if line1:
        doc_type = line1[0]
        issuer = line1[2:5].replace("<", "")
        from engine.identity import parse_mrz_name
        surname, givens = parse_mrz_name(line1[5:])
        givens = _strip_mrz_filler_garble(givens)
    else:
        doc_type, issuer, surname, givens = "?", "", "", ""

    # A check digit is only computable when the FIELD is readable — a garbled
    # date field with a surviving digit as "check" would otherwise yield
    # False = a false forgery indication (the exact F2 failure this tool
    # exists to prevent). Unreadable → None (not checkable), never False.
    dob_num = dob_raw.translate(_OCR_NUM_FIX)
    exp_num = exp_raw.translate(_OCR_NUM_FIX)
    checks = {
        "document_number": _check(number_raw, num_chk),
        "dob": _check(dob_num, dob_chk)
        if re.fullmatch(r"\d{6}", dob_num) else None,
        "expiry": _check(exp_num, exp_chk)
        if re.fullmatch(r"\d{6}", exp_num) else None,
        "personal_number": None,
        "composite": None,
    }
    # Retry the document-number check with OCR-confusion fixes if it failed.
    corrected = False
    if checks["document_number"] is False:
        alt = number_raw.translate(_OCR_NUM_FIX)
        if alt != number_raw and _check(alt, num_chk):
            checks["document_number"] = True
            corrected = True

    # Personal number + composite only when the full line survived OCR.
    if fmt == "TD3" and len(line2) >= 44:
        personal_raw, personal_chk = line2[28:42], line2[42]
        checks["personal_number"] = _check(personal_raw, personal_chk)
        composite_src = line2[0:10] + line2[13:20] + line2[21:43]
        checks["composite"] = _check(composite_src, line2[43])
    elif fmt == "TD2" and len(line2) >= 36:
        composite_src = line2[0:10] + line2[13:20] + line2[21:35]
        checks["composite"] = _check(composite_src, line2[35])

    return {
        "format": fmt, "doc_type": doc_type, "issuer": issuer,
        "nationality": nat, "sex": sex, "dob": dob, "expiry": exp,
        "surname": surname, "givens": givens,
        "document_number": number_raw.strip("<"),
        "checks": checks, "ocr_corrections": corrected,
    }


def _parse_mrz_td1(line1: str, line2: str, name_lines: list[str]) -> dict | None:
    """TD1 (ID cards, 3×30): line1 = type(2) issuer(3) number(9) chk(1) opt;
    line2 = dob(6) chk(1) sex(1) expiry(6) chk(1) nat(3) opt(11) composite(1);
    line3 = name."""
    if len(line1) < 15 or len(line2) < 18:
        return None
    dob_raw, dob_chk = line2[0:6], line2[6]
    sex = line2[7] if line2[7] in ("M", "F", "<", "X") else "?"
    exp_raw, exp_chk = line2[8:14], line2[14]
    dob = _parse_yymmdd(dob_raw, dob=True)
    exp = _parse_yymmdd(exp_raw, dob=False)
    if dob is None or exp is None:
        return None
    number_raw, num_chk = line1[5:14], line1[14]
    nat = line2[15:18].replace("<", "")
    surname, givens = "", ""
    for nl in name_lines:
        if "<<" in nl:
            from engine.identity import parse_mrz_name
            surname, givens = parse_mrz_name(nl)
            break
    checks = {
        "document_number": _check(number_raw, num_chk),
        "dob": _check(dob_raw.translate(_OCR_NUM_FIX), dob_chk),
        "expiry": _check(exp_raw.translate(_OCR_NUM_FIX), exp_chk),
        "personal_number": None,
        "composite": None,
    }
    if len(line1) >= 30 and len(line2) >= 30:
        composite_src = line1[5:30] + line2[0:7] + line2[8:15] + line2[18:29]
        checks["composite"] = _check(composite_src, line2[29])
    return {
        "format": "TD1", "doc_type": line1[0:2].strip("<"),
        "issuer": line1[2:5].replace("<", ""), "nationality": nat,
        "sex": sex, "dob": dob, "expiry": exp,
        "surname": surname, "givens": givens,
        "document_number": number_raw.strip("<"),
        "checks": checks, "ocr_corrections": False,
    }


def _ocr_mrz_strip(path: str) -> str:
    """MRZ-targeted OCR pass: crop the BOTTOM strip of each page (the MRZ
    always sits there), upscale, and run tesseract with a hard character
    whitelist (A-Z, 0-9, '<' — the full OCR-B alphabet of a machine-readable
    zone). The generic full-page pass misreads MRZ fillers/digits on phone
    photos ('«' for '4', lowercase bleed); the whitelist eliminates exactly
    that confusion class. Measured on the real 10-JPG reference set — the
    generic pass yields ZERO parseable data lines, this pass is what makes
    mrz_verify work on photographed passports."""
    try:
        from engine.tools.ocr_tools import _require_tesseract, _load_pages
        pyt, terr = _require_tesseract()
        if pyt is None:
            return ""
        pages, _ = _load_pages(path, "1-3")
        out = []
        cfg = ("--psm 6 -c tessedit_char_whitelist="
               "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")
        for _idx, img in pages:
            w, h = img.size
            # Bottom strips (scanned documents) + the full frame (phone photos
            # frame the passport anywhere in the image, not bottom-aligned).
            for top_frac in (0.60, 0.45, 0.0):
                crop = img.crop((0, int(h * top_frac), w, h))
                if crop.size[0] < 1600:  # upscale small crops for OCR-B
                    scale = 1600 / crop.size[0]
                    crop = crop.resize((1600, int(crop.size[1] * scale)))
                try:
                    txt = pyt.image_to_string(crop, lang="ocrb+eng",
                                              config=cfg)
                except Exception:
                    txt = pyt.image_to_string(crop, lang="eng", config=cfg)
                if txt.strip():
                    out.append(txt)
        return "\n".join(out)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Text acquisition (path → text) — OCR for images/PDF, plain read for text
# ---------------------------------------------------------------------------
def _read_source_text(path: str, lang: str) -> tuple[str, str, str]:
    """Return (deterministic_text, model_text, source_kind). Images/PDFs get
    tesseract (deterministic) PLUS the configured OCR model as second reading
    (the v9.333 pattern) — MRZ checksums self-validate which reading is right.
    Text-ish files are read directly."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _TEXT_EXTS:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), "", "text"
        except OSError as e:
            return "", "", f"error:{e}"
    det_text, model_text = "", ""
    try:
        from engine.tools.ocr_tools import (_require_tesseract, _ocr_all_pages,
                                            _log_pages)
        pyt, terr = _require_tesseract()
        if pyt is not None:
            results, _ = _ocr_all_pages(pyt, path, None, lang)
            _log_pages(len(results))
            det_text = "\n".join(r["text"] for r in results)
    except Exception:
        pass
    if ext in _IMAGE_EXTS:
        try:
            from engine.doc_convert import _extract_image_ocr
            model_text, _backend, _e = _extract_image_ocr(path)
        except Exception:
            model_text = ""
    elif ext == ".pdf" and not det_text.strip():
        try:
            from engine.doc_convert import _do_extract
            model_text = _do_extract(path, use_markitdown=True, caps=False) or ""
        except Exception:
            model_text = ""
    return det_text, model_text or "", "ocr"


def _resolve_path(path: str, tool: str):
    from engine.tools.ocr_tools import _resolve_input
    cand = os.path.expanduser(path or "")
    # ocr_tools' resolver rejects non-image/pdf types — allow text files too.
    ext = os.path.splitext(cand)[1].lower()
    if ext in _TEXT_EXTS:
        tries = [cand] if os.path.isabs(cand) else [os.path.abspath(cand)]
        if not os.path.isabs(cand):
            try:
                from engine.tools.file_tools import _resolve_artifact_dir
                adir, _ = _resolve_artifact_dir()
                if adir:
                    tries.append(os.path.join(adir, cand))
            except Exception:
                pass
        for t in tries:
            if os.path.isfile(t):
                return t, None
        return None, _err(f"{tool}: file not found: {path}")
    return _resolve_input(path, tool)


def _anon(text: str, src: str) -> str:
    try:
        import brain as _brain
        return _brain._gdpr_anon_tool_text(text, src)
    except Exception:
        return text


def _age_years(dob: _dt.date, ref: _dt.date | None = None) -> int:
    ref = ref or _dt.date.today()
    return ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))


# ===========================================================================
# TOOLS
# ===========================================================================
def tool_mrz_verify(args: dict) -> str:
    """Parse the machine-readable zone (TD1/TD2/TD3) of an ID document and
    verify ALL ICAO-9303 check digits SERVER-SIDE. Returns PII-free verdicts —
    no document number, no name, no raw DOB (age only). Prefers `path` (raw
    pixels/bytes on disk); `text` only when no file is available."""
    text_arg = (args.get("text") or "").strip()
    path_arg = (args.get("path") or "").strip()
    if not path_arg and not text_arg:
        return _err("mrz_verify: provide `path` (preferred — an image/PDF/"
                    "text file of the document) or `text` (raw MRZ lines).")
    sources = []
    if path_arg:
        path, err = _resolve_path(path_arg, "mrz_verify")
        if err:
            return err
        ext = os.path.splitext(path)[1].lower()
        strip_parsed = None
        if ext in _IMAGE_EXTS or ext == ".pdf":
            strip = _ocr_mrz_strip(path)
            if strip.strip():
                sources.append(("ocr_mrz_strip", strip))
                strip_parsed = parse_mrz(strip)
        # The whitelist strip pass is the strong reader for MRZ (measured on
        # the reference set) — when it already verifies the 3 core check
        # digits, skip the expensive full-page + vision-model reads.
        full_hit = bool(strip_parsed) and all(
            strip_parsed["checks"].get(k) is True
            for k in ("document_number", "dob", "expiry"))
        if not full_hit:
            det, model, kind = _read_source_text(
                path, args.get("lang") or "deu+eng")
            if kind.startswith("error:"):
                return _err(f"mrz_verify: cannot read {path_arg}: {kind[6:]}")
            if det.strip():
                sources.append(("ocr", det))
            if model.strip():
                sources.append(("model", model))
    if text_arg:
        sources.append(("text", text_arg))
    if not sources:
        return _err("mrz_verify: no text could be extracted from the file "
                    "(is the OCR engine installed?).")

    # Try every reading; the checksums SELF-VALIDATE which one is correct —
    # prefer the parse with the most passing check digits.
    best, best_src, best_score = None, "", -1
    for src_kind, text in sources:
        parsed = parse_mrz(text)
        if not parsed:
            continue
        score = sum(1 for v in parsed["checks"].values() if v is True)
        if score > best_score:
            best, best_src, best_score = parsed, src_kind, score
    if best is None:
        return _ok({"mrz_found": False,
                    "note": "No machine-readable zone detected in the "
                            "document text/OCR."})

    checks = best["checks"]
    checkable = [v for v in checks.values() if v is not None]
    today = _dt.date.today()
    # all_valid needs ≥3 verifiable check digits (number, dob, expiry) to
    # mean anything — a single surviving checksum on a garbled photo must
    # not read as "document verified". Partial reads say so explicitly.
    out = {
        "mrz_found": True,
        "format": best["format"],
        "doc_type": best["doc_type"],
        "issuer": best["issuer"],
        "nationality": best["nationality"],
        "sex": best["sex"],
        "checksums": checks,
        "checksums_checkable": len(checkable),
        "all_valid": (all(checkable) if len(checkable) >= 3 else None),
        "source": best_src,
    }
    if len(checkable) < 3:
        out["partial"] = True
        out["note"] = ("MRZ only partially readable on this image — "
                       f"{len(checkable)} of 5 check digits verifiable "
                       f"({sum(1 for v in checkable if v)} valid). Do NOT "
                       "treat unreadable fields as forgery evidence; try a "
                       "higher-quality scan of the document.")
    if best.get("ocr_corrections"):
        out["ocr_corrections_applied"] = True
    if best["expiry"]:
        out["expiry_state"] = "valid" if best["expiry"] >= today else "expired"
        out["expiry_month"] = best["expiry"].strftime("%Y-%m")
        out["expiry_date"] = best["expiry"].isoformat()  # doc lifecycle, low id-power
    if best["dob"]:
        out["age_years"] = _age_years(best["dob"])
    return _ok(out)


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTHS.update({"mär": 3, "mrz": 3, "mai": 5, "okt": 10, "dez": 12, "jun": 6,
                "jul": 7})

_DATE_PATTERNS = [
    # ISO / EXIF
    (re.compile(r"\b(\d{4})[:\-](\d{2})[:\-](\d{2})\b"), ("y", "m", "d")),
    # EU dot/dash: 27.01.2017 / 27-01-2017
    (re.compile(r"\b(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})\b"), ("d", "m", "y")),
    # US slash: 01/27/2017
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), ("m", "d", "y")),
    # Textual month: 5 FEB 1947 · 26. Jan 2027
    (re.compile(r"\b(\d{1,2})\.?\s*([A-Za-zÄÖÜäöü]{3,9})\.?\s*(\d{4})\b"),
     ("d", "mon", "y")),
]


def parse_date_value(s: str) -> _dt.date | None:
    """Parse one date string in the formats that occur on ID documents /
    EXIF: ISO, EXIF (2026:07:02), EU dot, US slash, textual months
    (EN + DE, `5 FEB 1947`, `26. Jan 2027`)."""
    s = (s or "").strip()
    for rx, order in _DATE_PATTERNS:
        m = rx.search(s)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        if "mon" in parts:
            mon = _MONTHS.get(parts["mon"][:3].lower())
            if not mon:
                continue
            parts["m"] = str(mon)
        try:
            d = _dt.date(int(parts["y"]), int(parts["m"]), int(parts["d"]))
        except (ValueError, KeyError):
            continue
        return d
    return None


def _add_years(d: _dt.date, n: int) -> _dt.date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # Feb 29 → Feb 28
        return d.replace(year=d.year + n, day=28)


def _human_delta_dates(a: _dt.date, b: _dt.date) -> str:
    """CALENDAR-exact human-readable gap: '9 days', '10y - 1d', '-5 days'.
    A plain days//365 approximation misreports the reference case
    (2017-01-27 → 2027-01-26 is exactly 10y − 1d, but 3651 days) — and a
    wrong '10y + 1d' would read as a forged validity span."""
    days = (b - a).days
    sign = "-" if days < 0 else ""
    if days < 0:
        a, b = b, a
        days = -days
    years = 0
    while _add_years(a, years + 1) <= b:
        years += 1
    if years == 0:
        return f"{sign}{days} days"
    rest = (b - _add_years(a, years)).days
    if rest == 0:
        return f"{sign}{years}y"
    over = (_add_years(a, years + 1) - b).days
    if over <= 31 and over < rest:
        return f"{sign}{years + 1}y - {over}d"
    if rest <= 31:
        return f"{sign}{years}y + {rest}d"
    return f"{sign}{years}y {rest}d"


def _resolve_date_source(src: dict, lang: str):
    """One doc_dates_check source → (name, date|None, kind, error|None)."""
    name = (src.get("name") or "").strip() or "unnamed"
    if src.get("date"):
        d = parse_date_value(str(src["date"]))
        return (name, d, "value",
                None if d else f"unparseable date: {src['date']!r}")
    if not src.get("path"):
        return name, None, "value", "source needs `date` or `path`"
    path, err = _resolve_path(str(src["path"]), "doc_dates_check")
    if err:
        return name, None, "path", f"file not found: {src['path']}"
    select = (src.get("select") or "").strip().lower()
    ext = os.path.splitext(path)[1].lower()
    if not select:
        select = "exif_datetime" if ext in _IMAGE_EXTS else "mrz_expiry"
    if select == "file_mtime":
        return (name, _dt.date.fromtimestamp(os.path.getmtime(path)),
                "file_mtime", None)
    if select == "exif_datetime":
        try:
            from PIL import Image
            img = Image.open(path)
            exif = img.getexif() or {}
            # 36867 DateTimeOriginal, 306 DateTime
            raw = None
            try:
                raw = exif.get_ifd(0x8769).get(36867)
            except Exception:
                pass
            raw = raw or exif.get(306) or exif.get(36867)
            if raw:
                d = parse_date_value(str(raw))
                return (name, d, "exif_datetime",
                        None if d else f"unparseable EXIF date: {raw!r}")
            return name, None, "exif_datetime", "no EXIF date present"
        except Exception as e:
            return name, None, "exif_datetime", f"EXIF read failed: {e}"
    if select in ("mrz_dob", "mrz_expiry"):
        det, model, _ = _read_source_text(path, lang)
        for text in (det, model):
            parsed = parse_mrz(text) if text.strip() else None
            if parsed:
                d = parsed["dob"] if select == "mrz_dob" else parsed["expiry"]
                return (name, d, select,
                        None if d else "MRZ found but date unreadable")
        return name, None, select, "no MRZ found in document"
    return name, None, select, f"unknown select: {select!r}"


def tool_doc_dates_check(args: dict) -> str:
    """Compute date RELATIONS server-side instead of having the model do
    arithmetic on (possibly pseudonymised) absolute values: validity vs
    today, pairwise gaps (renewal gap, validity span, photo-vs-doc date).
    Sources are named dates and/or paths (EXIF / MRZ / mtime)."""
    sources = args.get("sources")
    if not isinstance(sources, list) or len(sources) < 1:
        return _err("doc_dates_check: sources=[{name, date | path[, select]}] "
                    "is required (select: exif_datetime | mrz_dob | "
                    "mrz_expiry | file_mtime).")
    if len(sources) > 12:
        return _err("doc_dates_check: too many sources (max 12).")
    lang = args.get("lang") or "deu+eng"
    resolved, errors = [], []
    for src in sources:
        if not isinstance(src, dict):
            continue
        name, d, kind, err = _resolve_date_source(src, lang)
        if err:
            errors.append({"name": name, "error": err})
        if d is not None:
            resolved.append({"name": name, "kind": kind, "date": d})

    today = _dt.date.today()
    out_resolved = []
    for r in resolved:
        entry = {"name": r["name"], "kind": r["kind"],
                 "vs_today": "past" if r["date"] < today
                 else ("today" if r["date"] == today else "future")}
        # DOB-ish dates never leave the tool raw — age only (PII discipline).
        if r["kind"] == "mrz_dob" or "birth" in r["name"].lower() \
                or "dob" in r["name"].lower() or "geburt" in r["name"].lower():
            entry["age_years"] = _age_years(r["date"])
        else:
            entry["date"] = r["date"].isoformat()
        out_resolved.append(entry)

    # Pairs: explicit (a, b by name) or all consecutive in chronological order.
    pairs_arg = args.get("pairs")
    by_name = {r["name"]: r for r in resolved}
    checks = []
    if isinstance(pairs_arg, list) and pairs_arg:
        for p in pairs_arg:
            if not isinstance(p, dict):
                continue
            a, b = by_name.get(p.get("a")), by_name.get(p.get("b"))
            if not a or not b:
                checks.append({"pair": f"{p.get('a')} → {p.get('b')}",
                               "error": "unresolved source name"})
                continue
            days = (b["date"] - a["date"]).days
            checks.append({"pair": f"{a['name']} → {b['name']}",
                           "delta_days": days,
                           "delta": _human_delta_dates(a["date"], b["date"]),
                           "order": "a_before_b" if days > 0
                           else ("same_day" if days == 0 else "a_after_b")})
    else:
        chron = sorted(resolved, key=lambda r: r["date"])
        for a, b in zip(chron, chron[1:]):
            days = (b["date"] - a["date"]).days
            checks.append({"pair": f"{a['name']} → {b['name']}",
                           "delta_days": days,
                           "delta": _human_delta_dates(a["date"], b["date"])})

    payload = {"resolved": out_resolved, "checks": checks,
               "today": today.isoformat()}
    if errors:
        payload["errors"] = errors
    return _ok(payload)


def tool_identity_consistency(args: dict) -> str:
    """Server-side identity-field comparison across documents: extracts the
    MRZ identity, filename name-form and birth-context dates from each source
    and reports MATCH VERDICTS (name normalised across case/order/initials/
    MRZ-form, fuzzy for OCR garble) — the 'identical personal data across N
    years' check without shipping the personal data through the LLM."""
    paths = args.get("paths")
    if not isinstance(paths, list) or len(paths) < 2:
        return _err("identity_consistency: paths=[…] with at least 2 document "
                    "paths is required.")
    if len(paths) > 8:
        return _err("identity_consistency: too many sources (max 8).")
    lang = args.get("lang") or "deu+eng"

    from engine import identity as _id
    per_source = []
    for p in paths:
        path, err = _resolve_path(str(p), "identity_consistency")
        if err:
            per_source.append({"source": os.path.basename(str(p)),
                               "error": "file not found"})
            continue
        # MRZ-targeted strip pass FIRST (the strong reader — see mrz_verify);
        # only fall back to the generic + model reads when it finds nothing.
        parsed = None
        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_EXTS or ext == ".pdf":
            strip = _ocr_mrz_strip(path)
            if strip.strip():
                parsed = parse_mrz(strip)
        if not parsed:
            det, model, _ = _read_source_text(path, lang)
            for text in (det, model):
                if text.strip():
                    parsed = parse_mrz(text)
                    if parsed:
                        break
        # Filename name-form: strip extension + digit runs, keep name tokens.
        fname = os.path.splitext(os.path.basename(path))[0]
        fname_form = " ".join(_id.name_tokens(fname))
        entry = {"source": os.path.basename(path)}
        names = []
        if parsed:
            mrz_name = f"{parsed['surname']}, {parsed['givens']}".strip(", ")
            if mrz_name:
                names.append(("mrz", mrz_name))
            entry["mrz"] = {
                "present": True,
                "doc_number_len": len(parsed["document_number"]),
                "dob": parsed["dob"], "expiry": parsed["expiry"],
                "issuer": parsed["issuer"],
            }
        if fname_form and len(fname_form.split()) >= 2:
            names.append(("filename", fname_form))
        entry["_names"] = names
        entry["_doc_number"] = parsed["document_number"] if parsed else ""
        per_source.append(entry)

    # ── Name consistency across sources ──────────────────────────────────
    all_forms, form_src = [], []
    for e in per_source:
        for kind, form in e.get("_names", []):
            all_forms.append(form)
            form_src.append((e["source"], kind))
    clusters = _id.cluster_names(all_forms, fuzzy=True)
    name_result = {"forms_found": len(all_forms),
                   "distinct_persons": len(clusters)}
    if clusters:
        main = max(clusters, key=len)
        name_result["largest_cluster"] = len(main)
        # Variants that did NOT join the main cluster — the analyst needs the
        # deviating surface form + where it came from (result text passes
        # through the GDPR tool-result seam like every read tool).
        discrepancies = []
        for cl in clusters:
            if cl is main:
                continue
            for idx in cl:
                discrepancies.append({
                    "source": form_src[idx][0], "via": form_src[idx][1],
                    "reads": all_forms[idx]})
        if discrepancies:
            name_result["discrepancies"] = discrepancies

    # ── DOB consistency (values never leave; equality only) ──────────────
    dobs = [(e["source"], e["mrz"]["dob"]) for e in per_source
            if e.get("mrz", {}).get("present") and e["mrz"].get("dob")]
    dob_result = {"sources_with_dob": len(dobs)}
    if dobs:
        distinct = {d.isoformat() for _, d in dobs}
        dob_result["all_match"] = len(distinct) == 1
        if len(distinct) == 1:
            dob_result["age_years"] = _age_years(dobs[0][1])
        else:
            dob_result["distinct_values"] = len(distinct)

    # ── Document-number chain (lengths/equality only, never the number) ──
    numbers = [(e["source"], e["_doc_number"]) for e in per_source
               if e.get("_doc_number")]
    chain = {"documents_with_number": len(numbers),
             "distinct_numbers": len({n for _, n in numbers})}
    expiries = sorted([e["mrz"]["expiry"] for e in per_source
                       if e.get("mrz", {}).get("present")
                       and e["mrz"].get("expiry")])
    if len(expiries) >= 2:
        chain["expiry_chain"] = " → ".join(d.strftime("%Y-%m") for d in expiries)

    # Strip internals + shape per-source output.
    sources_out = []
    for e in per_source:
        o = {"source": e["source"]}
        if e.get("error"):
            o["error"] = e["error"]
        if e.get("mrz", {}).get("present"):
            o["mrz_present"] = True
        forms = [k for k, _ in e.get("_names", [])]
        if forms:
            o["name_forms"] = forms
        sources_out.append(o)

    payload = {
        "sources_compared": len(per_source),
        "sources": sources_out,
        "name": name_result,
        "dob": dob_result,
        "document_numbers": chain,
    }
    # Discrepancy `reads` strings are the ONLY place raw name variants can
    # appear — run the result through the GDPR tool-result seam like every
    # read-style tool.
    return _anon(_ok(payload),
                 f"identity_consistency:{len(per_source)} sources")
