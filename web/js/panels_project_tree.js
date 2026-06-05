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
  // Virtual (user) folder — dashed-look via a tag/bookmark-ish glyph to read as
  // "grouping", distinct from the solid real-folder icon.
  vfolder: _ptSvg('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>'),
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

// ─── Virtual-folder grouping (C3) ───────────────────────────────────────────
// state._projectDetail.source_groups[type] = {groups:[{id,name,parent,order}], assign:{itemId:groupId}}
const _PT_MAX_DEPTH = 3;

function _ptGroups(type) {
  const sg = (state._projectDetail && state._projectDetail.source_groups) || {};
  const bucket = sg[type] || {};
  return { groups: bucket.groups || [], assign: bucket.assign || {} };
}

// Depth of a group (1 = top level), walking the parent chain (cycle-safe).
function _ptGroupDepth(groups, gid, seen) {
  seen = seen || new Set();
  const g = groups.find(x => x.id === gid);
  if (!g || seen.has(gid)) return 1;
  if (!g.parent) return 1;
  return 1 + _ptGroupDepth(groups, g.parent, new Set([...seen, gid]));
}

// Persist the current source_groups to the project (debounced-ish: immediate).
async function _ptSaveGroups() {
  const agentId = state._projectDetailAgent || 'main';
  const projectName = state._projectDetailName || '';
  const sg = (state._projectDetail && state._projectDetail.source_groups) || {};
  try {
    await API.updateProject(agentId, projectName, { source_groups: sg });
  } catch (e) { showToast('Gruppierung konnte nicht gespeichert werden: ' + (e.message || e), true); }
}

function _ptEnsureBucket(type) {
  if (!state._projectDetail.source_groups) state._projectDetail.source_groups = {};
  if (!state._projectDetail.source_groups[type]) state._projectDetail.source_groups[type] = { groups: [], assign: {} };
  const b = state._projectDetail.source_groups[type];
  if (!b.groups) b.groups = [];
  if (!b.assign) b.assign = {};
  return b;
}

function _ptNewGroupId() {
  return 'g' + Math.random().toString(36).slice(2, 10);
}

// Render a type's items nested under their virtual folders. `itemsHtmlById` maps
// each item id → its rendered row HTML (built by the type filler). Returns the
// full nested HTML for #pt-items-<type>.
function _ptRenderGrouped(type, itemIds, itemsHtmlById) {
  const { groups, assign } = _ptGroups(type);
  // Children-of map: groupId (or '' root) → child group list (ordered).
  const childGroups = (parentId) => groups
    .filter(g => (g.parent || '') === parentId)
    .sort((a, b) => (a.order || 0) - (b.order || 0) || a.name.localeCompare(b.name));
  // Items assigned to a given group (or root).
  const itemsIn = (groupId) => itemIds.filter(id => (assign[id] || '') === groupId);

  const renderGroup = (g, depth) => {
    const key = `${type}/grp/${g.id}`;
    const open = _ptIsExpanded(key, false);
    const canNest = depth < _PT_MAX_DEPTH;
    const inner = childGroups(g.id).map(cg => renderGroup(cg, depth + 1)).join('')
                + itemsIn(g.id).map(id => itemsHtmlById[id] || '').join('');
    return `<div class="pt-branch pt-vgroup" data-type="${type}" data-group="${esc(g.id)}">
      <div class="pt-row pt-grouprow" data-droptarget="1" data-type="${type}" data-group="${esc(g.id)}"
           onclick="ptToggleGroup('${esc(key)}', this)">
        ${_ptCaret(open)}
        <span class="pt-icon">${_PT_ICON.vfolder}</span>
        <span class="pt-label">${esc(g.name)}</span>
        <span class="pt-actions">
          ${canNest ? `<button class="pt-act" onclick="event.stopPropagation(); ptCreateGroup('${type}','${esc(g.id)}')" title="Untergruppe">＋</button>` : ''}
          <button class="pt-act" onclick="event.stopPropagation(); ptRenameGroup('${type}','${esc(g.id)}')" title="Umbenennen">✎</button>
          <button class="pt-act" onclick="event.stopPropagation(); ptDeleteGroup('${type}','${esc(g.id)}')" title="Gruppe auflösen">✕</button>
        </span>
      </div>
      <div class="pt-children pt-groupbody" data-group-body="${esc(g.id)}" style="${open ? '' : 'display:none'}">${inner || '<div class="pt-empty">Leer — Elemente hierher ziehen.</div>'}</div>
    </div>`;
  };

  const topGroups = childGroups('').map(g => renderGroup(g, 1)).join('');
  const rootItems = itemsIn('').map(id => itemsHtmlById[id] || '').join('');
  return topGroups + rootItems;
}

