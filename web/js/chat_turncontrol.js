// chat_turncontrol.js — end-user controls over a STREAMING chat turn:
//   • Queue      — type more while it's answering; queued messages auto-send as
//                  normal turns when the current one finishes. Edit/remove/
//                  reorder/inject-now/send-now. Persisted per session.
//   • Pause      — soft-hold the turn at the next round boundary; resume later.
//   • Inject     — splice a clarification into the RUNNING turn (model sees it
//                  next round). Distinct from the queue (which starts new turns).
//   • btw        — ask a side question answered in a SEPARATE bubble, grounded in
//                  what the agent is doing right now (round, current tool, elapsed).
//
// Single global (net-globals invariant). All DOM is mounted transiently:
//   - the queue panel + pending-inject chip go ABOVE the active composer,
//   - btw bubbles + the "injected" note go into #messages-container,
// so renderMessages()/the message model stay untouched. Backend endpoints:
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

  notePendingInjection(chat, text) {
    chat = chat || state.activeChat;
    if (!chat) return;
    chat._pendingInjections = chat._pendingInjections || [];
    chat._pendingInjections.push(text);
    this.render(chat);
  },

  commitInjection(chat, text) {
    chat = chat || state.activeChat;
    if (!chat) return;
    // Drop it from pending (first match) and drop a small note row into the flow.
    if (Array.isArray(chat._pendingInjections)) {
      const i = chat._pendingInjections.indexOf(text);
      if (i >= 0) chat._pendingInjections.splice(i, 1);
    }
    this._mountInjectedNote(text);
    this.render(chat);
  },

  _mountInjectedNote(text) {
    const container = document.getElementById('messages-container');
    if (!container) return;
    const row = document.createElement('div');
    row.className = 'tc-injected-note';
    row.innerHTML =
      '<span class="tc-injected-arrow">↳</span> ' +
      '<span class="tc-injected-label">Eingefügt (wird in dieser Runde berücksichtigt):</span> ' +
      '<span class="tc-injected-text"></span>';
    row.querySelector('.tc-injected-text').textContent = text;
    container.appendChild(row);
    if (typeof scrollToBottom === 'function') scrollToBottom();
  },

  // ── btw: side question in a dedicated centered modal overlay ─────────────
  // The whole btw exchange lives in its own modal, NOT in the message flow —
  // so it's clearly separate from the running answer and survives re-renders.
  // Always available: while a turn streams the answer is grounded in live state
  // (round / current tool / elapsed); when idle it answers from context.
  _btwActiveId: null,   // the btw_id we're currently waiting on

  // Find the visible btw button for the current composer view (the one that
  // was clicked) so the bubble can point at it.
  _btwButtonEl() {
    for (const id of ['chat-btn-btw', 'project-btn-btw', 'welcome-btn-btw']) {
      const el = document.getElementById(id);
      if (el && el.offsetParent !== null) return el;   // visible
    }
    return document.getElementById('chat-btn-btw');
  },

  openBtw() {
    const chat = state.activeChat;
    if (!chat || !chat.sessionId) {
      if (typeof showToast === 'function') showToast('Kein aktiver Chat.', true);
      return;
    }
    if (document.getElementById('btw-pop')) { this.closeBtw(); return; }  // toggle
    const streaming = !!chat.streaming;
    const hint = streaming
      ? 'Unterbricht die laufende Antwort nicht. Z. B. „Was machst du gerade?“ / „Wie lange noch?“.'
      : 'Gerade läuft keine Antwort — wird aus dem bisherigen Gespräch beantwortet.';

    // Light click-catcher (no dim) so a click outside closes the bubble.
    const scrim = document.createElement('div');
    scrim.className = 'btw-scrim';
    scrim.id = 'btw-scrim';
    scrim.onclick = () => ChatTurnControl.closeBtw();
    document.body.appendChild(scrim);

    // The speech bubble itself, as a popover anchored to the button.
    const pop = document.createElement('div');
    pop.className = 'btw-pop';
    pop.id = 'btw-pop';
    pop.innerHTML =
      '<div class="btw-pop-head">' +
        '<span class="tc-btw-tag">btw</span>' +
        '<span class="btw-pop-title">Zwischenfrage</span>' +
        '<button class="btw-pop-close" onclick="ChatTurnControl.closeBtw()" title="Schließen">&times;</button>' +
      '</div>' +
      '<div class="btw-pop-hint">' + esc(hint) + '</div>' +
      '<div class="btw-pop-thread" id="btw-modal-thread"></div>' +
      '<div class="btw-pop-composer">' +
        '<textarea id="btw-modal-input" class="btw-pop-input" rows="2" ' +
          'placeholder="Nebenfrage stellen…"></textarea>' +
        '<button class="btw-pop-send" id="btw-modal-send" ' +
          'onclick="ChatTurnControl.sendBtw()">Fragen</button>' +
      '</div>' +
      '<div class="btw-pop-tail"></div>';
    document.body.appendChild(pop);
    this._positionBtwPop(pop);
    // Reposition on resize/scroll while open.
    this._btwReposition = () => { const p = document.getElementById('btw-pop'); if (p) ChatTurnControl._positionBtwPop(p); };
    window.addEventListener('resize', this._btwReposition);
    window.addEventListener('scroll', this._btwReposition, true);

    const inp = document.getElementById('btw-modal-input');
    if (inp) {
      const comp = _composerInputEl();
      if (comp && comp.value && comp.value.trim()) inp.value = comp.value.trim();
      inp.focus();
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ChatTurnControl.sendBtw(); }
        if (e.key === 'Escape') { e.preventDefault(); ChatTurnControl.closeBtw(); }
      });
    }
  },

  // Anchor the bubble above the btw button, with the tail pointing down at it.
  // Clamps horizontally to the viewport; flips below the button if there's no
  // room above.
  _positionBtwPop(pop) {
    const btn = this._btwButtonEl();
    if (!btn) return;
    const b = btn.getBoundingClientRect();
    const pw = pop.offsetWidth || 360, ph = pop.offsetHeight || 260;
    const gap = 12, margin = 8;
    // Horizontal: center on the button, clamp to viewport.
    let left = b.left + b.width / 2 - pw / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - pw - margin));
    // Vertical: prefer above; flip below if it wouldn't fit.
    let top = b.top - ph - gap;
    let below = false;
    if (top < margin) { top = b.bottom + gap; below = true; }
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    pop.classList.toggle('btw-pop-below', below);
    // Point the tail at the button centre (relative to the bubble's left edge).
    const tailX = Math.max(16, Math.min(b.left + b.width / 2 - left, pw - 16));
    pop.style.setProperty('--btw-tail-x', tailX + 'px');
  },

  // Back-compat name (composer button + terminal call this).
  promptBtw() { this.openBtw(); },

  closeBtw() {
    const p = document.getElementById('btw-pop'); if (p) p.remove();
    const s = document.getElementById('btw-scrim'); if (s) s.remove();
    if (this._btwReposition) {
      window.removeEventListener('resize', this._btwReposition);
      window.removeEventListener('scroll', this._btwReposition, true);
      this._btwReposition = null;
    }
    this._btwActiveId = null;
  },

  sendBtw() {
    const chat = state.activeChat;
    if (!chat || !chat.sessionId) return;
    const inp = document.getElementById('btw-modal-input');
    const q = inp ? inp.value.trim() : '';
    if (!q) return;
    // Render the question + a pending answer bubble in the thread immediately.
    const thread = document.getElementById('btw-modal-thread');
    if (thread) {
      thread.innerHTML =
        '<div class="btw-msg btw-msg-q"></div>' +
        '<div class="btw-msg btw-msg-a btw-pending" id="btw-modal-answer">' +
          '<span class="tc-btw-spinner"></span> antwortet…</div>';
      thread.querySelector('.btw-msg-q').textContent = q;
    }
    if (inp) { inp.value = ''; inp.focus(); }
    const sendBtn = document.getElementById('btw-modal-send');
    if (sendBtn) sendBtn.disabled = true;
    const _pop = document.getElementById('btw-pop');
    if (_pop) this._positionBtwPop(_pop);   // thread grew → re-anchor
    // The endpoint runs the btw call synchronously and returns the answer in the
    // response (works idle OR while streaming — no dependency on an SSE stream,
    // which doesn't exist when the chat is idle). This response IS the primary
    // render path; the btw_done SSE event is only a mirror for other tabs.
    this._btwActiveId = null;
    API.btwChat(chat.sessionId, q).then((r) => {
      this._btwAnswer((r && r.answer) || '', (r && r.error) || '');
    }).catch(() => {
      this._btwAnswer('', 'Anfrage fehlgeschlagen.');
    });
  },

  // Called from the SSE handlers. The modal is the single render target; we
  // ignore btw_start (the pending bubble is already shown by sendBtw) and fill
  // the answer on btw_done. Match on the active id when we have it.
  btwStart(chat, btwId, question) { this._btwActiveId = btwId || this._btwActiveId; },

  btwDone(chat, btwId, answer, error) {
    if (this._btwActiveId && btwId && btwId !== this._btwActiveId) return;
    this._btwAnswer(answer || '', error || '');
    this._btwActiveId = null;
  },

  _btwAnswer(answer, error) {
    const el = document.getElementById('btw-modal-answer');
    const sendBtn = document.getElementById('btw-modal-send');
    if (sendBtn) sendBtn.disabled = false;
    if (!el) return;
    el.classList.remove('btw-pending');
    if (error) { el.innerHTML = '<span class="tc-btw-err"></span>';
                 el.querySelector('.tc-btw-err').textContent = 'Fehler: ' + error; return; }
    if (typeof renderMarkdown === 'function') {
      try { el.innerHTML = renderMarkdown(answer); } catch (e) { el.textContent = answer; }
    } else {
      el.textContent = answer;
    }
    const pop = document.getElementById('btw-pop');
    if (pop) this._positionBtwPop(pop);   // answer grew the bubble → re-anchor
  },

  // ── Rendering ────────────────────────────────────────────────────────────
  // Toggle the composer's btw/pause buttons + repaint the queue panel + chips.
  renderControls(chat) {
    chat = chat || state.activeChat;
    const streaming = !!(chat && chat.streaming);
    // The two composer buttons live in whichever composer is mounted for the
    // current view; query by their per-view id set in init.js.
    const ids = ['chat', 'welcome', 'project'];
    for (const p of ids) {
      const btw = document.getElementById(p + '-btn-btw') ||
                  (p === 'chat' ? document.getElementById('chat-btn-btw') : null);
      const pause = document.getElementById(p + '-btn-pause');
      // btw is ALWAYS available (grounded in live state while streaming, in
      // conversation context when idle) → never hidden. Pause is streaming-only.
      if (btw) btw.classList.remove('hidden');
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

  // Mount / update the queue panel + pending-inject chip above the composer.
  render(chat) {
    chat = chat || state.activeChat;
    if (!chat || state.activeChat !== chat) return;
    const input = _composerInputEl();
    const box = input ? input.closest('.composer-box') : null;
    if (!box) return;

    let panel = box.querySelector('.tc-panel');
    const arr = this._arr(chat);
    const pending = (chat._pendingInjections || []);
    const nothing = !arr.length && !pending.length;

    if (nothing) { if (panel) panel.remove(); return; }
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'tc-panel';
      box.insertBefore(panel, box.firstChild);
    }

    let html = '';
    if (pending.length) {
      html += '<div class="tc-pending">' +
        pending.map(t => '<span class="tc-pending-chip">↳ ' +
          esc(t) + ' <span class="tc-pending-hint">(nächste Runde)</span></span>').join('') +
        '</div>';
    }
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
