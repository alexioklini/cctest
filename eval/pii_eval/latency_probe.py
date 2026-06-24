#!/usr/bin/env python3
"""Latency + parallelization probe for the M4-7B PII pass.

Putting M4 in the PII hot path adds an LLM call to every scan. This measures:
  1. ours-alone scan latency (the baseline cost today) over the live server.
  2. M4 per-call latency on realistic PII-scan inputs (full sentences/windows).
  3. M4 CONCURRENCY behavior: fire N requests in parallel and compare wall-clock
     to N sequential. If the host serializes (per project_omlx_batching_measured /
     vllm-metal max_concurrent), parallel wall-clock ~= sequential and there's no
     throughput win from fan-out — which decides whether a batched PII pass helps.

Run from repo root (any venv with stdlib):
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/pii_eval/latency_probe.py
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "adapters"))
from common import load_jsonl  # noqa: E402

N_PAR = int(os.environ.get("PII_PAR", "8"))


def _percentile(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    return xs[f] if f + 1 >= len(xs) else xs[f] + (xs[f + 1] - xs[f]) * (k - f)


def _stats(xs):
    return {"n": len(xs), "mean": sum(xs) / len(xs) if xs else 0,
            "p50": _percentile(xs, .5), "p95": _percentile(xs, .95),
            "min": min(xs) if xs else 0, "max": max(xs) if xs else 0}


def _fmt(label, s):
    print(f"  {label:28s} n={s['n']:3d}  mean={s['mean']*1000:6.0f}ms  "
          f"p50={s['p50']*1000:6.0f}ms  p95={s['p95']*1000:6.0f}ms  "
          f"max={s['max']*1000:6.0f}ms")


def probe_ours(texts):
    import ours_adapter
    lat = []
    for t in texts:
        t0 = time.time()
        try:
            ours_adapter.detect_full(t)
        except Exception as e:
            print("   ours error:", e); continue
        lat.append(time.time() - t0)
    return lat


def probe_m4_sequential(texts):
    import m4_llm_adapter
    lat = []
    for t in texts:
        t0 = time.time()
        try:
            m4_llm_adapter.detect(t)
        except Exception as e:
            print("   m4 error:", e); continue
        lat.append(time.time() - t0)
    return lat


def probe_m4_parallel(texts, workers):
    import m4_llm_adapter
    t0 = time.time()
    lat = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_timed, m4_llm_adapter.detect, t): t for t in texts}
        for fu in cf.as_completed(futs):
            try:
                lat.append(fu.result())
            except Exception as e:
                print("   m4 par error:", e)
    wall = time.time() - t0
    return lat, wall


def _timed(fn, t):
    t0 = time.time()
    fn(t)
    return time.time() - t0


def main():
    rows = load_jsonl(os.path.join(HERE, "data", "handcrafted_de.jsonl"))
    texts = [r["text"] for r in rows][:24]  # realistic short PII-scan inputs
    print(f"[latency] {len(texts)} inputs, parallel workers={N_PAR}\n")

    print("1) ours-alone (live server regex+NER) — the cost TODAY:")
    _fmt("ours scan", _stats(probe_ours(texts)))

    print("\n2) M4 per-call (sequential) — realistic PII-scan sentences:")
    seq = probe_m4_sequential(texts)
    _fmt("m4 sequential", _stats(seq))
    seq_total = sum(seq)

    print(f"\n3) M4 parallel ({N_PAR} workers) — does the host actually batch?")
    par_lat, par_wall = probe_m4_parallel(texts, N_PAR)
    _fmt("m4 parallel (per-call)", _stats(par_lat))
    print(f"\n  sequential total wall : {seq_total:6.1f}s for {len(seq)} calls")
    print(f"  parallel   total wall : {par_wall:6.1f}s for {len(par_lat)} calls "
          f"({N_PAR} workers)")
    speedup = seq_total / par_wall if par_wall else 0
    print(f"  => effective speedup  : {speedup:.2f}x "
          f"({'real batching' if speedup > 1.5 else 'host SERIALIZES — fan-out does not help'})")
    print(f"\n  per-call latency under load p95: {_percentile(par_lat,.95)*1000:.0f}ms "
          f"(this is what a user waits if M4 is in the hot path under concurrency)")


if __name__ == "__main__":
    main()
