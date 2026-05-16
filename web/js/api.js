'use strict';

/* ═══════════════════════════════════════════════════════════
   CONSTANTS & CONFIG
   ═══════════════════════════════════════════════════════════ */
const BASE_URL = window.location.origin;
const SSE_KEEPALIVE_TIMEOUT = 15000;

/* ═══════════════════════════════════════════════════════════
   API CLASS — All server communication
   ═══════════════════════════════════════════════════════════ */
class API {
  static _abortController = null;

  static _headers(extra) {
    const h = {'Content-Type':'application/json'};
    const t = localStorage.getItem('auth-token');
    if (t) h['Authorization'] = `Bearer ${t}`;
    if (extra) Object.assign(h, extra);
    return h;
  }
  static _handleAuthError(r) {
    if (r.status === 401) { authLogout(); return true; }
    return false;
  }
  static async get(path) {
    let r;
    try { r = await fetch(`${BASE_URL}${path}`, {headers: this._headers()}); } catch(e) { throw new Error(`GET ${path}: ${e.message}`); }
    if (this._handleAuthError(r)) return {};
    if (!r.ok) throw new Error(`GET ${path}: ${r.status}`);
    return r.json();
  }
  static async post(path, body) {
    let r;
    try { r = await fetch(`${BASE_URL}${path}`, { method:'POST', headers: this._headers(), body: JSON.stringify(body) }); } catch(e) { throw new Error(`POST ${path}: ${e.message}`); }
    if (this._handleAuthError(r)) return {};
    if (!r.ok) { const t = await r.text(); throw new Error(`POST ${path}: ${r.status} ${t}`); }
    return r.json();
  }
  static async put(path, body) {
    let r;
    try { r = await fetch(`${BASE_URL}${path}`, { method:'PUT', headers: this._headers(), body: JSON.stringify(body) }); } catch(e) { throw new Error(`PUT ${path}: ${e.message}`); }
    if (this._handleAuthError(r)) return {};
    if (!r.ok) throw new Error(`PUT ${path}: ${r.status}`);
    return r.json();
  }
  static async del(path) {
    let r;
    try { r = await fetch(`${BASE_URL}${path}`, { method:'DELETE', headers: this._headers() }); } catch(e) { throw new Error(`DELETE ${path}: ${e.message}`); }
    if (this._handleAuthError(r)) return {};
    if (!r.ok) throw new Error(`DELETE ${path}: ${r.status}`);
    return r.json();
  }

  // Agents
  static getStatus() { return this.get('/v1/status'); }
  static getAgents() { return this.get('/v1/agents'); }
  static getModels() { return this.get('/v1/models'); }
  static getProviders() { return this.get('/v1/providers'); }
  static createAgent(a) { return this.post('/v1/agents/create', a); }
  static deleteAgent(id) { return this.post('/v1/agents/delete', {agent:id}); }
  static getAgentConfig(id) { return this.get(`/v1/agents/${id}/config`); }
  static saveAgentConfig(id, cfg) { return this.post(`/v1/agents/${id}/config`, cfg); }

  // Sessions
  static createSession(agent, model, project, status, workflowRunId) {
    const body = { agent, model };
    if (project) body.project = project;
    if (status) body.status = status;
    if (workflowRunId) body.workflow_run_id = workflowRunId;
    return this.post('/v1/sessions', body);
  }
  static deleteSession(id) { return this.del(`/v1/sessions/${id}`); }
  static getSessionsForAgent(agentId, status) {
    let url = `/v1/sessions?agent=${agentId}`;
    if (status) url += `&status=${status}`;
    return this.get(url);
  }
  static getSessionMessages(id) { return this.get(`/v1/sessions/${id}/messages`); }
  static manageSession(body) { return this.post('/v1/sessions/manage', body); }
  static inspectSession(sessionId) { return this.get(`/v1/sessions/${encodeURIComponent(sessionId)}/inspect`); }
  static cancelChat(sessionId) { return this.post('/v1/chat/cancel', {session_id: sessionId}); }
  // Transparent anonymisation: deliver the user's choice on the
  // anonymisation-failure recovery modal. `action` is 'local_model' | 'cancel'.
  // There is intentionally no 'send_to_cloud_anyway' value — the server
  // refuses any other action with 400.
  static chatGdprRecovery(sessionId, action) {
    return this.post('/v1/chat/gdpr-recovery', {session_id: sessionId, action});
  }

