// panels_agentpane.js — Subagent-Panes im Code-Mode-Bottom-Panel.
//
// Ein vierter Tab-KIND ('agent') im Bottom-Workspace (panels_terminal.js):
// startet ein Code-Mode-Chat-Turn eine Hintergrundaufgabe (run_background_task),
// öffnet automatisch ein Pane, das den Live-Transcript des Subagenten streamt —
// Text, Thinking, Tool-Aufrufe, Token-Verbrauch — inspiriert von der
// tmux-Multiplexer-Integration in oh-my-opencode-slim. Read-only (kein Composer);
// Stopp-Knopf → POST /v1/background-tasks/cancel.
//
// Datenquelle: GET /v1/background-tasks/<id>/transcript (SSE, LiveStream
// replay+follow, Brain-Event-Vokabular) via API.streamBackgroundTranscript.
// Dieselbe Quelle wie das rechte Hintergrundaufgaben-Panel — hier nur als
// Bottom-Panel-Ansicht. Panes sind EPHEMER (nicht in bottom_workspace
// persistiert): laufende Aufgaben werden beim Panel-Laden re-attacht.
//
// Globals only, fixed load order (nach panels_termchat.js, vor init.js).
// Reuses esc(), renderMarkdown(), _term, API, terminalAvailable().

// ── Tab object shape (kind:'agent') ──────────────────────────────────────────
//   { id:'agent-'+taskId, kind:'agent', taskId, name, el, pane,
//     status:'running'|'done'|'error'|'cancelled', _ctrl:AbortController,
//     _live:{...render state}, tokensIn, tokensOut }

// Auto-Open-Deckel pro Turn: eine Fan-out-Gruppe mit N Aufgaben soll das
// Panel nicht mit N Panes fluten — die ersten 4 öffnen, der Rest läuft im
// Hintergrundaufgaben-Panel weiter sichtbar.
const _AGENT_PANE_AUTO_MAX = 4;
let _agentPaneAutoCount = 0;
let _agentPaneAutoTurnKey = '';

function _agentPaneTab(taskId) {
  return (typeof _term !== 'undefined' ? _term.tabs : [])
    .find(t => t.kind === 'agent' && t.taskId === taskId);
}

// Öffnet (oder aktiviert) das Subagent-Pane für eine Hintergrundaufgabe.
function terminalOpenAgentPane(taskId, title, paneId) {
  if (typeof _term === 'undefined' || !_term.open) return null;
  const exist = _agentPaneTab(taskId);
  if (exist) { _terminalActivate(exist.id); return exist; }
  const id = 'agent-' + taskId;
  const target = paneId || _terminalDefaultPane('chat');
  const pane = _terminalGetPane(target) || _terminalActivePane() || _term.panes[0];
  const el = document.createElement('div');
  el.style.display = 'none';
  (pane ? pane.bodyEl : document.getElementById('terminal-panes')).appendChild(el);
  const tab = {
    id, kind: 'agent', taskId, name: title || 'Subagent', el,
    status: 'running', _ctrl: null, _live: null,
    tokensIn: 0, tokensOut: 0,
    pane: pane ? pane.id : 'pane-a',
  };
  _term.tabs.push(tab);
  _agentPaneBuildBody(tab);
  _terminalRenderTabs();
  _terminalActivate(id);
  _agentPaneAttach(tab);
  return tab;
}

// Baut das Pane-DOM: Kopfzeile (Status + Stopp), Log (termchat-CSS wiederver-
// wendet), Status-Footer. Kein Composer — der Subagent ist read-only.
function _agentPaneBuildBody(tab) {
  const el = tab.el;
  el.className = 'termchat agentpane';
  el.innerHTML = `
    <div class="ap-head" id="${esc(tab.id)}-head">
      <span class="ap-dot running"></span>
      <span class="ap-title">${esc(tab.name)}</span>
      <span style="flex:1"></span>
      <button class="ap-stop btn-secondary" title="Aufgabe stoppen">Stopp</button>
    </div>
    <div class="tc-log" id="${esc(tab.id)}-log"></div>
    <div class="tc-status" id="${esc(tab.id)}-status"></div>`;
  const stopBtn = el.querySelector('.ap-stop');
  stopBtn.addEventListener('click', async () => {
    stopBtn.disabled = true;
    try { await API.cancelBackgroundTask(tab.taskId); }
    catch (_) { stopBtn.disabled = false; }
  });
  _agentPaneStatus(tab, 'läuft…');
}

