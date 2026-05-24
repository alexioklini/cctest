// settings_general.js — General Settings modal: tab dispatcher, models/providers/nodes/context config savers, sidecar restart. Split from settings.js (Tier F Phase 2). Global <script>, no modules.

function openGeneralSettings() {
  // Server-wide config (providers, models, quotas, GDPR, MemPalace, ...) is
  // admin-only at the server's POST gate. Refuse early so non-admin entry
  // points (slash command, future shortcuts) fail closed instead of letting
  // a non-admin browse and then 403 on save.
  if ((state.authUser?.role || 'admin') !== 'admin') {
    showToast('General settings are admin-only', true);
    return;
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content x-wide has-sidebar-tabs';

  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">General Settings</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-tabs modal-tabs-vertical" id="general-settings-tabs">
      <div class="sidebar-group-label">Server</div>
      <button class="modal-tab active" onclick="switchGeneralTab('server',this)">Server</button>
      <button class="modal-tab" onclick="switchGeneralTab('providers',this)">Providers</button>
      <button class="modal-tab" onclick="switchGeneralTab('nodes',this)">Nodes</button>

      <div class="sidebar-group-label">Models</div>
      <button class="modal-tab" onclick="switchGeneralTab('models',this)">Models</button>

      <div class="sidebar-group-label">Users &amp; Costs</div>
      <button class="modal-tab" onclick="switchGeneralTab('agents',this)">Agents</button>
      <button class="modal-tab" onclick="switchGeneralTab('teams',this)">Teams</button>
      <button class="modal-tab" onclick="switchGeneralTab('costs',this)">Costs</button>
      <button class="modal-tab" onclick="switchGeneralTab('quotas',this)">Quotas</button>

      <div class="sidebar-group-label">Privacy &amp; Memory</div>
      <button class="modal-tab" onclick="switchGeneralTab('gdpr',this)">GDPR</button>
      <button class="modal-tab" onclick="switchGeneralTab('classification',this)">Classification</button>
      <button class="modal-tab" onclick="switchGeneralTab('context',this)">Context</button>
      <button class="modal-tab" onclick="switchGeneralTab('mempalace',this)">MemPalace</button>
      <button class="modal-tab" onclick="switchGeneralTab('knowledge-graph',this)">Knowledge&nbsp;Graph</button>

      <div class="sidebar-group-label">Tools</div>
      <button class="modal-tab" onclick="switchGeneralTab('tools',this)">Tools</button>
    </div>
    <div class="modal-body" id="general-tab-content">
    </div>
  `;

  overlay.appendChild(content);
  document.body.appendChild(overlay);
  switchGeneralTab('server');
}

function _renderScheduleRow(t) {
  const sc = t.status === 'active' ? 'var(--success)' : t.status === 'paused' ? 'var(--warning)' : 'var(--text-400)';
  return `<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px">
    <span style="width:6px;height:6px;border-radius:50%;background:${sc};flex-shrink:0"></span>
    <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(t.name)}</span>
    <span style="font-size:11px;font-family:var(--font-mono);color:var(--text-400)">${esc(t.schedule||'')}</span>
    <span style="font-size:11px;color:var(--text-400)">${esc(t.agent||'')}</span>
    <span style="font-size:11px;padding:2px 8px;border-radius:4px;background:${t.status==='active'?'rgba(22,163,74,0.1)':'var(--bg-200)'};color:${sc}">${esc(t.status)}</span>
  </div>`;
}

async function restartSidecar(btn) {
  if (!confirm('Hard-restart the sidecar?\n\nIn-flight chat turns will fail with a sidecar error and need to be retried.')) return;
  const orig = btn?.textContent || 'Restart sidecar';
  if (btn) { btn.disabled = true; btn.textContent = 'Restarting…'; }
  try {
    const r = await API.post('/v1/sidecar/restart', {});
    if (r && r.ok) {
      showToast('Sidecar restarting');
    } else {
      showToast(r?.error || 'Restart failed', true);
    }
  } catch (e) {
    showToast('Restart failed: ' + (e?.message || e), true);
  } finally {
    // Re-render the Server tab so the new status (PID, uptime) shows up.
    setTimeout(() => {
      const t = document.querySelector('.modal-tab.active[onclick*="server"]');
      if (t) switchGeneralTab('server', t);
      else if (btn) { btn.disabled = false; btn.textContent = orig; }
    }, 1500);
  }
}

async function restartSearxng(btn) {
  if (!confirm('Hard-restart the bundled SearXNG instance?\n\nWeb searches will briefly fail until it comes back up.')) return;
  const orig = btn?.textContent || 'Restart SearXNG';
  if (btn) { btn.disabled = true; btn.textContent = 'Restarting…'; }
  try {
    const r = await API.post('/v1/searxng/restart', {});
    if (r && r.ok) showToast('SearXNG restarting');
    else showToast(r?.error || 'Restart failed', true);
  } catch (e) {
    showToast('Restart failed: ' + (e?.message || e), true);
  } finally {
    setTimeout(() => {
      const t = document.querySelector('.modal-tab.active[onclick*="server"]');
      if (t) switchGeneralTab('server', t);
      else if (btn) { btn.disabled = false; btn.textContent = orig; }
    }, 1500);
  }
}

async function restartCrawl4ai(btn) {
  if (!confirm('Hard-restart the crawl4ai render service?\n\nJS-rendered fetches briefly fall back to plain HTTP until it comes back up.')) return;
  const orig = btn?.textContent || 'Restart crawl4ai';
  if (btn) { btn.disabled = true; btn.textContent = 'Restarting…'; }
  try {
    const r = await API.post('/v1/crawl4ai/restart', {});
    if (r && r.ok) showToast('crawl4ai restarting');
    else showToast(r?.error || 'Restart failed', true);
  } catch (e) {
    showToast('Restart failed: ' + (e?.message || e), true);
  } finally {
    setTimeout(() => {
      const t = document.querySelector('.modal-tab.active[onclick*="server"]');
      if (t) switchGeneralTab('server', t);
      else if (btn) { btn.disabled = false; btn.textContent = orig; }
    }, 1500);
  }
}

// Renders the per-engine SearXNG health panel: one row per enabled search
// engine with its last-test state + latency, the time of the last probe, the
// next scheduled automatic probe (every 4h, anchored to server startup), and a
// "Test now" button. `sxe` is the /v1/searxng/engines snapshot (may be null).
function _renderSearxngEngines(sxe) {
  const ROW = 'display:flex;align-items:center;gap:8px;padding:4px 12px';
  const MONO = 'font-family:var(--font-mono);font-size:11px;color:var(--text-300)';
  const fmtAgo = (t) => !t ? 'never' : (function(s){
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.round(s/60) + 'm ago';
    return Math.round(s/3600) + 'h ago';
  })(Math.max(0, Math.round(Date.now()/1000 - t)));
  const fmtIn = (t) => !t ? '—' : (function(s){
    if (s <= 0) return 'due';
    if (s < 3600) return 'in ' + Math.round(s/60) + 'm';
    return 'in ' + Math.round(s/3600) + 'h';
  })(Math.round(t - Date.now()/1000));
  // state → {colour, dot}: ok = healthy, fail/error = down (wasting resources),
  // empty = alive but no match for the probe query (situational engine).
  const STATE = {
    ok:    { c: 'var(--success)',          label: 'ok' },
    fail:  { c: 'var(--error)',            label: 'fail' },
    error: { c: 'var(--error)',            label: 'error' },
    empty: { c: 'var(--text-400)',         label: 'no match' },
  };
  const engines = (sxe && Array.isArray(sxe.engines)) ? sxe.engines : [];
  const testBtn = `<button class="btn-secondary" onclick="testSearxngEngines(this)">Test now</button>`;

  let body;
  if (!sxe || sxe.error) {
    body = `<div style="${ROW}">${DOT(false)}<span style="font-size:13px;color:var(--text-100);flex:1">${esc(sxe?.error || 'Engine health unavailable')}</span></div>`;
  } else if (!engines.length) {
    body = `<div style="${ROW}"><span style="font-size:12px;color:var(--text-400);flex:1">No probe has run yet — first automatic test runs shortly after startup, or click Test now.</span></div>`;
  } else {
    body = engines.map(e => {
      const st = STATE[e.state] || { c: 'var(--text-400)', label: esc(e.state || '?') };
      const healthy = e.state === 'ok' || e.state === 'empty';
      return `<div style="${ROW}">
        ${DOT(healthy)}
        <span style="font-size:13px;color:var(--text-100);flex:1">${esc(e.name || '')}</span>
        <span style="${MONO}">${e.latency_ms != null ? e.latency_ms + 'ms' : ''}</span>
        <span style="font-size:11px;color:${st.c};min-width:54px;text-align:right" title="${esc(e.detail||'')}">${st.label}</span>
      </div>`;
    }).join('');
  }

  const tested = (sxe && sxe.tested_at) ? sxe.tested_at : 0;
  const nextAt = (sxe && sxe.next_auto_at) ? sxe.next_auto_at : 0;
  const meta = `<div style="font-size:11px;color:var(--text-400);padding:2px 12px;display:grid;grid-template-columns:auto auto;gap:4px 18px">
      <span>Last test</span><span style="${MONO}">${fmtAgo(tested)}</span>
      <span>Next auto test</span><span style="${MONO}">${nextAt ? fmtIn(nextAt) : 'every 4h'}</span>
    </div>`;

  return `<div style="font-size:11px;color:var(--text-400);padding:6px 12px 2px">Per-engine health (probed in isolation; failing engines waste a request on every search).</div>
    ${body}
    ${meta}
    <div style="display:flex;gap:8px;padding:6px 12px 0">${testBtn}
      <span style="font-size:11px;color:var(--text-400);align-self:center">Manual test does not change the automatic 4-hour schedule.</span>
    </div>`;
}

async function testSearxngEngines(btn) {
  const orig = btn?.textContent || 'Test now';
  if (btn) { btn.disabled = true; btn.textContent = 'Testing…'; }
  try {
    const snap = await API.post('/v1/searxng/test-engines', {});
    const panel = document.getElementById('searxng-engines-panel');
    if (panel) panel.innerHTML = _renderSearxngEngines(snap);
    else showToast('Engine test complete');
  } catch (e) {
    showToast('Engine test failed: ' + (e?.message || e), true);
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// Shared renderer for a ProcessSupervisor status block (sidecar + searxng share
// the same status dict shape). `opts.restartFn` is the onclick handler name,
// `opts.note` the warning shown next to the restart button, `opts.disabledHint`
// the config key shown when auto_start is off.
function _renderSupervisorStatus(sc, opts) {
  const ROW = 'display:flex;align-items:center;gap:8px;padding:6px 12px';
  const MONO = 'font-family:var(--font-mono);font-size:11px;color:var(--text-300)';
  if (!sc) {
    return `<div style="${ROW}">${DOT(false)}<span style="font-size:13px;color:var(--text-100);flex:1">Status unavailable</span></div>`;
  }
  if (!sc.enabled) {
    return `<div style="${ROW}">${DOT(false)}<span style="font-size:13px;color:var(--text-100);flex:1">Supervisor disabled</span><span style="font-size:11px;color:var(--text-400)">${esc(opts.disabledHint||'')}</span></div>`;
  }
  const running = !!sc.running;
  const healthOk = !!sc.last_health_ok;
  const breaker = !!sc.breaker_open;
  const uptime = running && sc.started_at ? Math.max(0, Math.round(Date.now()/1000 - sc.started_at)) : 0;
  const fmtAgo = (t) => !t ? 'never' : (function(s){
    if (s<60) return s+'s ago';
    if (s<3600) return Math.round(s/60)+'m ago';
    return Math.round(s/3600)+'h ago';
  })(Math.max(0, Math.round(Date.now()/1000 - t)));
  const statusLabel = breaker ? 'breaker open' : (running ? (healthOk ? 'running' : 'unresponsive') : 'stopped');
  const statusColor = breaker ? 'var(--error)' : (running && healthOk ? 'var(--success)' : 'var(--warning, #b45309)');
  return `
    <div style="${ROW}">
      ${DOT(running && healthOk && !breaker)}
      <span style="font-size:13px;color:var(--text-100);flex:1">${esc(sc.url||'')}</span>
      ${running ? `<span style="${MONO}">PID ${sc.pid}</span>` : ''}
      <span style="font-size:11px;color:${statusColor}">${esc(statusLabel)}</span>
    </div>
    <div style="font-size:11px;color:var(--text-400);padding:0 12px;display:grid;grid-template-columns:auto auto;gap:4px 18px">
      ${running ? `<span>Uptime</span><span style="${MONO}">${uptime}s</span>` : ''}
      <span>Last health probe</span><span style="${MONO}">${healthOk?'ok':'fail'} &middot; ${fmtAgo(sc.last_health_at)}</span>
      <span>Crashes (last 60s)</span><span style="${MONO}">${sc.crash_count_60s||0} / ${sc.crash_limit||3}</span>
      ${sc.last_exit_rc !== null && sc.last_exit_rc !== undefined ? `<span>Last exit</span><span style="${MONO}">rc=${sc.last_exit_rc} &middot; ${fmtAgo(sc.last_exit_at)}</span>` : ''}
      ${breaker ? `<span style="color:var(--error)">Circuit breaker</span><span style="${MONO};color:var(--error)">open — auto-restart halted</span>` : ''}
    </div>
    <div style="display:flex;gap:8px;padding:0 12px;margin-top:6px">
      <button class="btn-secondary" onclick="${opts.restartFn}(this)">${breaker ? 'Restart & clear breaker' : (opts.restartLabel||'Restart')}</button>
      <span style="font-size:11px;color:var(--text-400);align-self:center">${esc(opts.note||'')}</span>
    </div>`;
}

/* ═══════════════════════════════════════════════════════════
   GENERAL SETTINGS TAB SWITCHER
   ═══════════════════════════════════════════════════════════ */

// shared render helpers for the General Settings tab bodies (hoisted from switchGeneralTab)
const P = (s) => `<div style="padding:16px">${s}</div>`;
const G = (gap='8px') => `display:grid;gap:${gap}`;
const ROW = `display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px`;
const DOT = (ok) => `<span style="width:7px;height:7px;border-radius:50%;background:${ok?'var(--success)':'var(--error)'};flex-shrink:0"></span>`;
const MONO = `font-size:11px;font-family:var(--font-mono);color:var(--text-400)`;
const BADGE = (t,c='var(--text-400)') => `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg-200);color:${c}">${esc(t)}</span>`;
const SEC = (title) => `<div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:12px 0 4px">${title}</div>`;

