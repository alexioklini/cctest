"""Detector #2 (ours_spacy) and the regex-half of #3 (ours_gliner).

Calls the RUNNING server's POST /v1/gdpr/scan-text, which runs the real
production pipeline `engine._pii_scan_text` (71 regex + checksums + spaCy NER,
with rule-ordering, min_occurrences, context gates). We go over HTTP on purpose:
importing brain standalone gives a process with no server_config -> wrong
behavior (see memory feedback_never_probe_server_config_via_import).

We call the endpoint in `{"full": true}` mode (an eval-only escape hatch added
to the handler) so it returns EVERY finding's raw value + offsets, uncapped —
the default aggregated response caps samples at 3 per rule_id, which would lose
occurrences on org-dense policy docs.

Two entry points:
  * detect_full(text)  -> regex + spaCy NER (detector #2, the baseline)
  * detect_regex_only(text) -> drops NER-sourced rules name/address/organisation
    so the caller can pair regex with GLiNER (detector #3).

Auth: BRAIN_USER / BRAIN_PASS env (same as eval/run.py). Token cached.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common import Finding, map_our_finding  # noqa: E402

_BASE = os.environ.get("BRAIN_BASE_URL", "http://127.0.0.1:8420").rstrip("/")
_TOKEN = None
_NER_RULES = {"name", "address", "organisation"}
# Default: measure raw DETECTION capability (min_occurrences gate neutralized),
# so per-sentence handcrafted cases aren't zeroed by the production
# single-occurrence suppression. Set PII_OURS_PRODUCTION_GATE=1 to instead
# measure exactly what production fires (gate ON).
_RAW = os.environ.get("PII_OURS_PRODUCTION_GATE", "") not in ("1", "true", "yes")
# Opt-in name-precision gate (tightens the `name` rule against German-common-noun
# FPs). None = leave server default; set PII_OURS_NAME_PRECISION=1/0 to force.
_NAME_PREC_ENV = os.environ.get("PII_OURS_NAME_PRECISION", "")
_NAME_PREC = (None if _NAME_PREC_ENV == ""
              else _NAME_PREC_ENV in ("1", "true", "yes"))


def available() -> tuple[bool, str]:
    try:
        _login()
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _login() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    user = os.environ.get("BRAIN_USER")
    pwd = os.environ.get("BRAIN_PASS")
    if not user or not pwd:
        raise RuntimeError("set BRAIN_USER and BRAIN_PASS env vars")
    req = urllib.request.Request(
        _BASE + "/v1/auth/login",
        data=json.dumps({"username": user, "password": pwd}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read().decode())
    if "token" not in body:
        raise RuntimeError(f"login failed: {body}")
    _TOKEN = body["token"]
    return _TOKEN


def _scan(text: str) -> list[dict]:
    token = _login()
    payload = {"text": text, "source": "pii_eval", "full": True,
               "raw_detection": _RAW}
    if _NAME_PREC is not None:
        payload["name_precision"] = _NAME_PREC
    req = urllib.request.Request(
        _BASE + "/v1/gdpr/scan-text",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read().decode())
    if body.get("disabled"):
        raise RuntimeError("gdpr_scanner is DISABLED in config.json — enable it to eval our detector")
    return body.get("findings", [])


def _to_findings(findings: list[dict], drop_ner: bool) -> list[Finding]:
    out: list[Finding] = []
    for f in findings:
        rid = f.get("rule_id", "?")
        if drop_ner and rid in _NER_RULES:
            continue
        canon = map_our_finding(rid, f.get("category", ""))
        val = (f.get("value") or "").strip()
        if val:
            out.append(Finding(value=val, type=canon))
    return out


def detect_full(text: str) -> list[Finding]:
    return _to_findings(_scan(text), drop_ner=False)


def detect_regex_only(text: str) -> list[Finding]:
    return _to_findings(_scan(text), drop_ner=True)
