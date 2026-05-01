#!/usr/bin/env python3
"""Standalone judge that calls Mistral Medium 3.5 directly (no Brain, no Claude Code).

Reads an existing eval results dir and writes `judge.json` per question + a
`summary_mistral.csv` / `summary_mistral.md` alongside the existing summaries.
Does NOT touch the original `judge.json` files — writes `judge_mistral.json`
per question instead, so an Opus or Haiku judging pass on the same dir stays
untouched.

Usage:
  python3 eval/judge_mistral.py eval/results/<results_dir>
  python3 eval/judge_mistral.py eval/results/<dir> --only F1_geldwaesche,F2_kreditvergabe
  python3 eval/judge_mistral.py eval/results/<dir> --model mistral-vibe/mistral-medium-3.5
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_config() -> dict:
    with open(os.path.join(REPO_ROOT, "config.json")) as f:
        return json.load(f)


def _resolve_provider(model_id: str, config: dict) -> tuple[str, str, str]:
    """Returns (api_key, base_url, base_model_id) for the given model id.

    Mirrors enough of Brain's `resolve_provider_for_model` to work on
    provider-scoped ids like `mistral-vibe/mistral-medium-3.5`.
    """
    models = config.get("models", {})
    providers = config.get("providers", {})
    if model_id not in models:
        raise SystemExit(f"model {model_id!r} not found in config.json[models]")
    m = models[model_id]
    provider_name = m.get("provider")
    base_model = m.get("base_model_id") or model_id.rsplit("/", 1)[-1]
    if not provider_name or provider_name not in providers:
        raise SystemExit(f"provider {provider_name!r} for {model_id!r} not in config.json[providers]")
    p = providers[provider_name]
    return p["api_key"], p["base_url"].rstrip("/"), base_model


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_text_from_claude_json(blob: dict) -> str:
    """Same logic as eval/run.py — surface a marker on error rather than dredging up a UUID."""
    if blob.get("is_error") or blob.get("subtype", "").startswith("error"):
        st = blob.get("subtype", "error")
        return f"[CLAUDE_CODE_ERROR: subtype={st} terminal_reason={blob.get('terminal_reason','')} num_turns={blob.get('num_turns','?')}]"
    for key in ("result", "response", "output", "message"):
        v = blob.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            t = v.get("text") or v.get("content")
            if isinstance(t, str) and t.strip():
                return t
            if isinstance(t, list):
                parts = [b.get("text", "") for b in t if isinstance(b, dict)]
                joined = "\n".join(p for p in parts if p)
                if joined.strip():
                    return joined
    return "[CLAUDE_CODE_EMPTY: no result field present]"


def call_mistral(api_key: str, base_url: str, model: str, prompt: str,
                 timeout: float = 180.0, temperature: float = 0.0,
                 max_tokens: int = 1500) -> dict:
    """Single non-streaming chat completion. Returns parsed response."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise scoring judge. Output ONLY the requested JSON object — no prose, no markdown fences, no explanation outside the JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base_url + "/chat/completions", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {base_url}: {err_body[:500]}")


