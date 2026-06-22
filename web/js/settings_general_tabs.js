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
      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          ${DOT(true)}<span style="font-size:14px;font-weight:500;color:var(--text-100)">Verbunden</span>
          <span style="${MONO};margin-left:auto">${esc(BASE_URL)}</span>
          ${srv.version?`<span style="${MONO}">v${esc(srv.version)}</span>`:''}
          ${srv.pid?`<span style="${MONO}">PID ${srv.pid}</span>`:''}
        </div>
        ${SEC('Dienste')}${svcRows}
        ${SEC('Modelle')}
        <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          <span style="font-size:13px;color:var(--text-200);flex:1">Standardmodell, Bildbeschreibung, Chat-Zusammenfassung, Auto-Routing-Klassifikator und alle weiteren Dienst-Modelle werden zentral unter <b>Service-Modelle</b> gepflegt.</span>
          <button class="btn-secondary" style="font-size:12px;padding:4px 10px" onclick="switchGeneralTab('service-models', document.querySelector('.modal-tab[onclick*=\\'service-models\\']'))">Konfigurieren &rarr;</button>
        </div>
        ${SEC('Auto-Routing', 'Legt fest, wie das „✨ Smart/Auto"-Modell im Verfasser (und background_task_model=auto beim Fan-out) die Absicht einer Anfrage erkennt und das passende Modell wählt.\n\n• Schlüsselwörter: regelbasiert, ohne Kosten, ohne LLM-Aufruf.\n• LLM: ein Klassifizierungsmodell erkennt die Absicht. Welches Modell, legt der Slot „Prompt-Klassifikation (Auto-Routing)" unter Service-Modelle fest (ist er leer, das günstigste/lokale Modell).\n• Hybrid: erst Schlüsselwörter, das LLM nur bei Unklarheit.\n\nLLM/Hybrid fallen bei Fehler oder Timeout still auf Schlüsselwörter zurück. Im LLM-Modus liefert der Classifier zusätzlich eine Komplexität (gering/mittel/hoch): hoch hebt die Modellstufe an (Reasoning-Modell), gering senkt sie (günstigeres Modell). Einfachere Aufgaben bleiben bevorzugt in der Cloud (günstigstes Cloud-Modell), lokal nur als letzte Option.')}
        <div style="display:flex;gap:8px;align-items:center">
          ${(() => {
            const arm = srv.auto_route_classifier_mode || 'keywords';
            const opt = (v, lbl) => `<option value="${v}" ${arm===v?'selected':''}>${lbl}</option>`;
            return `<select class="form-select" id="srv-auto-route-mode" style="flex:1">
              ${opt('keywords', 'Schlüsselwörter (Standard, ohne Kosten)')}
              ${opt('llm', 'LLM (Modell unter Service-Modelle konfigurierbar)')}
              ${opt('hybrid', 'Hybrid (erst Schlüsselwörter, LLM nur bei Bedarf)')}
            </select>`;
          })()}
          <button class="btn-secondary" onclick="API.post('/v1/services/server',{auto_route_classifier_mode:document.getElementById('srv-auto-route-mode').value}).then(()=>showToast('Auto-Routing aktualisiert')).catch(e=>showToast('Fehlgeschlagen',true))">Setzen</button>
        </div>
        ${SEC('Eingabefeld-Standards (global)', 'GLOBALE Vorgabe, mit der ein NEUER Chat startet: Denk-Stufe, Caveman-Modus und Gedächtnis-Modus. Jeder Nutzer kann das pro Konto übersteuern (Benutzereinstellungen → Memory → „Eingabefeld-Standards“, „Server-Standard verwenden“ = erbt diesen Wert hier). Gilt nur für frische Chats — beim Wiederöffnen wird der eigene gespeicherte Stand des Chats wiederhergestellt. (Der Gedächtnis-Standard ist derselbe Wert wie in MemPalace → Classifier.)')}
        ${(() => {
          const cd = state.composerDefaults || {};
          const tl = String(cd.thinking_level || 'none').toLowerCase();
          const cm = parseInt(cd.caveman_mode) || 0;
          const mm = parseInt(cd.memory_mode) || 0;
          const optTL = (v, lbl) => `<option value="${v}" ${tl===v?'selected':''}>${lbl}</option>`;
          const optN = (cur, v, lbl) => `<option value="${v}" ${cur===v?'selected':''}>${lbl}</option>`;
          return `<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
            <label style="font-size:12px;color:var(--text-200)">Denk-Stufe
              <select class="form-select" id="cd-thinking" style="width:100%;margin-top:4px">
                ${optTL('none','Aus')}${optTL('low','Niedrig')}${optTL('medium','Mittel')}${optTL('high','Hoch')}
              </select>
            </label>
            <label style="font-size:12px;color:var(--text-200)">Caveman-Modus
              <select class="form-select" id="cd-caveman" style="width:100%;margin-top:4px">
                ${optN(cm,0,'Aus')}${optN(cm,1,'Lite')}${optN(cm,2,'Voll')}${optN(cm,3,'Ultra')}
              </select>
            </label>
            <label style="font-size:12px;color:var(--text-200)">Gedächtnis-Modus
              <select class="form-select" id="cd-memory" style="width:100%;margin-top:4px">
                ${optN(mm,0,'Aus')}${optN(mm,2,'Auto')}${optN(mm,1,'Ein')}
              </select>
            </label>
          </div>
          <div style="margin-top:8px">
            <button class="btn-secondary" onclick="saveComposerDefaults()">Standards speichern</button>
          </div>`;
        })()}
        ${SEC('Sidecar')}
        ${_renderSupervisorStatus(sc, {
          restartFn: 'restartSidecar',
          restartLabel: 'Sidecar neu starten',
          note: 'Laufende Durchläufe schlagen mit einem Sidecar-Fehler fehl.',
          disabledHint: 'sidecar.auto_start=false',
        })}
        ${SEC('Websuche (SearXNG)')}
        ${_renderSupervisorStatus(sx, {
          restartFn: 'restartSearxng',
          restartLabel: 'SearXNG neu starten',
          note: 'Betreibt das searxng_search-Tool. Websuchen schlagen während des Neustarts kurzzeitig fehl.',
          disabledHint: 'searxng.auto_start=false',
        })}
        <div id="searxng-engines-panel">${_renderSearxngEngines(sxe)}</div>
        ${SEC('Web-Rendering (crawl4ai)')}
        ${_renderSupervisorStatus(c4, {
          restartFn: 'restartCrawl4ai',
          restartLabel: 'crawl4ai neu starten',
          note: 'Headless-Browser-Fallback für JS-gerenderte Seiten in web_fetch + Projekt-URL-Mining. Abrufe fallen während des Neustarts kurzzeitig auf einfaches HTTP zurück.',
          disabledHint: 'crawl4ai.auto_start=false',
        })}
        ${SEC('Kostenkontingente')}
        <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          <span style="font-size:13px;color:var(--text-200);flex:1">Limits pro Benutzer und Rolle mit Zurücksetzung im Abrechnungszyklus.</span>
          <button class="btn-secondary" style="font-size:12px;padding:4px 10px" onclick="switchGeneralTab('quotas', document.querySelector('.modal-tab[onclick*=\\'quotas\\']'))">Konfigurieren &rarr;</button>
        </div>
        ${SEC('DSGVO / PII-Scanner', 'Schnellüberblick über den PII-Scanner. Die vollständige Konfiguration — granulare Kategorie-Aktionen, E-Mail-Allowlist und das lokale Fallback-Modell — liegt im eigenen DSGVO-Tab. „Hard-Block an" bedeutet: erkannte personenbezogene Daten werden vor dem Senden an ein Nicht-lokales Modell hart blockiert (statt nur zu warnen oder lokal zu ersetzen).')}
        <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          ${DOT((srv.gdpr_scanner||{}).enabled !== false)}
          <span style="font-size:13px;color:var(--text-200);flex:1">
            ${(srv.gdpr_scanner||{}).enabled !== false ? 'Scanner aktiv' : 'Scanner deaktiviert'}
            ${(srv.gdpr_scanner||{}).server_block ? ' &middot; <b style="color:var(--warning,#b45309)">Hard-Block an</b>' : ''}
          </span>
          <button class="btn-secondary" style="font-size:12px;padding:4px 10px" onclick="switchGeneralTab('gdpr', document.querySelector('.modal-tab[onclick*=\\'gdpr\\']'))">Konfigurieren &rarr;</button>
        </div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn-secondary" onclick="API.restartServer().then(()=>showToast('Server wird neu gestartet…')).catch(e=>showToast('Fehlgeschlagen',true))">Server neu starten</button>
        </div>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--error)">Server nicht erreichbar</div>'); }
}

async function _genTab_models(C) {
  /* ─── MODELS ─── */
    const mc = state.modelsConfig?.models || {};
    // Chat-capable model ids, used to populate the per-model "fan-out model"
    // dropdown (a leaf-task offload target may be any chat model).
    const _chatModelIds = Object.entries(mc)
      .filter(([, c]) => (c.capabilities || ['chat']).includes('chat'))
      .map(([id]) => id).sort();
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
        inp = `<select class="${cls}" style="${s}">${choices.map(c => `<option value="${c}"${val===c?' selected':''}>${c||'(Standard)'}</option>`).join('')}</select>`;
      } else {
        inp = `<input class="${cls}" type="${type}" value="${val??''}" style="${s}" ${step?`step="${step}"`:''}${min!=null?` min="${min}"`:''}${max!=null?` max="${max}"`:''}${ph?` placeholder="${ph}"`:''}>`;
      }
      return `<div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px">${label}</label>${inp}</div>`;
    };

    let html = `<div style="${G('6px')}">
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center">
        <button class="btn-secondary" onclick="this.disabled=true;this.textContent='Synchronisiere…';API.post('/v1/models/config',{action:'sync'}).then(()=>{showToast('Synchronisiere…');setTimeout(()=>API.getModelsConfig().then(d=>{state.modelsConfig=d;switchGeneralTab('models');showToast('Synchronisiert')}),3000)}).catch(e=>{showToast('Fehlgeschlagen',true);this.disabled=false;this.textContent='Von Providern synchronisieren'})">Von Providern synchronisieren</button>
        <button class="btn-secondary" onclick="runBenchmark()" title="Misst Fähigkeit (0-100 %, bewertet vom Server-Standardmodell) und Geschwindigkeit jedes aktivierten Modells pro Aufgabentyp. Das Ranking (fähig → schnell → günstig) steuert die ✨ Auto-Modellwahl.">Benchmark: alle aktivierten</button>
        <span id="bench-progress" style="font-size:11px;color:var(--text-400)"></span>
      </div>`;
    for (const prov of provKeys) {
      const models = byProvider[prov];
      const provId = `mdl-prov-${prov.replace(/[^a-zA-Z0-9]/g,'_')}`;
      const isOmlx = prov === 'omlx';
      html += `<div style="margin-bottom:6px;border:1px solid var(--border-100);border-radius:8px;overflow:hidden">
        <div style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:8px 12px;background:var(--bg-100)" onclick="const c=document.getElementById('${provId}');const open=c.style.display!=='none';c.style.display=open?'none':'block';this.querySelector('.mdl-arrow').textContent=open?'▶':'▼'">
          <span class="mdl-arrow" style="font-size:10px;color:var(--text-400)">▶</span>
          <span style="font-size:13px;font-weight:600;color:var(--text-100)">${esc(prov)}</span>
          <span style="font-size:11px;color:var(--text-400)">${models.length} Modell${models.length!==1?'e':''}</span>
          <span style="margin-left:auto;display:flex;gap:4px" onclick="event.stopPropagation()">
            <button class="btn-secondary" style="padding:1px 6px;font-size:10px" onclick="document.querySelectorAll('#${provId} .mdl-enabled').forEach(c=>{c.checked=true;c.closest('.mdl-header-row').style.opacity=1})">Alle</button>
            <button class="btn-secondary" style="padding:1px 6px;font-size:10px" onclick="document.querySelectorAll('#${provId} .mdl-enabled').forEach(c=>{c.checked=false;c.closest('.mdl-header-row').style.opacity=0.5})">Keine</button>
          </span>
        </div>
        <div id="${provId}" style="display:none;padding:4px 8px">`;
      for (const [mid, cfg] of models) {
        const inf = cfg.inference || {};
        const detId = `mdl-det-${mid.replace(/[^a-zA-Z0-9]/g,'_')}`;
        html += `<div data-model-id="${esc(mid)}">
          <div style="${ROW};opacity:${cfg.enabled?1:0.5}" class="mdl-header-row">
            <input type="checkbox" class="mdl-enabled" ${cfg.enabled?'checked':''} onchange="this.closest('.mdl-header-row').style.opacity=this.checked?1:0.5">
            <input class="mdl-display-name" value="${esc(cfg.display_name || modelShortName(mid, false))}" style="width:140px;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:12px;background:var(--bg-000);color:var(--text-100)" placeholder="Anzeigename" title="Anzeigename">
            <span style="${MONO};flex:1;overflow:hidden;text-overflow:ellipsis" title="${esc(mid)}">${esc(mid)}</span>
            <span class="mdl-warmup-dot" data-model-dot="${esc(mid)}" style="display:${cfg.warmup?'inline-block':'none'};width:8px;height:8px;border-radius:50%;background:var(--text-500);flex:none" title="Warmup-Status"></span>
            <label style="font-size:11px;color:var(--text-400)">P</label><input type="number" class="mdl-priority" value="${cfg.priority||0}" style="width:50px;padding:2px 4px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;text-align:center;background:var(--bg-000);color:var(--text-200)">
            <button class="btn-secondary" style="padding:2px 6px;font-size:12px" onclick="const d=document.getElementById('${detId}');d.style.display=d.style.display==='none'?'block':'none'" title="Modelleinstellungen">&#9881;</button>
            <button class="btn-secondary" style="padding:2px 6px;font-size:10px;color:var(--error)" onclick="_confirmRemoveModel('${esc(mid)}')">&#10005;</button>
          </div>
          <div id="${detId}" style="display:none;padding:8px 12px;margin:0 0 6px 0;border:1px solid var(--border-100);border-top:none;border-radius:0 0 8px 8px;background:var(--bg-100)">
            <div style="margin-bottom:8px">
              <label style="font-size:11px;font-weight:600;color:var(--text-100);display:block;margin-bottom:3px">Beschreibung <span style="color:var(--text-400);font-weight:400">(wird als Tooltip in Modell-Dropdowns angezeigt)</span></label>
              <textarea class="mdl-description" rows="2" style="width:100%;padding:4px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:12px;background:var(--bg-000);color:var(--text-100);font-family:inherit;resize:vertical" placeholder="z. B. Am besten für Long-Context-Analyse. Langsam, aber günstig.">${esc(cfg.description || '')}</textarea>
            </div>
            <div style="display:flex;align-items:center;gap:10px;padding:6px 8px;margin-bottom:8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000)">
              <label style="font-size:13px;font-weight:600;color:var(--text-100);margin:0">Profil${helpIcon('Optimierungs-Profil — setzt sinnvolle Standardwerte für die Felder unten; explizit gesetzte Felder überschreiben das Profil.\n\n• Speed (lokal): Warmup + stabiles KV-Präfix, keine Token-Einsparung.\n• Balanced (Standard): aktuelle Standardwerte.\n• Frugal (Cloud): aggressive Token-Einsparung, knapper Caveman-Ausgabestil.\n• Custom: keine Überlagerung.')}</label>
              <select class="mdl-profile" style="padding:3px 8px;border:1px solid var(--border-100);border-radius:4px;font-size:13px;background:var(--bg-100);color:var(--text-100)">
                ${[['custom','Custom (keine Überlagerung)'],['speed','Speed (lokal, warmer Cache)'],['balanced','Balanced (Standard)'],['frugal','Frugal (Cloud, Tokens sparen)']].map(([v,l]) => `<option value="${v}"${(cfg.profile||'custom')===v?' selected':''}>${l}</option>`).join('')}
              </select>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px">
              ${mdlInput('mdl-max-context','Kontextfenster',cfg.max_context,{ph:'131072'})}
              ${mdlInput('mdl-max-output','Max. Ausgabe',cfg.max_output,{ph:'16384'})}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              ${mdlInput('mdl-inf-temperature','Temperature',inf.temperature,{step:'0.1',min:0,max:2,ph:'1.0'})}
              ${mdlInput('mdl-inf-top_p','Top P',inf.top_p,{step:'0.05',min:0,max:1,ph:'1.0'})}
              ${mdlInput('mdl-inf-top_k','Top K',inf.top_k,{min:0,ph:'(keine)'})}
              ${mdlInput('mdl-inf-max_tokens','Max-Tokens-Überschreibung',inf.max_tokens,{ph:'(auto)'})}
              ${mdlInput('mdl-inf-frequency_penalty','Freq.-Penalty',inf.frequency_penalty,{step:'0.1',min:-2,max:2,ph:'0'})}
              ${mdlInput('mdl-inf-presence_penalty','Pres.-Penalty',inf.presence_penalty,{step:'0.1',min:-2,max:2,ph:'0'})}
              ${isOmlx ? `
                ${mdlInput('mdl-inf-min_p','Min P',inf.min_p,{step:'0.01',min:0,max:1,ph:'0'})}
                ${mdlInput('mdl-inf-repetition_penalty','Rep.-Penalty',inf.repetition_penalty,{step:'0.1',min:0,max:2,ph:'1.0'})}
              ` : ''}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              ${mdlInput('mdl-cost-input','Kosten ein ($/M)',cfg.cost_input,{step:'0.01',min:0,ph:'0'})}
              ${mdlInput('mdl-cost-output','Kosten aus ($/M)',cfg.cost_output,{step:'0.01',min:0,ph:'0'})}
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              <div style="border-left:1px solid var(--border-100);margin:0 -2px"></div>
              <div><label style="font-size:13px;color:var(--text-400);display:block;margin-bottom:2px">Caveman (Standard-Ausgabestil)${helpIcon('Standard-Antwortstil für dieses Modell (0=aus, lite/voll/ultra). Wirkt NUR auf den Ausgabestil — der System-Prompt und die Tool-Beschreibungen werden NICHT mehr komprimiert (das war fehleranfällig). Wird angewendet, solange im Chat der 🪨-Schalter auf „aus" steht; der Schalter pro Chat hat Vorrang. Die Eingabe wird nicht beeinflusst — die Komprimierung der Eingabe passiert nur beim Verfeinern.')}</label>
                <select class="mdl-caveman-system" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:13px;background:var(--bg-000);color:var(--text-200)">
                  ${[[0,'off'],[1,'lite'],[2,'full'],[3,'ultra']].map(([v,l]) => `<option value="${v}"${(cfg.caveman_system||0)===v?' selected':''}>${l}</option>`).join('')}
                </select>
              </div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Wie dieses Modell Reasoning ausgibt. none = deaktiviert. inline_tags = <think>...</think> im Inhalt (DeepSeek-R1, GLM-Zero). reasoning_field = separates reasoning_content (oMLX mit enable_thinking, Gemini 2.5, DeepSeek-R1 direkt). mistral_blocks = verschachtelte Thinking-Blöcke (magistral, mistral-small-2603+). openai_opaque = verborgen, nur Token-Anzahl sichtbar (o1/o3/o4-mini).">Thinking-Format</label>
                <select class="mdl-thinking-format" data-mid="${esc(mid)}" onchange="_mdlRefreshThinkingLevel(this)" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  ${['none','inline_tags','reasoning_field','mistral_blocks','openai_opaque'].map(v => `<option value="${v}"${(cfg.thinking_format||'none')===v?' selected':''}>${v}</option>`).join('')}
                </select>
              </div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Standard-Denkstufe für dieses Modell. Wird verwendet, wenn ein Chat oder eine geplante Aufgabe „Vom Modell erben" auswählt. Verfügbare Optionen hängen vom Thinking-Format ab.">Denkstufe</label>
                <select class="mdl-thinking-level" data-mid="${esc(mid)}" data-current="${esc((inf||{}).thinking_level||'')}" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                </select>
              </div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Wenn dieses Modell im Chat eine Hintergrundaufgabe per Fan-out aufteilt, laufen die Leaf-Tasks auf dem hier gewählten (i.d.R. günstigeren) Modell. Die Orchestrierung bleibt auf diesem Chat-Modell. Leer = Leaf-Tasks bleiben auf diesem Modell. Auto = die Absicht jedes Leaf-Tasks wird klassifiziert und das passende Modell gewählt (wie ✨ Auto im Verfasser). Die Denkstufe wird beim Wechsel automatisch passend gesetzt.">Fan-out-Modell</label>
                <select class="mdl-bgtask-model" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  <option value="">— keins (auf diesem Modell) —</option>
                  <option value="auto"${cfg.background_task_model==='auto'?' selected':''}>✨ Auto (per Absicht klassifizieren)</option>
                  ${_chatModelIds.filter(id => id !== mid).map(id => `<option value="${esc(id)}"${cfg.background_task_model===id?' selected':''}>${esc(modelShortName(id, false))}</option>`).join('')}
                </select>
              </div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-parallel-tools" ${cfg.parallel_tool_calls !== false ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer">Parallele Tool-Aufrufe</label></div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-auto-lcm" ${cfg.auto_lcm === true ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer" title="Automatische verlustfreie Kontext-Verdichtung (Auto-LCM): Brain verdichtet/entfaltet den Chat-Verlauf vor jedem Turn automatisch, sodass das Kontextfenster unter dem Schwellenwert bleibt. Manuelle Verdichtung ist dann deaktiviert. Standard: aus (pro Modell aktivieren).">Auto-LCM</label></div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-warmup" ${cfg.warmup ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer" title="Den KV-Cache dieses Modells einmal vorbereiten, damit die Latenz bis zum ersten Token minimal ist. Der warme Zustand wird gehalten, bis das Modell verdrängt wird — keine periodische Neu-Vorbereitung.">Warmup</label></div>
              <div><label style="font-size:10px;color:var(--text-400);display:block;margin-bottom:2px" title="Full: System+Tools in den KV-Cache vorladen (~5-6s erste Antwort, belegt GPU-Speicher). Minimal: nur Gewichte laden (~10-15s erste Antwort, winziger Speicherbedarf). Full-vorbereitete Modelle können sich gegenseitig verdrängen, wenn der GPU-Speicher knapp ist.">Warmup-Modus</label>
                <select class="mdl-warmup-mode" style="width:100%;padding:2px 6px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)">
                  <option value="full" ${(cfg.warmup_mode||'full')==='full'?'selected':''}>full (KV-Präfix)</option>
                  <option value="minimal" ${cfg.warmup_mode==='minimal'?'selected':''}>minimal (nur Gewichte)</option>
                </select>
              </div>
              <div style="display:flex;align-items:center;gap:6px;padding-top:14px"><input type="checkbox" class="mdl-warmup-allow-cloud" ${cfg.warmup_allow_cloud ? 'checked' : ''} style="margin:0"><label class="form-label" style="font-size:11px;margin:0;cursor:pointer" title="Warmup gegen Cloud-Provider erlauben (kostet Tokens)">Cloud erlauben</label></div>
              <div style="grid-column:1/-1"><label class="form-label" style="font-size:11px">Raw-Formate <span style="color:var(--text-400);font-weight:400">(MIME-Muster, die das Modell nativ als multimodal verarbeitet)</span></label><input class="form-input mdl-raw-formats" value="${esc((cfg.raw_formats||[]).join(', '))}" placeholder="z. B. image/*, application/pdf" style="font-size:12px"></div>
              <div style="grid-column:1/-1"><label class="form-label" style="font-size:11px">Fähigkeiten <span style="color:var(--text-400);font-weight:400">(Routing-Flags — steuern, wo das Modell in der UI auswählbar ist)</span></label>
                <div class="mdl-capabilities-grid" data-mid="${esc(mid)}" style="display:flex;flex-wrap:wrap;gap:10px;padding:6px 8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-100)">
                  ${(()=>{
                    const caps = new Set(cfg.capabilities||[]);
                    const opts = [
                      ['chat',  'Chat',  'Im Chat-Eingabefeld und jedem allgemeinen Modell-Dropdown auswählbar.'],
                      ['image', 'Bild',  'Vision-Eingabe — von read_document für Bildanhänge verwendet.'],
                      ['audio', 'Audio', 'Sprache-zu-Text — unter transcribe_audio aufgeführt.'],
                      ['tts',   'TTS',   'Text-zu-Sprache — unter text_to_speech aufgeführt.'],
                      ['video', 'Video', 'Video-Eingabe — für videofähige Modelle reserviert.'],
                    ];
                    return opts.map(([k,l,t]) => `<label style="display:flex;gap:5px;align-items:center;font-size:11px;cursor:pointer" title="${esc(t)}"><input type="checkbox" class="mdl-cap-cb" data-cap="${k}" ${caps.has(k)?'checked':''}>${l}</label>`).join('');
                  })()}
                </div>
              </div>
              ${_mdlBenchmarkSection(mid, cfg)}
            </div>
          </div>
        </div>`;
      }
      html += `</div></div>`;
    }
    // Add Model form
    const knownProvs = [...new Set(Object.values(mc).map(c=>c.provider).filter(Boolean))].sort();
    html += `<div style="margin-top:12px;padding:12px;border:1px solid var(--border-200);border-radius:8px;${G('8px')}">
      <div style="font-size:13px;font-weight:600;color:var(--text-100);margin-bottom:4px">Modell manuell hinzufügen</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
        <div style="flex:2;min-width:180px"><label class="form-label">Modell-ID</label><input class="form-input" id="add-model-id" placeholder="z. B. my-model-v1"></div>
        <div style="flex:1;min-width:120px"><label class="form-label">Provider</label><input class="form-input" id="add-model-provider" list="add-model-provs" placeholder="Provider-Name"><datalist id="add-model-provs">${knownProvs.map(p=>`<option value="${esc(p)}">`).join('')}</datalist></div>
        <div style="flex:1;min-width:120px"><label class="form-label">Anzeigename</label><input class="form-input" id="add-model-display" placeholder="Optional"></div>
        <button class="btn-primary" style="height:34px" onclick="addManualModel()">Hinzufügen</button>
      </div>
    </div>`;
    html += `<div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn-primary" onclick="saveModelsConfig()">Speichern</button>
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

// Per-model benchmark table inside the detail panel: one row per task type
// with measured capability%/latency and editable admin overrides (override
// wins over measured at routing time + survives the next benchmark run).
function _mdlBenchmarkSection(mid, cfg) {
  const TASK_TYPES = ['coding','math','research','analysis','reporting','creative','orchestration','agentic','fast'];
  const bench = cfg.benchmark || {};
  const rows = TASK_TYPES.map(t => {
    const cell = bench[t] || {};
    const m = cell.measured || {};
    const ov = cell.override || {};
    const measTxt = (m.capability != null)
      ? `${m.capability}% · ${m.tps||0} tok/s${m.n?` · n=${m.n}`:''}${m.error?` ⚠`:''}`
      : '<span style="color:var(--text-500)">—</span>';
    const inS = `width:60px;padding:1px 4px;border:1px solid var(--border-100);border-radius:4px;font-size:11px;background:var(--bg-000);color:var(--text-200)`;
    return `<tr data-bench-task="${t}">
      <td style="padding:2px 6px;font-size:11px;color:var(--text-200)">${t}</td>
      <td style="padding:2px 6px;font-size:11px;color:var(--text-300)" title="${esc(m.error||'')}">${measTxt}</td>
      <td style="padding:2px 6px"><input class="mdl-bench-ov-cap" type="number" min="0" max="100" value="${ov.capability??''}" placeholder="auto" style="${inS}" title="Override Fähigkeit %"></td>
      <td style="padding:2px 6px"><input class="mdl-bench-ov-tps" type="number" min="0" step="0.1" value="${ov.tps??''}" placeholder="auto" style="${inS}" title="Override Durchsatz tok/s"></td>
    </tr>`;
  }).join('');
  return `<div style="grid-column:1/-1;margin-top:6px;border-top:1px solid var(--border-100);padding-top:8px">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
      <label class="form-label" style="font-size:11px;margin:0">Benchmark <span style="color:var(--text-400);font-weight:400">(Fähigkeit % bewertet vom Server-Standardmodell · Geschwindigkeit · Override schlägt Messung)</span></label>
      <button class="btn-secondary" style="padding:1px 8px;font-size:10px;margin-left:auto" onclick="runBenchmark('${esc(mid)}')" title="Nur dieses Modell über alle Aufgabentypen benchmarken">Dieses Modell benchmarken</button>
      <button class="btn-secondary" style="padding:1px 8px;font-size:10px" onclick="saveBenchmarkOverrides('${esc(mid)}',this)" title="Overrides dieses Modells speichern">Overrides speichern</button>
    </div>
    <table data-bench-model="${esc(mid)}" style="width:100%;border-collapse:collapse">
      <thead><tr style="font-size:10px;color:var(--text-400);text-align:left">
        <th style="padding:2px 6px">Aufgabe</th><th style="padding:2px 6px">Gemessen</th>
        <th style="padding:2px 6px">Override %</th><th style="padding:2px 6px">Override tok/s</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

