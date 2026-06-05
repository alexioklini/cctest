// panels_research.js — the "Research" tab on the project detail page.
// Two modes over one import seam (RESEARCH_IMPORT_DETAILED_SPEC):
//  - Fast: topic → search → SERP pick → append selected URLs to project web_urls
//    (the existing update_project path; the sync daemon mines them).
//  - Deep: topic → bounded background loop (engine/deep_research.py) → live
//    progress + budget → propose-approve sources + a cited report saved to Studio.
// Sources are PROPOSED, never auto-imported. Globals only; loaded after
// panels_projects.js, before init.js.

let _researchPollHandle = null;
let _researchBackend = '';   // THE active search backend (one tool), '' if none

function loadProjectResearch(agentId, projectName) {
  const el = document.getElementById('project-detail-research');
  if (!el) return;
  state._researchAgent = agentId;
  state._researchProject = projectName;
  el.innerHTML = '<div style="padding:18px 8px;color:var(--text-400);font-size:13px;text-align:center">Lädt…</div>';
  // E1 — gate on the active search backend (the one enabled search tool).
  API.researchBackends(agentId, projectName).then(d => {
    _researchBackend = d.backend || '';
    renderResearchForm();
    loadResearchHistory();   // restore latest + list recent (persisted in DB)
  }).catch(e => {
    el.innerHTML = `<div style="padding:14px;color:var(--error);font-size:13px">${esc(e.message || e)}</div>`;
  });
}

// Recent runs are persisted in research_runs — fetch them so proposed sources +
// reports survive a reload / tab switch (previously only state._researchProposed
// in JS held them). Renders a 'Letzte Recherchen' list and auto-restores the
// most recent run's results into the result area.
async function loadResearchHistory() {
  let data;
  try {
    data = await API.researchRuns(state._researchAgent, state._researchProject);
  } catch (_) { return; }
  const runs = (data && data.runs) || [];
  state._researchRuns = runs;
  renderResearchHistory(runs);
  // Auto-restore the most recent run: re-attach if still running, else show
  // its results. Skipped if a run is already live in this view.
  if (!state._researchRunId && runs.length) {
    const latest = runs[0];
    if (latest.status === 'running') { researchReopen(latest.run_id, true); }
    else { researchReopen(latest.run_id, false); }
  }
}

