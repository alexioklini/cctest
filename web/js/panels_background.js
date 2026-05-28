/* Hintergrundaufgaben — detached background tasks panel.
 *
 * Data: state.backgroundTasks[sessionId] = [{id,title,status,...}] (newest
 * first), fetched from GET /v1/background-tasks. A 2s poll runs while the
 * bgtasks tab is open AND at least one task is running (matches the screenshot:
 * a running task's counts tick while the chat is otherwise idle). The top-bar
 * pill shows the count of running + finished-not-yet-consumed tasks and opens
 * the panel on click.
 *
 * Globals (load order: after panels_right.js, before init.js):
 *   loadBackgroundTasks, renderBackgroundTasksPane, backgroundTasksActiveCount,
 *   refreshBackgroundTasksPill, startBackgroundTasksPoll, stopBackgroundTasksPoll,
 *   cancelBgTask, deleteBgTask, openBgTranscript
 */

let _bgPollHandle = null;
let _bgTranscriptCtrl = null;

function _bgTasksFor(sessionId) {
  if (!state.backgroundTasks) state.backgroundTasks = {};
  return state.backgroundTasks[sessionId] || [];
}

// Count worth a badge/pill: running, or finished-but-not-yet-pulled-into-chat.
function backgroundTasksActiveCount() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return 0;
  return _bgTasksFor(sid).filter(t =>
    t.status === 'running' || (t.consumed_at == null && (t.status === 'done' || t.status === 'cancelled'))
  ).length;
}

function _bgAnyRunning() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return false;
  return _bgTasksFor(sid).some(t => t.status === 'running');
}

async function loadBackgroundTasks() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return;
  try {
    const resp = await API.getBackgroundTasks(sid);
    if (!state.backgroundTasks) state.backgroundTasks = {};
    state.backgroundTasks[sid] = resp.tasks || [];
  } catch (e) {
    // Leave prior data on a transient error; never wipe the panel.
    return;
  }
  refreshBackgroundTasksPill();
  if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
  if (state.rightPanelOpen && state.rightPanelTab === 'bgtasks') renderBackgroundTasksPane();
  // Self-regulate the poll: run while anything is still going, stop otherwise.
  if (_bgAnyRunning()) startBackgroundTasksPoll();
  else stopBackgroundTasksPoll();
}

function startBackgroundTasksPoll() {
  if (_bgPollHandle) return;
  _bgPollHandle = setInterval(loadBackgroundTasks, 2000);
}

function stopBackgroundTasksPoll() {
  if (_bgPollHandle) { clearInterval(_bgPollHandle); _bgPollHandle = null; }
}

// Top-bar pill: count of active tasks; hidden when zero.
function refreshBackgroundTasksPill() {
  const pill = document.getElementById('bgtasks-pill');
  const countEl = document.getElementById('bgtasks-pill-count');
  if (!pill) return;
  const n = backgroundTasksActiveCount();
  if (countEl) countEl.textContent = n;
  pill.style.display = n > 0 ? '' : 'none';
}

const _BG_STATUS = {
  running:   { label: 'läuft',         cls: 'bg-st-running' },
  done:      { label: 'Abgeschlossen', cls: 'bg-st-done' },
  cancelled: { label: 'Abgebrochen',   cls: 'bg-st-cancelled' },
  error:     { label: 'Fehler',        cls: 'bg-st-error' },
};

function _bgDuration(t) {
  const start = t.created_at;
  const end = t.finished_at || (Date.now() / 1000);
  if (!start) return '';
  const s = Math.max(0, Math.round(end - start));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  return `${m}m ${r}s`;
}

