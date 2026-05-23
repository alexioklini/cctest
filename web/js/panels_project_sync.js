// panels_project_sync.js — project input-folders, sync, sync-history. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

// ─── Project input folders + sync indicator ───────────────────────────
async function loadProjectInputFolders(agentId, projectName) {
  const container = document.getElementById('project-panel-input-folders');
  if (!container) return;
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders`);
    const folders = data.folders || [];
    // Stash for the edit modal so it doesn't need to refetch.
    state._projectInputFolders = folders;
    if (!folders.length) {
      container.innerHTML = '<span class="project-panel-placeholder">Add folders on disk to ingest into this project\'s memory. Files are scanned every 30 minutes and indexed for semantic search.</span>';
      return;
    }
    container.innerHTML = folders.map((f, idx) => {
      const fullPath = f.path || '';
      // Folder name = last path segment (or full path if there is no separator).
      const nameMatch = fullPath.replace(/\/+$/, '').split('/').filter(Boolean);
      const name = nameMatch.length ? nameMatch[nameMatch.length - 1] : fullPath;
      const recursive = f.recursive !== false;
      const autoSync = f.auto_sync !== false;  // default true for legacy entries
      return `
      <div class="project-input-folder-row">
        <div class="pif-row-head">
          <svg class="pif-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <span class="pif-name" title="${esc(fullPath)}">${esc(name)}</span>
          <button class="pif-action-btn" onclick="editProjectInputFolder(${idx})" title="Edit folder settings" aria-label="Edit">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
          </button>
          <button class="pif-action-btn pif-delete" onclick="removeProjectInputFolder(${idx})" title="Remove folder" aria-label="Remove">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
          </button>
        </div>
        <div class="pif-path" dir="ltr" title="${esc(fullPath)}">${esc(fullPath)}</div>
        <div class="pif-badges">
          <span class="pif-flag">${recursive ? 'recursive' : 'top-level'}</span>
          ${autoSync ? '' : '<span class="pif-flag" data-flag="paused" title="Excluded from automatic sync cycles — runs only on manual Sync now">auto-sync off</span>'}
          <span data-pif-pill data-pif-kind="folder" data-pif-id="${esc(fullPath)}">${projectItemPillHtml('folder', fullPath)}</span>
        </div>
      </div>
    `;}).join('');
  } catch(e) {
    container.innerHTML = '<span class="project-panel-placeholder">Failed to load input folders.</span>';
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
    <h2>Add input folder</h2>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">Pick a folder on disk. Files are scanned periodically and indexed into this project's memory.</div>
    <div id="pif-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pif-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-recursive" checked>
      Scan recursively (include all subfolders)
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-auto-sync" checked>
      Include in automatic sync cycles
      <span style="color:var(--text-400);font-size:12px">— uncheck to only sync manually</span>
    </label>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" onclick="_pifPickerSelect()">Add this folder</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  _pifLoadFolder('');  // empty path → server defaults to $HOME
}

async function _pifLoadFolder(path) {
  const crumbs = document.getElementById('pif-picker-crumbs');
  const list = document.getElementById('pif-picker-list');
  if (!crumbs || !list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-400);text-align:center">Loading…</div>';
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
      html += '<div style="padding:14px;color:var(--text-400);text-align:center;font-size:12px">(no subfolders)</div>';
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
  if (!path) { showToast('No folder selected', true); return; }
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
    showToast('Folder added — first scan running');
    document.querySelector('.sched-modal-overlay')?.remove();
    loadProjectInputFolders(agentId, projectName);
    // Trigger a sync now so the user sees activity immediately, even if
    // auto_sync is off — the user just opted in to a one-shot index.
    projectSyncNow();
  } catch(e) {
    showToast('Failed to add folder: ' + (e?.message || e));
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
      Remove input folder?
    </h2>
    <div style="font-size:13px;color:var(--text-300);line-height:1.5;margin:8px 0 16px">
      <div style="font-family:var(--font-mono);font-size:12px;background:var(--bg-100);padding:8px 10px;border-radius:6px;border:1px solid var(--border-100);word-break:break-all;margin-bottom:10px">${esc(folder.path || '')}</div>
      This folder will no longer be scanned. Already-indexed content stays in this project's memory until the project is purged.
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" style="background:#d33;border-color:#d33" onclick="_pifConfirmDelete(${idx})">Remove folder</button>
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
    showToast('Folder removed');
  } catch(e) {
    showToast('Failed to remove folder');
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
    <h2>Edit input folder</h2>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">Change the path or how this folder is scanned.</div>
    <div id="pif-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pif-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-recursive" ${recChecked}>
      Scan recursively (include all subfolders)
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-auto-sync" ${autoChecked}>
      Include in automatic sync cycles
      <span style="color:var(--text-400);font-size:12px">— uncheck to only sync manually</span>
    </label>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" onclick="_pifEditSave()">Save changes</button>
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
    showToast('Folder updated');
    document.querySelector('.sched-modal-overlay')?.remove();
    window._pifEditingIdx = null;
    loadProjectInputFolders(agentId, projectName);
  } catch(e) {
    showToast('Failed to update folder: ' + (e?.message || e));
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
    showToast('No project in scope', true);
    return;
  }
  if (typeof kgOpenProject !== 'function') {
    showToast('Knowledge graph viewer not available', true);
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
    showToast('Sync queued');
    refreshProjectSyncStatus(agentId, projectName);
  } catch(e) {
    showToast('Failed to trigger sync');
  }
}

async function projectFullResync() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  if (!await showConfirmDanger(`Full Resync will wipe all memory, knowledge graph triples, and sync state for "${projectName}", then re-index everything from scratch.\n\nContinue?`, 'Full Resync', 'Resync')) return;
  const btn = document.getElementById('project-action-full-resync');
  if (btn) { btn.disabled = true; btn.textContent = 'Wiping…'; }
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/full-resync`, {});
    showToast('Full resync queued — re-indexing from scratch');
    refreshProjectSyncStatus(agentId, projectName);
  } catch(e) {
    showToast('Full resync failed', true);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3"/></svg> Full Resync'; }
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
    const tripleStr = (triples != null && triples > 0) ? ` · ${triples} relations` : '';
    let label = 'Memory: idle';
    if (live === 'syncing') {
      // Live progress: P/T file count + ETA from elapsed-rate. cycle_total_files
      // is from a cheap pre-walk and may overshoot the miner's filtered file
      // count — that's deliberate (better than the bar getting stuck at 100%).
      const proc = Number(st.cycle_processed_files || 0);
      const tot = Number(st.cycle_total_files || 0);
      let progress = '';
      if (tot > 0) progress = ` ${proc}/${tot}`;
      // ETA: extrapolate from elapsed wall time and processed share. Only
      // show once we've made meaningful progress (>=5%) so an early 0/N
      // doesn't claim "ETA 12 days".
      let eta = '';
      const startedAt = st.started_at || st.last_run_started || '';
      if (startedAt && proc > 0 && tot > 0 && proc / tot >= 0.05) {
        const elapsedMs = Math.max(0, Date.now() - new Date(startedAt).getTime());
        const remainMs = elapsedMs * (tot - proc) / proc;
        if (remainMs > 1000 && remainMs < 1000 * 60 * 60 * 48) {
          const remainSec = Math.floor(remainMs / 1000);
          const etaStr = remainSec < 60 ? `${remainSec}s`
                       : remainSec < 3600 ? `${Math.floor(remainSec / 60)}m`
                       : `${Math.floor(remainSec / 3600)}h`;
          eta = ` · ETA ${etaStr}`;
        }
      }
      const cur = st.current_folder ? ` (${st.current_folder.split('/').pop()})` : '';
      label = `Memory: syncing${progress} files${eta}${cur}`;
      chip.title = 'Sync in progress';
      const sublabelElSync = document.getElementById('project-sync-sublabel');
      if (sublabelElSync) sublabelElSync.textContent = '';
    } else if (live === 'error') {
      label = 'Memory: error';
      chip.title = st.last_error || 'Sync failed — use Sync now to retry';
      const sublabelElErr = document.getElementById('project-sync-sublabel');
      if (sublabelElErr) sublabelElErr.textContent = '';
    } else {
      // Idle: lead with files (the unit users care about), then triples,
      // then "next sync in Xh". Last-synced timestamp and type shown in sub-label.
      const filesStr = totalFiles != null
        ? `${totalFiles} file${totalFiles === 1 ? '' : 's'}`
        : `${totalDrawers} indexed`;
      const next = data.next_run_at ? ` · next sync in ${humanIn(data.next_run_at)}` : '';
      label = `Memory: ${filesStr}${tripleStr}${next}`;
      const last = st.last_run_finished || data.last_scan || '';
      const drawerHint = (totalFiles != null && totalDrawers)
        ? `${totalDrawers} drawer${totalDrawers === 1 ? '' : 's'} · ` : '';
      chip.title = `${drawerHint}${last ? 'Last synced ' + humanAgo(last) + ' ago — ' : ''}use the buttons on the right to sync or open the knowledge graph`;
      // Sub-label: "synced Xh ago · Scheduled" or "synced Xh ago · Full Resync"
      const sublabelEl = document.getElementById('project-sync-sublabel');
      if (sublabelEl) {
        if (last) {
          const typeLabel = st.last_triggered_by === 'full_resync' ? 'Full Resync'
                          : st.last_triggered_by === 'manual' ? 'Manual' : 'Scheduled';
          sublabelEl.textContent = `synced ${humanAgo(last)} ago · ${typeLabel}`;
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
      let kgProgress = kgTotal > 0 ? ` ${kgDone}/${kgTotal} chunks` : (kgDone > 0 ? ` ${kgDone} chunks` : '');
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
      const kgTriplesStr = kgTriples > 0 ? ` · ${kgTriples} so far` : '';
      label = `Memory: KG extracting${kgProgress}${kgEta}${kgTriplesStr}`;
      chip.title = 'Knowledge graph extraction in progress';
      const sublabelElKg = document.getElementById('project-sync-sublabel');
      if (sublabelElKg) sublabelElKg.textContent = '';
    }
    labelEl.textContent = label;
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
        kgBtn.title = hasRelations
          ? `Open the knowledge graph drilldown (${st.total_triples} relations)`
          : 'No relations extracted yet — sync this project first';
      }
    }
    const syncBtn = document.getElementById('project-action-sync');
    if (syncBtn) {
      syncBtn.disabled = (live === 'syncing');
      syncBtn.title = live === 'syncing'
        ? 'Sync already in progress'
        : 'Run a memory sync now';
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
  } catch(e) {
    // Hide on auth/404 — non-managers may not be able to read it.
    chip.style.display = 'none';
  }
}

