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
  currentExecId: null,  // run modal
  pollTimer: null,
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
          <button class="wf-btn wf-btn-ghost" onclick="wfToggleHistory('${escapeJs(wf.name)}')">History</button>
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
  if (!root.classList.contains('hidden')) {
    root.classList.add('hidden');
    return;
  }
  root.classList.remove('hidden');
  root.innerHTML = '<div class="wf-hist-loading">Loading history…</div>';
  try {
    const data = await API.get(`/v1/workflows/history?workflow=${encodeURIComponent(name)}&limit=10`);
    wfRenderHistoryRows(root, data.executions || []);
  } catch (e) {
    root.innerHTML = `<div class="wf-hist-error">Could not load history: ${escapeHtml(e.message)}</div>`;
  }
}

function wfRenderHistoryRows(root, rows) {
  if (!rows.length) {
    root.innerHTML = '<div class="wf-hist-empty">No runs yet.</div>';
    return;
  }
  root.innerHTML = `
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
      <td><button class="wf-btn wf-btn-ghost wf-btn-mini" onclick="wfShowHistoryDetail('${escapeJs(r.execution_id)}')">View</button></td>
    </tr>`;
}

async function wfShowHistoryDetail(executionId) {
  try {
    // Active executions live in-memory at /v1/workflows/executions/<id> with
    // fresh steps; the /history row only finalises on completion. Try the
    // live endpoint first; if the execution is still active, attach to it.
    let liveData = null;
    try {
      const r = await API.get(`/v1/workflows/executions/${executionId}`);
      if (r && !r.error) liveData = r;
    } catch (_) { /* not live (404) — fall through to history */ }
    if (liveData && (liveData.status === 'running' || liveData.status === 'pending' || liveData.status === 'waiting_approval')) {
      // Active run — reattach to it: live polling + working Cancel button.
      document.getElementById('wf-run-title').textContent = `Running: ${liveData.workflow_name || executionId}`;
      document.getElementById('wf-run-steps').innerHTML = '';
      document.getElementById('wf-run-prompt').classList.add('hidden');
      document.getElementById('wf-run-result').classList.add('hidden');
      document.getElementById('wf-run-cancel-btn').classList.remove('hidden');
      wfState.currentExecId = executionId;
      document.getElementById('wf-run-modal').classList.remove('hidden');
      wfStartPolling();
      return;
    }
    const data = await API.get(`/v1/workflows/history/${executionId}`);
    // Terminal status (completed / failed / cancelled) — read-only history view.
    document.getElementById('wf-run-title').textContent = `History: ${data.workflow_name} (${executionId})`;
    document.getElementById('wf-run-status').textContent = `Status: ${data.status}` +
      (data.error ? ` — ${data.error}` : '') +
      ` — ${data.user_display || 'unknown'} via ${data.trigger_kind || 'manual'} — ` +
      ((data.cost_usd || 0).toFixed ? '$' + Number(data.cost_usd || 0).toFixed(4) : '$0');
    document.getElementById('wf-run-status').className = 'wf-run-status wf-status-' + data.status;
    const stepsArr = data.steps || (typeof data.steps_json === 'string' ? JSON.parse(data.steps_json || '[]') : []);
    const stepsEl = document.getElementById('wf-run-steps');
    stepsEl.innerHTML = stepsArr.map(s => {
      const cls = (s.kind || '').includes('error') ? 'wf-step-err' : ((s.kind === 'call_done') ? 'wf-step-done' : '');
      return `<div class="wf-step ${cls}">
        <span class="wf-step-line">L${s.line}</span>
        <span class="wf-step-kind">${escapeHtml(s.kind || '')}</span>
        <span class="wf-step-detail">${escapeHtml(s.detail || '')}</span>
      </div>`;
    }).join('') || '<div class="wf-hist-empty">No steps recorded.</div>';
    const resultEl = document.getElementById('wf-run-result');
    let rv = data.return_value;
    if (typeof rv === 'string' && rv) {
      try { rv = JSON.parse(rv); } catch (_) {}
    }
    if (rv !== null && rv !== undefined && rv !== '') {
      resultEl.innerHTML = `<div class="wf-result-label">Returned:</div><pre class="wf-result-value">${escapeHtml(typeof rv === 'string' ? rv : JSON.stringify(rv, null, 2))}</pre>`;
      resultEl.classList.remove('hidden');
    } else if (data.error) {
      resultEl.innerHTML = `<div class="wf-result-label wf-error">Error</div><pre class="wf-result-value">${escapeHtml(data.error || '')}</pre>`;
      resultEl.classList.remove('hidden');
    } else {
      resultEl.classList.add('hidden');
    }
    document.getElementById('wf-run-prompt').classList.add('hidden');
    document.getElementById('wf-run-cancel-btn').classList.add('hidden');
    wfState.currentExecId = null;
    wfStopPolling();
    document.getElementById('wf-run-modal').classList.remove('hidden');
  } catch (e) {
    alert('Could not load history detail: ' + e.message);
  }
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
                <td><button class="wf-btn wf-btn-ghost wf-btn-mini" onclick="wfShowHistoryDetail('${escapeJs(r.execution_id)}')">View</button></td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    wrap.innerHTML = `<div class="wf-hist-error">Could not load: ${escapeHtml(e.message)}</div>`;
  }
}

