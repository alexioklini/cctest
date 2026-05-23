/* ═══════════════════════════════════════════════════════════
   THEME
   ═══════════════════════════════════════════════════════════ */
function setTheme(mode) {
  document.documentElement.setAttribute('data-mode', mode);
  localStorage.setItem('theme', mode);

  // Toggle hljs themes
  document.getElementById('hljs-theme-light').disabled = (mode === 'dark');
  document.getElementById('hljs-theme-dark').disabled = (mode === 'light');
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-mode') || 'light';
  setTheme(current === 'light' ? 'dark' : 'light');
}

/* ═══════════════════════════════════════════════════════════
   THINKING LEVEL
   ═══════════════════════════════════════════════════════════ */
function getActiveThinkingFormat() {
  const mid = state.activeChat?.model || '';
  if (!mid) return 'none';
  const cfg = state.modelsConfig?.models?.[mid];
  return (cfg && cfg.thinking_format) || 'none';
}

// Levels the composer cycles through for a given thinking_format. Mirrors
// the schedule + Models-tab logic so the UI never offers a setting the
// provider would silently coerce. Returns ['none'] when format='none' so
// callers can short-circuit.
function _composerLevelsForFormat(fmt) {
  const info = _thinkingOptionsForFormat(fmt);
  if (!info) return ['none'];  // unsupported
  // info.options is [{value,label}, ...] — extract values, ensure 'none' is first.
  const vals = info.options.map(o => o.value);
  if (!vals.includes('none')) vals.unshift('none');
  return vals;
}

function cycleThinkingLevel() {
  const fmt = getActiveThinkingFormat();
  if (fmt === 'none') {
    showToast("Selected model doesn't support thinking", true);
    return;
  }
  const levels = _composerLevelsForFormat(fmt);
  // If the saved level isn't in this format's set, jump to the first non-off
  // level. Otherwise advance one step.
  const cur = state.thinkingLevel || 'none';
  let next;
  const idx = levels.indexOf(cur);
  if (idx < 0) {
    next = levels.length > 1 ? levels[1] : 'none';
  } else {
    next = levels[(idx + 1) % levels.length];
  }
  state.thinkingLevel = next;
  localStorage.setItem('thinking-level', state.thinkingLevel);
  refreshThinkingButton();
  showToast(`Thinking: ${state.thinkingLevel}`);
}

// Demote state.thinkingLevel to a value valid for the current model's
// thinking_format. Called when the active chat's model changes.
function _ensureValidThinkingLevel() {
  const fmt = getActiveThinkingFormat();
  const cur = state.thinkingLevel || 'none';
  if (fmt === 'none') {
    // Don't clear localStorage — user might switch back to a thinking model.
    // The button is disabled in this state regardless.
    return;
  }
  const levels = _composerLevelsForFormat(fmt);
  if (!levels.includes(cur)) {
    state.thinkingLevel = 'none';
    localStorage.setItem('thinking-level', 'none');
    refreshThinkingButton();
  }
}

