// chat_turncontrol.js — end-user controls over a STREAMING chat turn:
//   • Queue      — type more while it's answering; queued messages auto-send as
//                  normal turns when the current one finishes. Edit/remove/
//                  reorder/inject-now/send-now. Persisted per session.
//   • Pause      — soft-hold the turn at the next round boundary; resume later.
//   • Inject     — splice a clarification into the RUNNING turn (model sees it
//                  next round). Distinct from the queue (which starts new turns).
//                  Lifecycle (pending → übernommen) renders as CARDS in the
//                  right panel's Aktivität tab (chat.turnActivity), not in the
//                  message flow.
//   • btw        — ask a side question answered in the right panel's OWN
//                  "Zwischenfragen" tab (thread + composer, chat.btwThread),
//                  grounded in what the agent is doing right now.
//   • Goal       — judge/iteration activity is mirrored into chat.turnActivity
//                  so the Aktivität tab shows planned/running/finished goal work.
//
// Single global (net-globals invariant). The queue panel mounts ABOVE the
// active composer; btw + activity render into static right-panel panes, so
// renderMessages()/the message model stay untouched. Backend endpoints:
//   POST /v1/chat/{pause,resume,inject,btw}; queue persists via manage action
//   'message_queue' (mirrors the Websuche basket).
const ChatTurnControl = {
  // ── Queue state (per chat: chat.messageQueue = [{id,text}]) ──────────────
  _arr(chat) {
    chat = chat || state.activeChat;
    if (!chat) return [];
    if (!Array.isArray(chat.messageQueue)) chat.messageQueue = [];
    return chat.messageQueue;
  },

  loadFromJson(chat, jsonStr) {
    let arr = [];
    try { arr = jsonStr ? JSON.parse(jsonStr) : []; } catch (e) { arr = []; }
    if (!Array.isArray(arr)) arr = [];
    // Normalise (drop malformed, ensure ids).
    chat.messageQueue = arr
      .filter(x => x && typeof x.text === 'string' && x.text.trim())
      .map(x => ({ id: x.id || this._mkId(), text: x.text }));
    this.render(chat);
  },

  _mkId() { return 'q' + Math.random().toString(36).slice(2, 9); },

  _persist(chat) {
    chat = chat || state.activeChat;
    if (!chat || !chat.sessionId) return;
    API.manageSession({ action: 'message_queue', session_id: chat.sessionId,
                        value: this._arr(chat) }).catch(() => {});
  },

  enqueue(chat, text) {
    text = (text || '').trim();
    if (!text) return;
    this._arr(chat).push({ id: this._mkId(), text });
    this._persist(chat);
    this.render(chat);
    if (typeof showToast === 'function') showToast('In Warteschlange eingereiht', false);
  },

  remove(chat, id) {
    chat = chat || state.activeChat;
    const arr = this._arr(chat);
    const i = arr.findIndex(x => x.id === id);
    if (i < 0) return;
    arr.splice(i, 1);
    this._persist(chat);
    this.render(chat);
  },

  move(chat, id, dir) {
    chat = chat || state.activeChat;
    const arr = this._arr(chat);
    const i = arr.findIndex(x => x.id === id);
    if (i < 0) return;
    const j = i + dir;
    if (j < 0 || j >= arr.length) return;
    const t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    this._persist(chat);
    this.render(chat);
  },

  edit(chat, id) {
    chat = chat || state.activeChat;
    const arr = this._arr(chat);
    const item = arr.find(x => x.id === id);
    if (!item) return;
    const next = window.prompt('Nachricht bearbeiten:', item.text);
    if (next === null) return;               // cancelled
    const t = next.trim();
    if (!t) { this.remove(chat, id); return; }
    item.text = t;
    this._persist(chat);
    this.render(chat);
  },

  clear(chat) {
    chat = chat || state.activeChat;
    chat.messageQueue = [];
    this._persist(chat);
    this.render(chat);
  },

  // Send a queued item RIGHT NOW as its own turn. If a turn is still running it
  // is re-queued to the FRONT (send once free) — sendMessage() enqueues while
  // streaming, so we route through the same guard by only firing when idle.
  sendNow(chat, id) {
    chat = chat || state.activeChat;
    const arr = this._arr(chat);
    const i = arr.findIndex(x => x.id === id);
    if (i < 0) return;
    if (chat.streaming) {
      // Can't start a second turn — move it to the head so it goes out first.
      const [item] = arr.splice(i, 1);
      arr.unshift(item);
      this._persist(chat);
      this.render(chat);
      if (typeof showToast === 'function')
        showToast('Läuft noch — wird als Nächstes gesendet', false);
      return;
    }
    const [item] = arr.splice(i, 1);
    this._persist(chat);
    this.render(chat);
    this._sendText(chat, item.text);
  },

  // Auto-send the head of the queue once the current turn has ended, by ANY
  // path — clean done, cancel, error, or the safety net. Called both from the
  // `done` handler and from updateStreamingUI(false) (the central turn-end
  // choke point), so a turn that ends WITHOUT a clean `done` still delivers the
  // queued message as the next turn. Idempotent per turn via _drainScheduled so
  // the two callers can't double-send.
  drainNext(chat) {
    chat = chat || state.activeChat;
    if (!chat || state.activeChat !== chat) return;   // only the visible chat
    if (chat.streaming) return;                        // turn still running — wait
    const arr = this._arr(chat);
    if (!arr.length) return;
    if (chat._drainScheduled) return;                  // already draining this turn
    chat._drainScheduled = true;
    const item = arr.shift();
    this._persist(chat);
    this.render(chat);
    // Small yield so the just-finished turn's DOM settles before the next send.
    setTimeout(() => {
      chat._drainScheduled = false;
      try {
        // Re-check: user may have started a turn or the chat changed in the gap.
        if (!chat.streaming && state.activeChat === chat) this._sendText(chat, item.text);
        else { arr.unshift(item); this._persist(chat); this.render(chat); }  // put it back
      } catch (e) {}
    }, 80);
  },

  // Put text into the composer and fire the normal send path (so every queued
  // message goes through identical GDPR/model/render logic as a typed one).
  _sendText(chat, text) {
    const input = _composerInputEl();
    if (!input) return;
    input.value = text;
    if (typeof autoResizeInput === 'function') autoResizeInput(input);
    sendMessage();
  },

  // ── Pause / resume ───────────────────────────────────────────────────────
  togglePause() {
    const chat = state.activeChat;
    if (!chat || !chat.streaming || !chat.sessionId) return;
    if (chat._turnPaused) {
      API.resumeChat(chat.sessionId).catch(() => {});
    } else {
      API.pauseChat(chat.sessionId).catch(() => {});
    }
    // Optimistic flip; the paused/resumed SSE event confirms + repaints.
    chat._turnPaused = !chat._turnPaused;
    this.renderControls(chat);
  },

  // ── Inject a clarification into the running turn ─────────────────────────
  promptInject() {
    const chat = state.activeChat;
    if (!chat || !chat.streaming || !chat.sessionId) return;
    const text = window.prompt(
      'Klarstellung an die laufende Antwort (wird in der nächsten Runde berücksichtigt):');
    if (text === null) return;
    const t = text.trim();
    if (!t) return;
    this.inject(chat, t);
  },

  inject(chat, text) {
    chat = chat || state.activeChat;
    if (!chat || !chat.sessionId) return;
    API.injectChat(chat.sessionId, text).catch(() => {});
  },

  // ── Turn activity (Aktivität-tab cards: injections + goal judge/rounds) ──
  // chat.turnActivity = [{id,kind,status,text,iteration,max,verdict,round,ts}]
  // In-memory per session (like the old pending chips were). The Aktivität
  // pane renders these as cards next to tool calls (_turnControlEntries in
  // panels_background.js); entries still open when the turn ends are shown as
  // finished/stale by the collector — no teardown hook needed here.
  _activityArr(chat) {
    chat = chat || state.activeChat;
    if (!chat) return [];
    if (!Array.isArray(chat.turnActivity)) chat.turnActivity = [];
    return chat.turnActivity;
  },

  _activityRefresh() {
    if (typeof refreshBackgroundTasksPill === 'function') refreshBackgroundTasksPill();
    if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
    if (state.rightPanelOpen && state.rightPanelTab === 'bgtasks'
        && typeof renderBackgroundTasksPane === 'function') renderBackgroundTasksPane();
  },

  notePendingInjection(chat, text) {
    chat = chat || state.activeChat;
    if (!chat) return;
    this._activityArr(chat).push({ id: this._mkId(), kind: 'inject', status: 'pending',
                                   text: text || '', ts: Date.now() });
    this._activityRefresh();
  },

  commitInjection(chat, text, round) {
    chat = chat || state.activeChat;
    if (!chat) return;
    const arr = this._activityArr(chat);
    const e = arr.find(x => x.kind === 'inject' && x.status === 'pending' && x.text === (text || ''))
           || arr.find(x => x.kind === 'inject' && x.status === 'pending');
    if (e) { e.status = 'done'; e.round = round || null; }
    this._activityRefresh();
  },

  // Goal-mode mirrors (called from the SSE handlers in chat_send.js).
  goalJudgeStart(chat, iteration, max) {
    chat = chat || state.activeChat;
    if (!chat) return;
    const arr = this._activityArr(chat);
    // The iteration that was running is over — close its card before judging.
    for (const e of arr) if (e.kind === 'goal_round' && e.status === 'running') e.status = 'done';
    arr.push({ id: this._mkId(), kind: 'goal_judge', status: 'running',
               iteration: iteration || 1, max: max || 0, ts: Date.now() });
    this._activityRefresh();
  },

  goalVerdict(chat, status, iteration, reasoning, instruction) {
    chat = chat || state.activeChat;
    if (!chat) return;
    const e = this._activityArr(chat).slice().reverse()
      .find(x => x.kind === 'goal_judge' && x.status === 'running');
    if (e) {
      e.status = 'done'; e.verdict = status || '';
      if (iteration) e.iteration = iteration;
      e.reasoning = reasoning || '';
      e.instruction = instruction || '';
    }
    this._activityRefresh();
  },

  goalRoundStart(chat, iteration, max, text) {
    chat = chat || state.activeChat;
    if (!chat) return;
    this._activityArr(chat).push({ id: this._mkId(), kind: 'goal_round', status: 'running',
                                   iteration: iteration || 0, max: max || 0,
                                   text: text || '', ts: Date.now() });
    this._activityRefresh();
  },

  // ── btw: side questions in the right panel's own "Zwischenfragen" tab ────
  // The whole btw exchange lives in #tab-pane-btw (thread + its own composer)
  // — clearly separate from the running answer and from the chat composer.
  // Always available: while a turn streams the answer is grounded in live
  // state (round / current tool / elapsed); when idle it answers from context.
  // Thread state: chat.btwThread = [{q, a, error, pending}], in-memory per
  // session (btw exchanges are deliberately not part of the chat history).
  _btwActiveId: null,   // the btw_id we're currently waiting on (SSE mirror)

  _btwThread(chat) {
    chat = chat || state.activeChat;
    if (!chat) return [];
    if (!Array.isArray(chat.btwThread)) chat.btwThread = [];
    return chat.btwThread;
  },

  // Open the right panel on the btw tab (also the back-compat entry point).
  openBtw() {
    const chat = state.activeChat;
    if (!chat || !chat.sessionId) {
      if (typeof showToast === 'function') showToast('Kein aktiver Chat.', true);
      return;
    }
    if (typeof openRightPanel === 'function') openRightPanel('btw');
    const inp = document.getElementById('btw-tab-input');
    if (inp) inp.focus();
  },

  promptBtw() { this.openBtw(); },

  // Render the btw pane (hint + thread) for the active chat. Called by the
  // right-panel tab switch and after every thread mutation.
  renderBtwPane() {
    const chat = state.activeChat;
    const thread = document.getElementById('btw-tab-thread');
    if (!thread) return;
    const hintEl = document.getElementById('btw-tab-hint');
    if (hintEl) {
      hintEl.textContent = (chat && chat.streaming)
        ? 'Zwischenfrage zur laufenden Antwort — unterbricht sie nicht. Z. B. „Was machst du gerade?“ / „Wie lange noch?“.'
        : 'Nebenfrage zum Gespräch — wird separat beantwortet, ohne den Chat-Verlauf zu verändern.';
    }
    const items = this._btwThread(chat);
    thread.innerHTML = '';
    for (const it of items) {
      const q = document.createElement('div');
      q.className = 'btw-msg btw-msg-q';
      q.textContent = it.q;
      thread.appendChild(q);
      const a = document.createElement('div');
      a.className = 'btw-msg btw-msg-a' + (it.pending ? ' btw-pending' : '');
      if (it.pending) {
        a.innerHTML = '<span class="tc-btw-spinner"></span> antwortet…';
      } else if (it.error) {
        a.innerHTML = '<span class="tc-btw-err"></span>';
        a.querySelector('.tc-btw-err').textContent = 'Fehler: ' + it.error;
      } else if (typeof renderMarkdown === 'function') {
        try { a.innerHTML = renderMarkdown(it.a || ''); } catch (e) { a.textContent = it.a || ''; }
      } else {
        a.textContent = it.a || '';
      }
      thread.appendChild(a);
    }
    thread.scrollTop = thread.scrollHeight;
    const sendBtn = document.getElementById('btw-tab-send');
    if (sendBtn) sendBtn.disabled = items.some(it => it.pending);
  },

  sendBtw() {
    const chat = state.activeChat;
    if (!chat || !chat.sessionId) {
      if (typeof showToast === 'function') showToast('Kein aktiver Chat.', true);
      return;
    }
    const inp = document.getElementById('btw-tab-input');
    const q = inp ? inp.value.trim() : '';
    if (!q) return;
    const items = this._btwThread(chat);
    if (items.some(it => it.pending)) return;   // one question at a time
    items.push({ q, a: '', error: '', pending: true });
    if (inp) { inp.value = ''; inp.focus(); }
    this.renderBtwPane();
    if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
    // The endpoint runs the btw call synchronously and returns the answer in the
    // response (works idle OR while streaming — no dependency on an SSE stream,
    // which doesn't exist when the chat is idle). This response IS the primary
    // render path; the btw_start/btw_done SSE events mirror it to other tabs.
    this._btwActiveId = null;
    API.btwChat(chat.sessionId, q).then((r) => {
      this._btwAnswer(chat, (r && r.answer) || '', (r && r.error) || '');
    }).catch(() => {
      this._btwAnswer(chat, '', 'Anfrage fehlgeschlagen.');
    });
  },

  // SSE mirrors. A tab that did NOT initiate the question learns about it via
  // btw_start (adds the pending Q to its thread) and btw_done (fills it). On
  // the initiating tab the HTTP response usually lands first; whoever fills
  // first flips the pending flag, so the second caller is a no-op.
  btwStart(chat, btwId, question) {
    this._btwActiveId = btwId || this._btwActiveId;
    const items = this._btwThread(chat);
    if (question && !items.some(it => it.pending)) {
      items.push({ q: question, a: '', error: '', pending: true });
      if (state.activeChat === chat) this.renderBtwPane();
      if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
    }
  },

  btwDone(chat, btwId, answer, error) {
    this._btwAnswer(chat, answer || '', error || '');
    this._btwActiveId = null;
  },

  _btwAnswer(chat, answer, error) {
    chat = chat || state.activeChat;
    const items = this._btwThread(chat);
    const e = items.find(it => it.pending);
    if (!e) return;   // already filled (HTTP response vs SSE mirror race)
    e.pending = false;
    e.a = answer || '';
    e.error = error || '';
    if (state.activeChat === chat) this.renderBtwPane();
    if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
  },

  // ── Rendering ────────────────────────────────────────────────────────────
  // Toggle the composer's pause button + repaint the queue panel.
  renderControls(chat) {
    chat = chat || state.activeChat;
    const streaming = !!(chat && chat.streaming);
    // The pause button lives in whichever composer is mounted for the current
    // view; query by its per-view id set in init.js. Pause is streaming-only.
    const ids = ['chat', 'welcome', 'project'];
    for (const p of ids) {
      const pause = document.getElementById(p + '-btn-pause');
      if (pause) {
        pause.classList.toggle('hidden', !streaming);
        pause.classList.toggle('tc-active', streaming && !!chat._turnPaused);
        pause.title = chat && chat._turnPaused
          ? 'Fortsetzen' : 'Antwort pausieren (am nächsten Rundenende)';
        // Swap the icon between pause-bars and a play-triangle.
        const icon = pause.querySelector('[data-id="btn-pause-icon"]') ||
                     pause.querySelector('svg');
        if (icon) {
          icon.innerHTML = chat && chat._turnPaused
            ? '<polygon points="7 4 20 12 7 20 7 4"/>'
            : '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>';
        }
      }
    }
    this.render(chat);
  },

  // Mount / update the queue panel above the composer. (Pending injections no
  // longer render here — they live as cards in the Aktivität tab.)
  render(chat) {
    chat = chat || state.activeChat;
    if (!chat || state.activeChat !== chat) return;
    const input = _composerInputEl();
    const box = input ? input.closest('.composer-box') : null;
    if (!box) return;

    let panel = box.querySelector('.tc-panel');
    const arr = this._arr(chat);

    if (!arr.length) { if (panel) panel.remove(); return; }
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'tc-panel';
      box.insertBefore(panel, box.firstChild);
    }

    let html = '';
    if (arr.length) {
      html += '<div class="tc-queue-head">' +
        '<span class="tc-queue-title">Warteschlange (' + arr.length + ')</span>' +
        '<button class="tc-queue-clear" onclick="ChatTurnControl.clear()">Alle entfernen</button>' +
        '</div>';
      html += '<div class="tc-queue-list">' + arr.map((item, i) => {
        const first = i === 0, last = i === arr.length - 1;
        return '<div class="tc-queue-item" data-id="' + esc(item.id) + '">' +
          '<span class="tc-queue-text" title="' + esc(item.text) + '">' + esc(item.text) + '</span>' +
          '<span class="tc-queue-actions">' +
            '<button class="tc-qa" title="Nach oben" ' + (first ? 'disabled' : '') +
              ' onclick="ChatTurnControl.move(null,\'' + esc(item.id) + '\',-1)">↑</button>' +
            '<button class="tc-qa" title="Nach unten" ' + (last ? 'disabled' : '') +
              ' onclick="ChatTurnControl.move(null,\'' + esc(item.id) + '\',1)">↓</button>' +
            '<button class="tc-qa" title="Bearbeiten" ' +
              'onclick="ChatTurnControl.edit(null,\'' + esc(item.id) + '\')">✎</button>' +
            '<button class="tc-qa" title="Jetzt senden" ' +
              'onclick="ChatTurnControl.sendNow(null,\'' + esc(item.id) + '\')">➤</button>' +
            '<button class="tc-qa tc-qa-del" title="Entfernen" ' +
              'onclick="ChatTurnControl.remove(null,\'' + esc(item.id) + '\')">✕</button>' +
          '</span>' +
        '</div>';
      }).join('') + '</div>';
    }
    panel.innerHTML = html;
  },
};

