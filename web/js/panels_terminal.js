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
  // Layout state (persisted PER-PROJECT in bottom_workspace, not per-chat):
  treeVisible: true, // file-tree column shown?
  treeWidth: 240,    // file-tree column width (px)
  singleEditor: false, // single-editor mode: tree click replaces the editor tab
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
    refreshTerminalTree();
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
  // restore layout state (per-project) before painting tabs/tree
  if (ws) {
    if (typeof ws.tree_visible === 'boolean') _term.treeVisible = ws.tree_visible;
    if (ws.tree_width >= 120) _term.treeWidth = Math.min(ws.tree_width, 700);
    if (typeof ws.single_editor === 'boolean') _term.singleEditor = ws.single_editor;
  }
  _terminalApplyLayout();
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
      tree_visible: _term.treeVisible,
      tree_width: _term.treeWidth,
      single_editor: _term.singleEditor,
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

// Build an xterm theme from the app's CSS variables so the terminal follows the
// light/dark mode of the rest of the UI (was hardcoded #1e1e1e → black in light
// mode). Reads the resolved --bg-000 / --text-100 / --accent-brand off :root.
function _terminalXtermTheme() {
  let bg = '#1e1e1e', fg = '#eaeaec', accent = '#3b82f6';
  try {
    const cs = getComputedStyle(document.documentElement);
    bg = (cs.getPropertyValue('--bg-000') || bg).trim() || bg;
    fg = (cs.getPropertyValue('--text-100') || fg).trim() || fg;
    accent = (cs.getPropertyValue('--accent-brand') || accent).trim() || accent;
  } catch (_) { /* fall back to dark defaults */ }
  return { background: bg, foreground: fg, cursor: accent,
           selectionBackground: accent + '55' };
}

// Re-apply the theme to all live terminals (call after a light/dark switch).
function terminalRetheme() {
  const theme = _terminalXtermTheme();
  for (const t of _term.tabs) {
    if (t.kind === 'terminal' && t.term) { try { t.term.options.theme = theme; } catch (_) {} }
  }
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
    theme: _terminalXtermTheme(),
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
    setTimeout(() => {
      try {
        if (!tab.cm) return;
        tab.cm.refresh();
        // restore the saved caret position, then refocus the editor
        const cur = _terminalLoadCursor(tab.path);
        if (cur) tab.cm.setCursor(cur);
        tab.cm.focus();
      } catch (_) {}
    }, 30);
    if (typeof repaintTerminalTree === 'function') repaintTerminalTree();  // highlight in tree
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
  const wasEditor = tab.kind === 'editor';
  _term.tabs = _term.tabs.filter(t => t.id !== id);
  if (_term.active === id) {
    if (_term.tabs.length) _terminalActivate(_term.tabs[0].id);
    else terminalTogglePanel(false);
  }
  _terminalRenderTabs();
  if (wasEditor && typeof repaintTerminalTree === 'function') repaintTerminalTree();
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
    let label, cls = '';
    if (t.kind === 'editor') {
      // '*' = unsaved changes; italic (.ed-editing) when the tab is in edit mode.
      label = esc(t.name) + (t.dirty ? '*' : '');
      if (t.mode === 'raw') cls = ' ed-editing';
    } else {
      termN += 1; label = 'Terminal ' + termN;
    }
    return `<div class="terminal-tab${active}${cls}" title="${esc(t.path || label)}" onclick="_terminalActivate('${t.id}')" oncontextmenu="_terminalTabMenu('${t.id}', event)">
      <span class="tt-name">${label}</span>
      <span class="terminal-tab-close" onclick="terminalCloseTab('${t.id}', event)">✕</span>
    </div>`;
  }).join('');
}

