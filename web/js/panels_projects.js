// panels_projects.js — project list/detail/files/members/CRUD/instructions. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

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
  list.innerHTML = '<div style="padding:16px;color:var(--text-400)">Loading...</div>';

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
    list.innerHTML = '<div style="padding:16px;color:var(--error)">Failed to load projects</div>';
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
    list.innerHTML = '<div class="project-grid-empty">No projects found</div>';
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
    const glyphHtml = customIcon
      ? esc(customIcon)
      : (window.Favourites?.typeGlyphSvg?.('project', 44) || '');
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
        <button class="project-card-menu" onclick="event.stopPropagation(); showProjectListMenu(event, '${esc(agent)}', '${esc(p.name)}')" title="More options">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
        </button>
      </div>
      <div class="project-card-info">
        <div class="project-card-title"${titleAttr}>${esc(displayName)}</div>
        <div class="project-card-meta">
          <span class="project-card-type">Project</span>
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
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function openProject(agentId, projectName) {
  selectAgent(agentId);
  state.currentProject = projectName;
  state._projectDetailAgent = agentId;
  state._projectDetailName = projectName;
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
  if (file.size > 2 * 1024 * 1024) { await showAlert('Image too large (max 2 MB).'); return; }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(
      `${BASE_URL}/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`,
      { method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth-token') || ''}` },
        body: fd });
    if (!r.ok) { await showAlert(`Upload failed: ${r.status}`); return; }
    const data = await r.json();
    const preview = document.getElementById('edit-project-image-preview');
    const label   = document.getElementById('edit-project-image-label');
    const clear   = document.getElementById('edit-project-image-clear');
    if (preview) preview.style.backgroundImage = `url('/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image?v=${Date.now()}')`;
    if (label) label.textContent = 'Replace';
    if (clear) clear.style.display = 'inline-block';
    if (window._projectEditOriginal) window._projectEditOriginal.image = data.image || '';
    try { await window.Favourites?.reload?.(); } catch(_) {}
    // Refresh the projects list cache so the card reflects the new image
    // when the user closes the modal and returns to the list.
    try { await loadProjectsList(); } catch(_) {}
  } catch (e) {
    await showAlert(`Upload failed: ${e.message || e}`);
  } finally {
    ev.target.value = '';
  }
}

async function _editProjectImageClear(agentId, projectName) {
  if (!await showConfirmDanger('Remove this project image?', 'Remove Image', 'Remove')) return;
  try {
    await API.del(`/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`);
    const preview = document.getElementById('edit-project-image-preview');
    const label   = document.getElementById('edit-project-image-label');
    const clear   = document.getElementById('edit-project-image-clear');
    if (preview) preview.style.backgroundImage = '';
    if (label) label.textContent = 'Upload';
    if (clear) clear.style.display = 'none';
    if (window._projectEditOriginal) window._projectEditOriginal.image = '';
    try { await window.Favourites?.reload?.(); } catch(_) {}
    try { await loadProjectsList(); } catch(_) {}
  } catch (e) {
    await showAlert(`Remove failed: ${e.message || e}`);
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
    if (label) label.textContent = 'Replace image';
  } else {
    banner.style.backgroundImage = '';
    banner.style.background = accent;
    if (glyph) { glyph.style.display = ''; glyph.textContent = icon; }
    if (remove) remove.style.display = 'none';
    if (label) label.textContent = 'Upload image';
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
    await showAlert('Image too large (max 2 MB).');
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
      await showAlert(`Upload failed: ${r.status}`);
      return;
    }
    const data = await r.json();
    project.image = data.image || '';
    paintProjectDetailBanner(agentId, projectName, project);
    // Reload the favourites cache so any favourite of this project picks up
    // the new source_image_url on next render.
    try { await window.Favourites?.reload?.(); } catch(_) {}
  } catch (e) {
    await showAlert(`Upload failed: ${e.message || e}`);
  } finally {
    ev.target.value = '';
  }
}

