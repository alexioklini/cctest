# Mac mini M4 — Konfiguration für das Windows-11-Deployment

Stand v9.367.0. Der Windows-11-Client betreibt den kompletten Brain-Agent-Server;
der Mac mini M4 ist die **Inferenz-Box** im LAN. Diese Anleitung beschreibt, was
auf dem Mac mini laufen und konfiguriert sein muss, damit der Windows-Server
alles aufrufen kann.

## Was der Windows-Server vom Mac mini braucht

| Funktion auf Windows | Mac-mini-Dienst | Pflicht? |
|---|---|---|
| Chat / alle LLM-Hintergrundaufrufe (Zusammenfassungen, Klassifikator, Next-Prompt, Wiki, …) | oMLX `/v1/chat/completions` mit einem Chat-Modell | **Pflicht** |
| MemPalace-Gedächtnis-Embedding | oMLX `/v1/embeddings` mit `embeddinggemma-300m-bf16` | **Pflicht** (sonst CPU-Fallback auf dem Client, deutlich langsamer) |
| OCR gescannter Dokumente (`ocr.engine: "local_vision"`) | oMLX mit einem Vision-Modell (z. B. `GLM-OCR-8bit` oder `gemma-4-12B`-Vision) | Optional |
| Bild-Beschreibung bei Text-Modellen (`attachments.image_model`) | dito Vision-Modell | Optional |
| Sprach-Transkription (STT) | oMLX `/v1/audio/transcriptions` mit Whisper-Modell — sonst Zusatz-Inferencer, s. Abschnitt 4 | Optional |
| Podcast/Vorlesen (TTS) | oMLX `/v1/audio/speech` mit TTS-Modell — sonst Zusatz-Inferencer/Cloud, s. Abschnitt 4 | Optional |
| Retrieval-Reranker | Zusatz-Inferencer (Infinity), s. Abschnitt 4 — im Windows-Seed deaktiviert | Nein (Follow-up) |

Alles andere (GDPR/PII-NER, Dokument-Extraktion, Qdrant, Websuche, DuckDB,
Code-Graph) läuft CPU-seitig auf dem Windows-Client und braucht den Mac mini
nicht.

## 1. oMLX installieren und als Dienst einrichten

1. oMLX (macOS-App) installieren; beim ersten Start Admin-Dashboard öffnen.
2. **Server-Einstellungen** (`~/.omlx/settings.json` bzw. Admin-GUI):
   - `server.host: "0.0.0.0"` (sonst ist oMLX nur auf dem Mac selbst erreichbar!)
   - `server.port: 8000`
   - `auth.api_key`: derselbe Wert wie im Windows-`config.json` unter
     `providers.Lokal.api_keys[0].key` (Standard-Seed: `brain`).
   - `auto_start_on_launch: true`; oMLX als Login-Item registrieren, damit es
     nach einem Neustart automatisch läuft.
3. **macOS-Systemeinstellungen**:
   - Energiesparen: Ruhezustand deaktivieren („Bei Netzzugriff wach bleiben" /
     `sudo pmset -a sleep 0 displaysleep 10`).
   - Netzwerk: feste IP oder DHCP-Reservierung (die IP steht im Windows-
     `config.json`; ändert sie sich, dort in `providers.Lokal.base_url` UND
     `mempalace.embedding_url` nachziehen — oder `install.ps1` erneut laufen lassen).
   - macOS-Firewall: eingehende Verbindungen für oMLX erlauben (Port 8000).
   - Automatisches Einschalten nach Stromausfall: Systemeinstellungen →
     Energie → „Nach Stromausfall automatisch starten".

## 2. Modelle registrieren

Modell-Ordner liegen unter `~/.omlx/models/` (Unterordner erlaubt, z. B.
`mlx-community/<modell>`). Nach jedem Hinzufügen: Admin-GUI „Reload" oder

```bash
# Login mit dem api_key, dann Reload (ohne oMLX-Neustart, Warm-Cache bleibt):
curl -c /tmp/omlx.jar -X POST http://localhost:8000/admin/api/login \
     -H 'Content-Type: application/json' -d '{"api_key":"brain"}'
curl -b /tmp/omlx.jar -X POST http://localhost:8000/admin/api/reload
```

### 2a. Chat-Modell (Pflicht)

