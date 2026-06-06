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
let _chatAudioLang = '';       // language pinned at start — stays fixed across all chunks

function _chatAudioStop() {
  _chatAudioStopped = true;
  _chatAudioQueue = [];
  _chatAudioLang = '';
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

async function _ttsBlobUrl(text, lang) {
  // Pin the voice via an explicit `lang` so it stays fixed across every chunk
  // (detected once at start). Only fall back to per-chunk auto_voice if no
  // language was resolved — otherwise a chunk with a foreign quote would flip
  // the voice mid-playback.
  const body = lang ? { text, lang } : { text, auto_voice: true };
  const resp = await fetch('/v1/translate/tts', {
    method: 'POST',
    headers: API._headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
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
    url = await _ttsBlobUrl(text, _chatAudioLang);
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

async function readMessageAloud(idx, btn) {
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
  // Detect the language ONCE on the full text and pin it for every chunk, so
  // the voice can't switch mid-playback (a foreign quote in a later chunk must
  // not flip the voice). Best-effort: on failure we fall back to per-chunk
  // auto_voice.
  _chatAudioLang = '';
  try {
    const det = await API.post('/v1/translate/detect', { text: speech });
    if (_chatAudioStopped) return;   // user toggled off during detection
    _chatAudioLang = (det && det.lang ? String(det.lang) : '').slice(0, 2);
  } catch (_) { /* fall back to auto_voice per chunk */ }
  _playChatQueue();
}

// ─── generate a podcast (Audio Overview) from this chat ───────────────────────

// In-flight podcast generation, so a second click on the same button cancels it.
let _chatPodcastBtn = null;
let _chatPodcastAbort = null;

function _chatPodcastStop() {
  if (_chatPodcastAbort) { try { _chatPodcastAbort.abort(); } catch (_) {} _chatPodcastAbort = null; }
  if (_chatPodcastBtn) { _chatPodcastBtn.dataset.busy = '0'; _chatPodcastBtn.classList.remove('msg-action-generating'); _chatPodcastBtn = null; }
}

async function generateChatPodcast(btn) {
  // Toggle off if this same button is already generating.
  if (btn && btn.dataset.busy === '1') { _chatPodcastStop(); showToast('Podcast-Erstellung abgebrochen'); return; }
  const chat = state.activeChat;
  if (!chat || !chat.sessionId) { showToast('Kein aktiver Chat', true); return; }
  _chatPodcastStop();             // stop any other in-flight generation first
  const ctrl = new AbortController();
  _chatPodcastAbort = ctrl;
  _chatPodcastBtn = btn;
  if (btn) { btn.dataset.busy = '1'; btn.classList.add('msg-action-generating'); }
  showToast('Podcast wird erstellt — das dauert ~1 Minute… (nochmal klicken zum Abbrechen)');
  try {
    const resp = await fetch(`/v1/sessions/${encodeURIComponent(chat.sessionId)}/audio-overview`, {
      method: 'POST',
      headers: API._headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ length: 'std' }),
      signal: ctrl.signal,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    showToast('Podcast fertig — wird abgespielt');
    if (typeof refreshRightPanelContent === 'function') { try { refreshRightPanelContent(); } catch (_) {} }
    if (data.artifact_id) _openChatPodcastModal(data.artifact_id, data.audio_file);
  } catch (e) {
    if (e && e.name === 'AbortError') return;   // user cancelled — already toasted
    showToast('Podcast fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    // Only clear if this call still owns the button (a later click may have taken over).
    if (_chatPodcastBtn === btn) { _chatPodcastAbort = null; _chatPodcastStop(); }
  }
}

// ─── voice manager (clone / list / delete custom TTS voices) ──────────────────

async function openVoiceManager() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-content" style="max-width:640px;width:92vw;max-height:88vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">🎙️ TTS-Stimmen</span>
      <button class="modal-close" style="margin-left:auto" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div style="padding:14px 16px;overflow:auto">
      <div style="font-weight:600;font-size:13px;margin-bottom:6px">Neue Stimme klonen</div>
      <div style="font-size:11px;color:var(--text-400);margin-bottom:8px">Eine Audioprobe (≥3 s, klare Sprache) der Zielstimme hochladen. Die Sprache der Probe sollte der Zielsprache entsprechen.</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px">
        <input id="vm-name" type="text" placeholder="Name (z. B. Klaus DE)" class="form-input" style="font-size:12px;flex:1 1 160px">
        <select id="vm-lang" class="form-select" style="font-size:12px">
          <option value="de">Deutsch</option><option value="fr">Französisch</option>
          <option value="es">Spanisch</option><option value="it">Italienisch</option>
          <option value="nl">Niederländisch</option><option value="pt">Portugiesisch</option>
          <option value="hi">Hindi</option><option value="ar">Arabisch</option>
          <option value="en">Englisch</option>
        </select>
        <select id="vm-gender" class="form-select" style="font-size:12px">
          <option value="male">männlich</option><option value="female">weiblich</option>
        </select>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:14px">
        <input id="vm-file" type="file" accept="audio/*" style="font-size:12px;flex:1">
        <button class="btn-primary" style="font-size:12px;padding:4px 12px" onclick="submitCloneVoice(this)">Klonen</button>
      </div>
      <div style="font-weight:600;font-size:13px;margin-bottom:6px">Vorhandene Stimmen</div>
      <div id="vm-list" style="font-size:12px;color:var(--text-300)">Lädt…</div>
    </div>
  </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  _refreshVoiceList(overlay);
}

async function _refreshVoiceList(root) {
  const list = (root || document).querySelector('#vm-list');
  if (!list) return;
  try {
    const data = await API.get('/v1/translate/tts/voices');
    const voices = (data && data.voices) || [];
    list.innerHTML = voices.map(v => {
      const langs = (v.languages || []).join(', ');
      const id = v.id || v.slug || '';
      const custom = v.user_id ? '' : ' <span style="color:var(--text-500)">(Standard)</span>';
      const delBtn = v.user_id
        ? `<button class="btn-secondary" style="font-size:11px;padding:2px 8px" onclick="deleteVoice('${esc(id)}', this)">Löschen</button>`
        : '';
      return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border-100)">
        <span style="flex:1">${esc(v.name || id)} <span style="color:var(--text-500)">· ${esc(langs)} · ${esc(v.gender || '')}</span>${custom}</span>
        ${delBtn}</div>`;
    }).join('') || '<div style="color:var(--text-400)">Keine Stimmen.</div>';
  } catch (e) {
    list.innerHTML = `<div style="color:var(--error)">Konnte Stimmen nicht laden: ${esc(e.message || e)}</div>`;
  }
}

async function submitCloneVoice(btn) {
  const root = btn.closest('.modal-content');
  const name = root.querySelector('#vm-name').value.trim();
  const lang = root.querySelector('#vm-lang').value;
  const gender = root.querySelector('#vm-gender').value;
  const fileEl = root.querySelector('#vm-file');
  const file = fileEl.files && fileEl.files[0];
  if (!name || !file) { showToast('Name und Audioprobe erforderlich', true); return; }
  btn.disabled = true; btn.textContent = 'Klont…';
  try {
    const b64 = await _fileToBase64(file);
    const data = await API.post('/v1/translate/tts/voices', {
      name, sample_audio_b64: b64, sample_filename: file.name,
      languages: [lang], gender,
    });
    if (data && data.error) throw new Error(data.error);
    showToast('Stimme geklont — wird ab sofort für ' + lang + ' verwendet');
    root.querySelector('#vm-name').value = ''; fileEl.value = '';
    _refreshVoiceList(root.closest('.modal-overlay'));
  } catch (e) {
    showToast('Klonen fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    btn.disabled = false; btn.textContent = 'Klonen';
  }
}

async function deleteVoice(voiceId, btn) {
  if (!confirm('Diese Stimme löschen?')) return;
  btn.disabled = true;
  try {
    await API.del(`/v1/translate/tts/voices/${encodeURIComponent(voiceId)}`);
    showToast('Stimme gelöscht');
    _refreshVoiceList(btn.closest('.modal-overlay'));
  } catch (e) {
    showToast('Löschen fehlgeschlagen: ' + (e.message || e), true);
    btn.disabled = false;
  }
}

function _fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] || '');  // strip data: prefix
    r.onerror = reject;
    r.readAsDataURL(file);
  });
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
