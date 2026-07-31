---
name: project_gdpr_pii_in_llm_only_policy
description: v9.397 GDPR-Kernpolitik invertiert — schützt NUR PII+LLM-Kombination; Deny-Liste GDPR_LLM_ARG_TOOLS; Egress-Gate + Netzwerk-Guard gelöscht; Dateien real from the start (xlsx-Race weg durch Konstruktion)
metadata: 
  node_type: memory
  type: project
  originSessionId: 7bddb1a3-74bf-48bf-afde-d5afe87900a5
  modified: 2026-07-22T09:43:36.066Z
---

**v9.397.0** (2026-07-22): GDPR-Kernpolitik nach Nutzer-Grundsatzentscheid invertiert — **"wir schützen NUR die Kombination PII+LLM, sonst nichts, egal ob nötig/plausibel"**.

**Drei Seams:**
1. `_gdpr_anon_tool_text` (Result-Seam) — anonymisiert JEDES Tool-Ergebnis zurück zum Chat-LLM. **BLEIBT** — die eigentliche PII+LLM-Grenze.
2. `_gdpr_deanon_tool_args` (Args-Seam) — **INVERTIERT**: war Allow-Liste `GDPR_ARGS_DEANON_TOOLS`, jetzt Deny-Liste **`GDPR_LLM_ARG_TOOLS`**. JEDES Tool bekommt reale Args AUSSER den 10 Tools, die Args in ein internes/Cloud-MODELL reichen: ask_llm, agent_step, delegate_task, run_background_task, translate_text/_document, generate_audio_overview, context_recall, transcribe_audio, generate_image. Deny-Liste durch Lesen jeder Impl ermittelt (Kennzeichen: sidecar_proxy/gdpr_pick_model_for_background/Sub-Agent-Spawn — nur 4 Tool-Dateien).
3. `_gdpr_guard_web_args` (Egress-BLOCK-Gate) — **GELÖSCHT** (~490 Z inkl. `_web_gate_*`, `_WEB_GATE_PASS_CATEGORIES`, `_web_release_translate_args`). Ein Nicht-LLM-Tool, das reale Daten nach außen sendet, ist außer Scope.

**xlsx-Deanon-Race strukturell weg** (löst [[project_gdpr_xlsx_deanon_race]]): write_file/write_document/edit_file/edit_document/r_exec/kernel_exec/ast_grep_replace deanonymisieren ihre Args → Dateien REAL from the start, KEIN On-Disk-Reverse, keine halb-geschriebene Datei zum Rennen. Racy `deanonymize_file` im After-File-Write-Callback ENTFERNT → nur noch READ-ONLY-Lint (race-tolerant) + autoritativer Turn-End-Sweep `gdpr_lint_written_files_at_turn_end` (aus `sidecar_proxy.run_turn` finally, nach Loop-Ende, 1 Lint/Datei, Rest-Fake=fail-loud). Neu `RequestContext._gdpr_written_files`. Verifiziert tx Anon AN: deanon_errs=0, deanon_calls=0, fake_leaks=0.

**Auch gelöscht:** Deny-by-default-Netzwerk-Guard für execute_command/python_exec (`_deanon_string_is_local_safe`). **Trade-off auf Protokoll:** modell-verfasstes netzwerk-Skript läuft jetzt mit realen Werten, kann sie senden. **BLEIBT:** `_gdpr_scan_cloud_egress` auf generate_image (Nutzer: Cloud-Bildmodell = in Scope).

**web_egress-Admin-Knopf komplett entfernt** (Schema/Default/Loader + admin_config/admin_artifacts + settings_general_tabs.js/nav.js).

**OFFEN (separat, NICHT GDPR):** mistral-medium transkribiert PDF-Tabellen manuell als Inline-CSV in python_exec statt zu parsen → 4 Iterationen (_test/_partial/_complete/_final) für 1 Excel. Model-Verhalten, war mit Alt-Code schlimmer (12 Skripte). Nächster Schritt Phase-1: ≥3x AN/AUS-Vergleich.

Tests: 909 discover, 0 neue Failures (1 pre-existing test_pii_ner NER-FP). Commit 139c95d7 auf main (nicht gepusht). Server läuft 9.397.0.
