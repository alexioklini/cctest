/* ═══════════════════════════════════════════════════════════
   CHAT VIEW MANAGEMENT
   ═══════════════════════════════════════════════════════════ */
function updateChatView() {
  const chat = state.activeChat;
  if (!chat) return;

  // Update header — show agent name, then chat title (editable on click)
  const agent = state.agents.find(a => (a.id || a.name) === state.activeAgentId);
  const agentDisplay = agent?.display_name || state.activeAgentId || 'Chat';
  const chatTitle = chat.chatTitle || '';
  // Build the favourite descriptor for this chat (skipped for not-yet-saved
  // chats and for synthetic scheduled-run sessions, which the user pins via
  // their schedule entry instead).
  const sid = chat.sessionId || '';
  const isSynthetic = sid.startsWith('sched-') || chat.readonly;
  const favOpts = (sid && !isSynthetic) ? {
    item_type: state.currentProject ? 'project_chat' : 'chat',
    item_id: sid,
    agent_id: chat.agentId || state.activeAgentId || 'main',
    title: chatTitle || 'Untitled chat',
  } : null;
  if (state.currentProject) {
    const projAgent = chat.agentId || state._projectDetailAgent || state.activeAgentId;
    updatePageHeader(chatTitle || agentDisplay, state.currentProject, projAgent, favOpts);
  } else if (chatTitle) {
    updatePageHeader(chatTitle, agentDisplay, null, favOpts);
  } else {
    updatePageHeader(agentDisplay, null, null, favOpts);
  }

  // Update model
  updateModelSelectorDisplay(chat.model);

  // Update status bar
  updateStatusBar();

  // Render messages
  renderMessages();
  scrollToBottom();
}

// Role-gate: admins see everything; powerusers lose pool/queue (infra-internal);
// users additionally lose tokens in/out, speed, cost, and the inspect button.
// Returns the role string so callers can short-circuit later sections.
function applyStatusBarRoleVisibility() {
  const role = (state.authUser && state.authUser.role) || 'admin';
  const hide = (id, on) => { const el = document.getElementById(id); if (el) el.style.display = on ? 'none' : ''; };
  const isUser = role === 'user';
  const isPower = role === 'poweruser';
  hide('status-tokens-in-wrap',  isUser);
  hide('status-tokens-out-wrap', isUser);
  hide('status-speed-wrap',      isUser);
  hide('status-cost-wrap',       isUser);
  hide('status-inspect-btn',     isUser);
  // Pool + queue: hidden for powerusers and users (admins-only).
  // Their monitors set display:flex on poll — see _renderPoolIndicator + QueueMonitor._render.
  if (isUser || isPower) {
    hide('status-warmpool', true);
    hide('status-queue', true);
  }
  return role;
}

function updateStatusBar() {
  const chat = state.activeChat;
  if (!chat) return;

  document.getElementById('status-agent').textContent = state.activeAgentId || '';
  document.getElementById('status-model').textContent = '';
  document.getElementById('status-session').textContent = chat.sessionId ? (chat.sessionId.startsWith('sched-') ? chat.sessionId : chat.sessionId.substring(0,8)) : '';

  // Save-to-memory toggle: green=on, amber=auto, grey=off
  // Mirror the same state to both chat- and welcome-screen composers.
  const mode = chat.memoryMode || (chat.saveToMemory ? 'on' : 'off');
  for (const memBtn of _composerToggleEls('btn-save-to-memory')) {
    if (mode === 'on') {
      memBtn.style.color = 'var(--success, #22c55e)';
      memBtn.title = 'Memory: on (all messages saved) — click to cycle';
    } else if (mode === 'auto') {
      const clf = state.mempalaceClassifier || {};
      const detail = clf.enabled && clf.model
        ? `LLM classifier: ${modelShortName(clf.model, false)}`
        : clf.min_turns ? `min ${clf.min_turns} turns` : 'default rules';
      memBtn.style.color = 'var(--warning, #f59e0b)';
      memBtn.title = `Memory: auto (${detail}) — click to cycle`;
    } else {
      memBtn.style.color = '';
      memBtn.title = 'Memory: off — click to cycle';
    }
  }

  // Caveman mode toggle: icon per level (off=spaceship, lite=car, full=horse, ultra=campfire)
  const cm = chat.cavemanMode || 0;
  const cavTitle = {
    0: 'Caveman: off (spaceship) — click to cycle',
    1: 'Caveman: lite (car) — click to cycle',
    2: 'Caveman: full (horse) — click to cycle',
    3: 'Caveman: ultra (campfire) — click to cycle',
  }[cm];
  for (const cavBtn of _composerToggleEls('btn-caveman')) {
    cavBtn.innerHTML = cavemanIconFor(cm);
    cavBtn.title = cavTitle;
    cavBtn.style.color = '';
  }

  // Transparent-anonymisation sticky preference indicator (step 6.3). Shows
  // a shield-with-checkmark next to the composer when chat.gdprActionPref
  // is set, so the user sees PII handling is automatic for this chat and
  // can reset with a click. Hidden when no preference is active.
  const gdprPref = (chat.gdprActionPref || '').trim();
  const gdprLabel = {
    'anonymise':   'Auto-anonymising PII before send',
    'local_model': 'Auto-routing PII messages to local model',
    'continue':    'Auto-continuing past PII warnings',
  }[gdprPref] || '';
  for (const gBtn of _composerToggleEls('btn-gdpr-pref')) {
    if (gdprPref && gdprLabel) {
      gBtn.style.display = '';
      gBtn.title = `${gdprLabel} — click to reset`;
    } else {
      gBtn.style.display = 'none';
    }
  }

  // Warmup indicator
  let warmupEl = document.getElementById('status-warmup');
  if (!warmupEl) {
    warmupEl = document.createElement('div');
    warmupEl.id = 'status-warmup';
    warmupEl.className = 'status-item';
    warmupEl.innerHTML = '<span style="font-size:11px;color:#8b5cf6;font-weight:500;animation:pulse 1s infinite">Warming up...</span>';
    document.getElementById('status-bar').insertBefore(warmupEl, document.getElementById('status-bar').children[2]);
  }
  warmupEl.style.display = chat._warmingUp ? '' : 'none';
  // Welcome view warmup indicator
  const wwEl = document.getElementById('welcome-warmup');
  if (wwEl) wwEl.style.display = chat._warmingUp ? '' : 'none';

  // Compute token totals from message metadata
  let totalIn = 0, totalOut = 0, lastSpeed = null;
  const msgs = chat.messages || [];
  for (const m of msgs) {
    if (m.role === 'assistant' && m.metadata) {
      totalIn += m.metadata.tokens_in || 0;
      totalOut += m.metadata.tokens_out || 0;
      if (m.metadata.duration > 0 && m.metadata.tokens_out > 0) {
        lastSpeed = Math.round(m.metadata.tokens_out / m.metadata.duration);
      }
    }
  }
  // Also use chat-level tracking from done events
  if (chat._tokensIn) totalIn = chat._tokensIn;
  if (chat._tokensOut) totalOut = chat._tokensOut;
  if (chat._lastSpeed) lastSpeed = chat._lastSpeed;

  document.getElementById('status-tokens-in').textContent = totalIn ? totalIn.toLocaleString() : '0';
  document.getElementById('status-tokens-out').textContent = totalOut ? totalOut.toLocaleString() : '0';
  document.getElementById('status-speed').textContent = lastSpeed ? `${lastSpeed} tok/s` : '-';

  // Context fill bar — use last API tokens_in as real context usage
  const wrap = document.getElementById('status-context-wrap');
  const fill = document.getElementById('status-context-fill');
  const label = document.getElementById('status-context-label');
  // Find last-round prompt tokens (= size of the most recent API call, not cumulative across tool rounds)
  let lastApiIn = 0;
  for (let mi = msgs.length - 1; mi >= 0; mi--) {
    const md = msgs[mi].metadata;
    if (msgs[mi].role === 'assistant' && md && (md.last_tokens_in || md.tokens_in)) {
      lastApiIn = md.last_tokens_in || md.tokens_in;
      break;
    }
  }
  if (chat._lastApiIn) lastApiIn = chat._lastApiIn;
  const contextUsed = lastApiIn || chat.totalTokens || 0;
  const modelMaxContext = (state.modelsConfig?.models?.[chat.model]?.max_context) || 0;
  const effectiveMaxContext = modelMaxContext || chat.maxContext;
  if (contextUsed > 0 && effectiveMaxContext) {
    const pct = Math.min(100, Math.round(contextUsed / effectiveMaxContext * 100));
    wrap.style.display = '';
    fill.style.width = Math.max(pct, 1) + '%';
    fill.className = 'context-fill' + (pct >= 80 ? ' danger' : pct >= 50 ? ' warn' : '');
    const fmtK = (n) => n >= 1000 ? (n/1000).toFixed(1) + 'K' : n.toString();
    label.textContent = `${fmtK(contextUsed)} / ${fmtK(effectiveMaxContext)} (${pct}%)`;
    label.title = `${contextUsed.toLocaleString()} / ${effectiveMaxContext.toLocaleString()} tokens (last API input)`;

    // LCM warning banner: show at ≥60% — compaction is manual-only, so the
    // banner stays visible until the user runs ✂️ Compact or the conversation
    // resets.
    const banner = document.getElementById('lcm-warn-banner');
    if (banner) {
      const isStreaming = !!document.getElementById('stop-btn')?.offsetParent;
      if (pct >= 60 && !isStreaming) {
        const txt = document.getElementById('lcm-warn-text');
        if (txt) txt.textContent = `Context is ${pct}% full — compact now to keep the conversation going.`;
        banner.classList.add('visible');
      } else {
        banner.classList.remove('visible');
      }
    }
  } else {
    wrap.style.display = 'none';
    document.getElementById('lcm-warn-banner')?.classList.remove('visible');
  }

  // Session cost indicator — shows current session $ spend. Quota state
  // (with role-based thresholds + cycle reset) lives in the Plan-usage pill.
  const costWrap = document.getElementById('status-cost-wrap');
  const costLabel = document.getElementById('status-cost-label');
  let sessionCost = 0;
  let sawCostField = false;
  for (let mi = msgs.length - 1; mi >= 0; mi--) {
    const m = msgs[mi];
    if (m.role === 'assistant' && m._cost !== undefined) {
      sessionCost = m._cost || 0;
      sawCostField = true;
      break;
    }
  }
  if (chat._sessionCost !== undefined) { sessionCost = chat._sessionCost || 0; sawCostField = true; }
  if (sawCostField) {
    costWrap.style.display = '';
    if (sessionCost <= 0) {
      costLabel.textContent = '0.00';
      costLabel.style.color = 'var(--text-400)';
      costWrap.title = 'Session cost: $0.00 — no pricing configured for this model. Set cost_input/cost_output in Settings → Models.';
    } else {
      costLabel.textContent = sessionCost < 1 ? sessionCost.toFixed(3) : sessionCost.toFixed(2);
      costLabel.style.color = '';
      costWrap.title = `Session cost: $${sessionCost.toFixed(4)}`;
    }
  } else {
    costWrap.style.display = 'none';
  }

  // Final pass: hide role-restricted items. Runs last so it wins over the
  // data-driven branches above (e.g. cost-wrap re-show on cost data arrival).
  applyStatusBarRoleVisibility();
}

/* ─── GDPR / PII detection modal ───────────────────────────── */
// Auto-displayed during an interactive chat when the PII scanner flags personal
// data in the outgoing message or its text attachments. Lists exactly which
// fragments tripped which detector (partially masked), then offers a set of
// ways forward. Resolves to a string verdict:
//   'cancel'           — abort, don't send
//   'local'            — switch to a local model and send unchanged
//   'send'             — send to the selected model anyway (only when not a
//                        hard block, or a local model is already active)
//   'auto-anon'        — (not yet implemented)
//   'manual-anon'      — (not yet implemented)
//   'auto-anon-deanon' — (not yet implemented)
// `localActive` = true when the currently selected model is already local
// (so the data would stay on-prem); controls whether a hard block can proceed.
function gdprActionModal(scan, chat, localActive) {
  return new Promise((resolve) => {
    const isBlock = scan.worstAction === 'block';
    // A hard block can only proceed if a local model is already active. Cloud +
    // block → the user must pick "execute via local model" (or cancel/anonymise
    // once that lands). Warn-level (or block+local) keeps "send anyway".
    const canSend = !isBlock || localActive;
    // Inject the PII modal's one-off styles once per page lifetime.
    if (!document.getElementById('pii-modal-styles-v3')) {
      document.getElementById('pii-modal-styles')?.remove();
      document.getElementById('pii-modal-styles-v2')?.remove();
      const style = document.createElement('style');
      style.id = 'pii-modal-styles-v3';
      style.textContent = `
        @keyframes pii-fade-in { from{opacity:0} to{opacity:1} }
        @keyframes pii-pop-in  { from{opacity:0;transform:translateY(8px) scale(.985)} to{opacity:1;transform:translateY(0) scale(1)} }
        .pii-overlay {
          position:fixed; inset:0; z-index:9999;
          display:flex; align-items:center; justify-content:center;
          background:rgba(20,18,16,.52);
          backdrop-filter:blur(4px);
          -webkit-backdrop-filter:blur(4px);
          animation:pii-fade-in .15s ease-out;
          padding:20px;
        }
        .pii-card {
          width:min(560px, 100%);
          max-height:min(82vh, 720px);
          display:flex; flex-direction:column;
          background:var(--bg-000, #faf9f7);
          border-radius:14px;
          box-shadow:0 20px 50px -16px rgba(31,30,29,.32), 0 0 0 1px rgba(31,30,29,.06);
          overflow:hidden;
          animation:pii-pop-in .18s cubic-bezier(.2,.8,.2,1);
        }
        .pii-header {
          display:flex; align-items:flex-start; gap:14px;
          padding:20px 24px 16px;
          border-bottom:1px solid var(--border-100);
        }
        .pii-shield {
          flex:none;
          width:36px; height:36px; border-radius:9px;
          background:#fef3c7; color:#b45309;
          display:flex; align-items:center; justify-content:center;
          margin-top:1px;
        }
        .pii-header.is-block .pii-shield { background:#fee2e2; color:#b91c1c; }
        .pii-header-text { flex:1; min-width:0; }
        .pii-title { font-size:15.5px; font-weight:600; letter-spacing:-.005em; line-height:1.3; margin:0; color:var(--text-000); }
        .pii-subtitle { font-size:12.5px; margin:3px 0 0; color:var(--text-300); line-height:1.45; }
        .pii-stat {
          flex:none;
          font-size:11px; font-weight:600;
          padding:4px 10px; border-radius:999px;
          background:#fef3c7; color:#92400e;
          letter-spacing:.01em;
          white-space:nowrap;
          margin-top:2px;
        }
        .pii-header.is-block .pii-stat { background:#fee2e2; color:#991b1b; }
        .pii-body { flex:1 1 auto; overflow-y:auto; padding:14px 24px 16px; }
        .pii-source-card {
          padding:10px 12px;
          border:1px solid var(--border-100);
          border-radius:10px;
          background:var(--bg-050, var(--bg-100));
          margin-top:8px;
        }
        .pii-source-card:first-child { margin-top:0; }
        .pii-source-head {
          display:flex; align-items:center; justify-content:space-between;
          gap:10px; margin-bottom:6px;
        }
        .pii-source-name { font-size:12px; font-weight:600; color:var(--text-100); }
        .pii-source-count {
          font-size:10.5px; color:var(--text-300);
          background:var(--bg-200); padding:1px 7px; border-radius:999px;
        }
        .pii-finding {
          display:flex; align-items:baseline; gap:8px;
          padding:5px 0; border-top:1px solid var(--border-050, var(--border-100));
          font-size:12px; line-height:1.4;
        }
        .pii-finding:first-of-type { border-top:none; }
        .pii-finding-sev {
          flex:none; width:6px; height:6px; border-radius:50%; margin-top:5px;
          background:#d97706;
        }
        .pii-finding-sev.is-block { background:#dc2626; }
        .pii-finding-sev.is-ignore { background:var(--text-400); }
        .pii-finding-label { flex:none; font-weight:500; color:var(--text-100); min-width:130px; }
        .pii-finding-cat { flex:none; font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--text-400); }
        .pii-finding-val {
          flex:1 1 auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
          color:var(--text-300); word-break:break-all; font-size:11.5px;
        }
        .pii-finding-loc { flex:none; font-size:10px; color:var(--text-400); white-space:nowrap; }
        .pii-footer {
          flex:none;
          display:flex; flex-direction:column; gap:10px;
          padding:14px 24px 16px;
          border-top:1px solid var(--border-100);
          background:var(--bg-050, var(--bg-100));
        }
        .pii-actions {
          display:flex; align-items:center;
          gap:8px;
          flex-wrap:wrap;
        }
        .pii-actions-spacer { flex:1; }
        .pii-suppress {
          display:flex; align-items:center; gap:6px;
          font-size:11.5px; color:var(--text-300);
          cursor:pointer; user-select:none;
        }
        .pii-suppress input { margin:0; }
        .pii-btn {
          padding:7px 13px; border-radius:8px;
          font-size:12.5px; font-weight:500;
          border:1px solid transparent;
          cursor:pointer;
          transition:background .12s, border-color .12s, box-shadow .12s;
          white-space:nowrap;
        }
        .pii-btn:active { transform:translateY(1px); }
        .pii-btn[disabled] { opacity:.45; cursor:not-allowed; }
        .pii-btn-text {
          background:transparent; border-color:transparent;
          color:var(--text-300); padding:7px 8px;
        }
        .pii-btn-text:hover:not([disabled]) { color:var(--text-100); background:var(--bg-200); }
        .pii-btn-secondary {
          background:var(--bg-000);
          border-color:var(--border-200);
          color:var(--text-100);
        }
        .pii-btn-secondary:hover:not([disabled]) { background:var(--bg-200); }
        .pii-btn-warn {
          background:transparent;
          border-color:#fbbf24;
          color:#92400e;
        }
        .pii-btn-warn:hover:not([disabled]) { background:#fef3c7; }
        .pii-btn-primary {
          background:#047857; color:#fff;
          border-color:#047857;
        }
        .pii-btn-primary:hover:not([disabled]) { background:#065f46; border-color:#065f46; }
      `;
      document.head.appendChild(style);
    }

    // Mask a matched value: keep a couple of edge chars, dot out the middle.
    const mask = (m) => {
      if (!m) return '';
      if (m.length <= 6) return m[0] + '•'.repeat(Math.max(0, m.length - 1));
      return m.slice(0, 2) + '•'.repeat(m.length - 4) + m.slice(-2);
    };
    const sevClass = (a) => a === 'block' ? ' is-block' : (a === 'ignore' ? ' is-ignore' : '');

    // Build a per-source breakdown. Entries that came from the server-side
    // aggregated `groups` carry `count` + `samples`; render one row per
    // rule_id with the total + up to 3 sample previews. Plain client-side
    // findings (text or legacy file scan) still render per-fragment.
    const sections = [];
    for (const [source, findings] of Object.entries(scan.bySource)) {
      const isAggregated = findings.length > 0 && typeof findings[0].count === 'number';
      let rows = '';
      let total = 0;
      if (isAggregated) {
        // Dedupe — `all` was inflated by count, but bySource[] still holds
        // one entry per rule_id.
        const grouped = new Map();
        for (const f of findings) {
          if (!grouped.has(f.rule_id)) grouped.set(f.rule_id, f);
        }
        const ordered = [...grouped.values()].sort((a, b) => (b.count || 0) - (a.count || 0));
        rows = ordered.map(f => {
          const action = f.action || 'warn';
          const samples = (f.samples || []).map(s => mask(s)).join(', ');
          const samplesEsc = samples ? ('<span class="pii-finding-val" style="opacity:.7">e.g. ' + esc(samples) + '</span>') : '';
          return '<div class="pii-finding">' +
            '<span class="pii-finding-sev' + sevClass(action) + '" title="' + esc(action) + '"></span>' +
            '<span class="pii-finding-label">' + esc(f.label) + '</span>' +
            '<span class="pii-finding-cat">×' + f.count + '</span>' +
            samplesEsc +
          '</div>';
        }).join('');
        for (const f of grouped.values()) total += (f.count || 0);
      } else {
        // Stable order: by position within the source, when known.
        const ordered = [...findings].sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
        rows = ordered.map(f => {
          const action = f.action || 'warn';
          const loc = Number.isFinite(f.index) ? ('char ' + f.index) : '';
          return '<div class="pii-finding">' +
            '<span class="pii-finding-sev' + sevClass(action) + '" title="' + esc(action) + '"></span>' +
            '<span class="pii-finding-label">' + esc(f.label) + '</span>' +
            '<span class="pii-finding-cat">' + esc(f.category || '') + '</span>' +
            '<span class="pii-finding-val">' + esc(mask(f.match)) + '</span>' +
            '<span class="pii-finding-loc">' + esc(loc) + '</span>' +
          '</div>';
        }).join('');
        total = findings.length;
      }
      const sourceLabel = source === 'text'
        ? 'Nachrichtentext'
        : source === 'history'
          ? 'Chat-Verlauf (frühere Turns)'
          : source.replace(/^file:/, 'Anhang · ');
      sections.push(
        '<div class="pii-source-card">' +
          '<div class="pii-source-head">' +
            '<div class="pii-source-name">' + esc(sourceLabel) + '</div>' +
            '<div class="pii-source-count">' + total + ' ' + (total === 1 ? 'Treffer' : 'Treffer') + '</div>' +
          '</div>' +
          rows +
        '</div>'
      );
    }

    const total = scan.findings.length;
    const blockCls = isBlock ? ' is-block' : '';
    const subtitle = isBlock
      ? (canSend
          ? 'Hochsensible Daten erkannt — das gewählte Modell ist lokal, die Daten verlassen das System nicht.'
          : 'Hochsensible Daten erkannt — können nicht an ein Cloud-Modell gesendet werden. Bitte Anonymisierung oder lokales Modell wählen.')
      : 'Bitte vor dem Senden prüfen — Werte sind teilweise maskiert.';
    const title = isBlock
      ? 'Hochsensible personenbezogene Daten erkannt'
      : 'Personenbezogene Daten in der Nachricht erkannt';
    const sourcesN = Object.keys(scan.bySource).length;
    const statBadge = total + ' Treffer · ' + sourcesN + ' Quelle' + (sourcesN === 1 ? '' : 'n');

    // Shield SVG (same vocabulary as the inline composer badge)
    const shieldSvg = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/><path d="M12 8v4"/><circle cx="12" cy="16" r="0.6" fill="currentColor"/></svg>';

    // Footer action hierarchy:
    //   left:  Abbrechen (text-button) + "Trotzdem senden" (warn outline, only if allowed)
    //   right: Lokales Modell (secondary) + Anonymisieren & senden (primary, focus)
    const sendBtn = canSend
      ? '<button class="pii-btn pii-btn-warn" id="pii-send-btn">Trotzdem senden</button>'
      : '';
    const modalId = 'pii-warning-modal';
    document.getElementById(modalId)?.remove();
    const html =
      '<div class="pii-overlay" id="' + modalId + '">' +
        '<div class="pii-card" role="dialog" aria-modal="true" aria-labelledby="pii-title">' +
          '<div class="pii-header' + blockCls + '">' +
            '<div class="pii-shield" aria-hidden="true">' + shieldSvg + '</div>' +
            '<div class="pii-header-text">' +
              '<h2 id="pii-title" class="pii-title">' + esc(title) + '</h2>' +
              '<p class="pii-subtitle">' + esc(subtitle) + '</p>' +
            '</div>' +
            '<div class="pii-stat">' + esc(statBadge) + '</div>' +
          '</div>' +
          '<div class="pii-body">' + sections.join('') + '</div>' +
          '<div class="pii-footer">' +
            '<div class="pii-actions">' +
              '<button class="pii-btn pii-btn-text" id="pii-cancel-btn">Abbrechen</button>' +
              sendBtn +
              '<div class="pii-actions-spacer"></div>' +
              '<button class="pii-btn pii-btn-secondary" id="pii-local-btn">Lokales Modell verwenden</button>' +
              '<button class="pii-btn pii-btn-primary" id="pii-anon-btn">Anonymisieren &amp; senden</button>' +
            '</div>' +
            '<label class="pii-suppress">' +
              '<input type="checkbox" id="pii-suppress-session"> Für diesen Chat nicht mehr fragen' +
            '</label>' +
          '</div>' +
        '</div>' +
      '</div>';
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const overlay = wrap.firstElementChild;
    document.body.appendChild(overlay);
    const cleanup = (verdict) => {
      const persist = verdict !== 'cancel' &&
        !!document.getElementById('pii-suppress-session')?.checked;
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve({ verdict, persist });
    };
    const onKey = (e) => { if (e.key === 'Escape') cleanup('cancel'); };
    document.addEventListener('keydown', onKey);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup('cancel'); });
    document.getElementById('pii-cancel-btn').onclick = () => cleanup('cancel');
    document.getElementById('pii-local-btn').onclick = () => cleanup('local');
    document.getElementById('pii-anon-btn').onclick = () => cleanup('anonymise');
    document.getElementById('pii-send-btn')?.addEventListener('click', () => cleanup('send'));
    // Default focus per design: Anonymise & continue. Falls back to Cancel
    // when there's nothing safe to default to (extremely rare edge case).
    setTimeout(() => {
      document.getElementById('pii-anon-btn')?.focus();
    }, 50);
  });
}

/** Modal shown when the server-side anonymisation step fails. The user
 *  must pick a recovery action — there is intentionally no "send to cloud
 *  anyway" path. Returns 'local_model' or 'cancel'.
 *
 *  Reuses the GDPR modal's stylesheet (`pii-modal-styles-v2`) so the
 *  recovery dialog matches the original modal visually. */
