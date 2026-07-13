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

// Human-readable local timestamp for a unix-seconds mtime (file-tree tooltip).
function _fmtMtime(sec) {
  sec = Number(sec) || 0;
  if (!sec) return '—';
  try { return new Date(sec * 1000).toLocaleString(); } catch (_) { return '—'; }
}

// ── File-tree sort order (per-project, persisted in localStorage) ─────────────
// Modes: 'type' (folders first, then files, each A→Z — the default), 'name'
// (A→Z, folders + files mixed), 'date' (newest mtime first), 'size' (largest
// first). Sorting is applied client-side in _wdRenderTree, recursively.
const _WD_SORT_LABELS = { type: 'Art (Ordner zuerst)', name: 'Name', date: 'Datum (neueste zuerst)', size: 'Größe (größte zuerst)' };
function _wdSortKey() {
  const pid = (state._projectDetail && state._projectDetail.id) || state.currentProject || 'p';
  return `brain.wdtree.sort.${pid}`;
}
function _wdSortMode() {
  try { return localStorage.getItem(_wdSortKey()) || 'type'; } catch (_) { return 'type'; }
}
function _wdSetSortMode(mode) {
  try { localStorage.setItem(_wdSortKey(), mode); } catch (_) {}
}
// ── New / modified file detection (persisted across reloads) ─────────────────
// We keep two per-project localStorage maps:
//   • snapshot  brain.wdtree.snap.<pid>   = {relPath: "mtime:size"} last seen
//   • changes   brain.wdtree.changed.<pid> = {relPath: "new"|"modified"}
// On every tree load we diff the tree against the snapshot: paths absent from
// the snapshot → "new", paths whose mtime/size changed → "modified". The change
// flags ACCUMULATE (and persist) until the user opens the file (or clears all),
// so a reload still shows what changed since last acknowledged. First-ever load
// of a project seeds the snapshot WITHOUT flagging everything (no prior state =
// nothing "new" to report).
function _wdSnapKey() {
  const pid = (state._projectDetail && state._projectDetail.id) || state.currentProject || 'p';
  return `brain.wdtree.snap.${pid}`;
}
function _wdChangeKey() {
  const pid = (state._projectDetail && state._projectDetail.id) || state.currentProject || 'p';
  return `brain.wdtree.changed.${pid}`;
}
function _wdLoadMap(key) { try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) { return {}; } }
function _wdSaveMap(key, m) { try { localStorage.setItem(key, JSON.stringify(m)); } catch (_) {} }

// Flatten a tree to {relPath: "mtime:size"} for files (folders tracked as "dir").
function _wdFlattenTree(nodes, wd, acc) {
  acc = acc || {};
  for (const n of (nodes || [])) {
    const rel = _wdRelToWorkingDir(n.path);
    if (n.type === 'dir') { acc[rel] = 'dir'; _wdFlattenTree(n.children || [], wd, acc); }
    else acc[rel] = `${n.mtime || 0}:${n.size || 0}`;
  }
  return acc;
}

// Diff `tree` against the persisted snapshot; merge new/modified flags into the
// persisted change map; advance the snapshot. Returns the (path→state) change
// map for rendering. window._wdChangeState caches it for the synchronous render.
function _wdComputeChanges(tree) {
  const snapKey = _wdSnapKey(), chgKey = _wdChangeKey();
  const prev = _wdLoadMap(snapKey);
  const cur = _wdFlattenTree(tree, '');
  const hadSnapshot = Object.keys(prev).length > 0;
  const changes = _wdLoadMap(chgKey);
  // Files currently OPEN in an editor aren't "news" when their mtime changes —
  // the user just saved them. Skip modified-flagging for those (new files still
  // flag). Built from _term.tabs (editor kind) as repo-relative paths.
  const openRel = new Set(
    ((typeof _term !== 'undefined' && _term.tabs) || [])
      .filter(t => t.kind === 'editor' && t.path)
      .map(t => _wdRelToWorkingDir(t.path)));
  if (hadSnapshot) {
    for (const rel in cur) {
      if (cur[rel] === 'dir') { if (!(rel in prev)) changes[rel] = 'new'; continue; }
      if (!(rel in prev)) changes[rel] = 'new';
      else if (prev[rel] !== cur[rel] && changes[rel] !== 'new' && !openRel.has(rel)) changes[rel] = 'modified';
    }
  }
  // Drop change flags for paths that no longer exist (deleted/renamed away).
  for (const rel in changes) { if (!(rel in cur)) delete changes[rel]; }
  _wdSaveMap(snapKey, cur);
  _wdSaveMap(chgKey, changes);
  window._wdChangeState = changes;
  return changes;
}

