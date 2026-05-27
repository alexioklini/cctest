'use strict';

/* ═══════════════════════════════════════════════════════════
   FEEDBACK WIDGET — 👍/👎 + threaded one-line conversation on any
   assistant response or result, across every surface (chat, brainy,
   workflow, schedule, translation, classification).

   The first 👍/👎 (with optional comment) creates a feedback row; from
   then on the same popover becomes a thread the user and an admin
   exchange one-line messages in (emoji welcome). An unread dot appears
   on the widget when the admin has replied since the user last looked.

   Globals exposed (load order: after api/state/utils, before surfaces):
     renderFeedbackControl(surface, targetId, sessionId, snapshot) -> HTML
     feedbackThumb(surface, targetId, sessionId, rating, btn)      -> onclick
     feedbackSubmit(surface, targetId, sessionId, rating)          -> submit
     feedbackHydrateState(surface, sessionId)                      -> restore
     feedbackJumpTo(...)                                           -> admin

   The control stores its context in data attributes on a wrapper
   (`.fb-control`) so a single set of handlers serves all surfaces.
   ═══════════════════════════════════════════════════════════ */

// In-memory cache of the caller's own feedback, keyed "surface|targetId" ->
// { rating:'up'|'down', id:<feedbackId>, unread:<n> }. Populated by
// feedbackHydrateState + feedbackSubmit, read on render/open.
const _fbMine = {};

// A small, friendly emoji palette for the one-line replies.
const _FB_EMOJI = ['👍', '👎', '🙏', '🎉', '😀', '😅', '😕', '😡', '❤️', '🔥', '💡', '✅', '❌', '🤔', '👀'];