Z. B. `gemma-4-26B-A4B-it-MLX-4bit` oder `gemma-4-12B-it-qat-oQ4-fp16` — die
Modell-ID muss der Angabe entsprechen, die bei der Windows-Installation
(`install.ps1`-Abfrage „Modell-ID") gemacht wurde bzw. in den Windows-
Einstellungen als Standard-Modell gewählt ist. Download am einfachsten über
das oMLX-Admin-Dashboard (HF-Suche) auf dem Mac mini.

### 2b. Embedding-Modell (Pflicht)

```bash
# einmalig auf dem Mac mini (Internetzugang nötig, ~600 MB):
pip3 install -U huggingface_hub
hf download mlx-community/embeddinggemma-300m-bf16 \
   --local-dir ~/.omlx/models/mlx-community/embeddinggemma-300m-bf16
# danach Reload (siehe oben) und testen:
curl -s -X POST http://localhost:8000/v1/embeddings \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer brain' \
  -d '{"model":"embeddinggemma-300m-bf16","input":["test"]}'
# -> muss einen 768-dim Vektor liefern (kein "model not found")
```

Die Modell-ID `embeddinggemma-300m-bf16` ist im Windows-Seed als
`mempalace.embedding_remote_model` hinterlegt — bei abweichendem Ordnernamen
dort anpassen.

### 2c. Vision-Modell für OCR/Bildbeschreibung (optional, empfohlen)

`GLM-OCR-8bit` (OCR-spezialisiert) oder ein Vision-fähiges gemma registrieren.
Dann im Windows-`config.json`:

```json
"ocr": {"engine": "local_vision", "local_vision_model": "<modell-id>"}
```

### 2d. Whisper/STT und TTS (optional)

Zuerst prüfen, ob die installierte oMLX-Version das Modell direkt bedienen
kann — Whisper-MLX-Modell (z. B. `mlx-community/whisper-large-v3-turbo`,
HF-Repo OHNE `-mlx`-Suffix) registrieren und testen:
`curl -F file=@test.wav -F model=<id> http://localhost:8000/v1/audio/transcriptions`.
Auf Windows anschließend in den Einstellungen ein Transkriptions-Modell mit
Provider „Lokal" anlegen (NICHT `local-mlx-whisper` — das ist der In-Process-
Pfad des alten Mac-Servers). TTS analog über `/v1/audio/speech`.
**Kann oMLX das Modell nicht bedienen → Abschnitt 4.**

## 3. Erreichbarkeit vom Windows-Client prüfen

Auf dem Windows-Client (PowerShell):

```powershell
curl.exe http://<MACMINI_IP>:8000/v1/models -H "Authorization: Bearer brain"
```

Muss die Modellliste inkl. Chat-Modell und `embeddinggemma-300m-bf16` zeigen.
`install.ps1` führt diesen Test am Ende automatisch aus. Danach im Brain-Agent
Web-UI: Einstellungen → Diagnose (Doctor) — `Embedding device = remote` muss
grün sein; im Server-Log darf beim ersten Memory-Zugriff keine
„LATCHING to local ONNX fallback"-Warnung stehen.

## 4. Was oMLX nicht kann → geeigneter Zusatz-Inferencer auf dem Mac mini

Grundsatz (Betriebsentscheidung): Fähigkeiten, die oMLX nicht abdeckt, werden
durch einen ZUSÄTZLICHEN Inferencer-Dienst auf dem Mac mini gelöst — nicht auf
dem Windows-Client gerechnet. Brain spricht überall nur OpenAI-kompatible
HTTP-Endpoints; ein weiterer Dienst ist also nur ein weiterer Provider-Eintrag
(eigener Port) im Windows-`config.json`.

- **STT (falls oMLX kein Whisper bedient):** `mlx-whisper` hinter einem
  kleinen OpenAI-kompatiblen `/v1/audio/transcriptions`-Wrapper (FastAPI,
  eigener Port z. B. 8001, launchd-KeepAlive) — Metal-beschleunigt, dieselbe
  Bibliothek, die der bisherige In-Process-Pfad nutzt. Alternative ohne
  Metal: `speaches` (faster-whisper/CTranslate2, CPU — auf dem M4 für
  gelegentliche Diktate ausreichend). Windows-Seite: Transkriptions-Modell
  mit einem Provider anlegen, dessen `base_url` auf diesen Port zeigt.
- **TTS (falls oMLX kein TTS-Modell bedient):** Heute läuft TTS über
  Mistral-Cloud (`voxtral-mini-tts` via Provider `mistral-direct`) — das
  funktioniert auch vom Windows-Client, braucht aber Internet + Cloud-Freigabe.
  Lokale Alternative: TTS-MLX-Modell (z. B. Kokoro-MLX) hinter einem
  `/v1/audio/speech`-Wrapper analog zu STT. Wenn beides nicht gewünscht:
  Podcast/Vorlesen bleibt deaktiviert — kein sonstiger Funktionsverlust.
