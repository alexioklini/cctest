'use strict';

/* ═══════════════════════════════════════════════════════════
   SHARING — generic visibility dialog (chats · projects ·
   schedules · workflows · artifacts). One mechanism:
   private / users / team / global, owner-managed, transferable.
   Reuses the .fav-modal-overlay CSS for layout.
   ═══════════════════════════════════════════════════════════ */

const SHARE_VIS_LABELS = {
  private: 'Private — only me',
  users:   'Specific people',
  team:    'Team',
  global:  'Everyone (global)',
};

const SHARE_VIS_PILL = {
  private: { txt: 'PRIVATE',  cls: 'share-pill-n' },
  users:   { txt: 'PEOPLE',   cls: 'share-pill-w' },
  team:    { txt: 'TEAM',     cls: 'share-pill-i' },
  global:  { txt: 'GLOBAL',   cls: 'share-pill-g' },
};

function shareVisibilityPillHtml(visibility, extraCount, teamName) {
  const v = visibility || 'private';
  const p = SHARE_VIS_PILL[v] || SHARE_VIS_PILL.private;
  let txt = p.txt;
  if (v === 'team' && teamName) txt = 'TEAM · ' + String(teamName).toUpperCase().slice(0, 10);
  if (v === 'users' && extraCount) txt = `${extraCount} ${extraCount === 1 ? 'PERSON' : 'PEOPLE'}`;
  return `<span class="share-pill ${p.cls}" title="Visibility: ${SHARE_VIS_LABELS[v] || v}">${txt}</span>`;
}

/* A small people-icon button you can drop into any header. onClick opens the
   share dialog for (itemType, itemId, agentId). */
