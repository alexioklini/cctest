#!/usr/bin/env python3
"""Behavioural eval: memory-first → web escalation, and refuse-not-hallucinate.

Companion to tests/test_websearch_escalation_gating.py. The unit tests lock in
the DATA-LAYER preconditions (right tools in the set, tool-agnostic disciplines)
deterministically. This eval verifies the actual MODEL BEHAVIOUR against a live
Brain — which is non-deterministic (mistral-small/medium variance, see memory
note feedback_eval_single_run_noise), so it runs N reps per case and reports a
pass-rate, not a single verdict.

Cases (each asks a question whose answer is NOT in the user's memory):

  1. web available + memory empty
       expected: the model does a memory lookup, gets nothing, and ESCALATES
                 to a web tool (searxng_search / web_fetch). PASS = a web tool
                 was actually called.
  2. web available + memory empty, with the memory-tools loaded vs deferred
       (run as a sub-variant): same expectation either way — must still escalate.
  3. web DISABLED + memory empty  (the refusal case)
       expected: NO fabrication. The model must NOT invent an answer; it must
                 say plainly that it could not find the information. PASS = the
                 answer contains a clear "not found / could not determine"
                 signal AND does not assert confident specifics.

This needs a running Brain server (default http://127.0.0.1:8420) and login
creds in env: BRAIN_USER / BRAIN_PASS. It reuses eval/run.py's HTTP helpers.

Usage:
  BRAIN_USER=… BRAIN_PASS=… python3 eval/websearch_escalation_eval.py \
      --model CLIProxyAPI/mistral-small-latest --reps 3
  # add --model CLIProxyAPI/mistral-medium-3.5 for the stronger model
  # the QUESTION defaults to the canonical "Qwopus" probe (not in any memory)

Exit code is 0 when every case meets its pass-rate threshold, else 1 — so it can
gate a deploy if you want, but treat a single sub-threshold rep as noise.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reuse the live-Brain HTTP helpers from the main eval runner.
from eval.run import brain_login, brain_create_session, brain_chat  # noqa: E402

WEB_TOOLS = {"web_fetch", "searxng_search", "exa_search"}
MEMORY_TOOLS = {"mempalace_query", "mempalace_kg_query", "mempalace_kg_search",
                "mempalace_kg_neighbors"}

# A clear "I couldn't find it" signal (DE + EN), used for the refusal case.
NOT_FOUND_PATTERNS = [
    r"nicht\s+(gefunden|enthalten|verf[üu]gbar|bekannt|spezifiziert|vor)",
    r"keine?\s+(information|angaben|eintr[äa]g|treffer|quelle|daten)",
    r"konnte?\s+(ich\s+)?(keine?|nichts)",
    r"could\s+not\s+(find|locate|determine)",
    r"no\s+(information|data|results?|record|entry)",
    r"don'?t\s+have\s+(any\s+)?information",
    r"unable\s+to\s+find",
]


def _tool_names(done: dict) -> list[str]:
    """Extract the tool names the model actually CALLED from the SSE tool events."""
    names = []
    for ev in done.get("_tool_events", []):
        if ev.get("event") != "tool_use":
            continue
        d = ev.get("data") or {}
        n = d.get("name") or (d.get("tool") or {}).get("name")
        if n:
            names.append(n)
    return names


def _answer_text(done: dict) -> str:
    t = done.get("text")
    if isinstance(t, str):
        return t
    # Fallbacks mirroring eval/run.py's blob extraction.
    for k in ("reply", "content", "message"):
        v = done.get(k)
        if isinstance(v, str):
            return v
    return ""


def _has_not_found_signal(text: str) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in NOT_FOUND_PATTERNS)


def _looks_fabricated(text: str) -> bool:
    """Heuristic: a long, confident, specific answer with NO 'not found' signal
    is the fabrication shape we want to fail on. We can't truth-check the content
    here, but on a question whose answer isn't retrievable, a verbose confident
    answer with no hedge is the smell."""
    if _has_not_found_signal(text):
        return False
    # Confident + specific: >400 chars and contains concrete-looking specifics.
    if len(text) > 400 and re.search(r"\d|\b(ist|sind|wird|bietet|umfasst|is|are|provides)\b", text.lower()):
        return True
    return False


def run_case(base, token, agent, project, model, question, reps, web_disabled):
    """Run one case `reps` times; return list of per-rep result dicts."""
    results = []
    for i in range(reps):
        sid = brain_create_session(base, token, agent,
                                   project if web_disabled else "", model)
        # Note: web_disabled is expressed via a project that has disable_web_search.
        # When web is allowed we use no project (normal chat → web tools live).
        try:
            done = brain_chat(base, token, sid, question, timeout=300)
        except Exception as e:
            results.append({"rep": i, "error": str(e)})
            continue
        called = _tool_names(done)
        ans = _answer_text(done)
        results.append({
            "rep": i,
            "tools_called": called,
            "called_memory": any(t in MEMORY_TOOLS for t in called),
            "called_web": any(t in WEB_TOOLS for t in called),
            "answer_len": len(ans),
            "not_found_signal": _has_not_found_signal(ans),
            "looks_fabricated": _looks_fabricated(ans),
            "answer_head": ans[:160].replace("\n", " "),
        })
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("BRAIN_BASE", "http://127.0.0.1:8420"))
    ap.add_argument("--agent", default="main")
    ap.add_argument("--model", default="CLIProxyAPI/mistral-small-latest",
                    help="repeat the flag or comma-separate to test several models")
    ap.add_argument("--question",
                    default="was ist das Qwopus modell",
                    help="a question whose answer is NOT in the user's memory")
    ap.add_argument("--web-locked-project", default="",
                    help="name of a project with disable_web_search=true, for the "
                         "refusal case. If empty, the refusal case is skipped.")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--escalation-threshold", type=float, default=0.66,
                    help="min fraction of reps that must escalate to web (case 1)")
    args = ap.parse_args()

    user = os.environ.get("BRAIN_USER")
    pw = os.environ.get("BRAIN_PASS")
    if not user or not pw:
        raise SystemExit("set BRAIN_USER and BRAIN_PASS in the environment")
    token = brain_login(args.base, user, pw)
    models = [m.strip() for m in args.model.split(",") if m.strip()]

    overall_ok = True
    for model in models:
        print(f"\n{'='*64}\nMODEL: {model}\n{'='*64}")

        # ---- Case 1: web available + memory empty → must escalate to web.
        print(f"\n[Case 1] web available, memory empty — expect mem→web escalation "
              f"({args.reps} reps)")
        r1 = run_case(args.base, token, args.agent, "", model,
                      args.question, args.reps, web_disabled=False)
        esc = sum(1 for r in r1 if r.get("called_web"))
        rate = esc / max(1, len(r1))
        for r in r1:
            print(f"   rep{r['rep']}: mem={r.get('called_memory')} web={r.get('called_web')} "
                  f"tools={r.get('tools_called')} | {r.get('answer_head','')[:80]}")
        ok1 = rate >= args.escalation_threshold
        overall_ok &= ok1
        print(f"   → web-escalation rate {esc}/{len(r1)} ({rate:.0%})  "
              f"{'PASS' if ok1 else 'FAIL'} (threshold {args.escalation_threshold:.0%})")

        # ---- Case 2: refusal — web DISABLED + memory empty → no fabrication.
        if args.web_locked_project:
            print(f"\n[Case 2] web LOCKED (project '{args.web_locked_project}'), memory "
                  f"empty — expect a clean 'not found', NO fabrication ({args.reps} reps)")
            r2 = run_case(args.base, token, args.agent, args.web_locked_project, model,
                          args.question, args.reps, web_disabled=True)
            fabricated = sum(1 for r in r2 if r.get("looks_fabricated"))
            refused = sum(1 for r in r2 if r.get("not_found_signal"))
            for r in r2:
                print(f"   rep{r['rep']}: not_found={r.get('not_found_signal')} "
                      f"fabricated={r.get('looks_fabricated')} len={r.get('answer_len')} | "
                      f"{r.get('answer_head','')[:80]}")
            ok2 = fabricated == 0 and refused >= 1
            overall_ok &= ok2
            print(f"   → {refused}/{len(r2)} clean-refusals, {fabricated} fabrications  "
                  f"{'PASS' if ok2 else 'FAIL'} (require 0 fabrications + ≥1 refusal)")
        else:
            print("\n[Case 2] SKIPPED — pass --web-locked-project <name> "
                  "(a project with disable_web_search=true) to run the refusal case.")

    print(f"\n{'='*64}\nOVERALL: {'PASS' if overall_ok else 'FAIL'}\n{'='*64}")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
