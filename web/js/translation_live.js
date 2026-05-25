// translation_live.js — live mic translate-as-you-go (recording, VAD, WAV encode, live SSE, live segment render). Split from translation.js (Tier F Phase 4). Global <script>, no modules.

/* ═══════════════════════════════════════════════════════════
   LIVE MICROPHONE TAB (C2)
   Browser MediaRecorder → server-side rolling buffer → Voxtral
   per-chunk → translate-as-you-go. No WebSocket — we use POST
   chunks + an SSE event stream for results (same plumbing
   conventions as the rest of the app).
   ═══════════════════════════════════════════════════════════ */

const trLiveState = {
  recording: false,
  sessionId: '',
  recorder: null,
  stream: null,
  startedAt: 0,
  elapsedTimer: null,
  abortSse: null,
  segments: [],          // finalized segments {start,end,text,translation,detectedLang}
  partialIndex: -1,      // DOM/state index of the in-progress segment, if any
  // Auto-TTS for translated segments. Played sequentially so utterances don't
  // overlap; mic is muted during playback so we don't transcribe our own audio.
  ttsEnabled: false,
  ttsQueue: [],          // pending {index, text, lang}
  ttsPlaying: false,
  ttsAudio: null,
  ttsSpokenIdx: new Set(), // segment indices already queued/spoken (dedup re-renders)
};

// Normalize 'en-US' / 'EN_us' → 'en'. Same logic as backend _norm_lang.
function _trNormLang(s) {
  if (!s) return '';
  s = String(s).trim().toLowerCase();
  const i = s.search(/[-_]/);
  return i >= 0 ? s.slice(0, i) : s;
}

function trLiveOnTtsToggleChange() {
  const el = document.getElementById('tr-live-tts-toggle');
  trLiveState.ttsEnabled = !!(el && el.checked);
  try { localStorage.setItem('tr-live-tts', trLiveState.ttsEnabled ? '1' : '0'); } catch (_) {}
  // Disabling mid-stream: stop current playback and drop the queue. Restore mic.
  if (!trLiveState.ttsEnabled) {
    trLiveTtsStop();
  }
}

// Restore toggle state on first script load.
(function _trLiveTtsRestore() {
  try {
    const saved = localStorage.getItem('tr-live-tts');
    if (saved === '1') {
      const el = document.getElementById('tr-live-tts-toggle');
      if (el) { el.checked = true; trLiveState.ttsEnabled = true; }
    }
  } catch (_) {}
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      try {
        const saved = localStorage.getItem('tr-live-tts');
        const el = document.getElementById('tr-live-tts-toggle');
        if (saved === '1' && el) { el.checked = true; trLiveState.ttsEnabled = true; }
      } catch (_) {}
    }, { once: true });
  }
})();

function _trLiveSetMicMuted(muted) {
  // Toggle the MediaStreamTrack — the VAD recorder still runs but receives
  // silence, so it won't flush a chunk full of TTS playback echo. Cheaper
  // than tearing the recorder down.
  const stream = trLiveState.stream;
  if (!stream) return;
  for (const t of stream.getTracks()) {
    try { t.enabled = !muted; } catch (_) {}
  }
}

function trLiveTtsStop() {
  if (trLiveState.ttsAudio) {
    try { trLiveState.ttsAudio.pause(); } catch (_) {}
    trLiveState.ttsAudio = null;
  }
  trLiveState.ttsQueue = [];
  trLiveState.ttsPlaying = false;
  // Always restore mic when we tear playback down.
  if (trLiveState.recording) _trLiveSetMicMuted(false);
}

function trLiveMaybeQueueTts(seg, idx) {
  // Decide whether to speak this translated segment, then enqueue.
  if (!trLiveState.ttsEnabled) return;
  if (!trLiveState.recording) return;          // post-stop translation: skip
  if (!seg || !seg.translation) return;
  if (trLiveState.ttsSpokenIdx.has(idx)) return;
  const targetLang = _trNormLang(trState.targetLang);
  if (!targetLang) return;                     // transcribe-only mode
  const detected = _trNormLang(seg.detectedLang);
  // Skip when speaker's source language matches the target — meeting case:
  // English-only listener doesn't need TTS for their own English speech, only
  // for the German segments that get translated.
  if (detected && detected === targetLang) return;
  trLiveState.ttsSpokenIdx.add(idx);
  trLiveState.ttsQueue.push({ index: idx, text: seg.translation, lang: targetLang });
  _trLiveTtsDrain();
}

