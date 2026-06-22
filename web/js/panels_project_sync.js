// panels_project_sync.js — project input-folders, sync, sync-history. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

// ─── Project input folders + sync indicator ───────────────────────────
// Shim: input folders now render inside the unified source tree. Still stash
// state._projectInputFolders for the edit modal, then refresh the tree's
// Ordner branch in place.
async function loadProjectInputFolders(agentId, projectName) {
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders`);
    state._projectInputFolders = data.folders || data.input_folders || [];
  } catch (_) { /* keep stale stash on transient error */ }
  if (typeof _ptFillFolders === 'function' && document.getElementById('pt-items-folders')) {
    return _ptFillFolders(agentId, projectName);
  }
}

function addProjectInputFolder() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  // Custom modal: filesystem browser (reuses the same /v1/files/tree backend
  // as the schedule modal's picker, but renders standalone so the schedule
  // modal's `_schedFolderPickerSelect` close-topmost behavior doesn't apply).
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Ordner hinzufügen</h2>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">Wähle einen Ordner auf der Festplatte. Dessen Inhalte werden regelmäßig eingelesen und für dieses Projekt durchsuchbar gemacht.</div>
    <div id="pif-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pif-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-recursive" checked>
      Unterordner mit einbeziehen
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-auto-sync" checked>
      Automatisch abgleichen
      <span style="color:var(--text-400);font-size:12px">— abwählen, um nur manuell abzugleichen</span>
    </label>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="_pifPickerSelect()">Diesen Ordner hinzufügen</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  _pifLoadFolder('');  // empty path → server defaults to $HOME
}

async function _pifLoadFolder(path) {
  const crumbs = document.getElementById('pif-picker-crumbs');
  const list = document.getElementById('pif-picker-list');
  if (!crumbs || !list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-400);text-align:center">Wird geladen …</div>';
  try {
    const data = await API.get(`/v1/files/tree?path=${encodeURIComponent(path)}&depth=0`);
    if (data.error) { list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(data.error)}</div>`; return; }
    const cur = data.path || path || '/';
    window._pifPickerPath = cur;
    crumbs.textContent = cur;
    const dirs = (data.tree || []).filter(n => n.type === 'dir');
    const parent = (cur && cur !== '/') ? cur.replace(/\/[^\/]+\/?$/, '') || '/' : null;
    let html = '';
    if (parent !== null) {
      html += `<div onclick="_pifLoadFolder('${esc(parent)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-300)" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">↑ ..</div>`;
    }
    if (!dirs.length) {
      html += '<div style="padding:14px;color:var(--text-400);text-align:center;font-size:12px">(keine Unterordner)</div>';
    } else {
      for (const d of dirs) {
        html += `<div onclick="_pifLoadFolder('${esc(d.path)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-200);display:flex;align-items:center;gap:8px" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">
          <span style="color:var(--text-400)">📁</span>${esc(d.name)}
        </div>`;
      }
    }
    list.innerHTML = html;
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(e.message)}</div>`;
  }
}

async function _pifPickerSelect() {
  const path = window._pifPickerPath || '';
  if (!path) { showToast('Kein Ordner ausgewählt', true); return; }
  const recursive = document.getElementById('pif-picker-recursive')?.checked ?? true;
  const autoSync = document.getElementById('pif-picker-auto-sync')?.checked ?? true;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    const res = await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders`,
      { path, recursive, auto_sync: autoSync });
    if (res?.error) {
      showToast(res.error);
      return;
    }
    showToast('Ordner hinzugefügt — erster Scan läuft');
    document.querySelector('.sched-modal-overlay')?.remove();
    loadProjectInputFolders(agentId, projectName);
    // Trigger a sync now so the user sees activity immediately, even if
    // auto_sync is off — the user just opted in to a one-shot index.
    projectSyncNow();
  } catch(e) {
    showToast('Ordner konnte nicht hinzugefügt werden: ' + (e?.message || e));
  }
}

function removeProjectInputFolder(idx) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const folders = state._projectInputFolders || [];
  const folder = folders[idx];
  if (!folder) return;
  // Warning modal — replaces the legacy confirm() so the destructive action
  // is gated by a clearly red button instead of a system dialog the user
  // can dismiss with Enter.
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:520px">
    <h2 style="display:flex;align-items:center;gap:8px">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#d33" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      Eingabeordner entfernen?
    </h2>
    <div style="font-size:13px;color:var(--text-300);line-height:1.5;margin:8px 0 16px">
      <div style="font-family:var(--font-mono);font-size:12px;background:var(--bg-100);padding:8px 10px;border-radius:6px;border:1px solid var(--border-100);word-break:break-all;margin-bottom:10px">${esc(folder.path || '')}</div>
      Dieser Ordner wird nicht mehr gescannt. Bereits indexierte Inhalte bleiben im Speicher dieses Projekts, bis das Projekt geleert wird.
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" style="background:#d33;border-color:#d33" onclick="_pifConfirmDelete(${idx})">Ordner entfernen</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

async function _pifConfirmDelete(idx) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  document.querySelector('.sched-modal-overlay')?.remove();
  try {
    await API.del(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders/${idx}`);
    loadProjectInputFolders(agentId, projectName);
    showToast('Ordner entfernt');
  } catch(e) {
    showToast('Ordner konnte nicht entfernt werden');
  }
}

function editProjectInputFolder(idx) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const folder = (state._projectInputFolders || [])[idx];
  if (!folder) return;
  // Edit modal — same picker shell as add, but pre-loaded at the existing
  // path, with recursive + auto_sync prefilled. Save button is wired to
  // _pifEditSave (not _pifPickerSelect) which PATCHes via POST /input-folders/<idx>.
  window._pifEditingIdx = idx;
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  const recChecked = folder.recursive !== false ? 'checked' : '';
  const autoChecked = folder.auto_sync !== false ? 'checked' : '';
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Eingabeordner bearbeiten</h2>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">Pfad oder Scan-Verhalten dieses Ordners ändern.</div>
    <div id="pif-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pif-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-recursive" ${recChecked}>
      Rekursiv scannen (alle Unterordner einbeziehen)
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-auto-sync" ${autoChecked}>
      In automatische Abgleichzyklen einbeziehen
      <span style="color:var(--text-400);font-size:12px">— abwählen, um nur manuell abzugleichen</span>
    </label>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="_pifEditSave()">Änderungen speichern</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  // Open the picker at the parent of the current path so the user can either
  // keep the current path (just toggle flags + Save) or navigate elsewhere.
  const cur = folder.path || '';
  const parent = cur.replace(/\/[^\/]+\/?$/, '') || '/';
  _pifLoadFolder(parent);
  // Pre-set picker path to the current folder so a no-navigate Save keeps it.
  window._pifPickerPath = cur;
}

async function _pifEditSave() {
  const idx = window._pifEditingIdx;
  if (idx == null) return;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const folder = (state._projectInputFolders || [])[idx] || {};
  const path = window._pifPickerPath || folder.path || '';
  const recursive = document.getElementById('pif-picker-recursive')?.checked ?? true;
  const autoSync = document.getElementById('pif-picker-auto-sync')?.checked ?? true;
  const body = { recursive, auto_sync: autoSync };
  // Only send path when it actually changed — saves a realpath round-trip
  // server-side and skips the "folder already added" dedup against itself.
  if (path && path !== folder.path) body.path = path;
  try {
    const res = await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders/${idx}`, body);
    if (res?.error) { showToast(res.error); return; }
    showToast('Ordner aktualisiert');
    document.querySelector('.sched-modal-overlay')?.remove();
    window._pifEditingIdx = null;
    loadProjectInputFolders(agentId, projectName);
  } catch(e) {
    showToast('Ordner konnte nicht aktualisiert werden: ' + (e?.message || e));
  }
}

