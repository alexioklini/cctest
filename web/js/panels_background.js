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

// "Active" = running, or finished-but-not-yet-pulled-into-chat. Used for the
// right-panel TAB badge (transient attention signal).
function backgroundTasksActiveCount() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return 0;
  return _bgTasksFor(sid).filter(t =>
    t.status === 'running' || (t.consumed_at == null && (t.status === 'done' || t.status === 'cancelled'))
  ).length;
}

// Total tasks for the session, regardless of status/consumed. Drives the
// top-bar PILL so finished+delivered tasks stay findable after a reload — the
// pill clears only when the user deletes them (Löschen).
function backgroundTasksTotalCount() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return 0;
  return _bgTasksFor(sid).length;
}

function _bgAnyRunning() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return false;
  return _bgTasksFor(sid).some(t => t.status === 'running');
}

async function loadBackgroundTasks() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return;
  const _prev = _bgTasksFor(sid);
  const _prevRunning = new Set(_prev.filter(t => t.status === 'running').map(t => t.id));
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

  // Live delivery (poll-reattach): if a task just went running -> terminal, the
  // server may have auto-fired a delivery turn into this idle session. An idle
  // client holds no chat stream, so attach now to render that turn live (rather
  // than only on the next reload). attachStream replays from the turn start; if
  // no turn is actually live it emits `idle` and harmlessly returns.
  const _justFinished = _bgTasksFor(sid).some(
    t => _prevRunning.has(t.id) && t.status !== 'running');
  if (_justFinished) _reattachForBackgroundDelivery(sid);

  // Self-regulate the poll: run while anything is still going, stop otherwise.
  if (_bgAnyRunning()) startBackgroundTasksPoll();
  else stopBackgroundTasksPoll();
}

// Attach to the (possibly just-started) delivery turn for the active session so
// it renders live in the open chat. Mirrors openSession's resume-streaming
// block. Safe to call when idle: attachStream → `idle` → no-op. Guarded so we
// don't stomp a turn the user is already watching.
function _reattachForBackgroundDelivery(sid, _attempt) {
  _attempt = _attempt || 0;
  const chat = state.activeChat;
  if (!chat || chat.sessionId !== sid) return;       // session switched away
  if (chat.streaming) return;                        // already watching a turn
  const isActive = () => state.activeChat === chat && chat.sessionId === sid;
  const cbs = buildStreamCallbacks(chat, isActive);
  const _origDone = cbs.done;
  cbs.done = (d) => { if (_origDone) _origDone(d); refreshBackgroundTasksPill(); };
  // Only flip the streaming UI on once we actually see the turn begin — `idle`
  // means there was nothing to attach to, so leave the chat untouched.
  let _started = false;
  const _begin = () => {
    if (_started) return;
    _started = true;
    chat.streaming = true;
    chat.streamingText = '';
    chat.thinkingText = '';
    chat._streamGen = (chat._streamGen || 0) + 1;
    chat._streamStartTime = Date.now();
    clearInterval(chat._streamTimerInterval);
    chat._streamTimerInterval = setInterval(() => updateStreamTimer(chat), 100);
    if (isActive()) updateStreamingUI(true, chat);
  };
  for (const ev of ['text_block_start', 'text_delta', 'thinking_start', 'tool_call']) {
    const orig = cbs[ev];
    cbs[ev] = (d) => { _begin(); if (orig) orig(d); };
  }
  // Timing: the server fires the delivery turn from the runner's `finally`,
  // slightly AFTER the task row flips to done. Two races to cover:
  //   - too early: turn hasn't started yet → `idle`. Retry briefly.
  //   - too late: a FAST delivery turn already finished + tore down its
  //     live_stream before we attached → `idle`, but the turn is persisted.
  //     Reload the session so the now-saved delivery turn renders (no manual
  //     F5). We can't tell the two apart from `idle` alone, so: retry a couple
  //     times, then fall back to a reload.
  cbs.idle = () => {
    if (!isActive() || chat.streaming) return;
    if (_attempt < 2) {
      setTimeout(() => _reattachForBackgroundDelivery(sid, _attempt + 1), 1500);
    } else if (typeof openSession === 'function') {
      // Give up on live attach; pull the persisted delivery turn in.
      openSession(sid, chat.agent);
    }
  };
  API.attachStream(sid, cbs);
}

function startBackgroundTasksPoll() {
  if (_bgPollHandle) return;
  _bgPollHandle = setInterval(loadBackgroundTasks, 2000);
}

function stopBackgroundTasksPoll() {
  if (_bgPollHandle) { clearInterval(_bgPollHandle); _bgPollHandle = null; }
}

// Top-bar pill: shown whenever the session has ANY background task (running,
// finished, or already delivered) so they stay findable after a reload; clears
// only when the user deletes them. The count itself highlights still-active
// ones, but presence is driven by the total.
function refreshBackgroundTasksPill() {
  const pill = document.getElementById('bgtasks-pill');
  const countEl = document.getElementById('bgtasks-pill-count');
  if (!pill) return;
  const total = backgroundTasksTotalCount();
  const active = backgroundTasksActiveCount();
  if (countEl) countEl.textContent = active || total;
  pill.style.display = total > 0 ? '' : 'none';
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
  // Two labelled sections: Anfrage (the prompt that started the task) +
  // Ergebnis (the run's output). textContent on each keeps it injection-safe.
  box.innerHTML =
    '<div class="bgtask-tr-label">Anfrage</div>' +
    '<div class="bgtask-tr-request" id="bgtask-tr-req-' + taskId + '"></div>' +
    '<div class="bgtask-tr-label">Ergebnis</div>' +
    '<div class="bgtask-tr-output" id="bgtask-tr-out-' + taskId + '"></div>';
  const reqEl = document.getElementById('bgtask-tr-req-' + taskId);
  const outEl = document.getElementById('bgtask-tr-out-' + taskId);
  _bgTranscriptCtrl = API.streamBackgroundTranscript(
    taskId,
    (chunk) => { if (outEl) { outEl.textContent += chunk; box.scrollTop = box.scrollHeight; } },
    (d) => { if (d && d.error && outEl && !outEl.textContent) outEl.textContent = '(Fehler: ' + d.error + ')'; },
    (req) => { if (reqEl) reqEl.textContent = req.prompt || '(keine Anfrage gespeichert)'; }
  );
}
