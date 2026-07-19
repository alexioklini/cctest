// Wiki view — page tree + CodeMirror (raw) / marked (render) editor.
// Globals (browser-scope, no modules): loadWikiView, wikiSetFilter, wikiNewPage,
// wikiOpenPage, wikiSetMode, wikiSavePage, wikiDeleteCurrent, wikiOpenVersions,
// wikiCloseVersions, wikiPromoteVersion, wikiViewVersion. State on window._wiki.

window._wiki = window._wiki || {
  filter: 'all',
  grouping: 'manual', // manual | topic | project | source | created_by | updated_by
  tagFilters: [],     // active filter tags (lowercase); empty = no tag filter (OR-match)
  pages: [],          // flat rows from the tree endpoint
  current: null,      // currently open page object
  mode: 'render',     // 'render' | 'raw'
  cm: null,           // CodeMirror instance
  dirty: false,
  dragId: null,       // page id being dragged
  palette: {},        // tag name (lowercase) → color, from the global palette
  expanded: {},       // page id → true when its subtree is expanded (manual mode)
  treeInit: false,    // first render seeds collapsed-by-default state once
  search: '',         // free-text tree filter (title + tags)
};

// A tag's color from the global palette (neutral grey if undefined).
function wikiTagColor(name) {
  return window._wiki.palette[(name || '').toLowerCase()] || '#888888';
}
// Render one colored tag pill. `onRemove` (a JS expr string) adds a × button.
function wikiTagPill(name, opts) {
  opts = opts || {};
  const c = wikiTagColor(name);
  const rm = opts.onRemove
    ? `<span onclick="event.stopPropagation();${opts.onRemove}" style="cursor:pointer;margin-left:4px;opacity:.7" title="Entfernen">×</span>` : '';
  const click = opts.onClick ? `onclick="${opts.onClick}"` : '';
  return `<span class="wiki-tag" ${click} style="background:${c}22;color:${c};border-color:${c}55;cursor:${opts.onClick ? 'pointer' : 'default'}">${esc(name)}${rm}</span>`;
}

async function wikiLoadPalette() {
  try {
    const res = await API.wikiTags();
    const map = {};
    (res.tags || []).forEach(t => { map[t.name] = t.color; });
    window._wiki.palette = map;
  } catch (_) { /* keep prior palette */ }
}

// Inline SVG icons (brain-agent style: currentColor stroke, no emoji).
const WIKI_ICONS = {
  page:    '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  global:  '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/></svg>',
  team:    '<svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  source:  '<svg viewBox="0 0 24 24"><path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 0 1 0 10h-2"/><line x1="8" y1="12" x2="16" y2="12"/></svg>',
};
const WIKI_ICONS_EDIT = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>';
const WIKI_ICONS_CARET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="6 9 12 15 18 9"/></svg>';
const WIKI_ICONS_TRASH = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
const WIKI_ICONS_FILTER = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>';
const WIKI_ICONS_GEAR = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';

const WIKI_SOURCE_LABELS = {
  manual: 'Manuell', agent: 'Vom Agent', chat: 'Aus Chat', studio: 'Aus Studio',
  generated: 'Erzeugt', migrated: 'Migriert', activity: 'Profil/Aktivität',
  translation: 'Übersetzung', scheduled: 'Geplante Aufgabe', workflow: 'Workflow',
};

function wikiScopeIcon(scope) {
  if (scope === 'global') return WIKI_ICONS.global;
  if (scope === 'team') return WIKI_ICONS.team;
  return WIKI_ICONS.page;
}

function loadWikiView() {
  // Restore a previously dragged sidebar width.
  const w = parseInt(localStorage.getItem('wiki-sidebar-w') || '', 10);
  const sb = document.getElementById('wiki-sidebar');
  if (sb && w >= 180 && w <= 640) sb.style.width = w + 'px';
  wikiRefreshTree();
}