// A draggable leaf item row (file / url / folder-top-node). `delCall` is the
// inline onclick for the ✕ delete button. `extra` allows folders to add a caret.
function _ptItemRow(type, id, label, iconSvg, stateName, delCall, opts) {
  opts = opts || {};
  const sel = (state._ptSelected && state._ptSelected.has(`${type}:${id}`)) ? ' pt-selected' : '';
  return `<div class="pt-row pt-item${sel}" draggable="true" data-type="${type}" data-id="${esc(id)}" title="${esc(opts.title || label)}"
       onclick="ptItemClick(event,'${type}','${esc(id)}')"
       ondragstart="ptDragStart(event,'${type}','${esc(id)}')" ondragend="ptDragEnd(event)">
      ${opts.caret || ''}
      ${stateName ? _ptDot(stateName) : ''}
      <span class="pt-icon pt-fileicon">${iconSvg}</span>
      <span class="pt-label">${esc(label)}</span>
      <span class="pt-actions">${opts.actions || ''}<button class="pt-act" onclick="event.stopPropagation(); ${delCall}" title="Entfernen">✕</button></span>
    </div>`;
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

  // Delegated drag/drop on the container (handlers check _ptDrag + type-lock).
  host.ondragover = ptDragOver;
  host.ondrop = ptDrop;

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
  const groupAction = `<button class="pt-act" onclick="event.stopPropagation(); ptCreateGroup('${type}','')" title="Neue Gruppe">⊞</button>`;
  return `
    <div class="pt-branch" data-node="${type}" data-type="${type}">
      <div class="pt-row pt-typerow" data-droptarget="1" data-type="${type}" data-group="" onclick="ptToggle('${type}')">
        ${_ptCaret(open)}
        <span class="pt-icon">${icon}</span>
        <span class="pt-label">${esc(label)}</span>
        <span class="pt-count" id="pt-count-${type}"></span>
        <span class="pt-actions">${groupAction}${addAction}</span>
      </div>
      <div class="pt-children" data-children="${type}" style="${open ? '' : 'display:none'}">
        <div class="pt-items pt-typeroot" id="pt-items-${type}" data-droptarget="1" data-type="${type}" data-group=""><div class="pt-loading">Lädt…</div></div>
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
    const ids = [], byId = {};
    for (const d of docs) {
      const id = d.source_hash || '';
      const st = _ptItemState('attachment', id);
      ids.push(id);
      byId[id] = _ptItemRow('files', id, d.source || d.name || 'Dokument', _PT_ICON.file, st,
        `deleteProjectFile('${esc(agentId)}','${esc(projectName)}','${esc(id)}')`);
    }
    host.innerHTML = _ptRenderGrouped('files', ids, byId) || '<div class="pt-empty">Noch keine Dateien.</div>';
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
    const ids = [], byId = {};
    folders.forEach((f, i) => {
      const path = f.path || '';
      const nm = path.split('/').filter(Boolean).pop() || path;
      const st = _ptItemState('folder', path);
      ids.push(path);
      // The folder's TOP NODE is the draggable item (groupable). It ALSO expands
      // to its real read-only subtree (lazy). Contents are NOT draggable.
      byId[path] = `<div class="pt-branch pt-folder" data-folder="${esc(path)}">
        <div class="pt-row pt-item pt-folderrow${(state._ptSelected && state._ptSelected.has('folders:' + path)) ? ' pt-selected' : ''}" draggable="true"
             data-type="folders" data-id="${esc(path)}" title="${esc(path)}"
             onclick="ptFolderRowClick(event, this,'${esc(agentId)}','${esc(projectName)}','${esc(path)}')"
             ondragstart="ptDragStart(event,'folders','${esc(path)}')" ondragend="ptDragEnd(event)">
          ${_ptCaret(false)}
          ${_ptDot(st)}
          <span class="pt-icon">${_PT_ICON.folders}</span>
          <span class="pt-label">${esc(nm)}</span>
          <span class="pt-actions"><button class="pt-act" onclick="event.stopPropagation(); removeProjectInputFolder(${i})" title="Entfernen">✕</button></span>
        </div>
        <div class="pt-children pt-foldertree" style="display:none"><div class="pt-loading">…</div></div>
      </div>`;
    });
    host.innerHTML = _ptRenderGrouped('folders', ids, byId) || '<div class="pt-empty">Noch keine Ordner.</div>';
  } catch (_) { host.innerHTML = '<div class="pt-empty">Ordner konnten nicht geladen werden.</div>'; }
}

function _ptFillUrls(p) {
  const host = document.getElementById('pt-items-urls');
  if (!host) return;
  const urls = p.web_urls || [];
  _ptSetCount('urls', urls.length);
  if (!urls.length) { host.innerHTML = '<div class="pt-empty">Noch keine Web-Adressen.</div>'; return; }
  const ids = [], byId = {};
  urls.forEach((u, i) => {
    const url = u.url || '';
    let host_ = url; try { host_ = new URL(url).host.replace(/^www\./, ''); } catch (_) {}
    const st = (state._ptUrlStates || {})[url] || 'pending';
    ids.push(url);
    byId[url] = _ptItemRow('urls', url, u.title || host_, _PT_ICON.urls, st,
      `removeProjectWebUrl(${i})`, { title: url });
  });
  host.innerHTML = _ptRenderGrouped('urls', ids, byId) || '<div class="pt-empty">Noch keine Web-Adressen.</div>';
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

// ─── C3: group toggle, CRUD, selection, drag/drop ───────────────────────────

function ptToggleGroup(key, rowEl) {
  const body = rowEl.parentElement.querySelector('.pt-groupbody');
  const caret = rowEl.querySelector('.pt-caret');
  if (!body) return;
  const willOpen = body.style.display === 'none';
  body.style.display = willOpen ? '' : 'none';
  if (caret) caret.classList.toggle('open', willOpen);
  _ptSetExpanded(key, willOpen);
}

async function ptCreateGroup(type, parentId) {
  const groups = _ptGroups(type).groups;
  if (parentId && _ptGroupDepth(groups, parentId) >= _PT_MAX_DEPTH) {
    showToast(`Maximal ${_PT_MAX_DEPTH} Ebenen — hier ist keine Untergruppe möglich.`, true);
    return;
  }
  const name = (prompt('Name der neuen Gruppe:') || '').trim();
  if (!name) return;
  const b = _ptEnsureBucket(type);
  b.groups.push({ id: _ptNewGroupId(), name: name.slice(0, 120), parent: parentId || '', order: b.groups.length });
  await _ptSaveGroups();
  _ptRefreshType(type);
}

async function ptRenameGroup(type, gid) {
  const b = _ptEnsureBucket(type);
  const g = b.groups.find(x => x.id === gid);
  if (!g) return;
  const name = (prompt('Gruppe umbenennen:', g.name) || '').trim();
  if (!name) return;
  g.name = name.slice(0, 120);
  await _ptSaveGroups();
  _ptRefreshType(type);
}

// Dissolve a group: its items + child groups move up to the group's parent
// (never deletes the underlying sources — only the virtual folder).
async function ptDeleteGroup(type, gid) {
  const b = _ptEnsureBucket(type);
  const g = b.groups.find(x => x.id === gid);
  if (!g) return;
  const parent = g.parent || '';
  b.groups = b.groups.filter(x => x.id !== gid);
  b.groups.forEach(x => { if (x.parent === gid) x.parent = parent; });
  for (const k of Object.keys(b.assign)) { if (b.assign[k] === gid) b.assign[k] = parent; }
  // Clean empty-string assignments (root) for tidiness.
  for (const k of Object.keys(b.assign)) { if (!b.assign[k]) delete b.assign[k]; }
  await _ptSaveGroups();
  _ptRefreshType(type);
}

// Refresh just one type branch (re-fetch its items + re-render grouped).
function _ptRefreshType(type) {
  const agentId = state._projectDetailAgent || 'main';
  const projectName = state._projectDetailName || '';
  if (type === 'files') _ptFillFiles(agentId, projectName);
  else if (type === 'folders') _ptFillFolders(agentId, projectName);
  else _ptFillUrls(state._projectDetail || {});
}

// ── Multi-select ──
function _ptSelKey(type, id) { return `${type}:${id}`; }
function ptItemClick(ev, type, id) {
  if (!state._ptSelected) state._ptSelected = new Set();
  const k = _ptSelKey(type, id);
  if (ev.metaKey || ev.ctrlKey) {
    if (state._ptSelected.has(k)) state._ptSelected.delete(k); else state._ptSelected.add(k);
  } else {
    // plain click on a different-type selection clears it; selecting one item.
    const sameType = [...state._ptSelected].every(x => x.startsWith(type + ':'));
    state._ptSelected.clear();
    if (sameType) state._ptSelected.add(k); else state._ptSelected.add(k);
  }
  _ptPaintSelection();
}

function _ptPaintSelection() {
  document.querySelectorAll('#project-source-tree .pt-item[data-type][data-id]').forEach(row => {
    const k = _ptSelKey(row.dataset.type, row.dataset.id);
    row.classList.toggle('pt-selected', !!(state._ptSelected && state._ptSelected.has(k)));
  });
}

// Folder rows: caret toggles the real subtree; clicking the row body selects.
function ptFolderRowClick(ev, rowEl, agentId, projectName, folderPath) {
  if (ev.target.closest('.pt-caret')) {
    ptToggleFolder(rowEl, agentId, projectName, folderPath);
  } else {
    ptItemClick(ev, 'folders', folderPath);
  }
}

// ── Drag / drop ──
function ptDragStart(ev, type, id) {
  if (!state._ptSelected) state._ptSelected = new Set();
  const k = _ptSelKey(type, id);
  // If dragging an unselected item, that item becomes the (sole) drag set.
  if (!state._ptSelected.has(k)) { state._ptSelected.clear(); state._ptSelected.add(k); _ptPaintSelection(); }
  // Only same-type items travel together (type-locked).
  const ids = [...state._ptSelected].filter(x => x.startsWith(type + ':')).map(x => x.slice(type.length + 1));
  state._ptDrag = { type, ids };
  ev.dataTransfer.effectAllowed = 'move';
  try { ev.dataTransfer.setData('text/plain', type + '\n' + ids.join('\n')); } catch (_) {}
  document.querySelectorAll('#project-source-tree [data-droptarget]').forEach(t => {
    if (t.dataset.type === type) t.classList.add('pt-droparmed');
  });
}

function ptDragEnd() {
  state._ptDrag = null;
  document.querySelectorAll('#project-source-tree .pt-droparmed, #project-source-tree .pt-dropover')
    .forEach(t => t.classList.remove('pt-droparmed', 'pt-dropover'));
}

// Delegated dragover/drop on the tree container (set up once in renderProjectSourceTree).
function ptDragOver(ev) {
  const tgt = ev.target.closest('[data-droptarget]');
  const d = state._ptDrag;
  if (!tgt || !d || tgt.dataset.type !== d.type) return;
  ev.preventDefault();
  ev.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('#project-source-tree .pt-dropover').forEach(t => t.classList.remove('pt-dropover'));
  tgt.classList.add('pt-dropover');
}

async function ptDrop(ev) {
  const tgt = ev.target.closest('[data-droptarget]');
  const d = state._ptDrag;
  if (!tgt || !d || tgt.dataset.type !== d.type) { ptDragEnd(); return; }
  ev.preventDefault();
  const groupId = tgt.dataset.group || '';
  const b = _ptEnsureBucket(d.type);
  for (const id of d.ids) {
    if (groupId) b.assign[id] = groupId; else delete b.assign[id];
  }
  ptDragEnd();
  if (state._ptSelected) state._ptSelected.clear();
  await _ptSaveGroups();
  _ptRefreshType(d.type);
}