// Bulb glyph that grows brighter and more filled with the thinking level.
// Same outline at every level (so the user can read it as the same icon),
// but the fill height + glow inside the bulb step up: off=empty, low=1/3,
// medium=2/3, high=full. Color is applied via the button's CSS color so
// stroke + fill inherit cleanly.
let _thinkingIconSeq = 0;
function thinkingIconFor(level) {
  const bulbStroke = `<path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/><line x1="9" y1="21" x2="15" y2="21"/>`;
  let innerFill = '';
  if (level === 'low')         innerFill = `<rect x="5" y="13" width="14" height="4"  fill="currentColor" fill-opacity="0.35"/>`;
  else if (level === 'medium') innerFill = `<rect x="5" y="9"  width="14" height="8"  fill="currentColor" fill-opacity="0.55"/>`;
  else if (level === 'high')   innerFill = `<rect x="5" y="4"  width="14" height="13" fill="currentColor" fill-opacity="0.75"/>`;
  // Unique clipPath id per render — SVG ids are document-global and we may
  // have two thinking buttons (welcome + chat) mounted simultaneously.
  const cid = `tbc-${++_thinkingIconSeq}`;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
    <defs><clipPath id="${cid}"><path d="M12 2a7 7 0 017 7c0 3-2 5-2 8H7c0-3-2-5-2-8a7 7 0 017-7z"/></clipPath></defs>
    <g clip-path="url(#${cid})">${innerFill}</g>
    ${bulbStroke}
  </svg>`;
}

function refreshThinkingButton() {
  const fmt = getActiveThinkingFormat();
  const supported = fmt !== 'none';
  // Self-correct: if the saved level isn't valid for this model's format,
  // demote in-place. Done before the icon/tooltip render so the displayed
  // state matches what would actually be sent on the next turn. Skipped
  // when the model doesn't support thinking at all (button is disabled
  // anyway, so the saved level can stay around for when the user switches
  // back to a thinking-capable model).
  if (supported) {
    const valid = _composerLevelsForFormat(fmt);
    if (!valid.includes(state.thinkingLevel || 'none')) {
      state.thinkingLevel = 'none';
      localStorage.setItem('thinking-level', 'none');
    }
  }
  // Per-level color ramp: off=neutral grey, low=amber, medium=orange, high=red.
  const colorMap = { none: '', low: '#f59e0b', medium: '#f97316', high: '#ef4444' };
  const level = state.thinkingLevel || 'none';
  for (const btn of _composerToggleEls('btn-thinking')) {
    btn.innerHTML = thinkingIconFor(level);
    if (!supported) {
      btn.classList.add('disabled');
      btn.style.color = '';
      btn.style.opacity = '0.35';
      btn.style.cursor = 'not-allowed';
      btn.title = "Model doesn't support thinking";
    } else {
      btn.classList.remove('disabled');
      btn.style.opacity = '';
      btn.style.cursor = '';
      btn.style.color = colorMap[level] || '';
      // Show the levels this model accepts so the user knows what cycling
      // does. e.g. mistral_blocks → "Thinking: off · cycle: off → high".
      const valid = _composerLevelsForFormat(fmt);
      const cycleHint = valid.length > 1 ? ' · cycle: ' + valid.join(' → ') : '';
      btn.title = `Thinking: ${level} (${fmt})${cycleHint}`;
    }
  }
}

/* ═══════════════════════════════════════════════════════════
   REFINE & TOOL TOGGLE
   ═══════════════════════════════════════════════════════════ */
async function refineInput() {
  const input = _composerInputEl()
    || document.getElementById('chat-input')
    || document.getElementById('welcome-input')
    || document.getElementById('project-input');
  const text = input?.value?.trim();
  if (!text) { showToast('Type a message first', true); return; }

  const refineBtns = _composerToggleEls('btn-refine');
  try {
    for (const btn of refineBtns) btn.style.opacity = '0.4';
    showToast('Refining...');

    // Mirror the chat's caveman setting into the refiner so a caveman-mode
    // chat gets caveman-style rewrites instead of polished prose that
    // contradicts the response style we'll then ask the model to use.
    const result = await API.post("/v1/refine", {
      text: text,
      session_id: state.activeChat?.sessionId || null,
      caveman: parseInt(state.activeChat?.cavemanMode || 0, 10) || 0,
    });

    if (result.refined) {
      input.value = result.refined;
      autoResizeInput(input);
      updateSendButton();
      showToast('Message refined');
    }
    for (const btn of refineBtns) btn.style.opacity = '';
  } catch(e) {
    showToast('Refine failed: ' + e.message, true);
    for (const btn of refineBtns) btn.style.opacity = '';
  }
}

function toggleToolDisplay() {
  state.showToolCalls = !state.showToolCalls;
  localStorage.setItem('showToolCalls', state.showToolCalls);

  for (const btn of _composerToggleEls('btn-toggle-tools')) {
    btn.style.color = state.showToolCalls ? 'var(--accent-brand)' : '';
    btn.title = state.showToolCalls ? 'Tool calls: visible' : 'Tool calls: hidden';
  }

  renderMessages();
  showToast(state.showToolCalls ? 'Tool calls visible' : 'Tool calls hidden');
}

// Composer toggle for GDPR detail visibility. Privacy-first default: when
// off (initial state), every assistant reply renders with the inline yellow
// highlight stripped out and the Datenschutz disclosure body suppressed —
// only the header counts remain. When on, restored spans get tooltips and
// the disclosure expands as designed. Mirror of `toggleToolDisplay` —
// localStorage scope, immediate re-render of currently visible messages.
function toggleGdprDetails() {
  state.showGdprDetails = !state.showGdprDetails;
  localStorage.setItem('showGdprDetails', state.showGdprDetails);

  for (const btn of _composerToggleEls('btn-toggle-gdpr-details')) {
    btn.style.color = state.showGdprDetails ? 'var(--warning, #d97706)' : '';
    btn.title = state.showGdprDetails
      ? 'Datenschutz-Details: sichtbar (Markierungen + aufklappbarer Block)'
      : 'Datenschutz-Details: ausgeblendet (nur Statistik im Block)';
  }

  renderMessages();
  showToast(state.showGdprDetails
    ? 'Datenschutz-Details sichtbar'
    : 'Datenschutz-Details ausgeblendet');
}

// Transparent-anonymisation sticky preference reset (step 6.3). Clicking the
// shield-with-checkmark composer icon (only visible when chat.gdprActionPref
// is set) calls this to clear the preference, re-enabling the PII modal on
// the next send. Fire-and-forget toast on failure.
async function resetGdprActionPref() {
  const chat = state.activeChat;
  if (!chat || !chat.sessionId) return;
  const prev = chat.gdprActionPref || '';
  try {
    await API.updateGdprActionPref(chat.sessionId, '');
    chat.gdprActionPref = '';
    // Also clear the implicit "session already has a mapping" sticky so
    // sendMessage won't silently re-anonymise. The next PII-bearing turn
    // brings the modal back; chats without further PII proceed normally.
    chat.hasGdprMapping = false;
    updateStatusBar();
    const labels = {
      'anonymise':   'auto-anonymise',
      'local_model': 'auto-local-model',
      'continue':    'auto-continue',
    };
    showToast(`PII preference reset (was: ${labels[prev] || prev}) — modal will reappear`);
  } catch (e) {
    showToast('Failed to reset PII preference: ' + e.message, true);
  }
}

async function toggleSaveToMemory() {
  const chat = state.activeChat;
  if (!chat) return;
  // Welcome screen: session may not be created yet — wait for it. ensureSession is
  // fire-and-forget kicked off by newChat(), so this just awaits the same promise.
  if (!chat.sessionId) {
    try { await ensureSession(chat); } catch { return; }
    if (!chat.sessionId) return;
  }
  // Three states: on → auto → off → on (DB: 1=on, 2=auto, 0=off)
  const cur = chat.memoryMode || (chat.saveToMemory ? 'on' : 'off');
  const next = cur === 'on' ? 'auto' : cur === 'auto' ? 'off' : 'on';
  const modeMap = { on: 1, auto: 2, off: 0 };

  // When turning off after the chat was being memorised, ask whether to purge
  // previously filed drawers from MemPalace or just stop filing new turns.
  // Skip the prompt on an empty chat — there's nothing to have been memorised yet.
  let purge = false;
  const hasHistory = (chat.messages || []).some(m => m.role === 'user' || m.role === 'assistant');
  if (next === 'off' && (cur === 'on' || cur === 'auto') && hasHistory) {
    const choice = await showDialog({
      title: 'Memory turned off',
      message: 'Also delete previously stored memories of this chat from MemPalace?',
      buttons: [
        { label: 'Keep memories', value: 'keep' },
        { label: 'Delete memories', value: 'delete', primary: true, danger: true },
      ],
    });
    purge = choice === 'delete';
  }

  try {
    await API.post('/v1/sessions/manage', {
      action: 'save_to_memory', session_id: chat.sessionId,
      mode: modeMap[next],
    });
    chat.saveToMemory = next === 'on';
    chat.memoryMode = next;
    updateStatusBar();

    if (purge) {
      try {
        await API.post('/v1/sessions/manage', {
          action: 'purge_memory', session_id: chat.sessionId,
        });
        showToast('Memory: off — stored memories deleted');
      } catch(e) {
        showToast('Toggled off, but purge failed: ' + e.message, true);
      }
      return;
    }
    const labels = { on: 'Memory: on — all messages saved', auto: 'Memory: auto — LLM classifier decides', off: 'Memory: off' };
    showToast(labels[next]);
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

// ─── Research-mode override (project chats only) ───────────────────────
// Cache the project's `research_mode` default per agent+name so the
// composer button can render the effective state without an extra
// fetch on every chat open. Refreshed on demand from
// API.getProject(...) — sub-second; the cache just absorbs repeat
// reads in the same view.
state._projectResearchModeCache = state._projectResearchModeCache || {};
async function _projectResearchModeDefault(agentId, projectName) {
  if (!agentId || !projectName) return null;
  const key = agentId + '::' + projectName;
  const cached = state._projectResearchModeCache[key];
  if (cached !== undefined) return cached;
  try {
    const project = await API.getProject(agentId, projectName);
    const v = !!(project && project.research_mode);
    state._projectResearchModeCache[key] = v;
    return v;
  } catch {
    return null;
  }
}

// Composer button cycles between two states (per spec):
//   project default ↔ override-opposite-of-default
// Clicking when no override is set installs the opposite of the project
// default; clicking again clears the override (back to project default).
async function toggleResearchModeOverride() {
  const chat = state.activeChat;
  if (!chat) return;
  if (!chat.project) return;  // button is hidden anyway in non-project chats
  if (!chat.sessionId) {
    try { await ensureSession(chat); } catch { return; }
    if (!chat.sessionId) return;
  }
  const agentId = chat.agentId || state.activeAgentId || 'main';
  const projectDefault = await _projectResearchModeDefault(agentId, chat.project);
  if (projectDefault === null) {
    showToast('Could not read project default', true);
    return;
  }
  // Two-state cycle: if override is null, install !projectDefault. Else clear.
  const cur = (chat.researchModeOverride === null
                || chat.researchModeOverride === undefined)
                ? null
                : !!chat.researchModeOverride;
  const next = (cur === null) ? !projectDefault : null;
  try {
    await API.post('/v1/sessions/manage', {
      action: 'research_mode_override',
      session_id: chat.sessionId,
      value: next,
    });
    chat.researchModeOverride = next;
    refreshResearchModeButton();
    let effective = (next === null) ? projectDefault : next;
    let label;
    if (next === null) {
      label = `Research mode: project default (${projectDefault ? 'on' : 'off'})`;
    } else {
      label = `Research mode: ${effective ? 'on' : 'off'} (overriding project default)`;
    }
    showToast(label);
  } catch (e) {
    showToast('Failed: ' + e.message, true);
  }
}

async function refreshResearchModeButton() {
  const btns = _composerToggleEls('btn-research-mode');
  if (!btns.length) return;
  const chat = state.activeChat;
  // Hide entirely when not in a project chat — research mode is a
  // per-project concept; non-project chats have nothing to override.
  const inProject = !!(chat && chat.project);
  for (const btn of btns) {
    btn.style.display = inProject ? '' : 'none';
  }
  if (!inProject) return;
  const agentId = chat.agentId || state.activeAgentId || 'main';
  const projectDefault = await _projectResearchModeDefault(agentId, chat.project);
  const override = (chat.researchModeOverride === null
                     || chat.researchModeOverride === undefined)
                     ? null
                     : !!chat.researchModeOverride;
  const effective = (override === null) ? !!projectDefault : override;
  for (const btn of btns) {
    btn.classList.toggle('active', effective);
    btn.style.color = effective ? '#3b82f6' : '';  // blue when on
    btn.style.opacity = (override === null) ? '' : '1';  // emphasised when overridden
    let tip;
    if (override === null) {
      tip = `Research mode: ${effective ? 'on' : 'off'} (project default — click to override)`;
    } else {
      tip = `Research mode: ${effective ? 'on' : 'off'} (session override — click to revert to project default of ${projectDefault ? 'on' : 'off'})`;
    }
    btn.title = tip;
    // Thin underline marker shows there's an active override.
    btn.style.borderBottom = (override === null) ? '' : '2px solid #3b82f6';
  }
}

// Caveman mode icon set — one per level. Metaphor: off = fastest/modern
// (spaceship), down through car, horse, to campfire = primitive.
// Shared icon-toggle button used by every refine-with-AI surface
// (profile fields, scheduled-task prompts, long-form profile editor).
// Same spaceship→car→horse→campfire glyphs as the chat composer's
// btn-caveman so users see one visual vocabulary across the app.
// Click cycles 0→1→2→3→0; tooltip names the level. Persists to
// localStorage('refine-caveman:<textareaId>'); dependants read the
// current value via _refineCavemanValue(textareaId).
function _refineCavemanLabel(mode) {
  return ['off (spaceship)', 'lite (car)', 'full (horse)', 'ultra (campfire)'][mode] || 'off';
}
function _refineCavemanButton(textareaId) {
  let cur = 0;
  try { cur = parseInt(localStorage.getItem('refine-caveman:' + textareaId) || '0', 10) || 0; }
  catch(e) {}
  if (![0,1,2,3].includes(cur)) cur = 0;
  return `<button type="button" id="${textareaId}-refine-caveman"
    data-caveman="${cur}"
    onclick="_refineCavemanCycle('${textareaId}')"
    title="Refiner caveman: ${_refineCavemanLabel(cur)} — click to cycle"
    style="background:transparent;border:1px solid var(--border-light);border-radius:4px;width:24px;height:24px;padding:2px;color:var(--text-300);cursor:pointer;display:inline-flex;align-items:center;justify-content:center">${_sizedCavemanSvg(cur)}</button>`;
}

// cavemanIconFor() returns SVGs without width/height attributes (sized by
// .composer-btn svg CSS in the chat). The refine button has no such rule —
// stamp explicit 16×16 dimensions onto the <svg> tag so it renders at a
// visible size regardless of stylesheet context.
function _sizedCavemanSvg(mode) {
  return cavemanIconFor(mode).replace('<svg ', '<svg width="16" height="16" ');
}
function _refineCavemanCycle(textareaId) {
  const btn = document.getElementById(textareaId + '-refine-caveman');
  if (!btn) return;
  const cur = parseInt(btn.dataset.caveman || '0', 10) || 0;
  const next = (cur + 1) % 4;
  btn.dataset.caveman = String(next);
  btn.innerHTML = _sizedCavemanSvg(next);
  btn.title = `Refiner caveman: ${_refineCavemanLabel(next)} — click to cycle`;
  try { localStorage.setItem('refine-caveman:' + textareaId, String(next)); }
  catch(e) {}
}
function _refineCavemanValue(textareaId) {
  const btn = document.getElementById(textareaId + '-refine-caveman');
  if (btn && btn.dataset && btn.dataset.caveman != null) {
    const v = parseInt(btn.dataset.caveman, 10);
    if ([0,1,2,3].includes(v)) return v;
  }
  // Back-compat: a legacy <select> may still exist if something forgot to
  // re-render. Read its value if so.
  const sel = document.getElementById(textareaId + '-refine-caveman');
  if (sel && 'value' in sel) {
    const v = parseInt(sel.value, 10);
    if ([0,1,2,3].includes(v)) return v;
  }
  return 0;
}

function cavemanIconFor(mode) {
  const common = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"';
  if (mode === 1) {
    // Car — side silhouette with two wheels
    return `<svg ${common}><path d="M3 14l2-5a2 2 0 012-2h10a2 2 0 012 2l2 5v4H3z" fill="currentColor" fill-opacity="0.15"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/><path d="M5 14h14"/></svg>`;
  }
  if (mode === 2) {
    // Horse — simplified side profile (head, neck, body, legs)
    return `<svg ${common}><path d="M4 19v-4c0-2 1.5-3.5 3.5-3.5h6c1 0 1.5-.5 2-1.5L17 7l2 1-1 2c-.3.8-.8 1.3-1.5 1.7.5.8.5 1.8.5 2.8v4" fill="currentColor" fill-opacity="0.15"/><path d="M17 7l2-1.5 1 1-1.5 1.5"/><circle cx="18.5" cy="6" r="0.5" fill="currentColor"/><line x1="7" y1="19" x2="7" y2="22"/><line x1="10" y1="19" x2="10" y2="22"/><line x1="14" y1="19" x2="14" y2="22"/><line x1="17" y1="19" x2="17" y2="22"/></svg>`;
  }
  if (mode === 3) {
    // Campfire (flame + crossed logs) — same glyph used elsewhere for caveman
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c.5 2.5 2.5 3.8 2.5 6.5a2.5 2.5 0 01-5 0c0-.8.3-1.4.9-2-.2 1 .4 1.6 1 1.6 0-1.8.5-3.6.6-6.1z" fill="currentColor" fill-opacity="0.15"/><line x1="4" y1="21" x2="20" y2="15"/><line x1="4" y1="15" x2="20" y2="21"/></svg>`;
  }
  // Default / off — spaceship (modern, fastest)
  return `<svg ${common}><path d="M12 2c3 3 5 7 5 12v3l-5-2-5 2v-3c0-5 2-9 5-12z" fill="currentColor" fill-opacity="0.15"/><circle cx="12" cy="10" r="1.5"/><path d="M7 15l-3 3v3l4-2M17 15l3 3v3l-4-2"/><path d="M10 19l2 3 2-3"/></svg>`;
}

