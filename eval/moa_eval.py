#!/usr/bin/env python3
"""MoA eval — does Mixture-of-Agents (N reference models -> 1 aggregator) beat the
best single model on the same question, with OUR cloud models (Mistral + DeepSeek)?

This mirrors the Hermes "Mixture of Agents" mechanism we want to integrate into Brain:
  1. REFERENCE phase — each reference model answers the question WITHOUT tools and
     WITHOUT a system prompt (plain user text only). They do not see each other.
  2. AGGREGATION   — the aggregator model gets the question PLUS the reference drafts
     appended as private context, and writes the final answer.
The baseline arms run a single model alone (no references) — that is what MoA must beat.

Like eval/refine_eval.py the answer-generation is STANDALONE: it reads config.json,
resolves each provider directly, and POSTs /chat/completions. No running Brain/sidecar
server is needed. NO local models are used (Mistral medium/small + DeepSeek only).

JUDGING IS TWO-PHASE (Opus judges, but Opus is not reachable via our config providers):
  Phase "gen"   (this script): generate answers for every (arm,question,rep), check
                checkable questions programmatically against gold where possible, and
                write every answer needing a human/LLM judgment into a BLIND queue
                (arm label masked) at eval/results/moa_judge_queue.json.
  Phase "judge" (Opus, in the Claude Code session): read the queue, score each blind
                answer per eval/moa_rubric.md, write eval/results/moa_judge_scores.json
                keyed by a stable answer-hash so scores are REUSED across runs.
  Phase "report"(this script, --phase report): join answers + scores, double-weight
                correctness on checkable questions, print per-arm means +- spread and a
                noise-aware verdict.

A single-run delta under the per-question spread is NOISE not signal
(feedback_eval_single_run_noise) — hence >= 3 reps and a spread-aware verdict.
Output is line-buffered (flush=True) so a backgrounded run streams live
(feedback_evals_line_buffered).

Usage:
  python3 -u eval/moa_eval.py --phase gen --reps 3      # generate answers + queue
  #   -> then ask Opus to run the judge phase on the queue (writes scores file)
  python3 -u eval/moa_eval.py --phase report            # aggregate + verdict
  python3 -u eval/moa_eval.py --phase gen --only HARD_MATH3_modular --arms baseline_M,moa_SMD
"""
import argparse
import hashlib
import json
from concurrent import futures
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
RESULTS = os.path.join(REPO_ROOT, "eval", "results")
ANSWERS_PATH = os.path.join(RESULTS, "moa_answers.json")
QUEUE_PATH = os.path.join(RESULTS, "moa_judge_queue.json")
SCORES_PATH = os.path.join(RESULTS, "moa_judge_scores.json")

# ── model ids (must exist in config.json[models]) ─────────────────────────────
M_SMALL = "mistral-small-latest"
M_MEDIUM = "mistral-medium-latest"
M_DEEPSEEK = "deepseek-v4-pro"

ARMS = {
    "baseline_S":  {"references": [],                              "aggregator": M_SMALL},
    "baseline_M":  {"references": [],                              "aggregator": M_MEDIUM},
    "baseline_D":  {"references": [],                              "aggregator": M_DEEPSEEK},
    "moa_SM":      {"references": [M_SMALL, M_MEDIUM],             "aggregator": M_MEDIUM},
    "moa_SMD":     {"references": [M_SMALL, M_MEDIUM, M_DEEPSEEK], "aggregator": M_MEDIUM},
    "moa_MD_aggD": {"references": [M_MEDIUM, M_DEEPSEEK],          "aggregator": M_DEEPSEEK},
}

CHECKABLE = {"reasoning_checkable"}  # double-weight correctness here


# ── config / provider resolution (mirrors eval/refine_eval.py) ────────────────

def _load_config() -> dict:
    with open(os.path.join(REPO_ROOT, "config.json")) as f:
        return json.load(f)


def _resolve_provider(model_id: str, config: dict):
    models = config.get("models", {})
    providers = config.get("providers", {})
    if model_id not in models:
        raise SystemExit(f"model {model_id!r} not in config.json[models]")
    m = models[model_id]
    provider_name = m.get("provider")
    base_model = m.get("base_model_id") or model_id.rsplit("/", 1)[-1]
    if not provider_name or provider_name not in providers:
        raise SystemExit(
            f"provider {provider_name!r} for {model_id!r} not in config.json[providers]")
    p = providers[provider_name]
    return p["api_key"], p["base_url"].rstrip("/"), base_model