/* ─── Editor ─── */
async function wfOpenEditor(name) {
  wfState.currentName = name;
  const modal = document.getElementById('wf-editor-modal');
  const nameInput = document.getElementById('wf-editor-name');
  const editor = document.getElementById('wf-editor');
  const status = document.getElementById('wf-editor-status');
  status.textContent = '';
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
    wfState.currentExecId = data.execution_id;
    wfOpenRunModal(name);
    wfStartPolling();
  } catch (e) {
    alert('Run error: ' + e.message);
  }
}

function wfOpenRunModal(name) {
  document.getElementById('wf-run-title').textContent = `Running: ${name}`;
  document.getElementById('wf-run-status').textContent = 'Status: starting…';
  document.getElementById('wf-run-steps').innerHTML = '';
  document.getElementById('wf-run-prompt').classList.add('hidden');
  document.getElementById('wf-run-result').classList.add('hidden');
  document.getElementById('wf-run-cancel-btn').classList.remove('hidden');
  document.getElementById('wf-run-modal').classList.remove('hidden');
}

function wfCloseRun() {
  wfStopPolling();
  document.getElementById('wf-run-modal').classList.add('hidden');
}

async function wfCancelRun() {
  if (!wfState.currentExecId) return;
  try {
    await API.post(`/v1/workflows/executions/${wfState.currentExecId}/cancel`, {});
  } catch (e) {}
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
    wfRenderRunState(data);
    if (['completed','failed','cancelled'].includes(data.status)) {
      wfStopPolling();
      document.getElementById('wf-run-cancel-btn').classList.add('hidden');
    }
  } catch (e) { /* swallow transient */ }
}

function wfRenderRunState(data) {
  const statusEl = document.getElementById('wf-run-status');
  statusEl.textContent = `Status: ${data.status}` + (data.error ? ` — ${data.error}` : '');
  statusEl.className = 'wf-run-status wf-status-' + data.status;

  const stepsEl = document.getElementById('wf-run-steps');
  stepsEl.innerHTML = (data.steps || []).map(s => {
    const cls = s.kind.includes('error') ? 'wf-step-err' : (s.kind === 'call_done' ? 'wf-step-done' : '');
    return `<div class="wf-step ${cls}">
      <span class="wf-step-line">L${s.line}</span>
      <span class="wf-step-kind">${escapeHtml(s.kind)}</span>
      <span class="wf-step-detail">${escapeHtml(s.detail || '')}</span>
    </div>`;
  }).join('');

  // Detect waiting-for-file: most recent step is `ask_user_for_file` call (not call_done)
  const lastSteps = (data.steps || []).slice(-2);
  const askStep = lastSteps.find(s =>
    s.kind === 'call' && (s.detail || '').includes('ask_user_for_file('));
  const completedAsk = lastSteps.some(s =>
    s.kind === 'call_done' && (s.detail || '').includes('ask_user_for_file '));
  const promptEl = document.getElementById('wf-run-prompt');
  if (askStep && !completedAsk && data.status === 'running') {
    if (promptEl.classList.contains('hidden')) {
      const { prompt, accept } = wfParseAskFileDetail(askStep.detail || '');
      wfRenderUploadPrompt(promptEl, prompt, accept);
      promptEl.classList.remove('hidden');
    }
  } else {
    promptEl.classList.add('hidden');
  }

  // Render result
  const resultEl = document.getElementById('wf-run-result');
  if (data.status === 'completed' && data.return_value !== null && data.return_value !== undefined) {
    resultEl.innerHTML = `<div class="wf-result-label">Returned:</div><pre class="wf-result-value">${escapeHtml(JSON.stringify(data.return_value, null, 2))}</pre>`;
    resultEl.classList.remove('hidden');
  } else if (data.status === 'failed') {
    resultEl.innerHTML = `<div class="wf-result-label wf-error">Error</div><pre class="wf-result-value">${escapeHtml(data.error || '')}</pre>`;
    resultEl.classList.remove('hidden');
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
