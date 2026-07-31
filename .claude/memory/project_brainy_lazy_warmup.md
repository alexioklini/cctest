---
name: project_brainy_lazy_warmup
description: "Brainy (helpdesk) hat einen EIGENEN KV-Prefix; lazy-geprimt beim Öffnen der Bubble, nicht im Keeper"
metadata: 
  node_type: memory
  type: project
  originSessionId: eb62a0b4-ca36-4ecb-a5e1-2490ff6da695
---

2026-05-25 (v9.25.0): Brainy nutzt einen ANDEREN System-Prompt (helpdesk-Prompt aus `config.json → helpdesk.system_prompt`) + ein anderes Tool-Set (`_HELPDESK_TOOLS`, 15 read-only) als der normale Chat → **eigener KV-Prefix**. Der warmup-keeper primt nur den interaktiven Prefix; Brainys erste Frage auf einem lokalen Modell zahlte sonst vollen Prefill.

**Mechanismus:** `POST /v1/helpdesk/warmup` (von `brainyOpen()` fire-and-forget gerufen) primt Brainys Prefix im Hintergrund. No-op außer Brainys Modell ist **lokal + warmup an**. 90s-Debounce + in-flight-Dedup (`_helpdesk_warmup_state`) gegen Re-Open-Spam.

**Warum lazy (nicht im Keeper):** auf SINGLE-GPU (heutiger Stand, `max_concurrent=1`) evicten sich Chat- und Brainy-Prefix gegenseitig — der zuletzt genutzte bleibt warm. Den Keeper jeden Zyklus mitprimen zu lassen wäre zu aggressiv (~25s-Prefill alle 30s + Dauer-Evict des Chat-Prefix). Lazy-on-open holt Brainys Prefix bei Bedarf zurück. Auf der DGX Spark wäre Dauer-Prime besser — siehe [[project_dgx_spark_warmup_plan]].

**Shared infra (wiederverwendbar):** `build_first_turn_prefix(purpose=, system_prompt_override=)` + `run_model_warmup(purpose='helpdesk', track_state=False)`. `track_state=False` ist KRITISCH — sonst clobbert der Brainy-Side-Prime den Modell-Warmup-State (Keeper keyt nach Modell). Verifiziert: der helpdesk-Prefix ist byte-identisch zum echten Brainy-Turn (Prompt + 15 Tools).

**Nebenbei in v9.25.0 behoben:** vorbestehender Bug — `server_config['warmup']` wurde beim Boot NIE aus config.json geladen (jeder `wcfg.get()` fiel auf Code-Defaults zurück → ganze warmup-Section war toter Code). Jetzt geladen; dadurch greift auch `pool_depth=10`.
