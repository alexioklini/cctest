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

/* ─── Workflow-run open: route through the regular chat view ───
   The detail view is no longer a separate surface — the chat view itself
   becomes the workflow-run view when the active chat is bound to a
   workflow_run_id. Run-specific UI lives in the workflow-run-banner that
   sits above #messages-container; the composer is the regular chat
   composer (so file-attach, thinking levels, model selection, etc. all
   work identically to a normal chat). */

const WF_TERMINAL_STATUSES = new Set(['completed', 'succeeded', 'failed', 'cancelled']);
const WF_LIVE_STATUSES = new Set(['running', 'pending', 'waiting_approval']);

// Shared state for the active workflow-run banner. Reset whenever the
// active chat is no longer workflow-bound. Polling timer fires while the
// run is live so the banner reflects fresh steps + terminal transitions.
const wfBanner = {
  execId: null,        // active workflow_run_id
  data: null,          // last fetched run data (live or persisted)
  pollTimer: null,
  traceCollapsed: true,// run-trace section default-collapsed
  filesCollapsed: false,
};

async function wfOpenDetail(executionId) {
  if (!executionId) return;
  // Resolve (or create) the chat session bound to this run for the current
  // user. This is the single source of truth — re-opening the same run
  // hands back the same session so the conversation survives across
  // workflow visits.
  let info;
  try {
    // Pick a sensible default model: prefer the active chat's model so
    // the user gets the model they've been using; fall back to the run's
    // own model field; the server has a final fallback to first-enabled.
    const model = (state.activeChat && state.activeChat.model) || '';
    info = await API.post(`/v1/workflows/history/${encodeURIComponent(executionId)}/session`,
                          model ? { model } : {});
    if (info && info.error) { alert('Open failed: ' + info.error); return; }
  } catch (e) {
    alert('Open failed: ' + e.message);
    return;
  }
  // Open the bound session in the regular chat view. The session-loading
  // pipeline restores workflow_run_id onto the chat object (see
  // sessions.js openSession), and the banner renderer keys off that.
  if (typeof openSession !== 'function') {
    alert('Internal error: openSession not available');
    return;
  }
  const agentId = WF_AGENT;
  navigateTo('chat');
  await openSession(info.session_id, agentId);
  // After openSession finishes, the banner should be primed. Fire the
  // initial render explicitly (covers the case where openSession ran
  // before workflow_run_id had reached the chat object).
  wfBannerSetExec(executionId);
}

function wfBannerSetExec(execId) {
  // Stop any prior polling — switching runs invalidates the timer.
  wfBannerStopPolling();
  wfBanner.execId = execId || null;
  wfBanner.data = null;
  if (!execId) {
    wfBannerHide();
    return;
  }
  wfBannerFetch(/*initial=*/true);
}

async function wfBannerFetch(initial) {
  const id = wfBanner.execId;
  if (!id) return;
  // Live endpoint first (fresh steps); fall back to history row.
  let data = null;
  try {
    const r = await API.get(`/v1/workflows/executions/${id}`);
    if (r && !r.error) data = r;
  } catch (_) {}
  if (!data) {
    try { data = await API.get(`/v1/workflows/history/${id}`); }
    catch (e) {
      const banner = document.getElementById('workflow-run-banner');
      if (banner) {
        banner.classList.remove('hidden');
        banner.innerHTML = `<div class="workflow-run-banner-error">Could not load run: ${escapeHtml(e.message)}</div>`;
      }
      return;
    }
  }
  wfBanner.data = data;
  renderWorkflowBanner();
  if (initial && data && WF_LIVE_STATUSES.has(data.status || '')) {
    wfBannerStartPolling();
  } else if (data && WF_TERMINAL_STATUSES.has(data.status || '')) {
    // Re-fetch the persisted /history row once so the banner shows the
    // full final trace + return value instead of the truncated live data.
    try {
      const persisted = await API.get(`/v1/workflows/history/${id}`);
      if (persisted && !persisted.error) {
        wfBanner.data = persisted;
        renderWorkflowBanner();
      }
    } catch (_) {}
  }
}

function wfBannerStartPolling() {
  wfBannerStopPolling();
  wfBanner.pollTimer = setInterval(() => wfBannerFetch(false), 800);
}

function wfBannerStopPolling() {
  if (wfBanner.pollTimer) {
    clearInterval(wfBanner.pollTimer);
    wfBanner.pollTimer = null;
  }
}

