#!/usr/bin/env python3
"""Refine eval — does the two-tier "Engineer" rewrite beat today's "Polish" cleaner?

For each draft prompt (eval/refine_cases.json) we produce TWO refined outputs:
  * OLD  — the current /v1/refine prompt (the conservative grammar/clarity cleaner).
  * NEW  — the proposed "Engineer" tier (intent-extract + restructure + grounding:
           model hint, tool names, project, agentic stop-conditions for scheduled).
Then a judge model scores each on clarity / intent_preserved / actionability /
token_economy (eval/refine_rubric.md), with an intent-drift HARD GATE.

The OLD/NEW prompt builders below MIRROR the real handler (handlers/admin_artifacts.py).
OLD is copied verbatim from the live handler so the baseline is the true product.
NEW is the spec the handler will implement in build step 2 — keep them in sync.

Like eval/web_fetch_eval.py this is standalone: reads config.json, resolves the
provider directly, calls /chat/completions. No running server needed.

Usage:
  python3 eval/refine_eval.py                       # all cases, both tiers, judged
  python3 eval/refine_eval.py --only CHAT1_auth_bug,SCHED2_cleanup_files
  python3 eval/refine_eval.py --baseline-only       # run OLD only (Polish baseline)
  python3 eval/refine_eval.py --refine-model mistral-vibe/mistral-medium-3.5
  python3 eval/refine_eval.py --judge-model  mistral-vibe/mistral-medium-3.5
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


# ── config / provider resolution (mirrors eval/web_fetch_eval.py) ─────────────

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
        raise SystemExit(f"provider {provider_name!r} for {model_id!r} not in config.json[providers]")
    p = providers[provider_name]
    return p["api_key"], p["base_url"].rstrip("/"), base_model


def _default_background_model(config: dict) -> str:
    """Mirror brain._background_model_default() WITHOUT importing brain (no
    server_config in a bare process — see feedback_never_probe_server_config).
    Falls back to config.json default_model, the same value the helper returns
    when no explicit background model is configured."""
    return (config.get("default_model") or "").strip()


def call_llm(api_key, base_url, model, system, user,
             timeout=180.0, temperature=0.0, max_tokens=2000) -> str:
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        blob = json.loads(resp.read().decode("utf-8"))
    return (blob.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _approx_tokens(s: str) -> int:
    """Rough token count (chars/4) — good enough for relative bloat comparison."""
    return max(1, len(s or "") // 4)


# ── OLD builder — VERBATIM from handlers/admin_artifacts.py (the Polish baseline)

def build_old(purpose: str, draft: str, context: dict) -> tuple[str, str]:
    """Returns (system_or_instructions, request_line) for the current handler.
    purpose 'scheduled_task' uses the chat_prompt path (today it has no special
    handling — that's exactly the gap NEW fills)."""
    if purpose == "soul":
        instructions = (
            "You are a TEXT POLISHER for an AI agent's soul.md (its system prompt). "
            "The soul defines the agent's identity, role, and behavioural rules — it is "
            "written in second person ('You are …', 'Your job is …'). Your job is to "
            "lightly polish what the user wrote without changing meaning.\n"
            "CRITICAL RULES:\n"
            "- Output ONLY the polished soul, nothing else.\n"
            "- Keep second-person voice ('You are …', 'Your job …'). Do NOT switch to first or third person.\n"
            "- Do NOT change the agent's name, role, or capabilities.\n"
            "- Do NOT add new behaviours, tools, or rules. Do NOT remove existing rules.\n"
            "- Do NOT answer or respond — just clean up what's there.\n"
            "- Fix grammar, spelling, punctuation, awkward phrasing, redundancy.\n"
            "- Preserve Markdown structure and code blocks exactly.\n"
            "- Keep the existing tone. If the input is already clean, return it unchanged."
        )
        return instructions, (
            "Polish this soul.md (output ONLY the polished version, preserve all "
            "Markdown structure and code blocks):\n\n" + draft)
    # chat_prompt (and scheduled_task today)
    instructions = (
        "You are a PROMPT REWRITER for an AI chat system. "
        "The user will give you a draft prompt/message they want to send to an AI assistant. "
        "Your job is to rewrite it into a better, clearer version of the SAME request. "
        "CRITICAL RULES:\n"
        "- Output ONLY the rewritten prompt, nothing else\n"
        "- Do NOT answer the question or fulfill the request — REWRITE it\n"
        "- Do NOT add explanations, analysis, alternatives, or commentary\n"
        "- Do NOT use markdown headings, bullet points, or formatting\n"
        "- The output replaces the user's input in a chat box — it must be a clean prompt\n"
        "- Fix grammar, spelling, punctuation\n"
        "- Make the request clearer and more specific using the context provided\n"
        "- Keep the same intent and language\n"
        "Example: Input: 'whats weather vienna' → Output: 'What is the weather like in Vienna today?'"
    )
    return instructions, f"Rewrite this prompt (output ONLY the rewritten version):\n\n{draft}"


# ── NEW builder — the "Engineer" tier spec (REFINE_ENHANCEMENT_DESIGN.md §4.2)
#    Keep in sync with the handler when build step 2 lands.

def _model_hint(model: str, config: dict) -> str:
    """Light, non-drifting hint derived from OUR config (mirror of the planned
    handler helper _refine_model_hint). Reasoning-native → no CoT; local → flat."""
    m = (config.get("models") or {}).get(model, {})
    inf = m.get("inference", {}) or {}
    name = (model or "").lower()
    reasoning = bool(inf.get("thinking_level")) or any(
        t in name for t in ("r1", "o3", "o4-mini", "qwen3", "reason", "think"))
    is_local = (m.get("provider", "") or "").lower() in ("omlx", "local", "cliproxyapi") \
        or bool(m.get("local"))
    if reasoning:
        return ("Target model reasons internally — do NOT add 'think step by step' "
                "or other reasoning scaffolding; state the goal and the output cleanly.")
    if is_local:
        return ("Target is a local/open-weight model — keep the prompt flat and "
                "explicit; avoid deep nesting.")
    return ""


def build_new(purpose: str, draft: str, context: dict, config: dict) -> tuple[str, str]:
    model = context.get("model", "")
    tools = context.get("tools", []) or []
    project = context.get("project", "") or ""
    hint = _model_hint(model, config)
    tool_line = ("Available tools: " + ", ".join(tools) +
                 " — reference them by name when the task needs one.\n") if tools else ""
    proj_line = (f"Active project: {project} — respect its conventions.\n") if project else ""
    ground = ""
    if hint or tool_line or proj_line:
        ground = "\nGROUNDING:\n" + (hint + "\n" if hint else "") + tool_line + proj_line

    if purpose == "soul":
        instructions = (
            "You are an EDITOR for an AI agent's soul.md (its system prompt, second "
            "person: 'You are ...', 'Your job is ...'). Improve it structurally without "
            "changing who the agent is.\n"
            "CRITICAL RULES:\n"
            "- Output ONLY the improved soul. No commentary.\n"
            "- Keep second-person voice. Keep the agent's name, role, and listed tools.\n"
            "- You MAY: tighten wording, remove redundancy, group related rules, surface a "
            "missing stop-condition/guardrail that the existing rules clearly imply.\n"
            "- You MUST NOT: invent new capabilities, tools, or behaviours the user didn't "
            "imply; remove an existing rule; change the tone; pad with ceremony.\n"
            "- Do NOT add Markdown you weren't given: no new bold/**emphasis**, no converting "
            "bullets to numbered lists, no extra nesting, and NEVER wrap the whole soul in a "
            "```code fence```. Match the input's existing formatting exactly.\n"
            "- Preserve all Markdown structure and code/inline `code` exactly.\n"
            "- DEFAULT TO RETURNING IT UNCHANGED. Only edit if there is a real grammar error, "
            "true redundancy, or a clearly-implied missing guardrail. If the soul already "
            "reads cleanly, return it byte-for-byte. Restructuring a good soul is a failure."
        )
        return instructions, (
            "Improve this soul.md (output ONLY the improved version):\n\n" + draft)

    # chat_prompt + scheduled_task share the engineer core. MUST stay byte-aligned
    # with handlers/admin_artifacts.py (the handler ports this text).
    # DESIGN NOTE v2: the v1 "restraint-by-default" tuning over-corrected — on a
    # contextless chat draft Engineer barely differed from Polish (just trimmed),
    # because rules like "stay at the user's level of detail" + "return essentially
    # unchanged" suppressed the value. v2 makes Engineer ASSERTIVELY add structure/
    # format/success-criteria (the value), while keeping ONE hard limit: never
    # fabricate concrete FACTS (filenames/URLs/numbers). Adding structure ≠
    # inventing facts.
    instructions = (
        "You are a PROMPT ENGINEER for an AI assistant. The user gives you a "
        "rough draft of what they want the assistant to do. Turn it into a "
        "noticeably STRONGER, more effective prompt that gets the right "
        "result on the first try. A good rewrite is clearly more capable "
        "than the draft — not a near-copy with the typos fixed.\n"
        "DO add (this is the value — apply whatever the task needs):\n"
        "- A precise task verb (replace 'fix/make/handle/do' with the exact "
        "operation).\n"
        "- The expected OUTPUT shape when implied (format, structure, length, "
        "language) — e.g. 'as a bulleted list', 'a single function', 'in 3 "
        "sentences'.\n"
        "- An explicit success criterion when the task has one ('Done when: "
        "...').\n"
        "- A role/expert framing when the task is specialized.\n"
        "- Structure (steps, sections) when the request is multi-part.\n"
        "THE ONE HARD LIMIT — do NOT INVENT FALSE FACTS the draft didn't give: "
        "no specific filenames, paths, URLs, numbers, API fields, library "
        "names, or pixel sizes the user never mentioned. Adding structure, "
        "format, and explicitness is REQUIRED; fabricating concrete details "
        "is FORBIDDEN. (Generic placeholders like '[the relevant file]' are "
        "fine; a made-up 'index.html' is not.)\n"
        "OTHER RULES:\n"
        "- PROPORTION: the rewrite should be at most ~2× the draft's length "
        "unless the draft is genuinely vague and needs real structure. A "
        "one-line, already-clear request should come back as a tightened one- "
        "or two-line prompt — never a multi-section spec. If you're adding "
        "more than the task needs, cut it.\n"
        "- Output ONLY the rewritten prompt. No commentary, no 'here is'.\n"
        "- Preserve the user's actual intent and language. Do NOT answer the "
        "request yourself.\n"
        "- If two unrelated tasks are mixed, keep the primary one and note the "
        "split in ONE trailing line '(Second task: ...)'.\n"
        "- CALIBRATE to the draft. If it is already strong — it already "
        "names a clear task AND its scope (and, for a recurring task, a stop "
        "condition) — then it does NOT need your scaffolding: do only light "
        "tightening and do NOT bolt on a role, a multi-section format, a data "
        "flow, or 'Done when' that it didn't ask for. Adding ceremony to an "
        "already-complete prompt is a FAILURE. Save the heavy structuring for "
        "drafts that are actually rough or vague.\n"
        "- If the draft is so under-specified that even adding structure would "
        "require GUESSING the actual goal (e.g. 'fix the bug' with no hint of "
        "which bug), do NOT invent it — instead return a short prompt that "
        "asks the user for the missing piece(s). One focused question beats a "
        "confident wrong guess.\n"
        "- DO NOT OVER-STRICTIFY a casual factual lookup. If the draft is a "
        "casual everyday question whose answer is a quick web lookup (weather, "
        "exchange/stock price, sports score, opening hours, 'what is X'), keep "
        "it casual: fix spelling/grammar and stop. Do NOT add words that demand "
        "precision or an official source ('präzise', 'genau', 'exakt', "
        "'verbindlich', 'offizielle Quelle', 'precise', 'exact', 'authoritative/"
        "official source', 'to N decimal places', 'real-time'), and do NOT impose "
        "a rigid output spec. Those raise the assistant's evidentiary bar so it "
        "REFUSES ordinary web results instead of just answering — the opposite "
        "of helpful. 'wie wird das wetter morgen in wien' → 'Wie wird das Wetter "
        "morgen in Wien?', NOT 'Gib eine präzise Wettervorhersage … aus "
        "offizieller Quelle'.\n"
        "- For simple requests output plain prose. For genuinely complex "
        "multi-part requests you MAY use <context>/<task>/<constraints> XML "
        "sections. No commentary outside the prompt."
    )
    if purpose == "scheduled_task":
        instructions += (
            "\nThis prompt runs UNATTENDED on a schedule. Additionally, but ONLY if the draft "
            "does not already cover them (do NOT restate what's already there):\n"
            "- If no stop/completion condition is stated, add a brief one.\n"
            "- If the task performs a destructive action (delete/overwrite/send/transfer) and "
            "has NO safeguard, add: 'Stop and report instead of acting if uncertain.'\n"
            "- If it relies on info that may be missing, add: 'Report instead of guessing if "
            "information is missing.'\n"
            "Add nothing else. A well-scoped scheduled draft comes back essentially unchanged."
        )
    instructions += ground
    return instructions, f"Rewrite this draft (output ONLY the rewritten prompt):\n\n{draft}"


# ── judging ───────────────────────────────────────────────────────────────────

JUDGE_SYS = (
    "You are a strict evaluator of prompt-refinement quality. You score how well a "
    "refined prompt improves a user's rough draft WITHOUT inventing scope. Follow the "
    "rubric exactly and return ONLY strict JSON."
)


def build_judge_user(rubric: str, purpose: str, draft: str, intent: str,
                     refined: str, mode: str = "") -> str:
    return (
        rubric + "\n\n=== SAMPLE ===\n"
        f"purpose: {purpose}\n"
        f"mode: {mode or 'normal'}\n"
        f"original draft:\n{draft}\n\n"
        f"stated intent (what a good refine should/shouldn't do):\n{intent}\n\n"
        f"refined output to score:\n{refined}\n\n"
        "Return ONLY the JSON object specified in the rubric."
    )


def parse_judge(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"clarity": 0, "intent_preserved": 0, "actionability": 0,
                "token_economy": 0, "intent_drift": True, "note": "unparseable judge output"}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"clarity": 0, "intent_preserved": 0, "actionability": 0,
                "token_economy": 0, "intent_drift": True, "note": "bad json"}
    for k in ("clarity", "intent_preserved", "actionability", "token_economy"):
        try:
            d[k] = float(d.get(k, 0))
        except (TypeError, ValueError):
            d[k] = 0.0
    d["intent_drift"] = bool(d.get("intent_drift")) or d["intent_preserved"] < 0.7
    return d


