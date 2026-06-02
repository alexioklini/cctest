#!/usr/bin/env python3
"""web_fetch optimization eval — does a content-reducing optimization lose answer-critical content?

For each case we fetch the SAME url twice:
  * OPT  — the optimized code path (abstract survey / academic-PDF rewrite /
           matched-region trim / the tool's conversion choice).
  * GOLD — optimizations OFF, web_fetch returns the COMPLETE page content.
Then a model answers the case question from EACH fetch, and Mistral judges
whether the OPT answer matches the GOLD answer. A "gold" winner / content_loss
flag means the optimization dropped content the answer needed.

This calls `engine.tools.misc_tools.tool_web_fetch` IN-PROCESS (it only needs
`import brain` lazily — no running server), so the gold/opt split is the real
production fetch code, not a re-implementation. The GOLD path is produced by
narrowly disabling exactly one optimization per mode (monkeypatch), so the only
difference between the two answers is that optimization.

Usage:
  python3 eval/web_fetch_eval.py
  python3 eval/web_fetch_eval.py --only ABS1_arxiv_attention,ACA1_arxiv_full_vs_wrapper
  python3 eval/web_fetch_eval.py --answer-model mistral-vibe/mistral-medium-3.5
  python3 eval/web_fetch_eval.py --judge-model mistral-vibe/mistral-medium-3.5
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# ── config / provider resolution (mirrors eval/judge_mistral.py) ──────────────


def _load_config() -> dict:
    with open(os.path.join(REPO_ROOT, "config.json")) as f:
        return json.load(f)


def _resolve_provider(model_id: str, config: dict):
    """(api_key, base_url, base_model_id) for a provider-scoped model id."""
    models = config.get("models", {})
    providers = config.get("providers", {})
    if model_id not in models:
        raise SystemExit(f"model {model_id!r} not in config.json[models]")
    m = models[model_id]
    provider_name = m.get("provider")
    base_model = m.get("base_model_id") or model_id.rsplit("/", 1)[-1]
    if not provider_name or provider_name not in providers:
        raise SystemExit(f"provider {provider_name!r} for {model_id!r} not in config.json[providers]")
    p = providers[provider_name]
    return p["api_key"], p["base_url"].rstrip("/"), base_model


def call_mistral(api_key, base_url, model, system, user,
                 timeout=180.0, temperature=0.0, max_tokens=1500) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
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
            blob = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {base_url}: {err[:400]}")
    return (blob.get("choices") or [{}])[0].get("message", {}).get("content", "")


# ── fetch helpers ─────────────────────────────────────────────────────────────


def _unwrap(tool_result: str) -> dict:
    """tool_web_fetch returns an _ok/_err envelope JSON string. Pull the payload."""
    try:
        obj = json.loads(tool_result)
    except (TypeError, ValueError):
        return {"error": "non-JSON tool result", "content": tool_result or ""}
    # _ok wraps as {"ok": true, "result": {...}} or similar; be liberal.
    payload = obj.get("result", obj) if isinstance(obj, dict) else {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {"content": payload}
    return payload if isinstance(payload, dict) else {"content": str(payload)}


def fetch_pair(case: dict):
    """Return (opt_payload, gold_payload) for a case — the same url fetched on the
    optimized path and with that mode's optimization disabled."""
    from engine.tools import misc_tools
    from engine import tool_exec

    mode = case["mode"]
    url = case["url"]

    def _fetch(args):
        # force_fresh on every call so the in-process WebCache can't serve a
        # cross-mode hit (opt and gold differ only by the patched optimization).
        a = {"url": url, "force_fresh": True}
        a.update(args)
        return _unwrap(misc_tools.tool_web_fetch(a))

    if mode == "abstract":
        opt = _fetch({"mode": "abstract"})
        gold = _fetch({"mode": "full"})
        return opt, gold

    if mode == "academic":
        # OPT: the academic rewrite fires (landing url → full-text PDF).
        opt = _fetch({"mode": "full"})
        # GOLD here = optimization OFF: bypass the rewrite so the tool fetches the
        # raw landing/abstract HTML wrapper (what the user pasted, no PDF inlining).
        orig = misc_tools._academic_pdf_url
        misc_tools._academic_pdf_url = lambda u: None
        try:
            gold = _fetch({"mode": "full"})
        finally:
            misc_tools._academic_pdf_url = orig
        return opt, gold

    if mode == "brain_code":
        # OPT: seed a recorded brain_code region for this file so web_fetch trims
        # the returned content to the matched region (the production trigger).
        repo_path = misc_tools._github_raw_repo_path(url)
        anchor = case.get("brain_code_anchor", "")
        # The "chunk" we pretend the brain_code query matched: just the anchor
        # line — _trim_to_brain_code_regions relocates it by fingerprint.
        tool_exec._record_brain_code_region(repo_path, anchor)
        try:
            opt = _fetch({"mode": "full"})
        finally:
            # clear so GOLD sees no recorded region → full file returned.
            with tool_exec._brain_code_regions_lock:
                tool_exec._brain_code_regions.clear()
        gold = _fetch({"mode": "full"})
        return opt, gold

    if mode == "conversion":
        # OPT: the tool's auto conversion choice (raw / markitdown / crawl4ai).
        opt = _fetch({"mode": "full"})
        # GOLD = most complete extraction: force the headless render when the page
        # has one, else the same complete content. We can't force crawl4ai through
        # the tool API, so GOLD reuses the auto path — for static pages opt==gold
        # (the test then confirms conversion lost nothing). Documented limitation.
        gold = _fetch({"mode": "full"})
        return opt, gold

    raise SystemExit(f"unknown mode {mode!r} in case {case.get('id')}")


