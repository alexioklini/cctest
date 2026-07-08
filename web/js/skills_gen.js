/* skills_gen.js — KI-Skill-Generator: turn a chat (or its approved MoA plan)
   into a reusable, shareable SKILL.md. Mirrors the workflow generator
   (workflows.js) but is self-contained: generate → poll → review (slug/name/
   description/body + sharing) → save. Backend: engine/skill_gen.py +
   handlers/admin_skills_gen.py. All identifiers are browser globals (no ES
   modules) — see web/js CLAUDE.md; keep the net-globals count in sync with
   js_gate.sh. */

const SKILL_GEN_AGENT = 'main';  // single-agent MVP, matches WF_AGENT.

const skillGen = { genId: null, timer: null, source: null, draft: null };

// Fixed generation stages (mirror engine/skill_gen.py's phase steps) + the
// bar percentage shown while that stage is ACTIVE.
const SKILL_GEN_STAGES = [
  { label: 'Quellmaterial sammeln', pct: 10 },
  { label: 'Skill verfassen',       pct: 45 },
  { label: 'Entwurf validieren',    pct: 80 },
  { label: 'Fertigstellen',         pct: 95 },
];

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
    skillRenderProgress({ status: 'generating', steps: [] });
    skillGen.timer = setInterval(skillPollGenerate, 1500);
  } catch (e) {
    document.getElementById('skill-gen-steps').innerHTML = '';
    if (typeof showToast === 'function') showToast('Start fehlgeschlagen: ' + e.message, true);
  }
}

function skillRenderProgress(d) {
  const bar = document.getElementById('skill-gen-bar');
  const list = document.getElementById('skill-gen-checklist');
  const detail = document.getElementById('skill-gen-steps');
  if (!bar || !list) return;
  const steps = d.steps || [];
  const phases = steps.filter(s => s.kind === 'phase').map(s => s.text || '');
  const last = phases.length ? phases[phases.length - 1] : '';
  const ready = d.status === 'ready' || d.status === 'ready_with_warnings';
  const failed = d.status === 'error' || d.status === 'cancelled';
  // Stage from the newest phase step. The prefixes are OUR step texts
  // (engine/skill_gen.py _push_step) — keep both sides in sync.
  let idx = 1;
  if (ready || /^Fertig/.test(last)) idx = 4;
  else if (/^Validiert/.test(last)) idx = 3;
  else if (/^(Verfasst|Korrigiert)/.test(last)) idx = 2;
  const ICONS = {
    done: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#16a34a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    active: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="var(--accent, #3b82f6)" stroke-width="2.5" stroke-linecap="round"><path d="M12 2 a10 10 0 0 1 10 10"/></svg>',
    pending: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" opacity="0.4"><circle cx="12" cy="12" r="9"/></svg>',
    failed: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#dc2626" stroke-width="2.5" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>',
  };
  list.innerHTML = SKILL_GEN_STAGES.map((st, i) => {
    const n = i + 1;
    let state = 'pending';
    if (ready || n < idx) state = 'done';
    else if (n === idx) state = failed ? 'failed' : 'active';
    return `<div class="sg-check-row sg-${state}">` +
           `<span class="sg-check-icon">${ICONS[state]}</span>` +
           `<span>${st.label}</span></div>`;
  }).join('');
  bar.style.width = (ready ? 100 : SKILL_GEN_STAGES[idx - 1].pct) + '%';
  bar.classList.toggle('sg-failed', failed);
  bar.classList.toggle('sg-active', !failed && !ready);
  if (detail) {
    // Detail log: model line + non-phase steps (info/error) — the phases
    // themselves are represented by the checklist above.
    const rows = steps.filter(s => s.kind !== 'phase').map(s =>
      `<div style="${s.kind === 'error' ? 'color:var(--error,#ef4444)' : ''}">${escapeHtml(s.text)}</div>`);
    if (d.model) rows.unshift(`<div>Modell: ${escapeHtml(d.model)}</div>`);
    detail.innerHTML = rows.join('');
  }
}

async function skillPollGenerate() {
  if (!skillGen.genId) return;
  let d;
  try { d = await API.get(`/v1/skills/generate/${skillGen.genId}`); }
  catch (e) { return; }
  skillRenderProgress(d);
  if (d.status === 'generating') return;
  clearInterval(skillGen.timer); skillGen.timer = null;
  skillGen.genId = null;
  if (d.status === 'ready' || d.status === 'ready_with_warnings') {
    skillShowReview(d);
  } else if (d.status === 'error') {
    const detail = document.getElementById('skill-gen-steps');
    if (detail) detail.innerHTML += `<div style="color:var(--error,#ef4444)">Fehler: ${escapeHtml(d.error || 'unbekannt')}</div>`;
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
