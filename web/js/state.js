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
  // Set of session IDs with a live turn running (from GET /v1/sessions/active),
  // refreshed by pollActiveSessions(); drives the "läuft gerade" list pills.
  streamingSessions: new Set(),

  // Chat state
  currentProject: null,
  planModeActive: false,
  // Server-provided new-chat composer defaults (loaded on init from
  // /v1/composer/defaults). thinking_level/caveman_mode live in config.json
  // → composer_defaults; memory_mode mirrors the classifier default_mode.
  // Thinking level is now PER-CHAT (chat.thinkingLevel), restored on reload +
  // reset to this default on a new chat — no longer a single global.
  composerDefaults: { thinking_level: 'none', caveman_mode: 0, memory_mode: 0 },
  // Privacy-first default: GDPR details + inline highlights stay hidden
  // until the user explicitly opts in via the composer toggle. Only the
  // Datenschutz statistics header (count of anonymisations / de-anonymisations)
  // shows by default.
  showGdprDetails: localStorage.getItem('showGdprDetails') === 'true',
  // PII catalog (rule→category map, labels, default actions) + live policy,
  // both loaded from the server gdpr_scanner config (applyGdprConfigToScanner).
  // The browser-side detector was removed in 9.200.0 — these are reference data
  // only (Settings panel + chat-view labels), never used to detect PII.
  gdprCatalog: { ruleCategories: {}, categoryLabels: {}, defaultCategoryActions: {}, ruleLabels: {} },
  gdprPolicy: { enabled: true, categories: null, ruleOverrides: {}, emailAllowlist: [] },
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
  // True once the user has explicitly clicked a panel tab this page session.
  // While false, opening the panel auto-selects the first tab that has data.
  // Once true, reopening restores the user's last chosen tab. Reset on reload.
  userPickedTab: false,
  // Set true when the user deliberately closes the right panel. Suppresses
  // auto-open on new references/artifacts for the rest of the page session
  // (reset only on reload). User-initiated opens still work.
  userClosedRightPanel: false,
  // One-shot GDPR mode override for "redo this turn as <mode>" (chat_render.js
  // → consumed by sendMessage). Empty = normal scan/modal flow.
  _gdprActionOverride: '',
  get artifactPanelOpen() { return this.rightPanelOpen && this.rightPanelTab === 'artifacts'; },
  get referencesPanelOpen() { return this.rightPanelOpen && this.rightPanelTab === 'references'; },
  chatReferences: {},  // sessionId -> [{title, link, snippet, domain, favicon}]
  artifacts: {},           // { sessionId: [artifact objects] }
  backgroundTasks: {},     // { sessionId: [background task objects] }
  activeArtifactId: null,
  activeArtifactVersion: null,
  artifactSourceMode: false,

  get activeChat() {
    return this.activeAgentId ? this.agentChats[this.activeAgentId] : null;
  },

  // Standard/default model for an agent — the agent's configured model, else
  // the first available model. This is the model a FRESH chat must start on;
  // it never reflects the model last picked in a previous conversation.
  defaultModelForAgent(agentId) {
    const agent = this.agents.find(a => (a.id || a.name) === agentId);
    return agent?.model || (this.models.length ? this.models[0].id || this.models[0] : '');
  },

  // Default composer modes for a FRESH chat — memory, caveman, thinking.
  // Two-tier resolution (same model as the existing memory default):
  //   per-user preference (User Settings) → global default (composer_defaults)
  //   → off. NEVER the last chat's values and NEVER localStorage. Returned in
  // the per-chat shape used across the composer.
  defaultComposerModes() {
    const cd = this.composerDefaults || {};
    const prefs = (this.authUser || {}).preferences || {};

    // Memory: per-user memory_chats_default (null = inherit) → global → off.
    let memMode;
    if (prefs.memory_chats_default !== undefined && prefs.memory_chats_default !== null) {
      memMode = parseInt(prefs.memory_chats_default) || 0;
    } else if (cd.memory_mode !== undefined && cd.memory_mode !== null) {
      memMode = parseInt(cd.memory_mode) || 0;
    } else {
      memMode = parseInt((this.mempalaceClassifier || {}).default_mode) || 0;
    }

    // Thinking: per-user thinking_level_default (null = inherit) → global → none.
    let tl = (prefs.thinking_level_default != null && prefs.thinking_level_default !== '')
      ? String(prefs.thinking_level_default).toLowerCase()
      : String(cd.thinking_level || 'none').toLowerCase();
    if (!['none', 'low', 'medium', 'high'].includes(tl)) tl = 'none';

    // Caveman: per-user caveman_mode_default (null = inherit) → global → 0.
    const cav = (prefs.caveman_mode_default != null && prefs.caveman_mode_default !== '')
      ? parseInt(prefs.caveman_mode_default) || 0
      : parseInt(cd.caveman_mode) || 0;

    return {
      saveToMemory: memMode === 1,
      memoryMode: memMode === 1 ? 'on' : memMode === 2 ? 'auto' : 'off',
      cavemanMode: Math.max(0, Math.min(3, cav)),
      thinkingLevel: tl,
    };
  },

  ensureAgentChat(agentId) {
    if (!this.agentChats[agentId]) {
      const def = this.defaultComposerModes();
      this.agentChats[agentId] = {
        sessionId: null,
        agent: agentId,
        model: this.defaultModelForAgent(agentId),
        messages: [],
        streaming: false,
        totalTokens: 0,
        streamingText: '',
        thinkingText: '',
        files: [],
        _streamStartTime: null,
        _streamTimerInterval: null,
        saveToMemory: def.saveToMemory,
        memoryMode: def.memoryMode,
        cavemanMode: def.cavemanMode,
        thinkingLevel: def.thinkingLevel,
        chatTitle: '',
        chatSummary: '',
        _summaryOpen: false,
      };
    }
    return this.agentChats[agentId];
  }
};
