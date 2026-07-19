/* ═══════════════════════════════════════════════════════════
   WORKFLOWS — list, editor, runner
   ═══════════════════════════════════════════════════════════ */

const WF_AGENT = 'main';  // MVP: single agent
// Mirror engine/workflow.py `_WF_KEYWORDS` exactly (source of truth for the
// lexer). AGENT + MODEL are header keywords (AGENT main / MODEL kimi-k2.6) —
// they were missing here, so the MODEL line in a .flow wasn't syntax-coloured.
const WF_KEYWORDS = new Set([
  'WORKFLOW','DESCRIPTION','TRIGGER','AGENT','MODEL',
  'SET','CALL','IF','ELSE','FOR','EACH','IN','RETURN',
  'AND','OR','NOT','TRUE','FALSE','NULL'
]);
const WF_BUILTINS = new Set(['len','str','int','float','bool','now','lower','upper','trim','contains','split','join','replace','plan_steps']);

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
            <span class="wf-card-title-text">${escapeHtml(wf.display_name || wf.name)}</span>
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
  if (typeof feedbackHydrateState === 'function') feedbackHydrateState('workflow', '');
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
  // Feedback only on terminal runs (a result exists to rate).
  const fb = (!isCancelable && typeof renderFeedbackControl === 'function')
    ? renderFeedbackControl('workflow', r.execution_id, '', `${r.workflow_name || ''} · ${status}`)
    : '';
  return `
    <div class="wf-hist-actions">
      <button class="wf-btn wf-btn-ghost wf-btn-mini" onclick="wfShowHistoryDetail('${escapeJs(r.execution_id)}')">Anzeigen</button>
      ${cancelBtn}
      ${deleteBtn}
      ${fb}
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
  const planEditor = document.getElementById('wf-plan-editor');
  if (name) {
    nameInput.value = name;
    nameInput.disabled = true;
    try {
      const data = await API.get(`/v1/agents/${WF_AGENT}/workflows/${encodeURIComponent(name)}`);
      editor.value = data.source || '';
      if (planEditor) planEditor.value = data.plan_md || '';
    } catch (e) {
      editor.value = '';
      if (planEditor) planEditor.value = '';
    }
  } else {
    nameInput.value = '';
    nameInput.disabled = false;
    editor.value = WF_TEMPLATE_MEETING_NOTES;
    if (planEditor) planEditor.value = '';
  }
  wfSwitchEditorTab('flow');
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
    const planEl = document.getElementById('wf-plan-editor');
    const data = await API.post(`/v1/agents/${WF_AGENT}/workflows`,
      { name, source, plan_md: planEl ? planEl.value : '' });
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

/* ─── Editor: Flow/Plan tabs ─── */
function wfSwitchEditorTab(tab) {
  const flowTab = document.getElementById('wf-tab-flow');
  const planTab = document.getElementById('wf-tab-plan');
  if (!flowTab || !planTab) return;
  flowTab.classList.toggle('active', tab === 'flow');
  planTab.classList.toggle('active', tab === 'plan');
  document.getElementById('wf-flow-wrap').classList.toggle('hidden', tab !== 'flow');
  document.getElementById('wf-plan-wrap').classList.toggle('hidden', tab !== 'plan');
  if (tab === 'plan') setTimeout(() => document.getElementById('wf-plan-editor')?.focus(), 30);
}

/* ─── KI-Generator: Workflow aus Chat / Plan / Beschreibung ───
   Gemeinsamer Fluss aller Einstiegspunkte (Status-Bar-Button, /workflow im
   Terminal-Chat, Artifact-Viewer, "Neu aus Beschreibung"): Modal → POST
   /v1/workflows/generate → Poll → bei ready Editor mit Entwurf öffnen
   (review-before-save, nichts wird automatisch gespeichert). */
let wfGen = { source: null, files: [], genId: null, timer: null };

function wfOpenGenerateModal(kind, payload) {
  // kind: 'nl' | 'chat' | 'plan'; payload: {session_id,title} bzw. {text,title}
  if (wfGen.timer) { clearInterval(wfGen.timer); }
  wfGen = { source: { type: kind, ...(payload || {}) }, files: [], genId: null, timer: null };
  const srcLine = document.getElementById('wf-gen-source-line');
  const instrLabel = document.getElementById('wf-gen-instr-label');
  const instr = document.getElementById('wf-gen-instructions');
  if (kind === 'chat') {
    srcLine.textContent = `Quelle: Chat ${((payload || {}).session_id || '').substring(0, 8)}` +
      ((payload || {}).title ? ` — ${payload.title}` : '');
    instrLabel.textContent = 'Zusätzliche Vorgaben (optional)';
    instr.placeholder = 'z. B. „nur die Analyse-Schritte übernehmen", „Report auf Englisch" …';
  } else if (kind === 'plan') {
    srcLine.textContent = `Quelle: Plan${(payload || {}).title ? ` — ${payload.title}` : ''}`;
    instrLabel.textContent = 'Zusätzliche Vorgaben (optional)';
    instr.placeholder = 'z. B. welche Eingaben der Workflow abfragen soll …';
  } else {
    srcLine.textContent = 'Quelle: Beschreibung';
    instrLabel.textContent = 'Beschreibung des Workflows';
    instr.placeholder = 'Was soll der Workflow tun? Eingaben, Schritte, gewünschtes Ergebnis …';
  }
  instr.value = '';
  wfGenRenderFiles();
  wfResetSkillMode();
  document.getElementById('wf-gen-form').classList.remove('hidden');
  document.getElementById('wf-gen-progress').classList.add('hidden');
  document.getElementById('wf-generate-modal').classList.remove('hidden');
  setTimeout(() => instr.focus(), 50);
  // Offer existing skills to reference — match against the source's title/text.
  wfLoadSkillMatches();
}

function wfResetSkillMode() {
  const r = document.querySelector('input[name="wf-skill-mode"][value="none"]');
  if (r) r.checked = true;
  const sel = document.getElementById('wf-skill-ref-sel');
  if (sel) { sel.innerHTML = ''; sel.style.display = 'none'; }
  const hint = document.getElementById('wf-skill-match-hint');
  if (hint) hint.textContent = '';
}

function wfOnSkillModeChange() {
  const mode = (document.querySelector('input[name="wf-skill-mode"]:checked') || {}).value || 'none';
  const sel = document.getElementById('wf-skill-ref-sel');
  if (sel) sel.style.display = (mode === 'reference') ? 'block' : 'none';
}

async function wfLoadSkillMatches() {
  // Query text = the source title (chat/plan) or the description field.
  const src = wfGen.source || {};
  const task = (src.title || src.text || document.getElementById('wf-gen-instructions').value || '').trim();
  const sel = document.getElementById('wf-skill-ref-sel');
  const hint = document.getElementById('wf-skill-match-hint');
  if (!task || !sel) return;
  try {
    const r = await API.get(`/v1/skills/match?agent_id=${encodeURIComponent(WF_AGENT)}&task=${encodeURIComponent(task)}`);
    const matches = (r.matches || []).filter(m => m.slug);
    if (!matches.length) {
      sel.innerHTML = '<option value="">(keine passenden Skills)</option>';
      if (hint) hint.textContent = 'Es wurden keine passenden vorhandenen Skills gefunden.';
      return;
    }
    sel.innerHTML = matches.map(m =>
      `<option value="${escapeHtml(m.slug)}">${escapeHtml(m.name || m.slug)}${m.matched_via === 'semantic' ? ' ·◎' : ''}</option>`
    ).join('');
    if (hint) hint.textContent = `${matches.length} passende(r) Skill(s) gefunden — zum Referenzieren „Vorhandenen Skill" wählen.`;
  } catch (e) { /* match is best-effort */ }
}

