// user_admin.js — admin user/team management + per-user settings panel. Split from init.js (Tier F Phase 4). Global <script>, no modules.

// ── User Management Modal (admin only) ──
async function openUserManagement() {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  try {
    const data = await API.get('/v1/auth/users');
    const users = data.users || [];
    const meId = state.authUser?.id || '';
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    const roleBadge = (r) => `<span class="role-badge ${esc(r)}">${esc(roleLabelDe(r))}</span>`;
    const statusDot = (disabled) => `<span title="${disabled?'deaktiviert':'aktiv'}" style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle;background:${disabled?'#dc2626':'#16a34a'}"></span>`;
    modal.innerHTML = `
      <div class="modal-content" style="max-width:920px">
        <div class="modal-header"><h2>Benutzerverwaltung</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <div class="cfg-help" style="flex:1">Selbstregistrierung ist deaktiviert. Benutzer müssen hier angelegt werden.</div>
            <button class="um-btn" onclick="document.querySelector('.modal-overlay').remove();openUserTeams()">Teams &rsaquo;</button>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="text-align:left;border-bottom:2px solid var(--border-light);color:var(--text-secondary);font-size:11px;text-transform:uppercase;letter-spacing:0.04em">
              <th style="padding:8px">Status</th>
              <th style="padding:8px">Benutzername</th>
              <th style="padding:8px">Anzeigename</th>
              <th style="padding:8px">Rolle${helpIcon('Benutzer — nur Chat innerhalb freigegebener Agents und Modelle.\n\nHauptbenutzer — Teamleitung: kann eigene Benutzer-Teams erstellen und verwalten.\n\nAdministrator — voller Konfigurationszugriff auf alle Agents, Modelle und Funktionen.')}</th>
              <th style="padding:8px">Letzte Anmeldung</th>
              <th style="padding:8px;text-align:right">Aktionen</th>
            </tr></thead>
            <tbody>
              ${users.map(u => {
                const isSelf = u.id === meId;
                const isDefaultAdmin = u.username === 'admin';
                return `<tr style="border-bottom:1px solid var(--border-light)">
                <td style="padding:8px">${statusDot(u.disabled)}${u.disabled?'<span style="font-size:11px;color:#dc2626">deaktiviert</span>':'<span style="font-size:11px;color:#16a34a">aktiv</span>'}</td>
                <td style="padding:8px"><code>${esc(u.username)}</code>${isSelf?' <span style="font-size:10px;color:var(--text-secondary)">(Sie)</span>':''}</td>
                <td style="padding:8px">${esc(u.display_name || '')}</td>
                <td style="padding:8px">
                  <select onchange="changeUserRole('${esc(u.id)}', this.value)" ${isSelf?'disabled title="Eigene Rolle kann nicht geändert werden"':''} style="font-size:12px;padding:2px 6px;border-radius:4px;border:1px solid var(--border-light)">
                    ${['admin','poweruser','user'].map(r => `<option value="${r}" ${u.role===r?'selected':''}>${esc(roleLabelDe(r))}</option>`).join('')}
                  </select>
                </td>
                <td style="padding:8px;color:var(--text-secondary);font-size:12px">${u.last_login ? new Date(u.last_login*1000).toLocaleString() : 'Nie'}</td>
                <td style="padding:8px;text-align:right;white-space:nowrap">
                  <button class="um-btn" onclick="openUserPermissions('${esc(u.id)}','${esc(u.username)}')" title="Berechtigungen">&#9881;</button>
                  <button class="um-btn" onclick="adminResetPassword('${esc(u.id)}','${esc(u.username)}')" title="Passwort zurücksetzen">&#128273;</button>
                  ${isSelf ? '' : (u.disabled
                    ? `<button class="um-btn" onclick="setUserDisabled('${esc(u.id)}', false)" title="Benutzer aktivieren" style="color:#16a34a">&#9654;</button>`
                    : `<button class="um-btn" onclick="setUserDisabled('${esc(u.id)}', true)" title="Benutzer deaktivieren" style="color:#b45309">&#9209;</button>`)}
                  ${(!isSelf && !isDefaultAdmin) ? `<button class="um-btn" onclick="deleteUser('${esc(u.id)}','${esc(u.username)}')" title="Benutzer löschen" style="color:#dc2626">&#128465;</button>` : ''}
                </td>
              </tr>`;
              }).join('')}
            </tbody>
          </table>
          <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border-light)">
            <h3 style="font-size:14px;margin:0 0 12px">Benutzer hinzufügen</h3>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <input id="new-user-name" placeholder="Benutzername" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-display" placeholder="Anzeigename (optional)" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-pass" type="password" placeholder="Passwort (min. 6)" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <select id="new-user-role" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
                <option value="user">${esc(roleLabelDe('user'))}</option><option value="poweruser">${esc(roleLabelDe('poweruser'))}</option><option value="admin">${esc(roleLabelDe('admin'))}</option>
              </select>
              <button onclick="addUser()" class="auth-btn" style="width:auto;padding:8px 20px">Hinzufügen</button>
            </div>
          </div>
        </div>
      </div>
    `;
    if (!document.getElementById('um-btn-style')) {
      const st = document.createElement('style');
      st.id = 'um-btn-style';
      st.textContent = `.um-btn{background:none;border:1px solid var(--border-light);border-radius:6px;padding:3px 8px;margin-left:4px;cursor:pointer;font-size:13px;line-height:1}.um-btn:hover{background:var(--bg-200)}`;
      document.head.appendChild(st);
    }
    document.body.appendChild(modal);
  } catch(e) { console.error('Failed to load users:', e); showAlert('Benutzer konnten nicht geladen werden: ' + e.message); }
}

async function changeUserRole(userId, role) {
  try {
    const r = await API.post('/v1/auth/users', {action:'update', user_id: userId, updates:{role}});
    if (r && r.error) { await showAlert(r.error); return; }
    showToast?.('Rolle aktualisiert');
  } catch(e) { await showAlert('Rolle konnte nicht aktualisiert werden: ' + e.message); }
}

