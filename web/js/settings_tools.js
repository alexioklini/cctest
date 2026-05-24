// settings_tools.js — tool settings, research-mode disciplines, integrations, KG + mempalace-classifier + classification config. Split from settings.js (Tier F Phase 2). Global <script>, no modules.

/* ── Per-tool registry UI (Commit 5b) ── */

function toggleToolGroup(groupName) {
  const body = document.getElementById('group-body-' + groupName);
  const chev = document.getElementById('group-chevron-' + groupName);
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  if (chev) chev.textContent = open ? '▸' : '▾';
}

function toggleToolPanel(toolName) {
  const panel = document.getElementById('tool-panel-' + toolName);
  const chev = document.getElementById('chevron-' + toolName);
  if (!panel) return;
  const open = panel.style.display !== 'none';
  if (open) {
    panel.style.display = 'none';
    if (chev) chev.style.transform = '';
  } else {
    panel.innerHTML = renderToolPanelBody(toolName);
    panel.style.display = 'block';
    if (chev) chev.style.transform = 'rotate(90deg)';
  }
}

function renderToolPanelBody(toolName) {
  const t = (window._toolSettingsCache || {})[toolName];
  if (!t) return '<div style="color:var(--error)">No data for ' + toolName + '</div>';
  const cfg = (window._toolConfigCache || {})[toolName] || null;
  const status = (window._toolStatusCache || {})[toolName] || null;
  const allTools = Object.keys(window._toolSettingsCache || {}).filter(n => n !== toolName);

  const txt = (id, label, val, rows=4) => `
    <div style="margin-bottom:10px">
      <div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:3px">${label}</div>
      <textarea id="${id}" rows="${rows}" class="form-input" style="width:100%;font-family:var(--font-mono);font-size:11px;resize:vertical">${esc(val||'')}</textarea>
    </div>`;

  // applies_with multi-select
  const aw = new Set(t.applies_with || []);
  const awOptions = allTools.map(n =>
    `<option value="${esc(n)}" ${aw.has(n)?'selected':''}>${esc(n)}</option>`
  ).join('');

  // purposes — checkboxes for the canonical 4 purposes. Empty selection
  // is rendered as "all purposes" (the resolver treats empty list as no
  // filter). Cached canonical list comes from the GET response.
  const allPurposes = window._toolPurposesCanonical || ['interactive', 'transform', 'memory_summary', 'research_minimal'];
  const havePurposes = new Set(t.purposes || []);
  const purposeChecks = allPurposes.map(p => `
    <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:11px">
      <input type="checkbox" class="ts-purpose" data-purpose="${esc(p)}"
             id="ts-${esc(toolName)}-purpose-${esc(p)}" ${havePurposes.has(p)?'checked':''}>
      <span style="font-family:var(--font-mono)">${esc(p)}</span>
    </label>`).join('');

  // Integration knobs section (only for tools that have one in tool_config)
  let integHTML = '';
  if (cfg) {
    integHTML = `
      <div style="margin-bottom:14px;padding:10px;border:1px dashed var(--border-100);border-radius:6px;background:var(--bg-100)">
        <div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between">
          <span>Integration</span>
          ${status ? `<span style="font-size:10px;color:var(--text-400)">${esc(status.status||'')}</span>` : ''}
        </div>
        ${renderToolIntegrationFields(toolName, cfg)}
        <div style="margin-top:8px;display:flex;justify-content:flex-end">
          <button class="btn-secondary" onclick="saveToolIntegration('${esc(toolName)}')" style="padding:4px 12px;font-size:11px">Save integration</button>
        </div>
      </div>`;
  }

  // Integration-only pseudo-tools (refinement, translation, text_to_speech,
  // gmail, code_graph): no TOOL_DISPATCH entry → no prompt prose, no purposes,
  // no applies_with. Render the integration block alone.
  if (t.integration_only) {
    return `
      <div style="font-size:11px;color:var(--text-400);margin-bottom:10px">
        Integration-only — this entry configures a service used by the server (no agent-callable tool).
      </div>
      ${integHTML || '<div style="color:var(--error);font-size:11px">No integration fields registered for this entry.</div>'}`;
  }

  return `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:12px">
      <div>
        <div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:3px">Group</div>
        <div style="font-size:12px;font-family:var(--font-mono);color:var(--text-100);padding:6px 8px;background:var(--bg-100);border-radius:4px">${esc(t.group || '(ungrouped)')}</div>
      </div>
      <div style="display:flex;gap:14px;align-items:end">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="ts-${esc(toolName)}-enabled" ${t.enabled?'checked':''}>
          <span style="font-size:12px;color:var(--text-100)">Enabled</span>
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="ts-${esc(toolName)}-deferred" ${t.deferred?'checked':''}>
          <span style="font-size:12px;color:var(--text-100)" title="Hide from initial tool list; expose only via tool_search">Deferred</span>
        </label>
      </div>
    </div>

    ${integHTML}

    <div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px">Prompt prose</div>
    ${txt('ts-' + toolName + '-description', 'Description', t.description, 6)}
    ${txt('ts-' + toolName + '-when_to_use', 'When to use', t.when_to_use, 3)}
    ${txt('ts-' + toolName + '-warnings', 'Warnings', t.warnings, 3)}
    ${txt('ts-' + toolName + '-examples', 'Examples', t.examples, 4)}

    <div style="margin-bottom:10px">
      <div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:3px">
        Purposes <span style="text-transform:none;font-weight:400">— call purposes where this tool is allowed. Empty (all unchecked) = available for every purpose.</span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:14px;padding:6px 8px;background:var(--bg-100);border-radius:4px" id="ts-${esc(toolName)}-purposes-wrap">
        ${purposeChecks}
      </div>
    </div>

    <div style="margin-bottom:10px">
      <div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:3px">
        Applies with <span style="text-transform:none;font-weight:400">— prose renders only when ALL selected tools are also active (Cmd/Ctrl-click for multi-select)</span>
      </div>
      <select id="ts-${esc(toolName)}-applies_with" multiple size="4" class="form-input" style="width:100%;font-family:var(--font-mono);font-size:11px">${awOptions}</select>
    </div>

    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn-secondary" onclick="resetToolPromptSettings('${esc(toolName)}')" style="padding:6px 14px;font-size:12px" title="Clear all prose fields and applies_with (does NOT touch enabled/deferred)">Clear prose</button>
      <button class="btn-primary" onclick="saveToolPromptSettings('${esc(toolName)}')" style="padding:6px 14px;font-size:12px">Save</button>
    </div>`;
}

