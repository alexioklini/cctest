#!/usr/bin/env python3
"""Run all 15 canary questions through eval/harness/run.py and judge with Mistral.

Reuses the existing baseline gold answers (no new claude -p calls). Writes a
new results dir under eval/results/<timestamp>_disc-none_harness/ with the
same shape as eval/run.py's output (gold.json reused, brain.json from harness,
judge_mistral.json from judge_mistral.py, summary.csv + summary.md).

Usage:
  python3 eval/harness/run_eval.py                          # full 15Q
  python3 eval/harness/run_eval.py --only F1,P2,C2          # subset
  python3 eval/harness/run_eval.py --label sysprompt-v2     # custom label
  python3 eval/harness/run_eval.py --system-prompt eval/harness/system_prompt.md
"""

import argparse
import csv
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_PY = os.path.join(HARNESS_DIR, "run.py")
JUDGE_PY = os.path.join(REPO_ROOT, "eval", "judge_mistral.py")
QUESTIONS_PATH = os.path.join(REPO_ROOT, "eval", "questions.json")
RUBRIC_PATH = os.path.join(REPO_ROOT, "eval", "rubric.md")
DEFAULT_BASELINE = os.path.join(REPO_ROOT, "eval", "results",
                                "20260501T092520_disc-none_medium-3.5")


