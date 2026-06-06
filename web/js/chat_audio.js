// chat_audio.js — spoken audio in the chat view: (1) read an assistant reply
// aloud via TTS (chunked, sequential playback), (2) generate a two-host Audio
// Overview podcast from the current chat. Globals only (no modules); loaded
// after chat_send.js, before init.js.
//
// AUTH NOTE: artifact-download + TTS endpoints require the Bearer header, so we
// NEVER put a bare download URL in <audio src> (that 401s). We fetch the bytes
// with API._headers() and play a blob URL instead — the same pattern the
// Translation tab uses.

// ─── shared audio playback state (one clip at a time across both features) ────
let _chatAudioEl = null;
let _chatAudioBtn = null;
let _chatAudioQueue = [];      // pending TTS chunk texts (read-aloud)
let _chatAudioStopped = false;

function _chatAudioStop() {
  _chatAudioStopped = true;
  _chatAudioQueue = [];
  if (_chatAudioEl) { try { _chatAudioEl.pause(); } catch (_) {} _chatAudioEl = null; }
  if (_chatAudioBtn) { _chatAudioBtn.classList.remove('msg-action-active'); _chatAudioBtn = null; }
}

// ─── read an assistant reply aloud ────────────────────────────────────────────

// Strip markdown to plain speech text — we don't want the voice reading '**',
// code fences, citation chips, or URL noise.
function _stripMarkdownForSpeech(md) {
  let t = String(md || '');
  t = t.replace(/```[\s\S]*?```/g, ' ');              // fenced code blocks
  t = t.replace(/`([^`]+)`/g, '$1');                  // inline code
  t = t.replace(/!\[[^\]]*\]\([^)]*\)/g, ' ');        // images
  t = t.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');      // links → link text
  t = t.replace(/\[\d+\]/g, ' ');                     // [n] citation chips
  t = t.replace(/^>\s?/gm, '');                       // blockquote markers
  t = t.replace(/^#{1,6}\s+/gm, '');                  // headings
  t = t.replace(/(\*\*|__|\*|_|~~)/g, '');            // emphasis markers
  t = t.replace(/^\s*[-*+]\s+/gm, '');                // bullet markers
  t = t.replace(/\|/g, ' ');                          // table pipes
  t = t.replace(/\n{2,}/g, '. ').replace(/\s+/g, ' '); // collapse whitespace
  return t.trim();
}

// Split into <=~3k-char chunks on sentence boundaries so each TTS call stays
// within provider limits and playback can start sooner.
function _chunkForTts(text, maxLen) {
  const cap = maxLen || 3000;
  const out = [];
  let buf = '';
  for (const sentence of text.split(/(?<=[.!?])\s+/)) {
    if ((buf + ' ' + sentence).length > cap && buf) { out.push(buf.trim()); buf = ''; }
    // A single sentence longer than the cap: hard-split it.
    if (sentence.length > cap) {
      for (let i = 0; i < sentence.length; i += cap) out.push(sentence.slice(i, i + cap));
    } else {
      buf += ' ' + sentence;
    }
  }
  if (buf.trim()) out.push(buf.trim());
  return out;
}

async function _ttsBlobUrl(text) {
  const resp = await fetch('/v1/translate/tts', {
    method: 'POST',
    headers: API._headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  return URL.createObjectURL(await resp.blob());
}

// Play the queued chunks one after another (fetch chunk N+1 while N plays).
async function _playChatQueue() {
  if (_chatAudioStopped || !_chatAudioQueue.length) {
    if (!_chatAudioStopped && _chatAudioBtn) _chatAudioBtn.classList.remove('msg-action-active');
    _chatAudioBtn = null;
    return;
  }
  const text = _chatAudioQueue.shift();
  let url;
  try {
    url = await _ttsBlobUrl(text);
  } catch (e) {
    showToast('Vorlesen fehlgeschlagen: ' + (e.message || e), true);
    _chatAudioStop();
    return;
  }
  if (_chatAudioStopped) { URL.revokeObjectURL(url); return; }
  const audio = new Audio(url);
  _chatAudioEl = audio;
  audio.onended = () => { URL.revokeObjectURL(url); _chatAudioEl = null; _playChatQueue(); };
  audio.onerror = () => { URL.revokeObjectURL(url); _chatAudioStop(); };
  audio.play().catch(() => { /* autoplay/gesture issues — surface quietly */ });
}

function readMessageAloud(idx, btn) {
  // Toggle off if this same button is already playing.
  if (_chatAudioBtn === btn) { _chatAudioStop(); return; }
  _chatAudioStop();               // stop anything else first
  _chatAudioStopped = false;
  const chat = state.activeChat;
  const raw = chat && chat.messages[idx] && chat.messages[idx].content;
  const speech = _stripMarkdownForSpeech(typeof raw === 'string' ? raw : '');
  if (!speech) { showToast('Nichts zum Vorlesen', true); return; }
  _chatAudioQueue = _chunkForTts(speech, 3000);
  _chatAudioBtn = btn;
  if (btn) btn.classList.add('msg-action-active');
  _playChatQueue();
}

// ─── generate a podcast (Audio Overview) from this chat ───────────────────────

async function generateChatPodcast(btn) {
  const chat = state.activeChat;
  if (!chat || !chat.sessionId) { showToast('Kein aktiver Chat', true); return; }
  if (btn && btn.dataset.busy === '1') return;
  if (btn) { btn.dataset.busy = '1'; btn.classList.add('msg-action-active'); }
  showToast('Podcast wird erstellt — das dauert ~1 Minute…');
  try {
    const resp = await fetch(`/v1/sessions/${encodeURIComponent(chat.sessionId)}/audio-overview`, {
      method: 'POST',
      headers: API._headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ length: 'std' }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    showToast('Podcast fertig — wird abgespielt');
    if (typeof refreshRightPanelContent === 'function') { try { refreshRightPanelContent(); } catch (_) {} }
    if (data.artifact_id) _openChatPodcastModal(data.artifact_id, data.audio_file);
  } catch (e) {
    showToast('Podcast fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    if (btn) { btn.dataset.busy = '0'; btn.classList.remove('msg-action-active'); }
  }
}

// Open a small modal with an <audio> player fed by an auth'd blob (NOT a bare
// download URL — that 401s).
async function _openChatPodcastModal(artifactId, filename) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:560px;width:90vw">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">🎧 Podcast aus diesem Chat</span>
      <button class="modal-close" style="margin-left:auto" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:14px;align-items:center;padding:22px 16px">
      <div style="font-size:42px">🎧</div>
      <div style="font-size:12px;color:var(--text-400);text-align:center">Zwei-Host-Podcast (englisch) aus diesem Gespräch.</div>
      <div class="chat-podcast-audio-mount" style="width:100%;display:flex;justify-content:center">Lädt…</div>
    </div>
  </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  const mount = overlay.querySelector('.chat-podcast-audio-mount');
  try {
    const resp = await fetch(API.getArtifactDownloadUrl(artifactId), { headers: API._headers() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const url = URL.createObjectURL(await resp.blob());
    mount.innerHTML = `<audio controls autoplay preload="metadata" style="width:100%" src="${url}"></audio>`;
  } catch (e) {
    mount.innerHTML = `<div style="color:var(--error)">Audio konnte nicht geladen werden: ${esc(e.message || e)}</div>`;
  }
}
