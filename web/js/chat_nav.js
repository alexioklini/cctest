// chat_nav.js — turn navigation / collapse / activity summary. Split from chat.js (Tier F Phase 4). Global <script>, no modules.

function turnQuestionPreview(msg, maxChars) {
  if (!msg) return '';
  let txt = '';
  if (typeof msg.content === 'string') txt = msg.content;
  else if (Array.isArray(msg.content)) {
    for (const b of msg.content) if (b?.type === 'text') txt += (b.text || '');
  }
  txt = _stripPreamble(msg, txt).replace(/\s+/g, ' ').trim();
  if (txt.length > maxChars) txt = txt.slice(0, maxChars - 1) + '…';
  return txt;
}
function turnQuestionFull(msg) {
  if (!msg) return '';
  let txt = '';
  if (typeof msg.content === 'string') txt = msg.content;
  else if (Array.isArray(msg.content)) {
    for (const b of msg.content) if (b?.type === 'text') txt += (b.text || '');
  }
  return _stripPreamble(msg, txt).trim();
}
function toggleHintExpand(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._expandedHints) chat._expandedHints = new Set();
  if (chat._expandedHints.has(turnNum)) chat._expandedHints.delete(turnNum);
  else chat._expandedHints.add(turnNum);
  renderMessages();
}
// Chat-summary block toggle. Now a plain div (not <details>) so it shares the
// animated grid-rows collapse. State lives on the chat object so it survives
// re-renders without auto-expanding when the server pushes summary updates;
// flip `.is-open` on the live node so the transition runs (a re-render would
// replace the node and skip the animation).
function toggleChatSummary() {
  const chat = state.activeChat;
  if (!chat) return;
  chat._summaryOpen = !chat._summaryOpen;
  const node = document.querySelector('.chat-summary-block[data-summary]');
  if (node) node.classList.toggle('is-open', chat._summaryOpen);
}
function listTurns() {
  const chat = state.activeChat;
  if (!chat?.messages) return [];
  const out = [];
  let n = 0;
  for (let i = 0; i < chat.messages.length; i++) {
    const m = chat.messages[i];
    if (m.role === 'user' || m.role === 'human') {
      n++;
      out.push({ turnNum: n, userIdx: i, userMsg: m });
    }
  }
  return out;
}
function turnNumForMessageIdx(idx) {
  const chat = state.activeChat;
  if (!chat?.messages) return 0;
  let n = 0;
  for (let i = 0; i <= idx && i < chat.messages.length; i++) {
    const m = chat.messages[i];
    if (m.role === 'user' || m.role === 'human') n++;
  }
  return n;
}
function toggleTurnCollapse(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();
  if (chat._collapsedTurns.has(turnNum)) chat._collapsedTurns.delete(turnNum);
  else chat._collapsedTurns.add(turnNum);
  // Animate by flipping `.is-open` on the live node (a full renderMessages()
  // would replace the node and the grid-rows transition wouldn't run). Covers
  // both regular turns (.turn-group) and compacted-context turns
  // (.lcm-summary-block), which share _collapsedTurns.
  const open = !chat._collapsedTurns.has(turnNum);
  const nodes = document.querySelectorAll(`.turn-group[data-turn="${turnNum}"], .lcm-summary-block[data-turn="${turnNum}"]`);
  if (nodes.length) nodes.forEach(n => n.classList.toggle('is-open', open));
  else renderMessages();
}
function setAllTurnsCollapsed(collapsed) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();
  if (collapsed) {
    for (const t of listTurns()) chat._collapsedTurns.add(t.turnNum);
  } else {
    chat._collapsedTurns.clear();
  }
  // Animate every turn node in place rather than re-rendering.
  document.querySelectorAll('.turn-group[data-turn], .lcm-summary-block[data-turn]').forEach(node => {
    const tn = Number(node.getAttribute('data-turn'));
    node.classList.toggle('is-open', !chat._collapsedTurns.has(tn));
  });
}
function setTurnsCollapsedRelativeTo(anchorMsgIdx, direction, collapsed) {
  // direction: 'above' | 'below' | 'self'
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();
  const anchorTurn = turnNumForMessageIdx(anchorMsgIdx);
  for (const t of listTurns()) {
    let match = false;
    if (direction === 'self') match = t.turnNum === anchorTurn;
    else if (direction === 'above') match = t.turnNum < anchorTurn;
    else if (direction === 'below') match = t.turnNum > anchorTurn;
    if (!match) continue;
    if (collapsed) chat._collapsedTurns.add(t.turnNum);
    else chat._collapsedTurns.delete(t.turnNum);
  }
  // Animate affected turn nodes in place.
  document.querySelectorAll('.turn-group[data-turn], .lcm-summary-block[data-turn]').forEach(node => {
    const tn = Number(node.getAttribute('data-turn'));
    node.classList.toggle('is-open', !chat._collapsedTurns.has(tn));
  });
}
// Turn-badge press handling: a short click toggles just this turn; a long
// press (≥ _TURN_LP_MS) expands/collapses ALL turns, mirroring the held turn's
// CURRENT state — hold an expanded turn → collapse all; hold a collapsed turn
// → expand all. The long press fires on its own timer (so it triggers even
// while the finger/button is still down); the subsequent mouseup/touchend is
// then suppressed so it doesn't also run the single-turn toggle.
const _TURN_LP_MS = 450;
let _turnPress = null; // { turnNum, timer, fired }
function turnBadgePressStart(event, turnNum) {
  if (event && event.type === 'mousedown' && event.button !== 0) return;
  turnBadgePressCancel();
  const chat = state.activeChat;
  if (!chat) return;
  const wasExpanded = !(chat._collapsedTurns && chat._collapsedTurns.has(turnNum));
  _turnPress = { turnNum, fired: false, timer: setTimeout(() => {
    if (!_turnPress) return;
    _turnPress.fired = true;
    // Mirror the held turn's state onto all turns: expanded → collapse all.
    setAllTurnsCollapsed(wasExpanded);
    // Tactile cue that the long-press took effect.
    const badge = document.querySelector(`.turn-group[data-turn="${turnNum}"] .turn-group-badge`);
    if (badge) { badge.classList.add('lp-fired'); setTimeout(() => badge.classList.remove('lp-fired'), 300); }
    // Keep the held turn in view — after collapsing/expanding all, its
    // position shifts; scroll its header back into view so the user keeps
    // their place. Wait one frame for the layout to settle.
    requestAnimationFrame(() => {
      const turnEl = document.querySelector(`.turn-group[data-turn="${turnNum}"]`);
      if (turnEl) turnEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }, _TURN_LP_MS) };
}
function turnBadgePressEnd(event, turnNum) {
  if (!_turnPress || _turnPress.turnNum !== turnNum) { turnBadgePressCancel(); return; }
  const fired = _turnPress.fired;
  turnBadgePressCancel();
  // touchend would also synthesise a mouse click → prevent the double-fire.
  if (event && event.type === 'touchend' && event.cancelable) event.preventDefault();
  if (!fired) toggleTurnCollapse(turnNum); // short click → single-turn toggle
}
function turnBadgePressCancel() {
  if (_turnPress) { clearTimeout(_turnPress.timer); _turnPress = null; }
}
function jumpToTurn(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (chat._collapsedTurns) chat._collapsedTurns.delete(turnNum);
  renderMessages();
  // Scroll the turn header into view
  requestAnimationFrame(() => {
    const el = document.querySelector(`.turn-group[data-turn="${turnNum}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      // Brief flash
      el.style.transition = 'background 0.4s';
      el.style.background = 'rgba(255,200,80,0.18)';
      setTimeout(() => { el.style.background = ''; }, 900);
    }
  });
  // Close any open dropdowns
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(d => d.classList.remove('open'));
}
function renderTurnNavMenuItems(anchorMsgIdx) {
  const turns = listTurns();
  const anchorTurn = turnNumForMessageIdx(anchorMsgIdx);
  const chat = state.activeChat;
  const collapsed = chat?._collapsedTurns || new Set();
  const thisCollapsed = collapsed.has(anchorTurn);

  // Decide bulk actions: if everything (above/below/all) is currently collapsed, offer expand; else offer collapse.
  const above = turns.filter(t => t.turnNum < anchorTurn);
  const below = turns.filter(t => t.turnNum > anchorTurn);
  const allCollapsed = turns.length > 0 && turns.every(t => collapsed.has(t.turnNum));
  const aboveAllCollapsed = above.length > 0 && above.every(t => collapsed.has(t.turnNum));
  const belowAllCollapsed = below.length > 0 && below.every(t => collapsed.has(t.turnNum));

  const caretDown = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="6 9 12 15 18 9"/></svg>';
  const caretUp = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="18 15 12 9 6 15"/></svg>';

  let html = '';
  html += `<div class="msg-edit-dropdown-section-label">Diese Anfrage</div>`;
  html += `<div class="msg-edit-dropdown-item" onclick="setTurnsCollapsedRelativeTo(${anchorMsgIdx}, 'self', ${!thisCollapsed})">
    ${thisCollapsed ? caretDown : caretUp}
    ${thisCollapsed ? 'Diese Anfrage aufklappen' : 'Diese Anfrage zuklappen'}
  </div>`;

  html += `<hr>`;
  html += `<div class="msg-edit-dropdown-section-label">Alle Anfragen</div>`;
  html += `<div class="msg-edit-dropdown-item" onclick="setAllTurnsCollapsed(${!allCollapsed})">
    ${allCollapsed ? caretDown : caretUp}
    ${allCollapsed ? 'Alle aufklappen' : 'Alle zuklappen'}
  </div>`;
  if (above.length > 0) {
    html += `<div class="msg-edit-dropdown-item" onclick="setTurnsCollapsedRelativeTo(${anchorMsgIdx}, 'above', ${!aboveAllCollapsed})">
      ${aboveAllCollapsed ? caretDown : caretUp}
      ${aboveAllCollapsed ? 'Alle oberhalb aufklappen' : 'Alle oberhalb zuklappen'} (${above.length})
    </div>`;
  }
  if (below.length > 0) {
    html += `<div class="msg-edit-dropdown-item" onclick="setTurnsCollapsedRelativeTo(${anchorMsgIdx}, 'below', ${!belowAllCollapsed})">
      ${belowAllCollapsed ? caretDown : caretUp}
      ${belowAllCollapsed ? 'Alle unterhalb aufklappen' : 'Alle unterhalb zuklappen'} (${below.length})
    </div>`;
  }

  if (turns.length > 0) {
    html += `<hr>`;
    html += `<div class="msg-edit-dropdown-section-label">Zu Anfrage springen</div>`;
    for (const t of turns) {
      const q = esc(turnQuestionPreview(t.userMsg, 60) || '(leer)');
      const isHere = t.turnNum === anchorTurn ? ' style="background:var(--bg-200)"' : '';
      html += `<div class="msg-edit-dropdown-item"${isHere} onclick="jumpToTurn(${t.turnNum})">
        <span class="turn-nav-num">${t.turnNum}.</span>
        <span class="turn-nav-q">${q}</span>
      </div>`;
    }
  }
  return html;
}
function toggleTurnNavMenu(ev, idx) {
  ev.stopPropagation();
  const all = document.querySelectorAll('.msg-edit-dropdown');
  const target = document.getElementById('turn-nav-menu-' + idx);
  all.forEach(d => { if (d !== target) d.classList.remove('open'); });
  if (target) {
    if (!target.classList.contains('open')) {
      target.innerHTML = renderTurnNavMenuItems(idx);
    }
    target.classList.toggle('open');
  }
}
// Returns the 1-based turn number that a new message would belong to,
// by counting user/human messages already in chat.messages.
function currentTurnNum(chat) {
  let n = 0;
  for (const m of chat.messages) {
    if (m.role === 'user' || m.role === 'human') n++;
  }
  return n;
}
// Renders all messages in a turn, wrapping pre-response activity (thinking +
// tool calls) in a collapsed summary block once a final assistant response exists.
// ── Activity summary state machine ─────────────────────────────────────────
// Per-turn collapse state lives on chat._activityStates (Map<turnNum, str>).
// Values: 'auto-open' | 'auto-closed' | 'user-open' | 'user-closed'
// Absent key = no activity yet seen OR history load (always closed).
//
// Rules:
//   add (new activity element during streaming):
//     first element → 'auto-open'
//     4th element   → 'auto-closed'  (only if not user-controlled)
//   response (assistant response finalised):
//     → 'auto-closed'  (only if not user-controlled)
//   user toggle:
//     → 'user-open' / 'user-closed'  (never overridden again)

function _activityCount(messages, memberIdxs) {
  let n = 0;
  for (const idx of memberIdxs) {
    const m = messages[idx];
    if (m.role === 'thinking' || m.role === 'tool_call') n++;
    // worker calls are tracked via tool_call with worker result — count the tool_call
  }
  return n;
}
// Schedule an ANIMATED auto-close for a turn that is currently rendered open.
// During streaming each tool event re-renders the whole turn block, so the
// .activity-summary node is fresh every time — we can't just toggle a class
// and expect a transition (the node had no prior `.open` state to animate
// from). Instead we keep the state `auto-open` so the next render paints it
// open, then on the following frame strip `.open` from the live node so the
// CSS grid-rows transition runs open→closed, and only THEN commit the
// `auto-closed` state (so subsequent renders keep it closed without a snap).
function _scheduleActivityAutoClose(chat, turnNum) {
  if (chat._activityPendingClose && chat._activityPendingClose.has(turnNum)) return;
  if (!chat._activityPendingClose) chat._activityPendingClose = new Set();
  chat._activityPendingClose.add(turnNum);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    chat._activityPendingClose.delete(turnNum);
    // Bail if the user took control in the meantime, or the chat switched.
    const cur = chat._activityStates && chat._activityStates.get(turnNum);
    if (cur === 'user-open' || cur === 'user-closed') return;
    const node = state.activeChat === chat
      ? document.querySelector(`.activity-summary[data-activity-turn="${turnNum}"]`)
      : null;
    if (node) node.classList.remove('open');     // triggers the transition
    chat._activityStates.set(turnNum, 'auto-closed');
  }));
}
function _activityAutoUpdate(chat, turnNum, event) {
  if (!chat._activityStates) chat._activityStates = new Map();
  const cur = chat._activityStates.get(turnNum);
  const userControlled = cur === 'user-open' || cur === 'user-closed';
  if (userControlled) return; // never touch user-controlled state

  if (event === 'add') {
    if (!cur) {
      chat._activityStates.set(turnNum, 'auto-open');
    } else if (cur === 'auto-open') {
      // Count current activity elements — if reaching 4, animate-close.
      const t = state.activeChat;
      if (t) {
        // Find this turn's memberIdxs
        let memberIdxs = null;
        let n = 0;
        for (let i = 0; i < t.messages.length; i++) {
          const m = t.messages[i];
          if (m.role === 'user' || m.role === 'human') {
            n++;
            if (n === turnNum) { memberIdxs = []; }
            else if (memberIdxs !== null) break;
          } else if (memberIdxs !== null) {
            memberIdxs.push(i);
          }
        }
        if (memberIdxs && _activityCount(t.messages, memberIdxs) >= 4) {
          // Leave state auto-open; the deferred close animates it shut.
          _scheduleActivityAutoClose(chat, turnNum);
        }
      }
    }
  } else if (event === 'response') {
    // Response finalised: animate the block shut (only if still auto-state).
    _scheduleActivityAutoClose(chat, turnNum);
  }
}
function toggleActivitySummary(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._activityStates) chat._activityStates = new Map();
  const cur = chat._activityStates.get(turnNum);
  const isOpen = cur === 'auto-open' || cur === 'user-open';
  // user-* state is sticky: once the user toggles, _activityAutoUpdate stops
  // auto-collapsing this turn (it bails on user-open/user-closed).
  chat._activityStates.set(turnNum, isOpen ? 'user-closed' : 'user-open');
  // Cancel any in-flight auto-close for this turn so a queued rAF can't fight
  // the user's explicit choice (race: tools auto-close the block, the user
  // expands it BEFORE the response arrives, then the deferred close fires).
  // The rAF already bails on user-* state, but dropping the pending marker
  // makes the intent explicit and lets a fresh schedule run later if needed.
  if (chat._activityPendingClose) chat._activityPendingClose.delete(turnNum);
  // Animate by flipping the `.open` class on the live node instead of
  // re-rendering (a full renderMessages() would replace the node and the CSS
  // grid-rows transition would never run). Fall back to a re-render only if
  // the node isn't found (shouldn't happen — the click came from it).
  const node = document.querySelector(`.activity-summary[data-activity-turn="${turnNum}"]`);
  if (node) node.classList.toggle('open', !isOpen);
  else renderMessages();
}