// ── Sidebar resize (drag the handle between tree + editor) ──
function wikiResizeStart(e) {
  e.preventDefault();
  const sb = document.getElementById('wiki-sidebar');
  const handle = document.getElementById('wiki-resize');
  if (!sb) return;
  const startX = e.clientX;
  const startW = sb.getBoundingClientRect().width;
  if (handle) handle.classList.add('dragging');
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
  const onMove = (ev) => {
    const w = Math.max(180, Math.min(640, startW + (ev.clientX - startX)));
    sb.style.width = w + 'px';
    if (window._wiki.cm) window._wiki.cm.refresh();   // keep CM layout in sync
  };
  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    if (handle) handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    localStorage.setItem('wiki-sidebar-w', String(Math.round(sb.getBoundingClientRect().width)));
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

async function wikiRefreshTree() {
  const tree = document.getElementById('wiki-tree');
  if (!tree) return;
  try {
    await wikiLoadPalette();   // colors for the pills/filter
    // A search query is applied server-side (matches title + tags + body text).
    const res = await API.wikiTree(window._wiki.filter, { q: window._wiki.search || '' });
    window._wiki.pages = res.pages || [];
    wikiRenderTree();
  } catch (e) {
    tree.innerHTML = `<div style="padding:12px;color:var(--error)">Fehler: ${esc(e.message)}</div>`;
  }
}

function wikiSetFilter(f) {
  window._wiki.filter = f;
  document.querySelectorAll('.wiki-filter-tab').forEach(b => {
    const on = b.dataset.filter === f;
    b.classList.toggle('active', on);
    b.style.background = on ? 'var(--bg-300)' : 'transparent';
    b.style.color = on ? 'var(--text-000)' : 'var(--text-300)';
  });
  wikiRefreshTree();
}

function wikiSetGrouping(mode) {
  window._wiki.grouping = mode;
  wikiRenderTree();
}

// Free-text tree filter. Matches title + tags + BODY text (server-side, so it
// finds pages by their content, not just their name). A non-empty query flattens
// the tree to matching rows so results aren't hidden inside collapsed branches.
// Debounced: re-fetch the tree ~220ms after the last keystroke.
function wikiSetSearch(q) {
  window._wiki.search = (q || '').trim().toLowerCase();
  clearTimeout(window._wiki._searchTimer);
  window._wiki._searchTimer = setTimeout(() => { wikiRefreshTree(); }, 220);
}

// Collapse/expand a node's subtree (manual tree mode).
function wikiToggleExpand(id) {
  window._wiki.expanded[id] = !window._wiki.expanded[id];
  wikiRenderTree();
}

// Expand every ancestor of `id` so a page opened from elsewhere is visible.
function wikiExpandAncestors(id) {
  const byId = {}; window._wiki.pages.forEach(p => { byId[p.id] = p; });
  let cur = byId[id];
  let guard = 0;
  while (cur && cur.parent_id && guard++ < 100) {
    window._wiki.expanded[cur.parent_id] = true;
    cur = byId[cur.parent_id];
  }
}

// One tree row. `manual` mode is draggable + indented; grouped modes are flat.
// `caret` is 'none' (grouped/leaf) | 'open' | 'collapsed' (manual mode w/ children).
function wikiRowHtml(page, depth, draggable, caret) {
  const active = window._wiki.current?.id === page.id;
  const tags = (page.tags || []).map(t =>
    wikiTagPill(t, { onClick: `event.stopPropagation();wikiToggleTag('${esc(t)}')` })).join('');
  const srcLabel = WIKI_SOURCE_LABELS[page.source] || page.source || '';
  const dot = `<span class="wiki-mp-dot ${page.mirrored ? 'on' : ''}" title="${page.mirrored ? 'In MemPalace durchsuchbar' : 'Nicht gespiegelt'}"></span>`;
  const caretHtml = caret === 'none' ? ''
    : `<span class="wiki-caret${caret === 'collapsed' ? ' collapsed' : ''}${caret === 'leaf' ? ' leaf' : ''}"
        onclick="event.stopPropagation();wikiToggleExpand('${page.id}')"
        title="${caret === 'collapsed' ? 'Aufklappen' : 'Zuklappen'}">${WIKI_ICONS_CARET}</span>`;
  return `<div class="wiki-tree-item${active ? ' active' : ''}"
      ${draggable ? `draggable="true" ondragstart="wikiDragStart(event,'${page.id}')" ondragend="wikiDragEnd(event)" ondragover="wikiDragOver(event)" ondragleave="wikiDragLeave(event)" ondrop="wikiDrop(event,'${page.id}')"` : ''}
      onclick="wikiOpenPage('${page.id}')" style="padding-left:${8 + depth * 14}px"
      title="${esc(srcLabel)}">
      ${caretHtml}
      ${dot}
      <span class="wiki-row-icon">${wikiScopeIcon(page.scope)}</span>
      <span class="wiki-row-title">${esc(page.title || 'Ohne Titel')}${tags ? ' ' + tags : ''}</span>
      <span class="wiki-row-actions">
        <button title="Umbenennen" onclick="event.stopPropagation();wikiRenamePage('${page.id}')">${WIKI_ICONS_EDIT}</button>
        <button title="Löschen" onclick="event.stopPropagation();wikiDeletePage('${page.id}')">${WIKI_ICONS_TRASH}</button>
      </span>
    </div>`;
}

function wikiVisiblePages() {
  let pages = window._wiki.pages;
  const filters = (window._wiki.tagFilters || []).map(t => t.toLowerCase());
  if (filters.length) {
    // OR-match: a page passes if it has ANY of the selected tags.
    pages = pages.filter(p => {
      const pt = (p.tags || []).map(t => t.toLowerCase());
      return filters.some(f => pt.includes(f));
    });
  }
  // NB: no client-side text filter here — a search query is applied server-side
  // in wikiRefreshTree (so it can match page BODY, which tree rows don't carry).
  return pages;
}

function wikiRenderTree() {
  const tree = document.getElementById('wiki-tree');
  if (!tree) return;
  wikiRenderTagFilter();
  const pages = wikiVisiblePages();
  if (!pages.length) {
    tree.innerHTML = `<div style="padding:12px;color:var(--text-400);font-size:13px">Keine Seiten.</div>`;
    return;
  }
  const mode = window._wiki.grouping;
  // A text search flattens the tree to matches (parents may not match), so drop
  // the nested/collapsible path when a query is active — same as tag filters.
  if (window._wiki.search) {
    tree.innerHTML = pages
      .sort((a, b) => (a.title || '').localeCompare(b.title || ''))
      .map(p => wikiRowHtml(p, 0, false, 'none')).join('');
    return;
  }
  if (mode === 'manual' && !(window._wiki.tagFilters || []).length) {
    // Editable nested tree (parent_id/position), drag-and-drop enabled.
    const byParent = {};
    pages.forEach(p => { (byParent[p.parent_id || ''] = byParent[p.parent_id || ''] || []).push(p); });
    Object.values(byParent).forEach(arr => arr.sort((a, b) => (a.position || 0) - (b.position || 0)));
    const ids = new Set(pages.map(p => p.id));
    const roots = pages.filter(p => !p.parent_id || !ids.has(p.parent_id));
    // First render: collapse every node by default (per user preference).
    if (!window._wiki.treeInit) { window._wiki.expanded = {}; window._wiki.treeInit = true; }
    const seen = new Set();
    const render = (page, depth) => {
      if (seen.has(page.id)) return '';
      seen.add(page.id);
      const kids = (byParent[page.id] || []).filter(k => k.id !== page.id);
      const hasKids = kids.length > 0;
      const open = !!window._wiki.expanded[page.id];
      const caret = !hasKids ? 'leaf' : (open ? 'open' : 'collapsed');
      let html = wikiRowHtml(page, depth, true, caret);
      if (hasKids && open) kids.forEach(k => { html += render(k, depth + 1); });
      return html;
    };
    tree.innerHTML = roots.map(r => render(r, 0)).join('');
    return;
  }
  // Computed grouping (read-only flat groups).
  const keyOf = (p) => {
    if (mode === 'project') return p.project_id || '(kein Projekt)';
    if (mode === 'source') return WIKI_SOURCE_LABELS[p.source] || p.source || '(unbekannt)';
    if (mode === 'created_by') return p.created_by || '(unbekannt)';
    if (mode === 'updated_by') return p.updated_by || '(unbekannt)';
    if (mode === 'topic') return (p.tags && p.tags[0]) || '(ohne Tag)';
    return '(alle)';
  };
  const groups = {};
  pages.forEach(p => { (groups[keyOf(p)] = groups[keyOf(p)] || []).push(p); });
  const keys = Object.keys(groups).sort((a, b) => a.localeCompare(b));
  tree.innerHTML = keys.map(k => {
    const rows = groups[k].sort((a, b) => (a.title || '').localeCompare(b.title || ''))
      .map(p => wikiRowHtml(p, 0, false, 'none')).join('');
    return `<div class="wiki-group-header">${esc(k)} <span style="opacity:.6">· ${groups[k].length}</span></div>${rows}`;
  }).join('');
}

// Tag filter row: colored chips for the tags in view + a 'Tags verwalten' button
// that opens the palette-management modal.
// The tag bar (tree view): a Filter button + a Manage button. Active filter
// tags appear as small colored chips next to the buttons (click a chip = remove
// it from the filter). The list of all tags lives in the modals, NOT inline.
function wikiRenderTagFilter() {
  const host = document.getElementById('wiki-tag-filter');
  if (!host) return;
  const active = window._wiki.tagFilters || [];
  const filterLabel = active.length ? `Filter (${active.length})` : 'Filter';
  const filterBtn = `<button class="wiki-tagbar-btn${active.length ? ' on' : ''}" onclick="wikiOpenTagFilter()" title="Nach Tags filtern">${WIKI_ICONS_FILTER} ${filterLabel}</button>`;
  const manageBtn = `<button class="wiki-tagbar-btn" onclick="wikiOpenTagManager()" title="Tags verwalten (anlegen, umbenennen, Farbe, löschen)">${WIKI_ICONS_GEAR} Verwalten</button>`;
  const chips = active.map(t => {
    const c = wikiTagColor(t);
    return `<span class="wiki-tag" onclick="wikiToggleTag('${esc(t)}')" title="Aus Filter entfernen" style="background:${c};color:#fff;border-color:${c};cursor:pointer">${esc(t)} ✕</span>`;
  }).join('');
  host.innerHTML = `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">${filterBtn}${manageBtn}${chips}</div>`;
}

// Toggle a tag in the multi-select filter (OR-match).
function wikiToggleTag(tag) {
  const t = (tag || '').toLowerCase();
  const set = window._wiki.tagFilters || [];
  const i = set.findIndex(x => x.toLowerCase() === t);
  if (i >= 0) set.splice(i, 1); else set.push(t);
  window._wiki.tagFilters = set;
  wikiRenderTree();
  // keep an open filter modal in sync
  if (document.getElementById('wiki-tagfilter-modal')) wikiRenderTagFilterList();
}

// ── Filter modal: pick which tags filter the tree (multi-select, OR) ──
async function wikiOpenTagFilter() {
  await wikiLoadPalette();
  let m = document.getElementById('wiki-tagfilter-modal');
  if (m) m.remove();
  m = document.createElement('div');
  m.id = 'wiki-tagfilter-modal';
  m.className = 'modal-overlay';
  m.style.cssText = 'display:flex;position:fixed;inset:0;z-index:9000;background:var(--modal-overlay);align-items:center;justify-content:center';
  m.innerHTML = `<div class="modal" style="max-width:480px;width:90%;background:var(--bg-100);border-radius:10px;overflow:hidden">
      <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border-100)">
        <h3 style="margin:0">Nach Tags filtern</h3>
        <button class="modal-close" onclick="wikiCloseTagFilter()" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text-300)">×</button>
      </div>
      <div class="modal-body" style="padding:14px 18px;max-height:60vh;overflow-y:auto">
        <div style="font-size:12px;color:var(--text-400);margin-bottom:10px">Mehrfachauswahl — eine Seite erscheint, wenn sie <b>mindestens einen</b> der gewählten Tags hat.</div>
        <div id="wiki-tagfilter-list" style="display:flex;flex-wrap:wrap;gap:6px"></div>
        <div style="margin-top:14px;text-align:right">
          <button onclick="wikiClearTagFilter()" style="padding:6px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer">Filter zurücksetzen</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(m);
  wikiRenderTagFilterList();
}

function wikiRenderTagFilterList() {
  const host = document.getElementById('wiki-tagfilter-list');
  if (!host) return;
  const names = Object.keys(window._wiki.palette).sort();
  if (!names.length) { host.innerHTML = '<div style="color:var(--text-400);font-size:13px">Noch keine Tags.</div>'; return; }
  const active = (window._wiki.tagFilters || []).map(t => t.toLowerCase());
  host.innerHTML = names.map(n => {
    const c = window._wiki.palette[n];
    const on = active.includes(n);
    const style = on ? `background:${c};color:#fff;border-color:${c}` : `background:${c}22;color:${c};border-color:${c}55`;
    return `<span class="wiki-tag" onclick="wikiToggleTag('${esc(n)}')" style="${style};cursor:pointer">${on ? '✓ ' : ''}${esc(n)}</span>`;
  }).join('');
}

