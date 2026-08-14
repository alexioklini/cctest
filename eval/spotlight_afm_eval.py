#!/usr/bin/env python3
"""Spotlight+AFM-Arm des KG-Real-Policies-Evals (15 Fragen, Opus-Gold-Reuse).

Pipeline pro Frage: ssh M4 → spotlight-qa (Core-Spotlight-Discovery + AFM-3B-
Antwort aus Companion-Markdowns) → Judge (run.py-Rubrik, mistral-medium via
run_judge_mistral) gegen die GESPEICHERTEN Gold-Antworten eines früheren Laufs.

Usage:
  python3 -u eval/spotlight_afm_eval.py [--gold-dir eval/results/<dir>] [--label x] [--only R1,...]
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M4 = "alexander@192.168.1.214"
DEFAULT_GOLD = "eval/results/20260705T065248_disc-none_moa-delegate-rep3"

# run.py als Modul laden (run_judge_mistral + extract_text_from_claude_json)
_spec = importlib.util.spec_from_file_location("evalrun", os.path.join(REPO, "eval", "run.py"))
evalrun = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evalrun)


def spotlight_answer(question: str, timeout: float = 240, retrieve_only: bool = False, ctx_budget: str = "24000", cli_model: str = "") -> dict:
    env = f"RETRIEVE_ONLY=1 CTX_BUDGET={ctx_budget} " if retrieve_only else ""
    if cli_model:
        env += f"MODEL={_shq(cli_model)} CTX_BUDGET={ctx_budget} "
    proc = subprocess.run(
        ["ssh", M4, "cd spotlight-eval && " + env + ".build/release/spotlight-qa " + _shq(question)],
        capture_output=True, text=True, timeout=timeout)
    out = proc.stdout
    i = out.find("{")
    if i < 0:
        return {"answer": "", "error": f"no json; stderr={proc.stderr[:200]}"}
    try:
        return json.loads(out[i:])
    except Exception as e:
        return {"answer": "", "error": f"parse: {e}; head={out[i:i+200]!r}"}


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


class ServeClient:
    """Persistente spotlight-qa --serve Instanz auf dem M4: Modell bleibt
    RESIDENT über alle Fragen (statt 6-GB-Reload pro Frage). Protokoll:
    Frage als Zeile rein, eine '@@RESULT@@{json}'-Zeile raus."""

    def __init__(self, cli_model: str, ctx_budget: str, retrieve_only: bool = False):
        env = ""
        if retrieve_only:
            env += "RETRIEVE_ONLY=1 "
        if cli_model:
            env += f"MODEL={_shq(cli_model)} "
        env += f"CTX_BUDGET={ctx_budget} "
        self.p = subprocess.Popen(
            ["ssh", M4, f"cd spotlight-eval && {env}.build/release/spotlight-qa --serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def ask(self, question: str) -> dict:
        self.p.stdin.write(question.replace("\n", " ") + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("serve-Prozess beendet")
            if line.startswith("@@RESULT@@"):
                return json.loads(line[len("@@RESULT@@"):])

    def close(self):
        try:
            self.p.stdin.write("@@QUIT@@\n")
            self.p.stdin.flush()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def router_answer(model: str, question: str, context: str, base: str = "") -> str:
    """Antwortstufe über den llm-router (z.B. gemma-4-12B) oder — mit
    explizitem base — direkt über fm-agent (CoreAI-Zoo-Modelle). Identischer
    Prompt wie die AFM-Stufe-2 in spotlight_qa.swift."""
    import urllib.request
    if base:
        m = {"base_model_id": model}
        p = {"base_url": base, "api_key": "none"}
    else:
        cfg = json.load(open(os.path.join(REPO, "config.json")))
        m = cfg["models"][model]
        p = cfg["providers"][m["provider"]]
    body = {
        "model": m.get("base_model_id") or model,
        "messages": [
            {"role": "system", "content": (
                "Beantworte die Frage präzise auf Deutsch, NUR auf Basis der "
                "Dokumentauszüge. Belege JEDE Aussage in der Form "
                "[Quelle: <Dateiname> — \"wörtliches Zitat aus dem Auszug\"]. "
                "Erfinde keine Werte: was nicht im Auszug steht, ist \"nicht "
                "spezifiziert\". Steht die Antwort gar nicht in den Auszügen, "
                "sage das ausdrücklich.")},
            {"role": "user", "content": f"Frage: {question}\n\nDokumentauszüge:\n{context}"},
        ],
        "temperature": 0.1, "max_tokens": 1200,
    }
    req = urllib.request.Request(p["base_url"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + p["api_key"])
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.loads(r.read().decode())
    return (d.get("choices") or [{}])[0].get("message", {}).get("content", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-dir", default=DEFAULT_GOLD)
    ap.add_argument("--label", default="spotlight-afm")
    ap.add_argument("--only", default="")
    ap.add_argument("--answer-model", default="",
                    help="statt AFM-3B: Router-Modell (z.B. gemma-4-12B...) beantwortet aus dem Spotlight-Kontext")
    ap.add_argument("--answer-base", default="",
                    help="expliziter OpenAI-Base-URL für die Antwortstufe (z.B. http://192.168.1.214:8007/v1 für CoreAI via fm-agent)")
    ap.add_argument("--ctx-budget", default="24000")
    ap.add_argument("--serve", action="store_true",
                    help="EINE persistente spotlight-qa-Instanz für alle Fragen (Modell bleibt geladen)")
    ap.add_argument("--cli-model", default="",
                    help="MODEL-Spec für spotlight-qa selbst (afm | coreai:<bundle-pfad> | mlx:<hf-id>) — volle Pipeline im Binary")
    args = ap.parse_args()

    cfg = json.load(open(os.path.join(REPO, "eval", "config.json")))
    rubric = open(os.path.join(REPO, cfg["rubric_file"])).read()
    judge_model = cfg["judge"]["model"]
    judge_timeout = float(cfg["judge"].get("timeout_seconds", 180))

    questions = json.load(open(os.path.join(REPO, cfg["questions_file"])))["questions"]
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only:
        questions = [q for q in questions if q["id"] in only]

    serve = ServeClient(args.cli_model, args.ctx_budget,
                        retrieve_only=bool(args.answer_model)) if args.serve else None

    ts = time.strftime("%Y%m%dT%H%M%S")
    outdir = os.path.join(REPO, "eval", "results", f"{ts}_{args.label}")
    os.makedirs(outdir, exist_ok=True)

    rows = []
    for q in questions:
        qid = q["id"]
        gold_path = os.path.join(REPO, args.gold_dir, qid, "gold.json")
        gold_text = evalrun.extract_text_from_claude_json(json.load(open(gold_path)))

        t0 = time.time()
        if args.answer_model:
            sp = (serve.ask(q["question"]) if serve else
                  spotlight_answer(q["question"], retrieve_only=True, ctx_budget=args.ctx_budget))
            ctx = sp.get("context", "")
            if ctx:
                sp_text = router_answer(args.answer_model, q["question"], ctx, base=args.answer_base)
            else:
                sp_text = ("Die Dokumente enthalten keine Antwort auf diese "
                           "Frage (keine relevanten Treffer im Regelwerk).")
            sp["answer"] = sp_text
            sp.pop("context", None)
        else:
            sp = (serve.ask(q["question"]) if serve else
                  spotlight_answer(q["question"], timeout=600,
                                   ctx_budget=args.ctx_budget,
                                   cli_model=args.cli_model))
        sp_text = sp.get("answer", "") or ""
        dur = time.time() - t0
        print(f"[{qid}] docs={sp.get('docs')} dur={dur:.1f}s answer={sp_text[:90]!r}", flush=True)

        try:
            judge = evalrun.run_judge_mistral(q, gold_text, sp_text, rubric,
                                              judge_model, judge_timeout)
        except Exception as e:
            judge = {"error": str(e)}
        b = judge.get("brain", {})
        print(f"    judge: total={b.get('total')} retr={b.get('retrieval')} "
              f"prec={b.get('precision')} cit={b.get('citation')} "
              f"ref={b.get('refusal')} comp={b.get('composition')}", flush=True)

        qd = os.path.join(outdir, qid)
        os.makedirs(qd, exist_ok=True)
        json.dump(q, open(os.path.join(qd, "question.json"), "w"), ensure_ascii=False, indent=1)
        json.dump(sp, open(os.path.join(qd, "spotlight.json"), "w"), ensure_ascii=False, indent=1)
        json.dump(judge, open(os.path.join(qd, "judge.json"), "w"), ensure_ascii=False, indent=1)
        rows.append({"id": qid, "bucket": q.get("bucket"),
                     "total": b.get("total"), "judge_ok": "error" not in judge,
                     "dur_s": round(dur, 1)})

    totals = [r["total"] for r in rows if isinstance(r.get("total"), (int, float))]
    mean = sum(totals) / len(totals) if totals else 0.0
    summary = {"label": args.label, "gold_dir": args.gold_dir, "n": len(rows),
               "mean_total": round(mean, 3), "rows": rows}
    json.dump(summary, open(os.path.join(outdir, "summary.json"), "w"),
              ensure_ascii=False, indent=1)
    if serve:
        serve.close()
    print(f"\nMEAN total = {mean:.3f} über {len(totals)}/{len(rows)} Fragen → {outdir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
