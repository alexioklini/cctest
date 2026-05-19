'use strict';

/* ═══════════════════════════════════════════════════════════
   Document Classification — Data view
   Detects ARL 20.02.02.06 classification level per file.
   Backend: /v1/classification/*
   ═══════════════════════════════════════════════════════════ */

const clsState = {
  mode: 'upload',          // 'upload' | 'folder' | 'project'
  pickedFiles: [],         // File objects
  agentsLoaded: false,
  lastResults: [],         // last scan results array
  lastScanId: '',          // empty until persisted
  inited: false,
};

const CLS_LEVEL_LABEL = {
  public: 'Öffentlich',
  internal: 'Intern',
  confidential: 'Vertraulich',
  strict: 'Streng Vertraulich',
  unmarked: 'Unmarked',
};

function clsInit() {
  if (clsState.inited) return;
  clsState.inited = true;
  // Drag-drop wiring
  const dz = document.getElementById('cls-dropzone');
  if (dz) {
    dz.addEventListener('click', () => document.getElementById('cls-file-input').click());
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
    dz.addEventListener('drop', e => {
      e.preventDefault();
      dz.classList.remove('dragover');
      clsFilesPicked(e.dataTransfer.files);
    });
  }
  // Preload agents for project mode
  clsLoadAgents();
}

function clsOpenView() {
  clsInit();
  clsSwitchTab('scan');
}

function clsSwitchTab(tab) {
  document.querySelectorAll('.cls-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.clsTab === tab);
  });
  document.getElementById('cls-tab-scan').classList.toggle('active', tab === 'scan');
  document.getElementById('cls-tab-history').classList.toggle('active', tab === 'history');
  if (tab === 'history') clsLoadHistory();
}

function clsModeChanged() {
  const mode = document.querySelector('input[name="cls-mode"]:checked').value;
  clsState.mode = mode;
  document.getElementById('cls-input-upload').classList.toggle('hidden', mode !== 'upload');
  document.getElementById('cls-input-folder').classList.toggle('hidden', mode !== 'folder');
  document.getElementById('cls-input-project').classList.toggle('hidden', mode !== 'project');
}

function clsFilesPicked(fileList) {
  clsState.pickedFiles = Array.from(fileList || []);
  clsRenderFileList();
}

function clsRenderFileList() {
  const box = document.getElementById('cls-file-list');
  if (!box) return;
  if (!clsState.pickedFiles.length) {
    box.innerHTML = '';
    return;
  }
  box.innerHTML = clsState.pickedFiles.map((f, i) => `
    <div class="cls-file-row">
      <span class="nm">${clsEsc(f.name)}</span>
      <span class="sz">${clsFmtSize(f.size)}</span>
      <span class="x" onclick="clsRemoveFile(${i})" title="Remove">✕</span>
    </div>
  `).join('');
}

function clsRemoveFile(idx) {
  clsState.pickedFiles.splice(idx, 1);
  clsRenderFileList();
}

function clsResetScan() {
  clsState.pickedFiles = [];
  clsState.lastResults = [];
  clsState.lastScanId = '';
  clsRenderFileList();
  document.getElementById('cls-results').classList.add('hidden');
  document.getElementById('cls-scan-status').textContent = '';
  document.getElementById('cls-folder-path').value = '';
}

async function clsLoadAgents() {
  try {
    const data = await API.get('/v1/agents');
    const agents = data.agents || [];
    const sel = document.getElementById('cls-project-agent');
    if (!sel) return;
    sel.innerHTML = agents.map(a => `<option value="${clsEsc(a.id)}">${clsEsc(a.id)}</option>`).join('');
    clsState.agentsLoaded = true;
    clsLoadProjects();
  } catch (e) {
    console.warn('cls: agents load failed', e);
  }
}

async function clsLoadProjects() {
  const agent = document.getElementById('cls-project-agent').value;
  if (!agent) return;
  try {
    const data = await API.get(`/v1/agents/${encodeURIComponent(agent)}/projects`);
    const projs = data.projects || [];
    const sel = document.getElementById('cls-project-name');
    sel.innerHTML = projs.map(p => {
      const nm = p.name || p.folder_name || '';
      const label = p.display_name || nm;
      return `<option value="${clsEsc(nm)}">${clsEsc(label)}</option>`;
    }).join('');
  } catch (e) {
    console.warn('cls: projects load failed', e);
  }
}

