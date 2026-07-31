---
name: project_pii_quality_test_plan
description: Test-Fahrplan PII-Qualität — Anonymisierung darf Ergebnis nicht wesentlich verschlechtern; Reihenfolge Attachments→Projekte→Lokal-Fallback
metadata: 
  node_type: memory
  type: project
  originSessionId: 2a5d4fdd-4b90-4a02-922f-e7cdc4d16758
  modified: 2026-07-22T07:14:54.585Z
---

**Ziel der ganzen Phase:** Mit eingeschalteter Anonymisierung dürfen die Ergebnisse **nicht wesentlich schlechter** sein als ohne. Wenn Anonymisierung nicht ausreicht, ist LOKAL der Fallback — und Mitarbeiter sollen einen klaren Rat bekommen: **zuerst anonymisieren probieren, wenn das nicht geht → lokal als Fallback.**

Reihenfolge (festgelegt 2026-07-22):

1. **HEUTE — Attachments.** Ziel präzisiert: ermitteln ob der **AKTUELLE** Code mit Anonymisierung das Ergebnis verschlechtert. **ALLE bisherigen Anon-AN-Sessions (z.B. 3ba2cfa5) sind WERTLOS** — liefen mit altem Code (vor 9.383/9.393/9.394). Anon-AN muss FRISCH mit aktuellem Code gefahren werden.

   **9 Cluster identifiziert** (aus 67 non-project Analyse-Chats mit Dok-Attachment+Artefakt, dedupliziert). NUR 2 haben Input-Attachments noch (Rest /tmp-gelöscht, siehe [[project_attachment_persistence_bug]] — jetzt gefixt, künftige Uploads persistent):
   - **Cluster 1 CoC**: Gold=013088664b (glm-5.2, Anon AUS), 4 PDFs (LATCodeofConduct, Code_of_Conduct_DE_Leitfaden, 4_Konsolidierter-Corporate-Governance-Bericht, JuliusBaer_Code-of-Ethics). Query: "Code of Conduct für Wiener Privatbank, Punkte 5+6 als Word".
   - **Cluster 2 Transaktion**: Gold=877840716a (mistral-medium-3.5, Anon AUS), 1 PDF (Income Fees and Taxes Report customer 704783). Query: "lies pdf, mach excel aus transaktions-historie".

   **Mechanik-Erkenntnis:** `_gdpr_anon_tool_text` (Read-Seam) ist **APPLY-ONLY** (decision-driven seit v9.383) — scannt NICHT selbst, wendet nur das aus bestätigten Dialog-Findings geseedete Mapping an. Bei `pii_scan_done=True` OHNE `pii_decisions` → leeres Mapping → KEINE Attachment-Anonymisierung (Testlauf-Beweis: findings:0, Ledger leer). Also MUSS der Anon-Lauf ALLE Findings enumerieren (`_pii_scan_text` auf Attachment-Text, alle Werte) und als `pii_decisions[{action:anonymise}]` mitschicken (= Nutzer bestätigt alles im Dialog).

   **DREI Läufe pro Cluster** (User-Entscheid 2026-07-22): Gold (aus) · Anon-alle (worst: inkl. FP-Firmennamen wie Repsol/Allianz/Corporate Governance) · Anon-nur-PII (best: FP-Orgs als false_positive markiert). Zeigt ob Anon SELBST oder die FPs die Qualität drücken.

   **Bewertung:** Transaktion = harter Fakten-Check (Excel-Zahlen == PDF-Zahlen; Anon darf Namen faken, NIE Zahlen). CoC = inhaltliche Vollständigkeit Punkte 5+6. Methode final entschieden wenn Ergebnisse da.

   **KORREKTUR nach Test (2026-07-22): Anon-alle vs -nur-PII VERWORFEN.** Der aktuelle Production-Code filtert FP-Firmennamen selbst (org=ignore + min_occurrences): Transaktions-PDF → nur 4 echte IDs anonymisiert (keine Namen!); CoC-PDFs → 58 Findings (35 name/11 address/9 date/2 phone/1 email, echte Vorstände). Also EIN Production-Anon-Lauf/Cluster (User-Entscheid), Findings via `/v1/gdpr/scan-text {full:true}` PRODUCTION-mode (nicht raw_detection). Enumeration braucht Server-Prozess (NER geladen); brain.extract_attachment_text(path)→(text,kind) TUPLE.

   **ERSTES CoC-ERGEBNIS (1 Lauf, 09d587fb):** Anon technisch SAUBER — 0 Fake-Leaks im finalen docx, Deanon korrekt, Punkte 5+6 strukturell vollständig (Anon sogar mehr Unterstruktur). ABER Anon-docx 57% kürzer (7.5k vs 17.3k) + fehlende Diversitäts-Kennzahlen (44%/49% Frauenanteil). KRITISCH: die fehlenden Zahlen sind PROZENTE (keine PII, von Anon UNBERÜHRT) → vermutlich glm-5.2-STOCHASTIK, NICHT Anon-Schaden. BEWEIS nötig (User-Entscheid): ≥3 Läufe je Bedingung (AN+AUS mit akt. Code), messen ob Kennzahl-Verlust an Anon oder Modell-Varianz liegt ([[feedback_eval_single_run_noise]]). Harness erweitert auf reps + Metriken (chars/kennzahlen/abschnitte/punkt5+6/fake_leaks/applied). Läuft.

   **API-Mechanik verifiziert:** POST /v1/sessions {model,agent} → session_id; dann POST /v1/chat {session_id, message, model, files:[{name,content(b64),encoding}], gdpr_action:"anonymise", pii_decisions:[...], pii_scan_done:true} (SSE). Scan: POST /v1/attachments/scan {name,content,encoding} OHNE session_id (nutzt _scan-Scratch) → groups/finding_count (nur SAMPLES, für alle Werte _pii_scan_text direkt). Harness: scratchpad/pii_run.py. Akzeptanz: AN ≈ AUS. Delta <0.05 = Rauschen ([[feedback_eval_single_run_noise]], ≥3 Reps). python3 -u ([[feedback_evals_line_buffered]]).

