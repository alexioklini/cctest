/* ═══════════════════════════════════════════════════════════
   WORKFLOWS — list, editor, runner
   ═══════════════════════════════════════════════════════════ */

const WF_AGENT = 'main';  // MVP: single agent
const WF_KEYWORDS = new Set([
  'WORKFLOW','DESCRIPTION','TRIGGER','SET','CALL','IF','ELSE','FOR','EACH',
  'IN','RETURN','AND','OR','NOT','TRUE','FALSE','NULL'
]);
const WF_BUILTINS = new Set(['len','str','int','float','bool','now','lower','upper','trim','contains','split','join','replace']);

let wfState = {
  list: [],
  tools: [],            // tool palette
  currentName: null,    // editor: workflow currently being edited (null = new)
  currentExecId: null,  // currently-open inline detail view
  pollTimer: null,
  // Inline detail view state. Populated by wfOpenDetail(); torn down by
  // wfCloseDetail(). The follow-up session is created lazily on the first
  // wfDetailSend() — read-only browsing of a run leaves no DB rows behind.
  detailRun: null,            // last fetched run data (live or persisted)
  detailFollowups: [],        // [{role:'user'|'assistant', text, streaming?}]
  detailFollowupSid: null,    // hidden chat session bound to this run
  detailFollowupStreaming: false,
  detailPrevSubtab: 'list',   // restore on Back
};

/* ─── List view ─── */
async function loadWorkflows() {
  try {
    const data = await API.get(`/v1/agents/${WF_AGENT}/workflows`);
    wfState.list = data.workflows || [];
    wfRenderList();
  } catch (e) {
    console.error('loadWorkflows:', e);
  }
}

function wfRenderList() {
  const root = document.getElementById('wf-list');
  if (!root) return;
  if (!wfState.list.length) {
    root.innerHTML = `
      <div class="wf-empty">
        <p>No workflows yet.</p>
        <p class="wf-empty-hint">Click <strong>New Workflow</strong> to create one. Workflows are small scripts that compose Brain tools — useful for repeatable, automatable tasks.</p>
      </div>`;
    return;
  }
  root.innerHTML = wfState.list.map(wf => `
    <div class="wf-card" data-wf-name="${escapeHtml(wf.name)}">
      <div class="wf-card-row">
        <div class="wf-card-main">
          <div class="wf-card-title">${escapeHtml(wf.display_name || wf.name)}</div>
          <div class="wf-card-desc">${escapeHtml(wf.description || '')}</div>
          <div class="wf-card-meta">
            <span class="wf-pill">${escapeHtml(wf.trigger || 'manual')}</span>
            <span class="wf-card-fname">${escapeHtml(wf.file)}</span>
          </div>
        </div>
        <div class="wf-card-actions">
          <button class="wf-btn wf-btn-primary" onclick="wfRun('${escapeJs(wf.name)}')">Run</button>
          <button class="wf-btn wf-btn-ghost" onclick="wfOpenEditor('${escapeJs(wf.name)}')">Edit</button>
          <button class="wf-btn wf-btn-ghost" data-wf-history-btn="${escapeHtml(wf.name)}"
                  onclick="wfToggleHistory('${escapeJs(wf.name)}')">History</button>
          <button class="wf-btn wf-btn-ghost" onclick="wfDelete('${escapeJs(wf.name)}')">Delete</button>
        </div>
      </div>
      <div class="wf-card-history hidden" id="wf-hist-${escapeHtml(wf.name)}">
        <div class="wf-hist-loading">Loading history…</div>
      </div>
    </div>
  `).join('');
}

async function wfToggleHistory(name) {
  const root = document.getElementById('wf-hist-' + name);
  if (!root) return;
  const btn = document.querySelector(`[data-wf-history-btn="${name}"]`);
  if (!root.classList.contains('hidden')) {
    root.classList.add('hidden');
    if (btn) btn.textContent = 'History';
    return;
  }
  root.classList.remove('hidden');
  if (btn) btn.textContent = 'Hide history';
  root.innerHTML = '<div class="wf-hist-loading">Loading history…</div>';
  try {
    const data = await API.get(`/v1/workflows/history?workflow=${encodeURIComponent(name)}&limit=10`);
    wfRenderHistoryRows(root, data.executions || []);
  } catch (e) {
    root.innerHTML = `<div class="wf-hist-error">Could not load history: ${escapeHtml(e.message)}</div>`;
  }
}

function wfRenderHistoryRows(root, rows) {
  // Recover the workflow name from the container id so the Clear button knows
  // which workflow to scope to. Empty when this helper is reused elsewhere.
  const wfName = (root && root.id) ? root.id.replace(/^wf-hist-/, '') : '';
  if (!rows.length) {
    root.innerHTML = '<div class="wf-hist-empty">No runs yet.</div>';
    return;
  }
  // Toolbar appears whenever there's at least one terminal row to clear.
  const hasTerminal = rows.some(r => !['running','pending','waiting_approval'].includes(r.status));
  const clearBtn = (hasTerminal && wfName)
    ? `<button class="wf-btn wf-btn-ghost wf-btn-mini wf-btn-clear-hist" onclick="wfClearWorkflowHistory('${escapeJs(wfName)}')">Clear history</button>`
    : '';
  root.innerHTML = `
    ${clearBtn ? `<div class="wf-hist-toolbar">${clearBtn}</div>` : ''}
    <table class="wf-hist-table">
      <thead>
        <tr>
          <th>When</th>
          <th>Status</th>
          <th>Trigger</th>
          <th>User</th>
          <th>Duration</th>
          <th>LLM calls</th>
          <th>Tokens (in/out)</th>
          <th>Cost</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => wfHistoryRowHtml(r)).join('')}
      </tbody>
    </table>`;
}

function wfHistoryRowActions(r) {
  const status = r.status || 'unknown';
  const isCancelable = (status === 'running' || status === 'pending' || status === 'waiting_approval');
  const cancelBtn = isCancelable
    ? `<button class="wf-btn wf-btn-mini wf-btn-cancel" onclick="wfCancelFromHistory('${escapeJs(r.execution_id)}', event)">Cancel</button>`
    : '';
  // Terminal rows (completed / failed / cancelled) get a Delete button. Live
  // rows don't — user must Cancel first; once cancelled the next render shows
  // Delete in place of Cancel.
  const deleteBtn = isCancelable
    ? ''
    : `<button class="wf-btn wf-btn-mini wf-btn-delete" title="Delete this history row" onclick="wfDeleteRunFromHistory('${escapeJs(r.execution_id)}', event)">Delete</button>`;
  return `
    <div class="wf-hist-actions">
      <button class="wf-btn wf-btn-ghost wf-btn-mini" onclick="wfShowHistoryDetail('${escapeJs(r.execution_id)}')">View</button>
      ${cancelBtn}
      ${deleteBtn}
    </div>`;
}

