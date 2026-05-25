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
        <p>Noch keine Workflows.</p>
        <p class="wf-empty-hint">Klicken Sie auf <strong>Neuer Workflow</strong>, um einen zu erstellen. Workflows sind kleine Skripte, die Brain-Tools verketten — nützlich für wiederholbare, automatisierbare Aufgaben.</p>
      </div>`;
    return;
  }
  root.innerHTML = wfState.list.map(wf => `
    <div class="wf-card" data-wf-name="${escapeHtml(wf.name)}">
      <div class="wf-card-row">
        <div class="wf-card-main">
          <div class="wf-card-title">
            <span class="wf-card-fav-slot" data-wf-fav="${escapeHtml(wf.name)}"></span>
            ${escapeHtml(wf.display_name || wf.name)}
          </div>
          <div class="wf-card-desc">${escapeHtml(wf.description || '')}</div>
          <div class="wf-card-meta">
            <span class="wf-pill">${escapeHtml(wf.trigger || 'manuell')}</span>
            <span class="wf-card-fname">${escapeHtml(wf.file)}</span>
          </div>
        </div>
        <div class="wf-card-actions">
          <button class="wf-btn wf-btn-primary" onclick="wfRun('${escapeJs(wf.name)}')">Ausführen</button>
          <button class="wf-btn wf-btn-ghost" onclick="wfOpenEditor('${escapeJs(wf.name)}')">Bearbeiten</button>
          <button class="wf-btn wf-btn-ghost" onclick="shareDialog('workflow','${escapeJs(wf.name)}','${WF_AGENT}',{title:'${escapeJs(wf.display_name || wf.name)}',onChange:loadWorkflows})">Teilen</button>
          <button class="wf-btn wf-btn-ghost" data-wf-history-btn="${escapeHtml(wf.name)}"
                  onclick="wfToggleHistory('${escapeJs(wf.name)}')">Verlauf</button>
          <button class="wf-btn wf-btn-ghost" onclick="wfDelete('${escapeJs(wf.name)}')">Löschen</button>
        </div>
      </div>
      <div class="wf-card-history hidden" id="wf-hist-${escapeHtml(wf.name)}">
        <div class="wf-hist-loading">Verlauf wird geladen…</div>
      </div>
    </div>
  `).join('');
  // Mount a star button in each card's title slot.
  if (window.Favourites?.mount) {
    root.querySelectorAll('.wf-card-fav-slot').forEach(slot => {
      const name = slot.dataset.wfFav;
      if (!name) return;
      window.Favourites.mount(slot, {
        item_type: 'workflow',
        item_id: name,
        agent_id: WF_AGENT,
      });
    });
  }
}

async function wfToggleHistory(name) {
  const root = document.getElementById('wf-hist-' + name);
  if (!root) return;
  const btn = document.querySelector(`[data-wf-history-btn="${name}"]`);
  if (!root.classList.contains('hidden')) {
    root.classList.add('hidden');
    if (btn) btn.textContent = 'Verlauf';
    return;
  }
  root.classList.remove('hidden');
  if (btn) btn.textContent = 'Verlauf ausblenden';
  root.innerHTML = '<div class="wf-hist-loading">Verlauf wird geladen…</div>';
  try {
    const data = await API.get(`/v1/workflows/history?workflow=${encodeURIComponent(name)}&limit=10`);
    wfRenderHistoryRows(root, data.executions || []);
  } catch (e) {
    root.innerHTML = `<div class="wf-hist-error">Verlauf konnte nicht geladen werden: ${escapeHtml(e.message)}</div>`;
  }
}

function wfRenderHistoryRows(root, rows) {
  // Recover the workflow name from the container id so the Clear button knows
  // which workflow to scope to. Empty when this helper is reused elsewhere.
  const wfName = (root && root.id) ? root.id.replace(/^wf-hist-/, '') : '';
  if (!rows.length) {
    root.innerHTML = '<div class="wf-hist-empty">Noch keine Läufe.</div>';
    return;
  }
  // Toolbar appears whenever there's at least one terminal row to clear.
  const hasTerminal = rows.some(r => !['running','pending','waiting_approval'].includes(r.status));
  const clearBtn = (hasTerminal && wfName)
    ? `<button class="wf-btn wf-btn-ghost wf-btn-mini wf-btn-clear-hist" onclick="wfClearWorkflowHistory('${escapeJs(wfName)}')">Verlauf löschen</button>`
    : '';
  root.innerHTML = `
    ${clearBtn ? `<div class="wf-hist-toolbar">${clearBtn}</div>` : ''}
    <table class="wf-hist-table">
      <thead>
        <tr>
          <th>Wann</th>
          <th>Status</th>
          <th>Auslöser</th>
          <th>Benutzer</th>
          <th>Dauer</th>
          <th>LLM-Aufrufe</th>
          <th>Token (ein/aus)</th>
          <th>Kosten</th>
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
    ? `<button class="wf-btn wf-btn-mini wf-btn-cancel" onclick="wfCancelFromHistory('${escapeJs(r.execution_id)}', event)">Abbrechen</button>`
    : '';
  // Terminal rows (completed / failed / cancelled) get a Delete button. Live
  // rows don't — user must Cancel first; once cancelled the next render shows
  // Delete in place of Cancel.
  const deleteBtn = isCancelable
    ? ''
    : `<button class="wf-btn wf-btn-mini wf-btn-delete" title="Diesen Verlaufseintrag löschen" onclick="wfDeleteRunFromHistory('${escapeJs(r.execution_id)}', event)">Löschen</button>`;
  return `
    <div class="wf-hist-actions">
      <button class="wf-btn wf-btn-ghost wf-btn-mini" onclick="wfShowHistoryDetail('${escapeJs(r.execution_id)}')">Anzeigen</button>
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
  if (!await showConfirmDanger('Diesen Verlaufseintrag löschen? Dies kann nicht rückgängig gemacht werden.', 'Lauf löschen', 'Löschen')) return;
  try {
    const r = await API.del(`/v1/workflows/history/${executionId}`);
    if (r && r.error) {
      await showAlert('Löschen fehlgeschlagen: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    await showAlert('Löschfehler: ' + e.message);
  }
}

async function wfClearWorkflowHistory(name) {
  if (!await showConfirmDanger(`ALLE abgeschlossenen Läufe für "${name}" löschen? Dies kann nicht rückgängig gemacht werden. Laufende Einträge bleiben erhalten (zuerst abbrechen, um sie zu löschen).`, 'Verlauf löschen', 'Alle löschen')) return;
  try {
    const r = await API.del(`/v1/workflows/history?workflow=${encodeURIComponent(name)}`);
    if (r && r.error) {
      await showAlert('Löschen fehlgeschlagen: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    await showAlert('Löschfehler: ' + e.message);
  }
}

async function wfClearAllRuns() {
  const mine = document.getElementById('wf-runs-mine');
  const onlyMine = mine && mine.checked;
  const scope = onlyMine ? 'Ihre Läufe' : 'alle sichtbaren Läufe';
  if (!await showConfirmDanger(`${scope} löschen? Dies kann nicht rückgängig gemacht werden. Laufende Einträge bleiben erhalten (zuerst abbrechen, um sie zu löschen).`, 'Läufe löschen', 'Alle löschen')) return;
  try {
    const url = '/v1/workflows/history' + (onlyMine ? '?mine=1' : '');
    const r = await API.del(url);
    if (r && r.error) {
      await showAlert('Löschen fehlgeschlagen: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    await showAlert('Löschfehler: ' + e.message);
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
      <td>${escapeHtml(r.trigger_kind || 'manuell')}</td>
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
      await showAlert('Abbrechen fehlgeschlagen: ' + r.error);
      return;
    }
    _wfRefreshOpenHistoryTables();
  } catch (e) {
    await showAlert('Abbruchfehler: ' + e.message);
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
  wrap.innerHTML = '<div class="wf-hist-loading">Läufe werden geladen…</div>';
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
      wrap.innerHTML = '<div class="wf-hist-empty">Keine passenden Läufe.</div>';
      return;
    }
    wrap.innerHTML = `
      <table class="wf-hist-table wf-runs-table">
        <thead>
          <tr>
            <th>Wann</th><th>Workflow</th><th>Status</th><th>Auslöser</th>
            <th>Benutzer</th><th>Dauer</th><th>LLM-Aufrufe</th><th>Token</th>
            <th>Kosten</th><th></th>
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
                <td>${escapeHtml(r.trigger_kind || 'manuell')}</td>
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
    wrap.innerHTML = `<div class="wf-hist-error">Konnte nicht geladen werden: ${escapeHtml(e.message)}</div>`;
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
  wfACClose();
}

