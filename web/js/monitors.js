// monitors.js — runtime status monitor classes (Connection/Mempalace/Warmup/Queue/Quota) + their detail modals. Split from init.js (Tier F Phase 4). Global <script>, no modules.

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
    wrap.title = connected ? 'Server: verbunden' : 'Server: getrennt';
    renderUserMenu();
    if (connected && this._failCount === 0) return;
    if (connected) showToast('Wieder mit dem Server verbunden');
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
  if (ready >= target) return ` · Pool ${ready}/${target} bereit ✓`;
  if (ready > 0) return ` · Pool ${ready}/${target} bereit (${building} im Aufbau)`;
  if (building > 0) return ` · ${building} Session${building>1?'s':''} werden vorbereitet…`;
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
      <h2 style="margin:0;font-size:18px;font-weight:600;color:var(--text-000)">Warm-Session-Pool${helpIcon('Warmup hält Modelle vorgeladen, um die Latenz bis zum ersten Token zu verringern. Der Modus wird pro Modell im Tab „Modelle“ festgelegt:\n\nfull: lädt System-Prompt + Werkzeuge in den KV-Cache vor (~5-6 s bis zur ersten Antwort).\n\nminimal: lädt nur die Modellgewichte, keinen Prompt und keine Werkzeuge (~10-15 s bis zur ersten Antwort).\n\nVoll vorgeladene Modelle teilen sich den GPU-Speicher — bei Engpässen verdrängen sie sich gegenseitig.\n\nIst kein Modell mit Warmup aktiviert: Aktivieren Sie Warmup für ein Modell im Tab „Modelle“, um Sessions vorzubereiten.')}</h2>
      <span id="warmpool-modal-hint" style="font-size:12px;color:var(--text-400)"></span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()" style="margin-left:auto">&times;</button>
    </div>
    <div id="warmpool-body" style="flex:1;overflow-y:auto;padding:12px 24px 24px">
      <div style="color:var(--text-400);padding:24px;text-align:center">Wird geladen...</div>
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
    ? `${entries.length} Modell${entries.length>1?'e':''} mit aktiviertem Warmup`
    : 'Kein Modell hat Warmup aktiviert';
  if (!entries.length) {
    body.innerHTML = `<div style="color:var(--text-400);padding:24px;text-align:center">
      Kein Modell hat Warmup aktiviert. Details über das <span style="font-weight:500">?</span> oben im Titel.
    </div>`;
    return;
  }
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
    else if (st.state === 'warming') stateBadge = '<span style="color:#f59e0b">● wird aufgewärmt…</span>';
    else if (st.state === 'failed') stateBadge = '<span style="color:#ef4444">● fehlgeschlagen</span>';
    else if (st.state === 'skipped_cloud') stateBadge = '<span style="color:var(--text-400)">○ übersprungen (Cloud)</span>';
    else stateBadge = '<span style="color:var(--text-400)">○ inaktiv</span>';
    const age = (() => {
      const t = st.last_warmup_ts || st.last_used_ts || 0;
      if (!t) return 'nie aufgewärmt';
      const secs = Math.max(0, Math.round(now - t));
      if (secs < 60) return `vor ${secs} s`;
      if (secs < 3600) return `vor ${Math.round(secs/60)} Min.`;
      return `vor ${Math.round(secs/3600)} Std.`;
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
      modeChip += `<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:${actCol.bg};color:${actCol.fg};font-weight:500" title="Derzeit im Modus ${esc(actual)} vorgeladen — wird im nächsten Zyklus auf ${esc(desired)} umgestellt">${esc(actual)} ⟲</span>`;
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
        <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-300)">${ready}/${target}${building ? ` (+${building} im Aufbau)` : ''}</span>
        <span style="font-size:11px">${age}</span>
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="triggerPoolWarmup('${esc(mid)}')">Jetzt aufwärmen</button>
      </div>
      ${err}
    </div>`;
  }).join('');
  body.innerHTML = rows;
}

async function triggerPoolWarmup(modelId) {
  try {
    await fetch(`${BASE_URL}/v1/warmup/trigger`, {
      method: 'POST',
      headers: {'Content-Type':'application/json', ...API._headers()},
      body: JSON.stringify({model: modelId}),
    });
    showToast(`${modelId} wird aufgewärmt…`);
    WarmupMonitor._mode = 'fast';
    WarmupMonitor._tick();
  } catch (e) { showToast(`Warmup fehlgeschlagen: ${e}`, true); }
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
      const age = st.age_seconds != null ? 'vor ' + Math.round(st.age_seconds) + ' s' : 'nie';
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
    wrap.title = `Warm-Pool: ${ready}/${target} bereit`
               + (building ? ` · ${building} im Aufbau` : '')
               + (failed ? ` · ${failed} fehlgeschlagen` : '')
               + ' — für Details klicken';
  },
  _applyComposerDot(id, model) {
    const el = document.getElementById(id);
    if (!el) return;
    const st = this.states[model];
    if (!st) { el.style.display = 'none'; return; }
    el.style.display = 'inline-block';
    el.className = 'warmup-dot ' + st.state;
    const age = st.age_seconds != null ? 'vor ' + Math.round(st.age_seconds) + ' s' : 'nie';
    el.title = `Modell-Warmup: ${st.state}`
            + (st.state === 'warm' ? ` · vorgeladen ${age}` : '')
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
      ? `Provider-Warteschlange — ${active} aktiv, ${waiting} wartend (Kapazität ${capacity}) — für Details klicken`
      : `Provider-Warteschlange — inaktiv (Kapazität ${capacity}) — für Details klicken`;

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
    // Hide-Pille wenn kein Limit konfiguriert ist (vermeidet stillen 0-%-Donut)
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
    const lim = (cycleOn ? `Zyklus ${st.cycle.pct.toFixed(0)}%` : '');
    const day = (dailyOn ? `heute ${st.daily.pct.toFixed(0)}%` : '');
    wrap.title = `Plan-Nutzung — ${[day, lim].filter(Boolean).join(' · ')} — für Details klicken`;
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
    if (!pop) { document.removeEventListener('mousedown', onDocClick, true); window.removeEventListener('resize', repositionQuotaModal); return; }
    if (!pop.contains(e.target) && e.target !== pill && !pill.contains(e.target)) {
      pop.remove();
      document.removeEventListener('mousedown', onDocClick, true);
      window.removeEventListener('resize', repositionQuotaModal);
    }
  };
  const onKeydown = (e) => {
    if (e.key === 'Escape') {
      document.getElementById('quota-modal')?.remove();
      document.removeEventListener('keydown', onKeydown, true);
      window.removeEventListener('resize', repositionQuotaModal);
    }
  };
  const pop = document.createElement('div');
  pop.id = 'quota-modal';
  // Flex column: fixed header + a single scrollable content region. The scroll
  // lives on #quota-modal-scroll (not the popover root) so the title bar stays
  // pinned and the body scrolls reliably regardless of height/anchor. max-height
  // is set dynamically by repositionQuotaModal() to the real space available at
  // the chosen anchor, so the scrollbar is always fully on-screen + reachable.
  pop.style.cssText = 'position:fixed;width:420px;display:flex;flex-direction:column;background:var(--bg-000);border:1px solid var(--border-100);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,0.18);z-index:100;font-size:13px;visibility:hidden;left:0;top:0;overflow:hidden';
  pop.onclick = (e) => e.stopPropagation();
  const isAdmin = (state.authUser?.role || 'admin') === 'admin';
  pop.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;padding:14px 16px 10px;flex:0 0 auto">
      <div style="font-size:13px;font-weight:600;color:var(--text-100);flex:1">Plan-Nutzung</div>
      ${isAdmin ? `<button onclick="document.getElementById('quota-modal')?.remove();openGeneralSettings();setTimeout(()=>{const t=document.querySelector('.modal-tab[onclick*=&quot;quotas&quot;]');if(t)switchGeneralTab('quotas',t);},50);"
              style="background:transparent;border:1px solid var(--border-100);color:var(--text-300);
                     border-radius:6px;width:24px;height:24px;cursor:pointer;display:flex;align-items:center;justify-content:center"
              title="Kontingent-Einstellungen öffnen">&#x2192;</button>` : ''}
    </div>
    <div id="quota-modal-scroll" style="overflow-y:auto;padding:0 16px 14px;flex:1 1 auto;min-height:0">
      <div id="quota-modal-body"><div style="color:var(--text-300);text-align:center;padding:12px">Wird geladen…</div></div>
      <div id="cost-breakdown-section" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border-100)"></div>
    </div>`;
  document.body.appendChild(pop);

  // Cost breakdown: window selector + per-use-case × per-model table. Lazy —
  // fetched on open and whenever the window changes, NOT on the 30s quota poll.
  renderCostBreakdownSection();

  // Render synchronously so we can measure the real height before positioning.
  // Without this, the first paint uses the placeholder body and the upward
  // offset is too small — the bottom third clips below the viewport edge.
  renderQuotaModalBody();

  // First measure after layout, then refresh once more after the async fetch
  // so the height reflects the final content.
  requestAnimationFrame(repositionQuotaModal);

  // Defer outside-click handler so the click that opened us doesn't close us
  setTimeout(() => document.addEventListener('mousedown', onDocClick, true), 0);
  document.addEventListener('keydown', onKeydown, true);
  // Re-fit on viewport resize (rotate / window resize) while the modal is open.
  window.addEventListener('resize', repositionQuotaModal);

  QuotaMonitor.refresh().then(() => {
    if (document.getElementById('quota-modal')) requestAnimationFrame(repositionQuotaModal);
  }).catch(() => {});
}

// Anchor the Plan-usage popover to the status-bar pill AND size its scroll
// region to the real space available, so the scrollbar is always fully on-screen
// and reachable. Prefers placing above the pill; falls back below. Called on
// open, after the async breakdown loads, on expand/collapse, and on resize.
function repositionQuotaModal() {
  const pop = document.getElementById('quota-modal');
  const pill = document.getElementById('status-quota');
  // Modal closed (e.g. via the settings-shortcut button) — detach this resize
  // handler so it doesn't linger.
  if (!pop) { window.removeEventListener('resize', repositionQuotaModal); return; }
  if (!pill) return;
  const margin = 8;
  const r = pill.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const popW = pop.offsetWidth || 420;
  // Space above the pill vs below it; pick the roomier side.
  const spaceAbove = r.top - margin * 2;
  const spaceBelow = vh - r.bottom - margin * 2;
  const placeAbove = spaceAbove >= spaceBelow;
  const avail = Math.max(120, Math.floor(placeAbove ? spaceAbove : spaceBelow));
  // Cap the whole popover to the available space (also never exceed ~88vh).
  const maxH = Math.min(avail, Math.floor(vh * 0.88));
  pop.style.maxHeight = maxH + 'px';
  // Now measure the (possibly clamped) height and place it.
  const popH = Math.min(pop.offsetHeight || 200, maxH);
  let left = r.right - popW;
  if (left < margin) left = margin;
  if (left + popW > vw - margin) left = vw - popW - margin;
  let top = placeAbove ? (r.top - popH - margin) : (r.bottom + margin);
  if (top < margin) top = margin;
  if (top + popH > vh - margin) top = Math.max(margin, vh - popH - margin);
  pop.style.left = left + 'px';
  pop.style.top = top + 'px';
  pop.style.visibility = 'visible';
}

function renderQuotaModalBody() {
  const body = document.getElementById('quota-modal-body');
  if (!body) return;
  const st = QuotaMonitor.state;
  if (!st) { body.innerHTML = `<div style="color:var(--text-300);text-align:center;padding:20px">Wird geladen…</div>`; return; }
  if (!st.enabled) {
    body.innerHTML = `<div style="color:var(--text-300);text-align:center;padding:24px">Kontingente sind deaktiviert.</div>`;
    return;
  }
  const dailyOn = (st.daily?.limit_usd || 0) > 0;
  const cycleOn = (st.cycle?.limit_usd || 0) > 0;
  if (!dailyOn && !cycleOn) {
    body.innerHTML = `<div style="color:var(--text-300);text-align:center;padding:24px">Für Ihre Rolle (<b>${esc(st.role)}</b>) sind keine Limits gesetzt.
      Bitten Sie einen Administrator, sie unter Einstellungen &rarr; Kontingente zu konfigurieren.</div>`;
    return;
  }
  const enforce = st.enforce_red || 'warn_only';
  const enforceLabel = ({warn_only:'nur warnen', force_local:'bei Rot lokal erzwingen', hard_block:'bei Rot hart blockieren'})[enforce] || enforce;
  const cycleLabel = ({monthly:'Monatlich', weekly:'Wöchentlich', yearly:'Jährlich'})[st.billing_cycle] || 'Zyklus';
  const fmt = (v) => '$' + (v < 1 ? v.toFixed(3) : v.toFixed(2));
  const row = (label, used, limit, level, resetIso) => {
    if (!(limit > 0)) return '';
    return `<div style="margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span style="font-size:13px;color:var(--text-100);font-weight:500">${esc(label)}</span>
        <span style="font-size:12px;color:var(--text-300)">
          <b style="color:var(--text-100)">${(used/limit*100).toFixed(0)}%</b>
          &middot; ${fmt(used)} / ${fmt(limit)}
          ${resetIso ? ` &middot; Zurücksetzung in ${_quotaResetCountdown(resetIso)}` : ''}
        </span>
      </div>
      ${_quotaBar(used, limit, level)}
    </div>`;
  };
  const showLocalCTA = st.level === 'red';
  body.innerHTML = `
    ${row('Täglich', st.daily.used_usd, st.daily.limit_usd, st.daily.level, st.daily.resets_at)}
    ${row(cycleLabel, st.cycle.used_usd, st.cycle.limit_usd, st.cycle.level, st.cycle.resets_at)}
    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;font-size:11px;color:var(--text-400)">
      <span>Rolle: <b style="color:var(--text-200)">${esc(st.role)}</b>${st.has_override ? ' (überschrieben)' : ''}</span>
      <span>Modus: ${esc(enforceLabel)}</span>
    </div>
    ${showLocalCTA && enforce === 'force_local' && st.default_local_fallback_model ? `
      <div style="margin-top:12px;padding:10px 12px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;font-size:12px;color:var(--text-200)">
        Kontingent ausgeschöpft &mdash; neue Anfragen werden automatisch an <b>${esc(modelShortName(st.default_local_fallback_model))}</b> geleitet.
      </div>` : ''}
    ${showLocalCTA && enforce === 'hard_block' ? `
      <div style="margin-top:12px;padding:10px 12px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;font-size:12px;color:var(--error)">
        Kontingent ausgeschöpft &mdash; weitere Anfragen werden bis zur Zurücksetzung abgelehnt. Wechseln Sie zu einem lokalen Modell oder bitten Sie einen Administrator, das Limit zu erhöhen.
      </div>` : ''}
    ${showLocalCTA && enforce === 'warn_only' ? `
      <div style="margin-top:12px;padding:10px 12px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;font-size:12px;color:var(--warning)">
        Über dem konfigurierten Limit, aber die Durchsetzung steht auf <b>nur warnen</b> &mdash; Anfragen sind weiterhin erlaubt.
      </div>` : ''}
  `;
}

// --- Cost breakdown (per use-case × per model, multi-window) ---

// Remembered across opens within a session so the user's window choice sticks.
let _costBreakdownWindow = '30d';
const _COST_WINDOWS = [
  ['today', 'Heute'],
  ['week', 'Diese Woche'],
  ['7d', 'Letzte 7 Tage'],
  ['30d', 'Letzte 30 Tage'],
  ['180d', 'Letzte 180 Tage'],
  ['365d', 'Letzte 365 Tage'],
  ['ytd', 'Seit Jahresbeginn'],
  ['cycle', 'Aktueller Abrechnungszeitraum'],
  ['last_cycle', 'Letzter Abrechnungszeitraum'],
  ['all', 'Gesamt'],
];

function _costFmt(v) {
  const n = Number(v) || 0;
  if (n === 0) return '$0.00';
  if (n < 0.01) return '$' + n.toFixed(4);
  if (n < 1) return '$' + n.toFixed(3);
  if (n < 1000) return '$' + n.toFixed(2);
  return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function _tokFmt(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
  return String(n);
}

// Stable accent palette for use-case bars (cycles for >8 buckets). Tuned to
// read well on both light + dark themes.
const _COST_PALETTE = [
  '#6366f1', '#0ea5e9', '#10b981', '#f59e0b',
  '#ec4899', '#8b5cf6', '#14b8a6', '#ef4444',
];

function renderCostBreakdownSection() {
  const sec = document.getElementById('cost-breakdown-section');
  if (!sec) return;
  const opts = _COST_WINDOWS.map(([k, label]) =>
    `<option value="${k}"${k === _costBreakdownWindow ? ' selected' : ''}>${esc(label)}</option>`).join('');
  sec.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
      <div style="font-size:13px;font-weight:600;color:var(--text-100);flex:1;display:flex;align-items:center;gap:6px">
        <span style="font-size:14px">📊</span>Kostenaufschlüsselung
      </div>
      <select id="cost-breakdown-window" onchange="onCostBreakdownWindowChange(this.value)"
              style="background:var(--bg-100);border:1px solid var(--border-100);color:var(--text-100);
                     border-radius:7px;padding:4px 8px;font-size:12px;cursor:pointer;font-family:inherit">${opts}</select>
    </div>
    <div id="cost-breakdown-body"><div style="color:var(--text-300);text-align:center;padding:14px">Wird geladen…</div></div>`;
  loadCostBreakdown(_costBreakdownWindow);
}