// Bridge from the "Knowledge graph" project header button to the existing
// kgOpenProject drilldown modal. Uses the project ids stashed on the
// project-detail state so it works regardless of whether the chip has
// finished its first refresh.
function projectOpenKnowledgeGraph() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) {
    showToast('Kein Projekt im Kontext', true);
    return;
  }
  if (typeof kgOpenProject !== 'function') {
    showToast('Knowledge-Graph-Ansicht nicht verfügbar', true);
    return;
  }
  kgOpenProject(agentId, projectName);
}

async function projectSyncNow() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-now`, {});
    showToast('Synchronisierung eingeplant');
    refreshProjectSyncStatus(agentId, projectName);
  } catch(e) {
    showToast('Synchronisierung konnte nicht ausgelöst werden');
  }
}

async function projectFullResync() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  if (!await showConfirmDanger(`Eine vollständige Neu-Synchronisierung löscht den gesamten Speicher, alle Knowledge-Graph-Tripel und den Abgleichstatus für „${projectName}“ und indexiert anschließend alles von Grund auf neu.\n\nFortfahren?`, 'Vollständige Neu-Synchronisierung', 'Neu synchronisieren')) return;
  const btn = document.getElementById('project-action-full-resync');
  if (btn) { btn.disabled = true; btn.textContent = 'Wird gelöscht …'; }
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/full-resync`, {});
    showToast('Vollständige Neu-Synchronisierung eingeplant — alles wird neu indexiert');
    refreshProjectSyncStatus(agentId, projectName);
  } catch(e) {
    showToast('Vollständige Neu-Synchronisierung fehlgeschlagen', true);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3"/></svg> Vollständige Neu-Synchronisierung'; }
  }
}

function startProjectSyncPoll(agentId, projectName) {
  stopProjectSyncPoll();
  refreshProjectSyncStatus(agentId, projectName);
  state._projectSyncPollHandle = setInterval(() => {
    refreshProjectSyncStatus(agentId, projectName);
  }, 5000);
}

function stopProjectSyncPoll() {
  if (state._projectSyncPollHandle) {
    clearInterval(state._projectSyncPollHandle);
    state._projectSyncPollHandle = null;
  }
}

// Render the per-phase checklist shown while a sync is in flight. The daemon
// reports a single `mining_phase` (the phase running NOW); phases ordered
// before it are done, the named one is running (with live count + ETA), the
// rest are pending. The three phases map to the cost breakdown: Indexierung =
// convert+mine+embed (A+B), KG-Extraktion = triple extraction (C),
// Closet-Rerank = the LLM closet pass (D). Embedding is not separately
// instrumented (it happens inside the miner's per-file step), so it's folded
// into Indexierung rather than shown as a phantom row.
const _SYNC_PHASES = [
  { key: 'indexing', label: 'Indexierung',     doneK: 'mining_done',  totK: 'mining_total',  startK: 'started_at',         unit: 'Dokumente' },
  { key: 'kg',       label: 'KG-Extraktion',   doneK: 'kg_done',      totK: 'kg_total',      startK: 'kg_started_at',      unit: 'Abschnitte' },
  { key: 'closet',   label: 'Closet-Rerank',   doneK: 'closet_done',  totK: 'closet_total',  startK: 'closet_started_at',  unit: 'Quellen' },
];

