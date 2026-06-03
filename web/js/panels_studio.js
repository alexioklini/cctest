// panels_studio.js — Output Presets "Generate" panel + Studio browse, on the
// project detail page's "Studio" tab. View over the SHARED project_outputs store
// (POST …/generate · GET …/outputs · rename · delete). No generation logic here —
// the server owns it (engine/output_gen.py). Globals only (no modules), loaded
// after panels_projects.js, before init.js.

// Preset metadata — mirrors engine/output_presets.PRESETS (icon/label) + the kinds
// the generate endpoint validates. Research reports / audio overviews are also
// project_outputs kinds (rendered in the browse groups) but generated elsewhere.
const STUDIO_PRESETS = [
  { kind: 'study_guide', icon: '📖', label: 'Study Guide', blurb: 'Konzepte · Begriffe · Wiederholungsfragen' },
  { kind: 'briefing',    icon: '📋', label: 'Briefing',    blurb: 'Kurzfassung · Kernpunkte · Implikationen' },
  { kind: 'faq',         icon: '❓', label: 'FAQ',          blurb: 'Belegte Frage-/Antwort-Paare' },
  { kind: 'timeline',    icon: '🕒', label: 'Timeline',    blurb: 'Datierte Ereignisse (chronologisch)' },
];

// kind → display label/icon for browse-group headers (covers presets + the
// later research/audio kinds so they render with a sensible header too).
const STUDIO_KIND_META = {
  study_guide:     { icon: '📖', label: 'Study Guides' },
  briefing:        { icon: '📋', label: 'Briefings' },
  faq:             { icon: '❓', label: 'FAQ' },
  timeline:        { icon: '🕒', label: 'Timelines' },
  research_report: { icon: '🔬', label: 'Research Reports' },
  audio_overview:  { icon: '🎧', label: 'Audio Overviews' },
};

let _studioPollHandle = null;

function _studioKindMeta(kind) {
  return STUDIO_KIND_META[kind] || { icon: '📄', label: kind };
}

// Entry point from setProjectChatsFilter('studio').
function loadProjectStudio(agentId, projectName) {
  const container = document.getElementById('project-detail-studio');
  if (!container) return;
  state._studioAgent = agentId;
  state._studioProject = projectName;
  // Build the static shell once (Generate panel + outputs mount), then fill.
  container.innerHTML = `
    <div id="studio-generate-panel"></div>
    <div id="studio-outputs" style="margin-top:18px"></div>`;
  renderStudioGeneratePanel();
  refreshStudioOutputs();
}

function reloadProjectStudio() {
  if (state._studioAgent && state._studioProject) refreshStudioOutputs();
}

// ─── Generate panel (preset cards + options) ───────────────────────────────

function renderStudioGeneratePanel() {
  const el = document.getElementById('studio-generate-panel');
  if (!el) return;
  const hasSources = _studioProjectHasSources();
  if (!hasSources) {
    el.innerHTML = `
      <div class="studio-empty-sources" style="padding:14px 16px;border:1px solid var(--border-200);border-radius:10px;color:var(--text-300);font-size:13px">
        ⓘ Dieses Projekt hat noch keine Quellen.
        <div style="margin-top:6px;color:var(--text-400)">Füge Dateien, Eingabe-Ordner oder Web-Adressen hinzu — dann lassen sich daraus Ausgaben generieren.</div>
      </div>`;
    return;
  }
  const cards = STUDIO_PRESETS.map(p => `
    <div class="studio-card" style="flex:1 1 150px;min-width:140px;border:1px solid var(--border-200);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px">
      <div style="font-size:22px">${p.icon}</div>
      <div style="font-weight:600;font-size:13px">${esc(p.label)}</div>
      <div style="font-size:11px;color:var(--text-400);flex:1">${esc(p.blurb)}</div>
      <button class="btn-secondary" style="padding:4px 10px;font-size:12px" onclick="studioGenerate('${esc(p.kind)}')">Generieren</button>
    </div>`).join('');
  el.innerHTML = `
    <div style="font-weight:600;font-size:14px;margin-bottom:4px">Aus den Quellen generieren</div>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:10px">Erzeuge eine belegte Ausgabe aus den Quellen dieses Projekts.</div>
    <div style="display:flex;flex-wrap:wrap;gap:10px">${cards}</div>
    <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;font-size:12px;color:var(--text-300)">
      <label style="display:flex;align-items:center;gap:6px">Fokus:
        <input id="studio-opt-focus" type="text" placeholder="optional" style="padding:3px 8px;font-size:12px;border:1px solid var(--border-200);border-radius:6px;background:var(--bg-000);color:var(--text-100);width:180px">
      </label>
      <label style="display:flex;align-items:center;gap:6px">Länge:
        <select id="studio-opt-length" style="padding:3px 8px;font-size:12px;border:1px solid var(--border-200);border-radius:6px;background:var(--bg-000);color:var(--text-100)">
          <option value="short">Kurz</option>
          <option value="std" selected>Standard</option>
          <option value="long">Lang</option>
        </select>
      </label>
    </div>`;
}

