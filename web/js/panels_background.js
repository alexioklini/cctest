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
 *   cancelBgTask, deleteBgTask, openBgTranscript, _turnControlEntries,
 *   _tcActivityCard
 */

let _bgPollHandle = null;
let _bgTranscriptCtrl = null;
// Signature of the last full pane render ("<id>:<status>|…") — lets the 2s poll
// skip a redundant innerHTML rebuild when nothing structural changed (preserves
// expanded tool cards on running tasks; live updates are targeted instead).
let _bgPaneSig = null;
// The session `_bgPaneSig` was last computed for. The signature only covers
// server BACKGROUND tasks — NOT the session's inline tool calls — so on a
// SESSION SWITCH the sig can be unchanged (e.g. old + new session both have no
// bg-tasks) even though the whole activity list changed. Without this the pane
// would keep the previous session's cards (or the static empty default) and
// never re-render the new session's tool-call activity. Tracking the sid forces
// a rebuild whenever the session changes, regardless of the bg-task signature.
let _bgPaneSid = null;

// Live tool-activity subscriptions for RUNNING tasks. The sidecar already emits
// tool_dispatch_start/done + text deltas on its per-turn event log, and the
// /transcript endpoint proxies them raw while a task runs — but the 2s metadata
// poll only carries a final tool-COUNT. So for each running task we open the
// transcript SSE once and accumulate a live timeline here, keyed by task id:
//   _bgLive[taskId] = { ctrl, tools: [{id,name,args,status,elapsed_ms,is_error}],
//                       text: '<streamed answer so far>' }
// Torn down when the task leaves `running` (terminal metadata arrives) or the
// session switches. This mirrors the interactive chat's live tool-call display.
const _bgLive = {};

function _bgLiveStop(taskId) {
  const L = _bgLive[taskId];
  if (!L) return;
  try { if (L.ctrl) L.ctrl.abort(); } catch (_) {}
  delete _bgLive[taskId];
}

function _bgLiveStopAll() {
  for (const id of Object.keys(_bgLive)) _bgLiveStop(id);
}

// Open the live transcript stream for one running task (idempotent). Events
// mutate _bgLive[taskId]; we re-render the pane so the card's timeline updates
// as tool calls happen — not only when the whole task finishes.
function _bgLiveStart(taskId) {
  if (_bgLive[taskId]) return;                       // already subscribed
  const L = { ctrl: null, tools: [], text: '' };
  _bgLive[taskId] = L;
  const _rerenderIfVisible = () => {
    if (state.rightPanelOpen && state.rightPanelTab === 'bgtasks') _bgLiveRenderCard(taskId);
  };
  L.ctrl = API.streamBackgroundTranscript(
    taskId,
    (chunk) => { L.text += chunk; _rerenderIfVisible(); },     // onText
    () => { /* onDone: terminal handled by the metadata poll */ },
    null,                                                       // onRequest (unused live)
    (ev) => {                                                   // onTool
      if (ev.phase === 'start') {
        L.tools.push({ id: ev.tool_use_id, name: ev.name, args: ev.args || {},
                       status: 'running', elapsed_ms: null, is_error: false });
      } else if (ev.phase === 'done') {
        const t = L.tools.find(x => x.id === ev.tool_use_id)
               || L.tools.slice().reverse().find(x => x.name === ev.name && x.status === 'running');
        if (t) { t.status = 'done'; t.elapsed_ms = ev.elapsed_ms; t.is_error = !!ev.is_error; }
      }
      _rerenderIfVisible();
    }
  );
}

// Reconcile live subscriptions against the current running set: open streams for
// newly-running tasks, drop streams for tasks that left `running`.
function _bgLiveReconcile() {
  const sid = state.activeChat?.sessionId;
  const running = new Set(
    (sid ? _bgTasksFor(sid) : []).filter(t => t.status === 'running' && t.turn_id).map(t => t.id));
  for (const id of Object.keys(_bgLive)) if (!running.has(id)) _bgLiveStop(id);
  for (const id of running) _bgLiveStart(id);
}

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
    t.status === 'running' || (t.consumed_at == null && (t.status === 'done'
      || t.status === 'cancelled' || t.status === 'timeout' || t.status === 'empty'))
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