function wikiClearTagFilter() {
  window._wiki.tagFilters = [];
  wikiRenderTree();
  wikiRenderTagFilterList();
}

function wikiCloseTagFilter() {
  const m = document.getElementById('wiki-tagfilter-modal');
  if (m) m.remove();
}

// ── Tag palette manager (modal) ──
async function wikiOpenTagManager() {
  await wikiLoadPalette();
  let modal = document.getElementById('wiki-tag-modal');
  if (modal) modal.remove();
  modal = document.createElement('div');
  modal.id = 'wiki-tag-modal';
  modal.className = 'modal-overlay';
  modal.style.cssText = 'display:flex;position:fixed;inset:0;z-index:9000;background:var(--modal-overlay);align-items:center;justify-content:center';
  modal.innerHTML = `<div class="modal" style="max-width:520px;width:90%;background:var(--bg-100);border-radius:10px;overflow:hidden">
      <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border-100)">
        <h3 style="margin:0">Tags verwalten</h3>
        <button class="modal-close" onclick="wikiCloseTagManager()" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text-300)">×</button>
      </div>
      <div class="modal-body" style="padding:14px 18px;max-height:60vh;overflow-y:auto">
        <div style="display:flex;gap:8px;margin-bottom:14px">
          <input id="wiki-newtag-name" placeholder="Neuer Tag…" style="flex:1;padding:6px 8px;border-radius:6px;border:1px solid var(--border-100);background:var(--bg-100);color:var(--text-100)">
          <input id="wiki-newtag-color" type="color" value="#2563eb" style="width:40px;height:34px;border:none;background:none;cursor:pointer">
          <button onclick="wikiCreateTag()" style="padding:6px 12px;border-radius:6px;border:none;background:var(--accent-main-100,#2563eb);color:#fff;cursor:pointer">Anlegen</button>
        </div>
        <div id="wiki-tag-list"></div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  wikiRenderTagList();
}

function wikiRenderTagList() {
  const host = document.getElementById('wiki-tag-list');
  if (!host) return;
  const entries = Object.entries(window._wiki.palette).sort((a, b) => a[0].localeCompare(b[0]));
  if (!entries.length) { host.innerHTML = '<div style="color:var(--text-400);font-size:13px">Noch keine Tags.</div>'; return; }
  host.innerHTML = entries.map(([name, color]) => `
    <div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border-100)">
      <input type="color" value="${esc(color)}" onchange="wikiSetTagColor('${esc(name)}',this.value)" title="Farbe ändern" style="width:32px;height:28px;border:none;background:none;cursor:pointer">
      <span class="wiki-tag" style="background:${color}22;color:${color};border-color:${color}55;flex:1">${esc(name)}</span>
      <button onclick="wikiRenameTagDef('${esc(name)}')" title="Umbenennen" style="background:none;border:none;color:var(--text-400);cursor:pointer;padding:2px">${WIKI_ICONS_EDIT}</button>
      <button onclick="wikiDeleteTagDef('${esc(name)}')" title="Tag löschen" style="background:none;border:none;color:var(--error,#dc2626);cursor:pointer;padding:2px">${WIKI_ICONS_TRASH}</button>
    </div>`).join('');
}

async function wikiRenameTagDef(oldName) {
  const next = (prompt(`Tag umbenennen — "${oldName}" → ?`, oldName) || '').trim().toLowerCase();
  if (!next || next === oldName.toLowerCase()) return;
  if (window._wiki.palette[next] && !confirm(`Tag "${next}" existiert bereits — die beiden zusammenführen?`)) return;
  try {
    const res = await API.wikiRenameTag(oldName, next);
    if (res.error) { alert(res.error); return; }
    await wikiLoadPalette();
    wikiRenderTagList();
    // tree/page may reference the renamed tag → refresh both.
    wikiRefreshTree();
    if (window._wiki.current) {
      const fresh = await API.wikiGet(window._wiki.current.id).catch(() => null);
      if (fresh && !fresh.error) { window._wiki.current = fresh; wikiRenderMeta(fresh); }
    }
  } catch (e) { alert('Umbenennen fehlgeschlagen: ' + e.message); }
}

function wikiCloseTagManager() {
  const m = document.getElementById('wiki-tag-modal');
  if (m) m.remove();
  wikiRenderTagFilter();   // refresh chip colors after edits
  if (window._wiki.current) wikiRenderMeta(window._wiki.current);
}

async function wikiCreateTag() {
  const name = (document.getElementById('wiki-newtag-name')?.value || '').trim().toLowerCase();
  const color = document.getElementById('wiki-newtag-color')?.value || '#2563eb';
  if (!name) return;
  if (window._wiki.palette[name]) { alert('Tag existiert bereits.'); return; }  // no duplicate
  try {
    await API.wikiSaveTag(name, color);
    await wikiLoadPalette();
    document.getElementById('wiki-newtag-name').value = '';
    wikiRenderTagList();
  } catch (e) { alert('Anlegen fehlgeschlagen: ' + e.message); }
}

async function wikiSetTagColor(name, color) {
  try {
    await API.wikiSaveTag(name, color);
    window._wiki.palette[name.toLowerCase()] = color;
    wikiRenderTagList();
  } catch (e) { alert('Farbe ändern fehlgeschlagen: ' + e.message); }
}

async function wikiDeleteTagDef(name) {
  if (!confirm(`Tag "${name}" aus der Palette löschen? (Seiten behalten das Tag, es wird nur farblos.)`)) return;
  try {
    await API.wikiDeleteTag(name);
    delete window._wiki.palette[name.toLowerCase()];
    wikiRenderTagList();
  } catch (e) { alert('Löschen fehlgeschlagen: ' + e.message); }
}

// ── Drag-and-drop re-parenting (Manuell mode) ──
function wikiDragStart(e, id) {
  window._wiki.dragId = id;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', id);
  e.currentTarget.classList.add('dragging');
  e.stopPropagation();
}
function wikiDragEnd(e) { e.currentTarget.classList.remove('dragging'); window._wiki.dragId = null; }
function wikiDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; e.currentTarget.classList.add('drag-over'); }
function wikiDragLeave(e) { e.currentTarget.classList.remove('drag-over'); }
async function wikiDrop(e, targetId) {
  e.preventDefault(); e.stopPropagation();
  e.currentTarget.classList.remove('drag-over');
  const src = window._wiki.dragId;
  window._wiki.dragId = null;
  if (!src || src === targetId) return;
  // Guard: don't drop a page onto its own descendant (would orphan the subtree).
  if (wikiIsDescendant(targetId, src)) { return; }
  try {
    await API.wikiMove(src, { parent_id: targetId });   // becomes a child of target
    await wikiRefreshTree();
  } catch (err) { alert('Verschieben fehlgeschlagen: ' + err.message); }
}
function wikiIsDescendant(maybeChildId, ancestorId) {
  const byId = {}; window._wiki.pages.forEach(p => { byId[p.id] = p; });
  let cur = byId[maybeChildId];
  let guard = 0;
  while (cur && cur.parent_id && guard++ < 100) {
    if (cur.parent_id === ancestorId) return true;
    cur = byId[cur.parent_id];
  }
  return false;
}

async function wikiRenamePage(id) {
  const p = window._wiki.pages.find(x => x.id === id);
  const title = prompt('Neuer Titel:', p?.title || '');
  if (title == null || !title.trim()) return;
  try {
    await API.wikiUpdate(id, { title: title.trim() });
    await wikiRefreshTree();
    if (window._wiki.current?.id === id) {
      document.getElementById('wiki-title-input').value = title.trim();
      window._wiki.current.title = title.trim();
    }
  } catch (e) { alert('Umbenennen fehlgeschlagen: ' + e.message); }
}

async function wikiDeletePage(id) {
  const p = window._wiki.pages.find(x => x.id === id);
  if (!confirm(`Seite "${p?.title || ''}" löschen? Unterseiten rücken eine Ebene hoch.`)) return;
  try {
    await API.wikiDelete(id);
    if (window._wiki.current?.id === id) {
      window._wiki.current = null;
      document.getElementById('wiki-page').style.display = 'none';
      document.getElementById('wiki-empty').style.display = 'flex';
    }
    await wikiRefreshTree();
  } catch (e) { alert('Löschen fehlgeschlagen: ' + e.message); }
}

// Jump to the Wiki view + open a page (used by citation/source badges in chat).
async function wikiOpenFromCitation(pageId) {
  if (!pageId) return;
  if (typeof navigateTo === 'function') navigateTo('wiki');
  // loadWikiView (via navigateTo) refreshes the tree; open the page after.
  setTimeout(() => { try { wikiOpenPage(pageId); } catch (_) {} }, 150);
}

async function wikiOpenPage(id) {
  if (window._wiki.dirty && !confirm('Ungespeicherte Änderungen verwerfen?')) return;
  try {
    const page = await API.wikiGet(id);
    if (page.error) { alert(page.error); return; }
    window._wiki.current = page;
    window._wiki.dirty = false;
    document.getElementById('wiki-empty').style.display = 'none';
    document.getElementById('wiki-page').style.display = 'flex';
    document.getElementById('wiki-title-input').value = page.title || '';
    wikiRenderMeta(page);
    wikiSetMode('render');
    wikiExpandAncestors(id);   // reveal the opened page if it's nested
    wikiRenderTree();   // refresh active highlight
  } catch (e) {
    alert('Fehler beim Laden: ' + e.message);
  }
}

function wikiRenderMeta(page) {
  const meta = document.getElementById('wiki-page-meta');
  if (!meta) return;
  const bits = [];
  bits.push(`Bereich: ${esc(page.scope)}`);
  bits.push(`Version: ${page.current_version || 1}`);
  if (page.manually_edited) bits.push('manuell bearbeitet');
  if (page.source && page.source !== 'manual') {
    bits.push('Quelle: ' + esc(WIKI_SOURCE_LABELS[page.source] || page.source));
  }
  if (page.source_ref) {
    const label = wikiSourceJumpLabel(page.source_ref);
    if (label) bits.push(`<a class="wiki-jump" onclick="wikiJumpToSource('${esc(page.source_ref)}')" style="color:var(--accent-blue);cursor:pointer;text-decoration:underline">↪ ${esc(label)}</a>`);
  }
  // Per-page tag manager: colored pills (× removes from THIS page) + an add control.
  const tags = (page.tags || []).map(t =>
    wikiTagPill(t, { onClick: `wikiToggleTag('${esc(t)}')`, onRemove: `wikiRemoveTagFromPage('${esc(t)}')` })).join('');
  const addBtn = `<span class="wiki-tag wiki-tag-add" onclick="wikiAddTagToPage()" title="Tag hinzufügen">+ Tag</span>`;
  meta.innerHTML =
    `<div class="wiki-meta-info">${bits.join('<span class="wiki-meta-sep">·</span>')}</div>` +
    `<div class="wiki-meta-tags">${tags}${addBtn}</div>`;
}

// Page tag assignment: a small modal to pick WHICH existing palette tags apply
// to this page (toggle on/off). NO creating/renaming/deleting here — that's the
// tree's Verwalten modal. New tags are created there (or by auto-tagging).
async function wikiAddTagToPage() {
  if (!window._wiki.current) return;
  await wikiLoadPalette();
  let m = document.getElementById('wiki-pagetag-modal');
  if (m) m.remove();
  m = document.createElement('div');
  m.id = 'wiki-pagetag-modal';
  m.className = 'modal-overlay';
  m.style.cssText = 'display:flex;position:fixed;inset:0;z-index:9000;background:var(--modal-overlay);align-items:center;justify-content:center';
  m.innerHTML = `<div class="modal" style="max-width:460px;width:90%;background:var(--bg-100);border-radius:10px;overflow:hidden">
      <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border-100)">
        <h3 style="margin:0">Tags dieser Seite</h3>
        <button class="modal-close" onclick="wikiClosePageTagPicker()" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text-300)">×</button>
      </div>
      <div class="modal-body" style="padding:14px 18px;max-height:60vh;overflow-y:auto">
        <div style="font-size:12px;color:var(--text-400);margin-bottom:10px">Vorhandene Tags an-/abwählen. Neue Tags anlegen unter <b>Verwalten</b> in der Übersicht.</div>
        <div id="wiki-pagetag-list" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
    </div>`;
  document.body.appendChild(m);
  wikiRenderPageTagPicker();
}

function wikiRenderPageTagPicker() {
  const host = document.getElementById('wiki-pagetag-list');
  if (!host) return;
  const names = Object.keys(window._wiki.palette).sort();
  if (!names.length) { host.innerHTML = '<div style="color:var(--text-400);font-size:13px">Noch keine Tags. Lege welche unter „Verwalten" an.</div>'; return; }
  const on = (window._wiki.current.tags || []).map(t => t.toLowerCase());
  host.innerHTML = names.map(n => {
    const c = window._wiki.palette[n];
    const sel = on.includes(n);
    const style = sel ? `background:${c};color:#fff;border-color:${c}` : `background:${c}22;color:${c};border-color:${c}55`;
    return `<span class="wiki-tag" onclick="wikiTogglePageTag('${esc(n)}')" style="${style};cursor:pointer">${sel ? '✓ ' : ''}${esc(n)}</span>`;
  }).join('');
}

