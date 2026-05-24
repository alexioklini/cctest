// settings_general_tabs.js — per-tab body renderers for the General Settings modal (extracted from switchGeneralTab, Tier F Phase 2). Global <script>.
// Shared render helpers (P/G/ROW/DOT/MONO/BADGE/SEC) are defined as module-scope globals in settings_general.js (loaded first).

async function _genTab_server(C) {
  /* ─── SERVER ─── */
    try {
      const [svc, sc, sx, sxe, c4] = await Promise.all([
        API.getServices(),
        API.get('/v1/sidecar/status').catch(() => null),
        API.get('/v1/searxng/status').catch(() => null),
        API.get('/v1/searxng/engines').catch(() => null),
        API.get('/v1/crawl4ai/status').catch(() => null),
      ]);
      const srv = svc.server || {};
      applyGdprConfigToScanner(srv.gdpr_scanner);
      let svcRows = '';
      for (const [name, info] of Object.entries(svc)) {
        if (typeof info !== 'object') continue;
        const ok = info.status === 'running' || info.status === 'ok' || info.connected;
        svcRows += `<div style="${ROW}">${DOT(ok)}
          <span style="font-size:13px;color:var(--text-100);flex:1">${esc(name)}</span>
          ${info.port ? `<span style="${MONO}">:${info.port}</span>` : ''}
          <span style="font-size:11px;color:${ok?'var(--success)':'var(--error)'}">${esc(info.status||(ok?'running':'stopped'))}</span>
        </div>`;
      }
      // Default model selector — chat-capable only.
      const mc = state.modelsConfig?.models || {};
      const enabledModels = enabledModelsWithCapability('chat');
      const modelOpts = enabledModels.map(([mid])=>modelOption(mid, {selected: mid===srv.default_model})).join('');

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          ${DOT(true)}<span style="font-size:14px;font-weight:500;color:var(--text-100)">Connected</span>
          <span style="${MONO};margin-left:auto">${esc(BASE_URL)}</span>
          ${srv.version?`<span style="${MONO}">v${esc(srv.version)}</span>`:''}
          ${srv.pid?`<span style="${MONO}">PID ${srv.pid}</span>`:''}
        </div>
        ${SEC('Services')}${svcRows}
        ${SEC('Default Model')}
        <div style="display:flex;gap:8px;align-items:center">
          <select class="form-select" id="srv-default-model" style="flex:1">${modelOpts}</select>
          <button class="btn-secondary" onclick="API.post('/v1/services/server',{default_model:document.getElementById('srv-default-model').value}).then(()=>showToast('Default model updated')).catch(e=>showToast('Failed',true))">Set</button>
        </div>
        ${SEC('Attachments')}
        <div style="display:flex;gap:8px;align-items:center">
          <select class="form-select" id="srv-attachment-image-model" style="flex:1">
            <option value="">None (images not described)</option>
            ${enabledModelsWithCapability('image').map(([mid])=>modelOption(mid, {selected: mid===(srv.attachment_image_model||'')})).join('')}
          </select>
          <button class="btn-secondary" onclick="API.post('/v1/services/server',{attachment_image_model:document.getElementById('srv-attachment-image-model').value}).then(()=>showToast('Image model updated')).catch(e=>showToast('Failed',true))">Set</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:2px">Vision model used to describe attached images when the active model has no vision support (e.g. gemini-2.5-flash, mistral-small-latest)</div>
        ${(() => {
          const defMdl = srv.default_model || '';
          const hasVision = modelHasCapability(defMdl, 'image');
          const hasImageModel = !!(srv.attachment_image_model);
          return (!hasVision && !hasImageModel) ? `<div style="font-size:11px;color:var(--warning, #b45309);margin-top:4px;padding:6px 8px;border-radius:6px;background:var(--bg-200)">&#9888; Your default model does not support vision and no image description model is configured. Attached images will only return basic metadata (dimensions, format).</div>` : '';
        })()}
        ${SEC('Summaries')}
        <div style="display:flex;gap:8px;align-items:center">
          <select class="form-select" id="srv-chat-summary-model" style="flex:1">
            <option value="">Auto (use server default model)</option>
            ${enabledModels.map(([mid])=>modelOption(mid, {selected: mid===(srv.chat_summary_model||'')})).join('')}
          </select>
          <button class="btn-secondary" onclick="API.post('/v1/services/server',{chat_summary_model:document.getElementById('srv-chat-summary-model').value}).then(()=>showToast('Summary model updated')).catch(e=>showToast('Failed',true))">Set</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:2px">Background model that generates the per-chat synopsis (hover tooltip + collapsible block) and the auto-maintained user profile. Leave on Auto unless you want a specific model.</div>
        ${SEC('Sidecar')}
        ${_renderSupervisorStatus(sc, {
          restartFn: 'restartSidecar',
          restartLabel: 'Restart sidecar',
          note: 'In-flight turns will fail with a sidecar error.',
          disabledHint: 'sidecar.auto_start=false',
        })}
        ${SEC('Web Search (SearXNG)')}
        ${_renderSupervisorStatus(sx, {
          restartFn: 'restartSearxng',
          restartLabel: 'Restart SearXNG',
          note: 'Powers the searxng_search tool. Web searches briefly fail during restart.',
          disabledHint: 'searxng.auto_start=false',
        })}
        <div id="searxng-engines-panel">${_renderSearxngEngines(sxe)}</div>
        ${SEC('Web Rendering (crawl4ai)')}
        ${_renderSupervisorStatus(c4, {
          restartFn: 'restartCrawl4ai',
          restartLabel: 'Restart crawl4ai',
          note: 'Headless-browser fallback for JS-rendered pages in web_fetch + project URL mining. Fetches briefly fall back to plain HTTP during restart.',
          disabledHint: 'crawl4ai.auto_start=false',
        })}
        ${SEC('Cost Quotas')}
        <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          <span style="font-size:12px;color:var(--text-200);flex:1">Per-user, per-role limits with billing-cycle reset.</span>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="switchGeneralTab('quotas', document.querySelector('.modal-tab[onclick*=\\'quotas\\']'))">Configure &rarr;</button>
        </div>
        ${SEC('GDPR / PII Scanner')}
        <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          ${DOT((srv.gdpr_scanner||{}).enabled !== false)}
          <span style="font-size:12px;color:var(--text-200);flex:1">
            ${(srv.gdpr_scanner||{}).enabled !== false ? 'Scanner active' : 'Scanner disabled'}
            ${(srv.gdpr_scanner||{}).server_block ? ' &middot; <b style="color:var(--warning,#b45309)">hard-block on</b>' : ''}
          </span>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="switchGeneralTab('gdpr', document.querySelector('.modal-tab[onclick*=\\'gdpr\\']'))">Configure &rarr;</button>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:2px">Granular category actions, email allowlist, and the local fallback model live in the dedicated GDPR tab.</div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn-secondary" onclick="API.restartServer().then(()=>showToast('Server restarting...')).catch(e=>showToast('Failed',true))">Restart Server</button>
        </div>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--error)">Cannot reach server</div>'); }
}

