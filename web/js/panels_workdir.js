// panels_workdir.js — Code-Mode working-directory file tree.
//
// The tree lives in the LEFT column of the bottom panel (#terminal-tree), next
// to the terminal/editor tabs (panels_terminal.js), styled to match the editor
// (dark fg/bg + mono font). Each file row shows three statuses:
//   • git working-tree state — colours the name (M amber · ?/A green · D/U red ·
//     R blue) plus a one-letter code badge,
//   • unsaved-edit marker — a '*' when the file is open in the editor with
//     unsaved changes (read off _term.tabs),
//   • MemPalace/index sync dot — the same _ptDot the project source tree uses
//     (code-mode files carry no ingest state today, so this is usually
//     "pending"; kept for parity + future code-index sync surfacing).
// Clicking a file opens it in the bottom editor (terminalOpenFile).
//
// Code-mode-detection helpers (_workdirActiveProject / _workdirIsCodeChat) are
// kept here — panels_terminal.js + the right-panel/sessions wiring depend on
// them. Globals only, no modules (fixed load order; after panels_artifacts.js).

// Lazy cache of the active chat's project code-mode info, keyed by project name.
// Avoids re-fetching the project on every tab switch / turn.
window._workdirProjectCache = window._workdirProjectCache || {};

// Resolve {code_mode, working_dir, agent, name} for the active chat's project,
// or null when there is no (code-mode) project. Cached per name.
async function _workdirActiveProject() {
  const name = state.currentProject;
  if (!name) return null;
  const agent = state.activeAgentId || state._projectDetailAgent || 'main';
  // Reuse the already-loaded project detail if it matches (project-detail view).
  if (state._projectDetail && state._projectDetailName === name && state._projectDetail.code_mode != null) {
    const d = state._projectDetail;
    return d.code_mode ? { code_mode: true, working_dir: d.working_dir || '', agent, name } : null;
  }
  const cached = window._workdirProjectCache[name];
  if (cached) return cached.code_mode ? cached : null;
  try {
    const p = await API.getProject(agent, name);
    const info = { code_mode: !!p.code_mode, working_dir: p.working_dir || '', agent, name };
    window._workdirProjectCache[name] = info;
    return info.code_mode ? info : null;
  } catch (e) {
    return null;
  }
}

// Cheap synchronous best-effort check used by tab-visibility (uses the cache /
// loaded detail; triggers a lazy fetch that flips visibility once it resolves).
function _workdirIsCodeChat() {
  const name = state.currentProject;
  if (!name) return false;
  if (state._projectDetail && state._projectDetailName === name && state._projectDetail.code_mode) return true;
  const c = window._workdirProjectCache[name];
  if (c) return !!c.code_mode;
  // Not known yet → kick a fetch that will refresh the terminal toggle on resolve.
  _workdirActiveProject().then(() => { try { if (typeof terminalRefreshToggle === 'function') terminalRefreshToggle(); } catch (_) {} });
  return false;
}

// Hide the right-panel tabs that make no sense in a code-mode chat. Artefakte
// (artifacts) + Web-Adressen (websuche) are MemPalace/normal-chat surfaces that
// don't apply to code-mode projects → hide them; Anhänge/Referenzen/Aktivität
// stay. Restores a normal-chat tab set otherwise. Called from the right-panel
// open/refresh. (The working-directory tree itself now lives in the bottom
// panel, not a right-panel tab.)
function updateCodeModeTabs() {
  const isCode = _workdirIsCodeChat();
  ['artifacts', 'websuche'].forEach(t => {
    const btn = document.querySelector(`.right-panel-tab[data-tab="${t}"]`);
    if (btn) btn.style.display = isCode ? 'none' : '';
  });
  // If the active tab just got hidden, fall back to a sensible visible one.
  if (isCode && ['artifacts', 'websuche'].includes(state.rightPanelTab)) {
    if (typeof switchRightTab === 'function') switchRightTab('attachments');
  }
}

// Human-readable byte size (shared helper; used by the editor status line too).
function _fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

// ── Tree render (into the bottom panel's left column) ─────────────────────────
// git code → human tooltip (the file ICON colour comes from data-git CSS).
const _WD_GIT_TIP = {
  M: 'geändert', '?': 'unversioniert (neu)', A: 'hinzugefügt',
  D: 'gelöscht', R: 'umbenannt', U: 'Konflikt',
};

