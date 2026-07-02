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
      <button class="tpane-scroll tpane-scroll-l" data-scroll="l" title="Tabs nach links" style="display:none">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
      </button>
      <div class="tpane-tabs" data-pane="pane-${slot}"></div>
      <button class="tpane-scroll tpane-scroll-r" data-scroll="r" title="Tabs nach rechts" style="display:none">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
      </button>
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
  // Tab overflow scroll arrows (VS Code-style): only shown when the tab strip
  // overflows; click scrolls by ~70% of the visible width. Visibility is kept in
  // sync on scroll/resize + after every tab render (_terminalUpdateTabScroll).
  const tabsEl = pane.querySelector('.tpane-tabs');
  pane.querySelectorAll('.tpane-scroll').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const dx = Math.max(80, Math.round(tabsEl.clientWidth * 0.7));
      tabsEl.scrollBy({ left: btn.dataset.scroll === 'l' ? -dx : dx, behavior: 'smooth' });
    };
  });
  tabsEl.addEventListener('scroll', () => _terminalUpdateTabScroll(pane));
  // drag-drop onto the BODY: edge zones split the grid toward that direction; the
  // centre is a plain move into this cell. The overlay highlights the target zone.
  _terminalWirePaneDrop(pane, slot);
  return pane;
}

// Show/hide the tab-scroll arrows for a pane based on overflow, and disable the
// left/right arrow at the respective scroll extreme. Cheap; safe to over-call.
function _terminalUpdateTabScroll(pane) {
  if (!pane) return;
  const tabsEl = pane.querySelector ? pane.querySelector('.tpane-tabs')
    : (pane.paneEl && pane.paneEl.querySelector('.tpane-tabs'));
  const root = pane.querySelector ? pane : pane.paneEl;
  if (!tabsEl || !root) return;
  const l = root.querySelector('.tpane-scroll-l');
  const r = root.querySelector('.tpane-scroll-r');
  const overflow = tabsEl.scrollWidth - tabsEl.clientWidth > 2;
  const atL = tabsEl.scrollLeft <= 1;
  const atR = tabsEl.scrollLeft >= (tabsEl.scrollWidth - tabsEl.clientWidth - 1);
  if (l) { l.style.display = overflow ? '' : 'none'; l.disabled = atL; l.classList.toggle('tpane-scroll-off', atL); }
  if (r) { r.style.display = overflow ? '' : 'none'; r.disabled = atR; r.classList.toggle('tpane-scroll-off', atR); }
}

