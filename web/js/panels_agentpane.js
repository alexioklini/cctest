// panels_agentpane.js — Subagenten-Hub im Code-Mode-Bottom-Panel.
//
// v9.312.0: EIN ✦-Tab „Subagenten" (kind 'agent', Singleton) statt eines Tabs
// pro Hintergrundaufgabe (v9.308.0 — User-Feedback: N Einzel-Tabs sind bei
// Fan-out unübersichtlich). Im Hub liegt pro Aufgabe eine KARTE: Status-Punkt,
// Titel, ausführendes MODELL, Token-Zähler, Stopp-Knopf und eine Tail-Zeile
// (aktuelles Werkzeug / letzter Text) — Klick auf den Kopf klappt das volle
// Live-Transcript auf. Der Tab-Titel trägt einen Zähler und pulsiert, solange
// mindestens eine Aufgabe läuft — das ist das „es arbeitet noch"-Signal,
// nachdem der spawnende Chat-Turn selbst längst fertig ist.
//
// Datenquelle unverändert: GET /v1/background-tasks/<id>/transcript (SSE,
// LiveStream replay+follow) via API.streamBackgroundTranscript; das führende
// request-Event trägt seit 9.312.0 auch das ausführende Modell. Karten sind
// EPHEMER (nicht in bottom_workspace persistiert); laufende Aufgaben werden
// beim Panel-Laden re-attacht. Stopp = POST /v1/background-tasks/cancel;
// Karte/Tab schließen stoppt NIE.
//
// Globals only, fixed load order (nach panels_termchat.js, vor init.js).
// Reuses esc(), renderMarkdown(), _term, API, terminalAvailable().

// Auto-Open-Deckel pro Turn: eine Fan-out-Gruppe mit N Aufgaben soll nicht
// unbegrenzt Karten aufmachen — ab der fünften läuft der Rest nur im rechten
// Hintergrundaufgaben-Panel weiter (dort vollständig sichtbar).
const _AGENT_PANE_AUTO_MAX = 4;
let _agentPaneAutoCount = 0;
let _agentPaneAutoTurnKey = '';

const _AGENT_HUB_ID = 'agent-hub';

function _agentHubTab() {
  return (typeof _term !== 'undefined' ? _term.tabs : []).find(t => t.kind === 'agent');
}

// Anzahl laufender Karten — von _terminalRenderTabs fürs Tab-Label gelesen.
function agentHubRunningCount(tab) {
  const cards = (tab && tab._cards) || {};
  return Object.values(cards).filter(c => c.status === 'running').length;
}

// Öffnet das Hub (erstellt es bei Bedarf) und stellt sicher, dass die Aufgabe
// eine Karte hat. Aktiviert den Tab.
function terminalOpenAgentPane(taskId, title, sessionId, opts) {
  // opts: {activate: true, notify: true} — Reattach von FERTIGEN Karten setzt
  // beides false (kein Fokus-Klau beim Panel-Load, kein Delivery-Reload für
  // laengst zugestellte Ergebnisse).
  opts = opts || {};
  const _activate = opts.activate !== false;
  if (typeof _term === 'undefined' || !_term.open) return null;
  let tab = _agentHubTab();
  if (!tab) {
    const target = opts.paneId || _terminalDefaultPane('chat');
    const pane = _terminalGetPane(target) || _terminalActivePane() || _term.panes[0];
    const el = document.createElement('div');
    el.style.display = 'none';
    (pane ? pane.bodyEl : document.getElementById('terminal-panes')).appendChild(el);
    tab = {
      id: _AGENT_HUB_ID, kind: 'agent', name: 'Subagenten', el,
      _cards: {}, pane: pane ? pane.id : 'pane-a',
    };
    _term.tabs.push(tab);
    el.className = 'termchat agenthub';
    el.innerHTML = `<div class="ap-cards" id="${_AGENT_HUB_ID}-cards"></div>
      <div class="ap-empty" id="${_AGENT_HUB_ID}-empty">Keine Subagenten in dieser Sitzung.</div>`;
    _terminalRenderTabs();
  }
  if (taskId && !tab._cards[taskId]) {
    const c = _agentHubAddCard(tab, taskId, title || '', sessionId || '');
    if (c) c._notifyOnDone = opts.notify !== false;
  }
  if (_activate) _terminalActivate(tab.id);
  return tab;
}

