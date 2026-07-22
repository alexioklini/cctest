# Plan: Mid-Turn PII-Dialog bei Projekt-Retrieval

**Stand:** 2026-07-22. **Status:** ✅ ABGESCHLOSSEN in v9.398.0 (Schritte 1-4 + 14 Unit-Tests
`tests/test_gdpr_retrieval_dialog.py`; E2E §6.2 PASS — Dialog mid-turn, applied=665, Antwort
deanonymisiert; Eval §6.3: 10 Fragen gleicher Executor Anon 0.853 vs 0.816 = **verschlechtert
nicht**, Nutzer-Abnahme 2026-07-22: „PII geht in Projekten, Ergebnisse nicht schlechter").
**Anlass:** PII-Phase-1 Test 2 (projektbasierte policies-Eval) deckte auf, dass **Projekt-Retrieval-PII ungeschützt ans Cloud-Chat-LLM geht.**

---

## 1. Der Befund (warum dieser Plan existiert)

Bei einem **Projekt-Chat** (KG-Real-Policies o.ä.) fließt PII aus den gemineten Dokumenten
über `mempalace_query` / KG-Tools **roh ans Cloud-Chat-LLM** — auch mit `gdpr_action=anonymise`.

**Belegt (2026-07-22):** Frage, die die „Systemverantwortliche"-Liste (23 Namen) abruft,
mit `gdpr_action=anonymise` → `applied:0`, `anonymise_read:0`. Nichts anonymisiert.

### Warum (Code-Analyse)
| Schicht | Verhalten | Quelle |
|---|---|---|
| **Minen → MemPalace-Drawer** | RAW gespeichert (bewusst) | v9.96.0: *"storing anonymised drawers would change retrieval and was explicitly declined"* |
| **Minen → KG-Extrakt** | schützt nur den Weg zum *Extraktions-LLM* (`gdpr_pick_model_for_background`) — nicht den Speicher | `engine/kg_extract.py:670` |
| **Retrieval → Result-Seam** | `_gdpr_anon_tool_text` fakt auf `mempalace_query` (seit v9.336), ABER **apply-only** (keine frische Detektion) | `brain.py:3336`, Docstring §C |
| **Retrieval-Seam braucht** | ein Mapping mit den konkreten Werten | seeded nur durch Attachment-Scan / pii_decisions |
| **Projekt-Chat hat** | KEIN Attachment, KEINE pii_decisions → **leeres Mapping** | → Seam no-op → RAW ans LLM |

**Kernpunkt:** Die Retrieval-Anon-Architektur ist zu 90% fertig (Drawer roh = Retrieval intakt;
Result-Seam fakt schon). Es fehlt nur der **SEED** — die konkreten Werte im Session-Mapping.

### Warum Drawer roh bleiben MÜSSEN (nicht verhandelbar)
Retrieval ist **Vektor-Ähnlichkeit** (embeddinggemma). Query „Wer ist verantwortlich für System X"
matcht gegen Drawer-Embeddings mit **echten** Namen. Anonymisiert man die gespeicherten Drawer,
ändert sich das Embedding → Query matcht evtl. nicht mehr. **Deshalb:** roh speichern, erst das
Retrieval-ERGEBNIS faken (vor dem LLM). Das ist die existierende v9.336-Architektur.

---

## 2. Nutzer-Entscheidung (2026-07-22): der gewählte Ansatz

**Option A + blockierender Dialog.** Der Result-Seam soll bei Projekt-Retrieval **frisch scannen**,
aber statt automatisch zu faken, den Turn **anhalten und den Nutzer fragen** — wie der Composer-
Send-Dialog, nur MITTEN im Turn. Nutzer entscheidet: **abbrechen / lokal / anonymisieren** (mit FP-Markierung).

**Design-Entscheidungen (Nutzer bestätigt):**
1. **Ein Batch-Dialog pro Turn** (nicht pro Wert) — alle neuen PII-Werte in EINEM `AskUserQuestion`-Batch.
   Genau das Muster, das der (gelöschte) Web-Egress-Consent v9.338.0 nutzte.
2. **Bestehende FP-Gates vorschalten** (`org=ignore`, Konfidenz-Bänder, `min_occurrences`) — nur
   echte Personen-PII über der Schwelle löst den Dialog aus. System-/Rollennamen (NER-FPs wie
   „CRS Suite", „ÖNB Portal") fallen raus, der Dialog flutet nicht.

---

## 3. Vorhandene Infrastruktur (WIEDERVERWENDEN, nicht neu bauen)

| Baustein | Wo | Zustand |
|---|---|---|
| **Mid-Turn-Block** (`ask_user` → Event → `POST /v1/chat/answer`) | `handlers/chat.py:8441` (`deliver_ask_user_answer`), `_ask_user_register` | ✅ existiert, funktioniert |
| **Blaupause: Web-Egress-Consent** (mid-turn Batch-Dialog per Wert, → pii_decisions) | war `brain._web_consent_ask` | ⚠️ **GELÖSCHT in v9.386.0** (~110 Z) — via `git show` als Referenz holen! |
| **Result-Seam** (`_gdpr_anon_tool_text`) | `brain.py:3336`, gerufen an 6 Stellen | ✅ apply-only; muss um frischen Scan erweitert werden |
| **Frischer Scan** (`_pii_scan_text` mit FP-Gates) | `engine/pii_ner.py:2354` | ✅ nutzt `_pii_effective_action` + `min_occurrences` |
| **Sticky pii_decisions-Ledger** | `pseudonym_maps` + `pii_decisions` DB | ✅ session-sticky, latest-wins |
| **GDPR-Recovery-Dialog-UI** | `panels_gdpr.js:862` (`pii-recovery-modal`), `chat_send.js:920` | ✅ existiert (für Anon-Fehler), evtl. wiederverwendbar |

**Die 6 Result-Seam-Einhängepunkte** (`engine/mempalace_glue.py`):
- `tool_mempalace_query` (1137), `tool_wiki_read` (1421+1462), `tool_mempalace_kg_query` (1703),
  `tool_mempalace_kg_search` (1823), `tool_mempalace_kg_neighbors` (1897).

---

## 4. Umsetzungsplan (Schritte)

### Schritt 1 — Retrieval-Scan-Seam (neu)
Neue Funktion `brain._gdpr_retrieval_scan_and_seed(text, source) -> None` (oder in `_gdpr_anon_tool_text`
integriert), die NUR für Retrieval-Quellen läuft (`source` beginnt mit `mempalace:`/`kg:`/`wiki:`):
1. Nur aktiv, wenn ein anonymisierendes Mapping auf der Session existiert (`gdpr_action=anonymise` gesetzt).
2. `_pii_scan_text(text, cfg)` mit den **Production-FP-Gates** (org=ignore, min_occurrences, Konfidenz).
3. Werte gegen das **bestehende Session-Mapping** + die **bestehenden pii_decisions** abgleichen
   → nur **NEUE, unentschiedene** Werte übrig behalten (bereits entschiedene fragen nie wieder).
4. Gibt es neue Werte → **Dialog** (Schritt 2). Sonst → no-op, der apply-only-Seam läuft normal.

### Schritt 2 — Mid-Turn-Batch-Dialog (Blaupause: gelöschter `_web_consent_ask`)
1. `git show <commit-vor-9.386.0>:brain.py` → `_web_consent_ask` als Vorlage holen.
2. EIN `AskUserQuestion`-Batch über alle neuen Werte: pro Wert „Anonymisieren / Als Falschtreffer markieren".
   PLUS die Turn-weite Wahl: **Abbrechen / Lokales Modell / Anonymisieren** (wie Recovery-Dialog).
3. `_ask_user_register` VOR dem Emit (race-frei). Nicht-interaktive Turns (Background/Scheduler,
   kein `event_callback`) → **fail-closed**: refuse/skip (können nicht fragen — kein stiller Leak).
4. Ein Dialog pro Turn (Timeout → asked-Set auf RequestContext, Retry im selben Turn ohne 2. Modal).

### Schritt 3 — Entscheidung → Mapping-Seed
1. „Anonymisieren"-Werte → `pii_decisions`-Zeilen (`turn_action=anonymise`) + ins aktive Mapping (`mapping.record`).
   **NAMESPACED Hash beachten** (v9.338 lesson: value-only Hash, sonst shadowen sich die Zeilen).
2. „FP"-Werte → `false_positive=1`-Zeilen (bleiben im Klartext, fragen nie wieder).
3. „Lokal" → Turn auf lokales Fallback-Modell umleiten (wie Recovery `local_model`).
4. „Abbrechen" → Turn abbrechen, nichts persistieren.
5. Ab jetzt fakt der **bestehende** apply-only-Seam die Werte automatisch — im ganzen Turn + Folge-Turns (sticky).

### Schritt 4 — Roundtrip absichern
Der args-deanon (L3a, `GDPR_LLM_ARG_TOOLS`-INVERS aus v9.397) übersetzt Namen in `read_path`
zurück, wenn das Modell sie an `read_document` gibt. **Verifizieren, dass der Roundtrip auch für
Retrieval-geseedete Werte schließt** (nicht nur Attachment-Werte).

---

## 5. Fallstricke / Invarianten (aus der Analyse)

1. **`GDPR_ALL_CHECKS_PRE_DIALOG_PLAN` §C durchbrechen — bewusst:** Der bestehende Plan sagt
   „eine Detektion pro Turn, vor dem Dialog". Retrieval-PII ist zur Pre-Send-Zeit UNBEKANNT (kommt
   erst durch die Modell-Query). Also braucht Retrieval einen ZWEITEN, mid-turn-Detektionspunkt.
   Das ist eine bewusste Erweiterung, NICHT der pre-dialog-Invariante widersprechend (die galt für
   Attachments/typed-text, die pre-send bekannt sind).