function _syncPhaseEta(startVal, done, tot) {
  if (!startVal || !(done > 0) || !(tot > 0) || done / tot < 0.05) return '';
  let startedMs = 0;
  if (typeof startVal === 'number') startedMs = startVal * 1000;
  else { const t = new Date(startVal).getTime(); if (!isNaN(t)) startedMs = t; }
  if (!startedMs) return '';
  const remainMs = Math.max(0, Date.now() - startedMs) * (tot - done) / done;
  if (remainMs <= 1000 || remainMs > 1000 * 60 * 60 * 8) return '';
  const s = Math.floor(remainMs / 1000);
  const str = s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h`;
  return ` · ETA ${str}`;
}

function renderSyncPhases(st, live) {
  const box = document.getElementById('project-sync-phases');
  if (!box) return;
  if (live !== 'syncing') { box.style.display = 'none'; box.innerHTML = ''; return; }
  const cur = st.mining_phase || '';
  const curIdx = _SYNC_PHASES.findIndex(p => p.key === cur);
  // While syncing but before the daemon names a phase (cycle warm-up), treat
  // the first phase as the active one so the list isn't all-pending.
  const activeIdx = curIdx >= 0 ? curIdx : 0;
  const rows = _SYNC_PHASES.map((p, i) => {
    let icon, cls, detail = '';
    if (i < activeIdx) { icon = '✓'; cls = 'done'; }
    else if (i > activeIdx) { icon = '⏳'; cls = 'pending'; }
    else {
      icon = '▶'; cls = 'running';
      const done = Number(st[p.doneK] || 0);
      const tot = Number(st[p.totK] || 0);
      if (tot > 0) detail = `${done}/${tot} ${p.unit}${_syncPhaseEta(st[p.startK], done, tot)}`;
      else if (done > 0) detail = `${done} ${p.unit}`;
    }
    return `<div class="sync-phase-row ${cls}">
      <span class="sync-phase-icon">${icon}</span>
      <span class="sync-phase-name">${esc(p.label)}</span>
      <span class="sync-phase-detail">${esc(detail)}</span>
    </div>`;
  }).join('');
  box.innerHTML = rows;
  box.style.display = '';
}

async function refreshProjectSyncStatus(agentId, projectName) {
  const chip = document.getElementById('project-sync-chip');
  const labelEl = document.getElementById('project-sync-label');
  if (!chip || !labelEl) return;
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-status`);
    if (window._brainProjectSyncDebug) console.log('[sync-status]', data);
    const st = data.status || {};
    // Cache for per-item pill rendering inside loadProjectFiles /
    // loadProjectInputFolders.
    state._projectSyncItems = st.items || {};
    const live = st.state || 'idle';
    chip.style.display = '';
    chip.dataset.state = live;
    // Pull the headline numbers once — used by both syncing and idle labels.
    // total_files = distinct source files (what users count); total_indexed
    // = drawer count (internal storage detail, kept in tooltip).
    const totalFiles = st.total_files != null ? st.total_files : null;
    const totalDrawers = st.total_indexed != null ? st.total_indexed : (st.last_files_filed || 0);
    const triples = st.total_triples;
    // Renamed for non-technical users: a (subject, predicate, object) triple
    // doesn't mean anything outside the KG world. "Relation" reads as "fact
    // we extracted" in plain English.
    const tripleStr = (triples != null && triples > 0) ? ` · ${triples} Beziehungen` : '';
    let label = 'Speicher: inaktiv';
    if (live === 'syncing') {
      // Live progress: P/T file count + ETA from elapsed-rate. cycle_total_files
      // is from a cheap pre-walk and may overshoot the miner's filtered file
      // count — that's deliberate (better than the bar getting stuck at 100%).
      // Prefer the fine-grained PHASE counters the daemon pushes after each
      // mine batch / KG chunk (mining_done/total during indexing, kg_done/total
      // during KG) so the bar advances steadily instead of sitting at the
      // start-of-cycle pre-walk number. Falls back to the cycle counters.
      let proc = Number(st.cycle_processed_files || 0);
      let tot = Number(st.cycle_total_files || 0);
      let phaseWord = '';
      let unit = 'Dateien';
      // Per-phase start time (epoch seconds OR ISO string) for the ETA.
      let phaseStart = st.started_at || st.last_run_started || '';
      if (st.mining_phase === 'kg' && Number(st.kg_total || 0) > 0) {
        proc = Number(st.kg_done || 0); tot = Number(st.kg_total || 0);
        phaseWord = 'KG-Extraktion'; unit = '';
        if (st.kg_started_at) phaseStart = st.kg_started_at;
      } else if (st.mining_phase === 'indexing' && Number(st.mining_total || 0) > 0) {
        proc = Number(st.mining_done || 0); tot = Number(st.mining_total || 0);
        phaseWord = 'Indexierung'; unit = 'Dokumente';
      }
      let progress = '';
      if (tot > 0) progress = ` ${proc}/${tot}`;
      // ETA: extrapolate from elapsed wall time and processed share. Only
      // show once we've made meaningful progress (>=3%) so an early 0/N
      // doesn't claim "ETA 12 days". phaseStart may be epoch-seconds (float)
      // or an ISO string — normalise to ms.
      let eta = '';
      let startedMs = 0;
      if (typeof phaseStart === 'number') startedMs = phaseStart * 1000;
      else if (phaseStart) { const t = new Date(phaseStart).getTime(); if (!isNaN(t)) startedMs = t; }
      if (startedMs && proc > 0 && tot > 0 && proc / tot >= 0.03) {
        const elapsedMs = Math.max(0, Date.now() - startedMs);
        const remainMs = elapsedMs * (tot - proc) / proc;
        if (remainMs > 1000 && remainMs < 1000 * 60 * 60 * 48) {
          const remainSec = Math.floor(remainMs / 1000);
          const etaStr = remainSec < 60 ? `${remainSec}s`
                       : remainSec < 3600 ? `${Math.floor(remainSec / 60)}m`
                       : `${Math.floor(remainSec / 3600)}h`;
          eta = ` · ETA ${etaStr}`;
        }
      }
      // Build label per phase: "Speicher: KG-Extraktion 235/6994 · ETA 4m" or
      // "Speicher: Indexierung 168/258 Dokumente · ETA 2m". When no phase
      // counter is live yet, fall back to the generic Dateien count.
      const headWord = phaseWord || 'synchronisiert';
      const unitStr = unit ? ` ${unit}` : '';
      label = `Speicher: ${headWord}${progress}${unitStr}${eta}`;
      chip.title = 'Synchronisierung läuft';
      const sublabelElSync = document.getElementById('project-sync-sublabel');
      if (sublabelElSync) sublabelElSync.textContent = '';
    } else if (live === 'error') {
      label = 'Speicher: Fehler';
      chip.title = st.last_error || 'Synchronisierung fehlgeschlagen — über „Jetzt abgleichen“ erneut versuchen';
      const sublabelElErr = document.getElementById('project-sync-sublabel');
      if (sublabelElErr) sublabelElErr.textContent = '';
    } else {
      // Idle: lead with files (the unit users care about), then triples,
      // then "next sync in Xh". Last-synced timestamp and type shown in sub-label.
      const filesStr = totalFiles != null
        ? `${totalFiles} Datei${totalFiles === 1 ? '' : 'en'}`
        : `${totalDrawers} indexiert`;
      const next = data.next_run_at ? ` · nächster Abgleich in ${humanIn(data.next_run_at)}` : '';
      label = `Speicher: ${filesStr}${tripleStr}${next}`;
      const last = st.last_run_finished || data.last_scan || '';
      const drawerHint = (totalFiles != null && totalDrawers)
        ? `${totalDrawers} Schublade${totalDrawers === 1 ? '' : 'n'} · ` : '';
      chip.title = `${drawerHint}${last ? 'Zuletzt abgeglichen vor ' + humanAgo(last) + ' — ' : ''}über die Schaltflächen rechts abgleichen oder den Knowledge Graph öffnen`;
      // Sub-label: "synced Xh ago · Scheduled" or "synced Xh ago · Full Resync"
      const sublabelEl = document.getElementById('project-sync-sublabel');
      if (sublabelEl) {
        if (last) {
          const typeLabel = st.last_triggered_by === 'full_resync' ? 'Vollständige Neu-Synchronisierung'
                          : st.last_triggered_by === 'manual' ? 'Manuell' : 'Geplant';
          sublabelEl.textContent = `abgeglichen vor ${humanAgo(last)} · ${typeLabel}`;
        } else {
          sublabelEl.textContent = '';
        }
      }
    }
    // KG is currently extracting on any item? pulse purple.
    let kgWorking = false;
    let kgError = false;
    for (const k of Object.keys(state._projectSyncItems || {})) {
      const it = state._projectSyncItems[k] || {};
      if (it.kg_state === 'extracting') { kgWorking = true; break; }
      if (it.kg_state === 'error') kgError = true;
    }
    chip.dataset.kgState = kgWorking ? 'extracting' : (kgError ? 'error' : '');
    if (kgWorking) {
      // Find the item currently extracting to get live progress.
      let kgDone = 0, kgTotal = 0, kgStarted = 0, kgTriples = 0;
      for (const k of Object.keys(state._projectSyncItems || {})) {
        const it = state._projectSyncItems[k] || {};
        if (it.kg_state === 'extracting') {
          kgDone   = Number(it.kg_chunks_done  || 0);
          kgTotal  = Number(it.kg_chunks_total || 0);
          kgStarted = it.kg_started_at ? Number(it.kg_started_at) * 1000 : 0;
          kgTriples = Number(it.kg_triples_live || it.triples_extracted || 0);
          break;
        }
      }
      let kgProgress = kgTotal > 0 ? ` ${kgDone}/${kgTotal} Abschnitte` : (kgDone > 0 ? ` ${kgDone} Abschnitte` : '');
      let kgEta = '';
      if (kgStarted && kgDone > 0 && kgTotal > 0 && kgDone / kgTotal >= 0.05) {
        const elapsedMs = Math.max(0, Date.now() - kgStarted);
        const remainMs = elapsedMs * (kgTotal - kgDone) / kgDone;
        if (remainMs > 1000 && remainMs < 1000 * 60 * 60 * 4) {
          const remainSec = Math.floor(remainMs / 1000);
          const etaStr = remainSec < 60 ? `${remainSec}s` : `${Math.floor(remainSec / 60)}m`;
          kgEta = ` · ETA ${etaStr}`;
        }
      }
      const kgTriplesStr = kgTriples > 0 ? ` · ${kgTriples} bisher` : '';
      label = `Speicher: KG-Extraktion${kgProgress}${kgEta}${kgTriplesStr}`;
      chip.title = 'Knowledge-Graph-Extraktion läuft';
      const sublabelElKg = document.getElementById('project-sync-sublabel');
      if (sublabelElKg) sublabelElKg.textContent = '';
    }
    labelEl.textContent = label;
    // Per-phase checklist (Indexierung / KG-Extraktion / Closet-Rerank) —
    // only while syncing; hidden otherwise.
    renderSyncPhases(st, live);
    // Knowledge-graph button: admin-only. The drilldown is a debug /
    // operations surface (predicate distribution, sample triples,
    // extraction-log, admin re-extract) — useful for verifying extraction
    // quality and auditing the corpus, not for end users. Hidden entirely
    // for non-admins. When admin: enabled if there are any relations to
    // show, greyed otherwise.
    const kgBtn = document.getElementById('project-action-kg');
    if (kgBtn) {
      const isAdmin = state.authUser && state.authUser.role === 'admin';
      if (!isAdmin) {
        kgBtn.style.display = 'none';
      } else {
        kgBtn.style.display = '';
        const hasRelations = (st.total_triples || 0) > 0;
        kgBtn.disabled = !hasRelations;
        // Distinguish "never synced" from "synced but this content has no
        // extractable relations" — the latter is normal (e.g. news articles
        // under the policy-oriented profile yield 0 triples), NOT a problem to
        // fix by re-syncing, so don't tell the user to sync again.
        const everSynced = !!(st.last_run_finished || st.total_files);
        kgBtn.title = hasRelations
          ? `Knowledge-Graph-Detailansicht öffnen (${st.total_triples} Beziehungen)`
          : (everSynced
              ? 'Aus den Quellen dieses Projekts wurden keine Beziehungen extrahiert (z. B. bei Nachrichten-/Fließtext-Inhalten normal).'
              : 'Noch keine Beziehungen extrahiert — dieses Projekt zuerst abgleichen.');
      }
    }
    const syncBtn = document.getElementById('project-action-sync');
    if (syncBtn) {
      syncBtn.disabled = (live === 'syncing');
      syncBtn.title = live === 'syncing'
        ? 'Synchronisierung läuft bereits'
        : 'Speicher jetzt abgleichen';
    }
    const fullResyncBtn = document.getElementById('project-action-full-resync');
    if (fullResyncBtn) {
      const isAdmin = state.authUser && state.authUser.role === 'admin';
      fullResyncBtn.style.display = isAdmin ? '' : 'none';
      fullResyncBtn.disabled = (live === 'syncing');
    }
    const historyBtn = document.getElementById('project-action-sync-history');
    if (historyBtn) {
      const isAdmin = state.authUser && state.authUser.role === 'admin';
      historyBtn.style.display = isAdmin ? '' : 'none';
    }
    // Re-paint per-item pills without re-fetching the underlying lists.
    paintProjectItemPills();
    // Re-tint the source-tree state dots from the fresh sync state.
    if (typeof repaintProjectTreeDots === 'function') repaintProjectTreeDots();
  } catch(e) {
    // Hide on auth/404 — non-managers may not be able to read it.
    chip.style.display = 'none';
    const ph = document.getElementById('project-sync-phases');
    if (ph) { ph.style.display = 'none'; ph.innerHTML = ''; }
  }
}

