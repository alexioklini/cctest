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
    const roleBadge = (r) => `<span class="role-badge ${esc(r)}">${esc(r)}</span>`;
    const statusDot = (disabled) => `<span title="${disabled?'disabled':'active'}" style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle;background:${disabled?'#dc2626':'#16a34a'}"></span>`;
    modal.innerHTML = `
      <div class="modal-content" style="max-width:920px">
        <div class="modal-header"><h2>User Management</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <div style="font-size:12px;color:var(--text-secondary);flex:1">Self-registration is disabled. Users must be provisioned here.</div>
            <button class="um-btn" onclick="document.querySelector('.modal-overlay').remove();openUserTeams()">Teams &rsaquo;</button>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="text-align:left;border-bottom:2px solid var(--border-light);color:var(--text-secondary);font-size:11px;text-transform:uppercase;letter-spacing:0.04em">
              <th style="padding:8px">Status</th>
              <th style="padding:8px">Username</th>
              <th style="padding:8px">Display Name</th>
              <th style="padding:8px">Role</th>
              <th style="padding:8px">Last Login</th>
              <th style="padding:8px;text-align:right">Actions</th>
            </tr></thead>
            <tbody>
              ${users.map(u => {
                const isSelf = u.id === meId;
                const isDefaultAdmin = u.username === 'admin';
                return `<tr style="border-bottom:1px solid var(--border-light)">
                <td style="padding:8px">${statusDot(u.disabled)}${u.disabled?'<span style="font-size:11px;color:#dc2626">disabled</span>':'<span style="font-size:11px;color:#16a34a">active</span>'}</td>
                <td style="padding:8px"><code>${esc(u.username)}</code>${isSelf?' <span style="font-size:10px;color:var(--text-secondary)">(you)</span>':''}</td>
                <td style="padding:8px">${esc(u.display_name || '')}</td>
                <td style="padding:8px">
                  <select onchange="changeUserRole('${esc(u.id)}', this.value)" ${isSelf?'disabled title="Cannot change your own role"':''} style="font-size:12px;padding:2px 6px;border-radius:4px;border:1px solid var(--border-light)">
                    ${['admin','poweruser','user'].map(r => `<option value="${r}" ${u.role===r?'selected':''}>${r}</option>`).join('')}
                  </select>
                </td>
                <td style="padding:8px;color:var(--text-secondary);font-size:12px">${u.last_login ? new Date(u.last_login*1000).toLocaleString() : 'Never'}</td>
                <td style="padding:8px;text-align:right;white-space:nowrap">
                  <button class="um-btn" onclick="openUserPermissions('${esc(u.id)}','${esc(u.username)}')" title="Permissions">&#9881;</button>
                  <button class="um-btn" onclick="adminResetPassword('${esc(u.id)}','${esc(u.username)}')" title="Reset password">&#128273;</button>
                  ${isSelf ? '' : (u.disabled
                    ? `<button class="um-btn" onclick="setUserDisabled('${esc(u.id)}', false)" title="Enable user" style="color:#16a34a">&#9654;</button>`
                    : `<button class="um-btn" onclick="setUserDisabled('${esc(u.id)}', true)" title="Disable user" style="color:#b45309">&#9209;</button>`)}
                  ${(!isSelf && !isDefaultAdmin) ? `<button class="um-btn" onclick="deleteUser('${esc(u.id)}','${esc(u.username)}')" title="Delete user" style="color:#dc2626">&#128465;</button>` : ''}
                </td>
              </tr>`;
              }).join('')}
            </tbody>
          </table>
          <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border-light)">
            <h3 style="font-size:14px;margin:0 0 12px">Add User</h3>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <input id="new-user-name" placeholder="Username" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-display" placeholder="Display name (optional)" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-pass" type="password" placeholder="Password (min 6)" style="flex:1;min-width:120px;padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <select id="new-user-role" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
                <option value="user">user</option><option value="poweruser">poweruser (team lead)</option><option value="admin">admin</option>
              </select>
              <button onclick="addUser()" class="auth-btn" style="width:auto;padding:8px 20px">Add</button>
            </div>
            <div style="font-size:11px;color:var(--text-secondary);margin-top:8px">
              <b>user</b> — chat-only within granted agents/models.
              <b>poweruser</b> — team lead, can create/manage their own teams.
              <b>admin</b> — full config access.
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
  } catch(e) { console.error('Failed to load users:', e); showAlert('Failed to load users: ' + e.message); }
}

async function changeUserRole(userId, role) {
  try {
    const r = await API.post('/v1/auth/users', {action:'update', user_id: userId, updates:{role}});
    if (r && r.error) { await showAlert(r.error); return; }
    showToast?.('Role updated');
  } catch(e) { await showAlert('Failed to update role: ' + e.message); }
}

async function deleteUser(userId, username) {
  if (!await showConfirmDanger(`Delete user "${username}"? This cannot be undone.`, 'Delete user', 'Delete')) return;
  try {
    const r = await API.post('/v1/auth/users', {action:'delete', user_id: userId});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { await showAlert('Failed to delete user: ' + e.message); }
}

async function adminResetPassword(userId, username) {
  const pw = await showPrompt(`Enter new password (min 6 characters):`, '', `Reset password for "${username}"`);
  if (pw === null) return;
  if (pw.length < 6) { await showAlert('Password must be at least 6 characters', 'Invalid password'); return; }
  try {
    const r = await API.post('/v1/auth/users', {action:'reset_password', user_id: userId, new_password: pw});
    if (r && r.error) { await showAlert(r.error); return; }
    await showAlert(`Password reset for "${username}". Share the new password with them securely.`, 'Password reset');
  } catch(e) { await showAlert('Failed to reset password: ' + e.message); }
}

async function setUserDisabled(userId, disabled) {
  try {
    const r = await API.post('/v1/auth/users', {action: disabled ? 'disable' : 'enable', user_id: userId});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { await showAlert('Failed: ' + e.message); }
}

async function addUser() {
  const username = document.getElementById('new-user-name')?.value.trim();
  const password = document.getElementById('new-user-pass')?.value;
  const display_name = document.getElementById('new-user-display')?.value.trim();
  const role = document.getElementById('new-user-role')?.value;
  if (!username || !password) { await showAlert('Username and password required'); return; }
  if (password.length < 6) { await showAlert('Password must be at least 6 characters'); return; }
  try {
    const r = await API.post('/v1/auth/users', {action:'create', username, password, role, display_name});
    if (r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserManagement();
  } catch(e) { await showAlert('Failed to add user: ' + e.message); }
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
      ${viaTeam && viaTeam.length ? `<span style="font-size:10px;padding:2px 6px;background:var(--bg-base);border-radius:10px;color:var(--text-secondary)">via team: ${esc(viaTeam.join(', '))}</span>` : ''}
    </div>`;
    modal.innerHTML = `
      <div class="modal-content" style="max-width:780px">
        <div class="modal-header">
          <h2>Permissions: ${esc(username)}${isAdmin?' (admin — full access)':''}</h2>
          <button class="modal-close" onclick="document.querySelector('.modal-overlay').remove();openUserManagement()">&times;</button>
        </div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          <div style="font-size:12px;color:var(--text-secondary);margin-bottom:16px">
            ${isAdmin
              ? 'Admins always have access to all agents, models, and features. Settings below are stored but not enforced for admins.'
              : 'Grants control which agents/models this user can chat with. Team memberships add to direct grants. Capabilities toggle feature access.'}
          </div>
          <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Capabilities</h3>
          <div id="user-caps-row" style="display:flex;flex-wrap:wrap;gap:12px;padding:10px 12px;border:1px solid var(--border-light);border-radius:8px;margin-bottom:16px">
            ${(() => {
              const caps = (target.capabilities || {});
              const keys = ['allow_projects','allow_artifacts','allow_workflows','allow_skills_install'];
              const labels = {allow_projects:'Projects', allow_artifacts:'Artifacts', allow_workflows:'Workflows', allow_skills_install:'Install skills'};
              return keys.map(k => `<label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:${isAdmin?'not-allowed':'pointer'};${isAdmin?'opacity:0.5':''}">
                <input type="checkbox" ${caps[k]?'checked':''} ${isAdmin?'disabled':''} data-cap="${k}" onchange="_toggleCapability('${esc(userId)}', this)">
                <span>${labels[k]}</span>
              </label>`).join('');
            })()}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
            <div>
              <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Agents (${allAgents.length})</h3>
              ${allAgents.map(a => row(a, directAgents.has(a), 'agent', teamAgents[a])).join('') || '<div style="color:var(--text-secondary);font-size:12px">No agents available.</div>'}
            </div>
            <div>
              <h3 style="font-size:13px;margin:0 0 8px;font-weight:600">Models (${allModels.length})</h3>
              ${allModels.map(m => row(m, directModels.has(m), 'model', teamModels[m])).join('') || '<div style="color:var(--text-secondary);font-size:12px">No models enabled.</div>'}
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  } catch(e) { await showAlert('Failed to load permissions: ' + e.message); }
}

async function _togglePermission(userId, kind, id, checked, el) {
  try {
    const payload = {action: checked ? 'grant' : 'revoke', kind, user_id: userId};
    if (kind === 'agent') payload.agent_id = id; else payload.model_id = id;
    const r = await API.post('/v1/auth/permissions', payload);
    if (r && r.error) { el.checked = !checked; await showAlert(r.error); return; }
  } catch(e) { el.checked = !checked; await showAlert('Failed: ' + e.message); }
}

async function _toggleCapability(userId, el) {
  // Read all capability checkboxes in the row, send a full update
  const checkboxes = document.querySelectorAll('#user-caps-row input[type=checkbox][data-cap]');
  const caps = {};
  for (const cb of checkboxes) caps[cb.dataset.cap] = cb.checked;
  try {
    const r = await API.post('/v1/auth/users', {action:'update', user_id: userId, updates:{capabilities: caps}});
    if (r && r.error) { el.checked = !el.checked; await showAlert(r.error); return; }
  } catch(e) { el.checked = !el.checked; await showAlert('Failed: ' + e.message); }
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
          <button onclick="dissolveUserTeam('${esc(t.id)}')" class="um-btn" style="color:#dc2626" title="Dissolve team">Dissolve</button>
        </div>
        <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:4px">
          ${(t.members||[]).map(m => `
            <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:var(--bg-base);border:1px solid var(--border-light);border-radius:12px;font-size:12px">
              ${esc(m.display_name || m.username)}
              ${m.id === t.head_user_id ? '<span class="role-badge poweruser" style="font-size:10px;margin-left:2px">head</span>'
                : `<button onclick="removeUserTeamMember('${esc(t.id)}','${esc(m.id)}')" style="background:none;border:none;cursor:pointer;color:#999;font-size:14px;padding:0;line-height:1" title="Remove">&times;</button>`}
            </span>
          `).join('')}
        </div>
        ${addableUsers.length ? `<div style="display:flex;gap:6px;margin-top:10px;align-items:center">
          <select id="ut-add-${esc(t.id)}" style="flex:1;font-size:12px;padding:6px;border:1px solid var(--border-light);border-radius:6px">
            <option value="">Add member...</option>
            ${addableUsers.map(u => `<option value="${esc(u.id)}">${esc(u.display_name || u.username)} (${esc(u.role)})</option>`).join('')}
          </select>
          <button class="um-btn" onclick="addUserTeamMember('${esc(t.id)}')">Add</button>
        </div>` : ''}
      </div>`;
    };
    modal.innerHTML = `
      <div class="modal-content" style="max-width:720px">
        <div class="modal-header"><h2>User Teams</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
        <div class="modal-body" style="max-height:75vh;overflow-y:auto">
          ${isAdmin ? `<div style="display:flex;justify-content:flex-end;margin-bottom:12px">
            <button class="um-btn" onclick="document.querySelector('.modal-overlay').remove();openUserManagement()">&lsaquo; Users</button>
          </div>` : ''}
          ${teams.length
            ? teams.map(teamCard).join('')
            : '<p style="color:var(--text-secondary);text-align:center;padding:16px">No user teams yet</p>'}
          <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border-light)">
            <h3 style="font-size:14px;margin:0 0 12px">Create Team</h3>
            <div style="display:flex;flex-direction:column;gap:8px">
              <input id="new-user-team-name" placeholder="Team name" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              <input id="new-user-team-desc" placeholder="Description (optional)" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
              ${isAdmin && headCandidates.length ? `
                <select id="new-user-team-head" style="padding:8px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
                  <option value="">Team head... (defaults to you)</option>
                  ${headCandidates.map(u => `<option value="${esc(u.id)}">${esc(u.display_name || u.username)} (${esc(u.role)})</option>`).join('')}
                </select>` : ''}
              <button onclick="createUserTeam()" class="auth-btn" style="width:auto;padding:8px 20px">Create Team</button>
              <div style="font-size:11px;color:var(--text-secondary)">Team head must be a poweruser or admin.</div>
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  } catch(e) { console.error('Failed to load teams:', e); await showAlert('Failed to load teams: ' + e.message); }
}

async function createUserTeam() {
  const name = document.getElementById('new-user-team-name')?.value.trim();
  const desc = document.getElementById('new-user-team-desc')?.value.trim();
  const head = document.getElementById('new-user-team-head')?.value;
  if (!name) { await showAlert('Team name required'); return; }
  try {
    const body = {action:'create', name, description: desc};
    if (head) body.head_user_id = head;
    const r = await API.post('/v1/user-teams', body);
    if (r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Failed to create team: ' + e.message); }
}

async function dissolveUserTeam(teamId) {
  if (!await showConfirmDanger('Dissolve this team? Members will be detached.', 'Dissolve team', 'Dissolve')) return;
  try {
    await API.post('/v1/user-teams', {action:'dissolve', team_id: teamId});
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Failed to dissolve team: ' + e.message); }
}

async function removeUserTeamMember(teamId, userId) {
  try {
    const r = await API.post('/v1/user-teams', {action:'remove_member', team_id: teamId, user_id: userId});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Failed to remove member: ' + e.message); }
}

async function addUserTeamMember(teamId) {
  const sel = document.getElementById('ut-add-' + teamId);
  const uid = sel?.value;
  if (!uid) { await showAlert('Select a user to add'); return; }
  try {
    const r = await API.post('/v1/user-teams', {action:'add_member', team_id: teamId, user_id: uid});
    if (r && r.error) { await showAlert(r.error); return; }
    document.querySelector('.modal-overlay')?.remove();
    openUserTeams();
  } catch(e) { await showAlert('Failed to add member: ' + e.message); }
}

// ── Change Password Modal ──
function openChangePassword() {
  document.getElementById('sb-user-dropdown')?.classList.remove('open');
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.onclick = e => { if (e.target === modal) modal.remove(); };
  modal.innerHTML = `
    <div class="modal-content" style="max-width:400px">
      <div class="modal-header"><h2>Change Password</h2><button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button></div>
      <div class="modal-body">
        <input id="cp-old" type="password" placeholder="Current password" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <input id="cp-new" type="password" placeholder="New password" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:10px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <input id="cp-new2" type="password" placeholder="Confirm new password" style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-bottom:16px;border:1px solid var(--border-light);border-radius:6px;font-size:13px">
        <button onclick="doChangePassword()" class="auth-btn">Change Password</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

