---
name: project_subagent_panes_oh_my_opencode
description: "v9.308.0: Subagent-Panes im Code-Mode-Bottom-Panel (Tab-Kind 'agent', Live-Transcript via per-Task-LiveStream) + Fix des seit 9.247.0 toten Transcript-Live-Zweigs; Backlog aus oh-my-opencode-slim-Analyse (Rescue-Hook, ast-grep, Worktree-Lanes)"
metadata: 
  node_type: memory
  type: project
  originSessionId: cc082453-4a24-444b-bcd2-cf1d42a705de
---

v9.308.0 (2026-07-11, 8aa80e6e): **Subagent-Panes** — Idee 1 aus der Analyse von github.com/alvinunreal/oh-my-opencode-slim (deren tmux-Multiplexer-Integration: jede Background-Spezialisten-Session als Live-Pane).

**Latenter Bug zuerst gefunden**: Der Live-Zweig von `GET /v1/background-tasks/<id>/transcript` rief noch `_sp.sidecar_url()` — Sidecar seit 9.247.0 GELÖSCHT → "Transkript anzeigen" degradierte seit Monaten still zum Stored-Replay (AttributeError riss den SSE bei laufender Task ab; nur urllib-Fehler waren gefangen). Der 06-user-manual-Text versprach Live-Verhalten, das nicht existierte.

**Mechanik (Choke-Point-Fix, ein Seam für beide Panels):**
- `BackgroundTaskRunner` hält pro Task einen `LiveStream` (server.LiveStream via sys.modules-`__main__`-Seam wie delegation_tools; None-tolerant für Tests) und reicht `emit=_emit` in `background_call` — der Param existierte schon (Workflow-agent_step-Seam in run_turn_blocking).
- tool_result-VIEW auf 4000 Zeichen gekappt, `result_chars` = echte Länge (Modell sieht weiter alles). Terminales `done` NACH DB-Write, VOR `_live`-Pop (Late-Attach → Stored-Replay).
- Endpoint: attach() = Replay+Follow, 5s-Keepalive via queue.get-Timeout.
- Frontend: `web/js/panels_agentpane.js` (NEU, 13 Globals) — vierter Tab-Kind `agent` im Bottom-Workspace, read-only termchat-Styling, Status-Punkt + Stopp (Tab-Schließen cancelt NIE). Auto-Open-Hooks in BEIDEN tool_result-Callbacks (`_tcCallbacks` panels_termchat + `buildStreamCallbacks` chat_send), gated auf `terminalAvailable()`, Deckel 4 Panes/Turn, `_terminalLoadSessions` re-attacht Laufende via `_agentPaneReattachAll`. Panes ephemer (nicht in bottom_workspace).
- GOTCHA: `_terminalShowActiveTabs` setzt display:'block' außer für kind 'chat' — neuer flex-Layout-Kind muss dort ergänzt werden (agent brauchte 'flex', sonst bricht das termchat-Spaltenlayout).
- api.js-Normalizer spricht jetzt NUR Brain-Vokabular (text_delta/thinking_*/tool_call/tool_result/usage/error als kanonische event:-Frames); toter Sidecar-Shape-Parser (`anthropic.content_block_delta`, `tool_dispatch_*`) entfernt. panels_background.js brauchte NULL Änderungen (gleiche onTool-Shapes) und hat seine Live-Ansicht zurück.
- E2E verifiziert: Spawner-Turn auf openrouter/gpt-5.6-luna → 87 text_delta live + request/tool_call/tool_result/done via curl-SSE. net-globals 1914→1927.

**Backlog aus derselben Analyse — ALLE 4 UMGESETZT (2026-07-11, "implementiere den rest"):**
- ✅ (3) **Edit-Rescue** v9.309.0: `tool_edit_file` bekam zwei tolerante Pässe NUR bei Exakt-Match=0 — `_edit_rescue_unicode` (Typo-Look-alikes mit Index-Map auf Original-Bytes) + `_edit_rescue_lines` (Ganzzeilen, rstrip + uniformes Einrückungs-Delta, new_string wird mit-eingerückt); eindeutig→angewendet (`rescued`-Flag), mehrdeutig→harter Fehler, <6 Zeichen nie. Schema BEWUSST unverändert (KV-Prefix). 13 Unit-Tests (tests/test_edit_rescue.py).
- ✅ (4) **ast-grep** v9.310.0: `ast_grep_search` (read-only, READONLY_TOOLS) + `ast_grep_replace` (Dry-Run-Default, apply=true explizit, >500-Treffer-Guard, _after_file_write je Datei). Binary via brew (0.44.1), Subprozess 60s-Timeout, `--json=stream` (Zeilen 0-basiert). root default = ctx.working_dir. Gating: tool_settings interactive=deferred + Code-Mode-undefer (apply_domain_context). E2E: 103 Treffer in qb ohne root-Param.
- ✅ (2) **Worktree-Lanes** v9.311.0: EIN Tool `git_worktree(action=create|list|diff|remove)` (git-Gruppe); Lanes IN working_dir (`.worktrees/<slug>`, Branch `brain/<slug>`) → Terminal-Lockdown/Baum/File-Tools decken sie ab; `.git/info/exclude`-Append (nie User-.gitignore); Registry lanes.json; dirty-remove-Guard, KEIN Auto-Merge (User merged bewusst, diff=Review). Voller Lifecycle auf Test-Repo E2E-verifiziert.
- Verworfen: festes Pantheon-Rollenmodell (unser Routing flexibler), Rust-Companion, Interview; Council≈MoA, Reflect≈Skill-Gen, Deepwork≈Plan-Delegation, Codemap≈code_graph (nur die per-Ordner-Prosa-Idee tangiert [[backlog_lean_ctx_coding_mode]]).

