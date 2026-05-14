#!/usr/bin/env python3
"""Phase 1 end-to-end test.

Drives the sidecar (port 8421) + tool-server stub (port 8420) through the
"Mistral AI News" scheduled task replay. Acceptance: produces a real ~5-7 KB
report.md on disk, just like eval/sdk_harness/run_sdk.py does today.

Usage:
  python3 sidecar/test_replay.py --model gemma-4-26B-A4B-it-MLX-4bit \\
                                  --base-url http://localhost:8000 \\
                                  --api-key brain \\
                                  --out-dir /tmp/sidecar_phase1_gemma26

  python3 sidecar/test_replay.py --model mistral-medium-3.5 \\
                                  --base-url http://localhost:8317 \\
                                  --api-key brain-agent \\
                                  --out-dir /tmp/sidecar_phase1_mistral
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    ap.add_argument("--sidecar-url", default="http://127.0.0.1:8421")
    ap.add_argument("--tool-endpoint", default="http://127.0.0.1:8430/v1/tools/call")
    ap.add_argument("--schedule-id", type=int, default=95)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True,
                    help="Where the LLM lives (oMLX 8000, CLIProxy 8317)")
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--tool-stub-write-dir", default="/tmp/sidecar_phase1_out",
                    help="Where the tool-server stub was launched with --write-base-dir. "
                         "report.md will land in this directory. Must match the stub.")
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--top-p", type=float, default=0.85)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--system-prompt",
                    default=os.path.join(REPO_ROOT, "eval", "sdk_harness",
                                         "system_prompt_scheduler.md"))
    args = ap.parse_args()

    name, task = fetch_schedule(args.schedule_id)
    with open(args.system_prompt) as f:
        system_prompt = f.read()

    # Same tool schemas the SDK harness uses — load from there.
    sys.path.insert(0, os.path.join(REPO_ROOT, "eval", "sdk_harness"))
    import run as harness_tools  # noqa: E402
    tool_names = ["exa_search", "web_fetch", "write_file"]
    tools = [harness_tools._TOOL_SCHEMAS[t] for t in tool_names]

    payload = {
        "model": args.model,
        "base_url": args.base_url,
        "api_key": args.api_key,
        "system": system_prompt,
        "messages": [{"role": "user", "content": task}],
        "tools": tools,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "max_rounds": args.max_rounds,
        "tool_endpoint": args.tool_endpoint,
        "tool_endpoint_auth": "Bearer phase1-stub",
        "trace_id": f"phase1-{int(time.time())}",
    }

    print(f"[test] sidecar={args.sidecar_url}  tool={args.tool_endpoint}", flush=True)
    print(f"[test] model={args.model}  base_url={args.base_url}", flush=True)
    print(f"[test] task: {task[:160]}{'...' if len(task) > 160 else ''}", flush=True)
    print(f"[test] tool-stub write dir: {args.tool_stub_write_dir}", flush=True)

    req = urllib.request.Request(
        args.sidecar_url.rstrip("/") + "/turn",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    t0 = time.time()
    rounds_seen = 0
    tool_calls = 0
    done_payload: dict = {}
    last_round_summary = None
    error_payload = None
    final_text_preview = ""

    def _iter_sse_events(resp):
        """Read SSE byte-by-byte. Yields (event_type, data_dict) per event.

        `for line in resp:` buffers until the OS buffer is filled, which means
        keepalive comments arrive in bursts and the loop appears hung. Using
        unbuffered raw reads with our own line accumulator gives us real-time
        event delivery."""
        line = b""
        block_lines: list[bytes] = []
        while True:
            chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(1)
            if not chunk:
                if block_lines:
                    yield _parse_block(b"\n".join(block_lines))
                return
            line += chunk
            while b"\n" in line:
                cut, _, rest = line.partition(b"\n")
                line = rest
                if cut == b"":
                    if block_lines:
                        parsed = _parse_block(b"\n".join(block_lines))
                        if parsed is not None:
                            yield parsed
                        block_lines = []
                else:
                    block_lines.append(cut)

    def _parse_block(blk: bytes):
        text = blk.decode("utf-8", errors="replace").strip()
        if not text or text.startswith(":"):
            return None
        for ln in text.splitlines():
            if ln.startswith("data:"):
                try:
                    env = json.loads(ln[5:].strip())
                except Exception:
                    return None
                return env.get("type", ""), env.get("data") or {}
        return None

    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" not in ctype:
                print(f"[test] WARNING expected SSE got Content-Type={ctype!r}", flush=True)
            for parsed in _iter_sse_events(resp):
                if parsed is None:
                    continue
                etype, data = parsed
                if etype == "round_start":
                    rounds_seen += 1
                    print(f"[test] round {data.get('round')} start", flush=True)
                elif etype == "tool_dispatch_start":
                    tool_calls += 1
                    ap_args = data.get("args") or {}
                    ap_str = json.dumps(ap_args, ensure_ascii=False)
                    if len(ap_str) > 140:
                        ap_str = ap_str[:140] + "..."
                    print(f"[test]   tool_use #{tool_calls}: {data.get('name')}({ap_str})",
                          flush=True)
                elif etype == "tool_dispatch_done":
                    tag = " ERROR" if data.get("is_error") else ""
                    print(f"[test]   tool_done: {data.get('result_chars')}c "
                          f"in {data.get('elapsed_ms')}ms{tag}", flush=True)
                elif etype == "round_end":
                    last_round_summary = data
                    print(f"[test] round {data.get('round')} end "
                          f"stop={data.get('stop_reason')} "
                          f"content_chars={data.get('content_chars')} "
                          f"tools={data.get('has_tool_use')}", flush=True)
                elif etype == "done":
                    done_payload = data
                    final_text_preview = (data.get("final_text") or "")[:400]
                elif etype == "error":
                    error_payload = data
                    print(f"[test] ERROR event: {data.get('message')}", flush=True)
                elif etype.startswith("anthropic."):
                    # Phase 1: don't spam Anthropic deltas — but count them via round_end usage
                    pass
                else:
                    # surface anything we didn't categorise
                    print(f"[test] {etype}: {json.dumps(data)[:200]}", flush=True)
    except Exception as e:
        print(f"[test] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    elapsed = round(time.time() - t0, 1)
    print()
    print("=" * 70)
    if error_payload:
        print(f"FINISHED WITH ERROR after {elapsed}s")
        print(json.dumps(error_payload, indent=2)[:1500])
        return 1
    print(f"FINISHED in {elapsed}s")
    print(f"  stop_reason     = {done_payload.get('stop_reason')}")
    print(f"  rounds          = {done_payload.get('rounds')}")
    print(f"  tool_calls      = {done_payload.get('tool_calls_total')}")
    print(f"  usage_total     = {done_payload.get('usage_total')}")
    print(f"  final_text head = {final_text_preview!r}")
    print()
    print(f"Files under {args.tool_stub_write_dir}:")
    any_file = False
    if os.path.isdir(args.tool_stub_write_dir):
        for entry in sorted(os.listdir(args.tool_stub_write_dir)):
            full = os.path.join(args.tool_stub_write_dir, entry)
            if os.path.isfile(full):
                any_file = True
                print(f"  {entry}  ({os.path.getsize(full)} bytes)")
    if not any_file:
        print("  (none)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