async function clsRunScan() {
  const btn = document.getElementById('cls-run-btn');
  const status = document.getElementById('cls-scan-status');
  status.classList.remove('err');
  status.textContent = 'Scanning…';
  btn.disabled = true;
  try {
    let resp;
    if (clsState.mode === 'upload') {
      if (!clsState.pickedFiles.length) throw new Error('No files picked');
      const fd = new FormData();
      clsState.pickedFiles.forEach(f => fd.append('files', f, f.name));
      const token = localStorage.getItem('auth-token');
      const headers = token ? {Authorization: `Bearer ${token}`} : {};
      const r = await fetch('/v1/classification/scan-files', {method: 'POST', headers, body: fd});
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      resp = await r.json();
    } else if (clsState.mode === 'folder') {
      const path = document.getElementById('cls-folder-path').value.trim();
      if (!path) throw new Error('Folder path required');
      resp = await API.post('/v1/classification/scan-folder', {
        path,
        recursive: document.getElementById('cls-folder-recursive').checked,
        persist: true,
      });
    } else {
      const agent_id = document.getElementById('cls-project-agent').value;
      const project_name = document.getElementById('cls-project-name').value;
      if (!project_name) throw new Error('Pick a project');
      resp = await API.post('/v1/classification/scan-project', {
        agent_id, project_name, persist: true,
      });
    }
    clsState.lastResults = resp.results || [];
    clsState.lastScanId = resp.scan_id || '';
    clsRenderSummary(resp.summary || {});
    clsRenderResults();
    document.getElementById('cls-results').classList.remove('hidden');
    status.textContent = `Scanned ${clsState.lastResults.length} file(s).`;
  } catch (e) {
    status.classList.add('err');
    status.textContent = e.message || String(e);
  } finally {
    btn.disabled = false;
  }
}

function clsRenderSummary(summary) {
  const box = document.getElementById('cls-summary');
  if (!box) return;
  const total = summary.total || 0;
  const bl = summary.by_level || {};
  const mismatch = summary.mismatch_count || 0;
  const errors = summary.error_count || 0;
  const parts = [
    `<b>${total}</b> file(s)`,
    `<span class="sep">·</span>`,
    `Public ${bl.public || 0}`,
    `<span class="sep">·</span>`,
    `Intern ${bl.internal || 0}`,
    `<span class="sep">·</span>`,
    `Vertraulich ${bl.confidential || 0}`,
    `<span class="sep">·</span>`,
    `Streng ${bl.strict || 0}`,
    `<span class="sep">·</span>`,
    `Unmarked ${bl.unmarked || 0}`,
  ];
  if (mismatch) parts.push(`<span class="sep">·</span><span class="warn">${mismatch} mismatch(es)</span>`);
  if (errors)   parts.push(`<span class="sep">·</span><span class="warn">${errors} error(s)</span>`);
  box.innerHTML = parts.join(' ');
}

function clsRenderResults() {
  const onlyMm = document.getElementById('cls-flt-mismatch').checked;
  const onlyUm = document.getElementById('cls-flt-unmarked').checked;
  const lvl = document.getElementById('cls-flt-level').value;
  const tbody = document.getElementById('cls-table-body');
  const rows = (clsState.lastResults || []).filter(r => {
    if (onlyMm && !r.mismatch) return false;
    if (onlyUm && r.final_level !== 'unmarked') return false;
    if (lvl && r.final_level !== lvl) return false;
    return true;
  });
  tbody.innerHTML = rows.map(clsRowHtml).join('') || `
    <tr><td colspan="6" style="text-align:center;color:var(--text-400);padding:24px">No matching results.</td></tr>`;
}

function clsRowHtml(r) {
  const markerPill = r.marker_level
    ? `<span class="cls-pill cls-pill-${r.marker_level}">${CLS_LEVEL_LABEL[r.marker_level]}</span>`
    : `<span class="cls-pill cls-pill-none">—</span>`;
  const finalPill = `<span class="cls-pill cls-pill-${r.final_level || 'unmarked'}">${CLS_LEVEL_LABEL[r.final_level] || r.final_level || 'Unmarked'}</span>`;
  const heur = r.heuristic_level || 'public';
  const heurPill = `<span class="cls-pill cls-pill-${heur}">${CLS_LEVEL_LABEL[heur]}</span>`;
  const mm = r.mismatch || null;
  let mmCell;
  if (!mm) {
    mmCell = `<span class="cls-mm-none">—</span>`;
  } else {
    const sev = mm.severity || 'low';
    const cls = sev === 'high' ? 'cls-mm-high' : sev === 'med' ? 'cls-mm-med' : 'cls-mm-low';
    mmCell = `<span class="${cls}">${sev.toUpperCase()}</span>
              <div class="cls-evidence">${(mm.reasons || []).map(clsEsc).join(' · ')}</div>`;
  }
  // Signals: keyword hits + PII count + marker excerpt
  const kw = r.keyword_hits || {};
  const kwHtml = Object.entries(kw).flatMap(([lvl, words]) =>
    words.slice(0, 3).map(w => `<span class="cls-kw" title="${clsEsc(lvl)}">${clsEsc(w)}</span>`)
  ).join('');
  const pii = r.pii_count ? `<span class="cls-kw" style="background:#fde2e2;color:#a02020">PII×${r.pii_count}</span>` : '';
  const ev = (r.marker_evidence && r.marker_evidence[0])
    ? `<div class="cls-evidence">"${clsEsc(r.marker_evidence[0].excerpt || '')}"</div>`
    : (r.filename_hint
        ? `<div class="cls-evidence">via filename</div>`
        : '');
  const errCell = r.error ? `<div class="cls-evidence" style="color:var(--error,#d33)">${clsEsc(r.error)}</div>` : '';
  return `<tr>
    <td><div>${clsEsc(r.filename || '')}</div>${errCell}</td>
    <td>${markerPill}${ev}</td>
    <td>${finalPill}</td>
    <td>${heurPill}</td>
    <td>${mmCell}</td>
    <td>${pii}${kwHtml || (pii ? '' : '<span class="cls-mm-none">—</span>')}</td>
  </tr>`;
}

