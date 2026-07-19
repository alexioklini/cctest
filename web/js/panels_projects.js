// panels_projects.js — project list/detail/files/members/CRUD/instructions. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

// Cached check: is the auto-route classifier in an LLM-driven mode (llm/hybrid)?
// In that mode the citation discipline is dynamic (effective-tools-driven,
// server-side) and the per-project research_mode flag is disabled. Cached for
// the session — classifier mode is a server config, stable across a session.
let _classifierModeCache = null;
async function classifierModeIsLlm() {
  if (_classifierModeCache !== null) return _classifierModeCache;
  try {
    const svc = await API.getServices();
    const mode = ((svc && svc.server && svc.server.auto_route_classifier_mode) || 'keywords');
    _classifierModeCache = (mode === 'llm' || mode === 'hybrid');
  } catch (e) {
    _classifierModeCache = false;  // fail-open: keep the manual flag usable
  }
  return _classifierModeCache;
}

/* ═══════════════════════════════════════════════════════════
   PROJECTS LIST
   ═══════════════════════════════════════════════════════════ */
/* ═══════════════════════════════════════════════════════════
   PROJECTS — Claude.ai-style list + detail + CRUD
   ═══════════════════════════════════════════════════════════ */
let _allProjectsCache = [];
let _projectsFilter = 'active';

async function loadProjectsList() {
  const list = document.getElementById('projects-list');
  list.innerHTML = '<div style="padding:16px;color:var(--text-400)">Wird geladen …</div>';

  try {
    let allProjects = [];
    for (const agent of state.agents) {
      try {
        const aid = agent.id || agent.name;
        const data = await API.getProjects(aid);
        const projects = data.projects || [];
        for (const p of projects) {
          allProjects.push({...p, agentId: aid, agentDisplay: agent.display_name || aid});
        }
        state.agentProjects[aid] = projects;
      } catch(e) {}
    }
    _allProjectsCache = allProjects;
    renderProjectsList();
  } catch(e) {
    list.innerHTML = '<div style="padding:16px;color:var(--error)">Projekte konnten nicht geladen werden</div>';
  }
}

function renderProjectsList() {
  const list = document.getElementById('projects-list');
  const query = (document.getElementById('projects-search')?.value || '').toLowerCase();
  const sortBy = document.getElementById('projects-sort-select')?.value || 'activity';

  let filtered = _allProjectsCache.filter(p => {
    const statusMatch = _projectsFilter === 'active'
      ? (p.status || 'active') !== 'archived'
      : (p.status || 'active') === 'archived';
    if (!statusMatch) return false;
    if (query) {
      return (p.display_name || p.name || '').toLowerCase().includes(query) ||
             (p.name || '').toLowerCase().includes(query) ||
             (p.description || '').toLowerCase().includes(query);
    }
    return true;
  });

  // Sort
  if (sortBy === 'name') {
    filtered.sort((a, b) => (a.display_name || a.name || '').localeCompare(b.display_name || b.name || ''));
  } else if (sortBy === 'created') {
    filtered.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  }
  // 'activity' = default order (already sorted by filesystem)

  list.innerHTML = '';
  list.classList.add('project-grid');
  if (!filtered.length) {
    list.innerHTML = '<div class="project-grid-empty">Keine Projekte gefunden</div>';
    return;
  }
  for (let i = 0; i < filtered.length; i++) {
    const p = filtered[i];
    const item = document.createElement('div');
    item.className = 'project-card';
    const timeAgo = p.created_at ? formatTimeAgo(new Date(p.created_at)) : '';
    const agent = p.agentId || 'main';
    const hasImage = !!p.image;
    const imgUrl = hasImage
      ? `/v1/agents/${encodeURIComponent(agent)}/projects/${encodeURIComponent(p.name)}/image`
      : '';
    // Treat the type-default emoji as "no custom icon" → render line-art glyph.
    const rawIcon = (p.icon && p.icon.length <= 4) ? p.icon : '';
    const customIcon = (rawIcon && rawIcon !== '📁') ? rawIcon : '';
    // Code projects get a distinct </> glyph in the overview (unless the owner
    // set a custom emoji icon).
    const codeGlyphSvg = '<svg viewBox="0 0 24 24" width="44" height="44" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
    const glyphHtml = customIcon
      ? esc(customIcon)
      : (p.code_mode ? codeGlyphSvg : (window.Favourites?.typeGlyphSvg?.('project', 44) || ''));
    const artClass = hasImage ? 'project-card-art has-image' : 'project-card-art';
    const artStyle = hasImage
      ? `style="background-image:url('${esc(imgUrl)}');background-size:cover;background-position:center"`
      : '';
    const displayName = p.display_name || p.name;
    const titleAttr = p.description ? ` title="${esc(p.description)}"` : '';
    item.innerHTML = `
      <div class="${artClass}" ${artStyle}>
        ${hasImage ? '<div class="project-card-art-overlay"></div>' : ''}
        ${!hasImage ? `<span class="project-card-glyph">${glyphHtml}</span>` : ''}
        <div class="project-card-fav-slot" onclick="event.stopPropagation()"></div>
        <button class="project-card-menu" onclick="event.stopPropagation(); showProjectListMenu(event, '${esc(agent)}', '${esc(p.name)}')" title="Weitere Optionen">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
        </button>
      </div>
      <div class="project-card-info">
        <div class="project-card-title"${titleAttr}>${esc(displayName)}</div>
        <div class="project-card-meta">
          <span class="project-card-type">${p.code_mode ? 'Code-Projekt' : 'Projekt'}</span>
          ${timeAgo ? `<span>· ${esc(timeAgo)}</span>` : ''}
        </div>
      </div>
    `;
    item.onclick = () => openProject(agent, p.name);
    if (p.id && window.Favourites?.mount) {
      const slot = item.querySelector('.project-card-fav-slot');
      if (slot) {
        window.Favourites.mount(slot, {
          item_type: 'project',
          item_id: p.id,
          agent_id: agent,
          simple: true,
        });
      }
    }
    list.appendChild(item);
  }
}

function filterProjectsList() { renderProjectsList(); }
function sortProjectsList() { renderProjectsList(); }

function setProjectFilter(filter, el) {
  _projectsFilter = filter;
  document.querySelectorAll('.projects-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  renderProjectsList();
}

function formatTimeAgo(date) {
  if (!date || isNaN(date.getTime())) return '';
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'gerade eben';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `vor ${minutes} Min.`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `vor ${days} T.`;
  const months = Math.floor(days / 30);
  return `vor ${months} Mon.`;
}

function openProject(agentId, projectName) {
  selectAgent(agentId);
  state.currentProject = projectName;
  state._projectDetailAgent = agentId;
  state._projectDetailName = projectName;
  // The project-landing composer creates a FRESH chat on send (sendMessage's
  // project branch calls newChat({inheritProject})), so it must show composer
  // defaults — not whatever the previously-viewed chat had set. Entering a
  // project does not run openSession()/newChat(), so reset the active chat's
  // composer-mode fields here and let the toggle-button sync below repaint
  // them. Without this, caveman / memory / thinking / PII-shield carry over
  // from the last chat the user looked at.
  const _pchat = state.ensureAgentChat(agentId);
  if (_pchat) {
    const _def = state.defaultComposerModes();
    _pchat.cavemanMode = _def.cavemanMode;
    _pchat.saveToMemory = _def.saveToMemory;
    _pchat.memoryMode = _def.memoryMode;
    _pchat.thinkingLevel = _def.thinkingLevel;
    // Fresh project chat → agent's DEFAULT model, never the last picked one
    // (mirrors newChat(); without this the model selector keeps the prior
    // chat's model). autoPicked/autoReason cleared so no stale auto-route hint.
    if (typeof state.defaultModelForAgent === 'function') {
      _pchat.model = state.defaultModelForAgent(agentId);
    }
    _pchat.autoPicked = null;
    _pchat.autoReason = '';
    // Fresh project chat → ALL GDPR/PII state to defaults (consent, mapping,
    // per-finding decisions, history scans) via the single reset point, so no
    // analysis leaks from the prior conversation. (Was a partial reset that
    // missed _piiDecisions + the server/worst history caches.)
    resetChatGdprState(_pchat);
    // Fresh project chat → empty Websuche basket (mirrors newChat()).
    _pchat.webBasket = [];
    // Repaint the composer controls from the freshly-reset state. The
    // 'project-detail' nav branch does NOT run updateChatView()/updateStatusBar()
    // (per feedback_composer_controls_are_source_of_truth), so without this the
    // GDPR shield / thinking / Websuche / model controls keep showing the
    // PREVIOUS chat's state and the stale button wins at send time.
    if (typeof refreshThinkingButton === 'function') refreshThinkingButton();
    if (typeof _refreshWebsuche === 'function') _refreshWebsuche();
    if (typeof updateModelSelectorDisplay === 'function') updateModelSelectorDisplay(_pchat.model);
    if (typeof updateStatusBar === 'function') updateStatusBar();
    if (typeof schedulePIIBadgeUpdate === 'function') schedulePIIBadgeUpdate();
  }
  // Default to the Active tab on entry; the tab UI also resets visually below.
  state._projectChatsFilter = 'active';
  document.querySelectorAll('.project-chats-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.pcfilter === 'active');
  });
  navigateTo('project-detail', { agentId, projectName });
  // Wire the right-pane resize handle once the view is on screen. Idempotent
  // — repeat calls are short-circuited via the handle's _bound flag.
  initProjectDetailPanelResize();
}

async function _editProjectImageUpload(ev, agentId, projectName) {
  const file = ev?.target?.files?.[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { await showAlert('Bild zu groß (max. 2 MB).'); return; }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(
      `${BASE_URL}/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`,
      { method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth-token') || ''}` },
        body: fd });
    if (!r.ok) { await showAlert(`Hochladen fehlgeschlagen: ${r.status}`); return; }
    const data = await r.json();
    const preview = document.getElementById('edit-project-image-preview');
    const label   = document.getElementById('edit-project-image-label');
    const clear   = document.getElementById('edit-project-image-clear');
    if (preview) preview.style.backgroundImage = `url('/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image?v=${Date.now()}')`;
    if (label) label.textContent = 'Ersetzen';
    if (clear) clear.style.display = 'inline-block';
    if (window._projectEditOriginal) window._projectEditOriginal.image = data.image || '';
    try { await window.Favourites?.reload?.(); } catch(_) {}
    // Refresh the projects list cache so the card reflects the new image
    // when the user closes the modal and returns to the list.
    try { await loadProjectsList(); } catch(_) {}
  } catch (e) {
    await showAlert(`Hochladen fehlgeschlagen: ${e.message || e}`);
  } finally {
    ev.target.value = '';
  }
}

async function _editProjectImageClear(agentId, projectName) {
  if (!await showConfirmDanger('Dieses Projektbild entfernen?', 'Bild entfernen', 'Entfernen')) return;
  try {
    await API.del(`/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`);
    const preview = document.getElementById('edit-project-image-preview');
    const label   = document.getElementById('edit-project-image-label');
    const clear   = document.getElementById('edit-project-image-clear');
    if (preview) preview.style.backgroundImage = '';
    if (label) label.textContent = 'Hochladen';
    if (clear) clear.style.display = 'none';
    if (window._projectEditOriginal) window._projectEditOriginal.image = '';
    try { await window.Favourites?.reload?.(); } catch(_) {}
    try { await loadProjectsList(); } catch(_) {}
  } catch (e) {
    await showAlert(`Entfernen fehlgeschlagen: ${e.message || e}`);
  }
}

function paintProjectDetailBanner(agentId, projectName, project) {
  const banner = document.getElementById('project-detail-banner');
  const glyph  = document.getElementById('project-detail-banner-glyph');
  const remove = document.getElementById('project-detail-banner-remove');
  const label  = document.getElementById('project-detail-banner-upload-label');
  if (!banner) return;
  const palette = ['#6366f1','#8b5cf6','#0ea5e9','#10b981','#f59e0b','#ec4899','#475569','#0f172a'];
  const accent = project?.color
    || palette[(project?.name || '').split('').reduce((s,c) => s + c.charCodeAt(0), 0) % palette.length];
  const icon = (project?.icon && project.icon.length <= 4) ? project.icon : '📁';
  if (project?.image) {
    const url = `/v1/agents/${encodeURIComponent(agentId || 'main')}/projects/${encodeURIComponent(projectName)}/image?v=${Date.now()}`;
    banner.style.backgroundImage = `url('${url}')`;
    banner.style.backgroundSize = 'cover';
    banner.style.backgroundPosition = 'center';
    banner.style.background = '';
    if (glyph) glyph.style.display = 'none';
    if (remove) remove.style.display = '';
    if (label) label.textContent = 'Bild ersetzen';
  } else {
    banner.style.backgroundImage = '';
    banner.style.background = accent;
    if (glyph) { glyph.style.display = ''; glyph.textContent = icon; }
    if (remove) remove.style.display = 'none';
    if (label) label.textContent = 'Bild hochladen';
  }
}

async function handleProjectImageUpload(ev) {
  const file = ev?.target?.files?.[0];
  if (!file) return;
  const project = state._projectDetail;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!project || !agentId || !projectName) return;
  if (file.size > 2 * 1024 * 1024) {
    await showAlert('Bild zu groß (max. 2 MB).');
    return;
  }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(
      `${BASE_URL}/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`,
      {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth-token') || ''}` },
        body: fd,
      });
    if (!r.ok) {
      await showAlert(`Hochladen fehlgeschlagen: ${r.status}`);
      return;
    }
    const data = await r.json();
    project.image = data.image || '';
    paintProjectDetailBanner(agentId, projectName, project);
    // Reload the favourites cache so any favourite of this project picks up
    // the new source_image_url on next render.
    try { await window.Favourites?.reload?.(); } catch(_) {}
  } catch (e) {
    await showAlert(`Hochladen fehlgeschlagen: ${e.message || e}`);
  } finally {
    ev.target.value = '';
  }
}

async function removeProjectImage() {
  const project = state._projectDetail;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!project || !agentId || !projectName) return;
  if (!await showConfirmDanger('Dieses Projektbild entfernen?', 'Bild entfernen', 'Entfernen')) return;
  try {
    await API.del(`/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`);
    project.image = '';
    paintProjectDetailBanner(agentId, projectName, project);
    try { await window.Favourites?.reload?.(); } catch(_) {}
  } catch (e) {
    await showAlert(`Entfernen fehlgeschlagen: ${e.message || e}`);
  }
}