function timeAgo(timestamp) {
  const now = Date.now() / 1000;
  const diff = now - timestamp;
  if (diff < 60) return 'gerade eben';
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min.`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} Std.`;
  if (diff < 2592000) return `vor ${Math.floor(diff / 86400)} T.`;
  return `vor ${Math.floor(diff / 2592000)} Mon.`;
}

// ─── Sync History Modal ──────────────────────────────────────────────────────

let _syncHistoryPollHandles = {};

async function projectSyncHistory() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const modal = document.getElementById('sync-history-modal');
  const nameEl = document.getElementById('sync-history-project-name');
  if (!modal) return;
  if (nameEl) nameEl.textContent = projectName;
  modal.style.display = 'flex';
  await _loadSyncRuns(agentId, projectName);
}

function closeSyncHistoryModal() {
  const modal = document.getElementById('sync-history-modal');
  if (modal) modal.style.display = 'none';
  Object.values(_syncHistoryPollHandles).forEach(clearInterval);
  _syncHistoryPollHandles = {};
}

async function _loadSyncRuns(agentId, projectName) {
  const loadingEl = document.getElementById('sync-history-loading');
  const listEl = document.getElementById('sync-history-list');
  if (loadingEl) loadingEl.style.display = '';
  if (listEl) listEl.innerHTML = '';
  try {
    const data = await API.get(
      `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs?limit=20`
    );
    if (loadingEl) loadingEl.style.display = 'none';
    _renderSyncRuns(agentId, projectName, data.runs || []);
  } catch(e) {
    if (loadingEl) loadingEl.textContent = 'Verlauf konnte nicht geladen werden.';
  }
}