// Trigger a benchmark (one model when mid given, else all enabled) and poll
// progress until done, then reload the models tab to show fresh scores.
async function runBenchmark(mid) {
  try {
    const body = { action: 'benchmark' };
    if (mid) body.model_id = mid;
    await API.post('/v1/models/config', body);
    showToast(mid ? 'Benchmark gestartet (1 Modell)…' : 'Benchmark gestartet (alle aktivierten)…');
    _pollBenchmark();
  } catch (e) { showToast('Benchmark-Start fehlgeschlagen: ' + (e.message||e), true); }
}

function _pollBenchmark() {
  const el = document.getElementById('bench-progress');
  const tick = async () => {
    let s;
    try { s = await API.get('/v1/models/benchmark/status'); }
    catch (e) { if (el) el.textContent = ''; return; }
    if (s.running) {
      if (el) el.textContent = `Benchmark: ${s.done}/${s.total} · ${modelShortName(s.current_model||'', false)}`;
      setTimeout(tick, 2000);
    } else {
      if (el) el.textContent = s.errors && s.errors.length ? `Fertig (${s.errors.length} Fehler)` : 'Fertig';
      // Reload fresh config so the benchmark tables repopulate.
      API.getModelsConfig().then(d => { state.modelsConfig = d; switchGeneralTab('models'); });
      showToast('Benchmark abgeschlossen');
    }
  };
  tick();
}

// Persist this model's override cells. Empty inputs clear the override (router
// falls back to measured). Reads the live config, merges, saves the whole map.
async function saveBenchmarkOverrides(mid, btn) {
  try {
    const mc = { ...(state.modelsConfig?.models || {}) };
    if (!mc[mid]) return;
    const table = Array.from(document.querySelectorAll('table[data-bench-model]'))
      .find(t => t.dataset.benchModel === mid);
    if (!table) return;
    const bench = { ...(mc[mid].benchmark || {}) };
    table.querySelectorAll('tr[data-bench-task]').forEach(tr => {
      const task = tr.dataset.benchTask;
      const cap = tr.querySelector('.mdl-bench-ov-cap')?.value?.trim();
      const tps = tr.querySelector('.mdl-bench-ov-tps')?.value?.trim();
      const cell = { ...(bench[task] || {}) };
      if (cap === '' && tps === '') { delete cell.override; }
      else {
        cell.override = {};
        if (cap !== '') cell.override.capability = Math.max(0, Math.min(100, Number(cap)));
        if (tps !== '') cell.override.tps = Math.max(0, Number(tps));
      }
      if (Object.keys(cell).length) bench[task] = cell; else delete bench[task];
    });
    mc[mid] = { ...mc[mid], benchmark: bench };
    await API.post('/v1/models/config', { action: 'save', models: mc });
    state.modelsConfig.models = mc;
    showToast('Overrides gespeichert');
  } catch (e) { showToast('Speichern fehlgeschlagen: ' + (e.message||e), true); }
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
        const USAGE_LABELS = {preferred:'Bevorzugt (Prio 1)',round_robin:'Round-Robin (Prio 2)',fallback:'Fallback (Prio 3)'};
        const USAGE_COLORS = {preferred:'var(--accent)',round_robin:'var(--text-200)',fallback:'var(--text-400)'};
        const pStats = statsByProvider[p.name];
        const fmtNum = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : String(n||0);
        const keys = p.api_keys || [];
        const keyCounts = {preferred:0, round_robin:0, fallback:0};
        for (const k of keys) keyCounts[k.usage] = (keyCounts[k.usage]||0) + 1;
        const keySummaryParts = [];
        if (keyCounts.preferred) keySummaryParts.push(`${keyCounts.preferred} bevorzugt`);
        if (keyCounts.round_robin) keySummaryParts.push(`${keyCounts.round_robin} Round-Robin`);
        if (keyCounts.fallback) keySummaryParts.push(`${keyCounts.fallback} Fallback`);
        const keySummary = keys.length
          ? `${keys.length} Schlüssel${keySummaryParts.length?` · ${keySummaryParts.join(' · ')}`:''}`
          : 'Keine Schlüssel konfiguriert';
        const keySummaryColor = keys.length ? 'var(--text-200)' : 'var(--warning)';
        const provStatsLine = pStats
          ? `${pStats.calls} Aufrufe · ${fmtNum(pStats.tokens_in)} ein · ${fmtNum(pStats.tokens_out)} aus${pStats.cost_usd > 0 ? ' · $'+pStats.cost_usd.toFixed(4) : ''} (30 T)`
          : 'Keine Nutzung in den letzten 30 Tagen';
        html += `<div style="padding:12px;border:1px solid var(--border-100);border-radius:10px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            ${DOT(ok)}
            <span style="font-size:14px;font-weight:500;color:var(--text-000)">${esc(p.name)}</span>
            <span style="${MONO};margin-left:auto">${mc} Modelle</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="syncProvider(this,'${esc(p.name)}')" title="Neu verfügbare Modelle dieses Providers hinzufügen. Berücksichtigt Löschungen.">Sync</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="resyncProvider(this,'${esc(p.name)}')" title="Alle Modelle dieses Providers verwerfen UND Lösch-Tombstones löschen, dann neu ermitteln. Nur manuell.">Vollständige Neusynchronisierung</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="testProvider('${esc(p.name)}')">Test</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="document.getElementById('${pid}').style.display=document.getElementById('${pid}').style.display==='none'?'block':'none'">Einstellungen</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="renameProvider('${esc(p.name)}')" title="Diesen Provider umbenennen. Aktualisiert Modelle, default_provider, Tombstones und provider-bezogene Modell-IDs in einem Schritt.">Umbenennen</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_confirmDeleteProvider('${esc(p.name)}')">Löschen</button>
          </div>
          <div style="${MONO};overflow:hidden;text-overflow:ellipsis;margin-bottom:8px">${esc(p.base_url||'')}</div>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 8px;background:var(--bg-100);border-radius:6px">
            <span style="font-size:11px;color:${keySummaryColor};font-weight:500">${keySummary}</span>
            <span style="${MONO};font-size:10px;color:var(--text-400);margin-left:6px">${provStatsLine}</span>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;margin-left:auto" onclick="openProviderKeysModal('${esc(p.name)}')">Schlüssel verwalten</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="openProviderStatsModal('${esc(p.name)}')">Statistiken</button>
          </div>
          ${(p.models||[]).length?`<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">${(p.models||[]).slice(0,8).map(m=>{const mid=typeof m==='string'?m:(m.id||m);return BADGE(modelShortName(mid,false));}).join('')}${(p.models||[]).length>8?`<span style="${MONO}">+${(p.models||[]).length-8} weitere</span>`:''}</div>`:''}
          <div id="${pid}" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--border-100)">
            <div style="${G('8px')}">
              <div><label class="form-label">Basis-URL</label><input class="form-input" id="${pid}-url" value="${esc(p.base_url||'')}"></div>
              <div><label class="form-label">Standardmodell</label><input class="form-input" id="${pid}-model" value="${esc(p.default_model||'')}"></div>
              <div><label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200);cursor:pointer"><input type="checkbox" id="${pid}-is-local"${p.is_local?' checked':''}> Lokaler Provider${helpIcon('Markiert diesen Provider als „läuft auf diesem Gerät". Lokale Modelle umgehen den harten PII-/DSGVO-Block und die Kostenkontingente (es entstehen keine Cloud-Kosten und die Daten verlassen das Gerät nicht).')}</label></div>
              <div><button class="btn-primary" style="font-size:12px" onclick="saveProviderEdit('${esc(p.name)}','${pid}')">Einstellungen speichern</button></div>
            </div>
          </div>
        </div>`;
      }
      html += `
        ${SEC('Provider hinzufügen')}
        <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('10px')}">
          <div><label class="form-label">Name</label><input class="form-input" id="prov-name" placeholder="z. B. my-provider"></div>
          <div><label class="form-label">Basis-URL</label><input class="form-input" id="prov-url" placeholder="http://localhost:8081/v1"></div>
          <div><label class="form-label">API-Schlüssel</label><input class="form-input" id="prov-key" placeholder="sk-..." type="password"></div>
          <div><label class="form-label">Standardmodell</label><input class="form-input" id="prov-model" placeholder="Modellname (optional)"></div>
          <div><label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200);cursor:pointer"><input type="checkbox" id="prov-is-local"> Lokaler Provider${helpIcon('Markiert diesen Provider als „läuft auf diesem Gerät". Lokale Modelle umgehen den harten PII-/DSGVO-Block und die Kostenkontingente (es entstehen keine Cloud-Kosten und die Daten verlassen das Gerät nicht).')}</label></div>
          <div style="display:flex;gap:8px">
            <button class="btn-secondary" onclick="testNewProvider()">Verbindung testen</button>
            <button class="btn-primary" onclick="saveNewProvider()">Provider hinzufügen</button>
          </div>
          <div id="prov-test-result"></div>
        </div>
      </div>`;
      C.innerHTML = P(html);
    } catch(e) { C.innerHTML = P('<div style="color:var(--error)">Provider konnten nicht geladen werden</div>'); }
}

async function _genTab_agents(C) {
  /* ─── AGENTS ─── */
    const agents = state.agents || [];
    let html = `<div style="${G('12px')}">`;
    html += `${SEC('Agent erstellen')}
      <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
        <div style="display:flex;gap:8px">
          <div style="flex:1"><label class="form-label">Agent-ID</label><input class="form-input" id="new-agent-id" placeholder="z. B. Analyst"></div>
          <div style="flex:1"><label class="form-label">Anzeigename</label><input class="form-input" id="new-agent-display" placeholder="Optionaler Anzeigename"></div>
        </div>
        <div><label class="form-label">Beschreibung</label><input class="form-input" id="new-agent-desc" placeholder="Was macht dieser Agent?"></div>
        <div><label class="form-label">Modell</label><select class="form-select" id="new-agent-model" style="width:100%">
          <option value="auto-cloud" title="Wählt pro Nachricht automatisch das beste Cloud-Modell">✨ Smart (Cloud)</option>
          <option value="auto-local" title="Wählt pro Nachricht automatisch das beste lokale Modell">✨ Smart (Lokal)</option>
          ${enabledModelsWithCapability('chat').map(([mid])=>modelOption(mid)).join('')}
        </select></div>
        <div><label class="form-label">Soul (System-Prompt)</label><textarea class="form-input" id="new-agent-soul" rows="3" placeholder="Optionaler anfänglicher soul.md-Inhalt" style="resize:vertical"></textarea></div>
        <div style="display:flex;gap:8px">
          <button class="btn-primary" onclick="_createNewAgent()">Agent erstellen</button>
        </div>
        <div id="agent-create-result"></div>
      </div>`;
    html += SEC('Alle Agents');
    for (const a of agents) {
      const aid = a.id || a.name;
      const isMain = aid === 'main';
      html += `<div style="${ROW}">
        <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(a.display_name||aid)}</span>
        <span style="${MONO}">${esc(aid)}</span>
        ${a.model?`<span style="${MONO}">${esc(modelShortName(a.model))}</span>`:''}
        ${a.paused?BADGE('pausiert','var(--warning)'):''}
        ${a.is_team_head?BADGE('Team-Leiter','var(--accent)'):''}
        <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="openAgentConfig('${esc(aid)}');this.closest('.modal-overlay').remove()">Konfigurieren</button>
        ${isMain?'':`<button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_deleteAgent('${esc(aid)}')">Löschen</button>`}
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
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_dissolveTeam('${esc(tid)}')">Auflösen</button>
          </div>
          ${team.description?`<div style="font-size:12px;color:var(--text-400);margin:4px 0">${esc(team.description)}</div>`:''}
          <div style="${G('4px')};margin-top:8px">`;
        for (const m of (team.members||[])) {
          const mid = m.id;
          html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--border-100);border-radius:6px">
            <span style="font-size:13px;color:var(--text-100);flex:1">${esc(m.display_name||mid)}</span>
            <span style="${MONO}">${esc(mid)}</span>
            ${BADGE(m.is_team_head?'Leiter':'Mitglied')}
            ${!m.is_team_head?`<button class="btn-secondary" style="padding:1px 6px;font-size:10px;color:var(--error)" onclick="_removeFromTeam('${esc(mid)}','${esc(tid)}')">Entfernen</button>`:''}
          </div>`;
        }
        html += `</div>
          <div style="display:flex;gap:6px;margin-top:8px;align-items:center">
            <select class="form-select" id="team-add-${esc(tid)}" style="flex:1;font-size:12px">
              <option value="">Agent zum Team hinzufügen…</option>
              ${allAgents.filter(a=>{const aid=a.id||a.name;return aid!=='main'&&!(team.members||[]).some(m=>m.id===aid)}).map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
            </select>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="_addToTeam('${esc(tid)}')">Hinzufügen</button>
          </div>
        </div>`;
      }
    }

    /* Standalone agents */
    if (ts.standalone?.length) {
      html += SEC('Eigenständig');
      for (const a of ts.standalone) {
        html += `<div style="${ROW}"><span style="font-size:13px;color:var(--text-100);flex:1">${esc(a.display_name||a.id)}</span><span style="${MONO}">${esc(a.id)}</span></div>`;
      }
    }

    /* Create team form */
    html += SEC('Team erstellen');
    const nonMainAgents = allAgents.filter(a=>(a.id||a.name)!=='main');
    html += `<div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
      <div style="display:flex;gap:8px">
        <div style="flex:1"><label class="form-label">Team-Name</label><input class="form-input" id="new-team-name" placeholder="z. B. Research Team"></div>
        <div style="flex:1"><label class="form-label">Beschreibung</label><input class="form-input" id="new-team-desc" placeholder="Optional"></div>
      </div>
      <div><label class="form-label">Team-Leiter</label><select class="form-select" id="new-team-head" style="width:100%">
        <option value="">Leiter-Agent auswählen…</option>
        ${nonMainAgents.map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
      </select></div>
      <div><label class="form-label">Mitglieder (mehrere auswählbar)</label><select class="form-select" id="new-team-members" multiple style="width:100%;min-height:80px">
        ${nonMainAgents.map(a=>`<option value="${esc(a.id||a.name)}">${esc(a.display_name||a.id||a.name)}</option>`).join('')}
      </select></div>
      <div style="display:flex;gap:8px">
        <button class="btn-primary" onclick="_createTeam()">Team erstellen</button>
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
            ${n.paused?BADGE('pausiert','var(--warning)'):''}
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
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px" onclick="API.post('/v1/nodes',{action:'${n.paused?'resume':'pause'}',name:'${esc(n.name)}'}).then(()=>{showToast('${n.paused?'Fortgesetzt':'Pausiert'}');switchGeneralTab('nodes')})">${n.paused?'Fortsetzen':'Pausieren'}</button>
            <button class="btn-secondary" style="padding:2px 8px;font-size:11px;color:var(--error)" onclick="_confirmRemoveNode('${esc(n.name)}')">Entfernen</button>
          </div>
        </div>`;
      }
      if (!nodes.length) html += '<div style="padding:20px;text-align:center;color:var(--text-400)">Keine Remote-Nodes konfiguriert</div>';
      html += `${SEC('Node hinzufügen')}
        <div style="padding:12px;border:1px solid var(--border-200);border-radius:10px;${G('8px')}">
          <div><label class="form-label">Name</label><input class="form-input" id="node-name" placeholder="my-node"></div>
          <div><label class="form-label">Beschreibung</label><input class="form-input" id="node-desc" placeholder="Optionale Beschreibung"></div>
          <button class="btn-primary" onclick="createNode()">Node erstellen</button>
          <div id="node-result"></div>
        </div></div>`;
      C.innerHTML = P(html);
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Nodes nicht verfügbar</div>'); }
}

