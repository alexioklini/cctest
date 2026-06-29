// Code-mode interactive terminal — xterm.js front-end over the SSE+POST PTY
// backend (server_lib/terminal.py). Bottom-docked panel, multiple tabs (= PTY
// sessions), shown in BOTH the code-mode project-detail view and the code-mode
// chat. Sessions are keyed per (agent,project) server-side and REUSED across
// both views: opening the panel lists existing sessions and re-attaches.
//
// Each tab holds: { id, term (xterm), fit (FitAddon), offset (abs byte pos),
// es (EventSource), el (xterm host div) }.

const _term = {
  agent: '', project: '', wd: '',
  tabs: [],          // [{id, kind, el, pane, …}] — each tab is assigned to a pane
  open: false,
  // Dynamic split-pane workspace. A 2×2 grid of slots — a=top-left, b=top-right,
  // c=bottom-left, d=bottom-right — but the VISIBLE layout is DERIVED from which
  // slots actually hold tabs (empty cells collapse, neighbours reclaim the space).
  // There is no preset layout to pick: the user drags a tab onto a pane's edge to
  // split toward that direction. Slot 'a' is the always-present home cell.
  panes: [],         // [{id, slot, barEl, bodyEl, active}]  slot ∈ a|b|c|d
  activePane: null,  // pane id that owns keyboard focus / new tabs
  sizes: {},         // {col, row}: left-column + top-row fraction (CSS fr/px), 0..1
  // Layout state (persisted PER-PROJECT in bottom_workspace, not per-chat):
  treeVisible: true, // file-tree column shown?
  treeWidth: 240,    // file-tree column width (px)
  treeSplit: 0.5,    // file-tree vs Terminal-Chats height split (tree's fraction)
  singleEditor: false, // single-editor mode: tree click replaces the editor tab
};

// Slot order (DOM/visual): top-left, top-right, bottom-left, bottom-right.
const _TERM_SLOTS = ['a', 'b', 'c', 'd'];
// Per-slot grid position: which row/column each slot occupies in the 2×2.
const _TERM_SLOT_POS = {
  a: { row: 'top', col: 'left' },  b: { row: 'top', col: 'right' },
  c: { row: 'bot', col: 'left' },  d: { row: 'bot', col: 'right' },
};

// One-shot explicit-pane override: a pane-scoped action (the per-pane +/◈/
// new-file button) sets this to its pane id so the next open lands there instead
// of the content-type default. Consumed (cleared) by the open it triggers.
let _termOpenInPane = null;

// ── Split-pane infrastructure ────────────────────────────────────────────────
function _terminalGetPane(id) { return _term.panes.find(p => p.id === id); }
function _terminalPaneTabs(paneId) { return _term.tabs.filter(t => t.pane === paneId); }
function _terminalActivePane() {
  return _terminalGetPane(_term.activePane) || _term.panes[0] || null;
}

// Which slots currently hold ≥1 tab (real occupancy, before any normalization).
function _terminalRawOccupied() {
  const occ = new Set();
  for (const t of _term.tabs) { const s = (t.pane || '').replace('pane-', ''); if (_TERM_SLOT_POS[s]) occ.add(s); }
  return _TERM_SLOTS.filter(s => occ.has(s));
}

// Compact the workspace so there is never an EMPTY cell sitting between/around
// occupied ones (which would render as a big blank band — the drag bug). We
// relabel the real occupied slots into a canonical minimal arrangement and RETAG
// every tab's `.pane` to match, in place. Rules, by occupied-cell count:
//   1 cell  → always 'a' (single)
//   2 cells → if they share a row → a|b (cols); if a column → a/c (rows);
//             diagonal → keep as a 2-of-4 (quad with the two real cells)
//   3/4     → keep positions (already a real 2×2 sub-grid)
// Returns the post-compaction occupied-slot list. Mutates tab.pane.
function _terminalNormalizeSlots() {
  const occ = _terminalRawOccupied();
  if (occ.length === 0) return ['a'];           // empty → home cell a
  let remap = null;
  if (occ.length === 1) {
    if (occ[0] !== 'a') remap = { [occ[0]]: 'a' };   // lone tab → top-left
  } else if (occ.length === 2) {
    const [p, q] = occ.map(s => _TERM_SLOT_POS[s]);
    if (p.row === q.row) {                       // same row → collapse to top a|b
      remap = {}; occ.forEach((s, i) => { remap[s] = i === 0 ? 'a' : 'b'; });
    } else {
      // same column OR diagonal → collapse to a stacked a/c so there is never a
      // half-empty row (a diagonal b+c would otherwise leave top-left + bottom-
      // right blank). First (visually higher/left) slot → a, the other → c.
      remap = {}; occ.forEach((s, i) => { remap[s] = i === 0 ? 'a' : 'c'; });
    }
  }
  if (remap) {
    for (const t of _term.tabs) {
      const s = (t.pane || '').replace('pane-', '');
      if (remap[s]) t.pane = 'pane-' + remap[s];
    }
    return _terminalRawOccupied();
  }
  return occ;
}

// Derive the visible grid from (normalized) occupancy: which slots render, which
// dividers exist, and the data-grid key the CSS uses. Empty cells are dropped
// entirely (space reclaimed). Always run AFTER _terminalNormalizeSlots.
function _terminalComputeGrid() {
  const occ = _terminalNormalizeSlots();
  const has = (s) => occ.includes(s);
  const topR = has('b');                 // right column present in the top row?
  const bot = has('c') || has('d');      // any bottom row at all?
  const botR = has('d');                 // right column present in the bottom row?
  const cols = (topR || botR);           // grid has two columns?
  // Pick the most specific data-grid key so empty cells get absorbed (e.g. a top
  // row with no right cell spans full width). Dividers are emitted to match.
  let key = 'single';
  if (!bot && cols) key = 'cols';                  // a | b
  else if (bot && !cols) key = 'rows';             // a / c
  else if (bot && cols) {
    // 2 rows, 2 columns, but a row may be missing its right cell → span that row.
    if (topR && botR) key = 'quad';                // all four columns present
    else if (topR && !botR) key = 'quad-tr';       // a|b over c(full)
    else if (!topR && botR) key = 'quad-br';       // a(full) over c|d
    else key = 'rows';                             // neither right cell (shouldn't reach)
  }
  return { occ, topR, bot, botR, cols, key };
}

// Pick the DEFAULT pane a freshly-opened tab of `kind` should land in. Prefers an
// already-occupied slot suited to the content (source → top-left; other files →
// top-right then top-left; terminals/chats → bottom then top-right then top-left),
// but never CREATES a new cell — splitting is a drag gesture, not an auto-open. So
// the chain is filtered to slots that already exist; falls back to the active pane
// then 'pane-a'.
function _terminalDefaultPane(kind, ext) {
  // Use the panes that actually exist right now (already compacted at build time).
  const live = new Set(_term.panes.map(p => p.slot));
  let chain;
  if (kind === 'terminal' || kind === 'chat') chain = ['c', 'd', 'b', 'a'];
  else if (kind === 'editor' && _terminalIsSourceExt(ext)) chain = ['a'];
  else chain = ['b', 'a'];   // other files
  for (const s of chain) {
    if (live.has(s)) { const p = _terminalGetPane('pane-' + s); if (p) return p.id; }
  }
  return (_terminalActivePane() && _terminalActivePane().id) || 'pane-a';
}

// A "source file" = a code file CodeMirror has a real language mode for, that
// is NOT a renderable doc type (html/htm/md/markdown/svg open as views, so they
// count as "other files" for placement). Everything without a code mode
// (txt/csv/log/images/pdf/…) is also "other".
function _terminalIsSourceExt(ext) {
  ext = (ext || '').toLowerCase();
  if (_terminalIsRenderable(ext)) return false;
  return !!_cmModeFor(ext);
}

// (Re)build the pane DOM from the CURRENT occupancy (the dynamic grid), preserving
// existing tab elements (re-parented into their pane bodies). Empty cells are not
// rendered — their space is reclaimed by the remaining panes. Dividers appear only
// between cells that actually share an edge. Idempotent: safe to call after every
// tab move / close / split.
function _terminalBuildPanes() {
  const host = document.getElementById('terminal-panes');
  if (!host) return;
  const g = _terminalComputeGrid();
  const slots = g.occ;
  host.dataset.grid = g.key;
  // detach existing tab els so re-parenting doesn't drop them
  for (const t of _term.tabs) { if (t.el && t.el.parentNode) t.el.parentNode.removeChild(t.el); }
  host.innerHTML = '';
  // Panes in slot order; dividers between adjacent cells. The CSS grid (keyed by
  // data-grid) maps each slot's grid-area, so DOM order only needs to include the
  // right elements — positions come from CSS.
  for (const s of slots) host.appendChild(_terminalMakePane(s));
  // vertical divider(s): between left & right columns (top row and/or bottom row)
  if (g.topR) host.appendChild(_terminalMakeDivider('v', 'vt'));
  if (g.botR) host.appendChild(_terminalMakeDivider('v', 'vb'));
  // horizontal divider: between top & bottom rows
  if (g.bot) host.appendChild(_terminalMakeDivider('h', 'h'));
  // rebuild _term.panes from the DOM, keeping prior active where possible
  const prevActive = {};
  _term.panes.forEach(p => { prevActive[p.slot] = p.active; });
  _term.panes = slots.map(slot => {
    const paneEl = host.querySelector(`.tpane[data-slot="${slot}"]`);
    return { id: 'pane-' + slot, slot,
             barEl: paneEl.querySelector('.tpane-tabs'),
             bodyEl: paneEl.querySelector('.tpane-body'),
             dropEl: paneEl.querySelector('.tpane-drop'),
             paneEl, active: prevActive[slot] || null };
  });
  // reassign orphaned tabs (pane gone) to the first pane
  const validPanes = new Set(_term.panes.map(p => p.id));
  const firstId = _term.panes[0] && _term.panes[0].id;
  for (const t of _term.tabs) {
    if (!validPanes.has(t.pane)) t.pane = firstId;
  }
  // re-parent each tab el into its pane body
  for (const t of _term.tabs) {
    const pane = _terminalGetPane(t.pane);
    if (pane && t.el) pane.bodyEl.appendChild(t.el);
  }
  // ensure each pane has a valid active tab
  for (const p of _term.panes) {
    const tabs = _terminalPaneTabs(p.id);
    if (!tabs.find(t => t.id === p.active)) p.active = tabs.length ? tabs[0].id : null;
  }
  if (!_terminalGetPane(_term.activePane)) _term.activePane = firstId;
  _terminalApplySizes();
  _terminalRenderTabs();
  _terminalShowActiveTabs();
  _terminalInitPaneDividers();
}

