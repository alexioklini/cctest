#!/usr/bin/env python3
"""Background-task fan-out END-TO-END test — runs against the LIVE brain-agent.

Unlike eval/fanout_probe.py (which stubs the tool endpoint and only measures the
MODEL's decomposition), this exercises the WHOLE real stack the same way the
policy eval (eval/run.py) does — over the running server's HTTP API:

    POST /v1/chat  →  sidecar (real LLM)  →  real tool_run_background_task
      →  real spawn (daemon threads)  →  real background_tasks DB rows
      →  each task runs a REAL sidecar turn
      →  real atomic claim_background_group (last finisher)
      →  real deliver_background_group  →  a join answer lands in the conversation

No stubs. It then verifies the real artifacts via the API:
  - GET /v1/background-tasks   → tasks were spawned, share ONE group_id, all
                                 reached a terminal status.
  - GET /v1/sessions/<id>/messages → a `background_delivery` turn appeared with a
                                 non-empty assistant answer (the join fired).

This is SLOW (each scenario runs N real research turns) and needs the server +
sidecar up, with a cloud model that actually fans out (Mistral; gemma-4-26b
collapses to one task — still valid, asserted as a group-of-one). Use it to
confirm the final implementation matches what the probe predicted.

Run:
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/fanout_e2e.py
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/fanout_e2e.py --only multi_vendor
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/fanout_e2e.py --model mistral-medium-3.5
"""
import argparse
import json
import os
import time
import urllib.request

BASE = os.environ.get("BRAIN_BASE", "http://127.0.0.1:8420")
# Each background task is asked to do SHORT work — we test the plumbing/join,
# not research depth, so keep real LLM turns quick. Generous overall budgets.
CHAT_TIMEOUT = 180          # the spawning turn (model decides + fires tasks)
GROUP_DEADLINE = 900        # wait up to 15 min for all tasks + the join turn
SPAWN_ATTEMPTS = 4          # retry the (stochastic) fan-out decision this many times

# Same prompts as eval/fanout_probe.py's fan-out scenarios — deliberately NOT
# trimmed. An earlier "keep it short" variant SUPPRESSED the fan-out: the tool's
# own heuristic is "use background tasks ONLY for genuinely long work; for a quick
# lookup, do it inline", so telling the model to be brief made it (correctly)
# answer inline. The fan-out decision needs a genuinely long/thorough request.
# This makes real runs slower — acceptable; this is the faithful e2e.
SCENARIOS = [
    {
        "id": "multi_vendor", "min_tasks": 2,
        "user": ("Vergleiche die Cloud-Sicherheitsrichtlinien der Anbieter AWS, Azure "
                 "und Google Cloud und gib mir am Ende eine Empfehlung, welcher für "
                 "eine Bank am besten geeignet ist. Recherchiere gründlich."),
    },
    {
        "id": "two_topics", "min_tasks": 2,
        "user": ("Erstelle mir einen ausführlichen Überblick zu zwei Themen, jeweils "
                 "separat und tiefgehend recherchiert: (1) die führenden E-Bike-"
                 "Hersteller weltweit und (2) aktuelle Akku-Technologie-Trends bei "
                 "E-Bikes. Fasse danach alles in einem Bericht zusammen."),
    },
]