async function toggleCavemanMode() {
  const chat = state.activeChat;
  if (!chat) return;
  // Welcome screen: session may not be created yet — wait for it.
  if (!chat.sessionId) {
    try { await ensureSession(chat); } catch { return; }
    if (!chat.sessionId) return;
  }
  const next = ((chat.cavemanMode || 0) + 1) % 4;
  try {
    await API.post('/v1/sessions/manage', {
      action: 'caveman_mode', session_id: chat.sessionId,
      mode: next,
    });
    chat.cavemanMode = next;
    localStorage.setItem('caveman-chat-mode', String(next));
    updateStatusBar();
    const labels = {
      0: 'Caveman: off 🚀',
      1: 'Caveman: lite 🚗 — removes filler',
      2: 'Caveman: full 🐎 — telegraphic',
      3: 'Caveman: ultra 🔥 — max compression',
    };
    showToast(labels[next]);
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

/* ═══════════════════════════════════════════════════════════
   KEYBOARD SHORTCUTS
   ═══════════════════════════════════════════════════════════ */
document.addEventListener('keydown', (e) => {
  // Ctrl+B: toggle sidebar
  if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
    e.preventDefault();
    toggleSidebar();
  }

  // Ctrl+K: search
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault();
    openSearchModal();
  }

  // Ctrl+Shift+O: new chat
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'O') {
    e.preventDefault();
    newChat();
  }

  // Enter to send (in composer)
  if (e.key === 'Enter' && !e.shiftKey) {
    const active = document.activeElement;
    if (active?.id === 'welcome-input' || active?.id === 'chat-input' || active?.id === 'project-input') {
      e.preventDefault();
      sendMessage();
    }
  }

  // Escape to close modals
  if (e.key === 'Escape') {
    const modal = document.querySelector('.modal-overlay');
    if (modal) modal.remove();
  }

  // Slash commands
  const active = document.activeElement;
  if ((active?.id === 'welcome-input' || active?.id === 'chat-input' || active?.id === 'project-input') && active.value === '/') {
    showSlashPopup(active);
  }
});

