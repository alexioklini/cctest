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
    showToast('Sitzung konnte nicht erstellt werden: ' + e.message, true);
    throw e;
  }
}

async function openSession(sessionId, agentId) {
  NextPrompt.clear();
  // Detach from any in-progress turn we were watching (does NOT cancel the
  // server-side worker — it keeps running and we can re-attach later).
  API.abortStreamAttach();
  // Leaving a chat that's mid-stream: stop watching its live UI. If this tab
  // was the one that *sent* the turn, the POST stream is aborted here too —
  // but the worker keeps running server-side, so reopening this session will
  // re-attach via GET /v1/chat/stream and resume rendering from the buffer.
  const prevChat = state.activeChat;
  if (prevChat?.streaming) {
    if (API._abortController) API._abortController.abort();
    prevChat.streaming = false;
    clearInterval(prevChat._streamTimerInterval);
    prevChat.streamingText = '';
    prevChat.thinkingText = '';
    prevChat.files = [];
    updateStreamingUI(false);
  }

  selectAgent(agentId);
  const chat = state.ensureAgentChat(agentId);
  chat.sessionId = sessionId;
  chat.messages = [];
  // Clear per-session fields up front so the prior chat's values can't render
  // during the async load window. All four are repopulated from `data` below
  // (summary/title/gdpr pref restored from the opened session's own state).
  chat.chatSummary = '';
  chat.chatTitle = '';
  chat.gdprActionPref = '';
  chat.hasGdprMapping = false;
  chat.streamingText = '';
  chat.thinkingText = '';
  chat.files = [];
  chat._tokensIn = 0;
  chat._tokensOut = 0;
  chat._lastSpeed = null;
  chat._lastApiIn = 0;
  chat._activityStates = new Map();
  chat._collapsedTurns = new Set();
  chat._expandedHints = new Set();
  // Real chat open — drop any sticky scheduled-run selection.
  state.activeScheduledRunId = null;

  let resumeStreaming = false;
  let resumeStreamingText = '';

  try {
    const data = await API.getSessionMessages(sessionId);
    const rawMessages = data.messages || [];
    if (data.model) {
      chat.model = data.model;
      // Reflect the restored model in the composer — a session that ran on
      // Auto persists model="auto", so this keeps the composer on "✨ Auto"
      // instead of the last concrete model that answered.
      if (typeof updateModelSelectorDisplay === 'function') updateModelSelectorDisplay(chat.model);
    }
    if (data.max_context) chat.maxContext = data.max_context;
    // Title is the primary label (user-typed or auto-derived from first
    // message). Summary is the LLM-generated synopsis, shown as a hover
    // tooltip on the header and as a collapsible block below the first
    // turn — never overlays the title.
    chat.chatTitle = data.title || '';
    chat.chatSummary = data.summary || '';
    chat.cavemanMode = parseInt(data.caveman_mode) || 0;
    chat.workflowRunId = data.workflow_run_id || '';
    const memVal = parseInt(data.save_to_memory) || 0;
    chat.saveToMemory = memVal === 1;
    chat.memoryMode = memVal === 1 ? 'on' : memVal === 2 ? 'auto' : 'off';
    // Per-session research-mode override: null = use project default,
    // true = force on, false = force off. Composer button reads this.
    chat.researchModeOverride = (data.research_mode_override === null
                                  || data.research_mode_override === undefined)
      ? null
      : !!data.research_mode_override;
    // Sticky 'allow further web search/fetch' escape hatch (Websuche tab).
    chat.allowFurtherWeb = !!data.allow_further_web;
    // Sticky transparent-anonymisation preference (step 6.2). Empty string =
    // ask each time. Other allowed values map 1:1 to body.gdpr_action.
    chat.gdprActionPref = ['anonymise', 'local_model', 'continue']
      .includes(data.gdpr_action_pref) ? data.gdpr_action_pref : '';
    // True when this session has any persisted pseudonym map. Used by
    // sendMessage to skip the modal on follow-up turns of an
    // already-anonymising session — once consent was given, every send
    // continues anonymising until the user clears the pref.
    chat.hasGdprMapping = !!data.has_gdpr_mapping;
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
      // Transparent-anonymisation synthetic rows: server persists them as
      // tool_use / tool_result with metadata.synthetic=true. Map to the
      // client's tool_call / tool_result shape so the renderer's shield-icon
      // branch picks them up identically on reload + live.
      const m = msg.metadata;
      if (m && m.synthetic) {
        let parsed = null;
        try {
          parsed = typeof msg.content === 'string' ? JSON.parse(msg.content) : msg.content;
        } catch (e) {
          parsed = null;
        }
        if (msg.role === 'tool_use') {
          expanded.push({
            role: 'tool_call',
            synthetic: true,
            kind: m.kind || (parsed && parsed.name) || '',
            name: m.kind || (parsed && parsed.name) || '',
            args: (parsed && parsed.args) || {},
            tool_use_id: m.tool_use_id || (parsed && parsed.tool_use_id) || null,
          });
          continue;
        }
        if (msg.role === 'tool_result') {
          expanded.push({
            role: 'tool_result',
            synthetic: true,
            kind: m.kind || (parsed && parsed.name) || '',
            name: m.kind || (parsed && parsed.name) || '',
            result: (parsed && parsed.result) || {},
            status: m.status || (parsed && parsed.status) || 'ok',
            duration_ms: m.duration_ms || (parsed && parsed.duration_ms) || 0,
            tool_use_id: m.tool_use_id || (parsed && parsed.tool_use_id) || null,
          });
          continue;
        }
      }
      expanded.push(msg);
    }
    // Trailing thinking rows (no assistant after them). If a turn is in
    // progress, these belong to the in-flight turn and the live-stream replay
    // will re-emit them via thinking_done — so DROP them here to avoid
    // duplicates. Otherwise flush them verbatim (rare: session ended mid-think).
    if (!data.streaming) {
      for (const tm of pendingThinking) expanded.push(tm);
    }
    // Server-side live estimate takes priority over stale per-message metadata
    if (data.total_tokens) chat.totalTokens = data.total_tokens;
    chat.messages = expanded;

    // A turn is in progress for this session — flag it so we re-attach to the
    // live stream after navigating in. The stream replays every event from the
    // start of the turn, so it rebuilds streamingText (and the in-flight
    // thinking/tool rows) fully — don't pre-seed from data.streaming_text (that
    // would double the text). We keep the persisted partial only as a fallback
    // shown if the attach can't connect.
    if (data.streaming) {
      resumeStreaming = true;
      resumeStreamingText = data.streaming_text || '';
      chat.streamingText = '';
    }

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
    showToast('Sitzung konnte nicht geladen werden', true);
  }

  // Close artifact and references panels when switching sessions
  closeRightPanel();

  // Reset the composer so the previous session's draft / attachments don't
  // leak into the newly opened one. Without this the PII badge can stay lit
  // from a draft typed for chat A while chat B is open. Drafts are not
  // persisted per-session today; if/when they are, restore here from
  // `chat._draft` after the reset instead of always clearing.
  try {
    const _input = _composerInputEl();
    if (_input) {
      _input.value = '';
      try { autoGrow(_input); } catch (e) {}
    }
    state._pendingFiles = [];
    state._pendingImages = [];
    if (typeof renderFilePreviews === 'function') renderFilePreviews();
    if (typeof updateSendButton === 'function') updateSendButton();
  } catch (e) {}

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

  // Re-attach to an in-progress turn: replay everything emitted so far, then
  // follow live events until done. The server-side worker is untouched by us
  // attaching/detaching; multiple tabs may attach to the same turn.
  if (resumeStreaming) {
    const isActive = () => state.activeChat === chat;
    chat.streaming = true;
    chat.streamingText = '';
    chat.thinkingText = '';
    chat.thinkingSummary = null;
    chat.queueStatus = null;
    chat.files = [];
    chat._streamGen = (chat._streamGen || 0) + 1;
    chat._streamStartTime = Date.now();
    clearInterval(chat._streamTimerInterval);
    chat._streamTimerInterval = setInterval(() => updateStreamTimer(chat), 100);
    updateStreamingUI(true, chat);
    if (resumeStreamingText && isActive()) {
      // Show the persisted partial immediately; replay overwrites it in a beat.
      chat.streamingText = resumeStreamingText;
      renderStreamingMessage(chat);
    }
    const cbs = buildStreamCallbacks(chat, isActive);
    // On the first replayed text_delta, clear the placeholder so the replayed
    // stream (which carries the full text) doesn't append onto it.
    let _replayStarted = false;
    const origTextDelta = cbs.text_delta;
    cbs.text_delta = (d) => {
      if (!_replayStarted) { _replayStarted = true; chat.streamingText = ''; }
      origTextDelta(d);
    };
    cbs.idle = () => {
      // The turn finished between GET /messages and the attach — reload so the
      // now-persisted assistant message renders, and clear the streaming UI.
      chat.streaming = false;
      chat.streamingText = '';
      chat.thinkingText = '';
      clearInterval(chat._streamTimerInterval);
      if (isActive()) {
        updateStreamingUI(false);
        openSession(sessionId, agentId);
      }
    };
    API.attachStream(sessionId, cbs);
  }
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
  chat.chatSummary = '';
  chat.workflowRunId = '';
  // Sticky PII consent ("auto-continue past warnings") is per-session — a
  // fresh chat must re-prompt, never inherit the prior chat's consent.
  chat.gdprActionPref = '';
  chat.hasGdprMapping = false;
  chat.messages = [];
  chat.totalTokens = 0;
  chat.maxContext = 0;
  chat.streamingText = '';
  chat.thinkingText = '';
  chat.files = [];
  chat._collapsedTurns = new Set();
  chat._expandedHints = new Set();
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
  // Clear composer state — mirrors openSession() so opening a fresh chat
  // doesn't carry over a half-typed message or attached files from whatever
  // the user was looking at before.
  try {
    const _input = _composerInputEl();
    if (_input) {
      _input.value = '';
      try { autoGrow(_input); } catch (e) {}
    }
    state._pendingFiles = [];
    state._pendingImages = [];
    if (typeof renderFilePreviews === 'function') renderFilePreviews();
    if (typeof updateSendButton === 'function') updateSendButton();
  } catch (e) {}
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
    showToast('Chat archiviert');
    // Refresh the appropriate list
    const agentId = state._projectDetailAgent;
    const projectName = state._projectDetailName;
    if (state.currentView === 'project-detail' && agentId && projectName) {
      loadProjectChats(agentId, projectName);
    } else {
      loadAgentSessions(state.activeAgentId);
    }
  } catch(e) { showToast('Archivieren fehlgeschlagen', true); }
}

