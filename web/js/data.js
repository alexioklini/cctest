'use strict';

/* ═══════════════════════════════════════════════════════════
   DATA WORKBENCH VIEW
   A workbench session is a normal chat session flagged
   is_data_workbench. This view handles session-create, file
   upload (→ DuckDB tables), and table listing; the conversation
   itself happens in the ordinary chat view.
   ═══════════════════════════════════════════════════════════ */

const dvState = {
  sessions: [],          // [{session_id, title, tables, ...}]
  currentSid: null,
  tables: [],            // [{name, n_rows, columns, flagged_columns, sample}]
};

function _dvAuthHeaders() {
  const t = localStorage.getItem('auth-token');
  return t ? { 'Authorization': `Bearer ${t}` } : {};
}

async function loadDataView() {
  try {
    const r = await API.get('/v1/data/sessions');
    dvState.sessions = r.sessions || [];
  } catch (e) {
    dvState.sessions = [];
  }
  // Keep current selection if still present, else pick the newest.
  if (!dvState.currentSid || !dvState.sessions.some(s => s.session_id === dvState.currentSid)) {
    dvState.currentSid = dvState.sessions.length ? dvState.sessions[0].session_id : null;
  }
  dvRenderSessionSelect();
  if (dvState.currentSid) {
    await dvLoadTables();
  } else {
    dvState.tables = [];
    dvRenderTables();
    dvUpdateChatCta();
  }
}

function dvRenderSessionSelect() {
  const sel = document.getElementById('dv-session-select');
  if (!sel) return;
  if (!dvState.sessions.length) {
    sel.innerHTML = '<option value="">— no workbench yet —</option>';
    sel.value = '';
    return;
  }
  sel.innerHTML = dvState.sessions.map(s => {
    const label = (s.title && s.title.trim()) ? s.title : `Workbench ${s.session_id.slice(0, 8)}`;
    const n = (s.tables || []).length;
    return `<option value="${s.session_id}">${escapeHtml(label)}${n ? ` · ${n} table${n === 1 ? '' : 's'}` : ''}</option>`;
  }).join('');
  sel.value = dvState.currentSid || '';
}

async function dvNewSession() {
  const title = prompt('Name this workbench (optional):', '') || '';
  let r;
  try {
    r = await API.post('/v1/data/sessions', { title: title.trim() });
  } catch (e) {
    alert('Could not create workbench: ' + (e.message || e));
    return;
  }
  dvState.currentSid = r.session_id;
  await loadDataView();
}

function dvSwitchSession(sid) {
  dvState.currentSid = sid || null;
  if (sid) dvLoadTables();
  else { dvState.tables = []; dvRenderTables(); dvUpdateChatCta(); }
}

async function dvLoadTables() {
  if (!dvState.currentSid) return;
  try {
    const r = await API.get(`/v1/data/sessions/${dvState.currentSid}/tables`);
    dvState.tables = r.tables || [];
  } catch (e) {
    dvState.tables = [];
  }
  dvRenderTables();
  dvUpdateChatCta();
}

function dvRenderTables() {
  if (typeof dvSyncChartTableSelect === 'function') dvSyncChartTableSelect();
  const host = document.getElementById('dv-tables');
  if (!host) return;
  if (!dvState.tables.length) {
    host.innerHTML = '<div class="dv-table-meta">No tables yet — upload a .csv or .xlsx.</div>';
    return;
  }
  host.innerHTML = dvState.tables.map(t => {
    const cols = (t.columns || []).map(c => c.name).join(', ');
    const flagged = (t.flagged_columns || []).length
      ? `<div class="dv-flagged">⚑ contains personal data: ${(t.flagged_columns || []).map(escapeHtml).join(', ')}</div>` : '';
    const headers = (t.columns || []).map(c => `<th title="${escapeHtml(c.type || '')}">${escapeHtml(c.name)}</th>`).join('');
    const rows = (t.sample || []).slice(0, 8).map(r =>
      '<tr>' + r.map(v => {
        if (v === '«PII»') return '<td class="dv-pii">«PII»</td>';
        const s = (v === null || v === undefined) ? '' : String(v);
        return `<td title="${escapeHtml(s)}">${escapeHtml(s.slice(0, 80))}</td>`;
      }).join('') + '</tr>'
    ).join('');
    return `<div class="dv-table-card">
      <div class="dv-table-name">${escapeHtml(t.name)}</div>
      <div class="dv-table-meta">${t.n_rows} rows · ${(t.columns || []).length} columns</div>
      <div class="dv-table-meta" style="opacity:0.7">${escapeHtml(cols.slice(0, 200))}${cols.length > 200 ? '…' : ''}</div>
      ${flagged}
      <table class="dv-sample-table"><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>
    </div>`;
  }).join('');
}