async function _trLiveTtsDrain() {
  if (trLiveState.ttsPlaying) return;
  const job = trLiveState.ttsQueue.shift();
  if (!job) return;
  trLiveState.ttsPlaying = true;
  _trLiveSetMicMuted(true);
  let blobUrl = '';
  try {
    blobUrl = await _trTtsFetch(job.text, job.lang);
    if (!trLiveState.ttsEnabled || !trLiveState.recording) {
      // Toggled off / stopped while fetching — bail.
      try { URL.revokeObjectURL(blobUrl); } catch (_) {}
      trLiveState.ttsPlaying = false;
      _trLiveSetMicMuted(false);
      return;
    }
    const audio = new Audio(blobUrl);
    trLiveState.ttsAudio = audio;
    await new Promise((resolve) => {
      audio.onended = resolve;
      audio.onerror = resolve;
      audio.play().catch(resolve);
    });
    try { URL.revokeObjectURL(blobUrl); } catch (_) {}
  } catch (_) {
    // Silent: a single failed TTS shouldn't kill the queue.
    if (blobUrl) { try { URL.revokeObjectURL(blobUrl); } catch (_) {} }
  } finally {
    trLiveState.ttsAudio = null;
    trLiveState.ttsPlaying = false;
  }
  // Drain next, or restore mic if the queue is empty.
  if (trLiveState.ttsQueue.length && trLiveState.ttsEnabled && trLiveState.recording) {
    _trLiveTtsDrain();
  } else {
    _trLiveSetMicMuted(false);
  }
}

function trLiveStatus(msg, isError = false) {
  const el = document.getElementById('tr-live-status');
  if (!el) return;
  el.textContent = msg || '';
  el.classList.toggle('error', !!isError);
}

function trLiveToggle() {
  if (trLiveState.recording) {
    trLiveStop();
  } else {
    trLiveStart();
  }
}

