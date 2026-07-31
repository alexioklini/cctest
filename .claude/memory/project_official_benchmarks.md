---
name: project_official_benchmarks
description: "v9.275.0: Benchmark-Fähigkeits-% aus offiziellen Leaderboards (AA-API + LMArena-HF-Dataset), Perzentil statt Pool-Min-Max, Speed weiter interner Seed-Test, interner Judge-Bench nur noch Fallback"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3992b6a8-1261-4f2c-a6af-e70acf783adb
---

v9.275.0 (2026-07-03, daa0574f): Modell-Benchmark-CAPABILITY kommt von OFFIZIELLEN Leaderboards (`engine/bench_official.py`); SPEED (tps) weiter intern via `model_bench.benchmark_cell(measure_only=True)` (Seed-Test, alle Prompts, kein Judge). Interner Prompt+Judge-Bench BEHALTEN als Fallback für Modelle ohne Leaderboard-Eintrag (lokale/oMLX) — `source:"internal"`.

- **Quellen**: Artificial Analysis Data API `https://artificialanalysis.ai/api/v2/data/llms/models` (x-api-key, kostenloser Key 1000 req/Tag — Key ist HINTERLEGT (2026-07-03, config.json → benchmark_official, gitignored; pre-commit-Mirror redigiert *api_key zu YOUR_API_KEY); AA liefert 550 Modelle, II für 537, coding 131, math 269, agentic-Index LEER → Kette fällt auf Arena multi_turn) + LMArena HF-Dataset `lmarena-ai/leaderboard-dataset` (datasets-server `/filter`-API, config `text_style_control`, split `latest`, CC-BY-4.0, kein Auth, ~364 Modelle/Kategorie, ~9s/Kategorie). `TASK_SOURCE_MAP`: coding/math/research/analysis/agentic → AA-Indizes zuerst; reporting/creative/fast → Arena-Elo zuerst.
- **GOTCHA Normalisierung**: Pool-Min-Max REJECTED — pinnte mistral-small auf 35 (< Router-Floor 50) für ALLES → Kostenexplosion. Stattdessen PERZENTIL in der vollen Leaderboard-Verteilung: small ~55, medium ~78, Frontier ~90 — Floor-Semantik bleibt, research-45-Override (9.272.3) wirkt weiter (override sticky).
- **Matching** (9.275.1, release-date-bewusst): `_norm2` liefert (norm, is_alias); VERSIONS-GEPINNTE IDs matchen exakt, ALIAS-IDs (`-latest`) nehmen den NEUESTEN Familien-Eintrag (AA release_date > YYMM-Token im Namen > II-Tie-Break). GOTCHA: AA parkt die ÄLTESTE Release unter dem nackten Familien-Slug (slug 'mistral-small' = Sep '24, II 4.7!) — Exakt-Match-Shortcut für Aliase wäre genau falsch (kostete beim ersten AA-Lauf Perzentil 16 statt 63). Per-Modell-Override `models.<id>.official_names {artificialanalysis, lmarena}` (GUI "Zuordnung") gewinnt. Getroffener Name + Rohwert auf der measured-Zelle (`source/raw/official_name`). Matcher-Änderungen brauchen Server-RESTART vor dem nächsten Benchmark-Lauf (in-memory Code).
- **Cache**: `agents/main/bench_official_cache.json` (24h TTL/Quelle, gitignored; Fetch-Fehler → stale Cache → interner Fallback, blockiert nie). Config: `benchmark_official {artificialanalysis_api_key, cache_ttl_hours, enabled}`; Key-Save via `POST /v1/services/server {benchmark_aa_api_key}`; `GET /v1/models/config → benchmark_official.aa_key_set`.
- LMArena deckt NICHT: Devstral, Codestral, Qwen2.5-7B (AA-Key nötig oder interner Fallback). Live verifiziert: fast-Zelle mistral-small = capability 55 (lmarena, Elo 1357.67, mistral-small-2506) + tps 11.6 intern.
- Vorbestand (nicht angefasst): "Benchmark: alle aktivierten" targeted ALLE enabled Modelle inkl. Whisper/Voxtral/TTS — die scheitern an Chat-Prompts mit capability 0.