# ── answer + judge ────────────────────────────────────────────────────────────

ANSWER_SYSTEM = (
    "You answer the user's question using ONLY the supplied web page content. "
    "If the content does not contain the answer, say so explicitly — do not "
    "guess or use prior knowledge. Be concise and specific."
)


def answer_from_content(api_key, base_url, model, question, content) -> str:
    content = content or "[empty content]"
    # Generous cap so the GOLD (complete-content) reference is genuinely complete
    # — a too-tight cap would truncate the reference and mask whether the
    # optimization lost anything. Mistral medium handles this comfortably.
    if len(content) > 60000:
        content = content[:60000] + "\n... (truncated for answer model)"
    user = (
        f"# Web page content\n\n{content}\n\n---\n\n"
        f"# Question\n\n{question}\n\n"
        f"Answer using only the content above."
    )
    return call_mistral(api_key, base_url, model, ANSWER_SYSTEM, user,
                        temperature=0.0, max_tokens=700)


JUDGE_SYSTEM = (
    "You are a precise scoring judge. Output ONLY the requested JSON object — "
    "no prose, no markdown fences, no explanation outside the JSON."
)


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON found. head={text[:200]!r}")
    return json.loads(m.group(0))


def judge(api_key, base_url, model, rubric, mode, question, gold_answer, opt_answer) -> dict:
    user = (
        f"# Rubric\n\n{rubric}\n\n---\n\n"
        f"# Mode\n\n`{mode}` — apply the matching branch of the rubric.\n\n---\n\n"
        f"# Question\n\n{question}\n\n---\n\n"
        f"# GOLD answer (from COMPLETE content)\n\n{gold_answer}\n\n---\n\n"
        f"# OPT answer (from OPTIMIZED fetch)\n\n{opt_answer}\n\n---\n\n"
        f"Output the JSON object only."
    )
    last = None
    for _ in range(2):
        try:
            out = call_mistral(api_key, base_url, model, JUDGE_SYSTEM, user,
                               temperature=0.0, max_tokens=900)
            if out.strip():
                return parse_json(out)
            last = "empty content"
        except Exception as e:
            last = str(e)
    raise RuntimeError(f"judge failed: {last}")


# ── runner ────────────────────────────────────────────────────────────────────


