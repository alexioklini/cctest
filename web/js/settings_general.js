// settings_general.js — General Settings modal: tab dispatcher, models/providers/nodes/context config savers, supervisor restarts (searxng/crawl4ai). Split from settings.js (Tier F Phase 2). Global <script>, no modules.

function openGeneralSettings() {
  // Server-wide config (providers, models, quotas, GDPR, MemPalace, ...) is
  // admin-only at the server's POST gate. Refuse early so non-admin entry
  // points (slash command, future shortcuts) fail closed instead of letting
  // a non-admin browse and then 403 on save.
  if ((state.authUser?.role || 'admin') !== 'admin') {
    showToast('Allgemeine Einstellungen sind nur für Administratoren', true);
    return;
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content x-wide has-sidebar-tabs';

  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Allgemeine Einstellungen</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-tabs modal-tabs-vertical" id="general-settings-tabs">
      <div class="sidebar-group-label">Server</div>
      <button class="modal-tab active" onclick="switchGeneralTab('server',this)">Server</button>
      <button class="modal-tab" onclick="switchGeneralTab('providers',this)">Provider</button>
      <button class="modal-tab" onclick="switchGeneralTab('nodes',this)">Nodes</button>

      <div class="sidebar-group-label">Modelle</div>
      <button class="modal-tab" onclick="switchGeneralTab('models',this)">Modelle</button>
      <button class="modal-tab" onclick="switchGeneralTab('service-models',this)">Service-Modelle</button>

      <div class="sidebar-group-label">Benutzer &amp; Kosten</div>
      <button class="modal-tab" onclick="switchGeneralTab('agents',this)">Agents</button>
      <button class="modal-tab" onclick="switchGeneralTab('teams',this)">Teams</button>
      <button class="modal-tab" onclick="switchGeneralTab('costs',this)">Kosten</button>
      <button class="modal-tab" onclick="switchGeneralTab('quotas',this)">Kontingente</button>
      <button class="modal-tab" onclick="switchGeneralTab('feedback',this)">Feedback</button>

      <div class="sidebar-group-label">Datenschutz &amp; Memory</div>
      <button class="modal-tab" onclick="switchGeneralTab('gdpr',this)">DSGVO</button>
      <button class="modal-tab" onclick="switchGeneralTab('classification',this)">Klassifizierung</button>
      <button class="modal-tab" onclick="switchGeneralTab('context',this)">Kontext</button>
      <button class="modal-tab" onclick="switchGeneralTab('mempalace',this)">MemPalace</button>
      <button class="modal-tab" onclick="switchGeneralTab('knowledge-graph',this)">Knowledge&nbsp;Graph</button>

      <div class="sidebar-group-label">Tools</div>
      <button class="modal-tab" onclick="switchGeneralTab('tools',this)">Tools</button>
      <button class="modal-tab" onclick="switchGeneralTab('doc-styles',this)">Dokument-Stile</button>
      <button class="modal-tab" onclick="switchGeneralTab('wiki',this)">Wiki</button>
      <button class="modal-tab" onclick="switchGeneralTab('cleanup',this)">Bereinigung</button>
      <button class="modal-tab" onclick="switchGeneralTab('helpdesk',this)">Brainy</button>
      <button class="modal-tab" onclick="switchGeneralTab('doctor',this)">Doctor</button>
      <button class="modal-tab" onclick="switchGeneralTab('libraries',this)">Bibliotheken</button>
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

async function restartSearxng(btn) {
  if (!confirm('Die mitgelieferte SearXNG-Instanz hart neu starten?\n\nWebsuchen schlagen kurzzeitig fehl, bis sie wieder hochgefahren ist.')) return;
  const orig = btn?.textContent || 'SearXNG neu starten';
  if (btn) { btn.disabled = true; btn.textContent = 'Wird neu gestartet…'; }
  try {
    const r = await API.post('/v1/searxng/restart', {});
    if (r && r.ok) showToast('SearXNG wird neu gestartet');
    else showToast(r?.error || 'Neustart fehlgeschlagen', true);
  } catch (e) {
    showToast('Neustart fehlgeschlagen: ' + (e?.message || e), true);
  } finally {
    setTimeout(() => {
      const t = document.querySelector('.modal-tab.active[onclick*="server"]');
      if (t) switchGeneralTab('server', t);
      else if (btn) { btn.disabled = false; btn.textContent = orig; }
    }, 1500);
  }
}

async function restartCrawl4ai(btn) {
  if (!confirm('Den crawl4ai-Render-Dienst hart neu starten?\n\nJS-gerenderte Abrufe fallen kurzzeitig auf einfaches HTTP zurück, bis er wieder hochgefahren ist.')) return;
  const orig = btn?.textContent || 'crawl4ai neu starten';
  if (btn) { btn.disabled = true; btn.textContent = 'Wird neu gestartet…'; }
  try {
    const r = await API.post('/v1/crawl4ai/restart', {});
    if (r && r.ok) showToast('crawl4ai wird neu gestartet');
    else showToast(r?.error || 'Neustart fehlgeschlagen', true);
  } catch (e) {
    showToast('Neustart fehlgeschlagen: ' + (e?.message || e), true);
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
function _renderSearxngEngines(sxe, toolState) {
  toolState = toolState || (typeof window !== 'undefined' ? window._searxngToolState : null) || {};
  const ROW = 'display:flex;align-items:center;gap:8px;padding:4px 12px';
  const MONO = 'font-family:var(--font-mono);font-size:11px;color:var(--text-300)';
  const fmtAgo = (t) => !t ? 'nie' : (function(s){
    if (s < 60) return 'vor ' + s + 's';
    if (s < 3600) return 'vor ' + Math.round(s/60) + 'm';
    return 'vor ' + Math.round(s/3600) + 'h';
  })(Math.max(0, Math.round(Date.now()/1000 - t)));
  const fmtIn = (t) => !t ? '—' : (function(s){
    if (s <= 0) return 'fällig';
    if (s < 3600) return 'in ' + Math.round(s/60) + 'm';
    return 'in ' + Math.round(s/3600) + 'h';
  })(Math.round(t - Date.now()/1000));
  // state → {colour, dot}: ok = healthy, fail/error = down (wasting resources),
  // empty = alive but no match for the probe query (situational engine).
  const STATE = {
    ok:    { c: 'var(--success)',          label: 'ok' },
    fail:  { c: 'var(--error)',            label: 'Fehler' },
    error: { c: 'var(--error)',            label: 'Fehler' },
    empty: { c: 'var(--text-400)',         label: 'kein Treffer' },
  };
  const engines = (sxe && Array.isArray(sxe.engines)) ? sxe.engines : [];
  const testBtn = `<button class="btn-secondary" onclick="testSearxngEngines(this)">Jetzt testen</button>`;

  // Category → {label, tool}: names the search tool each category backs, so the
  // panel reads as "these engines power science_search" etc. Order matches the
  // health probe's _TRACKED_CATEGORIES.
  const CATS = [
    { key: 'general', label: 'Allgemeine Websuche', tool: 'searxng_search' },
    { key: 'science', label: 'Wissenschaft', tool: 'science_search' },
    { key: 'it',      label: 'Programmierung / Technik', tool: 'dev_search' },
    { key: 'images',  label: 'Bilder', tool: 'image_search' },
    { key: 'news',    label: 'Nachrichten', tool: 'news_search' },
  ];
  const engineRow = (e) => {
    const st = STATE[e.state] || { c: 'var(--text-400)', label: esc(e.state || '?') };
    const healthy = e.state === 'ok' || e.state === 'empty';
    return `<div style="${ROW}">
      ${DOT(healthy)}
      <span style="font-size:13px;color:var(--text-100);flex:1">${esc(e.name || '')}</span>
      <span style="${MONO}">${e.latency_ms != null ? e.latency_ms + 'ms' : ''}</span>
      <span style="font-size:11px;color:${st.c};min-width:54px;text-align:right" title="${esc(e.detail||'')}">${st.label}</span>
    </div>`;
  };

  let body;
  if (!sxe || sxe.error) {
    body = `<div style="${ROW}">${DOT(false)}<span style="font-size:13px;color:var(--text-100);flex:1">${esc(sxe?.error || 'Engine-Zustand nicht verfügbar')}</span></div>`;
  } else if (!engines.length) {
    body = `<div style="${ROW}"><span style="font-size:12px;color:var(--text-400);flex:1">Noch keine Prüfung durchgeführt — der erste automatische Test läuft kurz nach dem Start, oder klicken Sie auf „Jetzt testen".</span></div>`;
  } else if (engines.some(e => e.category)) {
    // Categorized snapshot (v9.288+): group engines under the search tool they
    // back. An engine with no category (older snapshot rows) falls to 'general'.
    const grouped = {};
    engines.forEach(e => { (grouped[e.category || 'general'] ||= []).push(e); });
    const CATHDR = 'font-size:11px;font-weight:600;color:var(--text-200);padding:8px 12px 2px;display:flex;gap:8px;align-items:center';
    body = CATS.filter(c => (grouped[c.key] || []).length).map(c => {
      // Per-tool enable toggle — reflects + writes tool_settings.<tool>.state.
      // Default 'active' when the tool has no record (matches _global_tool_enabled).
      const on = (toolState[c.tool] || 'active') !== 'inactive';
      const toggle = `<label style="margin-left:auto;display:flex;align-items:center;gap:5px;font-weight:400;cursor:pointer" title="Werkzeug ${esc(c.tool)} aktivieren/deaktivieren">
          <input type="checkbox" ${on ? 'checked' : ''} onchange="toggleSearchTool('${esc(c.tool)}', this)">
          <span style="font-size:10px;color:var(--text-400)">${on ? 'aktiv' : 'aus'}</span>
        </label>`;
      return `<div style="${CATHDR}"><span>${esc(c.label)}</span>` +
        `<span style="${MONO};font-weight:400">${esc(c.tool)}</span>${toggle}</div>` +
        grouped[c.key].map(engineRow).join('');
    }).join('');
  } else {
    body = engines.map(engineRow).join('');
  }

  const tested = (sxe && sxe.tested_at) ? sxe.tested_at : 0;
  const nextAt = (sxe && sxe.next_auto_at) ? sxe.next_auto_at : 0;
  const meta = `<div style="font-size:11px;color:var(--text-400);padding:2px 12px;display:grid;grid-template-columns:auto auto;gap:4px 18px">
      <span>Letzter Test</span><span style="${MONO}">${fmtAgo(tested)}</span>
      <span>Nächster Auto-Test</span><span style="${MONO}">${nextAt ? fmtIn(nextAt) : 'alle 4 h'}</span>
    </div>`;

  return `<div style="font-size:11px;color:var(--text-400);padding:6px 12px 2px">Such-Werkzeuge nach Kategorie mit dem Zustand ihrer Engines (isoliert geprüft; fehlerhafte Engines verschwenden bei jeder Suche eine Anfrage). Der Schalter je Kategorie aktiviert/deaktiviert das zugehörige Werkzeug.</div>
    ${body}
    ${meta}
    <div style="display:flex;gap:8px;padding:6px 12px 0">${testBtn}
      <span style="font-size:11px;color:var(--text-400);align-self:center">Ein manueller Test ändert den automatischen 4-Stunden-Zeitplan nicht.</span>
    </div>`;
}

async function toggleSearchTool(tool, cb) {
  // Enable/disable one search tool via its canonical tool_settings state.
  // active = the model may call it; inactive = removed from every tool list.
  const want = cb.checked ? 'active' : 'inactive';
  cb.disabled = true;
  try {
    await API.post('/v1/tools/settings', { name: tool, state: want });
    if (window._searxngToolState) window._searxngToolState[tool] = want;
    const lbl = cb.parentElement && cb.parentElement.querySelector('span');
    if (lbl) lbl.textContent = cb.checked ? 'aktiv' : 'aus';
    showToast(`${tool}: ${cb.checked ? 'aktiviert' : 'deaktiviert'}`);
  } catch (e) {
    cb.checked = !cb.checked;  // revert on failure
    showToast('Umschalten fehlgeschlagen: ' + (e?.message || e), true);
  } finally {
    cb.disabled = false;
  }
}

async function testSearxngEngines(btn) {
  const orig = btn?.textContent || 'Jetzt testen';
  if (btn) { btn.disabled = true; btn.textContent = 'Wird getestet…'; }
  try {
    const snap = await API.post('/v1/searxng/test-engines', {});
    const panel = document.getElementById('searxng-engines-panel');
    if (panel) panel.innerHTML = _renderSearxngEngines(snap);
    else showToast('Engine-Test abgeschlossen');
  } catch (e) {
    showToast('Engine-Test fehlgeschlagen: ' + (e?.message || e), true);
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// Shared renderer for a ProcessSupervisor status block (searxng + crawl4ai share
// the same status dict shape). `opts.restartFn` is the onclick handler name,
// `opts.note` the warning shown next to the restart button, `opts.disabledHint`
// the config key shown when auto_start is off.
function _renderSupervisorStatus(sc, opts) {
  const ROW = 'display:flex;align-items:center;gap:8px;padding:6px 12px';
  const MONO = 'font-family:var(--font-mono);font-size:11px;color:var(--text-300)';
  if (!sc) {
    return `<div style="${ROW}">${DOT(false)}<span style="font-size:13px;color:var(--text-100);flex:1">Status nicht verfügbar</span></div>`;
  }
  if (!sc.enabled) {
    return `<div style="${ROW}">${DOT(false)}<span style="font-size:13px;color:var(--text-100);flex:1">Supervisor deaktiviert</span><span style="font-size:11px;color:var(--text-400)">${esc(opts.disabledHint||'')}</span></div>`;
  }
  const running = !!sc.running;
  const healthOk = !!sc.last_health_ok;
  const breaker = !!sc.breaker_open;
  const uptime = running && sc.started_at ? Math.max(0, Math.round(Date.now()/1000 - sc.started_at)) : 0;
  const fmtAgo = (t) => !t ? 'nie' : (function(s){
    if (s<60) return 'vor '+s+'s';
    if (s<3600) return 'vor '+Math.round(s/60)+'m';
    return 'vor '+Math.round(s/3600)+'h';
  })(Math.max(0, Math.round(Date.now()/1000 - t)));
  const statusLabel = breaker ? 'Breaker offen' : (running ? (healthOk ? 'läuft' : 'reagiert nicht') : 'gestoppt');
  const statusColor = breaker ? 'var(--error)' : (running && healthOk ? 'var(--success)' : 'var(--warning, #b45309)');
  return `
    <div style="${ROW}">
      ${DOT(running && healthOk && !breaker)}
      <span style="font-size:13px;color:var(--text-100);flex:1">${esc(sc.url||'')}</span>
      ${running ? `<span style="${MONO}">PID ${sc.pid}</span>` : ''}
      <span style="font-size:11px;color:${statusColor}">${esc(statusLabel)}</span>
    </div>
    <div style="font-size:11px;color:var(--text-400);padding:0 12px;display:grid;grid-template-columns:auto auto;gap:4px 18px">
      ${running ? `<span>Laufzeit</span><span style="${MONO}">${uptime}s</span>` : ''}
      <span>Letzte Zustandsprüfung</span><span style="${MONO}">${healthOk?'ok':'Fehler'} &middot; ${fmtAgo(sc.last_health_at)}</span>
      <span>Abstürze (letzte 60s)</span><span style="${MONO}">${sc.crash_count_60s||0} / ${sc.crash_limit||3}</span>
      ${sc.last_exit_rc !== null && sc.last_exit_rc !== undefined ? `<span>Letzter Exit</span><span style="${MONO}">rc=${sc.last_exit_rc} &middot; ${fmtAgo(sc.last_exit_at)}</span>` : ''}
      ${breaker ? `<span style="color:var(--error)">Sicherung</span><span style="${MONO};color:var(--error)">offen — automatischer Neustart angehalten</span>` : ''}
    </div>
    <div style="display:flex;gap:8px;padding:0 12px;margin-top:6px">
      <button class="btn-secondary" onclick="${opts.restartFn}(this)">${breaker ? 'Neu starten & Sicherung zurücksetzen' : (opts.restartLabel||'Neu starten')}</button>
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
// Section label. Optional second arg `help` appends a "?" icon that reveals the
// explanation in a popover (utils.helpIcon) — keeps prose out of the dialog
// until asked for. The icon stays non-uppercase so the "?" reads cleanly.
const SEC = (title, help) => `<div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:12px 0 4px">${title}${help ? `<span style="text-transform:none">${helpIcon(help)}</span>` : ''}</div>`;

async function switchGeneralTab(tab, btn) {
  if (btn) {
    btn.closest('.modal-tabs').querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
  }
  const C = document.getElementById('general-tab-content');
  C.innerHTML = '<div style="padding:20px;color:var(--text-400)">Lädt…</div>';
  // per-tab body renderers live in settings_general_tabs.js
  const RENDERERS = { server:_genTab_server, models:_genTab_models, 'service-models':_genTab_service_models, providers:_genTab_providers, agents:_genTab_agents, teams:_genTab_teams, nodes:_genTab_nodes, context:_genTab_context, costs:_genTab_costs, quotas:_genTab_quotas, mempalace:_genTab_mempalace, 'knowledge-graph':_genTab_knowledge_graph, gdpr:_genTab_gdpr, classification:_genTab_classification, tools:_genTab_tools, 'doc-styles':_genTab_doc_styles, wiki:_genTab_wiki, cleanup:_genTab_cleanup, helpdesk:_genTab_helpdesk, feedback:_genTab_feedback, doctor:_genTab_doctor, libraries:_genTab_libraries };
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
    showToast('Kontext-Konfiguration gespeichert');
  } catch(e) { showToast('Speichern fehlgeschlagen: ' + e.message, true); }
}

async function saveCleanupConfig() {
  try {
    const archive = parseInt(document.getElementById('cleanup-archive-days')?.value);
    const del = parseInt(document.getElementById('cleanup-delete-days')?.value);
    await API.post('/v1/cleanup/config', {
      enabled: document.getElementById('cleanup-enabled')?.checked ?? false,
      archive_after_days: Number.isFinite(archive) && archive >= 0 ? archive : 0,
      delete_after_days: Number.isFinite(del) && del >= 0 ? del : 0,
    });
    showToast('Bereinigungs-Konfiguration gespeichert');
  } catch(e) { showToast('Speichern fehlgeschlagen: ' + e.message, true); }
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
      // Fan-out model: leaf tasks of this chat model offload here ('' = none)
      const bgModel = row.querySelector('.mdl-bgtask-model')?.value?.trim();
      if (bgModel) mc[mid].background_task_model = bgModel;
      else delete mc[mid].background_task_model;
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
      // Cached-input rate. Also the switch that marks a model "cache-priced":
      // a non-zero value freezes Auto model+tool selection to turn 1 so the
      // provider prompt cache hits (brain.model_is_cache_priced). Unset =
      // auto 0.1× of cost_input for billing, and NOT cache-priced (no freeze).
      const ccr = readNum(row, 'mdl-cost-cache-read');
      if (ccr !== undefined) mc[mid].cost_cache_read = ccr; else delete mc[mid].cost_cache_read;
      // Per-model UNIT rates for the char/page/minute-billed services (OCR/TTS/
      // STT). Only the field matching the model's capability is rendered, so an
      // absent input just means "not applicable to this model" → delete the key.
      const cpm = readNum(row, 'mdl-cost-per-minute');
      if (cpm !== undefined) mc[mid].cost_per_minute_usd = cpm; else delete mc[mid].cost_per_minute_usd;
      const cpk = readNum(row, 'mdl-cost-per-1k-chars');
      if (cpk !== undefined) mc[mid].cost_per_1k_chars_usd = cpk; else delete mc[mid].cost_per_1k_chars_usd;
      const cpp = readNum(row, 'mdl-cost-per-page');
      if (cpp !== undefined) mc[mid].cost_per_page_usd = cpp; else delete mc[mid].cost_per_page_usd;
      // Abrechnungskonto: '' = Feld weg ⇒ ERBT die Provider-Vorgabe;
      // 'none' = Sentinel, der die Provider-Vorgabe bewusst ignoriert (kein Plan);
      // '__flat__' = generische Flatrate (flat_plan:true); sonst Plan-Id.
      // Flat-Pläne buchen $0; Credit-Konten bleiben echte Token-Abrechnung
      // (Typ steht am Plan). Auflösung: brain.resolve_model_plan_id.
      const planSel = row.querySelector('.mdl-coding-plan')?.value;
      if (planSel === '__flat__') { mc[mid].flat_plan = true; delete mc[mid].coding_plan; }
      else if (planSel) { mc[mid].coding_plan = planSel; delete mc[mid].flat_plan; }
      else { delete mc[mid].flat_plan; delete mc[mid].coding_plan; }
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
      // Caveman system level (per-model DEFAULT output style — v9.120.0; no
      // longer compresses the system prompt, only appends a terse response style)
      const cavSys = readNum(row, 'mdl-caveman-system');
      if (cavSys) mc[mid].caveman_system = cavSys; else delete mc[mid].caveman_system;
      // Parallel tool calls
      const ptc = row.querySelector('.mdl-parallel-tools')?.checked;
      if (ptc === false) mc[mid].parallel_tool_calls = false; else delete mc[mid].parallel_tool_calls;
      // Auto-LCM (automatic context compaction). Default OFF (opt-in per model);
      // persist explicit true only, drop the key when off.
      const autoLcm = row.querySelector('.mdl-auto-lcm')?.checked;
      if (autoLcm === true) mc[mid].auto_lcm = true; else delete mc[mid].auto_lcm;
      // scratchpad_mode — per-model: off | simple | sequential | calibrate | auto. Replaces
      // the legacy force_think / force_sequential_thinking booleans (which are
      // dropped on save). "off" is the default; persist only a non-off value.
      const spMode = row.querySelector('.mdl-scratchpad-mode')?.value || 'off';
      if (spMode && spMode !== 'off') mc[mid].scratchpad_mode = spMode;
      else delete mc[mid].scratchpad_mode;
      // Drop legacy keys so they can't shadow the new field.
      delete mc[mid].force_think;
      delete mc[mid].force_sequential_thinking;
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
      // Capabilities — canonical {chat, image, audio, audio_transcription, tts,
      // video} checkboxes. Order is preserved to keep the saved config diff-stable.
      const _capOrder = ['chat','image','audio','audio_transcription','tts','video'];
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
    showToast('Modell-Konfiguration gespeichert');
  } catch(e) { showToast('Speichern fehlgeschlagen: ' + e.message, true); }
}

async function addManualModel() {
  const id = document.getElementById('add-model-id')?.value?.trim();
  const provider = document.getElementById('add-model-provider')?.value?.trim();
  const display = document.getElementById('add-model-display')?.value?.trim();
  if (!id) { showToast('Modell-ID erforderlich', true); return; }
  if (!provider) { showToast('Provider erforderlich', true); return; }
  const mc = state.modelsConfig?.models || {};
  if (mc[id]) { showToast('Modell existiert bereits', true); return; }
  mc[id] = { enabled: true, provider, display_name: display || id, shortname: id, priority: 10, capabilities: [], icon: '\u{1F916}', manual: true };
  state.modelsConfig.models = mc;
  try {
    await API.post('/v1/models/config', { action: 'save', models: mc });
    showToast('Modell hinzugefügt');
    switchGeneralTab('models');
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
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
    showToast('Modell entfernt');
    switchGeneralTab('models');
  } catch(e) { showToast('Fehlgeschlagen: ' + e.message, true); }
}

async function _confirmRemoveModel(mid) {
  if (!await showConfirmDanger(`Modell ${mid} entfernen?`, 'Modell entfernen', 'Entfernen')) return;
  removeModel(mid);
}

async function resyncProvider(btn, name) {
  if (!await showConfirmDanger(`Vollständige Neusynchronisierung von „${name}"? Dies löscht ALLE seine Modelle UND deren Lösch-Tombstones und ermittelt sie dann erneut über den /models-Endpunkt des Providers.`, 'Vollständige Neusynchronisierung', 'Neu synchronisieren')) return;
  btn.disabled = true; const orig = btn.textContent; btn.textContent = 'Synchronisiere…';
  try {
    await API.post('/v1/models/config', { action: 'resync_provider', provider: name });
    const d = await API.getModelsConfig();
    state.modelsConfig = d;
    switchGeneralTab('providers');
    showToast(`${name} vollständig neu synchronisiert`);
  } catch(e) {
    showToast('Neusynchronisierung fehlgeschlagen: ' + e.message, true);
    btn.disabled = false; btn.textContent = orig;
  }
}

async function syncProvider(btn, name) {
  btn.disabled = true; btn.textContent = 'Synchronisiere…';
  try {
    await API.post('/v1/models/config', { action: 'sync', provider: name });
    showToast(`${name} wird synchronisiert…`);
    setTimeout(async () => {
      const d = await API.getModelsConfig();
      state.modelsConfig = d;
      switchGeneralTab('providers');
      showToast(`${name} synchronisiert`);
    }, 3000);
  } catch(e) { showToast('Synchronisierung fehlgeschlagen', true); btn.disabled = false; btn.textContent = 'Sync'; }
}

async function saveProviderEdit(name, pid) {
  const base_url = document.getElementById(`${pid}-url`).value;
  const default_model = document.getElementById(`${pid}-model`).value;
  const is_local = !!document.getElementById(`${pid}-is-local`)?.checked;
  const wire_api = document.getElementById(`${pid}-wire-api`)?.value || 'openai';
  // Coding-Plan-Vorgabe: gilt für alle Modelle dieses Providers, die keinen
  // eigenen Plan gesetzt haben (Modell sticht Provider — resolve_model_plan_id).
  // Leer = keine Vorgabe (der Resolver liest '' als "nicht gesetzt").
  const coding_plan = document.getElementById(`${pid}-coding-plan`)?.value || '';
  const provider = { base_url, default_model, is_local, wire_api, coding_plan };
  try {
    await API.post('/v1/providers', { action: 'add', name, provider });
    showToast('Provider aktualisiert');
    switchGeneralTab('providers');
  } catch(e) { showToast('Speichern fehlgeschlagen: ' + e.message, true); }
}

async function openProviderKeysModal(provName) {
  document.getElementById('_prov-keys-modal')?.remove();
  const overlay = document.createElement('div');
  overlay.id = '_prov-keys-modal';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:640px;padding:20px;display:grid;gap:12px">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:15px;font-weight:600;color:var(--text-000)">API-Schlüssel — ${esc(provName)}</span>
      <span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">preferred → round-robin → fallback</span>
      <button class="modal-close" style="margin-left:auto" onclick="document.getElementById('_prov-keys-modal')?.remove()">&times;</button>
    </div>
    <div id="_prov-keys-list" style="display:grid;gap:6px"><span style="color:var(--text-400);font-size:12px">Lädt…</span></div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" style="font-size:12px" onclick="openKeyEditModal('${esc(provName)}','')">+ Schlüssel hinzufügen</button>
      <button class="btn-secondary" style="font-size:12px;margin-left:auto" onclick="openProviderStatsModal('${esc(provName)}')">Statistiken anzeigen →</button>
    </div>
  </div>`;
  overlay.addEventListener('click', e => { if (e.target===overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  await _renderProviderKeysList(provName);
}

async function _renderProviderKeysList(provName) {
  const target = document.getElementById('_prov-keys-list');
  if (!target) return;
  const USAGE_LABELS = {preferred:'Bevorzugt (Prio 1)',round_robin:'Round-Robin (Prio 2)',fallback:'Fallback (Prio 3)'};
  const USAGE_COLORS = {preferred:'var(--accent)',round_robin:'var(--text-200)',fallback:'var(--text-400)'};
  try {
    const provs = await API.getProviders();
    const providers = Array.isArray(provs) ? provs : (provs.providers || []);
    state.providers = providers;
    const prov = providers.find(p => p.name === provName);
    const keys = prov?.api_keys || [];
    if (!keys.length) {
      target.innerHTML = `<span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">Noch keine Schlüssel — auf „+ Schlüssel hinzufügen" klicken</span>`;
      return;
    }
    target.innerHTML = keys.map(k => {
      const usageColor = USAGE_COLORS[k.usage]||'var(--text-200)';
      const deadlinePart = k.deadline ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">gültig bis ${esc(k.deadline.slice(0,10))}</span>` : '';
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:var(--bg-100);border-radius:6px;flex-wrap:wrap">
        <span style="font-size:13px;font-weight:500;color:var(--text-100);min-width:100px">${esc(k.name)}</span>
        <span style="font-family:var(--font-mono);color:${usageColor};font-size:11px">${esc(USAGE_LABELS[k.usage]||k.usage)}</span>
        <span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">${esc(k.key_hint||'')}</span>
        ${deadlinePart}
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px;margin-left:auto" onclick="openKeyEditModal('${esc(provName)}','${esc(k.name)}')">Bearbeiten</button>
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="deleteProviderKey('${esc(provName)}','${esc(k.name)}')">Löschen</button>
      </div>`;
    }).join('');
  } catch(e) {
    target.innerHTML = `<span style="color:var(--error);font-size:12px">Laden fehlgeschlagen: ${esc(e.message||'error')}</span>`;
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
      <span style="font-size:15px;font-weight:600;color:var(--text-000)">Nutzungsstatistiken — ${esc(provName)}</span>
      <select class="form-select" id="_prov-stats-days" style="width:auto;font-size:12px;padding:2px 8px;margin-left:8px"
        onchange="openProviderStatsModal('${esc(provName)}', this.value)">
        <option value="7"${d===7?' selected':''}>Letzte 7 Tage</option>
        <option value="30"${d===30?' selected':''}>Letzte 30 Tage</option>
        <option value="90"${d===90?' selected':''}>Letzte 90 Tage</option>
        <option value="365"${d===365?' selected':''}>Letzte 365 Tage</option>
      </select>
      <button class="modal-close" style="margin-left:auto" onclick="document.getElementById('_prov-stats-modal')?.remove()">&times;</button>
    </div>
    <div id="_prov-stats-body"><span style="color:var(--text-400);font-size:12px">Lädt…</span></div>
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
      body.innerHTML = `<span style="font-family:var(--font-mono);color:var(--text-400);font-size:11px">Keine Nutzung im ausgewählten Zeitraum.</span>`;
      return;
    }
    const totals = `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:10px;background:var(--bg-100);border-radius:8px">
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Aufrufe</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.calls)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens ein</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.tokens_in)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Tokens aus</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtNum(ps.tokens_out)}</div></div>
      <div><div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px">Kosten</div><div style="font-size:18px;font-weight:600;color:var(--text-000)">${fmtCost(ps.cost_usd)}</div></div>
    </div>`;
    const keys = (ps.keys || []).slice().sort((a,b)=>b.calls-a.calls);
    const headerRow = `<div style="display:grid;grid-template-columns:1.4fr repeat(5, 1fr);gap:8px;padding:6px 8px;font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid var(--border-100)">
      <div>Schlüssel</div><div style="text-align:right">Aufrufe</div><div style="text-align:right">Tokens ein</div><div style="text-align:right">Tokens aus</div><div style="text-align:right">Kosten</div><div style="text-align:right">Zuletzt verwendet</div>
    </div>`;
    const rows = keys.length ? keys.map(k => `<div style="display:grid;grid-template-columns:1.4fr repeat(5, 1fr);gap:8px;padding:6px 8px;font-size:12px;border-bottom:1px solid var(--border-100)">
      <div style="color:var(--text-100);font-weight:500;overflow:hidden;text-overflow:ellipsis">${esc(k.key_name||'(unknown)')}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-100)">${fmtNum(k.calls)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-200)">${fmtNum(k.tokens_in)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-200)">${fmtNum(k.tokens_out)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-100)">${fmtCost(k.cost_usd)}</div>
      <div style="font-family:var(--font-mono);text-align:right;color:var(--text-400);font-size:10px">${esc((k.last_used||'').slice(0,16).replace('T',' '))}</div>
    </div>`).join('') : `<div style="padding:8px;font-family:var(--font-mono);color:var(--text-400);font-size:11px">Keine Aufschlüsselung pro Schlüssel verfügbar.</div>`;
    body.innerHTML = `${totals}
      <div style="margin-top:8px">
        <div style="font-size:11px;font-weight:500;color:var(--text-200);margin-bottom:4px">Pro API-Schlüssel</div>
        ${headerRow}${rows}
      </div>`;
  } catch(e) {
    const body = document.getElementById('_prov-stats-body');
    if (body) body.innerHTML = `<span style="color:var(--error);font-size:12px">Statistiken konnten nicht geladen werden: ${esc(e.message||'error')}</span>`;
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
    <div style="font-size:15px;font-weight:600;color:var(--text-000)">${isEdit?'API-Schlüssel bearbeiten':'API-Schlüssel hinzufügen'}</div>
    <div><label class="form-label">Schlüsselname</label><input class="form-input" id="_kmod-name" placeholder="z. B. main, backup-1" value="${isEdit?esc(keyName):''}"></div>
    <div><label class="form-label">API-Schlüsselwert</label><input class="form-input" id="_kmod-key" type="password" placeholder="${isEdit?'leer lassen, um aktuellen zu behalten':'sk-...'}"></div>
    <div><label class="form-label">Verwendung / Priorität</label>
      <select class="form-select" id="_kmod-usage" style="width:100%">
        <option value="preferred">Bevorzugt (Prio 1 — zuerst verwendet, Round-Robin unter gleichrangigen)</option>
        <option value="round_robin">Round-Robin (Prio 2 — wenn kein bevorzugter verfügbar)</option>
        <option value="fallback">Fallback (Prio 3 — letzte Möglichkeit)</option>
      </select>
    </div>
    <div><label class="form-label">Ablaufdatum <span style="color:var(--text-400)">(optional, ISO-Datum z. B. 2026-12-31)</span></label>
      <input class="form-input" id="_kmod-deadline" placeholder="2026-12-31">
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="saveKeyFromModal('${esc(provName)}','${isEdit?esc(keyName):''}')">Speichern</button>
      <button class="btn-secondary" onclick="document.getElementById('_key-edit-modal')?.remove()">Abbrechen</button>
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
  if (!name) { showToast('Schlüsselname erforderlich', true); return; }
  const isEdit = !!oldKeyName;
  // If editing and no new key value, we need to look up the existing key from stored list
  // The server only has the masked hint, so require the value if it's a new key;
  // for edits without a new value, send a special _keep_key_value flag
  const entry = { name, usage };
  if (keyVal) entry.key = keyVal;
  else if (!isEdit) { showToast('API-Schlüsselwert für neuen Schlüssel erforderlich', true); return; }
  else entry._keep_key_value = true;
  if (deadline) entry.deadline = deadline;
  try {
    await API.post('/v1/providers', { action: 'save_key', name: provName, key_entry: entry, old_name: oldKeyName });
    showToast('Schlüssel gespeichert');
    document.getElementById('_key-edit-modal')?.remove();
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    if (document.getElementById('_prov-keys-modal')) await _renderProviderKeysList(provName);
    else switchGeneralTab('providers');
  } catch(e) { showToast('Speichern fehlgeschlagen: ' + e.message, true); }
}

async function deleteProviderKey(provName, keyName) {
  if (!await showConfirmDanger(`Schlüssel „${keyName}" von ${provName} löschen?`, 'Schlüssel löschen', 'Löschen')) return;
  try {
    await API.post('/v1/providers', { action: 'delete_key', name: provName, key_name: keyName });
    showToast('Schlüssel gelöscht');
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    if (document.getElementById('_prov-keys-modal')) await _renderProviderKeysList(provName);
    else switchGeneralTab('providers');
  } catch(e) { showToast('Löschen fehlgeschlagen: ' + e.message, true); }
}

async function testProvider(name) {
  try {
    const r = await API.post('/v1/providers/test', { name });
    showToast(r.status === 'ok' ? `${name}: verbunden` : `${name}: ${r.error||'fehlgeschlagen'}`, r.status !== 'ok');
  } catch(e) { showToast('Test fehlgeschlagen: ' + e.message, true); }
}

async function deleteProvider(name) {
  try {
    await API.post('/v1/providers', { action: 'delete', name });
    showToast('Provider gelöscht');
    switchGeneralTab('providers');
  } catch(e) { showToast('Löschen fehlgeschlagen: ' + e.message, true); }
}

async function _confirmDeleteProvider(name) {
  if (!await showConfirmDanger(`Provider ${name} löschen?`, 'Provider löschen', 'Löschen')) return;
  deleteProvider(name);
}

async function renameProvider(name) {
  const next = await showPrompt(`Provider „${name}" umbenennen in:`, name);
  if (next === null) return;
  const trimmed = next.trim();
  if (!trimmed || trimmed === name) return;
  if (/[\s/]/.test(trimmed)) { showToast("Der Name darf kein '/' und keine Leerzeichen enthalten", true); return; }
  try {
    await API.post('/v1/providers', { action: 'rename', name, new_name: trimmed });
    showToast(`Umbenannt in ${trimmed}`);
    switchGeneralTab('providers');
  } catch(e) { showToast('Umbenennen fehlgeschlagen: ' + e.message, true); }
}

async function testNewProvider() {
  const res = document.getElementById('prov-test-result');
  res.innerHTML = '<span style="color:var(--text-400)">Wird getestet…</span>';
  try {
    const r = await API.post('/v1/providers/test', {
      base_url: document.getElementById('prov-url')?.value,
      api_key: document.getElementById('prov-key')?.value,
    });
    res.innerHTML = r.status === 'ok'
      ? `<span style="color:var(--success)">Verbunden (${r.models||0} Modelle)</span>`
      : `<span style="color:var(--error)">${esc(r.error||'Fehlgeschlagen')}</span>`;
  } catch(e) { res.innerHTML = `<span style="color:var(--error)">${esc(e.message)}</span>`; }
}

async function saveNewProvider() {
  const name = document.getElementById('prov-name')?.value?.trim();
  if (!name) { showToast('Name erforderlich', true); return; }
  const rawKey = document.getElementById('prov-key')?.value || '';
  const provider = {
    base_url: document.getElementById('prov-url')?.value || '',
    default_model: document.getElementById('prov-model')?.value || '',
    is_local: !!document.getElementById('prov-is-local')?.checked,
    wire_api: document.getElementById('prov-wire-api')?.value || 'openai',
    api_keys: rawKey ? [{ name: 'default', key: rawKey, usage: 'preferred' }] : [],
  };
  try {
    await API.post('/v1/providers', { action: 'add', name, provider });
    showToast('Provider hinzugefügt');
    state.providers = await API.getProviders().then(p => Array.isArray(p) ? p : (p.providers||[]));
    switchGeneralTab('providers');
  } catch(e) { showToast('Hinzufügen fehlgeschlagen: ' + e.message, true); }
}

async function createNode() {
  const name = document.getElementById('node-name')?.value?.trim();
  if (!name) { showToast('Name erforderlich', true); return; }
  try {
    const r = await API.post('/v1/nodes', {
      action: 'add', name,
      description: document.getElementById('node-desc')?.value || '',
    });
    const res = document.getElementById('node-result');
    if (r.token) {
      res.innerHTML = `<div style="margin-top:8px;padding:10px;background:var(--bg-200);border-radius:8px"><div style="font-size:12px;font-weight:500;color:var(--text-200);margin-bottom:4px">Installations-Token:</div><code style="font-size:11px;color:var(--accent-brand);word-break:break-all">${esc(r.token)}</code></div>`;
    }
    showToast('Node erstellt');
    switchGeneralTab('nodes');
  } catch(e) { showToast('Erstellen fehlgeschlagen: ' + e.message, true); }
}

async function _confirmRemoveNode(name) {
  if (!await showConfirmDanger('Node entfernen?', 'Node entfernen', 'Entfernen')) return;
  try {
    await API.post('/v1/nodes', { action: 'remove', name });
    showToast('Entfernt');
    switchGeneralTab('nodes');
  } catch(e) { showToast('Entfernen fehlgeschlagen: ' + e.message, true); }
}