// Renders the integration-knob form for the ~10 tools that have entries in
// /v1/tools/config. Reuses the same field IDs as the legacy bulk save so
// saveToolIntegration can pick them up via document.getElementById.
function renderToolIntegrationFields(name, cfg) {
  const lbl = (t) => `<div style="font-size:10px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:2px">${t}</div>`;
  const maskF = (id, val) => `<div style="display:flex;gap:6px;align-items:center">
    <input id="${id}" type="password" value="${esc(val||'')}" class="form-input" style="flex:1;font-family:var(--font-mono);font-size:11px" autocomplete="off">
    <button class="btn-secondary" style="font-size:10px;padding:4px 8px" onclick="const i=document.getElementById('${id}');i.type=i.type==='password'?'text':'password';this.textContent=i.type==='password'?'Show':'Hide'">Show</button>
  </div>`;
  // Build a chat-capable model dropdown. If the saved value isn't currently
  // a configured/enabled chat model, surface it as a "(legacy/missing)"
  // option so the admin can see it instead of it silently flipping.
  const chatModelSelect = (id, sel, placeholderLabel) => {
    const entries = enabledModelsWithCapability('chat');
    const ids = new Set(entries.map(([mid]) => mid));
    let opts = `<option value="">${esc(placeholderLabel || 'Auto')}</option>`;
    if (sel && !ids.has(sel)) {
      opts += `<option value="${esc(sel)}" selected>${esc(sel)} (legacy/missing)</option>`;
    }
    opts += entries.map(([mid]) => modelOption(mid, {selected: mid === sel})).join('');
    return `<select id="${id}" class="form-select" style="font-size:11px;width:100%">${opts}</select>`;
  };
  switch (name) {
    case 'exa_search':
      return `${lbl('API Key')}${maskF('tool-exa-key', cfg.api_key)}
        ${lbl('Default Results Per Query')}
        <input id="tool-exa-num" type="number" min="1" max="50" value="${cfg.default_num_results||5}" class="form-input" style="width:80px;font-family:var(--font-mono);font-size:11px">`;
    case 'searxng_search':
      return `${lbl('SearXNG URL')}<div style="font-size:10px;color:var(--text-400);margin-bottom:2px">Base URL of a self-hosted SearXNG instance, e.g. http://localhost:8888 (its settings.yml must enable the json format).</div>
        <input id="tool-searxng-url" type="text" value="${esc(cfg.url||'')}" class="form-input" style="font-family:var(--font-mono);font-size:11px" placeholder="http://localhost:8888">
        ${lbl('Default Results Per Query')}
        <input id="tool-searxng-num" type="number" min="1" max="50" value="${cfg.default_num_results||5}" class="form-input" style="width:80px;font-family:var(--font-mono);font-size:11px">`;
    case 'gmail':
      return `${lbl('Email')}
        <input id="tool-gmail-email" type="email" value="${esc(cfg.email||'')}" class="form-input" style="font-family:var(--font-mono);font-size:11px">
        ${lbl('App Password')}${maskF('tool-gmail-pass', cfg.app_password)}
        <div style="margin-top:6px"><a href="https://myaccount.google.com/apppasswords" target="_blank" style="font-size:11px;color:var(--accent)">Create app password</a></div>`;
    case 'execute_command':
      return `${lbl('Default Timeout (seconds)')}
        <input id="tool-exec-timeout" type="number" min="1" max="600" value="${cfg.timeout||120}" class="form-input" style="width:100px;font-family:var(--font-mono);font-size:11px">
        ${lbl('Banned Commands (comma-separated)')}
        <input id="tool-exec-banned" type="text" value="${esc((cfg.banned_commands||[]).join(', '))}" class="form-input" style="font-family:var(--font-mono);font-size:11px">`;
    case 'web_fetch':
      return `<div style="display:flex;gap:12px">
        <div>${lbl('Timeout (seconds)')}<input id="tool-wf-timeout" type="number" min="1" max="120" value="${cfg.timeout||30}" class="form-input" style="width:80px;font-family:var(--font-mono);font-size:11px"></div>
        <div>${lbl('Max Size (MB)')}<input id="tool-wf-maxsize" type="number" min="1" max="100" value="${cfg.max_size_mb||10}" class="form-input" style="width:80px;font-family:var(--font-mono);font-size:11px"></div>
      </div>`;
    case 'refinement':
      return `${lbl('Model')}
        ${chatModelSelect('tool-refine-model', cfg.model || '', 'Auto (Haiku > Sonnet > cheapest)')}
        <div style="font-size:10px;color:var(--text-400);margin-top:4px">Model used by the refine button in chat and note-AI inputs.</div>`;
    case 'read_document': {
      const visEntries = enabledModelsWithCapability('image');
      const visIds = new Set(visEntries.map(([mid]) => mid));
      const visSel = cfg.vision_model || '';
      let visOpts = `<option value="">Auto (cheapest vision model)</option>`;
      if (visSel && !visIds.has(visSel)) visOpts += `<option value="${esc(visSel)}" selected>${esc(visSel)} (legacy/missing)</option>`;
      visOpts += visEntries.map(([mid]) => modelOption(mid, {selected: mid === visSel})).join('');
      return `${lbl('Max File Size (MB)')}
        <input id="tool-rdoc-maxsize" type="number" min="1" max="200" value="${cfg.max_file_size_mb||50}" class="form-input" style="width:100px;font-family:var(--font-mono);font-size:11px">
        ${lbl('Vision Model (for images)')}
        <select id="tool-rdoc-vision-model" class="form-select" style="font-size:11px;width:100%">${visOpts}</select>`;
    }
    case 'code_graph':
      return `${lbl('Exclude Directories (comma-separated)')}
        <input id="tool-cg-exclude" type="text" value="${esc(cfg.exclude_dirs||'node_modules,.git,__pycache__,venv')}" class="form-input" style="font-family:var(--font-mono);font-size:11px">
        ${lbl('Max File Size (KB)')}
        <input id="tool-cg-maxsize" type="number" min="50" max="5000" value="${cfg.max_file_size_kb||500}" class="form-input" style="width:100px;font-family:var(--font-mono);font-size:11px">`;
    case 'transcribe_audio': {
      // Capability-filtered dropdown: only models tagged `audio` are
      // selectable. Saved-but-missing/uncapable values surface as
      // `(legacy/missing)` so the admin can see what's there.
      const audioEntries = enabledModelsWithCapability('audio');
      const audioIds = new Set(audioEntries.map(([mid]) => mid));
      const audioSelectHtml = (id, sel) => {
        let opts = '';
        if (sel && !audioIds.has(sel)) opts += `<option value="${esc(sel)}" selected>${esc(sel)} (legacy/missing)</option>`;
        opts += audioEntries.map(([mid]) => modelOption(mid, {selected: mid === sel})).join('');
        return `<select id="${id}" class="form-select" style="font-size:11px;width:100%">${opts}</select>`;
      };
      return `${lbl('Default Model')}
        ${audioSelectHtml('tool-ta-default-model', cfg.default_model || '')}
        ${lbl('Fallback Model')}
        ${audioSelectHtml('tool-ta-fallback-model', cfg.fallback_model || '')}
        <div style="font-size:10px;color:var(--text-400);margin-top:4px">Only models tagged with the <code>audio</code> capability are listed. The tool will use the configured id exactly — fuzzy name matching has been removed.</div>`;
    }
    case 'text_to_speech': {
      const ttsEntries = enabledModelsWithCapability('tts');
      const ttsIds = new Set(ttsEntries.map(([mid]) => mid));
      const sel = cfg.default_model || '';
      let opts = '';
      if (sel && !ttsIds.has(sel)) opts += `<option value="${esc(sel)}" selected>${esc(sel)} (legacy/missing)</option>`;
      opts += ttsEntries.map(([mid]) => modelOption(mid, {selected: mid === sel})).join('');
      return `${lbl('Default Model')}
        <select id="tool-tts-model" class="form-select" style="font-size:11px;width:100%">${opts}</select>
        ${lbl('Voice')}
        <input id="tool-tts-voice" type="text" value="${esc(cfg.voice||'en_paul_neutral')}" class="form-input" style="font-family:var(--font-mono);font-size:11px">
        <div style="font-size:10px;color:var(--text-400);margin-top:4px">Only models tagged with the <code>tts</code> capability are listed. The tool will use the configured id exactly.</div>`;
    }
    case 'translation':
      return `${lbl('Default Model')}
        ${chatModelSelect('tool-tr-model', cfg.default_model || '', 'Auto (refinement model → fallback)')}
        <div style="font-size:10px;color:var(--text-400);margin-top:4px">Chat-capable LLM used to translate text, documents, and audio/video segments. Separate from the transcription model (Voxtral/Whisper) and TTS.</div>`;
    default:
      return `<div style="font-size:11px;color:var(--text-400)">No integration fields for this tool.</div>`;
  }
}