// ── Goal-Modus popover (composer 🎯 button) ──────────────────────────────────
// Set / edit / clear the per-session goal. While a goal is 'active' the server
// judges every reply against it and auto-continues (visible iterations) until
// fulfilled, judged impossible, or the iteration cap. Reuses the btw bubble's
// CSS (btw-pop / btw-scrim) with its own element ids.

function _goalButtonEl() {
  for (const id of ['btn-goal', 'project-btn-goal', 'welcome-btn-goal']) {
    const el = document.getElementById(id);
    if (el && el.offsetParent !== null) return el;   // visible instance
  }
  return document.getElementById('btn-goal');
}

function _positionGoalPop(pop) {
  const btn = _goalButtonEl();
  if (!btn) return;
  const b = btn.getBoundingClientRect();
  const pw = pop.offsetWidth || 380, ph = pop.offsetHeight || 240;
  const gap = 12, margin = 8;
  let left = b.left + b.width / 2 - pw / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - pw - margin));
  let top = b.top - ph - gap;
  let below = false;
  if (top < margin) { top = b.bottom + gap; below = true; }
  pop.style.left = left + 'px';
  pop.style.top = top + 'px';
  pop.classList.toggle('btw-pop-below', below);
  const tailX = Math.max(16, Math.min(b.left + b.width / 2 - left, pw - 16));
  pop.style.setProperty('--btw-tail-x', tailX + 'px');
}

