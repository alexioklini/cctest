---
name: project_omlx_batching_measured
description: "Gemessen 2026-05-25 — oMLX KANN continuous batching, aber max_concurrent=1 drosselt es; realer Gewinn auf der Mac klein (bandbreitenlimitiert)"
metadata: 
  node_type: memory
  type: project
  originSessionId: eb62a0b4-ca36-4ecb-a5e1-2490ff6da695
---

2026-05-25 gemessen (gemma-4-26B-A4B-it-MLX-4bit auf der aktuellen Mac, NICHT Spark).

**oMLX kann continuous batching** (Recherche + Test bestätigt): Default 8 gleichzeitige Requests, `--max-concurrent-requests`, paged/block KV-Cache à la vLLM mit Prefix-Sharing + SSD-Tiering. Damit ist der `LocalProviderQueue`-Grund aus dem **v8.9.0-Changelog ("oMLX serialisiert intern, 2. Anfrage stallt, 500er") ÜBERHOLT** — bei `max_concurrent=4` liefen 4 parallele Chats sauber, keine Stalls/500er.

**ABER der reale Durchsatz-Gewinn auf dieser Mac ist klein** (Bandbreite ist der Engpass, ~273 GB/s gilt erst für Spark — die Mac ist hier offenbar ähnlich limitiert):
- Irreführender Erst-Test (4× IDENTISCHE Prompts, KEIN system prompt): 4 parallel ≈ 1 allein → wirkte wie 4×. Das war der Prefix-Sharing-Best-Case, NICHT repräsentativ.
- Realistischer oMLX-Direkttest (Brain-SP 2121 chars + 28 tools, gewärmt, 4 VERSCHIEDENE Fragen): 1 warm = 5,2s; 4 parallel = 15,1s → ~2,9× pro-Stream-Slowdown, ~1,4× Durchsatz.
- Echter Brain-Pfad (4 separate Sessions via /v1/chat, max_concurrent=4): Baseline 11,6s; 4 parallel Wandzeit 40,5s (Einzel 19–40,5s, Antworten 1022–3386c) vs. ~47s seriell → nur **~1,15× Durchsatz**, Einzelantwort massiv langsamer unter Last.

**NACHGEMESSEN (Concurrency-Sweep, direkt an oMLX, gemma-4-26B-A4B MoE, Brain-SP+28 tools, warm):** c=1 → 28,9 tok/s/stream, Aggregat 28,9 · c=4 → 8,7/stream, Aggregat 34,7 · c=8 → 3,4/stream, Aggregat 26,2 · c=16 → 2,5/stream, Aggregat 30,7. KEINE Fehler/Stalls. **Befund: oMLX batcht NICHT echt** — das AGGREGAT bleibt flach bei ~30 tok/s über ALLE Concurrency-Stufen (bei echtem continuous batching müsste es steigen, wie Spark/vLLM: 30→300+). Die Streams teilen sich nur einen festen Durchsatz. Das erklärt die Mac-„1,15×". → Der Spark-Vorteil ist damit GEMESSEN-belegt (nicht mehr Hypothese): NICHT Hardware-Bandbreite (Mac hat mehr, 400 vs 273 GB/s), sondern **Batching-Reife der Engine** — vLLM bündelt Requests in einen echten Batch-Forward-Pass, oMLX/MLX (Stand 2026-05-25) nicht. Vorbehalt: evtl. gibt es ein continuous-batching-Flag/DFlash-Modell das anders skaliert — nicht erschöpfend getestet. KONTROLLLAUF: identischer Sweep mit SpecPrefill+TurboQuant AUS → praktisch identisch (Aggregat c1=27, c4=36, c8=30, c16=31), flach. D.h. das Batching-Limit liegt NICHT an SpecPrefill/TurboQuant, sondern an der oMLX/MLX-Batching-Engine selbst.

