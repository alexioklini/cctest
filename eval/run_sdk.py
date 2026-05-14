#!/usr/bin/env python3
"""Run the SDK-harness (Anthropic-format loop via CLIProxyAPI) against the
15-question eval set, then judge with the existing Mistral judge.

Skips gold (Opus) entirely — the comparison is against a prior eval run's
gold.json files, which are copied into the new results dir so judge_mistral.py
finds them.

Usage:
  python3 eval/run_sdk.py \
      --reuse-gold-from eval/results/20260513T105407_disc-none_variance-tuned-temp02 \
      --system-prompt eval/sdk_harness/system_prompt_lean.md \
      --label sdk-lean

  python3 eval/run_sdk.py \
      --reuse-gold-from eval/results/20260513T105407_disc-none_variance-tuned-temp02 \
      --system-prompt eval/sdk_harness/system_prompt_full.md \
      --label sdk-full
"""

import argparse
import csv
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def run_sdk_harness(question_text: str, system_prompt_path: str, output_path: str,
                    base_url: str, api_key: str, model: str,
                    tools: str, max_rounds: int, palace_path: str, wing: str,
                    timeout_s: int) -> dict:
    """Invoke eval/sdk_harness/run.py as a subprocess (matches how the
    OpenAI-format harness is wired). Returns the parsed transcript or an
    error stub if the subprocess failed."""
    cmd = [
        sys.executable,
        os.path.join(REPO_ROOT, "eval", "sdk_harness", "run.py"),
        "--question", question_text,
        "--system-prompt", system_prompt_path,
        "--base-url", base_url,
        "--api-key", api_key,
        "--model", model,
        "--tools", tools,
        "--max-rounds", str(max_rounds),
        "--palace-path", palace_path,
        "--wing", wing,
        "--output", output_path,
        "--quiet",
    ]
    try:
        proc = subprocess.run(cmd, timeout=timeout_s, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout_s}s", "final_answer": "", "rounds": []}
    if proc.returncode != 0:
        return {
            "error": f"harness exit {proc.returncode}",
            "stderr": (proc.stderr or "")[-2000:],
            "final_answer": "",
            "rounds": [],
        }
    try:
        return _load_json(output_path)
    except Exception as e:
        return {"error": f"failed to load transcript: {e}", "final_answer": "", "rounds": []}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-gold-from", required=True,
                    help="Prior eval results dir whose gold.json files will be copied into the new run")
    ap.add_argument("--system-prompt",
                    default=os.path.join(REPO_ROOT, "eval", "sdk_harness", "system_prompt_lean.md"))
    ap.add_argument("--label", default="sdk-run")
    ap.add_argument("--only", default="",
                    help="Comma-separated question ids to run; default = all")
    ap.add_argument("--base-url", default="http://localhost:8317")
    ap.add_argument("--api-key", default=os.environ.get("CLIPROXY_KEY", "brain-agent"))
    ap.add_argument("--model", default="mistral-medium-3.5")
    ap.add_argument("--tools",
                    default="mempalace_query,mempalace_kg_search,read_document,read_file")
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--palace-path", default="/Users/alexander/.mempalace/brain")
    ap.add_argument("--wing", default="project__f201b24ff6a2")
    ap.add_argument("--per-question-timeout", type=int, default=600)
    ap.add_argument("--skip-judge", action="store_true",
                    help="Run the harness but skip the Mistral judging pass")
    args = ap.parse_args()

    questions_path = os.path.join(REPO_ROOT, "eval", "questions.json")
    rubric_path = os.path.join(REPO_ROOT, "eval", "rubric.md")
    if not os.path.isfile(questions_path):
        print(f"ERROR: questions.json not found at {questions_path}", file=sys.stderr)
        return 2
    if not os.path.isdir(args.reuse_gold_from):
        print(f"ERROR: --reuse-gold-from is not a directory: {args.reuse_gold_from}", file=sys.stderr)
        return 2

    qset = _load_json(questions_path)
    questions = qset.get("questions") if isinstance(qset, dict) else qset
    if not isinstance(questions, list):
        print("ERROR: questions.json has unexpected shape", file=sys.stderr)
        return 2

    if args.only:
        only_ids = {x.strip() for x in args.only.split(",") if x.strip()}
        questions = [q for q in questions if q.get("id") in only_ids]
        if not questions:
            print(f"ERROR: no questions matched --only {args.only!r}", file=sys.stderr)
            return 2

    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = os.path.join(REPO_ROOT, "eval", "results",
                           f"{ts}_disc-none_{args.label}")
    _ensure_dir(out_dir)

    # Snapshot config + question set + system prompt for reproducibility
    shutil.copy(questions_path, os.path.join(out_dir, "questions.json"))
    if os.path.isfile(rubric_path):
        shutil.copy(rubric_path, os.path.join(out_dir, "rubric.md"))
    shutil.copy(args.system_prompt, os.path.join(out_dir, "system_prompt.md"))
    with open(os.path.join(out_dir, "run.json"), "w") as f:
        json.dump({
            "label": args.label,
            "started_at": ts,
            "reuse_gold_from": args.reuse_gold_from,
            "system_prompt": args.system_prompt,
            "base_url": args.base_url,
            "model": args.model,
            "tools": args.tools,
            "max_rounds": args.max_rounds,
            "wing": args.wing,
        }, f, indent=2)

    print(f"[run_sdk] out_dir={out_dir}", flush=True)
    print(f"[run_sdk] {len(questions)} question(s) — reusing gold from {os.path.basename(args.reuse_gold_from)}", flush=True)

    rows = []
    t0 = time.time()
    for i, q in enumerate(questions, 1):
        qid = q.get("id") or f"q{i}"
        q_dir = os.path.join(out_dir, qid)
        _ensure_dir(q_dir)
        with open(os.path.join(q_dir, "question.json"), "w") as f:
            json.dump(q, f, indent=2, ensure_ascii=False)

        # Copy gold.json from the prior dir if it exists; without it the judge
        # can still run but the comparison column will be empty.
        src_gold = os.path.join(args.reuse_gold_from, qid, "gold.json")
        if os.path.isfile(src_gold):
            shutil.copy(src_gold, os.path.join(q_dir, "gold.json"))
        else:
            print(f"  [warn] no gold.json for {qid} in reuse dir", flush=True)

        print(f"\n[{i}/{len(questions)}] {qid}: {q.get('question','')[:80]}", flush=True)
        transcript_path = os.path.join(q_dir, "transcript.json")
        t_q0 = time.time()
        transcript = run_sdk_harness(
            question_text=q.get("question", ""),
            system_prompt_path=args.system_prompt,
            output_path=transcript_path,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            tools=args.tools,
            max_rounds=args.max_rounds,
            palace_path=args.palace_path,
            wing=args.wing,
            timeout_s=args.per_question_timeout,
        )
        q_elapsed = round(time.time() - t_q0, 1)
        text = transcript.get("final_answer") or ""
        if transcript.get("error"):
            text = text or f"[SDK_HARNESS_ERROR: {transcript['error']}]"
        # Write brain.json in the shape judge_mistral.py expects (text key + a few aux)
        brain_obj = {
            "text": text,
            "model": args.model,
            "duration": q_elapsed,
            "tokens_in": transcript.get("usage_total", {}).get("input_tokens", 0),
            "tokens_out": transcript.get("usage_total", {}).get("output_tokens", 0),
            "_session_id": f"sdk-{qid}",
            "_elapsed_s": q_elapsed,
            "_tool_events": [
                {"name": tc.get("name"), "round": r.get("round")}
                for r in transcript.get("rounds", []) for tc in (r.get("tool_calls") or [])
            ],
            "_errors": [transcript["error"]] if transcript.get("error") else [],
            "_stop_reason": transcript.get("stop_reason", ""),
            "_rounds": len(transcript.get("rounds", [])),
        }
        with open(os.path.join(q_dir, "brain.json"), "w") as f:
            json.dump(brain_obj, f, indent=2, ensure_ascii=False)

        rows.append({
            "id": qid,
            "bucket": q.get("bucket", ""),
            "text_chars": len(text),
            "elapsed_s": q_elapsed,
            "rounds": brain_obj["_rounds"],
            "tool_calls": len(brain_obj["_tool_events"]),
            "in": brain_obj["tokens_in"],
            "out": brain_obj["tokens_out"],
            "stop": brain_obj["_stop_reason"],
            "error": (transcript.get("error") or "")[:120],
        })
        print(f"  -> {brain_obj['_rounds']}r {len(brain_obj['_tool_events'])}t "
              f"{brain_obj['tokens_in']}in/{brain_obj['tokens_out']}out  "
              f"{len(text)}c  {q_elapsed}s  stop={brain_obj['_stop_reason']}"
              f"{'  ERROR=' + transcript['error'] if transcript.get('error') else ''}", flush=True)

    total_elapsed = round(time.time() - t0, 1)
    # Summary CSV
    csv_path = os.path.join(out_dir, "summary_brain_run.csv")
    with open(csv_path, "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"\n[run_sdk] all done in {total_elapsed}s. brain-side summary -> {csv_path}", flush=True)

    if args.skip_judge:
        print("[run_sdk] --skip-judge set, exiting before judge phase")
        return 0

    # Trigger judge pass
    judge_script = os.path.join(REPO_ROOT, "eval", "judge_mistral.py")
    if not os.path.isfile(judge_script):
        print(f"[run_sdk] judge script not found at {judge_script}, skipping", flush=True)
        return 0
    print(f"\n[run_sdk] launching judge: python3 {judge_script} {out_dir}", flush=True)
    try:
        subprocess.run([sys.executable, judge_script, out_dir], check=False)
    except Exception as e:
        print(f"[run_sdk] judge subprocess failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