async function wikiTogglePageTag(name) {
  const page = window._wiki.current;
  if (!page) return;
  const n = name.toLowerCase();
  const cur = (page.tags || []);
  const has = cur.some(t => t.toLowerCase() === n);
  const next = has ? cur.filter(t => t.toLowerCase() !== n) : [...cur, n];
  try {
    const updated = await API.wikiUpdate(page.id, { tags: next });
    window._wiki.current = updated;
    wikiRenderPageTagPicker();   // refresh the picker's checks
    wikiRenderMeta(updated);     // refresh the page's pill row
    wikiRefreshTree();
  } catch (e) { alert('Tag ändern fehlgeschlagen: ' + e.message); }
}

function wikiClosePageTagPicker() {
  const m = document.getElementById('wiki-pagetag-modal');
  if (m) m.remove();
}

// × on a page pill removes the tag from this page (no modal).
async function wikiRemoveTagFromPage(tag) {
  const page = window._wiki.current;
  if (!page) return;
  const next = (page.tags || []).filter(t => t.toLowerCase() !== tag.toLowerCase());
  try {
    const updated = await API.wikiUpdate(page.id, { tags: next });
    window._wiki.current = updated;
    wikiRenderMeta(updated);
    wikiRefreshTree();
  } catch (e) { alert('Tag entfernen fehlgeschlagen: ' + e.message); }
}