async function wfSaveCurrent() {
  const nameInput = document.getElementById('wf-editor-name');
  const editor = document.getElementById('wf-editor');
  const status = document.getElementById('wf-editor-status');
  const name = (nameInput.value || '').trim();
  const source = editor.value;
  if (!name) {
    status.textContent = 'Name ist erforderlich.';
    status.className = 'wf-editor-status wf-error';
    return;
  }
  status.textContent = 'Wird gespeichert…';
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
    status.textContent = 'Gespeichert.';
    status.className = 'wf-editor-status wf-ok';
    loadWorkflows();
  } catch (e) {
    status.textContent = 'Speichern fehlgeschlagen: ' + e.message;
    status.className = 'wf-editor-status wf-error';
  }
}

async function wfDelete(name) {
  if (!await showConfirmDanger(`Workflow "${name}" löschen?`, 'Workflow löschen', 'Löschen')) return;
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
  // Trigger context-aware autocomplete after every input change. This is
  // safe to call every keystroke — wfACMaybeOpen exits cheaply when there
  // is no token under the caret.
  wfACMaybeOpen(false);
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
  // Reposition the popover / ghost so they track caret on scroll.
  if (wfAC.open) wfACRenderPopover();
  if (wfAC.ghost) wfACRenderGhost();
}

function wfOnEditorBlur() {
  // Defer a tick so a click on a popover item can fire first.
  setTimeout(() => {
    if (document.activeElement && document.activeElement.id === 'wf-editor') return;
    wfACClose();
  }, 120);
}