async function _genTab_context(C) {
  /* ─── CONTEXT ─── */
    try {
      const cfg = await API.get('/v1/context/config');
      const enabledModels = enabledModelsWithCapability('chat');
      const modelOpts = `<option value="">Auto (günstigstes)</option>` + enabledModels.map(([mid])=>modelOption(mid, {selected: mid===cfg.summary_model})).join('');

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="ctx-enabled" ${cfg.enabled!==false?'checked':''}>
          <label for="ctx-enabled" style="font-size:14px;font-weight:500;color:var(--text-200)">Verlustfreie Kontextverwaltung aktiviert</label>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div><label class="form-label">Frische Endnachrichten (Anzahl)</label><input class="form-input" id="ctx-fresh-tail" type="number" value="${cfg.fresh_tail_count||cfg.fresh_tail||16}" min="4" max="200"></div>
          <div><label class="form-label">Komprimierungsschwelle (%)</label><input class="form-input" id="ctx-threshold" type="number" value="${Math.round((cfg.compact_threshold||0.6)*100)}" min="50" max="95"></div>
          <div><label class="form-label">Nachrichten pro Zusammenfassung</label><input class="form-input" id="ctx-msgs-per-sum" type="number" value="${cfg.messages_per_summary||10}" min="3" max="50"></div>
          <div><label class="form-label">Verdichtungsschwelle</label><input class="form-input" id="ctx-condense" type="number" value="${cfg.condense_threshold||4}" min="2" max="10"></div>
          <div><label class="form-label">Max. Tiefe</label><input class="form-input" id="ctx-max-depth" type="number" value="${cfg.max_depth||5}" min="1" max="10"></div>
          <div><label class="form-label">Ziel-Tokens für Zusammenfassung</label><input class="form-input" id="ctx-target-tokens" type="number" value="${cfg.summary_target_tokens||1000}" min="200" max="4000" step="100"></div>
        </div>
        <div><label class="form-label">Zusammenfassungsmodell</label><select class="form-select" id="ctx-summary-model" style="width:100%">${modelOpts}</select></div>
        <button class="btn-primary" onclick="saveContextConfig()">Speichern</button>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Kontext-Konfiguration nicht verfügbar</div>'); }
}

async function _genTab_cleanup(C) {
  /* ─── BEREINIGUNG (auto archive + delete) ─── */
    try {
      const cfg = await API.get('/v1/cleanup/config');
      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="cleanup-enabled" ${cfg.enabled?'checked':''}>
          <label for="cleanup-enabled" style="font-size:14px;font-weight:500;color:var(--text-200)">Automatisches Archivieren &amp; Löschen aktiviert</label>
        </div>
        <div style="font-size:13px;color:var(--text-400);line-height:1.5">
          Private Chats, die nicht gemerkt (Wiki/Memory), favorisiert oder anderweitig
          referenziert sind und seit der eingestellten Zahl an Tagen nicht aufgerufen wurden,
          werden automatisch archiviert. Alles Archivierte wird nach Ablauf der Lösch-Frist
          endgültig gelöscht (inkl. zugehörigem Wiki). Gilt für projektlose und projektbasierte Chats.
          <br><strong>0 = Stufe deaktiviert.</strong>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div><label class="form-label">Archivieren nach (Tage Inaktivität)</label><input class="form-input" id="cleanup-archive-days" type="number" value="${cfg.archive_after_days ?? 30}" min="0" max="3650"></div>
          <div><label class="form-label">Löschen nach (Tage im Archiv)</label><input class="form-input" id="cleanup-delete-days" type="number" value="${cfg.delete_after_days ?? 90}" min="0" max="3650"></div>
        </div>
        <button class="btn-primary" onclick="saveCleanupConfig()">Speichern</button>
      </div>`);
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Bereinigungs-Konfiguration nicht verfügbar</div>'); }
}

async function _genTab_costs(C) {
  /* ─── COSTS ─── */
    try {
      const [stats, daily] = await Promise.all([API.getCosts(24).catch(()=>({})), API.getCostsDaily(7).catch(()=>({daily:[]}))]);
      let html = `<div style="${G('16px')}">
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--accent-brand)">$${(stats.total_cost||0).toFixed(2)}</div>
            <div style="font-size:11px;color:var(--text-400)">Letzte 24 h</div>
          </div>
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--text-000)">${(stats.total_calls||0).toLocaleString()}</div>
            <div style="font-size:11px;color:var(--text-400)">API-Aufrufe</div>
          </div>
          <div style="padding:12px 20px;background:var(--bg-200);border-radius:8px;text-align:center">
            <div style="font-size:22px;font-weight:600;color:var(--text-000)">${((stats.total_tokens_in||0)+(stats.total_tokens_out||0)).toLocaleString()}</div>
            <div style="font-size:11px;color:var(--text-400)">Tokens gesamt</div>
          </div>
        </div>
        ${Array.isArray(stats.by_agent)&&stats.by_agent.length?`${SEC('Nach Agent')}${stats.by_agent.map(s=>`<div style="${ROW}"><span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(s.agent)}</span><span style="${MONO}">${s.calls||0} Aufrufe</span><span style="font-size:13px;font-weight:500;color:var(--accent-brand)">$${(s.cost||0).toFixed(3)}</span></div>`).join('')}`:''}
        ${SEC('Täglich (7 Tage)')}`;
      for (const d of (daily.daily||[])) {
        html += `<div style="${ROW}">
          <span style="font-size:13px;color:var(--text-200);font-family:var(--font-mono)">${esc(d.day||d.date||'')}</span>
          <span style="flex:1"></span>
          <span style="${MONO}">${(d.calls||0)} Aufrufe</span>
          <span style="${MONO}">${((d.tokens_in||0)+(d.tokens_out||0)).toLocaleString()} Tok</span>
          <span style="font-size:13px;font-weight:500;color:var(--text-100)">$${(d.cost||0).toFixed(3)}</span>
        </div>`;
      }
      if (!(daily.daily||[]).length) html += '<div style="padding:20px;text-align:center;color:var(--text-400)">Keine Kostendaten</div>';
      C.innerHTML = P(html + '</div>');
    } catch(e) { C.innerHTML = P('<div style="color:var(--text-400)">Kostendaten nicht verfügbar</div>'); }
}

async function _genTab_quotas(C) {
  /* ─── QUOTAS ─── */
    if (!state.authUser || state.authUser.role !== 'admin') {
      C.innerHTML = P('<div style="color:var(--text-400);text-align:center;padding:32px">Die Kontingent-Konfiguration ist nur für Administratoren.</div>');
      return;
    }
    try {
      const cfg = await API.get('/v1/quotas/config');
      const usersResp = await API.get('/v1/quotas/admin/users').catch(()=>({users:[]}));
      const users = usersResp.users || [];
      const localModels = enabledModelsWithCapability('chat')
        .filter(([,c]) => c.is_local).map(([mid]) => mid);
      const _cycleLabels = {monthly:'monatlich',weekly:'wöchentlich',yearly:'jährlich'};
      const cycleOpts = ['monthly','weekly','yearly'].map(c => `<option value="${c}" ${c===cfg.billing_cycle?'selected':''}>${_cycleLabels[c]}</option>`).join('');
      const enforceOpts = [
        ['warn_only','Nur warnen (keine serverseitige Ablehnung)'],
        ['force_local','Bei Rot lokales Modell erzwingen'],
        ['hard_block','Bei Rot hart blockieren'],
      ].map(([v,l]) => `<option value="${v}" ${v===cfg.enforce_red?'selected':''}>${esc(l)}</option>`).join('');
      const fbOpts = ['<option value="">— keines —</option>'].concat(
        localModels.map(mid => modelOption(mid, {selected: mid===cfg.default_local_fallback_model, label: modelShortName(mid, true)}))
      ).join('');
      const startDayLabel = (cycle) => ({monthly:'Tag des Monats (1-31)', weekly:'Wochentag (0=Mo … 6=So)', yearly:'Monat des Jahres (1-12)'})[cycle] || 'Start';
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
              ${u.has_override ? '<span style="font-size:10px;color:var(--accent-brand)">Überschreibung</span>' : ''}
              ${u.disabled ? '<span style="font-size:10px;color:var(--error)">deaktiviert</span>' : ''}
            </div>
            <div style="font-size:11px;color:var(--text-300);margin-top:2px">
              heute ${fmt(u.daily.used_usd)} / ${fmt(u.daily.limit_usd)} (${dayPct}%) &middot;
              Zyklus ${fmt(u.cycle.used_usd)} / ${fmt(u.cycle.limit_usd)} (${cycPct}%)
            </div>
          </div>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="quotaOpenUserBreakdown('${esc(u.user_id)}','${esc(u.display_name||u.username)}')">Details</button>
          <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="quotaEditOverride('${esc(u.user_id)}','${esc(u.display_name||u.username)}')">${u.has_override ? 'Überschreibung bearbeiten' : 'Überschreibung setzen'}</button>
        </div>`;
      }).join('<div style="height:6px"></div>') : '<div style="color:var(--text-400);padding:12px 0">Keine Benutzer.</div>';
      C.innerHTML = P(`<div style="${G('16px')}">
        ${SEC('Zyklus')}
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Abrechnungszyklus</label>
            <select id="q-billing-cycle" class="form-input" style="width:140px">${cycleOpts}</select>
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)" id="q-start-day-label">${startDayLabel(cfg.billing_cycle)}</label>
            <input id="q-start-day" class="form-input" type="number" min="0" max="31" value="${cfg.cycle_start_day}" style="width:120px">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Warnen bei (%)</label>
            <input id="q-warn-pct" class="form-input" type="number" min="0" max="100" value="${cfg.warn_pct}" style="width:80px">
          </div>
          <div style="display:flex;flex-direction:column;gap:4px"><label style="font-size:11px;color:var(--text-400)">Blockieren bei (%)</label>
            <input id="q-block-pct" class="form-input" type="number" min="0" max="200" value="${cfg.block_pct}" style="width:80px">
          </div>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-200);margin-left:auto">
            <input id="q-enabled" type="checkbox" ${cfg.enabled?'checked':''}> Aktiviert
          </label>
        </div>

        ${SEC('Durchsetzung bei Rot', 'Was passiert, sobald ein Benutzer sein Kostenlimit erreicht (die Plan-Pille wird rot):\n\n• Nur warnen: Pille wird rot, Anfragen bleiben erlaubt.\n• Lokal erzwingen: Anfragen wechseln automatisch zum konfigurierten lokalen Fallback-Modell.\n• Hart blockieren: Anfragen werden abgelehnt, bis der Abrechnungszyklus zurücksetzt.\n\nDas lokale Fallback-Modell wird nur im Modus „Lokal erzwingen" verwendet.')}
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <select id="q-enforce" class="form-input" style="flex:1;max-width:340px">${enforceOpts}</select>
          <div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:200px">
            <label style="font-size:13px;color:var(--text-400)">Lokales Fallback-Modell (force_local-Modus)</label>
            <select id="q-fallback" class="form-input">${fbOpts}</select>
          </div>
        </div>

        ${SEC('Limits pro Rolle (USD)')}
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="color:var(--text-400);font-size:11px">
            <th style="text-align:left;padding:6px 8px;font-weight:500">Rolle</th>
            <th style="text-align:right;padding:6px 8px;font-weight:500">Täglich</th>
            <th style="text-align:right;padding:6px 8px;font-weight:500">${esc(({monthly:'Monatlich',weekly:'Wöchentlich',yearly:'Jährlich'})[cfg.billing_cycle]||'Zyklus')}</th>
          </tr></thead>
          <tbody>
          ${['admin','poweruser','user'].map(role => `
            <tr><td style="padding:6px 8px;color:var(--text-100)">${esc(roleLabelDe(role))}</td>
              <td style="padding:6px 8px;text-align:right">${limitInput(role,'daily_usd')}</td>
              <td style="padding:6px 8px;text-align:right">${limitInput(role,'cycle_usd')}</td>
            </tr>`).join('')}
          </tbody>
        </table>
        <div class="cfg-help">0 setzen bedeutet „kein Limit" für diese Achse. Nutzung lokaler Modelle zählt nie.</div>

        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn-primary" onclick="saveQuotaConfig()">Einstellungen speichern</button>
          <button class="btn-secondary" onclick="switchGeneralTab('quotas', document.querySelector('.modal-tab.active'))">Neu laden</button>
        </div>

        ${SEC('Benutzer')}
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
      C.innerHTML = P(`<div style="color:var(--text-400)">Kontingente nicht verfügbar: ${esc(String(e))}</div>`);
    }
}

async function _genTab_mempalace(C) {
  /* ─── MEMPALACE ─── */
    try {
      const mp = await API.get('/v1/mempalace/stats');
      if (!mp.enabled) {
        C.innerHTML = P(`<div style="${G('12px')}"><div style="color:var(--text-400)">MemPalace ist in config.json deaktiviert</div></div>`);
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
        ${STAT(mp.palace_size_mb + ' MB', 'DB-Größe', 'var(--text-200)')}
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
          : `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:color-mix(in srgb, var(--accent-brand) 15%, transparent);color:var(--accent-brand)">geteilt</span>`;
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
        wingsHtml += `<div style="font-size:11px;color:var(--text-400);margin:8px 0 4px">Benutzergebundene Wings (${userWings.length})</div>`;
        wingsHtml += userWings.map(([n,i]) => wingRow(n,i)).join('');
      }

      // Daemons config + chat sync status merged
      const sync = mp.chat_sync || {};
      const syncTime = sync.last_sync ? new Date(sync.last_sync * 1000).toLocaleString() : 'never';
      const cfg = mp.config || {};
      const daemonRows = `
        <div style="${ROW}">
          ${DOT(cfg.mine_enabled)} <span style="flex:1">Miner</span>
          <span style="${MONO}">alle ${Math.round(cfg.mine_interval_s/60)}m</span>
          <span style="${MONO}">${cfg.mine_sources} Quelle(n)</span>
        </div>
        <div style="${ROW}">
          ${DOT(cfg.chat_sync_enabled)} <span style="flex:1">Chat-Sync</span>
          <span style="${MONO}">alle ${cfg.chat_sync_interval_s}s</span>
          ${cfg.chat_sync_build_closets ? BADGE('Closets','var(--success)') : BADGE('keine Closets')}
          <span style="${MONO}">${sync.synced_sessions} Sitzungen</span>
          <span style="${MONO}">zuletzt: ${esc(syncTime)}</span>
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
        tunnelsHtml = `<div style="color:var(--text-400);font-size:12px">Keine expliziten Tunnel konfiguriert</div>`;
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
            <span style="font-size:12px;color:var(--text-300)">${wal.total_ops.toLocaleString()} Operationen gesamt</span>
            ${opBadges}
          </div>
          <div style="max-height:200px;overflow-y:auto">${recentRows}</div>
        </div>`;
      } else {
        walHtml = `<div style="color:var(--text-400);font-size:12px">Keine Write-Ahead-Log-Einträge</div>`;
      }

      // Anomaly detection
      let anomalies = [];
      if (mp.total_drawers > 0 && mp.total_closets === 0) anomalies.push('Keine Closets gebaut — das Such-Ranking kann beeinträchtigt sein');
      if (mp.total_drawers > 10000) anomalies.push(`Großer Palace (${mp.total_drawers.toLocaleString()} Drawers) — die Suche kann sich verlangsamen`);
      const emptyWings = sortedWings.filter(([,v]) => v.drawer_count < 3);
      if (emptyWings.length) anomalies.push(`${emptyWings.length} Wing(s) mit <3 Drawers: ${emptyWings.map(([n])=>n).join(', ')}`);
      if (!cfg.chat_sync_enabled) anomalies.push('Chat-Sync ist deaktiviert — neue Konversationen werden nicht gespeichert');
      if (!cfg.mine_enabled) anomalies.push('Miner ist deaktiviert — Dateiänderungen werden nicht indiziert');
      if (sync.last_sync && (Date.now()/1000 - sync.last_sync) > 600) anomalies.push('Letzter Chat-Sync liegt über 10 Minuten zurück');
      const orphanRatio = mp.total_closets > 0 ? mp.total_drawers / mp.total_closets : 0;
      if (orphanRatio > 20 && mp.total_drawers > 100) anomalies.push(`Hohes Drawer/Closet-Verhältnis (${Math.round(orphanRatio)}:1) — viele Drawers haben möglicherweise keine Closet-Abdeckung`);

      const anomalyHtml = anomalies.length
        ? anomalies.map(a => `<div style="${ROW};border-color:color-mix(in srgb, var(--warning,#f59e0b) 40%, transparent)">
            <span style="color:var(--warning,#f59e0b)">\u26A0</span>
            <span style="font-size:12px">${esc(a)}</span>
          </div>`).join('')
        : `<div style="${ROW};border-color:color-mix(in srgb, var(--success) 30%, transparent)">${DOT(true)} <span style="font-size:12px;color:var(--text-300)">Keine Anomalien erkannt</span></div>`;

      C.innerHTML = P(`<div style="${G('16px')}">
        ${SEC('Übersicht')}
        ${statsRow}

        ${SEC('Palace-Explorer')}
        <div id="mp-tree-tabs" style="display:flex;gap:0;margin-bottom:8px">
          <button class="modal-tab active" onclick="mpTreeSwitch('wings',this)" style="padding:6px 14px;font-size:12px">Wings</button>
          <button class="modal-tab" onclick="mpTreeSwitch('tunnels',this)" style="padding:6px 14px;font-size:12px">Tunnel</button>
        </div>
        <div id="mp-tree" style="max-height:400px;overflow-y:auto;border:1px solid var(--border-100);border-radius:8px;padding:4px 0"></div>

        ${anomalies.length ? SEC('Anomalien') + anomalyHtml : ''}

        ${SEC('Daemons')}
        ${daemonRows}

        ${SEC('Chat-Sync-Classifier', 'LLM-Gate, das jede Nachricht klassifiziert, bevor sie ins MemPalace-Gedächtnis abgelegt wird. Filtert Ablehnungen, Smalltalk und generische Inhalte heraus, damit nur sinnvolle Inhalte gespeichert werden.\n\n• Min. Turns: Chats, die kürzer sind, werden übersprungen (0 = kein Minimum).\n• Standard für neue Chats: Aus / Auto (Classifier entscheidet) / An (immer speichern).\n• Kategorien zum Ablegen: welche klassifizierten Inhaltsarten gespeichert werden.')}
        <div style="${G('10px')}">
          <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
            <label style="display:flex;align-items:center;gap:6px;font-size:12px"><input type="checkbox" id="mp-clf-enabled" ${clf.enabled?'checked':''}>Aktiviert</label>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Modell:</label>
              <select id="mp-clf-model" class="form-input" style="font-size:12px;padding:4px 8px;max-width:260px">
                <option value="">— Modell auswählen —</option>
                ${modelOpts}
              </select>
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Min. Turns:</label>
              <input type="number" id="mp-clf-min-turns" class="form-input" style="font-size:12px;padding:4px 8px;width:60px" value="${clf.min_turns||0}" min="0" max="100" title="Chats, die kürzer sind als dieser Wert, überspringen (0 = kein Minimum)">
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <label style="font-size:11px;color:var(--text-400)">Standard für neue Chats:</label>
              <select id="mp-clf-default-mode" class="form-input" style="font-size:12px;padding:4px 8px">
                <option value="0" ${(clf.default_mode||0)===0?'selected':''}>Aus</option>
                <option value="2" ${(clf.default_mode||0)===2?'selected':''}>Auto</option>
                <option value="1" ${(clf.default_mode||0)===1?'selected':''}>An</option>
              </select>
            </div>
          </div>
          <div style="margin-top:8px;font-size:11px;color:var(--text-400)">
            Auto-Modus: ${clf.enabled && clf.model ? 'LLM-Classifier' : ''}${clf.enabled && clf.model && clf.min_turns ? ' + ' : ''}${clf.min_turns ? 'mind. ' + clf.min_turns + ' Turns' : ''}${!clf.enabled && !clf.min_turns ? 'keine Filter konfiguriert' : ''}
          </div>
          <div style="margin-top:8px">
            <label style="font-size:11px;color:var(--text-400)">Kategorien zum Ablegen:</label>
            <div style="margin-top:4px">${catChecks}</div>
          </div>
          <button class="btn-primary" style="margin-top:10px;font-size:12px;padding:6px 16px" onclick="saveMpClassifier()">Speichern</button>
        </div>

        ${SEC('Write-Ahead-Log')}
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
          if (!drawers.length && !closets.length) { container.innerHTML = '<div style="padding:4px 8px;padding-left:'+(12+depth*20)+'px;color:var(--text-400);font-size:11px">Leer</div>'; return; }
          if (closets.length) {
            for (const c of closets) container.appendChild(_mpClosetNode(c, depth));
          }
          for (const d of drawers) container.appendChild(_mpDrawerNode(d, depth));
        } catch(e) { container.innerHTML = '<div style="color:var(--error);padding:4px 12px;font-size:11px">Laden fehlgeschlagen</div>'; }
      }

      function _mpRenderWingsTree(tree) {
        tree.innerHTML = '';
        const sorted = Object.entries(_mpWings).sort((a,b) => b[1].drawer_count - a[1].drawer_count);
        if (!sorted.length) { tree.innerHTML = '<div style="padding:12px;color:var(--text-400);font-size:12px">Keine Wings</div>'; return; }

        // Section A: Rooms view
        const secA = document.createElement('div');
        secA.innerHTML = '<div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;padding:8px 12px">Nach Room</div>';
        for (const [wname, winfo] of sorted) {
          const scopeBadge = winfo.user_scoped ? _mpBadge(winfo.user_name||winfo.user_id,'var(--text-300)') : _mpBadge('geteilt','var(--accent-brand)');
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
          secB.innerHTML = '<div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;padding:8px 12px">Nach Hall</div>';
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
          tree.innerHTML = '<div style="padding:12px;color:var(--text-400);font-size:12px">Keine Tunnel konfiguriert</div>';
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

    } catch(e) { C.innerHTML = P(`<div style="color:var(--error)">MemPalace-Statistiken konnten nicht geladen werden: ${esc(e.message||e)}</div>`); }
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
      const modelOptionsKg = '<option value="">Auto (Hintergrundauswahl: zuerst günstigstes lokales)</option>'
        + enabledModelList.map(([mid,cfg])=>{
          const sel = mid === currentModel ? ' selected' : '';
          const localTag = cfg.is_local ? ' [lokal]' : '';
          return modelOption(mid, {selected: mid === currentModel, suffix: localTag});
        }).join('');

      const profOpts = (sel) => ['normative','generic'].map(p =>
        `<option value="${p}" ${p === (sel||'normative')?'selected':''}>${p}</option>`
      ).join('');
      const methodOpts = (sel) => [['llm','LLM (hochwertig)'],['rules','Regelbasiert (kein LLM, lokal)']]
        .map(([v,l]) => `<option value="${v}" ${v === (sel||'llm')?'selected':''}>${l}</option>`).join('');
      const profileOpts = profOpts(kgConfig.profile);

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
      }).join('') || `<div style="padding:14px;color:var(--text-400);font-size:12px">Noch kein KG-Inhalt — legen Sie Dokumente in den Eingabeordner eines Projekts, um die Extraktion zu starten.</div>`;

      C.innerHTML = P(`<div style="${G('16px')}">
        <div style="display:flex;align-items:center;gap:8px">
          <span style="width:7px;height:7px;border-radius:50%;background:${kgConfig.enabled === false ? 'var(--error)' : 'var(--success)'};flex-shrink:0"></span>
          <span style="font-size:14px;font-weight:500;color:var(--text-100)">Knowledge Graph</span>
          <span style="${MONO}">${kgConfig.enabled === false ? 'deaktiviert' : 'aktiv'}</span>
          <span style="margin-left:auto;${MONO}">Geltungsbereich: ${esc((kgConfig.scopes||['projects']).join(','))}</span>
        </div>

        ${SEC('Übersicht')}
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          ${STAT(totalEntities, 'Entitäten')}
          ${STAT(totalTriples, 'Triples')}
          ${STAT(totalProjects, 'Projekte mit KG')}
          ${STAT(esc(kgConfig.profile || 'normative'), 'Profil', 'var(--text-200)')}
        </div>

        ${SEC('Extraktionseinstellungen')}
        <div style="${G('10px')};padding:12px;border:1px solid var(--border-100);border-radius:8px">
          <div style="display:grid;grid-template-columns:140px 1fr auto;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Aktiviert</label>
            <label style="display:inline-flex;gap:6px;font-size:12px"><input type="checkbox" id="kg-enabled" ${kgConfig.enabled===false?'':'checked'}> KG-Extraktion während der Projekt-Synchronisierung ausführen</label>
            <span></span>
          </div>
          <div style="font-size:12px;font-weight:500;color:var(--text-200);margin-top:4px">Projekte (Standard)</div>
          <div style="font-size:11px;color:var(--text-400);margin-top:-4px">Standard-Methode + -Profil für alle Projekte. Jedes Projekt kann beides in der Projektansicht überschreiben.</div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Methode</label>
            <select class="form-select" id="kg-method" onchange="kgSyncMethodUI()" ${isAdmin?'':'disabled'}>${methodOpts(kgConfig.method)}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px"><b>LLM</b>: ein Modell extrahiert Triples (hochwertig, kann Cloud sein). <b>Regelbasiert</b>: spaCy-NER + Beziehungsmuster, ganz lokal, kein LLM — nur offene (generic) Prädikate, geringere Qualität.</div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Extraktionsmodell</label>
            <select class="form-select" id="kg-model" ${isAdmin?'':'disabled'}>${modelOptionsKg}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px">Nur für die <b>LLM</b>-Methode. Cloud-Modelle extrahieren hochwertigere Triples; lokale Modelle halten Ihre Dokumente vor Ort. Das ausgewählte Modell läuft einmal pro Drawer während der Synchronisierung — wählen Sie sparsam. <b>Getesteter Standard:</b> gemma-4-e4b-it-4bit (lokal, deutschfähig, läuft neben dem Chat-Warmpool).</div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Profil</label>
            <select class="form-select" id="kg-profile" ${isAdmin?'':'disabled'}>${profileOpts}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px" id="kg-profile-help"><b>normative</b>: Richtlinien, Verordnungen, Gesetze, Spezifikationen, Verträge, SOPs &mdash; kontrollierte Prädikate (requires/forbids/cites/...). <b>generic</b>: offene Prädikate, beliebiger Dokumenttyp.</div>

          <div style="font-size:12px;font-weight:500;color:var(--text-200);margin-top:8px">Wiki</div>
          <div style="font-size:11px;color:var(--text-400);margin-top:-4px">Eigene Einstellungen für die KG-Extraktion aus Wiki-Seiten (das Gedächtnis des Agenten), unabhängig von den Projekten.</div>
          <div style="display:grid;grid-template-columns:140px 1fr auto;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Aktiviert</label>
            <label style="display:inline-flex;gap:6px;font-size:12px"><input type="checkbox" id="kg-wiki-enabled" ${kgConfig.wiki?'checked':''} ${isAdmin?'':'disabled'}> KG-Triples aus projektmarkierten Wiki-Seiten extrahieren</label>
            <span></span>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr 140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Methode</label>
            <select class="form-select" id="kg-wiki-method" onchange="kgSyncMethodUI()" ${isAdmin?'':'disabled'}>${methodOpts(kgConfig.wiki_method)}</select>
            <label style="font-size:12px;color:var(--text-300)">Profil</label>
            <select class="form-select" id="kg-wiki-profile" ${isAdmin?'':'disabled'}>${profOpts(kgConfig.wiki_profile)}</select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px" id="kg-wiki-profile-help">Wiki-Inhalte sind meist biografisch/relational — <b>generic</b> + <b>Regelbasiert</b> passt hier gut. Profil ist nur bei der LLM-Methode wirksam.</div>

          <div style="display:grid;grid-template-columns:140px 1fr 140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Max. Triples / Drawer</label>
            <input type="number" class="form-input" id="kg-max-triples" min="1" max="50" value="${kgConfig.max_triples_per_drawer||12}" ${isAdmin?'':'disabled'}>
            <label style="font-size:12px;color:var(--text-300)">Min. Konfidenz</label>
            <input type="number" class="form-input" id="kg-min-conf" min="0" max="1" step="0.05" value="${kgConfig.min_confidence??0.5}" ${isAdmin?'':'disabled'}>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Max. Zeichen / Drawer</label>
            <input type="number" class="form-input" id="kg-max-chars" min="500" max="20000" step="500" value="${kgConfig.max_drawer_chars||6000}" ${isAdmin?'':'disabled'}>
          </div>
          <div style="display:grid;grid-template-columns:140px 1fr 140px 1fr;gap:10px;align-items:center">
            <label style="font-size:12px;color:var(--text-300)">Closets neu erzeugen</label>
            <label style="display:inline-flex;gap:6px;font-size:12px"><input type="checkbox" id="kg-regen-closets" ${kgConfig.regenerate_closets?'checked':''} ${isAdmin?'':'disabled'}> Drawer-Retrieval nach jeder Synchronisierung per LLM neu ranken</label>
            <span></span><span></span>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-left:150px;margin-top:-4px">Fügt ~1 LLM-Aufruf pro Quelldatei pro Zyklus hinzu. Verbessert das <code>mempalace_query</code>-Ranking, indem die Regex-Closet-Erzeugung von MemPalace durch einen LLM-Durchlauf ersetzt wird, der implizite Themen, fremdsprachige Inhalte und kontextuelle Bezüge erfasst. Verwendet das oben ausgewählte Extraktionsmodell erneut.</div>
          <div style="display:flex;gap:8px;justify-content:flex-end">
            <button class="btn-primary" id="kg-save-btn" onclick="saveKgConfig()" ${isAdmin?'':'disabled'}>${isAdmin?'Einstellungen speichern':'Nur für Administratoren'}</button>
          </div>
        </div>

        ${SEC('Knowledge Graphs pro Projekt')}
        <div style="${G('6px')}">${projectRows}</div>

        ${SEC('Dokumentation')}
        <div style="font-size:12px;color:var(--text-300);padding:10px 12px;background:var(--bg-100);border-radius:8px;line-height:1.5">
          Der KG wird automatisch vom Projekt-Sync-Daemon erstellt. Jeder aus den Eingabeordnern eines Projekts geminte Drawer wird zur Triple-Extraktion an das konfigurierte LLM gesendet. Triples werden mit <code>source_file</code>- und <code>source_drawer_id</code>-Herkunft nach <code>${esc((window.__brain_palace_path||'~/.mempalace/brain'))}/knowledge_graph.sqlite3</code> geschrieben — so verweist jede Aussage zurück auf ihren Ursprung.
          <br><br>
          Agent-Tools: <code>mempalace_kg_query(entity)</code>, <code>mempalace_kg_search(predicate)</code>, <code>mempalace_kg_neighbors(entity, depth)</code> — alle automatisch auf das aufrufende Projekt beschränkt.
        </div>
      </div>`);
      kgSyncMethodUI();  // grey out profile selects where method=rules
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--error)">Knowledge-Graph-Ansicht konnte nicht geladen werden: ${esc(e.message||e)}</div>`);
    }
}

