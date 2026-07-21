/* ═══════════════════════════════════════════════════════════
   NAVIGATION
   ═══════════════════════════════════════════════════════════ */
function navigateTo(view, opts) {
  state.currentView = view;
  // On mobile, picking a view dismisses the slide-in sidebar drawer so the
  // chosen view is actually visible (no-op on desktop where it's inline).
  closeMobileSidebar();
  // Stop project-sync polling whenever we leave the project-detail view.
  if (view !== 'project-detail') stopProjectSyncPoll();
  // Stop code-mode init/file-tree polling when leaving project-detail.
  if (view !== 'project-detail' && typeof stopCodeModePoll === 'function') stopCodeModePoll();

  // Wiki gets a fullscreen work area: squeeze the app nav to its icon rail while
  // it's open (same as code-mode terminal). Leaving wiki restores the user's
  // saved sidebar preference. A manual toggle while in wiki wins (rewrites the
  // localStorage flag the restore reads).
  if (view === 'wiki') {
    document.getElementById('sidebar')?.classList.add('collapsed');
  } else if (localStorage.getItem('sidebar-collapsed') !== '1') {
    document.getElementById('sidebar')?.classList.remove('collapsed');
  }

  // Hide all views
  document.getElementById('welcome-view').style.display = 'none';
  document.getElementById('chat-view').classList.remove('active');
  document.getElementById('chats-view').classList.remove('active');
  document.getElementById('projects-view').classList.remove('active');
  document.getElementById('project-detail-view').classList.remove('active');
  document.getElementById('artifacts-view').classList.remove('active');
  document.getElementById('scheduled-view').classList.remove('active');
  const favView = document.getElementById('favourites-view');
  if (favView) favView.classList.remove('active');
  const wfView = document.getElementById('workflows-view');
  if (wfView) wfView.classList.remove('active');
  const trView = document.getElementById('translation-view');
  if (trView) trView.classList.remove('active');
  const dvView = document.getElementById('data-view');
  if (dvView) dvView.classList.remove('active');
  const wkView = document.getElementById('wiki-view');
  if (wkView) wkView.classList.remove('active');

  // Update sidebar active state: highlight the matching sub-nav item and switch
  // the tab rail to the section this view belongs to.
  document.querySelectorAll('.sb-nav-item').forEach(n => n.classList.remove('active'));
  const navItem = document.querySelector(`.sb-subnav .sb-nav-item[data-view="${view}"]`);
  if (navItem) navItem.classList.add('active');
  syncSidebarTabsToView(view);

  switch(view) {
    case 'welcome':
      document.getElementById('welcome-view').style.display = '';
      // No page-header title on welcome — the sidebar brand already reads
      // "Brain Agent", so a header title here is redundant.
      updatePageHeader('');
      // Hide per-chat status bar — welcome is the new-chat landing screen,
      // there's no active session, so showing the previous chat's session
      // id / model / tokens / cost is misleading.
      document.getElementById('status-bar').style.display = 'none';
      try { renderPromptCards(); } catch (_) {}
      break;

    case 'chat':
      document.getElementById('chat-view').classList.add('active');
      updateChatView();
      document.getElementById('status-bar').style.display = '';
      break;

    case 'chats':
      document.getElementById('chats-view').classList.add('active');
      // No page-header title — the view renders its own "Chats und Aufgaben"
      // heading, so a top-bar title would be redundant.
      updatePageHeader('');
      document.getElementById('status-bar').style.display = '';
      loadChatsList();
      break;

    case 'projects':
      document.getElementById('projects-view').classList.add('active');
      updatePageHeader('');  // view has its own "Projekte" heading
      // Hide per-chat status bar — same reason as project-detail: no chat
      // in scope, the bar would otherwise show stale session data.
      document.getElementById('status-bar').style.display = 'none';
      loadProjectsList();
      break;

    case 'project-detail':
      document.getElementById('project-detail-view').classList.add('active');
      updatePageHeader('');  // view shows the project name in its own heading
      // Hide the per-chat status bar — there's no chat in scope on this view,
      // so the bar would otherwise leak the previous chat's session id /
      // model / tokens / cost / context fill, which the user reads as
      // "current state" and isn't.
      document.getElementById('status-bar').style.display = 'none';
      if (opts?.agentId && opts?.projectName) {
        loadProjectDetail(opts.agentId, opts.projectName);
      }
      // Refresh composer toggles so the project composer mirrors chat state
      // (model selector, thinking, save-to-memory, caveman, gdpr shield, local
      // chip). openProject() resets state.activeChat's composer modes to
      // defaults before navigating here, so updateStatusBar() — which is what
      // repaints the caveman / memory / gdpr-pref toggle buttons — now reads
      // the fresh defaults, not the prior chat's state. (The status BAR itself
      // stays hidden via display:none above; only the composer toggles, which
      // updateStatusBar also drives, are the target.)
      try {
        if (state.activeChat?.model) updateModelSelectorDisplay(state.activeChat.model);
        refreshThinkingButton();
  if (typeof refreshResearchModeButton === 'function') refreshResearchModeButton();
  if (typeof refreshDeepResearchButton === 'function') refreshDeepResearchButton();
        if (typeof updateStatusBar === 'function') updateStatusBar();
        updateSendButton();
        renderFilePreviews();
        schedulePIIBadgeUpdate();
      } catch(_) {}
      break;

    case 'artifacts':
      document.getElementById('artifacts-view').classList.add('active');
      updatePageHeader('');  // view has its own "Artefakte" heading
      // Context split: Startseite → chat artifacts, Projekte → project artifacts.
      if (typeof setArtifactsBrowseContext === 'function') {
        setArtifactsBrowseContext(opts?.context || '');
      }
      // Hide per-chat status bar — artifacts overview is a cross-session
      // grid; no chat in scope, so the bar would otherwise show stale
      // session data from whichever chat the user was last viewing.
      document.getElementById('status-bar').style.display = 'none';
      loadArtifactsBrowse();
      break;

    case 'scheduled':
      document.getElementById('scheduled-view').classList.add('active');
      updatePageHeader('');  // view has its own "Geplant" heading
      // Hide per-chat status bar — scheduled is a list view with no chat
      // in scope. Once a run is opened (openScheduledArtifact → chat view)
      // the bar comes back with that run's actual data.
      document.getElementById('status-bar').style.display = 'none';
      loadScheduledView();
      renderRecentChats(); // dispatches to renderRecentScheduledRuns when view==='scheduled'
      break;

    case 'workflows':
      document.getElementById('workflows-view').classList.add('active');
      updatePageHeader('');  // view has its own "Workflows" heading
      document.getElementById('status-bar').style.display = 'none';
      if (typeof loadWorkflows === 'function') loadWorkflows();
      break;

    case 'translation': {
      document.getElementById('translation-view').classList.add('active');
      const activeTab = document.querySelector('.tr-tab.active')?.dataset?.tab || 'text';
      _updateTranslationHeaderStar(activeTab);
      document.getElementById('status-bar').style.display = 'none';
      if (typeof loadTranslationView === 'function') loadTranslationView();
      break;
    }

    case 'data': {
      document.getElementById('data-view').classList.add('active');
      updatePageHeader('');  // view has its own "Dokumentklassifizierung" heading
      document.getElementById('status-bar').style.display = 'none';
      if (typeof clsOpenView === 'function') clsOpenView();
      break;
    }

    case 'wiki': {
      document.getElementById('wiki-view').classList.add('active');
      updatePageHeader('');  // wiki renders its own heading
      document.getElementById('status-bar').style.display = 'none';
      if (typeof loadWikiView === 'function') loadWikiView();
      break;
    }

    case 'favourites':
      if (favView) favView.classList.add('active');
      updatePageHeader('');  // view has its own "Favoriten" heading
      document.getElementById('status-bar').style.display = 'none';
      if (typeof loadFavouritesView === 'function') loadFavouritesView();
      break;

  }

  // Sync the sidebar list to the new view. Dispatcher in renderRecentChats
  // picks runs vs chats based on currentView + active chat readonly state.
  // Skip if 'scheduled' branch above already triggered a runs render.
  // Defer to microtask so callers like openScheduledArtifact have a chance to
  // set readonly markers on the active chat AFTER navigateTo returns.
  if (view !== 'scheduled') {
    Promise.resolve().then(() => {
      // Re-check view in case another navigateTo fired in the meantime.
      if (state.currentView === view) renderRecentChats();
    });
  }

  // Show the right-panel toggle only where a panel makes sense (active chat
  // session); hide it elsewhere and close any panel left open.
  if (typeof updateRightPanelButtonVisibility === 'function') updateRightPanelButtonVisibility();

  // Re-evaluate the code-mode terminal panel on every navigation: it auto-closes
  // when the project/chat it was launched from is no longer the shown view (e.g.
  // navigating to welcome/projects/wiki, or to a different project). The view's
  // own data (project-detail / chat) is set above, so the sync check inside
  // terminalRefreshToggle resolves against the freshly-shown view.
  if (typeof terminalRefreshToggle === 'function') {
    try { terminalRefreshToggle(); } catch (_) {}
  }

  closeMobileSidebar();
}

/* ═══════════════════════════════════════════════════════════
   SIDEBAR TABS (top-level sections, claude.ai-style)
   Icon-only tab rail; each tab reveals its own sub-nav below.
   ═══════════════════════════════════════════════════════════ */
// Reusable icon markup so the rail and the sub-nav share one glyph per concept.
const SB_ICONS = {
  home:        '<svg viewBox="0 0 24 24"><path d="M3 12l9-9 9 9"/><path d="M5 10v10a1 1 0 001 1h4v-6h4v6h4a1 1 0 001-1V10"/></svg>',
  chats:       '<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
  projects:    '<svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>',
  favourites:  '<svg viewBox="0 0 24 24"><polygon points="12 2 15 8.5 22 9.3 17 14 18.2 21 12 17.8 5.8 21 7 14 2 9.3 9 8.5 12 2"/></svg>',
  artifacts:   '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
  scheduled:   '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  workflows:   '<svg viewBox="0 0 24 24"><polyline points="4 7 8 11 4 15"/><polyline points="20 7 16 11 20 15"/><line x1="9" y1="18" x2="15" y2="6"/></svg>',
  translation: '<svg viewBox="0 0 24 24"><path d="M5 8l6 6"/><path d="M4 14l6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="M22 22l-5-10-5 10"/><path d="M14 18h6"/></svg>',
  data:        '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>',
  wiki:        '<svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
  settings:    '<svg viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
};

// A sub-nav item that opens the Anpassen (agent settings) modal. It's not a real
// view — it never becomes the active view — so it carries no data-view.
const SB_SETTINGS_ITEM = { label: 'Anpassen', icon: 'settings', onclick: 'openAgentSettings()' };

// Top-level tabs. `views` lists every currentView that belongs to this tab (for
// active-tab highlighting). `subnav` is the list shown when the tab is active.
const SIDEBAR_TABS = [
  { id: 'startseite',  title: 'Startseite',   icon: 'home',        activate: () => navigateTo('welcome'),
    views: ['welcome', 'chat', 'chats', 'artifacts', 'search'],
    subnav: [
      { label: 'Chats',     icon: 'chats',     view: 'chats',     onclick: "navigateTo('chats')" },
      { label: 'Artefakte', icon: 'artifacts', view: 'artifacts', onclick: "navigateTo('artifacts', {context:'chat'})" },
      SB_SETTINGS_ITEM,
    ] },
  { id: 'projekte',    title: 'Projekte',     icon: 'projects',    activate: () => navigateTo('projects'),
    views: ['projects', 'project-detail'],
    subnav: [
      { label: 'Projekte',  icon: 'projects',  view: 'projects',  onclick: "navigateTo('projects')" },
      { label: 'Artefakte', icon: 'artifacts', view: 'artifacts', onclick: "navigateTo('artifacts', {context:'project'})" },
      SB_SETTINGS_ITEM,
    ] },
  { id: 'favoriten',   title: 'Favoriten',    icon: 'favourites',  activate: () => navigateTo('favourites'),
    views: ['favourites'],
    subnav: [
      { label: 'Favoriten', icon: 'favourites', view: 'favourites', onclick: "navigateTo('favourites')" },
      SB_SETTINGS_ITEM,
    ] },
  { id: 'geplant',     title: 'Geplant',      icon: 'scheduled',   activate: () => navigateTo('scheduled'),
    views: ['scheduled'],
    subnav: [
      { label: 'Geplant',   icon: 'scheduled', view: 'scheduled', onclick: "navigateTo('scheduled')" },
      SB_SETTINGS_ITEM,
    ] },
  { id: 'workflows',   title: 'Workflows',    icon: 'workflows',   activate: () => navigateTo('workflows'),
    views: ['workflows'],
    subnav: [
      { label: 'Workflows', icon: 'workflows', view: 'workflows', onclick: "navigateTo('workflows')" },
      SB_SETTINGS_ITEM,
    ] },
  { id: 'uebersetzung', title: 'Übersetzung', icon: 'translation', activate: () => navigateTo('translation'),
    views: ['translation'],
    subnav: [
      { label: 'Übersetzung', icon: 'translation', view: 'translation', onclick: "navigateTo('translation')" },
      SB_SETTINGS_ITEM,
    ] },
  { id: 'daten',       title: 'Daten',        icon: 'data',        activate: () => navigateTo('data'),
    views: ['data'],
    subnav: [
      { label: 'Daten',     icon: 'data',      view: 'data',      onclick: "navigateTo('data')" },
      SB_SETTINGS_ITEM,
    ] },
  { id: 'wiki',        title: 'Wiki',         icon: 'wiki',        activate: () => navigateTo('wiki'),
    views: ['wiki'],
    subnav: [
      { label: 'Wiki',      icon: 'wiki',      view: 'wiki',      onclick: "navigateTo('wiki')" },
      SB_SETTINGS_ITEM,
    ] },
];

// Which tab currently owns the sub-nav. Driven by the active view; falls back to
// the first tab (Startseite) for views not claimed by any tab.
let _activeSidebarTabId = 'startseite';

function _tabForView(view) {
  const t = SIDEBAR_TABS.find(tab => tab.views.includes(view));
  return t ? t.id : _activeSidebarTabId;
}

