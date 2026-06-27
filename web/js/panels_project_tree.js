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
  not_indexed: { cls: 'error', label: 'Nicht indexiert (sollte sein)' },
  not_source:  { cls: 'muted', label: 'Keine Quelldatei (wird nicht indexiert)' },
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
  // link (chain) — "find linked documents on these pages"
  link: _ptSvg('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'),
  // external-link (open in new tab)
  external: _ptSvg('<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>'),
};

// Open a project web-URL in a new browser tab (used by the row click + the ↗
// action). noopener/noreferrer for safety on outbound links.
function ptOpenWebUrl(url) {
  if (!url) return;
  window.open(url, '_blank', 'noopener,noreferrer');
}

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

// Per-file KG badge for the source tree. `kg` ∈ kg|skipped|empty|none.
// Combined with `mined` so the operator sees the full per-document state:
//   indexed + kg      → "Gemined + KG" (green KG pill)
//   indexed + skipped → "KG übersprungen (DSGVO/Klassifizierung)" (amber)
//   indexed + empty   → "Gemined, keine Triples" (grey)
//   indexed + none    → "Gemined" (no KG pill — KG not run for this doc)
//   pending           → not mined yet (no KG pill)
const _PT_KG = {
  kg:      { c: 'var(--success)', t: 'KG', label: 'Knowledge-Graph-Triples extrahiert' },
  skipped: { c: '#d9a000',        t: 'KG⊘', label: 'KG übersprungen' },
  empty:   { c: 'var(--text-500)', t: 'KG·', label: 'Gemined, keine extrahierbaren Triples' },
};
function _ptKgBadge(node) {
  const kg = node && node.kg;
  const info = _PT_KG[kg];
  if (!info) return '';  // 'none'/missing → no badge
  let tip = info.label;
  if (kg === 'skipped' && node.skip_reason) {
    const r = String(node.skip_reason).replace('gdpr_', 'DSGVO: ').replace('classification', 'Klassifizierung');
    tip = `KG-Extraktion übersprungen — ${r}. Das Dokument würde durch DSGVO/Klassifizierung blockiert oder anonymisiert; eine Extraktion würde verfälschte Triples liefern.`;
  }
  return `<span class="pt-kgbadge" title="${esc(tip)}" style="font-size:9px;padding:1px 4px;margin-left:4px;border-radius:3px;border:1px solid ${info.c};color:${info.c};white-space:nowrap">${esc(info.t)}</span>`;
}