function wfCloseGenerateModal() {
  if (wfGen.timer) { clearInterval(wfGen.timer); wfGen.timer = null; }
  // Läuft noch eine Generierung, serverseitig mit abbrechen (Entwurf wäre
  // nach dem Schließen ohnehin unerreichbar — kein Orphan-Lauf hinterlassen).
  if (wfGen.genId) { API.post(`/v1/workflows/generate/${wfGen.genId}/cancel`, {}).catch(() => {}); wfGen.genId = null; }
  document.getElementById('wf-generate-modal').classList.add('hidden');
}

function wfGenAddFiles(input) {
  const files = Array.from(input.files || []);
  input.value = '';
  for (const f of files) {
    if (wfGen.files.length >= 10) break;
    const reader = new FileReader();
    reader.onload = () => {
      wfGen.files.push({ name: f.name, text: String(reader.result || '').slice(0, 200000) });
      wfGenRenderFiles();
    };
    reader.readAsText(f);
  }
}

function wfGenRemoveFile(idx) {
  wfGen.files.splice(idx, 1);
  wfGenRenderFiles();
}

function wfGenRenderFiles() {
  const root = document.getElementById('wf-gen-files');
  if (!root) return;
  root.innerHTML = wfGen.files.map((f, i) =>
    `<span class="wf-pill">${escapeHtml(f.name)} <a href="#" onclick="wfGenRemoveFile(${i});return false" title="Entfernen">✕</a></span>`
  ).join('');
}

async function wfStartGenerate() {
  const instr = (document.getElementById('wf-gen-instructions').value || '').trim();
  const src = wfGen.source || { type: 'nl' };
  const body = { agent_id: WF_AGENT, instructions: instr, attachments: wfGen.files };
  // Skill integration: extract a new skill, or reference an existing one.
  const skillMode = (document.querySelector('input[name="wf-skill-mode"]:checked') || {}).value || 'none';
  if (skillMode === 'extract') {
    body.extract_skill = true;
  } else if (skillMode === 'reference') {
    const ref = (document.getElementById('wf-skill-ref-sel') || {}).value || '';
    if (!ref) { showToast('Bitte einen Skill zum Referenzieren wählen', true); return; }
    body.skill_ref = ref;
  }
  if (src.type === 'chat') {
    body.source = { type: 'chat', session_id: src.session_id };
  } else if (src.type === 'plan') {
    body.source = { type: 'plan', text: src.text || '' };
  } else {
    if (!instr) {
      document.getElementById('wf-gen-instructions').focus();
      return;
    }
    body.source = { type: 'nl', text: instr };
  }
  try {
    const res = await API.post('/v1/workflows/generate', body);
    if (res.error) throw new Error(res.error);
    wfGen.genId = res.gen_id;
    document.getElementById('wf-gen-form').classList.add('hidden');
    document.getElementById('wf-gen-progress').classList.remove('hidden');
    document.getElementById('wf-gen-steps').innerHTML = '<div>Gestartet …</div>';
    wfGen.timer = setInterval(wfPollGenerate, 1500);
  } catch (e) {
    document.getElementById('wf-gen-steps').innerHTML = '';
    alert('Start fehlgeschlagen: ' + e.message);
  }
}

async function wfPollGenerate() {
  if (!wfGen.genId) return;
  let d;
  try {
    d = await API.get(`/v1/workflows/generate/${wfGen.genId}`);
  } catch (e) { return; }
  const stepsEl = document.getElementById('wf-gen-steps');
  if (stepsEl) {
    const rows = (d.steps || []).map(s =>
      `<div style="${s.kind === 'error' ? 'color:var(--error,#ef4444)' : ''}">${escapeHtml(s.text)}</div>`);
    if (d.status === 'generating' && d.phase) rows.push(`<div>… (${escapeHtml(d.phase)})</div>`);
    stepsEl.innerHTML = rows.join('') || '<div>…</div>';
  }
  if (d.status === 'generating') return;
  clearInterval(wfGen.timer); wfGen.timer = null;
  wfGen.genId = null;
  if (d.status === 'ready' || d.status === 'ready_with_warnings') {
    await wfApplyGeneratedDraft(d);
  } else if (d.status === 'error') {
    if (stepsEl) stepsEl.innerHTML += `<div style="color:var(--error,#ef4444)">Fehler: ${escapeHtml(d.error || 'unbekannt')}</div>`;
  }
}