def _approx_tokens(s: str) -> int:
    return max(1, len(s or "") // 4)


def call_llm(config, model_id, messages, *, timeout=240.0, temperature=0.3,
             max_tokens=2048):
    api_key, base_url, base_model = _resolve_provider(model_id, config)
    body = {"model": base_model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base_url + "/chat/completions", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        blob = json.loads(resp.read().decode("utf-8"))
    text = (blob.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    usage = blob.get("usage") or {}
    pt = usage.get("prompt_tokens") or _approx_tokens(
        "".join(m.get("content", "") for m in messages))
    ct = usage.get("completion_tokens") or _approx_tokens(text)
    return text, pt, ct


# ── MoA mechanism (the thing under test) ──────────────────────────────────────

_AGG_SYSTEM = (
    "You have been given a user question together with candidate answers drafted "
    "independently by several other AI models. The drafts may disagree, and some may "
    "be wrong. Do NOT trust any draft blindly. Synthesize a single, correct, complete "
    "final answer: verify the reasoning, reconcile disagreements, fix errors, and fill "
    "gaps. Answer the user directly — do not mention the drafts or that you were given "
    "any. If the question demands specific parts (a fraction, a final recommendation, "
    "a ranked list), include every demanded part."
)


def _build_aggregator_messages(question, ref_drafts):
    if not ref_drafts:
        return [{"role": "user", "content": question}]
    block = "\n\n".join(
        f"--- Draft {chr(65 + i)} ---\n{txt.strip()}"
        for i, (_lbl, txt) in enumerate(ref_drafts) if (txt or "").strip())
    user = (f"{question}\n\n"
            f"=== Candidate answers from other models (private context, do not quote) ===\n"
            f"{block}\n=== end candidate answers ===")
    return [{"role": "system", "content": _AGG_SYSTEM},
            {"role": "user", "content": user}]


def run_arm(config, arm, question, *, temperature):
    # REFERENCE phase runs the N reference models CONCURRENTLY — that is how Brain
    # would run it (background_call fan-out, gated by LocalProviderQueue), so the
    # measured latency reflects production, not an artificial serial sum. Results are
    # kept in the declared reference ORDER (not completion order) for a stable prompt.
    refs_by_idx, ref_pt, ref_ct, ref_errors = {}, 0, 0, []
    if arm["references"]:
        with futures.ThreadPoolExecutor(max_workers=len(arm["references"])) as ex:
            fut_to_idx = {
                ex.submit(call_llm, config, rm,
                          [{"role": "user", "content": question}],
                          temperature=temperature): (i, rm)
                for i, rm in enumerate(arm["references"])}
            for fut in futures.as_completed(fut_to_idx):
                i, rm = fut_to_idx[fut]
                try:
                    txt, pt, ct = fut.result()
                    refs_by_idx[i] = (rm, txt)
                    ref_pt += pt
                    ref_ct += ct
                except Exception as e:  # credential/transport fail must NOT abort the arm
                    ref_errors.append(f"{rm}: {e}")
    refs = [refs_by_idx[i] for i in sorted(refs_by_idx)]
    agg_msgs = _build_aggregator_messages(question, refs)
    ans, apt, act = call_llm(config, arm["aggregator"], agg_msgs, temperature=temperature)
    return {"answer": ans, "n_refs_ok": len(refs), "ref_errors": ref_errors,
            "total_tokens": ref_pt + ref_ct + apt + act}


# ── programmatic gold check (free, deterministic, stable across runs) ──────────

def _numbers(s: str):
    """Pull numeric tokens (ints/decimals) from text for gold matching."""
    return [x.replace(",", "") for x in re.findall(r"-?\d[\d,]*\.?\d*", s or "")]


def auto_check(q, answer):
    """Return (verdict, detail) where verdict in {'pass','fail',None}.
    None = cannot decide programmatically -> goes to the blind LLM judge.
    We ONLY auto-decide when the gold has a crisp final value we can require in the
    answer (a number or short phrase). Anything ambiguous defers to the judge so we
    never silently mis-grade."""
    gold = q.get("gold")
    if not gold or q["category"] not in CHECKABLE:
        return None, "deferred (open question)"
    # Extract crisp expected tokens from gold for a few well-defined questions.
    # Strategy: require the gold's KEY final number(s) to appear in the answer.
    expected = {
        "HARD_MATH1_trains": ["360"],
        "HARD_MATH3_modular": ["49"],
        "HARD_PROB1_monty": ["3/4", "1/4"],
        "HARD_LOGIC1_hats": ["blue"],
    }.get(q["id"])
    if not expected:
        return None, "deferred (no crisp auto-rule)"
    low = (answer or "").lower()
    ans_nums = set(_numbers(answer))
    missing = []
    for tok in expected:
        t = tok.lower()
        if "/" in tok or not tok.replace(".", "").isdigit():
            if t not in low:
                missing.append(tok)
        else:
            if tok not in ans_nums and t not in low:
                missing.append(tok)
    if missing:
        return "fail", f"missing required gold token(s): {missing}"
    return "pass", f"all required gold tokens present: {expected}"


# ── answer hashing for the judge-score cache (reuse across runs) ───────────────

def answer_hash(qid, answer):
    h = hashlib.sha256()
    h.update(qid.encode("utf-8"))
    h.update(b"\x00")
    h.update((answer or "").strip().encode("utf-8"))
    return h.hexdigest()[:16]


# ── stats ─────────────────────────────────────────────────────────────────────

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _spread(xs):
    return statistics.stdev(xs) if len(xs) >= 2 else 0.0


def _overall_from_axes(sc, category):
    axes = ["correctness", "completeness", "reasoning", "calibration"]
    if any(a not in sc for a in axes):
        return None
    if category in CHECKABLE:
        w = {"correctness": 2.0, "completeness": 1.0, "reasoning": 1.0, "calibration": 1.0}
        return sum(sc[a] * w[a] for a in axes) / sum(w.values())
    return sum(sc[a] for a in axes) / len(axes)


# ── PHASE: gen ────────────────────────────────────────────────────────────────

def phase_gen(args):
    config = _load_config()
    with open(os.path.join(REPO_ROOT, "eval", "moa_questions.json")) as f:
        questions = json.load(f)
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in want]
    arm_names = ([x.strip() for x in args.arms.split(",") if x.strip()]
                 if args.arms else list(ARMS.keys()))
    for a in arm_names:
        if a not in ARMS:
            raise SystemExit(f"unknown arm {a!r}; known: {list(ARMS)}")

    os.makedirs(RESULTS, exist_ok=True)
    print(f"PHASE gen — {len(questions)} questions x {len(arm_names)} arms x {args.reps} "
          f"reps = {len(questions) * len(arm_names) * args.reps} answers", flush=True)
    print(f"arms: {arm_names}  temp: {args.temperature}\n", flush=True)

    # answers[qid] = list of {arm, rep, answer, total_tokens, latency, auto, hash, ...}
    answers = {}
    queue = []   # blind judge items (arm masked)
    t_start = time.time()
    qmeta = {q["id"]: {"category": q["category"], "question": q["question"],
                       "gold": q.get("gold")} for q in questions}
    qById = {q["id"]: q for q in questions}

    # Fan out every (question, arm, rep) job concurrently. Each MoA job itself fans out
    # its reference calls (run_arm), so total concurrency is bounded by --workers here
    # times the per-arm reference count — fine for a handful of cloud providers.
    jobs = [(q["id"], arm_name, rep)
            for q in questions for arm_name in arm_names for rep in range(args.reps)]

    def _do(job):
        qid, arm_name, rep = job
        t0 = time.time()
        res = run_arm(config, ARMS[arm_name], qById[qid]["question"],
                      temperature=args.temperature)
        return job, res, time.time() - t0

    done = 0
    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_to_job = {ex.submit(_do, j): j for j in jobs}
        for fut in futures.as_completed(fut_to_job):
            qid, arm_name, rep = fut_to_job[fut]
            done += 1
            try:
                _job, res, latency = fut.result()
            except Exception as e:
                print(f"[{done}/{len(jobs)}] {qid} {arm_name} rep{rep}: ARM ERROR {e}",
                      flush=True)
                continue
            q = qById[qid]
            verdict, detail = auto_check(q, res["answer"])
            h = answer_hash(qid, res["answer"])
            rec = {"arm": arm_name, "rep": rep, "answer": res["answer"],
                   "total_tokens": res["total_tokens"], "latency": latency,
                   "n_refs_ok": res["n_refs_ok"], "ref_errors": res["ref_errors"],
                   "auto_verdict": verdict, "auto_detail": detail, "hash": h}
            answers.setdefault(qid, []).append(rec)
            tag = (f"AUTO:{verdict}" if verdict else "->judge")
            err = f" REF_ERR={len(res['ref_errors'])}" if res["ref_errors"] else ""
            print(f"[{done}/{len(jobs)}] {qid:22s} {arm_name:13s} rep{rep}: {tag:10s} "
                  f"tok={res['total_tokens']:5d} {latency:4.1f}s{err}", flush=True)
            if verdict is None:  # needs blind judging
                queue.append({"hash": h, "qid": qid, "category": q["category"],
                              "question": q["question"], "gold": q.get("gold"),
                              "answer": res["answer"]})

    # keep per-question answer lists in a stable (arm, rep) order for readable output
    for qid in answers:
        answers[qid].sort(key=lambda r: (r["arm"], r["rep"]))

    # de-dup the queue by hash (identical answers judged once)
    seen, dedup = set(), []
    for item in queue:
        if item["hash"] in seen:
            continue
        seen.add(item["hash"])
        dedup.append(item)

    with open(ANSWERS_PATH, "w") as f:
        json.dump({"qmeta": qmeta, "answers": answers,
                   "config": {"reps": args.reps, "temperature": args.temperature,
                              "arms": arm_names}}, f, indent=2)
    with open(QUEUE_PATH, "w") as f:
        json.dump(dedup, f, indent=2)
    print(f"wrote {ANSWERS_PATH}", flush=True)
    print(f"wrote {QUEUE_PATH}  ({len(dedup)} unique answers need blind judging)",
          flush=True)
    auto_n = sum(1 for recs in answers.values() for r in recs if r["auto_verdict"])
    print(f"auto-decided (free): {auto_n} answers", flush=True)
    print(f"\nPHASE gen wall-clock: {time.time() - t_start:.0f}s", flush=True)
    print("\nNEXT: have Opus judge eval/results/moa_judge_queue.json -> "
          "write eval/results/moa_judge_scores.json (keyed by hash), then run "
          "--phase report", flush=True)


# ── PHASE: report ─────────────────────────────────────────────────────────────

def phase_report(args):
    with open(ANSWERS_PATH) as f:
        blob = json.load(f)
    qmeta, answers = blob["qmeta"], blob["answers"]
    scores = {}
    if os.path.exists(SCORES_PATH):
        with open(SCORES_PATH) as f:
            scores = json.load(f)
    arm_names = blob["config"]["arms"]

    # build per-(arm,qid) list of overall scores
    AUTO = {"pass": 1.0, "fail": 0.0}
    records = {a: {} for a in arm_names}
    missing_judgments = []
    for qid, recs in answers.items():
        cat = qmeta[qid]["category"]
        for r in recs:
            arm = r["arm"]
            if r["auto_verdict"] is not None:
                ov = AUTO[r["auto_verdict"]]
            else:
                sc = scores.get(r["hash"])
                if not sc:
                    missing_judgments.append((qid, arm, r["hash"]))
                    continue
                ov = _overall_from_axes(sc, cat)
                if ov is None:
                    missing_judgments.append((qid, arm, r["hash"]))
                    continue
            records[arm].setdefault(qid, []).append(ov)

    if missing_judgments:
        print(f"WARNING: {len(missing_judgments)} answers have no judge score yet "
              f"(run the judge phase). Showing partial results.\n", flush=True)

    print("=" * 78, flush=True)
    print("PER-ARM SUMMARY (mean overall across questions; per-rep means)", flush=True)
    print("=" * 78, flush=True)
    arm_means, arm_q_spread, arm_tokens, arm_latency = {}, {}, {}, {}
    for arm in arm_names:
        per_q_means, per_q_spreads = [], []
        for qid, ovs in records[arm].items():
            per_q_means.append(_mean(ovs))
            per_q_spreads.append(_spread(ovs))
        toks = [r["total_tokens"] for recs in answers.values() for r in recs
                if r["arm"] == arm]
        lats = [r["latency"] for recs in answers.values() for r in recs
                if r["arm"] == arm]
        arm_means[arm] = _mean(per_q_means)
        arm_q_spread[arm] = _mean(per_q_spreads)
        arm_tokens[arm] = _mean(toks)
        arm_latency[arm] = _mean(lats)
        print(f"{arm:13s} overall={arm_means[arm]:.3f}  "
              f"avg_q_spread=±{arm_q_spread[arm]:.3f}  "
              f"avg_tokens={arm_tokens[arm]:6.0f}  "
              f"avg_latency={arm_latency[arm]:5.1f}s", flush=True)

    print("\n" + "=" * 78, flush=True)
    print("VERDICT (MoA wins only if lift > max per-question spread of the two arms)",
          flush=True)
    print("=" * 78, flush=True)
    baselines = {a: v for a, v in arm_means.items() if a.startswith("baseline_")}
    if not baselines:
        print("no baseline arm in this run — cannot compute verdict", flush=True)
        return
    best_base = max(baselines, key=baselines.get)
    print(f"best single-model baseline: {best_base} = {arm_means[best_base]:.3f}\n",
          flush=True)
    for arm in arm_names:
        if not arm.startswith("moa_"):
            continue
        lift = arm_means[arm] - arm_means[best_base]
        noise = max(arm_q_spread[arm], arm_q_spread[best_base])
        tok_mult = (arm_tokens[arm] / arm_tokens[best_base]
                    if arm_tokens[best_base] else float("nan"))
        if lift > noise and lift > 0:
            v = f"WIN  (+{lift:.3f} > noise ±{noise:.3f})"
        elif lift > 0:
            v = f"noise (+{lift:.3f} <= noise ±{noise:.3f})"
        else:
            v = f"LOSS ({lift:+.3f})"
        print(f"  {arm:13s} {v}  at {tok_mult:.1f}x tokens", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["gen", "report"], default="gen")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--arms", default="")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent (question,arm,rep) jobs in the gen phase")
    args = ap.parse_args()
    if args.phase == "gen":
        phase_gen(args)
    else:
        phase_report(args)


if __name__ == "__main__":
    main()