- **Reranker (`BAAI/bge-reranker-v2-m3`):** oMLX hat zwar `/v1/rerank`, aber
  kein MLX-Format dieses Modells; geeigneter Inferencer ist **Infinity**
  (`michaelfeil/infinity`, serviert Embeddings + Rerank, läuft auf Apple-MPS):
  `pip install infinity-emb[all]` + launchd-Dienst auf z. B. Port 8002.
  Brain-seitig fehlt dafür noch ein Remote-Rerank-Anschluss (heute lädt der
  Reranker in-process via sentence_transformers; im Windows-Seed deaktiviert)
  — das ist ein kleines Follow-up nach dem Muster des Remote-Embeddings.
  Kostenpunkt der Lücke: ~+0.075 Retrieval-Score, kein Funktionsausfall.
- **Bereits vorhanden:** Auf dem M4 läuft heute schon `vllm-metal`
  (Port 8012, Provider `Lokal-M4`) als zweiter Inferencer für
  Hintergrund-Tasks — das Muster „mehrere Inferencer, ein Provider-Eintrag
  pro Dienst" ist also etabliert.

## 5. Kapazität / Betrieb

- `providers.Lokal.max_concurrent` im Windows-`config.json` steuert, wie viele
  Chat-Turns gleichzeitig auf den Mac mini losgelassen werden (Seed: 1;
  bei gutem Batching-Verhalten des Modells vorsichtig auf 2 erhöhen —
  Embedding-Requests laufen daran vorbei und sind kurz).
- Für die 10-User-Testphase ist EIN Chat-Modell geladen zu halten sinnvoll;
  jedes zusätzlich geladene Modell kostet Unified Memory des M4.
- Log-Check auf dem Mac mini: oMLX-Admin → Logs (Fehler 401 = api_key-
  Mismatch mit dem Windows-config.json; 404 model not found = Reload vergessen).

## 6. Minimal-Profil des Windows-Setups: SearXNG, crawl4ai und Qdrant auf dem Mac mini

Nur nötig, wenn die Windows-Installation mit der Checkbox **„Minimal-Installation"**
eingerichtet wurde (spart ~1,3 GB auf dem Client — Abwägung siehe
`WIN_FOOTPRINT_ANALYSIS.md` im Windows-Bundle). Der Windows-Server erwartet die
drei Dienste dann auf der **gleichen IP wie oMLX**, Standard-Ports:

| Dienst | Port | Zweck auf Windows |
|---|---|---|
| SearXNG | 8088 | Websuche (`searxng_search` + Websuche-Tab) |
| crawl4ai-Render | 8422 | JS-Seiten → Markdown (`web_fetch`-Fallback; Ausfall degradiert graceful) |
| Qdrant | 6333 | MemPalace-Vektor-DB (Gedächtnis-Suche!) |

Einrichtung (einmalig, alle Dienste müssen auf **0.0.0.0** lauschen, nicht 127.0.0.1):

1. **Qdrant**: Release-Binary (macOS-arm64) oder `brew install qdrant`; als
   launchd-Dienst mit eigenem Storage-Pfad (z. B. `~/qdrant-brainwin/`) und
   `QDRANT__SERVICE__HTTP_PORT=6333`. Läuft auf dem Mini bereits ein Qdrant
   für andere Zwecke: zweite Instanz mit eigenem Port + Pfad, und den Port in
   `brain-env.bat` auf dem Windows-Client nachziehen. Optional absichern:
   `QDRANT__SERVICE__API_KEY` setzen (dann `MEMPALACE_QDRANT_API_KEY` in
   `brain-env.bat` ergänzen).
2. **SearXNG**: searxng-Checkout vom Build-Mac übernehmen, macOS-venv bauen
   (`python3 -m venv ~/.venv_searxng && … pip install -r searxng/requirements.txt`),
   Start `python -m searx.webapp` mit cwd im Checkout und einer settings.yml
   mit `bind_address: "0.0.0.0"`, Port 8088 (Muster: Produktions-Mac).
3. **crawl4ai**: `.venv_crawl4ai` wie auf dem Produktions-Mac (crawl4ai +
   playwright, `playwright install chromium`), Start `crawl4ai/render_service.py`,
   Port 8422, Bind 0.0.0.0.
4. macOS-Firewall: Ports 8088/8422/6333 für den Windows-Client freigeben.

**RAM-Warnung**: Chromium-Renders (Spitzen mehrere hundert MB) und Qdrant
konkurrieren mit dem Unified Memory des geladenen Chat-Modells — vor dem Umzug
freies RAM bei geladenem Modell messen.
