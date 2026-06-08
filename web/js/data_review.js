'use strict';

/* ═══════════════════════════════════════════════════════════
   Document Review — GDPR + Classification reviewer
   Full-screen overlay: rendered document text with inline,
   color-coded highlights, a violation navigator (prev/next/jump),
   per-violation tooltips, overrule, anonymise, revert, export.
   Backend: /v1/data-review/*
   Shared by the Data view, the project tree, and right-panel
   attachments — all open it via drOpen*().
   ═══════════════════════════════════════════════════════════ */

const drState = {
  review: null,        // last /analyze or /<id> payload
  current: -1,         // index into visible violations for the navigator
  filter: 'all',       // 'all' | 'pii' | 'classification'
};

function drEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ── Open paths ─────────────────────────────────────────────── */

// From the Data view scan table (uploaded File object).
async function drOpenFile(file) {
  drShowOverlay();
  drSetStatus('Analysiere…');
  try {
    const fd = new FormData();
    fd.append('files', file, file.name);
    const t = localStorage.getItem('auth-token');
    const r = await fetch(`${window.location.origin}/v1/data-review/analyze`, {
      method: 'POST',
      headers: t ? { Authorization: `Bearer ${t}` } : {},
      body: fd,
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    drRender(await r.json());
  } catch (e) {
    drSetStatus('Fehler: ' + e.message, true);
  }
}

// From the project tree / attachment (server-side file by path or hash).
async function drOpenProjectFile({ agentId, project, path, sourceHash }) {
  drShowOverlay();
  drSetStatus('Analysiere…');
  try {
    const body = { agent_id: agentId, project };
    if (path) body.path = path;
    if (sourceHash) body.source_hash = sourceHash;
    const resp = await API.post('/v1/data-review/analyze', body);
    if (resp.error) throw new Error(resp.error);
    drRender(resp);
  } catch (e) {
    drSetStatus('Fehler: ' + e.message, true);
  }
}

// Re-open a persisted review by id.
async function drOpenReview(reviewId) {
  drShowOverlay();
  drSetStatus('Lade…');
  try {
    const resp = await API.get(`/v1/data-review/${encodeURIComponent(reviewId)}`);
    if (resp.error) throw new Error(resp.error);
    drRender(resp);
  } catch (e) {
    drSetStatus('Fehler: ' + e.message, true);
  }
}

/* ── Overlay shell ──────────────────────────────────────────── */

function drShowOverlay() {
  let ov = document.getElementById('dr-overlay');
  if (!ov) {
    ov = document.createElement('div');
    ov.id = 'dr-overlay';
    ov.className = 'dr-overlay';
    ov.innerHTML = `
      <div class="dr-modal">
        <div class="dr-header">
          <div class="dr-title" id="dr-title">Dokument-Prüfung</div>
          <div class="dr-nav" id="dr-nav"></div>
          <button class="dr-close" onclick="drClose()" title="Schließen">✕</button>
        </div>
        <div class="dr-toolbar" id="dr-toolbar"></div>
        <div class="dr-body">
          <div class="dr-doc" id="dr-doc"></div>
          <div class="dr-side" id="dr-side"></div>
        </div>
        <div class="dr-status" id="dr-status"></div>
      </div>`;
    document.body.appendChild(ov);
    ov.addEventListener('click', e => { if (e.target === ov) drClose(); });
    document.addEventListener('keydown', drKeydown);
  }
  ov.style.display = 'flex';
}

function drClose() {
  const ov = document.getElementById('dr-overlay');
  if (ov) ov.style.display = 'none';
}

function drKeydown(e) {
  const ov = document.getElementById('dr-overlay');
  if (!ov || ov.style.display === 'none') return;
  if (e.key === 'Escape') drClose();
  else if (e.key === 'ArrowRight' || e.key === 'n') drJump(1);
  else if (e.key === 'ArrowLeft' || e.key === 'p') drJump(-1);
}

function drSetStatus(msg, isErr) {
  const el = document.getElementById('dr-status');
  if (el) { el.textContent = msg || ''; el.classList.toggle('dr-err', !!isErr); }
}

/* ── Render ─────────────────────────────────────────────────── */

function drVisibleViolations() {
  const vs = (drState.review && drState.review.violations) || [];
  if (drState.filter === 'all') return vs;
  return vs.filter(v => v.kind === drState.filter);
}

function drOverruledSet() {
  const ovs = (drState.review && drState.review.overrules) || [];
  return new Set(ovs.map(o => o.id));
}

function drRender(review) {
  drState.review = review;
  drState.current = -1;
  document.getElementById('dr-title').textContent =
    (review.filename || 'Dokument') +
    (review.reused ? '  ·  (bereits geprüft)' : '');
  drRenderToolbar();
  drRenderDoc();
  drRenderSide();
  drRenderNav();
  drSetStatus('');
}

function drRenderToolbar() {
  const r = drState.review;
  const counts = r.counts || {};
  const pii = (r.violations || []).filter(v => v.kind === 'pii').length;
  const cls = (r.violations || []).filter(v => v.kind === 'classification').length;
  const anon = r.anonymised;
  const tb = document.getElementById('dr-toolbar');
  tb.innerHTML = `
    <div class="dr-filters">
      <button class="dr-fbtn ${drState.filter === 'all' ? 'active' : ''}" onclick="drSetFilter('all')">Alle (${(r.violations || []).length})</button>
      <button class="dr-fbtn ${drState.filter === 'pii' ? 'active' : ''}" onclick="drSetFilter('pii')">GDPR (${pii})</button>
      <button class="dr-fbtn ${drState.filter === 'classification' ? 'active' : ''}" onclick="drSetFilter('classification')">Klassifizierung (${cls})</button>
    </div>
    <div class="dr-actions">
      ${anon
        ? `<span class="dr-badge dr-badge-anon" title="Anonymisierte Version wird an das LLM gesendet">🛡️ Anonymisiert</span>
           <button class="dr-abtn" onclick="drExport()">Anon. Kopie exportieren</button>
           <button class="dr-abtn dr-warn" onclick="drRevert()">Zurücksetzen (Original)</button>`
        : `<button class="dr-abtn dr-primary" onclick="drAnonymise()">Anonymisieren</button>`}
    </div>`;
}

function drSetFilter(f) { drState.filter = f; drState.current = -1; drRenderToolbar(); drRenderDoc(); drRenderSide(); drRenderNav(); }

// Build the document HTML with inline highlight spans for each violation.
function drRenderDoc() {
  const r = drState.review;
  const text = r.text || '';
  const overruled = drOverruledSet();
  // Use ALL violations for spans (not just filtered) so positions stay exact;
  // dim ones outside the current filter.
  const all = (r.violations || []).slice().sort((a, b) => a.start - b.start);
  let html = '';
  let pos = 0;
  for (const v of all) {
    if (v.start < pos) continue; // skip overlaps defensively
    html += drEsc(text.slice(pos, v.start));
    const cls = [
      'dr-hl',
      v.kind === 'pii' ? 'dr-hl-pii' : 'dr-hl-cls',
      overruled.has(v.id) ? 'dr-hl-over' : '',
      (drState.filter !== 'all' && v.kind !== drState.filter) ? 'dr-hl-dim' : '',
    ].join(' ');
    html += `<span class="${cls}" id="dr-hl-${v.id}" data-vid="${v.id}" `
         + `onclick="drSelectById('${v.id}')" `
         + `title="${drEsc(v.label)} — ${drEsc(v.why)}">`
         + drEsc(text.slice(v.start, v.end)) + `</span>`;
    pos = v.end;
  }
  html += drEsc(text.slice(pos));
  const doc = document.getElementById('dr-doc');
  doc.innerHTML = html || '<em>Kein Textinhalt.</em>';
}

function drRenderSide() {
  const vs = drVisibleViolations();
  const overruled = drOverruledSet();
  const r = drState.review;
  const ovById = {};
  (r.overrules || []).forEach(o => { ovById[o.id] = o; });
  const items = vs.map((v, i) => {
    const isOver = overruled.has(v.id);
    const ov = ovById[v.id];
    return `
      <div class="dr-item ${isOver ? 'dr-item-over' : ''}" id="dr-item-${v.id}" onclick="drSelectById('${v.id}')">
        <div class="dr-item-head">
          <span class="dr-chip ${v.kind === 'pii' ? 'dr-chip-pii' : 'dr-chip-cls'}">${v.kind === 'pii' ? 'GDPR' : 'KLASS'}</span>
          <span class="dr-item-label">${drEsc(v.label)}</span>
          ${isOver ? '<span class="dr-chip dr-chip-over">übersteuert</span>' : ''}
        </div>
        <div class="dr-item-ex">"${drEsc((v.excerpt || '').slice(0, 80))}"</div>
        <div class="dr-item-why">${drEsc(v.why)}</div>
        ${isOver
          ? `<div class="dr-item-ovexpl">Begründung: ${drEsc(ov ? ov.explanation : '')}
               <button class="dr-link" onclick="event.stopPropagation();drUnoverrule('${v.id}')">aufheben</button></div>`
          : `<button class="dr-link" onclick="event.stopPropagation();drOverrulePrompt('${v.id}')">Übersteuern…</button>`}
      </div>`;
  }).join('');
  document.getElementById('dr-side').innerHTML = items ||
    '<div class="dr-empty">Keine Verstöße in dieser Ansicht. ✓</div>';
}

function drRenderNav() {
  const vs = drVisibleViolations();
  const nav = document.getElementById('dr-nav');
  if (!vs.length) { nav.innerHTML = '<span class="dr-clean">✓ sauber</span>'; return; }
  const n = drState.current >= 0 ? (drState.current + 1) : 0;
  nav.innerHTML = `
    <button class="dr-navbtn" onclick="drJump(-1)" title="Zurück (←)">‹</button>
    <span class="dr-navcount">${n} / ${vs.length}</span>
    <button class="dr-navbtn" onclick="drJump(1)" title="Weiter (→)">›</button>`;
}

/* ── Navigation + selection ─────────────────────────────────── */

function drJump(delta) {
  const vs = drVisibleViolations();
  if (!vs.length) return;
  let idx = drState.current + delta;
  if (idx < 0) idx = vs.length - 1;
  if (idx >= vs.length) idx = 0;
  drSelect(idx);
}

function drSelect(idx) {
  const vs = drVisibleViolations();
  if (idx < 0 || idx >= vs.length) return;
  drState.current = idx;
  const v = vs[idx];
  document.querySelectorAll('.dr-hl.dr-active').forEach(e => e.classList.remove('dr-active'));
  document.querySelectorAll('.dr-item.dr-active').forEach(e => e.classList.remove('dr-active'));
  const hl = document.getElementById('dr-hl-' + v.id);
  const item = document.getElementById('dr-item-' + v.id);
  if (hl) { hl.classList.add('dr-active'); hl.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
  if (item) { item.classList.add('dr-active'); item.scrollIntoView({ block: 'nearest' }); }
  drRenderNav();
}

function drSelectById(vid) {
  const vs = drVisibleViolations();
  const idx = vs.findIndex(v => v.id === vid);
  if (idx >= 0) drSelect(idx);
}

/* ── Overrule ───────────────────────────────────────────────── */

function drOverrulePrompt(vid) {
  const expl = window.prompt('Begründung für die Übersteuerung dieses Treffers:');
  if (expl == null || !expl.trim()) return;
  drOverrule(vid, expl.trim());
}

async function drOverrule(vid, explanation) {
  const r = drState.review;
  try {
    const resp = await API.post('/v1/data-review/overrule', {
      review_id: r.review_id, violation_id: vid, explanation,
    });
    if (resp.error) throw new Error(resp.error);
    await drOpenReview(r.review_id);
  } catch (e) { drSetStatus('Fehler: ' + e.message, true); }
}

async function drUnoverrule(vid) {
  const r = drState.review;
  try {
    const resp = await API.post('/v1/data-review/overrule', {
      review_id: r.review_id, violation_id: vid, remove: true,
    });
    if (resp.error) throw new Error(resp.error);
    await drOpenReview(r.review_id);
  } catch (e) { drSetStatus('Fehler: ' + e.message, true); }
}

/* ── Anonymise / revert / export ────────────────────────────── */

async function drAnonymise() {
  const r = drState.review;
  drSetStatus('Anonymisiere…');
  try {
    const resp = await API.post('/v1/data-review/anonymise', { review_id: r.review_id });
    if (resp.error) throw new Error(resp.error);
    if (resp.status === 'noop') { drSetStatus(resp.message || 'Nichts zu anonymisieren.'); return; }
    await drOpenReview(r.review_id);
    drSetStatus(`${resp.replaced} Treffer anonymisiert. Das LLM erhält die anonymisierte Version.`);
  } catch (e) { drSetStatus('Fehler: ' + e.message, true); }
}

async function drRevert() {
  const r = drState.review;
  if (!window.confirm('Anonymisierung zurücksetzen? Das Original wird wieder verwendet.')) return;
  try {
    const resp = await API.post('/v1/data-review/revert', { review_id: r.review_id });
    if (resp.error) throw new Error(resp.error);
    await drOpenReview(r.review_id);
    drSetStatus('Zurückgesetzt — das Original wird verwendet.');
  } catch (e) { drSetStatus('Fehler: ' + e.message, true); }
}

function drExport() {
  const r = drState.review;
  const t = localStorage.getItem('auth-token');
  fetch(`${window.location.origin}/v1/data-review/${encodeURIComponent(r.review_id)}/export`,
    { headers: t ? { Authorization: `Bearer ${t}` } : {} })
    .then(resp => { if (!resp.ok) throw new Error(resp.status); return resp.blob(); })
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = (r.filename || 'document').replace(/(\.[^.]+)?$/, '.anon$1');
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    })
    .catch(e => drSetStatus('Export-Fehler: ' + e, true));
}
