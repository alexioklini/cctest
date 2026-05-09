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
    const choice = confirm(
      'Memory turned off for this chat.\n\n' +
      'Also delete previously stored memories of this chat from MemPalace?\n\n' +
      'OK  — delete stored memories (cannot be undone)\n' +
      'Cancel — just stop memorising new turns (existing memories stay)'
    );
    purge = !!choice;
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
          if (s) s.summary = newTitle;
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

// ── User Management Modal (admin only) ──
async function openUserManagement() {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  try {
    const data = await API.get('/v1/auth/users');
    const users = data.users || [];
    const meId = state.authUser?.id || '';
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    const roleBadge = (r) => `<span class="role-badge ${esc(r)}">${esc(r)}</span>`;
    const statusDot = (disabled) => `<span title="${disabled?'disabled':'active'}" style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle;background:${disabled?'#dc2626':'#16a34a'}"></span>`;
    modal.innerHTML = `
      <div class="modal-content" style="max-width:920px">
        <div class="modal-header"><h2>User Management</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <div style="font-size:12px;color:var(--text-secondary);flex:1">Self-registration is disabled. Users must be provisioned here.</div>
            <button class="um-btn" onclick="document.querySelector('.modal-overlay').remove();openUserTeams()">Teams &rsaquo;</button>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="text-align:left;border-bottom:2px solid var(--border-light);color:var(--text-secondary);font-size:11px;text-transform:uppercase;letter-spacing:0.04em">
              <th style="padding:8px">Status</th>
              <th style="padding:8px">Username</th>
              <th style="padding:8px">Display Name</th>
              <th style="padding:8px">Role</th>
              <th style="padding:8px">Last Login</th>
              <th style="padding:8px;text-align:right">Actions</th>
            </tr></thead>
            <tbody>
              ${users.map(u => {
                const isSelf = u.id === meId;
                const isDefaultAdmin = u.username === 'admin';
                return `<tr style="border-bottom:1px solid var(--border-light)">
                <td style="padding:8px">${statusDot(u.disabled)}${u.disabled?'<span style="font-size:11px;color:#dc2626">disabled</span>':'<span style="font-size:11px;color:#16a34a">active</span>'}</td>
                <td style="padding:8px"><code>${esc(u.username)}</code>${isSelf?' <span style="font-size:10px;color:var(--text-secondary)">(you)</span>':''}</td>
                <td style="padding:8px">${esc(u.display_name || '')}</td>
                <td style="padding:8px">
                  <select onchange="changeUserRole('${esc(u.id)}', this.value)" ${isSelf?'disabled title="Cannot change your own role"':''} style="font-size:12px;padding:2px 6px;border-radius:4px;border:1px solid var(--border-light)">
                    ${['admin','poweruser','user'].map(r => `<option value="${r}" ${u.role===r?'selected':''}>${r}</option>`).join('')}
                  </select>
                </td>
                <td style="padding:8px;color:var(--text-secondary);font-size:12px">${u.last_login ? new Date(u.last_login*1000).toLocaleString() : 'Never'}</td>
                <td style="padding:8px;text-align:right;white-space:nowrap">
                  <button class="um-btn" onclick="openUserPermissions('${esc(u.id)}','${esc(u.username)}')" title="Permissions">&#9881;</button>
                  <button class="um-btn" onclick="adminResetPassword('${esc(u.id)}','${esc(u.username)}')" title="Reset password">&#128273;</button>
                  ${isSelf ? '' : (u.disabled
                    ? `<button class="um-btn" onclick="setUserDisabled('${esc(u.id)}', false)" title="Enable user" style="color:#16a34a">&#9654;</button>`
                    : `<button class="um-btn" onclick="setUserDisabled('${esc(u.id)}', true)" title="Disable user" style="color:#b45309">&#9209;</button>`)}
                  ${(!isSelf && !isDefaultAdmin) ? `<button class="um-btn" onclick="deleteUser('${esc(u.id)}','${esc(u.username)}')" title="Delete user" style="color:#dc2626">&#128465;</button>` : ''}
                </td>
              </tr>`;
              }).join('')}
            </tbody>
          </table>
          <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border-light)">
            <h3 style="font-size:14px;margin:0 0 12px">Add User</h3>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <input id="new-user-name" placeholder="Username" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-display" placeholder="Display name (optional)" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-pass" type="password" placeholder="Password (min 6)" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <select id="new-user-role" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
                <option value="user">user</option><option value="poweruser">poweruser (team lead)</option><option value="admin">admin</option>
              </select>
              <button onclick="addUser()" class="auth-btn" style="width:auto;padding:8px 20px">Add</button>
            </div>
            <div style="font-size:11px;color:var(--text-secondary);margin-top:8px">
              <b>user</b> — chat-only within granted agents/models.
              <b>poweruser</b> — team lead, can create/manage their own teams.
              <b>admin</b> — full config access.
            </div>
          </div>
        </div>
      </div>
    `;
    if (!document.getElementById('um-btn-style')) {
      const st = document.createElement('style');
      st.id = 'um-btn-style';
      st.textContent = `.um-btn{background:none;border:1px solid var(--border-light);border-radius:6px;padding:3px 8px;margin-left:4px;cursor:pointer;font-size:13px;line-height:1}.um-btn:hover{background:var(--bg-200)}`;
      document.head.appendChild(st);
    }
    document.body.appendChild(modal);
  } catch(e) { console.error('Failed to load users:', e); alert('Failed to load users: ' + e.message); }
}

async function changeUserRole(userId, role) {
  try {
    const r = await API.post('/v1/auth/users', {action:'update', user_id: userId, updates:{role}});
    if (r && r.error) { alert(r.error); return; }
    showToast?.('Role updated');
  } catch(e) { alert('Failed to update role: ' + e.message); }
}

