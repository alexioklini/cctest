#!/usr/bin/env python3
"""Aggregate the July MoA-vs-auto policy-eval runs (+ the June auto baseline)
into per-arm mean±spread and per-bucket tables. Reads summary.csv of each run
dir. Noise discipline: >=3 reps, report mean±stdev, win only beyond spread."""
import csv
import glob
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(REPO, "eval", "results")

ARMS = {
    "moa (Juli)": sorted(glob.glob(os.path.join(RES, "*_moa-jul-rep*"))),
    "auto (Juli)": sorted(glob.glob(os.path.join(RES, "*_auto-jul-rep*"))),
    "auto (Juni-Baseline)": sorted(glob.glob(os.path.join(RES, "*_v9981-rep*"))),
}


def load_run(d):
    path = os.path.join(d, "summary.csv")
    if not os.path.exists(path):
        return None
    rows = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                rows[r["id"]] = {"total": float(r["brain_total"]),
                                 "bucket": r["bucket"],
                                 "citation": float(r.get("brain_citation") or 0),
                                 "precision": float(r.get("brain_precision") or 0),
                                 "retrieval": float(r.get("brain_retrieval") or 0)}
            except (ValueError, KeyError):
                pass
    return rows


def main():
    per_arm = {}
    for arm, dirs in ARMS.items():
        runs = [r for r in (load_run(d) for d in dirs) if r]
        if not runs:
            print(f"{arm}: keine Läufe gefunden")
            continue
        per_arm[arm] = runs
        rep_means = [statistics.mean(v["total"] for v in run.values()) for run in runs]
        m = statistics.mean(rep_means)
        sd = statistics.stdev(rep_means) if len(rep_means) > 1 else 0.0
        print(f"{arm:22s} reps={len(runs)}  overall={m:.3f} ±{sd:.3f}  "
              f"(reps: {', '.join(f'{x:.3f}' for x in rep_means)})")
    print()
    # per question across reps
    arms = [a for a in ARMS if a in per_arm]
    qids = sorted({q for runs in per_arm.values() for run in runs for q in run})
    print(f"{'Frage':24s} {'bucket':10s} " + " ".join(f"{a.split()[0]:>13s}" for a in arms))
    for q in qids:
        cells, bucket = [], ""
        for a in arms:
            vals = [run[q]["total"] for run in per_arm[a] if q in run]
            bucket = next((run[q]["bucket"] for run in per_arm[a] if q in run), bucket)
            cells.append(f"{statistics.mean(vals):5.2f}±{(statistics.stdev(vals) if len(vals)>1 else 0):4.2f}" if vals else "    —    ")
        print(f"{q:24s} {bucket:10s} " + " ".join(f"{c:>13s}" for c in cells))
    print()
    # per bucket
    buckets = sorted({run[q]["bucket"] for runs in per_arm.values() for run in runs for q in run})
    print(f"{'bucket':12s} " + " ".join(f"{a.split()[0]:>10s}" for a in arms))
    for b in buckets:
        cells = []
        for a in arms:
            vals = [v["total"] for run in per_arm[a] for v in run.values() if v["bucket"] == b]
            cells.append(f"{statistics.mean(vals):.3f}" if vals else "—")
        print(f"{b:12s} " + " ".join(f"{c:>10s}" for c in cells))
    # verdict moa vs auto (Juli)
    if "moa (Juli)" in per_arm and "auto (Juli)" in per_arm:
        mm = [statistics.mean(v["total"] for v in run.values()) for run in per_arm["moa (Juli)"]]
        am = [statistics.mean(v["total"] for v in run.values()) for run in per_arm["auto (Juli)"]]
        lift = statistics.mean(mm) - statistics.mean(am)
        noise = max(statistics.stdev(mm) if len(mm) > 1 else 0,
                    statistics.stdev(am) if len(am) > 1 else 0)
        v = ("WIN" if lift > noise and lift > 0 else
             "noise" if lift > 0 else "LOSS")
        print(f"\nVERDICT moa vs auto (Juli): {lift:+.3f} bei Rep-Spread ±{noise:.3f} → {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