async function saveToolPromptSettings(toolName) {
  const t = (window._toolSettingsCache || {})[toolName];
  if (!t) { showToast('No cached record for ' + toolName, true); return; }
  const get = (suffix) => document.getElementById('ts-' + toolName + '-' + suffix);
  const aw = [...(get('applies_with')?.selectedOptions || [])].map(o => o.value);
  // Purposes: read the checked checkboxes inside the wrapper.
  const purposesWrap = document.getElementById('ts-' + toolName + '-purposes-wrap');
  const purposes = purposesWrap
    ? [...purposesWrap.querySelectorAll('.ts-purpose:checked')].map(cb => cb.dataset.purpose)
    : (t.purposes || []);
  const body = {
    name: toolName,
    enabled: !!get('enabled')?.checked,
    deferred: !!get('deferred')?.checked,
    description: get('description')?.value || '',
    when_to_use: get('when_to_use')?.value || '',
    warnings: get('warnings')?.value || '',
    examples: get('examples')?.value || '',
    applies_with: aw,
    purposes: purposes,
  };
  try {
    const resp = await API.post('/v1/tools/settings', body);
    showToast('Saved ' + toolName);
    // Update local cache so the row badges + collapse state reflect new flags
    if (window._toolSettingsCache && resp.tool) {
      window._toolSettingsCache[toolName] = { ...window._toolSettingsCache[toolName], ...resp.tool };
    }
    // Refresh deferred badge inline
    const badge = document.getElementById('defer-badge-' + toolName);
    if (badge) badge.style.display = body.deferred ? 'inline' : 'none';
    // Refresh prose badge inline (★ in the collapsed row header)
    const row = document.querySelector('.tool-row[data-tool="' + toolName + '"]');
    if (row) {
      const headerSpans = row.querySelectorAll(':scope > div > span');
      const proseSpan = [...headerSpans].find(s => s.textContent.trim().startsWith('★'));
      const hasProse = !!(body.description || body.when_to_use || body.warnings || body.examples);
      if (hasProse && !proseSpan) {
        // Insert a new prose badge after the name span
        const nameSpan = headerSpans[0];
        if (nameSpan) {
          const star = document.createElement('span');
          star.title = 'Custom prompt prose configured';
          star.style.cssText = 'font-size:10px;color:var(--accent)';
          star.textContent = '★ prose';
          nameSpan.parentNode.insertBefore(star, nameSpan.nextSibling);
        }
      } else if (!hasProse && proseSpan) {
        proseSpan.remove();
      }
    }
  } catch(e) {
    showToast('Save failed: ' + (e.message || e), true);
  }
}