// All activity entries (sync tool calls + background tasks) for the session —
// drives the right-panel TAB badge now that the panel shows everything.
function backgroundActivityCount() {
  return _collectActivityEntries().length;
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
  // Full pane re-render only when the STRUCTURE changed (task added/removed or a
  // status transition) — a plain 2s tick on unchanged running tasks must NOT
  // rebuild innerHTML, or it would collapse tool cards the user just expanded.
  // Live tool/text updates for running tasks go through _bgLiveRenderCard
  // (targeted, in-place) instead. A status flip to terminal re-renders so the
  // card switches from live data to the persisted (expandable-result) tool_events.
  if (state.rightPanelOpen && state.rightPanelTab === 'bgtasks') {
    const sig = _bgTasksFor(sid).map(t => t.id + ':' + t.status).join('|');
    // Force a render on session change (sig alone misses inline-tool-call-only
    // sessions whose bg-task sig is identical to the previous session's).
    if (sig !== _bgPaneSig || sid !== _bgPaneSid) {
      _bgPaneSig = sig; _bgPaneSid = sid; renderBackgroundTasksPane();
    }
  }

  // Live delivery (poll-reattach): if a task just went running -> terminal, the
  // server may have auto-fired a delivery turn into this idle session. An idle
  // client holds no chat stream, so attach now to render that turn live (rather
  // than only on the next reload). attachStream replays from the turn start; if
  // no turn is actually live it emits `idle` and harmlessly returns.
  const _justFinished = _bgTasksFor(sid).some(
    t => _prevRunning.has(t.id) && t.status !== 'running');
  if (_justFinished) _reattachForBackgroundDelivery(sid);

  // Open/close live tool-activity streams to match the running set.
  _bgLiveReconcile();

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
  // Leaving the chat view / no session in scope → drop live tool streams too
  // (reopened by _bgLiveReconcile on the next loadBackgroundTasks).
  _bgLiveStopAll();
}

// Top-bar pill: shown whenever the session has ANY activity entry — synchronous
// tool calls OR background tasks — so the panel is findable as soon as the model
// uses a tool (not only for background tasks). Count highlights still-running
// ones; presence is driven by the total activity count.
function refreshBackgroundTasksPill() {
  const pill = document.getElementById('bgtasks-pill');
  const countEl = document.getElementById('bgtasks-pill-count');
  if (!pill) return;
  const total = backgroundActivityCount();
  const active = backgroundTasksActiveCount();
  if (countEl) countEl.textContent = active || total;
  pill.style.display = total > 0 ? '' : 'none';
}

