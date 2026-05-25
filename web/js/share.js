'use strict';

/* ═══════════════════════════════════════════════════════════
   SHARING — generic visibility dialog (chats · projects ·
   schedules · workflows · artifacts). One mechanism:
   private / users / team / global, owner-managed, transferable.
   Reuses the .fav-modal-overlay CSS for layout.
   ═══════════════════════════════════════════════════════════ */

const SHARE_VIS_LABELS = {
  private: 'Privat – nur ich',
  users:   'Bestimmte Personen',
  team:    'Team',
  global:  'Jeder (global)',
};

const SHARE_VIS_PILL = {
  private: { txt: 'PRIVAT',   cls: 'share-pill-n' },
  users:   { txt: 'PERSONEN', cls: 'share-pill-w' },
  team:    { txt: 'TEAM',     cls: 'share-pill-i' },
  global:  { txt: 'GLOBAL',   cls: 'share-pill-g' },
};

function shareVisibilityPillHtml(visibility, extraCount, teamName) {
  const v = visibility || 'private';
  const p = SHARE_VIS_PILL[v] || SHARE_VIS_PILL.private;
  let txt = p.txt;
  if (v === 'team' && teamName) txt = 'TEAM · ' + String(teamName).toUpperCase().slice(0, 10);
  if (v === 'users' && extraCount) txt = `${extraCount} ${extraCount === 1 ? 'PERSON' : 'PERSONEN'}`;
  return `<span class="share-pill ${p.cls}" title="Sichtbarkeit: ${SHARE_VIS_LABELS[v] || v}">${txt}</span>`;
}

/* A small people-icon button you can drop into any header. onClick opens the
   share dialog for (itemType, itemId, agentId). */