async function deleteSession(sessionId) {
  if (!await showConfirmDanger('Diesen Chat löschen?', 'Chat löschen', 'Löschen')) return;
  try {
    await API.deleteSession(sessionId);
    if (state.activeChat?.sessionId === sessionId) {
      newChat();
    }
    showToast('Sitzung gelöscht');
    loadAgentSessions(state.activeAgentId);
    // If we're inside a project, refresh that project's list too.
    const agentId = state._projectDetailAgent;
    const projectName = state._projectDetailName;
    if (state.currentView === 'project-detail' && agentId && projectName) {
      loadProjectChats(agentId, projectName);
    }
  } catch(e) { showToast('Löschen fehlgeschlagen', true); }
}

async function archiveAllChats() {
  const scope = state.chatsFilter === 'archived' ? 'archivierten' : 'aktiven';
  if (!await showConfirm(`Alle ${scope} Chats archivieren?`, 'Chats archivieren')) return;
  try {
    await API.manageSession({action:'archive_all'});
    showToast('Alle Chats archiviert');
    loadChatsList();
    loadAgentSessions(state.activeAgentId);
  } catch(e) { showToast('Alle archivieren fehlgeschlagen', true); }
}

async function deleteAllChats() {
  const archived = state.chatsFilter === 'archived';
  const label = archived ? 'archivierten' : 'ALLE';
  if (!await showConfirmDanger(`${label} Chats dauerhaft löschen? Dies kann nicht rückgängig gemacht werden.`, 'Chats löschen', 'Alle löschen')) return;
  try {
    const r = await API.manageSession({action:'delete_all', archived_only: archived});
    showToast(`${r.count || 'Alle'} Chats gelöscht`);
    newChat();
    loadChatsList();
    loadAgentSessions(state.activeAgentId);
  } catch(e) { showToast('Alle löschen fehlgeschlagen', true); }
}

