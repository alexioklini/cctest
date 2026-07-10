/* ═══════════════════════════════════════════════════════════
   SEARCH MODAL — globale Suche (v9.306.0)
   Server-backed: Chats (GET /v1/sessions/search — SQL-Volltext über Titel/
   Zusammenfassung/Nachrichten, access-gefiltert) + Wissen (GET /v1/wiki/search
   — semantisch über Wiki-Seiten aller zugänglichen Wings + das eigene
   MemPalace-Gedächtnis). Debounced; Ergebnisse gruppiert mit Sprunglinks.
   ═══════════════════════════════════════════════════════════ */
function openSearchModal() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.innerHTML = `
    <div class="modal-body" style="padding:16px">
      <input class="form-input" id="global-search" placeholder="Chats, Wiki und Gedächtnis durchsuchen…" autofocus
             oninput="performGlobalSearch(this.value)"
             style="font-size:16px;padding:12px 16px;border-radius:12px">
      <div id="search-results" style="margin-top:12px;max-height:440px;overflow-y:auto"></div>
    </div>
  `;

  overlay.appendChild(content);
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('global-search')?.focus(), 100);
}

let _searchDebounce = null;

function performGlobalSearch(query) {
  clearTimeout(_searchDebounce);
  const container = document.getElementById('search-results');
  if (!container) return;
  query = (query || '').trim();
  if (query.length < 2) { container.innerHTML = ''; return; }
  container.innerHTML = '<div style="padding:8px;color:var(--text-400);font-size:13px">Wird gesucht…</div>';
  _searchDebounce = setTimeout(() => _runGlobalSearch(query), 300);
}

async function _runGlobalSearch(query) {
  const enc = encodeURIComponent(query);
  const [sess, kb] = await Promise.allSettled([
    API.get(`/v1/sessions/search?q=${enc}&limit=10`),
    API.get(`/v1/wiki/search?q=${enc}&limit=6`),
  ]);
  // Stale guard: the user kept typing while we searched — drop this response.
  const cur = (document.getElementById('global-search')?.value || '').trim();
  if (cur !== query) return;
  const container = document.getElementById('search-results');
  if (!container) return;

  const groupHeader = (label, count) => `
    <div style="font-size:11px;font-weight:600;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;padding:8px 4px 4px">${label}${count ? ` (${count})` : ''}</div>`;

  let html = '';

  // ── Chats (Volltext: Titel/Zusammenfassung/Nachrichten) ──
  const chats = (sess.status === 'fulfilled' && sess.value.results) || [];
  if (chats.length) {
    html += groupHeader('Chats', chats.length);
    for (const r of chats) {
      const sid = r.id || r.session_id;
      const preview = r.match_preview
        ? `<div style="font-size:11px;color:var(--text-400);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.match_preview)}</div>`
        : '';
      html += `
        <div class="dropdown-item" style="display:block" onclick="openSession('${esc(sid)}','${esc(r.agent_id || 'main')}'); this.closest('.modal-overlay').remove()">
          <div style="display:flex;justify-content:space-between;gap:8px">
            <span class="dd-label">${esc(r.title || r.summary || '(ohne Titel)')}</span>
            <span class="dd-meta">${esc(r.agent_id || '')} &middot; ${relativeTime(r.last_active)}</span>
          </div>
          ${preview}
        </div>`;
    }
  }

  // ── Wiki (semantisch, alle zugänglichen Wings) ──
  const wiki = (kb.status === 'fulfilled' && kb.value.wiki) || [];
  if (wiki.length) {
    html += groupHeader('Wiki', wiki.length);
    const scopeLabel = { user: 'Meine', team: 'Team', global: 'Global' };
    for (const w of wiki) {
      html += `
        <div class="dropdown-item" style="display:block" onclick="wikiOpenFromCitation('${esc(w.page_id)}'); this.closest('.modal-overlay').remove()">
          <div style="display:flex;justify-content:space-between;gap:8px">
            <span class="dd-label">${esc(w.title)}</span>
            <span class="dd-meta">${esc(scopeLabel[w.scope] || w.scope || '')}</span>
          </div>
          <div style="font-size:11px;color:var(--text-400);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(w.snippet || '')}</div>
        </div>`;
    }
  }

  // ── Gedächtnis (MemPalace, eigene Wing — Anzeige-only) ──
  const memory = (kb.status === 'fulfilled' && kb.value.memory) || [];
  if (memory.length) {
    html += groupHeader('Gedächtnis', memory.length);
    for (const m of memory) {
      html += `
        <div class="dropdown-item" style="display:block;cursor:default" onclick="event.stopPropagation()">
          <div style="display:flex;justify-content:space-between;gap:8px">
            <span class="dd-label">${esc(m.source)}</span>
            <span class="dd-meta">MemPalace</span>
          </div>
          <div style="font-size:11px;color:var(--text-400)">${esc(m.snippet || '')}</div>
        </div>`;
    }
  }

  if (!html) {
    html = '<div style="padding:12px;text-align:center;color:var(--text-400);font-size:13px">Keine Ergebnisse</div>';
  }
  container.innerHTML = html;
}