function _renderSyncRuns(agentId, projectName, runs) {
  const listEl = document.getElementById('sync-history-list');
  if (!listEl) return;
  if (!runs.length) {
    listEl.innerHTML = '<p style="color:var(--text-400);font-size:.85rem">Noch keine Abgleichläufe aufgezeichnet.</p>';
    return;
  }
  // Pair purge runs with the full_resync run that follows immediately after.
  // purge runs (triggered_by='full_resync_purge') are absorbed into the next
  // full_resync row so the user sees one logical "Full Resync" entry, not two.
  const paired = [];
  for (let i = 0; i < runs.length; i++) {
    const r = runs[i];
    if (r.triggered_by === 'full_resync_purge') {
      // Look behind for the full_resync that was queued right after this purge
      const prev = paired.length ? paired[paired.length - 1] : null;
      if (prev && prev._type === 'full_resync_pair') {
        prev._purgeRun = r;
      } else {
        paired.push({ _type: 'full_resync_pair', _purgeRun: r, _resyncRun: null });
      }
    } else if (r.triggered_by === 'full_resync') {
      const prev = paired.length ? paired[paired.length - 1] : null;
      if (prev && prev._type === 'full_resync_pair' && !prev._resyncRun) {
        prev._resyncRun = r;
      } else {
        paired.push({ _type: 'full_resync_pair', _purgeRun: null, _resyncRun: r });
      }
    } else {
      paired.push({ _type: 'single', _run: r });
    }
  }

  listEl.innerHTML = paired.map(entry => {
    if (entry._type === 'single') return _syncRunRowHtml(entry._run);
    return _syncRunPairHtml(entry._purgeRun, entry._resyncRun);
  }).join('');

  // Wire expand toggles — lazy-fetch full log on first expand
  listEl.querySelectorAll('.sh-run-header').forEach(hdr => {
    hdr.addEventListener('click', async () => {
      const row = hdr.closest('.sh-run');
      const wasExpanded = row.classList.contains('sh-expanded');
      row.classList.toggle('sh-expanded');
      if (!wasExpanded && !row.dataset.logLoaded) {
        row.dataset.logLoaded = '1';
        await _loadRunDetail(agentId, projectName, row);
      }
    });
  });
  // Wire cancel buttons
  listEl.querySelectorAll('.sh-cancel-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      btn.textContent = 'Wird abgebrochen …';
      try {
        await API.post(
          `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-cancel`, {}
        );
      } catch(_) {}
    });
  });
  // Start polling for any running rows
  runs.forEach(r => {
    if (r.state === 'running') _startSyncRunPoll(agentId, projectName, r.id);
  });
}

// Triggered-by → human label + colour hint
function _triggerLabel(tb) {
  return {
    scheduled: { text: 'Geplant', cls: '' },
    manual:    { text: 'Manuell', cls: '' },
    full_resync: { text: 'Vollständige Neu-Synchronisierung', cls: 'accent' },
    full_resync_purge: { text: 'Vollständige Neu-Synchronisierung', cls: 'accent' },
  }[tb] || { text: tb, cls: '' };
}

function _syncRunPairHtml(purgeRun, resyncRun) {
  // Use the resync run as the primary for state/timing; fall back to purge run.
  const primary = resyncRun || purgeRun;
  const stateColors = {
    running: 'var(--accent-blue)', idle: 'var(--success)',
    error: 'var(--error)', cancelled: 'var(--text-400)',
  };
  const color = stateColors[primary.state] || 'var(--text-400)';
  const isPulse = primary.state === 'running';
  const startedAgo = primary.started_at ? timeAgo(primary.started_at) : '?';
  const totalElapsed = (primary.finished_at && primary.started_at)
    ? _fmtElapsed(primary.finished_at - primary.started_at) : (primary.state === 'running' ? '…' : '');

  const rSum = resyncRun
    ? (typeof resyncRun.summary === 'string' ? JSON.parse(resyncRun.summary) : resyncRun.summary) || {}
    : {};
  const pSum = purgeRun
    ? (typeof purgeRun.summary === 'string' ? JSON.parse(purgeRun.summary) : purgeRun.summary) || {}
    : {};

  const statParts = [
    rSum.total_files != null    ? `${rSum.total_files} Dateien` : null,
    rSum.total_indexed != null  ? `${rSum.total_indexed} Schubladen` : null,
    rSum.total_triples != null  ? `${rSum.total_triples} Tripel` : null,
  ].filter(Boolean).join(' · ');

  const errStr = [...(rSum.errors || []), ...(pSum.errors || [])].filter(Boolean).join(', ');

  const runId = primary.id;
  const purgeAttr = purgeRun ? ` data-purge-run-id="${purgeRun.id}"` : '';
  return `<div class="sh-run" data-run-id="${runId}"${purgeAttr}>
    <div class="sh-run-header" style="display:flex;align-items:center;gap:10px;padding:9px 0;cursor:pointer;border-bottom:1px solid var(--border-200)">
      <span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0${isPulse ? ';animation:pulse 1.4s ease-in-out infinite' : ''}"></span>
      <span style="font-size:.82rem;color:var(--text-400);min-width:70px">${startedAgo}</span>
      <span style="font-size:.82rem;font-weight:500;flex:1">${statParts || (primary.state === 'running' ? 'Läuft …' : '—')}</span>
      ${errStr ? `<span style="font-size:.75rem;color:var(--error);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${errStr}">⚠ ${errStr}</span>` : ''}
      <span style="font-size:.75rem;padding:2px 7px;border-radius:4px;background:color-mix(in srgb,var(--accent-blue) 12%,transparent);color:var(--accent-blue)">Vollständige Neu-Synchronisierung</span>
      ${totalElapsed ? `<span style="font-size:.78rem;color:var(--text-400)">${totalElapsed}</span>` : ''}
      ${primary.state === 'running' ? `<button class="sh-cancel-btn" style="font-size:.75rem;padding:2px 8px;background:color-mix(in srgb,var(--error) 12%,transparent);border:1px solid color-mix(in srgb,var(--error) 30%,transparent);border-radius:4px;color:var(--error);cursor:pointer">Abbrechen</button>` : ''}
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" class="sh-chevron" style="transition:transform .2s;flex-shrink:0"><polyline points="6 9 12 15 18 9"/></svg>
    </div>
    <div class="sh-run-detail" style="display:none;padding:10px 0 4px">
      ${_syncRunPairDetailHtml(purgeRun, resyncRun)}
    </div>
  </div>`;
}