// Tab / ArrowRight to accept next-prompt ghost-text suggestion (chat input only)
document.addEventListener('keydown', function(e) {
  if (e.target?.id !== 'chat-input') return;
  if (!NextPrompt.active()) return;
  const input = e.target;
  // Tab accepts + edits; ArrowRight accepts only when cursor is at end of an empty input
  if (e.key === 'Tab' && !e.shiftKey) {
    if (input.value.length === 0) {
      e.preventDefault();
      NextPrompt.accept({ submit: false });
    }
  } else if (e.key === 'ArrowRight') {
    if (input.value.length === 0) {
      e.preventDefault();
      NextPrompt.accept({ submit: false });
    }
  } else if (e.key === 'Escape') {
    NextPrompt.clear();
  }
}, true);

// Attach input/paste listeners to all three composer textareas.
// Must be called after initComposers() so the elements exist.
function _initComposerListeners() {
  ['welcome-input', 'chat-input', 'project-input'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', function() {
      // Any typing dismisses an active suggestion (chat-input only)
      if (id === 'chat-input' && NextPrompt.active() && this.value.length > 0) {
        NextPrompt.clear();
      }
      if (this.value.startsWith('/') && this.value.length > 0) {
        showSlashPopup(this);
      } else {
        hideSlashPopup();
      }
    });
    // Paste handler for images (Electron native clipboard + browser fallback)
    el.addEventListener('paste', async function(e) {
      let handled = false;
      // Browser clipboard items (works for Cmd+V of copied images in browser)
      const items = e.clipboardData?.items;
      if (items) {
        for (const item of items) {
          if (item.type.startsWith('image/')) {
            e.preventDefault();
            handled = true;
            const blob = item.getAsFile();
            const reader = new FileReader();
            reader.onload = (ev) => {
              state._pendingFiles.push({
                name: 'pasted-image',
                type: item.type,
                data: ev.target.result.split(',')[1],
                encoding: 'base64',
                preview: ev.target.result,
                // Image paste — accepted PII-scan gap; mark scan as done +
                // non-blocking so the composer's "wait for scan" gate
                // doesn't hold the send.
                scan: { state: 'done', scanned: false, reason: 'media' },
              });
              renderFilePreviews();
              updateSendButton();
            };
            reader.readAsDataURL(blob);
          }
        }
      }
      // Electron native clipboard fallback (catches macOS screenshots etc.)
      if (!handled && window.electronAPI?.clipboardReadImage) {
        const img = await window.electronAPI.clipboardReadImage();
        if (img) {
          e.preventDefault();
          state._pendingFiles.push({
            name: 'pasted-image',
            type: 'image/png',
            data: img.data,
            encoding: 'base64',
            preview: `data:image/png;base64,${img.data}`,
            scan: { state: 'done', scanned: false, reason: 'media' },
          });
          renderFilePreviews();
          updateSendButton();
        }
      }
    });
  });
}