// Clear a single path's new/modified flag (called when the user opens the file)
// and repaint so the badge disappears.
function _wdClearChange(absPath) {
  const chgKey = _wdChangeKey();
  const changes = _wdLoadMap(chgKey);
  const rel = _wdRelToWorkingDir(absPath);
  if (rel in changes) {
    delete changes[rel];
    _wdSaveMap(chgKey, changes);
    window._wdChangeState = changes;
    if (typeof repaintTerminalTree === 'function') repaintTerminalTree();
  }
}

// Clear ALL change flags for the active project (programmatic; markers normally
// clear per-file on open).
function wdClearChanges() {
  _wdSaveMap(_wdChangeKey(), {});
  window._wdChangeState = {};
  if (typeof repaintTerminalTree === 'function') repaintTerminalTree();
}

// State for the current path (from the cached change map). '' when unflagged.
function _wdChangeFor(absPath) {
  const m = window._wdChangeState || _wdLoadMap(_wdChangeKey());
  return m[_wdRelToWorkingDir(absPath)] || '';
}

// Does any descendant file of a dir node carry a new/modified flag? (Used to dot
// a collapsed folder.)
function _wdSubtreeHasChange(dirNode) {
  for (const c of (dirNode.children || [])) {
    if (c.type === 'dir') { if (_wdSubtreeHasChange(c)) return true; }
    else if (_wdChangeFor(c.path)) return true;
  }
  return false;
}