function _syncRunPairDetailHtml(purgeRun, resyncRun) {
  let html = '';

  // 1. Purge phase
  if (purgeRun) {
    const pLog = typeof purgeRun.log === 'string' ? JSON.parse(purgeRun.log) : (purgeRun.log || {});
    const pSum = typeof purgeRun.summary === 'string' ? JSON.parse(purgeRun.summary) : (purgeRun.summary || {});
    const purgeActions = pLog.purge_actions || [];
    const purgeElapsed = (purgeRun.finished_at && purgeRun.started_at)
      ? _fmtElapsed(purgeRun.finished_at - purgeRun.started_at) : '';

    html += `<div style="font-size:.78rem;font-weight:600;margin-bottom:6px;color:var(--text-400);display:flex;align-items:center;gap:8px">
      <span>Löschen</span>${purgeElapsed ? `<span style="font-weight:400">${purgeElapsed}</span>` : ''}
    </div>`;

    const P_LBL  = 'width:160px;min-width:160px;padding:2px 10px 2px 0;color:var(--text-400);white-space:nowrap';
    const P_DET  = 'padding:2px 0';
    const P_TIME = 'width:44px;min-width:44px;padding:2px 0 2px 10px;text-align:right;color:var(--text-400);white-space:nowrap';
    if (purgeActions.length) {
      const actionLabels = {
        drawers_purged: 'Schubladen gelöscht',
        kg_triples_purged: 'KG-Tripel gelöscht',
        closet_cursor_cleared: 'Closet-Cursor zurückgesetzt',
        doc_convert_cache_cleared: 'Doc-Convert-Cache geleert',
      };
      html += `<table style="font-size:.78rem;border-collapse:collapse;width:100%;table-layout:fixed;margin-bottom:12px">`;
      for (const a of purgeActions) {
        const label = actionLabels[a.action] || a.action;
        let detail = '';
        if (a.action === 'drawers_purged') detail = `${a.deleted ?? 0} gelöscht`;
        else if (a.action === 'kg_triples_purged') detail = `${a.triples_deleted ?? 0} Tripel, ${a.progress_cursors_deleted ?? 0} Cursor`;
        else if (a.action === 'doc_convert_cache_cleared') detail = `${a.dirs_removed ?? 0} Verzeichnisse, ${a.files_removed ?? 0} Dateien`;
        else detail = '✓';
        const errTxt = a.error ? ` <span style="color:var(--error)">⚠ ${a.error}</span>` : '';
        const elTxt = a.elapsed_s > 0 ? _fmtElapsed(a.elapsed_s) : '';
        html += `<tr>
          <td style="${P_LBL}">${label}</td>
          <td style="${P_DET}">${detail}${errTxt}</td>
          <td style="${P_TIME}">${elTxt}</td>
        </tr>`;
      }
      html += `</table>`;
    } else {
      // Fallback to summary fields if no purge_actions recorded
      const rows = [
        pSum.drawers_deleted != null ? ['Schubladen gelöscht', `${pSum.drawers_deleted}`] : null,
        pSum.triples_deleted != null ? ['KG-Tripel gelöscht', `${pSum.triples_deleted}`] : null,
        pSum.kg_progress_deleted != null ? ['KG-Cursor zurückgesetzt', `${pSum.kg_progress_deleted}`] : null,
        pSum.brain_extracted_cleared != null ? ['Doc-Convert-Verzeichnisse geleert', `${pSum.brain_extracted_cleared}`] : null,
      ].filter(Boolean);
      if (rows.length) {
        html += `<table style="font-size:.78rem;border-collapse:collapse;width:100%;table-layout:fixed;margin-bottom:12px">
          ${rows.map(([k,v]) => `<tr><td style="${P_LBL}">${k}</td><td style="${P_DET}">${v}</td><td style="${P_TIME}"></td></tr>`).join('')}
        </table>`;
      }
    }
  }

  // 2. Re-index phase
  if (resyncRun) {
    const rElapsed = (resyncRun.finished_at && resyncRun.started_at)
      ? _fmtElapsed(resyncRun.finished_at - resyncRun.started_at) : (resyncRun.state === 'running' ? '…' : '');
    html += `<div style="font-size:.78rem;font-weight:600;margin-bottom:6px;color:var(--text-400);display:flex;align-items:center;gap:8px">
      <span>Neu indexieren</span>${rElapsed ? `<span style="font-weight:400">${rElapsed}</span>` : ''}
    </div>`;
    html += _syncRunDetailHtml(resyncRun, { hideTitle: true });
  }

  return html || '<span style="color:var(--text-400);font-size:.78rem">Keine Details aufgezeichnet.</span>';
}