function renderSidebarTabs() {
  const rail = document.getElementById('sb-tab-rail');
  if (!rail) return;
  rail.innerHTML = SIDEBAR_TABS.map(tab => `
    <button class="sb-tab${tab.id === _activeSidebarTabId ? ' active' : ''}"
            role="tab" data-tab="${tab.id}" title="${esc(tab.title)}"
            aria-label="${esc(tab.title)}" aria-selected="${tab.id === _activeSidebarTabId}"
            onclick="selectSidebarTab('${tab.id}', true)">${SB_ICONS[tab.icon] || ''}</button>`).join('');
  renderSidebarSubnav();
}

function renderSidebarSubnav() {
  const host = document.getElementById('sb-subnav');
  if (!host) return;
  const tab = SIDEBAR_TABS.find(t => t.id === _activeSidebarTabId) || SIDEBAR_TABS[0];
  // "Anpassen" opens admin-gated editors (soul/agent.json/MCP/hooks) — hide it
  // for non-admins so the row doesn't tease an editor that 403s on save.
  const isAdmin = (state.authUser?.role || 'admin') === 'admin';
  const items = (tab.subnav || []).filter(item => item !== SB_SETTINGS_ITEM || isAdmin);
  host.innerHTML = items.map(item => {
    const dv = item.view ? ` data-view="${item.view}"` : '';
    const active = item.view && item.view === state.currentView ? ' active' : '';
    return `<div class="sb-nav-item${active}"${dv} onclick="${item.onclick}">
      <span class="sb-icon">${SB_ICONS[item.icon] || ''}</span>
      <span class="sb-label">${esc(item.label)}</span>
    </div>`;
  }).join('');
}

