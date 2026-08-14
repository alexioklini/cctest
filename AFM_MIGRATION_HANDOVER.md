# Handover: AFM-Migration der LLM-Calls + Embeddings an den Router

Stand 14.08.2026, v9.424.0. Kontext: Die Apple-Lanes (SpeechAnalyzer-STT,
FM-Partial-Übersetzung) sind produktiv; dieses Dokument beschreibt die
NÄCHSTEN Schritte — welche Brain-LLM-Calls auf das Apple Foundation Model
(`apple-fm-system` im Router) umziehen können, und wie die MemPalace-
Embeddings über den llm-router laufen sollen. Nichts davon ist umgesetzt;
alles ist config-/kleinteilig und einzeln rückholbar.

## UMSETZUNGSSTAND (13.08. abends, Claude-Session)

- ✅ `apple-fm-system` als Brain-Modell angelegt (provider llm-router-local,
  is_local, thinking_format none, capabilities [chat], max_context 4096).
- ✅ `chat_summary_model` + `next_prompt_model` → apple-fm-system, LIVE
  verifiziert (Titel + Ghost via Router-request_log, Deutsch auf deutschem
  Inhalt gut; bei gemischtsprachigem/technischem Inhalt kippt der Titel
  gelegentlich ins Englische — beobachten).