// Escape a value for use inside an attribute-selector "...". Our ids are
// numeric/uuid/slug, but guard quotes + backslashes just in case.
function _fbSel(v) { return String(v).replace(/(["\\])/g, '\\$1'); }

function _fbKey(surface, targetId) { return surface + '|' + String(targetId); }

// Keep the (hover-only) chat actions bar pinned open while a popover is up.
// Marks both the control and its containing message turn so a CSS rule can
// override the hover-gated `display:none`. Surface-agnostic: rows that aren't
// hover-gated simply ignore the class.
function _feedbackMarkOpen(ctrl, on) {
  ctrl.classList.toggle('fb-open', on);
  ctrl.closest('.msg-turn')?.classList.toggle('fb-pop-open', on);
}

// The X button shared by both popovers. Clicking it closes the popover and
// releases the pinned-open state.
function _feedbackCloseBtn() {
  return `<button type="button" class="fb-close" title="Schließen" aria-label="Schließen">`
    + `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" `
    + `stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>`;
}

// Close + un-pin. Wired to the X and reused after a successful first comment.
function _feedbackClosePopover(pop) {
  const ctrl = pop && pop.closest('.fb-control');
  if (pop) pop.remove();
  if (ctrl) _feedbackMarkOpen(ctrl, false);
}

function renderFeedbackControl(surface, targetId, sessionId, snapshot) {
  const tid = String(targetId == null ? '' : targetId);
  if (!tid) return '';
  const entry = _fbMine[_fbKey(surface, tid)] || {};
  const mine = entry.rating || '';
  const a = (s) => (s === mine ? ' fb-active' : '');
  const dsnap = esc(String(snapshot || '').slice(0, 500));
  const unreadDot = entry.unread
    ? `<span class="fb-unread-dot" title="Neue Antwort vom Team"></span>` : '';
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
    + unreadDot
    + `</span>`;
}

// Open (or re-open, switching rating) the feedback popover. Before a rating
// exists it's the original comment box; once the user has rated (a feedback
// id is known) it's the conversation thread.
function feedbackThumb(surface, targetId, sessionId, rating, btn) {
  const ctrl = btn.closest('.fb-control');
  if (!ctrl) return;
  const entry = _fbMine[_fbKey(surface, targetId)] || {};
  // Already-rated → switching the thumb just re-rates (silent upsert); the
  // popover shows the thread either way.
  ctrl.querySelectorAll('.fb-thumb').forEach(b => b.classList.remove('fb-active'));
  btn.classList.add('fb-active');

  let pop = ctrl.querySelector('.fb-popover');
  if (pop) pop.remove();  // rebuild each open so thread state is fresh
  pop = document.createElement('div');
  pop.className = 'fb-popover';
  pop.dataset.rating = rating;
  ctrl.appendChild(pop);
  // Mark the surrounding controls "open" so the (hover-only) chat actions bar
  // stays visible while the popover is up — otherwise moving the mouse toward
  // the popover/X leaves the message turn and the whole bar (incl. popover)
  // vanishes. Cleared by _feedbackClosePopover.
  _feedbackMarkOpen(ctrl, true);

  if (entry.id) {
    // If the rating changed, persist the new rating quietly first.
    if (rating !== entry.rating) {
      feedbackSubmit(surface, targetId, sessionId, rating, { silent: true });
    }
    _feedbackRenderThread(pop, surface, targetId, sessionId, entry.id);
  } else {
    _feedbackRenderComposer(pop, surface, targetId, sessionId);
  }
}

// First-time composer: the comment box that creates the feedback row.
function _feedbackRenderComposer(pop, surface, targetId, sessionId) {
  pop.innerHTML = _feedbackCloseBtn()
    + `<textarea class="fb-comment" rows="2" maxlength="300" `
    + `placeholder="Optional: Was war gut bzw. nicht gut? (hilft uns sehr)"></textarea>`
    + `<div class="fb-popover-actions">`
    + `<button type="button" class="fb-cancel">Abbrechen</button>`
    + `<button type="button" class="fb-send">Senden</button>`
    + `</div>`;
  const ctrl = pop.closest('.fb-control');
  pop.querySelector('.fb-close').addEventListener('click', () => _feedbackClosePopover(pop));
  pop.querySelector('.fb-cancel').addEventListener('click', () => _feedbackClosePopover(pop));
  pop.querySelector('.fb-send').addEventListener('click', () => {
    const chosen = ctrl.querySelector('.fb-thumb.fb-active');
    const r = chosen && chosen.classList.contains('fb-down') ? 'down' : 'up';
    feedbackSubmit(surface, targetId, sessionId, r);
  });
  const ta = pop.querySelector('.fb-comment');
  setTimeout(() => ta && ta.focus(), 0);
}

// Thread view: scrollable history + one-line input + emoji picker. Loads the
// messages, then marks the thread read (clears the unread dot).
async function _feedbackRenderThread(pop, surface, targetId, sessionId, fbId) {
  pop.classList.add('fb-thread-pop');
  pop.innerHTML = _feedbackCloseBtn()
    + `<div class="fb-thread-log">Lädt…</div>`
    + `<div class="fb-thread-input-row">`
    + `<button type="button" class="fb-emoji-btn" title="Emoji">🙂</button>`
    + `<input type="text" class="fb-thread-input" maxlength="300" placeholder="Eine Zeile schreiben…">`
    + `<button type="button" class="fb-thread-send" title="Senden">➤</button>`
    + `</div>`
    + `<div class="fb-emoji-pop" hidden>${_FB_EMOJI.map(e =>
        `<button type="button" class="fb-emoji-pick">${e}</button>`).join('')}</div>`;

  const log = pop.querySelector('.fb-thread-log');
  const input = pop.querySelector('.fb-thread-input');
  const emojiPop = pop.querySelector('.fb-emoji-pop');
  pop.querySelector('.fb-close').addEventListener('click', () => _feedbackClosePopover(pop));

  const paint = (thread) => {
    log.innerHTML = (thread && thread.length)
      ? thread.map(_feedbackBubble).join('')
      : `<div class="fb-thread-empty">Schreib uns – das Team antwortet hier.</div>`;
    log.scrollTop = log.scrollHeight;
  };

  try {
    const res = await API.feedbackThread(fbId);
    paint(res.thread || []);
  } catch (e) { paint([]); }

  // Mark read + clear the unread dot (best-effort).
  const entry = _fbMine[_fbKey(surface, targetId)];
  if (entry) entry.unread = 0;
  pop.closest('.fb-control')?.querySelector('.fb-unread-dot')?.remove();
  API.feedbackSeen(fbId).catch(() => {});

  const send = async () => {
    const text = (input.value || '').trim();
    if (!text) return;
    input.value = '';
    try {
      const res = await API.feedbackMessage(fbId, text);
      paint(res.thread || []);
    } catch (e) {
      input.value = text;  // restore so the user doesn't lose it
    }
  };
  pop.querySelector('.fb-thread-send').addEventListener('click', send);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); send(); }
  });
  pop.querySelector('.fb-emoji-btn').addEventListener('click', () => {
    emojiPop.hidden = !emojiPop.hidden;
  });
  emojiPop.querySelectorAll('.fb-emoji-pick').forEach(b =>
    b.addEventListener('click', () => {
      input.value += b.textContent;
      emojiPop.hidden = true;
      input.focus();
    }));
  setTimeout(() => input.focus(), 0);
}

