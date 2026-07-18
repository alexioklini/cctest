# Mac mini M4 — Konfiguration für das Windows-11-Deployment

Stand v9.376.0. Der Windows-11-Client betreibt den kompletten Brain-Agent-Server;
der Mac mini M4 ist die **Inferenz-Box** im LAN. Diese Anleitung beschreibt, was
auf dem Mac mini laufen und konfiguriert sein muss, damit der Windows-Server
alles aufrufen kann.

## Installationsvarianten auf der Windows-Seite — was der Mini jeweils leisten muss

Das Windows-Setup (`BrainAgent-setup.exe`; Quelle wahlweise Offline-Payload-Zip
airgapped oder Online-Download vom GitHub-Release/Mirror — **für den Mac mini
macht der Installationsweg keinen Unterschied**, nur das gewählte Profil zählt)
kennt zwei Profile:

| Windows-Profil | Pflicht auf dem Mini | Empfohlen / Optional auf dem Mini |
|---|---|---|
| **Voll-Installation** (Standard, ~2,1 GB auf dem Client) | oMLX: Chat-Modell + Embedding (§1–§3) | GLM-OCR-Wrapper (§4a) + Reranker via Infinity (§4) — beide im Windows-Seed aktiv; STT/TTS (§2d/§4) optional |
| **Minimal-Installation** (~0,9 GB auf dem Client) | zusätzlich SearXNG + crawl4ai + Qdrant (§6) | dito |

Windows-seitige Updates (setup.exe erneut ausführen bzw. app-only-Payload)
ändern am Mini nichts — die Dienste hier sind versionsunabhängig.

## Was der Windows-Server vom Mac mini braucht

| Funktion auf Windows | Mac-mini-Dienst | Pflicht? |
|---|---|---|
| Chat / alle LLM-Hintergrundaufrufe (Zusammenfassungen, Klassifikator, Next-Prompt, Wiki, …) | oMLX `/v1/chat/completions` mit einem Chat-Modell | **Pflicht** |
| MemPalace-Gedächtnis-Embedding | oMLX `/v1/embeddings` mit `embeddinggemma-300m-bf16` | **Pflicht** (sonst CPU-Fallback auf dem Client, deutlich langsamer) |
| OCR gescannter Dokumente (`ocr.engine: "mlx_ocr"` remote) | **GLM-OCR-Wrapper** auf Port 8003 (Abschnitt 4a) — volle Qualitätsparität zum Mac-Studio-In-Process-Pfad | Empfohlen (Windows-Seed: aktiv) |
| Bild-Beschreibung bei Text-Modellen (`attachments.image_model`) | dito GLM-OCR-Wrapper (Port 8003) oder ein Vision-gemma via oMLX | Optional |
| Sprach-Transkription (STT) | oMLX `/v1/audio/transcriptions` mit Whisper-Modell — sonst Zusatz-Inferencer, s. Abschnitt 4 | Optional |
| Podcast/Vorlesen (TTS) | oMLX `/v1/audio/speech` mit TTS-Modell — sonst Zusatz-Inferencer/Cloud, s. Abschnitt 4 | Optional |
| Retrieval-Reranker (bessere Gedächtnis-Treffer, +0.075 Score) | Infinity `/rerank` auf Port 8002, s. Abschnitt 4 — Windows-Seed: **aktiv/remote**; läuft der Dienst nicht, latcht der Client nach 2 Fehlversuchen automatisch auf die Vektor-Reihenfolge (kein Ausfall) | Optional (empfohlen) |

Alles andere (GDPR/PII-NER, Dokument-Extraktion, DuckDB, Code-Graph,
Shell-Befehle via `execute_command` — dafür liegt MinGit/bash im Windows-Bundle,
der Mini leistet dazu nichts — und in der **Voll-Installation** auch Qdrant +
Websuche) läuft CPU-seitig auf dem Windows-Client und braucht den Mac mini nicht.
In der **Minimal-Installation** liegen SearXNG, crawl4ai und Qdrant zusätzlich
auf dem Mini → Abschnitt 6.

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

### 2c. OCR gescannter Dokumente (empfohlen — der Windows-Seed erwartet es)

Auf dem Mac Studio läuft OCR in-process über das dedizierte Modell `GLM-OCR-8bit`
(schneller + genauer als ein Vision-Generalist). Für die Windows-Kombi gibt es
dafür einen kleinen **GLM-OCR-Wrapper** auf dem Mac mini (gleiches Modell, gleiche
Prompts, gleicher Output — nur über HTTP): **Einrichtung in Abschnitt 4a.** Der
Windows-Seed ist bereits darauf gestellt (`ocr.engine: "mlx_ocr"`,
`mlx_ocr_url: http://<MACMINI_IP>:8003`).

Alternativen ohne den Wrapper:
- `ocr.engine: "local_vision"` + ein Vision-fähiges gemma via oMLX (Port 8000) —
  langsamer/generalistischer, aber kein Zusatzdienst:
  ```json
  "ocr": {"engine": "local_vision", "local_vision_model": "gemma-4-12B-it-qat-oQ4-fp16"}
  ```
- `ocr.engine: "mistral_ocr"` (Cloud, beste Qualität, aber Dokumente verlassen das
  Haus — für Bankdaten i. d. R. nicht zulässig).

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

Die beiden Wrapper-Dienste liegen im Repo unter `packaging/macmini/`
(`glm_ocr_server.py`, `whisper_stt_server.py`) — auf den Mac mini kopieren.

