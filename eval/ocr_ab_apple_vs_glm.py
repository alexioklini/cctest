#!/usr/bin/env python3
"""OCR-A/B: apple-vision-ocr (fm-agent :8007, Vision RecognizeDocuments) vs
GLM-OCR-8bit (oMLX :8000, Produktions-Prompt aus engine/mlx_ocr.py).

Entscheidungsfrage (AFM-Handover): kann Apple-Vision GLM-OCR als Default
ersetzen? Der bekannte harte Fall sind RANDLOSE Banktabellen
(project_pdf_table_extraction_fix) — deshalb:

  1. kontoauszug   — synthetisch, randlose Umsatztabelle (12 Zeilen), exakte
                     Ground-Truth: Beträge/IBAN/Daten + Zeilen-Paarung
                     (gehört der Betrag zur richtigen Buchung?).
  2. gebuehren     — synthetisch, Tabelle MIT Linien + Fließtext-Abschnitte.
  3. risiko_real   — Seite 1 der echten WPB_Risikoanalyse (docx → soffice-PDF
                     → PNG); Referenz = python-docx-Textphrasen (Präsenz-Check).

Alles LOKAL (beide Engines auf dem M4, Rendering hier) — keine Cloud, kein
PII-Risiko. Läuft ohne Server (direkt gegen :8007/:8000).

Run: python3 -u eval/ocr_ab_apple_vs_glm.py [--reps 2]
"""
import argparse
import base64
import difflib
import io
import json
import os
import re
import subprocess
import sys
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

M4 = "192.168.1.214"
APPLE_URL = f"http://{M4}:8007/v1/chat/completions"
GLM_URL = f"http://{M4}:8000/v1/chat/completions"
GLM_MODEL = "mlx-community/GLM-OCR-8bit"
GLM_KEY = "brain"
# Produktions-Prompt (engine/mlx_ocr.py DEFAULT_PROMPT)
GLM_PROMPT = (
    "Text Recognition: Transcribe ONLY text that is clearly and legibly visible "
    "in the image. Do NOT guess, do NOT complete partial words, do NOT invent "
    "names, dates or numbers. If a field is blurred, cut off or unreadable, "
    "omit it."
)
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ocr_ab_out")

REAL_DOCX = ("/Users/alexander/Documents/dev/cctest/agents/main/projects/"
             "risikoanalysen/instruction-files/WPB_Risikoanalyse_BRA_2025_v1.0.docx")


# ── Fonts (macOS-Systemfonts) ───────────────────────────────────────────────
def _font(size, bold=False):
    path = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf")
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# ── Dokument 1: randloser Kontoauszug (exakte Ground-Truth) ────────────────
KONTO_ROWS = [
    ("01.07.2026", "Gehalt Wiener Privatbank SE", "+3.412,86", "5.918,44"),
    ("02.07.2026", "Miete Objekt Graben 14", "-1.480,00", "4.438,44"),
    ("04.07.2026", "REWE Dankt 1122 Wien", "-87,63", "4.350,81"),
    ("07.07.2026", "SEPA-Lastschrift Wien Energie", "-214,90", "4.135,91"),
    ("09.07.2026", "Depotgebuehr Q2/2026", "-25,00", "4.110,91"),
    ("11.07.2026", "Ueberweisung M. Leitner", "-350,00", "3.760,91"),
    ("14.07.2026", "Apotheke Zum Engel", "-42,17", "3.718,74"),
    ("17.07.2026", "Gutschrift Steuererstattung", "+612,38", "4.331,12"),
    ("21.07.2026", "OEBB Ticketshop", "-119,80", "4.211,32"),
    ("24.07.2026", "Restaurant Steirereck", "-156,40", "4.054,92"),
    ("28.07.2026", "Sparplan MSCI World", "-500,00", "3.554,92"),
    ("31.07.2026", "Habenzinsen", "+1,84", "3.556,76"),
]
KONTO_IBAN = "AT61 1904 3002 3457 3201"


def render_kontoauszug(path):
    W, H = 1600, 1240
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 50), "Wiener Privatbank SE", font=_font(40, True), fill="black")
    d.text((60, 105), "Kontoauszug 07/2026", font=_font(30), fill="black")
    d.text((60, 150), f"IBAN {KONTO_IBAN}   BIC WIPBATWW", font=_font(26), fill="black")
    cols = [60, 260, 1090, 1360]
    d.text((cols[0], 230), "Datum", font=_font(26, True), fill="black")
    d.text((cols[1], 230), "Buchungstext", font=_font(26, True), fill="black")
    d.text((cols[2], 230), "Betrag EUR", font=_font(26, True), fill="black")
    d.text((cols[3], 230), "Saldo EUR", font=_font(26, True), fill="black")
    # BEWUSST: keine Linien, keine Schattierung — der randlose harte Fall.
    y = 290
    for dat, txt, btr, sld in KONTO_ROWS:
        d.text((cols[0], y), dat, font=_font(26), fill="black")
        d.text((cols[1], y), txt, font=_font(26), fill="black")
        d.text((cols[2], y), btr, font=_font(26), fill="black")
        d.text((cols[3], y), sld, font=_font(26), fill="black")
        y += 52
    d.text((60, y + 40), "Neuer Saldo per 31.07.2026: 3.556,76 EUR",
           font=_font(28, True), fill="black")
    img.save(path)


