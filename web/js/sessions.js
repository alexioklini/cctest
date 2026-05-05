/* ═══════════════════════════════════════════════════════════
   SESSION MANAGEMENT
   ═══════════════════════════════════════════════════════════ */

/** Stop any active warmup poll on a chat and reset warmup state. */
function stopWarmupPoll(chat) {
  if (chat._warmupPoll) {
    clearInterval(chat._warmupPoll);
    chat._warmupPoll = null;
  }
  chat._warmingUp = false;
}

/** Start polling warmup status for a chat's session. Chat is captured by value. */
function startWarmupPoll(chat) {
  stopWarmupPoll(chat);
  const sid = chat.sessionId;
  if (!sid) return;
  chat._warmingUp = true;
  updateStatusBar();
  chat._warmupPoll = setInterval(async () => {
    try {
      const w = await API.get(`/v1/sessions/${sid}/warmup`);
      if (!w.warming_up) {
        stopWarmupPoll(chat);
        updateStatusBar();
      }
    } catch(e) {
      stopWarmupPoll(chat);
      updateStatusBar();
    }
  }, 500);
}

async function ensureSession(chat) {
  if (chat.sessionId) return chat.sessionId;
  stopWarmupPoll(chat);
  // Generation counter to handle race conditions on model switch
  const gen = (chat._sessionGen = (chat._sessionGen || 0) + 1);
  try {
    const data = await API.createSession(chat.agent, chat.model, state.currentProject);
    // Discard if a newer ensureSession was called (model switched)
    if (chat._sessionGen !== gen) return null;
    chat.sessionId = data.session_id;
    if (data.max_context) chat.maxContext = data.max_context;
    // Restore user's last caveman chat mode for new sessions
    const savedCaveman = parseInt(localStorage.getItem('caveman-chat-mode')) || 0;
    if (savedCaveman > 0) {
      chat.cavemanMode = savedCaveman;
      API.post('/v1/sessions/manage', {
        action: 'caveman_mode', session_id: chat.sessionId, mode: savedCaveman,
      }).catch(() => {});
      updateStatusBar();
    }
    // Show warmup indicator if provider supports it
    if (data.warmup) {
      startWarmupPoll(chat);
    }
    return chat.sessionId;
  } catch(e) {
    showToast('Failed to create session: ' + e.message, true);
    throw e;
  }
}