const _BG_STATUS = {
  running:   { label: 'läuft',                    cls: 'bg-st-running' },
  done:      { label: 'Abgeschlossen',            cls: 'bg-st-done' },
  cancelled: { label: 'Abgebrochen',              cls: 'bg-st-cancelled' },
  error:     { label: 'Fehler',                   cls: 'bg-st-error' },
  timeout:   { label: 'Zeitlimit überschritten',  cls: 'bg-st-error' },
  empty:     { label: 'Leere Antwort',            cls: 'bg-st-error' },
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

// Per-task allowed wall-clock, mirrors engine.background_tasks._TIMEOUT_S (3600s).
// Keep in sync if the server constant changes.
const _BG_TASK_TIMEOUT_S = 3600;

// Bullet (dot) state-color for a single task:
//   green  = running, < 80% of allowed time
//   yellow = running, 80–90%
//   orange = running, 90–100%
//   red    = running ≥ 100% (timeout) OR cancelled/error
//   grey   = done
// Returns the dot CSS class.
function _bgDotClass(t) {
  const status = t.status;
  if (status === 'cancelled' || status === 'error'
      || status === 'timeout' || status === 'empty') return 'bg-st-error';
  if (status === 'done') return 'bg-st-done';
  if (status === 'running') {
    const start = t.created_at;
    if (start) {
      const elapsed = Math.max(0, (Date.now() / 1000) - start);
      const pct = elapsed / _BG_TASK_TIMEOUT_S;
      if (pct >= 1.0) return 'bg-st-error';   // timed out
      if (pct >= 0.9) return 'bg-st-warn2';   // orange
      if (pct >= 0.8) return 'bg-st-warn';    // yellow
    }
    return 'bg-st-running';                    // green
  }
  return 'bg-st-running';
}

// Group bullet = the WORST member state (most-urgent wins), so the collapsed
// group header signals trouble without expanding. Priority:
//   red (error/cancelled/timeout) > orange > yellow > green (running) > grey (done).
const _BG_DOT_SEVERITY = { 'bg-st-error': 5, 'bg-st-warn2': 4, 'bg-st-warn': 3, 'bg-st-running': 2, 'bg-st-done': 1 };
function _bgGroupDotClass(members) {
  let worst = 'bg-st-done';
  for (const m of (members || [])) {
    const c = _bgDotClass(m);
    if ((_BG_DOT_SEVERITY[c] || 0) > (_BG_DOT_SEVERITY[worst] || 0)) worst = c;
  }
  return worst;
}

function _bgCard(t, inGroup) {
  const st = _BG_STATUS[t.status] || _BG_STATUS.running;
  const dur = _bgDuration(t);
  const tokens = (t.usage_in || 0) + (t.usage_out || 0);
  const metaBits = [st.label];
  if (dur) metaBits.push(dur);
  if (tokens) metaBits.push(`${(tokens / 1000).toFixed(1)}k Tokens`);
  if (t.tool_calls) metaBits.push(`${t.tool_calls} Tool-Verwendungen`);
  // Executing model — for fanned-out tasks this is the (often cheaper) offload
  // model the chat model declared via background_task_model, so surface it.
  if (t.model) metaBits.push(modelShortName(t.model, false));
  const meta = metaBits.join(' · ');
  // Right-aligned actions. Always an explicit "Transkript anzeigen" link (the
  // affordance users expect); plus Stopp while running, or Löschen when finished
  // (Löschen dropped for in-GROUP members — the group/section owns delete).
  const actions = [];
  if (t.status === 'running') {
    actions.push(`<button class="bgtask-action" onclick="event.stopPropagation();cancelBgTask('${t.id}')">Stopp</button>`);
  } else if (!inGroup) {
    actions.push(`<button class="bgtask-action bgtask-action-del" onclick="event.stopPropagation();deleteBgTask('${t.id}')">Löschen</button>`);
  }
  actions.push(`<button class="bgtask-action bgtask-link" onclick="event.stopPropagation();openBgTranscript('${t.id}')">Transkript anzeigen</button>`);
  const errLine = ((t.status === 'error' || t.status === 'timeout'
                    || t.status === 'empty') && t.error)
    ? `<div class="bgtask-error">${escapeHtml(t.error)}</div>` : '';
  const dotCls = _bgDotClass(t);
  // Request shown IMMEDIATELY (no transcript expand needed) — the prompt that
  // started this task. list_background_tasks carries it.
  const reqText = t.prompt || '';
  const reqBlock = reqText
    ? `<div class="bgtask-req"><div class="bgtask-req-label">Anfrage</div>`
      + `<div class="bgtask-req-text">${escapeHtml(reqText)}</div></div>` : '';
  // Tool calls as the SAME expandable cards as in-chat tools (live for running
  // tasks, persisted tool_events after reload). The whole list sits in a
  // <details> wrapper, DEFAULT COLLAPSED (a 30-tool subagent otherwise bloats
  // its card with 30 rows); each tool row inside stays its own collapsed
  // <details>. Live updates target the INNER container + count span, so the
  // user's open/closed choice on the wrapper survives streaming events.
  const _toolCount = _bgToolEntries(t).length;
  const toolsBlock = (_toolCount || t.status === 'running')
    ? `<details class="bgtask-tools-wrap" onclick="event.stopPropagation()">
        <summary class="bgtask-tools-summary">Tool-Verwendungen (<span id="bgtask-tools-count-${t.id}">${_toolCount}</span>)
          <svg class="bggroup-chev" viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </summary>
        <div class="bgtask-tools" id="bgtask-tools-${t.id}">${_bgToolsInner(t)}</div>
      </details>`
    : '';
  // Streamed answer-so-far (running only) — the live text deltas.
  const liveText = (t.status === 'running')
    ? `<div class="bgtask-live-text" id="bgtask-live-text-${t.id}">${_bgLiveTextInner(t.id)}</div>` : '';
  return `
    <div class="bgtask-card" data-task="${t.id}" onclick="openBgTranscript('${t.id}')" title="Transkript anzeigen">
      <div class="bgtask-row1">
        <span class="bgtask-dot ${dotCls}"></span>
        <span class="bgtask-title">${escapeHtml(t.title || 'Hintergrundaufgabe')}</span>
        ${actions.join('')}
      </div>
      <div class="bgtask-row2 ${st.cls}">${escapeHtml(meta)}</div>
      ${errLine}
      ${reqBlock}
      ${toolsBlock}
      ${liveText}
      <div class="bgtask-transcript" id="bgtask-transcript-${t.id}" style="display:none" onclick="event.stopPropagation()"></div>
    </div>`;
}

// Normalise a bg-task's tool calls to the activity-entry shape _toolEntryCard
// renders. Source of truth: the live stream while running (real-time, so cards
// appear as calls happen), the persisted tool_events otherwise (survives reload).
function _bgToolEntries(t) {
  const L = _bgLive[t.id];
  if (t.status === 'running' && L && L.tools.length) {
    return L.tools.map((tool, i) => ({
      kind: 'tool',
      id: tool.id || ('bglive-' + t.id + '-' + i),
      type: tool.name || 'tool',
      args: tool.args || {},
      status: tool.status === 'done' ? 'done' : 'running',
      result: tool.result != null ? tool.result : null,  // live stream has no result text
      isBackground: true,
      // Running tool of a running task → show the per-tool cancel (✕). The id is
      // the real sidecar tool_use_id (carried on the live event), which the
      // sidecar's /cancel-tool addresses.
      cancelTaskId: (tool.status !== 'done' && tool.id) ? t.id : null,
    }));
  }
  const evs = Array.isArray(t.tool_events) ? t.tool_events : [];
  return evs.map((ev, i) => ({
    kind: 'tool',
    id: ev.tool_use_id || ('bgte-' + t.id + '-' + i),
    type: ev.name || 'tool',
    args: ev.args || {},
    status: 'done',
    result: typeof ev.result === 'string' ? ev.result : (ev.result != null ? JSON.stringify(ev.result) : null),
    isBackground: true,
  }));
}

// Inner HTML of a task's tool block: one standard _toolEntryCard per tool call.
function _bgToolsInner(t) {
  const entries = _bgToolEntries(t);
  if (!entries.length) return '';
  return entries.map(_toolEntryCard).join('');
}

// Streamed answer-so-far for a running task (live text deltas).
function _bgLiveTextInner(taskId) {
  const L = _bgLive[taskId];
  return (L && L.text) ? escapeHtml(L.text) : '';
}

// Targeted in-place update of one running card's tool list + live text, so
// streaming events don't trigger a full pane rebuild (which would fight the 2s
// poll + reset the transcript toggle / scroll of sibling cards). Finds the task
// row by id across the session list (handles grouped + standalone alike).
function _bgLiveRenderCard(taskId) {
  const sid = state.activeChat?.sessionId;
  const t = (sid ? _bgTasksFor(sid) : []).find(x => x.id === taskId);
  const toolsEl = document.getElementById('bgtask-tools-' + taskId);
  if (toolsEl && t) {
    toolsEl.innerHTML = _bgToolsInner(t);
    // Keep the collapsed wrapper's count fresh (the wrapper itself is NOT
    // re-rendered here, so its open/closed state survives).
    const cnt = document.getElementById('bgtask-tools-count-' + taskId);
    if (cnt) cnt.textContent = String(_bgToolEntries(t).length);
  }
  const textEl = document.getElementById('bgtask-live-text-' + taskId);
  if (textEl) textEl.innerHTML = _bgLiveTextInner(taskId);
}

/* ───────────────────────────────────────────────────────────
   Activity entries: the panel shows ALL tool calls of the CURRENT session,
   synchronous (in-chat tool_call/tool_result, or assistant.metadata.tools after
   reload) AND asynchronous (background_tasks). One normaliser produces a single
   chronologically-sorted list (newest first) that the pane renders. Full-view /
   copy / download for a tool result reuse chat_tools.js's buildToolResultBlock +
   handlers (single-sourced — those live in the panel now, capped-out of chat).
   ─────────────────────────────────────────────────────────── */

// Sort: newest first (per user). `_seq`/`id` break ties within the same ms.
function _bgSortNewestFirst(a, b) {
  if (b.ts !== a.ts) return b.ts - a.ts;
  return (b.seq || 0) - (a.seq || 0);
}

// Collect the current session's synchronous tool calls as activity entries.
// Live: tool_call/tool_result message pairs in chat.messages. After reload the
// raw pairs are gone, but assistant.metadata.tools[] carries them — covered by
// _toolEntriesFromMetadata below.
function _syncToolEntries() {
  const chat = state.activeChat;
  if (!chat || !Array.isArray(chat.messages)) return [];
  const msgs = chat.messages;
  const out = [];
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (!m || m.role !== 'tool_call') continue;
    // Synthetic rows: GDPR ops stay chat-only (they have their own Datenschutz
    // display), but MoA reference drafts DO belong in the Aktivität tab — they
    // are real per-turn LLM activity (one card per reference, expandable to
    // the draft text).
    const isMoaRef = m.synthetic && ((m.kind || m.name) === 'moa_reference'
      || (m.kind || m.name) === 'moa_planner'
      || (m.kind || m.name) === 'moa_verify'
      || (m.kind || m.name) === 'moa_plan_review');
    if (m.synthetic && !isMoaRef) continue;
    // Pair with its result (match by tool_use_id, else name; don't cross turns).
    let result = null, resTs = null, deanonResult = null;
    for (let j = i + 1; j < msgs.length; j++) {
      const n = msgs[j];
      if (n.role === 'tool_result') {
        const idMatch = m.tool_use_id && n.tool_use_id && m.tool_use_id === n.tool_use_id;
        const nameMatch = !m.tool_use_id && n.name === m.name;
        if (idMatch || nameMatch) { result = n.result; resTs = n._ts; deanonResult = n.deanon_result || null; break; }
      }
      if (n.role === 'assistant' || n.role === 'user') break;
    }
    // MoA entries: surface a READABLE body instead of the raw {model,…} object
    // (which would render as JSON noise). Reference/planner → draft text;
    // verify → verdict + reason/fix; plan_review → outcome + feedback.
    if (isMoaRef && result && typeof result === 'object') {
      const _k = m.kind || m.name;
      if (_k === 'moa_verify') {
        const _v = result.verdict === 'insufficient' ? 'Nachbesserung angefordert' : 'Ergebnis bestätigt';
        result = result.error || `${_v}${result.reason || result.instruction ? ' — ' + (result.reason || result.instruction) : ''}`;
      } else if (_k === 'moa_plan_review') {
        result = result.error
          || `${result.outcome || 'Entscheidung'}${result.feedback ? ' — ' + result.feedback : ''}`;
      } else {
        result = result.draft || result.error
          || (result.model ? `${result.model} · ${result.chars || 0} Zeichen` : null);
      }
    }
    out.push({
      kind: 'tool',
      id: m.tool_use_id || ('tc-' + (m._seq || i)),
      type: m.name || 'tool',
      args: m.args || {},
      // GDPR: the real (de-anonymised) args the local tool ran on, so the
      // activity card shows the same real values + 🔓 badge as the inline chat
      // line (not the model's pseudonyms). See _toolEntryCard.
      deanon_args: m.deanon_args || null,
      status: result != null ? 'done' : 'running',
      result: result,
      deanon_result: deanonResult,   // de-anonymised result for display (see _toolEntryCard)
      ts: m._ts || 0,
      seq: m._seq || 0,
      isBackground: false,
    });
  }
  return out;
}