function gdprRecoveryModal(detail, chat) {
  return new Promise((resolve) => {
    const errMsg = (detail && detail.error) ? String(detail.error).slice(0, 400) : 'Unbekannter Fehler';
    const sources = (detail && Array.isArray(detail.sources)) ? detail.sources : [];
    const sourceList = sources.length
      ? '<ul style="margin:6px 0 0 18px; padding:0; font-size:12px; line-height:1.6;">' +
        sources.map(s => '<li>' + esc(s) + '</li>').join('') + '</ul>'
      : '';
    const shieldSvg = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>';
    const modalId = 'pii-recovery-modal';
    document.getElementById(modalId)?.remove();
    const html =
      '<div class="pii-overlay" id="' + modalId + '">' +
        '<div class="pii-card" role="dialog" aria-modal="true" aria-labelledby="pii-recovery-title">' +
          '<div class="pii-header is-block">' +
            '<div class="pii-shield" aria-hidden="true">' + shieldSvg + '</div>' +
            '<div class="pii-header-text">' +
              '<h2 id="pii-recovery-title" class="pii-title">Anonymisierung fehlgeschlagen</h2>' +
              '<p class="pii-subtitle">Der Originalinhalt wurde NICHT an die Cloud gesendet. Bitte Vorgehen wählen.</p>' +
            '</div>' +
          '</div>' +
          '<div class="pii-body">' +
            '<div class="pii-source-card">' +
              '<div class="pii-source-name">Fehler</div>' +
              '<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--text-200);margin-top:6px;word-break:break-word;">' +
                esc(errMsg) + '</div>' +
              (sources.length
                ? '<div style="font-size:12px;color:var(--text-300);margin-top:10px;">' +
                  'Betroffene Quelle' + (sources.length === 1 ? '' : 'n') + ':' + sourceList +
                  '</div>'
                : '') +
            '</div>' +
          '</div>' +
          '<div class="pii-footer">' +
            '<div class="pii-actions">' +
              '<button class="pii-btn pii-btn-text" id="pii-rec-cancel">Turn abbrechen</button>' +
              '<div class="pii-actions-spacer"></div>' +
              '<button class="pii-btn pii-btn-primary" id="pii-rec-local">Lokales Modell verwenden</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const overlay = wrap.firstElementChild;
    document.body.appendChild(overlay);
    const cleanup = (choice) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(choice);
    };
    const onKey = (e) => { if (e.key === 'Escape') cleanup('cancel'); };
    document.addEventListener('keydown', onKey);
    // Click-outside intentionally NOT mapped to cancel — recovery is too
    // important to dismiss accidentally. The user must pick.
    document.getElementById('pii-rec-cancel').onclick = () => cleanup('cancel');
    document.getElementById('pii-rec-local').onclick = () => cleanup('local_model');
    setTimeout(() => document.getElementById('pii-rec-local')?.focus(), 50);
  });
}

// Live inline badge — runs on composer input + history load. Shows a pill
// above the composer if PII is present in the current draft, attachments,
// or the loaded conversation history.
function updatePIIBadge() {
  const host = state.currentView === 'welcome'        ? 'welcome-composer'
             : state.currentView === 'project-detail' ? 'project-composer'
             : 'chat-composer';
  const composer = document.getElementById(host);
  if (!composer) return;
  let badge = document.getElementById('pii-inline-badge');
  if (state.piiScannerEnabled === false) {
    badge?.remove();
    return;
  }
  const input = _composerInputEl();
  const text = input?.value || '';
  const draftScan = PIIScanner.scanPayload(text, state._pendingFiles);
  const chat = state.activeChat;
  const historyHas = !!(chat && piiHistoryHasFindings(chat));
  const draftHas = draftScan.findings.length > 0;

  // Show/hide the inline composer-toolbar info button for the history-only
  // case. Compact icon + hover popover; never blocks the composer footprint.
  _updatePIIHistoryComposerBadge(chat, historyHas && !draftHas);

  if (!draftHas && !historyHas) { badge?.remove(); return; }
  // History-only case: surface via the composer-toolbar info badge above, NOT
  // the prominent pill. The pill is reserved for actionable (draft) findings.
  if (!draftHas && historyHas) { badge?.remove(); return; }

  if (!badge) {
    badge = document.createElement('div');
    badge.id = 'pii-inline-badge';
    badge.style.cssText = [
      'display:flex',
      'align-items:center',
      'gap:8px',
      'padding:7px 14px',
      'margin:0 8px 8px 8px',
      'border-radius:999px',
      'background:linear-gradient(90deg,#fef3c7,#fde68a)',
      'border:1px solid #fcd34d',
      'box-shadow:0 1px 3px rgba(180,83,9,.15)',
      'font-size:12px',
      'font-weight:500',
      'color:#78350f',
      'width:fit-content',
      'max-width:calc(100% - 16px)',
      'cursor:help',
      'transition:transform .15s',
    ].join(';');
    // Hover nudge for feedback
    badge.onmouseenter = () => { badge.style.transform = 'translateY(-1px)'; };
    badge.onmouseleave = () => { badge.style.transform = ''; };
    composer.parentElement?.insertBefore(badge, composer);
  }
  // Shield-icon SVG + formatted counts
  const icon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none"><path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/><path d="M12 8v4"/><circle cx="12" cy="16" r="0.6" fill="currentColor"/></svg>';
  // If server is configured to block PII requests, auto-swap to a local model
  // and surface that in the badge. Otherwise fall back to the original warn
  // phrasing so we don't regress existing behavior.
  const swapped = piiEnsureLocalModel();
  const blockOn = piiBlockActive(chat);
  const curLocal = chat && chat.model && isModelLocal(chat.model);

  const countsLabel = PIIScanner.formatCounts(draftScan.counts);
  const scopeLabel = 'Personenbezogene Daten in der Nachricht';

  if (blockOn && !curLocal) {
    badge.innerHTML = icon +
      '<span><b>' + esc(scopeLabel) + '</b> — bitte lokales Modell wählen (kein lokales Modell verfügbar).</span>';
    badge.style.background = 'linear-gradient(90deg,#fee2e2,#fecaca)';
    badge.style.borderColor = '#f87171';
    badge.style.color = '#7f1d1d';
    badge.title = 'Cloud-Versand vom GDPR-Scanner blockiert. Lokales Modell installieren/aktivieren oder „Anfragen mit PII blockieren" in Einstellungen → Server deaktivieren.';
  } else if (blockOn && curLocal) {
    const localName = modelShortName(chat.model);
    const countsFrag = countsLabel ? ('<b>' + esc(countsLabel) + '</b> · ') : '';
    badge.innerHTML = icon +
      '<span>' + countsFrag + esc(scopeLabel) + ' · läuft über lokales Modell <b>' + esc(localName) + '</b>' +
      (swapped ? ' (automatisch gewählt)' : '') + '</span>';
    badge.style.background = 'linear-gradient(90deg,#ecfccb,#d9f99d)';
    badge.style.borderColor = '#a3e635';
    badge.style.color = '#3f6212';
    badge.title = 'Daten verlassen das lokale Netzwerk nicht. GDPR-Sperre aktiv; die Modellauswahl ist für diesen Chat auf lokale Modelle gefiltert.';
  } else {
    const countsFrag = countsLabel ? (' · <b>' + esc(countsLabel) + '</b>') : '';
    badge.innerHTML = icon + '<span>' + esc(scopeLabel) + countsFrag + '</span>';
    badge.style.background = 'linear-gradient(90deg,#fef3c7,#fde68a)';
    badge.style.borderColor = '#fcd34d';
    badge.style.color = '#78350f';
    badge.title = 'Vor dem Senden erscheint eine Warnung. Die Prüfung erfolgt lokal im Browser.';
  }
}

// History-PII Composer-Toolbar-Badge. Sichtbar nur wenn der Chat-Verlauf
// PII enthält und der aktuelle Draft sauber ist. Hover öffnet einen kleinen
// Popover mit der Treffer-Aufschlüsselung pro Kategorie.
function _updatePIIHistoryComposerBadge(chat, shouldShow) {
  // The composer template is cloned into three mount points
  // (chat/welcome/project). Update each visible instance.
  const buttons = document.querySelectorAll('[data-id="btn-pii-history"]');
  buttons.forEach((btn) => {
    if (!shouldShow) {
      btn.style.display = 'none';
      _piiHistoryHidePopover();
      return;
    }
    btn.style.display = '';
    const counts = chat?._piiHistoryCounts || {};
    const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0);
    const fmt = PIIScanner.formatCounts(counts) || (total + ' Treffer');
    btn.setAttribute('title', 'Personenbezogene Daten im Chat-Verlauf: ' + fmt);
    btn.onmouseenter = () => _piiHistoryShowPopover(btn, counts);
    btn.onmouseleave = () => _piiHistoryHidePopover();
    btn.onfocus = () => _piiHistoryShowPopover(btn, counts);
    btn.onblur = () => _piiHistoryHidePopover();
  });
}

let _piiHistoryPopover = null;
function _piiHistoryShowPopover(anchorBtn, counts) {
  _piiHistoryHidePopover();
  const rect = anchorBtn.getBoundingClientRect();
  const entries = Object.entries(counts || {}).filter(([, v]) => (v || 0) > 0);
  if (entries.length === 0) return;
  entries.sort((a, b) => (b[1] || 0) - (a[1] || 0));
  const pop = document.createElement('div');
  pop.id = 'pii-history-popover';
  pop.style.cssText = [
    'position:fixed',
    'left:' + Math.round(rect.left) + 'px',
    'bottom:' + Math.round(window.innerHeight - rect.top + 8) + 'px',
    'z-index:9000',
    'background:var(--bg-000)',
    'color:var(--text-100)',
    'border:1px solid var(--border-200)',
    'border-radius:10px',
    'box-shadow:0 10px 28px -8px rgba(31,30,29,.25), 0 0 0 1px rgba(31,30,29,.04)',
    'padding:10px 12px',
    'font-size:12px',
    'line-height:1.45',
    'min-width:220px',
    'max-width:320px',
    'pointer-events:none',
  ].join(';');
  const total = entries.reduce((a, [, v]) => a + (v || 0), 0);
  const rows = entries.map(([k, v]) =>
    '<div style="display:flex;justify-content:space-between;gap:12px;padding:3px 0">' +
      '<span style="color:var(--text-200)">' + esc(k) + '</span>' +
      '<span style="font-weight:600;color:var(--text-100)">' + v + '</span>' +
    '</div>'
  ).join('');
  pop.innerHTML =
    '<div style="display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:6px;color:#92400e">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/><path d="M12 8v4"/><circle cx="12" cy="16" r="0.6" fill="currentColor"/></svg>' +
      'Personenbezogene Daten im Verlauf' +
    '</div>' +
    '<div style="color:var(--text-300);font-size:11.5px;margin-bottom:8px">' +
      total + ' Treffer in früheren Turns dieses Chats' +
    '</div>' +
    rows +
    '<div style="margin-top:8px;padding-top:6px;border-top:1px solid var(--border-100);color:var(--text-400);font-size:11px">' +
      'Neue Nachrichten werden vor dem Senden erneut geprüft.' +
    '</div>';
  document.body.appendChild(pop);
  _piiHistoryPopover = pop;
}
function _piiHistoryHidePopover() {
  if (_piiHistoryPopover) {
    _piiHistoryPopover.remove();
    _piiHistoryPopover = null;
  }
}

// Debounced hook — called from composer oninput + after file previews change.
let _piiBadgeTimer = null;
function schedulePIIBadgeUpdate() {
  clearTimeout(_piiBadgeTimer);
  _piiBadgeTimer = setTimeout(updatePIIBadge, 180);
}

function scrollToBottom() {
  const el = document.getElementById('messages-scroll');
  if (el) {
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }
}

/* ═══════════════════════════════════════════════════════════
   CHATS LIST
   ═══════════════════════════════════════════════════════════ */
async function loadChatsList() {
  const container = document.getElementById('chats-list');
  container.innerHTML = '<div style="padding:16px;color:var(--text-400)">Loading...</div>';

  try {
    // Load sessions for all agents
    let allSessions = [];
    for (const agent of state.agents) {
      try {
        const aid = agent.id || agent.name;
        const data = await API.getSessionsForAgent(aid, state.chatsFilter === 'archived' ? 'archived' : undefined);
        const sessions = data.sessions || [];
        for (const s of sessions) {
          if ((s.message_count || 0) > 0 && !(s.project || s.project_id)) allSessions.push({...s, agentId: aid, agentDisplay: agent.display_name || aid});
        }
        state.agentSessions[aid] = { sessions, loaded: true };
      } catch(e) {}
    }

    allSessions.sort((a,b) => new Date(b.last_active||0) - new Date(a.last_active||0));

    // Apply search filter
    if (state.chatsSearchQuery) {
      const q = state.chatsSearchQuery.toLowerCase();
      allSessions = allSessions.filter(s =>
        (s.summary || s.title || '').toLowerCase().includes(q) ||
        (s.agentId || '').toLowerCase().includes(q)
      );
    }

    container.innerHTML = '';
    for (const s of allSessions) {
      const csid = s.id || s.session_id;
      const title = s.summary || s.title || `Chat ${csid?.substring(0,8)}`;
      const div = document.createElement('div');
      div.className = 'chat-list-item';
      div.innerHTML = `
        <div class="chat-list-item-title">${esc(title)}</div>
        <div class="chat-list-item-meta">
          Last message ${relativeTime(s.last_active)}
          ${s.agentId ? ' in <span class="chat-list-item-agent">' + esc(s.agentDisplay) + '</span>' : ''}
        </div>
      `;
      div.onclick = () => openSession(csid, s.agentId);
      container.appendChild(div);
    }

    if (!allSessions.length) {
      container.innerHTML = '<div style="padding:32px;text-align:center;color:var(--text-400)">No chats found</div>';
    }
  } catch(e) {
    container.innerHTML = '<div style="padding:16px;color:var(--error)">Failed to load chats</div>';
  }
}

function filterChatsList() {
  state.chatsSearchQuery = document.getElementById('chats-search').value;
  loadChatsList();
}

function setChatFilter(filter, el) {
  state.chatsFilter = filter;
  document.querySelectorAll('.chats-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  loadChatsList();
}

/* ═══════════════════════════════════════════════════════════
   PROJECTS LIST
   ═══════════════════════════════════════════════════════════ */
/* ═══════════════════════════════════════════════════════════
   PROJECTS — Claude.ai-style list + detail + CRUD
   ═══════════════════════════════════════════════════════════ */
let _allProjectsCache = [];
let _projectsFilter = 'active';

async function loadProjectsList() {
  const list = document.getElementById('projects-list');
  list.innerHTML = '<div style="padding:16px;color:var(--text-400)">Loading...</div>';

  try {
    let allProjects = [];
    for (const agent of state.agents) {
      try {
        const aid = agent.id || agent.name;
        const data = await API.getProjects(aid);
        const projects = data.projects || [];
        for (const p of projects) {
          allProjects.push({...p, agentId: aid, agentDisplay: agent.display_name || aid});
        }
        state.agentProjects[aid] = projects;
      } catch(e) {}
    }
    _allProjectsCache = allProjects;
    renderProjectsList();
  } catch(e) {
    list.innerHTML = '<div style="padding:16px;color:var(--error)">Failed to load projects</div>';
  }
}

function renderProjectsList() {
  const list = document.getElementById('projects-list');
  const query = (document.getElementById('projects-search')?.value || '').toLowerCase();
  const sortBy = document.getElementById('projects-sort-select')?.value || 'activity';

  let filtered = _allProjectsCache.filter(p => {
    const statusMatch = _projectsFilter === 'active'
      ? (p.status || 'active') !== 'archived'
      : (p.status || 'active') === 'archived';
    if (!statusMatch) return false;
    if (query) {
      return (p.display_name || p.name || '').toLowerCase().includes(query) ||
             (p.name || '').toLowerCase().includes(query) ||
             (p.description || '').toLowerCase().includes(query);
    }
    return true;
  });

  // Sort
  if (sortBy === 'name') {
    filtered.sort((a, b) => (a.display_name || a.name || '').localeCompare(b.display_name || b.name || ''));
  } else if (sortBy === 'created') {
    filtered.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  }
  // 'activity' = default order (already sorted by filesystem)

  list.innerHTML = '';
  list.classList.add('project-grid');
  if (!filtered.length) {
    list.innerHTML = '<div class="project-grid-empty">No projects found</div>';
    return;
  }
  for (let i = 0; i < filtered.length; i++) {
    const p = filtered[i];
    const item = document.createElement('div');
    item.className = 'project-card';
    const timeAgo = p.created_at ? formatTimeAgo(new Date(p.created_at)) : '';
    const agent = p.agentId || 'main';
    const hasImage = !!p.image;
    const imgUrl = hasImage
      ? `/v1/agents/${encodeURIComponent(agent)}/projects/${encodeURIComponent(p.name)}/image`
      : '';
    // Treat the type-default emoji as "no custom icon" → render line-art glyph.
    const rawIcon = (p.icon && p.icon.length <= 4) ? p.icon : '';
    const customIcon = (rawIcon && rawIcon !== '📁') ? rawIcon : '';
    const glyphHtml = customIcon
      ? esc(customIcon)
      : (window.Favourites?.typeGlyphSvg?.('project', 44) || '');
    const artClass = hasImage ? 'project-card-art has-image' : 'project-card-art';
    const artStyle = hasImage
      ? `style="background-image:url('${esc(imgUrl)}');background-size:cover;background-position:center"`
      : '';
    const displayName = p.display_name || p.name;
    const titleAttr = p.description ? ` title="${esc(p.description)}"` : '';
    item.innerHTML = `
      <div class="${artClass}" ${artStyle}>
        ${hasImage ? '<div class="project-card-art-overlay"></div>' : ''}
        ${!hasImage ? `<span class="project-card-glyph">${glyphHtml}</span>` : ''}
        <div class="project-card-fav-slot" onclick="event.stopPropagation()"></div>
        <button class="project-card-menu" onclick="event.stopPropagation(); showProjectListMenu(event, '${esc(agent)}', '${esc(p.name)}')" title="More options">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>
        </button>
      </div>
      <div class="project-card-info">
        <div class="project-card-title"${titleAttr}>${esc(displayName)}</div>
        <div class="project-card-meta">
          <span class="project-card-type">Project</span>
          ${timeAgo ? `<span>· ${esc(timeAgo)}</span>` : ''}
        </div>
      </div>
    `;
    item.onclick = () => openProject(agent, p.name);
    if (p.id && window.Favourites?.mount) {
      const slot = item.querySelector('.project-card-fav-slot');
      if (slot) {
        window.Favourites.mount(slot, {
          item_type: 'project',
          item_id: p.id,
          agent_id: agent,
          simple: true,
        });
      }
    }
    list.appendChild(item);
  }
}

function filterProjectsList() { renderProjectsList(); }
function sortProjectsList() { renderProjectsList(); }

function setProjectFilter(filter, el) {
  _projectsFilter = filter;
  document.querySelectorAll('.projects-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  renderProjectsList();
}

function formatTimeAgo(date) {
  if (!date || isNaN(date.getTime())) return '';
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function openProject(agentId, projectName) {
  selectAgent(agentId);
  state.currentProject = projectName;
  state._projectDetailAgent = agentId;
  state._projectDetailName = projectName;
  // Default to the Active tab on entry; the tab UI also resets visually below.
  state._projectChatsFilter = 'active';
  document.querySelectorAll('.project-chats-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.pcfilter === 'active');
  });
  navigateTo('project-detail', { agentId, projectName });
  // Wire the right-pane resize handle once the view is on screen. Idempotent
  // — repeat calls are short-circuited via the handle's _bound flag.
  initProjectDetailPanelResize();
}

async function _editProjectImageUpload(ev, agentId, projectName) {
  const file = ev?.target?.files?.[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { await showAlert('Image too large (max 2 MB).'); return; }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(
      `${BASE_URL}/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`,
      { method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth-token') || ''}` },
        body: fd });
    if (!r.ok) { await showAlert(`Upload failed: ${r.status}`); return; }
    const data = await r.json();
    const preview = document.getElementById('edit-project-image-preview');
    const label   = document.getElementById('edit-project-image-label');
    const clear   = document.getElementById('edit-project-image-clear');
    if (preview) preview.style.backgroundImage = `url('/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image?v=${Date.now()}')`;
    if (label) label.textContent = 'Replace';
    if (clear) clear.style.display = 'inline-block';
    if (window._projectEditOriginal) window._projectEditOriginal.image = data.image || '';
    try { await window.Favourites?.reload?.(); } catch(_) {}
    // Refresh the projects list cache so the card reflects the new image
    // when the user closes the modal and returns to the list.
    try { await loadProjectsList(); } catch(_) {}
  } catch (e) {
    await showAlert(`Upload failed: ${e.message || e}`);
  } finally {
    ev.target.value = '';
  }
}

async function _editProjectImageClear(agentId, projectName) {
  if (!await showConfirmDanger('Remove this project image?', 'Remove Image', 'Remove')) return;
  try {
    await API.del(`/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`);
    const preview = document.getElementById('edit-project-image-preview');
    const label   = document.getElementById('edit-project-image-label');
    const clear   = document.getElementById('edit-project-image-clear');
    if (preview) preview.style.backgroundImage = '';
    if (label) label.textContent = 'Upload';
    if (clear) clear.style.display = 'none';
    if (window._projectEditOriginal) window._projectEditOriginal.image = '';
    try { await window.Favourites?.reload?.(); } catch(_) {}
    try { await loadProjectsList(); } catch(_) {}
  } catch (e) {
    await showAlert(`Remove failed: ${e.message || e}`);
  }
}

function paintProjectDetailBanner(agentId, projectName, project) {
  const banner = document.getElementById('project-detail-banner');
  const glyph  = document.getElementById('project-detail-banner-glyph');
  const remove = document.getElementById('project-detail-banner-remove');
  const label  = document.getElementById('project-detail-banner-upload-label');
  if (!banner) return;
  const palette = ['#6366f1','#8b5cf6','#0ea5e9','#10b981','#f59e0b','#ec4899','#475569','#0f172a'];
  const accent = project?.color
    || palette[(project?.name || '').split('').reduce((s,c) => s + c.charCodeAt(0), 0) % palette.length];
  const icon = (project?.icon && project.icon.length <= 4) ? project.icon : '📁';
  if (project?.image) {
    const url = `/v1/agents/${encodeURIComponent(agentId || 'main')}/projects/${encodeURIComponent(projectName)}/image?v=${Date.now()}`;
    banner.style.backgroundImage = `url('${url}')`;
    banner.style.backgroundSize = 'cover';
    banner.style.backgroundPosition = 'center';
    banner.style.background = '';
    if (glyph) glyph.style.display = 'none';
    if (remove) remove.style.display = '';
    if (label) label.textContent = 'Replace image';
  } else {
    banner.style.backgroundImage = '';
    banner.style.background = accent;
    if (glyph) { glyph.style.display = ''; glyph.textContent = icon; }
    if (remove) remove.style.display = 'none';
    if (label) label.textContent = 'Upload image';
  }
}

async function handleProjectImageUpload(ev) {
  const file = ev?.target?.files?.[0];
  if (!file) return;
  const project = state._projectDetail;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!project || !agentId || !projectName) return;
  if (file.size > 2 * 1024 * 1024) {
    await showAlert('Image too large (max 2 MB).');
    return;
  }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(
      `${BASE_URL}/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`,
      {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth-token') || ''}` },
        body: fd,
      });
    if (!r.ok) {
      await showAlert(`Upload failed: ${r.status}`);
      return;
    }
    const data = await r.json();
    project.image = data.image || '';
    paintProjectDetailBanner(agentId, projectName, project);
    // Reload the favourites cache so any favourite of this project picks up
    // the new source_image_url on next render.
    try { await window.Favourites?.reload?.(); } catch(_) {}
  } catch (e) {
    await showAlert(`Upload failed: ${e.message || e}`);
  } finally {
    ev.target.value = '';
  }
}

async function removeProjectImage() {
  const project = state._projectDetail;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!project || !agentId || !projectName) return;
  if (!await showConfirmDanger('Remove this project image?', 'Remove Image', 'Remove')) return;
  try {
    await API.del(`/v1/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(projectName)}/image`);
    project.image = '';
    paintProjectDetailBanner(agentId, projectName, project);
    try { await window.Favourites?.reload?.(); } catch(_) {}
  } catch (e) {
    await showAlert(`Remove failed: ${e.message || e}`);
  }
}

async function loadProjectDetail(agentId, projectName) {
  // Load project config
  try {
    const project = await API.getProject(agentId, projectName);
    if (!project) {
      showToast('Project not found');
      navigateTo('projects');
      return;
    }
    state._projectDetail = project;

    // Mount the favourite star + share button into the page-header.
    if (project.id) {
      updatePageHeader(project.name || projectName, null, null, {
        item_type: 'project',
        item_id: project.id,
        agent_id: agentId || 'main',
        title: project.name || projectName,
      });
    }

    // Render header
    document.getElementById('project-detail-name').textContent = project.name || projectName;
    const descEl = document.getElementById('project-detail-desc');
    if (project.description) {
      const desc = project.description;
      if (desc.length > 200) {
        descEl.innerHTML = esc(desc.slice(0, 200)) + '... <span class="project-detail-desc-toggle" onclick="toggleProjectDesc()">Show more</span>';
        descEl.dataset.full = desc;
        descEl.dataset.collapsed = 'true';
      } else {
        descEl.textContent = desc;
      }
    } else {
      descEl.innerHTML = '<span style="color:var(--text-400);font-style:italic">No description</span>';
    }

    // Render the Research / Q&A project checkbox state.
    const researchCb = document.getElementById('project-research-mode-checkbox');
    if (researchCb) {
      researchCb.checked = !!project.research_mode;
    }

    // Render instructions panel — markdown rendered, capped height with
    // vertical scroll so long default disciplines don't push attachments
    // + input folders below the fold.
    const instrEl = document.getElementById('project-panel-instructions');
    if (project.instructions) {
      instrEl.innerHTML = `<div class="project-panel-instructions-rendered">${renderMarkdown(project.instructions)}</div>`;
      instrEl.classList.remove('project-panel-placeholder');
    } else {
      instrEl.innerHTML = '<span class="project-panel-placeholder">Add instructions to customize Brain Agent\'s responses (optional, additive)</span>';
    }

    // Personalise the composer placeholder with the project name. Falls back
    // to the routing slug when the display name is missing.
    const composerInput = document.getElementById('project-input');
    if (composerInput) {
      const displayName = project.name || projectName;
      composerInput.placeholder = `Write your message to ${displayName}`;
    }

    // Load project files
    loadProjectFiles(agentId, projectName);

    // Load input folders + start polling sync status.
    loadProjectInputFolders(agentId, projectName);
    startProjectSyncPoll(agentId, projectName);

    // Load project conversations
    loadProjectChats(agentId, projectName);
  } catch(e) {
    showToast('Failed to load project');
    console.error(e);
  }
}