async function openSession(sessionId, agentId) {
  NextPrompt.clear();
  // Abort any running stream before switching
  const prevChat = state.activeChat;
  if (prevChat?.streaming) {
    if (API._abortController) API._abortController.abort();
    prevChat.streaming = false;
    clearInterval(prevChat._streamTimerInterval);
    if (prevChat.streamingText) {
      prevChat.messages.push({role:'assistant', content: prevChat.streamingText, _cancelled: true});
    }
    prevChat.streamingText = '';
    prevChat.thinkingText = '';
    prevChat.files = [];
    updateStreamingUI(false);
  }

  selectAgent(agentId);
  const chat = state.ensureAgentChat(agentId);
  chat.sessionId = sessionId;
  chat.messages = [];
  chat.streamingText = '';
  chat.thinkingText = '';
  chat.files = [];
  chat._tokensIn = 0;
  chat._tokensOut = 0;
  chat._lastSpeed = null;
  chat._lastApiIn = 0;
  chat._activityStates = new Map();
  // Real chat open — drop any sticky scheduled-run selection.
  state.activeScheduledRunId = null;

  try {
    const data = await API.getSessionMessages(sessionId);
    const rawMessages = data.messages || [];
    if (data.model) chat.model = data.model;
    if (data.max_context) chat.maxContext = data.max_context;
    chat.chatTitle = data.summary || data.title || '';
    chat.cavemanMode = parseInt(data.caveman_mode) || 0;
    chat.workflowRunId = data.workflow_run_id || '';
    const memVal = parseInt(data.save_to_memory) || 0;
    chat.saveToMemory = memVal === 1;
    chat.memoryMode = memVal === 1 ? 'on' : memVal === 2 ? 'auto' : 'off';
    // Sync state.currentProject to the loaded chat's project. Without this,
    // sticky values from a previous project-detail visit leak into chats
    // opened from the chats list (and vice versa) — every outgoing turn
    // would carry a wrong body.project.
    chat.project = data.project || '';
    state.currentProject = chat.project || null;

    // Reconstruct tool blocks from metadata and extract per-message fields.
    // Order requirement: thinking(round N) → tools_of_round(N) → thinking(N+1) → ... → assistant.
    // Tool rows aren't persisted as their own DB rows — they come from the assistant's
    // metadata.tools. Each tool carries tool_round so we can interleave them with thinking.
    const expanded = [];
    let pendingThinking = [];  // thinking rows accumulated since the last assistant flush
    for (const msg of rawMessages) {
      const meta = msg.metadata;
      if (msg.role === 'thinking') {
        // Hold thinking rows until we see the assistant so we can interleave by round.
        pendingThinking.push(msg);
        continue;
      }
      if (meta && msg.role === 'assistant') {
        // Bucket tools by round. Tools without tool_round (legacy, or unrecoverable)
        // bucket under -1 and render after any interleaved section.
        const toolsByRound = new Map();
        for (const t of (meta.tools || [])) {
          const r = (t.tool_round !== null && t.tool_round !== undefined) ? t.tool_round : -1;
          if (!toolsByRound.has(r)) toolsByRound.set(r, []);
          toolsByRound.get(r).push(t);
        }
        const emittedRounds = new Set();
        const emitTools = (tools) => {
          for (const t of tools) {
            expanded.push({ role: 'tool_call', name: t.name, args: t.args || {}, tool_round: t.tool_round });
            if (t.result !== undefined) {
              expanded.push({ role: 'tool_result', name: t.name, result: t.result, tool_round: t.tool_round });
              try {
                const rj = JSON.parse(typeof t.result === 'string' ? t.result : JSON.stringify(t.result));
                if (rj && rj.worker && rj.worker_id) {
                  state.workerFlows[rj.worker_id] = {
                    worker_id: rj.worker_id,
                    tool_name: t.name,
                    state: rj.state || 'COMPLETED',
                    duration: rj.duration_seconds || null,
                    flow: Array.isArray(rj.flow) ? rj.flow : [],
                    artifacts: rj.artifacts || [],
                    question: null,
                    summariser_usage: rj.summariser_usage || null,
                  };
                }
              } catch (e) {}
            }
          }
        };
        // Interleave: for each pending thinking row, emit it, then emit any tools
        // whose tool_round matches (or is <= the thinking row's round — catches
        // legacy data where tools had no round number stamped yet).
        for (const tm of pendingThinking) {
          expanded.push(tm);
          const tr = tm.metadata?.tool_round;
          if (tr !== null && tr !== undefined && toolsByRound.has(tr)) {
            emitTools(toolsByRound.get(tr));
            emittedRounds.add(tr);
          }
        }
        pendingThinking = [];
        // Any tool bucket not yet emitted (rounds without a matching thinking row,
        // or tools tagged with unknown/legacy tool_round = -1) — append after.
        for (const [r, tools] of toolsByRound) {
          if (!emittedRounds.has(r)) emitTools(tools);
        }
        if (meta.thinking) msg._thinking = meta.thinking;
        if (meta.thinking_summary) msg._thinkingSummary = meta.thinking_summary;
        if (meta.cost) msg._cost = meta.cost;
        if (meta.files) msg._files = meta.files;
        if (meta.model && !data.model) chat.model = meta.model;
        if (!data.total_tokens && meta.tokens) chat.totalTokens = meta.tokens;
        // Accumulate token in/out for status bar
        if (meta.tokens_in) chat._tokensIn = (chat._tokensIn || 0) + meta.tokens_in;
        if (meta.tokens_out) chat._tokensOut = (chat._tokensOut || 0) + meta.tokens_out;
        if (meta.duration > 0 && meta.tokens_out > 0) chat._lastSpeed = Math.round(meta.tokens_out / meta.duration);
      } else {
        // Any non-thinking, non-assistant row (user, system, etc.) — flush any
        // pending thinking rows verbatim first, then the row itself. Normally
        // there are no pending thinking rows here because the model always
        // follows thinking with an assistant turn, but be defensive.
        for (const tm of pendingThinking) expanded.push(tm);
        pendingThinking = [];
      }
      expanded.push(msg);
    }
    // Flush any trailing pending thinking (session ended before final assistant — rare).
    for (const tm of pendingThinking) expanded.push(tm);
    // Server-side live estimate takes priority over stale per-message metadata
    if (data.total_tokens) chat.totalTokens = data.total_tokens;
    chat.messages = expanded;

    // Load memorized turn_ids (for the memory menu) in background
    refreshMemorizedTurns();

    // Load artifacts for this session
    try {
      const artResp = await API.getArtifacts(sessionId);
      state.artifacts[sessionId] = artResp.artifacts || [];
    } catch(e) {
      state.artifacts[sessionId] = [];
    }
  } catch(e) {
    showToast('Failed to load session', true);
  }

  // Close artifact and references panels when switching sessions
  closeRightPanel();

  // Invalidate the history PII cache — new session = new content to scan.
  // updatePIIBadge() below will populate it and show the banner if needed.
  chat._piiHistoryScanLen = -1;
  chat._piiHistoryHas = false;
  chat._piiHistoryCounts = {};

  // Trigger warmup for the restored session's model if provider supports it
  stopWarmupPoll(chat);
  if (chat.sessionId) {
    API.post(`/v1/sessions/${chat.sessionId}/warmup`, {}).then(data => {
      if (data.warmup) {
        startWarmupPoll(chat);
      }
    }).catch(() => {});
  }

  navigateTo('chat');
  // Force a scan of loaded history so the badge appears immediately if the
  // chat already contains PII, and auto-swap to a local model under block.
  schedulePIIBadgeUpdate();
  // Update the workflow-run banner (visible only when chat.workflowRunId).
  if (typeof maybeUpdateWorkflowBanner === 'function') maybeUpdateWorkflowBanner();
}