function _terminalMakePane(slot) {
  const pane = document.createElement('div');
  pane.className = 'tpane';
  pane.dataset.slot = slot;
  pane.innerHTML = `
    <div class="tpane-bar" data-pane="pane-${slot}">
      <div class="tpane-tabs" data-pane="pane-${slot}"></div>
      <button class="pt-act" title="Neues Terminal" data-act="newterm">+</button>
      <button class="pt-act" title="Neuer Terminal-Chat" data-act="newchat">◈</button>
      <button class="pt-act" title="Neue Datei" data-act="newfile">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
      </button>
    </div>
    <div class="tpane-body">
      <div class="tpane-drop"></div>
    </div>`;
  // pane-scoped + / new-chat / new-file: a per-pane button means "open HERE" —
  // set _termOpenInPane so the content-type default is overridden for this open.
  pane.querySelector('[data-act="newterm"]').onclick = () => { _term.activePane = 'pane-' + slot; terminalNewTab('pane-' + slot); };
  pane.querySelector('[data-act="newchat"]').onclick = () => {
    _term.activePane = 'pane-' + slot;
    if (typeof _terminalAddChatTab === 'function') {
      const t = _terminalAddChatTab('', 'pane-' + slot, 'Neuer Chat');
      if (t) { const ta = t.el.querySelector('.tc-ta'); if (ta) setTimeout(() => ta.focus(), 40); }
    }
  };
  pane.querySelector('[data-act="newfile"]').onclick = () => { _term.activePane = 'pane-' + slot; _termOpenInPane = 'pane-' + slot; terminalNewFile(); };
  const bar = pane.querySelector('.tpane-bar');
  bar.addEventListener('mousedown', () => { _term.activePane = 'pane-' + slot; _terminalPaintActivePane(); });
  // drag-drop onto the BAR: plain move into this existing cell (no split).
  bar.addEventListener('dragover', (e) => { e.preventDefault(); bar.classList.add('tp-drop'); });
  bar.addEventListener('dragleave', () => bar.classList.remove('tp-drop'));
  bar.addEventListener('drop', (e) => {
    e.preventDefault(); bar.classList.remove('tp-drop');
    const tabId = e.dataTransfer.getData('text/tab-id');
    if (tabId) _terminalMoveTabToPane(tabId, 'pane-' + slot);
  });
  // drag-drop onto the BODY: edge zones split the grid toward that direction; the
  // centre is a plain move into this cell. The overlay highlights the target zone.
  _terminalWirePaneDrop(pane, slot);
  return pane;
}

// Wire the drop overlay for a pane. The overlay (.tpane-drop) is the DROP TARGET,
// not the body: pane content (xterm / CodeMirror / chat DOM) would otherwise eat
// the dragover/drop (HTML5 DnD requires preventDefault on the element UNDER the
// cursor, which inner content doesn't do → drop never fires + the cursor shows
// "no-drop"). While a tab drag is active, #terminal-panes gets a `tp-dnd` class
// that raises every overlay above the content (pointer-events:auto, full inset),
// so dragover/drop always land here. Outside a drag the overlay is inert.
function _terminalWirePaneDrop(pane, slot) {
  const drop = pane.querySelector('.tpane-drop');
  if (!drop) return;
  // Edge zone under the cursor, measured against the overlay (== body) rect.
  const zoneFor = (e) => {
    const r = drop.getBoundingClientRect();
    const fx = (e.clientX - r.left) / Math.max(1, r.width);
    const fy = (e.clientY - r.top) / Math.max(1, r.height);
    const edge = 0.28;
    const dl = fx, dr = 1 - fx, dt = fy, db = 1 - fy;
    const m = Math.min(dl, dr, dt, db);
    if (m > edge) return 'center';
    if (m === dl) return 'left';
    if (m === dr) return 'right';
    if (m === dt) return 'top';
    return 'bottom';
  };
  drop.addEventListener('dragenter', (e) => { e.preventDefault(); });
  drop.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    drop.dataset.zone = zoneFor(e);   // CSS highlights only the active quarter
  });
  drop.addEventListener('dragleave', (e) => {
    // clear only when the cursor actually leaves this overlay
    if (!drop.contains(e.relatedTarget)) drop.removeAttribute('data-zone');
  });
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    const z = zoneFor(e);
    drop.removeAttribute('data-zone');
    const tabId = e.dataTransfer.getData('text/tab-id');
    if (!tabId) return;
    if (z === 'center') { _terminalMoveTabToPane(tabId, 'pane-' + slot); return; }
    _terminalSplitToSlot(tabId, slot, z);
  });
}

function _terminalMakeDivider(dir, which) {
  const d = document.createElement('div');
  d.className = 'tpane-divider';
  d.dataset.dir = dir; d.dataset.which = which;
  return d;
}

// Show only each pane's active tab element; hide the rest. Chat panes lay out
// as a column flex (scrollback grows, input pinned at the bottom), so they show
// as 'flex' rather than 'block'.
function _terminalShowActiveTabs() {
  for (const p of _term.panes) {
    for (const t of _terminalPaneTabs(p.id)) {
      const shown = t.kind === 'chat' ? 'flex' : 'block';
      t.el.style.display = (t.id === p.active) ? shown : 'none';
    }
  }
  _terminalPaintActivePane();
}

function _terminalPaintActivePane() {
  for (const p of _term.panes) {
    p.paneEl.classList.toggle('tp-active', p.id === _term.activePane);
  }
}

// Move a tab into an EXISTING pane (drag onto its bar / centre). Re-parents the el,
// makes it active there. If the source cell empties, the next _terminalBuildPanes
// collapses it and reclaims the space.
function _terminalMoveTabToPane(tabId, paneId) {
  const tab = _term.tabs.find(t => t.id === tabId);
  const pane = _terminalGetPane(paneId);
  if (!tab || !pane || tab.pane === paneId) return;
  const srcId = tab.pane;
  tab.pane = paneId;
  _term.activePane = paneId;
  pane.active = tab.id;
  // fix the source pane's active tab
  const src = _terminalGetPane(srcId);
  if (src) {
    const left = _terminalPaneTabs(srcId);
    if (!left.find(t => t.id === src.active)) src.active = left.length ? left[0].id : null;
  }
  _terminalReflow(tab.id);
}

// Resolve a (sourceSlot, direction) split into the concrete 2×2 slot the tab should
// land in, then move it there. Directions: left/right (same row, other column),
// top/bottom (same column, other row). If the resolved slot is the source itself or
// can't be expressed in the 2×2, fall back to a plain move into the source.
function _terminalSplitToSlot(tabId, srcSlot, dir) {
  const tab = _term.tabs.find(t => t.id === tabId);
  if (!tab) return;
  const pos = _TERM_SLOT_POS[srcSlot]; if (!pos) return;
  let row = pos.row, col = pos.col;
  if (dir === 'left') col = 'left';
  else if (dir === 'right') col = 'right';
  else if (dir === 'top') row = 'top';
  else if (dir === 'bottom') row = 'bot';
  // map (row,col) → slot
  const target = _TERM_SLOTS.find(s => _TERM_SLOT_POS[s].row === row && _TERM_SLOT_POS[s].col === col);
  if (!target || ('pane-' + target) === tab.pane) {
    // dropped on an edge that maps back to the same cell — keep it here
    if ('pane-' + srcSlot !== tab.pane) _terminalMoveTabToPane(tabId, 'pane-' + srcSlot);
    return;
  }
  const srcId = tab.pane;
  // creating a new cell: ensure a pane object exists after rebuild; just retag.
  tab.pane = 'pane-' + target;
  _term.activePane = 'pane-' + target;
  const src = _terminalGetPane(srcId);
  if (src) {
    const left = _terminalPaneTabs(srcId);
    if (!left.find(t => t.id === src.active)) src.active = left.length ? left[0].id : null;
  }
  // The new pane DOM doesn't exist yet — rebuild from occupancy, then activate.
  _terminalBuildPanes();
  const np = _terminalGetPane('pane-' + target);
  if (np) np.active = tab.id;
  _terminalReflow(tab.id, true);
}

// Common tail after a move/split: rebuild panes (collapses emptied cells), re-render
// tabs, activate the moved tab, persist. `built` skips the rebuild when the caller
// already did it (split path).
function _terminalReflow(activateId, built) {
  if (!built) _terminalBuildPanes();
  _terminalRenderTabs();
  _terminalShowActiveTabs();
  if (activateId) _terminalActivate(activateId);
  _terminalPersist();
  setTimeout(_terminalOnResize, 40);
}

// Apply persisted pane sizings to the grid CSS custom props. The grid uses ONE
// column split (--tp-col = left fraction) and ONE row split (--tp-row = top
// fraction), shared by both rows/columns so the 2×2 stays rectangular.
function _terminalApplySizes() {
  const host = document.getElementById('terminal-panes');
  if (!host) return;
  const s = _term.sizes || {};
  const col = (typeof s.col === 'number' && s.col > 0.08 && s.col < 0.92) ? s.col : 0.5;
  const row = (typeof s.row === 'number' && s.row > 0.08 && s.row < 0.92) ? s.row : 0.5;
  host.style.setProperty('--tp-col', col);
  host.style.setProperty('--tp-row', row);
}

