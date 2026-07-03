#!/usr/bin/env python3
"""PRODUCTION MoA eval — does the shipped 🧬 Experten-Gremium (v9.271.0) beat
plain Smart (Cloud) on the same questions, through the REAL Brain API?

Unlike eval/moa_eval.py (standalone direct-HTTP, FIXED arms), this harness
exercises the full production path: POST /v1/chat with model="moa" — i.e. the
LLM classifier, the task_pools gate, the per-task contribution mode
(answer|plan), the auto-route aggregator pick, the GDPR reference gate, the
wire-only draft injection AND the aggregator's tool use. Baseline arm =
model="auto-cloud" (identical routing, no fan-out).

Reuses from moa_eval.py: the 15 questions, auto_check (programmatic gold),
answer_hash + the SHARED judge-score cache eval/results/moa_judge_scores.json
(same hash scheme -> scores are reused across harnesses), the overall-score
weighting and the noise-aware verdict rule (feedback_eval_single_run_noise:
>= 3 reps, win only if lift > per-question spread).

Each job: create a fresh session (turn-1 => classifier always runs), send the
question (+ a no-clarifying-questions suffix, same for BOTH arms, because
production models may otherwise block on AskUserQuestion), stream the SSE,
auto-answer any user_input_needed with "proceed", read the final assistant
message + metadata (auto_route.moa carries gate/mode/references ground truth),
then DELETE the session (keeps the sidebar + MemPalace clean).

Usage:
  python3 -u eval/moa_prod_eval.py --phase gen --reps 3 --workers 5
  #   -> judge eval/results/moa_prod_judge_queue.json (blind) into
  #      eval/results/moa_judge_scores.json, then:
  python3 -u eval/moa_prod_eval.py --phase report
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from concurrent import futures

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "eval"))
import moa_eval as base  # noqa: E402  (questions, auto_check, hashing, stats)

RESULTS = os.path.join(REPO_ROOT, "eval", "results")
ANSWERS_PATH = os.path.join(RESULTS, "moa_prod_answers.json")
QUEUE_PATH = os.path.join(RESULTS, "moa_prod_judge_queue.json")
SCORES_PATH = base.SCORES_PATH  # SHARED judge cache (same hash scheme)

BASE_URL = "http://127.0.0.1:8420"
ARMS = {"auto": "auto-cloud", "gremium": "moa"}

# Same suffix for BOTH arms — production models (grounding discipline) may
# otherwise block the turn on an AskUserQuestion, which an eval can't answer.
NO_ASK = ("\n\nAnswer directly and completely in one turn; do not ask "
          "clarifying questions.")


def _mint_token() -> str:
    import jwt as pyjwt
    cfg = json.load(open(os.path.join(REPO_ROOT, "config.json")))
    secret = cfg.get("auth", {}).get("jwt_secret", "")
    conn = sqlite3.connect(os.path.join(REPO_ROOT, "agents", "main", "auth.db"))
    row = conn.execute(
        "SELECT id, username, role FROM users WHERE role='admin' LIMIT 1").fetchone()
    conn.close()
    return pyjwt.encode({"user_id": row[0], "username": row[1], "role": row[2],
                         "exp": time.time() + 12 * 3600, "iat": time.time()},
                        secret, algorithm="HS256")


def _api(token, method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _run_turn(token, model, question, *, timeout=300):
    """One production chat turn. Returns dict with answer/meta/latency."""
    sid = _api(token, "POST", "/v1/sessions",
               {"model": model, "agent": "main"})["session_id"]
    t0 = time.time()
    body = json.dumps({"session_id": sid, "model": model,
                       "message": question + NO_ASK}).encode()
    req = urllib.request.Request(BASE_URL + "/v1/chat", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    done = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ev = None
            deadline = time.time() + timeout
            for raw in resp:
                if time.time() > deadline:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event: "):
                    ev = line[7:].strip()
                elif line.startswith("data: ") and ev == "user_input_needed":
                    # Unblock a clarifying question so the turn can finish.
                    try:
                        _api(token, "POST", "/v1/chat/answer",
                             {"session_id": sid,
                              "answer": "No preferences — proceed with your "
                                        "best judgment and answer fully."})
                    except Exception:
                        pass
                elif line.startswith("data: ") and ev == "done":
                    done = True
                    break
    except Exception as e:
        return {"sid": sid, "answer": "", "error": f"stream: {e}",
                "latency": time.time() - t0, "meta": {}}
    latency = time.time() - t0
    # Ground truth from the persisted assistant message (+ metadata).
    answer, meta = "", {}
    try:
        msgs = _api(token, "GET", f"/v1/sessions/{sid}/messages")["messages"]
        for m in reversed(msgs):
            if m.get("role") == "assistant":
                answer = m.get("content") or ""
                meta = m.get("metadata") or {}
                break
    except Exception as e:
        return {"sid": sid, "answer": "", "error": f"read: {e}",
                "latency": latency, "meta": {}}
    return {"sid": sid, "answer": answer, "error": "" if done else "no done event",
            "latency": latency, "meta": meta}


def _delete_session(token, sid):
    try:
        _api(token, "POST", "/v1/sessions/manage",
             {"action": "delete", "session_id": sid})
    except Exception:
        pass


def phase_gen(args):
    token = _mint_token()
    with open(os.path.join(REPO_ROOT, "eval", "moa_questions.json")) as f:
        questions = json.load(f)
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in want]
    arm_names = ([x.strip() for x in args.arms.split(",") if x.strip()]
                 if args.arms else list(ARMS.keys()))
    os.makedirs(RESULTS, exist_ok=True)
    jobs = [(q["id"], a, rep)
            for q in questions for a in arm_names for rep in range(args.reps)]
    print(f"PHASE gen (PRODUCTION) — {len(questions)} questions x {arm_names} x "
          f"{args.reps} reps = {len(jobs)} turns via {BASE_URL}", flush=True)
    qById = {q["id"]: q for q in questions}
    qmeta = {q["id"]: {"category": q["category"], "question": q["question"],
                       "gold": q.get("gold")} for q in questions}
    answers, queue = {}, []
    t_start = time.time()

    def _do(job):
        qid, arm, rep = job
        res = _run_turn(token, ARMS[arm], qById[qid]["question"],
                        timeout=args.timeout)
        _delete_session(token, res["sid"])
        return res

    done = 0
    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_to_job = {ex.submit(_do, j): j for j in jobs}
        for fut in futures.as_completed(fut_to_job):
            qid, arm, rep = fut_to_job[fut]
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                print(f"[{done}/{len(jobs)}] {qid} {arm} rep{rep}: JOB ERROR {e}",
                      flush=True)
                continue
            q = qById[qid]
            verdict, detail = base.auto_check(q, res["answer"])
            h = base.answer_hash(qid, res["answer"])
            md = res.get("meta") or {}
            ar = md.get("auto_route") or {}
            moa = ar.get("moa") or {}
            rec = {"arm": arm, "rep": rep, "answer": res["answer"],
                   "latency": round(res["latency"], 1),
                   "tokens_in": md.get("tokens_in", 0),
                   "tokens_out": md.get("tokens_out", 0),
                   "cost": md.get("cost"), "model": md.get("model", ""),
                   "task_types": (ar.get("analysis") or {}).get("task_types"),
                   "moa": {k: moa.get(k) for k in
                           ("gate_hit", "mode", "gated_out", "ok", "failed",
                            "models", "ms")} if moa else None,
                   "error": res.get("error") or "",
                   "auto_verdict": verdict, "auto_detail": detail, "hash": h}
            answers.setdefault(qid, []).append(rec)
            gate = ""
            if arm == "gremium":
                gate = (" GATED-OUT" if (not moa or moa.get("gated_out"))
                        else f" refs={moa.get('ok', '?')}/{len(moa.get('models') or [])}"
                             f" mode={moa.get('mode', '?')}")
            tag = (f"AUTO:{verdict}" if verdict else "->judge")
            err = f" ERR={res['error'][:40]}" if res.get("error") else ""
            print(f"[{done}/{len(jobs)}] {qid:22s} {arm:8s} rep{rep}: {tag:10s} "
                  f"{res['latency']:5.1f}s{gate}{err}", flush=True)
            if verdict is None and res["answer"].strip():
                queue.append({"hash": h, "qid": qid, "category": q["category"],
                              "question": q["question"], "gold": q.get("gold"),
                              "answer": res["answer"]})

    for qid in answers:
        answers[qid].sort(key=lambda r: (r["arm"], r["rep"]))
    # de-dup queue by hash AND drop hashes already in the shared score cache
    scores = {}
    if os.path.exists(SCORES_PATH):
        scores = json.load(open(SCORES_PATH))
    seen, dedup = set(scores.keys()), []
    for item in queue:
        if item["hash"] in seen:
            continue
        seen.add(item["hash"])
        dedup.append(item)
    with open(ANSWERS_PATH, "w") as f:
        json.dump({"qmeta": qmeta, "answers": answers,
                   "config": {"reps": args.reps, "arms": arm_names}}, f, indent=2)
    with open(QUEUE_PATH, "w") as f:
        json.dump(dedup, f, indent=2)
    print(f"wrote {ANSWERS_PATH}", flush=True)
    print(f"wrote {QUEUE_PATH} ({len(dedup)} unique answers need blind judging; "
          f"cache already covers the rest)", flush=True)
    print(f"PHASE gen wall-clock: {time.time() - t_start:.0f}s", flush=True)


def phase_report(args):
    blob = json.load(open(ANSWERS_PATH))
    qmeta, answers = blob["qmeta"], blob["answers"]
    arm_names = blob["config"]["arms"]
    scores = json.load(open(SCORES_PATH)) if os.path.exists(SCORES_PATH) else {}
    AUTO = {"pass": 1.0, "fail": 0.0}
    records = {a: {} for a in arm_names}
    per_cat = {a: {} for a in arm_names}
    missing = []
    for qid, recs in answers.items():
        cat = qmeta[qid]["category"]
        for r in recs:
            if r["auto_verdict"] is not None:
                ov = AUTO[r["auto_verdict"]]
            else:
                sc = scores.get(r["hash"])
                ov = base._overall_from_axes(sc, cat) if sc else None
                if ov is None:
                    missing.append((qid, r["arm"], r["hash"]))
                    continue
            records[r["arm"]].setdefault(qid, []).append(ov)
            per_cat[r["arm"]].setdefault(cat, []).append(ov)
    if missing:
        print(f"WARNING: {len(missing)} answers unjudged — partial results.\n",
              flush=True)
    print("=" * 78, flush=True)
    print("PRODUCTION ARMS (auto = Smart Cloud, gremium = 🧬 Experten-Gremium)",
          flush=True)
    print("=" * 78, flush=True)
    arm_means, arm_q_spread = {}, {}
    for arm in arm_names:
        per_q_means = [base._mean(v) for v in records[arm].values()]
        per_q_spreads = [base._spread(v) for v in records[arm].values()]
        lats = [r["latency"] for recs in answers.values() for r in recs
                if r["arm"] == arm]
        costs = [r["cost"] for recs in answers.values() for r in recs
                 if r["arm"] == arm and isinstance(r.get("cost"), (int, float))]
        arm_means[arm] = base._mean(per_q_means)
        arm_q_spread[arm] = base._mean(per_q_spreads)
        print(f"{arm:8s} overall={arm_means[arm]:.3f}  "
              f"avg_q_spread=±{arm_q_spread[arm]:.3f}  "
              f"avg_latency={base._mean(lats):5.1f}s  "
              f"avg_cost=${base._mean(costs):.4f}", flush=True)
    print("\nPer category:", flush=True)
    cats = sorted({qm["category"] for qm in qmeta.values()})
    for cat in cats:
        row = "  " + cat.ljust(22)
        for arm in arm_names:
            xs = per_cat[arm].get(cat, [])
            row += f" {arm}={base._mean(xs):.3f}(n={len(xs)})"
        print(row, flush=True)
    # Gate behavior of the gremium arm
    g_in, g_out, modes = 0, 0, {}
    for recs in answers.values():
        for r in recs:
            if r["arm"] != "gremium":
                continue
            moa = r.get("moa") or {}
            if not moa or moa.get("gated_out"):
                g_out += 1
            else:
                g_in += 1
                modes[moa.get("mode") or "?"] = modes.get(moa.get("mode") or "?", 0) + 1
    print(f"\nGremium gate: fan-out on {g_in}, gated-out {g_out} "
          f"(modes: {modes})", flush=True)
    if "auto" in arm_means and "gremium" in arm_means:
        lift = arm_means["gremium"] - arm_means["auto"]
        noise = max(arm_q_spread["auto"], arm_q_spread["gremium"])
        if lift > noise and lift > 0:
            v = f"WIN (+{lift:.3f} > noise ±{noise:.3f})"
        elif lift > 0:
            v = f"noise (+{lift:.3f} <= ±{noise:.3f})"
        else:
            v = f"LOSS ({lift:+.3f}, noise ±{noise:.3f})"
        print(f"\nVERDICT gremium vs auto: {v}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["gen", "report"], default="gen")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--arms", default="")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    if args.phase == "gen":
        phase_gen(args)
    else:
        phase_report(args)


if __name__ == "__main__":
    main()
