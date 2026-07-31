---
name: project_flat_plan_and_refusal_eval
description: "v9.281.0 — flat_plan-Abrechnung pro Modell (Coding-Plan-Aliase zai-coding/kimi-coding), real-vs-Listenpreis-Anzeige, 402-Fail-loud; Refusal-Answer-Shape + MoA-Quellen-Prompts (F1 0.25→0.73, Vollkorpus 0.854 keine Regression); Eval-Gotchas (Kilo-Kredit-Kollaps, 300s-Timeout-Artefakte, Thinking hilft NICHT)"
metadata: 
  node_type: memory
  type: project
  originSessionId: afdb2207-c58f-40ed-ad0c-45f4a6fffa56
---

v9.281.0 (2026-07-04, Commit bf2c1812) — zwei Stränge in einem Release.

**Flatrate-Abrechnung (`flat_plan` pro Modell):** `config.json → models.<id>.flat_plan: true`
⇒ `engine/quotas._compute_cost` → $0 (EINE Seam: Quotas/Session/Plan-Verbrauch), reguläre
`cost_*`-Felder BEHALTEN den API-Listenpreis. `brain.model_is_flat_plan`, bewusst orthogonal
zu `model_is_cache_priced` (Cache-Freeze unberührt — NIE cost_cache_read nullen, das killt
den Freeze). Anzeige real vs. Liste: `get_session_cost().cost_list` → usage/done-Events →
Statuszeilen (`0.000 (API 0.412)`), `/v1/costs/breakdown` rechnet `cost_list` +
`cache_savings` ZUR LESEZEIT aus den Token-Spalten (rückwirkend, kein Schema-Change).
Checkbox im Modelle-Grid. Gesetzt auf: glm-5.2 (`base_model_id=zai-coding/glm-5.2`),
kimi-k2.6 (`kimi-coding/kimi-for-coding`, reasoning_effort:'none' verifiziert),
mistral-medium-3.5/-small-latest (vibe flat). **Kilo-BYOT umgeht das Guthaben** (Completion
bei Balance −$0.02); Coding-Plan-Modelle brauchen den Plan-Prefix, Kilo listet sie mit
$0.0/M. Nur deepseek-v4-* zahlt noch echte API-Kosten. Kilo berechnet LISTENPREIS
(verifiziert: $14 Guthaben = $13.6 Ledger-Tag); Coding-Plan-Pfad ist LANGSAMER
(Eval-Rep 16→30 min). **GLM-Plan-Quota** (User, 2026-07-04; LITE-Abo = kleinstes Tier): ~12,5M Tokens/5h +
~63M/Woche (4,4M = 35%/7%); Z.ai zählt ~0,79× unserer Alles-inkl.-Ledger-Zahl
(Cache rabattiert ~⅔ oder Bucket-Lag). Ein voller Eval-Nachmittag ≈ 35% des
5h-Fensters — bei Eval-Kampagnen aufs Fenster achten, Chat-Betrieb irrelevant.
**KIMI-Plan-Quota (Moderato)**: VIEL enger — ~37k Ledger-Tokens (57 Referenz-Drafts)
= 11%/5h + 2%/Woche → impliziert ~340k/5h + ~1,9M/Woche. Kimi NUR als MoA-Referenz/
leichte Chats sinnvoll; als Aggregator/Eval-Vollmodell würde der Plan sofort drosseln.
**MISTRAL-Vibe-Quota (monatlich)**: 1 Eval-Tag (~617k Tokens: Judge mistral-medium
106 Calls + small-Refs/Klassifikator) = 8%/Monat → impliziert ~7,7M/Monat ≈ 12 Eval-
Tage; Normalbetrieb <0,1%/Tag. Haupttreiber = der Eval-Judge (cache-arme 15-20k-Prompts).

