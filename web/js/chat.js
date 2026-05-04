/* ═══════════════════════════════════════════════════════════
   MESSAGE SENDING & STREAMING
   ═══════════════════════════════════════════════════════════ */
async function sendMessage() {
  const input = _composerInputEl();

  // If composer is empty but a ghost-text suggestion is active, accept it and use that
  if (NextPrompt.active() && !(input?.value?.trim())) {
    NextPrompt.accept({ submit: false });
  }

  let text = input?.value?.trim();
  if (!text && !state._pendingImages.length && !state._pendingFiles.length) return;

  // Workflow-run binding: if this chat is bound to a still-running workflow
  // execution, refuse the send. Asking follow-ups about a run that's still
  // mutating its trace mid-conversation produces confusing context for the
  // model. Composer re-enables automatically once the banner sees the run
  // hit a terminal status (poller updates wfBanner.data).
  const _wfChat = state.activeChat;
  if (_wfChat && _wfChat.workflowRunId && wfBanner && wfBanner.data) {
    const _ws = wfBanner.data.status || '';
    if (WF_LIVE_STATUSES.has(_ws)) {
      showToast(`Run is ${_ws} — wait for it to finish.`, true);
      return;
    }
  }

  // Clear any stale suggestion — user is sending something new
  NextPrompt.clear();

  // Ensure agent selected
  if (!state.activeAgentId) {
    if (state.agents.length) selectAgent(state.agents[0].name);
    else { showToast('No agents available', true); return; }
  }

  // From project-detail: bind to the project's agent, start a fresh chat for
  // this turn (so the project context applies), then drop into the chat view.
  // state.currentProject is already set by openProjectDetail.
  if (state.currentView === 'project-detail') {
    const projAgent = state._projectDetailAgent;
    if (projAgent && projAgent !== state.activeAgentId) {
      selectAgent(projAgent);
    }
    newChat();
  }

  const chat = state.ensureAgentChat(state.activeAgentId);

  // If on welcome or project-detail screen, switch to chat view
  if (state.currentView === 'welcome' || state.currentView === 'project-detail') {
    navigateTo('chat');
  }

  // Slash command expansion
  if (text && text.startsWith('/')) {
    const cmdMatch = text.match(/^\/(\S+)/);
    if (cmdMatch) {
      const cmd = cmdMatch[1].toLowerCase();
      // Handle built-in commands
      if (cmd === 'new') { newChat(); return; }
      if (cmd === 'clear') { chat.messages = []; renderMessages(); return; }
      if (cmd === 'model') { /* TODO: model switch */ return; }

      // Try expanding custom command
      try {
        const expanded = await API.expandCommand(state.activeAgentId, text);
        if (expanded.expanded) text = expanded.expanded;
      } catch(e) {}
    }
  }

  // GDPR/PII pre-submit check. Each finding carries its own action (per the
  // category policy): 'ignore' never reaches us (filtered during scan), 'warn'
  // triggers the confirmation modal, 'block' refuses unless a local model is
  // active. Skipped entirely when the feature is disabled or suppressed.
  if (state.piiScannerEnabled !== false && !sessionStorage.getItem('pii-suppress:' + (chat.sessionId || '_new'))) {
    const scan = PIIScanner.scanPayload(text, state._pendingFiles);
    if (scan.findings.length) {
      if (scan.worstAction === 'block') {
        piiEnsureLocalModel();
        const curModel = chat.model || '';
        if (!curModel || !isModelLocal(curModel)) {
          showToast('GDPR block: high-severity personal data detected. Select a local model before sending.', true);
          return;
        }
        // Local model is selected — data stays on-prem, no confirm modal.
      } else if (scan.worstAction === 'warn') {
        const go = await confirmPIIBeforeSend(scan, chat);
        if (!go) return;
      }
    }
  }

  // Capture pending files before clearing (needed for streamChat)
  const filesToSend = state._pendingFiles.length ? [...state._pendingFiles] : null;

  // Add user message
  const userMsg = { role: 'human', content: text || '[File]' };
  if (filesToSend) {
    userMsg.files = filesToSend;
  }
  chat.messages.push(userMsg);
  // The just-pushed user message may contain PII that the history cache
  // should see on the *next* badge tick even if the stream never completes.
  chat._piiHistoryScanLen = -1;
  state._pendingFiles = [];
  renderFilePreviews();

  // Clear input
  input.value = '';
  autoResizeInput(input);
  updateSendButton();

  // Render user message immediately
  renderMessages();
  scrollToBottom();

  // Start streaming (generation counter prevents stale safety-net from killing newer streams)
  chat.streaming = true;
  chat.streamingText = '';
  chat.thinkingText = '';
  chat.thinkingSummary = null;
  chat.queueStatus = null;
  chat.files = [];
  const streamGen = (chat._streamGen = (chat._streamGen || 0) + 1);
  updateStreamingUI(true, chat);

  // Timer (per-chat, not global)
  chat._streamStartTime = Date.now();
  chat._streamTimerInterval = setInterval(() => updateStreamTimer(chat), 100);

  // Guard: only update DOM when this chat is still the active one
  const isActive = () => state.activeChat === chat;

  try {
    const sid = await ensureSession(chat);
    if (!sid) {
      // Race condition: model switched during session creation — abort cleanly
      chat.streaming = false;
      updateStreamingUI(false);
      clearInterval(chat._streamTimerInterval);
      return;
    }

    await API.streamChat(chat.sessionId, text, {
      thinking_start: () => {
        // Each round's thinking becomes its own message row at thinking_done,
        // so the live buffer is per-round. Fresh start on every thinking_start.
        // renderMessages() first so any tool_calls from the completed prior round
        // render into the turn-body before the new thinking bubble is appended.
        chat.thinkingText = '';
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
      },
      thinking_delta: (d) => {
        chat.thinkingText += d.text || '';
        if (isActive()) renderStreamingMessage(chat);
      },
      thinking_done: (d) => {
        // Persist this round's thinking as its own message entry so it appears
        // inline in the transcript (before the tool calls that come next). Server
        // mirrors this into the DB; on reload the thinking row is restored in place.
        const roundText = (d && d.text) || chat.thinkingText || '';
        const trimmed = roundText.trim();
        if (trimmed) {
          chat.messages.push({
            role: 'thinking',
            content: trimmed,
            tool_round: d?.tool_round ?? null,
          });
          _activityAutoUpdate(chat, currentTurnNum(chat), 'add');
          if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
        }
        // Reset the streaming buffer so the next round's thinking starts fresh
        // (avoids concatenating multi-round thinking into a single live block).
        chat.thinkingText = '';
      },
      thinking_summary: (d) => {
        chat.thinkingSummary = { format: d.format, reasoning_tokens: d.reasoning_tokens || 0 };
      },
      queue_wait: (d) => {
        // Provider queue serialised this turn — show "waiting in line" hint.
        chat.queueStatus = {
          state: 'waiting',
          provider: d.provider || '',
          position: d.position || 0,
          waiting: d.waiting || 0,
          active: d.active || 0,
          max_concurrent: d.max_concurrent || 0,
          label: d.label || '',
          wait_ms: d.wait_ms || 0,
        };
        if (isActive()) renderStreamingMessage(chat);
        // Nudge the monitor into fast mode; its own timer will re-tick.
        QueueMonitor._mode = 'fast';
      },
      queue_acquired: (d) => {
        chat.queueStatus = null;
        if (isActive()) renderStreamingMessage(chat);
      },
      queue_released: (d) => {
        chat.queueStatus = null;
      },
      text_delta: (d) => {
        chat.streamingText += d.text || '';
        if (isActive()) { renderStreamingMessage(chat); scrollToBottom(); }
      },
      tool_call: (d) => {
        console.log('[SSE] tool_call:', d.name, 'showToolCalls:', state.showToolCalls);
        const last = chat.messages[chat.messages.length - 1];
        const isNewToolCall = !(last && last.role === 'tool_call' && last.name === d.name && d.args && Object.keys(d.args).length);
        if (isNewToolCall) {
          chat.messages.push({ role: 'tool_call', name: d.name, args: d.args || {}, _ts: Date.now() });
          _activityAutoUpdate(chat, currentTurnNum(chat), 'add');
        } else {
          last.args = d.args;
        }
        if (!state.showToolCalls) return;
        // renderMessages() wipes the container including the in-flight .msg-streaming div,
        // so re-render the streaming bubble right after. Without this, any partial assistant
        // text/thinking captured so far vanishes until the next text_delta arrives.
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); scrollToBottom(); }
      },
      references: (d) => {
        // Server-pushed normalized refs for the just-completed tool call.
        // Mirrors the tool_result path below but fires independently so
        // refs arrive even if tool_result is suppressed by showToolCalls=false.
        const refs = d.references || [];
        if (!refs.length || !chat.sessionId) return;
        if (!state.chatReferences[chat.sessionId]) state.chatReferences[chat.sessionId] = { cited: [], searched: [] };
        const cache = state.chatReferences[chat.sessionId];
        const allLinks = new Set([...cache.cited.map(r => r.link), ...cache.searched.map(r => r.link)]);
        let added = false;
        for (const ref of refs) {
          if (!allLinks.has(ref.link)) { cache.searched.push(ref); allLinks.add(ref.link); added = true; }
        }
        if (added && isActive()) { openRightPanel('references'); updateRightPanelBadges(); }
      },
      tool_result: (d) => {
        console.log('[SSE] tool_result:', d.name, 'hasResult:', !!d.result);
        // d.references is pre-extracted server-side; attach to the message so
        // extractReferencesFromToolResult reads from it directly (no re-parsing).
        const toolMsg = { role: 'tool_result', name: d.name, result: d.result,
                          references: d.references || undefined, _ts: Date.now() };
        chat.messages.push(toolMsg);
        // Live refs via the separate `references` event above — this path is
        // kept only for the legacy extractReferencesFromToolResult fallback on
        // old messages that predate server-side extraction.
        const refs = extractReferencesFromToolResult(toolMsg);
        if (refs.length && chat.sessionId) {
          if (!state.chatReferences[chat.sessionId]) state.chatReferences[chat.sessionId] = { cited: [], searched: [] };
          const cache = state.chatReferences[chat.sessionId];
          const allLinks = new Set([...cache.cited.map(r => r.link), ...cache.searched.map(r => r.link)]);
          let newRefAdded = false;
          for (const ref of refs) {
            if (!allLinks.has(ref.link)) { cache.searched.push(ref); allLinks.add(ref.link); newRefAdded = true; }
          }
          if (newRefAdded && isActive()) {
            openRightPanel('references');
            updateRightPanelBadges();
          }
        }
        if (!state.showToolCalls || !isActive()) return;
        renderMessages();
        renderStreamingMessage(chat);
        scrollToBottom();
      },
      tool_output: (d) => {
        // Live tool output streaming
      },
      file_created: (d) => {
        chat.files.push(d);
      },
      artifact_updated: (d) => {
        chat.files.push(d);
        updateArtifactRegistry(chat.sessionId, d);
        if (!isActive()) return;
        if (state.activeArtifactId === d.artifact_id) {
          const sel = document.getElementById('artifact-version-select');
          if (sel) {
            const opt = document.createElement('option');
            opt.value = d.artifact_version;
            opt.textContent = `v${d.artifact_version}`;
            sel.appendChild(opt);
            sel.value = d.artifact_version;
          }
          loadArtifactVersion(d.artifact_version);
        } else {
          openArtifactPanel(d.artifact_id, d.artifact_version);
        }
        updateRightPanelBadges();
      },
      'worker.started': (d) => {
        console.log('[SSE] worker.started:', d.tool_name, d.worker_id);
        if (!d.worker_id) return;
        state.activeWorkers[d.worker_id] = { tool_name: d.tool_name, state: 'RUNNING', started_at: Date.now() };
        state.workerFlows[d.worker_id] = {
          worker_id: d.worker_id,
          tool_call_id: d.tool_call_id || '',
          tool_name: d.tool_name,
          state: 'RUNNING',
          started_at: Date.now() / 1000,
          duration: null,
          flow: [],
          question: null,
        };
        // renderMessages() wipes the .msg-streaming div. Re-append it so the thinking
        // panel and partial text stay visible during the worker's lifetime.
        if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.progress': (d) => {
        if (!d.worker_id) return;
        const wf = state.workerFlows[d.worker_id] || (state.workerFlows[d.worker_id] = {
          worker_id: d.worker_id, tool_name: d.tool_name, state: 'RUNNING',
          started_at: Date.now() / 1000, flow: [], question: null,
        });
        if (d.entry) {
          wf.flow.push(d.entry);
          if (d.entry.kind === 'state' && d.entry.state) wf.state = d.entry.state;
          if (d.entry.kind === 'question') wf.question = { question: d.entry.question, options: d.entry.options };
          if (d.entry.kind === 'answer') wf.question = null;
        }
        if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.finished': (d) => {
        console.log('[SSE] worker.finished:', d.tool_name, d.duration_seconds + 's');
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = d.state || 'COMPLETED';
        const wf = state.workerFlows[d.worker_id];
        if (wf) { wf.state = d.state || 'COMPLETED'; wf.duration = d.duration_seconds; }
        if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.paused': (d) => {
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = 'PAUSED';
        const wf = state.workerFlows[d.worker_id]; if (wf) wf.state = 'PAUSED';
        if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.resumed': (d) => {
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = 'RUNNING';
        const wf = state.workerFlows[d.worker_id]; if (wf) wf.state = 'RUNNING';
        if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.aborted': (d) => {
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = 'ABORTED';
        const wf = state.workerFlows[d.worker_id]; if (wf) wf.state = 'ABORTED';
        if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
      },
      worker_usage: (d) => {
        if (!d.worker_id) return;
        const wf = state.workerFlows[d.worker_id];
        if (wf) {
          wf.summariser_usage = {
            tokens_in: d.tokens_in || 0,
            tokens_out: d.tokens_out || 0,
            model: d.model || '',
          };
        }
        if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.question': (d) => {
        console.log('[SSE] worker.question:', d.worker_id, d.question);
        if (d.worker_id) {
          const wf = state.workerFlows[d.worker_id] || (state.workerFlows[d.worker_id] = {
            worker_id: d.worker_id, state: 'WAITING_FOR_USER',
            started_at: Date.now() / 1000, flow: [], question: null,
          });
          wf.state = 'WAITING_FOR_USER';
          wf.question = { question: d.question, options: d.options };
          if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
        }
        if (!isActive()) return;
        const container = document.getElementById('messages-container');
        if (!container) return;
        const card = document.createElement('div');
        card.className = 'worker-question-card';
        card.id = `wq-${d.worker_id}`;
        const optionsHtml = (d.options || []).map((o, i) =>
          `<label class="wq-option" onclick="this.parentElement.querySelectorAll('.wq-option').forEach(e=>e.classList.remove('selected'));this.classList.add('selected')">
            <input type="radio" name="wq-opt-${d.worker_id}" value="${esc(o)}">${esc(o)}
          </label>`
        ).join('');
        card.innerHTML = `
          <div class="wq-header">
            <span class="wq-badge">WORKER</span>
            <span>Worker <code>${esc(d.worker_id)}</code> needs your input</span>
          </div>
          <div class="wq-body">
            ${d.context_summary ? `<div class="wq-context">${esc(d.context_summary)}</div>` : ''}
            <div class="wq-question">${esc(d.question)}</div>
            ${optionsHtml ? `<div class="wq-options">${optionsHtml}</div>` : ''}
            <textarea class="wq-freeform" placeholder="Type your answer..." style="width:100%;min-height:40px;margin-bottom:8px;border:1px solid var(--border-100);border-radius:6px;padding:6px 8px;font-size:13px;background:var(--bg-000);color:var(--text-200);resize:vertical;display:${d.options ? 'none' : 'block'}"></textarea>
            <div class="wq-actions">
              <button class="wq-btn-answer" onclick="answerWorkerQuestion('${esc(d.worker_id)}')">Answer</button>
              <button onclick="answerWorkerQuestion('${esc(d.worker_id)}', '__delegate__')">Let agent decide</button>
              <button class="wq-btn-abort" onclick="abortWorkerFromQuestion('${esc(d.worker_id)}')">Abort worker</button>
            </div>
          </div>
        `;
        container.appendChild(card);
        scrollToBottom();
      },
      'worker.answered': (d) => {
        if (d.worker_id) {
          const wf = state.workerFlows[d.worker_id];
          if (wf) { wf.question = null; wf.state = 'RUNNING'; }
          if (isActive() && state.showToolCalls) { renderMessages(); renderStreamingMessage(chat); }
        }
        const card = document.getElementById(`wq-${d.worker_id}`);
        if (card) {
          card.classList.add('wq-answered');
          const body = card.querySelector('.wq-body');
          if (body) body.innerHTML += `<div style="margin-top:8px;font-size:12px;color:var(--text-400)">Answered: ${esc(d.answer)}</div>`;
        }
      },
      user_input_needed: (d) => {
        console.log('[SSE] user_input_needed:', d.session_id, d.questions || d.question);
        if (!isActive()) return;
        const container = document.getElementById('messages-container');
        if (!container) return;
        const existing = document.getElementById(`aq-${d.session_id}`);
        if (existing) existing.remove();

        // Normalize: always work with a `questions` array.
        let questions = Array.isArray(d.questions) && d.questions.length
          ? d.questions
          : (d.question ? [{question: d.question, options: d.options}] : []);
        if (!questions.length) return;
        const isBatch = questions.length > 1;

        const card = document.createElement('div');
        card.className = 'worker-question-card';
        card.id = `aq-${d.session_id}`;

        const questionsHtml = questions.map((q, idx) => {
          const qid = `${d.session_id}-${idx}`;
          const opts = Array.isArray(q.options) ? q.options : null;
          const optionsHtml = (opts || []).map((o) =>
            `<label class="wq-option" onclick="this.parentElement.querySelectorAll('.wq-option').forEach(e=>e.classList.remove('selected'));this.classList.add('selected')">
              <input type="radio" name="aq-opt-${esc(qid)}" value="${esc(o)}">${esc(o)}
            </label>`
          ).join('');
          const header = isBatch
            ? `<div class="wq-question" style="display:flex;gap:8px;align-items:baseline"><span style="color:var(--text-400);font-weight:500;min-width:24px">${idx + 1}.</span><span>${esc(q.question)}</span></div>`
            : `<div class="wq-question">${esc(q.question)}</div>`;
          return `
            <div class="wq-item" data-qid="${esc(qid)}" data-question="${esc(q.question)}" style="${idx > 0 ? 'margin-top:14px;padding-top:14px;border-top:1px solid var(--border-100)' : ''}">
              ${header}
              ${optionsHtml ? `<div class="wq-options">${optionsHtml}</div>` : ''}
              <textarea class="wq-freeform" placeholder="Type your answer..." style="width:100%;min-height:40px;margin-top:6px;margin-bottom:0;border:1px solid var(--border-100);border-radius:6px;padding:6px 8px;font-size:13px;background:var(--bg-000);color:var(--text-200);resize:vertical;display:${opts ? 'none' : 'block'}"></textarea>
            </div>
          `;
        }).join('');

        const headerLabel = isBatch ? `${questions.length} questions` : 'The agent needs your input';
        card.innerHTML = `
          <div class="wq-header">
            <span class="wq-badge">QUESTION${isBatch ? 'S' : ''}</span>
            <span>${esc(headerLabel)}</span>
          </div>
          <div class="wq-body">
            ${d.context_summary ? `<div class="wq-context">${esc(d.context_summary)}</div>` : ''}
            ${questionsHtml}
            <div class="wq-actions" style="margin-top:12px">
              <button class="wq-btn-answer" onclick="answerChatQuestion('${esc(d.session_id)}')">${isBatch ? 'Submit answers' : 'Answer'}</button>
            </div>
          </div>
        `;
        container.appendChild(card);
        scrollToBottom();
      },
      user_input_received: (d) => {
        const card = document.getElementById(`aq-${d.session_id}`);
        if (!card) return;
        card.classList.add('wq-answered');
        const body = card.querySelector('.wq-body');
        if (!body) return;
        if (d.answers && typeof d.answers === 'object') {
          const lines = Object.entries(d.answers).map(([q, a]) =>
            `<div>· ${esc(q)} → ${esc(a)}</div>`
          ).join('');
          body.innerHTML += `<div style="margin-top:8px;font-size:12px;color:var(--text-400)">Answers:${lines}</div>`;
        } else if (d.answer != null) {
          body.innerHTML += `<div style="margin-top:8px;font-size:12px;color:var(--text-400)">Answered: ${esc(d.answer)}</div>`;
        }
      },
      fallback: (d) => {
        if (d.to) {
          chat.model = d.to;
          if (isActive()) {
            document.getElementById('spinner-model').textContent = modelShortName(d.to);
            updateModelSelectorDisplay(d.to);
          }
        }
      },
      warmup: (d) => {
        if (d.status === 'waiting') {
          if (isActive()) document.getElementById('spinner-label').textContent = 'Waiting for warmup...';
        } else if (d.status === 'ready') {
          stopWarmupPoll(chat);
          updateStatusBar();
        }
      },
      max_tokens_exhausted: (d) => {
        if (isActive() && d.message) {
          const el = document.getElementById('spinner-label');
          if (el) el.textContent = 'Token limit reached';
          showToast(d.message, true);
        }
      },
      proxy_request: (d) => {
        if (ClientProxy.enabled) {
          ClientProxy.handleProxyRequest(chat.sessionId, d)
            .catch(e => showToast('Proxy error: ' + e.message, true));
        }
      },
      local_inference_request: (d) => {
        // Server has decided this request should run on the client's own
        // local model (capability handshake matched a family). Route via
        // LocalInference — distinct from ClientProxy which goes to a cloud
        // endpoint with server-supplied creds.
        if (window.LocalInference && LocalInference.enabled) {
          LocalInference.handleRequest(chat.sessionId, d)
            .catch(e => showToast('Local inference error: ' + e.message, true));
        } else {
          showToast('Server routed request to client but local inference is off', true);
        }
      },
      proxy_tool: (d) => {
        if (ClientProxy.enabled) ClientProxy.handleProxyTool(chat.sessionId, d).catch(e => console.error('[ClientProxy] proxy_tool handler error:', e));
      },
      done: (d) => {
        console.log('[SSE] done event received', {textLen: (d.text||'').length, tokens: d.tokens, model: d.model, msgCount: chat.messages.length});
        // Finalize assistant message (always update data)
        const assistantMsg = {
          role: 'assistant',
          content: d.text || chat.streamingText,
        };
        if (d.tokens) chat.totalTokens = d.tokens;
        if (d.max_context) chat.maxContext = d.max_context;
        if (d.model) chat.model = d.model;
        const tokIn = d.tokens_in || 0;
        const lastTokIn = d.last_tokens_in || tokIn;
        const tokOut = d.tokens_out || 0;
        const dur = d.duration || 0;
        const estOut = tokOut || Math.ceil((d.text || chat.streamingText || '').length / 4);
        chat._tokensIn = (chat._tokensIn || 0) + tokIn;
        chat._tokensOut = (chat._tokensOut || 0) + (tokOut || estOut);
        assistantMsg.metadata = {
          ...(assistantMsg.metadata || {}),
          tokens_in: tokIn,
          last_tokens_in: lastTokIn,
          tokens_out: tokOut || estOut,
          duration: dur,
        };
        if (dur > 0 && estOut > 0) {
          chat._lastSpeed = Math.round(estOut / dur);
        }
        if (lastTokIn > 0) chat._lastApiIn = lastTokIn;
        if (d.cost !== undefined) { assistantMsg._cost = d.cost || 0; chat._sessionCost = d.cost || 0; }
        if (d.files?.length) assistantMsg._files = d.files;
        else if (chat.files.length) assistantMsg._files = chat.files;
        if (chat.thinkingText) assistantMsg._thinking = chat.thinkingText;
        if (chat.thinkingSummary) assistantMsg._thinkingSummary = chat.thinkingSummary;

        // Auto-close activity summary now that the response is finalised
        _activityAutoUpdate(chat, currentTurnNum(chat), 'response');

        chat.messages.push(assistantMsg);
        chat.streaming = false;
        chat.streamingText = '';
        chat.thinkingText = '';
        chat.thinkingSummary = null;
        chat.files = [];
        clearInterval(chat._streamTimerInterval);

        // New turn landed — invalidate the history PII cache so the badge /
        // model-dropdown filter picks up any PII the assistant may have
        // surfaced or the user just sent.
        chat._piiHistoryScanLen = -1;

        // Refs cache was seeded as searched-only during streaming; the
        // assistant text now exists, so re-split into cited/searched on
        // next read.
        invalidateChatReferences(chat.sessionId);

        // Only update DOM if this chat is still visible
        if (isActive()) {
          if (d.model) updateModelSelectorDisplay(d.model);
          renderMessages();
          scrollToBottom();
          updateStreamingUI(false);
          updateStatusBar();
          schedulePIIBadgeUpdate();
        }

        // Desktop notification when window not focused
        if (!document.hasFocus() && window.electronAPI?.showNotification) {
          const preview = (d.text || chat.streamingText || '').slice(0, 120).replace(/\n/g, ' ');
          window.electronAPI.showNotification({ title: `${chat.agent || 'Brain Agent'} responded`, body: preview || 'Response complete' });
        }

        // Reload sessions for sidebar
        loadAgentSessions(chat.agent);
        // Refresh quota pill so usage updates without waiting for the 30s tick
        if (typeof QuotaMonitor !== 'undefined') QuotaMonitor.refresh();

        // Fetch a "next prompt" suggestion for the composer ghost text (best-effort)
        if (isActive()) {
          NextPrompt.fetchFor(chat.sessionId);
        }
      },
      error: (d) => {
        chat.streaming = false;
        chat.streamingText = '';
        chat.thinkingText = '';
        chat.files = [];
        clearInterval(chat._streamTimerInterval);
        if (isActive()) {
          updateStreamingUI(false);
          const msg = d.message || 'Unknown error';
          if (!/Load failed|Failed to fetch|NetworkError|AbortError|network/i.test(msg)) {
            showToast('Error: ' + msg, true);
          }
        }
      },
    }, chat.model, filesToSend);

    // Safety net: if stream ended without a 'done' event
    // Guard with streamGen to prevent a stale safety-net (resumed via microtask)
    // from killing a newer stream's spinner
    if (chat.streaming && chat._streamGen === streamGen) {
      console.warn('[SSE] safety net triggered — done event was lost');
      const text = chat.streamingText || '';
      if (text) {
        chat.messages.push({ role: 'assistant', content: text });
      }
      chat.streaming = false;
      chat.streamingText = '';
      chat.thinkingText = '';
      chat.files = [];
      clearInterval(chat._streamTimerInterval);
      if (isActive()) {
        renderMessages();
        scrollToBottom();
        updateStreamingUI(false);
        updateStatusBar();
      }
      loadAgentSessions(chat.agent);
    }
  } catch(e) {
    if (chat._streamGen !== streamGen) return; // stale — newer stream took over
    chat.streaming = false;
    chat.streamingText = '';
    chat.thinkingText = '';
    chat.files = [];
    clearInterval(chat._streamTimerInterval);
    if (isActive()) {
      updateStreamingUI(false);
      // Suppress transient network errors (browser tab backgrounded, connection hiccup)
      const msg = e.message || '';
      if (!/Load failed|Failed to fetch|NetworkError|AbortError|network/i.test(msg)) {
        showToast('Send failed: ' + msg, true);
      }
    }
  }
}