// ── Editor tabs (CodeMirror 5) ───────────────────────────────────────────────
// Per-file cursor persistence (UI-only, localStorage, keyed by project+path) so
// reopening a file restores the caret line/col. Debounced on cursor activity.
function _terminalCursorKey(absPath) {
  return `brain.edcursor.${_term.project || 'p'}:${absPath}`;
}
let _terminalCursorTimer = null;
function _terminalSaveCursor(tab) {
  if (!tab || !tab.cm) return;
  clearTimeout(_terminalCursorTimer);
  _terminalCursorTimer = setTimeout(() => {
    try {
      const c = tab.cm.getCursor();
      localStorage.setItem(_terminalCursorKey(tab.path), JSON.stringify({ line: c.line, ch: c.ch }));
    } catch (_) {}
  }, 300);
}
function _terminalLoadCursor(absPath) {
  try {
    const v = JSON.parse(localStorage.getItem(_terminalCursorKey(absPath)) || 'null');
    return (v && typeof v.line === 'number') ? v : null;
  } catch (_) { return null; }
}

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
  // Single-editor mode: close the current (or any) open editor tab first so at
  // most one editor stays open — the new file replaces it. A dirty editor still
  // gets the unsaved-changes confirm via terminalCloseTab.
  if (_term.singleEditor) {
    const cur = _term.tabs.find(t => t.kind === 'editor');  // at most one open
    if (cur) {
      const before = _term.tabs.length;
      await terminalCloseTab(cur.id);
      // user cancelled the dirty-close confirm → abort opening the new file
      if (_term.tabs.length === before) return;
    }
  }
  const name = absPath.split('/').pop();
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  const id = 'edit-' + absPath;
  const el = document.createElement('div');
  el.className = 'terminal-editor';
  el.style.display = 'none';
  // Toolbar buttons are SVG-only (no text labels, per the UI icon rule). The two
  // mode buttons (Ansicht/Bearbeiten) toggle read-only vs editable — the editor
  // LOOKS identical in both (CodeMirror, same line numbers + colouring); only a
  // cursor distinguishes edit mode.
  el.innerHTML = `
    <div class="editor-toolbar">
      <button class="ed-iconbtn ed-mode active" data-mode="render" onclick="terminalEditorMode('${id}','render')" title="Ansicht (nur lesen)" aria-label="Ansicht">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>
      </button>
      <button class="ed-iconbtn ed-mode" data-mode="raw" onclick="terminalEditorMode('${id}','raw')" title="Bearbeiten" aria-label="Bearbeiten">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
      </button>
      <span class="ed-sep"></span>
      <button class="ed-iconbtn" onclick="codeSymbolPalette()" title="Symbol suchen (${_isMac()?'⌘':'Strg'}+P)" aria-label="Symbole suchen">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
      </button>
      <button class="ed-iconbtn" onclick="codeCypherBar()" title="Code-Index per Cypher abfragen (Power-User)" aria-label="Cypher-Abfrage">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
      </button>
      <span style="flex:1"></span>
      <button class="ed-iconbtn ed-save" onclick="terminalEditorSave('${id}')" title="Speichern" aria-label="Speichern" disabled>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
      </button>
      <button class="ed-iconbtn" onclick="terminalEditorDownload('${id}')" title="Herunterladen" aria-label="Herunterladen">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      </button>
    </div>
    <div class="editor-render" style="display:none"></div>
    <div class="editor-cm"></div>
    <div class="editor-status"><span class="ed-stat-name"></span><span style="flex:1"></span><span class="ed-stat-meta"></span></div>`;
  document.getElementById('terminal-body').appendChild(el);
  const tab = { id, kind: 'editor', path: absPath, name, ext, el, cm: null,
                raw: '', mode: 'render', dirty: false, loaded: false,
                size: 0, mtime: 0 };
  _term.tabs.push(tab);
  _terminalRenderTabs();
  _terminalActivate(id);
  // load content
  try {
    const d = await API.get(`/v1/files/preview?path=${encodeURIComponent(absPath)}&lines=100000`);
    if (d.error) { el.querySelector('.editor-render').style.display = 'block'; el.querySelector('.editor-cm').style.display = 'none'; el.querySelector('.editor-render').innerHTML = `<div class="pt-empty">${esc(d.error)}</div>`; return; }
    tab.size = d.size || 0;
    tab.mtime = d.mtime || 0;
    // SVG is text we can edit + render — the preview endpoint reports it as an
    // image, so fetch the raw XML and treat it as a renderable text file.
    if (d.type === 'image' && tab.ext === 'svg') {
      try {
        const r = await fetch(`${BASE_URL}/v1/files/download?path=${encodeURIComponent(absPath)}`,
          { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') } });
        tab.raw = await r.text();
      } catch (_) { tab.raw = ''; }
      tab.loaded = true;
      _terminalEditorPaint(tab);
      _terminalEditorStats(tab);
      return;
    }
    if (d.type === 'image') {
      _terminalEditorShowImage(tab);
      // images: no edit
      el.querySelector('.editor-cm').style.display = 'none';
      el.querySelector('.ed-mode[data-mode="raw"]').style.display = 'none';
      el.querySelector('.ed-save').style.display = 'none';
      _terminalEditorStats(tab);
      return;
    }
    tab.raw = d.content || '';
    tab.truncated = !!d.truncated;
    tab.loaded = true;
    _terminalEditorPaint(tab);
    _terminalEditorStats(tab);
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
  _terminalRenderTabs();  // reflect edit-mode italic in the tab label
}

// BOTH modes use the SAME CodeMirror instance (same line numbers + syntax
// colouring); 'render' = read-only (no cursor), 'raw' = editable. There is NO
// separate highlight.js/markdown view — per the user, the only visible
// difference between view and edit is the cursor — EXCEPT renderable files
// (html/md/svg), where read-only "Ansicht" shows the RENDERED output and edit
// shows the CodeMirror source.
const _ED_RENDERABLE = { html: 1, htm: 1, md: 1, markdown: 1, svg: 1 };
function _terminalIsRenderable(ext) { return !!_ED_RENDERABLE[(ext || '').toLowerCase()]; }

// Render a renderable file's content into the .editor-render pane (Ansicht mode).
function _terminalEditorRender(tab) {
  const renderEl = tab.el.querySelector('.editor-render');
  if (!renderEl) return;
  const txt = (tab.cm && tab.dirty) ? tab.cm.getValue() : tab.raw;
  const ext = (tab.ext || '').toLowerCase();
  if (ext === 'md' || ext === 'markdown') {
    renderEl.innerHTML = (typeof renderMarkdown === 'function')
      ? `<div class="ref-inline-md msg-content" style="padding:12px">${renderMarkdown(txt)}</div>`
      : `<pre class="editor-pre">${esc(txt)}</pre>`;
    renderEl.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch (_) {} });
  } else {
    // html/htm/svg → render the markup in a sandboxed iframe (srcdoc). No
    // allow-scripts/allow-same-origin → a preview can't reach the app or run JS.
    renderEl.innerHTML = '';
    const frame = document.createElement('iframe');
    frame.className = 'editor-render-frame';
    frame.setAttribute('sandbox', '');
    frame.srcdoc = txt;
    renderEl.appendChild(frame);
  }
}

