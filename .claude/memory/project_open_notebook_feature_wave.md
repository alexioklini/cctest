---
name: project_open_notebook_feature_wave
description: v9.302–9.307 (2026-07-10) — sechs Open-Notebook-inspirierte Features in einem Rutsch; nicht-offensichtliche Entscheidungen + Verifikationsbefunde
metadata: 
  node_type: memory
  type: project
  originSessionId: 4368a5a2-8b01-47d1-8334-d5965cf0588c
---

Analyse von github.com/lfnovo/open-notebook (NotebookLM-Klon) → sechs Features umgesetzt (je eigener Commit, je Version + kuratierter Eintrag + Skill-Update):

- **9.302.0 Custom Studio-Presets** („Transformations"): `config.json → studio_presets` + Live-Mirror (tool_settings-Muster), kind `custom:<id>`, CRUD `/v1/studio/presets`. **Pro-Quelle-Modus**: `output_gen._iter_project_sources` enumeriert die DREI Quell-Stores (Ingest-Chunks re-joined per source_hash · Input-Folder via `_do_extract` · `web-urls/*.md`); je Quelle eine Wiki-Seite mit stabilem `source_ref studio-preset/<pid>/<key>` + `replace=True` → **Re-Run re-versioniert statt dupliziert** (E2E: v1→v2, kein Duplikat). Kombinierte Report-Row mit `save_report_output(file_wiki=False)` gegen Doppel-Filing. Falle gefixt: kind mit `:` landete im Dateinamen → `kind_slug`.
- **9.303.0 Save-to-Wiki-Button**: `POST /v1/wiki/from-message`, `source_ref message/<id>` + replace=True = idempotent. Wiki-Writes hängen direkt nach Server-Restart u. U. MINUTENLANG am MemPalace-Mirror (Embedding-Modell-Load/`_palace_write_lock` im Boot-Fenster) — kein Bug des Endpoints, betrifft alle Wiki-Saves.
- **9.304.0 Podcast-Sprecher + Deutsch**: Der Header-Kommentar „AUDIO IS ENGLISH-ONLY" in audio_overview.py war VERALTET — nur der Studio-Worker (`run_audio_overview`) reichte nie `lang` durch; der Chat-Pfad konnte längst Deutsch. 1–4 Sprecher mit Personas: Tags `HOST_1..4` (Parser akzeptiert Legacy A/B), `_stitch(lines, voices-Liste)`, `_resolve_speakers` + `_voice_pool_for_lang`. Externe Caller angepasst: wiki_gen (`_stitch([va,vb])`), translate_tools (hosts-Feld). UI: Audio-Karte öffnet jetzt Optionen-Modal.
- **9.305.0 Quellen-Pinning**: Websuche-Basket-Muster 1:1 (`sessions.pinned_sources`, manage-Action, wire-only-Injektion via `_inject_web_preamble_into_wire`, `metadata.pinned_sources`). **Sicherheitsmodell: Client sendet keys, nie Pfade** — Worker löst nur gegen `_iter_project_sources` DIESES Projekts auf. Kein Tool-Lockout (anders als Websuche). Beifang-Fix: `get_project()` liefert Display-`name`, kein `folder_name` — bei `_iter_project_sources` immer `folder_name` explizit setzen, sonst falscher Ingest-Ordner bei umbenannten Projekten.
- **9.306.0 Globale Suche**: `GET /v1/wiki/search` (tool_wiki_read query-mode cross-wing + tool_mempalace_query eigene Wing, LLM-frei); search.js neu — der Server-Endpoint `/v1/sessions/search` (inkl. messages.content-LIKE) existierte seit langem, war aber NIE vom Modal verdrahtet.
- **9.307.0 YouTube/Audio-Transkripte**: Am web_fetch-Choke-Point (wirkt automatisch in Chat + Websuche-Prefetch + web_urls-Miner). yt-dlp = Host-Dependency (`/opt/homebrew/bin/yt-dlp`, NICHT requirements — wie crawl4ai). `.webm` nur via Content-Type erkennen (Extension = ambig Video). STT über `media.transcribe_and_translate(target_lang="")` = Resolver+Kosten gratis; auf dieser Maschine resolvte der Default zu voxtral-mini (flat $0), nicht whisper.

BEWUSST NICHT übernommen: Ask-Modus (agentisches Retrieval ist besser), Full-Content-Chat (KV-/Token-feindlich — stattdessen Pinning), Notes (Wiki ist Superset), MCP-Server (User-Entscheid: weggelassen).

E2E-Testmuster dieser Session: Wegwerf-Projekt + 2 Mini-Quellen per `/ingest`-Multipart; Studio-Korpus-Pfade brauchen den **Projekt-Wing** (erst nach `sync-now` + project-sync-Zyklus), der Pro-Quelle-Modus NICHT (liest Stores direkt). `POST /v1/chat` braucht eine via `POST /v1/sessions` angelegte Session (sonst „Session not found").
