/* skills_gen.js — KI-Skill-Generator: turn a chat (or its approved MoA plan)
   into a reusable, shareable SKILL.md. Mirrors the workflow generator
   (workflows.js) but is self-contained: generate → poll → review (slug/name/
   description/body + sharing) → save. Backend: engine/skill_gen.py +
   handlers/admin_skills_gen.py. All identifiers are browser globals (no ES
   modules) — see web/js CLAUDE.md; keep the net-globals count in sync with
   js_gate.sh. */

const SKILL_GEN_AGENT = 'main';  // single-agent MVP, matches WF_AGENT.

const skillGen = { genId: null, timer: null, source: null, draft: null };

function skillGenerateFromChat(sessionId, title) {
  const chat = (typeof state !== 'undefined' && state.activeChat) ? state.activeChat : null;
  const sid = sessionId || (chat && chat.sessionId) || '';
  if (!sid) { if (typeof showToast === 'function') showToast('Kein aktiver Chat', true); return; }
  skillOpenModal('chat', { session_id: sid, title: title || (chat && chat.title) || '' });
}

function skillOpenModal(kind, payload) {
  skillGen.source = Object.assign({ type: kind }, payload || {});
  skillGen.genId = null; skillGen.draft = null;
  if (skillGen.timer) { clearInterval(skillGen.timer); skillGen.timer = null; }
  const srcLine = document.getElementById('skill-gen-source-line');
  if (srcLine) {
    srcLine.textContent = kind === 'chat'
      ? ('Quelle: dieser Chat' + (payload && payload.title ? ' — ' + payload.title : ''))
      : (kind === 'plan' ? 'Quelle: Plan-Dokument' : 'Quelle: Beschreibung');
  }
  document.getElementById('skill-gen-form').classList.remove('hidden');
  document.getElementById('skill-gen-progress').classList.add('hidden');
  document.getElementById('skill-gen-review').classList.add('hidden');
  const instr = document.getElementById('skill-gen-instructions');
  if (instr) instr.value = '';
  document.getElementById('skill-generate-modal').classList.remove('hidden');
}

function skillCloseModal() {
  if (skillGen.timer) { clearInterval(skillGen.timer); skillGen.timer = null; }
  // A still-running generation would leave an unreachable draft — cancel it.
  if (skillGen.genId) {
    API.post(`/v1/skills/generate/${skillGen.genId}/cancel`, {}).catch(() => {});
    skillGen.genId = null;
  }
  document.getElementById('skill-generate-modal').classList.add('hidden');
}

async function skillStartGenerate() {
  const instr = (document.getElementById('skill-gen-instructions').value || '').trim();
  const src = skillGen.source || { type: 'nl' };
  const body = { agent_id: SKILL_GEN_AGENT, instructions: instr };
  if (src.type === 'chat') {
    body.source = { type: 'chat', session_id: src.session_id };
  } else if (src.type === 'plan') {
    body.source = { type: 'plan', text: src.text || '' };
  } else {
    if (!instr) { document.getElementById('skill-gen-instructions').focus(); return; }
    body.source = { type: 'nl', text: instr };
  }
  try {
    const res = await API.post('/v1/skills/generate', body);
    if (res.error) throw new Error(res.error);
    skillGen.genId = res.gen_id;
    document.getElementById('skill-gen-form').classList.add('hidden');
    document.getElementById('skill-gen-progress').classList.remove('hidden');
    document.getElementById('skill-gen-steps').innerHTML = '<div>Gestartet …</div>';
    skillGen.timer = setInterval(skillPollGenerate, 1500);
  } catch (e) {
    document.getElementById('skill-gen-steps').innerHTML = '';
    if (typeof showToast === 'function') showToast('Start fehlgeschlagen: ' + e.message, true);
  }
}