async function trLiveStart() {
  // Browser support gate.
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    trLiveStatus('Mikrofon wird in diesem Browser nicht unterstützt.', true);
    return;
  }
  const mode = document.getElementById('tr-live-mode')?.value || 'translate';
  if (mode === 'translate' && !trState.targetLang) {
    trLiveStatus('Wählen Sie eine Zielsprache.', true);
    return;
  }

  trLiveStatus('Mikrofonzugriff wird angefragt…');
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, sampleRate: 16000, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    trLiveStatus(`Mikrofonzugriff verweigert: ${e.message}`, true);
    return;
  }

  // Open a server session.
  const body = {
    target_lang: mode === 'translate' ? trState.targetLang : '',
    source_lang: (trState.sourceLangManual && trState.sourceLang) ? trState.sourceLang : '',
    glossary: trState.glossarySlug || '',
    model: trState.model || '',
  };
  let res;
  try {
    res = await fetch('/v1/translate/live/start', {
      method: 'POST',
      headers: { ...trAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    stream.getTracks().forEach(t => t.stop());
    trLiveStatus(`Session-Start fehlgeschlagen: ${e.message}`, true);
    return;
  }
  const session = await res.json();
  trLiveState.sessionId = session.id;
  trLiveState.stream = stream;
  trLiveState.recording = true;
  trLiveState.segments = [];
  trLiveState.partialIndex = -1;
  trLiveState.ttsSpokenIdx = new Set();
  trLiveState.ttsQueue = [];
  trLiveState.startedAt = Date.now();
  trLiveRenderSegments();
  document.getElementById('tr-live-record-btn').classList.add('recording');
  document.getElementById('tr-live-record-btn').innerHTML =
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg> Aufnahme stoppen';
  document.getElementById('tr-live-download-btn').disabled = true;
  trLiveStatus('Aufnahme läuft…');

  trLiveStartElapsedTimer();
  trLiveSubscribe(session.id);
  trLiveStartRecorder();
}

/* VAD parameters — tuned for spoken Banking-Meeting audio. ───────────────
 *  - HARD_CAP: maximum chunk length. Forces a flush if speech runs without
 *    a pause this long. Long enough to keep coherent paragraphs together,
 *    short enough that latency stays bounded.
 *  - MIN_LEN:  do not flush on silence before this much audio has accumulated.
 *    Stops a single "uh" or door-slam from triggering a chunk on its own.
 *  - SILENCE_HOLD: continuous silence required to trigger a flush. ~600ms is
 *    the sweet spot — comma pauses (~250ms) don't trigger, sentence ends do.
 *  - CALIBRATION: discard VAD decisions during the first stretch — the
 *    rolling noise floor needs samples before the threshold is meaningful.
 *  - NOISE_DECAY / SPEECH_FACTOR: rolling noise floor adapts only when
 *    audio is below the current threshold (the "noise" branch). Speech is
 *    detected as RMS > floor × SPEECH_FACTOR.
 *  -------------------------------------------------------------------- */
const TR_LIVE_VAD = {
  HARD_CAP_S: 8.0,
  MIN_LEN_S: 1.0,
  SILENCE_HOLD_MS: 600,
  CALIBRATION_S: 1.2,
  NOISE_DECAY: 0.95,      // EWMA factor when updating the rolling noise floor
  SPEECH_FACTOR: 2.5,     // RMS must exceed floor × factor to count as speech
  ABSOLUTE_FLOOR: 0.003,  // hard floor — below this is silence regardless of EWMA
};

function trLiveStartRecorder() {
  // We encode chunks as WAV client-side instead of using MediaRecorder's
  // timeslice mode. Reason: MediaRecorder only writes the webm/mp4 container
  // header in the *first* chunk. Subsequent timeslice chunks are bare
  // Cluster fragments that Voxtral can't decode standalone (HTTP 400
  // "Audio input could not be decoded"). WAV is a flat format — every
  // chunk is independently valid, no init-segment dance required.
  //
  // ScriptProcessor is deprecated but works everywhere; AudioWorklet would
  // be the modern path but adds a separate file load. Keep it simple.
  let ctx;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
  } catch (e) {
    trLiveStatus(`AudioContext fehlgeschlagen: ${e.message}`, true);
    trLiveStop();
    return;
  }
  const source = ctx.createMediaStreamSource(trLiveState.stream);
  // 4096 buffer @ ~ctx.sampleRate gives us ~85ms ticks (sufficient VAD resolution).
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const sampleRate = ctx.sampleRate;

  // Buffer is sized for the hard cap — VAD may flush earlier on silence.
  const maxSamples = Math.round(sampleRate * TR_LIVE_VAD.HARD_CAP_S);
  const minSamples = Math.round(sampleRate * TR_LIVE_VAD.MIN_LEN_S);
  const calibrationSamples = Math.round(sampleRate * TR_LIVE_VAD.CALIBRATION_S);
  let buf = new Float32Array(maxSamples);
  let bufFill = 0;
  let chunkIdx = 0;
  let silenceSamples = 0;             // running silent-sample count
  let totalSamples = 0;                // total audio seen since recording started
  let noiseFloor = TR_LIVE_VAD.ABSOLUTE_FLOOR;
  // Live UI feedback so the user can tell whether the mic is picking them up.
  let lastVadUpdate = 0;

  const flushChunk = async (samples, count, reason) => {
    if (count < sampleRate * 0.4) return;  // skip <0.4s leftover (cosmetic)
    const idx = chunkIdx++;
    const wav = trEncodeWav(samples.subarray(0, count), sampleRate);
    try {
      const fd = new FormData();
      fd.append('chunk', wav, `chunk-${idx}.wav`);
      fd.append('seq', String(idx));
      fd.append('mime', 'audio/wav');
      // Keep the user roughly informed about VAD decisions in the status line.
      // Don't spam — only on explicit hard-cap flushes (those are interesting).
      if (reason === 'hard_cap') {
        trLiveStatus(`Lange Äußerung — nach ${TR_LIVE_VAD.HARD_CAP_S} s übermittelt.`);
      }
      await fetch(`/v1/translate/live/${encodeURIComponent(trLiveState.sessionId)}/chunk`, {
        method: 'POST',
        headers: trAuthHeaders(),
        body: fd,
      });
    } catch (e) {
      console.warn('chunk upload failed', e);
    }
  };

  const flushAndReset = (reason) => {
    if (bufFill === 0) return;
    flushChunk(buf, bufFill, reason);
    // New buffer so the in-flight upload doesn't race with the next callback.
    buf = new Float32Array(maxSamples);
    bufFill = 0;
    silenceSamples = 0;
  };

  proc.onaudioprocess = (ev) => {
    if (!trLiveState.recording) return;
    const input = ev.inputBuffer.getChannelData(0);
    const blockLen = input.length;

    // RMS over this block — coarse but cheap (every ~85ms is fine for VAD).
    let sumSq = 0;
    for (let i = 0; i < blockLen; i++) sumSq += input[i] * input[i];
    const rms = Math.sqrt(sumSq / blockLen);

    // Append samples up to hard cap. If we'd overflow the buffer the
    // hard-cap branch below catches it and flushes first.
    const room = maxSamples - bufFill;
    const take = Math.min(room, blockLen);
    if (take > 0) {
      buf.set(input.subarray(0, take), bufFill);
      bufFill += take;
    }
    totalSamples += blockLen;

    // ─ VAD ─
    // Noise-floor EWMA: only update on quiet blocks so spikes don't poison it.
    const speechThreshold = Math.max(
      TR_LIVE_VAD.ABSOLUTE_FLOOR,
      noiseFloor * TR_LIVE_VAD.SPEECH_FACTOR,
    );
    const isSpeech = rms > speechThreshold;
    if (!isSpeech) {
      noiseFloor = TR_LIVE_VAD.NOISE_DECAY * noiseFloor +
                   (1 - TR_LIVE_VAD.NOISE_DECAY) * Math.max(rms, TR_LIVE_VAD.ABSOLUTE_FLOOR);
      silenceSamples += blockLen;
    } else {
      silenceSamples = 0;
    }

    // Update VAD indicator at most every ~100ms.
    const now = performance.now();
    if (now - lastVadUpdate > 100) {
      trLiveUpdateVadIndicator(isSpeech, rms, speechThreshold);
      lastVadUpdate = now;
    }

    // Flush decisions:
    // 1. Hard cap reached — flush regardless of speech state.
    if (bufFill >= maxSamples) {
      flushAndReset('hard_cap');
      return;
    }
    // 2. Past calibration AND past min-length AND silence held long enough.
    if (totalSamples > calibrationSamples
        && bufFill > minSamples
        && silenceSamples > sampleRate * (TR_LIVE_VAD.SILENCE_HOLD_MS / 1000)) {
      flushAndReset('silence');
    }
  };

  source.connect(proc);
  proc.connect(ctx.destination);

  // Stash the audio plumbing on the recorder slot so trLiveStop can tear it down.
  trLiveState.recorder = {
    ctx, source, proc,
    flushTail: () => flushChunk(buf, bufFill, 'stop'),
    stop: () => {
      try { proc.disconnect(); } catch (_) {}
      try { source.disconnect(); } catch (_) {}
      try { ctx.close(); } catch (_) {}
      trLiveUpdateVadIndicator(false, 0, 0);  // clear pulse
    },
  };
}

/* VAD indicator dot in the status line — pulses while speech detected. */
function trLiveUpdateVadIndicator(isSpeech, rms, threshold) {
  const btn = document.getElementById('tr-live-record-btn');
  if (!btn) return;
  // Reuse the existing 'recording' pulse but tighten the animation when
  // active speech is detected — it's a cheap visual cue without adding
  // a separate element.
  if (trLiveState.recording && isSpeech) {
    btn.classList.add('vad-active');
  } else {
    btn.classList.remove('vad-active');
  }
}

function trEncodeWav(samples, sampleRate) {
  // 16-bit PCM mono WAV. samples is Float32Array in [-1, 1].
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const view = new DataView(buf);
  const writeStr = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
  writeStr(0, 'RIFF');                 view.setUint32(4, 36 + n * 2, true);
  writeStr(8, 'WAVE'); writeStr(12, 'fmt '); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);         view.setUint16(22, 1, true);  // PCM, mono
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);         view.setUint16(34, 16, true); // 2 bytes/sample, 16 bit
  writeStr(36, 'data');                view.setUint32(40, n * 2, true);
  let off = 44;
  for (let i = 0; i < n; i++) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    s = s < 0 ? s * 0x8000 : s * 0x7FFF;
    view.setInt16(off, s, true);
    off += 2;
  }
  return new Blob([buf], { type: 'audio/wav' });
}