async function _genTab_models(C) {
  /* ─── MODELS ─── */
    const mc = state.modelsConfig?.models || {};
    // Group by provider, skipping models from non-existent providers
    const existingProviders = new Set((state.providers || []).map(p => p.name));
    const byProvider = {};
    for (const [mid, cfg] of Object.entries(mc)) {
      const prov = cfg.provider || 'unassigned';
      if (prov !== 'unassigned' && !existingProviders.has(prov)) continue;
      (byProvider[prov] = byProvider[prov] || []).push([mid, cfg]);
    }
    const provKeys = Object.keys(byProvider).sort();
    // Sort models within each provider by display name
    for (const pk of provKeys) byProvider[pk].sort((a, b) => {
      const ae = a[1].enabled ? 0 : 1, be = b[1].enabled ? 0 : 1;
      if (ae !== be) return ae - be;
      return (a[1].display_name || modelShortName(a[0], false)).localeCompare(b[1].display_name || modelShortName(b[0], false));
    });

    // Helper: small labeled input for model detail panel
    const mdlInput = (cls, label, val, opts = {}) => {
      const { type = 'number', step, min, max, ph, width, choices } = opts;
      const s = `width:${width||'100%'};padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)`;
      let inp;
      if (choices) {
        inp = `<select class="${cls}" style="${s}">${choices.map(c => `<option value="${c}"${val===c?' selected':''}>${c||'(default)'}</option>`).join('')}</select>`;
      } else {
        inp = `<input class="${cls}" type="${type}" value="${val??''}" style="${s}" ${step?`step="${step}"`:''}${min!=null?` min="${min}"`:''}${max!=null?` max="${max}"`:''}${ph?` placeholder="${ph}"`:''}>`;
      }
      return `<div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px">${label}</label>${inp}</div>`;
    };

    let html = `<div style="${G('6px')}">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <button class="btn-secondary" onclick="this.disabled=true;this.textContent='Syncing...';API.post('/v1/models/config',{action:'sync'}).then(()=>{showToast('Syncing...');setTimeout(()=>API.getModelsConfig().then(d=>{state.modelsConfig=d;switchGeneralTab('models');showToast('Synced')}),3000)}).catch(e=>{showToast('Failed',true);this.disabled=false;this.textContent='Sync from Providers'})">Sync from Providers</button>
      </div>`;
    for (const prov of provKeys) {
      const models = byProvider[prov];
      const provId = `mdl-prov-${prov.replace(/[^a-zA-Z0-9]/g,'_')}`;
      const isOmlx = prov === 'omlx';
      html += `<div style="margin-bottom:6px;border:1px solid var(--border-100);border-radius:8px;overflow:hidden">
        <div style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:8px 12px;background:var(--bg-100)" onclick="const c=document.getElementById('${provId}');const open=c.style.display!=='none';c.style.display=open?'none':'block';this.querySelector('.mdl-arrow').textContent=open?'▶':'▼'">
          <span class="mdl-arrow" style="font-size:10px;color:var(--text-400)">▶</span>
          <span style="font-size:13px;font-weight:600;color:var(--text-100)">${esc(prov)}</span>
          <span style="font-size:11px;color:var(--text-400)">${models.length} model${models.length!==1?'s':''}</span>
          <span style="margin-left:auto;display:flex;gap:4px" onclick="event.stopPropagation()">
            <button class="btn-secondary" style="padding:1px 6px;font-size:10px" onclick="document.querySelectorAll('#${provId} .mdl-enabled').forEach(c=>{c.checked=true;c.closest('.mdl-header-row').style.opacity=1})">All</button>
            <button class="btn-secondary" style="padding:1px 6px;font-size:10px" onclick="document.querySelectorAll('#${provId} .mdl-enabled').forEach(c=>{c.checked=false;c.closest('.mdl-header-row').style.opacity=0.5})">None</button>
          </span>
        </div>
        <div id="${provId}" style="display:none;padding:4px 8px">`;
      for (const [mid, cfg] of models) {
        const inf = cfg.inference || {};
        const detId = `mdl-det-${mid.replace(/[^a-zA-Z0-9]/g,'_')}`;
        html += `<div data-model-id="${esc(mid)}">
          <div style="${ROW};opacity:${cfg.enabled?1:0.5}" class="mdl-header-row">
            <input type="checkbox" class="mdl-enabled" ${cfg.enabled?'checked':''} onchange="this.closest('.mdl-header-row').style.opacity=this.checked?1:0.5">
            <input class="mdl-display-name" value="${esc(cfg.display_name || modelShortName(mid, false))}" style="width:140px;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:12px;background:var(--bg-000);color:var(--text-100)" placeholder="Display name" title="Display name">
            <span style="${MONO};flex:1;overflow:hidden;text-overflow:ellipsis" title="${esc(mid)}">${esc(mid)}</span>
            <span class="mdl-warmup-dot" data-model-dot="${esc(mid)}" style="display:${cfg.warmup?'inline-block':'none'};width:8px;height:8px;border-radius:50%;background:var(--text-500);flex:none" title="Warmup state"></span>
            <label style="font-size:11px;color:var(--text-400)">P</label><input type="number" class="mdl-priority" value="${cfg.priority||0}" style="width:50px;padding:2px 4px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;text-align:center;background:var(--bg-000);color:var(--text-200)">
            <button class="btn-secondary" style="padding:2px 6px;font-size:12px" onclick="const d=document.getElementById('${detId}');d.style.display=d.style.display==='none'?'block':'none'" title="Model settings">&#9881;</button>
            <button class="btn-secondary" style="padding:2px 6px;font-size:10px;color:var(--error)" onclick="_confirmRemoveModel('${esc(mid)}')">&#10005;</button>
          </div>
          <div id="${detId}" style="display:none;padding:8px 12px;margin:0 0 6px 0;border:1px solid var(--border-100);border-top:none;border-radius:0 0 8px 8px;background:var(--bg-100)">
            <div style="margin-bottom:8px">
              <label style="font-size:11px;font-weight:600;color:var(--text-100);display:block;margin-bottom:3px">Description <span style="color:var(--text-400);font-weight:400">(shown as tooltip in model dropdowns)</span></label>
              <textarea class="mdl-description" rows="2" style="width:100%;padding:4px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:12px;background:var(--bg-000);color:var(--text-100);font-family:inherit;resize:vertical" placeholder="e.g. Best for long-context analysis. Slow but cheap.">${esc(cfg.description || '')}</textarea>
            </div>
            <div style="display:flex;align-items:center;gap:10px;padding:6px 8px;margin-bottom:8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000)">
              <label style="font-size:11px;font-weight:600;color:var(--text-100);margin:0">Profile</label>
              <select class="mdl-profile" style="padding:3px 8px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-100);color:var(--text-100)" title="Speed: warmup + stable KV prefix, no token savings (local). Balanced: current defaults. Frugal: aggressive token savings, caveman system prompt (cloud). Custom: no overlay.">
                ${[['custom','Custom (no overlay)'],['speed','Speed (local, warm cache)'],['balanced','Balanced (default)'],['frugal','Frugal (cloud, save tokens)']].map(([v,l]) => `<option value="${v}"${(cfg.profile||'custom')===v?' selected':''}>${l}</option>`).join('')}
              </select>
              <span style="font-size:10px;color:var(--text-400);margin-left:auto">Profile sets defaults — explicit fields below override them</span>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px">
              ${mdlInput('mdl-max-context','Context Window',cfg.max_context,{ph:'131072'})}
              ${mdlInput('mdl-max-output','Max Output',cfg.max_output,{ph:'16384'})}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              ${mdlInput('mdl-inf-temperature','Temperature',inf.temperature,{step:'0.1',min:0,max:2,ph:'1.0'})}
              ${mdlInput('mdl-inf-top_p','Top P',inf.top_p,{step:'0.05',min:0,max:1,ph:'1.0'})}
              ${mdlInput('mdl-inf-top_k','Top K',inf.top_k,{min:0,ph:'(none)'})}
              ${mdlInput('mdl-inf-max_tokens','Max Tokens Override',inf.max_tokens,{ph:'(auto)'})}
              ${mdlInput('mdl-inf-frequency_penalty','Freq Penalty',inf.frequency_penalty,{step:'0.1',min:-2,max:2,ph:'0'})}
              ${mdlInput('mdl-inf-presence_penalty','Pres Penalty',inf.presence_penalty,{step:'0.1',min:-2,max:2,ph:'0'})}
              ${isOmlx ? `
                ${mdlInput('mdl-inf-min_p','Min P',inf.min_p,{step:'0.01',min:0,max:1,ph:'0'})}
                ${mdlInput('mdl-inf-repetition_penalty','Rep Penalty',inf.repetition_penalty,{step:'0.1',min:0,max:2,ph:'1.0'})}
              ` : ''}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              ${mdlInput('mdl-cost-input','Cost In ($/M)',cfg.cost_input,{step:'0.01',min:0,ph:'0'})}
              ${mdlInput('mdl-cost-output','Cost Out ($/M)',cfg.cost_output,{step:'0.01',min:0,ph:'0'})}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px">Caveman System</label>
                <select class="mdl-caveman-system" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  ${[[0,'off'],[1,'lite'],[2,'full'],[3,'ultra']].map(([v,l]) => `<option value="${v}"${(cfg.caveman_system||0)===v?' selected':''}>${l}</option>`).join('')}
                </select>
              </div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="How this model emits reasoning. none = disabled. inline_tags = <think>...</think> in content (DeepSeek-R1, GLM-Zero). reasoning_field = sibling reasoning_content (oMLX with enable_thinking, Gemini 2.5, DeepSeek-R1 direct). mistral_blocks = nested thinking blocks (magistral, mistral-small-2603+). openai_opaque = hidden, only token count exposed (o1/o3/o4-mini).">Thinking Format</label>
                <select class="mdl-thinking-format" data-mid="${esc(mid)}" onchange="_mdlRefreshThinkingLevel(this)" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  ${['none','inline_tags','reasoning_field','mistral_blocks','openai_opaque'].map(v => `<option value="${v}"${(cfg.thinking_format||'none')===v?' selected':''}>${v}</option>`).join('')}
                </select>
              </div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Default thinking level for this model. Used when a chat or scheduled task selects 'Inherit from model'. Available options depend on the Thinking Format.">Thinking Level</label>
                <select class="mdl-thinking-level" data-mid="${esc(mid)}" data-current="${esc((inf||{}).thinking_level||'')}" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                </select>
              </div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-parallel-tools" ${cfg.parallel_tool_calls !== false ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer">Parallel Tool Calls</label></div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-warmup" ${cfg.warmup ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer" title="Prime this model's KV cache once so first-token latency is minimal. The warm state is held until the model is evicted — no periodic re-priming.">Warmup</label></div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Full: prefill system+tools into KV cache (~5-6s first response, costs GPU memory). Minimal: load weights only (~10-15s first response, tiny memory footprint). Full-primed models may evict each other if GPU memory is tight.">Warmup Mode</label>
                <select class="mdl-warmup-mode" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  <option value="full" ${(cfg.warmup_mode||'full')==='full'?'selected':''}>full (KV prefix)</option>
                  <option value="minimal" ${cfg.warmup_mode==='minimal'?'selected':''}>minimal (weights only)</option>
                </select>
              </div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-warmup-allow-cloud" ${cfg.warmup_allow_cloud ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer" title="Permit warmup against cloud providers (costs tokens)">Allow cloud</label></div>
              <div style="grid-column:1/-1"><label class="form-label" style="font-size:11px">Raw Formats <span style="color:var(--text-400);font-weight:400">(MIME patterns the model handles natively as multimodal)</span></label><input class="form-input mdl-raw-formats" value="${esc((cfg.raw_formats||[]).join(', '))}" placeholder="e.g. image/*, application/pdf" style="font-size:12px"></div>
              <div style="grid-column:1/-1"><label class="form-label" style="font-size:11px">Capabilities <span style="color:var(--text-400);font-weight:400">(routing flags — controls where the model is selectable in the UI)</span></label>
                <div class="mdl-capabilities-grid" data-mid="${esc(mid)}" style="display:flex;flex-wrap:wrap;gap:10px;padding:6px 8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)">
                  ${(()=>{
                    const caps = new Set(cfg.capabilities||[]);
                    const opts = [
                      ['chat',  'Chat',  'Selectable in the chat composer + every general model dropdown.'],
                      ['image', 'Image', 'Vision input — used by read_document for image attachments.'],
                      ['audio', 'Audio', 'Speech-to-text — listed under transcribe_audio.'],
                      ['tts',   'TTS',   'Text-to-speech — listed under text_to_speech.'],
                      ['video', 'Video', 'Video input — reserved for video-capable models.'],
                    ];
                    return opts.map(([k,l,t]) => `<label style="display:flex;gap:5px;align-items:center;font-size:11px;cursor:pointer" title="${esc(t)}"><input type="checkbox" class="mdl-cap-cb" data-cap="${k}" ${caps.has(k)?'checked':''}>${l}</label>`).join('');
                  })()}
                </div>
              </div>
            </div>
          </div>
        </div>`;
      }
      html += `</div></div>`;
    }
    // Add Model form
    const knownProvs = [...new Set(Object.values(mc).map(c=>c.provider).filter(Boolean))].sort();
    html += `<div style="margin-top:12px;padding:12px;border:1px solid var(--border-200);border-radius:8px;${G('8px')}">
      <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:4px">Add Model Manually</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
        <div style="flex:2;min-width:180px"><label class="form-label">Model ID</label><input class="form-input" id="add-model-id" placeholder="e.g. my-model-v1"></div>
        <div style="flex:1;min-width:120px"><label class="form-label">Provider</label><input class="form-input" id="add-model-provider" list="add-model-provs" placeholder="provider name"><datalist id="add-model-provs">${knownProvs.map(p=>`<option value="${esc(p)}">`).join('')}</datalist></div>
        <div style="flex:1;min-width:120px"><label class="form-label">Display Name</label><input class="form-input" id="add-model-display" placeholder="Optional"></div>
        <button class="btn-primary" style="height:34px" onclick="addManualModel()">Add</button>
      </div>
    </div>`;
    html += `<div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn-primary" onclick="saveModelsConfig()">Save</button>
    </div></div>`;
    C.innerHTML = P(html);
    // Populate every per-model Thinking Level dropdown using the row's
    // current Thinking Format. The format <select> has an inline onchange
    // that re-renders its sibling level <select> via _mdlRefreshThinkingLevel.
    C.querySelectorAll('.mdl-thinking-level').forEach(sel => {
      const fmtSel = sel.closest('div').parentElement.querySelector('.mdl-thinking-format');
      if (fmtSel) _mdlPopulateThinkingLevel(fmtSel.value || 'none', sel, sel.dataset.current || '');
    });
}

