---
name: project_workflow_run_chat_view
description: "v9.290.2 — Workflow-Lauf zeigt LLM-Output als echte Chat-Nachrichten (transcript-Kanal), Artefakte zuverlässig im Panel; steps sind NUR Debug-Trace"
metadata: 
  node_type: memory
  type: project
  originSessionId: 01778bad-d744-4aff-8af1-720179c3af78
---

Kernerkenntnis: **Workflow-`steps` ≠ Chat-Inhalt.** engine/workflow.py:_summary kürzt jedes step-detail auf 120 Zeichen und redigiert Call-Args zu `k=…`. Der volle agent_step-Text (der eigentliche Report/LLM-Output) und sogar die write_file-Ausgabepfade werden dadurch abgeschnitten — sind in steps_json STRUKTURELL NICHT vorhanden. steps ist ein Diagnose-Trace (gehört in den Protokoll-Reiter), NIE die Chat-Anzeige.

Der erste 9.290.1-Versuch (steps als Chat rendern, "client-side, keine Backend-Änderung") war FALSCH: zeigte nur gekürzte Tool-Aufrufe statt LLM-Output, Artefakte fehlten. Node-Harness ("Funktion wirft nicht") verpasste das — E2E in Safari nötig. LESSON: [[feedback_depth_over_speed]] + bei UI-Änderung im echten Browser gegenschauen, nicht nur Funktionen smoke-testen.

FINALE Architektur (v9.290.2, backend + frontend):
- BACKEND ungekürzte Kanäle: `WorkflowExecution.transcript` (echte user/assistant-Turns) + `.output_paths` (volle Pfade), befüllt vom Interpreter in `_capture_transcript` (engine/workflow.py:_eval_call, aus dem VOLLEN parsed-Result VOR _summary): ask_user_for_file→user-Turn, agent_step→assistant-Turn (voller text+model+files), write_file/edit_file→output_path. Persistiert transcript_json/output_paths_json (additive ALTER TABLE in _workflow_history_init; finalize schreibt, to_dict exponiert live, history-GET dekodiert). `_workflow_run_paths_classified` nutzt output_paths_json als AUTORITATIVE Output-Quelle → Artefakte seeden zuverlässig (die kürzungs-lossy Regex war die "leeres Panel"-Ursache; return_value ist nur blanker Dateiname ohne '/').
- FRONTEND `_wfSyncTranscriptMessages()` (web/js/workflows.js): injiziert data.transcript als ECHTE user/assistant chat.messages-Zeilen (`_wfSynthetic`, nicht persistiert, PREFIX vor echten Folge-Nachrichten) → normaler renderMessages() rendert byte-identisch zu jedem Chat (User-Ziel "wie normaler Chat in Font/Farben/Abständen"). NICHT an den Wire: streamChat sendet nur text+session_id, Server lädt History aus DB → synthetische Zeilen erreichen LLM NIE. Alt-Läufe ohne transcript: Fallback aus return_value/error.
- DREI rechte Reiter (WF_TAB_NAMES, display:none, `updateWorkflowTabs()` nur bei Lauf-Chat; ZUSÄTZLICH zu Normal-Reitern): wf-statistik/wf-quellcode/wf-protokoll (letzterer = der Debug-Trace). Artefakte = bestehender Dateien-Reiter. Verdrahtet in panels_right.js.
- GOTCHA: Live-Endpoint `/executions/{id}` liefert `agent` (nicht agent_id) + KEIN workflow_source; History `/history/{id}` liefert agent_id + workflow_source + steps_json/transcript. Statistik liest `agent_id || agent`.
- BEIFANG-FIX: wfDetailDownloadTranscript + wfUploadFile lasen tote wfState.detailRun/.currentExecId/.detailFollowups (kaputt seit v8.24.2). Entfernt: #workflow-run-banner, renderWorkflowBanner, buildWorkflowRunBlock, wfRunToggleCollapse, wfBannerToggleTrace + custom wf-run-*-CSS im Hauptbereich. wfRenderUploadPrompt bleibt tot (kein Caller).

NACHTRÄGE v9.290.3/.4 (echter Dialog + Cards + Token):
- agent_step schreibt jetzt ZWEI transcript-Turns: user-Turn aus `instruction` (= 'die Anfrage', mit kwargs.files als 📎-Anhänge) + assistant-Turn (Antwort). So liest es sich als echtes Q&A statt kopfloser Antwort.
- Workflow-seitiges write_file (content=r.text, eigener DSL-Node NACH agent_step) hängt seinen Output via `attach_output_to_last_answer(path)` an den letzten assistant-Turn → rendert als klickbare artifact-card IM Fluss. Frontend: _wfSyncTranscriptMessages mappt transcript-files per Basename auf geseedete Artefakte (state.artifacts) → baut msg._files (mit artifact_id) → normaler Chat-Renderer zeichnet .artifact-card (openArtifactPanel). refreshWorkflowArtifacts re-synct+rendert sobald Artefakte nach 1. Render geladen (sonst leerer artByName-Index → nur plain file-card).
- Token/Kosten: Statistik-Reiter neue Token-Zeile (ein/aus); Statusleiste addiert workflow_history-Totals in updateStatusBar (gated _wfRunActive, wfBanner.data.tokens_in/out+cost_usd); Sitzungs-Inspektor (chat_send.js openInspectModal) neue 'Workflow-Lauf'-Kachelzeile. '110K Token'-Falschanzeige war stale State — synthetische Zeilen tragen KEINE token-metadata → Kontext-Meter liest 0.

Verifiziert E2E in Safari (b7731adee5): user+assistant-Paar, 1 artifact-card im Fluss, Statusleiste 'Aus: 249', Statistik 'Token 0 ein / 249 aus'. Server-Restart nötig (Schema + Interpreter-Capture). js_gate GRÜN. Siehe [[project_inprocess_openai_loop]] (v8.24.2: Lauf öffnet als reguläre Chat-Session via openSession).