// After a reload there are no live tool_call rows — reconstruct from each
// assistant message's metadata.tools[] instead.
function _toolEntriesFromMetadata() {
  const chat = state.activeChat;
  if (!chat || !Array.isArray(chat.messages)) return [];
  // Only use this when there are NO live REAL tool_call rows (avoid
  // double-listing). Synthetic rows (GDPR cards, MoA references) must NOT
  // suppress the reconstruction — they persist as tool_call messages across
  // reload, so counting them here made any chat containing them lose its
  // real-tool listing in the Aktivität tab after a reload.
  if (chat.messages.some(m => m && m.role === 'tool_call' && !m.synthetic)) return [];
  const out = [];
  let seq = 0;
  for (const m of chat.messages) {
    if (!m || m.role !== 'assistant') continue;
    const tools = m.metadata && Array.isArray(m.metadata.tools) ? m.metadata.tools : null;
    if (!tools) continue;
    for (const t of tools) {
      out.push({
        kind: 'tool',
        id: t.tool_use_id || ('tm-' + seq),
        type: t.name || 'tool',
        args: t.args || {},
        deanon_args: t.deanon_args || null,   // GDPR: real args (see _toolEntryCard)
        status: 'done',
        result: typeof t.result === 'string' ? t.result : (t.result != null ? JSON.stringify(t.result) : null),
        deanon_result: (typeof t.deanon_result === 'string' && t.deanon_result) ? t.deanon_result : null,
        ts: (m.metadata && m.metadata.ts) || seq,  // metadata has no per-tool ts; keep turn order
        seq: seq++,
        isBackground: false,
      });
    }
  }
  return out;
}