function renderResearchHistory(runs) {
  const host = document.getElementById('research-history');
  if (!host) return;
  if (!runs.length) { host.innerHTML = ''; return; }
  const _ico = { done: '✓', error: '✕', cancelled: '⊘', running: '⟳' };
  const rows = runs.map(r => {
    const when = r.created_at ? new Date(r.created_at * 1000).toLocaleString('de-DE',
      { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
    const ico = _ico[r.status] || '•';
    return `<div class="research-hist-row" onclick="researchReopen('${esc(r.run_id)}', ${r.status === 'running'})"
                 style="display:flex;gap:8px;align-items:center;padding:5px 6px;border-radius:6px;cursor:pointer;font-size:12px"
                 onmouseover="this.style.background='var(--bg-100)'" onmouseout="this.style.background=''">
      <span style="color:var(--text-400)">${ico}</span>
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.topic || '(ohne Thema)')}</span>
      <span style="color:var(--text-400);font-size:11px;flex-shrink:0">${esc(when)}</span>
    </div>`;
  }).join('');
  host.innerHTML = `
    <div style="margin-top:22px;max-width:680px">
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">Letzte Recherchen</div>
      <div style="border:1px solid var(--border-200);border-radius:8px;padding:4px">${rows}</div>
    </div>`;
}

// Re-open a persisted run: if running, re-attach the live progress + poll;
// otherwise fetch its final state and render the proposed sources + report.
async function researchReopen(runId, isRunning) {
  state._researchRunId = runId;
  state._researchCancelling = false;
  if (isRunning) {
    try {
      const r = await API.researchRun(state._researchAgent, state._researchProject, runId);
      if (r.created_at) state._researchStartedAt = r.created_at;
      state._researchLast = r;
      renderDeepProgress(r);
      startResearchPoll(); startResearchTimer();
    } catch (e) { showToast('Lauf konnte nicht geladen werden: ' + (e.message || e), true); }
    return;
  }
  try {
    const r = await API.researchRun(state._researchAgent, state._researchProject, runId);
    renderDeepDone(r);
  } catch (e) { showToast('Ergebnis konnte nicht geladen werden: ' + (e.message || e), true); }
}

function renderResearchForm() {
  const el = document.getElementById('project-detail-research');
  if (!el) return;
  if (!_researchBackend) {
    el.innerHTML = `
      <div style="padding:14px 16px;border:1px solid var(--border-200);border-radius:10px;color:var(--text-300);font-size:13px">
        🔍 Research ist nicht verfügbar — kein Such-Tool aktiviert.
        <div style="margin-top:6px;color:var(--text-400)">Aktiviere SearXNG oder Exa in Einstellungen → Tools.</div>
      </div>`;
    return;
  }
  const engineName = _researchBackend === 'exa' ? 'Exa' : 'SearXNG';
  el.innerHTML = `
    <div style="max-width:680px">
      <div style="font-weight:600;font-size:14px;margin-bottom:4px">Neue Quellen für dieses Projekt finden</div>
      <textarea id="research-topic" rows="3" placeholder="Thema oder Frage… z. B. „GPAI-Transparenzpflichten unter dem EU AI Act&quot;"
        style="width:100%;padding:8px 10px;font-size:13px;border:1px solid var(--border-200);border-radius:8px;background:var(--bg-000);color:var(--text-100);resize:vertical"></textarea>
      <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:18px;align-items:center;font-size:13px;color:var(--text-300)">
        <div style="display:flex;gap:14px;align-items:center">
          <span>Modus:</span>
          <label style="display:flex;align-items:center;gap:5px"><input type="radio" name="research-mode" value="fast" checked> Fast <span style="color:var(--text-400);font-size:11px">(schnelle Suche)</span></label>
          <label style="display:flex;align-items:center;gap:5px"><input type="radio" name="research-mode" value="deep"> Deep <span style="color:var(--text-400);font-size:11px">(plant · liest · schreibt Bericht)</span></label>
        </div>
        <span style="color:var(--text-400);font-size:12px">Suche über ${esc(engineName)}</span>
      </div>
      <div style="margin-top:12px"><button class="btn-primary" style="padding:6px 16px;font-size:13px" onclick="researchStart()">Recherche starten →</button></div>
      <div id="research-result" style="margin-top:16px"></div>
      <div id="research-history"></div>
    </div>`;
}

function researchStart() {
  const topic = (document.getElementById('research-topic')?.value || '').trim();
  if (!topic) { showToast('Bitte ein Thema eingeben', true); return; }
  const mode = document.querySelector('input[name="research-mode"]:checked')?.value || 'fast';
  if (mode === 'fast') researchRunFast(topic);
  else researchRunDeep(topic);
}

// ─── Fast Research (search → SERP pick → import) ───────────────────────────

async function researchRunFast(topic) {
  const out = document.getElementById('research-result');
  if (out) out.innerHTML = '<div style="padding:14px;color:var(--text-400);font-size:13px">Sucht…</div>';
  let data;
  try {
    data = await API.researchSearch(state._researchAgent, state._researchProject, topic);
  } catch (e) {
    if (out) out.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(e.message || e)}</div>`;
    return;
  }
  state._researchResults = data.results || [];
  renderFastResults(topic, data);
}

function renderFastResults(topic, data) {
  const out = document.getElementById('research-result');
  if (!out) return;
  const results = data.results || [];
  if (!results.length) {
    out.innerHTML = `<div style="padding:14px;color:var(--text-400);font-size:13px">Keine Ergebnisse. Thema anders formulieren oder Deep-Modus versuchen.</div>`;
    return;
  }
  const cap = data.total_found > results.length ? `<span style="color:var(--text-400)"> · zeige ${results.length} von ${data.total_found}</span>` : '';
  const rows = results.map((r, i) => {
    const disabled = r.in_project;
    const badge = disabled ? '<span style="color:var(--text-400);font-size:11px"> · ⛔ bereits im Projekt</span>'
                : (r.trust_hint ? `<span style="color:var(--warn,#b8860b);font-size:11px"> · ⚠ ${esc(r.trust_hint)}</span>` : '');
    return `
      <label style="display:flex;gap:8px;padding:7px 4px;border-bottom:1px solid var(--border-100);align-items:flex-start;${disabled ? 'opacity:0.55' : ''}">
        <input type="checkbox" class="research-pick" data-idx="${i}" ${disabled ? 'disabled' : ''} style="margin-top:3px;accent-color:var(--accent-brand)">
        <span style="flex:1;min-width:0">
          <span style="font-size:13px;color:var(--text-100)">${esc(r.title || r.url)}${badge}</span>
          <span style="display:block;font-size:11px;color:var(--text-400);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.url)}</span>
        </span>
      </label>`;
  }).join('');
  out.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <span style="font-weight:600;font-size:13px">${results.length} Ergebnisse${cap}</span>
      <button class="btn-secondary" style="padding:2px 10px;font-size:11px" onclick="researchPickAll(true)">Alle wählen</button>
      <button class="btn-secondary" style="padding:2px 10px;font-size:11px" onclick="researchPickAll(false)">Leeren</button>
    </div>
    <div style="max-height:360px;overflow:auto;border:1px solid var(--border-200);border-radius:8px;padding:4px 8px">${rows}</div>
    <div style="margin-top:12px"><button class="btn-primary" style="padding:6px 16px;font-size:13px" onclick="researchImportSelected()">Ausgewählte Quellen ins Projekt importieren →</button></div>`;
}

function researchPickAll(on) {
  document.querySelectorAll('.research-pick:not(:disabled)').forEach(c => { c.checked = on; });
}

async function researchImportSelected() {
  const picks = Array.from(document.querySelectorAll('.research-pick:checked')).map(c => parseInt(c.dataset.idx, 10));
  if (!picks.length) { showToast('Nichts ausgewählt', true); return; }
  const results = state._researchResults || [];
  const toAdd = picks.map(i => results[i]).filter(Boolean).map(r => ({ url: r.url, title: r.title || '' }));
  // Append to the project's existing web_urls (the server normalises + dedups).
  const existing = (state._projectDetail && state._projectDetail.web_urls) || [];
  const merged = existing.concat(toAdd);
  try {
    await API.updateProject(state._researchAgent, state._researchProject, { web_urls: merged });
    if (state._projectDetail) state._projectDetail.web_urls = merged;
    if (typeof renderProjectWebUrls === 'function') renderProjectWebUrls(merged);  // refresh right panel without reload
    showToast(`${toAdd.length} Quellen importiert — werden jetzt ins Gedächtnis gemined`);
    const out = document.getElementById('research-result');
    if (out) out.innerHTML = `
      <div style="padding:14px 16px;border:1px solid var(--border-200);border-radius:10px;font-size:13px">
        ✅ ${toAdd.length} Quellen zum Projekt hinzugefügt. Sie werden beim nächsten Sync ins Projektgedächtnis gemined und sind dann im Chat durchsuchbar.
        <div style="margin-top:8px"><button class="btn-secondary" style="padding:3px 10px;font-size:12px" onclick="setProjectChatsFilter('active')">Zu den Quellen</button></div>
      </div>`;
  } catch (e) {
    showToast('Import fehlgeschlagen: ' + (e.message || e), true);
  }
}

// ─── Deep Research (bounded loop → progress → propose-approve) ─────────────

async function researchRunDeep(topic) {
  const out = document.getElementById('research-result');
  if (out) out.innerHTML = '<div style="padding:14px;color:var(--text-400);font-size:13px">Startet…</div>';
  let resp;
  try {
    resp = await API.researchDeep(state._researchAgent, state._researchProject, topic, null);
  } catch (e) {
    if (out) out.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(e.message || e)}</div>`;
    return;
  }
  state._researchRunId = resp.run_id;
  state._researchCancelling = false;
  state._researchStartedAt = Date.now() / 1000;   // fallback; replaced by created_at from poll
  state._researchLast = { status: 'running', phase: 'planning', progress: {}, budget: resp.budget, topic };
  renderDeepProgress(state._researchLast);
  startResearchPoll();
  startResearchTimer();
}

function startResearchPoll() {
  if (_researchPollHandle) return;
  _researchPollHandle = setInterval(async () => {
    if (state.currentView !== 'project-detail' || state._projectChatsFilter !== 'research' || !state._researchRunId) {
      stopResearchPoll(); stopResearchTimer(); return;
    }
    try {
      const r = await API.researchRun(state._researchAgent, state._researchProject, state._researchRunId);
      if (r.created_at) state._researchStartedAt = r.created_at;
      if (r.status === 'running') { state._researchLast = r; renderDeepProgress(r); }
      else { stopResearchPoll(); stopResearchTimer(); renderDeepDone(r); loadResearchHistory(); }
    } catch (_) { /* transient — keep polling */ }
  }, 2500);
}

function stopResearchPoll() {
  if (_researchPollHandle) { clearInterval(_researchPollHandle); _researchPollHandle = null; }
}

// Independent 1s ticker so the elapsed time + the working heartbeat move every
// second even though the run is polled only every 2.5s — the long "writing"
// phase is a single LLM call that emits no intermediate progress, so without
// this the panel looks frozen.
let _researchTimerHandle = null;
function startResearchTimer() {
  if (_researchTimerHandle) return;
  _researchTimerHandle = setInterval(updateResearchElapsed, 1000);
}
function stopResearchTimer() {
  if (_researchTimerHandle) { clearInterval(_researchTimerHandle); _researchTimerHandle = null; }
}
function _fmtElapsed(sec) {
  sec = Math.max(0, Math.floor(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
function updateResearchElapsed() {
  const el = document.getElementById('research-elapsed');
  if (!el || !state._researchStartedAt) return;
  el.textContent = _fmtElapsed(Date.now() / 1000 - state._researchStartedAt);
}

const _RESEARCH_PHASES = [
  ['planning', 'Sub-Fragen planen'], ['searching', 'Suchen'],
  ['reading', 'Quellen lesen & bewerten'], ['writing', 'Bericht schreiben'], ['done', 'Fertig'],
];

function renderDeepProgress(r) {
  const out = document.getElementById('research-result');
  if (!out) return;
  const p = r.progress || {}, b = r.budget || {};
  const curIdx = _RESEARCH_PHASES.findIndex(x => x[0] === r.phase);
  const steps = _RESEARCH_PHASES.filter(x => x[0] !== 'done').map(([key, label], i) => {
    let mark = '◦', detail = '';
    if (i < curIdx) mark = '✓'; else if (i === curIdx) mark = '⟳';
    if (key === 'planning' && p.subqueries) detail = ` (${p.subqueries} Sub-Fragen)`;
    if (key === 'searching' && p.candidates != null) detail = ` (${p.candidates} Kandidaten)`;
    if (key === 'reading' && p.fetched != null) detail = ` (${p.fetched} gelesen, ${p.kept || 0} behalten)`;
    // The writing phase is one long LLM call with no sub-progress — show the
    // source count + a live ⟳ so it's clearly working, not frozen.
    if (key === 'writing' && i === curIdx) detail = ` (Synthese aus ${p.kept || 0} Quellen…)`;
    return `<div style="font-size:13px;color:${i === curIdx ? 'var(--text-100)' : 'var(--text-400)'}">${mark} ${esc(label)}${esc(detail)}</div>`;
  }).join('');
  const fetched = p.fetched || 0, fcap = b.fetches || 60;
  const cancelling = !!state._researchCancelling;
  out.innerHTML = `
    <div style="border:1px solid var(--border-200);border-radius:10px;padding:14px 16px;max-width:560px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
        <span style="font-weight:600;font-size:13px">🔬 Deep Research läuft…</span>
        <span style="font-size:11px;color:var(--text-400)">⏱ <span id="research-elapsed">0:00</span></span>
        <button class="btn-secondary" style="margin-left:auto;padding:3px 10px;font-size:11px"
                onclick="researchCancel()" ${cancelling ? 'disabled' : ''}>
          ${cancelling ? 'Wird abgebrochen…' : 'Abbrechen'}</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:5px">${steps}</div>
      <div style="margin-top:10px;font-size:11px;color:var(--text-400)">Budget: ${fetched} / ${fcap} Fetches</div>
      <div style="margin-top:8px;font-size:11px;color:var(--text-400)">${cancelling
        ? 'Abbruch wird beim nächsten Schritt wirksam…'
        : 'Du kannst diesen Tab verlassen — der Lauf läuft weiter.'}</div>
    </div>`;
  updateResearchElapsed();  // paint immediately so it isn't 0:00 for a tick
}

async function researchCancel() {
  if (!state._researchRunId) return;
  // Immediate feedback: flip the button to a disabled 'Wird abgebrochen…' and
  // explain it lands at the next checkpoint (the worker checks the cancel flag
  // between/within phases; an in-flight synthesis LLM call still completes).
  state._researchCancelling = true;
  if (state._researchLast) renderDeepProgress(state._researchLast);
  try { await API.researchCancel(state._researchAgent, state._researchProject, state._researchRunId); }
  catch (e) {
    state._researchCancelling = false;
    if (state._researchLast) renderDeepProgress(state._researchLast);
    showToast('Abbrechen fehlgeschlagen: ' + (e.message || e), true);
  }
}

function renderDeepDone(r) {
  state._researchCancelling = false;
  stopResearchTimer();
  const out = document.getElementById('research-result');
  if (!out) return;
  if (r.status === 'error') {
    out.innerHTML = `<div style="padding:14px;color:var(--error);font-size:13px">Fehlgeschlagen: ${esc(r.error || 'unbekannter Fehler')}</div>`;
    return;
  }
  if (r.status === 'cancelled') {
    out.innerHTML = `<div style="padding:14px;color:var(--text-400);font-size:13px">Abgebrochen.</div>`;
    return;
  }
  const proposed = r.proposed || [];
  state._researchProposed = proposed;
  state._researchReportOid = r.report_output_id || '';
  // W9 — nothing usable.
  if (!r.report_output_id && !proposed.length) {
    out.innerHTML = `
      <div style="padding:14px 16px;border:1px solid var(--border-200);border-radius:10px;font-size:13px">
        Keine belastbaren Quellen gefunden. ${esc(r.coverage_note || '')}
        <div style="margin-top:6px;color:var(--text-400)">Versuche das Thema breiter zu fassen oder den Fast-Modus.</div>
      </div>`;
    return;
  }
  const reportRow = r.report_output_id ? `
    <div style="padding:10px 0;border-bottom:1px solid var(--border-100)">
      📄 <b>Bericht erstellt</b> — gespeichert im Studio
      <button class="btn-secondary" style="margin-left:8px;padding:2px 10px;font-size:12px" onclick="researchOpenReport()">Bericht öffnen</button>
    </div>` : '';
  const rows = proposed.map((s, i) => {
    const disabled = s.in_project;
    const badge = disabled ? '<span style="color:var(--text-400);font-size:11px"> · ⛔ bereits im Projekt</span>'
                : (s.trust_hint ? `<span style="color:var(--warn,#b8860b);font-size:11px"> · ⚠ ${esc(s.trust_hint)}</span>` : '');
    return `
      <label style="display:flex;gap:8px;padding:6px 4px;align-items:flex-start;${disabled ? 'opacity:0.55' : ''}">
        <input type="checkbox" class="research-pick" data-idx="${i}" ${disabled ? 'disabled' : 'checked'} style="margin-top:3px;accent-color:var(--accent-brand)">
        <span style="flex:1;min-width:0">
          <span style="font-size:13px">${esc(s.title || s.url)}${badge}</span>
          <span style="display:block;font-size:11px;color:var(--text-400);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.url)}</span>
        </span>
      </label>`;
  }).join('');
  out.innerHTML = `
    <div style="max-width:640px">
      ${reportRow}
      <div style="font-weight:600;font-size:13px;margin:10px 0 4px">Vorgeschlagene Quellen zum Import (${proposed.length}):</div>
      <div style="max-height:300px;overflow:auto;border:1px solid var(--border-200);border-radius:8px;padding:4px 8px">${rows}</div>
      <div style="margin-top:8px;font-size:11px;color:var(--text-400)">${esc(r.coverage_note || '')}</div>
      <div style="margin-top:12px;display:flex;gap:10px">
        <button class="btn-primary" style="padding:6px 16px;font-size:13px" onclick="researchImportProposed()">Ausgewählte importieren →</button>
        <button class="btn-secondary" style="padding:6px 16px;font-size:13px" onclick="renderResearchForm()">Nur Bericht behalten</button>
      </div>
    </div>`;
}

function researchOpenReport() {
  // The report is a project_outputs row (kind=research_report) — it lives in
  // Studio. Jump there; the Studio tab browses + opens it like any output.
  setProjectChatsFilter('studio');
}

async function researchImportProposed() {
  const picks = Array.from(document.querySelectorAll('.research-pick:checked')).map(c => parseInt(c.dataset.idx, 10));
  if (!picks.length) { showToast('Nichts ausgewählt', true); return; }
  const proposed = state._researchProposed || [];
  const toAdd = picks.map(i => proposed[i]).filter(Boolean).map(s => ({ url: s.url, title: s.title || '' }));
  const existing = (state._projectDetail && state._projectDetail.web_urls) || [];
  const merged = existing.concat(toAdd);
  try {
    await API.updateProject(state._researchAgent, state._researchProject, { web_urls: merged });
    if (state._projectDetail) state._projectDetail.web_urls = merged;
    if (typeof renderProjectWebUrls === 'function') renderProjectWebUrls(merged);  // refresh right panel without reload
    showToast(`${toAdd.length} Quellen importiert — werden jetzt gemined`);
    setProjectChatsFilter('active');
  } catch (e) {
    showToast('Import fehlgeschlagen: ' + (e.message || e), true);
  }
}
