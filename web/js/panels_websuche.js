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
