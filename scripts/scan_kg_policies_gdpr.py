#!/usr/bin/env python3
"""One-off: run the LIVE GDPR rules over the KG-policy project's input docs and
report the worst per-doc action (warn/block), mirroring the non-interactive
mining decision path.

Mirrors the non-interactive KG mining decision exactly. Reads the
`.brain-extracted/<name>.<ext>.md` COMPANION files — i.e. the verbatim text the
KG miner actually saw (markitdown/OCR already applied at mine time) — rather than
re-extracting (a standalone _do_extract gives near-empty text on the scanned
image PDFs, which would falsely read as "clean"). Then:
  - strip the brain frontmatter (as _process_source does via
    _strip_brain_frontmatter),
  - scan the FULL text once (9.93 whole-doc scope so min_occurrences counts
    distinct values across the document),
  - _pii_scan_text with the live config (force-enabled here; the live server has
    it disabled, but rules/categories/min_occurrences come from config.json),
  - _pii_worst_action over the findings.

Standalone import of brain is fine here: _get_gdpr_scanner_config reads
config.json directly (NOT server_config), and _pii_scan_text is pure.
"""
import os
import re
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402

FOLDERS = [
    "/Users/alexander/Documents/kg-real-policies/4 IT  & Core Banking",
    "/Users/alexander/Documents/kg-real-policies/20 Datenschutz & Informationssicherheit",
]

# Same frontmatter strip the KG path uses (drop the leading <!-- brain-* --> lines).
_FM = re.compile(r"^(?:<!--\s*brain-[^>]*-->\s*\n)+", re.MULTILINE)


def strip_frontmatter(text):
    return _FM.sub("", text, count=1).lstrip("\n")


def gather():
    """Every .brain-extracted companion .md — keyed back to its source name."""
    out = []
    for base in FOLDERS:
        ce = os.path.join(base, ".brain-extracted")
        if not os.path.isdir(ce):
            continue
        for root, _dirs, files in os.walk(ce):
            for fn in files:
                if fn.endswith(".md"):
                    out.append(os.path.join(root, fn))
    return sorted(out)


def main():
    cfg = dict(brain._get_gdpr_scanner_config())
    cfg["enabled"] = True  # force-on for the scan (does not touch live server)
    print(f"[cfg] background_pii_action={cfg.get('background_pii_action')} "
          f"server_block={cfg.get('server_block')} "
          f"min_occurrences keys={len(cfg.get('min_occurrences') or {})}", flush=True)

    docs = gather()
    print(f"[scan] {len(docs)} documents\n", flush=True)

    flagged = []   # (worst, name, category_counts)
    clean = 0
    errors = []
    cat_totals = collections.Counter()

    for path in docs:
        name = os.path.basename(path)[:-3]  # drop trailing ".md" → source name
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = strip_frontmatter(fh.read())
        except Exception as e:
            errors.append((name, f"read: {type(e).__name__}: {e}"))
            continue
        if not text or not text.strip():
            errors.append((name, "empty companion"))
            continue
        try:
            findings = brain._pii_scan_text(text, max_findings=500, cfg=cfg)
        except Exception as e:
            errors.append((name, f"scan: {type(e).__name__}: {e}"))
            continue
        if not findings:
            clean += 1
            continue
        worst = brain._pii_worst_action(findings)
        # Per-rule breakdown for the flagged docs, with the DISTINCT matched
        # values (min_occurrences gates on distinct values, so this is what
        # actually matters). Map rule -> {value: count}.
        by_rule = collections.defaultdict(collections.Counter)
        for f in findings:
            key = (f.get("rule_id") or "?", f.get("category") or "?",
                   f.get("action") or "?")
            # Authoritative matched text from the span (the _value field is not
            # reliably populated after the min_occurrences post-pass).
            val = f.get("_value")
            if not val:
                s, e = f.get("start"), f.get("end")
                if isinstance(s, int) and isinstance(e, int):
                    val = text[s:e].strip()
            by_rule[key][val or "(?)"] += 1
            cat_totals[f.get("category") or "?"] += 1
        flagged.append((worst, name, by_rule, len(findings)))

    # Report
    blocks = [f for f in flagged if f[0] == "block"]
    warns = [f for f in flagged if f[0] == "warn"]
    print("=" * 72)
    print(f"RESULT: {len(docs)} docs | clean={clean} | "
          f"warn={len(warns)} | block={len(blocks)} | errors={len(errors)}")
    print("=" * 72)

    def dump(label, items):
        if not items:
            print(f"\n## {label}: none")
            return
        print(f"\n## {label} ({len(items)}):")
        for worst, name, by_rule, total in sorted(items, key=lambda x: -x[3]):
            # distinct values = sum of distinct per rule
            distinct = sum(len(vals) for vals in by_rule.values())
            print(f"\n  • {name}  [{total} findings, {distinct} distinct]")
            for (rid, cat, act), vals in sorted(
                    by_rule.items(), key=lambda kv: -sum(kv[1].values())):
                tot = sum(vals.values())
                print(f"        {act:5} {cat:10} {rid}  "
                      f"({len(vals)} distinct, {tot} total)")
                for v, n in vals.most_common():
                    suffix = f"  ×{n}" if n > 1 else ""
                    print(f"            - {v}{suffix}")

    dump("BLOCK", blocks)
    dump("WARN", warns)

    if errors:
        print(f"\n## extraction/scan errors ({len(errors)}):")
        for name, err in errors:
            print(f"  • {name}: {err}")

    if cat_totals:
        print("\n## category totals across all flagged docs:")
        for cat, n in cat_totals.most_common():
            print(f"  {cat:14} {n}")


if __name__ == "__main__":
    main()