async function openInspectModal() {
  const sessionId = state.activeChat?.sessionId;
  if (!sessionId) { showToast('No active session', true); return; }
  // For scheduled-run chats the session id has the shape `sched-<run_id>` —
  // there's a much richer per-run modal already (timeline + tool spans +
  // artifacts + result text) so route there instead of the generic per-turn
  // inspector. Keeps one modal as the single source of truth for "what
  // happened on this run" whether the user enters from history table or
  // from the read-only chat view's status-bar inspector button.
  const m = /^sched-(\d+)$/.exec(sessionId);
  if (m && typeof _schedViewRunDetail === 'function') {
    _schedViewRunDetail(parseInt(m[1], 10));
    return;
  }

  // Create modal overlay
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="modal-content wide" style="max-height:90vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;padding:20px 24px 0;gap:12px">
      <h2 style="margin:0;font-size:18px;font-weight:600;color:var(--text-000)">Session Inspector</h2>
      <span style="font-family:var(--font-mono);font-size:12px;color:var(--text-400)">${esc(sessionId)}</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()" style="margin-left:auto">&times;</button>
    </div>
    <div id="inspect-body" style="flex:1;overflow-y:auto;padding:16px 24px 24px">
      <div style="color:var(--text-400);padding:24px;text-align:center">Loading...</div>
    </div>
  </div>`;
  document.body.appendChild(overlay);

  try {
    const data = await API.inspectSession(sessionId);
    const body = document.getElementById('inspect-body');
    let html = '';

    // --- Summary bar ---
    const t = data.totals || {};
    const sessionExecMode = (data.interactions || []).find(ix => ix.assistant?.execution_mode)?.assistant?.execution_mode || 'server';
    html += `<div style="display:grid;grid-template-columns:repeat(${sessionExecMode === 'client' ? 6 : 5},1fr);gap:12px;margin-bottom:20px">
      ${sessionExecMode === 'client' ? `<div style="background:#7c3aed22;border:1px solid #7c3aed44;border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Execution</div>
        <div style="font-size:16px;font-weight:600;color:#7c3aed;margin-top:4px">CLIENT</div>
      </div>` : ''}
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Turns</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${t.turns || 0}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens In</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${(t.tokens_in||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens Out</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${(t.tokens_out||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Duration</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${t.duration ? t.duration.toFixed(1) + 's' : '-'}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Cost</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">$${(t.cost||0).toFixed(4)}</div>
      </div>
    </div>`;

    // --- Extract system prompt & tools from first payload (constant per session) ---
    const firstPayload = (data.interactions || []).find(ix => ix.assistant?.request_payloads?.length)?.assistant?.request_payloads?.[0];
    const spContent = firstPayload?.system_prompt || data.system_prompt?.content || '';
    const spTokens = firstPayload?.system_tokens || data.system_prompt?.tokens_est || 0;
    const toolNames = firstPayload?.tool_names || [];
    const toolsCount = firstPayload?.tools_count || 0;
    const toolsTokens = firstPayload?.tools_tokens || 0;

    // --- System Prompt (once) ---
    html += `<details style="margin-bottom:8px;border:1px solid var(--border-100);border-radius:10px;overflow:hidden">
      <summary style="padding:12px 16px;cursor:pointer;background:var(--bg-100);font-weight:500;color:var(--text-000);display:flex;align-items:center;gap:8px">
        <span style="color:#8b5cf6">System Prompt</span>
        <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-400);margin-left:auto">~${spTokens.toLocaleString()} tokens</span>
      </summary>
      <pre style="margin:0;padding:16px;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;max-height:400px;overflow-y:auto;background:var(--bg-200);color:var(--text-200);font-family:var(--font-mono)">${esc(spContent || '(not available)')}</pre>
    </details>`;


    // --- Tool Definitions (once) ---
    if (toolsCount > 0) {
      html += `<details style="margin-bottom:16px;border:1px solid var(--border-100);border-radius:10px;overflow:hidden">
        <summary style="padding:12px 16px;cursor:pointer;background:var(--bg-100);font-weight:500;color:var(--text-000);display:flex;align-items:center;gap:8px">
          <span style="color:var(--accent-brand)">Tool Definitions</span>
          <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-400);margin-left:auto">${toolsCount} tools &middot; ~${toolsTokens.toLocaleString()} tokens</span>
        </summary>
        <div style="padding:12px 16px;display:flex;flex-wrap:wrap;gap:4px">
          ${toolNames.map(n => `<span style="font-size:11px;font-family:var(--font-mono);background:var(--bg-200);color:var(--text-300);padding:2px 8px;border-radius:4px">${esc(n)}</span>`).join('')}
        </div>
      </details>`;
    }

    // --- Interactions ---
    html += `<div style="font-weight:600;font-size:14px;color:var(--text-000);margin-bottom:12px">Interactions</div>`;

    for (const ix of (data.interactions || [])) {
      const a = ix.assistant || {};

      // Speed calculation
      const speed = a.duration > 0 && a.tokens_out > 0 ? Math.round(a.tokens_out / a.duration) : null;

      html += `<div style="border:1px solid var(--border-100);border-radius:10px;margin-bottom:12px;overflow:hidden">`;

      // Turn header
      const execMode = a.execution_mode || 'server';
      const execBadge = execMode === 'client'
        ? '<span style="font-size:10px;background:#7c3aed;color:#fff;padding:1px 6px;border-radius:4px;margin-left:4px" title="LLM call executed in browser (client proxy mode)">CLIENT</span>'
        : '';
      // Per-turn state badges: thinking level + caveman modes
      const tLvl = a.thinking_level || (a.thinking ? 'on' : '');
      const thinkingBadge = (tLvl && tLvl !== 'none')
        ? `<span style="font-size:10px;background:#ede9fe;color:#8b5cf6;padding:1px 6px;border-radius:4px" title="Thinking level used for this turn">thinking: ${esc(tLvl)}</span>`
        : '';
      const cavChat = parseInt(a.caveman_chat) || 0;
      const cavSys = parseInt(a.caveman_system) || 0;
      const cavName = n => ({1: 'lite', 2: 'full', 3: 'ultra'})[n] || '';
      const cavParts = [];
      if (cavSys) cavParts.push(`sys ${cavName(cavSys)}`);
      if (cavChat) cavParts.push(`chat ${cavName(cavChat)}`);
      const cavBadge = cavParts.length
        ? `<span style="font-size:10px;background:#fef3c7;color:#b45309;padding:1px 6px;border-radius:4px" title="Caveman compression level applied for this turn (system prompt / chat response)">caveman: ${cavParts.join(' / ')}</span>`
        : '';
      html += `<div style="display:flex;align-items:center;gap:8px;padding:10px 16px;background:var(--bg-100);border-bottom:1px solid var(--border-100);flex-wrap:wrap">
        <span style="font-weight:600;color:var(--text-000)">Turn ${ix.turn}</span>${execBadge}
        ${thinkingBadge}
        ${cavBadge}
        <span style="margin-left:auto;font-family:var(--font-mono);font-size:11px;color:var(--text-400)">
          ${a.model ? esc(a.model) : ''}
          ${a.duration ? ' &middot; ' + a.duration.toFixed(1) + 's' : ''}
          ${speed ? ' &middot; ' + speed + ' tok/s' : ''}
          ${a.cost ? ' &middot; $' + a.cost.toFixed(4) : ''}
        </span>
      </div>`;

      // --- API Request Breakdown ---
      const payloads = (a.request_payloads || []);
      const MONO11 = 'font-family:var(--font-mono);font-size:11px;color:var(--text-400)';
      const BADGE = (label, bg, fg) => `<span style="font-size:10px;background:${bg};color:${fg};padding:1px 6px;border-radius:4px">${label}</span>`;
      const PRE = 'margin:0;padding:10px 16px;font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-word;max-height:250px;overflow-y:auto;background:var(--bg-000);color:var(--text-200);font-family:var(--font-mono)';

      if (payloads.length) {
        for (let pi = 0; pi < payloads.length; pi++) {
          const p = payloads[pi];
          const prev = pi > 0 ? payloads[pi - 1] : null;
          const round = p.tool_round || 0;
          const _hasApi = typeof p.tokens_in === 'number' && p.tokens_in > 0;
          const _hasOut = typeof p.tokens_out === 'number' && p.tokens_out > 0;
          const _tokLabel = _hasApi
            ? `${p.tokens_in.toLocaleString()} in${_hasOut ? ' / ' + p.tokens_out.toLocaleString() + ' out' : ''} tok (API)`
            : `~${(p.total_payload_tokens||0).toLocaleString()} tok est`;
          const histLen = (p.history || []).length;
          const prevHistLen = prev ? (prev.history || []).length : 0;
          const histDelta = histLen - prevHistLen;
          const deltaBadge = prev && histDelta > 0
            ? `<span style="${MONO11};background:var(--bg-200);padding:1px 6px;border-radius:4px">+${histDelta} msg${histDelta>1?'s':''}</span>`
            : '';
          html += `<details style="border-bottom:1px solid var(--border-050)"${pi === 0 ? ' open' : ''}>
            <summary style="padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <span style="color:var(--accent-brand);font-weight:500;font-size:13px">API Request${payloads.length > 1 ? ' (Round ' + round + ')' : ''}</span>
              <span style="${MONO11}">${_tokLabel}</span>
              ${deltaBadge}
              ${histLen ? `<span style="${MONO11}">&middot; history ${histLen}</span>` : ''}
            </summary>
            <div style="padding:8px 16px 12px">
              <!-- Token breakdown bar -->
              <div style="display:flex;gap:0;height:6px;margin-bottom:8px;border-radius:3px;overflow:hidden">
                <div style="flex:${p.system_tokens||1};background:#8b5cf6" title="System"></div>
                <div style="flex:${p.tools_tokens||1};background:var(--accent-brand)" title="Tools"></div>
                <div style="flex:${p.history_tokens||1};background:var(--text-400)" title="History"></div>
                <div style="flex:${p.user_tokens||1};background:var(--success)" title="User"></div>
              </div>
              <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:11px;margin-bottom:8px">
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:#8b5cf6"></span>System ~${(p.system_tokens||0).toLocaleString()}</span>
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--accent-brand)"></span>Tools (${p.tools_count||0}) ~${(p.tools_tokens||0).toLocaleString()}</span>
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--text-400)"></span>History (${histLen} msgs) ~${(p.history_tokens||0).toLocaleString()}</span>
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--success)"></span>User ~${(p.user_tokens||0).toLocaleString()}</span>
              </div>

              <!-- History (auto-open when it's what differs from the previous round) -->
              ${histLen ? `<details style="margin-bottom:4px"${histDelta > 0 ? ' open' : ''}>
                <summary style="cursor:pointer;${MONO11}">History (${histLen} messages)${histDelta > 0 ? ` &middot; ${histDelta} new this round` : ''}</summary>
                <div style="max-height:250px;overflow-y:auto;border:1px solid var(--border-050);border-radius:6px;margin-top:4px">
                  ${(p.history||[]).map((h, hi) => {
                    const isNew = pi > 0 && hi >= prevHistLen;
                    const bg = isNew ? 'background:var(--bg-100);' : '';
                    return `<div style="padding:4px 12px;border-bottom:1px solid var(--border-050);${bg}">
                      <span style="font-size:10px;font-weight:600;color:${h.role==='user'?'var(--accent-brand)':h.role==='tool'?'#0891b2':'var(--success)'};text-transform:uppercase">${esc(h.role)}${isNew?' · NEW':''}</span>
                      <pre style="margin:2px 0 0;font-size:11px;white-space:pre-wrap;word-break:break-word;color:var(--text-300);font-family:var(--font-mono)">${esc(String(h.content||'').substring(0, 2000))}${String(h.content||'').length > 2000 ? '\\n... (truncated)' : ''}</pre>
                    </div>`;
                  }).join('')}
                </div>
              </details>` : ''}

              <!-- User Message (only when present — absent on continuation rounds) -->
              ${p.user_message ? `<details open>
                <summary style="cursor:pointer;${MONO11}">User Message (~${(p.user_tokens||0).toLocaleString()} tok)</summary>
                <pre style="${PRE};margin-top:4px;border-radius:6px">${esc(p.user_message)}</pre>
              </details>` : ''}
            </div>
          </details>`;
        }
      } else {
        // No payload data — fallback to simple view
        html += `<details style="border-bottom:1px solid var(--border-050)">
          <summary style="padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:8px">
            <span style="color:var(--accent-brand);font-weight:500;font-size:13px">Request</span>
            <span style="${MONO11}">~${(ix.user.tokens_est||0).toLocaleString()} tok est</span>
            ${a.tokens_in ? `<span style="${MONO11}">&middot; ${a.tokens_in.toLocaleString()} tok in (API)</span>` : ''}
          </summary>
          <pre style="${PRE}">${esc(ix.user.content || '')}</pre>
        </details>`;
      }

      // --- Response ---
      if (ix.assistant) {
        const tools = a.tools || [];
        const toolBadges = tools.length ? tools.map(t => BADGE(esc(t.name), 'var(--bg-300)', 'var(--text-300)')).join(' ') : '';
        const hasWorkerTool = tools.some(t => { const rs = typeof t.result === 'string' ? t.result : JSON.stringify(t.result || ''); return rs.includes('"worker": true') || rs.includes('"worker":true'); });

        html += `<details>
          <summary style="padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span style="color:var(--success);font-weight:500;font-size:13px">Response</span>
            <span style="${MONO11}">~${(a.tokens_est||0).toLocaleString()} tok est</span>
            ${a.tokens_out ? `<span style="${MONO11}">&middot; ${a.tokens_out.toLocaleString()} tok out (API)</span>` : ''}
            ${a.thinking ? BADGE('thinking', '#ede9fe', '#8b5cf6') : ''}
            ${a.sdk ? BADGE('SDK', 'var(--bg-300)', 'var(--text-400)') : ''}
            ${hasWorkerTool ? BADGE('WORKER', '#0891b2', '#fff') : ''}
            ${toolBadges}
          </summary>
          <div style="max-height:400px;overflow-y:auto">`;

        if (tools.length) {
          html += `<div style="padding:8px 16px;background:var(--bg-100);border-bottom:1px solid var(--border-050)">`;
          for (const t of tools) {
            let tIsWorker = false;
            const rs = typeof t.result === 'string' ? t.result : JSON.stringify(t.result || '');
            if (rs.includes('"worker": true') || rs.includes('"worker":true')) tIsWorker = true;
            const wBadge = tIsWorker ? ' <span style="font-size:9px;font-weight:600;background:#0891b2;color:#fff;padding:1px 5px;border-radius:3px;letter-spacing:0.5px" title="Executed via worker subagent">WORKER</span>' : '';
            const wf = tIsWorker ? findWorkerFlow(t.name, rs) : null;
            const flowHtml = wf ? renderWorkerFlow(wf) : '';
            html += `<details style="margin-bottom:4px">
              <summary style="cursor:pointer;font-size:12px;font-family:var(--font-mono);color:var(--text-300)">${esc(t.name)}${wBadge}</summary>
              <div style="padding:4px 12px;font-size:11px;font-family:var(--font-mono);color:var(--text-400)">
                ${t.args ? '<div style="margin-bottom:2px"><strong>args:</strong> ' + esc(JSON.stringify(t.args).substring(0, 500)) + '</div>' : ''}
                ${t.result ? '<div><strong>result:</strong> ' + esc(String(t.result).substring(0, 500)) + '</div>' : ''}
                ${flowHtml}
              </div>
            </details>`;
          }
          html += `</div>`;
        }

        html += `<pre style="${PRE}">${esc(a.content || '')}</pre>
          </div>
        </details>`;
      } else {
        html += `<div style="padding:10px 16px;color:var(--text-400);font-style:italic;font-size:13px">No response</div>`;
      }

      html += `</div>`;
    }

    body.innerHTML = html;
  } catch(e) {
    document.getElementById('inspect-body').innerHTML = `<div style="color:var(--error);padding:24px">${esc(e.message)}</div>`;
  }
}

async function answerWorkerQuestion(workerId, delegateValue) {
  let answer = delegateValue || '';
  if (!answer) {
    const card = document.getElementById(`wq-${workerId}`);
    if (!card) return;
    const selected = card.querySelector('.wq-option.selected input');
    if (selected) { answer = selected.value; }
    else {
      const ta = card.querySelector('.wq-freeform');
      answer = ta ? ta.value.trim() : '';
    }
  }
  if (!answer) { showToast('Please select an option or type an answer', true); return; }
  try {
    await API.post(`/v1/workers/${encodeURIComponent(workerId)}/answer`, { answer });
    const card = document.getElementById(`wq-${workerId}`);
    if (card) { card.classList.add('wq-answered'); }
  } catch (e) { showToast('Failed to send answer: ' + e.message, true); }
}

async function answerChatQuestion(sessionId) {
  const card = document.getElementById(`aq-${sessionId}`);
  if (!card) return;
  const items = card.querySelectorAll('.wq-item');
  // Collect one answer per question item (radio selection wins over freeform).
  const answers = {};
  const missing = [];
  items.forEach((item) => {
    const question = item.getAttribute('data-question') || '';
    let val = '';
    const selected = item.querySelector('.wq-option.selected input');
    if (selected) { val = selected.value; }
    else {
      const ta = item.querySelector('.wq-freeform');
      val = ta ? ta.value.trim() : '';
    }
    if (!val) { missing.push(question); return; }
    answers[question] = val;
  });
  if (!items.length || missing.length) {
    showToast(items.length ? `Please answer all ${items.length} questions` : 'No question to answer', true);
    return;
  }
  const body = { session_id: sessionId };
  if (items.length === 1) {
    // Preserve legacy single-question wire shape for backward compat.
    body.answer = Object.values(answers)[0];
  } else {
    body.answers = answers;
  }
  try {
    await API.post('/v1/chat/answer', body);
    card.classList.add('wq-answered');
  } catch (e) { showToast('Failed to send answer: ' + e.message, true); }
}

async function abortWorkerFromQuestion(workerId) {
  try {
    await API.post(`/v1/workers/${encodeURIComponent(workerId)}/answer`, { answer: '__abort__' });
  } catch(e) {}
  const chat = state.activeChat;
  if (chat?.sessionId) {
    try { await API.post('/v1/chat', { session_id: chat.sessionId, message: `[abort worker ${workerId}]` }); } catch(e) {}
  }
  const card = document.getElementById(`wq-${workerId}`);
  if (card) { card.classList.add('wq-answered'); card.querySelector('.wq-body').innerHTML += '<div style="margin-top:8px;font-size:12px;color:var(--error)">Worker aborted</div>'; }
}

async function stopGeneration() {
  const chat = state.activeChat;
  if (!chat?.sessionId) return;
  try {
    // Abort the active fetch immediately so the client stops reading
    if (API._abortController) API._abortController.abort();
    await API.cancelChat(chat.sessionId);
    chat.streaming = false;
    if (chat.streamingText) {
      chat.messages.push({role:'assistant', content: chat.streamingText, _cancelled: true});
    }
    chat.streamingText = '';
    chat.thinkingText = '';
    chat.files = [];
    updateStreamingUI(false);
    renderMessages();
    updateStatusBar();
    clearInterval(chat._streamTimerInterval);
  } catch(e) {}
}

function updateStreamingUI(isStreaming, chat) {
  const spinnerBar = document.getElementById('spinner-bar');
  const sendBtn = document.getElementById('chat-send-btn');
  const stopBtn = document.getElementById('chat-stop-btn');
  // Use provided chat, fall back to activeChat for backward compat (stopGeneration, etc.)
  const targetChat = chat || state.activeChat;

  if (isStreaming) {
    spinnerBar.classList.add('active');
    sendBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    document.getElementById('spinner-model').textContent = modelShortName(targetChat?.model);
    document.getElementById('spinner-label').textContent = 'Thinking...';
    document.getElementById('spinner-elapsed').textContent = '';
  } else {
    spinnerBar.classList.remove('active');
    sendBtn.classList.remove('hidden');
    stopBtn.classList.add('hidden');
  }
}

function updateStreamTimer(chat) {
  const target = chat || state.activeChat;
  if (!target?._streamStartTime) return;
  const elapsed = ((Date.now() - target._streamStartTime) / 1000).toFixed(1);
  document.getElementById('spinner-elapsed').textContent = elapsed + 's';
}

/* ═══════════════════════════════════════════════════════════
   MESSAGE RENDERING
   ═══════════════════════════════════════════════════════════ */
function renderMessages() {
  const container = document.getElementById('messages-container');
  const chat = state.activeChat;
  if (!chat) { container.innerHTML = ''; return; }

  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();

  // Group flat messages[] into turns. A turn opens at every user/human role
  // and closes at the next one. Pre-user messages (rare) become turn 0.
  const turns = []; // { turnNum, userIdx, userMsg, memberIdxs: [...] }
  let cur = null;
  let nextTurnNum = 1;
  for (let i = 0; i < chat.messages.length; i++) {
    const m = chat.messages[i];
    if (m.role === 'user' || m.role === 'human') {
      if (cur) turns.push(cur);
      cur = { turnNum: nextTurnNum++, userIdx: i, userMsg: m, memberIdxs: [i] };
    } else {
      if (!cur) cur = { turnNum: 0, userIdx: -1, userMsg: null, memberIdxs: [] };
      cur.memberIdxs.push(i);
    }
  }
  if (cur) turns.push(cur);

  let html = '';
  for (const t of turns) {
    const isCollapsed = chat._collapsedTurns.has(t.turnNum);
    const cls = isCollapsed ? 'turn-group collapsed' : 'turn-group';
    const badge = t.turnNum > 0
      ? `<div class="turn-group-header">
           <span class="turn-group-badge" onclick="toggleTurnCollapse(${t.turnNum})" title="Klick zum ${isCollapsed ? 'Aufklappen' : 'Zuklappen'} dieser Anfrage">
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
             Anfrage ${t.turnNum}
           </span>
           <span class="turn-group-collapsed-hint">${esc(turnQuestionPreview(t.userMsg, 80))}</span>
         </div>`
      : '';
    let body = renderTurnBody(chat.messages, t.memberIdxs, t.turnNum, chat);
    html += `<div class="${cls}" data-turn="${t.turnNum}">${badge}<div class="turn-body">${body}</div></div>`;
  }

  container.innerHTML = html;

  // Syntax highlight code blocks (skip tool-result blocks — pre-highlighted inline)
  container.querySelectorAll('pre:not(.tool-result-pre) code').forEach(block => {
    try { hljs.highlightElement(block); } catch(e) {}
  });

  // Update right panel badges (attachment/reference/artifact counts)
  if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
}

// --- Turn collapse/nav helpers ---
function turnQuestionPreview(msg, maxChars) {
  if (!msg) return '';
  let txt = '';
  if (typeof msg.content === 'string') txt = msg.content;
  else if (Array.isArray(msg.content)) {
    for (const b of msg.content) if (b?.type === 'text') txt += (b.text || '');
  }
  txt = txt.replace(/\s+/g, ' ').trim();
  if (txt.length > maxChars) txt = txt.slice(0, maxChars - 1) + '…';
  return txt;
}

function listTurns() {
  const chat = state.activeChat;
  if (!chat?.messages) return [];
  const out = [];
  let n = 0;
  for (let i = 0; i < chat.messages.length; i++) {
    const m = chat.messages[i];
    if (m.role === 'user' || m.role === 'human') {
      n++;
      out.push({ turnNum: n, userIdx: i, userMsg: m });
    }
  }
  return out;
}

function turnNumForMessageIdx(idx) {
  const chat = state.activeChat;
  if (!chat?.messages) return 0;
  let n = 0;
  for (let i = 0; i <= idx && i < chat.messages.length; i++) {
    const m = chat.messages[i];
    if (m.role === 'user' || m.role === 'human') n++;
  }
  return n;
}

function toggleTurnCollapse(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();
  if (chat._collapsedTurns.has(turnNum)) chat._collapsedTurns.delete(turnNum);
  else chat._collapsedTurns.add(turnNum);
  renderMessages();
}

function setAllTurnsCollapsed(collapsed) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();
  if (collapsed) {
    for (const t of listTurns()) chat._collapsedTurns.add(t.turnNum);
  } else {
    chat._collapsedTurns.clear();
  }
  renderMessages();
}

function setTurnsCollapsedRelativeTo(anchorMsgIdx, direction, collapsed) {
  // direction: 'above' | 'below' | 'self'
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();
  const anchorTurn = turnNumForMessageIdx(anchorMsgIdx);
  for (const t of listTurns()) {
    let match = false;
    if (direction === 'self') match = t.turnNum === anchorTurn;
    else if (direction === 'above') match = t.turnNum < anchorTurn;
    else if (direction === 'below') match = t.turnNum > anchorTurn;
    if (!match) continue;
    if (collapsed) chat._collapsedTurns.add(t.turnNum);
    else chat._collapsedTurns.delete(t.turnNum);
  }
  renderMessages();
}

function jumpToTurn(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (chat._collapsedTurns) chat._collapsedTurns.delete(turnNum);
  renderMessages();
  // Scroll the turn header into view
  requestAnimationFrame(() => {
    const el = document.querySelector(`.turn-group[data-turn="${turnNum}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      // Brief flash
      el.style.transition = 'background 0.4s';
      el.style.background = 'rgba(255,200,80,0.18)';
      setTimeout(() => { el.style.background = ''; }, 900);
    }
  });
  // Close any open dropdowns
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(d => d.classList.remove('open'));
}