function wfCancelGenerate() {
  if (!wfGen.genId) { wfCloseGenerateModal(); return; }
  API.post(`/v1/workflows/generate/${wfGen.genId}/cancel`, {}).catch(() => {});
  wfGen.genId = null;
  if (wfGen.timer) { clearInterval(wfGen.timer); wfGen.timer = null; }
  wfCloseGenerateModal();
}

async function wfApplyGeneratedDraft(d) {
  // genId ist bereits abgeräumt → close cancelt nichts mehr serverseitig.
  wfCloseGenerateModal();
  await wfOpenEditor(null);
  const nameInput = document.getElementById('wf-editor-name');
  if (nameInput) nameInput.value = d.suggested_name || '';
  const editor = document.getElementById('wf-editor');
  editor.value = d.flow_source || '';
  const planEl = document.getElementById('wf-plan-editor');
  if (planEl) planEl.value = d.plan_md || '';
  wfApplyMetaFromSource(editor.value);
  wfOnInput();
  const status = document.getElementById('wf-editor-status');
  const warns = d.warnings || [];
  if (status) {
    status.textContent = (d.notes || 'Entwurf erzeugt — bitte prüfen und speichern.') +
      (warns.length ? ` — ${warns.length} Warnung(en): ${warns.join('; ')}` : '');
    status.className = 'wf-editor-status' + (warns.length ? ' wf-error' : ' wf-ok');
  }
}

function wfGenerateFromChat(sessionId, title) {
  const chat = (typeof state !== 'undefined' && state.activeChat) ? state.activeChat : null;
  const sid = sessionId || (chat && chat.sessionId) || '';
  if (!sid) return;
  wfOpenGenerateModal('chat', { session_id: sid, title: title || (chat && chat.title) || '' });
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
   workflow_run_id. The run output renders as a chat-style turn-group at the
   top of #messages-container (buildWorkflowRunBlock, injected by
   renderMessages), and the run's stats/source/protocol live in dedicated
   right-panel tabs (Statistik / Quellcode / Protokoll; artifacts reuse the
   normal Dateien tab). The composer is the regular chat composer (so
   file-attach, thinking levels, model selection, etc. all work identically
   to a normal chat). */

const WF_TERMINAL_STATUSES = new Set(['completed', 'succeeded', 'failed', 'cancelled']);
const WF_LIVE_STATUSES = new Set(['running', 'pending', 'waiting_approval']);

// Shared state for the active workflow run. Reset whenever the active chat
// is no longer workflow-bound. Polling timer fires while the run is live so
// the main-area run turn-group + right-panel tabs reflect fresh steps and
// terminal transitions.
//
// v9.290: the run is no longer shown as a banner above the messages. The run
// output renders in the MAIN message area as a chat-style turn-group (built
// by renderMessages() from wfBanner.data — see chat_render.js), and the
// stats / source / protocol move into dedicated right-panel tabs (Statistik /
// Quellcode / Protokoll; Artefakte reuses the normal artifacts tab). The
// object name stays `wfBanner` to avoid a global rename churn.
const wfBanner = {
  execId: null,        // active workflow_run_id
  data: null,          // last fetched run data (live or persisted)
  pollTimer: null,
  _loadError: null,    // last load failure message (shown in the run turn-group)
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
      wfBanner._loadError = e.message || 'Unbekannter Fehler';
      renderWorkflowRunUI();
      return;
    }
  }
  wfBanner._loadError = null;
  wfBanner.data = data;
  renderWorkflowRunUI();
  if (initial && data && WF_LIVE_STATUSES.has(data.status || '')) {
    wfBannerStartPolling();
  } else if (data && WF_TERMINAL_STATUSES.has(data.status || '')) {
    // Terminal — stop polling (was leaking a 800ms timer for the session's
    // life) and re-fetch the persisted /history row once so the run shows the
    // full final trace + return value instead of the truncated live data.
    wfBannerStopPolling();
    try {
      const persisted = await API.get(`/v1/workflows/history/${id}`);
      if (persisted && !persisted.error) {
        wfBanner.data = persisted;
        renderWorkflowRunUI();
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
  // Deactivating the workflow run: drop the injected main-area turn-group
  // (renderMessages() will now skip it since wfBanner.data is cleared) and
  // hide the workflow-only right-panel tabs. Tear down the upload card + give
  // the composer back (the run is no longer the active view).
  wfBanner._loadError = null;
  const host = document.getElementById('wf-upload-host');
  if (host) { host.innerHTML = ''; host.classList.add('hidden'); delete host.dataset.wfKey; }
  _wfSetComposerHidden(false);
  if (typeof renderMessages === 'function') {
    try { renderMessages(); } catch (_) {}
  }
  if (typeof updateWorkflowTabs === 'function') updateWorkflowTabs();
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
    // Re-render so the Save button (Statistik tab) switches to "Saved".
    renderWorkflowRunUI();
  } catch (e) { await showAlert('Speicherfehler: ' + e.message); }
}

function wfBannerBack() {
  // Tear down banner state and route back to the workflows list.
  wfBannerStopPolling();
  wfBanner.execId = null;
  wfBanner.data = null;
  if (typeof navigateTo === 'function') navigateTo('workflows');
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
  const data = wfBanner.data;
  if (!data) return;
  const execId = wfBanner.execId || '';
  const lines = [];
  lines.push(`# Workflow-Lauf: ${data.workflow_name || ''}`);
  lines.push('');
  lines.push(`- Ausführungs-ID: \`${execId}\``);
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
  const steps = _wfSteps(data);
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
  // Follow-up conversation: read from the bound chat's live message list
  // (the run's own turn-group is rendered from data.steps above, so only the
  // real user/assistant messages below it are the follow-up).
  const followups = (state.activeChat && Array.isArray(state.activeChat.messages))
    ? state.activeChat.messages.filter(m => (m.role === 'user' || m.role === 'assistant')
        && typeof m.content === 'string' && m.content.trim())
    : [];
  if (followups.length) {
    lines.push('## Folgekonversation');
    lines.push('');
    for (const f of followups) {
      lines.push(`### ${f.role === 'user' ? 'Benutzer' : 'Assistent'}`);
      lines.push('');
      lines.push(f.content || '');
      lines.push('');
    }
  }
  const md = lines.join('\n');
  const blob = new Blob([md], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const safeName = (data.workflow_name || 'workflow').replace(/[^A-Za-z0-9_\-]+/g, '_');
  a.download = `${safeName}_${execId.slice(0, 8)}.md`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1000);
}

/* ─── Workflow run render: main-area turn-group + right-panel tabs ─── */

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

// True when the active chat is bound to the run currently tracked in wfBanner.
function _wfRunActive() {
  const chat = state.activeChat;
  const wfId = chat && chat.workflowRunId ? chat.workflowRunId : '';
  return !!(wfId && wfId === wfBanner.execId);
}

// Normalise the run's steps[] (live dict or persisted steps_json string).
function _wfSteps(data) {
  if (!data) return [];
  if (Array.isArray(data.steps)) return data.steps;
  if (typeof data.steps_json === 'string') {
    try { return JSON.parse(data.steps_json || '[]') || []; } catch (_) { return []; }
  }
  return [];
}

// A live run is blocked on an upload when its most recent step is an
// `ask_user_for_file` CALL with no matching call_done yet. The backend emits
// the prompt + accept inline in that step's detail (engine/workflow.py), so we
// parse them straight out of it. Returns {prompt, accept} or null.
function _wfPendingUpload(data) {
  if (!data || !WF_LIVE_STATUSES.has(data.status || '')) return null;
  const steps = _wfSteps(data);
  // Walk from the end: the first ask_user_for_file we hit decides — if its
  // call_done already came through (later or as the same tail), it's answered.
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    const detail = s && s.detail || '';
    if (s && s.kind === 'call_done' && detail.includes('ask_user_for_file ')) return null;
    if (s && s.kind === 'call' && detail.includes('ask_user_for_file(')) {
      return wfParseAskFileDetail(detail);
    }
  }
  return null;
}

