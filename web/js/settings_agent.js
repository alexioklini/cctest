// settings_agent.js — agent config modal: soul, skills/CC marketplace, per-agent token/MCP, agent tab dispatcher. Split from settings.js (Tier F Phase 2). Global <script>, no modules.

function openAgentSettings() {
  // Agent config (soul, agent.json, hooks, MCP, tokens) is admin-only on the
  // server — POST /v1/agents/<id>/{file,hooks,...} returns 403 for everyone
  // else. Refuse early so non-admins don't see editable system prompts they
  // can't save, and so /settings + future entry points fail closed.
  if ((state.authUser?.role || 'admin') !== 'admin') {
    showToast('Agent-Anpassung ist nur für Administratoren', true);
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
      <div class="sidebar-group-label">Identität</div>
      <button class="modal-tab active" onclick="switchAgentTab('${esc(agentId)}','soul',this)">Soul</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','agent',this)">Agent</button>

      <div class="sidebar-group-label">Fähigkeiten</div>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','skills',this)">Skills</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','mcp',this)">MCP</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','tokens',this)">Tokens</button>

      <div class="sidebar-group-label">Automatisierung</div>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','hooks',this)">Hooks</button>
      <button class="modal-tab" onclick="switchAgentTab('${esc(agentId)}','schedule',this)">Zeitplan</button>
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
  if (!id) { showToast('Agent-ID ist erforderlich', true); return; }
  const desc = document.getElementById('new-agent-desc')?.value?.trim() || '';
  const soul = document.getElementById('new-agent-soul')?.value?.trim() || '';
  const model = document.getElementById('new-agent-model')?.value || '';
  const displayName = document.getElementById('new-agent-display')?.value?.trim() || '';
  try {
    await API.createAgent({ agent: id, description: desc, soul: soul || undefined, model: model || undefined, display_name: displayName || undefined });
    showToast(`Agent „${id}" erstellt`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('agents');
  } catch(e) { showToast('Erstellen fehlgeschlagen: ' + e.message, true); }
}
async function _deleteAgent(id) {
  if (!await showConfirmDanger(`Agent „${id}" löschen? Er wird nach .trash verschoben.`, 'Agent löschen', 'Löschen')) return;
  try {
    await API.deleteAgent(id);
    showToast(`Agent „${id}" gelöscht`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('agents');
  } catch(e) { showToast('Löschen fehlgeschlagen: ' + e.message, true); }
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
    results.innerHTML = '<div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Geben Sie einen Suchbegriff ein, um Plugins zu durchsuchen</div>';
    return;
  }
  results.innerHTML = `<div style="padding:20px;text-align:center">
    <div style="display:inline-block;width:18px;height:18px;border:2px solid #e8e7e0;border-top-color:#d97757;border-radius:50%;animation:spin 0.6s linear infinite"></div>
    <div style="margin-top:8px;color:#73726c;font-size:12px">Marketplace wird nach „${esc(query)}" durchsucht…</div>
  </div>`;
  if (searchBtn) { searchBtn.textContent = '...'; searchBtn.disabled = true; }
  try {
    const data = await API.browseCCPlugins(query);
    const plugins = data.plugins || [];
    if (searchBtn) { searchBtn.textContent = 'Suchen'; searchBtn.disabled = false; }
    if (!plugins.length) {
      results.innerHTML = `<div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Keine Plugins für „${esc(query)}" gefunden</div>`;
      return;
    }
    let html = `<div style="padding:4px 0;font-size:11px;color:#a1a09a;margin-bottom:4px">${plugins.length} Plugins gefunden</div>`;
    for (const p of plugins) {
      html += `
        <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px;margin-bottom:6px;transition:background 0.15s;overflow:hidden" onmouseover="this.style.background='rgba(0,0,0,0.02)'" onmouseout="this.style.background='transparent'">
          <div style="flex:1;min-width:0;overflow:hidden">
            <div style="font-size:13px;font-weight:600;color:#141413">${esc(p.name || p)}</div>
            ${p.description ? `<div style="font-size:11px;color:#73726c;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.description)}</div>` : ''}
          </div>
          <button onclick="installCCPlugin('${esc(typeof p === 'string' ? p : p.name)}',this)"
            style="${_mcpBtn};padding:4px 12px;flex-shrink:0">Installieren</button>
        </div>`;
    }
    results.innerHTML = html;
  } catch(e) {
    if (searchBtn) { searchBtn.textContent = 'Suchen'; searchBtn.disabled = false; }
    results.innerHTML = `<div style="padding:24px;text-align:center;color:#dc2626;font-size:12px">${esc(e.message)}</div>`;
  }
}

async function installCCPlugin(pluginName, btn) {
  btn.disabled = true;
  btn.textContent = 'Installiere…';
  btn.style.opacity = '0.5';
  try {
    await API.installCCPlugin(pluginName);
    const added = document.createElement('span');
    added.textContent = 'Hinzugefügt';
    added.style.cssText = 'font-size:11px;font-weight:500;color:#16a34a;padding:4px 12px';
    btn.replaceWith(added);
  } catch(e) {
    btn.textContent = 'Fehlgeschlagen';
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
  container.innerHTML = '<div style="padding:20px;color:var(--text-400)">Lädt…</div>';

  if (tab === 'soul') {
    try {
      const data = await API.get(`/v1/agents/${agentId}/file?name=soul.md`);
      const soul = data.content || '';
      container.innerHTML = `
        <div style="display:flex;flex-direction:column;height:100%;padding:16px;overflow:hidden">
          <div style="font-size:12px;color:var(--text-400);margin-bottom:6px">System-Prompt — definiert Persönlichkeit und Verhalten des Agents</div>
          <textarea id="agent-soul-editor" class="form-textarea" style="flex:3 1 0;min-height:80px;font-size:13px;overflow-y:auto">${esc(soul)}</textarea>
          <div style="display:flex;justify-content:flex-end;gap:8px;margin:6px 0;align-items:center">
            ${_refineTierButton('soul:' + esc(agentId))}
            <button type="button" id="agent-soul-editor-refine"
              onclick="refineAgentSoul('${esc(agentId)}')"
              title="Die Soul mit KI verfeinern (bewahrt die Du-/Sie-Anrede und die Markdown-Struktur)"
              class="btn-secondary"
              style="font-size:12px;padding:4px 10px;display:inline-flex;align-items:center;gap:4px">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
                <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
                <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
              </svg>
              <span id="agent-soul-editor-refine-label">Mit KI verfeinern</span>
            </button>
            <button class="btn-primary" onclick="saveAgentSoul('${esc(agentId)}')">Soul speichern</button>
          </div>
          <div style="border-top:1px solid var(--border);padding-top:6px;display:flex;flex-direction:column;flex:2 1 0;min-height:60px;overflow:hidden">
            <div style="font-size:12px;color:var(--text-400);margin-bottom:4px">KI-Soul-Editor — per Chat die Soul verfeinern</div>
            <div id="soul-chat-messages" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:6px;margin-bottom:4px;padding:2px 4px;min-height:0"></div>
            <div style="display:flex;gap:8px;flex-shrink:0">
              <input id="soul-chat-input" class="form-input" style="flex:1;font-size:13px" placeholder="KI bitten, die Soul zu bearbeiten…" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendSoulChat('${esc(agentId)}')}" />
              <button class="btn-primary" onclick="sendSoulChat('${esc(agentId)}')" id="soul-chat-send">Senden</button>
            </div>
          </div>
        </div>
      `;
      container.style.overflowY = 'hidden';
      window._soulChatHistory = [];
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">Soul konnte nicht geladen werden: ${esc(e.message)}</div>`;
    }
  }

  if (tab === 'agent') {
    try {
      const data = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
      const cfg = JSON.parse(data.content || '{}');
      const enabledModels = enabledModelsWithCapability('chat');
      // Legacy "auto" maps to Cloud — preselect Smart (Cloud) for it so an
      // old agent.json round-trips without surprise (server treats both alike).
      const _autoCloudSel = (cfg.model==='auto-cloud' || cfg.model==='auto') ? ' selected' : '';
      const _autoLocalSel = cfg.model==='auto-local' ? ' selected' : '';
      const autoOption =
        `<option value="auto-cloud" title="Wählt pro Nachricht automatisch das beste Cloud-Modell"${_autoCloudSel}>✨ Smart (Cloud)</option>` +
        `<option value="auto-local" title="Wählt pro Nachricht automatisch das beste lokale Modell"${_autoLocalSel}>✨ Smart (Lokal)</option>`;
      const modelOptions = autoOption + enabledModels.map(([mid]) =>
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
      const npsModelOptions = `<option value="" ${npsModel===''?'selected':''}>(Sitzungsmodell — beste Cache-Wiederverwendung)</option>`
        + enabledModels.map(([mid]) =>
            modelOption(mid, {selected: mid===npsModel})
          ).join('');

      container.innerHTML = `
        <div style="padding:16px;display:grid;gap:16px">
          <div>
            <label class="form-label">Anzeigename</label>
            <input class="form-input" id="cfg-display-name" value="${esc(cfg.display_name || '')}">
          </div>
          <div>
            <label class="form-label">Beschreibung</label>
            <input class="form-input" id="cfg-description" value="${esc(cfg.description || '')}">
          </div>
          <div>
            <label class="form-label">Modell</label>
            <select class="form-select" id="cfg-model" style="width:100%">${modelOptions}</select>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" id="cfg-paused" ${cfg.paused?'checked':''}>
            <label for="cfg-paused" style="font-size:14px;color:var(--text-200)">Pausiert</label>
          </div>
          <div style="border-top:1px solid var(--border-100);padding-top:12px;display:grid;gap:10px">
            <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em">Next-Prompt-Vorschläge</div>
            <div style="font-size:11px;color:var(--text-400)">Geistertext-Vorhersage, die nach jeder Assistenten-Antwort im Eingabefeld angezeigt wird. Überschreiben Sie das Modell, wenn das Sitzungsmodell ein langsames Denkmodell ist (z. B. Qwen3.6) — wählen Sie ein schnelles Modell wie Haiku für Vorschläge im Sekundenbruchteil.</div>
            <div style="display:flex;align-items:center;gap:8px">
              <input type="checkbox" id="cfg-nps-enabled" ${npsEnabled?'checked':''}>
              <label for="cfg-nps-enabled" style="font-size:14px;color:var(--text-200)">Aktiviert</label>
            </div>
            <div>
              <label class="form-label">Modell-Überschreibung</label>
              <select class="form-select" id="cfg-nps-model" style="width:100%">${npsModelOptions}</select>
            </div>
            <div>
              <label class="form-label">Max. Wörter</label>
              <input class="form-input" type="number" id="cfg-nps-max-words" value="${npsMaxWords}" min="3" max="40" style="width:120px">
            </div>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button class="btn-primary" onclick="saveAgentJson('${esc(agentId)}')">Speichern</button>
          </div>
        </div>
      `;
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">Konfiguration konnte nicht geladen werden: ${esc(e.message)}</div>`;
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
              <span style="font-size:14px;font-weight:700;color:#141413">Agent-Skills</span>
              <span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(217,119,87,0.12);color:#d97757">${agentSkills.length} installiert</span>
            </div>
          </div>`;
      if (!agentSkills.length) {
        html += '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">Keine Agent-Skills installiert.</div>';
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
                title="Skill entfernen">Entfernen</button>
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
              <div style="font-size:11px;color:#73726c;margin-top:2px">Skills aus ~/.claude umschalten, um sie für diesen Agent zu aktivieren</div>
            </div>
          </div>`;
      if (!ccSkills.length) {
        html += '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">Keine Claude Code Skills in ~/.claude gefunden</div>';
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
            <span style="font-size:14px;font-weight:700;color:#141413">CC Marketplace durchsuchen</span>
            <span style="font-size:10px;color:#a1a09a">Claude Code Plugins</span>
          </div>
          <div style="position:relative;margin-bottom:10px">
            <input id="cc-marketplace-search" style="width:100%;box-sizing:border-box;background:#f5f4ed;border:1px solid rgba(31,30,29,0.08);border-radius:8px;padding:9px 80px 9px 12px;font-size:13px;color:#141413;outline:none;transition:border-color 0.15s" placeholder="Claude Code Plugins durchsuchen…" onfocus="this.style.borderColor='#d97757'" onblur="this.style.borderColor='rgba(31,30,29,0.08)'" onkeydown="if(event.key==='Enter'){event.preventDefault();searchCCMarketplace(this.value)}" oninput="clearTimeout(window._ccMktDebounce);window._ccMktDebounce=setTimeout(()=>searchCCMarketplace(this.value),400)">
            <button id="cc-marketplace-search-btn" style="position:absolute;right:4px;top:50%;transform:translateY(-50%);font-size:11px;padding:5px 14px;border-radius:6px;background:#d97757;color:#fff;border:none;cursor:pointer;font-weight:500;transition:background 0.15s" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="searchCCMarketplace(document.getElementById('cc-marketplace-search').value)">Suchen</button>
          </div>
          <div id="cc-marketplace-results" style="display:grid;gap:6px">
            <div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Tippen, um den Claude Code Plugin-Marketplace zu durchsuchen</div>
          </div>
        </div>`;

      html += '</div>';
      container.innerHTML = html;
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:#73726c">Skills nicht verfügbar: ${esc(e.message)}</div>`;
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
              <div style="font-size:13px;font-weight:500;color:var(--text-100);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(displayName)}${isSystem?' <span style="font-size:10px;color:var(--text-400);font-weight:400">System</span>':''}</div>
              <div style="font-size:11px;color:var(--text-400);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(s.task||'')}">${esc((s.task||'').substring(0,80))}</div>
            </div>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:2px;flex-shrink:0">
              <span style="font-size:11px;font-family:var(--font-mono);color:var(--text-300)">${esc(s.schedule||'')}</span>
              ${s.next_run ? `<span style="font-size:10px;color:var(--text-400)">nächste: ${new Date(s.next_run+'Z').toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</span>` : ''}
            </div>
            <div style="display:flex;gap:4px;flex-shrink:0">
              ${isRunning ? `<button onclick="_schedCancel('${esc(s.name)}')" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--error);background:transparent;color:var(--error);cursor:pointer" title="Laufende Aufgabe abbrechen">Stopp</button>` : ''}
              <button onclick="_schedToggle('${esc(s.name)}',${enabled?0:1})" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer" title="${enabled?'Pausieren':'Fortsetzen'}">${enabled?'Pause':'Fortsetzen'}</button>
              <button onclick="_schedHistory('${esc(s.name)}')" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer" title="Verlauf anzeigen">Log</button>
              ${!isSystem ? `<button onclick="_schedDelete('${esc(s.name)}')" style="font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid var(--error);background:transparent;color:var(--error);cursor:pointer" title="Löschen">Lö</button>` : ''}
            </div>
          </div>`;
        }
      } else {
        html += '<div id="sched-empty" style="padding:20px;text-align:center;color:var(--text-400)">Keine geplanten Aufgaben für diesen Agent</div>';
      }
      html += '</div>';

      // History panel (hidden)
      html += '<div id="sched-history" style="display:none;padding:12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-200)"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span style="font-size:13px;font-weight:600;color:var(--text-100)" id="sched-history-title">Verlauf</span><button onclick="document.getElementById(\'sched-history\').style.display=\'none\'" style="font-size:11px;padding:2px 8px;border-radius:4px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer">Schließen</button></div><div id="sched-history-body" style="max-height:200px;overflow-y:auto;font-size:12px"></div></div>';

      // Add button + form
      html += `<div style="display:flex;justify-content:flex-start">
        <button onclick="_schedShowForm()" style="font-size:12px;padding:6px 14px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;font-weight:500">+ Aufgabe hinzufügen</button>
      </div>`;

      html += `<div id="sched-form" style="display:none;padding:14px;border:1px solid var(--accent);border-radius:8px;background:var(--bg-200)">
        <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:10px">Neue geplante Aufgabe</div>
        <div style="display:grid;gap:8px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div>
              <label style="font-size:11px;color:var(--text-400)">Name</label>
              <input id="sched-f-name" class="form-input" placeholder="daily-report" style="font-size:12px">
            </div>
            <div>
              <label style="font-size:11px;color:var(--text-400)">Zeitplan</label>
              <input id="sched-f-schedule" class="form-input" placeholder="daily 09:00" style="font-size:12px;font-family:var(--font-mono)">
            </div>
          </div>
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
              <label style="font-size:11px;color:var(--text-400);margin:0">Prompt / Aufgabe</label>
              ${_schedRefineControls('sched-f-task')}
            </div>
            <textarea id="sched-f-task" class="form-input" rows="3" placeholder="Was soll der Agent tun…" style="font-size:12px;resize:vertical"></textarea>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div>
              <label style="font-size:11px;color:var(--text-400)">Modell (optional)</label>
              <input id="sched-f-model" class="form-input" placeholder="default" style="font-size:12px;font-family:var(--font-mono)">
            </div>
            <div>
              <label style="font-size:11px;color:var(--text-400)">Timeout (Sekunden)</label>
              <input id="sched-f-timeout" class="form-input" type="number" value="300" style="font-size:12px">
            </div>
          </div>
          <div style="font-size:10px;color:var(--text-400);line-height:1.4">
            Formate: <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">every 5m</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">every 2h</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">daily 09:00</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">weekly mon 14:30</code>
            <code style="background:var(--bg-100);padding:1px 4px;border-radius:3px">once 2026-04-01 12:00</code>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button onclick="document.getElementById('sched-form').style.display='none'" style="font-size:12px;padding:5px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer">Abbrechen</button>
            <button class="btn-primary" onclick="_schedAdd()" style="font-size:12px;padding:5px 12px">Erstellen</button>
          </div>
        </div>
      </div>`;

      container.innerHTML = html + '</div>';
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">Zeitplan konnte nicht geladen werden</div>`;
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
          <label for="hooks-enabled" style="font-size:14px;font-weight:500;color:var(--text-100)">Hooks aktiviert</label>
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
        html += '<div id="hooks-empty" style="padding:20px;text-align:center;color:var(--text-400)">Keine Hook-Skripte konfiguriert</div>';
      }
      html += '</div>';

      // Add + Save buttons
      html += `<div style="display:flex;justify-content:space-between;align-items:center">
        <button onclick="_hookAdd()" style="font-size:12px;padding:6px 14px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;font-weight:500">+ Hook hinzufügen</button>
        <button class="btn-primary" onclick="_hooksSave()">Speichern</button>
      </div>`;

      // Add/Edit form (hidden initially)
      html += `<div id="hook-form" style="display:none;padding:14px;border:1px solid var(--accent);border-radius:8px;background:var(--bg-200)">
        <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:10px" id="hook-form-title">Hook hinzufügen</div>
        <div style="display:grid;gap:8px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div>
              <label style="font-size:11px;color:var(--text-400)">Name</label>
              <input id="hook-f-name" class="form-input" placeholder="my-hook" style="font-size:12px">
            </div>
            <div>
              <label style="font-size:11px;color:var(--text-400)">Typ</label>
              <select id="hook-f-type" class="form-input" style="font-size:12px">
                <option value="pre">pre</option>
                <option value="post">post</option>
                <option value="after_file_write">after_file_write</option>
              </select>
            </div>
          </div>
          <div>
            <label style="font-size:11px;color:var(--text-400)">Skriptpfad (relativ zum Agent-Verzeichnis)</label>
            <input id="hook-f-script" class="form-input" placeholder="hooks/my-hook.sh" style="font-size:12px;font-family:var(--font-mono)">
          </div>
          <div>
            <label style="font-size:11px;color:var(--text-400)">Tools (kommagetrennt, * für alle)</label>
            <input id="hook-f-tools" class="form-input" placeholder="*" value="*" style="font-size:12px;font-family:var(--font-mono)">
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" id="hook-f-enabled" checked>
            <label for="hook-f-enabled" style="font-size:12px;color:var(--text-200)">Aktiviert</label>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button onclick="document.getElementById('hook-form').style.display='none'" style="font-size:12px;padding:5px 12px;border-radius:6px;border:1px solid var(--border-100);background:transparent;color:var(--text-300);cursor:pointer">Abbrechen</button>
            <button class="btn-primary" onclick="_hookFormSave()" style="font-size:12px;padding:5px 12px">Hook speichern</button>
          </div>
        </div>
      </div>`;

      container.innerHTML = html + '</div>';
    } catch(e) { container.innerHTML = '<div style="padding:20px;color:var(--text-400)">Hooks nicht verfügbar</div>'; }
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
      serverCards = '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">Keine MCP-Server konfiguriert. Fügen Sie manuell einen hinzu oder durchsuchen Sie unten die Registry.</div>';
    } else {
      for (const [name, cfg] of Object.entries(allServers)) {
        const inherited = cfg._source === 'main' && agentId !== 'main';
        const transport = cfg.transport || cfg.type || (cfg.command ? 'stdio' : cfg.url ? 'sse' : '?');
        const target = cfg.command ? `${cfg.command} ${(cfg.args||[]).join(' ')}` : cfg.url || '';
        const live = liveStatus[name];
        const dot = live ? `<span style="width:7px;height:7px;border-radius:50%;background:${live.connected?'#16a34a':'#a1a09a'};flex-shrink:0" title="${live.connected?'Verbunden':'Getrennt'}"></span>` : '<span style="width:7px;height:7px;border-radius:50%;background:#d4d4d0;flex-shrink:0" title="Nicht verbunden"></span>';
        const tools = live && live.tools_count ? `<span style="${_b}background:rgba(22,163,74,0.1);color:#16a34a">${live.tools_count} Tools</span>` : '';
        const src = inherited
          ? `<span style="${_b}background:rgba(120,120,140,0.15);color:#73726c">geerbt</span>`
          : `<span style="${_b}background:rgba(139,92,246,0.15);color:#8b5cf6">lokal</span>`;
        const tp = `<span style="${_b}background:rgba(59,130,246,0.12);color:#3b82f6">${esc(transport)}</span>`;
        const rm = !inherited
          ? `<button style="font-size:11px;color:#dc2626;padding:3px 10px;border-radius:5px;cursor:pointer;background:rgba(220,38,38,0.08);border:none;white-space:nowrap" onmouseover="this.style.background='rgba(220,38,38,0.15)'" onmouseout="this.style.background='rgba(220,38,38,0.08)'" onclick="_removeAgentMcp('${esc(agentId)}','${esc(name)}')">Entfernen</button>`
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
          <span style="font-size:14px;font-weight:700;color:#141413">MCP-Server</span>
          <button style="font-size:11px;padding:5px 14px;border-radius:6px;background:#d97757;color:#fff;border:none;cursor:pointer;font-weight:500;transition:background 0.15s" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="_showAgentMcpAdd('${esc(agentId)}')">+ Manuell hinzufügen</button>
        </div>
        ${agentId !== 'main' ? '<div style="font-size:11px;color:#73726c;margin-top:-6px">Erbt Server von main. Fügen Sie unten agent-spezifische Server hinzu.</div>' : ''}
        <div id="agent-mcp-list" style="display:grid;gap:6px">${serverCards}</div>
        <div id="agent-mcp-add-form"></div>

        <div style="border-top:1px solid rgba(31,30,29,0.08);padding-top:16px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <span style="font-size:14px;font-weight:700;color:#141413">MCP-Registry durchsuchen</span>
            <span style="font-size:10px;color:#a1a09a">registry.modelcontextprotocol.io</span>
          </div>
          <div style="position:relative;margin-bottom:10px">
            <input id="mcp-registry-search" style="width:100%;box-sizing:border-box;background:#f5f4ed;border:1px solid rgba(31,30,29,0.08);border-radius:8px;padding:9px 80px 9px 12px;font-size:13px;color:#141413;outline:none;transition:border-color 0.15s" placeholder="Server suchen (z. B. github, slack, postgres)…" onfocus="this.style.borderColor='#d97757'" onblur="this.style.borderColor='rgba(31,30,29,0.08)'" onkeydown="if(event.key==='Enter'){event.preventDefault();_searchMcpRegistry('${esc(agentId)}')}" oninput="clearTimeout(window._mcpSearchDebounce);window._mcpSearchDebounce=setTimeout(()=>_searchMcpRegistry('${esc(agentId)}'),400)">
            <button id="mcp-registry-search-btn" style="position:absolute;right:4px;top:50%;transform:translateY(-50%);font-size:11px;padding:5px 14px;border-radius:6px;background:#d97757;color:#fff;border:none;cursor:pointer;font-weight:500;transition:background 0.15s" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="_searchMcpRegistry('${esc(agentId)}')">Suchen</button>
          </div>
          <div id="mcp-registry-results" style="display:grid;gap:6px">
            <div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Tippen, um die offizielle MCP-Server-Registry zu durchsuchen</div>
          </div>
        </div>
      </div>`;
  }

  if (tab === 'tokens') {
    try {
      const [agentFile, settingsResp] = await Promise.all([
        API.get(`/v1/agents/${agentId}/file?name=agent.json`),
        API.get('/v1/tools/settings'),
      ]);
      const agentCfg = JSON.parse(agentFile.content || '{}');
      const tcfg = agentCfg.token_config || {};
      const overrides = tcfg.tool_overrides || {};
      const allTools = settingsResp.tools || [];
      // Stash on window so per-tool save handlers can read the latest fetched
      // record without refetching (gets clobbered on next tab switch).
      window._tokTools = allTools;
      window._tokOverrides = overrides;
      window._tokAgentId = agentId;

      // Group tools by tool group
      const byGroup = {};
      for (const t of allTools) {
        const g = t.group || '(ungrouped)';
        (byGroup[g] = byGroup[g] || []).push(t);
      }
      const PRIMARY_GROUPS = ['core', 'memory', 'context', 'web', 'documents'];
      const otherGroups = Object.keys(byGroup).filter(g => !PRIMARY_GROUPS.includes(g)).sort();
      const groupOrder = PRIMARY_GROUPS.filter(g => byGroup[g]).concat(otherGroups);

      // Effective state of a tool for THIS agent, collapsing the global
      // (enabled, deferred) pair + any per-agent override into one of:
      //   default  — no override at all (inherits global)
      //   active   — in prompt · inactive — off · deferred — tool_search-only
      // Legacy/partial overrides ({deferred:true} alone, etc.) collapse to
      // whatever the code actually resolves live: compute effective enabled/
      // deferred (override field wins, else global) and map that pair.
      const agentToolState = (t, ovr) => {
        const hasOvr = ('enabled' in ovr) || ('deferred' in ovr);
        if (!hasOvr) return 'default';
        const effEnabled = 'enabled' in ovr ? ovr.enabled : t.enabled;
        const effDeferred = 'deferred' in ovr ? ovr.deferred : t.deferred;
        if (effEnabled === false) return 'inactive';
        return effDeferred ? 'deferred' : 'active';
      };

      // One dropdown per tool — default (inherit) + the 3 concrete states.
      const stateSelect = (toolName, cur) => `
        <select class="tok-override" data-tool="${esc(toolName)}"
                style="font-size:11px;padding:2px 4px;font-family:var(--font-mono);background:var(--bg-100);border:1px solid var(--border-100);border-radius:3px;width:130px"
                title="Standard: globalen Wert erben · Aktiv: im Prompt · Inaktiv: ganz aus · Aufgeschoben: nur über tool_search">
          <option value="default"  ${cur==='default'?'selected':''}>Standard (erben)</option>
          <option value="active"   ${cur==='active'?'selected':''}>Aktiv</option>
          <option value="inactive" ${cur==='inactive'?'selected':''}>Inaktiv</option>
          <option value="deferred" ${cur==='deferred'?'selected':''}>Aufgeschoben</option>
        </select>`;

      const toolRow = (t) => {
        const ovr = overrides[t.name] || {};
        const state = agentToolState(t, ovr);
        // Badge: what global resolves to, so the operator sees what "Standard"
        // would inherit. Reuses the global 3-state collapse.
        const gState = toolGlobalState(t);
        const gLabel = gState === 'active' ? 'global: aktiv'
          : gState === 'inactive' ? 'global: inaktiv' : 'global: aufgeschoben';
        const gColor = gState === 'active' ? 'var(--success)'
          : gState === 'inactive' ? 'var(--text-400)' : 'var(--warning,#d97706)';
        // Effective colour of the tool name = how it resolves for this agent.
        const effState = state === 'default' ? gState : state;
        const effColor = effState === 'inactive' ? 'var(--text-400)' : 'var(--text-100)';
        return `
          <div style="display:grid;grid-template-columns:1fr 150px;gap:8px;align-items:center;padding:5px 8px;border-bottom:1px solid var(--border-100)">
            <div style="display:flex;flex-direction:column;gap:1px">
              <span style="font-family:var(--font-mono);font-size:11px;color:${effColor}">${esc(t.name)}</span>
              <span style="font-size:9px;color:${gColor}">${gLabel}</span>
            </div>
            <div style="display:flex;justify-content:flex-end">
              ${stateSelect(t.name, state)}
            </div>
          </div>`;
      };

      const groupSection = (gName, tools) => {
        // Auto-expand groups that have any agent override
        const hasOverride = tools.some(t => overrides[t.name]);
        return `
          <div style="margin-bottom:14px">
            <div style="display:flex;align-items:center;gap:8px;padding:6px 8px;cursor:pointer;border-radius:4px;background:var(--bg-100)" onclick="toggleTokGroup('${esc(gName)}')">
              <span style="font-size:14px;color:var(--text-400)" id="tok-group-chevron-${esc(gName)}">${hasOverride?'▾':'▸'}</span>
              <span style="font-size:13px;font-weight:600;color:var(--text-100);text-transform:uppercase;letter-spacing:0.04em">${esc(gName)}</span>
              <span style="font-size:11px;color:var(--text-400)">${tools.length} Tool${tools.length===1?'':'s'}</span>
              ${hasOverride ? `<span style="font-size:10px;padding:1px 6px;border-radius:3px;background:rgba(245,158,11,0.12);color:var(--warning,#d97706)">Überschreibung</span>` : ''}
            </div>
            <div id="tok-group-body-${esc(gName)}" style="display:${hasOverride?'block':'none'};padding:6px 0 0 12px">
              <div style="display:grid;grid-template-columns:1fr 150px;gap:8px;padding:4px 8px;border-bottom:1px solid var(--border-100)">
                <span style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em">Tool</span>
                <span style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;text-align:right">Status</span>
              </div>
              ${tools.map(toolRow).join('')}
            </div>
          </div>`;
      };

      container.innerHTML = `
        <div style="padding:16px;display:grid;gap:14px">
          <div style="font-size:16px;font-weight:600;color:var(--text-100)">Token-Optimierung</div>
          <div style="font-size:11px;color:var(--text-400)">
            Pro-Tool-Überschreibungen für diesen Agent. Jedes Tool wird aufgelöst über:
            <b>global</b> (Einstellungen → Tools) → <b>Agent-Überschreibung</b> → <b>Zweck-Filter</b>.
            <b>Standard</b> erbt den globalen Wert; <b>Aktiv</b> / <b>Inaktiv</b> / <b>Aufgeschoben</b> überschreiben nur für diesen Agent.
            Tool-Definitionskosten und Pro-Tool-Text finden Sie unter <b>Allgemeine Einstellungen → Tools</b>.
          </div>

          <div style="border:1px solid var(--border-100);border-radius:8px;padding:12px">
            <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:8px">
              Tool-Überschreibungen
              <span style="font-size:11px;color:var(--text-400);font-weight:400">— ${Object.keys(overrides).length} Tool${Object.keys(overrides).length===1?'':'s'} derzeit überschrieben</span>
            </div>
            ${groupOrder.map(g => groupSection(g, byGroup[g])).join('')}
          </div>

          <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
            <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:8px">Werkzeug-Optimierung pro Anfrage</div>
            <label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer">
              <input type="checkbox" id="tok-optimize-tools" ${tcfg.optimize_tools === false ? '' : 'checked'} style="margin-top:2px">
              <span style="font-size:12px;color:var(--text-300)">
                Klassifiziert jede Anfrage und stellt nicht benötigte Werkzeuge zurück (bleiben per <code>tool_search</code> erreichbar) — schlankerer Prompt, bessere Treffsicherheit bei schwächeren Modellen.
                <span style="display:block;color:var(--text-400);margin-top:3px">
                  Unabhängig von der Modellwahl (✨ Smart). Bei <b>lokalen Modellen mit aktiviertem Warmup</b> wird die Optimierung übersprungen, um den warmen KV-Prefix stabil zu halten; lokale Modelle <b>ohne</b> Warmup werden optimiert wie Cloud-Modelle.
                </span>
              </span>
            </label>
          </div>

          <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px">
            <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:8px">Kontext-Komprimierung</div>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:12px;color:var(--text-300)">Komprimierungsschwelle:</span>
              <input type="number" id="tok-compact-threshold" value="${tcfg.compact_threshold ? Math.round(tcfg.compact_threshold * 100) : ''}"
                placeholder="60" min="40" max="95" step="5"
                class="form-input" style="width:70px;font-size:12px">
              <span style="font-size:11px;color:var(--text-400)">% (leer = Standard 60 %)</span>
            </div>
          </div>

          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button class="btn-secondary" onclick="_clearTokenOverrides('${esc(agentId)}')" title="Alle Pro-Tool-Überschreibungen für diesen Agent zurücksetzen (Komprimierungsschwelle bleibt erhalten).">Alle Überschreibungen löschen</button>
            <button class="btn-primary" onclick="_saveTokenConfig('${esc(agentId)}')">Token-Konfiguration speichern</button>
          </div>
        </div>`;
    } catch(e) {
      container.innerHTML = `<div style="padding:20px;color:var(--error)">${esc(e.message)}</div>`;
    }
  }
}

function toggleTokGroup(groupName) {
  const body = document.getElementById('tok-group-body-' + groupName);
  const chev = document.getElementById('tok-group-chevron-' + groupName);
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  if (chev) chev.textContent = open ? '▸' : '▾';
}

window._saveTokenConfig = async function(agentId) {
  // Collect every per-tool state <select>; 'default' = inherit (no override
  // entry), the 3 concrete states write the {enabled, deferred} pair via the
  // shared toolStateToFlags mapping (settings_tools.js).
  const overrides = {};
  document.querySelectorAll('.tok-override').forEach(sel => {
    const tool = sel.dataset.tool;
    const state = sel.value;
    if (state === 'default') return;  // inherit — no override
    overrides[tool] = toolStateToFlags(state);
  });

  const threshVal = document.getElementById('tok-compact-threshold')?.value;
  // optimize_tools defaults ON; persist the explicit boolean so a saved-OFF
  // value sticks (resolver reads `!== false`).
  const optTools = document.getElementById('tok-optimize-tools');
  const tcfg = {
    tool_overrides: overrides,
    compact_threshold: threshVal ? parseInt(threshVal) / 100 : null,
    optimize_tools: optTools ? !!optTools.checked : true,
  };

  try {
    const res = await API.get(`/v1/agents/${agentId}/file?name=agent.json`);
    const cfg = JSON.parse(res.content || '{}');
    cfg.token_config = Object.assign(cfg.token_config || {}, tcfg);
    // Strip deprecated legacy fields when we save — resolver ignores them,
    // but keeping them on disk creates confusion.
    delete cfg.token_config.tool_groups;
    delete cfg.token_config.extra_tools;
    delete cfg.token_config.deferred_tool_groups;
    delete cfg.token_config.include_tools_guide;
    delete cfg.token_config.scheduled_task_tools;
    await API.post(`/v1/agents/${encodeURIComponent(agentId)}/file`, {
      name: 'agent.json',
      content: JSON.stringify(cfg, null, 2)
    });
    showToast(`Gespeichert (${Object.keys(overrides).length} Überschreibungen)`);
  } catch(e) { showToast('Fehler: ' + e.message, true); }
};

window._clearTokenOverrides = async function(agentId) {
  if (!confirm('ALLE Pro-Tool-Überschreibungen für diesen Agent löschen? Komprimierungsschwelle und Einstellungen für geplante Aufgaben bleiben erhalten.')) return;
  document.querySelectorAll('.tok-override').forEach(sel => sel.value = 'default');
  showToast('Überschreibungen gelöscht (nicht gespeichert — zum Übernehmen auf Speichern klicken)');
};

window._loadToolBreakdown = async function(agentId) {
  const container = document.getElementById('tok-breakdown');
  if (!container) return;
  container.innerHTML = '<span style="color:var(--text-400)">Wird gemessen…</span>';
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
        <div style="flex:${nm};background:#8b5cf6" title="Name ${nm} Tok"></div>
        <div style="flex:${ds};background:var(--accent-brand)" title="Beschreibung ${ds} Tok"></div>
        <div style="flex:${sc};background:var(--success)" title="Schema ${sc} Tok"></div>
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
        <span style="text-align:right;color:#8b5cf6" title="Name">${t.name_tokens}</span>
        <span style="text-align:right;color:var(--accent-brand)" title="Beschreibung">${t.desc_tokens}</span>
        <span style="text-align:right;color:var(--success)" title="Schema (${t.param_count} Parameter)">${t.schema_tokens}</span>
        <span style="text-align:right;font-weight:600;color:var(--text-100)">${t.total_tokens.toLocaleString()}</span>
      </div>`;
    };

    const rows = groups.map(g => {
      const isMcp = g.source === 'mcp';
      const deferBadge = g.deferred
        ? '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#fef3c7;color:#d97706">AUFGESCHOBEN</span>'
        : '';
      const header = `<div style="display:grid;grid-template-columns:${isMcp?'18px ':''}1fr 90px 50px 50px 50px 60px;gap:8px;padding:2px 0 4px;font-size:9px;color:var(--text-400);text-transform:uppercase;border-bottom:1px solid var(--border-050)">
        ${isMcp?'<span></span>':''}<span>Tool</span><span style="text-align:center">Aufteilung</span>
        <span style="text-align:right">Name</span><span style="text-align:right">Beschr.</span><span style="text-align:right">Schema</span><span style="text-align:right">Gesamt</span>
      </div>`;
      const tools = (g.tools || []).map(t => toolRow(t, isMcp)).join('');
      return `<details style="border-top:1px solid var(--border-050)">
        <summary style="cursor:pointer;padding:6px 0;display:flex;align-items:center;gap:8px">
          <span style="font-size:10px;padding:1px 6px;border-radius:3px;background:var(--bg-200);color:var(--text-400);text-transform:uppercase">${esc(g.source)}</span>
          <span style="font-size:12px;color:var(--text-200);min-width:100px">${esc(g.name)}</span>
          ${deferBadge}
          <span style="font-size:11px;color:var(--text-400)">${g.tool_count} Tools</span>
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
          <b>MCP-Tool-Filter:</b> Markieren Sie nur die MCP-Tools, die dieser Agent sehen soll. Nicht markierte Tools werden aus jeder Anfrage ausgeschlossen.
          ${filterActive ? `<span style="color:var(--warning)">&nbsp;(Filter derzeit aktiv)</span>` : ''}
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_saveMcpFilter('${esc(agentId)}', false)">Auswahl als Filter speichern</button>
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_clearMcpFilter('${esc(agentId)}')">Filter löschen (alle erlauben)</button>
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_toggleAllMcp(true)">Alle markieren</button>
          <button class="btn-secondary" style="font-size:11px;padding:2px 10px" onclick="_toggleAllMcp(false)">Alle abwählen</button>
        </div>
      </div>` : '';

    const legend = `<div style="display:flex;gap:14px;margin-top:6px;font-size:10px;color:var(--text-400)">
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;background:#8b5cf6;border-radius:2px"></span>Name</span>
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;background:var(--accent-brand);border-radius:2px"></span>Beschreibung</span>
      <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;background:var(--success);border-radius:2px"></span>Schema (Parameter)</span>
      <span style="display:flex;align-items:center;gap:4px;margin-left:auto">&#9888; = Schema &gt;60 % des Gesamtwerts</span>
    </div>`;

    const deferNote = defer.deferred
      ? `<div style="margin-top:10px;padding:8px 10px;border-radius:6px;background:var(--bg-200);font-size:11px;color:var(--text-300)">&#9888; MCP-Aufschub <b>aktiv</b> — ~${(defer.tokens_saved_if_deferred||0).toLocaleString()} MCP-Tokens derzeit aus Anfragen ausgeschlossen, bis <code>tool_search</code> sie entdeckt. Schwelle: ${(defer.threshold||0).toLocaleString()} Tokens.</div>`
      : (mcp > 0
          ? `<div style="margin-top:10px;padding:8px 10px;border-radius:6px;background:var(--bg-200);font-size:11px;color:var(--text-400)">MCP-Tools (${mcp.toLocaleString()} Tok) liegen unter der 10-%-Auto-Aufschub-Schwelle (${(defer.threshold||0).toLocaleString()} Tok) — alle MCP-Schemata werden bei jeder Anfrage eingeschlossen.</div>`
          : '');

    const builtinDeferTokens = b.deferred_builtin_tokens || 0;
    const builtinDeferNote = builtinDeferTokens > 0
      ? `<div style="margin-top:8px;padding:8px 10px;border-radius:6px;background:#fef3c7;font-size:11px;color:#92400e">
          Tool-Aufschub spart ~<b>${builtinDeferTokens.toLocaleString()}</b> Tokens/Anfrage
          (${(b.deferred_builtin_groups||[]).join(', ')} aufgeschoben, bis über <code>tool_search</code> entdeckt).
          Effektive Kosten: <b>${(total - builtinDeferTokens).toLocaleString()}</b> Tokens/Anfrage.
        </div>`
      : '';

    container.innerHTML = `
      <div style="display:flex;gap:16px;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--border-050)">
        <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">Gesamt</div><div style="font-size:18px;font-weight:600;color:var(--text-100)">${total.toLocaleString()}</div><div style="font-size:10px;color:var(--text-400)">Tokens / Anfrage</div></div>
        ${builtinDeferTokens > 0 ? `<div><div style="font-size:10px;color:#d97706;text-transform:uppercase">Aufgeschoben</div><div style="font-size:14px;color:#d97706">-${builtinDeferTokens.toLocaleString()}</div></div>` : ''}
        <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">Integriert</div><div style="font-size:14px;color:var(--text-200)">${builtin.toLocaleString()}</div></div>
        <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">MCP</div><div style="font-size:14px;color:var(--text-200)">${mcp.toLocaleString()}</div></div>
        ${b.max_context ? `<div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase">% v. Ktx</div><div style="font-size:14px;color:var(--text-200)">${(total/b.max_context*100).toFixed(1)}%</div></div>` : ''}
      </div>
      ${rows}
      ${legend}
      ${mcpActions}
      ${deferNote}
      ${builtinDeferNote}
    `;
  } catch(e) {
    container.innerHTML = `<span style="color:var(--error)">Fehlgeschlagen: ${esc(e.message)}</span>`;
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
  if (!all.length) { showToast('Keine MCP-Tools zum Filtern'); return; }
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
    if (!silent) showToast(checked.length === all.length ? 'Filter gelöscht (alle Tools erlaubt)' : `Filter gespeichert: ${checked.length} von ${all.length} Tools erlaubt`);
    _loadToolBreakdown(agentId);
  } catch(e) { showToast('Fehler: ' + e.message, true); }
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
    showToast('Filter gelöscht');
    _loadToolBreakdown(agentId);
  } catch(e) { showToast('Fehler: ' + e.message, true); }
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
      <span style="font-size:13px;font-weight:600;color:#141413">MCP-Server manuell hinzufügen</span>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <label style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Name</label>
          <input id="amcp-name" style="${_mcpInput};margin-top:4px" placeholder="z. B. github">
        </div>
        <div>
          <label style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Transport</label>
          <select id="amcp-transport" style="${_mcpInput};margin-top:4px" onchange="document.getElementById('amcp-url-label').textContent=this.value==='stdio'?'BEFEHL':'URL';document.getElementById('amcp-url').placeholder=this.value==='stdio'?'npx -y @modelcontextprotocol/server-github':'https://...'">
            <option value="stdio">stdio (Befehl)</option>
            <option value="sse">SSE (HTTP)</option>
          </select>
        </div>
      </div>
      <div>
        <label id="amcp-url-label" style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Befehl</label>
        <input id="amcp-url" style="${_mcpInput};font-family:var(--font-mono);margin-top:4px" placeholder="npx -y @modelcontextprotocol/server-github">
      </div>
      <div>
        <label style="font-size:10px;color:#73726c;text-transform:uppercase;letter-spacing:0.5px">Argumente (leerzeichengetrennt, für stdio)</label>
        <input id="amcp-args" style="${_mcpInput};font-family:var(--font-mono);margin-top:4px" placeholder="--arg1 value1">
      </div>
      <div style="display:flex;gap:8px;margin-top:2px">
        <button style="${_mcpBtn}" onclick="_doAgentMcpAdd('${esc(agentId)}')">Server hinzufügen</button>
        <button style="${_mcpBtnSec}" onclick="document.getElementById('agent-mcp-add-form').innerHTML=''">Abbrechen</button>
      </div>
    </div>`;
};

window._doAgentMcpAdd = async function(agentId) {
  const name = document.getElementById('amcp-name')?.value?.trim();
  const transport = document.getElementById('amcp-transport')?.value;
  const url = document.getElementById('amcp-url')?.value?.trim();
  const argsStr = document.getElementById('amcp-args')?.value?.trim();
  if (!name || !url) { showToast('Name und Befehl/URL erforderlich', true); return; }

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
    showToast(`${name} hinzugefügt`);
    switchAgentTab(agentId, 'mcp');
  } catch (e) { showToast(e.message, true); }
};

window._removeAgentMcp = async function(agentId, serverName) {
  if (!await showConfirmDanger(`MCP-Server „${serverName}" entfernen?`, 'MCP-Server entfernen', 'Entfernen')) return;
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
    showToast(`${serverName} entfernt`);
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
    container.innerHTML = '<div style="padding:24px;text-align:center;color:#a1a09a;font-size:12px">Tippen, um die offizielle MCP-Server-Registry zu durchsuchen</div>';
    return;
  }

  // Show loading state
  container.innerHTML = `<div style="padding:20px;text-align:center">
    <div style="display:inline-block;width:18px;height:18px;border:2px solid #e8e7e0;border-top-color:#d97757;border-radius:50%;animation:spin 0.6s linear infinite"></div>
    <div style="margin-top:8px;color:#73726c;font-size:12px">Registry wird nach „${esc(query)}" durchsucht…</div>
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
      container.innerHTML = `<div style="padding:24px;text-align:center;color:#73726c;font-size:12px">Keine Server für „${esc(query)}" gefunden. Versuchen Sie einen anderen Suchbegriff.</div>`;
      return;
    }
    window._mcpRegistryResults = servers;

    container.innerHTML = `<div style="font-size:11px;color:#a1a09a;margin-bottom:2px">${servers.length} Server gefunden</div>` + servers.map((s, idx) => {
      const shortName = (s.name || '').split('/').pop().replace(/^server-/, '');
      const alreadyInstalled = installedNames.has(shortName) || installedNames.has(s.name);
      const typeBadge = s.registry_type ? `<span style="${_mcpBadge}background:rgba(234,179,8,0.15);color:#b45309">${esc(s.registry_type)}</span>` : '';
      const transportBadge = `<span style="${_mcpBadge}background:rgba(59,130,246,0.12);color:#3b82f6">${esc(s.transport || 'stdio')}</span>`;
      const envBadge = s.env_vars?.length ? `<span style="${_mcpBadge}background:rgba(239,68,68,0.1);color:#dc2626">${s.env_vars.length} env</span>` : '';
      const installBtn = alreadyInstalled
        ? `<span style="font-size:10px;color:#16a34a;padding:3px 10px;background:rgba(22,163,74,0.08);border-radius:5px;white-space:nowrap">Hinzugefügt</span>`
        : `<button style="${_mcpBtn};padding:4px 12px;font-size:11px" onmouseover="this.style.background='#c66140'" onmouseout="this.style.background='#d97757'" onclick="_installFromRegistry('${esc(agentId)}',${idx},this)">Hinzufügen</button>`;
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
    container.innerHTML = `<div style="padding:16px;text-align:center;color:#dc2626;font-size:12px">Fehler beim Durchsuchen der Registry: ${esc(e.message)}</div>`;
  } finally {
    if (searchBtn) { searchBtn.textContent = 'Suchen'; searchBtn.disabled = false; }
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
      fields += '<div style="font-size:11px;font-weight:600;color:#141413">Umgebungsvariablen:</div>';
      fields += requiredEnv.map(e => `
        <div>
          <label style="font-size:10px;color:#73726c">${esc(e.name)}${e.description ? ' — ' + esc(e.description) : ''}</label>
          <input data-env="${esc(e.name)}" style="${_mcpInput};font-family:var(--font-mono);margin-top:2px" placeholder="${esc(e.name)}">
        </div>
      `).join('');
    }
    if (requiredArgs.length) {
      fields += '<div style="font-size:11px;font-weight:600;color:#141413;margin-top:4px">Argumente:</div>';
      fields += requiredArgs.map(a => `
        <div>
          <label style="font-size:10px;color:#73726c">--${esc(a.name)}${a.description ? ' — ' + esc(a.description) : ''}</label>
          <input data-arg="${esc(a.name)}" style="${_mcpInput};font-family:var(--font-mono);margin-top:2px" placeholder="${esc(a.name)}">
        </div>
      `).join('');
    }
    const formHtml = `
      <div id="${formId}" style="margin-top:8px;padding:12px;border:1px solid rgba(217,119,87,0.3);border-radius:8px;display:grid;gap:8px;background:rgba(217,119,87,0.03)">
        <div style="font-size:12px;font-weight:600;color:#141413">Vor dem Hinzufügen konfigurieren</div>
        ${fields}
        <button style="${_mcpBtn};justify-self:start" onclick="window._doRegistryInstall('${esc(agentId)}',${idx},'${formId}')">Server hinzufügen</button>
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
  if (btn) { btn.textContent = 'Wird hinzugefügt…'; btn.disabled = true; btn.style.opacity = '0.7'; }

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
    showToast(`${shortName} hinzugefügt`);
    // Remove config form if present
    if (formId) document.getElementById(formId)?.remove();
    // Update button to show "Added"
    if (btn) {
      btn.outerHTML = `<span style="font-size:10px;color:#16a34a;padding:3px 10px;background:rgba(22,163,74,0.08);border-radius:5px;white-space:nowrap">Hinzugefügt</span>`;
    }
    // Refresh configured servers list at top
    _refreshMcpServerList(agentId);
  } catch (e) {
    showToast(e.message, true);
    if (btn) { btn.textContent = 'Hinzufügen'; btn.disabled = false; btn.style.opacity = '1'; }
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
    listEl.innerHTML = '<div style="padding:16px;text-align:center;color:#73726c;font-size:12px">Keine MCP-Server konfiguriert.</div>';
    return;
  }
  let html = '';
  for (const [name, cfg] of Object.entries(allServers)) {
    const inherited = cfg._source === 'main' && agentId !== 'main';
    const transport = cfg.transport || cfg.type || (cfg.command ? 'stdio' : cfg.url ? 'sse' : '?');
    const target = cfg.command ? `${cfg.command} ${(cfg.args||[]).join(' ')}` : cfg.url || '';
    const live = liveStatus[name];
    const dot = live ? `<span style="width:7px;height:7px;border-radius:50%;background:${live.connected?'#16a34a':'#a1a09a'};flex-shrink:0"></span>` : '<span style="width:7px;height:7px;border-radius:50%;background:#d4d4d0;flex-shrink:0"></span>';
    const tools = live && live.tools_count ? `<span style="${_mcpBadge}background:rgba(22,163,74,0.1);color:#16a34a">${live.tools_count} Tools</span>` : '';
    const src = inherited ? `<span style="${_mcpBadge}background:rgba(120,120,140,0.15);color:#73726c">geerbt</span>` : `<span style="${_mcpBadge}background:rgba(139,92,246,0.15);color:#8b5cf6">lokal</span>`;
    const tp = `<span style="${_mcpBadge}background:rgba(59,130,246,0.12);color:#3b82f6">${esc(transport)}</span>`;
    const rm = !inherited ? `<button style="font-size:11px;color:#dc2626;padding:3px 10px;border-radius:5px;cursor:pointer;background:rgba(220,38,38,0.08);border:none;white-space:nowrap" onclick="_removeAgentMcp('${esc(agentId)}','${esc(name)}')">Entfernen</button>` : '';
    html += `<div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border:1px solid rgba(31,30,29,0.08);border-radius:8px">${dot}<div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span style="font-size:13px;font-weight:600;color:#141413">${esc(name)}</span>${tp} ${src} ${tools}</div><div style="font-size:11px;color:#73726c;font-family:var(--font-mono);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(target.slice(0,100))}</div></div>${rm}</div>`;
  }
  listEl.innerHTML = html;
};

async function saveAgentSoul(agentId) {
  const editor = document.getElementById('agent-soul-editor');
  if (!editor) return;
  try {
    await API.post(`/v1/agents/${agentId}/file`, { name: 'soul.md', content: editor.value });
    showToast('Soul gespeichert');
  } catch(e) {
    showToast('Speichern fehlgeschlagen: ' + e.message, true);
  }
}

async function refineAgentSoul(agentId) {
  const ta = document.getElementById('agent-soul-editor');
  if (!ta) return;
  const text = (ta.value || '').replace(/^\s+|\s+$/g, '');
  if (!text) { showToast('Schreiben Sie zuerst etwas', true); return; }
  const btn = document.getElementById('agent-soul-editor-refine');
  const lbl = document.getElementById('agent-soul-editor-refine-label');
  const origLabel = lbl?.textContent || 'Mit KI verfeinern';
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Verfeinere…';
  ta.disabled = true;
  const original = ta.value;
  // No caveman here — caveman_system on the agent's model already
  // compresses the soul dynamically at request time (see
  // _build_system_prompt). Compressing the saved soul.md too would
  // double-apply and lock the user into a level they can't reverse.
  const tier = _refineTierValue('soul:' + agentId);
  try {
    const result = await API.post('/v1/refine', {
      text, purpose: 'soul', field_label: agentId, tier,
    });
    if (result && result.refined && result.refined !== text) {
      ta.value = result.refined;
      if (lbl) lbl.textContent = 'Rückgängig';
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
      showToast('Verfeinert — prüfen und auf „Soul speichern" klicken oder Rückgängig');
    } else {
      showToast('Bereits sauber — keine Änderung');
      if (lbl) lbl.textContent = origLabel;
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('Verfeinern fehlgeschlagen: ' + (e.message || e), true);
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
  thinkBubble.textContent = 'Denkt nach…';
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
      html += '<div style="display:flex;gap:6px;margin-top:4px"><button class="btn-primary" style="font-size:11px;padding:3px 10px" onclick="applySoulEdit(this)">Übernehmen</button><span style="font-size:11px;color:var(--text-400)">Klicken, um den Editor zu aktualisieren</span></div>';
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
    errBubble.textContent = 'Fehler: ' + e.message;
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
    showToast('Soul im Editor aktualisiert — auf „Soul speichern" klicken zum Übernehmen');
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
    showToast('Agent-Konfiguration gespeichert');

    // Refresh agents list
    const agentsData = await API.getAgents();
    state.agents = agentsData.agents || agentsData || [];
  } catch(e) {
    showToast('Speichern fehlgeschlagen: ' + e.message, true);
  }
}


/* ─── Classification tab helpers ─── */

