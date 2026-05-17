/* ═══════════════════════════════════════════════════════════
   APPLICATION STATE
   ═══════════════════════════════════════════════════════════ */
const state = {
  // Connection
  connected: false,
  serverInfo: null,

  // Navigation
  currentView: 'welcome', // welcome | chat | chats | projects | settings

  // Agents & Models
  agents: [],
  models: [],
  providers: [],
  modelsConfig: {},
  activeAgentId: null,

  // Per-agent chat state: { sessionId, agent, model, messages[], streaming, totalTokens, streamingText, thinkingText }
  agentChats: {},
  agentSessions: {},
  agentProjects: {},

  // Chat state
  currentProject: null,
  planModeActive: false,
  thinkingLevel: localStorage.getItem('thinking-level') || 'none',
  showToolCalls: localStorage.getItem('showToolCalls') !== 'false',
  _pendingImages: [],
  _pendingFiles: [],

  // Workers
  activeWorkers: {},  // worker_id -> {tool_name, state, started_at, ...}
  workerFlows: {},    // tool_call_id (or worker_id) -> {worker_id, tool_name, state, duration, flow:[...], question?}

  // Activity
  agentActivity: {},
  teamStructure: {},

  // Chats list
  chatsFilter: 'all',
  chatsSearchQuery: '',

  // Right panel (unified: attachments, references, artifacts)
  rightPanelOpen: false,
  rightPanelTab: 'attachments',
  get artifactPanelOpen() { return this.rightPanelOpen && this.rightPanelTab === 'artifacts'; },
  get referencesPanelOpen() { return this.rightPanelOpen && this.rightPanelTab === 'references'; },
  chatReferences: {},  // sessionId -> [{title, link, snippet, domain, favicon}]
  artifacts: {},           // { sessionId: [artifact objects] }
  activeArtifactId: null,
  activeArtifactVersion: null,
  artifactSourceMode: false,

  get activeChat() {
    return this.activeAgentId ? this.agentChats[this.activeAgentId] : null;
  },

  ensureAgentChat(agentId) {
    if (!this.agentChats[agentId]) {
      const agent = this.agents.find(a => (a.id || a.name) === agentId);
      const defMode = parseInt((this.mempalaceClassifier || {}).default_mode) || 0;
      this.agentChats[agentId] = {
        sessionId: null,
        agent: agentId,
        model: agent?.model || (this.models.length ? this.models[0].id || this.models[0] : ''),
        messages: [],
        streaming: false,
        totalTokens: 0,
        streamingText: '',
        thinkingText: '',
        files: [],
        _streamStartTime: null,
        _streamTimerInterval: null,
        saveToMemory: defMode === 1,
        memoryMode: defMode === 1 ? 'on' : defMode === 2 ? 'auto' : 'off',
        cavemanMode: 0,
        chatTitle: '',
        chatSummary: '',
        _summaryOpen: false,
      };
    }
    return this.agentChats[agentId];
  }
};