async function _genTab_providers(C) {
  /* ─── PROVIDERS ─── */
    try {
      const [provs, statsResp] = await Promise.all([
        API.getProviders(),
        API.get('/v1/providers/stats?days=30').catch(() => ({stats:[]})),
      ]);
      const providers = Array.isArray(provs) ? provs : (provs.providers || []);
      const statsByProvider = {};
      for (const s of (statsResp.stats || [])) statsByProvider[s.provider] = s;
      let html = `<div style="${G('12px')}">`;
      for (const p of providers) {
        const ok = p.model_count > 0;
        const mc = p.models?.length || p.model_count || 0;
        const pid = `prov-edit-${p.name.replace(/[^a-zA-Z0-9]/g,'_')}`;
        const USAGE_LABELS = {preferred:'Preferred (prio 1)',round_robin:'Round-robin (prio 2)',fallback:'Fallback (prio 3)'};
        const USAGE_COLORS = {preferred:'var(--accent)',round_robin:'var(--text-200)',fallback:'var(--text-400)'};
        const pStats = statsByProvider[p.name];
        const fmtNum = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n||0);
        const keys = p.api_keys || [];
        const keyCounts = {preferred:0, round_robin:0, fallback:0};
        for (const k of keys) keyCounts[k.usage] = (keyCounts[k.usage]||0) + 1;
        const keySummaryParts = [];
        if (keyCounts.preferred) keySummaryParts.push(`${keyCounts.preferred} preferred`);
        if (keyCounts.round_robin) keySummaryParts.push(`${keyCounts.round_robin} round-robin`);
        if (keyCounts.fallback) keySummaryParts.push(`${keyCounts.fallback} fallback`);
        const keySummary = keys.length
          ? `${keys.length} key${keys.length===1?'':'s'}${keySummaryParts.length?` · ${keySummaryParts.join(' · ')}`:''}`
          : 'No keys configured';
        const keySummaryColor = keys.length ? 'var(--text-200)' : 'var(--warning)';
        const provStatsLine = pStats
          ? `${pStats.calls} calls · ${fmtNum(pStats.tokens_in)} in · ${fmtNum(pStats.tokens_out)} out${pStats.cost_usd > 0 ? ' · $'+pStats.cost_usd.toFixed(4) : ''} (30d)`
          : 'No usage in last 30 days';
        html += `<div style="padding:12px;border:1px solid var(--border-100);border-radius:10px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            ${DOT(ok)}
            <span style="font-size:14px;font-weight:500;color:var(--text-000)">${esc(p.name)}</span>
            <span style="${MONO};margin-left:auto">${mc} models</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="syncProvider(this,'${esc(p.name)}')" title="Add newly-available models from this provider. Honors deletions.">Sync</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="resyncProvider(this,'${esc(p.name)}')" title="Drop all models for this provider AND clear deletion tombstones, then re-discover. Manual only.">Full Resync</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="testProvider('${esc(p.name)}')">Test</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="document.getElementById('${pid}').style.display=document.getElementById('${pid}').style.display==='none'?'block':'none'">Settings</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="renameProvider('${esc(p.name)}')" title="Rename this provider. Updates models, default_provider, tombstones, and provider-scoped model ids in one shot.">Rename</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_confirmDeleteProvider('${esc(p.name)}')">Delete</button>
          </div>
          <div style="${MONO};overflow:hidden;text-overflow:ellipsis;margin-bottom:8px">${esc(p.base_url||'')}</div>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 8px;background:var(--bg-100);border-radius:6px">
            <span style="font-size:11px;color:${keySummaryColor};font-weight:500">${keySummary}</span>
            <span style="${MONO};font-size:10px;color:var(--text-400);margin-left:6px">${provStatsLine}</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;margin-left:auto" onclick="openProviderKeysModal('${esc(p.name)}')">Manage Keys</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="openProviderStatsModal('${esc(p.name)}')">Stats</button>
          </div>
          ${(p.models||[]).length?`<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">${(p.models||[]).slice(0,8).map(m=>{const mid=typeof m==='string'?m:(m.id||m);return BADGE(modelShortName(mid,false));}).join('')}${(p.models||[]).length>8?`<span style="${MONO}">+${(p.models||[]).length-8} more</span>`:''}</div>`:''}
          <div id="${pid}" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--border-100)">
            <div style="${G('8px')}">
              <div><label class="form-label">Base URL</label><input class="form-input" id="${pid}-url" value="${esc(p.base_url||'')}"></div>
              <div><label class="form-label">Default Model</label><input class="form-input" id="${pid}-model" value="${esc(p.default_model||'')}"></div>
              <div><label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200);cursor:pointer"><input type="checkbox" id="${pid}-is-local"${p.is_local?' checked':''}> Local provider <span style="color:var(--text-400);font-size:11px">(inference happens on-device — bypasses PII block & cost quotas)</span></label></div>
              <div><button class="btn-primary" style="font-size:12px" onclick="saveProviderEdit('${esc(p.name)}','${pid}')">Save settings</button></div>
            </div>
          </div>
        </div>`;
      }
      html += `
        ${SEC('Add Provider')}
        <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('10px')}">
          <div><label class="form-label">Name</label><input class="form-input" id="prov-name" placeholder="e.g. my-provider"></div>
          <div><label class="form-label">Base URL</label><input class="form-input" id="prov-url" placeholder="http://localhost:8081/v1"></div>
          <div><label class="form-label">API Key</label><input class="form-input" id="prov-key" placeholder="sk-..." type="password"></div>
          <div><label class="form-label">Default Model</label><input class="form-input" id="prov-model" placeholder="model-name (optional)"></div>
          <div><label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200);cursor:pointer"><input type="checkbox" id="prov-is-local"> Local provider <span style="color:var(--text-400);font-size:11px">(inference happens on-device — bypasses PII block & cost quotas)</span></label></div>
          <div style="display:flex;gap:8px">
            <button class="btn-secondary" onclick="testNewProvider()">Test Connection</button>
            <button class="btn-primary" onclick="saveNewProvider()">Add Provider</button>
          </div>
          <div id="prov-test-result"></div>
        </div>
      </div>`;
      C.innerHTML = P(html);
    } catch(e) { C.innerHTML = P('<div style="color:var(--error)">Failed to load providers</div>'); }
}

async function _genTab_agents(C) {
  /* ─── AGENTS ─── */
    const agents = state.agents || [];
    let html = `<div style="${G('12px')}">`;
    html += `${SEC('Create Agent')}
      <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
        <div style="display:flex;gap:8px">
          <div style="flex:1"><label class="form-label">Agent ID</label><input class="form-input" id="new-agent-id" placeholder="e.g. Analyst"></div>
          <div style="flex:1"><label class="form-label">Display Name</label><input class="form-input" id="new-agent-display" placeholder="Optional display name"></div>
        </div>
        <div><label class="form-label">Description</label><input class="form-input" id="new-agent-desc" placeholder="What does this agent do?"></div>
        <div><label class="form-label">Model</label><select class="form-select" id="new-agent-model" style="width:100%">
          <option value="auto" title="Automatically picks the best-fitting model for each message">✨ Auto</option>
          ${enabledModelsWithCapability('chat').map(([mid])=>modelOption(mid)).join('')}
        </select></div>
        <div><label class="form-label">Soul (system prompt)</label><textarea class="form-input" id="new-agent-soul" rows="3" placeholder="Optional initial soul.md content" style="resize:vertical"></textarea></div>
        <div style="display:flex;gap:8px">
          <button class="btn-primary" onclick="_createNewAgent()">Create Agent</button>
        </div>
        <div id="agent-create-result"></div>
      </div>`;
    html += SEC('All Agents');
    for (const a of agents) {
      const aid = a.id || a.name;
      const isMain = aid === 'main';
      html += `<div style="${ROW}">
        <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(a.display_name||aid)}</span>
        <span style="${MONO}">${esc(aid)}</span>
        ${a.model?`<span style="${MONO}">${esc(modelShortName(a.model))}</span>`:''}
        ${a.paused?BADGE('paused','var(--warning)'):''}
        ${a.is_team_head?BADGE('team head','var(--accent)'):''}
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="openAgentConfig('${esc(aid)}');this.closest('.modal-overlay').remove()">Configure</button>
        ${isMain?'':`<button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_deleteAgent('${esc(aid)}')">Delete</button>`}
      </div>`;
    }
    C.innerHTML = P(html + '</div>');
}

async function _genTab_teams(C) {
  /* ─── TEAMS ─── */
    const ts = state.teamStructure;
    const allAgents = state.agents || [];
    let html = `<div style="${G('12px')}">`;

    /* Existing teams */
    if (ts.teams && Object.keys(ts.teams).length) {
      for (const [tid, team] of Object.entries(ts.teams)) {
        html += `<div style="padding:12px;border:1px solid var(--border-100);border-radius:10px">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-size:15px;font-weight:600;color:var(--text-000);flex:1">${esc(team.name||tid)}</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_dissolveTeam('${esc(tid)}')">Dissolve</button>
          </div>
          ${team.description?`<div style="font-size:12px;color:var(--text-400);margin:4px 0">${esc(team.description)}</div>`:''}
          <div style="${G('4px')};margin-top:8px">`;
        for (const m of (team.members||[])) {
          const mid = m.id;
          html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--border-100);border-radius:6px">
            <span style="font-size:13px;color:var(--text-100);flex:1">${esc(m.display_name||mid)}</span>
            <span style="${MONO}">${esc(mid)}</span>
            ${BADGE(m.is_team_head?'head':'member')}
            ${!m.is_team_head?`<button class="btn-secondary" style="padding:1px 6px;font-size:10px;color:var(--error)" onclick="_removeFromTeam('${esc(mid)}','${esc(tid)}')">Remove</button>`:''}
          </div>`;
        }
        html += `</div>
          <div style="display:flex;gap:6px;margin-top:8px;align-items:center">
            <select class="form-select" id="team-add-${esc(tid)}" style="flex:1;font-size:12px">
              <option value="">Add agent to team...</option>
              ${allAgents.filter(a=>{const aid=a.id||a.name;return aid!=='main'&&!(team.members||[]).some(m=>m.id===aid)}).map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
            </select>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="_addToTeam('${esc(tid)}')">Add</button>
          </div>
        </div>`;
      }
    }

    /* Standalone agents */
    if (ts.standalone?.length) {
      html += SEC('Standalone');
      for (const a of ts.standalone) {
        html += `<div style="${ROW}"><span style="font-size:13px;color:var(--text-100);flex:1">${esc(a.display_name||a.id)}</span><span style="${MONO}">${esc(a.id)}</span></div>`;
      }
    }

    /* Create team form */
    html += SEC('Create Team');
    const nonMainAgents = allAgents.filter(a=>(a.id||a.name)!=='main');
    html += `<div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
      <div style="display:flex;gap:8px">
        <div style="flex:1"><label class="form-label">Team Name</label><input class="form-input" id="new-team-name" placeholder="e.g. Research Team"></div>
        <div style="flex:1"><label class="form-label">Description</label><input class="form-input" id="new-team-desc" placeholder="Optional"></div>
      </div>
      <div><label class="form-label">Team Head</label><select class="form-select" id="new-team-head" style="width:100%">
        <option value="">Select head agent...</option>
        ${nonMainAgents.map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
      </select></div>
      <div><label class="form-label">Members (select multiple)</label><select class="form-select" id="new-team-members" multiple style="width:100%;min-height:80px">
        ${nonMainAgents.map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
      </select></div>
      <div style="display:flex;gap:8px">
        <button class="btn-primary" onclick="_createTeam()">Create Team</button>
      </div>
      <div id="team-create-result"></div>
    </div>`;

    C.innerHTML = P(html + '</div>');
}

