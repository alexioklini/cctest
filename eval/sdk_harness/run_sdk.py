#!/usr/bin/env python3
"""Anthropic-SDK agentic loop — same tools + same shape as run.py, but uses
the real `anthropic` Python package instead of hand-rolled urllib calls.

The point is to verify that the SDK ITSELF works against CLIProxyAPI and oMLX,
not just the wire format. If this script runs cleanly through the same
scheduled-task workload, the SDK is safe to integrate into Brain. If it hangs,
buffers, or errors where run.py succeeded, the v6/v7 sidecar pain may still
be a real risk.

Usage (single question):
  .venv_sdk/bin/python eval/sdk_harness/run_sdk.py \\
      --question "..." --system-prompt eval/sdk_harness/system_prompt_lean.md \\
      --output /tmp/sdk_run.json

Usage (scheduled-task replay):
  .venv_sdk/bin/python eval/sdk_harness/run_sdk.py \\
      --question "$(sqlite3 agents/main/scheduler.db 'select task from schedules where id=95')" \\
      --system-prompt eval/sdk_harness/system_prompt_scheduler.md \\
      --base-url http://localhost:8000 --api-key brain \\
      --model gemma-4-26B-A4B-it-MLX-4bit \\
      --tools exa_search,web_fetch,write_file --allow-write \\
      --write-base-dir /tmp/sdk_sched_out --output /tmp/sdk_sched_out/transcript.json
"""

import argparse
import json
import os
import sys
import time

# Reuse the tool implementations + schemas from the raw-HTTP harness — this is
# the part that is identical between the two; what differs is the LLM call.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import run as raw_harness  # noqa: E402  — sibling file

try:
    import anthropic  # noqa: F401
except ImportError:
    print("ERROR: anthropic package not installed. Run:", file=sys.stderr)
    print("  python3 -m venv .venv_sdk && .venv_sdk/bin/pip install anthropic", file=sys.stderr)
    sys.exit(2)