// Return a NEW sorted array of sibling nodes per the active sort mode.
function _wdSortNodes(nodes) {
  const mode = _wdSortMode();
  const arr = (nodes || []).slice();
  const byName = (a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base', numeric: true });
  arr.sort((a, b) => {
    const aDir = a.type === 'dir', bDir = b.type === 'dir';
    if (mode === 'type') {
      if (aDir !== bDir) return aDir ? -1 : 1;   // folders first
      return byName(a, b);
    }
    if (mode === 'name') return byName(a, b);
    if (mode === 'date') {
      // Dirs carry no mtime → keep them grouped first, newest files lead.
      if (aDir !== bDir) return aDir ? -1 : 1;
      return (b.mtime || 0) - (a.mtime || 0) || byName(a, b);
    }
    if (mode === 'size') {
      if (aDir !== bDir) return aDir ? -1 : 1;
      return (b.size || 0) - (a.size || 0) || byName(a, b);
    }
    return byName(a, b);
  });
  return arr;
}

// Sort-mode picker (toolbar button). Sets the mode + repaints the tree.
function wdSortMenu(ev) {
  const cur = _wdSortMode();
  const items = Object.keys(_WD_SORT_LABELS).map(k => ({
    label: (k === cur ? '● ' : '○ ') + _WD_SORT_LABELS[k],
    fn: () => { _wdSetSortMode(k); if (typeof repaintTerminalTree === 'function') repaintTerminalTree(); },
  }));
  _wdMenu(ev, items);
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
  if (!t) return false;
  // The active tab is tracked PER PANE (pane.active), not a global _term.active
  // (which never existed → the highlight was always off). A file is "active" when
  // its editor tab is the active tab of any pane.
  const panes = (typeof _term !== 'undefined' && _term.panes) || [];
  return panes.some(p => p.active === t.id);
}

// ── Unified file + symbol search ─────────────────────────────────────────────
// The tree search box filters by FILENAME and by SYMBOL name. Empty = no filter.
let _wdFilter = '';
function wdTreeFilter(v) {
  _wdFilter = (v || '').toLowerCase().trim();
  if (typeof repaintTerminalTree === 'function') repaintTerminalTree();
}
// Symbols of `absPath` matching the active filter (or all, if the FILE name
// itself matched). Returns [] when symbols aren't loaded.
function _wdFileSymbolMatches(absPath, fileNameMatched) {
  const syms = (typeof _wdSymbolsForFile === 'function') ? _wdSymbolsForFile(absPath) : [];
  if (!_wdFilter || fileNameMatched) return syms;
  return syms.filter(s => (s.name || '').toLowerCase().includes(_wdFilter));
}
// Does a FILE node survive the active filter? (filename match OR any symbol
// match.) No filter → always visible.
function _wdFileVisible(n, nameMatched) {
  if (!_wdFilter) return true;
  if (nameMatched) return true;
  return _wdFileSymbolMatches(n.path, false).length > 0;
}

// Render a source file's symbols inline (tree child rows). Reuses the symbol
// renderer from panels_terminal.js. Shown when the file row is expanded.
function _wdFileSymbolsHtml(n, fileNameMatched) {
  if (typeof _wdSymbolRowsHtml !== 'function') return '';
  const syms = _wdFileSymbolMatches(n.path, fileNameMatched);
  if (!syms.length) return '<div class="sym-none">keine Symbole</div>';
  return _wdSymbolRowsHtml(syms);
}

// ── Work-files toggle (chats/ output folders in the tree) ─────────────────────
// The per-chat output folders (chats/<slug>_<date>_<sid>/) are the agent's
// working directories, not project sources — with the toggle OFF the tree shows
// only what code mining sees (project files + imports). The chats/ contents are
// still FETCHED (window._wdTreeData stays complete — the Terminal-Chats list
// renders each chat's folder from it); only the tree DISPLAY filters them.
function _wdWorkVisible() {
  return !!(typeof _term !== 'undefined' && _term.workFilesVisible);
}
// Top-level display filter for the tree paint (root call only, not recursion).
function _wdDisplayNodes(nodes) {
  if (_wdWorkVisible()) return nodes || [];
  return (nodes || []).filter(n => !(n.type === 'dir' && n.name === 'chats'));
}
function wdToggleWorkFiles() {
  if (typeof _term === 'undefined') return;
  _term.workFilesVisible = !_term.workFilesVisible;
  if (typeof _terminalApplyLayout === 'function') _terminalApplyLayout();  // button active state
  if (typeof _terminalPersist === 'function') _terminalPersist();
  repaintTerminalTree();
  if (typeof showToast === 'function') {
    showToast(_term.workFilesVisible ? 'Chat-Arbeitsdateien im Baum: sichtbar' : 'Chat-Arbeitsdateien im Baum: ausgeblendet');
  }
}

// Per-chat-folder content signatures {folderName: "mtime:size,…"} from the
// chats/ top-level dir — drives the Terminal-Chats auto-expand on NEW files.
function _wdChatFolderSigs(nodes) {
  const root = (nodes || []).find(n => n.type === 'dir' && n.name === 'chats');
  const sigs = {};
  for (const d of ((root && root.children) || [])) {
    if (d.type !== 'dir') continue;
    const flat = [];
    (function walk(ns) {
      for (const n of (ns || [])) {
        if (n.type === 'dir') walk(n.children || []);
        else flat.push(`${n.name}:${n.mtime || 0}:${n.size || 0}`);
      }
    })(d.children || []);
    sigs[d.name] = flat.sort().join(',');
  }
  return sigs;
}

// Diff the fresh tree's chat-folder signatures against the last seen set; a
// changed/new folder auto-expands ITS chat's node in the Terminal-Chats list
// (never anything in the file tree) and re-renders that list. First load after
// panel-open only seeds (_chatFolderSigs reset in _terminalLoadSessions) so
// reopening restores the persisted expand state without surprises.
function _wdSyncChatFolders(tree) {
  if (typeof _term === 'undefined') return;
  const sigs = _wdChatFolderSigs(tree);
  const prev = _term._chatFolderSigs;
  _term._chatFolderSigs = sigs;
  if (!prev) {
    // Seed pass (first tree load after panel open): no auto-expand, but the
    // history list may have rendered BEFORE tree data existed — repaint it once
    // so the folder carets appear.
    if (Object.keys(sigs).length && typeof renderTermchatHistory === 'function') renderTermchatHistory();
    return;
  }
  let changed = false;
  for (const name in sigs) {
    if (prev[name] === sigs[name]) continue;
    changed = true;
    const sid = name.split('_').pop();
    if (sid && !(_term.chatFolderOpen || {})[sid]) {
      if (!_term.chatFolderOpen) _term.chatFolderOpen = {};
      _term.chatFolderOpen[sid] = true;
      if (typeof _terminalPersist === 'function') _terminalPersist();
    }
  }
  for (const name in prev) { if (!(name in sigs)) changed = true; }
  if (changed && typeof renderTermchatHistory === 'function') renderTermchatHistory();
}

function _wdRenderTree(nodes) {
  if (!nodes || !nodes.length) return '<div class="pt-empty">Leer.</div>';
  const filt = _wdFilter;
  const html = _wdSortNodes(nodes).map(n => {
    if (n.type === 'dir') {
      const childHtml = _wdRenderTree(n.children || []);
      // While filtering, hide directories whose subtree rendered nothing.
      if (filt && (!childHtml || childHtml.indexOf('pt-row') === -1)) return '';
      // Auto-expand directories during a filter so matches are visible.
      const open = filt ? true : _wdDirExpanded(n.path);
      const dp = esc(n.path);
      // Folder cue: a genuinely NEW folder pills green; an existing folder that
      // (while collapsed) hides changed descendants gets a subtler "contains
      // changes" pill so the change isn't invisible behind a closed folder.
      const dirChg = (typeof _wdChangeFor === 'function') ? _wdChangeFor(n.path) : '';
      const dirHasChg = !open && (typeof _wdSubtreeHasChange === 'function') && _wdSubtreeHasChange(n);
      const dirChgCls = dirChg ? ` pt-chg pt-chg-${dirChg}` : (dirHasChg ? ' pt-chg pt-chg-contains' : '');
      const dirChgTip = dirChg ? ` · ${dirChg === 'new' ? 'NEUER Ordner' : 'GEÄNDERT'}` : (dirHasChg ? ' · enthält neue/geänderte Dateien' : '');
      return `<div class="pt-branch pt-realdir">
        <div class="pt-row pt-realrow${dirChgCls}" data-dir="${dp}" draggable="true"
          title="${esc(n.path || n.name)}${dirChgTip}"
          onclick="wdToggleDir(this, '${dp}')"
          oncontextmenu="wdDirMenu(event, '${dp}')"
          ondragstart="wdDragStart(event, '${dp}')" ondragend="wdDragEnd(event)"
          ondragover="wdDragOver(event, this)" ondragleave="wdDragLeave(event, this)"
          ondrop="wdDrop(event, '${dp}')">
          ${_ptCaret(open)}
          <span class="pt-icon">${_PT_ICON.folders}</span>
          <span class="pt-label">${esc(n.name)}</span>
        </div>
        <div class="pt-children" style="display:${open ? 'block' : 'none'}">${childHtml}</div>
      </div>`;
    }
    const nameMatched = !filt || (n.name || '').toLowerCase().includes(filt);
    if (!_wdFileVisible(n, nameMatched)) return '';
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
    // New/modified-since-last-seen cue (persisted; cleared when the file is
    // opened). Draws the row as a glowing pill (CSS via the pt-chg-* class); the
    // colour distinguishes new (green) from modified (amber). The title surfaces
    // the meaning on hover.
    const chg = (typeof _wdChangeFor === 'function') ? _wdChangeFor(n.path) : '';
    const chgCls = chg ? ` pt-chg pt-chg-${chg}` : '';
    const chgTip = chg ? ` · ${chg === 'new' ? 'NEU seit dem letzten Ansehen' : 'GEÄNDERT seit dem letzten Ansehen'}` : '';
    // Tooltip: path · size · last-modified (size/mtime come from the folder-tree
    // endpoint per file).
    const metaTip = ` · ${_fmtBytes(n.size || 0)} · geändert ${_fmtMtime(n.mtime)}`;
    const fp = esc(n.path || '');
    // Source files with indexed symbols are EXPANDABLE → caret + inline symbols
    // (the merged former "Symbole" panel). Expanded state is auto-on while a
    // symbol filter matches, else the persisted per-file toggle.
    const hasSyms = (typeof _wdSymbolsForFile === 'function') && _wdSymbolsForFile(n.path).length > 0;
    const symMatch = !!filt && !nameMatched;   // shown because of a symbol hit
    const symOpen = hasSyms && (symMatch || _wdDirExpanded(n.path));
    // Caret (only on files with symbols) TOGGLES the inline symbols; clicking
    // the icon/name OPENS the file. This keeps the familiar "click file → open"
    // behaviour while making symbols an opt-in expand.
    const caret = hasSyms
      ? `<span class="pt-caret${symOpen ? ' open' : ''}" onclick="event.stopPropagation();wdToggleFileSymbols(this,'${fp}')"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg></span>`
      : '<span class="pt-caret-spacer"></span>';
    const symChildren = hasSyms
      ? `<div class="pt-children pt-symchildren" style="display:${symOpen ? 'block' : 'none'}">${symOpen ? _wdFileSymbolsHtml(n, nameMatched) : ''}</div>`
      : '';
    return `<div class="pt-branch pt-realfilebranch">
      <div class="pt-row pt-realfile${sel}${chgCls}" data-path="${fp}" data-git="${esc(git)}" draggable="true"
         title="${esc(n.path || n.name)}${metaTip}${gitTip}${chgTip}" onclick="wdOpenFile('${fp}')"
         oncontextmenu="wdFileMenu(event, '${fp}')"
         ondragstart="wdDragStart(event, '${fp}')" ondragend="wdDragEnd(event)">
      ${caret}
      <span class="pt-icon pt-fileicon">${_PT_ICON.file}</span>
      <span class="pt-label">${esc(n.name)}${star}</span>
      ${dot ? `<span class="pt-dot-wrap">${dot}</span>` : ''}
      </div>
      ${symChildren}
    </div>`;
  }).join('');
  return html || (filt ? '<div class="pt-empty">Keine Treffer.</div>' : '<div class="pt-empty">Leer.</div>');
}

// Toggle a source file's inline symbol list (lazy-fills on first open). Opening
// a file's symbols does NOT open the file in the editor — that's the name/icon's
// job via double-click or the caret-less area; here the whole row toggles. Reuse
// the folder expand-state store so it persists per file path.
function wdToggleFileSymbols(caretEl, absPath) {
  const branch = caretEl.closest('.pt-realfilebranch');
  if (!branch) { wdOpenFile(absPath); return; }
  const kids = branch.querySelector('.pt-symchildren');
  if (!kids) { wdOpenFile(absPath); return; }
  const show = kids.style.display === 'none';
  if (show && !kids.innerHTML.trim()) {
    // Lazy-fill from the loaded outline (filename considered "matched" so all
    // of the file's symbols show, not just filtered ones).
    const node = { path: absPath, name: (absPath || '').split('/').pop() };
    kids.innerHTML = _wdFileSymbolsHtml(node, true);
  }
  kids.style.display = show ? 'block' : 'none';
  caretEl.classList.toggle('open', show);
  _wdSetExpanded(absPath, show);
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
// external app (Word/Excel/PowerPoint/Acrobat/…) on click. xlsx/xlsm are NOT
// here (since v9.263.0): they open as an in-app grid preview in the bottom
// panel — the right-click menu still offers "extern öffnen".
const _WD_EXTERNAL_EXT = new Set([
  'docx', 'doc', 'xls', 'pptx', 'ppt', 'pdf',
  'odt', 'ods', 'odp', 'rtf', 'pages', 'numbers', 'key',
  'zip', 'mp4', 'mov', 'avi', 'mp3', 'wav', 'm4a',
]);
function _wdExt(p) { const n = (p || '').split('/').pop(); return n.includes('.') ? n.split('.').pop().toLowerCase() : ''; }

function wdOpenFile(absPath) {
  if (!absPath) return;
  // Opening a file acknowledges its new/modified cue → clear it (persisted).
  if (typeof _wdClearChange === 'function') _wdClearChange(absPath);
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

// Two-file compare: 'Zum Vergleich markieren' remembers side A; the next
// file's menu offers 'Vergleichen mit <A>' → diff tab (panels_terminal.js).
let _wdDiffMark = null;

// Right-click a FILE row → open / diff / rename / delete.
function wdFileMenu(ev, absPath) {
  const row = ev.target && ev.target.closest ? ev.target.closest('.pt-row') : null;
  const git = (row && row.dataset ? row.dataset.git : '') || '';
  const items = [
    { label: 'Im Editor öffnen', fn: () => terminalOpenFile(absPath) },
    { label: 'In externem Programm öffnen', fn: () => wdOpenExternal(absPath) },
    { sep: true },
  ];
  // git-modified (not untracked/deleted) → line diff against the last commit
  if (git && !'?D'.includes(git)) {
    items.push({ label: 'Diff gegen HEAD', fn: () => terminalOpenDiff(absPath, { git: true }) });
  }
  if (_wdDiffMark && _wdDiffMark !== absPath) {
    const aName = _wdDiffMark.split('/').pop();
    items.push({ label: `Vergleichen mit „${aName}“`, fn: () => {
      const a = _wdDiffMark; _wdDiffMark = null;
      terminalOpenDiff(absPath, { pathA: a });
    } });
  }
  items.push({ label: 'Zum Vergleich markieren', fn: () => { _wdDiffMark = absPath; } });
  items.push({ sep: true });
  items.push({ label: 'Umbenennen…', fn: () => wdRename(absPath) });
  items.push({ label: 'Löschen…', danger: true, fn: () => wdDelete(absPath) });
  _wdMenu(ev, items);
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

// ── Viewport scroll persistence (per project, survives reload) ────────────────
function _wdScrollKey() {
  const pid = (state._projectDetail && state._projectDetail.id) || state.currentProject || 'p';
  return `brain.wdtree.scroll.${pid}`;
}
function _wdLoadScrollTop() {
  try { return parseInt(localStorage.getItem(_wdScrollKey()) || '0', 10) || 0; } catch (_) { return 0; }
}
// Attach a (once-per-element) throttled scroll listener that persists scrollTop.
function _wdAttachScrollPersist(host) {
  if (!host || host._wdScrollBound) return;
  host._wdScrollBound = true;
  let t = null;
  host.addEventListener('scroll', () => {
    if (t) return;
    t = setTimeout(() => { t = null; try { localStorage.setItem(_wdScrollKey(), String(host.scrollTop)); } catch (_) {} }, 200);
  });
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
    // Detect new/modified files vs the persisted snapshot (first load only seeds
    // the snapshot — nothing flagged) BEFORE rendering so badges paint.
    _wdComputeChanges(window._wdTreeData);
    // Chat-output folders: diff their content signatures → auto-expand the
    // affected chat's node in the Terminal-Chats list (display-only there;
    // the file tree itself never auto-expands).
    _wdSyncChatFolders(window._wdTreeData);
    const prevTop = host.scrollTop;
    host.innerHTML = _wdRenderTree(_wdDisplayNodes(window._wdTreeData));
    host.dataset.loaded = '1';
    // Restore scroll: keep the live position on a refresh, or — on the very first
    // load after a page reload — the persisted viewport, so a reload doesn't reset
    // the tree to the top.
    _wdAttachScrollPersist(host);
    host.scrollTop = prevTop || _wdLoadScrollTop();
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
  // Preserve the scroll position across the innerHTML swap so a poll-driven
  // resync doesn't jump the viewport back to the top (no flicker/reset).
  const top = host.scrollTop;
  host.innerHTML = _wdRenderTree(_wdDisplayNodes(window._wdTreeData));
  host.scrollTop = top;
}