async function deleteUser(userId, username) {
  if (!await showConfirmDanger(`Benutzer "${username}" löschen? Dies kann nicht rückgängig gemacht werden.`, 'Benutzer löschen', 'Löschen')) return;
  try {
    const r = await API.post('/v1/auth/users', {action:'delete', user_id: userId});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { await showAlert('Benutzer konnte nicht gelöscht werden: ' + e.message); }
}

async function adminResetPassword(userId, username) {
  const pw = await showPrompt(`Neues Passwort eingeben (min. 6 Zeichen):`, '', `Passwort zurücksetzen für "${username}"`);
  if (pw === null) return;
  if (pw.length < 6) { await showAlert('Passwort muss mindestens 6 Zeichen lang sein', 'Ungültiges Passwort'); return; }
  try {
    const r = await API.post('/v1/auth/users', {action:'reset_password', user_id: userId, new_password: pw});
    if (r && r.error) { await showAlert(r.error); return; }
    await showAlert(`Passwort für "${username}" zurückgesetzt. Teilen Sie das neue Passwort sicher mit.`, 'Passwort zurückgesetzt');
  } catch(e) { await showAlert('Passwort konnte nicht zurückgesetzt werden: ' + e.message); }
}

async function setUserDisabled(userId, disabled) {
  try {
    const r = await API.post('/v1/auth/users', {action: disabled ? 'disable' : 'enable', user_id: userId});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { await showAlert('Fehlgeschlagen: ' + e.message); }
}

async function addUser() {
  const username = document.getElementById('new-user-name')?.value.trim();
  const password = document.getElementById('new-user-pass')?.value;
  const display_name = document.getElementById('new-user-display')?.value.trim();
  const role = document.getElementById('new-user-role')?.value;
  if (!username || !password) { await showAlert('Benutzername und Passwort erforderlich'); return; }
  if (password.length < 6) { await showAlert('Passwort muss mindestens 6 Zeichen lang sein'); return; }
  try {
    const r = await API.post('/v1/auth/users', {action:'create', username, password, role, display_name});
    if (r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { await showAlert('Benutzer konnte nicht hinzugefügt werden: ' + e.message); }
}

// ── Per-user Permissions Modal (admin only) ──
async function openUserPermissions(userId, username) {
  try {
    const [permData, agentsData, modelsData, userData] = await Promise.all([
      API.get('/v1/auth/permissions?user_id=' + encodeURIComponent(userId)),
      API.get('/v1/agents'),
      API.get('/v1/models'),
      API.get('/v1/auth/users').catch(() => ({users: []})),
    ]);
    const allAgents = (() => {
      const a = agentsData.agents;
      if (Array.isArray(a)) return a.map(x => x.id || x.name).filter(Boolean);
      if (a && typeof a === 'object') return Object.keys(a);
      return [];
    })().sort();
    const allModels = (() => {
      const m = modelsData.models;
      if (Array.isArray(m)) return m.map(x => typeof x === 'string' ? x : (x.id || x.name || x.model)).filter(Boolean);
      if (m && typeof m === 'object') return Object.keys(m);
      return [];
    })().sort();
    const target = (userData.users || []).find(u => u.id === userId) || {username, role: 'user'};
    const isAdmin = target.role === 'admin';
    const grants = permData.grants || {agents_direct: [], models_direct: [], agents_via_team: [], models_via_team: []};
    const directAgents = new Set(grants.agents_direct || []);
    const directModels = new Set(grants.models_direct || []);
    const teamAgents = {}; for (const g of (grants.agents_via_team || [])) teamAgents[g.agent_id] = (teamAgents[g.agent_id]||[]).concat(g.team_name);
    const teamModels = {}; for (const g of (grants.models_via_team || [])) teamModels[g.model_id] = (teamModels[g.model_id]||[]).concat(g.team_name);

    // Close the current user-management modal first
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.onclick = e => { if (e.target === modal) { modal.remove(); openUserManagement(); } };
    const row = (id, checked, kind, viaTeam) => `<div style="display:flex;align-items:center;gap:8px;padding:6px 4px;border-bottom:1px solid var(--border-light);font-size:13px">
      <label style="display:flex;align-items:center;gap:8px;flex:1;cursor:${isAdmin?'not-allowed':'pointer'};${isAdmin?'opacity:0.5':''}">
        <input type="checkbox" ${checked?'checked':''} ${isAdmin?'disabled':''} onchange="_togglePermission('${esc(userId)}', this.dataset.kind, this.dataset.id, this.checked, this)" data-kind="${esc(kind)}" data-id="${esc(id)}">
        <code style="font-size:12px">${esc(id)}</code>
      </label>
      ${viaTeam && viaTeam.length ? `<span style="font-size:10px;padding:2px 6px;background:var(--bg-base);border-radius:10px;color:var(--text-secondary)">über Team: ${esc(viaTeam.join(', '))}</span>` : ''}
    </div>`;
    modal.innerHTML = `
      <div class="modal-content" style="max-width:780px">
        <div class="modal-header">
          <h2>Berechtigungen: ${esc(username)}${isAdmin?' (Administrator — voller Zugriff)':''}${helpIcon(isAdmin
              ? 'Administratoren haben immer Zugriff auf alle Agents, Modelle und Funktionen. Die folgenden Einstellungen werden gespeichert, aber für Administratoren nicht durchgesetzt.'
              : 'Berechtigungen steuern, mit welchen Agents und Modellen dieser Benutzer chatten kann. Teammitgliedschaften ergänzen die direkten Berechtigungen. Fähigkeiten schalten den Funktionszugriff um.')}</h2>
          <button class="modal-close" onclick="document.querySelector('.modal-overlay').remove();openUserManagement()">&times;</button>
        </div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Fähigkeiten</h3>
          <div id="user-caps-row" style="display:flex;flex-wrap:wrap;gap:12px;padding:10px 12px;border:1px solid var(--border-light);border-radius:8px;margin-bottom:16px">
            ${(() => {
              const caps = (target.capabilities || {});
              const keys = ['allow_projects','allow_artifacts','allow_workflows','allow_skills_install'];
              const labels = {allow_projects:'Projekte', allow_artifacts:'Artefakte', allow_workflows:'Workflows', allow_skills_install:'Skills installieren'};
              return keys.map(k => `<label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:${isAdmin?'not-allowed':'pointer'};${isAdmin?'opacity:0.5':''}">
                <input type="checkbox" ${caps[k]?'checked':''} ${isAdmin?'disabled':''} data-cap="${k}" onchange="_toggleCapability('${esc(userId)}', this)">
                <span>${labels[k]}</span>
              </label>`).join('');
            })()}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
            <div>
              <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Agents (${allAgents.length})</h3>
              ${allAgents.map(a => row(a, directAgents.has(a), 'agent', teamAgents[a])).join('') || '<div style="color:var(--text-secondary);font-size:12px">Keine Agents verfügbar.</div>'}
            </div>
            <div>
              <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Modelle (${allModels.length})</h3>
              ${allModels.map(m => row(m, directModels.has(m), 'model', teamModels[m])).join('') || '<div style="color:var(--text-secondary);font-size:12px">Keine Modelle aktiviert.</div>'}
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  } catch(e) { await showAlert('Berechtigungen konnten nicht geladen werden: ' + e.message); }
}

async function _togglePermission(userId, kind, id, checked, el) {
  try {
    const payload = {action: checked ? 'grant' : 'revoke', kind, user_id: userId};
    if (kind === 'agent') payload.agent_id = id; else payload.model_id = id;
    const r = await API.post('/v1/auth/permissions', payload);
    if (r && r.error) { el.checked = !checked; await showAlert(r.error); return; }
  } catch(e) { el.checked = !checked; await showAlert('Fehlgeschlagen: ' + e.message); }
}

async function _toggleCapability(userId, el) {
  // Read all capability checkboxes in the row, send a full update
  const checkboxes = document.querySelectorAll('#user-caps-row input[type=checkbox][data-cap]');
  const caps = {};
  for (const cb of checkboxes) caps[cb.dataset.cap] = cb.checked;
  try {
    const r = await API.post('/v1/auth/users', {action:'update', user_id: userId, updates:{capabilities: caps}});
    if (r && r.error) { el.checked = !el.checked; await showAlert(r.error); return; }
  } catch(e) { el.checked = !el.checked; await showAlert('Fehlgeschlagen: ' + e.message); }
}

// ── User Teams Modal ──
async function openUserTeams() {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  try {
    const teamsData = await API.get('/v1/user-teams');
    const teams = teamsData.teams || [];
    const isAdmin = state.authUser?.role === 'admin';
    const usersData = isAdmin ? await API.get('/v1/auth/users').catch(() => ({users:[]})) : {users:[]};
    const allUsers = usersData.users || [];
    // Team heads: those with poweruser or admin role (users already have the role listed)
    const headCandidates = allUsers.filter(u => u.role === 'poweruser' || u.role === 'admin');

    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    const teamCard = (t) => {
      const memberIds = new Set((t.members||[]).map(m => m.id));
      const addableUsers = allUsers.filter(u => !memberIds.has(u.id) && !u.disabled);
      return `<div style="border:1px solid var(--border-light);border-radius:10px;padding:16px;margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div style="flex:1"><strong>${esc(t.name)}</strong> ${t.description ? `<span style="font-size:12px;color:var(--text-secondary);margin-left:8px">${esc(t.description)}</span>` : ''}</div>
          <button onclick="dissolveUserTeam('${esc(t.id)}')" class="um-btn" style="color:#dc2626" title="Team auflösen">Auflösen</button>
        </div>
        <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:4px">
          ${(t.members||[]).map(m => `
            <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:var(--bg-base);border:1px solid var(--border-light);border-radius:12px;font-size:12px">
              ${esc(m.display_name || m.username)}
              ${m.id === t.head_user_id ? '<span class="role-badge poweruser" style="font-size:10px;margin-left:2px">Leitung</span>'
                : `<button onclick="removeUserTeamMember('${esc(t.id)}','${esc(m.id)}')" style="background:none;border:none;cursor:pointer;color:#999;font-size:14px;padding:0;line-height:1" title="Entfernen">&times;</button>`}
            </span>
          `).join('')}
        </div>
        ${addableUsers.length ? `<div style="display:flex;gap:6px;margin-top:10px;align-items:center">
          <select id="ut-add-${esc(t.id)}" style="flex:1;font-size:12px;padding:6px;border:1px solid var(--border-light);border-radius:6px">
            <option value="">Mitglied hinzufügen...</option>
            ${addableUsers.map(u => `<option value="${esc(u.id)}">${esc(u.display_name || u.username)} (${esc(roleLabelDe(u.role))})</option>`).join('')}
          </select>
          <button class="um-btn" onclick="addUserTeamMember('${esc(t.id)}')">Hinzufügen</button>
        </div>` : ''}
      </div>`;
    };
    modal.innerHTML = `
      <div class="modal-content" style="max-width:720px">
        <div class="modal-header"><h2>Benutzer-Teams</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          ${isAdmin ? `<div style="display:flex;justify-content:flex-end;margin-bottom:12px">
            <button class="um-btn" onclick="document.querySelector('.modal-overlay').remove();openUserManagement()">&lsaquo; Benutzer</button>
          </div>` : ''}
          ${teams.length
            ? teams.map(teamCard).join('')
            : '<p style="color:var(--text-secondary);text-align:center;padding:16px">Noch keine Benutzer-Teams</p>'}
          <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border-light)">
            <h3 style="font-size:14px;margin:0 0 12px">Team erstellen</h3>
            <div style="display:flex;flex-direction:column;gap:8px">
              <input id="new-user-team-name" placeholder="Teamname" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-team-desc" placeholder="Beschreibung (optional)" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              ${isAdmin && headCandidates.length ? `
                <select id="new-user-team-head" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
                  <option value="">Teamleitung... (Standard: Sie)</option>
                  ${headCandidates.map(u => `<option value="${esc(u.id)}">${esc(u.display_name || u.username)} (${esc(roleLabelDe(u.role))})</option>`).join('')}
                </select>` : ''}
              <button onclick="createUserTeam()" class="auth-btn" style="width:auto;padding:8px 20px">Team erstellen</button>
              <div class="cfg-help">Die Teamleitung muss ein Hauptbenutzer oder Administrator sein.</div>
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  } catch(e) { console.error('Failed to load teams:', e); await showAlert('Teams konnten nicht geladen werden: ' + e.message); }
}

async function createUserTeam() {
  const name = document.getElementById('new-user-team-name')?.value.trim();
  const desc = document.getElementById('new-user-team-desc')?.value.trim();
  const head = document.getElementById('new-user-team-head')?.value;
  if (!name) { await showAlert('Teamname erforderlich'); return; }
  try {
    const body = {action:'create', name, description: desc};
    if (head) body.head_user_id = head;
    const r = await API.post('/v1/user-teams', body);
    if (r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Team konnte nicht erstellt werden: ' + e.message); }
}

async function dissolveUserTeam(teamId) {
  if (!await showConfirmDanger('Dieses Team auflösen? Mitglieder werden entfernt.', 'Team auflösen', 'Auflösen')) return;
  try {
    await API.post('/v1/user-teams', {action:'dissolve', team_id: teamId});
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Team konnte nicht aufgelöst werden: ' + e.message); }
}

async function removeUserTeamMember(teamId, userId) {
  try {
    const r = await API.post('/v1/user-teams', {action:'remove_member', team_id: teamId, user_id: userId});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Mitglied konnte nicht entfernt werden: ' + e.message); }
}

async function addUserTeamMember(teamId) {
  const sel = document.getElementById('ut-add-' + teamId);
  const uid = sel?.value;
  if (!uid) { await showAlert('Wählen Sie einen Benutzer zum Hinzufügen aus'); return; }
  try {
    const r = await API.post('/v1/user-teams', {action:'add_member', team_id: teamId, user_id: uid});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Mitglied konnte nicht hinzugefügt werden: ' + e.message); }
}

// ── Change Password Modal ──
function openChangePassword() {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  modal.innerHTML = `
    <div class="modal-content" style="max-width:400px">
      <div class="modal-header"><h2>Passwort ändern</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
      <div class="modal-body">
        <input id="cp-old" type="password" placeholder="Aktuelles Passwort" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <input id="cp-new" type="password" placeholder="Neues Passwort" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <input id="cp-new2" type="password" placeholder="Neues Passwort bestätigen" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:16px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <button onclick="doChangePassword()" class="auth-btn">Passwort ändern</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

async function doChangePassword() {
  const old_password = document.getElementById('cp-old')?.value;
  const new_password = document.getElementById('cp-new')?.value;
  const new_password2 = document.getElementById('cp-new2')?.value;
  if (!old_password || !new_password) { await showAlert('Alle Felder erforderlich'); return; }
  if (new_password !== new_password2) { await showAlert('Passwörter stimmen nicht überein'); return; }
  try {
    const r = await API.post('/v1/auth/password', {old_password, new_password});
    if (r.error) { await showAlert(r.error); return; }
    await showAlert('Passwort erfolgreich geändert', 'Passwort geändert');
    document.querySelector('.modal-overlay')?.remove();
  } catch(e) { await showAlert('Fehlgeschlagen: ' + e.message); }
}

// ═══ User Settings Modal ═══
// Distinct from openGeneralSettings (admin-style global config). Tabs:
// Profile | Memory | Schedules | Security. Personal state only — anything
// org-wide stays in the General Settings modal.
async function openUserSettings(initialTab) {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  modal.innerHTML = `
    <div class="modal-content" style="max-width:780px;width:90vw;height:80vh;display:flex;flex-direction:column">
      <div class="modal-header">
        <h2 style="display:flex;align-items:center;gap:8px">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/>
          </svg>
          Kontoeinstellungen
        </h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div style="display:flex;flex:1;overflow:hidden">
        <div class="modal-tabs modal-tabs-vertical" id="user-settings-tabs" style="width:160px;flex-shrink:0">
          <div class="modal-tab" data-tab="profile" onclick="switchUserSettingsTab('profile', this)">Profil</div>
          <div class="modal-tab" data-tab="memory" onclick="switchUserSettingsTab('memory', this)">Memory</div>
          <div class="modal-tab" data-tab="schedules" onclick="switchUserSettingsTab('schedules', this)">Meine Zeitpläne</div>
          <div class="modal-tab" data-tab="security" onclick="switchUserSettingsTab('security', this)">Sicherheit</div>
        </div>
        <div class="modal-body" id="user-settings-body" style="flex:1;overflow:auto;padding:20px">
          <div style="color:var(--text-400)">Wird geladen…</div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  // Pull a fresh /v1/auth/me so the modal reflects server state, not stale state.authUser
  try {
    const r = await API.get('/v1/auth/me');
    if (r && r.user) { state.authUser = r.user; if (typeof buddyInit === 'function') buddyInit(); }
  } catch(e) {}
  const startTab = initialTab || 'profile';
  const startEl = modal.querySelector(`.modal-tab[data-tab="${startTab}"]`);
  switchUserSettingsTab(startTab, startEl);
}

function switchUserSettingsTab(tab, el) {
  document.querySelectorAll('#user-settings-tabs .modal-tab').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  const body = document.getElementById('user-settings-body');
  if (!body) return;
  if (tab === 'profile') return renderUserSettingsProfile(body);
  if (tab === 'memory') return renderUserSettingsMemory(body);
  if (tab === 'schedules') return renderUserSettingsSchedules(body);
  if (tab === 'security') return renderUserSettingsSecurity(body);
}

function _us_input(label, id, val, type = 'text', help = '') {
  return `
    <div style="margin-bottom:14px">
      <label style="display:block;font-size:12px;font-weight:500;color:var(--text-200);margin-bottom:4px">${esc(label)}</label>
      <input id="${id}" type="${type}" value="${esc(val ?? '')}"
        style="display:block;width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px;background:var(--bg-100);color:var(--text-100)">
      ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:4px">${esc(help)}</div>` : ''}
    </div>`;
}

function _us_textarea(label, id, val, help = '', placeholder = '', opts = {}) {
  const rows = opts.rows || 3;
  const maxlength = opts.maxlength || 500;
  const minHeight = opts.minHeight ? `min-height:${opts.minHeight};` : '';
  // Optional inline AI polish button — same /v1/refine endpoint as the chat
  // composer, but with purpose=profile_field so the system prompt switches
  // to "polish, don't rewrite as a question". Disabled visually (no button)
  // when opts.refinable is falsy. Caveman is NOT a refine-time setting:
  // the chat composer's caveman toggle is the single source of truth and
  // applies to the outgoing message, not to the polish step.
  const refineBtn = opts.refinable ? `
    <button type="button" id="${id}-refine"
      onclick="refineProfileField('${id}', ${JSON.stringify(opts.fieldLabel || label).replace(/"/g, '&quot;')})"
      title="Mit KI verfeinern"
      style="background:none;border:1px solid var(--border-light);border-radius:4px;padding:2px 8px;font-size:11px;color:var(--text-300);cursor:pointer;display:inline-flex;align-items:center;gap:4px">
      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
        <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
        <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
      </svg>
      <span id="${id}-refine-label">Mit KI verfeinern</span>
    </button>` : '';
  return `
    <div style="margin-bottom:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;gap:8px">
        <label for="${id}" style="font-size:12px;font-weight:500;color:var(--text-200)">${esc(label)}</label>
        ${refineBtn}
      </div>
      <textarea id="${id}" rows="${rows}" maxlength="${maxlength}" placeholder="${esc(placeholder)}"
        style="display:block;width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px;background:var(--bg-100);color:var(--text-100);font-family:inherit;resize:vertical;${minHeight}">${esc(val ?? '')}</textarea>
      ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:4px">${esc(help)}</div>` : ''}
    </div>`;
}

// Inline AI refinement control for the long-form user-profile editor in
// the Memory tab. Single Refine-with-AI button — no caveman picker:
// the on-disk profile is always clean prose; the chat composer's caveman
// toggle compresses it dynamically at injection time.
function _userProfileRefineControls(textareaId) {
  return `
    <button type="button" id="${textareaId}-refine"
      onclick="refineProfileField('${textareaId}', 'Ihr Profil (langform Markdown mit ## Überschrift-Abschnitten; behalten Sie die dritte Person und die Abschnittsüberschriften bei)')"
      title="Profil mit KI verfeinern"
      class="btn-secondary"
      style="font-size:11px;padding:4px 10px;display:inline-flex;align-items:center;gap:4px">
      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
        <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
        <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
      </svg>
      <span id="${textareaId}-refine-label">Mit KI verfeinern</span>
    </button>`;
}

async function refineProfileField(textareaId, fieldLabel) {
  const ta = document.getElementById(textareaId);
  if (!ta) return;
  const text = (ta.value || '').replace(/^\s+|\s+$/g, '');
  if (!text) { showToast('Bitte zuerst etwas eingeben', true); return; }
  const btn = document.getElementById(textareaId + '-refine');
  const lbl = document.getElementById(textareaId + '-refine-label');
  const origLabel = lbl?.textContent || 'Mit KI verfeinern';
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Wird verfeinert…';
  ta.disabled = true;
  // Stash original so the user can undo a bad refinement
  const original = ta.value;
  try {
    // No caveman on profile-field refinement — the on-disk profile stays
    // clean prose, and the chat composer's caveman toggle compresses it
    // dynamically at injection time. Refining with caveman would also
    // double-apply at chat time.
    const result = await API.post('/v1/refine', {
      text,
      purpose: 'profile_field',
      field_label: fieldLabel || '',
    });
    if (result && result.refined && result.refined !== text) {
      ta.value = result.refined;
      // Offer one-click undo via the button label until the user clicks elsewhere
      if (lbl) lbl.textContent = 'Rückgängig';
      if (btn) {
        btn.disabled = false;
        const undoHandler = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          ta.value = original;
          if (lbl) lbl.textContent = origLabel;
          btn.removeEventListener('click', undoHandler);
          // Restore the normal refine handler on the next click
          btn.onclick = () => refineProfileField(textareaId, fieldLabel);
        };
        btn.onclick = undoHandler;
      }
      showToast('Verfeinert — zum Zurücksetzen auf Rückgängig klicken');
    } else {
      showToast('Bereits sauber — keine Änderung');
      if (lbl) lbl.textContent = origLabel;
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('Verfeinern fehlgeschlagen: ' + (e.message || e), true);
    if (lbl) lbl.textContent = origLabel;
    if (btn) btn.disabled = false;
  } finally {
    ta.disabled = false;
  }
}

// Buddy dropdown options. Labels come from buddy.js's BUDDY_SPECIES; the ""
// (auto) and "off" sentinels match the server-side validation in auth.py.
function _buddySpeciesOpts() {
  const opts = [
    {value: '', label: 'Überrasch mich (automatisch)'},
    {value: 'off', label: 'Aus — kein Buddy'},
  ];
  const species = (typeof BUDDY_SPECIES !== 'undefined') ? BUDDY_SPECIES : {};
  for (const id of Object.keys(species)) {
    opts.push({value: id, label: species[id].label || id});
  }
  return opts;
}

function renderUserSettingsProfile(body) {
  const u = state.authUser || {};
  const prefs = u.preferences || {};
  body.innerHTML = `
    <div style="max-width:520px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Profil</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        Wie der Agent Sie erkennt und anspricht. Änderungen werden beim Klick gespeichert.
      </div>
      <div style="font-size:11px;color:var(--text-400);margin-bottom:14px">
        <span style="font-weight:500;color:var(--text-300)">Benutzername:</span> ${esc(u.username || '')}
        &nbsp;·&nbsp;
        <span style="font-weight:500;color:var(--text-300)">Rolle:</span> ${esc(roleLabelDe(u.role || 'user'))}
      </div>
      ${_us_input('Vollständiger Name (Anzeige)', 'us-display-name', u.display_name, 'text', 'Wird in der Seitenleiste und in Admin-Listen angezeigt.')}
      ${_us_input('Anredename', 'us-greeting-name', prefs.greeting_name, 'text', 'Wie der Agent Sie im Gespräch nennen soll. Fällt auf den vollständigen Namen zurück.')}
      ${_us_input('E-Mail', 'us-email', u.email, 'email', 'Für Benachrichtigungen. Wird nicht weitergegeben.')}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">Über Sie</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Wird dem Agent in der ersten Runde jedes Chats mitgeteilt, damit er weiß, wer Sie sind und wie Sie angesprochen werden möchten. Halten Sie jedes Feld auf ein bis zwei Sätze.
      </div>
      ${_us_textarea('Was beschreibt Ihre Tätigkeit in einem Satz?', 'us-job-description', prefs.job_description,
        'z. B. „Backend-Entwickler im Bereich Zahlungsinfrastruktur“ oder „Doktorand der Computational Biology“. Max. 500 Zeichen.',
        'Ich bin …',
        {refinable: true, fieldLabel: 'Tätigkeitsbeschreibung'})}
      ${_us_textarea('Wie sind Ihre Kommunikationspräferenzen?', 'us-comm-prefs', prefs.communication_preferences,
        'Wie soul.md, aber für Sie — Ton, Stil, Formatierung, was zu vermeiden ist, wiederkehrender Kontext, den der Agent immer kennen sollte. Bis zu ~4000 Zeichen.',
        'Ich bevorzuge direkte, technische Antworten. Lassen Sie das Vorgeplänkel weg. Verwenden Sie Codeblöcke für Code, Markdown sparsam. Kein „kommt darauf an“ — entscheiden Sie sich und erklären Sie.\n\nWenn ich nach Architektur frage, diskutieren Sie standardmäßig Abwägungen. Wenn ich nach Code frage, liefern Sie standardmäßig kleine, fokussierte Diffs.',
        {rows: 12, maxlength: 4000, minHeight: '220px', refinable: true, fieldLabel: 'Kommunikationspräferenzen'})}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">Begleiter</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Ein kleiner Buddy, der unten rechts schwebt. Er verblasst fast vollständig, wenn Sie inaktiv sind, und wird bei einem Tastendruck oder während eine Antwort generiert wird heller. Rein kosmetisch.
      </div>
      ${_us_select('Buddy', 'us-buddy-species', prefs.buddy_species || '', _buddySpeciesOpts(),
        'Wählen Sie eine Art oder schalten Sie ihn aus. „Überrasch mich“ wählt anhand Ihres Kontos eine aus.')}
      <div style="display:flex;gap:8px;margin-top:18px">
        <button class="btn-primary" onclick="saveUserSettingsProfile()">Profil speichern</button>
        <span id="us-profile-msg" style="align-self:center;font-size:12px;color:var(--text-400)"></span>
      </div>
    </div>`;
}

async function saveUserSettingsProfile() {
  const display_name = document.getElementById('us-display-name')?.value?.trim() || '';
  const email = document.getElementById('us-email')?.value?.trim() || '';
  const greeting_name = document.getElementById('us-greeting-name')?.value?.trim() || '';
  const job_description = document.getElementById('us-job-description')?.value?.trim() || '';
  // Communication prefs: only `.trim()` whitespace at the ends. Internal
  // newlines are kept as-is so users can structure their soul.md-style block.
  const communication_preferences = (document.getElementById('us-comm-prefs')?.value || '')
    .replace(/^\s+|\s+$/g, '');
  const buddy_species = document.getElementById('us-buddy-species')?.value || '';
  const msg = document.getElementById('us-profile-msg');
  if (msg) { msg.textContent = 'Wird gespeichert…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r1 = await API.post('/v1/auth/profile', {display_name, email});
    if (r1.error) throw new Error(r1.error);
    const r2 = await API.post('/v1/auth/preferences', {preferences: {
      greeting_name, job_description, communication_preferences, buddy_species,
    }});
    if (r2.error) throw new Error(r2.error);
    if (r2.user) state.authUser = r2.user;
    renderUserMenu();
    // Buddy species/toggle may have changed — re-resolve from the fresh prefs.
    if (typeof buddyInit === 'function') buddyInit();
    if (msg) { msg.textContent = 'Gespeichert.'; msg.style.color = 'var(--success, #16a34a)'; }
  } catch (e) {
    if (msg) { msg.textContent = 'Fehlgeschlagen: ' + (e.message || e); msg.style.color = 'var(--error, #dc2626)'; }
  }
}

function _us_select(label, id, val, options, help = '') {
  const opts = options.map(o =>
    `<option value="${esc(o.value)}" ${String(o.value) === String(val ?? '') ? 'selected' : ''}>${esc(o.label)}</option>`
  ).join('');
  return `
    <div style="margin-bottom:14px">
      <label style="display:block;font-size:12px;font-weight:500;color:var(--text-200);margin-bottom:4px">${esc(label)}</label>
      <select id="${id}" style="display:block;width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px;background:var(--bg-100);color:var(--text-100)">${opts}</select>
      ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:4px">${esc(help)}</div>` : ''}
    </div>`;
}

function _us_checkbox(label, id, checked, help = '') {
  return `
    <label style="display:flex;align-items:flex-start;gap:8px;margin-bottom:14px;cursor:pointer">
      <input id="${id}" type="checkbox" ${checked ? 'checked' : ''} style="margin-top:2px">
      <div>
        <div style="font-size:13px;color:var(--text-100)">${esc(label)}</div>
        ${help ? `<div style="font-size:11px;color:var(--text-400);margin-top:2px">${esc(help)}</div>` : ''}
      </div>
    </label>`;
}

function renderUserSettingsMemory(body) {
  const prefs = (state.authUser || {}).preferences || {};
  // null sentinel for "use server default" — the server treats it as
  // memory_chats_default unset, falling through to chat_sync.classifier.default_mode.
  const memOpts = [
    {value: '', label: 'Server-Standard verwenden'},
    {value: '0', label: 'Aus — Chats nie im Memory speichern'},
    {value: '1', label: 'Ein — jeden Chat im Memory speichern'},
    {value: '2', label: 'Auto — Klassifizierer entscheidet pro Runde'},
  ];
  const schedOpts = [
    {value: '', label: 'Server-Standard verwenden (Datei-Artefakte)'},
    {value: '0', label: 'Aus — Artefakte geplanter Läufe überspringen'},
    {value: '1', label: 'Ein — Artefakte geplanter Läufe ablegen'},
  ];
  const hourOpts = Array.from({length: 24}, (_, i) => ({
    value: String(i), label: `${String(i).padStart(2, '0')}:00 Ortszeit`,
  }));
  // Per-user new-chat composer defaults. '' = inherit the global default
  // (Admin → General Settings → Server → Eingabefeld-Standards).
  const thinkingOpts = [
    {value: '', label: 'Server-Standard verwenden'},
    {value: 'none', label: 'Aus'},
    {value: 'low', label: 'Niedrig'},
    {value: 'medium', label: 'Mittel'},
    {value: 'high', label: 'Hoch'},
  ];
  const cavemanOpts = [
    {value: '', label: 'Server-Standard verwenden'},
    {value: '0', label: 'Aus'},
    {value: '1', label: 'Lite'},
    {value: '2', label: 'Voll'},
    {value: '3', label: 'Ultra'},
  ];
  body.innerHTML = `
    <div style="max-width:520px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Memory-Standardwerte</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        Steuert, wie Ihre Aktivität in MemPalace abgelegt wird. Der Pro-Chat-Schalter im Eingabefeld überschreibt diese Standardwerte immer.
      </div>
      ${_us_select('Standard für neue Chats', 'us-mem-chats',
        prefs.memory_chats_default == null ? '' : String(prefs.memory_chats_default),
        memOpts,
        'Jeder neue Chat startet mit diesem Memory-Modus. Sie können ihn weiterhin pro Chat umschalten.')}
      ${_us_select('Artefakte geplanter Läufe', 'us-mem-sched',
        prefs.memory_sched_default == null ? '' : String(prefs.memory_sched_default),
        schedOpts,
        'Ob der Miner die von Ihren geplanten Tasks erzeugten Artefakte ablegt. „Aus“ belässt sie nur auf der Festplatte.')}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">Eingabefeld-Standards für neue Chats</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Werte, mit denen ein <b>neuer</b> Chat startet. „Server-Standard verwenden“ übernimmt die globale Vorgabe des Administrators. Beim Wiederöffnen eines bestehenden Chats wird immer dessen eigener gespeicherter Stand wiederhergestellt — diese Standardwerte gelten nur für frische Chats und sind pro Chat weiter umschaltbar.
      </div>
      ${_us_select('Denk-Stufe', 'us-thinking-default',
        prefs.thinking_level_default == null ? '' : String(prefs.thinking_level_default),
        thinkingOpts,
        'Auf „✨ Smart/Auto“ wird die Stufe best-effort auf das gewählte Modell angewendet.')}
      ${_us_select('Caveman-Modus', 'us-caveman-default',
        prefs.caveman_mode_default == null ? '' : String(prefs.caveman_mode_default),
        cavemanOpts,
        'Knappheit der Antworten in neuen Chats.')}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">Memory aus dem Chat-Verlauf</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Brain liest Ihre Chats und pflegt ein einziges Profil, das Ihre Arbeit, Interessen und das beschreibt, was Sie aktuell beschäftigt. Der Agent lädt dieses in der ersten Runde jedes Chats.
      </div>
      ${_us_checkbox('Benutzerprofil aus Chat-Verlauf pflegen', 'us-daily-enabled', !!prefs.daily_summary_enabled,
        "Läuft einmal täglich um die gewählte Stunde. Das Profil liegt unter agents/main/user_profiles/&lt;Sie&gt;.md und spiegelt abschnittsweise Drawers in MemPalace. Die Datei auf der Festplatte ist immer sauberer Fließtext; der Caveman-Modus des Chats verdichtet sie bei Bedarf, wenn sie als Kontext eingefügt wird.")}
      ${_us_select('Aktualisieren um', 'us-daily-hour', String(prefs.daily_summary_hour_local ?? 6), hourOpts)}
      <div style="display:flex;gap:8px;margin-top:14px;align-items:center">
        <button class="btn-primary" onclick="saveUserSettingsMemory()">Memory-Einstellungen speichern</button>
        <span id="us-mem-msg" style="align-self:center;font-size:12px;color:var(--text-400)"></span>
      </div>

      <div id="us-profile-doc-section" style="margin-top:20px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;flex-wrap:wrap">
          <label for="us-profile-doc" style="font-size:13px;font-weight:500;color:var(--text-200)">Ihr Profil</label>
          <div style="display:flex;gap:6px;align-items:center">
            ${_userProfileRefineControls('us-profile-doc')}
            <button id="us-profile-update-btn" class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="userProfileUpdateNow()">Jetzt aktualisieren</button>
            <button id="us-profile-reset-btn" class="btn-secondary" style="font-size:11px;padding:4px 10px;color:var(--error,#dc2626)" onclick="userProfileReset()">Zurücksetzen</button>
          </div>
        </div>
        <textarea id="us-profile-doc" rows="20"
          placeholder="(Profil noch nicht generiert. Aktivieren Sie den Schalter oben und klicken Sie auf „Jetzt aktualisieren“, oder warten Sie auf den täglichen Lauf.)"
          style="display:block;width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--border-light);border-radius:6px;font-size:12px;background:var(--bg-100);color:var(--text-100);font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;resize:vertical;min-height:380px;line-height:1.5"></textarea>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px">
          <div id="us-profile-meta" style="font-size:11px;color:var(--text-400)"></div>
          <div style="display:flex;gap:6px;align-items:center">
            <span id="us-profile-doc-msg" style="font-size:11px;color:var(--text-400)"></span>
            <button class="btn-primary" style="padding:4px 12px;font-size:12px" onclick="userProfileSave()">Profil speichern</button>
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:6px">
          Bearbeitbares Markdown. Abschnitte müssen <code>## Überschrift</code> verwenden. Der nächste tägliche Lauf bearbeitet Ihre Änderungen direkt, statt sie zu überschreiben.
        </div>
      </div>
    </div>`;
  // Lazy-load the profile content after the DOM is in place.
  loadUserProfileDoc();
}

async function loadUserProfileDoc() {
  const ta = document.getElementById('us-profile-doc');
  const meta = document.getElementById('us-profile-meta');
  if (!ta) return;
  try {
    const r = await API.get('/v1/auth/profile-doc');
    ta.value = r.content || '';
    if (meta) {
      const cur = r.cursor || {};
      const ts = cur.last_run_ts ? new Date(cur.last_run_ts * 1000).toLocaleString() : '';
      const status = cur.last_status || '';
      const bytes = r.bytes || 0;
      const parts = [];
      if (bytes) parts.push(`${bytes} Bytes`);
      if (ts) parts.push(`letzter Lauf ${ts}${status ? ` · ${status}` : ''}`);
      else if (status) parts.push(status);
      meta.textContent = parts.join(' · ') || (r.exists ? '' : 'Noch nicht generiert');
    }
  } catch (e) {
    if (meta) meta.textContent = 'Laden fehlgeschlagen: ' + (e.message || e);
  }
}

async function userProfileSave() {
  const ta = document.getElementById('us-profile-doc');
  const msg = document.getElementById('us-profile-doc-msg');
  if (!ta) return;
  if (msg) { msg.textContent = 'Wird gespeichert…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc', {content: ta.value});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = `Gespeichert (${r.bytes} Bytes)`; msg.style.color = 'var(--success,#16a34a)'; }
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Fehlgeschlagen: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}

async function userProfileUpdateNow() {
  const btn = document.getElementById('us-profile-update-btn');
  const msg = document.getElementById('us-profile-doc-msg');
  const origLabel = btn?.textContent || 'Jetzt aktualisieren';
  if (btn) { btn.disabled = true; btn.textContent = 'Wird aktualisiert…'; }
  if (msg) { msg.textContent = 'Wird aus Chat-Verlauf generiert (5–60 s)…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc/update-now', {});
    if (r.error) throw new Error(r.error);
    const result = r.result || {};
    if (result.status === 'no_activity') {
      if (msg) { msg.textContent = 'Noch keine Chat-Aktivität zum Zusammenfassen.'; msg.style.color = 'var(--text-400)'; }
    } else if (result.status === 'error') {
      throw new Error(result.error || 'Generierung fehlgeschlagen');
    } else {
      if (msg) { msg.textContent = `Aktualisiert (${result.bytes} Bytes, ${result.samples} Chats)`; msg.style.color = 'var(--success,#16a34a)'; }
    }
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Fehlgeschlagen: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origLabel; }
  }
}

async function userProfileReset() {
  if (!await showConfirmDanger('Profil zurücksetzen?\n\nLöscht die Datei + MemPalace-Drawers. Der nächste tägliche Lauf (oder „Jetzt aktualisieren“) baut es aus Ihren Chats der letzten 90 Tage neu auf.\n\nDer Profilverlauf wird zum Zurückrollen auf der Festplatte aufbewahrt.', 'Profil zurücksetzen', 'Zurücksetzen')) {
    return;
  }
  const msg = document.getElementById('us-profile-doc-msg');
  if (msg) { msg.textContent = 'Wird zurückgesetzt…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc/reset', {});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = 'Zurückgesetzt.'; msg.style.color = 'var(--success,#16a34a)'; }
    const ta = document.getElementById('us-profile-doc');
    if (ta) ta.value = '';
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Fehlgeschlagen: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}

async function saveUserSettingsMemory() {
  const memChats = document.getElementById('us-mem-chats')?.value;
  const memSched = document.getElementById('us-mem-sched')?.value;
  const thinkDef = document.getElementById('us-thinking-default')?.value;
  const cavemanDef = document.getElementById('us-caveman-default')?.value;
  const dailyEnabled = !!document.getElementById('us-daily-enabled')?.checked;
  const dailyHour = parseInt(document.getElementById('us-daily-hour')?.value || '6', 10);
  const msg = document.getElementById('us-mem-msg');
  if (msg) { msg.textContent = 'Wird gespeichert…'; msg.style.color = 'var(--text-400)'; }
  const prefs = {
    memory_chats_default: memChats === '' ? null : parseInt(memChats, 10),
    memory_sched_default: memSched === '' ? null : parseInt(memSched, 10),
    thinking_level_default: thinkDef === '' ? null : thinkDef,
    caveman_mode_default: cavemanDef === '' ? null : parseInt(cavemanDef, 10),
    daily_summary_enabled: dailyEnabled,
    daily_summary_hour_local: dailyHour,
  };
  try {
    const r = await API.post('/v1/auth/preferences', {preferences: prefs});
    if (r.error) throw new Error(r.error);
    if (r.user) state.authUser = r.user;
    if (typeof buddyInit === 'function') buddyInit();
    if (msg) { msg.textContent = 'Gespeichert.'; msg.style.color = 'var(--success, #16a34a)'; }
  } catch (e) {
    if (msg) { msg.textContent = 'Fehlgeschlagen: ' + (e.message || e); msg.style.color = 'var(--error, #dc2626)'; }
  }
}

async function renderUserSettingsSchedules(body) {
  body.innerHTML = `<div style="color:var(--text-400)">Ihre geplanten Tasks werden geladen…</div>`;
  try {
    const r = await API.get('/v1/schedule');
    const schedules = (r && r.schedules) || [];
    if (!schedules.length) {
      body.innerHTML = `
        <div style="max-width:520px">
          <h3 style="margin:0 0 4px 0;font-size:15px">Meine geplanten Tasks</h3>
          <div style="font-size:12px;color:var(--text-400);margin-bottom:14px">
            Sie besitzen noch keine geplanten Tasks. Erstellen Sie einen in der Ansicht „Geplant“.
          </div>
          <button class="btn-secondary" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('scheduled')">Ansicht „Geplant“ öffnen</button>
        </div>`;
      return;
    }
    const rows = schedules.map(s => {
      const enabled = !!s.enabled;
      const nextRun = s.next_run ? new Date(s.next_run).toLocaleString() : '—';
      const stateLbl = enabled ? (s.is_running ? 'läuft' : 'aktiviert') : 'pausiert';
      return `
        <tr>
          <td style="padding:8px 6px;font-weight:500">${esc(s.name || '')}</td>
          <td style="padding:8px 6px;font-size:12px;color:var(--text-300)">${esc(s.schedule || '')}</td>
          <td style="padding:8px 6px;font-size:12px;color:var(--text-300)">${esc(nextRun)}</td>
          <td style="padding:8px 6px;font-size:12px;color:${enabled ? 'var(--success,#16a34a)' : 'var(--text-400)'}">
            ${stateLbl}
          </td>
        </tr>`;
    }).join('');
    body.innerHTML = `
      <div>
        <h3 style="margin:0 0 4px 0;font-size:15px">Meine geplanten Tasks (${schedules.length})</h3>
        <div style="font-size:12px;color:var(--text-400);margin-bottom:14px">
          Tasks, die Ihnen gehören. Bearbeiten, pausieren oder löschen Sie sie in der Ansicht „Geplant“.
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="text-align:left;border-bottom:1px solid var(--border-light)">
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Name</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Zeitplan</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Nächster Lauf</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Status</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="margin-top:14px">
          <button class="btn-secondary" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('scheduled')">Ansicht „Geplant“ öffnen</button>
        </div>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div style="color:var(--error,#dc2626)">Zeitpläne konnten nicht geladen werden: ${esc(e.message || e)}</div>`;
  }
}

function renderUserSettingsSecurity(body) {
  body.innerHTML = `
    <div style="max-width:480px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Passwort ändern</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        Wählen Sie etwas mit mindestens 6 Zeichen.
      </div>
      ${_us_input('Aktuelles Passwort', 'us-pw-old', '', 'password')}
      ${_us_input('Neues Passwort', 'us-pw-new', '', 'password')}
      ${_us_input('Neues Passwort bestätigen', 'us-pw-new2', '', 'password')}
      <div style="display:flex;gap:8px;margin-top:6px">
        <button class="btn-primary" onclick="saveUserSettingsPassword()">Passwort ändern</button>
        <span id="us-pw-msg" style="align-self:center;font-size:12px;color:var(--text-400)"></span>
      </div>
    </div>`;
}

async function saveUserSettingsPassword() {
  const old_password = document.getElementById('us-pw-old')?.value || '';
  const new_password = document.getElementById('us-pw-new')?.value || '';
  const new2 = document.getElementById('us-pw-new2')?.value || '';
  const msg = document.getElementById('us-pw-msg');
  if (!old_password || !new_password) {
    if (msg) { msg.textContent = 'Alle Felder erforderlich'; msg.style.color = 'var(--error,#dc2626)'; }
    return;
  }
  if (new_password !== new2) {
    if (msg) { msg.textContent = 'Passwörter stimmen nicht überein'; msg.style.color = 'var(--error,#dc2626)'; }
    return;
  }
  if (msg) { msg.textContent = 'Wird gespeichert…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/password', {old_password, new_password});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = 'Passwort geändert.'; msg.style.color = 'var(--success,#16a34a)'; }
    document.getElementById('us-pw-old').value = '';
    document.getElementById('us-pw-new').value = '';
    document.getElementById('us-pw-new2').value = '';
  } catch (e) {
    if (msg) { msg.textContent = 'Fehlgeschlagen: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}
