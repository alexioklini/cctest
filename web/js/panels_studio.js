// panels_studio.js — Output Presets "Generate" panel + Studio browse, on the
// project detail page's "Studio" tab. View over the SHARED project_outputs store
// (POST …/generate · GET …/outputs · rename · delete). No generation logic here —
// the server owns it (engine/output_gen.py). Globals only (no modules), loaded
// after panels_projects.js, before init.js.

// Inline Feather-style SVGs (viewBox 0 0 24 24, stroke=currentColor) so Studio
// matches the rest of the UI's icon convention instead of using emoji. `size`
// defaults to 1em so each icon inherits the surrounding text size/colour.
function studioIcon(name, size) {
  const s = size || '1em';
  // Custom presets share one icon — their kind is "custom:<id>".
  if (name && name.indexOf('custom:') === 0) name = 'custom';
  const open = `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-0.15em">`;
  const paths = {
    // book — study guide
    study_guide: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    // clipboard — briefing
    briefing: '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/>',
    // help circle — faq
    faq: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12" y2="17"/>',
    // clock — timeline
    timeline: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    // headphones — audio overview
    audio_overview: '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h3zM3 19a2 2 0 0 0 2 2h1a1 1 0 0 0 1-1v-3a1 1 0 0 0-1-1H3z"/>',
    // flask/search — research report
    research_report: '<path d="M9 2v6.5L4.6 17a2 2 0 0 0 1.8 3h11.2a2 2 0 0 0 1.8-3L15 8.5V2"/><line x1="8" y1="2" x2="16" y2="2"/><line x1="7" y1="14" x2="17" y2="14"/>',
    // file-text — generic document / output
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    // message-square — from chats
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    // paperclip — chat artifact
    attachment: '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    // trash-2 — delete
    trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    // layers — custom preset (user-defined "Transformation")
    custom: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    // plus — new preset card
    plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    // edit-3 — pencil (edit preset)
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  };
  return open + (paths[name] || paths.file) + '</svg>';
}

// Preset metadata — mirrors engine/output_presets.PRESETS (icon/label) + the kinds
// the generate endpoint validates. Research reports / audio overviews are also
// project_outputs kinds (rendered in the browse groups) but generated elsewhere.
const STUDIO_PRESETS = [
  { kind: 'study_guide',    label: 'Study Guide',    blurb: 'Konzepte · Begriffe · Wiederholungsfragen' },
  { kind: 'briefing',       label: 'Briefing',       blurb: 'Kurzfassung · Kernpunkte · Implikationen' },
  { kind: 'faq',            label: 'FAQ',             blurb: 'Belegte Frage-/Antwort-Paare' },
  { kind: 'timeline',       label: 'Timeline',       blurb: 'Datierte Ereignisse (chronologisch)' },
  { kind: 'audio_overview', label: 'Audio Overview', blurb: 'Podcast: zwei Hosts (englisch) · .mp3' },
];

// kind → display label for browse-group headers (covers presets + the later
// research/audio kinds so they render with a sensible header too). The icon is
// derived from `kind` via studioIcon().
const STUDIO_KIND_META = {
  study_guide:     { label: 'Study Guides' },
  briefing:        { label: 'Briefings' },
  faq:             { label: 'FAQ' },
  timeline:        { label: 'Timelines' },
  research_report: { label: 'Research Reports' },
  audio_overview:  { label: 'Audio Overviews' },
};

let _studioPollHandle = null;

function _studioKindMeta(kind) {
  if (kind && kind.indexOf('custom:') === 0) {
    const p = (state._studioCustomPresets || []).find(x => 'custom:' + x.id === kind);
    return { label: p ? p.label : 'Eigene Vorlage' };
  }
  return STUDIO_KIND_META[kind] || { label: kind };
}