def parse_judge_json(text: str) -> dict:
    """Strip optional fences, locate first { ... } block, parse it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object found. head={text[:300]!r}")
    return json.loads(m.group(0))


def run_judge(question_obj: dict, gold_text: str, brain_text: str,
              rubric: str, api_key: str, base_url: str, model: str,
              timeout: float = 180.0) -> dict:
    expected_docs = ", ".join(question_obj.get("expected_docs", [])) or "(none — refusal expected)"
    prompt = (
        f"# Eval rubric\n\n{rubric}\n\n"
        f"---\n\n"
        f"# Question\n\n{question_obj['question']}\n\n"
        f"**Bucket:** {question_obj.get('bucket','')}\n"
        f"**Expected docs:** {expected_docs}\n"
        f"**Expected to refuse:** {bool(question_obj.get('expected_refuse', False))}\n\n"
        f"---\n\n"
        f"# Gold answer (Claude Code + Opus + vanilla MemPalace)\n\n{gold_text}\n\n"
        f"---\n\n"
        f"# Brain answer (Brain agent as deployed)\n\n{brain_text}\n\n"
        f"---\n\n"
        f"Score both answers per the rubric. Output the JSON object only — no prose, no markdown fences."
    )
    last_err = None
    for attempt in range(2):
        try:
            resp = call_mistral(api_key, base_url, model, prompt, timeout=timeout, max_tokens=2000)
            content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not content.strip():
                last_err = "empty content"
                continue
            return parse_judge_json(content)
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"judge failed after 2 attempts: {last_err}")


def _g(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part, "")
        if cur == "":
            return ""
    return cur


def _delta(judge: dict):
    g = _g(judge, "gold.total")
    b = _g(judge, "brain.total")
    try:
        return round(float(b) - float(g), 2)
    except (TypeError, ValueError):
        return ""


def _render_summary_md(rows: list[dict], judge_label: str) -> str:
    if not rows:
        return "_no rows_\n"
    out = [f"# Mistral-judged summary ({judge_label})\n",
           "| id | bucket | gold | brain | Δ | winner | summary |",
           "|----|--------|-----:|------:|--:|--------|---------|"]
    valid_g, valid_b = [], []
    wins = {"gold": 0, "brain": 0, "tie": 0, "?": 0}
    for r in rows:
        g, b, d = r["gold_total"], r["brain_total"], r["delta"]
        if isinstance(g, (int, float)): valid_g.append(g)
        if isinstance(b, (int, float)): valid_b.append(b)
        wins[r["winner"] or "?"] = wins.get(r["winner"] or "?", 0) + 1
        out.append(f"| {r['id']} | {r['bucket']} | {g} | {b} | {d} | {r['winner']} | {(r['judge_summary'] or '')[:90]} |")
    out.append("")
    if valid_g and valid_b:
        out.append(f"**Means** — gold: {sum(valid_g)/len(valid_g):.2f}, brain: {sum(valid_b)/len(valid_b):.2f}, "
                   f"Δ_brain−gold: {(sum(valid_b)/len(valid_b)) - (sum(valid_g)/len(valid_g)):+.2f}")
    out.append(f"**Wins** — gold: {wins.get('gold',0)}, brain: {wins.get('brain',0)}, tie: {wins.get('tie',0)}, errors: {wins.get('?',0)}")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir", help="path to a previous eval run dir")
    ap.add_argument("--only", help="comma-separated question ids to judge")
    ap.add_argument("--model", default="mistral-vibe/mistral-medium-3.5",
                    help="judge model id (provider-scoped, must exist in config.json)")
    ap.add_argument("--rubric", default=None,
                    help="rubric file (defaults to <results_dir>/rubric.md)")
    args = ap.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    if not os.path.isdir(results_dir):
        print(f"ERROR: not a directory: {results_dir}", file=sys.stderr)
        return 2

    cfg = _load_config()
    api_key, base_url, base_model = _resolve_provider(args.model, cfg)
    print(f"[judge-mistral] model={args.model} base_model={base_model} base_url={base_url}")

    rubric_path = args.rubric or os.path.join(results_dir, "rubric.md")
    if not os.path.exists(rubric_path):
        rubric_path = os.path.join(REPO_ROOT, "eval/rubric.md")
    rubric = _read_text(rubric_path)

    qpath = os.path.join(results_dir, "questions.json")
    if not os.path.exists(qpath):
        qpath = os.path.join(REPO_ROOT, "eval/questions.json")
    questions = _load_json(qpath)["questions"]

    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    judge_label = f"{args.model}"
    summary_rows = []
    for i, q in enumerate(questions, 1):
        qid = q["id"]
        qdir = os.path.join(results_dir, qid)
        if not os.path.isdir(qdir):
            print(f"  [{i}/{len(questions)}] {qid}: SKIP (no dir)")
            continue

        gold_path = os.path.join(qdir, "gold.json")
        brain_path = os.path.join(qdir, "brain.json")
        if not (os.path.exists(gold_path) and os.path.exists(brain_path)):
            print(f"  [{i}/{len(questions)}] {qid}: SKIP (missing gold or brain)")
            continue

        gold_blob = _load_json(gold_path)
        gold_text = extract_text_from_claude_json(gold_blob) if "error" not in gold_blob else f"[GOLD ERROR: {gold_blob['error']}]"
        brain_blob = _load_json(brain_path)
        brain_text = brain_blob.get("text", "") if "error" not in brain_blob else f"[BRAIN ERROR: {brain_blob['error']}]"

        out_path = os.path.join(qdir, "judge_mistral.json")
        t0 = time.time()
        try:
            judge = run_judge(q, gold_text, brain_text, rubric, api_key, base_url, base_model)
            judge["_elapsed_s"] = round(time.time() - t0, 2)
            judge["_model"] = args.model
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(judge, f, indent=2, ensure_ascii=False)
            print(f"  [{i}/{len(questions)}] {qid}: gold={_g(judge,'gold.total')} brain={_g(judge,'brain.total')} winner={_g(judge,'comparison.winner')} ({judge['_elapsed_s']}s)")
        except Exception as e:
            judge = {"error": str(e), "_elapsed_s": round(time.time() - t0, 2), "_model": args.model}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(judge, f, indent=2, ensure_ascii=False)
            print(f"  [{i}/{len(questions)}] {qid}: FAILED — {e}")

        summary_rows.append({
            "id": qid,
            "bucket": q.get("bucket", ""),
            "expected_refuse": q.get("expected_refuse", False),
            "gold_total": _g(judge, "gold.total"),
            "brain_total": _g(judge, "brain.total"),
            "delta": _delta(judge),
            "winner": _g(judge, "comparison.winner"),
            "gold_retrieval": _g(judge, "gold.retrieval"),
            "brain_retrieval": _g(judge, "brain.retrieval"),
            "gold_precision": _g(judge, "gold.precision"),
            "brain_precision": _g(judge, "brain.precision"),
            "gold_citation": _g(judge, "gold.citation"),
            "brain_citation": _g(judge, "brain.citation"),
            "gold_refusal": _g(judge, "gold.refusal"),
            "brain_refusal": _g(judge, "brain.refusal"),
            "gold_composition": _g(judge, "gold.composition"),
            "brain_composition": _g(judge, "brain.composition"),
            "judge_summary": _g(judge, "comparison.summary") or judge.get("error", ""),
        })

    csv_path = os.path.join(results_dir, "summary_mistral.csv")
    if summary_rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            w.writeheader()
            w.writerows(summary_rows)

    md_path = os.path.join(results_dir, "summary_mistral.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_summary_md(summary_rows, judge_label))

    print(f"\n[judge-mistral] done. summary: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
