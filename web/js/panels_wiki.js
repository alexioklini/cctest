// Wiki view — page tree + CodeMirror (raw) / marked (render) editor.
// Globals (browser-scope, no modules): loadWikiView, wikiSetFilter, wikiNewPage,
// wikiOpenPage, wikiSetMode, wikiSavePage, wikiDeleteCurrent, wikiOpenVersions,
// wikiCloseVersions, wikiPromoteVersion, wikiViewVersion. State on window._wiki.

window._wiki = window._wiki || {
  filter: 'all',
  grouping: 'manual', // manual | topic | project | source | created_by | updated_by
  tagFilter: '',      // active tag chip ('' = none)
  pages: [],          // flat rows from the tree endpoint
  current: null,      // currently open page object
  mode: 'render',     // 'render' | 'raw'
  cm: null,           // CodeMirror instance
  dirty: false,
  dragId: null,       // page id being dragged
};

// Inline SVG icons (brain-agent style: currentColor stroke, no emoji).
const WIKI_ICONS = {
  page:    '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  global:  '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/></svg>',
  team:    '<svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  source:  '<svg viewBox="0 0 24 24"><path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 0 1 0 10h-2"/><line x1="8" y1="12" x2="16" y2="12"/></svg>',
};
const WIKI_ICONS_EDIT = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>';
const WIKI_ICONS_TRASH = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

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
  wikiRefreshTree();
}

async function wikiRefreshTree() {
  const tree = document.getElementById('wiki-tree');
  if (!tree) return;
  try {
    const res = await API.wikiTree(window._wiki.filter);
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

// One tree row. `manual` mode is draggable + indented; grouped modes are flat.
function wikiRowHtml(page, depth, draggable) {
  const active = window._wiki.current?.id === page.id;
  const tags = (page.tags || []).map(t =>
    `<span class="wiki-tag" onclick="event.stopPropagation();wikiToggleTag('${esc(t)}')">${esc(t)}</span>`).join('');
  const srcLabel = WIKI_SOURCE_LABELS[page.source] || page.source || '';
  const dot = `<span class="wiki-mp-dot ${page.mirrored ? 'on' : ''}" title="${page.mirrored ? 'In MemPalace durchsuchbar' : 'Nicht gespiegelt'}"></span>`;
  return `<div class="wiki-tree-item${active ? ' active' : ''}"
      ${draggable ? `draggable="true" ondragstart="wikiDragStart(event,'${page.id}')" ondragend="wikiDragEnd(event)" ondragover="wikiDragOver(event)" ondragleave="wikiDragLeave(event)" ondrop="wikiDrop(event,'${page.id}')"` : ''}
      onclick="wikiOpenPage('${page.id}')" style="padding-left:${8 + depth * 14}px"
      title="${esc(srcLabel)}">
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
  if (window._wiki.tagFilter) {
    const tf = window._wiki.tagFilter.toLowerCase();
    pages = pages.filter(p => (p.tags || []).some(t => t.toLowerCase() === tf));
  }
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
  if (mode === 'manual' && !window._wiki.tagFilter) {
    // Editable nested tree (parent_id/position), drag-and-drop enabled.
    const byParent = {};
    pages.forEach(p => { (byParent[p.parent_id || ''] = byParent[p.parent_id || ''] || []).push(p); });
    Object.values(byParent).forEach(arr => arr.sort((a, b) => (a.position || 0) - (b.position || 0)));
    const ids = new Set(pages.map(p => p.id));
    const roots = pages.filter(p => !p.parent_id || !ids.has(p.parent_id));
    const seen = new Set();
    const render = (page, depth) => {
      if (seen.has(page.id)) return '';
      seen.add(page.id);
      let html = wikiRowHtml(page, depth, true);
      (byParent[page.id] || []).filter(k => k.id !== page.id).forEach(k => { html += render(k, depth + 1); });
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
      .map(p => wikiRowHtml(p, 0, false)).join('');
    return `<div class="wiki-group-header">${esc(k)} <span style="opacity:.6">· ${groups[k].length}</span></div>${rows}`;
  }).join('');
}

// Tag filter chip row — every distinct tag across the visible scope.
function wikiRenderTagFilter() {
  const host = document.getElementById('wiki-tag-filter');
  if (!host) return;
  const counts = {};
  window._wiki.pages.forEach(p => (p.tags || []).forEach(t => { counts[t] = (counts[t] || 0) + 1; }));
  const tags = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, 24);
  if (!tags.length) { host.innerHTML = ''; return; }
  const active = window._wiki.tagFilter;
  host.innerHTML = (active ? `<span class="wiki-tag active" onclick="wikiToggleTag('${esc(active)}')">${esc(active)} ✕</span>` : '') +
    tags.filter(t => t !== active).map(t =>
      `<span class="wiki-tag" onclick="wikiToggleTag('${esc(t)}')">${esc(t)}</span>`).join('');
}

function wikiToggleTag(tag) {
  window._wiki.tagFilter = (window._wiki.tagFilter === tag) ? '' : tag;
  wikiRenderTree();
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
  // Tag pills (click to filter the tree by that tag).
  const tags = (page.tags || []).map(t =>
    `<span class="wiki-tag" onclick="wikiToggleTag('${esc(t)}')">${esc(t)}</span>`).join(' ');
  meta.innerHTML = bits.join('  ·  ') + (tags ? '<br>' + tags : '');
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
  [bRender, bRaw].forEach(b => { if (b) { b.classList.remove('active'); b.style.background = 'transparent'; b.style.color = 'var(--text-300)'; } });
  if (mode === 'raw') {
    render.style.display = 'none';
    raw.style.display = 'block';
    if (bRaw) { bRaw.classList.add('active'); bRaw.style.background = 'var(--bg-300)'; bRaw.style.color = 'var(--text-000)'; }
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
    if (bRender) { bRender.classList.add('active'); bRender.style.background = 'var(--bg-300)'; bRender.style.color = 'var(--text-000)'; }
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