function renderTurnNavMenuItems(anchorMsgIdx) {
  const turns = listTurns();
  const anchorTurn = turnNumForMessageIdx(anchorMsgIdx);
  const chat = state.activeChat;
  const collapsed = chat?._collapsedTurns || new Set();
  const thisCollapsed = collapsed.has(anchorTurn);

  // Decide bulk actions: if everything (above/below/all) is currently collapsed, offer expand; else offer collapse.
  const above = turns.filter(t => t.turnNum < anchorTurn);
  const below = turns.filter(t => t.turnNum > anchorTurn);
  const allCollapsed = turns.length > 0 && turns.every(t => collapsed.has(t.turnNum));
  const aboveAllCollapsed = above.length > 0 && above.every(t => collapsed.has(t.turnNum));
  const belowAllCollapsed = below.length > 0 && below.every(t => collapsed.has(t.turnNum));

  const caretDown = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="6 9 12 15 18 9"/></svg>';
  const caretUp = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="18 15 12 9 6 15"/></svg>';

  let html = '';
  html += `<div class="msg-edit-dropdown-section-label">Diese Anfrage</div>`;
  html += `<div class="msg-edit-dropdown-item" onclick="setTurnsCollapsedRelativeTo(${anchorMsgIdx}, 'self', ${!thisCollapsed})">
    ${thisCollapsed ? caretDown : caretUp}
    ${thisCollapsed ? 'Diese Anfrage aufklappen' : 'Diese Anfrage zuklappen'}
  </div>`;

  html += `<hr>`;
  html += `<div class="msg-edit-dropdown-section-label">Alle Anfragen</div>`;
  html += `<div class="msg-edit-dropdown-item" onclick="setAllTurnsCollapsed(${!allCollapsed})">
    ${allCollapsed ? caretDown : caretUp}
    ${allCollapsed ? 'Alle aufklappen' : 'Alle zuklappen'}
  </div>`;
  if (above.length > 0) {
    html += `<div class="msg-edit-dropdown-item" onclick="setTurnsCollapsedRelativeTo(${anchorMsgIdx}, 'above', ${!aboveAllCollapsed})">
      ${aboveAllCollapsed ? caretDown : caretUp}
      ${aboveAllCollapsed ? 'Alle oberhalb aufklappen' : 'Alle oberhalb zuklappen'} (${above.length})
    </div>`;
  }
  if (below.length > 0) {
    html += `<div class="msg-edit-dropdown-item" onclick="setTurnsCollapsedRelativeTo(${anchorMsgIdx}, 'below', ${!belowAllCollapsed})">
      ${belowAllCollapsed ? caretDown : caretUp}
      ${belowAllCollapsed ? 'Alle unterhalb aufklappen' : 'Alle unterhalb zuklappen'} (${below.length})
    </div>`;
  }

  if (turns.length > 0) {
    html += `<hr>`;
    html += `<div class="msg-edit-dropdown-section-label">Zu Anfrage springen</div>`;
    for (const t of turns) {
      const q = esc(turnQuestionPreview(t.userMsg, 60) || '(empty)');
      const isHere = t.turnNum === anchorTurn ? ' style="background:var(--bg-200)"' : '';
      html += `<div class="msg-edit-dropdown-item"${isHere} onclick="jumpToTurn(${t.turnNum})">
        <span class="turn-nav-num">${t.turnNum}.</span>
        <span class="turn-nav-q">${q}</span>
      </div>`;
    }
  }
  return html;
}