async function doChangePassword() {
  const old_password = document.getElementById('cp-old')?.value;
  const new_password = document.getElementById('cp-new')?.value;
  const new_password2 = document.getElementById('cp-new2')?.value;
  if (!old_password || !new_password) { await showAlert('All fields required'); return; }
  if (new_password !== new_password2) { await showAlert('Passwords do not match'); return; }
  try {
    const r = await API.post('/v1/auth/password', {old_password, new_password});
    if (r.error) { await showAlert(r.error); return; }
    await showAlert('Password changed successfully', 'Password changed');
    document.querySelector('.modal-overlay')?.remove();
  } catch(e) { await showAlert('Failed: ' + e.message); }
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
          Account Settings
        </h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div style="display:flex;flex:1;overflow:hidden">
        <div class="modal-tabs modal-tabs-vertical" id="user-settings-tabs" style="width:160px;flex-shrink:0">
          <div class="modal-tab" data-tab="profile" onclick="switchUserSettingsTab('profile', this)">Profile</div>
          <div class="modal-tab" data-tab="memory" onclick="switchUserSettingsTab('memory', this)">Memory</div>
          <div class="modal-tab" data-tab="schedules" onclick="switchUserSettingsTab('schedules', this)">My Schedules</div>
          <div class="modal-tab" data-tab="security" onclick="switchUserSettingsTab('security', this)">Security</div>
        </div>
        <div class="modal-body" id="user-settings-body" style="flex:1;overflow:auto;padding:20px">
          <div style="color:var(--text-400)">Loading…</div>
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
      title="Polish this with AI"
      style="background:none;border:1px solid var(--border-light);border-radius:4px;padding:2px 8px;font-size:11px;color:var(--text-300);cursor:pointer;display:inline-flex;align-items:center;gap:4px">
      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
        <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
        <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
      </svg>
      <span id="${id}-refine-label">Refine with AI</span>
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
      onclick="refineProfileField('${textareaId}', 'Your profile (long-form Markdown with ## Heading sections; keep third-person voice and section headings intact)')"
      title="Polish your profile with AI"
      class="btn-secondary"
      style="font-size:11px;padding:4px 10px;display:inline-flex;align-items:center;gap:4px">
      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" style="flex-shrink:0">
        <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z"/>
        <path d="M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z"/>
      </svg>
      <span id="${textareaId}-refine-label">Refine with AI</span>
    </button>`;
}

async function refineProfileField(textareaId, fieldLabel) {
  const ta = document.getElementById(textareaId);
  if (!ta) return;
  const text = (ta.value || '').replace(/^\s+|\s+$/g, '');
  if (!text) { showToast('Type something first', true); return; }
  const btn = document.getElementById(textareaId + '-refine');
  const lbl = document.getElementById(textareaId + '-refine-label');
  const origLabel = lbl?.textContent || 'Refine with AI';
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Refining…';
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
      if (lbl) lbl.textContent = 'Undo';
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
      showToast('Refined — click Undo to revert');
    } else {
      showToast('Already clean — no change');
      if (lbl) lbl.textContent = origLabel;
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('Refine failed: ' + (e.message || e), true);
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
    {value: '', label: 'Surprise me (auto-pick)'},
    {value: 'off', label: 'Off — no buddy'},
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
      <h3 style="margin:0 0 4px 0;font-size:15px">Profile</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        How the agent identifies and addresses you. Changes save on click.
      </div>
      <div style="font-size:11px;color:var(--text-400);margin-bottom:14px">
        <span style="font-weight:500;color:var(--text-300)">Username:</span> ${esc(u.username || '')}
        &nbsp;·&nbsp;
        <span style="font-weight:500;color:var(--text-300)">Role:</span> ${esc(u.role || 'user')}
      </div>
      ${_us_input('Full name (display)', 'us-display-name', u.display_name, 'text', 'Shown in the sidebar and admin lists.')}
      ${_us_input('Greeting name', 'us-greeting-name', prefs.greeting_name, 'text', 'What the agent should call you in conversation. Falls back to full name.')}
      ${_us_input('Email', 'us-email', u.email, 'email', 'Used for notifications. Not shared.')}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">About you</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Surfaced to the agent on the first turn of each chat so it has context about who you are and how you prefer to be talked to. Keep each field to a sentence or two.
      </div>
      ${_us_textarea('What describes your job in a sentence?', 'us-job-description', prefs.job_description,
        'e.g. "Backend engineer working on payments infrastructure" or "PhD student in computational biology". Max 500 chars.',
        'I\'m a …',
        {refinable: true, fieldLabel: 'Job description'})}
      ${_us_textarea('What are your communication preferences?', 'us-comm-prefs', prefs.communication_preferences,
        'Like soul.md but for you — tone, style, formatting, what to avoid, recurring context the agent should always know. Up to ~4000 characters.',
        'I prefer direct, technical answers. Skip the preamble. Use code blocks for code, markdown sparingly. Don\'t hedge with "it depends" — pick one and explain.\n\nWhen I ask about architecture, default to discussing tradeoffs. When I ask for code, default to small focused diffs.',
        {rows: 12, maxlength: 4000, minHeight: '220px', refinable: true, fieldLabel: 'Communication preferences'})}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">Companion</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        A little ASCII buddy that floats in the bottom-right corner. It fades to nearly invisible when you're idle and brightens on a keystroke or while a reply is generating. Cosmetic only.
      </div>
      ${_us_select('Buddy', 'us-buddy-species', prefs.buddy_species || '', _buddySpeciesOpts(),
        'Pick a species, or turn it off. "Surprise me" picks one for you based on your account.')}
      <div style="display:flex;gap:8px;margin-top:18px">
        <button class="btn-primary" onclick="saveUserSettingsProfile()">Save profile</button>
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
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
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
    if (msg) { msg.textContent = 'Saved.'; msg.style.color = 'var(--success, #16a34a)'; }
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error, #dc2626)'; }
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
    {value: '', label: 'Use server default'},
    {value: '0', label: 'Off — never save chats to memory'},
    {value: '1', label: 'On — save every chat to memory'},
    {value: '2', label: 'Auto — classifier decides per turn'},
  ];
  const schedOpts = [
    {value: '', label: 'Use server default (file artifacts)'},
    {value: '0', label: 'Off — skip scheduled-run artifacts'},
    {value: '1', label: 'On — file scheduled-run artifacts'},
  ];
  const hourOpts = Array.from({length: 24}, (_, i) => ({
    value: String(i), label: `${String(i).padStart(2, '0')}:00 local`,
  }));
  body.innerHTML = `
    <div style="max-width:520px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Memory defaults</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        Controls how your activity is filed to MemPalace. The per-chat toggle in the composer always overrides these defaults.
      </div>
      ${_us_select('Default for new chats', 'us-mem-chats',
        prefs.memory_chats_default == null ? '' : String(prefs.memory_chats_default),
        memOpts,
        'Each new chat starts with this memory mode. You can still flip it per-chat.')}
      ${_us_select('Scheduled-run artifacts', 'us-mem-sched',
        prefs.memory_sched_default == null ? '' : String(prefs.memory_sched_default),
        schedOpts,
        'Whether the miner files artifacts produced by your scheduled tasks. Off keeps them on disk only.')}
      <hr style="border:none;border-top:1px solid var(--border-light);margin:18px 0">
      <h3 style="margin:0 0 4px 0;font-size:15px">Memory from chat history</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:12px">
        Brain reads your chats and maintains a single profile describing your work, interests, and what's currently on your mind. The agent loads this on the first turn of every chat.
      </div>
      ${_us_checkbox('Maintain user profile from chat history', 'us-daily-enabled', !!prefs.daily_summary_enabled,
        "Runs once per day around the chosen hour. The profile lives at agents/main/user_profiles/&lt;you&gt;.md and mirrors per-section drawers into MemPalace. The on-disk file is always clean prose; the chat's caveman mode compresses it on the fly when injected as context.")}
      ${_us_select('Update at', 'us-daily-hour', String(prefs.daily_summary_hour_local ?? 6), hourOpts)}
      <div style="display:flex;gap:8px;margin-top:14px;align-items:center">
        <button class="btn-primary" onclick="saveUserSettingsMemory()">Save memory settings</button>
        <span id="us-mem-msg" style="align-self:center;font-size:12px;color:var(--text-400)"></span>
      </div>

      <div id="us-profile-doc-section" style="margin-top:20px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;flex-wrap:wrap">
          <label for="us-profile-doc" style="font-size:13px;font-weight:500;color:var(--text-200)">Your profile</label>
          <div style="display:flex;gap:6px;align-items:center">
            ${_userProfileRefineControls('us-profile-doc')}
            <button id="us-profile-update-btn" class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="userProfileUpdateNow()">Update now</button>
            <button id="us-profile-reset-btn" class="btn-secondary" style="font-size:11px;padding:4px 10px;color:var(--error,#dc2626)" onclick="userProfileReset()">Reset</button>
          </div>
        </div>
        <textarea id="us-profile-doc" rows="20"
          placeholder="(Profile not yet generated. Enable the toggle above and click Update now, or wait for the daily run.)"
          style="display:block;width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--border-light);border-radius:6px;font-size:12px;background:var(--bg-100);color:var(--text-100);font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;resize:vertical;min-height:380px;line-height:1.5"></textarea>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px">
          <div id="us-profile-meta" style="font-size:11px;color:var(--text-400)"></div>
          <div style="display:flex;gap:6px;align-items:center">
            <span id="us-profile-doc-msg" style="font-size:11px;color:var(--text-400)"></span>
            <button class="btn-primary" style="padding:4px 12px;font-size:12px" onclick="userProfileSave()">Save profile</button>
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-400);margin-top:6px">
          Editable Markdown. Sections must use <code>## Heading</code>. The next daily run will edit your changes in place rather than overwrite them.
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
      if (bytes) parts.push(`${bytes} bytes`);
      if (ts) parts.push(`last run ${ts}${status ? ` · ${status}` : ''}`);
      else if (status) parts.push(status);
      meta.textContent = parts.join(' · ') || (r.exists ? '' : 'Not yet generated');
    }
  } catch (e) {
    if (meta) meta.textContent = 'Failed to load: ' + (e.message || e);
  }
}