// Rule-based extraction only emits generic predicates, so the profile choice is
// inert when method=rules — grey the matching profile select + note it. Called
// on render and on each method <select> change.
function kgSyncMethodUI() {
  const pairs = [['kg-method', 'kg-profile', 'kg-profile-help'],
                 ['kg-wiki-method', 'kg-wiki-profile', 'kg-wiki-profile-help']];
  for (const [mId, pId, hId] of pairs) {
    const m = document.getElementById(mId);
    const p = document.getElementById(pId);
    if (!m || !p) continue;
    const rules = m.value === 'rules';
    if (rules) { p.value = 'generic'; p.disabled = true; p.style.opacity = '0.5'; }
    else { p.disabled = false; p.style.opacity = ''; }
    const h = document.getElementById(hId);
    if (h) h.style.opacity = rules ? '0.5' : '';
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
        ignore: 'Diese Kategorie nicht markieren.',
        warn:   'Vor dem Senden den Bestätigungsdialog anzeigen.',
        block:  'Ablehnen, sofern kein lokales Modell aktiv ist (erfordert aktivierten Master-Block).',
      };
      const ACT_COLORS = {
        ignore: 'var(--text-400)',
        warn:   '#b45309',
        block:  'var(--error)',
      };

      const actionSelect = (cat, current) => `
        <select class="form-select gdpr-cat-action" data-cat="${esc(cat)}" style="width:150px;font-size:12px">
          <option value="ignore" ${current==='ignore'?'selected':''}>Ignorieren</option>
          <option value="warn" ${current==='warn'?'selected':''}>Warnen</option>
          <option value="block" ${current==='block'?'selected':''}>Blockieren</option>
        </select>`;

      const policyCats = gs.categories || {};
      const policyOverrides = gs.rule_overrides || {};
      const policyMinOcc = gs.min_occurrences || {};

      // Build per-category rule expander
      const catRows = Object.keys(PIIScanner.categoryLabels).map(cat => {
        const catCfg = policyCats[cat] || {};
        const catAction = catCfg.action || PIIScanner.defaultCategoryActions[cat] || 'warn';
        const rules = catMembers[cat] || [];
        const overrideCount = rules.filter(r => policyOverrides[r]).length;
        const ruleRows = rules.map(rid => {
          const ovr = policyOverrides[rid] || '';
          const mo = policyMinOcc[rid];
          return `<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;border-bottom:1px solid var(--border-100)">
            <code style="font-size:10px;color:var(--text-400);min-width:160px">${esc(rid)}</code>
            <span style="flex:1;font-size:11px;color:var(--text-200)">${esc(ruleLabel(rid))}</span>
            <input type="number" min="1" class="form-input gdpr-rule-minocc" data-rule="${esc(rid)}" value="${mo!=null?esc(String(mo)):'1'}" title="Mindestanzahl UNTERSCHIEDLICHER Treffer im Dokument, bevor diese Regel auslöst (1 = bei jedem Treffer)" style="width:64px;font-size:11px">
            <select class="form-select gdpr-rule-override" data-rule="${esc(rid)}" style="width:150px;font-size:11px">
              <option value="">Kategorie verwenden (${catAction})</option>
              <option value="ignore" ${ovr==='ignore'?'selected':''}>Ignorieren</option>
              <option value="warn" ${ovr==='warn'?'selected':''}>Warnen</option>
              <option value="block" ${ovr==='block'?'selected':''}>Blockieren</option>
            </select>
          </div>`;
        }).join('');
        return `<div style="border:1px solid var(--border-100);border-radius:8px;margin-bottom:6px;background:var(--bg-100)">
          <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer" onclick="const n=this.nextElementSibling;n.style.display=n.style.display==='none'?'block':'none';this.querySelector('.gdpr-cat-caret').textContent=n.style.display==='none'?'&#9656;':'&#9662;'">
            <span class="gdpr-cat-caret" style="color:var(--text-400);font-size:11px">&#9656;</span>
            <span style="font-size:13px;font-weight:500;color:var(--text-100);flex:1">${esc(PIIScanner.categoryLabels[cat])}</span>
            <span style="font-size:10px;color:var(--text-400)">${rules.length} Regel${rules.length===1?'':'n'}${overrideCount?` &middot; <b style="color:#b45309">${overrideCount} Überschreibung${overrideCount===1?'':'en'}</b>`:''}</span>
            <span onclick="event.stopPropagation()">${actionSelect(cat, catAction)}</span>
          </div>
          <div style="display:none;border-top:1px solid var(--border-100);max-height:280px;overflow-y:auto">${ruleRows}</div>
        </div>`;
      }).join('');

      const allowlistText = (gs.email_allowlist || []).join('\n');

      C.innerHTML = P(`<div style="${G('12px')}">
        <div style="padding:12px 14px;border:1px solid var(--border-100);border-radius:8px;background:var(--bg-100)">
          <div style="font-size:13px;color:var(--text-100);margin-bottom:6px"><b>Wie Aktionen funktionieren</b></div>
          <div style="font-size:11px;color:var(--text-300);line-height:1.55">
            <b style="color:${ACT_COLORS.ignore}">Ignorieren</b>: Regel wird vollständig übersprungen — kein Scan, kein Log.<br>
            <b style="color:${ACT_COLORS.warn}">Warnen</b>: zeigt vor dem Senden den bernsteinfarbenen Bestätigungsdialog. Der Benutzer kann ihn schließen und fortfahren.<br>
            <b style="color:${ACT_COLORS.block}">Blockieren</b>: Das Senden wird abgelehnt, sofern das aktuelle Modell nicht lokal ist — das Eingabefeld leitet automatisch zum Fallback-Modell um. Erfordert den Master-Schalter <i>Anfragen mit PII blockieren</i> unten; andernfalls werden Block-Aktionen auf Warnen herabgestuft.
          </div>
        </div>

        ${SEC('Master-Schalter')}
        <div style="display:flex;flex-direction:column;gap:6px">
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-enabled" ${gs.enabled!==false?'checked':''}>
            <span><b>Scanner aktivieren</b> — Regex-Durchlauf ausgehender Nachrichten und Textanhänge</span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-serverlog" ${gs.server_log!==false?'checked':''}>
            <span><b>Serverseitiges Audit-Log</b> — jede Erkennung in <code>audit.db</code> aufzeichnen</span>
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-200);cursor:pointer">
            <input type="checkbox" id="gdpr-block" ${gs.server_block?'checked':''}>
            <span><b>Anfragen mit PII blockieren</b> — berücksichtigt <i>Block</i>-Aktionen der Kategorien. Wenn aus, wird Blockieren überall auf Warnen herabgestuft.</span>
          </label>
          <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
            <span style="font-size:12px;color:var(--text-300);min-width:200px">Standard-Fallback-Modell (lokal)</span>
            <select class="form-select" id="gdpr-fallback" style="flex:1" ${hasLocals?'':'disabled'}>
              <option value="">Keines (deaktiviert)</option>
              ${localOpts}
            </select>
          </div>
          <div style="font-size:11px;color:var(--text-400);margin-top:2px">Wird für Hintergrund-LLM-Aufrufe verwendet (Next-Prompt, Chat-Zusammenfassung, Memory-Classifier, Worker-Summariser, geplante Aufgaben) und für die automatische Umleitung im Eingabefeld, wenn ein blockierender Befund auf ein Cloud-Modell trifft. ${hasLocals?'':'<span style="color:var(--warning,#b45309)">Keine lokalen Modelle konfiguriert — fügen Sie zuerst eines unter Modelle hinzu.</span>'}</div>
        </div>

        ${SEC('NER-Modelle (Named Entity Recognition)')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:8px">
          spaCy erkennt Namen, Adressen und Organisationen zusätzlich zu den Regex-Regeln. Befunde liegen in der Kategorie <i>Kontaktdaten</i> — setzen Sie die Kategorieaktion unten auf <i>Warnen</i> oder <i>Blockieren</i>, um sie sichtbar zu machen. Geladene Modelle bleiben resident (~50 MB pro Stück); zum Freigeben von Speicher entladen.
        </div>
        <div id="gdpr-ner-pill" style="display:flex;flex-direction:column;gap:6px;min-height:32px">
          <div style="font-size:11px;color:var(--text-400);font-style:italic">Lädt…</div>
        </div>

        ${SEC('Hintergrund- / nicht-interaktive LLM-Aufrufe')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:8px">
          Richtlinie für Aufrufe, die Brain ohne Benutzerinteraktion macht (Next-Prompt-Vorschläge, Chat-Zusammenfassung, Memory-Classifier, geplante Aufgaben, Benutzerprofil-Daemon, KG-Extraktion). Der interaktive Chat ist nicht betroffen — Benutzer sehen dort weiterhin den Dialog pro Durchlauf.
        </div>
        <div style="display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:12px;color:var(--text-300);min-width:200px">Wenn PII erkannt wird</span>
            <select class="form-select" id="gdpr-bg-pii-action" style="flex:1">
              <option value="anonymise"${(gs.background_pii_action||'anonymise')==='anonymise'?' selected':''}>Auto-Anonymisierung (pseudonymisieren, dann Antwort de-anonymisieren)</option>
              <option value="swap_to_local"${gs.background_pii_action==='swap_to_local'?' selected':''}>Zum lokalen Fallback-Modell wechseln</option>
              <option value="skip"${gs.background_pii_action==='skip'?' selected':''}>Überspringen (kein Aufruf, leer fortfahren)</option>
              <option value="abort"${gs.background_pii_action==='abort'?' selected':''}>Aufruf abbrechen</option>
            </select>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:12px;color:var(--text-300);min-width:200px">Wenn Anonymisierung fehlschlägt</span>
            <select class="form-select" id="gdpr-bg-fail-action" style="flex:1">
              <option value="swap_to_local"${(gs.background_anonymise_fail_action||'swap_to_local')==='swap_to_local'?' selected':''}>Auf lokales Modell zurückfallen</option>
              <option value="abort"${gs.background_anonymise_fail_action==='abort'?' selected':''}>Aufruf abbrechen</option>
            </select>
          </div>
          <div style="font-size:11px;color:var(--text-400)"><b>Anonymisierung</b>: Cloud-Modell mit pseudonymisiertem Text, Antwort wird de-anonymisiert (Qualitätsverlust bei PII-dichten Texten wie Richtlinien — kann die KG-Extraktion entwerten). <b>Lokales Modell</b>: voller Text, bleibt auf dem Gerät (braucht ein konfiguriertes lokales Fallback-Modell; sonst Warn-Durchlauf aufs Originalmodell). <b>Überspringen</b>: der Aufruf wird gar nicht ausgeführt und fährt leer fort — die KG-Extraktion lässt das betroffene Dokument aus (im Quellbaum als „KG⊘" markiert) und versucht es nicht erneut; <i>kein</i> Fehler. <b>Abbrechen</b>: lehnt den Aufruf mit einem Fehler ab. Gilt für alle nicht-interaktiven Aufrufe einheitlich; der interaktive Chat fragt weiterhin pro Durchlauf.</div>
        </div>

        ${SEC('E-Mail-Allowlist')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:6px">
          Ein Eintrag pro Zeile. <code>user@example.com</code> stimmt exakt überein; <code>@example.com</code> stimmt mit jeder Adresse dieser Domain überein. Übereinstimmende E-Mails werden vollständig aus den Befunden unterdrückt.
        </div>
        <textarea id="gdpr-email-allowlist" rows="5" style="width:100%;font-family:var(--font-mono);font-size:12px;padding:8px;border:1px solid var(--border-100);border-radius:6px;background:var(--bg-000);color:var(--text-100);resize:vertical" placeholder="alexander@me.com&#10;@trusted-company.com">${esc(allowlistText)}</textarea>

        ${SEC('Kategorieaktionen')}
        <div style="font-size:11px;color:var(--text-400);margin-bottom:6px">
          Wählen Sie eine Aktion pro Kategorie. Aufklappen, um einzelne Regeln zu überschreiben. Die Schwere auf Kategorieebene ist der Standard; gesetzte Regel-Überschreibungen haben Vorrang.
        </div>
        ${catRows}

        <div style="display:flex;gap:8px;margin-top:14px;padding-top:12px;border-top:1px solid var(--border-100)">
          <button class="btn-primary" id="gdpr-save-btn" onclick="saveGdprConfig()">Alle DSGVO-Einstellungen speichern</button>
          <button class="btn-secondary" onclick="_confirmResetGdprCategories()">Kategorien auf Standard zurücksetzen</button>
        </div>
      </div>`);
      // Populate the NER pill (separate request — pill lives on its own
      // endpoint so it can be refreshed independently of saveGdprConfig).
      refreshGdprNerPill();
    } catch(e) {
      C.innerHTML = P(`<div style="color:var(--error)">DSGVO-Einstellungen konnten nicht geladen werden: ${esc(e.message||e)}</div>`);
    }
}