function shareButton(itemType, itemId, agentId, opts) {
  opts = opts || {};
  const b = document.createElement('button');
  b.className = 'share-btn' + (opts.className ? ' ' + opts.className : '');
  b.title = 'Teilen / Sichtbarkeit';
  b.setAttribute('aria-label', 'Teilen');
  b.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/></svg>`;
  b.addEventListener('click', (e) => { e.stopPropagation(); shareDialog(itemType, itemId, agentId, opts); });
  return b;
}

let _shareUserCache = null;
async function _shareLoadUsers() {
  if (_shareUserCache) return _shareUserCache;
  try {
    const r = await API.get('/v1/auth/users/lookup');
    _shareUserCache = (r.users || r || []).map(u => ({ id: u.id, name: u.display_name || u.username || u.id }));
  } catch (_e) {
    _shareUserCache = [];
  }
  return _shareUserCache;
}

async function _shareLoadMyTeams() {
  try {
    const r = await API.get('/v1/user-teams');
    return (r.teams || r || []).map(t => ({ id: t.id, name: t.name || t.id }));
  } catch (_e) { return []; }
}

function closeShareModal() {
  document.getElementById('share-modal-overlay')?.remove();
}

async function shareDialog(itemType, itemId, agentId, opts) {
  opts = opts || {};
  closeShareModal();
  let data;
  try {
    const qs = `item_type=${encodeURIComponent(itemType)}&item_id=${encodeURIComponent(itemId)}` + (agentId ? `&agent_id=${encodeURIComponent(agentId)}` : '');
    data = await API.get(`/v1/share?${qs}`);
  } catch (e) {
    showToast('Freigabeinformationen konnten nicht geladen werden: ' + e.message, true);
    return;
  }
  if (data && data.error) { showToast(data.error, true); return; }

  const overlay = document.createElement('div');
  overlay.className = 'fav-modal-overlay';
  overlay.id = 'share-modal-overlay';
  const titleName = opts.title || itemId;

  // ARTIFACT — narrow-only override, no transfer / no ACL.
  if (itemType === 'artifact') {
    const canMng = !!data.caller_can_manage;
    const parentVis = data.parent_visibility || 'private';
    const cur = data.visibility_override || '';
    overlay.innerHTML = `
      <div class="fav-modal share-modal" role="dialog" aria-modal="true">
        <div class="fav-modal-head"><h3>Teilen — ${escapeHtml(titleName)}</h3><button class="fav-modal-close" aria-label="Schließen">✕</button></div>
        <div class="fav-modal-body">
          <p class="share-line">Erstellt von ${escapeHtml(data.parent_label || 'einem übergeordneten Element')}.</p>
          <p class="share-line">Sichtbar für: ${shareVisibilityPillHtml(data.effective_visibility)} ${cur ? '<span class="share-muted">(eingeschränkt)</span>' : '<span class="share-muted">(geerbt)</span>'}</p>
          ${canMng ? `
          <div class="share-section">
            <label class="share-radio"><input type="radio" name="share-art" value="" ${cur === '' ? 'checked' : ''}> Vom übergeordneten Element erben (${SHARE_VIS_LABELS[parentVis] || parentVis})</label>
            <label class="share-radio"><input type="radio" name="share-art" value="private" ${cur === 'private' ? 'checked' : ''}> Auf privat beschränken (nur Eigentümer des übergeordneten Elements)</label>
            <p class="share-muted">Ein Artefakt kann nur enger als sein übergeordnetes Element gemacht werden — nie weiter.</p>
          </div>` : `<p class="share-muted">Nur der Eigentümer des übergeordneten Elements (oder ein Administrator) kann dieses Artefakt einschränken.</p>`}
        </div>
        <div class="fav-modal-foot">
          <button class="fav-btn" data-act="cancel">Schließen</button>
          ${canMng ? '<button class="fav-btn fav-btn-primary" data-act="save-art">Speichern</button>' : ''}
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) closeShareModal(); });
    overlay.querySelector('.fav-modal-close').addEventListener('click', closeShareModal);
    overlay.querySelector('[data-act="cancel"]').addEventListener('click', closeShareModal);
    const saveBtn = overlay.querySelector('[data-act="save-art"]');
    if (saveBtn) saveBtn.addEventListener('click', async () => {
      const v = overlay.querySelector('input[name="share-art"]:checked')?.value || '';
      try {
        await API.post('/v1/share', { item_type: 'artifact', item_id: itemId, visibility_override: v });
        showToast('Gespeichert'); closeShareModal();
        if (typeof opts.onChange === 'function') opts.onChange();
      } catch (e) { showToast('Speichern fehlgeschlagen: ' + e.message, true); }
    });
    return;
  }

  // STANDARD item (chat / project / schedule / workflow).
  const canMng = !!data.caller_can_manage;
  if (!canMng) {
    // read-only summary
    const rows = [];
    if (data.owner_team_name) rows.push(`<div class="share-acl-row"><span>${escapeHtml(data.owner_team_name)} (Team)</span><span class="share-pill share-pill-g">ÜBER TEAM</span></div>`);
    for (const m of (data.extra_members || [])) rows.push(`<div class="share-acl-row"><span>${escapeHtml(m.display_name)}</span><span class="share-pill share-pill-n">EXTRA-FREIGABE</span></div>`);
    overlay.innerHTML = `
      <div class="fav-modal share-modal" role="dialog" aria-modal="true">
        <div class="fav-modal-head"><h3>Geteilt mit — ${escapeHtml(titleName)}</h3><button class="fav-modal-close" aria-label="Schließen">✕</button></div>
        <div class="fav-modal-body">
          <div class="share-kv"><span>Eigentümer</span><span>${escapeHtml(data.owner_display_name || data.owner_user_id || '—')}</span></div>
          <div class="share-kv"><span>Sichtbarkeit</span><span>${shareVisibilityPillHtml(data.visibility, (data.extra_members||[]).length, data.owner_team_name)}</span></div>
          ${rows.length ? `<div class="share-section"><div class="share-muted" style="margin-bottom:4px">Geteilt mit</div>${rows.join('')}</div>` : ''}
          <p class="share-muted">Nur der Eigentümer (oder ein Administrator) kann ändern, mit wem dies geteilt wird.</p>
        </div>
        <div class="fav-modal-foot"><button class="fav-btn" data-act="cancel">Schließen</button></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) closeShareModal(); });
    overlay.querySelector('.fav-modal-close').addEventListener('click', closeShareModal);
    overlay.querySelector('[data-act="cancel"]').addEventListener('click', closeShareModal);
    return;
  }

  // owner / admin editable view
  const [allUsers, myTeams] = await Promise.all([_shareLoadUsers(), _shareLoadMyTeams()]);
  const _normVis = (v) => (v === 'user' || !v) ? 'private' : v;  // legacy alias
  const cur = {
    visibility: _normVis(data.visibility),
    owner_team_id: data.owner_team_id || (myTeams[0] && myTeams[0].id) || '',
    extras: (data.extra_members || []).map(m => m.id),
    excluded: (data.excluded || []).map(m => m.id),
  };
  const nameOf = (id) => (allUsers.find(u => u.id === id) || {}).name || id;

  overlay.innerHTML = `
    <div class="fav-modal share-modal" role="dialog" aria-modal="true">
      <div class="fav-modal-head"><h3>Teilen — ${escapeHtml(titleName)}</h3><button class="fav-modal-close" aria-label="Schließen">✕</button></div>
      <div class="fav-modal-body">
        <div class="share-kv">
          <span>Eigentümer</span>
          <span>${escapeHtml(data.owner_display_name || data.owner_user_id || 'Sie')}
            ${data.transferable ? '<button class="fav-btn fav-btn-sm" data-act="transfer">Eigentum übertragen…</button>' : ''}</span>
        </div>
        <div class="share-section">
          <div class="share-muted" style="margin-bottom:6px">SICHTBARKEIT</div>
          <label class="share-radio"><input type="radio" name="share-vis" value="private"> Privat – nur ich</label>
          <label class="share-radio"><input type="radio" name="share-vis" value="users"> Bestimmte Personen</label>
          <label class="share-radio"><input type="radio" name="share-vis" value="team"> Team:
            <select id="share-team-sel" ${myTeams.length ? '' : 'disabled'}>${
              myTeams.length ? myTeams.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('') : '<option>(keine Teams)</option>'
            }</select></label>
          <label class="share-radio"><input type="radio" name="share-vis" value="global"> Jeder (global)</label>
        </div>
        <div class="share-section" id="share-extras-sect">
          <div class="share-muted" style="margin-bottom:4px">Außerdem teilen mit (zusätzliche Personen)</div>
          <div id="share-extras-chips" class="share-chips"></div>
          <div class="share-add-row"><select id="share-extras-add"></select><button class="fav-btn fav-btn-sm" data-act="add-extra">+ hinzufügen</button></div>
        </div>
        <div class="share-section" id="share-excl-sect" style="display:none">
          <div class="share-muted" style="margin-bottom:4px">Diese Personen ausschließen (nur für global)</div>
          <div id="share-excl-chips" class="share-chips"></div>
          <div class="share-add-row"><select id="share-excl-add"></select><button class="fav-btn fav-btn-sm" data-act="add-excl">+ hinzufügen</button></div>
        </div>
      </div>
      <div class="fav-modal-foot">
        <button class="fav-btn" data-act="cancel">Abbrechen</button>
        <button class="fav-btn fav-btn-primary" data-act="save">Speichern</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) closeShareModal(); });
  overlay.querySelector('.fav-modal-close').addEventListener('click', closeShareModal);
  overlay.querySelector('[data-act="cancel"]').addEventListener('click', closeShareModal);

  const teamSel = overlay.querySelector('#share-team-sel');
  if (cur.owner_team_id) teamSel.value = cur.owner_team_id;
  overlay.querySelector(`input[name="share-vis"][value="${cur.visibility}"]`).checked = true;

  function fillUserSelect(sel, excludeIds) {
    sel.innerHTML = '';
    for (const u of allUsers) {
      if (excludeIds.includes(u.id)) continue;
      const o = document.createElement('option'); o.value = u.id; o.textContent = u.name; sel.appendChild(o);
    }
  }
  function renderChips() {
    const ec = overlay.querySelector('#share-extras-chips');
    ec.innerHTML = cur.extras.map(id => `<span class="share-chip" data-id="${id}">${escapeHtml(nameOf(id))} <button data-rm-extra="${id}">✕</button></span>`).join('') || '<span class="share-muted">keine</span>';
    ec.querySelectorAll('[data-rm-extra]').forEach(b => b.addEventListener('click', () => { cur.extras = cur.extras.filter(x => x !== b.dataset.rmExtra); renderChips(); }));
    const xc = overlay.querySelector('#share-excl-chips');
    xc.innerHTML = cur.excluded.map(id => `<span class="share-chip" data-id="${id}">${escapeHtml(nameOf(id))} <button data-rm-excl="${id}">✕</button></span>`).join('') || '<span class="share-muted">keine</span>';
    xc.querySelectorAll('[data-rm-excl]').forEach(b => b.addEventListener('click', () => { cur.excluded = cur.excluded.filter(x => x !== b.dataset.rmExcl); renderChips(); }));
    fillUserSelect(overlay.querySelector('#share-extras-add'), cur.extras.concat([data.owner_user_id]));
    fillUserSelect(overlay.querySelector('#share-excl-add'), cur.excluded.concat([data.owner_user_id]));
  }
  function syncSections() {
    const v = overlay.querySelector('input[name="share-vis"]:checked').value;
    overlay.querySelector('#share-extras-sect').style.display = (v === 'global') ? 'none' : '';
    overlay.querySelector('#share-excl-sect').style.display = (v === 'global') ? '' : 'none';
  }
  overlay.querySelectorAll('input[name="share-vis"]').forEach(r => r.addEventListener('change', syncSections));
  overlay.querySelector('[data-act="add-extra"]').addEventListener('click', () => {
    const id = overlay.querySelector('#share-extras-add').value; if (id && !cur.extras.includes(id)) { cur.extras.push(id); renderChips(); }
  });
  overlay.querySelector('[data-act="add-excl"]').addEventListener('click', () => {
    const id = overlay.querySelector('#share-excl-add').value; if (id && !cur.excluded.includes(id)) { cur.excluded.push(id); renderChips(); }
  });
  renderChips(); syncSections();

  const transferBtn = overlay.querySelector('[data-act="transfer"]');
  if (transferBtn) transferBtn.addEventListener('click', () => shareTransferDialog(itemType, itemId, agentId, titleName, allUsers, data.owner_user_id, opts));

  overlay.querySelector('[data-act="save"]').addEventListener('click', async () => {
    const v = overlay.querySelector('input[name="share-vis"]:checked').value;
    const body = { item_type: itemType, item_id: itemId, visibility: v,
                   extra_member_user_ids: cur.extras, excluded_user_ids: cur.excluded };
    if (agentId) body.agent_id = agentId;
    if (v === 'team') {
      body.owner_team_id = teamSel.value;
      if (!body.owner_team_id || body.owner_team_id === '(keine Teams)') { showToast('Wählen Sie zuerst ein Team', true); return; }
    }
    try {
      const r = await API.post('/v1/share', body);
      showToast(r.claimed_ownership ? 'Gespeichert — Sie sind jetzt Eigentümer dieses Elements.' : 'Gespeichert');
      closeShareModal();
      if (typeof opts.onChange === 'function') opts.onChange();
    } catch (e) { showToast('Speichern fehlgeschlagen: ' + e.message, true); }
  });
}