function resetToolPromptSettings(toolName) {
  const get = (suffix) => document.getElementById('ts-' + toolName + '-' + suffix);
  ['description', 'when_to_use', 'warnings', 'examples'].forEach(f => {
    const el = get(f);
    if (el) el.value = '';
  });
  const aw = get('applies_with');
  if (aw) [...aw.options].forEach(o => o.selected = false);
  showToast('Cleared (not saved — click Save to persist)');
}

/* ── Research-mode disciplines (D2 + D3) ── */

function resetResearchModeDiscipline(section) {
  const ta = document.getElementById('rmd-' + section);
  const dft = (window._rmdResp?.defaults || {})[section];
  if (!ta || dft === undefined) {
    showToast('No default available for ' + section, true);
    return;
  }
  ta.value = dft;
  showToast('Reset (not saved — click Save disciplines to persist)');
}

async function saveResearchModeDisciplines() {
  const sections = (window._rmdResp?.section_order) || ['refusal', 'precision', 'citation'];
  const body = {};
  for (const k of sections) {
    const ta = document.getElementById('rmd-' + k);
    if (ta) body[k] = ta.value;
  }
  try {
    const resp = await API.post('/v1/research-mode/disciplines', body);
    showToast('Disciplines saved');
    if (window._rmdResp) window._rmdResp.sections = resp.sections;
  } catch(e) {
    showToast('Save failed: ' + (e.message || e), true);
  }
}