// Strip the working-dir prefix from an absolute path → repo-relative key, to
// match state._codeIndexFiles (keyed repo-relative). Chat-safe: uses _term.wd
// (set when the panel opens) and falls back to the project-detail working_dir.
function _wdRelToWorkingDir(absPath) {
  if (!absPath) return '';
  const wd = ((typeof _term !== 'undefined' && _term.wd)
    || (state._projectDetail && state._projectDetail.working_dir) || '');
  if (wd && absPath.indexOf(wd) === 0) return absPath.slice(wd.length).replace(/^\/+/, '');
  return absPath.split('/').pop();
}

// Is `absPath` open in the editor with unsaved changes? (read off _term.tabs)
function _wdIsDirty(absPath) {
  const tabs = (typeof _term !== 'undefined' && _term.tabs) || [];
  const t = tabs.find(x => x.kind === 'editor' && x.path === absPath);
  return !!(t && t.dirty);
}
// Is `absPath` the currently-open editor file? (for selection highlight)
function _wdIsActive(absPath) {
  const tabs = (typeof _term !== 'undefined' && _term.tabs) || [];
  const t = tabs.find(x => x.kind === 'editor' && x.path === absPath);
  return !!(t && t.id === _term.active);
}

function _wdRenderTree(nodes) {
  if (!nodes || !nodes.length) return '<div class="pt-empty">Leer.</div>';
  return nodes.map(n => {
    if (n.type === 'dir') {
      const open = _wdDirExpanded(n.path);
      const dp = esc(n.path);
      return `<div class="pt-branch pt-realdir">
        <div class="pt-row pt-realrow" data-dir="${dp}" draggable="true"
          onclick="wdToggleDir(this, '${dp}')"
          oncontextmenu="wdDirMenu(event, '${dp}')"
          ondragstart="wdDragStart(event, '${dp}')" ondragend="wdDragEnd(event)"
          ondragover="wdDragOver(event, this)" ondragleave="wdDragLeave(event, this)"
          ondrop="wdDrop(event, '${dp}')">
          ${_ptCaret(open)}
          <span class="pt-icon">${_PT_ICON.folders}</span>
          <span class="pt-label">${esc(n.name)}</span>
        </div>
        <div class="pt-children" style="display:${open ? 'block' : 'none'}">${_wdRenderTree(n.children || [])}</div>
      </div>`;
    }
    const git = n.git || '';
    const dirty = _wdIsDirty(n.path);
    const sel = _wdIsActive(n.path) ? ' pt-selected' : '';
    // Sync dot: use the CODE-INDEX per-file state (indexed/stale/not_indexed/
    // not_source), the SAME source as the project-view tree — so the bullet
    // colour is identical in chat and project view (was wrongly using the
    // folder-tree mem state, which is always 'pending' → orange in chat). Falls
    // back to no dot when the code-index status isn't loaded yet.
    const ci = state._codeIndexFiles && state._codeIndexFiles[_wdRelToWorkingDir(n.path)];
    // Bullet (right) = SYNC state (code-index: indexed/stale/not_indexed/…).
    const dot = ci ? _ptDot(ci.state) : '';
    // File ICON (left of the name) is COLOURED by GIT state (via data-git CSS).
    // '*' appended to the name = open in the editor with unsaved changes.
    const star = dirty ? '<span class="tt-dirty" title="Ungespeicherte Änderungen">*</span>' : '';
    const gitTip = git ? ` · Git: ${esc(_WD_GIT_TIP[git] || git)}` : '';
    const fp = esc(n.path || '');
    return `<div class="pt-row pt-realfile${sel}" data-path="${fp}" data-git="${esc(git)}" draggable="true"
         title="${esc(n.path || n.name)}${gitTip}" onclick="wdOpenFile('${fp}')"
         oncontextmenu="wdFileMenu(event, '${fp}')"
         ondragstart="wdDragStart(event, '${fp}')" ondragend="wdDragEnd(event)">
      <span class="pt-icon pt-fileicon">${_PT_ICON.file}</span>
      <span class="pt-label">${esc(n.name)}${star}</span>
      ${dot ? `<span class="pt-dot-wrap">${dot}</span>` : ''}
    </div>`;
  }).join('');
}

// Per-project expand/collapse persistence (UI-only, localStorage). Reuses the
// project-tree key scheme so it survives reloads. Default: collapsed.
function _wdExpandKey() {
  const pid = (state._projectDetail && state._projectDetail.id) || state.currentProject || 'p';
  return `brain.wdtree.expanded.${pid}`;
}
function _wdLoadExpanded() {
  try { return JSON.parse(localStorage.getItem(_wdExpandKey()) || '{}') || {}; }
  catch (_) { return {}; }
}
function _wdDirExpanded(absPath) { return !!_wdLoadExpanded()[absPath]; }
function _wdSetExpanded(absPath, on) {
  const m = _wdLoadExpanded(); m[absPath] = !!on;
  try { localStorage.setItem(_wdExpandKey(), JSON.stringify(m)); } catch (_) {}
}

