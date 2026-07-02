#!/usr/bin/env python3
"""FastContext eval harness — run on the M4, inside a checkout of this repo.

For each gold query, invoke the official `fastcontext` CLI (which drives its own
Read/Glob/Grep loop against cwd) and score its <final_answer> citations against
the verified gold file:line. Reports per-item + aggregate File-hit, line-hit
(within tolerance), latency, and trajectory turn count.

Env (point at the served model):
  FC_BASE_URL  e.g. http://127.0.0.1:8016/v1
  FC_MODEL     e.g. fastcontext-rl-8bit
  FC_API_KEY   anything (local)
  FC_MAX_TURNS default 6
  FC_TEMPERATURE default 0.2

Usage:  cd ~/cctest-eval && python3 eval/fastcontext_eval.py [--gold eval/fastcontext_gold.json] [--limit N]
Output: eval/fastcontext_results.json  (+ stdout summary)
"""
import argparse, json, os, re, subprocess, sys, time

LINE_TOL = 8  # a line-range hit counts if it covers [gold-TOL, gold+TOL]

def parse_citations(text):
    """Extract (file, lo, hi) tuples from a <final_answer> block (or whole text)."""
    m = re.search(r"<final_answer>(.*?)</final_answer>", text, re.S)
    body = m.group(1) if m else text
    cites = []
    # path:NN-MM  or  path:NN
    for mm in re.finditer(r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?", body):
        f, lo, hi = mm.group(1), int(mm.group(2)), mm.group(3)
        hi = int(hi) if hi else lo
        cites.append((f.lstrip("./"), lo, hi))
    return cites

def score(cites, gold_file, gold_line):
    gf = gold_file.lstrip("./")
    file_hit = any(c[0].endswith(gf) or gf.endswith(c[0]) for c in cites)
    line_hit = any((c[0].endswith(gf) or gf.endswith(c[0]))
                   and (c[1] - LINE_TOL) <= gold_line <= (c[2] + LINE_TOL)
                   for c in cites)
    # rank: is the gold file the FIRST cited file?
    first_file_hit = bool(cites) and (cites[0][0].endswith(gf) or gf.endswith(cites[0][0]))
    return file_hit, line_hit, first_file_hit

def run_query(query, max_turns):
    traj = f".fastcontext/eval_{int(time.time()*1000)}.jsonl"
    t0 = time.time()
    # NOTE: deliberately NOT passing --citation. The upstream harness's
    # get_final_answer()/format_citations() crashes (TypeError) when the model's
    # final turn lacks a clean <final_answer> block — parse_citations returns a
    # dict on the no-block path that format_citations then iterates as if a list.
    # Without --citation the loop returns the model's raw final content, which we
    # parse ourselves (parse_citations below handles the no-block case).
    p = subprocess.run(
        [sys.executable, "-m", "fastcontext.cli", "--query", query,
         "--max-turns", str(max_turns), "--traj", traj],
        capture_output=True, text=True, timeout=900,
    )
    dt = time.time() - t0
    turns = None
    try:
        with open(traj) as fh:
            turns = sum(1 for _ in fh)
    except OSError:
        pass
    return p.stdout + "\n" + p.stderr, dt, turns

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="eval/fastcontext_gold.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="comma-separated ids to run (e.g. q02,q05)")
    ap.add_argument("--out", default="eval/fastcontext_results.json")
    args = ap.parse_args()

    max_turns = int(os.getenv("FC_MAX_TURNS", "6"))
    gold = json.load(open(args.gold))["items"]
    if args.only:
        keep = set(args.only.split(","))
        gold = [g for g in gold if g["id"] in keep]
    if args.limit:
        gold = gold[: args.limit]

    results, fh_n, lh_n, ffh_n = [], 0, 0, 0
    for i, item in enumerate(gold, 1):
        print(f"[{i}/{len(gold)}] {item['id']} ({item['difficulty']}) ...", flush=True)
        out, dt, turns = run_query(item["query"], max_turns)
        cites = parse_citations(out)
        file_hit, line_hit, first_hit = score(cites, item["file"], item["line"])
        fh_n += file_hit; lh_n += line_hit; ffh_n += first_hit
        results.append({
            "id": item["id"], "difficulty": item["difficulty"],
            "gold": f"{item['file']}:{item['line']}",
            "cites": [f"{c[0]}:{c[1]}-{c[2]}" for c in cites],
            "file_hit": file_hit, "line_hit": line_hit, "first_file_hit": first_hit,
            "latency_s": round(dt, 1), "turns": turns,
        })
        tag = "FILE+LINE" if line_hit else ("FILE" if file_hit else "MISS")
        print(f"    {tag}  {dt:.0f}s  turns={turns}  cites={results[-1]['cites'][:3]}", flush=True)

    n = len(gold)
    summary = {
        "n": n, "model": os.getenv("FC_MODEL", "?"),
        "file_hit_rate": round(fh_n / n, 3),
        "line_hit_rate": round(lh_n / n, 3),
        "first_file_hit_rate": round(ffh_n / n, 3),
        "median_latency_s": round(sorted(r["latency_s"] for r in results)[n // 2], 1),
        "line_tol": LINE_TOL, "max_turns": max_turns,
    }
    json.dump({"summary": summary, "results": results}, open(args.out, "w"), indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    # by difficulty
    for diff in ("exact", "concept", "misplaced"):
        sub = [r for r in results if r["difficulty"] == diff]
        if sub:
            print(f"  {diff:9s} n={len(sub)} file={sum(r['file_hit'] for r in sub)}/{len(sub)} "
                  f"line={sum(r['line_hit'] for r in sub)}/{len(sub)}")

if __name__ == "__main__":
    main()
