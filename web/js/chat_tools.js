// chat_tools.js — tool-call/result rendering + worker flows + per-message memory menus. Split from chat.js (Tier F Phase 4). Global <script>, no modules.

// --- Memory menu helpers ---

// Find the user message id that opens the turn containing messages[idx].
function getTurnIdForMessage(idx) {
  const chat = state.activeChat;
  if (!chat) return 0;
  const msgs = chat.messages || [];
  for (let i = Math.min(idx, msgs.length - 1); i >= 0; i--) {
    if (msgs[i].role === 'user' && msgs[i].id) return msgs[i].id;
  }
  return 0;
}
function renderMemoryMenuItems(idx) {
  const chat = state.activeChat;
  const memOff = !chat || (chat.memoryMode || (chat.saveToMemory ? 'on' : 'off')) === 'off';
  const turnId = getTurnIdForMessage(idx);
  const memorized = (state.memorizedTurns || {})[chat?.sessionId] || new Set();
  const hasAny = memorized.size > 0;
  const thisMemorized = turnId && memorized.has(turnId);

  // Collect turn ids from messages for above/below checks
  const msgs = chat?.messages || [];
  let turnsAbove = 0, turnsBelow = 0, totalTurns = 0;
  for (const m of msgs) {
    if (m.role !== 'user' || !m.id) continue;
    totalTurns++;
    if (m.id < turnId) turnsAbove++;
    else if (m.id > turnId) turnsBelow++;
  }
  const hasAbove = turnsAbove > 0;
  const hasBelow = turnsBelow > 0;

  // Each item: {label, scope, mode ('memorize'|'purge'), enabled}
  const items = [
    { label: 'Gesamten Chat merken',                       scope: 'all',   mode: 'memorize', enabled: memOff && totalTurns > 0 },
    { label: 'Diese Antwort merken',                       scope: 'this',  mode: 'memorize', enabled: memOff && !!turnId && !thisMemorized },
    { label: 'Alles oberhalb merken',                      scope: 'above', mode: 'memorize', enabled: memOff && hasAbove },
    { label: 'Alles unterhalb merken',                     scope: 'below', mode: 'memorize', enabled: memOff && hasBelow },
    { sep: true },
    { label: 'Gesamten Speicher dieses Chats entfernen',   scope: 'all',   mode: 'purge',    enabled: memOff && hasAny, destructive: true },
    { label: 'Speicher dieser Antwort entfernen',          scope: 'this',  mode: 'purge',    enabled: memOff && !!thisMemorized, destructive: true },
    { label: 'Speicher der Antworten oberhalb entfernen',  scope: 'above', mode: 'purge',    enabled: memOff && hasAbove, destructive: true },
    { label: 'Speicher der Antworten unterhalb entfernen', scope: 'below', mode: 'purge',    enabled: memOff && hasBelow, destructive: true },
  ];

  const svgMem = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 10l9-6 9 6v1H3z" fill="currentColor" fill-opacity="0.15"/><line x1="3" y1="11" x2="21" y2="11"/><line x1="3" y1="21" x2="21" y2="21"/></svg>';
  const svgDel = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>';

  let html = '';
  if (!memOff) {
    html += `<div class="msg-edit-dropdown-section-label" title="Chat-Speicher auf „aus“ setzen, um diese Aktionen zu nutzen">Speichermodus: ${chat.memoryMode || 'on'} — Aktionen deaktiviert</div>`;
  }
  for (const it of items) {
    if (it.sep) { html += '<hr>'; continue; }
    const cls = [
      'msg-edit-dropdown-item',
      it.destructive ? 'destructive' : '',
      it.enabled ? '' : 'disabled',
    ].filter(Boolean).join(' ');
    const handler = it.enabled ? `onclick="runTurnMemoryAction('${it.mode}','${it.scope}',${idx})"` : '';
    html += `<div class="${cls}" ${handler}>${it.mode === 'purge' ? svgDel : svgMem}${it.label}</div>`;
  }
  return html;
}
function toggleMsgMemoryMenu(event, idx) {
  event.stopPropagation();
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(el => el.classList.remove('open'));
  const menu = document.getElementById(`msg-memory-menu-${idx}`);
  if (!menu) return;
  // Re-render items fresh (memorizedTurns may have changed)
  menu.innerHTML = renderMemoryMenuItems(idx);
  menu.classList.toggle('open');
  const close = (e) => {
    if (!menu.contains(e.target)) {
      menu.classList.remove('open');
      document.removeEventListener('click', close);
    }
  };
  setTimeout(() => document.addEventListener('click', close), 0);
  // Refresh memorized state in the background; re-render on update
  refreshMemorizedTurns().then(() => {
    if (menu.classList.contains('open')) menu.innerHTML = renderMemoryMenuItems(idx);
  });
}
async function refreshMemorizedTurns() {
  const chat = state.activeChat;
  if (!chat?.sessionId) return;
  try {
    const data = await API.get(`/v1/mempalace/session-turns?session_id=${encodeURIComponent(chat.sessionId)}`);
    if (!state.memorizedTurns) state.memorizedTurns = {};
    state.memorizedTurns[chat.sessionId] = new Set((data.turn_ids || []).map(n => parseInt(n) || 0));
  } catch (e) {}
}
async function runTurnMemoryAction(mode, scope, idx) {
  const chat = state.activeChat;
  if (!chat?.sessionId) return;
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(el => el.classList.remove('open'));
  const turnId = getTurnIdForMessage(idx);
  const action = mode === 'purge' ? 'purge_turns' : 'memorize_turns';
  const payload = { action, session_id: chat.sessionId, scope };
  if (scope !== 'all') payload.anchor_turn_id = turnId;

  if (mode === 'purge') {
    const label = { all: 'den gesamten Speicher dieses Chats', this: 'den Speicher dieser Antwort',
                    above: 'den Speicher der Antworten oberhalb', below: 'den Speicher der Antworten unterhalb' }[scope] || scope;
    if (!await showConfirmDanger(`${label} löschen?\n\nDies entfernt die zugehörigen MemPalace-Einträge dauerhaft und kann nicht rückgängig gemacht werden.`, 'Speicher löschen', 'Löschen')) return;
  }

  try {
    const resp = await API.post('/v1/sessions/manage', payload);
    if (mode === 'purge') {
      showToast(`${resp.purged || 0} Anfrage(n) aus dem Speicher gelöscht`);
    } else {
      showToast(`${resp.memorizing || 0} Anfrage(n) werden gemerkt…`);
    }
    // Poll twice for updated state (memorize is async)
    setTimeout(refreshMemorizedTurns, 500);
    setTimeout(refreshMemorizedTurns, 2500);
  } catch (e) {
    showToast('Fehlgeschlagen: ' + e.message, true);
  }
}
function toolDescribe(name, args) {
  const a = args || {};
  const descs = {
    read_file: () => `Datei lesen: ${a.path || a.file_path || '...'}`,
    write_file: () => `Datei schreiben: ${a.path || a.file_path || '...'}`,
    edit_file: () => `Datei bearbeiten: ${a.path || a.file_path || '...'}`,
    list_directory: () => `Verzeichnis auflisten: ${a.path || a.directory || '...'}`,
    search_files: () => `Dateien durchsuchen nach „${a.query || a.pattern || '...'}"`,
    execute_command: () => `Befehl ausführen: \`${(a.command || '').substring(0, 60)}${(a.command || '').length > 60 ? '...' : ''}\``,
    python_exec: () => `Python ausführen (${(a.code || '').split('\n').length} Zeilen)`,
    web_fetch: () => { try { return `Webseite abrufen: ${a.url ? new URL(a.url).hostname : '...'}`; } catch(e) { return `Webseite abrufen: ${a.url || '...'}`; } },
    exa_search: () => `Im Web suchen nach „${a.query || '...'}"`,
    searxng_search: () => `Im Web suchen nach „${a.query || '...'}"`,
    run_background_task: () => a.title ? `Hintergrundaufgabe: ${a.title}` : 'Hintergrundaufgabe starten',
    gmail_inbox: () => 'Posteingang prüfen',
    gmail_read: () => `E-Mail lesen${a.id ? ' #' + a.id : ''}`,
    gmail_search: () => `E-Mails suchen: „${a.query || '...'}"`,
    gmail_send: () => `E-Mail senden an ${a.to || '...'}`,
    gmail_reply: () => `E-Mail beantworten${a.id ? ' #' + a.id : ''}`,
    memory_store: () => `Erinnerung speichern: „${a.name || a.title || '...'}"`,
    memory_recall: () => `Erinnerung abrufen: „${a.query || '...'}"`,
    memory_shared: () => `Geteilten Speicher lesen${a.scope ? ' (' + a.scope + ')' : ''}`,
    memory_delete: () => `Erinnerung löschen: „${a.name || '...'}"`,
    mempalace_query: () => `Hole Informationen aus Projektspeicher${a.query ? ': „' + String(a.query).substring(0, 60) + '"' : '...'}`,
    mempalace_get_drawer: () => `Projektspeicher-Eintrag abrufen`,
    mempalace_list_drawers: () => `Projektspeicher-Einträge auflisten`,
    mempalace_kg_query: () => `Wissensgraph abfragen`,
    mempalace_kg_search: () => `Wissensgraph durchsuchen`,
    mempalace_kg_neighbors: () => `Wissensgraph-Nachbarn abrufen`,
    save_chat_to_memory: () => `Chat in Speicher sichern`,
    delegate_task: () => `Aufgabe delegieren an ${a.agent || a.agent_id || '...'}`,
    task_status: () => `Aufgabenstatus prüfen`,
    task_cancel: () => `Aufgabe abbrechen`,
    git_command: () => `Git: ${a.subcommand || a.command || ''}`,
    github_command: () => `GitHub: ${a.subcommand || a.command || ''}`,
    use_skill: () => `Skill anwenden: „${a.skill || a.name || '...'}"`,
    code_graph_build: () => `Code-Graph erstellen${a.path ? ' für ' + a.path : ''}`,
    code_graph_query: () => `Code-Graph abfragen`,
    code_graph_impact: () => `Auswirkungen analysieren`,
    schedule_list: () => 'Zeitpläne auflisten',
    schedule_history: () => 'Zeitplan-Verlauf prüfen',
    context_search: () => `Kontext durchsuchen: „${a.query || '...'}"`,
    context_detail: () => `Kontext-Detail laden`,
    context_recall: () => `Kontext abrufen`,
    read_document: () => `Dokument lesen: ${a.path || a.file_path || ''}`,
    write_document: () => `Dokument schreiben: ${a.path || a.file_path || ''}`,
    edit_document: () => `Dokument bearbeiten: ${a.path || a.file_path || ''}`,
    list_nodes: () => 'Remote-Knoten auflisten',
    get_artifact_detail: () => `Artefakt prüfen: ${a.artifact_id || ''}`,
    worker_status: () => a.worker_id ? `Worker prüfen: ${a.worker_id}` : 'Worker-Status prüfen',
    worker_abort: () => `Worker abbrechen: ${a.worker_id || ''}`,
    worker_pause: () => `Worker pausieren: ${a.worker_id || ''}`,
    worker_resume: () => `Worker fortsetzen: ${a.worker_id || ''}`,
    worker_send: () => `An Worker senden: ${a.worker_id || ''}`,
    worker_ask_user: () => `Worker fragt: ${(a.question || '').substring(0, 50)}`,
  };
  const fn = descs[name];
  return fn ? fn() : name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
function renderToolArgsTable(args) {
  if (!args || typeof args !== 'object' || Object.keys(args).length === 0) return '';
  let html = '<table class="tool-args-table">';
  for (const [k, v] of Object.entries(args)) {
    const val = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
    const displayVal = val.length > 300 ? val.substring(0, 300) + '...' : val;
    html += `<tr><td class="tool-args-key">${esc(k)}</td><td class="tool-args-val"><pre>${esc(displayVal)}</pre></td></tr>`;
  }
  html += '</table>';
  return html;
}
// Find the worker flow associated with a tool_call msg.
// Prefers a worker_id parsed from the result envelope (completed); falls back
// to the most-recent still-running worker with the same tool_name.
function findWorkerFlow(toolName, resultStr) {
  if (resultStr) {
    try {
      const rj = JSON.parse(resultStr);
      if (rj && rj.worker_id && state.workerFlows[rj.worker_id]) {
        return state.workerFlows[rj.worker_id];
      }
      if (rj && rj.worker_id) {
        // Envelope has the flow even when state wasn't seeded yet
        return {
          worker_id: rj.worker_id,
          tool_name: toolName,
          state: rj.state || 'COMPLETED',
          duration: rj.duration_seconds || null,
          flow: Array.isArray(rj.flow) ? rj.flow : [],
          artifacts: rj.artifacts || [],
          question: null,
          summariser_usage: rj.summariser_usage || null,
        };
      }
    } catch (e) {}
  }
  // Live fallback: most-recent running worker matching tool name
  const candidates = Object.values(state.workerFlows).filter(w =>
    w.tool_name === toolName &&
    !['COMPLETED', 'FAILED', 'ABORTED', 'TIMED_OUT'].includes(w.state)
  );
  if (candidates.length) {
    candidates.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
    return candidates[0];
  }
  return null;
}
function renderWorkerFlow(wf) {
  if (!wf) return '';
  const stateCls = (wf.state || 'running').toLowerCase();
  const dur = wf.duration != null ? `${Number(wf.duration).toFixed(1)}s` :
              (wf.started_at ? `${((Date.now()/1000) - wf.started_at).toFixed(0)}s` : '');
  const su = wf.summariser_usage || {};
  const suTotal = (su.tokens_in || 0) + (su.tokens_out || 0);
  const suLabel = suTotal > 0
    ? `<span class="worker-flow-id" title="Zusammenfassungs-LLM: ${(su.tokens_in||0).toLocaleString()} ein / ${(su.tokens_out||0).toLocaleString()} aus${su.model ? ' · ' + esc(su.model) : ''}">LLM ${suTotal.toLocaleString()} Token</span>`
    : '';
  const header = `
    <div class="worker-flow-header">
      <span>Worker-Ablauf</span>
      <span class="worker-flow-state ${stateCls}">${esc(wf.state || 'RUNNING')}</span>
      <span class="worker-flow-id">${esc(wf.worker_id || '')}</span>
      ${suLabel}
      ${dur ? `<span class="worker-flow-duration">${esc(dur)}</span>` : ''}
    </div>`;
  const flow = wf.flow || [];
  const activeIdx = flow.length - 1;
  const steps = flow.map((e, i) => {
    const isActive = (i === activeIdx) && !['COMPLETED', 'FAILED', 'ABORTED', 'TIMED_OUT'].includes(wf.state || '');
    let label = '', meta = '', detail = '', cls = '';
    if (e.kind === 'phase') { label = esc(e.phase || 'step'); cls = isActive ? 'active' : ''; }
    else if (e.kind === 'artifact') {
      label = 'Artefakt gespeichert';
      meta = e.size_bytes != null ? `${e.size_bytes.toLocaleString()} Bytes` : '';
      detail = esc(e.artifact_id || e.name || '');
    }
    else if (e.kind === 'question') {
      label = 'Nutzer gefragt';
      cls = 'question';
      detail = esc(e.question || '');
    }
    else if (e.kind === 'answer') { label = 'Antwort erhalten'; cls = 'answer'; detail = esc(e.answer || ''); }
    else if (e.kind === 'state') { label = esc(e.state || 'state'); meta = e.reason ? esc(e.reason) : ''; cls = 'state'; }
    else if (e.kind === 'error') { label = 'Fehler'; cls = 'error'; detail = esc(e.message || ''); }
    else if (e.kind === 'summariser') {
      label = 'Zusammenfassungs-LLM';
      const ti = e.tokens_in || 0, to = e.tokens_out || 0;
      meta = `${ti.toLocaleString()} in / ${to.toLocaleString()} out`;
      detail = e.model ? esc(e.model) : '';
    }
    else { label = esc(e.kind || ''); }
    const nextTs = i + 1 < flow.length ? flow[i + 1].ts : (wf.state === 'RUNNING' ? Date.now() / 1000 : e.ts);
    const stepDur = (nextTs && e.ts) ? (nextTs - e.ts) : 0;
    const stepDurStr = stepDur > 0.1 ? `${stepDur.toFixed(1)}s` : '';
    return `<li class="worker-flow-step ${cls}">
      <span class="worker-flow-step-label">${label}</span>
      ${meta ? `<span class="worker-flow-step-meta">${meta}</span>` : ''}
      ${stepDurStr ? `<span class="worker-flow-step-meta">${stepDurStr}</span>` : ''}
      ${detail ? `<div class="worker-flow-step-detail">${detail}</div>` : ''}
    </li>`;
  }).join('');
  const artifacts = (wf.artifacts || []).map(a =>
    `<span class="worker-flow-artifact" title="${esc(a.path || '')}">${esc(a.artifact_id || a.name || 'artifact')}${a.size_bytes ? ' · ' + a.size_bytes.toLocaleString() + 'b' : ''}</span>`
  ).join('');
  return `<div class="worker-flow">
    ${header}
    ${steps ? `<ul class="worker-flow-timeline">${steps}</ul>` : '<div style="font-size:11px;color:var(--text-400)">Noch keine Schritte.</div>'}
    ${artifacts ? `<div class="worker-flow-artifacts">${artifacts}</div>` : ''}
  </div>`;
}
// --- Tool result rendering helpers (syntax highlight + expand + copy) ---
const TOOL_RESULT_INITIAL_CHARS = 8000;
const TOOL_RESULT_MAX_RENDER = 200000;
const _toolResultStore = new Map(); // id -> { full, lang, terminal }
let _toolResultSeq = 0;
// Per-tool default language hint for highlight.js. 'shell' = terminal styling.
const TOOL_LANG_HINTS = {
  execute_command: 'shell',
  python_exec: 'shell',
  read_file: null,        // inferred from args.path extension
  read_document: null,
  edit_file: null,
  write_file: null,
  search_files: 'shell',
  list_directory: 'shell',
  git_command: 'shell',
  exa_search: 'json',
  web_fetch: null,
  context_search: 'json',
  context_detail: 'json',
  context_recall: 'json',
  mempalace_query: 'json',
  mempalace_get_drawer: 'json',
  mempalace_list_drawers: 'json',
  mempalace_kg_query: 'json',
  mempalace_kg_search: 'json',
  mempalace_kg_neighbors: 'json',
  schedule_list: 'json',
  schedule_history: 'json',
  task_status: 'json',
  code_graph_query: 'json',
  list_nodes: 'json',
};
const EXT_TO_LANG = {
  py: 'python', js: 'javascript', ts: 'typescript', tsx: 'tsx', jsx: 'jsx',
  go: 'go', rs: 'rust', java: 'java', c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp',
  cs: 'csharp', rb: 'ruby', php: 'php', swift: 'swift', kt: 'kotlin',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml', ini: 'ini',
  xml: 'xml', html: 'xml', css: 'css', scss: 'scss',
  md: 'markdown', sql: 'sql', dockerfile: 'dockerfile',
};
function detectToolResultLang(toolName, args, body) {
  const hint = TOOL_LANG_HINTS[toolName];
  if (hint !== undefined && hint !== null) return hint;
  // Filename-based inference
  const path = (args && (args.path || args.file_path || args.file)) || '';
  if (path) {
    const m = String(path).toLowerCase().match(/\.([a-z0-9]+)$/);
    if (m && EXT_TO_LANG[m[1]]) return EXT_TO_LANG[m[1]];
    if (/dockerfile$/i.test(path)) return 'dockerfile';
  }
  // Body-shape inference
  const head = (body || '').slice(0, 200).trim();
  if (head.startsWith('{') || head.startsWith('[')) {
    try { JSON.parse(body); return 'json'; } catch(e) {}
  }
  return null; // plaintext
}
function highlightToolResult(text, lang) {
  if (!text || typeof hljs === 'undefined') return esc(text || '');
  if (!lang || lang === 'plaintext') return esc(text);
  try {
    if (lang === 'shell') {
      // Shell output isn't a language per se; use bash for the few highlight cues
      // (paths, quoted strings) without forcing structure on free-form stdout.
      return hljs.highlight(text, { language: 'bash', ignoreIllegals: true }).value;
    }
    if (hljs.getLanguage(lang)) {
      return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
    }
  } catch(e) {}
  return esc(text);
}
// Detects the server's _apply_tool_result_budget preview stub — when a >50KB
// result was spilled to disk and replaced with a preview after a turn/reload.
// Shape: "[Output too large (NkB). Full output saved to: PATH]\nPreview ...".
function _isToolResultStub(s) {
  return typeof s === 'string' && /^\[Output too large \(\d+KB\)\. Full output saved to:/.test(s);
}
// For web_fetch results, pull out how the content was produced so the chat
// view can badge it: crawl4ai (headless render) / markitdown (HTML→md) / raw.
// Lets the user see what transform the LLM's content went through, instead of
// guessing from raw-looking text.
function _extractFetchMethod(toolName, resultStr) {
  if (toolName !== 'web_fetch' || typeof resultStr !== 'string') return '';
  try {
    const m = resultStr.match(/"fetch_method"\s*:\s*"(crawl4ai|markitdown|raw)"/);
    return m ? m[1] : '';
  } catch (e) { return ''; }
}

function buildToolResultBlock(toolName, args, resultStr, toolUseId) {
  if (!resultStr) return '';
  const fullLen = resultStr.length;
  const fetchMethod = _extractFetchMethod(toolName, resultStr);
  const lang = detectToolResultLang(toolName, args, resultStr);
  const terminal = lang === 'shell';
  const id = `tres-${++_toolResultSeq}`;
  const isStub = _isToolResultStub(resultStr);
  // Cap actual rendering at MAX so a 5MB blob doesn't lock the browser; copy
  // still gets the rendered slice, but Download always gets the complete,
  // uncapped output (full).
  const renderable = fullLen > TOOL_RESULT_MAX_RENDER
    ? resultStr.substring(0, TOOL_RESULT_MAX_RENDER)
    : resultStr;
  const truncatedAtRender = fullLen > TOOL_RESULT_MAX_RENDER;
  const truncatedInitial = renderable.length > TOOL_RESULT_INITIAL_CHARS;
  const initial = truncatedInitial
    ? renderable.substring(0, TOOL_RESULT_INITIAL_CHARS)
    : renderable;
  _toolResultStore.set(id, {
    full: renderable, complete: resultStr, toolName, lang, terminal, fullLen, truncatedAtRender,
    // When the in-DOM copy is the server's preview stub (>50KB result spilled
    // to disk on a prior turn/reload), Download fetches the complete output
    // from the server by (session, tool_use_id) instead of saving the stub.
    stub: isStub,
    sessionId: state.activeChat?.sessionId || '',
    toolUseId: toolUseId || '',
  });
  const langBadge = lang ? `<span class="tool-result-lang">${esc(lang)}</span>` : '';
  const sizeBadge = `<span class="tool-result-lang">${formatBytes(fullLen)}</span>`;
  // Fetch-method badge (web_fetch only): how the content reached the LLM.
  const _fmTip = {
    crawl4ai: 'In einem Headless-Browser gerendert (Seite benötigt JavaScript)',
    markitdown: 'HTML zu Markdown konvertiert',
    raw: 'Rohinhalt, keine Konvertierung',
  }[fetchMethod] || '';
  const fetchBadge = fetchMethod
    ? `<span class="tool-result-fetch-badge" data-fm="${fetchMethod}" title="${esc(_fmTip)}">${esc(fetchMethod)}</span>`
    : '';
  const expandLabel = truncatedInitial ? 'Vollständig anzeigen' : 'Aufklappen';
  const expandBtn = `<button type="button" class="tool-result-btn" data-tres-expand="${id}" onclick="event.stopPropagation(); expandToolResult('${id}', this)">${expandLabel}</button>`;
  const copyBtn = `<button type="button" class="tool-result-btn" onclick="event.stopPropagation(); copyToolResult('${id}', this)">Kopieren</button>`;
  const downloadBtn = `<button type="button" class="tool-result-btn" onclick="event.stopPropagation(); downloadToolResult('${id}', this)">Herunterladen</button>`;
  const highlighted = highlightToolResult(initial, lang);
  const langCls = lang ? ` language-${lang}` : '';
  const termCls = terminal ? ' terminal' : '';
  const truncNote = truncatedAtRender
    ? `<div class="tool-result-truncated-note">Ausgabe überschreitet ${formatBytes(TOOL_RESULT_MAX_RENDER)}; Darstellung begrenzt. „Kopieren“ liefert den dargestellten Ausschnitt; „Herunterladen“ liefert die vollständige Ausgabe.</div>`
    : '';
  return `<div class="tool-result-section">
    <div class="tool-result-header">
      <span class="tool-result-label">Antwort</span>
      ${fetchBadge}
      ${langBadge}
      ${sizeBadge}
      <span class="tool-result-actions">${expandBtn}${copyBtn}${downloadBtn}</span>
    </div>
    <pre class="tool-result-pre${termCls}" data-tres-id="${id}"><code class="hljs${langCls}">${highlighted}</code></pre>
    ${truncNote}
  </div>`;
}
function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
function expandToolResult(id, btn) {
  const entry = _toolResultStore.get(id);
  if (!entry) return;
  const pre = document.querySelector(`pre[data-tres-id="${id}"]`);
  if (!pre) return;
  const code = pre.querySelector('code');
  if (code) code.innerHTML = highlightToolResult(entry.full, entry.lang);
  pre.classList.add('expanded');
  if (btn) btn.remove();
}
function copyToolResult(id, btn) {
  const entry = _toolResultStore.get(id);
  if (!entry) return;
  const text = entry.full;
  const done = () => {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = 'Kopiert';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}
// Download the COMPLETE, uncapped tool output (entry.complete), not the
// render-capped slice. Extension follows the detected language.
const _LANG_TO_EXT = {
  python: 'py', javascript: 'js', typescript: 'ts', tsx: 'tsx', jsx: 'jsx',
  go: 'go', rust: 'rs', java: 'java', c: 'c', cpp: 'cpp', csharp: 'cs',
  ruby: 'rb', php: 'php', swift: 'swift', kotlin: 'kt', bash: 'sh',
  json: 'json', yaml: 'yaml', toml: 'toml', ini: 'ini', xml: 'xml',
  css: 'css', scss: 'scss', markdown: 'md', sql: 'sql', dockerfile: 'dockerfile',
  shell: 'txt',
};
function _saveBlobAs(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadToolResult(id, btn) {
  const entry = _toolResultStore.get(id);
  if (!entry) return;
  const ext = _LANG_TO_EXT[entry.lang] || 'txt';
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const base = (entry.toolName || 'tool-output').replace(/[^a-z0-9_-]+/gi, '_');
  const filename = `${base}_${ts}.${ext}`;
  const flash = (label) => {
    if (!btn) return;
    const orig = btn._origLabel || (btn._origLabel = btn.textContent);
    btn.textContent = label;
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1200);
  };

  // When the in-DOM copy is the server's >50KB preview stub, fetch the complete
  // output the budget pass spilled to disk (reload-stable full download).
  if (entry.stub && entry.sessionId && entry.toolUseId) {
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
      const t = localStorage.getItem('auth-token');
      const r = await fetch(
        `${BASE_URL}/v1/tools/result?session_id=${encodeURIComponent(entry.sessionId)}&tool_use_id=${encodeURIComponent(entry.toolUseId)}`,
        { headers: t ? { Authorization: `Bearer ${t}` } : {} });
      if (r.ok) {
        _saveBlobAs(await r.blob(), filename);
        if (btn) { btn.disabled = false; }
        flash('Gespeichert');
        return;
      }
      // Fall through to saving the stub if the server has no persisted copy.
      if (typeof showToast === 'function') showToast('Vollständige Ausgabe nicht auf dem Server — Vorschau wird gespeichert', true);
    } catch (e) {
      if (typeof showToast === 'function') showToast('Download fehlgeschlagen — Vorschau wird gespeichert', true);
    }
    if (btn) { btn.disabled = false; }
  }

  const text = entry.complete != null ? entry.complete : entry.full;
  _saveBlobAs(new Blob([text], { type: 'text/plain;charset=utf-8' }), filename);
  flash('Gespeichert');
}
function fallbackCopy(text, cb) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed'; ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); cb && cb(); } catch(e) {}
  document.body.removeChild(ta);
}
function renderToolCall(msg, idx) {
  // Transparent-anonymisation synthetic rows render distinctly — they're not
  // LLM tool calls, they're server-side privacy operations the user should
  // ALWAYS see in their chat history (independent of the "show tool calls"
  // toggle, which only affects the model's tool calls). Renders shield-icon
  // + summary; click expands.
  if (msg.synthetic) {
    return renderSyntheticGdprCall(msg, idx);
  }
  if (!state.showToolCalls) return '';
  // Look ahead for matching tool_result — match by tool_use_id when available,
  // fall back to name. Don't stop at sibling tool_calls (parallel batches interleave).
  const chat = state.activeChat;
  let resultMsg = null;
  if (chat) {
    for (let j = idx + 1; j < chat.messages.length; j++) {
      const next = chat.messages[j];
      if (next.role === 'tool_result') {
        const idMatch = msg.tool_use_id && next.tool_use_id && msg.tool_use_id === next.tool_use_id;
        const nameMatch = !msg.tool_use_id && next.name === msg.name;
        if (idMatch || nameMatch) { resultMsg = next; break; }
      }
      if (next.role === 'assistant' || next.role === 'user') break;
    }
  }
  const desc = toolDescribe(msg.name, msg.args);
  const args = typeof msg.args === 'string' ? {} : (msg.args || {});
  const hasResult = resultMsg !== null;
  const duration = (hasResult && msg._ts && resultMsg._ts) ? ((resultMsg._ts - msg._ts) / 1000).toFixed(1) : null;
  // Check if this tool is running through a worker (live or completed)
  const isRunningWorker = !hasResult && msg._ts && Object.values(state.activeWorkers).some(
    w => w.tool_name === msg.name && w.state === 'RUNNING'
  );
  const liveElapsed = isRunningWorker ? ((Date.now() - msg._ts) / 1000).toFixed(0) : null;
  const icon = hasResult ? `<span class="tool-icon" style="color:var(--success)">&#10003;</span>` : `<span class="tool-icon tool-icon-spin">&#9881;</span>`;
  const timing = duration ? `<span class="tool-timing">${duration}s</span>` : (liveElapsed ? `<span class="tool-timing">${liveElapsed}s</span>` : '');

  const displayArgs = msg.name === 'python_exec' ? Object.fromEntries(Object.entries(args).filter(([k]) => k !== 'code')) : args;
  let bodyHtml = renderToolArgsTable(displayArgs);
  let isWorker = false;
  let resultStrForFlow = null;
  if (hasResult) {
    const resultStr = typeof resultMsg.result === 'string' ? resultMsg.result : JSON.stringify(resultMsg.result, null, 2);
    resultStrForFlow = resultStr;
    // Permissive check mirrors session inspector: handles stringified envelopes,
    // objects with nested `worker: true`, and survives truncation.
    if (resultStr.includes('"worker": true') || resultStr.includes('"worker":true')) {
      isWorker = true;
    } else {
      try { const rj = JSON.parse(resultStr); if (rj && rj.worker) isWorker = true; } catch(e) {}
    }
    bodyHtml += buildToolResultBlock(msg.name, args, resultStr, resultMsg.tool_use_id || msg.tool_use_id || '');
  }
  // Worker flow: shown when the tool ran (or is running) via a worker
  if (isWorker || isRunningWorker) {
    const wf = findWorkerFlow(msg.name, resultStrForFlow);
    if (wf) bodyHtml = renderWorkerFlow(wf) + bodyHtml;
  }
  const workerBadge = (isWorker || isRunningWorker) ? '<span class="tool-badge-worker" title="Über Worker-Subagent ausgeführt">Hintergrund</span>' : '';
  // Extraction-backend badge for read_document: which of the two surfaces
  // produced the text — markitdown (tried first) or our own fallback
  // (_extract_*), or OCR for scanned PDFs. Reads the `backend` field the
  // tool result carries; only present on read_document.
  let backendBadge = '';
  if (resultStrForFlow) {
    // Cheap regex extract of the short `backend` field — avoids a full
    // JSON.parse of the (up to 50KB) result string on every render.
    const bm = resultStrForFlow.match(/"backend"\s*:\s*"([^"]+)"/);
    if (bm) {
      const b = bm[1];
      let label, title;
      if (b.startsWith('markitdown')) { label = 'markitdown'; title = 'Extrahiert via markitdown (Primärpfad)'; }
      else if (b.includes('ocr') || b.includes('vision')) { label = 'OCR'; title = `Extrahiert via OCR (${b})`; }
      else { label = 'fallback'; title = `Extrahiert via interner Fallback (${b})`; }
      backendBadge = `<span class="tool-badge-backend" title="${title}">${label}</span>`;
    }
  }
  // Parallel badge: shown when 2+ tool_calls share the same tool_round
  const toolRound = msg.tool_round;
  const isParallel = toolRound != null && chat && chat.messages.filter(
    m => m.role === 'tool_call' && m.tool_round === toolRound
  ).length > 1;
  const parallelBadge = isParallel ? '<span class="tool-badge-parallel" title="Parallel ausgeführt">Parallel</span>' : '';

  // One flat line per tool call: icon + title + badges + timing. Click opens the
  // Aktivitäts-Panel (full args + result + copy/download live there).
  // Chat view shows ONLY the tool's title (desc) — no params, no result-JSON
  // preview (that was noise: trailing {"query":…}/{"url":…}). The full args +
  // result stay one click away in the Aktivitäts-Panel.
  const preview = '';
  const actId = msg.tool_use_id || ('tc-' + (msg._seq || idx));
  return `
    <div class="tool-line${hasResult ? ' has-result' : ''}" title="Im Aktivitäts-Panel öffnen"
         onclick="openActivityEntry('${esc(actId)}')">
      ${icon}
      <span class="tool-name">${desc}</span>
      ${workerBadge}${parallelBadge}${backendBadge}
      ${timing}
      ${preview}
    </div>
  `;
}
function renderSyntheticGdprCall(msg, idx) {
  // Pair this dispatch with its matching done row (look forward; same
  // tool_use_id, or same `kind` if no id). Synthetic pairs never get a
  // real LLM response between them, but be defensive.
  const chat = state.activeChat;
  let done = null;
  if (chat) {
    for (let j = idx + 1; j < chat.messages.length; j++) {
      const next = chat.messages[j];
      if (next.role === 'tool_result' && next.synthetic) {
        const idMatch = msg.tool_use_id && next.tool_use_id && msg.tool_use_id === next.tool_use_id;
        const kindMatch = !msg.tool_use_id && next.kind === msg.kind;
        if (idMatch || kindMatch) { done = next; break; }
      }
      if (next.role === 'assistant' || next.role === 'user') break;
    }
  }
  const kind = msg.kind || msg.name || 'anonymise';
  const status = done?.status || 'pending';
  const result = done?.result || {};

  const titleMap = {
    anonymise: 'Anonymisiert',
    anonymise_read: 'Tool-Ausgabe anonymisiert',
    deanonymise_text: 'Antwort wiederhergestellt',
    deanonymise_file: 'Datei wiederhergestellt',
  };
  const title = titleMap[kind] || kind;

  let summary = '';
  if (kind === 'anonymise' && status === 'ok') {
    const n = result.findings ?? 0;
    const cats = Object.keys(result.categories || {});
    const catLabel = cats.length ? ' · ' + cats.join(', ') : '';
    const pending = Array.isArray(result.pending_on_read) ? result.pending_on_read : [];
    const pendNote = pending.length ? ` · ${pending.length} Datei${pending.length === 1 ? '' : 'en'} ausstehend` : '';
    const mapNote = result.mapping === 'reused' ? ' · Session-Mapping wiederverwendet' : '';
    summary = `Chat-Text: ${n} Treffer${catLabel}${pendNote}${mapNote}`;
  } else if (kind === 'anonymise' && status === 'error') {
    summary = String(result.error || 'fehlgeschlagen').slice(0, 200);
  } else if (kind === 'anonymise_read' && status === 'ok') {
    const n = result.findings ?? 0;
    const minted = result.tokens_minted ?? 0;
    const cats = Object.keys(result.categories || {});
    const catLabel = cats.length ? ' · ' + cats.join(', ') : '';
    summary = `${result.source || 'Tool-Ausgabe'}: ${n} Treffer · ${minted} neue${minted === 1 ? 's' : ''} Token${catLabel}`;
  } else if (kind === 'deanonymise_text') {
    const n = result.restored ?? 0;
    summary = `${n} Token wiederhergestellt`;
  } else if (kind === 'deanonymise_file') {
    summary = (result.file || '') + ' · ' + (result.restored ?? 0) + ' wiederhergestellt';
  }

  // Status icon: green check / spinner / red x.
  let iconHtml;
  if (status === 'pending') {
    iconHtml = '<span class="tool-icon tool-icon-spin">&#9881;</span>';
  } else if (status === 'error') {
    iconHtml = '<span class="tool-icon" style="color:var(--danger,#dc2626)">&#10005;</span>';
  } else {
    iconHtml = '<span class="tool-icon" style="color:var(--success)">&#10003;</span>';
  }

  const ms = done?.duration_ms ?? 0;
  const timing = ms ? `<span class="tool-timing">${(ms / 1000).toFixed(1)}s</span>` : '';

  // Body: key/value pairs from result + args. Keeps PII out — only counts,
  // categories, sources, mapping_id; never actual values.
  const safeArgs = msg.args || {};
  const rows = [];
  if (safeArgs.sources) rows.push(['Quellen', safeArgs.sources.join(', ')]);
  if (safeArgs.source) rows.push(['Quelle', String(safeArgs.source)]);
  if (safeArgs.scope) rows.push(['Bereich', String(safeArgs.scope)]);
  if (Array.isArray(safeArgs.pending_on_read) && safeArgs.pending_on_read.length)
    rows.push(['Ausstehend (beim Lesen)', safeArgs.pending_on_read.join(', ')]);
  if (result.scope) rows.push(['Bereich', String(result.scope)]);
  if (result.source) rows.push(['Quelle', String(result.source)]);
  if (result.findings != null) rows.push(['Treffer', String(result.findings)]);
  if (result.restored != null) rows.push(['Wiederhergestellt', String(result.restored)]);
  if (result.categories) rows.push(['Kategorien', Object.entries(result.categories).map(([k, v]) => `${k}=${v}`).join(', ')]);
  if (result.tokens_minted != null) rows.push(['Neue Token', String(result.tokens_minted)]);
  if (Array.isArray(result.pending_on_read) && result.pending_on_read.length)
    rows.push(['Ausstehend (beim Lesen)', result.pending_on_read.join(', ')]);
  if (result.mapping_id) rows.push(['Mapping-ID', result.mapping_id]);
  if (status === 'error' && result.error) rows.push(['Fehler', String(result.error)]);
  const bodyHtml = rows.length
    ? '<table class="tool-args-table"><tbody>' +
        rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') +
      '</tbody></table>'
    : '<div style="font-size:12px;color:var(--text-300);">Keine weiteren Details.</div>';

  // Shield icon as a per-row marker so users can recognise these at a glance.
  const shieldBadge = '<span class="tool-badge-synthetic" title="Serverseitige Datenschutz-Operation" '
    + 'style="font-size:10.5px;font-weight:600;padding:2px 6px;border-radius:8px;'
    + 'background:rgba(4,120,87,.12);color:#047857;letter-spacing:.02em;">DATENSCHUTZ</span>';

  return `
    <div class="tool-block tool-block-synthetic${done ? ' has-result' : ''}" onclick="this.classList.toggle('open')">
      <div class="tool-block-header">
        ${iconHtml}
        <span class="tool-name">🛡️ ${esc(title)}${summary ? ': ' + esc(summary) : ''}</span>
        ${shieldBadge}
        ${timing}
        <span class="tool-chevron">&#9656;</span>
      </div>
      <div class="tool-block-body">${bodyHtml}</div>
    </div>
  `;
}
function renderToolResult(msg, idx) {
  // Synthetic results are rendered inside their dispatch row above.
  if (msg.synthetic) return '';
  // Tool results are now rendered inside their tool_call block
  // Only render standalone if no preceding tool_call found
  if (!state.showToolCalls) return '';
  const chat = state.activeChat;
  if (chat) {
    for (let j = idx - 1; j >= 0; j--) {
      const prev = chat.messages[j];
      if (prev.role === 'tool_call' && prev.name === msg.name) return ''; // already rendered inside tool_call
      if (prev.role === 'assistant' || prev.role === 'user') break;
    }
  }
  // Standalone result (no matching call found)
  const resultStr = typeof msg.result === 'string' ? msg.result : JSON.stringify(msg.result, null, 2);
  const desc = toolDescribe(msg.name, {});
  const block = buildToolResultBlock(msg.name, {}, resultStr);
  return `
    <div class="tool-block has-result" onclick="this.classList.toggle('open')">
      <div class="tool-block-header">
        <span class="tool-icon" style="color:var(--success)">&#10003;</span>
        <span class="tool-name">${desc}</span>
        <span class="tool-chevron">&#9656;</span>
      </div>
      <div class="tool-block-body">${block}</div>
    </div>
  `;
}
