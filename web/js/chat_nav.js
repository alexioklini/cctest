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
// Chat-summary block toggle handler. Bound to the <details> ontoggle event so
// we capture both clicks and keyboard activation. State lives on the chat
// object so it survives re-renders without auto-expanding when the server
// pushes summary updates.
function toggleChatSummary(detailsEl) {
  const chat = state.activeChat;
  if (!chat) return;
  chat._summaryOpen = !!detailsEl.open;
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
  renderMessages();
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
  renderMessages();
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
  renderMessages();
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
      const q = esc(turnQuestionPreview(t.userMsg, 60) || '(empty)');
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
function _activityAutoUpdate(chat, turnNum, event) {
  if (!chat._activityStates) chat._activityStates = new Map();
  const cur = chat._activityStates.get(turnNum);
  const userControlled = cur === 'user-open' || cur === 'user-closed';
  if (userControlled) return; // never touch user-controlled state

  if (event === 'add') {
    if (!cur) {
      chat._activityStates.set(turnNum, 'auto-open');
    } else if (cur === 'auto-open') {
      // Count current activity elements — if reaching 4, close
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
          chat._activityStates.set(turnNum, 'auto-closed');
        }
      }
    }
  } else if (event === 'response') {
    chat._activityStates.set(turnNum, 'auto-closed');
  }
}
function toggleActivitySummary(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._activityStates) chat._activityStates = new Map();
  const cur = chat._activityStates.get(turnNum);
  const isOpen = cur === 'auto-open' || cur === 'user-open';
  chat._activityStates.set(turnNum, isOpen ? 'user-closed' : 'user-open');
  // Re-render to apply the new state
  renderMessages();
}