def _g(d, dotted, default=""):
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p, default)
    return cur


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--answer-model", default="mistral-medium-3.5")
    ap.add_argument("--judge-model", default="mistral-medium-3.5")
    ap.add_argument("--cases", default=os.path.join(REPO_ROOT, "eval/web_fetch_cases.json"))
    ap.add_argument("--rubric", default=os.path.join(REPO_ROOT, "eval/web_fetch_rubric.md"))
    ap.add_argument("--out", default=None, help="results dir (default eval/results/webfetch_<ts>)")
    args = ap.parse_args()

    cfg = _load_config()
    a_key, a_base, a_model = _resolve_provider(args.answer_model, cfg)
    j_key, j_base, j_model = _resolve_provider(args.judge_model, cfg)
    print(f"[web_fetch_eval] answer={args.answer_model} judge={args.judge_model}")

    with open(args.cases) as f:
        cases = json.load(f)["cases"]
    with open(args.rubric) as f:
        rubric = f.read()
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    out_dir = args.out or os.path.join(REPO_ROOT, "eval/results", f"webfetch_{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    for i, case in enumerate(cases, 1):
        cid = case["id"]
        cdir = os.path.join(out_dir, cid)
        os.makedirs(cdir, exist_ok=True)
        print(f"  [{i}/{len(cases)}] {cid} ({case['mode']}) …", flush=True)
        try:
            opt_p, gold_p = fetch_pair(case)
        except Exception as e:
            print(f"      FETCH FAILED: {e}")
            rows.append({"id": cid, "mode": case["mode"], "error": f"fetch: {e}"})
            continue

        opt_content = opt_p.get("content", "")
        gold_content = gold_p.get("content", "")
        meta = {
            "opt_len": len(opt_content), "gold_len": len(gold_content),
            "opt_fetch_method": opt_p.get("fetch_method", ""),
            "gold_fetch_method": gold_p.get("fetch_method", ""),
            "opt_url": opt_p.get("url", ""), "gold_url": gold_p.get("url", ""),
        }
        with open(os.path.join(cdir, "fetch.json"), "w") as f:
            json.dump({"case": case, "meta": meta,
                       "opt_content": opt_content, "gold_content": gold_content},
                      f, indent=2, ensure_ascii=False)

        try:
            gold_ans = answer_from_content(a_key, a_base, a_model, case["question"], gold_content)
            opt_ans = answer_from_content(a_key, a_base, a_model, case["question"], opt_content)
        except Exception as e:
            print(f"      ANSWER FAILED: {e}")
            rows.append({"id": cid, "mode": case["mode"], "error": f"answer: {e}", **meta})
            continue

        try:
            jr = judge(j_key, j_base, j_model, rubric, case["mode"], case["question"], gold_ans, opt_ans)
        except Exception as e:
            print(f"      JUDGE FAILED: {e}")
            jr = {"error": str(e)}

        with open(os.path.join(cdir, "result.json"), "w") as f:
            json.dump({"case": case, "meta": meta, "gold_answer": gold_ans,
                       "opt_answer": opt_ans, "judge": jr}, f, indent=2, ensure_ascii=False)

        content_loss = _g(jr, "comparison.content_loss")
        # Saved fraction = how much smaller the optimized fetch was. For abstract
        # mode the headline is "full fetch avoided": the survey was SUFFICIENT
        # (no content_loss) AND much smaller, so the caller could have skipped the
        # full fetch — the optimization paid off. For the lossless modes a saving
        # only counts if it ALSO didn't drop content.
        saved = round(1 - (meta["opt_len"] / meta["gold_len"]), 2) if meta["gold_len"] else 0.0
        paid_off = (content_loss is not True) and saved >= 0.5
        row = {
            "id": cid, "mode": case["mode"],
            "opt_len": meta["opt_len"], "gold_len": meta["gold_len"],
            "saved_frac": saved, "paid_off": paid_off,
            "opt_method": meta["opt_fetch_method"],
            "gold_total": _g(jr, "gold.total"), "opt_total": _g(jr, "opt.total"),
            "winner": _g(jr, "comparison.winner"),
            "content_loss": content_loss,
            "summary": _g(jr, "comparison.summary") or jr.get("error", ""),
        }
        rows.append(row)
        print(f"      gold={row['gold_total']} opt={row['opt_total']} "
              f"winner={row['winner']} content_loss={content_loss} "
              f"saved={int(saved*100)}% paid_off={paid_off} "
              f"[{meta['opt_fetch_method']} {meta['opt_len']}c vs {meta['gold_len']}c]")

    # summaries
    csv_path = os.path.join(out_dir, "summary.csv")
    if rows:
        keys = sorted({k for r in rows for k in r})
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)

    scored = [r for r in rows if "winner" in r]
    losses = [r for r in scored if r.get("content_loss") is True]
    # abstract: paid off = survey sufficient AND much smaller (full fetch avoided).
    paid = [r for r in scored if r.get("paid_off") is True]
    md = ["# web_fetch optimization eval\n",
          f"answer={args.answer_model} · judge={args.judge_model}\n",
          "abstract = triage-sufficiency (`paid_off` = survey sufficient AND ≥50% smaller "
          "⇒ full fetch avoided). academic/brain_code/conversion = completeness (`loss` = dropped content).\n",
          "| id | mode | opt_len | gold_len | saved | method | gold | opt | winner | loss? | paid? | summary |",
          "|----|------|--------:|---------:|------:|--------|-----:|----:|--------|-------|-------|---------|"]
    for r in rows:
        if "error" in r and "winner" not in r:
            md.append(f"| {r['id']} | {r['mode']} | | | | | | | | | | ERROR: {r['error']} |")
            continue
        md.append(f"| {r['id']} | {r['mode']} | {r.get('opt_len','')} | {r.get('gold_len','')} | "
                  f"{int(r.get('saved_frac',0)*100)}% | {r.get('opt_method','')} | "
                  f"{r.get('gold_total','')} | {r.get('opt_total','')} | {r.get('winner','')} | "
                  f"{'**YES**' if r.get('content_loss') else 'no'} | "
                  f"{'**yes**' if r.get('paid_off') else '·'} | "
                  f"{(str(r.get('summary',''))[:80])} |")
    md.append("")
    md.append(f"**Content-loss cases: {len(losses)}/{len(scored)}** "
              f"— {', '.join(r['id'] for r in losses) or 'none'}")
    md.append(f"**Paid-off (full fetch avoided): {len(paid)}/{len(scored)}** "
              f"— {', '.join(r['id'] for r in paid) or 'none'}")
    md_path = os.path.join(out_dir, "summary.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md) + "\n")

    print(f"\n[web_fetch_eval] done → {md_path}")
    print("\n".join(md[-2:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
