#!/usr/bin/env python3
"""PII-detector shootout runner.

Compares four detection stacks on two German corpora:
  detectors:
    ours_spacy     - #2  our regex+checksums + spaCy NER     (live server)
    ours_gliner    - #3  our regex+checksums + GLiNER NER     (server regex + gliner venv)
    presidio_gliner- #1  Presidio analyzer w/ GLiNER NER      (gliner venv)
    presidio_spacy -      Presidio analyzer w/ spaCy NER       (bonus baseline)
    m4_llm         - #4  Qwen2.5-7B as sole detector          (sidecar)
  corpora:
    handcrafted    - eval/pii_eval/data/handcrafted_de.jsonl  (exact injected gold)
    policy         - kg-real-policies extracted .md            (real occurrences, LLM-prelabel gold)

Value-level precision/recall/F1, per-detector and per-category. Non-deterministic
detectors (m4_llm, and gliner if PII_GLINER_THRESHOLD jitter) run >=REPS times;
we report mean +/- sample-stdev (feedback_eval_single_run_noise: single-run
deltas <0.05 are noise).

IMPORTANT ENV / VENV NOTES
  * ours_spacy / ours_gliner need the LIVE server (BRAIN_USER/BRAIN_PASS) and
    gdpr_scanner enabled in config.json.
  * presidio_* and gliner need the eval venv (.venv_pii_eval) with presidio +
    gliner + de_core_news_lg installed.
  * m4_llm needs the sidecar (:8421) up and the Lokal-M4 provider reachable.
  Detectors whose deps are missing are SKIPPED with a loud note, not failed.

Usage:
  # full matrix (run inside .venv_pii_eval; server + sidecar must be up):
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/pii_eval/run_pii_eval.py

  # subset:
  ... run_pii_eval.py --detectors ours_spacy,m4_llm --corpora handcrafted --reps 3
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "adapters"))

from common import (Finding, score_doc, prf, mean_spread, load_jsonl,  # noqa: E402
                    CANON_TYPES)

POLICY_GLOB = os.environ.get(
    "PII_POLICY_GLOB",
    "/Users/alexander/Documents/kg-real-policies/**/.brain-extracted/**/*.md")
# Prefer the cleaned gold (deterministic filter over the M4-prelabel garbage);
# fall back to raw if the clean pass wasn't run.
_CLEAN = os.path.join(HERE, "data", "policy_gold.clean.jsonl")
POLICY_GOLD = _CLEAN if os.path.exists(_CLEAN) else os.path.join(HERE, "data", "policy_gold.jsonl")
HANDCRAFTED = os.path.join(HERE, "data", "handcrafted_de.jsonl")


# --- detector registry --------------------------------------------------------
def _det_ours_spacy():
    os.environ["PII_OURS_NAME_PRECISION"] = "0"
    import importlib, ours_adapter
    importlib.reload(ours_adapter)
    return ours_adapter.detect_full, True  # deterministic

def _det_ours_nameprec():
    os.environ["PII_OURS_NAME_PRECISION"] = "1"
    import importlib, ours_adapter
    importlib.reload(ours_adapter)
    return ours_adapter.detect_full, True

def _det_ours_gliner():
    import ours_adapter, gliner_adapter
    def detect(text):
        return ours_adapter.detect_regex_only(text) + gliner_adapter.detect_ner_only(text)
    return detect, False  # gliner nondeterministic-ish

def _det_presidio_gliner():
    os.environ["PII_PRESIDIO_NER"] = "gliner"
    import importlib, presidio_adapter
    importlib.reload(presidio_adapter)
    return presidio_adapter.detect, False

def _det_presidio_spacy():
    os.environ["PII_PRESIDIO_NER"] = "spacy"
    import importlib, presidio_adapter
    importlib.reload(presidio_adapter)
    return presidio_adapter.detect, True

def _det_m4_llm():
    import m4_llm_adapter
    return m4_llm_adapter.detect, False

def _det_ours_plus_m4():
    import union_adapter
    return union_adapter.detect, False  # M4 = nondeterministic

DETECTORS = {
    "ours_spacy": _det_ours_spacy,
    "ours_gliner": _det_ours_gliner,
    "presidio_gliner": _det_presidio_gliner,
    "presidio_spacy": _det_presidio_spacy,
    "m4_llm": _det_m4_llm,
    "ours_plus_m4": _det_ours_plus_m4,
    "ours_nameprec": _det_ours_nameprec,
}


def _availability(name):
    try:
        if name == "ours_plus_m4":
            import union_adapter; return union_adapter.available()
        if name.startswith("ours"):
            import ours_adapter; return ours_adapter.available()
        if name.startswith("presidio"):
            import presidio_adapter; return presidio_adapter.available()
        if name == "m4_llm":
            import m4_llm_adapter; return m4_llm_adapter.available()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return False, "unknown"


# --- corpora ------------------------------------------------------------------
def load_handcrafted():
    return [(d["id"], d["text"], d["gold"]) for d in load_jsonl(HANDCRAFTED)]


def load_policy():
    if not os.path.exists(POLICY_GOLD):
        print(f"[warn] policy gold {POLICY_GOLD} missing — run build_policy_gold.py first; "
              f"skipping policy corpus", flush=True)
        return []
    gold_by_id = {d["id"]: d for d in load_jsonl(POLICY_GOLD)}
    docs = []
    for d in gold_by_id.values():
        docs.append((d["id"], d["text"], d["gold"]))
    return docs


# --- run ----------------------------------------------------------------------
def run_detector(name, factory, corpus, reps):
    detect, deterministic = factory()
    n = 1 if deterministic else reps
    per_rep_overall = []   # list of (P,R,F1)
    per_rep_cat = []       # list of {cat: (P,R,F1)}
    rep_docscores = None
    for rep in range(n):
        agg_tp = agg_fp = agg_fn = 0
        cat_counts = {c: [0, 0, 0] for c in CANON_TYPES}  # tp,fp,fn
        doc_details = []
        for doc_id, text, gold in corpus:
            try:
                preds = detect(text)
            except Exception as e:
                print(f"    [{name}] doc {doc_id} rep{rep} ERROR: {e}", flush=True)
                preds = []
            sc = score_doc(doc_id, gold, preds)
            agg_tp += sc.tp; agg_fp += sc.fp; agg_fn += sc.fn
            # per-category attribution by gold type
            _attr_categories(gold, preds, cat_counts)
            doc_details.append((doc_id, sc))
        per_rep_overall.append(prf(agg_tp, agg_fp, agg_fn))
        per_rep_cat.append({c: prf(*cat_counts[c]) for c in CANON_TYPES})
        rep_docscores = doc_details
        print(f"    [{name}] rep {rep+1}/{n}: "
              f"P={per_rep_overall[-1][0]:.2f} R={per_rep_overall[-1][1]:.2f} "
              f"F1={per_rep_overall[-1][2]:.2f}", flush=True)
    return per_rep_overall, per_rep_cat, rep_docscores


def _attr_categories(gold, preds, cat_counts):
    """Per-category tp/fp/fn using the same value-match logic, scoped to one
    canonical type at a time so each category gets its own P/R/F1."""
    from common import normalize_value, _value_match, _accepts
    gold_by_cat = {}
    for g in gold:
        gold_by_cat.setdefault(g["type"], []).append(g["value"])
    pred_by_cat = {}
    for p in preds:
        if p.type != "other":
            pred_by_cat.setdefault(p.type, []).append(p.value)
    cats = set(gold_by_cat) | set(pred_by_cat)
    for c in cats:
        if c not in cat_counts:
            continue
        gvals = gold_by_cat.get(c, [])
        pvals = pred_by_cat.get(c, [])
        used = [False] * len(gvals)
        tp = 0
        for pv in pvals:
            pn = normalize_value(pv, c)
            hit = False
            for i, gv in enumerate(gvals):
                if used[i]:
                    continue
                if _value_match(pn, normalize_value(gv, c), c):
                    used[i] = True; tp += 1; hit = True; break
        fp = len(pvals) - tp
        fn = len(gvals) - tp
        cat_counts[c][0] += tp; cat_counts[c][1] += fp; cat_counts[c][2] += fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detectors", default="ours_spacy,ours_gliner,presidio_gliner,presidio_spacy,m4_llm")
    ap.add_argument("--corpora", default="handcrafted,policy")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)  # observable progress when piped
    except Exception:
        pass
    dets = [d.strip() for d in args.detectors.split(",") if d.strip()]
    corpora_names = [c.strip() for c in args.corpora.split(",") if c.strip()]
    ts = time.strftime("%Y%m%dT%H%M%S")
    outdir = os.path.join(args.out, ts)
    os.makedirs(outdir, exist_ok=True)

    corpora = {}
    if "handcrafted" in corpora_names:
        corpora["handcrafted"] = load_handcrafted()
    if "policy" in corpora_names:
        corpora["policy"] = load_policy()

    print(f"[pii-eval] {ts}  detectors={dets}  corpora={ {k: len(v) for k,v in corpora.items()} }")
    print("[pii-eval] availability check:")
    skip = set()
    for d in dets:
        ok, msg = _availability(d)
        print(f"    {d:18s} {'OK' if ok else 'SKIP — ' + msg}")
        if not ok:
            skip.add(d)

    rows = []
    report = [f"# PII Detector Shootout — {ts}\n"]
    for cname, corpus in corpora.items():
        if not corpus:
            report.append(f"## Corpus: {cname} — EMPTY (skipped)\n")
            continue
        report.append(f"## Corpus: {cname} ({len(corpus)} docs)\n")
        report.append("| Detector | P (mean±sd) | R | F1 | reps |\n|---|---|---|---|---|")
        cat_block = {}
        for d in dets:
            if d in skip:
                report.append(f"| {d} | — skipped — | | | |")
                continue
            print(f"\n[pii-eval] {cname} :: {d}")
            try:
                overall, percat, _ = run_detector(d, DETECTORS[d], corpus, args.reps)
            except Exception as e:
                print(f"    [{d}] FATAL: {e}", flush=True)
                report.append(f"| {d} | — FATAL: {e} — | | | |")
                continue
            ps = [o[0] for o in overall]; rs = [o[1] for o in overall]; fs = [o[2] for o in overall]
            pm, psd = mean_spread(ps); rm, rsd = mean_spread(rs); fm, fsd = mean_spread(fs)
            report.append(f"| **{d}** | {pm:.2f}±{psd:.2f} | {rm:.2f}±{rsd:.2f} | "
                          f"{fm:.2f}±{fsd:.2f} | {len(overall)} |")
            rows.append({"corpus": cname, "detector": d, "P": f"{pm:.3f}", "P_sd": f"{psd:.3f}",
                         "R": f"{rm:.3f}", "R_sd": f"{rsd:.3f}", "F1": f"{fm:.3f}", "F1_sd": f"{fsd:.3f}",
                         "reps": len(overall)})
            # average per-category F1 across reps
            cat_block[d] = {}
            for c in CANON_TYPES:
                f1s = [rep[c][2] for rep in percat]
                r1s = [rep[c][1] for rep in percat]
                cm, _ = mean_spread(f1s); rmc, _ = mean_spread(r1s)
                cat_block[d][c] = (rmc, cm)
        # per-category recall table (recall matters most for PII)
        report.append("\n### Per-category recall (mean across reps)\n")
        header = "| category | " + " | ".join(d for d in dets if d not in skip) + " |"
        report.append(header)
        report.append("|" + "---|" * (1 + len([d for d in dets if d not in skip])))
        for c in CANON_TYPES:
            cells = []
            for d in dets:
                if d in skip:
                    continue
                rmc, cm = cat_block.get(d, {}).get(c, (0.0, 0.0))
                cells.append(f"{rmc:.2f}")
            report.append(f"| {c} | " + " | ".join(cells) + " |")
        report.append("")

    # write outputs
    csv_path = os.path.join(outdir, "summary.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["corpus", "detector", "P", "P_sd", "R", "R_sd",
                                           "F1", "F1_sd", "reps"])
        w.writeheader()
        w.writerows(rows)
    md_path = os.path.join(outdir, "report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n")
    print(f"\n[pii-eval] wrote {md_path}\n[pii-eval] wrote {csv_path}")
    print("\n" + "\n".join(report))


if __name__ == "__main__":
    main()