function _wfRefreshOpenHistoryTables() {
  if (typeof loadWorkflowRuns === 'function' &&
      document.getElementById('wf-runs') &&
      !document.getElementById('wf-runs').classList.contains('hidden')) {
    loadWorkflowRuns();
  }
  document.querySelectorAll('.wf-card-history:not(.hidden)').forEach(root => {
    const wfName = root.id.replace(/^wf-hist-/, '');
    if (wfName) {
      API.get(`/v1/workflows/history?workflow=${encodeURIComponent(wfName)}&limit=10`)
        .then(d => wfRenderHistoryRows(root, d.executions || []))
        .catch(() => {});
    }
  });
}

async function wfDeleteRunFromHistory(executionId, ev) {
  if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation();
  if (!confirm('Delete this history entry? This cannot be undone.')) return;
  try {
    const r = await API.del(`/v1/workflows/history/${executionId}`);
    if (r && r.error) {
      alert('Delete failed: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    alert('Delete error: ' + e.message);
  }
}

async function wfClearWorkflowHistory(name) {
  if (!confirm(`Delete ALL terminal runs for "${name}"? This cannot be undone. Running entries are kept (cancel them first to delete).`)) return;
  try {
    const r = await API.del(`/v1/workflows/history?workflow=${encodeURIComponent(name)}`);
    if (r && r.error) {
      alert('Clear failed: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    alert('Clear error: ' + e.message);
  }
}

async function wfClearAllRuns() {
  const mine = document.getElementById('wf-runs-mine');
  const onlyMine = mine && mine.checked;
  const scope = onlyMine ? 'your runs' : 'all visible runs';
  if (!confirm(`Delete ${scope}? This cannot be undone. Running entries are kept (cancel them first to delete).`)) return;
  try {
    const url = '/v1/workflows/history' + (onlyMine ? '?mine=1' : '');
    const r = await API.del(url);
    if (r && r.error) {
      alert('Clear failed: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    alert('Clear error: ' + e.message);
  }
}

function wfHistoryRowHtml(r) {
  const status = r.status || 'unknown';
  const dur = r.duration_ms ? (r.duration_ms < 1000 ? r.duration_ms + 'ms' : (r.duration_ms / 1000).toFixed(1) + 's') : '—';
  const cost = r.cost_usd ? '$' + Number(r.cost_usd).toFixed(4) : '—';
  const tokens = (r.tokens_in || 0) + ' / ' + (r.tokens_out || 0);
  const when = r.started_at ? new Date(r.started_at).toLocaleString() : '—';
  return `
    <tr class="wf-hist-row wf-hist-${status}">
      <td>${escapeHtml(when)}</td>
      <td><span class="wf-status-${status}">${escapeHtml(status)}</span></td>
      <td>${escapeHtml(r.trigger_kind || 'manual')}</td>
      <td>${escapeHtml(r.user_display || '—')}</td>
      <td>${escapeHtml(dur)}</td>
      <td>${r.llm_calls || 0}</td>
      <td>${tokens}</td>
      <td>${cost}</td>
      <td>${wfHistoryRowActions(r)}</td>
    </tr>`;
}

async function wfCancelFromHistory(executionId, ev) {
  if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation();
  try {
    const r = await API.post(`/v1/workflows/executions/${executionId}/cancel`, {});
    if (r && r.error) {
      alert('Cancel failed: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    alert('Cancel error: ' + e.message);
  }
}

// Legacy entry point — every history-row View click routes through here. The
// modal it used to open is gone; the inline detail view takes over the
// workflow tab area instead.
async function wfShowHistoryDetail(executionId) {
  return wfOpenDetail(executionId);
}

/* ─── Sub-tab switching ─── */
function wfSwitchSubtab(name, el) {
  document.querySelectorAll('.wf-subtab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  const list = document.getElementById('wf-list');
  const runs = document.getElementById('wf-runs');
  if (name === 'list') {
    list.classList.remove('hidden');
    runs.classList.add('hidden');
  } else {
    list.classList.add('hidden');
    runs.classList.remove('hidden');
    loadWorkflowRuns();
  }
}

/* ─── Global runs view ─── */
async function loadWorkflowRuns() {
  const wrap = document.getElementById('wf-runs-table-wrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="wf-hist-loading">Loading runs…</div>';
  const status = document.getElementById('wf-runs-status').value;
  const mine = document.getElementById('wf-runs-mine').checked;
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (mine) params.set('mine', '1');
  params.set('limit', '100');
  try {
    const data = await API.get(`/v1/workflows/history?${params.toString()}`);
    const rows = data.executions || [];
    if (!rows.length) {
      wrap.innerHTML = '<div class="wf-hist-empty">No runs match.</div>';
      return;
    }
    wrap.innerHTML = `
      <table class="wf-hist-table wf-runs-table">
        <thead>
          <tr>
            <th>When</th><th>Workflow</th><th>Status</th><th>Trigger</th>
            <th>User</th><th>Duration</th><th>LLM calls</th><th>Tokens</th>
            <th>Cost</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => {
            const status = r.status || 'unknown';
            const dur = r.duration_ms ? (r.duration_ms < 1000 ? r.duration_ms + 'ms' : (r.duration_ms / 1000).toFixed(1) + 's') : '—';
            const cost = r.cost_usd ? '$' + Number(r.cost_usd).toFixed(4) : '—';
            const tokens = (r.tokens_in || 0) + ' / ' + (r.tokens_out || 0);
            const when = r.started_at ? new Date(r.started_at).toLocaleString() : '—';
            return `
              <tr class="wf-hist-row wf-hist-${status}">
                <td>${escapeHtml(when)}</td>
                <td><strong>${escapeHtml(r.workflow_name || '')}</strong></td>
                <td><span class="wf-status-${status}">${escapeHtml(status)}</span></td>
                <td>${escapeHtml(r.trigger_kind || 'manual')}</td>
                <td>${escapeHtml(r.user_display || '—')}</td>
                <td>${escapeHtml(dur)}</td>
                <td>${r.llm_calls || 0}</td>
                <td>${tokens}</td>
                <td>${cost}</td>
                <td>${wfHistoryRowActions(r)}</td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    wrap.innerHTML = `<div class="wf-hist-error">Could not load: ${escapeHtml(e.message)}</div>`;
  }
}

/* ─── Editor ─── */

// Round-trip helpers for header lines: WORKFLOW "..." / DESCRIPTION "..." / AGENT name
// Line patterns are anchored to start-of-line (allowing leading whitespace) and
// match an optional value, so we can both parse and rewrite.
const WF_HEADER_PATTERNS = {
  title:       /^[ \t]*WORKFLOW\b[^\n]*$/m,
  description: /^[ \t]*DESCRIPTION\b[^\n]*$/m,
  agent:       /^[ \t]*AGENT\b[^\n]*$/m,
};
const WF_HEADER_KEYWORDS = { title: 'WORKFLOW', description: 'DESCRIPTION', agent: 'AGENT' };
// Track which side of the meta↔script bridge made the most recent change so
// we don't re-sync back into the originator (would jump caret / trash undo).
let _wfSyncDirection = null;

function wfHeaderQuoted(field, raw) {
  // WORKFLOW + DESCRIPTION are quoted strings. AGENT is a bare identifier in
  // the .flow lexer (header parser accepts string OR ident; we emit ident
  // when the agent id is identifier-shaped, otherwise fall back to a quoted
  // string so unusual agent ids still round-trip safely).
  if (field === 'agent') {
    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(raw)) return raw;
    return JSON.stringify(raw);
  }
  return JSON.stringify(raw || '');
}

function wfParseHeader(source, field) {
  const re = WF_HEADER_PATTERNS[field];
  const m = re.exec(source || '');
  if (!m) return '';
  const line = m[0].trim();
  const after = line.replace(new RegExp('^' + WF_HEADER_KEYWORDS[field] + '\\b\\s*'), '');
  if (!after) return '';
  // Quoted string?
  const qm = after.match(/^"((?:[^"\\]|\\.)*)"|^'((?:[^'\\]|\\.)*)'/);
  if (qm) return (qm[1] !== undefined ? qm[1] : qm[2]).replace(/\\(.)/g, '$1');
  // Bare identifier (agent only).
  const im = after.match(/^([A-Za-z_][A-Za-z0-9_.\-]*)/);
  return im ? im[1] : '';
}

function wfWriteHeader(source, field, value) {
  // Replace the existing header line in-place if present, otherwise insert
  // a new one in canonical order: WORKFLOW → DESCRIPTION → TRIGGER → AGENT → MODEL.
  const re = WF_HEADER_PATTERNS[field];
  const kw = WF_HEADER_KEYWORDS[field];
  const trimmed = (value || '').trim();
  if (re.test(source || '')) {
    if (!trimmed) {
      // Empty value → drop the line entirely.
      return source.replace(re, '').replace(/^\n+/, '').replace(/\n{3,}/g, '\n\n');
    }
    return source.replace(re, `${kw} ${wfHeaderQuoted(field, trimmed)}`);
  }
  if (!trimmed) return source; // nothing to insert
  // Insert after the last existing header line, otherwise at top.
  const headerOrder = ['title','description','agent']; // WORKFLOW, DESCRIPTION, AGENT only — TRIGGER/MODEL we leave alone
  let insertAfter = -1;
  for (const f of headerOrder) {
    if (f === field) break;
    const m = WF_HEADER_PATTERNS[f].exec(source);
    if (m) insertAfter = Math.max(insertAfter, m.index + m[0].length);
  }
  // Also respect TRIGGER if present and we're inserting AGENT (canonical order: WORKFLOW/DESCRIPTION/TRIGGER/AGENT/MODEL)
  if (field === 'agent') {
    const trigRe = /^[ \t]*TRIGGER\b[^\n]*$/m;
    const tm = trigRe.exec(source);
    if (tm) insertAfter = Math.max(insertAfter, tm.index + tm[0].length);
  }
  const newLine = `${kw} ${wfHeaderQuoted(field, trimmed)}`;
  if (insertAfter < 0) {
    // No existing header lines — prepend.
    return newLine + '\n' + (source || '');
  }
  return source.slice(0, insertAfter) + '\n' + newLine + source.slice(insertAfter);
}

async function wfPopulateAgentDropdown() {
  const sel = document.getElementById('wf-editor-agent');
  if (!sel) return;
  // Skip refetch if already populated.
  if (sel.dataset.populated === '1') return;
  try {
    const data = await API.get('/v1/agents');
    const a = data && data.agents;
    let agents = [];
    if (Array.isArray(a)) agents = a.map(x => x.id || x.name).filter(Boolean);
    else if (a && typeof a === 'object') agents = Object.keys(a);
    agents.sort();
    // Keep the first option (Default) and append agents.
    const seen = new Set();
    sel.querySelectorAll('option').forEach(o => seen.add(o.value));
    for (const id of agents) {
      if (seen.has(id)) continue;
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = id;
      sel.appendChild(opt);
    }
    sel.dataset.populated = '1';
  } catch (e) {
    /* keep default option */
  }
}

function wfApplyMetaFromSource(source) {
  // Parse header values from the script and push into the input fields.
  _wfSyncDirection = 'script-to-meta';
  try {
    const titleEl = document.getElementById('wf-editor-title');
    const descEl  = document.getElementById('wf-editor-desc');
    const agentEl = document.getElementById('wf-editor-agent');
    if (titleEl) titleEl.value = wfParseHeader(source, 'title');
    if (descEl)  descEl.value  = wfParseHeader(source, 'description');
    if (agentEl) {
      const agentVal = wfParseHeader(source, 'agent');
      // If the parsed agent isn't in the dropdown yet, add it so we don't
      // silently drop the user's existing value.
      if (agentVal && !Array.from(agentEl.options).some(o => o.value === agentVal)) {
        const opt = document.createElement('option');
        opt.value = agentVal;
        opt.textContent = agentVal + ' (custom)';
        agentEl.appendChild(opt);
      }
      agentEl.value = agentVal;
    }
  } finally {
    _wfSyncDirection = null;
  }
}

function wfOnMetaChange(field) {
  if (_wfSyncDirection === 'script-to-meta') return;
  const editor = document.getElementById('wf-editor');
  if (!editor) return;
  const valEl = document.getElementById(
    field === 'title' ? 'wf-editor-title' :
    field === 'description' ? 'wf-editor-desc' : 'wf-editor-agent');
  if (!valEl) return;
  const newVal = (valEl.value || '').trim();
  _wfSyncDirection = 'meta-to-script';
  try {
    const before = editor.value;
    const after = wfWriteHeader(before, field, newVal);
    if (after !== before) {
      // Preserve caret position on the textarea by using setRangeText where
      // possible; for full-source rewrites we reset to current selection clamped.
      const selStart = editor.selectionStart, selEnd = editor.selectionEnd;
      editor.value = after;
      editor.selectionStart = Math.min(selStart, after.length);
      editor.selectionEnd   = Math.min(selEnd,   after.length);
      wfOnInput();
    }
  } finally {
    _wfSyncDirection = null;
  }
}

function wfOnScriptChange() {
  if (_wfSyncDirection === 'meta-to-script') return;
  const editor = document.getElementById('wf-editor');
  if (!editor) return;
  wfApplyMetaFromSource(editor.value || '');
}

async function wfOpenEditor(name) {
  wfState.currentName = name;
  const modal = document.getElementById('wf-editor-modal');
  const nameInput = document.getElementById('wf-editor-name');
  const editor = document.getElementById('wf-editor');
  const status = document.getElementById('wf-editor-status');
  status.textContent = '';
  // Populate agent list once per session.
  await wfPopulateAgentDropdown();
  if (name) {
    nameInput.value = name;
    nameInput.disabled = true;
    try {
      const data = await API.get(`/v1/agents/${WF_AGENT}/workflows/${encodeURIComponent(name)}`);
      editor.value = data.source || '';
    } catch (e) {
      editor.value = '';
    }
  } else {
    nameInput.value = '';
    nameInput.disabled = false;
    editor.value = WF_TEMPLATE_MEETING_NOTES;
  }
  // Sync the meta inputs from the script we just loaded.
  wfApplyMetaFromSource(editor.value);
  // Load tool palette eagerly so insertion is instant
  if (!wfState.tools.length) await wfLoadTools();
  modal.classList.remove('hidden');
  wfOnInput();
  setTimeout(() => editor.focus(), 50);
}

function wfCloseEditor() {
  document.getElementById('wf-editor-modal').classList.add('hidden');
}

async function wfSaveCurrent() {
  const nameInput = document.getElementById('wf-editor-name');
  const editor = document.getElementById('wf-editor');
  const status = document.getElementById('wf-editor-status');
  const name = (nameInput.value || '').trim();
  const source = editor.value;
  if (!name) {
    status.textContent = 'Name is required.';
    status.className = 'wf-editor-status wf-error';
    return;
  }
  status.textContent = 'Saving…';
  status.className = 'wf-editor-status';
  try {
    const data = await API.post(`/v1/agents/${WF_AGENT}/workflows`, { name, source });
    if (data.error) {
      status.textContent = data.error;
      status.className = 'wf-editor-status wf-error';
      return;
    }
    wfState.currentName = data.name;
    nameInput.disabled = true;
    status.textContent = 'Saved.';
    status.className = 'wf-editor-status wf-ok';
    loadWorkflows();
  } catch (e) {
    status.textContent = 'Save failed: ' + e.message;
    status.className = 'wf-editor-status wf-error';
  }
}

async function wfDelete(name) {
  if (!confirm(`Delete workflow "${name}"?`)) return;
  try {
    await API.del(`/v1/agents/${WF_AGENT}/workflows/${encodeURIComponent(name)}`);
    loadWorkflows();
  } catch (e) { console.error(e); }
}

/* ─── Editor: syntax highlighting ─── */
function wfOnInput() {
  const editor = document.getElementById('wf-editor');
  const overlay = document.getElementById('wf-highlight');
  const lineNumbers = document.getElementById('wf-line-numbers');
  if (!editor || !overlay) return;
  const src = editor.value;
  overlay.innerHTML = wfHighlight(src);
  // Line numbers
  const lines = src.split('\n').length;
  const nums = [];
  for (let i = 1; i <= lines; i++) nums.push(i);
  lineNumbers.textContent = nums.join('\n');
  wfSyncScroll();
}

function wfSyncScroll() {
  const editor = document.getElementById('wf-editor');
  const overlay = document.getElementById('wf-highlight');
  const lineNumbers = document.getElementById('wf-line-numbers');
  if (!editor) return;
  if (overlay) {
    overlay.scrollTop = editor.scrollTop;
    overlay.scrollLeft = editor.scrollLeft;
  }
  if (lineNumbers) {
    lineNumbers.scrollTop = editor.scrollTop;
  }
}

function wfOnKeydown(e) {
  // Tab inserts 4 spaces (don't break out of textarea)
  if (e.key === 'Tab') {
    e.preventDefault();
    const ta = e.target;
    const s = ta.selectionStart, en = ta.selectionEnd;
    ta.value = ta.value.slice(0, s) + '    ' + ta.value.slice(en);
    ta.selectionStart = ta.selectionEnd = s + 4;
    wfOnInput();
  }
}

/* Token-based highlighter — renders to HTML inside the overlay <pre>. */
function wfHighlight(src) {
  // We process line-by-line so comments + strings don't bleed across lines.
  const lines = src.split('\n');
  const out = [];
  for (const line of lines) {
    out.push(wfHighlightLine(line));
  }
  // Trailing newline so overlay matches textarea geometry
  return out.join('\n') + '\n';
}

function wfHighlightLine(line) {
  let i = 0, n = line.length;
  let buf = '';
  function emit(cls, text) {
    if (!text) return;
    if (cls) buf += `<span class="wf-tok-${cls}">${escapeHtml(text)}</span>`;
    else buf += escapeHtml(text);
  }
  while (i < n) {
    const c = line[i];
    // Comment
    if (c === '#') {
      emit('comment', line.slice(i));
      i = n;
      break;
    }
    // String
    if (c === '"' || c === "'") {
      const q = c;
      let j = i + 1;
      while (j < n) {
        if (line[j] === '\\' && j + 1 < n) { j += 2; continue; }
        if (line[j] === q) { j++; break; }
        j++;
      }
      // Highlight {{...}} interpolation segments specially
      const raw = line.slice(i, j);
      const parts = raw.split(/(\{\{[^}]*\}\})/);
      let composed = '';
      for (const p of parts) {
        if (p.startsWith('{{') && p.endsWith('}}')) {
          composed += `<span class="wf-tok-interp">${escapeHtml(p)}</span>`;
        } else {
          composed += `<span class="wf-tok-string">${escapeHtml(p)}</span>`;
        }
      }
      buf += composed;
      i = j;
      continue;
    }
    // Number
    if ((c >= '0' && c <= '9')) {
      let j = i + 1;
      while (j < n && (line[j] === '.' || (line[j] >= '0' && line[j] <= '9'))) j++;
      emit('number', line.slice(i, j));
      i = j;
      continue;
    }
    // Identifier / keyword
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c === '_') {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_]/.test(line[j])) j++;
      const word = line.slice(i, j);
      const upper = word.toUpperCase();
      if (WF_KEYWORDS.has(upper)) {
        // Special-case the very-likely-tool-call form: 'CALL <ident>'
        emit('keyword', word);
      } else if (WF_BUILTINS.has(word)) {
        emit('builtin', word);
      } else if (j < n && line[j] === '(') {
        emit('fn', word);
      } else {
        emit('ident', word);
      }
      i = j;
      continue;
    }
    // Punctuation / operators
    if ('+-*/<>=!?:,.()[]{}'.includes(c)) {
      // 2-char ops
      if (i + 1 < n) {
        const two = line.slice(i, i + 2);
        if (['==','!=','<=','>=','&&','||'].includes(two)) {
          emit('op', two);
          i += 2;
          continue;
        }
      }
      emit('op', c);
      i++;
      continue;
    }
    // Whitespace / fallback
    emit(null, c);
    i++;
  }
  return buf || ' ';  // ensure non-empty so empty lines still height-allocate
}

/* ─── Tool palette ─── */
async function wfLoadTools() {
  try {
    const data = await API.get(`/v1/agents/${WF_AGENT}/workflows/tools`);
    wfState.tools = data.tools || [];
  } catch (e) {
    console.error('wfLoadTools:', e);
  }
}

function wfShowToolPalette() {
  document.getElementById('wf-tool-palette').classList.remove('hidden');
  document.getElementById('wf-palette-filter').value = '';
  wfRenderToolList();
  setTimeout(() => document.getElementById('wf-palette-filter').focus(), 50);
}

function wfCloseToolPalette() {
  document.getElementById('wf-tool-palette').classList.add('hidden');
}

function wfRenderToolList() {
  const filter = (document.getElementById('wf-palette-filter').value || '').toLowerCase();
  const root = document.getElementById('wf-palette-list');
  const items = wfState.tools
    .filter(t => !filter || t.name.includes(filter) || (t.description || '').toLowerCase().includes(filter))
    .sort((a, b) => a.name.localeCompare(b.name));
  root.innerHTML = items.map(t => {
    const argHtml = t.args.map(a => `<span class="wf-arg ${a.required ? 'required' : ''}">${escapeHtml(a.name)}</span>`).join(' ');
    return `
      <div class="wf-palette-item" onclick="wfInsertToolCall('${escapeJs(t.name)}')">
        <div class="wf-palette-name">${escapeHtml(t.name)}</div>
        <div class="wf-palette-desc">${escapeHtml(t.description || '')}</div>
        <div class="wf-palette-args">${argHtml}</div>
      </div>`;
  }).join('');
}

function wfInsertToolCall(toolName) {
  const tool = wfState.tools.find(t => t.name === toolName);
  if (!tool) return;
  const editor = document.getElementById('wf-editor');
  // Build a CALL line with each required arg as name="" and a comment listing optional args
  const requiredArgs = tool.args.filter(a => a.required);
  const optionalArgs = tool.args.filter(a => !a.required);
  let line = `CALL ${tool.name}`;
  for (const a of requiredArgs) {
    line += ` ${a.name}=""`;
  }
  // If the tool has a primary_field, suggest a SET wrapper using that field name
  let snippet;
  if (tool.primary_field) {
    snippet = `SET result = ${line}\n# Access fields: result.${tool.primary_field}\n`;
  } else {
    snippet = line + '\n';
  }
  if (optionalArgs.length) {
    snippet += `# Optional args: ${optionalArgs.map(a => a.name).join(', ')}\n`;
  }
  // Insert at caret
  const s = editor.selectionStart, en = editor.selectionEnd;
  const before = editor.value.slice(0, s);
  const after = editor.value.slice(en);
  // Snap to start of current line
  const lineStart = before.lastIndexOf('\n') + 1;
  const newCaret = lineStart + snippet.length;
  editor.value = editor.value.slice(0, lineStart) + snippet + editor.value.slice(lineStart + (s - lineStart) + (en - s)) + after.slice(0);
  // Simpler approach: just insert at caret, ignoring the snap
  // (revert to direct insert to avoid bugs):
  editor.value = before + snippet + after;
  editor.selectionStart = editor.selectionEnd = s + snippet.length;
  wfCloseToolPalette();
  editor.focus();
  wfOnInput();
}

/* ─── Run ─── */
async function wfRun(name) {
  try {
    const data = await API.post(`/v1/agents/${WF_AGENT}/workflows/${encodeURIComponent(name)}/run`, { variables: {} });
    if (data.error) {
      alert('Run failed: ' + data.error);
      return;
    }
    // Drop straight into the inline detail view; live polling kicks in.
    wfOpenDetail(data.execution_id);
  } catch (e) {
    alert('Run error: ' + e.message);
  }
}

/* ─── Inline workflow-run detail view ─── */

const WF_TERMINAL_STATUSES = new Set(['completed', 'succeeded', 'failed', 'cancelled']);
const WF_LIVE_STATUSES = new Set(['running', 'pending', 'waiting_approval']);

async function wfOpenDetail(executionId) {
  // Remember which subtab the user came from so Back restores it. If we're
  // already in a different detail view, tear it down cleanly first.
  if (wfState.currentExecId && wfState.currentExecId !== executionId) {
    wfStopPolling();
    wfState.detailFollowups = [];
    wfState.detailFollowupSid = null;
    wfState.detailFollowupStreaming = false;
  }
  if (!document.getElementById('wf-detail').classList.contains('hidden')) {
    // Already in a detail view — keep prevSubtab intact.
  } else {
    const activeTab = document.querySelector('.wf-subtab.active');
    wfState.detailPrevSubtab = (activeTab && activeTab.dataset.subtab) || 'list';
  }
  wfState.currentExecId = executionId;
  // Hide the list/runs surface, show the detail surface.
  const subtabs = document.getElementById('wf-subtabs');
  if (subtabs) subtabs.classList.add('hidden');
  document.getElementById('wf-list').classList.add('hidden');
  document.getElementById('wf-runs').classList.add('hidden');
  const det = document.getElementById('wf-detail');
  det.classList.remove('hidden');
  // Reset transient view state
  document.getElementById('wf-detail-title').textContent = 'Loading…';
  document.getElementById('wf-detail-meta').textContent = '';
  document.getElementById('wf-detail-turns').innerHTML = '<div class="wf-hist-loading">Loading…</div>';
  document.getElementById('wf-detail-prompt').classList.add('hidden');
  document.getElementById('wf-detail-cancel-btn').classList.add('hidden');
  document.getElementById('wf-detail-save-btn').classList.add('hidden');
  wfDetailUpdateComposerEnabled(false, 'Loading run…');
  // Try the live endpoint first (fresh steps for an in-flight run); fall
  // back to the persisted /history row when the execution isn't live.
  let initial = null;
  try {
    const r = await API.get(`/v1/workflows/executions/${executionId}`);
    if (r && !r.error) initial = r;
  } catch (_) { /* not live → fall through */ }
  if (!initial) {
    try {
      initial = await API.get(`/v1/workflows/history/${executionId}`);
    } catch (e) {
      document.getElementById('wf-detail-turns').innerHTML =
        `<div class="wf-hist-error">Could not load: ${escapeHtml(e.message)}</div>`;
      return;
    }
  }
  wfState.detailRun = initial;
  wfRenderDetail(initial);
  if (WF_LIVE_STATUSES.has(initial.status)) {
    wfStartPolling();
  }
}

function wfCloseDetail() {
  wfStopPolling();
  // Abort any in-flight follow-up stream so we don't leak text into the
  // hidden view if the user re-opens it.
  if (wfState.detailFollowupStreaming && API._abortController) {
    try { API._abortController.abort(); } catch (_) {}
  }
  wfState.currentExecId = null;
  wfState.detailRun = null;
  wfState.detailFollowups = [];
  wfState.detailFollowupSid = null;
  wfState.detailFollowupStreaming = false;
  document.getElementById('wf-detail').classList.add('hidden');
  const subtabs = document.getElementById('wf-subtabs');
  if (subtabs) subtabs.classList.remove('hidden');
  // Restore previous subtab without forcing a reload of the run table.
  const prev = wfState.detailPrevSubtab || 'list';
  if (prev === 'runs') {
    document.getElementById('wf-list').classList.add('hidden');
    document.getElementById('wf-runs').classList.remove('hidden');
    loadWorkflowRuns();
  } else {
    document.getElementById('wf-runs').classList.add('hidden');
    document.getElementById('wf-list').classList.remove('hidden');
    // Refresh open history tables so a freshly-finished run reflects its
    // terminal status without the user clicking Refresh.
    _wfRefreshOpenHistoryTables();
  }
  document.querySelectorAll('.wf-subtab').forEach(t => {
    t.classList.toggle('active', t.dataset.subtab === prev);
  });
}

function wfStartPolling() {
  wfStopPolling();
  wfState.pollTimer = setInterval(wfPoll, 700);
  wfPoll();
}

function wfStopPolling() {
  if (wfState.pollTimer) {
    clearInterval(wfState.pollTimer);
    wfState.pollTimer = null;
  }
}

async function wfPoll() {
  const id = wfState.currentExecId;
  if (!id) return;
  try {
    const data = await API.get(`/v1/workflows/executions/${id}`);
    if (!data || data.error) return;
    wfState.detailRun = data;
    wfRenderDetail(data);
    if (WF_TERMINAL_STATUSES.has(data.status)) {
      wfStopPolling();
      // Re-fetch from /history once so the persisted row (which has the
      // full steps_json + return_value) matches what the chat-style view
      // shows for completed runs.
      try {
        const persisted = await API.get(`/v1/workflows/history/${id}`);
        if (persisted && !persisted.error) {
          wfState.detailRun = persisted;
          wfRenderDetail(persisted);
        }
      } catch (_) {}
    }
  } catch (e) { /* swallow transient */ }
}

async function wfDetailCancel() {
  const id = wfState.currentExecId;
  if (!id) return;
  try {
    const r = await API.post(`/v1/workflows/executions/${id}/cancel`, {});
    if (r && r.error) {
      alert('Cancel failed: ' + r.error);
      return;
    }
    // Poll once to reflect the new status immediately.
    wfPoll();
  } catch (e) {
    alert('Cancel error: ' + e.message);
  }
}

/* ─── Detail rendering: chat-style turns ─── */

function wfRenderDetail(data) {
  if (!data) return;
  const status = data.status || 'unknown';
  const isLive = WF_LIVE_STATUSES.has(status);
  // Header
  const titleEl = document.getElementById('wf-detail-title');
  const metaEl = document.getElementById('wf-detail-meta');
  const verb = isLive ? 'Running' : 'History';
  titleEl.textContent = `${verb}: ${data.workflow_name || wfState.currentExecId}`;
  const dur = data.duration_ms
    ? (data.duration_ms < 1000 ? data.duration_ms + 'ms' : (data.duration_ms / 1000).toFixed(1) + 's')
    : '';
  const cost = data.cost_usd ? '$' + Number(data.cost_usd).toFixed(4) : '';
  const metaBits = [
    `<span class="wf-status-${status}">${escapeHtml(status)}</span>`,
    data.user_display ? `by ${escapeHtml(data.user_display)}` : '',
    data.trigger_kind ? `via ${escapeHtml(data.trigger_kind)}` : '',
    dur,
    cost,
    `<span class="wf-detail-execid" title="${escapeHtml(wfState.currentExecId)}">${escapeHtml((wfState.currentExecId || '').slice(0, 8))}</span>`,
  ].filter(Boolean);
  metaEl.innerHTML = metaBits.join(' · ');
  // Cancel + Save buttons
  document.getElementById('wf-detail-cancel-btn').classList.toggle('hidden', !isLive);
  // Save-to-chats only meaningful once a follow-up session exists. Without
  // follow-ups there's nothing to save — just close the view.
  document.getElementById('wf-detail-save-btn').classList.toggle('hidden', !wfState.detailFollowupSid);
  // Composer is gated on terminal status — can't ask follow-ups about a
  // run that's still mutating its trace mid-conversation.
  if (isLive) {
    wfDetailUpdateComposerEnabled(false, `Composer disabled while run is ${status}.`);
  } else {
    wfDetailUpdateComposerEnabled(true, '');
  }
  // Chat-style turn rendering
  const turnsEl = document.getElementById('wf-detail-turns');
  turnsEl.innerHTML = wfDetailRenderTurnsHtml(data) + wfDetailRenderFollowupsHtml();
  turnsEl.scrollTop = turnsEl.scrollHeight;
  // Live ask-user-for-file prompt
  const lastSteps = (data.steps || []).slice(-2);
  const askStep = lastSteps.find(s =>
    s.kind === 'call' && (s.detail || '').includes('ask_user_for_file('));
  const completedAsk = lastSteps.some(s =>
    s.kind === 'call_done' && (s.detail || '').includes('ask_user_for_file '));
  const promptEl = document.getElementById('wf-detail-prompt');
  if (askStep && !completedAsk && status === 'running') {
    if (promptEl.classList.contains('hidden')) {
      const { prompt, accept } = wfParseAskFileDetail(askStep.detail || '');
      wfRenderUploadPrompt(promptEl, prompt, accept);
      promptEl.classList.remove('hidden');
    }
  } else {
    promptEl.classList.add('hidden');
  }
}

function wfDetailRenderTurnsHtml(data) {
  // Render the workflow source as a collapsible "system" turn at top, then
  // each step as its own bubble. Tool calls + their result are paired; bare
  // setup steps and engine bookkeeping become smaller dim rows.
  const out = [];
  const src = (data.workflow_source || '').trim();
  if (src) {
    out.push(`
      <div class="wf-detail-turn wf-detail-turn-system">
        <div class="wf-detail-turn-label">Workflow source</div>
        <pre class="wf-detail-source"><code>${escapeHtml(src)}</code></pre>
      </div>
    `);
  }
  const steps = data.steps || (typeof data.steps_json === 'string' ? JSON.parse(data.steps_json || '[]') : []) || [];
  // Pair `call` with the matching `call_done` immediately following — engine
  // emits them adjacent for synchronous tools.
  let i = 0;
  while (i < steps.length) {
    const s = steps[i];
    const kind = s.kind || '';
    if (kind === 'call') {
      const next = steps[i + 1];
      const paired = next && next.kind === 'call_done' ? next : null;
      out.push(`
        <div class="wf-detail-turn wf-detail-turn-tool">
          <div class="wf-detail-turn-label">Tool call · L${s.line}</div>
          <div class="wf-detail-turn-call">${escapeHtml(s.detail || '')}</div>
          ${paired ? `<div class="wf-detail-turn-result">${escapeHtml(paired.detail || '(no output)')}</div>` : '<div class="wf-detail-turn-result wf-detail-pending">…</div>'}
        </div>
      `);
      i += paired ? 2 : 1;
      continue;
    }
    if (kind === 'call_done') {
      // Orphan call_done (call already consumed) — skip.
      i += 1;
      continue;
    }
    if (kind === 'error') {
      out.push(`
        <div class="wf-detail-turn wf-detail-turn-error">
          <div class="wf-detail-turn-label">Error · L${s.line}</div>
          <div class="wf-detail-turn-result">${escapeHtml(s.detail || '')}</div>
        </div>
      `);
      i += 1;
      continue;
    }
    // Other engine steps (set, return, branch, …) — small dim row.
    out.push(`
      <div class="wf-detail-turn wf-detail-turn-step">
        <span class="wf-detail-step-kind">${escapeHtml(kind)}</span>
        <span class="wf-detail-step-line">L${s.line}</span>
        <span class="wf-detail-step-detail">${escapeHtml(s.detail || '')}</span>
      </div>
    `);
    i += 1;
  }
  // Final return / error
  let rv = data.return_value;
  if (typeof rv === 'string' && rv) {
    try { rv = JSON.parse(rv); } catch (_) {}
  }
  if (rv !== null && rv !== undefined && rv !== '') {
    const txt = typeof rv === 'string' ? rv : JSON.stringify(rv, null, 2);
    out.push(`
      <div class="wf-detail-turn wf-detail-turn-final">
        <div class="wf-detail-turn-label">Returned</div>
        <pre class="wf-detail-final-value">${escapeHtml(txt)}</pre>
      </div>
    `);
  } else if (data.error) {
    out.push(`
      <div class="wf-detail-turn wf-detail-turn-error">
        <div class="wf-detail-turn-label">Failed</div>
        <pre class="wf-detail-final-value">${escapeHtml(data.error)}</pre>
      </div>
    `);
  }
  return out.join('');
}

function wfDetailRenderFollowupsHtml() {
  if (!wfState.detailFollowups.length) return '';
  const blocks = wfState.detailFollowups.map(f => {
    const cls = f.role === 'user' ? 'wf-detail-fup-user' : 'wf-detail-fup-assistant';
    const label = f.role === 'user' ? 'You' : 'Assistant';
    const streaming = f.streaming ? '<span class="wf-detail-cursor">▍</span>' : '';
    return `
      <div class="wf-detail-followup ${cls}">
        <div class="wf-detail-followup-label">${label}</div>
        <div class="wf-detail-followup-text">${escapeHtml(f.text || '')}${streaming}</div>
      </div>
    `;
  });
  return `<div class="wf-detail-followups-divider">Follow-up</div>` + blocks.join('');
}

function wfDetailUpdateComposerEnabled(enabled, statusMsg) {
  const ta = document.getElementById('wf-detail-input');
  const btn = document.getElementById('wf-detail-send-btn');
  const status = document.getElementById('wf-detail-composer-status');
  if (!ta || !btn) return;
  ta.disabled = !enabled || wfState.detailFollowupStreaming;
  btn.disabled = !enabled || wfState.detailFollowupStreaming;
  status.textContent = statusMsg || '';
  if (enabled) {
    ta.placeholder = 'Ask a follow-up question about this run…';
  } else {
    ta.placeholder = statusMsg || 'Composer disabled.';
  }
}

function wfDetailOnKeydown(e) {
  // Cmd/Ctrl+Enter → send. Plain Enter → newline (long follow-ups about
  // workflow runs often warrant multi-line questions).
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    wfDetailSend();
  }
}

async function wfDetailSend() {
  const ta = document.getElementById('wf-detail-input');
  const text = (ta && ta.value || '').trim();
  if (!text) return;
  if (wfState.detailFollowupStreaming) return;
  const execId = wfState.currentExecId;
  if (!execId) return;
  // Lazy-create the hidden chat session bound to this workflow run on the
  // first send. Subsequent sends reuse it (full conversation context).
  if (!wfState.detailFollowupSid) {
    try {
      // Use the active chat's model so the user gets the model they expect.
      // Fall back to the first available model when no chat is open yet.
      const model = (state.activeChat && state.activeChat.model)
        || (state.models.length ? (state.models[0].id || state.models[0]) : '');
      if (!model) {
        alert('No model available — open a chat first to pick one.');
        return;
      }
      const created = await API.createSession(WF_AGENT, model, '', 'workflow_run', execId);
      if (created && created.error) {
        alert('Could not create follow-up session: ' + created.error);
        return;
      }
      wfState.detailFollowupSid = created.session_id;
    } catch (e) {
      alert('Could not create follow-up session: ' + e.message);
      return;
    }
  }
  // Append the user turn + an assistant placeholder for the streaming reply.
  wfState.detailFollowups.push({ role: 'user', text });
  const aIdx = wfState.detailFollowups.length;
  wfState.detailFollowups.push({ role: 'assistant', text: '', streaming: true });
  ta.value = '';
  wfState.detailFollowupStreaming = true;
  wfDetailUpdateComposerEnabled(true, 'Sending…');
  wfRenderDetail(wfState.detailRun);
  const turnsEl = document.getElementById('wf-detail-turns');
  if (turnsEl) turnsEl.scrollTop = turnsEl.scrollHeight;
  try {
    await API.streamChat(wfState.detailFollowupSid, text, {
      text_delta: (d) => {
        const f = wfState.detailFollowups[aIdx];
        if (!f) return;
        f.text = (f.text || '') + (d.text || '');
        // Cheap incremental update: re-render only the followups block.
        const turns = document.getElementById('wf-detail-turns');
        if (turns) {
          // Find the last assistant followup div and patch its text node.
          const fups = turns.querySelectorAll('.wf-detail-fup-assistant .wf-detail-followup-text');
          const last = fups[fups.length - 1];
          if (last) {
            last.innerHTML = escapeHtml(f.text) + '<span class="wf-detail-cursor">▍</span>';
            turns.scrollTop = turns.scrollHeight;
          }
        }
      },
      done: () => {
        const f = wfState.detailFollowups[aIdx];
        if (f) f.streaming = false;
        wfState.detailFollowupStreaming = false;
        wfDetailUpdateComposerEnabled(true, '');
        // Save-to-chats button becomes meaningful now that a session exists.
        document.getElementById('wf-detail-save-btn').classList.remove('hidden');
        wfRenderDetail(wfState.detailRun);
      },
      error: (d) => {
        const f = wfState.detailFollowups[aIdx];
        if (f) {
          f.text = (f.text || '') + `\n[Error: ${d.message || 'unknown'}]`;
          f.streaming = false;
        }
        wfState.detailFollowupStreaming = false;
        wfDetailUpdateComposerEnabled(true, 'Error — try again');
        wfRenderDetail(wfState.detailRun);
      },
    });
  } catch (e) {
    const f = wfState.detailFollowups[aIdx];
    if (f) {
      f.text = (f.text || '') + `\n[Error: ${e.message}]`;
      f.streaming = false;
    }
    wfState.detailFollowupStreaming = false;
    wfDetailUpdateComposerEnabled(true, 'Error — try again');
    wfRenderDetail(wfState.detailRun);
  }
}

async function wfDetailSaveToChats() {
  const sid = wfState.detailFollowupSid;
  const execId = wfState.currentExecId;
  if (!sid || !execId) {
    alert('Send at least one follow-up before saving to chats.');
    return;
  }
  try {
    const r = await API.post(`/v1/workflows/history/${execId}/promote-session/${sid}`, {});
    if (r && r.error) {
      alert('Save failed: ' + r.error);
      return;
    }
    // Drop the user into the freshly-promoted chat. Clear local detail
    // state so re-opening this run starts a fresh follow-up session.
    const promotedSid = sid;
    wfState.detailFollowupSid = null;
    wfState.detailFollowups = [];
    wfState.detailFollowupStreaming = false;
    wfCloseDetail();
    if (typeof openSession === 'function') {
      openSession(promotedSid, WF_AGENT);
    }
  } catch (e) {
    alert('Save error: ' + e.message);
  }
}

function wfParseAskFileDetail(detail) {
  // Backend emits: ask_user_for_file(prompt='Upload meeting recording', accept='audio/*')
  // Match repr-style strings (single OR double quotes, with escaped chars).
  const out = { prompt: '', accept: '' };
  const re = /(\w+)=(['"])((?:\\.|(?!\2).)*)\2/g;
  let m;
  while ((m = re.exec(detail)) !== null) {
    if (m[1] in out) out[m[1]] = m[3].replace(/\\(.)/g, '$1');
  }
  return out;
}

function wfRenderUploadPrompt(root, prompt, accept) {
  const promptText = prompt || 'The workflow is waiting for a file.';
  const acceptHint = accept ? `Accepted: ${escapeHtml(accept)}` : 'Any file type accepted';
  const acceptAttr = accept ? `accept="${escapeHtml(accept)}"` : '';
  root.innerHTML = `
    <div class="wf-upload">
      <div class="wf-upload-header">
        <div class="wf-upload-icon">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        </div>
        <div class="wf-upload-text">
          <div class="wf-upload-title">${escapeHtml(promptText)}</div>
          <div class="wf-upload-hint">${acceptHint}</div>
        </div>
      </div>
      <label class="wf-upload-drop" id="wf-upload-drop">
        <input type="file" id="wf-upload-input" ${acceptAttr} onchange="wfOnFilePicked()" hidden />
        <div class="wf-upload-drop-inner">
          <div class="wf-upload-drop-cta">
            <span class="wf-upload-link">Choose a file</span>
            <span class="wf-upload-or">or drop it here</span>
          </div>
          <div class="wf-upload-filename" id="wf-upload-filename"></div>
        </div>
      </label>
      <div class="wf-upload-actions">
        <button class="wf-btn wf-btn-ghost" onclick="wfCancelUpload()">Cancel</button>
        <button class="wf-btn wf-btn-primary" id="wf-upload-submit" onclick="wfUploadFile()" disabled>Upload</button>
      </div>
    </div>`;
  // Drag-and-drop wiring
  const drop = document.getElementById('wf-upload-drop');
  const input = document.getElementById('wf-upload-input');
  if (drop && input) {
    ['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => {
      e.preventDefault(); e.stopPropagation(); drop.classList.add('wf-upload-drop-active');
    }));
    ['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {
      e.preventDefault(); e.stopPropagation(); drop.classList.remove('wf-upload-drop-active');
    }));
    drop.addEventListener('drop', e => {
      const files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length) {
        input.files = files;
        wfOnFilePicked();
      }
    });
  }
}

