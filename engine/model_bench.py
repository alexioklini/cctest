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
# DISCRIMINATION is the whole point: the prior 2-trivial-prompts-per-cell set
# ("capital of Australia?") let a 2B model score 95-100 on every task, identical
# to a frontier model, so the router's capability FLOOR never separated anyone
# and the pick collapsed onto a noisy 0.3-tok/s speed tiebreak. The new sets are
# TIERED (easy → hard within each task) so a weak model passes the easy prompts
# and fails the hard ones, producing a SPREAD of capability scores. Each task
# has an easy anchor (so a weak model isn't pinned at 0) and 2-3 genuinely hard
# items (so a strong model pulls ahead).
#
# Each entry is a dict:
#   {"prompt": str, "check": <checker | None>}
# `check` is a DETERMINISTIC verifier for prompts with an objective answer —
# scored 0 or 100 by code, no judge call (cheaper, zero judge variance):
#   {"type": "exact",    "answer": "canberra"}      → normalized substring/equality
#   {"type": "regex",    "pattern": r"..."}          → re.search on the (lowered) reply
#   {"type": "all",      "needles": ["a", "b"]}      → ALL substrings present (lowered)
#   {"type": "pyfunc",   "name": "f", "cases": [...]} → exec the returned code, run cases
# `check: None` → the prompt is open-ended (research/creative/analysis/etc.) and
# is graded by the LLM judge (`_JUDGE_SYSTEM`) on 0-100 as before.
#
# Keep it bounded — a full run is models × types × prompts × (1 answer + ≤1
# judge) calls; deterministic checks skip the judge call entirely.
BENCH_TASKS: dict[str, list[dict]] = {
    "coding": [
        # easy anchor — runnable, exact behavior checked
        {"prompt": "Write a Python function `dedupe_stable(xs)` that removes duplicates from a list while preserving first-seen order. Return ONLY the code, no prose, no markdown fences.",
         "check": {"type": "pyfunc", "name": "dedupe_stable",
                   "cases": [[[[1, 1, 2, 3, 2, 1]], [1, 2, 3]], [[[]], []], [[["a", "b", "a"]], ["a", "b"]]]}},
        # medium — off-by-one fix, checkable
        {"prompt": "Write a Python function `binary_search(a, x)` returning the index of x in the sorted list a, or -1 if absent. Use iterative bisection. Return ONLY the code, no fences.",
         "check": {"type": "pyfunc", "name": "binary_search",
                   "cases": [[[[1, 3, 5, 7, 9], 7], 3], [[[1, 3, 5, 7, 9], 4], -1], [[[], 1], -1], [[[2], 2], 0]]}},
        # hard — recursion + edge cases, checkable
        {"prompt": "Write a Python function `roman(n)` converting an integer 1..3999 to a Roman numeral string (uppercase). Return ONLY the code, no fences.",
         "check": {"type": "pyfunc", "name": "roman",
                   "cases": [[[4], "IV"], [[9], "IX"], [[1994], "MCMXCIV"], [[3888], "MMMDCCCLXXXVIII"]]}},
        # debugging — open-ended explanation, judged
        {"prompt": "This code raises IndexError sometimes: `def last(xs): return xs[len(xs)]`. Explain the bug precisely and give the corrected one-line body.",
         "check": {"type": "regex", "pattern": r"xs\[\s*-?\s*1\s*\]|len\(xs\)\s*-\s*1"}},
    ],
    "math": [
        {"prompt": "Solve for x: 3(x - 4) = 2x + 5. Reply with the final value of x on the last line as 'x = <number>'.",
         "check": {"type": "regex", "pattern": r"x\s*=\s*17\b"}},
        {"prompt": "A train travels 120 km in 1.5 hours, then 200 km in 2 hours. What is its average speed over the whole trip in km/h? Show steps; put the final number on the last line.",
         "check": {"type": "regex", "pattern": r"\b91\.4\d*\b|\b91[.,]43\b|\b320\s*/\s*3\.5\b"}},
        {"prompt": "Compute the probability of rolling a sum of exactly 7 with two fair six-sided dice. Give the exact fraction in lowest terms.",
         "check": {"type": "regex", "pattern": r"\b1\s*/\s*6\b"}},
        {"prompt": "What is the derivative of f(x) = 3x^3 - 5x^2 + 2x - 7 with respect to x? Give f'(x).",
         "check": {"type": "all", "needles": ["9x", "10x", "2"]}},
        {"prompt": "A shirt costs 80 EUR after a 20% discount. What was the original price in EUR? Final number on the last line.",
         "check": {"type": "regex", "pattern": r"\b100\b"}},
    ],
    "research": [
        # all open-ended → judged; harder, more specific than before
        {"prompt": "Compare vector (dense embedding) search and BM25 keyword search for document retrieval across at least four dimensions (recall on paraphrase, exact-term/rare-token matching, index cost, interpretability). Be specific and balanced.",
         "check": None},
        {"prompt": "Explain the key differences between HTTP/1.1 and HTTP/2 that affect web performance (head-of-line blocking, multiplexing, header compression, server push) and note where HTTP/3 changes the picture.",
         "check": None},
        {"prompt": "What are the practical trade-offs between optimistic and pessimistic concurrency control in a database, and when would you choose each?",
         "check": None},
    ],
    "analysis": [
        {"prompt": "A test suite passes locally but fails ~20% of the time in CI. Enumerate the most likely root causes, ordered most-probable first, each with a one-line reason and a concrete way to confirm it.",
         "check": None},
        {"prompt": "Adding more workers to a job queue stopped increasing throughput past a point. Explain the most likely bottleneck mechanisms (downstream saturation, lock contention, connection-pool limits) and how to diagnose which one applies.",
         "check": None},
        {"prompt": "A web service's p50 latency is fine but p99 is 10× worse. Reason through the likely causes (GC pauses, tail-amplifying fan-out, cold caches, lock queueing) and which metric would confirm each.",
         "check": None},
    ],
    "reporting": [
        {"prompt": "Summarize into exactly 3 crisp bullet points: 'The migration finished Tuesday. Two services needed rollback. Latency improved 15% afterward but error rates briefly spiked during the cutover.' Output only the three bullets.",
         "check": None},
        {"prompt": "Write a 2-sentence executive summary of a project that shipped on time, 5% over budget, with high user satisfaction. Exactly two sentences.",
         "check": None},
        {"prompt": "Turn this changelog into a one-paragraph release note for non-technical readers: 'Refactored auth; added rate limiting per user; fixed a memory leak in the uploader; switched the default model.'",
         "check": None},
    ],
    "creative": [
        {"prompt": "Suggest 5 distinct, memorable, non-generic product names for a privacy-first note-taking app. One per line, no explanation, no numbering.",
         "check": None},
        {"prompt": "Write a single vivid opening sentence (one sentence) for a short story about a lighthouse keeper who has stopped sleeping.",
         "check": None},
        {"prompt": "Write a four-line poem about a deprecated API that still runs in production. No title.",
         "check": None},
    ],
    "orchestration": [
        {"prompt": "Break this goal into an ordered list of 4-6 concrete sub-tasks: 'Launch a weekly automated report that emails our top 5 support topics.' Number them 1..N.",
         "check": None},
        {"prompt": "Plan the steps to migrate a heavily-read database table to a new schema with ZERO downtime (dual-write, backfill, cutover, cleanup). List the steps in order.",
         "check": None},
        {"prompt": "You must roll out a risky config change to 10,000 servers safely. Lay out the ordered plan (canary, metrics gate, staged ramp, automatic rollback). Numbered steps.",
         "check": None},
    ],
    "agentic": [
        {"prompt": "You have tools: web_search, read_file, write_file, send_email. Give the EXACT ordered sequence of tool calls to research a topic online and email a one-page summary. One tool call per line as tool_name(args).",
         "check": None},
        {"prompt": "Given a folder of PDFs and tools read_file, write_file, and an index() tool, outline the exact tool-call sequence to extract their text and build a searchable index. One call per line.",
         "check": None},
        {"prompt": "A user asks: 'find last month's invoices in my Drive and total them.' You have tools: drive_search, read_file, python_exec. Give the precise ordered tool calls and what each returns. One call per line.",
         "check": None},
    ],
    "fast": [
        # short factual / arithmetic — deterministic, but now FOUR items so a
        # weak model can actually slip on one (e.g. the unit conversion).
        {"prompt": "What is the capital of Australia? Reply with one word only.",
         "check": {"type": "exact", "answer": "canberra"}},
        {"prompt": "Convert 2.5 hours to minutes. Reply with just the number.",
         "check": {"type": "regex", "pattern": r"\b150\b"}},
        {"prompt": "What is 17 × 23? Reply with just the number.",
         "check": {"type": "regex", "pattern": r"\b391\b"}},
        {"prompt": "How many days are in a leap year? Reply with just the number.",
         "check": {"type": "regex", "pattern": r"\b366\b"}},
    ],
}

