// monitors.js — runtime status monitor classes (Connection/Mempalace/Warmup/Queue/Quota) + their detail modals. Split from init.js (Tier F Phase 4). Global <script>, no modules.

// ═══ Connection Health Monitor ═══
const ConnectionMonitor = {
  _interval: null,
  _connected: false,
  _pollMs: 10000,
  _failCount: 0,

  start() {
    this._check();
    this._reschedule();
  },

  // Poll fast (2s) while the server is still warming (config not yet loaded) so
  // the "wird bereit" → "verbunden" transition is snappy; back off to 10s once
  // ready. Re-evaluated after each check.
  _reschedule() {
    if (this._interval) clearInterval(this._interval);
    const ms = state.modelsConfigReady ? 10000 : 2000;
    this._interval = setInterval(() => {
      this._check();
      const want = state.modelsConfigReady ? 10000 : 2000;
      if (want !== ms) this._reschedule();
    }, ms);
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
        // Connected but the model config may still be warming up after a
        // (re)start — re-fetch it until it arrives so model-locality (and the
        // GDPR scan gate that depends on it) becomes reliable. Refresh the dot.
        if (!state.modelsConfigReady) this._refreshConfig();
        else this._paintDot(true);
      } else {
        this._setConnected(false);
      }
    } catch {
      this._failCount++;
      if (this._failCount >= 2) this._setConnected(false);
    }
  },

  async _refreshConfig() {
    try {
      const cfg = await API.getModelsConfig();
      if (cfg && cfg.models && Object.keys(cfg.models).length) {
        state.modelsConfig = cfg;
        state.modelsConfigReady = true;
      }
    } catch { /* keep warming */ }
    this._paintDot(true);
  },

  // Paint the connection dot: amber "wird bereit" while connected-but-warming,
  // green "verbunden" once the model config is loaded, red when disconnected.
  _paintDot(connected) {
    const dot = document.getElementById('status-connection-dot');
    const wrap = document.getElementById('status-connection');
    if (!dot || !wrap) return;
    if (!connected) {
      dot.className = 'connection-dot disconnected';
      wrap.title = 'Server: getrennt';
    } else if (!state.modelsConfigReady) {
      dot.className = 'connection-dot connecting';
      wrap.title = 'Server wird bereit … (Modelle werden geladen)';
    } else {
      dot.className = 'connection-dot connected';
      wrap.title = 'Server: verbunden';
    }
  },

  _setConnected(connected) {
    if (this._connected === connected) {
      // Even on no transition, keep the dot in sync (warming → ready).
      if (connected) this._paintDot(true);
      return;
    }
    this._connected = connected;
    state.connected = connected;
    this._paintDot(connected);
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

// Combined infrastructure modal — warm-session pool + provider queue in one
// dialog, opened from the single status-bar infra icon. Both sections are live
// updated by their monitors' _render() while the modal is open.
function openInfraModal() {
  if (document.getElementById('infra-modal')) { document.getElementById('infra-modal').remove(); return; }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.id = 'infra-modal';
  overlay.style.display = 'flex';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="modal-content" style="max-width:720px;width:92%;max-height:85vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;padding:20px 24px 0;gap:12px">
      <h2 style="margin:0;font-size:18px;font-weight:600;color:var(--text-000)">Infrastruktur</h2>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()" style="margin-left:auto">&times;</button>
    </div>
    <div style="flex:1;overflow-y:auto;padding:8px 24px 24px">
      <div style="display:flex;align-items:center;gap:8px;margin:12px 0 4px">
        <h3 style="margin:0;font-size:14px;font-weight:600;color:var(--text-100)">Warm-Session-Pool</h3>
        ${helpIcon('Warmup hält Modelle vorgeladen, um die Latenz bis zum ersten Token zu verringern. Der Modus wird pro Modell im Tab „Modelle“ festgelegt:\n\nfull: lädt System-Prompt + Werkzeuge in den KV-Cache vor (~5-6 s bis zur ersten Antwort).\n\nminimal: lädt nur die Modellgewichte, keinen Prompt und keine Werkzeuge (~10-15 s bis zur ersten Antwort).\n\nVoll vorgeladene Modelle teilen sich den GPU-Speicher — bei Engpässen verdrängen sie sich gegenseitig.')}
        <span id="warmpool-modal-hint" style="font-size:12px;color:var(--text-400)"></span>
      </div>
      <div id="warmpool-body">
        <div style="color:var(--text-400);padding:16px;text-align:center">Wird geladen...</div>
      </div>
      <h3 style="margin:20px 0 4px;font-size:14px;font-weight:600;color:var(--text-100)">Lokale Provider-Warteschlange</h3>
      <div id="queue-modal-body"><em style="color:var(--text-muted)">Wird geladen…</em></div>
      <div style="color:var(--text-muted);font-size:12px;margin-top:8px">
        Provider mit <code>max_concurrent &gt; 0</code> in <code>config.json</code> serialisieren ihre Aufrufe (FIFO), damit
        mehrere Chats und Hintergrund-Tasks nicht um dasselbe lokale LLM-Gateway konkurrieren.
      </div>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  renderWarmPoolModalBody();
  renderQueueModalBody();
  // Kick the queue monitor into fast mode while the modal is open.
  QueueMonitor._mode = 'fast';
  QueueMonitor._tick();
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
    // Status-bar infra indicator + modal body live-refresh
    this._renderPoolIndicator();
    if (document.getElementById('infra-modal')) {
      renderWarmPoolModalBody();
    }
  },
  // Compute this monitor's contribution to the combined infra dot, then let
  // the shared updater decide the icon's visibility + colour.
  poolSummary() {
    const warmupModels = Object.entries(this.states).filter(([_, st]) => st.enabled);
    if (!warmupModels.length) return null;
    let ready = 0, target = 0, building = 0, failed = 0, anyWarm = false;
    for (const [_, st] of warmupModels) {
      ready += st.ready ?? 0;
      target += st.target ?? 0;
      building += st.building ?? 0;
      if (st.state === 'failed') failed++;
      if (st.state === 'warm') anyWarm = true;
    }
    return { count: warmupModels.length, ready, target, building, failed, anyWarm,
             warming: building > 0 || this.anyWarming };
  },
  _renderPoolIndicator() {
    updateInfraIndicator();
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
  // This monitor's contribution to the combined infra dot.
  queueSummary() {
    const entries = Object.entries(this.providers);
    if (!entries.length) return null;
    let active = 0, waiting = 0, capacity = 0;
    for (const [_, p] of entries) {
      active += p.active_count || 0;
      waiting += p.waiting_count || 0;
      capacity += p.max_concurrent || 0;
    }
    return { count: entries.length, active, waiting, capacity };
  },
  _render() {
    updateInfraIndicator();
    if (document.getElementById('infra-modal')) renderQueueModalBody();
  },
};

// ─── Combined infra indicator (warm pool + provider queue) ───
// One status-bar icon whose dot colour summarises both monitors. Admin-only.
// Click opens the combined modal (openInfraModal).
function updateInfraIndicator() {
  const wrap = document.getElementById('status-infra');
  if (!wrap) return;
  if ((state.authUser?.role || 'admin') !== 'admin') { wrap.style.display = 'none'; return; }
  const pool = (typeof WarmupMonitor !== 'undefined' && WarmupMonitor.poolSummary) ? WarmupMonitor.poolSummary() : null;
  const queue = (typeof QueueMonitor !== 'undefined' && QueueMonitor.queueSummary) ? QueueMonitor.queueSummary() : null;
  if (!pool && !queue) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'flex';
  // Dot priority: failed > warming/waiting > warm/active > idle.
  let dotCls = 'idle';
  if (pool && pool.failed && !pool.anyWarm) dotCls = 'failed';
  else if ((pool && pool.warming) || (queue && queue.waiting > 0)) dotCls = 'warming';
  else if ((pool && pool.ready > 0) || (queue && queue.active > 0)) dotCls = 'warm';
  const dot = document.getElementById('status-infra-dot');
  if (dot) dot.className = 'warmup-dot ' + dotCls;
  const parts = [];
  if (pool) parts.push(`Warm-Pool ${pool.ready}/${pool.target} bereit`
    + (pool.building ? ` (${pool.building} im Aufbau)` : '')
    + (pool.failed ? ` (${pool.failed} fehlgeschlagen)` : ''));
  if (queue) parts.push(`Warteschlange ${queue.active} aktiv`
    + (queue.waiting ? ` / ${queue.waiting} wartend` : '')
    + ` (Kapazität ${queue.capacity})`);
  wrap.title = parts.join(' · ') + ' — für Details klicken';
}

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
  // Echter modaler Dialog im General-Settings-Look (modal-overlay +
  // modal-content x-wide + modal-header/-body) — kein an die Pille
  // verankertes Popover mehr (User-Wunsch 9.283.6).
  const existing = document.getElementById('quota-modal');
  if (existing) { existing.remove(); return; }
  const overlay = document.createElement('div');
  overlay.id = 'quota-modal';
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  const content = document.createElement('div');
  content.className = 'modal-content x-wide';
  const isAdmin = (state.authUser?.role || 'admin') === 'admin';
  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Plan-Nutzung</span>
      ${isAdmin ? `<button onclick="document.getElementById('quota-modal')?.remove();openGeneralSettings();setTimeout(()=>{const t=document.querySelector('.modal-tab[onclick*=&quot;quotas&quot;]');if(t)switchGeneralTab('quotas',t);},50);"
              style="margin-left:auto;background:transparent;border:1px solid var(--border-100);color:var(--text-300);
                     border-radius:6px;padding:2px 10px;cursor:pointer;font-size:12px"
              title="Kontingent-Einstellungen öffnen">Einstellungen &#x2192;</button>` : ''}
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()" ${isAdmin ? '' : 'style="margin-left:auto"'}>&times;</button>
    </div>
    <div class="modal-body" id="quota-modal-scroll" style="overflow-y:auto">
      <div id="quota-modal-body"><div style="color:var(--text-300);text-align:center;padding:12px">Wird geladen…</div></div>
      <div id="coding-plans-section" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border-100)"></div>
      <div id="cost-breakdown-section" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border-100)"></div>
    </div>`;
  overlay.appendChild(content);
  document.body.appendChild(overlay);

  // Cost breakdown: window selector + per-use-case × per-model table. Lazy —
  // fetched on open and whenever the window changes, NOT on the 30s quota poll.
  renderCostBreakdownSection();
  loadCodingPlansSection();
  renderQuotaModalBody();
  QuotaMonitor.refresh().catch(() => {});
}

// Anchor the Plan-usage popover to the status-bar pill AND size its scroll
// region to the real space available, so the scrollbar is always fully on-screen
// and reachable. Prefers placing above the pill; falls back below. Called on
// open, after the async breakdown loads, on expand/collapse, and on resize.
function repositionQuotaModal() {
  // Seit 9.283.6 ist die Plan-Nutzung ein zentrierter Standard-Modal
  // (modal-overlay) — CSS übernimmt Position/Größe, hier gibt es nichts mehr
  // zu verankern. Bleibt als No-op erhalten, weil die Async-Render-Pfade
  // (Kosten-Tabelle, Plan-Sektion) sie nach dem Laden weiterhin aufrufen.
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
  // Tabellen-Layout: die Modell-Zeilen eines Anwendungsfalls sind <tr>s mit
  // data-uc="<id>" (keine Container-Div mehr).
  const r = document.getElementById(id + '-row');
  const open = r ? r.getAttribute('data-open') === '1' : false;
  document.querySelectorAll(`tr[data-uc="${id}"]`).forEach((tr) => {
    tr.style.display = open ? 'none' : '';
  });
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

  // Full cost picture: verrechnet (real, flat-plan rows = $0) · API-Listenpreis
  // (same usage at the models' list rates) · Flatrate-Ersparnis (difference) ·
  // Caching-Ersparnis (cache-hit tokens at full input rate minus cache rate).
  const totalList = (data && data.total_cost_list) || 0;
  const cacheSave = (data && data.total_cache_savings) || 0;
  const totalIn2 = (data && data.total_tokens_in) || 0;
  const totalOut2 = (data && data.total_tokens_out) || 0;
  const totalCr = (data && data.total_cache_read_tokens) || 0;
  const flatSave = Math.max(0, totalList - total);
  const _statCell = (label, value, opts) => `
      <div style="min-width:96px">
        <div style="font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--text-400);margin-bottom:2px" ${opts && opts.title ? `title="${opts.title}"` : ''}>${label}</div>
        <div style="font-size:${opts && opts.big ? 22 : 14}px;font-weight:${opts && opts.big ? 700 : 600};color:${(opts && opts.color) || 'var(--text-100)'};line-height:1.15">${value}</div>
      </div>`;
  const headline = `
    <div style="background:var(--bg-100);border:1px solid var(--border-100);border-radius:9px;
                padding:10px 13px;margin-bottom:12px">
      <div style="display:flex;flex-wrap:wrap;gap:14px 22px;align-items:flex-end">
        ${_statCell('Verrechnete Kosten', esc(_costFmt(total)), { big: true, title: 'Tatsächlich abgerechnete Kosten — Modelle mit Flatrate/Coding-Plan buchen 0 $.' })}
        ${_statCell('API-Kosten (Listenpreis)', esc(_costFmt(totalList)), { title: 'Dieselbe Nutzung zu den API-Listenpreisen der Modelle — was ohne Coding-/Vibe-Flatrates fällig wäre. Prompt-Caching ist darin bereits eingerechnet.' })}
        ${_statCell('Flatrate-Ersparnis', esc(_costFmt(flatSave)), { color: flatSave > 0.005 ? 'var(--success,#16a34a)' : 'var(--text-400)', title: 'API-Listenpreis minus verrechnete Kosten.' })}
        ${_statCell('Caching-Ersparnis', esc(_costFmt(cacheSave)), { color: cacheSave > 0.005 ? 'var(--success,#16a34a)' : 'var(--text-400)', title: 'Was die ⚡-gecachten Tokens zum vollen Eingabe-Tarif gekostet hätten, minus dem Cache-Tarif (~0,1×). Ohne Prompt-Caching läge der Listenpreis entsprechend höher.' })}
      </div>
      <div style="display:flex;justify-content:space-between;gap:10px;margin-top:8px;padding-top:7px;border-top:1px solid var(--border-100);font-size:11px;color:var(--text-400)">
        <div>Tokens: <span style="color:var(--text-300)">${esc(_tokFmt(totalIn2))} ein</span> · <span style="color:var(--text-300)">${esc(_tokFmt(totalOut2))} aus</span> · <span style="color:#10b981">⚡ ${esc(_tokFmt(totalCr))} gecached</span></div>
        <div>${esc(range)} · ${totalCalls.toLocaleString('de-DE')} Aufrufe</div>
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

  // Spalten-Tabelle (statt Balken + verschachtelter Inline-Werte): eine Zeile
  // pro Anwendungsfall (aufklappbar → Modell-Zeilen mit denselben Spalten).
  // Spaltenreihenfolge = überall gleich: Tokens (ein/aus/⚡) → API-Kosten →
  // Verrechnet → ⚡-Ersparnis.
  const NUM = 'text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;padding:4px 6px';
  const TH = `font-size:10px;letter-spacing:.03em;text-transform:uppercase;color:var(--text-400);
              text-align:right;padding:4px 6px;white-space:nowrap;border-bottom:1px solid var(--border-200)`;
  const cell = (v, extra) => `<td style="${NUM};${extra || ''}">${v}</td>`;
  const numRow = (o, cls) => [
    cell(`${o.calls}×`, 'color:var(--text-400)'),
    cell(esc(_tokFmt(o.tokens_in || 0)), `color:var(--text-${cls ? '300' : '200'})`),
    cell(esc(_tokFmt(o.tokens_out || 0)), `color:var(--text-${cls ? '300' : '200'})`),
    cell((o.cache_read_tokens || 0) > 0 ? `<span style="color:#10b981">${esc(_tokFmt(o.cache_read_tokens))}</span>` : '—', ''),
    cell(esc(_costFmt(o.cost_list != null ? o.cost_list : o.cost)), 'color:var(--text-200)'),
    cell(`<b>${esc(_costFmt(o.cost))}</b>`, 'color:var(--text-100)'),
    cell(Number(o.cache_savings) > 0.0005 ? `<span style="color:#10b981">−${esc(_costFmt(o.cache_savings))}</span>` : '—', ''),
  ].join('');

  const bodyRows = buckets.map((b, i) => {
    const id = `cost-uc-${i}`;
    const color = _COST_PALETTE[i % _COST_PALETTE.length];
    const modelRows = (b.by_model || []).map((m) => `
      <tr class="cost-model-row" data-uc="${id}" style="display:none;font-size:11px;color:var(--text-300)">
        <td style="padding:3px 6px 3px 30px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px"
            title="${esc(m.model)}">${esc(modelShortName(m.model) || m.model || '—')}</td>
        ${numRow(m, true)}
      </tr>`).join('');
    return `
      <tr onclick="toggleCostUseCase('${id}')" style="cursor:pointer;font-size:12px" class="cost-uc-head" id="${id}-row" data-open="0">
        <td style="padding:5px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px">
          <span id="${id}-arr" style="color:var(--text-400);font-size:9px;display:inline-block;width:10px;transition:transform .15s ease">▶</span>
          <span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${color};margin:0 5px 0 1px"></span>
          <span style="color:var(--text-100);font-weight:500" title="${esc(b.use_case)}">${esc(b.use_case)}</span>
        </td>
        ${numRow(b, false)}
      </tr>
      ${modelRows}`;
  }).join('');

  body.innerHTML = headline + `
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="${TH};text-align:left">Anwendungsfall / Modell</th>
          <th style="${TH}">Aufrufe</th>
          <th style="${TH}">Token ein</th>
          <th style="${TH}">Token aus</th>
          <th style="${TH}" title="Prompt-Cache-Treffer (zum ~0,1×-Tarif)">⚡ Gecached</th>
          <th style="${TH}" title="Zum API-Listenpreis der Modelle — was ohne Flatrates fällig wäre">API-Kosten</th>
          <th style="${TH}" title="Tatsächlich abgerechnet (Flatrate-Modelle: 0 $)">Verrechnet</th>
          <th style="${TH}" title="Ersparnis durch Prompt-Caching (voller Eingabe-Tarif minus Cache-Tarif)">⚡-Ersparnis</th>
        </tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>`;
}