async function _genTab_feedback(C) {
  /* ─── FEEDBACK (admin: examine all 👍/👎 + comments) ─── */
  const SURF_LABELS = {
    chat: 'Chat', brainy: 'Brainy', workflow: 'Workflow',
    schedule: 'Aufgabe', translation: 'Übersetzung', classification: 'Klassifizierung',
  };
  // Preserve the current filter selection across re-renders.
  const surfSel = C.querySelector('#fb-filter-surface')?.value || '';
  const rateSel = C.querySelector('#fb-filter-rating')?.value || '';
  const surfOpts = ['<option value="">Alle Bereiche</option>']
    .concat(Object.entries(SURF_LABELS).map(([k, v]) =>
      `<option value="${k}" ${k === surfSel ? 'selected' : ''}>${esc(v)}</option>`)).join('');
  const rateOpts = `<option value="">👍 + 👎</option>`
    + `<option value="up" ${rateSel === 'up' ? 'selected' : ''}>Nur 👍</option>`
    + `<option value="down" ${rateSel === 'down' ? 'selected' : ''}>Nur 👎</option>`;

  let rows = [];
  try {
    rows = (await API.listFeedback(surfSel || null, rateSel || null)).feedback || [];
  } catch (e) {
    C.innerHTML = `<div style="padding:20px;color:var(--error)">Feedback konnte nicht geladen werden: ${esc(e.message || String(e))}</div>`;
    return;
  }

  const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
  const reload = `_genTab_feedback(document.getElementById('general-tab-content'))`;

  const canJump = (s) => s !== 'brainy';  // brainy has no deep-link target
  const body = rows.length
    ? rows.map(r => {
        const jump = canJump(r.surface)
          ? `<button class="fb-jump" title="Zum Inhalt springen"
               onclick="feedbackJumpTo('${esc(r.surface)}','${esc(String(r.target_id))}','${esc(r.session_id || '')}')">↗</button>`
          : `<span class="fb-jump fb-jump-disabled" title="Kein direkter Sprung verfügbar">·</span>`;
        // Inline conversation thread + admin reply box. Messages older→newer;
        // 'admin' bubbles flush-left ("Team"), 'user' flush-right.
        const thread = Array.isArray(r.thread) ? r.thread : [];
        const threadHtml = thread.length
          ? `<div class="fb-admin-thread">${thread.map(m => {
              const isAdmin = m.author_role === 'admin';
              return `<div class="fb-bubble ${isAdmin ? 'fb-bubble-them' : 'fb-bubble-mine'}">`
                + `${isAdmin ? '<span class="fb-bubble-who">Team</span>' : ''}`
                + `<span class="fb-bubble-text">${esc(m.text || '')}</span></div>`;
            }).join('')}</div>`
          : '';
        return `
        <div class="fb-admin-row">
          <span>${esc(SURF_LABELS[r.surface] || r.surface)}</span>
          <span class="${r.rating === 'down' ? 'fb-badge-down' : 'fb-badge-up'}">${r.rating === 'down' ? '👎' : '👍'}</span>
          <span>${r.comment ? esc(r.comment) : '<i style="color:var(--text-400)">— kein Kommentar —</i>'}
            ${r.context_snapshot ? `<div class="fb-snap">↳ ${esc(r.context_snapshot)}</div>` : ''}
            ${threadHtml}
            <div class="fb-admin-reply">
              <input type="text" class="fb-admin-reply-input" maxlength="300"
                     placeholder="Eine Zeile antworten… (Enter)"
                     onkeydown="if(event.key==='Enter'){event.preventDefault();feedbackAdminReply(${r.id},this);}">
              <button class="fb-admin-reply-send" onclick="feedbackAdminReply(${r.id},this.previousElementSibling)">➤</button>
            </div>
          </span>
          <span style="font-size:12px;color:var(--text-400)">${esc(r.user_name || r.user_id || '—')}</span>
          <span style="font-size:12px;color:var(--text-400)">${esc(fmtDate(r.updated_at))}</span>
          ${jump}
          <button class="fb-del" title="Löschen" onclick="API.deleteFeedback(${r.id}).then(()=>${reload})">✕</button>
        </div>`;
      }).join('')
    : `<div style="padding:24px;text-align:center;color:var(--text-400)">Noch kein Feedback in dieser Auswahl.</div>`;

  C.innerHTML = `
    <div style="padding:20px">
      <h3 style="margin:0 0 4px">Feedback</h3>
      <div style="font-size:13px;color:var(--text-400);margin-bottom:16px">
        Bewertungen der Nutzer (👍/👎 + optionaler Kommentar) zu Antworten und Ergebnissen — über alle Bereiche.
      </div>
      <div class="fb-admin-filters">
        <select class="form-select" id="fb-filter-surface" style="width:200px" onchange="${reload}">${surfOpts}</select>
        <select class="form-select" id="fb-filter-rating" style="width:160px" onchange="${reload}">${rateOpts}</select>
        <span style="font-size:12px;color:var(--text-400)">${rows.length} Einträge</span>
      </div>
      <div class="fb-admin-row fb-admin-head">
        <span>Bereich</span><span>Bew.</span><span>Kommentar / Kontext</span><span>Benutzer</span><span>Aktualisiert</span><span></span><span></span>
      </div>
      ${body}
    </div>`;
}

// Admin posts a one-line reply into a feedback thread, then re-renders the tab
// so the new bubble + bumped timestamp show. Restores the text on failure.
async function feedbackAdminReply(fbId, input) {
  const text = (input.value || '').trim();
  if (!text) return;
  input.value = '';
  try {
    await API.feedbackMessage(fbId, text);
    _genTab_feedback(document.getElementById('general-tab-content'));
  } catch (e) {
    input.value = text;
    if (typeof showToast === 'function') showToast('Antwort fehlgeschlagen', true);
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
          return `<select class="form-select" disabled style="width:160px;font-size:12px;opacity:.6" title="Streng Vertraulich blockiert immer gemäß ARL §1.11">
            <option selected>blockieren (gesperrt)</option>
          </select>`;
        }
        return `<select class="form-select cls-policy-action" data-level="${esc(level)}" style="width:160px;font-size:12px">
          <option value="ignore"      ${current==='ignore'?'selected':''}>ignorieren</option>
          <option value="warn"        ${current==='warn'?'selected':''}>warnen</option>
          <option value="force_local" ${current==='force_local'?'selected':''}>lokal erzwingen</option>
          <option value="block"       ${current==='block'?'selected':''}>blockieren</option>
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
            <button class="btn-secondary" style="font-size:11px;padding:2px 8px" onclick="clsRestoreDefaultKw('${lvl}')">Standardwerte wiederherstellen</button>
          </label>
          <textarea id="cls-kw-${lvl}" class="form-input" rows="4"
            style="font-family:inherit;font-size:12px;width:100%"
            placeholder="Ein Schlüsselwort pro Zeile">${esc((kw[lvl]||[]).join('\n'))}</textarea>
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
            placeholder="Regex-Muster" value="${esc(item.pattern||'')}">
          <button class="btn-secondary" style="font-size:11px;padding:2px 8px" onclick="this.parentElement.remove()">Entfernen</button>
        </div>`;

      C.innerHTML = `
        <div style="max-width:760px">
          <h3 style="margin:0 0 4px;font-size:16px">Dokumentenklassifizierung</h3>
          <div style="font-size:12px;color:var(--text-400);margin-bottom:18px">
            Der Detektor verwendet den Regex-Marker-Scan + PII-Signale des DSGVO-Scanners erneut.
            Phase B setzt Routing-Entscheidungen pro Stufe bei Anhang-Uploads und
            Tool-Lesevorgängen durch.
          </div>

          <h4 style="margin:18px 0 8px;font-size:13px">Richtlinie</h4>
          <div style="background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;padding:14px 16px;margin-bottom:18px">
            <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin-bottom:8px">
              <input type="checkbox" id="cls-policy-enabled" ${policy.enabled !== false ? 'checked' : ''}>
              <span><b>Scanner aktiviert</b> — wenn aus, passiert nichts (Erkennung UND Durchsetzung deaktiviert)</span>
            </label>
            <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin-bottom:8px">
              <input type="checkbox" id="cls-policy-server-block" ${policy.server_block !== false ? 'checked' : ''}>
              <span><b>Hard-Block-Master-Schalter</b> — wenn aus, werden „blockieren"-Aktionen auf „lokal erzwingen" herabgestuft. Streng Vertraulich blockiert dennoch immer.</span>
            </label>
            <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin-bottom:14px">
              <input type="checkbox" id="cls-policy-server-log" ${policy.server_log !== false ? 'checked' : ''}>
              <span><b>Server-Audit-Log</b> — <code>classification_detected/auto_fallback/blocked</code>-Ereignisse ausgeben</span>
            </label>
            <div style="margin-bottom:14px">
              <label style="font-size:11.5px;color:var(--text-400);text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:4px">
                Standard-Fallback-Modell (lokal)
              </label>
              <select class="form-select" id="cls-policy-fallback" style="width:100%;font-size:12.5px">
                <option value="">— vom DSGVO-Scanner erben —</option>
                ${localModelOpts || '<option disabled>(keine lokalen Modelle aktiviert)</option>'}
              </select>
              <div style="font-size:11px;color:var(--text-400);margin-top:4px">
                Wird verwendet, wenn eine effektive Aktion von <code>force_local</code> einen Modellwechsel erfordert.
              </div>
            </div>
            <div style="font-size:11.5px;color:var(--text-400);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Aktion pro Stufe</div>
            ${levelRow('public', 'Öffentlich (public)')}
            ${levelRow('internal', 'Intern (internal)')}
            ${levelRow('confidential', 'Vertraulich (confidential)')}
            ${levelRow('strict', 'Streng Vertraulich (strict — gesperrt, ARL §1.11)')}
            ${levelRow('unmarked', 'Unmarkiert (kein Marker erkannt)')}
          </div>

          <h4 style="margin:18px 0 8px;font-size:13px">Schlüsselwörter nach Sensibilität</h4>
          ${kwBlock('internal',     'Intern — Vorhandensein allein ist in Ordnung, aber fehlender Marker stuft auf Intern herab')}
          ${kwBlock('confidential', 'Vertraulich — meldet Unstimmigkeiten, wenn das Dokument als Öffentlich/Intern markiert ist')}
          ${kwBlock('strict',       'Streng Vertraulich — stärkstes Signal; Unstimmigkeit wird zu HOHER Schwere')}

          <h4 style="margin:24px 0 8px;font-size:13px">Zusätzliche Marker-Muster (Regex)</h4>
          <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">
            Benutzerdefinierte Regex-Muster zur Erkennung organisationsspezifischer Markierungen
            zusätzlich zum integrierten <code>Dokumentenklassifizierung … &lt;level&gt;</code>-Matcher.
          </div>
          <div id="cls-extras-box">
            ${extras.map(extraRow).join('') || '<div style="color:var(--text-400);font-size:12px">Keine zusätzlichen Muster.</div>'}
          </div>
          <button class="btn-secondary" style="margin-top:6px;font-size:12px" onclick="clsAddExtraRow()">+ Muster hinzufügen</button>

          <div style="margin-top:24px;display:flex;gap:10px">
            <button class="btn-primary" onclick="clsSaveSettings()">Speichern</button>
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
      // Per-use-case status matrix (purpose × tool → {state, tokens} + summary).
      // Drives the per-purpose dropdown strip on each tool row + the status
      // summary header. Stashed for saveTool to read the canonical purpose list.
      window._toolMatrix = settingsResp.matrix || null;
      const MATRIX = window._toolMatrix || {};
      const MX_PURPOSES = (MATRIX.purposes || settingsResp.purposes || []);
      const MX_CELLS = MATRIX.matrix || {};
      const MX_SUMMARY = MATRIX.summary || {};
      // Short labels for the compact column headers.
      const PURPOSE_LABELS = {
        interactive: 'Chat', transform: 'Transform',
        memory_summary: 'Memory', research_minimal: 'Research', helpdesk: 'Brainy',
        instruction_gen: 'Projektanweisung',
      };
      // Compact Excel-style cell <select>. Colour-coded by state so the grid
      // reads at a glance (green=active, grey=inactive, amber=deferred).
      const CELL_BG = { active: 'rgba(34,197,94,0.10)', inactive: 'var(--bg-100)', deferred: 'rgba(245,158,11,0.12)' };
      const CELL_FG = { active: 'var(--success)', inactive: 'var(--text-400)', deferred: 'var(--warning,#d97706)' };
      const stCellSelect = (toolName, purpose, cur) =>
        `<select class="tsx-cell" data-tool="${esc(toolName)}" data-purpose="${esc(purpose)}"
           title="${esc(PURPOSE_LABELS[purpose]||purpose)}: Aktiv (im Prompt) · Inaktiv (nicht in diesem Kanal / aus) · Aufgeschoben (nur über tool_search)."
           style="font-size:10px;padding:1px 1px;font-family:var(--font-mono);background:${CELL_BG[cur]||'var(--bg-100)'};color:${CELL_FG[cur]||'var(--text-100)'};border:none;border-radius:0;width:100%;text-align:center"
           onchange="saveToolPurposeCell('${esc(toolName)}')">
          <option value="active"   ${cur==='active'?'selected':''}>Aktiv</option>
          <option value="inactive" ${cur==='inactive'?'selected':''}>Inaktiv</option>
          <option value="deferred" ${cur==='deferred'?'selected':''}>Aufgesch.</option>
        </select>`;

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
      // for those. EVERY tool is in the matrix, including integration-only
      // pseudo-tools (gmail/refinement/…): they count toward the Σ totals
      // (total == active+inactive+deferred for every column) and show their
      // status per purpose like any tool. The ⚙ opens their integration config.
      const matrixTools = allTools;
      const byGroup = {};
      for (const t of matrixTools) {
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

      const WRITE_EXEC = new Set(['write_file','edit_file','execute_command','python_exec','git_command','github_command','gmail_send','gmail_reply','write_document','edit_document','delegate_task','run_background_task']);
      const NCOL = 1 + MX_PURPOSES.length + 1;  // name + purposes + tokens

      // One <tr> per tool in the single flat matrix table. The matrix table is
      // PURELY status (per-purpose cells + tokens) — the per-tool config (prose /
      // purposes / applies_with / wire schema / integration) is SEPARATE, opened
      // in a modal via the ⚙ button so the two concerns don't interleave.
      const toolTr = (t) => {
        const integ = INTEGRATION_TOOLS.has(t.name) ? sBadge(t.name) : '';
        const proseFlag = (t.description || t.when_to_use || t.warnings || t.examples)
          ? `<span title="Prompt-Text konfiguriert" style="font-size:9px;color:var(--accent)">★</span>` : '';
        const appliesFlag = (t.applies_with && t.applies_with.length)
          ? `<span title="${esc(t.applies_with.join(', '))}" style="font-size:9px;color:var(--text-400)">+${t.applies_with.length}</span>` : '';
        const tokens = toolTokens[t.name] || 0;
        const purposeCells = MX_PURPOSES.map(p => {
          const cell = (MX_CELLS[p] || {})[t.name] || {};
          const cur = cell.state || 'inactive';
          // Every cell is an editable dropdown. A tool that isn't part of this
          // channel's tool set reports as Inaktiv; setting it Aktiv/Aufgeschoben
          // pulls it into the channel (the resolver's extend pass).
          const warn = (p === 'helpdesk' && cur === 'active' && WRITE_EXEC.has(t.name))
            ? `<span title="Brainy ist read-only — dieses Schreib/Ausführen-Tool hebt das auf" style="color:var(--warning);font-size:9px;position:absolute;right:1px;top:0">⚠</span>` : '';
          return `<td style="padding:0;border:1px solid var(--border-100);position:relative">${warn}${stCellSelect(t.name, p, cur)}</td>`;
        }).join('');
        return `
          <tr class="tsx-tool-row" data-tool="${esc(t.name)}">
            <td style="padding:3px 8px;border:1px solid var(--border-100);white-space:nowrap;background:var(--bg-100)">
              <span style="font-family:var(--font-mono);font-size:11px;color:${t.enabled?'var(--text-100)':'var(--text-400)'}">${esc(t.name)}</span>
              ${proseFlag}${appliesFlag}${integ}
              <span style="float:right;cursor:pointer;font-size:11px;color:var(--text-400)" title="Tool-Konfiguration (Prompt-Text, Zwecke, Integration, Wire-Schema)" onclick="openToolDetailModal('${esc(t.name)}')">⚙</span>
            </td>
            ${purposeCells}
            <td style="padding:3px 8px;border:1px solid var(--border-100);text-align:right;font-family:var(--font-mono);font-size:10px;color:var(--text-400)">${tokens||''}</td>
          </tr>`;
      };

      // Thin non-collapsible group separator row spanning the whole table.
      const groupSepTr = (gName, count) => `
        <tr><td colspan="${NCOL}" style="padding:3px 8px;background:var(--bg-200);border:1px solid var(--border-100);font-size:10px;font-weight:600;color:var(--text-300);text-transform:uppercase;letter-spacing:0.05em">${esc(gName)} <span style="color:var(--text-400);font-weight:400">· ${count}</span></td></tr>`;

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
          <span style="font-family:var(--font-mono);color:var(--text-400);min-width:60px;text-align:right">${n} Tok</span>
        </div>`;
      };

      // Research-mode disciplines section — three textareas + reset
      // buttons. Renders only when the GET succeeded.
      let rmdHTML = '';
      if (rmdResp && rmdResp.sections) {
        const sectionLabels = {
          refusal:   'Ablehnungs-Disziplin',
          precision: 'Präzisions-Disziplin',
          citation:  'Zitations-Disziplin',
        };
        const sectionTextarea = (k) => {
          const cur = rmdResp.sections[k] || '';
          const isDefault = cur === (rmdResp.defaults[k] || '');
          return `
            <div style="margin-bottom:12px">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <span style="font-size:12px;font-weight:600;color:var(--text-100)">${esc(sectionLabels[k] || k)}</span>
                <span style="font-size:10px;color:var(--text-400)">${isDefault ? '(Standard)' : '(benutzerdefiniert)'}</span>
                <button class="btn-secondary" style="font-size:10px;padding:2px 8px;margin-left:auto"
                        onclick="resetResearchModeDiscipline('${esc(k)}')" title="Werkseinstellung für diesen Abschnitt wiederherstellen">Zurücksetzen</button>
              </div>
              <textarea id="rmd-${esc(k)}" rows="6" class="form-input"
                style="width:100%;font-family:var(--font-mono);font-size:11px;resize:vertical">${esc(cur)}</textarea>
            </div>`;
        };
        rmdHTML = `
          <div style="border:1px solid var(--border-100);border-radius:8px;padding:14px;margin-bottom:14px;background:var(--bg-100)">
            <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px">
              <div style="font-size:12px;font-weight:600;color:var(--text-100)">Research-Modus-Disziplinen</div>
              <div style="font-size:11px;color:var(--text-400)">werden für Projekt-Chats mit research_mode=on in den System-Prompt eingefügt</div>
            </div>
            <div style="font-size:10px;color:var(--text-400);margin-bottom:10px">
              Bearbeiten Sie jeden Abschnitt unten; leeren Sie einen Abschnitt, um ihn vollständig aus dem Prompt zu entfernen. Pro-Tool-Retrieval-Hinweise (Suche zuerst, Abfragedisziplin, der 3-Schritte-Ablauf) befinden sich weiter unten in den Tool-Beschreibungen — diese drei Abschnitte betreffen nur die Ausgabehaltung.
            </div>
            ${(rmdResp.section_order || ['refusal','precision','citation']).map(sectionTextarea).join('')}
            <div style="display:flex;justify-content:flex-end;gap:8px">
              <button class="btn-primary" onclick="saveResearchModeDisciplines()" style="padding:6px 14px;font-size:12px">Disziplinen speichern</button>
            </div>
          </div>`;
      }

      // Header row: Tool · one column per use-case.
      const headTh = (label, extra) =>
        `<th style="padding:4px 8px;border:1px solid var(--border-100);background:var(--bg-200);font-size:10px;font-weight:600;color:var(--text-300);text-transform:uppercase;letter-spacing:0.04em;position:sticky;top:0;z-index:2;${extra||''}">${label}</th>`;
      const headerRow = `<tr>
        ${headTh('Tool', 'text-align:left')}
        ${MX_PURPOSES.map(p => headTh(esc(PURPOSE_LABELS[p]||p))).join('')}
      </tr>`;

      // Summary LAST ROWS: per-purpose active/inactive/deferred counts + the
      // realized token size of the tool injection on that channel (the Σ row is
      // where the per-channel token total lives — not a per-tool column).
      const sumCell = (p) => {
        const s = MX_SUMMARY[p] || {};
        return `<td style="padding:3px 4px;border:1px solid var(--border-100);text-align:center;font-family:var(--font-mono);font-size:10px;background:var(--bg-100)">
          <div><span style="color:var(--success)">${s.active||0}</span>·<span style="color:var(--text-400)">${s.inactive||0}</span>·<span style="color:var(--warning,#d97706)">${s.deferred||0}</span></div>
          <div style="color:var(--text-100);font-weight:600">${(s.tokens||0).toLocaleString()} Tok</div>
        </td>`;
      };
      const summaryRows = `
        <tr>
          <td style="padding:4px 8px;border:1px solid var(--border-100);background:var(--bg-200);font-size:10px;font-weight:600;color:var(--text-300);text-transform:uppercase;letter-spacing:0.04em">Σ aktiv·inaktiv·aufg. / Token</td>
          ${MX_PURPOSES.map(sumCell).join('')}
        </tr>
        <tr>
          <td colspan="${NCOL}" style="padding:3px 8px;border:1px solid var(--border-100);background:var(--bg-100);font-size:9px;color:var(--text-400)">
            Σ-Zeile: <span style="color:var(--success)">aktiv</span>·<span style="color:var(--text-400)">inaktiv</span>·<span style="color:var(--warning,#d97706)">aufgeschoben</span> je Kanal — die Summe ergibt IMMER die Gesamtzahl aller ${matrixTools.length} Tools. Dahinter die realisierte Token-Größe der Tool-Injektion (aktiv = volles Schema, aufgeschoben = nur Name, inaktiv = 0).
          </td>
        </tr>`;

      // Body: all tools in ONE table, with a thin non-collapsible separator row
      // before each group. Single flat grid (Excel-style).
      const bodyRows = groupOrder.map(g =>
        groupSepTr(g, byGroup[g].length) + byGroup[g].map(toolTr).join('')
      ).join('');

      const matrixTable = `
        <div style="overflow:auto;border:1px solid var(--border-100);border-radius:6px;max-height:64vh">
          <table style="border-collapse:collapse;width:100%;table-layout:fixed">
            <colgroup>
              <col style="width:240px">
              ${MX_PURPOSES.map(()=>'<col>').join('')}
            </colgroup>
            <thead>${headerRow}</thead>
            <tbody>${bodyRows}${summaryRows}</tbody>
          </table>
        </div>`;

      C.innerHTML = P(`<div>
        <div style="font-size:11px;color:var(--text-400);margin-bottom:10px">
          ${matrixTools.length} Tools, Status je Anwendungsfall (Chat · Transform · Memory · Research · Brainy). Klicken Sie auf das <b>⚙</b> eines Tools, um Zwecke, Integration und Prompt-Text zu bearbeiten. Setzen Sie eine Zelle auf <b>Aktiv</b>/<b>Aufgeschoben</b>, um das Tool diesem Kanal hinzuzufügen, oder <b>Inaktiv</b>, um es zu entfernen — die Tabelle ist die alleinige Quelle dafür, welche Tools ein Kanal sieht. Letzte Zeile = Σ je Kanal: <b>aktiv·inaktiv·aufgeschoben</b> (Summe = ${matrixTools.length}) + realisierte Token-Größe. Änderungen werden beim Auswählen sofort gespeichert.
        </div>

        ${rmdHTML}

        ${matrixTable}
      </div>`);
      return;
    } catch(e) {
      C.innerHTML = P('<div style="color:var(--error)">Tool-Einstellungen konnten nicht geladen werden: ' + esc(e.message || String(e)) + '</div>');
      return;
    }
}