**ENGINE-VERGLEICH GEMESSEN (2026-05-25, gleiche Mac/Modell/Brain-SP):** Hypothese „oMLX batcht nicht, vLLM schon" jetzt DIREKT belegt.
- **oMLX**: Aggregat FLACH ~30 tok/s bei c=1/4/8/16 (kein echtes Batching).
- **vllm-mlx 0.3.0**: kann gemma-4 GAR NICHT (Bug: gemma-4-Batching-Patch kennt mlx-lm 0.31.3 `shared_kv` nicht → TypeError, jeder Request 0 tok). Aus Code bestätigt.
- **vllm-metal** (offizieller vLLM-Apple-Silicon-Backend, `vllm-project/vllm-metal`, via install.sh → ~/.venv-vllm-metal, braucht python@3.12 arm64): betreibt gemma-4 KORREKT (Gemma4ForConditionalGeneration, paged attention, prefix caching). Sauberer Low-Sweep (1/2/4, kein Swap): Aggregat STEIGT 30,3 → 44,7 → 60,8 (c=4 = 1,75× Durchsatz, per-stream noch 15 tok/s). = **echtes continuous batching auf DERSELBEN Mac**.
- Vorbehalt: c=8/16 mit gemma-26B auf 32 GB ging ins Swapping (5,8 GB swap, Kurve 35→21→84 unbrauchbar) — höhere Concurrency mit 15-GB-Modell sprengt 32 GB RAM. Saubere Hoch-Concurrency braucht mehr RAM.
**ENGINE-VERGLEICH für gemma-4-26B-A4B auf der Mac (2026-05-25, alle gemessen):**
| Engine | echtes Batching | gemma-4 | TurboQuant/KV-Quant gemma-4 | Tools | Komfort |
|---|---|---|---|---|---|
| **oMLX** | ❌ flach (~30 agg, c1=c16) | ✅ | ✅ (eigener shape-aware Metal-Decode-Kernel, siehe unten) | ✅ Brain-integriert | ✅ Menübar-App |
| **vllm-metal** | ✅ Aggregat steigt (c4=60, 1,75-2×) | ✅ | ❌ NotImplementedError per-layer KV shapes | ✅ (parser gemma4) | CLI/manuell |
| **vllm-mlx** | — | ❌ shared_kv-Bug, 0 tok | — | — | — |
| **Ollama-MLX** (0.24.0, Preview seit 30.3.) | mlx-runner real (--mlx-engine, Qwen läuft) | ❌ Import scheitert `Error: unknown data type U32` (Konverter mag gemma-4-4bit nicht), Qwen-fokussiert, braucht >32GB | — | — | ✅ einfach |

**oMLX' TurboQuant-Trick (warum es bei gemma-4 geht, vllm-metal nicht):** oMLX (`/Applications/oMLX.app/Contents/Resources/omlx/turboquant_kv.py` + `patches/turboquant_attention.py`, Klartext-Python lesbar) ist PER-LAYER-SHAPE-AWARE: `_infer_head_dim` leitet head_dim pro quantisiertem State dynamisch aus packed_width ab (nimmt KEINE uniforme Shape an). Monkey-patcht `scaled_dot_product_attention` → Decode (L=1) läuft über eigenen Metal-Kernel DIREKT auf quantisiertem KV (kein Dequant), Prefill mit Fallback. Genau gemma-4s heterogene head-dims (256 lokal/512 global) + KV-sharing, an denen vllm-metals TurboQuant (`NotImplementedError`) + vllm-mlx (`shared_kv`) scheitern. oMLX hat hier die speziellere fertige Impl.

**DURCHBRUCH — 3-bit-Modell löst das KV-Problem auf vllm-metal (2026-05-25):** `bearzi/gemma-4-26B-A4B-it-oQ3` (echtes 3-bit, bits:3 group_size:64, 12,6 GB) lädt SAUBER auf vllm-metal (kein U32-/shared_kv-Fehler, 3,9s), antwortet korrekt, Tool-Calling ✅. KV-Vergleich vs 4-bit (15,6GB): model_memory 15→11,4 GB, kv_budget 10,48 GB, GPU KV cache **33.072 → 46.528 tokens (+41%)**, „Maximum concurrency for 8192 tokens/request: **5.68x**". → Das kleinere 3-bit-Modell schafft den KV-Platz, den TurboQuant nicht konnte. Damit hat man auf vllm-metal BEIDES: echtes Batching (Aggregat steigt) UND genug KV für 3-4+ Nutzer mit Reserve. TurboQuant wird damit für gemma-4 überflüssig. Modell im HF-cache: ~/.cache/huggingface/hub/models--bearzi--gemma-4-26B-A4B-it-oQ3.

**FAZIT (revidiert): vllm-metal + oQ3 (3-bit) ist die starke Kombi für gemma-4 auf 32GB — echtes Batching + ausreichend KV, KEIN TurboQuant nötig. Das kippt die Abwägung Richtung vllm-metal.** Alte Trade-off-Notiz galt nur für das 4-bit-Modell:

