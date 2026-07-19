/* ═══════════════════════════════════════════════════════════
   SEARCH MODAL — globale Suche (v9.306.0), claude.ai-style overlay
   Server-backed: Chats (GET /v1/sessions/search — SQL-Volltext über Titel/
   Zusammenfassung/Nachrichten, access-gefiltert) + Wissen (GET /v1/wiki/search
   — semantisch über Wiki-Seiten aller zugänglichen Wings + das eigene
   MemPalace-Gedächtnis). Debounced; Ergebnisse mit Typ-Icon + Recency-Label.
   Leerer Zustand zeigt die zuletzt verwendeten Chats/Projekte (wie claude.ai).
   Tastatur: ↑/↓ bewegt die Auswahl, Enter öffnet, Esc schließt.
   ═══════════════════════════════════════════════════════════ */

// Type-icon glyphs for result rows (mirror the sidebar concepts).
const SEARCH_ICONS = {
  chat:    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
  task:    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="18" x2="20" y2="18"/><circle cx="4.5" cy="6" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.2" fill="currentColor" stroke="none"/></svg>',
  code:    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
  project: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>',
  wiki:    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
  memory:  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 3H5a2 2 0 00-2 2v4m6-6h6m-6 0v18m0 0H5a2 2 0 01-2-2v-4m6 6h6m0-18h4a2 2 0 012 2v4m-6-6v18m6-6v4a2 2 0 01-2 2h-4"/></svg>',
};

// Coarse recency bucket for the right-hand label (Heute / Gestern / Letzte
// Woche / Letzter Monat / älteres Datum). Accepts epoch-seconds or ms/ISO.
function searchRecencyLabel(ts) {
  if (!ts) return '';
  let ms = typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : new Date(ts).getTime();
  if (!ms || isNaN(ms)) return '';
  const days = (Date.now() - ms) / 86400000;
  if (days < 1)  return 'Heute';
  if (days < 2)  return 'Gestern';
  if (days < 7)  return 'Letzte Woche';
  if (days < 31) return 'Letzter Monat';
  return new Date(ms).toLocaleDateString();
}

function openSearchModal() {
  // Don't stack multiple search overlays.
  if (document.getElementById('search-overlay')) return;

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay search-overlay';
  overlay.id = 'search-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) closeSearchModal(); };

  overlay.innerHTML = `
    <div class="search-modal">
      <div class="search-bar">
        <span class="search-bar-icon"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>
        <input class="search-input" id="global-search" placeholder="Chats und Projekte durchsuchen" autocomplete="off" spellcheck="false"
               oninput="performGlobalSearch(this.value)">
        <button class="search-close" onclick="closeSearchModal()" title="Schließen" aria-label="Schließen">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="search-results" id="search-results"></div>
    </div>`;

  document.body.appendChild(overlay);
  document.addEventListener('keydown', _searchKeydown);
  setTimeout(() => document.getElementById('global-search')?.focus(), 40);
  // Empty query → show recent chats/projects immediately (claude.ai behaviour).
  _renderRecentSearchResults();
}

function closeSearchModal() {
  document.removeEventListener('keydown', _searchKeydown);
  document.getElementById('search-overlay')?.remove();
}

// ── Keyboard navigation (↑/↓ move highlight, Enter opens, Esc closes) ──
function _searchKeydown(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeSearchModal(); return; }
  const rows = Array.from(document.querySelectorAll('#search-results .search-row[data-idx]'));
  if (!rows.length) return;
  let cur = rows.findIndex(r => r.classList.contains('highlight'));
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    _highlightSearchRow(rows, cur < 0 ? 0 : Math.min(cur + 1, rows.length - 1));
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    _highlightSearchRow(rows, cur <= 0 ? 0 : cur - 1);
  } else if (e.key === 'Enter') {
    const target = cur >= 0 ? rows[cur] : rows[0];
    if (target) { e.preventDefault(); target.click(); }
  }
}

function _highlightSearchRow(rows, idx) {
  rows.forEach(r => r.classList.remove('highlight'));
  const row = rows[idx];
  if (row) { row.classList.add('highlight'); row.scrollIntoView({ block: 'nearest' }); }
}

