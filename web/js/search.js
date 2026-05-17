/* ═══════════════════════════════════════════════════════════
   SEARCH MODAL
   ═══════════════════════════════════════════════════════════ */
function openSearchModal() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.innerHTML = `
    <div class="modal-body" style="padding:16px">
      <input class="form-input" id="global-search" placeholder="Search chats, projects, agents..." autofocus
             oninput="performGlobalSearch(this.value)"
             style="font-size:16px;padding:12px 16px;border-radius:12px">
      <div id="search-results" style="margin-top:12px;max-height:400px;overflow-y:auto"></div>
    </div>
  `;

  overlay.appendChild(content);
  document.body.appendChild(overlay);
  setTimeout(() => document.getElementById('global-search')?.focus(), 100);
}

async function performGlobalSearch(query) {
  const container = document.getElementById('search-results');
  if (!query || query.length < 2) { container.innerHTML = ''; return; }

  container.innerHTML = '<div style="padding:8px;color:var(--text-400);font-size:13px">Searching...</div>';

  // Search sessions
  let results = [];
  for (const [agentId, data] of Object.entries(state.agentSessions)) {
    if (!data?.sessions) continue;
    for (const s of data.sessions) {
      const hay = ((s.title || '') + ' ' + (s.summary || '')).toLowerCase();
      if (hay.includes(query.toLowerCase())) {
        results.push({type:'chat', title: s.title || s.summary || '', agentId, sessionId: s.id || s.session_id, time: s.last_active});
      }
    }
  }

  let html = '';
  for (const r of results.slice(0, 20)) {
    html += `
      <div class="dropdown-item" onclick="openSession('${r.sessionId}','${r.agentId}'); this.closest('.modal-overlay').remove()">
        <span class="dd-label">${esc(r.title)}</span>
        <span class="dd-meta">${esc(r.agentId)} &middot; ${relativeTime(r.time)}</span>
      </div>
    `;
  }
  if (!results.length) html = '<div style="padding:12px;text-align:center;color:var(--text-400);font-size:13px">No results</div>';
  container.innerHTML = html;
}