// Background tasks normalised to the same shape.
function _bgEntries() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return [];
  const tasks = _bgTasksFor(sid);
  // Group fan-out tasks (shared group_id, incl. auto-<turn>) into one entry; a
  // lone task in a group still renders as a single card (group-of-one), so only
  // collapse when ≥2 members share a group_id. Tasks with no group_id stay
  // individual (legacy + true standalone).
  const groups = {};
  for (const t of tasks) {
    const g = t.group_id;
    if (g) (groups[g] = groups[g] || []).push(t);
  }
  const entries = [];
  const grouped = new Set();
  for (const t of tasks) {
    const g = t.group_id;
    if (g && groups[g].length >= 2) {
      if (grouped.has(g)) continue;        // group already emitted
      grouped.add(g);
      const members = groups[g];
      const running = members.some(m => m.status === 'running');
      entries.push({
        kind: 'bggroup',
        id: 'grp-' + g,
        type: 'Hintergrund-Gruppe',
        title: '',
        status: running ? 'running' : 'done',
        ts: Math.max(...members.map(m => m.created_at || 0)),
        seq: 0,
        isBackground: true,
        members,
      });
    } else {
      entries.push({
        kind: 'bgtask',
        id: t.id,
        type: 'Hintergrundaufgabe',
        title: t.title,
        status: t.status,
        ts: t.created_at || 0,
        seq: 0,
        isBackground: true,
        raw: t,
      });
    }
  }
  return entries;
}