async function loadProjectDetail(agentId, projectName) {
  // Load project config
  try {
    const project = await API.getProject(agentId, projectName);
    if (!project) {
      showToast('Projekt nicht gefunden');
      navigateTo('projects');
      return;
    }
    state._projectDetail = project;

    // No page-header title: the view renders the project name (with its own
    // favourite star) in its own heading, so a top-bar title + star would be
    // redundant. Clearing it lets the empty header collapse.
    updatePageHeader('');

    // Render header
    document.getElementById('project-detail-name').textContent = project.name || projectName;
    const descEl = document.getElementById('project-detail-desc');
    if (project.description) {
      const desc = project.description;
      if (desc.length > 200) {
        descEl.innerHTML = esc(desc.slice(0, 200)) + '... <span class="project-detail-desc-toggle" onclick="toggleProjectDesc()">Mehr anzeigen</span>';
        descEl.dataset.full = desc;
        descEl.dataset.collapsed = 'true';
      } else {
        descEl.textContent = desc;
      }
    } else {
      descEl.innerHTML = '<span style="color:var(--text-400);font-style:italic">Keine Beschreibung</span>';
    }

    // Render the Research / Q&A project control. Under LLM-classifier mode the
    // citation discipline is applied DYNAMICALLY (from the effective tools,
    // server-side), so the manual flag is meaningless — HIDE it entirely and
    // show an explanatory note instead (a disabled-but-visible unchecked box
    // misleadingly reads as 'off'). Keyword mode keeps the manual control.
    const researchCb = document.getElementById('project-research-mode-checkbox');
    const researchManual = document.getElementById('project-research-manual');
    const researchNote = document.getElementById('project-research-dynamic-note');
    classifierModeIsLlm().then(isLlm => {
      if (researchCb) researchCb.checked = !isLlm && !!project.research_mode;
      if (researchManual) researchManual.style.display = isLlm ? 'none' : '';
      if (researchNote) researchNote.style.display = isLlm ? '' : 'none';
    });
    // Render the 'disable web search' checkbox state.
    const disableWebCb = document.getElementById('project-disable-web-checkbox');
    if (disableWebCb) {
      disableWebCb.checked = !!project.disable_web_search;
    }
    // Code Mode is fixed at creation. For a code project: show the Code Mode
    // section (working-dir + init), hide the Sources/ingest tree. For a normal
    // project: hide the Code Mode section entirely. No toggle (immutable mode).
    const cmSection = document.getElementById('project-codemode-section');
    const cmWd = document.getElementById('project-codemode-wd');
    const isCode = !!project.code_mode;
    // Code projects get a header "Terminal" button to open the terminal/editor
    // workspace directly — no need to start a chat first.
    const termBtn = document.getElementById('project-detail-terminal-btn');
    if (termBtn) termBtn.style.display = isCode ? 'inline-flex' : 'none';
    if (cmSection) cmSection.style.display = isCode ? '' : 'none';
    if (cmWd) cmWd.textContent = project.working_dir || '— (noch kein Verzeichnis gewählt)';
    // In code mode there is no MemPalace/ingest, so the project-mode, sources,
    // knowledge-graph and storage/sync sections are all irrelevant → hide them.
    ['project-mode-section', 'project-sources-section',
     'project-kg-section', 'project-sync-section'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = isCode ? 'none' : '';
    });
    // Datenquellen-Sektion rendert in normalen UND Code-Projekten (E9) —
    // deshalb NICHT in der Hide-Liste oben. Async, fire-and-forget.
    renderProjectDataSources(project);
    // Code project → show its working-directory file tree (refreshed here on
    // open; also after each turn via the post-turn hook) and start polling for
    // init progress + auto-refresh on file changes. Non-code projects stop any
    // leftover poller.
    if (isCode) {
      // Files list defaults collapsed; restore the user's last choice.
      if (typeof toggleCodeModeFiles === 'function') {
        let open = false;
        try { open = localStorage.getItem('project-codemode-files-open') === '1'; } catch (e) {}
        toggleCodeModeFiles(open);
      }
      if (typeof refreshCodeWorkingTree === 'function') refreshCodeWorkingTree();
      _renderInitStatus({ state: 'idle' });
      if (typeof startCodeModePoll === 'function') startCodeModePoll();
      if (typeof startCodeIndexPoll === 'function') startCodeIndexPoll();
    } else {
      if (typeof stopCodeModePoll === 'function') stopCodeModePoll();
      if (typeof stopCodeIndexPoll === 'function') stopCodeIndexPoll();
    }
    if (typeof terminalRefreshToggle === 'function') terminalRefreshToggle();
    // Per-project KG method/profile overrides (empty = inherit the global
    // default). Profile is inert under the rule-based method (generic only).
    const kgMethodSel = document.getElementById('project-kg-method');
    const kgProfileSel = document.getElementById('project-kg-profile');
    if (kgMethodSel) kgMethodSel.value = project.kg_method || '';
    if (kgProfileSel) {
      kgProfileSel.value = project.kg_profile || '';
      const rules = (project.kg_method || '') === 'rules';
      kgProfileSel.disabled = rules;
      kgProfileSel.style.opacity = rules ? '0.5' : '';
    }

    // Instructions now render inside the unified source tree (the Anweisungen
    // node) — no separate panel section.

    // Personalise the composer placeholder with the project name. Falls back
    // to the routing slug when the display name is missing.
    const composerInput = document.getElementById('project-input');
    if (composerInput) {
      const displayName = project.name || projectName;
      composerInput.placeholder = `Ihre Nachricht an ${displayName}`;
    }

    // Unified source tree (files + folders + web URLs + instructions) with
    // per-item MemPalace state colors + collapse/expand to file level. Replaces
    // the four separate panel sections.
    renderProjectSourceTree();

    // Apply the persisted "Hilfe" toggle state to this freshly-rendered panel.
    applyProjectHelpState();

    // Start polling sync status (repaints the tree's state dots).
    startProjectSyncPoll(agentId, projectName);

    // Load project conversations
    loadProjectChats(agentId, projectName);
  } catch(e) {
    showToast('Projekt konnte nicht geladen werden');
    console.error(e);
  }
}

// Render the right-side status pill for one item ("attachment:<hash>" or
// "folder:<abs path>"). Returns inline HTML so callers can splice it into
// their row template — but we also use it imperatively via paintProjectItemPills().
function projectItemPillHtml(kind, ident) {
  const items = state._projectSyncItems || {};
  const key = `${kind}:${ident}`;
  const it = items[key];
  if (!it) {
    return '<span class="project-item-pill" data-state="pending" title="Wartet auf den nächsten Abgleichzyklus">ausstehend</span>';
  }
  const stateName = it.state || 'pending';
  const tip = it.error ? it.error
            : (stateName === 'indexed'
                ? `Indexiert ${it.drawers_filed != null ? '(' + it.drawers_filed + ' Schubladen)' : ''}`.trim()
                : (stateName === 'syncing' ? 'Abgleich läuft …' : 'Ausstehend'));
  let label = stateName;
  if (stateName === 'indexed') label = 'indexiert';
  else if (stateName === 'syncing') label = 'wird abgeglichen …';
  else if (stateName === 'error') label = 'Fehler';
  let kgBadge = '';
  const kgState = it.kg_state || '';
  const triples = it.triples_extracted;
  const kgParseErrors = it.kg_parse_errors || 0;
  if (kgState === 'extracting') {
    kgBadge = ` <span class="project-item-pill" data-kg="extracting" title="Inhalte werden verknüpft">wird verknüpft …</span>`;
  } else if (kgState === 'error') {
    const kgErr = it.kg_last_error || 'Verknüpfen fehlgeschlagen';
    const triplesPart = (typeof triples === 'number' && triples > 0) ? `${triples} Beziehungen · ` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="error" title="${esc(kgErr)}">${triplesPart}Verknüpfen fehlgeschlagen</span>`;
  } else if (typeof triples === 'number' && triples > 0) {
    // Per-folder pill in the right pane — plain German, no jargon.
    // ("triples" → "Beziehungen", "Parse-Fehler" → "übersprungene Abschnitte").
    const warnPart = kgParseErrors > 0 ? ` · ${kgParseErrors} übersprungen` : '';
    const warnTitle = kgParseErrors > 0 ? ` (${kgParseErrors} Abschnitte konnten nicht ausgewertet werden — nicht kritisch)` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="${kgParseErrors > 0 ? 'warn' : 'ok'}" title="Aus diesem Ordner gewonnene Beziehungen${warnTitle}">${triples} Beziehungen${warnPart}</span>`;
  }
  return `<span class="project-item-pill" data-state="${stateName}" title="${esc(tip)}">${esc(label)}</span>${kgBadge}`;
}

// Imperative re-paint after each /sync-status poll. Cheaper than re-rendering
// the lists, and preserves DOM identity (no flicker).
function paintProjectItemPills() {
  document.querySelectorAll('[data-pif-pill]').forEach(el => {
    const kind = el.getAttribute('data-pif-kind');
    const ident = el.getAttribute('data-pif-id');
    if (!kind || !ident) return;
    el.outerHTML = `<span data-pif-pill data-pif-kind="${esc(kind)}" data-pif-id="${esc(ident)}">${projectItemPillHtml(kind, ident)}</span>`;
  });
}

function humanAgo(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (!t) return '';
  const sec = Math.max(0, (Date.now() - t) / 1000);
  if (sec < 60) return Math.floor(sec) + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h';
  return Math.floor(sec / 86400) + 'd';
}

// Human "in 4h" / "in 12m" — counterpart to humanAgo for future timestamps.
// Returns 'now' if the target is in the past or within a minute (so a stale
// next-run doesn't read as "in 0s"; the user just sees the cycle is due).
function humanIn(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (!t) return '';
  const sec = (t - Date.now()) / 1000;
  if (sec < 60) return 'jetzt';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h';
  return Math.floor(sec / 86400) + 'd';
}

function toggleProjectDesc() {
  const descEl = document.getElementById('project-detail-desc');
  if (descEl.dataset.collapsed === 'true') {
    descEl.textContent = descEl.dataset.full;
    descEl.dataset.collapsed = 'false';
  } else {
    const desc = descEl.dataset.full;
    descEl.innerHTML = esc(desc.slice(0, 200)) + '... <span class="project-detail-desc-toggle" onclick="toggleProjectDesc()">Mehr anzeigen</span>';
    descEl.dataset.collapsed = 'true';
  }
}

// Shim: files now render inside the unified source tree. Refresh the Dateien
// branch in place (re-fetches /docs) so upload/delete callers stay working.
async function loadProjectFiles(agentId, projectName) {
  if (typeof _ptFillFiles === 'function' && document.getElementById('pt-items-files')) {
    return _ptFillFiles(agentId, projectName);
  }
}

// Ingest ONE file into the project corpus. Returns the parsed server result
// (with .source_hash on success, or .error). Shared by the flat upload and the
// folder-structure upload so both hit the identical /ingest path.
async function _ingestOneProjectFile(agentId, projectName, file, relPath) {
  // Auth header is required — the global /v1/* gate rejects anonymous POST.
  // Don't set Content-Type; the browser inserts the multipart boundary.
  const token = localStorage.getItem('auth-token') || '';
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
  const formData = new FormData();
  // For a folder upload, file.name carries the FULL relative path
  // ("Sub/Datei.pdf"); send only the basename as the multipart filename so the
  // server's temp-write path is safe. The folder structure is preserved
  // separately via source_groups. (The server also basenames defensively.)
  const baseName = (file.name || 'datei').replace(/\\/g, '/').split('/').pop() || 'datei';
  formData.append('file', file, baseName);
  // For a folder import, also send the relative path so two same-named files in
  // different groups get DISTINCT source keys server-side (else the second
  // overwrites the first — same stem). Single-file uploads omit it.
  if (relPath && relPath !== baseName) formData.append('rel_path', relPath);
  const resp = await fetch(`${BASE_URL}/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/ingest`, {
    method: 'POST', headers, body: formData,
  });
  const body = await resp.json().catch(() => null);
  // Always surface a readable reason on failure: prefer the server's own error
  // string (translated for the common cases), else map the HTTP status.
  if (!resp.ok || (body && body.error)) {
    const raw = (body && body.error)
      || (resp.status === 413 ? 'Datei zu groß'
        : resp.status === 415 ? 'Dateityp nicht unterstützt'
        : `HTTP ${resp.status}`);
    return { error: _ingestReasonDe(raw) };
  }
  return body || { error: `HTTP ${resp.status}` };
}

// Translate the common server-side ingest error strings into something a German
// UI user understands; unknown strings pass through verbatim.
function _ingestReasonDe(raw) {
  const s = String(raw || '');
  if (/no content extracted/i.test(s)) return 'Kein Text extrahierbar (leer, gescanntes Bild ohne OCR, oder zu kurz)';
  if (/unsupported format/i.test(s)) {
    const m = s.match(/unsupported format:\s*(\S+)/i);
    return `Dateityp nicht unterstützt${m ? ` (${m[1].replace(/[.,]$/, '')})` : ''}`;
  }
  if (/file not found/i.test(s)) return 'Datei nicht gefunden';
  if (/too large/i.test(s)) return 'Datei zu groß';
  return s;
}

async function uploadProjectFiles(files) {
  if (!files || !files.length) return;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const items = Array.from(files).map(f => ({ file: f, relPath: '', dispName: f.name }));
  const title = items.length === 1 ? 'Datei wird hochgeladen …' : 'Dateien werden hochgeladen …';
  await _runProjectImport(agentId, projectName, items, { title });
}

// ── Two-phase import driver (shared: file upload + folder import) ───────────
// Phase 1 uploads with a small pool — since 9.324.0 /ingest only STAGES the
// bytes and returns immediately (source_hash reserved), so uploads are cheap
// and safe to parallelise. Phase 2 watches the background extraction via
// /ingest-status until every accepted file is terminal; the button becomes
// "Im Hintergrund fortsetzen" (extraction keeps running server-side, status
// stays visible in the file tree). onAccepted(item, result) fires per staged
// file (the folder import assigns source groups there).
async function _runProjectImport(agentId, projectName, items, { title, onAccepted } = {}) {
  const total = items.length;
  _folderImportProgressOpen(total, title);
  const failures = [];
  const watched = [];        // [{key, name}] accepted → background extraction
  let done = 0, aborted = false;

  const q = items.slice();
  const workers = Array.from({ length: Math.min(4, q.length) }, async () => {
    while (q.length) {
      if (window._folderImportAborted) { aborted = true; return; }
      const item = q.shift();
      const name = item.dispName || item.file.name;
      try {
        const result = await _ingestOneProjectFile(agentId, projectName, item.file, item.relPath);
        if (result.error) failures.push({ name, reason: result.error });
        else if (!result.source_hash) failures.push({ name, reason: 'kein Inhalt extrahiert' });
        else {
          watched.push({ key: result.source_hash, name });
          if (onAccepted) { try { onAccepted(item, result); } catch (_) {} }
        }
      } catch (e) {
        failures.push({ name, reason: (e && e.message) || 'Netzwerkfehler' });
      }
      done++;
      _folderImportProgressUpdate(done, total, name, failures.length);
    }
  });
  await Promise.all(workers);

  let backgrounded = false;
  let extracted = watched.length;
  if (watched.length && !aborted) {
    _folderImportPhaseExtract(watched.length);
    const res = await _ingestAwaitExtraction(agentId, projectName, watched);
    backgrounded = res.backgrounded;
    failures.push(...res.failures);
    extracted = watched.length - res.failures.length;
  }
  window._folderImportAborted = false;
  loadProjectFiles(agentId, projectName);
  if (typeof renderProjectSourceTree === 'function') renderProjectSourceTree();
  if (backgrounded) {
    _folderImportProgressClose();
    showToast('Import läuft im Hintergrund weiter — Status im Datei-Baum');
  } else if (!failures.length && !aborted) {
    _folderImportProgressClose();
    showToast(total === 1 ? `${items[0].dispName || items[0].file.name} importiert`
                          : `${extracted} Datei(en) importiert`);
  } else {
    _folderImportShowResult({ ok: extracted, total, aborted, failures });
  }
  return { ok: extracted, total, aborted, backgrounded, failures };
}

// Switch the progress modal into extraction-watch mode (phase 2).
function _folderImportPhaseExtract(totalWatched) {
  const ov = document.getElementById('folder-import-progress');
  if (!ov) return;
  const h2 = ov.querySelector('h2');
  if (h2) h2.textContent = 'Inhalte werden extrahiert …';
  const cur = document.getElementById('fip-current');
  if (cur) cur.textContent = 'Extraktion läuft (gescannte PDFs mit OCR können mehrere Minuten dauern) …';
  const bar = document.getElementById('fip-bar');
  if (bar) bar.style.width = '0%';
  const cnt = document.getElementById('fip-count');
  if (cnt) cnt.textContent = `0 / ${totalWatched}`;
  const btn = document.getElementById('fip-abort');
  if (btn) { btn.disabled = false; btn.textContent = 'Im Hintergrund fortsetzen'; }
}

// Poll /ingest-status until every watched key is terminal. Closing via the
// phase-2 button ("Im Hintergrund fortsetzen") stops WATCHING only — the
// server-side jobs keep running (per-file cancel lives in the file tree).
async function _ingestAwaitExtraction(agentId, projectName, watched) {
  const failures = [];
  const pending = new Set(watched.map(w => w.key));
  const nameByKey = {};
  watched.forEach(w => { nameByKey[w.key] = w.name; });
  const totalW = watched.length;
  while (pending.size) {
    if (window._folderImportAborted) return { failures, backgrounded: true };
    await new Promise(r => setTimeout(r, 2000));
    if (window._folderImportAborted) return { failures, backgrounded: true };
    let st;
    try { st = await API.getProjectIngestStatus(agentId, projectName); }
    catch (_) { continue; }
    state._projectIngestJobs = (st && st.jobs) || {};
    let currentName = '';
    for (const key of Array.from(pending)) {
      const job = state._projectIngestJobs[key];
      if (!job) {
        // Server restart pruned the registry: if nothing at all is pending
        // any more, treat the key as finished instead of waiting forever.
        if (!st.pending) pending.delete(key);
        continue;
      }
      if (job.state === 'done') pending.delete(key);
      else if (job.state === 'error') {
        failures.push({ name: nameByKey[key], reason: _ingestReasonDe(job.error) });
        pending.delete(key);
      } else if (job.state === 'cancelled') {
        failures.push({ name: nameByKey[key], reason: 'Abgebrochen' });
        pending.delete(key);
      } else if (job.state === 'extracting') {
        currentName = job.filename || nameByKey[key];
      }
    }
    _folderImportProgressUpdate(totalW - pending.size, totalW,
      currentName || 'Warten auf Extraktion …', failures.length);
    if (typeof _ptApplyIngestJobs === 'function') _ptApplyIngestJobs();
  }
  return { failures, backgrounded: false };
}