function _terminalEditorPaint(tab) {
  const cmEl = tab.el.querySelector('.editor-cm');
  const renderEl = tab.el.querySelector('.editor-render');
  // Renderable file in Ansicht mode → show the rendered output, hide CM.
  if (tab.mode === 'render' && _terminalIsRenderable(tab.ext)) {
    cmEl.style.display = 'none';
    if (renderEl) renderEl.style.display = 'block';
    _terminalEditorRender(tab);
    return;
  }
  if (renderEl) renderEl.style.display = 'none';
  cmEl.style.display = 'block';
  if (!tab.cm) {
    tab.cm = CodeMirror(cmEl, {
      value: tab.raw, mode: _cmModeFor(tab.ext), lineNumbers: true,
      lineWrapping: false, indentUnit: 4,
      readOnly: tab.mode === 'raw' ? false : 'nocursor',
      extraKeys: {
        // index-fed autocomplete (project symbols from the cbm index).
        // Ctrl-Space only — NOT Cmd-Space (that's macOS Spotlight, the OS
        // grabs it before the browser sees it).
        'Ctrl-Space': (cm) => codeIndexComplete(cm),
      },
    });
    // CSS drives the height (.editor-cm .CodeMirror { height:100% }); a
    // refresh() after the element is laid out lets CM measure + show scrollbars.
    // (setSize('100%','100%') broke CM's scroll measurement → no scrollbars.)
    tab.cm.on('change', () => {
      const wasDirty = tab.dirty;
      tab.dirty = true;
      const sv = tab.el.querySelector('.ed-save'); if (sv) sv.disabled = false;
      _terminalRenderTabs();
      // reflect the unsaved-'*' in the file tree (only on the false→true flip)
      if (!wasDirty && typeof repaintTerminalTree === 'function') repaintTerminalTree();
    });
    // right-click a symbol → go-to-definition / who-calls menu
    tab.cm.on('contextmenu', (cm, e) => _codeIndexContextMenu(cm, e, tab));
    // hover a symbol → signature + docstring + caller count tooltip
    _codeIndexAttachHover(tab);
    // persist the cursor (line/ch) per file so reopening restores the position
    tab.cm.on('cursorActivity', () => _terminalSaveCursor(tab));
    // restore the saved cursor on first paint (after the doc is in the CM)
    const cur = _terminalLoadCursor(tab.path);
    if (cur) { try { tab.cm.setCursor(cur); } catch (_) {} }
  } else {
    // keep CM in sync if raw changed via save; flip editability for the mode
    if (tab.cm.getValue() !== tab.raw && !tab.dirty) tab.cm.setValue(tab.raw);
    tab.cm.setOption('readOnly', tab.mode === 'raw' ? false : 'nocursor');
  }
  // edit mode gets focus (so the cursor shows); view mode does not
  setTimeout(() => { try { tab.cm.refresh(); if (tab.mode === 'raw') tab.cm.focus(); } catch (_) {} }, 20);
}

