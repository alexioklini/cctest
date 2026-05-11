'use strict';

/* ═══════════════════════════════════════════════════════════
   FAVOURITES — star button + customise modal
   Phase 2 wiring: button component you can drop into any
   item header. View/sidebar wiring lives in nav.js (later).
   ═══════════════════════════════════════════════════════════ */

const FAVOURITES_TYPE_DEFAULTS = {
  chat:         { icon: '💬', color: '#64748b', label: 'Chat' },
  project_chat: { icon: '🗂️', color: '#6366f1', label: 'Project chat' },
  project:      { icon: '📁', color: '#6366f1', label: 'Project' },
  workflow:     { icon: '🔀', color: '#10b981', label: 'Workflow' },
  schedule:     { icon: '⏰', color: '#f59e0b', label: 'Schedule' },
  artifact:     { icon: '📄', color: '#8b5cf6', label: 'Artifact' },
  translation:  { icon: '🌐', color: '#0ea5e9', label: 'Translation' },
};

/* Sidebar-style line-art glyphs used as the *default* card icon when the user
   has not picked a custom emoji. Currents-color stroke, no fill, weight 1.8 —
   matches .sb-nav-item .sb-icon. Returns inner SVG markup; caller wraps in
   <svg viewBox="0 0 24 24">. */
const FAVOURITES_TYPE_GLYPH_SVG = {
  project:      '<path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>',
  project_chat: '<path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>',
  chat:         '<path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>',
  workflow:     '<polyline points="4 7 8 11 4 15"/><polyline points="20 7 16 11 20 15"/><line x1="9" y1="18" x2="15" y2="6"/>',
  schedule:     '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  artifact:     '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  translation:  '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/>',
};