function _syncRunRowHtml(run) {
  const stateColors = {
    running: 'var(--accent-blue)', idle: 'var(--success)',
    error: 'var(--error)', cancelled: 'var(--text-400)',
  };
  const color = stateColors[run.state] || 'var(--text-400)';
  const isPulse = run.state === 'running';
  const startedAgo = run.started_at ? timeAgo(run.started_at) : '?';
  const elapsed = (run.finished_at && run.started_at)
    ? _fmtElapsed(run.finished_at - run.started_at) : (run.state === 'running' ? '…' : '');
  const summary = run.summary ? (typeof run.summary === 'string' ? JSON.parse(run.summary) : run.summary) : {};

  // Show current totals, not the per-cycle delta
  const statParts = [
    summary.total_files != null   ? `${summary.total_files} Dateien` : null,
    summary.total_indexed != null ? `${summary.total_indexed} Schubladen` : null,
    summary.total_triples != null && summary.total_triples > 0 ? `${summary.total_triples} Tripel` : null,
  ].filter(Boolean).join(' · ');

  const errStr = (summary.errors || []).filter(Boolean).join(', ');
  const trig = _triggerLabel(run.triggered_by);
  const trigHtml = `<span style="font-size:.75rem;padding:2px 6px;border-radius:4px;background:var(--bg-200);color:var(--text-400)">${trig.text}</span>`;

  return `<div class="sh-run" data-run-id="${run.id}">
    <div class="sh-run-header" style="display:flex;align-items:center;gap:10px;padding:9px 0;cursor:pointer;border-bottom:1px solid var(--border-200)">
      <span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0${isPulse ? ';animation:pulse 1.4s ease-in-out infinite' : ''}"></span>
      <span style="font-size:.82rem;color:var(--text-400);min-width:70px">${startedAgo}</span>
      <span style="font-size:.82rem;font-weight:500;flex:1">${statParts || (run.state === 'running' ? 'Läuft …' : '—')}</span>
      ${errStr ? `<span style="font-size:.75rem;color:var(--error);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${errStr}">⚠ ${errStr}</span>` : ''}
      ${trigHtml}
      ${elapsed ? `<span style="font-size:.78rem;color:var(--text-400)">${elapsed}</span>` : ''}
      ${run.state === 'running' ? `<button class="sh-cancel-btn" style="font-size:.75rem;padding:2px 8px;background:color-mix(in srgb,var(--error) 12%,transparent);border:1px solid color-mix(in srgb,var(--error) 30%,transparent);border-radius:4px;color:var(--error);cursor:pointer">Abbrechen</button>` : ''}
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" class="sh-chevron" style="transition:transform .2s;flex-shrink:0"><polyline points="6 9 12 15 18 9"/></svg>
    </div>
    <div class="sh-run-detail" style="display:none;padding:10px 0 4px">
      ${errStr ? `<div style="color:var(--error);font-size:.8rem;margin-bottom:8px">⚠ ${errStr}</div>` : ''}
      ${_syncRunDetailHtml(run)}
    </div>
  </div>`;
}

function _syncRunDetailHtml(run, opts = {}) {
  const log = run.log ? (typeof run.log === 'string' ? JSON.parse(run.log) : run.log) : {};
  const summary = run.summary ? (typeof run.summary === 'string' ? JSON.parse(run.summary) : run.summary) : {};
  const topSteps = log.steps || {};
  const folders = log.folders || [];

  let html = '';

  // Summary totals row (skip for purge runs — they have their own layout)
  if (!opts.hideTitle && run.triggered_by !== 'full_resync_purge') {
    const summaryParts = [
      summary.total_files != null   ? `${summary.total_files} Dateien` : null,
      summary.total_indexed != null ? `${summary.total_indexed} Schubladen` : null,
      summary.total_triples > 0    ? `${summary.total_triples} Tripel` : null,
      summary.folders_seen > 0     ? `${summary.folders_seen} Ordner` : null,
    ].filter(Boolean).join('  ·  ');
    if (summaryParts) {
      html += `<div style="font-size:.78rem;color:var(--text-400);margin-bottom:10px">${summaryParts}</div>`;
    }
  }

  // Per-folder phases + project-wide steps — rendered in one shared <table>
  // so the three columns (label | detail | elapsed) align across all sections.
  const LABEL_W = 'width:110px;min-width:110px';
  const TIME_W  = 'width:44px;min-width:44px';
  const TD_LBL  = `padding:2px 10px 2px 0;color:var(--text-400);white-space:nowrap;${LABEL_W}`;
  const TD_DET  = 'padding:2px 0';
  const TD_TIME = `padding:2px 0 2px 10px;text-align:right;color:var(--text-400);white-space:nowrap;${TIME_W}`;

  let tableRows = '';

  folders.forEach(f => {
    const fsteps = f.steps || {};
    const convSt  = fsteps.doc_convert || {};
    const indexSt = fsteps.indexing    || {};
    const kgSt    = fsteps.kg         || {};
    const fname = (f.path || '').split('/').filter(Boolean).pop() || f.path;

    // Section header row spanning all columns
    tableRows += `<tr>
      <td colspan="3" style="padding:8px 0 3px;font-size:.78rem;font-weight:600">
        <span style="display:inline-flex;align-items:center;gap:5px">
          <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <span title="${f.path}">${fname}</span>
        </span>
      </td>
    </tr>`;

    // Doc convert row
    if (convSt.started_at !== undefined || convSt.converted !== undefined) {
      const convErr = (convSt.errors || []).filter(Boolean).join(', ');
      const convElapsed = convSt.elapsed_s != null ? _fmtElapsed(convSt.elapsed_s)
        : (convSt.started_at && convSt.finished_at) ? _fmtElapsed(convSt.finished_at - convSt.started_at) : '';
      const convDetail = convErr
        ? `<span style="color:var(--error)">⚠ ${convErr}</span>`
        : [
            convSt.converted > 0   ? `${convSt.converted} konvertiert` : null,
            convSt.unchanged > 0   ? `${convSt.unchanged} unverändert` : null,
            convSt.failed > 0      ? `<span style="color:var(--error)">${convSt.failed} fehlgeschlagen</span>` : null,
            convSt.stale_removed > 0 ? `${convSt.stale_removed} veraltete entfernt` : null,
            convSt.seen_total === 0 ? 'nichts zu konvertieren' : null,
          ].filter(Boolean).join(', ') || '✓';
      tableRows += `<tr>
        <td style="${TD_LBL}">Doc-Convert</td>
        <td style="${TD_DET}">${convDetail}</td>
        <td style="${TD_TIME}">${convElapsed}</td>
      </tr>`;
    }

    // Indexing row
    if (indexSt.started_at !== undefined || indexSt.drawers_created !== undefined) {
      const idxErr = (indexSt.errors || []).filter(Boolean).join(', ');
      const idxElapsed = indexSt.elapsed_s != null ? _fmtElapsed(indexSt.elapsed_s)
        : (indexSt.started_at && indexSt.finished_at) ? _fmtElapsed(indexSt.finished_at - indexSt.started_at) : '';
      const idxDetail = idxErr
        ? `<span style="color:var(--error)">⚠ ${idxErr}</span>`
        : indexSt.drawers_created != null
          ? `${indexSt.drawers_created} Schubladen hinzugefügt`
          : '✓';
      tableRows += `<tr>
        <td style="${TD_LBL}">Indexierung</td>
        <td style="${TD_DET}">${idxDetail}</td>
        <td style="${TD_TIME}">${idxElapsed}</td>
      </tr>`;
    }

    // KG row
    if (kgSt.triples_this_cycle !== undefined || kgSt.triples_total !== undefined) {
      const kgErr = kgSt.error || '';
      const kgParseErrs = kgSt.parse_errors || 0;
      const kgElapsed = kgSt.elapsed_s != null ? _fmtElapsed(kgSt.elapsed_s) : '';
      const kgStats = [
        kgSt.triples_this_cycle != null ? `+${kgSt.triples_this_cycle} Tripel` : null,
        kgSt.triples_total != null      ? `(${kgSt.triples_total} gesamt)` : null,
        kgSt.drawers_processed != null  ? `${kgSt.drawers_processed} Schubladen verarbeitet` : null,
      ].filter(Boolean).join(' ') || '✓';
      const kgWarn = kgParseErrs > 0
        ? ` <span style="color:var(--warning,#a06000)" title="${kgErr}">· ${kgParseErrs} Parse-Fehler</span>`
        : (kgErr ? ` <span style="color:var(--error)" title="${esc(kgErr)}">⚠ ${kgErr}</span>` : '');
      const kgDetail = kgStats + kgWarn;
      tableRows += `<tr>
        <td style="${TD_LBL}">KG-Extraktion</td>
        <td style="${TD_DET}">${kgDetail}</td>
        <td style="${TD_TIME}">${kgElapsed}</td>
      </tr>`;
    }
  });

  // Stale-path purge (top-level step, not per-folder)
  const staleSt = topSteps.stale_path_purge;
  if (staleSt && (staleSt.drawers_deleted > 0 || staleSt.closets_deleted > 0)) {
    tableRows += `<tr>
      <td style="${TD_LBL}">Veraltete löschen</td>
      <td style="${TD_DET}">${staleSt.drawers_deleted || 0} Schubladen, ${staleSt.closets_deleted || 0} Closets entfernt</td>
      <td style="${TD_TIME}"></td>
    </tr>`;
  }

  // Closet rerank (project-wide, top-level)
  const closetSt = topSteps.closet_rerank;
  if (closetSt) {
    const closetErr = (closetSt.errors || []).filter(Boolean).join(', ');
    const closetElapsed = closetSt.elapsed_s != null ? _fmtElapsed(closetSt.elapsed_s)
      : (closetSt.started_at && closetSt.finished_at) ? _fmtElapsed(closetSt.finished_at - closetSt.started_at) : '';
    const closetDetail = closetErr
      ? `<span style="color:var(--error)">⚠ ${closetErr}</span>`
      : closetSt.regen_triggered
        ? `neu aufgebaut (${closetSt.sources_stale || 0}/${closetSt.sources_seen || 0} Quellen geändert)`
        : `übersprungen — ${closetSt.sources_seen || 0} Quellen unverändert`;
    tableRows += `<tr>
      <td style="${TD_LBL}">Closet-Neusortierung</td>
      <td style="${TD_DET}">${closetDetail}</td>
      <td style="${TD_TIME}">${closetElapsed}</td>
    </tr>`;
  }

  if (tableRows) {
    html += `<table style="font-size:.77rem;border-collapse:collapse;width:100%;table-layout:fixed">${tableRows}</table>`;
  }

  return html || '<span style="color:var(--text-400);font-size:.78rem">Keine Details aufgezeichnet.</span>';
}