function wfOnKeydown(e) {
  // Autocomplete popover takes priority on its keys.
  if (wfAC.open) {
    if (e.key === 'ArrowDown') { e.preventDefault(); wfACMove(1); return; }
    if (e.key === 'ArrowUp')   { e.preventDefault(); wfACMove(-1); return; }
    if (e.key === 'Escape')    { e.preventDefault(); wfACClose(); return; }
    if (e.key === 'Tab' || e.key === 'Enter') {
      // Tab always accepts; Enter only when there's a real selection (let
      // newline through if the popover is open with no items).
      if (wfAC.items.length > 0) {
        e.preventDefault();
        wfACAccept();
        return;
      }
    }
  }
  // Single-suggestion ghost text path: Tab accepts the only candidate
  // even when the popover is suppressed (matches composer "ghost" UX).
  if (e.key === 'Tab' && !e.shiftKey) {
    const ghost = wfACSingleCandidate();
    if (ghost) {
      e.preventDefault();
      wfACAcceptGhost(ghost);
      return;
    }
  }
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

/* ─── Inline autocomplete (tools / params / variables / keywords) ─── */

const WF_AC_MAX = 12;

const wfAC = {
  open: false,
  items: [],          // [{label, kind, insert, detail}]
  index: 0,
  prefix: '',         // the token currently being typed
  prefixStart: 0,     // offset in textarea.value where prefix begins
  context: null,      // 'tool' | 'arg' | 'field' | 'general' | 'interp'
  ghost: null,        // {label, insert} when single-candidate ghost is showing
};

function wfACEditor() { return document.getElementById('wf-editor'); }

// Walk the source up to caret and collect SET <name> = CALL <tool> assignments.
// Returns Map: varName → {tool, primary_field}. Best-effort, regex-based.
function wfCollectVars(srcUpToCaret) {
  const out = new Map();
  const re = /^[ \t]*SET[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(?:CALL[ \t]+([A-Za-z_][A-Za-z0-9_]*))?/gmi;
  let m;
  while ((m = re.exec(srcUpToCaret)) !== null) {
    const name = m[1];
    const tool = m[2] || null;
    let primary = '';
    if (tool) {
      const t = wfState.tools.find(x => x.name === tool);
      if (t) primary = t.primary_field || '';
    }
    out.set(name, { tool, primary_field: primary });
  }
  // Also pick up FOR EACH <name> IN ... loop variables (best effort).
  const reFor = /^[ \t]*FOR[ \t]+EACH[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]+IN\b/gmi;
  while ((m = reFor.exec(srcUpToCaret)) !== null) {
    if (!out.has(m[1])) out.set(m[1], { tool: null, primary_field: '' });
  }
  return out;
}

// Determine caret context: what kind of completion makes sense here.
// Returns { context, prefix, prefixStart, callTool? } or null.
function wfACContext(value, caret) {
  // Find the current logical line (caret-bearing).
  const lineStart = value.lastIndexOf('\n', caret - 1) + 1;
  const lineEnd = (() => {
    const i = value.indexOf('\n', caret);
    return i < 0 ? value.length : i;
  })();
  const before = value.slice(lineStart, caret);
  const fullLine = value.slice(lineStart, lineEnd);

  // Inside a string? Detect by counting unescaped quotes before caret on
  // the current line. Inside a string, only {{...}} interpolation completes.
  const inString = (() => {
    let i = 0, q = null;
    while (i < before.length) {
      const c = before[i];
      if (q) {
        if (c === '\\') { i += 2; continue; }
        if (c === q) { q = null; i++; continue; }
        i++; continue;
      }
      if (c === '"' || c === "'") { q = c; i++; continue; }
      if (c === '#') return false; // comment kills string scanning
      i++;
    }
    return q;
  })();

  if (inString) {
    // Look for an open '{{' without a matching '}}' before caret.
    const openIdx = before.lastIndexOf('{{');
    const closeIdx = before.lastIndexOf('}}');
    if (openIdx < 0 || openIdx <= closeIdx) return null;
    // Token = chars after '{{' that are identifier-ish.
    const inside = before.slice(openIdx + 2);
    const m = inside.match(/([A-Za-z_][A-Za-z0-9_.]*)$/);
    const prefix = m ? m[1] : '';
    return {
      context: 'interp',
      prefix,
      prefixStart: caret - prefix.length,
    };
  }

  // Identifier under caret (the prefix being typed).
  const tokMatch = before.match(/([A-Za-z_][A-Za-z0-9_]*)$/);
  const prefix = tokMatch ? tokMatch[1] : '';
  const prefixStart = caret - prefix.length;

  // Field access: <ident>.<prefix>
  // Look for "<word>." immediately before the prefix.
  const beforeToken = before.slice(0, before.length - prefix.length);
  const dotMatch = beforeToken.match(/([A-Za-z_][A-Za-z0-9_]*)\.\s*$/);
  if (dotMatch) {
    return {
      context: 'field',
      varName: dotMatch[1],
      prefix,
      prefixStart,
    };
  }

  // Find a CALL <tool> earlier on the line *before* the current prefix —
  // i.e. the prefix is NOT itself the tool name being typed.
  const beforePrefix = before.slice(0, before.length - prefix.length);
  const callPriorMatch = beforePrefix.match(/\bCALL[ \t]+([A-Za-z_][A-Za-z0-9_]*)\b([\s\S]*)$/);
  if (callPriorMatch) {
    const tail = callPriorMatch[2];  // text between the tool name and the prefix
    const lastEqual = tail.lastIndexOf('=');
    const lastSpace = Math.max(tail.lastIndexOf(' '), tail.lastIndexOf('\t'));
    // We're on an arg name slot when the most recent token-separator on
    // the tail is whitespace (i.e. not directly after `=`-value).
    const argLike = tail.trim() === '' || lastSpace > lastEqual;
    if (argLike) {
      return {
        context: 'arg',
        callTool: callPriorMatch[1],
        prefix,
        prefixStart,
      };
    }
  }

  // After `CALL ` (no tool name yet, or tool name being typed):
  // tool completion. Use beforePrefix so we don't see our own prefix as a
  // pre-existing tool name.
  if (/\bCALL[ \t]+$/.test(beforePrefix)) {
    return {
      context: 'tool',
      prefix,
      prefixStart,
    };
  }

  // General context: keywords, variables, builtins, tool names (low prio).
  // Only pop up if the user has typed at least one character — avoids
  // spamming the popover on every space. Empty-prefix completions are
  // available via Ctrl-Space (handled in wfACMaybeOpen).
  if (!prefix) return null;
  return {
    context: 'general',
    prefix,
    prefixStart,
  };
}

function wfACBuildItems(ctx, value, caret) {
  const p = (ctx.prefix || '').toLowerCase();
  const startsOrSubstr = (s) => {
    const sl = s.toLowerCase();
    if (!p) return true;
    if (sl.startsWith(p)) return 2;
    if (sl.includes(p)) return 1;
    return 0;
  };
  const collected = [];
  const push = (label, kind, insert, detail) => {
    const score = startsOrSubstr(label);
    if (!score) return;
    collected.push({ label, kind, insert: insert ?? label, detail: detail || '', score });
  };

  if (ctx.context === 'tool') {
    for (const t of wfState.tools) {
      push(t.name, 'tool', t.name, t.description || '');
    }
  } else if (ctx.context === 'arg') {
    const tool = wfState.tools.find(t => t.name === ctx.callTool);
    if (tool) {
      for (const a of tool.args) {
        push(a.name, a.required ? 'arg-required' : 'arg', a.name + '=""', a.description || a.type || '');
      }
    }
  } else if (ctx.context === 'field') {
    const vars = wfCollectVars(value.slice(0, caret));
    const v = vars.get(ctx.varName);
    if (v && v.primary_field) {
      push(v.primary_field, 'field', v.primary_field, 'primary field');
    }
    // Generic suggestions that show up across tool results we know about:
    const generic = ['text','content','data','result','path','status','error','transcript','json'];
    for (const g of generic) {
      if (v && v.primary_field === g) continue;
      push(g, 'field', g, '');
    }
  } else if (ctx.context === 'interp') {
    const vars = wfCollectVars(value.slice(0, caret));
    for (const [name, info] of vars) {
      const detail = info.tool ? `from CALL ${info.tool}` : 'variable';
      push(name, 'var', name, detail);
    }
    for (const b of WF_BUILTINS) push(b, 'builtin', b + '()', 'builtin');
  } else if (ctx.context === 'general') {
    // Keywords (uppercase to match DSL convention).
    for (const k of WF_KEYWORDS) push(k, 'keyword', k, '');
    // Variables in scope.
    const vars = wfCollectVars(value.slice(0, caret));
    for (const [name, info] of vars) {
      const detail = info.tool ? `from CALL ${info.tool}` : 'variable';
      push(name, 'var', name, detail);
    }
    // Builtins (lowercase).
    for (const b of WF_BUILTINS) push(b, 'builtin', b + '(', 'builtin');
    // Tool names — lower priority, so user typing "wri" still sees write_file.
    for (const t of wfState.tools) push(t.name, 'tool', t.name, t.description || '');
  }

  // Rank: prefix-match (2) before substring (1), then alpha.
  collected.sort((a, b) => (b.score - a.score) || a.label.localeCompare(b.label));
  // Dedupe by label (first wins — preserves rank).
  const seen = new Set();
  const out = [];
  for (const it of collected) {
    if (seen.has(it.label)) continue;
    seen.add(it.label);
    out.push(it);
    if (out.length >= WF_AC_MAX) break;
  }
  return out;
}

function wfACMaybeOpen(force = false) {
  const ed = wfACEditor();
  if (!ed) return;
  const caret = ed.selectionStart;
  if (caret !== ed.selectionEnd) { wfACClose(); return; }
  const ctx = wfACContext(ed.value, caret);
  if (!ctx) { wfACClose(); return; }
  const items = wfACBuildItems(ctx, ed.value, caret);
  if (!items.length) { wfACClose(); return; }
  // Single-candidate ghost path: don't open a popover; show ghost text and
  // let Tab accept. We set wfAC.ghost so wfOnKeydown can pick it up.
  if (items.length === 1 && !force) {
    wfAC.ghost = {
      label: items[0].label,
      insert: items[0].insert,
      prefix: ctx.prefix,
      prefixStart: ctx.prefixStart,
    };
    wfACRenderGhost();
    wfACHidePopover();
    wfAC.open = false;
    wfAC.items = [items[0]]; // remember for any consumer that asks
    return;
  }
  wfAC.ghost = null;
  wfACClearGhost();
  wfAC.open = true;
  wfAC.items = items;
  wfAC.prefix = ctx.prefix;
  wfAC.prefixStart = ctx.prefixStart;
  wfAC.context = ctx.context;
  wfAC.index = 0;
  wfACRenderPopover();
}

function wfACSingleCandidate() {
  // Returns the ghost candidate iff there's exactly one and it's showing.
  return wfAC.ghost;
}

function wfACAcceptGhost(ghost) {
  const ed = wfACEditor();
  if (!ed) return;
  const caret = ed.selectionStart;
  const before = ed.value.slice(0, ghost.prefixStart);
  const after = ed.value.slice(caret);
  const inserted = ghost.insert;
  ed.value = before + inserted + after;
  // Caret position: if the insert ends with `=""` put caret between quotes;
  // if it ends with `(` put caret right after; otherwise at end of insert.
  let newCaret = before.length + inserted.length;
  if (inserted.endsWith('=""')) newCaret = before.length + inserted.length - 1;
  ed.selectionStart = ed.selectionEnd = newCaret;
  wfAC.ghost = null;
  wfACClearGhost();
  wfOnInput();
  ed.focus();
}

function wfACAccept() {
  const ed = wfACEditor();
  if (!ed) return;
  const it = wfAC.items[wfAC.index];
  if (!it) { wfACClose(); return; }
  const caret = ed.selectionStart;
  const before = ed.value.slice(0, wfAC.prefixStart);
  const after = ed.value.slice(caret);
  const inserted = it.insert;
  ed.value = before + inserted + after;
  let newCaret = before.length + inserted.length;
  if (inserted.endsWith('=""')) newCaret = before.length + inserted.length - 1;
  ed.selectionStart = ed.selectionEnd = newCaret;
  wfACClose();
  wfOnInput();
  ed.focus();
}

function wfACMove(delta) {
  if (!wfAC.open || !wfAC.items.length) return;
  const n = wfAC.items.length;
  wfAC.index = (wfAC.index + delta + n) % n;
  wfACRenderPopover();
}

function wfACClose() {
  wfAC.open = false;
  wfAC.items = [];
  wfAC.ghost = null;
  wfACHidePopover();
  wfACClearGhost();
}

/* DOM helpers — popover + ghost overlay. */
function wfACEnsurePopover() {
  let pop = document.getElementById('wf-ac-popover');
  if (pop) return pop;
  pop = document.createElement('div');
  pop.id = 'wf-ac-popover';
  pop.className = 'wf-ac-popover hidden';
  pop.addEventListener('mousedown', (e) => {
    // Clicks on items shouldn't blur the textarea.
    e.preventDefault();
  });
  document.body.appendChild(pop);
  return pop;
}

function wfACHidePopover() {
  const pop = document.getElementById('wf-ac-popover');
  if (pop) pop.classList.add('hidden');
}

function wfACRenderPopover() {
  const ed = wfACEditor();
  const pop = wfACEnsurePopover();
  if (!ed) return;
  const pos = wfACCaretPos(ed, wfAC.prefixStart);
  pop.innerHTML = wfAC.items.map((it, i) => `
    <div class="wf-ac-item ${i === wfAC.index ? 'sel' : ''}" data-i="${i}">
      <span class="wf-ac-kind wf-ac-kind-${escapeHtml(it.kind)}">${escapeHtml(wfACKindLabel(it.kind))}</span>
      <span class="wf-ac-label">${escapeHtml(it.label)}</span>
      <span class="wf-ac-detail">${escapeHtml(it.detail || '')}</span>
    </div>
  `).join('');
  pop.querySelectorAll('.wf-ac-item').forEach(el => {
    el.addEventListener('click', () => {
      wfAC.index = parseInt(el.dataset.i, 10) || 0;
      wfACAccept();
    });
  });
  pop.classList.remove('hidden');
  pop.style.left = pos.left + 'px';
  pop.style.top  = pos.top + 'px';
}

function wfACKindLabel(k) {
  return ({
    'tool': 'tool',
    'arg': 'arg',
    'arg-required': 'arg*',
    'field': 'field',
    'var': 'var',
    'keyword': 'kw',
    'builtin': 'fn',
  })[k] || k;
}

/* Compute pixel position of a character offset inside the textarea by
   mirroring its content into a hidden div with identical font + padding,
   inserting a marker span at the offset, and reading its bounding box. */
function wfACCaretPos(ta, offset) {
  let mirror = document.getElementById('wf-ac-mirror');
  if (!mirror) {
    mirror = document.createElement('div');
    mirror.id = 'wf-ac-mirror';
    mirror.className = 'wf-ac-mirror';
    document.body.appendChild(mirror);
  }
  const cs = window.getComputedStyle(ta);
  const props = ['fontFamily','fontSize','fontWeight','fontStyle','letterSpacing',
                 'lineHeight','padding','border','tabSize','whiteSpace','wordWrap','wordBreak'];
  for (const p of props) mirror.style[p] = cs[p];
  mirror.style.width = ta.clientWidth + 'px';
  mirror.style.height = 'auto';
  mirror.style.position = 'fixed';
  mirror.style.visibility = 'hidden';
  mirror.style.left = '0px';
  mirror.style.top  = '0px';
  // Match textarea's whitespace handling.
  mirror.style.whiteSpace = 'pre';
  mirror.style.overflow = 'hidden';
  const before = ta.value.slice(0, offset);
  const after = ta.value.slice(offset);
  mirror.textContent = '';
  mirror.appendChild(document.createTextNode(before));
  const marker = document.createElement('span');
  marker.textContent = '​';
  mirror.appendChild(marker);
  mirror.appendChild(document.createTextNode(after.length ? after : ' '));
  // Now we need the marker's offsetLeft/offsetTop relative to the textarea,
  // accounting for the textarea's scroll position and its absolute viewport pos.
  const taRect = ta.getBoundingClientRect();
  const mRect = marker.getBoundingClientRect();
  const mirrorRect = mirror.getBoundingClientRect();
  // Position relative to the page, adjusted by textarea scroll.
  const left = taRect.left + (mRect.left - mirrorRect.left) - ta.scrollLeft + window.scrollX;
  const top  = taRect.top  + (mRect.top  - mirrorRect.top)  - ta.scrollTop  + window.scrollY
             + parseFloat(cs.lineHeight || '18');
  return { left, top };
}

/* Ghost-text rendering: paints a faded suffix into the highlight overlay so
   the single remaining candidate is visible inline. We rebuild the overlay
   HTML through wfHighlight() and append a ghost span at the caret offset. */
function wfACRenderGhost() {
  const ed = wfACEditor();
  const overlay = document.getElementById('wf-highlight');
  if (!ed || !overlay || !wfAC.ghost) return;
  // We avoid touching the highlight's structured HTML. Instead, place a
  // floating ghost element absolutely positioned at the caret.
  let g = document.getElementById('wf-ac-ghost');
  if (!g) {
    g = document.createElement('span');
    g.id = 'wf-ac-ghost';
    g.className = 'wf-ac-ghost';
    document.body.appendChild(g);
  }
  const caret = ed.selectionStart;
  const pos = wfACCaretPos(ed, caret);
  // The ghost shows the *unconsumed* tail of the candidate.
  const prefix = wfAC.ghost.prefix || '';
  const cand = wfAC.ghost.label;
  let tail = '';
  if (cand.toLowerCase().startsWith(prefix.toLowerCase())) {
    tail = cand.slice(prefix.length);
  } else {
    tail = ' → ' + cand; // substring match: show full on the side
  }
  g.textContent = tail + '  ⇥';
  g.style.left = pos.left + 'px';
  g.style.top  = (pos.top - parseFloat(window.getComputedStyle(ed).lineHeight || '18')) + 'px';
  g.classList.remove('hidden');
}

function wfACClearGhost() {
  const g = document.getElementById('wf-ac-ghost');
  if (g) g.classList.add('hidden');
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

/* (wfShowToolPalette / wfInsertToolCall removed — completion is now inline
   in the editor via wfACMaybeOpen + Ctrl-Space.) */

/* ─── Run ─── */
async function wfRun(name) {
  try {
    const data = await API.post(`/v1/agents/${WF_AGENT}/workflows/${encodeURIComponent(name)}/run`, { variables: {} });
    if (data.error) {
      await showAlert('Ausführung fehlgeschlagen: ' + data.error);
      return;
    }
    // Drop straight into the inline detail view; live polling kicks in.
    wfOpenDetail(data.execution_id);
  } catch (e) {
    await showAlert('Ausführungsfehler: ' + e.message);
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
};

async function wfOpenDetail(executionId) {
  if (!executionId) return;
  // Resolve (or create) the chat session bound to this run for the current
  // user. This is the single source of truth — re-opening the same run
  // hands back the same session so the conversation survives across
  // workflow visits. Also seeds artifact rows for outputs (server-side)
  // and returns input paths (we seed chatReferences locally below).
  let info;
  try {
    const model = (state.activeChat && state.activeChat.model) || '';
    info = await API.post(`/v1/workflows/history/${encodeURIComponent(executionId)}/session`,
                          model ? { model } : {});
    if (info && info.error) { await showAlert('Öffnen fehlgeschlagen: ' + info.error); return; }
  } catch (e) {
    await showAlert('Öffnen fehlgeschlagen: ' + e.message);
    return;
  }
  // Seed the References panel from the workflow's input files so the
  // regular tab populates immediately on chat open. This must happen
  // BEFORE openSession() so the renderer picks the cache up on first
  // render — otherwise the user sees an empty Refs panel for a beat.
  const refs = (info && info.references) || [];
  if (refs.length) {
    state.chatReferences = state.chatReferences || {};
    const existing = state.chatReferences[info.session_id] || { cited: [], searched: [] };
    const seen = new Set(existing.searched.map(x => x.link));
    for (const ref of refs) {
      if (seen.has(ref.path)) continue;
      existing.searched.push({
        title: ref.name,
        link: ref.path,
        snippet: '',
        domain: 'Workflow-Lauf',
        favicon: '',
        kind: 'file',
      });
    }
    state.chatReferences[info.session_id] = existing;
  }
  if (typeof openSession !== 'function') {
    await showAlert('Interner Fehler: openSession nicht verfügbar');
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
        banner.innerHTML = `<div class="workflow-run-banner-error">Lauf konnte nicht geladen werden: ${escapeHtml(e.message)}</div>`;
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
    if (r && r.error) { await showAlert('Abbrechen fehlgeschlagen: ' + r.error); return; }
    wfBannerFetch(false);
  } catch (e) { await showAlert('Abbruchfehler: ' + e.message); }
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
    if (r && r.error) { await showAlert('Speichern fehlgeschlagen: ' + r.error); return; }
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
          domain: 'Workflow-Lauf', favicon: '', kind: 'file',
        });
      }
      state.chatReferences[sid] = existing;
    }
    // The chat is now visible in the sidebar (status flipped to active).
    // Banner stays — the chat is forever recognizable as workflow-derived.
    if (typeof loadSessions === 'function') loadSessions();
    // Re-render the banner so the Save button switches to "Saved".
    renderWorkflowBanner();
  } catch (e) { await showAlert('Speicherfehler: ' + e.message); }
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

