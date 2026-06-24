// chat_send.js — send flow + SSE stream callbacks + LCM + stop/answer + streaming UI. Split from chat.js (Tier F Phase 4). Global <script>, no modules.

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
      showToast(`Ausführung ist ${_ws} — bitte auf Abschluss warten.`, true);
      return;
    }
  }

  // Clear any stale suggestion — user is sending something new
  NextPrompt.clear();

  // Ensure agent selected
  if (!state.activeAgentId) {
    if (state.agents.length) selectAgent(state.agents[0].name);
    else { showToast('Keine Agents verfügbar', true); return; }
  }

  // From project-detail: bind to the project's agent, start a fresh chat for
  // this turn (so the project context applies), then drop into the chat view.
  // state.currentProject is already set by openProjectDetail.
  if (state.currentView === 'project-detail') {
    const projAgent = state._projectDetailAgent;
    if (projAgent && projAgent !== state.activeAgentId) {
      selectAgent(projAgent);
    }
    // newChat() resets composer state — including state._pendingFiles /
    // _pendingImages (sessions.js). When the user attaches a file on the
    // PROJECT-LANDING composer and hits send, that wipe runs BEFORE we capture
    // filesToSend below, so the attachment is silently dropped and the LLM
    // never sees it (the turn answers from the project wing only). The composer
    // text survives because it's already read into `text` above; the files are
    // not — so snapshot them across newChat() and restore. (chat-input/welcome
    // composers don't call newChat() here, so they were never affected.)
    const _carryFiles = state._pendingFiles ? [...state._pendingFiles] : [];
    const _carryImages = state._pendingImages ? [...state._pendingImages] : [];
    // Same problem as the files above: the user can pick a dedicated model on
    // the project-landing composer, but newChat() resets chat.model back to the
    // agent default (→ Auto/smart-cloud, which then runs prompt classification).
    // The composer control is read at send time, so the reset wins and the pick
    // is silently dropped. Snapshot the chosen model (+ its Auto bookkeeping)
    // across newChat() and restore it. Only carry an explicit (non-default)
    // pick so a user who never touched the selector still inherits the default.
    const _preChat = state.ensureAgentChat(state.activeAgentId);
    const _agentDefault = state.defaultModelForAgent(state.activeAgentId);
    const _pickedModel = _preChat && _preChat.model;
    const _carryModel = (_pickedModel && _pickedModel !== _agentDefault)
      ? { model: _pickedModel, autoPicked: _preChat.autoPicked, autoReason: _preChat.autoReason }
      : null;
    // Same problem again for the thinking-level and caveman composer toggles:
    // the user can set them on the project-landing composer, but newChat()
    // resets both to their defaults (sessions.js) before the turn is built, so
    // the picks are silently dropped (the model was already special-cased above;
    // these two were missed). Snapshot and restore them across newChat().
    const _carryThinking = _preChat ? _preChat.thinkingLevel : undefined;
    const _carryCaveman = _preChat ? _preChat.cavemanMode : undefined;
    newChat();
    state._pendingFiles = _carryFiles;
    state._pendingImages = _carryImages;
    if (_carryModel) {
      const _freshChat = state.ensureAgentChat(state.activeAgentId);
      _freshChat.model = _carryModel.model;
      _freshChat.autoPicked = _carryModel.autoPicked;
      _freshChat.autoReason = _carryModel.autoReason;
      if (typeof updateModelSelectorDisplay === 'function') updateModelSelectorDisplay(_freshChat.model);
    }
    {
      const _freshChat = state.ensureAgentChat(state.activeAgentId);
      if (_carryThinking !== undefined) _freshChat.thinkingLevel = _carryThinking;
      if (_carryCaveman !== undefined) _freshChat.cavemanMode = _carryCaveman;
      if (typeof refreshThinkingButton === 'function') refreshThinkingButton();
      if (typeof updateStatusBar === 'function') updateStatusBar();
    }
    if (typeof renderFilePreviews === 'function') renderFilePreviews();
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

  // GDPR/PII pre-submit check. When the scanner flags any personal data in the
  // outgoing message or its text attachments, an action modal pops up listing
  // exactly which fragments tripped which detector and asks how to proceed.
  // Skipped entirely when the feature is disabled or suppressed for this chat.
  //
  // `gdprAction` is the body field forwarded to POST /v1/chat. The chat
  // worker decides what to do with it:
  //   'anonymise'  → server pseudonymises text + (later: files) and
  //                  de-anonymises the reply before persistence.
  //   'local_model' → server swaps the active model to the configured local
  //                   fallback for this turn. No anonymisation needed.
  //   'continue'   → user accepted warn-level findings; cloud send as-is.
  //   ''           → no scanner findings or feature disabled.
  // Block sending while any attachment upload-scan is still in flight, or
  // returned a "we couldn't scan it" reason. The user accepted these
  // outcomes as blocking (option 2b spec): unscannable content has unknown
  // PII state, so we never ship it. Images / audio / video / archives are
  // accepted gaps — they return reason in {media, archive}, which is NOT
  // blocking. Server-side scanner failures (timeout, too_large,
  // unsupported, extract_failed) ARE blocking.
  // A scan that TIMED OUT or FAILED is a coverage GAP, not a reason to forbid
  // sending — the user can remove the attachment any time, and a slow/failed
  // scan shouldn't hard-block them (same philosophy as archive/media, which are
  // already accepted non-blocking gaps). Only structural rejections
  // (unsupported format, over the size cap) stay blocking.
  const BLOCKING_REASONS = new Set([
    'unsupported', 'too_large',
  ]);
  const pendingScan = (state._pendingFiles || []).find(
    f => f.scan && f.scan.state === 'pending');
  if (pendingScan) {
    showToast('Wird gescannt: ' + pendingScan.name + ' — kurz warten …', true);
    return;
  }
  const blockingFile = (state._pendingFiles || []).find(
    f => f.scan && f.scan.scanned === false && BLOCKING_REASONS.has(f.scan.reason));
  if (blockingFile) {
    const reasonLabel = {
      'too_large': 'zu groß (>50 MB)',
      'extract_timeout': 'Scan-Zeitüberschreitung (>30 s)',
      'extract_failed': 'Scan fehlgeschlagen',
      'unsupported': 'nicht unterstütztes Format',
    }[blockingFile.scan.reason] || blockingFile.scan.reason;
    showToast(`Senden nicht möglich: ${blockingFile.name} — ${reasonLabel}. Zum Fortfahren entfernen.`, true);
    return;
  }

  let gdprAction = '';
  // Tracks whether the unified PII+classification modal has already
  // resolved this turn. Used by the classification fallback gate below
  // to avoid a second modal when the user just chose in the unified one.
  let unifiedModalRan = false;
  // Per-turn "redo as <mode>" override (set by redoTurnAsGdprMode in
  // chat_render.js). The user already saw the previous turn's GDPR outcome
  // and explicitly chose a different mode — honour it directly, skipping the
  // scan + modal. One-shot: consumed here so the next normal send re-scans.
  const _gdprOverride = (state._gdprActionOverride || '').trim();
  state._gdprActionOverride = '';
  if (_gdprOverride && ['anonymise', 'local_model', 'continue'].includes(_gdprOverride)) {
    gdprAction = _gdprOverride;
    // Persist as the sticky session pref so subsequent turns keep the chosen
    // mode (matches the modal's own consent-gate behaviour).
    chat.gdprActionPref = _gdprOverride;
    if (_gdprOverride === 'anonymise') chat.hasGdprMapping = true;
    if (chat.sessionId) API.updateGdprActionPref(chat.sessionId, _gdprOverride).catch(() => {});
  } else if (state.piiScannerEnabled !== false) {
    // 9.197.0: NO sticky short-circuit. The dialog fires iff there are NEW
    // (not-yet-seen) PII findings. Findings already shown to the user in a prior
    // turn are tagged _seen and displayed FIXED (not re-ratable); only NEW ones
    // are ratable. When there are no NEW findings at all, we skip the dialog and
    // apply the prior decision (FP values stay clear, the rest follows the last
    // chosen action). "Seen" = the (rule|value) was shown before (chat._piiDecisions).
    {
      // SERVER-ONLY scan (9.200.0 — the browser-side detector was removed).
      // Detection runs entirely on the server: the typed text via the
      // scan-text endpoint (regex + spaCy NER + confidence/band/disposition),
      // and attachments via their upload-time /v1/attachments/scan result
      // (f.scan.findings_full). We just ASSEMBLE those into the {findings,
      // bySource, worstAction, worstDisposition} shape the modal expects.
      //
      // The typed-text scan adds a round-trip, so it runs behind a cancellable
      // progress indicator (gdprScanProgress) — a slow NER pass / large
      // attachment shouldn't freeze the composer with no feedback or escape.
      const scan = { findings: [], bySource: {}, worstAction: 'ignore',
                     worstDisposition: 'ignore' };
      const _pushSrc = (src, arr) => {
        if (!arr || !arr.length) return;
        scan.bySource[src] = arr;
        for (const f of arr) {
          scan.findings.push(f);
          const a = f.action || 'warn';
          if (a === 'block') scan.worstAction = 'block';
          else if (a === 'warn' && scan.worstAction !== 'block') scan.worstAction = 'warn';
        }
      };
      // Attachments: reuse the per-finding records the server produced at
      // upload time (findings_full). No client-side file scan.
      for (const f of (state._pendingFiles || [])) {
        const ff = (f.scan && f.scan.scanned && Array.isArray(f.scan.findings_full))
          ? f.scan.findings_full : null;
        if (!ff || !ff.length) continue;
        const src = 'file:' + f.name;
        _pushSrc(src, ff.map(g => ({
          rule_id: g.rule_id, label: g.label || gdprRuleLabel(g.rule_id),
          category: g.category || gdprRuleCategory(g.rule_id),
          action: g.action || 'warn',
          confidence: g.confidence, band: g.band, disposition: g.disposition,
          value: g.value, _source: src,
        })));
      }
      // Typed text: server scan-text (the only detector for the message).
      // Cancellable — a cancel aborts the whole send.
      if (text && text.trim() && state.piiScannerEnabled !== false) {
        let srv;
        try {
          srv = await runCancellableGdprScan(text);
        } catch (e) {
          if (e && e._cancelled) return;   // user cancelled the scan → abort send
          // Scan failed/unreachable — fail-open (send proceeds without the
          // typed-text findings, same as before this feature existed).
          console.warn('[gdpr-scan] server scan failed:', e?.message || e);
          srv = null;
        }
        const srvFindings = Array.isArray(srv?.findings) ? srv.findings : [];
        if (srvFindings.length) {
          _pushSrc('message', srvFindings.map(f => ({
            rule_id: f.rule_id, label: f.label || gdprRuleLabel(f.rule_id),
            category: f.category || gdprRuleCategory(f.rule_id),
            action: f.action || 'warn',
            confidence: f.confidence, band: f.band, disposition: f.disposition,
            value: f.value, _source: 'message',
          })));
          if (srv.worst_disposition) scan.worstDisposition = srv.worst_disposition;
        }
      }

      // Follow-up-turn coverage: if a prior turn's persisted assistant
      // reply (or older user message) already carries PII, the LLM may
      // re-read disk-resident attachments OR have the PII inline in the
      // history we're about to ship. The outgoing-only scan above misses
      // this because the new typed text + new attachments are clean. Fold
      // a history scan into the modal trigger so the user gets asked
      // 9.197.0: history findings are NO LONGER injected into the dialog. Prior-
      // turn PII is, by definition, already SEEN — the user decided it on the
      // turn it first appeared. It's surfaced via the composer history badge
      // (which now shows the DECISION, not raw values), not re-listed in the
      // dialog. The dialog deals only with this turn's message + attachments,
      // split into seen/new. (Anything genuinely new here is a fresh value.)

      // Collect classified attachments (warn / force_local / block).
      // These compose with PII findings in the unified modal: the same
      // dialog now surfaces both signals and offers the strictest
      // combined verdict set (continue / local / anonymise / cancel).
      const classifiedFiles = (state._pendingFiles || []).filter(f => {
        const c = f.scan && f.scan.classification;
        if (!c || !c.effective_action) return false;
        return c.effective_action === 'warn'
            || c.effective_action === 'force_local'
            || c.effective_action === 'block';
      });
      // Unified seen-tagging: tag EVERY per-finding entry (message + each
      // attachment) by whether its (rule|value) was already decided in this
      // chat. Seen → fixed in the dialog; new → ratable. One pass so message
      // and attachment findings are treated identically.
      const decided = chat._piiDecisions || {};
      for (const f of (scan.findings || [])) {
        if (f.value == null) continue;   // aggregated/legacy entries can't be matched
        const prior = decided[(f.rule_id || '') + '|' + (f.value || '')];
        f._seen = !!prior;
        f._priorFp = !!(prior && prior.false_positive);
      }
      // Are there NEW (unseen) per-finding hits this turn? Only those open the
      // dialog. (Aggregated/legacy findings without a value also count as new.)
      const newFindings = (scan.findings || []).filter(f => !f._seen);
      const seenFindings = (scan.findings || []).filter(f => f._seen);
      if (newFindings.length || classifiedFiles.length) {
        const localActive = isModelLocal(chat.model || '');
        unifiedModalRan = true;
        const { verdict, askAfter, decisions } = await gdprActionModal(scan, chat, localActive, classifiedFiles);
        if (verdict === 'cancel') return;
        // Persist the per-finding decisions (value, confidence, disposition, and
        // FP flags) so this chat doesn't re-ask decided values, FP values skip
        // anonymisation, and the analysis is auditable / feeds global learning.
        // Also cache locally for the in-session 'already analysed' check.
        if (Array.isArray(decisions) && decisions.length && chat.sessionId) {
          chat._piiDecisions = chat._piiDecisions || {};
          for (const d of decisions) {
            // Stamp the turn-level verdict onto each cached decision so the
            // history tooltip + chat marks can tell anonymised from accepted-
            // cleartext (verdict 'send'/'local') without a server round-trip.
            chat._piiDecisions[(d.rule_id || '') + '|' + (d.value || '')] =
              Object.assign({ turn_action: verdict }, d);
          }
          // Persist server-side so the cleartext/anonymised marks survive a
          // reload (the chat marks read these rows back via getPiiDecisions).
          // Do NOT swallow the error silently — a dropped write means an
          // ACCEPTED-in-clear PII value (e.g. the user chose "Trotzdem senden"
          // / continue with the cloud model) never gets coloured on reload,
          // which is exactly the bug reported for the email case. Surface it.
          API.recordPiiDecisions(chat.sessionId, verdict, decisions)
            .catch(e => console.error('[gdpr] recordPiiDecisions failed — '
              + 'accepted PII will not be marked on reload:', e?.message || e));
        } else if (chat.sessionId && (verdict === 'send' || verdict === 'local')) {
          // No ratable rows were collected, yet the user actively ACCEPTED PII
          // in the clear (continue-with-cloud or local-model). This happens when
          // the dialog showed only already-"seen" rows (data-pii-seen="1"),
          // which the cleanup() collector skips. Without a persisted row the
          // accepted value can't be coloured on reload. Re-derive the decisions
          // straight from the scan findings (every value the dialog surfaced)
          // and persist them so the cleartext mark works in this case too.
          const fromScan = (scan.findings || [])
            .filter(f => f && f.value)
            .map(f => ({
              rule_id: f.rule_id || '', value: f.value,
              confidence: f.confidence || 0, band: f.band || '',
              disposition: f.disposition || '', source: f._source || 'message',
              false_positive: !!f._priorFp,
            }));
          if (fromScan.length) {
            chat._piiDecisions = chat._piiDecisions || {};
            for (const d of fromScan) {
              chat._piiDecisions[(d.rule_id || '') + '|' + (d.value || '')] =
                Object.assign({ turn_action: verdict }, d);
            }
            API.recordPiiDecisions(chat.sessionId, verdict, fromScan)
              .catch(e => console.error('[gdpr] recordPiiDecisions (seen-fallback) '
                + 'failed:', e?.message || e));
          }
        }
        // "Frag mich nachher wies gelaufen ist" — opt into the post-turn
        // feedback modal for this session. Persist + cache locally so the
        // done-handler knows to fire it without a round-trip.
        if (askAfter && chat.sessionId) {
          chat.gdprFeedbackAsk = true;
          API.updateGdprFeedbackAsk(chat.sessionId, true).catch(() => {});
        }
        if (verdict === 'local') {
          // Server-side swap: chat worker handles model switching when it sees
          // gdpr_action='local_model'. We forward the choice rather than
          // mutating chat.model client-side so the audit trail (pii_local_swap)
          // is on the server and a missing local model raises a 400.
          gdprAction = 'local_model';
        } else if (verdict === 'anonymise') {
          gdprAction = 'anonymise';
        } else if (verdict === 'send') {
          gdprAction = 'continue';
        }
        // Remember the last action so an only-seen follow-up (no dialog) can
        // reuse it (anonymise / local / continue). NOT sticky-consent — the
        // dialog still re-fires whenever NEW findings appear; this just carries
        // the prior choice for unchanged content. hasGdprMapping stays true so
        // the worker keeps a live mapping for anonymise sessions.
        if (gdprAction && chat.sessionId) {
          chat.gdprActionPref = gdprAction;
          if (gdprAction === 'anonymise') chat.hasGdprMapping = true;
          API.updateGdprActionPref(chat.sessionId, gdprAction).catch(() => {});
        }
      } else if (seenFindings.length) {
        // Only ALREADY-SEEN findings this turn (content unchanged / re-decided
        // values). No dialog — apply the prior decision: FP values stay clear
        // (the server's _filter_pii_false_positives honours them), the rest
        // follows the last chosen action (default anonymise if PII was ever
        // anonymised here, else continue).
        const prior = (chat.gdprActionPref || '').trim();
        if (['anonymise', 'local_model', 'continue'].includes(prior)) {
          gdprAction = prior;
        } else if (chat.hasGdprMapping) {
          gdprAction = 'anonymise';
        }
      }
    }
  }

  // ─── Classification fallback gate (Phase B) ───
  // The unified gdprActionModal above already folds classification into
  // the PII dialog when fired. This block catches the cases where the
  // unified modal was skipped — sticky PII preference, PII scanner
  // disabled, or no PII findings AND classification was effectively
  // ignore. We still need to gate force_local / block from
  // classification, since those are enforcement actions independent of
  // PII consent.
  if (!unifiedModalRan && state._pendingFiles && state._pendingFiles.length) {
    const curLocal = isModelLocal(chat.model || '');
    const blockedOrLocalOnly = state._pendingFiles.filter(f => {
      const c = f.scan && f.scan.classification;
      if (!c || !c.effective_action) return false;
      return c.effective_action === 'block' || c.effective_action === 'force_local';
    });
    if (blockedOrLocalOnly.length && !curLocal) {
      const verdict = await gdprActionModal(
        { findings: [], bySource: {}, worstAction: 'warn' },
        chat,
        curLocal,
        blockedOrLocalOnly,
      );
      if (verdict.verdict === 'cancel') return;
      if (verdict.verdict === 'local') {
        try { piiEnsureLocalModel(); } catch (e) {}
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
  // Reset this turn's live token counters (added on top of the session total in
  // the status bar while streaming; cleared again on done).
  chat._liveTurnTokensIn = 0;
  chat._liveTurnTokensOut = 0;
  chat.files = [];
  chat._msgSeq = chat._msgSeq || 0;
  const streamGen = (chat._streamGen = (chat._streamGen || 0) + 1);
  updateStreamingUI(true, chat);
  // Show the inline status line immediately (before the first SSE event) so the
  // user sees the spinner + model + "Denke nach…" at the start of the in-flight
  // response right after sending — the old top spinner-bar appeared on send too.
  renderStreamingMessage(chat);

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

    await API.streamChat(chat.sessionId, text, buildStreamCallbacks(chat, isActive), chat.model, filesToSend, null, gdprAction);
  } catch(e) {
    _onStreamCatch(e, chat, isActive, streamGen);
  }
  _streamSafetyNet(chat, isActive, streamGen);
}
/** Build the SSE callback map for a chat turn. Shared between the originating
 *  send (API.streamChat) and reconnecting to an in-progress turn after the chat
 *  was reopened (API.attachStream). `streamGen` is snapshotted so a stale
 *  reconnect can't clobber a newer turn. */
function _nextSeq(chat) { chat._msgSeq = (chat._msgSeq || 0) + 1; return chat._msgSeq; }

// Freeze the current live streaming text as a committed 'assistant_segment' row
// at the next _seq, so a following tool call renders AFTER this text (text →
// tool → text chronological flow). Clears chat.streamingText so the next run
// starts a fresh bubble. No-op when nothing has streamed yet. The committed
// segments are display-only (wire-filtered like thinking/tool rows); the turn's
// final assistant message still carries the full joined reply for history.
function _commitStreamingSegment(chat) {
  const t = (chat.streamingText || '').trim();
  if (!t) return;
  chat.messages.push({ role: 'assistant_segment', content: chat.streamingText,
                       _ts: Date.now(), _seq: _nextSeq(chat) });
  chat.streamingText = '';
}
function buildStreamCallbacks(chat, isActive) {
  const streamGen = chat._streamGen;
  return {
      thinking_start: () => {
        // Each round's thinking becomes its own message row at thinking_done,
        // so the live buffer is per-round. Fresh start on every thinking_start.
        // renderMessages() first so any tool_calls from the completed prior round
        // render into the turn-body before the new thinking bubble is appended.
        chat.thinkingText = '';
        if (isActive() && typeof buddyPhase === 'function') buddyPhase('thinking');
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
            _seq: _nextSeq(chat),
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
      // Live per-round usage: THIS turn's cumulative tokens + session cost so far,
      // emitted by the server as each round completes (not only at `done`). Held
      // in dedicated live-turn fields (NOT chat._tokensIn — that's the cross-turn
      // session total the `done` handler accumulates; clobbering it would make the
      // bar drop to just-this-turn while streaming). updateStatusBar adds these to
      // the session total while chat.streaming is true; they're cleared at turn
      // start + on done. Session cost from the server already includes this turn.
      usage: (d) => {
        if (typeof d.tokens_in === 'number') chat._liveTurnTokensIn = d.tokens_in;
        if (typeof d.tokens_out === 'number') chat._liveTurnTokensOut = d.tokens_out;
        if (d.cost !== undefined && d.cost !== null) chat._sessionCost = d.cost;
        // last_tokens_in = the most recent round's prompt size → drives the live
        // context-fill bar (updateStatusBar reads chat._lastApiIn).
        if (typeof d.last_tokens_in === 'number') chat._lastApiIn = d.last_tokens_in;
        if (isActive() && typeof updateStatusBar === 'function') updateStatusBar();
        if (isActive() && typeof renderStreamingMessage === 'function') renderStreamingMessage(chat);
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
        if (isActive() && typeof buddyPhase === 'function') buddyPhase('writing');
        if (isActive()) {
          // Real text is flowing → clear any prior nudge label so it doesn't
          // linger when the model finally answers.
          if ((chat._streamLabel || '').startsWith('Modell wird neu angestoßen')) {
            setStreamStatus(chat, 'label', '');
          }
          renderStreamingMessage(chat); scrollToBottom();
        }
      },
      tool_call: (d) => {
        if (isActive() && typeof buddyPhase === 'function') buddyPhase('tool');
        const last = chat.messages[chat.messages.length - 1];
        const isNewToolCall = !(last && last.role === 'tool_call' && last.tool_use_id && d.tool_use_id && last.tool_use_id === d.tool_use_id);
        if (isNewToolCall) {
          // Chronological interleave: a tool call CLOSES the current answer-text
          // run. Commit whatever streamed so far as a frozen text-segment row at
          // this _seq, then start a fresh streaming bubble after the tool — so
          // the turn renders text → tool → text in the order it happened
          // (instead of all tools shoved above one answer block). Wire-safe:
          // 'assistant_segment' rows are never sent to the model (sidecar_proxy
          // only forwards user/assistant); the final joined reply remains the
          // canonical assistant message for history.
          _commitStreamingSegment(chat);
          chat.messages.push({ role: 'tool_call', name: d.name, args: d.args || {}, tool_use_id: d.tool_use_id || null, tool_round: d.tool_round ?? null, _ts: Date.now(), _seq: _nextSeq(chat) });
          _activityAutoUpdate(chat, currentTurnNum(chat), 'add');
          // Surface the activity pill as soon as the first tool runs (live),
          // and refresh the panel if it's open on the activity tab.
          if (isActive()) {
            if (typeof refreshBackgroundTasksPill === 'function') refreshBackgroundTasksPill();
            if (state.rightPanelOpen && state.rightPanelTab === 'bgtasks'
                && typeof renderBackgroundTasksPane === 'function') renderBackgroundTasksPane();
          }
        } else {
          last.args = d.args;
        }
        // Re-render on every new tool call.
        // With the toggle OFF, renderTurnBody emits no tool cards but still
        // renders the static `Aktivität · N Tools` header (count updates live)
        // — the user expects to see *that a tool ran* even when the details are
        // hidden. With it ON, the expandable activity block + cards appear and
        // auto-update as each call streams in. (An args-only update of an
        // existing call doesn't change the count or cards, so skip it.)
        // renderMessages() wipes the container including the in-flight .msg-streaming div,
        // so re-render the streaming bubble right after. Without this, any partial assistant
        // text/thinking captured so far vanishes until the next text_delta arrives.
        if (isNewToolCall && isActive()) { renderMessages(); renderStreamingMessage(chat); scrollToBottom(); }
      },
      references: (d) => {
        // Server-pushed normalized refs for the just-completed tool call.
        // Mirrors the tool_result path below but fires independently.
        const refs = d.references || [];
        if (!refs.length || !chat.sessionId) return;
        if (!state.chatReferences[chat.sessionId]) state.chatReferences[chat.sessionId] = { cited: [], searched: [] };
        const cache = state.chatReferences[chat.sessionId];
        const allLinks = new Set([...cache.cited.map(r => r.link), ...cache.searched.map(r => r.link)]);
        let added = false;
        for (const ref of refs) {
          if (!allLinks.has(ref.link)) { cache.searched.push(ref); allLinks.add(ref.link); added = true; }
        }
        if (added && isActive()) {
          // References never auto-open the panel — just glow the button.
          // If it's already open on the refs tab, re-render it live.
          if (!state.rightPanelOpen) setRightPanelGlow(true);
          else if (state.rightPanelTab === 'references') renderReferencesPane();
          updateRightPanelBadges();
        }
      },
      tool_result: (d) => {
        // d.references is pre-extracted server-side; attach to the message so
        // extractReferencesFromToolResult reads from it directly (no re-parsing).
        const toolMsg = { role: 'tool_result', name: d.name, result: d.result,
                          tool_use_id: d.tool_use_id || null,
                          references: d.references || undefined, _ts: Date.now(), _seq: _nextSeq(chat) };
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
            // References never auto-open the panel — just glow the button.
            if (!state.rightPanelOpen) setRightPanelGlow(true);
            else if (state.rightPanelTab === 'references') renderReferencesPane();
            updateRightPanelBadges();
          }
        }
        if (!isActive()) return;
        renderMessages();
        renderStreamingMessage(chat);
        scrollToBottom();
      },
      tool_output: (d) => {
        // Live tool output streaming
      },
      // Generic live tool progress (report_tool_progress): phase label + optional
      // % / page-i-of-N for the running tool. Stash the latest on the matching
      // live tool_call row (by tool_use_id; fall back to the most recent running
      // tool) so the tool card can show "Extrahiere mit pymupdf4llm · 32%" etc.
      // Display-only — never persisted (the result + backend badge are durable).
      tool_progress: (d) => {
        const tuid = d.tool_use_id;
        let row = null;
        if (tuid) row = chat.messages.find(m => m.role === 'tool_call' && m.tool_use_id === tuid);
        if (!row) {
          // No id match — attach to the most recent tool_call without a result yet.
          for (let i = chat.messages.length - 1; i >= 0; i--) {
            const m = chat.messages[i];
            if (m.role === 'tool_call') { row = m; break; }
          }
        }
        if (!row) return;
        row._progress = { phase: d.phase || '', pct: (d.pct == null ? null : d.pct),
                          note: d.note || '', current: d.current, total: d.total };
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
      },
      // ── Transparent anonymisation synthetic tool-call rows ──
      // Server emits these to give the user a visible record of "your data
      // was anonymised before sending" / "the reply was de-anonymised". They
      // appear in chat history alongside real tool calls, but are marked
      // synthetic: true so the renderer can style them distinctly.
      synthetic_tool_use: (d) => {
        chat.messages.push({
          role: 'tool_call',
          synthetic: true,
          kind: d.kind,
          name: d.kind,
          args: d.args || {},
          tool_use_id: d.tool_use_id || null,
          _ts: Date.now(),
          _seq: _nextSeq(chat),
        });
        _activityAutoUpdate(chat, currentTurnNum(chat), 'add');
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); scrollToBottom(); }
      },
      synthetic_tool_result: (d) => {
        chat.messages.push({
          role: 'tool_result',
          synthetic: true,
          kind: d.kind,
          name: d.kind,
          result: d.result || {},
          status: d.status || 'ok',
          duration_ms: d.duration_ms || 0,
          tool_use_id: d.tool_use_id || null,
          _ts: Date.now(),
          _seq: _nextSeq(chat),
        });
        // Anonymise-done side-channel: attach real-PII spans onto the
        // most recent user message so the chat view paints them with the
        // same yellow <mark> overlay the assistant reply uses. We walk
        // backwards from the end (skipping the just-pushed synthetic
        // tool_result row) to find the user message that triggered this
        // anonymisation pass.
        const spans = d.kind === 'anonymise' && Array.isArray(d.result?.user_spans)
          ? d.result.user_spans : null;
        if (spans && spans.length) {
          for (let i = chat.messages.length - 1; i >= 0; i--) {
            const m = chat.messages[i];
            if (m && (m.role === 'user' || m.role === 'human')) {
              m.metadata = m.metadata || {};
              m.metadata.gdpr_restored_spans = spans;
              break;
            }
          }
        }
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); scrollToBottom(); }
      },
      gdpr_recovery_required: (d) => {
        // Anonymisation failed mid-flight. Open the recovery modal — the
        // user's choice goes to POST /v1/chat/gdpr-recovery, which unblocks
        // the worker thread (server-side).
        gdprRecoveryModal(d, chat).then((choice) => {
          API.chatGdprRecovery(d.session_id, choice).catch(() => {});
        });
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
        } else if (d.artifact_role === 'output') {
          // Only real output artifacts auto-open the panel — never intermediate
          // / technical artifacts. A user-closed panel stays closed for the
          // session; the button glows instead so the new artifact is noticed.
          if (!state.userClosedRightPanel) openArtifactPanel(d.artifact_id, d.artifact_version);
          else if (!state.rightPanelOpen) setRightPanelGlow(true);
        } else if (!state.rightPanelOpen) {
          // Intermediate / technical artifacts never auto-open, but new panel
          // data should still glow the button.
          setRightPanelGlow(true);
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
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
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
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.finished': (d) => {
        console.log('[SSE] worker.finished:', d.tool_name, d.duration_seconds + 's');
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = d.state || 'COMPLETED';
        const wf = state.workerFlows[d.worker_id];
        if (wf) { wf.state = d.state || 'COMPLETED'; wf.duration = d.duration_seconds; }
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.paused': (d) => {
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = 'PAUSED';
        const wf = state.workerFlows[d.worker_id]; if (wf) wf.state = 'PAUSED';
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.resumed': (d) => {
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = 'RUNNING';
        const wf = state.workerFlows[d.worker_id]; if (wf) wf.state = 'RUNNING';
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
      },
      'worker.aborted': (d) => {
        if (!d.worker_id) return;
        const w = state.activeWorkers[d.worker_id]; if (w) w.state = 'ABORTED';
        const wf = state.workerFlows[d.worker_id]; if (wf) wf.state = 'ABORTED';
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
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
        if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
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
          if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
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
            <span>Worker <code>${esc(d.worker_id)}</code> benötigt Ihre Eingabe</span>
          </div>
          <div class="wq-body">
            ${d.context_summary ? `<div class="wq-context">${esc(d.context_summary)}</div>` : ''}
            <div class="wq-question">${esc(d.question)}</div>
            ${optionsHtml ? `<div class="wq-options">${optionsHtml}</div>` : ''}
            <textarea class="wq-freeform" placeholder="Antwort eingeben..." style="width:100%;min-height:40px;margin-bottom:8px;border:1px solid var(--border-100);border-radius:6px;padding:6px 8px;font-size:13px;background:var(--bg-000);color:var(--text-200);resize:vertical;display:${d.options ? 'none' : 'block'}"></textarea>
            <div class="wq-actions">
              <button class="wq-btn-answer" onclick="answerWorkerQuestion('${esc(d.worker_id)}')">Antworten</button>
              <button onclick="answerWorkerQuestion('${esc(d.worker_id)}', '__delegate__')">Agent entscheiden lassen</button>
              <button class="wq-btn-abort" onclick="abortWorkerFromQuestion('${esc(d.worker_id)}')">Worker abbrechen</button>
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
          if (isActive()) { renderMessages(); renderStreamingMessage(chat); }
        }
        const card = document.getElementById(`wq-${d.worker_id}`);
        if (card) {
          card.classList.add('wq-answered');
          const body = card.querySelector('.wq-body');
          if (body) body.innerHTML += `<div style="margin-top:8px;font-size:12px;color:var(--text-400)">Beantwortet: ${esc(d.answer)}</div>`;
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
              <textarea class="wq-freeform" placeholder="Antwort eingeben..." style="width:100%;min-height:40px;margin-top:6px;margin-bottom:0;border:1px solid var(--border-100);border-radius:6px;padding:6px 8px;font-size:13px;background:var(--bg-000);color:var(--text-200);resize:vertical;display:${opts ? 'none' : 'block'}"></textarea>
            </div>
          `;
        }).join('');

        const headerLabel = isBatch ? `${questions.length} Fragen` : 'Der Agent benötigt Ihre Eingabe';
        card.innerHTML = `
          <div class="wq-header">
            <span class="wq-badge">${isBatch ? 'FRAGEN' : 'FRAGE'}</span>
            <span>${esc(headerLabel)}</span>
          </div>
          <div class="wq-body">
            ${d.context_summary ? `<div class="wq-context">${esc(d.context_summary)}</div>` : ''}
            ${questionsHtml}
            <div class="wq-actions" style="margin-top:12px">
              <button class="wq-btn-answer" onclick="answerChatQuestion('${esc(d.session_id)}')">${isBatch ? 'Antworten absenden' : 'Antworten'}</button>
            </div>
          </div>
        `;
        container.appendChild(card);
        scrollToBottom();
      },
      user_input_received: (d) => {
        // The answer landed (from this tab's submit, or another tab / the TUI).
        // Remove the question card — it's resolved. answerChatQuestion already
        // removes it on this tab's own submit; this covers the other paths.
        const card = document.getElementById(`aq-${d.session_id}`);
        if (card) card.remove();
      },
      fallback: (d) => {
        if (d.to) {
          chat.model = d.to;
          setStreamStatus(chat, 'model', modelShortName(d.to));
          if (isActive()) updateModelSelectorDisplay(d.to);
        }
      },
      // Auto routing — the server picked a concrete model for this turn. Keep
      // the composer on "Auto", but show the working model on the spinner and
      // the reason in the Auto tooltip.
      auto_route: (d) => {
        if (!d || !d.model) return;
        chat.autoPicked = d.model;
        chat.autoReason = d.reason || '';
        // Structured task analysis (task_types/tools/complexity) when the LLM
        // classifier ran — kept for an optional richer Auto badge.
        chat.autoAnalysis = d.analysis || null;
        setStreamStatus(chat, 'model', modelShortName(d.model));
        if (isActive()) {
          // Keep the composer on whichever Smart mode is active (Cloud/Lokal),
          // not a bare 'auto' — the label + tooltip stay correct per mode.
          if (typeof updateModelSelectorDisplay === 'function')
            updateModelSelectorDisplay(isAutoModel(chat.model) ? chat.model : 'auto-cloud');
        }
      },
      warmup: (d) => {
        if (d.status === 'waiting') {
          if (isActive() && typeof buddyPhase === 'function') buddyPhase('warmup');
          setStreamStatus(chat, 'label', 'Warte auf Warmup...');
        } else if (d.status === 'ready') {
          stopWarmupPoll(chat);
          updateStatusBar();
        }
      },
      max_tokens_exhausted: (d) => {
        if (isActive() && d.message) {
          setStreamStatus(chat, 'label', 'Token-Limit erreicht');
          showToast(d.message, true);
        }
      },
      compacting: (d) => {
        if (typeof buddyPhase === 'function') buddyPhase('compacting');
        // Auto-LCM emits a phase: 'compress' (verdichten) or 'expand' (entfalten,
        // when there's headroom to restore originals). Show progress accordingly.
        const verb = d.phase === 'expand' ? 'Kontext wird entfaltet' : 'Kontext wird verdichtet';
        setStreamStatus(chat, 'label', `${verb}${d.pct ? ` (${d.pct}% voll)` : ''}…`);
      },
      auto_lcm_over_threshold: (d) => {
        // Auto-LCM ran but the chat is STILL over threshold even after maximum
        // compaction. Let the user decide what to do.
        if (typeof showAutoLcmOverThresholdModal === 'function') {
          showAutoLcmOverThresholdModal(chat, d);
        }
      },
      citation_reround_start: (d) => {
        if (!isActive()) return;
        const ratio = (d && d.claim_total)
          ? ` (${d.uncited_claims}/${d.claim_total} ohne Quelle)`
          : '';
        setStreamStatus(chat, 'label', `Quellen werden erneut geprüft${ratio}…`);
      },
      citation_reround_done: () => {
        if (!isActive()) return;
        setStreamStatus(chat, 'label', '');
      },
      empty_round_nudge: (d) => {
        if (!isActive()) return;
        const attempt = (d && d.attempt) || 1;
        const max = (d && d.max) || 3;
        setStreamStatus(chat, 'label', `Modell wird neu angestoßen (${attempt}/${max})…`);
      },
      compacted: (d) => {
        // Inject a visual divider at the start of the message list so the user
        // sees where the auto-compacted history ends and the fresh tail begins.
        const existing = chat.messages[0]?.role === 'compacted';
        if (!existing) {
          chat.messages.unshift({ role: 'compacted' });
        }
        if (isActive()) renderMessages();
      },
      done: (d) => {
        console.log('[SSE] done event received', {textLen: (d.text||'').length, tokens: d.tokens, model: d.model, msgCount: chat.messages.length});
        // Chronological interleave: if we committed 'assistant_segment' rows
        // mid-turn (text → tool → text), the final assistant message must hold
        // ONLY the trailing run (chat.streamingText) — NOT d.text (the full
        // joined reply), which would duplicate the already-committed segments on
        // screen. The server persists the full reply as the message content for
        // history + tags metadata.text_rounds; on reload we reconstruct the
        // segment rows from that. Live keeps the committed rows + this last run.
        const _hasLiveSegments = chat.messages.some(
          m => m && m.role === 'assistant_segment');
        const _finalContent = _hasLiveSegments
          ? (chat.streamingText || '')
          : (d.text || chat.streamingText);
        // Finalize assistant message (always update data)
        const assistantMsg = {
          role: 'assistant',
          content: _finalContent,
        };
        if (_hasLiveSegments && Array.isArray(d.text_rounds) && d.text_rounds.length) {
          assistantMsg.metadata = { text_rounds: d.text_rounds };
        }
        if (d.tokens) chat.totalTokens = d.tokens;
        if (d.max_context) chat.maxContext = d.max_context;
        // Auto routing: keep the composer on "Auto" (the user re-routes every
        // turn) but remember which model was picked + why, for the label and
        // tooltip. Otherwise adopt the server's resolved model as usual.
        if (d.auto_route && isAutoModel(chat.model)) {
          chat.autoPicked = d.auto_route.model;
          chat.autoReason = d.auto_route.reason || '';
          if (typeof updateModelSelectorDisplay === 'function') updateModelSelectorDisplay(chat.model);
        } else if (d.model) {
          chat.model = d.model;
        }
        const tokIn = d.tokens_in || 0;
        const lastTokIn = d.last_tokens_in || tokIn;
        const tokOut = d.tokens_out || 0;
        const dur = d.duration || 0;
        const estOut = tokOut || Math.ceil((d.text || chat.streamingText || '').length / 4);
        chat._tokensIn = (chat._tokensIn || 0) + tokIn;
        chat._tokensOut = (chat._tokensOut || 0) + (tokOut || estOut);
        // This turn is committed into the session totals now — drop the live-turn
        // counters so the status bar doesn't add them again on top.
        chat._liveTurnTokensIn = 0;
        chat._liveTurnTokensOut = 0;
        assistantMsg.metadata = {
          ...(assistantMsg.metadata || {}),
          model: d.model || chat.model,
          tokens_in: tokIn,
          last_tokens_in: lastTokIn,
          tokens_out: tokOut || estOut,
          duration: dur,
        };
        // GDPR restored-span payload from the worker — used by the markdown
        // renderer to wrap each restored value in a <mark> tag with a
        // tooltip. Persisted into metadata so reload reads it back identically.
        if (Array.isArray(d.gdpr_restored_spans) && d.gdpr_restored_spans.length) {
          assistantMsg.metadata.gdpr_restored_spans = d.gdpr_restored_spans;
        }
        // Per-turn GDPR outcome (anonymise / local-fallback). Kept on the turn
        // metadata as the data source for the post-turn feedback modal below
        // (fired only when the session opted into "Frag mich nachher").
        if (d.gdpr && typeof d.gdpr === 'object') {
          assistantMsg.metadata.gdpr = d.gdpr;
        }
        // Persist the auto-route classification + routing/tool-gating decision
        // onto the turn metadata so the per-turn classification chip + modal
        // work on the live turn identically to a reloaded one.
        if (d.auto_route) {
          assistantMsg.metadata.auto_route = d.auto_route;
        }
        // Caveman level for this turn → drives the per-reply colour tint in
        // renderAssistantMessage. Without this, the just-finished reply renders
        // uncoloured (metadata had no caveman_*) until a reload fetched the
        // server-persisted value. Prefer the done payload if the server sent it,
        // else fall back to the live session toggle. Only set when non-zero so a
        // normal turn's metadata stays clean (matches the server, which omits 0).
        const _cavChat = parseInt(d.caveman_chat) || parseInt(chat.cavemanMode) || 0;
        const _cavSys = parseInt(d.caveman_system) || 0;
        if (_cavChat) assistantMsg.metadata.caveman_chat = _cavChat;
        if (_cavSys) assistantMsg.metadata.caveman_system = _cavSys;
        // Auto-LCM compaction level for this turn → drives the status-line badge
        // (chat._lcmState is the live cache; metadata.lcm_state survives reload).
        if (d.lcm_state) {
          assistantMsg.metadata.lcm_state = d.lcm_state;
          chat._lcmState = d.lcm_state;
        }
        if (dur > 0 && (tokIn + estOut) > 0) {
          // Total throughput (prompt-in + generated-out) over wall-clock.
          chat._lastSpeed = Math.round((tokIn + estOut) / dur);
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

        // Only update DOM if this chat is still visible.
        // CRITICAL: the assistant message is already in chat.messages (pushed
        // above). renderMessages() builds the DOM from it. If renderMessages()
        // (or any post-render helper) throws, the per-event try/catch in
        // API.streamChat swallows it — leaving the response in state but NOT in
        // the DOM, so it only appears after a reload (observed in 7129d8fb).
        // Guard the render so a single helper failure can't strand the reply:
        // renderMessages() runs first and on its own; if it throws we still
        // clear the streaming bubble + retry once, and the trailing cosmetics
        // (scroll/statusbar/panel) are isolated so they can't suppress it.
        if (isActive()) {
          updateModelSelectorDisplay(chat.model);
          try {
            renderMessages();
          } catch (e) {
            console.error('[done] renderMessages threw — recovering', e);
            // Drop any leftover streaming bubble so partial text doesn't linger,
            // then retry a clean render so the persisted reply still shows.
            const _b = document.querySelector('.msg-streaming');
            if (_b) _b.remove();
            try { renderMessages(); } catch (e2) { console.error('[done] render retry failed', e2); }
          }
          // Cosmetics — isolated so a failure here never hides the reply.
          try { scrollToBottom(); } catch (e) {}
          try { updateStreamingUI(false); } catch (e) {}
          try { updateStatusBar(); } catch (e) {}
          try { schedulePIIBadgeUpdate(); } catch (e) {}
          // Turn done — refresh the open panel so refs/attachments/artifacts
          // gathered this turn appear (streaming events only refreshed it
          // opportunistically; references no longer auto-open the pane).
          try { if (typeof refreshRightPanelContent === 'function') refreshRightPanelContent(); } catch (e) {}
          // Code-mode project: the turn may have created/edited files in the
          // working directory → refresh its file tree if the project detail is open.
          try {
            if (state._projectDetail && state._projectDetail.code_mode
                && typeof refreshCodeWorkingTree === 'function') {
              refreshCodeWorkingTree();
            }
          } catch (e) {}
          // Same for the right-panel Arbeitsverzeichnis tab in a code-mode chat.
          try {
            if (state.rightPanelTab === 'workdir' && typeof refreshWorkdirPanelTree === 'function') {
              refreshWorkdirPanelTree();
            }
          } catch (e) {}
        }

        // Desktop notification when window not focused
        if (!document.hasFocus() && window.electronAPI?.showNotification) {
          const preview = (d.text || chat.streamingText || '').slice(0, 120).replace(/\n/g, ' ');
          window.electronAPI.showNotification({ title: `${chat.agent || 'Brain Agent'} hat geantwortet`, body: preview || 'Antwort abgeschlossen' });
        }

        // Reload sessions for sidebar
        loadAgentSessions(chat.agent);
        // Refresh quota pill so usage updates without waiting for the 30s tick
        if (typeof QuotaMonitor !== 'undefined') QuotaMonitor.refresh();

        // Fetch a "next prompt" suggestion for the composer ghost text (best-effort)
        if (isActive()) {
          NextPrompt.fetchFor(chat.sessionId);
        }

        // ── Post-turn GDPR feedback modal ──
        // Only when: the session opted in ("Frag mich nachher") AND this turn
        // ACTIVELY took a GDPR action on the user's OWN input (d.gdpr.active).
        // d.gdpr is present on every turn of a sticky-anonymise session — even
        // when the user typed clean text and anonymise merely re-pseudonymised
        // prior chat history — so we gate on `active` to avoid asking "did it
        // work?" about untouched history. Fire-and-forget; never blocks done.
        if (isActive() && chat.gdprFeedbackAsk && d.gdpr && d.gdpr.active
            && typeof gdprFeedbackModal === 'function') {
          maybeRunGdprFeedback(chat, d.gdpr);
        }
      },
      error: (d) => {
        chat.streaming = false;
        chat.streamingText = '';
        chat.thinkingText = '';
        chat.files = [];
        // Clear live-turn token counters: on cancel/error the server persisted a
        // partial assistant message WITH tokens+cost (so a mid-stream cancel keeps
        // them), and openSession reloads that metadata — keeping the live fields
        // would double-count in the status bar. Refresh the bar so In/Out reflect
        // what was consumed before the interruption (from message metadata).
        chat._liveTurnTokensIn = 0;
        chat._liveTurnTokensOut = 0;
        clearInterval(chat._streamTimerInterval);
        if (isActive()) {
          updateStreamingUI(false);
          if (typeof updateStatusBar === 'function') updateStatusBar();
          const msg = d.message || 'Unbekannter Fehler';
          if (!/Load failed|Failed to fetch|NetworkError|AbortError|network/i.test(msg)) {
            showToast('Fehler: ' + msg, true);
          }
        }
      },
  };
}

/** Open the post-turn GDPR feedback modal and act on the user's choice.
 *  - "Passt so" (dismiss): nothing changes; result stands.
 *  - retry method: re-run THIS turn in the chosen mode (redoTurnAsGdprMode,
 *    which deletes the discarded attempt server-side first).
 *  - "Frag mich weiter" unchecked: clear the session opt-in (no more prompts);
 *    the chosen method is still reused on later turns via the sticky pref.
 *  Async + isolated — a failure here must never disturb the finished turn. */
async function maybeRunGdprFeedback(chat, gdpr) {
  let res;
  try {
    res = await gdprFeedbackModal(gdpr);
  } catch (e) {
    return;
  }
  if (!res) return;
  // Persist the keep-asking choice. Unchecking stops future feedback modals.
  if (!res.keepAsking) {
    chat.gdprFeedbackAsk = false;
    if (chat.sessionId) API.updateGdprFeedbackAsk(chat.sessionId, false).catch(() => {});
  }
  if (res.action === 'redo' && res.mode) {
    // The assistant reply for this turn is the last message in chat.messages.
    const lastIdx = (chat.messages?.length || 0) - 1;
    if (lastIdx >= 0 && typeof redoTurnAsGdprMode === 'function') {
      redoTurnAsGdprMode(lastIdx, res.mode);
    }
  }
}

/** Stream ended without a 'done' event — flush any partial text and reset UI.
 *  Guarded by streamGen so a stale safety-net can't kill a newer stream. */
function _streamSafetyNet(chat, isActive, streamGen) {
  if (!(chat.streaming && chat._streamGen === streamGen)) return;
  console.warn('[SSE] safety net triggered — done event was lost');
  const text = chat.streamingText || '';
  if (text) {
    chat.messages.push({ role: 'assistant', content: text });
  } else if (chat.sessionId) {
    // The stream ended without a `done` AND no partial text was buffered —
    // e.g. the SSE connection dropped after the last tool but before the
    // final reply streamed, while the server worker kept running and
    // persisted the full reply. Relying on streamingText would leave the
    // turn blank until a manual reload (observed in 7129d8fb). Re-open the
    // session so the persisted reply is fetched + rendered. openSession does
    // its own full render, so return early to avoid a double render.
    chat.streaming = false;
    chat.streamingText = '';
    chat.thinkingText = '';
    chat.files = [];
    clearInterval(chat._streamTimerInterval);
    if (isActive() && typeof openSession === 'function') {
      console.warn('[SSE] safety net — re-opening session to recover persisted reply');
      openSession(chat.sessionId, chat.agent);
    }
    loadAgentSessions(chat.agent);
    return;
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
function _onStreamCatch(e, chat, isActive, streamGen) {
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
      showToast('Senden fehlgeschlagen: ' + msg, true);
    }
  }
}
async function triggerLCM() {
  const chat = state.activeChat;
  const sessionId = chat?.sessionId;
  if (!sessionId) { showToast('Keine aktive Sitzung', true); return; }
  if (chat.streaming) { showToast('Bitte auf das Ende der Antwort warten', true); return; }
  const btn = document.getElementById('status-lcm-btn');
  if (btn) btn.disabled = true;
  showToast('Kontext wird verdichtet…');
  if (typeof buddyPhase === 'function') buddyPhase('compacting');
  try {
    const result = await API.post('/v1/context/compact', { session_id: sessionId });
    if (result.status === 'no_change') {
      showToast('Nichts zu verdichten');
      return;
    }
    // Reload messages from server, then inject a visual divider at the start
    // so the user sees where the compacted history ends and the fresh tail begins.
    const data = await API.getSessionMessages(sessionId);
    const rawMessages = data.messages || [];
    chat.messages = [{ role: 'compacted', before: result.before_tokens, after: result.after_tokens }];
    chat._tokensIn = 0;
    chat._tokensOut = 0;
    for (const msg of rawMessages) {
      const meta = msg.metadata;
      if (meta && msg.role === 'assistant') {
        if (meta.thinking) msg._thinking = meta.thinking;
        if (meta.thinking_summary) msg._thinkingSummary = meta.thinking_summary;
        if (meta.cost) msg._cost = meta.cost;
        if (meta.files) msg._files = meta.files;
        if (meta.tokens_in) chat._tokensIn += meta.tokens_in;
        if (meta.tokens_out) chat._tokensOut += meta.tokens_out;
      }
      chat.messages.push(msg);
    }
    if (data.total_tokens) chat.totalTokens = data.total_tokens;
    if (data.max_context) chat.maxContext = data.max_context;
    renderMessages();
    updateStatusBar();
    showToast(`Verdichtet: ${result.before_tokens}→${result.after_tokens} Token`);
  } catch(e) {
    showToast('LCM fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    if (btn) btn.disabled = false;
    if (typeof buddyTurnEnd === 'function') buddyTurnEnd();
  }
}
// Save a markdown export of the chat into the session's artifacts folder.
// kind 'summary' = LLM synopsis (chat_summary_model); 'dump' = verbatim history.
async function _exportChat(kind, btnId) {
  const chat = state.activeChat;
  const sessionId = chat?.sessionId;
  if (!sessionId) { showToast('Keine aktive Sitzung', true); return; }
  if (chat.streaming) { showToast('Bitte auf das Ende der Antwort warten', true); return; }
  const btn = document.getElementById(btnId);
  if (btn) btn.disabled = true;
  showToast(kind === 'summary' ? 'Zusammenfassung wird erstellt…' : 'Chat-Verlauf wird exportiert…');
  try {
    const result = await API.exportChat(sessionId, kind);
    if (result.error) { showToast('Export fehlgeschlagen: ' + result.error, true); return; }
    // Refresh this session's artifact list and surface the new file.
    try {
      const artResp = await API.getArtifacts(sessionId);
      state.artifacts[sessionId] = artResp.artifacts || [];
    } catch (e) { /* non-fatal */ }
    if (result.artifact_id && typeof openArtifactPanel === 'function') {
      openArtifactPanel(result.artifact_id);
    } else if (typeof setRightPanelGlow === 'function') {
      setRightPanelGlow(true);
    }
    showToast(`Gespeichert: ${result.name}`);
  } catch (e) {
    showToast('Export fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}
function exportChatSummary() { return _exportChat('summary', 'status-summary-btn'); }
function exportChatDump() { return _exportChat('dump', 'status-dump-btn'); }

// Build + download a complete-chat zip bundle, showing a live progress modal.
// Bundles everything the right panel shows: history, statistics, attachments,
// generated artifacts, tool-call input/output, references. Downloaded, not
// stored as an artifact.
async function exportChatBundle() {
  const chat = state.activeChat;
  const sessionId = chat?.sessionId;
  if (!sessionId) { showToast('Keine aktive Sitzung', true); return; }
  if (chat.streaming) { showToast('Bitte auf das Ende der Antwort warten', true); return; }
  const btn = document.getElementById('status-bundle-btn');
  if (btn) btn.disabled = true;

  const modal = _showBundleProgressModal();
  try {
    let result = null;
    await API.exportBundle(sessionId, {
      progress: (d) => modal.update(d.percent, d.stage),
      done: (d) => { result = d; },
      error: (d) => { throw new Error(d.message || 'Fehler'); },
    });
    if (!result || !result.token) throw new Error('Kein Bundle erhalten');
    modal.update(100, 'Download wird gestartet…');
    const blob = await API.fetchBundle(result.token);
    _saveBlobAs(blob, result.filename || `chat-bundle_${sessionId.slice(0,8)}.zip`);
    modal.close();
    showToast('Bundle heruntergeladen');
  } catch (e) {
    modal.close();
    showToast('Bundle fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Progress modal: title + stage text + determinate progress bar. Returns
// {update(percent, stage), close()}.
function _showBundleProgressModal() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal-content" style="max-width:460px">
      <div class="modal-header"><h2 class="modal-title">Chat-Bundle wird erstellt</h2></div>
      <div class="modal-body" style="padding:28px 24px">
        <p class="bundle-stage" style="margin:0 0 14px;color:var(--text-400);font-size:13px">Wird vorbereitet…</p>
        <div style="background:var(--bg-300,#2a2a2a);border-radius:6px;height:10px;overflow:hidden">
          <div class="bundle-bar" style="height:100%;width:0%;background:var(--accent-brand,#6c8cff);transition:width .25s ease"></div>
        </div>
        <p class="bundle-pct" style="margin:10px 0 0;text-align:right;color:var(--text-500);font-size:12px">0%</p>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const stageEl = overlay.querySelector('.bundle-stage');
  const barEl = overlay.querySelector('.bundle-bar');
  const pctEl = overlay.querySelector('.bundle-pct');
  return {
    update: (percent, stage) => {
      const p = Math.max(0, Math.min(100, Number(percent) || 0));
      barEl.style.width = p + '%';
      pctEl.textContent = p + '%';
      if (stage) stageEl.textContent = stage;
    },
    close: () => overlay.remove(),
  };
}

async function restoreLCM(sessionId) {
  if (!sessionId) return;
  if (!await showConfirm('Ursprüngliche Nachrichten wiederherstellen? Die verdichtete Zusammenfassung wird durch den vollständigen Verlauf ersetzt.', 'Verlauf wiederherstellen')) return;
  try {
    const result = await API.post('/v1/context/uncompact', { session_id: sessionId });
    if (result.status === 'no_originals') { showToast('Keine Originale zum Wiederherstellen'); return; }
    // Re-fetch messages in place without re-initialising the whole session
    const data = await API.getSessionMessages(sessionId);
    const chat = state.activeChat;
    if (chat) {
      chat.messages = [];
      chat._tokensIn = 0;
      chat._tokensOut = 0;
      for (const msg of (data.messages || [])) {
        const meta = msg.metadata;
        if (meta && msg.role === 'assistant') {
          if (meta.thinking) msg._thinking = meta.thinking;
          if (meta.cost) msg._cost = meta.cost;
          if (meta.files) msg._files = meta.files;
            if (meta.tokens_in) chat._tokensIn += meta.tokens_in;
          if (meta.tokens_out) chat._tokensOut += meta.tokens_out;
        }
        chat.messages.push(msg);
      }
      if (data.total_tokens) chat.totalTokens = data.total_tokens;
      if (data.max_context) chat.maxContext = data.max_context;
      renderMessages();
      scrollToBottom();
      updateStatusBar();
    }
    showToast('Ursprüngliche Nachrichten wiederhergestellt');
  } catch(e) {
    showToast('Wiederherstellung fehlgeschlagen: ' + (e.message || e), true);
  }
}
// UTF-8-safe base64 (umlauts in the German handover doc would corrupt btoa()).
function _utf8ToBase64(str) {
  return btoa(unescape(encodeURIComponent(str)));
}
// Generate a handover document for a session and open a NEW chat with it
// attached + a "continue where we left off" prompt seeded in the composer.
// Shared by the composer button (composerHandover) and the auto-LCM
// over-threshold modal. `sessionId` defaults to the active chat.
async function startHandoverChat(sessionId) {
  sessionId = sessionId || state.activeChat?.sessionId;
  if (!sessionId) { showToast('Keine aktive Sitzung', true); return; }
  // Capture the source chat's mode (general vs project-based) BEFORE newChat()
  // clears the binding — the handover chat must inherit it.
  const _srcProject = state.activeChat?.project || '';
  showToast('Übergabe wird erstellt…');
  let md, transcript, srcTitle;
  try {
    const res = await API.post('/v1/chat/handover', { session_id: sessionId });
    md = res.markdown;
    transcript = res.transcript || '';
    srcTitle = res.source_title || 'Chat';
    if (!md) { showToast('Übergabe konnte nicht erstellt werden', true); return; }
  } catch (e) {
    const msg = (e && e.message) ? e.message : String(e);
    showToast('Übergabe fehlgeschlagen: ' + msg, true);
    return;
  }
  // Fresh chat FIRST (newChat clears _pendingFiles), then seed BOTH docs as
  // attachments + prompt. Two separate files: the concise summary the model
  // works from, and the full verbatim history it opens only if it needs detail.
  // Both are our own generated docs — no PII gate, mark scan done.
  // Inherit the base chat's mode: a project-based source stays in that project,
  // a general source stays general (never silently rebinds the handover).
  newChat({ inheritProject: _srcProject });
  try {
    const safe = (s) => s.slice(0, 80).replace(/[\/\\]/g, '-');
    const attach = (name, text) => state._pendingFiles.push({
      name, type: 'text/markdown',
      data: _utf8ToBase64(text),
      encoding: 'base64', preview: null,
      scan: { state: 'done', reason: '' },
    });
    attach(safe(`Übergabe – ${srcTitle}`) + '.md', md);
    if (transcript) attach(safe(`Verlauf – ${srcTitle}`) + '.md', transcript);
    if (typeof renderFilePreviews === 'function') renderFilePreviews();
    if (typeof updateSendButton === 'function') updateSendButton();
    const input = _composerInputEl();
    if (input) {
      input.value = 'Dies ist eine Übergabe aus einem vorherigen Chat. Lies das angehängte Übergabe-Dokument ("Übergabe – …") und mach genau dort weiter, wo wir aufgehört haben. Der vollständige Verlauf liegt als separates Dokument ("Verlauf – …") bei — öffne ihn nur, falls du Details brauchst.';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus();
    }
    showToast('Neuer Chat mit Übergabe bereit — zum Fortfahren senden.');
  } catch (e) {
    showToast('Übergabe-Anhang fehlgeschlagen: ' + (e.message || e), true);
  }
}
// Composer toolbar button — handover from the current chat.
async function composerHandover() {
  await startHandoverChat(state.activeChat?.sessionId);
}
// Decision modal shown when auto-LCM ran but the chat is STILL over threshold
// even after maximum compaction. Three choices: retry the turn anyway, start a
// new chat with a handover, or start a fresh empty chat.
async function showAutoLcmOverThresholdModal(chat, d) {
  const pct = d && d.after_pct ? d.after_pct : '';
  const msg = `Der Kontext ist auch nach automatischer Verdichtung noch ${pct ? pct + '% ' : ''}voll — `
    + `das überschreitet die Kapazität dieses Modells.\n\n`
    + `Du kannst es trotzdem versuchen (kann fehlschlagen) oder einen neuen Chat beginnen. `
    + `Eine Übergabe fasst diesen Chat zusammen und nimmt sie in den neuen Chat mit.`;
  const choice = await showDialog({
    title: 'Kontext voll',
    message: msg,
    buttons: [
      { label: 'Erneut versuchen', value: 'retry' },
      { label: 'Neuer Chat (leer)', value: 'fresh' },
      { label: 'Neuer Chat mit Übergabe', value: 'handover', primary: true },
    ],
  });
  if (choice === 'retry') {
    if (typeof sendMessage === 'function') {
      // Re-send the last user message text. The worker will run auto_balance
      // again and proceed to run_turn regardless.
      const input = _composerInputEl();
      if (input && !input.value.trim()) {
        const lastUser = [...(chat.messages || [])].reverse().find(m => m.role === 'user');
        if (lastUser && typeof lastUser.content === 'string') input.value = lastUser.content;
      }
      showToast('Wird erneut versucht…');
    }
  } else if (choice === 'handover') {
    await startHandoverChat(chat.sessionId);
  } else if (choice === 'fresh') {
    newChat();
  }
}
async function openInspectModal() {
  const sessionId = state.activeChat?.sessionId;
  if (!sessionId) { showToast('Keine aktive Sitzung', true); return; }
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
      <h2 style="margin:0;font-size:18px;font-weight:600;color:var(--text-000)">Sitzungs-Inspektor</h2>
      <span style="font-family:var(--font-mono);font-size:12px;color:var(--text-400)">${esc(sessionId)}</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()" style="margin-left:auto">&times;</button>
    </div>
    <div id="inspect-body" style="flex:1;overflow-y:auto;padding:16px 24px 24px">
      <div style="color:var(--text-400);padding:24px;text-align:center">Wird geladen...</div>
    </div>
  </div>`;
  document.body.appendChild(overlay);

  try {
    const data = await API.inspectSession(sessionId);
    const body = document.getElementById('inspect-body');
    let html = '';

    // --- Summary bar ---
    const t = data.totals || {};
    html += `<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px">
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Anfragen</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${t.turns || 0}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Token ein</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${(t.tokens_in||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Token aus</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${(t.tokens_out||0).toLocaleString()}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Dauer</div>
        <div style="font-size:20px;font-weight:600;color:var(--text-000);margin-top:4px">${t.duration ? t.duration.toFixed(1) + 's' : '-'}</div>
      </div>
      <div style="background:var(--bg-200);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Kosten</div>
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
        <span style="color:#8b5cf6">System-Prompt</span>
        <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-400);margin-left:auto">~${spTokens.toLocaleString()} Token</span>
      </summary>
      <pre style="margin:0;padding:16px;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;max-height:400px;overflow-y:auto;background:var(--bg-200);color:var(--text-200);font-family:var(--font-mono)">${esc(spContent || '(nicht verfügbar)')}</pre>
    </details>`;

    // --- Round-0 Preamble (once; only when the session got one) ---
    // The artifact-folder note prepended into the first user message for the
    // wire. Shown here, not in chat view, because it's plumbing not dialogue.
    const preContent = data.preamble?.content || '';
    const preTokens = data.preamble?.tokens_est || 0;
    if (preContent) {
      html += `<details style="margin-bottom:8px;border:1px solid var(--border-100);border-radius:10px;overflow:hidden">
        <summary style="padding:12px 16px;cursor:pointer;background:var(--bg-100);font-weight:500;color:var(--text-000);display:flex;align-items:center;gap:8px">
          <span style="color:#0891b2">Präambel</span>
          <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-400);margin-left:auto">Runde 0 · erste Nutzernachricht · ~${preTokens.toLocaleString()} Token</span>
        </summary>
        <pre style="margin:0;padding:16px;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;max-height:400px;overflow-y:auto;background:var(--bg-200);color:var(--text-200);font-family:var(--font-mono)">${esc(preContent)}</pre>
      </details>`;
    }


    // --- Tool Definitions (once) ---
    if (toolsCount > 0) {
      html += `<details style="margin-bottom:16px;border:1px solid var(--border-100);border-radius:10px;overflow:hidden">
        <summary style="padding:12px 16px;cursor:pointer;background:var(--bg-100);font-weight:500;color:var(--text-000);display:flex;align-items:center;gap:8px">
          <span style="color:var(--accent-brand)">Tool-Definitionen</span>
          <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-400);margin-left:auto">${toolsCount} Tools &middot; ~${toolsTokens.toLocaleString()} Token</span>
        </summary>
        <div style="padding:12px 16px;display:flex;flex-wrap:wrap;gap:4px">
          ${toolNames.map(n => `<span style="font-size:11px;font-family:var(--font-mono);background:var(--bg-200);color:var(--text-300);padding:2px 8px;border-radius:4px">${esc(n)}</span>`).join('')}
        </div>
      </details>`;
    }

    // --- GDPR Mappings (admin-only; step 6.4) ---
    // Loaded lazily on first <details> open — the listing endpoint is
    // cheap (metadata only) but the per-mapping decrypt isn't, and most
    // sessions have zero pseudonym_maps rows. Auditor opens the section
    // explicitly when investigating "what got sent to the cloud".
    html += `<details id="inspect-gdpr-maps" style="margin-bottom:16px;border:1px solid var(--border-100);border-radius:10px;overflow:hidden">
      <summary style="padding:12px 16px;cursor:pointer;background:var(--bg-100);font-weight:500;color:var(--text-000);display:flex;align-items:center;gap:8px">
        <span style="color:#047857">GDPR-Zuordnungen</span>
        <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-400);margin-left:auto">nur Admin · zeigt, was gesendet wurde</span>
      </summary>
      <div id="inspect-gdpr-body" style="padding:12px 16px;font-size:12.5px;color:var(--text-300)">
        <div style="color:var(--text-400)">Zum Laden öffnen…</div>
      </div>
    </details>`;

    // --- Interactions ---
    html += `<div style="font-weight:600;font-size:14px;color:var(--text-000);margin-bottom:12px">Interaktionen</div>`;

    for (const ix of (data.interactions || [])) {
      const a = ix.assistant || {};

      // Speed calculation — total tokens (in + out) over wall-clock.
      const _aTot = (a.tokens_in || 0) + (a.tokens_out || 0);
      const speed = a.duration > 0 && _aTot > 0 ? Math.round(_aTot / a.duration) : null;

      html += `<div style="border:1px solid var(--border-100);border-radius:10px;margin-bottom:12px;overflow:hidden">`;

      // Per-turn state badges: thinking level + caveman modes
      const tLvl = a.thinking_level || (a.thinking ? 'on' : '');
      const thinkingBadge = (tLvl && tLvl !== 'none')
        ? `<span style="font-size:10px;background:#ede9fe;color:#8b5cf6;padding:1px 6px;border-radius:4px" title="Für diese Anfrage verwendete Denkstufe">thinking: ${esc(tLvl)}</span>`
        : '';
      const cavChat = parseInt(a.caveman_chat) || 0;
      const cavSys = parseInt(a.caveman_system) || 0;
      const cavName = n => ({1: 'lite', 2: 'full', 3: 'ultra'})[n] || '';
      const cavParts = [];
      if (cavSys) cavParts.push(`sys ${cavName(cavSys)}`);
      if (cavChat) cavParts.push(`chat ${cavName(cavChat)}`);
      const cavBadge = cavParts.length
        ? `<span style="font-size:10px;background:#fef3c7;color:#b45309;padding:1px 6px;border-radius:4px" title="Caveman-Ausgabestil dieser Anfrage (sys = Modell-Standard, chat = 🪨-Schalter). Wirkt nur auf den Antwortstil, nicht auf System-Prompt oder Tools.">caveman: ${cavParts.join(' / ')}</span>`
        : '';
      html += `<div style="display:flex;align-items:center;gap:8px;padding:10px 16px;background:var(--bg-100);border-bottom:1px solid var(--border-100);flex-wrap:wrap">
        <span style="font-weight:600;color:var(--text-000)">Anfrage ${ix.turn}</span>
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
            ? `${p.tokens_in.toLocaleString()} ein${_hasOut ? ' / ' + p.tokens_out.toLocaleString() + ' aus' : ''} Token (API)`
            : `~${(p.total_payload_tokens||0).toLocaleString()} Token gesch.`;
          const histLen = (p.history || []).length;
          const prevHistLen = prev ? (prev.history || []).length : 0;
          const histDelta = histLen - prevHistLen;
          const deltaBadge = prev && histDelta > 0
            ? `<span style="${MONO11};background:var(--bg-200);padding:1px 6px;border-radius:4px">+${histDelta} Nachr.</span>`
            : '';
          html += `<details style="border-bottom:1px solid var(--border-050)"${pi === 0 ? ' open' : ''}>
            <summary style="padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <span style="color:var(--accent-brand);font-weight:500;font-size:13px">API-Anfrage${payloads.length > 1 ? ' (Runde ' + round + ')' : ''}</span>
              <span style="${MONO11}">${_tokLabel}</span>
              ${deltaBadge}
              ${histLen ? `<span style="${MONO11}">&middot; Verlauf ${histLen}</span>` : ''}
            </summary>
            <div style="padding:8px 16px 12px">
              <!-- Token breakdown bar -->
              <div style="display:flex;gap:0;height:6px;margin-bottom:8px;border-radius:3px;overflow:hidden">
                <div style="flex:${p.system_tokens||1};background:#8b5cf6" title="System"></div>
                <div style="flex:${p.tools_tokens||1};background:var(--accent-brand)" title="Tools"></div>
                <div style="flex:${p.history_tokens||1};background:var(--text-400)" title="Verlauf"></div>
                <div style="flex:${p.user_tokens||1};background:var(--success)" title="Nutzer"></div>
              </div>
              <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:11px;margin-bottom:8px">
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:#8b5cf6"></span>System ~${(p.system_tokens||0).toLocaleString()}</span>
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--accent-brand)"></span>Tools (${p.tools_count||0}) ~${(p.tools_tokens||0).toLocaleString()}</span>
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--text-400)"></span>Verlauf (${histLen} Nachr.) ~${(p.history_tokens||0).toLocaleString()}</span>
                <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--success)"></span>Nutzer ~${(p.user_tokens||0).toLocaleString()}</span>
              </div>

              <!-- History (auto-open when it's what differs from the previous round) -->
              ${histLen ? `<details style="margin-bottom:4px"${histDelta > 0 ? ' open' : ''}>
                <summary style="cursor:pointer;${MONO11}">Verlauf (${histLen} Nachrichten)${histDelta > 0 ? ` &middot; ${histDelta} neu in dieser Runde` : ''}</summary>
                <div style="max-height:250px;overflow-y:auto;border:1px solid var(--border-050);border-radius:6px;margin-top:4px">
                  ${(p.history||[]).map((h, hi) => {
                    const isNew = pi > 0 && hi >= prevHistLen;
                    const bg = isNew ? 'background:var(--bg-100);' : '';
                    return `<div style="padding:4px 12px;border-bottom:1px solid var(--border-050);${bg}">
                      <span style="font-size:10px;font-weight:600;color:${h.role==='user'?'var(--accent-brand)':h.role==='tool'?'#0891b2':'var(--success)'};text-transform:uppercase">${esc(h.role)}${isNew?' · NEU':''}</span>
                      <pre style="margin:2px 0 0;font-size:11px;white-space:pre-wrap;word-break:break-word;color:var(--text-300);font-family:var(--font-mono)">${esc(String(h.content||'').substring(0, 2000))}${String(h.content||'').length > 2000 ? '\\n... (gekürzt)' : ''}</pre>
                    </div>`;
                  }).join('')}
                </div>
              </details>` : ''}

              <!-- User Message (only when present — absent on continuation rounds) -->
              ${p.user_message ? `<details open>
                <summary style="cursor:pointer;${MONO11}">Nutzernachricht (~${(p.user_tokens||0).toLocaleString()} Token)</summary>
                <pre style="${PRE};margin-top:4px;border-radius:6px">${esc(p.user_message)}</pre>
              </details>` : ''}
            </div>
          </details>`;
        }
      } else {
        // No payload data — fallback to simple view
        const userWireBlock = ix.user.wire_content
          ? `<details style="margin:0 16px 8px;border:1px solid var(--border-100);border-radius:8px;overflow:hidden">
              <summary style="padding:6px 10px;cursor:pointer;background:#ecfdf5;color:#047857;font-size:11.5px;font-weight:500">
                Wire-Anfrage (pseudonymisiert) — was das Cloud-LLM erhalten hat
              </summary>
              <pre style="${PRE};margin:0">${esc(ix.user.wire_content)}</pre>
            </details>`
          : '';
        html += `<details style="border-bottom:1px solid var(--border-050)">
          <summary style="padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:8px">
            <span style="color:var(--accent-brand);font-weight:500;font-size:13px">Anfrage</span>
            <span style="${MONO11}">~${(ix.user.tokens_est||0).toLocaleString()} Token gesch.</span>
            ${a.tokens_in ? `<span style="${MONO11}">&middot; ${a.tokens_in.toLocaleString()} Token ein (API)</span>` : ''}
          </summary>
          <pre style="${PRE}">${esc(ix.user.content || '')}</pre>
          ${userWireBlock}
        </details>`;
      }

      // --- Response ---
      if (ix.assistant) {
        const tools = a.tools || [];
        const toolBadges = tools.length ? tools.map(t => BADGE(esc(t.name), 'var(--bg-300)', 'var(--text-300)')).join(' ') : '';
        const hasWorkerTool = tools.some(t => { const rs = typeof t.result === 'string' ? t.result : JSON.stringify(t.result || ''); return rs.includes('"worker": true') || rs.includes('"worker":true'); });

        html += `<details>
          <summary style="padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span style="color:var(--success);font-weight:500;font-size:13px">Antwort</span>
            <span style="${MONO11}">~${(a.tokens_est||0).toLocaleString()} Token gesch.</span>
            ${a.tokens_out ? `<span style="${MONO11}">&middot; ${a.tokens_out.toLocaleString()} Token aus (API)</span>` : ''}
            ${a.thinking ? BADGE('thinking', '#ede9fe', '#8b5cf6') : ''}
            ${a.sdk ? BADGE('SDK', 'var(--bg-300)', 'var(--text-400)') : ''}
            ${hasWorkerTool ? BADGE('Hintergrund', '#0891b2', '#fff') : ''}
            ${toolBadges}
          </summary>
          <div style="max-height:400px;overflow-y:auto">`;

        if (tools.length) {
          html += `<div style="padding:8px 16px;background:var(--bg-100);border-bottom:1px solid var(--border-050)">`;
          for (const t of tools) {
            let tIsWorker = false;
            const rs = typeof t.result === 'string' ? t.result : JSON.stringify(t.result || '');
            if (rs.includes('"worker": true') || rs.includes('"worker":true')) tIsWorker = true;
            const wBadge = tIsWorker ? ' <span style="font-size:9px;font-weight:600;background:#0891b2;color:#fff;padding:1px 5px;border-radius:3px;letter-spacing:0.5px" title="Über Worker-Subagent ausgeführt">Hintergrund</span>' : '';
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

        // When transparent anonymisation transformed this turn, show the
        // wire-truth (pseudonymised reply as it came back from the cloud
        // LLM) alongside the de-anonymised text the user actually sees.
        // `a.gdpr_restored` is the count of tokens swapped on the reverse
        // path — zero means no transformation happened and there's nothing
        // worth showing twice (handler suppresses wire_content in that case).
        if (a.wire_content) {
          html += `<details style="margin:0 16px 8px;border:1px solid var(--border-100);border-radius:8px;overflow:hidden">
            <summary style="padding:6px 10px;cursor:pointer;background:#ecfdf5;color:#047857;font-size:11.5px;font-weight:500">
              Wire-Antwort (pseudonymisiert) — ${a.gdpr_restored||0} Token wiederhergestellt
            </summary>
            <pre style="${PRE};margin:0">${esc(a.wire_content)}</pre>
          </details>`;
        }
        // Manual web-search: the fetched sources this turn used, each with its
        // FULL content. Per-turn — re-sending the same prompt later shows a
        // DIFFERENT fetch here (e.g. today's vs tomorrow's weather), since the
        // content is re-fetched fresh each turn and never replayed from history.
        if (Array.isArray(a.web_sources) && a.web_sources.length) {
          const srcItems = a.web_sources.map(s => {
            const body = s.error
              ? `<div style="padding:6px 10px;color:#b91c1c;font-size:11.5px">⚠ ${esc(s.error)}</div>`
              : `<pre style="${PRE};margin:0">${esc(s.content || '')}</pre>`;
            return `<details style="margin:0;border-top:1px solid var(--border-100)">
              <summary style="padding:5px 10px;cursor:pointer;font-size:11.5px;color:var(--text-200)">
                ${esc(s.title || s.url)} <span style="color:var(--text-400)">— ${esc(s.url)}</span>
              </summary>${body}</details>`;
          }).join('');
          html += `<details style="margin:0 16px 8px;border:1px solid var(--border-100);border-radius:8px;overflow:hidden">
            <summary style="padding:6px 10px;cursor:pointer;background:#eff6ff;color:#1d4ed8;font-size:11.5px;font-weight:500">
              In dieser Anfrage verwendete Webquellen (${a.web_sources.length}, frisch abgerufen)
            </summary>${srcItems}
          </details>`;
        }
        html += `<pre style="${PRE}">${esc(a.content || '')}</pre>
          </div>
        </details>`;
      } else {
        html += `<div style="padding:10px 16px;color:var(--text-400);font-style:italic;font-size:13px">Keine Antwort</div>`;
      }

      html += `</div>`;
    }

    body.innerHTML = html;

    // GDPR Mappings — lazy load on first open (step 6.4). Listing endpoint
    // is admin-only; non-admin viewers will see a 403 here. We render the
    // error inline rather than hiding the section so admins-via-test see
    // the gate is wired (instead of wondering why nothing loaded).
    const gdprDetails = document.getElementById('inspect-gdpr-maps');
    if (gdprDetails) {
      gdprDetails.addEventListener('toggle', async () => {
        if (!gdprDetails.open) return;
        if (gdprDetails.dataset.loaded === '1') return;
        gdprDetails.dataset.loaded = '1';
        const gbody = document.getElementById('inspect-gdpr-body');
        gbody.innerHTML = '<div style="color:var(--text-400)">Zuordnungen werden geladen…</div>';
        try {
          const list = await API.listSessionGdprMaps(sessionId);
          const maps = list.mappings || [];
          if (!maps.length) {
            gbody.innerHTML = '<div style="color:var(--text-400)">Keine Anonymisierungs-Zuordnungen für diese Sitzung gespeichert.</div>';
            return;
          }
          let mhtml = `<div style="color:var(--text-400);margin-bottom:10px">${maps.length} Zuordnung${maps.length === 1 ? '' : 'en'} gespeichert. Jede Zeile war eine „Anonymisieren & fortfahren“-Anfrage — Pseudonymisierungs-Zuordnung verschlüsselt gespeichert, bei Bedarf entschlüsselt.</div>`;
          for (const m of maps) {
            const when = m.created_at
              ? new Date(m.created_at * 1000).toLocaleString()
              : '—';
            mhtml += `<details style="margin-bottom:8px;border:1px solid var(--border-100);border-radius:8px;overflow:hidden" data-mapping-id="${esc(m.mapping_id)}">
              <summary style="padding:8px 12px;cursor:pointer;background:var(--bg-100);display:flex;align-items:center;gap:10px;font-size:12.5px">
                <span style="font-family:var(--font-mono);color:var(--text-200)">${esc(m.mapping_id.slice(0, 16))}…</span>
                <span style="color:var(--text-400);margin-left:auto">${esc(when)}</span>
              </summary>
              <div class="gdpr-map-detail" style="padding:10px 12px;color:var(--text-400);font-size:12px">Klicken, um entschlüsselte Inhalte zu laden…</div>
            </details>`;
          }
          gbody.innerHTML = mhtml;
          // Per-mapping lazy-decrypt on row open. Each <details>.toggle
          // fires once; the response is rendered as a before/after table
          // with monospace value columns.
          for (const d of gbody.querySelectorAll('details[data-mapping-id]')) {
            d.addEventListener('toggle', async () => {
              if (!d.open || d.dataset.loaded === '1') return;
              d.dataset.loaded = '1';
              const detailBox = d.querySelector('.gdpr-map-detail');
              detailBox.innerHTML = 'Wird entschlüsselt…';
              try {
                const mapping = await API.getSessionGdprMap(sessionId, d.dataset.mappingId);
                const pairs = mapping.pairs || [];
                const cats = mapping.categories || {};
                const sources = mapping.sources || [];
                const catLine = Object.entries(cats)
                  .map(([k, v]) => `${esc(k)}:${v}`).join(' · ') || '—';
                const srcLine = sources.length ? sources.map(esc).join(', ') : '—';
                let inner = `<div style="display:grid;grid-template-columns:auto auto;gap:4px 12px;margin-bottom:10px;font-size:11.5px">
                  <span style="color:var(--text-400)">Quellen</span><span style="color:var(--text-200)">${srcLine}</span>
                  <span style="color:var(--text-400)">Kategorien</span><span style="color:var(--text-200)">${catLine}</span>
                  <span style="color:var(--text-400)">Erzeugte Token</span><span style="color:var(--text-200)">${mapping.token_count || 0}</span>
                </div>`;
                if (!pairs.length) {
                  inner += '<div style="color:var(--text-400)">Zuordnung entschlüsselt, enthält aber keine Einträge.</div>';
                } else {
                  inner += '<div style="font-size:11px;color:var(--text-400);margin-bottom:4px">Vorher → Nachher (was der Nutzer schrieb → was das Cloud-LLM erhielt)</div>';
                  inner += '<table style="width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:11.5px">';
                  inner += '<thead><tr><th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--border-100);color:var(--text-400);font-weight:500">Echter Wert</th><th style="text-align:left;padding:4px 8px;border-bottom:1px solid var(--border-100);color:var(--text-400);font-weight:500">Gesendeter Token</th></tr></thead><tbody>';
                  for (const p of pairs) {
                    inner += `<tr>
                      <td style="padding:4px 8px;color:var(--text-200);word-break:break-all;border-bottom:1px solid var(--border-100)">${esc(p.real)}</td>
                      <td style="padding:4px 8px;color:#047857;word-break:break-all;border-bottom:1px solid var(--border-100)">${esc(p.token)}</td>
                    </tr>`;
                  }
                  inner += '</tbody></table>';
                }
                detailBox.innerHTML = inner;
              } catch (mErr) {
                detailBox.innerHTML = `<div style="color:var(--error)">Entschlüsselung fehlgeschlagen: ${esc(mErr.message || mErr)}</div>`;
              }
            });
          }
        } catch (lErr) {
          // 403 = non-admin viewer. Surface as a normal info row so the
          // section's gating intent is obvious.
          const msg = (lErr && lErr.message) ? lErr.message : String(lErr);
          if (/403|admin/i.test(msg)) {
            gbody.innerHTML = '<div style="color:var(--text-400)">Nur Admin — als Administrator anmelden, um Pseudonymisierungs-Zuordnungen einzusehen.</div>';
          } else {
            gbody.innerHTML = `<div style="color:var(--error)">${esc(msg)}</div>`;
          }
        }
      });
    }
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
  if (!answer) { showToast('Bitte eine Option wählen oder eine Antwort eingeben', true); return; }
  try {
    await API.post(`/v1/workers/${encodeURIComponent(workerId)}/answer`, { answer });
    const card = document.getElementById(`wq-${workerId}`);
    if (card) { card.classList.add('wq-answered'); }
  } catch (e) { showToast('Antwort konnte nicht gesendet werden: ' + e.message, true); }
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
    showToast(items.length ? `Bitte alle ${items.length} Fragen beantworten` : 'Keine Frage zu beantworten', true);
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
    // Answer submitted → remove the question card immediately. The turn keeps
    // streaming (so reconcile would otherwise preserve it); leaving it visible
    // after answering confused users. The model's continued response renders
    // normally in the streaming block below.
    card.remove();
  } catch (e) { showToast('Antwort konnte nicht gesendet werden: ' + e.message, true); }
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
  if (card) { card.classList.add('wq-answered'); card.querySelector('.wq-body').innerHTML += '<div style="margin-top:8px;font-size:12px;color:var(--error)">Worker abgebrochen</div>'; }
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
// The spinner always shows the model actually doing the work. On "Auto" that
// is the per-turn picked model (chat.autoPicked), not the literal "auto".
function spinnerModelName(chat) {
  if (isAutoModel(chat?.model) && chat?.autoPicked) return modelShortName(chat.autoPicked);
  return modelShortName(chat?.model);
}
// Status text (model / label / elapsed) is mirrored onto chat state so the
// inline status line in renderStreamingMessage survives re-renders, then the
// live DOM span (if currently mounted) is updated in place. The SSE handlers
// still write some labels directly by id — those also land on the mounted span;
// the seed-from-state covers the next re-render.
function setStreamStatus(chat, field, text) {
  const target = chat || state.activeChat;
  if (!target) return;
  const key = field === 'model' ? '_streamModel' : field === 'elapsed' ? '_streamElapsed' : '_streamLabel';
  target[key] = text;
  if (target === state.activeChat) {
    const id = field === 'model' ? 'spinner-model' : field === 'elapsed' ? 'spinner-elapsed' : 'spinner-label';
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
}
function updateStreamingUI(isStreaming, chat) {
  const sendBtn = document.getElementById('chat-send-btn');
  const stopBtn = document.getElementById('chat-stop-btn');
  // Use provided chat, fall back to activeChat for backward compat (stopGeneration, etc.)
  const targetChat = chat || state.activeChat;

  if (isStreaming) {
    sendBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    if (targetChat) {
      targetChat._streamModel = spinnerModelName(targetChat);
      // The rotating working word (spinner-synth) now carries the generic
      // "is working" hint, so the label starts EMPTY — SSE handlers still set
      // it for SPECIFIC states (warmup, queue, citation re-check, …).
      targetChat._streamLabel = '';
      targetChat._streamElapsed = '';
    }
    if (typeof buddyTurnStart === 'function') buddyTurnStart();
  } else {
    sendBtn.classList.remove('hidden');
    stopBtn.classList.add('hidden');
    if (targetChat) { targetChat._streamModel = ''; targetChat._streamLabel = ''; targetChat._streamElapsed = ''; }
    if (typeof buddyTurnEnd === 'function') buddyTurnEnd();
  }
}
function updateStreamTimer(chat) {
  const target = chat || state.activeChat;
  if (!target?._streamStartTime) return;
  const elapsed = ((Date.now() - target._streamStartTime) / 1000).toFixed(1) + 's';
  target._streamElapsed = elapsed;
  if (target === state.activeChat) {
    const el = document.getElementById('spinner-elapsed');
    if (el) el.textContent = elapsed;
  }
  // Drive the model↔synthetic-word alternation (JS, not CSS — the streaming
  // block is rebuilt each delta so a keyframe animation can't run).
  if (typeof _streamRotateTick === 'function') _streamRotateTick(target);
}

/* ═══════════════════════════════════════════════════════════
   MESSAGE RENDERING
   ═══════════════════════════════════════════════════════════ */
