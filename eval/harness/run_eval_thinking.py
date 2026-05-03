#!/usr/bin/env python3
"""Run 15Q harness with Mistral Medium 3.5 thinking (reasoning_effort=high)
and judge with Sonnet 4.6 via claude -p.

Gold answers are reused from an existing baseline — no new Opus run.

Usage:
  python3 eval/harness/run_eval_thinking.py
  python3 eval/harness/run_eval_thinking.py --only F1,P2,C2
  python3 eval/harness/run_eval_thinking.py --label thinking-high
  python3 eval/harness/run_eval_thinking.py --baseline eval/results/<other-dir>
"""

import argparse
import csv
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_PY = os.path.join(HARNESS_DIR, "run.py")
QUESTIONS_PATH = os.path.join(REPO_ROOT, "eval", "questions.json")
RUBRIC_PATH = os.path.join(REPO_ROOT, "eval", "rubric.md")
DEFAULT_BASELINE = os.path.join(REPO_ROOT, "eval", "results",
                                "20260501T092520_disc-none_medium-3.5")
JUDGE_MODEL = "claude-sonnet-4-6"


def _g(d: dict, dotted: str):
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(p, "")
        if cur == "":
            return ""
    return cur


def extract_text_from_claude_json(blob: dict) -> str:
    if blob.get("is_error") or blob.get("subtype", "").startswith("error"):
        st = blob.get("subtype", "error")
        return f"[CLAUDE_ERROR: subtype={st}]"
    for key in ("result", "response", "output", "message"):
        v = blob.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            t = v.get("text") or v.get("content")
            if isinstance(t, str) and t.strip():
                return t
            if isinstance(t, list):
                parts = [b.get("text", "") for b in t if isinstance(b, dict)]
                joined = "\n".join(p for p in parts if p)
                if joined.strip():
                    return joined
    return str(blob)


def _build_judge_prompt(question_obj: dict, gold_text: str, brain_text: str, rubric: str) -> str:
    expected_docs = ", ".join(question_obj.get("expected_docs", [])) or "(none — refusal expected)"
    return (
        f"# Eval rubric\n\n{rubric}\n\n"
        f"---\n\n"
        f"# Question\n\n{question_obj['question']}\n\n"
        f"**Bucket:** {question_obj.get('bucket','')}\n"
        f"**Expected docs:** {expected_docs}\n"
        f"**Expected to refuse:** {bool(question_obj.get('expected_refuse', False))}\n\n"
        f"---\n\n"
        f"# Gold answer (Claude Code + Opus + vanilla MemPalace)\n\n{gold_text}\n\n"
        f"---\n\n"
        f"# Brain answer (Mistral Medium 3.5 with thinking)\n\n{brain_text}\n\n"
        f"---\n\n"
        f"Score both answers per the rubric. Output the JSON object only — no prose, no markdown fences."
    )


