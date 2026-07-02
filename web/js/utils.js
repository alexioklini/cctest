/* ═══════════════════════════════════════════════════════════
   UTILITY FUNCTIONS
   ═══════════════════════════════════════════════════════════ */
function esc(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// German display label for a role identifier. The role VALUE stays the English
// config key (admin/poweruser/user) — only the visible label is localized.
function roleLabelDe(role) {
  return { admin: 'Administrator', poweruser: 'Hauptbenutzer', user: 'Benutzer' }[role] || role;
}

function showToast(msg, isError) {
  const t = document.createElement('div');
  t.className = 'toast' + (isError ? ' error' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

/* ── Inline help: a "?" icon that reveals an explanation in an anchored popover.
   Used across the config modals (agent / user / admin) so prose explanations
   are available on demand instead of always cluttering the dialog. The text is
   stashed (URI-encoded) on the icon's data-help attribute; a single delegated
   handler (see helpIconClick) opens the popover. Mirrors the citation-popover
   anchoring (viewport-aware flip + outside-click/Esc close). */
function helpIcon(text, opts) {
  if (!text) return '';
  const enc = encodeURIComponent(String(text));
  const lbl = (opts && opts.label) ? ` ${esc(opts.label)}` : '';
  return `<span class="help-icon" role="button" tabindex="0" aria-label="Hilfe"`
    + ` data-help="${enc}" onclick="helpIconClick(event, this)"`
    + ` onkeydown="if(event.key==='Enter'||event.key===' '){helpIconClick(event,this);}">?${lbl}</span>`;
}
let _activeHelpPopover = null;
function closeHelpPopover() {
  if (_activeHelpPopover) { _activeHelpPopover.remove(); _activeHelpPopover = null; }
  document.removeEventListener('click', _helpPopoverOutside, true);
  document.removeEventListener('keydown', _helpPopoverEsc);
}
function _helpPopoverOutside(e) {
  if (_activeHelpPopover && !_activeHelpPopover.contains(e.target)
      && !e.target.closest('.help-icon')) closeHelpPopover();
}
function _helpPopoverEsc(e) { if (e.key === 'Escape') closeHelpPopover(); }
function helpIconClick(ev, iconEl) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  const wasOpen = _activeHelpPopover && _activeHelpPopover._anchor === iconEl;
  closeHelpPopover();
  if (wasOpen) return;  // toggle off if re-clicking the same icon
  let text = '';
  try { text = decodeURIComponent(iconEl.getAttribute('data-help') || ''); } catch (e) { text = ''; }
  if (!text) return;
  const pop = document.createElement('div');
  pop.className = 'help-popover';
  pop._anchor = iconEl;
  // Plain text (escaped); newlines become breaks so multi-paragraph help reads.
  pop.innerHTML = `<div class="help-popover-arrow"></div>`
    + `<div class="help-popover-body">${esc(text).replace(/\n/g, '<br>')}</div>`;
  document.body.appendChild(pop);
  // Anchor: prefer below the icon, flip above when short on room (citation-popover pattern).
  const r = iconEl.getBoundingClientRect();
  const pr = pop.getBoundingClientRect();
  const vh = window.innerHeight, vw = window.innerWidth;
  let top = r.bottom + 8, arrow = 'top';
  if (top + pr.height + 12 > vh && r.top - pr.height - 12 > 0) {
    top = r.top - pr.height - 8; arrow = 'bottom';
  }
  const left = Math.max(8, Math.min(vw - pr.width - 8, r.left + r.width / 2 - pr.width / 2));
  pop.style.top = top + 'px';
  pop.style.left = left + 'px';
  const arrowEl = pop.querySelector('.help-popover-arrow');
  if (arrowEl) {
    const ax = Math.max(10, Math.min(pr.width - 18, r.left + r.width / 2 - left - 5));
    arrowEl.style.left = ax + 'px';
    arrowEl.style[arrow === 'top' ? 'top' : 'bottom'] = '-5px';
    arrowEl.style[arrow === 'top' ? 'borderTop' : 'borderBottom'] = 'none';
  }
  _activeHelpPopover = pop;
  // Attach immediately — the opening click was on the "?" icon, which
  // _helpPopoverOutside explicitly ignores (closest('.help-icon')), so it
  // won't self-close. (No setTimeout → no open-then-instant-close race.)
  document.addEventListener('click', _helpPopoverOutside, true);
  document.addEventListener('keydown', _helpPopoverEsc);
}

/**
 * showDialog({ title, message, buttons, input, danger })
 *
 * Styled replacement for confirm() / alert() / prompt().
 * Returns a Promise that resolves with:
 *   - the clicked button's value (string) for confirm/alert dialogs
 *   - the entered string for input dialogs (or null if cancelled)
 *
 * buttons: array of { label, value, primary?, danger? }
 *          defaults to [{ label:'OK', value:'ok', primary:true }]
 * input:   { placeholder?, defaultValue? }  — shows a text input
 * danger:  boolean — adds a red accent to the primary action
 */
function showDialog({ title = '', message = '', buttons = null, input = null, danger = false } = {}) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay dialog-overlay';

    const btns = buttons || [{ label: 'OK', value: 'ok', primary: true }];
    const inputHtml = input ? `
      <div class="dialog-input-wrap">
        <input class="dialog-input" type="text" placeholder="${esc(input.placeholder || '')}" value="${esc(input.defaultValue || '')}" autocomplete="off">
      </div>` : '';

    const btnHtml = btns.map(b => {
      const cls = b.primary ? (danger || b.danger ? 'dialog-btn dialog-btn-danger' : 'dialog-btn dialog-btn-primary') : 'dialog-btn dialog-btn-ghost';
      return `<button class="${cls}" data-value="${esc(b.value)}">${esc(b.label)}</button>`;
    }).join('');

    overlay.innerHTML = `
      <div class="dialog-card" role="dialog" aria-modal="true">
        ${title ? `<div class="dialog-header"><span class="dialog-title">${esc(title)}</span></div>` : ''}
        <div class="dialog-body">
          ${message ? `<p class="dialog-message">${message.replace(/\n/g, '<br>')}</p>` : ''}
          ${inputHtml}
        </div>
        <div class="dialog-footer">${btnHtml}</div>
      </div>`;

    const inputEl = overlay.querySelector('.dialog-input');
    if (inputEl) setTimeout(() => { inputEl.focus(); inputEl.select(); }, 30);

    const finish = (value) => {
      overlay.classList.add('dialog-out');
      setTimeout(() => overlay.remove(), 150);
      resolve(value);
    };

    overlay.querySelectorAll('.dialog-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const val = btn.dataset.value;
        finish(input ? (val === 'cancel' ? null : (inputEl?.value ?? '')) : val);
      });
    });

    overlay.addEventListener('click', e => {
      if (e.target === overlay) finish(input ? null : (btns.find(b => !b.primary)?.value ?? 'cancel'));
    });

    overlay.addEventListener('keydown', e => {
      if (e.key === 'Escape') finish(input ? null : (btns.find(b => !b.primary)?.value ?? 'cancel'));
      if (e.key === 'Enter' && inputEl) {
        finish(inputEl.value);
      }
    });

    document.body.appendChild(overlay);
    overlay.focus();
  });
}

