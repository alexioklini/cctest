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
        <button class="project-card-menu" onclick="event.stopPropagation(); showProjectListMenu(event, '${esc(agent)}', '${esc(p.name)}')" title="Weitere Optionen">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
        </button>
      </div>
      <div class="project-card-info">
        <div class="project-card-title"${titleAttr}>${esc(displayName)}</div>
        <div class="project-card-meta">
          <span class="project-card-type">Projekt</span>
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
        descEl.innerHTML = esc(desc.slice(0, 200)) + '... <span class="project-detail-desc-toggle" onclick="toggleProjectDesc()">Mehr anzeigen</span>';
        descEl.dataset.full = desc;
        descEl.dataset.collapsed = 'true';
      } else {
        descEl.textContent = desc;
      }
    } else {
      descEl.innerHTML = '<span style="color:var(--text-400);font-style:italic">Keine Beschreibung</span>';
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
      composerInput.placeholder = `Ihre Nachricht an ${displayName}`;
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
    kgBadge = ` <span class="project-item-pill" data-kg="extracting" title="Knowledge-Graph-Extraktion läuft">KG…</span>`;
  } else if (kgState === 'error') {
    const kgErr = it.kg_last_error || 'KG-Extraktion fehlgeschlagen';
    const triplesPart = (typeof triples === 'number' && triples > 0) ? `${triples} Beziehungen · ` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="error" title="${esc(kgErr)}">${triplesPart}KG !</span>`;
  } else if (typeof triples === 'number' && triples > 0) {
    // Per-folder pill in the right pane — same renaming as the project chip
    // ("triples" is jargon, "relations" is what's been extracted).
    const warnPart = kgParseErrors > 0 ? ` · ${kgParseErrors} Parse-Fehler` : '';
    const warnTitle = kgParseErrors > 0 ? ` (${kgParseErrors} Abschnitte lieferten ungültiges JSON — nicht kritisch)` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="${kgParseErrors > 0 ? 'warn' : 'ok'}" title="Aus diesem Ordner extrahierte Knowledge-Graph-Beziehungen${warnTitle}">${triples} Beziehungen${warnPart}</span>`;
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
        <span class="project-file-name" title="${esc(doc.source || doc.name || '')}">${esc(doc.source || doc.name || 'Dokument')}</span>
        <span data-pif-pill data-pif-kind="attachment" data-pif-id="${esc(srcHash)}">${projectItemPillHtml('attachment', srcHash)}</span>
        <span class="project-file-delete" onclick="deleteProjectFile('${esc(agentId)}','${esc(projectName)}','${esc(srcHash)}'); event.stopPropagation();" title="Entfernen">
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
      empty.textContent = filter === 'archived' ? 'Keine archivierten Chats' : 'Noch keine Chats';
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
      const pcTitle = s.title || s.summary || 'Unbenannt';
      const pcTip = s.summary ? ` title="${esc(s.summary)}"` : '';
      item.innerHTML = `
        <span class="project-chat-item-title"${pcTip}>${esc(pcTitle)}</span>
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
  const chatsEl = document.getElementById('project-detail-chats');
  const schedEl = document.getElementById('project-detail-schedules');
  const chatBulk = document.getElementById('project-chats-bulk');
  const schedBulk = document.getElementById('project-sched-bulk');
  if (chatsEl) chatsEl.style.display = isSched ? 'none' : '';
  if (schedEl) schedEl.style.display = isSched ? '' : 'none';
  if (chatBulk) chatBulk.style.display = isSched ? 'none' : '';
  if (schedBulk) schedBulk.style.display = isSched ? '' : 'none';
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  if (isSched) loadProjectSchedules(agentId, projectName);
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
      // Each run opens the shared deep-detail view (result text, tool
      // timeline, artifacts) — _schedViewRunDetail is self-contained. We wrap
      // it so the detail overlay (z-index 1000) is lifted above THIS project
      // history modal (z-index 9000), otherwise it'd render behind it.
      html += `<div onclick="projectSchedRunDetail(${Number(h.id)})" style="display:flex;align-items:center;gap:8px;padding:8px 6px;border-bottom:1px solid var(--border-100);cursor:pointer;border-radius:6px" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''">
        <span style="width:6px;height:6px;border-radius:50%;background:${ok ? 'var(--success)' : 'var(--error)'};flex-shrink:0"></span>
        <span style="flex:1;color:var(--text-200)">${started}</span>
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
          <button class="pif-action-btn pif-delete" onclick="removeProjectWebUrl(${idx})" title="URL entfernen" aria-label="Entfernen">
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
      <span class="modal-title">Neues Projekt</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <div class="project-modal-field">
        <label class="project-modal-label">Projektname</label>
        <input class="project-modal-input" id="create-project-name" placeholder="Mein Projekt" autofocus>
      </div>
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