async function deleteUser(userId, username) {
  if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return;
  try {
    const r = await API.post('/v1/auth/users', {action:'delete', user_id: userId});
    if (r && r.error) { alert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { alert('Failed to delete user: ' + e.message); }
}

async function adminResetPassword(userId, username) {
  const pw = prompt(`Reset password for "${username}"?\n\nEnter new password (min 6 characters):`);
  if (pw === null) return;
  if (pw.length < 6) { alert('Password must be at least 6 characters'); return; }
  try {
    const r = await API.post('/v1/auth/users', {action:'reset_password', user_id: userId, new_password: pw});
    if (r && r.error) { alert(r.error); return; }
    alert(`Password reset for "${username}". Share the new password with them securely.`);
  } catch(e) { alert('Failed to reset password: ' + e.message); }
}

async function setUserDisabled(userId, disabled) {
  try {
    const r = await API.post('/v1/auth/users', {action: disabled ? 'disable' : 'enable', user_id: userId});
    if (r && r.error) { alert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { alert('Failed: ' + e.message); }
}

async function addUser() {
  const username = document.getElementById('new-user-name')?.value.trim();
  const password = document.getElementById('new-user-pass')?.value;
  const display_name = document.getElementById('new-user-display')?.value.trim();
  const role = document.getElementById('new-user-role')?.value;
  if (!username || !password) { alert('Username and password required'); return; }
  if (password.length < 6) { alert('Password must be at least 6 characters'); return; }
  try {
    const r = await API.post('/v1/auth/users', {action:'create', username, password, role, display_name});
    if (r.error) { alert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { alert('Failed to add user: ' + e.message); }
}

// ── Per-user Permissions Modal (admin only) ──
async function openUserPermissions(userId, username) {
  try {
    const [permData, agentsData, modelsData, userData] = await Promise.all([
      API.get('/v1/auth/permissions?user_id=' + encodeURIComponent(userId)),
      API.get('/v1/agents'),
      API.get('/v1/models'),
      API.get('/v1/auth/users').catch(() => ({users: []})),
    ]);
    const allAgents = (() => {
      const a = agentsData.agents;
      if (Array.isArray(a)) return a.map(x => x.id || x.name).filter(Boolean);
      if (a && typeof a === 'object') return Object.keys(a);
      return [];
    })().sort();
    const allModels = (() => {
      const m = modelsData.models;
      if (Array.isArray(m)) return m.map(x => typeof x === 'string' ? x : (x.id || x.name || x.model)).filter(Boolean);
      if (m && typeof m === 'object') return Object.keys(m);
      return [];
    })().sort();
    const target = (userData.users || []).find(u => u.id === userId) || {username, role: 'user'};
    const isAdmin = target.role === 'admin';
    const grants = permData.grants || {agents_direct: [], models_direct: [], agents_via_team: [], models_via_team: []};
    const directAgents = new Set(grants.agents_direct || []);
    const directModels = new Set(grants.models_direct || []);
    const teamAgents = {}; for (const g of (grants.agents_via_team || [])) teamAgents[g.agent_id] = (teamAgents[g.agent_id]||[]).concat(g.team_name);
    const teamModels = {}; for (const g of (grants.models_via_team || [])) teamModels[g.model_id] = (teamModels[g.model_id]||[]).concat(g.team_name);

    // Close the current user-management modal first
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.onclick = e => { if (e.target === modal) { modal.remove(); openUserManagement(); } };
    const row = (id, checked, kind, viaTeam) => `<div style="display:flex;align-items:center;gap:8px;padding:6px 4px;border-bottom:1px solid var(--border-light);font-size:13px">
      <label style="display:flex;align-items:center;gap:8px;flex:1;cursor:${isAdmin?'not-allowed':'pointer'};${isAdmin?'opacity:0.5':''}">
        <input type="checkbox" ${checked?'checked':''} ${isAdmin?'disabled':''} onchange="_togglePermission('${esc(userId)}', this.dataset.kind, this.dataset.id, this.checked, this)" data-kind="${esc(kind)}" data-id="${esc(id)}">
        <code style="font-size:12px">${esc(id)}</code>
      </label>
      ${viaTeam && viaTeam.length ? `<span style="font-size:10px;padding:2px 6px;background:var(--bg-base);border-radius:10px;color:var(--text-secondary)">via team: ${esc(viaTeam.join(', '))}</span>` : ''}
    </div>`;
    modal.innerHTML = `
      <div class="modal-content" style="max-width:780px">
        <div class="modal-header">
          <h2>Permissions: ${esc(username)}${isAdmin?' (admin — full access)':''}</h2>
          <button class="modal-close" onclick="document.querySelector('.modal-overlay').remove();openUserManagement()">&times;</button>
        </div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          <div style="font-size:12px;color:var(--text-secondary);margin-bottom:16px">
            ${isAdmin
              ? 'Admins always have access to all agents, models, and features. Settings below are stored but not enforced for admins.'
              : 'Grants control which agents/models this user can chat with. Team memberships add to direct grants. Capabilities toggle feature access.'}
          </div>
          <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Capabilities</h3>
          <div id="user-caps-row" style="display:flex;flex-wrap:wrap;gap:12px;padding:10px 12px;border:1px solid var(--border-light);border-radius:8px;margin-bottom:16px">
            ${(() => {
              const caps = (target.capabilities || {});
              const keys = ['allow_projects','allow_artifacts','allow_workflows','allow_skills_install'];
              const labels = {allow_projects:'Projects', allow_artifacts:'Artifacts', allow_workflows:'Workflows', allow_skills_install:'Install skills'};
              return keys.map(k => `<label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:${isAdmin?'not-allowed':'pointer'};${isAdmin?'opacity:0.5':''}">
                <input type="checkbox" ${caps[k]?'checked':''} ${isAdmin?'disabled':''} data-cap="${k}" onchange="_toggleCapability('${esc(userId)}', this)">
                <span>${labels[k]}</span>
              </label>`).join('');
            })()}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
            <div>
              <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Agents (${allAgents.length})</h3>
              ${allAgents.map(a => row(a, directAgents.has(a), 'agent', teamAgents[a])).join('') || '<div style="color:var(--text-secondary);font-size:12px">No agents available.</div>'}
            </div>
            <div>
              <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Models (${allModels.length})</h3>
              ${allModels.map(m => row(m, directModels.has(m), 'model', teamModels[m])).join('') || '<div style="color:var(--text-secondary);font-size:12px">No models enabled.</div>'}
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  } catch(e) { alert('Failed to load permissions: ' + e.message); }
}

async function _togglePermission(userId, kind, id, checked, el) {
  try {
    const payload = {action: checked ? 'grant' : 'revoke', kind, user_id: userId};
    if (kind === 'agent') payload.agent_id = id; else payload.model_id = id;
    const r = await API.post('/v1/auth/permissions', payload);
    if (r && r.error) { el.checked = !checked; alert(r.error); return; }
  } catch(e) { el.checked = !checked; alert('Failed: ' + e.message); }
}

async function _toggleCapability(userId, el) {
  // Read all capability checkboxes in the row, send a full update
  const checkboxes = document.querySelectorAll('#user-caps-row input[type=checkbox][data-cap]');
  const caps = {};
  for (const cb of checkboxes) caps[cb.dataset.cap] = cb.checked;
  try {
    const r = await API.post('/v1/auth/users', {action:'update', user_id: userId, updates:{capabilities: caps}});
    if (r && r.error) { el.checked = !el.checked; alert(r.error); return; }
  } catch(e) { el.checked = !el.checked; alert('Failed: ' + e.message); }
}

// ── User Teams Modal ──
async function openUserTeams() {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  try {
    const teamsData = await API.get('/v1/user-teams');
    const teams = teamsData.teams || [];
    const isAdmin = state.authUser?.role === 'admin';
    const usersData = isAdmin ? await API.get('/v1/auth/users').catch(() => ({users:[]})) : {users:[]};
    const allUsers = usersData.users || [];
    // Team heads: those with poweruser or admin role (users already have the role listed)
    const headCandidates = allUsers.filter(u => u.role === 'poweruser' || u.role === 'admin');

    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    const teamCard = (t) => {
      const memberIds = new Set((t.members||[]).map(m => m.id));
      const addableUsers = allUsers.filter(u => !memberIds.has(u.id) && !u.disabled);
      return `<div style="border:1px solid var(--border-light);border-radius:10px;padding:16px;margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div style="flex:1"><strong>${esc(t.name)}</strong> ${t.description ? `<span style="font-size:12px;color:var(--text-secondary);margin-left:8px">${esc(t.description)}</span>` : ''}</div>
          <button onclick="dissolveUserTeam('${esc(t.id)}')" class="um-btn" style="color:#dc2626" title="Dissolve team">Dissolve</button>
        </div>
        <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:4px">
          ${(t.members||[]).map(m => `
            <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:var(--bg-base);border:1px solid var(--border-light);border-radius:12px;font-size:12px">
              ${esc(m.display_name || m.username)}
              ${m.id === t.head_user_id ? '<span class="role-badge poweruser" style="font-size:10px;margin-left:2px">head</span>'
                : `<button onclick="removeUserTeamMember('${esc(t.id)}','${esc(m.id)}')" style="background:none;border:none;cursor:pointer;color:#999;font-size:14px;padding:0;line-height:1" title="Remove">&times;</button>`}
            </span>
          `).join('')}
        </div>
        ${addableUsers.length ? `<div style="display:flex;gap:6px;margin-top:10px;align-items:center">
          <select id="ut-add-${esc(t.id)}" style="flex:1;font-size:12px;padding:6px;border:1px solid var(--border-light);border-radius:6px">
            <option value="">Add member...</option>
            ${addableUsers.map(u => `<option value="${esc(u.id)}">${esc(u.display_name || u.username)} (${esc(u.role)})</option>`).join('')}
          </select>
          <button class="um-btn" onclick="addUserTeamMember('${esc(t.id)}')">Add</button>
        </div>` : ''}
      </div>`;
    };
    modal.innerHTML = `
      <div class="modal-content" style="max-width:720px">
        <div class="modal-header"><h2>User Teams</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          ${isAdmin ? `<div style="display:flex;justify-content:flex-end;margin-bottom:12px">
            <button class="um-btn" onclick="document.querySelector('.modal-overlay').remove();openUserManagement()">&lsaquo; Users</button>
          </div>` : ''}
          ${teams.length
            ? teams.map(teamCard).join('')
            : '<p style="color:var(--text-secondary);text-align:center;padding:16px">No user teams yet</p>'}
          <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border-light)">
            <h3 style="font-size:14px;margin:0 0 12px">Create Team</h3>
            <div style="display:flex;flex-direction:column;gap:8px">
              <input id="new-user-team-name" placeholder="Team name" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-team-desc" placeholder="Description (optional)" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              ${isAdmin && headCandidates.length ? `
                <select id="new-user-team-head" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
                  <option value="">Team head... (defaults to you)</option>
                  ${headCandidates.map(u => `<option value="${esc(u.id)}">${esc(u.display_name || u.username)} (${esc(u.role)})</option>`).join('')}
                </select>` : ''}
              <button onclick="createUserTeam()" class="auth-btn" style="width:auto;padding:8px 20px">Create Team</button>
              <div style="font-size:11px;color:var(--text-secondary)">Team head must be a poweruser or admin.</div>
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  } catch(e) { console.error('Failed to load teams:', e); alert('Failed to load teams: ' + e.message); }
}

async function createUserTeam() {
  const name = document.getElementById('new-user-team-name')?.value.trim();
  const desc = document.getElementById('new-user-team-desc')?.value.trim();
  const head = document.getElementById('new-user-team-head')?.value;
  if (!name) { alert('Team name required'); return; }
  try {
    const body = {action:'create', name, description: desc};
    if (head) body.head_user_id = head;
    const r = await API.post('/v1/user-teams', body);
    if (r.error) { alert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { alert('Failed to create team: ' + e.message); }
}

async function dissolveUserTeam(teamId) {
  if (!confirm('Dissolve this team? Members will be detached.')) return;
  try {
    await API.post('/v1/user-teams', {action:'dissolve', team_id: teamId});
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { alert('Failed to dissolve team: ' + e.message); }
}

async function removeUserTeamMember(teamId, userId) {
  try {
    const r = await API.post('/v1/user-teams', {action:'remove_member', team_id: teamId, user_id: userId});
    if (r && r.error) { alert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { alert('Failed to remove member: ' + e.message); }
}

async function addUserTeamMember(teamId) {
  const sel = document.getElementById('ut-add-' + teamId);
  const uid = sel?.value;
  if (!uid) { alert('Select a user to add'); return; }
  try {
    const r = await API.post('/v1/user-teams', {action:'add_member', team_id: teamId, user_id: uid});
    if (r && r.error) { alert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { alert('Failed to add member: ' + e.message); }
}

// ── Change Password Modal ──
function openChangePassword() {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  modal.innerHTML = `
    <div class="modal-content" style="max-width:400px">
      <div class="modal-header"><h2>Change Password</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
      <div class="modal-body">
        <input id="cp-old" type="password" placeholder="Current password" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <input id="cp-new" type="password" placeholder="New password" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <input id="cp-new2" type="password" placeholder="Confirm new password" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:16px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <button onclick="doChangePassword()" class="auth-btn">Change Password</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

async function doChangePassword() {
  const old_password = document.getElementById('cp-old')?.value;
  const new_password = document.getElementById('cp-new')?.value;
  const new_password2 = document.getElementById('cp-new2')?.value;
  if (!old_password || !new_password) { alert('All fields required'); return; }
  if (new_password !== new_password2) { alert('Passwords do not match'); return; }
  try {
    const r = await API.post('/v1/auth/password', {old_password, new_password});
    if (r.error) { alert(r.error); return; }
    alert('Password changed successfully');
    document.querySelector('.modal-overlay')?.remove();
  } catch(e) { alert('Failed: ' + e.message); }
}

// ═══ User Settings Modal ═══
// Distinct from openGeneralSettings (admin-style global config). Tabs:
// Profile | Memory | Schedules | Security. Personal state only — anything
// org-wide stays in the General Settings modal.
async function openUserSettings(initialTab) {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  modal.innerHTML = `
    <div class="modal-content" style="max-width:780px;width:90vw;height:80vh;display:flex;flex-direction:column">
      <div class="modal-header">
        <h2 style="display:flex;align-items:center;gap:8px">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/>
          </svg>
          Account Settings
        </h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div style="display:flex;flex:1;overflow:hidden">
        <div class="modal-tabs modal-tabs-vertical" id="user-settings-tabs" style="width:160px;flex-shrink:0">
          <div class="modal-tab" data-tab="profile" onclick="switchUserSettingsTab('profile', this)">Profile</div>
          <div class="modal-tab" data-tab="memory" onclick="switchUserSettingsTab('memory', this)">Memory</div>
          <div class="modal-tab" data-tab="schedules" onclick="switchUserSettingsTab('schedules', this)">My Schedules</div>
          <div class="modal-tab" data-tab="security" onclick="switchUserSettingsTab('security', this)">Security</div>
        </div>
        <div class="modal-body" id="user-settings-body" style="flex:1;overflow:auto;padding:20px">
          <div style="color:var(--text-400)">Loading…</div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  // Pull a fresh /v1/auth/me so the modal reflects server state, not stale state.authUser
  try {
    const r = await API.get('/v1/auth/me');
    if (r && r.user) state.authUser = r.user;
  } catch(e) {}
  const startTab = initialTab || 'profile';
  const startEl = modal.querySelector(`.modal-tab[data-tab="${startTab}"]`);
  switchUserSettingsTab(startTab, startEl);
}

function switchUserSettingsTab(tab, el) {
  document.querySelectorAll('#user-settings-tabs .modal-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  const body = document.getElementById('user-settings-body');
  if (!body) return;
  if (tab === 'profile') return renderUserSettingsProfile(body);
  if (tab === 'memory') return renderUserSettingsMemory(body);
  if (tab === 'schedules') return renderUserSettingsSchedules(body);
  if (tab === 'security') return renderUserSettingsSecurity(body);
}

function _us_input(label, id, val, type = 'text', help = '') {
  return `
    <div style="margin-bottom:14px">
      <label style="display:block;font-size:12px;font-weight:500;color:var(--text-200);margin-bottom:4px">${esc(label)}</label>
      <input id="${id}" type="${type}" value="${esc(val ?? '')}"
        style="display:block;width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px;background:var(--bg-100);color:var(--text-100)">
      ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:4px">${esc(help)}</div>` : ''}
    </div>`;
}

function _us_textarea(label, id, val, help = '', placeholder = '', opts = {}) {
  const rows = opts.rows || 3;
  const maxlength = opts.maxlength || 500;
  const minHeight = opts.minHeight ? `min-height:${opts.minHeight};` : '';
  // Optional inline AI polish button — same /v1/refine endpoint as the chat
  // composer, but with purpose=profile_field so the system prompt switches
  // to "polish, don't rewrite as a question". Disabled visually (no button)
  // when opts.refinable is falsy. Caveman is NOT a refine-time setting:
  // the chat composer's caveman toggle is the single source of truth and
  // applies to the outgoing message, not to the polish step.
  const refineBtn = opts.refinable ? `
    <button type="button" id="${id}-refine"
      onclick="refineProfileField('${id}', ${JSON.stringify(opts.fieldLabel || label).replace(/"/g, '&quot;')})"
      title="Polish this with AI"
      style="background:none;border:1px solid var(--border-light);border-radius:4px;padding:2px 8px;font-size:11px;color:var(--text-300);cursor:pointer;display:inline-flex;align-items:center;gap:4px">
      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
        <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
        <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
      </svg>
      <span id="${id}-refine-label">Refine with AI</span>
    </button>` : '';
  return `
    <div style="margin-bottom:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;gap:8px">
        <label for="${id}" style="font-size:12px;font-weight:500;color:var(--text-200)">${esc(label)}</label>
        ${refineBtn}
      </div>
      <textarea id="${id}" rows="${rows}" maxlength="${maxlength}" placeholder="${esc(placeholder)}"
        style="display:block;width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px;background:var(--bg-100);color:var(--text-100);font-family:inherit;resize:vertical;${minHeight}">${esc(val ?? '')}</textarea>
      ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:4px">${esc(help)}</div>` : ''}
    </div>`;
}

// Inline AI refinement control for the long-form user-profile editor in
// the Memory tab. Single Refine-with-AI button — no caveman picker:
// the on-disk profile is always clean prose; the chat composer's caveman
// toggle compresses it dynamically at injection time.
function _userProfileRefineControls(textareaId) {
  return `
    <button type="button" id="${textareaId}-refine"
      onclick="refineProfileField('${textareaId}', 'Your profile (long-form Markdown with ## Heading sections; keep third-person voice and section headings intact)')"
      title="Polish your profile with AI"
      class="btn-secondary"
      style="font-size:11px;padding:4px 10px;display:inline-flex;align-items:center;gap:4px">
      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
        <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
        <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
      </svg>
      <span id="${textareaId}-refine-label">Refine with AI</span>
    </button>`;
}

async function refineProfileField(textareaId, fieldLabel) {
  const ta = document.getElementById(textareaId);
  if (!ta) return;
  const text = (ta.value || '').replace(/^\s+|\s+$/g, '');
  if (!text) { showToast('Type something first', true); return; }
  const btn = document.getElementById(textareaId + '-refine');
  const lbl = document.getElementById(textareaId + '-refine-label');
  const origLabel = lbl?.textContent || 'Refine with AI';
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Refining…';
  ta.disabled = true;
  // Stash original so the user can undo a bad refinement
  const original = ta.value;
  try {
    // No caveman on profile-field refinement — the on-disk profile stays
    // clean prose, and the chat composer's caveman toggle compresses it
    // dynamically at injection time. Refining with caveman would also
    // double-apply at chat time.
    const result = await API.post('/v1/refine', {
      text,
      purpose: 'profile_field',
      field_label: fieldLabel || '',
    });
    if (result && result.refined && result.refined !== text) {
      ta.value = result.refined;
      // Offer one-click undo via the button label until the user clicks elsewhere
      if (lbl) lbl.textContent = 'Undo';
      if (btn) {
        btn.disabled = false;
        const undoHandler = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          ta.value = original;
          if (lbl) lbl.textContent = origLabel;
          btn.removeEventListener('click', undoHandler);
          // Restore the normal refine handler on the next click
          btn.onclick = () => refineProfileField(textareaId, fieldLabel);
        };
        btn.onclick = undoHandler;
      }
      showToast('Refined — click Undo to revert');
    } else {
      showToast('Already clean — no change');
      if (lbl) lbl.textContent = origLabel;
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('Refine failed: ' + (e.message || e), true);
    if (lbl) lbl.textContent = origLabel;
    if (btn) btn.disabled = false;
  } finally {
    ta.disabled = false;
  }
}

function renderUserSettingsProfile(body) {
  const u = state.authUser || {};
  const prefs = u.preferences || {};
  body.innerHTML = `
    <div style="max-width:520px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Profile</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        How the agent identifies and addresses you. Changes save on click.
      </div>
      <div style="font-size:11px;color:var(--text-400);margin-bottom:14px">
        <span style="font-weight:500;color:var(--text-300)">Username:</span> ${esc(u.username || '')}
        &nbsp;·&nbsp;
        <span style="font-weight:500;color:var(--text-300)">Role:</span> ${esc(u.role || 'user')}
      </div>
      ${_us_input('Full name (display)', 'us-display-name', u.display_name, 'text', 'Shown in the sidebar and admin lists.')}
      ${_us_input('Greeting name', 'us-greeting-name', prefs.greeting_name, 'text', 'What the agent should call you in conversation. Falls back to full name.')}
      ${_us_input('Email', 'us-email', u.email, 'email', 'Used for notifications. Not shared.')}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">About you</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Surfaced to the agent on the first turn of each chat so it has context about who you are and how you prefer to be talked to. Keep each field to a sentence or two.
      </div>
      ${_us_textarea('What describes your job in a sentence?', 'us-job-description', prefs.job_description,
        'e.g. "Backend engineer working on payments infrastructure" or "PhD student in computational biology". Max 500 chars.',
        'I\'m a …',
        {refinable: true, fieldLabel: 'Job description'})}
      ${_us_textarea('What are your communication preferences?', 'us-comm-prefs', prefs.communication_preferences,
        'Like soul.md but for you — tone, style, formatting, what to avoid, recurring context the agent should always know. Up to ~4000 characters.',
        'I prefer direct, technical answers. Skip the preamble. Use code blocks for code, markdown sparingly. Don\'t hedge with "it depends" — pick one and explain.\n\nWhen I ask about architecture, default to discussing tradeoffs. When I ask for code, default to small focused diffs.',
        {rows: 12, maxlength: 4000, minHeight: '220px', refinable: true, fieldLabel: 'Communication preferences'})}
      <div style="display:flex;gap:8px;margin-top:18px">
        <button class="btn-primary" onclick="saveUserSettingsProfile()">Save profile</button>
        <span id="us-profile-msg" style="align-self:center;font-size:12px;color:var(--text-400)"></span>
      </div>
    </div>`;
}

async function saveUserSettingsProfile() {
  const display_name = document.getElementById('us-display-name')?.value?.trim() || '';
  const email = document.getElementById('us-email')?.value?.trim() || '';
  const greeting_name = document.getElementById('us-greeting-name')?.value?.trim() || '';
  const job_description = document.getElementById('us-job-description')?.value?.trim() || '';
  // Communication prefs: only `.trim()` whitespace at the ends. Internal
  // newlines are kept as-is so users can structure their soul.md-style block.
  const communication_preferences = (document.getElementById('us-comm-prefs')?.value || '')
    .replace(/^\s+|\s+$/g, '');
  const msg = document.getElementById('us-profile-msg');
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r1 = await API.post('/v1/auth/profile', {display_name, email});
    if (r1.error) throw new Error(r1.error);
    const r2 = await API.post('/v1/auth/preferences', {preferences: {
      greeting_name, job_description, communication_preferences,
    }});
    if (r2.error) throw new Error(r2.error);
    if (r2.user) state.authUser = r2.user;
    renderUserMenu();
    if (msg) { msg.textContent = 'Saved.'; msg.style.color = 'var(--success, #16a34a)'; }
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error, #dc2626)'; }
  }
}

function _us_select(label, id, val, options, help = '') {
  const opts = options.map(o =>
    `<option value="${esc(o.value)}" ${String(o.value) === String(val ?? '') ? 'selected' : ''}>${esc(o.label)}</option>`
  ).join('');
  return `
    <div style="margin-bottom:14px">
      <label style="display:block;font-size:12px;font-weight:500;color:var(--text-200);margin-bottom:4px">${esc(label)}</label>
      <select id="${id}" style="display:block;width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px;background:var(--bg-100);color:var(--text-100)">${opts}</select>
      ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:4px">${esc(help)}</div>` : ''}
    </div>`;
}

function _us_checkbox(label, id, checked, help = '') {
  return `
    <label style="display:flex;align-items:flex-start;gap:8px;margin-bottom:14px;cursor:pointer">
      <input id="${id}" type="checkbox" ${checked ? 'checked' : ''} style="margin-top:2px">
      <div>
        <div style="font-size:13px;color:var(--text-100)">${esc(label)}</div>
        ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:2px">${esc(help)}</div>` : ''}
      </div>
    </label>`;
}

function renderUserSettingsMemory(body) {
  const prefs = (state.authUser || {}).preferences || {};
  // null sentinel for "use server default" — the server treats it as
  // memory_chats_default unset, falling through to chat_sync.classifier.default_mode.
  const memOpts = [
    {value: '', label: 'Use server default'},
    {value: '0', label: 'Off — never save chats to memory'},
    {value: '1', label: 'On — save every chat to memory'},
    {value: '2', label: 'Auto — classifier decides per turn'},
  ];
  const schedOpts = [
    {value: '', label: 'Use server default (file artifacts)'},
    {value: '0', label: 'Off — skip scheduled-run artifacts'},
    {value: '1', label: 'On — file scheduled-run artifacts'},
  ];
  const hourOpts = Array.from({length: 24}, (_, i) => ({
    value: String(i), label: `${String(i).padStart(2, '0')}:00 local`,
  }));
  body.innerHTML = `
    <div style="max-width:520px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Memory defaults</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        Controls how your activity is filed to MemPalace. The per-chat toggle in the composer always overrides these defaults.
      </div>
      ${_us_select('Default for new chats', 'us-mem-chats',
        prefs.memory_chats_default == null ? '' : String(prefs.memory_chats_default),
        memOpts,
        'Each new chat starts with this memory mode. You can still flip it per-chat.')}
      ${_us_select('Scheduled-run artifacts', 'us-mem-sched',
        prefs.memory_sched_default == null ? '' : String(prefs.memory_sched_default),
        schedOpts,
        'Whether the miner files artifacts produced by your scheduled tasks. Off keeps them on disk only.')}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">Memory from chat history</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Brain reads your chats and maintains a single profile describing your work, interests, and what's currently on your mind. The agent loads this on the first turn of every chat.
      </div>
      ${_us_checkbox('Maintain user profile from chat history', 'us-daily-enabled', !!prefs.daily_summary_enabled,
        "Runs once per day around the chosen hour. The profile lives at agents/main/user_profiles/&lt;you&gt;.md and mirrors per-section drawers into MemPalace. The on-disk file is always clean prose; the chat's caveman mode compresses it on the fly when injected as context.")}
      ${_us_select('Update at', 'us-daily-hour', String(prefs.daily_summary_hour_local ?? 6), hourOpts)}
      <div style="display:flex;gap:8px;margin-top:14px;align-items:center">
        <button class="btn-primary" onclick="saveUserSettingsMemory()">Save memory settings</button>
        <span id="us-mem-msg" style="align-self:center;font-size:12px;color:var(--text-400)"></span>
      </div>

      <div id="us-profile-doc-section" style="margin-top:20px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;flex-wrap:wrap">
          <label for="us-profile-doc" style="font-size:13px;font-weight:500;color:var(--text-200)">Your profile</label>
          <div style="display:flex;gap:6px;align-items:center">
            ${_userProfileRefineControls('us-profile-doc')}
            <button id="us-profile-update-btn" class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="userProfileUpdateNow()">Update now</button>
            <button id="us-profile-reset-btn" class="btn-secondary" style="font-size:11px;padding:4px 10px;color:var(--error,#dc2626)" onclick="userProfileReset()">Reset</button>
          </div>
        </div>
        <textarea id="us-profile-doc" rows="20"
          placeholder="(Profile not yet generated. Enable the toggle above and click Update now, or wait for the daily run.)"
          style="display:block;width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--border-light);border-radius:6px;font-size:12px;background:var(--bg-100);color:var(--text-100);font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;resize:vertical;min-height:380px;line-height:1.5"></textarea>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px">
          <div id="us-profile-meta" style="font-size:11px;color:var(--text-400)"></div>
          <div style="display:flex;gap:6px;align-items:center">
            <span id="us-profile-doc-msg" style="font-size:11px;color:var(--text-400)"></span>
            <button class="btn-primary" style="padding:4px 12px;font-size:12px" onclick="userProfileSave()">Save profile</button>
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:6px">
          Editable Markdown. Sections must use <code>## Heading</code>. The next daily run will edit your changes in place rather than overwrite them.
        </div>
      </div>
    </div>`;
  // Lazy-load the profile content after the DOM is in place.
  loadUserProfileDoc();
}

async function loadUserProfileDoc() {
  const ta = document.getElementById('us-profile-doc');
  const meta = document.getElementById('us-profile-meta');
  if (!ta) return;
  try {
    const r = await API.get('/v1/auth/profile-doc');
    ta.value = r.content || '';
    if (meta) {
      const cur = r.cursor || {};
      const ts = cur.last_run_ts ? new Date(cur.last_run_ts * 1000).toLocaleString() : '';
      const status = cur.last_status || '';
      const bytes = r.bytes || 0;
      const parts = [];
      if (bytes) parts.push(`${bytes} bytes`);
      if (ts) parts.push(`last run ${ts}${status ? ` · ${status}` : ''}`);
      else if (status) parts.push(status);
      meta.textContent = parts.join(' · ') || (r.exists ? '' : 'Not yet generated');
    }
  } catch (e) {
    if (meta) meta.textContent = 'Failed to load: ' + (e.message || e);
  }
}

async function userProfileSave() {
  const ta = document.getElementById('us-profile-doc');
  const msg = document.getElementById('us-profile-doc-msg');
  if (!ta) return;
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc', {content: ta.value});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = `Saved (${r.bytes} bytes)`; msg.style.color = 'var(--success,#16a34a)'; }
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}

async function userProfileUpdateNow() {
  const btn = document.getElementById('us-profile-update-btn');
  const msg = document.getElementById('us-profile-doc-msg');
  const origLabel = btn?.textContent || 'Update now';
  if (btn) { btn.disabled = true; btn.textContent = 'Updating…'; }
  if (msg) { msg.textContent = 'Generating from chat history (5–60s)…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc/update-now', {});
    if (r.error) throw new Error(r.error);
    const result = r.result || {};
    if (result.status === 'no_activity') {
      if (msg) { msg.textContent = 'No chat activity to summarize yet.'; msg.style.color = 'var(--text-400)'; }
    } else if (result.status === 'error') {
      throw new Error(result.error || 'Generation failed');
    } else {
      if (msg) { msg.textContent = `Updated (${result.bytes} bytes, ${result.samples} chats)`; msg.style.color = 'var(--success,#16a34a)'; }
    }
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origLabel; }
  }
}

async function userProfileReset() {
  if (!confirm('Reset your profile?\n\nDeletes the file + MemPalace drawers. The next daily run (or "Update now") will rebuild from scratch from your last 90 days of chats.\n\nProfile history is kept on disk for rollback.')) {
    return;
  }
  const msg = document.getElementById('us-profile-doc-msg');
  if (msg) { msg.textContent = 'Resetting…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc/reset', {});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = 'Reset.'; msg.style.color = 'var(--success,#16a34a)'; }
    const ta = document.getElementById('us-profile-doc');
    if (ta) ta.value = '';
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}

async function saveUserSettingsMemory() {
  const memChats = document.getElementById('us-mem-chats')?.value;
  const memSched = document.getElementById('us-mem-sched')?.value;
  const dailyEnabled = !!document.getElementById('us-daily-enabled')?.checked;
  const dailyHour = parseInt(document.getElementById('us-daily-hour')?.value || '6', 10);
  const msg = document.getElementById('us-mem-msg');
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
  const prefs = {
    memory_chats_default: memChats === '' ? null : parseInt(memChats, 10),
    memory_sched_default: memSched === '' ? null : parseInt(memSched, 10),
    daily_summary_enabled: dailyEnabled,
    daily_summary_hour_local: dailyHour,
  };
  try {
    const r = await API.post('/v1/auth/preferences', {preferences: prefs});
    if (r.error) throw new Error(r.error);
    if (r.user) state.authUser = r.user;
    if (msg) { msg.textContent = 'Saved.'; msg.style.color = 'var(--success, #16a34a)'; }
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error, #dc2626)'; }
  }
}

async function renderUserSettingsSchedules(body) {
  body.innerHTML = `<div style="color:var(--text-400)">Loading your scheduled tasks…</div>`;
  try {
    const r = await API.get('/v1/schedule');
    const schedules = (r && r.schedules) || [];
    if (!schedules.length) {
      body.innerHTML = `
        <div style="max-width:520px">
          <h3 style="margin:0 0 4px 0;font-size:15px">My scheduled tasks</h3>
          <div style="font-size:12px;color:var(--text-400);margin-bottom:14px">
            You don't own any scheduled tasks yet. Create one from the Scheduled view.
          </div>
          <button class="btn-secondary" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('scheduled')">Open Scheduled view</button>
        </div>`;
      return;
    }
    const rows = schedules.map(s => {
      const enabled = !!s.enabled;
      const nextRun = s.next_run ? new Date(s.next_run).toLocaleString() : '—';
      return `
        <tr>
          <td style="padding:8px 6px;font-weight:500">${esc(s.name || '')}</td>
          <td style="padding:8px 6px;font-size:12px;color:var(--text-300)">${esc(s.schedule || '')}</td>
          <td style="padding:8px 6px;font-size:12px;color:var(--text-300)">${esc(nextRun)}</td>
          <td style="padding:8px 6px;font-size:12px;color:${enabled ? 'var(--success,#16a34a)' : 'var(--text-400)'}">
            ${enabled ? (s.is_running ? 'running' : 'enabled') : 'paused'}
          </td>
        </tr>`;
    }).join('');
    body.innerHTML = `
      <div>
        <h3 style="margin:0 0 4px 0;font-size:15px">My scheduled tasks (${schedules.length})</h3>
        <div style="font-size:12px;color:var(--text-400);margin-bottom:14px">
          Tasks you own. Edit, pause, or delete them from the Scheduled view.
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="text-align:left;border-bottom:1px solid var(--border-light)">
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Name</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Schedule</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Next run</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">State</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="margin-top:14px">
          <button class="btn-secondary" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('scheduled')">Open Scheduled view</button>
        </div>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div style="color:var(--error,#dc2626)">Failed to load schedules: ${esc(e.message || e)}</div>`;
  }
}

function renderUserSettingsSecurity(body) {
  body.innerHTML = `
    <div style="max-width:480px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Change password</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        Pick something at least 6 characters long.
      </div>
      ${_us_input('Current password', 'us-pw-old', '', 'password')}
      ${_us_input('New password', 'us-pw-new', '', 'password')}
      ${_us_input('Confirm new password', 'us-pw-new2', '', 'password')}
      <div style="display:flex;gap:8px;margin-top:6px">
        <button class="btn-primary" onclick="saveUserSettingsPassword()">Change password</button>
        <span id="us-pw-msg" style="align-self:center;font-size:12px;color:var(--text-400)"></span>
      </div>
    </div>`;
}

async function saveUserSettingsPassword() {
  const old_password = document.getElementById('us-pw-old')?.value || '';
  const new_password = document.getElementById('us-pw-new')?.value || '';
  const new2 = document.getElementById('us-pw-new2')?.value || '';
  const msg = document.getElementById('us-pw-msg');
  if (!old_password || !new_password) {
    if (msg) { msg.textContent = 'All fields required'; msg.style.color = 'var(--error,#dc2626)'; }
    return;
  }
  if (new_password !== new2) {
    if (msg) { msg.textContent = 'Passwords do not match'; msg.style.color = 'var(--error,#dc2626)'; }
    return;
  }
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/password', {old_password, new_password});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = 'Password changed.'; msg.style.color = 'var(--success,#16a34a)'; }
    document.getElementById('us-pw-old').value = '';
    document.getElementById('us-pw-new').value = '';
    document.getElementById('us-pw-new2').value = '';
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}

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

// ═══ Connection Health Monitor ═══
const ConnectionMonitor = {
  _interval: null,
  _connected: false,
  _pollMs: 10000,
  _failCount: 0,

  start() {
    this._check();
    this._interval = setInterval(() => this._check(), this._pollMs);
  },

  stop() {
    if (this._interval) { clearInterval(this._interval); this._interval = null; }
  },

  async _check() {
    const dot = document.getElementById('status-connection-dot');
    const wrap = document.getElementById('status-connection');
    if (!dot || !wrap) return;
    try {
      const resp = await fetch(`${BASE_URL}/v1/status`, {
        method: 'GET',
        headers: API._headers(),
        signal: AbortSignal.timeout(5000),
      });
      if (resp.ok) {
        this._setConnected(true);
        this._failCount = 0;
      } else {
        this._setConnected(false);
      }
    } catch {
      this._failCount++;
      if (this._failCount >= 2) this._setConnected(false);
    }
  },

  _setConnected(connected) {
    if (this._connected === connected) return;
    this._connected = connected;
    state.connected = connected;
    const dot = document.getElementById('status-connection-dot');
    const wrap = document.getElementById('status-connection');
    if (!dot || !wrap) return;
    dot.className = 'connection-dot ' + (connected ? 'connected' : 'disconnected');
    wrap.title = connected ? 'Server: connected' : 'Server: disconnected';
    renderUserMenu();
    if (connected && this._failCount === 0) return;
    if (connected) showToast('Reconnected to server');
  },
};

const MempalaceActivityMonitor = {
  _interval: null,
  _pollMs: 2000,
  _storeActive: false,
  _retrieveActive: false,

  start() {
    if (this._interval) return;
    this._check();
    this._interval = setInterval(() => this._check(), this._pollMs);
  },

  stop() {
    if (this._interval) { clearInterval(this._interval); this._interval = null; }
  },

  async _check() {
    const btns = _composerToggleEls('btn-save-to-memory');
    if (!btns.length || !state.connected) return;
    try {
      const resp = await fetch(`${BASE_URL}/v1/mempalace/activity`, {
        method: 'GET',
        headers: API._headers(),
        signal: AbortSignal.timeout(3000),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      const nextStore = !!data.store_active;
      const nextRetrieve = !!data.retrieve_active;
      if (nextStore !== this._storeActive) {
        for (const b of btns) b.classList.toggle('mp-storing', nextStore);
        this._storeActive = nextStore;
      }
      if (nextRetrieve !== this._retrieveActive) {
        for (const b of btns) b.classList.toggle('mp-retrieving', nextRetrieve);
        this._retrieveActive = nextRetrieve;
      }
    } catch {}
  },
};

// Formats a pool-state tooltip fragment like " · pool 2/3 ready" for reuse
// across the Models tab and composer dots.
function poolTooltip(st) {
  if (!st) return '';
  const ready = st.ready ?? 0;
  const target = st.target ?? 0;
  const building = st.building ?? 0;
  if (target <= 0) return '';
  if (ready >= target) return ` · pool ${ready}/${target} ready ✓`;
  if (ready > 0) return ` · pool ${ready}/${target} ready (building ${building})`;
  if (building > 0) return ` · pre-baking ${building} session${building>1?'s':''}…`;
  return '';
}

// Warm-pool status modal — opened from the status bar "Pool" indicator. Live
// updated by WarmupMonitor._render() (polls /v1/warmup/status).
function openWarmPoolModal() {
  if (document.getElementById('warmpool-modal')) return;
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.id = 'warmpool-modal';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="modal-content" style="max-width:680px;max-height:80vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;padding:20px 24px 0;gap:12px">
      <h2 style="margin:0;font-size:18px;font-weight:600;color:var(--text-000)">Warm Session Pool</h2>
      <span id="warmpool-modal-hint" style="font-size:12px;color:var(--text-400)"></span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()" style="margin-left:auto">&times;</button>
    </div>
    <div id="warmpool-body" style="flex:1;overflow-y:auto;padding:12px 24px 24px">
      <div style="color:var(--text-400);padding:24px;text-align:center">Loading...</div>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  renderWarmPoolModalBody();
}

function renderWarmPoolModalBody() {
  const body = document.getElementById('warmpool-body');
  if (!body) return;
  const states = WarmupMonitor.states || {};
  const entries = Object.entries(states).filter(([_, st]) => st.enabled);
  const hint = document.getElementById('warmpool-modal-hint');
  if (hint) hint.textContent = entries.length
    ? `${entries.length} model${entries.length>1?'s':''} with warmup enabled`
    : 'No models have warmup enabled';
  if (!entries.length) {
    body.innerHTML = `<div style="color:var(--text-400);padding:24px;text-align:center">
      Enable <strong>Warmup</strong> on a model in the Models tab to pre-bake sessions for faster first-token latency.
    </div>`;
    return;
  }
  // Short explainer — mode is now per-model (set in Models tab)
  const banner = `<div style="background:var(--bg-200);border-radius:10px;padding:8px 14px;margin-bottom:12px;font-size:11px;color:var(--text-400);line-height:1.5">
    <span style="color:#22c55e;font-weight:500">full</span>: prefills system+tools into KV cache (~5-6s first response).
    <span style="color:#8b5cf6;font-weight:500;margin-left:8px">minimal</span>: weights only (~10-15s first response).
    Configure per model in the Models tab. Full-primed models share GPU memory — if it's tight, they evict each other.
  </div>`;
  entries.sort((a, b) => (a[1].display_name || a[0]).localeCompare(b[1].display_name || b[0]));
  const now = Date.now() / 1000;
  const rows = entries.map(([mid, st]) => {
    const dn = esc(st.display_name || mid);
    const prov = esc(st.provider || '');
    const ready = st.ready ?? 0;
    const target = st.target ?? 0;
    const building = st.building ?? 0;
    const pct = target > 0 ? Math.min(100, Math.round(ready / target * 100)) : 0;
    let stateBadge;
    if (st.state === 'warm') stateBadge = '<span style="color:#22c55e">● warm</span>';
    else if (st.state === 'warming') stateBadge = '<span style="color:#f59e0b">● warming…</span>';
    else if (st.state === 'failed') stateBadge = '<span style="color:#ef4444">● failed</span>';
    else if (st.state === 'skipped_cloud') stateBadge = '<span style="color:var(--text-400)">○ skipped (cloud)</span>';
    else stateBadge = '<span style="color:var(--text-400)">○ idle</span>';
    const age = (() => {
      const t = st.last_warmup_ts || st.last_used_ts || 0;
      if (!t) return 'never warmed';
      const secs = Math.max(0, Math.round(now - t));
      if (secs < 60) return `${secs}s ago`;
      if (secs < 3600) return `${Math.round(secs/60)}m ago`;
      return `${Math.round(secs/3600)}h ago`;
    })();
    const err = st.last_error ? `<div style="margin-top:6px;color:#ef4444;font-family:var(--font-mono);font-size:11px;background:#ef444411;padding:6px 8px;border-radius:6px;white-space:pre-wrap;word-break:break-word">${esc(st.last_error)}</div>` : '';
    const barColor = st.state === 'failed' ? '#ef4444'
                    : (building > 0 ? '#f59e0b'
                    : (ready >= target && target > 0 ? '#22c55e' : 'var(--text-500)'));
    const desired = st.desired_mode || 'full';
    const actual = st.mode || '';
    const chipColor = (m) => m === 'full' ? {bg:'#22c55e22', fg:'#22c55e'} : {bg:'#8b5cf622', fg:'#8b5cf6'};
    const desCol = chipColor(desired);
    let modeChip = `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:${desCol.bg};color:${desCol.fg};font-weight:500">${esc(desired)}</span>`;
    if (actual && actual !== desired) {
      const actCol = chipColor(actual);
      modeChip += `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:${actCol.bg};color:${actCol.fg};font-weight:500" title="Currently primed in ${esc(actual)} mode — keeper will re-prime to ${esc(desired)} on next cycle">${esc(actual)} ⟲</span>`;
    }
    return `<div style="background:var(--bg-200);border-radius:10px;padding:12px 14px;margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="font-weight:600;color:var(--text-000)">${dn}</span>
        ${prov ? `<span style="font-size:11px;color:var(--text-400)">${prov}</span>` : ''}
        ${modeChip}
        <span style="margin-left:auto;font-size:11px">${stateBadge}</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;font-size:12px;color:var(--text-400)">
        <div style="flex:1;height:6px;background:var(--bg-300);border-radius:3px;overflow:hidden">
          <div style="width:${pct}%;height:100%;background:${barColor};transition:width 0.3s"></div>
        </div>
        <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-300)">${ready}/${target}${building ? ` (+${building} building)` : ''}</span>
        <span style="font-size:11px">${age}</span>
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="triggerPoolWarmup('${esc(mid)}')">Warm now</button>
      </div>
      ${err}
    </div>`;
  }).join('');
  body.innerHTML = banner + rows;
}

async function triggerPoolWarmup(modelId) {
  try {
    await fetch(`${BASE_URL}/v1/warmup/trigger`, {
      method: 'POST',
      headers: {'Content-Type':'application/json', ...API._headers()},
      body: JSON.stringify({model: modelId}),
    });
    showToast(`Warming ${modelId}…`);
    WarmupMonitor._mode = 'fast';
    WarmupMonitor._tick();
  } catch (e) { showToast(`Warmup failed: ${e}`, true); }
}

// Polls /v1/warmup/status and pushes state into DOM dots (status bar, composer,
// Models tab rows). Poll faster (1s) when anything is actively warming so the
// user sees the amber → green flip promptly; slow to 5s otherwise.
const WarmupMonitor = {
  _interval: null,
  _fastMs: 1000,
  _slowMs: 5000,
  _mode: 'slow',
  states: {},        // model_id -> state dict from server
  anyWarming: false,

  start() {
    if (this._interval) return;
    this._tick();
    this._schedule();
  },
  stop() {
    if (this._interval) { clearTimeout(this._interval); this._interval = null; }
  },
  _schedule() {
    const ms = this._mode === 'fast' ? this._fastMs : this._slowMs;
    this._interval = setTimeout(() => this._tick(), ms);
  },
  async _tick() {
    if (!state.connected) { this._schedule(); return; }
    try {
      const resp = await fetch(`${BASE_URL}/v1/warmup/status`, {
        method: 'GET', headers: API._headers(),
        signal: AbortSignal.timeout(3000),
      });
      if (resp.ok) {
        const data = await resp.json();
        this.states = data.models || {};
        this.anyWarming = !!data.any_warming;
        this._mode = this.anyWarming ? 'fast' : 'slow';
        this._render();
      }
    } catch {}
    this._schedule();
  },
  _render() {
    // Composer dots — reflect the active session's model
    const activeModel = state.activeChat?.model
      || document.getElementById('model-selector-name')?.dataset?.modelId
      || '';
    this._applyComposerDot('welcome-warmup-dot', activeModel);
    this._applyComposerDot('chat-warmup-dot', activeModel);
    this._applyComposerDot('project-warmup-dot', activeModel);
    // Models tab row dots
    document.querySelectorAll('[data-model-dot]').forEach(el => {
      const mid = el.dataset.modelDot;
      const st = this.states[mid];
      if (!st) { el.style.display = 'none'; el.className = 'mdl-warmup-dot'; return; }
      el.style.display = 'inline-block';
      el.className = 'mdl-warmup-dot warmup-dot ' + st.state;
      const age = st.age_seconds != null ? Math.round(st.age_seconds) + 's ago' : 'never';
      el.title = `Warmup: ${st.state}` + (st.state === 'warm' ? ` · ${age}` : '')
              + poolTooltip(st) + (st.last_error ? ` · ${st.last_error}` : '');
    });
    // Status-bar pool indicator + modal body live-refresh
    this._renderPoolIndicator();
    if (document.getElementById('warmpool-modal')) {
      renderWarmPoolModalBody();
    }
  },
  _renderPoolIndicator() {
    const wrap = document.getElementById('status-warmpool');
    if (!wrap) return;
    // Admins-only — pool is infra detail not relevant to powerusers/users.
    if ((state.authUser?.role || 'admin') !== 'admin') { wrap.style.display = 'none'; return; }
    const entries = Object.entries(this.states);
    const warmupModels = entries.filter(([_, st]) => st.enabled);
    if (!warmupModels.length) { wrap.style.display = 'none'; return; }
    let ready = 0, target = 0, building = 0, failed = 0, anyWarm = false;
    for (const [_, st] of warmupModels) {
      ready += st.ready ?? 0;
      target += st.target ?? 0;
      building += st.building ?? 0;
      if (st.state === 'failed') failed++;
      if (st.state === 'warm') anyWarm = true;
    }
    wrap.style.display = 'flex';
    const dot = document.getElementById('status-warmpool-dot');
    let dotCls = 'idle';
    if (failed && !anyWarm) dotCls = 'failed';
    else if (building > 0 || this.anyWarming) dotCls = 'warming';
    else if (ready > 0) dotCls = 'warm';
    dot.className = 'warmup-dot ' + dotCls;
    document.getElementById('status-warmpool-label').textContent = `${ready}/${target}`;
    wrap.title = `Warm pool: ${ready}/${target} ready`
               + (building ? ` · ${building} building` : '')
               + (failed ? ` · ${failed} failed` : '')
               + ' — click for details';
  },
  _applyComposerDot(id, model) {
    const el = document.getElementById(id);
    if (!el) return;
    const st = this.states[model];
    if (!st) { el.style.display = 'none'; return; }
    el.style.display = 'inline-block';
    el.className = 'warmup-dot ' + st.state;
    const age = st.age_seconds != null ? Math.round(st.age_seconds) + 's ago' : 'never';
    el.title = `Model warmup: ${st.state}`
            + (st.state === 'warm' ? ` · prefilled ${age}` : '')
            + poolTooltip(st);
  },
  // Called by the Models tab "Warm now" button (if added later)
  async triggerManual(modelId) {
    try {
      await fetch(`${BASE_URL}/v1/warmup/trigger`, {
        method: 'POST', headers: {'Content-Type':'application/json', ...API._headers()},
        body: JSON.stringify({model: modelId}),
      });
      this._mode = 'fast';
      this._tick();
    } catch {}
  },
};

// ────────────────────────────────────────────────────────────────────────────
// Provider Queue Monitor
//
// Polls /v1/queue/status and renders the status-bar pill + modal body. Shows
// only providers with max_concurrent > 0 (local LLM gateways that can't handle
// parallel calls). Fast poll (1s) whenever any provider has waiting tickets so
// position changes appear promptly; slow (4s) otherwise.
// ────────────────────────────────────────────────────────────────────────────
const QueueMonitor = {
  _interval: null,
  _fastMs: 1000,
  _slowMs: 10000,
  _mode: 'slow',
  providers: {},
  anyWaiting: false,
  anyActive: false,

  start() {
    if (this._interval) return;
    this._tick();
    this._schedule();
  },
  _schedule() {
    const ms = this._mode === 'fast' ? this._fastMs : this._slowMs;
    this._interval = setTimeout(() => this._tick(), ms);
  },
  async _tick() {
    if (!state.connected) { this._schedule(); return; }
    try {
      const resp = await fetch(`${BASE_URL}/v1/queue/status`, {
        method: 'GET', headers: API._headers(),
        signal: AbortSignal.timeout(3000),
      });
      if (resp.ok) {
        const data = await resp.json();
        this.providers = data.providers || {};
        this.anyWaiting = !!data.any_waiting;
        this.anyActive = !!data.any_active;
        this._mode = (this.anyWaiting || this.anyActive) ? 'fast' : 'slow';
        this._render();
      }
    } catch {}
    this._schedule();
  },
  _render() {
    const wrap = document.getElementById('status-queue');
    if (!wrap) return;
    // Admins-only — provider queue is infra detail not relevant to powerusers/users.
    if ((state.authUser?.role || 'admin') !== 'admin') { wrap.style.display = 'none'; return; }
    const entries = Object.entries(this.providers);
    // Hide only when no providers are configured for queueing at all.
    if (!entries.length) { wrap.style.display = 'none'; return; }
    let active = 0, waiting = 0, capacity = 0;
    for (const [_, p] of entries) {
      active += p.active_count || 0;
      waiting += p.waiting_count || 0;
      capacity += p.max_concurrent || 0;
    }
    wrap.style.display = 'flex';
    const dot = document.getElementById('status-queue-dot');
    let dotCls = 'idle';
    if (waiting > 0) dotCls = 'warming';
    else if (active > 0) dotCls = 'warm';
    dot.className = 'warmup-dot ' + dotCls;
    const label = document.getElementById('status-queue-label');
    if (label) label.textContent = waiting > 0 ? `${active}+${waiting}/${capacity}` : `${active}/${capacity}`;
    wrap.title = waiting > 0 || active > 0
      ? `Provider queue — ${active} running, ${waiting} waiting (capacity ${capacity}) — click for details`
      : `Provider queue — idle (capacity ${capacity}) — click for details`;

    if (document.getElementById('queue-modal')) renderQueueModalBody();
  },
};

/* ─── Quota monitor ─── */
// Polls /v1/quotas/me, updates the status-bar Plan-usage pill and any open
// modal. The pill mirrors Claude.ai's Plan-usage donut: the visible arc is
// the larger of (daily_pct, cycle_pct), tinted green/yellow/red by `level`.
const QuotaMonitor = {
  _interval: null,
  _ms: 30000,
  state: null,
  start() {
    if (this._interval) return;
    this._tick();
    this._schedule();
  },
  stop() { if (this._interval) { clearTimeout(this._interval); this._interval = null; } },
  refresh() { return this._tick(); },
  _schedule() { this._interval = setTimeout(() => this._tick(), this._ms); },
  async _tick() {
    if (!state.connected || !state.authUser) { this._schedule(); return; }
    try {
      const resp = await fetch(`${BASE_URL}/v1/quotas/me`, {
        method: 'GET', headers: API._headers(),
        signal: AbortSignal.timeout(3000),
      });
      if (resp.ok) {
        this.state = await resp.json();
        this._render();
        if (document.getElementById('quota-modal')) renderQuotaModalBody();
      }
    } catch {}
    this._schedule();
  },
  _render() {
    const wrap = document.getElementById('status-quota');
    if (!wrap) return;
    const st = this.state;
    if (!st || !st.enabled) { wrap.style.display = 'none'; return; }
    // Hide pill when neither limit is configured (avoids a silent 0-pct donut)
    const dailyOn = (st.daily?.limit_usd || 0) > 0;
    const cycleOn = (st.cycle?.limit_usd || 0) > 0;
    if (!dailyOn && !cycleOn) { wrap.style.display = 'none'; return; }
    wrap.style.display = 'flex';
    const arc = document.getElementById('status-quota-arc');
    const pct = Math.max(st.daily?.pct || 0, st.cycle?.pct || 0);
    const shown = Math.min(100, Math.max(0, pct));
    arc.setAttribute('stroke-dasharray', `${shown} ${100 - shown}`);
    const colorByLevel = { green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--error)' };
    arc.setAttribute('stroke', colorByLevel[st.level] || 'var(--success)');
    document.getElementById('status-quota-label').textContent = pct < 1 && pct > 0 ? '<1%' : `${Math.round(pct)}%`;
    const lim = (cycleOn ? `cycle ${st.cycle.pct.toFixed(0)}%` : '');
    const day = (dailyOn ? `today ${st.daily.pct.toFixed(0)}%` : '');
    wrap.title = `Plan usage — ${[day, lim].filter(Boolean).join(' · ')} — click for details`;
  },
};

function _quotaResetCountdown(iso) {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const sec = Math.max(0, (t - Date.now()) / 1000);
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}

function _quotaBar(used, limit, level) {
  const colorByLevel = { green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--error)' };
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  return `
    <div style="height:6px;background:var(--bg-200);border-radius:999px;overflow:hidden">
      <div style="height:100%;width:${pct.toFixed(1)}%;background:${colorByLevel[level] || 'var(--success)'};transition:width 0.4s"></div>
    </div>`;
}

function openQuotaModal() {
  const existing = document.getElementById('quota-modal');
  if (existing) { existing.remove(); return; }
  const pill = document.getElementById('status-quota');
  // Outside-click handler attached to document — popover itself isn't an overlay
  const onDocClick = (e) => {
    const pop = document.getElementById('quota-modal');
    if (!pop) { document.removeEventListener('mousedown', onDocClick, true); return; }
    if (!pop.contains(e.target) && e.target !== pill && !pill.contains(e.target)) {
      pop.remove();
      document.removeEventListener('mousedown', onDocClick, true);
    }
  };
  const onKeydown = (e) => {
    if (e.key === 'Escape') {
      document.getElementById('quota-modal')?.remove();
      document.removeEventListener('keydown', onKeydown, true);
    }
  };
  const pop = document.createElement('div');
  pop.id = 'quota-modal';
  pop.style.cssText = 'position:fixed;width:340px;background:var(--bg-000);border:1px solid var(--border-100);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,0.18);z-index:100;padding:14px 16px;font-size:13px;visibility:hidden;left:0;top:0';
  pop.onclick = (e) => e.stopPropagation();
  const isAdmin = (state.authUser?.role || 'admin') === 'admin';
  pop.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
      <div style="font-size:13px;font-weight:600;color:var(--text-100);flex:1">Plan usage</div>
      ${isAdmin ? `<button onclick="document.getElementById('quota-modal')?.remove();openGeneralSettings();setTimeout(()=>{const t=document.querySelector('.modal-tab[onclick*=&quot;quotas&quot;]');if(t)switchGeneralTab('quotas',t);},50);"
              style="background:transparent;border:1px solid var(--border-100);color:var(--text-300);
                     border-radius:6px;width:24px;height:24px;cursor:pointer;display:flex;align-items:center;justify-content:center"
              title="Open Quota settings">&#x2192;</button>` : ''}
    </div>
    <div id="quota-modal-body"><div style="color:var(--text-300);text-align:center;padding:12px">Loading…</div></div>`;
  document.body.appendChild(pop);

  // Render synchronously so we can measure the real height before positioning.
  // Without this, the first paint uses the placeholder body and the upward
  // offset is too small — the bottom third clips below the viewport edge.
  renderQuotaModalBody();

  const positionPopover = () => {
    const r = pill.getBoundingClientRect();
    const popW = pop.offsetWidth || 340;
    const popH = pop.offsetHeight || 200;
    const margin = 8;
    let left = r.right - popW;
    if (left < margin) left = margin;
    if (left + popW > window.innerWidth - margin) left = window.innerWidth - popW - margin;
    let top = r.top - popH - margin;
    if (top < margin) {
      // Not enough room above; place below pill but clamp so bottom edge stays visible
      top = Math.min(r.bottom + margin, window.innerHeight - popH - margin);
      if (top < margin) top = margin;
    }
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    pop.style.visibility = 'visible';
  };
  // First measure after layout, then refresh once more after the async fetch
  // so the height reflects the final content.
  requestAnimationFrame(positionPopover);

  // Defer outside-click handler so the click that opened us doesn't close us
  setTimeout(() => document.addEventListener('mousedown', onDocClick, true), 0);
  document.addEventListener('keydown', onKeydown, true);

  QuotaMonitor.refresh().then(() => {
    if (document.getElementById('quota-modal')) requestAnimationFrame(positionPopover);
  }).catch(() => {});
}

function renderQuotaModalBody() {
  const body = document.getElementById('quota-modal-body');
  if (!body) return;
  const st = QuotaMonitor.state;
  if (!st) { body.innerHTML = `<div style="color:var(--text-300);text-align:center;padding:20px">Loading…</div>`; return; }
  if (!st.enabled) {
    body.innerHTML = `<div style="color:var(--text-300);text-align:center;padding:24px">Quotas are disabled.</div>`;
    return;
  }
  const dailyOn = (st.daily?.limit_usd || 0) > 0;
  const cycleOn = (st.cycle?.limit_usd || 0) > 0;
  if (!dailyOn && !cycleOn) {
    body.innerHTML = `<div style="color:var(--text-300);text-align:center;padding:24px">No limits set for your role (<b>${esc(st.role)}</b>).
      Ask an admin to configure them in Settings &rarr; Quotas.</div>`;
    return;
  }
  const enforce = st.enforce_red || 'warn_only';
  const enforceLabel = ({warn_only:'warn only', force_local:'force local on red', hard_block:'hard block on red'})[enforce] || enforce;
  const cycleLabel = ({monthly:'Monthly', weekly:'Weekly', yearly:'Yearly'})[st.billing_cycle] || 'Cycle';
  const fmt = (v) => '$' + (v < 1 ? v.toFixed(3) : v.toFixed(2));
  const row = (label, used, limit, level, resetIso) => {
    if (!(limit > 0)) return '';
    return `<div style="margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span style="font-size:13px;color:var(--text-100);font-weight:500">${esc(label)}</span>
        <span style="font-size:12px;color:var(--text-300)">
          <b style="color:var(--text-100)">${(used/limit*100).toFixed(0)}%</b>
          &middot; ${fmt(used)} / ${fmt(limit)}
          ${resetIso ? ` &middot; resets in ${_quotaResetCountdown(resetIso)}` : ''}
        </span>
      </div>
      ${_quotaBar(used, limit, level)}
    </div>`;
  };
  const showLocalCTA = st.level === 'red';
  body.innerHTML = `
    ${row('Daily', st.daily.used_usd, st.daily.limit_usd, st.daily.level, st.daily.resets_at)}
    ${row(cycleLabel, st.cycle.used_usd, st.cycle.limit_usd, st.cycle.level, st.cycle.resets_at)}
    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;font-size:11px;color:var(--text-400)">
      <span>Role: <b style="color:var(--text-200)">${esc(st.role)}</b>${st.has_override ? ' (override)' : ''}</span>
      <span>Mode: ${esc(enforceLabel)}</span>
    </div>
    ${showLocalCTA && enforce === 'force_local' && st.default_local_fallback_model ? `
      <div style="margin-top:12px;padding:10px 12px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;font-size:12px;color:var(--text-200)">
        Quota exhausted &mdash; new requests automatically route to <b>${esc(modelShortName(st.default_local_fallback_model))}</b>.
      </div>` : ''}
    ${showLocalCTA && enforce === 'hard_block' ? `
      <div style="margin-top:12px;padding:10px 12px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;font-size:12px;color:var(--error)">
        Quota exhausted &mdash; further requests will be refused until reset. Switch to a local model or ask an admin to raise the limit.
      </div>` : ''}
    ${showLocalCTA && enforce === 'warn_only' ? `
      <div style="margin-top:12px;padding:10px 12px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;font-size:12px;color:var(--warning)">
        Over the configured limit, but enforcement is set to <b>warn only</b> &mdash; requests are still allowed.
      </div>` : ''}
  `;
}

function openQueueModal() {
  const existing = document.getElementById('queue-modal');
  if (existing) { existing.remove(); return; }
  const backdrop = document.createElement('div');
  backdrop.id = 'queue-modal';
  backdrop.className = 'modal-overlay';
  backdrop.style.display = 'flex';
  backdrop.onclick = (e) => { if (e.target === backdrop) backdrop.remove(); };
  backdrop.innerHTML = `
    <div class="modal-content" style="max-width:720px;width:92%;max-height:85vh;display:flex;flex-direction:column">
      <div class="modal-header">
        <h2>Local provider queue</h2>
        <button class="modal-close" onclick="document.getElementById('queue-modal').remove()" style="margin-left:auto">&times;</button>
      </div>
      <div class="modal-body" id="queue-modal-body" style="overflow-y:auto"><em style="color:var(--text-muted)">Loading…</em></div>
      <div class="modal-footer" style="color:var(--text-muted);font-size:12px">
        Providers with <code>max_concurrent &gt; 0</code> in <code>config.json</code> serialise their calls so
        multiple chats and background tasks don't fight for the same local LLM gateway. FIFO.
      </div>
    </div>`;
  document.body.appendChild(backdrop);
  renderQueueModalBody();
  // Kick the monitor into fast mode while the modal is open
  QueueMonitor._mode = 'fast';
  QueueMonitor._tick();
}

function renderQueueModalBody() {
  const body = document.getElementById('queue-modal-body');
  if (!body) return;
  const entries = Object.entries(QueueMonitor.providers || {});
  if (!entries.length) {
    body.innerHTML = `<div style="color:var(--text-muted)">No providers are configured for queueing. Set <code>max_concurrent</code> in <code>config.json</code> → <code>providers.&lt;name&gt;</code> to enable.</div>`;
    return;
  }
  const isAdmin = state.authUser?.role === 'admin';
  const fmtAge = (ms) => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${Math.round(ms/1000)}s`;
    return `${Math.round(ms/60000)}m${Math.round((ms%60000)/1000)}s`;
  };
  const cancelBtn = (t, state) => {
    if (!isAdmin) return '';
    const stateLbl = state === 'running' ? 'running' : 'waiting';
    return `<button class="queue-cancel-btn"
              onclick="cancelQueueTicket('${esc(t.id)}', '${stateLbl}')"
              title="Cancel this ${stateLbl} ticket (admin)"
              style="background:var(--danger,#c0392b);color:#fff;border:0;padding:2px 8px;
                     border-radius:6px;font-size:11px;cursor:pointer">
              Cancel
            </button>`;
  };
  const rows = entries.map(([pname, p]) => {
    const active = (p.active || []).map(t => `
      <tr>
        <td><span class="pill" style="background:var(--accent-brand);color:#fff;padding:1px 8px;border-radius:10px;font-size:11px">running</span></td>
        <td><code>${esc(t.label || '')}</code></td>
        <td><code>${esc(t.model || '')}</code></td>
        <td>${esc((t.session_id || '').slice(0,8)) || '<em style="color:var(--text-muted)">—</em>'}</td>
        <td>${esc(t.agent_id || '')}</td>
        <td>${fmtAge(t.age_ms || 0)}</td>
        <td style="text-align:right">${cancelBtn(t, 'running')}</td>
      </tr>
    `).join('');
    const waiting = (p.waiting || []).map(t => `
      <tr>
        <td><span class="pill" style="background:#f5a623;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px">#${t.position}</span></td>
        <td><code>${esc(t.label || '')}</code></td>
        <td><code>${esc(t.model || '')}</code></td>
        <td>${esc((t.session_id || '').slice(0,8)) || '<em style="color:var(--text-muted)">—</em>'}</td>
        <td>${esc(t.agent_id || '')}</td>
        <td>${fmtAge(t.age_ms || 0)}</td>
        <td style="text-align:right">${cancelBtn(t, 'waiting')}</td>
      </tr>
    `).join('');
    const empty = !active && !waiting
      ? `<tr><td colspan="7" style="color:var(--text-muted);font-style:italic">Idle</td></tr>` : '';
    return `
      <div style="margin-bottom:20px">
        <h4 style="margin:0 0 6px 0;font-size:14px">
          <code>${esc(pname)}</code>
          <span style="color:var(--text-muted);font-weight:normal;font-size:12px">
            · ${p.active_count}/${p.max_concurrent} active${p.waiting_count ? ` · ${p.waiting_count} waiting` : ''}
          </span>
        </h4>
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead>
            <tr style="border-bottom:1px solid var(--border);color:var(--text-muted);text-align:left">
              <th style="padding:4px">State</th>
              <th style="padding:4px">Label</th>
              <th style="padding:4px">Model</th>
              <th style="padding:4px">Session</th>
              <th style="padding:4px">Agent</th>
              <th style="padding:4px">Age</th>
              <th style="padding:4px;text-align:right">${isAdmin ? 'Action' : ''}</th>
            </tr>
          </thead>
          <tbody>${active}${waiting}${empty}</tbody>
        </table>
      </div>`;
  }).join('');
  body.innerHTML = rows;
}

async function cancelQueueTicket(ticketId, stateLabel) {
  if (!ticketId) return;
  const confirmMsg = stateLabel === 'running'
    ? 'Cancel this RUNNING LLM call? The active chat will abort.'
    : 'Cancel this waiting ticket?';
  if (!confirm(confirmMsg)) return;
  try {
    const resp = await fetch(`${BASE_URL}/v1/queue/cancel`, {
      method: 'POST', headers: {'Content-Type':'application/json', ...API._headers()},
      body: JSON.stringify({ticket_id: ticketId, reason: 'cancelled from queue modal'}),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      showToast(`Cancel failed: ${data.error || resp.status}`, true);
      return;
    }
    showToast(`Queue ticket cancelled (${data.state || '?'})`);
    // Refresh modal + pill
    QueueMonitor._mode = 'fast';
    QueueMonitor._tick();
  } catch (e) {
    showToast(`Cancel error: ${e}`, true);
  }
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

  // Init artifact panel resize
  initArtifactResize();

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
  // Demote a stored thinking_level that doesn't fit the active chat's
  // model (e.g. localStorage carried 'medium' across a model switch to a
  // mistral_blocks model that only accepts none/high).
  try { _ensureValidThinkingLevel(); } catch(_) {}
  refreshThinkingButton();
  refreshResearchModeButton();

  // Start on welcome view
  navigateTo('welcome');

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