async function shareTransferDialog(itemType, itemId, agentId, titleName, allUsers, currentOwner, opts) {
  closeShareModal();
  const overlay = document.createElement('div');
  overlay.className = 'fav-modal-overlay';
  overlay.id = 'share-modal-overlay';
  const candidates = allUsers.filter(u => u.id !== currentOwner);
  const warnLines = (itemType === 'schedule')
    ? ['Der gespeicherte Speicher dieses Tasks bleibt in Ihrem Wing; neuer Speicher geht in den des neuen Eigentümers.',
       'Künftige Ausführungen werden dem Kontingent des neuen Eigentümers belastet.',
       'Der neue Eigentümer ist dann der Einzige, der das Element neu teilen oder löschen kann.']
    : ['Der gespeicherte Speicher dieses Elements bleibt in Ihrem Wing; neuer Speicher geht in den des neuen Eigentümers.',
       'Der neue Eigentümer ist dann der Einzige, der das Element neu teilen oder löschen kann.'];
  overlay.innerHTML = `
    <div class="fav-modal share-modal" role="dialog" aria-modal="true">
      <div class="fav-modal-head"><h3>Eigentum übertragen</h3><button class="fav-modal-close" aria-label="Schließen">✕</button></div>
      <div class="fav-modal-body">
        <div class="share-kv"><span>Neuer Eigentümer</span><span><select id="share-new-owner">${candidates.map(u => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join('')}</select></span></div>
        <div class="share-warn">${warnLines.map(l => `<div>• ${escapeHtml(l)}</div>`).join('')}</div>
        <div class="share-kv"><span>Zur Bestätigung den Namen eingeben</span><span><input type="text" id="share-confirm" placeholder="${escapeHtml(titleName)}"></span></div>
      </div>
      <div class="fav-modal-foot"><button class="fav-btn" data-act="cancel">Abbrechen</button><button class="fav-btn fav-btn-danger" data-act="go" disabled>Übertragen</button></div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) closeShareModal(); });
  overlay.querySelector('.fav-modal-close').addEventListener('click', closeShareModal);
  overlay.querySelector('[data-act="cancel"]').addEventListener('click', closeShareModal);
  const goBtn = overlay.querySelector('[data-act="go"]');
  const confirmInput = overlay.querySelector('#share-confirm');
  confirmInput.addEventListener('input', () => { goBtn.disabled = (confirmInput.value.trim() !== String(titleName).trim()); });
  goBtn.addEventListener('click', async () => {
    const newOwner = overlay.querySelector('#share-new-owner').value;
    if (!newOwner) return;
    const body = { item_type: itemType, item_id: itemId, new_owner_user_id: newOwner };
    if (agentId) body.agent_id = agentId;
    try {
      await API.post('/v1/share/transfer', body);
      showToast('Eigentum übertragen'); closeShareModal();
      if (typeof opts?.onChange === 'function') opts.onChange();
    } catch (e) { showToast('Übertragung fehlgeschlagen: ' + e.message, true); }
  });
}

// Small HTML escaper (utils.js may already have one; define a fallback).
if (typeof escapeHtml === 'undefined') {
  window.escapeHtml = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  };
}