function timeAgo(timestamp) {
  const now = Date.now() / 1000;
  const diff = now - timestamp;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 2592000) return `${Math.floor(diff / 86400)}d ago`;
  return `${Math.floor(diff / 2592000)}mo ago`;
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
    if (loadingEl) loadingEl.textContent = 'Failed to load history.';
  }
}

function _renderSyncRuns(agentId, projectName, runs) {
  const listEl = document.getElementById('sync-history-list');
  if (!listEl) return;
  if (!runs.length) {
    listEl.innerHTML = '<p style="color:var(--text-400);font-size:.85rem">No sync runs recorded yet.</p>';
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
      btn.textContent = 'Cancelling…';
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
    scheduled: { text: 'Scheduled', cls: '' },
    manual:    { text: 'Manual',    cls: '' },
    full_resync: { text: 'Full Resync', cls: 'accent' },
    full_resync_purge: { text: 'Full Resync', cls: 'accent' },
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
    rSum.total_files != null    ? `${rSum.total_files} files` : null,
    rSum.total_indexed != null  ? `${rSum.total_indexed} drawers` : null,
    rSum.total_triples != null  ? `${rSum.total_triples} triples` : null,
  ].filter(Boolean).join(' · ');

  const errStr = [...(rSum.errors || []), ...(pSum.errors || [])].filter(Boolean).join(', ');

  const runId = primary.id;
  const purgeAttr = purgeRun ? ` data-purge-run-id="${purgeRun.id}"` : '';
  return `<div class="sh-run" data-run-id="${runId}"${purgeAttr}>
    <div class="sh-run-header" style="display:flex;align-items:center;gap:10px;padding:9px 0;cursor:pointer;border-bottom:1px solid var(--border-200)">
      <span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0${isPulse ? ';animation:pulse 1.4s ease-in-out infinite' : ''}"></span>
      <span style="font-size:.82rem;color:var(--text-400);min-width:70px">${startedAgo}</span>
      <span style="font-size:.82rem;font-weight:500;flex:1">${statParts || (primary.state === 'running' ? 'Running…' : '—')}</span>
      ${errStr ? `<span style="font-size:.75rem;color:var(--error);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${errStr}">⚠ ${errStr}</span>` : ''}
      <span style="font-size:.75rem;padding:2px 7px;border-radius:4px;background:color-mix(in srgb,var(--accent-blue) 12%,transparent);color:var(--accent-blue)">Full Resync</span>
      ${totalElapsed ? `<span style="font-size:.78rem;color:var(--text-400)">${totalElapsed}</span>` : ''}
      ${primary.state === 'running' ? `<button class="sh-cancel-btn" style="font-size:.75rem;padding:2px 8px;background:color-mix(in srgb,var(--error) 12%,transparent);border:1px solid color-mix(in srgb,var(--error) 30%,transparent);border-radius:4px;color:var(--error);cursor:pointer">Cancel</button>` : ''}
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
      <span>Purge</span>${purgeElapsed ? `<span style="font-weight:400">${purgeElapsed}</span>` : ''}
    </div>`;

    const P_LBL  = 'width:160px;min-width:160px;padding:2px 10px 2px 0;color:var(--text-400);white-space:nowrap';
    const P_DET  = 'padding:2px 0';
    const P_TIME = 'width:44px;min-width:44px;padding:2px 0 2px 10px;text-align:right;color:var(--text-400);white-space:nowrap';
    if (purgeActions.length) {
      const actionLabels = {
        drawers_purged: 'Drawers wiped',
        kg_triples_purged: 'KG triples wiped',
        closet_cursor_cleared: 'Closet cursor cleared',
        doc_convert_cache_cleared: 'Doc-convert cache cleared',
      };
      html += `<table style="font-size:.78rem;border-collapse:collapse;width:100%;table-layout:fixed;margin-bottom:12px">`;
      for (const a of purgeActions) {
        const label = actionLabels[a.action] || a.action;
        let detail = '';
        if (a.action === 'drawers_purged') detail = `${a.deleted ?? 0} deleted`;
        else if (a.action === 'kg_triples_purged') detail = `${a.triples_deleted ?? 0} triples, ${a.progress_cursors_deleted ?? 0} cursors`;
        else if (a.action === 'doc_convert_cache_cleared') detail = `${a.dirs_removed ?? 0} dirs, ${a.files_removed ?? 0} files`;
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
        pSum.drawers_deleted != null ? ['Drawers wiped', `${pSum.drawers_deleted}`] : null,
        pSum.triples_deleted != null ? ['KG triples wiped', `${pSum.triples_deleted}`] : null,
        pSum.kg_progress_deleted != null ? ['KG cursors cleared', `${pSum.kg_progress_deleted}`] : null,
        pSum.brain_extracted_cleared != null ? ['Doc-convert dirs cleared', `${pSum.brain_extracted_cleared}`] : null,
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
      <span>Re-index</span>${rElapsed ? `<span style="font-weight:400">${rElapsed}</span>` : ''}
    </div>`;
    html += _syncRunDetailHtml(resyncRun, { hideTitle: true });
  }

  return html || '<span style="color:var(--text-400);font-size:.78rem">No detail recorded.</span>';
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
    summary.total_files != null   ? `${summary.total_files} files` : null,
    summary.total_indexed != null ? `${summary.total_indexed} drawers` : null,
    summary.total_triples != null && summary.total_triples > 0 ? `${summary.total_triples} triples` : null,
  ].filter(Boolean).join(' · ');

  const errStr = (summary.errors || []).filter(Boolean).join(', ');
  const trig = _triggerLabel(run.triggered_by);
  const trigHtml = `<span style="font-size:.75rem;padding:2px 6px;border-radius:4px;background:var(--bg-200);color:var(--text-400)">${trig.text}</span>`;

  return `<div class="sh-run" data-run-id="${run.id}">
    <div class="sh-run-header" style="display:flex;align-items:center;gap:10px;padding:9px 0;cursor:pointer;border-bottom:1px solid var(--border-200)">
      <span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0${isPulse ? ';animation:pulse 1.4s ease-in-out infinite' : ''}"></span>
      <span style="font-size:.82rem;color:var(--text-400);min-width:70px">${startedAgo}</span>
      <span style="font-size:.82rem;font-weight:500;flex:1">${statParts || (run.state === 'running' ? 'Running…' : '—')}</span>
      ${errStr ? `<span style="font-size:.75rem;color:var(--error);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${errStr}">⚠ ${errStr}</span>` : ''}
      ${trigHtml}
      ${elapsed ? `<span style="font-size:.78rem;color:var(--text-400)">${elapsed}</span>` : ''}
      ${run.state === 'running' ? `<button class="sh-cancel-btn" style="font-size:.75rem;padding:2px 8px;background:color-mix(in srgb,var(--error) 12%,transparent);border:1px solid color-mix(in srgb,var(--error) 30%,transparent);border-radius:4px;color:var(--error);cursor:pointer">Cancel</button>` : ''}
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
      summary.total_files != null   ? `${summary.total_files} files` : null,
      summary.total_indexed != null ? `${summary.total_indexed} drawers` : null,
      summary.total_triples > 0    ? `${summary.total_triples} triples` : null,
      summary.folders_seen > 0     ? `${summary.folders_seen} folders` : null,
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
            convSt.converted > 0   ? `${convSt.converted} converted` : null,
            convSt.unchanged > 0   ? `${convSt.unchanged} unchanged` : null,
            convSt.failed > 0      ? `<span style="color:var(--error)">${convSt.failed} failed</span>` : null,
            convSt.stale_removed > 0 ? `${convSt.stale_removed} stale removed` : null,
            convSt.seen_total === 0 ? 'nothing to convert' : null,
          ].filter(Boolean).join(', ') || '✓';
      tableRows += `<tr>
        <td style="${TD_LBL}">Doc convert</td>
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
          ? `${indexSt.drawers_created} drawers added`
          : '✓';
      tableRows += `<tr>
        <td style="${TD_LBL}">Indexing</td>
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
        kgSt.triples_this_cycle != null ? `+${kgSt.triples_this_cycle} triples` : null,
        kgSt.triples_total != null      ? `(${kgSt.triples_total} total)` : null,
        kgSt.drawers_processed != null  ? `${kgSt.drawers_processed} drawers processed` : null,
      ].filter(Boolean).join(' ') || '✓';
      const kgWarn = kgParseErrs > 0
        ? ` <span style="color:var(--warning,#a06000)" title="${kgErr}">· ${kgParseErrs} parse err</span>`
        : (kgErr ? ` <span style="color:var(--error)" title="${esc(kgErr)}">⚠ ${kgErr}</span>` : '');
      const kgDetail = kgStats + kgWarn;
      tableRows += `<tr>
        <td style="${TD_LBL}">KG extraction</td>
        <td style="${TD_DET}">${kgDetail}</td>
        <td style="${TD_TIME}">${kgElapsed}</td>
      </tr>`;
    }
  });

  // Stale-path purge (top-level step, not per-folder)
  const staleSt = topSteps.stale_path_purge;
  if (staleSt && (staleSt.drawers_deleted > 0 || staleSt.closets_deleted > 0)) {
    tableRows += `<tr>
      <td style="${TD_LBL}">Stale purge</td>
      <td style="${TD_DET}">${staleSt.drawers_deleted || 0} drawers, ${staleSt.closets_deleted || 0} closets removed</td>
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
        ? `rebuilt (${closetSt.sources_stale || 0}/${closetSt.sources_seen || 0} sources changed)`
        : `skipped — ${closetSt.sources_seen || 0} sources unchanged`;
    tableRows += `<tr>
      <td style="${TD_LBL}">Closet rerank</td>
      <td style="${TD_DET}">${closetDetail}</td>
      <td style="${TD_TIME}">${closetElapsed}</td>
    </tr>`;
  }

  if (tableRows) {
    html += `<table style="font-size:.77rem;border-collapse:collapse;width:100%;table-layout:fixed">${tableRows}</table>`;
  }

  return html || '<span style="color:var(--text-400);font-size:.78rem">No detail recorded.</span>';
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
  detailEl.innerHTML = '<span style="color:var(--text-400);font-size:.78rem">Loading…</span>';

  try {
    const primaryId = rowEl.dataset.runId;
    const purgeId   = rowEl.dataset.purgeRunId;

    const [primaryData, purgeData] = await Promise.all([
      primaryId ? API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${primaryId}`) : null,
      purgeId   ? API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${purgeId}`)   : null,
    ]);

    const primaryRun = primaryData?.run;
    const purgeRun   = purgeData?.run;

    if (!primaryRun) { detailEl.innerHTML = '<span style="color:var(--text-400);font-size:.78rem">No detail recorded.</span>'; return; }

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
    detailEl.innerHTML = `<span style="color:var(--error);font-size:.78rem">Failed to load detail.</span>`;
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

