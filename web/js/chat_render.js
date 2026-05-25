// chat_render.js — message rendering + markdown + GDPR-highlight + citation pins. Split from chat.js (Tier F Phase 4). Global <script>, no modules.

function renderMessages() {
  const container = document.getElementById('messages-container');
  const chat = state.activeChat;
  if (!chat) { container.innerHTML = ''; return; }

  if (!chat._collapsedTurns) chat._collapsedTurns = new Set();
  if (!chat._expandedHints) chat._expandedHints = new Set();

  // Active turn always expanded: while a stream is running, the most-
  // recent turn (= the one receiving deltas) gets dropped from the
  // collapsed set on every render. The user can collapse it mid-stream
  // to peek elsewhere, but the very next delta re-opens it. Old
  // completed turns keep their user-set collapsed state untouched.
  if (chat.streaming) {
    const _activeTurn = currentTurnNum(chat);
    if (_activeTurn > 0) chat._collapsedTurns.delete(_activeTurn);
  }

  // Group flat messages[] into turns. A turn opens at every user/human role
  // and closes at the next one. Pre-user messages (rare) become turn 0.
  const turns = []; // { turnNum, userIdx, userMsg, memberIdxs: [...] }
  // Extract leading compacted-divider marker (injected by triggerLCM, not a real turn).
  let lcmDividerHtml = '';
  const msgs = chat.messages[0]?.role === 'compacted'
    ? (lcmDividerHtml = (() => {
        const { before, after } = chat.messages[0];
        const saved = before && after ? ` — ${before.toLocaleString()}→${after.toLocaleString()} tokens` : '';
        return `<div class="lcm-divider"><span class="lcm-divider-label">Kontext verdichtet${saved}</span></div>`;
      })(), chat.messages.slice(1))
    : chat.messages;

  let cur = null;
  let nextTurnNum = 1;
  // Pre-user activity (e.g. the upfront transparent-anonymisation synthetic
  // rows that the server persists BEFORE the user message — see
  // `_emit_synthetic_tool_event` calls in handlers/chat.py). These belong to
  // the next user turn, not to a phantom turn 0 (which has no header and
  // collapses by default, hiding the privacy operations).
  let preTurnIdxs = [];
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (m.role === 'user' || m.role === 'human') {
      if (cur) turns.push(cur);
      cur = {
        turnNum: nextTurnNum++,
        userIdx: i,
        userMsg: m,
        // Pre-user synthetic rows ride INTO this turn so they appear
        // grouped with the user's send.
        memberIdxs: [...preTurnIdxs, i],
      };
      preTurnIdxs = [];
    } else if (!cur) {
      // No user message yet — hold non-user rows for the upcoming turn.
      preTurnIdxs.push(i);
    } else {
      cur.memberIdxs.push(i);
    }
  }
  if (cur) turns.push(cur);
  // Any remaining pre-turn rows (no user message in chat at all) — emit
  // as a header-less turn so they don't get dropped silently.
  if (preTurnIdxs.length && !cur) {
    turns.push({ turnNum: 0, userIdx: -1, userMsg: null, memberIdxs: preTurnIdxs });
  }

  // renderTurnBody references chat.messages by index; patch the reference so
  // indices into msgs (compacted-marker stripped) are still correct.
  const _savedMessages = chat.messages;
  if (lcmDividerHtml) chat.messages = msgs;

  // Incremental render (Ebene 3): instead of blowing away the whole container
  // and re-highlighting every prior turn on each SSE event, build an ordered
  // list of keyed blocks and reconcile them against the existing DOM. Each
  // block carries a content hash; an unchanged block keeps its DOM node (and
  // its already-applied syntax highlighting) untouched. Only changed/new blocks
  // are re-rendered + re-highlighted. The active turn is the usual hot block
  // during streaming; completed turns above it are skipped entirely.
  const blocks = []; // { key, html, hash }
  if (lcmDividerHtml) blocks.push({ key: 'lcm-divider', html: lcmDividerHtml, hash: lcmDividerHtml });
  for (const t of turns) {
    const isCollapsed = chat._collapsedTurns.has(t.turnNum);
    const cls = isCollapsed ? 'turn-group collapsed' : 'turn-group';
    const fullQ = turnQuestionFull(t.userMsg);
    const isHintExpanded = chat._expandedHints && chat._expandedHints.has(t.turnNum);
    const hasQ = fullQ.length > 0;
    const hintCls = 'turn-group-collapsed-hint' + (isHintExpanded ? ' expanded' : '');
    const chevronTitle = isHintExpanded ? 'Anfrage einklappen' : 'Vollständige Anfrage anzeigen';
    const chevron = hasQ
      ? `<button class="turn-group-hint-toggle${isHintExpanded ? ' expanded' : ''}" onclick="toggleHintExpand(${t.turnNum})" title="${chevronTitle}" aria-label="${chevronTitle}">
           <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
         </button>`
      : '';
    // GDPR highlight overlay on the turn-header hint text. Mirrors the
    // assistant-side behavior: when the user message was anonymised on the
    // way out, the original PII values get the yellow <mark> overlay so the
    // request side of every anonymised turn matches the response side. Gated
    // by the same composer toggle (state.showGdprDetails).
    const userSpans = t.userMsg?.metadata?.gdpr_restored_spans;
    const showUserGdpr = state.showGdprDetails && Array.isArray(userSpans) && userSpans.length;
    const hintInner = showUserGdpr
      ? renderPlainTextWithGdprHighlights(fullQ, userSpans)
      : esc(fullQ);
    const badge = t.turnNum > 0
      ? `<div class="turn-group-header">
           <span class="turn-group-badge" onclick="toggleTurnCollapse(${t.turnNum})" title="Klick zum ${isCollapsed ? 'Aufklappen' : 'Zuklappen'} dieser Anfrage">
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
             Anfrage ${t.turnNum}
           </span>
           <span class="${hintCls}">${hintInner}</span>
           ${chevron}
         </div>`
      : '';
    // Detect compacted-context turns: user msg starts with "[Conversation Context]"
    const userContent = typeof t.userMsg?.content === 'string' ? t.userMsg.content : '';
    if (userContent.startsWith('[Conversation Context]')) {
      // Extract summary text (strip the preamble header and tool-hint sentence)
      const summaryRaw = userContent
        .replace(/^\[Conversation Context\]\s*/, '')
        .replace(/^## Compacted Conversation History\s*/, '')
        .replace(/The following summaries cover[^\n]*\n?/g, '')
        .replace(/Use context_search[^\n]*\n?/g, '')
        .trim();
      const isOpen = !chat._collapsedTurns.has(t.turnNum);
      const toggleFn = `toggleTurnCollapse(${t.turnNum})`;
      const sessionId = chat.sessionId || '';
      const lcmHtml = `<div class="lcm-summary-block" data-turn="${t.turnNum}">
        <div class="lcm-summary-header" onclick="${toggleFn}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px;flex-shrink:0;transition:transform .2s;transform:rotate(${isOpen?'0':'-90'}deg)"><polyline points="6 9 12 15 18 9"/></svg>
          <span style="flex:1">Kontext verdichtet</span>
          <button class="lcm-restore-btn" onclick="event.stopPropagation();restoreLCM('${sessionId}')" title="Ursprüngliche Nachrichten wiederherstellen">Wiederherstellen</button>
        </div>
        ${isOpen ? `<div class="lcm-summary-body">${marked.parse(summaryRaw)}</div>` : ''}
      </div>`;
      blocks.push({ key: 'lcm-' + t.turnNum, html: lcmHtml, hash: lcmHtml });
    } else {
      let body = renderTurnBody(chat.messages, t.memberIdxs, t.turnNum, chat);
      // Chat summary block: rendered once, under the first turn's badge.
      // Default collapsed; the open/closed state lives on `chat._summaryOpen`
      // so it survives re-renders and summary refreshes never force-expand
      // a closed block. When the user has it open and the server pushes a
      // new summary, the content updates in place.
      let summaryBlock = '';
      if (t.turnNum === 1 && chat.chatSummary) {
        const sOpen = chat._summaryOpen === true;
        summaryBlock = `<div class="chat-summary-block">
          <details${sOpen ? ' open' : ''} ontoggle="toggleChatSummary(this)">
            <summary class="chat-summary-header">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px;flex-shrink:0"><polyline points="6 9 12 15 18 9"/></svg>
              <span>Zusammenfassung</span>
            </summary>
            <div class="chat-summary-body">${esc(chat.chatSummary)}</div>
          </details>
        </div>`;
      }
      // The round-0 preamble (artifact-folder note) is intentionally NOT shown
      // in chat view — it's plumbing, surfaced in the session inspector as its
      // own card. turnQuestionFull already strips it from the header hint.
      const turnHtml = `<div class="${cls}" data-turn="${t.turnNum}">${badge}${summaryBlock}<div class="turn-body">${body}</div></div>`;
      blocks.push({ key: 'turn-' + t.turnNum, html: turnHtml, hash: turnHtml });
    }
  }

  if (lcmDividerHtml) chat.messages = _savedMessages;

  // Reconcile the ordered block list against the container's children, keyed by
  // a `data-render-key` we stamp on each block's root element. Unchanged blocks
  // (same key + same hash) are left in place — their DOM and syntax highlighting
  // survive. Changed blocks are replaced, new blocks inserted, stale removed.
  // `changedRoots` collects the elements we actually (re)wrote so post-render
  // work (highlight, chevron-fit) runs only on them, not the whole tree.
  const changedRoots = _reconcileMessageBlocks(container, blocks);

  // Hide hint-toggle chevron when the question fits without ellipsis.
  // Expanded hints always show the chevron (so the user can collapse).
  // Only the freshly-written blocks can have changed layout; skip the rest.
  for (const root of changedRoots) {
    root.querySelectorAll('.turn-group-header').forEach(hdr => {
      const hint = hdr.querySelector('.turn-group-collapsed-hint');
      const toggle = hdr.querySelector('.turn-group-hint-toggle');
      if (!hint || !toggle) return;
      if (hint.classList.contains('expanded')) {
        toggle.style.display = '';
        return;
      }
      if (hint.clientWidth === 0) return; // not laid out yet — leave default
      const truncated = hint.scrollWidth > hint.clientWidth + 1;
      toggle.style.display = truncated ? '' : 'none';
    });

    // Syntax highlight code blocks (skip tool-result blocks — pre-highlighted inline)
    root.querySelectorAll('pre:not(.tool-result-pre) code').forEach(block => {
      try { hljs.highlightElement(block); } catch(e) {}
    });
  }

  // Update right panel badges (attachment/reference/artifact counts)
  if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
  // Re-attach the scroll-sync observer to the (possibly) rebuilt turn DOM so
  // scrolling a turn into view drives the right panel's per-turn focus. Cheap +
  // idempotent (disconnects then re-observes), so run it whenever anything moved.
  if (changedRoots.length && typeof initTurnScrollSync === 'function') initTurnScrollSync();
}
// Reconcile an ordered list of {key, html, hash} blocks against `container`'s
// direct children. Returns the array of element roots that were newly inserted
// or replaced (i.e. need post-render highlighting). Children are matched by the
// `data-render-key` attribute we stamp; a block whose key AND hash both match
// the existing element in the right position is left completely untouched.
function _reconcileMessageBlocks(container, blocks) {
  const changed = [];
  // Fast path: empty target.
  if (!blocks.length) { container.innerHTML = ''; return changed; }

  // Build a node from an HTML string (each block is a single root element).
  const makeNode = (b) => {
    const tpl = document.createElement('template');
    tpl.innerHTML = b.html.trim();
    const el = tpl.content.firstElementChild;
    if (el) { el.setAttribute('data-render-key', b.key); el._renderHash = b.hash; }
    return el;
  };

  // Index existing children by render-key for O(1) reuse lookups.
  const existing = new Map();
  for (const child of Array.from(container.children)) {
    const k = child.getAttribute && child.getAttribute('data-render-key');
    if (k != null) existing.set(k, child);
    else child.remove(); // stray node without our key — drop it
  }

  let cursor = container.firstElementChild;
  for (const b of blocks) {
    const prior = existing.get(b.key);
    if (prior && prior._renderHash === b.hash) {
      // Unchanged: ensure it sits at the cursor position, then advance past it.
      if (prior !== cursor) container.insertBefore(prior, cursor);
      else cursor = cursor.nextElementSibling;
      existing.delete(b.key);
      continue;
    }
    const node = makeNode(b);
    if (!node) continue;
    if (prior) {
      container.replaceChild(node, prior);
      existing.delete(b.key);
      cursor = node.nextElementSibling;
    } else {
      container.insertBefore(node, cursor);
    }
    changed.push(node);
  }

  // Remove any leftover nodes whose keys no longer appear.
  for (const stale of existing.values()) stale.remove();
  return changed;
}
// --- Turn collapse/nav helpers ---
// Strip the round-0 preamble (metadata.preamble) off a user message's text
// so turn headers, breadcrumb titles, and previews show only what the user
// typed — not the artifact-folder note prepended into content for the wire.
function _stripPreamble(msg, txt) {
  const pre = msg?.metadata?.preamble;
  if (typeof pre === 'string' && pre && txt.startsWith(pre)) {
    return txt.slice(pre.length).replace(/^\n+/, '');
  }
  return txt;
}
// Privacy (Datenschutz) state was previously tracked on a separate
// chat._privacyStates map with its own _privacyAutoUpdate / togglePrivacySummary
// helpers. Since v9.8.x synthetic anonymise/deanonymise rows live inside
// the same merged "Aktivität" disclosure as tool calls, so the activity
// state machine drives both.

