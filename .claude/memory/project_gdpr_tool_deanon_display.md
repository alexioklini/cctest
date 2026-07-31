---
name: project_gdpr_tool_deanon_display
description: "v9.387-391 — Tool-Args-Deanon war korrekt, Chat zeigte Fakes → deanon_args/deanon_result + 🔓; v9.391: PII-Marks auch auf Tool-Flächen (gdprHighlightPlain, Mark-CSS MUSS unscoped sein) + Fake-DATEINAME im Reply ('_' ist \\w → wortgegrenzte Keys matchen nie) via Mapping-Paar am Rename-Block"
metadata: 
  node_type: memory
  type: project
  originSessionId: 75ca2aaa-0030-43cc-a240-a95729cd08e1
  modified: 2026-07-21T14:01:53.918Z
---

**v9.387.0** — vier GDPR-Anzeige-Reports aus dem Anonymisierungs-Dialog + Chat 3003b961.

**Nutzer-Mentalmodell (durabel, Design-Prinzip):** "Tools laufen auf dem Rechner, Anonymisierung nur für den Weg zum LLM." → ALLE lokal ausführenden Tools bekommen IMMER echte (deanonymisierte) Args; nur was das Modell sieht bleibt Fake. Egress-Tools (Web/Mail/Bild/MCP) sind eine SEPARATE Schutzebene (bleiben gegatet).

**Der eigentliche Verständnis-Fehler (kein Bug!):** `mempalace_query("Collins Kerry A KYC Vollständigkeit")` lief bereits KORREKT deanonymisiert — L3a-Seam übersetzt Fake→Echt VOR Dispatch ("Stark Bonnie M KYC Vollständigkeit"). Aber die Chat-Anzeige zeigt BEWUSST die Fakes des Modells (Wire/History bleiben Fake) → sah aus, als liefe das Tool auf Pseudonymen. BEWIESEN durch Entschlüsseln des echten Session-Mappings (`pseudonymizer.load_mapping` + `deanonymize_text`). Mapping-Tabelle ist verschlüsselt (nonce+ciphertext) in `pseudonym_maps` (chats.db); mapping_id ≠ session_id.

**Mechanik-Referenz:** `_gdpr_deanon_tool_args(name, args)` (brain.py ~4175) an `engine/llm_loop.py:dispatch_tool` (~743), NACH Web-Gate (`_gdpr_guard_web_args`, Reihenfolge = Invariante). Allowlist `GDPR_ARGS_DEANON_TOOLS` (brain.py ~4006) — mempalace_query IST drin. `WEB_SEARCH_TOOLS ∩ Allowlist = ∅` (Egress-Invariante, test_dispatch_symmetry). Result-Reanon via `_gdpr_anon_tool_text`. Siehe CLAUDE.md-Changelog 9.336.0 (L3) + 9.343.0 (M1-M11 Seams). Vgl. [[project_gdpr_all_checks_pre_dialog_plan]] [[project_gdpr_decision_ledger_wire]].

**FIX (Anzeige-only):** llm_loop.py berechnet am `tool_call`-Emit dieselbe Deanon (idempotent, nur wenn geändert) → neues Feld `deanon_args` im Event; handlers/chat.py persistiert es pro Tool-Entry; web/js/chat_tools.js:`renderToolCall` nutzt `deanon_args` für Label+Args-Tabelle + Badge "🔓 deanonymisiert" (.tool-badge-deanon, grün). Wire/History UNANGETASTET; Dispatch leitet eigene Kopie ab.

**3 Dialog-Fixes (panels_gdpr.js + pii_ner.py):**
- Labels ganz deutsch: cz_rc "rodné číslo" → "Personenkennnummer"; NER-Labels waren 9.386.1 schon gefixt, brauchten nur Server-Restart (Katalog live aus `engine.PII_RULE_LABELS` an /v1/services → Dialog UND Chat-Aktivitätsblock lesen dieselbe Quelle).
- Modal-Breite: `.pii-units` auto-fill → auto-**fit** (einzelne Quell-Karte streckt sich voll).
- "Trotzdem senden" (9.387): war bei worstAction=block ausgeblendet, dann sichtbar+disabled+dynamisch. **In 9.388 KOMPLETT ERSETZT** (siehe unten).

**v9.388.0 — Sende-Dialog DREI Buttons → ZWEI (Nutzer-Modell, überstimmt 9.387er "Trotzdem senden"):**
- **"Senden an Cloud-Modell"** (Verdict `anonymise`): anonymisiert nicht-FP-Treffer + sendet; ALLE als FP → leere Mapping → Klartext an Cloud (FP-Werte kommen serverseitig NIE in Mapping). EIN Button deckt "anonymisieren" + "alle-FP-Klartext". Immer sichtbar, DISABLED nur wenn Klassifizierung Cloud verbietet (`cloudForbidden` = block/force_local auf Nicht-Lokal).
- **"Unverändert senden an lokales Modell"** (Verdict `local`): immer verfügbar.
- **"Streng vertraulich" (§1.11) erlaubt jetzt LOKAL-Versand** (vorher client nur Abbrechen). REINE Client-Lockerung — Server erlaubte lokal für strict schon immer (`engine/classification.py`: "Already on a local model — nothing to reroute"), Client war strenger. KEINE Server-Änderung.
- Labels modell-ehrlich: auf lokaler Session Primär="Anonymisiert senden"/Sekundär="Unverändert senden".
- Entfernt: `_reevalSend`, `sendBlocked`, `.pii-btn-warn`, separater `send`/continue-Verdict. `continue` in chat_send.js bleibt für Altsessions gültig. Nutzer-Prinzip: Nutzer entscheidet bewusst; Klartext-zu-Cloud über all-FP ist legitim, nur Klassifikation ist hart. Reine Client-Änderung, kein Server-Restart (nur Browser-Reload).