function wdToggleDir(rowEl, absPath) {
  const branch = rowEl.parentElement;
  const kids = branch.querySelector('.pt-children');
  const caret = rowEl.querySelector('.pt-caret');
  if (!kids) return;
  const show = kids.style.display === 'none';
  kids.style.display = show ? 'block' : 'none';
  if (caret) caret.classList.toggle('open', show);
  _wdSetExpanded(absPath, show);
}

// File types that aren't usefully editable/viewable inline → open in the host's
// external app (Word/Excel/PowerPoint/Acrobat/…) on click.
const _WD_EXTERNAL_EXT = new Set([
  'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'pdf',
  'odt', 'ods', 'odp', 'rtf', 'pages', 'numbers', 'key',
  'zip', 'mp4', 'mov', 'avi', 'mp3', 'wav', 'm4a',
]);
function _wdExt(p) { const n = (p || '').split('/').pop(); return n.includes('.') ? n.split('.').pop().toLowerCase() : ''; }

function wdOpenFile(absPath) {
  if (!absPath) return;
  // Office/PDF/media → external app; everything else → in-app editor.
  if (_WD_EXTERNAL_EXT.has(_wdExt(absPath))) { wdOpenExternal(absPath); return; }
  if (typeof terminalOpenFile === 'function') terminalOpenFile(absPath);
}

// Open a file in the host's default external application.
async function wdOpenExternal(absPath) {
  if (!absPath) return;
  try {
    const r = await fetch(`${BASE_URL}/v1/files/open-external`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: absPath }),
    });
    const d = await r.json();
    if (d.error) { if (typeof showToast === 'function') showToast(d.error, true); return; }
    if (typeof showToast === 'function') showToast('In externem Programm geöffnet');
  } catch (e) { if (typeof showToast === 'function') showToast('Öffnen fehlgeschlagen', true); }
}

// Build + show a context menu at the event position from [{label, fn}] items.
function _wdMenu(ev, items) {
  ev.preventDefault(); ev.stopPropagation();
  const old = document.getElementById('terminal-tab-menu');
  if (old) old.remove();
  const m = document.createElement('div');
  m.id = 'terminal-tab-menu';
  m.className = 'terminal-tab-menu';
  m.style.left = ev.clientX + 'px';
  m.style.top = ev.clientY + 'px';
  items.forEach((it, i) => {
    if (it.sep) { const s = document.createElement('div'); s.className = 'ttm-sep'; m.appendChild(s); return; }
    const d = document.createElement('div');
    d.textContent = it.label;
    if (it.danger) d.className = 'ttm-danger';
    d.onclick = () => { m.remove(); try { it.fn(); } catch (_) {} };
    m.appendChild(d);
  });
  document.body.appendChild(m);
  // keep the menu on-screen
  const r = m.getBoundingClientRect();
  if (r.right > window.innerWidth) m.style.left = (window.innerWidth - r.width - 4) + 'px';
  if (r.bottom > window.innerHeight) m.style.top = (window.innerHeight - r.height - 4) + 'px';
  const close = () => { const e = document.getElementById('terminal-tab-menu'); if (e) e.remove(); document.removeEventListener('click', close); };
  setTimeout(() => document.addEventListener('click', close), 0);
}

// Right-click a FILE row → open / rename / delete.
function wdFileMenu(ev, absPath) {
  _wdMenu(ev, [
    { label: 'Im Editor öffnen', fn: () => terminalOpenFile(absPath) },
    { label: 'In externem Programm öffnen', fn: () => wdOpenExternal(absPath) },
    { sep: true },
    { label: 'Umbenennen…', fn: () => wdRename(absPath) },
    { label: 'Löschen…', danger: true, fn: () => wdDelete(absPath) },
  ]);
}

// Right-click a FOLDER row → new file / new folder / rename / delete.
function wdDirMenu(ev, absPath) {
  _wdMenu(ev, [
    { label: 'Neue Datei…', fn: () => wdNewFileIn(absPath) },
    { label: 'Neuer Ordner…', fn: () => wdMkdir(absPath) },
    { sep: true },
    { label: 'Umbenennen…', fn: () => wdRename(absPath) },
    { label: 'Löschen…', danger: true, fn: () => wdDelete(absPath) },
  ]);
}

