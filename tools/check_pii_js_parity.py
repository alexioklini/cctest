#!/usr/bin/env python3
"""PII scanner Python <-> JS drift checker (U5).

The PII/GDPR scanner exists in two mirrored implementations that CLAUDE.md
says "must stay in sync" by hand:

  * Python (source of truth): `engine/pii_ner.py`
      - `_pii_rules()`                -> the regex rule catalogue (rule ids)
      - `PII_RULE_CATEGORIES`         -> rule_id -> category
      - `PII_DEFAULT_CATEGORY_ACTIONS`-> category -> default action
  * JavaScript (browser mirror): `web/js/utils.js` -> `PIIScanner`
      - `ruleCategories`              -> rule_id -> category
      - `defaultCategoryActions`      -> category -> default action

The two regex BODIES legitimately differ (Python `re` vs JS `RegExp` dialect),
so we do NOT diff regex source. What silently drifts in practice is the
metadata: a new Python rule gets added without a matching JS category entry,
or a category action diverges. This script catches exactly that.

It is a *checker*, not a generator — far less risky than rewriting JS by hand
from a tool, and catches the real failure mode (a missing/mismatched mapping).

Exit 0 = in sync. Exit 1 = drift found (prints a diff). Exit 2 = parse error.

Usage:  python3 tools/check_pii_js_parity.py
Run it in CI / the refactor gate to fail loud when the mirror drifts.
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS_PATH = os.path.join(_ROOT, "web", "js", "utils.js")


def _load_python():
    """Import the Python source-of-truth maps + the live rule-id set."""
    sys.path.insert(0, _ROOT)
    from engine import pii_ner  # noqa: E402
    rule_ids = [r["id"] for r in pii_ner._pii_rules()]
    return (
        dict(pii_ner.PII_RULE_CATEGORIES),
        dict(pii_ner.PII_DEFAULT_CATEGORY_ACTIONS),
        rule_ids,
    )


def _extract_js_object(text: str, key: str) -> dict[str, str]:
    """Pull a flat `{ a:'x', b:'y' }` object literal out of `PIIScanner`.

    Only handles the string-valued, single-level maps we care about
    (ruleCategories / defaultCategoryActions) — both are written as plain
    `ident:'literal'` pairs. Comments (`// ...`) are stripped first.
    """
    m = re.search(re.escape(key) + r"\s*:\s*\{", text)
    if not m:
        raise ValueError(f"JS key '{key}' not found in {_JS_PATH}")
    # Walk braces from the opening { to find the matching close.
    start = m.end() - 1
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                body = text[start + 1:i]
                break
    else:
        raise ValueError(f"unterminated JS object for '{key}'")
    # Strip line comments.
    body = re.sub(r"//[^\n]*", "", body)
    out: dict[str, str] = {}
    for km, vm in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*'([^']*)'", body):
        out[km] = vm
    return out


def _diff_map(name: str, py: dict, js: dict) -> list[str]:
    errs: list[str] = []
    for k in sorted(set(py) - set(js)):
        errs.append(f"  [{name}] '{k}' in Python but MISSING from JS (-> {py[k]!r})")
    for k in sorted(set(js) - set(py)):
        errs.append(f"  [{name}] '{k}' in JS but MISSING from Python (-> {js[k]!r})")
    for k in sorted(set(py) & set(js)):
        if py[k] != js[k]:
            errs.append(f"  [{name}] '{k}' MISMATCH: Python={py[k]!r} JS={js[k]!r}")
    return errs


def main() -> int:
    try:
        py_cats, py_actions, py_rule_ids = _load_python()
    except Exception as e:  # pragma: no cover - import-time failure
        print(f"FAIL: could not load Python PII source: {e}")
        return 2
    try:
        js_text = open(_JS_PATH, encoding="utf-8").read()
        js_cats = _extract_js_object(js_text, "ruleCategories")
        js_actions = _extract_js_object(js_text, "defaultCategoryActions")
    except Exception as e:
        print(f"FAIL: could not parse JS PIIScanner: {e}")
        return 2

    errs: list[str] = []
    errs += _diff_map("ruleCategories", py_cats, js_cats)
    errs += _diff_map("defaultCategoryActions", py_actions, js_actions)

    # Every regex rule the Python scanner actually emits must have a JS category
    # (otherwise the browser scanner can't classify/colour a finding the server
    # would). bare_identifier + NER ids (name/address/organisation) are
    # server-only and intentionally have no client detector, but they DO need a
    # category entry — which the map diff above already covers.
    for rid in py_rule_ids:
        if rid not in js_cats:
            errs.append(f"  [rules] Python rule '{rid}' has NO JS ruleCategories entry")

    if errs:
        print("PII scanner Python<->JS DRIFT detected:\n" + "\n".join(errs))
        print("\nFix: update web/js/utils.js PIIScanner.{ruleCategories,"
              "defaultCategoryActions} to match engine/pii_ner.py.")
        return 1
    print(f"OK — PII scanner Python<->JS in sync "
          f"({len(py_cats)} rule categories, {len(py_actions)} category actions, "
          f"{len(py_rule_ids)} regex rules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