async function userProfileSave() {
  const ta = document.getElementById('us-profile-doc');
  const msg = document.getElementById('us-profile-doc-msg');
  if (!ta) return;
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc', {content: ta.value});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = `Saved (${r.bytes} bytes)`; msg.style.color = 'var(--success,#16a34a)'; }
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}

async function userProfileUpdateNow() {
  const btn = document.getElementById('us-profile-update-btn');
  const msg = document.getElementById('us-profile-doc-msg');
  const origLabel = btn?.textContent || 'Update now';
  if (btn) { btn.disabled = true; btn.textContent = 'Updating…'; }
  if (msg) { msg.textContent = 'Generating from chat history (5–60s)…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc/update-now', {});
    if (r.error) throw new Error(r.error);
    const result = r.result || {};
    if (result.status === 'no_activity') {
      if (msg) { msg.textContent = 'No chat activity to summarize yet.'; msg.style.color = 'var(--text-400)'; }
    } else if (result.status === 'error') {
      throw new Error(result.error || 'Generation failed');
    } else {
      if (msg) { msg.textContent = `Updated (${result.bytes} bytes, ${result.samples} chats)`; msg.style.color = 'var(--success,#16a34a)'; }
    }
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origLabel; }
  }
}

async function userProfileReset() {
  if (!await showConfirmDanger('Reset your profile?\n\nDeletes the file + MemPalace drawers. The next daily run (or "Update now") will rebuild from scratch from your last 90 days of chats.\n\nProfile history is kept on disk for rollback.', 'Reset Profile', 'Reset')) {
    return;
  }
  const msg = document.getElementById('us-profile-doc-msg');
  if (msg) { msg.textContent = 'Resetting…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/profile-doc/reset', {});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = 'Reset.'; msg.style.color = 'var(--success,#16a34a)'; }
    const ta = document.getElementById('us-profile-doc');
    if (ta) ta.value = '';
    loadUserProfileDoc();
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}