// Turn-control activity of the CURRENT session (chat_turncontrol.js writes
// chat.turnActivity): mid-turn injections + goal-mode judge/round events,
// normalised to activity entries so they render as cards next to tool calls.
// While a goal is armed and a turn is streaming, a synthetic "geplant" entry
// announces the judge call that will run after the reply (future activity).
// Entries still open when the turn has ended are shown finished/stale here —
// the loop that would complete them is gone (no teardown hook needed).
function _turnControlEntries() {
  const chat = state.activeChat;
  const arr = (chat && Array.isArray(chat.turnActivity)) ? chat.turnActivity : [];
  const streaming = !!(chat && chat.streaming);
  const out = arr.map((e, i) => ({
    kind: 'tc', tc: e, id: e.id || ('tc-' + i),
    status: (e.status !== 'done' && streaming) ? 'running' : 'done',
    stale: e.status !== 'done' && !streaming,
    ts: e.ts || 0, seq: i, isBackground: false,
  }));
  if (streaming && chat.goalStatus === 'active'
      && !arr.some(e => e.kind === 'goal_judge' && e.status === 'running')) {
    out.push({
      kind: 'tc', id: 'tc-goal-planned', status: 'running', stale: false,
      ts: Date.now(), seq: arr.length, isBackground: false,
      tc: { kind: 'goal_planned', iteration: (chat._goalIteration || 0) + 1,
            max: chat._goalMax || chat.goalMaxIterations || 0 },
    });
  }
  return out;
}

// Card for one turn-control entry (injection / goal judge / goal round /
// planned judge) — same visual language as a tool-call card: dot + title +
// status line + optional text body.
function _tcActivityCard(e) {
  const t = e.tc || {};
  const running = e.status === 'running';
  const iterBit = t.max ? ` (Iteration ${t.iteration || '?'}/${t.max})`
    : (t.iteration ? ` (Iteration ${t.iteration})` : '');
  let title = '', line = '', body = '';
  let dot = running ? 'bg-st-running' : 'bg-st-done';
  if (t.kind === 'inject') {
    title = 'Klarstellung an die laufende Antwort';
    if (t.status === 'done') {
      line = t.round ? `In Runde ${t.round} übernommen` : 'Übernommen';
    } else if (e.stale) {
      line = 'Nicht übernommen — die Antwort war schon beendet';
      dot = 'bg-st-error';
    } else {
      line = 'Wartet auf das nächste Rundenende …';
    }
    body = t.text || '';
  } else if (t.kind === 'goal_judge') {
    title = '🎯 Ziel-Prüfung' + iterBit;
    const v = t.verdict || '';
    line = (t.status !== 'done' && !e.stale) ? 'Judge bewertet die Antwort …'
      : v === 'fulfilled' ? 'Ziel erreicht ✓'
      : v === 'active' ? 'Ziel noch nicht erreicht → weitere Iteration'
      : v === 'capped' ? 'Nicht erreicht — Iterations-Limit oder unerreichbar'
      : v === 'judge_error' ? 'Prüfung fehlgeschlagen — Durchlauf beendet'
      : 'Beendet';
    if (v === 'capped' || v === 'judge_error') dot = 'bg-st-error';
    // Judge verdict payload: why the goal is(n't) met + what the model was
    // told for the next iteration (only present on verdict 'active').
    if (t.reasoning) body = 'Begründung: ' + t.reasoning;
    if (t.instruction) {
      body += (body ? '\n' : '') + 'Anweisung für die nächste Iteration: ' + t.instruction;
    }
  } else if (t.kind === 'goal_round') {
    title = '🎯 Zusätzliche Iteration' + iterBit;
    line = running ? 'Der Agent arbeitet weiter am Ziel …' : 'Abgeschlossen';
    body = t.text || '';
  } else if (t.kind === 'goal_planned') {
    title = '🎯 Ziel-Prüfung geplant' + iterBit;
    line = 'Startet nach Abschluss der laufenden Antwort';
    dot = 'bg-st-warn';
  }
  const bodyHtml = body ? `<div class="act-tc-text">${escapeHtml(body)}</div>` : '';
  return `
    <div class="bgtask-card act-tc-card" data-act="${escapeHtml(e.id)}">
      <div class="bgtask-row1">
        <span class="bgtask-dot ${dot}"></span>
        <span class="bgtask-title">${escapeHtml(title)}</span>
      </div>
      <div class="bgtask-row2 ${running ? 'bg-st-running' : ''}">${escapeHtml(line)}</div>
      ${bodyHtml}
    </div>`;
}

// The unified, sorted activity list for the current session.
function _collectActivityEntries() {
  // Always merge both sources: _toolEntriesFromMetadata self-guards (returns []
  // while real live tool_call rows exist), so live turns list the live rows and
  // reloaded chats get the metadata reconstruction — and synthetic MoA rows
  // (which persist across reload in _syncToolEntries) never suppress either.
  const sync = _syncToolEntries();
  const synced = sync.concat(_toolEntriesFromMetadata());
  // Drop run_background_task tool-CALL entries: they're just the trigger for
  // background tasks, which already appear (grouped) via _bgEntries. Showing the
  // spawning call too double-lists every fan-out (the call rows AND the resulting
  // task group).
  const syncedReal = synced.filter(e => e.type !== 'run_background_task');
  return syncedReal.concat(_bgEntries()).concat(_turnControlEntries())
    .sort(_bgSortNewestFirst);
}