// Clicking a tab: switch the sub-nav and, when `navigate` is set (user click),
// jump to that section's landing view.
function selectSidebarTab(tabId, navigate) {
  const tab = SIDEBAR_TABS.find(t => t.id === tabId);
  if (!tab) return;
  _activeSidebarTabId = tabId;
  document.querySelectorAll('#sb-tab-rail .sb-tab').forEach(el => {
    const on = el.dataset.tab === tabId;
    el.classList.toggle('active', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  renderSidebarSubnav();
  if (navigate && typeof tab.activate === 'function') {
    tab.activate();
  }
}

// Keep the tab rail + sub-nav in sync when the view changes elsewhere (deep
// link, programmatic navigation, opening a chat). No navigation side-effect.
function syncSidebarTabsToView(view) {
  const tabId = _tabForView(view);
  if (tabId !== _activeSidebarTabId) {
    _activeSidebarTabId = tabId;
    document.querySelectorAll('#sb-tab-rail .sb-tab').forEach(el => {
      const on = el.dataset.tab === tabId;
      el.classList.toggle('active', on);
      el.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    renderSidebarSubnav();
  } else {
    // Same tab, but the active sub-nav item may have changed.
    renderSidebarSubnav();
  }
}

const _TR_TAB_TITLES = {
  text: 'Textübersetzung',
  document: 'Dokumentübersetzung',
  audio: 'Audio-/Videoübersetzung',
  live: 'Live-Mikrofonübersetzung',
};

function _updateTranslationHeaderStar(tab) {
  const title = _TR_TAB_TITLES[tab] || 'Übersetzung';
  updatePageHeader(title, null, null, {
    item_type: 'translation',
    item_id: tab,
    agent_id: 'main',
    title,
  });
}

function updatePageHeader(title, breadcrumb, breadcrumbAgentId, favouriteOpts, tooltip) {
  const el = document.getElementById('page-header-title');
  // Collapse the header bar when it carries no title (list/tool views render
  // their own heading) so the content isn't pushed down by an empty 48px bar.
  // The right-side controls (Panel toggle, bg-tasks pill) are only meaningful on
  // the chat view, which always has a title — so collapsing here is safe.
  const hdr = document.getElementById('page-header');
  if (hdr) hdr.classList.toggle('page-header--empty', !title && !breadcrumb);
  if (breadcrumb) {
    // When breadcrumbAgentId is set, the breadcrumb is a project name and the
    // span becomes a click target that opens the project view. The listener is
    // attached programmatically (not inline) so quotes in the names can't
    // break out of the attribute and silently disable the handler.
    const titleAttr = tooltip ? ` title="${esc(tooltip)}"` : '';
    el.innerHTML = `<span class="page-header-crumb"${breadcrumbAgentId ? ' data-clickable="1" style="color:var(--text-400);cursor:pointer" title="Projekt öffnen"' : ' style="color:var(--text-400)"'}>${esc(breadcrumb)}</span> <span class="breadcrumb-sep">/</span> <span${titleAttr}>${esc(title)}</span>`;
    if (breadcrumbAgentId) {
      const crumb = el.querySelector('.page-header-crumb');
      crumb.addEventListener('click', (ev) => {
        ev.stopPropagation();
        openProject(breadcrumbAgentId, breadcrumb);
      });
    }
  } else {
    el.textContent = title;
    if (tooltip) el.title = tooltip;
    else el.removeAttribute('title');
  }

  // Mount / refresh the favourite-star button + share button in the
  // header-right area. favouriteOpts: { item_type, item_id, agent_id, title }
  // or null/undefined to clear.
  const right = document.getElementById('page-header-right');
  if (right) {
    const existing = right.querySelector('.fav-star-btn');
    if (existing) existing.remove();
    const existingShare = right.querySelector('.share-btn');
    if (existingShare) existingShare.remove();
    const existingPill = right.querySelector('.share-pill');
    if (existingPill) existingPill.remove();
    if (favouriteOpts && favouriteOpts.item_id && window.Favourites?.mount) {
      const btn = window.Favourites.mount(right, favouriteOpts);
      if (btn) btn.style.order = '-1';
    }
    // Share button — supported for chat / project_chat / project / schedule /
    // workflow / artifact (not translation). The visibility pill is hydrated
    // asynchronously after the button mounts.
    const SHAREABLE = ['chat', 'project_chat', 'project', 'schedule', 'workflow', 'artifact'];
    if (favouriteOpts && favouriteOpts.item_id && SHAREABLE.includes(favouriteOpts.item_type) && typeof shareButton === 'function') {
      const sb = shareButton(favouriteOpts.item_type, favouriteOpts.item_id, favouriteOpts.agent_id || '',
                             { title: favouriteOpts.title, onChange: () => updatePageHeader(title, breadcrumb, breadcrumbAgentId, favouriteOpts) });
      sb.style.order = '-1';
      right.insertBefore(sb, right.firstChild);
      // Hydrate the pill (best-effort, ignore failures).
      const qs = `item_type=${encodeURIComponent(favouriteOpts.item_type)}&item_id=${encodeURIComponent(favouriteOpts.item_id)}` + (favouriteOpts.agent_id ? `&agent_id=${encodeURIComponent(favouriteOpts.agent_id)}` : '');
      API.get(`/v1/share?${qs}`).then(d => {
        if (!d || d.error) return;
        const vis = favouriteOpts.item_type === 'artifact' ? (d.effective_visibility || 'private') : (d.visibility || 'private');
        const pillHtml = (typeof shareVisibilityPillHtml === 'function')
          ? shareVisibilityPillHtml(vis, (d.extra_members || []).length, d.owner_team_name) : '';
        if (pillHtml) {
          const span = document.createElement('span');
          span.innerHTML = pillHtml;
          const pill = span.firstChild;
          if (pill) { pill.style.order = '-1'; pill.style.marginRight = '4px'; right.insertBefore(pill, right.firstChild); }
        }
      }).catch(() => {});
    }
  }
}


/* ═══════════════════════════════════════════════════════════
   AGENT SELECTION
   ═══════════════════════════════════════════════════════════ */
function selectAgent(agentName) {
  state.activeAgentId = agentName;
  state.ensureAgentChat(agentName);

  // Update UI
  const agentSel = document.getElementById('agent-selector-name');
  if (agentSel) agentSel.textContent = agentName;

  // Update model selector
  const chat = state.activeChat;
  updateModelSelectorDisplay(chat.model);

  // Update sidebar agent display
  updateSbAgentDisplay();

  // Load sessions in background
  loadAgentSessions(agentName);
}

async function loadAgentSessions(agentId) {
  try {
    const data = await API.getSessionsForAgent(agentId);
    state.agentSessions[agentId] = {
      sessions: data.sessions || [],
      loaded: true,
    };
    renderRecentChats();
    // Sync title + summary from session data. Title is primary; summary is
    // the LLM-generated synopsis surfaced via hover + the in-chat block.
    // Refresh both on every poll so summary updates land without the user
    // reloading. The summary block's open/closed state is decoupled (lives
    // on chat._summaryOpen) — a fresh summary never auto-expands.
    const chat = state.activeChat;
    if (chat?.sessionId && agentId === state.activeAgentId) {
      const sess = (data.sessions || []).find(s => (s.id || s.session_id) === chat.sessionId);
      if (sess) {
        let viewDirty = false;
        if (sess.title && !chat.chatTitle) {
          chat.chatTitle = sess.title;
          viewDirty = true;
        }
        const newSummary = sess.summary || '';
        if (newSummary !== (chat.chatSummary || '')) {
          chat.chatSummary = newSummary;
          viewDirty = true;
        }
        if (viewDirty && state.currentView === 'chat') updateChatView();
        const memVal = parseInt(sess.save_to_memory) || 0;
        chat.saveToMemory = memVal === 1;
        chat.memoryMode = memVal === 1 ? 'on' : memVal === 2 ? 'auto' : 'off';
        chat.cavemanMode = parseInt(sess.caveman_mode) || 0;
        // Goal-Modus: only adopt server state while NOT streaming — during a
        // goal loop the SSE events are the fresher source (avoids a poll race
        // flapping the badge mid-iteration).
        if (!chat.streaming) {
          chat.goalText = sess.goal_text || '';
          chat.goalStatus = sess.goal_status || '';
        }
      }
    }
  } catch(e) { console.error('loadAgentSessions:', e); }
}

async function loadAgentProjects(agentId) {
  try {
    const data = await API.getProjects(agentId);
    state.agentProjects[agentId] = data.projects || [];
  } catch(e) { console.error('loadAgentProjects:', e); }
}

/* ═══════════════════════════════════════════════════════════
   MODEL SELECTION
   ═══════════════════════════════════════════════════════════ */
function updateModelSelectorDisplay(modelId) {
  const name = modelShortName(modelId);
  let tip = modelDescription(modelId);
  // On Auto, the composer label stays "✨ Smart (…)"; the per-turn pick + the
  // reason behind it surface only in the tooltip.
  if (isAutoModel(modelId)) {
    const chat = state.activeChat;
    if (chat?.autoReason) tip = chat.autoReason;
  }
  for (const id of ['model-selector-name', 'welcome-model-name', 'chat-model-name', 'project-model-name']) {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = name;
      // Set on the parent button when present so the whole click target shows the tooltip.
      const target = el.closest('button') || el;
      if (tip) target.title = tip; else target.removeAttribute('title');
    }
  }
  refreshThinkingButton();
  if (typeof refreshResearchModeButton === 'function') refreshResearchModeButton();
  if (typeof updateStatusBar === 'function') updateStatusBar();
}

// Model locality helper — prefers the server-derived is_local flag exposed via
// /v1/models/config, falls back to a base_url sniff for older server builds.
function isModelLocal(mid) {
  const cfg = state.modelsConfig?.models?.[mid] || {};
  if (typeof cfg.is_local === 'boolean') return cfg.is_local;
  const u = (cfg.base_url || '').toLowerCase();
  if (!u) return false;
  if (u.includes('localhost') || u.includes('127.0.0.1') || u.includes('0.0.0.0')) return true;
  const host = (u.match(/\/\/([^\/:]+)/) || [])[1] || '';
  if (host.startsWith('192.168.') || host.startsWith('10.')) return true;
  if (host.startsWith('172.')) {
    const n = parseInt(host.split('.')[1], 10);
    if (n >= 16 && n <= 31) return true;
  }
  return false;
}

// Scan the chat's loaded history for PII and cache the worst-action result.
// Cache key is the message count so it refreshes on every new turn; callers
// can force a re-scan by setting `chat._piiHistoryScanLen = -1`.
//
// Two-layer scan:
//   1. Local regex (sync) — covers email/IBAN/credit cards/national IDs/etc.
//      Result is returned immediately.
//   2. Server-side scan (async, fire-and-forget) — runs the full
//      `_pii_scan_text` pipeline including spaCy German NER which surfaces
//      soft-PII (name / address / organisation) the browser scanner can't
//      see. When it returns it merges into `_piiHistoryCounts` and re-fires
//      `updatePIIBadge()` so the composer button surfaces accordingly.
function piiHistoryHasFindings(chat) {
  if (!chat || !Array.isArray(chat.messages)) return false;
  const len = chat.messages.length;
  if (!len) return false;
  if (chat._piiHistoryScanLen !== len) {
    // 9.200.0: detection is SERVER-ONLY (the browser regex scanner was
    // removed). The history badge now reflects only the async server scan
    // (regex + spaCy NER). Until that returns for this turn count the badge
    // stays at its previous state — no instant local interim. Empty local
    // caches keep _piiHistoryMergeAndCache's "not yet current" branch quiet.
    chat._piiHistoryCountsLocal = {};
    chat._piiHistoryWorstLocal = 'ignore';
    chat._piiHistoryHasLocal = false;
    chat._piiHistoryScanLen = len;
    _piiHistoryMergeAndCache(chat);
    // Kick the async server scan unless one's already in flight or we've
    // already got a fresh result for this turn count.
    if (chat._piiHistoryServerScanLen !== len && !chat._piiHistoryServerInFlight) {
      _piiHistoryFetchServer(chat, len);
    }
  }
  return !!chat._piiHistoryHas;
}

function _piiHistoryMergeAndCache(chat) {
  // The server scan (regex + NER) is a strict SUPERSET of the client regex.
  // Once it has run for this chat, it is the SINGLE source of truth — we do NOT
  // union with the local counts. Unioning double-counted the same value because
  // client + server label the same rule differently ("E-Mail-Adresse" vs
  // "Email address"), so a label-keyed merge never collapsed them. The local
  // counts are only the interim display until the server result lands.
  // "server result is current" = a server scan ran AND for this turn count.
  // On a brand-new turn the local scan shows first; once the server scan for
  // that same turn count returns, it takes over (superset, no double-count).
  const sLen = (chat._piiHistoryServerScanLen != null) ? chat._piiHistoryServerScanLen : -1;
  const serverCurrent = sLen >= 0 && sLen === (chat._piiHistoryScanLen ?? -2);
  if (serverCurrent) {
    chat._piiHistoryCounts = Object.assign({}, chat._piiHistoryCountsServer || {});
    chat._piiHistoryWorst = chat._piiHistoryWorstServer || 'ignore';
  } else {
    chat._piiHistoryCounts = Object.assign({}, chat._piiHistoryCountsLocal || {});
    chat._piiHistoryWorst = chat._piiHistoryWorstLocal || 'ignore';
  }
  chat._piiHistoryHas = Object.keys(chat._piiHistoryCounts).length > 0;
}

function _piiHistoryFetchServer(chat, expectLen) {
  // Scanner disabled → no server-side NER round-trip ("PII check" truly does
  // nothing when the admin turned the feature off).
  if (state.piiScannerEnabled === false) return;
  // No sessionId = new chat not yet persisted. Server has no history to
  // scan — local regex result is authoritative until the first send.
  if (!chat || !chat.sessionId) return;
  chat._piiHistoryServerInFlight = true;
  API.getSessionPiiHistorySummary(chat.sessionId).then((res) => {
    chat._piiHistoryServerInFlight = false;
    // Stale-result guard: if more turns have landed since the fetch fired,
    // mark this scan as not-yet-current so the next badge refresh re-fires.
    chat._piiHistoryServerScanLen = expectLen;
    if (!res || res.error) return;
    chat._piiHistoryCountsServer = res.counts || {};
    chat._piiHistoryWorstServer = res.worst_action || 'ignore';
    _piiHistoryMergeAndCache(chat);
    // Re-render the composer badge so a fresh NER hit (e.g. name+address)
    // flips the button visible without waiting on the next keystroke.
    if (state.activeChat === chat) {
      try { updatePIIBadge(); } catch (e) {}
    }
  }).catch(() => {
    chat._piiHistoryServerInFlight = false;
  });
}

function piiHistoryWorstAction(chat) {
  piiHistoryHasFindings(chat);
  return chat?._piiHistoryWorst || 'ignore';
}

// Collect the current GDPR tab form state into an outgoing config object.
// Returns the full gdpr_scanner body so the server can store it atomically.
function collectGdprFormConfig() {
  const enabled = document.getElementById('gdpr-enabled')?.checked !== false;
  const serverLog = document.getElementById('gdpr-serverlog')?.checked !== false;
  // server_block removed (9.195.0) — replaced by confidence thresholds.
  const confLowerRaw = parseFloat(document.getElementById('gdpr-conf-lower')?.value);
  const confUpperRaw = parseFloat(document.getElementById('gdpr-conf-upper')?.value);
  const confLower = Number.isFinite(confLowerRaw) ? confLowerRaw : 0.50;
  const confUpper = Number.isFinite(confUpperRaw) ? confUpperRaw : 0.85;
  const fallback = document.getElementById('gdpr-fallback')?.value || '';
  const bgPii = document.getElementById('gdpr-bg-pii-action')?.value || 'anonymise';
  const bgAsk = document.getElementById('gdpr-bg-ask-action')?.value || 'anonymise';
  const bgFail = document.getElementById('gdpr-bg-fail-action')?.value || 'swap_to_local';

  const categories = {};
  for (const sel of document.querySelectorAll('.gdpr-cat-action')) {
    const cat = sel.dataset.cat;
    if (!cat) continue;
    categories[cat] = { action: sel.value };
  }

  const rule_overrides = {};
  for (const sel of document.querySelectorAll('.gdpr-rule-override')) {
    const rid = sel.dataset.rule;
    if (rid && sel.value) rule_overrides[rid] = sel.value;
  }

  // Per-rule count_points [lo, hi] — write EVERY rule (full snapshot), the
  // count→score calibration. lo<hi enforced (hi auto-bumped to lo+1 if needed).
  // Blank/invalid lo → 1. These replaced the min_occurrences gate (9.195.0).
  const count_points = {};
  const _loByRule = {};
  for (const inp of document.querySelectorAll('.gdpr-rule-count-lo')) {
    const rid = inp.dataset.rule;
    if (!rid) continue;
    const n = parseInt((inp.value || '').trim(), 10);
    _loByRule[rid] = (Number.isFinite(n) && n >= 1) ? n : 1;
  }
  for (const inp of document.querySelectorAll('.gdpr-rule-count-hi')) {
    const rid = inp.dataset.rule;
    if (!rid) continue;
    const lo = _loByRule[rid] != null ? _loByRule[rid] : 1;
    let hi = parseInt((inp.value || '').trim(), 10);
    if (!Number.isFinite(hi) || hi <= lo) hi = lo + 1;
    count_points[rid] = [lo, hi];
  }

  const allowlistRaw = document.getElementById('gdpr-email-allowlist')?.value || '';
  const email_allowlist = allowlistRaw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);

  // Generic org/concept terms (9.393.3) — edited via the chip manager, held on
  // state.gdprGenericTerms. Single-word, lowercase, deduped (the server drops
  // multi-word entries; the filter is per capitalised token).
  const name_generic_terms = Array.isArray(state.gdprGenericTerms)
    ? [...new Set(state.gdprGenericTerms.map(t => String(t).trim().toLowerCase()).filter(Boolean))]
    : [];

  const webEgress = document.getElementById('gdpr-web-egress')?.value || 'refuse';

  return {
    enabled, server_log: serverLog,
    confidence_lower: confLower, confidence_upper: confUpper,
    default_local_fallback_model: fallback,
    background_pii_action: bgPii,
    background_ask_action: bgAsk,
    background_anonymise_fail_action: bgFail,
    web_egress: webEgress,
    categories, rule_overrides, count_points, email_allowlist,
    name_generic_terms,
  };
}

async function saveGdprConfig() {
  const btn = document.getElementById('gdpr-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Wird gespeichert...'; }
  try {
    const body = { gdpr_scanner: collectGdprFormConfig() };
    const r = await API.post('/v1/services/server', body);
    applyGdprConfigToScanner(r.gdpr_scanner);
    showToast('GDPR-Einstellungen gespeichert');
  } catch (e) {
    showToast('Fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Alle GDPR-Einstellungen speichern'; }
  }
  // Refresh the composer PII badge AFTER the save settles — guarded + outside
  // the try so a stale/cached client missing this global can never flip the
  // "gespeichert" success toast into a misleading "Fehlgeschlagen".
  if (typeof schedulePIIBadgeUpdate === 'function') schedulePIIBadgeUpdate();
}

function resetGdprCategories() {
  const defaults = (state.gdprCatalog && state.gdprCatalog.defaultCategoryActions) || {};
  for (const sel of document.querySelectorAll('.gdpr-cat-action')) {
    const cat = sel.dataset.cat;
    if (cat && defaults[cat]) {
      sel.value = defaults[cat];
    }
  }
  for (const sel of document.querySelectorAll('.gdpr-rule-override')) {
    sel.value = '';
  }
  showToast('Standardwerte wiederhergestellt — zum Übernehmen auf Speichern klicken');
}

async function _confirmResetGdprCategories() {
  if (!await showConfirm('Alle Kategorien und Überschreibungen auf Standardwerte zurücksetzen? (Hauptschalter und Allowlist bleiben erhalten.)')) return;
  resetGdprCategories();
}

/* ─── NER models pill (Settings → GDPR) ─── */

function _renderGdprNerPill(languages) {
  const host = document.getElementById('gdpr-ner-pill');
  if (!host) return;
  if (!languages || !languages.length) {
    host.innerHTML = `<div style="font-size:11px;color:var(--text-400);font-style:italic">Keine NER-Modelle registriert.</div>`;
    return;
  }
  host.innerHTML = languages.map(l => {
    const loaded = !!l.loaded;
    const failed = !!l.failed && !loaded;
    const statusColor = loaded ? 'var(--success,#16a34a)' : (failed ? 'var(--error,#dc2626)' : 'var(--text-400)');
    const statusText = loaded ? 'geladen' : (failed ? 'Laden fehlgeschlagen' : 'nicht geladen');
    const btnLabel = loaded ? 'Entladen' : 'Laden';
    const btnAction = loaded ? 'unload' : 'load';
    return `<div style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)">
      <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${statusColor};flex-shrink:0"></span>
      <span style="font-size:12px;color:var(--text-100);min-width:90px"><b>${esc(l.display)}</b></span>
      <code style="font-size:10px;color:var(--text-400)">${esc(l.model || '-')}</code>
      <span style="flex:1;font-size:11px;color:${statusColor}">${statusText}</span>
      <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="_gdprNerAction('${esc(btnAction)}','${esc(l.lang)}', this)">${btnLabel}</button>
    </div>`;
  }).join('');
}

async function refreshGdprNerPill() {
  try {
    const r = await API.get('/v1/gdpr/ner-models');
    _renderGdprNerPill(r.languages || []);
  } catch (e) {
    const host = document.getElementById('gdpr-ner-pill');
    if (host) host.innerHTML = `<div style="font-size:11px;color:var(--error)">NER-Status konnte nicht gelesen werden: ${esc(e.message || e)}</div>`;
  }
}

async function _gdprNerAction(action, lang, btn) {
  if (btn) { btn.disabled = true; btn.textContent = action === 'load' ? 'Wird geladen…' : 'Wird entladen…'; }
  try {
    const r = await API.post('/v1/gdpr/ner-models', { action, lang });
    _renderGdprNerPill(r.languages || []);
    if (r.status === 'load_failed') {
      showToast(`Laden von ${lang} fehlgeschlagen — server.error.log prüfen`, true);
    } else {
      showToast(`NER ${lang}: ${r.status}`);
    }
  } catch (e) {
    showToast('Fehlgeschlagen: ' + (e.message || e), true);
    refreshGdprNerPill();
  }
}

/* ─── Quota config save + helpers ─── */

async function saveQuotaConfig() {
  const limits = {};
  for (const role of ['admin', 'poweruser', 'user']) {
    limits[role] = {
      daily_usd: parseFloat(document.querySelector(`[data-quota-role="${role}"][data-quota-field="daily_usd"]`).value) || 0,
      cycle_usd: parseFloat(document.querySelector(`[data-quota-role="${role}"][data-quota-field="cycle_usd"]`).value) || 0,
    };
  }
  const body = {
    enabled: document.getElementById('q-enabled').checked,
    billing_cycle: document.getElementById('q-billing-cycle').value,
    cycle_start_day: parseInt(document.getElementById('q-start-day').value, 10) || 1,
    warn_pct: parseInt(document.getElementById('q-warn-pct').value, 10) || 70,
    block_pct: parseInt(document.getElementById('q-block-pct').value, 10) || 100,
    enforce_red: document.getElementById('q-enforce').value,
    default_local_fallback_model: document.getElementById('q-fallback').value || '',
    limits,
  };
  try {
    await API.post('/v1/quotas/config', body);
    showToast('Kontingent-Einstellungen gespeichert');
    QuotaMonitor.refresh();
    // Re-render the tab so the user list reflects new thresholds
    switchGeneralTab('quotas', document.querySelector('.modal-tab.active'));
  } catch (e) {
    showToast('Fehlgeschlagen: ' + (e.message || e), true);
  }
}

async function quotaEditOverride(userId, displayName) {
  // Quick prompt-driven override editor; full inline form would be heavier than worth it
  const dailyStr = await showPrompt(`Tageslimit für ${displayName} (USD; leer = Rollenstandard übernehmen; 0 = kein Limit):`, '');
  if (dailyStr === null) return;
  const cycleStr = await showPrompt(`Zykluslimit für ${displayName} (USD; leer = übernehmen; 0 = kein Limit):`, '');
  if (cycleStr === null) return;
  API.get('/v1/quotas/config').then(cfg => {
    const ov = Object.assign({}, cfg.user_overrides || {});
    const entry = {};
    if (dailyStr.trim() !== '') entry.daily_usd = parseFloat(dailyStr) || 0;
    if (cycleStr.trim() !== '') entry.cycle_usd = parseFloat(cycleStr) || 0;
    if (Object.keys(entry).length === 0) {
      delete ov[userId];
    } else {
      ov[userId] = entry;
    }
    return API.post('/v1/quotas/config', { user_overrides: ov });
  }).then(() => {
    showToast('Überschreibung aktualisiert');
    switchGeneralTab('quotas', document.querySelector('.modal-tab.active'));
  }).catch(e => showToast('Fehlgeschlagen: ' + (e.message || e), true));
}

async function quotaOpenUserBreakdown(userId, displayName) {
  const existing = document.getElementById('quota-breakdown-modal');
  if (existing) existing.remove();
  const div = document.createElement('div');
  div.id = 'quota-breakdown-modal';
  div.className = 'modal-overlay';
  div.style.display = 'flex';
  div.onclick = (e) => { if (e.target === div) div.remove(); };
  div.innerHTML = `<div class="modal-content" style="max-width:640px" onclick="event.stopPropagation()">
    <div class="modal-header"><div class="modal-title">Nutzungsaufschlüsselung — ${esc(displayName)}</div>
      <button class="modal-close" onclick="document.getElementById('quota-breakdown-modal').remove()">&times;</button>
    </div>
    <div class="modal-body" id="quota-breakdown-body"><div style="color:var(--text-300);text-align:center;padding:20px">Wird geladen…</div></div>
  </div>`;
  document.body.appendChild(div);
  try {
    const data = await API.get(`/v1/quotas/admin/breakdown?user_id=${encodeURIComponent(userId)}&days=30`);
    const body = document.getElementById('quota-breakdown-body');
    if (!body) return;
    const fmt = (v) => '$' + (v < 1 ? v.toFixed(3) : v.toFixed(2));
    const st = data.state || {};
    const perModel = (data.per_model || []).slice(0, 12);
    const daily = (data.daily || []).slice(0, 30);
    const totalCost = perModel.reduce((s, r) => s + (r.cost || 0), 0);
    body.innerHTML = `
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <div style="padding:10px 16px;background:var(--bg-200);border-radius:8px"><div style="font-size:11px;color:var(--text-400)">Zyklus</div><div style="font-size:18px;font-weight:600">${fmt(st.cycle?.used_usd||0)} <span style="font-size:11px;color:var(--text-400)">/ ${fmt(st.cycle?.limit_usd||0)}</span></div></div>
        <div style="padding:10px 16px;background:var(--bg-200);border-radius:8px"><div style="font-size:11px;color:var(--text-400)">Heute</div><div style="font-size:18px;font-weight:600">${fmt(st.daily?.used_usd||0)} <span style="font-size:11px;color:var(--text-400)">/ ${fmt(st.daily?.limit_usd||0)}</span></div></div>
        <div style="padding:10px 16px;background:var(--bg-200);border-radius:8px"><div style="font-size:11px;color:var(--text-400)">Rolle</div><div style="font-size:14px;font-weight:600;text-transform:capitalize">${esc(st.role||'')}</div></div>
      </div>
      <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:8px 0 4px">Nach Modell (aktueller Zyklus)</div>
      ${perModel.length ? perModel.map(r => {
        const pct = totalCost > 0 ? (r.cost / totalCost * 100) : 0;
        return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-bottom:1px solid var(--border-100)">
          <span style="flex:1;font-size:12px;color:var(--text-100);font-family:var(--font-mono)">${esc(modelShortName(r.model))}</span>
          <span style="font-size:11px;color:var(--text-400)">${(r.calls||0)} Aufrufe</span>
          <span style="font-size:11px;color:var(--text-400);min-width:36px;text-align:right">${pct.toFixed(0)}%</span>
          <span style="font-size:13px;font-weight:500;min-width:80px;text-align:right">${fmt(r.cost||0)}</span>
        </div>`;
      }).join('') : '<div style="color:var(--text-400);padding:8px 0">Keine Nutzung im aktuellen Zyklus.</div>'}
      <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:14px 0 4px">Letzte 30 Tage</div>
      ${daily.length ? daily.map(d => `<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;border-bottom:1px solid var(--border-100);font-size:12px">
        <span style="font-family:var(--font-mono);color:var(--text-200)">${esc(d.day || '')}</span>
        <span style="flex:1"></span>
        <span style="color:var(--text-400)">${(d.calls||0)} Aufrufe</span>
        <span style="color:var(--text-400)">${((d.tokens_in||0)+(d.tokens_out||0)).toLocaleString()} Token</span>
        <span style="font-weight:500;min-width:80px;text-align:right">${fmt(d.cost||0)}</span>
      </div>`).join('') : '<div style="color:var(--text-400);padding:8px 0">Keine Tagesdaten.</div>'}
    `;
  } catch (e) {
    document.getElementById('quota-breakdown-body').innerHTML =
      `<div style="color:var(--error);padding:16px">Fehlgeschlagen: ${esc(String(e))}</div>`;
  }
}

// Apply server-side gdpr_scanner config to client state. The browser-side
// PII scanner was removed in 9.200.0 — detection now runs ONLY on the server.
// This still mirrors the policy thresholds (advisory composer interlock) and
// caches the static PII catalog (rule→category map, labels, default actions)
// the Settings panel + chat-view labels render from. Call this anywhere
// state.pii* is updated from the /v1/services/status response.
function applyGdprConfigToScanner(gs) {
  gs = gs || {};
  state.piiScannerEnabled = gs.enabled !== false;
  // server_block removed (9.195.0). The confidence-band thresholds + per-rule
  // action now drive enforcement SERVER-SIDE (the scan endpoint returns a
  // disposition per finding). The client-side composer interlock is advisory:
  // it dims cloud models when the server reports a high-band block disposition.
  state.piiConfidenceLower = (gs.confidence_lower != null) ? gs.confidence_lower : 0.50;
  state.piiConfidenceUpper = (gs.confidence_upper != null) ? gs.confidence_upper : 0.85;
  state.piiLocalFallback = gs.default_local_fallback_model || '';
  // PII catalog from the server (replaces the deleted PIIScanner.ruleCategories
  // / categoryLabels / defaultCategoryActions / rules[*].label). Kept on state
  // so the Settings GDPR panel + chat-render labels read it. Fall back to the
  // prior cached catalog if the server omitted it (older server / partial resp).
  const cat = gs.catalog;
  if (cat && typeof cat === 'object') {
    state.gdprCatalog = {
      ruleCategories: cat.rule_categories || {},
      categoryLabels: cat.category_labels || {},
      defaultCategoryActions: cat.default_category_actions || {},
      ruleLabels: cat.rule_labels || {},
    };
  }
  // Live policy (categories / overrides / allowlist) used to render the
  // Settings panel's CURRENT selections — distinct from the static catalog.
  state.gdprPolicy = {
    enabled: state.piiScannerEnabled,
    categories: gs.categories || null,
    ruleOverrides: gs.rule_overrides || {},
    emailAllowlist: Array.isArray(gs.email_allowlist) ? gs.email_allowlist : [],
  };
  // Generic org/concept terms — held on state as the edit buffer for the
  // chip-manager widget (search + add/remove). Sorted, lowercased, deduped.
  state.gdprGenericTerms = Array.isArray(gs.name_generic_terms)
    ? [...new Set(gs.name_generic_terms.map(t => String(t).trim().toLowerCase()).filter(Boolean))].sort()
    : [];
  // Drop any cached per-chat history scans — action changes invalidate them.
  for (const c of (state.chats || [])) {
    if (c) c._piiHistoryScanLen = -1;
  }
}

// Catalog accessors — single lookup point so call sites don't reach into
// state.gdprCatalog shape directly. Safe when the catalog hasn't loaded yet.
function gdprRuleCategory(ruleId) {
  return (state.gdprCatalog && state.gdprCatalog.ruleCategories &&
    state.gdprCatalog.ruleCategories[ruleId]) || 'personal';
}
function gdprCategoryLabel(cat) {
  return (state.gdprCatalog && state.gdprCatalog.categoryLabels &&
    state.gdprCatalog.categoryLabels[cat]) || cat || '';
}
function gdprRuleLabel(ruleId) {
  return (state.gdprCatalog && state.gdprCatalog.ruleLabels &&
    state.gdprCatalog.ruleLabels[ruleId]) || ruleId || '';
}

// SINGLE source of truth for resetting a chat's GDPR/PII state to defaults.
// The per-agent chat object is REUSED across conversations (state.ensureAgentChat
// returns the same object), so without an explicit reset a fresh chat inherits
// the previous conversation's analysis (decisions, history scans, sticky consent).
// Called by BOTH newChat() and openSession() so "fresh chat = no GDPR leftovers"
// can never drift as new _pii* fields are added — add the field HERE only.
function resetChatGdprState(chat) {
  if (!chat) return;
  // Sticky consent / mapping (per-session, never inherited).
  chat.gdprActionPref = '';
  chat.gdprFeedbackAsk = false;
  chat.hasGdprMapping = false;
  // Per-finding review decisions (already-analysed + FP-for-chat).
  chat._piiDecisions = {};
  // History-scan caches (client regex + server NER), worst-action, in-flight.
  chat._piiHistoryScanLen = -1;
  chat._piiHistoryServerScanLen = -1;
  chat._piiHistoryServerInFlight = false;
  chat._piiHistoryHas = false;
  chat._piiHistoryHasLocal = false;
  chat._piiHistoryCounts = {};
  chat._piiHistoryCountsLocal = {};
  chat._piiHistoryCountsServer = {};
  chat._piiHistoryWorst = 'ignore';
  chat._piiHistoryWorstLocal = 'ignore';
  chat._piiHistoryWorstServer = 'ignore';
}

// Composer model-restriction gate. As of 9.196.0 the PII-driven restriction is
// REMOVED — PII findings NO LONGER dim cloud models or auto-swap to local. PII
// enforcement now lives entirely in the pre-send dialog + the server-side
// confidence bands (anonymise / ask / act), so locking the model picker up front
// was redundant and got in the way. The ONLY remaining composer restriction is
// document CLASSIFICATION (ARL §1.11 strict / force_local on attachments) — a
// hard regulatory rule, intentionally kept.
function piiBlockActive(chat) {
  chat = chat || state.activeChat;
  if (!chat) return false;
  return classificationBlockActive(chat);
}

// Mirrors piiBlockActive but for ARL classification levels. Returns true
// when any pending attachment carries an effective_action of 'block' or
// 'force_local' — both flip the composer to local-only.
function classificationBlockActive(chat) {
  chat = chat || state.activeChat;
  if (!chat) return false;
  if (sessionStorage.getItem('cls-suppress:' + (chat.sessionId || '_new'))) return false;
  for (const f of (state._pendingFiles || [])) {
    const cls = (f.scan && f.scan.classification) || null;
    if (!cls) continue;
    const act = cls.effective_action;
    if (act === 'block' || act === 'force_local') return true;
  }
  return false;
}

// True when any pending attachment is classified 'strict' AND the policy
// is hard-block (the strict-always-block invariant). The send modal then
// only offers Cancel, no swap-to-local.
function classificationStrictBlockActive(chat) {
  chat = chat || state.activeChat;
  if (!chat) return false;
  for (const f of (state._pendingFiles || [])) {
    const cls = (f.scan && f.scan.classification) || null;
    if (!cls) continue;
    if (cls.final_level === 'strict' && cls.effective_action === 'block') return true;
  }
  return false;
}

// Swap the current chat to the configured local fallback (or first local
// model). Returns true if a swap happened. Safe to call idempotently.
// As of 9.196.0 this is called ONLY when the user EXPLICITLY chooses "Lokales
// Modell verwenden" in the pre-send dialog (no automatic PII-driven swap any
// more) — so it no longer self-guards on piiBlockActive; the caller decides.
function piiEnsureLocalModel() {
  const chat = state.activeChat;
  if (!chat) return false;
  const cur = chat.model || '';
  if (cur && isModelLocal(cur)) return false;
  const mc = state.modelsConfig?.models || {};
  const fallback = state.piiLocalFallback;
  let target = '';
  if (fallback && modelHasCapability(fallback, 'chat') && isModelLocal(fallback)) {
    target = fallback;
  } else {
    const locals = enabledModelsWithCapability('chat').filter(([id]) => isModelLocal(id));
    if (locals.length) target = locals[0][0];
  }
  if (!target) return false;
  const oldModel = cur;
  chat.model = target;
  updateModelSelectorDisplay(target);
  if (oldModel !== target) {
    try { stopWarmupPoll(chat); } catch(e) {}
    updateStatusBar();
    if (chat.messages.length === 0) {
      // Session not yet created — drop any stale id and let the next send
      // create a fresh one. Don't pre-create: every model switch on an
      // unsent chat would otherwise leave an orphan session row.
      chat.sessionId = null;
    } else if (chat.sessionId) {
      API.post(`/v1/sessions/${chat.sessionId}/warmup`, {model: target}).then(d => {
        if (d.warmup) startWarmupPoll(chat);
      }).catch(() => {});
    }
  }
  return true;
}

function toggleModelDropdown(event) {
  event.stopPropagation();
  closeAllDropdowns();

  const btn = event.currentTarget;
  const rect = btn.getBoundingClientRect();

  const dd = document.createElement('div');
  dd.className = 'dropdown-menu';
  dd.id = 'model-dropdown';
  dd.style.position = 'fixed';
  dd.style.right = (window.innerWidth - rect.right) + 'px';
  dd.style.overflowY = 'auto';
  // Position above the button, clamped so top >= 8px
  const availHeight = rect.top - 12;
  dd.style.maxHeight = Math.min(320, availHeight) + 'px';
  dd.style.bottom = (window.innerHeight - rect.top + 4) + 'px';

  const currentModel = state.activeChat?.model || '';
  const localOnly = piiBlockActive(state.activeChat);

  // Build list from modelsConfig — enabled, capability=chat, sorted by priority.
  // When PII+block is active, restrict to local models.
  const enabledModels = enabledModelsWithCapability('chat')
    .filter(([id]) => !localOnly || isModelLocal(id));

  if (localOnly) {
    const hdr = document.createElement('div');
    hdr.style.cssText = 'padding:8px 12px;font-size:11px;color:#92400e;background:#fef3c7;border-bottom:1px solid #fde68a;line-height:1.35';
    hdr.innerHTML = '<b>Personenbezogene Daten erkannt</b><br>Während die GDPR-Sperre aktiv ist, sind nur lokale Modelle auswählbar.';
    dd.appendChild(hdr);
  }

  // "Smart" auto-routing — the server picks the best-fitting model per turn.
  // Two modes that differ only by candidate pool. "Smart (Cloud)" is hidden
  // under a GDPR local-only block (it can't guarantee a local pick); "Smart
  // (Lokal)" is always shown since its pool is local-only — safe even under
  // the GDPR lock (legacy "auto" still maps to Cloud server-side).
  const _addAutoItem = (val, label) => {
    const it = document.createElement('div');
    const isActive = (currentModel === val) || (val === 'auto-cloud' && currentModel === 'auto');
    it.className = 'dropdown-item' + (isActive ? ' active' : '');
    it.title = modelDescription(val);
    it.innerHTML = `
      <span class="dd-check">${isActive ? '&#10003;' : ''}</span>
      <span class="dd-label">${esc(label)}</span>
    `;
    it.onclick = () => { selectModel(val); closeAllDropdowns(); };
    dd.appendChild(it);
  };
  if (!localOnly) _addAutoItem('auto-cloud', '✨ Smart (Cloud)');
  _addAutoItem('auto-local', '✨ Smart (Lokal)');
  // Experten-Gremium (MoA / Mixture of Agents): Smart routing +
  // classification-gated reference fan-out. Cloud-only (references are cloud
  // models) → hidden under the GDPR local-only lock, and hidden entirely while
  // the admin has it disabled or the pool is empty (/v1/status → moa_enabled).
  if (!localOnly && state.serverInfo?.moa_enabled) _addAutoItem('moa', '🧬 Experten-Gremium');

  for (const [mid, cfg] of enabledModels) {
    const item = document.createElement('div');
    item.className = 'dropdown-item' + (mid === currentModel ? ' active' : '');
    const label = modelShortName(mid);
    item.title = modelDescription(mid);
    item.innerHTML = `
      <span class="dd-check">${mid === currentModel ? '&#10003;' : ''}</span>
      <span class="dd-label">${esc(label)}</span>
    `;
    item.onclick = () => { selectModel(mid); closeAllDropdowns(); };
    dd.appendChild(item);
  }

  document.body.appendChild(dd);
  document.addEventListener('click', closeAllDropdowns, {once: true});
}

// Apply a model selection to the active chat. `mid` may be a concrete model id
// or the synthetic "auto" directive (server picks per turn). Warmup is skipped
// for "auto" since there's no concrete model to pre-load.
function selectModel(mid) {
  if (!state.activeChat) return;
  const chat = state.activeChat;  // capture by value for async callbacks
  const oldModel = chat.model;
  chat.model = mid;
  // Drop any stale Auto pick when leaving Auto (or re-selecting it fresh).
  if (!isAutoModel(mid)) { chat.autoPicked = null; chat.autoReason = ''; }
  updateModelSelectorDisplay(mid);
  // New model may have a different thinking_format — demote the composer's
  // saved thinking_level when it's no longer valid and refresh the icon.
  try { _ensureValidThinkingLevel(); } catch(_) {}
  refreshThinkingButton();
  if (typeof refreshResearchModeButton === 'function') refreshResearchModeButton();
  if (mid === oldModel) return;
  stopWarmupPoll(chat);
  updateStatusBar();
  // GDPR marks (amber/red) are hidden while a LOCAL model is selected (nothing
  // leaves the machine → nothing to mark). If the locality changed, re-render
  // the messages so the marks appear/disappear immediately (9.205.2).
  try {
    if (typeof isModelLocal === 'function'
        && isModelLocal(oldModel) !== isModelLocal(mid)
        && (chat.messages || []).length
        && typeof renderMessages === 'function') {
      renderMessages();
    }
  } catch (_) {}
  if (chat.messages.length === 0) {
    // Drop stale session-id; let the next send create one. No pre-create —
    // orphans stack up otherwise.
    chat.sessionId = null;
  } else if (chat.sessionId && !isAutoModel(mid)) {
    // Trigger warmup on existing session with new concrete model.
    API.post(`/v1/sessions/${chat.sessionId}/warmup`, {model: mid}).then(data => {
      if (data.warmup) startWarmupPoll(chat);
    }).catch(() => {});
  }
}

function toggleAgentDropdown(event) {
  event.stopPropagation();
  closeAllDropdowns();

  const btn = event.currentTarget;
  const rect = btn.getBoundingClientRect();

  const dd = document.createElement('div');
  dd.className = 'dropdown-menu';
  dd.id = 'agent-dropdown';
  dd.style.position = 'fixed';
  dd.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
  dd.style.left = rect.left + 'px';

  for (const agent of state.agents) {
    const item = document.createElement('div');
    const aid = agent.id || agent.name;
    item.className = 'dropdown-item' + (aid === state.activeAgentId ? ' active' : '');
    const display = agent.display_name || aid;
    item.innerHTML = `
      <span class="dd-check">${aid === state.activeAgentId ? '&#10003;' : ''}</span>
      <span class="dd-label">${esc(display)}</span>
      <span class="dd-meta">${esc(modelShortName(agent.model))}</span>
    `;
    item.onclick = () => {
      selectAgent(aid);
      closeAllDropdowns();
    };
    dd.appendChild(item);
  }

  document.body.appendChild(dd);
  document.addEventListener('click', closeAllDropdowns, {once: true});
}

function closeAllDropdowns() {
  document.querySelectorAll('.dropdown-menu').forEach(d => d.remove());
}

/* ═══════════════════════════════════════════════════════════
   SIDEBAR
   ═══════════════════════════════════════════════════════════ */
/* ── Sidebar Agent Dropdown ── */
function toggleSbAgentDropdown() {
  const dropdown = document.getElementById('sb-agent-dropdown');
  const selector = document.getElementById('sb-agent-selector');
  if (!dropdown || !selector) return;  // selector removed (single-agent deployment)
  const isOpen = !dropdown.classList.contains('hidden');

  if (isOpen) {
    dropdown.classList.add('hidden');
    selector.classList.remove('open');
    return;
  }

  // Build dropdown content using team structure
  let html = '';
  const ts = state.teamStructure;

  function agentRow(agent, teamLabel) {
    const aid = agent.id || agent.name;
    const display = agent.display_name || aid;
    const isActive = aid === state.activeAgentId;
    const desc = agent.description || modelShortName(agent.model);
    const badge = teamLabel
      ? `<span style="font-size:9px;font-family:var(--font-mono);padding:1px 5px;border-radius:4px;background:var(--bg-200);color:var(--text-400);white-space:nowrap">${esc(teamLabel)}</span>`
      : '';
    return `
      <div class="sb-agent-dropdown-item${isActive ? ' active' : ''}" onclick="switchToAgent('${esc(aid)}')">
        <div class="ad-info">
          <div class="ad-name">${esc(display)}</div>
          <div class="ad-desc">${esc(desc)}</div>
        </div>
        ${badge}
        <span style="font-size:10px;font-family:var(--font-mono);color:var(--text-400);white-space:nowrap">${esc(aid)}</span>
        <span class="ad-check">${isActive ? '&#10003;' : ''}</span>
      </div>`;
  }

  // 1. Main agent first
  if (ts.main) {
    html += agentRow(ts.main, null);
  }

  // 2. Teams — header + members
  if (ts.teams) {
    for (const [teamId, team] of Object.entries(ts.teams)) {
      html += `<div style="padding:8px 10px 4px;font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em">${esc(team.name || teamId)}</div>`;
      for (const member of (team.members || [])) {
        const isHead = member.is_team_head;
        html += agentRow(member, isHead ? 'Leitung' : 'Mitglied');
      }
    }
  }

  // 3. Standalone agents
  if (ts.standalone?.length) {
    html += `<div style="padding:8px 10px 4px;font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em">Eigenständig</div>`;
    for (const agent of ts.standalone) {
      html += agentRow(agent, null);
    }
  }

  // Fallback: if no team structure loaded, use flat list
  if (!html) {
    for (const agent of state.agents) {
      html += agentRow(agent, null);
    }
  }

  dropdown.innerHTML = html;
  dropdown.classList.remove('hidden');
  selector.classList.add('open');

  // Close on outside click
  setTimeout(() => {
    document.addEventListener('click', function closeDd(e) {
      if (!dropdown.contains(e.target) && !selector.contains(e.target)) {
        dropdown.classList.add('hidden');
        selector.classList.remove('open');
        document.removeEventListener('click', closeDd);
      }
    });
  }, 0);
}

function switchToAgent(agentId) {
  // Close dropdown (selector may be absent in single-agent deployment)
  document.getElementById('sb-agent-dropdown')?.classList.add('hidden');
  document.getElementById('sb-agent-selector')?.classList.remove('open');

  selectAgent(agentId);

  // Update sidebar agent display
  updateSbAgentDisplay();

  // Reload sidebar content for this agent
  loadAgentSessions(agentId);

  // If on welcome, stay; if on chat with no session, go to welcome
  if (state.currentView === 'chats') {
    loadChatsList();
  } else if (state.currentView === 'projects') {
    loadProjectsList();
  } else if (state.currentView === 'chat' && !state.activeChat?.sessionId) {
    navigateTo('welcome');
  }
}

function updateSbAgentDisplay() {
  // The sidebar agent selector was removed (single-agent deployment); only the
  // welcome greeting still needs refreshing. Guard the selector elements in
  // case they are ever reintroduced.
  const agent = state.agents.find(a => (a.id || a.name) === state.activeAgentId);
  if (agent) {
    const aid = agent.id || agent.name;
    const display = agent.display_name || aid;
    const nameEl = document.getElementById('sb-agent-selector-name');
    const iconEl = document.getElementById('sb-agent-avatar-icon');
    if (nameEl) nameEl.textContent = display;
    if (iconEl) iconEl.textContent = aid;
  }

  refreshWelcomeGreeting();
}

// Build "Good morning, Alex" using time-of-day + the user's greeting name.
// Falls back to display_name → username; if the user is not logged in (auth
// disabled or pre-login), shows just "Good morning".
function refreshWelcomeGreeting() {
  const greetingEl = document.getElementById('welcome-greeting-text');
  if (!greetingEl) return;
  const hour = new Date().getHours();
  let timeLabel = 'Guten Abend';
  if (hour < 12) timeLabel = 'Guten Morgen';
  else if (hour < 18) timeLabel = 'Guten Tag';
  const u = state.authUser;
  let name = '';
  if (u) {
    const prefs = u.preferences || {};
    name = (prefs.greeting_name || '').trim()
      || (u.display_name || '').trim()
      || (u.username || '').trim();
  }
  greetingEl.textContent = name ? `${timeLabel}, ${name}` : timeLabel;
}

function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  sb.classList.toggle('collapsed');
  localStorage.setItem('sidebar-collapsed', sb.classList.contains('collapsed') ? '1' : '0');
}

/* ── Sidebar width resize (drag the right-edge handle) ── */
const SIDEBAR_WIDTH_MIN = 220;
const SIDEBAR_WIDTH_MAX = 480;
const SIDEBAR_WIDTH_DEFAULT = 288;

function applySidebarWidth(px) {
  const w = Math.max(SIDEBAR_WIDTH_MIN, Math.min(SIDEBAR_WIDTH_MAX, px));
  document.documentElement.style.setProperty('--sidebar-width', w + 'px');
  return w;
}

// Restore persisted width on load (called from init).
function initSidebarWidth() {
  const saved = parseInt(localStorage.getItem('sidebar-width') || '', 10);
  if (saved) applySidebarWidth(saved);
}

function startSidebarResize(event) {
  event.preventDefault();
  const sb = document.getElementById('sidebar');
  if (!sb || sb.classList.contains('collapsed')) return;
  const startX = event.clientX;
  const startW = sb.getBoundingClientRect().width;
  sb.classList.add('resizing');
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';

  const onMove = (e) => {
    applySidebarWidth(startW + (e.clientX - startX));
  };
  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    sb.classList.remove('resizing');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const finalW = Math.round(sb.getBoundingClientRect().width);
    localStorage.setItem('sidebar-width', String(finalW));
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

// Collapsable sidebar sections: Navigate / Favourites / Recent.
// Each section's open/closed state is persisted under sb-section-<id>; default
// is open. Open sections share remaining vertical space via flex: 1 1 0;
// collapsed sections shrink to header height (.collapsed → flex: 0 0 auto).
function _sidebarSectionEl(id) {
  return document.getElementById('sb-section-' + id);
}
function toggleSidebarSection(id) {
  const el = _sidebarSectionEl(id);
  if (!el) return;
  el.classList.toggle('collapsed');
  localStorage.setItem('sb-section-' + id, el.classList.contains('collapsed') ? '0' : '1');
}
function restoreSidebarSections() {
  for (const id of ['recent']) {
    const el = _sidebarSectionEl(id);
    if (!el) continue;
    const saved = localStorage.getItem('sb-section-' + id);
    // Default: all sections open. Only collapse when explicitly saved as '0'.
    if (saved === '0') el.classList.add('collapsed');
    else el.classList.remove('collapsed');
  }
}

function openMobileSidebar() {
  document.getElementById('sidebar').classList.add('mobile-open');
  document.getElementById('sidebar-backdrop').classList.add('active');
}

function closeMobileSidebar() {
  document.getElementById('sidebar').classList.remove('mobile-open');
  document.getElementById('sidebar-backdrop').classList.remove('active');
}

// ── Responsive chrome ──────────────────────────────────────────────
// Three optimised tiers — phone / tablet (iPad) / desktop — whose pixel
// edges MUST match the @media blocks in main.css:
//   phone   : width <= 768  (sidebar is a slide-in drawer; right-panel overlays)
//   tablet  : 769..1024     (sidebar inline; right-panel overlays, not a column)
//   desktop : width > 1024  (full 3-column layout)
// On phones the sidebar drawer is reachable ONLY via the hamburger (hidden on
// wider tiers), so we toggle it here and reset a slid-out drawer on resize so
// a rotate never strands the drawer open over the content.
const MOBILE_BREAKPOINT = 768;
const TABLET_BREAKPOINT = 1024;

function isMobileViewport() {
  return window.innerWidth <= MOBILE_BREAKPOINT;
}
function isTabletViewport() {
  return window.innerWidth > MOBILE_BREAKPOINT && window.innerWidth <= TABLET_BREAKPOINT;
}

function syncMobileChrome() {
  const mobile = isMobileViewport();
  const tablet = isTabletViewport();
  const burger = document.getElementById('mobile-hamburger');
  if (burger) burger.classList.toggle('hidden', !mobile);
  document.body.classList.toggle('is-mobile', mobile);
  document.body.classList.toggle('is-tablet', tablet);
  if (!mobile) {
    // Leaving mobile width: ensure the drawer + its backdrop are reset so
    // the sidebar shows inline again on tablet/desktop.
    closeMobileSidebar();
  }
}

// Debounced resize so orientation changes / soft-keyboard resizes are cheap.
let _mobileChromeRaf = null;
window.addEventListener('resize', () => {
  if (_mobileChromeRaf) return;
  _mobileChromeRaf = requestAnimationFrame(() => {
    _mobileChromeRaf = null;
    syncMobileChrome();
  });
});

/* ═══════════════════════════════════════════════════════════
   RECENT-CHATS FILTER (sidebar "Zuletzt verwendet")
   ═══════════════════════════════════════════════════════════ */
// Filter state persisted to localStorage. Each control cycles through its
// options in order when its row is clicked (label left, value right — matches
// the official Claude.ai popover).
const RECENT_FILTER_DEFS = {
  type:   { label: 'Typ',              options: [['all','Alle'], ['chats','Chats'], ['tasks','Aufgaben']] },
  status: { label: 'Status',           options: [['active','Aktiv'], ['archived','Archiviert'], ['all','Alle']] },
  recency:{ label: 'Letzte Aktivität', options: [['all','Alle'], ['today','Heute'], ['7d','7 Tage'], ['30d','30 Tage']] },
  group:  { label: 'Gruppieren nach',  options: [['none','Keine'], ['date','Datum']] },
};
const RECENT_FILTER_DEFAULT = { type: 'all', status: 'active', recency: 'all', group: 'none' };

function getRecentFilter() {
  try {
    const raw = JSON.parse(localStorage.getItem('recentChatsFilter') || '{}');
    return { ...RECENT_FILTER_DEFAULT, ...raw };
  } catch (_) { return { ...RECENT_FILTER_DEFAULT }; }
}
function setRecentFilter(f) {
  try { localStorage.setItem('recentChatsFilter', JSON.stringify(f)); } catch (_) {}
}
function recentFilterLabel(key, value) {
  const opt = (RECENT_FILTER_DEFS[key]?.options || []).find(o => o[0] === value);
  return opt ? opt[1] : value;
}
function isRecentFilterActive(f) {
  return f.type !== RECENT_FILTER_DEFAULT.type
    || f.status !== RECENT_FILTER_DEFAULT.status
    || f.recency !== RECENT_FILTER_DEFAULT.recency
    || f.group !== RECENT_FILTER_DEFAULT.group;
}

// ── Run-status filter (scheduled runs + workflow executions) ──
// Runs aren't sessions, so the chat filter (Typ/Status/Letzte Aktivität) doesn't
// map. In those views the same filter button opens a smaller run-status +
// recency menu. Persisted separately so it doesn't collide with the chat filter.
const RUN_FILTER_DEFS = {
  status:  { label: 'Status',           options: [['all','Alle'], ['success','Erfolg'], ['error','Fehler'], ['running','Läuft']] },
  recency: { label: 'Letzte Aktivität', options: [['all','Alle'], ['today','Heute'], ['7d','7 Tage'], ['30d','30 Tage']] },
};
const RUN_FILTER_DEFAULT = { status: 'all', recency: 'all' };
function getRunFilter() {
  try {
    const raw = JSON.parse(localStorage.getItem('recentRunsFilter') || '{}');
    return { ...RUN_FILTER_DEFAULT, ...raw };
  } catch (_) { return { ...RUN_FILTER_DEFAULT }; }
}
function setRunFilter(f) {
  try { localStorage.setItem('recentRunsFilter', JSON.stringify(f)); } catch (_) {}
}
function runFilterLabel(key, value) {
  const opt = (RUN_FILTER_DEFS[key]?.options || []).find(o => o[0] === value);
  return opt ? opt[1] : value;
}
function isRunFilterActive(f) {
  return f.status !== RUN_FILTER_DEFAULT.status || f.recency !== RUN_FILTER_DEFAULT.recency;
}
// Normalise a run's raw status string to one of success|error|running.
function _runStatusClass(raw) {
  if (raw === 'running') return 'running';
  if (raw === 'success' || raw === 'completed') return 'success';
  return 'error';  // failed, timeout, cancelled, unknown, …
}
// Filter a run list by the run-status + recency filter. `startedAtMs(run)`
// returns the run's start time in epoch ms (call site knows the field).
function applyRunFilter(runs, f, startedAtMs) {
  const now = Date.now();
  const WINDOWS = { today: 86400e3, '7d': 7 * 86400e3, '30d': 30 * 86400e3 };
  return runs.filter(r => {
    if (f.status !== 'all' && _runStatusClass(r.status) !== f.status) return false;
    if (f.recency !== 'all') {
      const win = WINDOWS[f.recency];
      const ts = startedAtMs(r);
      if (win && ts && (now - ts) > win) return false;
    }
    return true;
  });
}

// Which kind of list the sidebar "Zuletzt verwendet" shows right now. Drives
// both the render branch and which filter menu the filter button opens.
function _recentListContext() {
  const onScheduledRun = state.currentView === 'chat'
    && state.activeChat?._readonly
    && state.activeChat?._scheduledRun;
  if (state.currentView === 'scheduled' || onScheduledRun) return 'runs';
  if (state.currentView === 'workflows') return 'workflow-runs';
  if (state.currentView === 'favourites') return 'favourites';
  if (state.currentView === 'translation') return 'translations';
  if (state.currentView === 'data') return 'data';
  if (state.currentView === 'wiki') return 'wiki';
  const inProjectContext = state.currentView === 'project-detail'
    || state.currentView === 'projects'
    || !!state.currentProject;
  if (inProjectContext) return 'project-chats';
  return 'chats';
}

// A session counts as an "Aufgabe" when it carries a goal (🎯) — the closest
// analog to claude.ai's tasks in this sidebar.
function _isTaskSession(s) {
  return s.goal_status === 'active' || s.goal_status === 'fulfilled';
}

// Apply Typ / Status / Letzte-Aktivität filters to a flat session list. Does NOT
// group — grouping is applied at render time so it can inject date headers.
function applyRecentFilter(sessions, f) {
  const now = Date.now() / 1000;  // last_active is epoch seconds
  const WINDOWS = { today: 86400, '7d': 7 * 86400, '30d': 30 * 86400 };
  return sessions.filter(s => {
    if (f.type === 'chats' && _isTaskSession(s)) return false;
    if (f.type === 'tasks' && !_isTaskSession(s)) return false;
    if (f.status === 'active'   && s.status === 'archived') return false;
    if (f.status === 'archived' && s.status !== 'archived') return false;
    if (f.recency !== 'all') {
      const win = WINDOWS[f.recency];
      if (win && (now - (s.last_active || 0)) > win) return false;
    }
    return true;
  });
}

// Date-bucket label for grouping (Heute / Gestern / Diese Woche / Diesen Monat / Älter).
function _recentDateBucket(lastActive) {
  if (!lastActive) return 'Älter';
  const ageDays = (Date.now() / 1000 - lastActive) / 86400;
  if (ageDays < 1)  return 'Heute';
  if (ageDays < 2)  return 'Gestern';
  if (ageDays < 7)  return 'Diese Woche';
  if (ageDays < 30) return 'Diesen Monat';
  return 'Älter';
}

// Which filter rows the menu shows in the current context. `family` picks the
// persisted filter + option DEFS; `rows` are the shown keys (a null entry = a
// divider before the following row). Grouping ('group') always comes from the
// chat filter (shared date-bucket toggle) regardless of family.
function _recentFilterMenuSpec() {
  const ctx = _recentListContext();
  if (ctx === 'runs' || ctx === 'workflow-runs') {
    return { family: 'run', rows: ['status', 'recency', null, 'group'] };
  }
  if (ctx === 'favourites' || ctx === 'translations' || ctx === 'data' || ctx === 'wiki') {
    // No per-item status axis → recency + grouping only.
    return { family: 'chat', rows: ['recency', null, 'group'] };
  }
  // chats / project-chats → full chat filter.
  return { family: 'chat', rows: ['type', 'status', 'recency', null, 'group'] };
}

function toggleRecentFilterMenu(event) {
  const existing = document.getElementById('sb-recent-filter-menu');
  if (existing) { closeRecentFilterMenu(); return; }

  const btn = document.getElementById('sb-recent-filter-btn');
  const menu = document.createElement('div');
  menu.id = 'sb-recent-filter-menu';
  menu.className = 'sb-recent-filter-menu';

  const spec = _recentFilterMenuSpec();
  // Accessors bound to the family this context uses. 'group' is always a chat
  // filter key, so its row reads/writes the chat filter even in run family.
  const isRunKey = (key) => spec.family === 'run' && key !== 'group';
  const getVal = (key) => (isRunKey(key) ? getRunFilter() : getRecentFilter())[key];
  const defsFor = (key) => (isRunKey(key) ? RUN_FILTER_DEFS : RECENT_FILTER_DEFS)[key];
  const labelFor = (key, v) => (isRunKey(key) ? runFilterLabel : recentFilterLabel)(key, v);

  const chevron = '<svg class="rf-chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 6 15 12 9 18"/></svg>';
  const rowHtml = (key) => {
    const def = defsFor(key);
    return `<div class="sb-recent-filter-row" data-key="${key}">
      <span class="rf-label">${esc(def.label)}</span>
      <span class="rf-value"><span class="rf-value-text">${esc(labelFor(key, getVal(key)))}</span>${chevron}</span>
    </div>`;
  };
  menu.innerHTML = spec.rows.map(k =>
    k === null ? '<div class="sb-recent-filter-divider"></div>' : rowHtml(k)
  ).join('');

  document.body.appendChild(menu);

  // Position below the filter button, right-aligned to it, clamped to viewport.
  const rect = btn.getBoundingClientRect();
  const mw = menu.offsetWidth;
  let left = rect.right - mw;
  if (left < 8) left = 8;
  menu.style.top = (rect.bottom + 4) + 'px';
  menu.style.left = left + 'px';
  menu.classList.add('open');
  btn.classList.add('active');

  // Clicking a row cycles that control to its next option, updates the value
  // text in place, persists, and re-renders the list live.
  menu.querySelectorAll('.sb-recent-filter-row').forEach(row => {
    row.addEventListener('click', (e) => {
      e.stopPropagation();
      const key = row.dataset.key;
      const runKey = isRunKey(key);
      const opts = defsFor(key).options;
      const cur = runKey ? getRunFilter() : getRecentFilter();
      const idx = opts.findIndex(o => o[0] === cur[key]);
      const next = opts[(idx + 1) % opts.length][0];
      cur[key] = next;
      (runKey ? setRunFilter : setRecentFilter)(cur);
      row.querySelector('.rf-value-text').textContent = labelFor(key, next);
      updateRecentFilterButtonState();
      renderRecentChats();
    });
  });

  setTimeout(() => {
    document.addEventListener('click', function closeOnOutside(ev) {
      if (!menu.contains(ev.target) && !btn.contains(ev.target)) {
        closeRecentFilterMenu();
        document.removeEventListener('click', closeOnOutside);
      }
    });
  }, 0);
}

function closeRecentFilterMenu() {
  document.getElementById('sb-recent-filter-menu')?.remove();
  document.getElementById('sb-recent-filter-btn')?.classList.remove('active');
}

function updateRecentFilterButtonState() {
  const btn = document.getElementById('sb-recent-filter-btn');
  if (!btn) return;
  const spec = _recentFilterMenuSpec();
  // A context is "filtered" if its own family filter is active. Grouping is a
  // chat-filter axis and counts in every context.
  const groupActive = getRecentFilter().group !== RECENT_FILTER_DEFAULT.group;
  let active;
  if (spec.family === 'run') {
    active = isRunFilterActive(getRunFilter()) || groupActive;
  } else if (spec.rows.includes('type')) {
    active = isRecentFilterActive(getRecentFilter());  // full chat filter
  } else {
    // recency + grouping only (favourites/translations/data/wiki)
    const rf = getRecentFilter();
    active = rf.recency !== RECENT_FILTER_DEFAULT.recency || groupActive;
  }
  btn.classList.toggle('filtered', !!active);
}

function renderRecentChats() {
  // Always refresh the favourites sidebar block alongside Recent — same poll cadence.
  try { window.Favourites?.renderSidebar?.(); } catch(_) {}
  updateRecentFilterButtonState();
  const container = document.getElementById('sb-recent-chats');
  // The "Zuletzt verwendet" list is context-aware: each top-level area shows
  // the data that belongs to it (and the filter button opens the matching
  // menu — see toggleRecentFilterMenu). Scheduled runs also stay pinned while
  // the user click-through-explores a read-only run timeline (chat view).
  switch (_recentListContext()) {
    case 'runs':          renderRecentScheduledRuns(container); return;
    case 'workflow-runs': renderRecentWorkflowRuns(container); return;
    case 'favourites':    renderRecentFavouriteItems(container); return;
    case 'translations':  renderRecentTranslations(container); return;
    case 'data':          renderRecentClassifications(container); return;
    case 'wiki':          renderRecentWikiPages(container); return;
    case 'project-chats': renderRecentProjectChats(container); return;
    // 'chats' → falls through to the normal-chat logic below.
  }
  if (!state.activeAgentId) {
    // Show recent across all agents
    let allSessions = [];
    for (const [agentId, data] of Object.entries(state.agentSessions)) {
      if (data.sessions) {
        for (const s of data.sessions) {
          if (s.status !== 'code' && (s.message_count || 0) > 0 && !(s.project || '')) {
            allSessions.push({...s, agentId});
          }
        }
      }
    }
    // Merged multi-agent list: server sorts each agent's list, but we interleave
    // agents here, so a client sort is still needed. Sort by raw last_active
    // (epoch seconds — server bumps it only on a sent message since v9.251.0),
    // NOT new Date(seconds) which mis-scales seconds-as-ms.
    allSessions.sort((a,b) => (b.last_active||0) - (a.last_active||0));
    allSessions = applyRecentFilter(allSessions, getRecentFilter()).slice(0, 15);
    renderSessionsList(container, allSessions);
    return;
  }

  const data = state.agentSessions[state.activeAgentId];
  if (!data?.sessions) { container.innerHTML = ''; return; }

  // The Status filter can ask for archived chats, but the sidebar's default
  // session load only fetches the ACTIVE set — so "Archiviert"/"Alle" showed
  // nothing until a full reload (the reported bug). When such a filter is
  // active and we haven't yet loaded archived sessions for this agent, fetch
  // them once and re-render. Guarded by a per-agent flag so it fires at most
  // once per agent (not on every poll).
  const rf = getRecentFilter();
  if ((rf.status === 'archived' || rf.status === 'all') && !data._archivedLoaded) {
    data._archivedLoaded = true;  // set before await so concurrent renders don't stack fetches
    _loadArchivedForAgent(state.activeAgentId);
  }

  // Single agent → the server already returns them ordered by last MODIFICATION
  // (newest message, v9.251.0). Don't re-sort — a client last_active sort would
  // re-introduce the "opening a chat reshuffles the list" bug. The archived
  // vs active split is now driven by the Status filter (default: Aktiv).
  const filtered = data.sessions
    .filter(s => s.status !== 'code'
      && (s.message_count || 0) > 0
      && !(s.project || ''));
  const sessions = applyRecentFilter(filtered, rf).slice(0, 20);

  renderSessionsList(container, sessions);
}

// Merge the agent's archived sessions into its cached session list, then
// re-render the sidebar. Called on demand when the recent filter asks for
// archived/all. Dedupes by id so re-runs are harmless.
async function _loadArchivedForAgent(agentId) {
  try {
    const resp = await API.getSessionsForAgent(agentId, 'archived');
    const archived = resp.sessions || [];
    const entry = state.agentSessions[agentId];
    if (!entry) return;
    const seen = new Set((entry.sessions || []).map(s => s.id || s.session_id));
    for (const s of archived) {
      const sid = s.id || s.session_id;
      if (!seen.has(sid)) { entry.sessions.push(s); seen.add(sid); }
    }
    renderRecentChats();
  } catch (e) {
    // Allow a retry on the next filter change if the fetch failed.
    const entry = state.agentSessions[agentId];
    if (entry) entry._archivedLoaded = false;
  }
}

function renderSessionsList(container, sessions) {
  container.innerHTML = '';
  container.dataset.mode = 'chats';
  const grouping = getRecentFilter().group === 'date';
  let currentBucket = null;
  for (const s of sessions) {
    // Date grouping: inject a small header whenever the bucket changes. The list
    // is already newest-first, so buckets appear in descending recency order.
    if (grouping) {
      const bucket = _recentDateBucket(s.last_active);
      if (bucket !== currentBucket) {
        currentBucket = bucket;
        const hdr = document.createElement('div');
        hdr.className = 'sb-recent-group-header';
        hdr.textContent = bucket;
        container.appendChild(hdr);
      }
    }
    const div = document.createElement('div');
    const sid = s.id || s.session_id;
    const sagent = s.agent_id || s.agent || s.agentId || state.activeAgentId;
    // "läuft" = an dieser Sitzung wird gearbeitet. Das ist der eigene Turn ODER
    // ein abgekoppelter Subagent: run_background_task beendet den spawnenden Turn
    // sofort, die Aufgaben laufen aber minutenlang weiter — ohne den zweiten Fall
    // wirkt der Chat in der Liste tot, obwohl im Hintergrund gearbeitet wird.
    const streaming = state.streamingSessions?.has(sid);
    const subRows = (state.runningSubagents || {})[sid] || [];
    const subCount = subRows.length;
    const subAsking = subRows.some(t => t.pending_question);
    const busy = streaming || subCount > 0;
    div.className = 'sb-session-item' + (state.activeChat?.sessionId === sid ? ' active' : '')
      + (busy ? ' streaming' : '');
    // Title primary; summary is hover-only. Falls back to summary only when
    // no title (rare — pre-first-turn rows).
    const title = s.title || s.summary || `Chat ${sid?.substring(0,6)}`;  // "Chat" identical in German
    const tip = s.summary ? ` title="${esc(s.summary)}"` : '';
    // Goal-Modus pill: active goal (🎯) or fulfilled (🎯✓) — mirrors the
    // composer badge so goal chats are recognizable from the list.
    const goalPill = s.goal_status === 'active'
      ? `<span class="sb-stream-pill" style="background:var(--accent-500, #6366f1)" title="Goal-Modus aktiv: ${esc(s.goal_text || '')}">🎯</span>`
      : (s.goal_status === 'fulfilled'
        ? `<span class="sb-stream-pill" style="background:var(--success, #22c55e)" title="Ziel erreicht: ${esc(s.goal_text || '')}">🎯✓</span>`
        : '');
    div.innerHTML = `
      <span class="sb-sess-icon"><svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg></span>
      <span class="sb-session-title"${tip}>${esc(title)}</span>
      ${goalPill}
      ${subAsking
        ? '<span class="sb-stream-pill sb-ask-pill" title="Ein Subagent wartet auf Ihre Antwort">❓ Rückfrage</span>'
        : (streaming
          ? '<span class="sb-stream-pill" title="Antwort wird gerade erstellt">läuft</span>'
          : (subCount
            ? `<span class="sb-stream-pill" title="${subCount} Subagent${subCount === 1 ? '' : 'en'} ${subCount === 1 ? 'läuft' : 'laufen'} noch">✦ ${subCount}</span>`
            : ''))}
      <span class="sb-sess-actions">
        <button onclick="event.stopPropagation(); archiveSession('${sid}')" title="Archivieren">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 8v13H3V8M1 3h22v5H1z"/></svg>
        </button>
        <button onclick="event.stopPropagation(); deleteSession('${sid}')" title="Löschen">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
        </button>
      </span>
    `;
    div.onclick = () => openSession(sid, sagent);
    container.appendChild(div);
    // Subagenten-Tree: laufende Hintergrundaufgaben dieses Chats als
    // Kind-Zeilen unter dem Eintrag (✦ + pulsierender Punkt + Titel + Modell).
    // Gespeist vom pollRunningSubagents-Poller (state.runningSubagents).
    for (const t of subRows) {
      const row = document.createElement('div');
      row.className = 'sb-subagent-row';
      const mdl = (typeof modelShortName === 'function' && t.model)
        ? modelShortName(t.model, false) : (t.model || '');
      const asks = !!t.pending_question;
      row.innerHTML = `<span class="sb-sub-dot"></span>
        <span class="sb-sub-title" title="${asks ? 'Wartet auf Ihre Antwort — ' : ''}${esc(t.title || '')}${mdl ? ' · ' + esc(mdl) : ''}">${asks ? '❓' : '✦'} ${esc(t.title || 'Subagent')}</span>
        <button class="sb-sub-stop" title="Subagent stoppen (Teilergebnis bleibt erhalten)">
          <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
        </button>`;
      row.onclick = (e) => { e.stopPropagation(); openSession(sid, sagent); };
      const stopBtn = row.querySelector('.sb-sub-stop');
      if (stopBtn) {
        stopBtn.onclick = async (e) => {
          e.stopPropagation();
          try { await API.cancelBackgroundTask(t.id); } catch (_) {}
          pollRunningSubagents();
        };
      }
      container.appendChild(row);
    }
  }
}

// ── Sidebar-Subagenten-Poller ────────────────────────────────────────────────
// Spiegel des pollActiveSessions-Musters: alle 3s die LAUFENDEN Hintergrund-
// aufgaben aller Sessions holen (leichte Zeilen), bei Änderung die sichtbaren
// Chat-Listen neu malen, damit der ✦-Tree unter den Chat-Einträgen live
// erscheint/verschwindet. Läuft dauerhaft mit (billig; Signatur-Vergleich).
let _subagentPollTimer = null;
let _subagentSig = '';
async function pollRunningSubagents() {
  try {
    const res = await API.getRunningBackgroundTasks();
    const tasks = Array.isArray(res?.tasks) ? res.tasks : [];
    const map = {};
    for (const t of tasks) {
      (map[t.session_id] = map[t.session_id] || []).push(t);
    }
    state.runningSubagents = map;
    // Rückfragen blockierter Subagenten in die Hub-Karten spielen (Antwort-Box)
    // + Server-Uhr/Timeout für die Laufzeit-Anzeige übernehmen. VOR dem
    // Signatur-Check: die Uhr muss auch dann nachgeführt werden, wenn sich die
    // Task-Menge nicht geändert hat (sonst driftet die Laufzeit-Anzeige weg).
    if (typeof agentHubApplyPendingQuestions === 'function') {
      agentHubApplyPendingQuestions(tasks, { now: res?.now, timeout_s: res?.timeout_s });
    }
    // Signatur enthält die offene-Frage-Kennung mit: eine NEUE Rückfrage muss ein
    // Repaint auslösen, auch wenn sich die Task-Menge nicht geändert hat.
    const sig = tasks.map(t => t.id + (t.pending_question ? '?' : '')).sort().join(',');
    if (sig === _subagentSig) return;   // keine Änderung → kein Listen-Repaint
    _subagentSig = sig;
    if (typeof renderRecentChats === 'function') renderRecentChats();
    if (state.currentView === 'chats' && typeof loadChatsList === 'function') {
      loadChatsList();
    } else if (state.currentView === 'project-detail'
               && typeof loadProjectChats === 'function'
               && state._projectDetailAgent && state._projectDetailName) {
      loadProjectChats(state._projectDetailAgent, state._projectDetailName);
    }
    // Terminal-Chat-Statuszeilen mitziehen: ihr Puls hängt jetzt auch an den
    // laufenden Subagenten, und ihr eigener Turn ist längst fertig — ohne dieses
    // Repaint bliebe die Zeile im Zustand des Turn-Endes stehen (kein Puls beim
    // Spawn, kein Erlöschen beim Fertigwerden).
    if (typeof _term !== 'undefined' && _term.tabs && typeof tcRenderStatus === 'function') {
      for (const t of _term.tabs) {
        if (t.kind !== 'chat' || !t.sessionId) continue;
        tcRenderStatus(t);
        // Spinner-Zeile am Log-Ende, solange Subagenten dieses Chats laufen.
        if (typeof _tcSubSpinRender === 'function') _tcSubSpinRender(t);
      }
    }
    // Web-Chat: den Spinner der offenen Unterhaltung neu zeichnen. Ein
    // abgekoppelter Subagent hält ihn am Leben, obwohl chat.streaming false ist —
    // ohne dieses Repaint erschiene er erst beim nächsten Turn (bzw. verschwände
    // nicht, wenn der letzte Subagent fertig ist).
    if (typeof renderStreamingMessage === 'function' && state.activeChat
        && !state.activeChat.streaming) {
      renderStreamingMessage(state.activeChat);
    }
  } catch (_) { /* transient — nächster Tick */ }
}
function startRunningSubagentsPoll() {
  if (_subagentPollTimer) return;
  pollRunningSubagents();
  _subagentPollTimer = setInterval(pollRunningSubagents, 3000);
}

// Poll the set of currently-streaming session IDs and, when it changes, repaint
// the visible chat lists so their "läuft gerade" pills appear/disappear live.
// One shared 3s timer, started on init; cheap (bare id list). Repaints only on a
// real change (signature compare) so it never fights the other list renders.
let _activeSessPollTimer = null;
let _activeSessSig = '';
async function pollActiveSessions() {
  try {
    const res = await API.getActiveSessions();
    const ids = Array.isArray(res?.active) ? res.active : [];
    const sig = ids.slice().sort().join(',');
    if (sig === _activeSessSig) return;   // no change → no repaint
    _activeSessSig = sig;
    state.streamingSessions = new Set(ids);
    // Repaint whatever chat list is on screen (sidebar always; the main-area
    // list depends on the current view).
    if (typeof renderRecentChats === 'function') renderRecentChats();
    if (state.currentView === 'chats' && typeof loadChatsList === 'function') {
      loadChatsList();
    } else if (state.currentView === 'project-detail'
               && typeof loadProjectChats === 'function'
               && state._projectDetailAgent && state._projectDetailName) {
      loadProjectChats(state._projectDetailAgent, state._projectDetailName);
    }
  } catch (_) { /* transient — try again next tick */ }
}
function startActiveSessionsPoll() {
  if (_activeSessPollTimer) return;
  pollActiveSessions();
  _activeSessPollTimer = setInterval(pollActiveSessions, 3000);
}

// Sidebar renderer for project views. Shows chats whose (agentId, project)
// pair appears in the current user's accessible-projects map. The map is
// populated by loadProjectsList(); we lazy-load it on first use here.
async function renderRecentProjectChats(container) {
  if (!container) return;
  container.dataset.mode = 'project-chats';

  // Lazy-fill state.agentProjects so this works on direct deep-link to a
  // project view without first visiting the projects list.
  const needsLoad = !state.agentProjects || !Object.keys(state.agentProjects).length;
  if (needsLoad) {
    container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Projekte werden geladen…</div>';
    try { await loadProjectsList(); } catch(_) {}
  }

  // Build the access set: "<agentId>::<projectName>" → true for every project
  // the server returned for this user across all agents.
  const accessSet = new Set();
  for (const [aid, plist] of Object.entries(state.agentProjects || {})) {
    for (const p of (plist || [])) {
      if (p?.name) accessSet.add(aid + '::' + p.name);
    }
  }
  if (!accessSet.size) {
    container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Noch keine Projekte</div>';
    return;
  }

  // Pull sessions across all agents we have cached, filter to those whose
  // (agent, project) is in the access set, sort by recency. agentSessions is
  // populated incrementally as the user navigates; trigger a refresh for
  // agents we haven't seen yet so a new browser tab still shows results.
  const knownAgents = new Set(Object.keys(state.agentSessions || {}));
  const projectAgents = new Set();
  for (const key of accessSet) projectAgents.add(key.split('::', 1)[0]);
  const missing = [...projectAgents].filter(a => !knownAgents.has(a));
  if (missing.length) {
    await Promise.all(missing.map(async aid => {
      try {
        const data = await API.getSessionsForAgent(aid);
        state.agentSessions[aid] = data;
      } catch(_) {}
    }));
  }

  // The default session load fetches only ACTIVE sessions. When the Status
  // filter asks for archived/all, pull the archived set for every project
  // agent once (per-agent _archivedLoaded guard, same as the normal path) so
  // "Archiviert"/"Alle" isn't silently empty. Merge in place — do NOT call
  // _loadArchivedForAgent here (it re-enters renderRecentChats).
  const rf = getRecentFilter();
  if (rf.status === 'archived' || rf.status === 'all') {
    const need = [...projectAgents].filter(a => state.agentSessions[a] && !state.agentSessions[a]._archivedLoaded);
    if (need.length) {
      await Promise.all(need.map(async aid => {
        const entry = state.agentSessions[aid];
        entry._archivedLoaded = true;  // set before await so concurrent renders don't stack fetches
        try {
          const resp = await API.getSessionsForAgent(aid, 'archived');
          const seen = new Set((entry.sessions || []).map(s => s.id || s.session_id));
          for (const s of (resp.sessions || [])) {
            const sid = s.id || s.session_id;
            if (!seen.has(sid)) { entry.sessions.push(s); seen.add(sid); }
          }
        } catch (_) {
          entry._archivedLoaded = false;  // allow retry on next filter change
        }
      }));
    }
  }

  // In project-detail view, the main area already lists THIS project's chats.
  // Suppress them in the sidebar to avoid visual duplication; show only
  // chats from other accessible projects.
  const currentKey = (state.currentView === 'project-detail'
    && state._projectDetailAgent && state._projectDetailName)
    ? state._projectDetailAgent + '::' + state._projectDetailName
    : '';

  let sessions = [];
  for (const [agentId, data] of Object.entries(state.agentSessions || {})) {
    for (const s of (data?.sessions || [])) {
      // Status (Aktiv/Archiviert/Alle) is governed by the recent filter below —
      // do NOT hard-exclude archived here, that was the "only date grouping
      // works" bug. Code sessions are never chats.
      if (s.status === 'code') continue;
      if ((s.message_count || 0) === 0) continue;
      const proj = s.project || '';
      if (!proj) continue;
      const key = agentId + '::' + proj;
      if (!accessSet.has(key)) continue;
      if (currentKey && key === currentKey) continue;
      sessions.push({...s, agentId});
    }
  }
  // Apply Typ / Status / Letzte-Aktivität exactly like the normal-chat path
  // (grouping is applied later in renderSessionsList).
  sessions = applyRecentFilter(sessions, rf);
  // Merged across agents/projects → client sort needed; raw last_active (epoch
  // seconds, send-only since v9.251.0), not new Date(seconds).
  sessions.sort((a, b) => (b.last_active || 0) - (a.last_active || 0));
  sessions = sessions.slice(0, 30);

  if (!sessions.length) {
    container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Noch keine Projekt-Chats</div>';
    return;
  }
  renderSessionsList(container, sessions);
}

async function renderRecentScheduledRuns(container) {
  if (!container) return;
  const wasRuns = container.dataset.mode === 'runs';
  container.dataset.mode = 'runs';
  // Only show the loading placeholder on the first paint to avoid flicker on
  // every subsequent renderRecentChats() trigger (rename hook, polls, etc.).
  if (!wasRuns) {
    container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Ausführungen werden geladen…</div>';
    // Stale signature from a prior runs session — force a fresh render below.
    delete container.dataset.sig;
    delete container.dataset.activeRun;
  }
  try {
    // Fetch live schedules + history in parallel; filter out orphan runs
    // (schedule_name no longer in the schedules table — e.g. deleted user tasks
    // or future internal/system tasks) so the sidebar only reflects user-created
    // schedules that still exist.
    const [schedRes, histRes] = await Promise.all([
      API.getSchedule(),
      API.manageSchedule({ action: 'history', limit: 50 }),
    ]);
    const liveNames = new Set((schedRes.schedules || []).map(s => s.name));
    let runs = (histRes.history || []).filter(h => liveNames.has(h.schedule_name));
    // Apply the run-status + recency filter (started_at is a UTC string).
    runs = applyRunFilter(runs, getRunFilter(),
      h => (h.started_at ? new Date(h.started_at + 'Z').getTime() : 0));
    runs = runs.slice(0, 20);
    // Bail if dataset.mode flipped to 'chats' meanwhile (user navigated to a
    // chat-centric view).
    if (container.dataset.mode !== 'runs') return;
    if (!runs.length) {
      container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Noch keine geplanten Ausführungen</div>';
      return;
    }
    // Cheap stable signature to skip the DOM rebuild when nothing changed
    // (id+status; status changes mid-run, e.g. running → success). Include the
    // grouping mode so toggling Gruppieren forces a rebuild (headers appear).
    const grouping = getRecentFilter().group === 'date';
    const sig = (grouping ? 'g:' : '') + runs.map(h => `${h.id}:${h.status||''}`).join(',');
    const activeId = state.activeScheduledRunId || null;
    if (container.dataset.sig === sig && container.dataset.activeRun === String(activeId || '')) {
      return;
    }
    container.dataset.sig = sig;
    container.dataset.activeRun = String(activeId || '');
    container.innerHTML = '';
    let currentBucket = null;
    for (const h of runs) {
      // Date grouping: inject a header when the bucket changes (list is
      // newest-first, so buckets descend by recency).
      if (grouping) {
        const ms = h.started_at ? new Date(h.started_at + 'Z').getTime() : 0;
        const bucket = _recentDateBucket(ms / 1000);
        if (bucket !== currentBucket) {
          currentBucket = bucket;
          const hdr = document.createElement('div');
          hdr.className = 'sb-recent-group-header';
          hdr.textContent = bucket;
          container.appendChild(hdr);
        }
      }
      const ok = h.status === 'success' || h.status === 'completed';
      const running = h.status === 'running';
      const dotColor = running ? '#3b82f6' : (ok ? '#10b981' : (h.status === 'timeout' ? '#f59e0b' : '#ef4444'));
      const when = h.started_at ? new Date(h.started_at + 'Z').toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
      const title = h.schedule_name || `Ausführung #${h.id}`;
      const agentId = h.agent || 'main';
      const div = document.createElement('div');
      div.className = 'sb-session-item' + (activeId === h.id ? ' active' : '');
      div.title = `${h.schedule_name || ''}\n${h.status || ''}${when ? ' · ' + when : ''}`;
      div.innerHTML = `
        <span class="sb-sess-icon" style="color:${dotColor}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg></span>
        <span class="sb-session-title">${esc(title)}</span>
      `;
      div.onclick = () => openScheduledArtifact(h.id, `sched-${h.id}`, agentId, null);
      container.appendChild(div);
    }
  } catch (e) {
    if (!wasRuns) {
      container.innerHTML = `<div class="sb-session-item" style="opacity:.6;cursor:default;color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</div>`;
    }
  }
}