function renderTurnBody(messages, memberIdxs, turnNum, chat) {
  // Merged "Aktivität" disclosure: real tool_calls / thinking AND synthetic
  // GDPR rows (anonymise / anonymise_read / deanonymise_*) live inside the
  // same <details> block. Items render in their insertion order — which is
  // also their _ts order — so the body reads chronologically.
  //
  // Header counters (per user spec): N Tools · M Anon · K De-Anon.
  // Zero counters are omitted. state.showGdprDetails toggle hides the
  // synthetic ROWS only (header counters always appear).
  const isSynthetic = (m) => m.synthetic === true;
  const isActivity = (m) => !isSynthetic(m) && (
    m.role === 'thinking' || m.role === 'tool_call' || m.role === 'tool_result');
  const isResponse = (m) => m.role === 'assistant' && (typeof m.content === 'string' ? m.content.trim() : '');

  // Find the last assistant response with content in this turn.
  let lastResponseMemberPos = -1;
  for (let i = memberIdxs.length - 1; i >= 0; i--) {
    if (isResponse(messages[memberIdxs[i]])) { lastResponseMemberPos = i; break; }
  }
  const scanEnd = lastResponseMemberPos === -1 ? memberIdxs.length : lastResponseMemberPos;

  // First pass — counters and synthetic real-vs-attempted classification.
  const isAnonKind = (k) => k === 'anonymise' || k === 'anonymise_read';
  const isDeanonKind = (k) => k === 'deanonymise_text' || k === 'deanonymise_file';

  // Pair synthetic tool_call → tool_result so we can look up findings/restored.
  // Walk over ALL memberIdxs (synthetic rows can sit before AND after the
  // assistant response — the pre-anonymisation of the *next* user message
  // gets folded into this turn by the message walker).
  const synthDone = new Map(); // call-idx -> tool_result msg
  for (let i = 0; i < memberIdxs.length; i++) {
    const callMsg = messages[memberIdxs[i]];
    if (!isSynthetic(callMsg) || callMsg.role !== 'tool_call') continue;
    for (let j = i + 1; j < memberIdxs.length; j++) {
      const nxt = messages[memberIdxs[j]];
      if (!isSynthetic(nxt) || nxt.role !== 'tool_result') continue;
      const idMatch = callMsg.tool_use_id && nxt.tool_use_id && callMsg.tool_use_id === nxt.tool_use_id;
      const kindMatch = !callMsg.tool_use_id && nxt.kind === callMsg.kind;
      if (idMatch || kindMatch) { synthDone.set(memberIdxs[i], nxt); break; }
    }
  }

  // Tool / anon / deanon counters; "real" set of synthetic calls that
  // actually did something (findings>0 or restored>0).
  const realOpsByCall = new Map();
  let toolCount = 0;
  let anonAttempted = 0, anonReal = 0;
  let deanonAttempted = 0, deanonReal = 0;
  for (let i = 0; i < memberIdxs.length; i++) {
    const idx = memberIdxs[i];
    const m = messages[idx];
    if (isSynthetic(m)) {
      if (m.role !== 'tool_call') continue;
      const k = m.kind || m.name || '';
      const res = synthDone.get(idx)?.result || {};
      const findings = Number(res.findings || 0);
      const restored = Number(res.restored || 0);
      if (isAnonKind(k)) {
        anonAttempted++;
        if (findings > 0) { anonReal++; realOpsByCall.set(idx, true); }
      } else if (isDeanonKind(k)) {
        deanonAttempted++;
        if (restored > 0) { deanonReal++; realOpsByCall.set(idx, true); }
      } else {
        realOpsByCall.set(idx, true);
      }
      continue;
    }
    // Real activity — only count rows BEFORE the assistant response;
    // post-response activity is rare but should not inflate counters.
    if (i >= scanEnd) continue;
    if (!isActivity(m)) continue;
    if (m.role === 'tool_call') toolCount++;
  }

  // Second pass — build a linear list of body items, then sort
  // chronologically so anonymise/tool/deanonymise rows interleave by time
  // regardless of where they landed in chat.messages.
  //
  // Ordering signal per item (in priority order):
  //   1. _ts (set live on SSE events)
  //   2. message.id (DB row id — monotonic across persistence, used on
  //      page reload where _ts isn't populated by load_messages)
  //
  // Special case — pre-user anon rows: the server emits
  // synthetic_tool_use/anonymise BEFORE persisting the user message, so
  // those rows have member positions AHEAD of userIdx and (on reload)
  // smaller message ids than the user row. Logically they belong AFTER
  // the user-send — they're the system's reaction to it. We detect this
  // by comparing each item's member position against the user-row's
  // member position, and bump the sort key of pre-user synthetic rows
  // into the post-user window so they render between the user send and
  // the first real tool call.
  const userMemberPos = memberIdxs.findIndex(idx => {
    const m = messages[idx];
    return m && (m.role === 'user' || m.role === 'human');
  });
  // Anchor for the bump: the lowest sort key of any post-user activity
  // item, minus an epsilon, so pre-user synthetic rows still appear
  // BEFORE the first real tool call but AFTER the user message. If no
  // post-user activity exists yet, fall back to "right after user".
  let postUserAnchor = Infinity;
  const sortKey = (m) => m ? (Number(m._seq) || Number(m.id) || 0) : 0;
  for (let i = 0; i < memberIdxs.length; i++) {
    if (userMemberPos < 0 || i <= userMemberPos) continue;
    const m = messages[memberIdxs[i]];
    if (isSynthetic(m)) continue;
    if (m.role !== 'thinking' && m.role !== 'tool_call') continue;
    const k = sortKey(m);
    if (k && k < postUserAnchor) postUserAnchor = k;
  }
  // Fallback anchor: user message's own key + 1 (ensures pre-user
  // synthetic rows sort AFTER the user send).
  const userKey = userMemberPos >= 0 ? sortKey(messages[memberIdxs[userMemberPos]]) : 0;
  if (!Number.isFinite(postUserAnchor)) postUserAnchor = userKey + 1;

  const bodyItems = []; // { kind, sortTs, ... }
  let currentRound = null;
  const flushRound = () => {
    if (!currentRound) return;
    const firstMsg = currentRound.thinking?.m || currentRound.tools[0]?.m;
    const ts = sortKey(firstMsg);
    bodyItems.push({ kind: 'round', sortTs: ts, round: currentRound });
    currentRound = null;
  };
  for (let i = 0; i < memberIdxs.length; i++) {
    const idx = memberIdxs[i];
    const m = messages[idx];
    if (isSynthetic(m)) {
      // tool_result is rendered paired inside the tool_call by
      // renderSyntheticGdprCall — skip standalone to avoid duplication.
      if (m.role !== 'tool_call') continue;
      if (!realOpsByCall.has(idx)) continue;
      flushRound();
      let ts = sortKey(m);
      // Pre-user synthetic row: bump into the post-user window so it
      // renders between the user send and the first real activity item.
      // 0.5 keeps relative order between multiple pre-user synthetics
      // intact (stable sort) while landing them strictly before
      // postUserAnchor.
      if (userMemberPos >= 0 && i < userMemberPos) {
        ts = postUserAnchor - 0.5;
      }
      bodyItems.push({ kind: 'privacy', sortTs: ts, item: { idx, m } });
      continue;
    }
    // Real activity items: only those before the response.
    if (i >= scanEnd) continue;
    if (!isActivity(m)) continue;
    if (m.role === 'tool_result') continue; // paired inside tool_call
    if (m.role === 'thinking') {
      flushRound();
      currentRound = { thinking: { idx, m }, tools: [], toolRound: null };
    } else if (m.role === 'tool_call') {
      const tr = m.tool_round ?? null;
      // Flush when the LLM round number changes (new multi-step round
      // without a thinking block in between).
      if (currentRound && tr !== null && currentRound.toolRound !== null && tr !== currentRound.toolRound) {
        flushRound();
      }
      if (!currentRound) currentRound = { thinking: null, tools: [], toolRound: tr };
      if (currentRound.toolRound === null && tr !== null) currentRound.toolRound = tr;
      currentRound.tools.push({ idx, m });
    }
  }
  flushRound();
  // Stable sort by sortTs. Equal keys retain their walker-order.
  bodyItems.sort((a, b) => a.sortTs - b.sortTs);

  // Trailing per-category notes if attempts existed but nothing real fired.
  const trailingNotes = [];
  if (anonAttempted > 0 && anonReal === 0) {
    trailingNotes.push('<div class="privacy-empty-note">Keine Anonymisierungen notwendig</div>');
  }
  if (deanonAttempted > 0 && deanonReal === 0) {
    trailingNotes.push('<div class="privacy-empty-note">Keine De-Anonymisierungen notwendig</div>');
  }

  // Render body. Privacy rows respect state.showGdprDetails — when OFF,
  // their rows are omitted from the body but counters still appear in the
  // header.
  let bodyHtml = '';
  for (const entry of bodyItems) {
    if (entry.kind === 'round') {
      const r = entry.round;
      let roundHtml = '';
      if (r.thinking) {
        roundHtml += `<div class="activity-item activity-thinking">${renderMessage(r.thinking.m, r.thinking.idx)}</div>`;
      }
      if (r.tools.length) {
        const toolsHtml = r.tools.map(t =>
          `<div class="activity-item activity-tool">${renderMessage(t.m, t.idx)}</div>`
        ).join('');
        roundHtml += `<div class="activity-tools-group${r.thinking ? ' activity-tools-indented' : ''}">${toolsHtml}</div>`;
      }
      bodyHtml += `<div class="activity-round">${roundHtml}</div>`;
    } else if (entry.kind === 'privacy') {
      if (!state.showGdprDetails) continue;
      const it = entry.item;
      bodyHtml += `<div class="activity-item activity-privacy">${renderMessage(it.m, it.idx)}</div>`;
    }
  }
  if (state.showGdprDetails) bodyHtml += trailingNotes.join('');

  // Everything from lastResponseMemberPos onwards = assistant reply.
  // Trailing synthetic rows (next-turn pre-anonymisation) already counted
  // in the header above; skip here so they don't render bare under the
  // assistant reply.
  let responseHtml = '';
  if (lastResponseMemberPos !== -1) {
    for (let i = lastResponseMemberPos; i < memberIdxs.length; i++) {
      if (isSynthetic(messages[memberIdxs[i]])) continue;
      responseHtml += renderMessage(messages[memberIdxs[i]], memberIdxs[i]);
    }
  }

  // No activity AND no real synthetic rows — nothing to disclose.
  if (toolCount === 0 && anonReal === 0 && deanonReal === 0 && anonAttempted === 0 && deanonAttempted === 0
      && !bodyItems.some(e => e.kind === 'round' && e.round.thinking)) {
    return responseHtml;
  }

  // Header label per user spec: N Tools · M Anon · K De-Anon. Hide zeros.
  const parts = [];
  if (toolCount > 0) parts.push(toolCount === 1 ? '1 Tool' : `${toolCount} Tools`);
  if (anonReal > 0) parts.push(anonReal === 1 ? '1 Anon' : `${anonReal} Anon`);
  if (deanonReal > 0) parts.push(deanonReal === 1 ? '1 De-Anon' : `${deanonReal} De-Anon`);
  const label = parts.length ? `Aktivität · ${parts.join(' · ')}` : 'Aktivität';

  // Determine open/closed state — single shared map for tools + privacy.
  // Absent key = history load = closed (matches pre-merge tool-block UX).
  const stateVal = chat?._activityStates?.get(turnNum);
  const isOpen = stateVal === 'auto-open' || stateVal === 'user-open';

  const summaryEl = `<summary class="activity-summary-header" onclick="event.preventDefault();toggleActivitySummary(${turnNum})">
        <svg class="activity-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        ${esc(label)}
      </summary>`;

  const detailsHtml = `<details class="activity-summary"${isOpen ? ' open' : ''}>
      ${summaryEl}
      <div class="activity-summary-body">${bodyHtml}</div>
    </details>`;

  return lastResponseMemberPos === -1
    ? detailsHtml
    : `${detailsHtml}${responseHtml}`;
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
  // Round-0 preamble (metadata.preamble) is peeled off the displayed text so
  // the bubble shows only what the user typed. The collapsible "Preamble"
  // disclosure itself is rendered by renderMessages() under the turn header
  // (the turn-grouped layout shows the user message there, not as a bubble).
  const preamble = msg.metadata?.preamble;
  if (typeof preamble === 'string' && preamble && textContent.startsWith(preamble)) {
    textContent = textContent.slice(preamble.length).replace(/^\n+/, '');
  }
  // GDPR highlight overlay for the request side — mirrors the assistant
  // path but skips the markdown pipeline since user messages render as
  // plain escaped text. Gated by the same composer toggle.
  const userSpans = msg.metadata?.gdpr_restored_spans;
  const showGdpr = state.showGdprDetails && Array.isArray(userSpans) && userSpans.length;
  const userTextHtml = showGdpr
    ? renderPlainTextWithGdprHighlights(textContent, userSpans)
    : esc(textContent);
  return `
    <div class="msg-turn msg-turn-user">
      ${thumbsHtml}
      ${filesHtml}
      <div class="msg-user">${userTextHtml}</div>
      <div class="msg-actions-bar">
        <button class="msg-action-btn" onclick="toggleMsgEditMenu(event, ${idx})" title="Verlauf bearbeiten">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>
        </button>
        <div class="msg-edit-dropdown" id="msg-edit-menu-${idx}">
          <div class="msg-edit-dropdown-item" onclick="deleteMessages('turn', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 6h18M8 6V4h8v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>
            Frage-Antwort-Paar entfernen
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('after', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="20" x2="22" y2="20" stroke-dasharray="3 3"/></svg>
            Alle danach entfernen
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('before', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="4" x2="22" y2="4" stroke-dasharray="3 3"/></svg>
            Alle davor entfernen
          </div>
        </div>
      </div>
    </div>
  `;
}
function renderAssistantMessage(msg, idx) {
  const content = typeof msg.content === 'string' ? msg.content : '';
  // GDPR highlight overlay is gated by the composer toggle. When off
  // (privacy-first default), the reply renders identically to a non-
  // anonymised one — no yellow tint, no tooltip. Toggle on → restored
  // spans get `<mark class="gdpr-restored">` with category/value tooltip.
  const gdprSpans = msg.metadata?.gdpr_restored_spans;
  const showGdpr = state.showGdprDetails && Array.isArray(gdprSpans) && gdprSpans.length;
  const rendered = showGdpr
    ? renderMarkdownWithGdprHighlights(content, gdprSpans)
    : renderMarkdown(content);

  let thinkingHtml = '';
  if (msg._thinking) {
    const summaryNote = msg._thinkingSummary?.reasoning_tokens
      ? `<span style="margin-left:8px;opacity:0.7;font-size:11px;">${msg._thinkingSummary.reasoning_tokens.toLocaleString()} tok</span>`
      : '';
    thinkingHtml = `
      <div class="thinking-block" onclick="this.classList.toggle('open')">
        <div class="thinking-block-header">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/></svg>
          Denken${summaryNote}
        </div>
        <div class="thinking-block-body msg-content">${renderMarkdown(msg._thinking)}</div>
      </div>
    `;
  } else if (msg._thinkingSummary?.reasoning_tokens) {
    // Opaque reasoning: provider burned tokens on thinking but didn't return the text.
    // Render a non-expandable badge so the user knows it happened.
    const n = msg._thinkingSummary.reasoning_tokens.toLocaleString();
    thinkingHtml = `
      <div class="thinking-block" style="cursor:default;opacity:0.75;" title="Provider hat die Anzahl der Reasoning-Token zurückgegeben, aber nicht den Text (verdecktes Denken).">
        <div class="thinking-block-header">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/></svg>
          ${n} Token nachgedacht
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
      const badge = f.action === 'created' ? '<span class="fa-badge created">neu</span>' : f.action === 'modified' ? '<span class="fa-badge modified">bearbeitet</span>' : '';
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

  // Manual web-search: the curated sources this turn used, each with its FULL
  // fetched content — fetched fresh per turn and ephemeral on the wire (never
  // replayed from history), surfaced here from metadata.web_sources. Each
  // source renders as its own expandable result (title → URL → content), like
  // a web_fetch tool-call result. Re-sending the same prompt later shows a
  // DIFFERENT block (e.g. today's vs tomorrow's weather).
  let webSourcesHtml = '';
  const webSrc = msg.metadata?.web_sources;
  if (Array.isArray(webSrc) && webSrc.length) {
    const items = webSrc.map(s => {
      let host = s.url || '';
      try { host = new URL(s.url).hostname.replace(/^www\./, ''); } catch (e) {}
      const inner = s.error
        ? `<div class="msg-web-source-error">⚠ Abruf fehlgeschlagen: ${esc(s.error)}</div>`
        : `<pre class="msg-web-source-content">${esc(s.content || '')}</pre>`;
      const chars = s.content ? ` · ${(s.content.length).toLocaleString()} Zeichen` : '';
      return `
        <details class="msg-web-source">
          <summary class="msg-web-source-summary">
            <span class="msg-web-source-title">${esc(s.title || s.url)}</span>
            <a class="msg-web-source-host" href="${esc(s.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(host)}↗</a>
          </summary>
          <div class="msg-web-source-meta">${esc(s.url)}${chars}</div>
          ${inner}
        </details>`;
    }).join('');
    webSourcesHtml = `
      <details class="msg-web-sources">
        <summary class="msg-web-sources-summary">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
          <span>Webquellen dieser Anfrage</span>
          <span class="msg-web-sources-count">${webSrc.length}</span>
        </summary>
        <div class="msg-web-sources-body">${items}</div>
      </details>`;
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

  // Per-turn stats line (model · duration · speed · cost).
  // metadata.cost is *cumulative* session cost as of this turn; per-turn cost
  // is the delta against the previous assistant turn's cumulative snapshot.
  // Mirrors session-inspector header (chat.js:777).
  let turnStatsHtml = '';
  const meta = msg.metadata;
  if (meta && (meta.model || meta.duration || meta.tokens_out || msg._cost !== undefined)) {
    const allMsgs = state.activeChat?.messages || [];
    let prevCum = 0;
    for (let pi = idx - 1; pi >= 0; pi--) {
      if (allMsgs[pi]?.role === 'assistant' && allMsgs[pi]._cost !== undefined) {
        prevCum = allMsgs[pi]._cost || 0;
        break;
      }
    }
    const cum = (typeof msg._cost === 'number') ? msg._cost : (meta.cost || 0);
    const turnCost = Math.max(0, cum - prevCum);
    const dur = meta.duration || 0;
    const tokIn = meta.tokens_in || 0;
    const tokOut = meta.tokens_out || 0;
    const speed = (dur > 0 && tokOut > 0) ? Math.round(tokOut / dur) : null;
    const parts = [];
    if (meta.model) parts.push(esc(modelShortName(meta.model, false) || meta.model));
    if (dur > 0) parts.push(dur.toFixed(1) + 's');
    if (speed) parts.push(speed + ' tok/s');
    if (turnCost > 0) parts.push('$' + turnCost.toFixed(4));
    if (tokIn || tokOut) parts.push(`${tokIn.toLocaleString()} in / ${tokOut.toLocaleString()} out`);
    // Thinking level (mirrors session-inspector badges, chat.js:756–758).
    // metadata.thinking_level is set per turn; absence with no _thinking → 'none'.
    const tLvl = meta.thinking_level || (msg._thinking || meta.thinking ? 'on' : '');
    if (tLvl && tLvl !== 'none') parts.push('thinking: ' + esc(tLvl));
    // Caveman modes (sys / chat). Names mirror inspector (chat.js:762).
    const cavName = n => ({1: 'lite', 2: 'full', 3: 'ultra'})[n] || '';
    const cavSys = parseInt(meta.caveman_system) || 0;
    const cavChat = parseInt(meta.caveman_chat) || 0;
    const cavParts = [];
    if (cavSys) cavParts.push('sys ' + cavName(cavSys));
    if (cavChat) cavParts.push('chat ' + cavName(cavChat));
    if (cavParts.length) parts.push('caveman: ' + cavParts.join(' / '));
    if (parts.length) {
      turnStatsHtml = `<span class="msg-turn-stats" title="Modell · Dauer · Geschwindigkeit · Kosten der Anfrage · Token ein/aus · Denken · Caveman">${parts.join(' · ')}</span>`;
    }
  }

  return `
    <div class="msg-turn msg-turn-assistant">
      ${thinkingHtml}
      <div class="msg-assistant msg-content">${rendered}</div>
      ${filesHtml}
      ${webSourcesHtml}
      ${refsHtml}
      <div class="msg-actions-bar">
        ${turnStatsHtml}
        <button class="msg-action-btn" onclick="copyMessage(${idx})" title="Kopieren">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
        </button>
        <button class="msg-action-btn" onclick="retryMessage(${idx})" title="Erneut versuchen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
        </button>
        ${messageUsedKnowledge(idx) ? `<button class="msg-action-btn" onclick="openUsedMemoryGraph(${idx})" title="Verwendete Erinnerungen und Beziehungen anzeigen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><line x1="7.7" y1="7.5" x2="10.3" y2="16.5"/><line x1="16.3" y1="7.5" x2="13.7" y2="16.5"/><line x1="8.5" y1="6" x2="15.5" y2="6"/></svg>
        </button>` : ''}
        <button class="msg-action-btn" onclick="toggleTurnNavMenu(event, ${idx})" title="Anfragen: zuklappen / springen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
        </button>
        <div class="msg-edit-dropdown turn-nav-dropdown" id="turn-nav-menu-${idx}"></div>
        <button class="msg-action-btn" onclick="toggleMsgEditMenu(event, ${idx})" title="Verlauf bearbeiten">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>
        </button>
        <div class="msg-edit-dropdown" id="msg-edit-menu-${idx}">
          <div class="msg-edit-dropdown-item" onclick="deleteMessages('response', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 18L18 6M6 6l12 12"/></svg>
            Diese Antwort entfernen
          </div>
          <div class="msg-edit-dropdown-item" onclick="deleteMessages('turn', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 6h18M8 6V4h8v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>
            Frage-Antwort-Paar entfernen
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('after', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="20" x2="22" y2="20" stroke-dasharray="3 3"/></svg>
            Alle danach entfernen
          </div>
          <div class="msg-edit-dropdown-item destructive" onclick="deleteMessages('before', ${idx})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v20M2 12l4-4 4 4M14 8l4 4 4-4"/><line x1="2" y1="4" x2="22" y2="4" stroke-dasharray="3 3"/></svg>
            Alle davor entfernen
          </div>
        </div>
        <button class="msg-action-btn" onclick="toggleMsgMemoryMenu(event, ${idx})" title="Speicher">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10l9-6 9 6v1H3z" fill="currentColor" fill-opacity="0.15"/><line x1="3" y1="11" x2="21" y2="11"/><line x1="3" y1="21" x2="21" y2="21"/><line x1="5" y1="11" x2="5" y2="21"/><line x1="11" y1="11" x2="11" y2="21"/><line x1="17" y1="11" x2="17" y2="21"/><line x1="19" y1="11" x2="19" y2="21"/></svg>
        </button>
        <div class="msg-edit-dropdown" id="msg-memory-menu-${idx}">
          ${renderMemoryMenuItems(idx)}
        </div>
      </div>
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
      ? `Position ${q.position} von ${q.waiting}`
      : 'als Nächstes';
    const provTxt = q.provider ? ` bei <code>${esc(q.provider)}</code>` : '';
    const activeTxt = q.active ? ` · ${q.active}/${q.max_concurrent || '?'} aktiv` : '';
    const waitSec = Math.max(0, Math.round((q.wait_ms || 0) / 1000));
    const html = `<div class="msg-turn msg-turn-assistant msg-streaming">
      <div class="msg-assistant msg-content" style="color:var(--text-muted);font-style:italic">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-2px;margin-right:4px">
          <circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>
        </svg>
        In Warteschlange${provTxt} — ${posTxt}${activeTxt} · ${waitSec}s
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
      return `<div class="code-block-header"><span>${esc(langLabel)}</span><button class="code-copy-btn" onclick="copyCodeBlock(this)">Kopieren</button></div><pre><code${cls || ''}>`;
    });

    // Restore citations as compact pin buttons (don't disrupt text flow)
    html = restoreCitationPins(html, citations);

    return html;
  } catch(e) {
    return esc(text);
  }
}
// GDPR highlight sentinels — invisible separators that survive marked.parse
// unchanged so we can post-process them back into <mark> tags.
const GDPR_SENTINEL_OPEN = '⁣GDPR⁣';   // U+2063 INVISIBLE SEPARATOR
const GDPR_SENTINEL_CLOSE = '⁣/GDPR⁣';
// Internal id/value delimiter. Must NOT be `|` — markdown table cells use
// `|` as a separator, so a literal pipe inside the open sentinel breaks
// the sentinel when a restored value sits inside a table row.
const GDPR_SENTINEL_DELIM = '⁣';        // U+2063 INVISIBLE SEPARATOR
// Human-readable category labels for the tooltip. Falls back to the rule_id
// itself when not mapped (covers future detectors without a UI release).
const GDPR_CATEGORY_LABELS = {
  email: 'E-Mail-Adresse',
  iban: 'IBAN',
  phone: 'Telefonnummer',
  credit_card: 'Kreditkartennummer',
  steuer_id: 'Steuer-ID',
  steuernummer: 'Steuernummer',
  ssn: 'Sozialversicherungsnummer',
  passport: 'Reisepass',
  nhs_number: 'NHS-Nummer',
  bsn: 'BSN',
  taj: 'TAJ',
  cnp: 'CNP',
  rrn: 'RRN',
  aadhaar: 'Aadhaar',
  pesel: 'PESEL',
  api_key: 'API-Key',
  ip_address: 'IP-Adresse',
  date_of_birth: 'Geburtsdatum',
  unknown: 'Personenbezogener Wert',
};
function renderMarkdownWithGdprHighlights(text, spans) {
  // Pre-inject sentinels around each restored span in the raw text, then run
  // the normal markdown pipeline, then swap sentinels for <mark> tags after.
  // This piggy-backs on the same trick the citation extractor uses — the
  // markdown parser doesn't touch invisible separators, so spans cross
  // through marked.parse intact.
  if (!text || !spans || !spans.length) return renderMarkdown(text || '');
  // Re-locate every span client-side by value. Server-side offsets come
  // from Python (Unicode code-point indexing); JavaScript indexes by UTF-16
  // code units, so any non-BMP character (emoji, rare CJK) in the reply
  // shifts every downstream offset by +1 in JS land. Rather than try to
  // re-encode, we walk the text and find each original by-value — same
  // first-match-wins discipline the server uses, just re-anchored here.
  // Longest-first so a longer value claims its span before any shorter
  // substring of it gets a chance (mirrors `find_restored_spans`).
  const byLen = spans.slice().sort((a, b) => (b.original || '').length - (a.original || '').length);
  const claimed = []; // [start, end]
  const overlaps = (s, e) => claimed.some(([cs, ce]) => s < ce && e > cs);
  const located = []; // {start, end, original, fake, category}
  for (const sp of byLen) {
    const orig = sp.original;
    if (!orig) continue;
    let from = 0;
    while (true) {
      const i = text.indexOf(orig, from);
      if (i < 0) break;
      const j = i + orig.length;
      if (!overlaps(i, j)) {
        located.push({ start: i, end: j, original: orig, fake: sp.fake || '', category: sp.category || 'unknown' });
        claimed.push([i, j]);
      }
      from = j;
    }
  }
  if (!located.length) return renderMarkdown(text);
  // Sort descending by start so splicing from the end keeps earlier
  // offsets stable.
  located.sort((a, b) => b.start - a.start);
  const tooltips = [];
  let out = text;
  for (const sp of located) {
    const id = tooltips.length;
    tooltips.push({ original: sp.original, fake: sp.fake, category: sp.category });
    out = out.substring(0, sp.start)
        + GDPR_SENTINEL_OPEN + id + GDPR_SENTINEL_DELIM
        + out.substring(sp.start, sp.end)
        + GDPR_SENTINEL_CLOSE
        + out.substring(sp.end);
  }
  let html = renderMarkdown(out);
  // Replace sentinels with <mark> tags. The id between OPEN and the
  // delimiter tells us which tooltip to attach. The content between the
  // delimiter and CLOSE is the (already HTML-escaped by marked) restored
  // value.
  const re = new RegExp(
    GDPR_SENTINEL_OPEN.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      + '(\\d+)'
      + GDPR_SENTINEL_DELIM.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      + '([\\s\\S]*?)'
      + GDPR_SENTINEL_CLOSE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
    'g'
  );
  html = html.replace(re, (_match, idStr, inner) => {
    const t = tooltips[parseInt(idStr, 10)];
    if (!t) return inner;
    const label = GDPR_CATEGORY_LABELS[t.category] || t.category || 'Personenbezogener Wert';
    // Tooltip text — line breaks via &#10; render in native title tooltips.
    const tip = `${label} — "${t.original}" wurde mit "${t.fake}" anonymisiert`;
    return `<mark class="gdpr-restored" data-category="${esc(t.category)}" title="${esc(tip)}">${inner}</mark>`;
  });
  return html;
}
// Non-markdown variant for the user-message bubble. User messages render
// as plain escaped text (no markdown pipeline), so we splice the <mark>
// tags directly. Same longest-first / claim-non-overlapping discipline as
// the markdown variant, just without the sentinel dance.
function renderPlainTextWithGdprHighlights(text, spans) {
  if (!text || !spans || !spans.length) return esc(text || '');
  const byLen = spans.slice().sort((a, b) => (b.original || '').length - (a.original || '').length);
  const claimed = [];
  const overlaps = (s, e) => claimed.some(([cs, ce]) => s < ce && e > cs);
  const located = [];
  for (const sp of byLen) {
    const orig = sp.original;
    if (!orig) continue;
    let from = 0;
    while (true) {
      const i = text.indexOf(orig, from);
      if (i < 0) break;
      const j = i + orig.length;
      if (!overlaps(i, j)) {
        located.push({ start: i, end: j, original: orig, fake: sp.fake || '', category: sp.category || 'unknown' });
        claimed.push([i, j]);
      }
      from = j;
    }
  }
  if (!located.length) return esc(text);
  located.sort((a, b) => a.start - b.start);
  let out = '';
  let cursor = 0;
  for (const sp of located) {
    out += esc(text.substring(cursor, sp.start));
    const label = GDPR_CATEGORY_LABELS[sp.category] || sp.category || 'Personenbezogener Wert';
    const tip = `${label} — "${sp.original}" wurde mit "${sp.fake}" anonymisiert`;
    out += `<mark class="gdpr-restored" data-category="${esc(sp.category)}" title="${esc(tip)}">${esc(text.substring(sp.start, sp.end))}</mark>`;
    cursor = sp.end;
  }
  out += esc(text.substring(cursor));
  return out;
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
  return `<button type="button" class="citation-pin" data-citation="${esc(data)}" title="${esc(tip)}" onclick="openCitationPopover(this, event)" aria-label="Quelle: ${esc(file)}">${bookSvg}</button>`;
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