async function _genTab_helpdesk(C) {
  /* ─── BRAINY (Helpdesk-Bot) ─── */
  try {
    const cfg = await API.get('/v1/helpdesk/config');
    const enabledModels = enabledModelsWithCapability('chat');
    const modelOpts = enabledModels.map(([mid]) =>
      modelOption(mid, { selected: mid === (cfg.model || '') })).join('');
    const rounds = cfg.max_rounds || 6;

    C.innerHTML = P(`<div style="${G('14px')}">
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:26px">🧠</span>
        <div>
          <div style="font-size:15px;font-weight:600;color:var(--text-000)">Brainy — der Helpdesk-Bot</div>
          <div style="font-size:12px;color:var(--text-400)">Freundlicher Helfer im Chat. Kennt brain-agent, die aktuelle Sitzung und den Nutzer. Rein lesend.</div>
        </div>
      </div>

      ${SEC('Status')}
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-100)">
        <input type="checkbox" id="hd-enabled" ${cfg.enabled ? 'checked' : ''}>
        Brainy aktiviert (Buddy + Hilfe-Button im Chat)
      </label>

      ${SEC('Modell')}
      <select class="form-select" id="hd-model" style="width:100%">
        <option value="" ${!cfg.model ? 'selected' : ''}>Auto (Server-Standardmodell verwenden)</option>
        ${modelOpts}
      </select>
      <div style="font-size:11px;color:var(--text-400)">Eigenes Modell für Brainy. Auf Auto greift das Server-Standardmodell.${cfg.resolved_model ? ' Aktuell genutzt: <code>' + esc(cfg.resolved_model) + '</code>' : ''}</div>

      ${SEC('Tool-Runden')}
      <input type="number" class="form-input" id="hd-max-rounds" min="1" max="12" value="${rounds}" style="width:120px">
      <div style="font-size:11px;color:var(--text-400)">Wie viele Tool-Runden Brainy pro Frage nutzen darf (1–12). Höher = gründlicher, aber langsamer.</div>

      ${SEC('System-Prompt (Persönlichkeit)')}
      <textarea class="form-input" id="hd-prompt" rows="12" style="width:100%;font-family:var(--font-mono);font-size:12px;line-height:1.5">${esc(cfg.system_prompt || '')}</textarea>
      <div style="font-size:11px;color:var(--text-400)">Bestimmt, wie sich Brainy verhält. Anders als der Haupt-Agent: ein freundlicher, kompetenter Helpdesk-Mitarbeiter.</div>

      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:8px">
        <button class="btn-primary" onclick="_saveHelpdeskConfig()">Speichern</button>
      </div>
    </div>`);
  } catch (e) {
    C.innerHTML = P('<div style="color:var(--error)">Brainy-Einstellungen konnten nicht geladen werden: ' + esc(e.message || String(e)) + '</div>');
  }
}

async function _saveHelpdeskConfig() {
  const body = {
    enabled: document.getElementById('hd-enabled').checked,
    model: document.getElementById('hd-model').value,
    max_rounds: parseInt(document.getElementById('hd-max-rounds').value, 10) || 6,
    system_prompt: document.getElementById('hd-prompt').value,
  };
  try {
    await API.post('/v1/helpdesk/config', body);
    showToast('Brainy-Einstellungen gespeichert');
  } catch (e) {
    showToast('Speichern fehlgeschlagen', true);
  }
}

// ─── DOCTOR: config-health diagnostics (model/provider integrity, MemPalace,
// KG, provider reachability). Static by default; live probes on demand. ───
function _doctorRenderFindings(findings, summary) {
  const COLOR = { ok: 'var(--success)', warn: '#d9a000', fail: 'var(--error)' };
  const ICON = { ok: '✓', warn: '!', fail: '✕' };
  const s = summary || { overall: 'ok', counts: { ok: 0, warn: 0, fail: 0 } };
  const head = `<div style="display:flex;gap:10px;align-items:center;margin-bottom:14px">
    <span style="font-size:20px;font-weight:700;color:${COLOR[s.overall]}">${ICON[s.overall]} ${esc((s.overall || '').toUpperCase())}</span>
    <span style="font-size:12px;color:var(--text-400)">
      ${s.counts.ok} ok · ${s.counts.warn} Warnung · ${s.counts.fail} Fehler</span>
  </div>`;
  // sort fail → warn → ok so problems surface first
  const order = { fail: 0, warn: 1, ok: 2 };
  const sorted = [...(findings || [])].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
  const rows = sorted.map(f => {
    const c = COLOR[f.status] || 'var(--text-400)';
    const detail = f.detail ? `<div style="font-size:12px;color:var(--text-300);margin-top:3px">${esc(f.detail)}</div>` : '';
    const fix = (f.status !== 'ok' && f.fix)
      ? `<div style="font-size:12px;color:var(--text-400);margin-top:3px">→ ${esc(f.fix)}</div>` : '';
    return `<div style="padding:10px 12px;border-left:3px solid ${c};background:var(--bg-200);border-radius:6px;margin-bottom:8px">
      <div style="display:flex;gap:8px;align-items:baseline">
        <span style="color:${c};font-weight:700;font-family:monospace">${ICON[f.status] || '·'}</span>
        <span style="font-weight:600">${esc(f.title || f.check || '')}</span>
        <span style="margin-left:auto;font-size:10px;color:var(--text-500);font-family:monospace">${esc(f.check || '')}</span>
      </div>${detail}${fix}</div>`;
  }).join('');
  return head + (rows || '<div style="color:var(--text-400)">Keine Befunde.</div>');
}

async function _genTab_doctor(C) {
  C.innerHTML = `<div style="padding:16px;max-width:820px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <h3 style="margin:0">System-Doctor</h3>
      <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="_doctorRun(false)">Erneut prüfen</button>
      <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="_doctorRun(true)">Live-Prüfungen (langsamer)</button>
    </div>
    <p style="font-size:12px;color:var(--text-400);margin:0 0 14px">
      Erkennt Fehlkonfigurationen, die sonst still scheitern: Modelle/Konfig-Verweise auf
      nicht existierende Provider oder deaktivierte Modelle, Provider-Lücken, MemPalace-Zustand
      (Backend, Embedding-Gerät, Drawer-Zahl) und fehlschlagende KG-Extraktion.</p>
    <div id="doctor-results"><div style="color:var(--text-400)">Lädt…</div></div>
  </div>`;
  await _doctorRun(false);
}

async function _doctorRun(live) {
  const box = document.getElementById('doctor-results');
  if (!box) return;
  box.innerHTML = `<div style="color:var(--text-400)">${live ? 'Live-Prüfungen laufen…' : 'Prüfe…'}</div>`;
  try {
    const d = live ? await API.post('/v1/doctor/live', {}) : await API.get('/v1/doctor');
    box.innerHTML = _doctorRenderFindings(d.findings, d.summary);
  } catch (e) {
    box.innerHTML = `<div style="color:var(--error)">Doctor-Prüfung fehlgeschlagen: ${esc(e.message || String(e))}</div>`;
  }
}

// ─── Bibliotheken — installed versions of the external libs Brain depends on ──
// Read-only. Backend probes four venvs (server-python + mempalace + .venv_sdk +
// .venv_crawl4ai); "Aktualisiert" = local pip-install date (RECORD mtime), not a
// live PyPI lookup.
async function _genTab_libraries(C) {
  C.innerHTML = `<div style="padding:16px;max-width:820px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <h3 style="margin:0">Bibliotheken</h3>
      <button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="_libVersionsRun()">Neu laden</button>
    </div>
    <p style="font-size:12px;color:var(--text-400);margin:0 0 14px">
      Installierte Versionen der externen Bibliotheken über alle vier Python-Umgebungen
      (Server-Python, MemPalace-venv, <span style="${MONO}">.venv_sdk</span>,
      <span style="${MONO}">.venv_crawl4ai</span>). „Aktualisiert“ ist das lokale
      Installationsdatum (kein Live-Abgleich mit PyPI).</p>
    <div id="lib-versions-results"><div style="color:var(--text-400)">Lädt…</div></div>
  </div>`;
  await _libVersionsRun();
}

async function _libVersionsRun() {
  const box = document.getElementById('lib-versions-results');
  if (!box) return;
  box.innerHTML = `<div style="color:var(--text-400)">Prüfe…</div>`;
  try {
    const d = await API.get('/v1/lib-versions');
    box.innerHTML = _libVersionsRender(d);
  } catch (e) {
    box.innerHTML = `<div style="color:var(--error)">Versionsabfrage fehlgeschlagen: ${esc(e.message || String(e))}</div>`;
  }
}

function _libVersionsRender(d) {
  const head = `<div style="font-size:11px;color:var(--text-500);margin-bottom:12px">
    Server-Python ${esc(d.python || '?')} · ${esc(d.platform || '')}</div>`;
  const groups = (d.groups || []).map(g => {
    const rows = (g.libs || []).map(l => {
      const ok = l.status === 'ok' && l.version;
      const ver = ok
        ? `<span style="${MONO};color:var(--text-200)">${esc(l.version)}</span>`
        : `<span style="font-size:11px;color:var(--error)" title="${esc(l.status || '')}">${esc(l.status === 'missing' ? 'nicht installiert' : (l.status || 'unbekannt'))}</span>`;
      const when = l.installed
        ? `<span style="font-size:11px;color:var(--text-400)">${esc(l.installed)}</span>`
        : `<span style="font-size:11px;color:var(--text-500)">—</span>`;
      return `<div style="display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center;padding:6px 12px;border-top:1px solid var(--border-100)">
        <span style="font-size:13px;color:var(--text-100)">${esc(l.name)}</span>
        ${ver}
        ${when}
      </div>`;
    }).join('');
    return `<div style="border:1px solid var(--border-100);border-radius:8px;margin-bottom:12px;overflow:hidden">
      <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-200)">
        <span style="font-size:13px;font-weight:600;color:var(--text-100);flex:1">${esc(g.title)}</span>
        ${BADGE(g.source)}
      </div>
      <div style="display:grid;grid-template-columns:1fr auto auto;gap:12px;padding:4px 12px 2px;font-size:10px;color:var(--text-500);text-transform:uppercase;letter-spacing:.04em">
        <span>Bibliothek</span><span>Version</span><span>Aktualisiert</span>
      </div>
      ${rows}
    </div>`;
  }).join('');
  return head + groups;
}

// ─── Service Models — one editable home for every service-model slot ───
// Slots live across config.json + tools_config.json; this tab is the unified
// editor. Fail-loud: an unset slot shows a red 'nicht konfiguriert' pill (the
// server rejects unknown ids on save, and the Doctor flags unset/broken refs).
const _SVCMODEL_STATUS = {
  ok:      { c: 'var(--success)', t: 'OK' },
  unset:   { c: 'var(--error)',   t: 'nicht konfiguriert' },
  missing: { c: 'var(--error)',   t: 'fehlt' },
  disabled:{ c: '#d9a000',        t: 'deaktiviert' },
  off:     { c: 'var(--text-500)', t: 'aus' },
};

function _svcModelPill(status, why) {
  const s = _SVCMODEL_STATUS[status] || _SVCMODEL_STATUS.unset;
  const tip = why ? ` title="${esc(why)}"` : '';
  return `<span${tip} style="font-size:10px;padding:2px 7px;border-radius:4px;border:1px solid ${s.c};color:${s.c};white-space:nowrap">${esc(s.t)}</span>`;
}

function _svcModelSelect(id, value, options, status) {
  // '' option first (explicit unset = fail-loud), then enabled models.
  const unsetSel = value ? '' : ' selected';
  const opts = `<option value=""${unsetSel}>— nicht konfiguriert —</option>`
    + options.map(m => {
        const sel = m.id === value ? ' selected' : '';
        const tag = m.is_local ? ' [lokal]' : '';
        return `<option value="${esc(m.id)}"${sel}>${esc(m.display)}${tag}</option>`;
      }).join('');
  // If the saved value isn't in the enabled list, keep it visible (legacy/missing).
  const known = !value || options.some(m => m.id === value);
  const legacy = known ? '' : `<option value="${esc(value)}" selected>${esc(value)} (fehlt/deaktiviert)</option>`;
  return `<select class="form-select" id="${id}">${legacy}${opts}</select>`;
}