function shareButton(itemType, itemId, agentId, opts) {
  opts = opts || {};
  const b = document.createElement('button');
  b.className = 'share-btn' + (opts.className ? ' ' + opts.className : '');
  b.title = 'Share / visibility';
  b.setAttribute('aria-label', 'Share');
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
    showToast('Could not load sharing info: ' + e.message, true);
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
        <div class="fav-modal-head"><h3>Share — ${escapeHtml(titleName)}</h3><button class="fav-modal-close" aria-label="Close">✕</button></div>
        <div class="fav-modal-body">
          <p class="share-line">Produced by ${escapeHtml(data.parent_label || 'a parent item')}.</p>
          <p class="share-line">Visible to: ${shareVisibilityPillHtml(data.effective_visibility)} ${cur ? '<span class="share-muted">(restricted)</span>' : '<span class="share-muted">(inherited)</span>'}</p>
          ${canMng ? `
          <div class="share-section">
            <label class="share-radio"><input type="radio" name="share-art" value="" ${cur === '' ? 'checked' : ''}> Inherit from parent (${SHARE_VIS_LABELS[parentVis] || parentVis})</label>
            <label class="share-radio"><input type="radio" name="share-art" value="private" ${cur === 'private' ? 'checked' : ''}> Restrict to private (owner of the parent only)</label>
            <p class="share-muted">An artifact can only be made narrower than its parent — never wider.</p>
          </div>` : `<p class="share-muted">Only the parent's owner (or an admin) can restrict this artifact.</p>`}
        </div>
        <div class="fav-modal-foot">
          <button class="fav-btn" data-act="cancel">Close</button>
          ${canMng ? '<button class="fav-btn fav-btn-primary" data-act="save-art">Save</button>' : ''}
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
        showToast('Saved'); closeShareModal();
        if (typeof opts.onChange === 'function') opts.onChange();
      } catch (e) { showToast('Save failed: ' + e.message, true); }
    });
    return;
  }

  // STANDARD item (chat / project / schedule / workflow).
  const canMng = !!data.caller_can_manage;
  if (!canMng) {
    // read-only summary
    const rows = [];
    if (data.owner_team_name) rows.push(`<div class="share-acl-row"><span>${escapeHtml(data.owner_team_name)} (team)</span><span class="share-pill share-pill-g">VIA TEAM</span></div>`);
    for (const m of (data.extra_members || [])) rows.push(`<div class="share-acl-row"><span>${escapeHtml(m.display_name)}</span><span class="share-pill share-pill-n">EXTRA GRANT</span></div>`);
    overlay.innerHTML = `
      <div class="fav-modal share-modal" role="dialog" aria-modal="true">
        <div class="fav-modal-head"><h3>Shared with — ${escapeHtml(titleName)}</h3><button class="fav-modal-close" aria-label="Close">✕</button></div>
        <div class="fav-modal-body">
          <div class="share-kv"><span>Owner</span><span>${escapeHtml(data.owner_display_name || data.owner_user_id || '—')}</span></div>
          <div class="share-kv"><span>Visibility</span><span>${shareVisibilityPillHtml(data.visibility, (data.extra_members||[]).length, data.owner_team_name)}</span></div>
          ${rows.length ? `<div class="share-section"><div class="share-muted" style="margin-bottom:4px">Shared with</div>${rows.join('')}</div>` : ''}
          <p class="share-muted">Only the owner (or an admin) can change who this is shared with.</p>
        </div>
        <div class="fav-modal-foot"><button class="fav-btn" data-act="cancel">Close</button></div>
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
      <div class="fav-modal-head"><h3>Share — ${escapeHtml(titleName)}</h3><button class="fav-modal-close" aria-label="Close">✕</button></div>
      <div class="fav-modal-body">
        <div class="share-kv">
          <span>Owner</span>
          <span>${escapeHtml(data.owner_display_name || data.owner_user_id || 'you')}
            ${data.transferable ? '<button class="fav-btn fav-btn-sm" data-act="transfer">Transfer ownership…</button>' : ''}</span>
        </div>
        <div class="share-section">
          <div class="share-muted" style="margin-bottom:6px">VISIBILITY</div>
          <label class="share-radio"><input type="radio" name="share-vis" value="private"> Private — only me</label>
          <label class="share-radio"><input type="radio" name="share-vis" value="users"> Specific people</label>
          <label class="share-radio"><input type="radio" name="share-vis" value="team"> Team:
            <select id="share-team-sel" ${myTeams.length ? '' : 'disabled'}>${
              myTeams.length ? myTeams.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('') : '<option>(no teams)</option>'
            }</select></label>
          <label class="share-radio"><input type="radio" name="share-vis" value="global"> Everyone (global)</label>
        </div>
        <div class="share-section" id="share-extras-sect">
          <div class="share-muted" style="margin-bottom:4px">Also share with (extra people)</div>
          <div id="share-extras-chips" class="share-chips"></div>
          <div class="share-add-row"><select id="share-extras-add"></select><button class="fav-btn fav-btn-sm" data-act="add-extra">+ add</button></div>
        </div>
        <div class="share-section" id="share-excl-sect" style="display:none">
          <div class="share-muted" style="margin-bottom:4px">Exclude these people (only for global)</div>
          <div id="share-excl-chips" class="share-chips"></div>
          <div class="share-add-row"><select id="share-excl-add"></select><button class="fav-btn fav-btn-sm" data-act="add-excl">+ add</button></div>
        </div>
      </div>
      <div class="fav-modal-foot">
        <button class="fav-btn" data-act="cancel">Cancel</button>
        <button class="fav-btn fav-btn-primary" data-act="save">Save</button>
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
    ec.innerHTML = cur.extras.map(id => `<span class="share-chip" data-id="${id}">${escapeHtml(nameOf(id))} <button data-rm-extra="${id}">✕</button></span>`).join('') || '<span class="share-muted">none</span>';
    ec.querySelectorAll('[data-rm-extra]').forEach(b => b.addEventListener('click', () => { cur.extras = cur.extras.filter(x => x !== b.dataset.rmExtra); renderChips(); }));
    const xc = overlay.querySelector('#share-excl-chips');
    xc.innerHTML = cur.excluded.map(id => `<span class="share-chip" data-id="${id}">${escapeHtml(nameOf(id))} <button data-rm-excl="${id}">✕</button></span>`).join('') || '<span class="share-muted">none</span>';
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
      if (!body.owner_team_id || body.owner_team_id === '(no teams)') { showToast('Pick a team first', true); return; }
    }
    try {
      const r = await API.post('/v1/share', body);
      showToast(r.claimed_ownership ? 'Saved — you are now the owner of this item.' : 'Saved');
      closeShareModal();
      if (typeof opts.onChange === 'function') opts.onChange();
    } catch (e) { showToast('Save failed: ' + e.message, true); }
  });
}

async function shareTransferDialog(itemType, itemId, agentId, titleName, allUsers, currentOwner, opts) {
  closeShareModal();
  const overlay = document.createElement('div');
  overlay.className = 'fav-modal-overlay';
  overlay.id = 'share-modal-overlay';
  const candidates = allUsers.filter(u => u.id !== currentOwner);
  const warnLines = (itemType === 'schedule')
    ? ['This task\'s saved memory stays in your wing; new memory goes to the new owner\'s.',
       'Future runs bill against the new owner\'s quota.',
       'The new owner becomes the only one who can re-share or delete it.']
    : ['This item\'s saved memory stays in your wing; new memory goes to the new owner\'s.',
       'The new owner becomes the only one who can re-share or delete it.'];
  overlay.innerHTML = `
    <div class="fav-modal share-modal" role="dialog" aria-modal="true">
      <div class="fav-modal-head"><h3>Transfer ownership</h3><button class="fav-modal-close" aria-label="Close">✕</button></div>
      <div class="fav-modal-body">
        <div class="share-kv"><span>New owner</span><span><select id="share-new-owner">${candidates.map(u => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join('')}</select></span></div>
        <div class="share-warn">${warnLines.map(l => `<div>• ${escapeHtml(l)}</div>`).join('')}</div>
        <div class="share-kv"><span>Type the name to confirm</span><span><input type="text" id="share-confirm" placeholder="${escapeHtml(titleName)}"></span></div>
      </div>
      <div class="fav-modal-foot"><button class="fav-btn" data-act="cancel">Cancel</button><button class="fav-btn fav-btn-danger" data-act="go" disabled>Transfer</button></div>
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
      showToast('Ownership transferred'); closeShareModal();
      if (typeof opts?.onChange === 'function') opts.onChange();
    } catch (e) { showToast('Transfer failed: ' + e.message, true); }
  });
}

// Small HTML escaper (utils.js may already have one; define a fallback).
if (typeof escapeHtml === 'undefined') {
  window.escapeHtml = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  };
}