// ─── Project input folders + sync indicator ───────────────────────────
async function loadProjectInputFolders(agentId, projectName) {
  const container = document.getElementById('project-panel-input-folders');
  if (!container) return;
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders`);
    const folders = data.folders || [];
    // Stash for the edit modal so it doesn't need to refetch.
    state._projectInputFolders = folders;
    if (!folders.length) {
      container.innerHTML = '<span class="project-panel-placeholder">Add folders on disk to ingest into this project\'s memory. Files are scanned every 30 minutes and indexed for semantic search.</span>';
      return;
    }
    container.innerHTML = folders.map((f, idx) => {
      const fullPath = f.path || '';
      // Folder name = last path segment (or full path if there is no separator).
      const nameMatch = fullPath.replace(/\/+$/, '').split('/').filter(Boolean);
      const name = nameMatch.length ? nameMatch[nameMatch.length - 1] : fullPath;
      const recursive = f.recursive !== false;
      const autoSync = f.auto_sync !== false;  // default true for legacy entries
      return `
      <div class="project-input-folder-row">
        <div class="pif-row-head">
          <svg class="pif-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <span class="pif-name" title="${esc(fullPath)}">${esc(name)}</span>
          <button class="pif-action-btn" onclick="editProjectInputFolder(${idx})" title="Edit folder settings" aria-label="Edit">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
          </button>
          <button class="pif-action-btn pif-delete" onclick="removeProjectInputFolder(${idx})" title="Remove folder" aria-label="Remove">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
          </button>
        </div>
        <div class="pif-path" dir="ltr" title="${esc(fullPath)}">${esc(fullPath)}</div>
        <div class="pif-badges">
          <span class="pif-flag">${recursive ? 'recursive' : 'top-level'}</span>
          ${autoSync ? '' : '<span class="pif-flag" data-flag="paused" title="Excluded from automatic sync cycles — runs only on manual Sync now">auto-sync off</span>'}
          <span data-pif-pill data-pif-kind="folder" data-pif-id="${esc(fullPath)}">${projectItemPillHtml('folder', fullPath)}</span>
        </div>
      </div>
    `;}).join('');
  } catch(e) {
    container.innerHTML = '<span class="project-panel-placeholder">Failed to load input folders.</span>';
  }
}

function addProjectInputFolder() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  // Custom modal: filesystem browser (reuses the same /v1/files/tree backend
  // as the schedule modal's picker, but renders standalone so the schedule
  // modal's `_schedFolderPickerSelect` close-topmost behavior doesn't apply).
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Add input folder</h2>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">Pick a folder on disk. Files are scanned periodically and indexed into this project's memory.</div>
    <div id="pif-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pif-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-recursive" checked>
      Scan recursively (include all subfolders)
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-auto-sync" checked>
      Include in automatic sync cycles
      <span style="color:var(--text-400);font-size:12px">— uncheck to only sync manually</span>
    </label>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" onclick="_pifPickerSelect()">Add this folder</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  _pifLoadFolder('');  // empty path → server defaults to $HOME
}

async function _pifLoadFolder(path) {
  const crumbs = document.getElementById('pif-picker-crumbs');
  const list = document.getElementById('pif-picker-list');
  if (!crumbs || !list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-400);text-align:center">Loading…</div>';
  try {
    const data = await API.get(`/v1/files/tree?path=${encodeURIComponent(path)}&depth=0`);
    if (data.error) { list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(data.error)}</div>`; return; }
    const cur = data.path || path || '/';
    window._pifPickerPath = cur;
    crumbs.textContent = cur;
    const dirs = (data.tree || []).filter(n => n.type === 'dir');
    const parent = (cur && cur !== '/') ? cur.replace(/\/[^\/]+\/?$/, '') || '/' : null;
    let html = '';
    if (parent !== null) {
      html += `<div onclick="_pifLoadFolder('${esc(parent)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-300)" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">↑ ..</div>`;
    }
    if (!dirs.length) {
      html += '<div style="padding:14px;color:var(--text-400);text-align:center;font-size:12px">(no subfolders)</div>';
    } else {
      for (const d of dirs) {
        html += `<div onclick="_pifLoadFolder('${esc(d.path)}')" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-100);font-family:var(--font-mono);font-size:12px;color:var(--text-200);display:flex;align-items:center;gap:8px" onmouseover="this.style.background='var(--bg-200)'" onmouseout="this.style.background=''">
          <span style="color:var(--text-400)">📁</span>${esc(d.name)}
        </div>`;
      }
    }
    list.innerHTML = html;
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--error)">${esc(e.message)}</div>`;
  }
}

async function _pifPickerSelect() {
  const path = window._pifPickerPath || '';
  if (!path) { showToast('No folder selected', true); return; }
  const recursive = document.getElementById('pif-picker-recursive')?.checked ?? true;
  const autoSync = document.getElementById('pif-picker-auto-sync')?.checked ?? true;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    const res = await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders`,
      { path, recursive, auto_sync: autoSync });
    if (res?.error) {
      showToast(res.error);
      return;
    }
    showToast('Folder added — first scan running');
    document.querySelector('.sched-modal-overlay')?.remove();
    loadProjectInputFolders(agentId, projectName);
    // Trigger a sync now so the user sees activity immediately, even if
    // auto_sync is off — the user just opted in to a one-shot index.
    projectSyncNow();
  } catch(e) {
    showToast('Failed to add folder: ' + (e?.message || e));
  }
}

function removeProjectInputFolder(idx) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const folders = state._projectInputFolders || [];
  const folder = folders[idx];
  if (!folder) return;
  // Warning modal — replaces the legacy confirm() so the destructive action
  // is gated by a clearly red button instead of a system dialog the user
  // can dismiss with Enter.
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="sched-modal" style="max-width:520px">
    <h2 style="display:flex;align-items:center;gap:8px">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#d33" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      Remove input folder?
    </h2>
    <div style="font-size:13px;color:var(--text-300);line-height:1.5;margin:8px 0 16px">
      <div style="font-family:var(--font-mono);font-size:12px;background:var(--bg-100);padding:8px 10px;border-radius:6px;border:1px solid var(--border-100);word-break:break-all;margin-bottom:10px">${esc(folder.path || '')}</div>
      This folder will no longer be scanned. Already-indexed content stays in this project's memory until the project is purged.
    </div>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" style="background:#d33;border-color:#d33" onclick="_pifConfirmDelete(${idx})">Remove folder</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

async function _pifConfirmDelete(idx) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  document.querySelector('.sched-modal-overlay')?.remove();
  try {
    await API.del(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders/${idx}`);
    loadProjectInputFolders(agentId, projectName);
    showToast('Folder removed');
  } catch(e) {
    showToast('Failed to remove folder');
  }
}

function editProjectInputFolder(idx) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const folder = (state._projectInputFolders || [])[idx];
  if (!folder) return;
  // Edit modal — same picker shell as add, but pre-loaded at the existing
  // path, with recursive + auto_sync prefilled. Save button is wired to
  // _pifEditSave (not _pifPickerSelect) which PATCHes via POST /input-folders/<idx>.
  window._pifEditingIdx = idx;
  const overlay = document.createElement('div');
  overlay.className = 'sched-modal-overlay';
  overlay.style.zIndex = '10001';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  const recChecked = folder.recursive !== false ? 'checked' : '';
  const autoChecked = folder.auto_sync !== false ? 'checked' : '';
  overlay.innerHTML = `<div class="sched-modal" style="max-width:600px">
    <h2>Edit input folder</h2>
    <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">Change the path or how this folder is scanned.</div>
    <div id="pif-picker-crumbs" style="font-family:var(--font-mono);font-size:12px;color:var(--text-300);padding:6px 10px;background:var(--bg-100);border-radius:6px;margin-bottom:8px;word-break:break-all">…</div>
    <div id="pif-picker-list" style="max-height:340px;overflow-y:auto;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)"></div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-recursive" ${recChecked}>
      Scan recursively (include all subfolders)
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:13px;color:var(--text-300);cursor:pointer">
      <input type="checkbox" id="pif-picker-auto-sync" ${autoChecked}>
      Include in automatic sync cycles
      <span style="color:var(--text-400);font-size:12px">— uncheck to only sync manually</span>
    </label>
    <div class="sched-modal-actions">
      <button class="sched-cancel-btn" onclick="this.closest('.sched-modal-overlay').remove()">Cancel</button>
      <button class="sched-create-btn" onclick="_pifEditSave()">Save changes</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  // Open the picker at the parent of the current path so the user can either
  // keep the current path (just toggle flags + Save) or navigate elsewhere.
  const cur = folder.path || '';
  const parent = cur.replace(/\/[^\/]+\/?$/, '') || '/';
  _pifLoadFolder(parent);
  // Pre-set picker path to the current folder so a no-navigate Save keeps it.
  window._pifPickerPath = cur;
}

async function _pifEditSave() {
  const idx = window._pifEditingIdx;
  if (idx == null) return;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const folder = (state._projectInputFolders || [])[idx] || {};
  const path = window._pifPickerPath || folder.path || '';
  const recursive = document.getElementById('pif-picker-recursive')?.checked ?? true;
  const autoSync = document.getElementById('pif-picker-auto-sync')?.checked ?? true;
  const body = { recursive, auto_sync: autoSync };
  // Only send path when it actually changed — saves a realpath round-trip
  // server-side and skips the "folder already added" dedup against itself.
  if (path && path !== folder.path) body.path = path;
  try {
    const res = await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/input-folders/${idx}`, body);
    if (res?.error) { showToast(res.error); return; }
    showToast('Folder updated');
    document.querySelector('.sched-modal-overlay')?.remove();
    window._pifEditingIdx = null;
    loadProjectInputFolders(agentId, projectName);
  } catch(e) {
    showToast('Failed to update folder: ' + (e?.message || e));
  }
}

// Bridge from the "Knowledge graph" project header button to the existing
// kgOpenProject drilldown modal. Uses the project ids stashed on the
// project-detail state so it works regardless of whether the chip has
// finished its first refresh.
function projectOpenKnowledgeGraph() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) {
    showToast('No project in scope', true);
    return;
  }
  if (typeof kgOpenProject !== 'function') {
    showToast('Knowledge graph viewer not available', true);
    return;
  }
  kgOpenProject(agentId, projectName);
}

async function projectSyncNow() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-now`, {});
    showToast('Sync queued');
    refreshProjectSyncStatus(agentId, projectName);
  } catch(e) {
    showToast('Failed to trigger sync');
  }
}

async function projectFullResync() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  if (!await showConfirmDanger(`Full Resync will wipe all memory, knowledge graph triples, and sync state for "${projectName}", then re-index everything from scratch.\n\nContinue?`, 'Full Resync', 'Resync')) return;
  const btn = document.getElementById('project-action-full-resync');
  if (btn) { btn.disabled = true; btn.textContent = 'Wiping…'; }
  try {
    await API.post(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/full-resync`, {});
    showToast('Full resync queued — re-indexing from scratch');
    refreshProjectSyncStatus(agentId, projectName);
  } catch(e) {
    showToast('Full resync failed', true);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3"/></svg> Full Resync'; }
  }
}

function startProjectSyncPoll(agentId, projectName) {
  stopProjectSyncPoll();
  refreshProjectSyncStatus(agentId, projectName);
  state._projectSyncPollHandle = setInterval(() => {
    refreshProjectSyncStatus(agentId, projectName);
  }, 5000);
}

function stopProjectSyncPoll() {
  if (state._projectSyncPollHandle) {
    clearInterval(state._projectSyncPollHandle);
    state._projectSyncPollHandle = null;
  }
}

async function refreshProjectSyncStatus(agentId, projectName) {
  const chip = document.getElementById('project-sync-chip');
  const labelEl = document.getElementById('project-sync-label');
  if (!chip || !labelEl) return;
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-status`);
    if (window._brainProjectSyncDebug) console.log('[sync-status]', data);
    const st = data.status || {};
    // Cache for per-item pill rendering inside loadProjectFiles /
    // loadProjectInputFolders.
    state._projectSyncItems = st.items || {};
    const live = st.state || 'idle';
    chip.style.display = '';
    chip.dataset.state = live;
    // Pull the headline numbers once — used by both syncing and idle labels.
    // total_files = distinct source files (what users count); total_indexed
    // = drawer count (internal storage detail, kept in tooltip).
    const totalFiles = st.total_files != null ? st.total_files : null;
    const totalDrawers = st.total_indexed != null ? st.total_indexed : (st.last_files_filed || 0);
    const triples = st.total_triples;
    // Renamed for non-technical users: a (subject, predicate, object) triple
    // doesn't mean anything outside the KG world. "Relation" reads as "fact
    // we extracted" in plain English.
    const tripleStr = (triples != null && triples > 0) ? ` · ${triples} relations` : '';
    let label = 'Memory: idle';
    if (live === 'syncing') {
      // Live progress: P/T file count + ETA from elapsed-rate. cycle_total_files
      // is from a cheap pre-walk and may overshoot the miner's filtered file
      // count — that's deliberate (better than the bar getting stuck at 100%).
      const proc = Number(st.cycle_processed_files || 0);
      const tot = Number(st.cycle_total_files || 0);
      let progress = '';
      if (tot > 0) progress = ` ${proc}/${tot}`;
      // ETA: extrapolate from elapsed wall time and processed share. Only
      // show once we've made meaningful progress (>=5%) so an early 0/N
      // doesn't claim "ETA 12 days".
      let eta = '';
      const startedAt = st.started_at || st.last_run_started || '';
      if (startedAt && proc > 0 && tot > 0 && proc / tot >= 0.05) {
        const elapsedMs = Math.max(0, Date.now() - new Date(startedAt).getTime());
        const remainMs = elapsedMs * (tot - proc) / proc;
        if (remainMs > 1000 && remainMs < 1000 * 60 * 60 * 48) {
          const remainSec = Math.floor(remainMs / 1000);
          const etaStr = remainSec < 60 ? `${remainSec}s`
                       : remainSec < 3600 ? `${Math.floor(remainSec / 60)}m`
                       : `${Math.floor(remainSec / 3600)}h`;
          eta = ` · ETA ${etaStr}`;
        }
      }
      const cur = st.current_folder ? ` (${st.current_folder.split('/').pop()})` : '';
      label = `Memory: syncing${progress} files${eta}${cur}`;
      chip.title = 'Sync in progress';
      const sublabelElSync = document.getElementById('project-sync-sublabel');
      if (sublabelElSync) sublabelElSync.textContent = '';
    } else if (live === 'error') {
      label = 'Memory: error';
      chip.title = st.last_error || 'Sync failed — use Sync now to retry';
      const sublabelElErr = document.getElementById('project-sync-sublabel');
      if (sublabelElErr) sublabelElErr.textContent = '';
    } else {
      // Idle: lead with files (the unit users care about), then triples,
      // then "next sync in Xh". Last-synced timestamp and type shown in sub-label.
      const filesStr = totalFiles != null
        ? `${totalFiles} file${totalFiles === 1 ? '' : 's'}`
        : `${totalDrawers} indexed`;
      const next = data.next_run_at ? ` · next sync in ${humanIn(data.next_run_at)}` : '';
      label = `Memory: ${filesStr}${tripleStr}${next}`;
      const last = st.last_run_finished || data.last_scan || '';
      const drawerHint = (totalFiles != null && totalDrawers)
        ? `${totalDrawers} drawer${totalDrawers === 1 ? '' : 's'} · ` : '';
      chip.title = `${drawerHint}${last ? 'Last synced ' + humanAgo(last) + ' ago — ' : ''}use the buttons on the right to sync or open the knowledge graph`;
      // Sub-label: "synced Xh ago · Scheduled" or "synced Xh ago · Full Resync"
      const sublabelEl = document.getElementById('project-sync-sublabel');
      if (sublabelEl) {
        if (last) {
          const typeLabel = st.last_triggered_by === 'full_resync' ? 'Full Resync'
                          : st.last_triggered_by === 'manual' ? 'Manual' : 'Scheduled';
          sublabelEl.textContent = `synced ${humanAgo(last)} ago · ${typeLabel}`;
        } else {
          sublabelEl.textContent = '';
        }
      }
    }
    // KG is currently extracting on any item? pulse purple.
    let kgWorking = false;
    let kgError = false;
    for (const k of Object.keys(state._projectSyncItems || {})) {
      const it = state._projectSyncItems[k] || {};
      if (it.kg_state === 'extracting') { kgWorking = true; break; }
      if (it.kg_state === 'error') kgError = true;
    }
    chip.dataset.kgState = kgWorking ? 'extracting' : (kgError ? 'error' : '');
    if (kgWorking) {
      // Find the item currently extracting to get live progress.
      let kgDone = 0, kgTotal = 0, kgStarted = 0, kgTriples = 0;
      for (const k of Object.keys(state._projectSyncItems || {})) {
        const it = state._projectSyncItems[k] || {};
        if (it.kg_state === 'extracting') {
          kgDone   = Number(it.kg_chunks_done  || 0);
          kgTotal  = Number(it.kg_chunks_total || 0);
          kgStarted = it.kg_started_at ? Number(it.kg_started_at) * 1000 : 0;
          kgTriples = Number(it.kg_triples_live || it.triples_extracted || 0);
          break;
        }
      }
      let kgProgress = kgTotal > 0 ? ` ${kgDone}/${kgTotal} chunks` : (kgDone > 0 ? ` ${kgDone} chunks` : '');
      let kgEta = '';
      if (kgStarted && kgDone > 0 && kgTotal > 0 && kgDone / kgTotal >= 0.05) {
        const elapsedMs = Math.max(0, Date.now() - kgStarted);
        const remainMs = elapsedMs * (kgTotal - kgDone) / kgDone;
        if (remainMs > 1000 && remainMs < 1000 * 60 * 60 * 4) {
          const remainSec = Math.floor(remainMs / 1000);
          const etaStr = remainSec < 60 ? `${remainSec}s` : `${Math.floor(remainSec / 60)}m`;
          kgEta = ` · ETA ${etaStr}`;
        }
      }
      const kgTriplesStr = kgTriples > 0 ? ` · ${kgTriples} so far` : '';
      label = `Memory: KG extracting${kgProgress}${kgEta}${kgTriplesStr}`;
      chip.title = 'Knowledge graph extraction in progress';
      const sublabelElKg = document.getElementById('project-sync-sublabel');
      if (sublabelElKg) sublabelElKg.textContent = '';
    }
    labelEl.textContent = label;
    // Knowledge-graph button: admin-only. The drilldown is a debug /
    // operations surface (predicate distribution, sample triples,
    // extraction-log, admin re-extract) — useful for verifying extraction
    // quality and auditing the corpus, not for end users. Hidden entirely
    // for non-admins. When admin: enabled if there are any relations to
    // show, greyed otherwise.
    const kgBtn = document.getElementById('project-action-kg');
    if (kgBtn) {
      const isAdmin = state.authUser && state.authUser.role === 'admin';
      if (!isAdmin) {
        kgBtn.style.display = 'none';
      } else {
        kgBtn.style.display = '';
        const hasRelations = (st.total_triples || 0) > 0;
        kgBtn.disabled = !hasRelations;
        kgBtn.title = hasRelations
          ? `Open the knowledge graph drilldown (${st.total_triples} relations)`
          : 'No relations extracted yet — sync this project first';
      }
    }
    const syncBtn = document.getElementById('project-action-sync');
    if (syncBtn) {
      syncBtn.disabled = (live === 'syncing');
      syncBtn.title = live === 'syncing'
        ? 'Sync already in progress'
        : 'Run a memory sync now';
    }
    const fullResyncBtn = document.getElementById('project-action-full-resync');
    if (fullResyncBtn) {
      const isAdmin = state.authUser && state.authUser.role === 'admin';
      fullResyncBtn.style.display = isAdmin ? '' : 'none';
      fullResyncBtn.disabled = (live === 'syncing');
    }
    const historyBtn = document.getElementById('project-action-sync-history');
    if (historyBtn) {
      const isAdmin = state.authUser && state.authUser.role === 'admin';
      historyBtn.style.display = isAdmin ? '' : 'none';
    }
    // Re-paint per-item pills without re-fetching the underlying lists.
    paintProjectItemPills();
  } catch(e) {
    // Hide on auth/404 — non-managers may not be able to read it.
    chip.style.display = 'none';
  }
}

// Render the right-side status pill for one item ("attachment:<hash>" or
// "folder:<abs path>"). Returns inline HTML so callers can splice it into
// their row template — but we also use it imperatively via paintProjectItemPills().
function projectItemPillHtml(kind, ident) {
  const items = state._projectSyncItems || {};
  const key = `${kind}:${ident}`;
  const it = items[key];
  if (!it) {
    return '<span class="project-item-pill" data-state="pending" title="Waiting for next sync cycle">pending</span>';
  }
  const stateName = it.state || 'pending';
  const tip = it.error ? it.error
            : (stateName === 'indexed'
                ? `Indexed ${it.drawers_filed != null ? '(' + it.drawers_filed + ' drawers)' : ''}`.trim()
                : (stateName === 'syncing' ? 'Sync in progress…' : 'Pending'));
  let label = stateName;
  if (stateName === 'indexed') label = 'indexed';
  else if (stateName === 'syncing') label = 'syncing…';
  else if (stateName === 'error') label = 'error';
  let kgBadge = '';
  const kgState = it.kg_state || '';
  const triples = it.triples_extracted;
  const kgParseErrors = it.kg_parse_errors || 0;
  if (kgState === 'extracting') {
    kgBadge = ` <span class="project-item-pill" data-kg="extracting" title="Knowledge graph extraction running">KG…</span>`;
  } else if (kgState === 'error') {
    const kgErr = it.kg_last_error || 'KG extraction failed';
    const triplesPart = (typeof triples === 'number' && triples > 0) ? `${triples} relations · ` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="error" title="${esc(kgErr)}">${triplesPart}KG !</span>`;
  } else if (typeof triples === 'number' && triples > 0) {
    // Per-folder pill in the right pane — same renaming as the project chip
    // ("triples" is jargon, "relations" is what's been extracted).
    const warnPart = kgParseErrors > 0 ? ` · ${kgParseErrors} parse err` : '';
    const warnTitle = kgParseErrors > 0 ? ` (${kgParseErrors} chunks returned invalid JSON — non-fatal)` : '';
    kgBadge = ` <span class="project-item-pill" data-kg="${kgParseErrors > 0 ? 'warn' : 'ok'}" title="Knowledge graph relations extracted from this folder${warnTitle}">${triples} relations${warnPart}</span>`;
  }
  return `<span class="project-item-pill" data-state="${stateName}" title="${esc(tip)}">${esc(label)}</span>${kgBadge}`;
}

// Imperative re-paint after each /sync-status poll. Cheaper than re-rendering
// the lists, and preserves DOM identity (no flicker).
function paintProjectItemPills() {
  document.querySelectorAll('[data-pif-pill]').forEach(el => {
    const kind = el.getAttribute('data-pif-kind');
    const ident = el.getAttribute('data-pif-id');
    if (!kind || !ident) return;
    el.outerHTML = `<span data-pif-pill data-pif-kind="${esc(kind)}" data-pif-id="${esc(ident)}">${projectItemPillHtml(kind, ident)}</span>`;
  });
}

function humanAgo(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (!t) return '';
  const sec = Math.max(0, (Date.now() - t) / 1000);
  if (sec < 60) return Math.floor(sec) + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h';
  return Math.floor(sec / 86400) + 'd';
}

// Human "in 4h" / "in 12m" — counterpart to humanAgo for future timestamps.
// Returns 'now' if the target is in the past or within a minute (so a stale
// next-run doesn't read as "in 0s"; the user just sees the cycle is due).
function humanIn(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (!t) return '';
  const sec = (t - Date.now()) / 1000;
  if (sec < 60) return 'now';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h';
  return Math.floor(sec / 86400) + 'd';
}

function toggleProjectDesc() {
  const descEl = document.getElementById('project-detail-desc');
  if (descEl.dataset.collapsed === 'true') {
    descEl.textContent = descEl.dataset.full;
    descEl.dataset.collapsed = 'false';
  } else {
    const desc = descEl.dataset.full;
    descEl.innerHTML = esc(desc.slice(0, 200)) + '... <span class="project-detail-desc-toggle" onclick="toggleProjectDesc()">Show more</span>';
    descEl.dataset.collapsed = 'true';
  }
}

async function loadProjectFiles(agentId, projectName) {
  const container = document.getElementById('project-panel-files');
  try {
    const data = await API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/docs`);
    const docs = data.documents || [];
    if (!docs.length) {
      container.innerHTML = '<span class="project-panel-placeholder">Add PDFs, documents, or other texts to use as reference in this project.</span>';
      return;
    }
    container.innerHTML = '';
    for (const doc of docs) {
      const item = document.createElement('div');
      item.className = 'project-file-item';
      const srcHash = doc.source_hash || '';
      item.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <span class="project-file-name" title="${esc(doc.source || doc.name || '')}">${esc(doc.source || doc.name || 'Document')}</span>
        <span data-pif-pill data-pif-kind="attachment" data-pif-id="${esc(srcHash)}">${projectItemPillHtml('attachment', srcHash)}</span>
        <span class="project-file-delete" onclick="deleteProjectFile('${esc(agentId)}','${esc(projectName)}','${esc(srcHash)}'); event.stopPropagation();" title="Remove">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </span>
      `;
      container.appendChild(item);
    }
  } catch(e) {
    container.innerHTML = '<span class="project-panel-placeholder">Failed to load files</span>';
  }
}

async function uploadProjectFiles(files) {
  if (!files || !files.length) return;
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;

  // Auth header is required — the global /v1/* gate rejects anonymous POST.
  // Don't set Content-Type; the browser inserts the multipart boundary.
  const token = localStorage.getItem('auth-token') || '';
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
  for (const file of files) {
    try {
      const formData = new FormData();
      formData.append('file', file);
      showToast(`Uploading ${file.name}...`);
      const resp = await fetch(`${BASE_URL}/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/ingest`, {
        method: 'POST',
        headers,
        body: formData,
      });
      const result = await resp.json().catch(() => ({error: `HTTP ${resp.status}`}));
      if (!resp.ok || result.error) {
        showToast(`Error: ${result.error || resp.statusText}`);
      } else {
        showToast(`Uploaded ${file.name}`);
      }
    } catch(e) {
      showToast(`Failed to upload ${file.name}`);
    }
  }
  loadProjectFiles(agentId, projectName);
}