// Wire drag-resize on the current grid's dividers (rebuilt on every reflow). A
// vertical divider sets the column split (left fraction); a horizontal divider sets
// the row split (top fraction). Both are stored as 0..1 fractions so they stay sane
// across panel-size changes.
function _terminalInitPaneDividers() {
  const host = document.getElementById('terminal-panes');
  if (!host) return;
  if (!_term.sizes || typeof _term.sizes !== 'object') _term.sizes = {};
  host.querySelectorAll('.tpane-divider').forEach(div => {
    const dir = div.dataset.dir;  // 'v' | 'h'
    div.onmousedown = (e) => {
      e.preventDefault();
      const rect = host.getBoundingClientRect();
      document.body.style.userSelect = 'none';
      const move = (ev) => {
        if (dir === 'v') {
          const f = Math.max(0.1, Math.min(0.9, (ev.clientX - rect.left) / Math.max(1, rect.width)));
          host.style.setProperty('--tp-col', f);
          _term.sizes.col = f;
        } else {
          const f = Math.max(0.1, Math.min(0.9, (ev.clientY - rect.top) / Math.max(1, rect.height)));
          host.style.setProperty('--tp-row', f);
          _term.sizes.row = f;
        }
        _terminalOnResize();
      };
      const up = () => {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        document.body.style.userSelect = '';
        _terminalPersist();
        _terminalOnResize();
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    };
  });
}

// Resolve the current code-mode project context from whichever view we're in.
// Returns {agent, project, wd} or null if not a code-mode project.
async function _terminalCtx() {
  // 1) project-detail view
  if (state._projectDetail && state._projectDetail.code_mode) {
    return {
      agent: state._projectDetailAgent || 'main',
      project: state._projectDetailName || '',
      wd: state._projectDetail.working_dir || '',
    };
  }
  // 2) code-mode chat — load the active chat's project metadata
  const projName = state.currentProject || (state.activeChat && state.activeChat.project);
  if (!projName) return null;
  const agent = state.activeAgentId || 'main';
  try {
    const p = await API.get(`/v1/agents/${agent}/projects/${encodeURIComponent(projName)}`);
    if (p && p.code_mode) return { agent, project: projName, wd: p.working_dir || '' };
  } catch (e) { /* not a project chat */ }
  return null;
}

// Is the terminal available right now? (used to show/hide the toggle button)
// In the project-detail view: code_mode flag on the loaded detail. In a chat:
// reuse the existing _workdirIsCodeChat() sync check (cache-backed).
function terminalAvailable() {
  if (state._projectDetail && state._projectDetailName === state.currentProject
      && state._projectDetail.code_mode) return true;
  if (typeof _workdirIsCodeChat === 'function') return _workdirIsCodeChat();
  return false;
}

// Open the terminal/editor workspace DIRECTLY from the project-detail view
// (the header "Terminal" button) — no chat needed. Opens MAXIMIZED (full-area):
// a tiny bottom-docked strip makes no sense from the project view. We RESTORE the
// saved workspace (editors/chats/terminals) and only spawn a fresh terminal when
// it comes up COMPLETELY EMPTY — opening must not keep adding terminal windows on
// top of a restored workspace. _terminalLoadSessions (run by terminalTogglePanel)
// already spawns one when there are zero tabs, so here we just focus a sensible
// existing tab: a terminal if there is one, else the active pane's tab.
async function projectOpenTerminal() {
  await terminalTogglePanel(true);
  if (!_term.open) return;   // not a code-mode project / ctx failed
  // Force MAXIMIZED (full screen) when opened from the project view.
  if (!_term.maximized) terminalToggleMaximize();
  if (!(_term.tabs || []).length) return;   // load spawned a fresh terminal already
  const term = (_term.tabs || []).find(t => t.kind === 'terminal');
  if (term) { _terminalActivate(term.id); return; }
  // No terminal among the restored tabs — leave the restored workspace as-is and
  // focus the active pane's current tab (don't force a new terminal window).
  const ap = _terminalActivePane();
  if (ap && ap.active) _terminalActivate(ap.active);
}

// The project the CURRENT view belongs to (project-detail view or the active
// chat's project), or '' when none. Sync — mirrors _terminalCtx's project pick.
function _terminalCurrentViewProject() {
  if (state._projectDetail && state._projectDetailName === state.currentProject
      && state._projectDetail.code_mode) return state._projectDetailName || '';
  return state.currentProject || (state.activeChat && state.activeChat.project) || '';
}

// Show/hide the status-bar terminal toggle based on code-mode context. Also
// auto-closes the panel when the view it was launched from is no longer shown:
//   • navigated away from any code-mode context (terminalAvailable false), OR
//   • switched to a DIFFERENT project than the one the terminal is bound to
//     (the open workspace belongs to _term.project; showing it over another
//     project's view would be stale/wrong).
function terminalRefreshToggle() {
  const btn = document.getElementById('terminal-toggle-btn');
  const avail = terminalAvailable();
  if (btn) btn.classList.toggle('code-mode-available', !!avail);
  if (!_term.open) return;
  if (!avail) { terminalTogglePanel(false); return; }
  const viewProject = _terminalCurrentViewProject();
  // If the current view resolves to a project and it differs from the terminal's
  // bound project, the launching view is no longer shown → close the panel.
  if (_term.project && viewProject && viewProject !== _term.project) {
    terminalTogglePanel(false);
  }
}

async function terminalTogglePanel(force) {
  const panel = document.getElementById('terminal-panel');
  if (!panel) return;
  const want = (typeof force === 'boolean') ? force : !_term.open;
  if (want) {
    const ctx = await _terminalCtx();
    if (!ctx) { if (typeof showToast === 'function') showToast('Terminal nur in Code-Mode-Projekten'); return; }
    // Already open for this same project? Just (re)show it — do NOT re-run
    // _terminalLoadSessions, which would re-add tabs / re-trigger the empty→spawn
    // path and pile a new terminal on top of the restored workspace.
    if (_term.open && _term.project === ctx.project && _term.agent === ctx.agent) {
      panel.style.display = 'flex';
      document.getElementById('main-content').classList.add('terminal-open');
      return;
    }
    _term.agent = ctx.agent; _term.project = ctx.project; _term.wd = ctx.wd;
    panel.style.display = 'flex';
    document.getElementById('main-content').classList.add('terminal-open');
    _term.open = true;
    _terminalRestoreHeight();
    await _terminalLoadSessions();
    refreshTerminalTree();
    startEditorFreshPoll();   // auto-reload editors when files change on disk
  } else {
    panel.style.display = 'none';
    document.getElementById('main-content').classList.remove('terminal-open');
    _term.open = false;
    stopEditorFreshPoll();
  }
}

// Map a legacy bottom_workspace (old fixed-layout slots) onto the new dynamic
// a/b/c/d 2×2 scheme. Old slots by layout:
//   single → a   ·   lr → a(left) b(right)   ·   tb → a(top) b(bottom)
//   lrb → a(top-left) b(top-right) c(bottom full-width)
// New slots: a=TL b=TR c=BL d=BR. So tb's b(bottom) must become c; lr/lrb already
// align (lrb's c = bottom-left, which the new grid spans as the bottom row when no
// d exists). Idempotent for already-new workspaces (no `layout` field).
function _terminalMigrateWorkspace(ws) {
  if (!ws || typeof ws !== 'object') return ws;
  if (!ws.layout) return ws;            // already new shape (or empty)
  const remap = (ws.layout === 'tb') ? { 'pane-a': 'pane-a', 'pane-b': 'pane-c' } : null;
  if (remap && ws.panes && typeof ws.panes === 'object') {
    const next = {};
    for (const [k, v] of Object.entries(ws.panes)) next[remap[k] || k] = v;
    ws = { ...ws, panes: next };
  }
  delete ws.layout;
  return ws;
}

// Old sizes were {a,b,top,bot} as px/fr strings; the new model is {col,row} as
// 0..1 fractions. We can't recover exact fractions from px, so legacy sizes reset
// to a centred split (0.5/0.5) — a one-time, harmless loss of an old drag position.
function _terminalMigrateSizes(sizes, layout) {
  if (sizes && (typeof sizes.col === 'number' || typeof sizes.row === 'number')) return sizes;
  if (layout) return {};   // legacy px sizes → drop, fall back to centred
  return sizes || {};
}

async function _terminalLoadSessions() {
  // Load persisted workspace (layout + per-pane editor files + sizes) FIRST so
  // panes exist before we add tabs.
  let ws = null;
  try {
    const p = await API.get(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}`);
    ws = p && p.bottom_workspace;
  } catch (e) { /* ignore */ }
  if (ws) {
    if (typeof ws.tree_visible === 'boolean') _term.treeVisible = ws.tree_visible;
    if (ws.tree_width >= 120) _term.treeWidth = Math.min(ws.tree_width, 700);
    if (typeof ws.tree_split === 'number' && ws.tree_split > 0 && ws.tree_split < 1) _term.treeSplit = ws.tree_split;
    if (typeof ws.single_editor === 'boolean') _term.singleEditor = ws.single_editor;
    if (ws.sizes && typeof ws.sizes === 'object') _term.sizes = _terminalMigrateSizes(ws.sizes, ws.layout);
  }
  ws = _terminalMigrateWorkspace(ws);   // map legacy lr/tb/lrb panes → a/b/c/d
  _terminalApplyLayout();
  _terminalBuildPanes();   // creates pane DOM for the current occupancy

  // 1) reattach live terminal sessions (reused server-side per project) → pane-a
  let existing = [];
  try {
    const d = await API.get(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions`);
    existing = (d && d.sessions) || [];
    const liveIds = new Set(existing.map(s => s.id));
    _term.tabs = _term.tabs.filter(t => t.kind !== 'terminal' || liveIds.has(t.id) || t._fresh);
    for (const s of existing) {
      if (!_term.tabs.find(t => t.id === s.id)) _terminalAddTab(s.id, 'pane-a');
    }
  } catch (e) { /* terminal backend may be down — editors still work */ }

  // 2) restore persisted editor tabs into their panes. New shape: ws.panes =
  // {<paneId>: {editor_files:[paths], active_path}}. Legacy shape: ws.editor_files.
  const paneMap = (ws && ws.panes && typeof ws.panes === 'object') ? ws.panes : null;
  if (paneMap) {
    for (const paneId of Object.keys(paneMap)) {
      const slot = paneId.replace('pane-', '');
      if (!_TERM_SLOTS.includes(slot)) continue;  // unknown slot
      // The pane DOM may not exist yet (panes are occupancy-derived); we just tag
      // each tab's `.pane` and rebuild once at the end so b/c/d cells materialise.
      for (const fp of (paneMap[paneId].editor_files || [])) {
        if (_term.tabs.find(t => t.kind === 'editor' && t.path === fp)) continue;
        _term.activePane = paneId;
        await terminalOpenFile(fp);
        const t = _term.tabs.find(x => x.kind === 'editor' && x.path === fp);
        if (t) t.pane = paneId;
      }
      // Restore open terminal-chats into their pane + load each transcript.
      for (const sid of (paneMap[paneId].chat_sessions || [])) {
        if (!sid || _term.tabs.find(t => t.kind === 'chat' && t.sessionId === sid)) continue;
        _term.activePane = paneId;
        if (typeof _terminalAddChatTab === 'function') {
          const ct = _terminalAddChatTab(sid, paneId);
          if (ct) ct.pane = paneId;
          if (ct && typeof tcLoadTranscript === 'function') { try { await tcLoadTranscript(ct); } catch (_) {} }
        }
      }
    }
    _terminalBuildPanes();   // materialise b/c/d cells from the restored occupancy
  } else if (ws && Array.isArray(ws.editor_files)) {
    _term.activePane = 'pane-a';
    for (const fp of ws.editor_files) {
      if (!_term.tabs.find(t => t.kind === 'editor' && t.path === fp)) await terminalOpenFile(fp);
    }
  }

  if (!_term.tabs.length) { _term.activePane = 'pane-a'; await terminalNewTab(); return; }
  // pick an active tab per pane (restore active_path / active_chat where persisted)
  for (const pane of _term.panes) {
    const pm = paneMap && paneMap[pane.id];
    let act = null;
    if (pm && pm.active_path) {
      const t = _terminalPaneTabs(pane.id).find(x => x.kind === 'editor' && x.path === pm.active_path);
      if (t) act = t.id;
    }
    if (!act && pm && pm.active_chat) {
      const t = _terminalPaneTabs(pane.id).find(x => x.kind === 'chat' && x.sessionId === pm.active_chat);
      if (t) act = t.id;
    }
    const tabs = _terminalPaneTabs(pane.id);
    pane.active = act || (tabs.length ? tabs[0].id : null);
  }
  _term.activePane = 'pane-a';
  _terminalShowActiveTabs();
  _terminalRenderTabs();
  // activate pane-a's tab to wire streams/focus
  const aPane = _terminalGetPane('pane-a');
  if (aPane && aPane.active) _terminalActivate(aPane.active);
  if (typeof renderTermchatHistory === 'function') renderTermchatHistory();
}

// Persist the bottom workspace (open editor file paths + active tab) to the
// project (server-side, per-project, so it round-trips across devices). Live
// terminal sessions are reattached from the server list, so we only persist the
// editor file set + which tab was active. Debounced.
let _terminalPersistTimer = null;
function _terminalPersist() {
  if (!_term.open || !_term.project) return;
  clearTimeout(_terminalPersistTimer);
  _terminalPersistTimer = setTimeout(() => {
    // Per-pane editor file assignment + active (terminals reattach from the
    // server list into pane-a, so only editors carry a pane in the persisted
    // workspace). Legacy flat editor_files kept for back-compat readers.
    const panes = {};
    for (const p of _term.panes) {
      const ptabs = _terminalPaneTabs(p.id);
      const eds = ptabs.filter(t => t.kind === 'editor');
      const chats = ptabs.filter(t => t.kind === 'chat' && t.sessionId);
      const activeTab = ptabs.find(t => t.id === p.active);
      panes[p.id] = {
        editor_files: eds.map(t => t.path),
        // Persist open terminal-chats (by session id) so they reopen on reload.
        chat_sessions: chats.map(t => t.sessionId),
        active_path: (activeTab && activeTab.kind === 'editor') ? activeTab.path : '',
        active_chat: (activeTab && activeTab.kind === 'chat') ? (activeTab.sessionId || '') : '',
      };
    }
    const ws = {
      panes,
      // No stored layout: the grid is derived from pane occupancy on load. `sizes`
      // is the {col,row} split (0..1 fractions).
      sizes: _term.sizes,
      editor_files: _term.tabs.filter(t => t.kind === 'editor').map(t => t.path),  // legacy
      tree_visible: _term.treeVisible,
      tree_width: _term.treeWidth,
      tree_split: _term.treeSplit,
      single_editor: _term.singleEditor,
    };
    try { API.updateProject(_term.agent, _term.project, { bottom_workspace: ws }); } catch (_) {}
  }, 600);
}

async function terminalNewTab(paneId) {
  try {
    const s = await API.post(`/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions`, {});
    if (s.error) { if (typeof showToast === 'function') showToast(s.error); return; }
    // Explicit pane (per-pane + button) wins; otherwise default a terminal to
    // bottom → top-right → top-left for the current layout.
    const target = paneId || _terminalDefaultPane('terminal');
    _terminalAddTab(s.id, target);
    _terminalRenderTabs();
    _terminalActivate(s.id);
  } catch (e) { if (typeof showToast === 'function') showToast('Terminal konnte nicht gestartet werden'); }
}

