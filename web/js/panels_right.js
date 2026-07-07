// panels_right.js — right-panel mechanics, turn-grouping, references pane. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

function initRightPanelResize() {
  const handle = document.getElementById('right-panel-resize-handle');
  const panel = document.getElementById('right-panel');
  if (!handle || !panel) return;
  handle.addEventListener('mousedown', (e) => {
    const startX = e.clientX;
    const startW = panel.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const onMove = (e) => {
      const newW = Math.min(800, Math.max(320, startW + (startX - e.clientX)));
      panel.style.width = newW + 'px';
    };
    const onUp = () => {
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      localStorage.setItem('right-panel-width', panel.style.width);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  });
  const saved = localStorage.getItem('right-panel-width');
  if (saved) panel.style.width = saved;
}

// Counts of items per pane, in tab display order.
function _rightPanelTabCounts() {
  const sessionId = state.activeChat?.sessionId;
  const refs = (typeof collectChatReferences === 'function') ? collectChatReferences() : { cited: [], searched: [] };
  return {
    attachments: (typeof collectChatAttachments === 'function') ? collectChatAttachments().length : 0,
    references: (refs.cited.length + refs.searched.length),
    artifacts: sessionId ? ((state.artifacts[sessionId] || []).length) : 0,
    websuche: (typeof webBasketCount === 'function') ? webBasketCount() : 0,
  };
}

// First tab (in display order) that has data, or null if all empty. In a
// code-mode chat the Artefakte tab is hidden, so don't auto-select it.
function firstTabWithData() {
  const c = _rightPanelTabCounts();
  const isCode = (typeof _workdirIsCodeChat === 'function') && _workdirIsCodeChat();
  const order = isCode ? ['attachments', 'references'] : ['attachments', 'references', 'artifacts'];
  for (const tab of order) {
    if (c[tab] > 0) return tab;
  }
  return null;
}

function openRightPanel(tab) {
  const panel = document.getElementById('right-panel');
  if (!panel) return;
  panel.classList.add('open');
  state.rightPanelOpen = true;
  // Any explicit open re-arms auto-open for subsequent new refs/artifacts.
  state.userClosedRightPanel = false;
  // Code-mode chat: hide the tabs that don't apply (Artefakte / Web-Adressen)
  // BEFORE picking the tab so a hidden tab isn't chosen. No-op in normal chats.
  // (The working-directory tree now lives in the bottom panel, not a tab.)
  if (typeof updateCodeModeTabs === 'function') updateCodeModeTabs();
  // Show/hide the workflow-run tabs based on whether this chat is a run.
  if (typeof updateWorkflowTabs === 'function') updateWorkflowTabs();
  // Tab selection priority: explicit arg (e.g. artifact auto-open) wins;
  // else the user's last chosen tab if they've picked one this session;
  // else the first tab that has data; else fall back to attachments.
  const chosen = tab
    || (state.userPickedTab ? state.rightPanelTab : null)
    || firstTabWithData()
    || 'attachments';
  switchRightTab(chosen);
  initRightPanelResize();
  setRightPanelGlow(false);
  syncRightPanelToggle();
}

// Pulse the toggle button to signal new panel data (any type) while the
// panel is closed. No-op when the panel is open. Cleared on open.
function setRightPanelGlow(on) {
  const btn = document.getElementById('toggle-right-panel-btn');
  if (!btn) return;
  btn.classList.toggle('glow', !!on && !state.rightPanelOpen);
}

function toggleRightPanel() {
  if (state.rightPanelOpen) closeRightPanel(true);
  else openRightPanel();
}

function syncRightPanelToggle() {
  const btn = document.getElementById('toggle-right-panel-btn');
  if (btn) btn.classList.toggle('active', state.rightPanelOpen);
}

// The right panel (attachments / references / artifacts / Websuche) only
// makes sense for an active chat session. On every other view (welcome,
// chats list, projects, project-detail, artifacts browse, scheduled,
// workflows, translation, data, favourites) there's no session in scope, so
// hide the toggle button entirely and close the panel if it was left open.
// The close is programmatic (not userInitiated) so returning to a chat
// restores the panel's prior auto-open behaviour. Called at the end of
// navigateTo.
function updateRightPanelButtonVisibility() {
  const btn = document.getElementById('toggle-right-panel-btn');
  const makesSense = state.currentView === 'chat'
    && !!(state.activeChat && state.activeChat.sessionId);
  if (btn) btn.style.display = makesSense ? '' : 'none';
  if (!makesSense && state.rightPanelOpen) closeRightPanel(false);
  // Off the chat view: no session in scope — stop polling and hide the pill.
  if (!makesSense) {
    if (typeof stopBackgroundTasksPoll === 'function') stopBackgroundTasksPoll();
    const pill = document.getElementById('bgtasks-pill');
    if (pill) pill.style.display = 'none';
  } else if (typeof refreshBackgroundTasksPill === 'function') {
    refreshBackgroundTasksPill();
  }
}

function closeRightPanel(userInitiated = false) {
  const panel = document.getElementById('right-panel');
  if (panel) panel.classList.remove('open');
  state.rightPanelOpen = false;
  // A deliberate user close suppresses auto-open until reload. Programmatic
  // closes (e.g. switching sessions) leave the flag untouched.
  if (userInitiated) state.userClosedRightPanel = true;
  state.activeArtifactId = null;
  state.activeArtifactVersion = null;
  state.artifactSourceMode = false;
  syncRightPanelToggle();
}

// Re-render the currently open pane's content + badges. Used after a turn
// finishes so the panel reflects refs/attachments/artifacts gathered during
// the turn (the streaming events only opportunistically refreshed it).
function refreshRightPanelContent() {
  updateRightPanelBadges();
  // Update the activity pill immediately from this turn's tool calls (reads
  // chat.messages synchronously) — don't wait on the async background-task
  // fetch, which only covers async tasks and may report 0 for a sync-only turn.
  if (typeof refreshBackgroundTasksPill === 'function') refreshBackgroundTasksPill();
  // A turn may have spawned a background task — reload so the pill/panel pick
  // it up and the poll starts.
  if (typeof loadBackgroundTasks === 'function') loadBackgroundTasks();
  if (!state.rightPanelOpen) return;
  // Keep the code-mode tab set in sync (hide Artefakte/Web-Adressen). No-op
  // in normal chats.
  if (typeof updateCodeModeTabs === 'function') updateCodeModeTabs();
  if (typeof updateWorkflowTabs === 'function') updateWorkflowTabs();
  const tab = state.rightPanelTab;
  if (tab === 'attachments') renderAttachmentsPane();
  else if (tab === 'wf-statistik' && typeof renderWorkflowStatistikPane === 'function') renderWorkflowStatistikPane();
  else if (tab === 'wf-quellcode' && typeof renderWorkflowQuellcodePane === 'function') renderWorkflowQuellcodePane();
  else if (tab === 'wf-protokoll' && typeof renderWorkflowProtokollPane === 'function') renderWorkflowProtokollPane();
  else if (tab === 'references') renderReferencesPane();
  else if (tab === 'artifacts' && !state.activeArtifactId) showArtifactList();
  else if (tab === 'bgtasks' && typeof renderBackgroundTasksPane === 'function') renderBackgroundTasksPane();
  else if (tab === 'btw' && typeof ChatTurnControl !== 'undefined') ChatTurnControl.renderBtwPane();
  if (_activePanelTurn != null) syncRightPanelToActiveTurn(_activePanelTurn);
}

// User clicked a panel tab — record the explicit choice so reopening restores
// it (vs. the auto "first tab with data" on a fresh session).
function selectRightTab(tabName) {
  state.userPickedTab = true;
  switchRightTab(tabName);
}

function switchRightTab(tabName) {
  state.rightPanelTab = tabName;
  // Toggle tab buttons
  document.querySelectorAll('.right-panel-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  // Toggle panes
  document.querySelectorAll('.right-tab-pane').forEach(pane => {
    pane.classList.toggle('active', pane.id === 'tab-pane-' + tabName);
  });
  // Populate content on switch
  if (tabName === 'attachments') renderAttachmentsPane();
  if (tabName === 'references') renderReferencesPane();
  if (tabName === 'artifacts' && !state.activeArtifactId) showArtifactList();
  if (tabName === 'websuche' && typeof renderWebsuchePane === 'function') renderWebsuchePane();
  if (tabName === 'bgtasks' && typeof renderBackgroundTasksPane === 'function') {
    if (typeof _bgLiveReconcile === 'function') _bgLiveReconcile();
    renderBackgroundTasksPane();
  }
  if (tabName === 'btw' && typeof ChatTurnControl !== 'undefined') ChatTurnControl.renderBtwPane();
  if (tabName === 'wf-statistik' && typeof renderWorkflowStatistikPane === 'function') renderWorkflowStatistikPane();
  if (tabName === 'wf-quellcode' && typeof renderWorkflowQuellcodePane === 'function') renderWorkflowQuellcodePane();
  if (tabName === 'wf-protokoll' && typeof renderWorkflowProtokollPane === 'function') renderWorkflowProtokollPane();
  updateRightPanelBadges();
  // Re-apply the active-turn focus to the freshly rendered pane.
  if (_activePanelTurn != null) syncRightPanelToActiveTurn(_activePanelTurn);
}

/* ───────────────────────────────────────────────────────────
   Scroll-sync: the turn scrolled into view in the message list drives
   which per-turn section is expanded in the right panel (when open).
   ─────────────────────────────────────────────────────────── */
let _turnScrollObserver = null;
let _turnVisibility = new Map();   // turnNum -> intersectionRatio
let _activePanelTurn = null;
// True while syncRightPanelToActiveTurn is programmatically flipping <details>
// .open — suppresses the ontoggle handler so scroll-driven opens/closes aren't
// recorded as user choices (which would permanently pin every visited turn and
// break further auto-follow).
let _programmaticPanelToggle = false;

// (Re)attach the IntersectionObserver to the current .turn-group elements.
// Called after every renderMessages() since the turn DOM is rebuilt.
function initTurnScrollSync() {
  const root = document.getElementById('messages-scroll');
  if (!root) return;
  if (_turnScrollObserver) _turnScrollObserver.disconnect();
  _turnVisibility = new Map();
  _turnScrollObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const tn = parseInt(e.target.getAttribute('data-turn'), 10);
      if (Number.isNaN(tn)) continue;
      if (e.isIntersecting && e.intersectionRatio > 0) _turnVisibility.set(tn, e.intersectionRatio);
      else _turnVisibility.delete(tn);
    }
    _recomputeActiveTurn();
  }, { root, threshold: [0, 0.25, 0.5, 1] });
  // Only real turn groups drive focus — LCM compaction blocks have a
  // data-turn but no corresponding per-turn panel section.
  root.querySelectorAll('.turn-group[data-turn]')
    .forEach(el => _turnScrollObserver.observe(el));
}