async function removeProjectImage() {
  const project = state._projectDetail;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!project || !agentId || !projectName) return;
  if (!await showConfirmDanger('Remove this project image?', 'Remove Image', 'Remove')) return;
  try {
    await API.del(`/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`);
    project.image = '';
    paintProjectDetailBanner(agentId, projectName, project);
    try { await window.Favourites?.reload?.(); } catch(_) {}
  } catch (e) {
    await showAlert(`Remove failed: ${e.message || e}`);
  }
}

async function loadProjectDetail(agentId, projectName) {
  // Load project config
  try {
    const project = await API.getProject(agentId, projectName);
    if (!project) {
      showToast('Project not found');
      navigateTo('projects');
      return;
    }
    state._projectDetail = project;

    // Mount the favourite star + share button into the page-header.
    if (project.id) {
      updatePageHeader(project.name || projectName, null, null, {
        item_type: 'project',
        item_id: project.id,
        agent_id: agentId || 'main',
        title: project.name || projectName,
      });
    }

    // Render header
    document.getElementById('project-detail-name').textContent = project.name || projectName;
    const descEl = document.getElementById('project-detail-desc');
    if (project.description) {
      const desc = project.description;
      if (desc.length > 200) {
        descEl.innerHTML = esc(desc.slice(0, 200)) + '... <span class="project-detail-desc-toggle" onclick="toggleProjectDesc()">Show more</span>';
        descEl.dataset.full = desc;
        descEl.dataset.collapsed = 'true';
      } else {
        descEl.textContent = desc;
      }
    } else {
      descEl.innerHTML = '<span style="color:var(--text-400);font-style:italic">No description</span>';
    }

    // Render the Research / Q&A project checkbox state.
    const researchCb = document.getElementById('project-research-mode-checkbox');
    if (researchCb) {
      researchCb.checked = !!project.research_mode;
    }

    // Render instructions panel — markdown rendered, capped height with
    // vertical scroll so long default disciplines don't push attachments
    // + input folders below the fold.
    const instrEl = document.getElementById('project-panel-instructions');
    if (project.instructions) {
      instrEl.innerHTML = `<div class="project-panel-instructions-rendered">${renderMarkdown(project.instructions)}</div>`;
      instrEl.classList.remove('project-panel-placeholder');
    } else {
      instrEl.innerHTML = '<span class="project-panel-placeholder">Noch keine Anweisungen hinterlegt.</span>';
    }

    // Personalise the composer placeholder with the project name. Falls back
    // to the routing slug when the display name is missing.
    const composerInput = document.getElementById('project-input');
    if (composerInput) {
      const displayName = project.name || projectName;
      composerInput.placeholder = `Write your message to ${displayName}`;
    }

    // Load project files
    loadProjectFiles(agentId, projectName);

    // Render project-level web URLs (mined into the project's memory + KG by
    // the sync daemon, like input folders). Read straight off the config.
    renderProjectWebUrls(project.web_urls || []);

    // Apply the persisted "Hilfe" toggle state to this freshly-rendered panel.
    applyProjectHelpState();

    // Load input folders + start polling sync status.
    loadProjectInputFolders(agentId, projectName);
    startProjectSyncPoll(agentId, projectName);

    // Load project conversations
    loadProjectChats(agentId, projectName);
  } catch(e) {
    showToast('Failed to load project');
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
    return '<span class="project-item-pill" data-state="pending" title="Waiting for next sync cycle">pending</span>';
  }
  const stateName = it.state || 'pending';
  const tip = it.error ? it.error
            : (stateName === 'indexed'
                ? `Indexed ${it.drawers_filed != null ? '(' + it.drawers_filed + ' drawers)' : ''}`.trim()
                : (stateName === 'syncing' ? 'Sync in progress…' : 'Pending'));
  let label = stateName;
  if (stateName === 'indexed') label = 'indexed';
  else if (stateName === 'syncing') label = 'syncing…';
  else if (stateName === 'error') label = 'error';
  let kgBadge = '';
  const kgState = it.kg_state || '';
  const triples = it.triples_extracted;
  const kgParseErrors = it.kg_parse_errors || 0;
  if (kgState === 'extracting') {
    kgBadge = ` <span class="project-item-pill" data-kg="extracting" title="Knowledge graph extraction running">KG…</span>`;
  } else if (kgState === 'error') {
    const kgErr = it.kg_last_error || 'KG extraction failed';
    const triplesPart = (typeof triples === 'number' && triples > 0) ? `${triples} relations · ` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="error" title="${esc(kgErr)}">${triplesPart}KG !</span>`;
  } else if (typeof triples === 'number' && triples > 0) {
    // Per-folder pill in the right pane — same renaming as the project chip
    // ("triples" is jargon, "relations" is what's been extracted).
    const warnPart = kgParseErrors > 0 ? ` · ${kgParseErrors} parse err` : '';
    const warnTitle = kgParseErrors > 0 ? ` (${kgParseErrors} chunks returned invalid JSON — non-fatal)` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="${kgParseErrors > 0 ? 'warn' : 'ok'}" title="Knowledge graph relations extracted from this folder${warnTitle}">${triples} relations${warnPart}</span>`;
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
  if (sec < 60) return 'now';
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
    descEl.innerHTML = esc(desc.slice(0, 200)) + '... <span class="project-detail-desc-toggle" onclick="toggleProjectDesc()">Show more</span>';
    descEl.dataset.collapsed = 'true';
  }
}