async function skillPollGenerate() {
  if (!skillGen.genId) return;
  let d;
  try { d = await API.get(`/v1/skills/generate/${skillGen.genId}`); }
  catch (e) { return; }
  const stepsEl = document.getElementById('skill-gen-steps');
  if (stepsEl) {
    const rows = (d.steps || []).map(s =>
      `<div style="${s.kind === 'error' ? 'color:var(--error,#ef4444)' : ''}">${escapeHtml(s.text)}</div>`);
    if (d.status === 'generating' && d.phase) rows.push(`<div>… (${escapeHtml(d.phase)})</div>`);
    stepsEl.innerHTML = rows.join('') || '<div>…</div>';
  }
  if (d.status === 'generating') return;
  clearInterval(skillGen.timer); skillGen.timer = null;
  skillGen.genId = null;
  if (d.status === 'ready' || d.status === 'ready_with_warnings') {
    skillShowReview(d);
  } else if (d.status === 'error') {
    if (stepsEl) stepsEl.innerHTML += `<div style="color:var(--error,#ef4444)">Fehler: ${escapeHtml(d.error || 'unbekannt')}</div>`;
  }
}

function skillCancelGenerate() {
  if (!skillGen.genId) { skillCloseModal(); return; }
  API.post(`/v1/skills/generate/${skillGen.genId}/cancel`, {}).catch(() => {});
  skillGen.genId = null;
  if (skillGen.timer) { clearInterval(skillGen.timer); skillGen.timer = null; }
  skillCloseModal();
}

async function skillShowReview(d) {
  skillGen.draft = d;
  document.getElementById('skill-gen-progress').classList.add('hidden');
  document.getElementById('skill-gen-review').classList.remove('hidden');
  document.getElementById('skill-review-slug').value = d.slug || '';
  document.getElementById('skill-review-name').value = d.display_name || '';
  document.getElementById('skill-review-desc').value = d.description || '';
  document.getElementById('skill-review-body').value = d.body_md || '';
  document.getElementById('skill-review-visibility').value = 'private';
  const notes = document.getElementById('skill-gen-notes');
  const warns = d.warnings || [];
  if (notes) {
    notes.textContent = (d.notes || 'Entwurf erzeugt — bitte prüfen und speichern.') +
      (warns.length ? ` — ${warns.length} Hinweis(e): ${warns.join('; ')}` : '');
    notes.className = 'wf-editor-status' + (warns.length ? ' wf-error' : ' wf-ok');
  }
  // Populate the team dropdown (own teams) for the 'team' visibility option.
  try {
    const r = await API.get('/v1/user-teams');
    const teams = (r.teams || r || []);
    const sel = document.getElementById('skill-review-team');
    if (sel) {
      sel.innerHTML = teams.length
        ? teams.map(t => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.name || t.id)}</option>`).join('')
        : '<option value="">(keine Teams)</option>';
    }
  } catch (e) { /* team option just stays empty */ }
  skillOnVisibilityChange();
}

function skillOnVisibilityChange() {
  const v = document.getElementById('skill-review-visibility').value;
  const row = document.getElementById('skill-review-team-row');
  if (row) row.style.display = (v === 'team') ? 'flex' : 'none';
}

async function skillSaveReviewed() {
  const slug = (document.getElementById('skill-review-slug').value || '').trim();
  const body_md = (document.getElementById('skill-review-body').value || '').trim();
  if (!slug) { if (typeof showToast === 'function') showToast('Kurzname (slug) fehlt', true); return; }
  if (!body_md) { if (typeof showToast === 'function') showToast('Skill-Inhalt ist leer', true); return; }
  const src = skillGen.source || {};
  const payload = {
    agent_id: SKILL_GEN_AGENT,
    slug,
    display_name: (document.getElementById('skill-review-name').value || '').trim(),
    description: (document.getElementById('skill-review-desc').value || '').trim(),
    body_md,
    visibility: document.getElementById('skill-review-visibility').value,
    source_kind: src.type || '',
    source_ref: src.session_id || '',
  };
  if (payload.visibility === 'team') {
    const t = document.getElementById('skill-review-team').value;
    if (!t) { if (typeof showToast === 'function') showToast('Wählen Sie zuerst ein Team', true); return; }
    payload.owner_team_id = t;
  }
  const btn = document.getElementById('skill-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Speichert …'; }
  try {
    const res = await API.post('/v1/skills/save', payload);
    if (res.error) throw new Error(res.error);
    if (typeof showToast === 'function') showToast(`Skill „${res.slug}" gespeichert`);
    skillCloseModal();
  } catch (e) {
    if (typeof showToast === 'function') showToast('Speichern fehlgeschlagen: ' + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Skill speichern'; }
  }
}