function toggleTurnNavMenu(ev, idx) {
  ev.stopPropagation();
  const all = document.querySelectorAll('.msg-edit-dropdown');
  const target = document.getElementById('turn-nav-menu-' + idx);
  all.forEach(d => { if (d !== target) d.classList.remove('open'); });
  if (target) {
    if (!target.classList.contains('open')) {
      target.innerHTML = renderTurnNavMenuItems(idx);
    }
    target.classList.toggle('open');
  }
}

// Returns the 1-based turn number that a new message would belong to,
// by counting user/human messages already in chat.messages.
function currentTurnNum(chat) {
  let n = 0;
  for (const m of chat.messages) {
    if (m.role === 'user' || m.role === 'human') n++;
  }
  return n;
}

// Renders all messages in a turn, wrapping pre-response activity (thinking +
// tool calls) in a collapsed summary block once a final assistant response exists.
// ── Activity summary state machine ─────────────────────────────────────────
// Per-turn collapse state lives on chat._activityStates (Map<turnNum, str>).
// Values: 'auto-open' | 'auto-closed' | 'user-open' | 'user-closed'
// Absent key = no activity yet seen OR history load (always closed).
//
// Rules:
//   add (new activity element during streaming):
//     first element → 'auto-open'
//     4th element   → 'auto-closed'  (only if not user-controlled)
//   response (assistant response finalised):
//     → 'auto-closed'  (only if not user-controlled)
//   user toggle:
//     → 'user-open' / 'user-closed'  (never overridden again)

function _activityCount(messages, memberIdxs) {
  let n = 0;
  for (const idx of memberIdxs) {
    const m = messages[idx];
    if (m.role === 'thinking' || m.role === 'tool_call') n++;
    // worker calls are tracked via tool_call with worker result — count the tool_call
  }
  return n;
}

function _activityAutoUpdate(chat, turnNum, event) {
  if (!chat._activityStates) chat._activityStates = new Map();
  const cur = chat._activityStates.get(turnNum);
  const userControlled = cur === 'user-open' || cur === 'user-closed';
  if (userControlled) return; // never touch user-controlled state

  if (event === 'add') {
    if (!cur) {
      chat._activityStates.set(turnNum, 'auto-open');
    } else if (cur === 'auto-open') {
      // Count current activity elements — if reaching 4, close
      const t = state.activeChat;
      if (t) {
        // Find this turn's memberIdxs
        let memberIdxs = null;
        let n = 0;
        for (let i = 0; i < t.messages.length; i++) {
          const m = t.messages[i];
          if (m.role === 'user' || m.role === 'human') {
            n++;
            if (n === turnNum) { memberIdxs = []; }
            else if (memberIdxs !== null) break;
          } else if (memberIdxs !== null) {
            memberIdxs.push(i);
          }
        }
        if (memberIdxs && _activityCount(t.messages, memberIdxs) >= 4) {
          chat._activityStates.set(turnNum, 'auto-closed');
        }
      }
    }
  } else if (event === 'response') {
    chat._activityStates.set(turnNum, 'auto-closed');
  }
}

function toggleActivitySummary(turnNum) {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat._activityStates) chat._activityStates = new Map();
  const cur = chat._activityStates.get(turnNum);
  const isOpen = cur === 'auto-open' || cur === 'user-open';
  chat._activityStates.set(turnNum, isOpen ? 'user-closed' : 'user-open');
  // Re-render to apply the new state
  renderMessages();
}