// Convenience wrappers matching the native API shape
async function showConfirm(message, title = '') {
  const result = await showDialog({
    title,
    message,
    buttons: [
      { label: 'Abbrechen', value: 'cancel' },
      { label: 'OK', value: 'ok', primary: true },
    ],
  });
  return result === 'ok';
}

async function showConfirmDanger(message, title = '', confirmLabel = 'Löschen') {
  const result = await showDialog({
    title,
    message,
    danger: true,
    buttons: [
      { label: 'Abbrechen', value: 'cancel' },
      { label: confirmLabel, value: 'ok', primary: true, danger: true },
    ],
  });
  return result === 'ok';
}

async function showAlert(message, title = '') {
  await showDialog({
    title,
    message,
    buttons: [{ label: 'OK', value: 'ok', primary: true }],
  });
}

async function showPrompt(message, defaultValue = '', title = '') {
  return showDialog({
    title,
    message,
    input: { defaultValue },
    buttons: [
      { label: 'Abbrechen', value: 'cancel' },
      { label: 'OK', value: 'ok', primary: true },
    ],
  });
}

function relativeTime(ts) {
  if (!ts) return '';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return 'gerade eben';
  if (diff < 3600) return 'vor ' + Math.floor(diff/60) + ' Min.';
  if (diff < 86400) return 'vor ' + Math.floor(diff/3600) + ' Std.';
  if (diff < 604800) return 'vor ' + Math.floor(diff/86400) + ' Tg.';
  return new Date(ts).toLocaleDateString();
}