// Refresh tab-scroll arrows for ALL panes (after a render / resize).
function _terminalUpdateAllTabScroll() {
  for (const p of (_term.panes || [])) {
    if (p.paneEl) _terminalUpdateTabScroll(p.paneEl);
  }
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

// Snapshot the view the terminal is being opened FROM, so we can detect later when
// that exact view is no longer shown. Two origin kinds:
//   • 'project' — opened from the project-detail screen of a code-mode project →
//     close once that project is no longer shown in the project-detail view.
//   • 'chat'    — opened from a code-mode project chat (bound to that chat's
//     sessionId) → close once that specific chat is no longer visible.
// The chat id is `sessionId` (NOT `.id`, which doesn't exist on the chat object).
function _terminalCaptureOrigin() {
  if (state.currentView === 'project-detail' && state._projectDetail
      && state._projectDetail.code_mode) {
    _term.originKind = 'project';
    _term.originProject = state._projectDetailName || '';
    _term.originChatId = '';
  } else {
    _term.originKind = 'chat';
    _term.originChatId = (state.activeChat && state.activeChat.sessionId) || '';
    _term.originProject = (state.activeChat && state.activeChat.project)
                          || state.currentProject || '';
  }
}

// Is the originating view still the one currently shown? A project-origin requires
// the project-detail view of the SAME project; a chat-origin requires the chat view
// with the SAME active chat session. Anything else (welcome, list views, a different
// project, a different/other chat) means the origin is no longer visible.
function _terminalOriginStillShown() {
  if (_term.originKind === 'project') {
    return state.currentView === 'project-detail'
        && (state._projectDetailName || '') === _term.originProject;
  }
  if (_term.originKind === 'chat') {
    return state.currentView === 'chat'
        && !!_term.originChatId
        && (state.activeChat && state.activeChat.sessionId) === _term.originChatId;
  }
  return false;
}

// Show/hide the status-bar terminal toggle based on code-mode context. Also
// auto-closes the panel the moment the view it was launched from is no longer
// shown — the originating project-detail screen or the specific originating chat.
// Bound to the ORIGIN (not just the project), so opening another chat in the same
// project, or any list/welcome view, closes it too.
function terminalRefreshToggle() {
  const btn = document.getElementById('terminal-toggle-btn');
  const avail = terminalAvailable();
  if (btn) btn.classList.toggle('code-mode-available', !!avail);
  if (!_term.open) return;
  if (!_terminalOriginStillShown()) terminalTogglePanel(false);
}

async function terminalTogglePanel(force) {
  const panel = document.getElementById('terminal-panel');
  if (!panel) return;
  const want = (typeof force === 'boolean') ? force : !_term.open;
  if (want) {
    const ctx = await _terminalCtx();
    if (!ctx) { if (typeof showToast === 'function') showToast('Terminal nur in Code-Mode-Projekten'); return; }
    // Bind to the ORIGINATING view so we can auto-close once it's no longer shown
    // (project-detail screen, or a specific chat session). Captured every open/
    // re-show so re-opening from a different view rebinds.
    _terminalCaptureOrigin();
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
    _codeOutline.loaded = false;             // reset for the (possibly new) project
    if (typeof codeOutlineLoad === 'function') codeOutlineLoad();
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

// xterm needs a LITERAL font-family string — it does not resolve CSS `var()`
// (passing 'var(--font-mono…)' silently fell back to a default, hurting
// readability). We build a terminal-grade stack of each OS's DEFAULT terminal
// font, all of which carry full box-drawing / block-shade / Powerline coverage so
// TUIs (htop/btop/vim) render crisply:
//   macOS Terminal.app → SF Mono, classic default Menlo
//   Windows 11 Terminal → Cascadia Mono (Code = ligature variant); old Consolas
//   Linux → DejaVu Sans Mono
// `ui-monospace` maps to the OS default; literal monospace is the last resort.
// NOTE: the app's --font-mono ("Anthropic Mono") is intentionally NOT first — it
// lacks box-drawing glyphs, which is what garbled the monitor TUI.
const _TERM_FONT_STACK = '"SF Mono", "Menlo", "Cascadia Mono", "Cascadia Code", '
  + '"Consolas", "DejaVu Sans Mono", "Liberation Mono", ui-monospace, monospace';
function _terminalFontFamily() { return _TERM_FONT_STACK; }

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
    fontSize: 13, fontFamily: _terminalFontFamily(),
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
    // A fresh code chat routes via 'auto' (server picks the model per turn) —
    // show that, not a misleading "Standard". Once a turn resolves, the status
    // line shows auto→<model> (tab._autoPicked) / the concrete model.
    model: 'auto', thinking: 'none', caveman: 0, showTools: true,
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
    setTimeout(() => { try { const ta = tab.el.querySelector('.tc-ta'); if (ta) ta.focus({ preventScroll: true }); } catch (_) {} }, 30);
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
          // base64 → raw bytes. Write a Uint8Array (NOT the binary string): xterm
          // decodes bytes as UTF-8, so multi-byte glyphs (box-drawing █ ─ │, block
          // shades, powerline, emoji) render correctly. Passing the binary string
          // would make xterm read each byte as a Latin-1 code point → 0xE2 shows as
          // "â", the mojibake that garbled TUIs like htop/btop. offset stays the
          // BYTE count (resume `since=` is byte-based server-side).
          try {
            const bin = atob(data);
            const u8 = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
            tab.term.write(u8);
            tab.offset += u8.length;
          } catch (_) {}
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
  // Rebuild from occupancy so an emptied cell collapses and neighbours reclaim its
  // space (the dynamic-grid contract). When the LAST tab is closed this leaves a
  // single EMPTY pane (its bar keeps the +/◈ new-terminal/new-chat buttons) — the
  // bottom area stays OPEN. (Closing the last tab used to auto-close the whole
  // panel, which read as a bug; the panel only closes via its own ✕/toggle now.)
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
  const _t = (_term.tabs || []).find(x => x.id === id);
  // Chat tabs export their transcript (panels_termchat); shell terminals export
  // their xterm scrollback. Editors already have a download button.
  let exportRow = '';
  if (_t && _t.kind === 'chat') {
    exportRow = `<div onclick="tcExportMarkdown('${id}'); document.getElementById('terminal-tab-menu').remove()">Als Markdown exportieren</div>`;
  } else if (_t && _t.kind === 'terminal') {
    exportRow = `<div onclick="terminalExportMarkdown('${id}'); document.getElementById('terminal-tab-menu').remove()">Als Markdown exportieren</div>`;
  }
  m.innerHTML = `${exportRow}
    <div onclick="terminalCloseTab('${id}'); document.getElementById('terminal-tab-menu').remove()">Tab schließen</div>
    <div onclick="terminalCloseTabs('others','${id}'); document.getElementById('terminal-tab-menu').remove()">Andere schließen</div>
    <div onclick="terminalCloseTabs('right','${id}'); document.getElementById('terminal-tab-menu').remove()">Rechte schließen</div>
    <div onclick="terminalCloseTabs('all'); document.getElementById('terminal-tab-menu').remove()">Alle schließen</div>`;
  document.body.appendChild(m);
  const close = () => { const e = document.getElementById('terminal-tab-menu'); if (e) e.remove(); document.removeEventListener('click', close); };
  setTimeout(() => document.addEventListener('click', close), 0);
}

// Export a shell terminal's xterm scrollback as a Markdown code block, direct
// browser download (no server, no artifact write). Mirrors tcExportMarkdown for
// chat tabs.
function terminalExportMarkdown(id) {
  const tab = (_term.tabs || []).find(t => t.id === id && t.kind === 'terminal');
  if (!tab || !tab.term) return;
  const buf = tab.term.buffer && tab.term.buffer.active;
  if (!buf) return;
  const rows = [];
  // Walk the full scrollback (length includes off-screen lines); trim trailing
  // blank lines so the export isn't padded out to the scrollback cap.
  for (let i = 0; i < buf.length; i++) {
    const line = buf.getLine(i);
    rows.push(line ? line.translateToString(true) : '');
  }
  while (rows.length && !rows[rows.length - 1].trim()) rows.pop();
  const md = ['# Terminal', '', '```', rows.join('\n'), '```', ''].join('\n');
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const fname = `terminal_${ts}.md`;
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  if (typeof _saveBlobAs === 'function') _saveBlobAs(blob, fname);
  else if (typeof showToast === 'function') showToast('Export nicht verfügbar');
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
  // Tab count/width may have changed → refresh the overflow scroll arrows, and
  // keep the active tab of each pane scrolled into view.
  if (typeof _terminalUpdateAllTabScroll === 'function') {
    requestAnimationFrame(() => {
      _terminalUpdateAllTabScroll();
      for (const p of (_term.panes || [])) {
        const bar = p.barEl;
        if (!bar) continue;
        const act = bar.querySelector('.terminal-tab.active');
        if (act && act.scrollIntoView) { try { act.scrollIntoView({ inline: 'nearest', block: 'nearest' }); } catch (_) {} }
      }
    });
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
  // xlsx grid tabs have no text content — refresh the grid payload instead.
  if (tab.gridOnly) {
    try {
      const g = await API.get(`/v1/files/xlsx-grid?path=${encodeURIComponent(tab.path)}`);
      if (g && !g.error) {
        tab.grid = g;
        tab.size = g.size || tab.size;
        tab.mtime = g.mtime || tab.mtime;
        _terminalEditorShowGrid(tab, tab.gridSheet || 0);
        _terminalEditorStats(tab);
      }
    } catch (_) { /* transient */ }
    return;
  }
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
    // Include size+mtime so the date/size sort modes repaint when a file's
    // content changes (not just on structural add/remove).
    acc.push((n.type === 'dir' ? 'd:' : 'f:') + n.path + (n.type === 'dir' ? '' : `:${n.size || 0}:${n.mtime || 0}`));
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
  // Diff vs the persisted snapshot → new/modified badges (poll = work-time edits).
  if (typeof _wdComputeChanges === 'function') { try { _wdComputeChanges(data.tree); } catch (_) {} }
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
    json: { name: 'javascript', json: true }, jsonl: { name: 'javascript', json: true },
    geojson: { name: 'javascript', json: true },
    ts: 'javascript', jsx: 'javascript', tsx: 'javascript',
    html: 'htmlmixed', htm: 'htmlmixed', xml: 'xml', svg: 'xml',
    css: 'css', scss: 'css', less: 'css',
    md: 'markdown', markdown: 'markdown',
    c: 'text/x-csrc', h: 'text/x-csrc', cpp: 'text/x-c++src', cc: 'text/x-c++src',
    hpp: 'text/x-c++src', java: 'text/x-java', cs: 'text/x-csharp',
    yml: 'yaml', yaml: 'yaml', sh: 'shell', bash: 'shell', zsh: 'shell',
    go: 'go', rs: 'rust',
    r: 'text/x-rsrc',
    sql: 'text/x-sql',
    // ShowCase .dbq files are XML wrappers around SQL — the editor shows the
    // raw file (XML), so highlight as XML, not SQL.
    dbq: 'xml',
  };
  return m[ext] || null;
}

// CodeMirror fold helper kind for an extension → 'xml' | 'brace' | null. Drives
// the collapse arrows in the editor gutter (tree-like collapse/expand): XML-ish
// files fold by element, JSON-ish by {}/[] braces. Everything else: no fold.
function _cmFoldKindFor(ext) {
  ext = (ext || '').toLowerCase();
  if (ext === 'xml' || ext === 'svg' || ext === 'dbq' || ext === 'html' || ext === 'htm') return 'xml';
  if (ext === 'json' || ext === 'jsonl' || ext === 'geojson') return 'brace';
  return null;
}
// Is this a file type we offer a structured tree VIEW for (Ansicht mode)?
function _terminalHasTreeView(ext) {
  ext = (ext || '').toLowerCase();
  return ext === 'json' || ext === 'jsonl' || ext === 'geojson' || ext === 'xml';
}
// Build the CodeMirror fold rangeFinder for a kind. 'xml' uses the xml-fold
// helper, 'brace' the brace-fold helper; both fall back to indent-fold so a
// pretty-printed-but-unusual file still folds. Guards against an addon that
// failed to load (returns null → foldGutter simply shows no arrows).
function _cmFoldFinder(kind) {
  if (!window.CodeMirror || !CodeMirror.fold) return null;
  const f = CodeMirror.fold;
  const parts = [];
  if (kind === 'xml' && f.xml) parts.push(f.xml);
  if (kind === 'brace' && f.brace) parts.push(f.brace);
  if (f.indent) parts.push(f.indent);
  if (!parts.length) return null;
  return parts.length === 1 ? parts[0] : CodeMirror.fold.auto || f.combine(...parts);
}
// Enable/disable the fold gutter on an EXISTING CodeMirror instance (used when a
// .dbq tab switches between its foldable XML view and non-foldable SQL view).
function _terminalSetFold(tab, kind) {
  if (!tab || !tab.cm) return;
  const cm = tab.cm;
  const finder = kind ? _cmFoldFinder(kind) : null;
  const gutters = ['CodeMirror-linenumbers'];
  if (finder) gutters.push('CodeMirror-foldgutter');
  cm.setOption('gutters', gutters);
  cm.setOption('foldGutter', finder ? { rangeFinder: finder } : false);
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
  // xlsx/xlsm → structured grid preview (no text content to edit; the grid
  // endpoint reuses the agent's xlsx-toolset loader, so the preview shows
  // exactly what the tools see: header detection, multi-table split, merged
  // headers). Editing binary workbooks stays with the agent / open-external.
  if (ext === 'xlsx' || ext === 'xlsm') {
    tab.gridOnly = true;
    el.querySelector('.editor-cm').style.display = 'none';
    el.querySelector('.ed-mode[data-mode="raw"]').style.display = 'none';
    el.querySelector('.ed-save').style.display = 'none';
    try {
      const g = await API.get(`/v1/files/xlsx-grid?path=${encodeURIComponent(absPath)}`);
      const renderEl = el.querySelector('.editor-render');
      renderEl.style.display = 'block';
      if (g.error) { renderEl.innerHTML = `<div class="pt-empty">${esc(g.error)}</div>`; return; }
      tab.grid = g;
      tab.size = g.size || 0;
      tab.mtime = g.mtime || 0;
      tab.loaded = true;
      _terminalEditorShowGrid(tab, 0);
      _terminalEditorStats(tab);
    } catch (e) {
      el.querySelector('.editor-render').innerHTML = '<div class="pt-empty">Tabellen-Vorschau fehlgeschlagen.</div>';
    }
    return;
  }
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
    if (_isDbq(tab)) _terminalDbqRelabelModes(tab);
    else if (_terminalHasTreeView(tab.ext)) _terminalTreeRelabelModes(tab);
    _terminalEditorPaint(tab);
    _terminalEditorStats(tab);
  } catch (e) {
    el.querySelector('.editor-render').innerHTML = '<div class="pt-empty">Datei konnte nicht geladen werden.</div>';
  }
}

function terminalEditorMode(id, mode) {
  const tab = _term.tabs.find(t => t.id === id);
  if (!tab) return;
  // .dbq: before switching views, fold the CURRENT view's edits back into
  // tab.raw (the XML) so the other view shows them. SQL view → re-embed into
  // <DisplaySQL>/<Body>; XML view → it already IS tab.raw.
  // (the tree view is read-only → never fold its CM, which still holds the
  // previous editable view's text).
  if (_isDbq(tab) && tab.cm && tab.mode !== mode && tab.mode !== 'tree') {
    const cur = tab.cm.getValue();
    if (tab.mode === 'render') {            // leaving the SQL view
      const merged = _dbqEmbedSql(tab.raw, cur);
      if (merged !== null) tab.raw = merged;
    } else if (tab.mode === 'raw') {        // leaving the XML source view
      tab.raw = cur;
    }
  }
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
const _ED_RENDERABLE = { html: 1, htm: 1, md: 1, markdown: 1, svg: 1,
                         json: 1, jsonl: 1, geojson: 1, xml: 1,
                         csv: 1, tsv: 1 };
function _terminalIsRenderable(ext) { return !!_ED_RENDERABLE[(ext || '').toLowerCase()]; }

// ── ShowCase .dbq handling ───────────────────────────────────────────────────
// A .dbq is an XML wrapper around SQL. The editor offers TWO editable views,
// toggled by the Ansicht/Bearbeiten buttons (relabelled "SQL"/"XML-Quelle"):
//   • 'render' mode → just the extracted SQL (SQL highlighting)
//   • 'raw'    mode → the full raw XML (XML highlighting)
// tab.raw is the source of truth (always the XML). Saving in SQL mode splices
// the edited SQL back into <DisplaySQL>/<Body>; saving in XML mode writes verbatim.
function _dbqDecodeEntities(s) {
  const t = document.createElement('textarea');
  t.innerHTML = s;             // decode &lt; &amp; &quot; … via the DOM
  return t.value;
}
function _dbqEncodeEntities(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
// Pull the SQL out of <DisplaySQL> (preferred — formatted, always present) or
// <Body>. Mirrors the server-side _extract_dbq_sql so the SQL view matches
// what gets indexed.
function _dbqExtractSql(xml) {
  const m = /<DisplaySQL>([\s\S]*?)<\/DisplaySQL>/.exec(xml)
         || /<Body>([\s\S]*?)<\/Body>/.exec(xml);
  return m ? _dbqDecodeEntities(m[1]).trim() : '';
}
// Re-embed edited SQL into the XML: replace the inner text of <DisplaySQL> AND
// <Body> (both, so the wire query Showcase actually runs stays in sync) with the
// entity-encoded SQL. Returns the updated XML (or null if no tag was found).
function _dbqEmbedSql(xml, sql) {
  const enc = _dbqEncodeEntities(sql);
  let hit = false;
  let out = xml.replace(/(<DisplaySQL>)[\s\S]*?(<\/DisplaySQL>)/,
                        (_m, a, b) => { hit = true; return a + enc + b; });
  out = out.replace(/(<Body>)[\s\S]*?(<\/Body>)/,
                    (_m, a, b) => { hit = true; return a + enc + b; });
  return hit ? out : null;
}
function _isDbq(tab) { return (tab.ext || '').toLowerCase() === 'dbq'; }
// Relabel the two mode buttons for a .dbq tab: 'render' = "SQL (extrahiert)",
// 'raw' = "XML-Quelle". Both are editable for .dbq (unlike html/md/svg, where
// 'render' is a read-only preview), so the eye icon would mislead → swap it for
// a database icon on the SQL button.
function _terminalDbqRelabelModes(tab) {
  if (!tab || !tab.el) return;
  const sqlBtn = tab.el.querySelector('.ed-mode[data-mode="render"]');
  const xmlBtn = tab.el.querySelector('.ed-mode[data-mode="raw"]');
  if (sqlBtn) {
    sqlBtn.title = 'SQL (extrahiert) — bearbeitbar';
    sqlBtn.setAttribute('aria-label', 'SQL');
    sqlBtn.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"/></svg>';
  }
  if (xmlBtn) {
    xmlBtn.title = 'XML-Quelle — bearbeitbar';
    xmlBtn.setAttribute('aria-label', 'XML-Quelle');
    xmlBtn.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
    // Add a THIRD .dbq view: the structured XML tree (read-only, collapsible) —
    // right after the XML-Quelle button. Inserted once (guard on existing node).
    if (!tab.el.querySelector('.ed-mode[data-mode="tree"]')) {
      const treeBtn = document.createElement('button');
      treeBtn.className = 'ed-iconbtn ed-mode';
      treeBtn.dataset.mode = 'tree';
      treeBtn.setAttribute('onclick', `terminalEditorMode('${tab.id}','tree')`);
      treeBtn.title = 'XML-Baum (auf-/zuklappen)';
      treeBtn.setAttribute('aria-label', 'XML-Baum');
      treeBtn.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="9" y1="6" x2="20" y2="6"/><line x1="12" y1="12" x2="20" y2="12"/><line x1="12" y1="18" x2="20" y2="18"/><path d="M4 5v4a2 2 0 0 0 2 2h2"/><path d="M6 11v4a2 2 0 0 0 2 2h2"/></svg>';
      xmlBtn.insertAdjacentElement('afterend', treeBtn);
    }
  }
}
// JSON/XML: 'render' = collapsible data tree, 'raw' = editable source (with fold
// arrows). Relabel the eye → "Baum-Ansicht" (tree icon) so the toggle reads right.
function _terminalTreeRelabelModes(tab) {
  if (!tab || !tab.el) return;
  const treeBtn = tab.el.querySelector('.ed-mode[data-mode="render"]');
  const srcBtn = tab.el.querySelector('.ed-mode[data-mode="raw"]');
  if (treeBtn) {
    treeBtn.title = 'Baum-Ansicht (auf-/zuklappen)';
    treeBtn.setAttribute('aria-label', 'Baum-Ansicht');
    treeBtn.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="9" y1="6" x2="20" y2="6"/><line x1="12" y1="12" x2="20" y2="12"/><line x1="12" y1="18" x2="20" y2="18"/><path d="M4 5v4a2 2 0 0 0 2 2h2"/><path d="M6 11v4a2 2 0 0 0 2 2h2"/></svg>';
  }
  if (srcBtn) {
    srcBtn.title = 'Quelltext bearbeiten (mit Aufklapp-Pfeilen)';
    srcBtn.setAttribute('aria-label', 'Quelltext');
  }
}

// Paint a collapsible data tree from `txt` into `renderEl`. treeKind: 'xml' →
// DOM-parse; otherwise a JSON variant ('json'/'jsonl'/'geojson'). Shared by the
// JSON/XML Ansicht and the .dbq XML-tree view. Read-only; errors fall back to a
// hint pointing at the source/edit view.
function _terminalPaintTree(renderEl, txt, treeKind) {
  renderEl.innerHTML = '';
  const host = document.createElement('div');
  host.className = 'editor-tree';
  let rootNode;
  try {
    rootNode = (treeKind === 'xml') ? _treeFromXml(txt) : _treeFromJson(txt, treeKind);
  } catch (e) {
    host.innerHTML = `<div class="pt-empty">Konnte ${String(treeKind).toUpperCase()} nicht als Baum darstellen: ${esc(String(e.message || e))}<br><span style="opacity:.7">Nutzen Sie die Quelltext-Ansicht für die Rohdaten.</span></div>`;
    renderEl.appendChild(host);
    return;
  }
  host.appendChild(_treeRenderNode(rootNode, true));
  const bar = document.createElement('div');
  bar.className = 'editor-tree-bar';
  bar.innerHTML = '<button class="ed-tree-btn" data-act="expand">Alles aufklappen</button>'
                + '<button class="ed-tree-btn" data-act="collapse">Alles zuklappen</button>';
  bar.querySelector('[data-act="expand"]').onclick = () =>
    host.querySelectorAll('.etree-node.collapsible').forEach(n => n.classList.remove('etree-collapsed'));
  bar.querySelector('[data-act="collapse"]').onclick = () =>
    host.querySelectorAll('.etree-node.collapsible').forEach(n => n.classList.add('etree-collapsed'));
  renderEl.appendChild(bar);
  renderEl.appendChild(host);
}

// ── Spreadsheet grid preview (xlsx/xlsm/csv/tsv, v9.263.0) ───────────────────
// Shared table renderer for the /v1/files/xlsx-grid payload — used by the
// bottom-panel editor (here) and the artifacts fullview (panels_artifacts.js).
function _xlsxGridHtml(grid, activeIdx) {
  const sheets = (grid && grid.sheets) || [];
  if (!sheets.length) return '<div class="pt-empty">Keine Tabellendaten gefunden.</div>';
  const idx = Math.min(activeIdx || 0, sheets.length - 1);
  const s = sheets[idx];
  const tabsHtml = sheets.length > 1
    ? '<div class="xgrid-tabs">' + sheets.map((sh, i) =>
        `<button class="xgrid-sheet-btn${i === idx ? ' active' : ''}" data-idx="${i}">${esc(sh.name)}</button>`).join('')
      + '</div>'
    : '';
  const head = '<tr><th class="xgrid-rownum">#</th>'
    + s.header.map(h => `<th>${esc(String(h))}</th>`).join('') + '</tr>';
  const body = s.rows.map((r, ri) => {
    const cells = s.header.map((_, ci) => {
      const v = r[ci];
      const cls = (typeof v === 'number') ? ' class="xgrid-num"' : '';
      return `<td${cls}>${v === null || v === undefined ? '' : esc(String(v))}</td>`;
    }).join('');
    return `<tr><td class="xgrid-rownum">${ri + 1}</td>${cells}</tr>`;
  }).join('');
  const note = s.truncated
    ? `<div class="xgrid-note">Vorschau: ${s.rows.length} von ${s.total_rows} Zeilen</div>` : '';
  return `${tabsHtml}<div class="xgrid-wrap"><table class="xgrid-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>${note}`;
}

function _terminalEditorShowGrid(tab, sheetIdx) {
  const renderEl = tab.el.querySelector('.editor-render');
  if (!renderEl || !tab.grid) return;
  tab.gridSheet = sheetIdx || 0;
  renderEl.style.display = 'block';
  renderEl.innerHTML = _xlsxGridHtml(tab.grid, tab.gridSheet);
  renderEl.querySelectorAll('.xgrid-sheet-btn').forEach(b => {
    b.onclick = () => _terminalEditorShowGrid(tab, parseInt(b.dataset.idx, 10));
  });
}

// Render a renderable file's content into the .editor-render pane (Ansicht mode).
function _terminalEditorRender(tab) {
  const renderEl = tab.el.querySelector('.editor-render');
  if (!renderEl) return;
  const txt = (tab.cm && tab.dirty) ? tab.cm.getValue() : tab.raw;
  const ext = (tab.ext || '').toLowerCase();
  if (ext === 'csv' || ext === 'tsv') {
    // CSV/TSV Ansicht = the same server-parsed grid the xlsx preview uses
    // (proper delimiter sniffing + typing). Reflects the SAVED file — after
    // edits, save first, then the view re-fetches.
    renderEl.innerHTML = '<div class="pt-empty">Lade Tabelle…</div>';
    API.get(`/v1/files/xlsx-grid?path=${encodeURIComponent(tab.path)}`).then(g => {
      if (g.error) { renderEl.innerHTML = `<div class="pt-empty">${esc(g.error)}</div>`; return; }
      tab.grid = g;
      _terminalEditorShowGrid(tab, tab.gridSheet || 0);
      if (tab.dirty) {
        const note = document.createElement('div');
        note.className = 'xgrid-note';
        note.textContent = 'Ansicht zeigt den gespeicherten Stand — ungespeicherte Änderungen unter „Bearbeiten".';
        renderEl.appendChild(note);
      }
    }).catch(() => { renderEl.innerHTML = '<div class="pt-empty">Tabellen-Vorschau fehlgeschlagen.</div>'; });
    return;
  }
  if (_terminalHasTreeView(ext)) {
    // JSON / XML → a collapsible data tree (read-only). Editing happens in the
    // 'Bearbeiten' view (CodeMirror source, with fold arrows in the gutter).
    _terminalPaintTree(renderEl, txt, (ext === 'xml') ? 'xml' : ext);
    return;
  }
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

// ── JSON/XML data-tree viewer (Ansicht mode) ─────────────────────────────────
// A normalized node = { kind:'object'|'array'|'leaf', key, value, children[] }.
// Built from parsed JSON or a DOM-parsed XML document, then rendered as nested
// collapsible rows (containers get a click-toggle caret; leaves show key:value).

function _treeFromJson(txt, ext) {
  let data;
  if (ext === 'jsonl') {
    // JSON Lines → an array of the per-line records (skip blank lines).
    const recs = [];
    txt.split('\n').forEach((ln, i) => {
      const s = ln.trim();
      if (!s) return;
      try { recs.push(JSON.parse(s)); }
      catch (e) { throw new Error(`Zeile ${i + 1}: ${e.message}`); }
    });
    data = recs;
  } else {
    data = JSON.parse(txt);
  }
  return _jsonToNode(undefined, data);
}
function _jsonToNode(key, val) {
  if (Array.isArray(val)) {
    return { kind: 'array', key, value: val, count: val.length,
             children: val.map((v, i) => _jsonToNode(i, v)) };
  }
  if (val && typeof val === 'object') {
    const keys = Object.keys(val);
    return { kind: 'object', key, value: val, count: keys.length,
             children: keys.map(k => _jsonToNode(k, val[k])) };
  }
  return { kind: 'leaf', key, value: val };
}

function _treeFromXml(txt) {
  const doc = new DOMParser().parseFromString(txt, 'application/xml');
  const err = doc.querySelector('parsererror');
  if (err) throw new Error((err.textContent || 'XML-Parsefehler').split('\n')[0]);
  const root = doc.documentElement;
  if (!root) throw new Error('kein Wurzelelement');
  return _xmlToNode(root);
}
function _xmlToNode(el) {
  const attrs = Array.from(el.attributes || []).map(a => ({ name: a.name, value: a.value }));
  // element children (ignore whitespace-only text); collect significant text
  const elems = Array.from(el.children || []);
  const ownText = Array.from(el.childNodes || [])
    .filter(n => n.nodeType === 3).map(n => n.nodeValue.trim()).filter(Boolean).join(' ');
  if (!elems.length) {
    // leaf element: tag = text (+ attributes shown inline)
    return { kind: 'leaf', key: el.tagName, value: ownText, attrs };
  }
  return { kind: 'object', key: el.tagName, value: null, attrs,
           count: elems.length, text: ownText,
           children: elems.map(_xmlToNode) };
}

// Render one normalized node as a collapsible row (with its subtree).
function _treeRenderNode(node, isRoot) {
  const wrap = document.createElement('div');
  const isContainer = node.kind === 'object' || node.kind === 'array';
  wrap.className = 'etree-node' + (isContainer ? ' collapsible' : '');
  if (isContainer && !isRoot && (node.count || 0) > 30) wrap.classList.add('etree-collapsed');

  const row = document.createElement('div');
  row.className = 'etree-row';
  // caret (containers only)
  if (isContainer) {
    const car = document.createElement('span');
    car.className = 'etree-caret';
    car.textContent = '▾';
    car.onclick = () => wrap.classList.toggle('etree-collapsed');
    row.appendChild(car);
  } else {
    const sp = document.createElement('span'); sp.className = 'etree-caret etree-caret-leaf';
    row.appendChild(sp);
  }
  // key
  if (node.key !== undefined && node.key !== null && node.key !== '') {
    const k = document.createElement('span'); k.className = 'etree-key';
    k.textContent = String(node.key); row.appendChild(k);
    const c = document.createElement('span'); c.className = 'etree-colon'; c.textContent = ': ';
    row.appendChild(c);
  }
  // XML attributes (compact, after the key)
  if (node.attrs && node.attrs.length) {
    const a = document.createElement('span'); a.className = 'etree-attrs';
    a.textContent = node.attrs.map(x => `${x.name}="${x.value}"`).join(' ');
    row.appendChild(a);
  }
  if (isContainer) {
    const badge = document.createElement('span'); badge.className = 'etree-count';
    badge.textContent = node.kind === 'array' ? `[${node.count}]` : `{${node.count}}`;
    row.appendChild(badge);
    if (node.text) {  // XML element with both text and children
      const t = document.createElement('span'); t.className = 'etree-val etree-val-str';
      t.textContent = ' ' + node.text; row.appendChild(t);
    }
  } else {
    const v = document.createElement('span');
    const val = node.value;
    const cls = (val === null || val === undefined) ? 'etree-val-null'
      : (typeof val === 'number') ? 'etree-val-num'
      : (typeof val === 'boolean') ? 'etree-val-bool' : 'etree-val-str';
    v.className = 'etree-val ' + cls;
    v.textContent = (val === null || val === undefined) ? 'null'
      : (typeof val === 'string') ? (node.attrs ? (val || '') : JSON.stringify(val)) : String(val);
    row.appendChild(v);
  }
  wrap.appendChild(row);

  if (isContainer) {
    const kids = document.createElement('div');
    kids.className = 'etree-children';
    node.children.forEach(ch => kids.appendChild(_treeRenderNode(ch, false)));
    wrap.appendChild(kids);
  }
  return wrap;
}

function _terminalEditorPaint(tab) {
  const cmEl = tab.el.querySelector('.editor-cm');
  const renderEl = tab.el.querySelector('.editor-render');
  // .dbq XML-tree view → render tab.raw (the XML) as a collapsible tree.
  if (tab.mode === 'tree' && _isDbq(tab)) {
    cmEl.style.display = 'none';
    if (renderEl) { renderEl.style.display = 'block'; _terminalPaintTree(renderEl, tab.raw, 'xml'); }
    return;
  }
  // Renderable file in Ansicht mode → show the rendered output, hide CM.
  if (tab.mode === 'render' && _terminalIsRenderable(tab.ext)) {
    cmEl.style.display = 'none';
    if (renderEl) renderEl.style.display = 'block';
    _terminalEditorRender(tab);
    return;
  }
  if (renderEl) renderEl.style.display = 'none';
  cmEl.style.display = 'block';
  // .dbq: the CM content + language depend on the mode — SQL view (render)
  // shows the extracted query, XML view (raw) the whole wrapper. Both editable.
  const _dbq = _isDbq(tab);
  const _dbqWantSql = _dbq && tab.mode === 'render';
  const _initVal = _dbq ? (_dbqWantSql ? _dbqExtractSql(tab.raw) : tab.raw) : tab.raw;
  const _initMode = _dbq ? (_dbqWantSql ? 'text/x-sql' : 'xml') : _cmModeFor(tab.ext);
  // Wrap long lines for SQL / ShowCase content — these queries are often a
  // single very long line and would otherwise scroll far off-screen. Normal
  // code keeps wrapping off (editor convention).
  const _wrap = _dbq || (tab.ext || '').toLowerCase() === 'sql';
  // Code folding (tree-like collapse/expand in the gutter). For .dbq only the
  // XML view folds (the extracted-SQL view has no nesting). Mode string carries
  // the fold helper kind via `foldOptions`.
  const _foldKind = (_dbq && tab.mode === 'render') ? null : _cmFoldKindFor(tab.ext);
  if (!tab.cm) {
    const _gutters = ['CodeMirror-linenumbers'];
    if (_foldKind) _gutters.push('CodeMirror-foldgutter');
    tab.cm = CodeMirror(cmEl, {
      value: _initVal, mode: _initMode, lineNumbers: true,
      lineWrapping: _wrap, indentUnit: 4,
      readOnly: _dbq ? false : (tab.mode === 'raw' ? false : 'nocursor'),
      gutters: _gutters,
      foldGutter: _foldKind ? { rangeFinder: _cmFoldFinder(_foldKind) } : false,
      extraKeys: {
        // index-fed autocomplete (project symbols from the cbm index).
        // Ctrl-Space only — NOT Cmd-Space (that's macOS Spotlight, the OS
        // grabs it before the browser sees it).
        'Ctrl-Space': (cm) => codeIndexComplete(cm),
        // fold/unfold the block at the cursor
        'Ctrl-Q': (cm) => { try { cm.foldCode(cm.getCursor()); } catch (_) {} },
      },
    });
    tab._foldKind = _foldKind;
    // CSS drives the height (.editor-cm .CodeMirror { height:100% }); a
    // refresh() after the element is laid out lets CM measure + show scrollbars.
    // (setSize('100%','100%') broke CM's scroll measurement → no scrollbars.)
    tab.cm.on('change', () => {
      // ignore the change fired by a programmatic setValue (e.g. .dbq view swap)
      if (tab._suppressDirty) return;
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
  } else if (_dbq) {
    // .dbq: swap the CM doc + language to match the active view. tab.raw was
    // already updated (in terminalEditorMode, before the switch) so the freshly
    // shown view reflects edits made in the other view. Both views editable.
    tab.cm.setOption('mode', _initMode);
    // XML view folds, SQL view doesn't — toggle the fold gutter accordingly.
    if (_foldKind !== tab._foldKind) {
      _terminalSetFold(tab, _foldKind);
      tab._foldKind = _foldKind;
    }
    if (tab.cm.getValue() !== _initVal) {
      tab._suppressDirty = true;            // a view swap is not a user edit
      tab.cm.setValue(_initVal);
      tab._suppressDirty = false;
    }
  } else {
    // keep CM in sync if raw changed via save; flip editability for the mode
    if (tab.cm.getValue() !== tab.raw && !tab.dirty) tab.cm.setValue(tab.raw);
    tab.cm.setOption('readOnly', tab.mode === 'raw' ? false : 'nocursor');
  }
  // edit mode gets focus (so the cursor shows); view mode does not. For .dbq
  // both modes are editable, so always focus.
  setTimeout(() => { try { tab.cm.refresh(); if (_dbq || tab.mode === 'raw') tab.cm.focus(); } catch (_) {} }, 20);
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
  let content = tab.cm.getValue();
  // .dbq always writes the full XML wrapper. In the SQL view, splice the edited
  // query back into <DisplaySQL>/<Body>; in the XML view the CM value IS the file.
  if (_isDbq(tab)) {
    if (tab.mode === 'render') {
      const merged = _dbqEmbedSql(tab.raw, content);
      if (merged === null) {
        if (typeof showToast === 'function') showToast('Kein <DisplaySQL>/<Body> zum Einsetzen gefunden');
        return;
      }
      content = merged;
    }
    tab.raw = content;  // keep the XML source of truth current for the other view
  }
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
  if (typeof _terminalUpdateAllTabScroll === 'function') _terminalUpdateAllTabScroll();
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
async function _terminalJumpTo(absPath, line, col) {
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
    const ch = Math.max(0, (parseInt(col, 10) || 1) - 1);
    tab.cm.setCursor({ line: ln, ch });
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

// ─── Symbol outline panel (file-tree area) ───────────────────────────────────
// Whole-project code outline (classes/methods/functions/variables) grouped by
// file, fed by the code index (no LLM). Click an entry → jump to its definition;
// expand an entry → its callers (from the graph) + usages (text-grep). A search
// box filters live. Lives under the file tree; the Σ header button opens the
// code-analysis dialog.
// symbols: flat project list · byFile: repo-rel-path → symbol[] (built on load) ·
// refs: lazy callers/usages cache keyed by _outlineKey · expandedSym: which
// symbol rows have their refs sub-list open (file-row expansion lives in the
// tree's own _wdSetExpanded state, shared with folders).
let _codeOutline = { symbols: [], byFile: new Map(), loaded: false, expandedSym: {}, refs: {}, selectedKey: '' };

// Per-label glyph + CSS class for the outline rows (compact, monochrome).
const _OUTLINE_GLYPH = {
  Class:    { g: '◈', c: 'sym-class' },
  Interface:{ g: '◈', c: 'sym-class' },
  Struct:   { g: '◈', c: 'sym-class' },
  Enum:     { g: '◈', c: 'sym-class' },
  Trait:    { g: '◈', c: 'sym-class' },
  Method:   { g: 'ƒ', c: 'sym-method' },
  Function: { g: 'ƒ', c: 'sym-func' },
  Field:    { g: '□', c: 'sym-var' },
  Variable: { g: '□', c: 'sym-var' },
  Constant: { g: '□', c: 'sym-const' },
  // SQL/.dbq symbols (from the regex scanner, merged into the outline):
  Procedure:  { g: 'ƒ', c: 'sym-method' },   // CREATE PROC
  View:       { g: '◫', c: 'sym-class' },    // CREATE VIEW
  CTE:        { g: '◇', c: 'sym-func' },     // WITH … AS (SELECT …)
  Table:      { g: '▤', c: 'sym-var' },      // FROM/JOIN reference
  Column:     { g: '▪', c: 'sym-const' },    // qualified column ref (table.col)
  LinkedServer: { g: '⇄', c: 'sym-other' },  // OPENQUERY linked server
};
function _outlineGlyph(label) { return _OUTLINE_GLYPH[label] || { g: '·', c: 'sym-other' }; }

// Load (or reload) the whole-project symbol outline. Symbols are now rendered
// INLINE in the file tree (under each source file), so after loading we build a
// per-file index (_codeOutline.byFile, keyed by repo-relative path) and repaint
// the tree. force re-fetches even if already loaded. Auto-called when the
// terminal panel opens for a code project.
async function codeOutlineLoad(force) {
  if (_codeOutline.loaded && !force) { _codeOutlineReindex(); _wdRepaintTreeSafe(); return; }
  const d = await _codeIndexFetch('outline=1');
  if (!d || d.error || !Array.isArray(d.symbols)) {
    _codeOutline.symbols = []; _codeOutline.loaded = true; _codeOutline.byFile = new Map();
    _wdRepaintTreeSafe();
    return;
  }
  _codeOutline.symbols = d.symbols;
  _codeOutline.loaded = true;
  _codeOutline.refs = {};
  _codeOutlineReindex();
  _wdRepaintTreeSafe();
}

// (Re)build the repo-relative-path → symbol[] index off _codeOutline.symbols.
function _codeOutlineReindex() {
  const m = new Map();
  for (const s of (_codeOutline.symbols || [])) {
    const f = s.file || '';
    if (!m.has(f)) m.set(f, []);
    m.get(f).push(s);
  }
  _codeOutline.byFile = m;
}

// Symbols for an ABSOLUTE file path (tree nodes are absolute; the index is
// repo-relative). Returns [] when none / not indexed.
function _wdSymbolsForFile(absPath) {
  const m = _codeOutline.byFile;
  if (!m || !m.size) return [];
  const rel = (typeof _wdRelToWorkingDir === 'function') ? _wdRelToWorkingDir(absPath) : absPath;
  return m.get(rel) || [];
}

function _wdRepaintTreeSafe() {
  try { if (typeof repaintTerminalTree === 'function') repaintTerminalTree(); } catch (_) {}
}

// Stable id for a symbol row (file + label + name + line) — used as expand key.
function _outlineKey(s) { return `${s.file}|${s.label}|${s.name}|${s.line || 0}`; }

// Render a file's symbol rows (inline tree children). `syms` is that file's
// symbol list (already filtered if a search is active). Mirrors the old symbol
// panel's row markup; click a name → jump to def, ↗ → toggle callers/usages.
// Render one leaf symbol row (used for non-table symbols and nested columns).
// `display` overrides the shown label (e.g. a column shows just COLUMN under its
// table, while the title/jump keep the full TABLE.COLUMN).
function _wdSymRow(s, extraCls, display) {
  const k = _outlineKey(s);
  const gl = _outlineGlyph(s.label);
  // nested column rows: drop the redundant "Spalte" signature (the table parent
  // makes it obvious) — keep signatures for all other symbol rows.
  const showSig = s.signature && !(extraCls && extraCls.indexOf('sym-col') !== -1);
  const sig = showSig ? `<span class="sym-sig">${esc(s.signature)}</span>` : '';
  const ln = s.line ? `<span class="sym-line">:${s.line}</span>` : '';
  const open = _codeOutline.expandedSym[k] ? ' sym-open' : '';
  const seld = (_codeOutline.selectedKey === k) ? ' sym-selected' : '';
  let html = `<div class="sym-row${open}${seld}${extraCls || ''}" data-key="${esc(k)}">
      <span class="sym-glyph ${gl.c}">${gl.g}</span>
      <span class="sym-name" onclick="event.stopPropagation();_codeOutlineJump('${esc(k)}')" title="${esc(s.name)} → ${esc(s.file)}:${s.line || ''}">${esc(display || s.name)}</span>
      ${sig}${ln}
      <span class="sym-refs-btn" onclick="event.stopPropagation();_codeOutlineRefs('${esc(k)}')" title="Aufrufer &amp; Verwendungen">↗</span>
    </div>`;
  if (_codeOutline.expandedSym[k]) html += _codeOutlineRefsHtml(k);
  return html;
}

// Render a file's symbols. Columns (label 'Column', name 'TABLE.COL') are nested
// under their Table row as collapsible children, so the tree reads hierarchically
// (table › its columns) instead of a flat list of tables then columns.
function _wdSymbolRowsHtml(syms) {
  syms = syms || [];
  // bucket columns by their table prefix (everything before the last '.')
  const colsByTable = new Map();
  const nonCols = [];
  for (const s of syms) {
    if (s.label === 'Column') {
      const dot = (s.name || '').lastIndexOf('.');
      const tbl = dot > 0 ? s.name.slice(0, dot) : s.name;
      if (!colsByTable.has(tbl)) colsByTable.set(tbl, []);
      colsByTable.get(tbl).push(s);
    } else {
      nonCols.push(s);
    }
  }
  let html = '';
  const tablesRendered = new Set();
  for (const s of nonCols) {
    if (s.label === 'Table' && colsByTable.has(s.name)) {
      html += _wdTableWithCols(s, colsByTable.get(s.name));
      tablesRendered.add(s.name);
    } else {
      html += _wdSymRow(s);
    }
  }
  // columns whose table has no Table symbol in this file → synthetic table group
  for (const [tbl, cols] of colsByTable) {
    if (tablesRendered.has(tbl)) continue;
    html += _wdTableWithCols({ label: 'Table', name: tbl, file: cols[0].file,
                               line: cols[0].line, signature: 'Spalten' }, cols);
  }
  return html;
}

// A Table row with a caret that toggles its column children. Expansion state is
// keyed in _codeOutline.expandedSym by the table's outline key (reuses the same
// store as the ↗ refs toggle — distinct keys, no collision).
function _wdTableWithCols(tableSym, cols) {
  const tk = 'tblcols|' + tableSym.file + '|' + tableSym.name;
  const isOpen = !!_codeOutline.expandedSym[tk];
  const gl = _outlineGlyph('Table');
  const caret = `<span class="sym-caret" onclick="event.stopPropagation();_wdToggleTableCols('${esc(tk)}')">${isOpen ? '▾' : '▸'}</span>`;
  const seld = (_codeOutline.selectedKey === _outlineKey(tableSym)) ? ' sym-selected' : '';
  let html = `<div class="sym-row sym-tablerow${seld}" data-tk="${esc(tk)}">
      ${caret}
      <span class="sym-glyph ${gl.c}">${gl.g}</span>
      <span class="sym-name" onclick="event.stopPropagation();_wdToggleTableCols('${esc(tk)}')" title="${esc(tableSym.name)} (${cols.length} Spalten)">${esc(tableSym.name)}</span>
      <span class="sym-sig">${cols.length} Spalten</span>
    </div>`;
  if (isOpen) {
    html += '<div class="sym-colchildren">';
    for (const c of cols) {
      const dot = (c.name || '').lastIndexOf('.');
      const short = dot > 0 ? c.name.slice(dot + 1) : c.name;  // COLUMN, not TABLE.COLUMN
      html += _wdSymRow(c, ' sym-col', short);
    }
    html += '</div>';
  }
  return html;
}

function _wdToggleTableCols(tk) {
  _codeOutline.expandedSym[tk] = !_codeOutline.expandedSym[tk];
  _wdRepaintTreeSafe();
}

function _outlineByKey(k) { return _codeOutline.symbols.find(s => _outlineKey(s) === k); }

async function _codeOutlineJump(k) {
  const s = _outlineByKey(k);
  if (!s) return;
  _codeOutline.selectedKey = k;           // mark as selected in the tree
  _wdRepaintTreeSafe();
  await _terminalJumpTo(_codeIndexAbs(s.file), s.line || 1);
}

// Toggle the inline callers+usages sub-list for a symbol. Lazy-fetches once,
// then repaints the (merged) tree so the sub-list renders under the symbol row.
async function _codeOutlineRefs(k) {
  const s = _outlineByKey(k);
  if (!s) return;
  _codeOutline.expandedSym[k] = !_codeOutline.expandedSym[k];
  if (_codeOutline.expandedSym[k] && !_codeOutline.refs[k]) {
    _codeOutline.refs[k] = { loading: true };
    _wdRepaintTreeSafe();
    const d = await _codeIndexFetch(`usages=${encodeURIComponent(s.name)}`);
    _codeOutline.refs[k] = {
      loading: false,
      callers: (d && d.callers) || [],
      usages: (d && d.usages) || [],
      err: d && d.error,
    };
  }
  _wdRepaintTreeSafe();
}

function _codeOutlineRefsHtml(k) {
  const r = _codeOutline.refs[k];
  if (!r) return '';
  if (r.loading) return '<div class="sym-refs"><div class="sym-refs-loading">Lädt…</div></div>';
  if (r.err) return `<div class="sym-refs"><div class="sym-refs-empty">${esc(r.err)}</div></div>`;
  const row = (file, line, text) => {
    const abs = _codeIndexAbs(file);
    const fn = (file || '').split('/').pop();
    const label = text ? esc(text) : `${esc(fn)}:${line || ''}`;
    return `<div class="sym-ref" onclick="_terminalJumpTo('${esc(abs)}', ${line || 1})" title="${esc(file)}:${line || ''}">
      <span class="sym-ref-loc">${esc(fn)}:${line || '?'}</span><span class="sym-ref-text">${label}</span></div>`;
  };
  const callers = (r.callers || []).filter(c => c.file);
  const usages = (r.usages || []);
  let h = '<div class="sym-refs">';
  h += `<div class="sym-refs-grp">Aufrufer <span class="sym-refs-n">${callers.length}</span></div>`;
  h += callers.length ? callers.map(c => row(c.file, c.line, '')).join('')
                       : '<div class="sym-refs-empty">keine</div>';
  h += `<div class="sym-refs-grp">Verwendungen <span class="sym-refs-n">${usages.length}</span></div>`;
  h += usages.length ? usages.map(u => row(u.file, u.line, u.text)).join('')
                     : '<div class="sym-refs-empty">keine</div>';
  h += '</div>';
  return h;
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
  // Use onmousedown (not onclick) on the items: the outside-close listener below
  // ALSO fires on mousedown and removes the menu, so an onclick would never land
  // (mousedown closes the menu before the click resolves → actions did nothing).
  // event.preventDefault keeps editor focus from stealing the gesture.
  const wj = String(word).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  menu.innerHTML = `
    <div class="code-ctx-head">${esc(word)}</div>
    <div class="code-ctx-item" onmousedown="event.preventDefault();event.stopPropagation();_codeIndexCloseMenu();codeGotoDefinition('${wj}')">Gehe zu Definition</div>
    <div class="code-ctx-item" onmousedown="event.preventDefault();event.stopPropagation();_codeIndexCloseMenu();codeWhoCalls('${wj}')">Wer ruft das auf?</div>`;
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
let _codeHoverAnchor = null;   // {x,y} where the pending/shown hover was requested

function _codeIndexAttachHover(tab) {
  const wrap = tab.el.querySelector('.editor-cm');
  if (!wrap) return;
  wrap.addEventListener('mousemove', (e) => {
    // Any movement dismisses the current tooltip IMMEDIATELY (don't wait for the
    // next hover to resolve) — then re-arm so it only reappears once the mouse
    // rests for 450ms. _codeHoverAnchor records where the pending hover was
    // requested so a tiny jitter inside the same word doesn't re-flash.
    const moved = !_codeHoverAnchor
      || Math.abs(e.clientX - _codeHoverAnchor.x) > 3
      || Math.abs(e.clientY - _codeHoverAnchor.y) > 3;
    if (moved && _codeHoverTip) _codeIndexHideHover();
    clearTimeout(_codeHoverTimer);
    _codeHoverAnchor = { x: e.clientX, y: e.clientY };
    const cx = e.clientX, cy = e.clientY;
    _codeHoverTimer = setTimeout(() => _codeIndexHover(tab, cx, cy), 450);
  });
  wrap.addEventListener('mouseleave', _codeIndexHideHover);
  // Scrolling the editor (mouse wheel / trackpad) must dismiss the tooltip too —
  // its anchor position is stale the moment the viewport moves. Listen on the
  // wrap (capture, passive) AND on CodeMirror's own scroll so both wheel and
  // programmatic scrolls clear it.
  wrap.addEventListener('wheel', _codeIndexHideHover, { passive: true, capture: true });
  if (tab.cm && typeof tab.cm.on === 'function') tab.cm.on('scroll', _codeIndexHideHover);
}

async function _codeIndexHover(tab, cx, cy) {
  if (!tab.cm) return;
  const pos = tab.cm.coordsChar({ left: cx, top: cy });
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
  tip.style.left = Math.min(cx + 12, window.innerWidth - 360) + 'px';
  tip.style.top = (cy + 16) + 'px';
  document.body.appendChild(tip);
  _codeHoverTip = tip;
}

function _codeIndexHideHover() {
  clearTimeout(_codeHoverTimer);
  _codeHoverAnchor = null;
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
// ─── Code-Auswertungen (analysis dialog) ─────────────────────────────────────
// Ready-made, genuinely useful analyses over the code index, shown as cards with
// nicely-formatted results. The raw Cypher box is demoted to an "Eigene Abfrage"
// expander at the bottom (power users only). Each analysis is a labelled Cypher
// query + a renderer hint; queries use only labels/edges the cbm graph reliably
// has (verified live): :Function/:Method/:Class/:Variable, CALLS, INHERITS,
// n.complexity/n.file_path/n.start_line. cbm needs explicit property projections.
const _CODE_ANALYSES = [
  { id: 'complex', label: 'Komplexeste Funktionen', hint: 'Refactoring-Kandidaten',
    q: "MATCH (n) WHERE (n:Function OR n:Method) AND n.complexity IS NOT NULL RETURN n.name, n.complexity, n.file_path, n.start_line ORDER BY n.complexity DESC LIMIT 25",
    cols: ['Symbol', 'Komplexität', 'Datei', 'Zeile'], fileCol: 2, lineCol: 3, barCol: 1 },
  { id: 'mostcalled', label: 'Meistgenutzte Funktionen', hint: 'zentrale/kritische Stellen',
    q: "MATCH (n)<-[:CALLS]-(m) RETURN n.name, count(m) AS aufrufer, n.file_path, n.start_line ORDER BY aufrufer DESC LIMIT 25",
    cols: ['Symbol', 'Aufrufer', 'Datei', 'Zeile'], fileCol: 2, lineCol: 3, barCol: 1 },
  { id: 'orchestrators', label: 'Aufruf-intensivste Funktionen', hint: 'Orchestrierer / God-Functions',
    q: "MATCH (n)-[:CALLS]->(m) RETURN n.name, count(m) AS ruft_auf, n.file_path, n.start_line ORDER BY ruft_auf DESC LIMIT 25",
    cols: ['Symbol', 'ruft auf', 'Datei', 'Zeile'], fileCol: 2, lineCol: 3, barCol: 1 },
  { id: 'biggest', label: 'Größte Dateien', hint: 'wo sich der Code ballt',
    q: "MATCH (n) WHERE n.file_path IS NOT NULL AND NOT (n:File OR n:Module OR n:Folder OR n:Project) RETURN n.file_path, count(n) AS symbole ORDER BY symbole DESC LIMIT 25",
    cols: ['Datei', 'Symbole'], fileCol: 0, lineCol: -1, barCol: 1 },
  // Inheritance is :INHERITS in Python but :IMPLEMENTS for Rust/TS/JS/C# (verified
  // live across all of them) — match BOTH so the hierarchy works cross-language.
  { id: 'hierarchy', label: 'Klassenhierarchie', hint: 'Vererbung & Interfaces',
    q: "MATCH (a)-[r:INHERITS|IMPLEMENTS]->(b) RETURN a.name, b.name, a.file_path, a.start_line ORDER BY b.name, a.name LIMIT 50",
    cols: ['Klasse', 'erbt von / implementiert', 'Datei', 'Zeile'], fileCol: 2, lineCol: 3, barCol: -1 },
];

// SQL analysis cards, fetched per project (only shown when the project has .sql/
// .dbq files). Distinct from the Cypher analyses: they run server-side over the
// raw SQL corpus (regex, dialect-tolerant) and already return the table shape.
let _codeSqlCards = null;   // null = not yet fetched; [] = none/no sql
let _codeRCards = null;     // null = not yet fetched; [] = none/no R

function codeAnalysisDialog() {
  if (document.getElementById('code-analysis')) return;
  const ov = document.createElement('div');
  ov.id = 'code-analysis';
  ov.className = 'code-palette-overlay';
  const cards = _CODE_ANALYSES.map(a =>
    `<button class="code-an-card" data-id="${a.id}" onclick="_codeAnalysisRun('${a.id}')">
       <span class="code-an-card-label">${esc(a.label)}</span>
       <span class="code-an-card-hint">${esc(a.hint)}</span>
     </button>`).join('');
  ov.innerHTML = `
    <div class="code-palette code-an-box" onclick="event.stopPropagation()">
      <div class="code-an-head">Code-Auswertungen
        <span class="code-an-sub">Analysen über den Code-Index — Pfad/Zeile anklicken springt in den Code</span></div>
      <div class="code-an-cards">${cards}</div>
      <div id="code-an-sql-section" style="display:none">
        <div class="code-an-grouphead">SQL-Auswertungen</div>
        <div class="code-an-cards" id="code-an-sql-cards"></div>
      </div>
      <div id="code-an-r-section" style="display:none">
        <div class="code-an-grouphead">R-Auswertungen</div>
        <div class="code-an-cards" id="code-an-r-cards"></div>
      </div>
      <div class="code-an-resultwrap">
        <div class="code-an-resulthead"><span id="code-an-title"></span><span id="code-an-status" class="code-an-status"></span></div>
        <div id="code-an-results" class="code-an-results"><div class="code-palette-hint">Wählen Sie eine Auswertung.</div></div>
      </div>
      <details class="code-an-advanced">
        <summary>Eigene Abfrage (erweitert)</summary>
        <div class="code-an-adv-body">
          <textarea id="code-cypher-input" spellcheck="false" autocomplete="off"
            placeholder="MATCH (n:Function) RETURN n.name, n.file_path, n.start_line ORDER BY n.start_line"></textarea>
          <div class="code-an-adv-actions">
            <span class="code-cypher-hint">Cypher, nur lesend · ${_isMac() ? '⌘' : 'Strg'}+Enter</span>
            <button class="btn-primary" onclick="_codeCypherRun()">Ausführen</button>
          </div>
        </div>
      </details>
    </div>`;
  ov.addEventListener('click', () => ov.remove());
  document.body.appendChild(ov);
  ov.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') ov.remove();
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      const ta = document.getElementById('code-cypher-input');
      if (ta && document.activeElement === ta) { e.preventDefault(); _codeCypherRun(); }
    }
  });
  _codeAnalysisLoadSqlCards();   // adds SQL cards if the project has .sql/.dbq
  _codeAnalysisLoadRCards();     // adds R cards if the project has .R/.r
}

// Fetch + render the SQL analysis cards (only when the project actually has SQL).
async function _codeAnalysisLoadSqlCards() {
  const sec = document.getElementById('code-an-sql-section');
  const wrap = document.getElementById('code-an-sql-cards');
  if (!sec || !wrap) return;
  const d = await _codeIndexFetch('sql_meta=1');
  if (!d || !d.has_sql || !Array.isArray(d.analyses) || !d.analyses.length) return;
  _codeSqlCards = d.analyses;
  wrap.innerHTML = d.analyses.map(a =>
    `<button class="code-an-card code-an-sql" data-id="${esc(a.id)}" onclick="_codeSqlRun('${esc(a.id)}')">
       <span class="code-an-card-label">${esc(a.label)}</span>
       <span class="code-an-card-hint">${esc(a.hint)}</span>
     </button>`).join('');
  sec.style.display = '';
}

// Run a SQL analysis (server-side over the raw SQL corpus). Reuses the analysis
// result area + _codeAnalysisTable — the backend returns the same shape.
async function _codeSqlRun(id) {
  const card = _codeSqlCards && _codeSqlCards.find(x => x.id === id);
  const title = document.getElementById('code-an-title');
  const status = document.getElementById('code-an-status');
  const box = document.getElementById('code-an-results');
  if (!box) return;
  document.querySelectorAll('.code-an-card').forEach(c => c.classList.toggle('active', c.dataset.id === id));
  if (title) title.textContent = (card && card.label) || 'SQL';
  if (status) status.textContent = 'Läuft …';
  box.innerHTML = '';
  const d = await _codeIndexFetch(`sql=${encodeURIComponent(id)}`);
  if (!d || d.error) {
    if (status) status.textContent = '';
    box.innerHTML = `<div class="code-palette-hint code-cypher-err">${esc((d && d.error) || 'Auswertung fehlgeschlagen')}</div>`;
    return;
  }
  const rows = d.rows || [];
  if (status) status.textContent = `${rows.length} Treffer`;
  if (!rows.length) { box.innerHTML = '<div class="code-palette-hint">Keine Daten.</div>'; return; }
  // backend supplies the column spec (fileCol/lineCol/barCol) → reuse the renderer
  const spec = { cols: d.columns || [], fileCol: (d.fileCol ?? -1), lineCol: (d.lineCol ?? -1), barCol: (d.barCol ?? -1) };
  box.innerHTML = _codeAnalysisTable(spec, rows);
}

// Fetch + render the R analysis cards (only when the project actually has R).
async function _codeAnalysisLoadRCards() {
  const sec = document.getElementById('code-an-r-section');
  const wrap = document.getElementById('code-an-r-cards');
  if (!sec || !wrap) return;
  const d = await _codeIndexFetch('r_meta=1');
  if (!d || !d.has_r || !Array.isArray(d.analyses) || !d.analyses.length) return;
  _codeRCards = d.analyses;
  wrap.innerHTML = d.analyses.map(a =>
    `<button class="code-an-card code-an-r" data-id="${esc(a.id)}" onclick="_codeRRun('${esc(a.id)}')">
       <span class="code-an-card-label">${esc(a.label)}</span>
       <span class="code-an-card-hint">${esc(a.hint)}</span>
     </button>`).join('');
  sec.style.display = '';
}

// Run an R analysis (server-side over the raw R corpus). Reuses the analysis
// result area + _codeAnalysisTable — the backend returns the same shape.
async function _codeRRun(id) {
  const card = _codeRCards && _codeRCards.find(x => x.id === id);
  const title = document.getElementById('code-an-title');
  const status = document.getElementById('code-an-status');
  const box = document.getElementById('code-an-results');
  if (!box) return;
  document.querySelectorAll('.code-an-card').forEach(c => c.classList.toggle('active', c.dataset.id === id));
  if (title) title.textContent = (card && card.label) || 'R';
  if (status) status.textContent = 'Läuft …';
  box.innerHTML = '';
  const d = await _codeIndexFetch(`r=${encodeURIComponent(id)}`);
  if (!d || d.error) {
    if (status) status.textContent = '';
    box.innerHTML = `<div class="code-palette-hint code-cypher-err">${esc((d && d.error) || 'Auswertung fehlgeschlagen')}</div>`;
    return;
  }
  const rows = d.rows || [];
  if (status) status.textContent = `${rows.length} Treffer`;
  if (!rows.length) { box.innerHTML = '<div class="code-palette-hint">Keine Daten.</div>'; return; }
  // backend supplies the column spec (fileCol/lineCol/barCol) → reuse the renderer
  const spec = { cols: d.columns || [], fileCol: (d.fileCol ?? -1), lineCol: (d.lineCol ?? -1), barCol: (d.barCol ?? -1) };
  box.innerHTML = _codeAnalysisTable(spec, rows);
}

// Back-compat alias: the old entry point name still works (e.g. any cached HTML).
function codeCypherBar() { codeAnalysisDialog(); }

async function _codeAnalysisRun(id) {
  const a = _CODE_ANALYSES.find(x => x.id === id);
  if (!a) return;
  const title = document.getElementById('code-an-title');
  const status = document.getElementById('code-an-status');
  const box = document.getElementById('code-an-results');
  if (!box) return;
  document.querySelectorAll('.code-an-card').forEach(c =>
    c.classList.toggle('active', c.dataset.id === id));
  if (title) title.textContent = a.label;
  if (status) status.textContent = 'Läuft …';
  box.innerHTML = '';
  const d = await _codeIndexFetch(`cypher=${encodeURIComponent(a.q)}`);
  if (!d || d.error) {
    if (status) status.textContent = '';
    box.innerHTML = `<div class="code-palette-hint code-cypher-err">${esc((d && d.error) || 'Auswertung fehlgeschlagen')}</div>`;
    return;
  }
  const rows = d.rows || [];
  if (status) status.textContent = `${rows.length} Treffer`;
  if (!rows.length) { box.innerHTML = '<div class="code-palette-hint">Keine Daten für diese Auswertung in diesem Projekt.</div>'; return; }
  box.innerHTML = _codeAnalysisTable(a, rows);
}

// Render an analysis result as a clean table: a leading bar-cell visualises the
// numeric metric (barCol), path+line cells are clickable jumps.
function _codeAnalysisTable(a, rows) {
  // max for the bar scale
  let max = 0;
  if (a.barCol >= 0) for (const r of rows) { const v = Number(_cellAt(r, a.barCol)); if (v > max) max = v; }
  const head = a.cols.map((c, i) => `<th${i === a.barCol ? ' class="an-num"' : ''}>${esc(c)}</th>`).join('');
  const body = rows.map(r => {
    const cells = a.cols.map((_, i) => {
      const v = _cellAt(r, i);
      const s = v === null || v === undefined ? '' : String(v);
      if (i === a.fileCol && s) {
        const abs = _codeIndexAbs(s);
        const line = a.lineCol >= 0 ? (parseInt(_cellAt(r, a.lineCol), 10) || 1) : 1;
        const fn = s.split('/').pop();
        return `<td class="an-file"><a onclick="_terminalJumpTo('${esc(abs)}', ${line})" title="${esc(s)}">${esc(fn)}</a></td>`;
      }
      if (i === a.lineCol) return s ? `<td class="an-line">:${esc(s)}</td>` : '<td></td>';
      if (i === a.barCol) {
        const v = Number(s) || 0;
        const pct = max > 0 ? Math.round((v / max) * 100) : 0;
        return `<td class="an-num"><span class="an-bar" style="--an-pct:${pct}%"></span><span class="an-num-v">${esc(s)}</span></td>`;
      }
      return `<td>${esc(s)}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return `<table class="code-an-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function _cellAt(row, i) {
  if (i < 0) return '';
  if (Array.isArray(row)) return i < row.length ? row[i] : '';
  return i === 0 ? row : '';
}

// Run the advanced raw-Cypher box → render into the shared analysis result area.
async function _codeCypherRun() {
  const ta = document.getElementById('code-cypher-input');
  const status = document.getElementById('code-an-status');
  const box = document.getElementById('code-an-results');
  const title = document.getElementById('code-an-title');
  if (!ta || !box) return;
  const q = ta.value.trim();
  if (!q) { return; }
  document.querySelectorAll('.code-an-card.active').forEach(c => c.classList.remove('active'));
  if (title) title.textContent = 'Eigene Abfrage';
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
        return `<td class="an-file"><a onclick="_terminalJumpTo('${esc(_codeIndexAbs(s))}', 1)">${esc(s.split('/').pop())}</a></td>`;
      }
      return `<td>${esc(s)}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  box.innerHTML = `<table class="code-an-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