function renderTurnBody(messages, memberIdxs, turnNum, chat) {
  const isActivity = (m) => m.role === 'thinking' || m.role === 'tool_call' || m.role === 'tool_result';
  const isResponse = (m) => m.role === 'assistant' && (typeof m.content === 'string' ? m.content.trim() : '');

  // Find the last assistant response with content in this turn.
  let lastResponseMemberPos = -1;
  for (let i = memberIdxs.length - 1; i >= 0; i--) {
    if (isResponse(messages[memberIdxs[i]])) { lastResponseMemberPos = i; break; }
  }

  // Group activity before the response into rounds.
  // A round is one thinking block + all tool_calls that share its tool_round.
  // Tool_calls without a matching thinking (tool_round=null or no thinking) form a bare round.
  const scanEnd = lastResponseMemberPos === -1 ? memberIdxs.length : lastResponseMemberPos;
  let thinkCount = 0, toolCount = 0, workerCount = 0;

  // Collect activity messages in order with their original idx
  const activityItems = [];
  for (let i = 0; i < scanEnd; i++) {
    const idx = memberIdxs[i];
    const m = messages[idx];
    if (!isActivity(m)) continue;
    if (m.role === 'thinking') thinkCount++;
    if (m.role === 'tool_call') {
      // Check if worker
      let isWorker = false;
      for (let j = i + 1; j < memberIdxs.length; j++) {
        const next = messages[memberIdxs[j]];
        if (next.role === 'tool_result' && next.name === m.name) {
          const rs = typeof next.result === 'string' ? next.result : JSON.stringify(next.result);
          isWorker = rs.includes('"worker": true') || rs.includes('"worker":true');
          break;
        }
        if (next.role === 'tool_call' || next.role === 'assistant' || next.role === 'user') break;
      }
      if (isWorker) workerCount++; else toolCount++;
    }
    activityItems.push({ idx, m });
  }

  // Build rounds: each thinking msg starts a new round; tool_calls attach to current round
  const rounds = []; // [{thinking: item|null, tools: [item,...]}]
  let currentRound = null;
  for (const item of activityItems) {
    if (item.m.role === 'tool_result') continue; // rendered inside tool_call
    if (item.m.role === 'thinking') {
      currentRound = { thinking: item, tools: [] };
      rounds.push(currentRound);
    } else if (item.m.role === 'tool_call') {
      if (!currentRound) { currentRound = { thinking: null, tools: [] }; rounds.push(currentRound); }
      currentRound.tools.push(item);
    }
  }

  // Render rounds into HTML
  let activityHtml = '';
  for (const round of rounds) {
    let roundHtml = '';
    if (round.thinking) {
      roundHtml += `<div class="activity-item activity-thinking">${renderMessage(round.thinking.m, round.thinking.idx)}</div>`;
    }
    if (round.tools.length) {
      const toolsHtml = round.tools.map(t =>
        `<div class="activity-item activity-tool">${renderMessage(t.m, t.idx)}</div>`
      ).join('');
      roundHtml += `<div class="activity-tools-group${round.thinking ? ' activity-tools-indented' : ''}">${toolsHtml}</div>`;
    }
    activityHtml += `<div class="activity-round">${roundHtml}</div>`;
  }

  // Everything from lastResponseMemberPos onwards
  let responseHtml = '';
  if (lastResponseMemberPos !== -1) {
    for (let i = lastResponseMemberPos; i < memberIdxs.length; i++) {
      responseHtml += renderMessage(messages[memberIdxs[i]], memberIdxs[i]);
    }
  }

  // No activity at all — render flat
  if (thinkCount === 0 && toolCount === 0 && workerCount === 0) {
    return activityHtml + responseHtml;
  }

  // Determine open/closed state
  const stateVal = chat?._activityStates?.get(turnNum); // absent = history load = closed
  const isOpen = stateVal === 'auto-open' || stateVal === 'user-open';

  const parts = [];
  if (thinkCount > 0) parts.push(thinkCount === 1 ? '1 mal nachgedacht' : `${thinkCount} mal nachgedacht`);
  if (toolCount > 0) parts.push(toolCount === 1 ? '1 Tool-Aufruf' : `${toolCount} Tool-Aufrufe`);
  if (workerCount > 0) parts.push(workerCount === 1 ? '1 Worker-Aufruf' : `${workerCount} Worker-Aufrufe`);
  const label = parts.join(' · ');

  const summaryEl = `<summary class="activity-summary-header" onclick="event.preventDefault();toggleActivitySummary(${turnNum})">
        <svg class="activity-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        ${esc(label)}
      </summary>`;

  if (lastResponseMemberPos === -1) {
    return `
      <details class="activity-summary"${isOpen ? ' open' : ''}>
        ${summaryEl}
        <div class="activity-summary-body">${activityHtml}</div>
      </details>
    `;
  }

  return `
    <details class="activity-summary"${isOpen ? ' open' : ''}>
      ${summaryEl}
      <div class="activity-summary-body">${activityHtml}</div>
    </details>
    ${responseHtml}
  `;
}

function renderMessage(msg, idx) {
  if (msg.role === 'human' || msg.role === 'user') {
    return renderUserMessage(msg, idx);
  }
  if (msg.role === 'assistant') {
    return renderAssistantMessage(msg, idx);
  }
  if (msg.role === 'thinking') {
    return renderThinkingMessage(msg, idx);
  }
  if (msg.role === 'tool_call') {
    return renderToolCall(msg, idx);
  }
  if (msg.role === 'tool_result') {
    return renderToolResult(msg, idx);
  }
  if (msg.role === 'system') {
    return ''; // skip system messages
  }
  // Unknown role - try to render as assistant
  if (msg.content) {
    return renderAssistantMessage(msg, idx);
  }
  return '';
}

function renderThinkingMessage(msg, idx) {
  const text = typeof msg.content === 'string' ? msg.content : '';
  if (!text) return '';
  return `
    <div class="msg-turn msg-turn-assistant">
      <div class="thinking-block" onclick="this.classList.toggle('open')">
        <div class="thinking-block-header">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/></svg>
          Denke nach...
        </div>
        <div class="thinking-block-body msg-content">${renderMarkdown(text)}</div>
      </div>
    </div>
  `;
}

function renderUserMessage(msg, idx) {
  let textContent = '';
  const imageUrls = []; // collect data URLs for thumbnail rendering

  if (typeof msg.content === 'string') {
    textContent = msg.content;
  } else if (Array.isArray(msg.content)) {
    // Multimodal content blocks (e.g. from DB restore)
    for (const block of msg.content) {
      if (block.type === 'text') {
        textContent += block.text || '';
      } else if (block.type === 'image_url' && block.image_url?.url) {
        imageUrls.push(block.image_url.url);
      } else if (block.type === 'image' && block.source?.data) {
        const mt = block.source.media_type || 'image/png';
        imageUrls.push(`data:${mt};base64,${block.source.data}`);
      }
    }
  } else {
    textContent = JSON.stringify(msg.content);
  }

  // Collect from msg.images (legacy) and msg.files with preview (unified path)
  if (msg.images?.length) {
    for (const img of msg.images) {
      if (img.preview) imageUrls.push(img.preview);
      else if (img.data) imageUrls.push(`data:${img.type || 'image/png'};base64,${img.data}`);
    }
  }
  if (msg.files?.length) {
    for (const f of msg.files) {
      if (f.preview) imageUrls.push(f.preview);
      else if (f.data && f.type?.startsWith('image/')) imageUrls.push(`data:${f.type};base64,${f.data}`);
    }
  }

  let thumbsHtml = '';
  if (imageUrls.length) {
    thumbsHtml = '<div class="msg-user-files" style="gap:6px">';
    for (const url of imageUrls) {
      thumbsHtml += `<div class="msg-img-thumb" onclick="openRightPanel('attachments')"><img src="${url}" alt=""></div>`;
    }
    thumbsHtml += '</div>';
  }

  let filesHtml = '';
  if (msg.files?.length) {
    filesHtml = '<div class="msg-user-files">';
    for (const f of msg.files) {
      const ext = f.name?.split('.').pop()?.toUpperCase() || 'FILE';
      filesHtml += `<div class="msg-file-chip"><span class="file-ext">${esc(ext)}</span> ${esc(f.name || 'File')}</div>`;
    }
    filesHtml += '</div>';
  }
  return `
    <div class="msg-turn msg-turn-user">
      ${thumbsHtml}
      ${filesHtml}
      <div class="msg-user">${esc(textContent)}</div>
      <div class="msg-actions-bar">
        <button class="msg-action-btn" onclick="toggleMsgEditMenu(event, ${idx})" title="Edit history">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>
        </button>
        <div class="msg-edit-dropdown" id="msg-edit-menu-${idx}">
          <div class="msg-edit-dropdown-item" onclick="deleteMessages('turn', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 6h18M8 6V4h8v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>
            Remove Q&A pair
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('after', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="20" x2="22" y2="20" stroke-dasharray="3 3"/></svg>
            Remove all after this
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('before', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="4" x2="22" y2="4" stroke-dasharray="3 3"/></svg>
            Remove all before this
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderAssistantMessage(msg, idx) {
  const content = typeof msg.content === 'string' ? msg.content : '';
  const rendered = renderMarkdown(content);

  let thinkingHtml = '';
  if (msg._thinking) {
    const summaryNote = msg._thinkingSummary?.reasoning_tokens
      ? `<span style="margin-left:8px;opacity:0.7;font-size:11px;">${msg._thinkingSummary.reasoning_tokens.toLocaleString()} tok</span>`
      : '';
    thinkingHtml = `
      <div class="thinking-block" onclick="this.classList.toggle('open')">
        <div class="thinking-block-header">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/></svg>
          Thinking${summaryNote}
        </div>
        <div class="thinking-block-body msg-content">${renderMarkdown(msg._thinking)}</div>
      </div>
    `;
  } else if (msg._thinkingSummary?.reasoning_tokens) {
    // Opaque reasoning: provider burned tokens on thinking but didn't return the text.
    // Render a non-expandable badge so the user knows it happened.
    const n = msg._thinkingSummary.reasoning_tokens.toLocaleString();
    thinkingHtml = `
      <div class="thinking-block" style="cursor:default;opacity:0.75;" title="Provider returned reasoning token count but not the text (opaque thinking).">
        <div class="thinking-block-header">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/></svg>
          Thought for ${n} tokens
        </div>
      </div>
    `;
  }

  let filesHtml = '';
  if (msg._files?.length) {
    filesHtml = '<div class="msg-file-attachments">';
    for (const f of msg._files) {
      if (f.artifact_role === 'intermediate') continue;
      const name = f.path ? f.path.split('/').pop() : 'file';
      const badge = f.action === 'created' ? '<span class="fa-badge created">new</span>' : f.action === 'modified' ? '<span class="fa-badge modified">edit</span>' : '';
      if (f.artifact_id) {
        filesHtml += `
          <div class="file-attachment-card artifact-card" onclick="openArtifactPanel('${esc(f.artifact_id)}', ${f.artifact_version || ''})">
            <span class="fa-icon">${fileTypeIcon(name)}</span>
            <span class="fa-name">${esc(name)}</span>
            ${badge}
            <span class="fa-artifact-indicator"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></span>
          </div>
        `;
      } else {
        filesHtml += `
          <div class="file-attachment-card" onclick="previewFile('${esc(f.path)}')">
            <span class="fa-icon">${fileTypeIcon(name)}</span>
            <span class="fa-name">${esc(name)}</span>
            ${badge}
          </div>
        `;
      }
    }
    filesHtml += '</div>';
  }

  // Reference badges — split into Zitiert (always visible) and Durchsucht
  // (collapsed via <details>). Zitiert pulls from `[Quelle: <basename>...]`
  // markers in this assistant message's text; Durchsucht is the rest of the
  // mempalace_query results that didn't make it into a citation.
  let refsHtml = '';
  const { cited: citedMsgRefs, searched: searchedMsgRefs } = getReferencesForMessage(idx);
  const renderBadge = (ref) => {
    const isProject = ref.domain === 'project';
    const tipPath = ref.link;
    const ext = (ref.title || '').split('.').pop().toLowerCase();
    const iconBg = isProject ? ({
      pdf: '#d33', docx: '#2b579a', pptx: '#d24726',
      xlsx: '#217346', eml: '#0072c6', msg: '#0072c6',
    }[ext] || 'var(--accent-brand)') : '';
    const iconChip = isProject
      ? `<span class="ref-badge-icon" style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:3px;background:${iconBg};color:#fff;font-size:8px;font-weight:700;letter-spacing:0.02em">${esc(ext || 'F')}</span>`
      : `<img src="${esc(ref.favicon)}" onerror="this.style.display='none'" alt="">`;
    return `
      <div class="ref-badge" onclick="openReferencesPanel('${esc(ref.link)}')" title="${esc(tipPath)}">
        ${iconChip}
        <span class="ref-badge-text">${esc(ref.title || ref.domain)}</span>
      </div>
    `;
  };
  if (citedMsgRefs.length > 0 || searchedMsgRefs.length > 0) {
    refsHtml = '<div class="msg-references-wrap">';
    if (citedMsgRefs.length > 0) {
      refsHtml += `
        <div class="msg-references-row">
          <span class="msg-references-label">Zitiert</span>
          <div class="msg-references">${citedMsgRefs.map(renderBadge).join('')}</div>
        </div>`;
    }
    if (searchedMsgRefs.length > 0) {
      // Collapsed by default unless there are no cited refs.
      const open = citedMsgRefs.length === 0 ? 'open' : '';
      refsHtml += `
        <details class="msg-references-row msg-references-searched" ${open}>
          <summary class="msg-references-summary">
            <span class="msg-references-disclosure">▸</span>
            <span class="msg-references-label">Durchsucht</span>
            <span class="msg-references-count">${searchedMsgRefs.length}</span>
          </summary>
          <div class="msg-references">${searchedMsgRefs.map(renderBadge).join('')}</div>
        </details>`;
    }
    refsHtml += '</div>';
  }

  return `
    <div class="msg-turn msg-turn-assistant">
      ${thinkingHtml}
      <div class="msg-assistant msg-content">${rendered}</div>
      ${filesHtml}
      ${refsHtml}
      <div class="msg-actions-bar">
        <button class="msg-action-btn" onclick="copyMessage(${idx})" title="Copy">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
        </button>
        <button class="msg-action-btn" onclick="retryMessage(${idx})" title="Retry">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
        </button>
        ${messageUsedKnowledge(idx) ? `<button class="msg-action-btn" onclick="openUsedMemoryGraph(${idx})" title="Show used Memory and Relationships">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><line x1="7.7" y1="7.5" x2="10.3" y2="16.5"/><line x1="16.3" y1="7.5" x2="13.7" y2="16.5"/><line x1="8.5" y1="6" x2="15.5" y2="6"/></svg>
        </button>` : ''}
        <button class="msg-action-btn" onclick="toggleTurnNavMenu(event, ${idx})" title="Anfragen: zuklappen / springen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        </button>
        <div class="msg-edit-dropdown turn-nav-dropdown" id="turn-nav-menu-${idx}"></div>
        <button class="msg-action-btn" onclick="toggleMsgEditMenu(event, ${idx})" title="Edit history">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>
        </button>
        <div class="msg-edit-dropdown" id="msg-edit-menu-${idx}">
          <div class="msg-edit-dropdown-item" onclick="deleteMessages('response', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 18L18 6M6 6l12 12"/></svg>
            Remove this response
          </div>
          <div class="msg-edit-dropdown-item" onclick="deleteMessages('turn', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 6h18M8 6V4h8v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>
            Remove Q&A pair
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('after', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="20" x2="22" y2="20" stroke-dasharray="3 3"/></svg>
            Remove all after this
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('before', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="4" x2="22" y2="4" stroke-dasharray="3 3"/></svg>
            Remove all before this
          </div>
        </div>
        <button class="msg-action-btn" onclick="toggleMsgMemoryMenu(event, ${idx})" title="Memory">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10l9-6 9 6v1H3z" fill="currentColor" fill-opacity="0.15"/><line x1="3" y1="11" x2="21" y2="11"/><line x1="3" y1="21" x2="21" y2="21"/><line x1="5" y1="11" x2="5" y2="21"/><line x1="11" y1="11" x2="11" y2="21"/><line x1="17" y1="11" x2="17" y2="21"/><line x1="19" y1="11" x2="19" y2="21"/></svg>
        </button>
        <div class="msg-edit-dropdown" id="msg-memory-menu-${idx}">
          ${renderMemoryMenuItems(idx)}
        </div>
      </div>
    </div>
  `;
}