// ── File operations (rename/move · delete · mkdir · new file) ────────────────
async function _wdFileOp(endpoint, body, okMsg) {
  try {
    const r = await fetch(`${BASE_URL}/v1/files/${endpoint}`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.error) { if (typeof showToast === 'function') showToast(d.error, true); return null; }
    if (okMsg && typeof showToast === 'function') showToast(okMsg);
    if (typeof refreshTerminalTree === 'function') refreshTerminalTree();
    return d;
  } catch (e) { if (typeof showToast === 'function') showToast('Aktion fehlgeschlagen', true); return null; }
}

async function wdRename(absPath) {
  const cur = absPath.split('/').pop();
  const name = prompt('Neuer Name:', cur);
  if (!name || name === cur) return;
  if (/[/\\]/.test(name)) { if (typeof showToast === 'function') showToast('Name darf keinen Pfadtrenner enthalten', true); return; }
  const d = await _wdFileOp('rename', { path: absPath, to: name }, 'Umbenannt');
  if (d && d.path) _wdAfterMove(absPath, d.path);
}

async function wdDelete(absPath) {
  const nm = absPath.split('/').pop();
  if (!confirm(`„${nm}" in den Papierkorb verschieben?`)) return;
  const d = await _wdFileOp('delete', { path: absPath }, 'In den Papierkorb verschoben');
  if (d) _wdCloseTabFor(absPath);
}

async function wdMkdir(parentDir) {
  const name = prompt('Name des neuen Ordners:', 'neuer-ordner');
  if (!name) return;
  if (/[/\\]/.test(name)) { if (typeof showToast === 'function') showToast('Name darf keinen Pfadtrenner enthalten', true); return; }
  const target = parentDir.replace(/\/+$/, '') + '/' + name;
  await _wdFileOp('mkdir', { path: target }, 'Ordner erstellt');
  // expand the parent so the new folder is visible
  if (typeof _wdSetExpanded === 'function') _wdSetExpanded(parentDir, true);
}

// Create a new file inside a specific folder (folder-menu "Neue Datei").
async function wdNewFileIn(dir) {
  const name = prompt('Name der neuen Datei:', 'neu.txt');
  if (!name) return;
  if (/[/\\]/.test(name)) { if (typeof showToast === 'function') showToast('Name darf keinen Pfadtrenner enthalten', true); return; }
  const abs = dir.replace(/\/+$/, '') + '/' + name;
  const d = await _wdFileOp('save', { path: abs, content: '' }, 'Datei erstellt');
  if (d) { if (typeof _wdSetExpanded === 'function') _wdSetExpanded(dir, true); if (typeof terminalOpenFile === 'function') await terminalOpenFile(abs); }
}

// Header buttons: create at the working-dir ROOT.
function _wdRoot() {
  return (typeof _term !== 'undefined' && _term.wd) ||
    (state._projectDetail && state._projectDetail.working_dir) || '';
}
function wdNewFileRoot() { const r = _wdRoot(); if (r) wdNewFileIn(r); else if (typeof showToast === 'function') showToast('Kein Arbeitsverzeichnis', true); }
function wdNewFolderRoot() { const r = _wdRoot(); if (r) wdMkdir(r); else if (typeof showToast === 'function') showToast('Kein Arbeitsverzeichnis', true); }

// If a renamed/moved file is open in an editor tab, re-point it (simplest: close
// the stale tab so the user reopens from the tree at its new path).
function _wdAfterMove(oldPath, newPath) { _wdCloseTabFor(oldPath); }
function _wdCloseTabFor(absPath) {
  if (typeof _term === 'undefined' || !_term.tabs) return;
  const t = _term.tabs.find(x => x.kind === 'editor' && x.path === absPath);
  if (t && typeof terminalCloseTab === 'function') { try { terminalCloseTab(t.id); } catch (_) {} }
}