async function deleteProjectFile(agentId, projectName, sourceHash) {
  if (!sourceHash) return;
  try {
    await API.del(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/docs/${sourceHash}`);
    showToast('File removed');
    loadProjectFiles(agentId, projectName);
  } catch(e) {
    showToast('Failed to remove file');
  }
}

async function loadProjectChats(agentId, projectName) {
  const container = document.getElementById('project-detail-chats');
  const filter = state._projectChatsFilter || 'active';
  try {
    const data = await API.get(`/v1/sessions?agent=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}&status=${filter}`);
    const sessions = data.sessions || [];
    container.innerHTML = '';
    if (!sessions.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding:18px 8px;color:var(--text-400);font-size:13px;text-align:center';
      empty.textContent = filter === 'archived' ? 'No archived chats' : 'No chats yet';
      container.appendChild(empty);
      return;
    }
    for (const s of sessions) {
      const item = document.createElement('div');
      item.className = 'project-chat-item';
      const ago = s.last_active ? formatTimeAgo(new Date(s.last_active * 1000)) : '';
      const isArchived = filter === 'archived' || s.status === 'archived';
      // Stash status flag for the menu (avoids a second fetch).
      item.dataset.archived = isArchived ? '1' : '0';
      item.innerHTML = `
        <span class="project-chat-item-title">${esc(s.summary || s.title || 'Untitled')}</span>
        <span class="project-chat-item-meta">${ago ? 'Last message ' + ago : ''}</span>
        <span class="project-chat-item-actions">
          <button style="color:var(--text-400);padding:4px" onclick="event.stopPropagation(); showProjectChatMenu(event, '${esc(s.id)}', ${isArchived ? 'true' : 'false'})" title="More options">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
          </button>
        </span>
      `;
      // Pass the session's own agent_id (sessions can be re-homed; never
      // assume state.activeAgentId is correct). Without this, openSession's
      // selectAgent(undefined) corrupts the active chat context and the
      // session loads against the wrong chat object — looks like "reload
      // doesn't work" / "continue chat is broken".
      const sAgent = s.agent_id || s.agent || agentId;
      item.onclick = () => {
        openSession(s.id, sAgent);
        // openSession already navigates to chat; no second navigateTo needed.
      };
      container.appendChild(item);
    }
  } catch(e) {
    console.error('Failed to load project chats:', e);
  }
}

// Switch active/archived tab and reload list. Server filters by status, so
// the same loadProjectChats path works for both.
function setProjectChatsFilter(filter) {
  state._projectChatsFilter = filter;
  document.querySelectorAll('.project-chats-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.pcfilter === filter);
  });
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (agentId && projectName) loadProjectChats(agentId, projectName);
}

async function archiveAllProjectChats() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const filter = state._projectChatsFilter || 'active';
  // On the Active tab → archive all active in this project.
  // On the Archived tab → unarchive all archived in this project.
  if (filter === 'archived') {
    if (!await showConfirm(`Unarchive all archived chats in "${projectName}"?`)) return;
    try {
      await API.manageSession({ action: 'unarchive_all', agent: agentId, project: projectName });
      showToast('All chats unarchived');
      loadProjectChats(agentId, projectName);
      loadAgentSessions(agentId);
    } catch(e) { showToast('Unarchive all failed', true); }
    return;
  }
  if (!await showConfirm(`Archive all active chats in "${projectName}"?`)) return;
  try {
    await API.manageSession({ action: 'archive_all', agent: agentId, project: projectName });
    showToast('All chats archived');
    loadProjectChats(agentId, projectName);
    loadAgentSessions(agentId);
  } catch(e) { showToast('Archive all failed', true); }
}

async function deleteAllProjectChats() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const filter = state._projectChatsFilter || 'active';
  const archivedOnly = filter === 'archived';
  const label = archivedOnly ? 'archived chats' : 'ALL chats';
  if (!await showConfirmDanger(`Permanently delete ${label} in "${projectName}"? This cannot be undone.`, 'Delete Chats', 'Delete')) return;
  try {
    const r = await API.manageSession({
      action: 'delete_all', agent: agentId, project: projectName, archived_only: archivedOnly,
    });
    showToast(`Deleted ${r.count || 'all'} chats`);
    // If the active chat was inside this project, reset the view.
    if (state.activeChat?.sessionId && state.currentProject === projectName) {
      newChat();
    }
    loadProjectChats(agentId, projectName);
    loadAgentSessions(agentId);
  } catch(e) { showToast('Delete all failed', true); }
}

// Per-session unarchive helper used by the project chat menu and (eventually)
// the global chats list. Uses manageSession action 'unarchive'.
async function unarchiveSession(sessionId) {
  try {
    await API.manageSession({ action: 'unarchive', session_id: sessionId });
    showToast('Chat unarchived');
    const agentId = state._projectDetailAgent;
    const projectName = state._projectDetailName;
    if (state.currentView === 'project-detail' && agentId && projectName) {
      loadProjectChats(agentId, projectName);
    }
    loadAgentSessions(state.activeAgentId);
  } catch(e) { showToast('Unarchive failed', true); }
}

function editProjectInstructions() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  const project = state._projectDetail;
  if (!agentId || !projectName) return;

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.maxWidth = '600px';
  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Project Instructions</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <p style="font-size:13px;color:var(--text-400);margin-bottom:12px">
        Add instructions to customize how Brain Agent responds within this project.
        These instructions are appended to every conversation as additive
        owner guidance. Markdown is supported — use the Preview tab to see
        how it will render.
        <br><br>
        Leave empty for no extra instructions. Strict retrieval / citation
        discipline is a separate setting (<em>Research / Q&A project</em> in
        project settings) and is not controlled here.
      </p>
      <div class="instr-tabs" role="tablist">
        <button class="instr-tab active" id="instr-tab-edit" role="tab" onclick="switchInstrTab('edit')">Edit</button>
        <button class="instr-tab" id="instr-tab-preview" role="tab" onclick="switchInstrTab('preview')">Preview</button>
      </div>
      <textarea class="project-instructions-editor" id="project-instructions-textarea"
        placeholder="e.g. You are a helpful assistant for our marketing team. Always respond in a professional tone..."
      >${esc(project?.instructions || '')}</textarea>
      <div class="instr-preview-pane" id="project-instructions-preview" style="display:none"></div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
      <button class="btn-primary" onclick="saveProjectInstructions()">Save</button>
    </div>
  `;
  overlay.appendChild(content);
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('project-instructions-textarea')?.focus(), 100);
}

function switchInstrTab(mode) {
  const editTab = document.getElementById('instr-tab-edit');
  const previewTab = document.getElementById('instr-tab-preview');
  const textarea = document.getElementById('project-instructions-textarea');
  const preview = document.getElementById('project-instructions-preview');
  if (!editTab || !previewTab || !textarea || !preview) return;
  if (mode === 'preview') {
    editTab.classList.remove('active');
    previewTab.classList.add('active');
    const raw = textarea.value || '';
    if (raw.trim()) {
      preview.innerHTML = renderMarkdown(raw);
    } else {
      preview.innerHTML = '<span class="instr-preview-empty">Nothing to preview yet — write some instructions in the Edit tab.</span>';
    }
    textarea.style.display = 'none';
    preview.style.display = 'block';
  } else {
    previewTab.classList.remove('active');
    editTab.classList.add('active');
    preview.style.display = 'none';
    textarea.style.display = '';
    setTimeout(() => textarea.focus(), 0);
  }
}

async function toggleProjectResearchMode(enabled) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  try {
    await API.updateProject(agentId, projectName, { research_mode: !!enabled });
    if (state._projectDetail) state._projectDetail.research_mode = !!enabled;
    // Invalidate the composer button's cached project default so the
    // next refresh in any open chat reads fresh state.
    if (state._projectResearchModeCache) {
      state._projectResearchModeCache[agentId + '::' + projectName] = !!enabled;
    }
    if (typeof refreshResearchModeButton === 'function') refreshResearchModeButton();
    showToast(enabled ? 'Research mode on for this project'
                       : 'Research mode off for this project');
  } catch (e) {
    showToast('Could not update project mode', true);
    // Revert checkbox to the last known state on failure.
    const cb = document.getElementById('project-research-mode-checkbox');
    if (cb) cb.checked = !enabled;
  }
}

async function saveProjectInstructions() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  const textarea = document.getElementById('project-instructions-textarea');
  if (!agentId || !projectName || !textarea) return;

  const instructions = textarea.value.trim();
  try {
    await API.updateProject(agentId, projectName, { instructions });
    showToast('Instructions saved');
    document.querySelector('.modal-overlay')?.remove();
    // Update panel display — render as markdown to match loadProjectDetail.
    const instrEl = document.getElementById('project-panel-instructions');
    if (instructions) {
      instrEl.innerHTML = `<div class="project-panel-instructions-rendered">${renderMarkdown(instructions)}</div>`;
      instrEl.classList.remove('project-panel-placeholder');
    } else {
      instrEl.innerHTML = '<span class="project-panel-placeholder">Add instructions to customize Brain Agent\'s responses (optional, additive)</span>';
    }
    if (state._projectDetail) state._projectDetail.instructions = instructions;
  } catch(e) {
    showToast('Failed to save instructions');
  }
}

// ─── Project member-picker helpers ───────────────────────────────
async function _ensureUserDirectory() {
  if (state._userDirectory) return state._userDirectory;
  try {
    const r = await API.lookupUsers();
    state._userDirectory = (r.users || []);
  } catch(e) {
    state._userDirectory = [];
  }
  return state._userDirectory;
}

function _userLabel(u) {
  return u.display_name || u.username || u.id;
}

function _renderProjectMemberChips(containerId, ids, listId) {
  const wrap = document.getElementById(containerId);
  if (!wrap) return;
  const dir = state._userDirectory || [];
  const byId = {};
  for (const u of dir) byId[u.id] = u;
  if (!ids.length) {
    wrap.innerHTML = '<span style="font-size:12px;color:var(--text-400);font-style:italic">None added</span>';
    return;
  }
  wrap.innerHTML = ids.map(uid => {
    const u = byId[uid] || {id: uid, display_name: uid};
    return `<span class="project-member-chip" data-uid="${esc(uid)}" style="display:inline-flex;align-items:center;gap:6px;padding:4px 8px;background:var(--bg-200);border:1px solid var(--border-200);border-radius:12px;font-size:12px;margin:2px 4px 2px 0">
      ${esc(_userLabel(u))}
      <button onclick="_pmRemove('${esc(listId)}','${esc(uid)}','${esc(containerId)}')" style="background:none;border:none;cursor:pointer;color:var(--text-400);padding:0;line-height:1" title="Remove">×</button>
    </span>`;
  }).join('');
}

function _pmRemove(listId, uid, containerId) {
  const arr = window[listId] || [];
  const idx = arr.indexOf(uid);
  if (idx >= 0) arr.splice(idx, 1);
  _renderProjectMemberChips(containerId, arr, listId);
}

function _pmAdd(selectId, listId, containerId) {
  const sel = document.getElementById(selectId);
  const uid = sel?.value;
  if (!uid) return;
  const arr = window[listId] = window[listId] || [];
  if (!arr.includes(uid)) arr.push(uid);
  sel.value = '';
  _renderProjectMemberChips(containerId, arr, listId);
}

function _renderMemberPicker(opts) {
  // opts: {label, helpText, listId, containerId, selectId, excludeIds}
  const dir = state._userDirectory || [];
  const exclude = new Set(opts.excludeIds || []);
  const options = dir.filter(u => !exclude.has(u.id))
    .map(u => `<option value="${esc(u.id)}">${esc(_userLabel(u))}</option>`).join('');
  return `
    <div class="project-modal-field">
      <label class="project-modal-label">${esc(opts.label)}</label>
      ${opts.helpText ? `<div style="font-size:11px;color:var(--text-400);margin-bottom:6px">${opts.helpText}</div>` : ''}
      <div id="${opts.containerId}" style="min-height:24px;margin-bottom:6px"></div>
      <div style="display:flex;gap:6px">
        <select class="project-modal-input" id="${opts.selectId}" style="flex:1">
          <option value="">Select user…</option>
          ${options}
        </select>
        <button class="btn-secondary" onclick="_pmAdd('${esc(opts.selectId)}','${esc(opts.listId)}','${esc(opts.containerId)}')">Add</button>
      </div>
    </div>`;
}

async function showCreateProjectModal() {
  const agentId = state.activeAgentId || 'main';
  const authed = !!(state.authUser && state.authEnabled);
  if (authed) await _ensureUserDirectory();
  // Reset shared lists used by the chip picker
  window._projectCreateExtras = [];
  window._projectCreateExcluded = [];

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.maxWidth = '520px';
  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">New Project</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <div class="project-modal-field">
        <label class="project-modal-label">Project name</label>
        <input class="project-modal-input" id="create-project-name" placeholder="My Project" autofocus>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Description (optional)</label>
        <textarea class="project-modal-input" id="create-project-desc" rows="3" style="resize:vertical"
          placeholder="What is this project about?"></textarea>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Agent</label>
        <select class="project-modal-input" id="create-project-agent">
          ${state.agents.map(a => {
            const aid = a.id || a.name;
            return `<option value="${esc(aid)}" ${aid === agentId ? 'selected' : ''}>${esc(a.display_name || aid)}</option>`;
          }).join('')}
        </select>
      </div>
      ${authed ? (() => {
        const isAdmin = state.authUser.role === 'admin';
        const headedTeams = (state.userTeams || []).filter(t => t.head_user_id === state.authUser.id);
        const canTeam = isAdmin || headedTeams.length > 0;
        const teamOptions = isAdmin ? (state.userTeams || []) : headedTeams;
        const visOpts = [];
        visOpts.push(`<option value="user"${!isAdmin && !canTeam ? ' selected' : ''}>Personal</option>`);
        if (canTeam) visOpts.push(`<option value="team"${!isAdmin ? ' selected' : ''}>Team</option>`);
        if (isAdmin) visOpts.push('<option value="global" selected>Global (everyone)</option>');
        const initialVis = isAdmin ? 'global' : (canTeam ? 'team' : 'user');
        const ownerBlock = isAdmin
          ? `<div class="project-modal-field">
              <label class="project-modal-label">Owner</label>
              <select class="project-modal-input" id="create-project-owner">
                ${(state._userDirectory || []).map(u => `<option value="${esc(u.id)}" ${u.id===state.authUser.id?'selected':''}>${esc(_userLabel(u))}</option>`).join('')}
              </select>
            </div>`
          : `<input type="hidden" id="create-project-owner" value="${esc(state.authUser.id)}">`;
        const visBlock = (visOpts.length === 1)
          ? `<input type="hidden" id="create-project-visibility" value="user">`
          : `<div class="project-modal-field">
              <label class="project-modal-label">Visibility</label>
              <select class="project-modal-input" id="create-project-visibility" onchange="_createProjectOnVisChange(this.value)">
                ${visOpts.join('')}
              </select>
            </div>
            <div class="project-modal-field" id="create-project-team-wrap" style="display:${initialVis==='team'?'block':'none'}">
              <label class="project-modal-label">Team</label>
              <select class="project-modal-input" id="create-project-team">
                <option value="">Select team...</option>
                ${teamOptions.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}
              </select>
            </div>`;
        // Members panels (rendered for create; default to whatever initialVis dictates)
        const extrasPicker = _renderMemberPicker({
          label: 'Add members',
          helpText: 'Personal: people who get access. Team: extras outside the team. Global: ignored.',
          listId: '_projectCreateExtras',
          containerId: 'create-project-extras-chips',
          selectId: 'create-project-extras-select',
          excludeIds: [state.authUser.id],
        });
        const excludedPicker = _renderMemberPicker({
          label: 'Exclude users',
          helpText: 'Global only — block specific users from this project.',
          listId: '_projectCreateExcluded',
          containerId: 'create-project-excluded-chips',
          selectId: 'create-project-excluded-select',
          excludeIds: [state.authUser.id],
        });
        return `
          ${ownerBlock}
          ${visBlock}
          <div id="create-project-extras-wrap" style="display:${initialVis==='global'?'none':'block'}">${extrasPicker}</div>
          <div id="create-project-excluded-wrap" style="display:${initialVis==='global'?'block':'none'}">${excludedPicker}</div>
        `;
      })() : ''}
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
      <button class="btn-primary" onclick="createProject()">Create Project</button>
    </div>
  `;
  overlay.appendChild(content);
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('create-project-name')?.focus(), 100);

  // Render initial (empty) chips for the member-pickers
  if (document.getElementById('create-project-extras-chips')) {
    _renderProjectMemberChips('create-project-extras-chips', window._projectCreateExtras, '_projectCreateExtras');
  }
  if (document.getElementById('create-project-excluded-chips')) {
    _renderProjectMemberChips('create-project-excluded-chips', window._projectCreateExcluded, '_projectCreateExcluded');
  }

  // Enter key support
  document.getElementById('create-project-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') createProject();
  });
}

function _createProjectOnVisChange(value) {
  const teamWrap = document.getElementById('create-project-team-wrap');
  const extrasWrap = document.getElementById('create-project-extras-wrap');
  const excludedWrap = document.getElementById('create-project-excluded-wrap');
  if (teamWrap) teamWrap.style.display = (value === 'team' ? 'block' : 'none');
  if (extrasWrap) extrasWrap.style.display = (value === 'global' ? 'none' : 'block');
  if (excludedWrap) excludedWrap.style.display = (value === 'global' ? 'block' : 'none');
}

async function createProject() {
  const name = document.getElementById('create-project-name')?.value?.trim();
  const desc = document.getElementById('create-project-desc')?.value?.trim();
  const agentId = document.getElementById('create-project-agent')?.value || 'main';
  const visibility = document.getElementById('create-project-visibility')?.value || '';
  const teamId = document.getElementById('create-project-team')?.value || '';
  const ownerId = document.getElementById('create-project-owner')?.value || '';
  if (!name) { showToast('Project name is required'); return; }
  if (visibility === 'team' && !teamId) { showToast('Select a team for team-scoped project'); return; }

  const body = { name, description: desc || '' };
  if (visibility) body.visibility = visibility;
  if (teamId) body.owner_team_id = teamId;
  if (ownerId) body.owner_user_id = ownerId;
  if (visibility === 'global') {
    body.excluded_user_ids = (window._projectCreateExcluded || []).slice();
  } else {
    body.extra_member_user_ids = (window._projectCreateExtras || []).slice();
  }

  try {
    const result = await API.createProject(agentId, body);
    if (result.error) { showToast(result.error); return; }
    showToast('Project created');
    document.querySelector('.modal-overlay')?.remove();
    openProject(agentId, result.name || name);
  } catch(e) {
    showToast('Failed to create project');
  }
}

function showProjectListMenu(event, agentId, projectName) {
  event.stopPropagation();
  // Remove any existing context menu
  document.querySelectorAll('.ctx-menu').forEach(m => m.remove());

  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = `position:fixed;z-index:10000;background:var(--bg-000);border:1px solid var(--border-200);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:4px;min-width:140px`;
  menu.innerHTML = `
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="editProjectFromMenu('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Edit</div>
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="archiveProject('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Archive</div>
    <div style="padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--error)" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="deleteProject('${esc(agentId)}','${esc(projectName)}'); this.closest('.ctx-menu').remove()">Delete</div>
  `;
  document.body.appendChild(menu);
  // Position near the click
  const r = event.target.closest('button')?.getBoundingClientRect() || { left: event.clientX, bottom: event.clientY };
  menu.style.left = Math.min(r.left, window.innerWidth - 160) + 'px';
  menu.style.top = r.bottom + 4 + 'px';
  // Close on outside click
  setTimeout(() => document.addEventListener('click', function _cl() {
    menu.remove();
    document.removeEventListener('click', _cl);
  }), 10);
}

function showProjectMenu(event) {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  showProjectListMenu(event, agentId, projectName);
}

function showProjectChatMenu(event, sessionId, isArchived) {
  event.stopPropagation();
  document.querySelectorAll('.ctx-menu').forEach(m => m.remove());
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = `position:fixed;z-index:10000;background:var(--bg-000);border:1px solid var(--border-200);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);padding:4px;min-width:140px`;
  const itemStyle = `padding:8px 12px;cursor:pointer;border-radius:6px;font-size:13px;color:var(--text-200)`;
  const dangerStyle = itemStyle + ';color:var(--error)';
  const sid = esc(sessionId);
  const toggleAction = isArchived
    ? `<div style="${itemStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="unarchiveSession('${sid}'); this.closest('.ctx-menu').remove()">Unarchive</div>`
    : `<div style="${itemStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="archiveSession('${sid}'); this.closest('.ctx-menu').remove()">Archive</div>`;
  menu.innerHTML = toggleAction + `
    <div style="${dangerStyle}" onmouseover="this.style.background='var(--sidebar-hover)'" onmouseout="this.style.background=''" onclick="deleteSession('${sid}'); this.closest('.ctx-menu').remove()">Delete</div>
  `;
  document.body.appendChild(menu);
  const r = event.target.closest('button')?.getBoundingClientRect() || { left: event.clientX, bottom: event.clientY };
  menu.style.left = Math.min(r.left, window.innerWidth - 140) + 'px';
  menu.style.top = r.bottom + 4 + 'px';
  setTimeout(() => document.addEventListener('click', function _cl() {
    menu.remove();
    document.removeEventListener('click', _cl);
  }), 10);
}