### 4a. GLM-OCR-Wrapper (Port 8003) — volle OCR-Parität ohne Cloud

Bedient dasselbe `GLM-OCR-8bit`, das der Mac-Studio-Server in-process nutzt,
hinter einem OpenAI-kompatiblen `/v1/chat/completions`-Vision-Endpoint. Brain
schickt das Bild als base64 (kein geteiltes Dateisystem nötig); Modell, Prompts
und Rückgabeform sind identisch zum In-Process-Pfad → gleiche OCR-Qualität.

```bash
# Voraussetzung: mlx-vlm im System-Python (oder eigenem venv) des Mac mini:
pip3 install --break-system-packages mlx-vlm
# Start (foreground-Test):
python3 packaging/macmini/glm_ocr_server.py --host 0.0.0.0 --port 8003 \
        --model mlx-community/GLM-OCR-8bit
# Funktionstest (vom Mac mini): ein Foto mit Text durchschicken:
IMG=$(base64 -i test.png)
curl -s -X POST http://localhost:8003/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"mlx-community/GLM-OCR-8bit\",\"max_tokens\":512,\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Text Recognition:\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,$IMG\"}}]}]}"
# -> {"choices":[{"message":{"content":"<erkannter Text>"}}]}
```

Als launchd-Dienst mit KeepAlive einrichten (analog zu oMLX). Optionaler
Bearer-Schutz: `OCR_API_KEY` in der Env setzen und im Windows-`config.json` unter
`ocr.mlx_ocr_api_key` denselben Wert eintragen. Der erste Request lädt das Modell
(~1,5 GB Unified Memory) und ist langsamer; danach bleibt es warm. Steht der
Dienst nicht, meldet Brain pro Dokument einen OCR-Fehler und extrahiert ohne
OCR-Text weiter — kein Absturz.

- **STT (falls oMLX kein Whisper bedient):** `mlx-whisper` hinter dem
  mitgelieferten Wrapper `packaging/macmini/whisper_stt_server.py`
  (OpenAI-kompatibel `/v1/audio/transcriptions`, eigener Port z. B. 8001,
  launchd-KeepAlive) — Metal-beschleunigt, dieselbe Bibliothek wie der
  In-Process-Pfad:
  ```bash
  pip3 install --break-system-packages mlx-whisper
  python3 packaging/macmini/whisper_stt_server.py --host 0.0.0.0 --port 8001 \
          --default-model mlx-community/whisper-large-v3-turbo
  ```
  Alternative ohne Metal: `speaches` (faster-whisper/CTranslate2, CPU). Windows-
  Seite: ein Transkriptions-Modell mit einem Provider anlegen, dessen `base_url`
  auf `http://<MACMINI_IP>:8001/v1` zeigt (NICHT `local-mlx-whisper`).
- **TTS (falls oMLX kein TTS-Modell bedient):** Heute läuft TTS über
  Mistral-Cloud (`voxtral-mini-tts` via Provider `mistral-direct`) — das
  funktioniert auch vom Windows-Client, braucht aber Internet + Cloud-Freigabe.
  Lokale Alternative: TTS-MLX-Modell (z. B. Kokoro-MLX) hinter einem
  `/v1/audio/speech`-Wrapper analog zu STT. Wenn beides nicht gewünscht:
  Podcast/Vorlesen bleibt deaktiviert — kein sonstiger Funktionsverlust.
- **Reranker (`BAAI/bge-reranker-v2-m3`) — seit v9.376.0 remote angebunden:**
  oMLX hat zwar `/v1/rerank`, aber kein MLX-Format dieses Modells; der passende
  Inferencer ist **Infinity** (`michaelfeil/infinity`, läuft auf Apple-MPS).
  Einrichtung auf dem Mini:

  ```bash
  python3 -m venv ~/.venv_infinity
  ~/.venv_infinity/bin/pip install "infinity-emb[all]" "optimum<1.24" "click<8.2"
  #  ^ BEIDE Pins sind Pflicht (Stand 07/2026): optimum>=1.24 hat
  #    bettertransformer entfernt (Import-Crash beim Start), click>=8.2
  #    bricht die typer-CLI ("Secondary flag is not valid...").
  ~/.venv_infinity/bin/infinity_emb v2 --model-id BAAI/bge-reranker-v2-m3 \
      --port 8002 --host 0.0.0.0    # als launchd-Dienst mit KeepAlive einrichten
  # Funktionstest:
  curl -s -X POST http://localhost:8002/rerank -H 'Content-Type: application/json' \
    -d '{"model":"BAAI/bge-reranker-v2-m3","query":"test","documents":["a","b"]}'
  ```

  Windows-Seite: im Seed bereits gesetzt — `mempalace.reranker = {enabled: true,
  device: "remote", url: "http://<MACMINI_IP>:8002", model:
  "BAAI/bge-reranker-v2-m3"}` (install.ps1 trägt die IP ein). Score-Parität zum
  bisherigen in-process-Pfad ist live verifiziert (identische Ordnung, max
  |diff| = 0.000000 — beide Seiten sigmoid-normiert). Läuft der Dienst nicht,
  latcht der Windows-Client den Remote-Reranker nach 2 Fehlversuchen prozessweit
  ab: Gedächtnis-Suche behält die Vektor-Reihenfolge, kein Ausfall, nur ~−0.075
  Retrieval-Score. Speicher auf dem Mini: Prozess-RSS ~0,2 GB + Modellgewichte
  im Unified Memory (grob 1–2 GB) — neben dem Chat-Modell einplanen.
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
