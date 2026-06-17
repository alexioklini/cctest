// panels_chats.js — chat view, status bar, scroll anchors, chats list. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

/* ═══════════════════════════════════════════════════════════
   CHAT VIEW MANAGEMENT
   ═══════════════════════════════════════════════════════════ */
function updateChatView() {
  const chat = state.activeChat;
  if (!chat) return;

  // Update header — show agent name, then chat title (editable on click)
  const agent = state.agents.find(a => (a.id || a.name) === state.activeAgentId);
  const agentDisplay = agent?.display_name || state.activeAgentId || 'Chat';
  const chatTitle = chat.chatTitle || '';
  // Build the favourite descriptor for this chat (skipped for not-yet-saved
  // chats and for synthetic scheduled-run sessions, which the user pins via
  // their schedule entry instead).
  const sid = chat.sessionId || '';
  const isSynthetic = sid.startsWith('sched-') || chat.readonly;
  const favOpts = (sid && !isSynthetic) ? {
    item_type: state.currentProject ? 'project_chat' : 'chat',
    item_id: sid,
    agent_id: chat.agentId || state.activeAgentId || 'main',
    title: chatTitle || 'Unbenannter Chat',
  } : null;
  // LLM-generated summary surfaces only as a hover tooltip on the title —
  // never replaces it. Falls back to summary as visible label only when
  // there's no title yet (rare, e.g. a brand-new chat before the first
  // user message has landed).
  const tip = chat.chatSummary || '';
  const visibleTitle = chatTitle || chat.chatSummary || '';
  if (state.currentProject) {
    const projAgent = chat.agentId || state._projectDetailAgent || state.activeAgentId;
    updatePageHeader(visibleTitle || agentDisplay, state.currentProject, projAgent, favOpts, tip);
  } else if (visibleTitle) {
    updatePageHeader(visibleTitle, agentDisplay, null, favOpts, tip);
  } else {
    updatePageHeader(agentDisplay, null, null, favOpts);
  }

  // Update model
  updateModelSelectorDisplay(chat.model);

  // Update status bar
  updateStatusBar();

  // Render messages
  renderMessages();
  scrollToBottom();
}

// Role-gate: admins see everything; powerusers lose pool/queue (infra-internal);
// users additionally lose tokens in/out, speed, cost, and the inspect button.
// Returns the role string so callers can short-circuit later sections.
function applyStatusBarRoleVisibility() {
  const role = (state.authUser && state.authUser.role) || 'admin';
  const hide = (id, on) => { const el = document.getElementById(id); if (el) el.style.display = on ? 'none' : ''; };
  const isUser = role === 'user';
  const isPower = role === 'poweruser';
  hide('status-tokens-in-wrap',  isUser);
  hide('status-tokens-out-wrap', isUser);
  hide('status-speed-wrap',      isUser);
  hide('status-cost-wrap',       isUser);
  hide('status-inspect-btn',     isUser);
  // Pool + queue: hidden for powerusers and users (admins-only).
  // Their monitors set display:flex on poll — see _renderPoolIndicator + QueueMonitor._render.
  if (isUser || isPower) {
    hide('status-warmpool', true);
    hide('status-queue', true);
  }
  return role;
}