// Ingest a set of files that came from a dropped/picked FOLDER, preserving the
// folder structure as virtual source-groups (the "Gruppen" the project tree
// already supports). Each entry is { file, relPath } where relPath is the path
// from the dropped root, e.g. "MyFolder/sub/report.pdf". Files ingest via the
// same /ingest path as a single upload; afterwards we mirror each file's
// directory chain into source_groups.files.groups (depth-capped at the tree's
// 3-level limit; deeper dirs collapse onto the level-3 group, matching the
// server sanitiser) and assign each ingested source_hash to its leaf group.
// Existing groups are reused by (parent,name) so re-adding the same folder
// doesn't duplicate the tree.
// Entry point for both folder-import paths (picker + drag-drop). Shows a real
// confirmation dialog (not a browser confirm) summarising the import, then runs
// addProjectFolderFiles on accept. `entries` = [{file, relPath}].
function confirmProjectFolderImport(entries) {
  entries = (entries || []).filter(e => e && e.file);
  if (!entries.length) return;
  // Count distinct top-level folders + nesting for the summary.
  const folders = new Set();
  for (const e of entries) {
    const parts = String(e.relPath || e.file.name).split('/').filter(Boolean);
    if (parts.length > 1) folders.add(parts[0]);
  }
  const totalBytes = entries.reduce((s, e) => s + (e.file.size || 0), 0);
  const mb = (totalBytes / (1024 * 1024));
  const sizeStr = mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(totalBytes / 1024))} KB`;
  // Stash for the confirm handler (avoids serialising File objects into HTML).
  window._pendingFolderImport = entries;
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) { overlay.remove(); window._pendingFolderImport = null; } };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:520px">
    <h2 style="display:flex;align-items:center;gap:8px">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      Ordner importieren?
    </h2>
    <div style="font-size:13px;color:var(--text-300);line-height:1.6;margin:8px 0 16px">
      Es werden <strong>${entries.length}</strong> Datei(en)${folders.size ? ` aus <strong>${folders.size}</strong> Ordner(n)` : ''} (${esc(sizeStr)}) in dieses Projekt importiert.
      Die Ordnerstruktur wird als <strong>Gruppen</strong> übernommen.
      <div style="margin-top:8px;color:var(--text-400)">Die Dateien werden in den Projektspeicher aufgenommen (gemined).</div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove(); window._pendingFolderImport=null">Abbrechen</button>
      <button class="sched-create-btn" onclick="this.closest('.sched-modal-overlay').remove(); addProjectFolderFiles(window._pendingFolderImport); window._pendingFolderImport=null">Importieren</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

// Build / update / close a single progress modal for the folder import (replaces
// the overlapping per-file toasts). Includes an Abort button: the loop checks
// window._folderImportAborted between files and stops cleanly.
function _folderImportProgressOpen(total, title) {
  window._folderImportAborted = false;
  let ov = document.getElementById('folder-import-progress');
  if (!ov) {
    ov = document.createElement('div');
    ov.id = 'folder-import-progress';
    ov.className = 'sched-modal-overlay';
    ov.style.zIndex = '10002';
    document.body.appendChild(ov);
  }
  ov.innerHTML = `<div class="sched-modal" style="max-width:480px">
    <h2>${esc(title || 'Ordner wird importiert …')}</h2>
    <div style="font-size:13px;color:var(--text-300);margin:8px 0">
      <div id="fip-current" style="font-family:var(--font-mono);font-size:12px;color:var(--text-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:10px">Wird vorbereitet …</div>
      <div style="height:8px;background:var(--bg-100);border-radius:4px;overflow:hidden;border:1px solid var(--border-100)">
        <div id="fip-bar" style="height:100%;width:0%;background:var(--accent-500,#4a9eff);transition:width .15s"></div>
      </div>
      <div id="fip-count" style="margin-top:8px;text-align:right;color:var(--text-400)">0 / ${total}</div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" id="fip-abort" onclick="window._folderImportAborted=true; this.disabled=true; this.textContent='Wird abgebrochen …'">Abbrechen</button>
    </div>
  </div>`;
}
function _folderImportProgressUpdate(done, total, name, failed) {
  const bar = document.getElementById('fip-bar');
  const cnt = document.getElementById('fip-count');
  const cur = document.getElementById('fip-current');
  if (bar) bar.style.width = `${Math.round((done / Math.max(1, total)) * 100)}%`;
  if (cnt) cnt.textContent = `${done} / ${total}${failed ? ` · ${failed} fehlgeschlagen` : ''}`;
  if (cur && name) cur.textContent = name;
}
function _folderImportProgressClose() {
  document.getElementById('folder-import-progress')?.remove();
}

async function addProjectFolderFiles(entries) {
  entries = (entries || []).filter(e => e && e.file);
  if (!entries.length) return;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  if (!state._projectDetail) return;

  const MAX_DEPTH = (typeof _PT_MAX_DEPTH === 'number') ? _PT_MAX_DEPTH : 3;

  // Ensure the files bucket exists on the in-memory project detail.
  if (!state._projectDetail.source_groups) state._projectDetail.source_groups = {};
  if (!state._projectDetail.source_groups.files) state._projectDetail.source_groups.files = { groups: [], assign: {} };
  const bucket = state._projectDetail.source_groups.files;
  if (!bucket.groups) bucket.groups = [];
  if (!bucket.assign) bucket.assign = {};

  const mintId = (typeof _ptNewGroupId === 'function')
    ? _ptNewGroupId
    : () => 'g' + Math.random().toString(36).slice(2, 10);

  // Resolve (or create) the group chain for a list of directory segments,
  // returning the leaf group id ('' = root for a file at the folder root).
  // Caps at MAX_DEPTH groups — extra-deep segments are dropped so the file
  // lands in the deepest allowed group rather than an invalid one.
  function ensureGroupChain(segs) {
    let parent = '';
    let leaf = '';
    const limited = segs.slice(0, MAX_DEPTH);
    for (const name of limited) {
      let g = bucket.groups.find(x => (x.parent || '') === parent && x.name === name);
      if (!g) {
        g = { id: mintId(), name: name, parent: parent, order: bucket.groups.length };
        bucket.groups.push(g);
      }
      parent = g.id;
      leaf = g.id;
    }
    return leaf;
  }

  const items = entries.map(({ file, relPath }) => ({
    file, relPath, dispName: relPath || file.name,
  }));
  // Group assignment happens at ACCEPT time (phase 1) — the staged response
  // already carries the reserved source_hash, so grouping never waits on the
  // background extraction.
  await _runProjectImport(agentId, projectName, items, {
    onAccepted: (item, result) => {
      const parts = String(item.relPath || item.file.name).split('/').filter(Boolean);
      const leaf = ensureGroupChain(parts.slice(0, -1));
      if (leaf) bucket.assign[result.source_hash] = leaf;
    },
  });

  // Persist the groups for whatever was accepted so far (an aborted run keeps
  // the files it already staged, correctly grouped — nothing is rolled back).
  try {
    await API.updateProject(agentId, projectName, { source_groups: state._projectDetail.source_groups });
  } catch(e) {
    showToast('Gruppierung konnte nicht gespeichert werden: ' + (e.message || e), true);
  }
  if (typeof renderProjectSourceTree === 'function') renderProjectSourceTree();
}

// Replace the progress modal body with a final status: counts + a scrollable
// list of failed files with their reason. Stays open until the user closes it.
function _folderImportShowResult({ ok, total, aborted, failures }) {
  const ov = document.getElementById('folder-import-progress');
  if (!ov) {
    // Modal was dismissed somehow — fall back to a toast.
    showToast(`${ok}/${total} importiert${failures.length ? `, ${failures.length} fehlgeschlagen` : ''}`);
    return;
  }
  const failHtml = failures.length ? `
    <div style="margin-top:12px">
      <div style="font-size:12px;color:var(--text-300);margin-bottom:6px"><strong>${failures.length}</strong> nicht importiert:</div>
      <div style="max-height:240px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px">
        ${failures.map(f => `
          <div style="padding:7px 10px;border-bottom:1px solid var(--border-100);font-size:12px">
            <div style="font-family:var(--font-mono);color:var(--text-200);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(f.name)}">${esc(f.name)}</div>
            <div style="color:#d33;margin-top:2px">${esc(f.reason)}</div>
          </div>`).join('')}
      </div>
    </div>` : '';
  ov.innerHTML = `<div class="sched-modal" style="max-width:520px">
    <h2 style="display:flex;align-items:center;gap:8px">
      ${aborted
        ? `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#e0a000" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>Import abgebrochen`
        : failures.length
          ? `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#e0a000" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>Import abgeschlossen (mit Hinweisen)`
          : `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#2a9d3a" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>Import abgeschlossen`}
    </h2>
    <div style="font-size:13px;color:var(--text-300);margin:8px 0">
      <strong>${ok}</strong> von <strong>${total}</strong> Datei(en) importiert${aborted ? ' (abgebrochen)' : ''}.
      ${failHtml}
    </div>
    <div class="sched-modal-actions">
      <button class="sched-create-btn" onclick="document.getElementById('folder-import-progress')?.remove()">Schließen</button>
    </div>
  </div>`;
}

// Folder-picker handler: a <input type="file" webkitdirectory> yields a flat
// FileList where each File carries .webkitRelativePath ("Folder/sub/x.pdf").
function pickProjectFolder(input) {
  const files = input && input.files ? Array.from(input.files) : [];
  input.value = '';
  if (!files.length) return;
  const entries = files
    .filter(f => !f.name.startsWith('.'))   // skip dotfiles/OS cruft
    .map(f => ({ file: f, relPath: f.webkitRelativePath || f.name }));
  // The browser ALREADY showed its native "upload N files?" dialog for the
  // webkitdirectory picker, so our own confirm here would be a redundant second
  // dialog — go straight to the progress dialog. (Drag-drop has NO native
  // prompt, so that path keeps confirmProjectFolderImport.)
  addProjectFolderFiles(entries);
}

async function deleteProjectFile(agentId, projectName, sourceHash) {
  if (!sourceHash) return;
  try {
    await API.del(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/docs/${sourceHash}`);
    showToast('Datei entfernt');
    loadProjectFiles(agentId, projectName);
  } catch(e) {
    showToast('Datei konnte nicht entfernt werden');
  }
}

async function loadProjectChats(agentId, projectName) {
  const container = document.getElementById('project-detail-chats');
  const filter = state._projectChatsFilter || 'active';
  try {
    const data = await API.get(`/v1/sessions?agent=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}&status=${filter}`);
    const sessions = data.sessions || [];
    container.innerHTML = '';
    if (!sessions.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding:18px 8px;color:var(--text-400);font-size:13px;text-align:center';
      empty.textContent = filter === 'archived' ? 'Keine archivierten Chats' : 'Noch keine Chats';
      container.appendChild(empty);
      return;
    }
    for (const s of sessions) {
      const item = document.createElement('div');
      const pcStreaming = state.streamingSessions?.has(s.id);
      item.className = 'project-chat-item' + (pcStreaming ? ' streaming' : '');
      const ago = s.last_active ? formatTimeAgo(new Date(s.last_active * 1000)) : '';
      const isArchived = filter === 'archived' || s.status === 'archived';
      // Stash status flag for the menu (avoids a second fetch).
      item.dataset.archived = isArchived ? '1' : '0';
      const pcTitle = s.title || s.summary || 'Unbenannt';
      const pcTip = s.summary ? ` title="${esc(s.summary)}"` : '';
      item.innerHTML = `
        <span class="project-chat-item-title"${pcTip}>${esc(pcTitle)}</span>
        ${pcStreaming ? '<span class="sb-stream-pill" title="Antwort wird gerade erstellt">läuft</span>' : ''}
        <span class="project-chat-item-meta">${ago ? 'Letzte Nachricht ' + ago : ''}</span>
        <span class="project-chat-item-actions">
          <button style="color:var(--text-400);padding:4px" onclick="event.stopPropagation(); showProjectChatMenu(event, '${esc(s.id)}', ${isArchived ? 'true' : 'false'})" title="Weitere Optionen">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
          </button>
        </span>
      `;
      // Pass the session's own agent_id (sessions can be re-homed; never
      // assume state.activeAgentId is correct). Without this, openSession's
      // selectAgent(undefined) corrupts the active chat context and the
      // session loads against the wrong chat object — looks like "reload
      // doesn't work" / "continue chat is broken".
      const sAgent = s.agent_id || s.agent || agentId;
      item.onclick = () => {
        openSession(s.id, sAgent);
        // openSession already navigates to chat; no second navigateTo needed.
      };
      container.appendChild(item);
    }
  } catch(e) {
    console.error('Failed to load project chats:', e);
  }
}

// Switch active/archived/schedules tab and reload the matching list. Chats use
// status-filtered loadProjectChats; the schedules tab swaps to a separate list
// + bulk row (the chat-list container and its bulk buttons hide).
function setProjectChatsFilter(filter) {
  state._projectChatsFilter = filter;
  document.querySelectorAll('.project-chats-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.pcfilter === filter);
  });
  const isSched = filter === 'schedules';
  const isStudio = filter === 'studio';
  const isResearch = filter === 'research';
  const isChats = !isSched && !isStudio && !isResearch;
  const chatsEl = document.getElementById('project-detail-chats');
  const schedEl = document.getElementById('project-detail-schedules');
  const studioEl = document.getElementById('project-detail-studio');
  const researchEl = document.getElementById('project-detail-research');
  const chatBulk = document.getElementById('project-chats-bulk');
  const schedBulk = document.getElementById('project-sched-bulk');
  const studioBulk = document.getElementById('project-studio-bulk');
  if (chatsEl) chatsEl.style.display = isChats ? '' : 'none';
  if (schedEl) schedEl.style.display = isSched ? '' : 'none';
  if (studioEl) studioEl.style.display = isStudio ? '' : 'none';
  if (researchEl) researchEl.style.display = isResearch ? '' : 'none';
  if (chatBulk) chatBulk.style.display = isChats ? '' : 'none';
  if (schedBulk) schedBulk.style.display = isSched ? '' : 'none';
  if (studioBulk) studioBulk.style.display = isStudio ? '' : 'none';
  // Stop the per-tab polls whenever we leave their tab.
  if (!isStudio && typeof stopStudioPoll === 'function') stopStudioPoll();
  if (!isResearch && typeof stopResearchPoll === 'function') stopResearchPoll();
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  if (isSched) loadProjectSchedules(agentId, projectName);
  else if (isStudio) loadProjectStudio(agentId, projectName);
  else if (isResearch) loadProjectResearch(agentId, projectName);
  else loadProjectChats(agentId, projectName);
}

async function archiveAllProjectChats() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const filter = state._projectChatsFilter || 'active';
  // On the Active tab → archive all active in this project.
  // On the Archived tab → unarchive all archived in this project.
  if (filter === 'archived') {
    if (!await showConfirm(`Alle archivierten Chats in „${projectName}“ wiederherstellen?`)) return;
    try {
      await API.manageSession({ action: 'unarchive_all', agent: agentId, project: projectName });
      showToast('Alle Chats wiederhergestellt');
      loadProjectChats(agentId, projectName);
      loadAgentSessions(agentId);
    } catch(e) { showToast('Wiederherstellen aller Chats fehlgeschlagen', true); }
    return;
  }
  if (!await showConfirm(`Alle aktiven Chats in „${projectName}“ archivieren?`)) return;
  try {
    await API.manageSession({ action: 'archive_all', agent: agentId, project: projectName });
    showToast('Alle Chats archiviert');
    loadProjectChats(agentId, projectName);
    loadAgentSessions(agentId);
  } catch(e) { showToast('Archivieren aller Chats fehlgeschlagen', true); }
}