function _agentHubEmptyState(tab) {
  const empty = document.getElementById(_AGENT_HUB_ID + '-empty');
  if (empty) empty.style.display = Object.keys(tab._cards).length ? 'none' : 'block';
}

// ── Karte ────────────────────────────────────────────────────────────────────
function _agentHubAddCard(tab, taskId, title, sessionId) {
  const host = document.getElementById(_AGENT_HUB_ID + '-cards');
  if (!host) return null;
  const el = document.createElement('div');
  el.className = 'ap-card';
  el.innerHTML = `
    <div class="ap-card-head">
      <span class="ap-dot running"></span>
      <span class="ap-title">${esc(title || 'Subagent')}</span>
      <span class="ap-model"></span>
      <span class="ap-meta"></span>
      <button class="ap-stop btn-secondary" title="Aufgabe stoppen">Stopp</button>
      <button class="ap-x" title="Karte entfernen (stoppt NICHT)" style="display:none">✕</button>
    </div>
    <div class="ap-tail">wartet auf Ereignisse …</div>
    <div class="ap-ask" style="display:none"></div>
    <div class="ap-card-log tc-log" style="display:none"></div>`;
  host.prepend(el);  // neueste oben
  const card = {
    taskId, sessionId: sessionId || '', el, status: 'running', expanded: false,
    tokensIn: 0, tokensOut: 0, model: '',
    logEl: el.querySelector('.ap-card-log'),
    tailEl: el.querySelector('.ap-tail'),
    askEl: el.querySelector('.ap-ask'),
    _askSig: '',
    _live: null, _ctrl: null,
  };
  tab._cards[taskId] = card;
  // Nur eine Karte → direkt aufklappen (Einzelfall = volle Sicht).
  if (Object.keys(tab._cards).length === 1) _agentCardToggle(tab, card, true);
  el.querySelector('.ap-card-head').addEventListener('click', (e) => {
    if (e.target.closest('.ap-stop') || e.target.closest('.ap-x')) return;
    _agentCardToggle(tab, card);
  });
  el.querySelector('.ap-stop').addEventListener('click', async (e) => {
    e.stopPropagation();
    e.target.disabled = true;
    try { await API.cancelBackgroundTask(taskId); }
    catch (_) { e.target.disabled = false; }
  });
  el.querySelector('.ap-x').addEventListener('click', (e) => {
    e.stopPropagation();
    if (card._ctrl) { try { card._ctrl.abort(); } catch (_) {} }
    el.remove();
    delete tab._cards[taskId];
    _agentHubEmptyState(tab);
    _terminalRenderTabs();
  });
  _agentHubEmptyState(tab);
  _agentCardAttach(tab, card);
  _terminalRenderTabs();
  return card;
}

function _agentCardToggle(tab, card, force) {
  card.expanded = (force !== undefined) ? force : !card.expanded;
  card.logEl.style.display = card.expanded ? 'block' : 'none';
  card.tailEl.style.display = card.expanded ? 'none' : 'block';
  if (card.expanded) card.logEl.scrollTop = card.logEl.scrollHeight;
}

function _agentCardRow(card, cls, html) {
  const log = card.logEl;
  const stick = (log.scrollHeight - log.scrollTop - log.clientHeight) <= 40;
  const div = document.createElement('div');
  div.className = 'tc-row ' + cls;
  div.innerHTML = html;
  log.appendChild(div);
  if (stick) log.scrollTop = log.scrollHeight;
  return div;
}

function _agentCardTail(card, text) {
  card.tailEl.textContent = text;
}