function updateStatusBar() {
  const chat = state.activeChat;
  if (!chat) return;

  document.getElementById('status-agent').textContent = state.activeAgentId || '';
  document.getElementById('status-model').textContent = '';
  document.getElementById('status-session').textContent = chat.sessionId ? (chat.sessionId.startsWith('sched-') ? chat.sessionId : chat.sessionId.substring(0,8)) : '';

  // Save-to-memory toggle: green=on, amber=auto, grey=off
  // Mirror the same state to both chat- and welcome-screen composers.
  const mode = chat.memoryMode || (chat.saveToMemory ? 'on' : 'off');
  for (const memBtn of _composerToggleEls('btn-save-to-memory')) {
    if (mode === 'on') {
      memBtn.style.color = 'var(--success, #22c55e)';
      memBtn.title = 'Gedächtnis: an (alle Nachrichten werden gespeichert) — zum Umschalten klicken';
    } else if (mode === 'auto') {
      const clf = state.mempalaceClassifier || {};
      const detail = clf.enabled && clf.model
        ? `LLM-Klassifizierer: ${modelShortName(clf.model, false)}`
        : clf.min_turns ? `mind. ${clf.min_turns} Turns` : 'Standardregeln';
      memBtn.style.color = 'var(--warning, #f59e0b)';
      memBtn.title = `Gedächtnis: automatisch (${detail}) — zum Umschalten klicken`;
    } else {
      memBtn.style.color = '';
      memBtn.title = 'Gedächtnis: aus — zum Umschalten klicken';
    }
  }

  // Caveman mode toggle: icon per level (off=spaceship, lite=car, full=horse, ultra=campfire)
  const cm = chat.cavemanMode || 0;
  const cavTitle = {
    0: 'Caveman: aus (Raumschiff) — zum Umschalten klicken',
    1: 'Caveman: leicht (Auto) — zum Umschalten klicken',
    2: 'Caveman: voll (Pferd) — zum Umschalten klicken',
    3: 'Caveman: ultra (Lagerfeuer) — zum Umschalten klicken',
  }[cm];
  for (const cavBtn of _composerToggleEls('btn-caveman')) {
    cavBtn.innerHTML = cavemanIconFor(cm);
    cavBtn.title = cavTitle;
    cavBtn.style.color = '';
  }

  // Transparent-anonymisation sticky preference indicator (step 6.3). Shows
  // a shield-with-checkmark next to the composer when chat.gdprActionPref
  // is set, so the user sees PII handling is automatic for this chat and
  // can reset with a click. Hidden when no preference is active.
  const gdprPref = (chat.gdprActionPref || '').trim();
  const gdprLabel = {
    'anonymise':   'Personenbezogene Daten werden vor dem Senden automatisch anonymisiert',
    'local_model': 'Nachrichten mit personenbezogenen Daten werden automatisch an ein lokales Modell geleitet',
    'continue':    'PII-Warnungen werden automatisch übergangen',
  }[gdprPref] || '';
  for (const gBtn of _composerToggleEls('btn-gdpr-pref')) {
    if (gdprPref && gdprLabel) {
      gBtn.style.display = '';
      gBtn.title = `${gdprLabel} — zum Zurücksetzen klicken`;
    } else {
      gBtn.style.display = 'none';
    }
  }

  // Warmup indicator
  let warmupEl = document.getElementById('status-warmup');
  if (!warmupEl) {
    warmupEl = document.createElement('div');
    warmupEl.id = 'status-warmup';
    warmupEl.className = 'status-item';
    warmupEl.innerHTML = '<span style="font-size:11px;color:#8b5cf6;font-weight:500;animation:pulse 1s infinite">Aufwärmen …</span>';
    document.getElementById('status-bar').insertBefore(warmupEl, document.getElementById('status-bar').children[2]);
  }
  warmupEl.style.display = chat._warmingUp ? '' : 'none';
  // Welcome view warmup indicator
  const wwEl = document.getElementById('welcome-warmup');
  if (wwEl) wwEl.style.display = chat._warmingUp ? '' : 'none';

  // Compute token totals from message metadata
  let totalIn = 0, totalOut = 0, lastSpeed = null;
  const msgs = chat.messages || [];
  for (const m of msgs) {
    if (m.role === 'assistant' && m.metadata) {
      totalIn += m.metadata.tokens_in || 0;
      totalOut += m.metadata.tokens_out || 0;
      const _mTot = (m.metadata.tokens_in || 0) + (m.metadata.tokens_out || 0);
      if (m.metadata.duration > 0 && _mTot > 0) {
        lastSpeed = Math.round(_mTot / m.metadata.duration);  // total in+out / wall-clock
      }
    }
  }
  // Also use chat-level tracking from done events
  if (chat._tokensIn) totalIn = chat._tokensIn;
  if (chat._tokensOut) totalOut = chat._tokensOut;
  if (chat._lastSpeed) lastSpeed = chat._lastSpeed;
  // While a turn is streaming, add THIS turn's live tokens on top of the
  // committed session total (the in-flight assistant message has no metadata
  // yet). Cleared at turn start + on done, so we never double-count once the
  // final metadata lands.
  if (chat.streaming) {
    if (chat._liveTurnTokensIn) totalIn += chat._liveTurnTokensIn;
    if (chat._liveTurnTokensOut) totalOut += chat._liveTurnTokensOut;
  }

  document.getElementById('status-tokens-in').textContent = totalIn ? totalIn.toLocaleString() : '0';
  document.getElementById('status-tokens-out').textContent = totalOut ? totalOut.toLocaleString() : '0';
  document.getElementById('status-speed').textContent = lastSpeed ? `${lastSpeed} tok/s` : '-';

  // Context fill bar — use last API tokens_in as real context usage
  const wrap = document.getElementById('status-context-wrap');
  const fill = document.getElementById('status-context-fill');
  const label = document.getElementById('status-context-label');
  // Find last-round prompt tokens (= size of the most recent API call, not cumulative across tool rounds)
  let lastApiIn = 0;
  for (let mi = msgs.length - 1; mi >= 0; mi--) {
    const md = msgs[mi].metadata;
    if (msgs[mi].role === 'assistant' && md && (md.last_tokens_in || md.tokens_in)) {
      lastApiIn = md.last_tokens_in || md.tokens_in;
      break;
    }
  }
  if (chat._lastApiIn) lastApiIn = chat._lastApiIn;
  const contextUsed = lastApiIn || chat.totalTokens || 0;
  const modelMaxContext = (state.modelsConfig?.models?.[chat.model]?.max_context) || 0;
  const effectiveMaxContext = modelMaxContext || chat.maxContext;
  if (contextUsed > 0 && effectiveMaxContext) {
    const pct = Math.min(100, Math.round(contextUsed / effectiveMaxContext * 100));
    wrap.style.display = '';
    fill.style.width = Math.max(pct, 1) + '%';
    fill.className = 'context-fill' + (pct >= 80 ? ' danger' : pct >= 50 ? ' warn' : '');
    const fmtK = (n) => n >= 1000 ? (n/1000).toFixed(1) + 'K' : n.toString();
    label.textContent = `${fmtK(contextUsed)} / ${fmtK(effectiveMaxContext)} (${pct}%)`;
    label.title = `${contextUsed.toLocaleString()} / ${effectiveMaxContext.toLocaleString()} Token (letzte API-Eingabe)`;

    // LCM warning banner: show at ≥60% — compaction is manual-only, so the
    // banner stays visible until the user runs ✂️ Compact or the conversation
    // resets.
    const banner = document.getElementById('lcm-warn-banner');
    if (banner) {
      const isStreaming = !!document.getElementById('stop-btn')?.offsetParent;
      if (pct >= 60 && !isStreaming) {
        const txt = document.getElementById('lcm-warn-text');
        if (txt) txt.textContent = `Der Kontext ist zu ${pct}% gefüllt — jetzt verdichten, um das Gespräch fortzusetzen.`;
        banner.classList.add('visible');
      } else {
        banner.classList.remove('visible');
      }
    }
  } else {
    wrap.style.display = 'none';
    document.getElementById('lcm-warn-banner')?.classList.remove('visible');
  }

  // Session cost indicator — shows current session $ spend. Quota state
  // (with role-based thresholds + cycle reset) lives in the Plan-usage pill.
  const costWrap = document.getElementById('status-cost-wrap');
  const costLabel = document.getElementById('status-cost-label');
  let sessionCost = 0;
  let sawCostField = false;
  for (let mi = msgs.length - 1; mi >= 0; mi--) {
    const m = msgs[mi];
    if (m.role === 'assistant' && m._cost !== undefined) {
      sessionCost = m._cost || 0;
      sawCostField = true;
      break;
    }
  }
  if (chat._sessionCost !== undefined) { sessionCost = chat._sessionCost || 0; sawCostField = true; }
  if (sawCostField) {
    costWrap.style.display = '';
    if (sessionCost <= 0) {
      costLabel.textContent = '0.00';
      costLabel.style.color = 'var(--text-400)';
      costWrap.title = 'Sitzungskosten: $0.00 — für dieses Modell sind keine Preise hinterlegt. cost_input/cost_output unter Einstellungen → Modelle festlegen.';
    } else {
      costLabel.textContent = sessionCost < 1 ? sessionCost.toFixed(3) : sessionCost.toFixed(2);
      costLabel.style.color = '';
      costWrap.title = `Sitzungskosten: $${sessionCost.toFixed(4)}`;
    }
  } else {
    costWrap.style.display = 'none';
  }

  // Final pass: hide role-restricted items. Runs last so it wins over the
  // data-driven branches above (e.g. cost-wrap re-show on cost data arrival).
  applyStatusBarRoleVisibility();
}

