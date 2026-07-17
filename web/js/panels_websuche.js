/* ───────────────────────────────────────────────────────────
   Websuche — manual web-search curation (right-panel tab).

   The user runs SearXNG searches (POST /v1/web/search — search only, no
   fetch, no LLM), inspects a Google-style SERP, and marks results into a
   persistent "basket". Manual URLs and dropped links go into the same
   basket. On send, chat_send.js reads the ENABLED entries, passes them as
   body.web_urls_to_fetch + exclude_tools; the server pre-fetches them and
   injects the content (handlers/chat.py).

   The basket is PER-SESSION and persisted SERVER-SIDE: it lives on the active
   chat (state.activeChat.webBasket) and is saved to the session row
   (sessions.web_basket via manage action 'web_basket'), exactly like a chat's
   own attachments belong to that session. A fresh chat starts empty; opening a
   session loads ITS basket (from GET /messages → data.web_basket). Sources can
   never leak from one chat into another. For a brand-new (not-yet-saved) chat
   the basket lives in memory on the chat object and is flushed to the server
   once the session id exists (the next manual edit persists it; sending also
   carries the enabled set). Entries: { url, title, snippet, query, enabled }.
   Dedup by url, within a session.
   ─────────────────────────────────────────────────────────── */

let _webSearchResults = [];   // last SERP (transient, not persisted)
let _webSearchBusy = false;

// The basket array lives on the active chat so it's naturally per-session and
// swaps automatically when the chat switches. Returns a live array reference.
function _webBasketArr() {
  try {
    const chat = state.activeChat;
    if (!chat) return [];
    if (!Array.isArray(chat.webBasket)) chat.webBasket = [];
    return chat.webBasket;
  } catch (e) { return []; }
}

// Persist the active chat's basket to its session row. No-op until the session
// has an id (lazy chats); the basket is held in memory until then.
function _saveWebBasket() {
  try {
    const chat = state.activeChat;
    const sid = chat && chat.sessionId;
    if (!sid) return;
    API.post('/v1/sessions/manage', {
      action: 'web_basket', session_id: sid, value: _webBasketArr(),
    }).catch(() => {});
  } catch (e) {}
}

// Replace the active chat's basket from a JSON string (server load). Called by
// openSession after GET /messages.
function webBasketLoadFromJson(jsonStr) {
  const chat = state.activeChat;
  if (!chat) return;
  let arr = [];
  try { const p = jsonStr ? JSON.parse(jsonStr) : []; if (Array.isArray(p)) arr = p; } catch (e) {}
  chat.webBasket = arr;
  if (typeof _refreshWebsuche === 'function') _refreshWebsuche();
}

function webBasketCount() { return _webBasketArr().length; }

// Enabled entries — what a chat send will actually fetch.
function webBasketEnabled() { return _webBasketArr().filter(e => e.enabled); }

function _webBasketHas(url) { return _webBasketArr().some(e => e.url === url); }

// Add an entry (from SERP, manual, or drop). Dedup by url; enabled by default.
function addToWebBasket(url, title, snippet, query) {
  url = (url || '').trim();
  if (!url) return false;
  if (_webBasketHas(url)) return false;
  _webBasketArr().push({ url, title: (title || '').trim() || url,
                    snippet: snippet || '', query: query || '', enabled: true });
  _saveWebBasket();
  _refreshWebsuche();
  return true;
}

function removeFromWebBasket(url) {
  const chat = state.activeChat;
  if (chat) chat.webBasket = _webBasketArr().filter(e => e.url !== url);
  _saveWebBasket();
  _refreshWebsuche();
}

function toggleWebBasketEntry(url) {
  const e = _webBasketArr().find(x => x.url === url);
  if (e) { e.enabled = !e.enabled; _saveWebBasket(); _refreshWebsuche(); }
}

// ═══ Quellen-Pinning (v9.305.0) — DISTINCT from the Websuche basket ═════════
// Pinned PROJECT documents whose FULL text is injected wire-only into every
// send of this session (server seam: handlers/chat._build_pinned_sources).
// Persisted per session (sessions.pinned_sources) like the basket, but a
// different store + a different server mechanism — do NOT merge the two.

