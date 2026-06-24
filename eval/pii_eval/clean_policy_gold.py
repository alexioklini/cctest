#!/usr/bin/env python3
"""Deterministically clean machine-prelabelled policy gold.

The M4 pre-labeller (build_policy_gold.py) is unreliable on two categories for
this corpus, verified by inspection 2026-06-23:
  * `secret` — M4 reads it SEMANTICALLY (confidentiality concepts) not as
    credentials: every secret-tagged item was a legal/concept term
    ("Bankgeheimnis", "DSGVO", "§ 11 UWG", "Data Owner", ...). ZERO real secrets.
  * `date` — includes durations ("7 Monate", "3 Jahre") and template
    placeholders ("Wien, am XX.YY.2025") that aren't concrete dates.
  * `network` — M4 tagged a username ("dg_itsupport") as network.

Leaving this garbage in gold would punish every detector with bogus FNs and
make the policy precision number meaningless. We keep only what M4 labels
RELIABLY on this corpus (name/email/phone/address/organisation) plus
deterministically-validated date/network/secret:
  * date: must match a real date shape (dd.mm.yyyy / mm/yyyy / yyyy).
  * network: must be a dotted-quad IP.
  * secret: must look credential-shaped (>=16 chars AND has digit+letter mix,
    or a known key prefix) — drops all the concept-words.

Writes data/policy_gold.clean.jsonl. The runner reads the clean file if present.
This is a transparent, inspectable filter — NOT a second LLM pass.
"""
from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "policy_gold.jsonl")
OUT = os.path.join(HERE, "data", "policy_gold.clean.jsonl")

_DATE_RE = re.compile(r"^(?:\d{1,2}\.\d{1,2}\.\d{2,4}|\d{1,2}/\d{4}|\d{4})$")
_IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_KEY_PREFIX = re.compile(r"(sk-|AKIA|ghp_|xox|eyJ|-----BEGIN|AIza)", re.I)


def _keep(value: str, vtype: str) -> bool:
    v = value.strip()
    if vtype in {"name", "email", "phone", "address", "organisation"}:
        return True  # M4 reliable here on this corpus
    if vtype == "date":
        return bool(_DATE_RE.match(v))
    if vtype == "network":
        return bool(_IP_RE.match(v))
    if vtype == "secret":
        if _KEY_PREFIX.search(v):
            return True
        return len(v) >= 16 and bool(re.search(r"\d", v)) and bool(re.search(r"[A-Za-z]", v))
    if vtype in {"iban", "credit_card", "national_id"}:
        return True
    return False


def main():
    rows = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    kept_items = dropped_items = 0
    out = []
    for r in rows:
        clean = []
        for g in r["gold"]:
            if _keep(g["value"], g["type"]):
                clean.append(g)
                kept_items += 1
            else:
                dropped_items += 1
        out.append({**r, "gold": clean})
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_pos = sum(1 for r in out if r["gold"])
    print(f"[clean] kept {kept_items} gold items, dropped {dropped_items}")
    print(f"[clean] {n_pos}/{len(out)} windows now have PII -> {OUT}")
    from collections import Counter
    c = Counter(g["type"] for r in out for g in r["gold"])
    print(f"[clean] by type: {dict(c)}")


if __name__ == "__main__":
    main()
