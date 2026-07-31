---
name: project_openrouter_gpt56_provider
description: "2026-07-11: Provider 'openrouter' (openrouter.ai direkt) + 12 GPT-5.6-Einträge (luna/terra/sol ±pro, OpenRouter scoped + Kilo bare) enabled + vollständig gebencht; -pro-Varianten nicht auf AA → interner Judge-Fallback"
metadata: 
  node_type: memory
  type: project
  originSessionId: cc082453-4a24-444b-bcd2-cf1d42a705de
---

2026-07-11: Neuer Provider `openrouter` (base_url https://openrouter.ai/api/v1, type openai, User-Key sk-or-v1-…, paid tier) via `POST /v1/providers action=add` + `action=sync provider=openrouter` angelegt — alles über die laufende Server-API (config.json-Edit allein greift nicht, server_config ist in-memory).

**Nicht-offensichtliche Fakten:**
- OpenRouter serviert die GLEICHEN Upstream-IDs wie Kilo (`openai/gpt-5.6-*`) → Sync legte automatisch scoped Keys `openrouter/openai/gpt-5.6-*` mit `base_model_id` an (der Mehrfach-Provider-Mechanismus in init_models_config).
- Nur ~30 von 345 OpenRouter-Modellen kamen in die Config: die 343 `deleted_models`-Tombstones (bare Kilo-IDs) unterdrücken Duplikate — `if model_id in tombstones or scoped_key in tombstones: continue`. Gewollt, kein Bug.
- 12 Einträge enabled (6 OpenRouter plain display, Icon 🧭; 6 Kilo als "… (Kilo)"): `thinking_format: reasoning_field` (Low/Medium/High verbatim als reasoning_effort; Off → explizites `none`, von beiden Bahnen akzeptiert — live verifiziert mit thinking=low, korrekte Antworten), ctx 1.050.000, max_output 128k, `capabilities: [chat, image]` + `raw_formats: [image/*]`, Kosten (beide Bahnen identisch, $/M): luna 1/6 · terra 2.5/15 · sol 5/30.
- **Benchmark (alle 12, fehlerfrei)**: Basismodelle matchen offizielle Leaderboards ("GPT-5.6 Luna/Terra/Sol (max)" auf artificialanalysis, sol tw. lmarena; cap 92–100). Die **-pro-Varianten stehen NICHT auf AA/LMArena** → interner Judge-Fallback (source:"internal") — erwartetes Verhalten, kein Matching-Bug. math fällt bei allen auf internal (cap 80).
- Benchmark-Loop-Pattern: `POST /v1/models/config {action:'benchmark', model_id}` ist EIN Modell pro Lauf, 409 bei parallelem Start → sequenziell mit Poll auf `/v1/models/benchmark/status` (nohup-Script, ~90–450s/Modell). Judge = server default_model (glm-5.2).
- Chat-SSE-Probe-Gotcha: Event-Typ steht in der `event:`-Zeile, NICHT als `type`-Feld im data-JSON — Parser, die `ev.type` lesen, sehen leere Antworten.

Relates to [[project_glm_kimi_direct_providers]] (Kilo/direct-Provider-Landschaft), [[project_official_benchmarks]] (Leaderboard-Mechanik).
