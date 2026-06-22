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

  // Update sidebar active state
  document.querySelectorAll('.sb-nav-item').forEach(n => n.classList.remove('active'));
  const navItem = document.querySelector(`.sb-nav-item[data-view="${view}"]`);
  if (navItem) navItem.classList.add('active');

  switch(view) {
    case 'welcome':
      document.getElementById('welcome-view').style.display = '';
      updatePageHeader('Brain Agent');
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
      updatePageHeader('Chats');  // German keeps the same word
      document.getElementById('status-bar').style.display = '';
      loadChatsList();
      break;

    case 'projects':
      document.getElementById('projects-view').classList.add('active');
      updatePageHeader('Projekte');
      // Hide per-chat status bar — same reason as project-detail: no chat
      // in scope, the bar would otherwise show stale session data.
      document.getElementById('status-bar').style.display = 'none';
      loadProjectsList();
      break;

    case 'project-detail':
      document.getElementById('project-detail-view').classList.add('active');
      updatePageHeader(opts?.projectName || 'Projekt');
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
        if (typeof updateStatusBar === 'function') updateStatusBar();
        updateSendButton();
        renderFilePreviews();
        schedulePIIBadgeUpdate();
      } catch(_) {}
      break;

    case 'artifacts':
      document.getElementById('artifacts-view').classList.add('active');
      updatePageHeader('Artefakte');
      // Hide per-chat status bar — artifacts overview is a cross-session
      // grid; no chat in scope, so the bar would otherwise show stale
      // session data from whichever chat the user was last viewing.
      document.getElementById('status-bar').style.display = 'none';
      loadArtifactsBrowse();
      break;

    case 'scheduled':
      document.getElementById('scheduled-view').classList.add('active');
      updatePageHeader('Geplant');
      // Hide per-chat status bar — scheduled is a list view with no chat
      // in scope. Once a run is opened (openScheduledArtifact → chat view)
      // the bar comes back with that run's actual data.
      document.getElementById('status-bar').style.display = 'none';
      loadScheduledView();
      renderRecentChats(); // dispatches to renderRecentScheduledRuns when view==='scheduled'
      break;

    case 'workflows':
      document.getElementById('workflows-view').classList.add('active');
      updatePageHeader('Workflows');
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
      updatePageHeader('Daten');
      document.getElementById('status-bar').style.display = 'none';
      if (typeof clsOpenView === 'function') clsOpenView();
      break;
    }

    case 'wiki': {
      document.getElementById('wiki-view').classList.add('active');
      updatePageHeader('Wiki');
      document.getElementById('status-bar').style.display = 'none';
      if (typeof loadWikiView === 'function') loadWikiView();
      break;
    }

    case 'favourites':
      if (favView) favView.classList.add('active');
      updatePageHeader('Favoriten');
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

  closeMobileSidebar();
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

// Extract all user + assistant text (and attachment metadata) from a chat's
// in-memory history into a single string so PIIScanner can sweep it in one
// pass. Tool calls/results are excluded — they're downstream of the user's
// intent and would create noise (URLs, search snippets, etc.).
function piiHistoryText(chat) {
  if (!chat || !Array.isArray(chat.messages) || !chat.messages.length) return '';
  const parts = [];
  for (const m of chat.messages) {
    if (!m) continue;
    const role = m.role;
    if (role !== 'human' && role !== 'user' && role !== 'assistant') continue;
    const c = m.content;
    if (typeof c === 'string') {
      if (c) parts.push(c);
    } else if (Array.isArray(c)) {
      for (const b of c) {
        if (b && typeof b === 'object' && b.type === 'text' && typeof b.text === 'string') {
          parts.push(b.text);
        }
      }
    }
    if (Array.isArray(m.files)) {
      for (const f of m.files) {
        if (f && typeof f === 'object') {
          const bits = [f.name, f.filename, f.path, f.mime, f.type].filter(Boolean);
          if (bits.length) parts.push(bits.join(' '));
        }
      }
    }
  }
  return parts.join('\n');
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
    const text = piiHistoryText(chat);
    let has = false, worst = 'ignore', counts = {};
    try {
      const scan = PIIScanner.scanPayload(text, []);
      has = scan.findings.length > 0;
      worst = scan.worstAction || 'ignore';
      counts = scan.counts || {};
    } catch (e) {}
    chat._piiHistoryCountsLocal = counts;
    chat._piiHistoryWorstLocal = worst;
    chat._piiHistoryHasLocal = has;
    // Re-merge with any prior server NER result so the union stays correct
    // across turn-count refreshes. The server fetch below will overwrite
    // _piiHistoryCountsServer once it finishes.
    _piiHistoryMergeAndCache(chat);
    chat._piiHistoryScanLen = len;
    // Kick the async server scan unless one's already in flight or we've
    // already got a fresh result for this turn count.
    if (chat._piiHistoryServerScanLen !== len && !chat._piiHistoryServerInFlight) {
      _piiHistoryFetchServer(chat, len);
    }
  }
  return !!chat._piiHistoryHas;
}