function scrollToBottom() {
  const el = document.getElementById('messages-scroll');
  if (el) {
    // Pin synchronously FIRST so there's no painted intermediate frame. During
    // streaming, renderMessages() replaces the whole turn-group node when a new
    // tool card is added; that momentarily shrinks scrollHeight and the browser
    // clamps scrollTop toward the top. Setting scrollTop now (same JS turn,
    // before paint) corrects it so the view never visibly jumps up. The rAF
    // follow-up catches any late layout (images/iframes/markdown reflow).
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
      updateScrollAnchors();
    });
  }
}

function scrollToTop() {
  const el = document.getElementById('messages-scroll');
  if (el) el.scrollTop = 0;
}

// Count turn-groups whose top edge sits above (-) / below (+) the viewport
// top edge. Used both to pick the step target and to decide when a step
// button is redundant with the full top/bottom jump.
function _turnsRelToViewport(el) {
  const groups = Array.from(el.querySelectorAll('.turn-group'));
  const elTop = el.getBoundingClientRect().top;
  const EPS = 2; // px slack so the current top group counts as neither
  let above = 0, below = 0;
  for (const g of groups) {
    const off = g.getBoundingClientRect().top - elTop;
    if (off < -EPS) above++;
    else if (off > EPS) below++;
  }
  return { groups, above, below };
}