// ── Rückfrage eines Subagenten (ask_user) ────────────────────────────────────
// Ein abgekoppelter Subagent hat KEINEN Live-SSE-Kanal (der spawnende Turn ist
// längst beendet) — seine Frage emittierte bisher in einen toten event_callback
// und blockierte unsichtbar bis zum Timeout (User-Report: "einer der Subagenten
// hat ask_user aufgerufen, das geht komplett unter"). Die Frage wird deshalb
// server-seitig auf der Task-Zeile PERSISTIERT und vom bestehenden 3s-Poller
// mitgeliefert; hier wird sie als Antwort-Box in die Karte gerendert.
// `pq` = null → Box weg (beantwortet/abgelaufen).
function _agentCardRenderAsk(card, pq) {
  if (!card || !card.askEl) return;
  const sig = pq ? JSON.stringify(pq.questions || pq.question || '') : '';
  if (sig === card._askSig) return;      // unverändert → kein Repaint (tippt der Nutzer gerade)
  card._askSig = sig;
  if (!pq) { card.askEl.style.display = 'none'; card.askEl.innerHTML = ''; return; }

  const qs = Array.isArray(pq.questions) && pq.questions.length
    ? pq.questions
    : [{ question: pq.question || '', options: pq.options || [] }];
  const q = qs[0];                        // Subagenten stellen praktisch immer EINE Frage
  const opts = Array.isArray(q.options) ? q.options.filter(Boolean) : [];
  card.askEl.innerHTML = `
    <div class="ap-ask-q">❓ ${esc(q.question || 'Der Subagent hat eine Rückfrage.')}</div>
    ${pq.context_summary ? `<div class="ap-ask-ctx">${esc(pq.context_summary)}</div>` : ''}
    <div class="ap-ask-opts">
      ${opts.map((o, i) => `<button class="ap-ask-opt btn-secondary" data-i="${i}">${esc(o)}</button>`).join('')}
    </div>
    <div class="ap-ask-free">
      <input type="text" class="ap-ask-input" placeholder="Antwort eingeben …">
      <button class="ap-ask-send btn-secondary">Senden</button>
    </div>`;
  card.askEl.style.display = 'block';
  const _tab = _agentHubTab();
  if (_tab) _agentCardToggle(_tab, card, true);  // aufklappen — sonst übersieht man die Frage

  const send = async (text) => {
    const t = (text || '').trim();
    if (!t) return;
    card.askEl.querySelectorAll('button, input').forEach(e => { e.disabled = true; });
    try {
      await API.answerBackgroundTask(card.taskId, t);
      card._askSig = '';                  // erlaubt eine spätere ZWEITE Frage
      card.askEl.style.display = 'none';
      card.askEl.innerHTML = '';
      _agentCardTail(card, 'Antwort gesendet — Subagent läuft weiter …');
    } catch (_) {
      card.askEl.querySelectorAll('button, input').forEach(e => { e.disabled = false; });
    }
  };
  card.askEl.querySelectorAll('.ap-ask-opt').forEach(b => {
    b.addEventListener('click', () => send(opts[Number(b.dataset.i)]));
  });
  const inp = card.askEl.querySelector('.ap-ask-input');
  card.askEl.querySelector('.ap-ask-send').addEventListener('click', () => send(inp.value));
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(inp.value); });
  inp.focus();
}

// Vom Poller (nav.js) gerufen: verteilt die offenen Fragen aus
// /v1/background-tasks/running auf die Hub-Karten.
function agentHubApplyPendingQuestions(tasks) {
  const tab = _agentHubTab();
  if (!tab || !tab._cards) return;
  const byId = {};
  for (const t of (tasks || [])) byId[t.id] = t.pending_question || null;
  for (const [taskId, card] of Object.entries(tab._cards)) {
    if (card.status !== 'running') { _agentCardRenderAsk(card, null); continue; }
    _agentCardRenderAsk(card, byId[taskId] || null);
  }
}

function _agentCardMeta(card) {
  const m = card.el.querySelector('.ap-meta');
  if (!m) return;
  const tok = (card.tokensIn || card.tokensOut) ? `${card.tokensIn}↑ ${card.tokensOut}↓` : '';
  const label = { running: '', done: 'fertig', error: 'Fehler', cancelled: 'gestoppt' }[card.status] || card.status;
  m.textContent = [label, tok].filter(Boolean).join(' · ');
}

