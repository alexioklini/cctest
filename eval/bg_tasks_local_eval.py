#!/usr/bin/env python3
"""Background-task quality eval: the M4-flipped bg LLM tasks, Qwen vs cloud Mistral.

This is the PERSISTENT rebuild of the throwaway /tmp/bench_local.py that drove the
2026-06-14 decision (project_local_bg_model_vllmmetal_bench). It checks the exact
background tasks we flipped to the local M4 model — using the REAL production
prompts imported from brain (no drift) — against the cloud Mistral they replaced.

Flipped tasks under test (the 3 knobs):
  - auto-route classifier   (chat_summary_model)      -> forced 'route' tool, JSON-valid + memory routing
  - chat summary (German)   (chat_summary_model)      -> truly SUMMARIZES (1 sentence), not concatenates
  - memory classifier       (chat_sync.classifier)    -> one-word category label, in the valid set
  (refinement is prose like summary; covered by the summary signal.)

Both M4 (vllm-metal Qwen) and the cloud baseline (Mistral via CLIProxyAPI) speak
Anthropic /v1/messages, so we POST directly to each — the sidecar adds nothing the
comparison needs. Reads endpoints + prompts from config.json / brain.

Run:
  python3 eval/bg_tasks_local_eval.py                 # 3 reps each, both models
  python3 eval/bg_tasks_local_eval.py --reps 5
  python3 eval/bg_tasks_local_eval.py --only classifier,summary
"""
import argparse
import json
import re
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, ".")
import brain  # noqa: E402  -- for the real production prompts + enums

CONFIG = "config.json"

# Models under comparison: (label, model_id-in-config, cloud?)
M4_MODEL = "Lokal-M4/Qwen2.5-7B-Instruct-4bit"
M4_MODEL_3B = "Lokal-M4-3B/Qwen2.5-3B-Instruct-4bit"   # smaller local candidate (port 8013)
CLOUD_SMALL = "CLIProxyAPI/mistral-small-latest"   # summary + classifier baseline
CLOUD_MEDIUM = "CLIProxyAPI/mistral-medium-3.5"    # memory-classifier baseline

# Endpoints not registered in config.json (ad-hoc bench candidates served on the M4).
# vllm-metal serves one model per process: 7B on :8012 (config), 3B on :8013 (here).
_INLINE_ENDPOINTS = {
    M4_MODEL_3B: {"base": "http://192.168.1.214:8013", "api_key": "brain",
                  "served": "Qwen2.5-3B-Instruct-4bit"},
}


