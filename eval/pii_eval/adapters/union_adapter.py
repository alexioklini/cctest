"""Detector: ours ∪ M4 (parallel union, full-text context).

The design the eval pointed at: keep our deterministic detector as the backbone
and use M4-7B as a parallel RECALL booster. NOT an adjudicator — M4 runs over the
SAME full text independently, and we UNION the findings, with one rule:

    ours wins on overlap.

i.e. M4 may only ADD a (value, type) that ours did not already find. It can
never override a checksummed/secret hit from ours, nor suppress one — a key-leak
block or a Luhn-valid IBAN must stay deterministic. This caps M4's downside to
"adds a few false positives" while letting it recover the recall ours misses
(phone, national_id, names ours' spaCy dropped).

Value-overlap is checked on the normalized value (separator-insensitive for
structured types), so "DE89 3704…" from ours and "DE8937 04…" from M4 dedupe.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
from common import Finding, normalize_value  # noqa: E402
import ours_adapter  # noqa: E402
import m4_llm_adapter  # noqa: E402


def available() -> tuple[bool, str]:
    ok1, m1 = ours_adapter.available()
    if not ok1:
        return False, f"ours: {m1}"
    ok2, m2 = m4_llm_adapter.available()
    if not ok2:
        return False, f"m4: {m2}"
    return True, ""


def detect(text: str, temperature: float = 0.0) -> list[Finding]:
    ours = ours_adapter.detect_full(text)
    # set of normalized values ours already claimed — ours wins on overlap
    claimed = {normalize_value(f.value, f.type) for f in ours}
    merged = list(ours)
    try:
        m4 = m4_llm_adapter.detect(text, temperature=temperature)
    except Exception:
        m4 = []
    for f in m4:
        if f.type == "other":
            continue
        nv = normalize_value(f.value, f.type)
        # M4 only adds values ours didn't find (in ANY type — ours' typing wins)
        if nv in claimed:
            continue
        # also skip if ours found this value under a different type already
        if any(normalize_value(o.value, o.type) == nv for o in ours):
            continue
        merged.append(f)
        claimed.add(nv)
    return merged
