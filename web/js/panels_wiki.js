// Wiki view — page tree + CodeMirror (raw) / marked (render) editor.
// Globals (browser-scope, no modules): loadWikiView, wikiSetFilter, wikiNewPage,
// wikiOpenPage, wikiSetMode, wikiSavePage, wikiDeleteCurrent, wikiOpenVersions,
// wikiCloseVersions, wikiPromoteVersion, wikiViewVersion. State on window._wiki.

window._wiki = window._wiki || {
  filter: 'all',
  pages: [],          // flat rows from the tree endpoint
  current: null,      // currently open page object
  mode: 'render',     // 'render' | 'raw'
  cm: null,           // CodeMirror instance
  dirty: false,
};

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

// Build a parent→children map and render the tree recursively.
function wikiRenderTree() {
  const tree = document.getElementById('wiki-tree');
  const pages = window._wiki.pages;
  if (!pages.length) {
    tree.innerHTML = `<div style="padding:12px;color:var(--text-400);font-size:13px">Noch keine Seiten.</div>`;
    return;
  }
  const byParent = {};
  pages.forEach(p => {
    const k = p.parent_id || '';
    (byParent[k] = byParent[k] || []).push(p);
  });
  // Pages whose parent isn't in this filtered set render at top level too.
  const ids = new Set(pages.map(p => p.id));
  const roots = pages.filter(p => !p.parent_id || !ids.has(p.parent_id));
  const seen = new Set();
  const render = (page, depth) => {
    if (seen.has(page.id)) return '';   // guard against cycles
    seen.add(page.id);
    const kids = (byParent[page.id] || []).filter(k => k.id !== page.id);
    const active = window._wiki.current?.id === page.id;
    const scopeBadge = page.scope === 'global' ? '🌐' : page.scope === 'team' ? '👥' : '';
    const srcBadge = (page.source && page.source !== 'manual' && page.source !== 'agent')
      ? `<span title="${esc(page.source)}" style="opacity:.6">↩</span>` : '';
    let html = `<div class="wiki-tree-item${active ? ' active' : ''}" onclick="wikiOpenPage('${page.id}')"
        style="display:flex;align-items:center;gap:6px;padding:5px 8px;padding-left:${8 + depth * 14}px;
        border-radius:5px;cursor:pointer;font-size:13px;color:var(--text-100);
        background:${active ? 'var(--bg-300)' : 'transparent'}">
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${scopeBadge} ${esc(page.title || 'Ohne Titel')}</span>
        ${srcBadge}</div>`;
    kids.forEach(k => { html += render(k, depth + 1); });
    return html;
  };
  tree.innerHTML = roots.map(r => render(r, 0)).join('');
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
  bits.push(`Bereich: ${page.scope}`);
  bits.push(`Version: ${page.current_version || 1}`);
  if (page.manually_edited) bits.push('manuell bearbeitet');
  if (page.source && page.source !== 'manual') bits.push(`Quelle: ${esc(page.source)}`);
  if (page.source_ref) bits.push(`↩ ${esc(page.source_ref)}`);
  meta.textContent = bits.join('  ·  ');
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
