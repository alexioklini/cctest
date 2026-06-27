// Code-mode interactive terminal — xterm.js front-end over the SSE+POST PTY
// backend (server_lib/terminal.py). Bottom-docked panel, multiple tabs (= PTY
// sessions), shown in BOTH the code-mode project-detail view and the code-mode
// chat. Sessions are keyed per (agent,project) server-side and REUSED across
// both views: opening the panel lists existing sessions and re-attaches.
//
// Each tab holds: { id, term (xterm), fit (FitAddon), offset (abs byte pos),
// es (EventSource), el (xterm host div) }.

const _term = {
  agent: '', project: '', wd: '',
  tabs: [],          // [{id, term, fit, offset, es, el}]
  active: null,      // active session id
  open: false,
};

// Resolve the current code-mode project context from whichever view we're in.
// Returns {agent, project, wd} or null if not a code-mode project.
async function _terminalCtx() {
  // 1) project-detail view
  if (state._projectDetail && state._projectDetail.code_mode) {
    return {
      agent: state._projectDetailAgent || 'main',
      project: state._projectDetailName || '',
      wd: state._projectDetail.working_dir || '',
    };
  }
  // 2) code-mode chat — load the active chat's project metadata
  const projName = state.currentProject || (state.activeChat && state.activeChat.project);
  if (!projName) return null;
  const agent = state.activeAgentId || 'main';
  try {
    const p = await API.get(`/v1/agents/${agent}/projects/${encodeURIComponent(projName)}`);
    if (p && p.code_mode) return { agent, project: projName, wd: p.working_dir || '' };
  } catch (e) { /* not a project chat */ }
  return null;
}

// Is the terminal available right now? (used to show/hide the toggle button)
// In the project-detail view: code_mode flag on the loaded detail. In a chat:
// reuse the existing _workdirIsCodeChat() sync check (cache-backed).
function terminalAvailable() {
  if (state._projectDetail && state._projectDetailName === state.currentProject
      && state._projectDetail.code_mode) return true;
  if (typeof _workdirIsCodeChat === 'function') return _workdirIsCodeChat();
  return false;
}

// Show/hide the status-bar terminal toggle based on code-mode context. Also
// auto-closes the panel if we navigated away from a code-mode project.
function terminalRefreshToggle() {
  const btn = document.getElementById('terminal-toggle-btn');
  const avail = terminalAvailable();
  if (btn) btn.classList.toggle('code-mode-available', !!avail);
  if (!avail && _term.open) terminalTogglePanel(false);
}

async function terminalTogglePanel(force) {
  const panel = document.getElementById('terminal-panel');
  if (!panel) return;
  const want = (typeof force === 'boolean') ? force : !_term.open;
  if (want) {
    const ctx = await _terminalCtx();
    if (!ctx) { if (typeof showToast === 'function') showToast('Terminal nur in Code-Mode-Projekten'); return; }
    _term.agent = ctx.agent; _term.project = ctx.project; _term.wd = ctx.wd;
    panel.style.display = 'flex';
    document.getElementById('main-content').classList.add('terminal-open');
    _term.open = true;
    _terminalRestoreHeight();
    await _terminalLoadSessions();
  } else {
    panel.style.display = 'none';
    document.getElementById('main-content').classList.remove('terminal-open');
    _term.open = false;
  }
}

async function _terminalLoadSessions() {
  // 1) reattach live terminal sessions (reused server-side per project)
  let existing = [];
  try {
    const d = await API.get(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions`);
    existing = (d && d.sessions) || [];
    const liveIds = new Set(existing.map(s => s.id));
    _term.tabs = _term.tabs.filter(t => t.kind !== 'terminal' || liveIds.has(t.id) || t._fresh);
    for (const s of existing) {
      if (!_term.tabs.find(t => t.id === s.id)) _terminalAddTab(s.id);
    }
  } catch (e) { /* terminal backend may be down — editors still work */ }
  // 2) restore persisted editor tabs (open file paths) from bottom_workspace
  let ws = null;
  try {
    const p = await API.get(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}`);
    ws = p && p.bottom_workspace;
  } catch (e) { /* ignore */ }
  if (ws && Array.isArray(ws.editor_files)) {
    for (const fp of ws.editor_files) {
      if (!_term.tabs.find(t => t.kind === 'editor' && t.path === fp)) {
        await terminalOpenFile(fp);  // appends an editor tab
      }
    }
  }
  if (!_term.tabs.length) { await terminalNewTab(); return; }
  _terminalRenderTabs();
  const want = (ws && ws.active && _term.tabs.find(t => t.id === ws.active)) ? ws.active : _term.tabs[0].id;
  _terminalActivate(want);
}

// Persist the bottom workspace (open editor file paths + active tab) to the
// project (server-side, per-project, so it round-trips across devices). Live
// terminal sessions are reattached from the server list, so we only persist the
// editor file set + which tab was active. Debounced.
let _terminalPersistTimer = null;
function _terminalPersist() {
  if (!_term.open || !_term.project) return;
  clearTimeout(_terminalPersistTimer);
  _terminalPersistTimer = setTimeout(() => {
    const ws = {
      editor_files: _term.tabs.filter(t => t.kind === 'editor').map(t => t.path),
      active: _term.active || '',
    };
    try { API.updateProject(_term.agent, _term.project, { bottom_workspace: ws }); } catch (_) {}
  }, 600);
}

