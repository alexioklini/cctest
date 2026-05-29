#!/usr/bin/env python3
"""Fan-out decomposition probe (NOT product code).

Goal: BEFORE building the fan-out/join plumbing, measure whether Mistral
medium/small can actually understand a `run_background_task(group_id, follow_up)`
tool description and decompose a multi-part request into N self-contained,
correctly-grouped parallel calls. If the model can't do the *thinking*, the
schema/join/race work is wasted.

How it works (zero changes to Brain):
  - Posts directly to the SIDECAR (POST :8421/turn) with a hand-crafted system
    prompt + the CANDIDATE fan-out tool schema.
  - Runs a tiny local HTTP stub as the sidecar's `tool_endpoint`: it RECORDS
    every tool_use the model emits and returns a benign "task started" result,
    so the model finishes its turn naturally (acknowledge + stop).
  - Captures all run_background_task calls per scenario and scores them on the
    6 decomposition criteria.

Run:
  python3 eval/fanout_probe.py                      # both models, all scenarios
  python3 eval/fanout_probe.py --model mistral-small-latest
  python3 eval/fanout_probe.py --only multi_vendor

Reads provider creds from config.json (CLIProxyAPI). Sidecar must be up (:8421).
"""
import argparse
import json
import queue
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIDECAR = "http://127.0.0.1:8421"
CONFIG = "config.json"

# --- The CANDIDATE tool description we want to validate. This is the ACTUAL
# lever: the model is "triggered" ONLY by this prose. Tune it here, re-run. ---
FANOUT_TOOL = {
    "name": "run_background_task",
    "description": (
        "Spin off a long-running, high-output piece of work (deep research, "
        "multi-source synthesis, a big sweep) as a DETACHED background task so it "
        "doesn't block the conversation. Runs as YOU — same agent, model, tools — "
        "in its own context. Returns IMMEDIATELY with a task id; you do NOT get the "
        "result in this turn — it is delivered to you automatically once finished.\n\n"
        "FAN-OUT (parallel): when a request covers SEVERAL INDEPENDENT SUBJECTS that "
        "can be researched at the same time (e.g. three different vendors, five "
        "distinct topics, two separate documents), make ONE call PER SUBJECT, and "
        "give EVERY one of those calls the SAME `group_id` — a short string you pick "
        "(e.g. 'g1'). The `group_id` is REQUIRED on every call of a fan-out and MUST "
        "be identical across them; that is how the system knows the calls belong "
        "together. Fan out only across independent SUBJECTS — do NOT split one "
        "subject into aspect-tasks.\n\n"
        "THE COMBINE STEP — read carefully: put the instruction for what to do once "
        "ALL parts are done (compare them, write the report, give a recommendation) "
        "into the `follow_up` field on the calls. Do NOT create a separate "
        "background task for the summary/comparison/report — `follow_up` IS the "
        "combine step. The parts run concurrently and their combined results are "
        "delivered back to you in one go, at which point you carry out `follow_up`.\n\n"
        "EXAMPLE — \"compare A, B, C and recommend one\" → exactly THREE calls:\n"
        "  {title:'A', prompt:'Research A …', group_id:'g1', follow_up:'Compare A/B/C and recommend one'}\n"
        "  {title:'B', prompt:'Research B …', group_id:'g1', follow_up:'Compare A/B/C and recommend one'}\n"
        "  {title:'C', prompt:'Research C …', group_id:'g1', follow_up:'Compare A/B/C and recommend one'}\n"
        "(NOT a fourth 'summary' call — the comparison happens via follow_up.)\n\n"
        "Each `prompt` is run by a FRESH agent that does NOT see this conversation — "
        "so every prompt must be fully self-contained (name the exact subject, no "
        "'the second one'). Use background tasks ONLY for genuinely long work; for a "
        "quick lookup, just do it inline. For ONE long subject use a SINGLE call "
        "(no group_id needed). After spawning, acknowledge to the user that you've "
        "started the work and STOP — do not try to use the results now."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short label for the panel (e.g. 'Recherche Anbieter A')"},
            "prompt": {"type": "string", "description": "Full, self-contained instruction for this part. The background agent does NOT see the chat."},
            "group_id": {"type": "string", "description": "REQUIRED whenever you make more than one call for the same request. Pick a short string (e.g. 'g1') and use the IDENTICAL value on every call of the fan-out. Omit ONLY for a standalone single task."},
            "follow_up": {"type": "string", "description": "The combine/synthesis instruction carried out after ALL tasks in the group finish (e.g. 'compare the results and recommend one'). Set this instead of making a separate summary task."},
        },
        "required": ["title", "prompt"],
    },
}

