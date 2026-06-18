// panels_workdir.js — Code-Mode chat right-panel "Arbeitsverzeichnis" tab.
// Top: recursive working-dir file tree (collapse/expand, clickable). Bottom:
// inline file renderer (text/code → hljs, markdown → renderMarkdown, image →
// <img>, pdf/html → iframe) + status line (mtime, size) + download.
// Globals only, no modules (fixed load order; loaded after panels_artifacts.js).

// Lazy cache of the active chat's project code-mode info, keyed by project name.
// Avoids re-fetching the project on every tab switch / turn.
window._workdirProjectCache = window._workdirProjectCache || {};
window._workdirSelectedPath = window._workdirSelectedPath || null;

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
  // Not known yet → kick a fetch that will refresh tab visibility on resolve.
  _workdirActiveProject().then(() => { try { updateWorkdirTabVisibility(); } catch (_) {} });
  return false;
}

// Show the Arbeitsverzeichnis tab + hide Artefakte/Referenzen/Websuche in a
// code-mode chat; restore the normal tab set otherwise. (Anhänge + Aktivität
// stay in both.) Called from the right-panel refresh.
function updateWorkdirTabVisibility() {
  const isCode = _workdirIsCodeChat();
  const wdTab = document.querySelector('.right-panel-tab[data-tab="workdir"]');
  if (wdTab) wdTab.style.display = isCode ? '' : 'none';
  ['artifacts', 'references', 'websuche'].forEach(t => {
    const btn = document.querySelector(`.right-panel-tab[data-tab="${t}"]`);
    if (btn) btn.style.display = isCode ? 'none' : '';
  });
  // If the active tab just got hidden, fall back to a sensible visible one.
  if (isCode && ['artifacts', 'references', 'websuche'].includes(state.rightPanelTab)) {
    if (typeof switchRightTab === 'function') switchRightTab('workdir');
  }
}

// ── Tree (top area) ──────────────────────────────────────────────────────────
function _workdirRenderTree(nodes) {
  if (!nodes || !nodes.length) return '<div class="pt-empty">Leer.</div>';
  return nodes.map(n => {
    if (n.type === 'dir') {
      return `<div class="pt-branch pt-realdir">
        <div class="pt-row pt-realrow" onclick="ptToggleRealDir(this)">
          ${_ptCaret(false)}
          <span class="pt-icon">${_PT_ICON.folders}</span>
          <span class="pt-label">${esc(n.name)}</span>
        </div>
        <div class="pt-children" style="display:none">${_workdirRenderTree(n.children || [])}</div>
      </div>`;
    }
    const sel = (window._workdirSelectedPath === n.path) ? ' pt-selected' : '';
    const meta = encodeURIComponent(JSON.stringify({ path: n.path, name: n.name, size: n.size || 0, mtime: n.mtime || 0 }));
    return `<div class="pt-row pt-realfile${sel}" data-path="${esc(n.path || '')}" title="${esc(n.path || n.name)}"
         onclick="selectWorkdirFile('${meta}')">
      <span class="pt-icon pt-fileicon">${_PT_ICON.file}</span>
      <span class="pt-label">${esc(n.name)}</span>
    </div>`;
  }).join('');
}

async function refreshWorkdirPanelTree() {
  const host = document.getElementById('workdir-panel-tree');
  if (!host) return;
  const proj = await _workdirActiveProject();
  if (!proj || !proj.working_dir) {
    host.innerHTML = '<div class="pt-empty">Kein Arbeitsverzeichnis.</div>';
    return;
  }
  host.innerHTML = '<div class="pt-loading">Lädt…</div>';
  try {
    const data = await API.get(`/v1/agents/${proj.agent}/projects/${encodeURIComponent(proj.name)}/folder-tree?path=${encodeURIComponent(proj.working_dir)}`);
    if (data.error) { host.innerHTML = `<div class="pt-empty">${esc(data.error)}</div>`; return; }
    host.innerHTML = _workdirRenderTree(data.tree || []);
  } catch (e) {
    host.innerHTML = '<div class="pt-empty">Verzeichnis konnte nicht geladen werden.</div>';
  }
}

