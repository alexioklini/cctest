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
  static createSession(agent, model, project, status) {
    const body = { agent, model };
    if (project) body.project = project;
    if (status) body.status = status;
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
  static browseSkills(search) { return this.post('/v1/skills/browse', {search}); }
  static installSkill(skill, author, agent) { return this.post('/v1/skills/install', {skill, author, agent}); }
  static removeSkill(skill, agent) { return this.post('/v1/skills/remove', {skill, agent}); }

  // Memory

  // SSE Streaming Chat
  static async streamChat(sessionId, message, callbacks, model, files, images) {
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
}

/* ═══════════════════════════════════════════════════════════
   LOCAL INFERENCE — client-hosted local model (Electron + llama.cpp)
   Uses electronAPI.localInference.* to pull engine + weights lazily
   from the Brain server, spawn llama-server, and stream OpenAI-compat
   chat completions back to the server via the proxy-response channel.
   All downloads + IPC are scoped to a single instance.
   ═══════════════════════════════════════════════════════════ */
const LocalInference = {
  enabled: false,
  families: [],
  manifest: [],
  engineReady: false,      // engine binary has been downloaded
  modelReadySha: new Set(), // sha256s of weight files already cached
  _queue: Promise.resolve(),  // FIFO chain — max_concurrent=1
  _pending: new Map(),    // requestId -> { sessionId, aborted }

  async init() {
    try {
      const mf = await API.get('/v1/client/models/manifest');
      this.manifest = Array.isArray(mf.models) ? mf.models : [];
      const pref = JSON.parse(localStorage.getItem('local-inference') || '{}');
      this.enabled = !!(pref.enabled && window.electronAPI);
      this.families = Array.isArray(pref.families) ? pref.families : [];
      if (this.enabled) {
        this._bindIpcListeners();
        try {
          const status = await window.electronAPI.localInference.status();
          console.log('[LocalInference] Enabled — families:', this.families, 'status:', status);
        } catch (e) {
          console.warn('[LocalInference] status probe failed:', e.message);
        }
      }
    } catch (e) {
      console.warn('[LocalInference] init failed:', e.message);
    }
  },

  _bindIpcListeners() {
    if (this._ipcBound || !window.electronAPI?.localInference) return;
    this._ipcBound = true;
    const li = window.electronAPI.localInference;

    li.onChunk(({ requestId, line }) => {
      const pending = this._pending.get(requestId);
      if (!pending || pending.aborted) return;
      // llama-server emits `data: {...}\n\n` SSE frames. Ship the raw line
      // straight to the server's proxy channel — the engine already
      // normalises through _handle_openai_response.
      API.post('/v1/chat/proxy-response', {
        session_id: pending.sessionId,
        type: 'chunk',
        data: line,
      }).catch((e) => console.warn('[LocalInference] proxy-response chunk failed:', e.message));
    });

    li.onEnd(({ requestId }) => {
      const pending = this._pending.get(requestId);
      if (!pending) return;
      this._pending.delete(requestId);
      API.post('/v1/chat/proxy-response', {
        session_id: pending.sessionId,
        type: 'done',
      }).catch(() => {});
      // Report token usage (best-effort). We don't have real counts from
      // llama-server's streamed response beyond what the server's OpenAI
      // handler already logs — the server ingests the usage chunk. So
      // this POST is currently a placeholder for the explicit lane.
      if (pending.usage && pending.usage.tokens_in) {
        API.post('/v1/chat/local-inference-usage', {
          session_id: pending.sessionId,
          model: pending.modelId,
          tokens_in: pending.usage.tokens_in,
          tokens_out: pending.usage.tokens_out,
        }).catch(() => {});
      }
    });

    li.onError(({ requestId, message }) => {
      const pending = this._pending.get(requestId);
      if (!pending) return;
      this._pending.delete(requestId);
      API.post('/v1/chat/proxy-response', {
        session_id: pending.sessionId,
        type: 'error',
        message: 'Local inference: ' + message,
      }).catch(() => {});
    });

    li.onProgress((p) => {
      // Forwarded to the settings panel if it's open.
      if (typeof window.onLocalInferenceProgress === 'function') {
        try { window.onLocalInferenceProgress(p); } catch {}
      }
    });
  },

  async declareCapabilities(sessionId) {
    if (!sessionId) return;
    try {
      await API.post(`/v1/sessions/${sessionId}/capabilities`, {
        enabled: this.enabled,
        families: this.enabled ? this.families : [],
      });
    } catch (e) {
      console.warn('[LocalInference] capability handshake failed:', e.message);
    }
  },

  async _authToken() {
    // Token is whatever API.* uses for Authorization. Read the same
    // localStorage key the API module stores under.
    try {
      const t = localStorage.getItem('brain-auth-token') || '';
      return t;
    } catch { return ''; }
  },

  async ensureEngineAndModel(model) {
    if (!window.electronAPI?.localInference) {
      throw new Error('Desktop app required for local inference');
    }
    const li = window.electronAPI.localInference;
    const serverUrl = (await window.electronAPI.getServerUrl?.()) || location.origin;
    const authToken = await this._authToken();

    if (!this.engineReady) {
      const r = await li.ensureEngine({ serverUrl, authToken });
      if (!r || !r.ok) throw new Error('Engine download failed: ' + (r && r.error));
      this.engineReady = true;
    }
    if (!this.modelReadySha.has(model.sha256)) {
      const r = await li.ensureModel(model, serverUrl, authToken);
      if (!r || !r.ok) throw new Error('Model download failed: ' + (r && r.error));
      this.modelReadySha.add(model.sha256);
    }
  },

  async handleRequest(sessionId, data) {
    // Serialize requests per-installation (llama.cpp single-GPU reality).
    this._queue = this._queue.then(() => this._runOne(sessionId, data)).catch((e) => {
      console.error('[LocalInference] queue error:', e);
    });
    return this._queue;
  },

  async _runOne(sessionId, data) {
    const modelId = data.model;
    const entry = (this.manifest || []).find(m => m.id === modelId);
    if (!entry) {
      throw new Error('No manifest entry for model ' + modelId);
    }
    try {
      await this.ensureEngineAndModel(entry);
    } catch (e) {
      return API.post('/v1/chat/proxy-response', {
        session_id: sessionId, type: 'error',
        message: 'Local inference setup failed: ' + e.message,
      }).catch(() => {});
    }
    const requestId = (crypto.randomUUID && crypto.randomUUID()) ||
                      String(Date.now()) + Math.random().toString(16).slice(2);
    this._pending.set(requestId, { sessionId, modelId, aborted: false });
    window.electronAPI.localInference.run(requestId, data.payload, entry);
  },
};

/* ═══════════════════════════════════════════════════════════
   CLIENT EXECUTION MODE — proxy LLM + web tools through browser
   ═══════════════════════════════════════════════════════════ */
const ClientProxy = {
  enabled: false,
  providers: {},
  exaApiKey: '',

  async init() {
    try {
      const cfg = await API.get('/v1/config/execution-mode');
      this.enabled = cfg.execution_mode === 'client';
      if (this.enabled) {
        this.providers = cfg.providers || {};
        this.exaApiKey = cfg.exa_api_key || '';
        console.log('[ClientProxy] Enabled — LLM calls + web tools execute in browser');
      }
    } catch (e) {
      console.warn('[ClientProxy] Failed to check execution mode:', e.message);
    }
  },

  async handleProxyRequest(sessionId, data) {
    if (data.type === 'llm') {
      await this._proxyLLM(sessionId, data);
    }
  },

  async handleProxyTool(sessionId, data) {
    const { tool_call_id, name, args } = data;
    let result;
    try {
      if (name === 'web_fetch') {
        result = await this._execWebFetch(args);
      } else if (name === 'exa_search') {
        result = await this._execExaSearch(args);
      } else {
        result = JSON.stringify({ error: `Unknown proxy tool: ${name}` });
      }
    } catch (e) {
      result = JSON.stringify({ error: `${name}: ${e.message}` });
    }
    try {
      await API.post('/v1/chat/proxy-tool-result', {
        session_id: sessionId,
        tool_call_id,
        result,
      });
    } catch (e) {
      console.error('[ClientProxy] Failed to send tool result:', e);
    }
  },

  async _proxyLLM(sessionId, data) {
    const { endpoint, headers, payload } = data;
    try {
      // Electron: stream LLM via Node.js main process (no CORS)
      if (window.electronAPI) {
        await this._proxyLLMElectron(sessionId, endpoint, headers, payload);
        return;
      }
      let resp;
      try {
        resp = await fetch(endpoint, {
          method: 'POST',
          headers: { ...headers, 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } catch (fetchErr) {
        console.error('[ClientProxy] Fetch failed (likely CORS):', fetchErr);
        await API.post('/v1/chat/proxy-response', {
          session_id: sessionId,
          type: 'error',
          message: `Failed to reach LLM provider: ${fetchErr.message}. If CORS error, the provider must set Access-Control-Allow-Origin headers for browser requests.`,
        });
        return;
      }
      if (!resp.ok) {
        const errText = await resp.text().catch(() => '');
        await API.post('/v1/chat/proxy-response', {
          session_id: sessionId,
          type: 'error',
          message: `HTTP ${resp.status}: ${errText.slice(0, 500)}`,
        });
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let lineBuf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        lineBuf += decoder.decode(value, { stream: true });
        const parts = lineBuf.split('\n');
        lineBuf = parts.pop();
        const lines = parts.map(l => l.trim()).filter(l => l);
        if (lines.length) {
          await API.post('/v1/chat/proxy-response', {
            session_id: sessionId,
            type: 'chunks',
            lines,
          });
        }
      }
      if (lineBuf.trim()) {
        await API.post('/v1/chat/proxy-response', {
          session_id: sessionId,
          type: 'chunks',
          lines: [lineBuf.trim()],
        });
      }
      await API.post('/v1/chat/proxy-response', {
        session_id: sessionId,
        type: 'done',
      });
    } catch (e) {
      try {
        await API.post('/v1/chat/proxy-response', {
          session_id: sessionId,
          type: 'error',
          message: e.message,
        });
      } catch (_) {}
    }
  },

  async _proxyLLMElectron(sessionId, endpoint, headers, payload) {
    return new Promise((resolve) => {
      let lineBuf = '';
      window.electronAPI.removeStreamListeners();
      window.electronAPI.onStreamChunk(async (chunk) => {
        lineBuf += chunk;
        const parts = lineBuf.split('\n');
        lineBuf = parts.pop();
        const lines = parts.map(l => l.trim()).filter(l => l);
        if (lines.length) {
          await API.post('/v1/chat/proxy-response', {
            session_id: sessionId,
            type: 'chunks',
            lines,
          });
        }
      });
      window.electronAPI.onStreamEnd(async () => {
        if (lineBuf.trim()) {
          await API.post('/v1/chat/proxy-response', {
            session_id: sessionId,
            type: 'chunks',
            lines: [lineBuf.trim()],
          });
        }
        window.electronAPI.removeStreamListeners();
        await API.post('/v1/chat/proxy-response', { session_id: sessionId, type: 'done' });
        resolve();
      });
      window.electronAPI.onStreamError(async (msg) => {
        window.electronAPI.removeStreamListeners();
        await API.post('/v1/chat/proxy-response', { session_id: sessionId, type: 'error', message: msg });
        resolve();
      });
      window.electronAPI.proxyFetchStream({
        url: endpoint,
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });
    });
  },

  async _execWebFetch(args) {
    const url = args.url || '';
    const method = args.method || 'GET';
    const hdrs = args.headers || {};
    const body = args.body || null;
    const maxLength = args.max_length || 50000;

    // Electron: CORS-free fetch via Node.js main process
    if (window.electronAPI) {
      const res = await window.electronAPI.webFetch({ url, method, headers: hdrs, body, maxLength });
      return JSON.stringify(res);
    }

    const fetchOpts = { method, headers: hdrs };
    if (body) fetchOpts.body = body;

    const resp = await fetch(url, fetchOpts);
    let text = await resp.text();
    if (text.length > maxLength) text = text.slice(0, maxLength) + '\n... (truncated)';
    return JSON.stringify({ url, status: resp.status, length: text.length, content: text });
  },

  async _execExaSearch(args) {
    const query = args.query || '';
    const numResults = args.num_results || 5;
    const category = args.category || null;

    // Electron: CORS-free Exa search via Node.js main process
    if (window.electronAPI) {
      const data = await window.electronAPI.exaSearch({ query, numResults, category, apiKey: this.exaApiKey });
      if (data.error) return JSON.stringify(data);
      const results = (data.results || []).map(r => ({
        title: r.title || '',
        link: r.url || '',
        snippet: (r.highlights || []).join(' '),
      }));
      const searchInfo = { query, results, result_count: results.length };
      if (category) searchInfo.category = category;
      if (!results.length) searchInfo.message = 'No search results found. Try a different query.';
      return JSON.stringify(searchInfo, null, 1);
    }

    const body = {
      query,
      type: 'auto',
      num_results: numResults,
      contents: { highlights: { max_characters: 4000 } },
    };
    if (category) body.category = category;

    const resp = await fetch('https://api.exa.ai/search', {
      method: 'POST',
      headers: {
        'x-api-key': this.exaApiKey,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    const results = (data.results || []).map(r => ({
      title: r.title || '',
      link: r.url || '',
      snippet: (r.highlights || []).join(' '),
    }));
    const searchInfo = { query, results, result_count: results.length };
    if (category) searchInfo.category = category;
    if (!results.length) searchInfo.message = 'No search results found. Try a different query.';
    return JSON.stringify(searchInfo, null, 1);
  },
};