function _pinnedArr() {
  try {
    const chat = state.activeChat;
    if (!chat) return [];
    if (!Array.isArray(chat.pinnedSources)) chat.pinnedSources = [];
    return chat.pinnedSources;
  } catch (e) { return []; }
}

// Enabled entries — what a chat send will actually inject.
function pinnedSourcesEnabled() { return _pinnedArr().filter(e => e.enabled); }

// Replace the active chat's pinned set from a JSON string (server load).
// Called by openSession after GET /messages.
function pinnedSourcesLoadFromJson(jsonStr) {
  const chat = state.activeChat;
  if (!chat) return;
  let arr = [];
  try { const p = jsonStr ? JSON.parse(jsonStr) : []; if (Array.isArray(p)) arr = p; } catch (e) {}
  chat.pinnedSources = arr;
  if (typeof updateStatusBar === 'function') updateStatusBar();
}

function _savePinnedSources() {
  try {
    const chat = state.activeChat;
    const sid = chat && chat.sessionId;
    if (!sid) return;   // lazy chat — held in memory until the session exists
    API.post('/v1/sessions/manage', {
      action: 'pinned_sources', session_id: sid, value: _pinnedArr(),
    }).catch(() => {});
  } catch (e) {}
}

function togglePinnedSource(key, name, checked) {
  const arr = _pinnedArr();
  const i = arr.findIndex(e => e.key === key);
  if (checked && i < 0) arr.push({ key, name, enabled: true });
  else if (!checked && i >= 0) arr.splice(i, 1);
  _savePinnedSources();
  const cnt = document.getElementById('pin-modal-count');
  if (cnt) cnt.textContent = String(pinnedSourcesEnabled().length);
  if (typeof updateStatusBar === 'function') updateStatusBar();
}

