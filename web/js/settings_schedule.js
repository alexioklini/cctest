// settings_schedule.js — scheduled tasks: forms, views, run history, thinking-level helpers, attachments, folder picker. Split from settings.js (Tier F Phase 2). Global <script>, no modules.

// --- Schedule tab helpers ---
function _schedShowForm() {
  const f = document.getElementById('sched-form');
  if (f) f.style.display = 'block';
}

// Inline AI refinement controls for scheduled-task prompt textareas.
// Caveman picker lives here because a scheduled task has no chat-level
// caveman toggle to inherit from — the picker IS the equivalent setting
// for that task's prompt. Anywhere else (profile fields, long-form
// profile editor) the chat composer's caveman is the single setting.
function _schedRefineControls(textareaId) {
  return `
    <span style="display:inline-flex;align-items:center;gap:6px">
      ${_refineCavemanButton(textareaId)}
      <button type="button" id="${textareaId}-refine"
        onclick="refineSchedPrompt('${textareaId}')"
        title="Diesen Prompt für die geplante Aufgabe mit KI verfeinern"
        style="background:none;border:1px solid var(--border-light);border-radius:4px;padding:2px 8px;font-size:11px;color:var(--text-300);cursor:pointer;display:inline-flex;align-items:center;gap:4px">
        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
          <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
          <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
        </svg>
        <span id="${textareaId}-refine-label">Mit KI verfeinern</span>
      </button>
    </span>`;
}

async function refineSchedPrompt(textareaId) {
  const ta = document.getElementById(textareaId);
  if (!ta) return;
  const text = (ta.value || '').replace(/^\s+|\s+$/g, '');
  if (!text) { showToast('Geben Sie zuerst den Prompt ein', true); return; }
  const btn = document.getElementById(textareaId + '-refine');
  const lbl = document.getElementById(textareaId + '-refine-label');
  const caveman = _refineCavemanValue(textareaId);
  const origLabel = lbl?.textContent || 'Mit KI verfeinern';
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Verfeinere…';
  ta.disabled = true;
  const original = ta.value;
  try {
    // Default purpose=chat_prompt — same rewrite rules as the composer
    // refine button, since a scheduled task prompt is a chat-style prompt
    // the agent will execute. No session_id (no history context).
    const result = await API.post('/v1/refine', { text, caveman });
    if (result && result.refined && result.refined !== text) {
      ta.value = result.refined;
      if (lbl) lbl.textContent = 'Rückgängig';
      if (btn) {
        btn.disabled = false;
        const undoHandler = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          ta.value = original;
          if (lbl) lbl.textContent = origLabel;
          btn.removeEventListener('click', undoHandler);
          btn.onclick = () => refineSchedPrompt(textareaId);
        };
        btn.onclick = undoHandler;
      }
      showToast('Verfeinert — zum Zurücksetzen auf Rückgängig klicken');
    } else {
      showToast('Bereits sauber — keine Änderung');
      if (lbl) lbl.textContent = origLabel;
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('Verfeinern fehlgeschlagen: ' + (e.message || e), true);
    if (lbl) lbl.textContent = origLabel;
    if (btn) btn.disabled = false;
  } finally {
    ta.disabled = false;
  }
}