// Prompt-cache savings helper — resolve a model's per-1M input + cache_read
// rates from state.modelsConfig (mirrors engine.quotas._get_cost_rate:
// cache_read = per-model cost_cache_read if >0, else 0.1× input) and return the
// $ saved by having `cachedTokens` billed at the discounted rate vs. full input.
function cacheSavingsUSD(modelId, cachedTokens) {
  if (!cachedTokens) return 0;
  const cfg = state.modelsConfig?.models?.[modelId] || {};
  const input = Number(cfg.cost_input) || 0;
  const ccr = Number(cfg.cost_cache_read) || 0;
  const cacheRead = ccr > 0 ? ccr : input * 0.1;
  return (cachedTokens / 1e6) * (input - cacheRead);
}

function modelShortName(modelId, withProvider = true) {
  if (!modelId) return '';
  if (modelId === 'auto-cloud' || modelId === 'auto') return '✨ Smart (Cloud)';  // 'auto' = legacy alias
  if (modelId === 'auto-local') return '✨ Smart (Lokal)';
  if (modelId === 'moa') return '🧬 MoA (Smart)';
  const cfg = state.modelsConfig?.models?.[modelId];
  let name = '';
  // Check display_name first (user-configurable), then shortname
  if (cfg?.display_name) { name = cfg.display_name; }
  else if (cfg?.shortname && cfg.shortname !== modelId && !cfg.shortname.startsWith('claude-')) { name = cfg.shortname; }
  else {
    const m = modelId.toLowerCase();
    if (m === 'claude-opus-4-6' || m === 'claude-opus-4-20250514') name = 'Opus 4.6';
    else if (m === 'claude-sonnet-4-6' || m === 'claude-sonnet-4-20250514') name = 'Sonnet 4.6';
    else if (m.includes('claude-3-7-sonnet')) name = 'Sonnet 3.7';
    else if (m.includes('claude-3-5-sonnet')) name = 'Sonnet 3.5';
    else if (m.includes('haiku-4-5') || m.includes('haiku-4.5')) name = 'Haiku 4.5';
    else if (m.includes('haiku')) name = 'Haiku';
    else if (m.includes('crow-9b')) name = 'Crow 9B';
    else if (m.includes('crow-4b')) name = 'Crow 4B';
    else if (m === 'minimax-m2.7' || m === 'minimax-m2.5') name = modelId;
    else if (m.includes('gemini-3.1')) name = 'Gemini 3.1 Flash Lite';
    else if (m.includes('gemini-3-flash')) name = 'Gemini 3 Flash';
    else if (m.includes('gemini-2.5-pro')) name = 'Gemini 2.5 Pro';
    else if (m.includes('gemini-2.5-flash')) name = 'Gemini 2.5 Flash';
    else if (m.includes('gemini')) name = modelId;
    else if (m.includes('qwen')) name = modelId;
    else {
      const parts = modelId.split('/');
      const n = parts[parts.length - 1];
      name = n.length > 25 ? n.substring(0, 23) + '...' : n;
    }
  }
  if (withProvider && cfg?.provider) return `${name} (${cfg.provider})`;
  return name;
}

// User-editable description for a model — shown as tooltip in dropdowns.
// Falls back to provider-qualified short name when empty.
// True for any of the auto-routing directives: the two Smart modes, the
// legacy 'auto' alias (pre-split / agent.json model:"auto"), and 'moa'
// (Smart routing + reference fan-out). Behavioral gates (spinner,
// warmup-skip, "keep composer on Smart") key off this, not a bare
// === 'auto', so all directive modes are handled uniformly.
function isAutoModel(modelId) {
  return modelId === 'auto' || modelId === 'auto-cloud' || modelId === 'auto-local' || modelId === 'moa';
}

function modelDescription(modelId) {
  if (!modelId) return '';
  if (modelId === 'auto-local') return 'Wählt für jede Nachricht automatisch das am besten passende LOKALE Modell';
  if (modelId === 'auto-cloud' || modelId === 'auto') return 'Wählt für jede Nachricht automatisch das am besten passende CLOUD-Modell';
  if (modelId === 'moa') return 'Mixture of Agents: bei geeigneten Aufgabentypen entwerfen mehrere Modelle parallel, das Smart-Modell führt die Entwürfe zur Antwort zusammen';
  const cfg = state.modelsConfig?.models?.[modelId];
  const desc = (cfg?.description || '').trim();
  if (desc) return desc;
  return modelShortName(modelId, true);
}