**ALTER TRADE-OFF (nur 4-bit-Modell): kein klarer Gewinner für gemma-4 auf 32GB. vllm-metal = echtes Batching (mehr Nutzer) ABER kein KV-Quant. oMLX = KV-Quant (mehr Platz/Kontext) + Brain-integriert + Menübar-Komfort ABER kein echtes Batching (serialisiert). Ollama-MLX + vllm-mlx scheiden für gemma-4 aus. Der Concurrency-Engpass ist die ENGINE, nicht Hardware-Bandbreite (Mac 400 > Spark 273 GB/s).**

**vllm-metal AKZEPTANZTEST für Wechsel (2026-05-25, Nutzer-Kriterien: Tool-Calling, Warmup, 3-4 Nutzer) — ALLE BESTANDEN:**
- **Tool-Calling** ✅: `vllm serve --enable-auto-tool-choice --tool-call-parser gemma4`; Brain-Toolset → sauberer tool_calls-Block (read_file mit korrekten args), voller Mehrrunden-Loop (tool_call → tool-result zurück → finale Antwort) funktioniert. Kleiner Schönheitsfehler: gemma-4 Channel-Token (`thought\n<channel|>`) nicht ganz aus dem Output gefiltert — kosmetisch, Brain müsste das ggf. strippen.
- **Warmup** ✅: vLLM `enable_prefix_caching=True` automatisch, Prefix-Cache-Hit-Rate 66-68% (Server-Log), kein byte-genaues build_first_turn_prefix nötig wie bei oMLX → sogar einfacher.
- **3-4 Nutzer** ✅ (150 tok/Antwort, sauber, kein Swap): c=1 → 30 tok/s / 5,0s; c=3 → 55 tok/s / 8,1s; c=4 → 60 tok/s / 10,0s. Doppelter Aggregat-Durchsatz bei c=4, Per-Nutzer-Latenz brauchbar (10s vs seriell ~20s). oMLX hätte serialisiert.
**KV-Cache + TurboQuant auf vllm-metal (2026-05-25 gemessen):**
- **KV-Cache** ✅ läuft (PagedAttention, 30 layers, block_size=16; KV cache 16,3 GB / 33k tokens) + **Prefix-Caching** ✅ (hit-rate 66-68%) — fortschrittlicher als oMLX, automatisch.
- **TurboQuant** ❌ für gemma-4: Das RICHTIGE Flag ist `--additional-config '{"turboquant": true, "k_quant": "q4_0", "v_quant": "q4_0"}'` (NICHT `--kv-cache-dtype turboquant_*` — das ignoriert das Metal-Backend still, turboquant=False). Mit dem richtigen Flag bricht gemma-4 ab: `NotImplementedError: TurboQuant with per-layer KV shapes is not yet supported`. Ursache = gemma-4 Architektur (heterogene per-layer head-dims 256/512 + KV-sharing), nicht TurboQuant generell — auf uniformen Modellen (Llama/Qwen) ginge es vermutlich. Also: KV-Quant-Ersparnis, die oMLX (TurboQuant KV on) bot, fehlt auf vllm-metal für gemma-4. Auf 32 GB relevant; auf Spark/128GB egal.

**→ Wechsel technisch tragfähig. OFFEN für echte Migration (noch NICHT gemacht): Brain Lokal-Provider auf vllm-metal umstellen (1 Modell/Prozess statt oMLX-Modellverwaltung), launchd/Supervision für vllm serve, Channel-Token-Filter, Warmup-Keeper-Anpassung (vLLM cached selbst → evtl. Brain-Warmup vereinfachbar). Setup: ~/.venv-vllm-metal (python@3.12 arm64), Modell aus ~/.omlx/models/. Test-venv .venv_vllmmlx kann weg (vllm-mlx unbrauchbar für gemma-4).**

**Konsequenz:** `max_concurrent` auf der jetzigen Mac hochsetzen bringt nur ROBUSTHEIT (gleichzeitige Nutzer werden bedient statt hart serialisiert/blockiert), kaum Durchsatz, und langsamere Einzelantworten. Default daher bei **1 belassen** (zurückgesetzt nach dem Test). Der kleine Batching-Gewinn liegt an der oMLX/MLX-Batching-Reife + daran, dass Single-Stream-Generierung bandbreitengebunden ist — NICHT an zu wenig Mac-Bandbreite (M2 Max = 400 GB/s, mehr als die Spark mit 273 GB/s, siehe Korrektur in [[project_dgx_spark_warmup_plan]]). Echter Mehrnutzer-Durchsatz käme von vLLM (continuous batching) + mehr Compute, nicht von „mehr Bandbreite woanders". Lehre (Nutzer-Einwand war goldrichtig): Batching-Tests MÜSSEN echten system prompt + VERSCHIEDENE Prompts nutzen, sonst misst man nur Prefix-Sharing.
