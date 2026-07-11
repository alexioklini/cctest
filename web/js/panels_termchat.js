// panels_termchat.js — Code-Mode Terminal-Chat.
//
// A terminal-STYLED chat pane (NOT xterm.js): a "Claude Code in the terminal"
// experience that lives as a third tab KIND ('chat') inside the bottom-workspace
// split-pane system (panels_terminal.js), alongside terminal + editor tabs. It
// is a complete replacement for the normal chat view + composer when working in
// a code-mode project: streamed markdown, tool-call lines, a braille spinner, a
// live status footer, up/down prompt history, and slash commands.
//
// Persistence: each terminal-chat is a regular session created with
// status='code_chat' + the project bound, so it reuses the ENTIRE chat backend
// (POST /v1/chat → worker → sidecar streaming, project-instruction injection,
// code-graph tools). Because of the custom status it never appears in the normal
// project/sidebar chat lists — it is surfaced only in the "Terminal-Chats"
// section under the working-dir tree (renderTermchatHistory below).
//
// Globals only, fixed load order (after panels_workdir.js). Reuses esc(),
// renderMarkdown(), showToast(), BASE_URL, API, state.

// ── Tab object shape (kind:'chat') ───────────────────────────────────────────
//   { id:'chat-'+sid, kind:'chat', sessionId, name, el,
//     model, thinking, caveman, showTools,
//     history:[sent prompts], histIdx, draft,
//     streaming, _abort, _spinTimer, _spinIdx, _spinLabel,
//     log:[row-models], live:{textEl,thinkEl},
//     tokensIn, tokensOut, cached, cost, costList, lastApiIn, maxContext }

const _TC_SPIN = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const _TC_THINK = { '': 'Aus', none: 'Aus', low: 'Niedrig', medium: 'Mittel', high: 'Hoch' };

function _tcTab(id) { return (typeof _term !== 'undefined' ? _term.tabs : []).find(t => t.id === id && t.kind === 'chat'); }
function _tcActiveTab() {
  const p = (typeof _terminalActivePane === 'function') ? _terminalActivePane() : null;
  if (p) { const t = _tcTab(p.active); if (t) return t; }
  return (typeof _term !== 'undefined' ? _term.tabs : []).find(t => t.kind === 'chat' && t.id === (p && p.active)) || null;
}

// Build the chat-pane DOM into a tab's `el`. Called once by _terminalAddChatTab.
function tcBuildBody(tab) {
  const el = tab.el;
  el.className = 'termchat';
  // CLI-style layout: the composer (.tc-input-wrap) lives INSIDE the scrollback
  // (.tc-log), right after the messages, so it scrolls with the conversation and
  // sits directly under the last response — like the Claude Code CLI prompt.
  // Only the status footer stays pinned at the bottom.
  // NOTE: the slash-command autocomplete (.tc-ac) is a child of .termchat — NOT
  // of .tc-input-wrap — because .tc-log has overflow:auto, which would CLIP an
  // absolutely-positioned popup living inside it. As a sibling of .tc-log it
  // floats freely; _tcAcRender positions it just above the input line.
  el.innerHTML = `
    <div class="tc-log" id="${esc(tab.id)}-log">
      <div class="tc-input-wrap">
        <div class="tc-input">
          <span class="tc-prompt">›</span>
          <textarea class="tc-ta" rows="1" spellcheck="false"
            placeholder="Nachricht … (/ Befehle · ! für Shell · ↑↓ Verlauf)"></textarea>
        </div>
      </div>
    </div>
    <div class="tc-ac" id="${esc(tab.id)}-ac" style="display:none"></div>
    <div class="tc-status" id="${esc(tab.id)}-status"></div>`;
  const ta = el.querySelector('.tc-ta');
  ta.addEventListener('keydown', (e) => _tcKeydown(e, tab));
  ta.addEventListener('input', () => { _tcAutosize(ta); tab.draft = ta.value; _tcAcUpdate(tab); });
  ta.addEventListener('blur', () => setTimeout(() => _tcAcClose(tab), 120));
  // Esc cancels a running stream from ANYWHERE in the chat pane (not just the
  // textarea) — e.g. after clicking into the scrollback. Pane-level listener;
  // the textarea's own handler still covers the focused case (we guard against
  // double-handling by only acting here when the textarea isn't the target).
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && tab.streaming && e.target !== ta) {
      e.preventDefault();
      tcCancel(tab);
    }
  });
  // Make the pane focusable so it receives key events even when nothing inside
  // is focused (clicking the log focuses the pane).
  el.tabIndex = -1;
  // Refocus the composer only when the user clicks the EMPTY area of the log (or
  // the composer line) — NOT when clicking on a message row (they're reading or
  // selecting history; stealing focus would scroll the textarea into view and
  // jump to the end). Also skip when a text selection exists. preventScroll keeps
  // the viewport put even when we do focus.
  el.addEventListener('click', (e) => {
    if (window.getSelection && window.getSelection().toString()) return;
    const t = e.target;
    const onWrap = t.closest && t.closest('.tc-input-wrap');
    const onRow = t.closest && (t.closest('.tc-row') || t.closest('.tc-tool') || t.closest('.tc-tools'));
    // Clicking inside the composer: the textarea handles its own focus. Clicking
    // a content row: leave focus + scroll alone. Only a click on the bare log
    // background refocuses the composer for typing.
    if (onWrap || onRow) return;
    if (t === el || (t.closest && t.closest('.tc-log'))) {
      try { ta.focus({ preventScroll: true }); } catch (_) { try { ta.focus(); } catch (__) {} }
    }
  });
  // Track whether the user is "stuck" to the bottom on every scroll. This is the
  // single source of truth for auto-follow (see _tcScroll) — measuring at mutate
  // time would mis-read because the just-added row already shifted the viewport.
  // Reaching the bottom also clears the "new content" badge.
  const logEl = el.querySelector('.tc-log');
  if (logEl) logEl.addEventListener('scroll', () => {
    tab._stick = _tcAtBottom(tab);
    if (tab._stick) _tcHideNewBadge(tab);
  });
  tab._stick = true;   // fresh pane starts pinned to the bottom
  tab._ac = { open: false, items: [], sel: 0 };
  tcRenderStatus(tab);
}

function _tcAutosize(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
}

function _tcLog(tab) { return document.getElementById(tab.id + '-log'); }

// Is the log scrolled to (within a small slack of) the bottom? Used to gate
// auto-scroll: we only follow new content when the user is already at the end,
// so scrolling up to read history is never interrupted.
function _tcAtBottom(tab) {
  const l = _tcLog(tab);
  if (!l) return true;
  // 40px slack covers sub-pixel rounding + the composer's own height.
  return (l.scrollHeight - l.scrollTop - l.clientHeight) <= 40;
}
// Force scroll to the very bottom (used on explicit user intent: sending a
// message, clicking the new-content pill).
function _tcScrollToEnd(tab) {
  const l = _tcLog(tab);
  if (l) l.scrollTop = l.scrollHeight;
  tab._stick = true;
  _tcHideNewBadge(tab);
}
// Conditional auto-scroll: follow the tail ONLY when the user is "stuck" to the
// bottom. `tab._stick` is recomputed on every USER scroll (see the scroll
// listener), so the decision does NOT depend on measuring after a DOM mutation
// (which would already have pushed the viewport up by the new row's height and
// mis-read "not at bottom"). When not stuck, surface the "new content" badge.
function _tcScroll(tab) {
  if (tab._stick !== false) { _tcScrollToEnd(tab); }
  else { _tcShowNewBadge(tab); }
}

// "↓ Neue Nachrichten" pill, shown over the log's bottom edge when content lands
// while the user is scrolled up. Lives in the pane (.termchat), positioned by
// CSS; clicking it jumps to the end.
function _tcNewBadge(tab, create) {
  if (!tab.el) return null;
  let b = tab.el.querySelector('.tc-newbadge');
  if (!b && create) {
    b = document.createElement('button');
    b.className = 'tc-newbadge';
    b.textContent = '↓ Neue Nachrichten';
    b.onclick = () => _tcScrollToEnd(tab);
    tab.el.appendChild(b);
  }
  return b;
}
function _tcShowNewBadge(tab) { const b = _tcNewBadge(tab, true); if (b) b.classList.add('show'); }
function _tcHideNewBadge(tab) { const b = _tcNewBadge(tab, false); if (b) b.classList.remove('show'); }

// The composer (.tc-input-wrap) lives INSIDE .tc-log (CLI-style, scrolls with
// the conversation), so message rows must be inserted BEFORE it to keep the
// prompt last. Use this instead of log.appendChild for any chat/shell row.
function _tcInputWrap(tab) { const l = _tcLog(tab); return l ? l.querySelector('.tc-input-wrap') : null; }
function _tcAddRow(tab, node) {
  const log = _tcLog(tab); if (!log) return;
  const wrap = _tcInputWrap(tab);
  if (wrap) log.insertBefore(node, wrap); else log.appendChild(node);
}

// Shared live-turn state. Tool cards + answer-text rows are appended in ARRIVAL
// order (chronological) via _tcLiveInsert, so a multi-round turn renders
// think → tool → text → tool → text instead of clustering all tools at the top.
// `curTextRow` is the answer-text row currently being appended to; a tool_call
// closes it (lastWasTool=true) so the next text delta starts a FRESH text row
// after the tool card — preserving text↔tool interleaving.
function _tcNewLive(thinkRow, spinRow) {
  return { thinkRow, spinRow, text: '', think: '', toolById: {},
           curTextRow: null, lastWasTool: false };
}
// Insert a live row chronologically — right BEFORE the trailing spinner row.
function _tcLiveInsert(tab, live, node) {
  const log = _tcLog(tab); if (!log) return;
  if (live.spinRow && live.spinRow.parentNode === log) log.insertBefore(node, live.spinRow);
  else _tcAddRow(tab, node);
}
// Clear all message rows but KEEP the composer (.tc-input-wrap) in place.
function _tcClearRows(tab) {
  const log = _tcLog(tab); if (!log) return;
  const wrap = _tcInputWrap(tab);
  Array.from(log.children).forEach(c => { if (c !== wrap) c.remove(); });
  _tcHideNewBadge(tab);
}

// ── Input handling: Enter to send, Shift+Enter newline, ↑/↓ history ──────────
function _tcKeydown(e, tab) {
  const ta = e.target;
  // Autocomplete menu owns ↑/↓/Enter/Tab/Esc while it's open.
  if (_tcAcKey(e, tab)) return;
  // Esc cancels a running stream (when the autocomplete menu isn't open).
  if (e.key === 'Escape' && tab.streaming) {
    e.preventDefault();
    tcCancel(tab);
    return;
  }
  // Turn-control shortcuts while a turn is streaming: Ctrl-Z pause / Ctrl-Q
  // resume (Esc stays cancel). Chosen to avoid clobbering Enter/history keys.
  if (tab.streaming && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
    const k = (e.key || '').toLowerCase();
    if (k === 'z') { e.preventDefault(); tcPause(tab); return; }
    if (k === 'q') { e.preventDefault(); tcResume(tab); return; }
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) return;
    // While a turn streams: cancel commands always work; the turn-control slash
    // commands (/pause /resume /btw /inject /queue) are handled by tcSlash; and
    // a plain message is QUEUED (auto-sent as a normal turn when this one ends).
    if (tab.streaming) {
      const low = text.toLowerCase();
      if (low === '/cancel' || low === '/stop') {
        ta.value = ''; _tcAutosize(ta); tab.draft = ''; tcCancel(tab); return;
      }
      ta.value = ''; _tcAutosize(ta); tab.draft = ''; tab.histIdx = -1;
      if (text.startsWith('/')) { tcSlash(tab, text); return; }
      if (text.startsWith('!')) { return; }   // no shell mid-stream
      tcQueueAdd(tab, text);                   // plain text → queue
      return;
    }
    ta.value = ''; _tcAutosize(ta); tab.draft = '';
    tab.histIdx = -1;
    tcSubmit(tab, text);
    return;
  }
  // History only when the caret sits on the first/last line and there's a
  // history to walk — otherwise let arrows move the caret normally.
  if (e.key === 'ArrowUp' && tab.history.length && ta.selectionStart === 0) {
    e.preventDefault();
    if (tab.histIdx === -1) { tab.draft = ta.value; tab.histIdx = tab.history.length; }
    if (tab.histIdx > 0) { tab.histIdx--; ta.value = tab.history[tab.histIdx]; _tcAutosize(ta); }
    return;
  }
  if (e.key === 'ArrowDown' && tab.histIdx !== -1 && ta.selectionStart === ta.value.length) {
    e.preventDefault();
    tab.histIdx++;
    if (tab.histIdx >= tab.history.length) { tab.histIdx = -1; ta.value = tab.draft || ''; }
    else ta.value = tab.history[tab.histIdx];
    _tcAutosize(ta);
    return;
  }
  // Tab on empty input → on-demand next-prompt suggestion (fills the line).
  if (e.key === 'Tab' && !ta.value.trim() && tab.sessionId && !tab.streaming) {
    e.preventDefault();
    _tcSuggest(tab, true);
  }
}