def _g(d: dict, dotted: str):
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(p, "")
        if cur == "":
            return ""
    return cur


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="harness", help="results-dir label suffix")
    ap.add_argument("--only", help="comma-separated question ids to run")
    ap.add_argument("--system-prompt", default=os.path.join(HARNESS_DIR, "system_prompt.md"))
    ap.add_argument("--baseline", default=DEFAULT_BASELINE,
                    help="prior results dir to reuse gold/* from")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--top-p", type=float, default=0.85)
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--model", default=None, help="override harness default model")
    args = ap.parse_args()

    if not os.path.exists(args.system_prompt):
        print(f"ERROR: system prompt not found: {args.system_prompt}", file=sys.stderr)
        return 2
    if not os.path.isdir(args.baseline):
        print(f"ERROR: baseline dir not found: {args.baseline}", file=sys.stderr)
        return 2

    with open(QUESTIONS_PATH) as f:
        questions = json.load(f)["questions"]
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
        if not questions:
            print(f"ERROR: --only filtered out everything", file=sys.stderr)
            return 2

    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    results_dir = os.path.join(REPO_ROOT, "eval", "results", f"{ts}_disc-none_{args.label}")
    os.makedirs(results_dir, exist_ok=True)

    # Snapshot the inputs
    shutil.copy2(QUESTIONS_PATH, os.path.join(results_dir, "questions.json"))
    shutil.copy2(RUBRIC_PATH, os.path.join(results_dir, "rubric.md"))
    shutil.copy2(args.system_prompt, os.path.join(results_dir, "system_prompt_active.md"))
    with open(args.system_prompt) as f:
        system_prompt_chars = len(f.read())

    run_meta = {
        "timestamp": ts,
        "label": args.label,
        "system_prompt": args.system_prompt,
        "system_prompt_chars": system_prompt_chars,
        "baseline_dir": args.baseline,
        "questions": [q["id"] for q in questions],
        "harness": "eval/harness/run.py",
        "model_override": args.model,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_rounds": args.max_rounds,
    }
    with open(os.path.join(results_dir, "run.json"), "w") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)

    print(f"[harness-eval] results → {results_dir}")
    print(f"[harness-eval] system_prompt: {args.system_prompt} ({system_prompt_chars} chars)")
    print(f"[harness-eval] baseline (gold reuse): {args.baseline}")

    # Phase 1: run harness for each question, copy gold from baseline
    for i, q in enumerate(questions, 1):
        qid = q["id"]
        qdir = os.path.join(results_dir, qid)
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "question.json"), "w") as f:
            json.dump(q, f, indent=2, ensure_ascii=False)

        # Reuse gold
        gold_src = os.path.join(args.baseline, qid, "gold.json")
        gold_dst = os.path.join(qdir, "gold.json")
        if os.path.exists(gold_src):
            shutil.copy2(gold_src, gold_dst)
        else:
            print(f"  [{i}/{len(questions)}] {qid}: WARN no baseline gold")

        # Run harness for this question
        print(f"\n[{i}/{len(questions)}] {qid} — running harness…")
        cmd = [
            sys.executable, RUN_PY,
            "--question", q["question"],
            "--system-prompt", args.system_prompt,
            "--temperature", str(args.temperature),
            "--top-p", str(args.top_p),
            "--max-rounds", str(args.max_rounds),
            "--output", os.path.join(qdir, "harness_transcript.json"),
            "--quiet",
        ]
        if args.model:
            cmd += ["--model", args.model]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            print(f"  FAIL: harness exited {proc.returncode}")
            print(f"  stderr: {proc.stderr[:500]}")
            err_blob = {"error": f"harness exit {proc.returncode}", "stderr": proc.stderr[:1000]}
            with open(os.path.join(qdir, "brain.json"), "w") as f:
                json.dump(err_blob, f, indent=2, ensure_ascii=False)
            continue

        # Load transcript and extract final_answer into a brain.json shape
        transcript_path = os.path.join(qdir, "harness_transcript.json")
        if os.path.exists(transcript_path):
            with open(transcript_path) as f:
                tr = json.load(f)
            brain_blob = {
                "text": tr.get("final_answer", ""),
                "model": tr.get("model"),
                "_elapsed_s": tr.get("elapsed_total_s"),
                "_rounds": len(tr.get("rounds", [])),
                "_tool_calls_total": tr.get("tool_calls_total", 0),
                "_stop_reason": tr.get("stop_reason"),
                "_session_id": "harness",
            }
            with open(os.path.join(qdir, "brain.json"), "w") as f:
                json.dump(brain_blob, f, indent=2, ensure_ascii=False)
            print(f"  ok: {tr.get('elapsed_total_s')}s, {len(tr.get('rounds', []))} rounds, "
                  f"{tr.get('tool_calls_total', 0)} tool calls, {len(brain_blob['text'])} chars")
        else:
            print(f"  WARN: no transcript file written")

    # Phase 2: judge with Mistral
    print(f"\n[harness-eval] judging with Mistral self-judge…")
    judge_cmd = [sys.executable, JUDGE_PY, results_dir]
    if args.only:
        judge_cmd += ["--only", args.only]
    proc = subprocess.run(judge_cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print(f"  judge FAILED: {proc.stderr[:500]}")
    else:
        print(proc.stdout[-2000:])

    # Phase 3: build summary.csv + summary.md (mirrors run.py shape, with mistral judge results)
    summary_rows = []
    for q in questions:
        qid = q["id"]
        qdir = os.path.join(results_dir, qid)
        judge_path = os.path.join(qdir, "judge_mistral.json")
        if os.path.exists(judge_path):
            with open(judge_path) as f:
                j = json.load(f)
        else:
            j = {"error": "no judge output"}
        gt = _g(j, "gold.total")
        bt = _g(j, "brain.total")
        try:
            delta = round(float(bt) - float(gt), 2)
        except (TypeError, ValueError):
            delta = ""
        summary_rows.append({
            "id": qid,
            "bucket": q.get("bucket", ""),
            "expected_refuse": q.get("expected_refuse", False),
            "gold_total": gt,
            "brain_total": bt,
            "delta": delta,
            "winner": _g(j, "comparison.winner"),
            "gold_retrieval": _g(j, "gold.retrieval"),
            "brain_retrieval": _g(j, "brain.retrieval"),
            "gold_precision": _g(j, "gold.precision"),
            "brain_precision": _g(j, "brain.precision"),
            "gold_citation": _g(j, "gold.citation"),
            "brain_citation": _g(j, "brain.citation"),
            "gold_refusal": _g(j, "gold.refusal"),
            "brain_refusal": _g(j, "brain.refusal"),
            "gold_composition": _g(j, "gold.composition"),
            "brain_composition": _g(j, "brain.composition"),
            "judge_summary": _g(j, "comparison.summary") or j.get("error", ""),
        })

    csv_path = os.path.join(results_dir, "summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)

    md_path = os.path.join(results_dir, "summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Harness eval results — {ts} (label: {args.label})\n\n")
        f.write(f"system_prompt: `{args.system_prompt}` ({system_prompt_chars} chars)\n\n")
        f.write("| id | bucket | gold | brain | Δ | winner | summary |\n")
        f.write("|----|--------|-----:|------:|--:|--------|---------|\n")
        valid_g, valid_b = [], []
        wins = {"gold": 0, "brain": 0, "tie": 0, "?": 0}
        for r in summary_rows:
            if isinstance(r["gold_total"], (int, float)):
                valid_g.append(r["gold_total"])
            if isinstance(r["brain_total"], (int, float)):
                valid_b.append(r["brain_total"])
            wins[r["winner"] or "?"] = wins.get(r["winner"] or "?", 0) + 1
            f.write(f"| {r['id']} | {r['bucket']} | {r['gold_total']} | {r['brain_total']} | "
                    f"{r['delta']} | {r['winner']} | {(r['judge_summary'] or '')[:90]} |\n")
        f.write("\n")
        if valid_g and valid_b:
            f.write(f"**Means** — gold: {sum(valid_g)/len(valid_g):.2f}, "
                    f"brain: {sum(valid_b)/len(valid_b):.2f}, "
                    f"Δ_brain−gold: {(sum(valid_b)/len(valid_b)) - (sum(valid_g)/len(valid_g)):+.2f}\n")
        f.write(f"**Wins** — gold: {wins.get('gold', 0)}, brain: {wins.get('brain', 0)}, "
                f"tie: {wins.get('tie', 0)}, errors: {wins.get('?', 0)}\n")

    print(f"\n[harness-eval] done. summary: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