// One chat bubble. author_role 'user' → right, 'admin' → left ("Team").
function _feedbackBubble(m) {
  const mine = m.author_role !== 'admin';
  const who = mine ? '' : `<span class="fb-bubble-who">Team</span>`;
  return `<div class="fb-bubble ${mine ? 'fb-bubble-mine' : 'fb-bubble-them'}">`
    + `${who}<span class="fb-bubble-text">${esc(m.text || '')}</span></div>`;
}

async function feedbackSubmit(surface, targetId, sessionId, rating, opts) {
  opts = opts || {};
  const ctrl = document.querySelector(
    `.fb-control[data-fb-surface="${_fbSel(surface)}"][data-fb-target="${_fbSel(targetId)}"]`);
  const pop = ctrl && ctrl.querySelector('.fb-popover');
  const ta = pop && pop.querySelector('.fb-comment');
  const comment = ta ? (ta.value || '') : '';
  const snapshot = ctrl ? (ctrl.dataset.fbSnapshot || '') : '';
  let result;
  try {
    result = await API.submitFeedback({
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
  const key = _fbKey(surface, targetId);
  _fbMine[key] = Object.assign(_fbMine[key] || {}, {
    rating, id: (result && result.id) || (_fbMine[key] && _fbMine[key].id),
  });
  if (ctrl) {
    ctrl.querySelectorAll('.fb-thumb').forEach(b => b.classList.remove('fb-active'));
    const t = ctrl.querySelector(rating === 'down' ? '.fb-down' : '.fb-up');
    if (t) t.classList.add('fb-active');
  }
  // A silent re-rate (thumb flip while the thread is open) skips the
  // celebration + popover-swap — only the first explicit Send celebrates.
  if (opts.silent) return;
  if (ctrl) {
    _feedbackCelebrate(ctrl);
    // First comment created the row → flip the popover into the live thread so
    // the user can keep writing without re-clicking.
    if (pop && _fbMine[key].id) {
      pop.className = 'fb-popover';
      _feedbackRenderThread(pop, surface, targetId, sessionId, _fbMine[key].id);
    } else if (pop) {
      _feedbackClosePopover(pop);
    }
  }
}

// Restore the caller's previous ratings for one surface after a re-render,
// highlighting the chosen thumb + showing an unread dot when the team replied.
// Order-independent: matches by data attribute.
async function feedbackHydrateState(surface, sessionId) {
  let rows;
  try { rows = (await API.myFeedback(surface, sessionId)).feedback || []; }
  catch (e) { return; }
  for (const r of rows) {
    const key = _fbKey(r.surface, r.target_id);
    _fbMine[key] = { rating: r.rating, id: r.id, unread: r.unread || 0 };
    const ctrl = document.querySelector(
      `.fb-control[data-fb-surface="${_fbSel(r.surface)}"][data-fb-target="${_fbSel(r.target_id)}"]`);
    if (!ctrl) continue;
    ctrl.querySelectorAll('.fb-thumb').forEach(b => b.classList.remove('fb-active'));
    const t = ctrl.querySelector(r.rating === 'down' ? '.fb-down' : '.fb-up');
    if (t) t.classList.add('fb-active');
    if (r.unread && !ctrl.querySelector('.fb-unread-dot')) {
      const dot = document.createElement('span');
      dot.className = 'fb-unread-dot';
      dot.title = 'Neue Antwort vom Team';
      ctrl.appendChild(dot);
    }
  }
}

// Celebratory burst + "Danke!" — pure DOM/CSS, no library.
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
  document.querySelectorAll('.modal-overlay').forEach(o => o.remove());
  if (surface === 'chat') {
    if (!sessionId) { if (typeof showToast === 'function') showToast('Keine Sitzung hinterlegt', true); return; }
    Promise.resolve(typeof openSession === 'function' && openSession(sessionId, 'main'))
      .then(() => _feedbackScrollToMessage(targetId));
  } else if (surface === 'workflow') {
    if (typeof wfShowHistoryDetail === 'function') wfShowHistoryDetail(targetId);
  } else if (surface === 'schedule') {
    if (typeof _schedViewRunDetail === 'function') _schedViewRunDetail(targetId);
  } else if (surface === 'translation') {
    if (typeof navigateTo === 'function') navigateTo('translation');
    if (typeof trJumpToHistoryEntry === 'function') trJumpToHistoryEntry(targetId);
  } else if (surface === 'classification') {
    if (typeof navigateTo === 'function') navigateTo('data');
    setTimeout(() => { if (typeof clsOpenScan === 'function') clsOpenScan(targetId); }, 250);
  }
}

// After a chat opens, scroll to the rated assistant turn (data-msg-id) + flash.
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
