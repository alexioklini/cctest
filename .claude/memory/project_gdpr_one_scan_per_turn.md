---
name: project_gdpr_one_scan_per_turn
description: v9.393.0 — gdpr_turn_decided-Marker; Background-Seam apply-only in entschiedenen Turns; Anlass Chat 6ba13da5 (WP/Julius-Baer-Mint via moa_planner); Attachment-Scan-Lücke OFFEN
metadata: 
  node_type: memory
  type: project
  originSessionId: c6e709c4-57da-4ab6-ad27-2a3b80e661ae
  modified: 2026-07-21T16:59:27.362Z
---

**Invariante (User-Vorgabe, 2026-07-21): PII-Erkennung läuft GENAU EINMAL pro Turn — vor dem Dialog. Kein Code-Teil darf danach neu scannen/minten.**

Anlass Chat 6ba13da5: `gdpr_pick_model_for_background` → `_anonymise_background_samples` scannte auf jedem Background-Call frisch und mintete entscheidungs-blind ins Session-Mapping. Der **moa_planner**-Call (Transkript + 5 Drafts) fand "Wiener Privatbank" (im getippten Text — den der Typed-Text-Scan NICHT geflaggt hatte: gleicher `_pii_scan_text`, anderer Textkontext!) und "Julius Baer" (aus den Drafts) → als Personen geseedet ("Alex Allen"/"Tristan Wright"), Read-Seams ersetzten sie überall, Planner plante wörtlich für "Alex Allen". Beweiskette: `mapping.sources=['background:moa_planner']`, Audit `pii_detected 10 → pii_anonymised`, Varianten-Zählung (85+20 Seeds+18 Fuzzy=123 forward).

**Fix v9.393.0** (Commit afed71ed): `RequestContext.gdpr_turn_decided` — Worker stampt es aus `body.pii_scan_done` (Client setzt es nach `runCancellableGdprScan`, AUCH bei 0 Findings) ∨ Body-Decision-Set ∨ Sticky-Anonymise. Vererbung an Sub-Turns via `build_tool_context`-Key → `_apply_bg_context`. Im Seam: Marker gesetzt → `_apply_decided_mapping_background` (apply-only: apply_entity_variants + apply_known_values; Mapping via ctx-ID, sonst letztes Session-Mapping — moa läuft VOR run_turns ctx-Bind!); kein Mapping → Klartext-Passthrough; unladbare Mapping-ID → Local-Swap/Block (nie still Klartext). Ohne Marker (Scheduler/Daemons/Telegram/TUI — kein Dialog möglich) bleibt das Scan+Policy-Netz. ARL-Klassifikations-Gate läuft bewusst weiter (detection-only, mintet nie). Tote Frisch-Scan-Pfade GELÖSCHT: `_pseudonymize_history_for_wire`, `pseudonymize_with_scanner`.

**Why:** Wiederholtes Muster ("irgendein Code-Teil meint, er muss neu scannen") — v9.383 hatte Worker+Read-Seams umgestellt, den Background-Seam vergessen.

**How to apply:** Jeder neue LLM-Call-Pfad, der Session-Inhalte trägt, MUSS durch `gdpr_pick_model_for_background` UND darf bei `gdpr_turn_decided` nur apply-only arbeiten. Nie `_pii_scan_text`+`pseudonymize_text` gegen ein Session-Mapping außerhalb des Pre-Dialog-Pfads (erlaubte Detection-only-Nutzer: Egress-Gates, Klassifikation, History-Badges, Ledger-Nachtrag, Data-Review). Tests: `tests/test_gdpr_decision_driven.py::TestBackgroundSeamDecisionDriven`.

**Anschluss v9.393.2/.3 (Chat 3ba2cfa5):** Englische Org-/Fachbegriffe ("Corporate Governance", "Risk Management", "Compliance Officer") wurden als rule_id=name anonymisiert. Ursache: bei EN-dominantem Dok ist en_core_web_md das TRUSTED Model, der v9.349 strict-span-Gate läuft nur untrusted. Fix `engine.pii_ner._is_generic_org_phrase`: Mehrwort-name-Span, deren JEDES Großschreib-Token generisch ist, wird gedroppt (BEIDE Emissionspfade — Haupt-Loop UND Recall-Netz-append, sonst re-introduziert der de-Recall-Lauf die Begriffe). Ein echter Namensanteil schützt. Eval 4 Docs: 15 FPs weg, 0/17 echte Namen verloren. name_precision_gate war der FALSCHE Hebel (ließ FPs durch, verlor "Herr Briker"/"Herr Zehenter"). **v9.393.3: config-only** — Liste ist `gdpr_scanner.name_generic_terms` (SINGLE runtime source `_GENERIC_PHRASE_TERMS`), leer=Filter aus, kein Hardcode-Fallback; 138 Defaults nur als Seed `_GENERIC_PHRASE_SEED` (nur wenn Config-Key FEHLT; vorhandenes `[]` gilt verbatim — kein `or []`). GUI Chip-Manager (Suche/Add/Remove) Settings→DSGVO; `/v1/services` muss den Key mitliefern (admin_artifacts baut gdpr_scanner feldweise!). GEPUSHT.

**OFFEN (Problem 2 aus 6ba13da5):** Client scannte nur 1 von 4 Anhängen pre-send (att_01 ok 16:29:22; att_02 hochgeladen aber Ergebnis verloren; att_03/04 nie submitted) — Loop in `panels_gdpr.js` ist fail-open, Dialog zeigt still "keine Findings" für ungescannte Dateien. Server-Scans nachweislich ok (12/48/7/42 Findings reproduziert). Client-Trigger ungeklärt (Browser-Repro nötig); Fix-Idee: fail-loud pro Datei im Dialog ("⚠️ nicht geprüft") oder Send-Block bis alle Scans durch. Siehe [[project_gdpr_all_checks_pre_dialog_plan]], [[project_gdpr_tool_deanon_display]].