// ── Submit: slash command OR a chat turn ─────────────────────────────────────
async function tcSubmit(tab, text) {
  tab.history.push(text);
  if (text.startsWith('!')) { await tcShell(tab, text.slice(1).trim()); return; }
  if (text.startsWith('/')) { await tcSlash(tab, text); return; }
  await tcSend(tab, text);
}

// ── Slash-command autocomplete ───────────────────────────────────────────────
// Registry: each command has a description and (for value-taking commands) a
// `values()` returning [{value, label, hint}] for the second-level menu.
function _tcModelValues() {
  const models = (state.models || []).map(m => (typeof m === 'string' ? m : (m.id || m.name))).filter(Boolean);
  return [{ value: 'auto', label: 'auto', hint: 'automatische Wahl' }]
    .concat(models.map(m => ({ value: m, label: m, hint: '' })));
}
const _TC_COMMANDS = [
  { name: 'model', desc: 'Modell wechseln', values: _tcModelValues },
  { name: 'think', desc: 'Denktiefe', values: () => [
    { value: 'off', label: 'off', hint: 'Aus' }, { value: 'low', label: 'low', hint: 'Niedrig' },
    { value: 'medium', label: 'medium', hint: 'Mittel' }, { value: 'high', label: 'high', hint: 'Hoch' } ] },
  { name: 'tools', desc: 'Werkzeugaufrufe anzeigen', values: () => [
    { value: 'on', label: 'on', hint: 'anzeigen' }, { value: 'off', label: 'off', hint: 'ausblenden' } ] },
  { name: 'caveman', desc: 'Antwortstil', values: () => [
    { value: '0', label: '0', hint: 'aus' }, { value: '1', label: '1', hint: 'leicht' },
    { value: '2', label: '2', hint: 'stark' }, { value: '3', label: '3', hint: 'extrem' } ] },
  { name: 'goal', desc: 'Ziel setzen — Agent arbeitet bis zur Erfüllung weiter [<text>|off|status]' },
  { name: 'clear', desc: 'neue Sitzung (leerer Kontext)' },
  { name: 'lcm', desc: 'Kontext komprimieren (LCM)' },
  { name: 'sync', desc: 'Projekt-Sync anstoßen' },
  { name: 'init', desc: 'BRAIN.md erzeugen' },
  { name: 'suggest', desc: 'nächste Eingabe vorschlagen' },
  { name: 'cancel', desc: 'laufende Antwort abbrechen' },
  { name: 'pause', desc: 'laufende Antwort pausieren (Strg-Z)' },
  { name: 'resume', desc: 'pausierte Antwort fortsetzen (Strg-Q)' },
  { name: 'btw', desc: 'Nebenfrage (separate Blase, unterbricht nicht)' },
  { name: 'inject', desc: 'Klarstellung in die laufende Antwort einfügen' },
  { name: 'clarify', desc: 'Klarstellung einfügen (Alias für /inject)' },
  { name: 'queue', desc: 'Warteschlange: [list|rm N|mv N M|edit N …|clear]' },
  { name: 'workflow', desc: 'Workflow aus diesem Chat erzeugen (KI) [<session_id>]' },
  { name: 'help', desc: 'Befehlsübersicht' },
];

function _tcAcEl(tab) { return document.getElementById(tab.id + '-ac'); }

// Recompute the autocomplete items from the current input + render. Two levels:
// typing "/mod" → matching COMMANDS; typing "/model " → that command's VALUES.
function _tcAcUpdate(tab) {
  const ta = tab.el.querySelector('.tc-ta');
  const v = ta.value;
  if (!v.startsWith('/') || /\n/.test(v)) { _tcAcClose(tab); return; }
  const m = v.match(/^\/(\S*)(\s+)(.*)$/);   // "/cmd <space> rest"
  let items, mode;
  if (m) {
    const cmd = _TC_COMMANDS.find(c => c.name === m[1].toLowerCase());
    if (!cmd || !cmd.values) { _tcAcClose(tab); return; }
    const frag = (m[3] || '').toLowerCase();
    items = cmd.values().filter(o => o.value.toLowerCase().includes(frag))
      .slice(0, 50).map(o => ({ ...o, _cmd: cmd.name, kind: 'value' }));
    mode = 'value';
  } else {
    const frag = v.slice(1).toLowerCase();
    items = _TC_COMMANDS.filter(c => c.name.startsWith(frag))
      .map(c => ({ value: c.name, label: '/' + c.name, hint: c.desc, kind: 'cmd' }));
    mode = 'cmd';
  }
  if (!items.length) { _tcAcClose(tab); return; }
  tab._ac = { open: true, items, sel: 0, mode };
  _tcAcRender(tab);
}

function _tcAcRender(tab) {
  const ac = _tcAcEl(tab); if (!ac) return;
  const { items, sel } = tab._ac;
  ac.innerHTML = items.map((it, i) => `
    <div class="tc-ac-item${i === sel ? ' sel' : ''}" data-i="${i}"
      onmousedown="event.preventDefault();_tcAcPick(_tcTab('${esc(tab.id)}'), ${i})"
      onmousemove="_tcAcHover(_tcTab('${esc(tab.id)}'), ${i})">
      <span class="tc-ac-label">${esc(it.label)}</span>
      ${it.hint ? `<span class="tc-ac-hint">${esc(it.hint)}</span>` : ''}
    </div>`).join('');
  ac.style.display = 'block';
  const selEl = ac.querySelector('.tc-ac-item.sel');
  if (selEl) selEl.scrollIntoView({ block: 'nearest' });
}

function _tcAcClose(tab) {
  if (tab._ac) tab._ac.open = false;
  const ac = _tcAcEl(tab); if (ac) { ac.style.display = 'none'; ac.innerHTML = ''; }
}
function _tcAcHover(tab, i) { if (tab && tab._ac && tab._ac.open) { tab._ac.sel = i; _tcAcRender(tab); } }

// Accept the selected suggestion. Command → fill "/name " (and immediately open
// its value menu if it has values, else for no-value commands leave it ready to
// Enter). Value → fill "/cmd value" complete.
function _tcAcPick(tab, i) {
  if (!tab || !tab._ac || !tab._ac.open) return;
  const it = tab._ac.items[i != null ? i : tab._ac.sel];
  if (!it) return;
  const ta = tab.el.querySelector('.tc-ta');
  if (it.kind === 'cmd') {
    const cmd = _TC_COMMANDS.find(c => c.name === it.value);
    if (cmd && cmd.values) {
      // Value-taking command: fill "/name " and chain into the value menu.
      ta.value = '/' + it.value + ' ';
      _tcAcClose(tab);
      ta.focus(); _tcAutosize(ta);
      _tcAcUpdate(tab);
      return;
    }
    // No-value command (/help, /clear, …) → EXECUTE immediately (no second Enter).
    _tcAcClose(tab);
    ta.value = ''; _tcAutosize(ta); tab.draft = ''; tab.histIdx = -1;
    tcSubmit(tab, '/' + it.value);
    return;
  }
  // A value pick completes the command → EXECUTE immediately.
  _tcAcClose(tab);
  ta.value = ''; _tcAutosize(ta); tab.draft = ''; tab.histIdx = -1;
  tcSubmit(tab, '/' + it._cmd + ' ' + it.value);
}

// Keyboard handling while the menu is open (called from _tcKeydown). Returns
// true if it consumed the key.
function _tcAcKey(e, tab) {
  const ac = tab._ac;
  if (!ac || !ac.open || !ac.items.length) return false;
  if (e.key === 'ArrowDown') { e.preventDefault(); ac.sel = (ac.sel + 1) % ac.items.length; _tcAcRender(tab); return true; }
  if (e.key === 'ArrowUp') { e.preventDefault(); ac.sel = (ac.sel - 1 + ac.items.length) % ac.items.length; _tcAcRender(tab); return true; }
  if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); _tcAcPick(tab, ac.sel); return true; }
  if (e.key === 'Escape') { e.preventDefault(); _tcAcClose(tab); return true; }
  return false;
}

// `! <command>` — run a one-shot shell command in the project's working_dir and
// print stdout/stderr + exit code (no LLM, no chat session needed). Backed by
// POST .../terminal/run.
async function tcShell(tab, command) {
  if (!command) { tcPrint(tab, 'Verwendung: ! &lt;Shell-Befehl&gt;', 'tc-err'); return; }
  if (!_term || !_term.project) { tcPrint(tab, 'Kein Code-Mode-Projekt.', 'tc-err'); return; }
  // Echo the command shell-style, then a running indicator.
  tcPrint(tab, `<span class="tc-shprompt">$</span> ${esc(command)}`, 'tc-shell');
  const running = document.createElement('div');
  running.className = 'tc-row tc-spin';
  _tcAddRow(tab, running);
  tab._shellSpin = setInterval(() => {
    tab._spinIdx = ((tab._spinIdx || 0) + 1) % _TC_SPIN.length;
    running.innerHTML = `<span class="tc-spinner">${_TC_SPIN[tab._spinIdx]}</span> läuft…`;
  }, 80);
  try {
    // Own fetch (not API.post — that throws on a 4xx and swallows the server's
    // error body; a banned/invalid command returns 400 {error} we want to show).
    const resp = await fetch(`${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/run`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    });
    clearInterval(tab._shellSpin); tab._shellSpin = null; running.remove();
    let r = {}; try { r = await resp.json(); } catch (_) {}
    if (r && r.error) { tcPrint(tab, esc(r.error), 'tc-err'); return; }
    const out = (r && r.output != null) ? String(r.output) : '';
    const code = (r && typeof r.exit_code === 'number') ? r.exit_code : 0;
    if (out.trim()) {
      const pre = document.createElement('div');
      pre.className = 'tc-row tc-shout';
      pre.textContent = out.replace(/\n+$/, '');
      _tcAddRow(tab, pre);
    }
    // Exit-code line (green for 0, red otherwise; suppress the noisy "exit 0").
    if (code !== 0) tcPrint(tab, `<span class="tc-exit-bad">exit ${code}</span>`, 'tc-shell');
    _tcScroll(tab);
    // A command may have written/changed files → refresh the working-dir tree.
    if (typeof refreshTerminalTree === 'function') refreshTerminalTree();
  } catch (e) {
    clearInterval(tab._shellSpin); tab._shellSpin = null; running.remove();
    tcPrint(tab, 'Befehl fehlgeschlagen.', 'tc-err');
  }
}

// Print a terminal-style line into the log (system/ack/error rows).
function tcPrint(tab, html, cls) {
  const log = _tcLog(tab);
  if (!log) return;
  const div = document.createElement('div');
  div.className = 'tc-row ' + (cls || 'tc-sys');
  div.innerHTML = html;
  _tcAddRow(tab, div);
  _tcScroll(tab);
}