// ── Generic icon-row list for the non-chat contexts ──────────────
// items: [{ icon (html|''), title, tip, bucketMs (epoch ms, for date grouping),
//           onClick }]. `mode` tags the container so a mode flip forces a
// clean rebuild. Applies date grouping when the shared group filter is 'date'.
function _renderRecentItemList(container, items, mode, emptyText, dotColorForItem) {
  container.dataset.mode = mode;
  container.innerHTML = '';
  if (!items.length) {
    container.innerHTML = `<div class="sb-session-item" style="opacity:.6;cursor:default">${esc(emptyText)}</div>`;
    return;
  }
  const grouping = getRecentFilter().group === 'date';
  let currentBucket = null;
  for (const it of items) {
    if (grouping) {
      const bucket = _recentDateBucket((it.bucketMs || 0) / 1000);
      if (bucket !== currentBucket) {
        currentBucket = bucket;
        const hdr = document.createElement('div');
        hdr.className = 'sb-recent-group-header';
        hdr.textContent = bucket;
        container.appendChild(hdr);
      }
    }
    const div = document.createElement('div');
    div.className = 'sb-session-item';
    if (it.tip) div.title = it.tip;
    const iconStyle = it.dotColor ? ` style="color:${it.dotColor}"` : '';
    div.innerHTML = `
      <span class="sb-sess-icon"${iconStyle}>${it.icon || ''}</span>
      <span class="sb-session-title">${esc((it.title || '').slice(0, 60))}</span>
    `;
    div.onclick = it.onClick;
    container.appendChild(div);
  }
}