// Build one result row. `onclick` runs on click/Enter; it should navigate and
// then close the modal.
function _searchRow(idx, iconKey, title, meta, onclick, preview) {
  const previewHtml = preview
    ? `<div class="search-row-preview">${esc(preview)}</div>` : '';
  return `
    <div class="search-row${idx === 0 ? ' highlight' : ''}" data-idx="${idx}" onclick="${onclick}">
      <span class="search-row-icon">${SEARCH_ICONS[iconKey] || SEARCH_ICONS.chat}</span>
      <span class="search-row-main">
        <span class="search-row-title">${esc(title)}</span>
        ${previewHtml}
      </span>
      ${meta ? `<span class="search-row-meta">${esc(meta)}</span>` : ''}
      <span class="search-row-enter">↵</span>
    </div>`;
}

function _sessionIconKey(s) {
  if (s.status === 'code') return 'code';
  if (s.goal_status === 'active' || s.goal_status === 'fulfilled') return 'task';
  return 'chat';
}

// Empty-query state: the most-recently-used chats + projects, flat and newest
// first — mirrors claude.ai's search opening on recents.
function _renderRecentSearchResults() {
  const container = document.getElementById('search-results');
  if (!container) return;

  const sessions = [];
  for (const [agentId, data] of Object.entries(state.agentSessions || {})) {
    for (const s of (data.sessions || [])) {
      if ((s.message_count || 0) > 0 && !(s.project || '')) {
        sessions.push({ ...s, agentId });
      }
    }
  }
  sessions.sort((a, b) => (b.last_active || 0) - (a.last_active || 0));

  let idx = 0;
  let html = '';
  for (const s of sessions.slice(0, 14)) {
    const sid = s.id || s.session_id;
    const title = s.title || s.summary || `Chat ${String(sid).substring(0, 6)}`;
    html += _searchRow(idx++, _sessionIconKey(s), title, searchRecencyLabel(s.last_active),
      `openSession('${esc(sid)}','${esc(s.agent_id || s.agentId || 'main')}'); closeSearchModal()`);
  }

  container.innerHTML = html || '<div class="search-empty">Noch keine Chats. Tippen Sie, um zu suchen.</div>';
}

let _searchDebounce = null;

function performGlobalSearch(query) {
  clearTimeout(_searchDebounce);
  const container = document.getElementById('search-results');
  if (!container) return;
  query = (query || '').trim();
  if (query.length < 2) { _renderRecentSearchResults(); return; }
  container.innerHTML = '<div class="search-loading">Wird gesucht…</div>';
  _searchDebounce = setTimeout(() => _runGlobalSearch(query), 250);
}

async function _runGlobalSearch(query) {
  const enc = encodeURIComponent(query);
  const [sess, kb] = await Promise.allSettled([
    API.get(`/v1/sessions/search?q=${enc}&limit=12`),
    API.get(`/v1/wiki/search?q=${enc}&limit=6`),
  ]);
  // Stale guard: the user kept typing while we searched — drop this response.
  const cur = (document.getElementById('global-search')?.value || '').trim();
  if (cur !== query) return;
  const container = document.getElementById('search-results');
  if (!container) return;

  let idx = 0;
  let html = '';

  // ── Chats (Volltext: Titel/Zusammenfassung/Nachrichten) ──
  const chats = (sess.status === 'fulfilled' && sess.value.results) || [];
  for (const r of chats) {
    const sid = r.id || r.session_id;
    const meta = searchRecencyLabel(r.last_active) || (r.agent_id || '');
    html += _searchRow(idx++, _sessionIconKey(r), r.title || r.summary || '(ohne Titel)', meta,
      `openSession('${esc(sid)}','${esc(r.agent_id || 'main')}'); closeSearchModal()`,
      r.match_preview || '');
  }

  // ── Wiki (semantisch, alle zugänglichen Wings) ──
  const wiki = (kb.status === 'fulfilled' && kb.value.wiki) || [];
  const scopeLabel = { user: 'Meine', team: 'Team', global: 'Global' };
  for (const w of wiki) {
    html += _searchRow(idx++, 'wiki', w.title, scopeLabel[w.scope] || w.scope || 'Wiki',
      `wikiOpenFromCitation('${esc(w.page_id)}'); closeSearchModal()`,
      w.snippet || '');
  }

  // ── Gedächtnis (MemPalace, eigene Wing — Anzeige-only, kein Sprungziel) ──
  const memory = (kb.status === 'fulfilled' && kb.value.memory) || [];
  for (const m of memory) {
    html += _searchRow(idx++, 'memory', m.source, 'MemPalace',
      'event.stopPropagation()', m.snippet || '');
  }

  container.innerHTML = html || '<div class="search-empty">Keine Ergebnisse</div>';
}