function closeGoalPopover() {
  const p = document.getElementById('goal-pop'); if (p) p.remove();
  const s = document.getElementById('goal-scrim'); if (s) s.remove();
  if (window._goalReposition) {
    window.removeEventListener('resize', window._goalReposition);
    window.removeEventListener('scroll', window._goalReposition, true);
    window._goalReposition = null;
  }
}

function openGoalPopover() {
  const chat = state.activeChat;
  if (!chat) { if (typeof showToast === 'function') showToast('Kein aktiver Chat.', true); return; }
  if (document.getElementById('goal-pop')) { closeGoalPopover(); return; }  // toggle

  const scrim = document.createElement('div');
  scrim.className = 'btw-scrim';
  scrim.id = 'goal-scrim';
  scrim.onclick = () => closeGoalPopover();
  document.body.appendChild(scrim);

  const defMax = (state.composerDefaults && state.composerDefaults.goal_max_iterations) || 5;
  const st = chat.goalStatus || '';
  const stLine = st === 'fulfilled'
    ? '<div class="btw-pop-hint" style="color:var(--success)">✓ Ziel erreicht — neues Ziel setzen oder löschen.</div>'
    : st === 'capped'
      ? '<div class="btw-pop-hint" style="color:var(--error)">Ziel nicht erreicht (Limit/unerreichbar) — anpassen und erneut senden re-aktiviert es.</div>'
      : '';
  const pop = document.createElement('div');
  pop.className = 'btw-pop';
  pop.id = 'goal-pop';
  pop.innerHTML =
    '<div class="btw-pop-head">' +
      '<span class="tc-btw-tag">🎯</span>' +
      '<span class="btw-pop-title">Ziel (Goal-Modus)</span>' +
      '<button class="btw-pop-close" onclick="closeGoalPopover()" title="Schließen">&times;</button>' +
    '</div>' +
    '<div class="btw-pop-hint">Der Assistent prüft nach jeder Antwort, ob das Ziel erreicht ist, und arbeitet automatisch weiter, bis es erfüllt ist (max. Iterationen pro Anfrage begrenzt).</div>' +
    stLine +
    '<div class="btw-pop-composer" style="flex-direction:column;align-items:stretch;gap:8px">' +
      '<textarea id="goal-pop-input" class="btw-pop-input" rows="3" ' +
        'placeholder="Ziel beschreiben — z. B. „Der Bericht enthält alle 5 Abschnitte und jede Zahl ist belegt.“"></textarea>' +
      '<div style="display:flex;align-items:center;gap:8px">' +
        '<label style="font-size:12px;color:var(--text-300)">Max. Iterationen</label>' +
        '<input id="goal-pop-maxiter" type="number" min="1" max="10" style="width:70px" placeholder="' + defMax + '">' +
        '<span style="flex:1"></span>' +
        ((chat.goalText) ? '<button class="btw-pop-send" style="background:transparent;color:var(--error);border:1px solid var(--border-100)" onclick="clearGoalFromPopover()">Ziel löschen</button>' : '') +
        '<button class="btw-pop-send" onclick="saveGoalFromPopover()">Speichern</button>' +
      '</div>' +
    '</div>' +
    '<div class="btw-pop-tail"></div>';
  document.body.appendChild(pop);
  _positionGoalPop(pop);
  window._goalReposition = () => { const p = document.getElementById('goal-pop'); if (p) _positionGoalPop(p); };
  window.addEventListener('resize', window._goalReposition);
  window.addEventListener('scroll', window._goalReposition, true);

  const inp = document.getElementById('goal-pop-input');
  const maxinp = document.getElementById('goal-pop-maxiter');
  if (inp) {
    inp.value = chat.goalText || '';
    if (maxinp && chat.goalMaxIterations) maxinp.value = chat.goalMaxIterations;
    inp.focus();
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveGoalFromPopover(); }
      if (e.key === 'Escape') { e.preventDefault(); closeGoalPopover(); }
    });
  }
}

