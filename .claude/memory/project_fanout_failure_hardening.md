---
name: project_fanout_failure_hardening
description: "v9.320.0: Fan-out/Subagenten-Härtung — timeout_s in run_turn_blocking war TOT (jetzt Wanduhr via is_cancelled-Poll), Status timeout/empty, retry_background_task (1×-Cap via retry_of, model-Override), Preamble-Fehlerklassen (User-Abbruch ≠ Fehler), Stopp-Kaskade spawn_turn_id, worker_*-Tools entfernt"
metadata: 
  node_type: memory
  type: project
  originSessionId: a4e33e9e-18fa-4d59-9a58-29b12ffda3e5
---

v9.320.0 (2026-07-13, 4e572b13): volle Härtung des Fan-out/Subagent-Pfads nach Analyse-Auftrag ("wie geht der Orchestrator mit Fehlern/Cancels/Timeouts um"). User-Entscheide: alles härten / Retry mit Cap / Stopp = Turn+Subagenten.

**Kernbefunde der Analyse (vorher):**
- `run_turn_blocking(timeout_s=…)` nahm den Param an und nutzte ihn NICHT — `_TIMEOUT_S=3600` war dekorativ; einzige Grenzen: max_rounds=40, urlopen(1800s Socket), Gruppen-Sweep (nur Gruppen). Die Hub-UI zeigte "elapsed/timeout" für ein Limit, das nicht existierte.
- Join-Preamble lieferte Fehler ohne task_id/Anweisung; Respawn nur auf demselben Modell (kein model-Param); leerer Output = 'done'; User-Abbruch von API-Fehler ununterscheidbar.
- ask_user-blockierte Subagenten schliefen bei Stopp bis zum ask-Timeout (event.wait ohne Cancel-Check; is_cancelled nur zwischen Runden).
- TaskRunner.cancel (delegate_task) war Soft-Flag — der laufende background_call lief weiter (kein turn_id-Wiring); wait=True>300s-Join galt als Fehler.
- worker_*-Tools operierten auf einer Registry OHNE Writer (execution.run_worker_subagent hatte 0 Caller seit Native-Loop-Löschung) — worker_abort konnte nie etwas abbrechen. Entfernt; execution.py bleibt (route_tool_execution lebt).

**Mechanik (neu):**
- Timeout: Deadline ∨ Cancel-Event als is_cancelled-Closure → Socket-Watcher bricht auch mid-stream; Result `timed_out=True` + LAUTER error. bg-Task mappt auf Status 'timeout' (Teilergebnis bleibt), leerer Output ohne Fehler → 'empty'. Sweep markiert Straggler jetzt 'timeout'.
- `retry_background_task(task_id, model?)`: klont aus DB-Row; Cap server-seitig via retry_of-Spalte (Retry nie retrybar, background_task_retry_exists fail-closed); cancelled wird VERWEIGERT (Nutzer-Entscheid); model-Override schlägt background_task_model; läuft als eigene auto-Gruppe; beim Join hängt `_bg_original_group_blocks` die schon konsumierten done-Geschwister der Originalgruppe wieder an (wire-only-Delivery verbraucht!).
- Preamble: `_bg_member_block`/`_bg_decision_tail` — Klassen error/timeout/empty → "GENAU EIN Retry / selbst machen / berichten"; cancelled → "NICHT neu starten, ggf. Nutzer fragen".
- Stopp-Kaskade: Spalte `spawn_turn_id`; POST /v1/chat/cancel cancelt Tasks des AKTIVEN Turns (ChatDB.get_active_turn_id + cancel_session_tasks(spawn_turn_id)).
- ask_user/ask_user_for_file: 1s-Poll-Loop gegen `sidecar_proxy.is_turn_cancelled(turn_id)` (bg) + Session-CancelToken (interaktiv).
- Neuer Endpoint POST /v1/background-tasks/cancel-session; UI: Sidebar-✦-Stopp (hover-SVG), Termchat-Spinner "alle stoppen", Status-Labels Zeitlimit/leere Antwort in Panel+Hub.

**Gotchas:**
- ChatDB in handlers/chat.py ist server-injiziert — neue Fns müssen explizit `from server_lib.db import ChatDB` (NameError sonst nur außerhalb des Servers sichtbar; Test fing es).
- Termchat-Spinner repainted alle 80ms per innerHTML — Stopp-Knopf MUSS statischer Sibling des Text-Spans sein, sonst frisst der Ticker den Click-Handler.
- retry-Tool ist im Code-Mode-undefer neben run_background_task (9.311-Lektion: Tool-Sichtbarkeit vor Prompt-Tuning).
- Scheduler unberührt: nutzt run_turn (eigener Watchdog via cancel_token), nicht run_turn_blocking.

**v9.321.0 (45665487) — Queue-Fix + Panel-UI:**
- run_turn_blocking queued jetzt via acquire_if, aber NUR für top-level bg-Task-Turns (`tool_context.bg_task=True`). Alle anderen background_calls bleiben BEWUSST unqueued: run_turn hält den Slot whole-turn inkl. Tool-Ausführung → ein nested background_call aus einem gehaltenen Slot (ask_llm/Klassifikator/Summariser) würde deadlocken, wenn alle Slots äußeren Turns gehören. Das war der (undokumentierte) historische Grund, warum bg-Calls nie queueten.
- Wanduhr-Deadline startet NACH Slot-Erwerb (Queue-Wartezeit ≠ Arbeitsbudget); Cancel-Event als `.cancelled`-Property-Adapter (acquire_if pollt nur dieses Attribut); Queue-TimeoutError → eigener lauter error.
- Panel-UI: Tool-Liste jeder Subagent-Karte in `<details>`-Wrapper „Tool-Verwendungen (N)", default ZU; Live-Updates schreiben nur inneren Container + Zähler-Span (Klapp-Zustand überlebt Streaming; voller Pane-Rebuild resettet — akzeptiert).

Tests: tests/test_bgtask_failure_handling.py (14) + 2 Sweep-Assertions aktualisiert; Suite 406 grün. Relates to [[project_subagent_panes_oh_my_opencode]], [[project_inprocess_openai_loop]].