async function deleteAllProjectChats() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const filter = state._projectChatsFilter || 'active';
  const archivedOnly = filter === 'archived';
  const label = archivedOnly ? 'archivierte Chats' : 'ALLE Chats';
  if (!await showConfirmDanger(`${label} in „${projectName}“ endgültig löschen? Dies kann nicht rückgängig gemacht werden.`, 'Chats löschen', 'Löschen')) return;
  try {
    const r = await API.manageSession({
      action: 'delete_all', agent: agentId, project: projectName, archived_only: archivedOnly,
    });
    showToast(`${r.count || 'Alle'} Chats gelöscht`);
    // If the active chat was inside this project, reset the view.
    if (state.activeChat?.sessionId && state.currentProject === projectName) {
      newChat();
    }
    loadProjectChats(agentId, projectName);
    loadAgentSessions(agentId);
  } catch(e) { showToast('Löschen aller Chats fehlgeschlagen', true); }
}

// Per-session unarchive helper used by the project chat menu and (eventually)
// the global chats list. Uses manageSession action 'unarchive'.
async function unarchiveSession(sessionId) {
  try {
    await API.manageSession({ action: 'unarchive', session_id: sessionId });
    showToast('Chat wiederhergestellt');
    const agentId = state._projectDetailAgent;
    const projectName = state._projectDetailName;
    if (state.currentView === 'project-detail' && agentId && projectName) {
      loadProjectChats(agentId, projectName);
    }
    loadAgentSessions(state.activeAgentId);
  } catch(e) { showToast('Wiederherstellen fehlgeschlagen', true); }
}

// ─── Project-scoped scheduled tasks ────────────────────────────────────────
// Mirrors the project-chats list pattern. Tasks are bound to the project via
// project_id (resolved server-side from the current project's id); the list is
// fetched with the ?project= filter so only this project's tasks show. The
// editor/history reuse the generic /v1/schedule endpoint via API.manageSchedule.

async function loadProjectSchedules(agentId, projectName) {
  const container = document.getElementById('project-detail-schedules');
  if (!container) return;
  container.innerHTML = '<div style="padding:18px 8px;color:var(--text-400);font-size:13px;text-align:center">Lädt…</div>';
  try {
    const data = await API.get(`/v1/schedule?agent=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}`);
    const schedules = data.schedules || [];
    container.innerHTML = '';
    if (!schedules.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding:18px 8px;color:var(--text-400);font-size:13px;text-align:center';
      empty.textContent = 'Noch keine geplanten Aufgaben in diesem Projekt';
      container.appendChild(empty);
      return;
    }
    for (const s of schedules) {
      const item = document.createElement('div');
      item.className = 'project-chat-item';
      const enabled = !!s.enabled;
      const running = !!s.is_running;
      const next = s.next_run ? formatTimeAgo(new Date(s.next_run + (s.next_run.endsWith('Z') ? '' : 'Z'))) : '';
      const statusTxt = running ? 'läuft gerade' : (enabled ? (next ? 'nächster Lauf ' + next : 'aktiv') : 'pausiert');
      const nm = esc(s.name || '');
      item.innerHTML = `
        <span class="project-chat-item-title">${nm}</span>
        <span class="project-chat-item-meta">${esc(s.schedule || '')} · ${statusTxt}</span>
        <span class="project-chat-item-actions">
          <button style="color:var(--text-400);padding:4px" onclick="event.stopPropagation(); projectSchedMenu(event, '${nm}', ${enabled}, ${running})" title="Weitere Optionen">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
          </button>
        </span>
      `;
      // Stash the row so the edit modal can prefill without a refetch.
      item._sched = s;
      item.onclick = () => projectSchedShowForm(s);
      container.appendChild(item);
    }
  } catch (e) {
    container.innerHTML = `<div style="padding:18px 8px;color:var(--error);font-size:13px">${esc(e.message || e)}</div>`;
  }
}

// Build the per-task context menu, mirroring showProjectChatMenu's .ctx-menu
// pattern. name is single-quote-escaped for the inline onclick handlers.
function projectSchedMenu(event, name, enabled, running) {
  event.stopPropagation();
  document.querySelectorAll('.ctx-menu').forEach(m => m.remove());
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = `position:fixed;z-index:10000;background:var(--bg-000);border:1px solid var(--border-200);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:4px;min-width:150px`;
  const itemStyle = `padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)`;
  const dangerStyle = itemStyle + ';color:var(--error)';
  const nm = (name || '').replace(/'/g, "\\'");
  const row = (style, fn, label) =>
    `<div style="${style}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="${fn}; this.closest('.ctx-menu').remove()">${esc(label)}</div>`;
  let html = '';
  if (running) {
    html += row(itemStyle, `projectSchedCancel('${nm}')`, 'Abbrechen');
  } else {
    html += row(itemStyle, `projectSchedToggle('${nm}', ${!enabled})`, enabled ? 'Pausieren' : 'Fortsetzen');
    html += row(itemStyle, `projectSchedRunNow('${nm}')`, 'Jetzt ausführen');
  }
  html += row(itemStyle, `projectSchedHistory('${nm}')`, 'Verlauf');
  html += row(dangerStyle, `projectSchedDelete('${nm}')`, 'Löschen');
  menu.innerHTML = html;
  document.body.appendChild(menu);
  const r = event.target.closest('button')?.getBoundingClientRect() || { left: event.clientX, bottom: event.clientY };
  menu.style.left = Math.min(r.left, window.innerWidth - 150) + 'px';
  menu.style.top = r.bottom + 4 + 'px';
  setTimeout(() => document.addEventListener('click', function _cl() {
    menu.remove();
    document.removeEventListener('click', _cl);
  }), 10);
}

// Build the create/edit modal as a clone of the real schedule modal
// (settings_schedule.js) so it carries the SAME fields — Prompt+KI-Verfeinern,
// Modell, Häufigkeits-Builder, Timeout, Denkstufe, Caveman — minus
// Arbeitsverzeichnis / Tool-Profil / Anhänge (not needed for project tasks)
// and minus the Agent picker (fixed to the project's agent). Reuses the same
// `sched-new-*` element IDs so _schedRefineControls / _schedRefreshThinking /
// _schedFreqChanged work unchanged. window._projectSchedEdit carries the
// original name when editing (null = create).
function projectSchedShowForm(sched) {
  const editing = sched && sched.name;
  window._projectSchedEdit = editing ? sched.name : null;

  // Model options — chat-capable only (mirrors showCreateScheduledModal).
  const modelOpts = (state.models || []).filter(m => modelHasCapability(m, 'chat')).map(m =>
    `<option value="${esc(m)}" ${editing && m === sched.model ? 'selected' : ''}>${esc(m)}</option>`
  ).join('');
  const cavemanCur = editing ? Number(sched.caveman_chat || 0) : 0;
  const cavemanOpts = [[0, 'Off'], [1, 'Lite'], [2, 'Full'], [3, 'Ultra']]
    .map(([v, lbl]) => `<option value="${v}" ${cavemanCur === v ? 'selected' : ''}>${lbl}</option>`).join('');

  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal">
    <h2>${editing ? 'Bearbeiten: ' + esc(sched.name) : 'Neue geplante Aufgabe'}</h2>
    <div class="sched-form-group">
      <label>Name</label>
      <input id="sched-new-name" placeholder="z. B. Wochenbericht" value="${editing ? esc(sched.name || '') : ''}">
    </div>
    <div class="sched-form-group">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <label style="margin:0">Prompt</label>
        ${_schedRefineControls('sched-new-prompt')}
      </div>
      <textarea id="sched-new-prompt" placeholder="Was soll im Kontext dieses Projekts ausgeführt werden?">${editing ? esc(sched.task || '') : ''}</textarea>
    </div>
    <div class="sched-form-group">
      <label>Modell (optional)</label>
      <select id="sched-new-model" onchange="_schedRefreshThinking('sched-new-model','sched-new-thinking')"><option value="">Standard</option>${modelOpts}</select>
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Häufigkeit</label>
        <select id="sched-new-freq" onchange="_schedFreqChanged()">
          <option value="every 1h">Stündlich</option>
          <option value="daily 09:00" ${editing ? '' : 'selected'}>Täglich</option>
          <option value="weekly mon 09:00">Wöchentlich</option>
          <option value="custom" ${editing ? 'selected' : ''}>Benutzerdefiniert</option>
        </select>
      </div>
      <div class="sched-form-group">
        <label>Uhrzeit</label>
        <input id="sched-new-time" type="time" value="09:00">
      </div>
    </div>
    <div class="sched-form-group" id="sched-custom-row" style="display:${editing ? '' : 'none'}">
      <label>Benutzerdefinierter Zeitplan</label>
      <input id="sched-new-custom" placeholder="z. B. every 30m, daily 14:00, weekly fri 17:00" value="${editing ? esc(sched.schedule || '') : ''}" style="font-family:var(--font-mono)">
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Timeout (Sekunden)</label>
        <input id="sched-new-timeout" type="number" value="${editing ? (sched.timeout || 300) : 300}" min="30" max="3600">
      </div>
      <div class="sched-form-group">
        <label>Denkstufe <span style="color:var(--text-400);font-weight:normal;font-size:11px">(Reasoning-Aufwand)</span></label>
        <select id="sched-new-thinking"></select>
      </div>
      <div class="sched-form-group">
        <label>Caveman-Modus <span style="color:var(--text-400);font-weight:normal;font-size:11px">(Antwortkomprimierung)</span></label>
        <select id="sched-new-caveman">${cavemanOpts}</select>
      </div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="projectSchedSave()">${editing ? 'Speichern' : 'Erstellen'}</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  // Populate the thinking dropdown for the bound model (preselect the task's
  // level when editing, else generic set with "Inherit").
  _schedRefreshThinking('sched-new-model', 'sched-new-thinking', editing ? (sched.thinking_level || '') : '');
}

async function projectSchedSave() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  const projectId = (state._projectDetail || {}).id || '';
  if (!agentId || !projectName) return;
  const origName = window._projectSchedEdit || '';
  const name = document.getElementById('sched-new-name')?.value?.trim();
  const task = document.getElementById('sched-new-prompt')?.value?.trim();
  const model = document.getElementById('sched-new-model')?.value || '';
  const freq = document.getElementById('sched-new-freq')?.value;
  const timeVal = document.getElementById('sched-new-time')?.value || '09:00';
  const customSched = document.getElementById('sched-new-custom')?.value?.trim();
  const timeout = parseInt(document.getElementById('sched-new-timeout')?.value) || 300;
  const thinking_level = document.getElementById('sched-new-thinking')?.value || '';
  const caveman_chat = parseInt(document.getElementById('sched-new-caveman')?.value) || 0;
  if (!name || !task) { showToast('Name und Prompt sind erforderlich', true); return; }

  // Resolve the frequency builder to a schedule string (same logic as
  // _createScheduledTask).
  let schedule;
  if (freq === 'custom') {
    schedule = customSched;
    if (!schedule) { showToast('Benutzerdefinierter Zeitplan ist erforderlich', true); return; }
  } else if (freq.startsWith('every')) {
    schedule = freq;
  } else if (freq.startsWith('daily')) {
    schedule = `daily ${timeVal}`;
  } else if (freq.startsWith('weekly')) {
    schedule = `weekly ${freq.split(' ')[1] || 'mon'} ${timeVal}`;
  }

  try {
    let res;
    if (origName) {
      // Edit: model='' clears back to Default; project binding stays as-is.
      const payload = {
        action: 'edit', name: origName, task, schedule, timeout,
        model, thinking_level, caveman_chat,
      };
      if (name !== origName) payload.new_name = name;
      res = await API.manageSchedule(payload);
    } else {
      res = await API.manageSchedule({
        action: 'add', name, task, schedule, agent: agentId, timeout,
        model: model || undefined, thinking_level, caveman_chat,
        project_id: projectId,
      });
    }
    if (res && res.error) { showToast(res.error, true); return; }
    showToast(origName ? 'Aufgabe gespeichert' : 'Aufgabe erstellt');
    document.querySelector('.sched-modal-overlay')?.remove();
    loadProjectSchedules(agentId, projectName);
  } catch (e) { showToast('Fehlgeschlagen: ' + (e.message || e), true); }
}

async function projectSchedToggle(name, enable) {
  try {
    const res = await API.manageSchedule({ action: enable ? 'resume' : 'pause', name });
    if (res && res.error) { showToast(res.error, true); return; }
    showToast(enable ? 'Fortgesetzt' : 'Pausiert');
    loadProjectSchedules(state._projectDetailAgent, state._projectDetailName);
  } catch (e) { showToast('Fehlgeschlagen: ' + (e.message || e), true); }
}

async function projectSchedRunNow(name) {
  try {
    const res = await API.manageSchedule({ action: 'run_now', name });
    if (res && res.error) { showToast(res.error, true); return; }
    showToast('Ausführung gestartet');
    setTimeout(() => loadProjectSchedules(state._projectDetailAgent, state._projectDetailName), 800);
  } catch (e) { showToast('Fehlgeschlagen: ' + (e.message || e), true); }
}

async function projectSchedCancel(name) {
  try {
    await API.cancelScheduledTask(name);
    showToast('Wird abgebrochen…');
    setTimeout(() => loadProjectSchedules(state._projectDetailAgent, state._projectDetailName), 1000);
  } catch (e) { showToast('Fehlgeschlagen: ' + (e.message || e), true); }
}

async function projectSchedDelete(name) {
  if (!await showConfirmDanger(`Geplante Aufgabe „${name}" löschen?`, 'Aufgabe löschen', 'Löschen')) return;
  try {
    const res = await API.manageSchedule({ action: 'delete', name });
    if (res && res.error) { showToast(res.error, true); return; }
    showToast('Gelöscht');
    loadProjectSchedules(state._projectDetailAgent, state._projectDetailName);
  } catch (e) { showToast('Fehlgeschlagen: ' + (e.message || e), true); }
}

async function projectSchedHistory(name) {
  const modal = document.getElementById('project-sched-history-modal');
  const title = document.getElementById('project-sched-history-title');
  const body = document.getElementById('project-sched-history-body');
  if (!modal || !body) return;
  title.textContent = `Verlauf: ${name}`;
  body.innerHTML = '<div style="color:var(--text-400)">Lädt…</div>';
  modal.style.display = 'flex';
  try {
    const res = await API.manageSchedule({ action: 'history', name, limit: 20 });
    const history = res.history || [];
    if (!history.length) { body.innerHTML = '<div style="color:var(--text-400)">Kein Ausführungsverlauf</div>'; return; }
    let html = '<div style="color:var(--text-400);font-size:11px;margin-bottom:6px">Lauf anklicken für Ergebnis, Artefakte und Tool-Verlauf.</div>';
    for (const h of history) {
      const ok = h.status === 'success' || h.status === 'completed';
      const started = h.started_at ? new Date(h.started_at + 'Z').toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
      const costStr = (h.cost != null && h.cost > 0) ? '$' + h.cost.toFixed(h.cost < 0.01 ? 5 : 4) : '';
      // Each run opens the shared deep-detail view (result text, tool
      // timeline, artifacts) — _schedViewRunDetail is self-contained. We wrap
      // it so the detail overlay (z-index 1000) is lifted above THIS project
      // history modal (z-index 9000), otherwise it'd render behind it.
      html += `<div onclick="projectSchedRunDetail(${Number(h.id)})" style="display:flex;align-items:center;gap:8px;padding:8px 6px;border-bottom:1px solid var(--border-100);cursor:pointer;border-radius:6px" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''">
        <span style="width:6px;height:6px;border-radius:50%;background:${ok ? 'var(--success)' : 'var(--error)'};flex-shrink:0"></span>
        <span style="flex:1;color:var(--text-200)">${started}</span>
        ${costStr ? `<span style="color:var(--text-400);font-family:var(--font-mono);font-size:11px">${costStr}</span>` : ''}
        <span style="color:var(--text-400)">${esc(h.status || '')}</span>
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--text-400);flex-shrink:0"><polyline points="9 18 15 12 9 6"/></svg>
      </div>`;
    }
    body.innerHTML = html;
  } catch (e) { body.innerHTML = `<div style="color:var(--error)">${esc(e.message || e)}</div>`; }
}

function projectSchedCloseHistory() {
  const modal = document.getElementById('project-sched-history-modal');
  if (modal) modal.style.display = 'none';
}

// Open the shared run-detail view from inside the project history modal. The
// detail overlay defaults to z-index 1000 (< the 9000 history modal), so lift
// the freshly-created overlay above it.
function projectSchedRunDetail(runId) {
  _schedViewRunDetail(runId);
  const overlays = document.querySelectorAll('.sched-modal-overlay');
  const top = overlays[overlays.length - 1];
  if (top) top.style.zIndex = '9100';
}