**Toter Code (NICHT anfassen):** `config.json → gdpr_scanner.rule_labels` — nichts liest es, Labels kommen aus dem Code-Dict.

**v9.389.0 — drei weitere Dialog-Reports:**
- **Prompt weg bei Abbrechen**: Projekt-Detail-Sendepfad ruft `newChat()` VOR dem PII-Dialog (leert Composer); Cancel schrieb Text nie zurück. FIX chat_send.js `_abortRestore()` an allen Abbruch-Returns (nur wenn Feld leer).
- **GOTCHA deutsche Labels**: die scanner-internen `label`-Felder (`_pii_rules` in pii_ner.py, auch `rule["label"]` bei :2383) sind **ENGLISCH** ('Email address', 'Date of birth'). Die deutschen sind NUR in **`PII_RULE_LABELS`** (rule_id→de). JEDE user-sichtbare Label-Stelle MUSS `engine.PII_RULE_LABELS.get(rid)` mappen, NICHT `f.get('label')`. In handlers/chat.py waren 5 Stellen betroffen (Full-Mode scan-text + 2× Attachment-findings_full + 2× Grouped-Mode). Full-Mode hängte zudem GAR KEIN label an → Client-Fallback `gdprRuleLabel` griff nur bei geladenem Katalog.
- **Kontext-Anzeige**: Full-Mode-Findings tragen `start`/`end` (Offsets), wurden aber im Client-Assembly verworfen. NEU: server liefert `context_before`/`context_after` (±20 Zeichen, whitespace-kollabiert). WRAP-VERBOT + Treffer immer sichtbar: einzeilige Flex-Zelle, `<mark>` flex:none, Kontext ellipsiert (before via `direction:rtl`+`<bdi>` von links). NUR Nachrichtentext-Findings (Attachments tragen keine Offsets, `full_text` server-only).

**v9.391.0/.1 — PII-Marks auf Tool-Flächen + Fake-Dateiname im Reply (Chat 8de1eeb8):**
- **Tool-Marks**: `gdprHighlightPlain(text)` (chat_render.js) = Drop-in für `esc()` — Gate `_gdprMarksVisible`, ledger-getrieben (`chat._piiDecisions` → `buildGdprCleartextSpans` → `renderPlainTextWithGdprHighlights`). Angewendet: Tool-Zeile (renderToolCall desc), Panel-Kartentitel (_toolEntryCard), Args-Tabellen-WERTE (renderToolArgsTable). Ergebnis-Box: `highlightToolResult` = Wrapper `_gdprMarkHighlightedHtml(_highlightToolResultRaw(...))` — Marks auf FERTIGEM hljs-HTML, nur Text-Segmente zwischen Tags (Segment unescapen → Span-Match → durch geteilten Plain-Highlighter re-escapen; über hljs-Spans gesplitteter Wert wird schlicht nicht markiert). Deckt Chat + Aktivitäts-Panel + Expand über EINEN Seam. `toggleGdprDetails` muss `renderBackgroundTasksPane()` mitrufen (2s-Poll rebuildt nur bei Struktur-Änderung). net-globals 2099→2102.
- **GOTCHA CSS (9.391.1)**: `mark.gdpr-restored/.gdpr-cleartext` in main.css MÜSSEN **unscoped** sein — das alte `.msg-content`-Scoping ließ auf allen anderen Flächen den Browser-Default für `<mark>` durch (grell gelb, schwarzer Text, ignoriert Dark-Theme). Klassen sind feature-eindeutig → bare Selektor sicher.
- **Fake-DATEINAME im Antworttext**: Datei war korrekt renamed (v9.390.0), aber Reply nannte weiter `..._Sage_Emerson_Adams.html`. ROOT CAUSE: rein-alphabetische Reverse-Keys ersetzen **wortgegrenzt** `(?<!\w)key(?!\w)` (9.383.6 Cameronstrasse-Schutz, korrekt!) und `_` IST `\w` → 'Sage' matcht nie in 'Sage_Emerson'. FIX am Single-Fix-Point Rename-Block (`make_gdpr_after_file_write_cb`): nach committetem Rename `mapping.record(real_fname, fake_fname, 'name', count=False)` — ganzer Dateiname als derived Paar. Key hat `._` → Substring-Replace-Pfad; StreamingDeanonymizer recomputet pro Delta mit live Mapping → restauriert LIVE; Final-Pass + find_restored_spans + Forward-Sweep (User tippt Echtnamen → Modell sieht Fake) + Turn-End-`save_mapping` greifen alle automatisch. Ledger-Row `is_derived` (kein Fake-Finding im Report).
- **9.391.2**: '🔓 deanonymisiert'-Badge auf Nutzerwunsch ENTFERNT (Chat + Panel) — per-Wert-Marks+Tooltips tragen die Info; deanon_args/deanon_result-Mechanik bleibt vollständig.