// Pick the topmost visible turn (smallest turnNum in view) as the active one
// — that's the one the user is reading down into.
function _recomputeActiveTurn() {
  if (!_turnVisibility.size) return;
  let best = null;
  for (const tn of _turnVisibility.keys()) {
    if (best == null || tn < best) best = tn;
  }
  if (best === _activePanelTurn) return;
  _activePanelTurn = best;
  if (state.rightPanelOpen) syncRightPanelToActiveTurn(best);
}

// Expand the active turn's section in the current tab, collapse the rest,
// and scroll it into the panel's view. Auto-driven and authoritative:
// it persists the open/closed decision into the per-pane open-map so a
// re-render keeps the same focus. A manual toggle wins transiently until
// the next scroll re-drives focus.
function syncRightPanelToActiveTurn(turnNum) {
  if (turnNum == null) return;
  const pane = state.rightPanelTab;
  const containerId = pane === 'references' ? 'refs-content'
    : pane === 'attachments' ? 'attachments-grid'
    : 'artifact-turn-groups';
  const container = document.getElementById(containerId);
  if (!container) return;
  let target = null;
  // These open/close flips are scroll-driven, not user choices — guard the
  // ontoggle handler so they don't get written into the per-turn openMap
  // (doing so would pin every visited turn and stop further auto-follow).
  _programmaticPanelToggle = true;
  try {
    container.querySelectorAll('.panel-turn-section').forEach(sec => {
      const tn = parseInt(sec.getAttribute('data-turn'), 10);
      const isActive = (tn === turnNum);
      if (sec.open !== isActive) sec.open = isActive;
      sec.classList.toggle('panel-turn-active', isActive);
      if (isActive) target = sec;
    });
  } finally {
    _programmaticPanelToggle = false;
  }
  if (target && typeof target.scrollIntoView === 'function') {
    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function updateRightPanelBadges() {
  const chat = state.activeChat;
  const sessionId = chat?.sessionId;
  // Attachments count
  const attachCount = collectChatAttachments().length;
  const attachBadge = document.getElementById('tab-badge-attachments');
  if (attachBadge) attachBadge.textContent = attachCount || '0';
  // References count (cited + searched)
  const _refs = collectChatReferences();
  const refsCount = _refs.cited.length + _refs.searched.length;
  const refsBadge = document.getElementById('tab-badge-references');
  if (refsBadge) refsBadge.textContent = refsCount || '0';
  // Artifacts count
  const artifactCount = sessionId ? (state.artifacts[sessionId] || []).length : 0;
  const artBadge = document.getElementById('tab-badge-artifacts');
  if (artBadge) artBadge.textContent = artifactCount || '0';
  // Websuche basket count
  const webBadge = document.getElementById('tab-badge-websuche');
  if (webBadge) webBadge.textContent = (typeof webBasketCount === 'function' ? webBasketCount() : 0) || '0';
  // Activity count: all tool calls (sync + background) of this session.
  const bgCount = (typeof backgroundActivityCount === 'function') ? backgroundActivityCount() : 0;
  const bgBadge = document.getElementById('tab-badge-bgtasks');
  if (bgBadge) bgBadge.textContent = bgCount || '0';
  // Zwischenfragen (btw) thread length
  const btwBadge = document.getElementById('tab-badge-btw');
  if (btwBadge) {
    btwBadge.textContent = (chat && Array.isArray(chat.btwThread) ? chat.btwThread.length : 0) || '0';
  }
}

/* ───────────────────────────────────────────────────────────
   Per-turn grouping for the right panel (references / attachments /
   artifacts). All three panes share the same shape: every turn in the
   chat gets a collapsible <details> section (empty turns marked), plus
   an optional "ungrouped" section for items with no turn anchor (legacy
   artifacts written before message_idx existed). Scroll-tracking
   (syncRightPanelToActiveTurn) auto-expands the turn scrolled into view.
   ─────────────────────────────────────────────────────────── */

// Short label for a turn header, derived from the opening user message.
function _turnLabel(turn) {
  const q = (typeof turnQuestionFull === 'function' ? turnQuestionFull(turn.userMsg) : '') || '';
  const trimmed = q.replace(/\s+/g, ' ').trim();
  if (!trimmed) return `Anfrage ${turn.turnNum}`;
  return trimmed.length > 60 ? trimmed.slice(0, 57) + '…' : trimmed;
}

// Render the active tab's content as per-turn collapsible sections.
//   pane:      'references' | 'attachments' | 'artifacts'
//   itemsFor:  (turnNum) => HTML string for that turn's items ('' = empty)
//   countFor:  (turnNum) => number of items in that turn
//   ungrouped: { html, count } for items with no turn anchor (or null)
//   emptyAll:  HTML shown when there are no turns AND no items at all
function renderTurnGroupedPane(container, pane, { itemsFor, countFor, ungrouped, emptyAll }) {
  if (!container) return;
  const turns = (typeof listTurns === 'function' ? listTurns() : []);
  const chat = state.activeChat;
  if (!chat) { container.innerHTML = emptyAll || ''; return; }
  if (!chat._panelOpenTurns) chat._panelOpenTurns = {};
  const openMap = (chat._panelOpenTurns[pane] = chat._panelOpenTurns[pane] || {});

  const totalItems = turns.reduce((s, t) => s + (countFor(t.turnNum) || 0), 0)
    + (ungrouped ? (ungrouped.count || 0) : 0);
  if (!turns.length && !totalItems) { container.innerHTML = emptyAll || ''; return; }

  // Default open state: only turns that have items, and never auto-open more
  // than the latest non-empty turn so the pane isn't a wall of expanded
  // sections. The user's manual toggles (openMap) win once set.
  let lastNonEmpty = 0;
  for (const t of turns) if ((countFor(t.turnNum) || 0) > 0) lastNonEmpty = t.turnNum;

  let html = '';
  for (const t of turns) {
    const count = countFor(t.turnNum) || 0;
    const body = count ? itemsFor(t.turnNum) : '<div class="panel-turn-empty">—</div>';
    const userSet = Object.prototype.hasOwnProperty.call(openMap, t.turnNum);
    const open = (userSet ? openMap[t.turnNum] : (t.turnNum === lastNonEmpty && count > 0)) ? 'open' : '';
    html += `
      <details class="refs-section panel-turn-section" data-turn="${t.turnNum}" ${open}
               ontoggle="onPanelTurnToggle('${pane}', ${t.turnNum}, this.open)">
        <summary class="refs-section-header">
          <span class="refs-section-disclosure">▸</span>
          <span class="refs-section-label" style="text-transform:none;letter-spacing:0">Anfrage ${t.turnNum}</span>
          <span class="panel-turn-hint">${esc(_turnLabel(t))}</span>
          <span class="refs-section-count">${count}</span>
        </summary>
        <div class="refs-section-body">${body}</div>
      </details>`;
  }
  if (ungrouped && ungrouped.count) {
    const userSet = Object.prototype.hasOwnProperty.call(openMap, 0);
    const open = (userSet ? openMap[0] : false) ? 'open' : '';
    html += `
      <details class="refs-section panel-turn-section" data-turn="0" ${open}
               ontoggle="onPanelTurnToggle('${pane}', 0, this.open)">
        <summary class="refs-section-header">
          <span class="refs-section-disclosure">▸</span>
          <span class="refs-section-label">Ohne Zuordnung</span>
          <span class="refs-section-count">${ungrouped.count}</span>
        </summary>
        <div class="refs-section-body">${ungrouped.html}</div>
      </details>`;
  }
  container.innerHTML = html;
  // Re-assert the scroll-driven active turn so a content re-render (new ref /
  // artifact arriving) doesn't snap focus back to the default lastNonEmpty.
  if (state.rightPanelOpen && _activePanelTurn != null && state.rightPanelTab === pane) {
    syncRightPanelToActiveTurn(_activePanelTurn);
  }
}

// Remember the user's manual open/close so a re-render (or scroll-sync)
// doesn't fight their choice.
function onPanelTurnToggle(pane, turnNum, isOpen) {
  if (_programmaticPanelToggle) return;   // scroll-sync flip, not a user choice
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._panelOpenTurns) chat._panelOpenTurns = {};
  const openMap = (chat._panelOpenTurns[pane] = chat._panelOpenTurns[pane] || {});
  openMap[turnNum] = isOpen;
}

function _refCardHtml(ref) {
  const snippetHtml = ref.snippet ? `<div class="ref-card-snippet">${esc(ref.snippet)}</div>` : '';
  // Wiki source: a 'Wiki-Seite' card showing the page title; click opens the page.
  const isWiki = ref.source_kind === 'wiki' ||
    (typeof ref.source_file === 'string' && ref.source_file.startsWith('wiki/'));
  if (isWiki) {
    const pid = ref.wiki_page_id || (ref.source_file || '').split('/')[1] || '';
    const bookSvg = `<svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="#fff" stroke-width="1.5"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`;
    return `
      <div class="ref-card" onclick="wikiOpenFromCitation('${esc(pid)}')">
        <div class="ref-card-preview">
          <div class="ref-thumb-placeholder" style="display:flex;align-items:center;justify-content:center;background:var(--accent-brand)">${bookSvg}</div>
          <span class="ref-domain-pill">Wiki-Seite</span>
        </div>
        <div class="ref-card-body">
          <div class="ref-card-title">${esc(ref.title || 'Wiki-Seite')}</div>
          ${snippetHtml}
        </div>
      </div>
    `;
  }
  const isProject = ref.domain === 'project';
  const clickHandler = isProject
    ? `openProjectSource(this.dataset.link)`
    : `window.open('${esc(ref.link)}', '_blank')`;
  const ext = (ref.title || '').split('.').pop().toLowerCase();
  const iconBg = isProject ? {
    pdf: '#d33', docx: '#2b579a', pptx: '#d24726',
    xlsx: '#217346', eml: '#0072c6', msg: '#0072c6',
    md: 'var(--text-400)', txt: 'var(--text-400)',
  }[ext] || 'var(--accent-brand)' : '';
  const previewHtml = isProject
    ? `<div class="ref-thumb-placeholder" style="display:flex;align-items:center;justify-content:center;background:${iconBg};color:#fff;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em">${esc(ext || 'file')}</div>`
    : `<img class="ref-thumb" src="${esc(`https://api.microlink.io/?url=${encodeURIComponent(ref.link)}&screenshot=true&meta=false&embed=screenshot.url`)}" onerror="this.parentElement.innerHTML='<div class=\\'ref-thumb-placeholder\\'><svg viewBox=\\'0 0 24 24\\' width=\\'32\\' height=\\'32\\' fill=\\'none\\' stroke=\\'var(--text-400)\\' stroke-width=\\'1\\' opacity=\\'0.3\\'><path d=\\'M12 2a10 10 0 110 20 10 10 0 010-20z\\'/><path d=\\'M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10A15.3 15.3 0 0112 2z\\'/></svg></div>'" loading="lazy" alt="">`;
  const faviconHtml = isProject
    ? ''
    : `<img class="ref-favicon" src="${esc(ref.favicon)}" onerror="this.style.display='none'" alt="">`;
  const domainLabel = isProject ? 'Projektquelle' : esc(ref.domain);
  // Inline content preview for previewable text-ish project sources (md/txt/
  // code/csv): a lazy "Vorschau" toggle loads + renders the file inline so the
  // user sees the actual content, not just a gray type placeholder. Binary docs
  // (pdf/docx/…) keep open-in-tab only (no inline render possible).
  const canPreview = isProject && _REF_PREVIEW_EXTS.has(ext);
  const previewToggle = canPreview
    ? `<button class="ref-preview-toggle" onclick="event.stopPropagation();toggleRefPreview(this)" data-path="${esc(ref.link)}" data-ext="${esc(ext)}">Vorschau anzeigen</button>
       <div class="ref-inline-preview" style="display:none"></div>`
    : '';
  return `
    <div class="ref-card" data-link="${esc(ref.link)}" onclick="${clickHandler}">
      <div class="ref-card-preview">
        ${faviconHtml}
        ${previewHtml}
        <span class="ref-domain-pill">${domainLabel}</span>
      </div>
      <div class="ref-card-body">
        <div class="ref-card-title">${esc(ref.title)}</div>
        ${snippetHtml}
        <div class="ref-card-url" style="word-break:break-all">${esc(ref.link)}</div>
        ${previewToggle}
      </div>
    </div>
  `;
}

// Project-source extensions that can render inline in a reference card.
const _REF_PREVIEW_EXTS = new Set([
  'md', 'markdown', 'txt', 'csv', 'tsv', 'json', 'yaml', 'yml', 'xml', 'log',
  'py', 'js', 'ts', 'sh', 'sql', 'html', 'htm', 'ini', 'cfg', 'conf',
]);

// Lazy inline preview for a project-source reference card. Fetches the file
// (auth'd /v1/files/download) once, renders markdown / syntax-highlighted code
// into the card, and toggles visibility on subsequent clicks.
async function toggleRefPreview(btn) {
  const box = btn.nextElementSibling;
  if (!box) return;
  // Already loaded → just toggle.
  if (box.dataset.loaded === '1') {
    const showing = box.style.display !== 'none';
    box.style.display = showing ? 'none' : 'block';
    btn.textContent = showing ? 'Vorschau anzeigen' : 'Vorschau ausblenden';
    return;
  }
  const path = btn.dataset.path;
  const ext = btn.dataset.ext;
  box.style.display = 'block';
  box.innerHTML = '<div style="font-size:11px;color:var(--text-400);padding:6px 0">Wird geladen …</div>';
  btn.textContent = 'Vorschau ausblenden';
  try {
    const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(path)}`;
    const resp = await fetch(url, { headers: API._headers() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    let text = await resp.text();
    const MAX = 20000;  // cap so a huge file doesn't bloat the pane
    const truncated = text.length > MAX;
    if (truncated) text = text.slice(0, MAX);
    if (ext === 'md' || ext === 'markdown') {
      box.innerHTML = `<div class="ref-inline-md msg-content">${renderMarkdown(text)}</div>`;
      box.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch (_) {} });
    } else {
      const lang = (typeof hljs !== 'undefined' && hljs.getLanguage(ext)) ? ext : 'plaintext';
      let highlighted;
      try { highlighted = hljs.highlight(text, { language: lang }).value; } catch (_) { highlighted = esc(text); }
      box.innerHTML = `<pre class="ref-inline-code"><code class="hljs">${highlighted}</code></pre>`;
    }
    if (truncated) box.insertAdjacentHTML('beforeend', '<div style="font-size:11px;color:var(--text-400);padding:4px 0">… (gekürzt — vollständig über Klick auf die Karte öffnen)</div>');
    box.dataset.loaded = '1';
  } catch (e) {
    box.innerHTML = `<div style="font-size:11px;color:var(--text-400);padding:6px 0">Vorschau nicht verfügbar: ${esc(e.message || e)}</div>`;
  }
}

// References attributed per turn. Walks messages once; every ref-bearing
// row (live tool_result, or an assistant's metadata.tools[] after reload)
// is attributed to the turn it sits in. Cited vs searched uses the same
// basename-match-against-assistant-text rule as collectChatReferences.
function _referencesByTurn() {
  const chat = state.activeChat;
  const out = {};  // turnNum -> { cited: [], searched: [], seen: Set }
  if (!chat?.messages) return out;
  const citedBasenames = new Set();
  for (const msg of chat.messages) {
    // Include assistant_segment rows: with chronological interleave the early
    // answer text (and its citations) lives in segment rows, not the final
    // assistant message, so scanning only 'assistant' would miss those cites.
    if ((msg.role === 'assistant' || msg.role === 'assistant_segment') && msg.content) {
      for (const k of extractCitedBasenamesFromText(msg.content)) citedBasenames.add(k);
    }
  }
  let turnNum = 0;
  for (const msg of chat.messages) {
    if (msg.role === 'user' || msg.role === 'human') turnNum++;
    const candidates = [];
    if (msg.role === 'tool_result') candidates.push(msg);
    if (msg.role === 'assistant' && msg.metadata && Array.isArray(msg.metadata.tools)) {
      for (const t of msg.metadata.tools) {
        if (t && t.name) candidates.push({ role: 'tool_result', name: t.name, result: t.result });
      }
    }
    if (!candidates.length) continue;
    const bucket = out[turnNum] || (out[turnNum] = { cited: [], searched: [], seen: new Set() });
    for (const c of candidates) {
      for (const ref of extractReferencesFromToolResult(c)) {
        if (bucket.seen.has(ref.link)) continue;
        bucket.seen.add(ref.link);
        (citedBasenames.has(refBasenameKey(ref)) ? bucket.cited : bucket.searched).push(ref);
      }
    }
  }
  return out;
}

function renderReferencesPane() {
  const container = document.getElementById('refs-content');
  if (!container) return;
  const byTurn = _referencesByTurn();
  const turnRefs = (tn) => byTurn[tn] || { cited: [], searched: [] };
  renderTurnGroupedPane(container, 'references', {
    countFor: (tn) => { const r = turnRefs(tn); return r.cited.length + r.searched.length; },
    itemsFor: (tn) => {
      const r = turnRefs(tn);
      let h = '';
      if (r.cited.length) {
        h += `<div class="refs-subgroup-label">Zitiert</div>${r.cited.map(_refCardHtml).join('')}`;
      }
      if (r.searched.length) {
        h += `<div class="refs-subgroup-label">Durchsucht</div>${r.searched.map(_refCardHtml).join('')}`;
      }
      return h;
    },
    ungrouped: null,
    emptyAll: '<div class="attach-empty">Keine Quellen in diesem Chat</div>',
  });
}

// Open a project document (PDF/DOCX/PPTX/XLSX/EML/.md/...) in a new tab.
// PDFs render inline in the browser via the application/pdf MIME from the
// download endpoint; everything else triggers a normal save-as. We fetch
// with the auth-token header and pipe to a blob URL because the download
// endpoint is auth-gated and we can't put the JWT on a query string safely.
async function openProjectSource(absPath) {
  if (!absPath) return;
  try {
    const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(absPath)}`;
    const resp = await fetch(url, {
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
    });
    if (!resp.ok) {
      const err = await resp.text().catch(() => '');
      showToast(`${absPath.split('/').pop()} kann nicht geöffnet werden: ${resp.status} ${err.slice(0, 80)}`, true);
      return;
    }
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    // Open in new tab. The browser uses the response's Content-Type to
    // decide inline-render vs download. PDFs render inline; binaries
    // (.docx, .xlsx, .pptx) download.
    window.open(blobUrl, '_blank');
    // Revoke after a delay so the new tab has time to load. 60s is
    // arbitrary but long enough; the browser keeps a reference.
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
  } catch (e) {
    showToast(`Öffnen fehlgeschlagen: ${e.message || e}`, true);
  }
}

