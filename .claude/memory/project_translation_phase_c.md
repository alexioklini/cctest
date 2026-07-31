---
name: Translation Sektion — Phase C geshipped
description: Audio/Video file translation + Live microphone translation (translate-as-you-go) — Voxtral durchgehend, SRT/VTT/TXT outputs, segment-aligned timestamps
type: project
originSessionId: e749713c-93d0-4fce-af11-18ad808c4690
---
2026-05-05 — Phase C abgeschlossen, Translation Sektion ist feature-complete.

**Phase C umfasst zwei UI-Tabs, beide live:**

## C1 — Audio / Video file upload
- `server_lib/translate/media.py` — `transcribe_and_translate(file)` ruft `_transcribe_with_voxtral` mit `with_segments=True`, dann `translate_segments` (numbered-list batched, mirror von document.py:_translate_chunks à 50 segments). Per-segment fallback bei parse-fail damit Content nie verloren geht.
- `_transcribe_with_voxtral` + `_transcribe_with_whisper` in brain.py um `with_segments` Flag erweitert. Voxtral liefert nativ `segments[].{text,start,end}` zurück (auch ohne explizites `timestamp_granularities[]=segment`, aber wir senden's zur Sicherheit); whisper hatte bereits segments.
- 7 Output-Files pro Job: `.transcript.{txt,srt,vtt}`, `.{lang}.{txt,srt,vtt}` (translation), `.bilingual.txt` (Side-by-side, primary download). Alle landen im artifact-folder via `_after_file_write`.
- HTTP: `POST /v1/translate/media` (multipart, ≤200MB), `GET /v1/translate/jobs/<id>?format=<key>` (added query-param für media output-file picker).
- `TranslateJob` extended: `kind: 'document'|'media'`, `stage: 'transcribe'|'translate'`, `transcript`, `segments`, `output_files`, `transcribe_model`, `duration_s`. `update_stage()` + `finish_media()` Methoden.
- UI: Audio/Video Tab mit drop-zone, mode-picker (translate vs transcribe-only), progress-bar, segment-aligned Triple-pane render (Time | Source | Translation), 7 Download-Buttons.

## C2 — Live microphone (translate-as-you-go)
- `server_lib/translate/live.py` — `LiveSession` mit per-chunk-transcribe-Architektur. MediaRecorder-Fragments (4s timeslice, webm/opus oder mp4 je nach Browser) sind self-contained und gehen direkt zu Voxtral, **kein server-side audio splicing/ffmpeg**. Per-chunk transcribe ~0.4s, end-to-end Latenz <5s.
- Translate fires async-per-segment in einem daemon thread, damit nächster Chunk nicht blockiert. `segment` SSE Event sofort, `translation` SSE Event nach ~0.5s.
- HTTP: `POST /v1/translate/live/start` → `{id}`, `POST /v1/translate/live/<id>/chunk` (multipart audio), `POST /v1/translate/live/<id>/stop`, `GET /v1/translate/live/<id>` (SSE: segment / translation / error / closed events).
- Time-offset accumulator hält Timeline monoton: jeder Chunk's letztes segment.end wird als `_time_offset_s` gespeichert; falls Voxtral 0 Segmente liefert wird auf 4s default gefallen.
- UI: Live-Mic Tab mit Mic-Button (recording-Animation), elapsed-counter, scrolling segment-list, client-side SRT-export (segmente sind alle im JS-state gesammelt — kein server-side download nötig).

**Why:**
- **Voxtral durchgehend** statt mlx-whisper: Quality > Latency. Voxtral 4s clip ~0.4s — schnell genug für Live, hochwertig genug für Banking-Transkripte.
- **Translate-as-you-go**: jedes finalisierte Segment wird sofort übersetzt, damit User in Echtzeit Translation lesen kann (Meeting-Use-Case).
- **Per-chunk statt rolling buffer**: MediaRecorder-Fragments sind self-contained; rolling buffer hätte ffmpeg-dep gebracht ohne Quality-Gewinn (Voxtral würde gleich gechunkte Audio bekommen).
- **Client-side SRT für Live**: Segmente sind eh alle im JS-state, Server müsste sie nur wieder zurückschicken. Spart eine Round-Trip.

**Bekannte Trade-offs:**
- **Chunk-boundary Bug**: Sätze die einen Chunk-Boundary überspannen produzieren 2 Segmente die eigentlich 1 sein sollten. UI zeigt sie sequenziell mit korrekten Timestamps — akzeptiert. Gemessen im E2E-Test: "...multiple sent" / "sentences. Each sentence..." statt 1 Satz. Fix wäre rolling-buffer mit ffmpeg-splice — out-of-scope.
- **GDPR**: media-uploads gehen direkt zu Voxtral (cloud); im transcribe_audio existiert ein `gdpr.server_block` interlock mit auto-fallback auf whisper-base. **Live-mic hat KEINEN solchen interlock** — wenn Bank Live-Translation für vertrauliche Meetings will, braucht's eine Voxtral-local-Variante oder mlx-whisper-stream als Live-Backend. Backlog.
- **Voxtral language detection**: liefert oft `language: null` in segments-output. Kein Auto-detect-badge bei Audio (im Gegensatz zu Text).

**Smoke-Tests (2026-05-05):**
- Media file: 11s WAV (4 Sätze) → 4 Segmente, 4 Translations, 7 Output-Files, 2.4s end-to-end. Banking-Terminologie nicht spezifisch getestet (synthetic speech).
- Live mic: 3x 4s WAV-Chunks via curl simuliert → 4 Segmente in monotoner Timeline (0.0/1.2/3.9/8.5s offsets), translate-as-you-go funktioniert (jedes segment-event direkt gefolgt von translation-event nach ~0.5s), `closed`-Event nach `/stop`.
- UI: alle Tabs (Text/Document/Audio/Live) switchbar, segment-render zeigt Time | Source | Translation Triple-pane, Download-Buttons in fester Reihenfolge (Bilingual zuerst, dann Translation-Formate, dann Transcript-Formate).

**How to apply:**
- Phase C ist die letzte Phase — Translation-Sektion komplett.
- Wenn später Voxtral lokal läuft (mistral-mlx?), Live-Mic kann sofort darauf swappen via `_transcription_resolve` — keine Code-Änderungen nötig.
- Der numbered-list parser (`_parse_numbered_response`) in media.py ist die zweite Source of Truth (nach document.py); sollten irgendwann konsolidiert werden.
- Output-Files werden im Browse-Grid sichtbar (intermediate vs output: alle text-files sind output-role per `_ARTIFACT_INTERMEDIATE_EXTS` Logik — `.txt`/`.srt`/`.vtt` zählen alle als output).