# Back-compat: some callers/readers expect a {task: [prompt_str, ...]} view.
BENCH_PROMPTS: dict[str, list[str]] = {
    t: [p["prompt"] for p in items] for t, items in BENCH_TASKS.items()
}

TASK_TYPES = tuple(BENCH_TASKS.keys())

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


def _strip_code_fences(text: str) -> str:
    """Drop ```...``` fences if the model wrapped code despite being asked not to."""
    m = re.search(r"```(?:python|py)?\s*\n?(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def _run_pyfunc_check(reply: str, name: str, cases: list) -> bool:
    """Exec the model's returned code in a restricted namespace and run `cases`
    (each [args_list, expected]) against the function `name`. True iff ALL pass.

    Deliberately minimal sandbox: no builtins beyond a safe subset, hard
    AST-free guard via empty __builtins__ plus a small allowlist. The prompts
    ask for pure list/int/string functions, so nothing here needs IO. A model
    that emits import/exec/open simply fails the check (returns False), which is
    the correct benchmark outcome — it didn't answer the prompt as asked."""
    code = _strip_code_fences(reply)
    if not code or f"def {name}" not in code:
        return False
    safe_builtins = {
        "range": range, "len": len, "enumerate": enumerate, "list": list,
        "dict": dict, "set": set, "tuple": tuple, "str": str, "int": int,
        "float": float, "bool": bool, "abs": abs, "min": min, "max": max,
        "sum": sum, "sorted": sorted, "reversed": reversed, "map": map,
        "filter": filter, "zip": zip, "any": any, "all": all, "round": round,
        "divmod": divmod, "ord": ord, "chr": chr, "isinstance": isinstance,
    }
    ns: dict = {"__builtins__": safe_builtins}
    try:
        exec(code, ns)  # noqa: S102 — benchmark sandbox, restricted builtins
    except Exception:
        return False
    fn = ns.get(name)
    if not callable(fn):
        return False
    for args, expected in cases:
        try:
            got = fn(*args)
        except Exception:
            return False
        if got != expected:
            return False
    return True


def _deterministic_score(check: dict, reply: str) -> int | None:
    """Score a reply against a deterministic check spec → 0 or 100, or None if
    the check type is unknown (caller then falls back to the LLM judge).

    Matching is case-insensitive and whitespace-tolerant for text checks; the
    `pyfunc` check actually executes the code. None of these call the LLM, so
    they cost nothing and carry zero judge variance."""
    if not isinstance(check, dict):
        return None
    ctype = check.get("type")
    low = (reply or "").lower()
    if ctype == "exact":
        ans = str(check.get("answer", "")).lower().strip()
        return 100 if ans and ans in low else 0
    if ctype == "regex":
        pat = check.get("pattern") or ""
        try:
            return 100 if re.search(pat, low) else 0
        except re.error:
            return None
    if ctype == "all":
        needles = [str(n).lower() for n in (check.get("needles") or [])]
        return 100 if needles and all(n in low for n in needles) else 0
    if ctype == "pyfunc":
        return 100 if _run_pyfunc_check(reply, check.get("name", ""),
                                        check.get("cases") or []) else 0
    return None


def _now_iso() -> str:
    # new datetime() forbidden in workflow scripts only; this is normal module
    # code (server runtime), so utcnow() is fine here.
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def benchmark_cell(model: str, task_type: str, judge_model: str,
                   *, background_call, timeout_s: float = 60.0) -> dict:
    """Run one (model x task_type) cell: answer every prompt, score each
    (deterministic check where the prompt has one, else LLM judge), average.
    `background_call` is injected (handlers.sidecar_proxy.background_call) to
    keep this module import-cycle-free and unit-testable.

    Returns {capability: 0-100, tps: float, n: int, ts, error?}. On total
    failure (model errored on every prompt) returns capability 0 + error note.
    """
    items = BENCH_TASKS.get(task_type) or []
    if not items:
        return {"capability": 0, "tps": 0, "n": 0, "ts": _now_iso(),
                "error": f"no prompts for task type '{task_type}'"}

    scores: list[int] = []
    throughputs: list[float] = []  # tokens/sec per prompt
    errors: list[str] = []

    for item in items:
        prompt = item["prompt"]
        check = item.get("check")
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
            account_cost=False,  # benchmark measurement, not user-facing spend
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
        # Deterministic check first (0/100, no judge call) for prompts with an
        # objective answer; only open-ended prompts (check is None / unknown
        # type) fall through to the LLM judge.
        det = _deterministic_score(check, reply) if check else None
        if det is not None:
            scores.append(det)
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
            account_cost=False,  # benchmark measurement, not user-facing spend
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


def effective_task_types(task_types: list[str] | None = None) -> list[str]:
    """The task types a benchmark run will actually execute (callers use this
    to size progress totals before the run starts)."""
    return [t for t in (task_types or TASK_TYPES) if t in BENCH_PROMPTS]


def benchmark_model(model: str, judge_model: str, *, background_call,
                    task_types: list[str] | None = None,
                    timeout_s: float = 60.0,
                    progress_cb=None) -> dict:
    """Benchmark one model across all (or the given) task types.

    Returns {task_type: cell_result} — the `measured` block per cell. The caller
    merges this into config.json under models.<id>.benchmark.<task>.measured,
    preserving any existing `override`.

    `progress_cb(task_type, done_cells)` (optional) fires before each cell so
    live progress can surface per task, not just per model.
    """
    types = effective_task_types(task_types)
    out = {}
    for i, t in enumerate(types):
        if progress_cb:
            try:
                progress_cb(t, i)
            except Exception:
                pass
        out[t] = benchmark_cell(model, t, judge_model,
                                background_call=background_call, timeout_s=timeout_s)
    return out