// Fetch the user-defined presets (best-effort — the built-in cards render
// regardless). Cached on state so browse-group headers can resolve labels.
async function _loadStudioPresets() {
  try {
    const data = await API.listStudioPresets();
    state._studioCustomPresets = data.presets || [];
  } catch (_) {
    state._studioCustomPresets = state._studioCustomPresets || [];
  }
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
  // Custom presets arrive async; re-render the cards once they're known.
  _loadStudioPresets().then(renderStudioGeneratePanel);
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
      <div style="color:var(--text-300)">${studioIcon(p.kind, '22px')}</div>
      <div style="font-weight:600;font-size:13px">${esc(p.label)}</div>
      <div style="font-size:11px;color:var(--text-400);flex:1">${esc(p.blurb)}</div>
      <button class="btn-secondary" style="padding:4px 10px;font-size:12px" onclick="studioGenerate('${esc(p.kind)}')">Generieren</button>
    </div>`).join('');
  // User-defined presets ("Transformationen") — editable/deletable cards + a
  // dashed "Neue Vorlage" card. Per-source presets run once per project source
  // and file one wiki page per document.
  const customCards = (state._studioCustomPresets || []).map(p => `
    <div class="studio-card" style="flex:1 1 150px;min-width:140px;border:1px solid var(--border-200);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <span style="color:var(--text-300)">${studioIcon('custom', '22px')}</span>
        <span style="display:flex;gap:2px">
          <button title="Vorlage bearbeiten" onclick="studioOpenPresetEditor('${esc(p.id)}')"
                  style="background:none;border:none;color:var(--text-400);cursor:pointer;padding:2px">${studioIcon('edit', '14px')}</button>
          <button title="Vorlage löschen" onclick="studioDeletePreset('${esc(p.id)}')"
                  style="background:none;border:none;color:var(--text-400);cursor:pointer;padding:2px">${studioIcon('trash', '14px')}</button>
        </span>
      </div>
      <div style="font-weight:600;font-size:13px">${esc(p.label)}</div>
      <div style="font-size:11px;color:var(--text-400);flex:1">${p.per_source ? 'Pro Quelle · eine Wiki-Seite je Dokument' : 'Eigene Vorlage · Gesamtkorpus'}</div>
      <button class="btn-secondary" style="padding:4px 10px;font-size:12px" onclick="studioGenerate('custom:${esc(p.id)}')">Generieren</button>
    </div>`).join('');
  const newCard = `
    <div class="studio-card" onclick="studioOpenPresetEditor()" style="flex:1 1 150px;min-width:140px;border:1px dashed var(--border-200);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px;cursor:pointer;align-items:flex-start">
      <div style="color:var(--text-400)">${studioIcon('plus', '22px')}</div>
      <div style="font-weight:600;font-size:13px;color:var(--text-300)">Neue Vorlage</div>
      <div style="font-size:11px;color:var(--text-400);flex:1">Eigene Anweisung — einmal definieren, beliebig oft anwenden (optional pro Quelle)</div>
    </div>`;
  el.innerHTML = `
    <div style="font-weight:600;font-size:14px;margin-bottom:4px">Aus den Quellen generieren</div>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:10px">Erzeuge eine belegte Ausgabe aus den Quellen dieses Projekts.</div>
    <div style="display:flex;flex-wrap:wrap;gap:10px">${cards}${customCards}${newCard}</div>
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

// ─── Custom preset editor (create/edit/delete user-defined "Transformationen") ──

function studioOpenPresetEditor(presetId) {
  const p = presetId ? (state._studioCustomPresets || []).find(x => x.id === presetId) : null;
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.display = 'flex';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  const inputStyle = 'width:100%;padding:6px 10px;font-size:13px;border:1px solid var(--border-200);border-radius:6px;background:var(--bg-000);color:var(--text-100)';
  overlay.innerHTML = `<div class="modal-content" style="max-width:560px;width:92vw" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div class="modal-title">${p ? 'Vorlage bearbeiten' : 'Neue Vorlage'}</div>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="display:flex;flex-direction:column;gap:12px">
      <label style="font-size:12px;color:var(--text-300)">Name
        <input id="studio-preset-label" type="text" maxlength="80" style="${inputStyle};margin-top:4px" value="${esc(p ? p.label : '')}" placeholder="z. B. Literatur-Review">
      </label>
      <label style="font-size:12px;color:var(--text-300)">Anweisungen an das Modell
        <textarea id="studio-preset-instructions" rows="8" style="${inputStyle};margin-top:4px;resize:vertical;font-family:inherit" placeholder="z. B. Extrahiere aus dem Dokument: Fragestellung, Methodik, Kernergebnisse (nummeriert), Limitationen. Halte jeden Abschnitt auf 2–3 Sätze.">${esc(p ? p.instructions : '')}</textarea>
      </label>
      <label style="font-size:12px;color:var(--text-300)">Titel-Präfix der Ausgaben (optional, sonst der Name)
        <input id="studio-preset-prefix" type="text" maxlength="80" style="${inputStyle};margin-top:4px" value="${esc(p ? (p.title_prefix || '') : '')}" placeholder="z. B. Review">
      </label>
      <label style="display:flex;align-items:flex-start;gap:8px;font-size:12px;color:var(--text-300);cursor:pointer">
        <input id="studio-preset-per-source" type="checkbox" ${p && p.per_source ? 'checked' : ''} style="margin-top:2px">
        <span><b>Pro Quelle anwenden</b> — die Vorlage läuft einzeln über jedes Dokument des Projekts und legt je Quelle eine Wiki-Seite an (statt einer Gesamtausgabe über den Korpus). Belege/Zitate bleiben in beiden Modi Pflicht.</span>
      </label>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:4px">
        <button class="btn-secondary" style="padding:6px 14px;font-size:13px" onclick="this.closest('.modal-overlay').remove()">Abbrechen</button>
        <button style="padding:6px 14px;font-size:13px;background:var(--accent-brand);border:none;color:#fff;border-radius:6px;cursor:pointer" onclick="studioSavePreset('${esc(presetId || '')}', this)">Speichern</button>
      </div>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#studio-preset-label').focus();
}

async function studioSavePreset(presetId, btn) {
  const overlay = btn.closest('.modal-overlay');
  const label = overlay.querySelector('#studio-preset-label').value.trim();
  const instructions = overlay.querySelector('#studio-preset-instructions').value.trim();
  const title_prefix = overlay.querySelector('#studio-preset-prefix').value.trim();
  const per_source = overlay.querySelector('#studio-preset-per-source').checked;
  if (!label || !instructions) { showToast('Name und Anweisungen sind erforderlich', true); return; }
  try {
    if (presetId) await API.updateStudioPreset(presetId, { label, instructions, title_prefix, per_source });
    else await API.createStudioPreset({ label, instructions, title_prefix, per_source });
    overlay.remove();
    showToast(presetId ? 'Vorlage aktualisiert' : 'Vorlage angelegt');
    await _loadStudioPresets();
    renderStudioGeneratePanel();
  } catch (e) {
    showToast('Speichern fehlgeschlagen: ' + (e.message || e), true);
  }
}

async function studioDeletePreset(presetId) {
  const p = (state._studioCustomPresets || []).find(x => x.id === presetId);
  if (!p) return;
  if (!await showConfirmDanger(`Vorlage „${p.label}“ löschen? Bereits generierte Ausgaben und Wiki-Seiten bleiben erhalten.`, 'Vorlage löschen', 'Löschen')) return;
  try {
    await API.deleteStudioPreset(presetId);
    showToast('Vorlage gelöscht');
    await _loadStudioPresets();
    renderStudioGeneratePanel();
  } catch (e) {
    showToast('Löschen fehlgeschlagen: ' + (e.message || e), true);
  }
}

// ─── Outputs browse (grouped by kind, live status) ─────────────────────────

async function refreshStudioOutputs() {
  const agentId = state._studioAgent, projectName = state._studioProject;
  if (!agentId || !projectName) return;
  let outputs = [], chatArts = [];
  try {
    const data = await API.listProjectOutputs(agentId, projectName);
    outputs = data.outputs || [];
    chatArts = data.chat_artifacts || [];
  } catch (e) {
    const el = document.getElementById('studio-outputs');
    if (el) el.innerHTML = `<div style="padding:14px;color:var(--error);font-size:13px">${esc(e.message || e)}</div>`;
    return;
  }
  state._studioOutputs = outputs;
  state._studioChatArtifacts = chatArts;
  renderStudioOutputs(outputs, chatArts);
  // Poll while anything is still generating (mirrors panels_background.js).
  if (outputs.some(o => o.status === 'generating')) startStudioPoll();
  else stopStudioPoll();
}

function renderStudioOutputs(outputs, chatArts) {
  const el = document.getElementById('studio-outputs');
  if (!el) return;
  chatArts = chatArts || [];
  if (!outputs.length && !chatArts.length) {
    el.innerHTML = `
      <div style="padding:18px 8px;color:var(--text-400);font-size:13px;text-align:center">
        Noch keine Ausgaben. Generiere oben eine Study Guide, ein Briefing, eine FAQ oder eine Timeline aus den Quellen dieses Projekts.
      </div>`;
    return;
  }
  // Generated deliverables — grouped by kind, newest-first within each group.
  const groups = {};
  for (const o of outputs) (groups[o.kind] = groups[o.kind] || []).push(o);
  const order = Object.keys(groups).sort();
  const generated = order.map(kind => {
    const meta = _studioKindMeta(kind);
    const cards = groups[kind].map(studioOutputCardHtml).join('');
    return `
      <div class="studio-group" style="margin-bottom:16px">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px;display:flex;align-items:center;gap:6px"><span style="color:var(--text-300)">${studioIcon(kind)}</span>${esc(meta.label)} (${groups[kind].length})</div>
        <div style="display:flex;flex-wrap:wrap;gap:10px">${cards}</div>
      </div>`;
  }).join('');
  // Chat-produced output artifacts (live join, separate section so provenance
  // is clear and curated deliverables aren't conflated with chat files).
  let chatSection = '';
  if (chatArts.length) {
    const cards = chatArts.map(studioChatArtifactCardHtml).join('');
    chatSection = `
      <div class="studio-group" style="margin-bottom:16px;${outputs.length ? 'border-top:1px solid var(--border-100);padding-top:14px' : ''}">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px;display:flex;align-items:center;gap:6px"><span style="color:var(--text-300)">${studioIcon('chat')}</span>Aus Projekt-Chats (${chatArts.length})</div>
        <div style="display:flex;flex-wrap:wrap;gap:10px">${cards}</div>
      </div>`;
  }
  el.innerHTML = generated + chatSection;
}

function studioChatArtifactCardHtml(a) {
  const when = a.updated_at ? formatTimeAgo(new Date(a.updated_at * 1000)) : '';
  const aid = esc(a.artifact_id);
  return `
    <div class="studio-card" data-aid="${aid}" style="flex:1 1 220px;min-width:200px;max-width:320px;border:1px solid var(--border-200);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px">
      <div style="font-weight:600;font-size:13px;line-height:1.3;display:flex;align-items:center;gap:6px"><span style="color:var(--text-300)">${studioIcon('attachment')}</span>${esc(a.name || '(Datei)')}</div>
      <div style="font-size:11px;color:var(--text-400)">v${a.latest_version || 1} · ${esc(when)}</div>
      <div style="display:flex;gap:6px;margin-top:4px">
        <button class="studio-act" onclick="studioOpenChatArtifact('${aid}')"
                style="background:var(--accent-brand);border:none;color:#fff;cursor:pointer;font-size:12px;padding:4px 12px;border-radius:6px">Öffnen</button>
        <button class="studio-act" onclick="studioArchiveChatArtifact('${aid}')"
                style="background:var(--bg-100);border:1px solid var(--border-200);color:var(--text-200);cursor:pointer;font-size:12px;padding:4px 12px;border-radius:6px">Archivieren</button>
        <button class="studio-act" onclick="studioDeleteChatArtifact('${aid}')"
                style="background:none;border:1px solid var(--border-200);color:var(--error);cursor:pointer;font-size:12px;padding:4px 10px;border-radius:6px;display:inline-flex;align-items:center" title="Löschen">${studioIcon('trash')}</button>
      </div>
    </div>`;
}

async function studioOpenChatArtifact(artifactId) {
  const a = (state._studioChatArtifacts || []).find(x => x.artifact_id === artifactId);
  const title = a ? (a.name || 'Datei') : 'Datei';
  const isText = !a || !/\.(png|jpe?g|gif|webp|svg|pdf)$/i.test(a.name || '');
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:900px;width:90vw;max-height:88vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600;display:inline-flex;align-items:center;gap:6px"><span style="color:var(--text-300)">${studioIcon('attachment')}</span>${esc(title)}</span>
      <a class="btn-secondary" style="margin-left:auto;padding:3px 10px;font-size:12px;text-decoration:none" href="${esc(API.getArtifactDownloadUrl(artifactId))}" target="_blank" rel="noopener">Herunterladen</a>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="studio-view-body" style="flex:1;overflow:auto;padding:14px 18px"><div class="msg-content">Lädt…</div></div>
  </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const body = overlay.querySelector('.studio-view-body');
  if (!isText) {
    body.innerHTML = `<img src="${esc(API.getArtifactDownloadUrl(artifactId))}" style="max-width:100%;height:auto" alt="${esc(title)}">`;
    return;
  }
  try {
    const data = await API.getArtifactContent(artifactId);
    const text = (data && data.content) || '';
    body.innerHTML = `<div class="msg-content">${renderMarkdown(text)}</div>`;
    body.querySelectorAll('pre code').forEach(elc => { try { hljs.highlightElement(elc); } catch (_) {} });
  } catch (e) {
    body.innerHTML = `<div style="color:var(--error)">Inhalt konnte nicht geladen werden: ${esc(e.message || e)}</div>`;
  }
}

async function studioArchiveChatArtifact(artifactId) {
  try {
    await API.archiveProjectArtifact(state._studioAgent, state._studioProject, artifactId, true);
    showToast('Archiviert — bleibt in der Artefakt-Ansicht erhalten');
    refreshStudioOutputs();
  } catch (e) { showToast('Archivieren fehlgeschlagen: ' + (e.message || e), true); }
}

async function studioDeleteChatArtifact(artifactId) {
  if (!confirm('Dieses Artefakt endgültig löschen? Die Datei wird entfernt.')) return;
  try {
    await API.deleteProjectArtifact(state._studioAgent, state._studioProject, artifactId);
    showToast('Gelöscht');
    refreshStudioOutputs();
  } catch (e) { showToast('Löschen fehlgeschlagen: ' + (e.message || e), true); }
}

function studioOutputCardHtml(o) {
  const when = o.created_at ? formatTimeAgo(new Date(o.created_at * 1000)) : '';
  let statusLine, actions;
  if (o.status === 'generating') {
    const phaseLabel = o.phase === 'gathering' ? 'Quellen sammeln'
                     : o.phase === 'writing' ? 'Bericht schreiben'
                     : (o.phase || '').indexOf('writing ') === 0
                       ? `Quelle ${o.phase.slice(8)} verarbeiten`
                     : 'Wird vorbereitet';
    // Live elapsed clock (data-since drives the ticker; see studioTickElapsed).
    statusLine = `<span style="color:var(--text-400)">⟳ ${esc(phaseLabel)}… <span class="studio-elapsed" data-since="${o.created_at || ''}">${_studioElapsedStr(o.created_at)}</span></span>`;
    actions = `<button class="studio-act" onclick="studioCancelOutput('${esc(o.output_id)}')"
                style="background:none;border:1px solid var(--border-200);color:var(--error);cursor:pointer;font-size:12px;padding:4px 12px;border-radius:6px">Stoppen</button>`;
  } else if (o.status === 'cancelled') {
    statusLine = `<span style="color:var(--text-400)">⊘ Abgebrochen</span>`;
    actions = `<button class="studio-act" onclick="studioDeleteOutput('${esc(o.output_id)}')"
                style="background:none;border:1px solid var(--border-200);color:var(--error);cursor:pointer;font-size:12px;padding:4px 10px;border-radius:6px;display:inline-flex;align-items:center;gap:5px">${studioIcon('trash')} Löschen</button>`;
  } else if (o.status === 'error') {
    statusLine = `<span style="color:var(--error)" title="${esc(o.error || '')}">⚠ Fehler</span>`;
    actions = `<button class="studio-act" onclick="studioDeleteOutput('${esc(o.output_id)}')"
                style="background:none;border:1px solid var(--border-200);color:var(--error);cursor:pointer;font-size:12px;padding:4px 10px;border-radius:6px;display:inline-flex;align-items:center;gap:5px">${studioIcon('trash')} Löschen</button>`;
  } else {
    const oid = esc(o.output_id);
    const cites = (o.citations || 0) + ' Zitate';
    statusLine = `<span style="color:var(--text-400)">${cites} · ${esc(when)}</span>`;
    // Visible actions matching the chat-artifact cards (Öffnen/Archivieren/
    // Löschen) + a ⋯ for the secondary actions (Umbenennen/Neu generieren).
    actions = `
      <button class="studio-act" onclick="studioOpenOutput('${oid}')"
              style="background:var(--accent-brand);border:none;color:#fff;cursor:pointer;font-size:12px;padding:4px 12px;border-radius:6px">Öffnen</button>
      <button class="studio-act" onclick="studioArchiveOutput('${oid}')"
              style="background:var(--bg-100);border:1px solid var(--border-200);color:var(--text-200);cursor:pointer;font-size:12px;padding:4px 12px;border-radius:6px">Archivieren</button>
      <button class="studio-act" onclick="studioDeleteOutput('${oid}')"
              style="background:none;border:1px solid var(--border-200);color:var(--error);cursor:pointer;font-size:12px;padding:4px 10px;border-radius:6px;display:inline-flex;align-items:center" title="Löschen">${studioIcon('trash')}</button>
      <button class="studio-act" onclick="studioOutputMenu(event, '${oid}')"
              style="background:none;border:1px solid var(--border-200);color:var(--text-400);cursor:pointer;font-size:12px;padding:4px 10px;border-radius:6px" title="Weitere Optionen (Umbenennen, Neu generieren)">⋯</button>`;
  }
  // Metadata line (ready outputs only): model · cost. Full detail (date,
  // duration, tokens) lives in the report footer.
  let metaLine = '';
  if (o.status === 'ready' && (o.model || o.cost)) {
    const costStr = o.cost ? `$${Number(o.cost).toFixed(4)}` : '';
    const parts = [o.model && esc(o.model), costStr].filter(Boolean).join(' · ');
    if (parts) metaLine = `<div style="font-size:10px;color:var(--text-400)" title="Modell · Kosten dieser Generierung">${parts}</div>`;
  }
  return `
    <div class="studio-card" data-oid="${esc(o.output_id)}" style="flex:1 1 220px;min-width:200px;max-width:320px;border:1px solid var(--border-200);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px">
      <div style="font-weight:600;font-size:13px;line-height:1.3;display:flex;align-items:center;gap:6px"><span style="color:var(--text-300)">${studioIcon(o.kind)}</span>${esc(o.title || o.kind)}</div>
      <div style="font-size:11px">${statusLine}</div>
      ${metaLine}
      <div style="display:flex;gap:4px;margin-top:2px">${actions}</div>
    </div>`;
}

// ─── Open (.md viewer modal) ───────────────────────────────────────────────

async function studioOpenOutput(outputId) {
  const o = (state._studioOutputs || []).find(x => x.output_id === outputId);
  if (!o) return;
  if (!o.artifact_id) { showToast('Keine Datei für diese Ausgabe gefunden', true); return; }
  const isAudio = o.kind === 'audio_overview';
  // The styled HTML twin is the primary view + download when present; the .md
  // artifact is the fallback (older reports, or a render that was skipped).
  const hasHtml = !!o.html_artifact_id && !isAudio;
  const dlId = hasHtml ? o.html_artifact_id : o.artifact_id;
  const icon = `<span style="color:var(--text-300)">${studioIcon(o.kind)}</span>`;
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  // HTML reports render full-bleed in an iframe (they carry their own styling),
  // so give the modal more room and drop the padding for that case.
  const wide = hasHtml ? 'max-width:1040px' : 'max-width:900px';
  const bodyPad = hasHtml ? 'padding:0' : 'padding:14px 18px';
  overlay.innerHTML = `<div class="modal-content" style="${wide};width:92vw;max-height:90vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">${icon} ${esc(o.title || o.kind)}</span>
      <a class="btn-secondary" style="margin-left:auto;padding:3px 10px;font-size:12px;text-decoration:none" href="${esc(API.getArtifactDownloadUrl(dlId))}" target="_blank" rel="noopener">Herunterladen</a>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="studio-view-body" style="flex:1;overflow:auto;${bodyPad}"><div class="msg-content">Lädt…</div></div>
  </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const body = overlay.querySelector('.studio-view-body');
  // Audio overview: the artifact IS the .mp3 — play it inline (the dialogue
  // script is a separate .md artifact, surfaced under chat/Artifacts).
  if (isAudio) {
    body.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:14px;align-items:center;padding:24px 8px">
        <div style="color:var(--text-300)">${studioIcon('audio_overview', '48px')}</div>
        <div style="font-size:13px;color:var(--text-400);text-align:center">Zwei-Host-Podcast (englisch) aus den Projektquellen.</div>
        <div class="studio-audio-mount" style="width:100%;display:flex;justify-content:center">Lädt…</div>
      </div>`;
    // Auth'd blob fetch — a bare download URL in <audio src> 401s (Bearer-only).
    (async () => {
      const mount = body.querySelector('.studio-audio-mount');
      try {
        const resp = await fetch(API.getArtifactDownloadUrl(o.artifact_id), { headers: API._headers() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const url = URL.createObjectURL(await resp.blob());
        mount.innerHTML = `<audio controls preload="metadata" style="width:100%;max-width:520px" src="${url}"></audio>`;
      } catch (e) {
        mount.innerHTML = `<div style="color:var(--error)">Audio konnte nicht geladen werden: ${esc(e.message || e)}</div>`;
      }
    })();
    return;
  }
  try {
    if (hasHtml) {
      // Fetch the styled HTML (authenticated JSON) and render it into a
      // sandboxed iframe via srcdoc — self-contained, so no auth/CORS issues
      // and the report's own CSS/JS stays isolated from the app shell.
      const data = await API.getArtifactContent(o.html_artifact_id);
      const html = (data && data.content) || '';
      const iframe = document.createElement('iframe');
      iframe.style.cssText = 'width:100%;height:100%;border:0;display:block;min-height:70vh';
      iframe.setAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox allow-scripts');
      iframe.srcdoc = html;
      body.innerHTML = '';
      body.appendChild(iframe);
      return;
    }
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
    item('Herunterladen', `window.open(API.getArtifactDownloadUrl('${esc(o.html_artifact_id || o.artifact_id)}'), '_blank')`) +
    item('Löschen', `studioDeleteOutput('${esc(outputId)}')`, true);
  document.body.appendChild(menu);
  const r = event.target.getBoundingClientRect();
  menu.style.left = Math.min(r.left, window.innerWidth - 170) + 'px';
  // Flip ABOVE the button when the menu would overflow the bottom of the
  // viewport (cards near the bottom previously clipped the menu off-screen);
  // clamp to a small margin so it's always fully visible either way.
  const mh = menu.offsetHeight || 160;
  const below = r.bottom + 4;
  const top = (below + mh > window.innerHeight - 8)
    ? Math.max(8, r.top - mh - 4)
    : below;
  menu.style.top = top + 'px';
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

async function studioArchiveOutput(outputId) {
  try {
    await API.archiveProjectOutput(state._studioAgent, state._studioProject, outputId, true);
    showToast('Archiviert');
    refreshStudioOutputs();
  } catch (e) { showToast('Archivieren fehlgeschlagen: ' + (e.message || e), true); }
}

// ─── Poll (live generating→ready, mirrors panels_background.js) ────────────

function startStudioPoll() {
  if (!_studioElapsedHandle) _studioElapsedHandle = setInterval(studioTickElapsed, 1000);
  if (_studioPollHandle) return;
  _studioPollHandle = setInterval(() => {
    // Only poll while still on the Studio tab of this project.
    if (state.currentView !== 'project-detail' || state._projectChatsFilter !== 'studio') { stopStudioPoll(); return; }
    refreshStudioOutputs();
  }, 2500);
}

function stopStudioPoll() {
  if (_studioPollHandle) { clearInterval(_studioPollHandle); _studioPollHandle = null; }
  if (_studioElapsedHandle) { clearInterval(_studioElapsedHandle); _studioElapsedHandle = null; }
}

let _studioElapsedHandle = null;
// m:ss elapsed from a `created_at` epoch. Used BOTH at render time (so a fresh
// card shows the real elapsed immediately) and by the 1s ticker (so it keeps
// counting). Rendering the live value — not a hardcoded "0:00" — is what stops
// the clock from flashing back to 0:00 on every 2.5s poll re-render.
function _studioElapsedStr(since) {
  since = parseFloat(since);
  if (!since) return '0:00';
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}
// Tick every generating card's elapsed clock from its data-since epoch, so the
// long single LLM "writing" phase never looks frozen (no per-token progress).
function studioTickElapsed() {
  document.querySelectorAll('.studio-elapsed[data-since]').forEach(el => {
    const since = parseFloat(el.dataset.since);
    if (!since) return;
    el.textContent = _studioElapsedStr(since);
  });
}

async function studioCancelOutput(outputId) {
  try {
    await API.cancelProjectOutput(state._studioAgent, state._studioProject, outputId);
    showToast('Wird gestoppt… (eine laufende KI-Anfrage läuft noch zu Ende)');
    refreshStudioOutputs();
  } catch (e) { showToast('Stoppen fehlgeschlagen: ' + (e.message || e), true); }
}