**v9.311.1/.2 — Spawn-Neigung (User-Report 'luna spawnt nicht'):** Der Code-Mode-Prompt bekam Punkt 5 (Hintergrundaufgaben-Nudge). ZWEI Blocker bis es wirkte: (1) HART — Klassifikator flaggt delegation für Analyse-/Report-Prompts NICHT → defer_extra nahm run_background_task von der Wire; ein Modell wählt keinen Weg, den es nicht sieht → Code-Mode-undefer in apply_domain_context (wie code_*). (2) WEICH — vager Nudge ('absehbar viele Tool-Aufrufe') reicht NICHT: luna erledigte den 227-Prozeduren-Report in 33s inline über den Index. Erst die PRÜF-Anweisung mit messbaren Auslösern (ganzer Bestand / mehrere Themen / Nutzer-wartet-nicht-Signal) kippte es: vorher 2× inline, danach 2/2 Spawns (gleicher Prompt/Modell/Projekt). LESSON: Tool-Sichtbarkeit VOR Prompt-Tuning prüfen; Nudges brauchen vom Modell VOR Arbeitsbeginn prüfbare Kriterien. NEBENBEFUND GEFIXT (v9.311.3): tool_resolution fehlte auf ALLEN Turns — Refactor-Drift: _build_prefix_for stasht den Breakdown auf dem Request-Context, die Persist-Stellen (msg_metadata + done-Event in handlers/chat.py) lasen die tote bare Lokale `_tool_breakdown` → NameError still vom except-Gurt geschluckt. Fix: getattr aus dem Context (Muster des Scratchpad-Zweigs). LESSON: breite except-NameError-Gurte verstecken Refactor-Drift jahrelang.

**v9.312.0 — Hub-Rework nach echtem Einsatz (User-Feedback glm-Fan-out):** EIN Singleton-✦-Tab „Subagenten" mit Karten (Status/MODELL/Tokens/Stopp/Tail + aufklappbares Transcript; Zähler+Puls im Label) statt N Tabs; Sidebar-Tree (✦-Zeilen unter dem Chat-Eintrag, `GET /v1/background-tasks/running` + pollActiveSessions-Muster); Reload-Persistenz (Reattach neueste 12 inkl. FERTIGE — Stored-Replay spielt seit 9.312.0 auch tool_events + request.model aus); Delivery-Sichtbarkeit: Server-Delivery LIEF immer (DB: consumed=1, Delivery-Message, streaming_text) — aber der TERMINAL-Chat attacht extern gestartete Turns nie (nur eigener POST-Reader; Haupt-Chat hat _reattachForBackgroundDelivery, code_chat nicht) → _agentHubNotifyDelivery lädt den Spawner-Tab per tcLoadTranscript (attacht via _tcAttachLive bei streaming:true). Subagenten-MODELL: default = Chat-Modell (Snapshot beim Spawn), außer per-Modell `background_task_model` (fix oder 'auto' = Klassifikator je Leaf). GOTCHA: `git add -A` erfasste `.brain-trash/` (Test-Artefakte, 40k Zeilen) → untracked + gitignored in 44761c87.

**Gotchas aus der Umsetzung:**
- POST /v1/tools/settings erwartet Key `name` (nicht `tool`); Server-BOOT seedet für NEUE Tools sofort Records mit interactive=active → deferred-Wunsch NACH Restart per API setzen.
- tool_settings-Seeding ändert config.json → config.example.json-Mirror stale → pre-push blockt; `python3 scripts/scrub_config.py` + amend.
- LATENT (nicht gefixt): `git_command`/`tool_github_command` laufen OHNE cwd-Auflösung im Server-cwd — `git_worktree` löst explizit aus ctx.working_dir. KEINES der aktuellen Code-Mode-Projekte (qb/weather/sql/r) ist ein Git-Repo → Lanes-Verweigerung dort korrekt.

Relates to [[project_codemode_terminal]] (Bottom-Panel-Architektur), [[project_inprocess_openai_loop]] (Loop/emit-Vokabular).