// Editor status line: file name + size + last-modified + (for text) line count
// and a truncation hint. Reuses _fmtBytes (panels_workdir.js global).
function _terminalEditorStats(tab) {
  if (!tab || !tab.el) return;
  const nameEl = tab.el.querySelector('.ed-stat-name');
  const metaEl = tab.el.querySelector('.ed-stat-meta');
  if (nameEl) nameEl.textContent = tab.name + (tab.truncated ? ' · (gekürzt angezeigt)' : '');
  const bits = [];
  if (tab.size) bits.push(typeof _fmtBytes === 'function' ? _fmtBytes(tab.size) : tab.size + ' B');
  if (tab.cm) { try { bits.push(tab.cm.lineCount() + ' Zeilen'); } catch (_) {} }
  if (tab.mtime) bits.push('geändert ' + new Date(tab.mtime * 1000).toLocaleString());
  if (metaEl) metaEl.textContent = bits.join(' · ');
}

async function _terminalEditorShowImage(tab) {
  try {
    const resp = await fetch(`${BASE_URL}/v1/files/download?path=${encodeURIComponent(tab.path)}`,
      { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') } });
    const blob = await resp.blob();
    const renderEl = tab.el.querySelector('.editor-render');
    renderEl.style.display = 'block';
    renderEl.innerHTML = `<img src="${URL.createObjectURL(blob)}" style="max-width:100%;padding:10px"/>`;
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
    // refresh stats: size from the saved content, mtime = now (just written)
    tab.size = new Blob([content]).size;
    tab.mtime = Math.floor(Date.now() / 1000);
    tab.truncated = false;
    _terminalEditorStats(tab);
    const sv = tab.el.querySelector('.ed-save'); if (sv) sv.disabled = true;
    _terminalRenderTabs();
    // a save clears the unsaved-'*' and may flip the file's git state → reload
    if (typeof refreshTerminalTree === 'function') refreshTerminalTree();
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
    if (typeof refreshTerminalTree === 'function') refreshTerminalTree();
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

// ── File-tree column (left of the terminal/editor) ───────────────────────────
// Apply the persisted layout state (visibility, width, single-editor toggle) to
// the DOM. Called on open and after each toggle.
function _terminalApplyLayout() {
  const panel = document.getElementById('terminal-panel');
  const col = document.getElementById('terminal-tree-col');
  if (panel) panel.classList.toggle('tree-hidden', !_term.treeVisible);
  if (col) col.style.flexBasis = Math.max(120, _term.treeWidth || 240) + 'px';
  const se = document.getElementById('terminal-single-editor');
  if (se) se.classList.toggle('active', !!_term.singleEditor);
  const show = document.getElementById('terminal-tree-show');
  if (show) show.classList.toggle('active', !!_term.treeVisible);
}

function terminalToggleTree() {
  _term.treeVisible = !_term.treeVisible;
  _terminalApplyLayout();
  if (_term.treeVisible) refreshTerminalTree();
  _terminalPersist();
  setTimeout(_terminalOnResize, 30);
}

function terminalToggleSingleEditor() {
  _term.singleEditor = !_term.singleEditor;
  _terminalApplyLayout();
  if (typeof showToast === 'function') {
    showToast(_term.singleEditor ? 'Ein-Editor-Modus: an' : 'Ein-Editor-Modus: aus');
  }
  _terminalPersist();
}

function _terminalInitTreeResize() {
  const handle = document.getElementById('terminal-tree-resize');
  const col = document.getElementById('terminal-tree-col');
  if (!handle || !col) return;
  let startX = 0, startW = 0, dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true; startX = e.clientX; startW = col.offsetWidth;
    document.body.style.userSelect = 'none'; e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const w = Math.max(120, Math.min(700, startW + (e.clientX - startX)));
    col.style.flexBasis = w + 'px';
    _term.treeWidth = w;
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; document.body.style.userSelect = '';
    _terminalPersist();
    setTimeout(_terminalOnResize, 10);
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
