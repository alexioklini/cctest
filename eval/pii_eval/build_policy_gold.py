#!/usr/bin/env python3
"""Build gold PII annotations for the kg-real-policies corpus.

These are real German bank policy docs. They are PII-SPARSE (regulatory prose:
process descriptions, role names, vendor/product names, occasional dates). So
the gold here mostly tests:
  * RECALL on the few real person-names / dates / contacts that appear, and
  * PRECISION / false-positive behaviour on the dense ORG/product/role text
    that detectors love to mis-flag as persons or addresses.

Gold strategy (documented & loud, per the user's "verify, don't assert" rule):
  1. Slice each .md into ~1500-char windows (skip the brain-source HTML comment
     header) so docs are scoreable units and the LLM sees focused context.
  2. Pre-label each window with a STRONG model (default mistral-medium via the
     sidecar) using the same strict schema as the eval.
  3. DETERMINISTIC verification: keep a gold item only if its value is an exact
     substring of the window (drops hallucinated values). Normalize type to the
     canonical schema.
  4. Emit data/policy_gold.jsonl as {id, text, gold, source_file}.

This gold is MACHINE-PRELABELLED, not hand-verified end to end. The report
flags it as such. For a load-bearing decision, spot-check a sample (the runner
prints matched/missed/false-pos per doc). The handcrafted corpus carries the
exact-gold weight; policy carries the realism/FP weight.

Usage:
  python3 eval/pii_eval/build_policy_gold.py --max-windows 80
  PII_GOLD_MODEL=CLIProxyAPI/mistral-medium-3.5 python3 eval/pii_eval/build_policy_gold.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import CANON_TYPES, LLM_MAP  # noqa: E402

# Gold pre-label model. We call it DIRECTLY via the provider's native Anthropic
# /v1/messages endpoint (the Brain sidecar /turn path returned empty completions
# in this environment). Default = the M4 local model, since cloud (CLIProxyAPI)
# was returning api_error here.
#
# METHODOLOGY CAVEAT (loud, per the user's verify-don't-assert rule): when the
# gold model == a detector under test (M4), the policy-corpus gold is biased
# toward M4. We mitigate by (a) deterministic substring verification, (b) keeping
# the HANDCRAFTED corpus — whose gold is exact by construction, LLM-free — as the
# authoritative recall/precision number, and (c) treating policy results as a
# realism/false-positive signal, not a recall verdict. To rebuild gold with an
# unbiased judge once cloud is back: PII_GOLD_PROVIDER=CLIProxyAPI
# PII_GOLD_MODEL=CLIProxyAPI/mistral-medium-3.5.
GOLD_MODEL = os.environ.get("PII_GOLD_MODEL", "Qwen2.5-7B-Instruct-4bit")
GOLD_PROVIDER = os.environ.get("PII_GOLD_PROVIDER", "Lokal-M4")
POLICY_GLOB = os.environ.get(
    "PII_POLICY_GLOB",
    "/Users/alexander/Documents/kg-real-policies/**/.brain-extracted/**/*.md")
OUT = os.path.join(HERE, "data", "policy_gold.jsonl")

WINDOW = 1500

_SYSTEM = (
    "Du bist ein extrem präziser PII-Annotator für deutsche Banktexte. "
    "Extrahiere NUR echte personenbezogene Daten und Geheimnisse, die WÖRTLICH "
    "im Text stehen. Gib AUSSCHLIESSLICH ein JSON-Array zurück. "
    "Element: {\"value\": <exakter Ausschnitt>, \"type\": <Typ>}. "
    "Typen: " + ", ".join(CANON_TYPES) + ". "
    "WICHTIG: organisation NUR für konkrete Firmen-/Produkt-/Systemnamen "
    "(z.B. Bloomberg, SWIFT, Foconis), NICHT für generische Rollen wie "
    "'Leitung Compliance' oder Abteilungen. name NUR für echte Personennamen, "
    "NICHT für Rollen/Funktionen. date NUR für konkrete Datumsangaben. "
    "Im Zweifel weglassen. Wenn nichts: []."
)


def _resolve_provider():
    c = json.load(open(os.environ.get("BRAIN_CONFIG", "config.json"), encoding="utf-8"))
    p = c["providers"][GOLD_PROVIDER]
    return p.get("api_key") or "", (p.get("base_url") or "").rstrip("/")


def _call(text):
    api_key, base = _resolve_provider()
    url = base.rstrip("/") + "/messages"
    payload = {
        "model": GOLD_MODEL, "max_tokens": 1500, "temperature": 0.0,
        "system": _SYSTEM,
        "messages": [{"role": "user",
                      "content": f"Text:\n\"\"\"\n{text}\n\"\"\"\n\nJSON-Array:"}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    parts = []
    for blk in d.get("content", []) or []:
        if isinstance(blk, dict) and blk.get("type") == "text":
            parts.append(blk.get("text", ""))
    return "".join(parts)


def _parse_and_verify(raw, window):
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    gold = []
    seen = set()
    for item in arr:
        if not isinstance(item, dict):
            continue
        val = str(item.get("value", "")).strip()
        t = LLM_MAP.get(str(item.get("type", "")).strip().lower(), "other")
        if not val or t == "other":
            continue
        if val not in window:          # deterministic anti-hallucination gate
            continue
        key = (val.lower(), t)
        if key in seen:
            continue
        seen.add(key)
        gold.append({"value": val, "type": t})
    return gold


def _strip_header(md):
    # drop the leading <!-- brain-source ... --> comment block
    return re.sub(r"^(<!--.*?-->\s*)+", "", md, flags=re.DOTALL).strip()


def _windows(text, size=WINDOW):
    text = text.strip()
    if len(text) <= size:
        if text:
            yield text
        return
    paras = text.split("\n")
    buf, n = [], 0
    for p in paras:
        if n + len(p) > size and buf:
            yield "\n".join(buf)
            buf, n = [], 0
        buf.append(p); n += len(p) + 1
    if buf:
        yield "\n".join(buf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-windows", type=int, default=80,
                    help="cap total windows (cost guard); 0 = all")
    ap.add_argument("--min-chars", type=int, default=200,
                    help="skip windows shorter than this")
    args = ap.parse_args()

    files = sorted(glob.glob(POLICY_GLOB, recursive=True))
    print(f"[policy-gold] {len(files)} extracted .md files")
    if not files:
        print(f"[policy-gold] glob matched nothing: {POLICY_GLOB}", file=sys.stderr)
        sys.exit(1)

    out = []
    total_windows = 0
    for fp in files:
        try:
            md = _strip_header(open(fp, encoding="utf-8").read())
        except Exception as e:
            print(f"  skip {fp}: {e}")
            continue
        base = os.path.basename(fp).replace(".pdf.md", "").replace(".md", "")
        for wi, w in enumerate(_windows(md)):
            if len(w) < args.min_chars:
                continue
            if args.max_windows and total_windows >= args.max_windows:
                break
            total_windows += 1
            doc_id = f"{base}__w{wi}"
            try:
                gold = _parse_and_verify(_call(w), w)
            except Exception as e:
                print(f"  [{doc_id}] LLM error: {e}")
                gold = []
            out.append({"id": doc_id, "text": w, "gold": gold, "source_file": fp})
            ng = len(gold)
            if ng:
                print(f"  [{doc_id}] {ng} gold: " +
                      ", ".join(f"{g['value']!r}:{g['type']}" for g in gold[:5]))
        if args.max_windows and total_windows >= args.max_windows:
            print(f"[policy-gold] hit --max-windows={args.max_windows} cap")
            break

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_gold = sum(len(r["gold"]) for r in out)
    n_pos = sum(1 for r in out if r["gold"])
    print(f"\n[policy-gold] wrote {len(out)} windows ({n_pos} with PII, "
          f"{n_gold} gold items total) -> {OUT}")
    print("[policy-gold] NOTE: machine-prelabelled + substring-verified, "
          "NOT hand-verified. Spot-check before load-bearing decisions.")


if __name__ == "__main__":
    main()