async function terminalNewTab() {
  try {
    const s = await API.post(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions`, {});
    if (s.error) { if (typeof showToast === 'function') showToast(s.error); return; }
    _terminalAddTab(s.id);
    _terminalRenderTabs();
    _terminalActivate(s.id);
  } catch (e) { if (typeof showToast === 'function') showToast('Terminal konnte nicht gestartet werden'); }
}

function _terminalAddTab(id) {
  if (_term.tabs.find(t => t.id === id)) return;
  const el = document.createElement('div');
  el.className = 'terminal-xterm';
  el.style.display = 'none';
  document.getElementById('terminal-body').appendChild(el);
  const term = new Terminal({
    fontSize: 13, fontFamily: 'var(--font-mono, monospace)',
    cursorBlink: true, scrollback: 5000,
    theme: { background: '#1e1e1e' },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(el);
  const tab = { id, kind: 'terminal', term, fit, offset: 0, attached: false, abort: null, el, _fresh: true };
  // keystrokes → POST input
  term.onData(data => {
    fetch(`${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${id}/input`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ data }),
    }).catch(() => {});
  });
  _term.tabs.push(tab);
}

function _terminalActivate(id) {
  _term.active = id;
  _term.tabs.forEach(t => { t.el.style.display = (t.id === id) ? 'block' : 'none'; });
  _terminalRenderTabs();
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  if (tab.kind === 'editor') {
    setTimeout(() => { try { tab.cm.refresh(); tab.cm.focus(); } catch (_) {} }, 30);
    _terminalPersist();
    return;
  }
  // terminal: (re)attach the output stream from our current offset
  if (!tab.attached) _terminalAttach(tab);
  setTimeout(() => { try { tab.fit.fit(); tab.term.focus(); _terminalSendResize(tab); } catch (_) {} }, 30);
  _terminalPersist();
}

// Stream PTY output via fetch+reader (NOT EventSource — like the chat stream,
// because EventSource can't send the Bearer header; we never put the token in
// the URL). Parses SSE frames ('out' = base64 bytes, 'closed').
async function _terminalAttach(tab) {
  tab.attached = true;
  tab.abort = new AbortController();
  const url = `${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${tab.id}/stream?since=${tab.offset}`;
  try {
    const resp = await fetch(url, {
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
      signal: tab.abort.signal,
    });
    if (!resp.ok || !resp.body) { tab.attached = false; return; }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        let ev = 'message', data = '';
        frame.split('\n').forEach(line => {
          if (line.startsWith('event: ')) ev = line.slice(7);
          else if (line.startsWith('data: ')) data += line.slice(6);
        });
        if (ev === 'out' && data) {
          try { const bytes = atob(data); tab.term.write(bytes); tab.offset += bytes.length; } catch (_) {}
        } else if (ev === 'closed') {
          tab.term.write('\r\n[Sitzung beendet]\r\n');
        }
      }
    }
  } catch (e) { /* aborted or network — leave attached=false so a re-activate reconnects */ }
  tab.attached = false;
}

function _terminalSendResize(tab) {
  if (!tab) return;
  const rows = tab.term.rows, cols = tab.term.cols;
  fetch(`${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${tab.id}/input`, {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows, cols }),
  }).catch(() => {});
}

async function terminalCloseTab(id, ev) {
  if (ev) ev.stopPropagation();
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  // Warn on unsaved editor changes.
  if (tab.kind === 'editor' && tab.dirty) {
    if (!confirm(`„${tab.name}“ hat ungespeicherte Änderungen. Trotzdem schließen?`)) return;
  }
  if (tab.kind === 'editor') {
    tab.el.remove();
  } else {
    if (tab.abort) { try { tab.abort.abort(); } catch (_) {} }
    try { tab.term.dispose(); } catch (_) {}
    tab.el.remove();
    fetch(`${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${id}/close`, {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
    }).catch(() => {});
  }
  _term.tabs = _term.tabs.filter(t => t.id !== id);
  if (_term.active === id) {
    if (_term.tabs.length) _terminalActivate(_term.tabs[0].id);
    else terminalTogglePanel(false);
  }
  _terminalRenderTabs();
  _terminalPersist();
}

// Bulk close (browser-style): 'all' | 'others' | 'right' relative to a tab id.
async function terminalCloseTabs(mode, anchorId, ev) {
  if (ev) ev.stopPropagation();
  let ids = [];
  if (mode === 'all') {
    ids = _term.tabs.map(t => t.id);
  } else if (mode === 'others') {
    ids = _term.tabs.filter(t => t.id !== anchorId).map(t => t.id);
  } else if (mode === 'right') {
    const i = _term.tabs.findIndex(t => t.id === anchorId);
    ids = i >= 0 ? _term.tabs.slice(i + 1).map(t => t.id) : [];
  }
  // close non-anchor first so the anchor stays active
  for (const id of ids) {
    // skip the confirm-loop spam: only the dirty ones prompt
    await terminalCloseTab(id);
  }
}

function _terminalTabMenu(id, ev) {
  ev.preventDefault(); ev.stopPropagation();
  const old = document.getElementById('terminal-tab-menu');
  if (old) old.remove();
  const m = document.createElement('div');
  m.id = 'terminal-tab-menu';
  m.className = 'terminal-tab-menu';
  m.style.left = ev.clientX + 'px';
  m.style.top = ev.clientY + 'px';
  m.innerHTML = `
    <div onclick="terminalCloseTab('${id}'); document.getElementById('terminal-tab-menu').remove()">Tab schließen</div>
    <div onclick="terminalCloseTabs('others','${id}'); document.getElementById('terminal-tab-menu').remove()">Andere schließen</div>
    <div onclick="terminalCloseTabs('right','${id}'); document.getElementById('terminal-tab-menu').remove()">Rechte schließen</div>
    <div onclick="terminalCloseTabs('all'); document.getElementById('terminal-tab-menu').remove()">Alle schließen</div>`;
  document.body.appendChild(m);
  const close = () => { const e = document.getElementById('terminal-tab-menu'); if (e) e.remove(); document.removeEventListener('click', close); };
  setTimeout(() => document.addEventListener('click', close), 0);
}

function _terminalRenderTabs() {
  const bar = document.getElementById('terminal-tabs');
  if (!bar) return;
  let termN = 0;
  bar.innerHTML = _term.tabs.map((t) => {
    const active = t.id === _term.active ? ' active' : '';
    let label, icon;
    if (t.kind === 'editor') {
      label = (t.dirty ? '● ' : '') + esc(t.name);
      icon = '';
    } else {
      termN += 1; label = 'Terminal ' + termN; icon = '';
    }
    return `<div class="terminal-tab${active}" title="${esc(t.path || label)}" onclick="_terminalActivate('${t.id}')" oncontextmenu="_terminalTabMenu('${t.id}', event)">
      <span>${icon}${label}</span>
      <span class="terminal-tab-close" onclick="terminalCloseTab('${t.id}', event)">✕</span>
    </div>`;
  }).join('');
}

// ── Editor tabs (CodeMirror 5) ───────────────────────────────────────────────
// Map a file extension → a CodeMirror mode (modes loaded in index.html).
function _cmModeFor(ext) {
  ext = (ext || '').toLowerCase();
  const m = {
    py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript',
    json: { name: 'javascript', json: true }, ts: 'javascript', jsx: 'javascript', tsx: 'javascript',
    html: 'htmlmixed', htm: 'htmlmixed', xml: 'xml', svg: 'xml',
    css: 'css', scss: 'css', less: 'css',
    md: 'markdown', markdown: 'markdown',
    c: 'text/x-csrc', h: 'text/x-csrc', cpp: 'text/x-c++src', cc: 'text/x-c++src',
    hpp: 'text/x-c++src', java: 'text/x-java', cs: 'text/x-csharp',
    yml: 'yaml', yaml: 'yaml', sh: 'shell', bash: 'shell', zsh: 'shell',
    go: 'go', rs: 'rust',
  };
  return m[ext] || null;
}

// Open a file as an editor tab (or focus it if already open).
async function terminalOpenFile(absPath) {
  if (!absPath) return;
  if (!_term.open) { await terminalTogglePanel(true); }
  const existing = _term.tabs.find(t => t.kind === 'editor' && t.path === absPath);
  if (existing) { _terminalActivate(existing.id); return; }
  const name = absPath.split('/').pop();
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  const id = 'edit-' + absPath;
  const el = document.createElement('div');
  el.className = 'terminal-editor';
  el.style.display = 'none';
  el.innerHTML = `
    <div class="editor-toolbar">
      <button class="btn-secondary ed-mode active" data-mode="render" onclick="terminalEditorMode('${id}','render')">Ansicht</button>
      <button class="btn-secondary ed-mode" data-mode="raw" onclick="terminalEditorMode('${id}','raw')">Bearbeiten</button>
      <button class="btn-secondary" onclick="codeSymbolPalette()" title="Symbol suchen (${_isMac()?'⌘':'Strg'}+P)">Symbole</button>
      <button class="btn-secondary" onclick="codeCypherBar()" title="Code-Index per Cypher abfragen (Power-User)">Cypher</button>
      <span style="flex:1"></span>
      <button class="btn-secondary ed-save" onclick="terminalEditorSave('${id}')" disabled>Speichern</button>
      <button class="btn-secondary" onclick="terminalEditorDownload('${id}')">Herunterladen</button>
    </div>
    <div class="editor-render"></div>
    <div class="editor-cm" style="display:none"></div>`;
  document.getElementById('terminal-body').appendChild(el);
  const tab = { id, kind: 'editor', path: absPath, name, ext, el, cm: null,
                raw: '', mode: 'render', dirty: false, loaded: false };
  _term.tabs.push(tab);
  _terminalRenderTabs();
  _terminalActivate(id);
  // load content
  try {
    const d = await API.get(`/v1/files/preview?path=${encodeURIComponent(absPath)}&lines=100000`);
    if (d.error) { el.querySelector('.editor-render').innerHTML = `<div class="pt-empty">${esc(d.error)}</div>`; return; }
    if (d.type === 'image') {
      _terminalEditorShowImage(tab);
      // images: no edit
      el.querySelector('.ed-mode[data-mode="raw"]').style.display = 'none';
      el.querySelector('.ed-save').style.display = 'none';
      return;
    }
    tab.raw = d.content || '';
    tab.truncated = !!d.truncated;
    tab.loaded = true;
    _terminalEditorPaint(tab);
  } catch (e) {
    el.querySelector('.editor-render').innerHTML = '<div class="pt-empty">Datei konnte nicht geladen werden.</div>';
  }
}

function terminalEditorMode(id, mode) {
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  tab.mode = mode;
  tab.el.querySelectorAll('.ed-mode').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  _terminalEditorPaint(tab);
}

function _terminalEditorPaint(tab) {
  const renderEl = tab.el.querySelector('.editor-render');
  const cmEl = tab.el.querySelector('.editor-cm');
  if (tab.mode === 'raw') {
    // edit mode: CodeMirror
    renderEl.style.display = 'none';
    cmEl.style.display = 'block';
    if (!tab.cm) {
      tab.cm = CodeMirror(cmEl, {
        value: tab.raw, mode: _cmModeFor(tab.ext), lineNumbers: true,
        lineWrapping: false, indentUnit: 4,
        extraKeys: {
          // index-fed autocomplete (project symbols from the cbm index).
          // Ctrl-Space only — NOT Cmd-Space (that's macOS Spotlight, the OS
          // grabs it before the browser sees it).
          'Ctrl-Space': (cm) => codeIndexComplete(cm),
        },
      });
      tab.cm.setSize('100%', '100%');
      tab.cm.on('change', () => {
        tab.dirty = true;
        const sv = tab.el.querySelector('.ed-save'); if (sv) sv.disabled = false;
        _terminalRenderTabs();
      });
      // right-click a symbol → go-to-definition / who-calls menu
      tab.cm.on('contextmenu', (cm, e) => _codeIndexContextMenu(cm, e, tab));
      // hover a symbol → signature + docstring + caller count tooltip
      _codeIndexAttachHover(tab);
    } else {
      // keep CM in sync if raw changed via save
      if (tab.cm.getValue() !== tab.raw && !tab.dirty) tab.cm.setValue(tab.raw);
    }
    setTimeout(() => { try { tab.cm.refresh(); tab.cm.focus(); } catch (_) {} }, 20);
  } else {
    // render mode: syntax-highlighted read-only (or markdown)
    cmEl.style.display = 'none';
    renderEl.style.display = 'block';
    const txt = (tab.cm && tab.dirty) ? tab.cm.getValue() : tab.raw;
    if (tab.ext === 'md' && typeof renderMarkdown === 'function') {
      renderEl.innerHTML = `<div class="ref-inline-md msg-content" style="padding:10px">${renderMarkdown(txt)}</div>`;
      renderEl.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch (_) {} });
    } else {
      const lang = (typeof _ptLangFor === 'function') ? _ptLangFor(tab.ext) : 'plaintext';
      let html; try { html = hljs.highlight(txt, { language: lang }).value; } catch (_) { html = esc(txt); }
      renderEl.innerHTML = `<pre class="editor-pre"><code class="hljs">${html}</code></pre>`;
    }
  }
}

async function _terminalEditorShowImage(tab) {
  try {
    const resp = await fetch(`${BASE_URL}/v1/files/download?path=${encodeURIComponent(tab.path)}`,
      { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') } });
    const blob = await resp.blob();
    tab.el.querySelector('.editor-render').innerHTML =
      `<img src="${URL.createObjectURL(blob)}" style="max-width:100%;padding:10px"/>`;
  } catch (e) { /* ignore */ }
}

async function terminalEditorSave(id) {
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab || !tab.cm) return;
  const content = tab.cm.getValue();
  try {
    const r = await fetch(`${BASE_URL}/v1/files/save`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: tab.path, content }),
    });
    const d = await r.json();
    if (d.error) { if (typeof showToast === 'function') showToast(d.error); return; }
    tab.raw = content; tab.dirty = false;
    const sv = tab.el.querySelector('.ed-save'); if (sv) sv.disabled = true;
    _terminalRenderTabs();
    if (typeof showToast === 'function') showToast('Gespeichert');
  } catch (e) { if (typeof showToast === 'function') showToast('Speichern fehlgeschlagen'); }
}

function terminalEditorDownload(id) {
  const tab = _term.tabs.find(t => t.id === id);
  if (tab && typeof _ptDownloadFile === 'function') _ptDownloadFile(tab.path, tab.name);
}

// Create a new file in the project tree (prompts for a relative path).
async function terminalNewFile() {
  if (!_term.wd) { const ctx = await _terminalCtx(); if (ctx) { _term.agent = ctx.agent; _term.project = ctx.project; _term.wd = ctx.wd; } }
  if (!_term.wd) { if (typeof showToast === 'function') showToast('Kein Code-Mode-Projekt'); return; }
  const rel = prompt('Neue Datei (relativer Pfad im Projekt):', 'neu.txt');
  if (!rel) return;
  const clean = rel.replace(/^\/+/, '').split('/').filter(p => p && p !== '..').join('/');
  if (!clean) return;
  const abs = _term.wd.replace(/\/+$/, '') + '/' + clean;
  try {
    const r = await fetch(`${BASE_URL}/v1/files/save`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: abs, content: '' }),
    });
    const d = await r.json();
    if (d.error) { if (typeof showToast === 'function') showToast(d.error); return; }
    if (typeof refreshCodeWorkingTree === 'function') refreshCodeWorkingTree();
    await terminalOpenFile(abs);
    terminalEditorMode('edit-' + abs, 'raw');
  } catch (e) { if (typeof showToast === 'function') showToast('Datei konnte nicht erstellt werden'); }
}

// Maximize / restore the bottom panel.
function terminalToggleMaximize() {
  const panel = document.getElementById('terminal-panel');
  if (!panel) return;
  _term.maximized = !_term.maximized;
  panel.classList.toggle('maximized', _term.maximized);
  setTimeout(_terminalOnResize, 30);
}

// Re-fit the active terminal on window resize (editors just need a refresh).
function _terminalOnResize() {
  if (!_term.open) return;
  const tab = _term.tabs.find(t => t.id === _term.active);
  if (!tab) return;
  if (tab.kind === 'editor') { try { tab.cm.refresh(); } catch (_) {} return; }
  try { tab.fit.fit(); _terminalSendResize(tab); } catch (_) {}
}
window.addEventListener('resize', _terminalOnResize);

// Vertical resize of the bottom panel by dragging its top handle. The chosen
// height is remembered in localStorage (like right-panel-width) and restored on
// load, so the user's preferred terminal height persists across sessions.
const _TERMINAL_HEIGHT_KEY = 'terminal-panel-height';

function _terminalRestoreHeight() {
  const panel = document.getElementById('terminal-panel');
  if (!panel) return;
  const saved = parseInt(localStorage.getItem(_TERMINAL_HEIGHT_KEY) || '', 10);
  if (saved && saved >= 120) {
    panel.style.height = Math.min(saved, window.innerHeight - 160) + 'px';
  }
}

function _terminalInitResize() {
  const handle = document.getElementById('terminal-resize-handle');
  const panel = document.getElementById('terminal-panel');
  if (!handle || !panel) return;
  _terminalRestoreHeight();
  let startY = 0, startH = 0, dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true; startY = e.clientY; startH = panel.offsetHeight;
    document.body.style.userSelect = 'none'; e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const h = Math.max(120, Math.min(window.innerHeight - 160, startH + (startY - e.clientY)));
    panel.style.height = h + 'px';
    _terminalOnResize();
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; document.body.style.userSelect = '';
    // persist the chosen height as the preferred value
    try { localStorage.setItem(_TERMINAL_HEIGHT_KEY, String(panel.offsetHeight)); } catch (_) {}
    _terminalOnResize();
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// Code-index editor support — symbol palette, go-to-definition, who-calls,
// autocomplete, hover. All fed by the cbm code index via the lean
// .../code-index/symbols endpoint (?q= fuzzy search · ?def= definition+meta ·
// ?callers= inbound callers). cbm returns ?q file paths REPO-RELATIVE, so we
// join _term.wd to reach an absolute path for terminalOpenFile.
// ═══════════════════════════════════════════════════════════════════════════

function _isMac() {
  return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
}

// Query the code-index endpoint for the active code-mode project. `params` is a
// query string (e.g. 'q=foo&limit=20'). Returns the parsed object or null.
async function _codeIndexFetch(params) {
  if (!_term.project) {
    const ctx = await _terminalCtx();
    if (!ctx) return null;
    _term.agent = ctx.agent; _term.project = ctx.project; _term.wd = ctx.wd;
  }
  try {
    return await API.get(`/v1/agents/${_term.agent}/projects/`
      + `${encodeURIComponent(_term.project)}/code-index/symbols?${params}`);
  } catch (e) { return null; }
}

// Join a repo-relative file path from cbm to an absolute path under working_dir.
function _codeIndexAbs(relOrAbs) {
  if (!relOrAbs) return '';
  if (relOrAbs.startsWith('/')) return relOrAbs;       // already absolute
  const wd = (_term.wd || '').replace(/\/+$/, '');
  return wd ? `${wd}/${relOrAbs}` : relOrAbs;
}

// Open `absPath` in the editor and move the cursor to `line` (1-based), centred.
async function _terminalJumpTo(absPath, line) {
  if (!absPath) return;
  await terminalOpenFile(absPath);
  const id = 'edit-' + absPath;
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  // jumping needs the CodeMirror instance → force raw (edit) mode
  terminalEditorMode(id, 'raw');
  const doJump = () => {
    if (!tab.cm) { setTimeout(doJump, 40); return; }
    const ln = Math.max(0, (parseInt(line, 10) || 1) - 1);
    tab.cm.setCursor({ line: ln, ch: 0 });
    // centre the target line in the viewport
    const t = tab.cm.charCoords({ line: ln, ch: 0 }, 'local').top;
    const h = tab.cm.getScrollInfo().clientHeight;
    tab.cm.scrollTo(null, Math.max(0, t - h / 2));
    tab.cm.addLineClass(ln, 'background', 'cm-jump-flash');
    setTimeout(() => { try { tab.cm.removeLineClass(ln, 'background', 'cm-jump-flash'); } catch (_) {} }, 1200);
    tab.cm.focus();
  };
  setTimeout(doJump, 60);
}

// ─── Symbol palette (Cmd/Ctrl-P) ─────────────────────────────────────────────
let _codePaletteDebounce = null;

function codeSymbolPalette() {
  if (document.getElementById('code-palette')) return;  // already open
  const ov = document.createElement('div');
  ov.id = 'code-palette';
  ov.className = 'code-palette-overlay';
  ov.innerHTML = `
    <div class="code-palette" onclick="event.stopPropagation()">
      <input id="code-palette-input" type="text" autocomplete="off" spellcheck="false"
             placeholder="Symbol suchen (Funktion, Methode, Klasse) …">
      <div id="code-palette-results" class="code-palette-results">
        <div class="code-palette-hint">Tippen, um Projekt-Symbole zu durchsuchen.</div>
      </div>
    </div>`;
  ov.addEventListener('click', codeSymbolPaletteClose);
  document.body.appendChild(ov);
  const input = document.getElementById('code-palette-input');
  input.addEventListener('input', () => {
    clearTimeout(_codePaletteDebounce);
    _codePaletteDebounce = setTimeout(() => _codePaletteSearch(input.value), 160);
  });
  input.addEventListener('keydown', _codePaletteKeydown);
  input.focus();
}

function codeSymbolPaletteClose() {
  const ov = document.getElementById('code-palette');
  if (ov) ov.remove();
}

async function _codePaletteSearch(q) {
  const box = document.getElementById('code-palette-results');
  if (!box) return;
  q = (q || '').trim();
  if (!q) { box.innerHTML = '<div class="code-palette-hint">Tippen, um Projekt-Symbole zu durchsuchen.</div>'; return; }
  box.innerHTML = '<div class="code-palette-hint">Suche …</div>';
  const d = await _codeIndexFetch(`q=${encodeURIComponent(q)}&limit=40`);
  // the box may have been replaced by a newer query; only paint if still ours
  const cur = document.getElementById('code-palette-input');
  if (!cur || cur.value.trim() !== q) return;
  if (!d || d.error) { box.innerHTML = `<div class="code-palette-hint">${esc((d && d.error) || 'Index nicht verfügbar')}</div>`; return; }
  const syms = d.symbols || [];
  if (!syms.length) { box.innerHTML = '<div class="code-palette-hint">Keine Treffer.</div>'; return; }
  box.innerHTML = syms.map((s, i) => `
    <div class="code-palette-row${i === 0 ? ' active' : ''}" data-idx="${i}"
         onclick="_codePalettePick(${i})">
      <span class="cp-label cp-${esc((s.label || '').toLowerCase())}">${esc(s.label || '')}</span>
      <span class="cp-name">${esc(s.name || '')}</span>
      <span class="cp-loc">${esc(s.file || '')}${s.line ? ':' + s.line : ''}</span>
    </div>`).join('');
  _codePaletteSyms = syms;
}

let _codePaletteSyms = [];

function _codePaletteKeydown(e) {
  const rows = Array.from(document.querySelectorAll('.code-palette-row'));
  if (e.key === 'Escape') { codeSymbolPaletteClose(); return; }
  if (!rows.length) return;
  let idx = rows.findIndex(r => r.classList.contains('active'));
  if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(rows.length - 1, idx + 1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(0, idx - 1); }
  else if (e.key === 'Enter') { e.preventDefault(); _codePalettePick(idx < 0 ? 0 : idx); return; }
  else return;
  rows.forEach(r => r.classList.remove('active'));
  rows[idx].classList.add('active');
  rows[idx].scrollIntoView({ block: 'nearest' });
}

function _codePalettePick(idx) {
  const s = _codePaletteSyms[idx];
  if (!s) return;
  codeSymbolPaletteClose();
  _terminalJumpTo(_codeIndexAbs(s.file), s.line);
}

// ─── Index-fed autocomplete (CodeMirror show-hint) ───────────────────────────
async function codeIndexComplete(cm) {
  if (!cm || typeof cm.showHint !== 'function') return;
  const cur = cm.getCursor();
  const token = cm.getTokenAt(cur);
  const word = (token.string || '').trim();
  if (word.length < 2) return;   // need a couple chars to query the index
  const d = await _codeIndexFetch(`q=${encodeURIComponent(word)}&limit=25`);
  const syms = (d && d.symbols) || [];
  // dedup by name, keep the symbol so we can show its kind
  const seen = new Set();
  const list = [];
  for (const s of syms) {
    if (!s.name || seen.has(s.name)) continue;
    seen.add(s.name);
    list.push({ text: s.name, displayText: `${s.name}  ·  ${s.label || ''}` });
  }
  if (!list.length) return;
  const from = { line: cur.line, ch: token.start };
  const to = { line: cur.line, ch: token.end };
  cm.showHint({
    hint: () => ({ list, from, to }),
    completeSingle: false,
  });
}

// ─── Right-click: go-to-definition / who-calls ───────────────────────────────
function _wordAt(cm, pos) {
  const token = cm.getTokenAt(pos);
  let w = (token.string || '').trim();
  if (!/^[A-Za-z_]\w*$/.test(w)) {
    // fall back to a word-range probe (handles punctuation tokens)
    const wr = cm.findWordAt(pos);
    w = cm.getRange(wr.anchor, wr.head).trim();
  }
  return /^[A-Za-z_]\w*$/.test(w) ? w : '';
}

function _codeIndexContextMenu(cm, e, tab) {
  const pos = cm.coordsChar({ left: e.clientX, top: e.clientY });
  const word = _wordAt(cm, pos);
  if (!word) return;   // not on a symbol → let the native menu show
  e.preventDefault();
  _codeIndexCloseMenu();
  const menu = document.createElement('div');
  menu.id = 'code-ctx-menu';
  menu.className = 'code-ctx-menu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  menu.innerHTML = `
    <div class="code-ctx-head">${esc(word)}</div>
    <div class="code-ctx-item" onclick="codeGotoDefinition('${esc(word)}')">Gehe zu Definition</div>
    <div class="code-ctx-item" onclick="codeWhoCalls('${esc(word)}')">Wer ruft das auf?</div>`;
  document.body.appendChild(menu);
  // keep the menu on-screen
  const r = menu.getBoundingClientRect();
  if (r.right > window.innerWidth) menu.style.left = (window.innerWidth - r.width - 8) + 'px';
  if (r.bottom > window.innerHeight) menu.style.top = (window.innerHeight - r.height - 8) + 'px';
  setTimeout(() => document.addEventListener('mousedown', _codeIndexCloseMenu, { once: true }), 0);
}

function _codeIndexCloseMenu() {
  const m = document.getElementById('code-ctx-menu');
  if (m) m.remove();
}

async function codeGotoDefinition(word) {
  _codeIndexCloseMenu();
  const d = await _codeIndexFetch(`def=${encodeURIComponent(word)}`);
  if (!d || d.error || !d.file) {
    // fall back to BM25 search → first hit
    const s = await _codeIndexFetch(`q=${encodeURIComponent(word)}&limit=5`);
    const hit = s && s.symbols && s.symbols.find(x => x.name === word) || (s && s.symbols && s.symbols[0]);
    if (hit) { _terminalJumpTo(_codeIndexAbs(hit.file), hit.line); return; }
    if (typeof showToast === 'function') showToast('Keine Definition gefunden');
    return;
  }
  // ?def returns an ABSOLUTE file_path (from get_code_snippet)
  _terminalJumpTo(d.file, d.start_line);
}

async function codeWhoCalls(word) {
  _codeIndexCloseMenu();
  const d = await _codeIndexFetch(`callers=${encodeURIComponent(word)}`);
  const callers = (d && d.callers) || [];
  _codeIndexShowCallers(word, callers);
}

// Reuse the small modal shell the code-index status uses (_codeIndexShowModal),
// falling back to a built-in overlay if it isn't present.
function _codeIndexShowCallers(word, callers) {
  const rows = callers.length
    ? callers.map(c => {
        const qn = esc(c.qualified_name || c.name || '');
        const nm = esc(c.name || '');
        return `<div class="code-callers-row" onclick="codeGotoDefinition('${esc(c.name || '')}')"
                     title="${qn}"><span class="cp-name">${nm}</span>
                <span class="cp-loc">${esc(c.qualified_name || '')}</span></div>`;
      }).join('')
    : '<div class="code-palette-hint">Keine Aufrufer gefunden (oder Symbol wird nur extern aufgerufen).</div>';
  const html = `<div class="code-callers"><div class="code-callers-head">Aufrufer von „${esc(word)}"</div>${rows}</div>`;
  _codeIndexOverlay(html);
}

function _codeIndexOverlay(innerHtml) {
  const ov = document.createElement('div');
  ov.className = 'code-palette-overlay';
  ov.innerHTML = `<div class="code-palette" onclick="event.stopPropagation()">${innerHtml}</div>`;
  ov.addEventListener('click', () => ov.remove());
  document.body.appendChild(ov);
}

// ─── Hover: signature + docstring + caller count ─────────────────────────────
let _codeHoverTimer = null;
let _codeHoverTip = null;

function _codeIndexAttachHover(tab) {
  const wrap = tab.el.querySelector('.editor-cm');
  if (!wrap) return;
  wrap.addEventListener('mousemove', (e) => {
    clearTimeout(_codeHoverTimer);
    _codeHoverTimer = setTimeout(() => _codeIndexHover(tab, e), 450);
  });
  wrap.addEventListener('mouseleave', _codeIndexHideHover);
}

async function _codeIndexHover(tab, e) {
  if (!tab.cm) return;
  const pos = tab.cm.coordsChar({ left: e.clientX, top: e.clientY });
  const word = _wordAt(tab.cm, pos);
  if (!word) { _codeIndexHideHover(); return; }
  const d = await _codeIndexFetch(`def=${encodeURIComponent(word)}`);
  if (!d || d.error || !d.name) { _codeIndexHideHover(); return; }
  _codeIndexHideHover();
  const sig = d.signature ? esc(d.name + d.signature) : esc(d.name);
  const doc = d.docstring ? `<div class="code-hover-doc">${esc(String(d.docstring).replace(/^["']+|["']+$/g, '').slice(0, 280))}</div>` : '';
  const meta = `<div class="code-hover-meta">${esc(d.label || '')}`
    + (typeof d.callers === 'number' ? ` · ${d.callers} Aufrufer` : '')
    + (typeof d.callees === 'number' ? ` · ${d.callees} Aufrufe` : '') + '</div>';
  const tip = document.createElement('div');
  tip.className = 'code-hover-tip';
  tip.innerHTML = `<div class="code-hover-sig">${sig}</div>${meta}${doc}`;
  tip.style.left = Math.min(e.clientX + 12, window.innerWidth - 360) + 'px';
  tip.style.top = (e.clientY + 16) + 'px';
  document.body.appendChild(tip);
  _codeHoverTip = tip;
}

function _codeIndexHideHover() {
  clearTimeout(_codeHoverTimer);
  if (_codeHoverTip) { _codeHoverTip.remove(); _codeHoverTip = null; }
}

// Global Cmd/Ctrl-P → symbol palette, but only in code-mode (and not while a
// text input / the palette itself has focus, so it doesn't hijack normal typing).
document.addEventListener('keydown', (e) => {
  const isP = (e.key === 'p' || e.key === 'P');
  if (!isP || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
  if (typeof terminalAvailable === 'function' && !terminalAvailable()) return;
  const t = e.target;
  if (t && (t.id === 'code-palette-input')) return;
  // allow it to override the browser print dialog in code-mode
  e.preventDefault();
  codeSymbolPalette();
});

// ─── Cypher search bar (power-user) ──────────────────────────────────────────
// Run read-only Cypher over the code index and show {columns, rows}. cbm honours
// explicit property/aggregate projections (RETURN n.name, n.complexity); a bare
// `RETURN n` collapses to the node name only — the examples reflect that.
const _CYPHER_EXAMPLES = [
  { label: 'Komplexeste Methoden', q: "MATCH (n:Method) WHERE n.complexity > 5 RETURN n.name, n.complexity, n.file_path ORDER BY n.complexity DESC LIMIT 20" },
  { label: 'Alle Klassen + Datei', q: "MATCH (n:Class) RETURN n.name, n.file_path" },
  { label: 'Methoden je Datei (Anzahl)', q: "MATCH (n:Method) RETURN n.file_path, count(n) ORDER BY count(n) DESC" },
  { label: 'Funktionen ohne Tests', q: "MATCH (n:Method) WHERE NOT (n)-[:TESTED_BY]->() RETURN n.name, n.file_path LIMIT 30" },
];

function codeCypherBar() {
  if (document.getElementById('code-cypher')) return;
  const ov = document.createElement('div');
  ov.id = 'code-cypher';
  ov.className = 'code-palette-overlay';
  const exHtml = _CYPHER_EXAMPLES.map((e, i) =>
    `<button class="code-cypher-ex" onclick="_codeCypherExample(${i})">${esc(e.label)}</button>`).join('');
  ov.innerHTML = `
    <div class="code-palette code-cypher-box" onclick="event.stopPropagation()">
      <div class="code-cypher-head">Cypher-Abfrage über den Code-Index
        <span class="code-cypher-hint">${_isMac() ? '⌘' : 'Strg'}+Enter zum Ausführen · nur lesend</span></div>
      <textarea id="code-cypher-input" spellcheck="false" autocomplete="off"
        placeholder="MATCH (n:Method) WHERE n.complexity > 5 RETURN n.name, n.complexity ORDER BY n.complexity DESC LIMIT 20"></textarea>
      <div class="code-cypher-examples">${exHtml}</div>
      <div class="code-cypher-actions">
        <button class="btn-primary" onclick="_codeCypherRun()">Ausführen</button>
        <span id="code-cypher-status" class="code-cypher-status"></span>
      </div>
      <div id="code-cypher-results" class="code-cypher-results"></div>
    </div>`;
  ov.addEventListener('click', () => ov.remove());
  document.body.appendChild(ov);
  const ta = document.getElementById('code-cypher-input');
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { ov.remove(); return; }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); _codeCypherRun(); }
  });
  ta.focus();
}

function _codeCypherExample(i) {
  const e = _CYPHER_EXAMPLES[i];
  const ta = document.getElementById('code-cypher-input');
  if (e && ta) { ta.value = e.q; ta.focus(); }
}

async function _codeCypherRun() {
  const ta = document.getElementById('code-cypher-input');
  const status = document.getElementById('code-cypher-status');
  const box = document.getElementById('code-cypher-results');
  if (!ta || !box) return;
  const q = ta.value.trim();
  if (!q) { return; }
  if (status) status.textContent = 'Läuft …';
  box.innerHTML = '';
  const d = await _codeIndexFetch(`cypher=${encodeURIComponent(q)}`);
  if (!d || d.error) {
    if (status) status.textContent = '';
    box.innerHTML = `<div class="code-palette-hint code-cypher-err">${esc((d && d.error) || 'Abfrage fehlgeschlagen')}</div>`;
    return;
  }
  const cols = d.columns || [];
  const rows = d.rows || [];
  if (status) status.textContent = `${rows.length} Zeile${rows.length === 1 ? '' : 'n'}`;
  if (!rows.length) { box.innerHTML = '<div class="code-palette-hint">Keine Treffer.</div>'; return; }
  // a cell that looks like a repo-relative source path becomes a jump link
  const head = cols.map(c => `<th>${esc(String(c))}</th>`).join('');
  const body = rows.map(r => {
    const cells = (Array.isArray(r) ? r : [r]).map(v => {
      const s = v === null || v === undefined ? '' : String(v);
      if (/\.[a-z]{1,4}$/i.test(s) && /[\/\\]/.test(s) && !/\s/.test(s)) {
        return `<td><a class="code-cypher-link" onclick="_codeCypherJump('${esc(s)}')">${esc(s)}</a></td>`;
      }
      return `<td>${esc(s)}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  box.innerHTML = `<table class="code-cypher-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// Click a path cell → open that file in the editor (no line info in a generic
// Cypher result, so just open at the top).
function _codeCypherJump(relPath) {
  const ov = document.getElementById('code-cypher');
  if (ov) ov.remove();
  _terminalJumpTo(_codeIndexAbs(relPath), 1);
}
