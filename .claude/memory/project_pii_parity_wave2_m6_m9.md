---
name: project_pii_parity_wave2_m6_m9
description: v9.347 M6/G4 + M9/G12 — Tabellen-Spalten-Heuristik + Sperrschrift + EN-NER; Welle 2 KOMPLETT
metadata: 
  node_type: memory
  type: project
  originSessionId: 148121fa-c7e1-4a21-963c-10d48b755d01
---

**v9.347.0 — PII-Parität Welle 2, M6 (G4) + M9 (G12). Damit ist Welle 2 (M1–M11) KOMPLETT.** Ergänzt [[project_pii_parity_wave2_m7]], [[project_pii_parity_wave2_m4_m5]], [[project_pii_parity_l_progress]].

**LASTTEST-BEFUND (Handover-Mandat, vor M6-Design):** der O(M²·T)-Verdacht am Reverse-Pass ist WIDERLEGT. `deanonymize_text` skaliert LINEAR (40→1000 Tokens: 6,7ms→141ms/150k-Wire), die Fixpunkt-Schleife konvergiert in 2 Pässen (keine Ketten in der Praxis), Ledger-Bau 0,4µs/Eintrag. → Wire-Ansatz ist sicher; die „Massen-Tabellen gar nicht in den Wire"-Architektur ist NICHT nötig. Der echte Schaden war Recall: über die KO-Kunden-xlsx fand der Scanner 40 Findings aber NULL Kundennamen (13 Kontonummern als ca_sin mis-klassifiziert). Merke: LASTTEST kann die vermutete Architektur-Frage erledigen UND den eigentlichen Bug zeigen.

**M6/G4 Spalten-Heuristik** (`engine/pii_ner._scan_markdown_table_columns`, ERSTE Phase in `_pii_scan_text`):
- Der Extraktor rendert JEDE xlsx/csv als GitHub-Markdown-Tabelle (`| Header | … |` + `|---|`). Das macht die Heuristik sauber machbar.
- Header→Kategorie per WORT-GRENZEN-Match, NICHT Substring: `Depotvolumen` ≠ `depot`, `Information Kundenkontakt` ≠ `kunde`. Plus VETO-Liste (`volumen/cash/kontakt/kommentar/saldo/…`) für Mehr-Token-Header wo ein Keyword als ganzes Token steht aber ein Geld-/Text-Wort daneben (`Konto Saldo` = Saldo). Match auf de-punktierte Form (`kd.nr.`→`kdnr`).
- Ganze Spalte wird Kandidat → robust gegen Excel-Truncation (`KO TULLNERSAntonius`) + invertierte Formen. Header-Zeile NIE tokenisiert.
- **ca_sin-Substring-Bug (M6.4 Zellgrenzen-Anker):** ID-Zelle (`300622-800-1`) resolved auf `ignore` (business_id), ihre volle Span wird aber TROTZDEM reserviert → blockiert ca_sin's Fragment-Match `300622-800`. 13→0 FP. NICHT-OFFENSICHTLICH: Reservieren-aber-nicht-emittieren.

**M9.1/G12 Sperrschrift** (`_scan_sperrschrift_names`): Regex `([A-ZÄÖÜ]\s){3,}[A-ZÄÖÜ]`, ≥4 gesperrte Großbuchstaben → `name`-Finding über ROHE Spanne (Ledger anonymisiert On-Page-Text), kollabierte Form (`Gottwald KRANEBITTER`) als `_value` = distinct-value-Key. FP-fest: `A B C` (<4), `USA`/`EU`, `HTML CSS JS`.

**M9.3/G12 EN-NER:** `en_core_web_md` neben `de` geladen (server.py boot: `load_models(("de","en"))`), `scan_text` unioniert (de zuerst → name_spans für Proximity-Gates, en nur Nicht-Overlap). **DER LADENDE BUG:** englisches spaCy = OntoNotes-Labels (`PERSON`/`GPE`), deutsches = WikiNER (`PER`/`LOC`); `_LABEL_MAP` kannte nur letzteres → ALLE englischen Personen fielen durch, bis beide gemappt. en-Wheel in requirements.txt (Install: `pip install --break-system-packages <github-release-wheel-url>` in homebrew py3.14 — die Modelle sind PyPI-namenlos).

**Dokumentierter Rest:** 3 Float-Rundungs-Artefakte in GELD-Spalten (`18320.869999999995`→12-stellige jp_mynumber) — VORBESTEHEND, außerhalb Tabellenspalten-Scope, Junk-Fakes kein Leak. Kd.Nr.↔Konto-Join-Präfix moot (business_id überall ignore). M9.2/M9.4-7 (Verbalisierung/Transliteration/Familien-Stamm) nicht gebaut — geringerer Nutzen, teils „vermutet nicht gemessen".

**Test-Interaktion:** `test_unload_silences_ner_findings` musste de UND en unloaden (en ist jetzt Fallback). Bekannter `_NLP_CACHE`-Ordnungs-Flake bleibt (siehe [[project_pii_parity_l_progress]]).