// Jump one turn (= one user-question .turn-group) up (-1) or down (+1) relative
// to what's currently at the top of the message viewport. Scrolls the target
// turn header to the top edge.
function scrollTurn(dir) {
  const el = document.getElementById('messages-scroll');
  if (!el) return;
  const { groups } = _turnsRelToViewport(el);
  if (!groups.length) return;
  const elTop = el.getBoundingClientRect().top;
  const tops = groups.map(g => g.getBoundingClientRect().top - elTop);
  const EPS = 2;
  let target = null;
  if (dir < 0) {
    for (let i = tops.length - 1; i >= 0; i--) { if (tops[i] < -EPS) { target = groups[i]; break; } }
  } else {
    for (let i = 0; i < tops.length; i++) { if (tops[i] > EPS) { target = groups[i]; break; } }
  }
  if (!target) { if (dir < 0) scrollToTop(); else scrollToBottom(); return; }
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// One button per direction (short press = step a turn, long press = top/bottom;
// see wireLongPress). Show the up arrow only when scrolled away from the top,
// the down arrow only when away from the bottom. The arrows are position:fixed,
// so we anchor them to the message area's current viewport rect each call
// (centered horizontally, just inside the top / bottom edge of the scroll area
// — the bottom edge already sits above the composer).
function updateScrollAnchors() {
  const el = document.getElementById('messages-scroll');
  const upBtn = document.getElementById('scroll-up');
  const downBtn = document.getElementById('scroll-down');
  if (!el || !upBtn || !downBtn) return;
  const PAD = 24; // px slack so the arrow hides when "basically" at the edge
  const scrollable = el.scrollHeight - el.clientHeight;
  const atTop = el.scrollTop <= PAD;
  const atBottom = scrollable - el.scrollTop <= PAD;
  const r = el.getBoundingClientRect();
  // Clamp so the fixed buttons never poke past the viewport edge — at
  // fractional browser zoom (110%, 125%, …) sub-pixel rounding otherwise
  // lets a 1px sliver extend the document and spawn root scrollbars.
  const BTN = 34, HALF = BTN / 2;
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const cx = Math.round(r.left + r.width / 2);
  // Both buttons sit on the same bottom row, side by side around the centre:
  // ↑ left of centre, ↓ right of centre. GAP/2 keeps them from touching while
  // clamping each so it can't poke past the viewport edge (fractional zoom).
  const GAP = 12;
  const off = HALF + GAP / 2;
  const clampX = (x) => Math.round(Math.min(Math.max(x, HALF + 1), vw - HALF - 1));
  const botY = Math.round(Math.min(Math.max(r.bottom - 46, 1), vh - BTN - 1));
  upBtn.style.left = clampX(cx - off) + 'px';
  upBtn.style.top = botY + 'px';
  downBtn.style.left = clampX(cx + off) + 'px';
  downBtn.style.top = botY + 'px';
  upBtn.classList.toggle('visible', !atTop && r.height > 0);
  downBtn.classList.toggle('visible', !atBottom && r.height > 0);
  // Edge fade mask (Claude-desktop style): fade the top/bottom of the scroll
  // area only when there is hidden content beyond that edge. Drive a CSS
  // mask-image via data attributes the .messages-scroll rule reads.
  const hasOverflow = scrollable > PAD;
  el.dataset.fadeTop = (hasOverflow && !atTop) ? '1' : '0';
  el.dataset.fadeBottom = (hasOverflow && !atBottom) ? '1' : '0';
}

function initScrollAnchors() {
  const el = document.getElementById('messages-scroll');
  if (!el || el._scrollAnchorsWired) return;
  el._scrollAnchorsWired = true;
  el.addEventListener('scroll', updateScrollAnchors, { passive: true });
  window.addEventListener('resize', updateScrollAnchors, { passive: true });
  const upBtn = document.getElementById('scroll-up');
  const downBtn = document.getElementById('scroll-down');
  wireLongPress(upBtn, () => scrollTurn(-1), () => scrollToTop());
  wireLongPress(downBtn, () => scrollTurn(1), () => scrollToBottom());
  // Reveal the buttons only while the mouse is actively moving inside the chat
  // area; fade them out after a short idle pause or when the pointer leaves.
  // The buttons are position:fixed siblings (not children) of the scroll area,
  // so we also treat hovering a button as activity — otherwise reaching for one
  // would count as leaving the chat area and fade it out from under the cursor.
  const IDLE_MS = 1500;
  let idleTimer = null;
  const setActive = (on) => {
    if (upBtn) upBtn.classList.toggle('mouse-active', on);
    if (downBtn) downBtn.classList.toggle('mouse-active', on);
  };
  const poke = () => {
    setActive(true);
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => setActive(false), IDLE_MS);
  };
  const leave = () => {
    if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
    setActive(false);
  };
  el.addEventListener('mousemove', poke, { passive: true });
  el.addEventListener('scroll', poke, { passive: true });   // scrolling also reveals, then idle-fades
  el.addEventListener('mouseleave', leave);
  [upBtn, downBtn].forEach((b) => {
    if (!b) return;
    b.addEventListener('mousemove', poke, { passive: true });
    b.addEventListener('mouseleave', leave);
  });
  updateScrollAnchors();
}

