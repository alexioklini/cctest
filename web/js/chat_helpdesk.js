'use strict';

/* ═══════════════════════════════════════════════════════════
   Brainy 🧠 — der freundliche Helpdesk-Bot
   ───────────────────────────────────────────────────────────
   A floating bubble (visible in EVERY view) opens a mini-chat modal that
   talks to the /v1/helpdesk endpoint (own system prompt, own model, exclusive
   brain-agent-guide skill) — fully separate from the main chat, works during
   streaming. The bubble's symbol mirrors the user's buddy species (or 🧠).

   Brainy is told which view/modal the user is currently in (brainyViewContext)
   so it can help contextually even when no chat session is active.

   Globals: brainyState, brainyOpen, brainyClose, brainySend,
   brainyRefreshBubble, brainyViewContext (+ render helpers).
   ═══════════════════════════════════════════════════════════ */

const brainyState = {
  open: false,
  streaming: false,
  sessionId: '',          // current chat session (context only; may be empty)
  viewContext: null,      // where the user is, captured on open
  abort: null,            // AbortController for the in-flight ask
  exchanges: [],          // [{uid, aid, ts, q, a}] oldest-first (uid/aid = msg ids)
  oldestId: null,         // pagination cursor (smallest msg id currently shown)
  hasMore: false,         // older rows exist before oldestId
  loadingOlder: false,    // guard against concurrent lazy loads
  collapsed: {},          // {groupKey: true} — collapsed group state
  pageSize: 20,
  groupThreshold: 10,     // flat list at/below this many exchanges; grouped above
};

/* ── Floating bubble — its symbol is the user's buddy (or 🧠) ── */

function brainyRefreshBubble() {
  const fab = document.getElementById('brainy-bubble');
  if (!fab) return;
  const svg = (typeof buddySvgMarkup === 'function') ? buddySvgMarkup() : '';
  fab.innerHTML = svg
    ? `<span class="brainy-fab-buddy">${svg}</span>`
    : '<span class="brainy-fab-emoji">🧠</span>';
  // Tint with the buddy's color (falls back to brand accent).
  fab.style.setProperty('--brainy-accent',
    (typeof buddyColor === 'function') ? buddyColor() : 'var(--accent-brand)');
}

/* ── Where is the user right now? (for context-aware help) ──── */

function brainyViewContext() {
  // An open General Settings modal takes precedence over the underlying view.
  const gs = document.getElementById('general-settings-modal')
          || document.querySelector('#general-settings-tabs');
  if (gs && gs.offsetParent !== null) {
    const tab = document.querySelector('#general-settings-tabs .modal-tab.active');
    const tabLabel = tab ? tab.textContent.trim() : '';
    return { view: 'settings', label: 'Einstellungen' + (tabLabel ? ' → ' + tabLabel : '') };
  }
  const v = (typeof state !== 'undefined' && state.currentView) || '';
  const LABELS = {
    welcome: 'Startseite', chat: 'Chat', chats: 'Chat-Liste',
    projects: 'Projekte-Liste', 'project-detail': 'Projekt',
    scheduled: 'Geplante Aufgaben', workflows: 'Workflows',
    translation: 'Übersetzung', favourites: 'Favoriten',
  };
  const ctx = { view: v || 'unknown', label: LABELS[v] || v || 'Unbekannt' };
  if (state.currentProject) {
    ctx.project = state.currentProject;
    ctx.label = (v === 'chat' ? 'Projekt-Chat' : 'Projekt') + ' „' + state.currentProject + '"';
  }
  if (state.activeChat?.chatTitle) ctx.chat_title = state.activeChat.chatTitle;
  return ctx;
}

/* ── Open / close the modal ─────────────────────────────────── */