async function saveUserSettingsMemory() {
  const memChats = document.getElementById('us-mem-chats')?.value;
  const memSched = document.getElementById('us-mem-sched')?.value;
  const dailyEnabled = !!document.getElementById('us-daily-enabled')?.checked;
  const dailyHour = parseInt(document.getElementById('us-daily-hour')?.value || '6', 10);
  const msg = document.getElementById('us-mem-msg');
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
  const prefs = {
    memory_chats_default: memChats === '' ? null : parseInt(memChats, 10),
    memory_sched_default: memSched === '' ? null : parseInt(memSched, 10),
    daily_summary_enabled: dailyEnabled,
    daily_summary_hour_local: dailyHour,
  };
  try {
    const r = await API.post('/v1/auth/preferences', {preferences: prefs});
    if (r.error) throw new Error(r.error);
    if (r.user) state.authUser = r.user;
    if (typeof buddyInit === 'function') buddyInit();
    if (msg) { msg.textContent = 'Saved.'; msg.style.color = 'var(--success, #16a34a)'; }
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error, #dc2626)'; }
  }
}

async function renderUserSettingsSchedules(body) {
  body.innerHTML = `<div style="color:var(--text-400)">Loading your scheduled tasks…</div>`;
  try {
    const r = await API.get('/v1/schedule');
    const schedules = (r && r.schedules) || [];
    if (!schedules.length) {
      body.innerHTML = `
        <div style="max-width:520px">
          <h3 style="margin:0 0 4px 0;font-size:15px">My scheduled tasks</h3>
          <div style="font-size:12px;color:var(--text-400);margin-bottom:14px">
            You don't own any scheduled tasks yet. Create one from the Scheduled view.
          </div>
          <button class="btn-secondary" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('scheduled')">Open Scheduled view</button>
        </div>`;
      return;
    }
    const rows = schedules.map(s => {
      const enabled = !!s.enabled;
      const nextRun = s.next_run ? new Date(s.next_run).toLocaleString() : '—';
      return `
        <tr>
          <td style="padding:8px 6px;font-weight:500">${esc(s.name || '')}</td>
          <td style="padding:8px 6px;font-size:12px;color:var(--text-300)">${esc(s.schedule || '')}</td>
          <td style="padding:8px 6px;font-size:12px;color:var(--text-300)">${esc(nextRun)}</td>
          <td style="padding:8px 6px;font-size:12px;color:${enabled ? 'var(--success,#16a34a)' : 'var(--text-400)'}">
            ${enabled ? (s.is_running ? 'running' : 'enabled') : 'paused'}
          </td>
        </tr>`;
    }).join('');
    body.innerHTML = `
      <div>
        <h3 style="margin:0 0 4px 0;font-size:15px">My scheduled tasks (${schedules.length})</h3>
        <div style="font-size:12px;color:var(--text-400);margin-bottom:14px">
          Tasks you own. Edit, pause, or delete them from the Scheduled view.
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="text-align:left;border-bottom:1px solid var(--border-light)">
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Name</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Schedule</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">Next run</th>
            <th style="padding:6px;font-weight:500;color:var(--text-300)">State</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="margin-top:14px">
          <button class="btn-secondary" onclick="document.querySelector('.modal-overlay')?.remove();navigateTo('scheduled')">Open Scheduled view</button>
        </div>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div style="color:var(--error,#dc2626)">Failed to load schedules: ${esc(e.message || e)}</div>`;
  }
}