async function _genTab_service_models(C) {
  const isAdmin = state.authUser && state.authUser.role === 'admin';
  let d;
  try {
    d = await API.get('/v1/services/models');
  } catch (e) {
    C.innerHTML = P(`<div style="color:var(--error)">Service-Modelle konnten nicht geladen werden: ${esc(e.message || e)}</div>`);
    return;
  }
  const opts = d.model_options || [];
  const dis = isAdmin ? '' : ' disabled';

  // Remember every backend-declared slot key so saveServiceModels() persists
  // ALL of them generically (no hardcoded list to fall behind when a new slot
  // like classifier_model / next_prompt_model is added server-side).
  window._svcSlotKeys = (d.slots || []).map(s => s.key);
  const slotRows = (d.slots || []).map(s => {
    // capability hint for which models are appropriate (informational only).
    const capHint = s.capability ? ` <span style="${MONO}">benötigt: ${esc(s.capability)}</span>` : '';
    return `<div style="display:grid;grid-template-columns:200px 1fr auto;gap:10px;align-items:center">
      <label style="font-size:12px;color:var(--text-300)">${esc(s.label)}${capHint}</label>
      <div${dis ? ' style="opacity:.6;pointer-events:none"' : ''}>${_svcModelSelect('svc-' + s.key, s.value, opts, s.status)}</div>
      <span id="svc-pill-${s.key}">${_svcModelPill(s.status, s.why)}</span>
    </div>`;
  }).join('');

  const ocr = d.ocr || { engine: 'none', provider: '', model: '', status: 'off' };
  const engineOpts = ['none', 'mistral_ocr', 'local_vision', 'auto'].map(e =>
    `<option value="${e}"${e === ocr.engine ? ' selected' : ''}>${e}</option>`).join('');
  const provOpts = '<option value="">— —</option>' + (d.providers || []).map(p =>
    `<option value="${esc(p)}"${p === ocr.provider ? ' selected' : ''}>${esc(p)}</option>`).join('');

  C.innerHTML = P(`<div style="${G('16px')}">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:14px;font-weight:500;color:var(--text-100)">Service-Modelle</span>
      <span style="${MONO}">zentrale Modellzuordnung für Hintergrunddienste</span>
    </div>
    <p style="font-size:12px;color:var(--text-400);margin:0">
      Jeder Dienst nutzt ausschließlich das hier zugewiesene Modell — es gibt <b>keine fest verdrahteten
      Standardwerte</b>. Ein nicht zugewiesener Slot ist ein Fehler (rot) und wird auch vom System-Doctor
      gemeldet. Werte werden in <code>config.json</code> bzw. <code>tools_config.json</code> gespeichert.</p>

    ${SEC('Modellzuweisungen')}
    <div style="${G('12px')};padding:12px;border:1px solid var(--border-100);border-radius:8px">
      ${slotRows}
    </div>

    ${SEC('OCR (gescannte PDFs)')}
    <div style="${G('10px')};padding:12px;border:1px solid var(--border-100);border-radius:8px">
      <div style="display:grid;grid-template-columns:200px 1fr auto;gap:10px;align-items:center">
        <label style="font-size:12px;color:var(--text-300)">Engine</label>
        <select class="form-select" id="svc-ocr-engine"${dis} onchange="_svcOcrEngineToggle()">${engineOpts}</select>
        <span id="svc-pill-ocr">${_svcModelPill(ocr.status, ocr.why)}</span>
      </div>
      <div id="svc-ocr-cloud" style="${G('10px')}">
        <div style="display:grid;grid-template-columns:200px 1fr;gap:10px;align-items:center">
          <label style="font-size:12px;color:var(--text-300)">Provider</label>
          <select class="form-select" id="svc-ocr-provider"${dis}>${provOpts}</select>
        </div>
        <div style="display:grid;grid-template-columns:200px 1fr;gap:10px;align-items:center">
          <label style="font-size:12px;color:var(--text-300)">Modell</label>
          <input type="text" class="form-input" id="svc-ocr-model" value="${esc(ocr.model || '')}" placeholder="z.B. mistral-ocr-latest"${dis}>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-left:210px;margin-top:-4px">
          <b>none</b>: OCR aus. <b>mistral_ocr</b>: Cloud-OCR-Endpoint. <b>local_vision</b>: lokales Vision-LLM.
          <b>auto</b>: Cloud zuerst, bei Fehler/PII lokal.</div>
      </div>
      <div id="svc-ocr-local" style="${G('10px')}">
        <div style="display:grid;grid-template-columns:200px 1fr;gap:10px;align-items:center">
          <label style="font-size:12px;color:var(--text-300)">Lokales Vision-Modell</label>
          <input type="text" class="form-input" id="svc-ocr-local-vision-model" value="${esc(ocr.local_vision_model || '')}" placeholder="z.B. gemma-4-26B-A4B-it-MLX-4bit"${dis}>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-left:210px;margin-top:-4px">
          Genutzt bei Engine <b>local_vision</b> oder als lokaler Fallback bei <b>auto</b>.</div>
      </div>
    </div>

    ${_svcConversionMatrix(d.conversion, dis)}

    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn-primary" id="svc-save-btn" onclick="saveServiceModels()"${dis}>${isAdmin ? 'Service-Modelle speichern' : 'Nur für Administratoren'}</button>
    </div>
  </div>`);
  _svcOcrEngineToggle();
}

// Conversion matrix: per file type, markitdown-first vs. Brain's own extractor
// (read_document + mining use this). PDF has its own 3-way engine dropdown
// (pymupdf4llm / markitdown / fitz) rendered separately, so it's excluded here.
function _svcConversionMatrix(conv, dis) {
  conv = conv || {};
  const matrix = conv.matrix || [];
  const mdAvail = conv.markitdown_available;
  const rows = matrix.filter(m => m.ext !== '.pdf').map(m => {
    const own = m.own_extractor ? `eigen (${esc(m.own_extractor)})` : 'eigen';
    return `<div style="display:grid;grid-template-columns:90px 1fr;gap:10px;align-items:center;padding:3px 0">
        <code style="font-size:12px">${esc(m.ext)}</code>
        <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-300)">
          <input type="checkbox" class="svc-conv-md" data-ext="${esc(m.ext)}" ${m.markitdown ? 'checked' : ''}${dis}>
          markitdown zuerst <span style="color:var(--text-400)">— sonst ${own}</span>
        </label>
      </div>`;
  }).join('');
  const mdWarn = mdAvail ? '' : '<div style="font-size:11px;color:var(--warning,#c80)">⚠ markitdown ist nicht auf dem PATH — überall läuft der eigene Extractor.</div>';
  const pe = conv.pdf_engine || 'pymupdf4llm';
  const peOpt = (v, label) => `<option value="${v}"${pe === v ? ' selected' : ''}>${label}</option>`;
  const pdfEngineRow = `
    <div style="display:grid;grid-template-columns:90px 1fr;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border-100)">
      <code style="font-size:12px">.pdf</code>
      <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-300)">
        Engine:
        <select class="form-select" id="svc-conv-pdf-engine"${dis} style="max-width:220px">
          ${peOpt('pymupdf4llm', 'pymupdf4llm (beste Tabellen)')}
          ${peOpt('markitdown', 'markitdown')}
          ${peOpt('fitz', 'fitz (roh, ohne Tabellen)')}
        </select>
      </label>
    </div>`;
  return `
    <h4 style="margin:18px 0 6px;font-size:13px">Dokumentkonvertierung (read_document & Mining)</h4>
    <div style="display:flex;flex-direction:column;gap:2px;padding:12px;border:1px solid var(--border-100);border-radius:8px">
      <p style="font-size:12px;color:var(--text-400);margin:0 0 8px">
        Pro Dateityp: <b>markitdown</b> (guter Text, schwach bei Tabellen) oder Brains
        <b>eigener Extractor</b>. PDF hat eine eigene Engine-Wahl (pymupdf4llm rendert
        Tabellen/Layout am besten). .xlsx/.eml laufen bewusst über eigenen Code; .epub/.zip
        immer markitdown (kein eigener Extractor).</p>
      ${mdWarn}
      ${pdfEngineRow}
      ${rows.replace(/<div[^>]*>\s*<code[^>]*>\.pdf<\/code>[\s\S]*?<\/div>\s*<\/div>/, '')}
      <p style="font-size:11px;color:var(--text-400);margin:8px 0 0">
        read_document gibt den extrahierten Inhalt vollständig (ungekappt) an das
        Modell — die einzige Grenze ist das Kontextfenster des Modells. Die Wahl des
        Extractors bestimmt die Qualität (Tabellen!).</p>
    </div>`;
}

function _svcOcrEngineToggle() {
  const eng = document.getElementById('svc-ocr-engine');
  const cloud = document.getElementById('svc-ocr-cloud');
  if (!eng || !cloud) return;
  // provider/model only relevant for cloud OCR engines; local-vision model
  // for the local_vision / auto engines.
  cloud.style.display = (eng.value === 'mistral_ocr' || eng.value === 'auto') ? '' : 'none';
  const local = document.getElementById('svc-ocr-local');
  if (local) local.style.display = (eng.value === 'local_vision' || eng.value === 'auto') ? '' : 'none';
}

// Save the new-chat composer defaults (Server tab → Eingabefeld-Standards).
// thinking_level + caveman_mode persist to config.json composer_defaults;
// memory_mode writes through to the classifier default_mode. Updates
// state.composerDefaults so subsequent new chats pick them up without a reload.
async function saveComposerDefaults() {
  const body = {
    thinking_level: document.getElementById('cd-thinking')?.value || 'none',
    caveman_mode: parseInt(document.getElementById('cd-caveman')?.value) || 0,
    memory_mode: parseInt(document.getElementById('cd-memory')?.value) || 0,
  };
  try {
    await API.post('/v1/composer/defaults', body);
    state.composerDefaults = {
      thinking_level: body.thinking_level,
      caveman_mode: body.caveman_mode,
      memory_mode: body.memory_mode,
    };
    // Keep the memory classifier mirror in sync so anything reading it agrees.
    if (state.mempalaceClassifier) state.mempalaceClassifier.default_mode = body.memory_mode;
    showToast('Eingabefeld-Standards gespeichert');
  } catch (e) {
    showToast('Speichern fehlgeschlagen: ' + (e.message || e), true);
  }
}

async function saveServiceModels() {
  const body = {};
  // Persist EVERY slot the backend declared (window._svcSlotKeys, set at render),
  // not a hardcoded subset — so newly-added slots save without a frontend edit.
  (window._svcSlotKeys || ['default_model', 'chat_summary_model', 'background_task_model',
   'kg_extraction_model', 'tts_model', 'transcribe_model']).forEach(k => {
    const el = document.getElementById('svc-' + k);
    if (el) body[k] = el.value || '';
  });
  body.ocr = {
    engine: document.getElementById('svc-ocr-engine')?.value || 'none',
    provider: document.getElementById('svc-ocr-provider')?.value || '',
    model: document.getElementById('svc-ocr-model')?.value || '',
    local_vision_model: document.getElementById('svc-ocr-local-vision-model')?.value || '',
  };
  // Conversion matrix: which extensions are markitdown-first + the budget knobs.
  const mdExts = Array.from(document.querySelectorAll('.svc-conv-md:checked'))
    .map(cb => cb.dataset.ext);
  body.conversion = {
    markitdown_exts: mdExts,
    pdf_engine: document.getElementById('svc-conv-pdf-engine')?.value || 'pymupdf4llm',
  };
  try {
    await API.post('/v1/services/models', body);
    showToast('Service-Modelle gespeichert');
    // re-render to refresh status pills.
    const C = document.getElementById('general-tab-content');
    if (C) await _genTab_service_models(C);
  } catch (e) {
    showToast('Speichern fehlgeschlagen: ' + (e.message || e), true);
  }
}

// ─── Document Styles ─── editable per-format style presets (fonts/colors/
// layout) applied deterministically by write_document/render_diagram. Files in
// agents/main/skills/doc-styles/*.yaml. Admin-only.
async function _genTab_doc_styles(C) {
  const isAdmin = state.authUser && state.authUser.role === 'admin';
  let d;
  try {
    d = await API.get('/v1/doc-styles');
  } catch (e) {
    C.innerHTML = `<div style="padding:16px;color:var(--error)">${esc(e.message || e)}</div>`;
    return;
  }
  state._docStyles = d;
  const presets = d.presets || [];
  const rows = presets.length ? presets.map(p =>
    `<div style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border-100);border-radius:8px">
       <code style="font-size:13px;font-weight:600">${esc(p.name)}</code>
       <span style="flex:1;font-size:12px;color:var(--text-400)">${esc(p.description || '')}</span>
       <button class="btn-secondary" style="padding:3px 10px;font-size:12px" onclick="docStyleEdit('${esc(p.name)}')">Bearbeiten</button>
       ${isAdmin ? `<button class="btn-secondary" style="padding:3px 10px;font-size:12px;color:var(--error)" onclick="docStyleDelete('${esc(p.name)}')">Löschen</button>` : ''}
     </div>`).join('') :
    '<div style="font-size:13px;color:var(--text-400);padding:8px 0">Noch keine Stile.</div>';
  C.innerHTML = `
    <h3 style="margin:0 0 4px">Dokument-Stile</h3>
    <p style="font-size:12px;color:var(--text-400);margin:0 0 12px">
      Stil-Vorlagen (Schriften, Farben, Layout) für erzeugte <b>.docx / .pptx / .pdf</b>-Dateien
      und Diagramme. Werden vom Tool <b>deterministisch</b> angewandt, wenn ein Dokument mit
      <code>style="&lt;name&gt;"</code> erstellt wird — das Modell schreibt nur den Inhalt.</p>
    <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:14px">${rows}</div>
    ${isAdmin ? `<button class="btn-primary" style="padding:5px 14px;font-size:13px" onclick="docStyleNew()">+ Neuer Stil</button>` : '<p style="font-size:12px;color:var(--text-400)">Nur Administratoren können Stile bearbeiten.</p>'}
    <div id="doc-style-editor" style="margin-top:16px"></div>`;
}

// ── WYSIWYG form editor for doc-style presets ──────────────────────────────
// The storage format stays YAML (what _load_doc_style in file_tools.py reads);
// this form just builds that YAML deterministically from typed fields + shows a
// live preview. Field spec mirrors _DEFAULT_DOC_STYLE (file_tools.py): grouped
// by section, each field has a key path, a widget type, and an optional choice
// list. Keep in sync with the Python default shape.
const _DOC_STYLE_FIELDS = [
  { group: 'Schriften', icon: '🔤', fields: [
    { path: 'fonts.body',    label: 'Fließtext',   type: 'font' },
    { path: 'fonts.heading', label: 'Überschrift', type: 'font' },
    { path: 'fonts.mono',    label: 'Monospace',   type: 'font' },
  ]},
  { group: 'Schriftgrößen (pt)', icon: '📏', fields: [
    { path: 'sizes.body', label: 'Fließtext', type: 'num', min: 6, max: 32 },
    { path: 'sizes.h1',   label: 'H1',        type: 'num', min: 8, max: 60 },
    { path: 'sizes.h2',   label: 'H2',        type: 'num', min: 8, max: 48 },
    { path: 'sizes.h3',   label: 'H3',        type: 'num', min: 8, max: 40 },
  ]},
  { group: 'Farben', icon: '🎨', fields: [
    { path: 'colors.heading',           label: 'Überschrift',          type: 'color' },
    { path: 'colors.body',              label: 'Fließtext',            type: 'color' },
    { path: 'colors.accent',            label: 'Akzent / Links',       type: 'color' },
    { path: 'colors.table_header_bg',   label: 'Tabellenkopf (Füllung)', type: 'color' },
    { path: 'colors.table_header_text', label: 'Tabellenkopf (Text)',  type: 'color' },
  ]},
  { group: 'Word (.docx)', icon: '📄', fields: [
    { path: 'docx.table_style',  label: 'Tabellenstil', type: 'text', ph: 'Light Grid Accent 1' },
    { path: 'docx.heading_bold', label: 'Überschriften fett', type: 'bool' },
  ]},
  { group: 'PDF', icon: '📕', fields: [
    { path: 'pdf.page_size',   label: 'Seitengröße', type: 'choice', choices: ['letter', 'a4'] },
    { path: 'pdf.margin_inch', label: 'Rand (inch)', type: 'num', min: 0, max: 4, step: 0.25 },
  ]},
  { group: 'PowerPoint (.pptx)', icon: '📊', fields: [
    { path: 'pptx.title_color', label: 'Titelfarbe', type: 'color' },
    { path: 'pptx.body_color',  label: 'Textfarbe',  type: 'color' },
    { path: 'pptx.accent',      label: 'Akzent',     type: 'color' },
    { path: 'pptx.background',  label: 'Hintergrund', type: 'color' },
  ]},
  { group: 'Diagramme (Mermaid)', icon: '📈', fields: [
    { path: 'mermaid.theme',      label: 'Theme',       type: 'choice', choices: ['default', 'dark', 'forest', 'neutral'] },
    { path: 'mermaid.background', label: 'Hintergrund', type: 'text', ph: 'white' },
  ]},
  // Header / footer / logo apply to .docx + .pdf (page header & footer) and, for
  // the logo + footer text, to .pptx slides. {page} / {date} tokens in the text
  // render the live page number / current date. Empty text/logo = nothing drawn.
  { group: 'Kopfzeile', icon: '🔝', fields: [
    { path: 'header.text',      label: 'Text ({page}/{date})', type: 'text', ph: 'z.B. Firmenname  {date}', full: true },
    { path: 'header.align',     label: 'Ausrichtung', type: 'choice', choices: ['left', 'center', 'right'] },
    { path: 'header.font_size', label: 'Größe (pt)',  type: 'num', min: 6, max: 24 },
    { path: 'header.color',     label: 'Farbe',       type: 'color' },
  ]},
  { group: 'Fußzeile', icon: '🔻', fields: [
    { path: 'footer.text',         label: 'Text ({page}/{date})', type: 'text', ph: 'z.B. Vertraulich', full: true },
    { path: 'footer.align',        label: 'Ausrichtung', type: 'choice', choices: ['left', 'center', 'right'] },
    { path: 'footer.font_size',    label: 'Größe (pt)',  type: 'num', min: 6, max: 24 },
    { path: 'footer.color',        label: 'Farbe',       type: 'color' },
    { path: 'footer.page_numbers', label: 'Seitenzahlen', type: 'bool' },
  ]},
  { group: 'Logo', icon: '🖼️', fields: [
    { path: 'logo.__upload',   label: 'Logo-Bild', type: 'logo', full: true },
    { path: 'logo.position',   label: 'Position',  type: 'choice', choices: ['header', 'footer', 'slide', 'none'] },
    { path: 'logo.align',      label: 'Ausrichtung', type: 'choice', choices: ['left', 'center', 'right'] },
    { path: 'logo.width_inch', label: 'Breite (inch)', type: 'num', min: 0.3, max: 6, step: 0.1 },
  ]},
];

