#!/usr/bin/env python3
"""Data Workbench chart-pipeline canary.

Deterministic check (no LLM judge): for each question, send a chat turn to a
fresh workbench session that has `fixture.csv` loaded (table `fixture`), watch
the SSE stream for a `data_render_chart` tool call, and score the spec's mark +
encoded fields against `expect`. Ambiguity questions pass when NO chart call is
made (the model should clarify).

Usage:
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/data/run.py
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/data/run.py --only C1_bar_open_per_entity
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = os.environ.get("BRAIN_BASE", "http://127.0.0.1:8420")
USER = os.environ.get("BRAIN_USER", "admin")
PASS = os.environ.get("BRAIN_PASS", "admin")
HERE = os.path.dirname(os.path.abspath(__file__))


def _req(method, path, *, token=None, json_body=None, raw_body=None, headers=None, timeout=120):
    url = BASE + path
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def login():
    r = _req("POST", "/v1/auth/login", json_body={"username": USER, "password": PASS})
    return json.loads(r.read())["token"]


def upload_fixture(token, sid):
    path = os.path.join(HERE, "fixture.csv")
    with open(path, "rb") as f:
        body = f.read()
    boundary = "----dataeval" + str(int(time.time()))
    payload = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="fixture.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + body + f"\r\n--{boundary}--\r\n".encode()
    r = _req("POST", f"/v1/data/sessions/{sid}/upload", token=token, raw_body=payload,
             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(r.read())


def chat_turn(token, sid, message):
    """Send a chat turn, return (final_text, [tool_calls]). Drains the SSE stream."""
    body = json.dumps({"session_id": sid, "message": message}).encode()
    req = urllib.request.Request(
        BASE + "/v1/chat", data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST")
    text_parts = []
    tool_calls = []
    with urllib.request.urlopen(req, timeout=180) as resp:
        event = None
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                event = line[len("event: "):].strip()
            elif line.startswith("data: "):
                payload = line[len("data: "):]
                try:
                    data = json.loads(payload)
                except Exception:
                    data = {}
                if event == "text_delta":
                    text_parts.append(data.get("text", ""))
                elif event == "tool_call":
                    nm = data.get("name", "")
                    args = data.get("args", {})
                    if nm and (args or not any(tc["name"] == nm and not tc["args"] for tc in tool_calls)):
                        # update-in-place if args arrive on a second emission
                        if args and tool_calls and tool_calls[-1]["name"] == nm and not tool_calls[-1]["args"]:
                            tool_calls[-1]["args"] = args
                        else:
                            tool_calls.append({"name": nm, "args": args})
                elif event in ("done", "error"):
                    break
    return "".join(text_parts), tool_calls


def _collect_fields(spec):
    fields = set()
    def walk(node):
        if isinstance(node, dict):
            enc = node.get("encoding")
            if isinstance(enc, dict):
                for ch in enc.values():
                    for c in (ch if isinstance(ch, list) else [ch]):
                        if isinstance(c, dict) and isinstance(c.get("field"), str):
                            fields.add(c["field"])
            for k in ("layer", "concat", "hconcat", "vconcat", "spec", "facet"):
                v = node.get(k)
                if isinstance(v, list):
                    for x in v: walk(x)
                elif isinstance(v, dict):
                    walk(v)
        elif isinstance(node, list):
            for x in node: walk(x)
    walk(spec)
    return fields


def _mark_of(spec):
    m = spec.get("mark")
    if isinstance(m, dict):
        return m.get("type", "")
    return m or ""


def _y_aggregate_or_field(spec):
    """Return the set of {field names referenced anywhere as y, or 'y' if y uses an aggregate}."""
    out = set()
    def walk(node):
        if isinstance(node, dict):
            enc = node.get("encoding")
            if isinstance(enc, dict):
                y = enc.get("y")
                for c in (y if isinstance(y, list) else [y]):
                    if isinstance(c, dict):
                        if c.get("field"): out.add(c["field"])
                        if c.get("aggregate"): out.add("__aggregate__")
            for k in ("layer", "concat", "hconcat", "vconcat", "spec", "facet"):
                v = node.get(k)
                if isinstance(v, list):
                    for x in v: walk(x)
                elif isinstance(v, dict): walk(v)
        elif isinstance(node, list):
            for x in node: walk(x)
    walk(spec)
    return out


def score(q, text, tool_calls):
    exp = q["expect"]
    chart_calls = [tc for tc in tool_calls if tc["name"] == "data_render_chart"]
    if exp.get("chart") is False:
        # Should clarify, not chart.
        if chart_calls:
            return 0.0, "made a chart instead of clarifying"
        # crude clarify heuristic
        t = text.lower()
        asked = "?" in text or any(w in t for w in ("which", "what kind", "could you", "do you mean", "clarify", "welche", "was genau"))
        return (1.0, "clarified (no chart)") if asked else (0.5, "no chart, but didn't clearly ask")
    if not chart_calls:
        return 0.0, "no data_render_chart call"
    tc = chart_calls[-1]
    spec = tc["args"].get("spec") or {}
    if not isinstance(spec, dict):
        return 0.0, "data_render_chart called with non-dict spec"
    notes = []
    pts = 0.0; total = 0.0
    # mark
    total += 1
    if _mark_of(spec) in exp.get("mark_in", []):
        pts += 1
    else:
        notes.append(f"mark={_mark_of(spec)!r} not in {exp.get('mark_in')}")
    # fields: the model may chart a `register_as`'d aggregated table whose
    # columns are renamed (entity stays, findings_open → total_open). So we
    # accept an expected field if it appears verbatim OR if SOME encoded field
    # name contains the discriminating dimension token (entity/tier/region/month).
    used = _collect_fields(spec)
    want_fields = set(exp.get("fields", []))
    if want_fields:
        total += 1
        def _present(f):
            if f in used:
                return True
            tok = f.split("_")[0].lower()  # 'entity', 'tier', 'region', 'opened'→ matches 'opened_month'
            return any(tok in u.lower() for u in used)
        missing = [f for f in want_fields if not _present(f)]
        if not missing:
            pts += 1
        else:
            notes.append(f"missing fields {sorted(missing)} (encoded: {sorted(used)})")
    return (pts / total if total else 1.0), ("ok" if not notes else "; ".join(notes))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = set(x.strip() for x in args.only.split(",") if x.strip())

    qs = json.load(open(os.path.join(HERE, "questions.json")))["questions"]
    if only:
        qs = [q for q in qs if q["id"] in only]

    token = login()
    results = []
    for q in qs:
        # Fresh workbench per question (follow-ups set up their context turn first).
        r = _req("POST", "/v1/data/sessions", token=token, json_body={"title": f"eval {q['id']}"})
        sid = json.loads(r.read())["session_id"]
        try:
            upload_fixture(token, sid)
            ctx_after = q.get("context_after")
            if ctx_after:
                ctx_q = next((x for x in json.load(open(os.path.join(HERE, "questions.json")))["questions"] if x["id"] == ctx_after), None)
                if ctx_q:
                    chat_turn(token, sid, ctx_q["prompt"])
            text, tool_calls = chat_turn(token, sid, q["prompt"])
            sc, note = score(q, text, tool_calls)
            results.append((q["id"], sc, note))
            print(f"  {q['id']:<32} {sc:>4.2f}  {note}")
        finally:
            try:
                _req("DELETE", f"/v1/sessions/{sid}", token=token)
            except Exception:
                pass
    if results:
        mean = sum(s for _, s, _ in results) / len(results)
        print(f"\n  mean: {mean:.3f}  ({len(results)} questions)")


if __name__ == "__main__":
    main()