function editProjectInstructions() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  const project = state._projectDetail;
  if (!agentId || !projectName) return;

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.maxWidth = '1200px';
  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Projekt-Anweisungen</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <p style="font-size:13px;color:var(--text-400);margin-bottom:12px">
        Hinweise, an die sich das Projekt in jeder Antwort halten soll –
        zum Beispiel Tonfall, Sprache oder Formatvorgaben. Markdown wird
        unterstützt; über den Reiter „Vorschau“ siehst du, wie es aussieht.
        <br><br>
        Leer lassen, wenn keine zusätzlichen Hinweise nötig sind. Ob das
        Projekt streng auf Basis der Unterlagen antwortet, stellst du separat
        über den <em>Recherchemodus</em> ein.
      </p>

      <div id="instr-ai-box" style="border:1px solid var(--border-100);border-radius:8px;padding:12px;margin-bottom:14px;background:var(--bg-200)">
        <div style="font-weight:600;font-size:13px;margin-bottom:4px">✨ Anweisung mit KI erstellen</div>
        <p style="font-size:12px;color:var(--text-400);margin:0 0 10px">
          Beschreibe kurz Ziel und gewünschtes Ergebnis des Projekts. Die KI liest
          die beigelegten Referenzdateien sowie die eingelesenen Quellen und
          verfasst daraus eine vollständige Projektanweisung. Das Ergebnis wird
          unten zum Prüfen eingefügt – gespeichert wird erst mit „Speichern“.
        </p>
        <textarea id="instr-ai-prompt" rows="3"
          style="width:100%;box-sizing:border-box;font-size:13px;padding:8px;border:1px solid var(--border-100);border-radius:6px;resize:vertical"
          placeholder="z. B. Worum geht es in diesem Projekt, was soll dabei herauskommen (Ergebnisformat/Aufbau), worauf soll es sich stützen (beigelegte Dateien, eingelesene Quellen, Webrecherche)?"></textarea>
        <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
          <button class="btn-primary" type="button" id="instr-ai-generate-btn" onclick="generateProjectInstructionsAI()">Generieren</button>
          <button class="btn-secondary" type="button" id="instr-ai-cancel-btn" onclick="cancelProjectInstructionsAI()" style="display:none">Abbrechen</button>
        </div>
        <div id="instr-ai-progress" style="display:none;margin-top:10px;font-size:12px"></div>
      </div>

      <div class="instr-tabs" role="tablist">
        <button class="instr-tab active" id="instr-tab-edit" role="tab" onclick="switchInstrTab('edit')">Bearbeiten</button>
        <button class="instr-tab" id="instr-tab-preview" role="tab" onclick="switchInstrTab('preview')">Vorschau</button>
      </div>
      <textarea class="project-instructions-editor" id="project-instructions-textarea"
        placeholder="z. B. Du bist ein hilfsbereiter Assistent für unser Marketing-Team. Antworte stets in einem professionellen Ton..."
      >${esc(project?.instructions || '')}</textarea>
      <div class="instr-preview-pane" id="project-instructions-preview" style="display:none"></div>

      <div style="margin-top:18px;border-top:1px solid var(--border-100);padding-top:14px">
        <div style="font-weight:600;font-size:13px;margin-bottom:4px">Begleitdateien</div>
        <p style="font-size:12px;color:var(--text-400);margin:0 0 10px">
          Erläuternde Dateien, die die Anweisungen ergänzen (z. B. ein Styleguide,
          eine Vorlage, eine Begriffsliste). Sie werden <strong>nicht</strong> in
          die Projekt-Erinnerung aufgenommen — der Assistent bekommt ihren
          Speicherort genannt und liest sie bei Bedarf eigenständig
          (wie einen Chat-Anhang). Beliebige Dateitypen, max. 25 MB pro Datei.
        </p>
        <div id="project-instr-files-list" style="margin-bottom:10px"></div>
        <input type="file" id="project-instr-file-input" style="display:none"
          onchange="uploadProjectInstructionFile(this)">
        <button class="btn-secondary" type="button" id="project-instr-upload-btn"
          onclick="document.getElementById('project-instr-file-input').click()">
          Datei hochladen
        </button>
        <div id="project-instr-upload-progress" style="display:none;margin-top:10px">
          <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-400);margin-bottom:4px">
            <span id="project-instr-upload-label">Wird hochgeladen …</span>
            <span id="project-instr-upload-pct"></span>
          </div>
          <div style="height:6px;border-radius:3px;background:var(--bg-100);overflow:hidden">
            <div id="project-instr-upload-fill" style="height:100%;width:0%;background:var(--accent,#3b82f6);transition:width .15s ease"></div>
          </div>
        </div>
      </div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Abbrechen</button>
      <button class="btn-primary" onclick="saveProjectInstructions()">Speichern</button>
    </div>
  `;
  overlay.appendChild(content);
  document.body.appendChild(overlay);
  renderProjectInstructionFiles(project?.instruction_files || []);
  setTimeout(() => document.getElementById('project-instructions-textarea')?.focus(), 100);
}

function renderProjectInstructionFiles(files) {
  // Both surfaces show the same list, so repaint the project tree here (the one
  // point every add/remove passes through) — otherwise a modal upload leaves the
  // tree node behind it stale. Guarded: the tree only exists on the project page.
  if (document.getElementById('project-source-tree')) renderProjectSourceTree();
  const el = document.getElementById('project-instr-files-list');
  if (!el) return;
  const list = files || [];
  if (!list.length) {
    el.innerHTML = '<div style="font-size:12px;color:var(--text-400)">Noch keine Begleitdateien.</div>';
    return;
  }
  el.innerHTML = list.map(f => {
    const fn = (f && f.filename) || '';
    const kb = f && f.size ? ` · ${Math.max(1, Math.round(f.size / 1024))} KB` : '';
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 8px;border:1px solid var(--border-100);border-radius:6px;margin-bottom:6px">
      <span style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📎 ${esc(fn)}<span style="color:var(--text-400)">${esc(kb)}</span></span>
      <button class="btn-icon" title="Entfernen" onclick="deleteProjectInstructionFile('${esc(fn).replace(/'/g, "\\'")}')">&times;</button>
    </div>`;
  }).join('');
}

function _instrUploadProgress(pct, label) {
  const box = document.getElementById('project-instr-upload-progress');
  const fill = document.getElementById('project-instr-upload-fill');
  const pctEl = document.getElementById('project-instr-upload-pct');
  const labelEl = document.getElementById('project-instr-upload-label');
  if (!box) return;
  if (pct === false) { box.style.display = 'none'; return; }  // hide
  box.style.display = '';
  if (labelEl && label) labelEl.textContent = label;
  // pct null = sent, server processing → indeterminate (full bar, no number).
  if (pct === null) {
    if (fill) fill.style.width = '100%';
    if (pctEl) pctEl.textContent = '';
    if (labelEl && !label) labelEl.textContent = 'Wird verarbeitet …';
  } else {
    if (fill) fill.style.width = pct + '%';
    if (pctEl) pctEl.textContent = pct + '%';
  }
}

async function uploadProjectInstructionFile(input) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  const file = input && input.files && input.files[0];
  if (!agentId || !projectName || !file) return;
  if (file.size > 25 * 1024 * 1024) {
    alert('Datei zu groß (max. 25 MB).');
    input.value = '';
    return;
  }
  const btn = document.getElementById('project-instr-upload-btn');
  if (btn) btn.disabled = true;
  _instrUploadProgress(0, 'Wird hochgeladen …');
  try {
    const res = await API.uploadProjectInstructionFile(
      agentId, projectName, file, (pct) => _instrUploadProgress(pct));
    if (res && res.error) { alert('Upload fehlgeschlagen: ' + res.error); return; }
    if (state._projectDetail) state._projectDetail.instruction_files = res.instruction_files || [];
    renderProjectInstructionFiles((res && res.instruction_files) || []);
  } catch (e) {
    alert('Upload fehlgeschlagen: ' + ((e && e.message) || e));
  } finally {
    _instrUploadProgress(false);  // hide
    if (btn) btn.disabled = false;
    input.value = '';
  }
}

async function deleteProjectInstructionFile(filename) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName || !filename) return;
  try {
    const res = await API.deleteProjectInstructionFile(agentId, projectName, filename);
    if (res && res.error) { alert('Löschen fehlgeschlagen: ' + res.error); return; }
    if (state._projectDetail) state._projectDetail.instruction_files = res.instruction_files || [];
    renderProjectInstructionFiles((res && res.instruction_files) || []);
  } catch (e) {
    alert('Löschen fehlgeschlagen: ' + e);
  }
}

function switchInstrTab(mode) {
  const editTab = document.getElementById('instr-tab-edit');
  const previewTab = document.getElementById('instr-tab-preview');
  const textarea = document.getElementById('project-instructions-textarea');
  const preview = document.getElementById('project-instructions-preview');
  if (!editTab || !previewTab || !textarea || !preview) return;
  if (mode === 'preview') {
    editTab.classList.remove('active');
    previewTab.classList.add('active');
    const raw = textarea.value || '';
    if (raw.trim()) {
      preview.innerHTML = renderMarkdown(raw);
    } else {
      preview.innerHTML = '<span class="instr-preview-empty">Noch nichts zur Vorschau — schreiben Sie im Reiter „Bearbeiten“ einige Anweisungen.</span>';
    }
    textarea.style.display = 'none';
    preview.style.display = 'block';
  } else {
    previewTab.classList.remove('active');
    editTab.classList.add('active');
    preview.style.display = 'none';
    textarea.style.display = '';
    setTimeout(() => textarea.focus(), 0);
  }
}

async function toggleProjectResearchMode(enabled) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.updateProject(agentId, projectName, { research_mode: !!enabled });
    if (state._projectDetail) state._projectDetail.research_mode = !!enabled;
    // Invalidate the composer button's cached project default so the
    // next refresh in any open chat reads fresh state.
    if (state._projectResearchModeCache) {
      state._projectResearchModeCache[agentId + '::' + projectName] = !!enabled;
    }
    if (typeof refreshResearchModeButton === 'function') refreshResearchModeButton();
    showToast(enabled ? 'Recherchemodus für dieses Projekt aktiviert'
                       : 'Recherchemodus für dieses Projekt deaktiviert');
  } catch (e) {
    showToast('Projektmodus konnte nicht geändert werden', true);
    // Revert checkbox to the last known state on failure.
    const cb = document.getElementById('project-research-mode-checkbox');
    if (cb) cb.checked = !enabled;
  }
}

// Toggle the project-level web-search lockout. When on, chats + scheduled
// tasks of this project cannot use web_fetch/searxng/exa — the model must
// work from the project memory (model-independent enforcement).
async function toggleProjectDisableWeb(enabled) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.updateProject(agentId, projectName, { disable_web_search: !!enabled });
    if (state._projectDetail) state._projectDetail.disable_web_search = !!enabled;
    showToast(enabled ? 'Websuche für dieses Projekt unterbunden'
                       : 'Websuche für dieses Projekt wieder erlaubt');
  } catch (e) {
    showToast('Einstellung konnte nicht geändert werden', true);
    const cb = document.getElementById('project-disable-web-checkbox');
    if (cb) cb.checked = !enabled;
  }
}

// ── Code Mode ──────────────────────────────────────────────────────────────
// (Code Mode is fixed at creation — there is no toggle. The detail panel only
// exposes the working-dir picker + init for an existing code project.)

// Filesystem-browser picker for the code-mode working directory. Reuses the
// same /v1/files/tree backend as the input-folder picker.
function pickProjectWorkingDir() {
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Arbeitsverzeichnis wählen</h2>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">Wähle den Ordner, in dem dieses Projekt arbeitet (meist dein Code-Verzeichnis). Das Modell liest, bearbeitet und erzeugt Dateien direkt darin.</div>
    <div id="pwd-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pwd-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="_pwdSelect()">Dieses Verzeichnis verwenden</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  _pwdLoad('');
}

async function _pwdLoad(path) {
  const crumbs = document.getElementById('pwd-crumbs');
  const list = document.getElementById('pwd-list');
  if (!crumbs || !list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-400);text-align:center">Wird geladen …</div>';
  try {
    const data = await API.get(`/v1/files/tree?path=${encodeURIComponent(path)}&depth=0`);
    if (data.error) { list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(data.error)}</div>`; return; }
    const cur = data.path || path || '/';
    window._pwdPath = cur;
    crumbs.textContent = cur;
    const dirs = (data.tree || []).filter(n => n.type === 'dir');
    const parent = (cur && cur !== '/') ? cur.replace(/\/[^\/]+\/?$/, '') || '/' : null;
    let html = '';
    if (parent !== null) {
      html += `<div onclick="_pwdLoad('${esc(parent)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-300)">↑ ..</div>`;
    }
    if (!dirs.length) {
      html += '<div style="padding:14px;color:var(--text-400);text-align:center;font-size:12px">(keine Unterordner)</div>';
    } else {
      for (const d of dirs) {
        html += `<div onclick="_pwdLoad('${esc(d.path)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-200)"><span style="color:var(--text-400)">📁</span> ${esc(d.name)}</div>`;
      }
    }
    list.innerHTML = html;
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(e.message)}</div>`;
  }
}

async function _pwdSelect() {
  const path = window._pwdPath || '';
  if (!path) { showToast('Kein Verzeichnis ausgewählt', true); return; }
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    const res = await API.updateProject(agentId, projectName, { working_dir: path });
    if (res && res.error) { showToast(res.error, true); return; }
    if (state._projectDetail) state._projectDetail.working_dir = path;
    const wd = document.getElementById('project-codemode-wd');
    if (wd) wd.textContent = path;
    document.querySelector('.sched-modal-overlay')?.remove();
    if (typeof refreshCodeWorkingTree === 'function') refreshCodeWorkingTree();
    showToast('Arbeitsverzeichnis gesetzt');
  } catch (e) {
    showToast('Verzeichnis konnte nicht gesetzt werden: ' + (e.message || e), true);
  }
}

// Working-dir picker used DURING create (stores into a window flag instead of
// PUTting to a project that doesn't exist yet). Reuses the same _pwdLoad browser.
function _createProjectPickWd() {
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10002';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Arbeitsverzeichnis wählen</h2>
    <div id="pwd-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pwd-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="_createProjectWdSelect()">Dieses Verzeichnis verwenden</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  _pwdLoad('');
}

function _createProjectWdSelect() {
  const path = window._pwdPath || '';
  if (!path) { showToast('Kein Verzeichnis ausgewählt', true); return; }
  window._createProjectWorkingDir = path;
  const el = document.getElementById('create-project-wd');
  if (el) el.textContent = path;
  // Close only the picker overlay (the topmost), not the create modal.
  document.querySelectorAll('.sched-modal-overlay').forEach(o => {
    if (o.querySelector('#pwd-list')) o.remove();
  });
}

async function runProjectInit() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  if (!state._projectDetail?.working_dir) {
    showToast('Erst ein Arbeitsverzeichnis wählen', true);
    return;
  }
  try {
    const res = await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/init`, {});
    if (res && res.error) { showToast(res.error, true); return; }
    showToast('init gestartet — BRAIN.md wird generiert');
    _renderInitStatus({ state: 'generating', elapsed: 0 });
    startCodeModePoll();   // immediately switch to fast (running) cadence
  } catch (e) {
    showToast('init fehlgeschlagen: ' + (e.message || e), true);
  }
}

async function cancelProjectInit() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/init-cancel`, {});
    showToast('init wird abgebrochen…');
    _pollCodeModeOnce();   // refresh status promptly
  } catch (e) {
    showToast('Abbruch fehlgeschlagen: ' + (e.message || e), true);
  }
}