// Entry point from switchRightTab.
function renderWorkdirPane() {
  refreshWorkdirPanelTree();
}

// ── File select + render (bottom area) ───────────────────────────────────────
function _fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

const _WORKDIR_IMG_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico'];
const _WORKDIR_MD_EXT = ['md', 'markdown'];

async function selectWorkdirFile(metaEnc) {
  let meta;
  try { meta = JSON.parse(decodeURIComponent(metaEnc)); } catch (e) { return; }
  window._workdirSelectedPath = meta.path;
  window._workdirSelectedMeta = meta;
  // Highlight selection.
  document.querySelectorAll('#workdir-panel-tree .pt-realfile').forEach(r => {
    r.classList.toggle('pt-selected', r.dataset.path === meta.path);
  });
  const head = document.getElementById('workdir-view-head');
  const metaEl = document.getElementById('workdir-view-meta');
  const body = document.getElementById('workdir-view-body');
  if (head) head.style.display = '';
  if (metaEl) {
    const when = meta.mtime ? new Date(meta.mtime * 1000).toLocaleString() : '';
    metaEl.textContent = `${meta.name} · ${_fmtBytes(meta.size)}${when ? ' · ' + when : ''}`;
  }
  if (!body) return;
  body.innerHTML = '<div class="workdir-view-empty">Lädt…</div>';

  const ext = (meta.name.split('.').pop() || '').toLowerCase();
  const dlUrl = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(meta.path)}`;

  // Image: load via authenticated fetch → blob URL (the endpoint needs Bearer).
  if (_WORKDIR_IMG_EXT.includes(ext)) {
    try {
      const resp = await fetch(dlUrl, { headers: API._headers() });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const u = URL.createObjectURL(blob);
      body.innerHTML = `<div style="padding:8px"><img src="${u}" alt="${esc(meta.name)}"></div>`;
    } catch (e) {
      body.innerHTML = `<div class="workdir-view-empty">Bild konnte nicht geladen werden (${esc(e.message || e)}).</div>`;
    }
    return;
  }
  // PDF: blob → iframe (native viewer).
  if (ext === 'pdf') {
    try {
      const resp = await fetch(dlUrl, { headers: API._headers() });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const u = URL.createObjectURL(blob);
      body.innerHTML = `<iframe src="${u}"></iframe>`;
    } catch (e) {
      body.innerHTML = `<div class="workdir-view-empty">PDF konnte nicht geladen werden (${esc(e.message || e)}).</div>`;
    }
    return;
  }
  // Text / code / markdown: fetch text and render.
  try {
    const resp = await fetch(dlUrl, { headers: API._headers() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const text = await resp.text();
    if (_WORKDIR_MD_EXT.includes(ext)) {
      body.innerHTML = `<div class="artifact-markdown msg-content" style="padding:10px">${renderMarkdown(text)}</div>`;
      body.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch (_) {} });
    } else {
      const lang = (typeof hljs !== 'undefined' && hljs.getLanguage(ext)) ? ext : 'plaintext';
      let html;
      try { html = hljs.highlight(text, { language: lang }).value; }
      catch (_) { html = esc(text); }
      body.innerHTML = `<pre class="artifact-code" style="margin:0;padding:10px"><code class="hljs">${html}</code></pre>`;
    }
  } catch (e) {
    body.innerHTML = `<div class="workdir-view-empty">Datei konnte nicht geladen werden (${esc(e.message || e)}).</div>`;
  }
}

function downloadWorkdirFile() {
  const meta = window._workdirSelectedMeta;
  if (!meta || !meta.path) return;
  const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(meta.path)}&download=1`;
  // Authenticated fetch → blob → anchor (the endpoint needs Bearer).
  fetch(url, { headers: API._headers() })
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
    .then(blob => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = meta.name || 'datei';
      document.body.appendChild(a); a.click(); a.remove();
    })
    .catch(e => showToast('Download fehlgeschlagen: ' + (e.message || e), true));
}