function _agentPaneLog(tab) { return document.getElementById(tab.id + '-log'); }

function _agentPaneRow(tab, cls, html) {
  const log = _agentPaneLog(tab);
  if (!log) return null;
  const stick = (log.scrollHeight - log.scrollTop - log.clientHeight) <= 40;
  const div = document.createElement('div');
  div.className = 'tc-row ' + cls;
  div.innerHTML = html;
  log.appendChild(div);
  if (stick) log.scrollTop = log.scrollHeight;
  return div;
}

function _agentPaneStatus(tab, text) {
  const s = document.getElementById(tab.id + '-status');
  if (!s) return;
  const tok = (tab.tokensIn || tab.tokensOut)
    ? ` · ${tab.tokensIn}↑ ${tab.tokensOut}↓` : '';
  s.textContent = `Subagent · ${text}${tok}`;
}

function _agentPaneSetState(tab, status) {
  tab.status = status;
  const head = document.getElementById(tab.id + '-head');
  if (head) {
    const dot = head.querySelector('.ap-dot');
    if (dot) dot.className = 'ap-dot ' + status;
    const btn = head.querySelector('.ap-stop');
    if (btn && status !== 'running') btn.style.display = 'none';
  }
  _terminalRenderTabs();
}

// Attach an den Transcript-SSE (replay + live). Rendert das Brain-Event-
// Vokabular in termchat-artige Zeilen; Reihenfolge Text↔Tool bleibt
// chronologisch (gleiche Segmentlogik wie _tcCallbacks).
function _agentPaneAttach(tab) {
  const live = { curTextRow: null, curSegText: '', lastWasTool: false,
                 toolById: {}, thinkRow: null, think: '' };
  tab._live = live;
  tab._ctrl = API.streamBackgroundTranscript(
    tab.taskId,
    /* onText */ (chunk) => {
      if (!live.curTextRow || live.lastWasTool) {
        live.curTextRow = _agentPaneRow(tab, 'tc-asst', '');
        live.curSegText = '';
        live.lastWasTool = false;
      }
      live.curSegText += chunk;
      if (live.curTextRow) live.curTextRow.innerHTML = renderMarkdown(live.curSegText);
      _agentPaneStatus(tab, 'antwortet…');
    },
    /* onDone */ (d) => {
      const st = (d && d.status) || (d && d.error ? 'error' : 'done');
      _agentPaneSetState(tab, st === 'running' ? 'done' : st);
      if (d && d.error) _agentPaneRow(tab, 'tc-err', esc(String(d.error)));
      if (d && d.usage) {
        if (typeof d.usage.input === 'number') tab.tokensIn = d.usage.input;
        if (typeof d.usage.output === 'number') tab.tokensOut = d.usage.output;
      }
      const label = { done: 'fertig', error: 'Fehler', cancelled: 'gestoppt' }[tab.status] || tab.status;
      _agentPaneStatus(tab, label);
    },
    /* onRequest */ (d) => {
      if (d && d.title && (!tab.name || tab.name === 'Subagent')) {
        tab.name = d.title; _terminalRenderTabs();
        const head = document.getElementById(tab.id + '-head');
        const tEl = head && head.querySelector('.ap-title');
        if (tEl) tEl.textContent = d.title;
      }
      if (d && d.prompt) {
        _agentPaneRow(tab, 'tc-user',
          `<span class="tc-uprompt">›</span> ${esc(d.prompt)}`);
      }
    },
    /* onTool */ (ev) => {
      if (!ev) return;
      if (ev.phase === 'start') {
        const arg = (typeof _tcToolArg === 'function') ? _tcToolArg(ev.args) : '';
        const row = _agentPaneRow(tab, 'tc-tool',
          `<span class="tc-tool-dot">●</span> <span class="tc-tool-name">${esc(ev.name || '')}</span>` +
          (arg ? ` <span class="tc-tool-arg">${esc(arg)}</span>` : '') +
          ` <span class="tc-tool-state">…</span>`);
        if (row && ev.tool_use_id) live.toolById[ev.tool_use_id] = row;
        live.lastWasTool = true;
        live.curTextRow = null;
        _agentPaneStatus(tab, 'Werkzeug: ' + (ev.name || ''));
      } else if (ev.phase === 'done') {
        const row = live.toolById[ev.tool_use_id];
        if (row) {
          const s = row.querySelector('.tc-tool-state');
          if (s) { s.textContent = ev.is_error ? '✗' : '✓'; s.classList.add(ev.is_error ? 'err' : 'ok'); }
        }
      }
    },
    /* onThinking */ (chunk) => {
      if (!live.thinkRow || !live.thinkRow.parentNode) {
        live.thinkRow = _agentPaneRow(tab, 'tc-think', '');
        live.think = '';
        live.curTextRow = null;
      }
      live.think += chunk;
      live.thinkRow.textContent = '⠿ ' + live.think;
      _agentPaneStatus(tab, 'denkt nach…');
    },
    /* onUsage */ (d) => {
      if (d && typeof d.tokens_in === 'number') tab.tokensIn = d.tokens_in;
      if (d && typeof d.tokens_out === 'number') tab.tokensOut = d.tokens_out;
      _agentPaneStatus(tab, 'läuft…');
    },
  );
}