function dvUpdateChatCta() {
  const btn = document.getElementById('dv-open-chat-btn');
  const txt = document.querySelector('#dv-chat-cta-inner .dv-cta-text');
  const anonBtn = document.getElementById('dv-anon-btn');
  const deanonBtn = document.getElementById('dv-deanon-btn');
  const scanBtn = document.getElementById('dv-scan-btn');
  if (!btn) return;
  if (!dvState.currentSid) {
    btn.disabled = true;
    if (anonBtn) anonBtn.disabled = true;
    if (deanonBtn) deanonBtn.disabled = true;
    if (scanBtn) scanBtn.disabled = true;
    if (txt) txt.textContent = 'Create a workbench, upload data, then chat with it.';
    return;
  }
  btn.disabled = false;
  const haveTables = dvState.tables.length > 0;
  if (anonBtn) anonBtn.disabled = !haveTables;
  if (scanBtn) scanBtn.disabled = !haveTables;
  if (deanonBtn) deanonBtn.disabled = !dvState.currentSid;
  if (txt) {
    const n = dvState.tables.length;
    txt.textContent = n
      ? `${n} table${n === 1 ? '' : 's'} loaded. Ask the assistant to query, summarise, or chart them.`
      : 'No tables yet — you can still chat, but upload data first for it to be useful.';
  }
}

