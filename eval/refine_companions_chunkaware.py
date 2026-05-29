#!/usr/bin/env python3
"""One-shot experiment helper (NOT product code).

Refines a list of `.brain-extracted/*.md` companion files using Brain's
configured background model via the RUNNING server's /v1/chat API (a throwaway
non-project session), preserving the original frontmatter so convert_folder's
(mtime,size) gate keeps the refined body.

Backs up each original to `<file>.orig` (skips re-backup if one exists).

Usage:
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/refine_companions.py
  python3 eval/refine_companions.py --restore
"""
import json
import os
import shutil
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8420"
ROOT = "/Users/alexander/Documents/kg-real-policies"
# Chunk-aware variant: only the gap winner (C3) + gap loser (P2) from the
# naive run, to test whether chunk-aware sectioning removes the P2 regression.
FILES = [
    f"{ROOT}/4 IT  & Core Banking/.brain-extracted/4.4_Archivierung & Datensicherung/ARL_4_4_Archivierung und Datensicherung.pdf.md",
    f"{ROOT}/20 Datenschutz & Informationssicherheit/.brain-extracted/20_2 Informationssicherheit/20_2_1_3_ARL_Ziele der Informationssicherheit.pdf.md",
]

# Chunk-aware: the mempalace miner slices at ~800 chars, preferring a \n\n
# boundary only in the 2nd half of the window (400-800). So every self-contained
# knowledge unit must be its own \n\n-separated section UNDER ~700 chars, with
# enough standalone context to be answerable alone. That way each chunk cut lands
# on a section edge instead of mid-rule (the measured P2 failure: the retention
# periods got split across two chunks).
INSTRUCTION = (
    "Du bereitest ein aus einem PDF konvertiertes Bank-Richtliniendokument "
    "(Markdown) für ein Retrieval-System mit FESTER Chunk-Größe von ~800 Zeichen "
    "auf. Gib NUR das aufbereitete Markdown zurück — keine Vorrede, kein "
    "Code-Fence, kein Kommentar.\n\n"
    "REGELN:\n"
    "1. Entferne reinen Verwaltungs-/Formular-Lärm: Antragstabellen an Vorstand, "
    "Änderungs-Checkboxen, 'Seite X von Y', wiederholte 'Dokumentenklassifizierung: "
    "intern' / 'Verantwortlicher: CISO' Kopf-/Fußzeilen, Versionshistorie-Tabellen, "
    "Status-Kästchen.\n"
    "2. BEHALTE jeden inhaltlichen Satz wörtlich oder sinngleich — erfinde NICHTS, "
    "ergänze KEINE Inhalte, ziehe KEINE Schlüsse. Übernimm Zahlen/Fristen/Regeln exakt.\n"
    "3. CHUNK-OPTIMIERUNG (am wichtigsten): Gliedere den Inhalt in in sich "
    "geschlossene Wissens-Einheiten. Jede Einheit ist ein durch eine LEERZEILE "
    "(\\n\\n) abgetrennter Block und MUSS UNTER 700 ZEICHEN bleiben. "
    "Zusammengehörige Fakten (z. B. eine vollständige Liste von Fristen, alle "
    "Regeln zu einem Thema) MÜSSEN in DEMSELBEN Block stehen — niemals über zwei "
    "Blöcke verteilt. Ist ein Thema zu groß für 700 Zeichen, teile es in mehrere "
    "Blöcke, von denen JEDER für sich allein verständlich ist (wiederhole bei "
    "Bedarf das Bezugswort, damit ein Block nicht mit 'gilt für 7 Jahre' ohne "
    "Bezug beginnt).\n"
    "4. Beginne jeden Block mit einer kurzen ## Überschrift, die sein Thema nennt, "
    "damit er eigenständig auffindbar ist. Behalte den Dokumenttitel als # Überschrift.\n\n"
    "HIER DAS DOKUMENT:\n\n"
)


