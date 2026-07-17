# Windows-11-Footprint — was kann (noch) auf den Mac mini M4?

Stand v9.367.0, Messbasis: `dist/BrainAgent-9.367.0-win-x64/` (2,15 GB entpackt).
Ziel der Analyse: den Installationsaufwand auf der Win11-Maschine minimieren.
Wichtiger Rahmen: es gibt **eine** Win11-Server-Maschine (Mehrbenutzer, 10→70
User via Browser) — „Aufwand" heißt hier also primär Paketgröße, Update-Gewicht
und Zahl der lokal zu betreibenden Dienste, nicht N×Installationen.

## 1. Was heute schon auf dem Mac mini läuft

| Funktion | Mechanik | Status |
|---|---|---|
| Chat + ALLE LLM-Hintergrundaufrufe | Provider „Lokal" → oMLX `http://<ip>:8000/v1` | seit v9.366.0 |
| MemPalace-Embedding | `mempalace.embedding_device: "remote"` → oMLX `/v1/embeddings` | seit v9.367.0 (CPU-ONNX nur Notfall-Fallback) |
| OCR / Bildbeschreibung | `ocr.engine: "local_vision"` → Vision-Modell auf oMLX | optional, dokumentiert |
| STT / TTS | oMLX-Audio-Endpoints bzw. Zusatz-Inferencer (MACMINI_SETUP.md §4) | optional |
| Reranker | Infinity auf dem Mini — **brain-seitiger Remote-Seam fehlt noch** | Follow-up (Seed: OFF) |

## 2. Bundle-Zerlegung (entpackt)

| Komponente | Größe | Inhalt |
|---|---|---|
| `python/` | 823 MB | Py 3.13 + site-packages (Top: pymupdf 97, spacy 83, chromadb-rust 61, de_core_news_md 60, en_core_web_md 54, kubernetes 41, duckdb 36, onnxruntime 33, sympy 29) |
| `venv-site/` | 607 MB | crawl4ai 547 (inkl. Playwright) + searxng 60 |
| `hf-cache/` | 315 MB | ONNX-Embedding-Modell (NUR Notfall-Fallback, primär ist remote) |
| `browsers/` | 294 MB | Chromium CfT + headless-shell (Zips, für crawl4ai) |
| `qdrant/` | 81 MB | qdrant.exe (Vektor-DB) |
| `app/` | 34 MB | Brain-Quellcode + Web-UI + agents-Skeleton |

## 3. Kategorie A — heute per Config auslagerbar (0 Codeänderung, **−1,3 GB / −60 %**)

Alle drei Dienste sprechen ausschließlich HTTP; Brain kennt sie nur als URL:

