---
name: project_vllm_metal_migration
description: "Lokales Inferenz-Backend von oMLX auf vllm-metal migriert (2026-05-25) — echtes continuous batching, gemma-4 via oQ3 3-bit"
metadata: 
  node_type: memory
  type: project
  originSessionId: eb62a0b4-ca36-4ecb-a5e1-2490ff6da695
---

2026-05-25: Lokaler `Lokal`-Provider von **oMLX → vllm-metal** migriert (echtes continuous batching, das oMLX nicht kann — siehe [[project_omlx_batching_measured]]). Zugleich DGX-Spark-Vorbereitung (Spark nutzt auch vLLM).

**Setup (läuft):**
- **Engine**: vllm-metal (offizieller vLLM-Apple-Silicon-Backend) in `~/.venv-vllm-metal` (python@3.12 arm64, via install.sh → baut vLLM 0.21.0 aus source + vllm-metal 0.2.0).
- **Modell**: `bearzi/gemma-4-26B-A4B-it-oQ3` (echtes 3-bit, 12,6 GB, im HF-cache) — gewählt weil 3-bit ~3 GB kleiner → mehr KV-Platz (TurboQuant geht bei gemma-4 NICHT, [[project_omlx_batching_measured]]).
- **launchd-Dienst**: `~/Library/LaunchAgents/com.brain-agent.vllm-metal.plist` (Label com.brain-agent.vllm-metal, KeepAlive, NumberOfFiles 65536, log ~/.brain-agent/vllm-metal.log). Cmd: `vllm serve <oQ3-pfad> --served-model-name gemma-4-26B-A4B-it-MLX-4bit --port 8012 --max-num-seqs 8 --max-model-len 16384 --enable-auto-tool-choice --tool-call-parser gemma4`.
- **served-model-name = der ALTE oMLX-name** (`gemma-4-26B-A4B-it-MLX-4bit`) → Brains bestehende Modell-ID/Chats funktionieren ohne Rename, oQ3 läuft transparent dahinter.
- **Brain config.json**: `providers.Lokal.base_url → http://localhost:8012/v1`, `max_concurrent → 4` (vllm batcht echt, nutzt das aus — bei oMLX war 4 sinnlos). `models.gemma-4-26B-A4B-it-MLX-4bit`: `max_context=16384`, `max_output=4096`, **`inference.max_tokens=4096`**. e2b/e4b auf enabled=False (eigener vllm-Dienst später, falls gebraucht). Backup: config.json.bak-premigration-*.

**KRITISCHE FIX-KETTE (sonst hängt jeder Turn mit 500):** vllm-metal verlangt `prompt_tokens + max_output ≤ max-model-len`. (1) `--max-model-len` muss groß genug sein (8192 war zu klein). (2) Der chat-worker (`handlers/chat.py:2614`) liest `max_tokens` aus **`inference.max_tokens`** (Default hartcodiert 16000!), NICHT aus `max_output`/`get_model_max_output`. → `inference.max_tokens` MUSS gesetzt sein (4096), sonst fordert Brain 16000 Output an und sprengt max-model-len → VLLMValidationError 500 → Turn-Timeout. max-model-len 16384 + max_tokens 4096 = „Maximum concurrency 3.13x @16k".

**Verifiziert**: Tool-Calling ✅ (parser gemma4, voller Loop), Warmup/Prefix-Cache ✅ (66-68% hit), 3-4 Nutzer ✅ (Aggregat steigt), echter Brain-Stack-Chat ✅ (err=null, korrekte Antwort, hasChannel=false → kein Channel-Token-Filter nötig). KV: 51k tokens, 3,13x concurrency @16k.

**oMLX**: aus (Menübar-App), bleibt als Fallback installierbar. Modelle weiter in ~/.omlx/models. **Test-venv `.venv_vllmmlx` (vllm-mlx, unbrauchbar für gemma-4) gelöscht.**

## ROLLBACK 2026-05-25 — zurück zu oMLX (Migration rückgängig)

**Grund:** vllm-metal hat KEIN SSD-Cache-Tiering. oMLX schon (`/Applications/oMLX.app/.../omlx/cache/paged_ssd_cache.py` + `tiered_manager.py` + `hybrid_cache.py`): kalte KV-Blöcke werden auf SSD ausgelagert (`_enqueue_ssd_write`, `mark_block_cold`) statt verworfen → Kontext NICHT durch RAM limitiert, sondern durch Plattenplatz. DAS ist warum 256k-Kontext mit oMLX ging. vllm-metal: KV rein RAM-basiert, harte `--max-model-len`, auf 32GB Unified Memory nur ~48-51k tokens TOTAL (KV-budget ~10-11GB / ~225KB pro token). `--kv-offloading-size` (CPU-RAM) hilft auf Unified Memory kaum (gleiches physisches RAM). Nutzer braucht großen Kontext > 3-4-Nutzer-Batching → oMLX gewinnt auf 32GB.

**Echter Trade-off (32GB):** vllm-metal = echtes Batching (3-4 Nutzer) ABER Kontext ~48k-Limit. oMLX = großer Kontext (256k via SSD-tiering) + KV-Quant (TurboQuant) + Brain-integriert + Menübar-Komfort ABER kein echtes Batching (serialisiert, max_concurrent=1). Auf DGX Spark (128GB) verschiebt sich das: mehr RAM-KV-Pool, vLLM-Batching wird attraktiver — Migration dort neu bewerten.

**Rückgebaut:** config.json aus config.json.bak-premigration-* wiederhergestellt (Lokal→:8000, max_concurrent=1, 26B max_context=256000, e2b/e4b wieder enabled). vllm-metal-launchd ENTLADEN (`launchctl unload com.brain-agent.vllm-metal`, nicht gelöscht — plist + ~/.venv-vllm-metal + oQ3-Modell bleiben für späteren Spark-Einsatz/Re-Test). Aktueller vllm-Stand gesichert in config.json.bak-vllmmetal-*. oMLX muss manuell (Menübar-App) wieder gestartet werden → :8000.

**Was bleibt nutzbar (Spark-Vorbereitung):** ~/.venv-vllm-metal (vllm-metal 0.2.0), oQ3-Modell im HF-cache, das launchd-plist (nur `launchctl load` zum Reaktivieren). Fix-Wissen (inference.max_tokens, max-model-len) gilt weiter.