// ── Drag & drop: move a file/folder into a target folder ─────────────────────
let _wdDragPath = null;
function wdDragStart(ev, absPath) {
  _wdDragPath = absPath;
  ev.dataTransfer.effectAllowed = 'move';
  try { ev.dataTransfer.setData('text/plain', absPath); } catch (_) {}
  ev.stopPropagation();
}
function wdDragEnd(ev) {
  _wdDragPath = null;
  document.querySelectorAll('.pt-row.wd-drop').forEach(e => e.classList.remove('wd-drop'));
}
function wdDragOver(ev, rowEl) {
  if (!_wdDragPath) return;
  ev.preventDefault(); ev.stopPropagation();
  ev.dataTransfer.dropEffect = 'move';
  rowEl.classList.add('wd-drop');
}
function wdDragLeave(ev, rowEl) { rowEl.classList.remove('wd-drop'); }
async function wdDrop(ev, destDir) {
  ev.preventDefault(); ev.stopPropagation();
  document.querySelectorAll('.pt-row.wd-drop').forEach(e => e.classList.remove('wd-drop'));
  const src = _wdDragPath; _wdDragPath = null;
  if (!src || !destDir) return;
  // No-op if dropping into its own current folder, or onto itself / its subtree.
  const srcDir = src.replace(/\/[^/]+$/, '');
  if (srcDir === destDir.replace(/\/+$/, '')) return;
  if (destDir === src || destDir.indexOf(src.replace(/\/+$/, '') + '/') === 0) {
    if (typeof showToast === 'function') showToast('Ordner kann nicht in sich selbst verschoben werden', true);
    return;
  }
  const dest = destDir.replace(/\/+$/, '') + '/' + src.split('/').pop();
  const d = await _wdFileOp('rename', { path: src, to: dest }, 'Verschoben');
  if (d && d.path) { _wdAfterMove(src, d.path); if (typeof _wdSetExpanded === 'function') _wdSetExpanded(destDir, true); }
}

// Collect every directory path in the (cached) tree, recursively.
function _wdAllDirPaths(nodes, acc) {
  acc = acc || [];
  for (const n of (nodes || [])) {
    if (n.type === 'dir') { acc.push(n.path); _wdAllDirPaths(n.children || [], acc); }
  }
  return acc;
}

// Expand or collapse ALL directories at once; persist the new state (per-project)
// and repaint from the cached tree.
function wdExpandAll(on) {
  const dirs = _wdAllDirPaths(window._wdTreeData || []);
  const m = _wdLoadExpanded();
  dirs.forEach(p => { m[p] = !!on; });
  try { localStorage.setItem(_wdExpandKey(), JSON.stringify(m)); } catch (_) {}
  repaintTerminalTree();
}

// Fetch + render the tree into #terminal-tree. Called when the panel opens and
// after file changes (new file / save / turn end).
async function refreshTerminalTree() {
  const host = document.getElementById('terminal-tree');
  if (!host) return;
  const proj = await _workdirActiveProject();
  if (!proj || !proj.working_dir) {
    host.innerHTML = '<div class="pt-empty">Kein Arbeitsverzeichnis.</div>';
    return;
  }
  if (!host.dataset.loaded) host.innerHTML = '<div class="pt-loading">Lädt…</div>';
  // Refresh the code-index per-file state (sync bullets) — same source the
  // project-view tree uses, so the bullet colour matches in chat + project view.
  // Best-effort; the tree still renders (just without sync bullets) if it fails.
  try { await _wdLoadCodeIndexState(proj); } catch (_) {}
  try {
    const data = await API.get(`/v1/agents/${proj.agent}/projects/${encodeURIComponent(proj.name)}/folder-tree?path=${encodeURIComponent(proj.working_dir)}`);
    if (data.error) { host.innerHTML = `<div class="pt-empty">${esc(data.error)}</div>`; return; }
    window._wdTreeData = data.tree || [];
    host.innerHTML = _wdRenderTree(window._wdTreeData);
    host.dataset.loaded = '1';
    // Keep the poll's change-detection signature in sync so a full refresh here
    // doesn't trigger a redundant repaint on the next poll tick.
    if (typeof _term !== 'undefined' && typeof _wdTreeSignature === 'function') {
      _term._treeSig = _wdTreeSignature(window._wdTreeData).sort().join('\n');
    }
  } catch (e) {
    host.innerHTML = '<div class="pt-empty">Verzeichnis konnte nicht geladen werden.</div>';
  }
}

// Populate state._codeIndexFiles (repo-relative-keyed per-file index state) for
// the active code-mode project, so the bottom tree's sync bullets work even in a
// chat (where the project-view's status poll isn't running).
async function _wdLoadCodeIndexState(proj) {
  if (!proj || !proj.name) return;
  const d = await API.get(`/v1/agents/${proj.agent}/projects/${encodeURIComponent(proj.name)}/code-index/status`);
  if (d && !d.error && d.files) state._codeIndexFiles = d.files;
}

// Cheap re-paint from the cached tree data (no fetch) — used to flash the unsaved
// '*' / selection without re-walking the disk. Falls back to a fetch if no cache.
function repaintTerminalTree() {
  const host = document.getElementById('terminal-tree');
  if (!host) return;
  if (!window._wdTreeData) { refreshTerminalTree(); return; }
  host.innerHTML = _wdRenderTree(window._wdTreeData);
}
