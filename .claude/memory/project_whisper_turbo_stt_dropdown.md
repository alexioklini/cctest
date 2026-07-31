---
name: project_whisper_turbo_stt_dropdown
description: "v9.300-301 — whisper-large-v3-turbo lokal (HF-Repo OHNE -mlx-Suffix, Ausnahme-Map) + STT-Modell-Dropdown in Übersetzen-Tabs Audio/Video+Live; HF-Token liegt in ~/.cache/huggingface/token"
metadata: 
  node_type: memory
  type: project
  originSessionId: c4afab45-81b1-4dbb-935d-ad5935afb2e3
---

v9.300.0–9.301.0 (2026-07-10, Commit f44eb729):

**whisper-large-v3-turbo (9.300.0):** Sechste lokale Whisper-Größe. Die eine
Falle: das mlx-community-Repo heißt `mlx-community/whisper-large-v3-turbo`
**ohne** das `-mlx`-Suffix, das `_whisper_repo_for` mechanisch anhängt — dafür
gibt es jetzt `_WHISPER_REPO_EXCEPTIONS` (engine/tools/translate_tools.py).
Weitere Größen prüfen, bevor man sie in `_WHISPER_SIZES` aufnimmt (HF-API:
`?author=mlx-community&search=whisper-…`). Gemessen: Turbo Metal-Peak 2,48 GB
vs. large-v3 3,95 GB; ~2× schneller auf Kurzclip (4 statt 32 Decoder-Layer);
Default blieb BEWUSST large-v3 (Turbo teilt Whisper-Schwäche bei Telefon-/
Rauschaudio, s. 9.296-Nebenbefund).

**STT-Dropdown (9.301.0):** `GET /v1/translate/stt-models` (capability-Gate =
`_transcription_resolve`); Media-Upload hatte `transcribe_model` schon,
Live-Sessions NEU: `LiveSession.transcribe_model` (leer = tools-config-Default),
Worker resolvt `self.transcribe_model or cfg.default_model or voxtral-mini-latest`,
Abrechnung läuft über `_resolved_model_id` (bleibt korrekt bei Fallback).
Ein neues JS-Global `trEnsureSttModels` → net-globals-Baseline jetzt **1896**
(Cache-Flag liegt auf `trState._sttModelsLoaded`, kein zweites Global).

**Infra:** HF-Token liegt seit 2026-07-10 in `~/.cache/huggingface/token`
(huggingface_hub-Standardpfad, wirkt für alle Prozesse/Venvs — Server-Downloads
laufen nicht mehr unauthentifiziert/rate-limitiert).