// GDPR/classification review badge for a file node. `rev` ∈
// {anonymised, violations, checked} or falsy. Badge-only (does NOT change the
// mined/KG status dot), per the design decision.
const _PT_REVIEW = {
  anonymised: { icon: '🛡️', cls: 'review-badge-anonymised', tip: 'Anonymisiert — das LLM erhält die anonymisierte Version. Rechtsklick zum Verwalten.' },
  violations: { icon: '⚠️', cls: 'review-badge-violations', tip: 'GDPR/Klassifizierungs-Verstöße gefunden. Rechtsklick zum Prüfen.' },
  checked:    { icon: '✓', cls: 'review-badge-checked', tip: 'Geprüft — keine offenen Verstöße.' },
};
function _ptReviewBadge(rev) {
  const info = _PT_REVIEW[rev];
  if (!info) return '';
  return `<span class="review-badge ${info.cls}" title="${esc(info.tip)}">${info.icon}</span>`;
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
          <button class="pt-act" onclick="event.stopPropagation(); ptDeleteGroup('${type}','${esc(g.id)}')" title="Gruppe auflösen (Inhalte bleiben erhalten)">✕</button>
          <button class="pt-act" onclick="event.stopPropagation(); ptDeleteGroupWithContents('${type}','${esc(g.id)}')" title="Gruppe inkl. aller enthaltenen Dokumente löschen">🗑️</button>
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
  const ctxAttr = opts.ctx ? ` oncontextmenu="${opts.ctx}"` : '';
  return `<div class="pt-row pt-item${sel}" draggable="true" data-type="${type}" data-id="${esc(id)}" title="${esc(opts.title || label)}"
       onclick="ptItemClick(event,'${type}','${esc(id)}')"${ctxAttr}
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
  // Escape clears the multi-selection (wired once, idempotent).
  if (!window._ptEscWired) {
    window._ptEscWired = true;
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && state._ptSelected && state._ptSelected.size
          && document.getElementById('project-source-tree')) {
        state._ptSelected.clear();
        _ptPaintSelection();
      }
    });
  }

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
      + `<label class="pt-act" title="Ordner hinzufügen (Struktur als Gruppen übernommen)" onclick="event.stopPropagation()">📁<input type="file" webkitdirectory directory multiple style="display:none" onchange="pickProjectFolder(this)"></label>`
    : type === 'folders'
      ? `<button class="pt-act" onclick="event.stopPropagation(); addProjectInputFolder()" title="Ordner hinzufügen">＋</button>`
      : `<button class="pt-act" onclick="event.stopPropagation(); discoverProjectWebLinks()" title="Verlinkte Dokumente auf diesen Seiten finden">${_PT_ICON.link || '🔗'}</button><button class="pt-act" onclick="event.stopPropagation(); addProjectWebUrl()" title="Web-Adresse hinzufügen">＋</button>`;
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
      // source may be a relative path ("Kunde-A/Bericht.pdf") for folder
      // imports — show only the basename, keep the full path as tooltip.
      const full = d.source || d.name || 'Dokument';
      const label = String(full).replace(/\\/g, '/').split('/').pop() || full;
      ids.push(id);
      byId[id] = _ptItemRow('files', id, label, _PT_ICON.file, st,
        `deleteProjectFile('${esc(agentId)}','${esc(projectName)}','${esc(id)}')`,
        {
          title: full,
          actions: _ptReviewBadge(d.review),
          ctx: `ptReviewMenu(event, {agentId:'${esc(agentId)}',project:'${esc(projectName)}',sourceHash:'${esc(id)}'})`,
        });
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
             oncontextmenu="ptReviewMenu(event, {agentId:'${esc(agentId)}',project:'${esc(projectName)}',folder:'${esc(path)}'})"
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
    const openAct = `<button class="pt-act" onclick="event.stopPropagation(); ptOpenWebUrl('${esc(url)}')" title="In neuem Tab öffnen">${_PT_ICON.external || '↗'}</button>`;
    byId[url] = _ptItemRow('urls', url, u.title || host_, _PT_ICON.urls, st,
      `removeProjectWebUrl(${i})`, { title: url, actions: openAct });
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
    return `<div class="pt-row pt-realfile" title="${esc(n.path || n.name)}"
         oncontextmenu="ptReviewMenu(event, {path:'${esc(n.path || '')}'})">
      ${_ptDot(n.state || 'pending')}
      <span class="pt-icon pt-fileicon">${_PT_ICON.file}</span>
      <span class="pt-label">${esc(n.name)}</span>
      ${_ptKgBadge(n)}
      ${_ptReviewBadge(n.review)}
    </div>`;
  }).join('');
}

// Code-mode variant: render the working-directory tree WITHOUT MemPalace status
// dots / KG / review badges (a code project has no project memory — files are
// just files). Same collapse/expand interaction (ptToggleRealDir).
// Strip the project's working-dir prefix off an absolute file path → the
// repo-relative path used to key state._codeIndexFiles. Falls back to basename.
function _ptRelToWorkingDir(absPath) {
  if (!absPath) return '';
  const wd = (state._projectDetail && state._projectDetail.working_dir) || '';
  if (wd && absPath.indexOf(wd) === 0) {
    return absPath.slice(wd.length).replace(/^\/+/, '');
  }
  return absPath.split('/').pop();
}

function _ptRenderCodeTree(nodes, openDirs) {
  if (!nodes || !nodes.length) return '<div class="pt-empty">Leer.</div>';
  openDirs = openDirs || new Set();
  return nodes.map(n => {
    if (n.type === 'dir') {
      const isOpen = openDirs.has(n.path || '');
      return `<div class="pt-branch pt-realdir">
        <div class="pt-row pt-realrow" onclick="ptToggleRealDir(this)">
          ${_ptCaret(isOpen)}
          <span class="pt-icon">${_PT_ICON.folders}</span>
          <span class="pt-label pt-realdir-path" data-path="${esc(n.path || '')}">${esc(n.name)}</span>
        </div>
        <div class="pt-children" style="display:${isOpen ? '' : 'none'}">${_ptRenderCodeTree(n.children || [], openDirs)}</div>
      </div>`;
    }
    // _codeIndexFiles is keyed by repo-RELATIVE path; the folder-tree gives an
    // ABSOLUTE n.path. Strip the working-dir prefix to match.
    const _ci = state._codeIndexFiles && state._codeIndexFiles[_ptRelToWorkingDir(n.path)];
    const _dot = _ci ? _ptDot(_ci.state) : '';
    const _nodes = _ci && _ci.nodes ? ` <span class="pt-count" style="color:var(--text-400);font-size:10px">${_ci.nodes}</span>` : '';
    return `<div class="pt-row pt-realfile" title="${esc(n.path || n.name)}" data-filepath="${esc(n.path || '')}" onclick="terminalOpenFile(this.dataset.filepath)" style="cursor:pointer">
      <span class="pt-icon pt-fileicon">${_PT_ICON.file}</span>
      <span class="pt-label">${esc(n.name)}</span>${_nodes}${_dot}
    </div>`;
  }).join('');
}

// Load + render the code-mode project's working directory tree. Called when the
// project detail opens and after each turn (files may have changed).
// Stable signature of a code tree (path|size|mtime per file, recursive) so a
// poll can re-render ONLY when files were added / modified / deleted.
function _ptCodeTreeSig(nodes) {
  const parts = [];
  const walk = (ns) => (ns || []).forEach(n => {
    if (n.type === 'dir') { parts.push('d:' + (n.path || n.name)); walk(n.children || []); }
    else parts.push('f:' + (n.path || n.name) + ':' + (n.size || 0) + ':' + (n.mtime || 0));
  });
  walk(nodes);
  return parts.join('\n');
}

// Set of currently-open dir paths in the rendered code tree, so a re-render
// keeps the user's expansion state instead of collapsing everything.
function _ptCodeTreeOpenDirs(host) {
  const open = new Set();
  if (!host) return open;
  host.querySelectorAll('.pt-realdir').forEach(branch => {
    const kids = branch.querySelector(':scope > .pt-children');
    const lbl = branch.querySelector(':scope > .pt-row .pt-realdir-path');
    if (kids && lbl && kids.style.display !== 'none') open.add(lbl.dataset.path || '');
  });
  return open;
}

async function refreshCodeWorkingTree(opts) {
  opts = opts || {};
  const host = document.getElementById('project-codemode-tree');
  if (!host) return;
  const agentId = state._projectDetailAgent || 'main';
  const projectName = state._projectDetailName || '';
  const wd = state._projectDetail && state._projectDetail.working_dir;
  if (!wd) {
    host.innerHTML = '<div class="pt-empty">Kein Arbeitsverzeichnis gewählt.</div>';
    state._codeTreeSig = '';
    return;
  }
  // Silent polls keep the existing tree on screen (no "Lädt…" flicker).
  if (!opts.silent) host.innerHTML = '<div class="pt-loading">Lädt…</div>';
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/folder-tree?path=${encodeURIComponent(wd)}`);
    if (data.error) {
      if (!opts.silent) host.innerHTML = `<div class="pt-empty">${esc(data.error)}</div>`;
      return;
    }
    const tree = data.tree || [];
    const sig = _ptCodeTreeSig(tree);
    // No change since last render → leave the DOM (and expansion state) alone.
    // `force` (used after an index-status refresh) re-renders anyway so the
    // per-file index dots repaint even when the file set itself is unchanged.
    if (opts.silent && !opts.force && sig === state._codeTreeSig) return;
    const openDirs = _ptCodeTreeOpenDirs(host);
    host.innerHTML = _ptRenderCodeTree(tree, openDirs);
    state._codeTreeSig = sig;
  } catch (e) {
    if (!opts.silent) host.innerHTML = '<div class="pt-empty">Verzeichnis konnte nicht geladen werden.</div>';
  }
}