function _dsGet(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

// Pending logo upload state for the editor: base64 data + ext when the user
// picks a new file, the existing filename when editing, and a remove flag.
let _dsLogo = { data: null, ext: '', file: '', remove: false };

function docStyleNew() {
  const defaults = (state._docStyles && state._docStyles.defaults) || {};
  _dsLogo = { data: null, ext: '', file: '', remove: false };
  _docStyleRenderEditor('', { name: '', description: '', ...defaults });
}

async function docStyleEdit(name) {
  try {
    const d = await API.get('/v1/doc-styles?name=' + encodeURIComponent(name));
    // `parsed` is the preset deep-merged over defaults (full shape). Fall back
    // to defaults if the server is old and didn't send it.
    const parsed = d.parsed || (state._docStyles && state._docStyles.defaults) || {};
    // Seed logo state from the saved preset so the preview shows the existing logo.
    _dsLogo = { data: null, ext: '', file: ((parsed.logo || {}).file || ''), remove: false };
    _docStyleRenderEditor(d.name || name, parsed);
  } catch (e) { showToast('Laden fehlgeschlagen: ' + (e.message || e), true); }
}

function _docStyleRenderEditor(name, data) {
  const el = document.getElementById('doc-style-editor');
  if (!el) return;
  const fid = p => 'ds-f-' + p.replace(/\./g, '-');  // stable input id per key path
  const fieldHtml = (f) => {
    const v = _dsGet(data, f.path);
    const id = fid(f.path);
    const span = f.full ? 'grid-column:1/-1;' : '';
    const lbl = `<label style="font-size:11px;color:var(--text-300);display:block;margin-bottom:3px">${esc(f.label)}</label>`;
    if (f.type === 'logo') {
      // File picker + thumbnail + remove. The actual bytes live in _dsLogo;
      // the saved YAML's logo.file is derived at save time from the preset name.
      const hasFile = !!_dsLogo.file;
      const thumb = _dsLogo.data
        ? _dsLogo.data
        : (hasFile ? ('/v1/doc-styles?logo=' + encodeURIComponent(_dsLogo.file)) : '');
      return `<div style="${span}">${lbl}
        <div style="display:flex;align-items:center;gap:10px">
          <img id="ds-logo-thumb" src="${esc(thumb)}" alt=""
               style="height:40px;max-width:140px;object-fit:contain;border:1px solid var(--border-200);border-radius:5px;background:#fff;${thumb ? '' : 'display:none'}">
          <input type="file" accept="image/*" onchange="_docStyleLogoPick(this)"
                 style="font-size:12px;color:var(--text-200)">
          <button type="button" class="btn-secondary" style="padding:3px 8px;font-size:11px;color:var(--error);${(thumb ? '' : 'display:none')}"
                  id="ds-logo-remove" onclick="_docStyleLogoRemove()">Entfernen</button>
        </div></div>`;
    }
    if (f.type === 'color') {
      const hex = (typeof v === 'string' && v) ? v : '#000000';
      return `<div style="${span}">${lbl}<div style="display:flex;align-items:center;gap:6px">
        <input type="color" id="${id}" data-ds-path="${f.path}" data-ds-type="color" value="${esc(hex)}"
          oninput="document.getElementById('${id}-hex').value=this.value;_docStylePreview()"
          style="width:34px;height:28px;padding:0;border:1px solid var(--border-200);border-radius:5px;background:none;cursor:pointer">
        <input type="text" id="${id}-hex" value="${esc(hex)}" spellcheck="false"
          oninput="(/^#[0-9a-fA-F]{6}$/.test(this.value))&&(document.getElementById('${id}').value=this.value);_docStylePreview()"
          style="width:84px;padding:4px 6px;font-family:var(--font-mono);font-size:12px;border:1px solid var(--border-200);border-radius:5px;background:var(--bg-000);color:var(--text-100)">
      </div></div>`;
    }
    if (f.type === 'num') {
      return `<div style="${span}">${lbl}<input type="number" id="${id}" data-ds-path="${f.path}" data-ds-type="num"
        value="${v ?? ''}" ${f.min != null ? `min="${f.min}"` : ''} ${f.max != null ? `max="${f.max}"` : ''} step="${f.step || 1}"
        oninput="_docStylePreview()"
        style="width:100%;padding:4px 6px;font-size:13px;border:1px solid var(--border-200);border-radius:5px;background:var(--bg-000);color:var(--text-100)"></div>`;
    }
    if (f.type === 'bool') {
      return `<div style="${span}display:flex;align-items:center;gap:8px;padding-top:18px">
        <input type="checkbox" id="${id}" data-ds-path="${f.path}" data-ds-type="bool" ${v ? 'checked' : ''} onchange="_docStylePreview()">
        <label for="${id}" style="font-size:12px;color:var(--text-200);cursor:pointer">${esc(f.label)}</label></div>`;
    }
    if (f.type === 'choice') {
      return `<div style="${span}">${lbl}<select id="${id}" data-ds-path="${f.path}" data-ds-type="choice" onchange="_docStylePreview()"
        style="width:100%;padding:4px 6px;font-size:13px;border:1px solid var(--border-200);border-radius:5px;background:var(--bg-000);color:var(--text-100)">
        ${f.choices.map(c => `<option value="${esc(c)}" ${v === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>`;
    }
    if (f.type === 'font') {
      const FONTS = ['Calibri', 'Arial', 'Helvetica', 'Times New Roman', 'Georgia', 'Garamond', 'Verdana', 'Tahoma', 'Cambria', 'Consolas', 'Courier New', 'Roboto', 'Open Sans', 'Lato'];
      const cur = typeof v === 'string' ? v : '';
      const known = FONTS.includes(cur);
      return `<div style="${span}">${lbl}<input list="ds-fontlist" id="${id}" data-ds-path="${f.path}" data-ds-type="text"
        value="${esc(cur)}" oninput="_docStylePreview()" placeholder="Schriftname"
        style="width:100%;padding:4px 6px;font-size:13px;border:1px solid var(--border-200);border-radius:5px;background:var(--bg-000);color:var(--text-100)">
        ${known ? '' : ''}</div>`;
    }
    // text
    return `<div style="${span}">${lbl}<input type="text" id="${id}" data-ds-path="${f.path}" data-ds-type="text"
      value="${esc(typeof v === 'string' ? v : (v ?? ''))}" placeholder="${esc(f.ph || '')}" oninput="_docStylePreview()"
      style="width:100%;padding:4px 6px;font-size:13px;border:1px solid var(--border-200);border-radius:5px;background:var(--bg-000);color:var(--text-100)"></div>`;
  };

  const sections = _DOC_STYLE_FIELDS.map(sec => `
    <div style="border:1px solid var(--border-100);border-radius:8px;padding:10px 12px;background:var(--bg-100)">
      <div style="font-size:12px;font-weight:600;color:var(--text-200);margin-bottom:8px">${sec.icon} ${esc(sec.group)}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">
        ${sec.fields.map(fieldHtml).join('')}
      </div>
    </div>`).join('');

  el.innerHTML = `
    <datalist id="ds-fontlist">${['Calibri','Arial','Helvetica','Times New Roman','Georgia','Garamond','Verdana','Tahoma','Cambria','Consolas','Courier New','Roboto','Open Sans','Lato'].map(f=>`<option value="${esc(f)}">`).join('')}</datalist>
    <div style="border:1px solid var(--border-200);border-radius:10px;padding:14px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
        <label style="font-size:12px;color:var(--text-300)">Name:</label>
        <input id="doc-style-name" type="text" value="${esc(name)}" placeholder="z.B. corporate" ${name ? 'readonly' : ''}
               style="padding:4px 8px;font-size:13px;border:1px solid var(--border-200);border-radius:6px;background:${name ? 'var(--bg-100)' : 'var(--bg-000)'};color:var(--text-100);width:180px">
        <span style="font-size:11px;color:var(--text-400)">a-z 0-9 _ -</span>
        <label style="font-size:12px;color:var(--text-300);margin-left:8px">Beschreibung:</label>
        <input id="doc-style-desc" type="text" value="${esc(_dsGet(data, 'description') || '')}" placeholder="Kurze Beschreibung"
               style="flex:1;min-width:160px;padding:4px 8px;font-size:13px;border:1px solid var(--border-200);border-radius:6px;background:var(--bg-000);color:var(--text-100)">
      </div>
      <div style="display:grid;grid-template-columns:1fr 280px;gap:14px;margin-top:10px;align-items:start">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">${sections}</div>
        <div style="position:sticky;top:0">
          <div style="font-size:11px;font-weight:600;color:var(--text-300);margin-bottom:6px">Vorschau</div>
          <div id="doc-style-preview" style="border:1px solid var(--border-200);border-radius:8px;overflow:hidden;font-size:13px"></div>
          <details style="margin-top:10px">
            <summary style="font-size:11px;color:var(--text-400);cursor:pointer">YAML anzeigen</summary>
            <pre id="doc-style-yaml-preview" style="margin:6px 0 0;padding:8px;font-family:var(--font-mono);font-size:11px;background:var(--bg-000);border:1px solid var(--border-100);border-radius:6px;color:var(--text-300);white-space:pre-wrap;max-height:240px;overflow:auto"></pre>
          </details>
        </div>
      </div>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn-primary" style="padding:5px 14px;font-size:13px" onclick="docStyleSave()">Speichern</button>
        <button class="btn-secondary" style="padding:5px 12px;font-size:13px" onclick="document.getElementById('doc-style-editor').innerHTML=''">Schließen</button>
      </div>
    </div>`;
  _docStylePreview();
}

// Logo file picked in the editor → read as base64, stash in _dsLogo, refresh.
function _docStyleLogoPick(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) { showToast('Logo zu groß (max 5 MB)', true); input.value = ''; return; }
  const m = (file.name.match(/\.[a-z0-9]+$/i) || ['.png'])[0].toLowerCase();
  const reader = new FileReader();
  reader.onload = () => {
    _dsLogo = { data: reader.result, ext: m, file: _dsLogo.file, remove: false };
    const thumb = document.getElementById('ds-logo-thumb');
    if (thumb) { thumb.src = reader.result; thumb.style.display = ''; }
    const rm = document.getElementById('ds-logo-remove'); if (rm) rm.style.display = '';
    _docStylePreview();
  };
  reader.readAsDataURL(file);
}

function _docStyleLogoRemove() {
  _dsLogo = { data: null, ext: '', file: '', remove: true };
  const thumb = document.getElementById('ds-logo-thumb'); if (thumb) { thumb.src = ''; thumb.style.display = 'none'; }
  const rm = document.getElementById('ds-logo-remove'); if (rm) rm.style.display = 'none';
  _docStylePreview();
}

// Derive the logo filename that will be saved for a preset (matches the server's
// <slug>.logo<ext> naming). Empty if no logo is set / it was removed.
function _docStyleLogoFile() {
  if (_dsLogo.remove) return '';
  const slug = ((document.getElementById('doc-style-name')?.value || '').trim()
    .replace(/[^a-zA-Z0-9_-]/g, '')) || 'neu';
  if (_dsLogo.data) return `${slug}.logo${_dsLogo.ext || '.png'}`;
  return _dsLogo.file || '';
}

// Read every form field back into a nested style object keyed by the field paths.
function _docStyleCollect() {
  const out = {};
  document.querySelectorAll('#doc-style-editor [data-ds-path]').forEach(inp => {
    if (inp.id && inp.id.endsWith('-hex')) return;  // skip the color hex twin
    const path = inp.dataset.dsPath;
    const t = inp.dataset.dsType;
    let v;
    if (t === 'bool') v = inp.checked;
    else if (t === 'num') { v = inp.value.trim() === '' ? null : Number(inp.value); }
    else v = inp.value;
    if (v === null || v === '' || (typeof v === 'number' && Number.isNaN(v))) return;
    const keys = path.split('.');
    let o = out;
    for (let i = 0; i < keys.length - 1; i++) o = (o[keys[i]] = o[keys[i]] || {});
    o[keys[keys.length - 1]] = v;
  });
  // The logo file isn't a plain form field (it's an upload) — inject its name so
  // _load_doc_style finds <slug>.logo<ext> next to the preset on disk.
  const lf = _docStyleLogoFile();
  if (lf) { out.logo = out.logo || {}; out.logo.file = lf; }
  else if (out.logo) { delete out.logo.file; if (!Object.keys(out.logo).length) delete out.logo; }
  return out;
}

// Minimal YAML emitter for the fixed 2-level doc-style schema (top-level scalars
// + one level of nested maps; values are strings/numbers/bools). The storage
// format the doc tools read is YAML, so we build it here deterministically.
function _docStyleToYaml(name, desc, style) {
  const q = s => {
    s = String(s);
    // quote if it could be misread (leading #, has colon+space, empty, hex color)
    return /^[#\s]|[:#]|^$|^["']|^\d|^(true|false|null|yes|no)$/i.test(s) || /\s$/.test(s)
      ? '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"' : s;
  };
  const scalar = v => typeof v === 'boolean' ? (v ? 'true' : 'false')
    : typeof v === 'number' ? String(v) : q(v);
  let out = `name: ${q(name)}\n`;
  out += `description: ${q(desc || '')}\n`;
  for (const [sec, val] of Object.entries(style)) {
    if (sec === 'name' || sec === 'description') continue;
    if (val && typeof val === 'object' && !Array.isArray(val)) {
      out += `${sec}:\n`;
      for (const [k, vv] of Object.entries(val)) out += `  ${k}: ${scalar(vv)}\n`;
    } else {
      out += `${sec}: ${scalar(val)}\n`;
    }
  }
  return out;
}

// Live preview: render a heading/body/table sample in the chosen fonts+colors,
// and mirror the YAML that will be saved.
function _docStylePreview() {
  const box = document.getElementById('doc-style-preview');
  if (!box) return;
  const s = _docStyleCollect();
  const c = s.colors || {}, f = s.fonts || {}, sz = s.sizes || {};
  const fb = f.body || 'Calibri', fh = f.heading || fb, fm = f.mono || 'Consolas';
  // Header / footer / logo bands (mirror what the doc tools draw on each page).
  const hdr = s.header || {}, ftr = s.footer || {}, logo = s.logo || {};
  const tok = t => String(t || '').replace('{page}', '1').replace('{date}', new Date().toISOString().slice(0, 10));
  const logoSrc = _dsLogo.remove ? '' : (_dsLogo.data || (_dsLogo.file ? '/v1/doc-styles?logo=' + encodeURIComponent(_dsLogo.file) : ''));
  const logoPos = (logo.position || 'header').toLowerCase();
  const logoImg = (where) => (logoSrc && logoPos === where && logoPos !== 'none')
    ? `<img src="${esc(logoSrc)}" style="height:${Math.min(28, (logo.width_inch || 1.2) * 14)}px;max-width:90px;object-fit:contain;float:${esc((logo.align || 'right') === 'center' ? 'none' : (logo.align || 'right'))};${(logo.align === 'center') ? 'display:block;margin:0 auto' : ''}">` : '';
  const band = (spec, where, withPage) => {
    let txt = tok(spec.text);
    if (where === 'footer' && spec.page_numbers && !String(spec.text || '').includes('{page}'))
      txt = (txt ? txt + '  ' : '') + 'Seite 1';
    if (!txt && !logoImg(where)) return '';
    return `<div style="font-family:'${esc(fb)}',sans-serif;font-size:${(spec.font_size || 9)}px;color:${esc(spec.color || '#666')};text-align:${esc(spec.align || (where === 'footer' ? 'center' : 'left'))};padding:4px 14px;border-${where === 'header' ? 'bottom' : 'top'}:1px solid #eee;overflow:hidden">${logoImg(where)}${esc(txt)}</div>`;
  };
  box.innerHTML = `
    <div style="background:#fff">
    ${band(hdr, 'header', true)}
    <div style="padding:12px 14px;background:#fff">
      <div style="font-family:'${esc(fh)}',sans-serif;color:${esc(c.heading || '#1F3864')};font-size:${(sz.h1 || 20)}px;font-weight:700;line-height:1.15">Überschrift 1</div>
      <div style="font-family:'${esc(fh)}',sans-serif;color:${esc(c.heading || '#1F3864')};font-size:${(sz.h2 || 16)}px;font-weight:700;margin-top:6px">Überschrift 2</div>
      <p style="font-family:'${esc(fb)}',sans-serif;color:${esc(c.body || '#222')};font-size:${(sz.body || 11)}px;line-height:1.5;margin:6px 0">
        Beispiel-Fließtext mit einem <span style="color:${esc(c.accent || '#2E74B5')}">Akzent-Link</span> und
        <code style="font-family:'${esc(fm)}',monospace">monospace</code>.</p>
      <table style="border-collapse:collapse;width:100%;font-family:'${esc(fb)}',sans-serif;font-size:${Math.max(9, (sz.body || 11) - 1)}px;margin-top:4px">
        <tr><th style="background:${esc(c.table_header_bg || '#1F3864')};color:${esc(c.table_header_text || '#fff')};padding:3px 6px;text-align:left;border:1px solid #ddd">Spalte A</th>
            <th style="background:${esc(c.table_header_bg || '#1F3864')};color:${esc(c.table_header_text || '#fff')};padding:3px 6px;text-align:left;border:1px solid #ddd">Spalte B</th></tr>
        <tr><td style="color:${esc(c.body || '#222')};padding:3px 6px;border:1px solid #ddd">Zeile 1</td><td style="color:${esc(c.body || '#222')};padding:3px 6px;border:1px solid #ddd">Wert</td></tr>
      </table>
    </div>
    ${band(ftr, 'footer', true)}
    </div>`;
  const yp = document.getElementById('doc-style-yaml-preview');
  if (yp) {
    const name = (document.getElementById('doc-style-name')?.value || 'neu').trim() || 'neu';
    const desc = document.getElementById('doc-style-desc')?.value || '';
    yp.textContent = _docStyleToYaml(name, desc, s);
  }
}

async function docStyleSave() {
  const name = (document.getElementById('doc-style-name')?.value || '').trim();
  const desc = document.getElementById('doc-style-desc')?.value || '';
  if (!name) { showToast('Name erforderlich', true); return; }
  const yamlText = _docStyleToYaml(name, desc, _docStyleCollect());
  const payload = { name, yaml: yamlText };
  if (_dsLogo.data) { payload.logo_data = _dsLogo.data; payload.logo_ext = _dsLogo.ext || '.png'; }
  else if (_dsLogo.remove) { payload.logo_remove = true; }
  try {
    await API.post('/v1/doc-styles', payload);
    showToast('Stil gespeichert');
    const C = document.getElementById('general-tab-content');
    if (C) await _genTab_doc_styles(C);
  } catch (e) { showToast('Speichern fehlgeschlagen: ' + (e.message || e), true); }
}

async function docStyleDelete(name) {
  if (!confirm(`Stil „${name}" löschen?`)) return;
  try {
    await API.post('/v1/doc-styles', { name, delete: true });
    showToast('Gelöscht');
    const C = document.getElementById('general-tab-content');
    if (C) await _genTab_doc_styles(C);
  } catch (e) { showToast('Löschen fehlgeschlagen: ' + (e.message || e), true); }
}

async function _genTab_wiki(C) {
  /* ─── WIKI ─── settings for the LLM Wiki (engine/wiki_store). */
  try {
    const cfg = await API.get('/v1/wiki/config').catch(e => ({ error: e.message || String(e) }));
    if (cfg.error) { C.innerHTML = P(`<div style="color:var(--error)">${esc(cfg.error)}</div>`); return; }
    const isAdmin = state.authUser && state.authUser.role === 'admin';
    const dis = isAdmin ? '' : 'disabled';

    // TTS model picker — only models registered in config (any provider).
    const allModels = (cfg.available_models || []);
    const ttsCur = cfg.tts_model || '';
    const ttsOpts = '<option value="">(kein TTS-Modell — Podcast/Vorlesen deaktiviert)</option>'
      + allModels.map(mid => `<option value="${esc(mid)}" ${mid === ttsCur ? 'selected' : ''}>${esc(mid)}</option>`).join('');

    const ROW = 'display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid var(--border-100)';
    C.innerHTML = P(`
      <div style="max-width:720px">
        <h2 style="margin:0 0 4px;font-size:18px">Wiki</h2>
        <p style="color:var(--text-400);font-size:13px;margin:0 0 16px">
          Einstellungen für das LLM-Wiki — das durchsuchbare, editierbare Wissens-Wiki, das zugleich das Langzeit-Gedächtnis des Agenten ist.
        </p>

        <div style="${ROW}">
          <div style="flex:1">
            <div style="font-weight:600">Knowledge Graph für Wiki-Seiten</div>
            <div style="color:var(--text-400);font-size:12px">
              Aus <b>projekt-getaggten</b> Wiki-Seiten zusätzlich KG-Tripel in die Projekt-KG extrahieren (zusätzlich zur normalen Suche). Jeder LLM-Aufruf kostet — daher optional. Standard: aus.
            </div>
          </div>
          <label style="display:flex;align-items:center;gap:6px">
            <input type="checkbox" id="wiki-kg-toggle" ${cfg.kg_wiki ? 'checked' : ''} ${dis}>
          </label>
        </div>

        <div style="${ROW}">
          <div style="flex:1">
            <div style="font-weight:600">Text-to-Speech-Modell</div>
            <div style="color:var(--text-400);font-size:12px">
              Für <b>🔊 Vorlesen</b> und <b>🎧 Podcast</b> im Wiki (sowie Chat-Vorlesen + Studio Audio Overview). Ohne Modell sind diese Funktionen deaktiviert.
            </div>
          </div>
          <select id="wiki-tts-model" ${dis} style="min-width:240px;padding:6px 8px;border-radius:6px;background:var(--bg-100);color:var(--text-100);border:1px solid var(--border-100)">${ttsOpts}</select>
        </div>

        <div style="padding:14px 0;color:var(--text-300);font-size:13px;line-height:1.6">
          <div style="font-weight:600;color:var(--text-200);margin-bottom:4px">Zur Information (anderswo konfiguriert)</div>
          <div>• <b>Text-Modell des Wiki</b> (Reorganisieren von Chats, Auto-Tags, Zusammenfassungen): nutzt das Zusammenfassungs-Modell
            <code>${esc(cfg.summary_model || '(nicht gesetzt)')}</code>${cfg.summary_model ? '' : ` → Fallback Server-Standard <code>${esc(cfg.default_model || '—')}</code>`} — einstellbar unter <b>Server → Zusammenfassungen</b>.</div>
          <div>• <b>KG global</b> ist ${cfg.kg_enabled ? '<span style="color:#4caf50">aktiv</span>' : '<span style="color:var(--error)">deaktiviert</span>'} — Detail-Einstellungen unter <b>Knowledge Graph</b>. (Wiki-KG braucht beides: hier AN + KG global AN.)</div>
          <div>• <b>Chat → Wiki automatisch</b>: passiert pro Chat, wenn dessen Gedächtnis-Schalter (im Chat) auf AN/Auto steht — kein globaler Schalter.</div>
        </div>

        <div style="margin-top:12px">
          <button class="btn-primary" id="wiki-save-btn" onclick="saveWikiConfig()" ${dis}>${isAdmin ? 'Einstellungen speichern' : 'Nur für Administratoren'}</button>
        </div>
      </div>
    `);
  } catch (e) {
    C.innerHTML = P(`<div style="color:var(--error)">${esc(e.message || e)}</div>`);
  }
}

async function saveWikiConfig() {
  try {
    await API.post('/v1/wiki/config', {
      kg_wiki: document.getElementById('wiki-kg-toggle')?.checked ? true : false,
      tts_model: document.getElementById('wiki-tts-model')?.value || '',
    });
    showToast('Wiki-Einstellungen gespeichert');
  } catch (e) {
    showToast('Speichern fehlgeschlagen: ' + (e.message || e), true);
  }
}
