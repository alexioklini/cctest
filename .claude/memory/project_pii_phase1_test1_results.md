---
name: project_pii_phase1_test1_results
description: "PII-Phase-1 Test 1 (projektlose Attachments, Anon AN vs AUS, 3x3) ERGEBNIS — Anon verschlechtert NICHT; 0 echte Leaks; Δ = Modell-Varianz. Code 9.397.0"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7bddb1a3-74bf-48bf-afde-d5afe87900a5
  modified: 2026-07-22T15:01:04.923Z
---

**Test 1 — projektlose Attachment-Anonymisierung (Anon AN vs AUS), 3×3 je Cluster, Code v9.397.0** (2026-07-22). Nur die 2 Cluster mit noch vorhandenen Attachments; Rest braucht Re-Upload. Harness `scratchpad/pii_run.py`.

**ERGEBNIS: Anonymisierung verschlechtert die Ergebnisqualität NICHT.** Der AN-vs-AUS-Unterschied liegt in beiden Clustern KOMPLETT in der Modell-Stochastik (Streuung zwischen Läufen >> Anon-Effekt, [[feedback_eval_single_run_noise]]).

**Transaktion (mistral-medium, kein MoA):**
- AUS: 386 Zahlen Ø (Streuung 352–439). AN: 620 Zahlen Ø (Streuung 351–772). AN sogar vollständiger — reines Rauschen.
- Sicherheit: fake_leaks=0, deanon_errs=0, deanon_calls=0 über alle 3 AN. applied=1 (3 IDs). Echte IDs im Excel, Zahlen unberührt.

**Code of Conduct (glm-5.2, MoA aktiv, 4 PDFs, 58 Findings 35name/11addr/9date/2phone/1email):**
- AUS: 7030 chars, 13 Abschnitte Ø. AN: 11076 chars, 17.3 Abschnitte Ø. Punkte 5+6 in ALLEN 3 AN-Läufen vollständig (3/3). AN länger/strukturierter.
- Kennzahlen%: 4.0 (AUS) vs 3.7 (AN) — vernachlässigbar, in Varianz (AUS-Streuung selbst 0/0/12).
- applied=161, deanon_errs=0. MoA lief in allen (moa:true).

**fake_leaks=1 auf CoC-AN#2 = FALSCHER ALARM (kein echter Leak):** NER-FP `'Ag'→'Green'` (2-Zeichen-Token als name getaggt, conf 0.0), + Harness-Substring-Check matchte `'Green'` in `'Greenwashing'` (echtes Modell-Wort). Word-Boundary-Check (`\bGreen\b`) → 0 echte Leaks über ALLE 3 AN-Läufe. Zwei Mini-Findings (KEINE Datensicherheit): (1) NER sollte 2-Zeichen-Tokens nicht als Namen taggen ([[project_ner_fp_hardening_calibration]]); (2) Harness leak-check sollte Word-Boundary statt Substring nutzen (pii_run.py:176).

## KOMPLETT — alle 7 verfügbaren projektlosen Cluster getestet (2026-07-22)

Nach Attachment-Re-Upload alle 7 Cluster gefahren (3×3 AN/AUS). Cluster 3=Dublette von 4; Cluster 8 (orders.xlsx) nie vorhanden → entfällt. Alle Cloud-Modelle.

| # | Cluster | Modell | fake_leaks | deanon_errs | Inhalt/hard-fact |
|---|---|---|---|---|---|
| 1 | Transaktion→Excel | mistral-med | 0 | 0 | Zahlen echt, IDs anonymisiert |
| 2 | orders→Excel | mistral-med | 0 | 0 | Excel 216× echter Name "Pölzl", Modell sah nur Fake "Drew Brown"; 118 MO-Nrn+108 ISIN echt |
| 4 | Code of Conduct | glm+MoA | 0 | 0 | Punkt 5+6 = 3/3 |
| 5 | AML Statement | glm+MoA | 0 | 0 | 9/9 Pflichtthemen (AUS+AN), applied~223, 32 anonymise_read (Modell sah nur Fakes) |
| 6 | CSV Richtlinien→Excel | glm | 0 | 0 | 488 Zeilen, 2612 Zahlen Ø |
| 7 | Jahresabschluss (Scan-OCR) | glm | 0 | 0 | 71 Euro-Beträge, alle Kennzahlen EBIT/Umsatz/Eigenkapital×12 präsent |
| 9 | LEBC UBO-Recherche | auto-cloud | 0 | 0 | UBO-Report ECHTE Namen (Colin MacKay Northe, Christopher Stradli-Smith), applied~207, Fakes 0× |

**GESAMTURTEIL Test 1 (KOMPLETT): Anonymisierung verschlechtert die Ergebnisqualität NICHT — über ALLE 7 Cluster.** Durchgängig: fake_leaks=0, deanon_errs=0 (xlsx-Race strukturell weg, v9.397). Δ AN-vs-AUS = Modell-Stochastik. v9.397-Architektur end-to-end bewiesen (2 hard-facts orders+lebc): **Cloud-Modell sieht NUR Fakes, Nutzer bekommt echtes Artefakt mit echten Daten.** Stärkster Fall lebc (UBO=PII-intensivst, Namen=Kern): 207 Werte korrekt, 0 Leaks, AN inhaltlich ausführlicher.

**INFRA-LEHREN (nicht GDPR):** (1) MoA/glm + PARALLEL = HTTP 429 (Provider 'zai-coding' erschöpft — MoA×5 Sub-Calls unter Parallel-Last; aml+ja fielen aus, sequenziell dann fehlerfrei) → MoA-Cluster EINZELN fahren; Nicht-MoA parallel OK. (2) Harness-Chunking für Texte >200KB nötig (scan-text HTTP-413-Cap; AML-PDF 253KB fand sonst 0 Findings). (3) Word-Boundary-Leak-Check statt Substring (Green/Greenwashing-FP). (4) Cluster 7: Modell re-OCR't Scan-PDFs pro Turn via M4-GLM-OCR → ~800s/Lauf, sehr teuer.

**Empfehlung Mitarbeiter (Kern-Zusage Phase):** „zuerst anonymisieren probieren" ist für Attachments belegt tragfähig — kein Qualitätsverlust. Nächster Schritt: **projektbasierte policies-Eval** (Projekt-Mode + gemineten Input-Ordner + KG, Anon AN vs AUS) — wenn das hält, ist die Zusage auch für Projekt-Retrieval belegt.

SEPARAT (kein GDPR): mistral-medium transkribiert PDF-Tabellen manuell als Inline-CSV statt zu parsen → viele python_exec-Iterationen (war mit Alt-Code schlimmer).

Verwandt: [[project_pii_quality_test_plan]], [[project_gdpr_pii_in_llm_only_policy]].