2. **AB MORGEN — PII in Projekten mit ingested files/folders** (z.B. `policies`-Eval). Projekt-Mode mit gemineten Input-Ordnern + KG, Anonymisierung AN vs AUS. **Wenn das funktioniert, sind wir durch** (= Kern-Zusage 'anonymisiert = nicht schlechter' gilt auch für Projekt-Retrieval). NB: Chat-Attachments werden NICHT in MemPalace gemint ([[feedback_chat_attachments_no_mempalace]]), Projekt-Input-Ordner SCHON (project-sync-Daemon).

3. **DANACH — Lokal-Umstellung statt Anonymisierung.** Wenn User auf lokales Modell umstellt (kein Egress, keine Anonymisierung nötig): **Grenzen ausloten bzgl. Qualität UND Geschwindigkeit.** Ergebnis = Handlungsempfehlung für Mitarbeiter (der Fallback-Rat oben). Lokale Modelle: oMLX (gemma-26b > Qwen [[project_local_model_tool_quality]]), Grounding-Fälle ok ([[project_m4_7b_bg_usecases_verified]]); guided execution lokal kaputt ([[project_guided_execution_broken_local.md]]).

KONTEXT-Vorwissen aus dem Wien-Befund (2026-07-22): Org-Regel steht in dieser Instanz auf `rule_overrides.organisation='ignore'` — Firmennamen (Wiener Privatbank SE) werden NICHT anonymisiert, nur Personen/Adressen/Daten/etc. Das ist bewusst (Org-NER = viele FPs, [[project_pii_parity_wave2_m4_m5]]). Bei den Qualitäts-Tests bedenken: das Analyse-SUBJEKT (die Firma) reist im Klartext — die Anonymisierung schützt Personen im Dokument, nicht den Firmennamen. Für strengere Fälle: per-Projekt `gdpr_preset='kyc'` verstärkt name/org.

VERWANDT: [[project_transparent_anonymisation_complete]], [[project_pii_parity_l_progress]], [[project_kg_gdpr_anonymise_incident]] (LESSON: reproduce before assert), [[backlog_retrieval_eval_harness]].