async function editProjectFromMenu(agentId, projectName) {
  let project = null;
  try { project = await API.getProject(agentId, projectName); } catch(e) {}
  if (!project) { showToast('Failed to load project'); return; }

  const isAdmin = state.authUser && state.authUser.role === 'admin';
  const ownerUid = project.owner_user_id || '';
  const ownerTid = project.owner_team_id || '';
  const isOwner = state.authUser && ownerUid && ownerUid === state.authUser.id;
  const canManage = isAdmin || isOwner;
  if (!canManage) { showToast('Only the project owner can edit this project'); return; }

  await _ensureUserDirectory();
  // Stash for the save handler so it knows the effective scope when the
  // visibility selector isn't rendered (non-admin owner).
  window._projectEditOriginal = project;
  // Visibility / team re-scoping is admin-only.
  const canRescope = isAdmin;
  // Owner transfer: owner or admin.
  const canTransfer = canManage;
  const allTeams = state.userTeams || [];

  // Seed the chip lists from the project's current state
  window._projectEditExtras = (project.extra_member_user_ids || []).slice();
  window._projectEditExcluded = (project.excluded_user_ids || []).slice();

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.maxWidth = '540px';
  const ownerSelectorBlock = canTransfer
    ? `<div class="project-modal-field">
        <label class="project-modal-label">Owner</label>
        <select class="project-modal-input" id="edit-project-owner">
          ${(state._userDirectory || []).map(u => `<option value="${esc(u.id)}" ${u.id===ownerUid?'selected':''}>${esc(_userLabel(u))}</option>`).join('')}
        </select>
        ${isAdmin ? '' : '<div style="font-size:11px;color:var(--text-400);margin-top:4px">Transferring removes your edit rights.</div>'}
      </div>`
    : `<div class="project-modal-field" style="font-size:12px;color:var(--text-400)">Owner: <strong>${esc(_userLabel((state._userDirectory||[]).find(u=>u.id===ownerUid)||{id:ownerUid,display_name:ownerUid}))}</strong></div>`;

  const scopeBlock = canRescope
    ? `<div class="project-modal-field">
        <label class="project-modal-label">Visibility</label>
        <select class="project-modal-input" id="edit-project-visibility" onchange="_editProjectOnVisChange(this.value)">
          <option value="user" ${project.visibility==='user'?'selected':''}>Personal</option>
          <option value="team" ${project.visibility==='team'?'selected':''}>Team</option>
          <option value="global" ${project.visibility==='global'?'selected':''}>Global (everyone)</option>
        </select>
      </div>
      <div class="project-modal-field" id="edit-project-team-wrap" style="display:${project.visibility==='team'?'block':'none'}">
        <label class="project-modal-label">Team</label>
        <select class="project-modal-input" id="edit-project-team">
          <option value="">Select team...</option>
          ${allTeams.map(t => `<option value="${esc(t.id)}" ${t.id===ownerTid?'selected':''}>${esc(t.name)}</option>`).join('')}
        </select>
      </div>`
    : `<div class="project-modal-field" style="font-size:12px;color:var(--text-400)">
        Visibility: <strong>${esc(project.visibility || 'global')}</strong>${ownerTid?` · Team: <strong>${esc((allTeams.find(t=>t.id===ownerTid)||{}).name||ownerTid)}</strong>`:''}
        <div style="margin-top:4px">Only admins can change scope.</div>
      </div>`;

  // Member pickers
  const extrasPicker = _renderMemberPicker({
    label: 'Members',
    helpText: project.visibility === 'team'
      ? 'Team members are auto-included. List below holds extras outside the team.'
      : (project.visibility === 'global' ? '' : 'Users granted access in addition to the owner.'),
    listId: '_projectEditExtras',
    containerId: 'edit-project-extras-chips',
    selectId: 'edit-project-extras-select',
    excludeIds: [ownerUid],
  });
  const excludedPicker = _renderMemberPicker({
    label: 'Excluded users',
    helpText: 'Block these users from a Global project.',
    listId: '_projectEditExcluded',
    containerId: 'edit-project-excluded-chips',
    selectId: 'edit-project-excluded-select',
    excludeIds: [ownerUid],
  });

  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Edit Project</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="padding:16px">
      <div class="project-modal-field">
        <label class="project-modal-label">Project name</label>
        <input class="project-modal-input" id="edit-project-display-name" value="${esc(project.name || projectName)}" placeholder="My Project">
        <div style="font-size:11px;color:var(--text-400);margin-top:4px">Display name only. Folder name stays <code>${esc(projectName)}</code>.</div>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Description</label>
        <textarea class="project-modal-input" id="edit-project-desc" rows="3" style="resize:vertical"
          placeholder="What is this project about?">${esc(project.description || '')}</textarea>
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Icon</label>
        <input class="project-modal-input" id="edit-project-icon" value="${esc(project.icon || '📁')}" maxlength="4" style="width:80px;text-align:center;font-size:18px">
      </div>
      <div class="project-modal-field">
        <label class="project-modal-label">Image</label>
        <div style="display:flex;align-items:center;gap:10px">
          <div id="edit-project-image-preview" style="width:64px;height:42px;border-radius:6px;border:1px solid var(--border-200);background:var(--bg-300);background-size:cover;background-position:center;flex-shrink:0;${project.image ? `background-image:url('/v1/agents/${esc(agentId)}/projects/${esc(projectName)}/image?v=${Date.now()}')` : ''}"></div>
          <label class="btn-secondary" style="cursor:pointer">
            <span id="edit-project-image-label">${project.image ? 'Replace' : 'Upload'}</span>
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden onchange="_editProjectImageUpload(event,'${esc(agentId)}','${esc(projectName)}')">
          </label>
          <button type="button" class="btn-secondary" id="edit-project-image-clear" onclick="_editProjectImageClear('${esc(agentId)}','${esc(projectName)}')" style="display:${project.image?'inline-block':'none'}">Remove</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:4px">Used as the card background on the projects list and on favourites pinned to this project. Max 2 MB.</div>
      </div>
      ${ownerSelectorBlock}
      ${scopeBlock}
      <div id="edit-project-extras-wrap" style="display:${project.visibility==='global'?'none':'block'}">${extrasPicker}</div>
      <div id="edit-project-excluded-wrap" style="display:${project.visibility==='global'?'block':'none'}">${excludedPicker}</div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border-100)">
      <button class="btn-secondary" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
      <button class="btn-primary" onclick="saveProjectEdit('${esc(agentId)}','${esc(projectName)}')">Save</button>
    </div>
  `;
  overlay.appendChild(content);
  document.body.appendChild(overlay);
  // Render the chips after mount
  _renderProjectMemberChips('edit-project-extras-chips', window._projectEditExtras, '_projectEditExtras');
  _renderProjectMemberChips('edit-project-excluded-chips', window._projectEditExcluded, '_projectEditExcluded');
  setTimeout(() => document.getElementById('edit-project-display-name')?.focus(), 100);
}

function _editProjectOnVisChange(value) {
  const teamWrap = document.getElementById('edit-project-team-wrap');
  const extrasWrap = document.getElementById('edit-project-extras-wrap');
  const excludedWrap = document.getElementById('edit-project-excluded-wrap');
  if (teamWrap) teamWrap.style.display = (value === 'team' ? 'block' : 'none');
  if (extrasWrap) extrasWrap.style.display = (value === 'global' ? 'none' : 'block');
  if (excludedWrap) excludedWrap.style.display = (value === 'global' ? 'block' : 'none');
}

async function saveProjectEdit(agentId, projectName) {
  const displayName = document.getElementById('edit-project-display-name')?.value?.trim();
  const desc = document.getElementById('edit-project-desc')?.value;
  const icon = document.getElementById('edit-project-icon')?.value?.trim() || '📁';
  const visEl = document.getElementById('edit-project-visibility');
  const teamEl = document.getElementById('edit-project-team');
  const ownerEl = document.getElementById('edit-project-owner');
  if (!displayName) { showToast('Project name is required'); return; }
  const updates = { name: displayName, description: desc, icon };
  if (ownerEl) updates.owner_user_id = ownerEl.value || '';
  if (visEl) {
    updates.visibility = visEl.value;
    if (visEl.value === 'team') {
      const tid = teamEl?.value || '';
      if (!tid) { showToast('Select a team'); return; }
      updates.owner_team_id = tid;
    } else {
      updates.owner_team_id = '';
    }
  }
  // Effective scope for choosing which member list to send. Non-admins
  // can't change scope, so fall back to the project's stored visibility.
  const effectiveScope = visEl?.value || (window._projectEditOriginal?.visibility) || '';
  if (effectiveScope === 'global') {
    updates.excluded_user_ids = (window._projectEditExcluded || []).slice();
    updates.extra_member_user_ids = [];
  } else if (effectiveScope) {
    updates.extra_member_user_ids = (window._projectEditExtras || []).slice();
    updates.excluded_user_ids = [];
  }
  try {
    const result = await API.updateProject(agentId, projectName, updates);
    if (result && result.error) { showToast(result.error); return; }
    showToast('Project updated');
    document.querySelector('.modal-overlay')?.remove();
    if (state._projectDetailAgent === agentId && state._projectDetailName === projectName) {
      loadProjectDetail(agentId, projectName);
    }
    loadProjectsList();
  } catch(e) {
    showToast('Failed to update project');
  }
}

async function archiveProject(agentId, projectName) {
  try {
    await API.updateProject(agentId, projectName, { status: 'archived' });
    showToast('Project archived');
    loadProjectsList();
  } catch(e) { showToast('Failed to archive project'); }
}

async function deleteProject(agentId, projectName) {
  if (!await showConfirmDanger(`Delete project "${projectName}"? This cannot be undone.`, 'Delete Project', 'Delete')) return;
  try {
    await API.deleteProject(agentId, projectName);
    showToast('Project deleted');
    loadProjectsList();
  } catch(e) { showToast('Failed to delete project'); }
}

function toggleProjectStar() {
  // Visual toggle only (no backend persistence for stars yet)
  const btn = document.getElementById('project-detail-star');
  const svg = btn?.querySelector('svg');
  if (svg) {
    const filled = svg.getAttribute('fill') !== 'none';
    svg.setAttribute('fill', filled ? 'none' : 'var(--warning)');
    svg.setAttribute('stroke', filled ? 'currentColor' : 'var(--warning)');
  }
}

/* ═══════════════════════════════════════════════════════════
   ARTIFACT PANEL
   ═══════════════════════════════════════════════════════════ */

/* ═══ Unified Right Panel Functions ═══ */

// Project right-pane resize — same pattern as #right-panel. Idempotent;
// the bound flag prevents double-binding when openProject() reruns.
function initProjectDetailPanelResize() {
  const handle = document.getElementById('project-detail-panel-resize-handle');
  const panel = document.getElementById('project-detail-panel');
  if (!handle || !panel) return;
  // Restore persisted width on every init (cheap, idempotent).
  const saved = localStorage.getItem('project-detail-panel-width');
  if (saved) panel.style.width = saved;
  if (handle._bound) return;
  handle._bound = true;
  handle.addEventListener('mousedown', (e) => {
    const startX = e.clientX;
    const startW = panel.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const onMove = (ev) => {
      // Drag direction: handle is on the LEFT edge of the panel, so moving
      // the cursor LEFT widens the panel. Match #right-panel's math.
      const newW = Math.min(640, Math.max(240, startW + (startX - ev.clientX)));
      panel.style.width = newW + 'px';
    };
    const onUp = () => {
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      localStorage.setItem('project-detail-panel-width', panel.style.width);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  });
}

function initRightPanelResize() {
  const handle = document.getElementById('right-panel-resize-handle');
  const panel = document.getElementById('right-panel');
  if (!handle || !panel) return;
  handle.addEventListener('mousedown', (e) => {
    const startX = e.clientX;
    const startW = panel.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const onMove = (e) => {
      const newW = Math.min(800, Math.max(320, startW + (startX - e.clientX)));
      panel.style.width = newW + 'px';
    };
    const onUp = () => {
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      localStorage.setItem('right-panel-width', panel.style.width);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  });
  const saved = localStorage.getItem('right-panel-width');
  if (saved) panel.style.width = saved;
}

function openRightPanel(tab) {
  const panel = document.getElementById('right-panel');
  if (!panel) return;
  panel.classList.add('open');
  state.rightPanelOpen = true;
  switchRightTab(tab || state.rightPanelTab || 'attachments');
  initRightPanelResize();
  syncRightPanelToggle();
}

function toggleRightPanel() {
  if (state.rightPanelOpen) closeRightPanel();
  else openRightPanel();
}

function syncRightPanelToggle() {
  const btn = document.getElementById('toggle-right-panel-btn');
  if (btn) btn.classList.toggle('active', state.rightPanelOpen);
}

function closeRightPanel() {
  const panel = document.getElementById('right-panel');
  if (panel) panel.classList.remove('open');
  state.rightPanelOpen = false;
  state.activeArtifactId = null;
  state.activeArtifactVersion = null;
  state.artifactSourceMode = false;
  syncRightPanelToggle();
}

function switchRightTab(tabName) {
  state.rightPanelTab = tabName;
  // Toggle tab buttons
  document.querySelectorAll('.right-panel-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  // Toggle panes
  document.querySelectorAll('.right-tab-pane').forEach(pane => {
    pane.classList.toggle('active', pane.id === 'tab-pane-' + tabName);
  });
  // Populate content on switch
  if (tabName === 'attachments') renderAttachmentsPane();
  if (tabName === 'references') renderReferencesPane();
  if (tabName === 'artifacts' && !state.activeArtifactId) showArtifactList();
  updateRightPanelBadges();
}

function updateRightPanelBadges() {
  const chat = state.activeChat;
  const sessionId = chat?.sessionId;
  // Attachments count
  const attachCount = collectChatAttachments().length;
  const attachBadge = document.getElementById('tab-badge-attachments');
  if (attachBadge) attachBadge.textContent = attachCount || '0';
  // References count (cited + searched)
  const _refs = collectChatReferences();
  const refsCount = _refs.cited.length + _refs.searched.length;
  const refsBadge = document.getElementById('tab-badge-references');
  if (refsBadge) refsBadge.textContent = refsCount || '0';
  // Artifacts count
  const artifactCount = sessionId ? (state.artifacts[sessionId] || []).length : 0;
  const artBadge = document.getElementById('tab-badge-artifacts');
  if (artBadge) artBadge.textContent = artifactCount || '0';
}

function collectChatAttachments() {
  const chat = state.activeChat;
  if (!chat) return [];
  const attachments = [];
  for (let i = 0; i < chat.messages.length; i++) {
    const msg = chat.messages[i];
    if (msg.role !== 'human' && msg.role !== 'user') continue;
    // From msg.images (legacy)
    if (msg.images?.length) {
      for (const img of msg.images) {
        const url = img.preview || (img.data ? `data:${img.type || 'image/png'};base64,${img.data}` : null);
        if (url) attachments.push({ url, name: img.name || 'Image', isImage: true, msgIndex: i });
      }
    }
    // From msg.files (unified path — all file types)
    if (msg.files?.length) {
      for (const f of msg.files) {
        const isImg = f.type?.startsWith('image/');
        if (f.preview) {
          attachments.push({ url: f.preview, name: f.name || 'Image', isImage: true, msgIndex: i });
        } else if (f.data && isImg) {
          attachments.push({ url: `data:${f.type};base64,${f.data}`, name: f.name || 'Image', isImage: true, msgIndex: i });
        } else {
          attachments.push({ name: f.name || 'File', type: f.type || '', isImage: false, msgIndex: i });
        }
      }
    }
    // From content blocks (DB restore)
    if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (block.type === 'image_url' && block.image_url?.url) {
          attachments.push({ url: block.image_url.url, name: 'Image', isImage: true, msgIndex: i });
        } else if (block.type === 'image' && block.source?.data) {
          const mt = block.source.media_type || 'image/png';
          attachments.push({ url: `data:${mt};base64,${block.source.data}`, name: 'Image', isImage: true, msgIndex: i });
        }
      }
    }
    // From text content — detect disk-saved file notices (DB restore for non-image files)
    const textContent = typeof msg.content === 'string' ? msg.content : (Array.isArray(msg.content) ? msg.content.find(b => b.type === 'text')?.text : '') || '';
    const fileNoticeMatch = textContent.match(/\[User attached files saved to disk[^\]]*\]\n([\s\S]*?)$/);
    if (fileNoticeMatch) {
      const pathLines = fileNoticeMatch[1].trim().split('\n');
      for (const line of pathLines) {
        const pathMatch = line.match(/^\s*-\s*(.+)$/);
        if (pathMatch) {
          const fpath = pathMatch[1].trim();
          const fname = fpath.split('/').pop();
          const ext = fname.split('.').pop()?.toLowerCase() || '';
          const isImg = ['png','jpg','jpeg','gif','webp','svg','bmp','ico'].includes(ext);
          if (!attachments.some(a => a.name === fname)) {
            attachments.push({ name: fname, type: '', isImage: isImg, msgIndex: i });
          }
        }
      }
    }
  }
  return attachments;
}

function renderAttachmentsPane() {
  const attachments = collectChatAttachments();
  const grid = document.getElementById('attachments-grid');
  const empty = document.getElementById('attachments-empty');
  const fullview = document.getElementById('attachment-fullview');
  if (!grid) return;
  fullview.style.display = 'none';
  if (!attachments.length) {
    grid.style.display = 'none';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  grid.style.display = '';
  grid.innerHTML = attachments.map((a, i) => {
    if (a.isImage && a.url) {
      return `<div class="attach-card" onclick="showAttachmentFullview(${i})"><img src="${a.url}" alt="${esc(a.name)}" loading="lazy"></div>`;
    }
    const ext = a.name?.split('.').pop()?.toUpperCase() || 'FILE';
    return `<div class="attach-card attach-card-file" title="${esc(a.name)}"><div class="attach-file-ext">${esc(ext)}</div><div class="attach-file-name">${esc(a.name)}</div></div>`;
  }).join('');
}

function showAttachmentFullview(index) {
  const attachments = collectChatAttachments();
  if (index < 0 || index >= attachments.length) return;
  const a = attachments[index];
  const grid = document.getElementById('attachments-grid');
  const empty = document.getElementById('attachments-empty');
  const fullview = document.getElementById('attachment-fullview');
  grid.style.display = 'none';
  empty.style.display = 'none';
  fullview.style.display = '';
  fullview.innerHTML = `
    <button class="attach-fullview-back" onclick="renderAttachmentsPane()">Back to all</button>
    <img src="${a.url}" alt="${esc(a.name)}">
  `;
}

function _refCardHtml(ref) {
  const snippetHtml = ref.snippet ? `<div class="ref-card-snippet">${esc(ref.snippet)}</div>` : '';
  const isProject = ref.domain === 'project';
  const clickHandler = isProject
    ? `openProjectSource(this.dataset.link)`
    : `window.open('${esc(ref.link)}', '_blank')`;
  const ext = (ref.title || '').split('.').pop().toLowerCase();
  const iconBg = isProject ? {
    pdf: '#d33', docx: '#2b579a', pptx: '#d24726',
    xlsx: '#217346', eml: '#0072c6', msg: '#0072c6',
    md: 'var(--text-400)', txt: 'var(--text-400)',
  }[ext] || 'var(--accent-brand)' : '';
  const previewHtml = isProject
    ? `<div class="ref-thumb-placeholder" style="display:flex;align-items:center;justify-content:center;background:${iconBg};color:#fff;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em">${esc(ext || 'file')}</div>`
    : `<img class="ref-thumb" src="${esc(`https://api.microlink.io/?url=${encodeURIComponent(ref.link)}&screenshot=true&meta=false&embed=screenshot.url`)}" onerror="this.parentElement.innerHTML='<div class=\\'ref-thumb-placeholder\\'><svg viewBox=\\'0 0 24 24\\' width=\\'32\\' height=\\'32\\' fill=\\'none\\' stroke=\\'var(--text-400)\\' stroke-width=\\'1\\' opacity=\\'0.3\\'><path d=\\'M12 2a10 10 0 110 20 10 10 0 010-20z\\'/><path d=\\'M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10A15.3 15.3 0 0112 2z\\'/></svg></div>'" loading="lazy" alt="">`;
  const faviconHtml = isProject
    ? ''
    : `<img class="ref-favicon" src="${esc(ref.favicon)}" onerror="this.style.display='none'" alt="">`;
  const domainLabel = isProject ? 'Project source' : esc(ref.domain);
  return `
    <div class="ref-card" data-link="${esc(ref.link)}" onclick="${clickHandler}">
      <div class="ref-card-preview">
        ${faviconHtml}
        ${previewHtml}
        <span class="ref-domain-pill">${domainLabel}</span>
      </div>
      <div class="ref-card-body">
        <div class="ref-card-title">${esc(ref.title)}</div>
        ${snippetHtml}
        <div class="ref-card-url" style="word-break:break-all">${esc(ref.link)}</div>
      </div>
    </div>
  `;
}

function renderReferencesPane() {
  const { cited, searched } = collectChatReferences();
  const container = document.getElementById('refs-content');
  if (!container) return;
  if (!cited.length && !searched.length) {
    container.innerHTML = '<div class="attach-empty">No sources in this chat</div>';
    return;
  }
  let html = '';
  if (cited.length) {
    html += `
      <div class="refs-section">
        <div class="refs-section-header">
          <span class="refs-section-label">Zitiert</span>
          <span class="refs-section-count">${cited.length}</span>
        </div>
        <div class="refs-section-body">
          ${cited.map(_refCardHtml).join('')}
        </div>
      </div>`;
  }
  if (searched.length) {
    // Default-collapsed via <details>. If there are NO cited refs (e.g.
    // a refusal / no-source answer), open the searched section by default
    // so the user isn't staring at an empty pane.
    const open = cited.length === 0 ? 'open' : '';
    html += `
      <details class="refs-section refs-section-searched" ${open}>
        <summary class="refs-section-header">
          <span class="refs-section-disclosure">▸</span>
          <span class="refs-section-label">Durchsucht</span>
          <span class="refs-section-count">${searched.length}</span>
        </summary>
        <div class="refs-section-body">
          ${searched.map(_refCardHtml).join('')}
        </div>
      </details>`;
  }
  container.innerHTML = html;
}

// Open a project document (PDF/DOCX/PPTX/XLSX/EML/.md/...) in a new tab.
// PDFs render inline in the browser via the application/pdf MIME from the
// download endpoint; everything else triggers a normal save-as. We fetch
// with the auth-token header and pipe to a blob URL because the download
// endpoint is auth-gated and we can't put the JWT on a query string safely.
async function openProjectSource(absPath) {
  if (!absPath) return;
  try {
    const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(absPath)}`;
    const resp = await fetch(url, {
      headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
    });
    if (!resp.ok) {
      const err = await resp.text().catch(() => '');
      showToast(`Cannot open ${absPath.split('/').pop()}: ${resp.status} ${err.slice(0, 80)}`, true);
      return;
    }
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    // Open in new tab. The browser uses the response's Content-Type to
    // decide inline-render vs download. PDFs render inline; binaries
    // (.docx, .xlsx, .pptx) download.
    window.open(blobUrl, '_blank');
    // Revoke after a delay so the new tab has time to load. 60s is
    // arbitrary but long enough; the browser keeps a reference.
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
  } catch (e) {
    showToast(`Failed to open: ${e.message || e}`, true);
  }
}

// Legacy compat
function initArtifactResize() { initRightPanelResize(); }

function artifactTypeIcon(type) {
  const icons = {
    code: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    html: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    svg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 12l2 2 4-4"/></svg>',
    image: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
    markdown: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    document: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    text: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  };
  return icons[type] || icons.text;
}

async function openArtifactPanel(artifactId, version) {
  state.activeArtifactId = artifactId;
  state.artifactSourceMode = false;
  document.getElementById('artifact-source-btn')?.classList.remove('active');

  // Find artifact in registry
  const chat = state.activeChat;
  if (!chat) return;
  const sessionId = chat.sessionId;
  const artifacts = state.artifacts[sessionId] || [];
  const artifact = artifacts.find(a => a.id === artifactId);
  if (!artifact) {
    showToast('Artifact not found', true);
    return;
  }

  // Update header
  document.getElementById('artifact-title').textContent = artifact.name;

  // Populate version selector
  const sel = document.getElementById('artifact-version-select');
  sel.innerHTML = '';
  const versions = artifact.versions || [];
  for (const v of versions) {
    const opt = document.createElement('option');
    opt.value = v.version;
    opt.textContent = `v${v.version}`;
    sel.appendChild(opt);
  }
  const targetVersion = version || (versions.length ? versions[versions.length - 1].version : 1);
  sel.value = targetVersion;
  state.activeArtifactVersion = targetVersion;

  // Open unified right panel on artifacts tab
  openRightPanel('artifacts');

  // Show actions bar
  document.getElementById('artifact-actions').style.display = '';

  // Load content
  await loadArtifactVersion(targetVersion);
}

async function loadArtifactVersion(version) {
  const artifactId = state.activeArtifactId;
  if (!artifactId) return;
  state.activeArtifactVersion = version;
  const sel = document.getElementById('artifact-version-select');
  if (sel) sel.value = version;

  const container = document.getElementById('artifact-content');
  container.innerHTML = '<div class="artifact-empty"><div class="wave-bars"><span></span><span></span><span></span></div></div>';

  try {
    const data = await API.getArtifactContent(artifactId, version);
    if (!data || !data.content) {
      console.error('[artifact] Empty response for', artifactId, 'version', version, 'data:', data);
      container.innerHTML = `<div class="artifact-empty">No content available (version ${version})</div>`;
      return;
    }
    renderArtifactContent(data.content, data.type, data.name, data.encoding);
  } catch (e) {
    console.error('[artifact] Load failed for', artifactId, 'version', version, e);
    container.innerHTML = `<div class="artifact-empty">Failed to load content: ${e.message || e}</div>`;
  }
}

function shareCurrentArtifact() {
  const artifactId = state.activeArtifactId;
  if (!artifactId) { showToast('No artifact open', true); return; }
  const title = document.getElementById('artifact-title')?.textContent || 'artifact';
  shareDialog('artifact', artifactId, '', { title });
}

function renderArtifactContent(content, type, name, encoding) {
  const container = document.getElementById('artifact-content');
  const ext = name.split('.').pop().toLowerCase();

  // Store raw content for source toggle
  container._rawContent = content;
  container._rawType = type;
  container._rawName = name;
  container._rawEncoding = encoding;

  if (state.artifactSourceMode && type !== 'image') {
    // Source view — always raw text
    container.innerHTML = `<pre class="artifact-code"><code>${esc(content)}</code></pre>`;
    return;
  }

  switch (type) {
    case 'html':
      container.innerHTML = `<iframe sandbox="allow-scripts allow-same-origin" srcdoc="${esc(content)}" style="width:100%;height:100%;border:none"></iframe>`;
      break;
    case 'svg':
      container.innerHTML = `<div style="padding:24px;display:flex;align-items:center;justify-content:center;height:100%">${content}</div>`;
      break;
    case 'image': {
      const imgExt = ext === 'svg' ? 'svg+xml' : ext;
      const src = encoding === 'base64' ? `data:image/${imgExt};base64,${content}` : content;
      container.innerHTML = `<div style="padding:24px;display:flex;align-items:center;justify-content:center;height:100%;background:var(--bg-100)"><img src="${src}" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:8px"></div>`;
      break;
    }
    case 'markdown':
      container.innerHTML = `<div class="artifact-markdown msg-content">${renderMarkdown(content)}</div>`;
      // Apply syntax highlighting to code blocks
      container.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch(e) {} });
      break;
    case 'code':
    default: {
      const lang = (typeof hljs !== 'undefined' && hljs.getLanguage(ext)) ? ext : 'plaintext';
      let highlighted;
      try {
        highlighted = hljs.highlight(content, { language: lang }).value;
      } catch(e) {
        highlighted = esc(content);
      }
      container.innerHTML = `<pre class="artifact-code"><code class="hljs language-${lang}">${highlighted}</code></pre>`;
      break;
    }
  }
}

function closeArtifactPanel() {
  closeRightPanel();
}

/* ═══════════════════════════════════════════════════════════
   REFERENCES PANEL
   ═══════════════════════════════════════════════════════════ */

