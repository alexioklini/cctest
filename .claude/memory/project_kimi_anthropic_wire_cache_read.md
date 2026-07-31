---
name: project_kimi_anthropic_wire_cache_read
description: "k3/Kimi zeigte 0% Caching — Anthropic-Wire liefert cache_read_input_tokens erst in message_delta, nicht message_start"
metadata: 
  node_type: memory
  type: project
  originSessionId: d029b009-0fdb-4d15-8bbf-65b608fe93cd
  modified: 2026-07-18T13:45:58.160Z
---

Commit c2ad9d87 (2026-07-18). k3 (Provider kimi-coding, `wire_api:anthropic`, /v1/messages) meldete bei JEDEM Aufruf 0% Prompt-Caching in costs.db/SSE/GUI.

**Ursache:** `engine/llm_loop.py _drain_anthropic_stream_inner` las `cache_read_input_tokens` NUR aus dem `message_start`-Event. Direkt gegen api.kimi.com verifiziert — der Anthropic-Wire streamt Cache-Usage in ZWEI Events:
- `message_start`: `input_tokens=<voller Prompt>`, `cache_read_input_tokens=0` (immer 0, steht noch nicht fest)
- `message_delta` (final): `input_tokens=<nur nicht-gecachter Rest>`, `cache_read_input_tokens=<echter Wert>`

Der Loop baute die usage im message_delta-Zweig, nahm `cached` aber aus dem (0) message_start-Snapshot. **Fix:** cache_read UND das korrigierte input_tokens im message_delta-Zweig auslesen falls vorhanden — sonst prompt_tokens doppelt gezählt (message_start-input_tokens ist der VOLLE Prompt inkl. gecachtem Teil). Live verifiziert: Turn loggt jetzt cache_read_tokens=512 statt 0.

**Nicht die Ursache (empirisch ausgeschlossen):** fehlender `cache_control`-Breakpoint. Kimi cacht IMPLIZIT auch ohne Breakpoint (getestet: mit UND ohne cache_control → identischer cache_read). Also KEIN cache_control im build_anthropic_payload nötig. (Anders als echtes Anthropic, wo cache_control opt-in wäre.)

**Anzeige-Nebenfix (chat_render.js):** Cache-Tooltip behauptete bei fehlendem `cost_cache_read` fälschlich "kein Prompt-Caching". k3 ist ein Plan-Modell OHNE per-Token-Tarife (cost_input/output/cache_read alle fehlen — bewusst, Coding-Plan-Pauschale), cacht aber real. Tooltip unterscheidet jetzt gecacht-ohne-$-Tarif vs. nichts-gecacht. Die %-Anzeige ist rein tokenbasiert, nie an den Tarif gekoppelt.

**Merke:** gilt für ALLE `wire_api:anthropic`-Provider (aktuell nur kimi-coding, siehe [[project_glm_kimi_direct_providers]]). Der background_call-Pfad nutzt denselben `_drain_anthropic_stream`, ist mitgefixt. Verwandt: [[project_cache_cost_vs_classification]] (OpenAI-Wire: prompt_cache_key, cached_tokens aus prompt_tokens_details), [[reference_mistral_prompt_caching]].