function _agentCardSetState(tab, card, status) {
  card.status = status;
  const dot = card.el.querySelector('.ap-dot');
  if (dot) dot.className = 'ap-dot ' + status;
  const stop = card.el.querySelector('.ap-stop');
  const x = card.el.querySelector('.ap-x');
  if (status !== 'running') {
    if (stop) stop.style.display = 'none';
    if (x) x.style.display = '';
  }
  _agentCardMeta(card);
  _terminalRenderTabs();  // Zähler/Puls im Tab-Label aktualisieren
}

// Attach an den Transcript-SSE; rendert ins Karten-Log + pflegt die Tail-Zeile.
function _agentCardAttach(tab, card) {
  const live = { curTextRow: null, curSegText: '', lastWasTool: false,
                 toolById: {}, thinkRow: null, think: '' };
  card._live = live;
  card._ctrl = API.streamBackgroundTranscript(
    card.taskId,
    /* onText */ (chunk) => {
      if (!live.curTextRow || live.lastWasTool) {
        live.curTextRow = _agentCardRow(card, 'tc-asst', '');
        live.curSegText = '';
        live.lastWasTool = false;
      }
      live.curSegText += chunk;
      if (live.curTextRow) live.curTextRow.innerHTML = renderMarkdown(live.curSegText);
      const t = live.curSegText.trim();
      _agentCardTail(card, '… ' + t.slice(-120));
    },
    /* onDone */ (d) => {
      const st = (d && d.status) || (d && d.error ? 'error' : 'done');
      if (d && d.error) _agentCardRow(card, 'tc-err', esc(String(d.error)));
      if (d && d.usage) {
        if (typeof d.usage.input === 'number') card.tokensIn = d.usage.input;
        if (typeof d.usage.output === 'number') card.tokensOut = d.usage.output;
      }
      _agentCardSetState(tab, card, st === 'running' ? 'done' : st);
      _agentCardTail(card, { done: '✓ fertig', error: '✗ Fehler', cancelled: '⏹ gestoppt' }[card.status] || card.status);
      if (card._notifyOnDone) _agentHubNotifyDelivery(card.sessionId);
    },
    /* onRequest */ (d) => {
      if (d && d.title) {
        const tEl = card.el.querySelector('.ap-title');
        if (tEl) tEl.textContent = d.title;
      }
      if (d && d.model) {
        card.model = d.model;
        const mEl = card.el.querySelector('.ap-model');
        if (mEl) mEl.textContent = (typeof modelShortName === 'function')
          ? modelShortName(d.model, false) : d.model;
      }
      if (d && d.prompt) {
        _agentCardRow(card, 'tc-user', `<span class="tc-uprompt">›</span> ${esc(d.prompt)}`);
      }
    },
    /* onTool */ (ev) => {
      if (!ev) return;
      if (ev.phase === 'start') {
        const arg = (typeof _tcToolArg === 'function') ? _tcToolArg(ev.args) : '';
        const row = _agentCardRow(card, 'tc-tool',
          `<span class="tc-tool-dot">●</span> <span class="tc-tool-name">${esc(ev.name || '')}</span>` +
          (arg ? ` <span class="tc-tool-arg">${esc(arg)}</span>` : '') +
          ` <span class="tc-tool-state">…</span>`);
        if (row && ev.tool_use_id) live.toolById[ev.tool_use_id] = row;
        live.lastWasTool = true;
        live.curTextRow = null;
        _agentCardTail(card, '● ' + (ev.name || '') + (arg ? ' ' + arg : ''));
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
        live.thinkRow = _agentCardRow(card, 'tc-think', '');
        live.think = '';
        live.curTextRow = null;
      }
      live.think += chunk;
      live.thinkRow.textContent = '⠿ ' + live.think;
      _agentCardTail(card, '⠿ denkt nach …');
    },
    /* onUsage */ (d) => {
      if (d && typeof d.tokens_in === 'number') card.tokensIn = d.tokens_in;
      if (d && typeof d.tokens_out === 'number') card.tokensOut = d.tokens_out;
      _agentCardMeta(card);
    },
  );
}