async function _persistGoal(chat, goal, maxIter) {
  await API.post('/v1/sessions/manage', {
    action: 'goal', session_id: chat.sessionId,
    goal: goal, goal_max_iterations: maxIter || 0,
  });
  chat.goalText = goal;
  chat.goalStatus = goal ? 'active' : '';
  chat.goalIteration = 0;
  chat.goalMaxIterations = maxIter || 0;
  if (typeof updateStatusBar === 'function') updateStatusBar();
  if (typeof renderRecentChats === 'function') renderRecentChats();
}

async function saveGoalFromPopover() {
  const chat = state.activeChat;
  if (!chat) return;
  const inp = document.getElementById('goal-pop-input');
  const goal = inp ? inp.value.trim() : '';
  if (!goal) { if (typeof showToast === 'function') showToast('Bitte ein Ziel eingeben (oder „Ziel löschen“).', true); return; }
  const maxIter = parseInt(document.getElementById('goal-pop-maxiter')?.value) || 0;
  // Goal lives on the session row — make sure one exists (mirrors caveman).
  if (!chat.sessionId) {
    try { await ensureSession(chat); } catch (_) { return; }
    if (!chat.sessionId) return;
  }
  try {
    await _persistGoal(chat, goal, maxIter);
    closeGoalPopover();
    if (typeof showToast === 'function') showToast('🎯 Goal-Modus aktiviert');
  } catch (e) {
    if (typeof showToast === 'function') showToast('Fehlgeschlagen: ' + (e.message || e), true);
  }
}

async function clearGoalFromPopover() {
  const chat = state.activeChat;
  if (!chat || !chat.sessionId) { closeGoalPopover(); return; }
  try {
    await _persistGoal(chat, '', 0);
    closeGoalPopover();
    if (typeof showToast === 'function') showToast('Ziel gelöscht');
  } catch (e) {
    if (typeof showToast === 'function') showToast('Fehlgeschlagen: ' + (e.message || e), true);
  }
}