// One capped, expandable card for a synchronous tool-call entry. Reuses
// chat_tools.js's buildToolResultBlock (full view + copy + download) so that
// logic stays single-sourced — it just lives in the panel now.
function _toolEntryCard(e) {
  // GDPR: prefer the de-anonymised (real) args the local tool actually ran on,
  // so the activity card shows the same real values as the inline chat line
  // (not the model's pseudonyms). Mirrors renderToolCall in chat_tools.js.
  const _deanon = (e.deanon_args && typeof e.deanon_args === 'object') ? e.deanon_args : null;
  const _args = _deanon || e.args || {};
  // GDPR: the card title gets the same amber/red PII marks (+ tooltip "X wurde
  // mit Y anonymisiert") as the chat query/reply — see gdprHighlightPlain.
  const desc = (typeof toolDescribe === 'function')
    ? ((typeof gdprHighlightPlain === 'function')
        ? gdprHighlightPlain(toolDescribe(e.type, _args))
        : toolDescribe(e.type, _args))
    : escapeHtml(e.type);
  const st = e.status === 'running' ? _BG_STATUS.running : _BG_STATUS.done;
  const argsTable = (typeof renderToolArgsTable === 'function')
    ? renderToolArgsTable(e.type === 'python_exec'
        ? Object.fromEntries(Object.entries(_args).filter(([k]) => k !== 'code'))
        : _args)
    : '';
  // GDPR: prefer the de-anonymised (real) result for the displayed box so it
  // matches the de-anonymised query header (model-facing result stays fake).
  const _resultForBox = (typeof e.deanon_result === 'string' && e.deanon_result)
    ? e.deanon_result
    : (typeof e.result === 'string' ? e.result : JSON.stringify(e.result, null, 2));
  const resultBlock = (e.result != null && typeof buildToolResultBlock === 'function')
    ? buildToolResultBlock(e.type, _args, _resultForBox, e.id)
    : '';
  const hasBody = !!(argsTable || resultBlock);
  // Cancel (✕) for a still-running bg-task tool call. Only when the entry opts
  // in via cancelTaskId (a running background task's tool) — sync in-chat tool
  // entries don't set it, so the button is bg-only. stopPropagation so the click
  // neither toggles the <details> nor bubbles to the card's transcript opener.
  const cancelBtn = (e.status === 'running' && e.cancelTaskId)
    ? `<button class="bgtask-action act-tool-cancel" title="Tool-Aufruf abbrechen"`
      + ` onclick="event.stopPropagation();event.preventDefault();cancelBgTool('${e.cancelTaskId}','${escapeHtml(e.id)}')">✕</button>`
    : '';
  return `
    <details class="bgtask-card act-tool-card" data-act="${escapeHtml(e.id)}"${hasBody ? '' : ' open'}>
      <summary class="bgtask-summary">
        <span class="bgtask-dot ${st.cls}"></span>
        <span class="bgtask-title">${desc}</span>
        ${cancelBtn}
        ${hasBody ? '<svg class="bggroup-chev" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>' : ''}
      </summary>
      <div class="bgtask-row2 ${st.cls}">${escapeHtml(e.type)} · ${st.label}</div>
      ${hasBody ? `<div class="act-tool-body">${argsTable}${resultBlock}</div>` : ''}
    </details>`;
}

// A fan-out group: one header card (X von N fertig + follow_up) wrapping the
// member task cards. Reuses _bgCard for each member so per-task transcript /
// stop / delete stay identical.
function _bgGroupCard(e) {
  const members = e.members || [];
  const total = members.length;
  const done = members.filter(m => m.status !== 'running').length;
  const failed = members.filter(m => m.status === 'error' || m.status === 'cancelled'
    || m.status === 'timeout' || m.status === 'empty').length;
  const running = total - done;
  const st = running ? _BG_STATUS.running : _BG_STATUS.done;
  const followUp = (members.find(m => m.follow_up) || {}).follow_up || '';
  const failBit = failed ? ` · ${failed} fehlgeschlagen` : '';
  const memberCards = members.map(m => _bgCard(m, true)).join('');
  const fuLine = followUp
    ? `<div class="bggroup-followup">Zusammenführung: ${escapeHtml(followUp)}</div>` : '';
  const groupDot = _bgGroupDotClass(members);
  return `
    <details class="bggroup-card" data-group="${escapeHtml(e.id)}"${running ? ' open' : ''}>
      <summary class="bggroup-summary">
        <span class="bgtask-dot ${groupDot}"></span>
        <span class="bgtask-title">Parallele Recherche (${total} Aufgaben)</span>
        <span class="bggroup-count ${st.cls}">${done} von ${total} fertig${failBit}</span>
        <svg class="bggroup-chev" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
      </summary>
      ${fuLine}
      <div class="bggroup-members">${memberCards}</div>
    </details>`;
}