2. **NER-FP-Flut:** Ohne die Production-FP-Gates würde die „Systemverantwortliche"-Liste 23 Dialoge
   (bzw. 23 Batch-Einträge) mit viel Müll erzeugen. Die Gates (org=ignore etc.) sind Pflicht.
3. **Perf:** Frisches NER (spaCy) pro Retrieval-Turn. Nur laufen, wenn Mapping aktiv (anonymisierende
   Session) — der überwältigende Normalfall (keine Anon) zahlt nichts.
4. **Fail-closed für Background:** Ein Scheduler/Background-Turn kann nicht fragen → muss refuse/skip,
   nie still durchlassen (sonst Leak durch die Hintertür).
5. **KV-Cache:** Die Änderung ist im Tool-Result-Pfad (kein System-Prompt/Tool-Schema) → KV-Prefix
   unberührt, kein Warmup-Reprime. Server-Restart für die Logik nötig.
6. **v9.397-Konsistenz:** Der Result-Seam (`_gdpr_anon_tool_text`) ist die BESTEHENDE PII-in-LLM-Grenze
   (bleibt nach der v9.397-Inversion). Dieser Plan erweitert nur SEINEN Seed, nichts an der Politik.

---

## 6. Test (nach Implementierung)

1. **Unit:** neue Retrieval-Scan-Funktion — neue Werte → Dialog; entschiedene Werte → kein Dialog;
   FP-Gates greifen (org/min_occurrences); Background-Turn → fail-closed.