/* ═══════════════════════════════════════════════════════════
   SLASH COMMANDS
   ═══════════════════════════════════════════════════════════ */
const slashCommands = [
  { cmd: '/new', desc: 'Start new conversation' },
  { cmd: '/clear', desc: 'Clear messages' },
  { cmd: '/agent', desc: 'Switch agent' },
  { cmd: '/model', desc: 'Switch model' },
  { cmd: '/settings', desc: 'Open settings' },
  { cmd: '/thinking', desc: 'Toggle thinking level' },
];

function showSlashPopup(inputEl) {
  const popupId = inputEl.id === 'welcome-input' ? 'welcome-slash-popup'
                : inputEl.id === 'project-input' ? 'project-slash-popup'
                : 'chat-slash-popup';
  const popup = document.getElementById(popupId);
  if (!popup) return;

  const query = inputEl.value.substring(1).toLowerCase();
  const filtered = slashCommands.filter(c => c.cmd.substring(1).includes(query));

  if (!filtered.length) { popup.classList.remove('visible'); return; }

  popup.innerHTML = filtered.map((c, i) => `
    <div class="slash-popup-item${i === 0 ? ' active' : ''}" onclick="executeSlashCommand('${c.cmd}')">
      <span class="slash-popup-cmd">${c.cmd}</span>
      <span class="slash-popup-desc">${c.desc}</span>
    </div>
  `).join('');

  popup.classList.add('visible');
}

function hideSlashPopup() {
  document.querySelectorAll('.slash-popup').forEach(p => p.classList.remove('visible'));
}

function executeSlashCommand(cmd) {
  hideSlashPopup();
  const input = document.activeElement;
  if (input) input.value = '';

  switch(cmd) {
    case '/new': newChat(); break;
    case '/clear':
      if (state.activeChat) { state.activeChat.messages = []; renderMessages(); }
      break;
    case '/settings': openSettingsModal(); break;
    case '/thinking': cycleThinkingLevel(); break;
  }
}

/* ═══════════════════════════════════════════════════════════
   CHAT TITLE MENU
   ═══════════════════════════════════════════════════════════ */
function toggleChatTitleMenu(event) {
  const chat = state.activeChat;
  if (!chat?.sessionId || state.currentView !== 'chat') return;

  const el = document.getElementById('page-header-title');
  // Already editing?
  if (el.querySelector('input')) return;

  const current = chat.chatTitle || '';
  const input = document.createElement('input');
  input.type = 'text';
  input.value = current;
  input.placeholder = 'Name this chat...';
  input.style.cssText = 'font:inherit;color:inherit;background:var(--bg-200);border:1px solid var(--accent);border-radius:4px;padding:2px 6px;width:240px;outline:none;';

  const finish = async () => {
    const newTitle = input.value.trim();
    if (newTitle && newTitle !== current) {
      chat.chatTitle = newTitle;
      // Persist to server
      try {
        await API.post('/v1/sessions/manage', {action: 'rename', session_id: chat.sessionId, title: newTitle});
        // Update sidebar session list
        const agentData = state.agentSessions[state.activeAgentId];
        if (agentData?.sessions) {
          const s = agentData.sessions.find(s => (s.id || s.session_id) === chat.sessionId);
          if (s) s.title = newTitle;
          renderRecentChats();
        }
      } catch(e) {}
    }
    updateChatView();
  };

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = current; input.blur(); }
  });
  input.addEventListener('blur', finish);

  el.textContent = '';
  el.appendChild(input);
  input.focus();
  input.select();
}