// Render the init progress line. `st` = {state, elapsed, error}. While
// generating, show a spinner + elapsed time + a Cancel button; on terminal
// states show the outcome.
function _renderInitStatus(st) {
  const el = document.getElementById('project-codemode-init-status');
  if (!el) return;
  const s = (st && st.state) || 'idle';
  const secs = Math.round(st && st.elapsed || 0);
  const t = secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`;
  if (s === 'generating') {
    el.innerHTML =
      `<span class="codeinit-spin" aria-hidden="true"></span>` +
      `<span>BRAIN.md wird generiert… <span style="color:var(--text-400)">(${t})</span></span>` +
      `<button class="btn-secondary codeinit-cancel" type="button" onclick="cancelProjectInit()">Abbrechen</button>`;
    el.style.display = 'flex';
  } else if (s === 'done') {
    el.innerHTML = `✓ BRAIN.md generiert <span style="color:var(--text-400)">(${t})</span>`;
    el.style.display = 'block';
  } else if (s === 'cancelled') {
    el.innerHTML = '⃠ init abgebrochen.';
    el.style.display = 'block';
  } else if (s === 'error') {
    el.innerHTML = `⚠ init fehlgeschlagen: ${esc((st && st.error) || 'Unbekannter Fehler')}`;
    el.style.display = 'block';
  } else {
    el.innerHTML = '';
    el.style.display = 'none';
  }
}

// ── Code-mode polling: drives BOTH the init progress display AND auto-refresh
// of the working-dir file tree. One interval; cadence speeds up while an init
// is running (status changes fast) and slows when idle (only watching files).
async function _pollCodeModeOnce() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName || !state._projectDetail?.code_mode) return false;
  let running = false;
  try {
    const st = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/init-status`);
    // Guard: the detail panel may have switched projects mid-request.
    if (state._projectDetailName !== projectName) return false;
    running = st && st.state === 'generating';
    // Don't clobber a fresh terminal banner with 'idle' once a run has ended.
    if (st && st.state !== 'idle') _renderInitStatus(st);
    state._codeInitLastState = st && st.state;
  } catch (e) { /* transient — keep polling */ }
  // Auto-refresh the file tree (silent: only re-renders on a real change).
  try { await refreshCodeWorkingTree({ silent: true }); } catch (e) {}
  return running;
}

function startCodeModePoll() {
  stopCodeModePoll();
  if (!state._projectDetail?.code_mode) return;
  const tick = async () => {
    const running = await _pollCodeModeOnce();
    // Running init → poll fast (progress + files churn). Idle → slow watch.
    const next = running ? 1500 : 4000;
    state._codeModePollTimer = setTimeout(tick, next);
  };
  tick();
}

function stopCodeModePoll() {
  if (state._codeModePollTimer) {
    clearTimeout(state._codeModePollTimer);
    state._codeModePollTimer = null;
  }
}

// Per-project KG extraction method override (''=inherit global, 'llm', 'rules').
// Selecting 'rules' forces the generic profile (rule extraction can't do the
// normative vocabulary), so the profile select is greyed to match.
async function toggleProjectKgMethod(method) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.updateProject(agentId, projectName, { kg_method: method });
    if (state._projectDetail) state._projectDetail.kg_method = method;
    const pSel = document.getElementById('project-kg-profile');
    if (pSel) {
      const rules = method === 'rules';
      pSel.disabled = rules;
      pSel.style.opacity = rules ? '0.5' : '';
    }
    showToast('KG-Methode für dieses Projekt gespeichert');
  } catch (e) {
    showToast('KG-Einstellung konnte nicht geändert werden', true);
  }
}

// Per-project KG profile override (''=inherit global, 'normative', 'generic').
async function toggleProjectKgProfile(profile) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.updateProject(agentId, projectName, { kg_profile: profile });
    if (state._projectDetail) state._projectDetail.kg_profile = profile;
    showToast('KG-Profil für dieses Projekt gespeichert');
  } catch (e) {
    showToast('KG-Einstellung konnte nicht geändert werden', true);
  }
}

// ── Project-level web URLs ──────────────────────────────────
// A project-wide curated source set: each URL is fetched fresh by the
// project-sync daemon (hash-gated — re-mined only when content changes) and
// mined into the project's MemPalace wing + KG, just like input folders.
// Reached via memory/KG retrieval — NOT injected per turn (that's the
// separate per-chat Websuche basket).
/* ── Projekt-Datenquellen (DATA_SOURCES_V2 Phase 4, E2/E9) ──
   project.json → data_sources = [{name, tables:[]}]; tables leer = alle.
   Checkbox-Quelle ist /v1/data-sources/available (policy-gefiltert; leer →
   Sektion bleibt versteckt). Jede Änderung speichert sofort via
   API.updateProject — 1–2 Klicks: Quelle anhaken (+ optional Tabellen). */
async function renderProjectDataSources(project) {
  const sec = document.getElementById('project-datasources-section');
  const body = document.getElementById('project-datasources-body');
  if (!sec || !body) return;
  let avail = state._dsAvailCache;
  if (!avail) {
    try {
      avail = state._dsAvailCache = await API.get('/v1/data-sources/available');
    } catch (e) { sec.style.display = 'none'; return; }
  }
  const sources = avail?.sources || [];
  if (!sources.length) { sec.style.display = 'none'; return; }
  sec.style.display = '';
  const chosen = {};
  ((project || state._projectDetail || {}).data_sources || []).forEach(e => {
    if (e && e.name) chosen[e.name] = e.tables || [];
  });
  const tblCache = state._dsTablesCache || {};
  body.innerHTML = sources.map(s => {
    const on = Object.prototype.hasOwnProperty.call(chosen, s.name);
    const tabs = chosen[s.name] || [];
    const modeBadge = s.access_mode === 'rw'
      ? '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg-200);color:var(--error)">read/write</span>'
      : '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg-200);color:var(--text-400)">read-only</span>';
    let detail = '';
    if (on) {
      // REST sources restrict PATHS instead of tables (same scope shape).
      const resLabel = s.type === 'rest' ? 'Pfade' : 'Tabellen';
      const expanded = state._pdsExpanded === s.name;
      const known = tblCache[s.name];
      let picker = '';
      if (expanded && Array.isArray(known)) {
        picker = (known.length ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">` +
          known.map(t => {
            const sel = tabs.includes(t);
            return `<label style="font-size:11px;padding:2px 8px;border-radius:10px;cursor:pointer;border:1px solid var(--border-100);background:${sel ? 'var(--accent)' : 'var(--bg-200)'};color:${sel ? '#fff' : 'var(--text-200)'}">` +
              `<input type="checkbox" style="display:none" ${sel ? 'checked' : ''} onchange="pdsToggleTable('${esc(s.name)}','${esc(t)}')">${esc(t)}</label>`;
          }).join('') + `</div>` : `<div style="margin-top:6px;color:var(--text-400)">Keine Vorschläge hinterlegt.</div>`) +
          `<div style="margin-top:4px"><button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="pdsClearTables('${esc(s.name)}')">Einschränkung aufheben (alle ${resLabel})</button></div>`;
      } else if (expanded) {
        picker = `<div style="margin-top:6px;color:var(--text-400)">Lade ${resLabel}…</div>`;
      }
      detail = `<div style="margin:6px 0 2px 26px;font-size:12px;color:var(--text-300)">
        ${tabs.length ? `Beschränkt auf: <b>${tabs.map(esc).join(', ')}</b>` : `alle ${resLabel}`}
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px;margin-left:8px" onclick="pdsPickTables('${esc(s.name)}')">${expanded ? 'Zuklappen' : `${resLabel} wählen…`}</button>
        ${picker}</div>`;
    }
    return `<div style="padding:6px 0;border-bottom:1px solid var(--border-100)">
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
        <input type="checkbox" ${on ? 'checked' : ''} onchange="pdsToggleSource('${esc(s.name)}', this.checked)">
        <strong>${esc(s.name)}</strong>
        <span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg-200);color:var(--text-400)">${esc(s.type)}</span>
        ${modeBadge}
        ${s.guide_set ? '<span title="Steckbrief vorhanden — Nutzungswissen wird dem Modell automatisch mitgegeben" style="display:inline-flex;color:var(--text-400)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg></span>' : ''}
      </label>${detail}</div>`;
  }).join('');
}

function pdsToggleSource(name, on) {
  const pd = state._projectDetail;
  if (!pd) return;
  const list = (pd.data_sources || []).filter(e => e && e.name !== name);
  if (on) list.push({ name, tables: [] });
  pd.data_sources = list;
  if (!on && state._pdsExpanded === name) state._pdsExpanded = null;
  _pdsSave();
}

async function pdsPickTables(name) {
  if (state._pdsExpanded === name) {
    state._pdsExpanded = null;
    renderProjectDataSources(state._projectDetail);
    return;
  }
  state._pdsExpanded = name;
  renderProjectDataSources(state._projectDetail);
  state._dsTablesCache = state._dsTablesCache || {};
  if (!Array.isArray(state._dsTablesCache[name])) {
    try {
      const r = await API.get('/v1/data-sources/' + encodeURIComponent(name) + '/tables');
      if (r?.error) { showToast(r.error, true); state._pdsExpanded = null; }
      else state._dsTablesCache[name] = r?.tables || [];
    } catch (e) {
      showToast('Tabellenliste nicht verfügbar', true);
      state._pdsExpanded = null;
    }
    renderProjectDataSources(state._projectDetail);
  }
}

function pdsToggleTable(name, table) {
  const entry = (state._projectDetail?.data_sources || []).find(e => e && e.name === name);
  if (!entry) return;
  const tabs = entry.tables || [];
  entry.tables = tabs.includes(table) ? tabs.filter(t => t !== table) : tabs.concat([table]);
  _pdsSave();
}

function pdsClearTables(name) {
  const entry = (state._projectDetail?.data_sources || []).find(e => e && e.name === name);
  if (!entry) return;
  entry.tables = [];
  _pdsSave();
}

async function _pdsSave() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    const r = await API.updateProject(agentId, projectName, {
      data_sources: state._projectDetail?.data_sources || [],
    });
    if (r?.error) showToast(r.error, true);
  } catch (e) {
    showToast('Datenquellen konnten nicht gespeichert werden', true);
  }
  renderProjectDataSources(state._projectDetail);
}

// Stored in project.json → web_urls as [{url,title}].
// Shim: web URLs now render inside the unified source tree. Keep
// state._projectDetail.web_urls authoritative, then re-render the tree (the
// tree reads it). Expand/collapse state survives via localStorage.
function renderProjectWebUrls(urls) {
  if (state._projectDetail && Array.isArray(urls)) state._projectDetail.web_urls = urls;
  if (typeof renderProjectSourceTree === 'function' && document.getElementById('project-source-tree')) {
    renderProjectSourceTree();
  }
}

async function _saveProjectWebUrls(urls) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.updateProject(agentId, projectName, { web_urls: urls });
    if (state._projectDetail) state._projectDetail.web_urls = urls;
    renderProjectWebUrls(urls);
  } catch (e) {
    showToast('Web-Adressen konnten nicht gespeichert werden', true);
  }
}

function addProjectWebUrl() {
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:520px">
    <h2 style="display:flex;align-items:center;gap:8px">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
      Web-Adresse hinzufügen
    </h2>
    <div style="font-size:13px;color:var(--text-300);line-height:1.5;margin:8px 0 14px">
      Die Seite wird frisch abgerufen und als Wissensquelle in dieses Projekt eingelesen.
    </div>
    <label style="display:block;font-size:12px;color:var(--text-400);margin-bottom:4px">Adresse (URL)</label>
    <input id="pwu-url" type="text" placeholder="https://example.com/seite" autocomplete="off"
      style="width:100%;padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100);color:var(--text-100);font-size:13px;margin-bottom:12px"
      onkeydown="if(event.key==='Enter'){event.preventDefault();_pwuConfirm();}">
    <label style="display:block;font-size:12px;color:var(--text-400);margin-bottom:4px">Bezeichnung (optional)</label>
    <input id="pwu-title" type="text" placeholder="leer lassen für die Domain" autocomplete="off"
      style="width:100%;padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100);color:var(--text-100);font-size:13px"
      onkeydown="if(event.key==='Enter'){event.preventDefault();_pwuConfirm();}">
    <div id="pwu-err" style="color:#d33;font-size:12px;margin-top:8px;display:none"></div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Abbrechen</button>
      <button class="sched-create-btn" onclick="_pwuConfirm()">Hinzufügen</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('pwu-url')?.focus(), 50);
}

function _pwuConfirm() {
  const urlEl = document.getElementById('pwu-url');
  const titleEl = document.getElementById('pwu-title');
  const errEl = document.getElementById('pwu-err');
  if (!urlEl) return;
  let url = (urlEl.value || '').trim();
  if (!url) { if (errEl) { errEl.textContent = 'Bitte eine Adresse eingeben.'; errEl.style.display = 'block'; } return; }
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  const title = (titleEl?.value || '').trim();
  const urls = (state._projectDetail?.web_urls || []).slice();
  if (urls.some(u => u.url === url)) {
    if (errEl) { errEl.textContent = 'Diese Adresse ist bereits hinterlegt.'; errEl.style.display = 'block'; }
    return;
  }
  urls.push({ url, title });
  document.querySelector('.sched-modal-overlay')?.remove();
  _saveProjectWebUrls(urls);
}

function removeProjectWebUrl(idx) {
  const urls = (state._projectDetail?.web_urls || []).slice();
  if (idx < 0 || idx >= urls.length) return;
  urls.splice(idx, 1);
  _saveProjectWebUrls(urls);
}

// ── Discover linked documents (Option B — propose, don't auto-import) ────────
// Scans the project's configured HTML web_urls for SAME-HOST document links
// (PDF/DOCX/XLSX/…) and shows them in a modal. The user ticks which to add;
// approved links are appended to web_urls via the existing save path. Nothing
// is imported automatically. Deliberately NOT a recursive crawler (depth-1,
// same host, documents only — matches the closed-corpus design).
async function discoverProjectWebLinks() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const urls = state._projectDetail?.web_urls || [];
  if (!urls.length) { showToast('Erst eine Web-Adresse hinzufügen'); return; }
  showToast('Seiten werden nach verlinkten Dokumenten durchsucht…');
  let res;
  try {
    res = await API.discoverProjectWebLinks(agentId, projectName);
  } catch (e) {
    showToast('Linksuche fehlgeschlagen: ' + (e.message || e), true);
    return;
  }
  const proposed = (res.proposed || []);
  state._weblinkProposed = proposed;
  _renderWebLinkModal(proposed, res.scanned || 0);
}