// Ergebnis-Zustellung sichtbar machen: wenn eine Aufgabe fertig wird, startet
// der Server (idle vorausgesetzt) einen DELIVERY-Turn in der SPAWNENDEN
// Session. Der Terminal-Chat hat aber nur seinen eigenen POST-Reader — extern
// gestartete Turns attacht er nie (User-Report: 'main chat verarbeitet die
// Ergebnisse nicht, wirkt tot'). Fix: offenen Terminal-Chat-Tab der Session
// per tcLoadTranscript neu laden — der rendert die Delivery-Nachricht UND
// attacht den laufenden Turn live (d.streaming → _tcAttachLive). Zwei
// verzögerte Versuche, weil die Delivery erst nach dem Group-Claim startet.
function _agentHubNotifyDelivery(sessionId) {
  if (!sessionId || typeof _term === 'undefined') return;
  const check = () => {
    const t = (_term.tabs || []).find(
      x => x.kind === 'chat' && x.sessionId === sessionId);
    if (!t || t.streaming) return;  // eigener Reader läuft → nichts zu tun
    if (typeof tcLoadTranscript === 'function') {
      try { tcLoadTranscript(t); } catch (_) {}
    }
  };
  setTimeout(check, 1500);
  setTimeout(check, 6000);
}

// Hook aus den Chat-tool_result-Callbacks (Terminal-Chat + Haupt-Chat):
// legt die Karte an, sobald run_background_task eine task_id liefert.
// Nur im Code-Mode (Bottom-Panel verfügbar) — sonst still no-op; das rechte
// Hintergrundaufgaben-Panel deckt den Nicht-Code-Fall unverändert ab.
function terminalMaybeOpenAgentPane(toolName, resultStr, title, turnKey, sessionId) {
  if (toolName !== 'run_background_task') return;
  if (typeof terminalAvailable !== 'function' || !terminalAvailable()) return;
  let taskId = '';
  try {
    const r = JSON.parse(resultStr || '{}');
    taskId = r.task_id || '';
    if (!taskId || r.error) return;
  } catch (_) { return; }
  if (typeof _term !== 'undefined' && !_term.open && typeof terminalTogglePanel === 'function') {
    try { terminalTogglePanel(true); } catch (_) { return; }
  }
  const key = turnKey || 'turn';
  if (key !== _agentPaneAutoTurnKey) { _agentPaneAutoTurnKey = key; _agentPaneAutoCount = 0; }
  if (_agentPaneAutoCount >= _AGENT_PANE_AUTO_MAX) return;
  _agentPaneAutoCount += 1;
  // Panel-Öffnung baut Panes async auf — Karten-Erzeugung leicht verzögern.
  setTimeout(() => { try { terminalOpenAgentPane(taskId, title || '', sessionId || ''); } catch (_) {} }, 250);
}

// Re-Attach beim Laden des Bottom-Panels: die Hintergrundaufgaben der offenen
// Chat-Sessions (Terminal-Chats + aktiver Haupt-Chat) als Karten wiederholen —
// LAUFENDE live (Replay+Follow) UND FERTIGE aus dem Stored-Replay (der seit
// 9.312.0 auch die gespeicherten Tool-Events ausspielt), damit ein Seiten-
// Reload die Subagenten-Ansicht nicht verliert (User-Anforderung). Gecappt
// auf die neuesten 12 je Ladevorgang, damit alte Sessions das Hub nicht fluten.
const _AGENT_HUB_REATTACH_MAX = 12;
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
  let all = [];
  for (const sid of sids) {
    try {
      const d = await API.getBackgroundTasks(sid);
      for (const t of ((d && d.tasks) || [])) {
        t._sid = sid;
        all.push(t);
      }
    } catch (_) { /* Session ohne Tasks / transient */ }
  }
  // Neueste zuerst behalten, dann ÄLTESTE zuerst anlegen (prepend → neueste oben).
  all.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  all = all.slice(0, _AGENT_HUB_REATTACH_MAX).reverse();
  for (const t of all) {
    const hub = _agentHubTab();
    if (hub && hub._cards[t.id]) continue;
    terminalOpenAgentPane(t.id, t.title || '', t.session_id || t._sid,
      { activate: false, notify: t.status === 'running' });
  }
}