// Recency-only filter for the item contexts (favourites/translations/wiki):
// keep items whose timestamp is within the selected window. Reuses the chat
// filter's `recency` value; `tsMs(item)` → epoch ms.
function _applyRecencyMs(items, tsMs) {
  const rf = getRecentFilter();
  if (rf.recency === 'all') return items;
  const WINDOWS = { today: 86400e3, '7d': 7 * 86400e3, '30d': 30 * 86400e3 };
  const win = WINDOWS[rf.recency];
  if (!win) return items;
  const now = Date.now();
  return items.filter(it => { const t = tsMs(it); return t && (now - t) <= win; });
}

// ── Favourites view → all favourite items (newest first) ──────────
async function renderRecentFavouriteItems(container) {
  if (!container) return;
  try { await FavouritesCache.load(); } catch (_) {}
  let rows = (FavouritesCache.rows || []).filter(r => r.available !== false);
  rows = _applyRecencyMs(rows, r => (r.updated_at || 0) * 1000);
  rows.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  rows = rows.slice(0, 30);
  const items = rows.map(r => {
    const def = (typeof FAVOURITES_TYPE_DEFAULTS !== 'undefined' && FAVOURITES_TYPE_DEFAULTS[r.item_type]) || {};
    const rawIcon = r.icon || r.source_icon || '';
    const customIcon = (rawIcon && rawIcon !== def.icon) ? rawIcon : '';
    const icon = customIcon ? esc(customIcon)
      : (typeof favouriteTypeGlyphSvg === 'function' ? favouriteTypeGlyphSvg(r.item_type, 16) : '');
    return {
      icon, title: r.title || '(ohne Titel)',
      tip: `${r.item_type} · ${typeof labelForScope === 'function' ? labelForScope(r.scope, r.scope_id) : ''}`,
      bucketMs: (r.updated_at || 0) * 1000,
      onClick: () => openFavouriteRow(r),
    };
  });
  _renderRecentItemList(container, items, 'favourites', 'Noch keine Favoriten');
}