function onCostBreakdownWindowChange(w) {
  _costBreakdownWindow = w || '30d';
  loadCostBreakdown(_costBreakdownWindow);
}

function loadCostBreakdown(window) {
  const body = document.getElementById('cost-breakdown-body');
  if (!body) return;
  body.innerHTML = `<div style="color:var(--text-300);text-align:center;padding:12px">Wird geladen…</div>`;
  API.getCostBreakdown(window).then((data) => {
    renderCostBreakdownBody(data);
    // Content height changed — re-anchor + re-fit the scroll region.
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(repositionQuotaModal);
  }).catch((e) => {
    body.innerHTML = `<div style="color:var(--error);padding:8px;font-size:12px">Fehler beim Laden: ${esc(String(e && e.message || e))}</div>`;
  });
}

function toggleCostUseCase(id) {
  const e = document.getElementById(id);
  const r = document.getElementById(id + '-row');
  if (!e) return;
  const open = e.style.display !== 'none';
  e.style.display = open ? 'none' : 'block';
  if (r) r.setAttribute('data-open', open ? '0' : '1');
  const arr = document.getElementById(id + '-arr');
  if (arr) arr.style.transform = open ? 'rotate(0deg)' : 'rotate(90deg)';
}

function renderCostBreakdownBody(data) {
  const body = document.getElementById('cost-breakdown-body');
  if (!body) return;
  const buckets = (data && data.by_use_case) || [];
  const total = (data && data.total_cost) || 0;
  const totalCalls = (data && data.total_calls) || 0;
  const totalTok = ((data && data.total_tokens_in) || 0) + ((data && data.total_tokens_out) || 0);
  const range = data && data.since
    ? `${(data.since || '').slice(0, 10)} – ${(data.until || '').slice(0, 10) || 'jetzt'}`
    : 'Gesamter Zeitraum';

  // Headline total — the number users scan for first.
  const headline = `
    <div style="display:flex;align-items:flex-end;justify-content:space-between;
                background:var(--bg-100);border:1px solid var(--border-100);border-radius:9px;
                padding:10px 13px;margin-bottom:12px">
      <div>
        <div style="font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--text-400);margin-bottom:2px">Gesamtkosten</div>
        <div style="font-size:22px;font-weight:700;color:var(--text-100);line-height:1">${esc(_costFmt(total))}</div>
      </div>
      <div style="text-align:right;font-size:11px;color:var(--text-400);line-height:1.5">
        <div>${esc(range)}</div>
        <div>${totalCalls.toLocaleString('de-DE')} Aufrufe · ${esc(_tokFmt(totalTok))} Tokens</div>
      </div>
    </div>`;

  if (!buckets.length) {
    body.innerHTML = headline + `
      <div style="color:var(--text-300);text-align:center;padding:18px 8px;font-size:12px">
        Keine Kosten in diesem Zeitraum.<br>
        <span style="color:var(--text-400);font-size:11px">Lokale Modelle sind kostenlos und erscheinen nur, wenn sie Tokens verbraucht haben.</span>
      </div>`;
    return;
  }

  const maxCost = Math.max(...buckets.map((b) => Number(b.cost) || 0), 1e-9);

  const rows = buckets.map((b, i) => {
    const models = (b.by_model || []);
    const id = `cost-uc-${i}`;
    const color = _COST_PALETTE[i % _COST_PALETTE.length];
    const cost = Number(b.cost) || 0;
    const pctOfTotal = total > 0 ? (cost / total * 100) : 0;
    const barW = Math.max(2, (cost / maxCost) * 100);
    const modelRows = models.map((m) => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;
                  padding:4px 0 4px 22px;font-size:11px;color:var(--text-300)">
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1"
              title="${esc(m.model)}">${esc(modelShortName(m.model) || m.model || '—')}</span>
        <span style="white-space:nowrap;color:var(--text-400);font-variant-numeric:tabular-nums">${esc(_tokFmt((m.tokens_in||0)+(m.tokens_out||0)))} tok</span>
        <span style="white-space:nowrap;color:var(--text-200);font-variant-numeric:tabular-nums;min-width:54px;text-align:right">${esc(_costFmt(m.cost))}</span>
        <span style="white-space:nowrap;color:var(--text-400);min-width:32px;text-align:right">${b.calls ? Math.round((m.calls/b.calls)*100) : 0}%</span>
      </div>`).join('');
    return `
      <div id="${id}-row" data-open="0">
        <div onclick="toggleCostUseCase('${id}')" class="cost-uc-head"
             style="display:flex;align-items:center;gap:8px;padding:7px 4px;cursor:pointer;border-radius:7px">
          <span id="${id}-arr" style="color:var(--text-400);font-size:9px;display:inline-block;width:10px;
                transition:transform .15s ease;flex-shrink:0">▶</span>
          <span style="width:8px;height:8px;border-radius:2px;background:${color};flex-shrink:0"></span>
          <span style="flex:1;min-width:0">
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
              <span style="font-size:12.5px;color:var(--text-100);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(b.use_case)}</span>
              <span style="font-size:12.5px;color:var(--text-100);font-weight:600;white-space:nowrap;margin-left:8px;font-variant-numeric:tabular-nums">${esc(_costFmt(b.cost))}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              <div style="flex:1;height:5px;background:var(--bg-200);border-radius:999px;overflow:hidden">
                <div style="height:100%;width:${barW.toFixed(1)}%;background:${color};border-radius:999px"></div>
              </div>
              <span style="font-size:10.5px;color:var(--text-400);white-space:nowrap;font-variant-numeric:tabular-nums">${pctOfTotal.toFixed(0)}% · ${b.calls}×</span>
            </div>
          </span>
        </div>
        <div id="${id}" style="display:none;padding:2px 0 8px">${modelRows}</div>
      </div>`;
  }).join('');

  body.innerHTML = headline + `<div style="display:flex;flex-direction:column;gap:1px">${rows}</div>`;
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
        <h2>Lokale Provider-Warteschlange</h2>
        <button class="modal-close" onclick="document.getElementById('queue-modal').remove()" style="margin-left:auto">&times;</button>
      </div>
      <div class="modal-body" id="queue-modal-body" style="overflow-y:auto"><em style="color:var(--text-muted)">Wird geladen…</em></div>
      <div class="modal-footer" style="color:var(--text-muted);font-size:12px">
        Provider mit <code>max_concurrent &gt; 0</code> in <code>config.json</code> serialisieren ihre Aufrufe, damit
        mehrere Chats und Hintergrund-Tasks nicht um dasselbe lokale LLM-Gateway konkurrieren. FIFO.
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
    body.innerHTML = `<div style="color:var(--text-muted)">Keine Provider für die Warteschlange konfiguriert. Setzen Sie <code>max_concurrent</code> in <code>config.json</code> → <code>providers.&lt;name&gt;</code>, um sie zu aktivieren.</div>`;
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
    const stateLbl = state === 'running' ? 'laufende' : 'wartende';
    return `<button class="queue-cancel-btn"
              onclick="cancelQueueTicket('${esc(t.id)}', '${state}')"
              title="Dieses ${stateLbl} Ticket abbrechen (Admin)"
              style="background:var(--danger,#c0392b);color:#fff;border:0;padding:2px 8px;
                     border-radius:6px;font-size:11px;cursor:pointer">
              Abbrechen
            </button>`;
  };
  const rows = entries.map(([pname, p]) => {
    const active = (p.active || []).map(t => `
      <tr>
        <td><span class="pill" style="background:var(--accent-brand);color:#fff;padding:1px 8px;border-radius:10px;font-size:11px">aktiv</span></td>
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
      ? `<tr><td colspan="7" style="color:var(--text-muted);font-style:italic">Inaktiv</td></tr>` : '';
    return `
      <div style="margin-bottom:20px">
        <h4 style="margin:0 0 6px 0;font-size:14px">
          <code>${esc(pname)}</code>
          <span style="color:var(--text-muted);font-weight:normal;font-size:12px">
            · ${p.active_count}/${p.max_concurrent} aktiv${p.waiting_count ? ` · ${p.waiting_count} wartend` : ''}
          </span>
        </h4>
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead>
            <tr style="border-bottom:1px solid var(--border);color:var(--text-muted);text-align:left">
              <th style="padding:4px">Status</th>
              <th style="padding:4px">Bezeichnung</th>
              <th style="padding:4px">Modell</th>
              <th style="padding:4px">Session</th>
              <th style="padding:4px">Agent</th>
              <th style="padding:4px">Alter</th>
              <th style="padding:4px;text-align:right">${isAdmin ? 'Aktion' : ''}</th>
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
    ? 'Diesen LAUFENDEN LLM-Aufruf abbrechen? Der aktive Chat wird abgebrochen.'
    : 'Dieses wartende Ticket abbrechen?';
  if (!await showConfirm(confirmMsg)) return;
  try {
    const resp = await fetch(`${BASE_URL}/v1/queue/cancel`, {
      method: 'POST', headers: {'Content-Type':'application/json', ...API._headers()},
      body: JSON.stringify({ticket_id: ticketId, reason: 'cancelled from queue modal'}),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      showToast(`Abbrechen fehlgeschlagen: ${data.error || resp.status}`, true);
      return;
    }
    showToast(`Warteschlangen-Ticket abgebrochen (${data.state || '?'})`);
    // Refresh modal + pill
    QueueMonitor._mode = 'fast';
    QueueMonitor._tick();
  } catch (e) {
    showToast(`Abbruchfehler: ${e}`, true);
  }
}