// ── Send a chat turn (own lean SSE loop — NOT API.streamChat, which reads the
// composer globals). Streams into dedicated live rows in the log. ─────────────
async function tcSend(tab, text) {
  if (!tab.sessionId) {
    // Lazily create the code_chat session on first send (so empty tabs don't
    // pollute the DB / history list).
    const ok = await _tcEnsureSession(tab);
    if (!ok) { tcPrint(tab, 'Konnte keine Sitzung erstellen.', 'tc-err'); return; }
  }
  // user echo
  tcPrint(tab, `<span class="tc-uprompt">›</span> ${esc(text)}`, 'tc-user');

  // live rows for this turn. Only the transient think + spin rows are
  // pre-created; tool cards and answer-text rows are appended CHRONOLOGICALLY as
  // events arrive (see _tcCallbacks), so a multi-round turn renders
  // think → tool → text → tool → text in real order instead of clustering all
  // tools at the top. New rows are inserted BEFORE spinRow to keep the spinner
  // trailing. `_tcNewLive` builds the shared live-state object.
  const thinkRow = document.createElement('div'); thinkRow.className = 'tc-row tc-think'; thinkRow.style.display = 'none';
  const spinRow = document.createElement('div'); spinRow.className = 'tc-row tc-spin';
  _tcAddRow(tab, thinkRow); _tcAddRow(tab, spinRow);
  const live = _tcNewLive(thinkRow, spinRow);
  tab._live = live;

  // Sending is explicit intent to follow the new turn → always jump to the end.
  _tcScrollToEnd(tab);

  tab.streaming = true;
  _tcSpinStart(tab, 'Denkt nach');
  tcRenderStatus(tab);

  const body = {
    session_id: tab.sessionId,
    message: text,
    interactive: true,
    thinking: tab.thinking || 'none',
  };
  if (tab.model) body.model = tab.model;
  if (_term && _term.project) body.project = _term.project;

  tab._abort = new AbortController();
  try {
    const resp = await fetch(`${BASE_URL}/v1/chat`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: tab._abort.signal,
    });
    await _tcReadSSE(tab, resp, _tcCallbacks(tab, live));
  } catch (e) {
    if (e.name !== 'AbortError' && !/Load failed|Failed to fetch|NetworkError/i.test(e.message)) {
      tcPrint(tab, esc(e.message || 'Streaming-Fehler'), 'tc-err');
    }
  } finally {
    // GUARANTEED finalize: the SSE stream can close WITHOUT a terminal `done`/
    // `error` event reaching our callback (a missed/unparsed frame, a long
    // multi-round turn, a server-side stream close). If `done` already ran,
    // tab.streaming is false and this is a cheap no-op; otherwise this is what
    // stops the spinner so a finished turn never shows "running" forever.
    if (tab.streaming) _tcFinishTurn(tab, live);
  }
}

// Shared SSE frame reader (event:/data: lines, isolates callback exceptions).
// Stall watchdog (9.277.0, mirrors API._readOrStall): the server emits a
// keepalive every 5s for the whole life of a turn, so 45s of byte-silence
// means the connection is half-dead (tunnel drop, sleep/wake) — reader.read()
// would otherwise block forever and the finally-finalize in the callers would
// never run (the bodensatz case: turn finished server-side, spinner ran on).
// On stall: abort the fetch and return; the caller finalizes, and the LAST
// persisted state is one openSession/chat-history refresh away.
async function _tcReadSSE(tab, resp, cbs) {
  if (!resp.ok || !resp.body) { _tcFinishTurn(tab, tab._live); tcPrint(tab, 'Server-Fehler.', 'tc-err'); return; }
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '', ev = null;
  const dispatch = (line) => {
    if (line.startsWith('event: ')) ev = line.slice(7).trim();
    else if (line.startsWith('data: ') && ev) {
      let d = null; try { d = JSON.parse(line.slice(6)); } catch (_) { ev = null; return; }
      if (cbs[ev]) { try { cbs[ev](d); } catch (cbErr) { console.error('[tc-sse]', ev, cbErr); } }
      ev = null;
    }
  };
  const STALL_MS = 45000;
  for (;;) {
    let chunk;
    let timer;
    const readP = reader.read();
    readP.catch(() => {});  // pre-arm: the abort below rejects the pending read
    try {
      chunk = await Promise.race([
        readP,
        new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('sse-stalled')), STALL_MS); }),
      ]);
    } catch (stallErr) {
      if (stallErr && stallErr.message === 'sse-stalled') {
        console.warn('[tc-sse] stream stalled >45s — aborting dead connection');
        try { if (tab._abort) tab._abort.abort(); } catch (_) {}
        try { reader.cancel(); } catch (_) {}
        break;
      }
      throw stallErr;
    } finally { clearTimeout(timer); }
    const { value, done } = chunk;
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split('\n'); buf = lines.pop();
    for (const l of lines) dispatch(l);
  }
  if (buf.trim()) for (const l of buf.split('\n')) dispatch(l);
}