function brainyOpen() {
  // Visible everywhere — a chat session is optional. When present we attach to
  // it (session tools work); otherwise Brainy still answers general + view-aware
  // help. The session id keys the persisted helpdesk history.
  brainyState.sessionId = state.activeChat?.sessionId || '';
  brainyState.viewContext = brainyViewContext();
  brainyState.open = true;

  // Rebuild each open so the avatar reflects the user's current buddy species.
  let overlay = document.getElementById('brainy-overlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'brainy-overlay';
  overlay.className = 'modal-overlay brainy-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) brainyClose(); };
  const accent = (typeof buddyColor === 'function') ? buddyColor() : 'var(--accent-brand)';
  overlay.innerHTML = `
    <div class="modal-content brainy-modal">
      <div class="brainy-header" style="background:linear-gradient(135deg, ${accent}, color-mix(in srgb, ${accent} 70%, #000 30%))">
        <span class="brainy-avatar" aria-hidden="true" style="color:#fff">${brainyAvatarHTML(true)}</span>
        <div class="brainy-header-text">
          <div class="brainy-title">Brainy</div>
          <div class="brainy-subtitle">Dein freundlicher Helfer für brain-agent</div>
        </div>
        <button class="modal-close" onclick="brainyClose()" title="Schließen">&times;</button>
      </div>
      <div class="brainy-messages" id="brainy-messages" onscroll="brainyOnScroll()">
        <div id="brainy-load-more" class="brainy-load-more" style="display:none">
          <button class="brainy-load-more-btn" onclick="brainyLoadOlder()">Ältere laden</button>
        </div>
        <div id="brainy-list"></div>
      </div>
      <button id="brainy-to-top" class="brainy-anchor" title="Nach oben" onclick="brainyScrollTop()" style="display:none">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
      </button>
      <button id="brainy-to-bottom" class="brainy-anchor brainy-anchor-bottom" title="Nach unten" onclick="brainyScrollBottom()" style="display:none">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
      </button>
      <div class="brainy-input-row">
        <textarea id="brainy-input" class="brainy-input" rows="1"
                  placeholder="Frag Brainy etwas über brain-agent oder diese Sitzung…"
                  oninput="brainyAutogrow(this)"
                  onkeydown="brainyInputKey(event)"></textarea>
        <button class="brainy-send" id="brainy-send-btn" onclick="brainySend()" title="Senden">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.style.display = 'flex';
  // Lazy warmup: prime Brainy's KV prefix now (fire-and-forget). No-op unless
  // Brainy's model is local + warmup-enabled; server debounces re-opens.
  try { API.post('/v1/helpdesk/warmup', {}); } catch (e) {}
  brainyLoadHistory();
  setTimeout(() => { const el = document.getElementById('brainy-input'); if (el) { brainyAutogrow(el); el.focus(); } }, 80);
}

// Brainy's avatar IS the user's buddy (crab etc.). Falls back to 🧠 when the
// buddy is off / unavailable. `inheritColor` keeps currentColor (header = white);
// otherwise tints with the species color (bubbles).
function brainyAvatarHTML(inheritColor) {
  const svg = (typeof buddySvgMarkup === 'function') ? buddySvgMarkup() : '';
  if (!svg) return '🧠';
  const col = inheritColor ? '' : ` style="color:${(typeof buddyColor==='function')?buddyColor():'currentColor'}"`;
  return `<span class="brainy-avatar-buddy"${col}>${svg}</span>`;
}

function brainyClose() {
  brainyState.open = false;
  const overlay = document.getElementById('brainy-overlay');
  if (overlay) overlay.style.display = 'none';
  // Don't abort an in-flight answer — it persists server-side and finishes.
}

/* ── History: pair messages into exchanges, group by age, lazy-load ──────── */

// Pair a flat oldest-first [{id,role,content,ts}] list into exchanges
// [{uid,aid,ts,q,a}]. A user message opens an exchange; the next assistant
// message closes it. Tolerates orphans (assistant without a preceding user).
function brainyPairExchanges(msgs) {
  const out = [];
  let cur = null;
  for (const m of msgs) {
    const ctx = m.context_label || '';
    if (m.role === 'user') {
      if (cur) out.push(cur);
      cur = { uid: m.id, aid: null, ts: m.ts, q: m.content || '', a: '', ctx };
    } else {  // assistant
      if (cur && !cur.a) { cur.a = m.content || ''; cur.aid = m.id; if (!cur.ctx) cur.ctx = ctx; }
      else { out.push({ uid: null, aid: m.id, ts: m.ts, q: '', a: m.content || '', ctx }); }
    }
  }
  if (cur) out.push(cur);
  return out;
}

// Machine context key for the CURRENT view — mirrors the server's
// _context_label() so the live (pre-reload) badge matches what gets persisted.
function brainyContextKey() {
  const ctx = brainyViewContext() || {};
  if (ctx.project) return 'project:' + ctx.project;
  const v = (ctx.view || '').trim();
  return (v && v !== 'unknown') ? 'view:' + v : '';
}

// Machine context key (e.g. "project:Foo", "view:translation") → German badge
// text, or '' for none. Mirrors the view labels used by brainyViewContext().
function brainyContextBadge(label) {
  const ctx = (label || '').trim();
  if (!ctx) return '';
  if (ctx.startsWith('project:')) return 'Projekt: ' + ctx.slice(8);
  const VIEW = {
    welcome: 'Startseite', chat: 'Chat', chats: 'Chat-Liste',
    projects: 'Projekte', 'project-detail': 'Projekt',
    scheduled: 'Geplante Aufgaben', workflows: 'Workflows',
    translation: 'Übersetzung', favourites: 'Favoriten', settings: 'Einstellungen',
  };
  if (ctx.startsWith('view:')) { const v = ctx.slice(5); return VIEW[v] || v; }
  return ctx;
}

async function brainyLoadHistory() {
  Object.assign(brainyState, { exchanges: [], oldestId: null, hasMore: false, collapsed: {} });
  let msgs = [];
  try {
    // pageSize is in EXCHANGES; the server paginates by message rows (~2/exchange).
    const r = await API.get(`/v1/helpdesk/history?limit=${brainyState.pageSize * 2}`);
    msgs = r?.messages || [];
    brainyState.hasMore = !!r?.has_more;
  } catch (e) { /* best-effort */ }

  if (msgs.length) brainyState.oldestId = msgs[0].id;   // chronological → first is oldest
  brainyState.exchanges = brainyPairExchanges(msgs);
  brainyRenderHistory();
  brainyScrollBottom();
}

// Fetch the next older page (cursor = current oldest id), prepend, keep scroll.
async function brainyLoadOlder() {
  if (brainyState.loadingOlder || !brainyState.hasMore || !brainyState.oldestId) return;
  brainyState.loadingOlder = true;
  const box = document.getElementById('brainy-messages');
  const prevH = box ? box.scrollHeight : 0;
  try {
    const r = await API.get(`/v1/helpdesk/history?before_id=${brainyState.oldestId}&limit=${brainyState.pageSize * 2}`);
    const msgs = r?.messages || [];
    brainyState.hasMore = !!r?.has_more;
    if (msgs.length) {
      brainyState.oldestId = msgs[0].id;
      brainyState.exchanges = brainyPairExchanges(msgs).concat(brainyState.exchanges);
      brainyRenderHistory();
      // preserve scroll position so the viewport doesn't jump
      if (box) box.scrollTop = box.scrollHeight - prevH;
    }
  } catch (e) { /* best-effort */ }
  finally { brainyState.loadingOlder = false; }
}

/* ── Adaptive age grouping ──────────────────────────────────── */

// Bucket key + label for a timestamp (seconds), relative to now.
function brainyGroupOf(ts) {
  const d = new Date((ts || 0) * 1000);
  const now = new Date();
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const today = startOfDay(now);
  const dDay = startOfDay(d);
  const dayMs = 86400000;
  if (dDay === today) return { key: 'd0', label: 'Heute' };
  if (dDay === today - dayMs) return { key: 'd1', label: 'Gestern' };
  if (dDay > today - 7 * dayMs) return { key: 'w', label: 'Diese Woche' };
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth())
    return { key: 'm', label: 'Diesen Monat' };
  if (d.getFullYear() === now.getFullYear()) {
    const MON = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'];
    return { key: 'm' + d.getMonth(), label: MON[d.getMonth()] };
  }
  return { key: 'y' + d.getFullYear(), label: String(d.getFullYear()) };
}

function brainyFmtTime(ts) {
  const d = new Date((ts || 0) * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const sameDay = d.toDateString() === new Date().toDateString();
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return sameDay ? t : `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${t}`;
}

/* ── Render ─────────────────────────────────────────────────── */

function brainyRenderHistory() {
  const list = document.getElementById('brainy-list');
  if (!list) return;
  const ex = brainyState.exchanges;
  document.getElementById('brainy-load-more').style.display = brainyState.hasMore ? '' : 'none';

  if (!ex.length) {
    list.innerHTML = `<div class="brainy-bubble brainy-bot"><span class="brainy-bubble-avatar">${brainyAvatarHTML()}</span>`
      + `<div class="brainy-bubble-body">${renderMarkdown('Hi! Ich bin **Brainy** — dein persönlicher Helfer im brain-agent. '
      + 'Frag mich alles: wie die App funktioniert, was du gerade vor dir hast, oder wie du etwas erledigst. Womit kann ich helfen?')}</div></div>`;
    return;
  }

  // Flat list at/below the threshold; grouped above.
  if (ex.length <= brainyState.groupThreshold) {
    list.innerHTML = ex.map(brainyExchangeHTML).join('');
    return;
  }

  // Grouped: consecutive exchanges sharing a bucket key (list is oldest→newest).
  let html = '', curKey = null, groupItems = [];
  const flush = () => {
    if (!groupItems.length) return;
    const g = brainyGroupOf(groupItems[0].ts);
    const collapsed = !!brainyState.collapsed[g.key];
    const startTs = groupItems[0].ts, endTs = groupItems[groupItems.length - 1].ts + 1;
    html += `<div class="brainy-group" data-key="${esc(g.key)}">
      <div class="brainy-group-head" onclick="brainyToggleGroup('${esc(g.key)}')">
        <svg class="brainy-group-chevron${collapsed ? ' collapsed' : ''}" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        <span class="brainy-group-label">${esc(g.label)}</span>
        <span class="brainy-group-count">${groupItems.length}</span>
        <button class="brainy-group-del" title="Diese Gruppe löschen" onclick="event.stopPropagation();brainyDeleteGroup('${esc(g.key)}',${startTs},${endTs})">&times;</button>
      </div>
      <div class="brainy-group-body"${collapsed ? ' style="display:none"' : ''}>
        ${groupItems.map(brainyExchangeHTML).join('')}
      </div></div>`;
    groupItems = [];
  };
  for (const x of ex) {
    const k = brainyGroupOf(x.ts).key;
    if (k !== curKey) { flush(); curKey = k; }
    groupItems.push(x);
  }
  flush();
  list.innerHTML = html;
}

// One exchange = the user question + Brainy's answer + timestamp + delete.
// An exchange is BOTH rows (question + answer); delete removes both.
function brainyExchangeHTML(x) {
  const delId = x.uid || x.aid;   // identifies the exchange in local state
  const badge = brainyContextBadge(x.ctx);
  const badgeHTML = badge
    ? `<div class="brainy-ctx-badge" title="Gefragt in: ${esc(badge)}">${esc(badge)}</div>` : '';
  const q = x.q ? `<div class="brainy-bubble brainy-user">${badgeHTML}<div class="brainy-bubble-body">${esc(x.q)}</div></div>` : '';
  const a = (x.a || x.aid) ? `<div class="brainy-bubble brainy-bot"><span class="brainy-bubble-avatar">${brainyAvatarHTML()}</span>`
    + `<div class="brainy-bubble-body">${typeof renderMarkdown === 'function' ? renderMarkdown(x.a || '') : esc(x.a || '')}</div></div>` : '';
  return `<div class="brainy-exchange" data-id="${delId}">
    <button class="brainy-ex-del" title="Diesen Eintrag löschen" onclick="brainyDeleteExchange(${delId})">&times;</button>
    ${q}${a}
    <div class="brainy-ex-time">${esc(brainyFmtTime(x.ts))}</div>
  </div>`;
}

function brainyToggleGroup(key) {
  brainyState.collapsed[key] = !brainyState.collapsed[key];
  brainyRenderHistory();
}

/* ── Delete ─────────────────────────────────────────────────── */

async function brainyDeleteExchange(id) {
  if (!confirm('Diesen Brainy-Eintrag löschen?')) return;
  const x = brainyState.exchanges.find((e) => (e.uid || e.aid) === id);
  if (!x) return;
  // An exchange spans two rows (question + answer) — delete BOTH, else the
  // orphaned answer survives the next reload.
  const ids = [x.uid, x.aid].filter((v) => v != null);
  try {
    await API.post('/v1/helpdesk/delete', { ids });
    brainyState.exchanges = brainyState.exchanges.filter((e) => (e.uid || e.aid) !== id);
    brainyRenderHistory();
  } catch (e) { showToast('Löschen fehlgeschlagen', true); }
}

async function brainyDeleteGroup(key, startTs, endTs) {
  if (!confirm('Alle Einträge dieser Gruppe löschen?')) return;
  try {
    await API.post('/v1/helpdesk/delete', { start_ts: startTs, end_ts: endTs });
    brainyState.exchanges = brainyState.exchanges.filter((x) => brainyGroupOf(x.ts).key !== key);
    brainyRenderHistory();
  } catch (e) { showToast('Löschen fehlgeschlagen', true); }
}

/* ── Scroll: lazy-load trigger + top/bottom anchors ─────────── */

function brainyOnScroll() {
  const box = document.getElementById('brainy-messages');
  if (!box) return;
  if (box.scrollTop < 40) brainyLoadOlder();    // near the top → fetch older
  const top = document.getElementById('brainy-to-top');
  const bot = document.getElementById('brainy-to-bottom');
  const far = box.scrollHeight - box.clientHeight;
  if (top) top.style.display = box.scrollTop > 120 ? '' : 'none';
  if (bot) bot.style.display = (far - box.scrollTop) > 120 ? '' : 'none';
}
function brainyScrollTop() { const b = document.getElementById('brainy-messages'); if (b) b.scrollTop = 0; }
function brainyScrollBottom() { const b = document.getElementById('brainy-messages'); if (b) b.scrollTop = b.scrollHeight; }
function brainyScroll() { brainyScrollBottom(); }

function brainyAutogrow(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

function brainyInputKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); brainySend(); }
}

/* ── Send a question, stream the answer ─────────────────────── */

async function brainySend() {
  if (brainyState.streaming) return;
  const input = document.getElementById('brainy-input');
  const text = (input?.value || '').trim();
  if (!text) return;
  input.value = '';
  brainyAutogrow(input);

  // Append a LIVE exchange block at the bottom (id-less until reload). If the
  // welcome/empty placeholder is showing, clear it first.
  const list = document.getElementById('brainy-list');
  if (!brainyState.exchanges.length) list.innerHTML = '';
  const ctxKey = brainyContextKey();            // persisted server-side; badge now
  const badge = brainyContextBadge(ctxKey);
  const badgeHTML = badge
    ? `<div class="brainy-ctx-badge" title="Gefragt in: ${esc(badge)}">${esc(badge)}</div>` : '';
  const live = document.createElement('div');
  live.className = 'brainy-exchange brainy-exchange-live';
  live.innerHTML =
    `<div class="brainy-bubble brainy-user">${badgeHTML}<div class="brainy-bubble-body">${esc(text)}</div></div>`
    + `<div class="brainy-bubble brainy-bot"><span class="brainy-bubble-avatar">${brainyAvatarHTML()}</span>`
    + `<div class="brainy-bubble-body" data-live-body><span class="brainy-typing"><span></span><span></span><span></span></span></div></div>`;
  list.appendChild(live);
  const bodyEl = live.querySelector('[data-live-body]');
  brainyScrollBottom();

  brainyState.streaming = true;
  const sendBtn = document.getElementById('brainy-send-btn');
  if (sendBtn) sendBtn.disabled = true;
  let acc = '';
  let firstDelta = true;

  brainyState.abort = new AbortController();
  try {
    const resp = await fetch(`${BASE_URL}/v1/helpdesk`, {
      method: 'POST',
      headers: API._headers(),
      body: JSON.stringify({
        message: text,
        session_id: state.activeChat?.sessionId || '',   // current chat, if any (context)
        view_context: brainyViewContext(),               // where the user is now
      }),
      signal: brainyState.abort.signal,
    });
    if (!resp.ok) {
      let msg = `Fehler ${resp.status}`;
      try { msg = (await resp.json()).error || msg; } catch (e) {}
      bodyEl.innerHTML = `<span class="brainy-error">${esc(msg)}</span>`;
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let evType = '';
    let streamDone = false;   // set by the SSE `done` event — see below
    // IMPORTANT: break on the `done` EVENT, not just on reader EOF. The server
    // keeps the connection alive (Connection: keep-alive), so reader.read()
    // can block past the final event — leaving brainyState.streaming = true,
    // which disables the send button and blocks every further question. The
    // `done` event is the real end-of-turn signal.
    while (!streamDone) {
      const { value, done } = await reader.read();
      if (done) break;   // EOF fallback (server closed first)
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();   // keep incomplete tail
      for (const line of lines) {
        if (line.startsWith('event:')) { evType = line.slice(6).trim(); continue; }
        if (line.startsWith('data:')) {
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let data = {};
          try { data = JSON.parse(payload); } catch (e) { continue; }
          if (evType === 'text_delta' && data.text) {
            if (firstDelta) { bodyEl.innerHTML = ''; firstDelta = false; }
            acc += data.text;
            bodyEl.innerHTML = (typeof renderMarkdown === 'function') ? renderMarkdown(acc) : esc(acc);
            brainyScroll();
          } else if (evType === 'tool_call') {
            if (firstDelta) bodyEl.innerHTML = brainyToolHint(data.name);
          } else if (evType === 'error') {
            if (firstDelta) { bodyEl.innerHTML = ''; firstDelta = false; }
            bodyEl.innerHTML += `<div class="brainy-error">${esc(data.message || 'Fehler')}</div>`;
          } else if (evType === 'done') {
            if (data.reply && (firstDelta || !acc)) {
              acc = data.reply;
              bodyEl.innerHTML = (typeof renderMarkdown === 'function') ? renderMarkdown(data.reply) : esc(data.reply);
            }
            streamDone = true;   // end the loop — do NOT wait for reader EOF
            break;
          }
        }
      }
    }
    try { await reader.cancel(); } catch (e) {}   // release the kept-alive socket
    if (firstDelta && !acc) {
      bodyEl.innerHTML = '<span class="brainy-error">Brainy hat keine Antwort geliefert.</span>';
    }
    // Record the completed exchange in state (id-less until the next open, where
    // it reloads from the DB with real ids → delete becomes available).
    const ts = Math.floor(Date.now() / 1000);
    brainyState.exchanges.push({ uid: null, aid: null, ts, q: text, a: acc, ctx: ctxKey });
    const tline = document.createElement('div');
    tline.className = 'brainy-ex-time';
    tline.textContent = brainyFmtTime(ts);
    live.appendChild(tline);
  } catch (e) {
    if (e.name !== 'AbortError') {
      bodyEl.innerHTML = `<span class="brainy-error">${esc(e.message || 'Verbindungsfehler')}</span>`;
    }
  } finally {
    brainyState.streaming = false;
    brainyState.abort = null;
    if (sendBtn) sendBtn.disabled = false;
    brainyScroll();
    document.getElementById('brainy-input')?.focus();
  }
}

function brainyToolHint(name) {
  const map = {
    use_skill: 'liest die brain-agent-Anleitung',
    helpdesk_session_info: 'schaut sich diese Sitzung an',
    helpdesk_user_context: 'schaut nach, wer du bist',
    helpdesk_user_activity: 'schaut sich deine bisherige Aktivität an',
    mempalace_query: 'durchsucht den Speicher',
    read_document: 'liest ein Dokument',
  };
  const what = map[name] || 'schaut etwas nach';
  return `<span class="brainy-tool-hint">🧠 Brainy ${esc(what)}…</span>`;
}