// ── Coding-Pläne & API-Guthaben (Plan-Nutzung-Popover) ───────────────────────
// Geschätzt aus dem eigenen cost_log (keine Anbieter-Quota-API). Nur Pläne mit
// verknüpften Modellen erscheinen (Verknüpfung: Einstellungen → Modelle →
// „Coding-Plan / Konto").
function _cpTok(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.', ',') + ' M';
  if (n >= 1e3) return Math.round(n / 1e3) + 'k';
  return String(n);
}
// Countdown bis zum Fenster-Reset: Minuten < 90min, Stunden+Minuten < 48h,
// sonst Tage (aufgerundet — "in 27 Tagen" heißt: am 27. Tag ist Reset).
function _cpEta(s) {
  if (s == null || s <= 0) return '';
  const m = Math.round(s / 60);
  if (m < 90) return `in ${m} min`;
  if (s < 48 * 3600) {
    const h = Math.floor(s / 3600), rm = Math.round((s % 3600) / 60);
    return rm ? `in ${h} h ${rm} min` : `in ${h} h`;
  }
  return `in ${Math.ceil(s / 86400)} Tagen`;
}
async function loadCodingPlansSection() {
  const sec = document.getElementById('coding-plans-section');
  if (!sec) return;
  try {
    const data = await API.get('/v1/plans/usage');
    renderCodingPlansSection(data);
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(repositionQuotaModal);
  } catch (e) {
    sec.innerHTML = `<div style="color:var(--error);font-size:11px">Coding-Pläne: ${esc(String(e && e.message || e))}</div>`;
  }
}
function renderCodingPlansSection(data) {
  const sec = document.getElementById('coding-plans-section');
  if (!sec) return;
  const isAdmin = (state.authUser?.role || 'admin') === 'admin';
  const plans = (data && data.plans) || [];
  // Farbe = HOCHRECHNUNG, nicht bloßer Füllstand: rot wenn das Fenster bei
  // gleichem Tempo deutlich überlaufen würde (Projektion > 130 %) ODER fast
  // leer ist (≥ 90 %); gelb wenn die Projektion über 100 % liegt (Tempo müsste
  // sinken) ODER Füllstand ≥ 70 %; sonst grün = bei gleichem Nutzungsausmaß
  // bleibt man im Kontingent.
  const bar = (pct, projected) => {
    const p = Math.min(100, Math.max(0, pct || 0));
    const pr = (projected == null) ? p : projected;
    const color = (p >= 90 || pr > 130) ? 'var(--error)'
      : (p >= 70 || pr > 100) ? 'var(--warning)' : 'var(--success)';
    // Prognose-Segment: halbtransparente Fortsetzung vom Ist-Stand bis zum
    // projizierten Stand am Fensterende (bei gleichem Tempo), gedeckelt bei
    // 100 % — Überlauf zeigt die Farbe + der ⇥-Marker am Balkenende.
    const ghostW = Math.max(0, Math.min(100, pr) - p);
    const overflow = pr > 100;
    return `<div style="flex:1;height:5px;background:var(--bg-200);border-radius:999px;overflow:hidden;min-width:60px;display:flex;position:relative">
      <div style="height:100%;width:${p}%;background:${color};flex-shrink:0"></div>
      ${ghostW > 0.5 ? `<div style="height:100%;width:${ghostW}%;background:${color};opacity:.32;flex-shrink:0"></div>` : ''}
      ${overflow ? `<div style="position:absolute;right:0;top:0;bottom:0;width:3px;background:var(--error)"></div>` : ''}
    </div>`;
  };
  const rows = plans.map((p) => {
    const winRows = p.windows.map((w) => {
      if (w.kind === 'credit') {
        return `<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-300);padding:2px 0">
          <span style="width:44px;flex-shrink:0">${esc(w.label)}</span>
          ${bar(w.pct, null)}
          <span style="font-variant-numeric:tabular-nums;white-space:nowrap" title="verbraucht seit Aufladung ${esc(w.anchor)}${w.days_left_est != null ? ' · reicht bei gleichem Tempo noch ~' + w.days_left_est + ' Tage' : ''}">
            $${(w.used_usd || 0).toFixed(2)} / $${(w.balance_usd || 0).toFixed(2)} · <b style="color:var(--text-200)">$${(w.remaining_usd || 0).toFixed(2)} frei</b>${w.days_left_est != null ? ` <span style="color:var(--text-400)">(~${w.days_left_est} Tage)</span>` : ''}</span>
          ${isAdmin ? `<span style="white-space:nowrap"><input type="number" step="1" min="0" placeholder="$" id="cp-topup-${esc(p.id)}"
              style="width:44px;padding:1px 4px;font-size:10px;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-000);color:var(--text-200)"
              title="Neues Guthaben nach Aufladung in $ — setzt auch das Aufladedatum auf heute">
            <button onclick="topupCodingPlan('${esc(p.id)}')" style="font-size:10px;padding:1px 5px;cursor:pointer;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-100);color:var(--text-300)" title="Guthaben neu setzen (Aufladung)">⟳</button></span>` : ''}
        </div>`;
      }
      const eta = _cpEta(w.resets_in_s);
      const projTitle = w.projected_pct != null
        ? ` · bei gleichem Tempo ~${w.projected_pct.toFixed(0)} % am Fensterende${w.projected_pct > 100 ? ' (Überlauf!)' : ' (im Rahmen)'}`
        : '';
      return `<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-300);padding:2px 0">
        <span style="width:44px;flex-shrink:0">${esc(w.label)}</span>
        ${bar(w.pct, w.projected_pct)}
        <span style="font-variant-numeric:tabular-nums;white-space:nowrap" title="geschätzt: ${(w.used_est || 0).toLocaleString('de-DE')} von ${(w.limit_tokens || 0).toLocaleString('de-DE')} Tokens${w.resets_at ? ' · Reset ' + esc(w.resets_at) : ''}${projTitle}">
          ~${w.pct == null ? '—' : w.pct.toFixed(0) + ' %'}${w.projected_pct != null && Math.abs(w.projected_pct - (w.pct || 0)) >= 1 ? ` <span style="color:var(--text-400)">→ ~${w.projected_pct.toFixed(0)} %</span>` : ''} · ${_cpTok(w.used_est || 0)} / ${_cpTok(w.limit_tokens || 0)}</span>
        ${eta ? `<span style="color:var(--text-400);white-space:nowrap" title="${w.resets_at ? 'Reset ' + esc(w.resets_at) : ''}">↻ ${esc(eta)}</span>` : ''}
        ${isAdmin ? `<span style="white-space:nowrap"><input type="number" step="1" min="1" max="100" placeholder="%" id="cp-cal-${esc(p.id)}-${esc(w.kind)}"
            style="width:36px;padding:1px 4px;font-size:10px;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-000);color:var(--text-200)"
            title="Kalibrieren: aktuellen %-Wert vom Anbieter-Dashboard eintragen — das Limit wird daraus neu berechnet">
          <button onclick="calibrateCodingPlan('${esc(p.id)}','${esc(w.kind)}')" style="font-size:10px;padding:1px 5px;cursor:pointer;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-100);color:var(--text-300)" title="Limit aus Dashboard-% neu berechnen">✓</button></span>` : ''}
      </div>`;
    }).join('');
    return `<div style="padding:6px 0;border-bottom:1px solid var(--border-100)">
      <div style="display:flex;align-items:baseline;gap:8px">
        <span style="font-size:12px;font-weight:500;color:var(--text-100)" title="Modelle: ${esc(p.models.join(', '))}${p.quota_note ? '\n' + esc(p.quota_note) : ''}">${esc(p.name)}</span>
        <span style="font-size:10.5px;color:var(--text-400)">${esc(p.price)}</span>
        <span style="margin-left:auto;font-size:10px;color:var(--text-500)" title="zuletzt kalibriert">${esc(p.calibrated_at)}</span>
        ${isAdmin ? `<button onclick="openCodingPlanForm('${esc(p.id)}')" style="font-size:10px;padding:1px 5px;cursor:pointer;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-100);color:var(--text-300)" title="Plan bearbeiten">✎</button>
        <button onclick="deleteCodingPlan('${esc(p.id)}')" style="font-size:10px;padding:1px 5px;cursor:pointer;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-100);color:var(--error)" title="Plan löschen (Modell-Verknüpfungen werden entfernt)">×</button>` : ''}
      </div>
      ${winRows}
    </div>`;
  }).join('');
  window._cpLastPlans = plans;   // Rohdaten für das Bearbeiten-Formular
  sec.innerHTML = `
    <div style="display:flex;align-items:center;margin-bottom:6px">
      <div style="font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:var(--text-400)"
           title="Geschätzt aus dem eigenen Nutzungs-Ledger — die Anbieter haben keine Quota-API. Mit dem %-Feld gegen das echte Anbieter-Dashboard kalibrieren.">Coding-Pläne &amp; API-Guthaben <span style="font-weight:400;text-transform:none">(geschätzt)</span></div>
      ${isAdmin ? `<button onclick="openCodingPlanForm('')" style="margin-left:auto;font-size:10px;padding:1px 7px;cursor:pointer;border:1px solid var(--border-100);border-radius:4px;background:var(--bg-100);color:var(--text-300)">+ Plan</button>` : ''}
    </div>
    ${rows || '<div style="font-size:11px;color:var(--text-400)">Keine Pläne mit verknüpften Modellen. Verknüpfung: Einstellungen → Modelle → „Coding-Plan / Konto".</div>'}`;
}
async function calibrateCodingPlan(planId, kind) {
  const inp = document.getElementById(`cp-cal-${planId}-${kind}`);
  const pct = parseFloat(inp?.value);
  if (!pct || pct <= 0 || pct > 100) { showToast('Bitte den %-Wert vom Anbieter-Dashboard eintragen (1–100).', true); return; }
  try {
    const r = await API.post('/v1/plans/calibrate', { plan_id: planId, window_kind: kind, dashboard_pct: pct });
    showToast(`Kalibriert — neues Limit ${_cpTok(r.new_limit_tokens)} Tokens.`);
    loadCodingPlansSection();
  } catch (e) { showToast('Kalibrierung fehlgeschlagen: ' + (e.message || e), true); }
}
async function topupCodingPlan(planId) {
  const inp = document.getElementById(`cp-topup-${planId}`);
  const bal = parseFloat(inp?.value);
  if (isNaN(bal) || bal < 0) { showToast('Bitte das neue Guthaben in $ eintragen.', true); return; }
  try {
    await API.post('/v1/plans/calibrate', { plan_id: planId, balance_usd: bal });
    showToast(`Guthaben auf $${bal.toFixed(2)} gesetzt (Zähler ab heute).`);
    loadCodingPlansSection();
  } catch (e) { showToast('Aufladung fehlgeschlagen: ' + (e.message || e), true); }
}
function openCodingPlanForm(planId) {
  const p = (window._cpLastPlans || []).find((x) => x.id === planId) || null;
  const w = (kind) => (p?.windows || []).find((x) => x.kind === kind) || {};
  const ov = document.createElement('div');
  ov.className = 'modal-overlay';
  ov.id = 'coding-plan-form';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  const F = 'width:100%;padding:4px 8px;border:1px solid var(--border-100);border-radius:6px;font-size:12px;background:var(--bg-000);color:var(--text-100)';
  const L = 'font-size:11px;color:var(--text-400);display:block;margin:8px 0 2px';
  ov.innerHTML = `<div class="modal-content" style="max-width:560px;width:92%" onclick="event.stopPropagation()">
    <div class="modal-header"><h2 style="font-size:15px">${p ? 'Plan bearbeiten' : 'Plan anlegen'}</h2>
      <button class="modal-close" onclick="document.getElementById('coding-plan-form').remove()" style="margin-left:auto">&times;</button></div>
    <div class="modal-body" style="font-size:12px">
      <label style="${L}">Name</label><input id="cpf-name" style="${F}" value="${esc(p?.name || '')}" placeholder="z. B. Kilo API-Guthaben">
      <label style="${L}">Typ</label>
      <select id="cpf-type" style="${F}" onchange="document.getElementById('cpf-flat').style.display=this.value==='flat'?'':'none';document.getElementById('cpf-credit').style.display=this.value==='credit'?'':'none'">
        <option value="flat"${(p?.type || 'flat') === 'flat' ? ' selected' : ''}>Flat (Abo mit Zeitfenster-Quotas — $0 verrechnet)</option>
        <option value="credit"${p?.type === 'credit' ? ' selected' : ''}>Credit (API-Guthaben — echte Token-Abrechnung)</option>
      </select>
      <label style="${L}">Preis (Anzeige)</label><input id="cpf-price" style="${F}" value="${esc(p?.price || '')}" placeholder="z. B. $18/Monat">
      <label style="${L}">Notiz</label><input id="cpf-note" style="${F}" value="${esc(p?.quota_note || '')}" placeholder="Quota-Hinweise">
      <div id="cpf-flat" style="display:${(p?.type || 'flat') === 'flat' ? '' : 'none'}">
        <label style="${L}">Limit 5h-Fenster (Tokens, leer = kein Fenster)</label><input id="cpf-l5h" type="number" style="${F}" value="${w('session_5h').limit_tokens || w('rolling_5h').limit_tokens || ''}">
        <label style="${L}">Limit Woche (Tokens)</label><input id="cpf-l7d" type="number" style="${F}" value="${w('weekly').limit_tokens || w('rolling_7d').limit_tokens || ''}">
        <label style="${L}">Limit Monat (Tokens)</label><input id="cpf-lmon" type="number" style="${F}" value="${w('monthly').limit_tokens || ''}">
        <label style="${L}">Monats-Zyklusstart (YYYY-MM-DD)</label><input id="cpf-anchor-mon" style="${F}" value="${esc(w('monthly').anchor || '')}" placeholder="nur bei Monats-Limit">
        <label style="${L}">Cache-Token-Gewicht (Z.ai zählt ~0.67)</label><input id="cpf-wcached" type="number" step="0.01" style="${F}" value="${p?.count?.cached ?? 1.0}">
      </div>
      <div id="cpf-credit" style="display:${p?.type === 'credit' ? '' : 'none'}">
        <label style="${L}">Guthaben ($)</label><input id="cpf-balance" type="number" step="0.01" style="${F}" value="${p?.balance_usd ?? ''}">
        <label style="${L}">Aufladedatum (YYYY-MM-DD)</label><input id="cpf-anchor" style="${F}" value="${esc(p?.anchor || '')}" placeholder="heute wenn leer">
      </div>
      <div style="margin-top:8px;font-size:10.5px;color:var(--text-500)">Modelle verknüpfen Sie unter Einstellungen → Modelle → „Coding-Plan / Konto".</div>
    </div>
    <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:8px">
      <button onclick="document.getElementById('coding-plan-form').remove()" style="padding:5px 12px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100);color:var(--text-300);cursor:pointer">Abbrechen</button>
      <button onclick="saveCodingPlanForm('${esc(p?.id || '')}')" style="padding:5px 12px;border:none;border-radius:6px;background:var(--accent-brand);color:#fff;cursor:pointer">Speichern</button>
    </div></div>`;
  document.body.appendChild(ov);
}
async function saveCodingPlanForm(existingId) {
  const v = (id) => document.getElementById(id)?.value?.trim() || '';
  const name = v('cpf-name');
  if (!name) { showToast('Name erforderlich.', true); return; }
  const type = v('cpf-type') || 'flat';
  const plan = {
    id: existingId || name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''),
    name, type, price: v('cpf-price'), quota_note: v('cpf-note'),
  };
  if (type === 'credit') {
    plan.balance_usd = parseFloat(v('cpf-balance')) || 0;
    if (v('cpf-anchor')) plan.anchor = v('cpf-anchor');
  } else {
    plan.count = { fresh_in: 1.0, out: 1.0, cached: parseFloat(v('cpf-wcached')) || 1.0 };
    plan.windows = [];
    if (v('cpf-l5h')) plan.windows.push({ kind: 'session_5h', limit_tokens: parseInt(v('cpf-l5h'), 10) });
    if (v('cpf-l7d')) plan.windows.push({ kind: 'weekly', limit_tokens: parseInt(v('cpf-l7d'), 10) });
    if (v('cpf-lmon')) plan.windows.push({ kind: 'monthly', limit_tokens: parseInt(v('cpf-lmon'), 10), anchor: v('cpf-anchor-mon') || undefined });
    if (!plan.windows.length) { showToast('Mindestens ein Fenster-Limit angeben.', true); return; }
  }
  try {
    await API.post('/v1/plans/save', { plan });
    document.getElementById('coding-plan-form')?.remove();
    showToast('Plan gespeichert.');
    loadCodingPlansSection();
  } catch (e) { showToast('Speichern fehlgeschlagen: ' + (e.message || e), true); }
}
async function deleteCodingPlan(planId) {
  if (!confirm('Plan löschen? Die Verknüpfungen an den Modellen werden entfernt.')) return;
  try {
    await API.post('/v1/plans/delete', { plan_id: planId });
    showToast('Plan gelöscht.');
    loadCodingPlansSection();
  } catch (e) { showToast('Löschen fehlgeschlagen: ' + (e.message || e), true); }
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
