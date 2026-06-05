// panels_project_tree.js — unified virtual-filesystem source tree for the
// project right panel (replaces the separate Anweisungen/Dateien/Ordner/
// Web-Adressen sections). C2: read-only render + collapse/expand to file level
// + per-item MemPalace state colors + lazy ingested-folder subtree. Grouping +
// drag/drop (C3) build ON this. Globals only (no modules), loaded after
// panels_project_sync.js, before init.js.
//
// State color model (4 states), mapped from the per-item sync state:
//   indexed → 🟢 grün   | syncing/pending → 🟠 amber | error → 🔴 rot | stale → ⚪ grau
// The dot + a legend communicate it; hover gives the verbose status.

const _PT_STATE = {
  indexed: { cls: 'indexed', label: 'Indexiert' },
  syncing: { cls: 'pending', label: 'Wird abgeglichen' },
  pending: { cls: 'pending', label: 'Ausstehend' },
  error:   { cls: 'error',   label: 'Fehler' },
  stale:   { cls: 'stale',   label: 'Veraltet' },
};

// Feather-style SVG icons (match the rest of the app — no emoji). 14px, inherit
// stroke so they tint with the row color.
function _ptSvg(paths) {
  return `<svg class="pt-svgicon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}
const _PT_ICON = {
  instructions: _ptSvg('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/>'),
  files: _ptSvg('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'),
  folders: _ptSvg('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'),
  folderOpen: _ptSvg('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'),
  urls: _ptSvg('<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
  file: _ptSvg('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'),
};

// localStorage key for the per-project expand/collapse state (UI-only, per user).
function _ptExpandKey() {
  const pid = (state._projectDetail && state._projectDetail.id) || state._researchProject || 'p';
  return `brain.ptree.expanded.${pid}`;
}
function _ptLoadExpanded() {
  try { return JSON.parse(localStorage.getItem(_ptExpandKey()) || '{}') || {}; }
  catch (_) { return {}; }
}
function _ptSaveExpanded(map) {
  try { localStorage.setItem(_ptExpandKey(), JSON.stringify(map)); } catch (_) {}
}
function _ptIsExpanded(nodeKey, dflt) {
  const m = _ptLoadExpanded();
  return (nodeKey in m) ? !!m[nodeKey] : !!dflt;
}
function _ptSetExpanded(nodeKey, on) {
  const m = _ptLoadExpanded(); m[nodeKey] = !!on; _ptSaveExpanded(m);
}

// Resolve a per-item MemPalace state via the existing sync-items cache.
function _ptItemState(kind, ident) {
  const it = (state._projectSyncItems || {})[`${kind}:${ident}`];
  if (!it) return 'pending';
  return it.state || 'pending';
}

function _ptDot(stateName) {
  const s = _PT_STATE[stateName] || _PT_STATE.pending;
  return `<span class="pt-dot" data-state="${s.cls}" title="${esc(s.label)}"></span>`;
}

function _ptCaret(open) {
  return `<span class="pt-caret ${open ? 'open' : ''}">
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg>
  </span>`;
}

// ─── Entry point: render the whole tree into #project-source-tree ───────────

function renderProjectSourceTree() {
  const host = document.getElementById('project-source-tree');
  if (!host) return;
  const p = state._projectDetail || {};
  const agentId = state._projectDetailAgent || state.activeAgentId || 'main';
  const projectName = state._projectDetailName || state._researchProject || '';

  const legend = `
    <div class="pt-legend">
      <span><span class="pt-dot" data-state="indexed"></span>Indexiert</span>
      <span><span class="pt-dot" data-state="pending"></span>Ausstehend</span>
      <span><span class="pt-dot" data-state="error"></span>Fehler</span>
      <span><span class="pt-dot" data-state="stale"></span>Veraltet</span>
    </div>`;

  host.innerHTML = legend + `
    <div class="pt-root">
      ${_ptInstructionsNode(p)}
      ${_ptTypeNode('files', 'Dateien', p)}
      ${_ptTypeNode('folders', 'Ordner', p)}
      ${_ptTypeNode('urls', 'Web-Adressen', p)}
    </div>`;

  // Populate the three groupable branches (async/data-driven). Instructions is
  // already inlined above (its text is on project.json).
  _ptFillFiles(agentId, projectName);
  _ptFillFolders(agentId, projectName);
  _ptFillUrls(p);
}

// Anweisungen — a singleton top node (never grouped); expands to the text.
function _ptInstructionsNode(p) {
  const open = _ptIsExpanded('instructions', false);
  const txt = (p.instructions || '').trim();
  const body = txt
    ? `<div class="pt-instructions">${renderMarkdown(txt)}</div>`
    : `<div class="pt-empty">Noch keine Anweisungen hinterlegt.</div>`;
  return `
    <div class="pt-branch" data-node="instructions">
      <div class="pt-row pt-typerow" onclick="ptToggle('instructions')">
        ${_ptCaret(open)}
        <span class="pt-icon">${_PT_ICON.instructions}</span>
        <span class="pt-label">Anweisungen</span>
        <span class="pt-actions">
          <button class="pt-act" onclick="event.stopPropagation(); editProjectInstructions()" title="Bearbeiten">✎</button>
        </span>
      </div>
      <div class="pt-children" data-children="instructions" style="${open ? '' : 'display:none'}">${body}</div>
    </div>`;
}

// One groupable type branch (files / folders / urls). The virtual-folder groups
// (C3) render INSIDE #pt-items-<type>; C2 just lists the flat items.
function _ptTypeNode(type, label, p) {
  const open = _ptIsExpanded(type, false);   // default collapsed; localStorage remembers per-project
  const icon = type === 'files' ? _PT_ICON.files : type === 'folders' ? _PT_ICON.folders : _PT_ICON.urls;
  const addAction = type === 'files'
    ? `<label class="pt-act" title="Dateien hinzufügen" onclick="event.stopPropagation()">＋<input type="file" multiple style="display:none" onchange="uploadProjectFiles(this.files)"></label>`
    : type === 'folders'
      ? `<button class="pt-act" onclick="event.stopPropagation(); addProjectInputFolder()" title="Ordner hinzufügen">＋</button>`
      : `<button class="pt-act" onclick="event.stopPropagation(); addProjectWebUrl()" title="Web-Adresse hinzufügen">＋</button>`;
  return `
    <div class="pt-branch" data-node="${type}" data-type="${type}">
      <div class="pt-row pt-typerow" onclick="ptToggle('${type}')">
        ${_ptCaret(open)}
        <span class="pt-icon">${icon}</span>
        <span class="pt-label">${esc(label)}</span>
        <span class="pt-count" id="pt-count-${type}"></span>
        <span class="pt-actions">${addAction}</span>
      </div>
      <div class="pt-children" data-children="${type}" style="${open ? '' : 'display:none'}">
        <div class="pt-items" id="pt-items-${type}"><div class="pt-loading">Lädt…</div></div>
      </div>
    </div>`;
}

function ptToggle(nodeKey) {
  const children = document.querySelector(`.pt-children[data-children="${cssEsc(nodeKey)}"]`);
  const caret = document.querySelector(`.pt-branch[data-node="${cssEsc(nodeKey)}"] .pt-caret`);
  if (!children) return;
  const willOpen = children.style.display === 'none';
  children.style.display = willOpen ? '' : 'none';
  if (caret) caret.classList.toggle('open', willOpen);
  _ptSetExpanded(nodeKey, willOpen);
}

// cssEsc — minimal attribute-selector escape (node keys are our own slugs/paths).
function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

// ─── Type fillers (reuse the existing data endpoints) ───────────────────────

async function _ptFillFiles(agentId, projectName) {
  const host = document.getElementById('pt-items-files');
  if (!host) return;
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/docs`);
    const docs = data.documents || [];
    _ptSetCount('files', docs.length);
    if (!docs.length) { host.innerHTML = '<div class="pt-empty">Noch keine Dateien.</div>'; return; }
    host.innerHTML = docs.map(d => {
      const id = d.source_hash || '';
      const st = _ptItemState('attachment', id);
      return `<div class="pt-row pt-item" data-type="files" data-id="${esc(id)}" title="${esc(d.source || d.name || '')}">
        ${_ptDot(st)}
        <span class="pt-icon pt-fileicon">${_PT_ICON.file}</span>
        <span class="pt-label">${esc(d.source || d.name || 'Dokument')}</span>
        <span class="pt-actions"><button class="pt-act" onclick="event.stopPropagation(); deleteProjectFile('${esc(agentId)}','${esc(projectName)}','${esc(id)}')" title="Entfernen">✕</button></span>
      </div>`;
    }).join('');
  } catch (_) { host.innerHTML = '<div class="pt-empty">Dateien konnten nicht geladen werden.</div>'; }
}