// A friendly label for a source_ref jump link, or '' if not navigable.
function wikiSourceJumpLabel(ref) {
  const type = (ref || '').split('/')[0];
  return ({
    session: 'Zum Chat', output: 'Zum Studio-Ergebnis', translation: 'Zur Übersetzung',
    schedule: 'Zur geplanten Aufgabe', 'workflow-run': 'Zum Workflow-Lauf',
    'user-profile': 'Zu Profil/Aktivität',
  })[type] || '';
}

// Open the originating object for a wiki page's source_ref.
function wikiJumpToSource(ref) {
  const i = (ref || '').indexOf('/');
  const type = i < 0 ? ref : ref.slice(0, i);
  const id = i < 0 ? '' : ref.slice(i + 1);
  switch (type) {
    case 'session':
      if (typeof openSession === 'function') return openSession(id, (state && state.activeAgentId) || 'main');
      break;
    case 'output':
      if (typeof studioOpenOutput === 'function') return studioOpenOutput(id);
      break;
    case 'translation':
      navigateTo('translation'); break;
    case 'schedule':
      navigateTo('scheduled'); break;
    case 'workflow-run':
      navigateTo('workflows'); break;
    case 'user-profile':
      if (typeof openUserSettings === 'function') return openUserSettings();
      break;
  }
  // Fallback: no specific opener available.
  if (!['translation', 'schedule', 'workflow-run'].includes(type)) {
    alert('Quelle: ' + ref);
  }
}