def run_judge_sonnet(question_obj: dict, gold_text: str, brain_text: str,
                     rubric: str, timeout: float) -> dict:
    """Judge via claude -p with Sonnet 4.6. No MCP, no tools, 1 turn."""
    prompt = _build_judge_prompt(question_obj, gold_text, brain_text, rubric)
    cmd = [
        "claude", "-p",
        "--model", JUDGE_MODEL,
        "--output-format", "json",
        "--max-turns", "1",
        "--no-session-persistence",
        "--strict-mcp-config",
    ]
    last_err = None
    for attempt in range(2):
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                              input=prompt, timeout=timeout)
        if not proc.stdout.strip():
            last_err = f"empty stdout. stderr={proc.stderr[:300]}"
            continue
        try:
            outer = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            last_err = f"outer not JSON: {e}; head={proc.stdout[:300]!r}"
            continue
        judge_text = extract_text_from_claude_json(outer)
        judge_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", judge_text.strip(), flags=re.MULTILINE)
        m = re.search(r"\{.*\}", judge_text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError as e:
                last_err = f"inner not JSON: {e}; head={m.group(0)[:300]!r}"
                continue
        last_err = f"no JSON object in judge text. head={judge_text[:300]!r}"
    raise RuntimeError(f"sonnet judge failed after 2 attempts: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="thinking-high", help="results-dir label suffix")
    ap.add_argument("--only", help="comma-separated question ids to run")
    ap.add_argument("--system-prompt", default=os.path.join(HARNESS_DIR, "system_prompt.md"))
    ap.add_argument("--baseline", default=DEFAULT_BASELINE,
                    help="prior results dir to reuse gold/* from")
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--model", default=None, help="override harness model (default: mistral-medium-3.5)")
    ap.add_argument("--judge-timeout", type=float, default=300.0)
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

    with open(args.system_prompt) as f:
        system_prompt_chars = len(f.read())

    shutil.copy2(QUESTIONS_PATH, os.path.join(results_dir, "questions.json"))
    shutil.copy2(RUBRIC_PATH, os.path.join(results_dir, "rubric.md"))
    shutil.copy2(args.system_prompt, os.path.join(results_dir, "system_prompt_active.md"))

    run_meta = {
        "timestamp": ts,
        "label": args.label,
        "system_prompt": args.system_prompt,
        "system_prompt_chars": system_prompt_chars,
        "baseline_dir": args.baseline,
        "questions": [q["id"] for q in questions],
        "harness": "eval/harness/run.py",
        "thinking": True,
        "reasoning_effort": "high",
        "temperature": 1.0,
        "judge": JUDGE_MODEL,
        "model_override": args.model,
        "max_rounds": args.max_rounds,
    }
    with open(os.path.join(results_dir, "run.json"), "w") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)

    print(f"[thinking-eval] results → {results_dir}")
    print(f"[thinking-eval] system_prompt: {args.system_prompt} ({system_prompt_chars} chars)")
    print(f"[thinking-eval] baseline (gold reuse): {args.baseline}")
    print(f"[thinking-eval] judge: {JUDGE_MODEL} via claude -p")
    print(f"[thinking-eval] thinking: reasoning_effort=high, temperature=1.0")

    rubric = open(RUBRIC_PATH).read()

    # Phase 1: run harness per question (with thinking params), reuse gold
    for i, q in enumerate(questions, 1):
        qid = q["id"]
        qdir = os.path.join(results_dir, qid)
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "question.json"), "w") as f:
            json.dump(q, f, indent=2, ensure_ascii=False)

        # Reuse gold from baseline
        gold_src = os.path.join(args.baseline, qid, "gold.json")
        gold_dst = os.path.join(qdir, "gold.json")
        if os.path.exists(gold_src):
            shutil.copy2(gold_src, gold_dst)
        else:
            print(f"  [{i}/{len(questions)}] {qid}: WARN no baseline gold at {gold_src}")

        # Run harness — temperature=1.0 + reasoning_effort=high via --thinking flag
        print(f"\n[{i}/{len(questions)}] {qid} — running harness (thinking)…")
        cmd = [
            sys.executable, RUN_PY,
            "--question", q["question"],
            "--system-prompt", args.system_prompt,
            "--temperature", "1.0",
            "--top-p", "1.0",
            "--max-rounds", str(args.max_rounds),
            "--thinking",
            "--output", os.path.join(qdir, "harness_transcript.json"),
            "--quiet",
        ]
        if args.model:
            cmd += ["--model", args.model]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT after 600s")
            with open(os.path.join(qdir, "brain.json"), "w") as f:
                json.dump({"error": "harness timeout"}, f)
            continue

        if proc.returncode != 0:
            print(f"  FAIL: harness exited {proc.returncode}")
            print(f"  stderr: {proc.stderr[:500]}")
            with open(os.path.join(qdir, "brain.json"), "w") as f:
                json.dump({"error": f"harness exit {proc.returncode}", "stderr": proc.stderr[:1000]}, f)
            continue

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
                "_session_id": "harness-thinking",
                "_thinking": True,
            }
            with open(os.path.join(qdir, "brain.json"), "w") as f:
                json.dump(brain_blob, f, indent=2, ensure_ascii=False)
            print(f"  ok: {tr.get('elapsed_total_s')}s, {len(tr.get('rounds', []))} rounds, "
                  f"{tr.get('tool_calls_total', 0)} tool calls, {len(brain_blob['text'])} chars")
        else:
            print(f"  WARN: no transcript written")

    # Phase 2: judge each question with Sonnet 4.6
    print(f"\n[thinking-eval] judging with {JUDGE_MODEL}…")
    summary_rows = []
    for i, q in enumerate(questions, 1):
        qid = q["id"]
        qdir = os.path.join(results_dir, qid)
        judge_path = os.path.join(qdir, "judge.json")

        gold_path = os.path.join(qdir, "gold.json")
        brain_path = os.path.join(qdir, "brain.json")
        if not os.path.exists(gold_path) or not os.path.exists(brain_path):
            print(f"  [{i}/{len(questions)}] {qid}: SKIP — missing gold or brain")
            j = {"error": "missing answers"}
        else:
            with open(gold_path) as f:
                gold_blob = json.load(f)
            with open(brain_path) as f:
                brain_blob = json.load(f)

            gold_text = extract_text_from_claude_json(gold_blob)
            brain_raw = brain_blob.get("text", "")
            # Thinking mode may have stored a list of blocks instead of plain text
            if isinstance(brain_raw, list):
                brain_text = "\n".join(
                    b.get("text", "") for b in brain_raw
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                brain_text = str(brain_raw) if brain_raw else ""

            if not gold_text.strip() or not brain_text.strip():
                print(f"  [{i}/{len(questions)}] {qid}: SKIP — empty answer")
                j = {"error": "empty answer"}
            else:
                try:
                    print(f"  [{i}/{len(questions)}] {qid}: judging…", end=" ", flush=True)
                    j = run_judge_sonnet(q, gold_text, brain_text, rubric, args.judge_timeout)
                    print(f"gold={_g(j,'gold.total')} brain={_g(j,'brain.total')} Δ={_delta(j)}")
                except Exception as e:
                    print(f"FAILED — {e}")
                    j = {"error": str(e)}

        with open(judge_path, "w", encoding="utf-8") as f:
            json.dump(j, f, indent=2, ensure_ascii=False)

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

    if not summary_rows:
        print("[thinking-eval] no summary rows — aborting")
        return 1

    csv_path = os.path.join(results_dir, "summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)

    md_path = os.path.join(results_dir, "summary.md")
    valid_g, valid_b = [], []
    wins: dict = {}
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Thinking eval — {ts} (label: {args.label})\n\n")
        f.write(f"Model: Mistral Medium 3.5 + thinking (reasoning_effort=high, T=1.0)  \n")
        f.write(f"Judge: {JUDGE_MODEL} via claude -p  \n")
        f.write(f"Gold: reused from `{os.path.basename(args.baseline)}`  \n\n")
        f.write("| id | bucket | gold | brain | Δ | winner | summary |\n")
        f.write("|----|--------|-----:|------:|--:|--------|---------|\n")
        for r in summary_rows:
            if isinstance(r["gold_total"], (int, float)):
                valid_g.append(r["gold_total"])
            if isinstance(r["brain_total"], (int, float)):
                valid_b.append(r["brain_total"])
            w_key = r["winner"] or "?"
            wins[w_key] = wins.get(w_key, 0) + 1
            f.write(f"| {r['id']} | {r['bucket']} | {r['gold_total']} | {r['brain_total']} | "
                    f"{r['delta']} | {r['winner']} | {(r['judge_summary'] or '')[:90]} |\n")
        f.write("\n")
        if valid_g and valid_b:
            mean_g = sum(valid_g) / len(valid_g)
            mean_b = sum(valid_b) / len(valid_b)
            f.write(f"**Means** — gold: {mean_g:.2f}, brain: {mean_b:.2f}, "
                    f"Δ_brain−gold: {mean_b - mean_g:+.2f}\n")
        f.write(f"**Wins** — gold: {wins.get('gold',0)}, brain: {wins.get('brain',0)}, "
                f"tie: {wins.get('tie',0)}, errors: {wins.get('?',0)}\n")

    print(f"\n[thinking-eval] done. summary: {md_path}")
    if valid_g and valid_b:
        mean_g = sum(valid_g) / len(valid_g)
        mean_b = sum(valid_b) / len(valid_b)
        print(f"  gold mean={mean_g:.3f}  brain mean={mean_b:.3f}  Δ={mean_b-mean_g:+.3f}")
    return 0


def _delta(j: dict) -> str:
    g = _g(j, "gold.total")
    b = _g(j, "brain.total")
    try:
        return f"{round(float(b) - float(g), 2):+.2f}"
    except (TypeError, ValueError):
        return "?"


if __name__ == "__main__":
    sys.exit(main())