function newChat() {
  NextPrompt.clear();
  if (!state.activeAgentId) {
    if (state.agents.length) selectAgent(state.agents[0].name);
    else return;
  }
  const chat = state.ensureAgentChat(state.activeAgentId);
  // If a stream is active, stop it cleanly before resetting
  if (chat.streaming) {
    if (API._abortController) API._abortController.abort();
    chat.streaming = false;
    clearInterval(chat._streamTimerInterval);
    updateStreamingUI(false);
  }
  chat.sessionId = null;
  chat.chatTitle = '';
  chat.workflowRunId = '';
  chat.messages = [];
  chat.totalTokens = 0;
  chat.maxContext = 0;
  chat.streamingText = '';
  chat.thinkingText = '';
  chat.files = [];
  chat._tokensIn = 0;
  chat._tokensOut = 0;
  chat._lastSpeed = null;
  chat._lastApiIn = 0;
  chat._piiHistoryScanLen = -1;
  chat._piiHistoryHas = false;
  chat._piiHistoryCounts = {};
  // Drop project binding when starting a fresh chat outside the project-detail
  // flow. The project-detail composer's sendMessage branch sets currentProject
  // again before this call returns, so its turns still get tagged.
  if (state.currentView !== 'project-detail') {
    state.currentProject = null;
    chat.project = '';
  }
  stopWarmupPoll(chat);
  closeRightPanel();
  navigateTo('welcome');
  schedulePIIBadgeUpdate();
  if (typeof maybeUpdateWorkflowBanner === 'function') maybeUpdateWorkflowBanner();
  // Session is created lazily on first send — pre-creating here would
  // leave an orphan 0-message row in the DB whenever the user opens a
  // fresh chat and walks away. The warm-pool keeper (for main agent) and
  // the per-model warmup keeper handle first-token latency without
  // needing a session.
}

async function archiveSession(sessionId) {
  try {
    await API.manageSession({action:'archive', session_id: sessionId});
    showToast('Chat archived');
    // Refresh the appropriate list
    const agentId = state._projectDetailAgent;
    const projectName = state._projectDetailName;
    if (state.currentView === 'project-detail' && agentId && projectName) {
      loadProjectChats(agentId, projectName);
    } else {
      loadAgentSessions(state.activeAgentId);
    }
  } catch(e) { showToast('Archive failed', true); }
}

async function deleteSession(sessionId) {
  if (!confirm('Delete this chat?')) return;
  try {
    await API.deleteSession(sessionId);
    if (state.activeChat?.sessionId === sessionId) {
      newChat();
    }
    showToast('Session deleted');
    loadAgentSessions(state.activeAgentId);
    // If we're inside a project, refresh that project's list too.
    const agentId = state._projectDetailAgent;
    const projectName = state._projectDetailName;
    if (state.currentView === 'project-detail' && agentId && projectName) {
      loadProjectChats(agentId, projectName);
    }
  } catch(e) { showToast('Delete failed', true); }
}

async function archiveAllChats() {
  const scope = state.chatsFilter === 'archived' ? 'archived' : 'active';
  if (!confirm(`Archive all ${scope} chats?`)) return;
  try {
    await API.manageSession({action:'archive_all'});
    showToast('All chats archived');
    loadChatsList();
    loadAgentSessions(state.activeAgentId);
  } catch(e) { showToast('Archive all failed', true); }
}

async function deleteAllChats() {
  const archived = state.chatsFilter === 'archived';
  const label = archived ? 'archived' : 'ALL';
  if (!confirm(`Permanently delete ${label} chats? This cannot be undone.`)) return;
  try {
    const r = await API.manageSession({action:'delete_all', archived_only: archived});
    showToast(`Deleted ${r.count || 'all'} chats`);
    newChat();
    loadChatsList();
    loadAgentSessions(state.activeAgentId);
  } catch(e) { showToast('Delete all failed', true); }
}

