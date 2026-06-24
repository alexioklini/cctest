"""Detector #4: M4-7B (Qwen2.5-7B-Instruct-4bit on the Mac mini M4) as the SOLE
PII detection engine — the LLM does extraction + classification, no regex/NER.

Calls the sidecar /turn?stream=false (the standard eval path, mirrors
eval/m4_7b_usecase_eval.py). We force a strict JSON contract and parse it. The
model is non-deterministic, so the runner repeats it >=3 times and reports
mean+/-spread.

Provider creds resolve from config.json -> providers (Lokal-M4). The sidecar
needs the model id + base_url + api_key in the payload.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common import Finding, LLM_MAP, CANON_TYPES  # noqa: E402

_MODEL = os.environ.get("PII_M4_MODEL", "Qwen2.5-7B-Instruct-4bit")
_PROVIDER = os.environ.get("PII_M4_PROVIDER", "Lokal-M4")
# We call the M4 vLLM host DIRECTLY via its native Anthropic /v1/messages
# endpoint, NOT through the Brain sidecar /turn. Rationale: the sidecar /turn
# path was returning empty completions for both cloud + local models in this
# environment (stop_reason=api_error / empty final_text), while the M4 host
# answers correctly when hit directly. Fewer moving parts = a more faithful
# measure of the MODEL's detection ability, which is what the eval is about.

_SYSTEM = (
    "Du bist ein präziser PII-Detektor für deutsche Banktexte. "
    "Finde ALLE personenbezogenen Daten und Geheimnisse im Text. "
    "Gib AUSSCHLIESSLICH ein JSON-Array zurück, keine Erklärung. "
    "Jedes Element: {\"value\": <exakter Textausschnitt>, \"type\": <Typ>}. "
    "Erlaubte Typen: " + ", ".join(CANON_TYPES) + ". "
    "Regeln: name=Personenname; address=Postanschrift/Ort einer Person; "
    "organisation=Firma/Produkt/System; email; phone; iban; credit_card; "
    "national_id=Steuer-ID/Sozialversicherung/Pass/PESEL/BSN/AHV usw.; "
    "secret=API-Key/Token/Passwort/Private-Key; network=IP-Adresse; "
    "date=Geburts-/Einstellungs-/Ereignisdatum. "
    "Markiere KEINE generischen Rollen (z.B. 'Leitung Compliance'), "
    "Gesetzesnamen oder Richtliniennamen. Wenn nichts gefunden: []."
)

_USER_TMPL = "Text:\n\"\"\"\n{text}\n\"\"\"\n\nJSON-Array:"


def available() -> tuple[bool, str]:
    try:
        creds = _creds()
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _creds():
    cfg_path = os.environ.get("BRAIN_CONFIG", "config.json")
    c = json.load(open(cfg_path, encoding="utf-8"))
    p = c["providers"][_PROVIDER]
    base = (p.get("base_url") or "").rstrip("/")
    return p.get("api_key") or "brain", base


def _call(text: str, temperature: float = 0.0) -> str:
    api_key, base = _creds()  # base like http://192.168.1.214:8012/v1
    url = base.rstrip("/") + "/messages"
    payload = {
        "model": _MODEL, "max_tokens": 1200, "temperature": temperature,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": _USER_TMPL.format(text=text)}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    # native Anthropic shape: content is a list of blocks
    parts = []
    for blk in d.get("content", []) or []:
        if isinstance(blk, dict) and blk.get("type") == "text":
            parts.append(blk.get("text", ""))
    return "".join(parts)


def _parse(raw: str) -> list[Finding]:
    # Pull the first JSON array out of the model's text.
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    out: list[Finding] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        val = str(item.get("value", "")).strip()
        t = str(item.get("type", "")).strip().lower()
        if not val:
            continue
        out.append(Finding(value=val, type=LLM_MAP.get(t, "other")))
    return out


def detect(text: str, temperature: float = 0.0) -> list[Finding]:
    return _parse(_call(text, temperature=temperature))