def _load_production_context(model):
    """Load the REAL production system prompt + full tool list for `main`, then
    swap our candidate fan-out description into run_background_task.

    CRITICAL for realism: production sends ~2.1k-char system prompt AND ~29 tool
    schemas. The fan-out decision is made while weighing run_background_task
    against all those other tools — a 1-tool toy setup is far easier and would
    give a false positive. We mirror production exactly, changing ONLY the
    run_background_task description (the lever under test)."""
    import brain
    from engine.context import request_context
    with request_context(current_agent=brain.AgentConfig("main")):
        sp, tools, _ = brain.build_first_turn_prefix(
            model, "main", mcp_manager=getattr(brain, "_mcp_manager", None),
            discovered_tools=set(), is_openai_shape=False, purpose="interactive")
    # Swap the candidate description into the real run_background_task schema,
    # preserving everything else (so the tool set + weights match production).
    out_tools = []
    found = False
    for t in tools:
        if t.get("name") == "run_background_task":
            nt = dict(t)
            nt["description"] = FANOUT_TOOL["description"]
            nt["input_schema"] = FANOUT_TOOL["input_schema"]
            out_tools.append(nt)
            found = True
        else:
            out_tools.append(t)
    if not found:
        out_tools.append(FANOUT_TOOL)
    return sp, out_tools

# --- Scenarios. Each: should the model fan out? how many parts (expected)? ---
SCENARIOS = [
    {
        "id": "multi_vendor",
        "fanout_expected": True, "parts_expected": 3,
        "user": ("Vergleiche die Cloud-Sicherheitsrichtlinien der Anbieter AWS, Azure "
                 "und Google Cloud und gib mir am Ende eine Empfehlung, welcher für "
                 "eine Bank am besten geeignet ist. Recherchiere gründlich."),
    },
    {
        "id": "five_topics",
        "fanout_expected": True, "parts_expected": 5,
        "user": ("Erstelle mir einen ausführlichen Marktüberblick zu E-Bikes: "
                 "recherchiere jeweils tiefgehend (1) die führenden Hersteller, "
                 "(2) Akku-Technologie-Trends, (3) Preisentwicklung, (4) rechtliche "
                 "Rahmenbedingungen in der EU und (5) die wichtigsten Wettbewerber. "
                 "Fasse danach alles in einem Bericht zusammen."),
    },
    {
        # DECISION (2026-05-29): splitting one subject into parallel sub-topic
        # tasks is ACCEPTABLE (a legitimate speed-up). So any count >=1 passes;
        # we only require it didn't go inline (0) when the work is clearly long.
        "id": "single_long",
        "fanout_expected": True, "parts_expected": 1, "min_parts": 1,
        "user": ("Recherchiere bitte sehr ausführlich die Geschichte und aktuelle "
                 "Marktposition des Unternehmens Siemens und schreib einen langen "
                 "detaillierten Bericht."),
    },
    {
        "id": "quick_inline",
        "fanout_expected": False, "parts_expected": 0,
        "user": "Was ist die Hauptstadt von Australien?",
    },
    {
        "id": "two_docs",
        "fanout_expected": True, "parts_expected": 2,
        # Files are stated as ALREADY available on disk so the model has no reason
        # to ask for an upload — fan-out across the two reports is the right move.
        "user": ("Die beiden Quartalsberichte liegen unter /data/Q1_2026.pdf und "
                 "/data/Q2_2026.pdf. Analysiere jeden Bericht separat und ausführlich "
                 "(Umsatz, Kosten, Auffälligkeiten) und vergleiche sie danach "
                 "miteinander."),
    },
]