/* ═══════════════════════════════════════════════════════════
   REFERENCES PANEL
   ═══════════════════════════════════════════════════════════ */

// Mirror of ChatHandlerMixin._is_document_source — drops synthetic
// MemPalace addresses (chat turns, summaries, user-profile sections)
// that aren't openable file paths. Applied to BOTH live server-stored
// refs and legacy-fallback parsing so persisted-bad refs don't render.
function isDocumentRef(ref) {
  const sf = ref && (ref.source_file || ref.link) || '';
  if (!sf) return false;
  if (sf.startsWith('session/') || sf.startsWith('user/') || sf.startsWith('team/')) return false;
  if (/^\d+$/.test(sf)) return false;
  if (/^[a-f0-9]+#summary$/i.test(sf)) return false;
  return true;
}

function extractReferencesFromToolResult(msg) {
  // References are extracted server-side (ChatHandlerMixin._extract_references)
  // and stored in msg.references. The client just reads that field — no
  // per-tool name checks, no path resolution, no regex fallbacks here.
  if (msg.role !== 'tool_result') return [];
  if (Array.isArray(msg.references) && msg.references.length) {
    // Filter persisted-bad refs from legacy server versions.
    const onlyDocs = msg.references.filter(r => !r || r.domain !== 'project' || isDocumentRef(r));
    return onlyDocs;
  }
  // Legacy fallback: old persisted messages without server-side refs field.
  if (!msg.result) return [];
  const isWebTool = msg.name === 'exa_search' || msg.name === 'web_fetch';
  const isProjectTool = msg.name === 'mempalace_query'
                     || msg.name === 'mempalace_kg_query'
                     || msg.name === 'mempalace_kg_search'
                     || msg.name === 'mempalace_kg_neighbors';
  if (!isWebTool && !isProjectTool) return [];
  const refs = [];
  const resultStr = typeof msg.result === 'string' ? msg.result : JSON.stringify(msg.result);

  // ── Project-knowledge tools: surface source_file as clickable refs ──
  // mempalace_query returns {drawers: [{source_file, snippet, ...}]}.
  // mempalace_kg_* return {triples: [{source_file, source_drawer_id, ...}]}.
  // We dedupe by source_file, render basename as title, link = the
  // original-binary path (resolving .brain-extracted/foo.pdf.md → foo.pdf
  // via the converter's naming convention).
  if (isProjectTool) {
    let parsed = null;
    try { parsed = JSON.parse(resultStr); } catch(e) { parsed = null; }
    const items = parsed
      ? [...(parsed.drawers || []),
         ...(parsed.triples || []),
         ...(parsed.edges || [])]
      : [];
    const seen = new Set();
    const resolveOriginal = (sf) => {
      if (!sf) return sf;
      // Case 1: .brain-extracted/<name>.<ext>.md → <name>.<ext> in the parent dir
      const m = sf.match(/^(.+)\/\.brain-extracted\/(.+)\.md$/);
      if (m) return `${m[1]}/${m[2]}`;
      // Case 2: defensive — any path ending in `.<binext>.md` where binext is
      // a known binary doc-convert format. Catches cases where the agent
      // cited the .md companion in plain text without the .brain-extracted
      // prefix, or where a future doc_convert layout drops the prefix.
      const m2 = sf.match(/^(.+\.(pdf|docx|pptx|xlsx|xlsm|eml|msg))\.md$/i);
      if (m2) return m2[1];
      return sf;
    };
    for (const it of items) {
      const sf = it && it.source_file;
      if (!sf || seen.has(sf)) continue;
      seen.add(sf);  // claim before predicate so regex sweep can't re-add
      // Drop chat-derived drawers and any other synthetic non-file
      // sources (user-profile sections, team-wing addresses, …).
      if (!isDocumentRef({ source_file: sf, ...(it.room ? {} : {}) }) ||
          ['chat', 'chat_summary', 'chat_attachment', 'user_profile'].includes(it.room || '')) {
        continue;
      }
      const original = resolveOriginal(sf);
      const basename = original.split('/').pop() || original;
      // Use the snippet from a drawer if present; otherwise format the
      // triple/edge. Keeps each ref card informative without bloat.
      let snippet = '';
      if (it.snippet) snippet = String(it.snippet).slice(0, 280);
      else if (it.subject && it.predicate && it.object) {
        snippet = `(${it.subject}) — [${it.predicate}] → (${it.object})`.slice(0, 280);
      } else if (it.text) snippet = String(it.text).slice(0, 280);
      const ref = {
        title: basename,
        link: original,           // absolute path to the original binary
        snippet: snippet,
        domain: 'project',        // marker so the panel can render differently
        favicon: '',
        source_file: sf,          // raw drawer/triple source for debugging
      };
      if (sf.startsWith('wiki/')) {
        // Live/legacy path (no server title): label by the drawer's '# Title'
        // first line if present, else the id; mark as wiki so the card opens it.
        ref.source_kind = 'wiki';
        ref.wiki_page_id = sf.split('/')[1] || '';
        const m1 = (it.text || '').match(/^#\s+(.+)/);
        ref.title = (m1 && m1[1].trim()) || basename;
      }
      refs.push(ref);
    }
    // Regex top-up — always runs, not just when JSON parse failed. Even
    // when JSON.parse succeeds, the persisted `metadata.tools[i].result`
    // may be truncated past the first drawer (capped at ~4KB on the
    // server), so the JSON object is well-formed but only contains the
    // drawers that fit. Sweep the raw string for any `"source_file": "..."`
    // tokens, skip ones already in `seen`, and add the rest as refs with
    // an empty snippet. Without this top-up, reload of a multi-source
    // answer loses every reference past the first.
    {
      const sfMatches = [...resultStr.matchAll(/"source_file"\s*:\s*"([^"]+)"/g)];
      for (const m of sfMatches) {
        const sf = m[1];
        if (!sf || seen.has(sf)) continue;
        seen.add(sf);
        if (!isDocumentRef({ source_file: sf })) continue;
        const original = resolveOriginal(sf);
        const basename = original.split('/').pop() || original;
        refs.push({
          title: basename, link: original, snippet: '',
          domain: 'project', favicon: '', source_file: sf,
        });
      }
    }
    return refs;
  }


  // Worker-subagent envelope: the raw result is stored as an artifact and only
  // a summary + pre-extracted `references` array reach the client. Prefer that
  // explicit list over re-parsing the summary text.
  try {
    const data = JSON.parse(resultStr);
    if (data && data.worker && Array.isArray(data.references)) {
      for (const r of data.references) {
        if (!r || !r.link) continue;
        refs.push({
          title: r.title || r.domain || r.link,
          link: r.link,
          snippet: r.snippet || '',
          domain: r.domain || '',
          favicon: r.domain ? `https://www.google.com/s2/favicons?domain=${r.domain}&sz=32` : '',
        });
      }
      if (refs.length) return refs;
    }
  } catch(e) { /* fall through to legacy parsing */ }

  // Try full JSON parse first (legacy direct-tool path — small results that fit inline)
  try {
    const data = JSON.parse(resultStr);
    if (data.results && Array.isArray(data.results)) {
      for (const r of data.results) {
        if (r.link || r.url) {
          const url = r.link || r.url;
          let domain = '';
          try { domain = new URL(url).hostname.replace('www.', ''); } catch(e) {}
          refs.push({
            title: r.title || domain || url,
            link: url,
            snippet: (r.snippet || '').substring(0, 200),
            domain: domain,
            favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32`,
          });
        }
      }
      return refs;
    } else if (data.url && (data.content || data.status)) {
      let domain = '';
      try { domain = new URL(data.url).hostname.replace('www.', ''); } catch(e) {}
      let title = domain;
      const titleMatch = (data.content || '').match(/<title[^>]*>([^<]+)<\/title>/i);
      if (titleMatch) title = titleMatch[1].trim();
      refs.push({ title, link: data.url, snippet: '', domain, favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32` });
      return refs;
    }
  } catch(e) {
    // JSON truncated — fall back to regex extraction
  }

  // Regex fallback for truncated JSON (tool results capped at 500 chars)
  const decodeJsonStr = (s) => { try { return JSON.parse('"' + s + '"'); } catch(e) { return s; } };
  if (msg.name === 'exa_search') {
    const titleLinkPairs = [...resultStr.matchAll(/"title"\s*:\s*"([^"]*)"[^}]*?"link"\s*:\s*"([^"]*)"/g)];
    for (const match of titleLinkPairs) {
      const [, rawTitle, link] = match;
      const title = decodeJsonStr(rawTitle);
      let domain = '';
      try { domain = new URL(link).hostname.replace('www.', ''); } catch(e) {}
      refs.push({ title, link, snippet: '', domain, favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32` });
    }
  } else if (msg.name === 'web_fetch') {
    const urlMatch = resultStr.match(/"url"\s*:\s*"([^"]*)"/);
    if (urlMatch) {
      const url = urlMatch[1];
      let domain = '';
      try { domain = new URL(url).hostname.replace('www.', ''); } catch(e) {}
      let title = domain;
      const titleMatch = resultStr.match(/<title[^>]*>([^<]+)<\/title>/i);
      if (titleMatch) title = titleMatch[1].trim();
      refs.push({ title, link: url, snippet: '', domain, favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32` });
    }
  }
  return refs;
}

// Pull `[Quelle: <basename> — "..."]` and `[source: <basename>]` markers
// out of an assistant message's rendered content. Returns a Set of
// normalised basenames (lowercased, .md companion suffix stripped) so
// `policy.pdf` and `policy.pdf.md` and `Policy.PDF` all collapse to the
// same key. Used to split refs into cited-vs-searched sections.
function extractCitedBasenamesFromText(text) {
  const set = new Set();
  if (!text || typeof text !== 'string') return set;
  // Match both `[Quelle: foo.pdf — "..."]` and `[source: foo.pdf]` and
  // `[Quelle: foo.pdf §3.2]`. Capture the basename only (everything up to
  // the first em-dash, en-dash, hyphen-with-spaces, §, or closing bracket).
  // Em-dash variants: U+2014 (—), U+2013 (–), ASCII " - " with spaces.
  const re = /\[(?:Quelle|source|Source|QUELLE):\s*([^\]—–§]+?)(?:\s*[—–]|\s+-\s+|\s+§|\])/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    set.add(normaliseCitationBasename(raw));
  }
  return set;
}

function normaliseCitationBasename(s) {
  let n = String(s || '').trim().toLowerCase();
  // Strip path prefix if present
  const slash = n.lastIndexOf('/');
  if (slash >= 0) n = n.substring(slash + 1);
  // Strip trailing .md companion suffix when on a known binary
  const m = n.match(/^(.+\.(pdf|docx|pptx|xlsx|xlsm|eml|msg))\.md$/);
  if (m) n = m[1];
  return n;
}

function refBasenameKey(ref) {
  return normaliseCitationBasename(ref.title || ref.link || '');
}

function collectChatReferences() {
  const chat = state.activeChat;
  if (!chat) return { cited: [], searched: [] };
  const sessionId = chat.sessionId;
  // Use cached if available
  if (state.chatReferences[sessionId]) return state.chatReferences[sessionId];

  // Build the union of cited basenames across every assistant turn in the
  // chat. A ref is "cited" if any assistant message text contains a
  // `[Quelle: <basename>...]` marker matching its basename.
  const citedBasenames = new Set();
  for (const msg of chat.messages) {
    // Include assistant_segment rows: with chronological interleave the early
    // answer text (and its citations) lives in segment rows, not the final
    // assistant message, so scanning only 'assistant' would miss those cites.
    if ((msg.role === 'assistant' || msg.role === 'assistant_segment') && msg.content) {
      for (const k of extractCitedBasenamesFromText(msg.content)) citedBasenames.add(k);
    }
  }

  const cited = [];
  const searched = [];
  const seen = new Set();
  // Walk all messages: pick up live tool_result rows (during streaming) AND
  // assistant.metadata.tools[] (after reload from DB). Both feed the same
  // extractor — a synthetic tool_result-shaped object lets us reuse the
  // parser.
  for (const msg of chat.messages) {
    const candidates = [];
    if (msg.role === 'tool_result') candidates.push(msg);
    if (msg.role === 'assistant' && msg.metadata && Array.isArray(msg.metadata.tools)) {
      for (const t of msg.metadata.tools) {
        if (!t || !t.name) continue;
        candidates.push({ role: 'tool_result', name: t.name, result: t.result });
      }
    }
    for (const c of candidates) {
      const extracted = extractReferencesFromToolResult(c);
      for (const ref of extracted) {
        if (!seen.has(ref.link)) {
          seen.add(ref.link);
          if (citedBasenames.has(refBasenameKey(ref))) cited.push(ref);
          else searched.push(ref);
        }
      }
    }
  }
  const out = { cited, searched };
  state.chatReferences[sessionId] = out;
  return out;
}

function addChatReference(ref) {
  const chat = state.activeChat;
  if (!chat) return;
  const sessionId = chat.sessionId;
  // Live refs always go to "searched" — the cited/searched split is
  // determined by the assistant message text, which doesn't yet exist
  // when streaming starts. After stream end, the cache is invalidated
  // (see invalidateChatReferences) and re-split lazily on the next read.
  if (!state.chatReferences[sessionId]) state.chatReferences[sessionId] = { cited: [], searched: [] };
  const cache = state.chatReferences[sessionId];
  const allRefs = cache.cited.concat(cache.searched);
  if (!allRefs.some(r => r.link === ref.link)) {
    cache.searched.push(ref);
  }
}

function invalidateChatReferences(sessionId) {
  if (sessionId && state.chatReferences[sessionId]) {
    delete state.chatReferences[sessionId];
  }
}

function getReferencesForMessage(idx) {
  // Look backward from this assistant message for tool data — either live
  // tool_result rows (during streaming) or this very assistant message's
  // own metadata.tools[] array (after reload from DB). Stops at the
  // previous user/human message.
  // Returns {cited, searched}: refs whose basename matches a `[Quelle:...]`
  // marker in this assistant message's text go into cited; the rest go
  // into searched (still loaded into MemPalace, just not actually used in
  // the answer).
  const chat = state.activeChat;
  if (!chat) return { cited: [], searched: [] };
  const self = chat.messages[idx];
  const citedBasenames = (self && self.role === 'assistant')
    ? extractCitedBasenamesFromText(self.content || '')
    : new Set();
  const cited = [];
  const searched = [];
  const seen = new Set();
  const ingest = (synth) => {
    for (const ref of extractReferencesFromToolResult(synth)) {
      if (!seen.has(ref.link)) {
        seen.add(ref.link);
        if (citedBasenames.has(refBasenameKey(ref))) cited.push(ref);
        else searched.push(ref);
      }
    }
  };
  // First: this assistant message's own metadata.tools[] (the post-reload path)
  if (self && self.role === 'assistant' && self.metadata && Array.isArray(self.metadata.tools)) {
    for (const t of self.metadata.tools) {
      if (!t || !t.name) continue;
      ingest({ role: 'tool_result', name: t.name, result: t.result });
    }
  }
  // Then walk back for any live tool_result rows (the streaming path)
  for (let j = idx - 1; j >= 0; j--) {
    const m = chat.messages[j];
    if (m.role === 'user' || m.role === 'human') break;
    if (m.role === 'tool_result') ingest(m);
  }
  return { cited, searched };
}

function openReferencesPanel(highlightLink) {
  const { cited, searched } = collectChatReferences();
  if (!cited.length && !searched.length) { showToast('Keine Quellen in diesem Chat'); return; }
  openRightPanel('references');
  if (highlightLink) {
    setTimeout(() => {
      const cards = document.querySelectorAll('#refs-content .ref-card');
      for (const card of cards) {
        if (card.dataset.link === highlightLink) {
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.style.outline = '2px solid var(--accent-brand)';
          card.style.outlineOffset = '2px';
          setTimeout(() => { card.style.outline = ''; card.style.outlineOffset = ''; }, 2000);
          break;
        }
      }
    }, 50);
  }
}

function closeReferencesPanel() {
  closeRightPanel();
}

function initRefsResizeHandle() { /* no-op — merged into initRightPanelResize */ }

/* ═══════════════════════════════════════════════════════════
   USED-MEMORY GRAPH MODAL
   For an assistant message that called any project-knowledge tool
   (mempalace_query / mempalace_kg_*), this surfaces what the model
   actually retrieved: drawers (text snippets) and triples
   (subject — predicate → object), in a graph view that lets the
   user audit the answer.
   ═══════════════════════════════════════════════════════════ */

// Cheap check: did this assistant message use any of the project-knowledge
// tools? Used to gate the inline action button so it only appears when
// there's something meaningful to show.
function messageUsedKnowledge(idx) {
  const chat = state.activeChat;
  if (!chat) return false;
  const m = chat.messages[idx];
  if (!m || m.role !== 'assistant') return false;
  const KG_TOOLS = new Set(['mempalace_query', 'mempalace_kg_query',
                            'mempalace_kg_search', 'mempalace_kg_neighbors']);
  // Post-reload path: tools live in metadata.tools[]
  if (m.metadata && Array.isArray(m.metadata.tools)) {
    if (m.metadata.tools.some(t => t && KG_TOOLS.has(t.name))) return true;
  }
  // Live path: tool_result rows between this message and the previous user msg
  for (let j = idx - 1; j >= 0; j--) {
    const prev = chat.messages[j];
    if (prev.role === 'user' || prev.role === 'human') break;
    if (prev.role === 'tool_result' && KG_TOOLS.has(prev.name)) return true;
  }
  return false;
}

// Pull every drawer + triple the message saw across all four tools.
// Returns {drawers: [...], triples: [...]} deduped by content.
function _collectKnowledgeForMessage(idx) {
  const chat = state.activeChat;
  if (!chat) return { drawers: [], triples: [] };
  const KG_TOOLS = new Set(['mempalace_query', 'mempalace_kg_query',
                            'mempalace_kg_search', 'mempalace_kg_neighbors']);
  const tools = [];
  const m = chat.messages[idx];
  if (m && m.metadata && Array.isArray(m.metadata.tools)) {
    for (const t of m.metadata.tools) {
      if (t && KG_TOOLS.has(t.name)) tools.push(t);
    }
  }
  // Live tool_result rows fall back when metadata.tools[] is empty (mid-stream).
  if (!tools.length) {
    for (let j = idx - 1; j >= 0; j--) {
      const prev = chat.messages[j];
      if (prev.role === 'user' || prev.role === 'human') break;
      if (prev.role === 'tool_result' && KG_TOOLS.has(prev.name)) {
        tools.push({ name: prev.name, result: prev.result });
      }
    }
  }
  const drawers = [];
  const drawerSeen = new Set();
  const triples = [];
  const tripleSeen = new Set();
  for (const t of tools) {
    let parsed = null;
    const raw = typeof t.result === 'string' ? t.result : JSON.stringify(t.result || '');
    try { parsed = JSON.parse(raw); } catch(_) { continue; }
    if (!parsed) continue;
    for (const d of (parsed.drawers || [])) {
      const key = d.id || `${d.source_file}#${(d.text||d.snippet||'').slice(0,40)}`;
      if (drawerSeen.has(key)) continue;
      drawerSeen.add(key);
      drawers.push(d);
    }
    for (const tr of (parsed.triples || parsed.edges || [])) {
      if (!tr || !tr.subject || !tr.predicate || !tr.object) continue;
      const key = `${tr.subject}|${tr.predicate}|${tr.object}`;
      if (tripleSeen.has(key)) continue;
      tripleSeen.add(key);
      triples.push(tr);
    }
  }
  return { drawers, triples };
}
