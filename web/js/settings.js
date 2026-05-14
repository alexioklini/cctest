/* ═══════════════════════════════════════════════════════════
   AGENT SETTINGS MODAL (per-agent config)
   ═══════════════════════════════════════════════════════════ */
function openAgentSettings() {
  // Agent config (soul, agent.json, hooks, MCP, tokens) is admin-only on the
  // server — POST /v1/agents/<id>/{file,hooks,...} returns 403 for everyone
  // else. Refuse early so non-admins don't see editable system prompts they
  // can't save, and so /settings + future entry points fail closed.
  if ((state.authUser?.role || 'admin') !== 'admin') {
    showToast('Agent customization is admin-only', true);
    return;
  }
  const agentId = state.activeAgentId || 'main';
  const agent = state.agents.find(a => (a.id || a.name) === agentId);
  const display = agent?.display_name || agentId;

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content x-wide has-sidebar-tabs';

  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">${esc(display)} <span style="font-size:12px;font-family:var(--font-mono);color:var(--text-400);font-weight:400">${esc(agentId)}</span></span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-tabs modal-tabs-vertical" id="agent-settings-tabs">
      <div class="sidebar-group-label">Identity</div>
      <button class="modal-tab active" onclick="switchAgentTab('${esc(agentId)}','soul',this)">Soul</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','agent',this)">Agent</button>

      <div class="sidebar-group-label">Capabilities</div>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','skills',this)">Skills</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','mcp',this)">MCP</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','tokens',this)">Tokens</button>

      <div class="sidebar-group-label">Automation</div>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','hooks',this)">Hooks</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','schedule',this)">Schedule</button>
    </div>
    <div class="modal-body" id="settings-tab-content">
    </div>
  `;

  overlay.appendChild(content);
  document.body.appendChild(overlay);
  switchAgentTab(agentId, 'soul');
}

// Keep backward compat
function openSettingsModal() { openAgentSettings(); }

/* ═══════════════════════════════════════════════════════════
   GENERAL SETTINGS MODAL (system-wide)
   ═══════════════════════════════════════════════════════════ */
function openGeneralSettings() {
  // Server-wide config (providers, models, quotas, GDPR, MemPalace, ...) is
  // admin-only at the server's POST gate. Refuse early so non-admin entry
  // points (slash command, future shortcuts) fail closed instead of letting
  // a non-admin browse and then 403 on save.
  if ((state.authUser?.role || 'admin') !== 'admin') {
    showToast('General settings are admin-only', true);
    return;
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content x-wide has-sidebar-tabs';

  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">General Settings</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-tabs modal-tabs-vertical" id="general-settings-tabs">
      <div class="sidebar-group-label">Server</div>
      <button class="modal-tab active" onclick="switchGeneralTab('server',this)">Server</button>
      <button class="modal-tab" onclick="switchGeneralTab('providers',this)">Providers</button>
      <button class="modal-tab" onclick="switchGeneralTab('nodes',this)">Nodes</button>

      <div class="sidebar-group-label">Models</div>
      <button class="modal-tab" onclick="switchGeneralTab('models',this)">Models</button>

      <div class="sidebar-group-label">Users &amp; Costs</div>
      <button class="modal-tab" onclick="switchGeneralTab('agents',this)">Agents</button>
      <button class="modal-tab" onclick="switchGeneralTab('teams',this)">Teams</button>
      <button class="modal-tab" onclick="switchGeneralTab('costs',this)">Costs</button>
      <button class="modal-tab" onclick="switchGeneralTab('quotas',this)">Quotas</button>

      <div class="sidebar-group-label">Privacy &amp; Memory</div>
      <button class="modal-tab" onclick="switchGeneralTab('gdpr',this)">GDPR</button>
      <button class="modal-tab" onclick="switchGeneralTab('context',this)">Context</button>
      <button class="modal-tab" onclick="switchGeneralTab('mempalace',this)">MemPalace</button>
      <button class="modal-tab" onclick="switchGeneralTab('knowledge-graph',this)">Knowledge&nbsp;Graph</button>

      <div class="sidebar-group-label">Tools</div>
      <button class="modal-tab" onclick="switchGeneralTab('tools',this)">Tools</button>
    </div>
    <div class="modal-body" id="general-tab-content">
    </div>
  `;

  overlay.appendChild(content);
  document.body.appendChild(overlay);
  switchGeneralTab('server');
}

function _renderScheduleRow(t) {
  const sc = t.status === 'active' ? 'var(--success)' : t.status === 'paused' ? 'var(--warning)' : 'var(--text-400)';
  return `<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px">
    <span style="width:6px;height:6px;border-radius:50%;background:${sc};flex-shrink:0"></span>
    <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(t.name)}</span>
    <span style="font-size:11px;font-family:var(--font-mono);color:var(--text-400)">${esc(t.schedule||'')}</span>
    <span style="font-size:11px;color:var(--text-400)">${esc(t.agent||'')}</span>
    <span style="font-size:11px;padding:2px 8px;border-radius:4px;background:${t.status==='active'?'rgba(22,163,74,0.1)':'var(--bg-200)'};color:${sc}">${esc(t.status)}</span>
  </div>`;
}

function openAgentConfig(agentId) {
  if (agentId && agentId !== state.activeAgentId) switchToAgent(agentId);
  openAgentSettings();
}

