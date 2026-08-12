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

// Button shows two states: pulsating while audio is being generated/fetched,
// solid-lit once it's actually playing.
function _chatAudioBtnState(state) {
  if (!_chatAudioBtn) return;
  _chatAudioBtn.classList.toggle('msg-action-generating', state === 'generating');
  _chatAudioBtn.classList.toggle('msg-action-active', state === 'playing');
}

function _chatAudioStop() {
  _chatAudioStopped = true;
  _chatAudioQueue = [];
  _chatAudioLang = '';
  if (_chatAudioEl) { try { _chatAudioEl.pause(); } catch (_) {} _chatAudioEl = null; }
  try { window.speechSynthesis?.cancel(); } catch (_) {}
  if (_chatAudioBtn) {
    _chatAudioBtn.classList.remove('msg-action-active', 'msg-action-generating');
    _chatAudioBtn = null;
  }
}

// ─── audio engines (admin choice: server model vs. browser-native) ────────────

// The Tools tab lets the admin pick 'server' or 'browser' per direction
// (transcribe_audio.engine / text_to_speech.engine). Served alongside
// /v1/translate/stt-models; cached per page load. Browser engines only apply
// to mic/speech features — audio FILES always go through the server model.
let _audioEnginesCache = null;
async function fetchAudioEngines() {
  if (_audioEnginesCache) return _audioEnginesCache;
  try {
    const data = await API.get('/v1/translate/stt-models');
    _audioEnginesCache = (data && data.engines) || {};
  } catch (_) {
    _audioEnginesCache = {};
  }
  if (!_audioEnginesCache.stt) _audioEnginesCache.stt = 'server';
  if (!_audioEnginesCache.tts) _audioEnginesCache.tts = 'server';
  return _audioEnginesCache;
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
    if (!_chatAudioStopped && _chatAudioBtn) {
      _chatAudioBtn.classList.remove('msg-action-active', 'msg-action-generating');
    }
    _chatAudioBtn = null;
    return;
  }
  const text = _chatAudioQueue.shift();
  // Fetching/synthesizing this chunk — pulsate until it actually plays.
  _chatAudioBtnState('generating');
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
  // Solid-lit once playback truly starts (onplaying), not merely when play()
  // resolves — keeps the pulsate visible through any buffering.
  audio.onplaying = () => { _chatAudioBtnState('playing'); };
  audio.play().catch(() => { /* autoplay/gesture issues — surface quietly */ });
}

// Browser-native playback (engine 'browser'): OS voices via speechSynthesis —
// no server call, no cost, works offline. Same queue/stop/button state as the
// server path; speechSynthesis queues utterances natively but we feed them one
// at a time so stop() stays a single cancel().
function _speakChatQueueBrowser() {
  if (_chatAudioStopped || !_chatAudioQueue.length) {
    if (!_chatAudioStopped && _chatAudioBtn) {
      _chatAudioBtn.classList.remove('msg-action-active', 'msg-action-generating');
    }
    if (!_chatAudioStopped) _chatAudioBtn = null;
    return;
  }
  const u = new window.SpeechSynthesisUtterance(_chatAudioQueue.shift());
  if (_chatAudioLang) u.lang = _chatAudioLang;
  u.onstart = () => { if (!_chatAudioStopped) _chatAudioBtnState('playing'); };
  u.onend = () => { _speakChatQueueBrowser(); };
  // cancel() fires onerror ('interrupted') in some engines — _chatAudioStop is
  // idempotent, so routing every error through it is safe.
  u.onerror = () => { _chatAudioStop(); };
  window.speechSynthesis.speak(u);
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
  _chatAudioBtnState('generating');   // pulsate while we detect lang + fetch audio
  const engines = await fetchAudioEngines();
  if (_chatAudioStopped) return;      // user toggled off during the engines fetch
  const useBrowserTts = engines.tts === 'browser' && 'speechSynthesis' in window;
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
  if (useBrowserTts) { _speakChatQueueBrowser(); } else { _playChatQueue(); }
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
      // Server reuses the last podcast if the chat content hasn't changed.
      body: JSON.stringify({ length: 'std' }),
      signal: ctrl.signal,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    showToast(data.cached ? 'Podcast (unverändert) — wird abgespielt' : 'Podcast fertig — wird abgespielt');
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

// ─── dictation (composer mic button) ──────────────────────────────────────────
//
// Two backends behind the one button, chosen by the admin engine setting:
//  - 'server':  record raw mic samples, encode ONE wav on stop (encoder shared
//    with translation_live.js), POST /v1/translate/transcribe (whisper/voxtral;
//    with local whisper the audio never leaves the house).
//  - 'browser': webkitSpeechRecognition — recognized text streams into the
//    composer while speaking. NB: Chrome/Edge run this via Google/Microsoft
//    cloud; the admin help text says so. Unsupported (Firefox) → server path.

let _dictState = null;   // {btn, input, mode, rec?, ctx?, proc?, stream?, chunks?, sampleRate?, finalized?}

function _dictSetBtn(btn, active, busy) {
  btn.classList.toggle('dictating', !!active);
  btn.classList.toggle('msg-action-generating', !!busy);
  btn.title = active ? 'Diktat läuft — klicken zum Beenden'
    : busy ? 'Wird transkribiert…' : 'Diktieren (Spracheingabe)';
}

function _dictInsert(text) {
  const s = _dictState;
  if (!s || !s.input || !text) return;
  const sep = s.input.value && !/\s$/.test(s.input.value) ? ' ' : '';
  s.input.value += sep + text;
  try { autoResizeInput(s.input); updateSendButton(); } catch (_) {}
}

async function toggleDictation(btn) {
  if (_dictState) { _stopDictation(); return; }
  const box = btn.closest('[data-composer-box]');
  const input = box && box.querySelector('.composer-input');
  if (!input) { showToast('Kein Eingabefeld gefunden', true); return; }
  const engines = await fetchAudioEngines();
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const wantBrowser = engines.stt === 'browser';
  if (wantBrowser && SR) {
    _dictState = { btn, input, mode: 'browser' };
    _startDictationBrowser(SR);
  } else {
    if (wantBrowser && !SR) showToast('Browser-Spracherkennung nicht verfügbar — Server-Transkription wird verwendet');
    _dictState = { btn, input, mode: 'server' };
    _startDictationServer();
  }
}

function _startDictationBrowser(SR) {
  const s = _dictState;
  const rec = new SR();
  rec.continuous = true;
  rec.interimResults = false;
  rec.lang = navigator.language || 'de-DE';
  rec.onresult = (ev) => {
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      if (ev.results[i].isFinal) _dictInsert(ev.results[i][0].transcript.trim());
    }
  };
  rec.onerror = (ev) => {
    // 'no-speech' fires on silence — harmless, onend restarts. Real errors end
    // the dictation with a toast (e.g. 'not-allowed' = mic permission denied).
    if (ev.error && ev.error !== 'no-speech' && ev.error !== 'aborted') {
      showToast('Spracherkennung fehlgeschlagen: ' + ev.error, true);
      _stopDictation();
    }
  };
  // Chrome ends recognition after pauses on its own — keep going until the
  // user explicitly stops.
  rec.onend = () => { if (_dictState === s) { try { rec.start(); } catch (_) {} } };
  s.rec = rec;
  try { rec.start(); } catch (e) { showToast('Start fehlgeschlagen: ' + (e.message || e), true); _dictState = null; return; }
  _dictSetBtn(s.btn, true, false);
}

