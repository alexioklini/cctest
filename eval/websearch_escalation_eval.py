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


def run_case(base, token, agent, project, model, question, reps):
    """Run one case `reps` times against the given project (project="" = a normal
    non-project chat, web tools live). Web availability is a property of the
    project: a project with disable_web_search=true has no web tools; "" or a
    web-allowed project has them. Returns a list of per-rep result dicts."""
    results = []
    for i in range(reps):
        sid = brain_create_session(base, token, agent, project, model)
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
                         "refusal case (Case 2). Empty → Case 2 skipped.")
    ap.add_argument("--mem-web-project", default="",
                    help="name of a project that HAS mined memory AND allows web, "
                         "for Case 3 (mem+web both present). Empty → Case 3 skipped.")
    ap.add_argument("--mem-hit-question", default="",
                    help="Case 3a: a question whose answer IS fully in --mem-web-project "
                         "memory → expect mem lookup, NO web call (mem suffices).")
    ap.add_argument("--mem-miss-question", default="",
                    help="Case 3b: a question that needs current/external info BEYOND "
                         "the mined memory → expect mem AND web.")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--escalation-threshold", type=float, default=0.66,
                    help="min fraction of reps that must escalate to web (Case 1/3b)")
    args = ap.parse_args()

    def p(*a):  # flushed print so background runs show live progress
        print(*a, flush=True)

    user = os.environ.get("BRAIN_USER")
    pw = os.environ.get("BRAIN_PASS")
    if not user or not pw:
        raise SystemExit("set BRAIN_USER and BRAIN_PASS in the environment")
    token = brain_login(args.base, user, pw)
    models = [m.strip() for m in args.model.split(",") if m.strip()]

    overall_ok = True
    for model in models:
        p(f"\n{'='*64}\nMODEL: {model}\n{'='*64}")

        # ---- Case 1: web available + memory empty → must escalate to web.
        p(f"\n[Case 1] web available, memory empty — expect mem→web escalation "
          f"({args.reps} reps)")
        r1 = run_case(args.base, token, args.agent, "", model, args.question, args.reps)
        esc = sum(1 for r in r1 if r.get("called_web"))
        rate = esc / max(1, len(r1))
        for r in r1:
            p(f"   rep{r['rep']}: mem={r.get('called_memory')} web={r.get('called_web')} "
              f"tools={r.get('tools_called')} | {r.get('answer_head','')[:80]}")
        ok1 = rate >= args.escalation_threshold
        overall_ok &= ok1
        p(f"   → web-escalation rate {esc}/{len(r1)} ({rate:.0%})  "
          f"{'PASS' if ok1 else 'FAIL'} (threshold {args.escalation_threshold:.0%})")

        # ---- Case 2: refusal — web DISABLED + memory empty → no fabrication.
        if args.web_locked_project:
            p(f"\n[Case 2] web LOCKED (project '{args.web_locked_project}'), memory "
              f"empty — expect a clean 'not found', NO fabrication ({args.reps} reps)")
            r2 = run_case(args.base, token, args.agent, args.web_locked_project, model,
                          args.question, args.reps)
            fabricated = sum(1 for r in r2 if r.get("looks_fabricated"))
            refused = sum(1 for r in r2 if r.get("not_found_signal"))
            for r in r2:
                p(f"   rep{r['rep']}: not_found={r.get('not_found_signal')} "
                  f"fabricated={r.get('looks_fabricated')} len={r.get('answer_len')} | "
                  f"{r.get('answer_head','')[:80]}")
            ok2 = fabricated == 0 and refused >= 1
            overall_ok &= ok2
            p(f"   → {refused}/{len(r2)} clean-refusals, {fabricated} fabrications  "
              f"{'PASS' if ok2 else 'FAIL'} (require 0 fabrications + ≥1 refusal)")
        else:
            p("\n[Case 2] SKIPPED — pass --web-locked-project <name>.")

        # ---- Case 3: web AND memory both present, memory HAS data.
        #   3a: answer fully in memory → expect mem lookup, NO web (mem suffices).
        #   3b: answer needs info beyond memory → expect mem AND web.
        if args.mem_web_project and args.mem_hit_question and args.mem_miss_question:
            p(f"\n[Case 3a] mem+web both present, answer IS in memory "
              f"('{args.mem_web_project}') — expect mem lookup, NO web call ({args.reps} reps)")
            r3a = run_case(args.base, token, args.agent, args.mem_web_project, model,
                           args.mem_hit_question, args.reps)
            mem_only = sum(1 for r in r3a if r.get("called_memory") and not r.get("called_web"))
            for r in r3a:
                p(f"   rep{r['rep']}: mem={r.get('called_memory')} web={r.get('called_web')} "
                  f"| {r.get('answer_head','')[:80]}")
            # PASS = the model used memory and did NOT make a needless web call,
            # in the MAJORITY of reps (model variance tolerated).
            ok3a = mem_only / max(1, len(r3a)) >= 0.66
            overall_ok &= ok3a
            p(f"   → {mem_only}/{len(r3a)} mem-only (no needless web)  "
              f"{'PASS' if ok3a else 'FAIL'} (require ≥66% mem-only)")

            p(f"\n[Case 3b] mem+web both present, answer NEEDS info beyond memory "
              f"('{args.mem_web_project}') — expect mem AND web ({args.reps} reps)")
            r3b = run_case(args.base, token, args.agent, args.mem_web_project, model,
                           args.mem_miss_question, args.reps)
            both = sum(1 for r in r3b if r.get("called_web"))  # escalated to web
            for r in r3b:
                p(f"   rep{r['rep']}: mem={r.get('called_memory')} web={r.get('called_web')} "
                  f"| {r.get('answer_head','')[:80]}")
            ok3b = both / max(1, len(r3b)) >= args.escalation_threshold
            overall_ok &= ok3b
            p(f"   → {both}/{len(r3b)} escalated to web  "
              f"{'PASS' if ok3b else 'FAIL'} (threshold {args.escalation_threshold:.0%})")
        else:
            p("\n[Case 3] SKIPPED — pass --mem-web-project + --mem-hit-question + "
              "--mem-miss-question to run the mem+web-both-present case.")

    p(f"\n{'='*64}\nOVERALL: {'PASS' if overall_ok else 'FAIL'}\n{'='*64}")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
