---
name: project_ingest_originals_and_image_ocr
description: "v9.326/327 — 'unknown' im Dateibaum = 800-Byte-Cap beim Frontmatter-Read (brach still auch die Re-Ingest-Dedup); Audio-Import transkribiert; Originale bleiben erhalten; Bilder per OCR"
metadata: 
  node_type: memory
  type: project
  originSessionId: fd1b7539-f6f4-4838-9e19-f7dda8c9c0d1
---

**v9.326.0 — „unknown" im Projekt-Dateibaum war KEIN Extraktionsfehler.**

`list_ingested` / `_read_chunk_source` / `delete_ingested` / `output_gen` lasen das
Chunk-Frontmatter mit **festem Byte-Cap** (`f.read(800)`). Bei tiefem Ordner-Import wird der
Header ~970 Zeichen lang: der volle relative Pfad steckt **zweimal** drin (`title:` UND
`source:`), und **mittlere** Chunks tragen **zwei** `related:`-Einträge (prev+next, je ein
voller Chunk-Dateiname). Chunk `__000` (nur next) passt mit ~700 knapp rein — die anderen nicht.
Abgeschnitten ⇒ kein schließendes `---` ⇒ `_parse_frontmatter`-Regex matcht **gar nicht** ⇒ `{}`
⇒ jedes Feld fällt auf Default: `source='unknown'`. `os.listdir()` ist ungeordnet → meist traf
es zuerst einen mittleren Chunk → ganze Gruppe „unknown".

**Zweiter, stiller Schaden derselben Zeile:** `_read_chunk_source` speist `_source_key` = die
**Re-Ingest-Dedup**. Mit leerem `source` matchte ein Re-Upload nie den bestehenden Key und
mintete einen `-2`-Duplikat-Key statt zu überschreiben.

Fix: `IngestManager._read_frontmatter()` liest **bis zum Terminator** statt bis zu einer
geratenen Byte-Grenze. **Lehre: nie Frontmatter mit fixem Byte-Cap parsen.**
(Die `read(400)`-Stellen in `server_daemons.py` sind NICHT betroffen — Substring-Proben auf
einen Marker am Dateianfang, kein Parse mit Terminator-Zwang.)

**Ebenfalls 9.326:** Audio-Import (`AUDIO_EXTS`: mp3/m4a/wav/… + mp4/mov/webm) →
`DocumentParser.parse_audio` über den GETEILTEN STT-Choke-Point
`server_lib/translate/media.transcribe_and_translate(target_lang='')` — derselbe Resolver +
Kosten-Logging wie web_fetch-Audio / Übersetzen-Tab. `.msg` war **nie kaputt** (7 Stück sauber
importiert) — sah nur wegen „unknown" so aus.

**v9.327.0 — Originale bleiben erhalten + Bild-OCR.**

- `IngestQueue._run_job` LÖSCHTE die Roh-Bytes nach der Extraktion. Jetzt: `os.replace(staged →
  originals/<key><ext>)`. Abgebrochene/fehlgeschlagene Jobs weiterhin löschen (keine Doc-Row →
  unerreichbarer Müll). `delete_ingested` räumt das Original mit weg.
  `originals/` ist **kein Miner-Root** — der Daemon walkt eine EXPLIZITE Liste (`ingested/`,
  input_folders, `web-urls/`), nicht `pdir` rekursiv ⇒ Roh-Binaries können nicht in den Palace.
- `parse_image` lieferte **nur Pillow-Metadaten** — ein gescannter Pass landete als
  „821 x 852 JPEG" im Korpus, KG zog `triples=0`. Jetzt OCR über den `ocr`-Config-Block.

**Chunker-Falle (2x getreten):** `DocumentChunker` nimmt die **erste Markdown-Überschrift als
chunk-`title`**. Ein `## Transcript`-Header im Audio-Body bzw. Mistrals `## Page 1` im
OCR-Output ⇒ jedes Dokument hiesse so. Header strippen / keinen setzen.

Nicht reparierbar: die 10 Bild-Dokumente in `ko-kunden` haben weder Text noch Original —
müssen neu hochgeladen werden.

Verwandt: [[project_mlx_ocr_inprocess]], [[project_async_ingest_queue]]
