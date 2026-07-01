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

  // Auto-send the head of the queue after a turn finishes (called from `done`).
  drainNext(chat) {
    chat = chat || state.activeChat;
    if (!chat || state.activeChat !== chat) return;   // only the visible chat
    const arr = this._arr(chat);
    if (!arr.length) return;
    if (chat.streaming) return;                        // safety: don't stack turns
    const item = arr.shift();
    this._persist(chat);
    this.render(chat);
    // Small yield so the just-finished turn's DOM settles before the next send.
    setTimeout(() => { try { this._sendText(chat, item.text); } catch (e) {} }, 60);
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

  // ── btw: side question answered in its own bubble ────────────────────────
  promptBtw() {
    const chat = state.activeChat;
    if (!chat || !chat.streaming || !chat.sessionId) return;
    const q = window.prompt(
      'Nebenfrage (wird separat beantwortet, ohne die laufende Antwort zu unterbrechen) — ' +
      'z. B. „Was machst du gerade?“ / „Wie lange dauert das noch?“:');
    if (q === null) return;
    const t = q.trim();
    if (!t) return;
    API.btwChat(chat.sessionId, t).catch(() => {
      if (typeof showToast === 'function') showToast('btw fehlgeschlagen', true);
    });
  },

  btwStart(chat, btwId, question) {
    if (!btwId) return;
    const container = document.getElementById('messages-container');
    if (!container) return;
    if (document.getElementById('btw-' + btwId)) return;
    const card = document.createElement('div');
    card.className = 'tc-btw-bubble tc-btw-pending';
    card.id = 'btw-' + btwId;
    card.innerHTML =
      '<div class="tc-btw-head"><span class="tc-btw-tag">btw</span> ' +
      '<span class="tc-btw-q"></span></div>' +
      '<div class="tc-btw-body"><span class="tc-btw-spinner"></span> ' +
      'antwortet nebenbei…</div>';
    card.querySelector('.tc-btw-q').textContent = question || '';
    container.appendChild(card);
    if (typeof scrollToBottom === 'function') scrollToBottom();
  },

  btwDone(chat, btwId, answer, error) {
    if (!btwId) return;
    const card = document.getElementById('btw-' + btwId);
    if (!card) return;
    card.classList.remove('tc-btw-pending');
    const body = card.querySelector('.tc-btw-body');
    if (!body) return;
    if (error) {
      body.innerHTML = '<span class="tc-btw-err"></span>';
      body.querySelector('.tc-btw-err').textContent = 'Fehler: ' + error;
      return;
    }
    // Render markdown if available, else plain text.
    if (typeof renderMarkdown === 'function') {
      try { body.innerHTML = renderMarkdown(answer || ''); return; } catch (e) {}
    }
    body.textContent = answer || '';
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
      if (btw) btw.classList.toggle('hidden', !streaming);
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