async function _schedAdd() {
  const agentId = window._schedAgentId;
  if (!agentId) return;
  const name = document.getElementById('sched-f-name')?.value?.trim();
  const task = document.getElementById('sched-f-task')?.value?.trim();
  const schedule = document.getElementById('sched-f-schedule')?.value?.trim();
  const model = document.getElementById('sched-f-model')?.value?.trim() || undefined;
  const timeout = parseInt(document.getElementById('sched-f-timeout')?.value) || 300;
  if (!name || !task || !schedule) { showToast('Name, Aufgabe und Zeitplan sind erforderlich', true); return; }
  try {
    const res = await API.manageSchedule({ action: 'add', name, task, schedule, agent: agentId, model, timeout });
    if (res.error) { showToast(res.error, true); return; }
    showToast('Aufgabe erstellt');
    switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _schedToggle(name, enable) {
  const agentId = window._schedAgentId;
  try {
    await API.manageSchedule({ action: enable ? 'resume' : 'pause', name });
    showToast(enable ? 'Fortgesetzt' : 'Pausiert');
    switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _schedDelete(name) {
  if (!await showConfirmDanger(`Geplante Aufgabe „${name}" löschen?`, 'Aufgabe löschen', 'Löschen')) return;
  const agentId = window._schedAgentId;
  try {
    await API.manageSchedule({ action: 'delete', name });
    showToast('Gelöscht');
    switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _schedCancel(name) {
  try {
    await API.cancelScheduledTask(name);
    showToast('Wird abgebrochen…');
    setTimeout(() => {
      const agentId = window._schedAgentId;
      if (agentId) switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
    }, 1000);
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _schedHistory(name) {
  const panel = document.getElementById('sched-history');
  const title = document.getElementById('sched-history-title');
  const body = document.getElementById('sched-history-body');
  if (!panel || !body) return;
  title.textContent = `Verlauf: ${name}`;
  body.innerHTML = '<div style="color:var(--text-400)">Lädt…</div>';
  panel.style.display = 'block';
  try {
    const res = await API.manageSchedule({ action: 'history', name, limit: 20 });
    const history = res.history || [];
    if (!history.length) { body.innerHTML = '<div style="color:var(--text-400)">Kein Ausführungsverlauf</div>'; return; }
    let html = '';
    for (const h of history) {
      const ok = h.status === 'success' || h.status === 'completed';
      const started = h.started_at ? new Date(h.started_at+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
      const finished = h.finished_at ? new Date(h.finished_at+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
      html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-100)">
        <span style="width:6px;height:6px;border-radius:50%;background:${ok?'var(--success)':'var(--error)'};flex-shrink:0"></span>
        <span style="flex:1;color:var(--text-200)">${started}</span>
        <span style="color:var(--text-400)">${esc(h.status||'')}</span>
      </div>`;
    }
    body.innerHTML = html;
  } catch(e) { body.innerHTML = `<div style="color:var(--error)">${esc(e.message)}</div>`; }
}

// ═══ Geplante-Aufgaben-Ansicht ═══

let _scheduledFilter = 'all';

async function loadScheduledView() {
  const container = document.getElementById('scheduled-list');
  if (!container) return;
  container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-400)">Lädt…</div>';
  try {
    const schedData = await API.getSchedule();
    const schedules = schedData.schedules || [];
    const running = schedData.running || [];
    const runningNames = new Set(running.map(r => r.name));

    let html = '';

    const filteredSchedules = schedules.filter(s => {
      if (_scheduledFilter === 'running') return runningNames.has(s.name);
      return true; // 'all' or 'scheduled'
    });

    for (const s of filteredSchedules) {
      const isRunning = runningNames.has(s.name);
      const enabled = s.enabled !== 0;
      const badgeClass = isRunning ? 'running' : (enabled ? 'enabled' : 'paused');
      const badgeText = isRunning ? 'Läuft' : (enabled ? 'Aktiv' : 'Pausiert');
      const nextRun = s.next_run ? new Date(s.next_run + (s.next_run.includes('Z') ? '' : 'Z')).toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
      const lastRun = s.last_run ? new Date(s.last_run + (s.last_run.includes('Z') ? '' : 'Z')).toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : 'nie';
      const cardId = 'sched-card-' + btoa(s.name).replace(/[^a-zA-Z0-9]/g,'').slice(0,12);
      html += `<div class="sched-card" id="${cardId}">
        <div class="sched-card-header">
          <span class="sched-card-fav-slot" data-sched-fav="${esc(s.name)}"></span>
          <span class="sched-card-name">${esc(s.name)}</span>
          <span class="sched-card-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="sched-card-prompt">${esc(s.task || '')}</div>
        <div class="sched-card-meta">
          <span>Agent: ${esc(s.agent || 'main')}</span>
          <span>${esc(s.schedule || '')}</span>
          <span>Nächste: ${nextRun}</span>
          <span>Letzte: ${lastRun}</span>
        </div>
        <div class="sched-card-actions">
          ${isRunning
            ? `<button onclick="_schedViewCancel('${esc(s.name)}')">Abbrechen</button>`
            : `<button onclick="_schedViewRun('${esc(s.name)}')">Jetzt ausführen</button>`}
          <button onclick="_schedViewToggle('${esc(s.name)}', ${!enabled})">${enabled ? 'Pausieren' : 'Fortsetzen'}</button>
          <button onclick="_schedViewEdit('${esc(s.name)}')">Bearbeiten</button>
          <button onclick="shareDialog('schedule','${esc(s.name)}','',{title:'${esc(s.name)}',onChange:loadScheduledView})">Teilen</button>
          <button class="danger" onclick="_schedViewDelete('${esc(s.name)}')">Löschen</button>
        </div>
        <details class="sched-runs" ontoggle="if(this.open) _schedLoadRuns('${esc(s.name)}', this)">
          <summary><span>Ausführungsverlauf</span></summary>
          <div class="runs-body"><div class="sched-run-row loading">Zum Laden klicken…</div></div>
        </details>
      </div>`;
    }

    if (!html) {
      html = '<div style="padding:40px;text-align:center;color:var(--text-400)">Keine geplanten Aufgaben. Klicken Sie auf „Neue Aufgabe", um eine zu erstellen.</div>';
    }
    container.innerHTML = html;
    // Mount the star button in each schedule card.
    if (window.Favourites?.mount) {
      container.querySelectorAll('.sched-card-fav-slot').forEach(slot => {
        const name = slot.dataset.schedFav;
        if (!name) return;
        window.Favourites.mount(slot, {
          item_type: 'schedule',
          item_id: name,
          agent_id: 'main',
        });
      });
    }
  } catch(e) {
    container.innerHTML = `<div style="padding:20px;color:var(--error)">Laden fehlgeschlagen: ${esc(e.message)}</div>`;
  }
}

function setScheduledFilter(filter, el) {
  _scheduledFilter = filter;
  document.querySelectorAll('.scheduled-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  loadScheduledView();
}

// Persistent schedule actions from the Scheduled view
async function _schedViewRun(name) {
  try {
    // Run now = delete and re-add with same config but "once" schedule for now
    // Actually, simplest: just trigger via the existing scheduler run mechanism
    // For now, we'll use the schedule modify to set next_run to now
    showToast('Aufgabe wird ausgeführt…');
    // Trigger by cancelling and immediately re-scheduling isn't ideal.
    // Better approach: add a "run now" action to the scheduler
    await API.manageSchedule({ action: 'run_now', name });
    showToast('Aufgabe ausgelöst');
    setTimeout(loadScheduledView, 1000);
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _schedViewToggle(name, enable) {
  try {
    await API.manageSchedule({ action: enable ? 'resume' : 'pause', name });
    showToast(enable ? 'Fortgesetzt' : 'Pausiert');
    loadScheduledView();
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _schedViewCancel(name) {
  try {
    await API.cancelScheduledTask(name);
    showToast('Wird abgebrochen…');
    setTimeout(loadScheduledView, 1000);
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _schedViewDelete(name) {
  if (!await showConfirmDanger(`Geplante Aufgabe „${name}" löschen?`, 'Aufgabe löschen', 'Löschen')) return;
  try {
    await API.manageSchedule({ action: 'delete', name });
    showToast('Gelöscht');
    loadScheduledView();
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

// Inline accordion body: fetch the last N runs for a schedule and render
// them as clickable rows with Open (loads the read-only chat view) and
// Delete. Called on first <details> toggle per card.
async function _schedLoadRuns(name, detailsEl) {
  const body = detailsEl.querySelector('.runs-body');
  if (!body) return;
  if (detailsEl._loaded) return;
  body.innerHTML = '<div class="sched-run-row loading">Ausführungsverlauf wird geladen…</div>';
  try {
    const res = await API.manageSchedule({ action: 'history', name, limit: 30 });
    const history = res.history || [];
    detailsEl._loaded = true;
    _schedRenderRuns(body, name, history);
  } catch(e) {
    body.innerHTML = `<div class="sched-run-row loading" style="color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</div>`;
  }
}

function _schedRenderRuns(body, name, history) {
  if (!history.length) {
    body.innerHTML = '<div class="sched-run-row loading">Noch keine Ausführungen</div>';
    return;
  }
  let html = '';
  for (const h of history) {
    const ok = h.status === 'success' || h.status === 'completed';
    const running = h.status === 'running';
    const started = h.started_at ? new Date(h.started_at+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
    const statusColor = running ? '#3b82f6' : (ok ? '#10b981' : (h.status === 'timeout' ? '#f59e0b' : '#ef4444'));
    const durSec = (h.duration_ms != null) ? (h.duration_ms / 1000) : null;
    const durLabel = durSec == null ? '—' : (durSec < 1 ? `${Math.round(h.duration_ms)}ms` : (durSec < 60 ? `${durSec.toFixed(1)}s` : `${Math.floor(durSec/60)}m${Math.round(durSec%60).toString().padStart(2,'0')}s`));
    const toolLabel = (h.tool_calls == null) ? '—' : String(h.tool_calls);
    const agentId = h.agent || 'main';
    const runId = h.id;
    const deleteDisabled = running ? 'disabled title="Laufende Aufgabe kann nicht gelöscht werden"' : '';
    html += `<div class="sched-run-row" data-run-id="${runId}">
      <span class="r-dot" style="background:${statusColor}"></span>
      <span class="r-time">${started}</span>
      <span class="r-dur">${durLabel}</span>
      <span class="r-tools">${toolLabel} Tools</span>
      <span class="r-status" style="color:${statusColor}">${esc(h.status||'?')}</span>
      <span class="r-actions">
        <button onclick="openScheduledArtifact(${runId}, 'sched-${runId}', '${esc(agentId)}', null)">Öffnen</button>
        <button onclick="_schedViewRunDetail(${runId})">Details</button>
        <button class="danger" ${deleteDisabled} onclick="_schedDeleteRun('${esc(name)}', ${runId}, this)">Löschen</button>
      </span>
    </div>`;
  }
  html += `<div class="sched-runs-footer">
    <button class="danger" onclick="_schedClearHistory('${esc(name)}', this)">Gesamten Verlauf löschen</button>
  </div>`;
  body.innerHTML = html;
}

async function _schedDeleteRun(name, runId, btn) {
  if (!await showConfirmDanger(`Ausführung #${runId} löschen?\n\nDies entfernt die Verlaufszeile und jedes von dieser Ausführung erzeugte Artefakt (inkl. Dateien).`, 'Ausführung löschen', 'Löschen')) return;
  btn.disabled = true;
  try {
    const res = await API.manageSchedule({ action: 'delete_run', run_id: runId });
    if (res && res.error) { showToast(res.error, true); btn.disabled = false; return; }
    showToast(`Ausführung gelöscht · ${res.artifacts_removed||0} Artefakt(e) entfernt`);
    // Reload this card's history inline.
    const detailsEl = btn.closest('details.sched-runs');
    if (detailsEl) {
      detailsEl._loaded = false;
      _schedLoadRuns(name, detailsEl);
    }
  } catch(e) {
    showToast('Fehlgeschlagen: ' + e.message, true);
    btn.disabled = false;
  }
}

async function _schedClearHistory(name, btn) {
  if (!await showConfirmDanger(`GESAMTEN Ausführungsverlauf für „${name}" löschen?\n\nDies entfernt jede vergangene Ausführung und alle erzeugten Artefakte.\nDer Zeitplan selbst bleibt bestehen.`, 'Verlauf löschen', 'Alle löschen')) return;
  btn.disabled = true;
  try {
    const res = await API.manageSchedule({ action: 'clear_history', name });
    showToast(`Gelöscht · ${res.runs_removed||0} Ausführungen · ${res.artifacts_removed||0} Artefakte`);
    const detailsEl = btn.closest('details.sched-runs');
    if (detailsEl) {
      detailsEl._loaded = false;
      _schedLoadRuns(name, detailsEl);
    }
  } catch(e) {
    showToast('Fehlgeschlagen: ' + e.message, true);
    btn.disabled = false;
  }
}

async function _schedViewRunDetail(runId) {
  // Per-run deep view: start/finish times, duration, tools with timings,
  // artifacts produced, and the agent's result text. Pivots:
  //   schedule_history.id === run_id === session_id suffix
  //   trace_id or session_id → traces.db
  //   artifact_folder → agents/<id>/artifacts/<folder>/
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="width:720px;max-height:82vh;display:flex;flex-direction:column">
    <div id="sched-runstate" style="overflow:auto;flex:1">Lädt…</div>
    <div style="margin-top:12px;text-align:right;flex-shrink:0">
      <button onclick="this.closest('.sched-modal-overlay').remove()" style="padding:6px 16px;border-radius:8px;background:var(--bg-200);color:var(--text-200);font-size:13px">Schließen</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  try {
    const res = await API.manageSchedule({ action: 'run_detail', run_id: runId });
    if (res.error) {
      document.getElementById('sched-runstate').innerHTML = `<div style="color:var(--error)">${esc(res.error)}</div>`;
      return;
    }
    const run = res.run || {};
    const spans = res.spans || [];
    const artifacts = res.artifacts || [];
    const ok = run.status === 'success' || run.status === 'completed';
    const running = run.status === 'running';
    const statusColor = running ? '#3b82f6' : (ok ? '#10b981' : (run.status === 'timeout' ? '#f59e0b' : '#ef4444'));
    const started = run.started_at ? new Date(run.started_at+'Z').toLocaleString() : '—';
    const finished = run.finished_at ? new Date(run.finished_at+'Z').toLocaleString() : '—';
    const durSec = (run.duration_ms != null) ? (run.duration_ms / 1000) : null;
    const durLabel = durSec == null ? '—' : (durSec < 60 ? `${durSec.toFixed(1)}s` : `${Math.floor(durSec/60)}m${Math.round(durSec%60).toString().padStart(2,'0')}s`);

    // Tool span timeline — just tool_call spans, ordered by start.
    const toolSpans = spans.filter(s => s.type === 'tool_call').sort((a,b) => (a.started_at||'').localeCompare(b.started_at||''));
    const llmSpans = spans.filter(s => s.type === 'llm_call').sort((a,b) => (a.started_at||'').localeCompare(b.started_at||''));
    const tokensIn = spans.reduce((a,s) => a + (s.tokens_in||0), 0);
    const tokensOut = spans.reduce((a,s) => a + (s.tokens_out||0), 0);

    let html = `<h3 style="margin:0 0 4px;font-size:17px;color:var(--text-000)">Ausführung #${runId} · ${esc(run.schedule_name||'')}</h3>
    <div style="color:var(--text-400);font-size:12px;margin-bottom:12px"><span style="color:${statusColor};font-weight:500">${esc(run.status||'?')}</span> · ${esc(run.agent||'main')}${run.model ? ` · ${esc(run.model)}` : ''}</div>`;

    // Stats block
    html += `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px;padding:10px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Dauer</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${durLabel}</div></div>
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Tools</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${run.tool_calls ?? 0}</div></div>
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Tokens ein</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${tokensIn.toLocaleString()}</div></div>
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Tokens aus</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${tokensOut.toLocaleString()}</div></div>
    </div>`;

    // Times
    html += `<div style="font-size:12px;color:var(--text-300);margin-bottom:14px;font-family:var(--font-mono)">Gestartet ${esc(started)} · Beendet ${esc(finished)}</div>`;

    // Task prompt
    if (run.task) {
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px">Aufgabe</div><div style="padding:8px;background:var(--bg-100);border-radius:6px;font-size:13px;color:var(--text-200);white-space:pre-wrap">${esc(run.task)}</div></div>`;
    }

    // Tool timeline — each row expandable to show the full result summary
    // (which can expose, e.g., an exa_search returning an HTTP 400 with an
    // empty query, or a python_exec where the script's stdout reveals the
    // agent admitting it couldn't read an artifact).
    if (toolSpans.length) {
      const t0 = toolSpans[0].started_at ? new Date(toolSpans[0].started_at).getTime() : 0;
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Tool-Aufrufe (${toolSpans.length})</div>`;
      for (const s of toolSpans) {
        const sOk = s.status === 'success' || s.status === 'ok';
        const dotColor = sOk ? '#10b981' : '#ef4444';
        const dur = s.duration_ms != null ? `${s.duration_ms}ms` : '—';
        let meta = {};
        try { meta = s.metadata ? JSON.parse(s.metadata) : {}; } catch(e) {}
        const fullSummary = (meta.result_summary || '').toString();
        const shortSummary = fullSummary.length > 140 ? fullSummary.slice(0,140) + '…' : fullSummary;
        const tOffset = s.started_at && t0
          ? `+${((new Date(s.started_at).getTime() - t0)/1000).toFixed(1)}s`
          : '';
        const hasFull = fullSummary.length > 0;
        const sid = 'span-' + (s.id || Math.random().toString(36).slice(2));
        html += `<details style="border-bottom:1px dashed var(--border-100);padding:6px 0">
          <summary style="cursor:${hasFull?'pointer':'default'};list-style:none;display:flex;align-items:flex-start;gap:8px">
            <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};flex-shrink:0;margin-top:5px"></span>
            <div style="flex:1;min-width:0">
              <div style="font-size:13px;color:var(--text-200);font-family:var(--font-mono);display:flex;gap:8px;align-items:baseline">
                <span>${esc(s.name||'?')}</span>
                ${tOffset ? `<span style="font-size:10px;color:var(--text-500)">${esc(tOffset)}</span>` : ''}
              </div>
              ${shortSummary ? `<div style="font-size:11px;color:var(--text-400);margin-top:2px;font-family:var(--font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(shortSummary)}</div>` : ''}
            </div>
            <span style="font-size:11px;color:var(--text-400);font-family:var(--font-mono);flex-shrink:0;margin-top:4px">${dur}</span>
          </summary>
          ${hasFull ? `<pre style="margin:6px 0 6px 18px;padding:8px;background:var(--bg-100);border-radius:6px;font-size:11px;color:var(--text-300);white-space:pre-wrap;word-break:break-word;max-height:240px;overflow:auto">${esc(fullSummary)}</pre>` : ''}
        </details>`;
      }
      html += `</div>`;
    }

    // LLM rounds
    if (llmSpans.length) {
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">LLM-Runden (${llmSpans.length})</div>`;
      for (const s of llmSpans) {
        const dur = s.duration_ms != null ? `${s.duration_ms}ms` : '—';
        html += `<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:12px;color:var(--text-300);font-family:var(--font-mono)">
          <span>${esc(s.name||'?')}</span>
          <span>${s.tokens_in||0} in / ${s.tokens_out||0} out · ${dur}</span>
        </div>`;
      }
      html += `</div>`;
    }

    // Artifacts — each row opens the file in the artifact panel (plus the
    // pseudo-chat timeline view) when it has a registered artifact_id.
    // Unregistered fallback rows (from folder scan) stay non-clickable.
    if (artifacts.length) {
      const sessionId = res.session_id || ('sched-' + runId);
      const agentId = run.agent || 'main';
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Artefakte (${artifacts.length}) · <span style="font-family:var(--font-mono);text-transform:none">${esc(run.artifact_folder||'')}</span></div>`;
      for (const a of artifacts) {
        const kb = (a.size/1024).toFixed(1);
        const clickable = !!a.id;
        if (clickable) {
          html += `<div onclick="document.querySelector('.sched-modal-overlay').remove(); openArtifactFromBrowse('${esc(a.id)}', '${esc(sessionId)}', '${esc(agentId)}')" style="display:flex;justify-content:space-between;padding:6px 4px;font-size:12px;color:var(--accent-brand);font-family:var(--font-mono);cursor:pointer;border-radius:4px" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">
            <span>${esc(a.name)}</span><span style="color:var(--text-400)">${kb} KB${a.type ? ' · ' + esc(a.type) : ''}</span>
          </div>`;
        } else {
          html += `<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:12px;color:var(--text-400);font-family:var(--font-mono)" title="Nicht registrierte Datei — kann nicht im Viewer geöffnet werden">
            <span>${esc(a.name)}</span><span>${kb} KB</span>
          </div>`;
        }
      }
      html += `</div>`;
    }

    // Result text
    if (run.result) {
      html += `<details style="margin-bottom:8px"><summary style="cursor:pointer;font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;padding:4px 0">Ergebnis (${run.result.length} Zeichen)</summary><pre style="margin-top:6px;padding:10px;background:var(--bg-100);border-radius:6px;font-size:12px;color:var(--text-200);white-space:pre-wrap;word-break:break-word;max-height:300px;overflow:auto">${esc(run.result)}</pre></details>`;
    }

    document.getElementById('sched-runstate').innerHTML = html;
  } catch(e) {
    document.getElementById('sched-runstate').innerHTML = `<div style="color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</div>`;
  }
}

// Format-aware option set for the per-task / per-model thinking dropdown.
// modelId='' or unknown → caller decides whether to render a generic set
// (schedule modal "Default model") or hide the control entirely.
//
// Returns { format, supported, options:[{value,label}], note }.
//   format: the model's thinking_format, '' if unknown.
//   supported: false when format='none' — the control should be disabled or hidden.
//   options: ordered list (value strings) for a <select>.
//   note: optional one-liner the caller can show as a hint.
function _thinkingOptionsForModel(modelId) {
  const mc = state.modelsConfig?.models || {};
  const cfg = modelId ? (mc[modelId] || {}) : null;
  const fmt = cfg ? (cfg.thinking_format || 'none') : '';
  // Unknown model (e.g. schedule modal with model="" Default): full set,
  // resolved at fire time. Caller should add an "Inherit" entry on top.
  if (!modelId || !cfg) {
    return {
      format: '', supported: true,
      options: [
        {value:'none',   label:'Off'},
        {value:'low',    label:'Low'},
        {value:'medium', label:'Medium'},
        {value:'high',   label:'High'},
      ],
      note: "Die tatsächlichen Optionen hängen vom zur Ausführungszeit aufgelösten Modell ab.",
    };
  }
  if (fmt === 'none') {
    return {format: fmt, supported: false, options: [], note: "Dieses Modell unterstützt kein Reasoning."};
  }
  if (fmt === 'inline_tags') {
    // <think>...</think> models think unconditionally per turn; the only dial
    // most providers expose is on/off (chat_template_kwargs.enable_thinking on
    // oMLX-served Qwen3, etc.). No graduated levels.
    return {format: fmt, supported: true,
      options: [
        {value:'none', label:'Off'},
        {value:'high', label:'An'},
      ],
      note: "Dieses Modell unterstützt nur Denken an/aus — keine abgestuften Stufen."};
  }
  if (fmt === 'mistral_blocks') {
    // Mistral API only accepts reasoning_effort: none|high.
    return {format: fmt, supported: true,
      options: [
        {value:'none', label:'Off'},
        {value:'high', label:'High'},
      ],
      note: "Mistral akzeptiert nur Off / High."};
  }
  // reasoning_field (Gemini 2.5 / DeepSeek-R1 / oMLX), openai_opaque (o-series)
  return {format: fmt, supported: true,
    options: [
      {value:'none',   label:'Off'},
      {value:'low',    label:'Low'},
      {value:'medium', label:'Medium'},
      {value:'high',   label:'High'},
    ]};
}

// Format-driven option set, but for the Models tab (no "Inherit" entry —
// this dropdown IS the per-model default). Returns null when format='none'
// so the caller can disable the control.
function _thinkingOptionsForFormat(fmt) {
  if (fmt === 'none' || !fmt) return null;
  if (fmt === 'inline_tags') {
    return {options: [{value:'none',label:'Off'},{value:'high',label:'An'}],
            note: "Nur Denken an/aus — keine abgestuften Stufen."};
  }
  if (fmt === 'mistral_blocks') {
    return {options: [{value:'none',label:'Off'},{value:'high',label:'High'}],
            note: "Mistral akzeptiert nur Off / High."};
  }
  return {options: [
    {value:'none',label:'Off'},
    {value:'low',label:'Low'},
    {value:'medium',label:'Medium'},
    {value:'high',label:'High'},
  ]};
}

// Render a Models-tab Thinking Level <select> for a given format. selectedValue
// is the current saved level ('' = unset/inherit-API-default).
function _mdlPopulateThinkingLevel(fmt, levelSel, selectedValue) {
  if (!levelSel) return;
  const info = _thinkingOptionsForFormat(fmt);
  if (!info) {
    levelSel.innerHTML = `<option value="" selected>(nicht unterstützt)</option>`;
    levelSel.disabled = true;
    levelSel.title = "Dieses Modell unterstützt kein Reasoning.";
    return;
  }
  levelSel.disabled = false;
  levelSel.title = info.note || '';
  const allowed = new Set(['', ...info.options.map(o => o.value)]);
  const cur = allowed.has(selectedValue) ? selectedValue : '';
  // "(unset)" preserves the API default — same as deleting the key from
  // inference. Distinct from "Off" which sends thinking_level=none explicitly.
  const opts = [{value:'',label:'(nicht gesetzt)'}, ...info.options];
  levelSel.innerHTML = opts.map(o =>
    `<option value="${esc(o.value)}"${o.value === cur ? ' selected' : ''}>${esc(o.label)}</option>`
  ).join('');
}

// Called by the Models-tab Thinking Format <select> onchange. Finds the
// sibling level select in the same row and re-renders its options for the
// new format, preserving the user's level choice when still valid.
function _mdlRefreshThinkingLevel(fmtSelectEl) {
  // Format and level are both <div> children of the same flex row's grid
  // container. Find the level by class within the same parent grid.
  const grid = fmtSelectEl.closest('div[style*="grid-template-columns"]');
  const levelSel = grid?.querySelector('.mdl-thinking-level');
  if (!levelSel) return;
  _mdlPopulateThinkingLevel(fmtSelectEl.value || 'none', levelSel, levelSel.value || '');
}

// Re-render a schedule-modal thinking dropdown when the model selector
// changes. Includes the "Inherit from model" entry that's specific to
// scheduled tasks (model defaults win).
function _schedRefreshThinking(modelSelectId, thinkingSelectId, currentValue) {
  const modelSel = document.getElementById(modelSelectId);
  const ts = document.getElementById(thinkingSelectId);
  if (!ts) return;
  const modelId = modelSel?.value || '';
  const info = _thinkingOptionsForModel(modelId);
  // Preserve the user's prior choice when possible. If the new option set
  // doesn't include it, fall back to "" (Inherit).
  const prev = (currentValue !== undefined ? currentValue : ts.value) || '';
  const allowed = new Set(['', ...info.options.map(o => o.value)]);
  const keep = allowed.has(prev) ? prev : '';
  if (!info.supported) {
    ts.innerHTML = `<option value="" selected>(nicht unterstützt)</option>`;
    ts.disabled = true;
    ts.title = info.note || '';
    return;
  }
  ts.disabled = false;
  ts.title = info.note || '';
  const opts = [{value:'', label:'Vom Modell erben'}, ...info.options];
  ts.innerHTML = opts.map(o =>
    `<option value="${esc(o.value)}"${o.value === keep ? ' selected' : ''}>${esc(o.label)}</option>`
  ).join('');
}

async function _schedViewEdit(name) {
  // Inline edit modal: fetch the current task row, prefill form, PATCH via
  // action:'edit'. `schedule` is a free-form string (same format as create).
  let task;
  try {
    const data = await API.getSchedule();
    task = (data.schedules || []).find(s => s.name === name);
  } catch(e) { showToast('Laden fehlgeschlagen: ' + e.message, true); return; }
  if (!task) { showToast('Aufgabe nicht gefunden', true); return; }

  const agentOpts = (state.agents || []).map(a =>
    `<option value="${esc(a.id)}" ${a.id === task.agent ? 'selected' : ''}>${esc(a.display_name || a.id)}</option>`
  ).join('');
  const modelOpts = (state.models || []).filter(m => modelHasCapability(m, 'chat')).map(m =>
    modelOption(m, {selected: m === task.model, label: m})
  ).join('');

  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal">
    <h2>Bearbeiten: ${esc(name)}</h2>
    <div class="sched-form-group">
      <label>Name</label>
      <input id="sched-edit-newname" value="${esc(task.name || '')}">
    </div>
    <div class="sched-form-group">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <label style="margin:0">Prompt</label>
        ${_schedRefineControls('sched-edit-prompt')}
      </div>
      <textarea id="sched-edit-prompt">${esc(task.task || '')}</textarea>
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Agent</label>
        <select id="sched-edit-agent">${agentOpts}</select>
      </div>
      <div class="sched-form-group">
        <label>Modell</label>
        <select id="sched-edit-model" onchange="_schedRefreshThinking('sched-edit-model','sched-edit-thinking')"><option value="">Standard</option>${modelOpts}</select>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Zeitplan <span style="color:var(--text-400);font-weight:normal;font-size:11px">(z. B. „daily 09:00", „every 30m", „weekly mon 14:00", „once 2026-05-01 12:00")</span></label>
      <input id="sched-edit-schedule" value="${esc(task.schedule || '')}" style="font-family:var(--font-mono)">
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Timeout (Sekunden)</label>
        <input id="sched-edit-timeout" type="number" value="${task.timeout || 300}" min="30" max="3600">
      </div>
      <div class="sched-form-group">
        <label>Denkstufe <span style="color:var(--text-400);font-weight:normal;font-size:11px">(Reasoning-Aufwand)</span></label>
        <select id="sched-edit-thinking"></select>
      </div>
      <div class="sched-form-group">
        <label>Caveman-Modus <span style="color:var(--text-400);font-weight:normal;font-size:11px">(Antwortkomprimierung)</span></label>
        <select id="sched-edit-caveman">
          ${[[0,'Off'],[1,'Lite'],[2,'Full'],[3,'Ultra']].map(([v,lbl]) => {
            const sel = Number(task.caveman_chat || 0) === v ? 'selected' : '';
            return `<option value="${v}" ${sel}>${esc(lbl)}</option>`;
          }).join('')}
        </select>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Tool-Profil <span style="color:var(--text-400);font-weight:normal;font-size:11px">(welche Tools der Agent bei dieser Aufgabe sieht)</span></label>
      <select id="sched-edit-tool-profile">
        ${[
          ['','Standard — Research-minimal (exa_search, web_fetch, write_file)'],
          ['research_minimal','Research-minimal — wie Standard, explizit'],
          ['interactive','Interaktiv — volle Agent-Tool-Oberfläche (chat-ähnlich)'],
        ].map(([v,lbl]) => {
          const sel = (task.tool_profile || '') === v ? 'selected' : '';
          return `<option value="${esc(v)}" ${sel}>${esc(lbl)}</option>`;
        }).join('')}
      </select>
    </div>
    <div class="sched-form-group">
      <label>Arbeitsverzeichnis <span style="color:var(--text-400);font-weight:normal;font-size:11px">(optional)</span></label>
      <div style="display:flex;gap:6px">
        <input id="sched-edit-workdir" value="${esc(task.working_dir || '')}" placeholder="(keines)" readonly style="font-family:var(--font-mono);flex:1;background:var(--bg-100)">
        <button type="button" onclick="_schedOpenFolderPicker('sched-edit-workdir')" style="padding:6px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-200);cursor:pointer;font-size:12px">Durchsuchen…</button>
        <button type="button" onclick="document.getElementById('sched-edit-workdir').value=''" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-400);cursor:pointer;font-size:12px" title="Leeren">×</button>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Anhänge</label>
      <input id="sched-edit-files" type="file" multiple onchange="_schedUploadFiles(this, 'sched-edit-attlist')" style="font-size:12px">
      <div id="sched-edit-attlist" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px"></div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="_saveScheduledEdit('${esc(name)}')">Speichern</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  // Populate the thinking dropdown using the model the task is currently
  // bound to (or empty=Default → generic set with "Inherit" pre-selected).
  _schedRefreshThinking('sched-edit-model', 'sched-edit-thinking', task.thinking_level || '');
  // Seed the edit modal's attachment accumulator from the existing task row.
  try {
    const existing = task.attachments;
    let arr = [];
    if (Array.isArray(existing)) arr = existing;
    else if (typeof existing === 'string' && existing) arr = JSON.parse(existing);
    window._schedEditAttachments = arr || [];
  } catch(e) { window._schedEditAttachments = []; }
  _renderSchedAttList('sched-edit-attlist', '_schedEditAttachments');
}

async function _saveScheduledEdit(originalName) {
  const newName = document.getElementById('sched-edit-newname')?.value?.trim();
  const taskText = document.getElementById('sched-edit-prompt')?.value;
  const agent = document.getElementById('sched-edit-agent')?.value || 'main';
  const model = document.getElementById('sched-edit-model')?.value || '';
  const schedule = document.getElementById('sched-edit-schedule')?.value?.trim();
  const timeout = parseInt(document.getElementById('sched-edit-timeout')?.value) || 300;
  const thinking_level = document.getElementById('sched-edit-thinking')?.value || '';
  const caveman_chat = parseInt(document.getElementById('sched-edit-caveman')?.value) || 0;
  const tool_profile = document.getElementById('sched-edit-tool-profile')?.value || '';
  const working_dir = document.getElementById('sched-edit-workdir')?.value?.trim() || '';
  const attachments = window._schedEditAttachments || [];

  if (!newName) { showToast('Name ist erforderlich', true); return; }
  if (!taskText) { showToast('Prompt ist erforderlich', true); return; }
  if (!schedule) { showToast('Zeitplan ist erforderlich', true); return; }

  const payload = {
    action: 'edit',
    name: originalName,
    task: taskText,
    agent,
    schedule,
    timeout,
    thinking_level,
    caveman_chat,
    tool_profile,
    working_dir,
    attachments,
    // Empty string signals "clear back to Default" so the scheduler falls
    // back to the target agent's preferred_model at dispatch time. null
    // would mean "don't touch" in the server-side patch semantics.
    model: model,
  };
  if (newName !== originalName) payload.new_name = newName;

  try {
    const res = await API.manageSchedule(payload);
    if (res.error) { showToast(res.error, true); return; }
    showToast('Gespeichert');
    window._schedEditAttachments = [];
    document.querySelector('.sched-modal-overlay')?.remove();
    loadScheduledView();
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

// Create Scheduled Task modal
function showCreateScheduledModal() {
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

  // Build agent options
  const agentOpts = (state.agents || []).map(a =>
    `<option value="${esc(a.id)}" ${a.id === (state.activeAgentId || 'main') ? 'selected' : ''}>${esc(a.display_name || a.id)}</option>`
  ).join('');

  // Build model options — chat-capable only.
  const modelOpts = (state.models || []).filter(m => modelHasCapability(m, 'chat')).map(m =>
    `<option value="${esc(m)}">${esc(m)}</option>`
  ).join('');

  overlay.innerHTML = `<div class="sched-modal">
    <h2>Neue geplante Aufgabe</h2>
    <div class="sched-form-group">
      <label>Name</label>
      <input id="sched-new-name" placeholder="z. B. Tägliche PR-Prüfung">
    </div>
    <div class="sched-form-group">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <label style="margin:0">Prompt</label>
        ${_schedRefineControls('sched-new-prompt')}
      </div>
      <textarea id="sched-new-prompt" placeholder="Was soll der Agent tun?"></textarea>
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Agent</label>
        <select id="sched-new-agent">${agentOpts}</select>
      </div>
      <div class="sched-form-group">
        <label>Modell (optional)</label>
        <select id="sched-new-model" onchange="_schedRefreshThinking('sched-new-model','sched-new-thinking')"><option value="">Standard</option>${modelOpts}</select>
      </div>
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Häufigkeit</label>
        <select id="sched-new-freq" onchange="_schedFreqChanged()">
          <option value="every 1h">Stündlich</option>
          <option value="daily 09:00" selected>Täglich</option>
          <option value="weekly mon 09:00">Wöchentlich</option>
          <option value="custom">Benutzerdefiniert</option>
        </select>
      </div>
      <div class="sched-form-group">
        <label>Uhrzeit</label>
        <input id="sched-new-time" type="time" value="09:00">
      </div>
    </div>
    <div class="sched-form-group" id="sched-custom-row" style="display:none">
      <label>Benutzerdefinierter Zeitplan</label>
      <input id="sched-new-custom" placeholder="z. B. every 30m, daily 14:00, weekly fri 17:00" style="font-family:var(--font-mono)">
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Timeout (Sekunden)</label>
        <input id="sched-new-timeout" type="number" value="300" min="30" max="3600">
      </div>
      <div class="sched-form-group">
        <label>Denkstufe <span style="color:var(--text-400);font-weight:normal;font-size:11px">(Reasoning-Aufwand)</span></label>
        <select id="sched-new-thinking"></select>
      </div>
      <div class="sched-form-group">
        <label>Caveman-Modus <span style="color:var(--text-400);font-weight:normal;font-size:11px">(Antwortkomprimierung)</span></label>
        <select id="sched-new-caveman">
          <option value="0" selected>Off</option>
          <option value="1">Lite</option>
          <option value="2">Full</option>
          <option value="3">Ultra</option>
        </select>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Tool-Profil <span style="color:var(--text-400);font-weight:normal;font-size:11px">(welche Tools der Agent bei dieser Aufgabe sieht)</span></label>
      <select id="sched-new-tool-profile">
        <option value="" selected>Standard — Research-minimal (exa_search, web_fetch, write_file)</option>
        <option value="research_minimal">Research-minimal — wie Standard, explizit</option>
        <option value="interactive">Interaktiv — volle Agent-Tool-Oberfläche (chat-ähnlich)</option>
      </select>
    </div>
    <div class="sched-form-group">
      <label>Arbeitsverzeichnis <span style="color:var(--text-400);font-weight:normal;font-size:11px">(optional)</span></label>
      <div style="display:flex;gap:6px">
        <input id="sched-new-workdir" placeholder="(keines)" readonly style="font-family:var(--font-mono);flex:1;background:var(--bg-100)">
        <button type="button" onclick="_schedOpenFolderPicker('sched-new-workdir')" style="padding:6px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-200);cursor:pointer;font-size:12px">Durchsuchen…</button>
        <button type="button" onclick="document.getElementById('sched-new-workdir').value=''" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-400);cursor:pointer;font-size:12px" title="Leeren">×</button>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Anhänge <span style="color:var(--text-400);font-weight:normal;font-size:11px">(optional, bei jeder Ausführung in den Lauf kopiert)</span></label>
      <input id="sched-new-files" type="file" multiple onchange="_schedUploadFiles(this, 'sched-new-attlist')" style="font-size:12px">
      <div id="sched-new-attlist" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px"></div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="_createScheduledTask()">Erstellen</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  // Populate the thinking dropdown for the initially-selected model
  // (typically empty=Default → generic set with "Inherit" pre-selected).
  _schedRefreshThinking('sched-new-model', 'sched-new-thinking', '');
  // Attachments accumulator for the open modal. Cleared with the modal.
  window._schedNewAttachments = [];
}

// Upload picked files to /v1/schedule/upload and render chips. The chip
// list is the source of truth that's read at submit time.
async function _schedUploadFiles(input, listElId) {
  const files = Array.from(input.files || []);
  if (!files.length) return;
  const listEl = document.getElementById(listElId);
  // Decide which accumulator we're writing to based on the list element.
  const buckName = (listElId === 'sched-edit-attlist')
    ? '_schedEditAttachments' : '_schedNewAttachments';
  // Pick the agent the modal is targeting so the file lands under that
  // agent's scheduled_attachments folder.
  const agentSel = document.getElementById('sched-new-agent')
                || document.getElementById('sched-edit-agent');
  const agent = agentSel?.value || 'main';
  // Auth header is required — the global /v1/* gate rejects anonymous POST.
  // Don't set Content-Type; the browser inserts the multipart boundary.
  const token = localStorage.getItem('auth-token') || '';
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f, f.name);
    fd.append('agent', agent);
    try {
      const resp = await fetch(`${BASE_URL}/v1/schedule/upload`, {
        method: 'POST', headers, body: fd,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({error: `HTTP ${resp.status}`}));
        showToast('Upload fehlgeschlagen: ' + (err.error || resp.statusText), true);
        continue;
      }
      const meta = await resp.json();
      window[buckName] = window[buckName] || [];
      window[buckName].push(meta);
      _renderSchedAttList(listElId, buckName);
    } catch(e) {
      showToast('Upload fehlgeschlagen: ' + e.message, true);
    }
  }
  input.value = '';  // allow re-picking the same file
}

function _renderSchedAttList(listElId, buckName) {
  const listEl = document.getElementById(listElId);
  const items = window[buckName] || [];
  if (!listEl) return;
  if (!items.length) { listEl.innerHTML = ''; return; }
  listEl.innerHTML = items.map((m, i) => {
    const kb = (m.size/1024).toFixed(1);
    return `<span style="display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border:1px solid var(--border-100);border-radius:12px;background:var(--bg-100);font-size:11px;font-family:var(--font-mono);color:var(--text-200)" title="${esc(m.path||'')}">
      ${esc(m.name)} <span style="color:var(--text-400)">${kb}KB</span>
      <button onclick="_schedRemoveAtt('${esc(buckName)}', ${i}, '${esc(listElId)}')" style="border:none;background:transparent;color:var(--text-400);cursor:pointer;padding:0;font-size:14px;line-height:1">×</button>
    </span>`;
  }).join('');
}

function _schedRemoveAtt(buckName, idx, listElId) {
  const items = window[buckName] || [];
  items.splice(idx, 1);
  window[buckName] = items;
  _renderSchedAttList(listElId, buckName);
}

// --- Folder picker (server-side dir browser) ---
// Opens a modal stacked on top of the schedule modal. Navigates the server
// filesystem one level at a time via /v1/files/tree?path=X&depth=0. The
// selected absolute path is written to the input identified by targetInputId.
function _schedOpenFolderPicker(targetInputId) {
  const startVal = document.getElementById(targetInputId)?.value?.trim() || '';
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';  // above the schedule modal
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Arbeitsverzeichnis auswählen</h2>
    <div id="folder-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="folder-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" id="folder-picker-select" onclick="_schedFolderPickerSelect('${esc(targetInputId)}')">Diesen Ordner auswählen</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  _schedLoadFolder(startVal);  // empty string → server defaults to $HOME
}

async function _schedLoadFolder(path) {
  const crumbs = document.getElementById('folder-picker-crumbs');
  const list = document.getElementById('folder-picker-list');
  if (!crumbs || !list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-400);text-align:center">Lädt…</div>';
  try {
    const data = await API.get(`/v1/files/tree?path=${encodeURIComponent(path)}&depth=0`);
    if (data.error) { list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(data.error)}</div>`; return; }
    const cur = data.path || path || '/';
    window._schedPickerPath = cur;
    crumbs.textContent = cur;
    const dirs = (data.tree || []).filter(n => n.type === 'dir');
    const parent = (cur && cur !== '/') ? cur.replace(/\/[^\/]+\/?$/, '') || '/' : null;
    let html = '';
    if (parent !== null) {
      html += `<div onclick="_schedLoadFolder('${esc(parent)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-300)" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">↑ ..</div>`;
    }
    if (!dirs.length) {
      html += '<div style="padding:14px;color:var(--text-400);text-align:center;font-size:12px">(keine Unterordner)</div>';
    } else {
      for (const d of dirs) {
        html += `<div onclick="_schedLoadFolder('${esc(d.path)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-200);display:flex;align-items:center;gap:8px" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">
          <span style="color:var(--text-400)">📁</span>${esc(d.name)}
        </div>`;
      }
    }
    list.innerHTML = html;
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(e.message)}</div>`;
  }
}

function _schedFolderPickerSelect(targetInputId) {
  const path = window._schedPickerPath || '';
  if (!path) { showToast('Kein Ordner ausgewählt', true); return; }
  const inp = document.getElementById(targetInputId);
  if (inp) inp.value = path;
  // Close the topmost overlay (the picker), leaving the schedule modal open.
  const overlays = document.querySelectorAll('.sched-modal-overlay');
  if (overlays.length) overlays[overlays.length - 1].remove();
}

function _schedFreqChanged() {
  const freq = document.getElementById('sched-new-freq')?.value;
  const customRow = document.getElementById('sched-custom-row');
  const timeInput = document.getElementById('sched-new-time');
  if (freq === 'custom') {
    customRow.style.display = '';
    timeInput.parentElement.style.display = 'none';
  } else {
    customRow.style.display = 'none';
    timeInput.parentElement.style.display = '';
  }
}

async function _createScheduledTask() {
  const name = document.getElementById('sched-new-name')?.value?.trim();
  const task = document.getElementById('sched-new-prompt')?.value?.trim();
  const agent = document.getElementById('sched-new-agent')?.value || 'main';
  const model = document.getElementById('sched-new-model')?.value || undefined;
  const freq = document.getElementById('sched-new-freq')?.value;
  const timeVal = document.getElementById('sched-new-time')?.value || '09:00';
  const customSched = document.getElementById('sched-new-custom')?.value?.trim();
  const timeout = parseInt(document.getElementById('sched-new-timeout')?.value) || 300;
  const thinking_level = document.getElementById('sched-new-thinking')?.value || '';
  const caveman_chat = parseInt(document.getElementById('sched-new-caveman')?.value) || 0;
  const tool_profile = document.getElementById('sched-new-tool-profile')?.value || '';

  if (!name || !task) { showToast('Name und Prompt sind erforderlich', true); return; }

  let schedule;
  if (freq === 'custom') {
    schedule = customSched;
    if (!schedule) { showToast('Benutzerdefinierter Zeitplan ist erforderlich', true); return; }
  } else if (freq.startsWith('every')) {
    schedule = freq;
  } else if (freq.startsWith('daily')) {
    schedule = `daily ${timeVal}`;
  } else if (freq.startsWith('weekly')) {
    const day = freq.split(' ')[1] || 'mon';
    schedule = `weekly ${day} ${timeVal}`;
  }

  const working_dir = document.getElementById('sched-new-workdir')?.value?.trim() || '';
  const attachments = window._schedNewAttachments || [];
  try {
    const res = await API.manageSchedule({
      action: 'add', name, task, schedule, agent, model, timeout,
      thinking_level, caveman_chat, tool_profile,
      working_dir, attachments,
    });
    if (res.error) { showToast(res.error, true); return; }
    showToast('Aufgabe erstellt');
    window._schedNewAttachments = [];
    document.querySelector('.sched-modal-overlay')?.remove();
    loadScheduledView();
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