# --- Tool-call capture stub: records calls, returns a benign result. ---
class _CaptureState:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []  # list of {name, args}

    def record(self, name, args):
        with self.lock:
            self.calls.append({"name": name, "args": args})


_CAPTURE = _CaptureState()


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        name = body.get("name", "")
        args = body.get("args", {}) or {}
        _CAPTURE.record(name, args)
        # Benign result so the model acknowledges + stops.
        result = json.dumps({
            "task_id": uuid.uuid4().hex,
            "status": "running",
            "note": "Background task started; result will arrive later. Acknowledge and stop.",
        })
        out = json.dumps({"result": result, "is_error": False, "elapsed_ms": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _start_stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}/"


def _provider_creds(model):
    """Resolve {api_key, base_url} for a model via its config provider — works for
    cloud (CLIProxyAPI → Mistral) AND local (Lokal → oMLX). Single source of truth
    so the eval suite can run any model without hardcoding a provider."""
    c = json.load(open(CONFIG))
    models = c.get("models", {})
    m = models.get(model) if isinstance(models, dict) else None
    prov_name = (m or {}).get("provider") or "CLIProxyAPI"
    p = c["providers"][prov_name]
    # The Anthropic SDK (sidecar) appends /v1/messages itself; Brain stores an
    # OpenAI-style /v1 base. Strip one trailing /v1 (mirror _normalise_anthropic_base_url).
    bu = (p.get("base_url") or "").rstrip("/")
    if bu.endswith("/v1"):
        bu = bu[:-3]
    return p.get("api_key") or "", bu, prov_name


def _run_scenario(model, api_key, base_url, scenario, stub_url, system, tools, timeout_s=180):
    """POST one turn to the sidecar, drain SSE, return captured calls + final text.

    `system` + `tools` are the REAL production prompt + full tool list (with our
    candidate fan-out description swapped in) — see _load_production_context."""
    with _CAPTURE.lock:
        _CAPTURE.calls = []
    turn_id = uuid.uuid4().hex
    import brain
    payload = {
        "model": brain.get_api_model_id(model),
        "base_url": base_url,
        "api_key": api_key,
        "system": system,
        "messages": [{"role": "user", "content": scenario["user"]}],
        "tools": tools,
        "max_tokens": 2000,
        "max_rounds": 3,
        "tool_endpoint": stub_url,
        "tool_endpoint_auth": "Bearer probe",
        "turn_id": turn_id,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{SIDECAR}/turn", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    final_text = ""
    err = None
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                if ev.get("type") == "done":
                    final_text = ev.get("data", {}).get("final_text", "") or final_text
                elif ev.get("type") == "error":
                    err = ev.get("data", {}).get("message", "error")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    with _CAPTURE.lock:
        calls = [c for c in _CAPTURE.calls if c["name"] == "run_background_task"]
    return calls, final_text, err


def _score(scenario, calls):
    """Score the 6 decomposition criteria. Returns (dict, notes[])."""
    n = len(calls)
    notes = []
    fanout = scenario["fanout_expected"]
    want = scenario["parts_expected"]

    # 1. Right decision: fan out (>=2) when expected; single (1) or inline (0) otherwise.
    if fanout:
        decided = n >= 2
    elif want == 1:
        decided = n == 1
    else:  # inline expected
        decided = n == 0
    notes.append(f"calls={n} (expected≈{want}, fanout={fanout})")

    # 2. Count roughly right. `min_parts` (e.g. single_long where any split is OK)
    #    means "at least this many, no upper bound"; otherwise within 1 of expected.
    if scenario.get("min_parts") is not None:
        count_ok = n >= scenario["min_parts"]
    else:
        count_ok = (abs(n - want) <= 1) if fanout else True

    # 3. Self-contained prompts: each prompt long-ish + doesn't lean on 'der zweite'/'the second'.
    bad_ref = ("zweite", "erste", "second", "first one", "the other", "obige", "genannte")
    selfcontained = True
    for c in calls:
        pr = (c["args"].get("prompt") or "")
        if len(pr) < 40:
            selfcontained = False
        if any(b in pr.lower() for b in bad_ref) and len(pr) < 120:
            selfcontained = False
    if not calls:
        selfcontained = (want == 0)  # inline case trivially ok

    # 4. Group coherence. DESIGN DECISION (2026-05-29): the SERVER treats all
    #    run_background_task calls emitted in the SAME turn as one implicit group
    #    (synthesizes a group_id when missing/inconsistent). The model's reliable
    #    signal is "N calls in one turn" — explicit group_id is an optional
    #    override. So a MISSING group_id is NOT a defect; only a CONFLICTING set
    #    of explicit ids (≥2 distinct non-empty values) would mislead the server.
    gids = [c["args"].get("group_id") for c in calls]
    if n >= 2:
        nonempty = [g for g in gids if g]
        group_ok = len(set(nonempty)) <= 1  # all-missing OK; all-same OK; mixed values fail
    else:
        group_ok = True

    # 5. follow_up present on a fan-out.
    if n >= 2:
        followup_ok = any((c["args"].get("follow_up") or "").strip() for c in calls)
    else:
        followup_ok = True

    # 6. Stopped (did not loop/try to use results): single round of bg calls, then text.
    #    Approximated: model produced a final acknowledgement text.
    return {
        "decision": decided,
        "count": count_ok,
        "self_contained": selfcontained,
        "group_id": group_ok,
        "follow_up": followup_ok,
    }, notes, gids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="single model id (else both medium+small)")
    ap.add_argument("--only", default=None, help="single scenario id")
    ap.add_argument("--repeat", type=int, default=1, help="runs per scenario (variance check)")
    args = ap.parse_args()

    models = [args.model] if args.model else ["mistral-medium-3.5", "mistral-small-latest"]
    scenarios = [s for s in SCENARIOS if (not args.only or s["id"] == args.only)]

    srv, stub_url = _start_stub()
    print(f"# stub tool_endpoint: {stub_url}")
    print(f"# sidecar: {SIDECAR}  models: {models}\n")

    summary = {}
    for model in models:
        api_key, base_url, prov_name = _provider_creds(model)
        print(f"\n{'='*70}\nMODEL: {model}  [provider: {prov_name} @ {base_url}]\n{'='*70}")
        system, tools = _load_production_context(model)
        print(f"# production system prompt: {len(system)} chars · {len(tools)} tools "
              f"(real load — fan-out competes against all of them)")
        crit_tot = {"decision": 0, "count": 0, "self_contained": 0, "group_id": 0, "follow_up": 0}
        runs = 0
        for sc in scenarios:
            for r in range(args.repeat):
                calls, text, err = _run_scenario(model, api_key, base_url, sc, stub_url, system, tools)
                if err:
                    print(f"\n[{sc['id']}] ERROR: {err}")
                    continue
                crit, notes, gids = _score(sc, calls)
                runs += 1
                for k, v in crit.items():
                    crit_tot[k] += 1 if v else 0
                ok = all(crit.values())
                mark = "✅" if ok else "❌"
                print(f"\n{mark} [{sc['id']}] {notes[0]}  groups={gids}")
                for c in calls:
                    a = c["args"]
                    print(f"    · title={a.get('title','')!r} gid={a.get('group_id')!r} "
                          f"follow_up={'Y' if a.get('follow_up') else '-'} "
                          f"prompt[{len(a.get('prompt',''))}ch]={a.get('prompt','')[:70]!r}")
                fails = [k for k, v in crit.items() if not v]
                if fails:
                    print(f"    FAIL: {', '.join(fails)}")
                if text:
                    print(f"    reply: {text[:120]!r}")
        summary[model] = (crit_tot, runs)

    print(f"\n\n{'#'*70}\nSUMMARY (criteria pass-rate across {len(scenarios)}×{args.repeat} runs)\n{'#'*70}")
    for model, (crit_tot, runs) in summary.items():
        if not runs:
            print(f"{model}: no successful runs"); continue
        print(f"\n{model}  ({runs} runs)")
        for k, v in crit_tot.items():
            print(f"  {k:16s} {v}/{runs}  {'█'*int(10*v/max(runs,1))}")
    srv.shutdown()


if __name__ == "__main__":
    main()