function wikiSetMode(mode) {
  window._wiki.mode = mode;
  const render = document.getElementById('wiki-render');
  const raw = document.getElementById('wiki-raw');
  const bRender = document.getElementById('wiki-mode-render');
  const bRaw = document.getElementById('wiki-mode-raw');
  // Active state is CSS-driven now (.wiki-mode-btn.active) — just toggle the class.
  [bRender, bRaw].forEach(b => { if (b) b.classList.remove('active'); });
  if (mode === 'raw') {
    render.style.display = 'none';
    raw.style.display = 'block';
    if (bRaw) bRaw.classList.add('active');
    wikiEnsureEditor();
  } else {
    // Render the (possibly edited) markdown. Replace [[image|audio|video:<id>]]
    // media tokens with placeholders, render, then hydrate with authed blobs.
    let md = window._wiki.cm ? window._wiki.cm.getValue() : (window._wiki.current?.body_md || '');
    const media = [];
    md = md.replace(/\[\[(image|audio|video):([a-zA-Z0-9_-]+)\]\]/g, (m, kind, id) => {
      const slot = `wiki-media-${media.length}`;
      media.push({ slot, kind, id });
      return `\n\n<div id="${slot}" data-wiki-media="${kind}:${id}"></div>\n\n`;
    });
    render.innerHTML = (typeof renderMarkdown === 'function') ? renderMarkdown(md) : (window.marked ? marked.parse(md) : esc(md));
    render.style.display = 'block';
    raw.style.display = 'none';
    if (bRender) bRender.classList.add('active');
    media.forEach(m => wikiHydrateMedia(m.slot, m.kind, m.id));
  }
}

