"""Standalone speculative-decoding bench for Gemma 4 26B-A4B.

Hits any OpenAI-compatible /v1/chat/completions. Measures warmup-from-cold,
PP tps, TG tps across 3 workload shapes. Backend-agnostic — same script
runs against oMLX, ollama, mlx-vlm. CSV output for cross-setup compare.

Usage:
  python bench/gemma4_spec.py \
      --base-url http://localhost:8000/v1 \
      --api-key brain \
      --model gemma-4-26B-A4B-it-MLX-4bit \
      --label oMLX-SpecPrefill \
      --reps 3 \
      --out bench/results.csv

Memory: only one inferencer should be running. Stop the other before invoking.
"""
from __future__ import annotations
import argparse, csv, json, os, sys, time
from urllib import request as urlreq
from urllib.error import HTTPError, URLError

# ----- workloads -----
# Tokens are *approximate* (~4 chars/token). Prompts intentionally varied so
# speculative drafters don't get a free ride from trivial repetition.
LOREM = (
    "The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. "
    "How vexingly quick daft zebras jump! Sphinx of black quartz, judge my vow. "
    "Bright vixens jump; dozy fowl quack. Five quacking zephyrs jolt my wax bed. "
)

def _make_long_prompt(target_chars: int, seed_text: str = LOREM) -> str:
    out = []
    n = 0
    i = 0
    while n < target_chars:
        out.append(f"[Section {i}] " + seed_text)
        n += len(out[-1])
        i += 1
    return "".join(out)[:target_chars]

WORKLOADS = {
    # name: (system, user, max_tokens)
    "PP_heavy": (
        "You are a careful technical analyst. Read the document and produce a one-line answer.",
        _make_long_prompt(8000)
        + "\n\nQuestion: In one short sentence, name the animal mentioned most often.",
        100,
    ),
    "balanced": (
        "You are a helpful assistant. Answer thoroughly with concrete reasoning.",
        _make_long_prompt(2000)
        + "\n\nWrite an 800-token analysis of the linguistic patterns in the text above. "
          "Cover phonetic structure, repeated motifs, and stylistic register.",
        800,
    ),
    "TG_heavy": (
        "You are a creative writer.",
        "Write a 2000-token continuous narrative about an Apple Silicon engineer "
          "debugging a speculative decoding race condition at 3 AM. Include technical detail "
          "and dialogue. Do not stop early.",
        2000,
    ),
}

# ----- HTTP -----
def post_chat_stream(base_url: str, api_key: str, model: str, system: str, user: str,
                     max_tokens: int, temperature: float = 0.0, timeout: float = 600.0):
    """Stream /v1/chat/completions. Yield (event_type, payload) tuples.
    event_type ∈ {'first_token', 'delta', 'done', 'usage'}"""
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    req = urlreq.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
            "Accept": "text/event-stream",
        },
    )
    first = True
    with urlreq.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                yield ("done", None)
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    if first:
                        first = False
                        yield ("first_token", content)
                    else:
                        yield ("delta", content)
            if obj.get("usage"):
                yield ("usage", obj["usage"])