async function saveToolIntegration(toolName) {
  // Reuse the legacy field IDs that saveToolsConfig built around. Each branch
  // here mirrors that function's per-tool block, but POSTs only this one
  // tool's record so the server's full-replacement-per-tool semantics work.
  let rec;
  switch (toolName) {
    case 'exa_search':
      rec = {
        api_key: document.getElementById('tool-exa-key')?.value || '',
        default_num_results: parseInt(document.getElementById('tool-exa-num')?.value) || 5,
      };
      break;
    case 'searxng_search':
      rec = {
        url: document.getElementById('tool-searxng-url')?.value?.trim() || '',
        default_num_results: parseInt(document.getElementById('tool-searxng-num')?.value) || 5,
      };
      break;
    case 'gmail':
      rec = {
        enabled: window._toolConfigCache?.gmail?.enabled !== false,
        email: document.getElementById('tool-gmail-email')?.value || '',
        app_password: document.getElementById('tool-gmail-pass')?.value || '',
      };
      break;
    case 'execute_command':
      const banned = (document.getElementById('tool-exec-banned')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
      rec = {
        timeout: parseInt(document.getElementById('tool-exec-timeout')?.value) || 120,
        banned_commands: banned,
      };
      break;
    case 'web_fetch':
      rec = {
        timeout: parseInt(document.getElementById('tool-wf-timeout')?.value) || 30,
        max_size_mb: parseInt(document.getElementById('tool-wf-maxsize')?.value) || 10,
      };
      break;
    case 'refinement':
      rec = {
        enabled: window._toolConfigCache?.refinement?.enabled !== false,
        model: document.getElementById('tool-refine-model')?.value || '',
      };
      break;
    case 'read_document':
      rec = {
        max_file_size_mb: parseInt(document.getElementById('tool-rdoc-maxsize')?.value) || 50,
        vision_model: document.getElementById('tool-rdoc-vision-model')?.value || '',
      };
      break;
    case 'code_graph':
      rec = {
        enabled: window._toolConfigCache?.code_graph?.enabled !== false,
        exclude_dirs: document.getElementById('tool-cg-exclude')?.value || '',
        max_file_size_kb: parseInt(document.getElementById('tool-cg-maxsize')?.value) || 500,
      };
      break;
    case 'transcribe_audio':
      rec = {
        default_model: document.getElementById('tool-ta-default-model')?.value || '',
        fallback_model: document.getElementById('tool-ta-fallback-model')?.value || '',
      };
      break;
    case 'text_to_speech':
      rec = {
        enabled: window._toolConfigCache?.text_to_speech?.enabled !== false,
        default_model: document.getElementById('tool-tts-model')?.value || '',
        voice: document.getElementById('tool-tts-voice')?.value || '',
      };
      break;
    case 'translation':
      rec = {
        enabled: window._toolConfigCache?.translation?.enabled !== false,
        default_model: document.getElementById('tool-tr-model')?.value || '',
      };
      break;
    default:
      showToast('No integration save handler for ' + toolName, true);
      return;
  }
  try {
    await API.post('/v1/tools/config', { [toolName]: rec });
    showToast('Integration saved for ' + toolName);
    if (window._toolConfigCache) window._toolConfigCache[toolName] = rec;
  } catch(e) {
    showToast('Save failed: ' + (e.message || e), true);
  }
}

async function saveMpClassifier() {
  try {
    const cats = [...document.querySelectorAll('.mp-clf-cat:checked')].map(c => c.value);
    await API.post('/v1/mempalace/classifier', {
      enabled: document.getElementById('mp-clf-enabled')?.checked ?? false,
      model: document.getElementById('mp-clf-model')?.value || '',
      min_turns: parseInt(document.getElementById('mp-clf-min-turns')?.value) || 0,
      default_mode: parseInt(document.getElementById('mp-clf-default-mode')?.value) || 0,
      categories_to_file: cats,
    });
    // Refresh cached classifier config
    state.mempalaceClassifier = await API.get('/v1/mempalace/classifier').catch(() => ({}));
    showToast('Classifier config saved');
  } catch(e) { showToast('Save failed: ' + e.message, true); }
}

function _kgShowInfo(title, htmlBody) {
  const modalId = '__kgInfoModal';
  let m = document.getElementById(modalId);
  if (m) m.remove();
  m = document.createElement('div');
  m.id = modalId;
  m.className = 'modal-overlay';
  m.style.zIndex = '12001';
  m.innerHTML = `<div class="modal-content" style="max-width:780px;width:80vw;max-height:80vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">${esc(title)}</span>
      <button class="btn-secondary" style="margin-left:auto;font-size:11px;padding:4px 10px" onclick="document.getElementById('${modalId}').remove()">Close</button>
    </div>
    <div class="modal-body" style="overflow:auto;flex:1;padding:14px">${htmlBody}</div>
  </div>`;
  document.body.appendChild(m);
  m.addEventListener('click', (ev) => { if (ev.target === m) m.remove(); });
}

async function saveKgConfig() {
  try {
    const body = {
      enabled: document.getElementById('kg-enabled')?.checked ?? true,
      extraction_model: document.getElementById('kg-model')?.value || '',
      profile: document.getElementById('kg-profile')?.value || 'normative',
      max_triples_per_drawer: parseInt(document.getElementById('kg-max-triples')?.value) || 12,
      min_confidence: parseFloat(document.getElementById('kg-min-conf')?.value) || 0.5,
      max_drawer_chars: parseInt(document.getElementById('kg-max-chars')?.value) || 6000,
      regenerate_closets: document.getElementById('kg-regen-closets')?.checked ?? false,
    };
    await API.post('/v1/mempalace/kg/config', body);
    showToast('Knowledge Graph settings saved');
    switchGeneralTab('knowledge-graph');
  } catch(e) { showToast('Save failed: ' + (e.message || e), true); }
}

async function kgOpenProject(agentId, projectName) {
  // Modal with per-project drilldown: stats, top predicates, top entities,
  // sample triples, recent extraction-log rows. Admin gets a Re-extract
  // button.
  const modalId = '__kgProjectModal';
  let m = document.getElementById(modalId);
  if (m) m.remove();
  m = document.createElement('div');
  m.id = modalId;
  m.className = 'modal-overlay';
  m.style.zIndex = '12000';
  m.innerHTML = `<div class="modal-content" style="max-width:980px;width:90vw;max-height:90vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">Knowledge Graph &mdash; ${esc(projectName)}</span>
      <span style="font-size:11px;color:var(--text-400);font-family:var(--font-mono)">${esc(agentId)}</span>
      <button class="btn-secondary" style="margin-left:auto;font-size:11px;padding:4px 10px" onclick="document.getElementById('${modalId}').remove()">Close</button>
    </div>
    <div class="modal-body" id="kg-project-body" style="overflow:auto;flex:1;padding:16px">Loading…</div>
  </div>`;
  document.body.appendChild(m);
  m.addEventListener('click', (ev) => { if (ev.target === m) m.remove(); });

  try {
    const data = await API.get(`/v1/mempalace/kg/wing?agent_id=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}`);
    const isAdmin = state.authUser && state.authUser.role === 'admin';
    const body = document.getElementById('kg-project-body');
    if (!body) return;

    const STAT = (val, label) => `<div style="padding:10px 16px;background:var(--bg-200);border-radius:8px;text-align:center;min-width:110px">
      <div style="font-size:20px;font-weight:600;color:var(--accent-brand)">${val}</div>
      <div style="font-size:11px;color:var(--text-400)">${label}</div>
    </div>`;

    // Predicate frequency bar — biggest at left
    const maxPredCount = Math.max(1, ...(data.top_predicates||[]).map(p => p.count));
    const predBars = (data.top_predicates||[]).map(p => {
      const w = Math.max(8, Math.round(p.count / maxPredCount * 100));
      return `<div style="display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer" onclick="kgSearchPredicate('${esc(agentId)}','${esc(projectName)}','${esc(p.predicate)}')">
        <span style="font-family:var(--font-mono);min-width:160px;color:var(--text-200)">${esc(p.predicate)}</span>
        <div style="flex:1;height:14px;background:var(--bg-200);border-radius:3px;position:relative;overflow:hidden">
          <div style="height:100%;width:${w}%;background:var(--accent-brand);opacity:0.7"></div>
        </div>
        <span style="min-width:40px;text-align:right;color:var(--text-300);font-family:var(--font-mono)">${p.count}</span>
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No triples yet.</div>';

    // Top entities
    const entRows = (data.top_entities||[]).slice(0,15).map(e =>
      `<div style="display:flex;gap:8px;align-items:center;font-size:12px;padding:4px 0;cursor:pointer" onclick="kgQueryEntity('${esc(agentId)}','${esc(projectName)}','${esc(e.name||'').replace(/'/g,"\\'")}')">
        <span style="flex:1;color:var(--text-100);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.name||e.id)}</span>
        <span style="font-size:10px;padding:1px 5px;background:var(--bg-300);border-radius:3px;color:var(--text-400)">${esc(e.type||'unknown')}</span>
        <span style="min-width:30px;text-align:right;color:var(--text-300);font-family:var(--font-mono)">${e.degree}</span>
      </div>`
    ).join('') || '<div style="font-size:12px;color:var(--text-400)">No entities.</div>';

    // Sample triples
    const sampleRows = (data.sample_triples||[]).map(t => {
      const sf = (t.source_file||'').split('/').slice(-2).join('/');
      const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
      return `<div style="padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;margin-bottom:6px;font-size:12px">
        <div style="display:flex;gap:8px;align-items:flex-start">
          <span style="color:var(--text-100);flex:1">(${esc(t.subject)})</span>
          <span style="font-family:var(--font-mono);color:var(--accent-brand)">--[${esc(t.predicate)}]--&gt;</span>
          <span style="color:var(--text-100);flex:1">(${esc(t.object)})</span>
          ${conf?`<span style="font-family:var(--font-mono);color:var(--text-400)">c=${conf}</span>`:''}
        </div>
        <div style="font-family:var(--font-mono);font-size:10px;color:var(--text-400);margin-top:3px">${esc(sf)}</div>
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No triples extracted yet.</div>';

    // Extraction log
    const logRows = (data.extraction_log||[]).slice(0,15).map(r => {
      const dt = r.started_at ? new Date(r.started_at*1000).toLocaleString() : '?';
      const ok = !r.error_msg && (r.errors||0) === 0;
      const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${ok?'var(--success)':'var(--error)'};margin-right:6px"></span>`;
      return `<div style="font-size:11px;padding:4px 0;border-bottom:1px dotted var(--border-100)">
        ${dot}<span style="font-family:var(--font-mono);color:var(--text-300)">${esc(dt)}</span>
        seen=${r.drawers_seen||0} new=${r.drawers_processed||0} skip=${r.drawers_skipped||0}
        triples=<b>${r.triples_extracted||0}</b> errors=${r.errors||0}
        ${r.error_msg?`<span style="color:var(--error)" title="${esc(r.error_msg)}"> · ${esc((r.error_msg||'').slice(0,80))}</span>`:''}
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No runs yet.</div>';

    body.innerHTML = `
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
        ${STAT((data.entities||0).toLocaleString(),'Entities')}
        ${STAT((data.triples||0).toLocaleString(),'Relations')}
        ${STAT((data.top_predicates||[]).length,'Predicates seen')}
        ${STAT((data.extraction_log||[]).length,'Runs logged')}
      </div>
      ${isAdmin?`<div style="display:flex;gap:8px;justify-content:flex-end;margin-bottom:14px">
        <button class="btn-secondary" onclick="kgReextract('${esc(agentId)}','${esc(projectName)}')">Re-extract everything</button>
      </div>`:''}

      <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:8px 0 6px">Predicate frequency</div>
      <div style="display:grid;gap:4px">${predBars}</div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:18px">
        <div>
          <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Top entities by degree</div>
          ${entRows}
        </div>
        <div>
          <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Recent extraction runs</div>
          ${logRows}
        </div>
      </div>

      <div style="font-size:12px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin:18px 0 6px">Sample triples (highest confidence)</div>
      <div>${sampleRows}</div>
    `;
  } catch(e) {
    const body = document.getElementById('kg-project-body');
    if (body) body.innerHTML = `<div style="color:var(--error)">Failed to load: ${esc(e.message||e)}</div>`;
  }
}

async function kgQueryEntity(agentId, projectName, entityName) {
  try {
    const data = await API.get(`/v1/mempalace/kg/entity?agent_id=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}&name=${encodeURIComponent(entityName)}`);
    const triples = (data.triples||[]).map(t => {
      const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
      return `<div style="padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;margin-bottom:6px;font-size:12px">
        <div style="display:flex;gap:8px;align-items:flex-start">
          <span style="color:var(--text-100);flex:1">(${esc(t.subject||'')})</span>
          <span style="font-family:var(--font-mono);color:var(--accent-brand)">--[${esc(t.predicate||'')}]--&gt;</span>
          <span style="color:var(--text-100);flex:1">(${esc(t.object||'')})</span>
          ${conf?`<span style="font-family:var(--font-mono);color:var(--text-400)">c=${conf}</span>`:''}
        </div>
      </div>`;
    }).join('') || '<div style="font-size:12px;color:var(--text-400)">No triples for this entity.</div>';
    _kgShowInfo(`Entity: ${entityName}`, `<div style="font-size:11px;color:var(--text-400);margin-bottom:8px">${data.count} triples (${data.total_in_kg} in KG before scope filter)</div>${triples}`);
  } catch(e) { showToast('Lookup failed: ' + (e.message||e), true); }
}

async function kgSearchPredicate(agentId, projectName, predicate) {
  try {
    const data = await API.get(`/v1/mempalace/kg/wing?agent_id=${encodeURIComponent(agentId)}&project=${encodeURIComponent(projectName)}`);
    const filtered = (data.sample_triples||[]).filter(t => t.predicate === predicate);
    const list = filtered.map(t => {
      const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
      return `<div style="padding:6px 0;font-size:12px;border-bottom:1px dotted var(--border-100)">
        (${esc(t.subject||'')}) <span style="font-family:var(--font-mono);color:var(--accent-brand)">[${esc(predicate)}]</span> (${esc(t.object||'')}) ${conf?`<span style="font-family:var(--font-mono);color:var(--text-400);margin-left:6px">c=${conf}</span>`:''}
      </div>`;
    }).join('') || `<div style="font-size:12px;color:var(--text-400)">No "${esc(predicate)}" triples in the sample (full set may have more — ${(data.top_predicates||[]).find(p=>p.predicate===predicate)?.count||0} total).</div>`;
    _kgShowInfo(`Triples with predicate: ${predicate}`, list);
  } catch(e) { showToast('Search failed: ' + (e.message||e), true); }
}

async function kgReextract(agentId, projectName) {
  if (!await showConfirmDanger(`Purge all triples for "${projectName}" and re-extract from scratch?\n\nThis deletes existing triples + the extraction cursor, then queues the project for the next sync cycle (within 30 min, or trigger Sync now from the project panel).`, 'Re-extract KG', 'Purge & Re-extract')) return;
  try {
    const res = await API.post('/v1/mempalace/kg/reextract', {agent_id: agentId, project: projectName});
    showToast(`Purged ${res.triples_deleted||0} triples · queued for re-extraction`);
    setTimeout(()=>kgOpenProject(agentId, projectName), 500);
  } catch(e) { showToast('Re-extract failed: ' + (e.message||e), true); }
}

function clsRestoreDefaultKw(level) {
  const C = document.getElementById('general-tab-content');
  const defs = (C && C.__clsDefaults) || {};
  const ta = document.getElementById('cls-kw-' + level);
  if (!ta) return;
  ta.value = (defs[level] || []).join('\n');
}

function clsAddExtraRow() {
  const box = document.getElementById('cls-extras-box');
  if (!box) return;
  // First-time: replace the "No extra patterns" placeholder
  if (box.querySelector('.cls-extra-row') === null) box.innerHTML = '';
  const i = box.querySelectorAll('.cls-extra-row').length;
  const row = document.createElement('div');
  row.className = 'cls-extra-row';
  row.dataset.i = i;
  row.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:6px';
  row.innerHTML = `
    <select class="form-select cls-extra-level" style="width:140px;font-size:12px">
      <option value="public">Öffentlich</option>
      <option value="internal">Intern</option>
      <option value="confidential" selected>Vertraulich</option>
      <option value="strict">Streng Vertraulich</option>
    </select>
    <input type="text" class="form-input cls-extra-pattern" style="flex:1;font-family:monospace;font-size:12px" placeholder="Regex pattern">
    <button class="btn-secondary" style="font-size:11px;padding:2px 8px" onclick="this.parentElement.remove()">Remove</button>`;
  box.appendChild(row);
}

async function clsSaveSettings() {
  const status = document.getElementById('cls-settings-status');
  status.style.color = 'var(--text-400)';
  status.textContent = 'Saving…';
  try {
    const keywords = {};
    for (const lvl of ['internal', 'confidential', 'strict']) {
      const ta = document.getElementById('cls-kw-' + lvl);
      keywords[lvl] = (ta.value || '').split('\n')
        .map(s => s.trim()).filter(Boolean);
    }
    const extra_patterns = [];
    document.querySelectorAll('#cls-extras-box .cls-extra-row').forEach(row => {
      const level = row.querySelector('.cls-extra-level').value;
      const pattern = row.querySelector('.cls-extra-pattern').value.trim();
      if (pattern) extra_patterns.push({level, pattern});
    });
    // Policy block — present only when the Phase B section is rendered
    let policy = null;
    const enEl = document.getElementById('cls-policy-enabled');
    if (enEl) {
      const per_level_action = {};
      document.querySelectorAll('.cls-policy-action').forEach(sel => {
        per_level_action[sel.dataset.level] = sel.value;
      });
      policy = {
        enabled: enEl.checked,
        server_block: document.getElementById('cls-policy-server-block').checked,
        server_log: document.getElementById('cls-policy-server-log').checked,
        default_local_fallback_model: document.getElementById('cls-policy-fallback').value || '',
        per_level_action,
      };
    }
    const body = {keywords, extra_patterns};
    if (policy) body.policy = policy;
    await API.post('/v1/classification/config', body);
    status.style.color = 'var(--success,#1b6a31)';
    status.textContent = 'Saved.';
  } catch (e) {
    status.style.color = 'var(--error,#d33)';
    status.textContent = e.message || String(e);
  }
}