// Heuristic mirror of the server's has-sources guard (chunks / web_urls /
// input_folders). The endpoint enforces it authoritatively; this just disables
// the cards early for a clean empty state.
function _studioProjectHasSources() {
  const p = state._projectDetail || {};
  return !!(p.chunks || (p.web_urls && p.web_urls.length) || (p.input_folders && p.input_folders.length));
}

async function studioGenerate(kind) {
  const agentId = state._studioAgent, projectName = state._studioProject;
  if (!agentId || !projectName) return;
  const focus = (document.getElementById('studio-opt-focus')?.value || '').trim();
  const length = document.getElementById('studio-opt-length')?.value || 'std';
  try {
    await API.generateProjectOutput(agentId, projectName, kind, { focus, length });
    showToast('Generierung gestartet — erscheint unten, wenn fertig');
    refreshStudioOutputs();   // shows the new generating row + starts the poll
  } catch (e) {
    showToast('Generierung fehlgeschlagen: ' + (e.message || e), true);
  }
}

// Regenerate replays a row's stored kind + opts → a NEW output row (history kept).
async function studioRegenerate(kind, focus, length) {
  const agentId = state._studioAgent, projectName = state._studioProject;
  if (!agentId || !projectName) return;
  try {
    await API.generateProjectOutput(agentId, projectName, kind, { focus: focus || '', length: length || 'std' });
    showToast('Neu generieren gestartet');
    refreshStudioOutputs();
  } catch (e) {
    showToast('Neu generieren fehlgeschlagen: ' + (e.message || e), true);
  }
}

// ─── Outputs browse (grouped by kind, live status) ─────────────────────────

async function refreshStudioOutputs() {
  const agentId = state._studioAgent, projectName = state._studioProject;
  if (!agentId || !projectName) return;
  let outputs = [];
  try {
    const data = await API.listProjectOutputs(agentId, projectName);
    outputs = data.outputs || [];
  } catch (e) {
    const el = document.getElementById('studio-outputs');
    if (el) el.innerHTML = `<div style="padding:14px;color:var(--error);font-size:13px">${esc(e.message || e)}</div>`;
    return;
  }
  state._studioOutputs = outputs;
  renderStudioOutputs(outputs);
  // Poll while anything is still generating (mirrors panels_background.js).
  if (outputs.some(o => o.status === 'generating')) startStudioPoll();
  else stopStudioPoll();
}

function renderStudioOutputs(outputs) {
  const el = document.getElementById('studio-outputs');
  if (!el) return;
  if (!outputs.length) {
    el.innerHTML = `
      <div style="padding:18px 8px;color:var(--text-400);font-size:13px;text-align:center">
        Noch keine Ausgaben. Generiere oben eine Study Guide, ein Briefing, eine FAQ oder eine Timeline aus den Quellen dieses Projekts.
      </div>`;
    return;
  }
  // Group by kind, preserving the newest-first order within each group.
  const groups = {};
  for (const o of outputs) (groups[o.kind] = groups[o.kind] || []).push(o);
  const order = Object.keys(groups).sort();
  el.innerHTML = order.map(kind => {
    const meta = _studioKindMeta(kind);
    const cards = groups[kind].map(studioOutputCardHtml).join('');
    return `
      <div class="studio-group" style="margin-bottom:16px">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px">${meta.icon} ${esc(meta.label)} (${groups[kind].length})</div>
        <div style="display:flex;flex-wrap:wrap;gap:10px">${cards}</div>
      </div>`;
  }).join('');
}

function studioOutputCardHtml(o) {
  const when = o.created_at ? formatTimeAgo(new Date(o.created_at * 1000)) : '';
  let statusLine, actions;
  if (o.status === 'generating') {
    statusLine = `<span style="color:var(--text-400)">⟳ generiert… ${esc(when)}</span>`;
    actions = '';
  } else if (o.status === 'error') {
    statusLine = `<span style="color:var(--error)" title="${esc(o.error || '')}">⚠ Fehler</span>`;
    actions = `<button class="studio-act" onclick="studioDeleteOutput('${esc(o.output_id)}')" style="background:none;border:none;color:var(--text-400);cursor:pointer;font-size:12px;padding:2px 4px">Entfernen</button>`;
  } else {
    const cites = (o.citations || 0) + ' Zitate';
    statusLine = `<span style="color:var(--text-400)">${cites} · ${esc(when)}</span>`;
    actions = `
      <button class="studio-act" onclick="studioOpenOutput('${esc(o.output_id)}')" style="background:none;border:none;color:var(--accent-main-200,#6c5ce7);cursor:pointer;font-size:12px;padding:2px 4px">Öffnen</button>
      <button class="studio-act" onclick="studioOutputMenu(event, '${esc(o.output_id)}')" style="background:none;border:none;color:var(--text-400);cursor:pointer;font-size:12px;padding:2px 4px" title="Weitere Optionen">⋯</button>`;
  }
  return `
    <div class="studio-card" data-oid="${esc(o.output_id)}" style="flex:1 1 220px;min-width:200px;max-width:320px;border:1px solid var(--border-200);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px">
      <div style="font-weight:600;font-size:13px;line-height:1.3">📄 ${esc(o.title || o.kind)}</div>
      <div style="font-size:11px">${statusLine}</div>
      <div style="display:flex;gap:4px;margin-top:2px">${actions}</div>
    </div>`;
}