- ❌ `classifier_model` ZURÜCK auf mistral-small-latest: fm-serve lehnt in
  Beta 5 JEDEN erzwungenen Tool-Call ab ("An unsupported generation guide
  was used") — auch ein Ein-Feld-String-Schema ohne Enum/Array. Der
  Klassifikator braucht `forced_tool` → AFM derzeit unmöglich.
  **URSACHE wahrscheinlich geklärt** (Apple-Forums-Thread 841654): bekannte
  Beta-5-Regression — Tool-Calling bricht nach dem Update durch
  unvollständig geladene System-Language-Model-Assets; laut Apple-Staff kein
  API-Limit. Abhilfe: Gerät NEU STARTEN + 5–10 Min Assets nachladen lassen
  (fm-serve-Daemon-Restart allein reicht NICHT, 13.08. geprüft). Nach dem
  M4-Reboot forced-tool-Probe wiederholen; bei Erfolg classifier_model
  wieder auf apple-fm-system.
- ⚠️ fm-serve antwortet IMMER als SSE-Stream, auch bei `stream:false` →
  non-streaming Calls über den Router geben 502 "Upstream lieferte kein
  JSON". Brains Loop streamt immer → für Brain-Zwecke egal, aber für
  Router-Direktnutzer relevant (ggf. im Router aggregieren).
- ✅ Embeddings via Router: Router-Modellzeile `embeddinggemma-300m-bf16`
  (Provider Lokal) angelegt; Auth-Frage geklärt — der Remote-Embedder sendet
  `Authorization: Bearer $MEMPALACE_EMBEDDING_API_KEY` (kein venv-Patch
  nötig), Plist auf `http://127.0.0.1:8424` + Router-Key umgestellt, Brain
  neu gestartet (läuft jetzt 9.424.0). Read- UND Write-Pfad verifiziert
  (mempalace_query + Wiki-Mirror-Write, je request_log `endpoint=embeddings`),
  kein ONNX-Latch. Rollback: Plist-URL zurück auf `:8000`, Key auf `brain`.
- 🔎 NEBENBEFUND: der Remote-RERANKER (M4 :8002, infinity) timeoutet auf der
  jeweils ersten echten Rerank-Anfrage nach Brain-Restart (Health 20ms, Latch
  nach 2 Fehlern → Rerank für den Prozesslauf AUS — historisch oft passiert,
  auch in diesem Lauf 1×). Vermutlich Modell-Kaltladung > Brain-Timeout.
  Fix-Kandidaten: Timeout hoch / Reranker-Warmup nach Restart.

## Faktenlage (verifiziert 14.08.)

- **Kein On-Device-Quota**: `fm quota-usage` → "System: Not applicable
  (quota only applies to PCC)". Volumen ist KEIN Blocker, auch nicht für
  kg_extract (140 Calls/Tag).
- **AFM-Qualität** (10-Segment-A/B + Bank-Livetest): Ø 0,70 s, ANE (keine
  GPU-Last), Kurztext-Klassifikation/Titel-Niveau sicher; EN→DE-Übersetzung
  Vorschau-Klasse. **Guardrail-Quote im Bank-Test: ~1 %** der Übersetzungs-
  Sessions (harmlos bei fail-open-Zwecken, K.O. für fail-closed-Pfade).
- **Call-Profil 30 Tage** (agents/main/costs.db, cost_log.purpose):
  kg_extract 4098 · chat 2123 · auto_route_classify 606 · chat_summary 564 ·
  moa_reference 439 · next_prompt 186 · wiki 52 · drift_check 39 ·
  user_profile 30 · translate_text 12.

## 1. Sofort-Switches (Config-only, kein Code)

Vier Zwecke auf `apple-fm-system` umstellen — alle über **Einstellungen →
Service-Modelle** (bzw. `POST /v1/services/models`), alle einzeln rückholbar:

| Slot | Zweck (purpose) | Calls/30d | Risiko |
|---|---|---|---|
| `classifier_model` ("Prompt-Klassifikation (Auto-Routing)") | auto_route_classify + drift_check | 606+39 | gering — Fehlermodus = Fallback auf Defaults; NIE das Modell geroutet, nur Tools |
| `chat_summary_model` | chat_summary (Titel) | 564 | keins |
| `next_prompt_model` | next_prompt (Ghost-Vorschlag) | 186 | keins (Deutsch-Qualität stichprobenartig prüfen) |

Verifikation je Switch: einen Testcall auslösen (neuer Chat → Titel; Tab im
leeren Composer → next_prompt; beliebiger Turn → Klassifikator-Zeile im
Session-Inspector) und im Router-Log (`request_log`, model=apple-fm-system)
gegenprüfen. Rollback: Slot zurückstellen. WICHTIG: `apple-fm-system` muss
dafür als Brain-Modell angelegt sein (ist im ROUTER registriert, in Brains
Modellkatalog noch NICHT — via `POST /v1/models/config action=update`
anlegen wie gehabt: provider `llm-router-local`, is_local, thinking_format
none, capabilities ["chat"]).

## 2. A/B-Kandidaten (nicht ohne Eval umstellen)

- **kg_extract** (4098 Calls, größter GPU-Entlaster): AFM kann per Guided
  Generation (`@Generable`) erzwungene Schemas — technisch passend. ABER:
  KG-Qualität trägt das Projekt-Retrieval → Vorbedingung ist der
  Retrieval-Eval-Harness (Backlog `backlog_retrieval_eval`). Erst Eval,
  dann A/B auf einem Projekt, dann entscheiden.
- **wiki** (Tags/Zusammenfassungen, 52 Calls): AFM hat den dedizierten
  `contentTagging`-Use-Case (`fm respond --use-case content-tagging`).
  Kleiner A/B genügt.

## 3. Bewusste No-Gos

- **chat / scheduled / moa_*** — interaktive Qualität, bleibt 12B/Cloud.
- **user_profile** — Grounding-kritisch (Memory-Lektion: GROUNDING).
- **ARL-Dokumentklassifikation** — fail-closed-Sicherheitspfad; niemals an
  ein Modell mit Apple-Guardrails hängen (1-%-Blockrate = stilles
  Sicherheitsrisiko in beide Richtungen).
- **refine / Übersetzungs-FINALE** — Qualität (A/B hat 12B klar vorn gezeigt).

## 4. Embeddings an den Router

**IST** (in `~/Library/LaunchAgents/com.brain-agent.server.plist`):
```
MEMPALACE_EMBEDDING_DEVICE = remote
MEMPALACE_EMBEDDING_URL    = http://192.168.1.214:8000   ← oMLX DIREKT
MEMPALACE_EMBEDDING_REMOTE_MODEL = embeddinggemma-300m-bf16
```
Die Embeddings laufen also bereits remote auf dem M4, aber **am Router
vorbei** (kein Key, keine Metrics, zweite Stelle beim M4-Umzug).

**SOLL**: URL auf den Router (`http://127.0.0.1:8424`), damit Auth,
request_log und der Bank-Umzug (eine Stelle: Router-Provider-URLs) auch für
Embeddings gelten. **Kein Re-Embedding nötig** — Modell und damit Vektoren
bleiben identisch.

Schritte:
1. Router-Modellzeile anlegen: `embeddinggemma-300m-bf16` → Provider `Lokal`
   (fehlt aktuell! `/v1/embeddings` löst über die Registry auf).
2. **ZU KLÄREN**: Sendet MemPalaces Remote-Embedder einen
   `Authorization`-Header? (Paketquelle prüfen: `grep -rn
   MEMPALACE_EMBEDDING mempalace-Paket/`; das Env-Mapping liegt in dessen
   server.py ~Z. 3771.) Falls nein → venv-Patch nach dem Muster
   [[project_mempalace_venv_patches]] (Bearer aus neuem Env
   `MEMPALACE_EMBEDDING_API_KEY`), NICHT den Router keyless machen.
3. Plist-Env ändern → Brain sanft neustarten (`launchctl kill SIGTERM …`,
   NIE kickstart -k).
4. Smoke: `mempalace_query` (Read-Pfad) + einen Drawer schreiben
   (Write-Pfad) + Router-`request_log` zeigt `endpoint=embeddings`.
5. Rollback: URL zurück auf `:8000`.

**Nicht verwechseln**: Apples NLEmbedding/NLContextualEmbedding sind KEINE
Alternative zu embeddinggemma (Klassifikations-, nicht Retrieval-Embeddings;
geprüft 14.08.) — es bleibt bei embeddinggemma, nur der Transportweg ändert
sich.

## 5. Offen bei Übergabe

- **macOS 27 Beta 5 auf dem M4** (26A5406e): Paket heruntergeladen, Install
  braucht `sudo … --restart` (Root). Danach: **OCR-Retest**
  (`~/.omlx/apple_ocr_cli /tmp/testdoc.png` — Beta 4 lieferte 0 Ergebnisse,
  TextRecognition-Bundles ohne ANE-Varianten); bei Erfolg
  `apple-vision-ocr` im Router registrieren (Provider-Zeile existiert noch
  nicht; Server läuft als launchd `com.brain-agent.apple-ocr` :8006) und
  Tabellen-A/B gegen GLM-OCR fahren.
- CLT Beta 5 ist installiert → Swift-Toolchain kann jetzt auch die
  27er-SDK-Interfaces bauen (FoundationModels-`capabilities`-Probe möglich).

## Referenz: M4-Dienste (alle launchd, gui/501)

| Dienst | Port | Zweck |
|---|---|---|
| com.brain-agent.omlx-serve | 8000 | Gemma/EuroLLM/Llama + Embeddings (GPU) |
| com.brain-agent.whisper-stt | 8001 | Whisper/Voxtral Batch-STT |
| — (im STT-Prozess) | 8003 | Voxtral-Streaming-WS |
| com.brain-agent.speech-stream | 8004 | SpeechAnalyzer-Streaming-WS (Dual-Lane, language-Feld) |
| com.brain-agent.fm-serve | 8005 | Apple FM Chat-Completions |
| com.brain-agent.apple-ocr | 8006 | Vision-OCR (blockiert bis Beta 5) |

Commits dieser Woche: cctest `1d5e3fc4`…`526ff84d` (v9.414.2–9.424.0),
llm-router `5357c12`…`bcaa626`. Betriebswissen im Claude-Memory:
`project_m4_stt_omlx_ops`.