// Build an xterm theme from the app's CSS variables so the terminal follows the
// light/dark mode of the rest of the UI (was hardcoded #1e1e1e → black in light
// mode). Reads the resolved --bg-000 / --text-100 / --accent-brand off :root.
function _terminalXtermTheme() {
  let bg = '#1e1e1e', fg = '#eaeaec', accent = '#3b82f6';
  try {
    const cs = getComputedStyle(document.documentElement);
    bg = (cs.getPropertyValue('--bg-000') || bg).trim() || bg;
    fg = (cs.getPropertyValue('--text-100') || fg).trim() || fg;
    accent = (cs.getPropertyValue('--accent-brand') || accent).trim() || accent;
  } catch (_) { /* fall back to dark defaults */ }
  return { background: bg, foreground: fg, cursor: accent,
           selectionBackground: accent + '55' };
}

// Re-apply the theme to all live terminals (call after a light/dark switch).
function terminalRetheme() {
  const theme = _terminalXtermTheme();
  for (const t of _term.tabs) {
    if (t.kind === 'terminal' && t.term) { try { t.term.options.theme = theme; } catch (_) {} }
  }
}

function _terminalAddTab(id, paneId) {
  if (_term.tabs.find(t => t.id === id)) return;
  const pane = _terminalGetPane(paneId) || _terminalActivePane() || _term.panes[0];
  const el = document.createElement('div');
  el.className = 'terminal-xterm';
  el.style.display = 'none';
  (pane ? pane.bodyEl : document.getElementById('terminal-panes')).appendChild(el);
  const term = new Terminal({
    fontSize: 13, fontFamily: 'var(--font-mono, monospace)',
    cursorBlink: true, scrollback: 5000,
    theme: _terminalXtermTheme(),
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(el);
  const tab = { id, kind: 'terminal', term, fit, offset: 0, attached: false, abort: null, el, _fresh: true,
                pane: pane ? pane.id : 'pane-a' };
  // keystrokes → POST input
  term.onData(data => {
    fetch(`${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${id}/input`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ data }),
    }).catch(() => {});
  });
  _term.tabs.push(tab);
}

// Add a terminal-CHAT tab (kind:'chat') — the body + behaviour live in
// panels_termchat.js. `sessionId` may be '' for a fresh chat (the session is
// created lazily on first send). `tmpId` lets the caller supply a stable id for
// an as-yet-session-less tab. Returns the tab.
function _terminalAddChatTab(sessionId, paneId, title, tmpId) {
  const id = sessionId ? ('chat-' + sessionId) : (tmpId || ('chat-new-' + _term.tabs.length));
  const exist = _term.tabs.find(t => t.id === id);
  if (exist) { _terminalActivate(id); return exist; }
  // Explicit paneId (per-pane ◈ button / persistence restore) wins; otherwise a
  // terminal-chat defaults to bottom → top-right → top-left for the layout.
  const _targetPane = paneId || _terminalDefaultPane('chat');
  const pane = _terminalGetPane(_targetPane) || _terminalActivePane() || _term.panes[0];
  const el = document.createElement('div');
  el.style.display = 'none';
  (pane ? pane.bodyEl : document.getElementById('terminal-panes')).appendChild(el);
  const tab = {
    id, kind: 'chat', sessionId: sessionId || '', name: title || 'Chat', el,
    model: '', thinking: 'none', caveman: 0, showTools: true,
    history: [], histIdx: -1, draft: '',
    streaming: false, _abort: null, _spinTimer: null, _live: null,
    log: [], tokensIn: 0, tokensOut: 0, cost: null, lastApiIn: 0, maxContext: 0,
    pane: pane ? pane.id : 'pane-a',
  };
  _term.tabs.push(tab);
  if (typeof tcBuildBody === 'function') tcBuildBody(tab);
  _terminalRenderTabs();
  _terminalActivate(id);
  if (typeof renderTermchatHistory === 'function') renderTermchatHistory();
  return tab;
}

function _terminalActivate(id) {
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  // activate within the tab's pane (per-pane active tab; the pane becomes the
  // active pane). Only that pane's tab visibility changes.
  const pane = _terminalGetPane(tab.pane);
  if (pane) { pane.active = id; _term.activePane = pane.id; }
  _terminalShowActiveTabs();
  _terminalRenderTabs();
  if (tab.kind === 'editor') {
    setTimeout(() => {
      try {
        if (!tab.cm) return;
        tab.cm.refresh();
        // restore the saved caret position, then refocus the editor
        const cur = _terminalLoadCursor(tab.path);
        if (cur) tab.cm.setCursor(cur);
        tab.cm.focus();
      } catch (_) {}
    }, 30);
    if (typeof repaintTerminalTree === 'function') repaintTerminalTree();  // highlight in tree
    _terminalPersist();
    return;
  }
  if (tab.kind === 'chat') {
    // No stream/PTY to attach — just focus the input + refresh the status footer.
    setTimeout(() => { try { const ta = tab.el.querySelector('.tc-ta'); if (ta) ta.focus(); } catch (_) {} }, 30);
    if (typeof tcRenderStatus === 'function') tcRenderStatus(tab);
    if (typeof renderTermchatHistory === 'function') renderTermchatHistory();
    _terminalPersist();
    return;
  }
  // terminal: (re)attach the output stream from our current offset
  if (!tab.attached) _terminalAttach(tab);
  setTimeout(() => { try { tab.fit.fit(); tab.term.focus(); _terminalSendResize(tab); } catch (_) {} }, 30);
  _terminalPersist();
}

// Stream PTY output via fetch+reader (NOT EventSource — like the chat stream,
// because EventSource can't send the Bearer header; we never put the token in
// the URL). Parses SSE frames ('out' = base64 bytes, 'closed').
async function _terminalAttach(tab) {
  tab.attached = true;
  tab.abort = new AbortController();
  const url = `${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${tab.id}/stream?since=${tab.offset}`;
  try {
    const resp = await fetch(url, {
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
      signal: tab.abort.signal,
    });
    if (!resp.ok || !resp.body) { tab.attached = false; return; }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
        let ev = 'message', data = '';
        frame.split('\n').forEach(line => {
          if (line.startsWith('event: ')) ev = line.slice(7);
          else if (line.startsWith('data: ')) data += line.slice(6);
        });
        if (ev === 'out' && data) {
          try { const bytes = atob(data); tab.term.write(bytes); tab.offset += bytes.length; } catch (_) {}
        } else if (ev === 'closed') {
          tab.term.write('\r\n[Sitzung beendet]\r\n');
        }
      }
    }
  } catch (e) { /* aborted or network — leave attached=false so a re-activate reconnects */ }
  tab.attached = false;
}

function _terminalSendResize(tab) {
  if (!tab) return;
  const rows = tab.term.rows, cols = tab.term.cols;
  fetch(`${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${tab.id}/input`, {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows, cols }),
  }).catch(() => {});
}

async function terminalCloseTab(id, ev) {
  if (ev) ev.stopPropagation();
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  // Warn on unsaved editor changes.
  if (tab.kind === 'editor' && tab.dirty) {
    if (!confirm(`„${tab.name}“ hat ungespeicherte Änderungen. Trotzdem schließen?`)) return;
  }
  if (tab.kind === 'editor') {
    tab.el.remove();
  } else if (tab.kind === 'chat') {
    // Abort an in-flight stream (the server worker is NOT cancelled — closing a
    // view never cancels a turn; an explicit /cancel does). Then drop the el.
    if (tab._abort) { try { tab._abort.abort(); } catch (_) {} }
    if (tab._spinTimer) { try { clearInterval(tab._spinTimer); } catch (_) {} }
    tab.el.remove();
  } else {
    if (tab.abort) { try { tab.abort.abort(); } catch (_) {} }
    try { tab.term.dispose(); } catch (_) {}
    tab.el.remove();
    fetch(`${BASE_URL}/v1/agents/${_term.agent}/projects/${encodeURIComponent(_term.project)}/terminal/sessions/${id}/close`, {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
    }).catch(() => {});
  }
  const wasEditor = tab.kind === 'editor';
  const wasChat = tab.kind === 'chat';
  const paneId = tab.pane;
  _term.tabs = _term.tabs.filter(t => t.id !== id);
  const pane = _terminalGetPane(paneId);
  if (pane && pane.active === id) {
    const left = _terminalPaneTabs(paneId);
    pane.active = left.length ? left[0].id : null;
    if (pane.active) _terminalActivate(pane.active);
  }
  // close the whole bottom area only when NO tab remains anywhere
  if (!_term.tabs.length) { terminalTogglePanel(false); return; }
  // Rebuild from occupancy so an emptied cell collapses and neighbours reclaim its
  // space (the dynamic-grid contract). _terminalBuildPanes re-renders + re-shows.
  _terminalBuildPanes();
  if (pane && pane.active) _terminalActivate(pane.active);
  if (wasEditor && typeof repaintTerminalTree === 'function') repaintTerminalTree();
  if (wasChat && typeof renderTermchatHistory === 'function') renderTermchatHistory();
  _terminalPersist();
  setTimeout(_terminalOnResize, 40);
}

// Bulk close (browser-style): 'all' | 'others' | 'right' relative to a tab id.
async function terminalCloseTabs(mode, anchorId, ev) {
  if (ev) ev.stopPropagation();
  let ids = [];
  if (mode === 'all') {
    ids = _term.tabs.map(t => t.id);
  } else if (mode === 'others') {
    ids = _term.tabs.filter(t => t.id !== anchorId).map(t => t.id);
  } else if (mode === 'right') {
    const i = _term.tabs.findIndex(t => t.id === anchorId);
    ids = i >= 0 ? _term.tabs.slice(i + 1).map(t => t.id) : [];
  }
  // close non-anchor first so the anchor stays active
  for (const id of ids) {
    // skip the confirm-loop spam: only the dirty ones prompt
    await terminalCloseTab(id);
  }
}

function _terminalTabMenu(id, ev) {
  ev.preventDefault(); ev.stopPropagation();
  const old = document.getElementById('terminal-tab-menu');
  if (old) old.remove();
  const m = document.createElement('div');
  m.id = 'terminal-tab-menu';
  m.className = 'terminal-tab-menu';
  m.style.left = ev.clientX + 'px';
  m.style.top = ev.clientY + 'px';
  m.innerHTML = `
    <div onclick="terminalCloseTab('${id}'); document.getElementById('terminal-tab-menu').remove()">Tab schließen</div>
    <div onclick="terminalCloseTabs('others','${id}'); document.getElementById('terminal-tab-menu').remove()">Andere schließen</div>
    <div onclick="terminalCloseTabs('right','${id}'); document.getElementById('terminal-tab-menu').remove()">Rechte schließen</div>
    <div onclick="terminalCloseTabs('all'); document.getElementById('terminal-tab-menu').remove()">Alle schließen</div>`;
  document.body.appendChild(m);
  const close = () => { const e = document.getElementById('terminal-tab-menu'); if (e) e.remove(); document.removeEventListener('click', close); };
  setTimeout(() => document.addEventListener('click', close), 0);
}