def overall(d: dict) -> float:
    if d.get("intent_drift"):
        return 0.0
    return round((d["clarity"] + d["intent_preserved"] + d["actionability"] + d["token_economy"]) / 4, 3)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--baseline-only", action="store_true", help="run OLD (polish) only")
    ap.add_argument("--refine-model", default="", help="model that does the refining (default: config default_model)")
    ap.add_argument("--judge-model", default="", help="judge model (default: config default_model)")
    ap.add_argument("--out", default="", help="results dir (default eval/results/refine_<ts>)")
    args = ap.parse_args()

    config = _load_config()
    refine_model = args.refine_model or _default_background_model(config)
    judge_model = args.judge_model or _default_background_model(config)
    if not refine_model:
        raise SystemExit("no refine model (config.default_model empty) — pass --refine-model")

    rk_api, rk_base, rk_id = _resolve_provider(refine_model, config)
    jk_api, jk_base, jk_id = _resolve_provider(judge_model, config)

    with open(os.path.join(REPO_ROOT, "eval", "refine_cases.json")) as f:
        cases = json.load(f)["cases"]
    with open(os.path.join(REPO_ROOT, "eval", "refine_rubric.md")) as f:
        rubric = f.read()

    only = {c.strip() for c in args.only.split(",") if c.strip()}
    if only:
        cases = [c for c in cases if c["id"] in only]

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = args.out or os.path.join(REPO_ROOT, "eval", "results", f"refine_{ts}")
    os.makedirs(out_dir, exist_ok=True)

    tiers = ["old"] if args.baseline_only else ["old", "new"]
    rows = []
    print(f"refine_model={refine_model}  judge_model={judge_model}  cases={len(cases)}  tiers={tiers}\n")

    for c in cases:
        cid, purpose, draft = c["id"], c["purpose"], c["draft"]
        ctx, intent = c.get("context", {}), c.get("intent", "")
        mode = c.get("mode", "")
        rec = {"id": cid, "purpose": purpose}
        for tier in tiers:
            if tier == "old":
                instr, req = build_old(purpose, draft, ctx)
            else:
                instr, req = build_new(purpose, draft, ctx, config)
            try:
                refined = call_llm(rk_api, rk_base, rk_id, instr, req).strip()
            except Exception as e:
                refined = f"[REFINE ERROR: {e}]"
            jd = parse_judge(call_llm(
                jk_api, jk_base, jk_id, JUDGE_SYS,
                build_judge_user(rubric, purpose, draft, intent, refined, mode)))
            o = overall(jd)
            rec[tier] = {"refined": refined, "tokens": _approx_tokens(refined),
                         "scores": jd, "overall": o}
            with open(os.path.join(out_dir, f"{cid}.{tier}.json"), "w") as f:
                json.dump(rec[tier], f, indent=2, ensure_ascii=False)
            print(f"  {cid:28s} {tier:4s} overall={o:.2f} "
                  f"clr={jd['clarity']:.2f} int={jd['intent_preserved']:.2f} "
                  f"act={jd['actionability']:.2f} tok={jd['token_economy']:.2f} "
                  f"{'DRIFT' if jd['intent_drift'] else ''}")
        rows.append(rec)

    # ── summary + pass-bar ────────────────────────────────────────────────────
    def mean(key, tier):
        vals = [r[tier]["scores"][key] for r in rows if tier in r]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    summary = {"refine_model": refine_model, "judge_model": judge_model, "n": len(rows)}
    for tier in tiers:
        summary[tier] = {k: mean(k, tier) for k in
                         ("clarity", "intent_preserved", "actionability", "token_economy")}
        summary[tier]["overall"] = round(
            sum(r[tier]["overall"] for r in rows if tier in r) / max(1, len(rows)), 3)

    if "new" in tiers:
        # DESIGN: Engineer is an ASSERTIVE sharpener by decision (it out-engineers
        # Polish on rough/contextless drafts — the whole point). On an ALREADY-PERFECT
        # draft it may add some structure; that's an accepted mild cost, NOT the
        # failure mode. The failure mode we guard against is wholesale fabrication —
        # which historically ballooned tokens 3–5×. So the bloat gate is set at 3×
        # (egregious), not 1.5× (which would punish the intended assertiveness).
        clean = {"CHAT4_already_clean", "SCHED3_already_scoped", "SOUL2_already_tight"}
        # KNOWN ACCEPTED WEAK SPOT: CHAT4 (a terse-but-already-complete one-liner).
        # mistral-medium reliably over-structures it (~3–4×) no matter how the
        # restraint is phrased — four tuning rounds confirmed. Decision (user):
        # ship the assertive Engineer anyway — it's opt-in, Polish is the default,
        # and the aggregate win on rough/contextless drafts is large. We exempt
        # this single case from the bloat gate rather than hide it; any OTHER clean
        # case bloating >3× is still a real failure to investigate.
        accepted_bloat = {"CHAT4_already_clean"}
        regressions = [r["id"] for r in rows
                       if not r["old"]["scores"]["intent_drift"] and r["new"]["scores"]["intent_drift"]]
        bloat = [r["id"] for r in rows if r["id"] in clean
                 and r["id"] not in accepted_bloat
                 and r["new"]["tokens"] > 3.0 * max(1, r["old"]["tokens"])]
        # OVER-STRICTNESS GATE (2026-06-06 regression): casual_lookup drafts are
        # casual factual web-lookups. Engineer must NOT inject precision/officialness
        # that makes the downstream agent refuse ordinary web results (the "präzise
        # Wettervorhersage aus offizieller Quelle" → refusal bug). This is an ABSOLUTE
        # failure of the NEW tier — unlike intent_drift_regressions it does NOT require
        # OLD to have passed (Polish may also over-strictify, but Engineer is the tier
        # that did it in production and the one we're guarding). Any casual_lookup case
        # that NEW marks drift=true is a hard fail.
        casual_ids = {c["id"] for c in cases if c.get("mode") == "casual_lookup"}
        over_strict = [r["id"] for r in rows
                       if r["id"] in casual_ids and r["new"]["scores"]["intent_drift"]]
        # EPS tolerance: clarity/actionability are background-judged and wobble
        # ±0.01–0.02 run-to-run. Require Engineer to not REGRESS beyond that noise
        # floor (not strict ≥, which a 0.008 coin-flip would fail). The real signal
        # is the actionability GAIN + zero drift + zero un-accepted bloat.
        EPS = 0.02
        passed = (
            summary["new"]["clarity"] >= summary["old"]["clarity"] - EPS
            and summary["new"]["actionability"] >= summary["old"]["actionability"] - EPS
            and not regressions and not bloat and not over_strict)
        summary["pass_bar"] = {
            "passed": passed, "intent_drift_regressions": regressions,
            "clean_case_bloat": bloat,
            "over_strict_casual": over_strict,
            "clarity_delta": round(summary["new"]["clarity"] - summary["old"]["clarity"], 3),
            "actionability_delta": round(summary["new"]["actionability"] - summary["old"]["actionability"], 3)}

    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=2, ensure_ascii=False)

    with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "purpose", "tier", "overall", "clarity",
                    "intent_preserved", "actionability", "token_economy", "drift", "tokens"])
        for r in rows:
            for tier in tiers:
                s = r[tier]["scores"]
                w.writerow([r["id"], r["purpose"], tier, r[tier]["overall"],
                            s["clarity"], s["intent_preserved"], s["actionability"],
                            s["token_economy"], s["intent_drift"], r[tier]["tokens"]])

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nresults → {out_dir}")
    if "pass_bar" in summary and not summary["pass_bar"]["passed"]:
        print("\n⚠️  PASS BAR NOT MET — tune the NEW (engineer) prompt before shipping.")
        sys.exit(2)


if __name__ == "__main__":
    main()
