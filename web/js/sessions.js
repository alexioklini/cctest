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
    // The chat view was entered (navigateTo('chat')) BEFORE the session existed,
    // so the right-panel toggle button + activity pill stayed hidden (both gate
    // on sessionId). Now that the id exists, re-evaluate their visibility — else
    // they only appear after a reload.
    if (typeof updateRightPanelButtonVisibility === 'function') updateRightPanelButtonVisibility();
    // A draft chat may already have a Websuche basket curated before the first
    // send — now that the session has an id, persist it server-side so a later
    // reload of this chat restores the same sources.
    if (Array.isArray(chat.webBasket) && chat.webBasket.length
        && typeof _saveWebBasket === 'function') {
      API.post('/v1/sessions/manage', {
        action: 'web_basket', session_id: chat.sessionId, value: chat.webBasket,
      }).catch(() => {});
    }
    if (data.max_context) chat.maxContext = data.max_context;
    // Caveman mode is NOT reused from the last chat — a fresh session always
    // starts at the default (off / per-model system default). newChat() resets
    // chat.cavemanMode to 0 and the server applies any per-model default itself.
    // BUT: a draft chat may carry a caveman level set before the first send —
    // e.g. the project-landing composer, where sendMessage snapshots the 🪨
    // toggle across newChat() and restores it onto chat.cavemanMode. The server
    // reads caveman from the SESSION row (not the request body, unlike
    // thinking), so persist a non-default carried level now that the id exists —
    // else the toggle is silently dropped for the turn. Mirrors the webBasket
    // persist above.
    // Awaited (not fire-and-forget like webBasket above): the turn worker reads
    // session.caveman_mode at build time and the send fires right after
    // ensureSession returns, so the DB write must land BEFORE the turn starts.
    if ((parseInt(chat.cavemanMode, 10) || 0) > 0) {
      try {
        await API.post('/v1/sessions/manage', {
          action: 'caveman_mode', session_id: chat.sessionId,
          mode: parseInt(chat.cavemanMode, 10) || 0,
        });
      } catch (e) { /* non-fatal: turn proceeds at the session default */ }
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
  // during the async load window. Summary/title/gdpr pref are repopulated from
  // `data` below (restored from the opened session's own state).
  chat.chatSummary = '';
  chat.chatTitle = '';
  // ALL GDPR/PII state back to defaults up front (single reset point); the
  // session's own values + decisions are reloaded from `data` / the decisions
  // endpoint further down, overwriting these fresh defaults.
  resetChatGdprState(chat);
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
    // Deep Research is an EPHEMERAL per-turn intent, not a saved session setting —
    // opening any existing chat starts with it OFF (never silently inherited).
    chat.deepResearch = false;
    chat.workflowRunId = data.workflow_run_id || '';
    const memVal = parseInt(data.save_to_memory) || 0;
    chat.saveToMemory = memVal === 1;
    chat.memoryMode = memVal === 1 ? 'on' : memVal === 2 ? 'auto' : 'off';
    // Per-session thinking level: restore this chat's stored value; an empty
    // stored value (never set on this session) falls back to the new-chat
    // default. The composer button reads chat.thinkingLevel.
    {
      const _stl = String(data.thinking_level || '').toLowerCase();
      chat.thinkingLevel = ['none', 'low', 'medium', 'high'].includes(_stl)
        ? _stl
        : state.defaultComposerModes().thinkingLevel;
      if (typeof refreshThinkingButton === 'function') refreshThinkingButton();
    }
    // Per-session research-mode override: null = use project default,
    // true = force on, false = force off. Composer button reads this.
    chat.researchModeOverride = (data.research_mode_override === null
                                  || data.research_mode_override === undefined)
      ? null
      : !!data.research_mode_override;
    // Sticky 'allow further web search/fetch' escape hatch (Websuche tab).
    chat.allowFurtherWeb = !!data.allow_further_web;
    // Sticky opt-in for the post-turn GDPR feedback modal.
    chat.gdprFeedbackAsk = !!data.gdpr_feedback_ask;
    // Per-finding PII decisions already made in this chat (9.196.0): keyed by
    // rule|value so follow-up turns skip re-asking decided values and honour
    // false-positive markings. Fire-and-forget; absence just means re-ask.
    chat._piiDecisions = {};
    if (chat.sessionId) {
      API.getPiiDecisions(chat.sessionId).then(r => {
        const out = {};
        for (const d of Object.values((r && r.decisions) || {})) {
          out[(d.rule_id || '') + '|' + (d.value || '')] = d;
        }
        chat._piiDecisions = out;
        // Decisions load ASYNC after the chat first renders. The turn-header
        // cleartext PII marks (buildGdprCleartextSpans) read _piiDecisions, so
        // the first render — which ran with an empty map — shows the accepted
        // email/phone UNMARKED. Re-render once the map arrives so the marks
        // appear on chat open / page reload (not only after a manual toggle).
        // Guard: only if this is still the active chat.
        if (state.activeChat === chat && Object.keys(out).length &&
            typeof renderMessages === 'function') {
          renderMessages();
        }
        // The composer history button (and thus the history modal) must be
        // reachable whenever the chat has prior PII decisions — even if the
        // live history scan finds nothing (e.g. values were anonymised in the
        // stored text, or the server scanner is disabled). Decisions load
        // async after openSession's initial schedulePIIBadgeUpdate(), so repaint
        // the badge once they arrive. This is the fix for "button missing after
        // reload". Guard: still the active chat.
        if (state.activeChat === chat && typeof schedulePIIBadgeUpdate === 'function') {
          schedulePIIBadgeUpdate();
        }
      }).catch(() => {});
    }
    // Per-session Websuche basket — load THIS session's own curated sources.
    // Never inherit the basket of the chat we just left.
    if (typeof webBasketLoadFromJson === 'function') webBasketLoadFromJson(data.web_basket || '');
    // Per-session message queue — load THIS session's queued messages so a
    // reload / reconnect restores what the user lined up while a turn ran.
    if (typeof ChatTurnControl !== 'undefined') ChatTurnControl.loadFromJson(chat, data.message_queue || '');
    // Sticky transparent-anonymisation preference (step 6.2). Empty string =
    // ask each time. Other allowed values map 1:1 to body.gdpr_action.
    chat.gdprActionPref = ['anonymise', 'local_model', 'continue']
      .includes(data.gdpr_action_pref) ? data.gdpr_action_pref : '';
    // Per-chat "Datenschutz-Details sichtbar" toggle. Persisted per session so
    // reopening this chat restores the GDPR mark overlays + detail block in the
    // state the user left them. Drives the global state.showGdprDetails (read
    // by the render path) for the duration this chat is active; the toggle
    // writes it back to the server. Fall back to the localStorage default for
    // chats predating the per-chat column (data.gdpr_details_visible absent).
    chat.gdprDetailsVisible = (data.gdpr_details_visible != null)
      ? !!data.gdpr_details_visible
      : state.showGdprDetails;
    state.showGdprDetails = chat.gdprDetailsVisible;
    // Repaint the composer shield-detail button to match (the button DOM is the
    // source of truth at send/render time — see composer-controls feedback).
    if (typeof refreshGdprDetailsButton === 'function') refreshGdprDetailsButton();
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
    // Pre-warm the code-mode project info (cached) so the bottom-panel terminal
    // toggle can show synchronously — and refresh it once known.
    if (state.currentProject && typeof _workdirActiveProject === 'function') {
      _workdirActiveProject().then(() => {
        try { if (typeof terminalRefreshToggle === 'function') terminalRefreshToggle(); } catch (_) {}
      });
    }

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
        // Sort key for reconstructed tool rows. The live path stamps _seq
        // (chat_send.js _nextSeq) + _ts (Date.now) on every tool_call/result;
        // both the right-panel sort (_bgSortNewestFirst, reads `_ts || 0`) and
        // the chat-view activity sort (renderTurnBody sortKey = `_seq || id`)
        // rely on them. On reload these rows are rebuilt from metadata.tools[]
        // and previously carried NEITHER — so the panel collapsed every entry
        // to ts=0 (sort became a no-op → newest-on-top order lost). We base the
        // key on the OWNING assistant row's DB id so it stays on the same
        // monotonic scale as the turn's thinking/user rows (which sortKey reads
        // via `id`): tools land just before their assistant response and after
        // the prior turn, preserving round interleave. A per-assistant 0.001
        // step keeps within-turn emit order; +0.0005 puts each result right
        // after its call. Assistant ids are monotonic across turns, so the
        // panel's cross-turn newest-first sort is correct too.
        const _toolKeyBase = (Number(msg.id) || 0) - 1;
        // ONE shared monotonic fraction for BOTH tool rows AND answer-text
        // segments, so emission order == sort order in renderTurnBody (separate
        // counters would sort all segments before all tools — the reload
        // interleave bug). Step 0.001 per item; results sit at +0.0005.
        let _frac = 0;
        const emitTools = (tools) => {
          for (const t of tools) {
            _frac += 0.001;
            const _toolFrac = _frac;
            const _seq = _toolKeyBase + _toolFrac;
            // duration_ms is the real server-measured execution time (the _ts
            // values here are synthetic sort keys, NOT wall-clock, so the
            // renderer must use duration_ms on reload — see renderToolCall).
            expanded.push({ role: 'tool_call', name: t.name, args: t.args || {}, tool_round: t.tool_round, tool_use_id: t.tool_use_id || null, duration_ms: t.duration_ms, _seq, _ts: _seq });
            if (t.result !== undefined) {
              const _rSeq = _toolKeyBase + _toolFrac + 0.0005;
              expanded.push({ role: 'tool_result', name: t.name, result: t.result, tool_round: t.tool_round, tool_use_id: t.tool_use_id || null, _seq: _rSeq, _ts: _rSeq });
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
        // Per-round answer-text segments (chronological text↔tool interleave).
        // metadata.text_rounds = [{round, text}] — when present (model produced
        // visible text across multiple rounds), each segment becomes an
        // 'assistant_segment' display row woven in BEFORE that round's tools, so
        // the turn reads text → tool → text exactly as it streamed. The FINAL
        // round's text stays the assistant message's own content (rendered last),
        // so we emit segments for every round EXCEPT the final one. Wire-safe:
        // assistant_segment rows are display-only (never sent to the model).
        const textByRound = new Map();
        const _trs = Array.isArray(meta.text_rounds) ? meta.text_rounds : [];
        let _lastSegRound = -1;
        for (const s of _trs) {
          const r = (s.round !== null && s.round !== undefined) ? s.round : -1;
          if (r > _lastSegRound) _lastSegRound = r;
        }
        for (const s of _trs) {
          const r = (s.round !== null && s.round !== undefined) ? s.round : -1;
          if (r === _lastSegRound) continue;  // final round = the assistant content itself
          if (!textByRound.has(r)) textByRound.set(r, []);
          textByRound.get(r).push(s.text || '');
        }
        const emitTextSegs = (r) => {
          const segs = textByRound.get(r);
          if (!segs) return;
          for (const txt of segs) {
            if (!(txt || '').trim()) continue;
            _frac += 0.001;  // shared counter → segment sorts before this round's tools (emitted next)
            const _seq = _toolKeyBase + _frac;
            expanded.push({ role: 'assistant_segment', content: txt, _seq, _ts: _seq });
          }
          textByRound.delete(r);
        };
        // Interleave: for each pending thinking row, emit it, then this round's
        // answer-text segment, then this round's tools.
        for (const tm of pendingThinking) {
          expanded.push(tm);
          const tr = tm.metadata?.tool_round;
          if (tr !== null && tr !== undefined) {
            emitTextSegs(tr);
            if (toolsByRound.has(tr)) {
              emitTools(toolsByRound.get(tr));
              emittedRounds.add(tr);
            }
          }
        }
        pendingThinking = [];
        // Any round with text/tools not yet emitted (rounds without a matching
        // thinking row, or legacy -1) — weave text then tools, in round order.
        const _remRounds = new Set([...textByRound.keys(), ...toolsByRound.keys()].filter(r => !emittedRounds.has(r)));
        for (const r of [..._remRounds].sort((a, b) => a - b)) {
          emitTextSegs(r);
          if (toolsByRound.has(r) && !emittedRounds.has(r)) { emitTools(toolsByRound.get(r)); emittedRounds.add(r); }
        }
        // With segments woven in above, the assistant message must show ONLY the
        // FINAL round's text — otherwise its content (the full joined reply
        // persisted for history) would duplicate the segment rows on screen.
        if (_trs.length > 1 && _lastSegRound >= 0) {
          const _finalSeg = _trs.filter(s => ((s.round ?? -1) === _lastSegRound))
            .map(s => s.text || '').join('\n\n');
          if (_finalSeg.trim()) msg.content = _finalSeg;
        }
        if (meta.thinking) msg._thinking = meta.thinking;
        if (meta.thinking_summary) msg._thinkingSummary = meta.thinking_summary;
        if (meta.cost) msg._cost = meta.cost;
        if (meta.files) msg._files = meta.files;
        if (meta.deep_research) msg._deepResearch = true;
        if (meta.model && !data.model) chat.model = meta.model;
        if (!data.total_tokens && meta.tokens) chat.totalTokens = meta.tokens;
        // Accumulate token in/out for status bar
        if (meta.tokens_in) chat._tokensIn = (chat._tokensIn || 0) + meta.tokens_in;
        if (meta.tokens_out) chat._tokensOut = (chat._tokensOut || 0) + meta.tokens_out;
        if (meta.duration > 0 && ((meta.tokens_in || 0) + (meta.tokens_out || 0)) > 0) chat._lastSpeed = Math.round(((meta.tokens_in || 0) + (meta.tokens_out || 0)) / meta.duration);
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

    // Drop any cached references for this session so the badge + pane rebuild
    // from the freshly loaded messages/metadata. The cache is otherwise only
    // invalidated at stream end, so reopening a session that was visited earlier
    // this page-life returned a stale (often live-only "searched") split and a
    // wrong References count after reload.
    if (typeof invalidateChatReferences === 'function') invalidateChatReferences(sessionId);

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

  // Load this session's background tasks (top-bar pill + panel). Starts/stops
  // its own 2s poll based on whether any task is still running.
  if (typeof loadBackgroundTasks === 'function') loadBackgroundTasks();

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

  // (History PII cache already invalidated by resetChatGdprState at the top of
  // openSession; updatePIIBadge() below repopulates it.)

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

// opts.inheritProject — when starting a fresh chat that must keep a specific
// project binding regardless of the current view (e.g. handover from a
// project-based chat), pass the source project name here. Empty string forces
// a general (projectless) chat. Undefined keeps the default view-driven rule.
function newChat(opts) {
  opts = opts || {};
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
  // The per-agent chat object is reused across conversations, so a fresh chat
  // must reset the composer back to defaults instead of inheriting the previous
  // conversation's choices. Model → the agent's standard/default model (never
  // the last picked one); caveman/memory → their defaults (never reused).
  chat.model = state.defaultModelForAgent(state.activeAgentId);
  chat.autoPicked = null;
  chat.autoReason = '';
  if (typeof updateModelSelectorDisplay === 'function') updateModelSelectorDisplay(chat.model);
  const _def = state.defaultComposerModes();
  chat.cavemanMode = _def.cavemanMode;
  chat.saveToMemory = _def.saveToMemory;
  chat.memoryMode = _def.memoryMode;
  chat.thinkingLevel = _def.thinkingLevel;
  if (typeof refreshThinkingButton === 'function') refreshThinkingButton();
  // Fresh chat → Deep Research always OFF (never inherited from the prior
  // conversation, project or projectless). Reset the per-chat flag + repaint the
  // composer toggle (the button is the source of truth at send time).
  chat.deepResearch = false;
  if (typeof refreshDeepResearchButton === 'function') refreshDeepResearchButton();
  // Fresh chat → empty Websuche basket. Prevents the previous chat's marked
  // URLs from silently coming along into the new conversation.
  chat.webBasket = [];
  if (typeof _refreshWebsuche === 'function') _refreshWebsuche();
  // Fresh chat → ALL GDPR/PII state back to defaults (consent, mapping,
  // per-finding decisions, history scans). Single reset point so nothing leaks
  // from the prior conversation and the field list can't drift.
  resetChatGdprState(chat);
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
  chat._lcmState = null;
  // (GDPR/PII history-scan fields reset above via resetChatGdprState.)
  // Project binding for the fresh chat:
  //  - opts.inheritProject given → use it verbatim (handover keeps the base
  //    chat's mode: a project name binds to that project, '' forces general).
  //  - else inside project-detail → keep currentProject (sendMessage's
  //    project branch re-sets it before this call returns).
  //  - else → general (projectless) chat.
  if (opts.inheritProject !== undefined) {
    const inherited = opts.inheritProject || '';
    state.currentProject = inherited || null;
    chat.project = inherited;
  } else if (state.currentView !== 'project-detail') {
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
  // Re-render the composer toggle buttons (memory / caveman / gdpr-pref) from
  // the freshly-reset chat state. The 'welcome' view does NOT run
  // updateChatView()/updateStatusBar() (unlike navigateTo('chat')), so without
  // this the caveman/memory/gdpr shield icons would keep showing the PREVIOUS
  // chat's state even though chat.* were just reset to defaults above.
  if (typeof updateStatusBar === 'function') updateStatusBar();
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