function _renderWebLinkModal(proposed, scanned) {
  document.querySelector('.modal-overlay.weblink-modal')?.remove();
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay weblink-modal';
  // Pre-check everything not already in the project.
  const rows = proposed.map((s, i) => {
    const dimmed = s.in_project;
    return `
      <label style="display:flex;align-items:flex-start;gap:8px;padding:8px 4px;border-bottom:1px solid var(--border-100);${dimmed ? 'opacity:0.5' : ''}">
        <input type="checkbox" class="weblink-pick" data-idx="${i}" ${dimmed ? 'disabled' : 'checked'} style="margin-top:3px">
        <span style="flex:1;min-width:0">
          <span style="font-weight:600;font-size:13px;word-break:break-word">${esc(s.title || s.url)}</span>
          <span style="display:inline-block;margin-left:6px;font-size:10px;text-transform:uppercase;color:var(--text-400);border:1px solid var(--border-200);border-radius:4px;padding:0 4px">${esc((s.ext || '').replace('.', ''))}</span>
          ${dimmed ? '<span style="font-size:11px;color:var(--text-400);margin-left:6px">bereits im Projekt</span>' : ''}
          <span style="display:block;font-size:11px;color:var(--text-400);word-break:break-all">${esc(s.url)}</span>
        </span>
      </label>`;
  }).join('');
  const body = proposed.length
    ? `<div style="font-size:12px;color:var(--text-400);margin-bottom:8px">${proposed.length} verlinkte Dokumente auf ${scanned} Seite(n) gefunden. Wähle aus, was als Wissensquelle aufgenommen werden soll — nichts wird automatisch importiert.</div>
       <div style="max-height:50vh;overflow:auto">${rows}</div>
       <div style="margin-top:12px;display:flex;gap:8px;align-items:center">
         <button class="btn-primary" style="padding:6px 16px;font-size:13px" onclick="importSelectedWebLinks()">Ausgewählte importieren →</button>
         <button class="btn-secondary" style="padding:6px 12px;font-size:13px" onclick="this.closest('.modal-overlay').remove()">Abbrechen</button>
       </div>`
    : `<div style="font-size:13px;color:var(--text-400);padding:8px 0">Keine verlinkten Dokumente auf den ${scanned} durchsuchten Seite(n) gefunden. Nur Links zu Dateien (PDF, DOCX, XLSX …) auf derselben Domain werden vorgeschlagen.</div>
       <div style="margin-top:10px"><button class="btn-secondary" style="padding:6px 12px;font-size:13px" onclick="this.closest('.modal-overlay').remove()">Schließen</button></div>`;
  overlay.innerHTML = `<div class="modal-content" style="max-width:680px;width:90vw;max-height:82vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">Verlinkte Dokumente</span>
      <button class="modal-close" style="margin-left:auto" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div style="padding:14px 18px;overflow:auto">${body}</div>
  </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
}

async function importSelectedWebLinks() {
  const proposed = state._weblinkProposed || [];
  const picks = Array.from(document.querySelectorAll('.weblink-pick:checked'))
    .map(cb => proposed[parseInt(cb.dataset.idx, 10)]).filter(Boolean);
  if (!picks.length) { showToast('Nichts ausgewählt'); return; }
  const existing = (state._projectDetail?.web_urls || []).slice();
  const have = new Set(existing.map(u => (u.url || '').toLowerCase().replace(/\/$/, '')));
  const toAdd = picks
    .filter(s => !have.has((s.url || '').toLowerCase().replace(/\/$/, '')))
    .map(s => ({ url: s.url, title: s.title || '' }));
  if (!toAdd.length) { showToast('Alle ausgewählten sind bereits im Projekt'); return; }
  const merged = existing.concat(toAdd);
  await _saveProjectWebUrls(merged);
  document.querySelector('.modal-overlay.weblink-modal')?.remove();
  showToast(`${toAdd.length} Dokument(e) importiert — werden jetzt ins Gedächtnis gemined`);
}

// ── Project-settings help toggle ───────────────────────────
// One "Hilfe" button reveals/hides the per-section help blocks. Preference
// persisted so it stays on/off across project switches and reloads.
function applyProjectHelpState() {
  const on = localStorage.getItem('projectHelpVisible') === '1';
  const panel = document.getElementById('project-detail-panel');
  const btn = document.getElementById('project-help-toggle-btn');
  if (panel) panel.classList.toggle('help-on', on);
  if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
}

function toggleProjectHelp() {
  const on = localStorage.getItem('projectHelpVisible') === '1';
  localStorage.setItem('projectHelpVisible', on ? '0' : '1');
  applyProjectHelpState();
}

async function saveProjectInstructions() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  const textarea = document.getElementById('project-instructions-textarea');
  if (!agentId || !projectName || !textarea) return;

  const instructions = textarea.value.trim();
  try {
    await API.updateProject(agentId, projectName, { instructions });
    showToast('Anweisungen gespeichert');
    document.querySelector('.modal-overlay')?.remove();
    // Instructions live in the source tree now — update state + re-render it.
    if (state._projectDetail) state._projectDetail.instructions = instructions;
    if (typeof renderProjectSourceTree === 'function') renderProjectSourceTree();
  } catch(e) {
    showToast('Anweisungen konnten nicht gespeichert werden');
  }
}

// ─── AI-generation of project instructions (agentic, review-before-save) ──────
// state._instrGen = { id, poll, agentId, projectName } while a run is active.
function _instrAiSetRunning(running) {
  const genBtn = document.getElementById('instr-ai-generate-btn');
  const cancelBtn = document.getElementById('instr-ai-cancel-btn');
  const promptEl = document.getElementById('instr-ai-prompt');
  if (genBtn) { genBtn.disabled = running; genBtn.textContent = running ? 'Generiert …' : 'Generieren'; }
  if (cancelBtn) cancelBtn.style.display = running ? '' : 'none';
  if (promptEl) promptEl.disabled = running;
}

function _instrAiRenderProgress(data) {
  const box = document.getElementById('instr-ai-progress');
  if (!box) return;
  box.style.display = '';
  const steps = (data && data.steps) || [];
  const status = (data && data.status) || '';
  const ICON = { phase: '▸', tool: '⚙', info: 'ℹ', error: '✕' };
  let html = steps.map(s => {
    const col = s.kind === 'error' ? 'var(--danger,#dc2626)'
      : s.kind === 'tool' ? 'var(--text-300)' : 'var(--text-200)';
    return `<div style="color:${col};padding:1px 0">${esc(ICON[s.kind] || '·')} ${esc(s.text || '')}</div>`;
  }).join('');
  if (status === 'generating' || !status) {
    html += `<div style="color:var(--text-400);padding:2px 0">⏳ Läuft …</div>`;
  } else if (status === 'error') {
    html += `<div style="color:var(--danger,#dc2626);font-weight:600;padding:2px 0">Fehler: ${esc(data.error || 'unbekannt')}</div>`;
  } else if (status === 'cancelled') {
    html += `<div style="color:var(--text-400);padding:2px 0">Abgebrochen.</div>`;
  } else if (status === 'ready') {
    html += `<div style="color:var(--success);font-weight:600;padding:2px 0">✓ Fertig — Ergebnis unten eingefügt.</div>`;
  }
  box.innerHTML = html;
  box.scrollTop = box.scrollHeight;
}

async function generateProjectInstructionsAI() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  const promptEl = document.getElementById('instr-ai-prompt');
  if (!agentId || !projectName || !promptEl) return;
  const prompt = promptEl.value.trim();
  if (state._instrGen && state._instrGen.poll) return;  // already running
  _instrAiSetRunning(true);
  _instrAiRenderProgress({ status: 'generating', steps: [{ kind: 'phase', text: 'Wird gestartet …' }] });
  let genId = '';
  try {
    const r = await API.generateProjectInstructions(agentId, projectName, prompt);
    if (r && r.error) throw new Error(r.error);
    genId = r && r.gen_id;
  } catch (e) {
    _instrAiSetRunning(false);
    _instrAiRenderProgress({ status: 'error', error: (e && e.message) || 'Start fehlgeschlagen', steps: [] });
    return;
  }
  if (!genId) { _instrAiSetRunning(false); return; }
  state._instrGen = { id: genId, agentId, projectName, poll: null };

  const tick = async () => {
    // Stale guard: modal closed or a different run started → stop polling.
    if (!state._instrGen || state._instrGen.id !== genId
        || !document.getElementById('instr-ai-progress')) {
      if (state._instrGen && state._instrGen.poll) clearInterval(state._instrGen.poll);
      if (state._instrGen && state._instrGen.id === genId) state._instrGen = null;
      return;
    }
    let data;
    try {
      data = await API.getInstructionGen(agentId, projectName, genId);
    } catch (e) { return; }  // transient — keep polling
    _instrAiRenderProgress(data);
    if (['ready', 'error', 'cancelled'].includes(data.status)) {
      clearInterval(state._instrGen.poll);
      state._instrGen = null;
      _instrAiSetRunning(false);
      if (data.status === 'ready' && data.result_md) {
        const ta = document.getElementById('project-instructions-textarea');
        if (ta) {
          ta.value = data.result_md;
          // If preview is open, refresh it.
          if (typeof switchInstrTab === 'function'
              && document.getElementById('instr-tab-preview')?.classList.contains('active')) {
            switchInstrTab('preview');
          }
        }
        showToast('Anweisung erstellt — bitte prüfen und speichern');
      }
    }
  };
  state._instrGen.poll = setInterval(tick, 1200);
  tick();
}

async function cancelProjectInstructionsAI() {
  if (!state._instrGen) return;
  const { id, agentId, projectName } = state._instrGen;
  try {
    await API.cancelInstructionGen(agentId, projectName, id);
  } catch (e) { /* best-effort */ }
}

// ─── Project member-picker helpers ───────────────────────────────
async function _ensureUserDirectory() {
  if (state._userDirectory) return state._userDirectory;
  try {
    const r = await API.lookupUsers();
    state._userDirectory = (r.users || []);
  } catch(e) {
    state._userDirectory = [];
  }
  return state._userDirectory;
}

function _userLabel(u) {
  return u.display_name || u.username || u.id;
}

function _renderProjectMemberChips(containerId, ids, listId) {
  const wrap = document.getElementById(containerId);
  if (!wrap) return;
  const dir = state._userDirectory || [];
  const byId = {};
  for (const u of dir) byId[u.id] = u;
  if (!ids.length) {
    wrap.innerHTML = '<span style="font-size:12px;color:var(--text-400);font-style:italic">Niemand hinzugefügt</span>';
    return;
  }
  wrap.innerHTML = ids.map(uid => {
    const u = byId[uid] || {id: uid, display_name: uid};
    return `<span class="project-member-chip" data-uid="${esc(uid)}" style="display:inline-flex;align-items:center;gap:6px;padding:4px 8px;background:var(--bg-200);border:1px solid var(--border-200);border-radius:12px;font-size:12px;margin:2px 4px 2px 0">
      ${esc(_userLabel(u))}
      <button onclick="_pmRemove('${esc(listId)}','${esc(uid)}','${esc(containerId)}')" style="background:none;border:none;cursor:pointer;color:var(--text-400);padding:0;line-height:1" title="Entfernen">×</button>
    </span>`;
  }).join('');
}

function _pmRemove(listId, uid, containerId) {
  const arr = window[listId] || [];
  const idx = arr.indexOf(uid);
  if (idx >= 0) arr.splice(idx, 1);
  _renderProjectMemberChips(containerId, arr, listId);
}

function _pmAdd(selectId, listId, containerId) {
  const sel = document.getElementById(selectId);
  const uid = sel?.value;
  if (!uid) return;
  const arr = window[listId] = window[listId] || [];
  if (!arr.includes(uid)) arr.push(uid);
  sel.value = '';
  _renderProjectMemberChips(containerId, arr, listId);
}

function _renderMemberPicker(opts) {
  // opts: {label, helpText, listId, containerId, selectId, excludeIds}
  const dir = state._userDirectory || [];
  const exclude = new Set(opts.excludeIds || []);
  const options = dir.filter(u => !exclude.has(u.id))
    .map(u => `<option value="${esc(u.id)}">${esc(_userLabel(u))}</option>`).join('');
  return `
    <div class="project-modal-field">
      <label class="project-modal-label">${esc(opts.label)}</label>
      ${opts.helpText ? `<div style="font-size:11px;color:var(--text-400);margin-bottom:6px">${opts.helpText}</div>` : ''}
      <div id="${opts.containerId}" style="min-height:24px;margin-bottom:6px"></div>
      <div style="display:flex;gap:6px">
        <select class="project-modal-input" id="${opts.selectId}" style="flex:1">
          <option value="">Benutzer auswählen …</option>
          ${options}
        </select>
        <button class="btn-secondary" onclick="_pmAdd('${esc(opts.selectId)}','${esc(opts.listId)}','${esc(opts.containerId)}')">Hinzufügen</button>
      </div>
    </div>`;
}

async function showCreateProjectModal(codeMode) {
  codeMode = !!codeMode;
  window._createProjectCodeMode = codeMode;
  window._createProjectWorkingDir = '';
  const agentId = state.activeAgentId || 'main';
  const authed = !!(state.authUser && state.authEnabled);
  if (authed) await _ensureUserDirectory();
  // Reset shared lists used by the chip picker
  window._projectCreateExtras = [];
  window._projectCreateExcluded = [];

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.maxWidth = '520px';
  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">${codeMode ? 'Neues Code-Projekt' : 'Neues Projekt'}</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      ${codeMode ? `<div class="project-panel-help" style="margin-bottom:12px">
        <strong>Code-Projekt:</strong> arbeitet direkt in einem Verzeichnis (kein
        Projektgedächtnis/Ingest). Der Modus ist fest und kann später nicht
        geändert werden.</div>` : ''}
      <div class="project-modal-field">
        <label class="project-modal-label">Projektname</label>
        <input class="project-modal-input" id="create-project-name" placeholder="Mein Projekt" autofocus>
      </div>
      ${codeMode ? `<div class="project-modal-field">
        <label class="project-modal-label">Arbeitsverzeichnis</label>
        <div style="display:flex;align-items:center;gap:8px">
          <div id="create-project-wd" style="flex:1;font-family:var(--font-mono);font-size:12px;color:var(--text-300);background:var(--bg-100);border:1px solid var(--border-100);border-radius:6px;padding:7px 10px;word-break:break-all">— (optional, später wählbar)</div>
          <button class="btn-secondary" type="button" onclick="_createProjectPickWd()">Wählen</button>
        </div>
      </div>` : ''}
      <div class="project-modal-field">
        <label class="project-modal-label">Beschreibung (optional)</label>
        <textarea class="project-modal-input" id="create-project-desc" rows="3" style="resize:vertical"
          placeholder="Worum geht es in diesem Projekt?"></textarea>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Agent</label>
        <select class="project-modal-input" id="create-project-agent">
          ${state.agents.map(a => {
            const aid = a.id || a.name;
            return `<option value="${esc(aid)}" ${aid === agentId ? 'selected' : ''}>${esc(a.display_name || aid)}</option>`;
          }).join('')}
        </select>
      </div>
      ${authed ? (() => {
        const isAdmin = state.authUser.role === 'admin';
        const headedTeams = (state.userTeams || []).filter(t => t.head_user_id === state.authUser.id);
        const canTeam = isAdmin || headedTeams.length > 0;
        const teamOptions = isAdmin ? (state.userTeams || []) : headedTeams;
        const visOpts = [];
        visOpts.push(`<option value="user"${!isAdmin && !canTeam ? ' selected' : ''}>Persönlich</option>`);
        if (canTeam) visOpts.push(`<option value="team"${!isAdmin ? ' selected' : ''}>Team</option>`);
        if (isAdmin) visOpts.push('<option value="global" selected>Global (alle)</option>');
        const initialVis = isAdmin ? 'global' : (canTeam ? 'team' : 'user');
        const ownerBlock = isAdmin
          ? `<div class="project-modal-field">
              <label class="project-modal-label">Eigentümer</label>
              <select class="project-modal-input" id="create-project-owner">
                ${(state._userDirectory || []).map(u => `<option value="${esc(u.id)}" ${u.id===state.authUser.id?'selected':''}>${esc(_userLabel(u))}</option>`).join('')}
              </select>
            </div>`
          : `<input type="hidden" id="create-project-owner" value="${esc(state.authUser.id)}">`;
        const visBlock = (visOpts.length === 1)
          ? `<input type="hidden" id="create-project-visibility" value="user">`
          : `<div class="project-modal-field">
              <label class="project-modal-label">Sichtbarkeit</label>
              <select class="project-modal-input" id="create-project-visibility" onchange="_createProjectOnVisChange(this.value)">
                ${visOpts.join('')}
              </select>
            </div>
            <div class="project-modal-field" id="create-project-team-wrap" style="display:${initialVis==='team'?'block':'none'}">
              <label class="project-modal-label">Team</label>
              <select class="project-modal-input" id="create-project-team">
                <option value="">Team auswählen …</option>
                ${teamOptions.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}
              </select>
            </div>`;
        // Members panels (rendered for create; default to whatever initialVis dictates)
        const extrasPicker = _renderMemberPicker({
          label: 'Mitglieder hinzufügen',
          helpText: 'Persönlich: Personen mit Zugriff. Team: Zusätzliche außerhalb des Teams. Global: ignoriert.',
          listId: '_projectCreateExtras',
          containerId: 'create-project-extras-chips',
          selectId: 'create-project-extras-select',
          excludeIds: [state.authUser.id],
        });
        const excludedPicker = _renderMemberPicker({
          label: 'Benutzer ausschließen',
          helpText: 'Nur Global — bestimmte Benutzer von diesem Projekt ausschließen.',
          listId: '_projectCreateExcluded',
          containerId: 'create-project-excluded-chips',
          selectId: 'create-project-excluded-select',
          excludeIds: [state.authUser.id],
        });
        return `
          ${ownerBlock}
          ${visBlock}
          <div id="create-project-extras-wrap" style="display:${initialVis==='global'?'none':'block'}">${extrasPicker}</div>
          <div id="create-project-excluded-wrap" style="display:${initialVis==='global'?'block':'none'}">${excludedPicker}</div>
        `;
      })() : ''}
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Abbrechen</button>
      <button class="btn-primary" onclick="createProject()">Projekt erstellen</button>
    </div>
  `;
  overlay.appendChild(content);
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('create-project-name')?.focus(), 100);

  // Render initial (empty) chips for the member-pickers
  if (document.getElementById('create-project-extras-chips')) {
    _renderProjectMemberChips('create-project-extras-chips', window._projectCreateExtras, '_projectCreateExtras');
  }
  if (document.getElementById('create-project-excluded-chips')) {
    _renderProjectMemberChips('create-project-excluded-chips', window._projectCreateExcluded, '_projectCreateExcluded');
  }

  // Enter key support
  document.getElementById('create-project-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') createProject();
  });
}