function _piiHistoryMergeAndCache(chat) {
  const local = chat._piiHistoryCountsLocal || {};
  const server = chat._piiHistoryCountsServer || {};
  // Union by label — server-side findings include the regex hits the
  // client already saw, so prefer the server count where it exists (it's
  // strictly >= local for shared labels).
  const merged = Object.assign({}, local);
  for (const [k, v] of Object.entries(server)) {
    merged[k] = Math.max(merged[k] || 0, v || 0);
  }
  chat._piiHistoryCounts = merged;
  const worstRank = (a) => a === 'block' ? 2 : a === 'warn' ? 1 : 0;
  const lw = chat._piiHistoryWorstLocal || 'ignore';
  const sw = chat._piiHistoryWorstServer || 'ignore';
  chat._piiHistoryWorst = worstRank(sw) > worstRank(lw) ? sw : lw;
  chat._piiHistoryHas = Object.keys(merged).length > 0;
}

function _piiHistoryFetchServer(chat, expectLen) {
  // Scanner disabled → no server-side NER round-trip either (the local scan is
  // already gated in PIIScanner.scan; this stops the network call too so
  // "PII check" truly does nothing when the admin turned the feature off).
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
  const serverBlock = !!document.getElementById('gdpr-block')?.checked;
  const fallback = document.getElementById('gdpr-fallback')?.value || '';
  const bgPii = document.getElementById('gdpr-bg-pii-action')?.value || 'anonymise';
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

  // Per-rule min_occurrences — write EVERY rule (full snapshot, like
  // categories), so config.json is the single source of truth and blanking a
  // field never silently reverts to a hidden code default. A blank/invalid
  // input means the universal floor 1 (fire on any single match).
  const min_occurrences = {};
  for (const inp of document.querySelectorAll('.gdpr-rule-minocc')) {
    const rid = inp.dataset.rule;
    if (!rid) continue;
    const n = parseInt((inp.value || '').trim(), 10);
    min_occurrences[rid] = (Number.isFinite(n) && n >= 1) ? n : 1;
  }

  const allowlistRaw = document.getElementById('gdpr-email-allowlist')?.value || '';
  const email_allowlist = allowlistRaw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);

  return {
    enabled, server_log: serverLog, server_block: serverBlock,
    default_local_fallback_model: fallback,
    background_pii_action: bgPii,
    background_anonymise_fail_action: bgFail,
    categories, rule_overrides, min_occurrences, email_allowlist,
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
    schedulePIIBadgeUpdate();
  } catch (e) {
    showToast('Fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Alle GDPR-Einstellungen speichern'; }
  }
}