// Renders <option title="<description>">…</option> for a model id.
// Tooltip falls back to the qualified short name when no description is set,
// so dropdowns always show something useful on hover.
function modelOption(mid, { selected = false, label = null, suffix = '' } = {}) {
  const lbl = label != null ? label : modelShortName(mid);
  return `<option value="${esc(mid)}" title="${esc(modelDescription(mid))}"${selected ? ' selected' : ''}>${esc(lbl)}${suffix}</option>`;
}

// True when the model is enabled and its capabilities list includes `cap`.
// Default cap is 'chat' — every general model dropdown filters by this.
function modelHasCapability(midOrCfg, cap) {
  cap = cap || 'chat';
  const cfg = (typeof midOrCfg === 'string')
    ? (state.modelsConfig?.models || {})[midOrCfg]
    : (midOrCfg || {});
  if (!cfg || cfg.enabled === false) return false;
  const caps = Array.isArray(cfg.capabilities) ? cfg.capabilities : [];
  return caps.includes(cap);
}

// Returns [[mid, cfg], ...] for enabled models with capability `cap`,
// sorted by priority (desc). Default cap is 'chat'.
function enabledModelsWithCapability(cap) {
  cap = cap || 'chat';
  const mc = state.modelsConfig?.models || {};
  return Object.entries(mc)
    .filter(([mid, cfg]) => modelHasCapability(cfg, cap))
    .sort((a, b) => (b[1].priority || 0) - (a[1].priority || 0));
}

function autoResizeInput(el) {
  el.style.height = 'auto';
  const full = el.scrollHeight;
  el.style.height = Math.min(full, 200) + 'px';
  // Only enable the inner scrollbar once the field is capped at max-height.
  // Below that the CSS keeps overflow hidden + zero-width WebKit scrollbar,
  // which kills Safari's phantom scrollbar on an empty / single-line field.
  el.classList.toggle('is-scrolling', full > 200);
}

// Returns the textarea for the currently active composer view. The project-
// detail view now hosts a full composer (same toggles as chat), so it picks
// `project-input`; welcome/chat keep their existing ids.
function _composerInputEl() {
  if (state.currentView === 'welcome')        return document.getElementById('welcome-input');
  if (state.currentView === 'project-detail') return document.getElementById('project-input');
  return document.getElementById('chat-input');
}

function _composerToggleEls(suffix) {
  return [...document.querySelectorAll(`[data-composer-toggle="${suffix}"]`)];
}

function updateSendButton() {
  const hasFiles = state._pendingImages.length > 0 || state._pendingFiles.length > 0;
  const pairs = [
    ['welcome-send-btn', 'welcome-input'],
    ['chat-send-btn',    'chat-input'],
    ['project-send-btn', 'project-input'],
  ];
  for (const [btnId, inputId] of pairs) {
    const btn = document.getElementById(btnId);
    if (!btn) continue;
    const inp = document.getElementById(inputId);
    btn.classList.toggle('active', (inp?.value?.trim().length > 0) || hasFiles);
  }
}

/* ═══════════════════════════════════════════════════════════
   PII / GDPR DETECTION — SERVER-ONLY (9.200.0)
   The browser-side regex scanner (PIIScanner) was removed. PII
   detection now runs exclusively on the server (engine/pii_ner.py
   _pii_scan_text: regex catalog + spaCy NER + confidence bands).
   The pre-send dialog calls /v1/gdpr/scan-text and (9.205.0) scans
   attachments via /v1/attachments/scan AT SEND TIME — both under one
   cancellable progress overlay. The rule catalog / labels the Settings
   panel + chat view render come from the server gdpr_scanner config
   (state.gdprCatalog, populated in applyGdprConfigToScanner).
   ═══════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════
   NEXT-PROMPT SUGGESTIONS (ghost text in composer)
   ═══════════════════════════════════════════════════════════ */