function wfBannerHide() {
  const banner = document.getElementById('workflow-run-banner');
  if (!banner) return;
  banner.classList.add('hidden');
  banner.innerHTML = '';
}

async function wfBannerCancel() {
  const id = wfBanner.execId;
  if (!id) return;
  try {
    const r = await API.post(`/v1/workflows/executions/${id}/cancel`, {});
    if (r && r.error) { alert('Cancel failed: ' + r.error); return; }
    wfBannerFetch(false);
  } catch (e) { alert('Cancel error: ' + e.message); }
}

async function wfBannerSaveToChats() {
  const id = wfBanner.execId;
  const chat = state.activeChat;
  const sid = chat && chat.sessionId;
  if (!id || !sid) return;
  // No "needs at least one follow-up" gate — the user may want to save
  // a run for later reference even before asking anything.
  try {
    const r = await API.post(`/v1/workflows/history/${id}/promote-session/${sid}`, {});
    if (r && r.error) { alert('Save failed: ' + r.error); return; }
    // Seed the references panel from the input files the workflow read.
    const refs = (r && r.references) || [];
    if (refs.length) {
      state.chatReferences = state.chatReferences || {};
      const existing = state.chatReferences[sid] || { cited: [], searched: [] };
      const seen = new Set(existing.searched.map(x => x.link));
      for (const ref of refs) {
        if (seen.has(ref.path)) continue;
        existing.searched.push({
          title: ref.name, link: ref.path, snippet: '',
          domain: 'workflow run', favicon: '', kind: 'file',
        });
      }
      state.chatReferences[sid] = existing;
    }
    // The chat is now visible in the sidebar (status flipped to active).
    // Banner stays — the chat is forever recognizable as workflow-derived.
    if (typeof loadSessions === 'function') loadSessions();
    // Re-render the banner so the Save button switches to "Saved".
    renderWorkflowBanner();
  } catch (e) { alert('Save error: ' + e.message); }
}

function wfBannerBack() {
  // Tear down banner state and route back to the workflows list.
  wfBannerStopPolling();
  wfBanner.execId = null;
  wfBanner.data = null;
  if (typeof navigateTo === 'function') navigateTo('workflows');
}

function wfBannerToggleTrace() {
  wfBanner.traceCollapsed = !wfBanner.traceCollapsed;
  renderWorkflowBanner();
}
function wfBannerToggleFiles() {
  wfBanner.filesCollapsed = !wfBanner.filesCollapsed;
  renderWorkflowBanner();
}

/* Lifecycle hook: chat.js calls maybeUpdateWorkflowBanner() whenever the
   active chat changes (open / close / new chat) so the banner appears or
   vanishes in step with state.activeChat. */
function maybeUpdateWorkflowBanner() {
  const chat = state.activeChat;
  const wfId = chat && chat.workflowRunId ? chat.workflowRunId : '';
  if (wfId && wfId !== wfBanner.execId) {
    wfBannerSetExec(wfId);
  } else if (!wfId && wfBanner.execId) {
    wfBannerSetExec(null);
  }
}

/* ─── Heuristic file-path extraction from steps_json ───
   Mirrors the regex used in admin.py:_workflow_run_paths so what the user
   sees as a clickable reference / artifact is exactly what the backend
   gate will allow them to download. Diverging the regexes silently
   would hand the user broken download buttons.

   Categories returned per file:
     • role: 'input'     — read by the workflow (read_*, transcribe_*,
                           ask_user_for_file uploads)
     • role: 'output'    — written by the workflow (write_file/edit_file
                           or surfaced as the RETURN value)
     • role: 'unknown'   — referenced via a generic path/file arg on a
                           tool we don't classify

   Source-of-evidence label is preserved so the UI can show "uploaded by
   you", "written by write_file", etc. */