/* ═══════════════════════════════════════════════════════════
   CHATS LIST
   ═══════════════════════════════════════════════════════════ */
async function loadChatsList() {
  const container = document.getElementById('chats-list');
  container.innerHTML = '<div style="padding:16px;color:var(--text-400)">Wird geladen …</div>';

  try {
    // Load sessions for all agents
    let allSessions = [];
    for (const agent of state.agents) {
      try {
        const aid = agent.id || agent.name;
        const data = await API.getSessionsForAgent(aid, state.chatsFilter === 'archived' ? 'archived' : undefined);
        const sessions = data.sessions || [];
        for (const s of sessions) {
          if ((s.message_count || 0) > 0 && !(s.project || s.project_id)) allSessions.push({...s, agentId: aid, agentDisplay: agent.display_name || aid});
        }
        state.agentSessions[aid] = { sessions, loaded: true };
      } catch(e) {}
    }

    allSessions.sort((a,b) => new Date(b.last_active||0) - new Date(a.last_active||0));

    // Apply search filter
    if (state.chatsSearchQuery) {
      const q = state.chatsSearchQuery.toLowerCase();
      allSessions = allSessions.filter(s =>
        (s.title || '').toLowerCase().includes(q) ||
        (s.summary || '').toLowerCase().includes(q) ||
        (s.agentId || '').toLowerCase().includes(q)
      );
    }

    container.innerHTML = '';
    for (const s of allSessions) {
      const csid = s.id || s.session_id;
      // Title primary; summary as hover tooltip only.
      const title = s.title || s.summary || `Chat ${csid?.substring(0,8)}`;
      const tip = s.summary ? ` title="${esc(s.summary)}"` : '';
      const div = document.createElement('div');
      div.className = 'chat-list-item';
      div.innerHTML = `
        <div class="chat-list-item-title"${tip}>${esc(title)}</div>
        <div class="chat-list-item-meta">
          Letzte Nachricht ${relativeTime(s.last_active)}
          ${s.agentId ? ' in <span class="chat-list-item-agent">' + esc(s.agentDisplay) + '</span>' : ''}
        </div>
      `;
      div.onclick = () => openSession(csid, s.agentId);
      container.appendChild(div);
    }

    if (!allSessions.length) {
      container.innerHTML = '<div style="padding:32px;text-align:center;color:var(--text-400)">Keine Chats gefunden</div>';
    }
  } catch(e) {
    container.innerHTML = '<div style="padding:16px;color:var(--error)">Chats konnten nicht geladen werden</div>';
  }
}

function filterChatsList() {
  state.chatsSearchQuery = document.getElementById('chats-search').value;
  loadChatsList();
}

function setChatFilter(filter, el) {
  state.chatsFilter = filter;
  document.querySelectorAll('.chats-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  loadChatsList();
}