// ── File preview modal (click a file in the code-mode tree) ──────────────────
// Maps a file extension → a highlight.js language id (falls back to plaintext).
function _ptLangFor(ext) {
  ext = (ext || '').toLowerCase();
  const alias = { py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript',
    ts: 'typescript', jsx: 'javascript', tsx: 'typescript', rb: 'ruby', rs: 'rust',
    kt: 'kotlin', sh: 'bash', yml: 'yaml', md: 'markdown', htm: 'html' };
  const lang = alias[ext] || ext;
  return (typeof hljs !== 'undefined' && hljs.getLanguage(lang)) ? lang : 'plaintext';
}

// Trigger an authenticated browser download of one file via /v1/files/download.
async function _ptDownloadFile(absPath, name) {
  try {
    const resp = await fetch(`${BASE_URL}/v1/files/download?path=${encodeURIComponent(absPath)}`,
      { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') } });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name || (absPath.split('/').pop());
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { if (typeof showToast === 'function') showToast('Download fehlgeschlagen'); }
}

// (File preview modal removed in favour of the bottom-panel code editor — a
// click on a file now opens it as an editor tab via terminalOpenFile().)

// Download the whole working-dir tree as a ZIP.
async function ptDownloadProjectZip() {
  const wd = state._projectDetail && state._projectDetail.working_dir;
  if (!wd) { if (typeof showToast === 'function') showToast('Kein Arbeitsverzeichnis'); return; }
  if (typeof showToast === 'function') showToast('ZIP wird erstellt…');
  try {
    const resp = await fetch(`${BASE_URL}/v1/files/zip?path=${encodeURIComponent(wd)}`,
      { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') } });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = (wd.split('/').pop() || 'projekt') + '.zip';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) { if (typeof showToast === 'function') showToast('ZIP-Download fehlgeschlagen'); }
}

// ── Code-Index (codebase-memory) management ──────────────────────────────────
// Polls /code-index/status, paints the status chip + caches per-file state for
// the working-dir tree dots. Mirrors the project-sync chip pattern.
function _codeIndexCtx() {
  return {
    agentId: state._projectDetailAgent || 'main',
    projectName: state._projectDetailName || '',
  };
}

async function refreshCodeIndexStatus() {
  const { agentId, projectName } = _codeIndexCtx();
  if (!projectName) return;
  const chip = document.getElementById('code-index-chip');
  const label = document.getElementById('code-index-label');
  const sub = document.getElementById('code-index-sublabel');
  try {
    const d = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/code-index/status`);
    if (d.error) return;
    // cache per-file state (keyed by relpath) for the tree render
    state._codeIndexFiles = d.files || {};
    const live = d.live || {};
    const liveState = live.state;
    // Chip data-state mirrors MemPalace: idle (green) / syncing (pulsing accent)
    // / error (red). End-user friendly wording — no "nodes/edges" jargon.
    let chipState = 'idle', txt = 'Code-Index', subtxt = '';
    if (liveState === 'indexing') {
      chipState = 'syncing'; txt = 'Code wird eingelesen …';
      subtxt = 'Die Suche ist gleich einsatzbereit.';
    } else if (liveState === 'error') {
      chipState = 'error'; txt = 'Einlesen fehlgeschlagen';
      subtxt = 'Der Code konnte nicht vollständig erfasst werden.';
    } else if (!d.indexed) {
      chipState = 'syncing'; txt = 'Noch nicht eingelesen';
      subtxt = 'Der Code wird beim ersten Mal automatisch erfasst.';
    } else {
      const fileVals = d.files ? Object.values(d.files) : [];
      const nSource = fileVals.filter(f => f.state !== 'not_source').length;
      const nStale = fileVals.filter(f => f.state === 'stale').length;
      const nMiss = fileVals.filter(f => f.state === 'not_indexed').length;
      if (nStale || nMiss) {
        // something is out of date / missing → amber-ish "needs attention"
        chipState = 'syncing'; txt = 'Aktualisierung nötig';
        const bits = [];
        if (nStale) bits.push(`${nStale} ${nStale === 1 ? 'Datei wurde' : 'Dateien wurden'} geändert`);
        if (nMiss) bits.push(`${nMiss} ${nMiss === 1 ? 'Datei fehlt' : 'Dateien fehlen'} noch`);
        subtxt = bits.join(' · ') + ' — auf „Aktualisieren“ klicken.';
      } else {
        chipState = 'idle'; txt = 'Code durchsuchbar';
        subtxt = `${nSource} ${nSource === 1 ? 'Datei' : 'Dateien'} erfasst — der Assistent kennt den Code.`;
      }
    }
    if (live.error) subtxt = 'Der Code konnte nicht vollständig erfasst werden.';
    if (chip) chip.dataset.state = chipState;
    if (label) label.textContent = txt;
    if (sub) sub.textContent = subtxt;
    // repaint the file tree dots from the fresh cache (force: file set unchanged
    // but the index state may have, so re-render to repaint the per-file dots)
    refreshCodeWorkingTree({ silent: true, force: true });
  } catch (e) { /* transient */ }
}

let _codeIndexPollTimer = null;
function startCodeIndexPoll() {
  stopCodeIndexPoll();
  refreshCodeIndexStatus();
  _codeIndexPollTimer = setInterval(refreshCodeIndexStatus, 5000);
}
function stopCodeIndexPoll() {
  if (_codeIndexPollTimer) { clearInterval(_codeIndexPollTimer); _codeIndexPollTimer = null; }
}

async function codeIndexRefresh() {
  const { agentId, projectName } = _codeIndexCtx();
  if (!projectName) return;
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/code-index/refresh`, {});
    showToast && showToast('Index-Aktualisierung angestoßen');
    setTimeout(refreshCodeIndexStatus, 800);
  } catch (e) { showToast && showToast('Fehler beim Anstoßen'); }
}

async function codeIndexRebuild() {
  const { agentId, projectName } = _codeIndexCtx();
  if (!projectName) return;
  if (!confirm('Code-Index verwerfen und komplett neu aufbauen?')) return;
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/code-index/rebuild`, {});
    showToast && showToast('Index wird neu aufgebaut');
    setTimeout(refreshCodeIndexStatus, 800);
  } catch (e) { showToast && showToast('Fehler beim Neuaufbau'); }
}

// Small horizontal bar for a labeled count (used in the graph overview).
function _ciBar(label, count, max, color) {
  const pct = max > 0 ? Math.round((count / max) * 100) : 0;
  return `<div class="ci-bar-row">
    <span class="ci-bar-label">${esc(label)}</span>
    <span class="ci-bar-track"><span class="ci-bar-fill" style="width:${pct}%;background:${color || 'var(--accent-brand)'}"></span></span>
    <span class="ci-bar-count">${count}</span>
  </div>`;
}

async function codeIndexGraph() {
  const { agentId, projectName } = _codeIndexCtx();
  if (!projectName) return;
  try {
    const d = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/code-index/graph`);
    if (!d.indexed) { if (typeof showToast === 'function') showToast('Noch kein Index vorhanden'); return; }
    const a = d.architecture || {};
    const labels = a.node_labels || [];
    const edges = a.edge_types || [];
    const langs = a.languages || [];
    const pkgs = a.packages || [];
    const entries = a.entry_points || [];
    const hotspots = a.hotspots || [];
    const maxL = Math.max(1, ...labels.map(x => x.count || 0));
    const maxE = Math.max(1, ...edges.map(x => x.count || 0));
    const chip = (t) => `<span class="ci-chip">${esc(t)}</span>`;
    const section = (title, html) => html ? `<div class="ci-sec"><div class="ci-sec-title">${esc(title)}</div>${html}</div>` : '';
    const body = `
      <div class="ci-graph-head">
        <div class="ci-stat"><div class="ci-stat-num">${a.total_nodes != null ? a.total_nodes : (d.nodes || '?')}</div><div class="ci-stat-lbl">Symbole</div></div>
        <div class="ci-stat"><div class="ci-stat-num">${a.total_edges != null ? a.total_edges : (d.edges || '?')}</div><div class="ci-stat-lbl">Verknüpfungen</div></div>
        <div class="ci-stat"><div class="ci-stat-num">${langs.length}</div><div class="ci-stat-lbl">Sprachen</div></div>
        <div class="ci-stat"><div class="ci-stat-num">${pkgs.length}</div><div class="ci-stat-lbl">Pakete</div></div>
      </div>
      ${langs.length ? section('Sprachen', langs.map(l => chip(typeof l === 'string' ? l : (l.name || l.language || ''))).join('')) : ''}
      ${labels.length ? section('Symbol-Typen', labels.slice(0, 10).map(x => _ciBar(x.label, x.count || 0, maxL)).join('')) : ''}
      ${edges.length ? section('Beziehungen', edges.slice(0, 10).map(x => _ciBar(x.type, x.count || 0, maxE, '#8b5cf6')).join('')) : ''}
      ${entries.length ? section('Einstiegspunkte', entries.slice(0, 12).map(e => chip(typeof e === 'string' ? e : (e.name || e.qualified_name || ''))).join('')) : ''}
      ${hotspots.length ? section('Hotspots (am stärksten vernetzt)', hotspots.slice(0, 10).map(h => `<div class="ci-hotspot">${esc(typeof h === 'string' ? h : (h.name || h.qualified_name || ''))}${h.degree != null ? ` <span class="ci-deg">${h.degree}</span>` : ''}</div>`).join('')) : ''}
    `;
    _codeIndexShowModal('Code-Graph · Architektur', body);
  } catch (e) { if (typeof showToast === 'function') showToast('Graph konnte nicht geladen werden'); }
}

async function codeIndexHistory() {
  const { agentId, projectName } = _codeIndexCtx();
  if (!projectName) return;
  _codeIndexShowModal('Index-Verlauf', '<div class="pt-loading">Lädt…</div>');
  try {
    const d = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/code-index/history`);
    const runs = (d && d.runs) || [];
    if (!runs.length) {
      _codeIndexShowModal('Index-Verlauf', '<p style="color:var(--text-400);font-size:.85rem">Noch keine Index-Läufe aufgezeichnet.</p>');
      return;
    }
    const trig = { auto: 'Automatisch', manual: 'Manuell', full_rebuild: 'Neuaufbau' };
    const rows = runs.map(r => {
      const ok = r.state === 'indexed';
      const when = r.finished_at ? new Date(r.finished_at * 1000).toLocaleString() : '—';
      const dot = `<span class="project-sync-dot" style="background:${ok ? '#3fb950' : '#f85149'};margin-top:6px"></span>`;
      const meta = ok
        ? `${r.nodes != null ? r.nodes : '?'} Symbole · ${r.edges != null ? r.edges : '?'} Verknüpfungen`
        : `<span style="color:var(--danger)">${esc(r.error || 'Fehler')}</span>`;
      return `<div class="ci-run">
        ${dot}
        <div class="ci-run-body">
          <div class="ci-run-top"><b>${ok ? 'Indexiert' : 'Fehler'}</b>
            <span class="ci-chip">${esc(trig[r.trigger] || r.trigger || '')}</span>
            ${r.duration != null ? `<span class="ci-run-dur">${r.duration}s</span>` : ''}</div>
          <div class="ci-run-meta">${meta}</div>
          <div class="ci-run-when">${esc(when)}</div>
        </div>
      </div>`;
    }).join('');
    _codeIndexShowModal('Index-Verlauf', `<div class="ci-runs">${rows}</div>`);
  } catch (e) {
    _codeIndexShowModal('Index-Verlauf', '<p style="color:var(--danger)">Verlauf konnte nicht geladen werden.</p>');
  }
}

// Minimal reusable modal (reuses the app's modal styles if present, else inline).
function _codeIndexShowModal(title, html) {
  let m = document.getElementById('code-index-modal');
  if (!m) {
    m = document.createElement('div');
    m.id = 'code-index-modal';
    m.className = 'modal-overlay';
    m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:9999';
    m.onclick = (ev) => { if (ev.target === m) m.remove(); };
    document.body.appendChild(m);
  }
  m.innerHTML = `<div style="background:var(--bg-000);border:1px solid var(--border-100);border-radius:10px;max-width:720px;width:90%;padding:18px;box-shadow:0 10px 40px rgba(0,0,0,.3)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <h3 style="margin:0;font-size:15px">${esc(title)}</h3>
      <button class="pt-act" type="button" onclick="document.getElementById('code-index-modal').remove()">✕</button>
    </div>${html}</div>`;
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
  const name = ((await showPrompt('Name der neuen Gruppe:', '', 'Neue Gruppe')) || '').trim();
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
  const name = ((await showPrompt('Gruppe umbenennen:', g.name, 'Gruppe umbenennen')) || '').trim();
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

// Collect a group + ALL its descendant subgroups (ids), recursively.
function _ptGroupSubtreeIds(groups, gid) {
  const ids = [gid];
  const kids = groups.filter(x => (x.parent || '') === gid);
  for (const k of kids) ids.push(..._ptGroupSubtreeIds(groups, k.id));
  return ids;
}

// "Gruppe inkl. Inhalte löschen": removes the group, every nested subgroup,
// AND every document/folder/URL assigned anywhere in that subtree. This
// DELETES the underlying sources (unlike ptDeleteGroup, which only dissolves
// the virtual grouping). Confirmation shows the count first.
async function ptDeleteGroupWithContents(type, gid) {
  const b = _ptEnsureBucket(type);
  const g = b.groups.find(x => x.id === gid);
  if (!g) return;
  const subtree = new Set(_ptGroupSubtreeIds(b.groups, gid));
  // Member ids assigned to any group in the subtree (files: source_hash;
  // folders: path; urls: the url string).
  const memberIds = Object.keys(b.assign).filter(k => subtree.has(b.assign[k]));
  const labelType = type === 'files' ? 'Dokument(e)' : type === 'folders' ? 'Ordner' : 'Web-Adresse(n)';
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  const subgroupCount = subtree.size - 1;
  overlay.innerHTML = `<div class="sched-modal" style="max-width:520px">
    <h2 style="display:flex;align-items:center;gap:8px">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#d33" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      Gruppe „${esc(g.name)}" inkl. Inhalte löschen?
    </h2>
    <div style="font-size:13px;color:var(--text-300);line-height:1.5;margin:8px 0 16px">
      Dauerhaft entfernt werden:
      <ul style="margin:8px 0 0;padding-left:18px">
        <li><strong>${memberIds.length}</strong> ${esc(labelType)}</li>
        ${subgroupCount > 0 ? `<li>diese Gruppe + <strong>${subgroupCount}</strong> Untergruppe(n)</li>` : `<li>diese Gruppe</li>`}
      </ul>
      <div style="margin-top:10px">Die zugehörigen Quellen werden aus dem Projekt und seinem Speicher gelöscht. Das lässt sich nicht rückgängig machen.</div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" style="background:#d33;border-color:#d33" onclick="_ptConfirmDeleteGroupWithContents('${esc(type)}','${esc(gid)}')">Endgültig löschen</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

async function _ptConfirmDeleteGroupWithContents(type, gid) {
  document.querySelector('.sched-modal-overlay')?.remove();
  const agentId = state._projectDetailAgent || 'main';
  const projectName = state._projectDetailName || '';
  if (!agentId || !projectName) return;
  const b = _ptEnsureBucket(type);
  const g = b.groups.find(x => x.id === gid);
  if (!g) return;
  const subtree = new Set(_ptGroupSubtreeIds(b.groups, gid));
  const memberIds = Object.keys(b.assign).filter(k => subtree.has(b.assign[k]));

  // Delete the underlying sources per type. Best-effort per item; a failure is
  // toasted but doesn't abort the rest.
  let failed = 0;
  if (type === 'files') {
    for (const sourceHash of memberIds) {
      try {
        await API.del(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/docs/${encodeURIComponent(sourceHash)}`);
      } catch (e) { failed++; }
    }
  } else if (type === 'folders') {
    // input-folders delete is index-based; resolve each path→current index
    // fresh before each delete (indices shift as we remove).
    for (const path of memberIds) {
      try {
        const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders`);
        const list = (data && (data.folders || data.input_folders)) || [];
        const idx = list.findIndex(f => (f.path || '') === path);
        if (idx >= 0) await API.del(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders/${idx}`);
      } catch (e) { failed++; }
    }
  } else if (type === 'urls') {
    // web_urls: drop the matching urls in one save.
    const remaining = (state._projectDetail?.web_urls || []).filter(u => !memberIds.includes(u.url));
    try {
      if (typeof _saveProjectWebUrls === 'function') await _saveProjectWebUrls(remaining);
      else { if (state._projectDetail) state._projectDetail.web_urls = remaining;
             await API.updateProject(agentId, projectName, { web_urls: remaining }); }
    } catch (e) { failed += memberIds.length; }
  }

  // Drop the subtree groups + their assignments from source_groups.
  b.groups = b.groups.filter(x => !subtree.has(x.id));
  for (const k of Object.keys(b.assign)) { if (subtree.has(b.assign[k])) delete b.assign[k]; }
  await _ptSaveGroups();
  showToast(failed ? `Gruppe gelöscht, ${failed} Element(e) fehlgeschlagen` : 'Gruppe inkl. Inhalte gelöscht');
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
  // Web-URL rows: a PLAIN click opens the page in a new tab (the id IS the URL).
  // Modifier-clicks still fall through to multi-selection (drag-to-group etc.).
  if (type === 'urls' && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) {
    ptOpenWebUrl(id);
    return;
  }
  if (!state._ptSelected) state._ptSelected = new Set();
  const k = _ptSelKey(type, id);
  if (ev.metaKey || ev.ctrlKey) {
    // toggle into a same-type-only multi-selection (mixing types is meaningless
    // since groups are type-locked; a cmd-click of another type starts fresh).
    if (![...state._ptSelected].every(x => x.startsWith(type + ':'))) state._ptSelected.clear();
    if (state._ptSelected.has(k)) state._ptSelected.delete(k); else state._ptSelected.add(k);
  } else {
    state._ptSelected.clear();
    state._ptSelected.add(k);
  }
  _ptPaintSelection();
}

function _ptPaintSelection() {
  document.querySelectorAll('#project-source-tree .pt-item[data-type][data-id]').forEach(row => {
    const k = _ptSelKey(row.dataset.type, row.dataset.id);
    row.classList.toggle('pt-selected', !!(state._ptSelected && state._ptSelected.has(k)));
  });
  // Selection-count chip in the legend bar.
  const n = (state._ptSelected && state._ptSelected.size) || 0;
  let chip = document.getElementById('pt-selcount');
  const legend = document.querySelector('#project-source-tree .pt-legend');
  if (n > 1 && legend) {
    if (!chip) {
      chip = document.createElement('span');
      chip.id = 'pt-selcount';
      chip.className = 'pt-selcount';
      legend.appendChild(chip);
    }
    chip.textContent = `${n} ausgewählt · ziehen oder Esc`;
  } else if (chip) {
    chip.remove();
  }
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
  // Multi-select: drag a small count badge instead of a single row ghost.
  if (ids.length > 1) {
    const ghost = document.createElement('div');
    ghost.className = 'pt-dragghost';
    ghost.textContent = `${ids.length} Elemente`;
    document.body.appendChild(ghost);
    try { ev.dataTransfer.setDragImage(ghost, 10, 10); } catch (_) {}
    setTimeout(() => ghost.remove(), 0);
  }
  document.querySelectorAll('#project-source-tree [data-droptarget]').forEach(t => {
    if (t.dataset.type === type) t.classList.add('pt-droparmed');
  });
}

function ptDragEnd() {
  state._ptDrag = null;
  document.querySelectorAll('#project-source-tree .pt-droparmed, #project-source-tree .pt-dropover')
    .forEach(t => t.classList.remove('pt-droparmed', 'pt-dropover'));
}

// True when a drag carries OS files (from the desktop) rather than an internal
// item re-group. Such a drop onto the Dateien branch ingests the files (and,
// for a folder, preserves structure as groups).
function _ptIsOsFileDrag(ev) {
  const t = ev.dataTransfer && ev.dataTransfer.types;
  return !!(t && (Array.from(t).includes('Files')));
}

// Delegated dragover/drop on the tree container (set up once in renderProjectSourceTree).
function ptDragOver(ev) {
  const tgt = ev.target.closest('[data-droptarget]');
  // OS-file drop onto the Dateien branch → allow (ingest path).
  if (tgt && tgt.dataset.type === 'files' && !state._ptDrag && _ptIsOsFileDrag(ev)) {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
    document.querySelectorAll('#project-source-tree .pt-dropover').forEach(t => t.classList.remove('pt-dropover'));
    tgt.classList.add('pt-dropover');
    return;
  }
  const d = state._ptDrag;
  if (!tgt || !d || tgt.dataset.type !== d.type) return;
  ev.preventDefault();
  ev.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('#project-source-tree .pt-dropover').forEach(t => t.classList.remove('pt-dropover'));
  tgt.classList.add('pt-dropover');
}

// Recursively collect { file, relPath } under a browser FileSystemEntry,
// rooting relPath at the dropped entry's own name so a folder's structure is
// preserved. readEntries returns ~100 entries/call, so drain it in a loop.
function _ptCollectEntryFiles(entry, prefix) {
  return new Promise((resolve) => {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isFile) {
      entry.file((file) => resolve([{ file, relPath: rel }]), () => resolve([]));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const all = [];
      const readBatch = () => {
        reader.readEntries(async (batch) => {
          if (!batch.length) {
            const nested = await Promise.all(all.map(e => _ptCollectEntryFiles(e, rel)));
            resolve(nested.flat());
            return;
          }
          all.push(...batch);
          readBatch();
        }, () => resolve([]));
      };
      readBatch();
    } else {
      resolve([]);
    }
  });
}

async function ptDrop(ev) {
  const tgt = ev.target.closest('[data-droptarget]');
  // ── OS-file / folder drop onto the Dateien branch → ingest with structure ──
  if (tgt && tgt.dataset.type === 'files' && !state._ptDrag && _ptIsOsFileDrag(ev)) {
    ev.preventDefault();
    document.querySelectorAll('#project-source-tree .pt-dropover').forEach(t => t.classList.remove('pt-dropover'));
    // The DataTransferItemList + entries are only valid synchronously — snapshot
    // webkitGetAsEntry()/file.path before any await (same rule as files.js).
    const items = ev.dataTransfer && ev.dataTransfer.items;
    const captured = [];
    if (items && items.length) {
      for (const item of items) {
        if (item.kind !== 'file') continue;
        const file = item.getAsFile();
        const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
        captured.push({ file, entry, path: (file && file.path) || (entry ? null : '') });
      }
    } else if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) {
      for (const file of ev.dataTransfer.files) captured.push({ file, entry: null, path: (file && file.path) || '' });
    }
    const entries = [];
    for (const { file, entry, path: fsPath } of captured) {
      // Electron: native path present; recurse folders in the main process.
      if (window.electronAPI && window.electronAPI.readDroppedFile && fsPath) {
        let results = null;
        if (window.electronAPI.readDroppedFolder) {
          results = await window.electronAPI.readDroppedFolder(fsPath);
        }
        if (!Array.isArray(results)) {
          const single = await window.electronAPI.readDroppedFile(fsPath);
          results = single && !single.error ? [single] : [];
        }
        // Electron entries are {name,data,...}; relPath may be absent on an
        // older preload → fall back to the bare name (flat, still ingests).
        for (const r of results) {
          if (!r || r.error) continue;
          const f = _ptElectronEntryToFile(r);
          if (f) entries.push({ file: f, relPath: r.relPath || r.name });
        }
        continue;
      }
      // Browser: walk via FileSystem entry (preserves structure); else bare file.
      if (entry) {
        const walked = await _ptCollectEntryFiles(entry, '');
        for (const w of walked) entries.push(w);
      } else if (file) {
        entries.push({ file, relPath: file.name });
      }
    }
    if (entries.length && typeof confirmProjectFolderImport === 'function') {
      confirmProjectFolderImport(entries);
    }
    return;
  }
  // ── Internal item re-group (existing behavior) ──
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

// Convert an Electron read-dropped entry {name,type,data(base64)} into a File
// object so the ingest upload path is identical to the browser one.
function _ptElectronEntryToFile(r) {
  try {
    const bin = atob(r.data || '');
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], r.name, { type: r.type || 'application/octet-stream' });
  } catch (e) {
    return null;
  }
}

// ─── GDPR/Classification review context menu (right-click a tree node) ───────
// target ∈ {path} | {agentId,project,sourceHash} | {agentId,project,folder}.
function ptReviewMenu(event, target) {
  event.preventDefault();
  event.stopPropagation();
  _ptCloseReviewMenu();
  const agentId = target.agentId || state._projectDetailAgent || state.activeAgentId || 'main';
  const project = target.project || state._projectDetailName || state._researchProject || '';
  const menu = document.createElement('div');
  menu.className = 'dr-ctxmenu';
  menu.id = 'pt-review-ctxmenu';
  let buttons = '';
  if (target.folder) {
    buttons = `<button onclick="ptReviewFolder('${esc(agentId)}','${esc(project)}','${esc(target.folder)}')">Ordner: GDPR/Klassifizierung prüfen</button>`;
  } else {
    const open = target.path
      ? `drOpenProjectFile({agentId:'${esc(agentId)}',project:'${esc(project)}',path:${JSON.stringify(target.path)}})`
      : `drOpenProjectFile({agentId:'${esc(agentId)}',project:'${esc(project)}',sourceHash:'${esc(target.sourceHash || '')}'})`;
    buttons = `<button onclick="(${open});_ptCloseReviewMenu()">GDPR/Klassifizierung prüfen…</button>`;
  }
  menu.innerHTML = buttons;
  document.body.appendChild(menu);
  menu.style.left = Math.min(event.clientX, window.innerWidth - 230) + 'px';
  menu.style.top = Math.min(event.clientY, window.innerHeight - 80) + 'px';
  setTimeout(() => document.addEventListener('click', _ptCloseReviewMenu, { once: true }), 0);
}

function _ptCloseReviewMenu() {
  const m = document.getElementById('pt-review-ctxmenu');
  if (m) m.remove();
}

// Folder review: trigger a synchronous review of every file, then refresh badges.
async function ptReviewFolder(agentId, project, folder) {
  _ptCloseReviewMenu();
  try {
    // The folder-tree endpoint lists files with their absolute paths; review
    // each via the analyze endpoint (cheap no-op if unchanged + already reviewed).
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(project)}/folder-tree?path=${encodeURIComponent(folder)}`);
    const files = [];
    (function walk(ns) { (ns || []).forEach(n => { if (n.type === 'dir') walk(n.children); else files.push(n.path); }); })(data.tree || []);
    let done = 0;
    for (const p of files.slice(0, 200)) {
      try { await API.post('/v1/data-review/analyze', { agent_id: agentId, project, path: p }); done++; } catch (_) {}
    }
    // Refresh the tree so new badges show.
    if (typeof renderProjectSourceTree === 'function') renderProjectSourceTree();
    showToast(`${done} Datei(en) geprüft.`);
  } catch (e) {
    showToast('Ordner-Prüfung fehlgeschlagen: ' + e.message, true);
  }
}