const WF_PATH_ARG_RE = /\b(?:path|file|file_path|filename|audio|audio_path|image|image_path|pdf|pdf_path|src|source|input|output|target|dest|content_path)\s*=\s*(['"])((?:\\.|(?!\1).)+)\1/g;
const WF_QUOTED_PATH_RE = /(['"])((?:\/|~\/)[^'"\n]+)\1/g;
const WF_INPUT_TOOLS = new Set([
  'read_file', 'read_document', 'read_attachment',
  'transcribe_audio', 'parse_pdf', 'parse_docx',
  'ask_user_for_file',
]);
const WF_OUTPUT_TOOLS = new Set(['write_file', 'edit_file']);

function _wfUnescapeQuoted(s) {
  return s.replace(/\\(["'\\])/g, '$1');
}

function _wfParseToolCall(detail) {
  // Handles three shapes the engine emits:
  //   • "write_file(path=\"/tmp/foo.md\", content=...)"          (call line)
  //   • "ask_user_for_file → {'path': '/tmp/...'}"                (call_done line)
  //   • "transcribe_audio → {'transcript': ...}"                  (call_done line)
  // The arrow form is what carries the actual path on call_done since the
  // call line itself uses placeholder ellipses for elided args.
  if (!detail) return null;
  const paren = detail.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*\(/);
  if (paren) return paren[1];
  const arrow = detail.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*(?:→|->)/);
  return arrow ? arrow[1] : null;
}

function _wfExtractFilePathsFromDetail(detail) {
  const out = [];
  if (!detail) return out;
  WF_PATH_ARG_RE.lastIndex = 0;
  let m;
  while ((m = WF_PATH_ARG_RE.exec(detail)) !== null) {
    out.push({ path: _wfUnescapeQuoted(m[2]), via: 'arg' });
  }
  WF_QUOTED_PATH_RE.lastIndex = 0;
  while ((m = WF_QUOTED_PATH_RE.exec(detail)) !== null) {
    // Skip duplicates already captured by the arg regex.
    if (!out.some(p => p.path === m[2])) {
      out.push({ path: m[2], via: 'quoted' });
    }
  }
  return out;
}

function wfDetailExtractFiles(data) {
  // Returns { inputs: [refs...], outputs: [arts...], all: [...] }
  // where refs/arts share the shape:
  //   { path, filename, role, source, tool_name, ext }
  // `source` is a short user-facing label for where the path was seen.
  const steps = data.steps || (typeof data.steps_json === 'string' ? JSON.parse(data.steps_json || '[]') : []) || [];
  const seen = new Map(); // path -> entry
  const claim = (path, role, source, tool_name) => {
    if (!path) return;
    const trimmed = path.trim();
    if (!trimmed) return;
    const ext = trimmed.includes('.') ? trimmed.split('.').pop().toLowerCase() : '';
    const filename = trimmed.split('/').pop() || trimmed;
    const existing = seen.get(trimmed);
    if (existing) {
      // Promote: outputs win over inputs (a file written then re-read is
      // an output the user will care about); inputs win over unknown.
      const order = { unknown: 0, input: 1, output: 2 };
      if (order[role] > order[existing.role]) {
        existing.role = role; existing.source = source; existing.tool_name = tool_name;
      }
      return;
    }
    seen.set(trimmed, { path: trimmed, filename, role, source, tool_name, ext });
  };
  // Pair call/call_done so a result string can also contribute paths
  // (ask_user_for_file's return is "{'path': '/tmp/...'}" only on call_done).
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    const kind = s.kind || '';
    const detail = s.detail || '';
    const tool = _wfParseToolCall(detail);
    const role = tool && WF_OUTPUT_TOOLS.has(tool) ? 'output'
               : tool && WF_INPUT_TOOLS.has(tool) ? 'input'
               : 'unknown';
    const source = tool ? `${kind === 'call_done' ? 'returned by ' : 'used by '}${tool}` : kind;
    if (kind === 'call' || kind === 'call_done' || kind === 'error') {
      const paths = _wfExtractFilePathsFromDetail(detail);
      for (const p of paths) claim(p.path, role, source, tool || '');
    }
  }
  // RETURN value as a bare path
  let rv = data.return_value;
  if (typeof rv === 'string' && rv) {
    try { rv = JSON.parse(rv); } catch (_) {}
  }
  if (typeof rv === 'string' && (rv.startsWith('/') || rv.startsWith('~/'))) {
    claim(rv, 'output', 'return value', '');
  }
  const all = Array.from(seen.values()).sort((a, b) => a.filename.localeCompare(b.filename));
  return {
    inputs: all.filter(f => f.role === 'input'),
    outputs: all.filter(f => f.role === 'output'),
    other: all.filter(f => f.role === 'unknown'),
    all,
  };
}

/* ─── Files panel (refs + artifacts) + transcript download ─── */

function _wfFileIcon(ext) {
  const e = (ext || '').toLowerCase();
  if (['md','txt','log'].includes(e)) return '📝';
  if (['json','yaml','yml','toml','xml','csv'].includes(e)) return '🗂';
  if (['py','js','ts','tsx','jsx','go','rs','c','cpp','h','sh','rb','php'].includes(e)) return '💻';
  if (['png','jpg','jpeg','gif','svg','webp','bmp'].includes(e)) return '🖼';
  if (['wav','mp3','m4a','aac','ogg','flac'].includes(e)) return '🎵';
  if (['mp4','mov','avi','mkv'].includes(e)) return '🎞';
  if (['pdf'].includes(e)) return '📕';
  if (['docx','doc'].includes(e)) return '📘';
  if (['xlsx','xls'].includes(e)) return '📊';
  if (['pptx','ppt'].includes(e)) return '📽';
  if (['zip','tar','gz','7z','rar'].includes(e)) return '📦';
  return '📄';
}

function wfDetailRenderFilesHtml(data) {
  const groups = wfDetailExtractFiles(data);
  if (!groups.all.length) return '';
  const exec = wfState.currentExecId || '';
  const renderRow = (f) => {
    const icon = _wfFileIcon(f.ext);
    const safePath = encodeURIComponent(f.path);
    return `
      <div class="wf-detail-file-row" title="${escapeHtml(f.path)}">
        <span class="wf-detail-file-icon">${icon}</span>
        <div class="wf-detail-file-main">
          <div class="wf-detail-file-name">${escapeHtml(f.filename)}</div>
          <div class="wf-detail-file-source">${escapeHtml(f.source || '')}</div>
        </div>
        <div class="wf-detail-file-actions">
          <button class="wf-btn wf-btn-mini wf-btn-ghost" onclick="wfFilePreview('${escapeJs(exec)}','${escapeJs(f.path)}', event)" title="Preview">View</button>
          <a class="wf-btn wf-btn-mini wf-btn-ghost wf-detail-file-dl"
             href="${BASE_URL}/v1/workflows/history/${encodeURIComponent(exec)}/file?path=${safePath}"
             onclick="wfFileDownload('${escapeJs(exec)}','${escapeJs(f.path)}', event)"
             title="Download">↓</a>
        </div>
      </div>
    `;
  };
  const sections = [];
  if (groups.inputs.length) {
    sections.push(`
      <div class="wf-detail-files-section">
        <div class="wf-detail-files-section-label">References (${groups.inputs.length})</div>
        ${groups.inputs.map(renderRow).join('')}
      </div>
    `);
  }
  if (groups.outputs.length) {
    sections.push(`
      <div class="wf-detail-files-section">
        <div class="wf-detail-files-section-label">Artifacts (${groups.outputs.length})</div>
        ${groups.outputs.map(renderRow).join('')}
      </div>
    `);
  }
  if (groups.other.length) {
    sections.push(`
      <div class="wf-detail-files-section">
        <div class="wf-detail-files-section-label">Other files (${groups.other.length})</div>
        ${groups.other.map(renderRow).join('')}
      </div>
    `);
  }
  return `<details class="wf-detail-files-card" ${groups.outputs.length || groups.inputs.length ? 'open' : ''}>
    <summary>
      Files
      <span class="wf-detail-files-summary">
        ${groups.inputs.length ? `${groups.inputs.length} ref${groups.inputs.length === 1 ? '' : 's'}` : ''}
        ${groups.inputs.length && (groups.outputs.length || groups.other.length) ? ' · ' : ''}
        ${groups.outputs.length ? `${groups.outputs.length} artifact${groups.outputs.length === 1 ? '' : 's'}` : ''}
        ${(groups.inputs.length + groups.outputs.length) && groups.other.length ? ' · ' : ''}
        ${groups.other.length ? `${groups.other.length} other` : ''}
      </span>
    </summary>
    <div class="wf-detail-files-body">${sections.join('')}</div>
  </details>`;
}

async function wfFilePreview(execId, filePath, ev) {
  if (ev) ev.preventDefault();
  if (!execId || !filePath) return;
  // For images and audio we let the browser handle it via the download
  // endpoint with inline disposition (opens new tab). For text/document
  // we fetch the preview JSON and render in a modal.
  const ext = (filePath.split('.').pop() || '').toLowerCase();
  const dlUrl = `${BASE_URL}/v1/workflows/history/${encodeURIComponent(execId)}/file?path=${encodeURIComponent(filePath)}`;
  const inlineExts = new Set(['png','jpg','jpeg','gif','svg','webp','bmp','pdf','wav','mp3','m4a','ogg','flac']);
  if (inlineExts.has(ext)) {
    // Open inline via the existing download endpoint (server sets inline
    // disposition for these types). Use authenticated fetch + blob URL.
    try {
      const authToken = localStorage.getItem('auth-token');
      const headers = {};
      if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
      const resp = await fetch(dlUrl, { headers, credentials: 'include' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      // Revoke after a short delay so the new tab has time to load.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      alert('Preview failed: ' + e.message);
    }
    return;
  }
  // Text / document: fetch preview JSON.
  try {
    const data = await API.get(`/v1/workflows/history/${encodeURIComponent(execId)}/file-preview?path=${encodeURIComponent(filePath)}`);
    if (data && data.error) { alert('Preview error: ' + data.error); return; }
    wfShowFilePreviewModal(data, dlUrl);
  } catch (e) {
    alert('Preview failed: ' + e.message);
  }
}

async function wfFileDownload(execId, filePath, ev) {
  // Authenticated download — fetch as blob and trigger client-side save
  // so the Authorization header gets attached (a bare <a href> can't
  // carry the bearer token).
  if (ev) ev.preventDefault();
  if (!execId || !filePath) return;
  try {
    const authToken = localStorage.getItem('auth-token');
    const headers = {};
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    const url = `${BASE_URL}/v1/workflows/history/${encodeURIComponent(execId)}/file?path=${encodeURIComponent(filePath)}`;
    const resp = await fetch(url, { headers, credentials: 'include' });
    if (!resp.ok) {
      let errMsg = `HTTP ${resp.status}`;
      try { const j = await resp.json(); if (j.error) errMsg = j.error; } catch(_) {}
      alert('Download failed: ' + errMsg);
      return;
    }
    const blob = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objUrl;
    a.download = filePath.split('/').pop() || 'download';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(objUrl); }, 1000);
  } catch (e) {
    alert('Download failed: ' + e.message);
  }
}

function wfShowFilePreviewModal(data, dlUrl) {
  // Lightweight inline modal — reuses the wf-modal classes the editor uses.
  let modal = document.getElementById('wf-file-preview-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'wf-file-preview-modal';
    modal.className = 'wf-modal';
    modal.innerHTML = `
      <div class="wf-modal-backdrop" onclick="wfCloseFilePreview()"></div>
      <div class="wf-modal-content wf-file-preview-content">
        <div class="wf-modal-header">
          <h3 id="wf-file-preview-title">Preview</h3>
          <div class="wf-modal-actions">
            <a id="wf-file-preview-dl" class="wf-btn wf-btn-ghost" target="_blank">Download</a>
            <button class="wf-btn wf-btn-ghost" onclick="wfCloseFilePreview()">Close</button>
          </div>
        </div>
        <div id="wf-file-preview-body" class="wf-file-preview-body"></div>
      </div>`;
    document.body.appendChild(modal);
  }
  modal.classList.remove('hidden');
  document.getElementById('wf-file-preview-title').textContent = data.name || 'Preview';
  const dl = document.getElementById('wf-file-preview-dl');
  dl.href = dlUrl;
  dl.onclick = (ev) => { ev.preventDefault(); wfFileDownload(wfState.currentExecId, data.path, ev); };
  const body = document.getElementById('wf-file-preview-body');
  if (data.type === 'text' || data.type === 'document') {
    const trunc = data.truncated ? `\n\n…[truncated — download to see full content]` : '';
    body.innerHTML = `<pre class="wf-file-preview-pre">${escapeHtml((data.content || '') + trunc)}</pre>`;
  } else {
    body.innerHTML = `<div class="wf-file-preview-fallback">Binary preview not available — use Download.</div>`;
  }
}

function wfCloseFilePreview() {
  const modal = document.getElementById('wf-file-preview-modal');
  if (modal) modal.classList.add('hidden');
}

/* ─── Click-to-expand for long bubbles ─── */

const WF_COLLAPSE_THRESHOLD = 800; // chars; above this we collapse-by-default

function _wfMaybeCollapsed(text, opts) {
  const { tag = 'pre', cls = '', innerCls = '' } = opts || {};
  if (!text) return '';
  const escaped = escapeHtml(text);
  if (text.length <= WF_COLLAPSE_THRESHOLD) {
    return `<${tag} class="${cls}">${escaped}</${tag}>`;
  }
  // Collapsed view: show first ~12 lines or 800 chars (whichever shorter).
  let preview = text.slice(0, WF_COLLAPSE_THRESHOLD);
  const newlineCut = preview.split('\n').slice(0, 12).join('\n');
  if (newlineCut.length < preview.length) preview = newlineCut;
  return `
    <div class="wf-detail-collapsible" data-full="${encodeURIComponent(text)}">
      <${tag} class="${cls} wf-detail-collapsed-pre">${escapeHtml(preview)}<span class="wf-detail-ellipsis"> … (+${(text.length - preview.length).toLocaleString()} chars)</span></${tag}>
      <button type="button" class="wf-btn wf-btn-mini wf-btn-ghost wf-detail-expand-btn" onclick="wfDetailToggleExpand(this, '${tag}', '${cls.replace(/'/g, "\\'")}')">Show more</button>
    </div>
  `;
}

function wfDetailToggleExpand(btn, tag, cls) {
  const wrap = btn.closest('.wf-detail-collapsible');
  if (!wrap) return;
  const expanded = wrap.classList.toggle('wf-detail-expanded');
  if (expanded) {
    const full = decodeURIComponent(wrap.dataset.full || '');
    const pre = wrap.querySelector(tag || 'pre');
    if (pre) {
      pre.classList.remove('wf-detail-collapsed-pre');
      pre.classList.add('wf-detail-expanded-pre');
      pre.innerHTML = escapeHtml(full);
    }
    btn.textContent = 'Show less';
  } else {
    // Re-render collapsed
    const full = decodeURIComponent(wrap.dataset.full || '');
    let preview = full.slice(0, WF_COLLAPSE_THRESHOLD);
    const newlineCut = preview.split('\n').slice(0, 12).join('\n');
    if (newlineCut.length < preview.length) preview = newlineCut;
    const pre = wrap.querySelector(tag || 'pre');
    if (pre) {
      pre.classList.add('wf-detail-collapsed-pre');
      pre.classList.remove('wf-detail-expanded-pre');
      pre.innerHTML = escapeHtml(preview) +
        `<span class="wf-detail-ellipsis"> … (+${(full.length - preview.length).toLocaleString()} chars)</span>`;
    }
    btn.textContent = 'Show more';
  }
}

/* ─── Transcript download ─── */

function wfDetailDownloadTranscript() {
  const data = wfState.detailRun;
  if (!data) return;
  const lines = [];
  lines.push(`# Workflow run: ${data.workflow_name || ''}`);
  lines.push('');
  lines.push(`- Execution ID: \`${wfState.currentExecId}\``);
  lines.push(`- Status: ${data.status || 'unknown'}`);
  if (data.started_at) lines.push(`- Started: ${data.started_at}`);
  if (data.finished_at) lines.push(`- Finished: ${data.finished_at}`);
  if (data.duration_ms != null) lines.push(`- Duration: ${data.duration_ms}ms`);
  if (data.user_display) lines.push(`- User: ${data.user_display}`);
  if (data.trigger_kind) lines.push(`- Trigger: ${data.trigger_kind}`);
  if (data.cost_usd) lines.push(`- Cost: $${Number(data.cost_usd).toFixed(4)}`);
  lines.push('');
  if (data.workflow_source) {
    lines.push('## Workflow source');
    lines.push('');
    lines.push('```');
    lines.push(data.workflow_source);
    lines.push('```');
    lines.push('');
  }
  const steps = data.steps || (typeof data.steps_json === 'string' ? JSON.parse(data.steps_json || '[]') : []) || [];
  if (steps.length) {
    lines.push('## Trace');
    lines.push('');
    for (const s of steps) {
      const detail = s.detail || '';
      const tag = s.kind === 'call_done' ? '✓' : s.kind === 'error' ? '✗' : '·';
      lines.push(`${tag} **L${s.line} ${s.kind}**${detail ? ': ' + detail : ''}`);
      lines.push('');
    }
  }
  let rv = data.return_value;
  if (typeof rv === 'string' && rv) {
    try { rv = JSON.parse(rv); } catch (_) {}
  }
  if (rv != null && rv !== '') {
    lines.push('## Returned');
    lines.push('');
    lines.push('```');
    lines.push(typeof rv === 'string' ? rv : JSON.stringify(rv, null, 2));
    lines.push('```');
    lines.push('');
  }
  if (data.error) {
    lines.push('## Error');
    lines.push('');
    lines.push('```');
    lines.push(data.error);
    lines.push('```');
    lines.push('');
  }
  if (wfState.detailFollowups.length) {
    lines.push('## Follow-up conversation');
    lines.push('');
    for (const f of wfState.detailFollowups) {
      lines.push(`### ${f.role === 'user' ? 'User' : 'Assistant'}`);
      lines.push('');
      lines.push(f.text || '');
      lines.push('');
    }
  }
  const md = lines.join('\n');
  const blob = new Blob([md], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const safeName = (data.workflow_name || 'workflow').replace(/[^A-Za-z0-9_\-]+/g, '_');
  a.download = `${safeName}_${(wfState.currentExecId || '').slice(0, 8)}.md`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1000);
}

/* ─── Banner: header + files + collapsed run trace + actions ─── */

function _fmtTs(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch (_) { return iso; }
}

function _fmtDuration(ms) {
  if (!ms && ms !== 0) return '';
  if (ms < 1000) return ms + 'ms';
  const s = ms / 1000;
  if (s < 60) return s.toFixed(1) + 's';
  const m = Math.floor(s / 60);
  const rs = Math.round(s - m * 60);
  return `${m}m ${rs}s`;
}

function renderWorkflowBanner() {
  const banner = document.getElementById('workflow-run-banner');
  const chat = state.activeChat;
  if (!banner) return;
  const wfId = chat && chat.workflowRunId ? chat.workflowRunId : '';
  if (!wfId || wfId !== wfBanner.execId) {
    banner.classList.add('hidden');
    banner.innerHTML = '';
    return;
  }
  const data = wfBanner.data;
  if (!data) {
    banner.classList.remove('hidden');
    banner.innerHTML = `<div class="workflow-run-banner-loading">Loading workflow run…</div>`;
    return;
  }
  const status = data.status || 'unknown';
  const isLive = WF_LIVE_STATUSES.has(status);
  const dur = _fmtDuration(data.duration_ms);
  const cost = data.cost_usd ? '$' + Number(data.cost_usd).toFixed(4) : '';
  const started = _fmtTs(data.started_at);
  const finished = _fmtTs(data.finished_at);
  const agent = data.agent_id || '';
  const model = data.model || '—';

  const groups = wfDetailExtractFiles(data);
  const filesHtml = wfDetailRenderFilesHtml(data);

  // Trace: workflow source + tool-call/result pairs + return
  let traceHtml = '';
  const src = (data.workflow_source || '').trim();
  if (src) {
    traceHtml += `
      <div class="wf-banner-trace-card">
        <div class="wf-banner-trace-card-label">Workflow source</div>
        ${_wfMaybeCollapsed(src, { tag: 'pre', cls: 'wf-banner-source' })}
      </div>`;
  }
  const steps = data.steps || (typeof data.steps_json === 'string' ? JSON.parse(data.steps_json || '[]') : []) || [];
  let i = 0;
  while (i < steps.length) {
    const s = steps[i];
    const kind = s.kind || '';
    if (kind === 'call') {
      const next = steps[i + 1];
      const paired = next && next.kind === 'call_done' ? next : null;
      const callHtml = _wfMaybeCollapsed(s.detail || '', { tag: 'div', cls: 'wf-banner-trace-call' });
      const resultText = paired ? (paired.detail || '(no output)') : '';
      const resultHtml = paired
        ? _wfMaybeCollapsed(resultText, { tag: 'div', cls: 'wf-banner-trace-result' })
        : '<div class="wf-banner-trace-result wf-detail-pending">…</div>';
      traceHtml += `
        <div class="wf-banner-trace-card wf-banner-trace-tool">
          <div class="wf-banner-trace-card-label">Tool · L${s.line}</div>
          ${callHtml}
          ${resultHtml}
        </div>`;
      i += paired ? 2 : 1;
      continue;
    }
    if (kind === 'call_done') { i += 1; continue; }
    if (kind === 'error') {
      traceHtml += `
        <div class="wf-banner-trace-card wf-banner-trace-error">
          <div class="wf-banner-trace-card-label">Error · L${s.line}</div>
          ${_wfMaybeCollapsed(s.detail || '', { tag: 'div', cls: 'wf-banner-trace-result' })}
        </div>`;
      i += 1; continue;
    }
    traceHtml += `
      <div class="wf-banner-trace-step">
        <span class="wf-banner-step-kind">${escapeHtml(kind)}</span>
        <span class="wf-banner-step-line">L${s.line}</span>
        <span class="wf-banner-step-detail">${escapeHtml(s.detail || '')}</span>
      </div>`;
    i += 1;
  }
  let rv = data.return_value;
  if (typeof rv === 'string' && rv) { try { rv = JSON.parse(rv); } catch (_) {} }
  if (rv !== null && rv !== undefined && rv !== '') {
    const txt = typeof rv === 'string' ? rv : JSON.stringify(rv, null, 2);
    traceHtml += `
      <div class="wf-banner-trace-card wf-banner-trace-final">
        <div class="wf-banner-trace-card-label">Returned</div>
        ${_wfMaybeCollapsed(txt, { tag: 'pre', cls: 'wf-banner-final-value' })}
      </div>`;
  } else if (data.error) {
    traceHtml += `
      <div class="wf-banner-trace-card wf-banner-trace-error">
        <div class="wf-banner-trace-card-label">Failed</div>
        ${_wfMaybeCollapsed(data.error, { tag: 'pre', cls: 'wf-banner-final-value' })}
      </div>`;
  }

  // Save-to-chats label flips to "Saved" once the bound session is
  // active (status='active'), so the user gets a visual confirmation
  // and the button becomes a no-op rather than vanishing entirely.
  const sessionStatus = chat && chat.status; // not always populated
  const promoted = sessionStatus === 'active';

  banner.classList.remove('hidden');
  banner.innerHTML = `
    <div class="wf-banner-header">
      <button class="wf-btn wf-btn-ghost wf-banner-back" onclick="wfBannerBack()" title="Back to workflows">← Workflows</button>
      <div class="wf-banner-titlebar">
        <div class="wf-banner-title">
          <span class="wf-banner-workflow-name">${escapeHtml(data.workflow_name || 'Workflow')}</span>
          <span class="wf-status-${status} wf-banner-status">${escapeHtml(status)}</span>
        </div>
        <div class="wf-banner-meta">
          ${agent ? `<span class="wf-banner-meta-pill">agent: <strong>${escapeHtml(agent)}</strong></span>` : ''}
          <span class="wf-banner-meta-pill">model: <strong>${escapeHtml(model)}</strong></span>
          ${started ? `<span class="wf-banner-meta-pill">started: ${escapeHtml(started)}</span>` : ''}
          ${finished ? `<span class="wf-banner-meta-pill">finished: ${escapeHtml(finished)}</span>` : ''}
          ${dur ? `<span class="wf-banner-meta-pill">duration: ${escapeHtml(dur)}</span>` : ''}
          ${cost ? `<span class="wf-banner-meta-pill">cost: ${escapeHtml(cost)}</span>` : ''}
          ${data.user_display ? `<span class="wf-banner-meta-pill">by ${escapeHtml(data.user_display)}</span>` : ''}
          <span class="wf-banner-meta-pill wf-banner-execid" title="${escapeHtml(wfId)}">${escapeHtml(wfId.slice(0, 10))}</span>
        </div>
      </div>
      <div class="wf-banner-actions">
        ${isLive ? '<button class="wf-btn wf-btn-ghost" onclick="wfBannerCancel()">Cancel run</button>' : ''}
        <button class="wf-btn wf-btn-ghost" onclick="wfDetailDownloadTranscript()" title="Download a Markdown transcript of this run">Download transcript</button>
        ${promoted
          ? '<button class="wf-btn wf-btn-ghost" disabled title="This run is saved as a chat">✓ Saved</button>'
          : '<button class="wf-btn wf-btn-primary" onclick="wfBannerSaveToChats()">Save to chats</button>'}
      </div>
    </div>
    ${groups.all.length ? `<div class="wf-banner-files-wrap ${wfBanner.filesCollapsed ? 'wf-banner-section-collapsed' : ''}">
      ${filesHtml}
    </div>` : ''}
    <details class="wf-banner-trace-details" ${wfBanner.traceCollapsed ? '' : 'open'} ontoggle="wfBanner.traceCollapsed = !this.open">
      <summary>
        <span class="wf-banner-section-label">Run trace</span>
        <span class="wf-banner-section-summary">${steps.length} step${steps.length === 1 ? '' : 's'}${data.tool_calls ? ` · ${data.tool_calls} tool call${data.tool_calls === 1 ? '' : 's'}` : ''}${data.llm_calls ? ` · ${data.llm_calls} LLM call${data.llm_calls === 1 ? '' : 's'}` : ''}</span>
      </summary>
      <div class="wf-banner-trace-body">
        ${traceHtml || '<div class="wf-hist-empty">No steps recorded.</div>'}
      </div>
    </details>
    ${isLive ? '<div class="wf-banner-live-hint">⏵ Run is in progress — composer will accept follow-ups once it finishes.</div>' : ''}
  `;
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
