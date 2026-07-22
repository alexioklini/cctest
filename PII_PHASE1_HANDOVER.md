# PII-Phase-1 Handover — Attachment-Anonymisierungs-Qualitätstests

**Stand:** 2026-07-22, Version 9.396.1 (deployt, committet, gepusht).
**Zweck:** In einer frischen Session die PII-Qualitätstests fortsetzen.

---

## 1. Übergeordnetes Ziel

Ermitteln, ob der **AKTUELLE Code** mit eingeschalteter Anonymisierung die Ergebnisqualität **wesentlich verschlechtert** (vs. ohne Anonymisierung). Wenn ja → lokaler Fallback. Endprodukt: Mitarbeiter-Rat **„zuerst anonymisieren, wenn das nicht geht → lokal"**.

Detail-Plan + Kontext: Memory `project_pii_quality_test_plan`.

**Reihenfolge:** (1) HEUTE Attachments · (2) Projekte mit ingested files (policies-Eval) · (3) Lokal-Fallback Qualität/Speed.

---

## 2. Was schon erledigt ist (Fixes VOR den Tests)

Die Tests deckten zwei echte Bugs auf, die ERST gefixt werden mussten (sonst misst man auf kaputtem Code):

- **v9.396.0 — Attachment-Persistenz:** Attachments lagen in `/tmp/brain-attachments/` (macOS löscht nach 3d). Jetzt `agents/main/attachments/<sid>/` (persistent). Memory `project_attachment_persistence_bug`.
- **v9.396.1 — Dateinamen-Manipulation komplett zurückgebaut** (ZWEI Rückbauten):
  - Artefakt-Rename+Symlink (v9.390.0) DEAKTIVIERT → beseitigt den File-Deanon-Race (`FilePseudonymizeError`, mehrfach-fire via python_exec mtime-Diff, gescheiterter Reverse verschluckt → Fake-tragende Datei konnte ausgeliefert werden). Memory `project_gdpr_xlsx_deanon_race`.
  - Attachment-Filename-Pseudonymisierung (v9.394.0) KOMPLETT ENTFERNT → Attachments behalten Originalnamen, verbatim ans LLM.

**WICHTIG:** Beide Fixes sind deployt (Server läuft 9.396.1). Der Transaktions-Anon-Lauf, der den Race zeigte, muss nun WIEDERHOLT werden — er sollte jetzt race-frei durchlaufen (kein mehrfaches deanonymise_file, kein FilePseudonymizeError).

---

## 3. Test-Methodik (final geklärt)

- **Gold = bestehende Anon-AUS-Sessions** (nur Vergleich, NICHT neu fahren):
  - Cluster 1 CoC: `013088664b03` (glm-5.2, **MoA aktiv**)
  - Cluster 2 Transaktion: `877840716a57` (mistral-medium-3.5, **kein MoA**)
- **Anon AN = FRISCH mit aktuellem Code fahren.** Alle ALTEN Anon-AN-Sessions (z.B. 3ba2cfa5) sind WERTLOS (alter Code).
- **MoA-Modus des Golds SPIEGELN** (Nutzer-Hinweis, kritisch!): CoC braucht `model="moa"`, Transaktion normal. Sonst Äpfel-mit-Birnen.
- **≥3 Läufe je Bedingung** (AN + AUS mit akt. Code), um Anon-Effekt von Modell-Stochastik zu trennen (Delta <0.05 = Rauschen; `feedback_eval_single_run_noise`).

### Anon-Mechanik (nicht-interaktiv, verifiziert)
`_gdpr_anon_tool_text` (Read-Seam) ist **APPLY-ONLY** — wendet nur ein aus bestätigten Findings geseedetes Mapping an, scannt NICHT selbst. Also MUSS der Anon-Lauf die Findings mitschicken:
1. Attachment-Text extrahieren: `brain.extract_attachment_text(path)` → `(text, kind)` (TUPLE!). Braucht Server-Prozess (NER geladen).
2. Findings enumerieren: `POST /v1/gdpr/scan-text {text, full:true}` (PRODUCTION-mode, NICHT raw_detection) → alle Werte. Der aktuelle Code filtert FP-Firmennamen selbst (org=ignore + min_occurrences): Transaktion→nur 4 echte IDs, CoC→58 Findings (35 name/11 address/9 date/2 phone/1 email).
3. `pii_decisions = [{rule_id, value, false_positive:false, action:"anonymise"}]` (distinkt).
4. Session anlegen: `POST /v1/sessions {model, agent:"main"}` → `{session_id}`.
5. Turn: `POST /v1/chat {session_id, message, model:("moa" wenn Gold-MoA sonst spec.model), files:[{name,content(b64),encoding:"base64"}], gdpr_action:"anonymise", pii_scan_done:true, pii_decisions}` (SSE).
6. Verifizieren dass MoA lief (COUNT moa_reference rows) + Anon lief (anonymise_read applied>0, Ledger).

