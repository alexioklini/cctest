---
name: project_workflow_upload_prompt_regression
description: v9.291.1-.2 Workflow-Upload-Karte + Steuerleiste (Pause/Resume/Stop) + Komposer-Ausblenden waehrend Lauf
metadata: 
  node_type: memory
  type: project
  originSessionId: 2f7e636b-80fa-4990-9773-7fe1faec68d9
---

## v9.291.2 Runde 3 — Live-Fortschritt als echte Chat-Tool-Zeilen
User wollte: Tool-Calls WIE IM CHAT rendern (welcher Befehl/Suche/Fetch), keine Doppelzeilen (in-progress+done), LLM-Output+Thinking, WELCHER Workflow-Schritt läuft; + Bug: nach Schritt-Ende sind im Turn alle Tool-Infos WEG; + Runden-Limit → "No response was returned" (ganzer Lauf failt).

**Fixes:**
- **Strukturierte Events statt String-Log**: `WorkflowExecution._live_progress` hält jetzt `_wf_events` (ordered [{kind:tool_call{name,args,tool_use_id} | tool_result{result,is_error,duration_ms} | text | thinking}]). Frontend `_wfLiveRows` expandiert sie in ECHTE Message-Rows → der normale Chat-Renderer zeichnet sie: tool_call+tool_result-Paar → `renderToolCall` = `.tool-line` (NICHT `.tool-block`!) mit `toolDescribe` ("Befehl ausführen: `cp …`", "Im Web suchen nach …", "Datei lesen: …"), ✓/Spinner, Timing, Klick→Aktivitäts-Panel; `assistant_segment`→Text inline; `thinking`→Denkblock. GOTCHA: der Chat rendert Tool-Calls als kompakte `.tool-line` (Details im Aktivitäts-Panel), NICHT als `.tool-block` — meine erste E2E-Assertion suchte die falsche Klasse.
- **Step-Label = Instruktion**: agent_step setzt `begin_live_progress(label=erste Instruktionszeile)`; `_wfRunStatusLine` zeigt "Schritt (Zeile N): <Instruktion>". Step-Detail hilft NICHT (args sind zu `…` redigiert) — Label muss von agent_step kommen.
- **PERSISTENZ (Verschwinde-Bug)**: `end_live_progress` LÖSCHTE den Live-Turn bei Schritt-Ende → nur blanke Antwort blieb, Tools weg. NEU `finalize_live_progress(text,model,files)`: friert Live-Turn IN PLACE ein (`_wf_live`→false, Antworttext, `_wf_events` BLEIBEN); `_capture_transcript` erkennt `_transcript_done`, erfasst KEINE zweite Assistenten-Zeile, fügt User-Instruktion via `insert_user_before_last_answer` davor. `_wfSyncTranscriptMessages` expandiert JEDEN Turn mit `_wf_events` (live→Status+Buttons, final→Antworttext als letzte Zeile).
- **Runden-Limit-Rettung**: leere Endantwort + stop_reason=max_rounds (oder vorhandene tool_events) fällt NICHT mehr auf `_err('empty reply')` (das den GANZEN Lauf failte → "No response was returned"); Salvage-Text + "[Hinweis: Runden-Limit erreicht]" + Tools/Dateien bleiben. Nur wirklich leer+ungecappt failt noch.
- **emit-Durchreichung**: `run_turn_blocking`+`background_call` nehmen optionales `emit`-Callback (Default no-op) → agent_step gibt `execution.live_progress_event` rein.

E2E im Browser: `✓ Befehl ausführen: \`cp /tmp/…\`  0.5s` als `.tool-line`. GOTCHA: headless-Browser-Assetload gegen Dev-Server wirft sporadisch ERR_CONNECTION_RESET → Tests mit `waitUntil:'domcontentloaded'`+Delay statt 'load', 1 Retry.

