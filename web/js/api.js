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

  // Wiki
  static wikiTree(filter, opts) {
    const q = new URLSearchParams({ filter: filter || 'all' });
    if (opts?.team_id) q.set('team_id', opts.team_id);
    if (opts?.project_id) q.set('project_id', opts.project_id);
    return this.get(`/v1/wiki/tree?${q.toString()}`);
  }
  static wikiGet(id) { return this.get(`/v1/wiki/pages/${id}`); }
  static wikiVersions(id) { return this.get(`/v1/wiki/pages/${id}/versions`); }
  static wikiVersion(id, n) { return this.get(`/v1/wiki/pages/${id}/versions/${n}`); }
  static wikiCreate(body) { return this.post('/v1/wiki/pages', body); }
  static wikiUpdate(id, body) { return this.put(`/v1/wiki/pages/${id}`, body); }
  static wikiPromote(id, n) { return this.post(`/v1/wiki/pages/${id}/promote/${n}`, {}); }
  static wikiTags() { return this.get('/v1/wiki/tags'); }
  static wikiSaveTag(name, color) { return this.post('/v1/wiki/tags', { name, color }); }
  static wikiDeleteTag(name) { return this.del(`/v1/wiki/tags/${encodeURIComponent(name)}`); }
  static wikiRenameTag(oldName, newName) { return this.post('/v1/wiki/tags/rename', { old: oldName, new: newName }); }
  static wikiMove(id, body) { return this.post(`/v1/wiki/pages/${id}/move`, body); }
  static wikiGenerate(id, body) { return this.post(`/v1/wiki/pages/${id}/generate`, body); }
  static async wikiMedia(id, file) {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const h = {}; const t = localStorage.getItem('auth-token'); if (t) h['Authorization'] = `Bearer ${t}`;
    const r = await fetch(`${BASE_URL}/v1/wiki/pages/${id}/media`, { method: 'POST', headers: h, body: fd });
    return r.json();
  }
  static wikiDelete(id) { return this.del(`/v1/wiki/pages/${id}`); }

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
  // IDs of sessions with a live turn running — drives the "läuft gerade" pills
  // in the sidebar + project chat lists.
  static getActiveSessions() { return this.get('/v1/sessions/active'); }
  static getSessionMessages(id) { return this.get(`/v1/sessions/${id}/messages`); }
  // Export the chat as markdown into the session's artifacts folder.
  // kind: 'summary' (LLM, chat_summary_model) | 'dump' (verbatim, no LLM).
  static exportChat(sessionId, kind) {
    return this.post('/v1/sessions/export', { session_id: sessionId, kind });
  }
  // Build a complete-chat zip bundle with live SSE progress. `callbacks` map:
  // {progress({percent,stage}), done({token,filename,size}), error({message})}.
  // Streams via fetch-reader (Bearer header works; EventSource cannot send it).
  static async exportBundle(sessionId, callbacks) {
    const resp = await fetch(`${BASE_URL}/v1/sessions/export-bundle`, {
      method: 'POST',
      headers: this._headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!resp.ok || !resp.body) {
      if (callbacks.error) callbacks.error({ message: `HTTP ${resp.status}` });
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let lastEventType = null;
    // Terminal events (`done`/`error`) end the stream. We must NOT wait for the
    // connection to close — the server may hold it briefly after emitting `done`,
    // and the browser's reader would block on read() forever, hanging the whole
    // download. So we break the loop as soon as a terminal event is dispatched.
    let finished = false;
    const dispatch = (line) => {
      if (line.startsWith('event: ')) {
        lastEventType = line.slice(7).trim();
      } else if (line.startsWith('data: ') && lastEventType) {
        let data = null, parsed = false;
        try { data = JSON.parse(line.slice(6)); parsed = true; } catch (e) {}
        const evType = lastEventType;
        if (parsed && callbacks[evType]) {
          try { callbacks[evType](data); }
          catch (cbErr) { console.error('[bundle SSE] callback threw:', evType, cbErr); }
        }
        if (evType === 'done' || evType === 'error') finished = true;
        lastEventType = null;
      }
    };
    while (!finished) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        dispatch(line);
        if (finished) break;
      }
    }
    if (!finished && buffer.trim()) for (const line of buffer.split('\n')) dispatch(line);
    // Release the stream so the held connection is torn down promptly.
    try { await reader.cancel(); } catch (e) {}
  }
  // Fetch the finished bundle zip (Bearer-authed) and return a Blob.
  static async fetchBundle(token) {
    const resp = await fetch(`${BASE_URL}/v1/sessions/export-bundle/download?token=${encodeURIComponent(token)}`, {
      method: 'GET', headers: this._headers(),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.blob();
  }
  // Server-side PII summary over the session's history (regex + spaCy NER).
  // The composer history badge in nav.js unions these counts with its local
  // regex scan so soft-PII (name/address/organisation) only NER detects also
  // surfaces.
  static getSessionPiiHistorySummary(id) {
    return this.get(`/v1/sessions/${encodeURIComponent(id)}/pii-history-summary`);
  }
  static getSessionPiiHistoryDetail(id) {
    return this.get(`/v1/sessions/${encodeURIComponent(id)}/pii-history-detail`);
  }
  static getSessionPiiDecisionsView(id) {
    return this.get(`/v1/sessions/${encodeURIComponent(id)}/pii-decisions-view`);
  }
  static manageSession(body) { return this.post('/v1/sessions/manage', body); }
  static webSearch(query) { return this.post('/v1/web/search', { query }); }
  static inspectSession(sessionId) { return this.get(`/v1/sessions/${encodeURIComponent(sessionId)}/inspect`); }
  static cancelChat(sessionId) { return this.post('/v1/chat/cancel', {session_id: sessionId}); }
  // Turn-control (pause/resume a running turn, inject a mid-stream message the
  // model sees next round, ask a side question answered in a separate bubble).
  static pauseChat(sessionId)  { return this.post('/v1/chat/pause',  {session_id: sessionId}); }
  static resumeChat(sessionId) { return this.post('/v1/chat/resume', {session_id: sessionId}); }
  static injectChat(sessionId, message) { return this.post('/v1/chat/inject', {session_id: sessionId, message}); }
  static btwChat(sessionId, message)    { return this.post('/v1/chat/btw',    {session_id: sessionId, message}); }
  // Transparent anonymisation: deliver the user's choice on the
  // anonymisation-failure recovery modal. `action` is 'local_model' | 'cancel'.
  // There is intentionally no 'send_to_cloud_anyway' value — the server
  // refuses any other action with 400.
  static chatGdprRecovery(sessionId, action) {
    return this.post('/v1/chat/gdpr-recovery', {session_id: sessionId, action});
  }

  // Upload-time PII scan for one chat attachment. The server saves the file
  // to /tmp/brain-attachments/<sid>/, extracts text via the same parsers
  // tool_read_document uses, runs _pii_scan_text, returns
  // {scanned, attachment_id, source_name, findings, categories,
  //  finding_count, reason?}. `scanned: false` with reason in
  // {archive, media} is an accepted gap; reason in
  // {unsupported, too_large, extract_timeout, extract_failed} is BLOCKING
  // — the composer must refuse to send while any such attachment is
  // pending.
  static scanAttachment(sessionId, file, opts) {
    const body = {
      session_id: sessionId,
      name: file.name,
      content: file.data,
      encoding: file.encoding || 'base64',
      media_type: file.type || 'application/octet-stream',
    };
    // opts.signal → AbortSignal so the SEND-time progress overlay can CANCEL a
    // slow attachment scan (extract/OCR/NER on a large doc). Aborted fetch
    // rejects with AbortError → caller maps to "send cancelled".
    if (opts && opts.signal) {
      return (async () => {
        const r = await fetch(`${BASE_URL}/v1/attachments/scan`, {
          method: 'POST', headers: this._headers(),
          body: JSON.stringify(body), signal: opts.signal,
        });
        if (!r.ok) throw new Error(`POST /v1/attachments/scan: ${r.status}`);
        return r.json();
      })();
    }
    return this.post('/v1/attachments/scan', body);
  }

  // Server-side text scan — the ONLY PII detector for the typed message
  // (9.200.0: the browser regex scanner was removed). Runs the full
  // _pii_scan_text pipeline (regex + spaCy NER + confidence bands). With
  // {full:true} returns per-finding values the pre-send modal needs; with
  // {signal} the scan is cancellable. Network failures reject (the caller
  // fails open and sends without typed-text findings).
  static scanText(text, source, opts) {
    const body = { text, source: source || 'compose' };
    // full:true → per-finding values + confidence/band/disposition (the modal
    // needs these for the per-finding review UI). Default off (grouped shape).
    if (opts && opts.full) body.full = true;
    // opts.signal → AbortSignal so the pre-send progress UI can CANCEL a slow
    // scan (NER on a large message). Aborted fetch rejects with an AbortError,
    // which the caller maps to a "send cancelled". Without a signal, behaves
    // exactly like before.
    if (opts && opts.signal) {
      return (async () => {
        const r = await fetch(`${BASE_URL}/v1/gdpr/scan-text`, {
          method: 'POST', headers: this._headers(),
          body: JSON.stringify(body), signal: opts.signal,
        });
        if (!r.ok) throw new Error(`POST /v1/gdpr/scan-text: ${r.status}`);
        return r.json();
      })();
    }
    return this.post('/v1/gdpr/scan-text', body);
  }

  // Per-finding PII review decisions (9.196.0).
  static recordPiiDecisions(sessionId, turnAction, decisions, turnId) {
    return this.post('/v1/gdpr/decisions', {
      session_id: sessionId, turn_action: turnAction,
      decisions: decisions || [], turn_id: turnId || '',
    });
  }
  static getPiiDecisions(sessionId) {
    return this.get('/v1/gdpr/decisions?session_id=' + encodeURIComponent(sessionId));
  }
  static getPiiDecisionStats() {
    return this.get('/v1/gdpr/decisions/stats');
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

  // Sticky opt-in for the post-turn GDPR feedback modal. true → the modal
  // fires after every GDPR-action turn; false → no feedback prompts.
  static updateGdprFeedbackAsk(sessionId, value) {
    return this.post('/v1/sessions/manage', {
      action: 'gdpr_feedback_ask',
      session_id: sessionId,
      value: !!value,
    });
  }

  // Per-session "Datenschutz-Details sichtbar" toggle (shield detail switch).
  // Persisted per chat so the mark overlays + detail block visibility restore
  // on reload of the chat.
  static updateGdprDetailsVisible(sessionId, value) {
    return this.post('/v1/sessions/manage', {
      action: 'gdpr_details_visible',
      session_id: sessionId,
      value: !!value,
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
  static listProjectInstructionFiles(agent, name) { return this.get(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}/instruction-files`); }
  // XHR (not fetch) so upload.onprogress can drive a progress bar for big files.
  // onProgress(pct|null) — pct is 0..100, or null once the bytes are sent and the
  // server is still processing (length not computable / 100% reached).
  static uploadProjectInstructionFile(agent, name, file, onProgress) {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append('file', file, file.name);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${BASE_URL}/v1/agents/${agent}/projects/${encodeURIComponent(name)}/instruction-files`);
      const t = localStorage.getItem('auth-token');
      if (t) xhr.setRequestHeader('Authorization', `Bearer ${t}`);
      if (typeof onProgress === 'function' && xhr.upload) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            onProgress(pct >= 100 ? null : pct);  // 100% sent → server processing
          } else {
            onProgress(null);
          }
        };
        xhr.upload.onload = () => onProgress(null);  // bytes done → processing
      }
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText || '{}'); } catch (e) { /* keep {} */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(body.error || `HTTP ${xhr.status}`));
      };
      xhr.onerror = () => reject(new Error('Netzwerkfehler beim Upload'));
      xhr.send(fd);
    });
  }
  static deleteProjectInstructionFile(agent, name, filename) { return this.del(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}/instruction-files/${encodeURIComponent(filename)}`); }
  // AI-generation of project instructions (agentic, review-before-save).
  static generateProjectInstructions(agent, name, prompt) { return this.post(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}/generate-instructions`, { prompt }); }
  static getInstructionGen(agent, name, genId) { return this.get(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}/instruction-gen/${encodeURIComponent(genId)}`); }
  static cancelInstructionGen(agent, name, genId) { return this.post(`/v1/agents/${agent}/projects/${encodeURIComponent(name)}/instruction-gen/${encodeURIComponent(genId)}/cancel`, {}); }

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
  // Per-use-case × per-model cost breakdown for a named time window.
  static getCostBreakdown(window) {
    return this.get(`/v1/costs/breakdown?window=${encodeURIComponent(window||'30d')}`);
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
  static getArtifactThumbnailUrl(id, version) { return `${BASE_URL}/v1/artifacts/${id}/thumbnail${version ? '?version=' + version : ''}`; }

  // Project outputs (Output Presets / Studio — the SHARED store + generate endpoint)
  static _projOutBase(agentId, projectName) { return `/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}`; }
  static generateProjectOutput(agentId, projectName, kind, options) { return this.post(`${this._projOutBase(agentId, projectName)}/generate`, {kind, options: options || {}}); }
  static listProjectOutputs(agentId, projectName) { return this.get(`${this._projOutBase(agentId, projectName)}/outputs`); }
  static renameProjectOutput(agentId, projectName, outputId, title) { return this.post(`${this._projOutBase(agentId, projectName)}/outputs/${encodeURIComponent(outputId)}/rename`, {title}); }
  static archiveProjectOutput(agentId, projectName, outputId, archived) { return this.post(`${this._projOutBase(agentId, projectName)}/outputs/${encodeURIComponent(outputId)}/archive`, {archived}); }
  static cancelProjectOutput(agentId, projectName, outputId) { return this.post(`${this._projOutBase(agentId, projectName)}/outputs/${encodeURIComponent(outputId)}/cancel`, {}); }
  static deleteProjectOutput(agentId, projectName, outputId) { return this.del(`${this._projOutBase(agentId, projectName)}/outputs/${encodeURIComponent(outputId)}`); }
  static archiveProjectArtifact(agentId, projectName, artifactId, archived) { return this.post(`${this._projOutBase(agentId, projectName)}/artifacts/${encodeURIComponent(artifactId)}/archive`, {archived}); }
  static deleteProjectArtifact(agentId, projectName, artifactId) { return this.del(`${this._projOutBase(agentId, projectName)}/artifacts/${encodeURIComponent(artifactId)}`); }

  // Custom Studio presets ("Transformations", v9.302.0) — global, owner-gated CRUD.
  static listStudioPresets() { return this.get('/v1/studio/presets'); }
  static createStudioPreset(data) { return this.post('/v1/studio/presets', data); }
  static updateStudioPreset(id, data) { return this.put(`/v1/studio/presets/${encodeURIComponent(id)}`, data); }
  static deleteStudioPreset(id) { return this.del(`/v1/studio/presets/${encodeURIComponent(id)}`); }

  // Discover document links inside the project's configured HTML web_urls
  // (Option B — returns proposals, imports nothing).
  static discoverProjectWebLinks(agentId, projectName) { return this.post(`${this._projOutBase(agentId, projectName)}/web-urls/discover-links`, {}); }

  // Research (Fast + Deep)
  static researchBackends(agentId, projectName) { return this.get(`${this._projOutBase(agentId, projectName)}/research/backends`); }
  static researchSearch(agentId, projectName, topic) { return this.post(`${this._projOutBase(agentId, projectName)}/research/search`, {topic}); }
  static researchDeep(agentId, projectName, topic, budget) { return this.post(`${this._projOutBase(agentId, projectName)}/research/deep`, {topic, budget}); }
  static researchRuns(agentId, projectName) { return this.get(`${this._projOutBase(agentId, projectName)}/research/runs`); }
  static researchRun(agentId, projectName, runId) { return this.get(`${this._projOutBase(agentId, projectName)}/research/runs/${encodeURIComponent(runId)}`); }
  static researchCancel(agentId, projectName, runId) { return this.post(`${this._projOutBase(agentId, projectName)}/research/runs/${encodeURIComponent(runId)}/cancel`, {}); }

  // Background tasks (Hintergrundaufgaben)
  static getBackgroundTasks(sessionId) { return this.get(`/v1/background-tasks?session_id=${encodeURIComponent(sessionId)}`); }
  static cancelBackgroundTask(taskId) { return this.post('/v1/background-tasks/cancel', {task_id: taskId}); }
  // Cancel ONE in-flight tool call of a running task (task keeps going).
  static cancelBackgroundTool(taskId, toolUseId) { return this.post('/v1/background-tasks/cancel-tool', {task_id: taskId, tool_use_id: toolUseId}); }
  static deleteBackgroundTask(taskId) { return this.del(`/v1/background-tasks?task_id=${encodeURIComponent(taskId)}`); }
  // Live/replay transcript SSE. Callbacks:
  //   onRequest({title,prompt})  — leading ANFRAGE event
  //   onText(chunk)              — appended output chunks (live + stored replay)
  //   onDone(payload)            — terminal event
  //   onTool(ev)                 — live tool activity (running tasks only):
  //       {phase:'start', tool_use_id, name, args, round}
  //       {phase:'done',  tool_use_id, name, elapsed_ms, result_chars, is_error}
  // During a RUNNING task the endpoint proxies the sidecar's raw Anthropic-shape
  // frames (anthropic.content_block_delta for text, tool_dispatch_start/done for
  // tools); a FINISHED task replays the stored output as a single `text_delta`.
  // We normalise both wire shapes here so callers get one event vocabulary.
  // Returns an AbortController so the caller can stop it.
  static streamBackgroundTranscript(taskId, onText, onDone, onRequest, onTool) {
    const ctrl = new AbortController();
    (async () => {
      let resp;
      try {
        resp = await fetch(`${BASE_URL}/v1/background-tasks/${encodeURIComponent(taskId)}/transcript`, {
          headers: this._headers(), signal: ctrl.signal,
        });
      } catch (e) { if (onDone) onDone({error: String(e)}); return; }
      if (!resp.ok || !resp.body) { if (onDone) onDone({error: `HTTP ${resp.status}`}); return; }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '', ev = '';
      // The transcript endpoint mixes two SSE wire shapes:
      //  (a) FINISHED-task replay (server, encode_sse): canonical
      //      `event: <type>\ndata: <json>` — type is in the SSE event field.
      //  (b) RUNNING-task live passthrough (raw sidecar frames): bare
      //      `data: {"type":<type>,"data":<inner>}` — type is nested in JSON,
      //      no event: line. We normalise both to (type, payload) here.
      const dispatch = (type, payload) => {
        if (type === 'request') { if (onRequest) onRequest(payload); }
        else if (type === 'text_delta') { if (onText) onText(payload.text || ''); }
        else if (type === 'anthropic.content_block_delta') {
          const dd = payload.delta || {};
          if (dd.type === 'text_delta' && dd.text && onText) onText(dd.text);
        }
        else if (type === 'tool_dispatch_start') {
          if (onTool) onTool({phase: 'start', tool_use_id: payload.tool_use_id,
                              name: payload.name, args: payload.args || {}, round: payload.round});
        }
        else if (type === 'tool_dispatch_done') {
          if (onTool) onTool({phase: 'done', tool_use_id: payload.tool_use_id, name: payload.name,
                              elapsed_ms: payload.elapsed_ms, result_chars: payload.result_chars,
                              is_error: payload.is_error});
        }
        else if (type === 'done') { if (onDone) onDone(payload); }
      };
      try {
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          buf += dec.decode(value, {stream: true});
          let idx;
          while ((idx = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, idx); buf = buf.slice(idx + 1);
            if (line.startsWith('event:')) ev = line.slice(6).trim();
            else if (line.startsWith('data:')) {
              let d = {}; try { d = JSON.parse(line.slice(5).trim()); } catch (_) {}
              if (d && typeof d.type === 'string' && 'data' in d) {
                // Shape (b): raw sidecar frame — type + nested data in the JSON.
                dispatch(d.type, d.data || {});
              } else {
                // Shape (a): canonical frame — type came from the event: line.
                dispatch(ev, d);
              }
            }
          }
        }
      } catch (_) { /* aborted or connection closed */ }
    })();
    return ctrl;
  }

  // Skills
  static getClaudeCodeSkills(agent) { return this.get(`/v1/skills/claude-code?agent=${agent}`); }
  static toggleCCSkill(agent, slug, enabled) { return this.post('/v1/skills/claude-code', {agent, slug, enabled}); }
  static browseCCPlugins(query) { return this.post('/v1/skills/claude-code/browse', {query}); }
  static installCCPlugin(plugin, marketplace) { return this.post('/v1/skills/claude-code/install', {plugin, marketplace}); }
  static removeSkill(skill, agent) { return this.post('/v1/skills/remove', {skill, agent}); }

  // Memory

  // Read one SSE chunk, but treat prolonged byte-silence as a dead stream.
  // The server emits a keepalive comment every 5s for the whole life of a
  // turn, so 45s without a single byte means the connection is half-dead
  // (tunnel drop, laptop sleep/wake, network switch): reader.read() would
  // otherwise block forever, the turn end would never render, and the
  // caller's safety net would never run — the "turn ende wird nicht erkannt,
  // erst ein reload zeigt alles erledigt" failure mode. Throws
  // Error('sse-stalled') on silence; the caller aborts + recovers.
  static async _readOrStall(reader) {
    const STALL_MS = 45000;
    let timer;
    const readP = reader.read();
    readP.catch(() => {}); // pre-arm: the later abort() rejects this read
    try {
      return await Promise.race([
        readP,
        new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('sse-stalled')), STALL_MS); }),
      ]);
    } finally { clearTimeout(timer); }
  }

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
    // Thinking level is per-chat. Always send the active chat's value (incl.
    // 'none') so the server persists it onto the session — a reload then
    // restores exactly what the user chose, including an explicit "off".
    {
      const _tl = (state.activeChat && state.activeChat.thinkingLevel) || 'none';
      body.thinking = _tl;
    }
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

    // Deep Research toggle (composer 🔬): when on for this chat, the turn runs
    // the bounded research loop instead of a normal LLM answer and saves a
    // cited HTML report as a session artifact. Read off the active chat state
    // (set by toggleDeepResearch()); the button visual mirrors the same state.
    if (state.activeChat && state.activeChat.deepResearch) {
      body.deep_research = true;
    }

    // Manual web-search: the enabled entries of the Websuche basket. The
    // server pre-fetches these URLs and injects their content, and (unless
    // the session's allow_further_web flag is on) hard-disables web_search/
    // web_fetch for the turn. Basket persists across sends (not cleared here).
    if (typeof webBasketEnabled === 'function') {
      const _web = webBasketEnabled();
      if (_web.length) {
        body.web_urls_to_fetch = _web.map(e => ({ url: e.url, title: e.title }));
      }
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
        let chunk;
        try { chunk = await API._readOrStall(reader); }
        catch (stallErr) {
          if (stallErr && stallErr.message === 'sse-stalled') {
            console.warn('[SSE] chat stream stalled >45s — aborting dead connection (safety net recovers)');
            try { this._abortController.abort(); } catch (e) {}
            break;
          }
          throw stallErr;
        }
        const {done, value} = chunk;
        if (done) break;
        buffer += decoder.decode(value, {stream: true});

        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            lastEventType = line.slice(7).trim();
          } else if (line.startsWith('data: ') && lastEventType) {
            let data = null;
            let parsed = false;
            try { data = JSON.parse(line.slice(6)); parsed = true; }
            catch(e) {
              console.error('[SSE] JSON parse failed for event:', lastEventType, 'line length:', line.length, e.message);
            }
            if (parsed && callbacks[lastEventType]) {
              // Callback exceptions (render bugs, marked.parse failures, etc.) must
              // NOT unwind the SSE reader — that would silently kill the stream
              // mid-turn and leave the UI frozen. Isolate per event.
              try { callbacks[lastEventType](data); }
              catch(cbErr) { console.error('[SSE] callback threw for event:', lastEventType, cbErr); }
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
            let data = null;
            let parsed = false;
            try { data = JSON.parse(line.slice(6)); parsed = true; } catch(e) {}
            if (parsed && callbacks[lastEventType]) {
              try { callbacks[lastEventType](data); }
              catch(cbErr) { console.error('[SSE] callback threw for event:', lastEventType, cbErr); }
            }
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
          let data = null;
          let parsed = false;
          try { data = JSON.parse(line.slice(6)); parsed = true; } catch(e) {}
          if (parsed && callbacks[lastEventType]) {
            // Isolate callback exceptions — see streamChat() for rationale.
            try { callbacks[lastEventType](data); }
            catch(cbErr) { console.error('[SSE] callback threw for event:', lastEventType, cbErr); }
          }
          lastEventType = null;
        }
      };
      while (true) {
        let chunk;
        try { chunk = await API._readOrStall(reader); }
        catch (stallErr) {
          if (stallErr && stallErr.message === 'sse-stalled') {
            // Dead connection, but the worker may still be running. Re-attach
            // on a fresh connection — the LiveStream replays every event of
            // the turn (including a done we missed), so nothing is lost. Not
            // a tight loop: another stall costs 45s before the next retry,
            // and a truly dead server fails the fetch → error path ends it.
            console.warn('[SSE] attach stream stalled >45s — re-attaching on a fresh connection');
            try { this._streamController.abort(); } catch (e) {}
            return API.attachStream(sessionId, callbacks);
          }
          throw stallErr;
        }
        const {done, value} = chunk;
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

  // Feedback (👍/👎 + optional comment on any assistant response/result)
  static submitFeedback(body) { return this.post('/v1/feedback', body); }
  static listFeedback(surface, rating) {
    const q = new URLSearchParams();
    if (surface) q.set('surface', surface);
    if (rating) q.set('rating', rating);
    const qs = q.toString();
    return this.get('/v1/feedback' + (qs ? '?' + qs : ''));
  }
  static myFeedback(surface, sessionId) {
    const q = new URLSearchParams();
    if (surface) q.set('surface', surface);
    if (sessionId) q.set('session_id', sessionId);
    const qs = q.toString();
    return this.get('/v1/feedback/mine' + (qs ? '?' + qs : ''));
  }
  static deleteFeedback(id) { return this.del(`/v1/feedback/${id}`); }
  // Threaded conversation on one feedback row (user ↔ admin, one-liners).
  static feedbackThread(id) { return this.get(`/v1/feedback/${id}/thread`); }
  static feedbackMessage(id, text) { return this.post(`/v1/feedback/${id}/message`, { text }); }
  static feedbackSeen(id) { return this.post(`/v1/feedback/${id}/seen`, {}); }
}