async function trLiveStop() {
  trLiveState.recording = false;
  // Drop any pending/queued TTS playback so we don't keep speaking after stop.
  trLiveTtsStop();
  document.getElementById('tr-live-record-btn').classList.remove('recording');
  document.getElementById('tr-live-record-btn').innerHTML =
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="12" r="6"/></svg> Aufnahme starten';
  trLiveStatus('Wird abgeschlossen…');
  if (trLiveState.elapsedTimer) {
    clearInterval(trLiveState.elapsedTimer);
    trLiveState.elapsedTimer = null;
  }
  // Stop audio plumbing: flush leftover samples (<4s tail), then tear down
  // ScriptProcessor + AudioContext. Order matters — flush first so the tail
  // isn't dropped when the proc disconnects.
  if (trLiveState.recorder) {
    try { await trLiveState.recorder.flushTail?.(); } catch (_) {}
    try { trLiveState.recorder.stop?.(); } catch (_) {}
  }
  trLiveState.recorder = null;
  if (trLiveState.stream) {
    trLiveState.stream.getTracks().forEach(t => t.stop());
    trLiveState.stream = null;
  }
  // Tell the server we're done — it'll flush remaining buffer + emit final
  // events, then close the SSE stream.
  if (trLiveState.sessionId) {
    try {
      await fetch(`/v1/translate/live/${encodeURIComponent(trLiveState.sessionId)}/stop`, {
        method: 'POST',
        headers: trAuthHeaders(),
      });
    } catch (e) {
      console.warn('live stop failed', e);
    }
  }
  document.getElementById('tr-live-download-btn').disabled =
    !trLiveState.segments.length;
  trLiveStatus(trLiveState.segments.length ? 'Gestoppt.' : 'Gestoppt (keine Segmente).');
  if (trLiveState.segments.length) trHistoryRefresh();
}