async function loadProjectFiles(agentId, projectName) {
  const container = document.getElementById('project-panel-files');
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/docs`);
    const docs = data.documents || [];
    if (!docs.length) {
      container.innerHTML = '<span class="project-panel-placeholder">Noch keine Dateien hochgeladen.</span>';
      return;
    }
    container.innerHTML = '';
    for (const doc of docs) {
      const item = document.createElement('div');
      item.className = 'project-file-item';
      const srcHash = doc.source_hash || '';
      item.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <span class="project-file-name" title="${esc(doc.source || doc.name || '')}">${esc(doc.source || doc.name || 'Document')}</span>
        <span data-pif-pill data-pif-kind="attachment" data-pif-id="${esc(srcHash)}">${projectItemPillHtml('attachment', srcHash)}</span>
        <span class="project-file-delete" onclick="deleteProjectFile('${esc(agentId)}','${esc(projectName)}','${esc(srcHash)}'); event.stopPropagation();" title="Remove">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </span>
      `;
      container.appendChild(item);
    }
  } catch(e) {
    container.innerHTML = '<span class="project-panel-placeholder">Dateien konnten nicht geladen werden.</span>';
  }
}

async function uploadProjectFiles(files) {
  if (!files || !files.length) return;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;

  // Auth header is required — the global /v1/* gate rejects anonymous POST.
  // Don't set Content-Type; the browser inserts the multipart boundary.
  const token = localStorage.getItem('auth-token') || '';
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
  for (const file of files) {
    try {
      const formData = new FormData();
      formData.append('file', file);
      showToast(`${file.name} wird hochgeladen …`);
      const resp = await fetch(`${BASE_URL}/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/ingest`, {
        method: 'POST',
        headers,
        body: formData,
      });
      const result = await resp.json().catch(() => ({error: `HTTP ${resp.status}`}));
      if (!resp.ok || result.error) {
        showToast(`Fehler: ${result.error || resp.statusText}`);
      } else {
        showToast(`${file.name} hochgeladen`);
      }
    } catch(e) {
      showToast(`${file.name} konnte nicht hochgeladen werden`);
    }
  }
  loadProjectFiles(agentId, projectName);
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
      empty.textContent = filter === 'archived' ? 'No archived chats' : 'No chats yet';
      container.appendChild(empty);
      return;
    }
    for (const s of sessions) {
      const item = document.createElement('div');
      item.className = 'project-chat-item';
      const ago = s.last_active ? formatTimeAgo(new Date(s.last_active * 1000)) : '';
      const isArchived = filter === 'archived' || s.status === 'archived';
      // Stash status flag for the menu (avoids a second fetch).
      item.dataset.archived = isArchived ? '1' : '0';
      const pcTitle = s.title || s.summary || 'Untitled';
      const pcTip = s.summary ? ` title="${esc(s.summary)}"` : '';
      item.innerHTML = `
        <span class="project-chat-item-title"${pcTip}>${esc(pcTitle)}</span>
        <span class="project-chat-item-meta">${ago ? 'Last message ' + ago : ''}</span>
        <span class="project-chat-item-actions">
          <button style="color:var(--text-400);padding:4px" onclick="event.stopPropagation(); showProjectChatMenu(event, '${esc(s.id)}', ${isArchived ? 'true' : 'false'})" title="More options">
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

// Switch active/archived tab and reload list. Server filters by status, so
// the same loadProjectChats path works for both.
function setProjectChatsFilter(filter) {
  state._projectChatsFilter = filter;
  document.querySelectorAll('.project-chats-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.pcfilter === filter);
  });
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (agentId && projectName) loadProjectChats(agentId, projectName);
}

async function archiveAllProjectChats() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const filter = state._projectChatsFilter || 'active';
  // On the Active tab → archive all active in this project.
  // On the Archived tab → unarchive all archived in this project.
  if (filter === 'archived') {
    if (!await showConfirm(`Unarchive all archived chats in "${projectName}"?`)) return;
    try {
      await API.manageSession({ action: 'unarchive_all', agent: agentId, project: projectName });
      showToast('All chats unarchived');
      loadProjectChats(agentId, projectName);
      loadAgentSessions(agentId);
    } catch(e) { showToast('Unarchive all failed', true); }
    return;
  }
  if (!await showConfirm(`Archive all active chats in "${projectName}"?`)) return;
  try {
    await API.manageSession({ action: 'archive_all', agent: agentId, project: projectName });
    showToast('All chats archived');
    loadProjectChats(agentId, projectName);
    loadAgentSessions(agentId);
  } catch(e) { showToast('Archive all failed', true); }
}

async function deleteAllProjectChats() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const filter = state._projectChatsFilter || 'active';
  const archivedOnly = filter === 'archived';
  const label = archivedOnly ? 'archived chats' : 'ALL chats';
  if (!await showConfirmDanger(`Permanently delete ${label} in "${projectName}"? This cannot be undone.`, 'Delete Chats', 'Delete')) return;
  try {
    const r = await API.manageSession({
      action: 'delete_all', agent: agentId, project: projectName, archived_only: archivedOnly,
    });
    showToast(`Deleted ${r.count || 'all'} chats`);
    // If the active chat was inside this project, reset the view.
    if (state.activeChat?.sessionId && state.currentProject === projectName) {
      newChat();
    }
    loadProjectChats(agentId, projectName);
    loadAgentSessions(agentId);
  } catch(e) { showToast('Delete all failed', true); }
}

// Per-session unarchive helper used by the project chat menu and (eventually)
// the global chats list. Uses manageSession action 'unarchive'.
async function unarchiveSession(sessionId) {
  try {
    await API.manageSession({ action: 'unarchive', session_id: sessionId });
    showToast('Chat unarchived');
    const agentId = state._projectDetailAgent;
    const projectName = state._projectDetailName;
    if (state.currentView === 'project-detail' && agentId && projectName) {
      loadProjectChats(agentId, projectName);
    }
    loadAgentSessions(state.activeAgentId);
  } catch(e) { showToast('Unarchive failed', true); }
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
  content.style.maxWidth = '600px';
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
      <div class="instr-tabs" role="tablist">
        <button class="instr-tab active" id="instr-tab-edit" role="tab" onclick="switchInstrTab('edit')">Bearbeiten</button>
        <button class="instr-tab" id="instr-tab-preview" role="tab" onclick="switchInstrTab('preview')">Vorschau</button>
      </div>
      <textarea class="project-instructions-editor" id="project-instructions-textarea"
        placeholder="z. B. Du bist ein hilfsbereiter Assistent für unser Marketing-Team. Antworte stets in einem professionellen Ton..."
      >${esc(project?.instructions || '')}</textarea>
      <div class="instr-preview-pane" id="project-instructions-preview" style="display:none"></div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Abbrechen</button>
      <button class="btn-primary" onclick="saveProjectInstructions()">Speichern</button>
    </div>
  `;
  overlay.appendChild(content);
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('project-instructions-textarea')?.focus(), 100);
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
      preview.innerHTML = '<span class="instr-preview-empty">Nothing to preview yet — write some instructions in the Edit tab.</span>';
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