# ── Dokument 2: Gebührenaufstellung (Linien + Fließtext) ───────────────────
GEB_ROWS = [
    ("Depotfuehrung", "1", "25,00"),
    ("Orderspesen Inland", "3", "29,70"),
    ("Orderspesen Ausland", "2", "39,80"),
    ("Limitgebuehr", "5", "12,50"),
    ("Ausschuettungsgutschrift", "1", "0,00"),
]
GEB_INTRO = ("Sehr geehrter Kunde, nachstehend finden Sie die Aufstellung der "
             "im zweiten Quartal 2026 angefallenen Entgelte gemaess Preisverzeichnis.")


def render_gebuehren(path):
    W, H = 1600, 1000
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 50), "Entgeltaufstellung Q2/2026", font=_font(38, True), fill="black")
    # Fließtext (zwei Zeilen)
    d.text((60, 130), GEB_INTRO[:88], font=_font(25), fill="black")
    d.text((60, 168), GEB_INTRO[88:], font=_font(25), fill="black")
    cols = [60, 760, 1100]
    y0 = 260
    d.text((cols[0], y0), "Position", font=_font(26, True), fill="black")
    d.text((cols[1], y0), "Anzahl", font=_font(26, True), fill="black")
    d.text((cols[2], y0), "Summe EUR", font=_font(26, True), fill="black")
    d.line([(55, y0 + 42), (1400, y0 + 42)], fill="black", width=2)
    y = y0 + 60
    for pos, n, val in GEB_ROWS:
        d.text((cols[0], y), pos, font=_font(26), fill="black")
        d.text((cols[1], y), n, font=_font(26), fill="black")
        d.text((cols[2], y), val, font=_font(26), fill="black")
        d.line([(55, y + 44), (1400, y + 44)], fill="lightgray", width=1)
        y += 56
    d.line([(55, y + 4), (1400, y + 4)], fill="black", width=2)
    d.text((cols[0], y + 20), "Gesamt", font=_font(26, True), fill="black")
    d.text((cols[2], y + 20), "107,00", font=_font(26, True), fill="black")
    img.save(path)


# ── Dokument 3: echte Risikoanalyse-Seite ──────────────────────────────────
def render_real(path):
    """docx → PDF (soffice) → PNG Seite 1 (PyMuPDF; sips ist auf Beta 5 kaputt)."""
    import fitz
    pdf_dir = os.path.dirname(path)
    r = subprocess.run(["/opt/homebrew/bin/soffice", "--headless",
                        "--convert-to", "pdf", "--outdir", pdf_dir, REAL_DOCX],
                       capture_output=True, timeout=180)
    pdf = os.path.join(pdf_dir, os.path.splitext(os.path.basename(REAL_DOCX))[0] + ".pdf")
    if not os.path.exists(pdf):
        raise RuntimeError(f"soffice: {r.stderr.decode()[:200]}")
    doc = fitz.open(pdf)
    # Erste Seite mit nennenswertem Text (Deckblätter überspringen)
    page_no = 0
    for i in range(min(4, doc.page_count)):
        if len(doc[i].get_text().strip()) > 300:
            page_no = i
            break
    page = doc[page_no]
    zoom = 1600 / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    pix.save(path)
    ref_text = doc[page_no].get_text()
    doc.close()
    return ref_text


# ── Engine-Aufrufe ─────────────────────────────────────────────────────────
def _data_uri(path):
    return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()


def ocr_apple(path):
    body = {"model": "apple-vision-ocr", "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": _data_uri(path)}}]}]}
    t0 = time.time()
    r = httpx.post(APPLE_URL, json=body, timeout=300)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"], time.time() - t0