function _terminalRenderTabs() {
  let termN = 0;
  const termNum = new Map();   // stable "Terminal N" numbering across panes
  for (const t of _term.tabs) { if (t.kind === 'terminal') termNum.set(t.id, ++termN); }
  for (const pane of _term.panes) {
    if (!pane.barEl) continue;
    const tabs = _terminalPaneTabs(pane.id);
    pane.barEl.innerHTML = tabs.map((t) => {
      const active = t.id === pane.active ? ' active' : '';
      let label, cls = '';
      if (t.kind === 'editor') {
        // '*' = unsaved; italic = edit mode; amber tab (.ed-conflict) = the file
        // changed on disk while the tab had unsaved edits (not clobbered).
        label = esc(t.name) + (t.dirty ? '*' : '');
        if (t.mode === 'raw') cls = ' ed-editing';
        if (t.extConflict) cls += ' ed-conflict';
      } else if (t.kind === 'chat') {
        // ◈ chat icon + title; pulse dot while a turn streams.
        label = '◈ ' + esc(t.name || 'Chat');
        if (t.streaming) cls = ' tc-tab-live';
      } else {
        label = 'Terminal ' + (termNum.get(t.id) || '');
      }
      return `<div class="terminal-tab${active}${cls}" draggable="true" data-tab-id="${esc(t.id)}"
        title="${esc(t.path || label)}" onclick="_terminalActivate('${t.id}')"
        oncontextmenu="_terminalTabMenu('${t.id}', event)"
        ondragstart="_terminalTabDragStart(event,'${esc(t.id)}')" ondragend="_terminalTabDragEnd(event)">
        <span class="tt-name">${label}</span>
        <span class="terminal-tab-close" onclick="terminalCloseTab('${t.id}', event)">✕</span>
      </div>`;
    }).join('');
  }
}

// Tab drag-and-drop between panes (HTML5 DnD). While a drag is active, raise the
// per-pane drop overlays above the pane content (via the `tp-dnd` class on the
// host) so dragover/drop land on the overlay, not the xterm/editor/chat DOM that
// would otherwise swallow them.
function _terminalTabDragStart(ev, tabId) {
  ev.dataTransfer.setData('text/tab-id', tabId);
  ev.dataTransfer.effectAllowed = 'move';
  ev.currentTarget.classList.add('tp-dragging');
  const host = document.getElementById('terminal-panes');
  if (host) host.classList.add('tp-dnd');
}
function _terminalTabDragEnd(ev) {
  ev.currentTarget.classList.remove('tp-dragging');
  const host = document.getElementById('terminal-panes');
  if (host) { host.classList.remove('tp-dnd'); host.querySelectorAll('.tpane-drop[data-zone]').forEach(d => d.removeAttribute('data-zone')); }
}

// ── Editor tabs (CodeMirror 5) ───────────────────────────────────────────────
// Per-file cursor persistence (UI-only, localStorage, keyed by project+path) so
// reopening a file restores the caret line/col. Debounced on cursor activity.
function _terminalCursorKey(absPath) {
  return `brain.edcursor.${_term.project || 'p'}:${absPath}`;
}
let _terminalCursorTimer = null;
function _terminalSaveCursor(tab) {
  if (!tab || !tab.cm) return;
  clearTimeout(_terminalCursorTimer);
  _terminalCursorTimer = setTimeout(() => {
    try {
      const c = tab.cm.getCursor();
      localStorage.setItem(_terminalCursorKey(tab.path), JSON.stringify({ line: c.line, ch: c.ch }));
    } catch (_) {}
  }, 300);
}
function _terminalLoadCursor(absPath) {
  try {
    const v = JSON.parse(localStorage.getItem(_terminalCursorKey(absPath)) || 'null');
    return (v && typeof v.line === 'number') ? v : null;
  } catch (_) { return null; }
}

// ── Auto-reload editors on external/disk change ──────────────────────────────
// When a file open in an editor changes on disk (LLM write, terminal command,
// external edit), reflect it. Cheap mtime poll via /v1/files/stat; on change,
// re-fetch content. If the tab has UNSAVED edits we don't clobber — we mark the
// tab and toast once so the user decides.
async function _terminalReloadEditorContent(tab) {
  try {
    const d = await API.get(`/v1/files/preview?path=${encodeURIComponent(tab.path)}&lines=100000`);
    if (d.error) return;
    tab.size = d.size || 0;
    tab.mtime = d.mtime || 0;
    tab.raw = d.content || '';
    tab.truncated = !!d.truncated;
    tab.extConflict = false;
    if (tab.cm) {
      // preserve caret across the reload
      const cur = tab.cm.getCursor();
      tab.cm.setValue(tab.raw);
      try { tab.cm.setCursor(cur); } catch (_) {}
    }
    tab.dirty = false;
    const sv = tab.el.querySelector('.ed-save'); if (sv) sv.disabled = true;
    if (tab.mode === 'render') _terminalEditorPaint(tab);  // re-render preview
    _terminalEditorStats(tab);
    _terminalRenderTabs();
  } catch (_) { /* transient */ }
}

let _terminalFreshPollTimer = null;
async function terminalCheckEditorsFresh() {
  if (!_term.open) return;
  const editors = _term.tabs.filter(t => t.kind === 'editor' && t.loaded);
  for (const tab of editors) {
    let st;
    try { st = await API.get(`/v1/files/stat?path=${encodeURIComponent(tab.path)}`); }
    catch (_) { continue; }
    if (!st || st.error || typeof st.mtime !== 'number') continue;
    if (tab.mtime && st.mtime > tab.mtime) {
      if (tab.dirty) {
        // don't clobber unsaved work — flag once
        if (!tab.extConflict) {
          tab.extConflict = true;
          _terminalRenderTabs();
          if (typeof showToast === 'function') showToast(`„${tab.name}" wurde extern geändert (ungespeicherte Änderungen — nicht überschrieben)`, true);
        }
      } else {
        await _terminalReloadEditorContent(tab);
        if (typeof showToast === 'function') showToast(`„${tab.name}" neu geladen (extern geändert)`);
      }
    }
  }
}

// Poll the working-dir tree so files created/deleted out-of-band (a terminal
// command, the `!` shell command, the agent, an external program) show up
// without a manual refresh. Cheap: re-fetch the tree, compare a signature of the
// file/dir path set, and only re-render when it actually changed (so scroll +
// expand state aren't disturbed every tick). Runs on the same cadence as the
// editor-fresh poll, only while the panel is open + the tree is visible.
function _wdTreeSignature(nodes, acc) {
  acc = acc || [];
  for (const n of (nodes || [])) {
    acc.push((n.type === 'dir' ? 'd:' : 'f:') + n.path);
    if (n.type === 'dir') _wdTreeSignature(n.children || [], acc);
  }
  return acc;
}
async function terminalPollTree() {
  if (!_term.open || !_term.treeVisible) return;
  if (typeof _workdirActiveProject !== 'function') return;
  let proj;
  try { proj = await _workdirActiveProject(); } catch (_) { return; }
  if (!proj || !proj.working_dir) return;
  let data;
  try {
    data = await API.get(`/v1/agents/${proj.agent}/projects/${encodeURIComponent(proj.name)}/folder-tree?path=${encodeURIComponent(proj.working_dir)}`);
  } catch (_) { return; }
  if (!data || data.error || !Array.isArray(data.tree)) return;
  const sig = _wdTreeSignature(data.tree).sort().join('\n');
  if (sig === _term._treeSig) return;   // no structural change → leave the tree alone
  _term._treeSig = sig;
  window._wdTreeData = data.tree;
  // Also refresh the code-index sync dots, then repaint from the new data.
  if (typeof _wdLoadCodeIndexState === 'function') { try { await _wdLoadCodeIndexState(proj); } catch (_) {} }
  if (typeof repaintTerminalTree === 'function') repaintTerminalTree();
}

function startEditorFreshPoll() {
  stopEditorFreshPoll();
  _terminalFreshPollTimer = setInterval(() => {
    terminalCheckEditorsFresh();
    terminalPollTree();
  }, 4000);
}
function stopEditorFreshPoll() {
  if (_terminalFreshPollTimer) { clearInterval(_terminalFreshPollTimer); _terminalFreshPollTimer = null; }
}

// Map a file extension → a CodeMirror mode (modes loaded in index.html).
function _cmModeFor(ext) {
  ext = (ext || '').toLowerCase();
  const m = {
    py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript',
    json: { name: 'javascript', json: true }, ts: 'javascript', jsx: 'javascript', tsx: 'javascript',
    html: 'htmlmixed', htm: 'htmlmixed', xml: 'xml', svg: 'xml',
    css: 'css', scss: 'css', less: 'css',
    md: 'markdown', markdown: 'markdown',
    c: 'text/x-csrc', h: 'text/x-csrc', cpp: 'text/x-c++src', cc: 'text/x-c++src',
    hpp: 'text/x-c++src', java: 'text/x-java', cs: 'text/x-csharp',
    yml: 'yaml', yaml: 'yaml', sh: 'shell', bash: 'shell', zsh: 'shell',
    go: 'go', rs: 'rust',
  };
  return m[ext] || null;
}