function _fmtElapsed(secs) {
  if (secs < 1) return '<1s';
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

async function _loadRunDetail(agentId, projectName, rowEl) {
  // rowEl is a .sh-run element — it may be a single run or a paired full-resync.
  // For pairs, data-run-id is the primary (resync) run id; purge id is in data-purge-run-id.
  const detailEl = rowEl.querySelector('.sh-run-detail');
  if (!detailEl) return;
  detailEl.innerHTML = '<span style="color:var(--text-400);font-size:.78rem">Wird geladen …</span>';

  try {
    const primaryId = rowEl.dataset.runId;
    const purgeId   = rowEl.dataset.purgeRunId;

    const [primaryData, purgeData] = await Promise.all([
      primaryId ? API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${primaryId}`) : null,
      purgeId   ? API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${purgeId}`)   : null,
    ]);

    const primaryRun = primaryData?.run;
    const purgeRun   = purgeData?.run;

    if (!primaryRun) { detailEl.innerHTML = '<span style="color:var(--text-400);font-size:.78rem">Keine Details aufgezeichnet.</span>'; return; }

    let html = '';
    if (purgeRun) {
      // Paired full-resync: render purge + re-index sections
      html = _syncRunPairDetailHtml(purgeRun, primaryRun);
    } else {
      const errStr = ((primaryRun.summary?.errors || []).filter(Boolean)).join(', ');
      if (errStr) html += `<div style="color:var(--error);font-size:.8rem;margin-bottom:8px">⚠ ${errStr}</div>`;
      html += _syncRunDetailHtml(primaryRun);
    }
    detailEl.innerHTML = html;
  } catch(e) {
    detailEl.innerHTML = `<span style="color:var(--error);font-size:.78rem">Details konnten nicht geladen werden.</span>`;
  }
}

function _startSyncRunPoll(agentId, projectName, runId) {
  if (_syncHistoryPollHandles[runId]) return;
  _syncHistoryPollHandles[runId] = setInterval(async () => {
    try {
      const data = await API.get(
        `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${runId}`
      );
      const run = data.run;
      if (!run) return;
      const rowEl = document.querySelector(`[data-run-id="${runId}"]`);
      if (!rowEl) return;
      rowEl.outerHTML = _syncRunRowHtml(run);
      // Re-wire events for the new element
      const newRow = document.querySelector(`[data-run-id="${runId}"]`);
      if (newRow) {
        newRow.querySelector('.sh-run-header')?.addEventListener('click', () => {
          newRow.classList.toggle('sh-expanded');
        });
        newRow.querySelector('.sh-cancel-btn')?.addEventListener('click', async (e) => {
          e.stopPropagation();
          const btn = e.currentTarget;
          btn.disabled = true; btn.textContent = 'Cancelling…';
          try {
            await API.post(
              `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-cancel`, {}
            );
          } catch(_) {}
        });
      }
      if (run.state !== 'running') {
        clearInterval(_syncHistoryPollHandles[runId]);
        delete _syncHistoryPollHandles[runId];
      }
    } catch(_) {}
  }, 3000);
}

// Expand/collapse sh-run-detail on sh-expanded toggle (CSS-driven)
document.addEventListener('click', (e) => {
  const hdr = e.target.closest?.('.sh-run-header');
  if (!hdr) return;
  const run = hdr.closest('.sh-run');
  if (!run) return;
  const detail = run.querySelector('.sh-run-detail');
  const chevron = hdr.querySelector('.sh-chevron');
  if (detail) detail.style.display = run.classList.contains('sh-expanded') ? '' : 'none';
  if (chevron) chevron.style.transform = run.classList.contains('sh-expanded') ? 'rotate(180deg)' : '';
});