async function _startDictationServer() {
  const s = _dictState;
  if (!navigator.mediaDevices?.getUserMedia) {
    // Mic API exists only in secure contexts (HTTPS or localhost).
    showToast(window.isSecureContext
      ? 'Mikrofon wird in diesem Browser nicht unterstützt.'
      : 'Mikrofonzugriff erfordert eine sichere Verbindung — bitte die Seite über https:// oder http://localhost öffnen (nicht über die IP-Adresse).', true);
    _dictState = null;
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    showToast('Mikrofonzugriff verweigert: ' + (e.message || e), true);
    _dictState = null;
    return;
  }
  if (_dictState !== s) { stream.getTracks().forEach(t => t.stop()); return; }
  // Same capture path as the live translation (ScriptProcessor → Float32
  // samples), but ONE take: everything is buffered and encoded on stop.
  let ctx;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
  } catch (e) {
    stream.getTracks().forEach(t => t.stop());
    showToast('AudioContext fehlgeschlagen: ' + (e.message || e), true);
    _dictState = null;
    return;
  }
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  s.stream = stream; s.ctx = ctx; s.proc = proc;
  s.chunks = []; s.sampleRate = ctx.sampleRate;
  const MAX_S = 120;   // hard cap so a forgotten mic doesn't buffer forever
  let total = 0;
  proc.onaudioprocess = (ev) => {
    if (_dictState !== s) return;
    const d = ev.inputBuffer.getChannelData(0);
    s.chunks.push(new Float32Array(d));
    total += d.length;
    if (total > s.sampleRate * MAX_S) {
      showToast(`Diktat nach ${MAX_S} s automatisch beendet`);
      _stopDictation();
    }
  };
  source.connect(proc);
  proc.connect(ctx.destination);
  _dictSetBtn(s.btn, true, false);
}

async function _stopDictation() {
  const s = _dictState;
  if (!s) return;
  _dictState = null;
  if (s.mode === 'browser') {
    try { s.rec && s.rec.stop(); } catch (_) {}
    _dictSetBtn(s.btn, false, false);
    return;
  }
  // Server path: tear down capture, encode one WAV, transcribe.
  try { s.proc && s.proc.disconnect(); } catch (_) {}
  try { s.ctx && s.ctx.close(); } catch (_) {}
  try { s.stream && s.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
  const total = (s.chunks || []).reduce((n, c) => n + c.length, 0);
  if (total < (s.sampleRate || 16000) * 0.4) {   // <0.4s — nothing worth sending
    _dictSetBtn(s.btn, false, false);
    return;
  }
  const samples = new Float32Array(total);
  let off = 0;
  for (const c of s.chunks) { samples.set(c, off); off += c.length; }
  _dictSetBtn(s.btn, false, true);
  try {
    const wav = trEncodeWav(samples, s.sampleRate);   // shared encoder (translation_live.js)
    const fd = new FormData();
    fd.append('chunk', wav, 'dictation.wav');
    fd.append('mime', 'audio/wav');
    const resp = await fetch('/v1/translate/transcribe', {
      method: 'POST', headers: API._headers(), body: fd,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    _dictState = s;          // _dictInsert reads the input off the state
    _dictInsert((data.text || '').trim());
    _dictState = null;
    if (!(data.text || '').trim()) showToast('Keine Sprache erkannt');
  } catch (e) {
    showToast('Transkription fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    _dictSetBtn(s.btn, false, false);
  }
}
