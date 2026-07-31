---
name: project_glm_kimi_direct_providers
description: v9.292.0 — glm-5.2/kimi-k2.6 von Kilo BYOK auf eigene Direkt-Coding-Endpunkte umgezogen; Kimi=thinking-only lehnt reasoning_effort none ab
metadata: 
  node_type: memory
  type: project
  originSessionId: 35b3b3e4-c2dc-4979-8697-02e3b20ec955
---

v9.292.0 (2026-07-07): glm-5.2 + kimi-k2.6 verlassen Kilo, gehen DIREKT auf ihre Coding-Plan-Endpunkte. deepseek-v4-*/gemma-4-*-cloud BLEIBEN auf Kilo.

**Root cause (belegt, nicht vermutet):** Kilos **BYOK-Schicht** drosselte GLM — 8 schnelle Requests via kilo.ai/api/openrouter (zai-coding/glm-5.2) → 4× HTTP 429 Body `[BYOK] … hit its rate limit`, `error_type:byok_error`, `is_byok:true`. Derselbe GLM-Key DIREKT gegen api.z.ai → 8/8 200, GLM selbst limitiert NICHTS (Dashboard "ok" stimmte). Kimi ging über Kilo weiter, weil dessen Kilo-Weg **credit-basiert (kilo-credit)** statt BYOK ist — deshalb "kimi ging, glm 429".

**Neue Provider (config.json):**
- `zai-coding` → `https://api.z.ai/api/coding/paas/v4`, glm-5.2 `base_model_id: glm-5.2` (PLAINER Plan-Name, NICHT die Kilo-scoped `zai-coding/glm-5.2`-Form!)
- `kimi-coding` → `https://api.kimi.com/coding/v1` (POST /chat/completions), kimi-k2.6 `base_model_id: kimi-for-coding`. Key ist `sk-kimi-...` (nicht `k-kimi-`, das gab 401).

**Kimi upstream = "K2.7 Code"** (`/models`: kimi-for-coding, 262k ctx, reasoning+image-in, `supports_thinking_type:"only"` = reine Thinking-Maschine). Display-Rename: shortname+display_name → `kimi-k2.7`, aber **Modell-KEY bleibt `kimi-k2.6`** (User-Wahl "Display only" → keine chats.db/Kosten-Migration der 305 Sessions).

**CODE-FIX (brain._apply_inference_to_payload ~Zeile 13072):** Der 9.277.1-Weg sendet bei Thinking-AUS für reasoning_field-Hybride explizit `reasoning_effort:"none"`. Der DIREKTE Kimi-Coding-Endpunkt LEHNT `none` mit 400 ab (nur minimal|low|medium|high, weil thinking-only). NEU per-Modell-Flag `models.<id>.reasoning_no_none` (auf kimi-k2.6 true): dann wird reasoning_effort im Aus-Fall WEGGELASSEN statt "none" → schlankstes erlaubtes Reasoning (~135 Zeichen vs 295 bei minimal), nie 400. Per-Modell statt Provider-Name-Raten (Muster wie flat_plan).

**DIAGNOSE-FALLE (kostete Zeit):** byte-IDENTISCHE Tool-Requests gegen den Kimi-Coding-Endpunkt liefern sporadisch `400 Invalid request Error` (Replay-/Idempotenz-Guard). Führte fast zur Fehldiagnose "Tools nicht unterstützt". Widerlegt durch VARIIERTEN Nachrichtentext → 6/6 mit tool_calls. Gleiche Klasse wie die curl-Cache-Test-Falle ([[project_cliproxyapi_removed_direct_providers]]). LESSON: gegen echte Coding-Endpunkte NIE mit wiederholten identischen Payloads proben — variieren.

Verifiziert live durch Brain nach Restart (variierte Payloads, tools:true+thinking:off): beide error=False, cache_read>0 (Direkt-Caching aktiv auf beiden Bahnen), Antwort korrekt. Doks: brain CHANGELOG 9.292.0, changelog_curated (admin), Skill 05-internals + SKILL.md 1.156.0.
