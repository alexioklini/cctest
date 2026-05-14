#!/usr/bin/env python3
"""Replay a scheduled task through the SDK harness (Anthropic via CLIProxyAPI).

Looks up the task text from agents/main/scheduler.db, runs it through the
exa_search + web_fetch + write_file tool loop, captures the transcript and
the written report.md.

Usage:
  python3 eval/sdk_harness/schedule_task.py --schedule-id 95
  python3 eval/sdk_harness/schedule_task.py --schedule-id 95 --out-dir /tmp/sched95_sdk

Or pass the task text directly:
  python3 eval/sdk_harness/schedule_task.py --task "Get all recent news ..." --out-dir /tmp/X
"""

import argparse
import datetime as _dt
import json
import os
import sqlite3
import subprocess
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS = os.path.join(REPO_ROOT, "eval", "sdk_harness", "run.py")


def fetch_schedule(sched_id: int) -> tuple[str, str]:
    db = os.path.join(REPO_ROOT, "agents", "main", "scheduler.db")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT name, task FROM schedules WHERE id=?", (sched_id,)).fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"no schedule with id={sched_id}")
    return row[0], row[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--schedule-id", type=int)
    g.add_argument("--task", type=str)
    ap.add_argument("--out-dir", default=None,
                    help="Where to write transcript + report. Default /tmp/sdk_sched_<ts>")
    ap.add_argument("--system-prompt",
                    default=os.path.join(REPO_ROOT, "eval", "sdk_harness", "system_prompt_scheduler.md"))
    ap.add_argument("--base-url", default="http://localhost:8317")
    ap.add_argument("--api-key", default=os.environ.get("CLIPROXY_KEY", "brain-agent"))
    ap.add_argument("--model", default="mistral-medium-3.5")
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    if args.schedule_id is not None:
        name, task = fetch_schedule(args.schedule_id)
        print(f"[sched-replay] schedule #{args.schedule_id} = {name!r}", flush=True)
    else:
        name, task = "ad-hoc", args.task

    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = args.out_dir or f"/tmp/sdk_sched_{ts}"
    os.makedirs(out_dir, exist_ok=True)
    transcript_path = os.path.join(out_dir, "transcript.json")

    cmd = [
        sys.executable, HARNESS,
        "--question", task,
        "--system-prompt", args.system_prompt,
        "--base-url", args.base_url,
        "--api-key", args.api_key,
        "--model", args.model,
        "--max-rounds", str(args.max_rounds),
        "--tools", "exa_search,web_fetch,write_file",
        "--allow-write",
        "--write-base-dir", out_dir,
        "--output", transcript_path,
    ]
    print(f"[sched-replay] out_dir={out_dir}", flush=True)
    print(f"[sched-replay] task: {task[:200]}{'...' if len(task)>200 else ''}", flush=True)

    t0 = time.time()
    proc = subprocess.run(cmd, timeout=args.timeout)
    elapsed = round(time.time() - t0, 1)

    # Summary
    summary = {
        "schedule_id": args.schedule_id,
        "schedule_name": name,
        "out_dir": out_dir,
        "exit_code": proc.returncode,
        "elapsed_s": elapsed,
        "transcript": transcript_path if os.path.isfile(transcript_path) else None,
        "written_files": [],
    }
    for entry in sorted(os.listdir(out_dir)):
        full = os.path.join(out_dir, entry)
        if os.path.isfile(full) and entry != "transcript.json":
            summary["written_files"].append({"name": entry, "size": os.path.getsize(full)})
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n[sched-replay] done in {elapsed}s, exit={proc.returncode}", flush=True)
    print(f"[sched-replay] written files: {summary['written_files']}", flush=True)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