// Open a file as an editor tab (or focus it if already open).
async function terminalOpenFile(absPath) {
  if (!absPath) return;
  if (!_term.open) { await terminalTogglePanel(true); }
  const existing = _term.tabs.find(t => t.kind === 'editor' && t.path === absPath);
  if (existing) { _terminalActivate(existing.id); return; }
  // Single-editor mode: close the current (or any) open editor tab first so at
  // most one editor stays open — the new file replaces it. A dirty editor still
  // gets the unsaved-changes confirm via terminalCloseTab.
  if (_term.singleEditor) {
    const cur = _term.tabs.find(t => t.kind === 'editor');  // at most one open
    if (cur) {
      const before = _term.tabs.length;
      await terminalCloseTab(cur.id);
      // user cancelled the dirty-close confirm → abort opening the new file
      if (_term.tabs.length === before) return;
    }
  }
  const name = absPath.split('/').pop();
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  const id = 'edit-' + absPath;
  const el = document.createElement('div');
  el.className = 'terminal-editor';
  el.style.display = 'none';
  // Toolbar buttons are SVG-only (no text labels, per the UI icon rule). The two
  // mode buttons (Ansicht/Bearbeiten) toggle read-only vs editable — the editor
  // LOOKS identical in both (CodeMirror, same line numbers + colouring); only a
  // cursor distinguishes edit mode.
  el.innerHTML = `
    <div class="editor-toolbar">
      <button class="ed-iconbtn ed-mode active" data-mode="render" onclick="terminalEditorMode('${id}','render')" title="Ansicht (nur lesen)" aria-label="Ansicht">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>
      </button>
      <button class="ed-iconbtn ed-mode" data-mode="raw" onclick="terminalEditorMode('${id}','raw')" title="Bearbeiten" aria-label="Bearbeiten">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
      </button>
      <span class="ed-sep"></span>
      <button class="ed-iconbtn" onclick="codeSymbolPalette()" title="Symbol suchen (${_isMac()?'⌘':'Strg'}+P)" aria-label="Symbole suchen">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
      </button>
      <button class="ed-iconbtn" onclick="codeCypherBar()" title="Code-Index per Cypher abfragen (Power-User)" aria-label="Cypher-Abfrage">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
      </button>
      <span style="flex:1"></span>
      <button class="ed-iconbtn ed-save" onclick="terminalEditorSave('${id}')" title="Speichern" aria-label="Speichern" disabled>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
      </button>
      <button class="ed-iconbtn" onclick="terminalEditorDownload('${id}')" title="Herunterladen" aria-label="Herunterladen">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      </button>
    </div>
    <div class="editor-render" style="display:none"></div>
    <div class="editor-cm"></div>
    <div class="editor-status"><span class="ed-stat-name"></span><span style="flex:1"></span><span class="ed-stat-meta"></span></div>`;
  // Default placement by file kind + layout (source → top-left, other →
  // top-right→top-left). _termOpenInPane (set by the per-pane new-file button)
  // overrides with an explicit pane so a pane-scoped action stays in its pane.
  const _paneId = _termOpenInPane || _terminalDefaultPane('editor', ext);
  _termOpenInPane = null;
  const _pane = _terminalGetPane(_paneId) || _terminalActivePane() || _term.panes[0];
  (_pane ? _pane.bodyEl : document.getElementById('terminal-panes')).appendChild(el);
  const tab = { id, kind: 'editor', path: absPath, name, ext, el, cm: null,
                raw: '', mode: 'render', dirty: false, loaded: false,
                size: 0, mtime: 0, pane: _pane ? _pane.id : 'pane-a' };
  _term.tabs.push(tab);
  _terminalRenderTabs();
  _terminalActivate(id);
  // load content
  try {
    const d = await API.get(`/v1/files/preview?path=${encodeURIComponent(absPath)}&lines=100000`);
    if (d.error) { el.querySelector('.editor-render').style.display = 'block'; el.querySelector('.editor-cm').style.display = 'none'; el.querySelector('.editor-render').innerHTML = `<div class="pt-empty">${esc(d.error)}</div>`; return; }
    tab.size = d.size || 0;
    tab.mtime = d.mtime || 0;
    // SVG is text we can edit + render — the preview endpoint reports it as an
    // image, so fetch the raw XML and treat it as a renderable text file.
    if (d.type === 'image' && tab.ext === 'svg') {
      try {
        const r = await fetch(`${BASE_URL}/v1/files/download?path=${encodeURIComponent(absPath)}`,
          { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') } });
        tab.raw = await r.text();
      } catch (_) { tab.raw = ''; }
      tab.loaded = true;
      _terminalEditorPaint(tab);
      _terminalEditorStats(tab);
      return;
    }
    if (d.type === 'image') {
      _terminalEditorShowImage(tab);
      // images: no edit
      el.querySelector('.editor-cm').style.display = 'none';
      el.querySelector('.ed-mode[data-mode="raw"]').style.display = 'none';
      el.querySelector('.ed-save').style.display = 'none';
      _terminalEditorStats(tab);
      return;
    }
    tab.raw = d.content || '';
    tab.truncated = !!d.truncated;
    tab.loaded = true;
    _terminalEditorPaint(tab);
    _terminalEditorStats(tab);
  } catch (e) {
    el.querySelector('.editor-render').innerHTML = '<div class="pt-empty">Datei konnte nicht geladen werden.</div>';
  }
}

function terminalEditorMode(id, mode) {
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  tab.mode = mode;
  tab.el.querySelectorAll('.ed-mode').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  _terminalEditorPaint(tab);
  _terminalRenderTabs();  // reflect edit-mode italic in the tab label
}

// BOTH modes use the SAME CodeMirror instance (same line numbers + syntax
// colouring); 'render' = read-only (no cursor), 'raw' = editable. There is NO
// separate highlight.js/markdown view — per the user, the only visible
// difference between view and edit is the cursor — EXCEPT renderable files
// (html/md/svg), where read-only "Ansicht" shows the RENDERED output and edit
// shows the CodeMirror source.
const _ED_RENDERABLE = { html: 1, htm: 1, md: 1, markdown: 1, svg: 1 };
function _terminalIsRenderable(ext) { return !!_ED_RENDERABLE[(ext || '').toLowerCase()]; }

// Render a renderable file's content into the .editor-render pane (Ansicht mode).
function _terminalEditorRender(tab) {
  const renderEl = tab.el.querySelector('.editor-render');
  if (!renderEl) return;
  const txt = (tab.cm && tab.dirty) ? tab.cm.getValue() : tab.raw;
  const ext = (tab.ext || '').toLowerCase();
  if (ext === 'md' || ext === 'markdown') {
    renderEl.innerHTML = (typeof renderMarkdown === 'function')
      ? `<div class="ref-inline-md msg-content" style="padding:12px">${renderMarkdown(txt)}</div>`
      : `<pre class="editor-pre">${esc(txt)}</pre>`;
    renderEl.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch (_) {} });
  } else {
    // html/htm/svg → render the markup in a sandboxed iframe (srcdoc). No
    // allow-scripts/allow-same-origin → a preview can't reach the app or run JS.
    renderEl.innerHTML = '';
    const frame = document.createElement('iframe');
    frame.className = 'editor-render-frame';
    frame.setAttribute('sandbox', '');
    frame.srcdoc = txt;
    renderEl.appendChild(frame);
  }
}

function _terminalEditorPaint(tab) {
  const cmEl = tab.el.querySelector('.editor-cm');
  const renderEl = tab.el.querySelector('.editor-render');
  // Renderable file in Ansicht mode → show the rendered output, hide CM.
  if (tab.mode === 'render' && _terminalIsRenderable(tab.ext)) {
    cmEl.style.display = 'none';
    if (renderEl) renderEl.style.display = 'block';
    _terminalEditorRender(tab);
    return;
  }
  if (renderEl) renderEl.style.display = 'none';
  cmEl.style.display = 'block';
  if (!tab.cm) {
    tab.cm = CodeMirror(cmEl, {
      value: tab.raw, mode: _cmModeFor(tab.ext), lineNumbers: true,
      lineWrapping: false, indentUnit: 4,
      readOnly: tab.mode === 'raw' ? false : 'nocursor',
      extraKeys: {
        // index-fed autocomplete (project symbols from the cbm index).
        // Ctrl-Space only — NOT Cmd-Space (that's macOS Spotlight, the OS
        // grabs it before the browser sees it).
        'Ctrl-Space': (cm) => codeIndexComplete(cm),
      },
    });
    // CSS drives the height (.editor-cm .CodeMirror { height:100% }); a
    // refresh() after the element is laid out lets CM measure + show scrollbars.
    // (setSize('100%','100%') broke CM's scroll measurement → no scrollbars.)
    tab.cm.on('change', () => {
      const wasDirty = tab.dirty;
      tab.dirty = true;
      const sv = tab.el.querySelector('.ed-save'); if (sv) sv.disabled = false;
      _terminalRenderTabs();
      // reflect the unsaved-'*' in the file tree (only on the false→true flip)
      if (!wasDirty && typeof repaintTerminalTree === 'function') repaintTerminalTree();
    });
    // right-click a symbol → go-to-definition / who-calls menu
    tab.cm.on('contextmenu', (cm, e) => _codeIndexContextMenu(cm, e, tab));
    // hover a symbol → signature + docstring + caller count tooltip
    _codeIndexAttachHover(tab);
    // persist the cursor (line/ch) per file so reopening restores the position
    tab.cm.on('cursorActivity', () => _terminalSaveCursor(tab));
    // restore the saved cursor on first paint (after the doc is in the CM)
    const cur = _terminalLoadCursor(tab.path);
    if (cur) { try { tab.cm.setCursor(cur); } catch (_) {} }
  } else {
    // keep CM in sync if raw changed via save; flip editability for the mode
    if (tab.cm.getValue() !== tab.raw && !tab.dirty) tab.cm.setValue(tab.raw);
    tab.cm.setOption('readOnly', tab.mode === 'raw' ? false : 'nocursor');
  }
  // edit mode gets focus (so the cursor shows); view mode does not
  setTimeout(() => { try { tab.cm.refresh(); if (tab.mode === 'raw') tab.cm.focus(); } catch (_) {} }, 20);
}

// Editor status line: file name + size + last-modified + (for text) line count
// and a truncation hint. Reuses _fmtBytes (panels_workdir.js global).
function _terminalEditorStats(tab) {
  if (!tab || !tab.el) return;
  const nameEl = tab.el.querySelector('.ed-stat-name');
  const metaEl = tab.el.querySelector('.ed-stat-meta');
  if (nameEl) nameEl.textContent = tab.name + (tab.truncated ? ' · (gekürzt angezeigt)' : '');
  const bits = [];
  if (tab.size) bits.push(typeof _fmtBytes === 'function' ? _fmtBytes(tab.size) : tab.size + ' B');
  if (tab.cm) { try { bits.push(tab.cm.lineCount() + ' Zeilen'); } catch (_) {} }
  if (tab.mtime) bits.push('geändert ' + new Date(tab.mtime * 1000).toLocaleString());
  if (metaEl) metaEl.textContent = bits.join(' · ');
}

async function _terminalEditorShowImage(tab) {
  try {
    const resp = await fetch(`${BASE_URL}/v1/files/download?path=${encodeURIComponent(tab.path)}`,
      { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') } });
    const blob = await resp.blob();
    const renderEl = tab.el.querySelector('.editor-render');
    renderEl.style.display = 'block';
    renderEl.innerHTML = `<img src="${URL.createObjectURL(blob)}" style="max-width:100%;padding:10px"/>`;
  } catch (e) { /* ignore */ }
}

async function terminalEditorSave(id) {
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab || !tab.cm) return;
  const content = tab.cm.getValue();
  try {
    const r = await fetch(`${BASE_URL}/v1/files/save`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: tab.path, content }),
    });
    const d = await r.json();
    if (d.error) { if (typeof showToast === 'function') showToast(d.error); return; }
    tab.raw = content; tab.dirty = false; tab.extConflict = false;
    // refresh stats: size from the saved content, mtime = now (just written)
    tab.size = new Blob([content]).size;
    tab.mtime = Math.floor(Date.now() / 1000);
    tab.truncated = false;
    _terminalEditorStats(tab);
    const sv = tab.el.querySelector('.ed-save'); if (sv) sv.disabled = true;
    _terminalRenderTabs();
    // a save clears the unsaved-'*' and may flip the file's git state → reload
    if (typeof refreshTerminalTree === 'function') refreshTerminalTree();
    if (typeof showToast === 'function') showToast('Gespeichert');
  } catch (e) { if (typeof showToast === 'function') showToast('Speichern fehlgeschlagen'); }
}

function terminalEditorDownload(id) {
  const tab = _term.tabs.find(t => t.id === id);
  if (tab && typeof _ptDownloadFile === 'function') _ptDownloadFile(tab.path, tab.name);
}

// Create a new file in the project tree (prompts for a relative path).
async function terminalNewFile() {
  if (!_term.wd) { const ctx = await _terminalCtx(); if (ctx) { _term.agent = ctx.agent; _term.project = ctx.project; _term.wd = ctx.wd; } }
  if (!_term.wd) { if (typeof showToast === 'function') showToast('Kein Code-Mode-Projekt'); return; }
  const rel = prompt('Neue Datei (relativer Pfad im Projekt):', 'neu.txt');
  if (!rel) return;
  const clean = rel.replace(/^\/+/, '').split('/').filter(p => p && p !== '..').join('/');
  if (!clean) return;
  const abs = _term.wd.replace(/\/+$/, '') + '/' + clean;
  try {
    const r = await fetch(`${BASE_URL}/v1/files/save`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || ''), 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: abs, content: '' }),
    });
    const d = await r.json();
    if (d.error) { if (typeof showToast === 'function') showToast(d.error); return; }
    if (typeof refreshCodeWorkingTree === 'function') refreshCodeWorkingTree();
    if (typeof refreshTerminalTree === 'function') refreshTerminalTree();
    await terminalOpenFile(abs);
    terminalEditorMode('edit-' + abs, 'raw');
  } catch (e) { if (typeof showToast === 'function') showToast('Datei konnte nicht erstellt werden'); }
}

// Maximize / restore the bottom panel.
function terminalToggleMaximize() {
  const panel = document.getElementById('terminal-panel');
  if (!panel) return;
  _term.maximized = !_term.maximized;
  panel.classList.toggle('maximized', _term.maximized);
  setTimeout(_terminalOnResize, 30);
}

// Re-fit/refresh the active tab of EVERY pane on resize (each pane has its own
// visible tab — a terminal needs a fit, an editor a refresh).
function _terminalOnResize() {
  if (!_term.open) return;
  for (const pane of _term.panes) {
    const tab = _terminalPaneTabs(pane.id).find(t => t.id === pane.active);
    if (!tab) continue;
    if (tab.kind === 'editor') { try { tab.cm && tab.cm.refresh(); } catch (_) {} }
    else { try { tab.fit.fit(); _terminalSendResize(tab); } catch (_) {} }
  }
}
window.addEventListener('resize', _terminalOnResize);