function _createProjectOnVisChange(value) {
  const teamWrap = document.getElementById('create-project-team-wrap');
  const extrasWrap = document.getElementById('create-project-extras-wrap');
  const excludedWrap = document.getElementById('create-project-excluded-wrap');
  if (teamWrap) teamWrap.style.display = (value === 'team' ? 'block' : 'none');
  if (extrasWrap) extrasWrap.style.display = (value === 'global' ? 'none' : 'block');
  if (excludedWrap) excludedWrap.style.display = (value === 'global' ? 'block' : 'none');
}

async function createProject() {
  const name = document.getElementById('create-project-name')?.value?.trim();
  const desc = document.getElementById('create-project-desc')?.value?.trim();
  const agentId = document.getElementById('create-project-agent')?.value || 'main';
  const visibility = document.getElementById('create-project-visibility')?.value || '';
  const teamId = document.getElementById('create-project-team')?.value || '';
  const ownerId = document.getElementById('create-project-owner')?.value || '';
  if (!name) { showToast('Projektname ist erforderlich'); return; }
  if (visibility === 'team' && !teamId) { showToast('Wählen Sie ein Team für ein team-gebundenes Projekt'); return; }

  const body = { name, description: desc || '' };
  if (window._createProjectCodeMode) {
    body.code_mode = true;
    if (window._createProjectWorkingDir) body.working_dir = window._createProjectWorkingDir;
  }
  if (visibility) body.visibility = visibility;
  if (teamId) body.owner_team_id = teamId;
  if (ownerId) body.owner_user_id = ownerId;
  if (visibility === 'global') {
    body.excluded_user_ids = (window._projectCreateExcluded || []).slice();
  } else {
    body.extra_member_user_ids = (window._projectCreateExtras || []).slice();
  }

  try {
    const result = await API.createProject(agentId, body);
    if (result.error) { showToast(result.error); return; }
    showToast('Projekt erstellt');
    document.querySelector('.modal-overlay')?.remove();
    openProject(agentId, result.name || name);
  } catch(e) {
    showToast('Projekt konnte nicht erstellt werden');
  }
}

function showProjectListMenu(event, agentId, projectName) {
  event.stopPropagation();
  // Remove any existing context menu
  document.querySelectorAll('.ctx-menu').forEach(m => m.remove());

  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = `position:fixed;z-index:10000;background:var(--bg-000);border:1px solid var(--border-200);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:4px;min-width:140px`;
  menu.innerHTML = `
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="editProjectFromMenu('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Bearbeiten</div>
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="archiveProject('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Archivieren</div>
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--error)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="deleteProject('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Löschen</div>
  `;
  document.body.appendChild(menu);
  // Position near the click
  const r = event.target.closest('button')?.getBoundingClientRect() || { left: event.clientX, bottom: event.clientY };
  menu.style.left = Math.min(r.left, window.innerWidth - 160) + 'px';
  menu.style.top = r.bottom + 4 + 'px';
  // Close on outside click
  setTimeout(() => document.addEventListener('click', function _cl() {
    menu.remove();
    document.removeEventListener('click', _cl);
  }), 10);
}

function showProjectMenu(event) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  showProjectListMenu(event, agentId, projectName);
}

function showProjectChatMenu(event, sessionId, isArchived) {
  event.stopPropagation();
  document.querySelectorAll('.ctx-menu').forEach(m => m.remove());
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = `position:fixed;z-index:10000;background:var(--bg-000);border:1px solid var(--border-200);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:4px;min-width:140px`;
  const itemStyle = `padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)`;
  const dangerStyle = itemStyle + ';color:var(--error)';
  const sid = esc(sessionId);
  const toggleAction = isArchived
    ? `<div style="${itemStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="unarchiveSession('${sid}'); this.closest('.ctx-menu').remove()">Wiederherstellen</div>`
    : `<div style="${itemStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="archiveSession('${sid}'); this.closest('.ctx-menu').remove()">Archivieren</div>`;
  menu.innerHTML = toggleAction + `
    <div style="${dangerStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="deleteSession('${sid}'); this.closest('.ctx-menu').remove()">Löschen</div>
  `;
  document.body.appendChild(menu);
  const r = event.target.closest('button')?.getBoundingClientRect() || { left: event.clientX, bottom: event.clientY };
  menu.style.left = Math.min(r.left, window.innerWidth - 140) + 'px';
  menu.style.top = r.bottom + 4 + 'px';
  setTimeout(() => document.addEventListener('click', function _cl() {
    menu.remove();
    document.removeEventListener('click', _cl);
  }), 10);
}

async function editProjectFromMenu(agentId, projectName) {
  let project = null;
  try { project = await API.getProject(agentId, projectName); } catch(e) {}
  if (!project) { showToast('Projekt konnte nicht geladen werden'); return; }

  const isAdmin = state.authUser && state.authUser.role === 'admin';
  const ownerUid = project.owner_user_id || '';
  const ownerTid = project.owner_team_id || '';
  const isOwner = state.authUser && ownerUid && ownerUid === state.authUser.id;
  const canManage = isAdmin || isOwner;
  if (!canManage) { showToast('Nur der Projekteigentümer kann dieses Projekt bearbeiten'); return; }

  await _ensureUserDirectory();
  // Stash for the save handler so it knows the effective scope when the
  // visibility selector isn't rendered (non-admin owner).
  window._projectEditOriginal = project;
  // Visibility / team re-scoping is admin-only.
  const canRescope = isAdmin;
  // Owner transfer: owner or admin.
  const canTransfer = canManage;
  const allTeams = state.userTeams || [];

  // Seed the chip lists from the project's current state
  window._projectEditExtras = (project.extra_member_user_ids || []).slice();
  window._projectEditExcluded = (project.excluded_user_ids || []).slice();

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.maxWidth = '540px';
  const ownerSelectorBlock = canTransfer
    ? `<div class="project-modal-field">
        <label class="project-modal-label">Eigentümer</label>
        <select class="project-modal-input" id="edit-project-owner">
          ${(state._userDirectory || []).map(u => `<option value="${esc(u.id)}" ${u.id===ownerUid?'selected':''}>${esc(_userLabel(u))}</option>`).join('')}
        </select>
        ${isAdmin ? '' : '<div style="font-size:11px;color:var(--text-400);margin-top:4px">Bei einer Übertragung verlieren Sie Ihre Bearbeitungsrechte.</div>'}
      </div>`
    : `<div class="project-modal-field" style="font-size:12px;color:var(--text-400)">Eigentümer: <strong>${esc(_userLabel((state._userDirectory||[]).find(u=>u.id===ownerUid)||{id:ownerUid,display_name:ownerUid}))}</strong></div>`;

  const scopeBlock = canRescope
    ? `<div class="project-modal-field">
        <label class="project-modal-label">Sichtbarkeit</label>
        <select class="project-modal-input" id="edit-project-visibility" onchange="_editProjectOnVisChange(this.value)">
          <option value="user" ${project.visibility==='user'?'selected':''}>Persönlich</option>
          <option value="team" ${project.visibility==='team'?'selected':''}>Team</option>
          <option value="global" ${project.visibility==='global'?'selected':''}>Global (alle)</option>
        </select>
      </div>
      <div class="project-modal-field" id="edit-project-team-wrap" style="display:${project.visibility==='team'?'block':'none'}">
        <label class="project-modal-label">Team</label>
        <select class="project-modal-input" id="edit-project-team">
          <option value="">Team auswählen …</option>
          ${allTeams.map(t => `<option value="${esc(t.id)}" ${t.id===ownerTid?'selected':''}>${esc(t.name)}</option>`).join('')}
        </select>
      </div>`
    : `<div class="project-modal-field" style="font-size:12px;color:var(--text-400)">
        Sichtbarkeit: <strong>${esc(project.visibility || 'global')}</strong>${ownerTid?` · Team: <strong>${esc((allTeams.find(t=>t.id===ownerTid)||{}).name||ownerTid)}</strong>`:''}
        <div style="margin-top:4px">Nur Administratoren können den Geltungsbereich ändern.</div>
      </div>`;

  // Member pickers
  const extrasPicker = _renderMemberPicker({
    label: 'Mitglieder',
    helpText: project.visibility === 'team'
      ? 'Teammitglieder sind automatisch enthalten. Die Liste unten enthält Zusätzliche außerhalb des Teams.'
      : (project.visibility === 'global' ? '' : 'Benutzer, die zusätzlich zum Eigentümer Zugriff erhalten.'),
    listId: '_projectEditExtras',
    containerId: 'edit-project-extras-chips',
    selectId: 'edit-project-extras-select',
    excludeIds: [ownerUid],
  });
  const excludedPicker = _renderMemberPicker({
    label: 'Ausgeschlossene Benutzer',
    helpText: 'Diese Benutzer von einem globalen Projekt ausschließen.',
    listId: '_projectEditExcluded',
    containerId: 'edit-project-excluded-chips',
    selectId: 'edit-project-excluded-select',
    excludeIds: [ownerUid],
  });

  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Projekt bearbeiten</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <div class="project-modal-field">
        <label class="project-modal-label">Projektname</label>
        <input class="project-modal-input" id="edit-project-display-name" value="${esc(project.name || projectName)}" placeholder="Mein Projekt">
        <div style="font-size:11px;color:var(--text-400);margin-top:4px">Nur Anzeigename. Der Ordnername bleibt <code>${esc(projectName)}</code>.</div>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Beschreibung</label>
        <textarea class="project-modal-input" id="edit-project-desc" rows="3" style="resize:vertical"
          placeholder="Worum geht es in diesem Projekt?">${esc(project.description || '')}</textarea>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Symbol</label>
        <input class="project-modal-input" id="edit-project-icon" value="${esc(project.icon || '📁')}" maxlength="4" style="width:80px;text-align:center;font-size:18px">
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Bild</label>
        <div style="display:flex;align-items:center;gap:10px">
          <div id="edit-project-image-preview" style="width:64px;height:42px;border-radius:6px;border:1px solid var(--border-200);background:var(--bg-300);background-size:cover;background-position:center;flex-shrink:0;${project.image ? `background-image:url('/v1/agents/${esc(agentId)}/projects/${esc(projectName)}/image?v=${Date.now()}')` : ''}"></div>
          <label class="btn-secondary" style="cursor:pointer">
            <span id="edit-project-image-label">${project.image ? 'Ersetzen' : 'Hochladen'}</span>
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden onchange="_editProjectImageUpload(event,'${esc(agentId)}','${esc(projectName)}')">
          </label>
          <button type="button" class="btn-secondary" id="edit-project-image-clear" onclick="_editProjectImageClear('${esc(agentId)}','${esc(projectName)}')" style="display:${project.image?'inline-block':'none'}">Entfernen</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:4px">Wird als Kartenhintergrund in der Projektliste und bei an dieses Projekt angehefteten Favoriten verwendet. Max. 2 MB.</div>
      </div>
      ${ownerSelectorBlock}
      ${scopeBlock}
      <div id="edit-project-extras-wrap" style="display:${project.visibility==='global'?'none':'block'}">${extrasPicker}</div>
      <div id="edit-project-excluded-wrap" style="display:${project.visibility==='global'?'block':'none'}">${excludedPicker}</div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Abbrechen</button>
      <button class="btn-primary" onclick="saveProjectEdit('${esc(agentId)}','${esc(projectName)}')">Speichern</button>
    </div>
  `;
  overlay.appendChild(content);
  document.body.appendChild(overlay);
  // Render the chips after mount
  _renderProjectMemberChips('edit-project-extras-chips', window._projectEditExtras, '_projectEditExtras');
  _renderProjectMemberChips('edit-project-excluded-chips', window._projectEditExcluded, '_projectEditExcluded');
  setTimeout(() => document.getElementById('edit-project-display-name')?.focus(), 100);
}

function _editProjectOnVisChange(value) {
  const teamWrap = document.getElementById('edit-project-team-wrap');
  const extrasWrap = document.getElementById('edit-project-extras-wrap');
  const excludedWrap = document.getElementById('edit-project-excluded-wrap');
  if (teamWrap) teamWrap.style.display = (value === 'team' ? 'block' : 'none');
  if (extrasWrap) extrasWrap.style.display = (value === 'global' ? 'none' : 'block');
  if (excludedWrap) excludedWrap.style.display = (value === 'global' ? 'block' : 'none');
}

async function saveProjectEdit(agentId, projectName) {
  const displayName = document.getElementById('edit-project-display-name')?.value?.trim();
  const desc = document.getElementById('edit-project-desc')?.value;
  const icon = document.getElementById('edit-project-icon')?.value?.trim() || '📁';
  const visEl = document.getElementById('edit-project-visibility');
  const teamEl = document.getElementById('edit-project-team');
  const ownerEl = document.getElementById('edit-project-owner');
  if (!displayName) { showToast('Projektname ist erforderlich'); return; }
  const updates = { name: displayName, description: desc, icon };
  if (ownerEl) updates.owner_user_id = ownerEl.value || '';
  if (visEl) {
    updates.visibility = visEl.value;
    if (visEl.value === 'team') {
      const tid = teamEl?.value || '';
      if (!tid) { showToast('Wählen Sie ein Team'); return; }
      updates.owner_team_id = tid;
    } else {
      updates.owner_team_id = '';
    }
  }
  // Effective scope for choosing which member list to send. Non-admins
  // can't change scope, so fall back to the project's stored visibility.
  const effectiveScope = visEl?.value || (window._projectEditOriginal?.visibility) || '';
  if (effectiveScope === 'global') {
    updates.excluded_user_ids = (window._projectEditExcluded || []).slice();
    updates.extra_member_user_ids = [];
  } else if (effectiveScope) {
    updates.extra_member_user_ids = (window._projectEditExtras || []).slice();
    updates.excluded_user_ids = [];
  }
  try {
    const result = await API.updateProject(agentId, projectName, updates);
    if (result && result.error) { showToast(result.error); return; }
    showToast('Projekt aktualisiert');
    document.querySelector('.modal-overlay')?.remove();
    if (state._projectDetailAgent === agentId && state._projectDetailName === projectName) {
      loadProjectDetail(agentId, projectName);
    }
    loadProjectsList();
  } catch(e) {
    showToast('Projekt konnte nicht aktualisiert werden');
  }
}

async function archiveProject(agentId, projectName) {
  try {
    await API.updateProject(agentId, projectName, { status: 'archived' });
    showToast('Projekt archiviert');
    loadProjectsList();
  } catch(e) { showToast('Projekt konnte nicht archiviert werden'); }
}

async function deleteProject(agentId, projectName) {
  if (!await showConfirmDanger(`Projekt „${projectName}“ löschen? Dies kann nicht rückgängig gemacht werden.`, 'Projekt löschen', 'Löschen')) return;
  try {
    await API.deleteProject(agentId, projectName);
    showToast('Projekt gelöscht');
    loadProjectsList();
  } catch(e) { showToast('Projekt konnte nicht gelöscht werden'); }
}

function toggleProjectStar() {
  // Visual toggle only (no backend persistence for stars yet)
  const btn = document.getElementById('project-detail-star');
  const svg = btn?.querySelector('svg');
  if (svg) {
    const filled = svg.getAttribute('fill') !== 'none';
    svg.setAttribute('fill', filled ? 'none' : 'var(--warning)');
    svg.setAttribute('stroke', filled ? 'currentColor' : 'var(--warning)');
  }
}

/* ═══════════════════════════════════════════════════════════
   ARTIFACT PANEL
   ═══════════════════════════════════════════════════════════ */

/* ═══ Unified Right Panel Functions ═══ */

// Project right-pane resize — same pattern as #right-panel. Idempotent;
// the bound flag prevents double-binding when openProject() reruns.
function initProjectDetailPanelResize() {
  const handle = document.getElementById('project-detail-panel-resize-handle');
  const panel = document.getElementById('project-detail-panel');
  if (!handle || !panel) return;
  // Restore persisted width on every init (cheap, idempotent).
  const saved = localStorage.getItem('project-detail-panel-width');
  if (saved) panel.style.width = saved;
  if (handle._bound) return;
  handle._bound = true;
  handle.addEventListener('mousedown', (e) => {
    const startX = e.clientX;
    const startW = panel.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const onMove = (ev) => {
      // Drag direction: handle is on the LEFT edge of the panel, so moving
      // the cursor LEFT widens the panel. Match #right-panel's math.
      const newW = Math.min(640, Math.max(240, startW + (startX - ev.clientX)));
      panel.style.width = newW + 'px';
    };
    const onUp = () => {
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      localStorage.setItem('project-detail-panel-width', panel.style.width);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  });
}
