"""GLiNER adapter. Loads a multilingual GLiNER model once and runs span
extraction with our PII label schema. Used by:
  * detector #1 (presidio_gliner) — as Presidio's NER engine substitute
  * detector #3 (ours_regex + gliner) — GLiNER replaces spaCy for name/addr/org

We call GLiNER directly (not through Presidio) because the eval needs the raw
spans either way; the Presidio detector reuses these spans for its NER layer.

Model: defaults to "urchade/gliner_multi_pii-v1" (the multilingual PII variant).
Override with env PII_GLINER_MODEL. GLiNER2-PII (fastino) can be slotted here if
its HF id is available in the eval venv.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common import Finding, GLINER_MAP  # noqa: E402

# Labels we ask GLiNER to extract. GLiNER is LABEL-WORDING SENSITIVE: verified
# 2026-06-23 that "person" recalls German names reliably while "person name"
# misses ~half — so we use the wordings that perform best (this gives GLiNER its
# fair best-case, the same courtesy as running Presidio on de_core_news_lg).
# GLINER_MAP in common.py maps these back to our canonical schema.
GLINER_LABELS = [
    "person", "address", "organization", "email", "phone number",
    "iban", "credit card number", "tax id", "passport number", "national id number",
    "api key", "password", "ip address", "date",
]

_MODEL = None
_MODEL_ID = os.environ.get("PII_GLINER_MODEL", "urchade/gliner_multi_pii-v1")
_THRESHOLD = float(os.environ.get("PII_GLINER_THRESHOLD", "0.45"))


def available() -> tuple[bool, str]:
    try:
        import gliner  # noqa: F401
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _load():
    global _MODEL
    if _MODEL is None:
        from gliner import GLiNER
        _MODEL = GLiNER.from_pretrained(_MODEL_ID)
    return _MODEL


def detect(text: str) -> list[Finding]:
    model = _load()
    out: list[Finding] = []
    # GLiNER has a token window; chunk long docs by paragraph to stay safe.
    for chunk in _chunks(text, 1800):
        try:
            ents = model.predict_entities(chunk, GLINER_LABELS, threshold=_THRESHOLD)
        except Exception:
            continue
        for e in ents:
            canon = GLINER_MAP.get((e.get("label") or "").lower(), "other")
            val = (e.get("text") or "").strip()
            if val:
                out.append(Finding(value=val, type=canon))
    return out


def detect_ner_only(text: str) -> list[Finding]:
    """Only name/address/organisation spans — for pairing with our regex layer
    (detector #3), where regex already owns the structured categories."""
    return [f for f in detect(text) if f.type in {"name", "address", "organisation"}]


def _chunks(text: str, size: int):
    if len(text) <= size:
        yield text
        return
    buf = []
    n = 0
    for para in text.split("\n"):
        if n + len(para) > size and buf:
            yield "\n".join(buf)
            buf, n = [], 0
        buf.append(para)
        n += len(para) + 1
    if buf:
        yield "\n".join(buf)