**9.283.0 — Coding-Plan-Dashboard:** Abrechnungskonten = eigene Objekte (`config.json →
coding_plans`, type flat|credit), Verknüpfung AM MODELL (`models.<id>.coding_plan` —
ersetzt die flat_plan-Massenflags; model_is_flat_plan ist TYP-bewusst: credit-Konten wie
kilo-credit bleiben echte Abrechnung). Endpoints /v1/plans/{usage,save,delete,calibrate};
Popover-Sektion mit Balken, %-Kalibrierung gegen Anbieter-Dashboards, $-Aufladung;
Grid-Dropdown 'Coding-Plan / Konto'. brain.get_coding_plans = mtime-Cache auf config.json
(server_config kopiert coding_plans NICHT). Übergangstag: rollierende Fenster mischen
Vor-Plan-Verkehr ein (überschätzt bis durchrotiert). Preise: glm-lite $18 (Promo $12.60),
kimi-moderato $19, mistral-vibe $14.99/Monat.

**9.282.1 — komplette Mistral-Flatrate:** alle 66 mistral-direct-Modelle geflaggt (User:
'alles via mistral flat'). log_ocr/log_tts (der EINE Unit-Billing-Choke-Point) buchen bei
flat_plan $0; Listenpreis-Rekonstruktion via `quotas._unit_list_cost` (ocr=Seiten×
cost_per_page, read_aloud/audio_overview=Zeichen×cost_per_1k_chars — Units stecken in
tokens_in). OCR/TTS/Transkription/Voices alle direkt auf api.mistral.ai verifiziert;
TTS liefert `audio_data`-Base64-JSON (Brain dekodiert das schon), Voices = `items`-Format.

**402-Fail-loud:** Terminal-Provider-Fehler Runde 1 endete als leeres done
(`reply=0c error=None`) — run_loop fing den Fehler, nur SSE-error, `summary` trug ihn nie.
Fix: `summary["error"]` + run_turn merged; Chat zeigt `*(Sidecar error: …)*`.
[[project_empty_reply_and_wide_xlsx]] war der Nachbar-Fall (leer NACH Tools).

**Refusal-Fix (Eval KG-Real-Policies, Opus-Gold v9981-rep3, Mistral-Judge):**
(a) HARD GUARD + ANSWER SHAPE: Not-found als ERSTER Satz — aber nur NACH gründlicher
Multi-Quellen-Suche (User-Sorge: nie Suche verkürzen; offene Formulierung
documents/memory/web, nicht korpus-spezifisch). Config hält KOPIE der Defaults — beide
anheben ([[project_citation_discipline_two_lanes]]). (b) MoA-Refs+Suffix quellen-bewusst
(Drafts ohne Quellen-Zugriff → not-found ist korrektes Ergebnis; keine typischen Werte
als erwartete Findings). ERGEBNIS: F1 0.25→0.73 (+0.48), Vollkorpus 0.854 ±0.007 vs
0.851 ±0.023 (keine Regression, keine falschen Refusals), F2/F3 im Rauschen, F1 bleibt
BIMODAL (0.17–0.97). MoA-Arm gesamt: 0.851 vs auto 0.733–0.792 (Juli-4-Stack).

**Eval-Gotchas:** (1) Aggregator-**Thinking hilft NICHT**: 0.820 vs 0.851, Refusal-Bucket
SCHLECHTER (0.363 — Modell redet sich ins Antworten), 2.5× Tokens — aus lassen.
(2) Kilo-Kredit-Erschöpfung mitten im Lauf = leere Antworten, Reps unbrauchbar — vor
Läufen Guthaben prüfen; leere-Antwort-Nullen (len=0) bei Aggregation als Artefakt
ausschließen. (3) eval brain.timeout_seconds=300 zu knapp für Coding-Plan-Pfad +
gründliche Refusal-Turns → Timeout-Nuller. (4) Harness killt Background-Bash-Tasks
(auch Watcher) — Evals mit nohup+disown detachen, Monitor-Tool überlebt.

**MoA-Karten doppelt (Chat 6801c2e8):** Anzeige-Bug, nicht Doppel-Befragung — synthetische
Rows persistieren sofort UND attachStream-Replay pusht sie erneut beim Mid-Turn-Öffnen.
Fix: Dedupe per tool_use_id in synthetic_tool_use/_result (deckt auch GDPR-Karten).