// Lean callback map → drives the terminal renderer. Mirrors the server's SSE
// event vocabulary (subset relevant to a terminal-chat).
function _tcCallbacks(tab, live) {
  return {
    thinking_start: () => {
      live.think = '';
      if (tab.showTools) {
        // Each round's thinking gets its OWN row inserted at the current end
        // (before the spinner) — NOT the single pinned row from turn start,
        // which left all thinking at the top while tool cards stacked below it
        // (broke the think→tool→think→tool execution order).
        live.thinkRow = document.createElement('div');
        live.thinkRow.className = 'tc-row tc-think';
        _tcLiveInsert(tab, live, live.thinkRow);
        // Close the current answer row so following text lands AFTER the thinking.
        live.curTextRow = null;
      }
      _tcSpinSet(tab, 'Denkt nach');
    },
    thinking_delta: (d) => {
      live.think += d.text || '';
      if (tab.showTools) { live.thinkRow.textContent = '⠿ ' + live.think; _tcScroll(tab); }
    },
    thinking_done: () => {
      // Drop an empty round row (thinking_start fired but nothing streamed).
      if (live.thinkRow && live.thinkRow.parentNode && !live.thinkRow.textContent) live.thinkRow.remove();
      live.think = '';
    },
    queue_wait: (d) => { _tcSpinSet(tab, 'Wartet in der Warteschlange' + (d.position ? ` (#${d.position})` : '')); },
    queue_acquired: () => { _tcSpinSet(tab, 'Denkt nach'); },
    tool_call: (d) => {
      // Subagent-Pane: Titel des Spawns für den tool_result-Hook vormerken
      // (tool_result trägt keine args mehr).
      if (d.name === 'run_background_task' && d.tool_use_id) {
        (live._bgSpawn = live._bgSpawn || {})[d.tool_use_id] = (d.args || {}).title || '';
      }
      if (!tab.showTools) { _tcSpinSet(tab, 'Werkzeug: ' + (d.name || '')); return; }
      const tuid = d.tool_use_id || ('t' + Object.keys(live.toolById).length);
      let row = live.toolById[tuid];
      if (!row) {
        // New tool → its own row, appended chronologically (after whatever text
        // came before it). Close the current text row so a following text delta
        // opens a fresh one AFTER this tool card (text↔tool interleaving).
        row = document.createElement('div'); row.className = 'tc-row tc-tool';
        _tcLiveInsert(tab, live, row);
        live.toolById[tuid] = row;
        live.lastWasTool = true;
        live.curTextRow = null;
      }
      const arg = _tcToolArg(d.args);
      row.innerHTML = `<span class="tc-tool-dot">●</span> <span class="tc-tool-name">${esc(d.name || '')}</span>` +
                      (arg ? ` <span class="tc-tool-arg">${esc(arg)}</span>` : '') +
                      ` <span class="tc-tool-state">…</span>`;
      _tcSpinSet(tab, 'Werkzeug: ' + (d.name || ''));
      _tcScroll(tab);
    },
    tool_result: (d) => {
      // Subagent-Pane im Bottom-Panel öffnen, sobald run_background_task eine
      // task_id liefert (Code-Mode only; no-op sonst).
      if (d.name === 'run_background_task' && typeof terminalMaybeOpenAgentPane === 'function') {
        live._apKey = live._apKey || (tab.id + '-' + Date.now());
        terminalMaybeOpenAgentPane(d.name, d.result,
          (live._bgSpawn || {})[d.tool_use_id] || '', live._apKey, tab.sessionId);
      }
      if (!tab.showTools) return;
      const row = live.toolById[d.tool_use_id];
      if (row) { const s = row.querySelector('.tc-tool-state'); if (s) { s.textContent = '✓'; s.classList.add('ok'); } }
      _tcScroll(tab);
    },
    text_delta: (d) => {
      live.text += d.text || '';
      _tcSpinStop(tab);            // real text flowing → drop the spinner
      // Start a fresh answer-text row if none is open yet OR a tool just
      // interrupted → the new text lands AFTER the tool card, chronologically.
      if (!live.curTextRow || live.lastWasTool) {
        live.curTextRow = document.createElement('div');
        live.curTextRow.className = 'tc-row tc-asst';
        live.curSegText = '';
        _tcLiveInsert(tab, live, live.curTextRow);
        live.lastWasTool = false;
      }
      live.curSegText = (live.curSegText || '') + (d.text || '');
      live.curTextRow.innerHTML = renderMarkdown(live.curSegText);
      _tcScroll(tab);
    },
    usage: (d) => {
      // Live per-round figures for THIS turn (cumulative within the turn). Held
      // separately so the footer can show session-total + this-turn while
      // streaming; committed into the totals at `done`.
      if (typeof d.tokens_in === 'number') tab._liveIn = d.tokens_in;
      if (typeof d.tokens_out === 'number') tab._liveOut = d.tokens_out;
      if (typeof d.cache_read_tokens === 'number') tab._liveCached = d.cache_read_tokens;
      if (d.cost != null) tab.cost = d.cost;
      if (d.cost_list != null) tab.costList = d.cost_list;
      if (typeof d.last_tokens_in === 'number') tab.lastApiIn = d.last_tokens_in;
      tcRenderStatus(tab);
    },
    // ── Turn-control events ──
    paused: () => {
      tab._paused = true;
      _tcSpinSet(tab, 'Pausiert');
      tcPrint(tab, '⏸ Pausiert — /resume oder Strg-Q zum Fortsetzen.', 'tc-sys');
      tcRenderStatus(tab);
    },
    resumed: () => {
      tab._paused = false;
      _tcSpinSet(tab, 'Denkt nach');
      tcPrint(tab, '▶ Fortgesetzt.', 'tc-sys');
      tcRenderStatus(tab);
    },
    injected_pending: (d) => {
      tcPrint(tab, '↳ Eingefügt (wird in der nächsten Runde berücksichtigt): '
              + esc(d.text || ''), 'tc-inject');
    },
    injected_message: (d) => {
      // Already surfaced as pending; this confirms the loop spliced it in.
    },
    // ── Goal-Modus events ──
    goal_judge_start: (d) => {
      _tcSpinSet(tab, `Ziel wird geprüft (Iteration ${d.iteration}/${d.max})`);
    },
    goal_verdict: (d) => {
      tab.goalStatus = d.status || tab.goalStatus;
      if (d.status === 'fulfilled') {
        tcPrint(tab, '🎯 <b>Ziel erreicht</b>' + (d.reasoning ? ' — ' + esc(d.reasoning) : ''), 'tc-sys');
      } else if (d.status === 'capped') {
        const why = d.impossible ? 'nicht erreichbar / legitime Ablehnung' : `Limit ${d.max} Iterationen`;
        tcPrint(tab, `🎯 Ziel nicht erreicht (${esc(why)})` + (d.reasoning ? ' — ' + esc(d.reasoning) : ''), 'tc-err');
      } else if (d.status === 'judge_error') {
        tcPrint(tab, '🎯 Ziel-Prüfung fehlgeschlagen — Durchlauf beendet.', 'tc-err');
      }
      tcRenderStatus(tab);
    },
    goal_continue: (d) => {
      // Iteration boundary: close the current answer segment so the next
      // text_delta opens a fresh row AFTER the continue-instruction line.
      live.curTextRow = null; live.lastWasTool = false; live.curSegText = '';
      tcPrint(tab, `🎯 Iteration ${d.iteration}/${d.max} — automatische Fortsetzung: ${esc(d.text || '')}`, 'tc-inject');
      _tcSpinSet(tab, 'Denkt nach');
    },
    moa_verify_continue: (d) => {
      // Post-verify re-round: close the segment, announce the requested fix.
      live.curTextRow = null; live.lastWasTool = false; live.curSegText = '';
      tcPrint(tab, `🧬 Nachbesserung ${d.round}/${d.max} — ${esc(d.instruction || '')}`, 'tc-inject');
      _tcSpinSet(tab, 'Denkt nach');
    },
    // btw is rendered inline by tcBtw() from the synchronous /v1/chat/btw
    // response (works idle OR mid-stream) — no SSE btw_start/btw_done handling
    // here, which would double-render the row on this same tab.
    done: (d) => {
      // The chronological text rows already rendered the answer as it streamed.
      // Only fall back to the full done-text if NOTHING streamed (whole reply
      // arrived in `done`, e.g. a non-streaming path) — otherwise overwriting
      // would collapse the text↔tool interleaving into one block.
      if (d.text && !live.curTextRow) {
        const row = document.createElement('div'); row.className = 'tc-row tc-asst';
        row.innerHTML = renderMarkdown(d.text);
        _tcLiveInsert(tab, live, row);
        live.curTextRow = row;
      }
      live.text = d.text || live.text;
      if (d.model && (!tab.model || tab.model === 'auto')) tab._autoPicked = d.model;
      else if (d.model) tab.model = d.model;
      // Commit this turn's tokens into the running session totals (mirrors the
      // main chat's done handler). `done` carries the final per-turn figures;
      // fall back to the last live `usage` value if absent.
      const tin = (typeof d.tokens_in === 'number') ? d.tokens_in : (tab._liveIn || 0);
      const tout = (typeof d.tokens_out === 'number') ? d.tokens_out : (tab._liveOut || 0);
      const tcached = (typeof d.cache_read_tokens === 'number') ? d.cache_read_tokens : (tab._liveCached || 0);
      tab.tokensIn = (tab.tokensIn || 0) + tin;
      tab.tokensOut = (tab.tokensOut || 0) + tout;
      tab.cached = (tab.cached || 0) + tcached;
      tab._liveIn = 0; tab._liveOut = 0; tab._liveCached = 0;
      if (d.cost != null) tab.cost = d.cost;
      if (d.cost_list != null) tab.costList = d.cost_list;
      if (d.max_context) tab.maxContext = d.max_context;
      if (typeof d.last_tokens_in === 'number') tab.lastApiIn = d.last_tokens_in;
      tcRenderStatus(tab);
      _tcFinishTurn(tab, live);   // also aborts the reader → loop exits promptly
      // Title is auto-generated server-side after the turn (async, variable
      // latency) → refresh the history list a couple of times so the tab name +
      // list pick it up. renderTermchatHistory syncs open tabs' names.
      if (typeof renderTermchatHistory === 'function') {
        setTimeout(() => renderTermchatHistory(), 1500);
        setTimeout(() => renderTermchatHistory(), 5000);
      }
    },
    error: (d) => { _tcFinishTurn(tab, live); tcPrint(tab, esc(d.message || 'Fehler'), 'tc-err'); },
    // MoA delegate-plan review (v9.285.0), terminal flavor: plan text +
    // executor dropdown + Rückfrage input + Freigeben button in one row.
    // Buttons wired via addEventListener (no globals). Plan-EDITING is a
    // web-chat feature — the terminal offers approve / model change / revise.
    moa_plan_review: (d) => {
      if (tab._planReviewRow && tab._planReviewRow.parentNode) tab._planReviewRow.remove();
      const row = document.createElement('div');
      row.className = 'tc-row tc-plan-review';
      row.style.cssText = 'border:1px solid var(--border-100);border-radius:8px;padding:8px 10px;margin:4px 0';
      const cands = Array.isArray(d.executor_candidates) ? d.executor_candidates : [];
      const suitList = Array.isArray(d.executor_suitability) ? d.executor_suitability : [];
      const suitMap = {};
      suitList.forEach((s) => { if (s && s.id) suitMap[s.id] = s.suitable !== false; });
      const isSuitable = (m) => suitMap[m] !== false;
      const opts = cands.map((m) => {
        const ok = isSuitable(m);
        const label = (typeof modelShortName === 'function' ? modelShortName(m, false) : m)
          + (ok ? '' : ' — wenig geeignet');
        return `<option value="${esc(m)}" ${m === d.executor ? 'selected' : ''}`
          + `${ok ? '' : ' style="color:var(--text-400,#9ca3af)"'} data-suitable="${ok ? '1' : '0'}">${esc(label)}</option>`;
      }).join('');
      const verdictBad = (d.verdict || 'ready') !== 'ready';
      row.innerHTML = `
        <div style="font-weight:600;margin-bottom:4px">🧬 Ausführungsplan prüfen${d.round ? ` · Runde ${d.round + 1}` : ''} (Plan von ${esc(d.planner || '?')})${verdictBad ? ' — <span style="color:var(--danger,#dc2626)">Orchestrator: unzureichend</span>' : ''}</div>
        <pre style="white-space:pre-wrap;font-size:12px;max-height:220px;overflow-y:auto;margin:0 0 6px">${esc(d.plan || '')}</pre>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <select class="tcpr-exec form-select" style="font-size:12px;max-width:240px">${opts}</select>
          <button class="tcpr-approve btn-secondary" style="font-size:12px">Freigeben &amp; ausführen</button>
          <input class="tcpr-msg form-input" placeholder="Rückfrage / Änderungswunsch…" style="flex:1;min-width:160px;font-size:12px">
          <button class="tcpr-clarify btn-secondary" style="font-size:12px">Neu planen</button>
        </div>
        <div class="tcpr-warn" style="display:${isSuitable(d.executor) ? 'none' : 'block'};margin-top:5px;font-size:11.5px;color:var(--danger,#dc2626)">⚠️ Dieses Modell ist für die Art dieses Plans nur bedingt geeignet — das Ergebnis kann schwächer ausfallen.</div>`;
      const submit = async (action) => {
        const body = { session_id: tab.sessionId, action,
                       executor: row.querySelector('.tcpr-exec')?.value || '' };
        if (action === 'clarify') {
          body.message = (row.querySelector('.tcpr-msg')?.value || '').trim();
          if (!body.message) { tcPrint(tab, 'Bitte erst eine Rückfrage eingeben.', 'tc-err'); return; }
        }
        row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        try {
          const r = await fetch(`${BASE_URL}/v1/chat/plan-review`, {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          if (action === 'clarify') _tcSpinSet(tab, 'Orchestrator plant neu');
        } catch (e) {
          tcPrint(tab, esc('Plan-Review fehlgeschlagen: ' + (e.message || e)), 'tc-err');
          row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
        }
      };
      row.querySelector('.tcpr-approve').addEventListener('click', () => submit('approve'));
      row.querySelector('.tcpr-clarify').addEventListener('click', () => submit('clarify'));
      const _tcExec = row.querySelector('.tcpr-exec');
      const _tcWarn = row.querySelector('.tcpr-warn');
      if (_tcExec && _tcWarn) {
        _tcExec.addEventListener('change', () => {
          _tcWarn.style.display = isSuitable(_tcExec.value) ? 'none' : 'block';
        });
      }
      _tcLiveInsert(tab, live, row);
      tab._planReviewRow = row;
      _tcSpinSet(tab, 'Wartet auf Plan-Freigabe');
      _tcScroll(tab);
    },
    moa_plan_review_done: (d) => {
      if (tab._planReviewRow && tab._planReviewRow.parentNode) tab._planReviewRow.remove();
      tab._planReviewRow = null;
      _tcSpinSet(tab, 'Denkt nach');
    },
  };
}

function _tcToolArg(args) {
  if (!args || typeof args !== 'object') return '';
  // Pick the most informative single field for a compact one-liner.
  const k = args.path || args.file_path || args.query || args.command || args.url || args.name || args.symbol;
  if (typeof k === 'string') return k.length > 80 ? k.slice(0, 77) + '…' : k;
  return '';
}

function _tcFinishTurn(tab, live) {
  tab.streaming = false;
  tab._paused = false;
  _tcSpinStop(tab);
  if (live) {
    if (live.spinRow && live.spinRow.parentNode) live.spinRow.parentNode.removeChild(live.spinRow);
    if (live.thinkRow && live.thinkRow.parentNode && (!tab.showTools || !live.thinkRow.textContent)) live.thinkRow.style.display = 'none';
    // Drop a trailing EMPTY answer-text row (e.g. a tool-only final round left an
    // open, contentless curTextRow).
    if (live.curTextRow && !live.curSegText && live.curTextRow.parentNode) {
      live.curTextRow.parentNode.removeChild(live.curTextRow);
    }
    // record into the durable log model so a tab switch / re-render survives
    tab.log.push({ role: 'turn' });
  }
  tab._live = null;
  // Abort the SSE reader so the read loop exits promptly even if the server
  // keeps the connection open after the terminal event (prevents the spinner
  // from lingering on a finished turn). Safe if already torn down.
  if (tab._abort) { try { tab._abort.abort(); } catch (_) {} tab._abort = null; }
  tcRenderStatus(tab);
  const ta = tab.el && tab.el.querySelector('.tc-ta');
  if (ta) ta.focus();
  // Auto-send the next queued message as a normal turn (one at a time; each
  // turn's finish drains the next, so a whole queue chains through).
  if (Array.isArray(tab._queue) && tab._queue.length && !tab.streaming) {
    const next = tab._queue.shift();
    _tcQueueRender(tab);
    setTimeout(() => { try { if (!tab.streaming) tcSend(tab, next); } catch (e) {} }, 60);
  }
}

// ── Spinner (braille) ────────────────────────────────────────────────────────
function _tcSpinStart(tab, label) {
  tab._spinIdx = 0; tab._spinLabel = label || '';
  _tcSpinStop(tab);
  tab._spinTimer = setInterval(() => {
    const live = tab._live; if (!live || !live.spinRow) return;
    tab._spinIdx = (tab._spinIdx + 1) % _TC_SPIN.length;
    live.spinRow.innerHTML = `<span class="tc-spinner">${_TC_SPIN[tab._spinIdx]}</span> ${esc(tab._spinLabel)}…`;
  }, 80);
}
function _tcSpinSet(tab, label) { tab._spinLabel = label || ''; if (!tab._spinTimer && tab.streaming) _tcSpinStart(tab, label); }
function _tcSpinStop(tab) {
  if (tab._spinTimer) { clearInterval(tab._spinTimer); tab._spinTimer = null; }
  const live = tab._live;
  if (live && live.spinRow) live.spinRow.innerHTML = '';
}

// ── Subagenten-Spinner (überlebt das Turn-Ende) ──────────────────────────────
// Der normale Spinner hängt an `tab._live.spinRow`, die es nur WÄHREND eines
// Turns gibt. Ein abgekoppelter Subagent läuft aber weiter, nachdem der Turn
// beendet ist (run_background_task koppelt ab → Turn fertig, Arbeit nicht) —
// der Chat sah dann fertig aus, obwohl noch minutenlang gearbeitet wurde.
// Diese Zeile lebt daher am Log-Ende, unabhängig vom Turn, und wird vom
// 3s-Poller an-/abgeschaltet.
function _tcSubSpinRender(tab) {
  const n = tcRunningSubagents(tab);
  const asking = tcSubagentAsking(tab);
  const log = tab.el && tab.el.querySelector('.tc-log');
  if (!log) return;
  let row = tab._subSpinRow;
  if (!n || tab.streaming) {           // eigener Turn läuft → sein Spinner genügt
    if (row) { row.remove(); tab._subSpinRow = null; }
    if (tab._subSpinTimer) { clearInterval(tab._subSpinTimer); tab._subSpinTimer = null; }
    return;
  }
  if (!row) {
    row = document.createElement('div');
    row.className = 'tc-row tc-subspin';
    row.onclick = () => { try { _terminalActivate(_AGENT_HUB_ID); } catch (_) {} };
    log.appendChild(row);
    tab._subSpinRow = row;
    tab._subSpinIdx = 0;
  }
  const paint = () => {
    if (!tab._subSpinRow) return;
    tab._subSpinIdx = (tab._subSpinIdx + 1) % _TC_SPIN.length;
    const what = asking
      ? `❓ Subagent wartet auf Ihre Antwort`
      : `✦ ${n} Subagent${n === 1 ? '' : 'en'} arbeite${n === 1 ? 't' : 'n'} im Hintergrund`;
    tab._subSpinRow.innerHTML =
      `<span class="tc-spinner">${_TC_SPIN[tab._subSpinIdx]}</span> ${esc(what)} …`;
  };
  paint();
  if (!tab._subSpinTimer) tab._subSpinTimer = setInterval(paint, 80);
  log.scrollTop = log.scrollHeight;
}

// Laufende Subagenten DIESER Session. Der Terminal-Chat kennt sonst nur seinen
// EIGENEN Turn (tab.streaming) — der aber endet, sobald run_background_task die
// Aufgaben abgekoppelt hat, während die Subagenten noch minutenlang weiterlaufen.
// Ohne das hier wirkt der Chat nach dem Spawn tot (User-Report: "kein
// pulsierender grüner Punkt"), obwohl im Hintergrund gearbeitet wird.
// Zwei Quellen, in dieser Reihenfolge: (1) die Hub-Karten — live, exakt, kennen
// den Zustand sofort; (2) state.runningSubagents aus dem 3s-Sidebar-Poller —
// greift auch nach einem Reload, wo es noch keine Karten gibt.
function tcRunningSubagents(tab) {
  const sid = tab && tab.sessionId;
  if (!sid) return 0;
  const hub = (typeof _agentHubTab === 'function') ? _agentHubTab() : null;
  if (hub && hub._cards) {
    const cards = Object.values(hub._cards).filter(c => c.sessionId === sid);
    if (cards.length) return cards.filter(c => c.status === 'running').length;
  }
  const polled = (typeof state !== 'undefined' && state.runningSubagents) || {};
  return (polled[sid] || []).length;
}

// Wartet ein Subagent dieser Sitzung auf eine ANTWORT? Das ist dringlicher als
// "läuft" — der Subagent kommt ohne den Nutzer nicht weiter und läuft sonst in
// seinen Timeout. Quelle ist der 3s-Poller (state.runningSubagents).
function tcSubagentAsking(tab) {
  const sid = tab && tab.sessionId;
  if (!sid) return false;
  const polled = (typeof state !== 'undefined' && state.runningSubagents) || {};
  return (polled[sid] || []).some(t => t.pending_question);
}

// ── Status footer ────────────────────────────────────────────────────────────
function tcRenderStatus(tab) {
  const el = document.getElementById(tab.id + '-status');
  if (!el) return;
  const model = tab._autoPicked ? `auto→${tab._autoPicked}` : (tab.model || 'Standard');
  const think = _TC_THINK[tab.thinking || 'none'] || (tab.thinking || '');
  const tin = (tab.tokensIn || 0) + (tab.streaming ? (tab._liveIn || 0) : 0);
  const tout = (tab.tokensOut || 0) + (tab.streaming ? (tab._liveOut || 0) : 0);
  // Verrechnet (real) + API-Listenpreis, wenn ein Flatrate-Modell sie trennt.
  // `costHtml` ist FERTIGES MARKUP (der Listenpreis-Hinweis ist ein <span>) und
  // darf daher NICHT durch esc() — sonst stehen die Tags als Text in der Zeile
  // (User-Report). Alle Werte sind Number()/toFixed()-Zahlen, nichts Fremdes.
  const _cl = (tab.costList != null) ? Number(tab.costList) : null;
  const _cDiff = tab.cost != null && _cl != null && _cl > Number(tab.cost) * 1.01 + 0.0001;
  const costHtml = (tab.cost != null)
    ? ('$' + Number(tab.cost).toFixed(4) + (_cDiff ? ` <span title="API-Listenpreis ohne Flatrate — Ersparnis $${(_cl - Number(tab.cost)).toFixed(4)}" style="color:var(--text-400)">(API $${_cl.toFixed(4)})</span>` : ''))
    : '—';
  // Prompt-cache hits (session total + live turn) — mirrors the main chat's
  // status-bar item: hit % = cached / (full-price in + cached), green once >0.
  const cached = (tab.cached || 0) + (tab.streaming ? (tab._liveCached || 0) : 0);
  const cachePct = (tin + cached) ? Math.round(100 * cached / (tin + cached)) : 0;
  const cachedBadge = ` · <span title="Prompt-Cache-Treffer dieser Sitzung: ${cached.toLocaleString('de-DE')} Tokens = ${cachePct}% des Prompts (zum ~0,1×-Tarif)"${cached > 0 ? ' style="color:#10b981"' : ''}>⚡ ${cached.toLocaleString('de-DE')} (${cachePct}%)</span>`;
  let ctx = '';
  if (tab.maxContext && tab.lastApiIn) ctx = ' · ctx ' + Math.min(100, Math.round(tab.lastApiIn / tab.maxContext * 100)) + '%';
  const toolsBadge = tab.showTools ? '' : ' · tools:aus';
  const pausedBadge = (tab.streaming && tab._paused) ? '<span class="tc-paused">⏸ pausiert</span> ' : '';
  // Der Puls steht für "an dieser Sitzung wird gerade gearbeitet" — das ist der
  // EIGENE Turn ODER ein abgekoppelter Subagent (der den Turn überlebt).
  const subs = tcRunningSubagents(tab);
  const asking = tcSubagentAsking(tab);
  const busy = tab.streaming || subs > 0;
  const dot = (busy && !(tab.streaming && tab._paused)) ? '<span class="tc-live">●</span> ' : '';
  const subBadge = asking
    ? `<span class="tc-ask-badge" title="Ein Subagent wartet auf Ihre Antwort — klicken, um sie zu beantworten"
        onclick="_terminalActivate('${_AGENT_HUB_ID}')">❓ Rückfrage</span> `
    : (subs
      ? `<span class="tc-sub-badge" title="${subs} Subagent${subs === 1 ? '' : 'en'} dieser Sitzung ${subs === 1 ? 'läuft' : 'laufen'} noch — klicken für die Live-Karten"
          onclick="_terminalActivate('${_AGENT_HUB_ID}')">✦ ${subs} Subagent${subs === 1 ? '' : 'en'}</span> `
      : '');
  const qn = Array.isArray(tab._queue) ? tab._queue.length : 0;
  const queueBadge = qn ? ` · <span class="tc-queue-badge">⧉ ${qn} in Warteschlange</span>` : '';
  const goalBadge = tab.goalStatus === 'active'
    ? ` · <span class="tc-goal-badge" title="${esc(tab.goalText || '')}">🎯 Ziel aktiv</span>`
    : (tab.goalStatus === 'fulfilled' ? ' · 🎯 ✓ Ziel erreicht' : '');
  const cancelHint = tab.streaming
    ? ' · <span class="tc-cancel-hint">Esc /cancel · Strg-Z pausieren · /btw · /inject</span>'
    : '';
  // Export-as-markdown button (direct browser download) — only meaningful once
  // the chat has rows.
  const exportBtn = `<button class="tc-st-btn" title="Chatverlauf als Markdown herunterladen"
    onclick="tcExportMarkdown('${esc(tab.id)}')">⬇ .md</button>`;
  el.innerHTML = `<span class="tc-st-info">${dot}${pausedBadge}${subBadge}<span class="tc-st-model">${esc(model)}</span> · think:${esc(think)} · ${tin}/${tout} tok${cachedBadge} · ${costHtml}${ctx}${toolsBadge}${queueBadge}${goalBadge}${cancelHint}</span>${exportBtn}`;
}

// Build a Markdown transcript of a terminal-chat from its in-DOM rows and
// trigger a direct browser download (no server round-trip, no artifact write).
function tcExportMarkdown(tabId) {
  const tab = _tcTab(tabId);
  if (!tab) return;
  const log = _tcLog(tab);
  if (!log) return;
  const lines = [];
  const model = tab._autoPicked ? `auto→${tab._autoPicked}` : (tab.model || 'Standard');
  lines.push(`# Terminal-Chat`, '', `*Modell: ${model}*`, '');
  // Walk the rendered rows in order; map each row class to a Markdown block.
  // We read textContent (rendered text) so markdown/tool/shell rows export as
  // plain readable text — the composer + spinner rows are skipped.
  log.querySelectorAll('.tc-row').forEach(row => {
    if (row.closest('.tc-input-wrap')) return;          // skip the composer
    if (row.classList.contains('tc-spin')) return;       // skip live spinner
    const txt = (row.textContent || '').replace(/\s+$/,'');
    if (!txt) return;
    if (row.classList.contains('tc-user')) {
      lines.push('## › ' + txt.replace(/^›\s*/, ''), '');
    } else if (row.classList.contains('tc-asst')) {
      lines.push(txt, '');
    } else if (row.classList.contains('tc-shell') || row.classList.contains('tc-shout')) {
      lines.push('```', txt, '```', '');
    } else if (row.classList.contains('tc-think')) {
      lines.push('> ' + txt.replace(/\n/g, '\n> '), '');
    } else {
      lines.push('_' + txt + '_', '');
    }
  });
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const fname = `terminal-chat_${(tab.sessionId || tab.id).slice(0, 12)}_${ts}.md`;
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
  if (typeof _saveBlobAs === 'function') _saveBlobAs(blob, fname);
}

// ── Session lifecycle ────────────────────────────────────────────────────────
async function _tcEnsureSession(tab) {
  if (tab.sessionId) return true;
  try {
    const r = await API.createSession(_term.agent, tab.model || 'auto', _term.project, 'code_chat');
    if (r && r.session_id) {
      tab.sessionId = r.session_id;
      tab.maxContext = r.max_context || tab.maxContext;
      // upgrade the tab id so persistence keys on the session
      _tcRekey(tab, 'chat-' + r.session_id);
      if (typeof renderTermchatHistory === 'function') renderTermchatHistory();
      return true;
    }
  } catch (e) { /* fall through */ }
  return false;
}

// Re-key a tab (e.g. from a temp id to chat-<sid>) keeping DOM ids in sync.
function _tcRekey(tab, newId) {
  if (tab.id === newId) return;
  const oldId = tab.id;
  // pane.active pointer
  for (const p of (_term.panes || [])) { if (p.active === oldId) p.active = newId; }
  tab.id = newId;
  // re-id the inner DOM nodes (log/status/autocomplete reference tab.id)
  const log = tab.el.querySelector('.tc-log'); if (log) log.id = newId + '-log';
  const st = tab.el.querySelector('.tc-status'); if (st) st.id = newId + '-status';
  const ac = tab.el.querySelector('.tc-ac'); if (ac) ac.id = newId + '-ac';
  if (typeof _terminalRenderTabs === 'function') _terminalRenderTabs();
  if (typeof _terminalPersist === 'function') _terminalPersist();
}

// ── Slash commands ───────────────────────────────────────────────────────────
async function tcSlash(tab, raw) {
  const [cmd, ...rest] = raw.slice(1).trim().split(/\s+/);
  const arg = rest.join(' ').trim();
  const c = (cmd || '').toLowerCase();
  switch (c) {
    case 'help': return _tcHelp(tab);
    case 'model': return _tcCmdModel(tab, arg);
    case 'think': case 'thinking': return _tcCmdThink(tab, arg);
    case 'tools': return _tcCmdTools(tab, arg);
    case 'clear': return _tcCmdClear(tab);
    case 'lcm': case 'compact': return _tcCmdLcm(tab);
    case 'caveman': return _tcCmdCaveman(tab, arg);
    case 'goal': return _tcCmdGoal(tab, arg);
    case 'init': return _tcCmdInit(tab);
    case 'sync': return _tcCmdSync(tab);
    case 'suggest': return _tcSuggest(tab, true);
    case 'cancel': case 'stop': return tcCancel(tab);
    case 'pause': return tcPause(tab);
    case 'resume': return tcResume(tab);
    case 'btw': return tcBtw(tab, arg);
    case 'inject': case 'clarify': return tcInject(tab, arg);
    case 'queue': return tcQueueCmd(tab, arg);
    case 'workflow': return _tcCmdWorkflow(tab, arg);
    default: tcPrint(tab, `Unbekannter Befehl: <b>/${esc(c)}</b> — <b>/help</b> für die Liste.`, 'tc-err');
  }
}

function _tcHelp(tab) {
  tcPrint(tab, [
    '<b>Befehle:</b>',
    '/model &lt;name|auto&gt;   — Modell wechseln',
    '/think off|low|medium|high — Denktiefe',
    '/tools on|off          — Werkzeugaufrufe anzeigen',
    '/caveman 0-3           — Caveman-Stil',
    '/goal &lt;text&gt;|off|status — Ziel setzen (Goal-Modus): nach jeder Antwort prüft ein Judge, ob das Ziel erreicht ist; wenn nicht, arbeitet der Agent automatisch weiter',
    '/clear                 — neue Sitzung (leerer Kontext)',
    '/lcm                   — Kontext komprimieren (LCM)',
    '/sync                  — Projekt-Sync anstoßen',
    '/init                  — Projektanweisungen generieren',
    '/suggest               — nächste Eingabe vorschlagen (auch Tab)',
    '/cancel                — laufende Antwort abbrechen (auch Esc)',
    '<b>Während eine Antwort läuft:</b>',
    '/pause /resume         — pausieren / fortsetzen (Strg-Z / Strg-Q)',
    '/btw &lt;frage&gt;          — Nebenfrage (separate Blase; „Was machst du gerade?“)',
    '/inject &lt;text&gt;        — Klarstellung einfügen (Alias /clarify); nächste Runde',
    '/queue [list|rm N|mv N M|edit N …|clear] — Warteschlange verwalten',
    '/workflow [&lt;session_id&gt;]  — Workflow aus diesem (oder dem angegebenen) Chat erzeugen; Entwurf wird gespeichert und im Workflows-Panel geprüft',
    'einfacher Text während des Streamens → wird in die Warteschlange gestellt',
    '! &lt;befehl&gt;            — Shell-Befehl im Arbeitsverzeichnis ausführen',
  ].map(s => `<div class="tc-help-line">${s}</div>`).join(''), 'tc-sys');
}

function _tcCmdModel(tab, arg) {
  if (!arg) { tcPrint(tab, `Aktuelles Modell: <b>${esc(tab.model || 'Standard')}</b>`, 'tc-sys'); return; }
  const want = arg.trim();
  if (want.toLowerCase() === 'auto') { tab.model = 'auto'; tab._autoPicked = ''; }
  else {
    const models = (state.models || []).map(m => (typeof m === 'string' ? m : (m.id || m.name))).filter(Boolean);
    const hit = models.find(m => m === want) || models.find(m => m.toLowerCase().includes(want.toLowerCase()));
    if (!hit) { tcPrint(tab, `Modell nicht gefunden: <b>${esc(want)}</b>`, 'tc-err'); return; }
    tab.model = hit; tab._autoPicked = '';
  }
  tcPrint(tab, `Modell → <b>${esc(tab.model)}</b>`, 'tc-sys');
  tcRenderStatus(tab);
  if (typeof _terminalPersist === 'function') _terminalPersist();
}

// /workflow [<session_id>] — KI-Workflow-Generierung aus dem Chat, komplett im
// Terminal: startet POST /v1/workflows/generate, pollt, speichert den fertigen
// Entwurf unter dem vorgeschlagenen Namen (bei Warnungen wird NICHT gespeichert,
// sondern auf den Editor verwiesen — review-before-save bleibt erhalten).
async function _tcCmdWorkflow(tab, arg) {
  const sid = (arg || '').trim() || tab.sessionId || '';
  if (!sid) { tcPrint(tab, 'Noch keine Sitzung — erst eine Nachricht senden oder /workflow &lt;session_id&gt;.', 'tc-err'); return; }
  tcPrint(tab, `Erzeuge Workflow aus Chat <b>${esc(sid.substring(0, 8))}</b> …`, 'tc-sys');
  let genId;
  try {
    const res = await API.post('/v1/workflows/generate', {
      agent_id: 'main', source: { type: 'chat', session_id: sid },
    });
    if (res.error) throw new Error(res.error);
    genId = res.gen_id;
  } catch (e) {
    tcPrint(tab, `Start fehlgeschlagen: ${esc(e.message || e)}`, 'tc-err');
    return;
  }
  let lastStep = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, 1500));
    let d;
    try { d = await API.get(`/v1/workflows/generate/${genId}`); }
    catch (e) { tcPrint(tab, `Abfrage fehlgeschlagen: ${esc(e.message || e)}`, 'tc-err'); return; }
    for (const s of (d.steps || [])) {
      if (s.n > lastStep) { lastStep = s.n; tcPrint(tab, esc(s.text), s.kind === 'error' ? 'tc-err' : 'tc-sys'); }
    }
    if (d.status === 'generating') continue;
    if (d.status === 'error') { tcPrint(tab, `Fehler: ${esc(d.error || 'unbekannt')}`, 'tc-err'); return; }
    if (d.status === 'cancelled') { tcPrint(tab, 'Abgebrochen.', 'tc-sys'); return; }
    const warns = d.warnings || [];
    if (warns.length) {
      tcPrint(tab, `Entwurf hat ${warns.length} Validierungs-Warnung(en) — bitte im Workflows-Panel unter „Neu aus Beschreibung"/Editor prüfen: ${esc(warns.join('; '))}`, 'tc-err');
      return;
    }
    try {
      const saved = await API.post('/v1/agents/main/workflows', {
        name: d.suggested_name || 'workflow',
        source: d.flow_source || '',
        plan_md: d.plan_md || '',
      });
      if (saved.error) throw new Error(saved.error);
      tcPrint(tab, `Workflow gespeichert: <b>${esc(saved.name)}</b> — im Workflows-Panel prüfen/ausführen.${d.notes ? '<br>' + esc(d.notes) : ''}`, 'tc-sys');
    } catch (e) {
      tcPrint(tab, `Speichern fehlgeschlagen: ${esc(e.message || e)}`, 'tc-err');
    }
    return;
  }
}