// Vertical resize of the bottom panel by dragging its top handle. The chosen
// height is remembered in localStorage (like right-panel-width) and restored on
// load, so the user's preferred terminal height persists across sessions.
const _TERMINAL_HEIGHT_KEY = 'terminal-panel-height';

function _terminalRestoreHeight() {
  const panel = document.getElementById('terminal-panel');
  if (!panel) return;
  const saved = parseInt(localStorage.getItem(_TERMINAL_HEIGHT_KEY) || '', 10);
  if (saved && saved >= 120) {
    panel.style.height = Math.min(saved, window.innerHeight - 160) + 'px';
  }
}

function _terminalInitResize() {
  const handle = document.getElementById('terminal-resize-handle');
  const panel = document.getElementById('terminal-panel');
  if (!handle || !panel) return;
  _terminalRestoreHeight();
  let startY = 0, startH = 0, dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true; startY = e.clientY; startH = panel.offsetHeight;
    document.body.style.userSelect = 'none'; e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const h = Math.max(120, Math.min(window.innerHeight - 160, startH + (startY - e.clientY)));
    panel.style.height = h + 'px';
    _terminalOnResize();
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; document.body.style.userSelect = '';
    // persist the chosen height as the preferred value
    try { localStorage.setItem(_TERMINAL_HEIGHT_KEY, String(panel.offsetHeight)); } catch (_) {}
    _terminalOnResize();
  });
}

// ── File-tree column (left of the terminal/editor) ───────────────────────────
// Apply the persisted layout state (visibility, width, single-editor toggle) to
// the DOM. Called on open and after each toggle.
function _terminalApplyLayout() {
  const panel = document.getElementById('terminal-panel');
  const col = document.getElementById('terminal-tree-col');
  if (panel) panel.classList.toggle('tree-hidden', !_term.treeVisible);
  if (col) col.style.flexBasis = Math.max(120, _term.treeWidth || 240) + 'px';
  _terminalApplyTreeSplit();
  const se = document.getElementById('terminal-single-editor');
  if (se) se.classList.toggle('active', !!_term.singleEditor);
  const show = document.getElementById('terminal-tree-show');
  if (show) show.classList.toggle('active', !!_term.treeVisible);
}

function terminalToggleTree() {
  _term.treeVisible = !_term.treeVisible;
  _terminalApplyLayout();
  if (_term.treeVisible) refreshTerminalTree();
  _terminalPersist();
  setTimeout(_terminalOnResize, 30);
}

function terminalToggleSingleEditor() {
  _term.singleEditor = !_term.singleEditor;
  _terminalApplyLayout();
  if (typeof showToast === 'function') {
    showToast(_term.singleEditor ? 'Ein-Editor-Modus: an' : 'Ein-Editor-Modus: aus');
  }
  _terminalPersist();
}

function _terminalInitTreeResize() {
  const handle = document.getElementById('terminal-tree-resize');
  const col = document.getElementById('terminal-tree-col');
  if (!handle || !col) return;
  let startX = 0, startW = 0, dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true; startX = e.clientX; startW = col.offsetWidth;
    document.body.style.userSelect = 'none'; e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const w = Math.max(120, Math.min(700, startW + (e.clientX - startX)));
    col.style.flexBasis = w + 'px';
    _term.treeWidth = w;
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; document.body.style.userSelect = '';
    _terminalPersist();
    setTimeout(_terminalOnResize, 10);
  });
}

// Apply the file-tree ↔ Terminal-Chats height split (_term.treeSplit = the tree's
// fraction, 0.1–0.9) as flex-grow CSS vars on the left column.
function _terminalApplyTreeSplit() {
  const col = document.getElementById('terminal-tree-col');
  if (!col) return;
  const f = Math.max(0.1, Math.min(0.9, _term.treeSplit || 0.5));
  col.style.setProperty('--tree-grow', f.toFixed(3));
  col.style.setProperty('--chats-grow', (1 - f).toFixed(3));
}

