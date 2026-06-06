#!/usr/bin/env python3
"""Live-endpoint parity check for the two-tier refine (build step 3).

Confirms the DEPLOYED handler (handlers/admin_artifacts.py) behaves like the
validated harness spec (eval/refine_eval.py build_new): POSTs /v1/refine for
each case with tier=polish AND tier=engineer, and asserts:
  - both return 200 with a `tier` field echoing the request,
  - profile_field silently falls back to polish even if engineer asked,
  - purpose=scheduled_task is accepted,
  - engineer output is non-empty and (on non-trivial drafts) differs from polish.
It does NOT re-judge quality (the harness already did) — it verifies the wire
contract + that grounding/model-hint assembly doesn't error server-side.

Run:
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/refine_live_check.py
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("BRAIN_BASE", "http://127.0.0.1:8420")


def _post(path, body, token=None, timeout=120):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE.rstrip("/") + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def login(user, pwd):
    code, body = _post("/v1/auth/login", {"username": user, "password": pwd}, timeout=10)
    if code != 200 or "token" not in body:
        raise SystemExit(f"login failed ({code}): {body}")
    return body["token"]


CHECKS = [
    {"id": "chat_vague", "purpose": "chat_prompt", "text": "fix the auth bug", "differ": True},
    {"id": "chat_clean", "purpose": "chat_prompt",
     "text": "Read engine/provider.py and explain how LocalProviderQueue serializes oMLX requests. Quote the lines.",
     "differ": False},
    {"id": "sched_vague", "purpose": "scheduled_task", "text": "summarize my emails every morning", "differ": True},
    {"id": "soul_rough", "purpose": "soul",
     "text": "you are a helpdesk bot. you help users. dont make stuff up.", "differ": True},
    {"id": "profile_fallback", "purpose": "profile_field",
     "text": "i am a backend engineer who likes terse answers", "differ": False},
]


def main():
    user = os.environ.get("BRAIN_USER", "admin")
    pwd = os.environ.get("BRAIN_PASS", "admin")
    token = login(user, pwd)
    print(f"logged in as {user}; BASE={BASE}\n")

    failures = []
    for c in CHECKS:
        out = {}
        for tier in ("polish", "engineer"):
            code, resp = _post("/v1/refine", {
                "text": c["text"], "purpose": c["purpose"], "tier": tier,
            }, token=token)
            if code != 200:
                failures.append(f"{c['id']}/{tier}: HTTP {code} {resp}")
                out[tier] = None
                continue
            refined = (resp.get("refined") or "").strip()
            echoed = resp.get("tier")
            # profile_field must fall back to polish regardless of request
            want_tier = "polish" if c["purpose"] == "profile_field" else tier
            if echoed != want_tier:
                failures.append(f"{c['id']}/{tier}: tier echoed {echoed!r}, want {want_tier!r}")
            if not refined:
                failures.append(f"{c['id']}/{tier}: empty refined output")
            out[tier] = refined
            print(f"  {c['id']:18s} {tier:8s} tier={echoed!r:11s} len={len(refined):4d}  {refined[:70]!r}")

        # differ expectation (only meaningful when both succeeded)
        if c["differ"] and out.get("polish") and out.get("engineer"):
            if out["polish"] == out["engineer"]:
                failures.append(f"{c['id']}: engineer == polish but expected them to differ")
        print()

    print("=" * 60)
    if failures:
        print("LIVE PARITY: FAIL")
        for f in failures:
            print("  ✗", f)
        sys.exit(1)
    print("LIVE PARITY: PASS — handler matches the wire contract.")


if __name__ == "__main__":
    main()