// Hook aus den Chat-tool_result-Callbacks (Terminal-Chat + Haupt-Chat):
// öffnet das Pane, sobald run_background_task eine task_id zurückgibt.
// Nur im Code-Mode (Bottom-Panel verfügbar) — sonst still no-op; das rechte
// Hintergrundaufgaben-Panel deckt den Nicht-Code-Fall unverändert ab.
function terminalMaybeOpenAgentPane(toolName, resultStr, title, turnKey) {
  if (toolName !== 'run_background_task') return;
  if (typeof terminalAvailable !== 'function' || !terminalAvailable()) return;
  let taskId = '';
  try {
    const r = JSON.parse(resultStr || '{}');
    taskId = r.task_id || '';
    if (!taskId || r.error) return;
  } catch (_) { return; }
  // Panel bei Bedarf öffnen — der Spawn IST das Signal, dass unten etwas
  // Sichtbares passiert (Kern der Subagent-Pane-Idee).
  if (typeof _term !== 'undefined' && !_term.open && typeof terminalTogglePanel === 'function') {
    try { terminalTogglePanel(true); } catch (_) { return; }
  }
  const key = turnKey || 'turn';
  if (key !== _agentPaneAutoTurnKey) { _agentPaneAutoTurnKey = key; _agentPaneAutoCount = 0; }
  if (_agentPaneAutoCount >= _AGENT_PANE_AUTO_MAX) return;
  _agentPaneAutoCount += 1;
  // Panel-Öffnung baut Panes async auf — Pane-Erzeugung leicht verzögern.
  setTimeout(() => { try { terminalOpenAgentPane(taskId, title || ''); } catch (_) {} }, 250);
}

// Re-Attach beim Laden des Bottom-Panels: laufende Hintergrundaufgaben der
// offenen Chat-Sessions (Terminal-Chats + aktiver Haupt-Chat) wieder als
// Panes öffnen. Beendete Aufgaben werden NICHT reopened (ephemer).
async function _agentPaneReattachAll() {
  if (typeof _term === 'undefined' || !_term.open) return;
  const sids = new Set();
  for (const t of _term.tabs) {
    if (t.kind === 'chat' && t.sessionId) sids.add(t.sessionId);
  }
  try {
    const cur = (typeof state !== 'undefined' && state.activeChat && state.activeChat.sessionId);
    if (cur) sids.add(cur);
  } catch (_) {}
  for (const sid of sids) {
    let tasks = [];
    try {
      const d = await API.getBackgroundTasks(sid);
      tasks = (d && d.tasks) || [];
    } catch (_) { continue; }
    for (const t of tasks) {
      if (t.status === 'running' && !_agentPaneTab(t.id)) {
        terminalOpenAgentPane(t.id, t.title || '');
      }
    }
  }
}