def ocr_glm(path):
    body = {"model": GLM_MODEL, "max_tokens": 4096, "temperature": 0, "stream": False,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": GLM_PROMPT},
                {"type": "image_url", "image_url": {"url": _data_uri(path)}}]}]}
    t0 = time.time()
    r = httpx.post(GLM_URL, json=body, timeout=600,
                   headers={"Authorization": f"Bearer {GLM_KEY}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"], time.time() - t0


# ── Metriken ───────────────────────────────────────────────────────────────
def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _squash(s):
    """Für Wert-Präsenz: alle Whitespaces raus (OCR bricht Zeilen anders um)."""
    return re.sub(r"\s+", "", s)


def score_konto(text):
    sq = _squash(text)
    vals = ([r[2] for r in KONTO_ROWS] + [r[3] for r in KONTO_ROWS]
            + [r[0] for r in KONTO_ROWS] + [_squash(KONTO_IBAN), "3.556,76"])
    present = sum(1 for v in vals if _squash(v).lstrip("+") in sq.replace("+", ""))
    # Zeilen-Paarung: Buchungstext-Kern und zugehöriger Betrag in DERSELBEN
    # Ausgabezeile (Markdown-Zeile oder Fließzeile)?
    paired = 0
    lines = [_squash(l) for l in text.splitlines() if l.strip()]
    for dat, txt, btr, _ in KONTO_ROWS:
        key = _squash(txt)[:12]
        hit = any(key in l and _squash(btr).lstrip("+") in l.replace("+", "") for l in lines)
        paired += hit
    return {"werte": f"{present}/{len(vals)}", "werte_frac": present / len(vals),
            "paarung": f"{paired}/{len(KONTO_ROWS)}", "paarung_frac": paired / len(KONTO_ROWS),
            "tabelle_md": ("|" in text and text.count("|") > 20)}


def score_gebuehren(text):
    sq = _squash(text)
    vals = [r[0] for r in GEB_ROWS] + [r[2] for r in GEB_ROWS] + ["107,00"]
    present = sum(1 for v in vals if _squash(v) in sq)
    intro = difflib.SequenceMatcher(None, _norm(GEB_INTRO).lower(),
                                    _norm(text).lower()).find_longest_match()
    return {"werte": f"{present}/{len(vals)}", "werte_frac": present / len(vals),
            "intro_lesbar": intro.size > 40,
            "tabelle_md": ("|" in text and text.count("|") > 10)}


def score_real(text, ref_text):
    # 12 markante Phrasen (>= 5 Wörter) aus der echten Seite → Präsenz-Quote
    words_lines = [_norm(l) for l in ref_text.splitlines() if len(_norm(l).split()) >= 5]
    phrases = words_lines[:12]
    sq = _squash(text).lower()
    present = sum(1 for p in phrases if _squash(p).lower() in sq)
    return {"phrasen": f"{present}/{len(phrases)}",
            "werte_frac": present / max(1, len(phrases))}


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    konto = os.path.join(OUT_DIR, "kontoauszug.png")
    geb = os.path.join(OUT_DIR, "gebuehren.png")
    real = os.path.join(OUT_DIR, "risiko_real.png")
    render_kontoauszug(konto)
    render_gebuehren(geb)
    ref_text = render_real(real)
    print(f"Dokumente gerendert → {OUT_DIR}", flush=True)

    docs = [("kontoauszug", konto, score_konto),
            ("gebuehren", geb, score_gebuehren),
            ("risiko_real", real, lambda t: score_real(t, ref_text))]
    engines = [("apple", ocr_apple), ("glm", ocr_glm)]

    results = {}
    for dname, dpath, scorer in docs:
        for ename, fn in engines:
            times, texts = [], []
            for rep in range(args.reps):
                try:
                    text, dt = fn(dpath)
                except Exception as e:
                    print(f"  {dname}/{ename} rep{rep}: FEHLER {e}", flush=True)
                    text, dt = "", float("nan")
                times.append(dt)
                texts.append(text)
                print(f"  {dname}/{ename} rep{rep}: {dt:.1f}s, {len(text)} Zeichen", flush=True)
            best = max(texts, key=len)
            sc = scorer(best) if best else {"werte_frac": 0}
            results[(dname, ename)] = {"times": times, "score": sc, "chars": len(best)}
            with open(os.path.join(OUT_DIR, f"{dname}_{ename}.txt"), "w") as f:
                f.write(best)
            print(f"  {dname}/{ename}: {json.dumps(sc, ensure_ascii=False)}", flush=True)

    print("\n=== ZUSAMMENFASSUNG ===", flush=True)
    print(f"{'Dokument':14} {'Engine':6} {'warm s':>7} {'Score':>40}")
    for (dname, ename), r in results.items():
        warm = min(r["times"])
        sc = r["score"]
        keys = [f"{k}={v}" for k, v in sc.items() if not k.endswith("_frac")]
        print(f"{dname:14} {ename:6} {warm:7.1f} {' '.join(keys):>40}")
    with open(os.path.join(OUT_DIR, "results.json"), "w") as f:
        json.dump({f"{d}/{e}": r for (d, e), r in results.items()}, f,
                  ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