// Fetch an artifact (authed, Bearer-only) and mount it as img/audio/video.
async function wikiHydrateMedia(slot, kind, artifactId) {
  const el = document.getElementById(slot);
  if (!el) return;
  try {
    const url = (typeof API.getArtifactDownloadUrl === 'function')
      ? API.getArtifactDownloadUrl(artifactId)
      : `/v1/artifacts/${artifactId}/download`;
    const h = {}; const t = localStorage.getItem('auth-token'); if (t) h['Authorization'] = `Bearer ${t}`;
    const resp = await fetch(url, { headers: h });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const obj = URL.createObjectURL(await resp.blob());
    if (kind === 'image') el.innerHTML = `<img src="${obj}" style="max-width:100%;border-radius:8px">`;
    else if (kind === 'audio') el.innerHTML = `<audio controls preload="metadata" style="width:100%;max-width:520px" src="${obj}"></audio>`;
    else el.innerHTML = `<video controls preload="metadata" style="max-width:100%;border-radius:8px" src="${obj}"></video>`;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--error);font-size:12px">Medien konnten nicht geladen werden</div>`;
  }
}

// Read the current page aloud (reuses the chat read-aloud TTS pipeline).
async function wikiReadAloud() {
  const body = window._wiki.cm ? window._wiki.cm.getValue() : (window._wiki.current?.body_md || '');
  if (!body.trim()) return;
  // Strip media tokens before speaking.
  const text = body.replace(/\[\[(image|audio|video):[a-zA-Z0-9_-]+\]\]/g, '');
  if (typeof _stripMarkdownForSpeech === 'function' && typeof _chunkForTts === 'function' && typeof _playChatQueue === 'function') {
    window._chatAudioQueue = _chunkForTts(_stripMarkdownForSpeech(text), 3000);
    try {
      const det = await API.post('/v1/translate/detect', { text: text.slice(0, 400) });
      window._chatAudioLang = (det && det.lang) || '';
    } catch (_) { window._chatAudioLang = ''; }
    _playChatQueue();
  } else {
    alert('Vorlesen nicht verfügbar.');
  }
}

async function wikiGenerate(kind) {
  const page = window._wiki.current;
  if (!page) return;
  const inc = confirm('Auch Unterseiten einbeziehen?');
  const btnLabel = kind === 'podcast' ? '🎧 Podcast' : 'Zusammenfassung';
  const note = document.getElementById('wiki-page-meta');
  const prev = note ? note.textContent : '';
  if (note) note.textContent = `${btnLabel} wird erzeugt… (das kann einen Moment dauern)`;
  try {
    const res = await API.wikiGenerate(page.id, { kind, include_children: inc });
    if (res.error) { alert(res.error); if (note) note.textContent = prev; return; }
    await wikiRefreshTree();
    wikiOpenPage(res.id);   // open the new child page
  } catch (e) {
    alert('Erzeugung fehlgeschlagen: ' + e.message);
    if (note) note.textContent = prev;
  }
}

function wikiInsertMedia() {
  const inp = document.getElementById('wiki-media-input');
  if (inp) inp.click();
}

async function wikiMediaSelected(input) {
  const page = window._wiki.current;
  const file = input.files && input.files[0];
  input.value = '';   // reset for next pick
  if (!page || !file) return;
  try {
    const res = await API.wikiMedia(page.id, file);
    if (res.error) { alert(res.error); return; }
    // Insert the snippet at the end of the raw body + save.
    wikiSetMode('raw');
    const cur = window._wiki.cm.getValue();
    window._wiki.cm.setValue(cur + `\n\n${res.snippet}\n`);
    window._wiki.dirty = true;
    await wikiSavePage();
  } catch (e) {
    alert('Medien-Upload fehlgeschlagen: ' + e.message);
  }
}