/* ═══════════════════════════════════════════════════════════
   INITIALIZATION
   ═══════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// Auth System
// ═══════════════════════════════════════════════════════════
state.authUser = null;  // current user object
state.authEnabled = false;  // whether server has auth enabled
state.userTeams = [];  // user's teams (for project-visibility picker)

function showAuthOverlay() {
  document.getElementById('auth-overlay').style.display = 'flex';
  document.getElementById('app-root').style.display = 'none';
}
function hideAuthOverlay() {
  document.getElementById('auth-overlay').style.display = 'none';
  document.getElementById('app-root').style.display = '';
}
function showAuthError(msg) {
  const el = document.getElementById('auth-error');
  el.textContent = msg;
  el.style.display = '';
}

async function authLogin() {
  const username = document.getElementById('auth-username').value.trim();
  const password = document.getElementById('auth-password').value;
  if (!username || !password) { showAuthError('Username and password required'); return; }
  try {
    const r = await fetch(`${BASE_URL}/v1/auth/login`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username, password})
    });
    const data = await r.json();
    if (data.error) { showAuthError(data.error); return; }
    localStorage.setItem('auth-token', data.token);
    localStorage.setItem('auth-user', JSON.stringify(data.user));
    state.authUser = data.user;
    // Fetch user's teams for project visibility picker
    try {
      const me = await fetch(`${BASE_URL}/v1/auth/me`, {headers:{'Authorization':`Bearer ${data.token}`}}).then(r=>r.json());
      state.userTeams = me.teams || [];
    } catch(e) { state.userTeams = []; }
    hideAuthOverlay();
    renderUserMenu();
    init();
  } catch(e) { showAuthError('Connection failed'); }
}

function authLogout() {
  localStorage.removeItem('auth-token');
  localStorage.removeItem('auth-user');
  state.authUser = null;
  if (state.authEnabled) showAuthOverlay();
}

async function checkAuth() {
  // Check if server has auth enabled
  const token = localStorage.getItem('auth-token');

  // Try /v1/auth/me — if 401, auth is enabled and we need login
  // If no token and auth is enabled, show login
  if (!token) {
    // Check if auth is enabled by trying a protected endpoint
    try {
      const r = await fetch(`${BASE_URL}/v1/agents`, {headers: {'Content-Type':'application/json'}});
      if (r.status === 401) {
        state.authEnabled = true;
        showAuthOverlay();
        return false;
      }
      // Auth not enabled, continue as normal
      state.authEnabled = false;
      return true;
    } catch(e) { return true; } // server down, let init handle it
  }

  // Have token — validate it
  try {
    const r = await fetch(`${BASE_URL}/v1/auth/me`, {
      headers: {'Content-Type':'application/json', 'Authorization': `Bearer ${token}`}
    });
    if (r.status === 401) {
      state.authEnabled = true;
      localStorage.removeItem('auth-token');
      localStorage.removeItem('auth-user');
      showAuthOverlay();
      return false;
    }
    const data = await r.json();
    state.authUser = data.user;
    state.userTeams = data.teams || [];
    state.authEnabled = true;
    localStorage.setItem('auth-user', JSON.stringify(data.user));
    renderUserMenu();
    return true;
  } catch(e) { return true; }
}

function renderUserMenu() {
  const user = state.authUser;
  const avatarEl = document.getElementById('sb-user-avatar');
  const nameEl = document.getElementById('sb-user-name');
  const planEl = document.getElementById('sb-user-plan');
  const dropdownEl = document.getElementById('sb-user-dropdown');

  if (!user || !state.authEnabled) {
    // Not authenticated — show default "Agent / Connected"
    if (avatarEl) avatarEl.textContent = 'A';
    if (nameEl) nameEl.textContent = 'Agent';
    if (planEl) { planEl.textContent = state.connected ? 'Connected' : 'Disconnected'; planEl.className = 'sb-user-plan'; }
    if (dropdownEl) dropdownEl.innerHTML = '';
    return;
  }

  // Show user info
  const initials = (user.display_name || user.username || '?').split(' ').map(w => w[0]).join('').slice(0,2).toUpperCase();
  if (avatarEl) { avatarEl.textContent = initials; avatarEl.style.background = 'var(--accent-primary, #c96442)'; avatarEl.style.color = '#fff'; }
  if (nameEl) nameEl.textContent = user.display_name || user.username;
  if (planEl) { planEl.textContent = user.role; planEl.className = 'sb-user-plan'; planEl.style.textTransform = 'capitalize'; }

  // Greeting name may have changed (login, profile save) — refresh it.
  refreshWelcomeGreeting();

  // Populate dropdown. Account settings is the personal modal — distinct
  // from General settings (org-wide config, admin-only). Removed the
  // duplicate "Settings" entry that previously mirrored the gear icon.
  if (dropdownEl) {
    dropdownEl.innerHTML = `
      <div class="sb-user-dropdown-item" onclick="event.stopPropagation(); document.getElementById('sb-user-dropdown')?.classList.remove('open'); openUserSettings()"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="7" r="4"/><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/></svg>Account settings</div>
      ${user.role === 'admin' ? '<div class="sb-user-dropdown-item" onclick="event.stopPropagation(); openUserManagement()"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>Manage Users</div>' : ''}
      ${(user.role === 'admin' || user.role === 'poweruser') ? '<div class="sb-user-dropdown-item" onclick="event.stopPropagation(); openUserTeams()"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>User Teams</div>' : ''}
      ${user.role === 'admin' ? '<div class="sb-user-dropdown-item" onclick="event.stopPropagation(); openGeneralSettings()"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>General settings</div>' : ''}
      <div class="sb-user-dropdown-item danger" onclick="event.stopPropagation(); authLogout()"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/></svg>Sign out</div>
    `;
  }

  // Role may have changed (login, role edit) — refresh status-bar gating now
  // so users don't briefly see admin-only fields before their first turn.
  if (typeof applyStatusBarRoleVisibility === 'function') applyStatusBarRoleVisibility();

  // Sidebar "Customize" → admin-only (opens agent soul/agent.json/MCP/hooks
  // editors, all admin-gated POSTs). Hide for non-admins so the row doesn't
  // tease an editor that 403s on save.
  const customizeNav = document.getElementById('sb-nav-customize');
  if (customizeNav) {
    const isAdmin = (state.authUser?.role || 'admin') === 'admin';
    customizeNav.style.display = isAdmin ? '' : 'none';
  }
}

function toggleUserDropdown() {
  const dd = document.getElementById('sb-user-dropdown');
  if (dd && dd.innerHTML.trim()) dd.classList.toggle('open');
}

// Close user dropdown when clicking elsewhere
document.addEventListener('click', (e) => {
  const dd = document.getElementById('sb-user-dropdown');
  if (dd && !e.target.closest('#sb-user-section')) dd.classList.remove('open');
});


// ── Role-based UI visibility ──
function applyRoleVisibility() {
  const user = state.authUser;
  if (!user || !state.authEnabled) return;
  // Hide admin-only UI elements for non-admins
  document.querySelectorAll('[data-role-min]').forEach(el => {
    const minRole = el.dataset.roleMin;
    const roleOrder = {user: 0, poweruser: 1, admin: 2};
    const userLevel = roleOrder[user.role] ?? 0;
    const requiredLevel = roleOrder[minRole] ?? 2;
    el.style.display = userLevel >= requiredLevel ? '' : 'none';
  });
}

// ── Project visibility picker ──
function getProjectVisibilityHTML() {
  const user = state.authUser;
  if (!user || !state.authEnabled) return '';
  return `
    <div style="margin-top:8px">
      <label style="font-size:12px;color:var(--text-secondary)">Visibility</label>
      <select id="project-visibility" style="width:100%;padding:8px;margin-top:4px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <option value="global">Global (everyone)</option>
        <option value="user">Private (me only)</option>
        <option value="team">Team</option>
      </select>
    </div>
  `;
}


// Clone the <template id="composer-template"> into each of the three mount
// points, assigning per-view element IDs. Called once at startup before any
// code that queries composer elements.
//
// ID scheme (data-id → assigned id):
//   welcome:  welcome-input, welcome-image-preview, welcome-slash-popup,
//             welcome-btn-{thinking,refine,toggle-tools,save-to-memory,caveman},
//             welcome-model-selector, welcome-warmup-dot,
//             welcome-model-name, welcome-send-btn, welcome-stop-btn
//   chat:     chat-input, chat-image-preview, chat-slash-popup,
//             btn-{thinking,refine,toggle-tools,save-to-memory,caveman},
//             chat-model-selector, chat-warmup-dot,
//             chat-model-name, chat-send-btn, chat-stop-btn
//   project:  project-input, project-image-preview, project-slash-popup,
//             project-btn-{thinking,refine,toggle-tools,save-to-memory,caveman},
//             project-model-selector, project-warmup-dot,
//             project-model-name, project-send-btn, project-stop-btn
function initComposers() {
  const tpl = document.getElementById('composer-template');
  if (!tpl) return;

  const views = [
    {
      mountId:     'welcome-composer-mount',
      idPrefix:    'welcome-',   // applied to btn-*, model-selector, warmup-dot, model-name, send-btn, stop-btn, image-preview, slash-popup
      inputId:     'welcome-input',
      placeholder: 'Ask anything...',
    },
    {
      mountId:     'chat-composer-mount',
      idPrefix:    '',           // btn-thinking etc stay bare; send/stop/model get chat- prefix below
      inputId:     'chat-input',
      placeholder: 'Reply...',
      // For chat view the per-element IDs don't all follow one simple prefix rule:
      // buttons keep bare (btn-thinking), send/stop/model/warmup/local get chat- prefix.
      chatStyle:   true,
    },
    {
      mountId:     'project-composer-mount',
      idPrefix:    'project-',
      inputId:     'project-input',
      placeholder: 'Write your message to Claude',
    },
  ];

  for (const view of views) {
    const mount = document.getElementById(view.mountId);
    if (!mount) continue;

    const clone = tpl.content.cloneNode(true);
    const box = clone.querySelector('[data-composer-box]');

    // Assign the composer-box id (legacy callers like panels.js use e.g. #chat-composer)
    if (view.chatStyle)    box.id = 'chat-composer';
    else if (view.idPrefix === 'project-') box.id = 'project-composer';
    // welcome doesn't need a box id

    // Helper: find element by data-id inside clone and set its id attribute
    const set = (dataId, id) => {
      const el = clone.querySelector(`[data-id="${dataId}"]`);
      if (el) el.id = id;
    };

    const p = view.idPrefix;

    if (view.chatStyle) {
      // Chat: buttons are bare, infra elements are chat-prefixed
      set('input',                'chat-input');
      set('image-preview',        'chat-image-preview');
      set('slash-popup',          'chat-slash-popup');
      set('btn-thinking',         'btn-thinking');
      set('btn-refine',           'btn-refine');
      set('btn-toggle-tools',     'btn-toggle-tools');
      set('btn-save-to-memory',   'btn-save-to-memory');
      set('btn-caveman',          'btn-caveman');
      set('model-selector',       'chat-model-selector');
      set('warmup-dot',           'chat-warmup-dot');
      set('model-name',           'chat-model-name');
      set('send-btn',             'chat-send-btn');
      set('stop-btn',             'chat-stop-btn');
    } else {
      // welcome and project: all elements get the view prefix
      set('input',                view.inputId);
      set('image-preview',        p + 'image-preview');
      set('slash-popup',          p + 'slash-popup');
      set('btn-thinking',         p + 'btn-thinking');
      set('btn-refine',           p + 'btn-refine');
      set('btn-toggle-tools',     p + 'btn-toggle-tools');
      set('btn-save-to-memory',   p + 'btn-save-to-memory');
      set('btn-caveman',          p + 'btn-caveman');
      set('model-selector',       p + 'model-selector');
      set('warmup-dot',           p + 'warmup-dot');
      set('model-name',           p + 'model-name');
      set('send-btn',             p + 'send-btn');
      set('stop-btn',             p + 'stop-btn');
    }

    // Set placeholder text
    const textarea = clone.querySelector('textarea');
    if (textarea) textarea.placeholder = view.placeholder;

    mount.appendChild(clone);
  }
}

async function init() {
  // Check auth first
  const authOk = await checkAuth();
  if (!authOk) return;

  // Clone composer template into all three views before any code queries IDs
  initComposers();
  _initComposerListeners();

  // Load theme
  const savedTheme = localStorage.getItem('theme') || 'light';
  setTheme(savedTheme);

  // Restore sidebar state
  if (localStorage.getItem('sidebar-collapsed') === '1') {
    document.getElementById('sidebar').classList.add('collapsed');
  }
  restoreSidebarSections();

  // Init artifact panel resize
  initArtifactResize();

  // Wire chat scroll-to-top / scroll-to-bottom anchor arrows
  initScrollAnchors();

  // Set greeting (auth-aware; re-runs after /v1/auth/me lands)
  refreshWelcomeGreeting();

  // Load initial data
  try {
    const [statusData, agentsData, modelsData, providersData, teamsData, modelsConfigData, servicesData, clfData] = await Promise.all([
      API.getStatus().catch(() => null),
      API.getAgents().catch(() => ({agents:[]})),
      API.getModels().catch(() => ({models:[]})),
      API.getProviders().catch(() => []),
      API.getTeams().catch(() => ({})),
      API.getModelsConfig().catch(() => ({})),
      API.getServices().catch(() => ({})),
      API.get('/v1/mempalace/classifier').catch(() => ({})),
    ]);

    state.connected = !!statusData;
    state.serverInfo = statusData;
    state.agents = agentsData.agents || agentsData || [];
    state.models = modelsData.models || modelsData || [];
    state.providers = Array.isArray(providersData) ? providersData : (providersData.providers || []);
    state.teamStructure = teamsData || {};
    state.modelsConfig = modelsConfigData || {};
    applyGdprConfigToScanner((servicesData.server || {}).gdpr_scanner);
    state.mempalaceClassifier = clfData || {};

    // Update user menu / connection indicator
    renderUserMenu();

    // Auto-select first agent
    if (state.agents.length) {
      const mainAgent = state.agents.find(a => (a.id || a.name) === 'main') || state.agents[0];
      selectAgent(mainAgent.id || mainAgent.name);
    }

    // Load sessions for agents (background)
    for (const agent of state.agents) {
      loadAgentSessions(agent.id || agent.name);
    }

    // Refresh model display now that modelsConfig is loaded
    if (state.activeChat?.model) {
      updateModelSelectorDisplay(state.activeChat.model);
    }

    // Render favourite cards on the welcome screen; re-render on any change
    renderPromptCards();
    window.addEventListener('favourites:changed', () => renderPromptCards());

  } catch(e) {
    console.error('Init failed:', e);
    showToast('Failed to connect to server', true);
  }

  // Init button states from stored preferences
  for (const toolBtn of _composerToggleEls('btn-toggle-tools')) {
    toolBtn.style.color = state.showToolCalls ? 'var(--accent-brand)' : '';
    toolBtn.title = state.showToolCalls ? 'Tool calls: visible' : 'Tool calls: hidden';
  }
  for (const gdprBtn of _composerToggleEls('btn-toggle-gdpr-details')) {
    gdprBtn.style.color = state.showGdprDetails ? 'var(--warning, #d97706)' : '';
    gdprBtn.title = state.showGdprDetails
      ? 'Datenschutz-Details: sichtbar (Markierungen + aufklappbarer Block)'
      : 'Datenschutz-Details: ausgeblendet (nur Statistik im Block)';
  }
  // Demote a stored thinking_level that doesn't fit the active chat's
  // model (e.g. localStorage carried 'medium' across a model switch to a
  // mistral_blocks model that only accepts none/high).
  try { _ensureValidThinkingLevel(); } catch(_) {}
  refreshThinkingButton();
  refreshResearchModeButton();

  // Start on welcome view
  navigateTo('welcome');

  // Spin up the floating ASCII companion (reads buddy_species from prefs).
  if (typeof buddyInit === 'function') buddyInit();

  // Start connection health monitor
  ConnectionMonitor._connected = state.connected;
  const dot = document.getElementById('status-connection-dot');
  if (dot) dot.className = 'connection-dot ' + (state.connected ? 'connected' : 'disconnected');
  const cWrap = document.getElementById('status-connection');
  if (cWrap) cWrap.title = state.connected ? 'Server: connected' : 'Server: disconnected';
  ConnectionMonitor.start();
  MempalaceActivityMonitor.start();
  WarmupMonitor.start();
  QueueMonitor.start();
  QuotaMonitor.start();
}

async function renderPromptCards() {
  const container = document.getElementById('prompt-cards');
  if (!container) return;

  await FavouritesCache.load();
  const favs = FavouritesCache.rows
    .filter(r => r.available !== false)
    .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
    .slice(0, 6);

  if (!favs.length) {
    container.innerHTML = '';
    return;
  }

  container.innerHTML = favs.map((row, i) => {
    const def = (typeof FAVOURITES_TYPE_DEFAULTS !== 'undefined' && FAVOURITES_TYPE_DEFAULTS[row.item_type]) || {};
    const rawIcon = row.icon || row.source_icon || '';
    const customIcon = (rawIcon && rawIcon !== def.icon) ? rawIcon : '';
    const iconHtml = customIcon
      ? esc(customIcon)
      : (typeof favouriteTypeGlyphSvg === 'function' ? favouriteTypeGlyphSvg(row.item_type, 20) : '⭐');
    const bg = row.color || row.source_color || def.color || 'var(--chip-bg)';
    const title = (row.title || '(untitled)').slice(0, 50);
    const subtitle = (row.subtitle || '').slice(0, 80);
    return `<button class="prompt-card" data-fav-idx="${i}" style="--card-accent:${esc(bg)}">
      <span class="prompt-card-icon">${iconHtml}</span>
      <span class="prompt-card-title">${esc(title)}</span>
      ${subtitle ? `<span class="prompt-card-body">${esc(subtitle)}</span>` : ''}
    </button>`;
  }).join('');

  container.querySelectorAll('.prompt-card').forEach((btn, i) => {
    btn.addEventListener('click', () => {
      if (typeof openFavouriteRow === 'function') openFavouriteRow(favs[i]);
    });
  });
}

// Sidebar version badge — fetched independently of auth so it's visible
// on the login screen too. /v1/status is public.
(async () => {
  try {
    const r = await fetch(`${BASE_URL}/v1/status`, { signal: AbortSignal.timeout(5000) });
    if (!r.ok) return;
    const body = await r.json();
    const el = document.getElementById('sb-brand-version');
    if (el && body && body.version) el.textContent = 'v' + body.version;
  } catch {}
})();

// Start
init();