// Composer 📌 button → list the project's sources with pin checkboxes.
async function openPinnedSourcesModal() {
  const chat = state.activeChat;
  if (!chat || !chat.project) {
    showToast('Quellen-Pinning gibt es nur in Projekt-Chats', true);
    return;
  }
  let sources = [];
  try {
    const data = await API.getProjectSources(chat.agent || 'main', chat.project);
    sources = data.sources || [];
  } catch (e) {
    showToast('Quellen konnten nicht geladen werden: ' + (e.message || e), true);
    return;
  }
  const pinnedKeys = new Set(_pinnedArr().map(e => e.key));
  const kindLabel = { upload: 'Upload', file: 'Ordner', weburl: 'Web' };
  const rows = sources.map(s => `
    <label style="display:flex;align-items:center;gap:8px;padding:6px 4px;border-bottom:1px solid var(--border-100);cursor:pointer;font-size:12px">
      <input type="checkbox" ${pinnedKeys.has(s.key) ? 'checked' : ''}
             onchange="togglePinnedSource('${esc(s.key).replace(/'/g, '&#39;')}', '${esc(s.name).replace(/'/g, '&#39;')}', this.checked)">
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(s.name)}">${esc(s.name)}</span>
      <span style="font-size:10px;color:var(--text-400);border:1px solid var(--border-200);border-radius:4px;padding:1px 6px">${esc(kindLabel[s.kind] || s.kind)}</span>
    </label>`).join('');
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.display = 'flex';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `<div class="modal-content" style="max-width:560px;width:92vw;max-height:80vh;display:flex;flex-direction:column" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div class="modal-title">Projekt-Quellen anpinnen (<span id="pin-modal-count">${pinnedSourcesEnabled().length}</span> aktiv)</div>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="overflow:auto;flex:1">
      <div style="font-size:12px;color:var(--text-400);margin-bottom:8px">
        Angepinnte Dokumente werden mit ihrem <b>Volltext</b> in jede Anfrage dieses
        Chats eingespeist (max. 12 Quellen, je bis 60k Zeichen) — das Modell muss sie
        nicht erst per Suche finden. Gilt nur für diesen Chat; nichts davon landet im
        gespeicherten Verlauf. Viele große Quellen erhöhen Kosten und Antwortzeit.
      </div>
      ${rows || '<div style="padding:14px;color:var(--text-400);font-size:13px">Dieses Projekt hat keine lesbaren Quellen.</div>'}
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

function webBasketBulk(op) {
  const chat = state.activeChat;
  if (op === 'enable') _webBasketArr().forEach(e => e.enabled = true);
  else if (op === 'disable') _webBasketArr().forEach(e => e.enabled = false);
  else if (op === 'clear') {
    if (_webBasketArr().length && !confirm('Alle ausgewählten Quellen entfernen?')) return;
    if (chat) chat.webBasket = [];
  }
  _saveWebBasket();
  _refreshWebsuche();
}

// Rough token estimate of the ENABLED set. Snippet length is a poor proxy for
// full-page weight (pages are fetched server-side only on send), so this is
// labelled an estimate. ~4 chars/token; assume a fetched page averages ~3000
// tokens when we have no snippet to go on.
function _webBasketTokenEstimate() {
  let chars = 0;
  for (const e of webBasketEnabled()) {
    chars += e.snippet ? e.snippet.length * 8 : 12000;  // snippet underestimates full page
  }
  return Math.round(chars / 4);
}

async function runWebSearch() {
  if (_webSearchBusy) return;
  const input = document.getElementById('websuche-query');
  const query = (input?.value || '').trim();
  if (!query) return;
  _webSearchBusy = true;
  const resultsEl = document.getElementById('websuche-results');
  if (resultsEl) resultsEl.innerHTML = '<div class="websuche-loading">Suche läuft…</div>';
  try {
    const resp = await API.webSearch(query);
    _webSearchResults = (resp.results || []).map(r => ({
      title: r.title || r.link, link: r.link, snippet: r.snippet || '', query,
    }));
    if (resp.error && !_webSearchResults.length) {
      if (resultsEl) resultsEl.innerHTML =
        `<div class="websuche-empty">${esc(resp.error)}</div>`;
    } else {
      renderWebSerp();
    }
  } catch (e) {
    if (resultsEl) resultsEl.innerHTML =
      `<div class="websuche-empty">Suche fehlgeschlagen: ${esc(e.message)}</div>`;
  } finally {
    _webSearchBusy = false;
  }
}

// Google-style SERP: title link, green URL, snippet, checkbox to add/remove.
function renderWebSerp() {
  const el = document.getElementById('websuche-results');
  if (!el) return;
  if (!_webSearchResults.length) {
    el.innerHTML = '<div class="websuche-empty">Keine Ergebnisse</div>';
    return;
  }
  el.innerHTML = _webSearchResults.map(r => {
    const inBasket = _webBasketHas(r.link);
    let host = r.link;
    try { host = new URL(r.link).hostname.replace(/^www\./, ''); } catch (e) {}
    return `
      <div class="websuche-result ${inBasket ? 'in-basket' : ''}">
        <label class="websuche-result-check">
          <input type="checkbox" ${inBasket ? 'checked' : ''}
                 onchange="onSerpToggle('${esc(r.link)}', this.checked)">
        </label>
        <div class="websuche-result-body">
          <a class="websuche-result-title" href="${esc(r.link)}" target="_blank" rel="noopener">${esc(r.title)}</a>
          <div class="websuche-result-url">${esc(host)}</div>
          ${r.snippet ? `<div class="websuche-result-snippet">${esc(r.snippet)}</div>` : ''}
        </div>
      </div>`;
  }).join('');
}

function onSerpToggle(url, checked) {
  if (checked) {
    const r = _webSearchResults.find(x => x.link === url);
    if (r) addToWebBasket(r.link, r.title, r.snippet, r.query);
  } else {
    removeFromWebBasket(url);
  }
}

function addManualWebUrl() {
  const input = document.getElementById('websuche-manual-url');
  let url = (input?.value || '').trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  if (addToWebBasket(url, '', '', '(manuell)') && input) input.value = '';
}

// Drag & drop: accept dropped links / text containing URLs.
function onWebBasketDragOver(e) { e.preventDefault(); e.currentTarget.classList.add('drag-over'); }
function onWebBasketDragLeave(e) { e.currentTarget.classList.remove('drag-over'); }
function onWebBasketDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  const uriList = e.dataTransfer.getData('text/uri-list');
  const plain = e.dataTransfer.getData('text/plain');
  const urls = new Set();
  if (uriList) uriList.split(/\r?\n/).forEach(l => { l = l.trim(); if (l && !l.startsWith('#')) urls.add(l); });
  if (plain) (plain.match(/https?:\/\/[^\s"'<>]+/g) || []).forEach(u => urls.add(u));
  let added = 0;
  urls.forEach(u => { if (addToWebBasket(u, '', '', '(drop)')) added++; });
}

// Render the basket list (enable/disable toggle + remove per entry).
function renderWebsuchePane() {
  renderWebSerp();
  const basketEl = document.getElementById('websuche-basket');
  const countEl = document.getElementById('websuche-basket-count');
  const tokensEl = document.getElementById('websuche-basket-tokens');
  const allowRow = document.getElementById('websuche-allow-row');
  const allowCb = document.getElementById('websuche-allow-further');
  const basket = _webBasketArr();
  const enabledN = webBasketEnabled().length;
  if (countEl) countEl.textContent = `(${basket.length} · ${enabledN} aktiv)`;
  if (tokensEl) tokensEl.textContent = enabledN ? `~${_webBasketTokenEstimate().toLocaleString()} Tokens (Schätzung)` : '';
  // The allow-further-web checkbox is only meaningful with enabled sources.
  if (allowRow) allowRow.classList.toggle('disabled', enabledN === 0);
  if (allowCb) {
    allowCb.disabled = enabledN === 0;
    const sess = state.activeChat;
    allowCb.checked = !!(sess && sess.allowFurtherWeb);
  }
  if (!basketEl) return;
  if (!basket.length) {
    basketEl.innerHTML = '<div class="websuche-empty">Noch keine Quellen ausgewählt.<br>Suche oben, hake Ergebnisse an, füge URLs hinzu oder ziehe Links hierher.</div>';
    return;
  }
  basketEl.innerHTML = basket.map(e => {
    let host = e.url;
    try { host = new URL(e.url).hostname.replace(/^www\./, ''); } catch (x) {}
    return `
      <div class="websuche-basket-item ${e.enabled ? '' : 'disabled'}">
        <label class="websuche-basket-toggle" title="${e.enabled ? 'Aktiviert' : 'Deaktiviert'}">
          <input type="checkbox" ${e.enabled ? 'checked' : ''} onchange="toggleWebBasketEntry('${esc(e.url)}')">
        </label>
        <div class="websuche-basket-body">
          <a class="websuche-basket-title" href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>
          <div class="websuche-basket-url">${esc(host)}</div>
        </div>
        <button class="websuche-basket-remove" title="Entfernen" onclick="removeFromWebBasket('${esc(e.url)}')">✕</button>
      </div>`;
  }).join('');
}

// Re-render the pane + badge whenever the basket changes.
function _refreshWebsuche() {
  if (typeof updateRightPanelBadges === 'function') updateRightPanelBadges();
  if (state.rightPanelOpen && state.rightPanelTab === 'websuche') renderWebsuchePane();
}

// Persist the per-session escape-hatch checkbox.
async function toggleAllowFurtherWeb(checked) {
  const sess = state.activeChat;
  if (sess) sess.allowFurtherWeb = checked;
  const sid = sess?.sessionId;
  if (!sid) return;
  try { await API.manageSession({ session_id: sid, action: 'allow_further_web', value: checked }); }
  catch (e) {}
}


/* ───────────────────────────────────────────────────────────
   Datenquellen — per-session db_query scope (right-panel tab).
   SEPARATE mechanism from the Websuche basket above (shares only the file):
   the selection [{name, tables:[]}] decides which EXTERNAL DB SOURCES a
   plain (project-less) chat may query via db_query — enforcement is
   server-side (handlers/chat.py sets data_source_scope per turn; the tool
   gate denies everything unscoped). Persisted per session
   (sessions.data_sources via manage action 'data_sources', the web_basket
   pattern — never localStorage). In PROJECT sessions the tab shows the
   project scope READ-ONLY (project.json decides there — one source of
   truth per context, E9). Tab is hidden while /v1/data-sources/available
   is empty (no grant / nothing configured).
   ─────────────────────────────────────────────────────────── */

function _dsSelArr() {
  try {
    const chat = state.activeChat;
    if (!chat) return [];
    if (!Array.isArray(chat.dataSources)) chat.dataSources = [];
    return chat.dataSources;
  } catch (e) { return []; }
}

function _saveDataSourcesSel() {
  const chat = state.activeChat;
  const sid = chat && chat.sessionId;
  if (!sid) return;
  API.post('/v1/sessions/manage', {
    action: 'data_sources', session_id: sid, value: _dsSelArr(),
  }).catch(() => {});
}

// Called by openSession after GET /messages (and by newChat with '').
function dataSourcesLoadFromJson(jsonStr) {
  const chat = state.activeChat;
  if (!chat) return;
  let arr = [];
  try { const p = jsonStr ? JSON.parse(jsonStr) : []; if (Array.isArray(p)) arr = p; } catch (e) {}
  chat.dataSources = arr;
  _dsUpdateTab();
  if (state.rightPanelOpen && state.rightPanelTab === 'datenquellen') renderDatenquellenPane();
}

// Tab button visibility + badge. Availability is fetched ONCE per page load
// (cache shared with the project-settings section in panels_projects.js).
async function _dsUpdateTab() {
  const btn = document.getElementById('tab-btn-datenquellen');
  const badge = document.getElementById('tab-badge-datenquellen');
  if (badge) {
    const n = _dsSelArr().length;
    badge.textContent = String(n);
    badge.style.display = n ? '' : 'none';
  }
  if (!btn) return;
  if (!state._dsAvailCache) {
    try { state._dsAvailCache = await API.get('/v1/data-sources/available'); }
    catch (e) { btn.style.display = 'none'; return; }
  }
  const has = (state._dsAvailCache?.sources || []).length > 0;
  btn.style.display = (has || state.activeChat?.project) ? '' : 'none';
}

async function renderDatenquellenPane() {
  const el = document.getElementById('datenquellen-content');
  if (!el) return;
  const chat = state.activeChat;
  // PROJECT session → read-only view of the project scope (E9).
  if (chat?.project) {
    el.innerHTML = '<div style="color:var(--text-400);font-size:12px">Lade Projekt-Konfiguration…</div>';
    let pcfg = null;
    try { pcfg = await API.getProject(chat.agent || 'main', chat.project); } catch (e) {}
    const list = (pcfg?.data_sources || []);
    el.innerHTML = `<div style="font-size:12px;color:var(--text-400);margin-bottom:10px">
        Dieser Chat gehört zum Projekt <b>${esc(chat.project)}</b> — die nutzbaren
        Datenquellen sind <b>im Projekt konfiguriert</b> (Projekt-Einstellungen → Datenquellen).</div>` +
      (list.length ? list.map(e => `<div style="padding:6px 0;border-bottom:1px solid var(--border-100);font-size:13px">
        <b>${esc(e.name)}</b>
        <div style="font-size:11px;color:var(--text-400)">${(e.tables || []).length ? 'Beschränkt auf: ' + e.tables.map(esc).join(', ') : 'alle Tabellen'}</div>
      </div>`).join('') : '<div style="font-size:12px;color:var(--text-400)">Keine Datenquellen im Projekt freigegeben.</div>');
    return;
  }
  if (!state._dsAvailCache) {
    try { state._dsAvailCache = await API.get('/v1/data-sources/available'); }
    catch (e) { el.innerHTML = '<div style="font-size:12px;color:var(--text-400)">Datenquellen nicht verfügbar.</div>'; return; }
  }
  const sources = state._dsAvailCache?.sources || [];
  if (!sources.length) {
    el.innerHTML = '<div style="font-size:12px;color:var(--text-400)">Keine Datenquellen verfügbar — entweder ist keine konfiguriert oder dir fehlt die Freigabe (Admin: Einstellungen → Datenquellen).</div>';
    return;
  }
  const sel = {};
  _dsSelArr().forEach(e => { if (e && e.name) sel[e.name] = e.tables || []; });
  const tblCache = state._dsTablesCache || {};
  el.innerHTML = `<div style="font-size:12px;color:var(--text-400);margin-bottom:10px">
      Angehakte Quellen darf dieser Chat per <code>db_query</code> abfragen;
      optional auf Tabellen einschränken (nichts gewählt = alle). Die Auswahl
      gilt nur für diese Unterhaltung.</div>` +
    sources.map(s => {
      const on = Object.prototype.hasOwnProperty.call(sel, s.name);
      const tabs = sel[s.name] || [];
      const modeBadge = s.access_mode === 'rw'
        ? '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg-200);color:var(--error)">read/write</span>'
        : '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg-200);color:var(--text-400)">read-only</span>';
      let detail = '';
      if (on) {
        const expanded = state._dsSelExpanded === s.name;
        const known = tblCache[s.name];
        let picker = '';
        if (expanded && Array.isArray(known)) {
          picker = '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">' +
            known.map(t => {
              const isSel = tabs.includes(t);
              return `<label style="font-size:11px;padding:2px 8px;border-radius:10px;cursor:pointer;border:1px solid var(--border-100);background:${isSel ? 'var(--accent)' : 'var(--bg-200)'};color:${isSel ? '#fff' : 'var(--text-200)'}">` +
                `<input type="checkbox" style="display:none" ${isSel ? 'checked' : ''} onchange="dsSelToggleTable('${esc(s.name)}','${esc(t)}')">${esc(t)}</label>`;
            }).join('') +
            `</div><div style="margin-top:4px"><button class="websuche-bulk-btn" onclick="dsSelClearTables('${esc(s.name)}')">Einschränkung aufheben</button></div>`;
        } else if (expanded) {
          picker = '<div style="margin-top:6px;font-size:11px;color:var(--text-400)">Lade Tabellen…</div>';
        }
        detail = `<div style="margin:4px 0 2px 24px;font-size:11px;color:var(--text-300)">
          ${tabs.length ? 'Beschränkt auf: <b>' + tabs.map(esc).join(', ') + '</b>' : 'alle Tabellen'}
          <button class="websuche-bulk-btn" style="margin-left:6px" onclick="dsSelPickTables('${esc(s.name)}')">${expanded ? 'Zuklappen' : 'Tabellen wählen…'}</button>
          ${picker}</div>`;
      }
      return `<div style="padding:6px 0;border-bottom:1px solid var(--border-100)">
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
          <input type="checkbox" ${on ? 'checked' : ''} onchange="dsSelToggleSource('${esc(s.name)}', this.checked)">
          <strong>${esc(s.name)}</strong>
          <span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg-200);color:var(--text-400)">${esc(s.type)}</span>
          ${modeBadge}
        </label>${detail}</div>`;
    }).join('');
}

function dsSelToggleSource(name, on) {
  const chat = state.activeChat;
  if (!chat) return;
  const list = _dsSelArr().filter(e => e && e.name !== name);
  if (on) list.push({ name, tables: [] });
  chat.dataSources = list;
  if (!on && state._dsSelExpanded === name) state._dsSelExpanded = null;
  _saveDataSourcesSel();
  _dsUpdateTab();
  renderDatenquellenPane();
}

async function dsSelPickTables(name) {
  if (state._dsSelExpanded === name) {
    state._dsSelExpanded = null;
    renderDatenquellenPane();
    return;
  }
  state._dsSelExpanded = name;
  renderDatenquellenPane();
  state._dsTablesCache = state._dsTablesCache || {};
  if (!Array.isArray(state._dsTablesCache[name])) {
    try {
      const r = await API.get('/v1/data-sources/' + encodeURIComponent(name) + '/tables');
      if (r?.error) { showToast(r.error, true); state._dsSelExpanded = null; }
      else state._dsTablesCache[name] = r?.tables || [];
    } catch (e) {
      showToast('Tabellenliste nicht verfügbar', true);
      state._dsSelExpanded = null;
    }
    renderDatenquellenPane();
  }
}

function dsSelToggleTable(name, table) {
  const entry = _dsSelArr().find(e => e && e.name === name);
  if (!entry) return;
  const tabs = entry.tables || [];
  entry.tables = tabs.includes(table) ? tabs.filter(t => t !== table) : tabs.concat([table]);
  _saveDataSourcesSel();
  renderDatenquellenPane();
}

function dsSelClearTables(name) {
  const entry = _dsSelArr().find(e => e && e.name === name);
  if (!entry) return;
  entry.tables = [];
  _saveDataSourcesSel();
  renderDatenquellenPane();
}