2. **E2E:** Projekt-Chat „Liste Systemverantwortliche" mit Anon → Dialog erscheint, Nutzer wählt
   anonymisieren → `applied>0`, `anonymise_read>0`, Modell sieht Fakes, Antwort deanonymisiert echt.
3. **DANN Test 2 möglich:** die policies-Eval (MoA, Gold-Reuse aus
   `eval/results/20260705T065248_disc-none_moa-delegate-rep3`, gold 0.92 / brain 0.76) mit `--anonymise`
   erneut — jetzt greift die Anon wirklich → misst den echten Qualitätseffekt gegen Opus-Gold.

---

## 7. Kontext für die Umsetzungs-Session

- **Eval-Harness bereits vorbereitet:** `eval/run.py` hat schon einen `--anonymise`-Schalter
  (setzt `gdpr_action=anonymise`), verifiziert lauffähig (Smoke: MoA + Gold-Reuse + kein 429).
  Nach dem Fix greift die Anon dann auch wirklich.
- **Serialisieren:** MoA + parallel = HTTP 429 (Provider erschöpft). Eval mit `--parallel 1` fahren.
- **Gold NIE neu fahren** ([[feedback_eval_reuse_gold]]): `--skip-gold --reuse-results <dir>`.
- **Verwandt:** v9.336 (Result-Seam auf mempalace_query), v9.338 (Web-Consent-Blaupause, gelöscht in
  v9.386), v9.397 (PII-in-LLM-Politik), v9.96 (Drawer-raw-Entscheid), `GDPR_ALL_CHECKS_PRE_DIALOG_PLAN.md`.
- **Memory:** `project_pii_phase1_test1_results`, `project_gdpr_pii_in_llm_only_policy`, `project_pii_quality_test_plan`.