function resetGdprCategories() {
  for (const sel of document.querySelectorAll('.gdpr-cat-action')) {
    const cat = sel.dataset.cat;
    if (cat && PIIScanner.defaultCategoryActions[cat]) {
      sel.value = PIIScanner.defaultCategoryActions[cat];
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

// Apply server-side gdpr_scanner config to both state and PIIScanner.policy so
// the scanner knows which categories are ignored, warned on, or blocked. Call
// this anywhere state.pii* is updated from the /v1/services/status response.
function applyGdprConfigToScanner(gs) {
  gs = gs || {};
  state.piiScannerEnabled = gs.enabled !== false;
  state.piiServerBlock = !!gs.server_block;
  state.piiLocalFallback = gs.default_local_fallback_model || '';
  PIIScanner.policy.enabled = state.piiScannerEnabled;
  PIIScanner.policy.serverBlock = state.piiServerBlock;
  PIIScanner.policy.categories = gs.categories || null;
  PIIScanner.policy.ruleOverrides = gs.rule_overrides || {};
  PIIScanner.policy.emailAllowlist = Array.isArray(gs.email_allowlist) ? gs.email_allowlist : [];
  // Drop any cached per-chat history scans — action changes invalidate them.
  for (const c of (state.chats || [])) {
    if (c) c._piiHistoryScanLen = -1;
  }
}

// True when the active chat's draft OR its loaded history contains a
// block-severity finding AND the master block switch is on. In that state the
// composer disables cloud-model selection and auto-picks a local model, even
// if the current draft is empty.
function piiBlockActive(chat) {
  if (!state.piiServerBlock || state.piiScannerEnabled === false) return false;
  chat = chat || state.activeChat;
  if (!chat) return false;
  if (sessionStorage.getItem('pii-suppress:' + (chat.sessionId || '_new'))) return false;
  const input = _composerInputEl();
  const text = input?.value || '';
  const draftScan = PIIScanner.scanPayload(text, state._pendingFiles || []);
  if (draftScan.worstAction === 'block') return true;
  if (piiHistoryWorstAction(chat) === 'block') return true;
  // Phase B: classification gate. Any attached file whose detected level
  // has effective_action='block' or 'force_local' forces the composer
  // into local-only mode (parallels piiBlockActive).
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

// If PII is present + block is on + the current model is cloud, swap to the
// configured local fallback (or first local model). Returns true if a swap
// happened. Safe to call idempotently.
function piiEnsureLocalModel() {
  const chat = state.activeChat;
  if (!chat) return false;
  if (!piiBlockActive(chat)) return false;
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
  // Close dropdown
  document.getElementById('sb-agent-dropdown').classList.add('hidden');
  document.getElementById('sb-agent-selector').classList.remove('open');

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
  const agent = state.agents.find(a => (a.id || a.name) === state.activeAgentId);
  if (!agent) return;
  const aid = agent.id || agent.name;
  const display = agent.display_name || aid;

  document.getElementById('sb-agent-selector-name').textContent = display;
  document.getElementById('sb-agent-avatar-icon').textContent = aid;

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
  for (const id of ['nav', 'favourites', 'recent']) {
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

function renderRecentChats() {
  // Always refresh the favourites sidebar block alongside Recent — same poll cadence.
  try { window.Favourites?.renderSidebar?.(); } catch(_) {}
  const container = document.getElementById('sb-recent-chats');
  // Sidebar shows scheduled runs whenever the user is browsing the scheduled
  // view OR currently looking at a read-only scheduled-run timeline (which
  // technically lives in the chat view). Keeps the runs list pinned while the
  // user click-through-explores the runs.
  const onScheduledRun = state.currentView === 'chat'
    && state.activeChat?._readonly
    && state.activeChat?._scheduledRun;
  if (state.currentView === 'scheduled' || onScheduledRun) {
    renderRecentScheduledRuns(container);
    return;
  }
  // In any project context — the projects list, a project detail page, or a
  // chat that belongs to a project — the sidebar shows project chats the user
  // has access to. Project access is gated server-side via /v1/projects.
  // Inversely, the normal-chat sidebar excludes every project chat so the two
  // worlds stay visually separated.
  const inProjectContext = state.currentView === 'project-detail'
    || state.currentView === 'projects'
    || !!state.currentProject;
  if (inProjectContext) {
    renderRecentProjectChats(container);
    return;
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
    allSessions.sort((a,b) => new Date(b.last_active||0) - new Date(a.last_active||0));
    allSessions = allSessions.slice(0, 15);
    renderSessionsList(container, allSessions);
    return;
  }

  const data = state.agentSessions[state.activeAgentId];
  if (!data?.sessions) { container.innerHTML = ''; return; }

  const sessions = data.sessions
    .filter(s => s.status !== 'archived' && s.status !== 'code'
      && (s.message_count || 0) > 0
      && !(s.project || ''))
    .sort((a,b) => new Date(b.last_active||0) - new Date(a.last_active||0))
    .slice(0, 20);

  renderSessionsList(container, sessions);
}

function renderSessionsList(container, sessions) {
  container.innerHTML = '';
  container.dataset.mode = 'chats';
  for (const s of sessions) {
    const div = document.createElement('div');
    const sid = s.id || s.session_id;
    const sagent = s.agent_id || s.agent || s.agentId || state.activeAgentId;
    div.className = 'sb-session-item' + (state.activeChat?.sessionId === sid ? ' active' : '');
    // Title primary; summary is hover-only. Falls back to summary only when
    // no title (rare — pre-first-turn rows).
    const title = s.title || s.summary || `Chat ${sid?.substring(0,6)}`;  // "Chat" identical in German
    const tip = s.summary ? ` title="${esc(s.summary)}"` : '';
    div.innerHTML = `
      <span class="sb-sess-icon"><svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg></span>
      <span class="sb-session-title"${tip}>${esc(title)}</span>
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
  }
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
      if (s.status === 'archived' || s.status === 'code') continue;
      if ((s.message_count || 0) === 0) continue;
      const proj = s.project || '';
      if (!proj) continue;
      const key = agentId + '::' + proj;
      if (!accessSet.has(key)) continue;
      if (currentKey && key === currentKey) continue;
      sessions.push({...s, agentId});
    }
  }
  sessions.sort((a, b) => new Date(b.last_active || 0) - new Date(a.last_active || 0));
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
    const runs = (histRes.history || []).filter(h => liveNames.has(h.schedule_name)).slice(0, 20);
    // Bail if dataset.mode flipped to 'chats' meanwhile (user navigated to a
    // chat-centric view).
    if (container.dataset.mode !== 'runs') return;
    if (!runs.length) {
      container.innerHTML = '<div class="sb-session-item" style="opacity:.6;cursor:default">Noch keine geplanten Ausführungen</div>';
      return;
    }
    // Cheap stable signature to skip the DOM rebuild when nothing changed
    // (id+status; status changes mid-run, e.g. running → success).
    const sig = runs.map(h => `${h.id}:${h.status||''}`).join(',');
    const activeId = state.activeScheduledRunId || null;
    if (container.dataset.sig === sig && container.dataset.activeRun === String(activeId || '')) {
      return;
    }
    container.dataset.sig = sig;
    container.dataset.activeRun = String(activeId || '');
    container.innerHTML = '';
    for (const h of runs) {
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