function _tcCmdThink(tab, arg) {
  const map = { off: 'none', none: 'none', low: 'low', med: 'medium', medium: 'medium', high: 'high' };
  const lvl = map[(arg || '').toLowerCase()];
  if (!lvl) { tcPrint(tab, 'Verwendung: /think off|low|medium|high', 'tc-err'); return; }
  tab.thinking = lvl;
  tcPrint(tab, `Denktiefe → <b>${esc(_TC_THINK[lvl])}</b>`, 'tc-sys');
  tcRenderStatus(tab);
  if (typeof _terminalPersist === 'function') _terminalPersist();
}

function _tcCmdTools(tab, arg) {
  const v = (arg || '').toLowerCase();
  if (v === 'on') tab.showTools = true;
  else if (v === 'off') tab.showTools = false;
  else { tcPrint(tab, 'Verwendung: /tools on|off', 'tc-err'); return; }
  tcPrint(tab, `Werkzeuganzeige → <b>${tab.showTools ? 'an' : 'aus'}</b>`, 'tc-sys');
  tcRenderStatus(tab);
  if (typeof _terminalPersist === 'function') _terminalPersist();
}

async function _tcCmdClear(tab) {
  // Start a fresh code_chat session (drops context) — keep the same tab.
  tcCancel(tab);
  tab.sessionId = '';
  tab.tokensIn = 0; tab.tokensOut = 0; tab.cached = 0; tab._liveCached = 0; tab.cost = null; tab.costList = null; tab.lastApiIn = 0;
  tab.log = []; tab._autoPicked = '';
  _tcClearRows(tab);
  tcPrint(tab, 'Neue Sitzung — Kontext geleert.', 'tc-sys');
  tcRenderStatus(tab);
  if (typeof renderTermchatHistory === 'function') renderTermchatHistory();
}