// Mirror of ChatHandlerMixin._is_document_source — drops synthetic
// MemPalace addresses (chat turns, summaries, user-profile sections)
// that aren't openable file paths. Applied to BOTH live server-stored
// refs and legacy-fallback parsing so persisted-bad refs don't render.
function isDocumentRef(ref) {
  const sf = ref && (ref.source_file || ref.link) || '';
  if (!sf) return false;
  if (sf.startsWith('session/') || sf.startsWith('user/') || sf.startsWith('team/')) return false;
  if (/^\d+$/.test(sf)) return false;
  if (/^[a-f0-9]+#summary$/i.test(sf)) return false;
  return true;
}

function extractReferencesFromToolResult(msg) {
  // References are extracted server-side (ChatHandlerMixin._extract_references)
  // and stored in msg.references. The client just reads that field — no
  // per-tool name checks, no path resolution, no regex fallbacks here.
  if (msg.role !== 'tool_result') return [];
  if (Array.isArray(msg.references) && msg.references.length) {
    // Filter persisted-bad refs from legacy server versions.
    const onlyDocs = msg.references.filter(r => !r || r.domain !== 'project' || isDocumentRef(r));
    return onlyDocs;
  }
  // Legacy fallback: old persisted messages without server-side refs field.
  if (!msg.result) return [];
  const isWebTool = msg.name === 'exa_search' || msg.name === 'web_fetch';
  const isProjectTool = msg.name === 'mempalace_query'
                     || msg.name === 'mempalace_kg_query'
                     || msg.name === 'mempalace_kg_search'
                     || msg.name === 'mempalace_kg_neighbors';
  if (!isWebTool && !isProjectTool) return [];
  const refs = [];
  const resultStr = typeof msg.result === 'string' ? msg.result : JSON.stringify(msg.result);

  // ── Project-knowledge tools: surface source_file as clickable refs ──
  // mempalace_query returns {drawers: [{source_file, snippet, ...}]}.
  // mempalace_kg_* return {triples: [{source_file, source_drawer_id, ...}]}.
  // We dedupe by source_file, render basename as title, link = the
  // original-binary path (resolving .brain-extracted/foo.pdf.md → foo.pdf
  // via the converter's naming convention).
  if (isProjectTool) {
    let parsed = null;
    try { parsed = JSON.parse(resultStr); } catch(e) { parsed = null; }
    const items = parsed
      ? [...(parsed.drawers || []),
         ...(parsed.triples || []),
         ...(parsed.edges || [])]
      : [];
    const seen = new Set();
    const resolveOriginal = (sf) => {
      if (!sf) return sf;
      // Case 1: .brain-extracted/<name>.<ext>.md → <name>.<ext> in the parent dir
      const m = sf.match(/^(.+)\/\.brain-extracted\/(.+)\.md$/);
      if (m) return `${m[1]}/${m[2]}`;
      // Case 2: defensive — any path ending in `.<binext>.md` where binext is
      // a known binary doc-convert format. Catches cases where the agent
      // cited the .md companion in plain text without the .brain-extracted
      // prefix, or where a future doc_convert layout drops the prefix.
      const m2 = sf.match(/^(.+\.(pdf|docx|pptx|xlsx|xlsm|eml|msg))\.md$/i);
      if (m2) return m2[1];
      return sf;
    };
    for (const it of items) {
      const sf = it && it.source_file;
      if (!sf || seen.has(sf)) continue;
      seen.add(sf);  // claim before predicate so regex sweep can't re-add
      // Drop chat-derived drawers and any other synthetic non-file
      // sources (user-profile sections, team-wing addresses, …).
      if (!isDocumentRef({ source_file: sf, ...(it.room ? {} : {}) }) ||
          ['chat', 'chat_summary', 'chat_attachment', 'user_profile'].includes(it.room || '')) {
        continue;
      }
      const original = resolveOriginal(sf);
      const basename = original.split('/').pop() || original;
      // Use the snippet from a drawer if present; otherwise format the
      // triple/edge. Keeps each ref card informative without bloat.
      let snippet = '';
      if (it.snippet) snippet = String(it.snippet).slice(0, 280);
      else if (it.subject && it.predicate && it.object) {
        snippet = `(${it.subject}) — [${it.predicate}] → (${it.object})`.slice(0, 280);
      } else if (it.text) snippet = String(it.text).slice(0, 280);
      refs.push({
        title: basename,
        link: original,           // absolute path to the original binary
        snippet: snippet,
        domain: 'project',        // marker so the panel can render differently
        favicon: '',
        source_file: sf,          // raw drawer/triple source for debugging
      });
    }
    // Regex top-up — always runs, not just when JSON parse failed. Even
    // when JSON.parse succeeds, the persisted `metadata.tools[i].result`
    // may be truncated past the first drawer (capped at ~4KB on the
    // server), so the JSON object is well-formed but only contains the
    // drawers that fit. Sweep the raw string for any `"source_file": "..."`
    // tokens, skip ones already in `seen`, and add the rest as refs with
    // an empty snippet. Without this top-up, reload of a multi-source
    // answer loses every reference past the first.
    {
      const sfMatches = [...resultStr.matchAll(/"source_file"\s*:\s*"([^"]+)"/g)];
      for (const m of sfMatches) {
        const sf = m[1];
        if (!sf || seen.has(sf)) continue;
        seen.add(sf);
        if (!isDocumentRef({ source_file: sf })) continue;
        const original = resolveOriginal(sf);
        const basename = original.split('/').pop() || original;
        refs.push({
          title: basename, link: original, snippet: '',
          domain: 'project', favicon: '', source_file: sf,
        });
      }
    }
    return refs;
  }


  // Worker-subagent envelope: the raw result is stored as an artifact and only
  // a summary + pre-extracted `references` array reach the client. Prefer that
  // explicit list over re-parsing the summary text.
  try {
    const data = JSON.parse(resultStr);
    if (data && data.worker && Array.isArray(data.references)) {
      for (const r of data.references) {
        if (!r || !r.link) continue;
        refs.push({
          title: r.title || r.domain || r.link,
          link: r.link,
          snippet: r.snippet || '',
          domain: r.domain || '',
          favicon: r.domain ? `https://www.google.com/s2/favicons?domain=${r.domain}&sz=32` : '',
        });
      }
      if (refs.length) return refs;
    }
  } catch(e) { /* fall through to legacy parsing */ }

  // Try full JSON parse first (legacy direct-tool path — small results that fit inline)
  try {
    const data = JSON.parse(resultStr);
    if (data.results && Array.isArray(data.results)) {
      for (const r of data.results) {
        if (r.link || r.url) {
          const url = r.link || r.url;
          let domain = '';
          try { domain = new URL(url).hostname.replace('www.', ''); } catch(e) {}
          refs.push({
            title: r.title || domain || url,
            link: url,
            snippet: (r.snippet || '').substring(0, 200),
            domain: domain,
            favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32`,
          });
        }
      }
      return refs;
    } else if (data.url && (data.content || data.status)) {
      let domain = '';
      try { domain = new URL(data.url).hostname.replace('www.', ''); } catch(e) {}
      let title = domain;
      const titleMatch = (data.content || '').match(/<title[^>]*>([^<]+)<\/title>/i);
      if (titleMatch) title = titleMatch[1].trim();
      refs.push({ title, link: data.url, snippet: '', domain, favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32` });
      return refs;
    }
  } catch(e) {
    // JSON truncated — fall back to regex extraction
  }

  // Regex fallback for truncated JSON (tool results capped at 500 chars)
  const decodeJsonStr = (s) => { try { return JSON.parse('"' + s + '"'); } catch(e) { return s; } };
  if (msg.name === 'exa_search') {
    const titleLinkPairs = [...resultStr.matchAll(/"title"\s*:\s*"([^"]*)"[^}]*?"link"\s*:\s*"([^"]*)"/g)];
    for (const match of titleLinkPairs) {
      const [, rawTitle, link] = match;
      const title = decodeJsonStr(rawTitle);
      let domain = '';
      try { domain = new URL(link).hostname.replace('www.', ''); } catch(e) {}
      refs.push({ title, link, snippet: '', domain, favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32` });
    }
  } else if (msg.name === 'web_fetch') {
    const urlMatch = resultStr.match(/"url"\s*:\s*"([^"]*)"/);
    if (urlMatch) {
      const url = urlMatch[1];
      let domain = '';
      try { domain = new URL(url).hostname.replace('www.', ''); } catch(e) {}
      let title = domain;
      const titleMatch = resultStr.match(/<title[^>]*>([^<]+)<\/title>/i);
      if (titleMatch) title = titleMatch[1].trim();
      refs.push({ title, link: url, snippet: '', domain, favicon: `https://www.google.com/s2/favicons?domain=${domain}&sz=32` });
    }
  }
  return refs;
}

// Pull `[Quelle: <basename> — "..."]` and `[source: <basename>]` markers
// out of an assistant message's rendered content. Returns a Set of
// normalised basenames (lowercased, .md companion suffix stripped) so
// `policy.pdf` and `policy.pdf.md` and `Policy.PDF` all collapse to the
// same key. Used to split refs into cited-vs-searched sections.
function extractCitedBasenamesFromText(text) {
  const set = new Set();
  if (!text || typeof text !== 'string') return set;
  // Match both `[Quelle: foo.pdf — "..."]` and `[source: foo.pdf]` and
  // `[Quelle: foo.pdf §3.2]`. Capture the basename only (everything up to
  // the first em-dash, en-dash, hyphen-with-spaces, §, or closing bracket).
  // Em-dash variants: U+2014 (—), U+2013 (–), ASCII " - " with spaces.
  const re = /\[(?:Quelle|source|Source|QUELLE):\s*([^\]—–§]+?)(?:\s*[—–]|\s+-\s+|\s+§|\])/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    set.add(normaliseCitationBasename(raw));
  }
  return set;
}

function normaliseCitationBasename(s) {
  let n = String(s || '').trim().toLowerCase();
  // Strip path prefix if present
  const slash = n.lastIndexOf('/');
  if (slash >= 0) n = n.substring(slash + 1);
  // Strip trailing .md companion suffix when on a known binary
  const m = n.match(/^(.+\.(pdf|docx|pptx|xlsx|xlsm|eml|msg))\.md$/);
  if (m) n = m[1];
  return n;
}

function refBasenameKey(ref) {
  return normaliseCitationBasename(ref.title || ref.link || '');
}

function collectChatReferences() {
  const chat = state.activeChat;
  if (!chat) return { cited: [], searched: [] };
  const sessionId = chat.sessionId;
  // Use cached if available
  if (state.chatReferences[sessionId]) return state.chatReferences[sessionId];

  // Build the union of cited basenames across every assistant turn in the
  // chat. A ref is "cited" if any assistant message text contains a
  // `[Quelle: <basename>...]` marker matching its basename.
  const citedBasenames = new Set();
  for (const msg of chat.messages) {
    if (msg.role === 'assistant' && msg.content) {
      for (const k of extractCitedBasenamesFromText(msg.content)) citedBasenames.add(k);
    }
  }

  const cited = [];
  const searched = [];
  const seen = new Set();
  // Walk all messages: pick up live tool_result rows (during streaming) AND
  // assistant.metadata.tools[] (after reload from DB). Both feed the same
  // extractor — a synthetic tool_result-shaped object lets us reuse the
  // parser.
  for (const msg of chat.messages) {
    const candidates = [];
    if (msg.role === 'tool_result') candidates.push(msg);
    if (msg.role === 'assistant' && msg.metadata && Array.isArray(msg.metadata.tools)) {
      for (const t of msg.metadata.tools) {
        if (!t || !t.name) continue;
        candidates.push({ role: 'tool_result', name: t.name, result: t.result });
      }
    }
    for (const c of candidates) {
      const extracted = extractReferencesFromToolResult(c);
      for (const ref of extracted) {
        if (!seen.has(ref.link)) {
          seen.add(ref.link);
          if (citedBasenames.has(refBasenameKey(ref))) cited.push(ref);
          else searched.push(ref);
        }
      }
    }
  }
  const out = { cited, searched };
  state.chatReferences[sessionId] = out;
  return out;
}

function addChatReference(ref) {
  const chat = state.activeChat;
  if (!chat) return;
  const sessionId = chat.sessionId;
  // Live refs always go to "searched" — the cited/searched split is
  // determined by the assistant message text, which doesn't yet exist
  // when streaming starts. After stream end, the cache is invalidated
  // (see invalidateChatReferences) and re-split lazily on the next read.
  if (!state.chatReferences[sessionId]) state.chatReferences[sessionId] = { cited: [], searched: [] };
  const cache = state.chatReferences[sessionId];
  const allRefs = cache.cited.concat(cache.searched);
  if (!allRefs.some(r => r.link === ref.link)) {
    cache.searched.push(ref);
  }
}

function invalidateChatReferences(sessionId) {
  if (sessionId && state.chatReferences[sessionId]) {
    delete state.chatReferences[sessionId];
  }
}

function getReferencesForMessage(idx) {
  // Look backward from this assistant message for tool data — either live
  // tool_result rows (during streaming) or this very assistant message's
  // own metadata.tools[] array (after reload from DB). Stops at the
  // previous user/human message.
  // Returns {cited, searched}: refs whose basename matches a `[Quelle:...]`
  // marker in this assistant message's text go into cited; the rest go
  // into searched (still loaded into MemPalace, just not actually used in
  // the answer).
  const chat = state.activeChat;
  if (!chat) return { cited: [], searched: [] };
  const self = chat.messages[idx];
  const citedBasenames = (self && self.role === 'assistant')
    ? extractCitedBasenamesFromText(self.content || '')
    : new Set();
  const cited = [];
  const searched = [];
  const seen = new Set();
  const ingest = (synth) => {
    for (const ref of extractReferencesFromToolResult(synth)) {
      if (!seen.has(ref.link)) {
        seen.add(ref.link);
        if (citedBasenames.has(refBasenameKey(ref))) cited.push(ref);
        else searched.push(ref);
      }
    }
  };
  // First: this assistant message's own metadata.tools[] (the post-reload path)
  if (self && self.role === 'assistant' && self.metadata && Array.isArray(self.metadata.tools)) {
    for (const t of self.metadata.tools) {
      if (!t || !t.name) continue;
      ingest({ role: 'tool_result', name: t.name, result: t.result });
    }
  }
  // Then walk back for any live tool_result rows (the streaming path)
  for (let j = idx - 1; j >= 0; j--) {
    const m = chat.messages[j];
    if (m.role === 'user' || m.role === 'human') break;
    if (m.role === 'tool_result') ingest(m);
  }
  return { cited, searched };
}

function openReferencesPanel(highlightLink) {
  const { cited, searched } = collectChatReferences();
  if (!cited.length && !searched.length) { showToast('No sources in this chat'); return; }
  openRightPanel('references');
  if (highlightLink) {
    setTimeout(() => {
      const cards = document.querySelectorAll('#refs-content .ref-card');
      for (const card of cards) {
        if (card.dataset.link === highlightLink) {
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.style.outline = '2px solid var(--accent-brand)';
          card.style.outlineOffset = '2px';
          setTimeout(() => { card.style.outline = ''; card.style.outlineOffset = ''; }, 2000);
          break;
        }
      }
    }, 50);
  }
}

function closeReferencesPanel() {
  closeRightPanel();
}

function initRefsResizeHandle() { /* no-op — merged into initRightPanelResize */ }

/* ═══════════════════════════════════════════════════════════
   USED-MEMORY GRAPH MODAL
   For an assistant message that called any project-knowledge tool
   (mempalace_query / mempalace_kg_*), this surfaces what the model
   actually retrieved: drawers (text snippets) and triples
   (subject — predicate → object), in a graph view that lets the
   user audit the answer.
   ═══════════════════════════════════════════════════════════ */

// Cheap check: did this assistant message use any of the project-knowledge
// tools? Used to gate the inline action button so it only appears when
// there's something meaningful to show.
function messageUsedKnowledge(idx) {
  const chat = state.activeChat;
  if (!chat) return false;
  const m = chat.messages[idx];
  if (!m || m.role !== 'assistant') return false;
  const KG_TOOLS = new Set(['mempalace_query', 'mempalace_kg_query',
                            'mempalace_kg_search', 'mempalace_kg_neighbors']);
  // Post-reload path: tools live in metadata.tools[]
  if (m.metadata && Array.isArray(m.metadata.tools)) {
    if (m.metadata.tools.some(t => t && KG_TOOLS.has(t.name))) return true;
  }
  // Live path: tool_result rows between this message and the previous user msg
  for (let j = idx - 1; j >= 0; j--) {
    const prev = chat.messages[j];
    if (prev.role === 'user' || prev.role === 'human') break;
    if (prev.role === 'tool_result' && KG_TOOLS.has(prev.name)) return true;
  }
  return false;
}

// Pull every drawer + triple the message saw across all four tools.
// Returns {drawers: [...], triples: [...]} deduped by content.
function _collectKnowledgeForMessage(idx) {
  const chat = state.activeChat;
  if (!chat) return { drawers: [], triples: [] };
  const KG_TOOLS = new Set(['mempalace_query', 'mempalace_kg_query',
                            'mempalace_kg_search', 'mempalace_kg_neighbors']);
  const tools = [];
  const m = chat.messages[idx];
  if (m && m.metadata && Array.isArray(m.metadata.tools)) {
    for (const t of m.metadata.tools) {
      if (t && KG_TOOLS.has(t.name)) tools.push(t);
    }
  }
  // Live tool_result rows fall back when metadata.tools[] is empty (mid-stream).
  if (!tools.length) {
    for (let j = idx - 1; j >= 0; j--) {
      const prev = chat.messages[j];
      if (prev.role === 'user' || prev.role === 'human') break;
      if (prev.role === 'tool_result' && KG_TOOLS.has(prev.name)) {
        tools.push({ name: prev.name, result: prev.result });
      }
    }
  }
  const drawers = [];
  const drawerSeen = new Set();
  const triples = [];
  const tripleSeen = new Set();
  for (const t of tools) {
    let parsed = null;
    const raw = typeof t.result === 'string' ? t.result : JSON.stringify(t.result || '');
    try { parsed = JSON.parse(raw); } catch(_) { continue; }
    if (!parsed) continue;
    for (const d of (parsed.drawers || [])) {
      const key = d.id || `${d.source_file}#${(d.text||d.snippet||'').slice(0,40)}`;
      if (drawerSeen.has(key)) continue;
      drawerSeen.add(key);
      drawers.push(d);
    }
    for (const tr of (parsed.triples || parsed.edges || [])) {
      if (!tr || !tr.subject || !tr.predicate || !tr.object) continue;
      const key = `${tr.subject}|${tr.predicate}|${tr.object}`;
      if (tripleSeen.has(key)) continue;
      tripleSeen.add(key);
      triples.push(tr);
    }
  }
  return { drawers, triples };
}

// Build SVG graph from drawers + triples. Layout is "document → subject →
// object" reading left-to-right — matches how the user reads a triple in
// the relations list ("policy.pdf says: subject — predicate → object") and
// keeps doc circles meaningfully connected instead of orphaned.
//
// Sizing is dataset-aware: height grows with row count so labels never
// overlap, columns are hidden entirely when empty (a docless query
// renders 2 columns instead of forcing a tiny 3rd column with whitespace).
function _renderKnowledgeGraphSvg(drawers, triples, opts) {
  opts = opts || {};
  const W = opts.width || 880;
  // Node radii / spacing. Pick wider rectangles (rounded) instead of pure
  // circles — entity names like "Änderungen an den Berechtigungen" don't
  // fit in a 14px circle. Pills have width based on content with a sane
  // cap, height fixed.
  const PILL_H = 28;
  const ROW_GAP = 22;        // vertical gap between adjacent rows in a column
  const SIDE_PAD = 24;
  const TOP_PAD = 28;

  // Build node maps + edges. Same id can serve as subject AND object across
  // triples; we still render it once and route both edges to/from it.
  const nodes = new Map();   // id → {id, label, kind, sources:Set}
  const edges = [];          // {from, to, label, source_file}
  const ensureNode = (id, label, kind) => {
    if (!nodes.has(id)) nodes.set(id, { id, label: label || id, kind, sources: new Set() });
    return nodes.get(id);
  };
  // Triples first so we know which entities exist before docs.
  for (const tr of triples) {
    const s = ensureNode('e:' + tr.subject, tr.subject, 'subject');
    const o = ensureNode('e:' + tr.object,  tr.object,  'object');
    if (tr.source_file) { s.sources.add(tr.source_file); o.sources.add(tr.source_file); }
    edges.push({ from: s.id, to: o.id, label: tr.predicate, source_file: tr.source_file || '' });
  }
  // Mark dual-role entities (appear as both subject and object). They
  // render in a centre column to avoid double-drawing.
  for (const tr of triples) {
    const s = nodes.get('e:' + tr.subject);
    const o = nodes.get('e:' + tr.object);
    if (s && o && tr.subject === tr.object) continue;
  }
  // Document name normaliser — strip the trailing `.md` companion suffix
  // (`policy.pdf.md` → `policy.pdf`) and the binary ext for display so the
  // pill reads "policy" not "policy.pdf.md". Used for both the node id
  // (so drawers and triples that reference the same source merge) and the
  // visible label.
  const _binExt = /\.(pdf|docx|pptx|xlsx|xlsm|eml|msg)$/i;
  const docKeyOf = (sf) => {
    if (!sf) return '';
    const base = sf.split('/').pop().replace(/\.md$/, '');
    return base.toLowerCase();   // case-insensitive match across drawer/triple paths
  };
  const docLabelOf = (sf) => {
    if (!sf) return 'unknown';
    return sf.split('/').pop().replace(/\.md$/, '').replace(_binExt, '');
  };

  // Doc nodes from drawers AND from any triple's source_file. Without the
  // triple-derived nodes the user can't see which document each relation
  // came from when the agent extracted facts without fetching that doc's
  // drawer (common: KG search returns triples by predicate, doesn't pull
  // the underlying chunk).
  for (const d of drawers) {
    const sf = d.source_file;
    if (!sf) continue;
    const id = 'doc:' + docKeyOf(sf);
    ensureNode(id, docLabelOf(sf), 'doc');
  }
  for (const tr of triples) {
    const sf = tr.source_file;
    if (!sf) continue;
    const id = 'doc:' + docKeyOf(sf);
    ensureNode(id, docLabelOf(sf), 'doc');
  }

  // Doc → subject edges: dotted "this fact came from that document" link.
  // Resolved by basename so a drawer with bare-name source_file lines up
  // with a triple carrying the same name's full path.
  const docEdgeKeys = new Set();
  for (const tr of triples) {
    if (!tr.source_file) continue;
    const docId = 'doc:' + docKeyOf(tr.source_file);
    const subjId = 'e:' + tr.subject;
    if (!nodes.has(docId)) continue;
    const k = docId + '|' + subjId;
    if (docEdgeKeys.has(k)) continue;
    docEdgeKeys.add(k);
    edges.unshift({ from: docId, to: subjId, label: '', source_file: tr.source_file, dotted: true });
  }

  // Column buckets. Subject column = entities that appear as a subject
  // somewhere; object column = those that appear ONLY as an object.
  // Anything in BOTH stays in the subject column (where the relation
  // arrows fan out from).
  const subjects = new Set();
  const objects  = new Set();
  for (const tr of triples) { subjects.add('e:' + tr.subject); objects.add('e:' + tr.object); }
  const docNodes = [...nodes.values()].filter(n => n.kind === 'doc');
  const subjNodes = [...nodes.values()].filter(n => subjects.has(n.id));
  const objNodes  = [...nodes.values()].filter(n => !subjects.has(n.id) && objects.has(n.id));

  // Column geometry — only allocate a column when it has content.
  const cols = [];
  if (docNodes.length)  cols.push({ key: 'doc',  list: docNodes,  pillW: 200 });
  if (subjNodes.length) cols.push({ key: 'subj', list: subjNodes, pillW: 220 });
  if (objNodes.length)  cols.push({ key: 'obj',  list: objNodes,  pillW: 220 });
  // Distribute X across actual columns. With 1 column we centre it; with
  // 2 we space them nicely; with 3 we evenly distribute.
  const innerW = W - 2*SIDE_PAD;
  cols.forEach((col, i) => {
    if (cols.length === 1) col.x = W / 2;
    else col.x = SIDE_PAD + col.pillW/2 + (innerW - col.pillW) * (i / (cols.length - 1));
  });
  // Rows: tallest column drives height. Each pill takes PILL_H + ROW_GAP.
  const maxRows = Math.max(1, ...cols.map(c => c.list.length));
  const H = TOP_PAD * 2 + maxRows * PILL_H + Math.max(0, maxRows - 1) * ROW_GAP;
  // Place every node by walking each column top-to-bottom, vertically
  // centred so short columns don't sit at the top.
  for (const col of cols) {
    const totalH = col.list.length * PILL_H + Math.max(0, col.list.length - 1) * ROW_GAP;
    const yStart = (H - totalH) / 2 + PILL_H / 2;
    col.list.forEach((node, i) => {
      node.x = col.x;
      node.y = yStart + i * (PILL_H + ROW_GAP);
      node.pillW = col.pillW;
    });
  }

  // Helpers for label fit. Approx 6.5px per character at 11px font is
  // close enough for sans-serif; truncate when overflowing.
  const fitLabel = (s, pxWidth) => {
    const maxChars = Math.max(6, Math.floor(pxWidth / 6.8));
    return s.length > maxChars ? s.slice(0, maxChars - 1) + '…' : s;
  };

  // Compose SVG. Edges below nodes; arrowhead marker for direction.
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="background:var(--bg-100);border-radius:8px;border:1px solid var(--border-100);max-height:62vh;display:block" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="kg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0,0 L10,5 L0,10 z" fill="var(--accent-brand)" opacity="0.85"/>
      </marker>
    </defs>`;

  // Edges as cubic Béziers — straighter when columns are close, more
  // bowed when far. Label rides along the curve at 50% via textPath
  // anchored to a unique <path> per edge, so labels never overlap nodes.
  edges.forEach((e, idx) => {
    const a = nodes.get(e.from);
    const b = nodes.get(e.to);
    if (!a || !b) return;
    // Connect at the right edge of `a` and left edge of `b` so arrows
    // don't pierce the pill rectangles.
    const ax = a.x + a.pillW / 2;
    const bx = b.x - b.pillW / 2;
    const dx = bx - ax;
    // Bezier control points pulled toward the midline horizontally; this
    // gives a gentle S-curve when src/dst rows differ in y.
    const c1x = ax + dx * 0.45;
    const c1y = a.y;
    const c2x = bx - dx * 0.45;
    const c2y = b.y;
    const pathId = `kg-edge-${idx}`;
    const dotted = e.dotted ? 'stroke-dasharray="3 4" stroke-opacity="0.35"' : 'stroke-opacity="0.6"';
    svg += `<path id="${pathId}" d="M ${ax} ${a.y} C ${c1x} ${c1y} ${c2x} ${c2y} ${bx} ${b.y}"
                  fill="none" stroke="var(--accent-brand)" ${dotted} stroke-width="1.4"
                  ${e.dotted ? '' : 'marker-end="url(#kg-arrow)"'}/>`;
    if (e.label) {
      svg += `<text font-family="var(--font-mono)" font-size="10" fill="var(--text-300)"
                    style="paint-order:stroke;stroke:var(--bg-100);stroke-width:3px">
                <textPath href="#${pathId}" startOffset="50%" text-anchor="middle">${esc(e.label)}</textPath>
              </text>`;
    }
  });

  // Nodes as rounded rectangles ("pills") with text inside — fits long
  // German entity names that the previous circle layout truncated to
  // unrecognisable stubs.
  for (const n of nodes.values()) {
    const fill = n.kind === 'doc' ? '#d97706' : '#2563eb';
    const x = n.x - n.pillW / 2;
    const y = n.y - PILL_H / 2;
    const label = fitLabel(n.label, n.pillW - 16);
    svg += `<g data-node-id="${esc(n.id)}">
      <rect x="${x}" y="${y}" width="${n.pillW}" height="${PILL_H}" rx="14" ry="14"
            fill="${fill}" fill-opacity="0.14" stroke="${fill}" stroke-width="1.5"/>
      <text x="${n.x}" y="${n.y + 4}" font-size="11" fill="var(--text-100)"
            text-anchor="middle" font-family="var(--font-sans, system-ui)">${esc(label)}</text>
      <title>${esc(n.label)}</title>
    </g>`;
  }

  // Column headers — small, faded, only when there's a doc column to
  // disambiguate. Two-column subject/object layouts are obvious from
  // arrow direction.
  if (cols.length >= 2 && docNodes.length) {
    const headerLabel = (key) => key === 'doc' ? 'Documents'
                                : key === 'subj' ? 'Subjects' : 'Objects';
    cols.forEach(col => {
      svg += `<text x="${col.x}" y="14" font-size="10" fill="var(--text-400)"
                    text-anchor="middle" font-family="var(--font-sans, system-ui)"
                    style="text-transform:uppercase;letter-spacing:0.05em">${headerLabel(col.key)}</text>`;
    });
  }

  svg += '</svg>';
  return svg;
}

function openUsedMemoryGraph(idx) {
  const { drawers, triples } = _collectKnowledgeForMessage(idx);
  if (!drawers.length && !triples.length) {
    showToast('This response did not pull from project memory');
    return;
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.zIndex = '12000';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  const svg = _renderKnowledgeGraphSvg(drawers, triples);
  // Drawer list: each card shows source file + snippet, click scrolls
  // (and a click could later open the source — reuses openProjectSource
  // when available).
  const drawerCards = drawers.length
    ? drawers.map(d => {
        const sf = d.source_file || 'unknown';
        const base = sf.split('/').pop().replace(/\.md$/, '');
        const text = (d.snippet || d.text || '').slice(0, 320);
        return `<div style="border:1px solid var(--border-100);border-radius:6px;padding:8px 10px;margin-bottom:6px;font-size:12px;background:var(--bg-100)">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:3px;background:#d97706;color:#fff;font-size:8px;font-weight:700">DOC</span>
            <span style="font-weight:500;color:var(--text-100);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(base)}</span>
            ${typeof d.similarity === 'number' ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">sim ${d.similarity.toFixed(2)}</span>` : ''}
          </div>
          <div style="color:var(--text-300);line-height:1.4;white-space:pre-wrap">${esc(text)}</div>
        </div>`;
      }).join('')
    : '<div style="color:var(--text-400);font-size:12px;padding:8px">No drawers retrieved.</div>';
  // Triples list: subject — predicate → object, grouped to keep the panel
  // scannable when extraction returns dozens.
  const tripleRows = triples.length
    ? triples.map(t => {
        const sf = (t.source_file || '').split('/').pop().replace(/\.md$/, '');
        const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
        return `<div style="font-size:12px;padding:6px 8px;border-bottom:1px dotted var(--border-100)">
          <div style="display:flex;gap:6px;align-items:flex-start">
            <span style="color:var(--text-100);flex:1">${esc(t.subject)}</span>
            <span style="font-family:var(--font-mono);color:var(--accent-brand);font-size:11px">— ${esc(t.predicate)} →</span>
            <span style="color:var(--text-100);flex:1">${esc(t.object)}</span>
          </div>
          <div style="font-family:var(--font-mono);font-size:10px;color:var(--text-400);margin-top:2px">${esc(sf)}${conf ? ` · c=${conf}` : ''}</div>
        </div>`;
      }).join('')
    : '<div style="color:var(--text-400);font-size:12px;padding:8px">No relations extracted.</div>';
  // Tagline tells the user, in plain English, what the panel contains —
  // the header alone leaves users guessing what "drawers" and "relations"
  // mean in context.
  const tagline = `<div style="font-size:12px;color:var(--text-400);line-height:1.5;padding:10px 14px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;margin-bottom:12px">
    These are the document chunks (drawers) and extracted facts (relations) that the agent retrieved from this project's memory before composing its answer. Use them to verify which sources were consulted and what specific claims were drawn from them.
  </div>`;

  overlay.innerHTML = `<div class="modal-content" style="max-width:1180px;width:94vw;max-height:90vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">Memory & Relations used in this response</span>
      <span style="font-size:11px;color:var(--text-400)">${drawers.length} drawer${drawers.length===1?'':'s'} · ${triples.length} relation${triples.length===1?'':'s'}</span>
      <button class="modal-close" style="margin-left:auto" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="display:flex;flex-direction:column;gap:12px;overflow:auto;flex:1;padding:16px 20px">
      ${tagline}
      <div style="display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:16px;align-items:start">
        <div style="min-width:0;display:flex;flex-direction:column;gap:8px">
          <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em">Graph view</div>
          <div>${svg}</div>
          <div style="display:flex;gap:14px;font-size:11px;color:var(--text-400);align-items:center;flex-wrap:wrap;padding:4px 0 0">
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;height:10px;border-radius:5px;background:#d9770624;border:1.5px solid #d97706"></span> Document</span>
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;height:10px;border-radius:5px;background:#2563eb24;border:1.5px solid #2563eb"></span> Entity</span>
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;border-top:1.5px solid var(--accent-brand)"></span> Relation</span>
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;border-top:1.5px dashed var(--accent-brand);opacity:0.6"></span> Source link</span>
          </div>
        </div>
        <div style="min-width:0;display:flex;flex-direction:column;gap:14px">
          <div>
            <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Drawers (${drawers.length})</div>
            <div>${drawerCards}</div>
          </div>
          <div>
            <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Relations (${triples.length})</div>
            <div>${tripleRows}</div>
          </div>
        </div>
      </div>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

// Filter state for the right-panel artifact list. 'output' hides
// intermediate files (scripts, json data dumps) so the panel surfaces the
// actual deliverables for scheduled task runs; 'all' shows everything.
let _artifactListRoleFilter = 'output';

function setArtifactListRoleFilter(role) {
  _artifactListRoleFilter = role;
  showArtifactList();
}

function showArtifactList() {
  const chat = state.activeChat;
  if (!chat) return;
  const sessionId = chat.sessionId;
  const artifacts = state.artifacts[sessionId] || [];

  const container = document.getElementById('artifact-content');
  document.getElementById('artifact-title').textContent = 'Artifacts';
  document.getElementById('artifact-actions').style.display = 'none';
  document.getElementById('artifact-version-select').innerHTML = '';
  state.activeArtifactId = null;

  if (artifacts.length === 0) {
    container.innerHTML = '<div class="artifact-empty"><svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>No artifacts yet</div>';
    return;
  }

  // Count intermediates so we can show a hint even when filtered out.
  const intermediateCount = artifacts.filter(a => (a.role || 'output') === 'intermediate').length;
  const hasIntermediate = intermediateCount > 0;
  const filtered = _artifactListRoleFilter === 'output'
    ? artifacts.filter(a => (a.role || 'output') === 'output')
    : artifacts;

  let html = '';
  if (hasIntermediate) {
    const outActive = _artifactListRoleFilter === 'output';
    html += `<div style="display:flex;gap:4px;padding:8px 10px;border-bottom:1px solid var(--border-100)">
      <button onclick="setArtifactListRoleFilter('output')" style="padding:3px 10px;border-radius:5px;font-size:11px;background:${outActive?'var(--bg-300)':'transparent'};color:${outActive?'var(--text-000)':'var(--text-300)'};border:1px solid var(--border-100)">Outputs</button>
      <button onclick="setArtifactListRoleFilter('all')" style="padding:3px 10px;border-radius:5px;font-size:11px;background:${!outActive?'var(--bg-300)':'transparent'};color:${!outActive?'var(--text-000)':'var(--text-300)'};border:1px solid var(--border-100)">All (+${intermediateCount} working files)</button>
    </div>`;
  }
  html += '<div class="artifact-list">';
  for (const a of filtered) {
    const verCount = a.versions?.length || 0;
    const latestVer = verCount > 0 ? a.versions[verCount - 1] : null;
    const meta = latestVer ? (latestVer.action === 'created' ? 'Created' : 'Modified') : '';
    const isInter = (a.role || 'output') === 'intermediate';
    const roleBadge = isInter
      ? `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(120,120,120,0.15);color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-left:4px" title="Helper/working file produced during execution">working</span>`
      : '';
    html += `
      <div class="artifact-list-card" onclick="openArtifactPanel('${esc(a.id)}')">
        <span class="alc-icon">${artifactTypeIcon(a.type)}</span>
        <div class="alc-info">
          <div class="alc-name">${esc(a.name)}${roleBadge}</div>
          <div class="alc-meta">${esc(a.type)} ${meta ? '· ' + meta : ''}</div>
        </div>
        <span class="alc-versions">v${verCount}</span>
      </div>
    `;
  }
  html += '</div>';
  container.innerHTML = html;
}

async function copyArtifact() {
  const container = document.getElementById('artifact-content');
  const raw = container._rawContent;
  const encoding = container._rawEncoding;
  const type = container._rawType;
  if (!raw) { showToast('Nothing to copy', true); return; }
  // Iframes (HTML artifacts) can hold focus, breaking navigator.clipboard.
  try { window.focus(); } catch(_) {}
  try {
    if (encoding === 'base64' && type === 'image') {
      const imgExt = (container._rawName || '').split('.').pop().toLowerCase();
      const mime = imgExt === 'svg' ? 'image/svg+xml' : `image/${imgExt || 'png'}`;
      const bin = atob(raw);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      await navigator.clipboard.write([new ClipboardItem({ [mime]: new Blob([bytes], { type: mime }) })]);
      showToast('Image copied');
      return;
    }
    await navigator.clipboard.writeText(raw);
    showToast('Copied to clipboard');
  } catch (e) {
    // Fallback: hidden textarea + execCommand('copy') works even when
    // navigator.clipboard rejects due to focus stolen by an iframe.
    try {
      const ta = document.createElement('textarea');
      ta.value = raw;
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      if (ok) { showToast('Copied to clipboard'); return; }
      throw new Error('execCommand returned false');
    } catch (e2) {
      console.error('[artifact] copy failed', e, e2);
      showToast(`Copy failed: ${e.message || e}`, true);
    }
  }
}

async function downloadArtifact() {
  const id = state.activeArtifactId;
  const ver = state.activeArtifactVersion;
  if (!id) return;
  const url = API.getArtifactDownloadUrl(id, ver);
  try {
    const r = await fetch(url, { headers: API._headers() });
    if (!r.ok) {
      const msg = r.status === 401 ? 'Not authorized' : `Download failed (${r.status})`;
      showToast(msg, true);
      return;
    }
    const blob = await r.blob();
    const disp = r.headers.get('Content-Disposition') || '';
    const m = /filename="([^"]+)"/.exec(disp);
    const filename = m ? m[1] : 'artifact';
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
  } catch (e) {
    console.error('[artifact] download failed', e);
    showToast(`Download failed: ${e.message || e}`, true);
  }
}

function toggleArtifactSource() {
  state.artifactSourceMode = !state.artifactSourceMode;
  const btn = document.getElementById('artifact-source-btn');
  if (btn) btn.classList.toggle('active', state.artifactSourceMode);

  const container = document.getElementById('artifact-content');
  if (container._rawContent) {
    renderArtifactContent(container._rawContent, container._rawType, container._rawName, container._rawEncoding);
  }
}

function updateArtifactRegistry(sessionId, eventData) {
  if (!state.artifacts[sessionId]) state.artifacts[sessionId] = [];
  const artifacts = state.artifacts[sessionId];
  const existing = artifacts.find(a => a.id === eventData.artifact_id);
  if (existing) {
    // Add new version
    if (!existing.versions) existing.versions = [];
    existing.versions.push({
      version: eventData.artifact_version,
      size: eventData.size,
      action: eventData.action,
      created_at: Date.now() / 1000,
    });
  } else {
    // New artifact
    artifacts.push({
      id: eventData.artifact_id,
      name: eventData.name,
      path: eventData.path,
      type: eventData.artifact_type,
      versions: [{
        version: eventData.artifact_version,
        size: eventData.size,
        action: eventData.action,
        created_at: Date.now() / 1000,
      }],
    });
  }
}

/* ═══════════════════════════════════════════════════════════
   ARTIFACTS BROWSE VIEW
   ═══════════════════════════════════════════════════════════ */

let _browseArtifactsCache = [];
let _browseArtifactsFilter = 'all';
let _browseArtifactsAgent = null;
let _browseArtifactsSource = 'all';  // 'all' | 'chat' | 'scheduled'
// Default hides intermediate files (helper scripts / json data dumps) so the
// grid surfaces deliverables. Flip to 'all' to inspect the raw working set.
let _browseArtifactsRole = 'output';

async function loadArtifactsBrowse() {
  const grid = document.getElementById('artifacts-grid');
  grid.innerHTML = '<div class="artifacts-empty">Loading...</div>';

  try {
    const resp = await API.browseArtifacts(_browseArtifactsAgent);
    _browseArtifactsCache = resp.artifacts || [];
  } catch (e) {
    grid.innerHTML = '<div class="artifacts-empty">Failed to load artifacts</div>';
    return;
  }

  // Build agent filter chips
  const agents = [...new Set(_browseArtifactsCache.map(a => a.agent_id))];
  const filterEl = document.getElementById('artifacts-agent-filter');
  if (agents.length > 1) {
    let chips = `<button class="artifacts-agent-chip${!_browseArtifactsAgent ? ' active' : ''}" onclick="setArtifactsBrowseAgent(null)">All agents</button>`;
    for (const ag of agents) {
      chips += `<button class="artifacts-agent-chip${_browseArtifactsAgent === ag ? ' active' : ''}" onclick="setArtifactsBrowseAgent('${esc(ag)}')">${esc(ag)}</button>`;
    }
    filterEl.innerHTML = chips;
    filterEl.style.display = '';
  } else {
    filterEl.style.display = 'none';
  }

  renderArtifactsBrowse();
}

function renderArtifactsBrowse() {
  const grid = document.getElementById('artifacts-grid');
  let filtered = _browseArtifactsCache;

  if (_browseArtifactsSource !== 'all') {
    filtered = filtered.filter(a => (a.source || 'chat') === _browseArtifactsSource);
  }
  if (_browseArtifactsFilter !== 'all') {
    filtered = filtered.filter(a => a.type === _browseArtifactsFilter);
  }
  if (_browseArtifactsRole === 'output') {
    filtered = filtered.filter(a => (a.role || 'output') === 'output');
  }

  if (filtered.length === 0) {
    const roleHint = _browseArtifactsRole === 'output'
      ? '<div style="margin-top:8px;font-size:12px;color:var(--text-400)">Try <a href="#" onclick="event.preventDefault();filterArtifactsRole(\'all\')" style="color:var(--accent-brand)">Show working files</a> to include helper scripts and working data.</div>'
      : '';
    grid.innerHTML = `<div class="artifacts-empty" style="grid-column:1/-1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      No artifacts${_browseArtifactsFilter !== 'all' ? ' of this type' : ''}${_browseArtifactsSource !== 'all' ? ' in ' + _browseArtifactsSource : ''} yet
      ${roleHint}
    </div>`;
    return;
  }

  let html = '';
  for (const a of filtered) {
    const preview = a.preview ? esc(a.preview) : '';
    const binaryTypes = ['image', 'document'];
    const hasPreview = preview && !binaryTypes.includes(a.type);

    // Time ago
    const ts = a.latest_created_at || a.created_at;
    const ago = ts ? timeAgo(ts) : '';

    // Source badge — scheduled-task artifacts carry a pill with the task name
    // so you can tell at a glance which run produced the file.
    const isScheduled = a.source === 'scheduled';
    const schedRun = a.schedule_run;
    const sourceBadge = isScheduled
      ? `<span class="abm-source" style="background:rgba(245,158,11,0.12);color:#d97706;padding:1px 6px;border-radius:4px;font-size:10px;text-transform:uppercase;letter-spacing:0.04em" title="${esc(schedRun ? schedRun.schedule_name + ' · run #' + schedRun.run_id : 'Scheduled task')}">sched${schedRun ? ' · ' + esc((schedRun.schedule_name || '').slice(0, 18)) : ''}</span>`
      : '';
    const isInter = (a.role || 'output') === 'intermediate';
    const roleBadge = isInter
      ? `<span style="background:rgba(120,120,120,0.15);color:var(--text-400);padding:1px 5px;border-radius:3px;font-size:10px;text-transform:uppercase;letter-spacing:0.04em" title="Helper or working file used during execution">working</span>`
      : '';

    html += `
      <div class="artifact-browse-card" data-art-id="${esc(a.id)}" data-art-agent="${esc(a.agent_id)}" ${isInter ? 'style="opacity:0.78"' : ''}>
        <div class="artifact-browse-fav-slot" onclick="event.stopPropagation()" data-art-fav-id="${esc(a.id)}" data-art-fav-agent="${esc(a.agent_id)}"></div>
        <div class="artifact-browse-preview${hasPreview ? '' : ' no-preview'}" onclick="openArtifactFromBrowse('${esc(a.id)}', '${esc(a.session_id)}', '${esc(a.agent_id)}')">
          ${hasPreview ? preview : artifactTypeIcon(a.type)}
        </div>
        <div class="artifact-browse-info" onclick="openArtifactFromBrowse('${esc(a.id)}', '${esc(a.session_id)}', '${esc(a.agent_id)}')">
          <div class="artifact-browse-name">${esc(a.name)}</div>
          <div class="artifact-browse-meta">
            <span class="abm-type">${esc(a.type)}</span>
            <span>v${a.latest_version || 1}</span>
            ${ago ? `<span>· ${ago}</span>` : ''}
            ${roleBadge}
            ${sourceBadge}
          </div>
        </div>
      </div>
    `;
  }
  grid.innerHTML = html;
  if (window.Favourites?.mount) {
    grid.querySelectorAll('.artifact-browse-fav-slot').forEach(slot => {
      const id = slot.dataset.artFavId;
      const agent = slot.dataset.artFavAgent || 'main';
      if (!id) return;
      window.Favourites.mount(slot, {
        item_type: 'artifact',
        item_id: id,
        agent_id: agent,
      });
    });
  }
}

function filterArtifactsBrowse(type) {
  _browseArtifactsFilter = type;
  // Update tab active states
  document.querySelectorAll('#artifacts-tabs .artifacts-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  renderArtifactsBrowse();
}

function filterArtifactsSource(source) {
  _browseArtifactsSource = source;
  document.querySelectorAll('#artifacts-source-tabs .artifacts-source-tab').forEach(t => {
    const isActive = t.getAttribute('data-source') === source;
    t.classList.toggle('active', isActive);
    t.style.color = isActive ? 'var(--text-000)' : 'var(--text-300)';
    t.style.borderBottomColor = isActive ? 'var(--text-000)' : 'transparent';
    t.style.fontWeight = isActive ? '500' : 'normal';
  });
  renderArtifactsBrowse();
}

function filterArtifactsRole(role) {
  _browseArtifactsRole = role;
  document.querySelectorAll('#artifacts-role-tabs .artifacts-role-tab').forEach(t => {
    const isActive = t.getAttribute('data-role') === role;
    t.classList.toggle('active', isActive);
    t.style.background = isActive ? 'var(--bg-300)' : 'transparent';
    t.style.color = isActive ? 'var(--text-000)' : 'var(--text-300)';
  });
  renderArtifactsBrowse();
}

function setArtifactsBrowseAgent(agentId) {
  _browseArtifactsAgent = agentId;
  loadArtifactsBrowse();
}

async function openArtifactFromBrowse(artifactId, sessionId, agentId) {
  // Scheduled-task artifacts have a synthetic session_id `sched-<run_id>`
  // that has no row in `sessions`. The normal openSession path would 404.
  // Route those to a read-only timeline view built from schedule_history
  // + traces (the same data the Run detail modal uses).
  if (sessionId && sessionId.startsWith('sched-')) {
    const runId = parseInt(sessionId.split('-', 2)[1]);
    if (Number.isFinite(runId)) {
      await openScheduledArtifact(runId, sessionId, agentId, artifactId);
      return;
    }
  }

  try {
    const resp = await API.getArtifacts(sessionId);
    state.artifacts[sessionId] = resp.artifacts || [];
  } catch (e) {
    state.artifacts[sessionId] = [];
  }

  await openSession(sessionId, agentId);
  setTimeout(() => openArtifactPanel(artifactId), 300);
}

// Open a scheduled-task artifact: synthesize a read-only "chat" from the
// run_detail response (trace spans + task prompt + result text) and show
// the artifact in the side panel.
async function openScheduledArtifact(runId, sessionId, agentId, artifactId) {
  let detail;
  try {
    detail = await API.manageSchedule({ action: 'run_detail', run_id: runId });
    if (detail.error) { showToast(detail.error, true); return; }
  } catch (e) { showToast('Failed to load run: ' + e.message, true); return; }

  // Artifacts list for this session (so openArtifactPanel's lookup works).
  try {
    const resp = await API.getArtifacts(sessionId);
    state.artifacts[sessionId] = resp.artifacts || [];
  } catch (e) {
    state.artifacts[sessionId] = [];
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
  chat._readonly = true;
  chat._scheduledRun = detail.run;  // stash for header badge
  state.activeScheduledRunId = runId;
  const runRow = detail.run || {};
  chat.model = runRow.model || chat.model;
  chat.chatTitle = `${runRow.schedule_name || 'Scheduled task'} · run #${runId}`;

  // Build pseudo-message stream: user task, thinking+tool_call/result per
  // round in trace order, then the final assistant result.
  const spans = detail.spans || [];
  const toolSpans = spans.filter(s => s.type === 'tool_call')
    .sort((a, b) => (a.started_at || '').localeCompare(b.started_at || ''));

  chat.messages.push({
    role: 'user',
    content: runRow.task || '',
    _ts: runRow.started_at,
  });

  for (const s of toolSpans) {
    let meta = {};
    try { meta = s.metadata ? JSON.parse(s.metadata) : {}; } catch (e) {}
    chat.messages.push({
      role: 'tool_call',
      name: s.name,
      args: {},  // args aren't stored in trace metadata; summary lives in tool_result below
      tool_round: null,
    });
    chat.messages.push({
      role: 'tool_result',
      name: s.name,
      result: meta.result_summary || '',
      tool_round: null,
      _status: s.status,
      _duration_ms: s.duration_ms,
    });
  }

  // Strip the "[Duration: Xs | Tools: N]\n\n" header that complete_execution
  // prepends to result so the final assistant bubble shows only the model's
  // actual closing message.
  let finalText = runRow.result || '';
  finalText = finalText.replace(/^\[Duration:[^\]]+\]\s*\n+/, '');
  if (finalText) {
    chat.messages.push({
      role: 'assistant',
      content: finalText,
      _ts: runRow.finished_at,
    });
  }

  // Navigate first (this clears any prior readonly UI via the hook), then
  // render + re-apply readonly for THIS run.
  navigateTo('chat');
  chat._readonly = true;
  chat._scheduledRun = detail.run;
  if (typeof renderMessages === 'function') renderMessages();
  _applyScheduledReadonlyUI(runRow);
  if (artifactId) {
    setTimeout(() => openArtifactPanel(artifactId), 200);
  } else {
    // No specific artifact requested — open the right panel and show the
    // session's artifact list so the user can pick an output or drill into
    // a technical file.
    setTimeout(() => {
      if (typeof openRightPanel === 'function') openRightPanel('artifacts');
    }, 200);
  }
}

function _applyScheduledReadonlyUI(runRow) {
  // Slap a header badge + disable the composer so the user can't try to
  // send a follow-up into a non-existent session. Stored on chat._readonly
  // so a later openSession clears it naturally.
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send-btn');
  if (input) { input.disabled = true; input.placeholder = 'Read-only — scheduled task log'; }
  if (sendBtn) sendBtn.disabled = true;

  // Banner above messages.
  let banner = document.getElementById('scheduled-readonly-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'scheduled-readonly-banner';
    banner.style.cssText = 'margin:0 20px 8px;padding:10px 14px;border:1px solid rgba(245,158,11,0.35);background:rgba(245,158,11,0.08);border-radius:8px;font-size:13px;color:var(--text-200);display:flex;align-items:center;gap:10px';
    const msgs = document.getElementById('messages') || document.querySelector('.messages-container');
    if (msgs && msgs.parentElement) msgs.parentElement.insertBefore(banner, msgs);
  }
  const status = runRow.status || '?';
  const statusColor = (status === 'success' || status === 'completed') ? '#10b981'
    : (status === 'timeout' ? '#f59e0b' : '#ef4444');
  const running = status === 'running';
  // Layout flips: the banner becomes a two-row block so we can tuck the
  // task prompt into a <details> without squeezing the header line.
  banner.style.cssText = 'margin:0 20px 8px;padding:10px 14px;border:1px solid rgba(245,158,11,0.35);background:rgba(245,158,11,0.08);border-radius:8px;font-size:13px;color:var(--text-200);display:flex;flex-direction:column;gap:8px';
  const taskText = runRow.task || '';
  banner.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px">
      <span style="width:8px;height:8px;border-radius:50%;background:${statusColor};flex-shrink:0"></span>
      <div style="flex:1;min-width:0">
        <div style="color:var(--text-100);font-weight:500">Scheduled task · ${esc(runRow.schedule_name || '')} · run #${runRow.id}</div>
        <div style="color:var(--text-400);font-size:11px;margin-top:2px">
          ${runRow.started_at ? esc(new Date(runRow.started_at+'Z').toLocaleString()) : ''}
          · <span style="color:${statusColor}">${esc(status)}</span>
          ${runRow.duration_ms != null ? ` · ${(runRow.duration_ms/1000).toFixed(1)}s` : ''}
          · ${runRow.tool_calls || 0} tool calls
          ${runRow.model ? ' · ' + esc(runRow.model) : ''}
        </div>
      </div>
      <button onclick="_schedViewRunDetail(${runRow.id})" style="padding:4px 10px;border-radius:6px;background:var(--bg-200);color:var(--text-200);font-size:12px;border:1px solid var(--border-100);cursor:pointer">Details</button>
      <button ${running ? 'disabled title="Cannot delete a running task"' : ''} onclick="_schedDeleteRunFromBanner(${runRow.id})" style="padding:4px 10px;border-radius:6px;background:transparent;color:var(--text-300);font-size:12px;border:1px solid var(--border-100);cursor:${running?'not-allowed':'pointer'}">Delete run</button>
    </div>
    ${taskText ? `<details style="margin-left:18px">
      <summary style="cursor:pointer;font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;list-style:none">Task prompt</summary>
      <div style="margin-top:6px;padding:8px;background:var(--bg-100);border-radius:6px;font-size:12px;color:var(--text-200);white-space:pre-wrap;max-height:160px;overflow:auto">${esc(taskText)}</div>
    </details>` : ''}
  `;
}

async function _schedDeleteRunFromBanner(runId) {
  if (!await showConfirmDanger(`Delete run #${runId}?\n\nThis removes the history row and every artifact produced by this run (files included).`, 'Delete Run', 'Delete')) return;
  try {
    const res = await API.manageSchedule({ action: 'delete_run', run_id: runId });
    if (res && res.error) { showToast(res.error, true); return; }
    showToast(`Run deleted · ${res.artifacts_removed||0} artifact(s) purged`);
    // Leave the read-only view — no session to return to.
    navigateTo('scheduled');
  } catch(e) {
    showToast('Failed: ' + e.message, true);
  }
}

// Hook navigateTo so leaving 'chat' view for a scheduled run clears the
// readonly banner/composer when the user goes elsewhere.
(function() {
  const _origNav = window.navigateTo;
  if (typeof _origNav !== 'function') return;
  window.navigateTo = function(view) {
    const banner = document.getElementById('scheduled-readonly-banner');
    if (banner) banner.remove();
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('send-btn');
    if (input && input.disabled && state.activeChat?._readonly) {
      input.disabled = false;
      input.placeholder = '';
    }
    if (sendBtn && sendBtn.disabled && state.activeChat?._readonly) {
      sendBtn.disabled = false;
    }
    if (state.activeChat) {
      state.activeChat._readonly = false;
      state.activeChat._scheduledRun = null;
    }
    // Drop the active-run marker when leaving the read-only run viewer to a
    // non-scheduled context. openScheduledArtifact resets it AFTER navigateTo
    // returns, so chat-view re-entries from a fresh run-click stay correct.
    if (view !== 'scheduled' && view !== 'chat') {
      state.activeScheduledRunId = null;
    }
    return _origNav.apply(this, arguments);
  };
})();

function timeAgo(timestamp) {
  const now = Date.now() / 1000;
  const diff = now - timestamp;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 2592000) return `${Math.floor(diff / 86400)}d ago`;
  return `${Math.floor(diff / 2592000)}mo ago`;
}

// ─── Sync History Modal ──────────────────────────────────────────────────────

let _syncHistoryPollHandles = {};

async function projectSyncHistory() {
  const agentId = state._projectDetailAgent;
  const projectName = state._projectDetailName;
  if (!agentId || !projectName) return;
  const modal = document.getElementById('sync-history-modal');
  const nameEl = document.getElementById('sync-history-project-name');
  if (!modal) return;
  if (nameEl) nameEl.textContent = projectName;
  modal.style.display = 'flex';
  await _loadSyncRuns(agentId, projectName);
}

function closeSyncHistoryModal() {
  const modal = document.getElementById('sync-history-modal');
  if (modal) modal.style.display = 'none';
  Object.values(_syncHistoryPollHandles).forEach(clearInterval);
  _syncHistoryPollHandles = {};
}

async function _loadSyncRuns(agentId, projectName) {
  const loadingEl = document.getElementById('sync-history-loading');
  const listEl = document.getElementById('sync-history-list');
  if (loadingEl) loadingEl.style.display = '';
  if (listEl) listEl.innerHTML = '';
  try {
    const data = await API.get(
      `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs?limit=20`
    );
    if (loadingEl) loadingEl.style.display = 'none';
    _renderSyncRuns(agentId, projectName, data.runs || []);
  } catch(e) {
    if (loadingEl) loadingEl.textContent = 'Failed to load history.';
  }
}

function _renderSyncRuns(agentId, projectName, runs) {
  const listEl = document.getElementById('sync-history-list');
  if (!listEl) return;
  if (!runs.length) {
    listEl.innerHTML = '<p style="color:var(--text-400);font-size:.85rem">No sync runs recorded yet.</p>';
    return;
  }
  // Pair purge runs with the full_resync run that follows immediately after.
  // purge runs (triggered_by='full_resync_purge') are absorbed into the next
  // full_resync row so the user sees one logical "Full Resync" entry, not two.
  const paired = [];
  for (let i = 0; i < runs.length; i++) {
    const r = runs[i];
    if (r.triggered_by === 'full_resync_purge') {
      // Look behind for the full_resync that was queued right after this purge
      const prev = paired.length ? paired[paired.length - 1] : null;
      if (prev && prev._type === 'full_resync_pair') {
        prev._purgeRun = r;
      } else {
        paired.push({ _type: 'full_resync_pair', _purgeRun: r, _resyncRun: null });
      }
    } else if (r.triggered_by === 'full_resync') {
      const prev = paired.length ? paired[paired.length - 1] : null;
      if (prev && prev._type === 'full_resync_pair' && !prev._resyncRun) {
        prev._resyncRun = r;
      } else {
        paired.push({ _type: 'full_resync_pair', _purgeRun: null, _resyncRun: r });
      }
    } else {
      paired.push({ _type: 'single', _run: r });
    }
  }

  listEl.innerHTML = paired.map(entry => {
    if (entry._type === 'single') return _syncRunRowHtml(entry._run);
    return _syncRunPairHtml(entry._purgeRun, entry._resyncRun);
  }).join('');

  // Wire expand toggles — lazy-fetch full log on first expand
  listEl.querySelectorAll('.sh-run-header').forEach(hdr => {
    hdr.addEventListener('click', async () => {
      const row = hdr.closest('.sh-run');
      const wasExpanded = row.classList.contains('sh-expanded');
      row.classList.toggle('sh-expanded');
      if (!wasExpanded && !row.dataset.logLoaded) {
        row.dataset.logLoaded = '1';
        await _loadRunDetail(agentId, projectName, row);
      }
    });
  });
  // Wire cancel buttons
  listEl.querySelectorAll('.sh-cancel-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      btn.textContent = 'Cancelling…';
      try {
        await API.post(
          `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-cancel`, {}
        );
      } catch(_) {}
    });
  });
  // Start polling for any running rows
  runs.forEach(r => {
    if (r.state === 'running') _startSyncRunPoll(agentId, projectName, r.id);
  });
}