// Central re-render: refresh the main-area run turn-group AND the workflow
// right-panel tabs from wfBanner.data. Called on every poll tick + on
// terminal transition + after save/cancel.
function renderWorkflowRunUI() {
  // Sync the run's transcript into chat.messages as REAL message rows, then a
  // full renderMessages() renders them through the normal chat renderer — so
  // the workflow result looks byte-identical to a normal chat (same fonts,
  // colors, spacing, markdown). Follow-up messages render below them.
  _wfSyncTranscriptMessages();
  if (typeof renderMessages === 'function') {
    try { renderMessages(); } catch (_) {}
  }
  // Render the run control bar into the stable #wf-upload-host: upload card
  // when blocked on ask_user_for_file, otherwise pause/resume+stop. Hides the
  // composer while the run is live (no chatting mid-run), restores it on a
  // terminal run. All three states handled inside _wfRenderRunControls.
  _wfRenderRunControls();
  if (typeof updateWorkflowTabs === 'function') updateWorkflowTabs();
  // Refresh whichever workflow tab is currently open.
  const tab = state.rightPanelTab;
  if (tab === 'wf-statistik') renderWorkflowStatistikPane();
  else if (tab === 'wf-quellcode') renderWorkflowQuellcodePane();
  else if (tab === 'wf-protokoll') renderWorkflowProtokollPane();
  // A live run may have produced new artifacts — reload so the Artefakte tab
  // (and its badge) reflect them, exactly like a normal chat turn does.
  if (typeof refreshWorkflowArtifacts === 'function') refreshWorkflowArtifacts();
  if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
  // Statusline: reflect the run's token/cost totals (workflow_history rollup).
  if (typeof updateStatusBar === 'function') updateStatusBar();
  // Composer visibility is derived ONCE here, LAST, from the single source of
  // truth (run active + live) — so no earlier/async render step can leave it in
  // the wrong state. Hidden only while a live run owns the view.
  const _live = _wfRunActive() && wfBanner.data
    && WF_LIVE_STATUSES.has(wfBanner.data.status || '');
  _wfSetComposerHidden(!!_live);
}

// Reload the bound session's artifacts so freshly-written workflow outputs
// appear in the normal Artefakte tab. Skips while an artifact is open (so we
// don't yank the version the user is viewing) unless the list grew.
async function refreshWorkflowArtifacts() {
  const sid = state.activeChat && state.activeChat.sessionId;
  if (!sid) return;
  try {
    const resp = await API.getArtifacts(sid);
    const next = (resp && resp.artifacts) || [];
    const prev = state.artifacts[sid] || [];
    state.artifacts[sid] = next;
    // When the artifact set changes, re-sync the transcript + re-render the
    // main flow so the assistant turns' file entries upgrade from plain file
    // cards to clickable artifact-cards (artifact_id now known). This is the
    // bridge for the first render, where the sync ran before artifacts loaded.
    if (next.length !== prev.length) {
      _wfSyncTranscriptMessages();
      if (typeof renderMessages === 'function') { try { renderMessages(); } catch (_) {} }
      // Re-render the artifacts list if that tab is open and nothing is pinned.
      if (state.rightPanelTab === 'artifacts' && !state.activeArtifactId
          && typeof showArtifactList === 'function') {
        showArtifactList();
      }
    }
  } catch (_) {}
}

/* ── Main-area render: the run transcript AS real chat messages ──
   The run's transcript (data.transcript) is injected into chat.messages as
   REAL user/assistant rows so the normal chat renderer (renderMessages)
   renders them with byte-identical fonts/colors/spacing/markdown to any chat.
   The rows are tagged `_wfSynthetic` + not persisted; they're re-derived from
   wfBanner.data on every poll and always kept as a PREFIX before the real
   follow-up messages the user typed. */