const NextPrompt = {
  _suggestion: null,
  _origPlaceholder: null,
  _fetchToken: 0,
  _onDemandPending: false,

  _input() {
    return document.getElementById('chat-input');
  },

  clear() {
    this._suggestion = null;
    const input = this._input();
    if (!input) return;
    input.classList.remove('has-suggestion');
    if (this._origPlaceholder != null) {
      input.placeholder = this._origPlaceholder;
    }
  },

  set(text) {
    const input = this._input();
    if (!input || !text) return;
    // Only show if input is empty — otherwise we'd stomp what the user typed
    if (input.value.length > 0) return;
    this._suggestion = text;
    if (this._origPlaceholder == null) this._origPlaceholder = input.placeholder || '';
    input.placeholder = text;
    input.classList.add('has-suggestion');
  },

  accept({ submit = false } = {}) {
    const input = this._input();
    const text = this._suggestion;
    if (!input || !text) return false;
    input.value = text;
    this.clear();
    autoResizeInput(input);
    updateSendButton();
    if (submit) {
      sendMessage();
    } else {
      input.focus();
      const end = input.value.length;
      try { input.setSelectionRange(end, end); } catch (e) {}
    }
    return true;
  },

  active() {
    return !!this._suggestion;
  },

  async fetchFor(sessionId) {
    if (!sessionId) return;
    const token = ++this._fetchToken;
    try {
      const data = await API.get(`/v1/sessions/${encodeURIComponent(sessionId)}/next-prompt`);
      // Drop stale responses if a newer fetch was issued in the meantime
      if (token !== this._fetchToken) return;
      // Drop if the active session changed while we were waiting
      if (state.activeChat?.sessionId !== sessionId) return;
      const text = (data?.suggestion || '').trim();
      if (text) this.set(text);
    } catch (e) {
      // Silent — suggestions are best-effort
    }
  },

  // User pressed Tab on an EMPTY input with no ghost-text showing — either the
  // suggestion was never computed, or they dismissed it and changed their mind.
  // Reuse the precomputed suggestion if it's still around, otherwise generate
  // one on demand and FILL the input (they explicitly asked, so don't just
  // re-show a ghost placeholder). Returns true if a request was kicked off /
  // satisfied, false if nothing could be done.
  async requestOnDemand() {
    const input = this._input();
    if (!input || input.value.length > 0) return false;
    // 1) Reuse a precomputed suggestion that's still in hand.
    if (this._suggestion) return this.accept({ submit: false });
    // 2) Generate on demand. Guard against repeat Tabs while in flight.
    if (this._onDemandPending) return true;
    const sessionId = state.activeChat?.sessionId;
    if (!sessionId) return false;
    this._onDemandPending = true;
    const token = ++this._fetchToken;
    if (this._origPlaceholder == null) this._origPlaceholder = input.placeholder || '';
    input.placeholder = 'Vorschlag wird erzeugt…';
    input.classList.add('suggestion-loading');
    try {
      const data = await API.get(`/v1/sessions/${encodeURIComponent(sessionId)}/next-prompt`);
      // Stale (newer fetch) or session changed while waiting → bail.
      if (token !== this._fetchToken || state.activeChat?.sessionId !== sessionId) return false;
      const text = (data?.suggestion || '').trim();
      if (!text) return false;
      // The user may have started typing while we waited — don't stomp it.
      if (input.value.length > 0) { this.set(text); return false; }
      this._suggestion = text;
      return this.accept({ submit: false });
    } catch (e) {
      return false;
    } finally {
      this._onDemandPending = false;
      input.classList.remove('suggestion-loading');
      // Restore the original placeholder if nothing replaced it.
      if (!this._suggestion && this._origPlaceholder != null) {
        input.placeholder = this._origPlaceholder;
      }
    }
  },
};

// Wire a scroll-anchor button so a SHORT press calls onShort() and a LONG
// press (held ≥ HOLD_MS) calls onLong() instead. While held, a CSS-driven
// progress ring fills (the button gets `.lp-charging`; the ring animation
// duration matches HOLD_MS). Pointer-events based → works on mouse + touch.
// Releasing/leaving/moving-away before the threshold = short press. Idempotent
// per element (guarded by _lpWired).
function wireLongPress(btn, onShort, onLong) {
  if (!btn || btn._lpWired) return;
  btn._lpWired = true;
  const HOLD_MS = 500;
  let timer = null, fired = false, downId = null;
  const clear = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    btn.classList.remove('lp-charging');
    downId = null;
  };
  btn.addEventListener('pointerdown', (e) => {
    if (e.button != null && e.button !== 0) return;   // primary button only
    fired = false;
    downId = e.pointerId;
    btn.classList.add('lp-charging');
    timer = setTimeout(() => {
      fired = true;
      btn.classList.remove('lp-charging');
      timer = null;
      try { onLong(); } catch (err) { /* best-effort */ }
    }, HOLD_MS);
  });
  btn.addEventListener('pointerup', (e) => {
    if (downId !== null && e.pointerId !== downId) return;
    const wasCharging = timer !== null;
    clear();
    if (wasCharging && !fired) { try { onShort(); } catch (err) { /* best-effort */ } }
  });
  // Pointer left the button or got cancelled before the threshold → abort
  // (no short fire — the user moved off intentionally).
  btn.addEventListener('pointerleave', clear);
  btn.addEventListener('pointercancel', clear);
}