## v9.291.2 Korrekturrunde (User-Feedback nach erstem .2-Wurf)
- **Status-Zeile bleibt, Buttons DANEBEN** (nicht ersetzen!): User war explizit — die Chat-Zeile "Workflow-Lauf in Bearbeitung …" ist OK, wollte NUR Buttons dazu. Erster Wurf ersetzte sie durch eine separate Leiste → falsch. Jetzt: synthetischer Status-Turn bleibt (`_wfSyncTranscriptMessages`), markiert `_wfControls`; `renderAssistantMessage` hängt via `wfRunControlsHtml(paused)` Pause↔Fortsetzen + Stopp INLINE in den Nachrichten-Block. LEKTION: wenn der User sagt "X ist ok, wir brauchen nur Y", NICHT X umbauen.
- **LIVE-FORTSCHRITT** (User: "agent_step läuft still im Hintergrund, keine Ausgabe bis fertig"): agent_step war blockierender `background_call` OHNE event_callback. FIX-BACKEND: `run_turn_blocking`+`background_call` nehmen optionales `emit`-Callback (per-Runde, Default no-op); agent_step öffnet Live-Transcript-Zeile (`WorkflowExecution.begin_live_progress`/`live_progress_event`/`end_live_progress`), speist `tool_call`/`tool_result`/`text_delta` → Poll zeigt "🔧 execute_command / ✓ … / 🔧 python_exec …". FIX-FRONTEND (der Live-Inhalt war zuerst UNSICHTBAR — der KRITISCHE Teil): `renderTurnBody` rendert nur die LETZTE Assistenten-Antwort einer Anfrage als Hauptantwort, frühere werden in die zugeklappte "Aktivität"-Sektion gefaltet. Der Live-Turn war eine SEPARATE Nachricht VOR dem Status-Turn → verschwand. FIX: `_wfSyncTranscriptMessages` legt Status-Präfix + `_wfControls`-Buttons AUF den `_wf_live`-Transcript-Eintrag (liveIdx) → EIN sichtbarer letzter Block trägt Status+Live-Tools+Buttons; eigenständige Status-Zeile nur wenn (noch) kein Live-Turn existiert. E2E im Browser: DOM zeigt "🔧 execute_command" tickweise (i4: tool=true, len 160→198). GOTCHAS: 22-Byte-Fake-JPEG → Provider 400 "invalid image format", nutze PIL für echtes JPEG. Backend-transcript hatte `_wf_live`-Eintrag korrekt — Bug war rein im Turn-Grouping des Chat-Renderers (letzte-Antwort-gewinnt).
- **KOMPOSER-SICHTBARKEIT ZENTRALISIERT** (Bug: nach Stopp blieb Komposer versteckt): war an mehreren Stellen in `_wfRenderRunControls` getoggelt; ein async Nach-Render (`refreshWorkflowArtifacts`) ließ ihn stale. FIX: EINMAL am Ende von `renderWorkflowRunUI` aus einziger Wahrheit (`_wfRunActive() && live`) ableiten. LEKTION: eine DOM-Eigenschaft = EIN Setz-Punkt; verteiltes Toggeln + async Re-Render = stale State.

## v9.291.2 (erster Wurf — die Basis-Fixes)
9.291.1 renderte die Upload-Karte in `#messages-container`, das der 800ms-Poll (`renderWorkflowRunUI`→`renderMessages`) JEDEN Tick neu aufbaut → `<input type=file>` mitten im Auswählen abgehängt, OS-Dialog verlor Bindung, `onchange` feuerte ins Leere (**JPG wählen → nichts passiert**).