function favouriteTypeGlyphSvg(itemType, sizePx) {
  const inner = FAVOURITES_TYPE_GLYPH_SVG[itemType] || FAVOURITES_TYPE_GLYPH_SVG.artifact;
  const size = sizePx || 44;
  return `<svg class="card-line-glyph" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

// Curated icon palette for the customise modal (emoji = visually solid + zero deps).
const FAVOURITES_ICON_PALETTE = [
  '⭐','🔥','💡','🎯','📌','🔖','🏷️','📚',
  '💬','🗂️','📁','📂','🗒️','📝','📊','📈',
  '🔀','⚙️','🛠️','🧩','🧠','🤖','🔬','🧪',
  '⏰','📅','🔔','✅','⚡','🚀','🎨','🌟',
  '💼','🏠','🏢','🌐','🔍','🔐','📡','💎',
];

const FAVOURITES_COLOR_PALETTE = [
  '#64748b','#475569','#dc2626','#ea580c','#f59e0b','#16a34a','#10b981','#0ea5e9',
  '#3b82f6','#6366f1','#8b5cf6','#a855f7','#ec4899','#f43f5e','#78716c','#0f172a',
];

/* In-memory cache of the caller's favourites. Keyed by "type:agent:id" so
   the star buttons can find existing rows quickly. Refilled by load(). */
const FavouritesCache = {
  rows: [],            // hydrated array as returned by GET /v1/favourites
  byKey: new Map(),    // "type:agent:id" -> row
  loaded: false,
  loading: null,       // in-flight Promise

  _key(item_type, agent_id, item_id) {
    return `${item_type}:${agent_id || 'main'}:${item_id}`;
  },

  async load(force = false) {
    if (this.loading) return this.loading;
    if (this.loaded && !force) return this.rows;
    this.loading = (async () => {
      try {
        const data = await API.get('/v1/favourites');
        this.rows = Array.isArray(data.favourites) ? data.favourites : [];
        this.byKey.clear();
        for (const r of this.rows) {
          this.byKey.set(this._key(r.item_type, r.agent_id, r.item_id), r);
        }
        this.loaded = true;
      } catch (e) {
        console.warn('[favourites] load failed', e);
      } finally {
        this.loading = null;
      }
      return this.rows;
    })();
    return this.loading;
  },

  get(item_type, agent_id, item_id) {
    return this.byKey.get(this._key(item_type, agent_id, item_id)) || null;
  },

  upsert(row) {
    if (!row || !row.item_type || !row.item_id) return;
    const key = this._key(row.item_type, row.agent_id, row.item_id);
    const idx = this.rows.findIndex(r => r.id === row.id);
    if (idx >= 0) this.rows[idx] = row; else this.rows.push(row);
    this.byKey.set(key, row);
  },

  removeById(id) {
    const idx = this.rows.findIndex(r => r.id === id);
    if (idx < 0) return;
    const row = this.rows[idx];
    this.rows.splice(idx, 1);
    this.byKey.delete(this._key(row.item_type, row.agent_id, row.item_id));
  },
};

/* ── Star button ────────────────────────────────────────────
   Drop a button anywhere with:
     mountFavouriteStar(container, {
       item_type: 'chat',
       item_id:   '<id>',
       agent_id:  'main',
       title:     'optional title for tooltip',
     });
   On click: if not yet favourited → POST as user-scope. If already
   favourited → opens the customise menu (re-scope, change icon,
   upload image, remove).
*/

function mountFavouriteStar(container, opts) {
  if (!container) return null;
  const { item_type, item_id, agent_id = 'main' } = opts || {};
  if (!item_type || !item_id) return null;

  const btn = document.createElement('button');
  btn.className = 'fav-star-btn';
  btn.type = 'button';
  btn.setAttribute('aria-label', 'Add to favourites');
  btn.innerHTML = '<span class="fav-glyph">☆</span>';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    onStarClick(btn, opts);
  });
  container.appendChild(btn);
  // `opts.simple = true` skips the popover (Customise / Change scope / Remove)
  // and makes the star a plain on/off toggle. Used on project cards where the
  // ⋮ menu already exposes Edit (visibility) and other actions, so a second
  // popover hanging off the star would be redundant clutter.
  if (opts.simple) btn.dataset.simple = '1';

  // Initial render once cache is ready.
  FavouritesCache.load().then(() => refreshStarButton(btn, opts));
  return btn;
}

function refreshStarButton(btn, opts) {
  if (!btn) return;
  const row = FavouritesCache.get(opts.item_type, opts.agent_id || 'main', opts.item_id);
  if (row) {
    btn.classList.add('is-favourited');
    btn.querySelector('.fav-glyph').textContent = '★';
    btn.setAttribute('aria-label', 'Customise favourite');
    btn.dataset.favId = row.id;
  } else {
    btn.classList.remove('is-favourited');
    btn.querySelector('.fav-glyph').textContent = '☆';
    btn.setAttribute('aria-label', 'Add to favourites');
    delete btn.dataset.favId;
  }
}

async function onStarClick(btn, opts) {
  await FavouritesCache.load();
  const existing = FavouritesCache.get(opts.item_type, opts.agent_id || 'main', opts.item_id);
  if (existing) {
    if (opts.simple) {
      await removeFavourite(existing, opts, btn);
      return;
    }
    openFavouriteMenu(btn, existing, opts);
    return;
  }
  // First-click default: scope=user, type-default icon.
  try {
    const def = FAVOURITES_TYPE_DEFAULTS[opts.item_type] || {};
    const row = await API.post('/v1/favourites', {
      scope: 'user',
      item_type: opts.item_type,
      item_id: opts.item_id,
      agent_id: opts.agent_id || 'main',
      icon: def.icon || '⭐',
      color: def.color || '',
    });
    if (row && row.id) {
      FavouritesCache.upsert(row);
      refreshStarButton(btn, opts);
      flashToast('Added to favourites');
      window.dispatchEvent(new CustomEvent('favourites:changed'));
    } else if (row && row.error) {
      flashToast(`Could not favourite: ${row.error}`, true);
    }
  } catch (e) {
    flashToast(`Favourite failed: ${e.message || e}`, true);
  }
}

/* ── Quick popover menu on the star ─── */

function openFavouriteMenu(anchorBtn, row, opts) {
  closeFavouriteMenu();
  const menu = document.createElement('div');
  menu.className = 'fav-popover';
  menu.id = 'fav-popover';
  menu.innerHTML = `
    <button class="fav-popover-item" data-act="customise">⚙ Customise</button>
    <button class="fav-popover-item" data-act="rescope">🔀 Change scope</button>
    <button class="fav-popover-item" data-act="remove">✕ Remove favourite</button>
  `;
  document.body.appendChild(menu);
  const r = anchorBtn.getBoundingClientRect();
  menu.style.left = `${Math.min(window.innerWidth - 220, r.left)}px`;
  menu.style.top  = `${r.bottom + 6}px`;

  menu.addEventListener('click', async (e) => {
    const act = e.target?.dataset?.act;
    if (!act) return;
    closeFavouriteMenu();
    if (act === 'customise') openCustomiseModal(row, opts);
    if (act === 'rescope')   openRescopeMenu(anchorBtn, row, opts);
    if (act === 'remove')    await removeFavourite(row, opts, anchorBtn);
  });
  setTimeout(() => {
    document.addEventListener('click', closeFavouriteMenu, { once: true });
  }, 0);
}
function closeFavouriteMenu() {
  document.getElementById('fav-popover')?.remove();
}

async function removeFavourite(row, opts, btn) {
  try {
    await API.del(`/v1/favourites/${row.id}`);
    FavouritesCache.removeById(row.id);
    refreshStarButton(btn, opts);
    flashToast('Removed from favourites');
    window.dispatchEvent(new CustomEvent('favourites:changed'));
  } catch (e) {
    flashToast(`Remove failed: ${e.message || e}`, true);
  }
}

/* ── Re-scope picker ─── */

function openRescopeMenu(anchorBtn, row, opts) {
  const menu = document.createElement('div');
  menu.className = 'fav-popover';
  menu.id = 'fav-popover';
  const isAdmin = state.authUser?.role === 'admin';
  const teams = state.userTeams || [];
  const items = [];
  items.push(`<button class="fav-popover-item" data-scope="user" data-scope-id="${state.authUser?.id || ''}">👤 Just me</button>`);
  for (const t of teams) {
    items.push(`<button class="fav-popover-item" data-scope="team" data-scope-id="${t.id}">👥 Team: ${escapeHtml(t.name || t.id)}</button>`);
  }
  if (isAdmin) {
    items.push(`<button class="fav-popover-item" data-scope="general" data-scope-id="">🌐 Everyone</button>`);
  }
  menu.innerHTML = items.join('');
  document.body.appendChild(menu);
  const r = anchorBtn.getBoundingClientRect();
  menu.style.left = `${Math.min(window.innerWidth - 220, r.left)}px`;
  menu.style.top  = `${r.bottom + 6}px`;

  menu.addEventListener('click', async (e) => {
    const scope    = e.target?.dataset?.scope;
    const scope_id = e.target?.dataset?.scopeId || '';
    if (!scope) return;
    closeFavouriteMenu();
    if (scope === row.scope && scope_id === (row.scope_id || '')) return;
    // Re-scope = remove + add (server has no in-place re-scope endpoint).
    try {
      await API.del(`/v1/favourites/${row.id}`);
      FavouritesCache.removeById(row.id);
      const fresh = await API.post('/v1/favourites', {
        scope, scope_id,
        item_type: row.item_type,
        item_id:   row.item_id,
        agent_id:  row.agent_id,
        icon:      row.icon || '',
        color:     row.color || '',
      });
      if (fresh && fresh.id) {
        FavouritesCache.upsert(fresh);
        flashToast(`Now favourited for ${labelForScope(scope, scope_id)}`);
        window.dispatchEvent(new CustomEvent('favourites:changed'));
      } else if (fresh && fresh.error) {
        flashToast(`Re-scope failed: ${fresh.error}`, true);
      }
    } catch (err) {
      flashToast(`Re-scope failed: ${err.message || err}`, true);
    }
    refreshStarButton(anchorBtn, opts);
  });
  setTimeout(() => document.addEventListener('click', closeFavouriteMenu, { once: true }), 0);
}

function labelForScope(scope, scope_id) {
  if (scope === 'user') return 'just you';
  if (scope === 'general') return 'everyone';
  if (scope === 'team') {
    const t = (state.userTeams || []).find(x => x.id === scope_id);
    return `team ${t?.name || scope_id}`;
  }
  return scope;
}

/* ── Customise modal: image / icon / color ─── */

function openCustomiseModal(row, opts) {
  closeCustomiseModal();
  const overlay = document.createElement('div');
  overlay.className = 'fav-modal-overlay';
  overlay.id = 'fav-modal-overlay';
  overlay.innerHTML = `
    <div class="fav-modal" role="dialog" aria-modal="true">
      <div class="fav-modal-head">
        <h3>Customise favourite</h3>
        <button class="fav-modal-close" aria-label="Close">✕</button>
      </div>
      <div class="fav-modal-body">
        <section class="fav-section">
          <h4>Image</h4>
          <div class="fav-image-row">
            <div class="fav-image-preview" id="fav-image-preview"></div>
            <div class="fav-image-actions">
              <label class="fav-btn">
                Upload…
                <input type="file" id="fav-image-input" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden>
              </label>
              <button class="fav-btn" data-act="clear-image">Use icon instead</button>
              <p class="fav-hint">Max 2 MB. JPG, PNG, WebP or SVG.</p>
            </div>
          </div>
        </section>
        <section class="fav-section">
          <h4>Icon</h4>
          <div class="fav-icon-grid" id="fav-icon-grid"></div>
        </section>
        <section class="fav-section">
          <h4>Accent colour</h4>
          <div class="fav-color-grid" id="fav-color-grid"></div>
        </section>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeCustomiseModal();
  });
  overlay.querySelector('.fav-modal-close').addEventListener('click', closeCustomiseModal);

  const state_ = { ...row };  // local working copy

  // Render preview
  const preview = overlay.querySelector('#fav-image-preview');
  function paintPreview() {
    const imgUrl = state_.image_path
      ? `/v1/favourites/image/${encodeURIComponent(state_.image_path)}`
      : (state_.source_image_url || '');
    if (imgUrl) {
      preview.style.backgroundImage = `url('${imgUrl}')`;
      preview.style.backgroundSize = 'cover';
      preview.style.backgroundPosition = 'center';
      preview.textContent = '';
    } else {
      preview.style.backgroundImage = '';
      preview.style.background = state_.color || state_.source_color
                                  || (FAVOURITES_TYPE_DEFAULTS[state_.item_type]?.color || '#475569');
      preview.textContent = state_.icon || state_.source_icon
                             || (FAVOURITES_TYPE_DEFAULTS[state_.item_type]?.icon || '⭐');
    }
  }
  paintPreview();

  // Icon palette
  const iconGrid = overlay.querySelector('#fav-icon-grid');
  for (const ic of FAVOURITES_ICON_PALETTE) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'fav-icon-cell' + (ic === state_.icon ? ' is-selected' : '');
    b.textContent = ic;
    b.addEventListener('click', async () => {
      state_.icon = ic;
      iconGrid.querySelectorAll('.fav-icon-cell').forEach(x => x.classList.remove('is-selected'));
      b.classList.add('is-selected');
      paintPreview();
      await persistVisual({ icon: ic });
    });
    iconGrid.appendChild(b);
  }

  // Color palette
  const colorGrid = overlay.querySelector('#fav-color-grid');
  for (const col of FAVOURITES_COLOR_PALETTE) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'fav-color-cell' + (col === state_.color ? ' is-selected' : '');
    b.style.background = col;
    b.addEventListener('click', async () => {
      state_.color = col;
      colorGrid.querySelectorAll('.fav-color-cell').forEach(x => x.classList.remove('is-selected'));
      b.classList.add('is-selected');
      paintPreview();
      await persistVisual({ color: col });
    });
    colorGrid.appendChild(b);
  }

  // Image upload
  overlay.querySelector('#fav-image-input').addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { flashToast('Image too large (max 2 MB)', true); return; }
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await fetch(`${BASE_URL}/v1/favourites/${row.id}/image`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${localStorage.getItem('auth-token') || ''}` },
        body: fd,
      });
      if (!r.ok) { flashToast(`Upload failed: ${r.status}`, true); return; }
      const updated = await r.json();
      Object.assign(state_, updated);
      FavouritesCache.upsert({ ...row, ...updated });
      paintPreview();
      window.dispatchEvent(new CustomEvent('favourites:changed'));
    } catch (err) {
      flashToast(`Upload failed: ${err.message || err}`, true);
    }
  });

  // Clear image → fall back to icon
  overlay.querySelector('[data-act="clear-image"]').addEventListener('click', async () => {
    try {
      const updated = await API.put(`/v1/favourites/${row.id}`, { clear_image: true });
      Object.assign(state_, updated);
      FavouritesCache.upsert({ ...row, ...updated });
      paintPreview();
      window.dispatchEvent(new CustomEvent('favourites:changed'));
    } catch (err) {
      flashToast(`Clear failed: ${err.message || err}`, true);
    }
  });

  async function persistVisual(patch) {
    try {
      const updated = await API.put(`/v1/favourites/${row.id}`, patch);
      Object.assign(state_, updated);
      FavouritesCache.upsert({ ...row, ...updated });
      window.dispatchEvent(new CustomEvent('favourites:changed'));
    } catch (err) {
      flashToast(`Save failed: ${err.message || err}`, true);
    }
  }
}
function closeCustomiseModal() {
  document.getElementById('fav-modal-overlay')?.remove();
}

/* ── Tiny toast helper (independent of any other module) ─── */

function flashToast(msg, isError = false) {
  let el = document.getElementById('fav-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'fav-toast';
    el.className = 'fav-toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle('is-error', !!isError);
  el.classList.add('is-visible');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('is-visible'), 2200);
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

/* ── Sidebar "Favourites" section ───────────────────────────
   Top 10 (across all visible scopes) ordered by updated_at.
   Click → dispatch to the existing opener for the item type.
*/
async function renderRecentFavourites() {
  const container = document.getElementById('sb-recent-favourites');
  const section   = document.getElementById('sb-section-favourites');
  if (!container || !section) return;

  await FavouritesCache.load();
  const all = FavouritesCache.rows
    .filter(r => r.available !== false)
    .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
    .slice(0, 10);

  if (!all.length) {
    section.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  section.style.display = '';
  container.innerHTML = '';
  for (const row of all) {
    const div = document.createElement('div');
    div.className = 'sb-session-item sb-fav-item';
    const def  = FAVOURITES_TYPE_DEFAULTS[row.item_type] || {};
    const rawIcon = row.icon || row.source_icon || '';
    const customIcon = (rawIcon && rawIcon !== def.icon) ? rawIcon : '';
    const iconHtml = customIcon
      ? escapeHtml(customIcon)
      : favouriteTypeGlyphSvg(row.item_type, 16);
    const t = (row.title || '(untitled)').slice(0, 50);
    div.innerHTML = `
      <span class="sb-sess-icon sb-fav-icon">${iconHtml}</span>
      <span class="sb-session-title">${escapeHtml(t)}</span>
    `;
    div.title = `${row.item_type} · ${labelForScope(row.scope, row.scope_id)}`;
    div.addEventListener('click', () => openFavouriteRow(row));
    container.appendChild(div);
  }
}

/* Single dispatch point — every item type has an existing opener. */
function openFavouriteRow(row) {
  const t = row.item_type;
  const id = row.item_id;
  const agent = row.agent_id || 'main';
  try {
    if (t === 'chat' || t === 'project_chat') {
      if (typeof openSession === 'function') return openSession(id, agent);
    } else if (t === 'project') {
      // openProject(agentId, name) — name lives in `_project_name` from hydrate.
      const name = row._project_name || row.title;
      if (typeof openProject === 'function') return openProject(agent, name);
    } else if (t === 'workflow') {
      navigateTo('workflows');
      // wfOpenEditor lives in workflows.js
      setTimeout(() => { try { wfOpenEditor && wfOpenEditor(id); } catch(_){} }, 200);
      return;
    } else if (t === 'schedule') {
      navigateTo('scheduled');
      return;
    } else if (t === 'artifact') {
      // Reuse the artifact-browse opener so it loads the parent session +
      // artifact registry first; openArtifactPanel alone bails when there's
      // no active chat.
      const sid = row._artifact_session_id || '';
      const aid = row._artifact_agent_id || agent;
      if (sid && typeof openArtifactFromBrowse === 'function') {
        return openArtifactFromBrowse(id, sid, aid);
      }
      // Fallback for older rows without session metadata.
      if (typeof openArtifactPanel === 'function') return openArtifactPanel(id);
    } else if (t === 'translation') {
      navigateTo('translation');
      const tab = id;
      setTimeout(() => { try { trSwitchTab(tab); } catch(_){} }, 100);
      return;
    }
  } catch (e) {
    console.warn('[favourites] open failed', e);
  }
}

/* Refresh sidebar whenever the cache changes. */
window.addEventListener('favourites:changed', () => {
  try { renderRecentFavourites(); } catch(_) {}
  try { if (state.currentView === 'favourites') renderFavouritesGrid(); } catch(_) {}
});

/* ── Favourites view (grid) ─────────────────────────────────
   Sidebar nav → navigateTo('favourites') → loadFavouritesView().
   Sort dropdown + scope chips + Clear-all (scoped to active chip).
*/

const FAV_VIEW_STATE = {
  sort: 'recent',          // 'recent' | 'name' | 'added'
  scope: 'mine',           // 'mine' | 'team:<id>' | 'general' | 'all'
};

async function loadFavouritesView() {
  await FavouritesCache.load(true);
  // Sync the sort dropdown to the persisted state so a fresh page load shows
  // the right option without a flash of mismatch.
  const sortSel = document.getElementById('fav-view-sort');
  if (sortSel && sortSel.value !== FAV_VIEW_STATE.sort) sortSel.value = FAV_VIEW_STATE.sort;
  renderFavouritesScopeChips();
  renderFavouritesGrid();
}

function renderFavouritesScopeChips() {
  const tabs = document.getElementById('fav-view-tabs');
  if (!tabs) return;
  const userId = state.authUser?.id || '';
  const teams  = state.userTeams || [];
  const isAdmin = state.authUser?.role === 'admin';
  const counts = countByScope();
  const chips = [];
  chips.push({ key: 'all',      label: `All (${counts.all})` });
  chips.push({ key: 'mine',     label: `Mine (${counts.mine})` });
  for (const t of teams) {
    chips.push({ key: `team:${t.id}`, label: `${t.name || t.id} (${counts.byTeam[t.id] || 0})` });
  }
  if (isAdmin || counts.general > 0) {
    chips.push({ key: 'general', label: `Everyone (${counts.general})` });
  }
  tabs.innerHTML = chips.map(c => `
    <button class="fav-view-tab${FAV_VIEW_STATE.scope === c.key ? ' is-active' : ''}"
            data-scope="${escapeHtml(c.key)}">${escapeHtml(c.label)}</button>
  `).join('');
  tabs.querySelectorAll('.fav-view-tab').forEach(b => {
    b.addEventListener('click', () => {
      FAV_VIEW_STATE.scope = b.dataset.scope;
      renderFavouritesScopeChips();
      renderFavouritesGrid();
    });
  });
}

function countByScope() {
  const userId = state.authUser?.id || '';
  const out = { all: 0, mine: 0, general: 0, byTeam: {} };
  for (const r of FavouritesCache.rows) {
    out.all++;
    if (r.scope === 'user' && r.scope_id === userId) out.mine++;
    else if (r.scope === 'general') out.general++;
    else if (r.scope === 'team') out.byTeam[r.scope_id] = (out.byTeam[r.scope_id] || 0) + 1;
  }
  return out;
}

function filterByScope(rows) {
  const userId = state.authUser?.id || '';
  const s = FAV_VIEW_STATE.scope;
  if (s === 'all') return rows;
  if (s === 'mine') return rows.filter(r => r.scope === 'user' && r.scope_id === userId);
  if (s === 'general') return rows.filter(r => r.scope === 'general');
  if (s.startsWith('team:')) {
    const tid = s.slice(5);
    return rows.filter(r => r.scope === 'team' && r.scope_id === tid);
  }
  return rows;
}

function renderFavouritesGrid() {
  const grid = document.getElementById('fav-view-grid');
  if (!grid) return;
  let rows = filterByScope(FavouritesCache.rows);
  if (FAV_VIEW_STATE.sort === 'name') {
    rows = [...rows].sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  } else if (FAV_VIEW_STATE.sort === 'added') {
    rows = [...rows].sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
  } else {
    rows = [...rows].sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  }

  if (!rows.length) {
    grid.innerHTML = `
      <div class="fav-view-empty">
        <div class="fav-view-empty-glyph">⭐</div>
        <p>No favourites in this view yet.</p>
        <p class="fav-view-empty-hint">Click the ☆ on any chat, project, workflow, scheduled task, or artifact to pin it here.</p>
      </div>`;
    return;
  }

  grid.innerHTML = rows.map(row => {
    const def  = FAVOURITES_TYPE_DEFAULTS[row.item_type] || {};
    const rawIcon = row.icon || row.source_icon || '';
    // Treat the seeded type-default emoji as "no custom icon picked" so cards
    // fall back to the line-art glyph (matching sidebar style). User-picked
    // icons differ from the default and are honoured verbatim.
    const customIcon = (rawIcon && rawIcon !== def.icon) ? rawIcon : '';
    const ic   = customIcon || def.icon || '⭐';
    const ago  = row.updated_at ? favRelativeTime(row.updated_at * 1000) : '';
    const scopeBadge = scopeBadgeHtml(row);
    const typeLabel  = def.label || row.item_type;
    const unavailable = row.available === false;
    // Image precedence: per-favourite override > underlying item's image > neutral bg
    const imageUrl = row.image_path
      ? `/v1/favourites/image/${encodeURIComponent(row.image_path)}`
      : (row.source_image_url || '');
    const artClass = imageUrl ? 'fav-view-card-art has-image' : 'fav-view-card-art';
    const bg = imageUrl
      ? `style="background-image:url('${escapeHtml(imageUrl)}');background-size:cover;background-position:center"`
      : '';
    const runnable = !unavailable && (row.item_type === 'workflow' || row.item_type === 'schedule');
    const runBtn = runnable
      ? `<button class="fav-view-card-run" title="Run now" aria-label="Run now">
           <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>
         </button>`
      : '';
    return `
      <div class="fav-view-card${unavailable ? ' is-unavailable' : ''}" data-fav-id="${row.id}">
        <div class="${artClass}" ${bg}>
          ${imageUrl ? '<div class="fav-view-card-overlay"></div>' : ''}
          ${!imageUrl ? `<span class="fav-view-card-glyph">${customIcon ? escapeHtml(ic) : favouriteTypeGlyphSvg(row.item_type, 44)}</span>` : ''}
          ${runBtn}
          <button class="fav-view-card-remove" title="Remove favourite">×</button>
        </div>
        <div class="fav-view-card-info">
          <div class="fav-view-card-title">${escapeHtml(row.title || '(untitled)')}</div>
          <div class="fav-view-card-meta">
            <span class="fav-view-card-type">${escapeHtml(typeLabel)}</span>
            ${scopeBadge}
            ${ago ? `<span class="fav-view-card-time">· ${escapeHtml(ago)}</span>` : ''}
          </div>
        </div>
      </div>`;
  }).join('');

  grid.querySelectorAll('.fav-view-card').forEach(card => {
    const id = parseInt(card.dataset.favId, 10);
    const row = FavouritesCache.rows.find(r => r.id === id);
    if (!row) return;
    card.addEventListener('click', (ev) => {
      // Don't open when × or ▶ Run is clicked.
      if (ev.target.closest('.fav-view-card-remove')) return;
      if (ev.target.closest('.fav-view-card-run')) return;
      if (row.available === false) {
        flashToast('This item is no longer accessible', true);
        return;
      }
      openFavouriteRow(row);
    });
    const removeBtn = card.querySelector('.fav-view-card-remove');
    if (removeBtn) {
      removeBtn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        try {
          await API.del(`/v1/favourites/${row.id}`);
          FavouritesCache.removeById(row.id);
          flashToast('Removed from favourites');
          window.dispatchEvent(new CustomEvent('favourites:changed'));
        } catch (e) {
          flashToast(`Remove failed: ${e.message || e}`, true);
        }
      });
    }
    const runBtn = card.querySelector('.fav-view-card-run');
    if (runBtn) {
      runBtn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        await runFavouriteRow(row);
      });
    }
  });
}

/* Trigger a one-shot execution for a runnable favourite (workflow / schedule).
   Reuses the same code path the dedicated views use:
     • workflow → POST /v1/agents/<agent>/workflows/<name>/run + open detail
     • schedule → manageSchedule({action:'run_now', name}) */
async function runFavouriteRow(row) {
  if (!row || row.available === false) {
    flashToast('This item is no longer accessible', true);
    return;
  }
  const t = row.item_type;
  const name = row.item_id;
  if (t === 'workflow') {
    if (typeof wfRun === 'function') {
      flashToast('Starting workflow…');
      navigateTo('workflows');
      try { await wfRun(name); } catch (e) { flashToast(`Run failed: ${e.message || e}`, true); }
    } else {
      flashToast('Workflow runner unavailable', true);
    }
  } else if (t === 'schedule') {
    try {
      await API.manageSchedule({ action: 'run_now', name });
      flashToast(`Triggered "${row.title || name}"`);
    } catch (e) {
      flashToast(`Run failed: ${e.message || e}`, true);
    }
  }
}

function scopeBadgeHtml(row) {
  if (row.scope === 'general') return `<span class="fav-view-card-scope is-general">Everyone</span>`;
  if (row.scope === 'team') {
    const t = (state.userTeams || []).find(x => x.id === row.scope_id);
    return `<span class="fav-view-card-scope is-team">Team · ${escapeHtml(t?.name || row.scope_id)}</span>`;
  }
  return `<span class="fav-view-card-scope is-user">Mine</span>`;
}

function favRelativeTime(ms) {
  if (!ms) return '';
  const diff = Date.now() - ms;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  try { return new Date(ms).toLocaleDateString(); } catch(_) { return ''; }
}

function favViewSort(value) {
  FAV_VIEW_STATE.sort = value || 'recent';
  renderFavouritesGrid();
}

async function favViewClear() {
  const s = FAV_VIEW_STATE.scope;
  let scope, scope_id, label;
  const userId = state.authUser?.id || '';
  if (s === 'mine') { scope = 'user'; scope_id = userId; label = 'your personal'; }
  else if (s === 'general') { scope = 'general'; scope_id = ''; label = 'global'; }
  else if (s.startsWith('team:')) {
    scope = 'team'; scope_id = s.slice(5);
    const t = (state.userTeams || []).find(x => x.id === scope_id);
    label = `team "${t?.name || scope_id}"`;
  } else {
    flashToast('Pick a specific scope (Mine / Team / Everyone) before clearing.', true);
    return;
  }
  if (!await showConfirmDanger(`Remove all ${label} favourites? This can't be undone.`, 'Clear favourites', 'Remove all')) return;
  try {
    const r = await API.del(`/v1/favourites?scope=${encodeURIComponent(scope)}&scope_id=${encodeURIComponent(scope_id)}`);
    flashToast(`Removed ${r?.removed || 0} favourite(s)`);
    await FavouritesCache.load(true);
    window.dispatchEvent(new CustomEvent('favourites:changed'));
    renderFavouritesScopeChips();
    renderFavouritesGrid();
  } catch (e) {
    flashToast(`Clear failed: ${e.message || e}`, true);
  }
}

window.loadFavouritesView = loadFavouritesView;
window.favViewSort = favViewSort;
window.favViewClear = favViewClear;

/* Expose globally for nav.js / chat.js / etc. */
window.Favourites = {
  cache: FavouritesCache,
  mount: mountFavouriteStar,
  refresh: refreshStarButton,
  reload: () => FavouritesCache.load(true).then(() => renderRecentFavourites()),
  renderSidebar: renderRecentFavourites,
  openRow: openFavouriteRow,
  TYPE_DEFAULTS: FAVOURITES_TYPE_DEFAULTS,
  typeGlyphSvg: favouriteTypeGlyphSvg,
};