def run_loop_sdk(question: str, system_prompt: str, base_url: str, api_key: str,
                 model: str, tool_names: list, max_rounds: int,
                 palace_path: str, default_wing: str,
                 temperature: float = 0.2, top_p: float = 0.85,
                 max_tokens: int = 16000, verbose: bool = True) -> dict:
    """Identical loop shape to run.py:run_loop, but uses anthropic.Anthropic
    client.messages.create() in place of raw HTTP."""
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    tools_payload = [raw_harness._TOOL_SCHEMAS[t] for t in tool_names
                     if t in raw_harness._TOOL_SCHEMAS]
    messages = [{"role": "user", "content": question}]
    transcript = {
        "question": question,
        "model": model,
        "base_url": base_url,
        "tools_enabled": tool_names,
        "system_prompt_chars": len(system_prompt),
        "rounds": [],
        "final_answer": "",
        "tool_calls_total": 0,
        "elapsed_total_s": 0.0,
        "stop_reason": "",
        "usage_total": {"input_tokens": 0, "output_tokens": 0},
        "sdk_version": anthropic.__version__,
    }

    t0 = time.time()
    for r in range(max_rounds):
        round_t0 = time.time()
        if verbose:
            print(f"\n=== round {r+1}/{max_rounds} ===", flush=True)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
                tools=tools_payload,
                temperature=temperature,
                top_p=top_p,
            )
        except anthropic.APIError as e:
            transcript["stop_reason"] = f"api_error: {type(e).__name__}: {e}"
            if verbose:
                print(f"  API ERROR: {e}", flush=True)
            break
        # Usage
        usage = {"input_tokens": getattr(resp.usage, "input_tokens", 0),
                 "output_tokens": getattr(resp.usage, "output_tokens", 0)}
        transcript["usage_total"]["input_tokens"] += int(usage["input_tokens"] or 0)
        transcript["usage_total"]["output_tokens"] += int(usage["output_tokens"] or 0)
        stop = resp.stop_reason or ""

        # SDK gives us typed blocks; pull text + tool_use
        content_blocks = resp.content or []
        text_parts = []
        tool_uses = []
        # Re-serialise content blocks back to dicts so we can append to messages
        serialised_blocks = []
        for blk in content_blocks:
            btype = getattr(blk, "type", None)
            if btype == "text":
                text_parts.append(blk.text)
                serialised_blocks.append({"type": "text", "text": blk.text})
            elif btype == "tool_use":
                tool_uses.append(blk)
                serialised_blocks.append({
                    "type": "tool_use",
                    "id": blk.id,
                    "name": blk.name,
                    "input": blk.input,
                })
            elif btype == "thinking":
                serialised_blocks.append({
                    "type": "thinking",
                    "thinking": getattr(blk, "thinking", ""),
                    "signature": getattr(blk, "signature", ""),
                })
        text = "\n".join(p for p in text_parts if p)

        round_record = {
            "round": r + 1,
            "elapsed_s": round(time.time() - round_t0, 2),
            "stop_reason": stop,
            "content_chars": len(text),
            "content_excerpt": text[:400],
            "tool_calls": [],
            "usage": usage,
        }

        messages.append({"role": "assistant", "content": serialised_blocks})

        if not tool_uses:
            transcript["final_answer"] = text
            transcript["stop_reason"] = stop or "no_tool_use"
            transcript["rounds"].append(round_record)
            if verbose:
                print(f"  stop_reason={stop}  text_chars={len(text)}", flush=True)
            break

        result_blocks = []
        for tu in tool_uses:
            tname = tu.name
            targs = tu.input or {}
            transcript["tool_calls_total"] += 1
            t_t0 = time.time()
            result = raw_harness._dispatch(tname, targs, palace_path, default_wing)
            t_elapsed = round(time.time() - t_t0, 2)
            result_str = json.dumps(result, ensure_ascii=False)
            if len(result_str) > 80000:
                result_str = result_str[:78000] + ' ... ", "_truncated": true}'
            round_record["tool_calls"].append({
                "name": tname,
                "args": targs,
                "elapsed_s": t_elapsed,
                "result_chars": len(result_str),
                "result_excerpt": result_str[:400],
            })
            if verbose:
                ap = json.dumps(targs, ensure_ascii=False)
                if len(ap) > 120:
                    ap = ap[:120] + "..."
                print(f"  tool_use: {tname}({ap})  -> {len(result_str)}c in {t_elapsed}s", flush=True)
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_str,
            })
        messages.append({"role": "user", "content": result_blocks})
        transcript["rounds"].append(round_record)
    else:
        transcript["stop_reason"] = "max_rounds"
        if verbose:
            print(f"\n!! hit max_rounds ({max_rounds}) without final answer", flush=True)

    transcript["elapsed_total_s"] = round(time.time() - t0, 2)
    return transcript


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Anthropic-SDK minimal agentic loop (real SDK, not raw HTTP)")
    ap.add_argument("--question", required=True)
    ap.add_argument("--system-prompt",
                    default=os.path.join(os.path.dirname(__file__), "system_prompt_lean.md"))
    ap.add_argument("--base-url", default="http://localhost:8317")
    ap.add_argument("--api-key", default=os.environ.get("CLIPROXY_KEY", "brain-agent"))
    ap.add_argument("--model", default="mistral-medium-3.5")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--top-p", type=float, default=0.85)
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--palace-path", default="/Users/alexander/.mempalace/brain")
    ap.add_argument("--wing", default="project__f201b24ff6a2")
    ap.add_argument("--tools",
                    default="mempalace_query,mempalace_kg_search,read_document,read_file")
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--write-base-dir", default="/tmp/sdk_harness_out")
    ap.add_argument("--output")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.allow_write:
        raw_harness._WRITE_GATE["allow"] = True
        raw_harness._WRITE_GATE["base_dir"] = args.write_base_dir
        os.makedirs(args.write_base_dir, exist_ok=True)

    with open(args.system_prompt, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    tool_names = [t.strip() for t in args.tools.split(",") if t.strip()]

    print(f"[sdk-real] SDK={anthropic.__version__}  model={args.model}  base_url={args.base_url}", flush=True)
    print(f"[sdk-real] tools={tool_names}", flush=True)
    print(f"[sdk-real] system_prompt={args.system_prompt} ({len(system_prompt)} chars)", flush=True)
    print(f"[sdk-real] question: {args.question[:200]}{'...' if len(args.question)>200 else ''}", flush=True)

    transcript = run_loop_sdk(
        question=args.question,
        system_prompt=system_prompt,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        tool_names=tool_names,
        max_rounds=args.max_rounds,
        palace_path=args.palace_path,
        default_wing=args.wing,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        verbose=not args.quiet,
    )

    print("\n" + "=" * 70)
    print(f"FINAL ANSWER ({len(transcript['final_answer'])} chars):")
    print("=" * 70)
    print(transcript["final_answer"])
    print("\n" + "=" * 70)
    print(f"summary: stop_reason={transcript['stop_reason']}  "
          f"rounds={len(transcript['rounds'])}  "
          f"tool_calls={transcript['tool_calls_total']}  "
          f"in={transcript['usage_total']['input_tokens']}  "
          f"out={transcript['usage_total']['output_tokens']}  "
          f"{transcript['elapsed_total_s']}s")

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2, ensure_ascii=False)
        print(f"transcript: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