/* The client-side path-extraction heuristic moved to the server
   (admin.py:_workflow_run_paths_classified). Single source of truth so
   what the regular Refs/Artifacts panels show always matches what the
   download endpoints will serve. */

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
      <${tag} class="${cls} wf-detail-collapsed-pre">${escapeHtml(preview)}<span class="wf-detail-ellipsis"> … (+${(text.length - preview.length).toLocaleString()} Zeichen)</span></${tag}>
      <button type="button" class="wf-btn wf-btn-mini wf-btn-ghost wf-detail-expand-btn" onclick="wfDetailToggleExpand(this, '${tag}', '${cls.replace(/'/g, "\\'")}')">Mehr anzeigen</button>
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
    btn.textContent = 'Weniger anzeigen';
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
        `<span class="wf-detail-ellipsis"> … (+${(full.length - preview.length).toLocaleString()} Zeichen)</span>`;
    }
    btn.textContent = 'Mehr anzeigen';
  }
}

/* ─── Transcript download ─── */

function wfDetailDownloadTranscript() {
  const data = wfState.detailRun;
  if (!data) return;
  const lines = [];
  lines.push(`# Workflow-Lauf: ${data.workflow_name || ''}`);
  lines.push('');
  lines.push(`- Ausführungs-ID: \`${wfState.currentExecId}\``);
  lines.push(`- Status: ${data.status || 'unbekannt'}`);
  if (data.started_at) lines.push(`- Gestartet: ${data.started_at}`);
  if (data.finished_at) lines.push(`- Beendet: ${data.finished_at}`);
  if (data.duration_ms != null) lines.push(`- Dauer: ${data.duration_ms}ms`);
  if (data.user_display) lines.push(`- Benutzer: ${data.user_display}`);
  if (data.trigger_kind) lines.push(`- Auslöser: ${data.trigger_kind}`);
  if (data.cost_usd) lines.push(`- Kosten: $${Number(data.cost_usd).toFixed(4)}`);
  lines.push('');
  if (data.workflow_source) {
    lines.push('## Workflow-Quellcode');
    lines.push('');
    lines.push('```');
    lines.push(data.workflow_source);
    lines.push('```');
    lines.push('');
  }
  const steps = data.steps || (typeof data.steps_json === 'string' ? JSON.parse(data.steps_json || '[]') : []) || [];
  if (steps.length) {
    lines.push('## Ablauf');
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
    lines.push('## Rückgabewert');
    lines.push('');
    lines.push('```');
    lines.push(typeof rv === 'string' ? rv : JSON.stringify(rv, null, 2));
    lines.push('```');
    lines.push('');
  }
  if (data.error) {
    lines.push('## Fehler');
    lines.push('');
    lines.push('```');
    lines.push(data.error);
    lines.push('```');
    lines.push('');
  }
  if (wfState.detailFollowups.length) {
    lines.push('## Folgekonversation');
    lines.push('');
    for (const f of wfState.detailFollowups) {
      lines.push(`### ${f.role === 'user' ? 'Benutzer' : 'Assistent'}`);
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
    banner.innerHTML = `<div class="workflow-run-banner-loading">Workflow-Lauf wird geladen…</div>`;
    return;
  }
  const status = data.status || 'unbekannt';
  const isLive = WF_LIVE_STATUSES.has(status);
  const dur = _fmtDuration(data.duration_ms);
  const cost = data.cost_usd ? '$' + Number(data.cost_usd).toFixed(4) : '';
  const started = _fmtTs(data.started_at);
  const finished = _fmtTs(data.finished_at);
  const agent = data.agent_id || '';
  const model = data.model || '—';

  // Files (refs + artifacts) are surfaced via the regular right-panel
  // tabs — seeded server-side at session-create time. The banner doesn't
  // render its own files card to avoid duplicating UI.

  // Trace: workflow source + tool-call/result pairs + return
  let traceHtml = '';
  const src = (data.workflow_source || '').trim();
  if (src) {
    traceHtml += `
      <div class="wf-banner-trace-card">
        <div class="wf-banner-trace-card-label">Workflow-Quellcode</div>
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
      const resultText = paired ? (paired.detail || '(keine Ausgabe)') : '';
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
          <div class="wf-banner-trace-card-label">Fehler · L${s.line}</div>
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
        <div class="wf-banner-trace-card-label">Rückgabewert</div>
        ${_wfMaybeCollapsed(txt, { tag: 'pre', cls: 'wf-banner-final-value' })}
      </div>`;
  } else if (data.error) {
    traceHtml += `
      <div class="wf-banner-trace-card wf-banner-trace-error">
        <div class="wf-banner-trace-card-label">Fehlgeschlagen</div>
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
      <button class="wf-btn wf-btn-ghost wf-banner-back" onclick="wfBannerBack()" title="Zurück zu Workflows">← Workflows</button>
      <div class="wf-banner-titlebar">
        <div class="wf-banner-title">
          <span class="wf-banner-workflow-name">${escapeHtml(data.workflow_name || 'Workflow')}</span>
          <span class="wf-status-${status} wf-banner-status">${escapeHtml(status)}</span>
        </div>
        <div class="wf-banner-meta">
          ${agent ? `<span class="wf-banner-meta-pill">Agent: <strong>${escapeHtml(agent)}</strong></span>` : ''}
          <span class="wf-banner-meta-pill">Modell: <strong>${escapeHtml(model)}</strong></span>
          ${started ? `<span class="wf-banner-meta-pill">gestartet: ${escapeHtml(started)}</span>` : ''}
          ${finished ? `<span class="wf-banner-meta-pill">beendet: ${escapeHtml(finished)}</span>` : ''}
          ${dur ? `<span class="wf-banner-meta-pill">Dauer: ${escapeHtml(dur)}</span>` : ''}
          ${cost ? `<span class="wf-banner-meta-pill">Kosten: ${escapeHtml(cost)}</span>` : ''}
          ${data.user_display ? `<span class="wf-banner-meta-pill">von ${escapeHtml(data.user_display)}</span>` : ''}
          <span class="wf-banner-meta-pill wf-banner-execid" title="${escapeHtml(wfId)}">${escapeHtml(wfId.slice(0, 10))}</span>
        </div>
      </div>
      <div class="wf-banner-actions">
        ${isLive ? '<button class="wf-btn wf-btn-ghost" onclick="wfBannerCancel()">Lauf abbrechen</button>' : ''}
        <button class="wf-btn wf-btn-ghost" onclick="wfDetailDownloadTranscript()" title="Markdown-Protokoll dieses Laufs herunterladen">Protokoll herunterladen</button>
        ${promoted
          ? '<button class="wf-btn wf-btn-ghost" disabled title="Dieser Lauf ist als Chat gespeichert">✓ Gespeichert</button>'
          : '<button class="wf-btn wf-btn-primary" onclick="wfBannerSaveToChats()">In Chats speichern</button>'}
      </div>
    </div>
    <details class="wf-banner-trace-details" ${wfBanner.traceCollapsed ? '' : 'open'} ontoggle="wfBanner.traceCollapsed = !this.open">
      <summary>
        <span class="wf-banner-section-label">Ablaufprotokoll</span>
        <span class="wf-banner-section-summary">${steps.length} Schritt${steps.length === 1 ? '' : 'e'}${data.tool_calls ? ` · ${data.tool_calls} Tool-Aufruf${data.tool_calls === 1 ? '' : 'e'}` : ''}${data.llm_calls ? ` · ${data.llm_calls} LLM-Aufruf${data.llm_calls === 1 ? '' : 'e'}` : ''}</span>
      </summary>
      <div class="wf-banner-trace-body">
        ${traceHtml || '<div class="wf-hist-empty">Keine Schritte aufgezeichnet.</div>'}
      </div>
    </details>
    ${isLive ? '<div class="wf-banner-live-hint">⏵ Lauf in Bearbeitung — das Eingabefeld akzeptiert Folgenachrichten, sobald er abgeschlossen ist.</div>' : ''}
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
  const promptText = prompt || 'Der Workflow wartet auf eine Datei.';
  const acceptHint = accept ? `Akzeptiert: ${escapeHtml(accept)}` : 'Jeder Dateityp akzeptiert';
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
            <span class="wf-upload-link">Datei auswählen</span>
            <span class="wf-upload-or">oder hier ablegen</span>
          </div>
          <div class="wf-upload-filename" id="wf-upload-filename"></div>
        </div>
      </label>
      <div class="wf-upload-actions">
        <button class="wf-btn wf-btn-ghost" onclick="wfCancelUpload()">Abbrechen</button>
        <button class="wf-btn wf-btn-primary" id="wf-upload-submit" onclick="wfUploadFile()" disabled>Hochladen</button>
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
    submit.textContent = 'Wird hochgeladen…';
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
      if (submit) { submit.disabled = false; submit.textContent = 'Hochladen'; }
      const nameEl = document.getElementById('wf-upload-filename');
      if (nameEl) nameEl.innerHTML = `<span class="wf-upload-error">${escapeHtml(data.error)}</span>`;
      return;
    }
    document.getElementById('wf-run-prompt').classList.add('hidden');
  } catch (e) {
    if (submit) { submit.disabled = false; submit.textContent = 'Hochladen'; }
    const nameEl = document.getElementById('wf-upload-filename');
    if (nameEl) nameEl.innerHTML = `<span class="wf-upload-error">${escapeHtml(e.message || 'Hochladen fehlgeschlagen')}</span>`;
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
const WF_TEMPLATE_MEETING_NOTES = `# Besprechungsnotizen — lädt eine Aufnahme hoch, transkribiert sie,
# extrahiert Notizen + Aufgaben, speichert in eine Markdown-Datei.

WORKFLOW "Besprechungsnotizen"
DESCRIPTION "Eine Besprechungsaufnahme transkribieren und strukturierte Notizen extrahieren."
TRIGGER manual

SET upload = CALL ask_user_for_file prompt="Besprechungsaufnahme hochladen (.wav / .mp3 / .m4a)" accept="audio/*"
SET transcript_result = CALL transcribe_audio file=upload.path
SET transcript = transcript_result.transcript

SET notes_result = CALL ask_llm prompt="Extrahiere aus diesem Besprechungstranskript:\\n\\n1. Eine Zusammenfassung in 2-3 Sätzen\\n2. Wichtige Entscheidungen (Aufzählung)\\n3. Aufgaben (Aufzählung, mit Verantwortlichem falls genannt)\\n4. Offene Fragen (Aufzählung)\\n\\nGib gut formatiertes Markdown zurück.\\n\\nTranskript:\\n\\n{{transcript}}"
SET notes = notes_result.text

SET filename = "/tmp/meeting_" + now("%Y-%m-%d_%H%M") + ".md"
SET body = "# Besprechungsnotizen — " + now("%Y-%m-%d %H:%M") + "\\n\\n## Transkript\\n\\n" + transcript + "\\n\\n## Notizen\\n\\n" + notes + "\\n"
CALL write_file path=filename content=body

RETURN filename
`;