| Dienst | Seam | Mac-mini-Seite |
|---|---|---|
| **SearXNG** (60 MB venv) | `searxng.url` → `http://<mini>:8088`, `auto_start:false` (Supervisor no-op't, `_searxng_base_url()` liest nur die URL) | `python -m searx.webapp` als launchd-Dienst (läuft auf dem Dev-Mac heute genauso) |
| **crawl4ai-Render** (547 MB venv + 294 MB Chromium) | `crawl4ai.url` → `http://<mini>:8422`, `auto_start:false`; Ausfall degradiert graceful (web_fetch fällt auf markitdown zurück) | `render_service.py` + `.venv_crawl4ai` als launchd-Dienst |
| **Qdrant** (81 MB + RAM!) | `MEMPALACE_QDRANT_URL` (Env aus `BrainAgent.bat` / `brain-env.bat`) → `http://<mini>:6333` | natives macOS-Binary, launchd |
| **ONNX-Fallback** (315 MB hf-cache) | kein Dienst — weglassen = Verzicht auf Embedding-Offline-Resilienz (Mini down ⇒ Memory-Suche down statt „langsam auf CPU") | entfällt (remote ist ohnehin primär) |

**Verbleib auf Win11: ~860 MB** (python 823 + app 34) statt 2,15 GB.
Das neue setup.exe bietet genau das als **„Minimal-Installation"** (eine
Checkbox; patcht die URLs auf die Mac-mini-IP, überspringt Venv-/Browser-/
Qdrant-/hf-cache-Installation).

Gegenrechnung — warum das Voll-Profil trotzdem Default bleibt:
- **RAM auf dem Mini**: Chromium-Renders (Spitzen mehrere hundert MB) + Qdrant
  (~0,6 GB bei 70×10k Drawern, int8) konkurrieren mit dem Unified Memory des
  geladenen Chat-Modells. Vor einem Umzug: freies RAM auf dem Mini bei
  geladenem Modell messen.
- **SPOF-Verbreiterung**: heute nimmt ein Mini-Ausfall nur Chat + (schnelles)
  Embedding mit; im Minimal-Profil zusätzlich Websuche, JS-Rendering und die
  komplette Gedächtnis-Suche (Qdrant remote, ONNX-Fallback fehlt).
- **Betrieb**: 3 zusätzliche launchd-Dienste auf dem Mini (Setup dokumentiert
  in MACMINI_SETUP.md §6, aber es bleibt Betriebsverantwortung).

## 4. Kategorie B — mit kleinem Eingriff (Follow-ups, bewusst NICHT in dieser Welle)

| Kandidat | Ersparnis | Aufwand / Risiko | Empfehlung |
|---|---|---|---|
| **site-packages-Trim**: chromadb-rust 61 + kubernetes 41 + grpc 12 (+ Kleinteile) werden nur vom `mempalace==3.4.0`-Anker gezogen; Backend ist Qdrant, chromadb ist toter Code | ~120–150 MB | Anker per `--no-deps` + Handliste ersetzen; Import-Smoke auf Win nötig | lohnt, kleiner Folge-PR |
| **Reranker remote** (Infinity `/v1/rerank`) | 0 MB (ist eh OFF), +0.075 Retrieval-Score | kleiner Seam nach dem Muster des Remote-Embeddings | bekanntes Follow-up |
| **GDPR/PII-NER remote** (spacy 83 + de_md 60 + en_md 54 + thinc/blis ~32) | ~230 MB | HTTP-Wrapper + Client-Seam in `engine/pii_ner.py`; ABER: Scan liegt auf JEDEM Send im Pfad, und die Fail-Semantik ist giftig — Bank-Kernfeature ⇒ fail-closed ⇒ Mini-Ausfall würde jeden Chat blocken | **lokal lassen** |
| en_core_web_md (54 MB) weglassen | 54 MB | EN-PERSON-Pass (v9.349) verschwindet — Bank hat EN-Dokumente | drin lassen |

## 5. Kategorie C — bleibt sinnvollerweise auf Win11

Server selbst + SQLite-DBs (das Produkt), Dokument-Extraktion (pymupdf 97 MB),
DuckDB/data_query, code_graph (tree-sitter), XLSX-Toolset, doc_convert: alles
in-process-Bibliotheken auf Kern-Pfaden. Auslagern hieße neue Dienste + Netz-
Roundtrips + Fehlermodi für Funktionen, die lokal auf CPU problemlos laufen.

## 6. Radikal-Option (nur der Vollständigkeit halber)

Kompletter Brain-Server auf den Mac mini, Win11-Seite = Browser-Verknüpfung
(Installationsaufwand ≈ 0, Updates nur noch auf dem Mini). Dagegen sprechen
die Gründe der v9.366.0-Entscheidung: Bank-Daten lägen komplett auf dem
Apple-Gerät (Backup/Domänen-Policy), der Mini würde Voll-SPOF, und die
Tool-/Ingest-CPU-Last von 70 Usern konkurriert mit der Inferenz. Nur neu
bewerten, falls die Bank-Policy das Hosting auf dem Mini ausdrücklich erlaubt.

## 7. Empfehlung

1. **Default: Voll-Profil** (alles lokal außer LLM/Embedding) — maximale
   Resilienz, die 2,15 GB sind einmalig.
2. **Minimal-Profil als Option im setup.exe** (diese Welle): für Disk-/RAM-
   knappe Win-Boxen bzw. wenn der Mini ohnehin gemanagt wird — −60 % Paket.
3. Follow-ups in Reihenfolge des Nutzens: site-packages-Trim (−150 MB, risikoarm)
   → Reranker-Remote-Seam (Qualität) → NER bleibt lokal.
4. Nicht größenrelevanter Restaufwand auf Win11 (unverändert, nicht
   auslagerbar): Firewall-Freigabe 8420 (einmalig, admin), optional ODBC-MSI
   für MSSQL, optional Node.js für Mermaid.