function clsExportCsv() {
  if (!clsState.lastScanId) {
    // No persisted scan — build CSV client-side
    const rows = clsState.lastResults || [];
    if (!rows.length) return;
    const header = ['filename','marker_level','final_level','mismatch_severity','heuristic_level','pii_count','error'];
    const lines = [header.join(',')];
    rows.forEach(r => {
      const mm = r.mismatch || {};
      const cells = [
        r.filename || '', r.marker_level || '', r.final_level || '',
        mm.severity || '', r.heuristic_level || '', r.pii_count || 0,
        r.error || '',
      ].map(v => {
        const s = String(v);
        return /[",\n]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s;
      });
      lines.push(cells.join(','));
    });
    const blob = new Blob([lines.join('\n')], {type: 'text/csv;charset=utf-8'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'classification-scan.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    return;
  }
  // Server-side CSV (preserves the persisted snapshot)
  const token = localStorage.getItem('auth-token');
  const url = `/v1/classification/scans/${encodeURIComponent(clsState.lastScanId)}.csv`;
  fetch(url, {headers: token ? {Authorization:`Bearer ${token}`} : {}})
    .then(r => r.blob())
    .then(blob => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `classification-scan-${clsState.lastScanId}.csv`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
}

async function clsLoadHistory() {
  const box = document.getElementById('cls-history-list');
  box.innerHTML = `<div style="color:var(--text-400);font-size:13px">Loading…</div>`;
  try {
    const data = await API.get('/v1/classification/scans');
    const scans = data.scans || [];
    if (!scans.length) {
      box.innerHTML = `<div style="color:var(--text-400);font-size:13px;padding:20px;text-align:center">No scans yet.</div>`;
      return;
    }
    box.innerHTML = scans.map(s => {
      const summary = s.summary || {};
      const bl = summary.by_level || {};
      const mm = summary.mismatch_count || 0;
      const date = new Date((s.created_at || 0) * 1000).toLocaleString();
      const counts = [
        bl.confidential ? `${bl.confidential} vert.` : '',
        bl.strict       ? `${bl.strict} streng`     : '',
        bl.unmarked     ? `${bl.unmarked} unmarked` : '',
        mm              ? `${mm} mismatch`          : '',
      ].filter(Boolean).join(' · ') || `${s.file_count || 0} files`;
      return `
        <div class="cls-history-row" onclick="clsOpenScan('${clsEsc(s.scan_id)}')">
          <div class="lbl">
            <b>${clsEsc(s.source_label || s.source_kind || '')}</b>
            <div class="meta">${clsEsc(s.source_kind || '')} · ${date}</div>
          </div>
          <div class="stat">${counts}</div>
          <span class="x" onclick="event.stopPropagation();clsDeleteScan('${clsEsc(s.scan_id)}')" title="Delete">✕</span>
        </div>`;
    }).join('');
  } catch (e) {
    box.innerHTML = `<div style="color:var(--error,#d33);font-size:13px">${clsEsc(e.message || String(e))}</div>`;
  }
}

async function clsOpenScan(scanId) {
  try {
    const data = await API.get(`/v1/classification/scans/${encodeURIComponent(scanId)}`);
    clsState.lastScanId = scanId;
    clsState.lastResults = data.results || [];
    clsSwitchTab('scan');
    clsRenderSummary(data.summary || {});
    clsRenderResults();
    document.getElementById('cls-results').classList.remove('hidden');
    document.getElementById('cls-scan-status').textContent =
      `Loaded scan: ${data.source_label || data.source_kind}`;
  } catch (e) {
    showToast(e.message || String(e), true);
  }
}

async function clsDeleteScan(scanId) {
  if (!confirm('Delete this scan?')) return;
  try {
    await API.del(`/v1/classification/scans/${encodeURIComponent(scanId)}`);
    clsLoadHistory();
  } catch (e) {
    showToast(e.message || String(e), true);
  }
}

function clsFmtSize(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function clsEsc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