/* ─── Agent & Team management helpers ─── */
async function _refreshAgentsAndTeams() {
  const [agentsData, teamsData] = await Promise.all([API.getAgents(), API.getTeams()]);
  state.agents = agentsData.agents || agentsData || [];
  state.teamStructure = teamsData || {};
}
async function _createNewAgent() {
  const id = document.getElementById('new-agent-id')?.value?.trim();
  if (!id) { showToast('Agent ID is required', true); return; }
  const desc = document.getElementById('new-agent-desc')?.value?.trim() || '';
  const soul = document.getElementById('new-agent-soul')?.value?.trim() || '';
  const model = document.getElementById('new-agent-model')?.value || '';
  const displayName = document.getElementById('new-agent-display')?.value?.trim() || '';
  try {
    await API.createAgent({ agent: id, description: desc, soul: soul || undefined, model: model || undefined, display_name: displayName || undefined });
    showToast(`Agent "${id}" created`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('agents');
  } catch(e) { showToast('Create failed: ' + e.message, true); }
}
async function _deleteAgent(id) {
  if (!await showConfirmDanger(`Delete agent "${id}"? It will be moved to .trash.`, 'Delete Agent', 'Delete')) return;
  try {
    await API.deleteAgent(id);
    showToast(`Agent "${id}" deleted`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('agents');
  } catch(e) { showToast('Delete failed: ' + e.message, true); }
}
async function _createTeam() {
  const name = document.getElementById('new-team-name')?.value?.trim();
  const desc = document.getElementById('new-team-desc')?.value?.trim() || '';
  const head = document.getElementById('new-team-head')?.value;
  const membersEl = document.getElementById('new-team-members');
  const members = Array.from(membersEl?.selectedOptions || []).map(o => o.value);
  if (!head) { showToast('Team head is required', true); return; }
  if (!members.length) { showToast('Select at least one member', true); return; }
  if (!members.includes(head)) members.push(head);
  try {
    await API.manageTeams({ action: 'create', head, members, name: name || undefined, description: desc || undefined });
    showToast(`Team "${name || head}" created`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Create team failed: ' + e.message, true); }
}
async function _dissolveTeam(teamId) {
  if (!await showConfirmDanger(`Dissolve this team? Members will become standalone.`, 'Dissolve Team', 'Dissolve')) return;
  try {
    await API.manageTeams({ action: 'dissolve', team_id: teamId });
    showToast('Team dissolved');
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Dissolve failed: ' + e.message, true); }
}
async function _removeFromTeam(agentId, teamId) {
  try {
    await API.manageTeams({ action: 'move', agent: agentId, from_team: teamId, to_team: null });
    showToast(`${agentId} removed from team`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Remove failed: ' + e.message, true); }
}
async function _addToTeam(teamId) {
  const sel = document.getElementById('team-add-' + teamId);
  const agentId = sel?.value;
  if (!agentId) { showToast('Select an agent to add', true); return; }
  try {
    const ts = state.teamStructure;
    // Find which team the agent is currently in (if any)
    let fromTeam = null;
    if (ts.teams) {
      for (const [tid, team] of Object.entries(ts.teams)) {
        if ((team.members||[]).some(m => m.id === agentId)) { fromTeam = tid; break; }
      }
    }
    await API.manageTeams({ action: 'move', agent: agentId, from_team: fromTeam, to_team: teamId });
    showToast(`${agentId} added to team`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Add failed: ' + e.message, true); }
}

/* ═══════════════════════════════════════════════════════════
   GENERAL SETTINGS TAB SWITCHER
   ═══════════════════════════════════════════════════════════ */
async function switchGeneralTab(tab, btn) {
  if (btn) {
    btn.closest('.modal-tabs').querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
  }
  const C = document.getElementById('general-tab-content');
  C.innerHTML = '<div style="padding:20px;color:var(--text-400)">Loading...</div>';
  const P = (s) => `<div style="padding:16px">${s}</div>`;
  const G = (gap='8px') => `display:grid;gap:${gap}`;
  const ROW = `display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px`;
  const DOT = (ok) => `<span style="width:7px;height:7px;border-radius:50%;background:${ok?'var(--success)':'var(--error)'};flex-shrink:0"></span>`;
  const MONO = `font-size:11px;font-family:var(--font-mono);color:var(--text-400)`;
  const BADGE = (t,c='var(--text-400)') => `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg-200);color:${c}">${esc(t)}</span>`;
  const SEC = (title) => `<div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:12px 0 4px">${title}</div>`;

  /* ─── SERVER ─── */
  if (tab === 'server') {
    try {
      const svc = await API.getServices();
      const srv = svc.server || {};
      applyGdprConfigToScanner(srv.gdpr_scanner);
      let svcRows = '';
      for (const [name, info] of Object.entries(svc)) {
        if (typeof info !== 'object') continue;
        const ok = info.status === 'running' || info.status === 'ok' || info.connected;
        svcRows += `<div style="${ROW}">${DOT(ok)}
          <span style="font-size:13px;color:var(--text-100);flex:1">${esc(name)}</span>
          ${info.port ? `<span style="${MONO}">:${info.port}</span>` : ''}
          <span style="font-size:11px;color:${ok?'var(--success)':'var(--error)'}">${esc(info.status||(ok?'running':'stopped'))}</span>
        </div>`;
      }
      // Default model selector — chat-capable only.
      const mc = state.modelsConfig?.models || {};
      const enabledModels = enabledModelsWithCapability('chat');
      const modelOpts = enabledModels.map(([mid])=>modelOption(mid, {selected: mid===srv.default_model})).join('');

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          ${DOT(true)}<span style="font-size:14px;font-weight:500;color:var(--text-100)">Connected</span>
          <span style="${MONO};margin-left:auto">${esc(BASE_URL)}</span>
          ${srv.version?`<span style="${MONO}">v${esc(srv.version)}</span>`:''}
          ${srv.pid?`<span style="${MONO}">PID ${srv.pid}</span>`:''}
        </div>
        ${SEC('Services')}${svcRows}
        ${SEC('Default Model')}
        <div style="display:flex;gap:8px;align-items:center">
          <select class="form-select" id="srv-default-model" style="flex:1">${modelOpts}</select>
          <button class="btn-secondary" onclick="API.post('/v1/services/server',{default_model:document.getElementById('srv-default-model').value}).then(()=>showToast('Default model updated')).catch(e=>showToast('Failed',true))">Set</button>
        </div>
        ${SEC('Attachments')}
        <div style="display:flex;gap:8px;align-items:center">
          <select class="form-select" id="srv-attachment-image-model" style="flex:1">
            <option value="">None (images not described)</option>
            ${enabledModelsWithCapability('image').map(([mid])=>modelOption(mid, {selected: mid===(srv.attachment_image_model||'')})).join('')}
          </select>
          <button class="btn-secondary" onclick="API.post('/v1/services/server',{attachment_image_model:document.getElementById('srv-attachment-image-model').value}).then(()=>showToast('Image model updated')).catch(e=>showToast('Failed',true))">Set</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:2px">Vision model used to describe attached images when the active model has no vision support (e.g. gemini-2.5-flash, mistral-small-latest)</div>
        ${(() => {
          const defMdl = srv.default_model || '';
          const hasVision = modelHasCapability(defMdl, 'image');
          const hasImageModel = !!(srv.attachment_image_model);
          return (!hasVision && !hasImageModel) ? `<div style="font-size:11px;color:var(--warning, #b45309);margin-top:4px;padding:6px 8px;border-radius:6px;background:var(--bg-200)">&#9888; Your default model does not support vision and no image description model is configured. Attached images will only return basic metadata (dimensions, format).</div>` : '';
        })()}
        ${SEC('Cost Quotas')}
        <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          <span style="font-size:12px;color:var(--text-200);flex:1">Per-user, per-role limits with billing-cycle reset.</span>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="switchGeneralTab('quotas', document.querySelector('.modal-tab[onclick*=\\'quotas\\']'))">Configure &rarr;</button>
        </div>
        ${SEC('GDPR / PII Scanner')}
        <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          ${DOT((srv.gdpr_scanner||{}).enabled !== false)}
          <span style="font-size:12px;color:var(--text-200);flex:1">
            ${(srv.gdpr_scanner||{}).enabled !== false ? 'Scanner active' : 'Scanner disabled'}
            ${(srv.gdpr_scanner||{}).server_block ? ' &middot; <b style="color:var(--warning,#b45309)">hard-block on</b>' : ''}
          </span>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="switchGeneralTab('gdpr', document.querySelector('.modal-tab[onclick*=\\'gdpr\\']'))">Configure &rarr;</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:2px">Granular category actions, email allowlist, and the local fallback model live in the dedicated GDPR tab.</div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn-secondary" onclick="API.restartServer().then(()=>showToast('Server restarting...')).catch(e=>showToast('Failed',true))">Restart Server</button>
        </div>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--error)">Cannot reach server</div>'); }
  }

  /* ─── MODELS ─── */
  if (tab === 'models') {
    const mc = state.modelsConfig?.models || {};
    // Group by provider, skipping models from non-existent providers
    const existingProviders = new Set((state.providers || []).map(p => p.name));
    const byProvider = {};
    for (const [mid, cfg] of Object.entries(mc)) {
      const prov = cfg.provider || 'unassigned';
      if (prov !== 'unassigned' && !existingProviders.has(prov)) continue;
      (byProvider[prov] = byProvider[prov] || []).push([mid, cfg]);
    }
    const provKeys = Object.keys(byProvider).sort();
    // Sort models within each provider by display name
    for (const pk of provKeys) byProvider[pk].sort((a, b) => {
      const ae = a[1].enabled ? 0 : 1, be = b[1].enabled ? 0 : 1;
      if (ae !== be) return ae - be;
      return (a[1].display_name || modelShortName(a[0], false)).localeCompare(b[1].display_name || modelShortName(b[0], false));
    });

    // Helper: small labeled input for model detail panel
    const mdlInput = (cls, label, val, opts = {}) => {
      const { type = 'number', step, min, max, ph, width, choices } = opts;
      const s = `width:${width||'100%'};padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)`;
      let inp;
      if (choices) {
        inp = `<select class="${cls}" style="${s}">${choices.map(c => `<option value="${c}"${val===c?' selected':''}>${c||'(default)'}</option>`).join('')}</select>`;
      } else {
        inp = `<input class="${cls}" type="${type}" value="${val??''}" style="${s}" ${step?`step="${step}"`:''}${min!=null?` min="${min}"`:''}${max!=null?` max="${max}"`:''}${ph?` placeholder="${ph}"`:''}>`;
      }
      return `<div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px">${label}</label>${inp}</div>`;
    };

    let html = `<div style="${G('6px')}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <button class="btn-secondary" onclick="this.disabled=true;this.textContent='Syncing...';API.post('/v1/models/config',{action:'sync'}).then(()=>{showToast('Syncing...');setTimeout(()=>API.getModelsConfig().then(d=>{state.modelsConfig=d;switchGeneralTab('models');showToast('Synced')}),3000)}).catch(e=>{showToast('Failed',true);this.disabled=false;this.textContent='Sync from Providers'})">Sync from Providers</button>
      </div>`;
    for (const prov of provKeys) {
      const models = byProvider[prov];
      const provId = `mdl-prov-${prov.replace(/[^a-zA-Z0-9]/g,'_')}`;
      const isOmlx = prov === 'omlx';
      html += `<div style="margin-bottom:6px;border:1px solid var(--border-100);border-radius:8px;overflow:hidden">
        <div style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:8px 12px;background:var(--bg-100)" onclick="const c=document.getElementById('${provId}');const open=c.style.display!=='none';c.style.display=open?'none':'block';this.querySelector('.mdl-arrow').textContent=open?'▶':'▼'">
          <span class="mdl-arrow" style="font-size:10px;color:var(--text-400)">▶</span>
          <span style="font-size:13px;font-weight:600;color:var(--text-100)">${esc(prov)}</span>
          <span style="font-size:11px;color:var(--text-400)">${models.length} model${models.length!==1?'s':''}</span>
          <span style="margin-left:auto;display:flex;gap:4px" onclick="event.stopPropagation()">
            <button class="btn-secondary" style="padding:1px 6px;font-size:10px" onclick="document.querySelectorAll('#${provId} .mdl-enabled').forEach(c=>{c.checked=true;c.closest('.mdl-header-row').style.opacity=1})">All</button>
            <button class="btn-secondary" style="padding:1px 6px;font-size:10px" onclick="document.querySelectorAll('#${provId} .mdl-enabled').forEach(c=>{c.checked=false;c.closest('.mdl-header-row').style.opacity=0.5})">None</button>
          </span>
        </div>
        <div id="${provId}" style="display:none;padding:4px 8px">`;
      for (const [mid, cfg] of models) {
        const inf = cfg.inference || {};
        const detId = `mdl-det-${mid.replace(/[^a-zA-Z0-9]/g,'_')}`;
        html += `<div data-model-id="${esc(mid)}">
          <div style="${ROW};opacity:${cfg.enabled?1:0.5}" class="mdl-header-row">
            <input type="checkbox" class="mdl-enabled" ${cfg.enabled?'checked':''} onchange="this.closest('.mdl-header-row').style.opacity=this.checked?1:0.5">
            <input class="mdl-display-name" value="${esc(cfg.display_name || modelShortName(mid, false))}" style="width:140px;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:12px;background:var(--bg-000);color:var(--text-100)" placeholder="Display name" title="Display name">
            <span style="${MONO};flex:1;overflow:hidden;text-overflow:ellipsis" title="${esc(mid)}">${esc(mid)}</span>
            <span class="mdl-warmup-dot" data-model-dot="${esc(mid)}" style="display:${cfg.warmup?'inline-block':'none'};width:8px;height:8px;border-radius:50%;background:var(--text-500);flex:none" title="Warmup state"></span>
            <label style="font-size:11px;color:var(--text-400)">P</label><input type="number" class="mdl-priority" value="${cfg.priority||0}" style="width:50px;padding:2px 4px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;text-align:center;background:var(--bg-000);color:var(--text-200)">
            <button class="btn-secondary" style="padding:2px 6px;font-size:12px" onclick="const d=document.getElementById('${detId}');d.style.display=d.style.display==='none'?'block':'none'" title="Model settings">&#9881;</button>
            <button class="btn-secondary" style="padding:2px 6px;font-size:10px;color:var(--error)" onclick="_confirmRemoveModel('${esc(mid)}')">&#10005;</button>
          </div>
          <div id="${detId}" style="display:none;padding:8px 12px;margin:0 0 6px 0;border:1px solid var(--border-100);border-top:none;border-radius:0 0 8px 8px;background:var(--bg-100)">
            <div style="margin-bottom:8px">
              <label style="font-size:11px;font-weight:600;color:var(--text-100);display:block;margin-bottom:3px">Description <span style="color:var(--text-400);font-weight:400">(shown as tooltip in model dropdowns)</span></label>
              <textarea class="mdl-description" rows="2" style="width:100%;padding:4px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:12px;background:var(--bg-000);color:var(--text-100);font-family:inherit;resize:vertical" placeholder="e.g. Best for long-context analysis. Slow but cheap.">${esc(cfg.description || '')}</textarea>
            </div>
            <div style="display:flex;align-items:center;gap:10px;padding:6px 8px;margin-bottom:8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000)">
              <label style="font-size:11px;font-weight:600;color:var(--text-100);margin:0">Profile</label>
              <select class="mdl-profile" style="padding:3px 8px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-100);color:var(--text-100)" title="Speed: warmup + stable KV prefix, no token savings (local). Balanced: current defaults. Frugal: aggressive token savings, caveman system prompt (cloud). Custom: no overlay.">
                ${[['custom','Custom (no overlay)'],['speed','Speed (local, warm cache)'],['balanced','Balanced (default)'],['frugal','Frugal (cloud, save tokens)']].map(([v,l]) => `<option value="${v}"${(cfg.profile||'custom')===v?' selected':''}>${l}</option>`).join('')}
              </select>
              <span style="font-size:10px;color:var(--text-400);margin-left:auto">Profile sets defaults — explicit fields below override them</span>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px">
              ${mdlInput('mdl-max-context','Context Window',cfg.max_context,{ph:'131072'})}
              ${mdlInput('mdl-max-output','Max Output',cfg.max_output,{ph:'16384'})}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              ${mdlInput('mdl-inf-temperature','Temperature',inf.temperature,{step:'0.1',min:0,max:2,ph:'1.0'})}
              ${mdlInput('mdl-inf-top_p','Top P',inf.top_p,{step:'0.05',min:0,max:1,ph:'1.0'})}
              ${mdlInput('mdl-inf-top_k','Top K',inf.top_k,{min:0,ph:'(none)'})}
              ${mdlInput('mdl-inf-max_tokens','Max Tokens Override',inf.max_tokens,{ph:'(auto)'})}
              ${mdlInput('mdl-inf-frequency_penalty','Freq Penalty',inf.frequency_penalty,{step:'0.1',min:-2,max:2,ph:'0'})}
              ${mdlInput('mdl-inf-presence_penalty','Pres Penalty',inf.presence_penalty,{step:'0.1',min:-2,max:2,ph:'0'})}
              ${isOmlx ? `
                ${mdlInput('mdl-inf-min_p','Min P',inf.min_p,{step:'0.01',min:0,max:1,ph:'0'})}
                ${mdlInput('mdl-inf-repetition_penalty','Rep Penalty',inf.repetition_penalty,{step:'0.1',min:0,max:2,ph:'1.0'})}
              ` : ''}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              ${mdlInput('mdl-cost-input','Cost In ($/M)',cfg.cost_input,{step:'0.01',min:0,ph:'0'})}
              ${mdlInput('mdl-cost-output','Cost Out ($/M)',cfg.cost_output,{step:'0.01',min:0,ph:'0'})}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px">Caveman System</label>
                <select class="mdl-caveman-system" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  ${[[0,'off'],[1,'lite'],[2,'full'],[3,'ultra']].map(([v,l]) => `<option value="${v}"${(cfg.caveman_system||0)===v?' selected':''}>${l}</option>`).join('')}
                </select>
              </div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="How this model emits reasoning. none = disabled. inline_tags = <think>...</think> in content (DeepSeek-R1, GLM-Zero). reasoning_field = sibling reasoning_content (oMLX with enable_thinking, Gemini 2.5, DeepSeek-R1 direct). mistral_blocks = nested thinking blocks (magistral, mistral-small-2603+). openai_opaque = hidden, only token count exposed (o1/o3/o4-mini).">Thinking Format</label>
                <select class="mdl-thinking-format" data-mid="${esc(mid)}" onchange="_mdlRefreshThinkingLevel(this)" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  ${['none','inline_tags','reasoning_field','mistral_blocks','openai_opaque'].map(v => `<option value="${v}"${(cfg.thinking_format||'none')===v?' selected':''}>${v}</option>`).join('')}
                </select>
              </div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Default thinking level for this model. Used when a chat or scheduled task selects 'Inherit from model'. Available options depend on the Thinking Format.">Thinking Level</label>
                <select class="mdl-thinking-level" data-mid="${esc(mid)}" data-current="${esc((inf||{}).thinking_level||'')}" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                </select>
              </div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-parallel-tools" ${cfg.parallel_tool_calls !== false ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer">Parallel Tool Calls</label></div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-warmup" ${cfg.warmup ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer" title="Prime this model's KV cache once so first-token latency is minimal. The warm state is held until the model is evicted — no periodic re-priming.">Warmup</label></div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Full: prefill system+tools into KV cache (~5-6s first response, costs GPU memory). Minimal: load weights only (~10-15s first response, tiny memory footprint). Full-primed models may evict each other if GPU memory is tight.">Warmup Mode</label>
                <select class="mdl-warmup-mode" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  <option value="full" ${(cfg.warmup_mode||'full')==='full'?'selected':''}>full (KV prefix)</option>
                  <option value="minimal" ${cfg.warmup_mode==='minimal'?'selected':''}>minimal (weights only)</option>
                </select>
              </div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-warmup-allow-cloud" ${cfg.warmup_allow_cloud ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer" title="Permit warmup against cloud providers (costs tokens)">Allow cloud</label></div>
              <div style="grid-column:1/-1"><label class="form-label" style="font-size:11px">Raw Formats <span style="color:var(--text-400);font-weight:400">(MIME patterns the model handles natively as multimodal)</span></label><input class="form-input mdl-raw-formats" value="${esc((cfg.raw_formats||[]).join(', '))}" placeholder="e.g. image/*, application/pdf" style="font-size:12px"></div>
              <div style="grid-column:1/-1"><label class="form-label" style="font-size:11px">Capabilities <span style="color:var(--text-400);font-weight:400">(routing flags — controls where the model is selectable in the UI)</span></label>
                <div class="mdl-capabilities-grid" data-mid="${esc(mid)}" style="display:flex;flex-wrap:wrap;gap:10px;padding:6px 8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)">
                  ${(()=>{
                    const caps = new Set(cfg.capabilities||[]);
                    const opts = [
                      ['chat',  'Chat',  'Selectable in the chat composer + every general model dropdown.'],
                      ['image', 'Image', 'Vision input — used by read_document for image attachments.'],
                      ['audio', 'Audio', 'Speech-to-text — listed under transcribe_audio.'],
                      ['tts',   'TTS',   'Text-to-speech — listed under text_to_speech.'],
                      ['video', 'Video', 'Video input — reserved for video-capable models.'],
                    ];
                    return opts.map(([k,l,t]) => `<label style="display:flex;gap:5px;align-items:center;font-size:11px;cursor:pointer" title="${esc(t)}"><input type="checkbox" class="mdl-cap-cb" data-cap="${k}" ${caps.has(k)?'checked':''}>${l}</label>`).join('');
                  })()}
                </div>
              </div>
            </div>
          </div>
        </div>`;
      }
      html += `</div></div>`;
    }
    // Add Model form
    const knownProvs = [...new Set(Object.values(mc).map(c=>c.provider).filter(Boolean))].sort();
    html += `<div style="margin-top:12px;padding:12px;border:1px solid var(--border-200);border-radius:8px;${G('8px')}">
      <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:4px">Add Model Manually</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
        <div style="flex:2;min-width:180px"><label class="form-label">Model ID</label><input class="form-input" id="add-model-id" placeholder="e.g. my-model-v1"></div>
        <div style="flex:1;min-width:120px"><label class="form-label">Provider</label><input class="form-input" id="add-model-provider" list="add-model-provs" placeholder="provider name"><datalist id="add-model-provs">${knownProvs.map(p=>`<option value="${esc(p)}">`).join('')}</datalist></div>
        <div style="flex:1;min-width:120px"><label class="form-label">Display Name</label><input class="form-input" id="add-model-display" placeholder="Optional"></div>
        <button class="btn-primary" style="height:34px" onclick="addManualModel()">Add</button>
      </div>
    </div>`;
    html += `<div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn-primary" onclick="saveModelsConfig()">Save</button>
    </div></div>`;
    C.innerHTML = P(html);
    // Populate every per-model Thinking Level dropdown using the row's
    // current Thinking Format. The format <select> has an inline onchange
    // that re-renders its sibling level <select> via _mdlRefreshThinkingLevel.
    C.querySelectorAll('.mdl-thinking-level').forEach(sel => {
      const fmtSel = sel.closest('div').parentElement.querySelector('.mdl-thinking-format');
      if (fmtSel) _mdlPopulateThinkingLevel(fmtSel.value || 'none', sel, sel.dataset.current || '');
    });
  }

  /* ─── PROVIDERS ─── */
  if (tab === 'providers') {
    try {
      const [provs, statsResp] = await Promise.all([
        API.getProviders(),
        API.get('/v1/providers/stats?days=30').catch(() => ({stats:[]})),
      ]);
      const providers = Array.isArray(provs) ? provs : (provs.providers || []);
      const statsByProvider = {};
      for (const s of (statsResp.stats || [])) statsByProvider[s.provider] = s;
      let html = `<div style="${G('12px')}">`;
      for (const p of providers) {
        const ok = p.model_count > 0;
        const mc = p.models?.length || p.model_count || 0;
        const pid = `prov-edit-${p.name.replace(/[^a-zA-Z0-9]/g,'_')}`;
        const USAGE_LABELS = {preferred:'Preferred (prio 1)',round_robin:'Round-robin (prio 2)',fallback:'Fallback (prio 3)'};
        const USAGE_COLORS = {preferred:'var(--accent)',round_robin:'var(--text-200)',fallback:'var(--text-400)'};
        const pStats = statsByProvider[p.name];
        const fmtNum = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n||0);
        const keys = p.api_keys || [];
        const keyCounts = {preferred:0, round_robin:0, fallback:0};
        for (const k of keys) keyCounts[k.usage] = (keyCounts[k.usage]||0) + 1;
        const keySummaryParts = [];
        if (keyCounts.preferred) keySummaryParts.push(`${keyCounts.preferred} preferred`);
        if (keyCounts.round_robin) keySummaryParts.push(`${keyCounts.round_robin} round-robin`);
        if (keyCounts.fallback) keySummaryParts.push(`${keyCounts.fallback} fallback`);
        const keySummary = keys.length
          ? `${keys.length} key${keys.length===1?'':'s'}${keySummaryParts.length?` · ${keySummaryParts.join(' · ')}`:''}`
          : 'No keys configured';
        const keySummaryColor = keys.length ? 'var(--text-200)' : 'var(--warning)';
        const provStatsLine = pStats
          ? `${pStats.calls} calls · ${fmtNum(pStats.tokens_in)} in · ${fmtNum(pStats.tokens_out)} out${pStats.cost_usd > 0 ? ' · $'+pStats.cost_usd.toFixed(4) : ''} (30d)`
          : 'No usage in last 30 days';
        html += `<div style="padding:12px;border:1px solid var(--border-100);border-radius:10px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            ${DOT(ok)}
            <span style="font-size:14px;font-weight:500;color:var(--text-000)">${esc(p.name)}</span>
            <span style="${MONO};margin-left:auto">${mc} models</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="syncProvider(this,'${esc(p.name)}')" title="Add newly-available models from this provider. Honors deletions.">Sync</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="resyncProvider(this,'${esc(p.name)}')" title="Drop all models for this provider AND clear deletion tombstones, then re-discover. Manual only.">Full Resync</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="testProvider('${esc(p.name)}')">Test</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="document.getElementById('${pid}').style.display=document.getElementById('${pid}').style.display==='none'?'block':'none'">Settings</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="renameProvider('${esc(p.name)}')" title="Rename this provider. Updates models, default_provider, tombstones, and provider-scoped model ids in one shot.">Rename</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_confirmDeleteProvider('${esc(p.name)}')">Delete</button>
          </div>
          <div style="${MONO};overflow:hidden;text-overflow:ellipsis;margin-bottom:8px">${esc(p.base_url||'')}</div>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 8px;background:var(--bg-100);border-radius:6px">
            <span style="font-size:11px;color:${keySummaryColor};font-weight:500">${keySummary}</span>
            <span style="${MONO};font-size:10px;color:var(--text-400);margin-left:6px">${provStatsLine}</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;margin-left:auto" onclick="openProviderKeysModal('${esc(p.name)}')">Manage Keys</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="openProviderStatsModal('${esc(p.name)}')">Stats</button>
          </div>
          ${(p.models||[]).length?`<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">${(p.models||[]).slice(0,8).map(m=>{const mid=typeof m==='string'?m:(m.id||m);return BADGE(modelShortName(mid,false));}).join('')}${(p.models||[]).length>8?`<span style="${MONO}">+${(p.models||[]).length-8} more</span>`:''}</div>`:''}
          <div id="${pid}" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--border-100)">
            <div style="${G('8px')}">
              <div><label class="form-label">Base URL</label><input class="form-input" id="${pid}-url" value="${esc(p.base_url||'')}"></div>
              <div><label class="form-label">Default Model</label><input class="form-input" id="${pid}-model" value="${esc(p.default_model||'')}"></div>
              <div><button class="btn-primary" style="font-size:12px" onclick="saveProviderEdit('${esc(p.name)}','${pid}')">Save settings</button></div>
            </div>
          </div>
        </div>`;
      }
      html += `
        ${SEC('Add Provider')}
        <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('10px')}">
          <div><label class="form-label">Name</label><input class="form-input" id="prov-name" placeholder="e.g. my-provider"></div>
          <div><label class="form-label">Base URL</label><input class="form-input" id="prov-url" placeholder="http://localhost:8081/v1"></div>
          <div><label class="form-label">API Key</label><input class="form-input" id="prov-key" placeholder="sk-..." type="password"></div>
          <div><label class="form-label">Default Model</label><input class="form-input" id="prov-model" placeholder="model-name (optional)"></div>
          <div style="display:flex;gap:8px">
            <button class="btn-secondary" onclick="testNewProvider()">Test Connection</button>
            <button class="btn-primary" onclick="saveNewProvider()">Add Provider</button>
          </div>
          <div id="prov-test-result"></div>
        </div>
      </div>`;
      C.innerHTML = P(html);
    } catch(e) { C.innerHTML = P('<div style="color:var(--error)">Failed to load providers</div>'); }
  }

  /* ─── AGENTS ─── */
  if (tab === 'agents') {
    const agents = state.agents || [];
    let html = `<div style="${G('12px')}">`;
    html += `${SEC('Create Agent')}
      <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
        <div style="display:flex;gap:8px">
          <div style="flex:1"><label class="form-label">Agent ID</label><input class="form-input" id="new-agent-id" placeholder="e.g. Analyst"></div>
          <div style="flex:1"><label class="form-label">Display Name</label><input class="form-input" id="new-agent-display" placeholder="Optional display name"></div>
        </div>
        <div><label class="form-label">Description</label><input class="form-input" id="new-agent-desc" placeholder="What does this agent do?"></div>
        <div><label class="form-label">Model</label><select class="form-select" id="new-agent-model" style="width:100%">
          ${enabledModelsWithCapability('chat').map(([mid])=>modelOption(mid)).join('')}
        </select></div>
        <div><label class="form-label">Soul (system prompt)</label><textarea class="form-input" id="new-agent-soul" rows="3" placeholder="Optional initial soul.md content" style="resize:vertical"></textarea></div>
        <div style="display:flex;gap:8px">
          <button class="btn-primary" onclick="_createNewAgent()">Create Agent</button>
        </div>
        <div id="agent-create-result"></div>
      </div>`;
    html += SEC('All Agents');
    for (const a of agents) {
      const aid = a.id || a.name;
      const isMain = aid === 'main';
      html += `<div style="${ROW}">
        <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(a.display_name||aid)}</span>
        <span style="${MONO}">${esc(aid)}</span>
        ${a.model?`<span style="${MONO}">${esc(modelShortName(a.model))}</span>`:''}
        ${a.paused?BADGE('paused','var(--warning)'):''}
        ${a.is_team_head?BADGE('team head','var(--accent)'):''}
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="openAgentConfig('${esc(aid)}');this.closest('.modal-overlay').remove()">Configure</button>
        ${isMain?'':`<button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_deleteAgent('${esc(aid)}')">Delete</button>`}
      </div>`;
    }
    C.innerHTML = P(html + '</div>');
  }

  /* ─── TEAMS ─── */
  if (tab === 'teams') {
    const ts = state.teamStructure;
    const allAgents = state.agents || [];
    let html = `<div style="${G('12px')}">`;

    /* Existing teams */
    if (ts.teams && Object.keys(ts.teams).length) {
      for (const [tid, team] of Object.entries(ts.teams)) {
        html += `<div style="padding:12px;border:1px solid var(--border-100);border-radius:10px">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-size:15px;font-weight:600;color:var(--text-000);flex:1">${esc(team.name||tid)}</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_dissolveTeam('${esc(tid)}')">Dissolve</button>
          </div>
          ${team.description?`<div style="font-size:12px;color:var(--text-400);margin:4px 0">${esc(team.description)}</div>`:''}
          <div style="${G('4px')};margin-top:8px">`;
        for (const m of (team.members||[])) {
          const mid = m.id;
          html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--border-100);border-radius:6px">
            <span style="font-size:13px;color:var(--text-100);flex:1">${esc(m.display_name||mid)}</span>
            <span style="${MONO}">${esc(mid)}</span>
            ${BADGE(m.is_team_head?'head':'member')}
            ${!m.is_team_head?`<button class="btn-secondary" style="padding:1px 6px;font-size:10px;color:var(--error)" onclick="_removeFromTeam('${esc(mid)}','${esc(tid)}')">Remove</button>`:''}
          </div>`;
        }
        html += `</div>
          <div style="display:flex;gap:6px;margin-top:8px;align-items:center">
            <select class="form-select" id="team-add-${esc(tid)}" style="flex:1;font-size:12px">
              <option value="">Add agent to team...</option>
              ${allAgents.filter(a=>{const aid=a.id||a.name;return aid!=='main'&&!(team.members||[]).some(m=>m.id===aid)}).map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
            </select>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="_addToTeam('${esc(tid)}')">Add</button>
          </div>
        </div>`;
      }
    }

    /* Standalone agents */
    if (ts.standalone?.length) {
      html += SEC('Standalone');
      for (const a of ts.standalone) {
        html += `<div style="${ROW}"><span style="font-size:13px;color:var(--text-100);flex:1">${esc(a.display_name||a.id)}</span><span style="${MONO}">${esc(a.id)}</span></div>`;
      }
    }

    /* Create team form */
    html += SEC('Create Team');
    const nonMainAgents = allAgents.filter(a=>(a.id||a.name)!=='main');
    html += `<div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
      <div style="display:flex;gap:8px">
        <div style="flex:1"><label class="form-label">Team Name</label><input class="form-input" id="new-team-name" placeholder="e.g. Research Team"></div>
        <div style="flex:1"><label class="form-label">Description</label><input class="form-input" id="new-team-desc" placeholder="Optional"></div>
      </div>
      <div><label class="form-label">Team Head</label><select class="form-select" id="new-team-head" style="width:100%">
        <option value="">Select head agent...</option>
        ${nonMainAgents.map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
      </select></div>
      <div><label class="form-label">Members (select multiple)</label><select class="form-select" id="new-team-members" multiple style="width:100%;min-height:80px">
        ${nonMainAgents.map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
      </select></div>
      <div style="display:flex;gap:8px">
        <button class="btn-primary" onclick="_createTeam()">Create Team</button>
      </div>
      <div id="team-create-result"></div>
    </div>`;

    C.innerHTML = P(html + '</div>');
  }

  /* ─── NODES ─── */
  if (tab === 'nodes') {
    try {
      const data = await API.get('/v1/nodes');
      const nodes = data.nodes || [];
      let html = `<div style="${G('8px')}">`;
      for (const n of nodes) {
        const ok = n.status === 'connected' || n.status === 'online';
        html += `<div style="padding:12px;border:1px solid var(--border-100);border-radius:10px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            ${DOT(ok)}
            <span style="font-size:14px;font-weight:500;color:var(--text-100)">${esc(n.name||n.id||'node')}</span>
            ${n.paused?BADGE('paused','var(--warning)'):''}
            <span style="${MONO};margin-left:auto">${esc(n.url||n.host||'')}</span>
          </div>
          ${n.description?`<div style="font-size:12px;color:var(--text-400)">${esc(n.description)}</div>`:''}
          <div style="display:flex;gap:4px;margin-top:8px;flex-wrap:wrap">
            ${n.os?`<span style="${MONO}">${esc(n.os)}</span>`:''}
            ${n.hostname?`<span style="${MONO}">${esc(n.hostname)}</span>`:''}
            ${n.cpu_percent!=null?`<span style="${MONO}">CPU ${n.cpu_percent}%</span>`:''}
            ${n.mem_used_gb!=null?`<span style="${MONO}">RAM ${n.mem_used_gb.toFixed(1)}/${n.mem_total_gb?.toFixed(1)||'?'}GB</span>`:''}
          </div>
          <div style="display:flex;gap:6px;margin-top:8px">
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="API.post('/v1/nodes',{action:'${n.paused?'resume':'pause'}',name:'${esc(n.name)}'}).then(()=>{showToast('${n.paused?'Resumed':'Paused'}');switchGeneralTab('nodes')})">${n.paused?'Resume':'Pause'}</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_confirmRemoveNode('${esc(n.name)}')">Remove</button>
          </div>
        </div>`;
      }
      if (!nodes.length) html += '<div style="padding:20px;text-align:center;color:var(--text-400)">No remote nodes configured</div>';
      html += `${SEC('Add Node')}
        <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
          <div><label class="form-label">Name</label><input class="form-input" id="node-name" placeholder="my-node"></div>
          <div><label class="form-label">Description</label><input class="form-input" id="node-desc" placeholder="Optional description"></div>
          <button class="btn-primary" onclick="createNode()">Create Node</button>
          <div id="node-result"></div>
        </div></div>`;
      C.innerHTML = P(html);
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Nodes not available</div>'); }
  }

  /* ─── CONTEXT ─── */
  if (tab === 'context') {
    try {
      const cfg = await API.get('/v1/context/config');
      const enabledModels = enabledModelsWithCapability('chat');
      const modelOpts = `<option value="">Auto (cheapest)</option>` + enabledModels.map(([mid])=>modelOption(mid, {selected: mid===cfg.summary_model})).join('');

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="ctx-enabled" ${cfg.enabled!==false?'checked':''}>
          <label for="ctx-enabled" style="font-size:14px;font-weight:500;color:var(--text-200)">Lossless Context Management enabled</label>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div><label class="form-label">Fresh Tail (messages)</label><input class="form-input" id="ctx-fresh-tail" type="number" value="${cfg.fresh_tail_count||cfg.fresh_tail||16}" min="4" max="200"></div>
          <div><label class="form-label">Compact Threshold (%)</label><input class="form-input" id="ctx-threshold" type="number" value="${Math.round((cfg.compact_threshold||0.6)*100)}" min="50" max="95"></div>
          <div><label class="form-label">Messages per Summary</label><input class="form-input" id="ctx-msgs-per-sum" type="number" value="${cfg.messages_per_summary||10}" min="3" max="50"></div>
          <div><label class="form-label">Condense Threshold</label><input class="form-input" id="ctx-condense" type="number" value="${cfg.condense_threshold||4}" min="2" max="10"></div>
          <div><label class="form-label">Max Depth</label><input class="form-input" id="ctx-max-depth" type="number" value="${cfg.max_depth||5}" min="1" max="10"></div>
          <div><label class="form-label">Summary Target Tokens</label><input class="form-input" id="ctx-target-tokens" type="number" value="${cfg.summary_target_tokens||1000}" min="200" max="4000" step="100"></div>
        </div>
        <div><label class="form-label">Summary Model</label><select class="form-select" id="ctx-summary-model" style="width:100%">${modelOpts}</select></div>
        <button class="btn-primary" onclick="saveContextConfig()">Save</button>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Context config not available</div>'); }
  }

  /* ─── COSTS ─── */
  if (tab === 'costs') {
    try {
      const [stats, daily] = await Promise.all([API.getCosts(24).catch(()=>({})), API.getCostsDaily(7).catch(()=>({daily:[]}))]);
      let html = `<div style="${G('16px')}">
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--accent-brand)">$${(stats.total_cost||0).toFixed(2)}</div>
            <div style="font-size:11px;color:var(--text-400)">Last 24h</div>
          </div>
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--text-000)">${(stats.total_calls||0).toLocaleString()}</div>
            <div style="font-size:11px;color:var(--text-400)">API Calls</div>
          </div>
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--text-000)">${((stats.total_tokens_in||0)+(stats.total_tokens_out||0)).toLocaleString()}</div>
            <div style="font-size:11px;color:var(--text-400)">Total Tokens</div>
          </div>
        </div>
        ${Array.isArray(stats.by_agent)&&stats.by_agent.length?`${SEC('By Agent')}${stats.by_agent.map(s=>`<div style="${ROW}"><span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(s.agent)}</span><span style="${MONO}">${s.calls||0} calls</span><span style="font-size:13px;font-weight:500;color:var(--accent-brand)">$${(s.cost||0).toFixed(3)}</span></div>`).join('')}`:''}
        ${SEC('Daily (7 days)')}`;
      for (const d of (daily.daily||[])) {
        html += `<div style="${ROW}">
          <span style="font-size:13px;color:var(--text-200);font-family:var(--font-mono)">${esc(d.day||d.date||'')}</span>
          <span style="flex:1"></span>
          <span style="${MONO}">${(d.calls||0)} calls</span>
          <span style="${MONO}">${((d.tokens_in||0)+(d.tokens_out||0)).toLocaleString()} tok</span>
          <span style="font-size:13px;font-weight:500;color:var(--text-100)">$${(d.cost||0).toFixed(3)}</span>
        </div>`;
      }
      if (!(daily.daily||[]).length) html += '<div style="padding:20px;text-align:center;color:var(--text-400)">No cost data</div>';
      C.innerHTML = P(html + '</div>');
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Cost data not available</div>'); }
  }

  /* ─── QUOTAS ─── */
  if (tab === 'quotas') {
    if (!state.authUser || state.authUser.role !== 'admin') {
      C.innerHTML = P('<div style="color:var(--text-400);text-align:center;padding:32px">Quota configuration is admin-only.</div>');
      return;
    }
    try {
      const cfg = await API.get('/v1/quotas/config');
      const usersResp = await API.get('/v1/quotas/admin/users').catch(()=>({users:[]}));
      const users = usersResp.users || [];
      const localModels = enabledModelsWithCapability('chat')
        .filter(([,c]) => c.is_local).map(([mid]) => mid);
      const cycleOpts = ['monthly','weekly','yearly'].map(c => `<option value="${c}" ${c===cfg.billing_cycle?'selected':''}>${c}</option>`).join('');
      const enforceOpts = [
        ['warn_only','Warn only (no server-side refusal)'],
        ['force_local','Force local model on red'],
        ['hard_block','Hard block on red'],
      ].map(([v,l]) => `<option value="${v}" ${v===cfg.enforce_red?'selected':''}>${esc(l)}</option>`).join('');
      const fbOpts = ['<option value="">— none —</option>'].concat(
        localModels.map(mid => modelOption(mid, {selected: mid===cfg.default_local_fallback_model, label: modelShortName(mid, true)}))
      ).join('');
      const startDayLabel = (cycle) => ({monthly:'Day of month (1-31)', weekly:'Day of week (0=Mon … 6=Sun)', yearly:'Month of year (1-12)'})[cycle] || 'Start';
      const limitInput = (role, fld) => {
        const v = (cfg.limits[role]||{})[fld] || 0;
        return `<input class="form-input" data-quota-role="${role}" data-quota-field="${fld}" type="number" step="0.01" min="0" value="${v}" style="width:100px;text-align:right">`;
      };
      const colorByLevel = { green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--error)' };
      const levelChip = (lv) => `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;background:var(--bg-200);font-size:11px;color:${colorByLevel[lv]||'var(--text-400)'};font-weight:600;text-transform:uppercase">${lv}</span>`;
      const fmt = (v) => '$' + (v < 1 ? v.toFixed(3) : v.toFixed(2));
      const usersHtml = users.length ? users.map(u => {
        const cycPct = (u.cycle?.pct || 0).toFixed(0);
        const dayPct = (u.daily?.pct || 0).toFixed(0);
        return `<div style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000)">
          ${levelChip(u.level)}
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;color:var(--text-100);font-weight:500;display:flex;align-items:center;gap:6px">
              ${esc(u.display_name || u.username)} <span style="font-size:10px;color:var(--text-400);text-transform:uppercase">${esc(u.role)}</span>
              ${u.has_override ? '<span style="font-size:10px;color:var(--accent-brand)">override</span>' : ''}
              ${u.disabled ? '<span style="font-size:10px;color:var(--error)">disabled</span>' : ''}
            </div>
            <div style="font-size:11px;color:var(--text-300);margin-top:2px">
              today ${fmt(u.daily.used_usd)} / ${fmt(u.daily.limit_usd)} (${dayPct}%) &middot;
              cycle ${fmt(u.cycle.used_usd)} / ${fmt(u.cycle.limit_usd)} (${cycPct}%)
            </div>
          </div>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="quotaOpenUserBreakdown('${esc(u.user_id)}','${esc(u.display_name||u.username)}')">Details</button>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="quotaEditOverride('${esc(u.user_id)}','${esc(u.display_name||u.username)}')">${u.has_override ? 'Edit override' : 'Set override'}</button>
        </div>`;
      }).join('<div style="height:6px"></div>') : '<div style="color:var(--text-400);padding:12px 0">No users.</div>';
      C.innerHTML = P(`<div style="${G('16px')}">
        ${SEC('Cycle')}
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Billing cycle</label>
            <select id="q-billing-cycle" class="form-input" style="width:140px">${cycleOpts}</select>
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)" id="q-start-day-label">${startDayLabel(cfg.billing_cycle)}</label>
            <input id="q-start-day" class="form-input" type="number" min="0" max="31" value="${cfg.cycle_start_day}" style="width:120px">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Warn at (%)</label>
            <input id="q-warn-pct" class="form-input" type="number" min="0" max="100" value="${cfg.warn_pct}" style="width:80px">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Block at (%)</label>
            <input id="q-block-pct" class="form-input" type="number" min="0" max="200" value="${cfg.block_pct}" style="width:80px">
          </div>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200);margin-left:auto">
            <input id="q-enabled" type="checkbox" ${cfg.enabled?'checked':''}> Enabled
          </label>
        </div>

        ${SEC('Enforcement on red')}
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <select id="q-enforce" class="form-input" style="flex:1;max-width:340px">${enforceOpts}</select>
          <div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:200px">
            <label style="font-size:11px;color:var(--text-400)">Local fallback model (force_local mode)</label>
            <select id="q-fallback" class="form-input">${fbOpts}</select>
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-400)">
          <b>Warn only</b>: pill goes red, requests still allowed. <b>Force local</b>: requests automatically swap to the configured local model. <b>Hard block</b>: requests are refused until the cycle resets.
        </div>

        ${SEC('Per-role limits (USD)')}
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="color:var(--text-400);font-size:11px">
            <th style="text-align:left;padding:6px 8px;font-weight:500">Role</th>
            <th style="text-align:right;padding:6px 8px;font-weight:500">Daily</th>
            <th style="text-align:right;padding:6px 8px;font-weight:500">${esc(({monthly:'Monthly',weekly:'Weekly',yearly:'Yearly'})[cfg.billing_cycle]||'Cycle')}</th>
          </tr></thead>
          <tbody>
          ${['admin','poweruser','user'].map(role => `
            <tr><td style="padding:6px 8px;color:var(--text-100);text-transform:capitalize">${role}</td>
              <td style="padding:6px 8px;text-align:right">${limitInput(role,'daily_usd')}</td>
              <td style="padding:6px 8px;text-align:right">${limitInput(role,'cycle_usd')}</td>
            </tr>`).join('')}
          </tbody>
        </table>
        <div style="font-size:11px;color:var(--text-400)">Set 0 to mean "no limit" for that axis. Local-model usage never counts.</div>

        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn-primary" onclick="saveQuotaConfig()">Save settings</button>
          <button class="btn-secondary" onclick="switchGeneralTab('quotas', document.querySelector('.modal-tab.active'))">Reload</button>
        </div>

        ${SEC('Users')}
        ${usersHtml}
      </div>`);
      // Wire dynamic label for start-day
      const cyc = document.getElementById('q-billing-cycle');
      if (cyc) {
        cyc.addEventListener('change', () => {
          const lbl = document.getElementById('q-start-day-label');
          if (lbl) lbl.textContent = startDayLabel(cyc.value);
        });
      }
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--text-400)">Quotas not available: ${esc(String(e))}</div>`);
    }
  }

  /* ─── MEMPALACE ─── */
  if (tab === 'mempalace') {
    try {
      const mp = await API.get('/v1/mempalace/stats');
      if (!mp.enabled) {
        C.innerHTML = P(`<div style="${G('12px')}"><div style="color:var(--text-400)">MemPalace is disabled in config.json</div></div>`);
        return;
      }
      if (mp.error) {
        C.innerHTML = P(`<div style="color:var(--error)">${esc(mp.error)}</div>`);
        return;
      }

      // Classifier config
      const clf = await API.get('/v1/mempalace/classifier').catch(() => ({}));
      const modelOpts = (state.models || []).filter(m => {
        const mid = (typeof m === 'string') ? m : (m.id || m.name);
        return modelHasCapability(mid, 'chat');
      }).map(m => {
        const mid = m.id || m.name || m;
        const sel = mid === (clf.model || '') ? ' selected' : '';
        return modelOption(mid, {selected: mid === (clf.model || '')});
      }).join('');
      const allCats = ['fact','preference','decision','reference','generic','refusal','chitchat'];
      const fileCats = new Set(clf.categories_to_file || ['fact','preference','decision','reference']);
      const catChecks = allCats.map(c => `<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;margin-right:10px"><input type="checkbox" class="mp-clf-cat" value="${c}" ${fileCats.has(c)?'checked':''}>${c}</label>`).join('');

      const STAT = (val, label, color='var(--accent-brand)') => `<div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center;min-width:110px">
        <div style="font-size:22px;font-weight:600;color:${color}">${val}</div>
        <div style="font-size:11px;color:var(--text-400)">${label}</div>
      </div>`;

      // Overview stats
      const hallEntries = Object.entries(mp.halls || {});
      const statsRow = `<div style="display:flex;gap:12px;flex-wrap:wrap">
        ${STAT(mp.total_drawers.toLocaleString(), 'Drawers')}
        ${STAT(mp.total_closets.toLocaleString(), 'Closets')}
        ${STAT(mp.wing_count, 'Wings')}
        ${STAT(mp.room_count, 'Rooms')}
        ${STAT(hallEntries.length, 'Halls')}
        ${STAT((mp.graph?.tunnel_rooms||0), 'Tunnels')}
        ${STAT(mp.palace_size_mb + ' MB', 'DB Size', 'var(--text-200)')}
      </div>`;

      // Hall breakdown
      const hallColors = {'memory':'#7cb5e8','technical':'#e8927c','emotions':'#d4a0e8','consciousness':'#7ce8d8','general':'#c8c8c8'};
      const hallsHtml = hallEntries.length ? hallEntries.sort((a,b) => b[1].count - a[1].count).map(([name, info]) => {
        const color = hallColors[name] || '#e8d87c';
        const roomChips = Object.entries(info.rooms || {}).sort((a,b)=>b[1]-a[1]).map(([r,c]) =>
          `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:var(--text-300)">${esc(r)} (${c})</span>`
        ).join(' ');
        return `<div style="${ROW}">
          <span style="width:10px;height:10px;border-radius:2px;background:${color};flex-shrink:0"></span>
          <span style="font-weight:500;min-width:100px">${esc(name)}</span>
          <span style="${MONO}">${info.count} drawers</span>
          <div style="display:flex;gap:4px;flex-wrap:wrap">${roomChips}</div>
        </div>`;
      }).join('') : '';



      // Wings breakdown
      const wings = mp.wings || {};
      const sortedWings = Object.entries(wings).sort((a,b) => b[1].drawer_count - a[1].drawer_count);
      const userWings = sortedWings.filter(([,v]) => v.user_scoped);
      const sharedWings = sortedWings.filter(([,v]) => !v.user_scoped);

      const wingRow = (name, info) => {
        const scope = info.user_scoped
          ? `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg-300);color:var(--text-300)">${esc(info.user_name || info.user_id)}</span>`
          : `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:color-mix(in srgb, var(--accent-brand) 15%, transparent);color:var(--accent-brand)">shared</span>`;
        const topRooms = Object.entries(info.rooms || {}).sort((a,b)=>b[1]-a[1]).slice(0,5);
        const roomChips = topRooms.map(([r,c]) => `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:var(--text-300)">${esc(r)} (${c})</span>`).join(' ');
        return `<div style="${ROW};flex-wrap:wrap">
          <span style="font-weight:500;flex:1;min-width:140px">${esc(name)}</span>
          ${scope}
          <span style="${MONO}">${info.drawer_count} drawers</span>
          <span style="${MONO}">${info.room_count} rooms</span>
          <div style="width:100%;display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">${roomChips}</div>
        </div>`;
      };

      let wingsHtml = '';
      if (sharedWings.length) {
        wingsHtml += sharedWings.map(([n,i]) => wingRow(n,i)).join('');
      }
      if (userWings.length) {
        wingsHtml += `<div style="font-size:11px;color:var(--text-400);margin:8px 0 4px">User-Scoped Wings (${userWings.length})</div>`;
        wingsHtml += userWings.map(([n,i]) => wingRow(n,i)).join('');
      }

      // Daemons config + chat sync status merged
      const sync = mp.chat_sync || {};
      const syncTime = sync.last_sync ? new Date(sync.last_sync * 1000).toLocaleString() : 'never';
      const cfg = mp.config || {};
      const daemonRows = `
        <div style="${ROW}">
          ${DOT(cfg.mine_enabled)} <span style="flex:1">Miner</span>
          <span style="${MONO}">every ${Math.round(cfg.mine_interval_s/60)}m</span>
          <span style="${MONO}">${cfg.mine_sources} source(s)</span>
        </div>
        <div style="${ROW}">
          ${DOT(cfg.chat_sync_enabled)} <span style="flex:1">Chat Sync</span>
          <span style="${MONO}">every ${cfg.chat_sync_interval_s}s</span>
          ${cfg.chat_sync_build_closets ? BADGE('closets','var(--success)') : BADGE('no closets')}
          <span style="${MONO}">${sync.synced_sessions} sessions</span>
          <span style="${MONO}">last: ${esc(syncTime)}</span>
        </div>
      `;

      // Tunnels
      const tunnelList = (mp.tunnels || {}).tunnels || [];
      let tunnelsHtml = '';
      if (tunnelList.length) {
        tunnelsHtml = tunnelList.map(t => `<div style="${ROW}">
          <span style="${MONO}">${esc(t.source_wing||'')}/${esc(t.source_room||'')}</span>
          <span style="color:var(--text-400)">\u2194</span>
          <span style="${MONO}">${esc(t.target_wing||'')}/${esc(t.target_room||'')}</span>
          ${t.label ? `<span style="font-size:11px;color:var(--text-300)">${esc(t.label)}</span>` : ''}
        </div>`).join('');
      } else {
        tunnelsHtml = `<div style="color:var(--text-400);font-size:12px">No explicit tunnels configured</div>`;
      }

      // Recent WAL activity
      const wal = mp.wal || {};
      let walHtml = '';
      if (wal.total_ops) {
        const opTypes = Object.entries(wal.ops_by_type || {}).sort((a,b)=>b[1]-a[1]);
        const opBadges = opTypes.map(([op,n]) => `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg-300);color:var(--text-300)">${esc(op)}: ${n}</span>`).join(' ');
        const recentOps = (wal.recent_ops || []).slice(-10).reverse();
        const recentRows = recentOps.map(o => {
          const ts = o.timestamp ? new Date(o.timestamp).toLocaleString() : '';
          return `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border-100)">
            <span style="${MONO};min-width:140px">${esc(ts)}</span>
            <span style="font-size:11px;font-weight:500;min-width:100px">${esc(o.operation)}</span>
            <span style="${MONO}">${esc(o.wing)}${o.room ? '/' + esc(o.room) : ''}</span>
          </div>`;
        }).join('');
        walHtml = `<div style="${G('8px')}">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span style="font-size:12px;color:var(--text-300)">${wal.total_ops.toLocaleString()} total operations</span>
            ${opBadges}
          </div>
          <div style="max-height:200px;overflow-y:auto">${recentRows}</div>
        </div>`;
      } else {
        walHtml = `<div style="color:var(--text-400);font-size:12px">No write-ahead log entries</div>`;
      }

      // Anomaly detection
      let anomalies = [];
      if (mp.total_drawers > 0 && mp.total_closets === 0) anomalies.push('No closets built — search ranking may be degraded');
      if (mp.total_drawers > 10000) anomalies.push(`Large palace (${mp.total_drawers.toLocaleString()} drawers) — search may slow down`);
      const emptyWings = sortedWings.filter(([,v]) => v.drawer_count < 3);
      if (emptyWings.length) anomalies.push(`${emptyWings.length} wing(s) with <3 drawers: ${emptyWings.map(([n])=>n).join(', ')}`);
      if (!cfg.chat_sync_enabled) anomalies.push('Chat sync is disabled — new conversations are not being memorized');
      if (!cfg.mine_enabled) anomalies.push('Miner is disabled — file changes are not being indexed');
      if (sync.last_sync && (Date.now()/1000 - sync.last_sync) > 600) anomalies.push('Last chat sync was over 10 minutes ago');
      const orphanRatio = mp.total_closets > 0 ? mp.total_drawers / mp.total_closets : 0;
      if (orphanRatio > 20 && mp.total_drawers > 100) anomalies.push(`High drawer/closet ratio (${Math.round(orphanRatio)}:1) — many drawers may lack closet coverage`);

      const anomalyHtml = anomalies.length
        ? anomalies.map(a => `<div style="${ROW};border-color:color-mix(in srgb, var(--warning,#f59e0b) 40%, transparent)">
            <span style="color:var(--warning,#f59e0b)">\u26A0</span>
            <span style="font-size:12px">${esc(a)}</span>
          </div>`).join('')
        : `<div style="${ROW};border-color:color-mix(in srgb, var(--success) 30%, transparent)">${DOT(true)} <span style="font-size:12px;color:var(--text-300)">No anomalies detected</span></div>`;

      C.innerHTML = P(`<div style="${G('16px')}">
        ${SEC('Overview')}
        ${statsRow}

        ${SEC('Palace Explorer')}
        <div id="mp-tree-tabs" style="display:flex;gap:0;margin-bottom:8px">
          <button class="modal-tab active" onclick="mpTreeSwitch('wings',this)" style="padding:6px 14px;font-size:12px">Wings</button>
          <button class="modal-tab" onclick="mpTreeSwitch('tunnels',this)" style="padding:6px 14px;font-size:12px">Tunnels</button>
        </div>
        <div id="mp-tree" style="max-height:400px;overflow-y:auto;border:1px solid var(--border-100);border-radius:8px;padding:4px 0"></div>

        ${anomalies.length ? SEC('Anomalies') + anomalyHtml : ''}

        ${SEC('Daemons')}
        ${daemonRows}

        ${SEC('Chat Sync Classifier')}
        <div style="${G('10px')}">
          <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">LLM gate that classifies messages before filing to MemPalace. Skips refusals, chitchat, and generic content.</div>
          <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
            <label style="display:flex;align-items:center;gap:6px;font-size:12px"><input type="checkbox" id="mp-clf-enabled" ${clf.enabled?'checked':''}>Enabled</label>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Model:</label>
              <select id="mp-clf-model" class="form-input" style="font-size:12px;padding:4px 8px;max-width:260px">
                <option value="">— select model —</option>
                ${modelOpts}
              </select>
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Min turns:</label>
              <input type="number" id="mp-clf-min-turns" class="form-input" style="font-size:12px;padding:4px 8px;width:60px" value="${clf.min_turns||0}" min="0" max="100" title="Skip chats shorter than this (0 = no minimum)">
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Default for new chats:</label>
              <select id="mp-clf-default-mode" class="form-input" style="font-size:12px;padding:4px 8px">
                <option value="0" ${(clf.default_mode||0)===0?'selected':''}>Off</option>
                <option value="2" ${(clf.default_mode||0)===2?'selected':''}>Auto</option>
                <option value="1" ${(clf.default_mode||0)===1?'selected':''}>On</option>
              </select>
            </div>
          </div>
          <div style="margin-top:8px;font-size:11px;color:var(--text-400)">
            Auto mode: ${clf.enabled && clf.model ? 'LLM classifier' : ''}${clf.enabled && clf.model && clf.min_turns ? ' + ' : ''}${clf.min_turns ? 'min ' + clf.min_turns + ' turns' : ''}${!clf.enabled && !clf.min_turns ? 'no filters configured' : ''}
          </div>
          <div style="margin-top:8px">
            <label style="font-size:11px;color:var(--text-400)">File categories:</label>
            <div style="margin-top:4px">${catChecks}</div>
          </div>
          <button class="btn-primary" style="margin-top:10px;font-size:12px;padding:6px 16px" onclick="saveMpClassifier()">Save</button>
        </div>

        ${SEC('Write-Ahead Log')}
        ${walHtml}

        <div style="font-size:10px;color:var(--text-400);margin-top:8px">Palace: ${esc(mp.palace_path)}</div>
      </div>`);

      // --- Palace tree view ---
      const _mpUNames = {};
      for (const [wn, wi] of Object.entries(mp.wings || {})) {
        if (wi.user_name) _mpUNames[wi.user_id] = wi.user_name;
      }
      function _mpFriendly(name) {
        if (name.includes('--')) { const [u,a] = name.split('--',2); return (_mpUNames[u]||u.slice(0,6)) + ' / ' + a; }
        if (name.includes('/')) { const [u,a] = name.split('/',2); return (_mpUNames[u]||u.slice(0,6)) + ' / ' + a; }
        return name;
      }
      const _mpWings = mp.wings || {};
      const _mpHalls = mp.halls || {};
      const _mpTunnels = ((mp.tunnels || {}).tunnels || []);

      function _mpIcon(type) {
        const icons = {wing:'\uD83D\uDCE6',room:'\uD83D\uDCBB',drawer:'\uD83D\uDCC4',closet:'\uD83D\uDDC4',hall:'\uD83D\uDEA7',tunnel:'\uD83D\uDD17'};
        return icons[type]||'\u25CF';
      }
      function _mpBadge(t,c) { return '<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:'+c+'">'+esc(t)+'</span>'; }
      function _mpCount(n,label) { return '<span style="font-size:10px;font-family:var(--font-mono);color:var(--text-400)">'+n+' '+label+'</span>'; }

      function _mpTreeNode(icon, label, count, badge, depth, expandFn) {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 8px;padding-left:'+(12+depth*20)+'px;cursor:pointer;border-radius:4px;font-size:12px';
        row.onmouseenter = () => row.style.background = 'var(--bg-200)';
        row.onmouseleave = () => row.style.background = '';
        const arrow = document.createElement('span');
        arrow.style.cssText = 'font-size:9px;color:var(--text-400);width:12px;text-align:center;flex-shrink:0;transition:transform 0.15s;pointer-events:none';
        arrow.textContent = expandFn ? '\u25B6' : '';
        row.appendChild(arrow);
        const ic = document.createElement('span');
        ic.style.cssText = 'font-size:12px;flex-shrink:0;pointer-events:none';
        ic.textContent = icon;
        row.appendChild(ic);
        const lbl = document.createElement('span');
        lbl.style.cssText = 'font-weight:500;color:var(--text-100);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;pointer-events:none';
        lbl.textContent = label;
        row.appendChild(lbl);
        if (count) { const c = document.createElement('span'); c.style.cssText='font-size:10px;font-family:var(--font-mono);color:var(--text-400);pointer-events:none'; c.textContent=count; row.appendChild(c); }
        if (badge) { const b = document.createElement('span'); b.style.pointerEvents='none'; b.innerHTML = badge; row.appendChild(b); }
        const children = document.createElement('div');
        children.style.display = 'none';
        let expanded = false;
        if (expandFn) {
          row.onclick = async () => {
            expanded = !expanded;
            arrow.style.transform = expanded ? 'rotate(90deg)' : '';
            if (expanded && !children.dataset.loaded) { children.dataset.loaded='1'; await expandFn(children, depth+1); }
            children.style.display = expanded ? '' : 'none';
          };
        }
        const wrap = document.createElement('div');
        wrap.appendChild(row);
        wrap.appendChild(children);
        return wrap;
      }

      function _mpDrawerNode(d, depth) {
        const ts = d.filed_at ? new Date(d.filed_at).toLocaleString() : '';
        const hallBadge = d.hall ? '<span style="font-size:9px;padding:1px 4px;border-radius:3px;background:'+(hallColors[d.hall]||'var(--bg-300)')+';color:rgba(0,0,0,0.6)">'+esc(d.hall)+'</span>' : '';
        const summary = d.id.slice(7,22);
        return _mpTreeNode('\uD83D\uDCC4', summary, ts, hallBadge, depth, (ch) => {
          const detail = document.createElement('div');
          detail.style.cssText = 'padding:6px 8px;padding-left:'+(12+(depth+1)*20)+'px;font-size:11px;color:var(--text-200);white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto;background:var(--bg-100);border-radius:4px;margin:2px 8px';
          detail.innerHTML = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px">' +
            '<span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">' + esc(d.id) + '</span>' +
            '<span style="font-size:10px;color:var(--text-400)">' + esc(d.added_by) + '</span>' +
            (d.source_file ? '<span style="font-size:10px;color:var(--text-400)">' + esc(d.source_file) + '</span>' : '') +
          '</div>' + esc(d.text);
          ch.appendChild(detail);
        });
      }

      function _mpClosetNode(c, depth) {
        const summary = c.id.slice(0, 25);
        return _mpTreeNode('\uD83D\uDDC4\uFE0F', summary, (c.drawer_count||0)+' refs', '', depth, (ch) => {
          const detail = document.createElement('div');
          detail.style.cssText = 'padding:6px 8px;padding-left:'+(12+(depth+1)*20)+'px;font-size:10px;font-family:var(--font-mono);color:var(--text-300);white-space:pre-wrap;word-break:break-word;max-height:160px;overflow-y:auto;background:var(--bg-100);border-radius:4px;margin:2px 8px';
          detail.textContent = c.text;
          ch.appendChild(detail);
        });
      }

      async function _mpLoadDrawers(container, wing, room, depth) {
        try {
          const data = await API.get('/v1/mempalace/drawers?wing='+encodeURIComponent(wing)+'&room='+encodeURIComponent(room));
          const drawers = data.drawers || [];
          const closets = data.closets || [];
          if (!drawers.length && !closets.length) { container.innerHTML = '<div style="padding:4px 8px;padding-left:'+(12+depth*20)+'px;color:var(--text-400);font-size:11px">Empty</div>'; return; }
          if (closets.length) {
            for (const c of closets) container.appendChild(_mpClosetNode(c, depth));
          }
          for (const d of drawers) container.appendChild(_mpDrawerNode(d, depth));
        } catch(e) { container.innerHTML = '<div style="color:var(--error);padding:4px 12px;font-size:11px">Failed to load</div>'; }
      }

      function _mpRenderWingsTree(tree) {
        tree.innerHTML = '';
        const sorted = Object.entries(_mpWings).sort((a,b) => b[1].drawer_count - a[1].drawer_count);
        if (!sorted.length) { tree.innerHTML = '<div style="padding:12px;color:var(--text-400);font-size:12px">No wings</div>'; return; }

        // Section A: Rooms view
        const secA = document.createElement('div');
        secA.innerHTML = '<div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;padding:8px 12px">By Room</div>';
        for (const [wname, winfo] of sorted) {
          const scopeBadge = winfo.user_scoped ? _mpBadge(winfo.user_name||winfo.user_id,'var(--text-300)') : _mpBadge('shared','var(--accent-brand)');
          const wNode = _mpTreeNode(_mpIcon('wing'), _mpFriendly(wname), winfo.drawer_count+' drawers', scopeBadge, 0, (ch, d) => {
            const rooms = Object.entries(winfo.rooms||{}).sort((a,b)=>b[1]-a[1]);
            for (const [rname, rcount] of rooms) {
              ch.appendChild(_mpTreeNode(_mpIcon('room'), rname, rcount+' drawers', '', d, (ch2, d2) => _mpLoadDrawers(ch2, wname, rname, d2)));
            }
          });
          secA.appendChild(wNode);
        }
        tree.appendChild(secA);

        // Section B: Halls view
        const hallEntries = Object.entries(_mpHalls).sort((a,b) => b[1].count - a[1].count);
        if (hallEntries.length) {
          const secB = document.createElement('div');
          secB.style.borderTop = '1px solid var(--border-100)';
          secB.style.marginTop = '8px';
          secB.innerHTML = '<div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;padding:8px 12px">By Hall</div>';
          for (const [hname, hinfo] of hallEntries) {
            const color = hallColors[hname] || '#e8d87c';
            const dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:'+color+';margin-right:2px"></span>';
            const hNode = _mpTreeNode(_mpIcon('hall'), hname, hinfo.count+' drawers', dot, 0, (ch, d) => {
              const hRooms = Object.entries(hinfo.rooms||{}).sort((a,b)=>b[1]-a[1]);
              for (const [rname, rcount] of hRooms) {
                ch.appendChild(_mpTreeNode(_mpIcon('room'), rname, rcount+' drawers', '', d, async (ch2, d2) => {
                  // Load drawers for this room, filtered to this hall
                  for (const [wname] of sorted) {
                    if (!(wname in _mpWings) || !(_mpWings[wname].rooms||{})[rname]) continue;
                    try {
                      const data = await API.get('/v1/mempalace/drawers?wing='+encodeURIComponent(wname)+'&room='+encodeURIComponent(rname));
                      const filtered = (data.drawers||[]).filter(dr => dr.hall === hname);
                      for (const d of filtered) ch2.appendChild(_mpDrawerNode(d, d2));
                    } catch(e) {}
                  }
                }));
              }
            });
            secB.appendChild(hNode);
          }
          tree.appendChild(secB);
        }
      }

      function _mpRenderTunnelsTree(tree) {
        tree.innerHTML = '';
        if (!_mpTunnels.length) {
          tree.innerHTML = '<div style="padding:12px;color:var(--text-400);font-size:12px">No tunnels configured</div>';
          return;
        }
        for (const t of _mpTunnels) {
          const label = (t.source_wing||'')+'/'+( t.source_room||'') + ' \u2194 ' + (t.target_wing||'')+'/'+(t.target_room||'');
          const tNode = _mpTreeNode(_mpIcon('tunnel'), t.label || label, '', '', 0, (ch, d) => {
            // Source side
            ch.appendChild(_mpTreeNode(_mpIcon('room'), (t.source_room||'') + ' (' + _mpFriendly(t.source_wing||'') + ')', '', '', d,
              (ch2, d2) => _mpLoadDrawers(ch2, t.source_wing, t.source_room, d2)));
            // Target side
            ch.appendChild(_mpTreeNode(_mpIcon('room'), (t.target_room||'') + ' (' + _mpFriendly(t.target_wing||'') + ')', '', '', d,
              (ch2, d2) => _mpLoadDrawers(ch2, t.target_wing, t.target_room, d2)));
          });
          tree.appendChild(tNode);
        }
      }

      window.mpTreeSwitch = function(tab, btn) {
        if (btn) { btn.closest('#mp-tree-tabs').querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active')); btn.classList.add('active'); }
        const tree = document.getElementById('mp-tree');
        if (!tree) return;
        if (tab === 'wings') _mpRenderWingsTree(tree);
        else if (tab === 'tunnels') _mpRenderTunnelsTree(tree);
      };
      setTimeout(() => { const t = document.getElementById('mp-tree'); if (t) _mpRenderWingsTree(t); }, 50);

      // (treemap code removed — replaced by tree view above)

    } catch(e) { C.innerHTML = P(`<div style="color:var(--error)">Failed to load MemPalace stats: ${esc(e.message||e)}</div>`); }
  }

  /* ─── KNOWLEDGE GRAPH ─── */
  if (tab === 'knowledge-graph') {
    try {
      const [stats, kgConfig] = await Promise.all([
        API.get('/v1/mempalace/kg/stats').catch(e => ({error: e.message || String(e)})),
        API.get('/v1/mempalace/kg/config').catch(() => ({})),
      ]);
      if (stats.error) {
        C.innerHTML = P(`<div style="color:var(--error)">${esc(stats.error)}</div>`);
        return;
      }
      const isAdmin = state.authUser && state.authUser.role === 'admin';

      // Model picker — same shape as classifier picker.
      const enabledMc = state.modelsConfig?.models || {};
      const enabledModelList = Object.entries(enabledMc).filter(([,c])=>c.enabled !== false)
        .sort((a,b)=>(b[1].priority||0)-(a[1].priority||0));
      const currentModel = kgConfig.extraction_model || '';
      const modelOptionsKg = '<option value="">Auto (background-pick: cheapest local first)</option>'
        + enabledModelList.map(([mid,cfg])=>{
          const sel = mid === currentModel ? ' selected' : '';
          const localTag = cfg.is_local ? ' [local]' : '';
          return modelOption(mid, {selected: mid === currentModel, suffix: localTag});
        }).join('');

      const profileOpts = ['normative','generic'].map(p =>
        `<option value="${p}" ${p === (kgConfig.profile||'normative')?'selected':''}>${p}</option>`
      ).join('');

      const STAT = (val, label, color='var(--accent-brand)') => `<div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center;min-width:120px">
        <div style="font-size:22px;font-weight:600;color:${color}">${val}</div>
        <div style="font-size:11px;color:var(--text-400)">${label}</div>
      </div>`;

      const totalEntities = (stats.entities || 0).toLocaleString();
      const totalTriples = (stats.triples || 0).toLocaleString();
      const totalProjects = (stats.projects || []).length;

      // Per-project rows
      const projectRows = (stats.projects || []).map(p => {
        const topPred = (p.top_predicates || []).slice(0,5).map(pp =>
          `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:var(--text-300)">${esc(pp.predicate)} (${pp.count})</span>`
        ).join(' ');
        return `<div style="${ROW};cursor:pointer" onclick="kgOpenProject('${esc(p.agent_id)}','${esc(p.project)}')">
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;color:var(--text-100);font-weight:500">${esc(p.project)}</div>
            <div style="${MONO}">${esc(p.agent_id)} &middot; wing=${esc(p.wing)}</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;flex:2">${topPred}</div>
          <div style="text-align:right;min-width:160px">
            <div style="font-size:13px;color:var(--text-100)"><b>${(p.triples||0).toLocaleString()}</b> triples</div>
            <div style="${MONO}">${(p.entities||0).toLocaleString()} entities</div>
          </div>
        </div>`;
      }).join('') || `<div style="padding:14px;color:var(--text-400);font-size:12px">No KG content yet — drop documents into a project's input folder to start extraction.</div>`;

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          <span style="width:7px;height:7px;border-radius:50%;background:${kgConfig.enabled === false ? 'var(--error)' : 'var(--success)'};flex-shrink:0"></span>
          <span style="font-size:14px;font-weight:500;color:var(--text-100)">Knowledge Graph</span>
          <span style="${MONO}">${kgConfig.enabled === false ? 'disabled' : 'active'}</span>
          <span style="margin-left:auto;${MONO}">scope: ${esc((kgConfig.scopes||['projects']).join(','))}</span>
        </div>

        ${SEC('Overview')}
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          ${STAT(totalEntities, 'Entities')}
          ${STAT(totalTriples, 'Triples')}
          ${STAT(totalProjects, 'Projects with KG')}
          ${STAT(esc(kgConfig.profile || 'normative'), 'Profile', 'var(--text-200)')}
        </div>

        ${SEC('Extraction Settings')}
        <div style="${G('10px')};padding:12px;border:1px solid var(--border-100);border-radius:8px">
          <div style="display:grid;grid-template-columns:140px 1fr auto;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Enabled</label>
            <label style="display:inline-flex;gap:6px;font-size:12px"><input type="checkbox" id="kg-enabled" ${kgConfig.enabled===false?'':'checked'}> Run KG extraction during project sync</label>
            <span></span>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Extraction model</label>
            <select class="form-select" id="kg-model" ${isAdmin?'':'disabled'}>${modelOptionsKg}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px">Cloud models extract higher-quality triples; local models keep your documents on-prem. The selected model runs once per drawer during sync — pick frugally. <b>Tested default:</b> gemma-4-e4b-it-4bit (local, German-capable, runs alongside the chat warmpool).</div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Profile</label>
            <select class="form-select" id="kg-profile" ${isAdmin?'':'disabled'}>${profileOpts}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px"><b>normative</b>: policies, regulations, laws, specifications, contracts, SOPs &mdash; controlled predicates (requires/forbids/cites/...). <b>generic</b>: open predicates, any document type.</div>
          <div style="display:grid;grid-template-columns:140px 1fr 140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Max triples / drawer</label>
            <input type="number" class="form-input" id="kg-max-triples" min="1" max="50" value="${kgConfig.max_triples_per_drawer||12}" ${isAdmin?'':'disabled'}>
            <label style="font-size:12px;color:var(--text-300)">Min confidence</label>
            <input type="number" class="form-input" id="kg-min-conf" min="0" max="1" step="0.05" value="${kgConfig.min_confidence??0.5}" ${isAdmin?'':'disabled'}>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Max chars / drawer</label>
            <input type="number" class="form-input" id="kg-max-chars" min="500" max="20000" step="500" value="${kgConfig.max_drawer_chars||6000}" ${isAdmin?'':'disabled'}>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr 140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Regenerate closets</label>
            <label style="display:inline-flex;gap:6px;font-size:12px"><input type="checkbox" id="kg-regen-closets" ${kgConfig.regenerate_closets?'checked':''} ${isAdmin?'':'disabled'}> Re-rank drawer retrieval via LLM after each sync</label>
            <span></span><span></span>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px">Adds ~1 LLM call per source file per cycle. Boosts <code>mempalace_query</code> ranking by replacing MemPalace's regex closet generation with an LLM pass that captures implicit topics, foreign-language content, and contextual references. Reuses the extraction model selected above.</div>
          <div style="display:flex;gap:8px;justify-content:flex-end">
            <button class="btn-primary" id="kg-save-btn" onclick="saveKgConfig()" ${isAdmin?'':'disabled'}>${isAdmin?'Save Settings':'Admin only'}</button>
          </div>
        </div>

        ${SEC('Per-Project Knowledge Graphs')}
        <div style="${G('6px')}">${projectRows}</div>

        ${SEC('Documentation')}
        <div style="font-size:12px;color:var(--text-300);padding:10px 12px;background:var(--bg-100);border-radius:8px;line-height:1.5">
          The KG is built automatically by the project-sync daemon. Every drawer mined from a project's input folders is sent to the configured LLM for triple extraction. Triples are written to <code>${esc((window.__brain_palace_path||'~/.mempalace/brain'))}/knowledge_graph.sqlite3</code> with <code>source_file</code> and <code>source_drawer_id</code> provenance — so every claim links back to its origin.
          <br><br>
          Agent tools: <code>mempalace_kg_query(entity)</code>, <code>mempalace_kg_search(predicate)</code>, <code>mempalace_kg_neighbors(entity, depth)</code> — all auto-scoped to the calling project.
        </div>
      </div>`);
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--error)">Failed to load Knowledge Graph view: ${esc(e.message||e)}</div>`);
    }
  }

  /* ─── GDPR ─── */
  if (tab === 'gdpr') {
    try {
      const svc = await API.getServices();
      const srv = svc.server || {};
      const gs = srv.gdpr_scanner || {};
      applyGdprConfigToScanner(gs);
      const mcAll = state.modelsConfig?.models || {};
      const localOpts = Object.entries(mcAll)
        .filter(([id, cfg]) => cfg.enabled && (cfg.is_local === true))
        .sort((a, b) => (b[1].priority || 0) - (a[1].priority || 0))
        .map(([mid]) => modelOption(mid, {selected: mid===(gs.default_local_fallback_model||'')}))
        .join('');
      const hasLocals = localOpts.length > 0;

      // Build category list with rule memberships
      const catMembers = {};
      for (const [rid, cat] of Object.entries(PIIScanner.ruleCategories)) {
        (catMembers[cat] = catMembers[cat] || []).push(rid);
      }
      // Sort rules within each category alphabetically for stable layout
      for (const cat of Object.keys(catMembers)) catMembers[cat].sort();

      const ruleLabel = (rid) => {
        const r = PIIScanner.rules.find(x => x.id === rid);
        return r ? r.label : rid;
      };

      const ACT_DESC = {
        ignore: 'Do not flag this category.',
        warn:   'Show the confirmation modal before sending.',
        block:  'Refuse unless a local model is active (requires master block on).',
      };
      const ACT_COLORS = {
        ignore: 'var(--text-400)',
        warn:   '#b45309',
        block:  'var(--error)',
      };

      const actionSelect = (cat, current) => `
        <select class="form-select gdpr-cat-action" data-cat="${esc(cat)}" style="width:150px;font-size:12px">
          <option value="ignore" ${current==='ignore'?'selected':''}>Ignore</option>
          <option value="warn" ${current==='warn'?'selected':''}>Warn</option>
          <option value="block" ${current==='block'?'selected':''}>Block</option>
        </select>`;

      const policyCats = gs.categories || {};
      const policyOverrides = gs.rule_overrides || {};

      // Build per-category rule expander
      const catRows = Object.keys(PIIScanner.categoryLabels).map(cat => {
        const catCfg = policyCats[cat] || {};
        const catAction = catCfg.action || PIIScanner.defaultCategoryActions[cat] || 'warn';
        const rules = catMembers[cat] || [];
        const overrideCount = rules.filter(r => policyOverrides[r]).length;
        const ruleRows = rules.map(rid => {
          const ovr = policyOverrides[rid] || '';
          return `<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;border-bottom:1px solid var(--border-100)">
            <code style="font-size:10px;color:var(--text-400);min-width:180px">${esc(rid)}</code>
            <span style="flex:1;font-size:11px;color:var(--text-200)">${esc(ruleLabel(rid))}</span>
            <select class="form-select gdpr-rule-override" data-rule="${esc(rid)}" style="width:150px;font-size:11px">
              <option value="">Use category (${catAction})</option>
              <option value="ignore" ${ovr==='ignore'?'selected':''}>Ignore</option>
              <option value="warn" ${ovr==='warn'?'selected':''}>Warn</option>
              <option value="block" ${ovr==='block'?'selected':''}>Block</option>
            </select>
          </div>`;
        }).join('');
        return `<div style="border:1px solid var(--border-100);border-radius:8px;margin-bottom:6px;background:var(--bg-100)">
          <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer" onclick="const n=this.nextElementSibling;n.style.display=n.style.display==='none'?'block':'none';this.querySelector('.gdpr-cat-caret').textContent=n.style.display==='none'?'&#9656;':'&#9662;'">
            <span class="gdpr-cat-caret" style="color:var(--text-400);font-size:11px">&#9656;</span>
            <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(PIIScanner.categoryLabels[cat])}</span>
            <span style="font-size:10px;color:var(--text-400)">${rules.length} rule${rules.length===1?'':'s'}${overrideCount?` &middot; <b style="color:#b45309">${overrideCount} override${overrideCount===1?'':'s'}</b>`:''}</span>
            <span onclick="event.stopPropagation()">${actionSelect(cat, catAction)}</span>
          </div>
          <div style="display:none;border-top:1px solid var(--border-100);max-height:280px;overflow-y:auto">${ruleRows}</div>
        </div>`;
      }).join('');

      const allowlistText = (gs.email_allowlist || []).join('\n');

      C.innerHTML = P(`<div style="${G('12px')}">
        <div style="padding:12px 14px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          <div style="font-size:13px;color:var(--text-100);margin-bottom:6px"><b>How actions work</b></div>
          <div style="font-size:11px;color:var(--text-300);line-height:1.55">
            <b style="color:${ACT_COLORS.ignore}">Ignore</b>: rule is skipped entirely — no scan, no log.<br>
            <b style="color:${ACT_COLORS.warn}">Warn</b>: shows the amber confirmation modal before sending. User may dismiss and proceed.<br>
            <b style="color:${ACT_COLORS.block}">Block</b>: the send is refused unless the current model is local — the composer auto-routes to the fallback model. Requires the master <i>Block requests with PII</i> switch below; otherwise block actions are downgraded to warn.
          </div>
        </div>

        ${SEC('Master switches')}
        <div style="display:flex;flex-direction:column;gap:6px">
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-enabled" ${gs.enabled!==false?'checked':''}>
            <span><b>Enable scanner</b> — regex sweep of outgoing messages and text attachments</span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-serverlog" ${gs.server_log!==false?'checked':''}>
            <span><b>Server-side audit log</b> — record every detection in <code>audit.db</code></span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-block" ${gs.server_block?'checked':''}>
            <span><b>Block requests with PII</b> — honors category <i>block</i> actions. When off, block is downgraded to warn everywhere.</span>
          </label>
          <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
            <span style="font-size:12px;color:var(--text-300);min-width:200px">Default local fallback model</span>
            <select class="form-select" id="gdpr-fallback" style="flex:1" ${hasLocals?'':'disabled'}>
              <option value="">None (disabled)</option>
              ${localOpts}
            </select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-top:2px">Used for background LLM calls (next-prompt, chat summary, memory classifier, worker summariser, scheduled tasks) and for composer auto-routing when a blocking finding lands on a cloud model. ${hasLocals?'':'<span style="color:var(--warning,#b45309)">No local models are configured — add one under Models first.</span>'}</div>
        </div>

        ${SEC('Email allowlist')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:6px">
          One entry per line. <code>user@example.com</code> matches exactly; <code>@example.com</code> matches any address at that domain. Matching emails are suppressed from findings entirely.
        </div>
        <textarea id="gdpr-email-allowlist" rows="5" style="width:100%;font-family:var(--font-mono);font-size:12px;padding:8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000);color:var(--text-100);resize:vertical" placeholder="alexander@me.com&#10;@trusted-company.com">${esc(allowlistText)}</textarea>

        ${SEC('Category actions')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:6px">
          Pick one action per category. Expand to override individual rules. Category-level severity is the default; rule overrides win when set.
        </div>
        ${catRows}

        <div style="display:flex;gap:8px;margin-top:14px;padding-top:12px;border-top:1px solid var(--border-100)">
          <button class="btn-primary" id="gdpr-save-btn" onclick="saveGdprConfig()">Save all GDPR settings</button>
          <button class="btn-secondary" onclick="_confirmResetGdprCategories()">Reset categories to defaults</button>
        </div>
      </div>`);
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--error)">Failed to load GDPR settings: ${esc(e.message||e)}</div>`);
    }
  }

  /* ─── TOOLS ─── */
  if (tab === 'tools') {
    try {
      const [cfg, status] = await Promise.all([API.get('/v1/tools/config'), API.get('/v1/tools/status')]);
      const mc = state.modelsConfig?.models || {};
      // chat-capability dropdowns (refinement model, etc).
      const enabledModels = enabledModelsWithCapability('chat');
      const modelOpts = (sel) => enabledModels.map(([mid])=>modelOption(mid, {selected: mid===sel})).join('');
      // image-capability dropdown for read_document vision_model.
      const visionModelOpts = (sel) => enabledModelsWithCapability('image').map(([mid])=>modelOption(mid, {selected: mid===sel})).join('');

      const sBadge = (name) => {
        const s = status[name]?.status || 'not configured';
        const c = s==='configured'?'var(--success)':s==='disabled'?'var(--text-400)':'var(--error)';
        const i = s==='configured'?'\u2713':s==='disabled'?'\u2013':'\u2717';
        return `<span style="font-size:11px;color:${c};font-weight:500">${i} ${s.charAt(0).toUpperCase()+s.slice(1)}</span>`;
      };
      const tog = (name, label) => {
        const ck = cfg[name]?.enabled !== false ? 'checked' : '';
        return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:13px;font-weight:500;color:var(--text-100)">${label}</span>
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
            <input type="checkbox" id="tool-${name}-enabled" ${ck}><span style="font-size:11px;color:var(--text-400)">Enabled</span>
          </label>
        </div>`;
      };
      const maskF = (id, val) => `<div style="display:flex;gap:6px;align-items:center">
        <input id="${id}" type="password" value="${esc(val||'')}" class="form-input" style="flex:1;font-family:var(--font-mono);font-size:11px" autocomplete="off">
        <button class="btn-secondary" style="font-size:10px;padding:4px 8px" onclick="const i=document.getElementById('${id}');i.type=i.type==='password'?'text':'password';this.textContent=i.type==='password'?'Show':'Hide'">Show</button>
      </div>`;
      const lbl = (t) => `<div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:2px">${t}</div>`;

      const exaCfg = cfg.exa_search || {};
      const gmailCfg = cfg.gmail || {};
      const execCfg = cfg.execute_command || {};
      const wfCfg = cfg.web_fetch || {};
      const refCfg = cfg.refinement || {};
      const rdCfg = cfg.read_document || {};
      const cgCfg = cfg.code_graph || {};
      const taCfg = cfg.transcribe_audio || {};
      const ttsCfg = cfg.text_to_speech || {};
      const trCfg = cfg.translation || {};
      // Transcription / TTS model lists come from the models config — filtered
      // by canonical capability ('audio' for STT, 'tts' for text-to-speech).
      // The local-only fallback dropdown for STT additionally requires is_local
      // so GDPR routing stays on-prem.
      const _entriesForCap = (cap) => enabledModelsWithCapability(cap).map(([id, c]) => ({
        id,
        label: c.display_name || c.shortname || id,
        isLocal: !!c.is_local || c.provider === 'local-mlx-whisper',
      })).sort((a,b) => a.label.localeCompare(b.label));
      const _audioEntries = _entriesForCap('audio');
      const _ttsEntries = _entriesForCap('tts');
      const _capOptionList = (entries, sel, capLabel) => {
        if (!entries.length) {
          return `<option value="" disabled selected>No ${esc(capLabel)}-capable models — enable the '${esc(capLabel)}' capability on a model in the Models tab</option>`;
        }
        // Include the saved value as a stub option even if it no longer matches
        // a model entry (legacy id, model removed) so the user can see what's
        // there without it silently flipping to the first option.
        const ids = new Set(entries.map(e => e.id));
        let html = entries.map(m =>
          `<option value="${esc(m.id)}" ${m.id===sel?'selected':''}>${esc(m.label)}</option>`).join('');
        if (sel && !ids.has(sel)) {
          html = `<option value="${esc(sel)}" selected>${esc(sel)} (legacy/missing)</option>` + html;
        }
        return html;
      };
      const transcribeOpts = (sel) => _capOptionList(_audioEntries, sel, 'audio');
      const whisperOpts = (sel) => _capOptionList(_audioEntries.filter(m => m.isLocal), sel, 'audio');
      const ttsOpts = (sel) => _capOptionList(_ttsEntries, sel, 'tts');
      // Load TTS voices from the provider; fall back to empty while loading.
      let _ttsVoices = [];
      try { _ttsVoices = (await API.get('/v1/translate/tts/voices')).voices || []; } catch(_) {}
      const ttsVoiceOpts = (sel) => {
        if (!_ttsVoices.length) return `<option value="${esc(sel||'en_paul_neutral')}" selected>${esc(sel||'en_paul_neutral')}</option>`;
        return _ttsVoices.map(v =>
          `<option value="${esc(v.slug)}" ${v.slug===sel?'selected':''}>${esc(v.name)}${v.gender?' ('+v.gender+')':''}</option>`
        ).join('');
      };

      C.innerHTML = P(`<div style="${G('12px')}">
        <!-- Exa Search -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('exa_search','Exa Search')}
          <div style="${G('8px')}">
            ${lbl('API Key')}${maskF('tool-exa-key', exaCfg.api_key)}
            ${lbl('Default Results Per Query')}
            <input id="tool-exa-num" type="number" min="1" max="50" value="${exaCfg.default_num_results||5}" class="form-input" style="width:80px;font-family:var(--font-mono);font-size:11px">
            ${sBadge('exa_search')}
          </div>
        </div>

        <!-- Gmail -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('gmail','Gmail')}
          <div style="${G('8px')}">
            ${lbl('Email')}
            <input id="tool-gmail-email" type="email" value="${esc(gmailCfg.email||'')}" class="form-input" style="font-family:var(--font-mono);font-size:11px">
            ${lbl('App Password')}${maskF('tool-gmail-pass', gmailCfg.app_password)}
            <div style="display:flex;align-items:center;justify-content:space-between">
              ${sBadge('gmail')}
              <a href="https://myaccount.google.com/apppasswords" target="_blank" style="font-size:11px;color:var(--accent)">Create app password</a>
            </div>
          </div>
        </div>

        <!-- Execute Command -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('execute_command','Execute Command')}
          <div style="${G('8px')}">
            ${lbl('Default Timeout (seconds)')}
            <input id="tool-exec-timeout" type="number" min="1" max="600" value="${execCfg.timeout||120}" class="form-input" style="width:100px;font-family:var(--font-mono);font-size:11px">
            ${lbl('Banned Commands (comma-separated)')}
            <input id="tool-exec-banned" type="text" value="${esc((execCfg.banned_commands||[]).join(', '))}" class="form-input" style="font-family:var(--font-mono);font-size:11px">
            ${sBadge('execute_command')}
          </div>
        </div>

        <!-- Web Fetch -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('web_fetch','Web Fetch')}
          <div style="${G('8px')}">
            <div style="display:flex;gap:12px">
              <div>${lbl('Timeout (seconds)')}<input id="tool-wf-timeout" type="number" min="1" max="120" value="${wfCfg.timeout||30}" class="form-input" style="width:80px;font-family:var(--font-mono);font-size:11px"></div>
              <div>${lbl('Max Size (MB)')}<input id="tool-wf-maxsize" type="number" min="1" max="100" value="${wfCfg.max_size_mb||10}" class="form-input" style="width:80px;font-family:var(--font-mono);font-size:11px"></div>
            </div>
            ${sBadge('web_fetch')}
          </div>
        </div>

        <!-- Prompt Refinement -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('refinement','Prompt Refinement')}
          <div style="${G('8px')}">
            ${lbl('Model')}
            <select id="tool-refine-model" class="form-select" style="font-size:11px">
              <option value="">Auto (Haiku > Sonnet > cheapest)</option>
              ${modelOpts(refCfg.model)}
            </select>
            <div style="font-size:10px;color:var(--text-400)">Model used by the refine button in chat and note AI inputs.</div>
          </div>
        </div>

        <!-- Read Document -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('read_document','Read Document')}
          <div style="${G('8px')}">
            ${lbl('Max File Size (MB)')}
            <input id="tool-rdoc-maxsize" type="number" min="1" max="200" value="${rdCfg.max_file_size_mb||50}" class="form-input" style="width:100px;font-family:var(--font-mono);font-size:11px">
            ${lbl('Vision Model (for images)')}
            <select id="tool-rdoc-vision-model" class="form-select" style="font-size:11px">
              <option value="">Auto (cheapest vision model)</option>
              ${visionModelOpts(rdCfg.vision_model)}
            </select>
            <div style="font-size:10px;color:var(--text-400)">Lists every model in the Models tab whose Capabilities include <code>image</code>. Reads PDF, DOCX, XLSX, PPTX, CSV, images, SVG with format-aware parsing.</div>
          </div>
        </div>

        <!-- Transcribe Audio -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('transcribe_audio','Transcribe Audio')}
          <div style="${G('8px')}">
            ${lbl('Default Model')}
            <select id="tool-ta-default-model" class="form-select" style="font-size:11px">
              ${transcribeOpts(taCfg.default_model || '')}
            </select>
            ${lbl('GDPR Fallback Model (local only)')}
            <select id="tool-ta-fallback-model" class="form-select" style="font-size:11px">
              ${whisperOpts(taCfg.fallback_model || '')}
            </select>
            <div style="font-size:10px;color:var(--text-400)">Lists every model in the Models tab whose Capabilities include <code>audio</code>. The fallback dropdown is restricted to local entries — GDPR server-block routes audio there silently because voice can't be content-scanned. Cloud HTTP errors also fall back automatically.</div>
          </div>
        </div>

        <!-- Text-to-Speech -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('text_to_speech','Text-to-Speech')}
          <div style="${G('8px')}">
            ${lbl('TTS Model')}
            <select id="tool-tts-model" class="form-select" style="font-size:11px">
              ${ttsOpts(ttsCfg.default_model || '')}
            </select>
            ${lbl('Voice')}
            <select id="tool-tts-voice" class="form-select" style="font-size:11px">
              ${ttsVoiceOpts(ttsCfg.voice || 'en_paul_neutral')}
            </select>
            <div style="font-size:10px;color:var(--text-400)">Used by the speaker buttons in the Translation text tab. Voice names follow the OpenAI /audio/speech convention — not all voices may be available depending on the model.</div>
          </div>
        </div>

        <!-- Translation -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('translation','Translation')}
          <div style="${G('8px')}">
            ${lbl('Translation Model')}
            <select id="tool-tr-model" class="form-select" style="font-size:11px">
              <option value="">Auto (refinement model → fallback)</option>
              ${modelOpts(trCfg.default_model || '')}
            </select>
            <div style="font-size:10px;color:var(--text-400)">Chat-capable LLM used to translate text, documents, and audio/video segments. Separate from the transcription model (Voxtral/Whisper) and TTS.</div>
          </div>
        </div>

        <!-- Write Document -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('write_document','Write Document')}
          <div style="font-size:10px;color:var(--text-400)">Creates DOCX, XLSX, PPTX, PDF from markdown content.</div>
        </div>

        <!-- Edit Document -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('edit_document','Edit Document')}
          <div style="font-size:10px;color:var(--text-400)">Targeted edits: DOCX replace text, XLSX update cells, PPTX update slides.</div>
        </div>

        <!-- Code Graph -->
        <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
          ${tog('code_graph','Code Graph')}
          <div style="${G('8px')}">
            ${lbl('Exclude Directories (comma-separated)')}
            <input id="tool-cg-exclude" type="text" value="${esc(cgCfg.exclude_dirs||'node_modules,.git,__pycache__,venv')}" class="form-input" style="font-family:var(--font-mono);font-size:11px">
            ${lbl('Max File Size (KB)')}
            <input id="tool-cg-maxsize" type="number" min="50" max="5000" value="${cgCfg.max_file_size_kb||500}" class="form-input" style="width:100px;font-family:var(--font-mono);font-size:11px">
            <div style="font-size:10px;color:var(--text-400)">AST-based code structure graph. 14 languages via Tree-sitter.</div>
          </div>
        </div>

        <div style="display:flex;justify-content:flex-end;margin-top:4px">
          <button class="btn-primary" onclick="saveToolsConfig()" style="padding:8px 20px;font-size:13px">Save Tools Config</button>
        </div>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--error)">Failed to load tools config</div>'); }
  }
}

/* ── General Settings Helpers ── */
async function saveToolsConfig() {
  const bannedRaw = document.getElementById('tool-exec-banned')?.value || '';
  const banned = bannedRaw.split(',').map(s => s.trim()).filter(Boolean);
  const cfg = {
    exa_search: {
      enabled: document.getElementById('tool-exa_search-enabled')?.checked ?? true,
      api_key: document.getElementById('tool-exa-key')?.value || '',
      default_num_results: parseInt(document.getElementById('tool-exa-num')?.value) || 5,
    },
    gmail: {
      enabled: document.getElementById('tool-gmail-enabled')?.checked ?? true,
      email: document.getElementById('tool-gmail-email')?.value || '',
      app_password: document.getElementById('tool-gmail-pass')?.value || '',
    },
    execute_command: {
      enabled: document.getElementById('tool-execute_command-enabled')?.checked ?? true,
      timeout: parseInt(document.getElementById('tool-exec-timeout')?.value) || 120,
      banned_commands: banned,
    },
    web_fetch: {
      enabled: document.getElementById('tool-web_fetch-enabled')?.checked ?? true,
      timeout: parseInt(document.getElementById('tool-wf-timeout')?.value) || 30,
      max_size_mb: parseInt(document.getElementById('tool-wf-maxsize')?.value) || 10,
    },
    refinement: {
      enabled: document.getElementById('tool-refinement-enabled')?.checked ?? true,
      model: document.getElementById('tool-refine-model')?.value || '',
    },
    read_document: {
      enabled: document.getElementById('tool-read_document-enabled')?.checked ?? true,
      max_file_size_mb: parseInt(document.getElementById('tool-rdoc-maxsize')?.value) || 50,
      vision_model: document.getElementById('tool-rdoc-vision-model')?.value || '',
    },
    write_document: {
      enabled: document.getElementById('tool-write_document-enabled')?.checked ?? true,
    },
    edit_document: {
      enabled: document.getElementById('tool-edit_document-enabled')?.checked ?? true,
    },
    code_graph: {
      enabled: document.getElementById('tool-code_graph-enabled')?.checked ?? true,
      exclude_dirs: document.getElementById('tool-cg-exclude')?.value || '',
      max_file_size_kb: parseInt(document.getElementById('tool-cg-maxsize')?.value) || 500,
    },
    transcribe_audio: {
      enabled: document.getElementById('tool-transcribe_audio-enabled')?.checked ?? true,
      default_model: document.getElementById('tool-ta-default-model')?.value || 'mistral-experimental/voxtral-mini-latest',
      fallback_model: document.getElementById('tool-ta-fallback-model')?.value || 'whisper-base',
    },
    text_to_speech: {
      enabled: document.getElementById('tool-text_to_speech-enabled')?.checked ?? true,
      default_model: document.getElementById('tool-tts-model')?.value || 'mistral-experimental/voxtral-mini-tts-latest',
      voice: document.getElementById('tool-tts-voice')?.value || 'en_paul_neutral',
    },
    translation: {
      enabled: document.getElementById('tool-translation-enabled')?.checked ?? true,
      default_model: document.getElementById('tool-tr-model')?.value || '',
    },
  };
  try {
    await API.post('/v1/tools/config', cfg);
    showToast('Tools configuration saved');
    switchGeneralTab('tools');
  } catch(e) { showToast('Failed to save: ' + e.message, true); }
}

async function saveMpClassifier() {
  try {
    const cats = [...document.querySelectorAll('.mp-clf-cat:checked')].map(c => c.value);
    await API.post('/v1/mempalace/classifier', {
      enabled: document.getElementById('mp-clf-enabled')?.checked ?? false,
      model: document.getElementById('mp-clf-model')?.value || '',
      min_turns: parseInt(document.getElementById('mp-clf-min-turns')?.value) || 0,
      default_mode: parseInt(document.getElementById('mp-clf-default-mode')?.value) || 0,
      categories_to_file: cats,
    });
    // Refresh cached classifier config
    state.mempalaceClassifier = await API.get('/v1/mempalace/classifier').catch(() => ({}));
    showToast('Classifier config saved');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

function _kgShowInfo(title, htmlBody) {
  const modalId = '__kgInfoModal';
  let m = document.getElementById(modalId);
  if (m) m.remove();
  m = document.createElement('div');
  m.id = modalId;
  m.className = 'modal-overlay';
  m.style.zIndex = '12001';
  m.innerHTML = `<div class="modal-content" style="max-width:780px;width:80vw;max-height:80vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">${esc(title)}</span>
      <button class="btn-secondary" style="margin-left:auto;font-size:11px;padding:4px 10px" onclick="document.getElementById('${modalId}').remove()">Close</button>
    </div>
    <div class="modal-body" style="overflow:auto;flex:1;padding:14px">${htmlBody}</div>
  </div>`;
  document.body.appendChild(m);
  m.addEventListener('click', (ev) => { if (ev.target === m) m.remove(); });
}

async function saveKgConfig() {
  try {
    const body = {
      enabled: document.getElementById('kg-enabled')?.checked ?? true,
      extraction_model: document.getElementById('kg-model')?.value || '',
      profile: document.getElementById('kg-profile')?.value || 'normative',
      max_triples_per_drawer: parseInt(document.getElementById('kg-max-triples')?.value) || 12,
      min_confidence: parseFloat(document.getElementById('kg-min-conf')?.value) || 0.5,
      max_drawer_chars: parseInt(document.getElementById('kg-max-chars')?.value) || 6000,
      regenerate_closets: document.getElementById('kg-regen-closets')?.checked ?? false,
    };
    await API.post('/v1/mempalace/kg/config', body);
    showToast('Knowledge Graph settings saved');
    switchGeneralTab('knowledge-graph');
  } catch(e) { showToast('Save failed: ' + (e.message || e), true); }
}

async function kgOpenProject(agentId, projectName) {
  // Modal with per-project drilldown: stats, top predicates, top entities,
  // sample triples, recent extraction-log rows. Admin gets a Re-extract
  // button.
  const modalId = '__kgProjectModal';
  let m = document.getElementById(modalId);
  if (m) m.remove();
  m = document.createElement('div');
  m.id = modalId;
  m.className = 'modal-overlay';
  m.style.zIndex = '12000';
  m.innerHTML = `<div class="modal-content" style="max-width:980px;width:90vw;max-height:90vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">Knowledge Graph &mdash; ${esc(projectName)}</span>
      <span style="font-size:11px;color:var(--text-400);font-family:var(--font-mono)">${esc(agentId)}</span>
      <button class="btn-secondary" style="margin-left:auto;font-size:11px;padding:4px 10px" onclick="document.getElementById('${modalId}').remove()">Close</button>
    </div>
    <div class="modal-body" id="kg-project-body" style="overflow:auto;flex:1;padding:16px">Loading…</div>
  </div>`;
  document.body.appendChild(m);
  m.addEventListener('click', (ev) => { if (ev.target === m) m.remove(); });

  try {
    const data = await API.get(`/v1/mempalace/kg/wing?agent_id=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}`);
    const isAdmin = state.authUser && state.authUser.role === 'admin';
    const body = document.getElementById('kg-project-body');
    if (!body) return;

    const STAT = (val, label) => `<div style="padding:10px 16px;background:var(--bg-200);border-radius:8px;text-align:center;min-width:110px">
      <div style="font-size:20px;font-weight:600;color:var(--accent-brand)">${val}</div>
      <div style="font-size:11px;color:var(--text-400)">${label}</div>
    </div>`;

    // Predicate frequency bar — biggest at left
    const maxPredCount = Math.max(1, ...(data.top_predicates||[]).map(p => p.count));
    const predBars = (data.top_predicates||[]).map(p => {
      const w = Math.max(8, Math.round(p.count / maxPredCount * 100));
      return `<div style="display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer" onclick="kgSearchPredicate('${esc(agentId)}','${esc(projectName)}','${esc(p.predicate)}')">
        <span style="font-family:var(--font-mono);min-width:160px;color:var(--text-200)">${esc(p.predicate)}</span>
        <div style="flex:1;height:14px;background:var(--bg-200);border-radius:3px;position:relative;overflow:hidden">
          <div style="height:100%;width:${w}%;background:var(--accent-brand);opacity:0.7"></div>
        </div>
        <span style="min-width:40px;text-align:right;color:var(--text-300);font-family:var(--font-mono)">${p.count}</span>
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No triples yet.</div>';

    // Top entities
    const entRows = (data.top_entities||[]).slice(0,15).map(e =>
      `<div style="display:flex;gap:8px;align-items:center;font-size:12px;padding:4px 0;cursor:pointer" onclick="kgQueryEntity('${esc(agentId)}','${esc(projectName)}','${esc(e.name||'').replace(/'/g,"\\'")}')">
        <span style="flex:1;color:var(--text-100);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.name||e.id)}</span>
        <span style="font-size:10px;padding:1px 5px;background:var(--bg-300);border-radius:3px;color:var(--text-400)">${esc(e.type||'unknown')}</span>
        <span style="min-width:30px;text-align:right;color:var(--text-300);font-family:var(--font-mono)">${e.degree}</span>
      </div>`
    ).join('') || '<div style="font-size:12px;color:var(--text-400)">No entities.</div>';

    // Sample triples
    const sampleRows = (data.sample_triples||[]).map(t => {
      const sf = (t.source_file||'').split('/').slice(-2).join('/');
      const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
      return `<div style="padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;margin-bottom:6px;font-size:12px">
        <div style="display:flex;gap:8px;align-items:flex-start">
          <span style="color:var(--text-100);flex:1">(${esc(t.subject)})</span>
          <span style="font-family:var(--font-mono);color:var(--accent-brand)">--[${esc(t.predicate)}]--&gt;</span>
          <span style="color:var(--text-100);flex:1">(${esc(t.object)})</span>
          ${conf?`<span style="font-family:var(--font-mono);color:var(--text-400)">c=${conf}</span>`:''}
        </div>
        <div style="font-family:var(--font-mono);font-size:10px;color:var(--text-400);margin-top:3px">${esc(sf)}</div>
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No triples extracted yet.</div>';

    // Extraction log
    const logRows = (data.extraction_log||[]).slice(0,15).map(r => {
      const dt = r.started_at ? new Date(r.started_at*1000).toLocaleString() : '?';
      const ok = !r.error_msg && (r.errors||0) === 0;
      const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${ok?'var(--success)':'var(--error)'};margin-right:6px"></span>`;
      return `<div style="font-size:11px;padding:4px 0;border-bottom:1px dotted var(--border-100)">
        ${dot}<span style="font-family:var(--font-mono);color:var(--text-300)">${esc(dt)}</span>
        seen=${r.drawers_seen||0} new=${r.drawers_processed||0} skip=${r.drawers_skipped||0}
        triples=<b>${r.triples_extracted||0}</b> errors=${r.errors||0}
        ${r.error_msg?`<span style="color:var(--error)" title="${esc(r.error_msg)}"> · ${esc((r.error_msg||'').slice(0,80))}</span>`:''}
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No runs yet.</div>';

    body.innerHTML = `
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
        ${STAT((data.entities||0).toLocaleString(),'Entities')}
        ${STAT((data.triples||0).toLocaleString(),'Relations')}
        ${STAT((data.top_predicates||[]).length,'Predicates seen')}
        ${STAT((data.extraction_log||[]).length,'Runs logged')}
      </div>
      ${isAdmin?`<div style="display:flex;gap:8px;justify-content:flex-end;margin-bottom:14px">
        <button class="btn-secondary" onclick="kgReextract('${esc(agentId)}','${esc(projectName)}')">Re-extract everything</button>
      </div>`:''}

      <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:8px 0 6px">Predicate frequency</div>
      <div style="display:grid;gap:4px">${predBars}</div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:18px">
        <div>
          <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Top entities by degree</div>
          ${entRows}
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Recent extraction runs</div>
          ${logRows}
        </div>
      </div>

      <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:18px 0 6px">Sample triples (highest confidence)</div>
      <div>${sampleRows}</div>
    `;
  } catch(e) {
    const body = document.getElementById('kg-project-body');
    if (body) body.innerHTML = `<div style="color:var(--error)">Failed to load: ${esc(e.message||e)}</div>`;
  }
}

async function kgQueryEntity(agentId, projectName, entityName) {
  try {
    const data = await API.get(`/v1/mempalace/kg/entity?agent_id=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}&name=${encodeURIComponent(entityName)}`);
    const triples = (data.triples||[]).map(t => {
      const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
      return `<div style="padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;margin-bottom:6px;font-size:12px">
        <div style="display:flex;gap:8px;align-items:flex-start">
          <span style="color:var(--text-100);flex:1">(${esc(t.subject||'')})</span>
          <span style="font-family:var(--font-mono);color:var(--accent-brand)">--[${esc(t.predicate||'')}]--&gt;</span>
          <span style="color:var(--text-100);flex:1">(${esc(t.object||'')})</span>
          ${conf?`<span style="font-family:var(--font-mono);color:var(--text-400)">c=${conf}</span>`:''}
        </div>
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No triples for this entity.</div>';
    _kgShowInfo(`Entity: ${entityName}`, `<div style="font-size:11px;color:var(--text-400);margin-bottom:8px">${data.count} triples (${data.total_in_kg} in KG before scope filter)</div>${triples}`);
  } catch(e) { showToast('Lookup failed: ' + (e.message||e), true); }
}

async function kgSearchPredicate(agentId, projectName, predicate) {
  try {
    const data = await API.get(`/v1/mempalace/kg/wing?agent_id=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}`);
    const filtered = (data.sample_triples||[]).filter(t => t.predicate === predicate);
    const list = filtered.map(t => {
      const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
      return `<div style="padding:6px 0;font-size:12px;border-bottom:1px dotted var(--border-100)">
        (${esc(t.subject||'')}) <span style="font-family:var(--font-mono);color:var(--accent-brand)">[${esc(predicate)}]</span> (${esc(t.object||'')}) ${conf?`<span style="font-family:var(--font-mono);color:var(--text-400);margin-left:6px">c=${conf}</span>`:''}
      </div>`;
    }).join('') || `<div style="font-size:12px;color:var(--text-400)">No "${esc(predicate)}" triples in the sample (full set may have more — ${(data.top_predicates||[]).find(p=>p.predicate===predicate)?.count||0} total).</div>`;
    _kgShowInfo(`Triples with predicate: ${predicate}`, list);
  } catch(e) { showToast('Search failed: ' + (e.message||e), true); }
}

async function kgReextract(agentId, projectName) {
  if (!await showConfirmDanger(`Purge all triples for "${projectName}" and re-extract from scratch?\n\nThis deletes existing triples + the extraction cursor, then queues the project for the next sync cycle (within 30 min, or trigger Sync now from the project panel).`, 'Re-extract KG', 'Purge & Re-extract')) return;
  try {
    const res = await API.post('/v1/mempalace/kg/reextract', {agent_id: agentId, project: projectName});
    showToast(`Purged ${res.triples_deleted||0} triples · queued for re-extraction`);
    setTimeout(()=>kgOpenProject(agentId, projectName), 500);
  } catch(e) { showToast('Re-extract failed: ' + (e.message||e), true); }
}

async function saveContextConfig() {
  try {
    await API.post('/v1/context/config', {
      enabled: document.getElementById('ctx-enabled')?.checked ?? true,
      fresh_tail_count: parseInt(document.getElementById('ctx-fresh-tail')?.value) || 16,
      compact_threshold: (parseInt(document.getElementById('ctx-threshold')?.value) || 60) / 100,
      messages_per_summary: parseInt(document.getElementById('ctx-msgs-per-sum')?.value) || 10,
      condense_threshold: parseInt(document.getElementById('ctx-condense')?.value) || 4,
      max_depth: parseInt(document.getElementById('ctx-max-depth')?.value) || 5,
      summary_target_tokens: parseInt(document.getElementById('ctx-target-tokens')?.value) || 1000,
      summary_model: document.getElementById('ctx-summary-model')?.value || '',
    });
    showToast('Context config saved');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function saveModelsConfig() {
  try {
    const mc = {...(state.modelsConfig?.models || {})};
    const readNum = (row, cls) => { const v = row.querySelector(`.${cls}`)?.value?.trim(); return v === '' || v == null ? undefined : Number(v); };
    const readStr = (row, cls) => { const v = row.querySelector(`.${cls}`)?.value?.trim(); return v || undefined; };
    document.querySelectorAll('[data-model-id]').forEach(row => {
      const mid = row.dataset.modelId;
      if (!mc[mid]) return;
      mc[mid].enabled = row.querySelector('.mdl-enabled')?.checked ?? mc[mid].enabled;
      const pVal = row.querySelector('.mdl-priority')?.value;
      if (pVal !== undefined) mc[mid].priority = parseInt(pVal) || 0;
      const dName = row.querySelector('.mdl-display-name')?.value?.trim();
      if (dName) mc[mid].display_name = dName;
      const desc = row.querySelector('.mdl-description')?.value?.trim();
      if (desc) mc[mid].description = desc; else delete mc[mid].description;
      // Profile (speed/balanced/frugal/custom)
      const profile = row.querySelector('.mdl-profile')?.value;
      if (profile && profile !== 'custom') mc[mid].profile = profile;
      else delete mc[mid].profile;
      // Context & output
      const maxCtx = readNum(row, 'mdl-max-context');
      if (maxCtx !== undefined) mc[mid].max_context = maxCtx; else delete mc[mid].max_context;
      const maxOut = readNum(row, 'mdl-max-output');
      if (maxOut !== undefined) mc[mid].max_output = maxOut; else delete mc[mid].max_output;
      // Cost
      const ci = readNum(row, 'mdl-cost-input');
      if (ci !== undefined) mc[mid].cost_input = ci; else delete mc[mid].cost_input;
      const co = readNum(row, 'mdl-cost-output');
      if (co !== undefined) mc[mid].cost_output = co; else delete mc[mid].cost_output;
      // Inference params — collect non-empty values
      const inf = {};
      const infKeys = ['temperature','top_p','top_k','max_tokens','frequency_penalty','presence_penalty','min_p','repetition_penalty','thinking_budget'];
      for (const k of infKeys) {
        const v = readNum(row, `mdl-inf-${k}`);
        if (v !== undefined && !isNaN(v)) inf[k] = v;
      }
      // String params (reasoning_effort)
      const re = readStr(row, 'mdl-inf-reasoning_effort');
      if (re) inf.reasoning_effort = re;
      // Thinking level: '' = unset (use API default for the format), explicit
      // 'none'/'low'/'medium'/'high' wins. Skip when format='none' (column is
      // disabled in that case anyway).
      const tlSel = row.querySelector('.mdl-thinking-level');
      const tl = tlSel && !tlSel.disabled ? (tlSel.value || '') : '';
      if (tl) inf.thinking_level = tl;
      mc[mid].inference = Object.keys(inf).length > 0 ? inf : undefined;
      if (!mc[mid].inference) delete mc[mid].inference;
      // Caveman system level (per-model system prompt compression)
      const cavSys = readNum(row, 'mdl-caveman-system');
      if (cavSys) mc[mid].caveman_system = cavSys; else delete mc[mid].caveman_system;
      // Parallel tool calls
      const ptc = row.querySelector('.mdl-parallel-tools')?.checked;
      if (ptc === false) mc[mid].parallel_tool_calls = false; else delete mc[mid].parallel_tool_calls;
      // Warmup. Persist explicit false so the speed profile's warmup=true
      // overlay can't silently re-enable warmup the user just turned off.
      const warm = row.querySelector('.mdl-warmup')?.checked;
      mc[mid].warmup = !!warm;
      // warmup_ttl_seconds retired: hold-forever semantics (no periodic reprime)
      delete mc[mid].warmup_ttl_seconds;
      const warmMode = row.querySelector('.mdl-warmup-mode')?.value;
      if (warmMode && warmMode !== 'full') mc[mid].warmup_mode = warmMode;
      else delete mc[mid].warmup_mode;
      const warmCloud = row.querySelector('.mdl-warmup-allow-cloud')?.checked;
      if (warmCloud) mc[mid].warmup_allow_cloud = true; else delete mc[mid].warmup_allow_cloud;
      // Raw formats
      const rawFmt = row.querySelector('.mdl-raw-formats')?.value?.trim();
      if (rawFmt) {
        mc[mid].raw_formats = rawFmt.split(',').map(s => s.trim()).filter(Boolean);
      } else {
        mc[mid].raw_formats = [];
      }
      // Capabilities — canonical {chat, image, audio, tts, video} checkboxes.
      // Order is preserved to keep the saved config diff-stable.
      const _capOrder = ['chat','image','audio','tts','video'];
      const _checkedCaps = new Set(
        Array.from(row.querySelectorAll('.mdl-cap-cb'))
          .filter(cb => cb.checked).map(cb => cb.dataset.cap)
      );
      mc[mid].capabilities = _capOrder.filter(c => _checkedCaps.has(c));
      mc[mid]._caps_canonical = true;
      // Thinking format
      const tfmt = row.querySelector('.mdl-thinking-format')?.value;
      if (tfmt && tfmt !== 'none') mc[mid].thinking_format = tfmt;
      else mc[mid].thinking_format = 'none';
    });
    await API.post('/v1/models/config', { action: 'save', models: mc });
    state.modelsConfig.models = mc;
    showToast('Models config saved');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function addManualModel() {
  const id = document.getElementById('add-model-id')?.value?.trim();
  const provider = document.getElementById('add-model-provider')?.value?.trim();
  const display = document.getElementById('add-model-display')?.value?.trim();
  if (!id) { showToast('Model ID required', true); return; }
  if (!provider) { showToast('Provider required', true); return; }
  const mc = state.modelsConfig?.models || {};
  if (mc[id]) { showToast('Model already exists', true); return; }
  mc[id] = { enabled: true, provider, display_name: display || id, shortname: id, priority: 10, capabilities: [], icon: '\u{1F916}', manual: true };
  state.modelsConfig.models = mc;
  try {
    await API.post('/v1/models/config', { action: 'save', models: mc });
    showToast('Model added');
    switchGeneralTab('models');
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function removeModel(mid) {
  // Tombstoned delete — server records the id in config.deleted_models so
  // it isn't re-added by startup discovery or sync. Re-add via manual add
  // (or resync_provider on the owning provider) clears the tombstone.
  const mc = {...(state.modelsConfig?.models || {})};
  delete mc[mid];
  state.modelsConfig.models = mc;
  try {
    await API.post('/v1/models/config', { action: 'delete', model_id: mid });
    showToast('Model removed');
    switchGeneralTab('models');
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _confirmRemoveModel(mid) {
  if (!await showConfirmDanger(`Remove model ${mid}?`, 'Remove Model', 'Remove')) return;
  removeModel(mid);
}

async function resyncProvider(btn, name) {
  if (!await showConfirmDanger(`Full resync of "${name}"? This deletes ALL its models AND their deletion tombstones, then re-discovers from the provider's /models endpoint.`, 'Full Resync', 'Resync')) return;
  btn.disabled = true; const orig = btn.textContent; btn.textContent = 'Resyncing...';
  try {
    await API.post('/v1/models/config', { action: 'resync_provider', provider: name });
    const d = await API.getModelsConfig();
    state.modelsConfig = d;
    switchGeneralTab('providers');
    showToast(`${name} fully resynced`);
  } catch(e) {
    showToast('Resync failed: ' + e.message, true);
    btn.disabled = false; btn.textContent = orig;
  }
}

async function syncProvider(btn, name) {
  btn.disabled = true; btn.textContent = 'Syncing...';
  try {
    await API.post('/v1/models/config', { action: 'sync', provider: name });
    showToast(`Syncing ${name}...`);
    setTimeout(async () => {
      const d = await API.getModelsConfig();
      state.modelsConfig = d;
      switchGeneralTab('providers');
      showToast(`${name} synced`);
    }, 3000);
  } catch(e) { showToast('Sync failed', true); btn.disabled = false; btn.textContent = 'Sync'; }
}

async function saveProviderEdit(name, pid) {
  const base_url = document.getElementById(`${pid}-url`).value;
  const default_model = document.getElementById(`${pid}-model`).value;
  const provider = { base_url, default_model, _keep_key: true };
  try {
    await API.post('/v1/providers', { action: 'add', name, provider });
    showToast('Provider updated');
    switchGeneralTab('providers');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function openProviderKeysModal(provName) {
  document.getElementById('_prov-keys-modal')?.remove();
  const overlay = document.createElement('div');
  overlay.id = '_prov-keys-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:640px;padding:20px;display:grid;gap:12px">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:15px;font-weight:600;color:var(--text-000)">API Keys — ${esc(provName)}</span>
      <span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">preferred → round-robin → fallback</span>
      <button class="modal-close" style="margin-left:auto" onclick="document.getElementById('_prov-keys-modal')?.remove()">&times;</button>
    </div>
    <div id="_prov-keys-list" style="display:grid;gap:6px"><span style="color:var(--text-400);font-size:12px">Loading…</span></div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" style="font-size:12px" onclick="openKeyEditModal('${esc(provName)}','')">+ Add key</button>
      <button class="btn-secondary" style="font-size:12px;margin-left:auto" onclick="openProviderStatsModal('${esc(provName)}')">View stats →</button>
    </div>
  </div>`;
  overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  await _renderProviderKeysList(provName);
}

async function _renderProviderKeysList(provName) {
  const target = document.getElementById('_prov-keys-list');
  if (!target) return;
  const USAGE_LABELS = {preferred:'Preferred (prio 1)',round_robin:'Round-robin (prio 2)',fallback:'Fallback (prio 3)'};
  const USAGE_COLORS = {preferred:'var(--accent)',round_robin:'var(--text-200)',fallback:'var(--text-400)'};
  try {
    const provs = await API.getProviders();
    const providers = Array.isArray(provs) ? provs : (provs.providers || []);
    state.providers = providers;
    const prov = providers.find(p => p.name === provName);
    const keys = prov?.api_keys || [];
    if (!keys.length) {
      target.innerHTML = `<span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">No keys yet — click + Add key</span>`;
      return;
    }
    target.innerHTML = keys.map(k => {
      const usageColor = USAGE_COLORS[k.usage]||'var(--text-200)';
      const deadlinePart = k.deadline ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">exp ${esc(k.deadline.slice(0,10))}</span>` : '';
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:var(--bg-100);border-radius:6px;flex-wrap:wrap">
        <span style="font-size:13px;font-weight:500;color:var(--text-100);min-width:100px">${esc(k.name)}</span>
        <span style="font-family:var(--font-mono);color:${usageColor};font-size:11px">${esc(USAGE_LABELS[k.usage]||k.usage)}</span>
        <span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">${esc(k.key_hint||'')}</span>
        ${deadlinePart}
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px;margin-left:auto" onclick="openKeyEditModal('${esc(provName)}','${esc(k.name)}')">Edit</button>
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="deleteProviderKey('${esc(provName)}','${esc(k.name)}')">Delete</button>
      </div>`;
    }).join('');
  } catch(e) {
    target.innerHTML = `<span style="color:var(--error);font-size:12px">Failed to load: ${esc(e.message||'error')}</span>`;
  }
}

async function openProviderStatsModal(provName, days) {
  document.getElementById('_prov-stats-modal')?.remove();
  const d = Number(days) || 30;
  const overlay = document.createElement('div');
  overlay.id = '_prov-stats-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:760px;padding:20px;display:grid;gap:12px">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:15px;font-weight:600;color:var(--text-000)">Usage Stats — ${esc(provName)}</span>
      <select class="form-select" id="_prov-stats-days" style="width:auto;font-size:12px;padding:2px 8px;margin-left:8px"
        onchange="openProviderStatsModal('${esc(provName)}', this.value)">
        <option value="7"${d===7?' selected':''}>Last 7 days</option>
        <option value="30"${d===30?' selected':''}>Last 30 days</option>
        <option value="90"${d===90?' selected':''}>Last 90 days</option>
        <option value="365"${d===365?' selected':''}>Last 365 days</option>
      </select>
      <button class="modal-close" style="margin-left:auto" onclick="document.getElementById('_prov-stats-modal')?.remove()">&times;</button>
    </div>
    <div id="_prov-stats-body"><span style="color:var(--text-400);font-size:12px">Loading…</span></div>
  </div>`;
  overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const fmtNum = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n||0);
  const fmtCost = c => (c||0) > 0 ? '$'+(c).toFixed(4) : '—';
  try {
    const resp = await API.get(`/v1/providers/stats?days=${d}`);
    const all = resp.stats || [];
    const ps = all.find(s => s.provider === provName);
    const body = document.getElementById('_prov-stats-body');
    if (!body) return;
    if (!ps) {
      body.innerHTML = `<span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">No usage in selected window.</span>`;
      return;
    }
    const totals = `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:10px;background:var(--bg-100);border-radius:8px">
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Calls</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.calls)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens in</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.tokens_in)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens out</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.tokens_out)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Cost</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtCost(ps.cost_usd)}</div></div>
    </div>`;
    const keys = (ps.keys || []).slice().sort((a,b)=>b.calls-a.calls);
    const headerRow = `<div style="display:grid;grid-template-columns:1.4fr repeat(5, 1fr);gap:8px;padding:6px 8px;font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid var(--border-100)">
      <div>Key</div><div style="text-align:right">Calls</div><div style="text-align:right">Tokens in</div><div style="text-align:right">Tokens out</div><div style="text-align:right">Cost</div><div style="text-align:right">Last used</div>
    </div>`;
    const rows = keys.length ? keys.map(k => `<div style="display:grid;grid-template-columns:1.4fr repeat(5, 1fr);gap:8px;padding:6px 8px;font-size:12px;border-bottom:1px solid var(--border-100)">
      <div style="color:var(--text-100);font-weight:500;overflow:hidden;text-overflow:ellipsis">${esc(k.key_name||'(unknown)')}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-100)">${fmtNum(k.calls)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-200)">${fmtNum(k.tokens_in)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-200)">${fmtNum(k.tokens_out)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-100)">${fmtCost(k.cost_usd)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-400);font-size:10px">${esc((k.last_used||'').slice(0,16).replace('T',' '))}</div>
    </div>`).join('') : `<div style="padding:8px;font-family:var(--font-mono);color:var(--text-400);font-size:11px">No per-key breakdown available.</div>`;
    body.innerHTML = `${totals}
      <div style="margin-top:8px">
        <div style="font-size:11px;font-weight:500;color:var(--text-200);margin-bottom:4px">Per API key</div>
        ${headerRow}${rows}
      </div>`;
  } catch(e) {
    const body = document.getElementById('_prov-stats-body');
    if (body) body.innerHTML = `<span style="color:var(--error);font-size:12px">Failed to load stats: ${esc(e.message||'error')}</span>`;
  }
}

function openKeyEditModal(provName, keyName) {
  // Remove any existing key modal
  document.getElementById('_key-edit-modal')?.remove();
  const isEdit = !!keyName;
  const overlay = document.createElement('div');
  overlay.id = '_key-edit-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:420px;padding:20px;display:grid;gap:12px">
    <div style="font-size:15px;font-weight:600;color:var(--text-000)">${isEdit?'Edit API Key':'Add API Key'}</div>
    <div><label class="form-label">Key name</label><input class="form-input" id="_kmod-name" placeholder="e.g. main, backup-1" value="${isEdit?esc(keyName):''}"></div>
    <div><label class="form-label">API key value</label><input class="form-input" id="_kmod-key" type="password" placeholder="${isEdit?'leave blank to keep current':'sk-...'}"></div>
    <div><label class="form-label">Usage / priority</label>
      <select class="form-select" id="_kmod-usage" style="width:100%">
        <option value="preferred">Preferred (prio 1 — used first, round-robin among peers)</option>
        <option value="round_robin">Round-robin (prio 2 — when no preferred available)</option>
        <option value="fallback">Fallback (prio 3 — last resort)</option>
      </select>
    </div>
    <div><label class="form-label">Expiry date <span style="color:var(--text-400)">(optional, ISO date e.g. 2026-12-31)</span></label>
      <input class="form-input" id="_kmod-deadline" placeholder="2026-12-31">
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="saveKeyFromModal('${esc(provName)}','${isEdit?esc(keyName):''}')">Save</button>
      <button class="btn-secondary" onclick="document.getElementById('_key-edit-modal')?.remove()">Cancel</button>
    </div>
  </div>`;
  overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  // If editing, pre-fill usage/deadline from current state
  if (isEdit) {
    const providers = state.providers || [];
    const prov = providers.find(p=>p.name===provName);
    if (prov) {
      const key = (prov.api_keys||[]).find(k=>k.name===keyName);
      if (key) {
        document.getElementById('_kmod-usage').value = key.usage || 'preferred';
        document.getElementById('_kmod-deadline').value = key.deadline || '';
      }
    }
  }
}

async function saveKeyFromModal(provName, oldKeyName) {
  const name = document.getElementById('_kmod-name')?.value?.trim();
  const keyVal = document.getElementById('_kmod-key')?.value;
  const usage = document.getElementById('_kmod-usage')?.value || 'preferred';
  const deadline = document.getElementById('_kmod-deadline')?.value?.trim();
  if (!name) { showToast('Key name required', true); return; }
  const isEdit = !!oldKeyName;
  // If editing and no new key value, we need to look up the existing key from stored list
  // The server only has the masked hint, so require the value if it's a new key;
  // for edits without a new value, send a special _keep_key_value flag
  const entry = { name, usage };
  if (keyVal) entry.key = keyVal;
  else if (!isEdit) { showToast('API key value required for new key', true); return; }
  else entry._keep_key_value = true;
  if (deadline) entry.deadline = deadline;
  try {
    await API.post('/v1/providers', { action: 'save_key', name: provName, key_entry: entry, old_name: oldKeyName });
    showToast('Key saved');
    document.getElementById('_key-edit-modal')?.remove();
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    if (document.getElementById('_prov-keys-modal')) await _renderProviderKeysList(provName);
    else switchGeneralTab('providers');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function deleteProviderKey(provName, keyName) {
  if (!await showConfirmDanger(`Delete key "${keyName}" from ${provName}?`, 'Delete Key', 'Delete')) return;
  try {
    await API.post('/v1/providers', { action: 'delete_key', name: provName, key_name: keyName });
    showToast('Key deleted');
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    if (document.getElementById('_prov-keys-modal')) await _renderProviderKeysList(provName);
    else switchGeneralTab('providers');
  } catch(e) { showToast('Delete failed: ' + e.message, true); }
}

async function testProvider(name) {
  try {
    const r = await API.post('/v1/providers/test', { name });
    showToast(r.status === 'ok' ? `${name}: connected` : `${name}: ${r.error||'failed'}`, r.status !== 'ok');
  } catch(e) { showToast('Test failed: ' + e.message, true); }
}

async function deleteProvider(name) {
  try {
    await API.post('/v1/providers', { action: 'delete', name });
    showToast('Provider deleted');
    switchGeneralTab('providers');
  } catch(e) { showToast('Delete failed: ' + e.message, true); }
}

async function _confirmDeleteProvider(name) {
  if (!await showConfirmDanger(`Delete provider ${name}?`, 'Delete Provider', 'Delete')) return;
  deleteProvider(name);
}

async function renameProvider(name) {
  const next = await showPrompt(`Rename provider "${name}" to:`, name);
  if (next === null) return;
  const trimmed = next.trim();
  if (!trimmed || trimmed === name) return;
  if (/[\s/]/.test(trimmed)) { showToast("Name must not contain '/' or whitespace", true); return; }
  try {
    await API.post('/v1/providers', { action: 'rename', name, new_name: trimmed });
    showToast(`Renamed to ${trimmed}`);
    switchGeneralTab('providers');
  } catch(e) { showToast('Rename failed: ' + e.message, true); }
}

async function testNewProvider() {
  const res = document.getElementById('prov-test-result');
  res.innerHTML = '<span style="color:var(--text-400)">Testing...</span>';
  try {
    const r = await API.post('/v1/providers/test', {
      base_url: document.getElementById('prov-url')?.value,
      api_key: document.getElementById('prov-key')?.value,
    });
    res.innerHTML = r.status === 'ok'
      ? `<span style="color:var(--success)">Connected (${r.models||0} models)</span>`
      : `<span style="color:var(--error)">${esc(r.error||'Failed')}</span>`;
  } catch(e) { res.innerHTML = `<span style="color:var(--error)">${esc(e.message)}</span>`; }
}

async function saveNewProvider() {
  const name = document.getElementById('prov-name')?.value?.trim();
  if (!name) { showToast('Name required', true); return; }
  const rawKey = document.getElementById('prov-key')?.value || '';
  const provider = {
    base_url: document.getElementById('prov-url')?.value || '',
    default_model: document.getElementById('prov-model')?.value || '',
    api_keys: rawKey ? [{ name: 'default', key: rawKey, usage: 'preferred' }] : [],
  };
  try {
    await API.post('/v1/providers', { action: 'add', name, provider });
    showToast('Provider added');
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    switchGeneralTab('providers');
  } catch(e) { showToast('Add failed: ' + e.message, true); }
}

async function createNode() {
  const name = document.getElementById('node-name')?.value?.trim();
  if (!name) { showToast('Name required', true); return; }
  try {
    const r = await API.post('/v1/nodes', {
      action: 'add', name,
      description: document.getElementById('node-desc')?.value || '',
    });
    const res = document.getElementById('node-result');
    if (r.token) {
      res.innerHTML = `<div style="margin-top:8px;padding:10px;background:var(--bg-200);border-radius:8px"><div style="font-size:12px;font-weight:500;color:var(--text-200);margin-bottom:4px">Install token:</div><code style="font-size:11px;color:var(--accent-brand);word-break:break-all">${esc(r.token)}</code></div>`;
    }
    showToast('Node created');
    switchGeneralTab('nodes');
  } catch(e) { showToast('Create failed: ' + e.message, true); }
}

async function _confirmRemoveNode(name) {
  if (!await showConfirmDanger('Remove node?', 'Remove Node', 'Remove')) return;
  try {
    await API.post('/v1/nodes', { action: 'remove', name });
    showToast('Removed');
    switchGeneralTab('nodes');
  } catch(e) { showToast('Remove failed: ' + e.message, true); }
}

async function toggleCCSkill(agentId, slug, enabled) {
  try {
    await API.toggleCCSkill(agentId, slug, enabled);
    switchAgentTab(agentId, 'skills', null);
  } catch(e) {
    console.error('Failed to toggle skill:', e);
  }
}

async function removeAgentSkill(agentId, skillName) {
  try {
    await API.removeSkill(skillName, agentId);
    switchAgentTab(agentId, 'skills', null);
  } catch(e) {
    console.error('Failed to remove skill:', e);
  }
}

async function searchCCMarketplace(query) {
  const results = document.getElementById('cc-marketplace-results');
  const searchBtn = document.getElementById('cc-marketplace-search-btn');
  if (!query) {
    results.innerHTML = '<div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Enter a search term to browse plugins</div>';
    return;
  }
  results.innerHTML = `<div style="padding:20px;text-align:center">
    <div style="display:inline-block;width:18px;height:18px;border:2px solid #e8e7e0;border-top-color:#d97757;border-radius:50%;animation:spin 0.6s linear infinite"></div>
    <div style="margin-top:8px;color:#73726c;font-size:12px">Searching marketplace for "${esc(query)}"...</div>
  </div>`;
  if (searchBtn) { searchBtn.textContent = '...'; searchBtn.disabled = true; }
  try {
    const data = await API.browseCCPlugins(query);
    const plugins = data.plugins || [];
    if (searchBtn) { searchBtn.textContent = 'Search'; searchBtn.disabled = false; }
    if (!plugins.length) {
      results.innerHTML = `<div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">No plugins found for "${esc(query)}"</div>`;
      return;
    }
    let html = `<div style="padding:4px 0;font-size:11px;color:#a1a09a;margin-bottom:4px">${plugins.length} plugins found</div>`;
    for (const p of plugins) {
      html += `
        <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px;margin-bottom:6px;transition:background 0.15s;overflow:hidden" onmouseover="this.style.background='rgba(0,0,0,0.02)'" onmouseout="this.style.background='transparent'">
          <div style="flex:1;min-width:0;overflow:hidden">
            <div style="font-size:13px;font-weight:600;color:#141413">${esc(p.name || p)}</div>
            ${p.description ? `<div style="font-size:11px;color:#73726c;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.description)}</div>` : ''}
          </div>
          <button onclick="installCCPlugin('${esc(typeof p === 'string' ? p : p.name)}',this)"
            style="${_mcpBtn};padding:4px 12px;flex-shrink:0">Install</button>
        </div>`;
    }
    results.innerHTML = html;
  } catch(e) {
    if (searchBtn) { searchBtn.textContent = 'Search'; searchBtn.disabled = false; }
    results.innerHTML = `<div style="padding:24px;text-align:center;color:#dc2626;font-size:12px">${esc(e.message)}</div>`;
  }
}

async function installCCPlugin(pluginName, btn) {
  btn.disabled = true;
  btn.textContent = 'Installing...';
  btn.style.opacity = '0.5';
  try {
    await API.installCCPlugin(pluginName);
    const added = document.createElement('span');
    added.textContent = 'Added';
    added.style.cssText = 'font-size:11px;font-weight:500;color:#16a34a;padding:4px 12px';
    btn.replaceWith(added);
  } catch(e) {
    btn.textContent = 'Failed';
    btn.style.cssText = 'font-size:11px;padding:4px 12px;border-radius:6px;background:rgba(220,38,38,0.08);color:#dc2626;border:none;cursor:pointer';
    btn.disabled = false;
  }
}

async function switchAgentTab(agentId, tab, btn) {
  if (btn) {
    btn.closest('.modal-tabs').querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
  }
  const container = document.getElementById('settings-tab-content');
  container.style.overflowY = 'auto';
  container.innerHTML = '<div style="padding:20px;color:var(--text-400)">Loading...</div>';

  if (tab === 'soul') {
    try {
      const data = await API.get(`/v1/agents/${agentId}/file?name=soul.md`);
      const soul = data.content || '';
      container.innerHTML = `
        <div style="display:flex;flex-direction:column;height:100%;padding:16px;overflow:hidden">
          <div style="font-size:12px;color:var(--text-400);margin-bottom:6px">System prompt — defines agent personality and behavior</div>
          <textarea id="agent-soul-editor" class="form-textarea" style="flex:3 1 0;min-height:80px;font-size:13px;overflow-y:auto">${esc(soul)}</textarea>
          <div style="display:flex;justify-content:flex-end;gap:8px;margin:6px 0;align-items:center">
            <button type="button" id="agent-soul-editor-refine"
              onclick="refineAgentSoul('${esc(agentId)}')"
              title="Polish the soul with AI (preserves second-person voice and Markdown structure)"
              class="btn-secondary"
              style="font-size:12px;padding:4px 10px;display:inline-flex;align-items:center;gap:4px">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
                <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
                <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
              </svg>
              <span id="agent-soul-editor-refine-label">Refine with AI</span>
            </button>
            <button class="btn-primary" onclick="saveAgentSoul('${esc(agentId)}')">Save Soul</button>
          </div>
          <div style="border-top:1px solid var(--border);padding-top:6px;display:flex;flex-direction:column;flex:2 1 0;min-height:60px;overflow:hidden">
            <div style="font-size:12px;color:var(--text-400);margin-bottom:4px">AI Soul Editor — chat to refine the soul</div>
            <div id="soul-chat-messages" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:6px;margin-bottom:4px;padding:2px 4px;min-height:0"></div>
            <div style="display:flex;gap:8px;flex-shrink:0">
              <input id="soul-chat-input" class="form-input" style="flex:1;font-size:13px" placeholder="Ask AI to edit the soul..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendSoulChat('${esc(agentId)}')}" />
              <button class="btn-primary" onclick="sendSoulChat('${esc(agentId)}')" id="soul-chat-send">Send</button>
            </div>
          </div>
        </div>
      `;
      container.style.overflowY = 'hidden';
      window._soulChatHistory = [];
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">Failed to load soul: ${esc(e.message)}</div>`;
    }
  }

  if (tab === 'agent') {
    try {
      const data = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
      const cfg = JSON.parse(data.content || '{}');
      const enabledModels = enabledModelsWithCapability('chat');
      const modelOptions = enabledModels.map(([mid]) =>
        modelOption(mid, {selected: mid===cfg.model})
      ).join('');
      // Next-prompt suggestion config — override model drives where the
      // ghost-text completion is generated. Empty = reuse session model
      // (best cache reuse, but slow on thinking local models like Qwen3.6).
      const nps = (cfg.next_prompt_suggestions && typeof cfg.next_prompt_suggestions === 'object')
        ? cfg.next_prompt_suggestions : {};
      const npsEnabled = nps.enabled !== false;
      const npsModel = nps.model || '';
      const npsMaxWords = nps.max_words != null ? nps.max_words : 15;
      const npsModelOptions = `<option value="" ${npsModel===''?'selected':''}>(session model — best cache reuse)</option>`
        + enabledModels.map(([mid]) =>
            modelOption(mid, {selected: mid===npsModel})
          ).join('');

      container.innerHTML = `
        <div style="padding:16px;display:grid;gap:16px">
          <div>
            <label class="form-label">Display Name</label>
            <input class="form-input" id="cfg-display-name" value="${esc(cfg.display_name || '')}">
          </div>
          <div>
            <label class="form-label">Description</label>
            <input class="form-input" id="cfg-description" value="${esc(cfg.description || '')}">
          </div>
          <div>
            <label class="form-label">Model</label>
            <select class="form-select" id="cfg-model" style="width:100%">${modelOptions}</select>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" id="cfg-paused" ${cfg.paused?'checked':''}>
            <label for="cfg-paused" style="font-size:14px;color:var(--text-200)">Paused</label>
          </div>
          <div style="border-top:1px solid var(--border-100);padding-top:12px;display:grid;gap:10px">
            <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em">Next-Prompt Suggestions</div>
            <div style="font-size:11px;color:var(--text-400)">Ghost-text prediction shown in the composer after each assistant response. Override model when the session model is a slow thinking model (e.g. Qwen3.6) — pick a fast model like Haiku for sub-second suggestions.</div>
            <div style="display:flex;align-items:center;gap:8px">
              <input type="checkbox" id="cfg-nps-enabled" ${npsEnabled?'checked':''}>
              <label for="cfg-nps-enabled" style="font-size:14px;color:var(--text-200)">Enabled</label>
            </div>
            <div>
              <label class="form-label">Model Override</label>
              <select class="form-select" id="cfg-nps-model" style="width:100%">${npsModelOptions}</select>
            </div>
            <div>
              <label class="form-label">Max Words</label>
              <input class="form-input" type="number" id="cfg-nps-max-words" value="${npsMaxWords}" min="3" max="40" style="width:120px">
            </div>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button class="btn-primary" onclick="saveAgentJson('${esc(agentId)}')">Save</button>
          </div>
        </div>
      `;
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">Failed to load config: ${esc(e.message)}</div>`;
    }
  }

  if (tab === 'skills') {
    try {
      const [ccData, filesData] = await Promise.all([
        API.getClaudeCodeSkills(agentId),
        API.get(`/v1/agents/${agentId}/files`).catch(() => null),
      ]);
      const ccSkills = ccData.skills || [];
      const agentSkills = filesData?.skills || [];

      let html = '<div style="padding:16px;display:grid;gap:14px;grid-template-columns:minmax(0,1fr)">';

      // --- Section 1: Installed Agent Skills ---
      html += `
        <div>
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:14px;font-weight:700;color:#141413">Agent Skills</span>
              <span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(217,119,87,0.12);color:#d97757">${agentSkills.length} installed</span>
            </div>
          </div>`;
      if (!agentSkills.length) {
        html += '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">No agent skills installed.</div>';
      } else {
        html += '<div style="display:grid;gap:6px;grid-template-columns:minmax(0,1fr)">';
        for (const s of agentSkills) {
          const name = typeof s === 'string' ? s : s.name;
          // Server's remove endpoint resolves by folder name (the slug); the
          // display `name` may differ (e.g. "Word / DOCX" → folder `word-docx`).
          const slug = typeof s === 'object' ? (s.slug || s.name) : s;
          const desc = typeof s === 'object' ? s.description : '';
          html += `
            <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px;transition:background 0.15s;overflow:hidden" onmouseover="this.style.background='rgba(0,0,0,0.02)'" onmouseout="this.style.background='transparent'">
              <div style="flex:1;min-width:0;overflow:hidden">
                <div style="font-size:13px;font-weight:600;color:#141413">${esc(name)}</div>
                ${desc ? `<div style="font-size:11px;color:#73726c;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(desc)}</div>` : ''}
              </div>
              <button onclick="removeAgentSkill('${esc(agentId)}','${esc(slug)}')"
                style="font-size:11px;color:#dc2626;padding:3px 10px;border-radius:5px;cursor:pointer;background:rgba(220,38,38,0.08);border:none;white-space:nowrap;flex-shrink:0" onmouseover="this.style.background='rgba(220,38,38,0.15)'" onmouseout="this.style.background='rgba(220,38,38,0.08)'"
                title="Remove skill">Remove</button>
            </div>`;
        }
        html += '</div>';
      }
      html += '</div>';

      // --- Section 2: Claude Code Skills ---
      html += `
        <div style="border-top:1px solid rgba(31,30,29,0.08);padding-top:16px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <div>
              <span style="font-size:14px;font-weight:700;color:#141413">Claude Code Skills</span>
              <div style="font-size:11px;color:#73726c;margin-top:2px">Toggle skills from ~/.claude to enable for this agent</div>
            </div>
          </div>`;
      if (!ccSkills.length) {
        html += '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">No Claude Code skills found in ~/.claude</div>';
      } else {
        html += '<div style="display:grid;gap:6px;grid-template-columns:minmax(0,1fr)">';
        for (const s of ccSkills) {
          const checked = s.agent_enabled ? 'checked' : '';
          html += `
            <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px;opacity:${s.agent_enabled ? '1' : '0.6'};transition:background 0.15s,opacity 0.2s;overflow:hidden" onmouseover="this.style.background='rgba(0,0,0,0.02)'" onmouseout="this.style.background='transparent'">
              <label style="position:relative;width:36px;height:20px;flex-shrink:0;cursor:pointer">
                <input type="checkbox" ${checked}
                  onchange="toggleCCSkill('${esc(agentId)}','${esc(s.slug)}',this.checked)"
                  style="opacity:0;width:0;height:0;position:absolute">
                <span style="position:absolute;inset:0;border-radius:10px;background:${s.agent_enabled ? '#d97757' : '#e8e7e0'};transition:background .2s;cursor:pointer"></span>
                <span style="position:absolute;top:2px;left:${s.agent_enabled ? '18px' : '2px'};width:16px;height:16px;border-radius:50%;background:white;transition:left .2s;box-shadow:0 1px 3px rgba(0,0,0,.3)"></span>
              </label>
              <div style="flex:1;min-width:0;overflow:hidden">
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                  <span style="font-size:13px;font-weight:600;color:#141413">${esc(s.name || s.slug)}</span>
                  ${s.source ? `<span style="font-size:9px;padding:2px 6px;border-radius:4px;background:rgba(120,120,140,0.15);color:#73726c;display:inline-block">${esc(s.source)}</span>` : ''}
                </div>
                ${s.description ? `<div style="font-size:11px;color:#73726c;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(s.description)}</div>` : ''}
                <div style="font-size:10px;color:#a1a09a;font-family:var(--font-mono);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.slug)}</div>
              </div>
            </div>`;
        }
        html += '</div>';
      }
      html += '</div>';

      // --- Section 3: Browse CC Marketplace (always visible) ---
      html += `
        <div style="border-top:1px solid rgba(31,30,29,0.08);padding-top:16px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <span style="font-size:14px;font-weight:700;color:#141413">Browse CC Marketplace</span>
            <span style="font-size:10px;color:#a1a09a">Claude Code plugins</span>
          </div>
          <div style="position:relative;margin-bottom:10px">
            <input id="cc-marketplace-search" style="width:100%;box-sizing:border-box;background:#f5f4ed;border:1px solid rgba(31,30,29,0.08);border-radius:8px;padding:9px 80px 9px 12px;font-size:13px;color:#141413;outline:none;transition:border-color 0.15s" placeholder="Search Claude Code plugins..." onfocus="this.style.borderColor='#d97757'" onblur="this.style.borderColor='rgba(31,30,29,0.08)'" onkeydown="if(event.key==='Enter'){event.preventDefault();searchCCMarketplace(this.value)}" oninput="clearTimeout(window._ccMktDebounce);window._ccMktDebounce=setTimeout(()=>searchCCMarketplace(this.value),400)">
            <button id="cc-marketplace-search-btn" style="position:absolute;right:4px;top:50%;transform:translateY(-50%);font-size:11px;padding:5px 14px;border-radius:6px;background:#d97757;color:#fff;border:none;cursor:pointer;font-weight:500;transition:background 0.15s" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="searchCCMarketplace(document.getElementById('cc-marketplace-search').value)">Search</button>
          </div>
          <div id="cc-marketplace-results" style="display:grid;gap:6px">
            <div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Type to search the Claude Code plugin marketplace</div>
          </div>
        </div>`;

      html += '</div>';
      container.innerHTML = html;
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:#73726c">Skills not available: ${esc(e.message)}</div>`;
    }
  }


  if (tab === 'schedule') {
    try {
      const data = await API.getSchedule();
      const schedules = (data.schedules || []).filter(t => t.agent === agentId);
      const running = data.running || [];
      const runningNames = new Set(running.map(r => r.name));
      window._schedAgentId = agentId;

      let html = '<div style="padding:16px;display:grid;gap:16px">';

      // Schedule list
      html += '<div id="sched-list" style="display:grid;gap:8px">';
      if (schedules.length) {
        for (const s of schedules) {
          const isSystem = (s.name||'').startsWith('_');
          const enabled = s.enabled === 1 || s.enabled === true;
          const isRunning = runningNames.has(s.name) || !!s.is_running;
          const displayName = isSystem ? s.name.replace(/^_/, '').replace(/_/g, ' ') : s.name;
          html += `<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;opacity:${enabled?1:0.5}">
            <span style="width:8px;height:8px;border-radius:50%;background:${isRunning?'var(--accent-brand)':enabled?'var(--success)':'var(--text-400)'};flex-shrink:0;${isRunning?'animation:pulse 1.5s infinite':''}"></span>
            <div style="flex:1;min-width:0">
              <div style="font-size:13px;font-weight:500;color:var(--text-100);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(displayName)}${isSystem?' <span style="font-size:10px;color:var(--text-400);font-weight:400">system</span>':''}</div>
              <div style="font-size:11px;color:var(--text-400);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(s.task||'')}">${esc((s.task||'').substring(0,80))}</div>
            </div>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:2px;flex-shrink:0">
              <span style="font-size:11px;font-family:var(--font-mono);color:var(--text-300)">${esc(s.schedule||'')}</span>
              ${s.next_run ? `<span style="font-size:10px;color:var(--text-400)">next: ${new Date(s.next_run+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</span>` : ''}
            </div>
            <div style="display:flex;gap:4px;flex-shrink:0">
              ${isRunning ? `<button onclick="_schedCancel('${esc(s.name)}')" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--error);background:transparent;color:var(--error);cursor:pointer" title="Cancel running task">Stop</button>` : ''}
              <button onclick="_schedToggle('${esc(s.name)}',${enabled?0:1})" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer" title="${enabled?'Pause':'Resume'}">${enabled?'Pause':'Resume'}</button>
              <button onclick="_schedHistory('${esc(s.name)}')" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer" title="View history">Log</button>
              ${!isSystem ? `<button onclick="_schedDelete('${esc(s.name)}')" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--error);background:transparent;color:var(--error);cursor:pointer" title="Delete">Del</button>` : ''}
            </div>
          </div>`;
        }
      } else {
        html += '<div id="sched-empty" style="padding:20px;text-align:center;color:var(--text-400)">No scheduled tasks for this agent</div>';
      }
      html += '</div>';

      // History panel (hidden)
      html += '<div id="sched-history" style="display:none;padding:12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-200)"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span style="font-size:13px;font-weight:600;color:var(--text-100)" id="sched-history-title">History</span><button onclick="document.getElementById(\'sched-history\').style.display=\'none\'" style="font-size:11px;padding:2px 8px;border-radius:4px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer">Close</button></div><div id="sched-history-body" style="max-height:200px;overflow-y:auto;font-size:12px"></div></div>';

      // Add button + form
      html += `<div style="display:flex;justify-content:flex-start">
        <button onclick="_schedShowForm()" style="font-size:12px;padding:6px 14px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;font-weight:500">+ Add Task</button>
      </div>`;

      html += `<div id="sched-form" style="display:none;padding:14px;border:1px solid var(--accent);border-radius:8px;background:var(--bg-200)">
        <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:10px">New Scheduled Task</div>
        <div style="display:grid;gap:8px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div>
              <label style="font-size:11px;color:var(--text-400)">Name</label>
              <input id="sched-f-name" class="form-input" placeholder="daily-report" style="font-size:12px">
            </div>
            <div>
              <label style="font-size:11px;color:var(--text-400)">Schedule</label>
              <input id="sched-f-schedule" class="form-input" placeholder="daily 09:00" style="font-size:12px;font-family:var(--font-mono)">
            </div>
          </div>
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
              <label style="font-size:11px;color:var(--text-400);margin:0">Prompt / Task</label>
              ${_schedRefineControls('sched-f-task')}
            </div>
            <textarea id="sched-f-task" class="form-input" rows="3" placeholder="What should the agent do..." style="font-size:12px;resize:vertical"></textarea>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div>
              <label style="font-size:11px;color:var(--text-400)">Model (optional)</label>
              <input id="sched-f-model" class="form-input" placeholder="default" style="font-size:12px;font-family:var(--font-mono)">
            </div>
            <div>
              <label style="font-size:11px;color:var(--text-400)">Timeout (seconds)</label>
              <input id="sched-f-timeout" class="form-input" type="number" value="300" style="font-size:12px">
            </div>
          </div>
          <div style="font-size:10px;color:var(--text-400);line-height:1.4">
            Formats: <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">every 5m</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">every 2h</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">daily 09:00</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">weekly mon 14:30</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">once 2026-04-01 12:00</code>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button onclick="document.getElementById('sched-form').style.display='none'" style="font-size:12px;padding:5px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer">Cancel</button>
            <button class="btn-primary" onclick="_schedAdd()" style="font-size:12px;padding:5px 12px">Create</button>
          </div>
        </div>
      </div>`;

      container.innerHTML = html + '</div>';
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">Failed to load schedule</div>`;
    }
  }

  if (tab === 'hooks') {
    try {
      const data = await API.get(`/v1/agents/${agentId}/hooks`);
      const hooksEnabled = data.enabled || false;
      const hooksTimeout = data.timeout || 5000;
      const scripts = data.scripts || [];
      window._hooksAgentId = agentId;
      window._hooksData = { enabled: hooksEnabled, timeout: hooksTimeout, scripts: [...scripts] };

      let html = '<div style="padding:16px;display:grid;gap:16px">';

      // Master toggle + timeout
      html += `<div style="display:flex;align-items:center;gap:16px;padding:12px;background:var(--bg-200);border-radius:8px">
        <div style="display:flex;align-items:center;gap:8px;flex:1">
          <input type="checkbox" id="hooks-enabled" ${hooksEnabled?'checked':''} onchange="window._hooksData.enabled=this.checked">
          <label for="hooks-enabled" style="font-size:14px;font-weight:500;color:var(--text-100)">Hooks enabled</label>
        </div>
        <div style="display:flex;align-items:center;gap:6px">
          <label style="font-size:12px;color:var(--text-400)">Timeout (ms)</label>
          <input type="number" id="hooks-timeout" value="${hooksTimeout}" style="width:70px;padding:4px 6px;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-100);color:var(--text-100);font-size:12px" onchange="window._hooksData.timeout=parseInt(this.value)||5000">
        </div>
      </div>`;

      // Scripts list
      html += '<div id="hooks-list" style="display:grid;gap:8px">';
      if (scripts.length) {
        for (let i = 0; i < scripts.length; i++) {
          html += _renderHookRow(scripts[i], i);
        }
      } else {
        html += '<div id="hooks-empty" style="padding:20px;text-align:center;color:var(--text-400)">No hook scripts configured</div>';
      }
      html += '</div>';

      // Add + Save buttons
      html += `<div style="display:flex;justify-content:space-between;align-items:center">
        <button onclick="_hookAdd()" style="font-size:12px;padding:6px 14px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;font-weight:500">+ Add Hook</button>
        <button class="btn-primary" onclick="_hooksSave()">Save</button>
      </div>`;

      // Add/Edit form (hidden initially)
      html += `<div id="hook-form" style="display:none;padding:14px;border:1px solid var(--accent);border-radius:8px;background:var(--bg-200)">
        <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:10px" id="hook-form-title">Add Hook</div>
        <div style="display:grid;gap:8px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div>
              <label style="font-size:11px;color:var(--text-400)">Name</label>
              <input id="hook-f-name" class="form-input" placeholder="my-hook" style="font-size:12px">
            </div>
            <div>
              <label style="font-size:11px;color:var(--text-400)">Type</label>
              <select id="hook-f-type" class="form-input" style="font-size:12px">
                <option value="pre">pre</option>
                <option value="post">post</option>
                <option value="after_file_write">after_file_write</option>
              </select>
            </div>
          </div>
          <div>
            <label style="font-size:11px;color:var(--text-400)">Script path (relative to agent dir)</label>
            <input id="hook-f-script" class="form-input" placeholder="hooks/my-hook.sh" style="font-size:12px;font-family:var(--font-mono)">
          </div>
          <div>
            <label style="font-size:11px;color:var(--text-400)">Tools (comma-separated, * for all)</label>
            <input id="hook-f-tools" class="form-input" placeholder="*" value="*" style="font-size:12px;font-family:var(--font-mono)">
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" id="hook-f-enabled" checked>
            <label for="hook-f-enabled" style="font-size:12px;color:var(--text-200)">Enabled</label>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button onclick="document.getElementById('hook-form').style.display='none'" style="font-size:12px;padding:5px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer">Cancel</button>
            <button class="btn-primary" onclick="_hookFormSave()" style="font-size:12px;padding:5px 12px">Save Hook</button>
          </div>
        </div>
      </div>`;

      container.innerHTML = html + '</div>';
    } catch(e) { container.innerHTML = '<div style="padding:20px;color:var(--text-400)">Hooks not available</div>'; }
  }


  if (tab === 'mcp') {
    // Load agent's mcp.json + main's (for inheritance display)
    let agentMcp = {}, mainMcp = {};
    try {
      const res = await API.get(`/v1/agents/${agentId}/file?name=mcp.json`);
      const raw = typeof res === 'string' ? res : (res.content || JSON.stringify(res));
      agentMcp = JSON.parse(raw);
      if (agentMcp.mcpServers) agentMcp = agentMcp.mcpServers;
    } catch {}
    if (agentId !== 'main') {
      try {
        const res = await API.get(`/v1/agents/main/file?name=mcp.json`);
        const raw = typeof res === 'string' ? res : (res.content || JSON.stringify(res));
        mainMcp = JSON.parse(raw);
        if (mainMcp.mcpServers) mainMcp = mainMcp.mcpServers;
      } catch {}
    }

    // Also load live connection status
    let liveStatus = {};
    try {
      const data = await API.get('/v1/mcp/connections');
      const connections = data.connections || data || [];
      if (Array.isArray(connections)) {
        for (const c of connections) {
          const n = c.name || c.server || '';
          liveStatus[n] = { connected: c.connected || c.status === 'connected', tools_count: c.tools_count || 0 };
        }
      }
    } catch {}

    // Merge: main (inherited) + agent (own, overrides)
    const allServers = {};
    for (const [name, cfg] of Object.entries(mainMcp)) {
      allServers[name] = { ...cfg, _source: 'main' };
    }
    for (const [name, cfg] of Object.entries(agentMcp)) {
      allServers[name] = { ...cfg, _source: agentId };
    }

    const _b = 'font-size:9px;padding:2px 6px;border-radius:4px;display:inline-block;';
    let serverCards = '';
    if (!Object.keys(allServers).length) {
      serverCards = '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">No MCP servers configured. Add one manually or browse the registry below.</div>';
    } else {
      for (const [name, cfg] of Object.entries(allServers)) {
        const inherited = cfg._source === 'main' && agentId !== 'main';
        const transport = cfg.transport || cfg.type || (cfg.command ? 'stdio' : cfg.url ? 'sse' : '?');
        const target = cfg.command ? `${cfg.command} ${(cfg.args||[]).join(' ')}` : cfg.url || '';
        const live = liveStatus[name];
        const dot = live ? `<span style="width:7px;height:7px;border-radius:50%;background:${live.connected?'#16a34a':'#a1a09a'};flex-shrink:0" title="${live.connected?'Connected':'Disconnected'}"></span>` : '<span style="width:7px;height:7px;border-radius:50%;background:#d4d4d0;flex-shrink:0" title="Not connected"></span>';
        const tools = live && live.tools_count ? `<span style="${_b}background:rgba(22,163,74,0.1);color:#16a34a">${live.tools_count} tools</span>` : '';
        const src = inherited
          ? `<span style="${_b}background:rgba(120,120,140,0.15);color:#73726c">inherited</span>`
          : `<span style="${_b}background:rgba(139,92,246,0.15);color:#8b5cf6">local</span>`;
        const tp = `<span style="${_b}background:rgba(59,130,246,0.12);color:#3b82f6">${esc(transport)}</span>`;
        const rm = !inherited
          ? `<button style="font-size:11px;color:#dc2626;padding:3px 10px;border-radius:5px;cursor:pointer;background:rgba(220,38,38,0.08);border:none;white-space:nowrap" onmouseover="this.style.background='rgba(220,38,38,0.15)'" onmouseout="this.style.background='rgba(220,38,38,0.08)'" onclick="_removeAgentMcp('${esc(agentId)}','${esc(name)}')">Remove</button>`
          : '';
        serverCards += `
          <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px;transition:background 0.15s" onmouseover="this.style.background='rgba(0,0,0,0.02)'" onmouseout="this.style.background='transparent'">
            ${dot}
            <div style="flex:1;min-width:0">
              <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                <span style="font-size:13px;font-weight:600;color:#141413">${esc(name)}</span>
                ${tp} ${src} ${tools}
              </div>
              <div style="font-size:11px;color:#73726c;font-family:var(--font-mono);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(target.slice(0,100))}</div>
            </div>
            ${rm}
          </div>`;
      }
    }

    container.innerHTML = `
      <div style="padding:16px;display:grid;gap:14px">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span style="font-size:14px;font-weight:700;color:#141413">MCP Servers</span>
          <button style="font-size:11px;padding:5px 14px;border-radius:6px;background:#d97757;color:#fff;border:none;cursor:pointer;font-weight:500;transition:background 0.15s" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="_showAgentMcpAdd('${esc(agentId)}')">+ Add Manually</button>
        </div>
        ${agentId !== 'main' ? '<div style="font-size:11px;color:#73726c;margin-top:-6px">Inherits servers from main. Add agent-specific servers below.</div>' : ''}
        <div id="agent-mcp-list" style="display:grid;gap:6px">${serverCards}</div>
        <div id="agent-mcp-add-form"></div>

        <div style="border-top:1px solid rgba(31,30,29,0.08);padding-top:16px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <span style="font-size:14px;font-weight:700;color:#141413">Browse MCP Registry</span>
            <span style="font-size:10px;color:#a1a09a">registry.modelcontextprotocol.io</span>
          </div>
          <div style="position:relative;margin-bottom:10px">
            <input id="mcp-registry-search" style="width:100%;box-sizing:border-box;background:#f5f4ed;border:1px solid rgba(31,30,29,0.08);border-radius:8px;padding:9px 80px 9px 12px;font-size:13px;color:#141413;outline:none;transition:border-color 0.15s" placeholder="Search servers (e.g. github, slack, postgres)..." onfocus="this.style.borderColor='#d97757'" onblur="this.style.borderColor='rgba(31,30,29,0.08)'" onkeydown="if(event.key==='Enter'){event.preventDefault();_searchMcpRegistry('${esc(agentId)}')}" oninput="clearTimeout(window._mcpSearchDebounce);window._mcpSearchDebounce=setTimeout(()=>_searchMcpRegistry('${esc(agentId)}'),400)">
            <button id="mcp-registry-search-btn" style="position:absolute;right:4px;top:50%;transform:translateY(-50%);font-size:11px;padding:5px 14px;border-radius:6px;background:#d97757;color:#fff;border:none;cursor:pointer;font-weight:500;transition:background 0.15s" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="_searchMcpRegistry('${esc(agentId)}')">Search</button>
          </div>
          <div id="mcp-registry-results" style="display:grid;gap:6px">
            <div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Type to search the official MCP server registry</div>
          </div>
        </div>
      </div>`;
  }

  if (tab === 'tokens') {
    try {
      const data = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
      const agentCfg = JSON.parse(data.content || '{}');
      const tcfg = agentCfg.token_config || {};
      const allGroups = ['core','memory','context','web','email','documents','delegation','code_graph','git','scheduler','mcp','skills','nodes'];
      const activeGroups = tcfg.tool_groups || null; // null = all
      const deferredGroups = 'deferred_tool_groups' in tcfg ? (tcfg.deferred_tool_groups || []) : ['email','documents','code_graph','scheduler'];
      const mc = state.modelsConfig?.models || {};
      const modelOptions = enabledModelsWithCapability('chat');

      container.innerHTML = `
        <div style="padding:16px;display:grid;gap:16px">
          <div style="font-size:16px;font-weight:600;color:var(--text-100)">Token Optimization</div>
          <div style="font-size:11px;color:var(--text-400)">Configure per-agent settings to minimize token usage and API costs.</div>

          <div style="border:1px solid var(--border);border-radius:8px;padding:14px">
            <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:8px">Tool Groups</div>
            <div style="font-size:11px;color:var(--text-400);margin-bottom:10px">
              Select which tool groups are sent to the LLM. <b>Deferred</b> groups are available but only loaded when the model uses tool_search — saves tokens on every request.
            </div>
            <div id="tok-tool-groups" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:4px">
              ${allGroups.map(g => {
                const enabled = activeGroups ? activeGroups.includes(g) : true;
                const deferred = deferredGroups.includes(g);
                return `
                <div style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200)">
                  <input type="checkbox" class="tok-group-enabled" value="${g}" ${enabled ? 'checked' : ''}
                    style="accent-color:var(--accent)" onchange="if(!this.checked){this.closest('div').querySelector('.tok-group-deferred').checked=false}">
                  <span style="min-width:80px">${g}</span>
                  <input type="checkbox" class="tok-group-deferred" value="${g}" ${deferred ? 'checked' : ''}
                    style="accent-color:var(--warning,#d97706)" title="Defer: load on-demand via tool_search"
                    onchange="if(this.checked){this.closest('div').querySelector('.tok-group-enabled').checked=true}">
                  <span style="font-size:10px;color:${deferred ? 'var(--warning,#d97706)' : 'var(--text-400)'}">defer</span>
                </div>`;
              }).join('')}
            </div>
            <div style="margin-top:6px;font-size:10px;color:var(--text-400)">
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="tok-all-tools" ${!activeGroups ? 'checked' : ''}
                  onchange="document.querySelectorAll('#tok-tool-groups .tok-group-enabled').forEach(i=>{i.disabled=this.checked;if(this.checked)i.checked=true})"
                  style="accent-color:var(--accent)">
                Send all tools (no filtering)
              </label>
            </div>
          </div>

          <div style="border:1px solid var(--border);border-radius:8px;padding:14px">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
              <div style="font-size:13px;font-weight:600;color:var(--text-100);flex:1">Tool Definition Cost</div>
              <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_loadToolBreakdown('${esc(agentId)}')">Measure</button>
            </div>
            <div style="font-size:11px;color:var(--text-400);margin-bottom:8px">
              Measures how many tokens each tool group contributes to every request. Use this to decide which groups to disable above.
            </div>
            <div id="tok-breakdown" style="font-size:12px;color:var(--text-300)">Click Measure to fetch.</div>
          </div>

          <div style="border:1px solid var(--border);border-radius:8px;padding:14px">
            <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:8px">System Prompt</div>
            <div style="display:grid;gap:8px">
              <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
                <input type="checkbox" id="tok-tools-guide" ${tcfg.include_tools_guide !== false ? 'checked' : ''}
                  style="accent-color:var(--accent)">
                Include tools.md guide (~400 tokens)
              </label>
            </div>
          </div>

          <div style="border:1px solid var(--border);border-radius:8px;padding:14px">
            <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:8px">Context Compaction</div>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:12px;color:var(--text-300)">Compact threshold:</span>
              <input type="number" id="tok-compact-threshold" value="${tcfg.compact_threshold ? Math.round(tcfg.compact_threshold * 100) : ''}"
                placeholder="60" min="40" max="95" step="5"
                class="form-input" style="width:70px;font-size:12px">
              <span style="font-size:11px;color:var(--text-400)">% (empty = default 60%)</span>
            </div>
          </div>

          <div style="border:1px solid var(--border);border-radius:8px;padding:14px">
            <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:8px">Scheduled Tasks</div>
            <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
              <input type="checkbox" id="tok-sched-tools" ${tcfg.scheduled_task_tools !== false ? 'checked' : ''}
                style="accent-color:var(--accent)">
              Include full tool schema in scheduled tasks
            </label>
          </div>

          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button class="btn-primary" onclick="_saveTokenConfig('${esc(agentId)}')">Save Token Config</button>
          </div>
        </div>`;

      // Disable group checkboxes if "all tools" is checked
      if (!activeGroups) {
        document.querySelectorAll('#tok-tool-groups .tok-group-enabled').forEach(i => i.disabled = true);
      }
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">${esc(e.message)}</div>`;
    }
  }
}

window._saveTokenConfig = async function(agentId) {
  const allTools = document.getElementById('tok-all-tools')?.checked;
  const groups = allTools ? null :
    Array.from(document.querySelectorAll('#tok-tool-groups .tok-group-enabled:checked')).map(i => i.value);
  const deferred = Array.from(document.querySelectorAll('#tok-tool-groups .tok-group-deferred:checked')).map(i => i.value);

  const tcfg = {
    tool_groups: groups,
    deferred_tool_groups: deferred.length > 0 ? deferred : null,
    include_tools_guide: document.getElementById('tok-tools-guide')?.checked ?? true,
    scheduled_task_tools: document.getElementById('tok-sched-tools')?.checked ?? true,
  };

  const threshVal = document.getElementById('tok-compact-threshold')?.value;
  tcfg.compact_threshold = threshVal ? parseInt(threshVal) / 100 : null;

  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
    const cfg = JSON.parse(res.content || '{}');
    cfg.token_config = Object.assign(cfg.token_config || {}, tcfg);
    await API.post(`/v1/agents/${encodeURIComponent(agentId)}/file`, {
      name: 'agent.json',
      content: JSON.stringify(cfg, null, 2)
    });
    showToast('Token config saved');
  } catch(e) { showToast('Error: ' + e.message, true); }
};

window._loadToolBreakdown = async function(agentId) {
  const container = document.getElementById('tok-breakdown');
  if (!container) return;
  container.innerHTML = '<span style="color:var(--text-400)">Measuring...</span>';
  try {
    // Load current filter from agent.json so checkboxes reflect saved state
    try {
      const agentFile = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
      const cfg = JSON.parse(agentFile.content || '{}');
      const flt = cfg.token_config?.mcp_tool_filter || null;
      state._mcpFilter = flt;
      state._mcpFilterEnabled = Array.isArray(flt) ? flt : null;
    } catch(_) { state._mcpFilter = null; state._mcpFilterEnabled = null; }
    const b = await API.get(`/v1/tools/breakdown?agent=${encodeURIComponent(agentId)}`);
    const groups = b.groups || [];
    const total = b.total_tokens || 0;
    const builtin = b.builtin_tokens || 0;
    const mcp = b.mcp_tokens || 0;
    const defer = b.deferrable_mcp || {};

    const bar = (n, max) => {
      const pct = max > 0 ? Math.round((n / max) * 100) : 0;
      return `<div style="flex:1;height:5px;background:var(--bg-200);border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--accent-brand)"></div></div>`;
    };

    const segBar = (nm, ds, sc) => {
      const tot = nm + ds + sc || 1;
      return `<div style="display:flex;gap:0;height:4px;border-radius:2px;overflow:hidden;min-width:80px">
        <div style="flex:${nm};background:#8b5cf6" title="name ${nm} tok"></div>
        <div style="flex:${ds};background:var(--accent-brand)" title="description ${ds} tok"></div>
        <div style="flex:${sc};background:var(--success)" title="schema ${sc} tok"></div>
      </div>`;
    };

    const filterSet = new Set(state._mcpFilterEnabled || []);
    const filterActive = (state._mcpFilter && state._mcpFilter.length > 0);

    const toolRow = (t, isMcp) => {
      const schemaPct = t.total_tokens > 0 ? Math.round((t.schema_tokens / t.total_tokens) * 100) : 0;
      const heavy = schemaPct >= 60 && t.schema_tokens >= 100;
      const checked = isMcp ? (filterActive ? (filterSet.has(t.name) ? 'checked' : '') : 'checked') : '';
      const checkbox = isMcp
        ? `<input type="checkbox" data-mcp-tool="${esc(t.name)}" ${checked} style="accent-color:var(--accent-brand);margin-right:6px" onchange="_updateMcpFilterSelection(this)">`
        : '';
      return `<div style="display:grid;grid-template-columns:${isMcp?'18px ':''}1fr 90px 50px 50px 50px 60px;gap:8px;padding:3px 0;font-size:11px;color:var(--text-300);align-items:center;border-top:1px dashed var(--border-050)">
        ${checkbox}
        <span style="font-family:var(--font-mono);color:${heavy?'var(--warning)':'var(--text-200)'};overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc((t.desc||'').toString())}">${esc(t.name)}${heavy?' &#9888;':''}</span>
        ${segBar(t.name_tokens, t.desc_tokens, t.schema_tokens)}
        <span style="text-align:right;color:#8b5cf6" title="name">${t.name_tokens}</span>
        <span style="text-align:right;color:var(--accent-brand)" title="description">${t.desc_tokens}</span>
        <span style="text-align:right;color:var(--success)" title="schema (${t.param_count} params)">${t.schema_tokens}</span>
        <span style="text-align:right;font-weight:600;color:var(--text-100)">${t.total_tokens.toLocaleString()}</span>
      </div>`;
    };

    const rows = groups.map(g => {
      const isMcp = g.source === 'mcp';
      const deferBadge = g.deferred
        ? '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#fef3c7;color:#d97706">DEFERRED</span>'
        : '';
      const header = `<div style="display:grid;grid-template-columns:${isMcp?'18px ':''}1fr 90px 50px 50px 50px 60px;gap:8px;padding:2px 0 4px;font-size:9px;color:var(--text-400);text-transform:uppercase;border-bottom:1px solid var(--border-050)">
        ${isMcp?'<span></span>':''}<span>Tool</span><span style="text-align:center">Split</span>
        <span style="text-align:right">Name</span><span style="text-align:right">Desc</span><span style="text-align:right">Schema</span><span style="text-align:right">Total</span>
      </div>`;
      const tools = (g.tools || []).map(t => toolRow(t, isMcp)).join('');
      return `<details style="border-top:1px solid var(--border-050)">
        <summary style="cursor:pointer;padding:6px 0;display:flex;align-items:center;gap:8px">
          <span style="font-size:10px;padding:1px 6px;border-radius:3px;background:var(--bg-200);color:var(--text-400);text-transform:uppercase">${esc(g.source)}</span>
          <span style="font-size:12px;color:var(--text-200);min-width:100px">${esc(g.name)}</span>
          ${deferBadge}
          <span style="font-size:11px;color:var(--text-400)">${g.tool_count} tools</span>
          <div style="flex:1;min-width:40px;max-width:150px">${bar(g.tokens, total)}</div>
          ${segBar(g.name_tokens||0, g.desc_tokens||0, g.schema_tokens||0)}
          <span style="font-size:12px;font-family:var(--font-mono);min-width:60px;text-align:right;color:${g.deferred?'#d97706':'var(--text-200)'};${g.deferred?'text-decoration:line-through':''}">${g.tokens.toLocaleString()}</span>
        </summary>
        <div style="padding:4px 0 8px 20px">
          ${header}
          ${tools}
        </div>
      </details>`;
    }).join('');

    const mcpActions = (mcp > 0) ? `
      <div style="margin-top:12px;padding:10px;border:1px solid var(--border-050);border-radius:6px;background:var(--bg-100)">
        <div style="font-size:11px;color:var(--text-300);margin-bottom:6px">
          <b>MCP tool filter:</b> check only the MCP tools this agent should see. Unchecked tools will be excluded from every request.
          ${filterActive ? `<span style="color:var(--warning)">&nbsp;(filter currently active)</span>` : ''}
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_saveMcpFilter('${esc(agentId)}', false)">Save selection as filter</button>
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_clearMcpFilter('${esc(agentId)}')">Clear filter (allow all)</button>
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_toggleAllMcp(true)">Check all</button>
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_toggleAllMcp(false)">Uncheck all</button>
        </div>
      </div>` : '';

    const legend = `<div style="display:flex;gap:14px;margin-top:6px;font-size:10px;color:var(--text-400)">
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;background:#8b5cf6;border-radius:2px"></span>Name</span>
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;background:var(--accent-brand);border-radius:2px"></span>Description</span>
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;background:var(--success);border-radius:2px"></span>Schema (parameters)</span>
      <span style="display:flex;align-items:center;gap:4px;margin-left:auto">&#9888; = schema &gt;60% of total</span>
    </div>`;

    const deferNote = defer.deferred
      ? `<div style="margin-top:10px;padding:8px 10px;border-radius:6px;background:var(--bg-200);font-size:11px;color:var(--text-300)">&#9888; MCP deferral <b>active</b> — ~${(defer.tokens_saved_if_deferred||0).toLocaleString()} MCP tokens currently excluded from requests until <code>tool_search</code> discovers them. Threshold: ${(defer.threshold||0).toLocaleString()} tokens.</div>`
      : (mcp > 0
          ? `<div style="margin-top:10px;padding:8px 10px;border-radius:6px;background:var(--bg-200);font-size:11px;color:var(--text-400)">MCP tools (${mcp.toLocaleString()} tok) are below the 10% auto-defer threshold (${(defer.threshold||0).toLocaleString()} tok) — all MCP schemas are included every request.</div>`
          : '');

    const builtinDeferTokens = b.deferred_builtin_tokens || 0;
    const builtinDeferNote = builtinDeferTokens > 0
      ? `<div style="margin-top:8px;padding:8px 10px;border-radius:6px;background:#fef3c7;font-size:11px;color:#92400e">
          Tool deferral saves ~<b>${builtinDeferTokens.toLocaleString()}</b> tokens/request
          (${(b.deferred_builtin_groups||[]).join(', ')} deferred until discovered via <code>tool_search</code>).
          Effective cost: <b>${(total - builtinDeferTokens).toLocaleString()}</b> tokens/request.
        </div>`
      : '';

    container.innerHTML = `
      <div style="display:flex;gap:16px;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--border-050)">
        <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">Total</div><div style="font-size:18px;font-weight:600;color:var(--text-100)">${total.toLocaleString()}</div><div style="font-size:10px;color:var(--text-400)">tokens / request</div></div>
        ${builtinDeferTokens > 0 ? `<div><div style="font-size:10px;color:#d97706;text-transform:uppercase">Deferred</div><div style="font-size:14px;color:#d97706">-${builtinDeferTokens.toLocaleString()}</div></div>` : ''}
        <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">Built-in</div><div style="font-size:14px;color:var(--text-200)">${builtin.toLocaleString()}</div></div>
        <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">MCP</div><div style="font-size:14px;color:var(--text-200)">${mcp.toLocaleString()}</div></div>
        ${b.max_context ? `<div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">% of ctx</div><div style="font-size:14px;color:var(--text-200)">${(total/b.max_context*100).toFixed(1)}%</div></div>` : ''}
      </div>
      ${rows}
      ${legend}
      ${mcpActions}
      ${deferNote}
      ${builtinDeferNote}
    `;
  } catch(e) {
    container.innerHTML = `<span style="color:var(--error)">Failed: ${esc(e.message)}</span>`;
  }
};

window._updateMcpFilterSelection = function(el) {
  // No-op placeholder; _saveMcpFilter reads DOM state directly on click.
};

window._toggleAllMcp = function(checked) {
  document.querySelectorAll('#tok-breakdown input[data-mcp-tool]').forEach(i => { i.checked = !!checked; });
};

window._saveMcpFilter = async function(agentId, silent) {
  const checked = Array.from(document.querySelectorAll('#tok-breakdown input[data-mcp-tool]:checked'))
    .map(i => i.getAttribute('data-mcp-tool'));
  const all = Array.from(document.querySelectorAll('#tok-breakdown input[data-mcp-tool]'));
  if (!all.length) { showToast('No MCP tools to filter'); return; }
  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
    const cfg = JSON.parse(res.content || '{}');
    cfg.token_config = cfg.token_config || {};
    // If everything is checked, treat as "no filter" so the config stays clean
    cfg.token_config.mcp_tool_filter = (checked.length === all.length) ? null : checked;
    await API.post(`/v1/agents/${encodeURIComponent(agentId)}/file`, {
      name: 'agent.json',
      content: JSON.stringify(cfg, null, 2)
    });
    if (!silent) showToast(checked.length === all.length ? 'Filter cleared (all tools allowed)' : `Filter saved: ${checked.length} of ${all.length} tools allowed`);
    _loadToolBreakdown(agentId);
  } catch(e) { showToast('Error: ' + e.message, true); }
};

window._clearMcpFilter = async function(agentId) {
  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
    const cfg = JSON.parse(res.content || '{}');
    if (cfg.token_config) { cfg.token_config.mcp_tool_filter = null; }
    await API.post(`/v1/agents/${encodeURIComponent(agentId)}/file`, {
      name: 'agent.json',
      content: JSON.stringify(cfg, null, 2)
    });
    showToast('Filter cleared');
    _loadToolBreakdown(agentId);
  } catch(e) { showToast('Error: ' + e.message, true); }
};

// --- MCP management helpers ---
// Shared inline styles (accent = #d97757 brand orange)
const _mcpInput = 'width:100%;box-sizing:border-box;background:#f5f4ed;border:1px solid rgba(31,30,29,0.08);border-radius:6px;padding:7px 10px;font-size:12px;color:#141413;outline:none';
const _mcpBtn = 'font-size:11px;padding:5px 14px;border-radius:6px;background:#d97757;color:#fff;border:none;cursor:pointer;font-weight:500;white-space:nowrap';
const _mcpBtnSec = 'font-size:11px;padding:5px 14px;border-radius:6px;background:#e8e7e0;color:#73726c;border:none;cursor:pointer';
const _mcpBadge = 'font-size:9px;padding:2px 6px;border-radius:4px;display:inline-block;';

window._showAgentMcpAdd = function(agentId) {
  const form = document.getElementById('agent-mcp-add-form');
  if (!form) return;
  if (form.innerHTML.trim()) { form.innerHTML = ''; return; } // toggle
  form.innerHTML = `
    <div style="border:1px solid rgba(31,30,29,0.08);border-radius:8px;padding:14px;display:grid;gap:10px;background:rgba(0,0,0,0.015)">
      <span style="font-size:13px;font-weight:600;color:#141413">Add MCP Server Manually</span>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <label style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Name</label>
          <input id="amcp-name" style="${_mcpInput};margin-top:4px" placeholder="e.g. github">
        </div>
        <div>
          <label style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Transport</label>
          <select id="amcp-transport" style="${_mcpInput};margin-top:4px" onchange="document.getElementById('amcp-url-label').textContent=this.value==='stdio'?'COMMAND':'URL';document.getElementById('amcp-url').placeholder=this.value==='stdio'?'npx -y @modelcontextprotocol/server-github':'https://...'">
            <option value="stdio">stdio (command)</option>
            <option value="sse">SSE (HTTP)</option>
          </select>
        </div>
      </div>
      <div>
        <label id="amcp-url-label" style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Command</label>
        <input id="amcp-url" style="${_mcpInput};font-family:var(--font-mono);margin-top:4px" placeholder="npx -y @modelcontextprotocol/server-github">
      </div>
      <div>
        <label style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Args (space-separated, for stdio)</label>
        <input id="amcp-args" style="${_mcpInput};font-family:var(--font-mono);margin-top:4px" placeholder="--arg1 value1">
      </div>
      <div style="display:flex;gap:8px;margin-top:2px">
        <button style="${_mcpBtn}" onclick="_doAgentMcpAdd('${esc(agentId)}')">Add Server</button>
        <button style="${_mcpBtnSec}" onclick="document.getElementById('agent-mcp-add-form').innerHTML=''">Cancel</button>
      </div>
    </div>`;
};

window._doAgentMcpAdd = async function(agentId) {
  const name = document.getElementById('amcp-name')?.value?.trim();
  const transport = document.getElementById('amcp-transport')?.value;
  const url = document.getElementById('amcp-url')?.value?.trim();
  const argsStr = document.getElementById('amcp-args')?.value?.trim();
  if (!name || !url) { showToast('Name and command/URL required', true); return; }

  let mcp = {};
  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=mcp.json`);
    const raw = typeof res === 'string' ? res : (res.content || '{}');
    mcp = JSON.parse(raw);
  } catch {}

  if (!mcp.mcpServers) mcp = { mcpServers: mcp };
  if (!mcp.mcpServers) mcp.mcpServers = {};

  if (transport === 'stdio') {
    const parts = url.split(/\s+/);
    const cmd = parts[0];
    const args = parts.slice(1);
    if (argsStr) args.push(...argsStr.split(/\s+/));
    mcp.mcpServers[name] = { command: cmd, args };
  } else {
    mcp.mcpServers[name] = { transport: 'sse', url };
  }

  try {
    await API.post(`/v1/agents/${encodeURIComponent(agentId)}/file`, { name: 'mcp.json', content: JSON.stringify(mcp, null, 2) });
    showToast(`Added ${name}`);
    switchAgentTab(agentId, 'mcp');
  } catch (e) { showToast(e.message, true); }
};

window._removeAgentMcp = async function(agentId, serverName) {
  if (!await showConfirmDanger(`Remove MCP server "${serverName}"?`, 'Remove MCP Server', 'Remove')) return;
  let mcp = {};
  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=mcp.json`);
    const raw = typeof res === 'string' ? res : (res.content || '{}');
    mcp = JSON.parse(raw);
  } catch {}

  if (mcp.mcpServers) delete mcp.mcpServers[serverName];
  else delete mcp[serverName];

  try {
    await API.post(`/v1/agents/${encodeURIComponent(agentId)}/file`, { name: 'mcp.json', content: JSON.stringify(mcp, null, 2) });
    showToast(`Removed ${serverName}`);
    switchAgentTab(agentId, 'mcp');
  } catch (e) { showToast(e.message, true); }
};

// --- MCP Registry browse helpers ---
window._searchMcpRegistry = async function(agentId) {
  const query = document.getElementById('mcp-registry-search')?.value?.trim() || '';
  const container = document.getElementById('mcp-registry-results');
  const searchBtn = document.getElementById('mcp-registry-search-btn');
  if (!container) return;
  if (!query) {
    container.innerHTML = '<div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Type to search the official MCP server registry</div>';
    return;
  }

  // Show loading state
  container.innerHTML = `<div style="padding:20px;text-align:center">
    <div style="display:inline-block;width:18px;height:18px;border:2px solid #e8e7e0;border-top-color:#d97757;border-radius:50%;animation:spin 0.6s linear infinite"></div>
    <div style="margin-top:8px;color:#73726c;font-size:12px">Searching registry for "${esc(query)}"...</div>
  </div>`;
  if (searchBtn) { searchBtn.textContent = '...'; searchBtn.disabled = true; }

  // Load current config to mark already-installed
  let installedNames = new Set();
  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=mcp.json`);
    const raw = typeof res === 'string' ? res : (res.content || '{}');
    const mcp = JSON.parse(raw);
    const servers = mcp.mcpServers || mcp;
    Object.keys(servers).forEach(n => installedNames.add(n));
  } catch {}
  if (agentId !== 'main') {
    try {
      const res = await API.get(`/v1/agents/main/file?name=mcp.json`);
      const raw = typeof res === 'string' ? res : (res.content || '{}');
      const mcp = JSON.parse(raw);
      const servers = mcp.mcpServers || mcp;
      Object.keys(servers).forEach(n => installedNames.add(n));
    } catch {}
  }

  try {
    const data = await API.get(`/v1/mcp/registry?q=${encodeURIComponent(query)}&limit=20`);
    const servers = data.servers || [];
    if (!servers.length) {
      container.innerHTML = `<div style="padding:24px;text-align:center;color:#73726c;font-size:12px">No servers found for "${esc(query)}". Try a different search term.</div>`;
      return;
    }
    window._mcpRegistryResults = servers;

    container.innerHTML = `<div style="font-size:11px;color:#a1a09a;margin-bottom:2px">${servers.length} server${servers.length>1?'s':''} found</div>` + servers.map((s, idx) => {
      const shortName = (s.name || '').split('/').pop().replace(/^server-/, '');
      const alreadyInstalled = installedNames.has(shortName) || installedNames.has(s.name);
      const typeBadge = s.registry_type ? `<span style="${_mcpBadge}background:rgba(234,179,8,0.15);color:#b45309">${esc(s.registry_type)}</span>` : '';
      const transportBadge = `<span style="${_mcpBadge}background:rgba(59,130,246,0.12);color:#3b82f6">${esc(s.transport || 'stdio')}</span>`;
      const envBadge = s.env_vars?.length ? `<span style="${_mcpBadge}background:rgba(239,68,68,0.1);color:#dc2626">${s.env_vars.length} env</span>` : '';
      const installBtn = alreadyInstalled
        ? `<span style="font-size:10px;color:#16a34a;padding:3px 10px;background:rgba(22,163,74,0.08);border-radius:5px;white-space:nowrap">Added</span>`
        : `<button style="${_mcpBtn};padding:4px 12px;font-size:11px" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="_installFromRegistry('${esc(agentId)}',${idx},this)">Add</button>`;
      return `
        <div class="mcp-result-card" style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px;transition:background 0.15s" onmouseover="this.style.background='rgba(0,0,0,0.02)'" onmouseout="this.style.background='transparent'">
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
              <span style="font-size:13px;font-weight:600;color:#141413">${esc(shortName)}</span>
              ${typeBadge}${transportBadge}${envBadge}
            </div>
            <div style="font-size:11px;color:#555;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((s.description||'').slice(0,120))}</div>
            <div style="font-size:10px;color:#a1a09a;font-family:var(--font-mono);margin-top:2px">${esc(s.command || '')} ${esc((s.args||[]).join(' '))}</div>
          </div>
          <div style="flex-shrink:0">${installBtn}</div>
        </div>`;
    }).join('');
  } catch (e) {
    container.innerHTML = `<div style="padding:16px;text-align:center;color:#dc2626;font-size:12px">Error searching registry: ${esc(e.message)}</div>`;
  } finally {
    if (searchBtn) { searchBtn.textContent = 'Search'; searchBtn.disabled = false; }
  }
};

window._installFromRegistry = async function(agentId, idx, btn) {
  const s = window._mcpRegistryResults?.[idx];
  if (!s) return;

  const requiredEnv = (s.env_vars || []).filter(e => e.required);
  const requiredArgs = (s.pkg_args || []).filter(a => a.required);
  if ((requiredEnv.length || requiredArgs.length) && !btn.dataset.envDone) {
    const formId = `mcp-env-form-${idx}`;
    const existing = document.getElementById(formId);
    if (existing) { existing.remove(); return; }
    let fields = '';
    if (requiredEnv.length) {
      fields += '<div style="font-size:11px;font-weight:600;color:#141413">Environment variables:</div>';
      fields += requiredEnv.map(e => `
        <div>
          <label style="font-size:10px;color:#73726c">${esc(e.name)}${e.description ? ' — ' + esc(e.description) : ''}</label>
          <input data-env="${esc(e.name)}" style="${_mcpInput};font-family:var(--font-mono);margin-top:2px" placeholder="${esc(e.name)}">
        </div>
      `).join('');
    }
    if (requiredArgs.length) {
      fields += '<div style="font-size:11px;font-weight:600;color:#141413;margin-top:4px">Arguments:</div>';
      fields += requiredArgs.map(a => `
        <div>
          <label style="font-size:10px;color:#73726c">--${esc(a.name)}${a.description ? ' — ' + esc(a.description) : ''}</label>
          <input data-arg="${esc(a.name)}" style="${_mcpInput};font-family:var(--font-mono);margin-top:2px" placeholder="${esc(a.name)}">
        </div>
      `).join('');
    }
    const formHtml = `
      <div id="${formId}" style="margin-top:8px;padding:12px;border:1px solid rgba(217,119,87,0.3);border-radius:8px;display:grid;gap:8px;background:rgba(217,119,87,0.03)">
        <div style="font-size:12px;font-weight:600;color:#141413">Configure before adding</div>
        ${fields}
        <button style="${_mcpBtn};justify-self:start" onclick="window._doRegistryInstall('${esc(agentId)}',${idx},'${formId}')">Add server</button>
      </div>`;
    btn.closest('.mcp-result-card')?.insertAdjacentHTML('afterend', formHtml);
    return;
  }

  window._doRegistryInstall(agentId, idx, null, btn);
};

window._doRegistryInstall = async function(agentId, idx, formId, btn) {
  const s = window._mcpRegistryResults?.[idx];
  if (!s) return;

  const shortName = (s.name || '').split('/').pop().replace(/^server-/, '');

  // Show adding state
  if (btn) { btn.textContent = 'Adding...'; btn.disabled = true; btn.style.opacity = '0.7'; }

  let env = {};
  let extraArgs = [];
  if (formId) {
    const form = document.getElementById(formId);
    if (form) {
      form.querySelectorAll('input[data-env]').forEach(inp => {
        if (inp.value.trim()) env[inp.dataset.env] = inp.value.trim();
      });
      form.querySelectorAll('input[data-arg]').forEach(inp => {
        if (inp.value.trim()) extraArgs.push(`--${inp.dataset.arg}`, inp.value.trim());
      });
    }
  }

  let mcp = {};
  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=mcp.json`);
    const raw = typeof res === 'string' ? res : (res.content || '{}');
    mcp = JSON.parse(raw);
  } catch {}

  if (!mcp.mcpServers) mcp = { mcpServers: mcp };
  if (!mcp.mcpServers) mcp.mcpServers = {};

  const entry = {};
  if (s.transport === 'stdio' || !s.transport) {
    entry.command = s.command || 'npx';
    entry.args = [...(s.args || []), ...extraArgs];
  } else {
    entry.transport = s.transport;
    entry.url = s.command || '';
  }
  if (Object.keys(env).length) entry.env = env;
  mcp.mcpServers[shortName] = entry;

  try {
    await API.post(`/v1/agents/${encodeURIComponent(agentId)}/file`, { name: 'mcp.json', content: JSON.stringify(mcp, null, 2) });
    showToast(`Added ${shortName}`);
    // Remove config form if present
    if (formId) document.getElementById(formId)?.remove();
    // Update button to show "Added"
    if (btn) {
      btn.outerHTML = `<span style="font-size:10px;color:#16a34a;padding:3px 10px;background:rgba(22,163,74,0.08);border-radius:5px;white-space:nowrap">Added</span>`;
    }
    // Refresh configured servers list at top
    _refreshMcpServerList(agentId);
  } catch (e) {
    showToast(e.message, true);
    if (btn) { btn.textContent = 'Add'; btn.disabled = false; btn.style.opacity = '1'; }
  }
};

// Refresh just the configured servers list without destroying search results
window._refreshMcpServerList = async function(agentId) {
  const listEl = document.getElementById('agent-mcp-list');
  if (!listEl) return;
  let agentMcp = {}, mainMcp = {}, liveStatus = {};
  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=mcp.json`);
    const raw = typeof res === 'string' ? res : (res.content || '{}');
    agentMcp = JSON.parse(raw);
    if (agentMcp.mcpServers) agentMcp = agentMcp.mcpServers;
  } catch {}
  if (agentId !== 'main') {
    try {
      const res = await API.get(`/v1/agents/main/file?name=mcp.json`);
      const raw = typeof res === 'string' ? res : (res.content || '{}');
      mainMcp = JSON.parse(raw);
      if (mainMcp.mcpServers) mainMcp = mainMcp.mcpServers;
    } catch {}
  }
  try {
    const data = await API.get('/v1/mcp/connections');
    const connections = data.connections || data || [];
    if (Array.isArray(connections)) {
      for (const c of connections) {
        const n = c.name || c.server || '';
        liveStatus[n] = { connected: c.connected || c.status === 'connected', tools_count: c.tools_count || 0 };
      }
    }
  } catch {}
  const allServers = {};
  for (const [name, cfg] of Object.entries(mainMcp)) allServers[name] = { ...cfg, _source: 'main' };
  for (const [name, cfg] of Object.entries(agentMcp)) allServers[name] = { ...cfg, _source: agentId };
  if (!Object.keys(allServers).length) {
    listEl.innerHTML = '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">No MCP servers configured.</div>';
    return;
  }
  let html = '';
  for (const [name, cfg] of Object.entries(allServers)) {
    const inherited = cfg._source === 'main' && agentId !== 'main';
    const transport = cfg.transport || cfg.type || (cfg.command ? 'stdio' : cfg.url ? 'sse' : '?');
    const target = cfg.command ? `${cfg.command} ${(cfg.args||[]).join(' ')}` : cfg.url || '';
    const live = liveStatus[name];
    const dot = live ? `<span style="width:7px;height:7px;border-radius:50%;background:${live.connected?'#16a34a':'#a1a09a'};flex-shrink:0"></span>` : '<span style="width:7px;height:7px;border-radius:50%;background:#d4d4d0;flex-shrink:0"></span>';
    const tools = live && live.tools_count ? `<span style="${_mcpBadge}background:rgba(22,163,74,0.1);color:#16a34a">${live.tools_count} tools</span>` : '';
    const src = inherited ? `<span style="${_mcpBadge}background:rgba(120,120,140,0.15);color:#73726c">inherited</span>` : `<span style="${_mcpBadge}background:rgba(139,92,246,0.15);color:#8b5cf6">local</span>`;
    const tp = `<span style="${_mcpBadge}background:rgba(59,130,246,0.12);color:#3b82f6">${esc(transport)}</span>`;
    const rm = !inherited ? `<button style="font-size:11px;color:#dc2626;padding:3px 10px;border-radius:5px;cursor:pointer;background:rgba(220,38,38,0.08);border:none;white-space:nowrap" onclick="_removeAgentMcp('${esc(agentId)}','${esc(name)}')">Remove</button>` : '';
    html += `<div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px">${dot}<div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span style="font-size:13px;font-weight:600;color:#141413">${esc(name)}</span>${tp} ${src} ${tools}</div><div style="font-size:11px;color:#73726c;font-family:var(--font-mono);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(target.slice(0,100))}</div></div>${rm}</div>`;
  }
  listEl.innerHTML = html;
};

// --- Hooks management helpers ---
function _renderHookRow(h, idx) {
  const name = h.name || 'unnamed';
  const type = h.type || 'pre';
  const tools = Array.isArray(h.tools) ? h.tools.join(', ') : (h.tools || '*');
  const enabled = h.enabled !== false;
  const typeBg = type === 'pre' ? 'rgba(234,179,8,0.15)' : type === 'post' ? 'rgba(59,130,246,0.15)' : 'rgba(139,92,246,0.15)';
  const typeColor = type === 'pre' ? '#ca8a04' : type === 'post' ? '#3b82f6' : '#8b5cf6';
  return `<div data-hook-idx="${idx}" style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;opacity:${enabled?1:0.5}">
    <input type="checkbox" ${enabled?'checked':''} onchange="_hookToggle(${idx},this.checked)" title="Enable/disable">
    <span style="font-size:13px;font-family:var(--font-mono);color:var(--text-100);flex:1">${esc(name)}</span>
    <span style="font-size:11px;padding:2px 6px;border-radius:4px;background:${typeBg};color:${typeColor};font-weight:500">${esc(type)}</span>
    <span style="font-size:11px;color:var(--text-400);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(tools)}">${esc(tools)}</span>
    <button onclick="_hookEdit(${idx})" style="background:none;border:none;cursor:pointer;padding:2px 4px" title="Edit">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-400)" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
    </button>
    <button onclick="_hookDelete(${idx})" style="background:none;border:none;cursor:pointer;padding:2px 4px" title="Delete">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-400)" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
    </button>
  </div>`;
}

function _hookToggle(idx, checked) {
  if (!window._hooksData) return;
  window._hooksData.scripts[idx].enabled = checked;
  const row = document.querySelector(`[data-hook-idx="${idx}"]`);
  if (row) row.style.opacity = checked ? 1 : 0.5;
}

function _hookDelete(idx) {
  if (!window._hooksData) return;
  window._hooksData.scripts.splice(idx, 1);
  _hookRerenderList();
}

function _hookRerenderList() {
  const list = document.getElementById('hooks-list');
  if (!list) return;
  const scripts = window._hooksData.scripts;
  if (!scripts.length) {
    list.innerHTML = '<div id="hooks-empty" style="padding:20px;text-align:center;color:var(--text-400)">No hook scripts configured</div>';
  } else {
    list.innerHTML = scripts.map((s, i) => _renderHookRow(s, i)).join('');
  }
}

window._hookEditIdx = -1;

function _hookAdd() {
  window._hookEditIdx = -1;
  document.getElementById('hook-form-title').textContent = 'Add Hook';
  document.getElementById('hook-f-name').value = '';
  document.getElementById('hook-f-type').value = 'pre';
  document.getElementById('hook-f-script').value = '';
  document.getElementById('hook-f-tools').value = '*';
  document.getElementById('hook-f-enabled').checked = true;
  document.getElementById('hook-form').style.display = 'block';
}

function _hookEdit(idx) {
  const h = window._hooksData?.scripts?.[idx];
  if (!h) return;
  window._hookEditIdx = idx;
  document.getElementById('hook-form-title').textContent = 'Edit Hook';
  document.getElementById('hook-f-name').value = h.name || '';
  document.getElementById('hook-f-type').value = h.type || 'pre';
  document.getElementById('hook-f-script').value = h.script || '';
  document.getElementById('hook-f-tools').value = Array.isArray(h.tools) ? h.tools.join(', ') : (h.tools || '*');
  document.getElementById('hook-f-enabled').checked = h.enabled !== false;
  document.getElementById('hook-form').style.display = 'block';
}

function _hookFormSave() {
  const name = document.getElementById('hook-f-name').value.trim();
  const type = document.getElementById('hook-f-type').value;
  const script = document.getElementById('hook-f-script').value.trim();
  const toolsRaw = document.getElementById('hook-f-tools').value.trim();
  const enabled = document.getElementById('hook-f-enabled').checked;

  if (!name) { showToast('Hook name is required', true); return; }
  if (!script) { showToast('Script path is required', true); return; }

  const tools = toolsRaw.split(',').map(t => t.trim()).filter(Boolean);
  const hookObj = { name, type, script, tools: tools.length ? tools : ['*'], enabled };

  if (window._hookEditIdx >= 0) {
    window._hooksData.scripts[window._hookEditIdx] = hookObj;
  } else {
    window._hooksData.scripts.push(hookObj);
  }

  document.getElementById('hook-form').style.display = 'none';
  _hookRerenderList();
}

async function _hooksSave() {
  const agentId = window._hooksAgentId;
  if (!agentId || !window._hooksData) return;
  try {
    await API.post(`/v1/agents/${agentId}/hooks`, window._hooksData);
    showToast('Hooks saved');
  } catch(e) {
    showToast('Save failed: ' + e.message, true);
  }
}



// --- Schedule tab helpers ---
function _schedShowForm() {
  const f = document.getElementById('sched-form');
  if (f) f.style.display = 'block';
}

// Inline AI refinement controls for scheduled-task prompt textareas.
// Caveman picker lives here because a scheduled task has no chat-level
// caveman toggle to inherit from — the picker IS the equivalent setting
// for that task's prompt. Anywhere else (profile fields, long-form
// profile editor) the chat composer's caveman is the single setting.
function _schedRefineControls(textareaId) {
  return `
    <span style="display:inline-flex;align-items:center;gap:6px">
      ${_refineCavemanButton(textareaId)}
      <button type="button" id="${textareaId}-refine"
        onclick="refineSchedPrompt('${textareaId}')"
        title="Refine this scheduled-task prompt with AI"
        style="background:none;border:1px solid var(--border-light);border-radius:4px;padding:2px 8px;font-size:11px;color:var(--text-300);cursor:pointer;display:inline-flex;align-items:center;gap:4px">
        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
          <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
          <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
        </svg>
        <span id="${textareaId}-refine-label">Refine with AI</span>
      </button>
    </span>`;
}

async function refineSchedPrompt(textareaId) {
  const ta = document.getElementById(textareaId);
  if (!ta) return;
  const text = (ta.value || '').replace(/^\s+|\s+$/g, '');
  if (!text) { showToast('Type the prompt first', true); return; }
  const btn = document.getElementById(textareaId + '-refine');
  const lbl = document.getElementById(textareaId + '-refine-label');
  const caveman = _refineCavemanValue(textareaId);
  const origLabel = lbl?.textContent || 'Refine with AI';
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Refining…';
  ta.disabled = true;
  const original = ta.value;
  try {
    // Default purpose=chat_prompt — same rewrite rules as the composer
    // refine button, since a scheduled task prompt is a chat-style prompt
    // the agent will execute. No session_id (no history context).
    const result = await API.post('/v1/refine', { text, caveman });
    if (result && result.refined && result.refined !== text) {
      ta.value = result.refined;
      if (lbl) lbl.textContent = 'Undo';
      if (btn) {
        btn.disabled = false;
        const undoHandler = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          ta.value = original;
          if (lbl) lbl.textContent = origLabel;
          btn.removeEventListener('click', undoHandler);
          btn.onclick = () => refineSchedPrompt(textareaId);
        };
        btn.onclick = undoHandler;
      }
      showToast('Refined — click Undo to revert');
    } else {
      showToast('Already clean — no change');
      if (lbl) lbl.textContent = origLabel;
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('Refine failed: ' + (e.message || e), true);
    if (lbl) lbl.textContent = origLabel;
    if (btn) btn.disabled = false;
  } finally {
    ta.disabled = false;
  }
}

async function _schedAdd() {
  const agentId = window._schedAgentId;
  if (!agentId) return;
  const name = document.getElementById('sched-f-name')?.value?.trim();
  const task = document.getElementById('sched-f-task')?.value?.trim();
  const schedule = document.getElementById('sched-f-schedule')?.value?.trim();
  const model = document.getElementById('sched-f-model')?.value?.trim() || undefined;
  const timeout = parseInt(document.getElementById('sched-f-timeout')?.value) || 300;
  if (!name || !task || !schedule) { showToast('Name, task and schedule are required', true); return; }
  try {
    const res = await API.manageSchedule({ action: 'add', name, task, schedule, agent: agentId, model, timeout });
    if (res.error) { showToast(res.error, true); return; }
    showToast('Task created');
    switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _schedToggle(name, enable) {
  const agentId = window._schedAgentId;
  try {
    await API.manageSchedule({ action: enable ? 'resume' : 'pause', name });
    showToast(enable ? 'Resumed' : 'Paused');
    switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _schedDelete(name) {
  if (!await showConfirmDanger(`Delete scheduled task "${name}"?`, 'Delete Task', 'Delete')) return;
  const agentId = window._schedAgentId;
  try {
    await API.manageSchedule({ action: 'delete', name });
    showToast('Deleted');
    switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _schedCancel(name) {
  try {
    await API.cancelScheduledTask(name);
    showToast('Cancelling...');
    setTimeout(() => {
      const agentId = window._schedAgentId;
      if (agentId) switchAgentTab(agentId, 'schedule', document.querySelector('.modal-tab.active'));
    }, 1000);
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _schedHistory(name) {
  const panel = document.getElementById('sched-history');
  const title = document.getElementById('sched-history-title');
  const body = document.getElementById('sched-history-body');
  if (!panel || !body) return;
  title.textContent = `History: ${name}`;
  body.innerHTML = '<div style="color:var(--text-400)">Loading...</div>';
  panel.style.display = 'block';
  try {
    const res = await API.manageSchedule({ action: 'history', name, limit: 20 });
    const history = res.history || [];
    if (!history.length) { body.innerHTML = '<div style="color:var(--text-400)">No execution history</div>'; return; }
    let html = '';
    for (const h of history) {
      const ok = h.status === 'success' || h.status === 'completed';
      const started = h.started_at ? new Date(h.started_at+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
      const finished = h.finished_at ? new Date(h.finished_at+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
      html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-100)">
        <span style="width:6px;height:6px;border-radius:50%;background:${ok?'var(--success)':'var(--error)'};flex-shrink:0"></span>
        <span style="flex:1;color:var(--text-200)">${started}</span>
        <span style="color:var(--text-400)">${esc(h.status||'')}</span>
      </div>`;
    }
    body.innerHTML = html;
  } catch(e) { body.innerHTML = `<div style="color:var(--error)">${esc(e.message)}</div>`; }
}

// ═══ Scheduled Tasks View ═══

let _scheduledFilter = 'all';

async function loadScheduledView() {
  const container = document.getElementById('scheduled-list');
  if (!container) return;
  container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-400)">Loading...</div>';
  try {
    const schedData = await API.getSchedule();
    const schedules = schedData.schedules || [];
    const running = schedData.running || [];
    const runningNames = new Set(running.map(r => r.name));

    let html = '';

    const filteredSchedules = schedules.filter(s => {
      if (_scheduledFilter === 'running') return runningNames.has(s.name);
      return true; // 'all' or 'scheduled'
    });

    for (const s of filteredSchedules) {
      const isRunning = runningNames.has(s.name);
      const enabled = s.enabled !== 0;
      const badgeClass = isRunning ? 'running' : (enabled ? 'enabled' : 'paused');
      const badgeText = isRunning ? 'Running' : (enabled ? 'Active' : 'Paused');
      const nextRun = s.next_run ? new Date(s.next_run + (s.next_run.includes('Z') ? '' : 'Z')).toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
      const lastRun = s.last_run ? new Date(s.last_run + (s.last_run.includes('Z') ? '' : 'Z')).toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : 'never';
      const cardId = 'sched-card-' + btoa(s.name).replace(/[^a-zA-Z0-9]/g,'').slice(0,12);
      html += `<div class="sched-card" id="${cardId}">
        <div class="sched-card-header">
          <span class="sched-card-fav-slot" data-sched-fav="${esc(s.name)}"></span>
          <span class="sched-card-name">${esc(s.name)}</span>
          <span class="sched-card-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="sched-card-prompt">${esc(s.task || '')}</div>
        <div class="sched-card-meta">
          <span>Agent: ${esc(s.agent || 'main')}</span>
          <span>${esc(s.schedule || '')}</span>
          <span>Next: ${nextRun}</span>
          <span>Last: ${lastRun}</span>
        </div>
        <div class="sched-card-actions">
          ${isRunning
            ? `<button onclick="_schedViewCancel('${esc(s.name)}')">Cancel</button>`
            : `<button onclick="_schedViewRun('${esc(s.name)}')">Run Now</button>`}
          <button onclick="_schedViewToggle('${esc(s.name)}', ${!enabled})">${enabled ? 'Pause' : 'Resume'}</button>
          <button onclick="_schedViewEdit('${esc(s.name)}')">Edit</button>
          <button onclick="shareDialog('schedule','${esc(s.name)}','',{title:'${esc(s.name)}',onChange:loadScheduledView})">Share</button>
          <button class="danger" onclick="_schedViewDelete('${esc(s.name)}')">Delete</button>
        </div>
        <details class="sched-runs" ontoggle="if(this.open) _schedLoadRuns('${esc(s.name)}', this)">
          <summary><span>Run history</span></summary>
          <div class="runs-body"><div class="sched-run-row loading">Click to load…</div></div>
        </details>
      </div>`;
    }

    if (!html) {
      html = '<div style="padding:40px;text-align:center;color:var(--text-400)">No scheduled tasks. Click "New Task" to create one.</div>';
    }
    container.innerHTML = html;
    // Mount the star button in each schedule card.
    if (window.Favourites?.mount) {
      container.querySelectorAll('.sched-card-fav-slot').forEach(slot => {
        const name = slot.dataset.schedFav;
        if (!name) return;
        window.Favourites.mount(slot, {
          item_type: 'schedule',
          item_id: name,
          agent_id: 'main',
        });
      });
    }
  } catch(e) {
    container.innerHTML = `<div style="padding:20px;color:var(--error)">Failed to load: ${esc(e.message)}</div>`;
  }
}

function setScheduledFilter(filter, el) {
  _scheduledFilter = filter;
  document.querySelectorAll('.scheduled-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  loadScheduledView();
}

// Persistent schedule actions from the Scheduled view
async function _schedViewRun(name) {
  try {
    // Run now = delete and re-add with same config but "once" schedule for now
    // Actually, simplest: just trigger via the existing scheduler run mechanism
    // For now, we'll use the schedule modify to set next_run to now
    showToast('Running task...');
    // Trigger by cancelling and immediately re-scheduling isn't ideal.
    // Better approach: add a "run now" action to the scheduler
    await API.manageSchedule({ action: 'run_now', name });
    showToast('Task triggered');
    setTimeout(loadScheduledView, 1000);
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _schedViewToggle(name, enable) {
  try {
    await API.manageSchedule({ action: enable ? 'resume' : 'pause', name });
    showToast(enable ? 'Resumed' : 'Paused');
    loadScheduledView();
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _schedViewCancel(name) {
  try {
    await API.cancelScheduledTask(name);
    showToast('Cancelling...');
    setTimeout(loadScheduledView, 1000);
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _schedViewDelete(name) {
  if (!await showConfirmDanger(`Delete scheduled task "${name}"?`, 'Delete Task', 'Delete')) return;
  try {
    await API.manageSchedule({ action: 'delete', name });
    showToast('Deleted');
    loadScheduledView();
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

// Inline accordion body: fetch the last N runs for a schedule and render
// them as clickable rows with Open (loads the read-only chat view) and
// Delete. Called on first <details> toggle per card.
async function _schedLoadRuns(name, detailsEl) {
  const body = detailsEl.querySelector('.runs-body');
  if (!body) return;
  if (detailsEl._loaded) return;
  body.innerHTML = '<div class="sched-run-row loading">Loading run history…</div>';
  try {
    const res = await API.manageSchedule({ action: 'history', name, limit: 30 });
    const history = res.history || [];
    detailsEl._loaded = true;
    _schedRenderRuns(body, name, history);
  } catch(e) {
    body.innerHTML = `<div class="sched-run-row loading" style="color:var(--error)">Failed: ${esc(e.message)}</div>`;
  }
}

function _schedRenderRuns(body, name, history) {
  if (!history.length) {
    body.innerHTML = '<div class="sched-run-row loading">No runs yet</div>';
    return;
  }
  let html = '';
  for (const h of history) {
    const ok = h.status === 'success' || h.status === 'completed';
    const running = h.status === 'running';
    const started = h.started_at ? new Date(h.started_at+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
    const statusColor = running ? '#3b82f6' : (ok ? '#10b981' : (h.status === 'timeout' ? '#f59e0b' : '#ef4444'));
    const durSec = (h.duration_ms != null) ? (h.duration_ms / 1000) : null;
    const durLabel = durSec == null ? '—' : (durSec < 1 ? `${Math.round(h.duration_ms)}ms` : (durSec < 60 ? `${durSec.toFixed(1)}s` : `${Math.floor(durSec/60)}m${Math.round(durSec%60).toString().padStart(2,'0')}s`));
    const toolLabel = (h.tool_calls == null) ? '—' : String(h.tool_calls);
    const agentId = h.agent || 'main';
    const runId = h.id;
    const deleteDisabled = running ? 'disabled title="Cannot delete a running task"' : '';
    html += `<div class="sched-run-row" data-run-id="${runId}">
      <span class="r-dot" style="background:${statusColor}"></span>
      <span class="r-time">${started}</span>
      <span class="r-dur">${durLabel}</span>
      <span class="r-tools">${toolLabel} tools</span>
      <span class="r-status" style="color:${statusColor}">${esc(h.status||'?')}</span>
      <span class="r-actions">
        <button onclick="openScheduledArtifact(${runId}, 'sched-${runId}', '${esc(agentId)}', null)">Open</button>
        <button onclick="_schedViewRunDetail(${runId})">Details</button>
        <button class="danger" ${deleteDisabled} onclick="_schedDeleteRun('${esc(name)}', ${runId}, this)">Delete</button>
      </span>
    </div>`;
  }
  html += `<div class="sched-runs-footer">
    <button class="danger" onclick="_schedClearHistory('${esc(name)}', this)">Clear all history</button>
  </div>`;
  body.innerHTML = html;
}

async function _schedDeleteRun(name, runId, btn) {
  if (!await showConfirmDanger(`Delete run #${runId}?\n\nThis removes the history row and every artifact produced by this run (files included).`, 'Delete Run', 'Delete')) return;
  btn.disabled = true;
  try {
    const res = await API.manageSchedule({ action: 'delete_run', run_id: runId });
    if (res && res.error) { showToast(res.error, true); btn.disabled = false; return; }
    showToast(`Run deleted · ${res.artifacts_removed||0} artifact(s) purged`);
    // Reload this card's history inline.
    const detailsEl = btn.closest('details.sched-runs');
    if (detailsEl) {
      detailsEl._loaded = false;
      _schedLoadRuns(name, detailsEl);
    }
  } catch(e) {
    showToast('Failed: ' + e.message, true);
    btn.disabled = false;
  }
}

async function _schedClearHistory(name, btn) {
  if (!await showConfirmDanger(`Clear ALL run history for "${name}"?\n\nThis removes every past run and all produced artifacts.\nThe schedule itself stays in place.`, 'Clear History', 'Clear All')) return;
  btn.disabled = true;
  try {
    const res = await API.manageSchedule({ action: 'clear_history', name });
    showToast(`Cleared · ${res.runs_removed||0} runs · ${res.artifacts_removed||0} artifacts`);
    const detailsEl = btn.closest('details.sched-runs');
    if (detailsEl) {
      detailsEl._loaded = false;
      _schedLoadRuns(name, detailsEl);
    }
  } catch(e) {
    showToast('Failed: ' + e.message, true);
    btn.disabled = false;
  }
}

async function _schedViewRunDetail(runId) {
  // Per-run deep view: start/finish times, duration, tools with timings,
  // artifacts produced, and the agent's result text. Pivots:
  //   schedule_history.id === run_id === session_id suffix
  //   trace_id or session_id → traces.db
  //   artifact_folder → agents/<id>/artifacts/<folder>/
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="width:720px;max-height:82vh;display:flex;flex-direction:column">
    <div id="sched-runstate" style="overflow:auto;flex:1">Loading…</div>
    <div style="margin-top:12px;text-align:right;flex-shrink:0">
      <button onclick="this.closest('.sched-modal-overlay').remove()" style="padding:6px 16px;border-radius:8px;background:var(--bg-200);color:var(--text-200);font-size:13px">Close</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  try {
    const res = await API.manageSchedule({ action: 'run_detail', run_id: runId });
    if (res.error) {
      document.getElementById('sched-runstate').innerHTML = `<div style="color:var(--error)">${esc(res.error)}</div>`;
      return;
    }
    const run = res.run || {};
    const spans = res.spans || [];
    const artifacts = res.artifacts || [];
    const ok = run.status === 'success' || run.status === 'completed';
    const running = run.status === 'running';
    const statusColor = running ? '#3b82f6' : (ok ? '#10b981' : (run.status === 'timeout' ? '#f59e0b' : '#ef4444'));
    const started = run.started_at ? new Date(run.started_at+'Z').toLocaleString() : '—';
    const finished = run.finished_at ? new Date(run.finished_at+'Z').toLocaleString() : '—';
    const durSec = (run.duration_ms != null) ? (run.duration_ms / 1000) : null;
    const durLabel = durSec == null ? '—' : (durSec < 60 ? `${durSec.toFixed(1)}s` : `${Math.floor(durSec/60)}m${Math.round(durSec%60).toString().padStart(2,'0')}s`);

    // Tool span timeline — just tool_call spans, ordered by start.
    const toolSpans = spans.filter(s => s.type === 'tool_call').sort((a,b) => (a.started_at||'').localeCompare(b.started_at||''));
    const llmSpans = spans.filter(s => s.type === 'llm_call').sort((a,b) => (a.started_at||'').localeCompare(b.started_at||''));
    const tokensIn = spans.reduce((a,s) => a + (s.tokens_in||0), 0);
    const tokensOut = spans.reduce((a,s) => a + (s.tokens_out||0), 0);

    let html = `<h3 style="margin:0 0 4px;font-size:17px;color:var(--text-000)">Run #${runId} · ${esc(run.schedule_name||'')}</h3>
    <div style="color:var(--text-400);font-size:12px;margin-bottom:12px"><span style="color:${statusColor};font-weight:500">${esc(run.status||'?')}</span> · ${esc(run.agent||'main')}${run.model ? ` · ${esc(run.model)}` : ''}</div>`;

    // Stats block
    html += `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px;padding:10px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Duration</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${durLabel}</div></div>
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Tools</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${run.tool_calls ?? 0}</div></div>
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Tokens in</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${tokensIn.toLocaleString()}</div></div>
      <div><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em">Tokens out</div><div style="font-size:15px;color:var(--text-100);font-family:var(--font-mono)">${tokensOut.toLocaleString()}</div></div>
    </div>`;

    // Times
    html += `<div style="font-size:12px;color:var(--text-300);margin-bottom:14px;font-family:var(--font-mono)">Started ${esc(started)} · Finished ${esc(finished)}</div>`;

    // Task prompt
    if (run.task) {
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px">Task</div><div style="padding:8px;background:var(--bg-100);border-radius:6px;font-size:13px;color:var(--text-200);white-space:pre-wrap">${esc(run.task)}</div></div>`;
    }

    // Tool timeline — each row expandable to show the full result summary
    // (which can expose, e.g., an exa_search returning an HTTP 400 with an
    // empty query, or a python_exec where the script's stdout reveals the
    // agent admitting it couldn't read an artifact).
    if (toolSpans.length) {
      const t0 = toolSpans[0].started_at ? new Date(toolSpans[0].started_at).getTime() : 0;
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Tool calls (${toolSpans.length})</div>`;
      for (const s of toolSpans) {
        const sOk = s.status === 'success' || s.status === 'ok';
        const dotColor = sOk ? '#10b981' : '#ef4444';
        const dur = s.duration_ms != null ? `${s.duration_ms}ms` : '—';
        let meta = {};
        try { meta = s.metadata ? JSON.parse(s.metadata) : {}; } catch(e) {}
        const fullSummary = (meta.result_summary || '').toString();
        const shortSummary = fullSummary.length > 140 ? fullSummary.slice(0,140) + '…' : fullSummary;
        const tOffset = s.started_at && t0
          ? `+${((new Date(s.started_at).getTime() - t0)/1000).toFixed(1)}s`
          : '';
        const hasFull = fullSummary.length > 0;
        const sid = 'span-' + (s.id || Math.random().toString(36).slice(2));
        html += `<details style="border-bottom:1px dashed var(--border-100);padding:6px 0">
          <summary style="cursor:${hasFull?'pointer':'default'};list-style:none;display:flex;align-items:flex-start;gap:8px">
            <span style="width:8px;height:8px;border-radius:50%;background:${dotColor};flex-shrink:0;margin-top:5px"></span>
            <div style="flex:1;min-width:0">
              <div style="font-size:13px;color:var(--text-200);font-family:var(--font-mono);display:flex;gap:8px;align-items:baseline">
                <span>${esc(s.name||'?')}</span>
                ${tOffset ? `<span style="font-size:10px;color:var(--text-500)">${esc(tOffset)}</span>` : ''}
              </div>
              ${shortSummary ? `<div style="font-size:11px;color:var(--text-400);margin-top:2px;font-family:var(--font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(shortSummary)}</div>` : ''}
            </div>
            <span style="font-size:11px;color:var(--text-400);font-family:var(--font-mono);flex-shrink:0;margin-top:4px">${dur}</span>
          </summary>
          ${hasFull ? `<pre style="margin:6px 0 6px 18px;padding:8px;background:var(--bg-100);border-radius:6px;font-size:11px;color:var(--text-300);white-space:pre-wrap;word-break:break-word;max-height:240px;overflow:auto">${esc(fullSummary)}</pre>` : ''}
        </details>`;
      }
      html += `</div>`;
    }

    // LLM rounds
    if (llmSpans.length) {
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">LLM rounds (${llmSpans.length})</div>`;
      for (const s of llmSpans) {
        const dur = s.duration_ms != null ? `${s.duration_ms}ms` : '—';
        html += `<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:12px;color:var(--text-300);font-family:var(--font-mono)">
          <span>${esc(s.name||'?')}</span>
          <span>${s.tokens_in||0} in / ${s.tokens_out||0} out · ${dur}</span>
        </div>`;
      }
      html += `</div>`;
    }

    // Artifacts — each row opens the file in the artifact panel (plus the
    // pseudo-chat timeline view) when it has a registered artifact_id.
    // Unregistered fallback rows (from folder scan) stay non-clickable.
    if (artifacts.length) {
      const sessionId = res.session_id || ('sched-' + runId);
      const agentId = run.agent || 'main';
      html += `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">Artifacts (${artifacts.length}) · <span style="font-family:var(--font-mono);text-transform:none">${esc(run.artifact_folder||'')}</span></div>`;
      for (const a of artifacts) {
        const kb = (a.size/1024).toFixed(1);
        const clickable = !!a.id;
        if (clickable) {
          html += `<div onclick="document.querySelector('.sched-modal-overlay').remove(); openArtifactFromBrowse('${esc(a.id)}', '${esc(sessionId)}', '${esc(agentId)}')" style="display:flex;justify-content:space-between;padding:6px 4px;font-size:12px;color:var(--accent-brand);font-family:var(--font-mono);cursor:pointer;border-radius:4px" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">
            <span>${esc(a.name)}</span><span style="color:var(--text-400)">${kb} KB${a.type ? ' · ' + esc(a.type) : ''}</span>
          </div>`;
        } else {
          html += `<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:12px;color:var(--text-400);font-family:var(--font-mono)" title="Unregistered file — cannot open in viewer">
            <span>${esc(a.name)}</span><span>${kb} KB</span>
          </div>`;
        }
      }
      html += `</div>`;
    }

    // Result text
    if (run.result) {
      html += `<details style="margin-bottom:8px"><summary style="cursor:pointer;font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;padding:4px 0">Result (${run.result.length} chars)</summary><pre style="margin-top:6px;padding:10px;background:var(--bg-100);border-radius:6px;font-size:12px;color:var(--text-200);white-space:pre-wrap;word-break:break-word;max-height:300px;overflow:auto">${esc(run.result)}</pre></details>`;
    }

    document.getElementById('sched-runstate').innerHTML = html;
  } catch(e) {
    document.getElementById('sched-runstate').innerHTML = `<div style="color:var(--error)">Failed: ${esc(e.message)}</div>`;
  }
}

// Format-aware option set for the per-task / per-model thinking dropdown.
// modelId='' or unknown → caller decides whether to render a generic set
// (schedule modal "Default model") or hide the control entirely.
//
// Returns { format, supported, options:[{value,label}], note }.
//   format: the model's thinking_format, '' if unknown.
//   supported: false when format='none' — the control should be disabled or hidden.
//   options: ordered list (value strings) for a <select>.
//   note: optional one-liner the caller can show as a hint.
function _thinkingOptionsForModel(modelId) {
  const mc = state.modelsConfig?.models || {};
  const cfg = modelId ? (mc[modelId] || {}) : null;
  const fmt = cfg ? (cfg.thinking_format || 'none') : '';
  // Unknown model (e.g. schedule modal with model="" Default): full set,
  // resolved at fire time. Caller should add an "Inherit" entry on top.
  if (!modelId || !cfg) {
    return {
      format: '', supported: true,
      options: [
        {value:'none',   label:'Off'},
        {value:'low',    label:'Low'},
        {value:'medium', label:'Medium'},
        {value:'high',   label:'High'},
      ],
      note: "Actual options depend on the model resolved at fire time.",
    };
  }
  if (fmt === 'none') {
    return {format: fmt, supported: false, options: [], note: "This model doesn't support reasoning."};
  }
  if (fmt === 'inline_tags') {
    // <think>...</think> models think unconditionally per turn; the only dial
    // most providers expose is on/off (chat_template_kwargs.enable_thinking on
    // oMLX-served Qwen3, etc.). No graduated levels.
    return {format: fmt, supported: true,
      options: [
        {value:'none', label:'Off'},
        {value:'high', label:'On'},
      ],
      note: "This model supports thinking on/off only — no graduated levels."};
  }
  if (fmt === 'mistral_blocks') {
    // Mistral API only accepts reasoning_effort: none|high.
    return {format: fmt, supported: true,
      options: [
        {value:'none', label:'Off'},
        {value:'high', label:'High'},
      ],
      note: "Mistral accepts only Off / High."};
  }
  // reasoning_field (Gemini 2.5 / DeepSeek-R1 / oMLX), openai_opaque (o-series)
  return {format: fmt, supported: true,
    options: [
      {value:'none',   label:'Off'},
      {value:'low',    label:'Low'},
      {value:'medium', label:'Medium'},
      {value:'high',   label:'High'},
    ]};
}

// Format-driven option set, but for the Models tab (no "Inherit" entry —
// this dropdown IS the per-model default). Returns null when format='none'
// so the caller can disable the control.
function _thinkingOptionsForFormat(fmt) {
  if (fmt === 'none' || !fmt) return null;
  if (fmt === 'inline_tags') {
    return {options: [{value:'none',label:'Off'},{value:'high',label:'On'}],
            note: "Thinking on/off only — no graduated levels."};
  }
  if (fmt === 'mistral_blocks') {
    return {options: [{value:'none',label:'Off'},{value:'high',label:'High'}],
            note: "Mistral accepts only Off / High."};
  }
  return {options: [
    {value:'none',label:'Off'},
    {value:'low',label:'Low'},
    {value:'medium',label:'Medium'},
    {value:'high',label:'High'},
  ]};
}

// Render a Models-tab Thinking Level <select> for a given format. selectedValue
// is the current saved level ('' = unset/inherit-API-default).
function _mdlPopulateThinkingLevel(fmt, levelSel, selectedValue) {
  if (!levelSel) return;
  const info = _thinkingOptionsForFormat(fmt);
  if (!info) {
    levelSel.innerHTML = `<option value="" selected>(unsupported)</option>`;
    levelSel.disabled = true;
    levelSel.title = "This model doesn't support reasoning.";
    return;
  }
  levelSel.disabled = false;
  levelSel.title = info.note || '';
  const allowed = new Set(['', ...info.options.map(o => o.value)]);
  const cur = allowed.has(selectedValue) ? selectedValue : '';
  // "(unset)" preserves the API default — same as deleting the key from
  // inference. Distinct from "Off" which sends thinking_level=none explicitly.
  const opts = [{value:'',label:'(unset)'}, ...info.options];
  levelSel.innerHTML = opts.map(o =>
    `<option value="${esc(o.value)}"${o.value === cur ? ' selected' : ''}>${esc(o.label)}</option>`
  ).join('');
}

// Called by the Models-tab Thinking Format <select> onchange. Finds the
// sibling level select in the same row and re-renders its options for the
// new format, preserving the user's level choice when still valid.
function _mdlRefreshThinkingLevel(fmtSelectEl) {
  // Format and level are both <div> children of the same flex row's grid
  // container. Find the level by class within the same parent grid.
  const grid = fmtSelectEl.closest('div[style*="grid-template-columns"]');
  const levelSel = grid?.querySelector('.mdl-thinking-level');
  if (!levelSel) return;
  _mdlPopulateThinkingLevel(fmtSelectEl.value || 'none', levelSel, levelSel.value || '');
}

// Re-render a schedule-modal thinking dropdown when the model selector
// changes. Includes the "Inherit from model" entry that's specific to
// scheduled tasks (model defaults win).
function _schedRefreshThinking(modelSelectId, thinkingSelectId, currentValue) {
  const modelSel = document.getElementById(modelSelectId);
  const ts = document.getElementById(thinkingSelectId);
  if (!ts) return;
  const modelId = modelSel?.value || '';
  const info = _thinkingOptionsForModel(modelId);
  // Preserve the user's prior choice when possible. If the new option set
  // doesn't include it, fall back to "" (Inherit).
  const prev = (currentValue !== undefined ? currentValue : ts.value) || '';
  const allowed = new Set(['', ...info.options.map(o => o.value)]);
  const keep = allowed.has(prev) ? prev : '';
  if (!info.supported) {
    ts.innerHTML = `<option value="" selected>(unsupported)</option>`;
    ts.disabled = true;
    ts.title = info.note || '';
    return;
  }
  ts.disabled = false;
  ts.title = info.note || '';
  const opts = [{value:'', label:'Inherit from model'}, ...info.options];
  ts.innerHTML = opts.map(o =>
    `<option value="${esc(o.value)}"${o.value === keep ? ' selected' : ''}>${esc(o.label)}</option>`
  ).join('');
}

async function _schedViewEdit(name) {
  // Inline edit modal: fetch the current task row, prefill form, PATCH via
  // action:'edit'. `schedule` is a free-form string (same format as create).
  let task;
  try {
    const data = await API.getSchedule();
    task = (data.schedules || []).find(s => s.name === name);
  } catch(e) { showToast('Failed to load: ' + e.message, true); return; }
  if (!task) { showToast('Task not found', true); return; }

  const agentOpts = (state.agents || []).map(a =>
    `<option value="${esc(a.id)}" ${a.id === task.agent ? 'selected' : ''}>${esc(a.display_name || a.id)}</option>`
  ).join('');
  const modelOpts = (state.models || []).filter(m => modelHasCapability(m, 'chat')).map(m =>
    modelOption(m, {selected: m === task.model, label: m})
  ).join('');

  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal">
    <h2>Edit: ${esc(name)}</h2>
    <div class="sched-form-group">
      <label>Name</label>
      <input id="sched-edit-newname" value="${esc(task.name || '')}">
    </div>
    <div class="sched-form-group">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <label style="margin:0">Prompt</label>
        ${_schedRefineControls('sched-edit-prompt')}
      </div>
      <textarea id="sched-edit-prompt">${esc(task.task || '')}</textarea>
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Agent</label>
        <select id="sched-edit-agent">${agentOpts}</select>
      </div>
      <div class="sched-form-group">
        <label>Model</label>
        <select id="sched-edit-model" onchange="_schedRefreshThinking('sched-edit-model','sched-edit-thinking')"><option value="">Default</option>${modelOpts}</select>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Schedule <span style="color:var(--text-400);font-weight:normal;font-size:11px">(e.g. "daily 09:00", "every 30m", "weekly mon 14:00", "once 2026-05-01 12:00")</span></label>
      <input id="sched-edit-schedule" value="${esc(task.schedule || '')}" style="font-family:var(--font-mono)">
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Timeout (seconds)</label>
        <input id="sched-edit-timeout" type="number" value="${task.timeout || 300}" min="30" max="3600">
      </div>
      <div class="sched-form-group">
        <label>Thinking level <span style="color:var(--text-400);font-weight:normal;font-size:11px">(reasoning effort)</span></label>
        <select id="sched-edit-thinking"></select>
      </div>
      <div class="sched-form-group">
        <label>Caveman mode <span style="color:var(--text-400);font-weight:normal;font-size:11px">(response compression)</span></label>
        <select id="sched-edit-caveman">
          ${[[0,'Off'],[1,'Lite'],[2,'Full'],[3,'Ultra']].map(([v,lbl]) => {
            const sel = Number(task.caveman_chat || 0) === v ? 'selected' : '';
            return `<option value="${v}" ${sel}>${esc(lbl)}</option>`;
          }).join('')}
        </select>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Tool profile <span style="color:var(--text-400);font-weight:normal;font-size:11px">(which tools the agent sees on this task)</span></label>
      <select id="sched-edit-tool-profile">
        ${[
          ['','Default — research minimal (exa_search, web_fetch, write_file)'],
          ['research_minimal','Research minimal — same as default, explicit'],
          ['interactive','Interactive — full agent tool surface (chat-like)'],
        ].map(([v,lbl]) => {
          const sel = (task.tool_profile || '') === v ? 'selected' : '';
          return `<option value="${esc(v)}" ${sel}>${esc(lbl)}</option>`;
        }).join('')}
      </select>
    </div>
    <div class="sched-form-group">
      <label>Working directory <span style="color:var(--text-400);font-weight:normal;font-size:11px">(optional)</span></label>
      <div style="display:flex;gap:6px">
        <input id="sched-edit-workdir" value="${esc(task.working_dir || '')}" placeholder="(none)" readonly style="font-family:var(--font-mono);flex:1;background:var(--bg-100)">
        <button type="button" onclick="_schedOpenFolderPicker('sched-edit-workdir')" style="padding:6px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-200);cursor:pointer;font-size:12px">Browse…</button>
        <button type="button" onclick="document.getElementById('sched-edit-workdir').value=''" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-400);cursor:pointer;font-size:12px" title="Clear">×</button>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Attachments</label>
      <input id="sched-edit-files" type="file" multiple onchange="_schedUploadFiles(this, 'sched-edit-attlist')" style="font-size:12px">
      <div id="sched-edit-attlist" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px"></div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" onclick="_saveScheduledEdit('${esc(name)}')">Save</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  // Populate the thinking dropdown using the model the task is currently
  // bound to (or empty=Default → generic set with "Inherit" pre-selected).
  _schedRefreshThinking('sched-edit-model', 'sched-edit-thinking', task.thinking_level || '');
  // Seed the edit modal's attachment accumulator from the existing task row.
  try {
    const existing = task.attachments;
    let arr = [];
    if (Array.isArray(existing)) arr = existing;
    else if (typeof existing === 'string' && existing) arr = JSON.parse(existing);
    window._schedEditAttachments = arr || [];
  } catch(e) { window._schedEditAttachments = []; }
  _renderSchedAttList('sched-edit-attlist', '_schedEditAttachments');
}

async function _saveScheduledEdit(originalName) {
  const newName = document.getElementById('sched-edit-newname')?.value?.trim();
  const taskText = document.getElementById('sched-edit-prompt')?.value;
  const agent = document.getElementById('sched-edit-agent')?.value || 'main';
  const model = document.getElementById('sched-edit-model')?.value || '';
  const schedule = document.getElementById('sched-edit-schedule')?.value?.trim();
  const timeout = parseInt(document.getElementById('sched-edit-timeout')?.value) || 300;
  const thinking_level = document.getElementById('sched-edit-thinking')?.value || '';
  const caveman_chat = parseInt(document.getElementById('sched-edit-caveman')?.value) || 0;
  const tool_profile = document.getElementById('sched-edit-tool-profile')?.value || '';
  const working_dir = document.getElementById('sched-edit-workdir')?.value?.trim() || '';
  const attachments = window._schedEditAttachments || [];

  if (!newName) { showToast('Name is required', true); return; }
  if (!taskText) { showToast('Prompt is required', true); return; }
  if (!schedule) { showToast('Schedule is required', true); return; }

  const payload = {
    action: 'edit',
    name: originalName,
    task: taskText,
    agent,
    schedule,
    timeout,
    thinking_level,
    caveman_chat,
    tool_profile,
    working_dir,
    attachments,
    // Empty string signals "clear back to Default" so the scheduler falls
    // back to the target agent's preferred_model at dispatch time. null
    // would mean "don't touch" in the server-side patch semantics.
    model: model,
  };
  if (newName !== originalName) payload.new_name = newName;

  try {
    const res = await API.manageSchedule(payload);
    if (res.error) { showToast(res.error, true); return; }
    showToast('Saved');
    window._schedEditAttachments = [];
    document.querySelector('.sched-modal-overlay')?.remove();
    loadScheduledView();
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

// Create Scheduled Task modal
function showCreateScheduledModal() {
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };

  // Build agent options
  const agentOpts = (state.agents || []).map(a =>
    `<option value="${esc(a.id)}" ${a.id === (state.activeAgentId || 'main') ? 'selected' : ''}>${esc(a.display_name || a.id)}</option>`
  ).join('');

  // Build model options — chat-capable only.
  const modelOpts = (state.models || []).filter(m => modelHasCapability(m, 'chat')).map(m =>
    `<option value="${esc(m)}">${esc(m)}</option>`
  ).join('');

  overlay.innerHTML = `<div class="sched-modal">
    <h2>New Scheduled Task</h2>
    <div class="sched-form-group">
      <label>Name</label>
      <input id="sched-new-name" placeholder="e.g. Daily PR review">
    </div>
    <div class="sched-form-group">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <label style="margin:0">Prompt</label>
        ${_schedRefineControls('sched-new-prompt')}
      </div>
      <textarea id="sched-new-prompt" placeholder="What should the agent do?"></textarea>
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Agent</label>
        <select id="sched-new-agent">${agentOpts}</select>
      </div>
      <div class="sched-form-group">
        <label>Model (optional)</label>
        <select id="sched-new-model" onchange="_schedRefreshThinking('sched-new-model','sched-new-thinking')"><option value="">Default</option>${modelOpts}</select>
      </div>
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Frequency</label>
        <select id="sched-new-freq" onchange="_schedFreqChanged()">
          <option value="every 1h">Hourly</option>
          <option value="daily 09:00" selected>Daily</option>
          <option value="weekly mon 09:00">Weekly</option>
          <option value="custom">Custom</option>
        </select>
      </div>
      <div class="sched-form-group">
        <label>Time</label>
        <input id="sched-new-time" type="time" value="09:00">
      </div>
    </div>
    <div class="sched-form-group" id="sched-custom-row" style="display:none">
      <label>Custom Schedule</label>
      <input id="sched-new-custom" placeholder="e.g. every 30m, daily 14:00, weekly fri 17:00" style="font-family:var(--font-mono)">
    </div>
    <div class="sched-form-row">
      <div class="sched-form-group">
        <label>Timeout (seconds)</label>
        <input id="sched-new-timeout" type="number" value="300" min="30" max="3600">
      </div>
      <div class="sched-form-group">
        <label>Thinking level <span style="color:var(--text-400);font-weight:normal;font-size:11px">(reasoning effort)</span></label>
        <select id="sched-new-thinking"></select>
      </div>
      <div class="sched-form-group">
        <label>Caveman mode <span style="color:var(--text-400);font-weight:normal;font-size:11px">(response compression)</span></label>
        <select id="sched-new-caveman">
          <option value="0" selected>Off</option>
          <option value="1">Lite</option>
          <option value="2">Full</option>
          <option value="3">Ultra</option>
        </select>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Tool profile <span style="color:var(--text-400);font-weight:normal;font-size:11px">(which tools the agent sees on this task)</span></label>
      <select id="sched-new-tool-profile">
        <option value="" selected>Default — research minimal (exa_search, web_fetch, write_file)</option>
        <option value="research_minimal">Research minimal — same as default, explicit</option>
        <option value="interactive">Interactive — full agent tool surface (chat-like)</option>
      </select>
    </div>
    <div class="sched-form-group">
      <label>Working directory <span style="color:var(--text-400);font-weight:normal;font-size:11px">(optional)</span></label>
      <div style="display:flex;gap:6px">
        <input id="sched-new-workdir" placeholder="(none)" readonly style="font-family:var(--font-mono);flex:1;background:var(--bg-100)">
        <button type="button" onclick="_schedOpenFolderPicker('sched-new-workdir')" style="padding:6px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-200);cursor:pointer;font-size:12px">Browse…</button>
        <button type="button" onclick="document.getElementById('sched-new-workdir').value=''" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-400);cursor:pointer;font-size:12px" title="Clear">×</button>
      </div>
    </div>
    <div class="sched-form-group">
      <label>Attachments <span style="color:var(--text-400);font-weight:normal;font-size:11px">(optional, copied into the run on each fire)</span></label>
      <input id="sched-new-files" type="file" multiple onchange="_schedUploadFiles(this, 'sched-new-attlist')" style="font-size:12px">
      <div id="sched-new-attlist" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px"></div>
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" onclick="_createScheduledTask()">Create</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  // Populate the thinking dropdown for the initially-selected model
  // (typically empty=Default → generic set with "Inherit" pre-selected).
  _schedRefreshThinking('sched-new-model', 'sched-new-thinking', '');
  // Attachments accumulator for the open modal. Cleared with the modal.
  window._schedNewAttachments = [];
}

// Upload picked files to /v1/schedule/upload and render chips. The chip
// list is the source of truth that's read at submit time.
async function _schedUploadFiles(input, listElId) {
  const files = Array.from(input.files || []);
  if (!files.length) return;
  const listEl = document.getElementById(listElId);
  // Decide which accumulator we're writing to based on the list element.
  const buckName = (listElId === 'sched-edit-attlist')
    ? '_schedEditAttachments' : '_schedNewAttachments';
  // Pick the agent the modal is targeting so the file lands under that
  // agent's scheduled_attachments folder.
  const agentSel = document.getElementById('sched-new-agent')
                || document.getElementById('sched-edit-agent');
  const agent = agentSel?.value || 'main';
  // Auth header is required — the global /v1/* gate rejects anonymous POST.
  // Don't set Content-Type; the browser inserts the multipart boundary.
  const token = localStorage.getItem('auth-token') || '';
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f, f.name);
    fd.append('agent', agent);
    try {
      const resp = await fetch(`${BASE_URL}/v1/schedule/upload`, {
        method: 'POST', headers, body: fd,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({error: `HTTP ${resp.status}`}));
        showToast('Upload failed: ' + (err.error || resp.statusText), true);
        continue;
      }
      const meta = await resp.json();
      window[buckName] = window[buckName] || [];
      window[buckName].push(meta);
      _renderSchedAttList(listElId, buckName);
    } catch(e) {
      showToast('Upload failed: ' + e.message, true);
    }
  }
  input.value = '';  // allow re-picking the same file
}

function _renderSchedAttList(listElId, buckName) {
  const listEl = document.getElementById(listElId);
  const items = window[buckName] || [];
  if (!listEl) return;
  if (!items.length) { listEl.innerHTML = ''; return; }
  listEl.innerHTML = items.map((m, i) => {
    const kb = (m.size/1024).toFixed(1);
    return `<span style="display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border:1px solid var(--border-100);border-radius:12px;background:var(--bg-100);font-size:11px;font-family:var(--font-mono);color:var(--text-200)" title="${esc(m.path||'')}">
      ${esc(m.name)} <span style="color:var(--text-400)">${kb}KB</span>
      <button onclick="_schedRemoveAtt('${esc(buckName)}', ${i}, '${esc(listElId)}')" style="border:none;background:transparent;color:var(--text-400);cursor:pointer;padding:0;font-size:14px;line-height:1">×</button>
    </span>`;
  }).join('');
}

function _schedRemoveAtt(buckName, idx, listElId) {
  const items = window[buckName] || [];
  items.splice(idx, 1);
  window[buckName] = items;
  _renderSchedAttList(listElId, buckName);
}

// --- Folder picker (server-side dir browser) ---
// Opens a modal stacked on top of the schedule modal. Navigates the server
// filesystem one level at a time via /v1/files/tree?path=X&depth=0. The
// selected absolute path is written to the input identified by targetInputId.
function _schedOpenFolderPicker(targetInputId) {
  const startVal = document.getElementById(targetInputId)?.value?.trim() || '';
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';  // above the schedule modal
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Select working directory</h2>
    <div id="folder-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="folder-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" id="folder-picker-select" onclick="_schedFolderPickerSelect('${esc(targetInputId)}')">Select this folder</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  _schedLoadFolder(startVal);  // empty string → server defaults to $HOME
}

async function _schedLoadFolder(path) {
  const crumbs = document.getElementById('folder-picker-crumbs');
  const list = document.getElementById('folder-picker-list');
  if (!crumbs || !list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-400);text-align:center">Loading…</div>';
  try {
    const data = await API.get(`/v1/files/tree?path=${encodeURIComponent(path)}&depth=0`);
    if (data.error) { list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(data.error)}</div>`; return; }
    const cur = data.path || path || '/';
    window._schedPickerPath = cur;
    crumbs.textContent = cur;
    const dirs = (data.tree || []).filter(n => n.type === 'dir');
    const parent = (cur && cur !== '/') ? cur.replace(/\/[^\/]+\/?$/, '') || '/' : null;
    let html = '';
    if (parent !== null) {
      html += `<div onclick="_schedLoadFolder('${esc(parent)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-300)" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">↑ ..</div>`;
    }
    if (!dirs.length) {
      html += '<div style="padding:14px;color:var(--text-400);text-align:center;font-size:12px">(no subfolders)</div>';
    } else {
      for (const d of dirs) {
        html += `<div onclick="_schedLoadFolder('${esc(d.path)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-200);display:flex;align-items:center;gap:8px" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">
          <span style="color:var(--text-400)">📁</span>${esc(d.name)}
        </div>`;
      }
    }
    list.innerHTML = html;
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(e.message)}</div>`;
  }
}

function _schedFolderPickerSelect(targetInputId) {
  const path = window._schedPickerPath || '';
  if (!path) { showToast('No folder selected', true); return; }
  const inp = document.getElementById(targetInputId);
  if (inp) inp.value = path;
  // Close the topmost overlay (the picker), leaving the schedule modal open.
  const overlays = document.querySelectorAll('.sched-modal-overlay');
  if (overlays.length) overlays[overlays.length - 1].remove();
}

function _schedFreqChanged() {
  const freq = document.getElementById('sched-new-freq')?.value;
  const customRow = document.getElementById('sched-custom-row');
  const timeInput = document.getElementById('sched-new-time');
  if (freq === 'custom') {
    customRow.style.display = '';
    timeInput.parentElement.style.display = 'none';
  } else {
    customRow.style.display = 'none';
    timeInput.parentElement.style.display = '';
  }
}

async function _createScheduledTask() {
  const name = document.getElementById('sched-new-name')?.value?.trim();
  const task = document.getElementById('sched-new-prompt')?.value?.trim();
  const agent = document.getElementById('sched-new-agent')?.value || 'main';
  const model = document.getElementById('sched-new-model')?.value || undefined;
  const freq = document.getElementById('sched-new-freq')?.value;
  const timeVal = document.getElementById('sched-new-time')?.value || '09:00';
  const customSched = document.getElementById('sched-new-custom')?.value?.trim();
  const timeout = parseInt(document.getElementById('sched-new-timeout')?.value) || 300;
  const thinking_level = document.getElementById('sched-new-thinking')?.value || '';
  const caveman_chat = parseInt(document.getElementById('sched-new-caveman')?.value) || 0;
  const tool_profile = document.getElementById('sched-new-tool-profile')?.value || '';

  if (!name || !task) { showToast('Name and prompt are required', true); return; }

  let schedule;
  if (freq === 'custom') {
    schedule = customSched;
    if (!schedule) { showToast('Custom schedule is required', true); return; }
  } else if (freq.startsWith('every')) {
    schedule = freq;
  } else if (freq.startsWith('daily')) {
    schedule = `daily ${timeVal}`;
  } else if (freq.startsWith('weekly')) {
    const day = freq.split(' ')[1] || 'mon';
    schedule = `weekly ${day} ${timeVal}`;
  }

  const working_dir = document.getElementById('sched-new-workdir')?.value?.trim() || '';
  const attachments = window._schedNewAttachments || [];
  try {
    const res = await API.manageSchedule({
      action: 'add', name, task, schedule, agent, model, timeout,
      thinking_level, caveman_chat, tool_profile,
      working_dir, attachments,
    });
    if (res.error) { showToast(res.error, true); return; }
    showToast('Task created');
    window._schedNewAttachments = [];
    document.querySelector('.sched-modal-overlay')?.remove();
    loadScheduledView();
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function saveAgentSoul(agentId) {
  const editor = document.getElementById('agent-soul-editor');
  if (!editor) return;
  try {
    await API.post(`/v1/agents/${agentId}/file`, { name: 'soul.md', content: editor.value });
    showToast('Soul saved');
  } catch(e) {
    showToast('Save failed: ' + e.message, true);
  }
}

async function refineAgentSoul(agentId) {
  const ta = document.getElementById('agent-soul-editor');
  if (!ta) return;
  const text = (ta.value || '').replace(/^\s+|\s+$/g, '');
  if (!text) { showToast('Write something first', true); return; }
  const btn = document.getElementById('agent-soul-editor-refine');
  const lbl = document.getElementById('agent-soul-editor-refine-label');
  const origLabel = lbl?.textContent || 'Refine with AI';
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Refining…';
  ta.disabled = true;
  const original = ta.value;
  // No caveman here — caveman_system on the agent's model already
  // compresses the soul dynamically at request time (see
  // _build_system_prompt). Compressing the saved soul.md too would
  // double-apply and lock the user into a level they can't reverse.
  try {
    const result = await API.post('/v1/refine', {
      text, purpose: 'soul', field_label: agentId,
    });
    if (result && result.refined && result.refined !== text) {
      ta.value = result.refined;
      if (lbl) lbl.textContent = 'Undo';
      if (btn) {
        btn.disabled = false;
        const undoHandler = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          ta.value = original;
          if (lbl) lbl.textContent = origLabel;
          btn.removeEventListener('click', undoHandler);
          btn.onclick = () => refineAgentSoul(agentId);
        };
        btn.onclick = undoHandler;
      }
      showToast('Refined — review and click Save Soul, or Undo');
    } else {
      showToast('Already clean — no change');
      if (lbl) lbl.textContent = origLabel;
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('Refine failed: ' + (e.message || e), true);
    if (lbl) lbl.textContent = origLabel;
    if (btn) btn.disabled = false;
  } finally {
    ta.disabled = false;
  }
}

async function sendSoulChat(agentId) {
  const input = document.getElementById('soul-chat-input');
  const msgContainer = document.getElementById('soul-chat-messages');
  const editor = document.getElementById('agent-soul-editor');
  const sendBtn = document.getElementById('soul-chat-send');
  const message = input?.value?.trim();
  if (!message) return;

  // Show user message
  const userBubble = document.createElement('div');
  userBubble.style.cssText = 'align-self:flex-end;background:var(--accent);color:#fff;padding:6px 10px;border-radius:10px 10px 2px 10px;font-size:13px;max-width:80%;word-break:break-word';
  userBubble.textContent = message;
  msgContainer.appendChild(userBubble);
  input.value = '';
  msgContainer.scrollTop = msgContainer.scrollHeight;

  // Show thinking indicator
  const thinkBubble = document.createElement('div');
  thinkBubble.style.cssText = 'align-self:flex-start;color:var(--text-400);font-size:12px;font-style:italic;padding:4px 8px';
  thinkBubble.textContent = 'Thinking...';
  msgContainer.appendChild(thinkBubble);
  msgContainer.scrollTop = msgContainer.scrollHeight;

  sendBtn.disabled = true;
  input.disabled = true;

  try {
    const result = await API.post(`/v1/agents/${agentId}/soul-chat`, {
      message,
      soul: editor?.value || '',
      history: window._soulChatHistory || [],
    });

    thinkBubble.remove();

    // Add to history
    if (!window._soulChatHistory) window._soulChatHistory = [];
    window._soulChatHistory.push({ role: 'user', content: message });
    window._soulChatHistory.push({ role: 'assistant', content: result.reply });

    // Parse reply — extract ```soul block if present
    const reply = result.reply || '';
    const soulMatch = reply.match(/```soul\n([\s\S]*?)```/);

    // Show assistant message
    const asstBubble = document.createElement('div');
    asstBubble.style.cssText = 'align-self:flex-start;background:var(--bg-200);padding:6px 10px;border-radius:10px 10px 10px 2px;font-size:13px;max-width:80%;word-break:break-word';

    if (soulMatch) {
      const newSoul = soulMatch[1].trim();
      const commentary = reply.replace(/```soul\n[\s\S]*?```/, '').trim();
      let html = '';
      if (commentary) html += esc(commentary) + '<br><br>';
      html += '<div style="display:flex;gap:6px;margin-top:4px"><button class="btn-primary" style="font-size:11px;padding:3px 10px" onclick="applySoulEdit(this)">Apply</button><span style="font-size:11px;color:var(--text-400)">Click to update the editor</span></div>';
      asstBubble.innerHTML = html;
      asstBubble.dataset.soul = newSoul;
    } else {
      asstBubble.textContent = reply;
    }
    msgContainer.appendChild(asstBubble);
    msgContainer.scrollTop = msgContainer.scrollHeight;

  } catch(e) {
    thinkBubble.remove();
    const errBubble = document.createElement('div');
    errBubble.style.cssText = 'align-self:flex-start;color:var(--error);font-size:12px;padding:4px 8px';
    errBubble.textContent = 'Error: ' + e.message;
    msgContainer.appendChild(errBubble);
  } finally {
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

function applySoulEdit(btn) {
  const bubble = btn.closest('[data-soul]');
  if (!bubble) return;
  const editor = document.getElementById('agent-soul-editor');
  if (editor) {
    editor.value = bubble.dataset.soul;
    showToast('Soul updated in editor — click Save Soul to persist');
  }
}

async function saveAgentJson(agentId) {
  try {
    // Read current config first
    const data = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
    const cfg = JSON.parse(data.content || '{}');

    // Update fields
    cfg.display_name = document.getElementById('cfg-display-name')?.value || '';
    cfg.description = document.getElementById('cfg-description')?.value || '';
    cfg.model = document.getElementById('cfg-model')?.value || cfg.model;
    cfg.paused = document.getElementById('cfg-paused')?.checked || false;

    // Next-prompt suggestions (only write the block when fields exist, so
    // we don't erase advanced keys the user added by hand)
    const npsEnabledEl = document.getElementById('cfg-nps-enabled');
    if (npsEnabledEl) {
      const nps = (cfg.next_prompt_suggestions && typeof cfg.next_prompt_suggestions === 'object')
        ? cfg.next_prompt_suggestions : {};
      nps.enabled = !!npsEnabledEl.checked;
      nps.model = document.getElementById('cfg-nps-model')?.value || '';
      const mw = parseInt(document.getElementById('cfg-nps-max-words')?.value, 10);
      if (!Number.isNaN(mw)) nps.max_words = Math.max(3, Math.min(40, mw));
      cfg.next_prompt_suggestions = nps;
    }

    await API.post(`/v1/agents/${agentId}/file`, { name: 'agent.json', content: JSON.stringify(cfg, null, 2) });
    showToast('Agent config saved');

    // Refresh agents list
    const agentsData = await API.getAgents();
    state.agents = agentsData.agents || agentsData || [];
  } catch(e) {
    showToast('Save failed: ' + e.message, true);
  }
}
