'use strict';

/* ═══════════════════════════════════════════════════════════
   Brainy 🧠 — der freundliche Helpdesk-Bot
   ───────────────────────────────────────────────────────────
   Schwebender Buddy im Chat + Mini-Chat-Modal. Spricht mit dem
   eigenen /v1/helpdesk-Endpoint (eigener System-Prompt, eigenes
   Model, exklusiver brain-agent-guide-Skill) — komplett getrennt
   vom Haupt-Chat, funktioniert auch während des Streamings.

   Globals: brainyState, brainyToggleBuddy, brainyBuddyEnabled,
   brainyOpen, brainyClose, brainySend, brainyRefreshBuddy.
   ═══════════════════════════════════════════════════════════ */

const brainyState = {
  open: false,
  streaming: false,
  sessionId: '',          // chat session Brainy is attached to
  abort: null,            // AbortController for the in-flight ask
};

function brainyBuddyEnabled() {
  // Default ON; the user can hide the floating buddy (then a composer
  // help button takes over). Persisted in localStorage.
  return localStorage.getItem('brainy-buddy-hidden') !== '1';
}

function brainyToggleBuddy() {
  const hidden = brainyBuddyEnabled();   // currently shown → will hide
  localStorage.setItem('brainy-buddy-hidden', hidden ? '1' : '0');
  brainyRefreshBuddy();
}

// Show/hide the floating buddy depending on the toggle + whether a chat is open.
function brainyRefreshBuddy() {
  const buddy = document.getElementById('brainy-buddy');
  if (!buddy) return;
  const inChat = (typeof state !== 'undefined') && state.currentView === 'chat'
                 && !!state.activeChat?.sessionId;
  buddy.style.display = (inChat && brainyBuddyEnabled()) ? 'flex' : 'none';
}

/* ── Open / close the modal ─────────────────────────────────── */