// --- Memory menu helpers ---

// Find the user message id that opens the turn containing messages[idx].
function getTurnIdForMessage(idx) {
  const chat = state.activeChat;
  if (!chat) return 0;
  const msgs = chat.messages || [];
  for (let i = Math.min(idx, msgs.length - 1); i >= 0; i--) {
    if (msgs[i].role === 'user' && msgs[i].id) return msgs[i].id;
  }
  return 0;
}

function renderMemoryMenuItems(idx) {
  const chat = state.activeChat;
  const memOff = !chat || (chat.memoryMode || (chat.saveToMemory ? 'on' : 'off')) === 'off';
  const turnId = getTurnIdForMessage(idx);
  const memorized = (state.memorizedTurns || {})[chat?.sessionId] || new Set();
  const hasAny = memorized.size > 0;
  const thisMemorized = turnId && memorized.has(turnId);

  // Collect turn ids from messages for above/below checks
  const msgs = chat?.messages || [];
  let turnsAbove = 0, turnsBelow = 0, totalTurns = 0;
  for (const m of msgs) {
    if (m.role !== 'user' || !m.id) continue;
    totalTurns++;
    if (m.id < turnId) turnsAbove++;
    else if (m.id > turnId) turnsBelow++;
  }
  const hasAbove = turnsAbove > 0;
  const hasBelow = turnsBelow > 0;

  // Each item: {label, scope, mode ('memorize'|'purge'), enabled}
  const items = [
    { label: 'Memorize complete chat',             scope: 'all',   mode: 'memorize', enabled: memOff && totalTurns > 0 },
    { label: 'Memorize this response',             scope: 'this',  mode: 'memorize', enabled: memOff && !!turnId && !thisMemorized },
    { label: 'Memorize all above this',            scope: 'above', mode: 'memorize', enabled: memOff && hasAbove },
    { label: 'Memorize all below this',            scope: 'below', mode: 'memorize', enabled: memOff && hasBelow },
    { sep: true },
    { label: 'Remove all memory from this chat',   scope: 'all',   mode: 'purge',    enabled: memOff && hasAny, destructive: true },
    { label: 'Remove memory from this response',   scope: 'this',  mode: 'purge',    enabled: memOff && !!thisMemorized, destructive: true },
    { label: 'Remove memory from responses above', scope: 'above', mode: 'purge',    enabled: memOff && hasAbove, destructive: true },
    { label: 'Remove memory from responses below', scope: 'below', mode: 'purge',    enabled: memOff && hasBelow, destructive: true },
  ];

  const svgMem = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 10l9-6 9 6v1H3z" fill="currentColor" fill-opacity="0.15"/><line x1="3" y1="11" x2="21" y2="11"/><line x1="3" y1="21" x2="21" y2="21"/></svg>';
  const svgDel = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>';

  let html = '';
  if (!memOff) {
    html += `<div class="msg-edit-dropdown-section-label" title="Set chat memory to off to use these actions">Memory mode: ${chat.memoryMode || 'on'} — actions disabled</div>`;
  }
  for (const it of items) {
    if (it.sep) { html += '<hr>'; continue; }
    const cls = [
      'msg-edit-dropdown-item',
      it.destructive ? 'destructive' : '',
      it.enabled ? '' : 'disabled',
    ].filter(Boolean).join(' ');
    const handler = it.enabled ? `onclick="runTurnMemoryAction('${it.mode}','${it.scope}',${idx})"` : '';
    html += `<div class="${cls}" ${handler}>${it.mode === 'purge' ? svgDel : svgMem}${it.label}</div>`;
  }
  return html;
}

function toggleMsgMemoryMenu(event, idx) {
  event.stopPropagation();
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(el => el.classList.remove('open'));
  const menu = document.getElementById(`msg-memory-menu-${idx}`);
  if (!menu) return;
  // Re-render items fresh (memorizedTurns may have changed)
  menu.innerHTML = renderMemoryMenuItems(idx);
  menu.classList.toggle('open');
  const close = (e) => {
    if (!menu.contains(e.target)) {
      menu.classList.remove('open');
      document.removeEventListener('click', close);
    }
  };
  setTimeout(() => document.addEventListener('click', close), 0);
  // Refresh memorized state in the background; re-render on update
  refreshMemorizedTurns().then(() => {
    if (menu.classList.contains('open')) menu.innerHTML = renderMemoryMenuItems(idx);
  });
}

async function refreshMemorizedTurns() {
  const chat = state.activeChat;
  if (!chat?.sessionId) return;
  try {
    const data = await API.get(`/v1/mempalace/session-turns?session_id=${encodeURIComponent(chat.sessionId)}`);
    if (!state.memorizedTurns) state.memorizedTurns = {};
    state.memorizedTurns[chat.sessionId] = new Set((data.turn_ids || []).map(n => parseInt(n) || 0));
  } catch (e) {}
}

async function runTurnMemoryAction(mode, scope, idx) {
  const chat = state.activeChat;
  if (!chat?.sessionId) return;
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(el => el.classList.remove('open'));
  const turnId = getTurnIdForMessage(idx);
  const action = mode === 'purge' ? 'purge_turns' : 'memorize_turns';
  const payload = { action, session_id: chat.sessionId, scope };
  if (scope !== 'all') payload.anchor_turn_id = turnId;

  if (mode === 'purge') {
    const label = { all: 'all memory for this chat', this: "this response's memory",
                    above: 'memory for responses above', below: 'memory for responses below' }[scope] || scope;
    if (!confirm(`Delete ${label}?\n\nThis permanently removes the matching MemPalace drawers and cannot be undone.`)) return;
  }

  try {
    const resp = await API.post('/v1/sessions/manage', payload);
    if (mode === 'purge') {
      showToast(`Deleted ${resp.purged || 0} turn(s) from memory`);
    } else {
      showToast(`Memorizing ${resp.memorizing || 0} turn(s)…`);
    }
    // Poll twice for updated state (memorize is async)
    setTimeout(refreshMemorizedTurns, 500);
    setTimeout(refreshMemorizedTurns, 2500);
  } catch (e) {
    showToast('Failed: ' + e.message, true);
  }
}