// ── Project-level web URLs ──────────────────────────────────
// A project-wide curated source set: each URL is fetched fresh by the
// project-sync daemon (hash-gated — re-mined only when content changes) and
// mined into the project's MemPalace wing + KG, just like input folders.
// Reached via memory/KG retrieval — NOT injected per turn (that's the
// separate per-chat Websuche basket).
// Stored in project.json → web_urls as [{url,title}].
function renderProjectWebUrls(urls) {
  const el = document.getElementById('project-panel-web-urls');
  if (!el) return;
  if (!urls.length) {
    el.innerHTML = '<span class="project-panel-placeholder">Noch keine Web-Adressen hinterlegt.</span>';
    return;
  }
  el.innerHTML = urls.map((u, idx) => {
    let host = u.url || '';
    try { host = new URL(u.url).hostname.replace(/^www\./, ''); } catch (e) {}
    return `
      <div class="project-input-folder-row">
        <div class="pif-row-head">
          <svg class="pif-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
          <span class="pif-name" title="${esc(u.url)}">${esc(u.title || host)}</span>
          <button class="pif-action-btn pif-delete" onclick="removeProjectWebUrl(${idx})" title="Remove URL" aria-label="Remove">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
          </button>
        </div>
        <div class="pif-path" dir="ltr" title="${esc(u.url)}"><a href="${esc(u.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(u.url)}</a></div>
      </div>`;
  }).join('');
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
  let url = (prompt('Web-Adresse hinzufügen (wird als Wissensquelle eingelesen):') || '').trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  const title = (prompt('Optionale Bezeichnung (leer lassen für die Domain):') || '').trim();
  const urls = (state._projectDetail?.web_urls || []).slice();
  if (urls.some(u => u.url === url)) { showToast('Diese Adresse ist bereits hinterlegt'); return; }
  urls.push({ url, title });
  _saveProjectWebUrls(urls);
}

