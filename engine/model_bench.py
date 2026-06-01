"""Model capability + speed benchmark for auto-routing.

Measures each enabled model against the auto-route task types (coding, math,
research, analysis, reporting, creative, orchestration, agentic, fast). For a
(model x task_type) cell it runs a small fixed prompt set, judges each answer
0-100 with the SERVER DEFAULT MODEL as judge, and records the mean capability%
plus the mean answer latency. Results persist into `config.json -> models.<id>.
benchmark` so the router (`brain._resolve_auto_model_tiered`) can rank by
capability -> speed -> cost instead of guessing from priority.

Storage shape (per model config entry):
    "benchmark": {
        "<task_type>": {
            "measured": {"capability": 0-100, "tps": float, "n": int,
                         "ts": "<iso>"},
            "override": {"capability": 0-100, "tps": float}   # optional
        },
        ...
    }
`tps` = mean output-token throughput (tokens/sec) — length-independent speed.
The router reads `override or measured` per cell — an admin edit is sticky and a
later benchmark run only rewrites `measured` (see brain.bench_cell_value).

This module owns the PROMPT SETS + run/judge loop only; persistence into
config.json + the HTTP trigger live in handlers/providers.py, and consumption
in brain.py. No top-level `import brain` (cycle) — reach it lazily.
"""

from __future__ import annotations

import datetime
import re
import time

# The task-type vocabulary mirrors brain._TASK_TYPE_TIER. Kept here as the
# benchmark's source of prompts; brain validates the key set on read.
#
# Each task type gets a few short prompts that EXERCISE the capability the task
# name implies. They are intentionally model-agnostic and need no tools (the
# judge grades the text reply). Keep them small — a full run is models x types x
# prompts x (1 answer + 1 judge) LLM calls.
BENCH_PROMPTS: dict[str, list[str]] = {
    "coding": [
        "Write a Python function `dedupe_stable(xs)` that removes duplicates from a list while preserving first-seen order. Return only the code.",
        "This code raises IndexError sometimes: `def last(xs): return xs[len(xs)]`. Explain the bug and give the fixed one-liner.",
    ],
    "math": [
        "A train travels 120 km in 1.5 hours, then 200 km in 2 hours. What is its average speed over the whole trip, in km/h? Show the steps.",
        "Solve for x: 3(x - 4) = 2x + 5. Show each step.",
    ],
    "research": [
        "List the main trade-offs between vector search and keyword (BM25) search for document retrieval. Be specific and balanced.",
        "What are the key differences between HTTP/1.1 and HTTP/2 that affect web performance?",
    ],
    "analysis": [
        "A team's test suite passes locally but fails in CI 20% of the time. Reason through the most likely root causes, most probable first, with why.",
        "Explain why adding more workers to a queue does not linearly increase throughput once the downstream database is saturated.",
    ],
    "reporting": [
        "Summarize the following into 3 crisp bullet points: 'The migration finished Tuesday. Two services needed rollback. Latency improved 15% afterward but error rates briefly spiked during the cutover.'",
        "Write a 2-sentence executive summary of a project that shipped on time, 5% over budget, with high user satisfaction.",
    ],
    "creative": [
        "Suggest 5 distinct, memorable product names for a privacy-first note-taking app. One line each, no explanation.",
        "Write a single vivid opening sentence for a short story about a lighthouse keeper who has stopped sleeping.",
    ],
    "orchestration": [
        "Break this goal into an ordered list of 4-6 concrete sub-tasks: 'Launch a weekly automated report that emails our top 5 support topics.' Number them.",
        "Plan the steps to migrate a database table to a new schema with zero downtime. List the steps in order.",
    ],
    "agentic": [
        "You have tools: web search, file read/write, send email. Describe the exact sequence of tool calls to research a topic online and email a summary. Be concrete about each step.",
        "Given a folder of PDFs, outline the tool steps to extract their text and build a searchable index.",
    ],
    "fast": [
        "What is the capital of Australia? One word.",
        "Convert 2.5 hours to minutes. Answer with just the number.",
    ],
}

TASK_TYPES = tuple(BENCH_PROMPTS.keys())

_JUDGE_SYSTEM = (
    "You are a strict grader. You are given a TASK TYPE, a PROMPT, and a MODEL "
    "ANSWER. Rate how well the answer fulfils the prompt FOR THAT TASK TYPE on a "
    "0-100 scale: 100 = fully correct, complete, well-formed; 50 = partially "
    "right or incomplete; 0 = wrong, empty, or off-topic. Judge correctness and "
    "fitness for the task type, not length or style. Reply with ONLY the integer "
    "score, nothing else."
)