def _resolve(model_id):
    if model_id in _INLINE_ENDPOINTS:
        return dict(_INLINE_ENDPOINTS[model_id])
    c = json.load(open(CONFIG))
    mc = c["models"].get(model_id, {})
    p = mc.get("provider")
    pc = c["providers"].get(p, {})
    base = (pc.get("base_url") or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return {
        "base": base,                      # e.g. http://192.168.1.214:8012
        "api_key": pc.get("api_key") or "",
        "served": mc.get("base_model_id") or model_id,
    }


def _messages_call(model_id, system, user, max_tokens=200, tools=None, tool_choice=None):
    """POST /v1/messages (Anthropic). Returns (text, tool_input, latency_s, err)."""
    ep = _resolve(model_id)
    body = {
        "model": ep["served"],
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        ep["base"] + "/v1/messages", data=data,
        headers={"content-type": "application/json",
                 "anthropic-version": "2023-06-01",
                 "authorization": f"Bearer {ep['api_key']}",
                 "x-api-key": ep["api_key"]})
    t0 = time.time()
    try:
        r = json.load(urllib.request.urlopen(req, timeout=60))
    except Exception as e:
        return None, None, time.time() - t0, str(e)[:160]
    dt = time.time() - t0
    text, tin = "", None
    for blk in r.get("content", []):
        if blk.get("type") == "text":
            text += blk.get("text", "")
        elif blk.get("type") == "tool_use":
            tin = blk.get("input")
    return text.strip(), tin, dt, None


# ---- the forced route tool, built from brain's live enums (no drift) ----
ROUTE_TOOL = {
    "name": "route",
    "description": "Emit the routing analysis for this task.",
    "input_schema": {
        "type": "object",
        "properties": {
            "task_types": {"type": "array",
                           "items": {"type": "string", "enum": sorted(brain._TASK_TYPE_TIER.keys())},
                           "minItems": 1, "maxItems": 3},
            "tools": {"type": "array",
                      "items": {"type": "string", "enum": sorted(brain._TASK_TOOL_GROUPS.keys())}},
            "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["task_types", "tools", "complexity"],
    },
}

# 15 KG-Real-Policies style routing cases. internal=True => 'memory' must be picked.
CLASSIFY_CASES = [
    ("Was sagt die Konzernrichtlinie zur Aufbewahrung von Kundendaten?", True),
    ("What does our internal vacation policy say about carryover days?", True),
    ("Summarize the attached Q3 financial report.", True),
    ("What were the key decisions in our last project meeting?", True),
    ("Welche Meldepflichten gelten laut unserer Geldwäsche-Richtlinie?", True),
    ("Erkläre die Schritte im Onboarding-Prozess laut Handbuch.", True),
    ("What's the weather in Zurich right now?", False),
    ("Find the latest news on the EU AI Act.", False),
    ("Translate this memo to French.", False),
    ("Write a python script to parse a CSV file.", False),
    ("Wie hoch ist der aktuelle EUR/USD Wechselkurs?", False),
    ("Was steht in unserer IT-Sicherheitsrichtlinie zu Passwörtern?", True),
    ("Create a flowchart of our approval process as a PNG.", True),
    ("Schedule a reminder for tomorrow at 9am.", False),
    ("Welche Fristen nennt der Kündigungsbrief im Projektordner?", True),
]

# German chat-summary cases: the production prompt is
# "Output only a brief summary sentence. No quotes, no prefix." The user content
# is a multi-turn sample. Good = ONE sentence that SUMMARIZES (not concatenates).
SUMMARY_USER = (
    "Fasse das folgende Gespräch in einem kurzen Satz zusammen:\n"
    "User: Wie eröffne ich ein Konto bei der Wiener Privatbank?\n"
    "User: Welche Unterlagen brauche ich dafür?\n"
    "User: Und wie lange dauert die Freischaltung?"
)

# Memory-classifier cases: (user, assistant, expected-category-in-validset-or-None)
MEMCLF_CASES = [
    ("Ich bevorzuge Antworten auf Deutsch.", "Verstanden, ich antworte auf Deutsch.", "preference"),
    ("Wir haben entschieden, Qdrant als Vektor-Backend zu nutzen.", "Gute Wahl.", "decision"),
    ("Die Hauptstadt von Österreich ist Wien.", "Korrekt.", "fact"),
    ("Hallo, wie geht's?", "Mir geht es gut, danke!", None),  # chitchat -> not filed
]


def run_classifier(model_id, reps):
    """Forced-tool routing: JSON-valid rate + memory-when-expected rate + latency."""
    valid = 0; total = 0; mem_hit = 0; mem_exp = 0; lats = []
    for case, internal in CLASSIFY_CASES:
        for _ in range(reps):
            total += 1
            txt, tin, dt, err = _messages_call(
                model_id, brain._STRUCTURED_CLASSIFY_SYSTEM, case,
                max_tokens=64, tools=[ROUTE_TOOL],
                tool_choice={"type": "tool", "name": "route"})
            lats.append(dt)
            if err or not isinstance(tin, dict) or "task_types" not in tin or "complexity" not in tin:
                continue
            valid += 1
            if internal:
                mem_exp += 1
                if "memory" in [t.lower() for t in tin.get("tools", [])]:
                    mem_hit += 1
    return {
        "json_valid": f"{valid}/{total}",
        "json_valid_pct": round(100 * valid / total, 1) if total else 0,
        "memory_when_expected": f"{mem_hit}/{mem_exp}",
        "memory_pct": round(100 * mem_hit / mem_exp, 1) if mem_exp else 0,
        "latency_s": round(statistics.mean(lats), 2) if lats else None,
    }


def run_summary(model_id, reps):
    """German summary: does it produce ONE sentence (summarize) not a concat?"""
    one_sentence = 0; total = 0; lats = []; samples = []
    for _ in range(reps):
        total += 1
        txt, _t, dt, err = _messages_call(
            model_id, "Output only a brief summary sentence. No quotes, no prefix.",
            SUMMARY_USER, max_tokens=120)
        lats.append(dt)
        if err or not txt:
            continue
        # heuristic: a SUMMARY is one sentence; concat keeps the 3 separate "?"s
        qmarks = txt.count("?")
        sentences = len([s for s in re.split(r"[.!?]+", txt) if s.strip()])
        if qmarks <= 1 and sentences <= 2:
            one_sentence += 1
        if len(samples) < 2:
            samples.append(txt[:200])
    return {
        "summarized_1sentence": f"{one_sentence}/{total}",
        "latency_s": round(statistics.mean(lats), 2) if lats else None,
        "samples": samples,
    }


def run_memclf(model_id, reps):
    """Memory classifier: one-word label in the valid set, matches expected."""
    valid_label = 0; correct = 0; total = 0; lats = []; got = []
    validset = {"fact", "preference", "decision", "reference", "generic", "refusal", "chitchat"}
    for user, asst, expected in MEMCLF_CASES:
        for _ in range(reps):
            total += 1
            content = f"User: {user[:2000]}\nAssistant: {asst[:2000]}"
            txt, _t, dt, err = _messages_call(
                model_id, brain._MEMORY_CLASSIFIER_PROMPT, content, max_tokens=20)
            lats.append(dt)
            if err or not txt:
                continue
            lab = txt.strip().strip('"').strip("'").lower()
            if "<think>" in lab and "</think>" in lab:
                lab = lab.split("</think>", 1)[1].strip()
            lab = lab.split()[0].strip(",.;:!?\"'`").lower() if lab else ""
            if lab in validset:
                valid_label += 1
                if expected is None or lab == expected:
                    correct += 1
            if len(got) < 4:
                got.append(f"{expected or '(chitchat)'}->{lab}")
    return {
        "valid_label": f"{valid_label}/{total}",
        "expected_match": f"{correct}/{total}",
        "latency_s": round(statistics.mean(lats), 2) if lats else None,
        "samples": got,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3, help="reps per case (>=3 per the noise rule)")
    ap.add_argument("--only", default="classifier,summary,memclf",
                    help="comma list: classifier,summary,memclf")
    args = ap.parse_args()
    only = set(args.only.split(","))

    tasks = [
        ("classifier", run_classifier,
         [("M4-Qwen-7B", M4_MODEL), ("M4-Qwen-3B", M4_MODEL_3B), ("cloud-small", CLOUD_SMALL)]),
        ("summary",    run_summary,
         [("M4-Qwen-7B", M4_MODEL), ("M4-Qwen-3B", M4_MODEL_3B), ("cloud-small", CLOUD_SMALL)]),
        ("memclf",     run_memclf,
         [("M4-Qwen-7B", M4_MODEL), ("M4-Qwen-3B", M4_MODEL_3B), ("cloud-medium", CLOUD_MEDIUM)]),
    ]
    out = {"reps": args.reps, "results": {}}
    for name, fn, models in tasks:
        if name not in only:
            continue
        out["results"][name] = {}
        print(f"\n{'='*60}\n{name.upper()}  (reps={args.reps})\n{'='*60}")
        for label, model_id in models:
            print(f"  running {label} ({model_id})...", flush=True)
            res = fn(model_id, args.reps)
            out["results"][name][label] = res
            print(f"  {label}: {json.dumps(res, ensure_ascii=False)}")
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = f"eval/results/bg_tasks_local_{ts}.json"
    json.dump(out, open(path, "w"), indent=2, ensure_ascii=False)
    print(f"\nSaved -> {path}")


if __name__ == "__main__":
    main()