function _wfSyncTranscriptMessages() {
  const chat = state.activeChat;
  if (!chat) return;
  if (!Array.isArray(chat.messages)) chat.messages = [];
  // Strip any previously-injected synthetic rows — we rebuild them fresh.
  const real = chat.messages.filter(m => !m || !m._wfSynthetic);

  if (!_wfRunActive()) {
    // Not (or no longer) a workflow run — leave only the real messages.
    chat.messages = real;
    return;
  }
  const data = wfBanner.data;
  const synth = [];
  if (wfBanner._loadError) {
    synth.push({ role: 'assistant', _wfSynthetic: true,
      content: `⚠️ Lauf konnte nicht geladen werden: ${wfBanner._loadError}` });
  } else if (!data) {
    synth.push({ role: 'assistant', _wfSynthetic: true, content: '_Workflow-Lauf wird geladen …_' });
  } else {
    const status = data.status || '';
    const isLive = WF_LIVE_STATUSES.has(status);
    const transcript = Array.isArray(data.transcript) ? data.transcript : [];
    // Index the run's seeded artifacts by basename so a transcript file path
    // maps to a real artifact_id → the chat renderer draws a clickable
    // artifact-card (identical to any normal chat), opening the right panel.
    const sid = chat.sessionId;
    const artByName = {};
    for (const a of (state.artifacts[sid] || [])) {
      const bn = (a.path || a.name || '').split('/').pop();
      if (bn) artByName[bn] = a;
    }
    const pending = _wfPendingUpload(data);
    // The in-flight step's live-progress turn (_wf_live) is the LAST assistant
    // turn while live+unblocked, so it renders as the main response (not folded
    // into the collapsed Aktivität) AND carries the pause/resume/stop buttons.
    const liveIdx = (isLive && !pending)
      ? transcript.map((m, i) => (m && m._wf_live ? i : -1)).filter(i => i >= 0).pop()
      : -1;
    const paused = !!data.paused;
    // v9.291.3: a run that FAILED/was CANCELLED mid-way but still produced a
    // transcript (partial work + artifacts) is confusing — the steps below look
    // complete, yet the run is marked failed. Prepend a concise note so the user
    // knows WHY it stopped (e.g. a transient 429 rate-limit on the final step)
    // and that the partial results/artifacts below are still usable.
    if (!isLive && (status === 'failed' || status === 'cancelled') && transcript.length) {
      const err = (data.error || '').trim();
      const rate = /\b429\b|rate limit|too many requests/i.test(err);
      const head = status === 'cancelled' ? 'Lauf abgebrochen' : 'Lauf vorzeitig beendet';
      const hint = rate
        ? 'Ein Schritt lief auf ein **Rate-Limit des Anbieters (HTTP 429)** — die bis dahin erzeugten Ergebnisse und Dateien unten sind gültig und im Dateien-Reiter verfügbar.'
        : 'Die bis dahin erzeugten Ergebnisse und Dateien unten sind verfügbar (Dateien-Reiter).';
      synth.push({ role: 'assistant', _wfSynthetic: true,
        content: `> ⚠️ **${head}.** ${hint}` + (err ? `\n>\n> \`${err.replace(/`/g, "'").slice(0, 400)}\`` : '') });
    }
    for (let ti = 0; ti < transcript.length; ti++) {
      const m = transcript[ti];
      // A step turn carrying _wf_events expands into REAL chat rows (thinking /
      // tool_call+tool_result / answer-text) so the renderer draws it exactly
      // like a normal turn — args tables, result blocks, streamed text. While
      // LIVE (ti===liveIdx) it also gets the status + pause/stop line; once
      // FINALISED the same events stay visible with the answer text (so the tool
      // history doesn't vanish when the step ends). See _wfLiveRows.
      if (Array.isArray(m._wf_events)) {
        for (const r of _wfLiveRows(m, ti === liveIdx, paused)) synth.push(r);
        continue;
      }
      const role = m.role === 'user' ? 'user' : 'assistant';
      const content = typeof m.content === 'string' ? m.content : '';
      const files = Array.isArray(m.files) ? m.files.filter(Boolean) : [];
      const row = { role, content, _wfSynthetic: true, _wfModel: m.model || '' };
      // Produced/attached files → _files[], which the chat renderer draws as
      // file-attachment / artifact cards (exactly like a normal chat turn).
      // Match against seeded artifacts to get the artifact_id (clickable card
      // opening the panel); unmatched paths still show as a plain file card.
      if (files.length) {
        row._files = files.map(f => {
          const bn = String(f).split('/').pop();
          const art = artByName[bn];
          return art
            ? { path: f, artifact_id: art.id, artifact_version: art.latest_version || 1,
                action: role === 'user' ? '' : 'created' }
            : { path: f, action: role === 'user' ? '' : 'created' };
        });
      }
      synth.push(row);
    }
    // Runs before v9.290.2 have no transcript. Fall back to a compact answer
    // from the return value so the main area is never empty for old runs.
    if (!transcript.length) {
      let rv = data.return_value;
      if (typeof rv === 'string' && rv) { try { rv = JSON.parse(rv); } catch (_) {} }
      const rvTxt = (rv !== null && rv !== undefined && rv !== '')
        ? (typeof rv === 'string' ? rv : JSON.stringify(rv, null, 2)) : '';
      if (data.error) {
        synth.push({ role: 'assistant', _wfSynthetic: true,
          content: `**Lauf fehlgeschlagen**\n\n\`\`\`\n${data.error}\n\`\`\`` });
      } else if (rvTxt) {
        synth.push({ role: 'assistant', _wfSynthetic: true,
          content: `**Ergebnis des Workflow-Laufs**\n\n${rvTxt}` });
      } else if (!isLive) {
        synth.push({ role: 'assistant', _wfSynthetic: true,
          content: '_Dieser Lauf hat keinen Text erzeugt — Details im Protokoll-Reiter._' });
      }
    }
    // Live + unblocked but NO in-flight step turn yet (between steps, or the
    // very first tick): still show the status line + pause/resume/stop buttons
    // as a standalone message so the controls are always reachable.
    if (isLive && !pending && liveIdx === -1) {
      synth.push({
        role: 'assistant', _wfSynthetic: true,
        _wfControls: true, _wfPaused: paused,
        content: paused ? '_⏸ Workflow-Lauf pausiert_' : `_⏵ ${_wfRunStatusLine(data)}_`,
      });
    }
  }
  chat.messages = synth.concat(real);
}