function removeProjectWebUrl(idx) {
  const urls = (state._projectDetail?.web_urls || []).slice();
  if (idx < 0 || idx >= urls.length) return;
  urls.splice(idx, 1);
  _saveProjectWebUrls(urls);
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
    // Update panel display — render as markdown to match loadProjectDetail.
    const instrEl = document.getElementById('project-panel-instructions');
    if (instructions) {
      instrEl.innerHTML = `<div class="project-panel-instructions-rendered">${renderMarkdown(instructions)}</div>`;
      instrEl.classList.remove('project-panel-placeholder');
    } else {
      instrEl.innerHTML = '<span class="project-panel-placeholder">Noch keine Anweisungen hinterlegt.</span>';
    }
    if (state._projectDetail) state._projectDetail.instructions = instructions;
  } catch(e) {
    showToast('Anweisungen konnten nicht gespeichert werden');
  }
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
    wrap.innerHTML = '<span style="font-size:12px;color:var(--text-400);font-style:italic">None added</span>';
    return;
  }
  wrap.innerHTML = ids.map(uid => {
    const u = byId[uid] || {id: uid, display_name: uid};
    return `<span class="project-member-chip" data-uid="${esc(uid)}" style="display:inline-flex;align-items:center;gap:6px;padding:4px 8px;background:var(--bg-200);border:1px solid var(--border-200);border-radius:12px;font-size:12px;margin:2px 4px 2px 0">
      ${esc(_userLabel(u))}
      <button onclick="_pmRemove('${esc(listId)}','${esc(uid)}','${esc(containerId)}')" style="background:none;border:none;cursor:pointer;color:var(--text-400);padding:0;line-height:1" title="Remove">×</button>
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
          <option value="">Select user…</option>
          ${options}
        </select>
        <button class="btn-secondary" onclick="_pmAdd('${esc(opts.selectId)}','${esc(opts.listId)}','${esc(opts.containerId)}')">Add</button>
      </div>
    </div>`;
}

async function showCreateProjectModal() {
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
      <span class="modal-title">New Project</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <div class="project-modal-field">
        <label class="project-modal-label">Project name</label>
        <input class="project-modal-input" id="create-project-name" placeholder="My Project" autofocus>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Description (optional)</label>
        <textarea class="project-modal-input" id="create-project-desc" rows="3" style="resize:vertical"
          placeholder="What is this project about?"></textarea>
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
        visOpts.push(`<option value="user"${!isAdmin && !canTeam ? ' selected' : ''}>Personal</option>`);
        if (canTeam) visOpts.push(`<option value="team"${!isAdmin ? ' selected' : ''}>Team</option>`);
        if (isAdmin) visOpts.push('<option value="global" selected>Global (everyone)</option>');
        const initialVis = isAdmin ? 'global' : (canTeam ? 'team' : 'user');
        const ownerBlock = isAdmin
          ? `<div class="project-modal-field">
              <label class="project-modal-label">Owner</label>
              <select class="project-modal-input" id="create-project-owner">
                ${(state._userDirectory || []).map(u => `<option value="${esc(u.id)}" ${u.id===state.authUser.id?'selected':''}>${esc(_userLabel(u))}</option>`).join('')}
              </select>
            </div>`
          : `<input type="hidden" id="create-project-owner" value="${esc(state.authUser.id)}">`;
        const visBlock = (visOpts.length === 1)
          ? `<input type="hidden" id="create-project-visibility" value="user">`
          : `<div class="project-modal-field">
              <label class="project-modal-label">Visibility</label>
              <select class="project-modal-input" id="create-project-visibility" onchange="_createProjectOnVisChange(this.value)">
                ${visOpts.join('')}
              </select>
            </div>
            <div class="project-modal-field" id="create-project-team-wrap" style="display:${initialVis==='team'?'block':'none'}">
              <label class="project-modal-label">Team</label>
              <select class="project-modal-input" id="create-project-team">
                <option value="">Select team...</option>
                ${teamOptions.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}
              </select>
            </div>`;
        // Members panels (rendered for create; default to whatever initialVis dictates)
        const extrasPicker = _renderMemberPicker({
          label: 'Add members',
          helpText: 'Personal: people who get access. Team: extras outside the team. Global: ignored.',
          listId: '_projectCreateExtras',
          containerId: 'create-project-extras-chips',
          selectId: 'create-project-extras-select',
          excludeIds: [state.authUser.id],
        });
        const excludedPicker = _renderMemberPicker({
          label: 'Exclude users',
          helpText: 'Global only — block specific users from this project.',
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
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
      <button class="btn-primary" onclick="createProject()">Create Project</button>
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
  if (!name) { showToast('Project name is required'); return; }
  if (visibility === 'team' && !teamId) { showToast('Select a team for team-scoped project'); return; }

  const body = { name, description: desc || '' };
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
    showToast('Project created');
    document.querySelector('.modal-overlay')?.remove();
    openProject(agentId, result.name || name);
  } catch(e) {
    showToast('Failed to create project');
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
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="editProjectFromMenu('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Edit</div>
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="archiveProject('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Archive</div>
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--error)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="deleteProject('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Delete</div>
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
    ? `<div style="${itemStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="unarchiveSession('${sid}'); this.closest('.ctx-menu').remove()">Unarchive</div>`
    : `<div style="${itemStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="archiveSession('${sid}'); this.closest('.ctx-menu').remove()">Archive</div>`;
  menu.innerHTML = toggleAction + `
    <div style="${dangerStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="deleteSession('${sid}'); this.closest('.ctx-menu').remove()">Delete</div>
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
  if (!project) { showToast('Failed to load project'); return; }

  const isAdmin = state.authUser && state.authUser.role === 'admin';
  const ownerUid = project.owner_user_id || '';
  const ownerTid = project.owner_team_id || '';
  const isOwner = state.authUser && ownerUid && ownerUid === state.authUser.id;
  const canManage = isAdmin || isOwner;
  if (!canManage) { showToast('Only the project owner can edit this project'); return; }

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
        <label class="project-modal-label">Owner</label>
        <select class="project-modal-input" id="edit-project-owner">
          ${(state._userDirectory || []).map(u => `<option value="${esc(u.id)}" ${u.id===ownerUid?'selected':''}>${esc(_userLabel(u))}</option>`).join('')}
        </select>
        ${isAdmin ? '' : '<div style="font-size:11px;color:var(--text-400);margin-top:4px">Transferring removes your edit rights.</div>'}
      </div>`
    : `<div class="project-modal-field" style="font-size:12px;color:var(--text-400)">Owner: <strong>${esc(_userLabel((state._userDirectory||[]).find(u=>u.id===ownerUid)||{id:ownerUid,display_name:ownerUid}))}</strong></div>`;

  const scopeBlock = canRescope
    ? `<div class="project-modal-field">
        <label class="project-modal-label">Visibility</label>
        <select class="project-modal-input" id="edit-project-visibility" onchange="_editProjectOnVisChange(this.value)">
          <option value="user" ${project.visibility==='user'?'selected':''}>Personal</option>
          <option value="team" ${project.visibility==='team'?'selected':''}>Team</option>
          <option value="global" ${project.visibility==='global'?'selected':''}>Global (everyone)</option>
        </select>
      </div>
      <div class="project-modal-field" id="edit-project-team-wrap" style="display:${project.visibility==='team'?'block':'none'}">
        <label class="project-modal-label">Team</label>
        <select class="project-modal-input" id="edit-project-team">
          <option value="">Select team...</option>
          ${allTeams.map(t => `<option value="${esc(t.id)}" ${t.id===ownerTid?'selected':''}>${esc(t.name)}</option>`).join('')}
        </select>
      </div>`
    : `<div class="project-modal-field" style="font-size:12px;color:var(--text-400)">
        Visibility: <strong>${esc(project.visibility || 'global')}</strong>${ownerTid?` · Team: <strong>${esc((allTeams.find(t=>t.id===ownerTid)||{}).name||ownerTid)}</strong>`:''}
        <div style="margin-top:4px">Only admins can change scope.</div>
      </div>`;

  // Member pickers
  const extrasPicker = _renderMemberPicker({
    label: 'Members',
    helpText: project.visibility === 'team'
      ? 'Team members are auto-included. List below holds extras outside the team.'
      : (project.visibility === 'global' ? '' : 'Users granted access in addition to the owner.'),
    listId: '_projectEditExtras',
    containerId: 'edit-project-extras-chips',
    selectId: 'edit-project-extras-select',
    excludeIds: [ownerUid],
  });
  const excludedPicker = _renderMemberPicker({
    label: 'Excluded users',
    helpText: 'Block these users from a Global project.',
    listId: '_projectEditExcluded',
    containerId: 'edit-project-excluded-chips',
    selectId: 'edit-project-excluded-select',
    excludeIds: [ownerUid],
  });

  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Edit Project</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <div class="project-modal-field">
        <label class="project-modal-label">Project name</label>
        <input class="project-modal-input" id="edit-project-display-name" value="${esc(project.name || projectName)}" placeholder="My Project">
        <div style="font-size:11px;color:var(--text-400);margin-top:4px">Display name only. Folder name stays <code>${esc(projectName)}</code>.</div>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Description</label>
        <textarea class="project-modal-input" id="edit-project-desc" rows="3" style="resize:vertical"
          placeholder="What is this project about?">${esc(project.description || '')}</textarea>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Icon</label>
        <input class="project-modal-input" id="edit-project-icon" value="${esc(project.icon || '📁')}" maxlength="4" style="width:80px;text-align:center;font-size:18px">
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Image</label>
        <div style="display:flex;align-items:center;gap:10px">
          <div id="edit-project-image-preview" style="width:64px;height:42px;border-radius:6px;border:1px solid var(--border-200);background:var(--bg-300);background-size:cover;background-position:center;flex-shrink:0;${project.image ? `background-image:url('/v1/agents/${esc(agentId)}/projects/${esc(projectName)}/image?v=${Date.now()}')` : ''}"></div>
          <label class="btn-secondary" style="cursor:pointer">
            <span id="edit-project-image-label">${project.image ? 'Replace' : 'Upload'}</span>
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden onchange="_editProjectImageUpload(event,'${esc(agentId)}','${esc(projectName)}')">
          </label>
          <button type="button" class="btn-secondary" id="edit-project-image-clear" onclick="_editProjectImageClear('${esc(agentId)}','${esc(projectName)}')" style="display:${project.image?'inline-block':'none'}">Remove</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:4px">Used as the card background on the projects list and on favourites pinned to this project. Max 2 MB.</div>
      </div>
      ${ownerSelectorBlock}
      ${scopeBlock}
      <div id="edit-project-extras-wrap" style="display:${project.visibility==='global'?'none':'block'}">${extrasPicker}</div>
      <div id="edit-project-excluded-wrap" style="display:${project.visibility==='global'?'block':'none'}">${excludedPicker}</div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
      <button class="btn-primary" onclick="saveProjectEdit('${esc(agentId)}','${esc(projectName)}')">Save</button>
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
  if (!displayName) { showToast('Project name is required'); return; }
  const updates = { name: displayName, description: desc, icon };
  if (ownerEl) updates.owner_user_id = ownerEl.value || '';
  if (visEl) {
    updates.visibility = visEl.value;
    if (visEl.value === 'team') {
      const tid = teamEl?.value || '';
      if (!tid) { showToast('Select a team'); return; }
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
    showToast('Project updated');
    document.querySelector('.modal-overlay')?.remove();
    if (state._projectDetailAgent === agentId && state._projectDetailName === projectName) {
      loadProjectDetail(agentId, projectName);
    }
    loadProjectsList();
  } catch(e) {
    showToast('Failed to update project');
  }
}

async function archiveProject(agentId, projectName) {
  try {
    await API.updateProject(agentId, projectName, { status: 'archived' });
    showToast('Project archived');
    loadProjectsList();
  } catch(e) { showToast('Failed to archive project'); }
}

async function deleteProject(agentId, projectName) {
  if (!await showConfirmDanger(`Delete project "${projectName}"? This cannot be undone.`, 'Delete Project', 'Delete')) return;
  try {
    await API.deleteProject(agentId, projectName);
    showToast('Project deleted');
    loadProjectsList();
  } catch(e) { showToast('Failed to delete project'); }
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