// ── Workflows view → execution history (filtered by run status) ──
async function renderRecentWorkflowRuns(container) {
  if (!container) return;
  const wasMode = container.dataset.mode === 'workflow-runs';
  if (!wasMode) container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Läufe werden geladen…</div>';
  try {
    const rf = getRunFilter();
    const params = new URLSearchParams({ limit: '50' });
    // Server understands ?status=success|error|running for the coarse buckets.
    if (rf.status !== 'all') params.set('status', rf.status);
    const data = await API.get(`/v1/workflows/history?${params.toString()}`);
    if (_recentListContext() !== 'workflow-runs') return;  // navigated away
    let rows = data.executions || [];
    // Client recency filter (server may not gate by time).
    rows = applyRunFilter(rows, { status: 'all', recency: rf.recency },
      r => (r.started_at ? new Date(r.started_at).getTime() : 0));
    rows = rows.slice(0, 30);
    const items = rows.map(r => {
      const cls = _runStatusClass(r.status);
      const dot = cls === 'running' ? '#3b82f6' : (cls === 'success' ? '#10b981' : '#ef4444');
      const when = r.started_at ? new Date(r.started_at).toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
      return {
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
        dotColor: dot,
        title: r.workflow_name || `Lauf ${(''+(r.execution_id||'')).slice(0,8)}`,
        tip: `${r.workflow_name || ''}\n${r.status || ''}${when ? ' · ' + when : ''}`,
        bucketMs: r.started_at ? new Date(r.started_at).getTime() : 0,
        onClick: () => { try { wfShowHistoryDetail(r.execution_id); } catch(_) {} },
      };
    });
    _renderRecentItemList(container, items, 'workflow-runs', 'Noch keine Workflow-Läufe');
  } catch (e) {
    if (!wasMode) container.innerHTML = `<div class="sb-session-item" style="opacity:.6;cursor:default;color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</div>`;
  }
}