async function _ptFillFolders(agentId, projectName) {
  const host = document.getElementById('pt-items-folders');
  if (!host) return;
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders`);
    const folders = data.input_folders || data.folders || [];
    _ptSetCount('folders', folders.length);
    if (!folders.length) { host.innerHTML = '<div class="pt-empty">Noch keine Ordner.</div>'; return; }
    host.innerHTML = folders.map((f, i) => {
      const path = f.path || '';
      const nm = path.split('/').filter(Boolean).pop() || path;
      const st = _ptItemState('folder', path);
      // Ingested folder: expandable to its REAL read-only subtree (lazy).
      return `<div class="pt-branch pt-folder" data-folder="${esc(path)}">
        <div class="pt-row pt-item pt-folderrow" data-type="folders" data-id="${esc(path)}" onclick="ptToggleFolder(this, '${esc(agentId)}','${esc(projectName)}','${esc(path)}')" title="${esc(path)}">
          ${_ptCaret(false)}
          ${_ptDot(st)}
          <span class="pt-icon">${_PT_ICON.folders}</span>
          <span class="pt-label">${esc(nm)}</span>
          <span class="pt-actions"><button class="pt-act" onclick="event.stopPropagation(); removeProjectInputFolder(${i})" title="Entfernen">✕</button></span>
        </div>
        <div class="pt-children pt-foldertree" style="display:none"><div class="pt-loading">…</div></div>
      </div>`;
    }).join('');
  } catch (_) { host.innerHTML = '<div class="pt-empty">Ordner konnten nicht geladen werden.</div>'; }
}

function _ptFillUrls(p) {
  const host = document.getElementById('pt-items-urls');
  if (!host) return;
  const urls = p.web_urls || [];
  _ptSetCount('urls', urls.length);
  if (!urls.length) { host.innerHTML = '<div class="pt-empty">Noch keine Web-Adressen.</div>'; return; }
  host.innerHTML = urls.map((u, i) => {
    const url = u.url || '';
    let host_ = url; try { host_ = new URL(url).host.replace(/^www\./, ''); } catch (_) {}
    // Web URLs are mined as a batch → no per-URL sync item. Render with a
    // placeholder dot, then patch from /web-url-states below (state._ptUrlStates).
    const st = (state._ptUrlStates || {})[url] || 'pending';
    return `<div class="pt-row pt-item" data-type="urls" data-id="${esc(url)}" title="${esc(url)}">
      ${_ptDot(st)}
      <span class="pt-icon">${_PT_ICON.urls}</span>
      <span class="pt-label">${esc(u.title || host_)}</span>
      <span class="pt-actions"><button class="pt-act" onclick="event.stopPropagation(); removeProjectWebUrl(${i})" title="Entfernen">✕</button></span>
    </div>`;
  }).join('');
  // Fetch real per-URL states (companion .md indexed in MemPalace) + patch dots.
  const agentId = state._projectDetailAgent || 'main';
  const projectName = state._projectDetailName || '';
  API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/web-url-states`)
    .then(d => { state._ptUrlStates = d.states || {}; repaintProjectTreeDots(); })
    .catch(() => {});
}