/* Expand the live in-flight step (_wf_live turn, carrying _wf_events) into REAL
   chat message rows so the normal renderer draws it like any turn:
     - thinking segment → a `thinking` row
     - tool_call (+result) → a `tool_call` row + paired `tool_result` row
       (renderToolCall shows the args table + result block, ✓/spinner)
     - answer-text segment → an assistant content row
   The LAST row is the assistant status+controls line (so it's the turn's main
   response, carrying the pause/stop buttons — not folded into Aktivität). */
function _wfLiveRows(m, live, paused) {
  const rows = [];
  const events = Array.isArray(m && m._wf_events) ? m._wf_events : [];
  let seq = 0;
  let lastTextSeg = '';
  for (const ev of events) {
    seq += 1;
    if (ev.kind === 'thinking') {
      const t = (ev.text || '').trim();
      if (t) rows.push({ role: 'thinking', content: t, _wfSynthetic: true, _seq: seq });
    } else if (ev.kind === 'text') {
      const t = (ev.text || '').trim();
      // Trailing text after the last tool = the answer-in-progress; when the
      // turn is finalised the real `content` holds the full answer, so drop the
      // last streamed segment to avoid double text. Keep intermediate segments.
      lastTextSeg = t;
      if (t) rows.push({ role: 'assistant_segment', content: t, _wfSynthetic: true, _seq: seq });
    } else if (ev.kind === 'tool_call') {
      const tuid = ev.tool_use_id || `wflive-${seq}`;
      rows.push({ role: 'tool_call', name: ev.name, args: ev.args || {},
        tool_use_id: tuid, _wfSynthetic: true, _seq: seq,
        duration_ms: (typeof ev.duration_ms === 'number' ? ev.duration_ms : undefined) });
      if (ev._done) {
        rows.push({ role: 'tool_result', name: ev.name, tool_use_id: tuid,
          result: ev.result, is_error: !!ev.is_error, _wfSynthetic: true, _seq: seq + 0.5 });
      }
      lastTextSeg = '';
    }
  }
  if (live) {
    // LIVE: trailing status + controls line = the turn's LAST assistant response.
    rows.push({
      role: 'assistant', _wfSynthetic: true, _wfControls: true, _wfPaused: paused,
      content: paused ? '_⏸ Workflow-Lauf pausiert_'
        : `_⏵ ${_wfRunStatusLine(wfBanner.data)}_`,
    });
  } else {
    // FINALISED: the answer text (m.content) is the LAST assistant response.
    // If it equals the last streamed segment, drop that segment row to avoid a
    // duplicate (pop it — it was the answer-in-progress).
    const answer = typeof m.content === 'string' ? m.content : '';
    if (answer && lastTextSeg && answer.startsWith(lastTextSeg) && rows.length
        && rows[rows.length - 1].role === 'assistant_segment') {
      rows.pop();
    }
    const files = Array.isArray(m.files) ? m.files.filter(Boolean) : [];
    const arow = { role: 'assistant', content: answer, _wfSynthetic: true, _wfModel: m.model || '' };
    if (files.length) arow._files = files.map(f => ({ path: f, action: 'created' }));
    rows.push(arow);
  }
  return rows;
}

/* Human-readable "which step is running" line. The backend labels the live
   turn (_wf_label) with the step's own instruction excerpt (set by agent_step),
   so the user sees WHERE in the workflow they are — plus the current step's
   DSL line number from the run steps for orientation. */
function _wfRunStatusLine(data) {
  const label = ((data && data.transcript) || [])
    .filter(x => x && x._wf_live).map(x => x._wf_label).filter(Boolean).pop();
  // Current agent_step line (for a "Schritt Zeile N" prefix).
  let line = 0;
  const steps = _wfSteps(data);
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s && s.kind === 'call' && /agent_step\(/.test(s.detail || '')) { line = s.line; break; }
  }
  if (label && line) return `Schritt (Zeile ${line}): ${label}`;
  if (label) return label;
  if (line) return `Führt Workflow-Schritt in Zeile ${line} aus …`;
  return 'Workflow-Lauf in Bearbeitung …';
}

/* ── Upload prompt: shown when a live run blocks on ask_user_for_file.
   The host (#wf-upload-host) is a STABLE element in the chat-input-area (see
   index.html) — renderMessages() never touches it, so the file <input>
   survives poll re-renders and the OS file dialog stays bound to a live input.
   The card's inner HTML is rebuilt ONLY when the prompt string changes (poll
   ticks with the same pending prompt are no-ops), so a picked file / open
   dialog is never clobbered. While blocked, the composer is hidden — the user
   shouldn't chat mid-workflow. */
function _wfRenderRunControls() {
  const host = document.getElementById('wf-upload-host');
  if (!host) return;
  const data = _wfRunActive() ? wfBanner.data : null;
  const live = !!(data && WF_LIVE_STATUSES.has(data.status || ''));
  const pending = live ? _wfPendingUpload(data) : null;
  // Upload card in the stable host ONLY while blocked on ask_user_for_file.
  // (Pause/resume/stop live inline in the chat status message; composer
  // visibility is handled centrally in renderWorkflowRunUI.)
  if (pending) {
    host.classList.remove('hidden');
    const key = `upload:${pending.prompt} ${pending.accept}`;
    if (host.dataset.wfKey === key) return;   // same prompt already rendered — leave the live input alone
    host.dataset.wfKey = key;
    wfRenderUploadPrompt(host, pending.prompt, pending.accept);
    return;
  }
  if (host.dataset.wfKey) { host.innerHTML = ''; host.classList.add('hidden'); delete host.dataset.wfKey; }
}