function brainyOpen() {
  const sid = state.activeChat?.sessionId || '';
  if (!sid) { return; }   // Brainy is session-bound
  brainyState.sessionId = sid;
  brainyState.open = true;

  let overlay = document.getElementById('brainy-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'brainy-overlay';
    overlay.className = 'modal-overlay brainy-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) brainyClose(); };
    overlay.innerHTML = `
      <div class="modal-content brainy-modal">
        <div class="brainy-header">
          <span class="brainy-avatar" aria-hidden="true">🧠</span>
          <div class="brainy-header-text">
            <div class="brainy-title">Brainy</div>
            <div class="brainy-subtitle">Dein freundlicher Helfer für brain-agent</div>
          </div>
          <button class="modal-close" onclick="brainyClose()" title="Schließen">&times;</button>
        </div>
        <div class="brainy-messages" id="brainy-messages"></div>
        <div class="brainy-input-row">
          <textarea id="brainy-input" class="brainy-input" rows="1"
                    placeholder="Frag Brainy etwas über brain-agent oder diese Sitzung…"
                    oninput="brainyAutogrow(this)"
                    onkeydown="brainyInputKey(event)"></textarea>
          <button class="brainy-send" id="brainy-send-btn" onclick="brainySend()" title="Senden">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          </button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  }
  overlay.style.display = 'flex';
  brainyLoadHistory();
  setTimeout(() => document.getElementById('brainy-input')?.focus(), 80);
}

function brainyClose() {
  brainyState.open = false;
  const overlay = document.getElementById('brainy-overlay');
  if (overlay) overlay.style.display = 'none';
  // Don't abort an in-flight answer — it persists server-side and finishes.
}

/* ── History restore ────────────────────────────────────────── */

async function brainyLoadHistory() {
  const box = document.getElementById('brainy-messages');
  if (!box) return;
  box.innerHTML = '';
  let msgs = [];
  try {
    const r = await API.get(`/v1/helpdesk/history?session_id=${encodeURIComponent(brainyState.sessionId)}`);
    msgs = r?.messages || [];
  } catch (e) { /* best-effort */ }

  if (!msgs.length) {
    brainyAppendBubble('assistant',
      'Hi! Ich bin **Brainy** 🧠 — dein Helfer hier im brain-agent. '
      + 'Frag mich alles: wie die App funktioniert, was in dieser Sitzung gerade passiert, '
      + 'oder wie du etwas erledigst. Womit kann ich helfen?');
    return;
  }
  for (const m of msgs) brainyAppendBubble(m.role, m.content || '');
  brainyScroll();
}

/* ── Rendering helpers ──────────────────────────────────────── */

function brainyAppendBubble(role, text) {
  const box = document.getElementById('brainy-messages');
  if (!box) return null;
  const wrap = document.createElement('div');
  wrap.className = `brainy-bubble brainy-${role === 'user' ? 'user' : 'bot'}`;
  if (role !== 'user') {
    const av = document.createElement('span');
    av.className = 'brainy-bubble-avatar';
    av.textContent = '🧠';
    wrap.appendChild(av);
  }
  const body = document.createElement('div');
  body.className = 'brainy-bubble-body';
  body.innerHTML = (role === 'user')
    ? esc(text)
    : (typeof renderMarkdown === 'function' ? renderMarkdown(text) : esc(text));
  wrap.appendChild(body);
  box.appendChild(wrap);
  brainyScroll();
  return body;
}

function brainyScroll() {
  const box = document.getElementById('brainy-messages');
  if (box) box.scrollTop = box.scrollHeight;
}

function brainyAutogrow(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

function brainyInputKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); brainySend(); }
}

/* ── Send a question, stream the answer ─────────────────────── */

async function brainySend() {
  if (brainyState.streaming) return;
  const input = document.getElementById('brainy-input');
  const text = (input?.value || '').trim();
  if (!text) return;
  input.value = '';
  brainyAutogrow(input);

  brainyAppendBubble('user', text);
  brainyState.streaming = true;
  const sendBtn = document.getElementById('brainy-send-btn');
  if (sendBtn) sendBtn.disabled = true;

  // Bot bubble with a typing indicator until the first delta arrives.
  const bodyEl = brainyAppendBubble('assistant', '');
  bodyEl.innerHTML = '<span class="brainy-typing"><span></span><span></span><span></span></span>';
  let acc = '';
  let firstDelta = true;

  brainyState.abort = new AbortController();
  try {
    const resp = await fetch(`${BASE_URL}/v1/helpdesk`, {
      method: 'POST',
      headers: API._headers(),
      body: JSON.stringify({ session_id: brainyState.sessionId, message: text }),
      signal: brainyState.abort.signal,
    });
    if (!resp.ok) {
      let msg = `Fehler ${resp.status}`;
      try { msg = (await resp.json()).error || msg; } catch (e) {}
      bodyEl.innerHTML = `<span class="brainy-error">${esc(msg)}</span>`;
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let evType = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();   // keep incomplete tail
      for (const line of lines) {
        if (line.startsWith('event:')) { evType = line.slice(6).trim(); continue; }
        if (line.startsWith('data:')) {
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let data = {};
          try { data = JSON.parse(payload); } catch (e) { continue; }
          if (evType === 'text_delta' && data.text) {
            if (firstDelta) { bodyEl.innerHTML = ''; firstDelta = false; }
            acc += data.text;
            bodyEl.innerHTML = (typeof renderMarkdown === 'function') ? renderMarkdown(acc) : esc(acc);
            brainyScroll();
          } else if (evType === 'tool_call') {
            if (firstDelta) bodyEl.innerHTML = brainyToolHint(data.name);
          } else if (evType === 'error') {
            if (firstDelta) { bodyEl.innerHTML = ''; firstDelta = false; }
            bodyEl.innerHTML += `<div class="brainy-error">${esc(data.message || 'Fehler')}</div>`;
          } else if (evType === 'done') {
            if (data.reply && (firstDelta || !acc)) {
              bodyEl.innerHTML = (typeof renderMarkdown === 'function') ? renderMarkdown(data.reply) : esc(data.reply);
            }
          }
        }
      }
    }
    if (firstDelta && !acc) {
      bodyEl.innerHTML = '<span class="brainy-error">Brainy hat keine Antwort geliefert.</span>';
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      bodyEl.innerHTML = `<span class="brainy-error">${esc(e.message || 'Verbindungsfehler')}</span>`;
    }
  } finally {
    brainyState.streaming = false;
    brainyState.abort = null;
    if (sendBtn) sendBtn.disabled = false;
    brainyScroll();
    document.getElementById('brainy-input')?.focus();
  }
}

function brainyToolHint(name) {
  const map = {
    use_skill: 'liest die brain-agent-Anleitung',
    helpdesk_session_info: 'schaut sich diese Sitzung an',
    helpdesk_user_context: 'schaut nach, wer du bist',
    helpdesk_user_activity: 'schaut sich deine bisherige Aktivität an',
    mempalace_query: 'durchsucht den Speicher',
    read_document: 'liest ein Dokument',
  };
  const what = map[name] || 'schaut etwas nach';
  return `<span class="brainy-tool-hint">🧠 Brainy ${esc(what)}…</span>`;
}