  // Admin-only audit endpoints for transparent anonymisation (step 6.4).
  // listSessionGdprMaps returns row metadata (mapping_id, turn_id,
  // created_at) — bodies stay encrypted at rest. getSessionGdprMap
  // decrypts one specific mapping and returns the before/after pairs so
  // the auditor can see what was sent to the cloud vs. what the user
  // typed. Both refuse with 403 for non-admin callers.
  static listSessionGdprMaps(sessionId) {
    return this.get(`/v1/sessions/${encodeURIComponent(sessionId)}/gdpr-maps`);
  }
  static getSessionGdprMap(sessionId, mappingId) {
    return this.get(`/v1/sessions/${encodeURIComponent(sessionId)}/gdpr-maps/${encodeURIComponent(mappingId)}`);
  }

  // Sticky GDPR action preference (step 6.2). value ∈ {'', 'anonymise',
  // 'local_model', 'continue'}. '' clears the preference (modal asks
  // again on next send). 'cancel' is rejected server-side (400) — never
  // valid as a persisted choice.
  static updateGdprActionPref(sessionId, value) {
    return this.post('/v1/sessions/manage', {
      action: 'gdpr_action_pref',
      session_id: sessionId,
      value: value || '',
    });
  }

  // Projects
  static getProjects(agent) { return this.get(`/v1/agents/${agent}/projects`); }
  static createProject(agent, body) {
    return this.post(`/v1/agents/${agent}/projects`, body);
  }
  static lookupUsers() { return this.get('/v1/auth/users/lookup'); }
  static getProject(agent, name) { return this.get(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}`); }
  static updateProject(agent, name, cfg) { return this.put(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}`, cfg); }
  static deleteProject(agent, name) { return this.del(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}`); }

  // Costs
  static getCosts(hours, agent) {
    let url = `/v1/costs?hours=${hours||24}`;
    if (agent) url += `&agent=${agent}`;
    return this.get(url);
  }
  static getCostsDaily(days, agent) {
    let url = `/v1/costs/daily?days=${days||7}`;
    if (agent) url += `&agent=${agent}`;
    return this.get(url);
  }

  // Schedule
  static getSchedule() { return this.get('/v1/schedule'); }
  static manageSchedule(body) { return this.post('/v1/schedule', body); }
  static getRunningTasks() { return this.get('/v1/schedule/running'); }
  static cancelScheduledTask(name) { return this.post('/v1/schedule/cancel', { name }); }

  // Models config
  static getModelsConfig() { return this.get('/v1/models/config'); }
  static saveModelsConfig(cfg) { return this.post('/v1/models/config', cfg); }

  // Teams
  static getTeams() { return this.get('/v1/teams'); }
  static manageTeams(body) { return this.post('/v1/teams', body); }
  static getAgentActivity() { return this.get('/v1/agents/activity'); }

  // Services
  static getServices() { return this.get('/v1/services'); }
  static restartServer() { return this.post('/v1/restart', {}); }

  // Commands
  static expandCommand(agent, cmd) { return this.post('/v1/commands/expand', {agent, command:cmd}); }
  static getCustomCommands(agent) { return this.get(`/v1/agents/${agent}/commands`); }

  // Files
  static getFilePreview(path) { return this.get(`/v1/files/preview?path=${encodeURIComponent(path)}&lines=100`); }
  static getFileDownloadUrl(path) { return `${BASE_URL}/v1/files/download?path=${encodeURIComponent(path)}`; }

  // Artifacts
  static getArtifacts(sessionId) { return this.get(`/v1/artifacts?session_id=${encodeURIComponent(sessionId)}`); }
  static browseArtifacts(agentId, limit) { return this.get(`/v1/artifacts/browse?${agentId ? 'agent_id=' + encodeURIComponent(agentId) + '&' : ''}limit=${limit || 100}`); }
  static getArtifactContent(id, version) { return this.get(`/v1/artifacts/${id}/content${version ? '?version=' + version : ''}`); }
  static getArtifactDownloadUrl(id, version) { return `${BASE_URL}/v1/artifacts/${id}/download${version ? '?version=' + version : ''}`; }

  // Skills
  static getClaudeCodeSkills(agent) { return this.get(`/v1/skills/claude-code?agent=${agent}`); }
  static toggleCCSkill(agent, slug, enabled) { return this.post('/v1/skills/claude-code', {agent, slug, enabled}); }
  static browseCCPlugins(query) { return this.post('/v1/skills/claude-code/browse', {query}); }
  static installCCPlugin(plugin, marketplace) { return this.post('/v1/skills/claude-code/install', {plugin, marketplace}); }
  static removeSkill(skill, agent) { return this.post('/v1/skills/remove', {skill, agent}); }

  // Memory

  // SSE Streaming Chat
  static async streamChat(sessionId, message, callbacks, model, files, images, gdprAction) {
    if (this._abortController) this._abortController.abort();
    this._abortController = new AbortController();

    const body = {
      session_id: sessionId,
      message,
      interactive: true,
    };
    if (model) body.model = model;
    if (state.planModeActive) body.mode = 'plan';
    if (state.thinkingLevel && state.thinkingLevel !== 'none') body.thinking = state.thinkingLevel;
    if (state.currentProject) body.project = state.currentProject;
    if (images && images.length) {
      body.images = images.map(i => ({data: i.data, media_type: i.type}));
    }
    if (files && files.length) {
      body.files = files.map(f => ({
        name: f.name,
        content: f.content || f.data,
        encoding: f.encoding,
        media_type: f.type,
      }));
    }
    // Transparent anonymisation: forwards the verdict from the pre-send
    // GDPR modal so the chat worker can pseudonymise / model-swap / pass
    // through. Server validates; unknown values are treated as null.
    if (gdprAction) body.gdpr_action = gdprAction;

    try {
      const resp = await fetch(`${BASE_URL}/v1/chat`, {
        method: 'POST',
        headers: this._headers(),
        body: JSON.stringify(body),
        signal: this._abortController.signal,
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      let lastEventType = null;
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});

        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            lastEventType = line.slice(7).trim();
          } else if (line.startsWith('data: ') && lastEventType) {
            try {
              const data = JSON.parse(line.slice(6));
              if (callbacks[lastEventType]) callbacks[lastEventType](data);
            } catch(e) {
              console.error('[SSE] JSON parse failed for event:', lastEventType, 'line length:', line.length, e.message);
            }
            lastEventType = null;
          } else if (line.startsWith(':')) {
            // keepalive comment
          }
        }
      }
      // Process any remaining data in buffer after stream ends
      if (buffer.trim()) {
        const remaining = buffer.split('\n');
        for (const line of remaining) {
          if (line.startsWith('event: ')) {
            lastEventType = line.slice(7).trim();
          } else if (line.startsWith('data: ') && lastEventType) {
            try {
              const data = JSON.parse(line.slice(6));
              if (callbacks[lastEventType]) callbacks[lastEventType](data);
            } catch(e) {}
            lastEventType = null;
          }
        }
      }
    } catch(e) {
      if (e.name !== 'AbortError' && !/Load failed|Failed to fetch|NetworkError/i.test(e.message)) {
        if (callbacks.error) callbacks.error({message: e.message});
      }
    }
  }

  /** Attach to an in-progress turn (GET /v1/chat/stream). Replays buffered
   *  events then follows live ones. Same callback map as streamChat, plus an
   *  `idle` callback fired when there's nothing to attach to. Uses a separate
   *  abort controller — aborting it never affects the server-side worker. */
  static async attachStream(sessionId, callbacks) {
    if (this._streamController) this._streamController.abort();
    this._streamController = new AbortController();
    try {
      const resp = await fetch(`${BASE_URL}/v1/chat/stream?session_id=${encodeURIComponent(sessionId)}`, {
        method: 'GET',
        headers: this._headers(),
        signal: this._streamController.signal,
      });
      if (!resp.ok || !resp.body) { if (callbacks.idle) callbacks.idle({}); return; }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let lastEventType = null;
      const dispatch = (line) => {
        if (line.startsWith('event: ')) {
          lastEventType = line.slice(7).trim();
        } else if (line.startsWith('data: ') && lastEventType) {
          try {
            const data = JSON.parse(line.slice(6));
            if (callbacks[lastEventType]) callbacks[lastEventType](data);
          } catch(e) {}
          lastEventType = null;
        }
      };
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) dispatch(line);
      }
      if (buffer.trim()) for (const line of buffer.split('\n')) dispatch(line);
    } catch(e) {
      if (e.name !== 'AbortError' && !/Load failed|Failed to fetch|NetworkError/i.test(e.message)) {
        if (callbacks.error) callbacks.error({message: e.message});
      }
    }
  }

  static abortStreamAttach() {
    if (this._streamController) { try { this._streamController.abort(); } catch(e){} this._streamController = null; }
  }
}