async function _tcCmdLcm(tab) {
  if (!tab.sessionId) { tcPrint(tab, 'Noch keine Sitzung.', 'tc-err'); return; }
  tcPrint(tab, 'Komprimiere Kontext (LCM)…', 'tc-sys');
  try {
    const r = await API.post('/v1/context/compact', { session_id: tab.sessionId });
    if (r && r.error) { tcPrint(tab, esc(r.error), 'tc-err'); return; }
    tcPrint(tab, 'Kontext komprimiert.', 'tc-sys');
  } catch (e) { tcPrint(tab, 'LCM fehlgeschlagen.', 'tc-err'); }
}

async function _tcCmdCaveman(tab, arg) {
  const n = parseInt(arg, 10);
  if (isNaN(n) || n < 0 || n > 3) { tcPrint(tab, 'Verwendung: /caveman 0-3', 'tc-err'); return; }
  tab.caveman = n;
  if (tab.sessionId) {
    // Canonical manage endpoint (session_id in body, level key = `mode`) —
    // the old `/v1/sessions/<sid>/manage` + `value` shape hit a nonexistent
    // route, so /caveman never actually persisted server-side.
    try { await API.post('/v1/sessions/manage', { action: 'caveman_mode', session_id: tab.sessionId, mode: n }); } catch (_) {}
  }
  tcPrint(tab, `Caveman-Stil → <b>${n}</b>`, 'tc-sys');
}

// /goal — Goal-Modus: persist a per-session goal; the server judges every
// turn against it and auto-continues until fulfilled / capped / cleared.
async function _tcCmdGoal(tab, arg) {
  const a = (arg || '').trim();
  const low = a.toLowerCase();
  if (!a || low === 'status') {
    if (tab.goalText) {
      const st = tab.goalStatus === 'fulfilled' ? 'erreicht'
        : tab.goalStatus === 'capped' ? 'nicht erreicht (Limit/unerreichbar)'
        : 'aktiv';
      tcPrint(tab, `🎯 Ziel (<b>${esc(st)}</b>): ${esc(tab.goalText)}`, 'tc-sys');
    } else {
      tcPrint(tab, 'Kein Ziel gesetzt. Verwendung: /goal &lt;text&gt; | off | status', 'tc-sys');
    }
    return;
  }
  if (low === 'off' || low === 'clear') {
    tab.goalText = ''; tab.goalStatus = '';
    if (tab.sessionId) {
      try { await API.post('/v1/sessions/manage', { action: 'goal', session_id: tab.sessionId, goal: '' }); } catch (_) {}
    }
    tcPrint(tab, '🎯 Ziel gelöscht.', 'tc-sys');
    tcRenderStatus(tab);
    return;
  }
  if (!tab.sessionId) {
    const ok = await _tcEnsureSession(tab);
    if (!ok) { tcPrint(tab, 'Sitzung konnte nicht erstellt werden.', 'tc-err'); return; }
  }
  try {
    await API.post('/v1/sessions/manage', { action: 'goal', session_id: tab.sessionId, goal: a });
    tab.goalText = a; tab.goalStatus = 'active';
    tcPrint(tab, `🎯 Ziel gesetzt — der Agent arbeitet nach jeder Antwort weiter, bis es erreicht ist: <b>${esc(a)}</b>`, 'tc-sys');
  } catch (e) {
    tcPrint(tab, 'Ziel setzen fehlgeschlagen: ' + esc(e.message || String(e)), 'tc-err');
  }
  tcRenderStatus(tab);
}

// /init → run the Code-Mode BRAIN.md init: an agentic turn that explores the
// working_dir and writes a BRAIN.md summary at the root. Async; we poll
// init-status and report progress into the log.
async function _tcCmdInit(tab) {
  tcPrint(tab, 'Initialisiere BRAIN.md (Projekt wird erkundet)…', 'tc-sys');
  try {
    const r = await API.post(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/init`, {});
    if (r && r.error) { tcPrint(tab, esc(r.error), 'tc-err'); return; }
  } catch (e) { tcPrint(tab, 'Start fehlgeschlagen.', 'tc-err'); return; }
  // Poll init-status until done/error (best-effort, capped).
  let n = 0;
  const poll = async () => {
    n++;
    let s = null;
    try { s = await API.get(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/init-status`); } catch (_) {}
    const st = s && s.state;
    if (st === 'done') { tcPrint(tab, 'BRAIN.md erstellt.', 'tc-sys'); if (typeof refreshTerminalTree === 'function') refreshTerminalTree(); return; }
    if (st === 'error') { tcPrint(tab, 'Init fehlgeschlagen: ' + esc((s && s.error) || ''), 'tc-err'); return; }
    if (st === 'cancelled') { tcPrint(tab, 'Init abgebrochen.', 'tc-sys'); return; }
    if (n > 120) { tcPrint(tab, 'Init läuft weiter… (Status im Projekt prüfen)', 'tc-sys'); return; }
    setTimeout(poll, 2000);
  };
  setTimeout(poll, 2000);
}

