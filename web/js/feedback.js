'use strict';

/* ═══════════════════════════════════════════════════════════
   FEEDBACK WIDGET — 👍/👎 + optional comment on any assistant
   response or result, across every surface (chat, brainy, workflow,
   schedule, translation, classification).

   Globals exposed (load order: after api/state/utils, before surfaces):
     renderFeedbackControl(surface, targetId, sessionId, snapshot) -> HTML
     feedbackThumb(surface, targetId, sessionId, rating, btn)      -> onclick
     feedbackSubmit(surface, targetId, sessionId, rating)          -> submit
     feedbackHydrateState(surface, sessionId)                      -> restore

   The control stores its context in data attributes on a wrapper
   (`.fb-control`) so a single set of handlers serves all surfaces.
   ═══════════════════════════════════════════════════════════ */

// In-memory cache of the caller's own ratings, keyed "surface|targetId"
// -> 'up'|'down'. Populated by feedbackHydrateState, read on render.
const _fbMine = {};

// Escape a value for use inside an attribute-selector "...". Our ids are
// numeric/uuid/slug, but guard quotes + backslashes just in case.
function _fbSel(v) { return String(v).replace(/(["\\])/g, '\\$1'); }

function renderFeedbackControl(surface, targetId, sessionId, snapshot) {
  const tid = String(targetId == null ? '' : targetId);
  if (!tid) return '';
  const key = surface + '|' + tid;
  const mine = _fbMine[key] || '';
  const a = (s) => (s === mine ? ' fb-active' : '');
  const dsnap = esc(String(snapshot || '').slice(0, 500));
  return `<span class="fb-control" data-fb-surface="${esc(surface)}" `
    + `data-fb-target="${esc(tid)}" data-fb-session="${esc(sessionId || '')}" `
    + `data-fb-snapshot="${dsnap}">`
    + `<button type="button" class="fb-thumb fb-up${a('up')}" title="Hilfreich" `
    + `onclick="feedbackThumb('${esc(surface)}','${esc(tid)}','${esc(sessionId || '')}','up',this)">`
    + `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7 11v9H4a1 1 0 01-1-1v-7a1 1 0 011-1h3zm0 0l4.5-8.5A2 2 0 0115 4l-1 6h5.5a2 2 0 011.95 2.45l-1.7 7A2 2 0 0116.8 21H7"/></svg>`
    + `</button>`
    + `<button type="button" class="fb-thumb fb-down${a('down')}" title="Nicht hilfreich" `
    + `onclick="feedbackThumb('${esc(surface)}','${esc(tid)}','${esc(sessionId || '')}','down',this)">`
    + `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M17 13V4h3a1 1 0 011 1v7a1 1 0 01-1 1h-3zm0 0l-4.5 8.5A2 2 0 019 20l1-6H4.5a2 2 0 01-1.95-2.45l1.7-7A2 2 0 017.2 3H17"/></svg>`
    + `</button>`
    + `</span>`;
}

// Open (or re-open, switching rating) the comment popover.
function feedbackThumb(surface, targetId, sessionId, rating, btn) {
  const ctrl = btn.closest('.fb-control');
  if (!ctrl) return;
  // Reflect provisional selection on the thumbs.
  ctrl.querySelectorAll('.fb-thumb').forEach(b => b.classList.remove('fb-active'));
  btn.classList.add('fb-active');
  // Build / reuse the popover.
  let pop = ctrl.querySelector('.fb-popover');
  if (!pop) {
    pop = document.createElement('div');
    pop.className = 'fb-popover';
    pop.innerHTML = `<textarea class="fb-comment" rows="2" `
      + `placeholder="Optional: Was war gut bzw. nicht gut? (hilft uns sehr)"></textarea>`
      + `<div class="fb-popover-actions">`
      + `<button type="button" class="fb-cancel">Abbrechen</button>`
      + `<button type="button" class="fb-send">Senden</button>`
      + `</div>`;
    ctrl.appendChild(pop);
    pop.querySelector('.fb-cancel').addEventListener('click', () => pop.remove());
    pop.querySelector('.fb-send').addEventListener('click', () => {
      const chosen = ctrl.querySelector('.fb-thumb.fb-active');
      const r = chosen && chosen.classList.contains('fb-down') ? 'down' : 'up';
      feedbackSubmit(surface, targetId, sessionId, r);
    });
  }
  pop.dataset.rating = rating;
  const ta = pop.querySelector('.fb-comment');
  setTimeout(() => ta && ta.focus(), 0);
}

async function feedbackSubmit(surface, targetId, sessionId, rating) {
  // Find the control in the DOM by its data attributes.
  const ctrl = document.querySelector(
    `.fb-control[data-fb-surface="${_fbSel(surface)}"][data-fb-target="${_fbSel(targetId)}"]`);
  const pop = ctrl && ctrl.querySelector('.fb-popover');
  const comment = pop ? (pop.querySelector('.fb-comment').value || '') : '';
  const snapshot = ctrl ? (ctrl.dataset.fbSnapshot || '') : '';
  try {
    await API.submitFeedback({
      surface, target_id: String(targetId), session_id: sessionId || '',
      rating, comment, context_snapshot: snapshot,
    });
  } catch (e) {
    if (pop) {
      const send = pop.querySelector('.fb-send');
      if (send) { send.textContent = 'Fehler – erneut'; send.classList.add('fb-error'); }
    }
    return;
  }
  _fbMine[surface + '|' + String(targetId)] = rating;
  if (ctrl) {
    ctrl.querySelectorAll('.fb-thumb').forEach(b => b.classList.remove('fb-active'));
    const t = ctrl.querySelector(rating === 'down' ? '.fb-down' : '.fb-up');
    if (t) t.classList.add('fb-active');
    if (pop) pop.remove();
    _feedbackCelebrate(ctrl);
  }
}

// Restore the caller's previous ratings for one surface after a re-render,
// highlighting the chosen thumb. Order-independent: matches by data attribute.
async function feedbackHydrateState(surface, sessionId) {
  let rows;
  try { rows = (await API.myFeedback(surface, sessionId)).feedback || []; }
  catch (e) { return; }
  for (const r of rows) {
    _fbMine[r.surface + '|' + String(r.target_id)] = r.rating;
    const ctrl = document.querySelector(
      `.fb-control[data-fb-surface="${_fbSel(r.surface)}"][data-fb-target="${_fbSel(r.target_id)}"]`);
    if (!ctrl) continue;
    ctrl.querySelectorAll('.fb-thumb').forEach(b => b.classList.remove('fb-active'));
    const t = ctrl.querySelector(r.rating === 'down' ? '.fb-down' : '.fb-up');
    if (t) t.classList.add('fb-active');
  }
}

// Celebratory burst + "Danke!" — pure DOM/CSS, no library. Attached as a
// property of feedbackSubmit to avoid adding another top-level global.
function _feedbackCelebrate(ctrl) {
  const burst = document.createElement('span');
  burst.className = 'fb-thanks';
  burst.innerHTML = `<svg class="fb-check" viewBox="0 0 24 24" fill="none" `
    + `stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">`
    + `<path d="M5 13l4 4L19 7"/></svg><span class="fb-thanks-text">Danke!</span>`;
  const COLORS = ['#34d399', '#60a5fa', '#fbbf24', '#f472b6', '#a78bfa'];
  for (let i = 0; i < 10; i++) {
    const c = document.createElement('i');
    c.className = 'fb-confetti';
    c.style.setProperty('--fb-dx', (Math.random() * 80 - 40).toFixed(0) + 'px');
    c.style.setProperty('--fb-dy', (-30 - Math.random() * 50).toFixed(0) + 'px');
    c.style.setProperty('--fb-rot', (Math.random() * 360).toFixed(0) + 'deg');
    c.style.background = COLORS[i % COLORS.length];
    c.style.animationDelay = (Math.random() * 80).toFixed(0) + 'ms';
    burst.appendChild(c);
  }
  ctrl.appendChild(burst);
  setTimeout(() => burst.remove(), 1600);
}

// Admin: jump from the Feedback table to the rated content. Closes the settings
// modal first, then routes per surface to the existing open/detail function.
function feedbackJumpTo(surface, targetId, sessionId) {
  // Close the General Settings modal so the target view/modal is visible.
  document.querySelectorAll('.modal-overlay').forEach(o => o.remove());
  if (surface === 'chat') {
    if (!sessionId) { if (typeof showToast === 'function') showToast('Keine Sitzung hinterlegt', true); return; }
    // openSession switches to the chat view itself; agent defaults to 'main'.
    Promise.resolve(typeof openSession === 'function' && openSession(sessionId, 'main'))
      .then(() => _feedbackScrollToMessage(targetId));
  } else if (surface === 'workflow') {
    if (typeof wfShowHistoryDetail === 'function') wfShowHistoryDetail(targetId);  // self-contained modal
  } else if (surface === 'schedule') {
    if (typeof _schedViewRunDetail === 'function') _schedViewRunDetail(targetId);  // self-contained modal
  } else if (surface === 'translation') {
    if (typeof navigateTo === 'function') navigateTo('translation');
    // trJumpToHistoryEntry resolves the entry's type → right tab → scroll/flash,
    // and polls for the async-loaded history itself.
    if (typeof trJumpToHistoryEntry === 'function') trJumpToHistoryEntry(targetId);
  } else if (surface === 'classification') {
    if (typeof navigateTo === 'function') navigateTo('data');
    setTimeout(() => { if (typeof clsOpenScan === 'function') clsOpenScan(targetId); }, 250);
  }
}

// After a chat opens, scroll to the rated assistant turn (data-msg-id) + flash.
// Messages load async, so poll briefly for the element to appear.
function _feedbackScrollToMessage(msgId, tries) {
  tries = tries || 0;
  const el = document.querySelector(`.msg-turn-assistant[data-msg-id="${_fbSel(msgId)}"]`);
  if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); _feedbackFlash(el); return; }
  if (tries < 20) setTimeout(() => _feedbackScrollToMessage(msgId, tries + 1), 200);
}

function _feedbackFlash(el) {
  el.classList.add('fb-flash');
  setTimeout(() => el.classList.remove('fb-flash'), 1600);
}