function _bgCard(t) {
  const st = _BG_STATUS[t.status] || _BG_STATUS.running;
  const dur = _bgDuration(t);
  const tokens = (t.usage_in || 0) + (t.usage_out || 0);
  const metaBits = [];
  if (dur) metaBits.push(dur);
  if (tokens) metaBits.push(`${(tokens / 1000).toFixed(1)}k Tokens`);
  if (t.tool_calls) metaBits.push(`${t.tool_calls} Tool-Verwendungen`);
  const meta = metaBits.join(' · ');
  const actions = [];
  if (t.status === 'running') {
    actions.push(`<button class="bgtask-action" onclick="cancelBgTask('${t.id}')">Stopp</button>`);
  } else {
    actions.push(`<button class="bgtask-action bgtask-action-del" onclick="deleteBgTask('${t.id}')">Löschen</button>`);
  }
  actions.push(`<button class="bgtask-action bgtask-link" onclick="openBgTranscript('${t.id}')">Transkript anzeigen</button>`);
  const errLine = (t.status === 'error' && t.error)
    ? `<div class="bgtask-error">${escapeHtml(t.error)}</div>` : '';
  return `
    <div class="bgtask-card" data-task="${t.id}">
      <div class="bgtask-row1">
        <span class="bgtask-dot ${st.cls}"></span>
        <span class="bgtask-title">${escapeHtml(t.title || 'Hintergrundaufgabe')}</span>
      </div>
      <div class="bgtask-row2"><span class="bgtask-status ${st.cls}">${st.label}</span>${meta ? ' · ' + escapeHtml(meta) : ''}</div>
      ${errLine}
      <div class="bgtask-actions">${actions.join('')}</div>
      <div class="bgtask-transcript" id="bgtask-transcript-${t.id}" style="display:none"></div>
    </div>`;
}

function renderBackgroundTasksPane() {
  const sid = state.activeChat?.sessionId;
  const host = document.getElementById('bgtasks-content');
  if (!host) return;
  const tasks = sid ? _bgTasksFor(sid) : [];
  if (!tasks.length) {
    host.innerHTML = '<div class="bgtasks-empty" id="bgtasks-empty">Keine Hintergrundaufgaben</div>';
    return;
  }
  const running = tasks.filter(t => t.status === 'running');
  const finished = tasks.filter(t => t.status !== 'running');
  let html = '';
  if (running.length) {
    html += '<div class="bgtasks-section-label">Wird ausgeführt</div>';
    html += running.map(_bgCard).join('');
  }
  if (finished.length) {
    html += '<div class="bgtasks-section-label">Fertig</div>';
    html += finished.map(_bgCard).join('');
  }
  host.innerHTML = html;
}

async function cancelBgTask(taskId) {
  try { await API.cancelBackgroundTask(taskId); } catch (_) {}
  loadBackgroundTasks();
}

async function deleteBgTask(taskId) {
  try { await API.deleteBackgroundTask(taskId); } catch (_) {}
  // Drop locally so the row vanishes immediately, then reconcile.
  const sid = state.activeChat?.sessionId;
  if (sid && state.backgroundTasks?.[sid]) {
    state.backgroundTasks[sid] = state.backgroundTasks[sid].filter(t => t.id !== taskId);
  }
  loadBackgroundTasks();
}

function openBgTranscript(taskId) {
  const box = document.getElementById('bgtask-transcript-' + taskId);
  if (!box) return;
  // Toggle closed if already open.
  if (box.style.display !== 'none') {
    box.style.display = 'none';
    box.textContent = '';
    if (_bgTranscriptCtrl) { _bgTranscriptCtrl.abort(); _bgTranscriptCtrl = null; }
    return;
  }
  if (_bgTranscriptCtrl) { _bgTranscriptCtrl.abort(); _bgTranscriptCtrl = null; }
  box.style.display = '';
  box.textContent = '';
  _bgTranscriptCtrl = API.streamBackgroundTranscript(
    taskId,
    (chunk) => { box.textContent += chunk; box.scrollTop = box.scrollHeight; },
    (d) => { if (d && d.error && !box.textContent) box.textContent = '(Fehler: ' + d.error + ')'; }
  );
}