// Inline pause/resume + stop buttons appended to the workflow status message in
// the chat (rendered by renderAssistantMessage when msg._wfControls is set).
function wfRunControlsHtml(paused) {
  const toggle = paused
    ? `<button class="wf-msg-ctrl" onclick="wfRunResume()" title="Lauf fortsetzen">
         <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Fortsetzen</button>`
    : `<button class="wf-msg-ctrl" onclick="wfRunPause()" title="Lauf pausieren (am nächsten Schritt)">
         <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg> Pause</button>`;
  return `
    <div class="wf-msg-controls">
      ${toggle}
      <button class="wf-msg-ctrl wf-msg-ctrl-stop" onclick="wfBannerCancel()" title="Workflow-Lauf abbrechen">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg> Stopp</button>
    </div>`;
}

async function wfRunPause() {
  const id = wfBanner.execId;
  if (!id) return;
  try {
    const r = await API.post(`/v1/workflows/executions/${id}/pause`, {});
    if (r && r.error) { await showAlert('Pausieren fehlgeschlagen: ' + r.error); return; }
    wfBannerFetch(false);
  } catch (e) { await showAlert('Pause-Fehler: ' + e.message); }
}

async function wfRunResume() {
  const id = wfBanner.execId;
  if (!id) return;
  try {
    const r = await API.post(`/v1/workflows/executions/${id}/resume`, {});
    if (r && r.error) { await showAlert('Fortsetzen fehlgeschlagen: ' + r.error); return; }
    wfBannerFetch(false);
  } catch (e) { await showAlert('Resume-Fehler: ' + e.message); }
}


// Hide/show the chat composer + disclaimer while a workflow run is active.
// Chatting mid-run is neither needed nor safe, and while blocked on an upload
// the card takes the composer's place. Toggles the mount + disclaimer only
// (leaves the input-area wrapper so the upload host stays visible).
function _wfSetComposerHidden(hidden) {
  const mount = document.getElementById('chat-composer-mount');
  if (mount) mount.classList.toggle('hidden', hidden);
  const area = document.querySelector('.chat-input-area');
  const disc = area && area.querySelector('.chat-disclaimer');
  if (disc) disc.classList.toggle('hidden', hidden);
}

/* ── Right-panel tab: Statistik (stats + run actions) ── */
function renderWorkflowStatistikPane() {
  const pane = document.getElementById('wf-statistik-content');
  if (!pane) return;
  const data = wfBanner.data;
  if (!data) { pane.innerHTML = '<div class="wf-hist-empty">Keine Laufdaten.</div>'; return; }
  const wfId = wfBanner.execId || '';
  const status = data.status || 'unbekannt';
  const isLive = WF_LIVE_STATUSES.has(status);
  const dur = _fmtDuration(data.duration_ms);
  const cost = (data.cost_usd != null) ? '$' + Number(data.cost_usd).toFixed(4) : '';
  const tokIn = Number(data.tokens_in || 0);
  const tokOut = Number(data.tokens_out || 0);
  const tokens = (tokIn || tokOut)
    ? `${tokIn.toLocaleString()} ein / ${tokOut.toLocaleString()} aus`
    : '';
  const started = _fmtTs(data.started_at);
  const finished = _fmtTs(data.finished_at);
  // Live runs (/executions) carry `agent`; the persisted history row carries
  // `agent_id`. Accept either so the pane fills during the run too.
  const agent = data.agent_id || data.agent || '';
  const model = data.model || '—';
  const promoted = (state.activeChat && state.activeChat.status) === 'active';
  const row = (label, val) => val
    ? `<div class="wf-stat-row"><span class="wf-stat-label">${escapeHtml(label)}</span><span class="wf-stat-val">${val}</span></div>`
    : '';
  pane.innerHTML = `
    <div class="wf-stat-head">
      <div class="wf-stat-name">${escapeHtml(data.workflow_name || 'Workflow')}</div>
      <span class="wf-status-${status} wf-stat-status">${escapeHtml(status)}</span>
    </div>
    <div class="wf-stat-grid">
      ${row('Agent', agent ? `<strong>${escapeHtml(agent)}</strong>` : '')}
      ${row('Modell', `<strong>${escapeHtml(model)}</strong>`)}
      ${row('Gestartet', escapeHtml(started))}
      ${row('Beendet', escapeHtml(finished))}
      ${row('Dauer', escapeHtml(dur))}
      ${row('Kosten', escapeHtml(cost))}
      ${row('Benutzer', data.user_display ? escapeHtml(data.user_display) : '')}
      ${row('Tool-Aufrufe', data.tool_calls != null ? escapeHtml(String(data.tool_calls)) : '')}
      ${row('LLM-Aufrufe', data.llm_calls != null ? escapeHtml(String(data.llm_calls)) : '')}
      ${row('Token', escapeHtml(tokens))}
      ${row('Ausführungs-ID', `<code class="wf-stat-execid" title="${escapeHtml(wfId)}">${escapeHtml(wfId)}</code>`)}
    </div>
    <div class="wf-stat-actions">
      ${isLive ? '<button class="wf-btn wf-btn-ghost" onclick="wfBannerCancel()">Lauf abbrechen</button>' : ''}
      <button class="wf-btn wf-btn-ghost" onclick="wfDetailDownloadTranscript()" title="Markdown-Protokoll dieses Laufs herunterladen">Protokoll herunterladen</button>
      ${promoted
        ? '<button class="wf-btn wf-btn-ghost" disabled title="Dieser Lauf ist als Chat gespeichert">✓ Gespeichert</button>'
        : '<button class="wf-btn wf-btn-primary" onclick="wfBannerSaveToChats()">In Chats speichern</button>'}
      <button class="wf-btn wf-btn-ghost" onclick="wfBannerBack()" title="Zurück zur Workflow-Liste">← Workflows</button>
    </div>`;
}