async function switchGeneralTab(tab, btn) {
  if (btn) {
    btn.closest('.modal-tabs').querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
  }
  const C = document.getElementById('general-tab-content');
  C.innerHTML = '<div style="padding:20px;color:var(--text-400)">Loading...</div>';
  // per-tab body renderers live in settings_general_tabs.js
  const RENDERERS = { server:_genTab_server, models:_genTab_models, providers:_genTab_providers, agents:_genTab_agents, teams:_genTab_teams, nodes:_genTab_nodes, context:_genTab_context, costs:_genTab_costs, quotas:_genTab_quotas, mempalace:_genTab_mempalace, 'knowledge-graph':_genTab_knowledge_graph, gdpr:_genTab_gdpr, classification:_genTab_classification, tools:_genTab_tools };
  const fn = RENDERERS[tab];
  if (fn) { await fn(C); return; }
}


async function saveContextConfig() {
  try {
    await API.post('/v1/context/config', {
      enabled: document.getElementById('ctx-enabled')?.checked ?? true,
      fresh_tail_count: parseInt(document.getElementById('ctx-fresh-tail')?.value) || 16,
      compact_threshold: (parseInt(document.getElementById('ctx-threshold')?.value) || 60) / 100,
      messages_per_summary: parseInt(document.getElementById('ctx-msgs-per-sum')?.value) || 10,
      condense_threshold: parseInt(document.getElementById('ctx-condense')?.value) || 4,
      max_depth: parseInt(document.getElementById('ctx-max-depth')?.value) || 5,
      summary_target_tokens: parseInt(document.getElementById('ctx-target-tokens')?.value) || 1000,
      summary_model: document.getElementById('ctx-summary-model')?.value || '',
    });
    showToast('Context config saved');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function saveModelsConfig() {
  try {
    const mc = {...(state.modelsConfig?.models || {})};
    const readNum = (row, cls) => { const v = row.querySelector(`.${cls}`)?.value?.trim(); return v === '' || v == null ? undefined : Number(v); };
    const readStr = (row, cls) => { const v = row.querySelector(`.${cls}`)?.value?.trim(); return v || undefined; };
    document.querySelectorAll('[data-model-id]').forEach(row => {
      const mid = row.dataset.modelId;
      if (!mc[mid]) return;
      mc[mid].enabled = row.querySelector('.mdl-enabled')?.checked ?? mc[mid].enabled;
      const pVal = row.querySelector('.mdl-priority')?.value;
      if (pVal !== undefined) mc[mid].priority = parseInt(pVal) || 0;
      const dName = row.querySelector('.mdl-display-name')?.value?.trim();
      if (dName) mc[mid].display_name = dName;
      const desc = row.querySelector('.mdl-description')?.value?.trim();
      if (desc) mc[mid].description = desc; else delete mc[mid].description;
      // Profile (speed/balanced/frugal/custom)
      const profile = row.querySelector('.mdl-profile')?.value;
      if (profile && profile !== 'custom') mc[mid].profile = profile;
      else delete mc[mid].profile;
      // Context & output
      const maxCtx = readNum(row, 'mdl-max-context');
      if (maxCtx !== undefined) mc[mid].max_context = maxCtx; else delete mc[mid].max_context;
      const maxOut = readNum(row, 'mdl-max-output');
      if (maxOut !== undefined) mc[mid].max_output = maxOut; else delete mc[mid].max_output;
      // Cost
      const ci = readNum(row, 'mdl-cost-input');
      if (ci !== undefined) mc[mid].cost_input = ci; else delete mc[mid].cost_input;
      const co = readNum(row, 'mdl-cost-output');
      if (co !== undefined) mc[mid].cost_output = co; else delete mc[mid].cost_output;
      // Inference params — collect non-empty values
      const inf = {};
      const infKeys = ['temperature','top_p','top_k','max_tokens','frequency_penalty','presence_penalty','min_p','repetition_penalty','thinking_budget'];
      for (const k of infKeys) {
        const v = readNum(row, `mdl-inf-${k}`);
        if (v !== undefined && !isNaN(v)) inf[k] = v;
      }
      // String params (reasoning_effort)
      const re = readStr(row, 'mdl-inf-reasoning_effort');
      if (re) inf.reasoning_effort = re;
      // Thinking level: '' = unset (use API default for the format), explicit
      // 'none'/'low'/'medium'/'high' wins. Skip when format='none' (column is
      // disabled in that case anyway).
      const tlSel = row.querySelector('.mdl-thinking-level');
      const tl = tlSel && !tlSel.disabled ? (tlSel.value || '') : '';
      if (tl) inf.thinking_level = tl;
      mc[mid].inference = Object.keys(inf).length > 0 ? inf : undefined;
      if (!mc[mid].inference) delete mc[mid].inference;
      // Caveman system level (per-model system prompt compression)
      const cavSys = readNum(row, 'mdl-caveman-system');
      if (cavSys) mc[mid].caveman_system = cavSys; else delete mc[mid].caveman_system;
      // Parallel tool calls
      const ptc = row.querySelector('.mdl-parallel-tools')?.checked;
      if (ptc === false) mc[mid].parallel_tool_calls = false; else delete mc[mid].parallel_tool_calls;
      // Warmup. Persist explicit false so the speed profile's warmup=true
      // overlay can't silently re-enable warmup the user just turned off.
      const warm = row.querySelector('.mdl-warmup')?.checked;
      mc[mid].warmup = !!warm;
      // warmup_ttl_seconds retired: hold-forever semantics (no periodic reprime)
      delete mc[mid].warmup_ttl_seconds;
      const warmMode = row.querySelector('.mdl-warmup-mode')?.value;
      if (warmMode && warmMode !== 'full') mc[mid].warmup_mode = warmMode;
      else delete mc[mid].warmup_mode;
      const warmCloud = row.querySelector('.mdl-warmup-allow-cloud')?.checked;
      if (warmCloud) mc[mid].warmup_allow_cloud = true; else delete mc[mid].warmup_allow_cloud;
      // Raw formats
      const rawFmt = row.querySelector('.mdl-raw-formats')?.value?.trim();
      if (rawFmt) {
        mc[mid].raw_formats = rawFmt.split(',').map(s => s.trim()).filter(Boolean);
      } else {
        mc[mid].raw_formats = [];
      }
      // Capabilities — canonical {chat, image, audio, tts, video} checkboxes.
      // Order is preserved to keep the saved config diff-stable.
      const _capOrder = ['chat','image','audio','tts','video'];
      const _checkedCaps = new Set(
        Array.from(row.querySelectorAll('.mdl-cap-cb'))
          .filter(cb => cb.checked).map(cb => cb.dataset.cap)
      );
      mc[mid].capabilities = _capOrder.filter(c => _checkedCaps.has(c));
      mc[mid]._caps_canonical = true;
      // Thinking format
      const tfmt = row.querySelector('.mdl-thinking-format')?.value;
      if (tfmt && tfmt !== 'none') mc[mid].thinking_format = tfmt;
      else mc[mid].thinking_format = 'none';
    });
    await API.post('/v1/models/config', { action: 'save', models: mc });
    state.modelsConfig.models = mc;
    showToast('Models config saved');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function addManualModel() {
  const id = document.getElementById('add-model-id')?.value?.trim();
  const provider = document.getElementById('add-model-provider')?.value?.trim();
  const display = document.getElementById('add-model-display')?.value?.trim();
  if (!id) { showToast('Model ID required', true); return; }
  if (!provider) { showToast('Provider required', true); return; }
  const mc = state.modelsConfig?.models || {};
  if (mc[id]) { showToast('Model already exists', true); return; }
  mc[id] = { enabled: true, provider, display_name: display || id, shortname: id, priority: 10, capabilities: [], icon: '\u{1F916}', manual: true };
  state.modelsConfig.models = mc;
  try {
    await API.post('/v1/models/config', { action: 'save', models: mc });
    showToast('Model added');
    switchGeneralTab('models');
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function removeModel(mid) {
  // Tombstoned delete — server records the id in config.deleted_models so
  // it isn't re-added by startup discovery or sync. Re-add via manual add
  // (or resync_provider on the owning provider) clears the tombstone.
  const mc = {...(state.modelsConfig?.models || {})};
  delete mc[mid];
  state.modelsConfig.models = mc;
  try {
    await API.post('/v1/models/config', { action: 'delete', model_id: mid });
    showToast('Model removed');
    switchGeneralTab('models');
  } catch(e) { showToast('Failed: ' + e.message, true); }
}

async function _confirmRemoveModel(mid) {
  if (!await showConfirmDanger(`Remove model ${mid}?`, 'Remove Model', 'Remove')) return;
  removeModel(mid);
}

async function resyncProvider(btn, name) {
  if (!await showConfirmDanger(`Full resync of "${name}"? This deletes ALL its models AND their deletion tombstones, then re-discovers from the provider's /models endpoint.`, 'Full Resync', 'Resync')) return;
  btn.disabled = true; const orig = btn.textContent; btn.textContent = 'Resyncing...';
  try {
    await API.post('/v1/models/config', { action: 'resync_provider', provider: name });
    const d = await API.getModelsConfig();
    state.modelsConfig = d;
    switchGeneralTab('providers');
    showToast(`${name} fully resynced`);
  } catch(e) {
    showToast('Resync failed: ' + e.message, true);
    btn.disabled = false; btn.textContent = orig;
  }
}

async function syncProvider(btn, name) {
  btn.disabled = true; btn.textContent = 'Syncing...';
  try {
    await API.post('/v1/models/config', { action: 'sync', provider: name });
    showToast(`Syncing ${name}...`);
    setTimeout(async () => {
      const d = await API.getModelsConfig();
      state.modelsConfig = d;
      switchGeneralTab('providers');
      showToast(`${name} synced`);
    }, 3000);
  } catch(e) { showToast('Sync failed', true); btn.disabled = false; btn.textContent = 'Sync'; }
}

async function saveProviderEdit(name, pid) {
  const base_url = document.getElementById(`${pid}-url`).value;
  const default_model = document.getElementById(`${pid}-model`).value;
  const is_local = !!document.getElementById(`${pid}-is-local`)?.checked;
  const provider = { base_url, default_model, is_local };
  try {
    await API.post('/v1/providers', { action: 'add', name, provider });
    showToast('Provider updated');
    switchGeneralTab('providers');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function openProviderKeysModal(provName) {
  document.getElementById('_prov-keys-modal')?.remove();
  const overlay = document.createElement('div');
  overlay.id = '_prov-keys-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:640px;padding:20px;display:grid;gap:12px">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:15px;font-weight:600;color:var(--text-000)">API Keys — ${esc(provName)}</span>
      <span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">preferred → round-robin → fallback</span>
      <button class="modal-close" style="margin-left:auto" onclick="document.getElementById('_prov-keys-modal')?.remove()">&times;</button>
    </div>
    <div id="_prov-keys-list" style="display:grid;gap:6px"><span style="color:var(--text-400);font-size:12px">Loading…</span></div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" style="font-size:12px" onclick="openKeyEditModal('${esc(provName)}','')">+ Add key</button>
      <button class="btn-secondary" style="font-size:12px;margin-left:auto" onclick="openProviderStatsModal('${esc(provName)}')">View stats →</button>
    </div>
  </div>`;
  overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  await _renderProviderKeysList(provName);
}

async function _renderProviderKeysList(provName) {
  const target = document.getElementById('_prov-keys-list');
  if (!target) return;
  const USAGE_LABELS = {preferred:'Preferred (prio 1)',round_robin:'Round-robin (prio 2)',fallback:'Fallback (prio 3)'};
  const USAGE_COLORS = {preferred:'var(--accent)',round_robin:'var(--text-200)',fallback:'var(--text-400)'};
  try {
    const provs = await API.getProviders();
    const providers = Array.isArray(provs) ? provs : (provs.providers || []);
    state.providers = providers;
    const prov = providers.find(p => p.name === provName);
    const keys = prov?.api_keys || [];
    if (!keys.length) {
      target.innerHTML = `<span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">No keys yet — click + Add key</span>`;
      return;
    }
    target.innerHTML = keys.map(k => {
      const usageColor = USAGE_COLORS[k.usage]||'var(--text-200)';
      const deadlinePart = k.deadline ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">exp ${esc(k.deadline.slice(0,10))}</span>` : '';
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:var(--bg-100);border-radius:6px;flex-wrap:wrap">
        <span style="font-size:13px;font-weight:500;color:var(--text-100);min-width:100px">${esc(k.name)}</span>
        <span style="font-family:var(--font-mono);color:${usageColor};font-size:11px">${esc(USAGE_LABELS[k.usage]||k.usage)}</span>
        <span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">${esc(k.key_hint||'')}</span>
        ${deadlinePart}
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px;margin-left:auto" onclick="openKeyEditModal('${esc(provName)}','${esc(k.name)}')">Edit</button>
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="deleteProviderKey('${esc(provName)}','${esc(k.name)}')">Delete</button>
      </div>`;
    }).join('');
  } catch(e) {
    target.innerHTML = `<span style="color:var(--error);font-size:12px">Failed to load: ${esc(e.message||'error')}</span>`;
  }
}

async function openProviderStatsModal(provName, days) {
  document.getElementById('_prov-stats-modal')?.remove();
  const d = Number(days) || 30;
  const overlay = document.createElement('div');
  overlay.id = '_prov-stats-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:760px;padding:20px;display:grid;gap:12px">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:15px;font-weight:600;color:var(--text-000)">Usage Stats — ${esc(provName)}</span>
      <select class="form-select" id="_prov-stats-days" style="width:auto;font-size:12px;padding:2px 8px;margin-left:8px"
        onchange="openProviderStatsModal('${esc(provName)}', this.value)">
        <option value="7"${d===7?' selected':''}>Last 7 days</option>
        <option value="30"${d===30?' selected':''}>Last 30 days</option>
        <option value="90"${d===90?' selected':''}>Last 90 days</option>
        <option value="365"${d===365?' selected':''}>Last 365 days</option>
      </select>
      <button class="modal-close" style="margin-left:auto" onclick="document.getElementById('_prov-stats-modal')?.remove()">&times;</button>
    </div>
    <div id="_prov-stats-body"><span style="color:var(--text-400);font-size:12px">Loading…</span></div>
  </div>`;
  overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const fmtNum = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n||0);
  const fmtCost = c => (c||0) > 0 ? '$'+(c).toFixed(4) : '—';
  try {
    const resp = await API.get(`/v1/providers/stats?days=${d}`);
    const all = resp.stats || [];
    const ps = all.find(s => s.provider === provName);
    const body = document.getElementById('_prov-stats-body');
    if (!body) return;
    if (!ps) {
      body.innerHTML = `<span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">No usage in selected window.</span>`;
      return;
    }
    const totals = `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:10px;background:var(--bg-100);border-radius:8px">
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Calls</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.calls)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens in</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.tokens_in)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens out</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.tokens_out)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Cost</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtCost(ps.cost_usd)}</div></div>
    </div>`;
    const keys = (ps.keys || []).slice().sort((a,b)=>b.calls-a.calls);
    const headerRow = `<div style="display:grid;grid-template-columns:1.4fr repeat(5, 1fr);gap:8px;padding:6px 8px;font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid var(--border-100)">
      <div>Key</div><div style="text-align:right">Calls</div><div style="text-align:right">Tokens in</div><div style="text-align:right">Tokens out</div><div style="text-align:right">Cost</div><div style="text-align:right">Last used</div>
    </div>`;
    const rows = keys.length ? keys.map(k => `<div style="display:grid;grid-template-columns:1.4fr repeat(5, 1fr);gap:8px;padding:6px 8px;font-size:12px;border-bottom:1px solid var(--border-100)">
      <div style="color:var(--text-100);font-weight:500;overflow:hidden;text-overflow:ellipsis">${esc(k.key_name||'(unknown)')}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-100)">${fmtNum(k.calls)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-200)">${fmtNum(k.tokens_in)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-200)">${fmtNum(k.tokens_out)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-100)">${fmtCost(k.cost_usd)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-400);font-size:10px">${esc((k.last_used||'').slice(0,16).replace('T',' '))}</div>
    </div>`).join('') : `<div style="padding:8px;font-family:var(--font-mono);color:var(--text-400);font-size:11px">No per-key breakdown available.</div>`;
    body.innerHTML = `${totals}
      <div style="margin-top:8px">
        <div style="font-size:11px;font-weight:500;color:var(--text-200);margin-bottom:4px">Per API key</div>
        ${headerRow}${rows}
      </div>`;
  } catch(e) {
    const body = document.getElementById('_prov-stats-body');
    if (body) body.innerHTML = `<span style="color:var(--error);font-size:12px">Failed to load stats: ${esc(e.message||'error')}</span>`;
  }
}

function openKeyEditModal(provName, keyName) {
  // Remove any existing key modal
  document.getElementById('_key-edit-modal')?.remove();
  const isEdit = !!keyName;
  const overlay = document.createElement('div');
  overlay.id = '_key-edit-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:420px;padding:20px;display:grid;gap:12px">
    <div style="font-size:15px;font-weight:600;color:var(--text-000)">${isEdit?'Edit API Key':'Add API Key'}</div>
    <div><label class="form-label">Key name</label><input class="form-input" id="_kmod-name" placeholder="e.g. main, backup-1" value="${isEdit?esc(keyName):''}"></div>
    <div><label class="form-label">API key value</label><input class="form-input" id="_kmod-key" type="password" placeholder="${isEdit?'leave blank to keep current':'sk-...'}"></div>
    <div><label class="form-label">Usage / priority</label>
      <select class="form-select" id="_kmod-usage" style="width:100%">
        <option value="preferred">Preferred (prio 1 — used first, round-robin among peers)</option>
        <option value="round_robin">Round-robin (prio 2 — when no preferred available)</option>
        <option value="fallback">Fallback (prio 3 — last resort)</option>
      </select>
    </div>
    <div><label class="form-label">Expiry date <span style="color:var(--text-400)">(optional, ISO date e.g. 2026-12-31)</span></label>
      <input class="form-input" id="_kmod-deadline" placeholder="2026-12-31">
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="saveKeyFromModal('${esc(provName)}','${isEdit?esc(keyName):''}')">Save</button>
      <button class="btn-secondary" onclick="document.getElementById('_key-edit-modal')?.remove()">Cancel</button>
    </div>
  </div>`;
  overlay.addEventListener('click', e => { if(e.target===overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  // If editing, pre-fill usage/deadline from current state
  if (isEdit) {
    const providers = state.providers || [];
    const prov = providers.find(p=>p.name===provName);
    if (prov) {
      const key = (prov.api_keys||[]).find(k=>k.name===keyName);
      if (key) {
        document.getElementById('_kmod-usage').value = key.usage || 'preferred';
        document.getElementById('_kmod-deadline').value = key.deadline || '';
      }
    }
  }
}

async function saveKeyFromModal(provName, oldKeyName) {
  const name = document.getElementById('_kmod-name')?.value?.trim();
  const keyVal = document.getElementById('_kmod-key')?.value;
  const usage = document.getElementById('_kmod-usage')?.value || 'preferred';
  const deadline = document.getElementById('_kmod-deadline')?.value?.trim();
  if (!name) { showToast('Key name required', true); return; }
  const isEdit = !!oldKeyName;
  // If editing and no new key value, we need to look up the existing key from stored list
  // The server only has the masked hint, so require the value if it's a new key;
  // for edits without a new value, send a special _keep_key_value flag
  const entry = { name, usage };
  if (keyVal) entry.key = keyVal;
  else if (!isEdit) { showToast('API key value required for new key', true); return; }
  else entry._keep_key_value = true;
  if (deadline) entry.deadline = deadline;
  try {
    await API.post('/v1/providers', { action: 'save_key', name: provName, key_entry: entry, old_name: oldKeyName });
    showToast('Key saved');
    document.getElementById('_key-edit-modal')?.remove();
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    if (document.getElementById('_prov-keys-modal')) await _renderProviderKeysList(provName);
    else switchGeneralTab('providers');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

async function deleteProviderKey(provName, keyName) {
  if (!await showConfirmDanger(`Delete key "${keyName}" from ${provName}?`, 'Delete Key', 'Delete')) return;
  try {
    await API.post('/v1/providers', { action: 'delete_key', name: provName, key_name: keyName });
    showToast('Key deleted');
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    if (document.getElementById('_prov-keys-modal')) await _renderProviderKeysList(provName);
    else switchGeneralTab('providers');
  } catch(e) { showToast('Delete failed: ' + e.message, true); }
}

async function testProvider(name) {
  try {
    const r = await API.post('/v1/providers/test', { name });
    showToast(r.status === 'ok' ? `${name}: connected` : `${name}: ${r.error||'failed'}`, r.status !== 'ok');
  } catch(e) { showToast('Test failed: ' + e.message, true); }
}

async function deleteProvider(name) {
  try {
    await API.post('/v1/providers', { action: 'delete', name });
    showToast('Provider deleted');
    switchGeneralTab('providers');
  } catch(e) { showToast('Delete failed: ' + e.message, true); }
}

async function _confirmDeleteProvider(name) {
  if (!await showConfirmDanger(`Delete provider ${name}?`, 'Delete Provider', 'Delete')) return;
  deleteProvider(name);
}

async function renameProvider(name) {
  const next = await showPrompt(`Rename provider "${name}" to:`, name);
  if (next === null) return;
  const trimmed = next.trim();
  if (!trimmed || trimmed === name) return;
  if (/[\s/]/.test(trimmed)) { showToast("Name must not contain '/' or whitespace", true); return; }
  try {
    await API.post('/v1/providers', { action: 'rename', name, new_name: trimmed });
    showToast(`Renamed to ${trimmed}`);
    switchGeneralTab('providers');
  } catch(e) { showToast('Rename failed: ' + e.message, true); }
}

async function testNewProvider() {
  const res = document.getElementById('prov-test-result');
  res.innerHTML = '<span style="color:var(--text-400)">Testing...</span>';
  try {
    const r = await API.post('/v1/providers/test', {
      base_url: document.getElementById('prov-url')?.value,
      api_key: document.getElementById('prov-key')?.value,
    });
    res.innerHTML = r.status === 'ok'
      ? `<span style="color:var(--success)">Connected (${r.models||0} models)</span>`
      : `<span style="color:var(--error)">${esc(r.error||'Failed')}</span>`;
  } catch(e) { res.innerHTML = `<span style="color:var(--error)">${esc(e.message)}</span>`; }
}

async function saveNewProvider() {
  const name = document.getElementById('prov-name')?.value?.trim();
  if (!name) { showToast('Name required', true); return; }
  const rawKey = document.getElementById('prov-key')?.value || '';
  const provider = {
    base_url: document.getElementById('prov-url')?.value || '',
    default_model: document.getElementById('prov-model')?.value || '',
    is_local: !!document.getElementById('prov-is-local')?.checked,
    api_keys: rawKey ? [{ name: 'default', key: rawKey, usage: 'preferred' }] : [],
  };
  try {
    await API.post('/v1/providers', { action: 'add', name, provider });
    showToast('Provider added');
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    switchGeneralTab('providers');
  } catch(e) { showToast('Add failed: ' + e.message, true); }
}

async function createNode() {
  const name = document.getElementById('node-name')?.value?.trim();
  if (!name) { showToast('Name required', true); return; }
  try {
    const r = await API.post('/v1/nodes', {
      action: 'add', name,
      description: document.getElementById('node-desc')?.value || '',
    });
    const res = document.getElementById('node-result');
    if (r.token) {
      res.innerHTML = `<div style="margin-top:8px;padding:10px;background:var(--bg-200);border-radius:8px"><div style="font-size:12px;font-weight:500;color:var(--text-200);margin-bottom:4px">Install token:</div><code style="font-size:11px;color:var(--accent-brand);word-break:break-all">${esc(r.token)}</code></div>`;
    }
    showToast('Node created');
    switchGeneralTab('nodes');
  } catch(e) { showToast('Create failed: ' + e.message, true); }
}

async function _confirmRemoveNode(name) {
  if (!await showConfirmDanger('Remove node?', 'Remove Node', 'Remove')) return;
  try {
    await API.post('/v1/nodes', { action: 'remove', name });
    showToast('Removed');
    switchGeneralTab('nodes');
  } catch(e) { showToast('Remove failed: ' + e.message, true); }
}