async function _tcCmdSync(tab) {
  tcPrint(tab, 'Projekt-Sync wird angestoßen…', 'tc-sys');
  try {
    const r = await API.post(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/sync-now`, {});
    if (r && r.error) { tcPrint(tab, esc(r.error), 'tc-err'); return; }
    tcPrint(tab, 'Sync läuft.', 'tc-sys');
  } catch (e) { tcPrint(tab, 'Sync fehlgeschlagen.', 'tc-err'); }
}

async function _tcSuggest(tab, fill) {
  if (!tab.sessionId) { if (!fill) return; tcPrint(tab, 'Noch keine Sitzung.', 'tc-err'); return; }
  try {
    const d = await API.get(`/v1/sessions/${encodeURIComponent(tab.sessionId)}/next-prompt`);
    const text = (d && d.suggestion || '').trim();
    if (!text) { if (fill) tcPrint(tab, 'Kein Vorschlag verfügbar.', 'tc-sys'); return; }
    const ta = tab.el.querySelector('.tc-ta');
    if (ta) { ta.value = text; _tcAutosize(ta); ta.focus(); }
  } catch (e) { /* best-effort */ }
}

// ── Turn control: pause / resume / btw / inject / queue ──────────────────────
function tcPause(tab) {
  if (!tab.streaming || !tab.sessionId) { tcPrint(tab, 'Nichts läuft gerade.', 'tc-sys'); return; }
  if (tab._paused) { tcPrint(tab, 'Läuft bereits pausiert.', 'tc-sys'); return; }
  try { API.pauseChat(tab.sessionId); } catch (e) {}
  // The `paused` SSE event confirms + prints; optimistic flag for the shortcut.
  tab._paused = true; tcRenderStatus(tab);
}

function tcResume(tab) {
  if (!tab.streaming || !tab.sessionId) return;
  try { API.resumeChat(tab.sessionId); } catch (e) {}
  tab._paused = false; tcRenderStatus(tab);
}

async function tcBtw(tab, arg) {
  const q = (arg || '').trim();
  if (!q) { tcPrint(tab, 'Verwendung: /btw &lt;Frage&gt; — z. B. „Was machst du gerade?“', 'tc-err'); return; }
  if (!tab.sessionId) { tcPrint(tab, 'Noch keine Sitzung.', 'tc-err'); return; }
  // Echo the question + a pending row; the endpoint runs synchronously and
  // returns the answer in the response (works idle OR mid-stream — no dependency
  // on an attached SSE stream). Render into a dedicated row keyed by btw_id.
  const rowId = tab.id + '-btw-inline-' + Math.random().toString(36).slice(2, 8);
  const row = document.createElement('div');
  row.className = 'tc-row tc-btw';
  row.id = rowId;
  row.innerHTML = '<span class="tc-btw-tag">btw</span> <span class="tc-btw-q"></span>' +
                  '<div class="tc-btw-a">⠿ antwortet…</div>';
  row.querySelector('.tc-btw-q').textContent = q;
  _tcAddRow(tab, row);
  _tcScroll(tab);
  try {
    const r = await API.btwChat(tab.sessionId, q);
    const a = row.querySelector('.tc-btw-a');
    if (a) {
      if (r && r.error) { a.innerHTML = '<span class="tc-err">Fehler: ' + esc(r.error) + '</span>'; }
      else { try { a.innerHTML = renderMarkdown((r && r.answer) || ''); } catch (e) { a.textContent = (r && r.answer) || ''; } }
    }
    _tcScroll(tab);
  } catch (e) {
    const a = row.querySelector('.tc-btw-a');
    if (a) a.innerHTML = '<span class="tc-err">btw fehlgeschlagen.</span>';
  }
}

function tcInject(tab, arg) {
  const t = (arg || '').trim();
  if (!t) { tcPrint(tab, 'Verwendung: /inject &lt;Text&gt; (Alias /clarify)', 'tc-err'); return; }
  if (!tab.streaming || !tab.sessionId) {
    tcPrint(tab, 'Einfügen geht nur, während eine Antwort läuft.', 'tc-err'); return;
  }
  try { API.injectChat(tab.sessionId, t); }
  catch (e) { tcPrint(tab, 'Einfügen fehlgeschlagen.', 'tc-err'); }
}

// Queue: add / list / rm / mv / edit / clear. tab._queue = [string].
function tcQueueAdd(tab, text) {
  text = (text || '').trim();
  if (!text) return;
  if (!Array.isArray(tab._queue)) tab._queue = [];
  tab._queue.push(text);
  _tcQueueRender(tab);
  tcPrint(tab, 'In Warteschlange (' + tab._queue.length + '): ' + esc(text), 'tc-queue');
}

function tcQueueCmd(tab, arg) {
  if (!Array.isArray(tab._queue)) tab._queue = [];
  const parts = (arg || '').trim().split(/\s+/);
  const sub = (parts[0] || 'list').toLowerCase();
  const q = tab._queue;
  const listOut = () => {
    if (!q.length) { tcPrint(tab, 'Warteschlange ist leer.', 'tc-sys'); return; }
    tcPrint(tab, '<b>Warteschlange:</b><br>' + q.map((t, i) =>
      (i + 1) + '. ' + esc(t)).join('<br>'), 'tc-queue');
  };
  if (sub === 'list' || sub === '') return listOut();
  if (sub === 'clear') { tab._queue = []; _tcQueueRender(tab); tcPrint(tab, 'Warteschlange geleert.', 'tc-sys'); return; }
  if (sub === 'rm' || sub === 'remove') {
    const n = parseInt(parts[1], 10);
    if (!n || n < 1 || n > q.length) { tcPrint(tab, 'Verwendung: /queue rm &lt;Nr&gt;', 'tc-err'); return; }
    const [x] = q.splice(n - 1, 1); _tcQueueRender(tab);
    tcPrint(tab, 'Entfernt: ' + esc(x), 'tc-sys'); return;
  }
  if (sub === 'mv' || sub === 'move') {
    const a = parseInt(parts[1], 10), b = parseInt(parts[2], 10);
    if (!a || !b || a < 1 || b < 1 || a > q.length || b > q.length) {
      tcPrint(tab, 'Verwendung: /queue mv &lt;von&gt; &lt;nach&gt;', 'tc-err'); return;
    }
    const [x] = q.splice(a - 1, 1); q.splice(b - 1, 0, x); _tcQueueRender(tab);
    return listOut();
  }
  if (sub === 'edit') {
    const n = parseInt(parts[1], 10);
    const newText = parts.slice(2).join(' ').trim();
    if (!n || n < 1 || n > q.length || !newText) {
      tcPrint(tab, 'Verwendung: /queue edit &lt;Nr&gt; &lt;neuer Text&gt;', 'tc-err'); return;
    }
    q[n - 1] = newText; _tcQueueRender(tab);
    return listOut();
  }
  tcPrint(tab, 'Verwendung: /queue [list|rm N|mv N M|edit N …|clear]', 'tc-err');
}

// Persistent status-line reflection of the queue length (best-effort; the queue
// itself is in-memory per tab — terminal chat is ephemeral session state).
function _tcQueueRender(tab) {
  tcRenderStatus(tab);
}

function tcCancel(tab) {
  if (!tab.streaming) return;
  if (tab._abort) { try { tab._abort.abort(); } catch (_) {} }
  if (tab.sessionId) { try { API.cancelChat(tab.sessionId); } catch (_) {} }
  _tcFinishTurn(tab, tab._live);
  tcPrint(tab, 'Abgebrochen.', 'tc-sys');
}

// ── Load a past terminal-chat's transcript into the log ──────────────────────
async function tcLoadTranscript(tab) {
  if (!tab.sessionId) return;
  const log = _tcLog(tab); if (!log) return;
  _tcClearRows(tab);
  tcPrint(tab, 'Lädt…', 'tc-sys');
  try {
    const d = await API.getSessionMessages(tab.sessionId);
    _tcClearRows(tab);
    if (d.model) tab.model = d.model;
    if (d.thinking_level) tab.thinking = d.thinking_level;
    if (d.max_context) tab.maxContext = d.max_context;
    if (typeof d.caveman_mode === 'number') tab.caveman = d.caveman_mode;
    tab.goalText = d.goal_text || '';
    tab.goalStatus = d.goal_status || '';
    if (d.title) { tab.name = d.title; if (typeof _terminalRenderTabs === 'function') _terminalRenderTabs(); }
    // Restore the running token/cost totals + last-used model + last prompt size
    // from the per-message metadata so the status line reflects the conversation
    // after a reload (was showing 0/0 tok and "Standard").
    let tin = 0, tout = 0, cached = 0, cost = 0, lastModel = '', lastIn = 0, haveCost = false;
    for (const m of (d.messages || [])) {
      const meta = m.metadata || {};
      if (m.role === 'assistant' || m.role === 'assistant_segment') {
        tin += meta.tokens_in || 0;
        tout += meta.tokens_out || 0;
        cached += meta.cache_read_tokens || 0;
        // meta.cost is the CUMULATIVE session cost at that turn (mirrors the
        // done event) — the most recent value wins; summing would inflate.
        if (typeof meta.cost === 'number') { cost = meta.cost; haveCost = true; }
        if (typeof meta.cost_list === 'number') tab.costList = meta.cost_list;
        if (meta.model) lastModel = meta.model;
        if (meta.tokens_in) lastIn = meta.tokens_in;
      }
    }
    tab.tokensIn = tin; tab.tokensOut = tout; tab.cached = cached;
    if (haveCost) tab.cost = cost;
    if (lastIn) tab.lastApiIn = lastIn;
    if (lastModel) tab.model = lastModel;   // most recent turn's model wins for display
    // Thinking rows are separate DB messages persisted BEFORE their assistant
    // turn — rendering them flat groups all thinking above all tool cards.
    // Buffer them and let _tcRenderHistAssistant weave them in per tool_round
    // (mirrors the main chat's reload interleave in sessions.js).
    let pendingThink = [];
    for (const m of (d.messages || [])) {
      if (m.role === 'thinking') { pendingThink.push(m); continue; }
      if (m.role === 'assistant') { _tcRenderHistAssistant(tab, m, pendingThink); pendingThink = []; continue; }
      if (pendingThink.length) { for (const tm of pendingThink) _tcRenderHistMsg(tab, tm); pendingThink = []; }
      _tcRenderHistMsg(tab, m);
    }
    for (const tm of pendingThink) _tcRenderHistMsg(tab, tm);   // trailing (turn cut off)
    tcRenderStatus(tab);
    _tcScrollToEnd(tab);   // open a past chat at its most recent message
    // If a turn is still streaming server-side, attach + follow it.
    if (d.streaming) _tcAttachLive(tab);
  } catch (e) {
    _tcClearRows(tab);
    tcPrint(tab, 'Verlauf konnte nicht geladen werden.', 'tc-err');
  }
}

function _tcRenderHistMsg(tab, m) {
  const role = m.role;
  if (role === 'user') { tcPrint(tab, `<span class="tc-uprompt">›</span> ${esc(_tcMsgText(m))}`, 'tc-user'); return; }
  if (role === 'assistant') { _tcRenderHistAssistant(tab, m); return; }
  if (role === 'thinking') { if (tab.showTools) tcPrint(tab, '⠿ ' + esc(_tcMsgText(m)), 'tc-think'); return; }
  if (role === 'tool_call') {
    if (!tab.showTools) return;
    const arg = _tcToolArg(m.args);
    tcPrint(tab, `<span class="tc-tool-dot">●</span> <span class="tc-tool-name">${esc(m.name || '')}</span>${arg ? ` <span class="tc-tool-arg">${esc(arg)}</span>` : ''}`, 'tc-tool');
    return;
  }
  // tool_result / system / assistant_segment → skip or render plainly
  if (role === 'assistant_segment') { tcPrint(tab, renderMarkdown(_tcMsgText(m)), 'tc-asst'); }
}

// Render one persisted assistant turn CHRONOLOGICALLY — interleaving its tool
// cards (metadata.tools, ordered by tool_round) with its per-round answer text
// (metadata.text_rounds), so a reloaded transcript matches the live view
// (round1 tools → round1 text → round2 tools → …) instead of dumping all tools
// then the whole answer. Tools are stored ONLY in the assistant message's
// metadata (not as separate rows), which is why the old flat render clustered
// them. Falls back to the plain joined text when there are no tools / rounds.
function _tcRenderHistAssistant(tab, m, thinkMsgs) {
  const meta = m.metadata || {};
  const tools = tab.showTools ? (meta.tools || []) : [];
  const rounds = meta.text_rounds || [];
  // Buffered thinking rows for THIS turn (each carries metadata.tool_round) —
  // woven in at the START of their round, matching the live stream order
  // (thinking → that round's tools → that round's text).
  const thinks = tab.showTools ? (thinkMsgs || []) : [];
  const printThink = (tm) => tcPrint(tab, '⠿ ' + esc(_tcMsgText(tm)), 'tc-think');
  // No round split available → render thinking, then the tools (if any), then
  // the whole answer, preserving at least think/tool-before-text ordering.
  if (!rounds.length) {
    for (const tm of thinks) printThink(tm);
    for (const t of tools) _tcPrintHistTool(tab, t);
    const txt = _tcMsgText(m);
    if (txt) tcPrint(tab, renderMarkdown(txt), 'tc-asst');
    return;
  }
  // Group tools + thinking by round; walk rounds in order: that round's
  // thinking, its tools, then its text.
  const byRound = {};
  for (const t of tools) { const r = t.tool_round || 0; (byRound[r] = byRound[r] || []).push(t); }
  const thinkByRound = {};
  for (const tm of thinks) { const r = tm.metadata?.tool_round ?? 0; (thinkByRound[r] = thinkByRound[r] || []).push(tm); }
  const seen = new Set(), seenThink = new Set();
  const roundNos = rounds.map(r => r.round).filter(n => typeof n === 'number');
  const maxRound = Math.max(0, ...roundNos, ...Object.keys(byRound).map(Number), ...Object.keys(thinkByRound).map(Number));
  const textByRound = {};
  for (const r of rounds) if (typeof r.round === 'number') textByRound[r.round] = r.text || '';
  for (let r = 0; r <= maxRound; r++) {
    for (const tm of (thinkByRound[r] || [])) { printThink(tm); seenThink.add(tm); }
    for (const t of (byRound[r] || [])) { _tcPrintHistTool(tab, t); seen.add(t); }
    if (textByRound[r]) tcPrint(tab, renderMarkdown(textByRound[r]), 'tc-asst');
  }
  // Anything without a matching round bucket (defensive) → append at the end.
  for (const tm of thinks) if (!seenThink.has(tm)) printThink(tm);
  for (const t of tools) if (!seen.has(t)) _tcPrintHistTool(tab, t);
}

function _tcPrintHistTool(tab, t) {
  const arg = _tcToolArg(t.args);
  const ok = t.is_error ? '' : ' <span class="tc-tool-state ok">✓</span>';
  tcPrint(tab, `<span class="tc-tool-dot">●</span> <span class="tc-tool-name">${esc(t.name || '')}</span>` +
               (arg ? ` <span class="tc-tool-arg">${esc(arg)}</span>` : '') + ok, 'tc-tool');
}

function _tcMsgText(m) {
  if (typeof m.content === 'string') return m.content;
  if (Array.isArray(m.content)) return m.content.map(b => (typeof b === 'string' ? b : (b.text || ''))).join('');
  return '';
}

// Attach to an in-progress turn on a freshly-opened past chat (resumable stream).
async function _tcAttachLive(tab) {
  const log = _tcLog(tab);
  const thinkRow = document.createElement('div'); thinkRow.className = 'tc-row tc-think'; thinkRow.style.display = 'none';
  const spinRow = document.createElement('div'); spinRow.className = 'tc-row tc-spin';
  _tcAddRow(tab, thinkRow); _tcAddRow(tab, spinRow);
  const live = _tcNewLive(thinkRow, spinRow);
  tab._live = live; tab.streaming = true; _tcSpinStart(tab, 'Denkt nach'); tcRenderStatus(tab);
  try {
    const resp = await fetch(`${BASE_URL}/v1/chat/stream?session_id=${encodeURIComponent(tab.sessionId)}`, {
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
    });
    const cbs = _tcCallbacks(tab, live);
    cbs.idle = () => _tcFinishTurn(tab, live);
    await _tcReadSSE(tab, resp, cbs);
  } catch (e) { /* finalized in finally */ } finally {
    if (tab.streaming) _tcFinishTurn(tab, live);   // guaranteed finalize
  }
}

// ── "Terminal-Chats" history list (under the working-dir tree) ───────────────
async function renderTermchatHistory() {
  const host = document.getElementById('terminal-chats');
  if (!host) return;
  if (!_term || !_term.open || !_term.agent || !_term.project) { host.innerHTML = ''; return; }
  let sessions = [];
  try {
    const d = await API.get(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/code-chats`);
    sessions = (d && d.sessions) || [];
  } catch (e) { /* keep empty */ }
  sessions.sort((a, b) => (b.last_active || 0) - (a.last_active || 0));
  // Propagate freshly-generated titles to any OPEN chat tab (the title is created
  // server-side after the first turn → the tab would otherwise stay "Chat").
  let _tabTitleChanged = false;
  for (const s of sessions) {
    if (!s.title) continue;
    const t = (_term.tabs || []).find(x => x.kind === 'chat' && x.sessionId === s.id);
    if (t && t.name !== s.title) { t.name = s.title; _tabTitleChanged = true; }
  }
  if (_tabTitleChanged && typeof _terminalRenderTabs === 'function') _terminalRenderTabs();
  const openIds = new Set((_term.tabs || []).filter(t => t.kind === 'chat' && t.sessionId).map(t => t.sessionId));
  // Which session is the ACTIVE chat tab → the selected row. The active chat is
  // the active tab of ANY pane that currently shows a chat (prefer the active
  // pane); falls back to the single open chat.
  let activeSid = '';
  const _ap = (typeof _terminalActivePane === 'function') ? _terminalActivePane() : null;
  const _apTab = _ap && (_term.tabs || []).find(x => x.id === _ap.active && x.kind === 'chat');
  if (_apTab) activeSid = _apTab.sessionId;
  else {
    for (const p of (_term.panes || [])) {
      const t = (_term.tabs || []).find(x => x.id === p.active && x.kind === 'chat');
      if (t) { activeSid = t.sessionId; break; }
    }
  }
  const rows = sessions.map(s => {
    const openCls = openIds.has(s.id) ? ' tc-hist-open' : '';
    const activeCls = (activeSid && s.id === activeSid) ? ' tc-hist-active' : '';
    const title = s.title || 'Chat';
    return `<div class="tc-hist-row${openCls}${activeCls}" data-sid="${esc(s.id)}" onclick="tcOpenHistory('${esc(s.id)}','${esc(title)}')"
      oncontextmenu="tcHistMenu(event,'${esc(s.id)}')" title="${esc(title)}">
      <span class="tc-hist-icon">◈</span><span class="tc-hist-label">${esc(title)}</span>
      <span style="flex:1"></span>
      <button class="tc-hist-del" title="Chat löschen"
        onclick="event.stopPropagation();tcDeleteHistory('${esc(s.id)}')">✕</button></div>`;
  }).join('');
  // "Delete all" only when there's something to delete.
  const delAllBtn = sessions.length ? `
      <button class="pt-act" onclick="event.stopPropagation();tcDeleteAllHistory()" title="Alle Terminal-Chats löschen">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
      </button>` : '';
  host.innerHTML = `
    <div class="tc-hist-head" onclick="tcHistToggle()">
      ${_tcHistCaret()}<span class="tc-hist-title">Terminal-Chats</span>
      <span style="flex:1"></span>
      ${delAllBtn}
      <button class="pt-act" onclick="event.stopPropagation();tcNewChat()" title="Neuer Terminal-Chat">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </button>
    </div>
    <div class="tc-hist-body" style="display:${_tcHistCollapsed() ? 'none' : 'block'}">
      ${rows || '<div class="tc-hist-empty">Noch keine Chats.</div>'}
    </div>`;
  // Collapsed → shrink to just the header (give the file tree the full height);
  // expanded → share the column with the tree at the persisted split. Hide the
  // drag handle when collapsed (no split to adjust).
  const collapsed = _tcHistCollapsed();
  host.classList.toggle('tc-collapsed', collapsed);
  const col = document.getElementById('terminal-tree-col');
  if (col) col.classList.toggle('chats-collapsed', collapsed);
}