function toolDescribe(name, args) {
  const a = args || {};
  const descs = {
    read_file: () => `Datei lesen: ${a.path || a.file_path || '...'}`,
    write_file: () => `Datei schreiben: ${a.path || a.file_path || '...'}`,
    edit_file: () => `Datei bearbeiten: ${a.path || a.file_path || '...'}`,
    list_directory: () => `Verzeichnis auflisten: ${a.path || a.directory || '...'}`,
    search_files: () => `Dateien durchsuchen nach „${a.query || a.pattern || '...'}"`,
    execute_command: () => `Befehl ausführen: \`${(a.command || '').substring(0, 60)}${(a.command || '').length > 60 ? '...' : ''}\``,
    python_exec: () => `Python ausführen (${(a.code || '').split('\n').length} Zeilen)`,
    web_fetch: () => { try { return `Webseite abrufen: ${a.url ? new URL(a.url).hostname : '...'}`; } catch(e) { return `Webseite abrufen: ${a.url || '...'}`; } },
    exa_search: () => `Im Web suchen nach „${a.query || '...'}"`,
    gmail_inbox: () => 'Posteingang prüfen',
    gmail_read: () => `E-Mail lesen${a.id ? ' #' + a.id : ''}`,
    gmail_search: () => `E-Mails suchen: „${a.query || '...'}"`,
    gmail_send: () => `E-Mail senden an ${a.to || '...'}`,
    gmail_reply: () => `E-Mail beantworten${a.id ? ' #' + a.id : ''}`,
    memory_store: () => `Erinnerung speichern: „${a.name || a.title || '...'}"`,
    memory_recall: () => `Erinnerung abrufen: „${a.query || '...'}"`,
    memory_shared: () => `Geteilten Speicher lesen${a.scope ? ' (' + a.scope + ')' : ''}`,
    memory_delete: () => `Erinnerung löschen: „${a.name || '...'}"`,
    mempalace_query: () => `Hole Informationen aus Projektspeicher${a.query ? ': „' + String(a.query).substring(0, 60) + '"' : '...'}`,
    mempalace_get_drawer: () => `Projektspeicher-Eintrag abrufen`,
    mempalace_list_drawers: () => `Projektspeicher-Einträge auflisten`,
    mempalace_kg_query: () => `Wissensgraph abfragen`,
    mempalace_kg_search: () => `Wissensgraph durchsuchen`,
    mempalace_kg_neighbors: () => `Wissensgraph-Nachbarn abrufen`,
    save_chat_to_memory: () => `Chat in Speicher sichern`,
    delegate_task: () => `Aufgabe delegieren an ${a.agent || a.agent_id || '...'}`,
    task_status: () => `Aufgabenstatus prüfen`,
    task_cancel: () => `Aufgabe abbrechen`,
    git_command: () => `Git: ${a.subcommand || a.command || ''}`,
    github_command: () => `GitHub: ${a.subcommand || a.command || ''}`,
    use_skill: () => `Skill anwenden: „${a.skill || a.name || '...'}"`,
    code_graph_build: () => `Code-Graph erstellen${a.path ? ' für ' + a.path : ''}`,
    code_graph_query: () => `Code-Graph abfragen`,
    code_graph_impact: () => `Auswirkungen analysieren`,
    schedule_list: () => 'Zeitpläne auflisten',
    schedule_history: () => 'Zeitplan-Verlauf prüfen',
    context_search: () => `Kontext durchsuchen: „${a.query || '...'}"`,
    context_detail: () => `Kontext-Detail laden`,
    context_recall: () => `Kontext abrufen`,
    read_document: () => `Dokument lesen: ${a.path || a.file_path || ''}`,
    write_document: () => `Dokument schreiben: ${a.path || a.file_path || ''}`,
    edit_document: () => `Dokument bearbeiten: ${a.path || a.file_path || ''}`,
    list_nodes: () => 'Remote-Knoten auflisten',
    get_artifact_detail: () => `Artefakt prüfen: ${a.artifact_id || ''}`,
    worker_status: () => a.worker_id ? `Worker prüfen: ${a.worker_id}` : 'Worker-Status prüfen',
    worker_abort: () => `Worker abbrechen: ${a.worker_id || ''}`,
    worker_pause: () => `Worker pausieren: ${a.worker_id || ''}`,
    worker_resume: () => `Worker fortsetzen: ${a.worker_id || ''}`,
    worker_send: () => `An Worker senden: ${a.worker_id || ''}`,
    worker_ask_user: () => `Worker fragt: ${(a.question || '').substring(0, 50)}`,
  };
  const fn = descs[name];
  return fn ? fn() : name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function renderToolArgsTable(args) {
  if (!args || typeof args !== 'object' || Object.keys(args).length === 0) return '';
  let html = '<table class="tool-args-table">';
  for (const [k, v] of Object.entries(args)) {
    const val = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
    const displayVal = val.length > 300 ? val.substring(0, 300) + '...' : val;
    html += `<tr><td class="tool-args-key">${esc(k)}</td><td class="tool-args-val"><pre>${esc(displayVal)}</pre></td></tr>`;
  }
  html += '</table>';
  return html;
}

// Find the worker flow associated with a tool_call msg.
// Prefers a worker_id parsed from the result envelope (completed); falls back
// to the most-recent still-running worker with the same tool_name.
function findWorkerFlow(toolName, resultStr) {
  if (resultStr) {
    try {
      const rj = JSON.parse(resultStr);
      if (rj && rj.worker_id && state.workerFlows[rj.worker_id]) {
        return state.workerFlows[rj.worker_id];
      }
      if (rj && rj.worker_id) {
        // Envelope has the flow even when state wasn't seeded yet
        return {
          worker_id: rj.worker_id,
          tool_name: toolName,
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
  // Live fallback: most-recent running worker matching tool name
  const candidates = Object.values(state.workerFlows).filter(w =>
    w.tool_name === toolName &&
    !['COMPLETED', 'FAILED', 'ABORTED', 'TIMED_OUT'].includes(w.state)
  );
  if (candidates.length) {
    candidates.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
    return candidates[0];
  }
  return null;
}

function renderWorkerFlow(wf) {
  if (!wf) return '';
  const stateCls = (wf.state || 'running').toLowerCase();
  const dur = wf.duration != null ? `${Number(wf.duration).toFixed(1)}s` :
              (wf.started_at ? `${((Date.now()/1000) - wf.started_at).toFixed(0)}s` : '');
  const su = wf.summariser_usage || {};
  const suTotal = (su.tokens_in || 0) + (su.tokens_out || 0);
  const suLabel = suTotal > 0
    ? `<span class="worker-flow-id" title="Summariser LLM: ${(su.tokens_in||0).toLocaleString()} in / ${(su.tokens_out||0).toLocaleString()} out${su.model ? ' · ' + esc(su.model) : ''}">LLM ${suTotal.toLocaleString()} tok</span>`
    : '';
  const header = `
    <div class="worker-flow-header">
      <span>Worker flow</span>
      <span class="worker-flow-state ${stateCls}">${esc(wf.state || 'RUNNING')}</span>
      <span class="worker-flow-id">${esc(wf.worker_id || '')}</span>
      ${suLabel}
      ${dur ? `<span class="worker-flow-duration">${esc(dur)}</span>` : ''}
    </div>`;
  const flow = wf.flow || [];
  const activeIdx = flow.length - 1;
  const steps = flow.map((e, i) => {
    const isActive = (i === activeIdx) && !['COMPLETED', 'FAILED', 'ABORTED', 'TIMED_OUT'].includes(wf.state || '');
    let label = '', meta = '', detail = '', cls = '';
    if (e.kind === 'phase') { label = esc(e.phase || 'step'); cls = isActive ? 'active' : ''; }
    else if (e.kind === 'artifact') {
      label = 'Artifact stored';
      meta = e.size_bytes != null ? `${e.size_bytes.toLocaleString()} bytes` : '';
      detail = esc(e.artifact_id || e.name || '');
    }
    else if (e.kind === 'question') {
      label = 'Asked user';
      cls = 'question';
      detail = esc(e.question || '');
    }
    else if (e.kind === 'answer') { label = 'Answer received'; cls = 'answer'; detail = esc(e.answer || ''); }
    else if (e.kind === 'state') { label = esc(e.state || 'state'); meta = e.reason ? esc(e.reason) : ''; cls = 'state'; }
    else if (e.kind === 'error') { label = 'Error'; cls = 'error'; detail = esc(e.message || ''); }
    else if (e.kind === 'summariser') {
      label = 'Summariser LLM';
      const ti = e.tokens_in || 0, to = e.tokens_out || 0;
      meta = `${ti.toLocaleString()} in / ${to.toLocaleString()} out`;
      detail = e.model ? esc(e.model) : '';
    }
    else { label = esc(e.kind || ''); }
    const nextTs = i + 1 < flow.length ? flow[i + 1].ts : (wf.state === 'RUNNING' ? Date.now() / 1000 : e.ts);
    const stepDur = (nextTs && e.ts) ? (nextTs - e.ts) : 0;
    const stepDurStr = stepDur > 0.1 ? `${stepDur.toFixed(1)}s` : '';
    return `<li class="worker-flow-step ${cls}">
      <span class="worker-flow-step-label">${label}</span>
      ${meta ? `<span class="worker-flow-step-meta">${meta}</span>` : ''}
      ${stepDurStr ? `<span class="worker-flow-step-meta">${stepDurStr}</span>` : ''}
      ${detail ? `<div class="worker-flow-step-detail">${detail}</div>` : ''}
    </li>`;
  }).join('');
  const artifacts = (wf.artifacts || []).map(a =>
    `<span class="worker-flow-artifact" title="${esc(a.path || '')}">${esc(a.artifact_id || a.name || 'artifact')}${a.size_bytes ? ' · ' + a.size_bytes.toLocaleString() + 'b' : ''}</span>`
  ).join('');
  return `<div class="worker-flow">
    ${header}
    ${steps ? `<ul class="worker-flow-timeline">${steps}</ul>` : '<div style="font-size:11px;color:var(--text-400)">No steps yet.</div>'}
    ${artifacts ? `<div class="worker-flow-artifacts">${artifacts}</div>` : ''}
  </div>`;
}

// --- Tool result rendering helpers (syntax highlight + expand + copy) ---
const TOOL_RESULT_INITIAL_CHARS = 8000;
const TOOL_RESULT_MAX_RENDER = 200000;
const _toolResultStore = new Map(); // id -> { full, lang, terminal }
let _toolResultSeq = 0;

// Per-tool default language hint for highlight.js. 'shell' = terminal styling.
const TOOL_LANG_HINTS = {
  execute_command: 'shell',
  python_exec: 'shell',
  read_file: null,        // inferred from args.path extension
  read_document: null,
  edit_file: null,
  write_file: null,
  search_files: 'shell',
  list_directory: 'shell',
  git_command: 'shell',
  exa_search: 'json',
  web_fetch: null,
  context_search: 'json',
  context_detail: 'json',
  context_recall: 'json',
  mempalace_query: 'json',
  mempalace_get_drawer: 'json',
  mempalace_list_drawers: 'json',
  mempalace_kg_query: 'json',
  mempalace_kg_search: 'json',
  mempalace_kg_neighbors: 'json',
  schedule_list: 'json',
  schedule_history: 'json',
  task_status: 'json',
  code_graph_query: 'json',
  list_nodes: 'json',
};

const EXT_TO_LANG = {
  py: 'python', js: 'javascript', ts: 'typescript', tsx: 'tsx', jsx: 'jsx',
  go: 'go', rs: 'rust', java: 'java', c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp',
  cs: 'csharp', rb: 'ruby', php: 'php', swift: 'swift', kt: 'kotlin',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml', ini: 'ini',
  xml: 'xml', html: 'xml', css: 'css', scss: 'scss',
  md: 'markdown', sql: 'sql', dockerfile: 'dockerfile',
};

function detectToolResultLang(toolName, args, body) {
  const hint = TOOL_LANG_HINTS[toolName];
  if (hint !== undefined && hint !== null) return hint;
  // Filename-based inference
  const path = (args && (args.path || args.file_path || args.file)) || '';
  if (path) {
    const m = String(path).toLowerCase().match(/\.([a-z0-9]+)$/);
    if (m && EXT_TO_LANG[m[1]]) return EXT_TO_LANG[m[1]];
    if (/dockerfile$/i.test(path)) return 'dockerfile';
  }
  // Body-shape inference
  const head = (body || '').slice(0, 200).trim();
  if (head.startsWith('{') || head.startsWith('[')) {
    try { JSON.parse(body); return 'json'; } catch(e) {}
  }
  return null; // plaintext
}

function highlightToolResult(text, lang) {
  if (!text || typeof hljs === 'undefined') return esc(text || '');
  if (!lang || lang === 'plaintext') return esc(text);
  try {
    if (lang === 'shell') {
      // Shell output isn't a language per se; use bash for the few highlight cues
      // (paths, quoted strings) without forcing structure on free-form stdout.
      return hljs.highlight(text, { language: 'bash', ignoreIllegals: true }).value;
    }
    if (hljs.getLanguage(lang)) {
      return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
    }
  } catch(e) {}
  return esc(text);
}

function buildToolResultBlock(toolName, args, resultStr) {
  if (!resultStr) return '';
  const fullLen = resultStr.length;
  const lang = detectToolResultLang(toolName, args, resultStr);
  const terminal = lang === 'shell';
  const id = `tres-${++_toolResultSeq}`;
  // Cap actual rendering at MAX so a 5MB blob doesn't lock the browser; copy
  // still gets the full string.
  const renderable = fullLen > TOOL_RESULT_MAX_RENDER
    ? resultStr.substring(0, TOOL_RESULT_MAX_RENDER)
    : resultStr;
  const truncatedAtRender = fullLen > TOOL_RESULT_MAX_RENDER;
  const truncatedInitial = renderable.length > TOOL_RESULT_INITIAL_CHARS;
  const initial = truncatedInitial
    ? renderable.substring(0, TOOL_RESULT_INITIAL_CHARS)
    : renderable;
  _toolResultStore.set(id, { full: renderable, lang, terminal, fullLen, truncatedAtRender });
  const langBadge = lang ? `<span class="tool-result-lang">${esc(lang)}</span>` : '';
  const sizeBadge = `<span class="tool-result-lang">${formatBytes(fullLen)}</span>`;
  const expandLabel = truncatedInitial ? 'Show full' : 'Expand';
  const expandBtn = `<button type="button" class="tool-result-btn" data-tres-expand="${id}" onclick="event.stopPropagation(); expandToolResult('${id}', this)">${expandLabel}</button>`;
  const copyBtn = `<button type="button" class="tool-result-btn" onclick="event.stopPropagation(); copyToolResult('${id}', this)">Copy</button>`;
  const highlighted = highlightToolResult(initial, lang);
  const langCls = lang ? ` language-${lang}` : '';
  const termCls = terminal ? ' terminal' : '';
  const truncNote = truncatedAtRender
    ? `<div class="tool-result-truncated-note">Output exceeds ${formatBytes(TOOL_RESULT_MAX_RENDER)}; rendering capped. Copy still returns the rendered slice.</div>`
    : '';
  return `<div class="tool-result-section">
    <div class="tool-result-header">
      <span class="tool-result-label">Response</span>
      ${langBadge}
      ${sizeBadge}
      <span class="tool-result-actions">${expandBtn}${copyBtn}</span>
    </div>
    <pre class="tool-result-pre${termCls}" data-tres-id="${id}"><code class="hljs${langCls}">${highlighted}</code></pre>
    ${truncNote}
  </div>`;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function expandToolResult(id, btn) {
  const entry = _toolResultStore.get(id);
  if (!entry) return;
  const pre = document.querySelector(`pre[data-tres-id="${id}"]`);
  if (!pre) return;
  const code = pre.querySelector('code');
  if (code) code.innerHTML = highlightToolResult(entry.full, entry.lang);
  pre.classList.add('expanded');
  if (btn) btn.remove();
}

function copyToolResult(id, btn) {
  const entry = _toolResultStore.get(id);
  if (!entry) return;
  const text = entry.full;
  const done = () => {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, cb) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed'; ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); cb && cb(); } catch(e) {}
  document.body.removeChild(ta);
}

function renderToolCall(msg, idx) {
  if (!state.showToolCalls) return '';
  // Look ahead for matching tool_result
  const chat = state.activeChat;
  let resultMsg = null;
  if (chat) {
    for (let j = idx + 1; j < chat.messages.length; j++) {
      const next = chat.messages[j];
      if (next.role === 'tool_result' && next.name === msg.name) { resultMsg = next; break; }
      if (next.role === 'tool_call' || next.role === 'assistant' || next.role === 'user') break;
    }
  }
  const desc = toolDescribe(msg.name, msg.args);
  const args = typeof msg.args === 'string' ? {} : (msg.args || {});
  const hasResult = resultMsg !== null;
  const duration = (hasResult && msg._ts && resultMsg._ts) ? ((resultMsg._ts - msg._ts) / 1000).toFixed(1) : null;
  // Check if this tool is running through a worker (live or completed)
  const isRunningWorker = !hasResult && msg._ts && Object.values(state.activeWorkers).some(
    w => w.tool_name === msg.name && w.state === 'RUNNING'
  );
  const liveElapsed = isRunningWorker ? ((Date.now() - msg._ts) / 1000).toFixed(0) : null;
  const icon = hasResult ? `<span class="tool-icon" style="color:var(--success)">&#10003;</span>` : `<span class="tool-icon tool-icon-spin">&#9881;</span>`;
  const timing = duration ? `<span class="tool-timing">${duration}s</span>` : (liveElapsed ? `<span class="tool-timing">${liveElapsed}s</span>` : '');

  const displayArgs = msg.name === 'python_exec' ? Object.fromEntries(Object.entries(args).filter(([k]) => k !== 'code')) : args;
  let bodyHtml = renderToolArgsTable(displayArgs);
  let isWorker = false;
  let resultStrForFlow = null;
  if (hasResult) {
    const resultStr = typeof resultMsg.result === 'string' ? resultMsg.result : JSON.stringify(resultMsg.result, null, 2);
    resultStrForFlow = resultStr;
    // Permissive check mirrors session inspector: handles stringified envelopes,
    // objects with nested `worker: true`, and survives truncation.
    if (resultStr.includes('"worker": true') || resultStr.includes('"worker":true')) {
      isWorker = true;
    } else {
      try { const rj = JSON.parse(resultStr); if (rj && rj.worker) isWorker = true; } catch(e) {}
    }
    bodyHtml += buildToolResultBlock(msg.name, args, resultStr);
  }
  // Worker flow: shown when the tool ran (or is running) via a worker
  if (isWorker || isRunningWorker) {
    const wf = findWorkerFlow(msg.name, resultStrForFlow);
    if (wf) bodyHtml = renderWorkerFlow(wf) + bodyHtml;
  }
  const workerBadge = (isWorker || isRunningWorker) ? '<span class="tool-badge-worker" title="Executed via worker subagent">WORKER</span>' : '';

  return `
    <div class="tool-block${hasResult ? ' has-result' : ''}" onclick="this.classList.toggle('open')">
      <div class="tool-block-header">
        ${icon}
        <span class="tool-name">${desc}</span>
        ${workerBadge}
        ${timing}
        <span class="tool-chevron">&#9656;</span>
      </div>
      <div class="tool-block-body">${bodyHtml}</div>
    </div>
  `;
}

function renderToolResult(msg, idx) {
  // Tool results are now rendered inside their tool_call block
  // Only render standalone if no preceding tool_call found
  if (!state.showToolCalls) return '';
  const chat = state.activeChat;
  if (chat) {
    for (let j = idx - 1; j >= 0; j--) {
      const prev = chat.messages[j];
      if (prev.role === 'tool_call' && prev.name === msg.name) return ''; // already rendered inside tool_call
      if (prev.role === 'assistant' || prev.role === 'user') break;
    }
  }
  // Standalone result (no matching call found)
  const resultStr = typeof msg.result === 'string' ? msg.result : JSON.stringify(msg.result, null, 2);
  const desc = toolDescribe(msg.name, {});
  const block = buildToolResultBlock(msg.name, {}, resultStr);
  return `
    <div class="tool-block has-result" onclick="this.classList.toggle('open')">
      <div class="tool-block-header">
        <span class="tool-icon" style="color:var(--success)">&#10003;</span>
        <span class="tool-name">${desc}</span>
        <span class="tool-chevron">&#9656;</span>
      </div>
      <div class="tool-block-body">${block}</div>
    </div>
  `;
}

function renderStreamingMessage(chat) {
  const container = document.getElementById('messages-container');
  // Remove previous streaming element if any
  const existing = container.querySelector('.msg-streaming');
  if (existing) existing.remove();

  // Inject inside the last turn-body if present so the streaming bubble belongs
  // to the active turn (same collapse behaviour as the rest of the turn).
  const turnBodies = container.querySelectorAll('.turn-group .turn-body');
  const injectTarget = turnBodies.length ? turnBodies[turnBodies.length - 1] : container;

  // Queue-wait banner: show before any tokens arrive if the provider queued us.
  const q = chat.queueStatus;
  if (q && q.state === 'waiting' && !chat.thinkingText && !chat.streamingText) {
    const posTxt = q.position > 1
      ? `position ${q.position} of ${q.waiting}`
      : 'next up';
    const provTxt = q.provider ? ` on <code>${esc(q.provider)}</code>` : '';
    const activeTxt = q.active ? ` · ${q.active}/${q.max_concurrent || '?'} in flight` : '';
    const waitSec = Math.max(0, Math.round((q.wait_ms || 0) / 1000));
    const html = `<div class="msg-turn msg-turn-assistant msg-streaming">
      <div class="msg-assistant msg-content" style="color:var(--text-muted);font-style:italic">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-2px;margin-right:4px">
          <circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>
        </svg>
        Waiting in queue${provTxt} — ${posTxt}${activeTxt} · ${waitSec}s
      </div>
    </div>`;
    injectTarget.insertAdjacentHTML('beforeend', html);
    return;
  }

  // Nothing to show yet — don't emit an empty wrapper.
  if (!chat.thinkingText && !chat.streamingText) return;

  let html = '<div class="msg-turn msg-turn-assistant msg-streaming">';

  // Show the thinking panel during streaming. Default collapsed — header shows
  // "Thinking..." progress, click to peek at the chain-of-thought as it arrives.
  if (chat.thinkingText) {
    html += `
      <div class="thinking-block" onclick="this.classList.toggle('open')">
        <div class="thinking-block-header">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/></svg>
          Denke nach...
        </div>
        <div class="thinking-block-body msg-content">${renderMarkdown(chat.thinkingText)}</div>
      </div>
    `;
  }

  if (chat.streamingText) {
    html += `<div class="msg-assistant msg-content">${renderMarkdown(chat.streamingText)}</div>`;
  }

  html += '</div>';

  injectTarget.insertAdjacentHTML('beforeend', html);

  // Highlight code in streaming content
  container.querySelectorAll('.msg-streaming pre code').forEach(block => {
    try { hljs.highlightElement(block); } catch(e) {}
  });
}

function renderMarkdown(text) {
  if (!text) return '';
  try {
    // Configure marked
    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: false,
      mangle: false,
    });

    // Citation extraction MUST happen BEFORE marked.parse, because the model
    // often emits the verbatim quote inside markdown italics (`*"..."*`),
    // which marked converts to <em>"..."</em> and then breaks the
    // `[Quelle: ... — *"..."*]` outer-bracket match. Pre-extract the brackets
    // from the raw text, replace each with a non-markdown sentinel, run
    // marked, then swap sentinels for pin buttons.
    const { stripped, citations } = extractCitationsFromRaw(text);

    let html = marked.parse(stripped);

    // Add copy buttons to code blocks
    html = html.replace(/<pre><code( class="language-(\w+)")?>/g, (match, cls, lang) => {
      const langLabel = lang || 'code';
      return `<div class="code-block-header"><span>${esc(langLabel)}</span><button class="code-copy-btn" onclick="copyCodeBlock(this)">Copy</button></div><pre><code${cls || ''}>`;
    });

    // Restore citations as compact pin buttons (don't disrupt text flow)
    html = restoreCitationPins(html, citations);

    return html;
  } catch(e) {
    return esc(text);
  }
}

// Sentinel markers — single chars unlikely to appear in normal text and
// not touched by marked's tokenizer (it preserves them verbatim).
const CITATION_SENTINEL_OPEN = '⁣CIT⁣';   // U+2063 INVISIBLE SEPARATOR
const CITATION_SENTINEL_CLOSE = '⁣/CIT⁣';

function extractCitationsFromRaw(text) {
  // Strip backtick-wrapping around citation brackets — models sometimes emit
  //   `[Quelle: file — "quote"]`  or  `[file — "quote"]`
  // The backticks cause marked.parse to render them as <code> before we can
  // extract them. Strip the surrounding backticks so the bracket is bare.
  // Use a broad match: backtick + [ ... ] + backtick where the content starts
  // with Quelle:/source: or contains an em-dash followed by a quote char.
  text = text.replace(/`(\[[^\]]*?(?:(?:Quelle|QUELLE|source|Source|SOURCE):|[—–][^"]*[„""])[^\]]*?\])`/g, '$1');

  // Pull citation brackets up onto the previous line so the pin renders
  // INLINE at the end of the claim sentence, not as its own paragraph.
  // The model often emits the bracket on its own line:
  //
  //   "Bewahrung der Schutzziele:\n[Quelle: ... — \"...\"]"
  //
  // marked would render this as two stacked paragraphs (or an unwanted
  // soft-break with breaks:true). We lift the bracket onto the prior line
  // with a single space separator. We do this for ALL standalone bracket
  // lines — the bracket is a citation marker for the claim above it, so
  // joining is always the right behavior.
  // Citation bracket pattern — two accepted forms:
  //   [Quelle: file — "quote"]   (preferred, with Quelle:/source: prefix)
  //   [file.pdf — "quote"]       (no-prefix form some models emit)
  // The no-prefix form requires the em-dash + quote to avoid matching
  // arbitrary markdown links like [text](url).
  const _BRACKET_PAT = '(?:(?:Quelle|QUELLE|source|Source|SOURCE):\\s*(?:[^\\[\\]]|\\[\\.{2,3}\\])+?|[^\\[\\]\\n]+?\\s*[—–]\\s*[„""«][^„"""»\\]]+[„"""»][^\\[\\]]*?)';
  text = text.replace(new RegExp('([^\\n])[ \\t]*\\n[ \\t]*(\\[' + _BRACKET_PAT + '\\])', 'g'), '$1 $2');
  // Bracket regex for extraction
  const re = new RegExp('\\[(' + _BRACKET_PAT + ')\\]', 'g');
  const citations = [];
  const stripped = text.replace(re, (_full, body) => {
    const parsed = parseCitationBodyRaw(body);
    if (!parsed) return _full; // leave malformed citations alone
    const id = citations.length;
    citations.push(parsed);
    return CITATION_SENTINEL_OPEN + id + CITATION_SENTINEL_CLOSE;
  });
  return { stripped, citations };
}

function parseCitationBodyRaw(body) {
  if (!body) return null;
  let s = body.trim();
  // Strip optional "Quelle: " / "source: " prefix
  s = s.replace(/^(?:Quelle|QUELLE|source|Source|SOURCE):\s*/i, '');
  // Quote can be in straight ", curly “ ”, German „ “, or wrapped in
  // markdown italics (*"..."*). Strip optional surrounding `*` first.
  let quote = '';
  // Try with markdown italic markers first: *"..."*
  let qMatch = s.match(/\*\s*[„"“]([^„"“”]+)[“"”]\s*\*\s*$/);
  if (!qMatch) {
    qMatch = s.match(/[„"“]([^„"“”]+)[“"”]\s*$/);
  }
  if (qMatch) {
    quote = qMatch[1].trim();
    s = s.substring(0, s.length - qMatch[0].length).trim();
    // Strip trailing em/en-dash separator left over from "<file> — <quote>"
    s = s.replace(/\s*[—–-]\s*$/, '').trim();
  }
  // Remaining s = "<filename>" or "<filename> <locator>"
  let file = s;
  let locator = '';
  const locRe = /\s+(Page\s+\S+|Slide\s+\S+|Sheet\s+["“„][^"“”]+["“”]|§\s*\S+.*|Zeile[n]?\s*\d+[\d\s\-–]*)$/;
  const lMatch = s.match(locRe);
  if (lMatch) {
    locator = lMatch[1].trim();
    file = s.substring(0, s.length - lMatch[0].length).trim();
  }
  if (!file) file = body.trim();
  // Drop the trailing .md companion suffix when citing original binary
  file = file.replace(/\.(pdf|docx|pptx|xlsx|xlsm|eml|msg)\.md$/i, '.$1');
  return { file, locator, quote };
}

function restoreCitationPins(html, citations) {
  if (!citations.length) return html;
  // Sentinels survived marked.parse intact (they're invisible chars). Replace
  // every occurrence with a pin button. Each pin carries its citation data
  // as an attribute so the popover handler can read it.
  const re = new RegExp(CITATION_SENTINEL_OPEN + '(\\d+)' + CITATION_SENTINEL_CLOSE, 'g');
  return html.replace(re, (_match, idStr) => {
    const c = citations[parseInt(idStr, 10)];
    if (!c) return '';
    return renderCitationPin(c);
  });
}

function renderCitationPin({ file, locator, quote }) {
  // Compact "book" icon — Lucide-style open book. Doesn't break text flow.
  const data = encodeURIComponent(JSON.stringify({ file, locator, quote }));
  const tip = quote ? `${file}${locator ? ' · ' + locator : ''}\n\n"${quote}"` : file;
  const bookSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>`;
  return `<button type="button" class="citation-pin" data-citation="${esc(data)}" title="${esc(tip)}" onclick="openCitationPopover(this, event)" aria-label="Citation: ${esc(file)}">${bookSvg}</button>`;
}

// Click handler: open a popover anchored to the pin showing file + locator + quote.
let _activeCitationPopover = null;
function closeCitationPopover() {
  if (_activeCitationPopover) {
    _activeCitationPopover.remove();
    _activeCitationPopover = null;
  }
  document.removeEventListener('click', _citationPopoverOutsideHandler, true);
  document.removeEventListener('keydown', _citationPopoverEscHandler);
}
function _citationPopoverOutsideHandler(e) {
  if (_activeCitationPopover && !_activeCitationPopover.contains(e.target) && !e.target.closest('.citation-pin')) {
    closeCitationPopover();
  }
}
function _citationPopoverEscHandler(e) {
  if (e.key === 'Escape') closeCitationPopover();
}
function openCitationPopover(pinBtn, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  closeCitationPopover();
  let data = {};
  try { data = JSON.parse(decodeURIComponent(pinBtn.getAttribute('data-citation') || '')); } catch (e) {}
  if (!data.file && !data.quote) return;

  const pop = document.createElement('div');
  pop.className = 'citation-popover';
  const fileIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
  const fileLine = `<div class="citation-popover-file">${fileIcon}<span>${esc(data.file || '')}</span></div>`;
  const locLine = data.locator ? `<div class="citation-popover-locator">${esc(data.locator)}</div>` : '';
  const quoteLine = data.quote ? `<div class="citation-popover-quote">${esc(data.quote)}</div>` : '';
  pop.innerHTML = `<div class="citation-popover-arrow"></div>${fileLine}${locLine}${quoteLine}`;
  document.body.appendChild(pop);

  // Position: prefer below pin, flip above if not enough room
  const pinRect = pinBtn.getBoundingClientRect();
  const popRect = pop.getBoundingClientRect();
  const vh = window.innerHeight;
  const vw = window.innerWidth;
  let top = pinRect.bottom + 10;
  let arrowSide = 'top';
  if (top + popRect.height + 12 > vh && pinRect.top - popRect.height - 12 > 0) {
    top = pinRect.top - popRect.height - 10;
    arrowSide = 'bottom';
  }
  let left = Math.max(8, Math.min(vw - popRect.width - 8, pinRect.left + pinRect.width / 2 - popRect.width / 2));
  pop.style.top = top + 'px';
  pop.style.left = left + 'px';
  // Position arrow to point at the pin
  const arrow = pop.querySelector('.citation-popover-arrow');
  if (arrow) {
    const arrowLeft = Math.max(8, Math.min(popRect.width - 18, pinRect.left + pinRect.width / 2 - left - 5));
    arrow.style.left = arrowLeft + 'px';
    if (arrowSide === 'bottom') {
      arrow.style.top = '';
      arrow.style.bottom = '-6px';
      arrow.style.transform = 'rotate(225deg)';
    }
  }

  _activeCitationPopover = pop;
  // Defer outside-click handler to next tick so the same click that opened us
  // doesn't immediately close it.
  setTimeout(() => {
    document.addEventListener('click', _citationPopoverOutsideHandler, true);
    document.addEventListener('keydown', _citationPopoverEscHandler);
  }, 0);
}

// LEGACY (no longer called) — replaced by extractCitationsFromRaw +
// restoreCitationPins which run BEFORE marked.parse so markdown italics
// (*"..."*) inside the verbatim quote don't break the bracket match.
// Kept for reference / fallback only.
function renderCitationBadgesInHtml(html) {
  if (!html || html.indexOf('[') === -1) return html;
  // Tokenize: walk through characters, switching between "text" and "tag"
  // mode. Inside <pre>/<code>/<a> blocks we also stay in passthrough mode.
  const out = [];
  const skipBlocks = ['pre', 'code', 'a'];
  let i = 0;
  let skipDepth = 0; // > 0 when inside one of skipBlocks
  while (i < html.length) {
    if (html[i] === '<') {
      // Take a full tag through the next '>'
      const end = html.indexOf('>', i);
      if (end === -1) { out.push(html.substring(i)); break; }
      const tag = html.substring(i, end + 1);
      out.push(tag);
      // Track entering/leaving skip blocks
      const m = tag.match(/^<\s*(\/?)([a-zA-Z][a-zA-Z0-9]*)/);
      if (m) {
        const isClose = m[1] === '/';
        const name = m[2].toLowerCase();
        const selfClose = tag.endsWith('/>');
        if (skipBlocks.includes(name) && !selfClose) {
          skipDepth += isClose ? -1 : 1;
          if (skipDepth < 0) skipDepth = 0;
        }
      }
      i = end + 1;
      continue;
    }
    // Text run until next '<'
    const next = html.indexOf('<', i);
    const chunk = next === -1 ? html.substring(i) : html.substring(i, next);
    if (skipDepth > 0 || chunk.indexOf('[') === -1) {
      out.push(chunk);
    } else {
      out.push(replaceCitationMarkersInText(chunk));
    }
    if (next === -1) break;
    i = next;
  }
  return out.join('');
}

function replaceCitationMarkersInText(text) {
  // Greedy across the whole bracket content; capture {body}.
  // Bodies can span multiple sentences and contain quotes — we only need to
  // stop at the matching closing bracket. Disallow `[` inside to avoid
  // swallowing too much when nesting goes wrong.
  const re = /\[(?:Quelle|QUELLE|source|Source|SOURCE):\s*((?:[^\[\]]|\[\.{2,3}\])+?)\]/g;
  return text.replace(re, (full, body) => {
    const parsed = parseCitationBody(body);
    if (!parsed) return full;
    return renderCitationBadge(parsed);
  });
}

function parseCitationBody(body) {
  if (!body) return null;
  // marked.parse() runs before us and HTML-escapes " → &quot;, & → &amp;, etc.
  // Decode so quote-detection regexes see the raw characters the model emitted.
  let s = decodeHtmlEntities(body).trim();
  // Quote can be in straight ", curly “ ”, or German „ “
  let quote = '';
  const qMatch = s.match(/[„"“]([^„"“”]+)[“"”]\s*$/);
  if (qMatch) {
    quote = qMatch[1].trim();
    s = s.substring(0, s.length - qMatch[0].length).trim();
    // Strip trailing em/en-dash separator left over from "<file> — <quote>"
    s = s.replace(/\s*[—–-]\s*$/, '').trim();
  }
  // Remaining s = "<filename>" or "<filename> <locator>"
  // Locators we recognise: "Page N", "Slide N", "Sheet \"X\"", "§...".
  let file = s;
  let locator = '';
  const locRe = /\s+(Page\s+\S+|Slide\s+\S+|Sheet\s+["“„][^"“”]+["“”]|§\s*\S+.*)$/;
  const lMatch = s.match(locRe);
  if (lMatch) {
    locator = lMatch[1].trim();
    file = s.substring(0, s.length - lMatch[0].length).trim();
  }
  // If after splitting, file is empty, fall back to original body.
  if (!file) file = decodeHtmlEntities(body).trim();
  return { file, locator, quote };
}

function decodeHtmlEntities(s) {
  if (!s || s.indexOf('&') === -1) return s;
  return s
    .replace(/&quot;/g, '"')
    .replace(/&#34;/g, '"')
    .replace(/&#x22;/gi, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

function renderCitationBadge({ file, locator, quote }) {
  const icon = `<span class="citation-badge-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg></span>`;
  const fileSpan = `<span class="citation-badge-file">${esc(file)}</span>`;
  const locSpan = locator ? `<span class="citation-badge-locator">${esc(locator)}</span>` : '';
  const quoteSpan = quote ? `<span class="citation-badge-quote">${esc(quote)}</span>` : '';
  return `<span class="citation-badge" title="${esc(file)}${locator ? ' · ' + esc(locator) : ''}">${icon}<span class="citation-badge-body">${fileSpan}${locSpan}${quoteSpan}</span></span>`;
}

