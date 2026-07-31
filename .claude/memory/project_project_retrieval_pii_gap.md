---
name: project_project_retrieval_pii_gap
description: "GELÖST v9.398.0 — Mid-Turn-PII-Dialog bei Projekt-Retrieval implementiert (Guard _gdpr_retrieval_guard in _gdpr_anon_tool_text, Batch-Dialog, seed_from_decision, Worker-Lokal-Restart, fail-closed für Background). OFFEN: E2E-Test im UI + policies-Eval mit --anonymise."
metadata: 
  node_type: memory
  type: project
  originSessionId: 7bddb1a3-74bf-48bf-afde-d5afe87900a5
  modified: 2026-07-22T19:52:00.969Z
---

**GELÖST in v9.398.0 (2026-07-22, Commit cfa706ac).** Ursprünglicher Befund (PII-Phase-1 Test 2):
Projekt-Retrieval-PII (mempalace_query/KG/Wiki) ging ROH ans Cloud-Chat-LLM — Drawer bewusst raw
(v9.96.0, Embedding-Stabilität), Result-Seam `_gdpr_anon_tool_text` apply-only, Projekt-Chat ohne
Seed → leeres Mapping → no-op.

**Implementierung (Plan PROJECT_RETRIEVAL_PII_DIALOG_PLAN.md, alle 4 Schritte):**
- `brain._gdpr_retrieval_guard(text, source, mapping)` — in `_gdpr_anon_tool_text` nach der
  Mapping-Auflösung, NUR für die 6 Retrieval-Quellen (`_RETRIEVAL_PII_SOURCES`), nur bei aktivem
  Mapping + nicht-lokalem Modell (`_retrieval_turn_model_is_local` über `ctx._current_model`).
- Frischer `_pii_scan_text` mit Production-FP-Gates; Kandidaten = Werte ohne JEDE Vorentscheidung
  (Mapping forward+reverse casefold, Ledger per `_latest_decisions_by_value`, asked-Set). Cap 40.
- EIN Batch-Dialog pro Turn (`_retrieval_pii_ask`, Blaupause `_web_consent_ask` aus
  `git show 525bd19e~1:brain.py`): pro Wert Anonymisieren/Falschtreffer + Turn-Frage
  fortfahren/lokal/abbrechen. Bestehende ask_user-Mechanik + Frage-Karte, null neue UI.
- Anonymisieren → `seed_from_decision` + pii_decisions-Zeile (STANDARD-Hash sha256(rule|value),
  fake_value, disposition=retrieval-dialog) + `gdpr_persist_mapping` → bestehende Apply-Sweeps
  faken sofort + sticky. FP → false_positive=1-Zeile, bleibt klar. Abbrechen →
  `session.cancel_token.cancel()` (normaler Stop-Pfad). Lokal → ctx-Flags
  `_retrieval_pii_local_switch`/`_retrieval_pii_all_local` + Cancel; Worker (chat.py direkt nach
  `run_turn`) poppt Flag, re-runt EINMAL auf `default_local_fallback_model` (Wire unverändert =
  Fakes bleiben konsistent, Deanonymiser aktiv — bewusste Abweichung vom Pre-Turn-Recovery-
  "ORIGINAL content"). Background/Timeout fail-closed (`retrieval_pii_withheld`-Refusal).
- Roundtrip verifiziert: args-deanon (L3a) übersetzt retrieval-geseedete Fakes in read_path zurück.
- Tests: `tests/test_gdpr_retrieval_dialog.py` (14, alle grün); GDPR-Familie ohne Regression
  (1 pre-existing test_pii_ner-NER-FP aus v9.397.0).