**Fixes (web/js/workflows.js + index.html + brain.py + engine/workflow.py + server.py + handlers/admin_workflows.py):**
- **Stabiler Host** `#wf-upload-host` jetzt FEST in `.chat-input-area` (index.html), den `renderMessages()` NIE anfasst. Inner-HTML nur neu bei Zustands-Key-Wechsel (`dataset.wfKey`: `upload:<prompt> <accept>` bzw. `run:<paused>`) → gleiche Pending-Prompt über Poll-Ticks = No-op, gewählte Datei / offener Dialog bleiben erhalten. Funktion `_wfRenderRunControls` (ersetzt `_wfRenderPendingUpload`).
- **Komposer ausgeblendet während LIVE-Lauf** (`_wfSetComposerHidden`: `#chat-composer-mount` + Disclaimer); terminaler Lauf / `wfBannerHide` → Komposer zurück. Chatten mitten im Lauf unerwünscht.
- **3 Buttons** auf der Upload-Karte: `Abbrechen`=ganzen Lauf stoppen (`wfBannerCancel`), `Zurücksetzen`=nur Dateiauswahl löschen (`wfResetUpload`, ex-`wfCancelUpload`), `Hochladen`.
- **Steuerleiste** (`wfRenderRunControlBar`) bei live+unblockiert: Spinner + Pause/Fortsetzen + Stopp — ersetzt die bloße `⏵ in Bearbeitung`-Chat-Zeile (im transcript-synth entfernt).
- **Backend Pause/Resume** (brain.py `WorkflowExecution`): `_pause` (threading.Event, gesetzt=laufend) + `_paused`-Flag; `pause()`/`resume()`/`_pause_wait()`; Interpreter (`engine/workflow.py run()`) blockiert KOOPERATIV am nächsten Top-Level-Node (weckt bei resume ODER cancel; ein laufender agent_step-LLM-Turn läuft erst zu Ende — kein Mid-Generation-Kill). `cancel()` setzt `_pause` frei, damit ein pausierter Lauf aufwacht + Cancel sieht. `to_dict()`→`paused`. Endpunkte `POST /v1/workflows/executions/{id}/pause|resume`.

**Alles E2E in Playwright verifiziert** (echter Browser, nicht nur Node): JPG wählen übersteht Poll-Ticks → Submit aktiv → Hochladen entblockt Lauf; Reset leert Auswahl; Abbrechen stoppt + Komposer zurück; pause→paused=True, resume→paused=False, cancel→cancelled.

**GOTCHA (Test-Harness):** `API` ist eine FUNCTION, kein object → `typeof API==='object'` im readyState-wait hängt ewig (`typeof API==='function'` nutzen). `state`/`API`/`wfOpenDetail` sind bare lexical globals (NICHT auf window). `modelsConfigReady` braucht nach Server-Restart ein paar Sekunden — sonst Login-Wait-Flake (auch im smoke-gate dokumentiert).

## v9.291.1 (unvollständig)
Beim Start eines Workflows mit `ask_user_for_file` (z. B. ausweispruefung-manipulationserkennung) erschien KEIN Upload-Popup — der Lauf blockierte still bis 300s-Timeout.

**Root cause:** Der Workflow-Lauf-als-Chat-Umbau ([[project_workflow_run_chat_view]], v9.290.x/9.291.0) ersetzte das alte step-basierte `renderWorkflowBanner` durch transcript-basiertes Chat-Rendering (`_wfSyncTranscriptMessages`). Die Upload-Erkennung ging ersatzlos verloren: `wfRenderUploadPrompt` + `wfParseAskFileDetail` blieben DEFINIERT, aber von NIRGENDS aufgerufen (0 Call-Sites, per grep). 9.291.1 hängte sie wieder ein — aber in den falschen (instabilen) Container, daher 9.291.2.

**LESSON:** Beim Umbau eines Render-Pfads prüfen, welche Detektions-/Sonderfall-Zweige des alten Pfads im neuen fehlen (`grep` auf Call-Sites definierter Funktionen fängt verwaiste Feature-Logik). DOM mit eigenem Lifecycle (file-input, laufende Auswahl) gehört in einen STABILEN Container, nicht in einen, den ein Poll-Loop neu aufbaut. E2E IM BROWSER, nicht nur py_compile/Node — siehe [[project_workflow_run_chat_view]].