async function dvUploadSelected(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  input.value = '';  // allow re-selecting the same file later
  if (!dvState.currentSid) {
    // Auto-create a workbench so the upload has somewhere to go.
    try {
      const r = await API.post('/v1/data/sessions', { title: '' });
      dvState.currentSid = r.session_id;
    } catch (e) {
      alert('Could not create workbench: ' + (e.message || e));
      return;
    }
  }
  const host = document.getElementById('dv-tables');
  if (host) host.innerHTML = '<div class="dv-table-meta">Uploading & ingesting…</div>';
  const fd = new FormData();
  fd.append('file', file, file.name);
  try {
    const res = await fetch(`/v1/data/sessions/${dvState.currentSid}/upload`, {
      method: 'POST', headers: _dvAuthHeaders(), body: fd,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
  } catch (e) {
    if (host) host.innerHTML = `<div class="dv-flagged">Upload failed: ${escapeHtml((e.message || String(e)).slice(0, 240))}</div>`;
    return;
  }
  await loadDataView();
}

async function dvOpenChat() {
  if (!dvState.currentSid) return;
  // The workbench session is a real chat session — open it in the chat view.
  // openSession() loads its messages, status bar, etc.
  navigateTo('chat');
  try {
    await openSession(dvState.currentSid, 'main');
  } catch (e) {
    // Fall back to a hard reload of the view if openSession isn't ready.
    console.error('dvOpenChat:', e);
  }
}

// Small HTML escaper (in case escapeHtml isn't globally available yet).
if (typeof escapeHtml !== 'function') {
  window.escapeHtml = function (s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };
}


/* ── Anonymise / deanonymise modals ─────────────────────────────────────── */

// Per-detector → sensible default strategy for pre-filling flagged columns.
const _DV_STRAT_FOR_CATEGORY = {
  contact: 'redact', personal: 'tokenise', national_id: 'tokenise',
  national_id_ctx: 'tokenise', bare_id: 'tokenise', financial: 'hash',
  secrets: 'redact', network: 'redact',
};

const _DV_STRATEGIES = [
  ['', '— keep as-is —'],
  ['tokenise', 'tokenise (surrogate, reversible)'],
  ['hash', 'hash (salted SHA-256)'],
  ['redact', 'redact (scrub PII inside text)'],
  ['generalise', 'generalise (coarsen)'],
  ['nullify', 'nullify (drop values)'],
  ['shuffle', 'shuffle (permute column)'],
  ['noise', 'noise (numeric jitter)'],
];

function _dvStratOptions(sel) {
  return _DV_STRATEGIES.map(([v, l]) => `<option value="${v}"${v === sel ? ' selected' : ''}>${l}</option>`).join('');
}

function dvOpenAnonModal(preTable) {
  if (!dvState.tables.length) { alert('Upload a table first.'); return; }
  const m = document.getElementById('dv-anon-modal');
  const body = document.getElementById('dv-anon-body');
  const tableOpts = dvState.tables.map(t => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)} (${t.n_rows} rows)</option>`).join('');
  body.innerHTML = `
    <div class="dv-anon-toolbar">
      <label>Table</label>
      <select id="dv-anon-table" onchange="dvAnonTableChanged()">${tableOpts}</select>
      <label>Output format</label>
      <select id="dv-anon-fmt">
        <option value="csv">csv (fresh)</option>
        <option value="xlsx">xlsx (fresh)</option>
        <option value="markdown">markdown</option>
        <option value="preserve">preserve original file</option>
      </select>
      <label><input type="checkbox" id="dv-anon-embed-salt"> embed hash salt (makes hash reversible)</label>
    </div>
    <div id="dv-anon-cols"></div>
    <div id="dv-anon-result"></div>
  `;
  document.getElementById('dv-anon-title').textContent = 'Anonymise';
  document.getElementById('dv-anon-status').textContent = '';
  document.getElementById('dv-anon-status').className = 'dv-modal-status';
  document.getElementById('dv-anon-run').disabled = false;
  m.classList.remove('hidden');
  if (preTable) document.getElementById('dv-anon-table').value = preTable;
  dvAnonTableChanged();
}

function dvAnonTableChanged() {
  const tname = document.getElementById('dv-anon-table').value;
  const t = dvState.tables.find(x => x.name === tname);
  const host = document.getElementById('dv-anon-cols');
  if (!t) { host.innerHTML = ''; return; }
  const flagged = new Set(t.flagged_columns || []);
  const rows = (t.columns || []).map((c, i) => {
    const isFlag = flagged.has(c.name);
    const def = isFlag ? 'tokenise' : '';
    // sample value from first row
    let sv = '';
    if (t.sample && t.sample[0]) {
      const v = t.sample[0][i];
      sv = (v === '«PII»') ? '«PII»' : String(v == null ? '' : v).slice(0, 40);
    }
    return `<div class="dv-anon-row" data-col="${escapeHtml(c.name)}">
      <input type="checkbox" class="dv-anon-pick"${isFlag ? ' checked' : ''}>
      <div>${isFlag ? '<span class="dv-flag">⚑ </span>' : ''}${escapeHtml(c.name)} <span style="opacity:.5">${escapeHtml(c.type || '')}</span></div>
      <div class="dv-anon-sample" title="${escapeHtml(sv)}">${escapeHtml(sv)}</div>
      <select class="dv-anon-strat">${_dvStratOptions(def)}</select>
      <input class="dv-anon-opts" placeholder='opts JSON e.g. {"prefix":"CUST"}'>
    </div>`;
  }).join('');
  host.innerHTML = `<div class="dv-anon-row header"><div></div><div>column</div><div>sample</div><div>strategy</div><div>options</div></div>${rows}`;
}

function dvCloseAnonModal() { document.getElementById('dv-anon-modal').classList.add('hidden'); }

async function dvRunAnonymise() {
  const tname = document.getElementById('dv-anon-table').value;
  const fmt = document.getElementById('dv-anon-fmt').value;
  const embedSalt = document.getElementById('dv-anon-embed-salt').checked;
  const cols = [];
  let badOpts = null;
  document.querySelectorAll('#dv-anon-cols .dv-anon-row[data-col]').forEach(row => {
    const pick = row.querySelector('.dv-anon-pick').checked;
    const strat = row.querySelector('.dv-anon-strat').value;
    if (!pick || !strat) return;
    const name = row.dataset.col;
    const optsRaw = (row.querySelector('.dv-anon-opts').value || '').trim();
    let opts = {};
    if (optsRaw) { try { opts = JSON.parse(optsRaw); } catch (e) { badOpts = name; } }
    cols.push({ name, strategy: strat, opts });
  });
  const status = document.getElementById('dv-anon-status');
  if (badOpts) { status.textContent = `Invalid options JSON for column "${badOpts}".`; status.className = 'dv-modal-status error'; return; }
  if (!cols.length) { status.textContent = 'Pick at least one column + strategy.'; status.className = 'dv-modal-status error'; return; }
  const body = { table: tname, columns: cols, output_format: fmt, embed_salt: embedSalt };
  if (fmt === 'preserve') {
    // Need the source file; offer to pick from known uploads — for PR2 the
    // server falls back to a fresh xlsx if it can't line up the sheet.
    const guess = prompt('Source file to rewrite in place (filename, e.g. ' + tname + '.xlsx). Leave blank to emit a fresh file:', '');
    if (guess && guess.trim()) body.source_file = guess.trim();
  }
  status.textContent = 'Running…'; status.className = 'dv-modal-status';
  document.getElementById('dv-anon-run').disabled = true;
  let res;
  try {
    res = await API.post(`/v1/data/sessions/${dvState.currentSid}/anonymise`, body);
  } catch (e) {
    status.textContent = (e.message || String(e)).slice(0, 240); status.className = 'dv-modal-status error';
    document.getElementById('dv-anon-run').disabled = false;
    return;
  }
  document.getElementById('dv-anon-run').disabled = false;
  if (res.error) { status.textContent = res.error.slice(0, 240); status.className = 'dv-modal-status error'; return; }
  // Remember for the deanonymise modal.
  dvState.lastAnon = { output_artifact: res.output_artifact, mapping_file: res.mapping_file };
  const rscan = res.residual_scan || {};
  const clean = rscan.clean;
  const findings = (rscan.findings || []).map(f => `${f.column} (${f.category} ×${f.count})`).join(', ');
  const kw = res.k_anon_warning;
  const dl = res.mapping_file
    ? `<div>Index file: <a href="/v1/data/sessions/${dvState.currentSid}/mapping/${encodeURIComponent(res.mapping_file)}" target="_blank">${escapeHtml(res.mapping_file)}</a> <span class="warn">(downloading is audit-logged — it's the re-identification key)</span></div>`
    : '<div>No index file — all strategies one-way (irreversible).</div>';
  document.getElementById('dv-anon-result').innerHTML = `<div class="dv-result-block">
    <div><b>Done.</b> New table: <code>${escapeHtml(res.new_table)}</code> · output: <code>${escapeHtml(res.output_artifact)}</code> (${escapeHtml(res.output_format)})</div>
    <div>Residual scan: ${clean ? '<span class="ok">CLEAN — 0 detections</span>' : '<span class="bad">still flagged: ' + escapeHtml(findings) + '</span> — treat those columns too and re-run.'}</div>
    ${kw ? `<div class="warn">k-anonymity warning: ${kw.groups_below_k} group(s) below k=${kw.k} (min ${kw.min_group_size}).</div>` : ''}
    ${dl}
  </div>`;
  status.textContent = clean ? 'Anonymised — residual scan clean.' : 'Anonymised, but residual scan still found PII.';
  status.className = 'dv-modal-status ' + (clean ? 'ok' : 'error');
  // Refresh the table list so the new _anon table shows up.
  dvLoadTables();
}

function dvOpenDeanonModal() {
  if (!dvState.currentSid) return;
  const m = document.getElementById('dv-deanon-modal');
  const last = dvState.lastAnon || {};
  document.getElementById('dv-deanon-body').innerHTML = `
    <div style="font-size:12px;color:var(--text-400);margin-bottom:10px">
      Restore the reversible columns (tokenise / hash-with-embedded-salt) of an anonymised file using the index workbook the anonymise run produced.
    </div>
    <div class="dv-anon-toolbar">
      <label>Anonymised file</label>
      <input id="dv-deanon-src" placeholder="e.g. mytable_anon.xlsx" value="${escapeHtml(last.output_artifact || '')}" style="min-width:240px;padding:4px 8px;border-radius:6px;background:var(--bg-200);color:var(--text-100);border:1px solid var(--border-200);font-size:12px">
    </div>
    <div class="dv-anon-toolbar">
      <label>Index file</label>
      <input id="dv-deanon-idx" placeholder="e.g. mytable_anon_map.xlsx" value="${escapeHtml(last.mapping_file || '')}" style="min-width:240px;padding:4px 8px;border-radius:6px;background:var(--bg-200);color:var(--text-100);border:1px solid var(--border-200);font-size:12px">
    </div>
    <div id="dv-deanon-result"></div>
  `;
  document.getElementById('dv-deanon-status').textContent = '';
  document.getElementById('dv-deanon-status').className = 'dv-modal-status';
  document.getElementById('dv-deanon-run').disabled = false;
  m.classList.remove('hidden');
}
function dvCloseDeanonModal() { document.getElementById('dv-deanon-modal').classList.add('hidden'); }

async function dvRunDeanonymise() {
  const src = (document.getElementById('dv-deanon-src').value || '').trim();
  const idx = (document.getElementById('dv-deanon-idx').value || '').trim();
  const status = document.getElementById('dv-deanon-status');
  if (!src || !idx) { status.textContent = 'Both filenames are required.'; status.className = 'dv-modal-status error'; return; }
  status.textContent = 'Running…'; status.className = 'dv-modal-status';
  document.getElementById('dv-deanon-run').disabled = true;
  let res;
  try {
    res = await API.post(`/v1/data/sessions/${dvState.currentSid}/deanonymise`, { source_file: src, index_file: idx });
  } catch (e) {
    status.textContent = (e.message || String(e)).slice(0, 240); status.className = 'dv-modal-status error';
    document.getElementById('dv-deanon-run').disabled = false;
    return;
  }
  document.getElementById('dv-deanon-run').disabled = false;
  if (res.error) { status.textContent = res.error.slice(0, 240); status.className = 'dv-modal-status error'; return; }
  const nr = (res.not_reversible || []).map(x => `${x.column} (${x.strategy})`).join(', ');
  document.getElementById('dv-deanon-result').innerHTML = `<div class="dv-result-block">
    <div><b>Done.</b> Restored file: <code>${escapeHtml(res.output_artifact)}</code></div>
    <div>Columns restored: ${escapeHtml((res.columns_restored || []).join(', ') || '—')}</div>
    ${nr ? `<div class="warn">Not reversible (left as-is): ${escapeHtml(nr)}</div>` : ''}
  </div>`;
  status.textContent = 'Restored.'; status.className = 'dv-modal-status ok';
}


/* ── §17 GDPR file-scan triage ──────────────────────────────────────────── */

const dvScanState = { results: [] };  // [{name, status, findings, worst_category, total_hits, _fixed?}]

function dvOpenScanModal() {
  if (!dvState.currentSid) return;
  const m = document.getElementById('dv-scan-modal');
  document.getElementById('dv-scan-body').innerHTML = '<div class="dv-table-meta">Click “Re-scan” to scan this workbench’s tables for GDPR-relevant data.</div>';
  document.getElementById('dv-scan-status').textContent = '';
  document.getElementById('dv-scan-status').className = 'dv-modal-status';
  document.getElementById('dv-scan-fixall').disabled = true;
  m.classList.remove('hidden');
  dvRunScan();
}
function dvCloseScanModal() { document.getElementById('dv-scan-modal').classList.add('hidden'); }

async function dvRunScan() {
  const status = document.getElementById('dv-scan-status');
  status.textContent = 'Scanning…'; status.className = 'dv-modal-status';
  document.getElementById('dv-scan-run').disabled = true;
  let res;
  try {
    res = await API.post(`/v1/data/sessions/${dvState.currentSid}/scan`, {});
  } catch (e) {
    status.textContent = (e.message || String(e)).slice(0, 240); status.className = 'dv-modal-status error';
    document.getElementById('dv-scan-run').disabled = false;
    return;
  }
  document.getElementById('dv-scan-run').disabled = false;
  if (res.error) { status.textContent = res.error.slice(0, 240); status.className = 'dv-modal-status error'; return; }
  dvScanState.results = res.files || [];
  dvRenderScan(res.summary || {});
}

function dvRenderScan(summary) {
  const body = document.getElementById('dv-scan-body');
  const rows = dvScanState.results;
  const hasDirty = rows.some(r => r.status === 'dirty');
  document.getElementById('dv-scan-fixall').disabled = !hasDirty;
  const sumHtml = `<div class="dv-scan-summary">
    <span>scanned ${summary.scanned ?? rows.length}</span>
    <span class="ok">${summary.clean ?? rows.filter(r => r.status === 'clean').length} clean</span>
    <span class="bad">${summary.dirty ?? rows.filter(r => r.status === 'dirty').length} contain GDPR-relevant data</span>
    ${(summary.error || 0) ? `<span>${summary.error} error</span>` : ''}
  </div>`;
  if (!rows.length) { body.innerHTML = sumHtml + '<div class="dv-table-meta">No tables in this workbench.</div>'; return; }
  const rowHtml = rows.map((r, i) => {
    if (r.status === 'clean') {
      return `<div class="dv-scan-row clean"><div class="dv-scan-head"><span class="dv-scan-name">✓ ${escapeHtml(r.name)}</span><span class="dv-scan-detail">clean — 0 detections</span></div></div>`;
    }
    if (r.status === 'error') {
      return `<div class="dv-scan-row"><div class="dv-scan-head"><span class="dv-scan-name bad">${escapeHtml(r.name)}</span><span class="dv-scan-detail">scan error: ${escapeHtml(r.error || '')}</span></div></div>`;
    }
    const findStr = (r.findings || []).map(f => `${escapeHtml(f.column)} (${escapeHtml(f.category)} ×${f.count} → ${escapeHtml(f.suggested_strategy)})`).join(', ');
    const fixed = r._fixed
      ? `<span class="dv-scan-fixmark">✓ fixed → ${escapeHtml(r._fixed.new_table || '')} (${r._fixed.clean ? 'rescan clean' : 'rescan still flagged'})</span>`
      : `<div class="dv-scan-actions">
           <button class="dv-btn" onclick="dvScanAutoFix(${i})">▶ Auto-fix</button>
           <button class="dv-btn" onclick="dvScanFixInGui(${i})">⚙ Fix in GUI…</button>
         </div>`;
    return `<div class="dv-scan-row${r._fixed ? ' fixed' : ''}">
      <div class="dv-scan-head">
        <span class="dv-scan-name bad">⚑ ${escapeHtml(r.name)}</span>
        ${fixed}
      </div>
      <div class="dv-scan-detail">worst: ${escapeHtml(r.worst_category || '?')} · ${r.total_hits} hits · suggested: ${findStr}</div>
    </div>`;
  }).join('');
  body.innerHTML = sumHtml + rowHtml;
}

function _dvColumnsFromFindings(findings) {
  // One spec per (column) — if a column has multiple categories, prefer the
  // most severe suggested strategy (redact > hash > tokenise covers the cases).
  const order = { redact: 3, hash: 2, tokenise: 1, generalise: 1, nullify: 1, shuffle: 0, noise: 0 };
  const byCol = {};
  (findings || []).forEach(f => {
    const cur = byCol[f.column];
    const s = f.suggested_strategy || 'tokenise';
    if (!cur || (order[s] || 0) > (order[cur] || 0)) byCol[f.column] = s;
  });
  return Object.entries(byCol).map(([name, strategy]) => ({ name, strategy, opts: {} }));
}

async function dvScanAutoFix(i) {
  const r = dvScanState.results[i];
  if (!r || r.status !== 'dirty') return;
  const cols = _dvColumnsFromFindings(r.findings);
  if (!cols.length) return;
  const status = document.getElementById('dv-scan-status');
  status.textContent = `Auto-fixing ${r.name}…`; status.className = 'dv-modal-status';
  let res;
  try {
    res = await API.post(`/v1/data/sessions/${dvState.currentSid}/anonymise`, { table: r.name, columns: cols, output_format: 'csv', embed_salt: true });
  } catch (e) {
    status.textContent = (e.message || String(e)).slice(0, 240); status.className = 'dv-modal-status error';
    return;
  }
  if (res.error) { status.textContent = res.error.slice(0, 240); status.className = 'dv-modal-status error'; return; }
  r._fixed = { new_table: res.new_table, clean: (res.residual_scan || {}).clean, mapping_file: res.mapping_file };
  status.textContent = `Fixed ${r.name} → ${res.new_table}${(res.residual_scan || {}).clean ? ' (rescan clean)' : ' (rescan still flagged)'}`;
  status.className = 'dv-modal-status ' + ((res.residual_scan || {}).clean ? 'ok' : 'error');
  dvRenderScan({});
  dvLoadTables();
}

function dvScanFixInGui(i) {
  const r = dvScanState.results[i];
  if (!r) return;
  dvCloseScanModal();
  dvOpenAnonModal(r.name);
}

async function dvScanFixAll() {
  const dirty = dvScanState.results.map((r, i) => ({ r, i })).filter(x => x.r.status === 'dirty' && !x.r._fixed);
  for (const { i } of dirty) {
    await dvScanAutoFix(i);  // sequential — each writes a new table
  }
  // Re-scan to confirm the new state.
  await dvRunScan();
}


/* ── Chart builder pane (PR1b) ──────────────────────────────────────────── */

function dvSyncChartTableSelect() {
  const sel = document.getElementById('dv-chart-table');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = (dvState.tables || []).map(t =>
    `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)} (${t.n_rows} rows)</option>`).join('')
    || '<option value="">— no tables —</option>';
  if (cur && dvState.tables.some(t => t.name === cur)) sel.value = cur;
}

function dvChartCols() {
  const tname = (document.getElementById('dv-chart-table') || {}).value;
  const t = (dvState.tables || []).find(x => x.name === tname);
  return t ? (t.columns || []) : [];
}

function dvChartInsertTemplate(kind) {
  const cols = dvChartCols();
  // Guess: first text-ish column for nominal, first numeric for quantitative.
  const isNum = c => /int|float|double|decimal|numeric|bigint/i.test(c.type || '');
  const isTime = c => /date|time/i.test(c.type || '');
  const cat = (cols.find(c => !isNum(c) && !isTime(c)) || cols[0] || {}).name || 'category';
  const num = (cols.find(isNum) || cols[1] || cols[0] || {}).name || 'value';
  const tm = (cols.find(isTime) || {}).name || cat;
  let spec;
  if (kind === 'bar') {
    spec = { mark: 'bar', encoding: { x: { field: cat, type: 'nominal', sort: '-y' }, y: { field: num, type: 'quantitative' } }, title: `${num} by ${cat}` };
  } else if (kind === 'line') {
    spec = { mark: 'line', encoding: { x: { field: tm, type: /date|time/i.test((cols.find(isTime) || {}).type || '') ? 'temporal' : 'nominal' }, y: { field: num, type: 'quantitative' } }, title: `${num} over ${tm}` };
  } else {
    const num2 = (cols.filter(isNum)[1] || {}).name || num;
    spec = { mark: 'point', encoding: { x: { field: num, type: 'quantitative' }, y: { field: num2, type: 'quantitative' }, color: { field: cat, type: 'nominal' } }, title: `${num} vs ${num2}` };
  }
  document.getElementById('dv-chart-spec').value = JSON.stringify(spec, null, 2);
}

function _dvParseSpec() {
  const raw = (document.getElementById('dv-chart-spec').value || '').trim();
  const status = document.getElementById('dv-chart-status');
  if (!raw) { status.textContent = 'Enter a Vega-Lite spec (or click a template).'; status.className = 'dv-modal-status error'; return null; }
  let spec;
  try { spec = JSON.parse(raw); } catch (e) { status.textContent = 'Spec is not valid JSON: ' + e.message; status.className = 'dv-modal-status error'; return null; }
  return spec;
}

async function dvChartPreview() {
  const spec = _dvParseSpec();
  if (!spec) return;
  const tname = (document.getElementById('dv-chart-table') || {}).value;
  const status = document.getElementById('dv-chart-status');
  if (!tname) { status.textContent = 'Pick a table.'; status.className = 'dv-modal-status error'; return; }
  // Client preview uses the ≤20-row sample we already have (no server round-trip).
  // For the full chart over all rows, use "Render PNG (server)".
  const t = (dvState.tables || []).find(x => x.name === tname);
  const cols = (t.columns || []).map(c => c.name);
  const rows = (t.sample || []).map(r => { const o = {}; cols.forEach((c, i) => { o[c] = r[i] === '«PII»' ? null : r[i]; }); return o; });
  const out = document.getElementById('dv-chart-out');
  out.innerHTML = '<div class="vega-embed" id="dv-vega-host"></div>';
  const full = Object.assign({ $schema: 'https://vega.github.io/schema/vega-lite/v5.json', data: { values: rows } }, spec);
  try {
    await vegaEmbed('#dv-vega-host', full, { actions: false });
    status.textContent = `Client preview (≤${rows.length}-row sample — use "Render PNG" for the full chart).`; status.className = 'dv-modal-status ok';
  } catch (e) {
    status.textContent = 'Vega-Lite error: ' + (e.message || String(e)).slice(0, 200); status.className = 'dv-modal-status error';
  }
}

async function dvChartRenderServer() {
  const spec = _dvParseSpec();
  if (!spec) return;
  const tname = (document.getElementById('dv-chart-table') || {}).value;
  const status = document.getElementById('dv-chart-status');
  if (!tname) { status.textContent = 'Pick a table.'; status.className = 'dv-modal-status error'; return; }
  status.textContent = 'Rendering…'; status.className = 'dv-modal-status';
  let res;
  try {
    res = await API.post(`/v1/data/sessions/${dvState.currentSid}/render`, { spec, table: tname });
  } catch (e) {
    status.textContent = (e.message || String(e)).slice(0, 240); status.className = 'dv-modal-status error';
    return;
  }
  if (res.error) { status.textContent = res.error.slice(0, 240); status.className = 'dv-modal-status error'; return; }
  const out = document.getElementById('dv-chart-out');
  out.innerHTML = `<img src="data:image/png;base64,${res.png_b64}" alt="chart">`;
  status.textContent = `Rendered (${res.n_rows} rows${res.note ? ' — ' + escapeHtml(res.note) : ''})${res.artifact ? ' · saved as ' + escapeHtml(res.artifact) : ''}.`;
  status.className = 'dv-modal-status ok';
}