// ── Translation view → translation history (recency filter) ──────
async function renderRecentTranslations(container) {
  if (!container) return;
  const wasMode = container.dataset.mode === 'translations';
  if (!wasMode) container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Verlauf wird geladen…</div>';
  try {
    const data = await API.get('/v1/translate/history');
    if (_recentListContext() !== 'translations') return;
    let rows = data.entries || [];
    rows = _applyRecencyMs(rows, e => (e.created_at || 0) * 1000);
    rows.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    rows = rows.slice(0, 30);
    const TYPE_GLYPH = {
      text: '📝', document: '📄', media: '🎧', live: '🎙️',
    };
    const items = rows.map(e => {
      const langs = [e.source_lang, e.target_lang].filter(Boolean).join(' → ');
      const title = e.title || langs || 'Übersetzung';
      return {
        icon: esc(TYPE_GLYPH[e.type] || '📝'),
        title,
        tip: `${e.type || ''}${langs ? ' · ' + langs : ''}`,
        bucketMs: (e.created_at || 0) * 1000,
        onClick: () => {
          navigateTo('translation');
          setTimeout(() => { try { trJumpToHistoryEntry && trJumpToHistoryEntry(e.id); } catch(_) {} }, 120);
        },
      };
    });
    _renderRecentItemList(container, items, 'translations', 'Noch kein Verlauf');
  } catch (e) {
    if (!wasMode) container.innerHTML = `<div class="sb-session-item" style="opacity:.6;cursor:default;color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</div>`;
  }
}