// Render one activity entry (group, background task, turn-control event, OR
// synchronous tool call) using the matching card builder.
function _activityCard(e) {
  if (e.kind === 'bggroup') return _bgGroupCard(e);
  if (e.kind === 'tc') return _tcActivityCard(e);
  return e.kind === 'bgtask' ? _bgCard(e.raw) : _toolEntryCard(e);
}

function renderBackgroundTasksPane() {
  const host = document.getElementById('bgtasks-content');
  if (!host) return;
  // Sync the poll's skip-signature to what we're about to render, so the next
  // 2s tick only rebuilds on a real structural change.
  const _sid = state.activeChat?.sessionId;
  _bgPaneSig = (_sid ? _bgTasksFor(_sid) : []).map(t => t.id + ':' + t.status).join('|');
  _bgPaneSid = _sid || null;   // keep the session marker in sync with the render
  const entries = _collectActivityEntries();
  if (!entries.length) {
    host.innerHTML = '<div class="bgtasks-empty" id="bgtasks-empty">Keine Aktivität in diesem Chat</div>';
    return;
  }
  const running = entries.filter(e => e.status === 'running');
  const finished = entries.filter(e => e.status !== 'running');
  // Are there any deletable finished BACKGROUND tasks? (sync tool entries +
  // groups aren't individually deletable — only real bg-task rows are.)
  const anyDeletable = finished.some(e => e.kind === 'bgtask'
    || (e.kind === 'bggroup' && (e.members || []).length));
  let html = '';
  if (running.length) {
    html += '<div class="bgtasks-section-label">Wird ausgeführt</div>';
    html += running.map(_activityCard).join('');
  }
  if (finished.length) {
    const del = anyDeletable
      ? '<button class="bgtasks-section-action" onclick="clearFinishedBgTasks()">Löschen</button>' : '';
    html += `<div class="bgtasks-section-head"><span class="bgtasks-section-label">Fertig</span>${del}</div>`;
    html += finished.map(_activityCard).join('');
  }
  host.innerHTML = html;
}

// Header-level "Löschen" on the Fertig section: delete all finished background
// tasks of the active session (Claude-desktop puts delete at the section level,
// not per-card). Sync tool entries + group wrappers aren't DB rows, so only real
// bgtask rows are deleted; the panel re-renders from what's left.
async function clearFinishedBgTasks() {
  const sid = state.activeChat?.sessionId;
  if (!sid) return;
  const tasks = _bgTasksFor(sid).filter(t => t.status !== 'running');
  for (const t of tasks) {
    try { await API.deleteBackgroundTask(t.id); } catch (e) {}
  }
  if (typeof loadBackgroundTasks === 'function') { try { await loadBackgroundTasks(); } catch (e) {} }
  renderBackgroundTasksPane();
}

// Open the activity panel and scroll/highlight a specific entry. Called from a
// capped tool-line in the chat (the in-chat block no longer expands).
function openActivityEntry(entryId) {
  if (typeof openRightPanel === 'function') openRightPanel('bgtasks');
  // Defer so the pane has rendered, then locate the card by data-act/data-task.
  setTimeout(() => {
    const host = document.getElementById('bgtasks-content');
    if (!host) return;
    const _esc = (window.CSS && window.CSS.escape) ? window.CSS.escape(entryId) : entryId;
    const sel = `[data-act="${_esc}"],[data-task="${_esc}"]`;
    const card = host.querySelector(sel);
    if (card) {
      card.scrollIntoView({ block: 'center', behavior: 'smooth' });
      card.classList.add('act-highlight');
      setTimeout(() => card.classList.remove('act-highlight'), 1600);
    }
  }, 60);
}

async function cancelBgTask(taskId) {
  try { await API.cancelBackgroundTask(taskId); } catch (_) {}
  loadBackgroundTasks();
}

// Cancel ONE in-flight tool call of a running task. The task keeps running; the
// sidecar returns a synthetic error result for this tool and proceeds. Mark the
// entry done locally for instant feedback, then let the live stream reconcile.
async function cancelBgTool(taskId, toolUseId) {
  const L = _bgLive[taskId];
  if (L) {
    const tool = L.tools.find(x => x.id === toolUseId);
    if (tool && tool.status !== 'done') { tool.status = 'done'; tool.is_error = true; }
    _bgLiveRenderCard(taskId);
  }
  try { await API.cancelBackgroundTool(taskId, toolUseId); } catch (_) {}
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
