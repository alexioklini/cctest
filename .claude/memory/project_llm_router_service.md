---
name: project_llm_router_service
description: "Eigenständiger LLM-Router-Service (Repo dev/llm-router, Port 8424, launchd, Tunnel llmrouter.alexklinsky.dev) — Stand, Betriebsdaten, nicht-offensichtliche Fallen"
metadata: 
  node_type: memory
  type: project
  originSessionId: 2ed4ef32-5501-45cd-b68b-dfaaed0e854c
  modified: 2026-07-19T19:19:15.000Z
---

# LLM-Router-Service (2026-07-19)

Eigenes Repo `/Users/alexander/Documents/dev/llm-router` (FastAPI/httpx/SQLite, eigenes `.venv`, KEIN brain-Import — Wire-Logik aus `engine/llm_loop.py` PORTIERT). Daten `~/.llm-router/` (config.db + metrics.db). launchd `com.llm-router.server` (Plist ruft `.venv/bin/python -m router.main` DIREKT — `run.sh` via launchd scheitert an macOS-TCC „can't open input file" in ~/Documents). Cloudflare: `llmrouter.alexklinsky.dev` → 127.0.0.1:8424 (Ingress in `~/.cloudflared/config.yml`, Backup `.bak-pre-llmrouter`).

**Zweck**: Provider-Verwaltung + Routing (OpenAI- UND Anthropic-Wire, volle Übersetzung beide Richtungen via kanonischem Event-Modell, Passthrough-Fastpath bei gleichem Wire), Router-API-Keys mit fnmatch-Modell-Restriktionen, Kosten-/Plan-Tracking (flat/credit, session_5h-Fensterkette), Monitoring (TTFT/tok-s/Fehler, request_log + daily_rollup). **UMSTELLUNG VOLLZOGEN 19.07.**: brain routet ALLE 30 Modelle über den Router — Provider-Set in brain: `llm-router` (cloud) + `llm-router-local` (is_local:true für gemma/whisper, max_concurrent:1, supports_chat_template_kwargs:true — **`is_model_local()` ist PROVIDER-basiert**, der Split erhält GDPR-Weiche/Quota-Bypass/LocalProviderQueue!) (Credential-Halter `mistral-direct` wieder ENTFERNT in v9.380.0: `image_gen.py` löst jetzt via `_image_api()` den default_provider auf und ruft `{base}/agents|/conversations|/files/<id>/content` — Router-Spezial-Passthroughs, Provider dort via Setting `voices_provider_id`; Agent-model bleibt die Upstream-Mistral-ID). Brain-Modell-IDs unverändert, base_model_id = Router-ID; 12 Mistral-Modelle bekamen den Provider-Default-Plan (`mistral-vibe`) EXPLIZIT (Provider-Vererbung wäre beim Umzug verloren gegangen). Duplikate `llm-router/*` getombstoned; 53 disabled Alt-Modelle mit toten Provider-Refs gelöscht (Katalog = exakt die 30 aktiven). Rollback: `config.json.bak-pre-router-only`. Voices (`/v1/audio/voices`) routen modell-los via Setting `voices_provider_id`. Verifiziert nach Umstellung: glm/gemma/kimi/mistral-Turns, Voices (10 Stimmen), Warm-Pool 10/10 durch den Router, Mistral-Cache-Hits in brains Hintergrund-Calls. Invarianten im Router-CLAUDE.md (Usage-Split, message_delta-Gotcha, [DONE]-Disziplin, Extras-Passthrough).

**Live-validiert (alle gegen echte Upstreams)**: openai→openai lokal+zai (inkl. `reasoning_effort`/`prompt_cache_key`-Durchleitung) · anthropic→openai (oMLX) · openai→anthropic (Kimi, `cached_tokens` aus message_delta korrekt in `prompt_tokens_details`) · anthropic→anthropic (Kimi, implizites Caching 16/16 getappt) · brain→Router-Chat-Turn · Flat-Plan ⇒ cost 0 + cost_list + Plan-Belastung. **Aux-Endpunkte** (`aux_proxy.py`): STT/TTS/OCR/Embeddings-Passthrough mit Unit-Billing (`request_log.units` = Seiten/Sekunden/Zeichen; live: Whisper-STT ok, Voxtral-TTS 21 Zeichen, Mistral-OCR 1 Seite); `/audio/voices` bewusst offen. Key-lose lokale Provider → Leer-Key-Sentinel ohne Auth-Header/Rotation. **KV-Prefix-Reuse durch Router nachgewiesen** (TTFT 1732→950 ms) und **Multi-Turn-Caching real** (glm via brain-Session: Turn 2 = 1408/1458 cached). 34 pytest-Tests.

**Fallen / Betriebswissen**:
- Brains `POST /v1/models/config action=update` ERSETZT den ganzen Modell-Eintrag (kein Merge) — partielles `{"enabled":true}` löscht provider/base_model_id und der Turn fällt still auf den default_provider (Fehlerbild: oMLX-„Invalid model"-400 mit scoped id auf dem Wire). Immer VOLLE Config senden.
- Import-Skript `tools/import_brain_config.py` (idempotent) seedet aus brains config.json; danach Registry-Cache erneuern (Admin-Write oder Neustart) — externe DB-Edits sieht der laufende Router nicht.
- Admin-Passwort: Erstboot-Ausgabe in `~/.llm-router/server.log`; Router-API-Key `brain-agent` (id 1) steckt in brains Provider-Eintrag; stats_token in `~/sparkdash.json → llmrouter` (für die sparkdash-Read-only-Page, Commit 385193fe in cctest).
- **sparkdash-Prod läuft `~/sparkdash.py --port 8015`** (nicht 8013 wie Repo-Plist!). Repo-Kopie (inkl. LLM-Router-Page) am 19.07. mit User-OK deployed (Backup `~/sparkdash.py.bak-pre-llmrouter`); Neustart via `launchctl kill SIGTERM` (Hook verbietet `kickstart -k` pauschal).
- Umstellung „Router als einziger Provider" erst nach Live-Validierung (User-Entscheidung); brain-Quotas bleiben in brain.

Verwandt: [[project_inprocess_openai_loop]], [[project_kimi_anthropic_wire_cache_read]], [[project_cliproxyapi_removed_direct_providers]]