// Triggered-by → human label + colour hint
function _triggerLabel(tb) {
  return {
    scheduled: { text: 'Scheduled', cls: '' },
    manual:    { text: 'Manual',    cls: '' },
    full_resync: { text: 'Full Resync', cls: 'accent' },
    full_resync_purge: { text: 'Full Resync', cls: 'accent' },
  }[tb] || { text: tb, cls: '' };
}

function _syncRunPairHtml(purgeRun, resyncRun) {
  // Use the resync run as the primary for state/timing; fall back to purge run.
  const primary = resyncRun || purgeRun;
  const stateColors = {
    running: 'var(--accent-blue)', idle: 'var(--success)',
    error: 'var(--error)', cancelled: 'var(--text-400)',
  };
  const color = stateColors[primary.state] || 'var(--text-400)';
  const isPulse = primary.state === 'running';
  const startedAgo = primary.started_at ? timeAgo(primary.started_at) : '?';
  const totalElapsed = (primary.finished_at && primary.started_at)
    ? _fmtElapsed(primary.finished_at - primary.started_at) : (primary.state === 'running' ? '…' : '');

  const rSum = resyncRun
    ? (typeof resyncRun.summary === 'string' ? JSON.parse(resyncRun.summary) : resyncRun.summary) || {}
    : {};
  const pSum = purgeRun
    ? (typeof purgeRun.summary === 'string' ? JSON.parse(purgeRun.summary) : purgeRun.summary) || {}
    : {};

  const statParts = [
    rSum.total_files != null    ? `${rSum.total_files} files` : null,
    rSum.total_indexed != null  ? `${rSum.total_indexed} drawers` : null,
    rSum.total_triples != null  ? `${rSum.total_triples} triples` : null,
  ].filter(Boolean).join(' · ');

  const errStr = [...(rSum.errors || []), ...(pSum.errors || [])].filter(Boolean).join(', ');

  const runId = primary.id;
  const purgeAttr = purgeRun ? ` data-purge-run-id="${purgeRun.id}"` : '';
  return `<div class="sh-run" data-run-id="${runId}"${purgeAttr}>
    <div class="sh-run-header" style="display:flex;align-items:center;gap:10px;padding:9px 0;cursor:pointer;border-bottom:1px solid var(--border-200)">
      <span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0${isPulse ? ';animation:pulse 1.4s ease-in-out infinite' : ''}"></span>
      <span style="font-size:.82rem;color:var(--text-400);min-width:70px">${startedAgo}</span>
      <span style="font-size:.82rem;font-weight:500;flex:1">${statParts || (primary.state === 'running' ? 'Running…' : '—')}</span>
      ${errStr ? `<span style="font-size:.75rem;color:var(--error);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${errStr}">⚠ ${errStr}</span>` : ''}
      <span style="font-size:.75rem;padding:2px 7px;border-radius:4px;background:color-mix(in srgb,var(--accent-blue) 12%,transparent);color:var(--accent-blue)">Full Resync</span>
      ${totalElapsed ? `<span style="font-size:.78rem;color:var(--text-400)">${totalElapsed}</span>` : ''}
      ${primary.state === 'running' ? `<button class="sh-cancel-btn" style="font-size:.75rem;padding:2px 8px;background:color-mix(in srgb,var(--error) 12%,transparent);border:1px solid color-mix(in srgb,var(--error) 30%,transparent);border-radius:4px;color:var(--error);cursor:pointer">Cancel</button>` : ''}
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" class="sh-chevron" style="transition:transform .2s;flex-shrink:0"><polyline points="6 9 12 15 18 9"/></svg>
    </div>
    <div class="sh-run-detail" style="display:none;padding:10px 0 4px">
      ${_syncRunPairDetailHtml(purgeRun, resyncRun)}
    </div>
  </div>`;
}