// ─── Open (.md viewer modal) ───────────────────────────────────────────────

async function studioOpenOutput(outputId) {
  const o = (state._studioOutputs || []).find(x => x.output_id === outputId);
  if (!o) return;
  if (!o.artifact_id) { showToast('Keine Datei für diese Ausgabe gefunden', true); return; }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:900px;width:90vw;max-height:88vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">📄 ${esc(o.title || o.kind)}</span>
      <a class="btn-secondary" style="margin-left:auto;padding:3px 10px;font-size:12px;text-decoration:none" href="${esc(API.getArtifactDownloadUrl(o.artifact_id))}" target="_blank" rel="noopener">Herunterladen</a>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="studio-view-body" style="flex:1;overflow:auto;padding:14px 18px"><div class="msg-content">Lädt…</div></div>
  </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const body = overlay.querySelector('.studio-view-body');
  try {
    const data = await API.getArtifactContent(o.artifact_id);
    const text = (data && data.content) || '';
    body.innerHTML = `<div class="msg-content">${renderMarkdown(text)}</div>`;
    body.querySelectorAll('pre code').forEach(elc => { try { hljs.highlightElement(elc); } catch (_) {} });
  } catch (e) {
    body.innerHTML = `<div style="color:var(--error)">Inhalt konnte nicht geladen werden: ${esc(e.message || e)}</div>`;
  }
}

// ─── Per-output menu (Rename · Download · Regenerate · Delete) ─────────────

function studioOutputMenu(event, outputId) {
  event.stopPropagation();
  document.querySelectorAll('.ctx-menu').forEach(m => m.remove());
  const o = (state._studioOutputs || []).find(x => x.output_id === outputId);
  if (!o) return;
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--bg-000);border:1px solid var(--border-200);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:4px;min-width:150px';
  const item = (label, onclick, danger) =>
    `<div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:${danger ? 'var(--error)' : 'var(--text-200)'}"
        onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''"
        onclick="${onclick}; this.closest('.ctx-menu').remove()">${label}</div>`;
  // Regenerate replays the kind (a NEW row); the original focus isn't surfaced
  // on the list row, so it re-runs with default options for v1.
  menu.innerHTML =
    item('Umbenennen', `studioRenameOutput('${esc(outputId)}')`) +
    item('Neu generieren', `studioRegenerate('${esc(o.kind)}', '', 'std')`) +
    item('Herunterladen', `window.open(API.getArtifactDownloadUrl('${esc(o.artifact_id)}'), '_blank')`) +
    item('Löschen', `studioDeleteOutput('${esc(outputId)}')`, true);
  document.body.appendChild(menu);
  const r = event.target.getBoundingClientRect();
  menu.style.left = Math.min(r.left, window.innerWidth - 170) + 'px';
  menu.style.top = (r.bottom + 4) + 'px';
  setTimeout(() => document.addEventListener('click', function _cl() { menu.remove(); document.removeEventListener('click', _cl); }), 10);
}

async function studioRenameOutput(outputId) {
  const o = (state._studioOutputs || []).find(x => x.output_id === outputId);
  if (!o) return;
  const title = await showPrompt('Neuer Titel:', o.title || '', 'Ausgabe umbenennen');
  if (title === null) return;
  const t = title.trim();
  if (!t) { showToast('Titel darf nicht leer sein', true); return; }
  try {
    await API.renameProjectOutput(state._studioAgent, state._studioProject, outputId, t);
    refreshStudioOutputs();
  } catch (e) { showToast('Umbenennen fehlgeschlagen: ' + (e.message || e), true); }
}

async function studioDeleteOutput(outputId) {
  const o = (state._studioOutputs || []).find(x => x.output_id === outputId);
  if (!o) return;
  if (!await showConfirmDanger(`„${o.title || o.kind}“ löschen? Die Ausgabe und ihre Datei werden entfernt. Das kann nicht rückgängig gemacht werden.`, 'Ausgabe löschen', 'Löschen')) return;
  try {
    await API.deleteProjectOutput(state._studioAgent, state._studioProject, outputId);
    showToast('Ausgabe gelöscht');
    refreshStudioOutputs();
  } catch (e) { showToast('Löschen fehlgeschlagen: ' + (e.message || e), true); }
}

// ─── Poll (live generating→ready, mirrors panels_background.js) ────────────

function startStudioPoll() {
  if (_studioPollHandle) return;
  _studioPollHandle = setInterval(() => {
    // Only poll while still on the Studio tab of this project.
    if (state.currentView !== 'project-detail' || state._projectChatsFilter !== 'studio') { stopStudioPoll(); return; }
    refreshStudioOutputs();
  }, 2500);
}

function stopStudioPoll() {
  if (_studioPollHandle) { clearInterval(_studioPollHandle); _studioPollHandle = null; }
}