function renderUserSettingsSecurity(body) {
  body.innerHTML = `
    <div style="max-width:480px">
      <h3 style="margin:0 0 4px 0;font-size:15px">Change password</h3>
      <div style="font-size:12px;color:var(--text-400);margin-bottom:16px">
        Pick something at least 6 characters long.
      </div>
      ${_us_input('Current password', 'us-pw-old', '', 'password')}
      ${_us_input('New password', 'us-pw-new', '', 'password')}
      ${_us_input('Confirm new password', 'us-pw-new2', '', 'password')}
      <div style="display:flex;gap:8px;margin-top:6px">
        <button class="btn-primary" onclick="saveUserSettingsPassword()">Change password</button>
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
    if (msg) { msg.textContent = 'All fields required'; msg.style.color = 'var(--error,#dc2626)'; }
    return;
  }
  if (new_password !== new2) {
    if (msg) { msg.textContent = 'Passwords do not match'; msg.style.color = 'var(--error,#dc2626)'; }
    return;
  }
  if (msg) { msg.textContent = 'Saving…'; msg.style.color = 'var(--text-400)'; }
  try {
    const r = await API.post('/v1/auth/password', {old_password, new_password});
    if (r.error) throw new Error(r.error);
    if (msg) { msg.textContent = 'Password changed.'; msg.style.color = 'var(--success,#16a34a)'; }
    document.getElementById('us-pw-old').value = '';
    document.getElementById('us-pw-new').value = '';
    document.getElementById('us-pw-new2').value = '';
  } catch (e) {
    if (msg) { msg.textContent = 'Failed: ' + (e.message || e); msg.style.color = 'var(--error,#dc2626)'; }
  }
}