function _syncRunPairDetailHtml(purgeRun, resyncRun) {
  let html = '';

  // 1. Purge phase
  if (purgeRun) {
    const pLog = typeof purgeRun.log === 'string' ? JSON.parse(purgeRun.log) : (purgeRun.log || {});
    const pSum = typeof purgeRun.summary === 'string' ? JSON.parse(purgeRun.summary) : (purgeRun.summary || {});
    const purgeActions = pLog.purge_actions || [];
    const purgeElapsed = (purgeRun.finished_at && purgeRun.started_at)
      ? _fmtElapsed(purgeRun.finished_at - purgeRun.started_at) : '';

    html += `<div style="font-size:.78rem;font-weight:600;margin-bottom:6px;color:var(--text-400);display:flex;align-items:center;gap:8px">
      <span>Purge</span>${purgeElapsed ? `<span style="font-weight:400">${purgeElapsed}</span>` : ''}
    </div>`;

    const P_LBL  = 'width:160px;min-width:160px;padding:2px 10px 2px 0;color:var(--text-400);white-space:nowrap';
    const P_DET  = 'padding:2px 0';
    const P_TIME = 'width:44px;min-width:44px;padding:2px 0 2px 10px;text-align:right;color:var(--text-400);white-space:nowrap';
    if (purgeActions.length) {
      const actionLabels = {
        drawers_purged: 'Drawers wiped',
        kg_triples_purged: 'KG triples wiped',
        closet_cursor_cleared: 'Closet cursor cleared',
        doc_convert_cache_cleared: 'Doc-convert cache cleared',
      };
      html += `<table style="font-size:.78rem;border-collapse:collapse;width:100%;table-layout:fixed;margin-bottom:12px">`;
      for (const a of purgeActions) {
        const label = actionLabels[a.action] || a.action;
        let detail = '';
        if (a.action === 'drawers_purged') detail = `${a.deleted ?? 0} deleted`;
        else if (a.action === 'kg_triples_purged') detail = `${a.triples_deleted ?? 0} triples, ${a.progress_cursors_deleted ?? 0} cursors`;
        else if (a.action === 'doc_convert_cache_cleared') detail = `${a.dirs_removed ?? 0} dirs, ${a.files_removed ?? 0} files`;
        else detail = '✓';
        const errTxt = a.error ? ` <span style="color:var(--error)">⚠ ${a.error}</span>` : '';
        const elTxt = a.elapsed_s > 0 ? _fmtElapsed(a.elapsed_s) : '';
        html += `<tr>
          <td style="${P_LBL}">${label}</td>
          <td style="${P_DET}">${detail}${errTxt}</td>
          <td style="${P_TIME}">${elTxt}</td>
        </tr>`;
      }
      html += `</table>`;
    } else {
      // Fallback to summary fields if no purge_actions recorded
      const rows = [
        pSum.drawers_deleted != null ? ['Drawers wiped', `${pSum.drawers_deleted}`] : null,
        pSum.triples_deleted != null ? ['KG triples wiped', `${pSum.triples_deleted}`] : null,
        pSum.kg_progress_deleted != null ? ['KG cursors cleared', `${pSum.kg_progress_deleted}`] : null,
        pSum.brain_extracted_cleared != null ? ['Doc-convert dirs cleared', `${pSum.brain_extracted_cleared}`] : null,
      ].filter(Boolean);
      if (rows.length) {
        html += `<table style="font-size:.78rem;border-collapse:collapse;width:100%;table-layout:fixed;margin-bottom:12px">
          ${rows.map(([k,v]) => `<tr><td style="${P_LBL}">${k}</td><td style="${P_DET}">${v}</td><td style="${P_TIME}"></td></tr>`).join('')}
        </table>`;
      }
    }
  }

  // 2. Re-index phase
  if (resyncRun) {
    const rElapsed = (resyncRun.finished_at && resyncRun.started_at)
      ? _fmtElapsed(resyncRun.finished_at - resyncRun.started_at) : (resyncRun.state === 'running' ? '…' : '');
    html += `<div style="font-size:.78rem;font-weight:600;margin-bottom:6px;color:var(--text-400);display:flex;align-items:center;gap:8px">
      <span>Re-index</span>${rElapsed ? `<span style="font-weight:400">${rElapsed}</span>` : ''}
    </div>`;
    html += _syncRunDetailHtml(resyncRun, { hideTitle: true });
  }

  return html || '<span style="color:var(--text-400);font-size:.78rem">No detail recorded.</span>';
}

function _syncRunRowHtml(run) {
  const stateColors = {
    running: 'var(--accent-blue)', idle: 'var(--success)',
    error: 'var(--error)', cancelled: 'var(--text-400)',
  };
  const color = stateColors[run.state] || 'var(--text-400)';
  const isPulse = run.state === 'running';
  const startedAgo = run.started_at ? timeAgo(run.started_at) : '?';
  const elapsed = (run.finished_at && run.started_at)
    ? _fmtElapsed(run.finished_at - run.started_at) : (run.state === 'running' ? '…' : '');
  const summary = run.summary ? (typeof run.summary === 'string' ? JSON.parse(run.summary) : run.summary) : {};

  // Show current totals, not the per-cycle delta
  const statParts = [
    summary.total_files != null   ? `${summary.total_files} files` : null,
    summary.total_indexed != null ? `${summary.total_indexed} drawers` : null,
    summary.total_triples != null && summary.total_triples > 0 ? `${summary.total_triples} triples` : null,
  ].filter(Boolean).join(' · ');

  const errStr = (summary.errors || []).filter(Boolean).join(', ');
  const trig = _triggerLabel(run.triggered_by);
  const trigHtml = `<span style="font-size:.75rem;padding:2px 6px;border-radius:4px;background:var(--bg-200);color:var(--text-400)">${trig.text}</span>`;

  return `<div class="sh-run" data-run-id="${run.id}">
    <div class="sh-run-header" style="display:flex;align-items:center;gap:10px;padding:9px 0;cursor:pointer;border-bottom:1px solid var(--border-200)">
      <span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0${isPulse ? ';animation:pulse 1.4s ease-in-out infinite' : ''}"></span>
      <span style="font-size:.82rem;color:var(--text-400);min-width:70px">${startedAgo}</span>
      <span style="font-size:.82rem;font-weight:500;flex:1">${statParts || (run.state === 'running' ? 'Running…' : '—')}</span>
      ${errStr ? `<span style="font-size:.75rem;color:var(--error);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${errStr}">⚠ ${errStr}</span>` : ''}
      ${trigHtml}
      ${elapsed ? `<span style="font-size:.78rem;color:var(--text-400)">${elapsed}</span>` : ''}
      ${run.state === 'running' ? `<button class="sh-cancel-btn" style="font-size:.75rem;padding:2px 8px;background:color-mix(in srgb,var(--error) 12%,transparent);border:1px solid color-mix(in srgb,var(--error) 30%,transparent);border-radius:4px;color:var(--error);cursor:pointer">Cancel</button>` : ''}
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" class="sh-chevron" style="transition:transform .2s;flex-shrink:0"><polyline points="6 9 12 15 18 9"/></svg>
    </div>
    <div class="sh-run-detail" style="display:none;padding:10px 0 4px">
      ${errStr ? `<div style="color:var(--error);font-size:.8rem;margin-bottom:8px">⚠ ${errStr}</div>` : ''}
      ${_syncRunDetailHtml(run)}
    </div>
  </div>`;
}

function _syncRunDetailHtml(run, opts = {}) {
  const log = run.log ? (typeof run.log === 'string' ? JSON.parse(run.log) : run.log) : {};
  const summary = run.summary ? (typeof run.summary === 'string' ? JSON.parse(run.summary) : run.summary) : {};
  const topSteps = log.steps || {};
  const folders = log.folders || [];

  let html = '';

  // Summary totals row (skip for purge runs — they have their own layout)
  if (!opts.hideTitle && run.triggered_by !== 'full_resync_purge') {
    const summaryParts = [
      summary.total_files != null   ? `${summary.total_files} files` : null,
      summary.total_indexed != null ? `${summary.total_indexed} drawers` : null,
      summary.total_triples > 0    ? `${summary.total_triples} triples` : null,
      summary.folders_seen > 0     ? `${summary.folders_seen} folders` : null,
    ].filter(Boolean).join('  ·  ');
    if (summaryParts) {
      html += `<div style="font-size:.78rem;color:var(--text-400);margin-bottom:10px">${summaryParts}</div>`;
    }
  }

  // Per-folder phases + project-wide steps — rendered in one shared <table>
  // so the three columns (label | detail | elapsed) align across all sections.
  const LABEL_W = 'width:110px;min-width:110px';
  const TIME_W  = 'width:44px;min-width:44px';
  const TD_LBL  = `padding:2px 10px 2px 0;color:var(--text-400);white-space:nowrap;${LABEL_W}`;
  const TD_DET  = 'padding:2px 0';
  const TD_TIME = `padding:2px 0 2px 10px;text-align:right;color:var(--text-400);white-space:nowrap;${TIME_W}`;

  let tableRows = '';

  folders.forEach(f => {
    const fsteps = f.steps || {};
    const convSt  = fsteps.doc_convert || {};
    const indexSt = fsteps.indexing    || {};
    const kgSt    = fsteps.kg         || {};
    const fname = (f.path || '').split('/').filter(Boolean).pop() || f.path;

    // Section header row spanning all columns
    tableRows += `<tr>
      <td colspan="3" style="padding:8px 0 3px;font-size:.78rem;font-weight:600">
        <span style="display:inline-flex;align-items:center;gap:5px">
          <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;opacity:.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <span title="${f.path}">${fname}</span>
        </span>
      </td>
    </tr>`;

    // Doc convert row
    if (convSt.started_at !== undefined || convSt.converted !== undefined) {
      const convErr = (convSt.errors || []).filter(Boolean).join(', ');
      const convElapsed = convSt.elapsed_s != null ? _fmtElapsed(convSt.elapsed_s)
        : (convSt.started_at && convSt.finished_at) ? _fmtElapsed(convSt.finished_at - convSt.started_at) : '';
      const convDetail = convErr
        ? `<span style="color:var(--error)">⚠ ${convErr}</span>`
        : [
            convSt.converted > 0   ? `${convSt.converted} converted` : null,
            convSt.unchanged > 0   ? `${convSt.unchanged} unchanged` : null,
            convSt.failed > 0      ? `<span style="color:var(--error)">${convSt.failed} failed</span>` : null,
            convSt.stale_removed > 0 ? `${convSt.stale_removed} stale removed` : null,
            convSt.seen_total === 0 ? 'nothing to convert' : null,
          ].filter(Boolean).join(', ') || '✓';
      tableRows += `<tr>
        <td style="${TD_LBL}">Doc convert</td>
        <td style="${TD_DET}">${convDetail}</td>
        <td style="${TD_TIME}">${convElapsed}</td>
      </tr>`;
    }

    // Indexing row
    if (indexSt.started_at !== undefined || indexSt.drawers_created !== undefined) {
      const idxErr = (indexSt.errors || []).filter(Boolean).join(', ');
      const idxElapsed = indexSt.elapsed_s != null ? _fmtElapsed(indexSt.elapsed_s)
        : (indexSt.started_at && indexSt.finished_at) ? _fmtElapsed(indexSt.finished_at - indexSt.started_at) : '';
      const idxDetail = idxErr
        ? `<span style="color:var(--error)">⚠ ${idxErr}</span>`
        : indexSt.drawers_created != null
          ? `${indexSt.drawers_created} drawers added`
          : '✓';
      tableRows += `<tr>
        <td style="${TD_LBL}">Indexing</td>
        <td style="${TD_DET}">${idxDetail}</td>
        <td style="${TD_TIME}">${idxElapsed}</td>
      </tr>`;
    }

    // KG row
    if (kgSt.triples_this_cycle !== undefined || kgSt.triples_total !== undefined) {
      const kgErr = kgSt.error || '';
      const kgParseErrs = kgSt.parse_errors || 0;
      const kgElapsed = kgSt.elapsed_s != null ? _fmtElapsed(kgSt.elapsed_s) : '';
      const kgStats = [
        kgSt.triples_this_cycle != null ? `+${kgSt.triples_this_cycle} triples` : null,
        kgSt.triples_total != null      ? `(${kgSt.triples_total} total)` : null,
        kgSt.drawers_processed != null  ? `${kgSt.drawers_processed} drawers processed` : null,
      ].filter(Boolean).join(' ') || '✓';
      const kgWarn = kgParseErrs > 0
        ? ` <span style="color:var(--warning,#a06000)" title="${kgErr}">· ${kgParseErrs} parse err</span>`
        : (kgErr ? ` <span style="color:var(--error)" title="${esc(kgErr)}">⚠ ${kgErr}</span>` : '');
      const kgDetail = kgStats + kgWarn;
      tableRows += `<tr>
        <td style="${TD_LBL}">KG extraction</td>
        <td style="${TD_DET}">${kgDetail}</td>
        <td style="${TD_TIME}">${kgElapsed}</td>
      </tr>`;
    }
  });

  // Stale-path purge (top-level step, not per-folder)
  const staleSt = topSteps.stale_path_purge;
  if (staleSt && (staleSt.drawers_deleted > 0 || staleSt.closets_deleted > 0)) {
    tableRows += `<tr>
      <td style="${TD_LBL}">Stale purge</td>
      <td style="${TD_DET}">${staleSt.drawers_deleted || 0} drawers, ${staleSt.closets_deleted || 0} closets removed</td>
      <td style="${TD_TIME}"></td>
    </tr>`;
  }

  // Closet rerank (project-wide, top-level)
  const closetSt = topSteps.closet_rerank;
  if (closetSt) {
    const closetErr = (closetSt.errors || []).filter(Boolean).join(', ');
    const closetElapsed = closetSt.elapsed_s != null ? _fmtElapsed(closetSt.elapsed_s)
      : (closetSt.started_at && closetSt.finished_at) ? _fmtElapsed(closetSt.finished_at - closetSt.started_at) : '';
    const closetDetail = closetErr
      ? `<span style="color:var(--error)">⚠ ${closetErr}</span>`
      : closetSt.regen_triggered
        ? `rebuilt (${closetSt.sources_stale || 0}/${closetSt.sources_seen || 0} sources changed)`
        : `skipped — ${closetSt.sources_seen || 0} sources unchanged`;
    tableRows += `<tr>
      <td style="${TD_LBL}">Closet rerank</td>
      <td style="${TD_DET}">${closetDetail}</td>
      <td style="${TD_TIME}">${closetElapsed}</td>
    </tr>`;
  }

  if (tableRows) {
    html += `<table style="font-size:.77rem;border-collapse:collapse;width:100%;table-layout:fixed">${tableRows}</table>`;
  }

  return html || '<span style="color:var(--text-400);font-size:.78rem">No detail recorded.</span>';
}

function _fmtElapsed(secs) {
  if (secs < 1) return '<1s';
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

async function _loadRunDetail(agentId, projectName, rowEl) {
  // rowEl is a .sh-run element — it may be a single run or a paired full-resync.
  // For pairs, data-run-id is the primary (resync) run id; purge id is in data-purge-run-id.
  const detailEl = rowEl.querySelector('.sh-run-detail');
  if (!detailEl) return;
  detailEl.innerHTML = '<span style="color:var(--text-400);font-size:.78rem">Loading…</span>';

  try {
    const primaryId = rowEl.dataset.runId;
    const purgeId   = rowEl.dataset.purgeRunId;

    const [primaryData, purgeData] = await Promise.all([
      primaryId ? API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${primaryId}`) : null,
      purgeId   ? API.get(`/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${purgeId}`)   : null,
    ]);

    const primaryRun = primaryData?.run;
    const purgeRun   = purgeData?.run;

    if (!primaryRun) { detailEl.innerHTML = '<span style="color:var(--text-400);font-size:.78rem">No detail recorded.</span>'; return; }

    let html = '';
    if (purgeRun) {
      // Paired full-resync: render purge + re-index sections
      html = _syncRunPairDetailHtml(purgeRun, primaryRun);
    } else {
      const errStr = ((primaryRun.summary?.errors || []).filter(Boolean)).join(', ');
      if (errStr) html += `<div style="color:var(--error);font-size:.8rem;margin-bottom:8px">⚠ ${errStr}</div>`;
      html += _syncRunDetailHtml(primaryRun);
    }
    detailEl.innerHTML = html;
  } catch(e) {
    detailEl.innerHTML = `<span style="color:var(--error);font-size:.78rem">Failed to load detail.</span>`;
  }
}

function _startSyncRunPoll(agentId, projectName, runId) {
  if (_syncHistoryPollHandles[runId]) return;
  _syncHistoryPollHandles[runId] = setInterval(async () => {
    try {
      const data = await API.get(
        `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-runs/${runId}`
      );
      const run = data.run;
      if (!run) return;
      const rowEl = document.querySelector(`[data-run-id="${runId}"]`);
      if (!rowEl) return;
      rowEl.outerHTML = _syncRunRowHtml(run);
      // Re-wire events for the new element
      const newRow = document.querySelector(`[data-run-id="${runId}"]`);
      if (newRow) {
        newRow.querySelector('.sh-run-header')?.addEventListener('click', () => {
          newRow.classList.toggle('sh-expanded');
        });
        newRow.querySelector('.sh-cancel-btn')?.addEventListener('click', async (e) => {
          e.stopPropagation();
          const btn = e.currentTarget;
          btn.disabled = true; btn.textContent = 'Cancelling…';
          try {
            await API.post(
              `/v1/agents/${agentId}/projects/${encodeURIComponent(projectName)}/sync-cancel`, {}
            );
          } catch(_) {}
        });
      }
      if (run.state !== 'running') {
        clearInterval(_syncHistoryPollHandles[runId]);
        delete _syncHistoryPollHandles[runId];
      }
    } catch(_) {}
  }, 3000);
}

// Expand/collapse sh-run-detail on sh-expanded toggle (CSS-driven)
document.addEventListener('click', (e) => {
  const hdr = e.target.closest?.('.sh-run-header');
  if (!hdr) return;
  const run = hdr.closest('.sh-run');
  if (!run) return;
  const detail = run.querySelector('.sh-run-detail');
  const chevron = hdr.querySelector('.sh-chevron');
  if (detail) detail.style.display = run.classList.contains('sh-expanded') ? '' : 'none';
  if (chevron) chevron.style.transform = run.classList.contains('sh-expanded') ? 'rotate(180deg)' : '';
});