def measure_run(base_url, api_key, model, system, user, max_tokens) -> dict:
    """Run one streamed completion. Return timing + token stats."""
    t0 = time.perf_counter()
    t_first = None
    text = []
    usage = None
    for ev, p in post_chat_stream(base_url, api_key, model, system, user, max_tokens):
        if ev == "first_token":
            t_first = time.perf_counter()
            text.append(p)
        elif ev == "delta":
            text.append(p)
        elif ev == "usage":
            usage = p
        elif ev == "done":
            break
    t_end = time.perf_counter()

    full = "".join(text)
    ttft = (t_first - t0) if t_first else None
    decode_time = (t_end - t_first) if t_first else None

    pp_tokens = (usage or {}).get("prompt_tokens")
    tg_tokens = (usage or {}).get("completion_tokens")
    # Fallback char/4 if usage missing
    if tg_tokens is None:
        tg_tokens = max(1, len(full) // 4)

    pp_tps = (pp_tokens / ttft) if (pp_tokens and ttft) else None
    tg_tps = (tg_tokens / decode_time) if (tg_tokens and decode_time) else None

    return dict(
        ttft_s=ttft,
        decode_s=decode_time,
        wall_s=t_end - t0,
        pp_tokens=pp_tokens,
        tg_tokens=tg_tokens,
        pp_tps=pp_tps,
        tg_tps=tg_tps,
        out_chars=len(full),
    )

def warmup_probe(base_url, api_key, model) -> float:
    """Tiny request — measures cold→first-token wall time after server start."""
    t0 = time.perf_counter()
    r = measure_run(base_url, api_key, model,
                    "You are a benchmark probe.", "Say 'ok' and nothing else.", 8)
    return time.perf_counter() - t0, r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="e.g. http://localhost:8000/v1")
    ap.add_argument("--api-key", default="", help="Bearer key (omit for none)")
    ap.add_argument("--model", required=True, help="Target model id served by backend")
    ap.add_argument("--label", required=True, help="Setup name for CSV (e.g. oMLX-SpecPrefill)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--workloads", default="PP_heavy,balanced,TG_heavy")
    ap.add_argument("--skip-warmup-probe", action="store_true",
                    help="Skip the cold-warmup measurement (use if server already warm)")
    ap.add_argument("--out", default="bench/results.csv")
    args = ap.parse_args()

    rows = []
    print(f"[{args.label}] base={args.base_url} model={args.model} reps={args.reps}", flush=True)

    if not args.skip_warmup_probe:
        print("warmup probe (cold first-token)...", flush=True)
        try:
            wall, r = warmup_probe(args.base_url, args.api_key, args.model)
            print(f"  warmup wall={wall:.2f}s ttft={r['ttft_s']:.2f}s", flush=True)
            rows.append(dict(label=args.label, workload="WARMUP_PROBE", rep=0,
                             ttft_s=r["ttft_s"], decode_s=r["decode_s"], wall_s=wall,
                             pp_tokens=r["pp_tokens"], tg_tokens=r["tg_tokens"],
                             pp_tps=r["pp_tps"], tg_tps=r["tg_tps"]))
        except Exception as e:
            print(f"  warmup probe FAILED: {type(e).__name__}: {e}", flush=True)

    for wl in args.workloads.split(","):
        wl = wl.strip()
        if wl not in WORKLOADS:
            print(f"unknown workload {wl!r}, skipping", flush=True)
            continue
        sys_p, usr_p, mt = WORKLOADS[wl]
        for rep in range(1, args.reps + 1):
            print(f"\n[{wl} rep {rep}/{args.reps}] sys={len(sys_p)}c usr={len(usr_p)}c max={mt}", flush=True)
            try:
                r = measure_run(args.base_url, args.api_key, args.model, sys_p, usr_p, mt)
                pp_s = f"{r['pp_tps']:.1f}" if r['pp_tps'] else "NA"
                tg_s = f"{r['tg_tps']:.1f}" if r['tg_tps'] else "NA"
                print(f"  ttft={r['ttft_s']:.2f}s decode={r['decode_s']:.2f}s "
                      f"pp_tok={r['pp_tokens']} tg_tok={r['tg_tokens']} "
                      f"pp_tps={pp_s} tg_tps={tg_s}", flush=True)
                rows.append(dict(label=args.label, workload=wl, rep=rep, **r))
            except (HTTPError, URLError, TimeoutError) as e:
                print(f"  FAILED: {type(e).__name__}: {e}", flush=True)
                rows.append(dict(label=args.label, workload=wl, rep=rep, error=str(e)))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    write_header = not os.path.exists(args.out)
    keys = ["label","workload","rep","ttft_s","decode_s","wall_s",
            "pp_tokens","tg_tokens","pp_tps","tg_tps","out_chars","error"]
    with open(args.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\n→ wrote {len(rows)} rows to {args.out}", flush=True)

if __name__ == "__main__":
    sys.exit(main() or 0)
