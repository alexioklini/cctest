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
//     tokensIn, tokensOut, cost, lastApiIn, maxContext }

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
  el.innerHTML = `
    <div class="tc-log" id="${esc(tab.id)}-log">
      <div class="tc-input-wrap">
        <div class="tc-ac" id="${esc(tab.id)}-ac" style="display:none"></div>
        <div class="tc-input">
          <span class="tc-prompt">›</span>
          <textarea class="tc-ta" rows="1" spellcheck="false"
            placeholder="Nachricht … (/ Befehle · ! für Shell · ↑↓ Verlauf)"></textarea>
        </div>
      </div>
    </div>
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
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) return;
    // While a turn streams, the only thing we accept is a cancel command — so
    // /cancel and /stop work mid-stream (everything else is ignored until the
    // turn ends). This is why a hung/long turn can always be stopped.
    if (tab.streaming) {
      const low = text.toLowerCase();
      if (low === '/cancel' || low === '/stop') { ta.value = ''; _tcAutosize(ta); tab.draft = ''; tcCancel(tab); }
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
  { name: 'clear', desc: 'neue Sitzung (leerer Kontext)' },
  { name: 'lcm', desc: 'Kontext komprimieren (LCM)' },
  { name: 'sync', desc: 'Projekt-Sync anstoßen' },
  { name: 'init', desc: 'BRAIN.md erzeugen' },
  { name: 'suggest', desc: 'nächste Eingabe vorschlagen' },
  { name: 'cancel', desc: 'laufende Antwort abbrechen' },
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

  // live rows for this turn
  const log = _tcLog(tab);
  const thinkRow = document.createElement('div'); thinkRow.className = 'tc-row tc-think'; thinkRow.style.display = 'none';
  const toolWrap = document.createElement('div'); toolWrap.className = 'tc-tools';
  const textRow = document.createElement('div'); textRow.className = 'tc-row tc-asst';
  const spinRow = document.createElement('div'); spinRow.className = 'tc-row tc-spin';
  _tcAddRow(tab, thinkRow); _tcAddRow(tab, toolWrap); _tcAddRow(tab, textRow); _tcAddRow(tab, spinRow);
  const live = { thinkRow, toolWrap, textRow, spinRow, text: '', think: '', toolById: {} };
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
  for (;;) {
    const { value, done } = await reader.read();
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
    thinking_start: () => { live.think = ''; if (tab.showTools) { live.thinkRow.style.display = ''; } _tcSpinSet(tab, 'Denkt nach'); },
    thinking_delta: (d) => {
      live.think += d.text || '';
      if (tab.showTools) { live.thinkRow.textContent = '⠿ ' + live.think; _tcScroll(tab); }
    },
    thinking_done: () => { live.think = ''; },
    queue_wait: (d) => { _tcSpinSet(tab, 'Wartet in der Warteschlange' + (d.position ? ` (#${d.position})` : '')); },
    queue_acquired: () => { _tcSpinSet(tab, 'Denkt nach'); },
    tool_call: (d) => {
      if (!tab.showTools) { _tcSpinSet(tab, 'Werkzeug: ' + (d.name || '')); return; }
      const tuid = d.tool_use_id || ('t' + Object.keys(live.toolById).length);
      let row = live.toolById[tuid];
      if (!row) {
        row = document.createElement('div'); row.className = 'tc-tool';
        live.toolWrap.appendChild(row); live.toolById[tuid] = row;
      }
      const arg = _tcToolArg(d.args);
      row.innerHTML = `<span class="tc-tool-dot">●</span> <span class="tc-tool-name">${esc(d.name || '')}</span>` +
                      (arg ? ` <span class="tc-tool-arg">${esc(arg)}</span>` : '') +
                      ` <span class="tc-tool-state">…</span>`;
      _tcSpinSet(tab, 'Werkzeug: ' + (d.name || ''));
      _tcScroll(tab);
    },
    tool_result: (d) => {
      if (!tab.showTools) return;
      const row = live.toolById[d.tool_use_id];
      if (row) { const s = row.querySelector('.tc-tool-state'); if (s) { s.textContent = '✓'; s.classList.add('ok'); } }
      _tcScroll(tab);
    },
    text_delta: (d) => {
      live.text += d.text || '';
      _tcSpinStop(tab);            // real text flowing → drop the spinner
      live.textRow.innerHTML = renderMarkdown(live.text);
      _tcScroll(tab);
    },
    usage: (d) => {
      // Live per-round figures for THIS turn (cumulative within the turn). Held
      // separately so the footer can show session-total + this-turn while
      // streaming; committed into the totals at `done`.
      if (typeof d.tokens_in === 'number') tab._liveIn = d.tokens_in;
      if (typeof d.tokens_out === 'number') tab._liveOut = d.tokens_out;
      if (d.cost != null) tab.cost = d.cost;
      if (typeof d.last_tokens_in === 'number') tab.lastApiIn = d.last_tokens_in;
      tcRenderStatus(tab);
    },
    done: (d) => {
      if (d.text) { live.text = d.text; live.textRow.innerHTML = renderMarkdown(live.text); }
      if (d.model && (!tab.model || tab.model === 'auto')) tab._autoPicked = d.model;
      else if (d.model) tab.model = d.model;
      // Commit this turn's tokens into the running session totals (mirrors the
      // main chat's done handler). `done` carries the final per-turn figures;
      // fall back to the last live `usage` value if absent.
      const tin = (typeof d.tokens_in === 'number') ? d.tokens_in : (tab._liveIn || 0);
      const tout = (typeof d.tokens_out === 'number') ? d.tokens_out : (tab._liveOut || 0);
      tab.tokensIn = (tab.tokensIn || 0) + tin;
      tab.tokensOut = (tab.tokensOut || 0) + tout;
      tab._liveIn = 0; tab._liveOut = 0;
      if (d.cost != null) tab.cost = d.cost;
      if (d.max_context) tab.maxContext = d.max_context;
      if (typeof d.last_tokens_in === 'number') tab.lastApiIn = d.last_tokens_in;
      tcRenderStatus(tab);
      _tcFinishTurn(tab, live);   // also aborts the reader → loop exits promptly
      // Title may have been auto-generated server-side → refresh the history list.
      if (typeof renderTermchatHistory === 'function') setTimeout(() => renderTermchatHistory(), 1500);
    },
    error: (d) => { _tcFinishTurn(tab, live); tcPrint(tab, esc(d.message || 'Fehler'), 'tc-err'); },
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
  _tcSpinStop(tab);
  if (live) {
    if (live.spinRow && live.spinRow.parentNode) live.spinRow.parentNode.removeChild(live.spinRow);
    if (live.thinkRow && live.thinkRow.parentNode && (!tab.showTools || !live.thinkRow.textContent)) live.thinkRow.style.display = 'none';
    if (live.textRow && !live.text) live.textRow.parentNode && live.textRow.parentNode.removeChild(live.textRow);
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

// ── Status footer ────────────────────────────────────────────────────────────
function tcRenderStatus(tab) {
  const el = document.getElementById(tab.id + '-status');
  if (!el) return;
  const model = tab._autoPicked ? `auto→${tab._autoPicked}` : (tab.model || 'Standard');
  const think = _TC_THINK[tab.thinking || 'none'] || (tab.thinking || '');
  const tin = (tab.tokensIn || 0) + (tab.streaming ? (tab._liveIn || 0) : 0);
  const tout = (tab.tokensOut || 0) + (tab.streaming ? (tab._liveOut || 0) : 0);
  const cost = (tab.cost != null) ? ('$' + Number(tab.cost).toFixed(4)) : '—';
  let ctx = '';
  if (tab.maxContext && tab.lastApiIn) ctx = ' · ctx ' + Math.min(100, Math.round(tab.lastApiIn / tab.maxContext * 100)) + '%';
  const toolsBadge = tab.showTools ? '' : ' · tools:aus';
  const dot = tab.streaming ? '<span class="tc-live">●</span> ' : '';
  const cancelHint = tab.streaming ? ' · <span class="tc-cancel-hint">Esc oder /cancel zum Abbrechen</span>' : '';
  // Export-as-markdown button (direct browser download) — only meaningful once
  // the chat has rows.
  const exportBtn = `<button class="tc-st-btn" title="Chatverlauf als Markdown herunterladen"
    onclick="tcExportMarkdown('${esc(tab.id)}')">⬇ .md</button>`;
  el.innerHTML = `<span class="tc-st-info">${dot}<span class="tc-st-model">${esc(model)}</span> · think:${esc(think)} · ${tin}/${tout} tok · ${esc(cost)}${ctx}${toolsBadge}${cancelHint}</span>${exportBtn}`;
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
  // re-id the inner DOM nodes (log/status reference tab.id)
  const log = tab.el.querySelector('.tc-log'); if (log) log.id = newId + '-log';
  const st = tab.el.querySelector('.tc-status'); if (st) st.id = newId + '-status';
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
    case 'init': return _tcCmdInit(tab);
    case 'sync': return _tcCmdSync(tab);
    case 'suggest': return _tcSuggest(tab, true);
    case 'cancel': case 'stop': return tcCancel(tab);
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
    '/clear                 — neue Sitzung (leerer Kontext)',
    '/lcm                   — Kontext komprimieren (LCM)',
    '/sync                  — Projekt-Sync anstoßen',
    '/init                  — Projektanweisungen generieren',
    '/suggest               — nächste Eingabe vorschlagen (auch Tab)',
    '/cancel                — laufende Antwort abbrechen',
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
  tab.tokensIn = 0; tab.tokensOut = 0; tab.cost = null; tab.lastApiIn = 0;
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
    try { await API.post(`/v1/sessions/${tab.sessionId}/manage`, { action: 'caveman_mode', value: n }); } catch (_) {}
  }
  tcPrint(tab, `Caveman-Stil → <b>${n}</b>`, 'tc-sys');
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
    for (const m of (d.messages || [])) _tcRenderHistMsg(tab, m);
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
  if (role === 'assistant') { tcPrint(tab, renderMarkdown(_tcMsgText(m)), 'tc-asst'); return; }
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

function _tcMsgText(m) {
  if (typeof m.content === 'string') return m.content;
  if (Array.isArray(m.content)) return m.content.map(b => (typeof b === 'string' ? b : (b.text || ''))).join('');
  return '';
}

// Attach to an in-progress turn on a freshly-opened past chat (resumable stream).
async function _tcAttachLive(tab) {
  const log = _tcLog(tab);
  const thinkRow = document.createElement('div'); thinkRow.className = 'tc-row tc-think'; thinkRow.style.display = 'none';
  const toolWrap = document.createElement('div'); toolWrap.className = 'tc-tools';
  const textRow = document.createElement('div'); textRow.className = 'tc-row tc-asst';
  const spinRow = document.createElement('div'); spinRow.className = 'tc-row tc-spin';
  _tcAddRow(tab, thinkRow); _tcAddRow(tab, toolWrap); _tcAddRow(tab, textRow); _tcAddRow(tab, spinRow);
  const live = { thinkRow, toolWrap, textRow, spinRow, text: '', think: '', toolById: {} };
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
  const openIds = new Set((_term.tabs || []).filter(t => t.kind === 'chat' && t.sessionId).map(t => t.sessionId));
  const rows = sessions.map(s => {
    const active = openIds.has(s.id) ? ' tc-hist-open' : '';
    const title = s.title || 'Chat';
    return `<div class="tc-hist-row${active}" data-sid="${esc(s.id)}" onclick="tcOpenHistory('${esc(s.id)}','${esc(title)}')"
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