function wfOnFilePicked() {
  const input = document.getElementById('wf-upload-input');
  const nameEl = document.getElementById('wf-upload-filename');
  const submit = document.getElementById('wf-upload-submit');
  if (!input || !nameEl) return;
  const f = input.files && input.files[0];
  if (f) {
    const sizeKB = f.size / 1024;
    const sizeStr = sizeKB < 1024 ? sizeKB.toFixed(1) + ' KB' : (sizeKB / 1024).toFixed(2) + ' MB';
    nameEl.innerHTML = `<span class="wf-upload-filename-pill">${escapeHtml(f.name)} <span class="wf-upload-filename-size">· ${sizeStr}</span></span>`;
    if (submit) submit.disabled = false;
  } else {
    nameEl.innerHTML = '';
    if (submit) submit.disabled = true;
  }
}

function wfCancelUpload() {
  const input = document.getElementById('wf-upload-input');
  if (input) { input.value = ''; }
  wfOnFilePicked();
}

async function wfUploadFile() {
  const id = wfState.currentExecId;
  const input = document.getElementById('wf-upload-input');
  const submit = document.getElementById('wf-upload-submit');
  if (!id || !input || !input.files || !input.files[0]) return;
  const f = input.files[0];
  const fd = new FormData();
  fd.append('file', f);
  const authToken = localStorage.getItem('auth-token');
  const headers = {};
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  if (submit) {
    submit.disabled = true;
    submit.textContent = 'Uploading…';
  }
  try {
    const res = await fetch(`${BASE_URL}/v1/workflows/executions/${id}/upload-file`, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: fd,
    });
    const data = await res.json();
    if (data.error) {
      if (submit) { submit.disabled = false; submit.textContent = 'Upload'; }
      const nameEl = document.getElementById('wf-upload-filename');
      if (nameEl) nameEl.innerHTML = `<span class="wf-upload-error">${escapeHtml(data.error)}</span>`;
      return;
    }
    document.getElementById('wf-run-prompt').classList.add('hidden');
  } catch (e) {
    if (submit) { submit.disabled = false; submit.textContent = 'Upload'; }
    const nameEl = document.getElementById('wf-upload-filename');
    if (nameEl) nameEl.innerHTML = `<span class="wf-upload-error">${escapeHtml(e.message || 'Upload failed')}</span>`;
  }
}