// Delete ALL terminal-chats for this project (confirm first). Closes any open
// chat tabs for them, then deletes each session.
async function tcDeleteAllHistory() {
  if (!_term || !_term.agent || !_term.project) return;
  let sessions = [];
  try {
    const d = await API.get(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/code-chats`);
    sessions = (d && d.sessions) || [];
  } catch (_) { return; }
  if (!sessions.length) return;
  if (!confirm(`Alle ${sessions.length} Terminal-Chats dieses Projekts löschen?`)) return;
  for (const s of sessions) {
    const open = (_term.tabs || []).find(t => t.kind === 'chat' && t.sessionId === s.id);
    if (open && typeof terminalCloseTab === 'function') { try { await terminalCloseTab(open.id); } catch (_) {} }
    try { await API.deleteSession(s.id); } catch (_) {}
  }
  if (typeof showToast === 'function') showToast('Alle Terminal-Chats gelöscht');
  renderTermchatHistory();
}

function _tcHistKey() { return 'brain.tchist.collapsed.' + (_term && _term.project || 'p'); }
function _tcHistCollapsed() { return localStorage.getItem(_tcHistKey()) === '1'; }
function _tcHistCaret() {
  const open = !_tcHistCollapsed();
  return `<span class="pt-caret${open ? ' open' : ''}"><svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg></span>`;
}
function tcHistToggle() {
  localStorage.setItem(_tcHistKey(), _tcHistCollapsed() ? '0' : '1');
  renderTermchatHistory();
}

// Open a past chat: focus its tab if already open, else add a chat tab + load.
async function tcOpenHistory(sid, title) {
  const open = (_term.tabs || []).find(t => t.kind === 'chat' && t.sessionId === sid);
  if (open) { _terminalActivate(open.id); return; }
  const tab = _terminalAddChatTab(sid, null, title);
  if (tab) { await tcLoadTranscript(tab); }
}

function tcHistMenu(ev, sid) {
  ev.preventDefault(); ev.stopPropagation();
  const old = document.getElementById('terminal-tab-menu'); if (old) old.remove();
  const m = document.createElement('div');
  m.id = 'terminal-tab-menu'; m.className = 'terminal-tab-menu';
  m.style.left = ev.clientX + 'px'; m.style.top = ev.clientY + 'px';
  m.innerHTML = `<div onclick="tcDeleteHistory('${esc(sid)}');document.getElementById('terminal-tab-menu').remove()">Chat löschen</div>`;
  document.body.appendChild(m);
  const close = () => { const e = document.getElementById('terminal-tab-menu'); if (e) e.remove(); document.removeEventListener('click', close); };
  setTimeout(() => document.addEventListener('click', close), 0);
}

async function tcDeleteHistory(sid, skipConfirm) {
  if (!skipConfirm && !confirm('Diesen Terminal-Chat löschen?')) return;
  // Close an open tab for it first.
  const open = (_term.tabs || []).find(t => t.kind === 'chat' && t.sessionId === sid);
  if (open && typeof terminalCloseTab === 'function') await terminalCloseTab(open.id);
  try { await API.deleteSession(sid); } catch (_) {}
  renderTermchatHistory();
}

// Create a brand-new (empty) terminal-chat tab. Session is created lazily on the
// first send (see _tcEnsureSession).
function tcNewChat() {
  if (typeof _terminalAddChatTab !== 'function') return;
  // temp id until a session exists
  const tmp = 'chat-new-' + ((_term.tabs || []).length + 1) + '-' + (_term.tabs || []).length;
  const tab = _terminalAddChatTab('', null, 'Neuer Chat', tmp);
  if (tab) {
    const ta = tab.el.querySelector('.tc-ta');
    if (ta) setTimeout(() => ta.focus(), 40);
  }
}