def _judge_prompt(task_type: str, prompt: str, answer: str) -> str:
    return (
        f"TASK TYPE: {task_type}\n\n"
        f"PROMPT:\n{prompt}\n\n"
        f"MODEL ANSWER:\n{answer[:4000]}\n\n"
        "Score (0-100):"
    )


def _parse_score(reply: str) -> int | None:
    """Pull the first integer 0-100 out of the judge reply; None if none."""
    if not reply:
        return None
    m = re.search(r"\b(100|[0-9]{1,2})\b", reply)
    if not m:
        return None
    try:
        v = int(m.group(1))
    except ValueError:
        return None
    return max(0, min(100, v))


def _now_iso() -> str:
    # new datetime() forbidden in workflow scripts only; this is normal module
    # code (server runtime), so utcnow() is fine here.
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def benchmark_cell(model: str, task_type: str, judge_model: str,
                   *, background_call, timeout_s: float = 60.0) -> dict:
    """Run one (model x task_type) cell: answer every prompt, judge each,
    average. `background_call` is injected (handlers.sidecar_proxy.background_call)
    to keep this module import-cycle-free and unit-testable.

    Returns {capability: 0-100, latency_ms: int, n: int, ts, error?}. On total
    failure (model errored on every prompt) returns capability 0 + error note.
    """
    prompts = BENCH_PROMPTS.get(task_type) or []
    if not prompts:
        return {"capability": 0, "latency_ms": 0, "n": 0, "ts": _now_iso(),
                "error": f"no prompts for task type '{task_type}'"}

    scores: list[int] = []
    throughputs: list[float] = []  # tokens/sec per prompt
    errors: list[str] = []

    for prompt in prompts:
        # Time the answer call ourselves — run_turn_blocking surfaces no timing.
        # Speed is ranked on THROUGHPUT (output tokens / sec), which is
        # length-independent and comparable across tasks (unlike raw latency,
        # which just tracks how long the answer happened to be).
        _t0 = time.monotonic()
        ans = background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            purpose="transform",
            max_tokens=1024,
            max_rounds=1,
            timeout_s=timeout_s,
        )
        _secs = max(1e-6, time.monotonic() - _t0)
        if ans.get("error"):
            errors.append(str(ans["error"])[:120])
            continue
        reply = (ans.get("reply") or "").strip()
        _out_tok = int((ans.get("usage_total") or {}).get("output_tokens") or 0)
        if _out_tok > 0:
            throughputs.append(_out_tok / _secs)
        if not reply:
            scores.append(0)
            continue
        # Judge the answer with the server default model.
        judged = background_call(
            messages=[{"role": "user", "content": _judge_prompt(task_type, prompt, reply)}],
            model=judge_model,
            system_prompt=_JUDGE_SYSTEM,
            purpose="transform",
            max_tokens=8,
            max_rounds=1,
            timeout_s=timeout_s,
        )
        if judged.get("error"):
            errors.append("judge: " + str(judged["error"])[:100])
            continue
        sc = _parse_score(judged.get("reply") or "")
        scores.append(sc if sc is not None else 0)

    if not scores:
        return {"capability": 0, "tps": 0, "n": 0, "ts": _now_iso(),
                "error": "; ".join(errors[:3]) or "no scored prompts"}

    cap = round(sum(scores) / len(scores))
    tps = round(sum(throughputs) / len(throughputs), 1) if throughputs else 0
    out = {"capability": cap, "tps": tps, "n": len(scores), "ts": _now_iso()}
    if errors:
        out["error"] = "; ".join(errors[:3])
    return out


def benchmark_model(model: str, judge_model: str, *, background_call,
                    task_types: list[str] | None = None,
                    timeout_s: float = 60.0) -> dict:
    """Benchmark one model across all (or the given) task types.

    Returns {task_type: cell_result} — the `measured` block per cell. The caller
    merges this into config.json under models.<id>.benchmark.<task>.measured,
    preserving any existing `override`.
    """
    types = [t for t in (task_types or TASK_TYPES) if t in BENCH_PROMPTS]
    return {t: benchmark_cell(model, t, judge_model,
                              background_call=background_call, timeout_s=timeout_s)
            for t in types}