/* ─── Helpers ─── */
function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function escapeJs(s) {
  return String(s).replace(/'/g, "\\'");
}

/* ─── Sample template ─── */
const WF_TEMPLATE_MEETING_NOTES = `# Meeting Notes — uploads a recording, transcribes it,
# extracts notes + action items, saves to a markdown file.

WORKFLOW "Meeting Notes"
DESCRIPTION "Transcribe a meeting recording and extract structured notes."
TRIGGER manual

SET upload = CALL ask_user_for_file prompt="Upload meeting recording (.wav / .mp3 / .m4a)" accept="audio/*"
SET transcript_result = CALL transcribe_audio file=upload.path
SET transcript = transcript_result.transcript

SET notes_result = CALL ask_llm prompt="Extract from this meeting transcript:\\n\\n1. A 2-3 sentence summary\\n2. Key decisions (bullet list)\\n3. Action items (bullet list, with owner if mentioned)\\n4. Open questions (bullet list)\\n\\nReturn well-formatted Markdown.\\n\\nTranscript:\\n\\n{{transcript}}"
SET notes = notes_result.text

SET filename = "/tmp/meeting_" + now("%Y-%m-%d_%H%M") + ".md"
SET body = "# Meeting Notes — " + now("%Y-%m-%d %H:%M") + "\\n\\n## Transcript\\n\\n" + transcript + "\\n\\n## Notes\\n\\n" + notes + "\\n"
CALL write_file path=filename content=body

RETURN filename
`;