function _ptSetCount(type, n) {
  const el = document.getElementById(`pt-count-${type}`);
  if (el) el.textContent = n ? `(${n})` : '';
}

// ─── Lazy ingested-folder subtree (read-only; fixed hierarchy) ──────────────

async function ptToggleFolder(rowEl, agentId, projectName, folderPath) {
  const branch = rowEl.closest('.pt-folder');
  const children = branch && branch.querySelector('.pt-foldertree');
  const caret = rowEl.querySelector('.pt-caret');
  if (!children) return;
  const willOpen = children.style.display === 'none';
  children.style.display = willOpen ? '' : 'none';
  if (caret) caret.classList.toggle('open', willOpen);
  if (willOpen && !children.dataset.loaded) {
    try {
      const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/folder-tree?path=${encodeURIComponent(folderPath)}`);
      children.innerHTML = _ptRenderFolderTree(data.tree || []);
      children.dataset.loaded = '1';
    } catch (e) {
      children.innerHTML = `<div class="pt-empty">Ordnerinhalt konnte nicht geladen werden.</div>`;
    }
  }
}

// Render the real subtree returned by /folder-tree (read-only; not draggable).
function _ptRenderFolderTree(nodes) {
  if (!nodes.length) return '<div class="pt-empty">Leer.</div>';
  return nodes.map(n => {
    if (n.type === 'dir') {
      return `<div class="pt-branch pt-realdir">
        <div class="pt-row pt-realrow" onclick="ptToggleRealDir(this)">
          ${_ptCaret(false)}
          <span class="pt-icon">${_PT_ICON.folders}</span>
          <span class="pt-label">${esc(n.name)}</span>
        </div>
        <div class="pt-children" style="display:none">${_ptRenderFolderTree(n.children || [])}</div>
      </div>`;
    }
    return `<div class="pt-row pt-realfile" title="${esc(n.path || n.name)}">
      ${_ptDot(n.state || 'pending')}
      <span class="pt-icon pt-fileicon">${_PT_ICON.file}</span>
      <span class="pt-label">${esc(n.name)}</span>
    </div>`;
  }).join('');
}

function ptToggleRealDir(rowEl) {
  const branch = rowEl.closest('.pt-branch');
  const children = branch && branch.querySelector('.pt-children');
  const caret = rowEl.querySelector('.pt-caret');
  if (!children) return;
  const willOpen = children.style.display === 'none';
  children.style.display = willOpen ? '' : 'none';
  if (caret) caret.classList.toggle('open', willOpen);
}

// Re-tint the top-level dots when fresh sync state arrives (no full re-render).
function repaintProjectTreeDots() {
  document.querySelectorAll('#project-source-tree .pt-item[data-type][data-id]').forEach(row => {
    const type = row.dataset.type, id = row.dataset.id;
    const dot = row.querySelector('.pt-dot');
    if (!dot) return;
    let stateName;
    if (type === 'urls') {
      // Web URLs: derived per-URL state (no per-URL sync item) from /web-url-states.
      stateName = (state._ptUrlStates || {})[id] || 'pending';
    } else {
      const kind = type === 'files' ? 'attachment' : 'folder';
      stateName = _ptItemState(kind, id);
    }
    const s = _PT_STATE[stateName] || _PT_STATE.pending;
    dot.setAttribute('data-state', s.cls);
    dot.setAttribute('title', s.label);
  });
}