// ── Data view → document-classification scans (recency filter) ───
async function renderRecentClassifications(container) {
  if (!container) return;
  const wasMode = container.dataset.mode === 'data';
  if (!wasMode) container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Scans werden geladen…</div>';
  try {
    const data = await API.get('/v1/classification/scans');
    if (_recentListContext() !== 'data') return;
    let rows = data.scans || [];
    rows = _applyRecencyMs(rows, s => (s.created_at || 0) * 1000);
    rows.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    rows = rows.slice(0, 30);
    const items = rows.map(s => {
      const when = s.created_at ? new Date(s.created_at * 1000).toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
      const title = s.source_label || s.source_kind || `Scan ${(''+(s.scan_id||'')).slice(0,8)}`;
      return {
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
        title,
        tip: `${s.source_kind || ''}${when ? ' · ' + when : ''}`,
        bucketMs: (s.created_at || 0) * 1000,
        onClick: () => {
          navigateTo('data');
          setTimeout(() => { try { clsOpenScan && clsOpenScan(s.scan_id); } catch(_) {} }, 120);
        },
      };
    });
    _renderRecentItemList(container, items, 'data', 'Noch keine Scans');
  } catch (e) {
    if (!wasMode) container.innerHTML = `<div class="sb-session-item" style="opacity:.6;cursor:default;color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</div>`;
  }
}

// ── Wiki view → recently changed pages (recency filter) ──────────
async function renderRecentWikiPages(container) {
  if (!container) return;
  const wasMode = container.dataset.mode === 'wiki';
  if (!wasMode) container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Seiten werden geladen…</div>';
  try {
    const data = await API.wikiTree('all');
    if (_recentListContext() !== 'wiki') return;
    let rows = data.pages || [];
    rows = _applyRecencyMs(rows, p => (p.updated_at || 0) * 1000);
    rows.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
    rows = rows.slice(0, 30);
    const items = rows.map(p => ({
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
      title: p.title || 'Ohne Titel',
      tip: (typeof WIKI_SOURCE_LABELS !== 'undefined' && WIKI_SOURCE_LABELS[p.source]) || p.source || '',
      bucketMs: (p.updated_at || 0) * 1000,
      onClick: () => {
        navigateTo('wiki');
        setTimeout(() => { try { wikiOpenPage && wikiOpenPage(p.id); } catch(_) {} }, 120);
      },
    }));
    _renderRecentItemList(container, items, 'wiki', 'Noch keine Wiki-Seiten');
  } catch (e) {
    if (!wasMode) container.innerHTML = `<div class="sb-session-item" style="opacity:.6;cursor:default;color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</div>`;
  }
}

