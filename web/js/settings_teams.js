// settings_teams.js — agent team CRUD (create/dissolve/add/remove). Split from settings.js (Tier F Phase 2). Global <script>, no modules.

async function _createTeam() {
  const name = document.getElementById('new-team-name')?.value?.trim();
  const desc = document.getElementById('new-team-desc')?.value?.trim() || '';
  const head = document.getElementById('new-team-head')?.value;
  const membersEl = document.getElementById('new-team-members');
  const members = Array.from(membersEl?.selectedOptions || []).map(o => o.value);
  if (!head) { showToast('Team-Leiter ist erforderlich', true); return; }
  if (!members.length) { showToast('Wählen Sie mindestens ein Mitglied aus', true); return; }
  if (!members.includes(head)) members.push(head);
  try {
    await API.manageTeams({ action: 'create', head, members, name: name || undefined, description: desc || undefined });
    showToast(`Team „${name || head}" erstellt`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Team-Erstellung fehlgeschlagen: ' + e.message, true); }
}
async function _dissolveTeam(teamId) {
  if (!await showConfirmDanger(`Dieses Team auflösen? Mitglieder werden eigenständig.`, 'Team auflösen', 'Auflösen')) return;
  try {
    await API.manageTeams({ action: 'dissolve', team_id: teamId });
    showToast('Team aufgelöst');
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Auflösen fehlgeschlagen: ' + e.message, true); }
}
async function _removeFromTeam(agentId, teamId) {
  try {
    await API.manageTeams({ action: 'move', agent: agentId, from_team: teamId, to_team: null });
    showToast(`${agentId} aus Team entfernt`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Entfernen fehlgeschlagen: ' + e.message, true); }
}
async function _addToTeam(teamId) {
  const sel = document.getElementById('team-add-' + teamId);
  const agentId = sel?.value;
  if (!agentId) { showToast('Wählen Sie einen Agent zum Hinzufügen aus', true); return; }
  try {
    const ts = state.teamStructure;
    // Find which team the agent is currently in (if any)
    let fromTeam = null;
    if (ts.teams) {
      for (const [tid, team] of Object.entries(ts.teams)) {
        if ((team.members||[]).some(m => m.id === agentId)) { fromTeam = tid; break; }
      }
    }
    await API.manageTeams({ action: 'move', agent: agentId, from_team: fromTeam, to_team: teamId });
    showToast(`${agentId} zum Team hinzugefügt`);
    await _refreshAgentsAndTeams();
    switchGeneralTab('teams');
  } catch(e) { showToast('Hinzufügen fehlgeschlagen: ' + e.message, true); }
}