def _post(path, body, token=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def _login():
    u = os.environ.get("BRAIN_USER", "admin")
    p = os.environ.get("BRAIN_PASS", "admin")
    _, resp = _post("/v1/auth/login", {"username": u, "password": p})
    return resp["token"]


def _new_session(token):
    _, resp = _post("/v1/sessions", {"agent": "main"}, token)
    return resp["session_id"]


def _chat(token, sid, message, timeout=600.0):
    req = urllib.request.Request(BASE + "/v1/chat",
                                 data=json.dumps({"session_id": sid,
                                                  "message": message}).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "text/event-stream")
    final, errors, ev, buf, start = {}, [], None, [], time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            if time.time() - start > timeout:
                raise TimeoutError("chat timeout")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith(":"):
                continue
            if line == "":
                if ev and buf:
                    try:
                        p = json.loads("\n".join(buf))
                    except Exception:
                        p = {"_raw": "\n".join(buf)}
                    if ev == "done":
                        final = p
                        break
                    if ev == "error":
                        errors.append(p.get("message", str(p)))
                ev, buf = None, []
                continue
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                buf.append(line[6:])
    return final, errors


def _split_frontmatter(text):
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (lines[i].lstrip().startswith("<!--") or lines[i].strip() == ""):
        i += 1
    return "".join(lines[:i]), "".join(lines[i:])


def _chunk_body(body, target=7000):
    """Split raw body into chunks <= ~target chars on paragraph boundaries.
    Refining each chunk independently avoids the single-shot truncation seen
    on large docs (28KB Lieferanten cut off mid-document)."""
    if len(body) <= target:
        return [body]
    paras = body.split("\n\n")
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > target:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def restore():
    n = 0
    for f in FILES:
        orig = f + ".orig"
        if os.path.isfile(orig):
            shutil.copyfile(orig, f)
            n += 1
            print(f"restored: {os.path.basename(f)}")
        else:
            print(f"no .orig: {os.path.basename(f)}")
    print(f"\n{n}/{len(FILES)} restored.")


def refine():
    token = _login()
    print("logged in.\n")
    for f in FILES:
        if not os.path.isfile(f):
            print(f"MISSING: {f}")
            continue
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        fm, body = _split_frontmatter(text)
        orig = f + ".orig"
        if not os.path.isfile(orig):
            shutil.copyfile(f, orig)

        chunks = _chunk_body(body)
        parts, failed = [], False
        for idx, ch in enumerate(chunks):
            sid = _new_session(token)
            final, errors = _chat(token, sid, INSTRUCTION + ch)
            r = (final.get("reply") or final.get("text") or "").strip()
            if errors and not r:
                print(f"ERROR {os.path.basename(f)} chunk {idx+1}/{len(chunks)}: {errors}")
                failed = True
                break
            if r.startswith("```"):
                r = r.split("\n", 1)[-1]
                if r.rstrip().endswith("```"):
                    r = r.rstrip()[:-3].rstrip()
            # Truncation guard: refined chunk should not be wildly shorter than
            # input (refinement trims noise ~40-65%, not 85%+). A chunk ending on
            # an incomplete table row is the tell-tale of a cut-off generation.
            ratio = len(r) / max(1, len(ch))
            tail = r.rstrip().splitlines()[-1] if r.strip() else ""
            looks_cut = tail.startswith("|") and tail.count("|") < 2
            if len(r) < 100 or ratio < 0.15 or looks_cut:
                print(f"SUSPECT chunk {idx+1}/{len(chunks)} {os.path.basename(f)}: "
                      f"{len(ch)}c->{len(r)}c ratio={ratio:.2f} cut={looks_cut}")
                failed = True
                break
            parts.append(r)
        if failed:
            print(f"  -> {os.path.basename(f)} NOT written (chunk failure)")
            continue
        refined = "\n\n".join(parts)
        out = fm + (refined if refined.endswith("\n") else refined + "\n")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"refined: {os.path.basename(f)}  ({len(body)}c -> {len(refined)}c, "
              f"{len(chunks)} chunk(s))")


if __name__ == "__main__":
    if "--restore" in sys.argv:
        restore()
    else:
        refine()