function trLiveStartElapsedTimer() {
  const el = document.getElementById('tr-live-elapsed');
  const tick = () => {
    if (!trLiveState.recording) return;
    const s = Math.floor((Date.now() - trLiveState.startedAt) / 1000);
    el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };
  tick();
  trLiveState.elapsedTimer = setInterval(tick, 500);
}

async function trLiveSubscribe(sessionId) {
  const ctrl = new AbortController();
  trLiveState.abortSse = ctrl;
  let resp;
  try {
    resp = await fetch(`/v1/translate/live/${encodeURIComponent(sessionId)}`, {
      headers: { ...trAuthHeaders(), 'Accept': 'text/event-stream' },
      signal: ctrl.signal,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  } catch (e) {
    if (e.name !== 'AbortError') trLiveStatus(`SSE fehlgeschlagen: ${e.message}`, true);
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let evType = null;
  const dispatch = (type, data) => {
    if (!type || !data) return;
    let payload;
    try { payload = JSON.parse(data); } catch (_) { return; }
    if (type === 'segment') {
      // Final transcript segment landed (translation may follow).
      const seg = {
        start: payload.start || 0,
        end: payload.end || 0,
        text: payload.text || '',
        translation: payload.translation || '',
        detectedLang: payload.detected_lang || '',
        speaker: payload.speaker || '',
        translating: !payload.translation && !!payload.target_lang,
      };
      const idx = trLiveState.segments.length;
      trLiveState.segments.push(seg);
      trLiveRenderSegments();
      // Replay path: translation already attached on the segment event itself.
      if (seg.translation) trLiveMaybeQueueTts(seg, idx);
    } else if (type === 'translation') {
      // Translation for an existing segment landed — match by index.
      const i = (typeof payload.index === 'number') ? payload.index : -1;
      if (i >= 0 && i < trLiveState.segments.length) {
        const seg = trLiveState.segments[i];
        seg.translation = payload.translation || '';
        seg.translating = false;
        trLiveRenderSegments();
        trLiveMaybeQueueTts(seg, i);
      }
    } else if (type === 'error') {
      trLiveStatus(`Server: ${payload.error || 'Fehler'}`, true);
    } else if (type === 'closed') {
      // Server finished flushing.
      try { ctrl.abort(); } catch (_) {}
    }
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (line.startsWith('event:')) evType = line.slice(6).trim();
        else if (line.startsWith('data:')) { dispatch(evType, line.slice(5).trim()); evType = null; }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') trLiveStatus(`SSE-Fehler: ${e.message}`, true);
  } finally {
    if (trLiveState.abortSse === ctrl) trLiveState.abortSse = null;
  }
}

function trLiveRenderSegments() {
  const wrap = document.getElementById('tr-live-stream');
  if (!wrap) return;
  if (!trLiveState.segments.length) {
    wrap.innerHTML = '<div class="tr-placeholder">Höre zu… sprechen Sie ins Mikrofon. Jedes fertiggestellte Segment erscheint hier.</div>';
    return;
  }
  const html = trLiveState.segments.map((s, idx) => {
    const t = trFormatTimeShort(s.start || 0);
    const cls = s.translating ? ' translating' : '';
    const speakerN = s.speaker ? parseInt(s.speaker.replace(/\D/g, ''), 10) || 1 : 0;
    const speakerCls = speakerN ? ` tr-speaker-${((speakerN - 1) % 6) + 1}` : '';
    const speakerLabel = s.speaker
      ? `<span class="tr-live-speaker-label">${escapeHtml(s.speaker)}</span>` : '';
    const text = `<div class="tr-live-segment-src">${speakerLabel}${escapeHtml(s.text)}</div>`;
    const trans = s.translation
      ? `<div class="tr-live-segment-tgt">${escapeHtml(s.translation)}</div>`
      : (s.translating ? `<div class="tr-live-segment-tgt"></div>` : '');
    return `<div class="tr-live-segment${cls}${speakerCls}">
      <div class="tr-live-segment-time">${t}</div>
      <div>${text}${trans}</div>
    </div>`;
  }).join('');
  wrap.innerHTML = html;
  // Auto-scroll to newest line.
  wrap.scrollTop = wrap.scrollHeight;
}

function trLiveClear() {
  if (trLiveState.recording) return;  // no-op while live
  trLiveState.segments = [];
  trLiveState.sessionId = '';
  trLiveRenderSegments();
  document.getElementById('tr-live-download-btn').disabled = true;
  trLiveStatus('');
  document.getElementById('tr-live-elapsed').textContent = '0:00';
}

function trLiveDownload() {
  // Build SRT client-side from the in-memory segment list — server-side
  // download isn't necessary, the segments are already complete.
  if (!trLiveState.segments.length) return;
  const fmt = (sec) => {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    const ms = Math.round((sec - Math.floor(sec)) * 1000);
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')},${String(ms).padStart(3,'0')}`;
  };
  let i = 0;
  const lines = [];
  for (const s of trLiveState.segments) {
    const rawText = (s.translation || s.text || '').trim();
    if (!rawText) continue;
    const text = s.speaker ? `${s.speaker}: ${rawText}` : rawText;
    i++;
    lines.push(String(i));
    lines.push(`${fmt(s.start || 0)} --> ${fmt(s.end || s.start || 0)}`);
    lines.push(text);
    lines.push('');
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `live-${new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')}.srt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