def _post(path, body, token=None, timeout=30):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE.rstrip("/") + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def _get(path, token=None, timeout=30):
    req = urllib.request.Request(BASE.rstrip("/") + path, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def login(user, pwd):
    code, body = _post("/v1/auth/login", {"username": user, "password": pwd}, timeout=10)
    if code != 200 or "token" not in body:
        raise SystemExit(f"login failed ({code}): {body}")
    return body["token"]


def create_session(token, model):
    body = {"agent": "main", "project": "", "skip_warmup": True}
    if model:
        body["model"] = model
    code, resp = _post("/v1/sessions", body, token=token, timeout=20)
    if code != 200 or "session_id" not in resp:
        raise RuntimeError(f"create_session failed ({code}): {resp}")
    return resp["session_id"]


def chat_turn(token, sid, message, timeout):
    """POST /v1/chat, drain SSE until done. Returns (final_text, tool_events)."""
    body = {"session_id": sid, "message": message}
    req = urllib.request.Request(BASE.rstrip("/") + "/v1/chat",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "text/event-stream")
    final, tools, ev, buf = {}, [], None, []
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            if time.time() - start > timeout:
                raise TimeoutError("chat_turn timeout")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith(":"):
                continue
            if line == "":
                if ev and buf:
                    try:
                        payload = json.loads("\n".join(buf))
                    except Exception:
                        payload = {}
                    if ev == "done":
                        final = payload
                        break
                    if ev in ("tool_use", "tool_call", "tool_result"):
                        tools.append({"event": ev, "data": payload})
                ev, buf = None, []
                continue
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                buf.append(line[6:])
    return final.get("text", ""), tools


def list_tasks(token, sid):
    _, body = _get(f"/v1/background-tasks?session_id={sid}", token=token)
    return body.get("tasks", [])


def get_messages(token, sid):
    _, body = _get(f"/v1/sessions/{sid}/messages", token=token)
    return body.get("messages", [])


def run_scenario(token, sc, model):
    print(f"\n=== [{sc['id']}] model={model} ===")
    sid = create_session(token, model)
    # 1) Spawning turn. The fan-out DECISION is stochastic (the model answers
    # inline ~1 in 5 on medium — measured in fanout_probe.py); that's the model's
    # judgment, NOT the plumbing under test here. So retry the spawn turn (fresh
    # session each time) up to SPAWN_ATTEMPTS until the model actually fans out,
    # then assert the PLUMBING. A run where it never fans out is reported as
    # "model-declined" (not a plumbing failure).
    tasks0 = []
    for attempt in range(1, SPAWN_ATTEMPTS + 1):
        spawn_text, tools = chat_turn(token, sid, sc["user"], CHAT_TIMEOUT)
        tasks0 = list_tasks(token, sid)
        print(f"  spawn attempt {attempt}: {len(tasks0)} task row(s) created · "
              f"ack={spawn_text[:70]!r}")
        if tasks0:
            break
        if attempt < SPAWN_ATTEMPTS:
            sid = create_session(token, model)  # fresh session, retry
    if not tasks0:
        return {"id": sc["id"], "ok": None,
                "why": f"model declined to fan out in {SPAWN_ATTEMPTS} attempts (stochastic; not a plumbing failure)"}

    groups = {t.get("group_id") for t in tasks0 if t.get("group_id")}
    print(f"  group_id(s): {groups or '(none)'}  ·  task count: {len(tasks0)}")

    # 2) Wait for ALL tasks terminal AND the join delivery turn to appear.
    deadline = time.time() + GROUP_DEADLINE
    delivered_turn = None
    last_status = ""
    while time.time() < deadline:
        tasks = list_tasks(token, sid)
        running = [t for t in tasks if t.get("status") == "running"]
        status = " ".join(f"{t['title'][:14]}:{t['status']}" for t in tasks)
        if status != last_status:
            print(f"    … {len(tasks)-len(running)}/{len(tasks)} terminal | {status}")
            last_status = status
        if not running and tasks:
            # All tasks terminal — look for the delivery turn (real join fired).
            msgs = get_messages(token, sid)
            for i, m in enumerate(msgs):
                md = m.get("metadata") or {}
                if m.get("role") in ("user", "human") and md.get("background_delivery"):
                    # the assistant answer right after it is the join result
                    nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                    if nxt and nxt.get("role") == "assistant" and (nxt.get("content") or "").strip():
                        delivered_turn = nxt
                        break
            if delivered_turn:
                break
        time.sleep(3)

    tasks = list_tasks(token, sid)
    terminal = [t for t in tasks if t.get("status") != "running"]
    # Assertions — the REAL plumbing contract.
    checks = {
        "spawned_min": len(tasks) >= sc["min_tasks"],
        "one_group": len({t.get("group_id") for t in tasks if t.get("group_id")}) == 1,
        "all_terminal": len(terminal) == len(tasks) and len(tasks) > 0,
        "join_delivered": delivered_turn is not None,
        "join_nonempty": bool(delivered_turn and (delivered_turn.get("content") or "").strip()),
    }
    ok = all(checks.values())
    print(f"  RESULT: {'✅ PASS' if ok else '❌ FAIL'}  {checks}")
    if delivered_turn:
        print(f"  join answer: {(delivered_turn.get('content') or '')[:160]!r}")
    return {"id": sc["id"], "ok": ok, "checks": checks,
            "tasks": len(tasks), "delivered": delivered_turn is not None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mistral-medium-3.5")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    user = os.environ.get("BRAIN_USER", "admin")
    pwd = os.environ.get("BRAIN_PASS", "admin")
    token = login(user, pwd)
    scenarios = [s for s in SCENARIOS if (not args.only or s["id"] == args.only)]

    print(f"# e2e against {BASE}  model={args.model}  (LIVE — no stubs)")
    results = []
    for sc in scenarios:
        try:
            results.append(run_scenario(token, sc, args.model))
        except Exception as e:
            print(f"  ERROR [{sc['id']}]: {type(e).__name__}: {e}")
            results.append({"id": sc["id"], "ok": False, "why": str(e)})

    # ok is True/False for runs that fanned out; None = model declined (stochastic,
    # not a plumbing verdict). The exit code fails ONLY on a real plumbing failure
    # (ok is False); declined runs don't fail the suite (they prove nothing about
    # plumbing) but are surfaced loudly.
    npass = sum(1 for r in results if r.get("ok") is True)
    nfail = sum(1 for r in results if r.get("ok") is False)
    ndeclined = sum(1 for r in results if r.get("ok") is None)
    print(f"\n{'#'*60}\nE2E SUMMARY: {npass} passed · {nfail} FAILED · {ndeclined} model-declined "
          f"(of {len(results)})")
    for r in results:
        mark = "✅" if r.get("ok") is True else ("⚪" if r.get("ok") is None else "❌")
        extra = r.get("why") or r.get("checks") or ""
        print(f"  {mark} {r['id']}: {extra}")
    raise SystemExit(1 if nfail else 0)


if __name__ == "__main__":
    main()
