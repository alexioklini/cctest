---
name: project_omlx_nanbeige_bundle_patch
description: Nanbeige4.2 KOMPLETT ENTFERNT (24.07.26, zu langsam für brauchbare Antworten) — Patch revertiert, Modelle + Router-Provider + Brain-Einträge gelöscht; NICHT wieder aufsetzen
metadata:
  node_type: memory
  type: project
  originSessionId: f3e4fc2f-321e-4f28-b945-8baf374ea117
  modified: 2026-07-24T11:25:37.244Z
---

**ENTFERNT am 2026-07-24** (User-Entscheid: Nanbeige braucht viel zu lange für brauchbare Antworten — nicht wieder aufsetzen):

- **oMLX** (Mac Studio lokal, :8000): alle 4 Nanbeige-Modelle entfernt (3 HF-Cache-Symlinks + selbstquantisiertes `Nanbeige4.2-3B-oQ6e`-Verzeichnis GELÖSCHT, ~13,4 GB freigegeben inkl. HF-Snapshots); Bundle-Patch `nanbeige.py` + Marker `_BRAIN_PATCH_nanbeige.md` aus dem App-Bundle revertiert; Discovery-Reload → Instanz serviert nur noch `gemma-4-12B-it-qat-oQ4-fp16`. **Nach oMLX-Updates ist NICHTS mehr anzuwenden.**
- **llm-router**: Provider `local-nanbeige` (127.0.0.1:8000) + seine 4 Modell-Einträge per Admin-API gelöscht (Registry-Cache invalidiert, kein Restart). Der Provider `Lokal` (M4, 192.168.1.214:8000) blieb unberührt.
- **Brain**: 4 Modelle `nanbeige4.2{,-fp8,-8bit,-oq6e}` per `models/config action=delete` entfernt → Tombstones in `deleted_models` (zusätzlich zu den alten `llm-router/nanbeige*`-Tombstones) — ein Router-Sync fügt sie NICHT wieder hinzu. ~14 alte Sessions tragen das Modell noch als last-used (fallen aufs Default zurück, s. [[project_next_prompt_dead_pin_precedence]]).

Auch der pipx-Install des Forks (`~/.local/pipx/venvs/mlx-lm/`, war nur Patch-Quelle) ist deinstalliert (24.07.) — es gibt KEINE Überbleibsel mehr.

Historie (falls je wieder relevant): Nanbeige4.2 (looped transformer, model_type `nanbeige`) lief via Bundle-Patch aus dem Fork-Commit b9ec5398 in `/Applications/oMLX.app/…/mlx_lm/models/`; oMLX liefert Thinking als `reasoning_content`; Template öffnet `<think>` ohne eigenes Token.