### Bewertung
- Transaktion = harter Fakten-Check: Excel-Zahlen == PDF-Zahlen. Anon darf Namen/IDs faken, NIE Zahlen verfälschen.
- CoC = inhaltliche Vollständigkeit Punkte 5+6.

---

## 4. Verfügbare Test-Daten

NUR 2 von 9 Clustern haben ihre Input-Attachments noch (Rest /tmp-gelöscht):
- **Cluster 1 CoC** — `/tmp/brain-attachments/013088664b03/` (4 PDFs): LATCodeofConduct.pdf, Code_of_Conduct_DE_Leitfaden.pdf, 4_Konsolidierter-Corporate-Governance-Bericht-gem.-243c-UGB_2026-07-01-124946_ipup.pdf, JuliusBaer_Code-of-Ethics-and-Business-Conduct.pdf
- **Cluster 2 Transaktion** — `/tmp/brain-attachments/877840716a57/` (1 PDF): "Income Fees and Taxes Report for customer 704783 (01_01_2025 - 31_12_2025).pdf"

**ACHTUNG:** Diese liegen noch in /tmp (könnten bald vom macOS-Cleanup gelöscht werden!). Für dauerhafte Tests früh nach `agents/main/attachments/` kopieren, oder die Original-Queries + Modelle sind in `scratchpad/pii_run.py` hinterlegt.

Queries (verifiziert):
- CoC: "wir benötigen einen Code of Conduct (Verhaltenskodex) für die Wiener Privatbank. Siehe Beispiele von anderen vergleichbaren Banken. Im Konsolidierten Governance Bericht findest du ein Beispiel. Wir benötigen daraus explizit nur Punkte 5 und 6 als word Dokument ausgearbeitet."
- Transaktion: "bitte lies das pdf ein und mache ein excel aus der transaktions-historie"

---

## 5. Harness

`scratchpad/pii_run.py` (im session-scratchpad, NICHT committet). Aus dem Repo-Root laufen (`brain`-Import): `python3 -u scratchpad/pii_run.py <coc|tx> <reps>`.

**Bekannte Harness-Baustelle (FIXEN vor Weiterfahren):** `find_output_docx_or_xlsx` nimmt die ERSTE Datei alphabetisch — bei mehreren Ausgabedateien (Modell iteriert) die FALSCHE. Muss die vollständigste/größte wählen (meiste Zeilen/Zahlen).

Existierender PII-Eval-Harness: `eval/pii_eval/` (nutzt `{full:true}`-Escape-Hatch; `ours_adapter.py` als Vorlage).

---

## 6. Nächste Schritte (in frischer Session)

1. **Verifizieren dass der Race weg ist:** Cluster 2 Transaktion, 1 Anon-Lauf. Prüfen: kein `FilePseudonymizeError`, kein mehrfaches `deanonymise_file` pro Datei, finale xlsx korrekt (echte Werte, valide).
2. **Harness `find_output`-Fix.**
3. **≥3 Läufe AN + ≥3 AUS je Cluster** (CoC mit MoA!), objektive Metriken aggregieren (chars/Zahlen/Kennzahlen/Vollständigkeit/fake_leaks/applied). Mitteln → Anon-Effekt vs Stochastik trennen.
4. **Urteil:** verschlechtert Anon wesentlich? → dann Cluster-spezifisch (Judge oder Fakten-Check).
5. Danach Phase 2 (Projekt „Regelwerk der Bank", /Users/alexander/Documents/kg-real-policies/, KG aktiv).

**Aufräumen:** Test-Sessions IMMER sofort löschen (`DELETE /v1/sessions/<sid>`, `feedback_cleanup_test_sessions`). Login admin/admin.

---

## 7. Erstes (confounded) CoC-Ergebnis — zur Einordnung, NICHT als Signal werten

Ein CoC-Anon-Lauf (OHNE MoA, daher ungültig, gelöscht) war 57% kürzer als Gold + fehlende Kennzahlen. ABER: Gold lief MIT MoA, Anon ohne → der Unterschied war MoA, nicht Anon. Technisch war der Anon-Lauf sauber (0 Fake-Leaks, Deanon korrekt, Punkte 5+6 vollständig). Lehre: MoA spiegeln, ≥3 Läufe, `feedback_eval_single_run_noise`.