// Drag handle between the file tree and the Terminal-Chats section → adjust the
// vertical split. Persisted per project (bottom_workspace.tree_split).
function _terminalInitTreeVsplit() {
  const handle = document.getElementById('terminal-tree-vsplit');
  const col = document.getElementById('terminal-tree-col');
  if (!handle || !col) return;
  let dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true; document.body.style.userSelect = ''; document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const r = col.getBoundingClientRect();
    // Fraction of the column height above the cursor = the tree's share.
    const f = Math.max(0.1, Math.min(0.9, (e.clientY - r.top) / r.height));
    _term.treeSplit = f;
    _terminalApplyTreeSplit();
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; document.body.style.userSelect = '';
    _terminalPersist();
    setTimeout(_terminalOnResize, 10);
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// Code-index editor support — symbol palette, go-to-definition, who-calls,
// autocomplete, hover. All fed by the cbm code index via the lean
// .../code-index/symbols endpoint (?q= fuzzy search · ?def= definition+meta ·
// ?callers= inbound callers). cbm returns ?q file paths REPO-RELATIVE, so we
// join _term.wd to reach an absolute path for terminalOpenFile.
// ═══════════════════════════════════════════════════════════════════════════

function _isMac() {
  return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
}

// Query the code-index endpoint for the active code-mode project. `params` is a
// query string (e.g. 'q=foo&limit=20'). Returns the parsed object or null.
async function _codeIndexFetch(params) {
  if (!_term.project) {
    const ctx = await _terminalCtx();
    if (!ctx) return null;
    _term.agent = ctx.agent; _term.project = ctx.project; _term.wd = ctx.wd;
  }
  try {
    return await API.get(`/v1/agents/${_term.agent}/projects/`
      + `${encodeURIComponent(_term.project)}/code-index/symbols?${params}`);
  } catch (e) { return null; }
}

// Join a repo-relative file path from cbm to an absolute path under working_dir.
function _codeIndexAbs(relOrAbs) {
  if (!relOrAbs) return '';
  if (relOrAbs.startsWith('/')) return relOrAbs;       // already absolute
  const wd = (_term.wd || '').replace(/\/+$/, '');
  return wd ? `${wd}/${relOrAbs}` : relOrAbs;
}

// Open `absPath` in the editor and move the cursor to `line` (1-based), centred.
async function _terminalJumpTo(absPath, line) {
  if (!absPath) return;
  await terminalOpenFile(absPath);
  const id = 'edit-' + absPath;
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  // jumping needs the CodeMirror instance → force raw (edit) mode
  terminalEditorMode(id, 'raw');
  const doJump = () => {
    if (!tab.cm) { setTimeout(doJump, 40); return; }
    const ln = Math.max(0, (parseInt(line, 10) || 1) - 1);
    tab.cm.setCursor({ line: ln, ch: 0 });
    // centre the target line in the viewport
    const t = tab.cm.charCoords({ line: ln, ch: 0 }, 'local').top;
    const h = tab.cm.getScrollInfo().clientHeight;
    tab.cm.scrollTo(null, Math.max(0, t - h / 2));
    tab.cm.addLineClass(ln, 'background', 'cm-jump-flash');
    setTimeout(() => { try { tab.cm.removeLineClass(ln, 'background', 'cm-jump-flash'); } catch (_) {} }, 1200);
    tab.cm.focus();
  };
  setTimeout(doJump, 60);
}

// ─── Symbol palette (Cmd/Ctrl-P) ─────────────────────────────────────────────
let _codePaletteDebounce = null;

function codeSymbolPalette() {
  if (document.getElementById('code-palette')) return;  // already open
  const ov = document.createElement('div');
  ov.id = 'code-palette';
  ov.className = 'code-palette-overlay';
  ov.innerHTML = `
    <div class="code-palette" onclick="event.stopPropagation()">
      <input id="code-palette-input" type="text" autocomplete="off" spellcheck="false"
             placeholder="Symbol suchen (Funktion, Methode, Klasse) …">
      <div id="code-palette-results" class="code-palette-results">
        <div class="code-palette-hint">Tippen, um Projekt-Symbole zu durchsuchen.</div>
      </div>
    </div>`;
  ov.addEventListener('click', codeSymbolPaletteClose);
  document.body.appendChild(ov);
  const input = document.getElementById('code-palette-input');
  input.addEventListener('input', () => {
    clearTimeout(_codePaletteDebounce);
    _codePaletteDebounce = setTimeout(() => _codePaletteSearch(input.value), 160);
  });
  input.addEventListener('keydown', _codePaletteKeydown);
  input.focus();
}

function codeSymbolPaletteClose() {
  const ov = document.getElementById('code-palette');
  if (ov) ov.remove();
}

async function _codePaletteSearch(q) {
  const box = document.getElementById('code-palette-results');
  if (!box) return;
  q = (q || '').trim();
  if (!q) { box.innerHTML = '<div class="code-palette-hint">Tippen, um Projekt-Symbole zu durchsuchen.</div>'; return; }
  box.innerHTML = '<div class="code-palette-hint">Suche …</div>';
  const d = await _codeIndexFetch(`q=${encodeURIComponent(q)}&limit=40`);
  // the box may have been replaced by a newer query; only paint if still ours
  const cur = document.getElementById('code-palette-input');
  if (!cur || cur.value.trim() !== q) return;
  if (!d || d.error) { box.innerHTML = `<div class="code-palette-hint">${esc((d && d.error) || 'Index nicht verfügbar')}</div>`; return; }
  const syms = d.symbols || [];
  if (!syms.length) { box.innerHTML = '<div class="code-palette-hint">Keine Treffer.</div>'; return; }
  box.innerHTML = syms.map((s, i) => `
    <div class="code-palette-row${i === 0 ? ' active' : ''}" data-idx="${i}"
         onclick="_codePalettePick(${i})">
      <span class="cp-label cp-${esc((s.label || '').toLowerCase())}">${esc(s.label || '')}</span>
      <span class="cp-name">${esc(s.name || '')}</span>
      <span class="cp-loc">${esc(s.file || '')}${s.line ? ':' + s.line : ''}</span>
    </div>`).join('');
  _codePaletteSyms = syms;
}

let _codePaletteSyms = [];

function _codePaletteKeydown(e) {
  const rows = Array.from(document.querySelectorAll('.code-palette-row'));
  if (e.key === 'Escape') { codeSymbolPaletteClose(); return; }
  if (!rows.length) return;
  let idx = rows.findIndex(r => r.classList.contains('active'));
  if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(rows.length - 1, idx + 1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(0, idx - 1); }
  else if (e.key === 'Enter') { e.preventDefault(); _codePalettePick(idx < 0 ? 0 : idx); return; }
  else return;
  rows.forEach(r => r.classList.remove('active'));
  rows[idx].classList.add('active');
  rows[idx].scrollIntoView({ block: 'nearest' });
}

function _codePalettePick(idx) {
  const s = _codePaletteSyms[idx];
  if (!s) return;
  codeSymbolPaletteClose();
  _terminalJumpTo(_codeIndexAbs(s.file), s.line);
}

// ─── Index-fed autocomplete (CodeMirror show-hint) ───────────────────────────
async function codeIndexComplete(cm) {
  if (!cm || typeof cm.showHint !== 'function') return;
  const cur = cm.getCursor();
  const token = cm.getTokenAt(cur);
  const word = (token.string || '').trim();
  if (word.length < 2) return;   // need a couple chars to query the index
  const d = await _codeIndexFetch(`q=${encodeURIComponent(word)}&limit=25`);
  const syms = (d && d.symbols) || [];
  // dedup by name, keep the symbol so we can show its kind
  const seen = new Set();
  const list = [];
  for (const s of syms) {
    if (!s.name || seen.has(s.name)) continue;
    seen.add(s.name);
    list.push({ text: s.name, displayText: `${s.name}  ·  ${s.label || ''}` });
  }
  if (!list.length) return;
  const from = { line: cur.line, ch: token.start };
  const to = { line: cur.line, ch: token.end };
  cm.showHint({
    hint: () => ({ list, from, to }),
    completeSingle: false,
  });
}

// ─── Right-click: go-to-definition / who-calls ───────────────────────────────
function _wordAt(cm, pos) {
  const token = cm.getTokenAt(pos);
  let w = (token.string || '').trim();
  if (!/^[A-Za-z_]\w*$/.test(w)) {
    // fall back to a word-range probe (handles punctuation tokens)
    const wr = cm.findWordAt(pos);
    w = cm.getRange(wr.anchor, wr.head).trim();
  }
  return /^[A-Za-z_]\w*$/.test(w) ? w : '';
}

function _codeIndexContextMenu(cm, e, tab) {
  const pos = cm.coordsChar({ left: e.clientX, top: e.clientY });
  const word = _wordAt(cm, pos);
  if (!word) return;   // not on a symbol → let the native menu show
  e.preventDefault();
  _codeIndexCloseMenu();
  const menu = document.createElement('div');
  menu.id = 'code-ctx-menu';
  menu.className = 'code-ctx-menu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  menu.innerHTML = `
    <div class="code-ctx-head">${esc(word)}</div>
    <div class="code-ctx-item" onclick="codeGotoDefinition('${esc(word)}')">Gehe zu Definition</div>
    <div class="code-ctx-item" onclick="codeWhoCalls('${esc(word)}')">Wer ruft das auf?</div>`;
  document.body.appendChild(menu);
  // keep the menu on-screen
  const r = menu.getBoundingClientRect();
  if (r.right > window.innerWidth) menu.style.left = (window.innerWidth - r.width - 8) + 'px';
  if (r.bottom > window.innerHeight) menu.style.top = (window.innerHeight - r.height - 8) + 'px';
  setTimeout(() => document.addEventListener('mousedown', _codeIndexCloseMenu, { once: true }), 0);
}

function _codeIndexCloseMenu() {
  const m = document.getElementById('code-ctx-menu');
  if (m) m.remove();
}

async function codeGotoDefinition(word) {
  _codeIndexCloseMenu();
  const d = await _codeIndexFetch(`def=${encodeURIComponent(word)}`);
  if (!d || d.error || !d.file) {
    // fall back to BM25 search → first hit
    const s = await _codeIndexFetch(`q=${encodeURIComponent(word)}&limit=5`);
    const hit = s && s.symbols && s.symbols.find(x => x.name === word) || (s && s.symbols && s.symbols[0]);
    if (hit) { _terminalJumpTo(_codeIndexAbs(hit.file), hit.line); return; }
    if (typeof showToast === 'function') showToast('Keine Definition gefunden');
    return;
  }
  // ?def returns an ABSOLUTE file_path (from get_code_snippet)
  _terminalJumpTo(d.file, d.start_line);
}

async function codeWhoCalls(word) {
  _codeIndexCloseMenu();
  const d = await _codeIndexFetch(`callers=${encodeURIComponent(word)}`);
  const callers = (d && d.callers) || [];
  _codeIndexShowCallers(word, callers);
}

// Reuse the small modal shell the code-index status uses (_codeIndexShowModal),
// falling back to a built-in overlay if it isn't present.
function _codeIndexShowCallers(word, callers) {
  const rows = callers.length
    ? callers.map(c => {
        const qn = esc(c.qualified_name || c.name || '');
        const nm = esc(c.name || '');
        return `<div class="code-callers-row" onclick="codeGotoDefinition('${esc(c.name || '')}')"
                     title="${qn}"><span class="cp-name">${nm}</span>
                <span class="cp-loc">${esc(c.qualified_name || '')}</span></div>`;
      }).join('')
    : '<div class="code-palette-hint">Keine Aufrufer gefunden (oder Symbol wird nur extern aufgerufen).</div>';
  const html = `<div class="code-callers"><div class="code-callers-head">Aufrufer von „${esc(word)}"</div>${rows}</div>`;
  _codeIndexOverlay(html);
}

function _codeIndexOverlay(innerHtml) {
  const ov = document.createElement('div');
  ov.className = 'code-palette-overlay';
  ov.innerHTML = `<div class="code-palette" onclick="event.stopPropagation()">${innerHtml}</div>`;
  ov.addEventListener('click', () => ov.remove());
  document.body.appendChild(ov);
}

// ─── Hover: signature + docstring + caller count ─────────────────────────────
let _codeHoverTimer = null;
let _codeHoverTip = null;

function _codeIndexAttachHover(tab) {
  const wrap = tab.el.querySelector('.editor-cm');
  if (!wrap) return;
  wrap.addEventListener('mousemove', (e) => {
    clearTimeout(_codeHoverTimer);
    _codeHoverTimer = setTimeout(() => _codeIndexHover(tab, e), 450);
  });
  wrap.addEventListener('mouseleave', _codeIndexHideHover);
}

async function _codeIndexHover(tab, e) {
  if (!tab.cm) return;
  const pos = tab.cm.coordsChar({ left: e.clientX, top: e.clientY });
  const word = _wordAt(tab.cm, pos);
  if (!word) { _codeIndexHideHover(); return; }
  const d = await _codeIndexFetch(`def=${encodeURIComponent(word)}`);
  if (!d || d.error || !d.name) { _codeIndexHideHover(); return; }
  _codeIndexHideHover();
  const sig = d.signature ? esc(d.name + d.signature) : esc(d.name);
  const doc = d.docstring ? `<div class="code-hover-doc">${esc(String(d.docstring).replace(/^["']+|["']+$/g, '').slice(0, 280))}</div>` : '';
  const meta = `<div class="code-hover-meta">${esc(d.label || '')}`
    + (typeof d.callers === 'number' ? ` · ${d.callers} Aufrufer` : '')
    + (typeof d.callees === 'number' ? ` · ${d.callees} Aufrufe` : '') + '</div>';
  const tip = document.createElement('div');
  tip.className = 'code-hover-tip';
  tip.innerHTML = `<div class="code-hover-sig">${sig}</div>${meta}${doc}`;
  tip.style.left = Math.min(e.clientX + 12, window.innerWidth - 360) + 'px';
  tip.style.top = (e.clientY + 16) + 'px';
  document.body.appendChild(tip);
  _codeHoverTip = tip;
}

function _codeIndexHideHover() {
  clearTimeout(_codeHoverTimer);
  if (_codeHoverTip) { _codeHoverTip.remove(); _codeHoverTip = null; }
}

// Global Cmd/Ctrl-P → symbol palette, but only in code-mode (and not while a
// text input / the palette itself has focus, so it doesn't hijack normal typing).
document.addEventListener('keydown', (e) => {
  const isP = (e.key === 'p' || e.key === 'P');
  if (!isP || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
  if (typeof terminalAvailable === 'function' && !terminalAvailable()) return;
  const t = e.target;
  if (t && (t.id === 'code-palette-input')) return;
  // allow it to override the browser print dialog in code-mode
  e.preventDefault();
  codeSymbolPalette();
});

// ─── Cypher search bar (power-user) ──────────────────────────────────────────
// Run read-only Cypher over the code index and show {columns, rows}. cbm honours
// explicit property/aggregate projections (RETURN n.name, n.complexity); a bare
// `RETURN n` collapses to the node name only — the examples reflect that.
const _CYPHER_EXAMPLES = [
  { label: 'Komplexeste Methoden', q: "MATCH (n:Method) WHERE n.complexity > 5 RETURN n.name, n.complexity, n.file_path ORDER BY n.complexity DESC LIMIT 20" },
  { label: 'Alle Klassen + Datei', q: "MATCH (n:Class) RETURN n.name, n.file_path" },
  { label: 'Methoden je Datei (Anzahl)', q: "MATCH (n:Method) RETURN n.file_path, count(n) ORDER BY count(n) DESC" },
  { label: 'Funktionen ohne Tests', q: "MATCH (n:Method) WHERE NOT (n)-[:TESTED_BY]->() RETURN n.name, n.file_path LIMIT 30" },
];

function codeCypherBar() {
  if (document.getElementById('code-cypher')) return;
  const ov = document.createElement('div');
  ov.id = 'code-cypher';
  ov.className = 'code-palette-overlay';
  const exHtml = _CYPHER_EXAMPLES.map((e, i) =>
    `<button class="code-cypher-ex" onclick="_codeCypherExample(${i})">${esc(e.label)}</button>`).join('');
  ov.innerHTML = `
    <div class="code-palette code-cypher-box" onclick="event.stopPropagation()">
      <div class="code-cypher-head">Cypher-Abfrage über den Code-Index
        <span class="code-cypher-hint">${_isMac() ? '⌘' : 'Strg'}+Enter zum Ausführen · nur lesend</span></div>
      <textarea id="code-cypher-input" spellcheck="false" autocomplete="off"
        placeholder="MATCH (n:Method) WHERE n.complexity > 5 RETURN n.name, n.complexity ORDER BY n.complexity DESC LIMIT 20"></textarea>
      <div class="code-cypher-examples">${exHtml}</div>
      <div class="code-cypher-actions">
        <button class="btn-primary" onclick="_codeCypherRun()">Ausführen</button>
        <span id="code-cypher-status" class="code-cypher-status"></span>
      </div>
      <div id="code-cypher-results" class="code-cypher-results"></div>
    </div>`;
  ov.addEventListener('click', () => ov.remove());
  document.body.appendChild(ov);
  const ta = document.getElementById('code-cypher-input');
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { ov.remove(); return; }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); _codeCypherRun(); }
  });
  ta.focus();
}

function _codeCypherExample(i) {
  const e = _CYPHER_EXAMPLES[i];
  const ta = document.getElementById('code-cypher-input');
  if (e && ta) { ta.value = e.q; ta.focus(); }
}

async function _codeCypherRun() {
  const ta = document.getElementById('code-cypher-input');
  const status = document.getElementById('code-cypher-status');
  const box = document.getElementById('code-cypher-results');
  if (!ta || !box) return;
  const q = ta.value.trim();
  if (!q) { return; }
  if (status) status.textContent = 'Läuft …';
  box.innerHTML = '';
  const d = await _codeIndexFetch(`cypher=${encodeURIComponent(q)}`);
  if (!d || d.error) {
    if (status) status.textContent = '';
    box.innerHTML = `<div class="code-palette-hint code-cypher-err">${esc((d && d.error) || 'Abfrage fehlgeschlagen')}</div>`;
    return;
  }
  const cols = d.columns || [];
  const rows = d.rows || [];
  if (status) status.textContent = `${rows.length} Zeile${rows.length === 1 ? '' : 'n'}`;
  if (!rows.length) { box.innerHTML = '<div class="code-palette-hint">Keine Treffer.</div>'; return; }
  // a cell that looks like a repo-relative source path becomes a jump link
  const head = cols.map(c => `<th>${esc(String(c))}</th>`).join('');
  const body = rows.map(r => {
    const cells = (Array.isArray(r) ? r : [r]).map(v => {
      const s = v === null || v === undefined ? '' : String(v);
      if (/\.[a-z]{1,4}$/i.test(s) && /[\/\\]/.test(s) && !/\s/.test(s)) {
        return `<td><a class="code-cypher-link" onclick="_codeCypherJump('${esc(s)}')">${esc(s)}</a></td>`;
      }
      return `<td>${esc(s)}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  box.innerHTML = `<table class="code-cypher-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// Click a path cell → open that file in the editor (no line info in a generic
// Cypher result, so just open at the top).
function _codeCypherJump(relPath) {
  const ov = document.getElementById('code-cypher');
  if (ov) ov.remove();
  _terminalJumpTo(_codeIndexAbs(relPath), 1);
}