**E2E (§6.2) PASS 2026-07-22:** Dialog mid-turn (14 Werte+Turn-Frage), per API beantwortet,
applied=665, 14 Ledger-Zeilen, Antwort deanonymisiert ohne Fakes. Beobachtung: Kandidaten aus
OCR-PDF überwiegend NER-FPs (Systemnamen als „name"); Werte können JSON-Escapes überspannen
(`\n` literal) — Detektion+Apply gleiche Fläche, konsistent, aber FP-Rauschen.

**Test 2 (§6.3) TEIL-ERGEBNIS 2026-07-22** (Run `20260722T190418_disc-none_anon-retrieval-dialog-rep1`):
Eval-Harness beantwortet den Dialog jetzt auto bei `--anonymise` (Commit 1da05319, Erkennung
über Turn-Frage). **10 valide Fragen: Anon 0.853 vs Referenz 0.816 (Δ+0.037 = Noise) —
Retrieval-Anon verschlechtert NICHT** (konsistent mit Test 1). 28 Dialoge im Lauf (bis zu 9
pro Frage bei F1 — jede Retrieval-Welle mit neuen Werten fragt; UX-Konsolidierung offen).
F2/F3/C1/C2/C3 fielen zunächst auf HTTP 429 (zai-coding/glm-5.2 erschöpft trotz --parallel 1 —
MoA-Fan-out × 10 Fragen frisst die Quota); **Re-Run nach Nutzer-Umstellung auf k3** (Router-ID
`k3` = Kimi K3, in MoA-Pools ergänzt; Runs `…193428_anon-f2-retry-smoke` + `…193826_…rep1-rest`):
alle 5 ok auf model=k3 → F2 0.97, F3 0.5, C1 0.93, C2 0.9, C3 1.0 (vs glm-Referenz 0.652 —
konfundiert durch Modellwechsel, NICHT als Anon-Effekt lesen). Komplett-15 (gemischt glm/k3):
0.855 vs Referenz 0.76. Der Dialog+Auto-Answer lief auch auf k3 fehlerfrei (F2: 11 Dialoge!).
**ABGESCHLOSSEN per Nutzer-Entscheid 2026-07-22:** „das passt, wir haben genug Infos — PII geht
in Projekten und die Ergebnisse sind nicht schlechter." KEINE weiteren Reps. (Der k3-Paar-Versuch
scheiterte an erschöpfter kimi-coding-Quota — Dirs `…195751_k3-plain-rep1` + `…200326_k3-anon-rep1`
UNGÜLTIG, nie als Baseline verwenden. Merke: EIN 15-Fragen-MoA-Lauf frisst einen erheblichen Teil
eines Coding-Plan-Fensters — künftige Eval-Läufe gegen die Fenstergröße planen, Paare über Fenster
verteilen.) **Dialog-Konsolidierung UMGESETZT in v9.399.0** (Commit 28af3bb8, wegen [[project_pii_default_on_production]]):
(1) Turn-Vererbung — „Anonymisiert fortfahren" gilt für den Rest des Turns (`_retrieval_pii_turn_policy`
auf ctx, max. 1 Dialog/Turn); (2) Session-Standing-Order — Sticky-Frage im ersten Dialog persistiert
`sessions.retrieval_auto_anon` (manage-Action `retrieval_auto_anon` = Widerruf; Muster allow_further_web);
Background-Turns MIT Order seeden statt fail-closed refuse → geplante Tasks auf Anon-Projekten nutzbar.
Gemeinsamer Helper `_retrieval_seed_values` (Audit-choice dialog|turn_auto|session_auto). 20 Tests.
**Option 3 UMGESETZT in v9.400.0** (Commit a65386cd; Nutzer-Entscheide: gilt für ALLE Projekt-Nutzer,
neue Funde bleiben 'open', KEIN Admin-Default): `project_pii_decisions`-Ledger (Entscheidungen, nie
Fakes) + inkrementeller Korpus-Scan (`brain.project_pii_scan`, sha1-Cursor, Auto-Hook am Ende jedes
veränderten project-sync-Zyklus) + Bewertungs-UI im Projekt-Panel (owner/admin, „Datenschutz (PII)")
+ Guard-Konsultation VOR den Standing-Orders (anonymise=Lazy-Seed der RUNTIME-Form, fp=Klartext,
open=Dialog; Session schlägt Projekt). `_retrieval_pii_norm` überbrückt JSON-Escapes beidseitig.
Endpoints `…/pii-scan` + `…/pii-decisions` (require_manage). Live: KG-Real-Policies 62 Dateien →
371 offene Kandidaten (echte Namen + NER-FPs — VOR dem Produktions-Rollout kuratieren!).

VERWANDT (weiter offen): analoge Lücke Cloud-OCR (PII geht roh an Mistral-OCR, Typ-Check erst
NACH OCR). [[project_gdpr_pii_in_llm_only_policy]], [[project_pii_phase1_test1_results]],
[[project_pii_quality_test_plan]].
