---
name: project_async_ingest_queue
description: v9.324 — Projekt-Upload extrahiert ASYNC (IngestQueue-Staging); 524-Root-Cause OCR-synchron-im-Request; Key-Reservierung beim Stagen; Mining-Blockade + Drain-Kick; Cancel = Ergebnis verwerfen
metadata: 
  node_type: memory
  type: project
  originSessionId: d1011bc5-92da-4e5f-9146-80c664d1449c
---

v9.324.0 (2026-07-13): `/ingest` extrahiert NICHT mehr inline. Root cause der
ko-kunden-524er: Scan-PDF = pymupdf4llm (60s) + Mistral-OCR (300s) SYNCHRON im
Request vs. ~100s Cloudflare-Tunnel-Limit; Client lud seriell; Originale wurden
nach Extraktion verworfen ("no persisted original on disk").

Architektur-Entscheidungen (nicht wieder aufmachen):
- **Staging statt Job-DB**: Bytes+Sidecar nach `<pdir>/ingest-staging/`
  (Sibling von ingested/ — NIE hinein, docs-Liste/_source_key/Daemon
  enumerieren ingested/). Boot-Rescan re-enqueued Reste (crash-safe).
- **Key-Reservierung beim Stagen** (`_reserve_key_locked`): _source_key gegen
  Disk + pending Jobs — zwei gleichnamige Dateien back-to-back bekämen sonst
  denselben Stem (noch keine Chunks auf Disk). `key_override` pinnt ihn durch
  ingest_file→_store_chunks. Client bekommt source_hash SOFORT (Gruppen-Zuordnung).
- **Cancel pro Datei** = ehrliche Semantik: queued stirbt sofort; extracting
  ist nicht mid-flight killbar (Extractor besitzt eigene Timeouts) → Flag,
  Worker verwirft Ergebnis + delete_ingested. DELETE auf terminalem Job = Dismiss.
- **Mining-Kopplung beidseitig**: Drain-Kick `_project_sync_request(…,'upload')`
  via sys.modules (kein Import-Zyklus); Daemon SKIPPT Projekt bei
  `INGEST_QUEUE.has_pending()` (halber Stapel würde churnen; deckt auch
  mid-extraction 'Sync now' ab, das sonst verloren ginge).
- Worker in `with request_context(current_user_id=…)` (Pool-Thread-Bleed;
  OCR-Kosten-Attribution liest den Context in doc_convert).
- Client: EIN Zwei-Phasen-Treiber `_runProjectImport` für Einzel+Ordner
  (4 parallele Uploads, Phase 2 = Extraktions-Watch im selben Dialog,
  'Im Hintergrund fortsetzen' stoppt nur das ZUSCHAUEN).

Beifang: `IngestManager._yaml_unquote` — _parse_frontmatter liefert gequotete
Werte verbatim; gequotete source brach Re-Ingest-Dedup (Re-Upload mintete
-2-Key) + leakte `"` in die docs-Anzeige. Choke-Points: _read_chunk_source +
list_ingested.

Banner-Gotcha: Startup-prints landen in `server.log` (stdout), NICHT nur in
server.error.log — [[feedback_brain_log_file]] gilt für Daemon-Fehler, aber
"Ingest queue: started" steht in server.log.

Verwandt: [[project_pdf_subprocess_timeout_fix]] (Extractor-Timeouts),
[[feedback_single_fix_point]], [[project_summary]].