async function _genTab_nodes(C) {
  /* ─── NODES ─── */
    try {
      const data = await API.get('/v1/nodes');
      const nodes = data.nodes || [];
      let html = `<div style="${G('8px')}">`;
      for (const n of nodes) {
        const ok = n.status === 'connected' || n.status === 'online';
        html += `<div style="padding:12px;border:1px solid var(--border-100);border-radius:10px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            ${DOT(ok)}
            <span style="font-size:14px;font-weight:500;color:var(--text-100)">${esc(n.name||n.id||'node')}</span>
            ${n.paused?BADGE('paused','var(--warning)'):''}
            <span style="${MONO};margin-left:auto">${esc(n.url||n.host||'')}</span>
          </div>
          ${n.description?`<div style="font-size:12px;color:var(--text-400)">${esc(n.description)}</div>`:''}
          <div style="display:flex;gap:4px;margin-top:8px;flex-wrap:wrap">
            ${n.os?`<span style="${MONO}">${esc(n.os)}</span>`:''}
            ${n.hostname?`<span style="${MONO}">${esc(n.hostname)}</span>`:''}
            ${n.cpu_percent!=null?`<span style="${MONO}">CPU ${n.cpu_percent}%</span>`:''}
            ${n.mem_used_gb!=null?`<span style="${MONO}">RAM ${n.mem_used_gb.toFixed(1)}/${n.mem_total_gb?.toFixed(1)||'?'}GB</span>`:''}
          </div>
          <div style="display:flex;gap:6px;margin-top:8px">
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="API.post('/v1/nodes',{action:'${n.paused?'resume':'pause'}',name:'${esc(n.name)}'}).then(()=>{showToast('${n.paused?'Resumed':'Paused'}');switchGeneralTab('nodes')})">${n.paused?'Resume':'Pause'}</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_confirmRemoveNode('${esc(n.name)}')">Remove</button>
          </div>
        </div>`;
      }
      if (!nodes.length) html += '<div style="padding:20px;text-align:center;color:var(--text-400)">No remote nodes configured</div>';
      html += `${SEC('Add Node')}
        <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
          <div><label class="form-label">Name</label><input class="form-input" id="node-name" placeholder="my-node"></div>
          <div><label class="form-label">Description</label><input class="form-input" id="node-desc" placeholder="Optional description"></div>
          <button class="btn-primary" onclick="createNode()">Create Node</button>
          <div id="node-result"></div>
        </div></div>`;
      C.innerHTML = P(html);
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Nodes not available</div>'); }
}

async function _genTab_context(C) {
  /* ─── CONTEXT ─── */
    try {
      const cfg = await API.get('/v1/context/config');
      const enabledModels = enabledModelsWithCapability('chat');
      const modelOpts = `<option value="">Auto (cheapest)</option>` + enabledModels.map(([mid])=>modelOption(mid, {selected: mid===cfg.summary_model})).join('');

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="ctx-enabled" ${cfg.enabled!==false?'checked':''}>
          <label for="ctx-enabled" style="font-size:14px;font-weight:500;color:var(--text-200)">Lossless Context Management enabled</label>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div><label class="form-label">Fresh Tail (messages)</label><input class="form-input" id="ctx-fresh-tail" type="number" value="${cfg.fresh_tail_count||cfg.fresh_tail||16}" min="4" max="200"></div>
          <div><label class="form-label">Compact Threshold (%)</label><input class="form-input" id="ctx-threshold" type="number" value="${Math.round((cfg.compact_threshold||0.6)*100)}" min="50" max="95"></div>
          <div><label class="form-label">Messages per Summary</label><input class="form-input" id="ctx-msgs-per-sum" type="number" value="${cfg.messages_per_summary||10}" min="3" max="50"></div>
          <div><label class="form-label">Condense Threshold</label><input class="form-input" id="ctx-condense" type="number" value="${cfg.condense_threshold||4}" min="2" max="10"></div>
          <div><label class="form-label">Max Depth</label><input class="form-input" id="ctx-max-depth" type="number" value="${cfg.max_depth||5}" min="1" max="10"></div>
          <div><label class="form-label">Summary Target Tokens</label><input class="form-input" id="ctx-target-tokens" type="number" value="${cfg.summary_target_tokens||1000}" min="200" max="4000" step="100"></div>
        </div>
        <div><label class="form-label">Summary Model</label><select class="form-select" id="ctx-summary-model" style="width:100%">${modelOpts}</select></div>
        <button class="btn-primary" onclick="saveContextConfig()">Save</button>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Context config not available</div>'); }
}

async function _genTab_costs(C) {
  /* ─── COSTS ─── */
    try {
      const [stats, daily] = await Promise.all([API.getCosts(24).catch(()=>({})), API.getCostsDaily(7).catch(()=>({daily:[]}))]);
      let html = `<div style="${G('16px')}">
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--accent-brand)">$${(stats.total_cost||0).toFixed(2)}</div>
            <div style="font-size:11px;color:var(--text-400)">Last 24h</div>
          </div>
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--text-000)">${(stats.total_calls||0).toLocaleString()}</div>
            <div style="font-size:11px;color:var(--text-400)">API Calls</div>
          </div>
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--text-000)">${((stats.total_tokens_in||0)+(stats.total_tokens_out||0)).toLocaleString()}</div>
            <div style="font-size:11px;color:var(--text-400)">Total Tokens</div>
          </div>
        </div>
        ${Array.isArray(stats.by_agent)&&stats.by_agent.length?`${SEC('By Agent')}${stats.by_agent.map(s=>`<div style="${ROW}"><span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(s.agent)}</span><span style="${MONO}">${s.calls||0} calls</span><span style="font-size:13px;font-weight:500;color:var(--accent-brand)">$${(s.cost||0).toFixed(3)}</span></div>`).join('')}`:''}
        ${SEC('Daily (7 days)')}`;
      for (const d of (daily.daily||[])) {
        html += `<div style="${ROW}">
          <span style="font-size:13px;color:var(--text-200);font-family:var(--font-mono)">${esc(d.day||d.date||'')}</span>
          <span style="flex:1"></span>
          <span style="${MONO}">${(d.calls||0)} calls</span>
          <span style="${MONO}">${((d.tokens_in||0)+(d.tokens_out||0)).toLocaleString()} tok</span>
          <span style="font-size:13px;font-weight:500;color:var(--text-100)">$${(d.cost||0).toFixed(3)}</span>
        </div>`;
      }
      if (!(daily.daily||[]).length) html += '<div style="padding:20px;text-align:center;color:var(--text-400)">No cost data</div>';
      C.innerHTML = P(html + '</div>');
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Cost data not available</div>'); }
}

async function _genTab_quotas(C) {
  /* ─── QUOTAS ─── */
    if (!state.authUser || state.authUser.role !== 'admin') {
      C.innerHTML = P('<div style="color:var(--text-400);text-align:center;padding:32px">Quota configuration is admin-only.</div>');
      return;
    }
    try {
      const cfg = await API.get('/v1/quotas/config');
      const usersResp = await API.get('/v1/quotas/admin/users').catch(()=>({users:[]}));
      const users = usersResp.users || [];
      const localModels = enabledModelsWithCapability('chat')
        .filter(([,c]) => c.is_local).map(([mid]) => mid);
      const cycleOpts = ['monthly','weekly','yearly'].map(c => `<option value="${c}" ${c===cfg.billing_cycle?'selected':''}>${c}</option>`).join('');
      const enforceOpts = [
        ['warn_only','Warn only (no server-side refusal)'],
        ['force_local','Force local model on red'],
        ['hard_block','Hard block on red'],
      ].map(([v,l]) => `<option value="${v}" ${v===cfg.enforce_red?'selected':''}>${esc(l)}</option>`).join('');
      const fbOpts = ['<option value="">— none —</option>'].concat(
        localModels.map(mid => modelOption(mid, {selected: mid===cfg.default_local_fallback_model, label: modelShortName(mid, true)}))
      ).join('');
      const startDayLabel = (cycle) => ({monthly:'Day of month (1-31)', weekly:'Day of week (0=Mon … 6=Sun)', yearly:'Month of year (1-12)'})[cycle] || 'Start';
      const limitInput = (role, fld) => {
        const v = (cfg.limits[role]||{})[fld] || 0;
        return `<input class="form-input" data-quota-role="${role}" data-quota-field="${fld}" type="number" step="0.01" min="0" value="${v}" style="width:100px;text-align:right">`;
      };
      const colorByLevel = { green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--error)' };
      const levelChip = (lv) => `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;background:var(--bg-200);font-size:11px;color:${colorByLevel[lv]||'var(--text-400)'};font-weight:600;text-transform:uppercase">${lv}</span>`;
      const fmt = (v) => '$' + (v < 1 ? v.toFixed(3) : v.toFixed(2));
      const usersHtml = users.length ? users.map(u => {
        const cycPct = (u.cycle?.pct || 0).toFixed(0);
        const dayPct = (u.daily?.pct || 0).toFixed(0);
        return `<div style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000)">
          ${levelChip(u.level)}
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;color:var(--text-100);font-weight:500;display:flex;align-items:center;gap:6px">
              ${esc(u.display_name || u.username)} <span style="font-size:10px;color:var(--text-400);text-transform:uppercase">${esc(u.role)}</span>
              ${u.has_override ? '<span style="font-size:10px;color:var(--accent-brand)">override</span>' : ''}
              ${u.disabled ? '<span style="font-size:10px;color:var(--error)">disabled</span>' : ''}
            </div>
            <div style="font-size:11px;color:var(--text-300);margin-top:2px">
              today ${fmt(u.daily.used_usd)} / ${fmt(u.daily.limit_usd)} (${dayPct}%) &middot;
              cycle ${fmt(u.cycle.used_usd)} / ${fmt(u.cycle.limit_usd)} (${cycPct}%)
            </div>
          </div>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="quotaOpenUserBreakdown('${esc(u.user_id)}','${esc(u.display_name||u.username)}')">Details</button>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="quotaEditOverride('${esc(u.user_id)}','${esc(u.display_name||u.username)}')">${u.has_override ? 'Edit override' : 'Set override'}</button>
        </div>`;
      }).join('<div style="height:6px"></div>') : '<div style="color:var(--text-400);padding:12px 0">No users.</div>';
      C.innerHTML = P(`<div style="${G('16px')}">
        ${SEC('Cycle')}
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Billing cycle</label>
            <select id="q-billing-cycle" class="form-input" style="width:140px">${cycleOpts}</select>
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)" id="q-start-day-label">${startDayLabel(cfg.billing_cycle)}</label>
            <input id="q-start-day" class="form-input" type="number" min="0" max="31" value="${cfg.cycle_start_day}" style="width:120px">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Warn at (%)</label>
            <input id="q-warn-pct" class="form-input" type="number" min="0" max="100" value="${cfg.warn_pct}" style="width:80px">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Block at (%)</label>
            <input id="q-block-pct" class="form-input" type="number" min="0" max="200" value="${cfg.block_pct}" style="width:80px">
          </div>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200);margin-left:auto">
            <input id="q-enabled" type="checkbox" ${cfg.enabled?'checked':''}> Enabled
          </label>
        </div>

        ${SEC('Enforcement on red')}
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <select id="q-enforce" class="form-input" style="flex:1;max-width:340px">${enforceOpts}</select>
          <div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:200px">
            <label style="font-size:11px;color:var(--text-400)">Local fallback model (force_local mode)</label>
            <select id="q-fallback" class="form-input">${fbOpts}</select>
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-400)">
          <b>Warn only</b>: pill goes red, requests still allowed. <b>Force local</b>: requests automatically swap to the configured local model. <b>Hard block</b>: requests are refused until the cycle resets.
        </div>

        ${SEC('Per-role limits (USD)')}
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="color:var(--text-400);font-size:11px">
            <th style="text-align:left;padding:6px 8px;font-weight:500">Role</th>
            <th style="text-align:right;padding:6px 8px;font-weight:500">Daily</th>
            <th style="text-align:right;padding:6px 8px;font-weight:500">${esc(({monthly:'Monthly',weekly:'Weekly',yearly:'Yearly'})[cfg.billing_cycle]||'Cycle')}</th>
          </tr></thead>
          <tbody>
          ${['admin','poweruser','user'].map(role => `
            <tr><td style="padding:6px 8px;color:var(--text-100);text-transform:capitalize">${role}</td>
              <td style="padding:6px 8px;text-align:right">${limitInput(role,'daily_usd')}</td>
              <td style="padding:6px 8px;text-align:right">${limitInput(role,'cycle_usd')}</td>
            </tr>`).join('')}
          </tbody>
        </table>
        <div style="font-size:11px;color:var(--text-400)">Set 0 to mean "no limit" for that axis. Local-model usage never counts.</div>

        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn-primary" onclick="saveQuotaConfig()">Save settings</button>
          <button class="btn-secondary" onclick="switchGeneralTab('quotas', document.querySelector('.modal-tab.active'))">Reload</button>
        </div>

        ${SEC('Users')}
        ${usersHtml}
      </div>`);
      // Wire dynamic label for start-day
      const cyc = document.getElementById('q-billing-cycle');
      if (cyc) {
        cyc.addEventListener('change', () => {
          const lbl = document.getElementById('q-start-day-label');
          if (lbl) lbl.textContent = startDayLabel(cyc.value);
        });
      }
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--text-400)">Quotas not available: ${esc(String(e))}</div>`);
    }
}

async function _genTab_mempalace(C) {
  /* ─── MEMPALACE ─── */
    try {
      const mp = await API.get('/v1/mempalace/stats');
      if (!mp.enabled) {
        C.innerHTML = P(`<div style="${G('12px')}"><div style="color:var(--text-400)">MemPalace is disabled in config.json</div></div>`);
        return;
      }
      if (mp.error) {
        C.innerHTML = P(`<div style="color:var(--error)">${esc(mp.error)}</div>`);
        return;
      }

      // Classifier config
      const clf = await API.get('/v1/mempalace/classifier').catch(() => ({}));
      const modelOpts = (state.models || []).filter(m => {
        const mid = (typeof m === 'string') ? m : (m.id || m.name);
        return modelHasCapability(mid, 'chat');
      }).map(m => {
        const mid = m.id || m.name || m;
        const sel = mid === (clf.model || '') ? ' selected' : '';
        return modelOption(mid, {selected: mid === (clf.model || '')});
      }).join('');
      const allCats = ['fact','preference','decision','reference','generic','refusal','chitchat'];
      const fileCats = new Set(clf.categories_to_file || ['fact','preference','decision','reference']);
      const catChecks = allCats.map(c => `<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;margin-right:10px"><input type="checkbox" class="mp-clf-cat" value="${c}" ${fileCats.has(c)?'checked':''}>${c}</label>`).join('');

      const STAT = (val, label, color='var(--accent-brand)') => `<div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center;min-width:110px">
        <div style="font-size:22px;font-weight:600;color:${color}">${val}</div>
        <div style="font-size:11px;color:var(--text-400)">${label}</div>
      </div>`;

      // Overview stats
      const hallEntries = Object.entries(mp.halls || {});
      const statsRow = `<div style="display:flex;gap:12px;flex-wrap:wrap">
        ${STAT(mp.total_drawers.toLocaleString(), 'Drawers')}
        ${STAT(mp.total_closets.toLocaleString(), 'Closets')}
        ${STAT(mp.wing_count, 'Wings')}
        ${STAT(mp.room_count, 'Rooms')}
        ${STAT(hallEntries.length, 'Halls')}
        ${STAT((mp.graph?.tunnel_rooms||0), 'Tunnels')}
        ${STAT(mp.palace_size_mb + ' MB', 'DB Size', 'var(--text-200)')}
      </div>`;

      // Hall breakdown
      const hallColors = {'memory':'#7cb5e8','technical':'#e8927c','emotions':'#d4a0e8','consciousness':'#7ce8d8','general':'#c8c8c8'};
      const hallsHtml = hallEntries.length ? hallEntries.sort((a,b) => b[1].count - a[1].count).map(([name, info]) => {
        const color = hallColors[name] || '#e8d87c';
        const roomChips = Object.entries(info.rooms || {}).sort((a,b)=>b[1]-a[1]).map(([r,c]) =>
          `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:var(--text-300)">${esc(r)} (${c})</span>`
        ).join(' ');
        return `<div style="${ROW}">
          <span style="width:10px;height:10px;border-radius:2px;background:${color};flex-shrink:0"></span>
          <span style="font-weight:500;min-width:100px">${esc(name)}</span>
          <span style="${MONO}">${info.count} drawers</span>
          <div style="display:flex;gap:4px;flex-wrap:wrap">${roomChips}</div>
        </div>`;
      }).join('') : '';



      // Wings breakdown
      const wings = mp.wings || {};
      const sortedWings = Object.entries(wings).sort((a,b) => b[1].drawer_count - a[1].drawer_count);
      const userWings = sortedWings.filter(([,v]) => v.user_scoped);
      const sharedWings = sortedWings.filter(([,v]) => !v.user_scoped);

      const wingRow = (name, info) => {
        const scope = info.user_scoped
          ? `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg-300);color:var(--text-300)">${esc(info.user_name || info.user_id)}</span>`
          : `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:color-mix(in srgb, var(--accent-brand) 15%, transparent);color:var(--accent-brand)">shared</span>`;
        const topRooms = Object.entries(info.rooms || {}).sort((a,b)=>b[1]-a[1]).slice(0,5);
        const roomChips = topRooms.map(([r,c]) => `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:var(--text-300)">${esc(r)} (${c})</span>`).join(' ');
        return `<div style="${ROW};flex-wrap:wrap">
          <span style="font-weight:500;flex:1;min-width:140px">${esc(name)}</span>
          ${scope}
          <span style="${MONO}">${info.drawer_count} drawers</span>
          <span style="${MONO}">${info.room_count} rooms</span>
          <div style="width:100%;display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">${roomChips}</div>
        </div>`;
      };

      let wingsHtml = '';
      if (sharedWings.length) {
        wingsHtml += sharedWings.map(([n,i]) => wingRow(n,i)).join('');
      }
      if (userWings.length) {
        wingsHtml += `<div style="font-size:11px;color:var(--text-400);margin:8px 0 4px">User-Scoped Wings (${userWings.length})</div>`;
        wingsHtml += userWings.map(([n,i]) => wingRow(n,i)).join('');
      }

      // Daemons config + chat sync status merged
      const sync = mp.chat_sync || {};
      const syncTime = sync.last_sync ? new Date(sync.last_sync * 1000).toLocaleString() : 'never';
      const cfg = mp.config || {};
      const daemonRows = `
        <div style="${ROW}">
          ${DOT(cfg.mine_enabled)} <span style="flex:1">Miner</span>
          <span style="${MONO}">every ${Math.round(cfg.mine_interval_s/60)}m</span>
          <span style="${MONO}">${cfg.mine_sources} source(s)</span>
        </div>
        <div style="${ROW}">
          ${DOT(cfg.chat_sync_enabled)} <span style="flex:1">Chat Sync</span>
          <span style="${MONO}">every ${cfg.chat_sync_interval_s}s</span>
          ${cfg.chat_sync_build_closets ? BADGE('closets','var(--success)') : BADGE('no closets')}
          <span style="${MONO}">${sync.synced_sessions} sessions</span>
          <span style="${MONO}">last: ${esc(syncTime)}</span>
        </div>
      `;

      // Tunnels
      const tunnelList = (mp.tunnels || {}).tunnels || [];
      let tunnelsHtml = '';
      if (tunnelList.length) {
        tunnelsHtml = tunnelList.map(t => `<div style="${ROW}">
          <span style="${MONO}">${esc(t.source_wing||'')}/${esc(t.source_room||'')}</span>
          <span style="color:var(--text-400)">\u2194</span>
          <span style="${MONO}">${esc(t.target_wing||'')}/${esc(t.target_room||'')}</span>
          ${t.label ? `<span style="font-size:11px;color:var(--text-300)">${esc(t.label)}</span>` : ''}
        </div>`).join('');
      } else {
        tunnelsHtml = `<div style="color:var(--text-400);font-size:12px">No explicit tunnels configured</div>`;
      }

      // Recent WAL activity
      const wal = mp.wal || {};
      let walHtml = '';
      if (wal.total_ops) {
        const opTypes = Object.entries(wal.ops_by_type || {}).sort((a,b)=>b[1]-a[1]);
        const opBadges = opTypes.map(([op,n]) => `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg-300);color:var(--text-300)">${esc(op)}: ${n}</span>`).join(' ');
        const recentOps = (wal.recent_ops || []).slice(-10).reverse();
        const recentRows = recentOps.map(o => {
          const ts = o.timestamp ? new Date(o.timestamp).toLocaleString() : '';
          return `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border-100)">
            <span style="${MONO};min-width:140px">${esc(ts)}</span>
            <span style="font-size:11px;font-weight:500;min-width:100px">${esc(o.operation)}</span>
            <span style="${MONO}">${esc(o.wing)}${o.room ? '/' + esc(o.room) : ''}</span>
          </div>`;
        }).join('');
        walHtml = `<div style="${G('8px')}">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span style="font-size:12px;color:var(--text-300)">${wal.total_ops.toLocaleString()} total operations</span>
            ${opBadges}
          </div>
          <div style="max-height:200px;overflow-y:auto">${recentRows}</div>
        </div>`;
      } else {
        walHtml = `<div style="color:var(--text-400);font-size:12px">No write-ahead log entries</div>`;
      }

      // Anomaly detection
      let anomalies = [];
      if (mp.total_drawers > 0 && mp.total_closets === 0) anomalies.push('No closets built — search ranking may be degraded');
      if (mp.total_drawers > 10000) anomalies.push(`Large palace (${mp.total_drawers.toLocaleString()} drawers) — search may slow down`);
      const emptyWings = sortedWings.filter(([,v]) => v.drawer_count < 3);
      if (emptyWings.length) anomalies.push(`${emptyWings.length} wing(s) with <3 drawers: ${emptyWings.map(([n])=>n).join(', ')}`);
      if (!cfg.chat_sync_enabled) anomalies.push('Chat sync is disabled — new conversations are not being memorized');
      if (!cfg.mine_enabled) anomalies.push('Miner is disabled — file changes are not being indexed');
      if (sync.last_sync && (Date.now()/1000 - sync.last_sync) > 600) anomalies.push('Last chat sync was over 10 minutes ago');
      const orphanRatio = mp.total_closets > 0 ? mp.total_drawers / mp.total_closets : 0;
      if (orphanRatio > 20 && mp.total_drawers > 100) anomalies.push(`High drawer/closet ratio (${Math.round(orphanRatio)}:1) — many drawers may lack closet coverage`);

      const anomalyHtml = anomalies.length
        ? anomalies.map(a => `<div style="${ROW};border-color:color-mix(in srgb, var(--warning,#f59e0b) 40%, transparent)">
            <span style="color:var(--warning,#f59e0b)">\u26A0</span>
            <span style="font-size:12px">${esc(a)}</span>
          </div>`).join('')
        : `<div style="${ROW};border-color:color-mix(in srgb, var(--success) 30%, transparent)">${DOT(true)} <span style="font-size:12px;color:var(--text-300)">No anomalies detected</span></div>`;

      C.innerHTML = P(`<div style="${G('16px')}">
        ${SEC('Overview')}
        ${statsRow}

        ${SEC('Palace Explorer')}
        <div id="mp-tree-tabs" style="display:flex;gap:0;margin-bottom:8px">
          <button class="modal-tab active" onclick="mpTreeSwitch('wings',this)" style="padding:6px 14px;font-size:12px">Wings</button>
          <button class="modal-tab" onclick="mpTreeSwitch('tunnels',this)" style="padding:6px 14px;font-size:12px">Tunnels</button>
        </div>
        <div id="mp-tree" style="max-height:400px;overflow-y:auto;border:1px solid var(--border-100);border-radius:8px;padding:4px 0"></div>

        ${anomalies.length ? SEC('Anomalies') + anomalyHtml : ''}

        ${SEC('Daemons')}
        ${daemonRows}

        ${SEC('Chat Sync Classifier')}
        <div style="${G('10px')}">
          <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">LLM gate that classifies messages before filing to MemPalace. Skips refusals, chitchat, and generic content.</div>
          <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
            <label style="display:flex;align-items:center;gap:6px;font-size:12px"><input type="checkbox" id="mp-clf-enabled" ${clf.enabled?'checked':''}>Enabled</label>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Model:</label>
              <select id="mp-clf-model" class="form-input" style="font-size:12px;padding:4px 8px;max-width:260px">
                <option value="">— select model —</option>
                ${modelOpts}
              </select>
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Min turns:</label>
              <input type="number" id="mp-clf-min-turns" class="form-input" style="font-size:12px;padding:4px 8px;width:60px" value="${clf.min_turns||0}" min="0" max="100" title="Skip chats shorter than this (0 = no minimum)">
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Default for new chats:</label>
              <select id="mp-clf-default-mode" class="form-input" style="font-size:12px;padding:4px 8px">
                <option value="0" ${(clf.default_mode||0)===0?'selected':''}>Off</option>
                <option value="2" ${(clf.default_mode||0)===2?'selected':''}>Auto</option>
                <option value="1" ${(clf.default_mode||0)===1?'selected':''}>On</option>
              </select>
            </div>
          </div>
          <div style="margin-top:8px;font-size:11px;color:var(--text-400)">
            Auto mode: ${clf.enabled && clf.model ? 'LLM classifier' : ''}${clf.enabled && clf.model && clf.min_turns ? ' + ' : ''}${clf.min_turns ? 'min ' + clf.min_turns + ' turns' : ''}${!clf.enabled && !clf.min_turns ? 'no filters configured' : ''}
          </div>
          <div style="margin-top:8px">
            <label style="font-size:11px;color:var(--text-400)">File categories:</label>
            <div style="margin-top:4px">${catChecks}</div>
          </div>
          <button class="btn-primary" style="margin-top:10px;font-size:12px;padding:6px 16px" onclick="saveMpClassifier()">Save</button>
        </div>

        ${SEC('Write-Ahead Log')}
        ${walHtml}

        <div style="font-size:10px;color:var(--text-400);margin-top:8px">Palace: ${esc(mp.palace_path)}</div>
      </div>`);

      // --- Palace tree view ---
      const _mpUNames = {};
      for (const [wn, wi] of Object.entries(mp.wings || {})) {
        if (wi.user_name) _mpUNames[wi.user_id] = wi.user_name;
      }
      function _mpFriendly(name) {
        if (name.includes('--')) { const [u,a] = name.split('--',2); return (_mpUNames[u]||u.slice(0,6)) + ' / ' + a; }
        if (name.includes('/')) { const [u,a] = name.split('/',2); return (_mpUNames[u]||u.slice(0,6)) + ' / ' + a; }
        return name;
      }
      const _mpWings = mp.wings || {};
      const _mpHalls = mp.halls || {};
      const _mpTunnels = ((mp.tunnels || {}).tunnels || []);

      function _mpIcon(type) {
        const icons = {wing:'\uD83D\uDCE6',room:'\uD83D\uDCBB',drawer:'\uD83D\uDCC4',closet:'\uD83D\uDDC4',hall:'\uD83D\uDEA7',tunnel:'\uD83D\uDD17'};
        return icons[type]||'\u25CF';
      }
      function _mpBadge(t,c) { return '<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:'+c+'">'+esc(t)+'</span>'; }
      function _mpCount(n,label) { return '<span style="font-size:10px;font-family:var(--font-mono);color:var(--text-400)">'+n+' '+label+'</span>'; }

      function _mpTreeNode(icon, label, count, badge, depth, expandFn) {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 8px;padding-left:'+(12+depth*20)+'px;cursor:pointer;border-radius:4px;font-size:12px';
        row.onmouseenter = () => row.style.background = 'var(--bg-200)';
        row.onmouseleave = () => row.style.background = '';
        const arrow = document.createElement('span');
        arrow.style.cssText = 'font-size:9px;color:var(--text-400);width:12px;text-align:center;flex-shrink:0;transition:transform 0.15s;pointer-events:none';
        arrow.textContent = expandFn ? '\u25B6' : '';
        row.appendChild(arrow);
        const ic = document.createElement('span');
        ic.style.cssText = 'font-size:12px;flex-shrink:0;pointer-events:none';
        ic.textContent = icon;
        row.appendChild(ic);
        const lbl = document.createElement('span');
        lbl.style.cssText = 'font-weight:500;color:var(--text-100);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;pointer-events:none';
        lbl.textContent = label;
        row.appendChild(lbl);
        if (count) { const c = document.createElement('span'); c.style.cssText='font-size:10px;font-family:var(--font-mono);color:var(--text-400);pointer-events:none'; c.textContent=count; row.appendChild(c); }
        if (badge) { const b = document.createElement('span'); b.style.pointerEvents='none'; b.innerHTML = badge; row.appendChild(b); }
        const children = document.createElement('div');
        children.style.display = 'none';
        let expanded = false;
        if (expandFn) {
          row.onclick = async () => {
            expanded = !expanded;
            arrow.style.transform = expanded ? 'rotate(90deg)' : '';
            if (expanded && !children.dataset.loaded) { children.dataset.loaded='1'; await expandFn(children, depth+1); }
            children.style.display = expanded ? '' : 'none';
          };
        }
        const wrap = document.createElement('div');
        wrap.appendChild(row);
        wrap.appendChild(children);
        return wrap;
      }

      function _mpDrawerNode(d, depth) {
        const ts = d.filed_at ? new Date(d.filed_at).toLocaleString() : '';
        const hallBadge = d.hall ? '<span style="font-size:9px;padding:1px 4px;border-radius:3px;background:'+(hallColors[d.hall]||'var(--bg-300)')+';color:rgba(0,0,0,0.6)">'+esc(d.hall)+'</span>' : '';
        const summary = d.id.slice(7,22);
        return _mpTreeNode('\uD83D\uDCC4', summary, ts, hallBadge, depth, (ch) => {
          const detail = document.createElement('div');
          detail.style.cssText = 'padding:6px 8px;padding-left:'+(12+(depth+1)*20)+'px;font-size:11px;color:var(--text-200);white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto;background:var(--bg-100);border-radius:4px;margin:2px 8px';
          detail.innerHTML = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px">' +
            '<span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">' + esc(d.id) + '</span>' +
            '<span style="font-size:10px;color:var(--text-400)">' + esc(d.added_by) + '</span>' +
            (d.source_file ? '<span style="font-size:10px;color:var(--text-400)">' + esc(d.source_file) + '</span>' : '') +
          '</div>' + esc(d.text);
          ch.appendChild(detail);
        });
      }

      function _mpClosetNode(c, depth) {
        const summary = c.id.slice(0, 25);
        return _mpTreeNode('\uD83D\uDDC4\uFE0F', summary, (c.drawer_count||0)+' refs', '', depth, (ch) => {
          const detail = document.createElement('div');
          detail.style.cssText = 'padding:6px 8px;padding-left:'+(12+(depth+1)*20)+'px;font-size:10px;font-family:var(--font-mono);color:var(--text-300);white-space:pre-wrap;word-break:break-word;max-height:160px;overflow-y:auto;background:var(--bg-100);border-radius:4px;margin:2px 8px';
          detail.textContent = c.text;
          ch.appendChild(detail);
        });
      }

      async function _mpLoadDrawers(container, wing, room, depth) {
        try {
          const data = await API.get('/v1/mempalace/drawers?wing='+encodeURIComponent(wing)+'&room='+encodeURIComponent(room));
          const drawers = data.drawers || [];
          const closets = data.closets || [];
          if (!drawers.length && !closets.length) { container.innerHTML = '<div style="padding:4px 8px;padding-left:'+(12+depth*20)+'px;color:var(--text-400);font-size:11px">Empty</div>'; return; }
          if (closets.length) {
            for (const c of closets) container.appendChild(_mpClosetNode(c, depth));
          }
          for (const d of drawers) container.appendChild(_mpDrawerNode(d, depth));
        } catch(e) { container.innerHTML = '<div style="color:var(--error);padding:4px 12px;font-size:11px">Failed to load</div>'; }
      }

      function _mpRenderWingsTree(tree) {
        tree.innerHTML = '';
        const sorted = Object.entries(_mpWings).sort((a,b) => b[1].drawer_count - a[1].drawer_count);
        if (!sorted.length) { tree.innerHTML = '<div style="padding:12px;color:var(--text-400);font-size:12px">No wings</div>'; return; }

        // Section A: Rooms view
        const secA = document.createElement('div');
        secA.innerHTML = '<div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;padding:8px 12px">By Room</div>';
        for (const [wname, winfo] of sorted) {
          const scopeBadge = winfo.user_scoped ? _mpBadge(winfo.user_name||winfo.user_id,'var(--text-300)') : _mpBadge('shared','var(--accent-brand)');
          const wNode = _mpTreeNode(_mpIcon('wing'), _mpFriendly(wname), winfo.drawer_count+' drawers', scopeBadge, 0, (ch, d) => {
            const rooms = Object.entries(winfo.rooms||{}).sort((a,b)=>b[1]-a[1]);
            for (const [rname, rcount] of rooms) {
              ch.appendChild(_mpTreeNode(_mpIcon('room'), rname, rcount+' drawers', '', d, (ch2, d2) => _mpLoadDrawers(ch2, wname, rname, d2)));
            }
          });
          secA.appendChild(wNode);
        }
        tree.appendChild(secA);

        // Section B: Halls view
        const hallEntries = Object.entries(_mpHalls).sort((a,b) => b[1].count - a[1].count);
        if (hallEntries.length) {
          const secB = document.createElement('div');
          secB.style.borderTop = '1px solid var(--border-100)';
          secB.style.marginTop = '8px';
          secB.innerHTML = '<div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;padding:8px 12px">By Hall</div>';
          for (const [hname, hinfo] of hallEntries) {
            const color = hallColors[hname] || '#e8d87c';
            const dot = '<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:'+color+';margin-right:2px"></span>';
            const hNode = _mpTreeNode(_mpIcon('hall'), hname, hinfo.count+' drawers', dot, 0, (ch, d) => {
              const hRooms = Object.entries(hinfo.rooms||{}).sort((a,b)=>b[1]-a[1]);
              for (const [rname, rcount] of hRooms) {
                ch.appendChild(_mpTreeNode(_mpIcon('room'), rname, rcount+' drawers', '', d, async (ch2, d2) => {
                  // Load drawers for this room, filtered to this hall
                  for (const [wname] of sorted) {
                    if (!(wname in _mpWings) || !(_mpWings[wname].rooms||{})[rname]) continue;
                    try {
                      const data = await API.get('/v1/mempalace/drawers?wing='+encodeURIComponent(wname)+'&room='+encodeURIComponent(rname));
                      const filtered = (data.drawers||[]).filter(dr => dr.hall === hname);
                      for (const d of filtered) ch2.appendChild(_mpDrawerNode(d, d2));
                    } catch(e) {}
                  }
                }));
              }
            });
            secB.appendChild(hNode);
          }
          tree.appendChild(secB);
        }
      }

      function _mpRenderTunnelsTree(tree) {
        tree.innerHTML = '';
        if (!_mpTunnels.length) {
          tree.innerHTML = '<div style="padding:12px;color:var(--text-400);font-size:12px">No tunnels configured</div>';
          return;
        }
        for (const t of _mpTunnels) {
          const label = (t.source_wing||'')+'/'+( t.source_room||'') + ' \u2194 ' + (t.target_wing||'')+'/'+(t.target_room||'');
          const tNode = _mpTreeNode(_mpIcon('tunnel'), t.label || label, '', '', 0, (ch, d) => {
            // Source side
            ch.appendChild(_mpTreeNode(_mpIcon('room'), (t.source_room||'') + ' (' + _mpFriendly(t.source_wing||'') + ')', '', '', d,
              (ch2, d2) => _mpLoadDrawers(ch2, t.source_wing, t.source_room, d2)));
            // Target side
            ch.appendChild(_mpTreeNode(_mpIcon('room'), (t.target_room||'') + ' (' + _mpFriendly(t.target_wing||'') + ')', '', '', d,
              (ch2, d2) => _mpLoadDrawers(ch2, t.target_wing, t.target_room, d2)));
          });
          tree.appendChild(tNode);
        }
      }

      window.mpTreeSwitch = function(tab, btn) {
        if (btn) { btn.closest('#mp-tree-tabs').querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active')); btn.classList.add('active'); }
        const tree = document.getElementById('mp-tree');
        if (!tree) return;
        if (tab === 'wings') _mpRenderWingsTree(tree);
        else if (tab === 'tunnels') _mpRenderTunnelsTree(tree);
      };
      setTimeout(() => { const t = document.getElementById('mp-tree'); if (t) _mpRenderWingsTree(t); }, 50);

      // (treemap code removed — replaced by tree view above)

    } catch(e) { C.innerHTML = P(`<div style="color:var(--error)">Failed to load MemPalace stats: ${esc(e.message||e)}</div>`); }
}

async function _genTab_knowledge_graph(C) {
  /* ─── KNOWLEDGE GRAPH ─── */
    try {
      const [stats, kgConfig] = await Promise.all([
        API.get('/v1/mempalace/kg/stats').catch(e => ({error: e.message || String(e)})),
        API.get('/v1/mempalace/kg/config').catch(() => ({})),
      ]);
      if (stats.error) {
        C.innerHTML = P(`<div style="color:var(--error)">${esc(stats.error)}</div>`);
        return;
      }
      const isAdmin = state.authUser && state.authUser.role === 'admin';

      // Model picker — same shape as classifier picker.
      const enabledMc = state.modelsConfig?.models || {};
      const enabledModelList = Object.entries(enabledMc).filter(([,c])=>c.enabled !== false)
        .sort((a,b)=>(b[1].priority||0)-(a[1].priority||0));
      const currentModel = kgConfig.extraction_model || '';
      const modelOptionsKg = '<option value="">Auto (background-pick: cheapest local first)</option>'
        + enabledModelList.map(([mid,cfg])=>{
          const sel = mid === currentModel ? ' selected' : '';
          const localTag = cfg.is_local ? ' [local]' : '';
          return modelOption(mid, {selected: mid === currentModel, suffix: localTag});
        }).join('');

      const profileOpts = ['normative','generic'].map(p =>
        `<option value="${p}" ${p === (kgConfig.profile||'normative')?'selected':''}>${p}</option>`
      ).join('');

      const STAT = (val, label, color='var(--accent-brand)') => `<div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center;min-width:120px">
        <div style="font-size:22px;font-weight:600;color:${color}">${val}</div>
        <div style="font-size:11px;color:var(--text-400)">${label}</div>
      </div>`;

      const totalEntities = (stats.entities || 0).toLocaleString();
      const totalTriples = (stats.triples || 0).toLocaleString();
      const totalProjects = (stats.projects || []).length;

      // Per-project rows
      const projectRows = (stats.projects || []).map(p => {
        const topPred = (p.top_predicates || []).slice(0,5).map(pp =>
          `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--bg-300);color:var(--text-300)">${esc(pp.predicate)} (${pp.count})</span>`
        ).join(' ');
        return `<div style="${ROW};cursor:pointer" onclick="kgOpenProject('${esc(p.agent_id)}','${esc(p.project)}')">
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;color:var(--text-100);font-weight:500">${esc(p.project)}</div>
            <div style="${MONO}">${esc(p.agent_id)} &middot; wing=${esc(p.wing)}</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;flex:2">${topPred}</div>
          <div style="text-align:right;min-width:160px">
            <div style="font-size:13px;color:var(--text-100)"><b>${(p.triples||0).toLocaleString()}</b> triples</div>
            <div style="${MONO}">${(p.entities||0).toLocaleString()} entities</div>
          </div>
        </div>`;
      }).join('') || `<div style="padding:14px;color:var(--text-400);font-size:12px">No KG content yet — drop documents into a project's input folder to start extraction.</div>`;

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          <span style="width:7px;height:7px;border-radius:50%;background:${kgConfig.enabled === false ? 'var(--error)' : 'var(--success)'};flex-shrink:0"></span>
          <span style="font-size:14px;font-weight:500;color:var(--text-100)">Knowledge Graph</span>
          <span style="${MONO}">${kgConfig.enabled === false ? 'disabled' : 'active'}</span>
          <span style="margin-left:auto;${MONO}">scope: ${esc((kgConfig.scopes||['projects']).join(','))}</span>
        </div>

        ${SEC('Overview')}
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          ${STAT(totalEntities, 'Entities')}
          ${STAT(totalTriples, 'Triples')}
          ${STAT(totalProjects, 'Projects with KG')}
          ${STAT(esc(kgConfig.profile || 'normative'), 'Profile', 'var(--text-200)')}
        </div>

        ${SEC('Extraction Settings')}
        <div style="${G('10px')};padding:12px;border:1px solid var(--border-100);border-radius:8px">
          <div style="display:grid;grid-template-columns:140px 1fr auto;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Enabled</label>
            <label style="display:inline-flex;gap:6px;font-size:12px"><input type="checkbox" id="kg-enabled" ${kgConfig.enabled===false?'':'checked'}> Run KG extraction during project sync</label>
            <span></span>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Extraction model</label>
            <select class="form-select" id="kg-model" ${isAdmin?'':'disabled'}>${modelOptionsKg}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px">Cloud models extract higher-quality triples; local models keep your documents on-prem. The selected model runs once per drawer during sync — pick frugally. <b>Tested default:</b> gemma-4-e4b-it-4bit (local, German-capable, runs alongside the chat warmpool).</div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Profile</label>
            <select class="form-select" id="kg-profile" ${isAdmin?'':'disabled'}>${profileOpts}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px"><b>normative</b>: policies, regulations, laws, specifications, contracts, SOPs &mdash; controlled predicates (requires/forbids/cites/...). <b>generic</b>: open predicates, any document type.</div>
          <div style="display:grid;grid-template-columns:140px 1fr 140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Max triples / drawer</label>
            <input type="number" class="form-input" id="kg-max-triples" min="1" max="50" value="${kgConfig.max_triples_per_drawer||12}" ${isAdmin?'':'disabled'}>
            <label style="font-size:12px;color:var(--text-300)">Min confidence</label>
            <input type="number" class="form-input" id="kg-min-conf" min="0" max="1" step="0.05" value="${kgConfig.min_confidence??0.5}" ${isAdmin?'':'disabled'}>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Max chars / drawer</label>
            <input type="number" class="form-input" id="kg-max-chars" min="500" max="20000" step="500" value="${kgConfig.max_drawer_chars||6000}" ${isAdmin?'':'disabled'}>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr 140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Regenerate closets</label>
            <label style="display:inline-flex;gap:6px;font-size:12px"><input type="checkbox" id="kg-regen-closets" ${kgConfig.regenerate_closets?'checked':''} ${isAdmin?'':'disabled'}> Re-rank drawer retrieval via LLM after each sync</label>
            <span></span><span></span>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px">Adds ~1 LLM call per source file per cycle. Boosts <code>mempalace_query</code> ranking by replacing MemPalace's regex closet generation with an LLM pass that captures implicit topics, foreign-language content, and contextual references. Reuses the extraction model selected above.</div>
          <div style="display:flex;gap:8px;justify-content:flex-end">
            <button class="btn-primary" id="kg-save-btn" onclick="saveKgConfig()" ${isAdmin?'':'disabled'}>${isAdmin?'Save Settings':'Admin only'}</button>
          </div>
        </div>

        ${SEC('Per-Project Knowledge Graphs')}
        <div style="${G('6px')}">${projectRows}</div>

        ${SEC('Documentation')}
        <div style="font-size:12px;color:var(--text-300);padding:10px 12px;background:var(--bg-100);border-radius:8px;line-height:1.5">
          The KG is built automatically by the project-sync daemon. Every drawer mined from a project's input folders is sent to the configured LLM for triple extraction. Triples are written to <code>${esc((window.__brain_palace_path||'~/.mempalace/brain'))}/knowledge_graph.sqlite3</code> with <code>source_file</code> and <code>source_drawer_id</code> provenance — so every claim links back to its origin.
          <br><br>
          Agent tools: <code>mempalace_kg_query(entity)</code>, <code>mempalace_kg_search(predicate)</code>, <code>mempalace_kg_neighbors(entity, depth)</code> — all auto-scoped to the calling project.
        </div>
      </div>`);
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--error)">Failed to load Knowledge Graph view: ${esc(e.message||e)}</div>`);
    }
}

async function _genTab_gdpr(C) {
  /* ─── GDPR ─── */
    try {
      const svc = await API.getServices();
      const srv = svc.server || {};
      const gs = srv.gdpr_scanner || {};
      applyGdprConfigToScanner(gs);
      const mcAll = state.modelsConfig?.models || {};
      const localOpts = Object.entries(mcAll)
        .filter(([id, cfg]) => cfg.enabled && (cfg.is_local === true))
        .sort((a, b) => (b[1].priority || 0) - (a[1].priority || 0))
        .map(([mid]) => modelOption(mid, {selected: mid===(gs.default_local_fallback_model||'')}))
        .join('');
      const hasLocals = localOpts.length > 0;

      // Build category list with rule memberships
      const catMembers = {};
      for (const [rid, cat] of Object.entries(PIIScanner.ruleCategories)) {
        (catMembers[cat] = catMembers[cat] || []).push(rid);
      }
      // Sort rules within each category alphabetically for stable layout
      for (const cat of Object.keys(catMembers)) catMembers[cat].sort();

      // Labels for rule_ids that have no client-side detector (server-only,
      // e.g. spaCy NER). Listed explicitly so the admin UI is readable
      // instead of just showing the raw rid.
      const SERVER_ONLY_RULE_LABELS = {
        name: 'Name (spaCy NER, German)',
        address: 'Adresse / Ort (spaCy NER, German)',
        organisation: 'Organisation (spaCy NER, German)',
      };
      const ruleLabel = (rid) => {
        const r = PIIScanner.rules.find(x => x.id === rid);
        if (r) return r.label;
        return SERVER_ONLY_RULE_LABELS[rid] || rid;
      };

      const ACT_DESC = {
        ignore: 'Do not flag this category.',
        warn:   'Show the confirmation modal before sending.',
        block:  'Refuse unless a local model is active (requires master block on).',
      };
      const ACT_COLORS = {
        ignore: 'var(--text-400)',
        warn:   '#b45309',
        block:  'var(--error)',
      };

      const actionSelect = (cat, current) => `
        <select class="form-select gdpr-cat-action" data-cat="${esc(cat)}" style="width:150px;font-size:12px">
          <option value="ignore" ${current==='ignore'?'selected':''}>Ignore</option>
          <option value="warn" ${current==='warn'?'selected':''}>Warn</option>
          <option value="block" ${current==='block'?'selected':''}>Block</option>
        </select>`;

      const policyCats = gs.categories || {};
      const policyOverrides = gs.rule_overrides || {};

      // Build per-category rule expander
      const catRows = Object.keys(PIIScanner.categoryLabels).map(cat => {
        const catCfg = policyCats[cat] || {};
        const catAction = catCfg.action || PIIScanner.defaultCategoryActions[cat] || 'warn';
        const rules = catMembers[cat] || [];
        const overrideCount = rules.filter(r => policyOverrides[r]).length;
        const ruleRows = rules.map(rid => {
          const ovr = policyOverrides[rid] || '';
          return `<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;border-bottom:1px solid var(--border-100)">
            <code style="font-size:10px;color:var(--text-400);min-width:180px">${esc(rid)}</code>
            <span style="flex:1;font-size:11px;color:var(--text-200)">${esc(ruleLabel(rid))}</span>
            <select class="form-select gdpr-rule-override" data-rule="${esc(rid)}" style="width:150px;font-size:11px">
              <option value="">Use category (${catAction})</option>
              <option value="ignore" ${ovr==='ignore'?'selected':''}>Ignore</option>
              <option value="warn" ${ovr==='warn'?'selected':''}>Warn</option>
              <option value="block" ${ovr==='block'?'selected':''}>Block</option>
            </select>
          </div>`;
        }).join('');
        return `<div style="border:1px solid var(--border-100);border-radius:8px;margin-bottom:6px;background:var(--bg-100)">
          <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer" onclick="const n=this.nextElementSibling;n.style.display=n.style.display==='none'?'block':'none';this.querySelector('.gdpr-cat-caret').textContent=n.style.display==='none'?'&#9656;':'&#9662;'">
            <span class="gdpr-cat-caret" style="color:var(--text-400);font-size:11px">&#9656;</span>
            <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(PIIScanner.categoryLabels[cat])}</span>
            <span style="font-size:10px;color:var(--text-400)">${rules.length} rule${rules.length===1?'':'s'}${overrideCount?` &middot; <b style="color:#b45309">${overrideCount} override${overrideCount===1?'':'s'}</b>`:''}</span>
            <span onclick="event.stopPropagation()">${actionSelect(cat, catAction)}</span>
          </div>
          <div style="display:none;border-top:1px solid var(--border-100);max-height:280px;overflow-y:auto">${ruleRows}</div>
        </div>`;
      }).join('');

      const allowlistText = (gs.email_allowlist || []).join('\n');

      C.innerHTML = P(`<div style="${G('12px')}">
        <div style="padding:12px 14px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          <div style="font-size:13px;color:var(--text-100);margin-bottom:6px"><b>How actions work</b></div>
          <div style="font-size:11px;color:var(--text-300);line-height:1.55">
            <b style="color:${ACT_COLORS.ignore}">Ignore</b>: rule is skipped entirely — no scan, no log.<br>
            <b style="color:${ACT_COLORS.warn}">Warn</b>: shows the amber confirmation modal before sending. User may dismiss and proceed.<br>
            <b style="color:${ACT_COLORS.block}">Block</b>: the send is refused unless the current model is local — the composer auto-routes to the fallback model. Requires the master <i>Block requests with PII</i> switch below; otherwise block actions are downgraded to warn.
          </div>
        </div>

        ${SEC('Master switches')}
        <div style="display:flex;flex-direction:column;gap:6px">
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-enabled" ${gs.enabled!==false?'checked':''}>
            <span><b>Enable scanner</b> — regex sweep of outgoing messages and text attachments</span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-serverlog" ${gs.server_log!==false?'checked':''}>
            <span><b>Server-side audit log</b> — record every detection in <code>audit.db</code></span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-block" ${gs.server_block?'checked':''}>
            <span><b>Block requests with PII</b> — honors category <i>block</i> actions. When off, block is downgraded to warn everywhere.</span>
          </label>
          <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
            <span style="font-size:12px;color:var(--text-300);min-width:200px">Default local fallback model</span>
            <select class="form-select" id="gdpr-fallback" style="flex:1" ${hasLocals?'':'disabled'}>
              <option value="">None (disabled)</option>
              ${localOpts}
            </select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-top:2px">Used for background LLM calls (next-prompt, chat summary, memory classifier, worker summariser, scheduled tasks) and for composer auto-routing when a blocking finding lands on a cloud model. ${hasLocals?'':'<span style="color:var(--warning,#b45309)">No local models are configured — add one under Models first.</span>'}</div>
        </div>

        ${SEC('NER models (Named Entity Recognition)')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:8px">
          spaCy detects names, addresses, and organisations alongside the regex rules. Findings sit in the <i>Contact info</i> category — set the category action to <i>warn</i> or <i>block</i> below to surface them. Loaded models stay resident (~50 MB each); unload to free memory.
        </div>
        <div id="gdpr-ner-pill" style="display:flex;flex-direction:column;gap:6px;min-height:32px">
          <div style="font-size:11px;color:var(--text-400);font-style:italic">Loading…</div>
        </div>

        ${SEC('Background / non-interactive LLM calls')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:8px">
          Policy for calls Brain makes without user interaction (next-prompt suggestions, chat summary, memory classifier, scheduled tasks, user-profile daemon, KG extraction). Interactive chat is unaffected — users still see the per-turn modal there.
        </div>
        <div style="display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:12px;color:var(--text-300);min-width:200px">When PII is detected</span>
            <select class="form-select" id="gdpr-bg-pii-action" style="flex:1">
              <option value="anonymise"${(gs.background_pii_action||'anonymise')==='anonymise'?' selected':''}>Auto-anonymise (pseudonymise, then de-anonymise reply)</option>
              <option value="swap_to_local"${gs.background_pii_action==='swap_to_local'?' selected':''}>Swap to local fallback model</option>
              <option value="abort"${gs.background_pii_action==='abort'?' selected':''}>Abort the call</option>
            </select>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:12px;color:var(--text-300);min-width:200px">If anonymisation fails</span>
            <select class="form-select" id="gdpr-bg-fail-action" style="flex:1">
              <option value="swap_to_local"${(gs.background_anonymise_fail_action||'swap_to_local')==='swap_to_local'?' selected':''}>Fall back to local model</option>
              <option value="abort"${gs.background_anonymise_fail_action==='abort'?' selected':''}>Abort the call</option>
            </select>
          </div>
          <div style="font-size:11px;color:var(--text-400)">Only the <i>Abort</i> options actually refuse a call. The two swap paths always proceed: if anonymisation succeeds, the call uses the configured cloud model on pseudonymised text; if no usable local fallback is configured, the call falls through to the original model with a warning in the audit log.</div>
        </div>

        ${SEC('Email allowlist')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:6px">
          One entry per line. <code>user@example.com</code> matches exactly; <code>@example.com</code> matches any address at that domain. Matching emails are suppressed from findings entirely.
        </div>
        <textarea id="gdpr-email-allowlist" rows="5" style="width:100%;font-family:var(--font-mono);font-size:12px;padding:8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000);color:var(--text-100);resize:vertical" placeholder="alexander@me.com&#10;@trusted-company.com">${esc(allowlistText)}</textarea>

        ${SEC('Category actions')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:6px">
          Pick one action per category. Expand to override individual rules. Category-level severity is the default; rule overrides win when set.
        </div>
        ${catRows}

        <div style="display:flex;gap:8px;margin-top:14px;padding-top:12px;border-top:1px solid var(--border-100)">
          <button class="btn-primary" id="gdpr-save-btn" onclick="saveGdprConfig()">Save all GDPR settings</button>
          <button class="btn-secondary" onclick="_confirmResetGdprCategories()">Reset categories to defaults</button>
        </div>
      </div>`);
      // Populate the NER pill (separate request — pill lives on its own
      // endpoint so it can be refreshed independently of saveGdprConfig).
      refreshGdprNerPill();
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--error)">Failed to load GDPR settings: ${esc(e.message||e)}</div>`);
    }
}

async function _genTab_classification(C) {
  /* ─── CLASSIFICATION ─── */
    try {
      const cfg = await API.get('/v1/classification/config');
      const kw = cfg.keywords || {};
      const defaults = (cfg.defaults && cfg.defaults.keywords) || {};
      const extras = cfg.extra_patterns || [];
      const policy = cfg.policy || {};
      const perLvl = policy.per_level_action || {};

      // Local fallback model dropdown (mirrors GDPR tab pattern)
      const mcAll = state.modelsConfig?.models || {};
      const localModelOpts = Object.entries(mcAll)
        .filter(([id, c]) => c.enabled && c.is_local === true)
        .sort((a, b) => (b[1].priority || 0) - (a[1].priority || 0))
        .map(([id]) => `<option value="${esc(id)}" ${id === policy.default_local_fallback_model ? 'selected' : ''}>${esc(id)}</option>`)
        .join('');

      const actionSelect = (level, current, strict) => {
        if (strict) {
          return `<select class="form-select" disabled style="width:160px;font-size:12px;opacity:.6" title="Strict always blocks per ARL §1.11">
            <option selected>block (locked)</option>
          </select>`;
        }
        return `<select class="form-select cls-policy-action" data-level="${esc(level)}" style="width:160px;font-size:12px">
          <option value="ignore"      ${current==='ignore'?'selected':''}>ignore</option>
          <option value="warn"        ${current==='warn'?'selected':''}>warn</option>
          <option value="force_local" ${current==='force_local'?'selected':''}>force local</option>
          <option value="block"       ${current==='block'?'selected':''}>block</option>
        </select>`;
      };

      const levelRow = (level, labelDe) => `
        <div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--border-100)">
          <span style="flex:1;font-size:12.5px">${esc(labelDe)}</span>
          ${actionSelect(level, perLvl[level] || '', level === 'strict')}
        </div>`;

      const kwBlock = (lvl, label) => `
        <div style="margin-bottom:14px">
          <label style="display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--text-200);font-weight:500;margin-bottom:4px">
            <span>${label}</span>
            <button class="btn-secondary" style="font-size:11px;padding:2px 8px" onclick="clsRestoreDefaultKw('${lvl}')">Restore defaults</button>
          </label>
          <textarea id="cls-kw-${lvl}" class="form-input" rows="4"
            style="font-family:inherit;font-size:12px;width:100%"
            placeholder="One keyword per line">${esc((kw[lvl]||[]).join('\n'))}</textarea>
        </div>`;

      const extraRow = (item, i) => `
        <div class="cls-extra-row" data-i="${i}" style="display:flex;gap:6px;align-items:center;margin-bottom:6px">
          <select class="form-select cls-extra-level" style="width:140px;font-size:12px">
            <option value="public"       ${item.level==='public'?'selected':''}>Öffentlich</option>
            <option value="internal"     ${item.level==='internal'?'selected':''}>Intern</option>
            <option value="confidential" ${item.level==='confidential'?'selected':''}>Vertraulich</option>
            <option value="strict"       ${item.level==='strict'?'selected':''}>Streng Vertraulich</option>
          </select>
          <input type="text" class="form-input cls-extra-pattern" style="flex:1;font-family:monospace;font-size:12px"
            placeholder="Regex pattern" value="${esc(item.pattern||'')}">
          <button class="btn-secondary" style="font-size:11px;padding:2px 8px" onclick="this.parentElement.remove()">Remove</button>
        </div>`;

      C.innerHTML = `
        <div style="max-width:760px">
          <h3 style="margin:0 0 4px;font-size:16px">Document Classification</h3>
          <div style="font-size:12px;color:var(--text-400);margin-bottom:18px">
            Detector reuses the regex marker scan + PII signals from the GDPR scanner.
            Phase B enforces per-level routing decisions on attachment uploads and
            tool reads.
          </div>

          <h4 style="margin:18px 0 8px;font-size:13px">Policy</h4>
          <div style="background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;padding:14px 16px;margin-bottom:18px">
            <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin-bottom:8px">
              <input type="checkbox" id="cls-policy-enabled" ${policy.enabled !== false ? 'checked' : ''}>
              <span><b>Scanner enabled</b> — when off, nothing fires (detection AND enforcement disabled)</span>
            </label>
            <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin-bottom:8px">
              <input type="checkbox" id="cls-policy-server-block" ${policy.server_block !== false ? 'checked' : ''}>
              <span><b>Hard-block master switch</b> — when off, 'block' actions downgrade to 'force local'. Strict always blocks regardless.</span>
            </label>
            <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin-bottom:14px">
              <input type="checkbox" id="cls-policy-server-log" ${policy.server_log !== false ? 'checked' : ''}>
              <span><b>Server audit log</b> — emit <code>classification_detected/auto_fallback/blocked</code> events</span>
            </label>
            <div style="margin-bottom:14px">
              <label style="font-size:11.5px;color:var(--text-400);text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:4px">
                Default local fallback model
              </label>
              <select class="form-select" id="cls-policy-fallback" style="width:100%;font-size:12.5px">
                <option value="">— inherit from GDPR scanner —</option>
                ${localModelOpts || '<option disabled>(no local models enabled)</option>'}
              </select>
              <div style="font-size:11px;color:var(--text-400);margin-top:4px">
                Used when an effective action of <code>force_local</code> needs to swap models.
              </div>
            </div>
            <div style="font-size:11.5px;color:var(--text-400);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Per-level action</div>
            ${levelRow('public', 'Öffentlich (public)')}
            ${levelRow('internal', 'Intern (internal)')}
            ${levelRow('confidential', 'Vertraulich (confidential)')}
            ${levelRow('strict', 'Streng Vertraulich (strict — locked, ARL §1.11)')}
            ${levelRow('unmarked', 'Unmarked (no marker detected)')}
          </div>

          <h4 style="margin:18px 0 8px;font-size:13px">Keywords by sensitivity</h4>
          ${kwBlock('internal',     'Internal — presence alone is fine, but absence of marker downgrades to Intern')}
          ${kwBlock('confidential', 'Confidential — flags mismatches if document is marked Öffentlich/Intern')}
          ${kwBlock('strict',       'Strict — strongest signal; mismatch becomes HIGH severity')}

          <h4 style="margin:24px 0 8px;font-size:13px">Extra marker patterns (regex)</h4>
          <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">
            Custom regex patterns to recognise organisation-specific markings on top of
            the built-in <code>Dokumentenklassifizierung … &lt;level&gt;</code> matcher.
          </div>
          <div id="cls-extras-box">
            ${extras.map(extraRow).join('') || '<div style="color:var(--text-400);font-size:12px">No extra patterns.</div>'}
          </div>
          <button class="btn-secondary" style="margin-top:6px;font-size:12px" onclick="clsAddExtraRow()">+ Add pattern</button>

          <div style="margin-top:24px;display:flex;gap:10px">
            <button class="btn-primary" onclick="clsSaveSettings()">Save</button>
            <span id="cls-settings-status" style="font-size:12px;color:var(--text-400);align-self:center"></span>
          </div>
        </div>
      `;

      // Stash defaults on the container for the restore button
      C.__clsDefaults = defaults;
      C.__clsExtras = extras;
    } catch (e) {
      C.innerHTML = `<div style="color:var(--error,#d33);padding:20px">${esc(e.message || String(e))}</div>`;
    }
    return;
}

async function _genTab_tools(C) {
  /* ─── TOOLS ─── */
    try {
      const [cfg, status, settingsResp, breakdown, rmdResp] = await Promise.all([
        API.get('/v1/tools/config'),
        API.get('/v1/tools/status'),
        API.get('/v1/tools/settings'),
        // Cost breakdown is global (resolver bypasses agent overrides via
        // agent='main' here, but tool token cost is the schema bytes, not
        // agent-dependent). Agent-specific surface vs cost lives in
        // agent Tokens tab.
        API.get('/v1/tools/breakdown?agent=main').catch(() => ({})),
        // Research-mode disciplines — admin-editable per-section text that
        // gets injected into the system prompt for project chats with
        // research_mode=on.
        API.get('/v1/research-mode/disciplines').catch(() => null),
      ]);
      window._rmdResp = rmdResp;
      const allTools = settingsResp.tools || [];
      // Stash on window so per-tool save handlers can read the latest fetched
      // record without refetching (gets clobbered on next tab switch).
      window._toolSettingsCache = Object.fromEntries(allTools.map(t => [t.name, t]));
      window._toolPurposesCanonical = settingsResp.purposes || null;
      window._toolConfigCache = cfg || {};
      window._toolStatusCache = status || {};

      // Build name → tokens map from breakdown response. Each group entry
      // has a `builtin_tools` list of {name, total_tokens, ...} records.
      const toolTokens = {};
      for (const grp of (breakdown.groups || [])) {
        for (const ti of (grp.builtin_tools || [])) {
          if (ti.name) toolTokens[ti.name] = ti.total_tokens || 0;
        }
      }

      // Group → tools (sorted by name within group). '(ungrouped)' bucket
      // surfaces tools missing from TOOL_GROUPS — server returns group=''
      // for those.
      const byGroup = {};
      for (const t of allTools) {
        const g = t.group || '(ungrouped)';
        (byGroup[g] = byGroup[g] || []).push(t);
      }
      // Group order: core/memory/web first (most-edited), then alpha.
      const PRIMARY_GROUPS = ['core', 'memory', 'context', 'web', 'documents'];
      const otherGroups = Object.keys(byGroup).filter(g => !PRIMARY_GROUPS.includes(g)).sort();
      const groupOrder = PRIMARY_GROUPS.filter(g => byGroup[g]).concat(otherGroups);

      // Tools that have integration-knob support (the existing /v1/tools/config keys)
      const INTEGRATION_TOOLS = new Set(Object.keys(cfg || {}));

      // Helper: per-tool integration-status badge (re-uses old sBadge logic
      // but only for tools that actually appear in /v1/tools/status).
      const sBadge = (name) => {
        const s = (status[name] || {}).status;
        if (!s) return '';
        const c = s==='configured'?'var(--success)':s==='disabled'?'var(--text-400)':'var(--error)';
        const i = s==='configured'?'✓':s==='disabled'?'–':'✗';
        return `<span style="font-size:10px;color:${c};font-weight:500">${i} ${esc(s)}</span>`;
      };

      // Per-tool row (collapsed). Click → toggles expanded panel below.
      const toolRow = (t) => {
        const integ = INTEGRATION_TOOLS.has(t.name) ? sBadge(t.name) : '';
        const proseFlag = (t.description || t.when_to_use || t.warnings || t.examples)
          ? `<span title="Custom prompt prose configured" style="font-size:10px;color:var(--accent)">★ prose</span>`
          : '';
        const appliesFlag = (t.applies_with && t.applies_with.length)
          ? `<span title="Renders only when ${esc(t.applies_with.join(', '))} are also active" style="font-size:10px;color:var(--text-400)">+${t.applies_with.length}</span>`
          : '';
        const tokens = toolTokens[t.name] || 0;
        const tokensFlag = tokens > 0
          ? `<span title="Tool definition contributes ~${tokens} tokens to every request" style="font-size:10px;color:var(--text-400);font-family:var(--font-mono)">${tokens}t</span>`
          : '';
        const enabledColor = t.enabled ? 'var(--success)' : 'var(--text-400)';
        const deferredColor = t.deferred ? 'var(--warning)' : 'var(--text-400)';
        return `
          <div class="tool-row" data-tool="${esc(t.name)}" style="border:1px solid var(--border-100);border-radius:6px;margin-bottom:6px;overflow:hidden">
            <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;background:var(--bg-100)" onclick="toggleToolPanel('${esc(t.name)}')">
              <span style="font-family:var(--font-mono);font-size:12px;color:${enabledColor};font-weight:500;flex:1">${esc(t.name)}${t.enabled ? '' : ' <span style="color:var(--text-400);font-weight:400">(disabled)</span>'}</span>
              ${proseFlag}
              ${appliesFlag}
              ${tokensFlag}
              ${integ}
              <span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(245,158,11,0.12);color:${deferredColor};display:${t.deferred?'inline':'none'}" id="defer-badge-${esc(t.name)}">deferred</span>
              <span style="font-size:14px;color:var(--text-400);transition:transform 0.1s" id="chevron-${esc(t.name)}">▸</span>
            </div>
            <div class="tool-panel" id="tool-panel-${esc(t.name)}" style="display:none;padding:12px 14px;background:var(--bg-50);border-top:1px solid var(--border-100)"></div>
          </div>`;
      };

      // Group section (collapsible header + tool rows).
      const groupSection = (gName, tools) => {
        // Default-expanded if any tool in the group has non-default state
        const hasNonDefault = tools.some(t =>
          !t.enabled || t.deferred || t.description || t.when_to_use ||
          t.warnings || t.examples || INTEGRATION_TOOLS.has(t.name));
        const expanded = hasNonDefault;
        return `
          <div style="margin-bottom:14px">
            <div style="display:flex;align-items:center;gap:8px;padding:6px 8px;cursor:pointer;border-radius:4px;background:var(--bg-100)" onclick="toggleToolGroup('${esc(gName)}')">
              <span style="font-size:14px;color:var(--text-400);transition:transform 0.1s" id="group-chevron-${esc(gName)}">${expanded?'▾':'▸'}</span>
              <span style="font-size:13px;font-weight:600;color:var(--text-100);text-transform:uppercase;letter-spacing:0.04em">${esc(gName)}</span>
              <span style="font-size:11px;color:var(--text-400)">${tools.length} tool${tools.length===1?'':'s'}</span>
            </div>
            <div id="group-body-${esc(gName)}" style="display:${expanded?'block':'none'};padding:8px 0 0 16px">
              ${tools.map(toolRow).join('')}
            </div>
          </div>`;
      };

      // Cost totals header — sums by group + grand total
      const tokensByGroup = {};
      let tokensTotal = 0;
      for (const t of allTools) {
        const tk = toolTokens[t.name] || 0;
        const g = t.group || '(ungrouped)';
        tokensByGroup[g] = (tokensByGroup[g] || 0) + tk;
        tokensTotal += tk;
      }
      const builtinTotal = breakdown.builtin_tokens || tokensTotal;
      const mcpTotal = breakdown.mcp_tokens || 0;
      const grandTotal = builtinTotal + mcpTotal;
      const sortedGroupCosts = Object.entries(tokensByGroup)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6);
      const costRow = (label, n, max) => {
        const pct = max > 0 ? Math.round((n / max) * 100) : 0;
        return `<div style="display:flex;align-items:center;gap:8px;font-size:11px;padding:2px 0">
          <span style="font-family:var(--font-mono);min-width:120px;color:var(--text-300)">${esc(label)}</span>
          <div style="flex:1;height:5px;background:var(--bg-200);border-radius:3px;overflow:hidden;max-width:240px">
            <div style="height:100%;width:${pct}%;background:var(--accent-brand)"></div>
          </div>
          <span style="font-family:var(--font-mono);color:var(--text-400);min-width:60px;text-align:right">${n} tok</span>
        </div>`;
      };

      // Research-mode disciplines section — three textareas + reset
      // buttons. Renders only when the GET succeeded.
      let rmdHTML = '';
      if (rmdResp && rmdResp.sections) {
        const sectionLabels = {
          refusal:   'Refusal discipline',
          precision: 'Precision discipline',
          citation:  'Citation discipline',
        };
        const sectionTextarea = (k) => {
          const cur = rmdResp.sections[k] || '';
          const isDefault = cur === (rmdResp.defaults[k] || '');
          return `
            <div style="margin-bottom:12px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <span style="font-size:12px;font-weight:600;color:var(--text-100)">${esc(sectionLabels[k] || k)}</span>
                <span style="font-size:10px;color:var(--text-400)">${isDefault ? '(default)' : '(custom)'}</span>
                <button class="btn-secondary" style="font-size:10px;padding:2px 8px;margin-left:auto"
                        onclick="resetResearchModeDiscipline('${esc(k)}')" title="Restore the factory default for this section">Reset</button>
              </div>
              <textarea id="rmd-${esc(k)}" rows="6" class="form-input"
                style="width:100%;font-family:var(--font-mono);font-size:11px;resize:vertical">${esc(cur)}</textarea>
            </div>`;
        };
        rmdHTML = `
          <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px;margin-bottom:14px;background:var(--bg-100)">
            <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px">
              <div style="font-size:12px;font-weight:600;color:var(--text-100)">Research-mode disciplines</div>
              <div style="font-size:11px;color:var(--text-400)">injected into the system prompt for project chats with research_mode=on</div>
            </div>
            <div style="font-size:10px;color:var(--text-400);margin-bottom:10px">
              Edit each section below; clear a section to drop it from the prompt entirely. Per-tool retrieval guidance (search-first, query discipline, the 3-step flow) lives in the tool descriptions further down — these three sections cover the output posture only.
            </div>
            ${(rmdResp.section_order || ['refusal','precision','citation']).map(sectionTextarea).join('')}
            <div style="display:flex;justify-content:flex-end;gap:8px">
              <button class="btn-primary" onclick="saveResearchModeDisciplines()" style="padding:6px 14px;font-size:12px">Save disciplines</button>
            </div>
          </div>`;
      }

      C.innerHTML = P(`<div>
        <div style="font-size:11px;color:var(--text-400);margin-bottom:12px">
          ${allTools.length} tools across ${groupOrder.length} groups. Click a tool to expand and edit its enabled / defer flags, purposes, integration knobs (where applicable), and prompt prose. The "<span style="font-family:var(--font-mono)">Nt</span>" badge in each row shows the tool's token cost in every request.
        </div>

        ${rmdHTML}

        <div style="border:1px solid var(--border-100);border-radius:8px;padding:12px;margin-bottom:14px;background:var(--bg-100)">
          <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px">
            <div style="font-size:12px;font-weight:600;color:var(--text-100)">Tool definition cost</div>
            <div style="font-size:11px;color:var(--text-400)">measured against agent <span style="font-family:var(--font-mono)">main</span></div>
          </div>
          <div style="display:flex;gap:18px;font-size:11px;margin-bottom:8px">
            <div><span style="color:var(--text-400)">Built-in tools:</span> <b style="font-family:var(--font-mono);color:var(--text-100)">${builtinTotal} tok</b></div>
            <div><span style="color:var(--text-400)">MCP tools:</span> <b style="font-family:var(--font-mono);color:var(--text-100)">${mcpTotal} tok</b></div>
            <div><span style="color:var(--text-400)">Total per request:</span> <b style="font-family:var(--font-mono);color:var(--text-100)">${grandTotal} tok</b></div>
          </div>
          <div style="font-size:10px;color:var(--text-400);margin-bottom:6px;text-transform:uppercase;letter-spacing:0.04em">Top groups by cost</div>
          ${sortedGroupCosts.map(([g, n]) => costRow(g, n, sortedGroupCosts[0]?.[1] || 1)).join('')}
        </div>

        ${groupOrder.map(g => groupSection(g, byGroup[g])).join('')}
      </div>`);
      return;
    } catch(e) {
      C.innerHTML = P('<div style="color:var(--error)">Failed to load tools settings: ' + esc(e.message || String(e)) + '</div>');
      return;
    }
}