// Lazily build the CodeMirror editor inside #wiki-raw with the current body.
function wikiEnsureEditor() {
  const host = document.getElementById('wiki-raw');
  const body = window._wiki.current?.body_md || '';
  if (window._wiki.cm) {
    // Reuse: only reset value when switching pages (tracked via _cmPageId).
    if (window._wiki._cmPageId !== window._wiki.current?.id) {
      window._wiki.cm.setValue(body);
      window._wiki._cmPageId = window._wiki.current?.id;
    }
    setTimeout(() => window._wiki.cm.refresh(), 0);
    return;
  }
  host.innerHTML = '';
  if (window.CodeMirror) {
    const dark = document.documentElement.classList.contains('dark') ||
      (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    window._wiki.cm = CodeMirror(host, {
      value: body,
      mode: 'markdown',
      lineNumbers: true,
      lineWrapping: true,
      theme: dark ? 'default' : 'default',
    });
    window._wiki.cm.setSize('100%', '100%');
    window._wiki.cm.on('change', () => { window._wiki.dirty = true; });
    window._wiki._cmPageId = window._wiki.current?.id;
  } else {
    // Fallback: plain textarea if CodeMirror failed to load.
    const ta = document.createElement('textarea');
    ta.style.cssText = 'width:100%;height:100%;border:none;outline:none;resize:none;padding:18px 24px;font-family:monospace;font-size:13px;background:transparent;color:var(--text-000)';
    ta.value = body;
    ta.addEventListener('input', () => { window._wiki.dirty = true; });
    host.appendChild(ta);
    window._wiki.cm = { getValue: () => ta.value, setValue: v => { ta.value = v; }, refresh: () => {}, on: () => {} };
    window._wiki._cmPageId = window._wiki.current?.id;
  }
}

async function wikiSavePage() {
  const page = window._wiki.current;
  if (!page) return;
  const title = document.getElementById('wiki-title-input').value.trim();
  const body = window._wiki.cm ? window._wiki.cm.getValue() : page.body_md;
  try {
    const updated = await API.wikiUpdate(page.id, { title, body_md: body });
    if (updated.error) { alert(updated.error); return; }
    window._wiki.current = updated;
    window._wiki.dirty = false;
    window._wiki._cmPageId = null;  // force re-seed next raw open
    wikiRenderMeta(updated);
    wikiSetMode('render');
    wikiRefreshTree();
  } catch (e) {
    alert('Speichern fehlgeschlagen: ' + e.message);
  }
}

async function wikiNewPage() {
  const title = prompt('Titel der neuen Seite:');
  if (!title) return;
  // Scope from current filter: mine→user, team→team, global→global, all→user.
  const f = window._wiki.filter;
  const scope = f === 'team' ? 'team' : f === 'global' ? 'global' : 'user';
  const parent_id = window._wiki.current?.id && confirm(`Als Unterseite von "${window._wiki.current.title}" anlegen?`)
    ? window._wiki.current.id : '';
  try {
    const page = await API.wikiCreate({ title, scope, body_md: '', parent_id });
    if (page.error) { alert(page.error); return; }
    await wikiRefreshTree();
    wikiOpenPage(page.id);
  } catch (e) {
    alert('Anlegen fehlgeschlagen: ' + e.message);
  }
}

async function wikiDeleteCurrent() {
  const page = window._wiki.current;
  if (!page) return;
  if (!confirm(`Seite "${page.title}" löschen? Unterseiten bleiben erhalten.`)) return;
  try {
    await API.wikiDelete(page.id);
    window._wiki.current = null;
    window._wiki.dirty = false;
    document.getElementById('wiki-page').style.display = 'none';
    document.getElementById('wiki-empty').style.display = 'flex';
    wikiRefreshTree();
  } catch (e) {
    alert('Löschen fehlgeschlagen: ' + e.message);
  }
}

function wikiBuildVersionsModal() {
  let modal = document.getElementById('wiki-versions-modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'wiki-versions-modal';
  modal.className = 'modal-overlay';
  modal.style.cssText = 'display:flex;position:fixed;inset:0;z-index:9000;background:var(--modal-overlay);align-items:center;justify-content:center';
  modal.innerHTML = `<div class="modal" style="max-width:560px;background:var(--bg-100);border-radius:10px;overflow:hidden">
      <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border-100)">
        <h3 style="margin:0">Versionen</h3>
        <button class="modal-close" onclick="wikiCloseVersions()" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text-300)">×</button>
      </div>
      <div class="modal-body" id="wiki-versions-list" style="max-height:60vh;overflow-y:auto"></div>
    </div>`;
  document.body.appendChild(modal);
  return modal;
}

async function wikiOpenVersions() {
  const page = window._wiki.current;
  if (!page) return;
  wikiBuildVersionsModal();
  const list = document.getElementById('wiki-versions-list');
  list.innerHTML = '<div style="padding:12px;color:var(--text-400)">Lade…</div>';
  try {
    const res = await API.wikiVersions(page.id);
    const vs = res.versions || [];
    list.innerHTML = vs.map(v => {
      const cur = (v.version === page.current_version);
      const date = v.created_at ? new Date(v.created_at * 1000).toLocaleString() : '';
      return `<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border-100)">
        <div style="flex:1">
          <strong>v${v.version}</strong> ${cur ? '<span style="color:var(--accent-main-100,#2563eb)">(aktuell)</span>' : ''}
          <span style="color:var(--text-400);font-size:12px">${esc(v.note || '')}</span><br>
          <span style="color:var(--text-400);font-size:11px">${esc(date)}</span>
        </div>
        <button onclick="wikiViewVersion(${v.version})" style="padding:4px 10px;border-radius:5px;border:1px solid var(--border-100);background:transparent;color:var(--text-100);cursor:pointer;font-size:12px">Ansehen</button>
        ${cur ? '' : `<button onclick="wikiPromoteVersion(${v.version})" style="padding:4px 10px;border-radius:5px;border:none;background:var(--accent-main-100,#2563eb);color:#fff;cursor:pointer;font-size:12px">Aktivieren</button>`}
      </div>`;
    }).join('') || '<div style="padding:12px;color:var(--text-400)">Keine Versionen.</div>';
  } catch (e) {
    list.innerHTML = `<div style="padding:12px;color:var(--error)">${esc(e.message)}</div>`;
  }
}

function wikiCloseVersions() {
  const m = document.getElementById('wiki-versions-modal');
  if (m) m.remove();
}

async function wikiViewVersion(n) {
  const page = window._wiki.current;
  if (!page) return;
  try {
    const v = await API.wikiVersion(page.id, n);
    if (v.error) { alert(v.error); return; }
    // Read-only preview in the render pane.
    wikiCloseVersions();
    wikiSetMode('render');
    const render = document.getElementById('wiki-render');
    render.innerHTML = `<div style="padding:6px 10px;margin-bottom:10px;background:var(--bg-300);border-radius:6px;font-size:12px;color:var(--text-300)">
      Vorschau v${n} (schreibgeschützt). <a href="#" onclick="wikiOpenPage('${page.id}');return false">Zur aktuellen Version</a></div>` +
      ((typeof renderMarkdown === 'function') ? renderMarkdown(v.body_md || '') : esc(v.body_md || ''));
  } catch (e) {
    alert(e.message);
  }
}

async function wikiPromoteVersion(n) {
  const page = window._wiki.current;
  if (!page) return;
  if (!confirm(`Version ${n} zur aktuellen Version machen?`)) return;
  try {
    const updated = await API.wikiPromote(page.id, n);
    if (updated.error) { alert(updated.error); return; }
    window._wiki.current = updated;
    window._wiki._cmPageId = null;
    wikiCloseVersions();
    document.getElementById('wiki-title-input').value = updated.title || '';
    wikiRenderMeta(updated);
    wikiSetMode('render');
    wikiRefreshTree();
  } catch (e) {
    alert('Aktivieren fehlgeschlagen: ' + e.message);
  }
}