/* ── Right-panel tab: Quellcode (the .flow source) ── */
function renderWorkflowQuellcodePane() {
  const pane = document.getElementById('wf-quellcode-content');
  if (!pane) return;
  const src = (wfBanner.data && wfBanner.data.workflow_source || '').trim();
  pane.innerHTML = src
    ? `<pre class="wf-source-pre">${escapeHtml(src)}</pre>`
    : '<div class="wf-hist-empty">Kein Quellcode gespeichert.</div>';
}

/* ── Right-panel tab: Protokoll (step-by-step execution log) ── */
function renderWorkflowProtokollPane() {
  const pane = document.getElementById('wf-protokoll-content');
  if (!pane) return;
  const data = wfBanner.data;
  if (!data) { pane.innerHTML = '<div class="wf-hist-empty">Kein Protokoll.</div>'; return; }
  const steps = _wfSteps(data);
  let html = '';
  let i = 0;
  while (i < steps.length) {
    const s = steps[i];
    const kind = s.kind || '';
    if (kind === 'call') {
      const next = steps[i + 1];
      const paired = next && next.kind === 'call_done' ? next : null;
      const callHtml = _wfMaybeCollapsed(s.detail || '', { tag: 'div', cls: 'wf-banner-trace-call' });
      const resultHtml = paired
        ? _wfMaybeCollapsed(paired.detail || '(keine Ausgabe)', { tag: 'div', cls: 'wf-banner-trace-result' })
        : '<div class="wf-banner-trace-result wf-detail-pending">…</div>';
      html += `
        <div class="wf-banner-trace-card wf-banner-trace-tool">
          <div class="wf-banner-trace-card-label">Tool · L${escapeHtml(String(s.line))}</div>
          ${callHtml}
          ${resultHtml}
        </div>`;
      i += paired ? 2 : 1;
      continue;
    }
    if (kind === 'call_done') { i += 1; continue; }
    if (kind === 'error') {
      html += `
        <div class="wf-banner-trace-card wf-banner-trace-error">
          <div class="wf-banner-trace-card-label">Fehler · L${escapeHtml(String(s.line))}</div>
          ${_wfMaybeCollapsed(s.detail || '', { tag: 'div', cls: 'wf-banner-trace-result' })}
        </div>`;
      i += 1; continue;
    }
    html += `
      <div class="wf-banner-trace-step">
        <span class="wf-banner-step-kind">${escapeHtml(kind)}</span>
        <span class="wf-banner-step-line">L${escapeHtml(String(s.line))}</span>
        <span class="wf-banner-step-detail">${escapeHtml(s.detail || '')}</span>
      </div>`;
    i += 1;
  }
  let rv = data.return_value;
  if (typeof rv === 'string' && rv) { try { rv = JSON.parse(rv); } catch (_) {} }
  if (rv !== null && rv !== undefined && rv !== '') {
    const txt = typeof rv === 'string' ? rv : JSON.stringify(rv, null, 2);
    html += `
      <div class="wf-banner-trace-card wf-banner-trace-final">
        <div class="wf-banner-trace-card-label">Rückgabewert</div>
        ${_wfMaybeCollapsed(txt, { tag: 'pre', cls: 'wf-banner-final-value' })}
      </div>`;
  } else if (data.error) {
    html += `
      <div class="wf-banner-trace-card wf-banner-trace-error">
        <div class="wf-banner-trace-card-label">Fehlgeschlagen</div>
        ${_wfMaybeCollapsed(data.error, { tag: 'pre', cls: 'wf-banner-final-value' })}
      </div>`;
  }
  pane.innerHTML = html || '<div class="wf-hist-empty">Keine Schritte aufgezeichnet.</div>';
}

// Show the workflow-only right-panel tabs (Statistik / Quellcode / Protokoll)
// only when the active chat is a workflow run; hide them otherwise. Added to
// the normal chat tab set — the standard tabs (Anhänge/Referenzen/Artefakte/
// …) stay visible so a follow-up conversation works exactly like a normal
// chat. Called from openRightPanel/refreshRightPanelContent + on run (de)-
// activation.
const WF_TAB_NAMES = ['wf-statistik', 'wf-quellcode', 'wf-protokoll'];
function updateWorkflowTabs() {
  const active = _wfRunActive();
  WF_TAB_NAMES.forEach(t => {
    const btn = document.querySelector(`.right-panel-tab[data-tab="${t}"]`);
    if (btn) btn.style.display = active ? '' : 'none';
  });
  // If a workflow tab was open but the run just deactivated, fall back to a
  // sensible normal tab so we don't leave an empty hidden pane showing.
  if (!active && WF_TAB_NAMES.includes(state.rightPanelTab)) {
    if (typeof switchRightTab === 'function') switchRightTab('attachments');
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
        <button class="wf-btn wf-btn-ghost wf-upload-cancel" onclick="wfBannerCancel()" title="Den gesamten Workflow-Lauf abbrechen">Abbrechen</button>
        <button class="wf-btn wf-btn-ghost" onclick="wfResetUpload()" title="Dateiauswahl zurücksetzen">Zurücksetzen</button>
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

// Reset the upload card's file selection (the "Zurücksetzen" button) — clears
// the picked file + any inline error, re-disabling the submit button. Does NOT
// cancel the run (that's the "Abbrechen" button → wfBannerCancel).
function wfResetUpload() {
  const input = document.getElementById('wf-upload-input');
  if (input) { input.value = ''; }
  const submit = document.getElementById('wf-upload-submit');
  if (submit) { submit.disabled = true; submit.textContent = 'Hochladen'; }
  wfOnFilePicked();
}

async function wfUploadFile() {
  const id = wfBanner.execId;
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
    // Upload accepted — the workflow unblocks server-side. Drop the card now;
    // the next poll tick re-renders the run (which will no longer be pending).
    const host = document.getElementById('wf-upload-host');
    if (host) host.remove();
    wfBannerFetch(false);
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
