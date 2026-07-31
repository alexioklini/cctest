---
name: project_dgx_spark_warmup_plan
description: Wenn auf NVIDIA DGX Spark (128GB unified) umgezogen wird — Warmup/Concurrency-Entscheidungen für lokale Modelle (gemma-4 etc.)
metadata: 
  node_type: memory
  type: project
  originSessionId: eb62a0b4-ca36-4ecb-a5e1-2490ff6da695
---

Geplanter/erwogener Umzug der lokalen Inferenz auf eine **NVIDIA DGX Spark (GB10 Grace-Blackwell, 128 GB Unified Memory, ~273 GB/s LPDDR5x)**. Was sich dann für Warmup/Brainy/Pool ändert (Stand 2026-05-25, v9.25.0):

**Speicher ist KEIN Engpass mehr.** gemma-4-26B 4-bit ≈ 15 GB Gewichte → ~100+ GB frei für KV. Pro warmem Prompt-Prefix ~1,5–3 GB → **30+ verschiedene Prefixe gleichzeitig warm** möglich. Die Eviction-Problematik (Chat- vs. Brainy-Prefix verdrängen sich) existiert auf der Spark nicht — alle Prefixe + Projekt-Prompts + Tool-Sets bleiben resident.

**WICHTIGE KORREKTUR (2026-05-25, verifiziert): die Spark hat WENIGER Bandbreite als die aktuelle Mac.** DGX Spark GB10 = **273 GB/s** (LPDDR5x, LMSYS nennt das explizit den „main limiting factor"). Mac Studio **M2 Max = 400 GB/s**, M2 Ultra = 800 GB/s. → Für reine LLM-Token-Generierung (bandbreitengebunden) wäre gemma-4 auf der Spark eher LANGSAMER (tokens/sec) als auf dem M2 Max, NICHT schneller. Mein früheres „Spark ist der Durchsatz-Sprung wegen Bandbreite" war FALSCH.

**Was die Spark trotzdem bringt** (NICHT Bandbreite): (a) Kapazität 128 GB unified → größere Modelle / mehr warme Prefixe gleichzeitig; (b) CUDA/Blackwell → reifes vLLM mit echtem continuous batching + stärkeres Compute für Prefill und paralleles Batching (wo Compute > Single-Stream-Bandbreite zählt). Der Batching-Vorteil der Spark kommt also aus Software-Reife + Compute, nicht aus Bandbreite. Für Single-User-Speed ist der M2 Max (oder erst recht ein M2/M3 Ultra) tendenziell besser.

**Wie viele gleichzeitige Zugriffe auf gemma-4-26B?** Speicher ist NICHT der Limiter (~100 GB frei für KV → viele Dutzend Sessions speicherseitig kein Problem). Limiter = Compute + Bandbreite + Inference-Server:
- **vLLM (continuous batching, für Spark empfohlen):** realistisch **~8–12 gleichzeitige Nutzer** komfortabel, weicher Abfall darüber (Gesamtdurchsatz steigt weiter mit Batching, aber Per-Stream-tokens/sec fällt, sobald die 273 GB/s saturieren).
- **oMLX/MLX (heutiger Stack):** continuous batching begrenzt, `max_concurrent` deckelt hart → eher **2–4**.
- Heute (single-GPU, `max_concurrent=1`, oMLX): strikt **1**, alles serialisiert. Der Sprung kommt von `max_concurrent` hoch UND echtem batching-Server, NICHT vom Spark-Speicher allein.
- **8–12 ist eine SCHÄTZUNG** (gemma-4-26B 4-bit). Exakte Zahl hängt an Kontextlänge/Anfrage, Batching-Konfig, gewünschter Mindest-tokens/sec → am Ende mit vLLM-Benchmark auf dem echten Prompt-Mix MESSEN, nicht vorab versprechen.

**SPARK-CONCURRENCY für gemma-4-26B (verifiziert 2026-05-25, Web-Benchmarks):** gemma-4-26B-**A4B ist MoE** (~4B aktiv, NICHT dense — das „A4B" im Namen). Richtige Analogie = Qwen3.5-35B-A3B MoE auf Spark (gemessen, github.com/adadrag/qwen3.5-dgx-spark + dendro-logic.com): Single-User ~30 tok/s (flüssig); Sweet Spot **5–20 gleichzeitige User** bei 9–13 tok/s/User; brauchbar (~6 tok/s) bis ~50; unbrauchbar (<4 tok/s) erst jenseits ~100. vLLM continuous batching bleibt bis 100 fehlerfrei. → **Realistisch ~10–20 User komfortabel, mehrere Dutzend brauchbar.** WICHTIG: weil MoE (nur ~4B aktiv/Token), ist gemma-4-26B NICHT stark bandbreitengebunden → die niedrige Spark-Bandbreite (273 GB/s) trifft es viel weniger als ein dense-Modell. (Ein DENSE ~26B läge bei nur ~5–6 tok/s/User — Nemotron-49B-NVFP4-Messung — das ist NICHT der gemma-Fall.)

**GEMESSEN 2026-05-25 auf der aktuellen Mac (NICHT Spark) — siehe [[project_omlx_batching_measured]]:** oMLX KANN batchen (Default 8, paged KV à la vLLM), die v8.9.0-„stallt/500er"-Begründung für max_concurrent=1 ist ÜBERHOLT. ABER realer Batching-Gewinn auf der jetzigen Mac ist KLEIN: 4 parallele echte Brain-Chats (max_concurrent=4) = ~1,15× Durchsatz, Einzelantwort 11,6s→bis 40,5s langsamer. Bestätigt: Bandbreite ist der Engpass, nicht die Queue. → max_concurrent hochsetzen lohnt erst auf der Spark (mehr Bandbreite); auf der Mac bringt es nur Robustheit (keine harte Serialisierung) bei kaum Durchsatz.

**Migrations-To-dos (heute NICHT umgesetzt, bewusst single-GPU-pragmatisch gelassen):**
1. `config.json → providers.Lokal.max_concurrent` HOCHsetzen (z.B. 3–4) — heute 1 (strikte Serialisierung). Erst dann echte Parallelität (mehrere Brainys/Chats gleichzeitig). Siehe [[project_provider_queue]].
2. Brainy-Prefix DAUERHAFT im warmup-keeper mitwärmen (die „jeden Zyklus mitprimen"-Variante), statt des heutigen Lazy-Prime-on-bubble-open — siehe [[project_brainy_lazy_warmup]]. Auf der Spark kein Eviction → kein Grund für Lazy/Debounce. Infra dafür existiert: `run_model_warmup(purpose='helpdesk', track_state=False)` + `build_first_turn_prefix(purpose=, system_prompt_override=)`.
3. **vLLM mit `enable_prefix_caching`** als Inference-Server evaluieren (CUDA/Blackwell-nativ). Verwaltet einen großen Prefix-Cache-Pool explizit → alle Prompt-Varianten automatisch gecacht/wiederverwendet, Brain müsste gar nicht mehr selbst „primen". Ggf. oMLX/MLX ablösen.
4. Größeres/höher-präzises Modell wird denkbar: gemma-4-26B 8-bit ~28 GB oder bf16 ~52 GB lässt noch reichlich KV-Platz.

**Kontext:** `pool_depth=10` (Session-Hüllen) ist davon UNABHÄNGIG — teilt EINEN Prefix, zero GPU cost, betrifft nur gleichzeitige „Neuer Chat"-Klicks, nicht Prefix-Warmhaltung. Warmup-Invarianten: [[project_warmup_load_aware_backoff]].
