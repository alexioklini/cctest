'use strict';

/* ═══════════════════════════════════════════════════════════
   TRANSLATION VIEW — Phase A: text + glossaries
   Document / audio / live tabs land in later sessions.
   ═══════════════════════════════════════════════════════════ */

const trState = {
  // Auto-detect when '', else ISO 639-1.
  sourceLang: '',
  // Manual override flag — once user picks a source explicitly we stop
  // overwriting it from auto-detection.
  sourceLangManual: false,
  // Last detection result for the badge (lang, confidence, source).
  detected: null,
  targetLang: 'en',
  glossarySlug: '',
  model: '',
  tone: '',
  glossaries: [],          // cached list from /v1/translate/glossaries
  detectTimer: null,
  inflightAbort: null,
  // Glossary modal state — null = list view, object = editing form.
  modalEditing: null,
};

// Curated language list — covers what a German bank realistically translates
// in/out of. Easy to extend without touching server-side LANG_NAMES.
const TR_LANGS = [
  ['en', 'Englisch'],
  ['de', 'Deutsch'],
  ['fr', 'Französisch'],
  ['es', 'Spanisch'],
  ['it', 'Italienisch'],
  ['pt', 'Portugiesisch'],
  ['nl', 'Niederländisch'],
  ['pl', 'Polnisch'],
  ['cs', 'Tschechisch'],
  ['sk', 'Slowakisch'],
  ['hu', 'Ungarisch'],
  ['ro', 'Rumänisch'],
  ['tr', 'Türkisch'],
  ['el', 'Griechisch'],
  ['sv', 'Schwedisch'],
  ['da', 'Dänisch'],
  ['no', 'Norwegisch'],
  ['fi', 'Finnisch'],
  ['ru', 'Russisch'],
  ['uk', 'Ukrainisch'],
  ['ja', 'Japanisch'],
  ['zh', 'Chinesisch'],
  ['ko', 'Koreanisch'],
  ['ar', 'Arabisch'],
  ['hi', 'Hindi'],
];
const TR_LANG_NAMES = Object.fromEntries(TR_LANGS);

function trLangLabel(code) {
  if (!code) return 'Automatisch erkennen';
  return TR_LANG_NAMES[code] || code.toUpperCase();
}

/* ─── Init ─────────────────────────────────────────────────── */

async function loadTranslationView() {
  trRenderTargetPill();
  trRenderSourcePill();
  await trLoadGlossaries();
  trUpdateCounts();
  // Inline history is now part of every tab — fetch once on mount.
  _trHistoryInstallFileClickHandler();
  trHistoryRefresh();
}

async function trLoadGlossaries() {
  try {
    const data = await API.get('/v1/translate/glossaries');
    trState.glossaries = data.glossaries || [];
  } catch (e) {
    console.warn('trLoadGlossaries failed', e);
    trState.glossaries = [];
  }
  const sel = document.getElementById('tr-glossary-select');
  if (!sel) return;
  const prev = trState.glossarySlug;
  sel.innerHTML = '<option value="">— keines —</option>' +
    trState.glossaries.map(g =>
      `<option value="${escapeHtml(g.slug)}">${escapeHtml(g.name)}</option>`
    ).join('');
  if (prev && trState.glossaries.some(g => g.slug === prev)) {
    sel.value = prev;
  } else {
    trState.glossarySlug = '';
    sel.value = '';
  }
}

/* ─── Tabs ─────────────────────────────────────────────────── */

function trSwitchTab(tab) {
  if (!['text', 'document', 'audio', 'live'].includes(tab)) return;
  document.querySelectorAll('.tr-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  const map = {
    text: 'tr-panel-text',
    document: 'tr-panel-document',
    audio: 'tr-panel-audio',
    live: 'tr-panel-live',
  };
  for (const [key, id] of Object.entries(map)) {
    const el = document.getElementById(id);
    if (el) el.style.display = (tab === key) ? '' : 'none';
  }
  const modeGroup = document.getElementById('tr-toolbar-mode-group');
  if (modeGroup) modeGroup.style.display = (tab === 'audio') ? '' : 'none';
  const liveModeGroup = document.getElementById('tr-toolbar-live-mode-group');
  if (liveModeGroup) liveModeGroup.style.display = (tab === 'live') ? '' : 'none';
  if (typeof _updateTranslationHeaderStar === 'function') _updateTranslationHeaderStar(tab);
}

/* ─── Source / target language pills ──────────────────────── */

function trRenderSourcePill() {
  const label = document.getElementById('tr-source-label');
  if (!label) return;
  if (trState.sourceLang) {
    label.textContent = trLangLabel(trState.sourceLang) +
      (trState.sourceLangManual ? '' : ' (auto)');
  } else {
    label.textContent = 'Automatisch erkennen';
  }
  const pill = document.getElementById('tr-source-pill');
  if (pill) pill.classList.toggle('tr-pill-manual', trState.sourceLangManual);
}

function trRenderTargetPill() {
  const label = document.getElementById('tr-target-label');
  if (label) label.textContent = trLangLabel(trState.targetLang || 'en');
}

function trToggleSourceMenu(ev) {
  ev.stopPropagation();
  trCloseAllMenus();
  const pill = document.getElementById('tr-source-pill');
  const menu = document.createElement('div');
  menu.className = 'tr-lang-menu';
  menu.id = 'tr-source-menu';
  const items = [['', 'Automatisch erkennen'], null, ...TR_LANGS];
  menu.innerHTML = items.map(it => {
    if (it === null) return '<div class="tr-lang-menu-divider"></div>';
    const [code, name] = it;
    const active = trState.sourceLang === code ? 'active' : '';
    return `<div class="tr-lang-menu-item ${active}" data-code="${code}">${escapeHtml(name)}</div>`;
  }).join('');
  menu.querySelectorAll('.tr-lang-menu-item').forEach(el => {
    el.onclick = () => {
      const code = el.dataset.code || '';
      trState.sourceLang = code;
      trState.sourceLangManual = !!code;
      // When user reverts to auto, re-run detection on what's already typed.
      if (!code) trState.detected = null;
      trRenderSourcePill();
      trUpdateDetectBadge();
      trCloseAllMenus();
      if (!code) trMaybeAutoDetect(true);
    };
  });
  pill.appendChild(menu);
}

function trToggleTargetMenu(ev) {
  ev.stopPropagation();
  trCloseAllMenus();
  const pill = document.getElementById('tr-target-pill');
  const menu = document.createElement('div');
  menu.className = 'tr-lang-menu';
  menu.id = 'tr-target-menu';
  menu.innerHTML = TR_LANGS.map(([code, name]) => {
    const active = trState.targetLang === code ? 'active' : '';
    return `<div class="tr-lang-menu-item ${active}" data-code="${code}">${escapeHtml(name)}</div>`;
  }).join('');
  menu.querySelectorAll('.tr-lang-menu-item').forEach(el => {
    el.onclick = () => {
      trState.targetLang = el.dataset.code;
      trRenderTargetPill();
      trCloseAllMenus();
    };
  });
  pill.appendChild(menu);
}

function trCloseAllMenus() {
  document.querySelectorAll('.tr-lang-menu').forEach(m => m.remove());
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.tr-pill') && !e.target.closest('.tr-lang-menu')) {
    trCloseAllMenus();
  }
});

function trSwapLanguages() {
  if (!trState.sourceLang) {
    // Nothing to swap if source is auto — bump it from the detection result.
    const det = trState.detected?.lang || '';
    if (!det) return;
    trState.sourceLang = trState.targetLang;
    trState.targetLang = det;
  } else {
    [trState.sourceLang, trState.targetLang] = [trState.targetLang, trState.sourceLang];
  }
  trState.sourceLangManual = true;
  trRenderSourcePill();
  trRenderTargetPill();
  // Also swap the actual content if a translation has already been produced.
  const src = document.getElementById('tr-source-textarea');
  const out = document.getElementById('tr-target-output');
  if (src && out) {
    const outText = out.dataset.translation || '';
    if (outText) {
      src.value = outText;
      out.dataset.translation = '';
      out.innerHTML = '<div class="tr-placeholder">Die Übersetzung erscheint hier.</div>';
      trUpdateCounts();
    }
  }
}

function trSetGlossary(slug) {
  trState.glossarySlug = slug || '';
}

function trSetTone(tone) {
  trState.tone = tone || '';
}

/* ─── Source input + auto-detect ──────────────────────────── */

function trOnSourceInput() {
  trUpdateCounts();
  trMaybeAutoDetect(false);
}

function trUpdateCounts() {
  const src = document.getElementById('tr-source-textarea');
  const out = document.getElementById('tr-target-output');
  const sc = document.getElementById('tr-source-count');
  const tc = document.getElementById('tr-target-count');
  if (sc && src) sc.textContent = `${src.value.length} Zeichen`;
  if (tc && out) {
    const len = (out.dataset.translation || '').length;
    tc.textContent = `${len} Zeichen`;
  }
}

function trClearSource() {
  const src = document.getElementById('tr-source-textarea');
  if (src) src.value = '';
  const out = document.getElementById('tr-target-output');
  if (out) {
    out.innerHTML = '<div class="tr-placeholder">Die Übersetzung erscheint hier.</div>';
    out.dataset.translation = '';
  }
  trState.detected = null;
  trUpdateDetectBadge();
  trUpdateCounts();
}

function trMaybeAutoDetect(force) {
  if (trState.sourceLangManual && !force) return;
  if (trState.detectTimer) clearTimeout(trState.detectTimer);
  trState.detectTimer = setTimeout(async () => {
    const src = document.getElementById('tr-source-textarea');
    const text = src ? src.value.trim() : '';
    if (text.length < 12) {
      trState.detected = null;
      trUpdateDetectBadge();
      return;
    }
    try {
      const r = await API.post('/v1/translate/detect', { text: text.slice(0, 1500) });
      trState.detected = r;
      if (!trState.sourceLangManual && r.lang) {
        trState.sourceLang = r.lang;
        trRenderSourcePill();
      }
      trUpdateDetectBadge();
    } catch (e) {
      console.warn('detect failed', e);
    }
  }, 350);
}

function trUpdateDetectBadge() {
  const badge = document.getElementById('tr-detect-badge');
  if (!badge) return;
  const d = trState.detected;
  if (!d || !d.lang) {
    badge.classList.remove('visible', 'low-conf');
    badge.textContent = '';
    return;
  }
  const pct = Math.round((d.confidence || 0) * 100);
  badge.textContent = `Erkannt: ${trLangLabel(d.lang)} · ${pct}%`;
  badge.classList.add('visible');
  badge.classList.toggle('low-conf', d.confidence < 0.7);
}

/* ─── Translate ───────────────────────────────────────────── */

async function trRunTextTranslation() {
  const src = document.getElementById('tr-source-textarea');
  const out = document.getElementById('tr-target-output');
  const status = document.getElementById('tr-status');
  const btn = document.getElementById('tr-translate-btn');
  if (!src || !out) return;

  const text = src.value;
  if (!text.trim()) {
    status.textContent = 'Der Quelltext ist leer.';
    status.classList.add('error');
    return;
  }
  if (!trState.targetLang) {
    status.textContent = 'Wählen Sie eine Zielsprache.';
    status.classList.add('error');
    return;
  }

  status.textContent = 'Wird übersetzt…';
  status.classList.remove('error');
  btn.disabled = true;
  out.classList.add('translating');
  out.innerHTML = '<div class="tr-placeholder">Wird übersetzt…</div>';
  out.dataset.translation = '';

  const t0 = Date.now();
  try {
    const body = {
      text,
      target_lang: trState.targetLang,
    };
    if (trState.sourceLangManual && trState.sourceLang) {
      body.source_lang = trState.sourceLang;
    }
    if (trState.glossarySlug) body.glossary = trState.glossarySlug;
    if (trState.model) body.model = trState.model;
    if (trState.tone) body.tone = trState.tone;
    const r = await API.post('/v1/translate/text', body);
    const dt = ((Date.now() - t0) / 1000).toFixed(1);
    out.classList.remove('translating');
    out.dataset.translation = r.translation || '';
    out.textContent = r.translation || '';
    if (r.detected && !trState.sourceLangManual) {
      trState.detected = r.detected;
      if (r.detected.lang) trState.sourceLang = r.detected.lang;
      trRenderSourcePill();
      trUpdateDetectBadge();
    }
    if (r.noop) {
      status.textContent = `Quelltext ist bereits ${trLangLabel(trState.targetLang)} — keine Übersetzung nötig.`;
    } else {
      const m = r.model ? ` · ${r.model}` : '';
      status.textContent = `Fertig in ${dt}s${m}`;
    }
    trUpdateCounts();
    // Prefetch TTS for both sides so audio is ready on first click.
    const srcLang = trState.sourceLang || trState.detected?.lang || '';
    _trTtsPrefetch('source', text, srcLang);
    if (r.translation && !r.noop) _trTtsPrefetch('target', r.translation, trState.targetLang);
    if (r.translation && !r.noop) trHistoryRefresh();
  } catch (e) {
    out.classList.remove('translating');
    out.innerHTML = '<div class="tr-placeholder">Übersetzung fehlgeschlagen.</div>';
    status.textContent = (e.message || 'Übersetzung fehlgeschlagen').slice(0, 240);
    status.classList.add('error');
  } finally {
    btn.disabled = false;
  }
}

function trCopyTarget() {
  const out = document.getElementById('tr-target-output');
  const text = out?.dataset.translation || '';
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    const status = document.getElementById('tr-status');
    if (status) {
      const prev = status.textContent;
      status.textContent = 'Kopiert.';
      setTimeout(() => { if (status.textContent === 'Kopiert.') status.textContent = prev; }, 1500);
    }
  });
}

/* ─── Text-to-Speech ─────────────────────────────────────── */

// Shared state for the currently active TTS playback.
let _trTtsAudio = null;
let _trTtsPlaying = false;
// Pre-fetched blob URLs keyed by side ('source'|'target'), cleared on new translation.
const _trTtsCache = { source: null, target: null };

function _trTtsSetStatus(msg) {
  const el = document.getElementById('tr-status');
  if (el) el.textContent = msg || '';
}

function _trTtsResetBtns() {
  document.getElementById('tr-tts-source-btn')?.classList.remove('tr-tts-active');
  document.getElementById('tr-tts-target-btn')?.classList.remove('tr-tts-active');
}

function _trTtsStop() {
  if (_trTtsAudio) { _trTtsAudio.pause(); _trTtsAudio = null; }
  _trTtsPlaying = false;
  _trTtsResetBtns();
}

async function _trTtsFetch(text, lang) {
  // Returns a blob URL ready to play, or throws.
  const resp = await fetch('/v1/translate/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...trAuthHeaders() },
    body: JSON.stringify({ text, lang }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}

// Prefetch is called optimistically after a translation completes so the audio
// is ready by the time the user clicks the speaker button.
async function _trTtsPrefetch(side, text, lang) {
  if (!text) return;
  // Revoke any previous blob for this side.
  if (_trTtsCache[side]) { try { URL.revokeObjectURL(_trTtsCache[side]); } catch(_) {} }
  _trTtsCache[side] = null;
  try {
    _trTtsCache[side] = await _trTtsFetch(text, lang);
  } catch(_) { /* silent — will fetch on demand if prefetch failed */ }
}

// Called on real button click (user gesture) — plays immediately if cached,
// fetches synchronously otherwise.
async function _trTtsPlay(side, text, lang, btn) {
  // Toggle off if already playing.
  if (_trTtsPlaying) { _trTtsStop(); return; }

  _trTtsSetStatus('');
  _trTtsResetBtns();
  if (btn) btn.classList.add('tr-tts-active');

  try {
    let blobUrl = _trTtsCache[side];
    if (!blobUrl) {
      // No prefetch — fetch now (still within the user-gesture callstack).
      blobUrl = await _trTtsFetch(text, lang);
    } else {
      // Clear cache so next call re-fetches fresh.
      _trTtsCache[side] = null;
    }

    const audio = new Audio(blobUrl);
    _trTtsAudio = audio;
    _trTtsPlaying = true;

    audio.onended = () => {
      URL.revokeObjectURL(blobUrl);
      _trTtsPlaying = false;
      _trTtsResetBtns();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(blobUrl);
      _trTtsPlaying = false;
      _trTtsResetBtns();
      _trTtsSetStatus('TTS-Wiedergabefehler');
    };

    await audio.play();
  } catch (e) {
    _trTtsStop();
    _trTtsSetStatus('TTS fehlgeschlagen: ' + e.message);
  }
}

function trSpeakSource() {
  const text = document.getElementById('tr-source-textarea')?.value?.trim();
  if (!text) return;
  const lang = trState.sourceLang || trState.detected?.lang || '';
  const btn = document.getElementById('tr-tts-source-btn');
  _trTtsPlay('source', text, lang, btn);
}

function trSpeakTarget() {
  const out = document.getElementById('tr-target-output');
  const text = out?.dataset.translation || '';
  if (!text) return;
  const lang = trState.targetLang || 'en';
  const btn = document.getElementById('tr-tts-target-btn');
  _trTtsPlay('target', text, lang, btn);
}

/* ─── Glossaries modal ────────────────────────────────────── */

function trOpenGlossariesModal() {
  trState.modalEditing = null;
  document.getElementById('tr-glossaries-modal').classList.remove('hidden');
  trRenderGlossariesModal();
}
function trCloseGlossariesModal() {
  document.getElementById('tr-glossaries-modal').classList.add('hidden');
  // Refresh the dropdown so newly created glossaries appear.
  trLoadGlossaries();
}

async function trRenderGlossariesModal() {
  const title = document.getElementById('tr-modal-title');
  const body = document.getElementById('tr-modal-body');
  if (!body) return;

  if (trState.modalEditing) {
    title.textContent = trState.modalEditing.slug ? 'Glossar bearbeiten' : 'Neues Glossar';
    body.innerHTML = trGlossaryFormHtml(trState.modalEditing);
    trBindGlossaryFormEvents();
    return;
  }

  title.textContent = 'Glossare';
  // Fresh fetch so the modal always shows current state.
  let list = [];
  try {
    const data = await API.get('/v1/translate/glossaries');
    list = data.glossaries || [];
    trState.glossaries = list;
  } catch (e) {
    body.innerHTML = `<div class="tr-gloss-empty">Glossare konnten nicht geladen werden: ${escapeHtml(e.message || '')}</div>`;
    return;
  }
  const newBtn = `
    <div style="margin-bottom:14px;display:flex;justify-content:flex-end">
      <button class="tr-btn tr-btn-primary" onclick="trEditGlossary('')">+ Neues Glossar</button>
    </div>`;
  if (!list.length) {
    body.innerHTML = newBtn +
      '<div class="tr-gloss-empty">Noch keine Glossare. Erstellen Sie eines, um bankspezifische Terminologie durchzusetzen.</div>';
    return;
  }
  body.innerHTML = newBtn + '<div class="tr-gloss-list">' +
    list.map(g => `
      <div class="tr-gloss-card">
        <div class="tr-gloss-card-main" style="flex:1;min-width:0">
          <div class="tr-gloss-card-name">${escapeHtml(g.name)}</div>
          ${g.description ? `<div class="tr-gloss-card-desc">${escapeHtml(g.description)}</div>` : ''}
          <div class="tr-gloss-card-meta">
            ${g.source ? `${trLangLabel(g.source)} → ${trLangLabel(g.target)} · ` : ''}${g.entry_count} Einträge${g.do_not_translate_count ? ` · ${g.do_not_translate_count} nicht übersetzen` : ''}
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="tr-btn" onclick="trEditGlossary('${escapeHtml(g.slug)}')">Bearbeiten</button>
          <button class="tr-btn tr-btn-danger" onclick="trDeleteGlossary('${escapeHtml(g.slug)}','${escapeHtml(g.name)}')">Löschen</button>
        </div>
      </div>
    `).join('') + '</div>';
}

async function trEditGlossary(slug) {
  if (!slug) {
    trState.modalEditing = {
      slug: '',
      name: '',
      description: '',
      source: 'de',
      target: 'en',
      entries: [{ src: '', tgt: '' }],
      do_not_translate: [],
    };
  } else {
    try {
      const g = await API.get(`/v1/translate/glossaries/${encodeURIComponent(slug)}`);
      trState.modalEditing = {
        slug: g.slug || slug,
        name: g.name || '',
        description: g.description || '',
        source: g.source || '',
        target: g.target || '',
        entries: (g.entries && g.entries.length ? g.entries : [{ src: '', tgt: '' }]).map(e => ({ src: e.src, tgt: e.tgt })),
        do_not_translate: g.do_not_translate || [],
      };
    } catch (e) {
      await showAlert('Glossar konnte nicht geladen werden: ' + (e.message || ''));
      return;
    }
  }
  trRenderGlossariesModal();
}

function trGlossaryFormHtml(g) {
  const langOptions = (sel) => {
    const opts = ['<option value="">—</option>'].concat(
      TR_LANGS.map(([c, n]) => `<option value="${c}" ${sel === c ? 'selected' : ''}>${n}</option>`)
    );
    return opts.join('');
  };
  const entries = g.entries.map((e, i) => `
    <div class="tr-gloss-entry" data-idx="${i}">
      <input type="text" class="tr-gloss-entry-src" placeholder="Quellbegriff"
             value="${escapeHtml(e.src || '')}">
      <input type="text" class="tr-gloss-entry-tgt" placeholder="Zielbegriff"
             value="${escapeHtml(e.tgt || '')}">
      <button class="tr-gloss-entry-remove" type="button" data-idx="${i}" title="Entfernen">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
  `).join('');

  return `
    <form class="tr-gloss-form" id="tr-gloss-form" onsubmit="return false">
      <div class="tr-gloss-row">
        <div class="tr-gloss-field">
          <label>Name</label>
          <input type="text" id="tr-gloss-name" value="${escapeHtml(g.name)}" required placeholder="z. B. Bank DE-EN">
        </div>
        <div class="tr-gloss-field">
          <label>Beschreibung</label>
          <input type="text" id="tr-gloss-desc" value="${escapeHtml(g.description)}" placeholder="optional">
        </div>
      </div>
      <div class="tr-gloss-row">
        <div class="tr-gloss-field">
          <label>Quellsprache (Hinweis)</label>
          <select id="tr-gloss-source">${langOptions(g.source)}</select>
        </div>
        <div class="tr-gloss-field">
          <label>Zielsprache (Hinweis)</label>
          <select id="tr-gloss-target">${langOptions(g.target)}</select>
        </div>
      </div>

      <div class="tr-gloss-field">
        <label>Begriffszuordnungen</label>
        <div class="tr-gloss-entries" id="tr-gloss-entries">
          <div class="tr-gloss-entries-header">
            <div>Quelle</div><div>Ziel</div><div></div>
          </div>
          ${entries}
          <div class="tr-gloss-add-row">
            <button class="tr-btn" type="button" onclick="trAddGlossaryEntry()">+ Begriff hinzufügen</button>
          </div>
        </div>
      </div>

      <div class="tr-gloss-field">
        <label>Nicht übersetzen (ein Begriff pro Zeile)</label>
        <textarea id="tr-gloss-dnt" rows="3"
          placeholder="BaFin&#10;DZ Bank&#10;MaRisk">${escapeHtml((g.do_not_translate || []).join('\n'))}</textarea>
      </div>

      <div class="tr-gloss-form-actions">
        ${g.slug ? `<button class="tr-btn tr-btn-danger" type="button" onclick="trDeleteGlossary('${escapeHtml(g.slug)}','${escapeHtml(g.name)}')">Löschen</button>` : ''}
        <div class="tr-btn-spacer"></div>
        <button class="tr-btn" type="button" onclick="trCancelGlossaryEdit()">Abbrechen</button>
        <button class="tr-btn tr-btn-primary" type="button" onclick="trSaveGlossary()">Speichern</button>
      </div>
    </form>
  `;
}

function trBindGlossaryFormEvents() {
  document.querySelectorAll('.tr-gloss-entry-remove').forEach(btn => {
    btn.onclick = () => {
      const idx = parseInt(btn.dataset.idx, 10);
      trReadGlossaryFormIntoState();
      trState.modalEditing.entries.splice(idx, 1);
      if (!trState.modalEditing.entries.length) {
        trState.modalEditing.entries.push({ src: '', tgt: '' });
      }
      trRenderGlossariesModal();
    };
  });
}

function trReadGlossaryFormIntoState() {
  const g = trState.modalEditing;
  if (!g) return;
  g.name = document.getElementById('tr-gloss-name')?.value || '';
  g.description = document.getElementById('tr-gloss-desc')?.value || '';
  g.source = document.getElementById('tr-gloss-source')?.value || '';
  g.target = document.getElementById('tr-gloss-target')?.value || '';
  const dnt = (document.getElementById('tr-gloss-dnt')?.value || '')
    .split('\n').map(s => s.trim()).filter(Boolean);
  g.do_not_translate = dnt;
  const rows = document.querySelectorAll('#tr-gloss-entries .tr-gloss-entry');
  g.entries = Array.from(rows).map(row => ({
    src: row.querySelector('.tr-gloss-entry-src')?.value || '',
    tgt: row.querySelector('.tr-gloss-entry-tgt')?.value || '',
  }));
}

function trAddGlossaryEntry() {
  trReadGlossaryFormIntoState();
  trState.modalEditing.entries.push({ src: '', tgt: '' });
  trRenderGlossariesModal();
}

function trCancelGlossaryEdit() {
  trState.modalEditing = null;
  trRenderGlossariesModal();
}

async function trSaveGlossary() {
  trReadGlossaryFormIntoState();
  const g = trState.modalEditing;
  if (!g.name.trim()) {
    await showAlert('Name ist erforderlich.');
    return;
  }
  // Strip empty rows.
  g.entries = (g.entries || []).filter(e => (e.src || '').trim() && (e.tgt || '').trim());
  try {
    const saved = await API.post('/v1/translate/glossaries', {
      slug: g.slug || undefined,
      name: g.name,
      description: g.description,
      source: g.source,
      target: g.target,
      entries: g.entries,
      do_not_translate: g.do_not_translate,
    });
    trState.modalEditing = null;
    trRenderGlossariesModal();
    // Sync selector state too.
    trState.glossarySlug = saved.slug;
    await trLoadGlossaries();
    const sel = document.getElementById('tr-glossary-select');
    if (sel) sel.value = saved.slug;
  } catch (e) {
    await showAlert('Speichern fehlgeschlagen: ' + (e.message || ''));
  }
}

async function trDeleteGlossary(slug, name) {
  if (!slug) return;
  if (!await showConfirmDanger(`Glossar "${name || slug}" löschen?`, 'Glossar löschen', 'Löschen')) return;
  try {
    await API.del(`/v1/translate/glossaries/${encodeURIComponent(slug)}`);
    if (trState.glossarySlug === slug) trState.glossarySlug = '';
    trState.modalEditing = null;
    trRenderGlossariesModal();
    await trLoadGlossaries();
  } catch (e) {
    await showAlert('Löschen fehlgeschlagen: ' + (e.message || ''));
  }
}

/* ─── Document translation ────────────────────────────────── */

const trDocState = {
  file: null,        // File object the user dropped/selected
  jobId: '',
  source: null,      // EventSource for SSE
  outputName: '',
  fallback: false,   // true when PDF→DOCX conversion happened
};

const TR_DOC_EXTS = ['.docx', '.pptx', '.pdf'];
const TR_DOC_MAX_BYTES = 50 * 1024 * 1024;

function trDocDragOver(ev) {
  ev.preventDefault();
  document.getElementById('tr-doc-drop')?.classList.add('dragging');
}
function trDocDragLeave(ev) {
  ev.preventDefault();
  document.getElementById('tr-doc-drop')?.classList.remove('dragging');
}
function trDocDrop(ev) {
  ev.preventDefault();
  document.getElementById('tr-doc-drop')?.classList.remove('dragging');
  const f = ev.dataTransfer?.files?.[0];
  if (f) trDocSetFile(f);
}
function trDocFileSelected(ev) {
  const f = ev.target.files?.[0];
  if (f) trDocSetFile(f);
  // Reset input so re-selecting the same file fires onchange.
  ev.target.value = '';
}

function trDocSetFile(f) {
  const status = document.getElementById('tr-doc-status');
  const ext = ('.' + (f.name.split('.').pop() || '')).toLowerCase();
  if (!TR_DOC_EXTS.includes(ext)) {
    if (status) {
      status.textContent = `Nicht unterstützter Dateityp ${ext}. Verwenden Sie ${TR_DOC_EXTS.join(', ')}.`;
      status.classList.add('error');
    }
    return;
  }
  if (f.size > TR_DOC_MAX_BYTES) {
    if (status) {
      status.textContent = 'Datei zu groß (max. 50 MB).';
      status.classList.add('error');
    }
    return;
  }
  trDocState.file = f;
  if (status) {
    status.classList.remove('error');
    status.textContent = `${f.name} · ${(f.size / 1024).toFixed(0)} KB`;
  }
  document.getElementById('tr-doc-translate-btn').disabled = false;
  // Clear any prior completed job UI so the user sees a fresh state.
  trDocClearJobPanel();
}

function trDocClearJobPanel() {
  const panel = document.getElementById('tr-doc-job');
  panel?.classList.add('hidden');
  document.getElementById('tr-doc-progress-bar').style.width = '0%';
  document.getElementById('tr-doc-download-btn')?.classList.add('hidden');
}

function trDocReset() {
  if (trDocState.source) {
    // source is an AbortController for the active SSE fetch.
    try { trDocState.source.abort(); } catch (_) {}
  }
  trDocState.source = null;
  trDocState.jobId = '';
  trDocState.file = null;
  trDocState.outputName = '';
  trDocState.fallback = false;
  document.getElementById('tr-doc-translate-btn').disabled = true;
  trDocClearJobPanel();
  const status = document.getElementById('tr-doc-status');
  if (status) {
    status.textContent = '';
    status.classList.remove('error');
  }
}

function trAuthHeaders() {
  const t = localStorage.getItem('auth-token');
  return t ? { 'Authorization': `Bearer ${t}` } : {};
}

async function trRunDocTranslation() {
  const file = trDocState.file;
  const status = document.getElementById('tr-doc-status');
  if (!file) {
    if (status) { status.textContent = 'Wählen Sie zuerst eine Datei.'; status.classList.add('error'); }
    return;
  }
  if (!trState.targetLang) {
    if (status) { status.textContent = 'Wählen Sie eine Zielsprache.'; status.classList.add('error'); }
    return;
  }

  // Build multipart body — same shape the server expects.
  const fd = new FormData();
  fd.append('file', file, file.name);
  fd.append('target_lang', trState.targetLang);
  if (trState.sourceLangManual && trState.sourceLang) {
    fd.append('source_lang', trState.sourceLang);
  }
  if (trState.glossarySlug) fd.append('glossary', trState.glossarySlug);
  if (trState.model) fd.append('model', trState.model);
  if (trState.tone) fd.append('tone', trState.tone);

  document.getElementById('tr-doc-translate-btn').disabled = true;
  status.textContent = 'Wird hochgeladen…';
  status.classList.remove('error');

  let job;
  try {
    // Don't set Content-Type — fetch derives it (with the boundary) from the FormData.
    const res = await fetch('/v1/translate/document', {
      method: 'POST',
      headers: trAuthHeaders(),
      body: fd,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    job = await res.json();
  } catch (e) {
    status.textContent = (e.message || 'Hochladen fehlgeschlagen').slice(0, 240);
    status.classList.add('error');
    document.getElementById('tr-doc-translate-btn').disabled = false;
    return;
  }

  trDocState.jobId = job.job_id;
  trDocShowJobPanel(job, file.name);
  trDocSubscribe(job.job_id);
  status.textContent = 'Wird übersetzt…';
}

function trDocShowJobPanel(job, filename) {
  const panel = document.getElementById('tr-doc-job');
  panel.classList.remove('hidden');
  document.getElementById('tr-doc-job-name').textContent = filename || job.filename || 'Dokument';
  trDocApplyJobState(job);
}

function trDocApplyJobState(job) {
  const stateEl = document.getElementById('tr-doc-job-state');
  const meta = document.getElementById('tr-doc-job-meta');
  const bar = document.getElementById('tr-doc-progress-bar');
  const dl = document.getElementById('tr-doc-download-btn');
  if (!stateEl || !meta || !bar) return;
  stateEl.classList.remove('running', 'done', 'error');
  switch (job.state) {
    case 'queued':
      stateEl.textContent = 'In Warteschlange'; break;
    case 'running':
      stateEl.textContent = 'Wird übersetzt';
      stateEl.classList.add('running');
      break;
    case 'done':
      stateEl.textContent = 'Fertig';
      stateEl.classList.add('done');
      break;
    case 'error':
      stateEl.textContent = 'Fehler';
      stateEl.classList.add('error');
      break;
    default:
      stateEl.textContent = job.state || '';
  }
  const pct = Math.max(0, Math.min(100, job.progress_pct || 0));
  bar.style.width = pct + '%';
  if (job.state === 'done') {
    trDocState.outputName = job.output_filename || '';
    trDocState.fallback = !!job.fallback;
    const lines = [
      `${job.runs} Segment${job.runs === 1 ? '' : 'e'} übersetzt`,
    ];
    if (job.model) lines.push(job.model);
    if (job.fallback) lines.push('PDF in DOCX umgewandelt');
    if (job.noop) lines.push('Quelle bereits in Zielsprache — kopiert');
    meta.textContent = lines.join(' · ');
    dl?.classList.remove('hidden');
    const status = document.getElementById('tr-doc-status');
    if (status) { status.textContent = 'Fertig.'; status.classList.remove('error'); }
    trHistoryRefresh();
  } else if (job.state === 'error') {
    meta.textContent = job.error || 'Unbekannter Fehler';
    dl?.classList.add('hidden');
    const status = document.getElementById('tr-doc-status');
    if (status) {
      status.textContent = (job.error || 'Übersetzung fehlgeschlagen').slice(0, 240);
      status.classList.add('error');
    }
    document.getElementById('tr-doc-translate-btn').disabled = false;
  } else {
    if (job.runs_total) {
      meta.textContent = `${job.runs_done} / ${job.runs_total} Segmente · ${pct.toFixed(0)}%`;
    } else {
      meta.textContent = 'Wird vorbereitet…';
    }
  }
}

async function trDocSubscribe(jobId) {
  // Native EventSource can't carry an Authorization header — and our /v1/*
  // gate requires Bearer auth — so we drive SSE with fetch() + a streaming
  // ReadableTextDecoder. Same line-buffering trick API.streamChat uses.
  const ctrl = new AbortController();
  trDocState.source = ctrl;
  let resp;
  try {
    resp = await fetch(`/v1/translate/jobs/${encodeURIComponent(jobId)}`, {
      headers: { ...trAuthHeaders(), 'Accept': 'text/event-stream' },
      signal: ctrl.signal,
    });
  } catch (e) {
    if (e.name !== 'AbortError') {
      const status = document.getElementById('tr-doc-status');
      if (status) { status.textContent = `SSE fehlgeschlagen: ${e.message}`; status.classList.add('error'); }
    }
    return;
  }
  if (!resp.ok) {
    const status = document.getElementById('tr-doc-status');
    if (status) { status.textContent = `SSE: HTTP ${resp.status}`; status.classList.add('error'); }
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let evType = null;

  const dispatch = (type, data) => {
    if (!type || !data) return;
    let job;
    try { job = JSON.parse(data); } catch (_) { return; }
    trDocApplyJobState(job);
    if (type === 'done' || type === 'error') {
      try { ctrl.abort(); } catch (_) {}
      trDocState.source = null;
      document.getElementById('tr-doc-translate-btn').disabled = false;
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
        if (line.startsWith('event:')) {
          evType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          dispatch(evType, line.slice(5).trim());
          evType = null;
        }
        // ': keepalive' comments / blank lines: ignore
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      const status = document.getElementById('tr-doc-status');
      if (status) { status.textContent = `SSE-Fehler: ${e.message}`; status.classList.add('error'); }
    }
  } finally {
    if (trDocState.source === ctrl) trDocState.source = null;
  }
}

async function trDocDownload() {
  if (!trDocState.jobId) return;
  // fetch() so we can carry the Bearer header — anchor-href can't. Materialise
  // the response as a blob and trigger the browser's download via object URL.
  const status = document.getElementById('tr-doc-status');
  try {
    const resp = await fetch(
      `/v1/translate/jobs/${encodeURIComponent(trDocState.jobId)}/result`,
      { headers: trAuthHeaders() }
    );
    if (!resp.ok) {
      const err = await resp.text().catch(() => '');
      throw new Error(`HTTP ${resp.status} ${err.slice(0, 120)}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = trDocState.outputName || 'translation';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) {
    if (status) {
      status.textContent = `Herunterladen fehlgeschlagen: ${e.message}`;
      status.classList.add('error');
    }
  }
}

/* ═══════════════════════════════════════════════════════════
   AUDIO / VIDEO TAB (C1)
   File upload → segmented transcript + translation in TXT/SRT/VTT.
   Mirrors document-tab plumbing — multipart upload, SSE-progress,
   per-format download buttons.
   ═══════════════════════════════════════════════════════════ */

const trMediaState = {
  file: null,
  jobId: '',
  abort: null,
};

const TR_MEDIA_EXTS = [
  '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac',
  '.mp4', '.mov', '.webm', '.mkv', '.avi',
];
const TR_MEDIA_MAX_BYTES = 200 * 1024 * 1024;

function trMediaDragOver(ev) {
  ev.preventDefault();
  document.getElementById('tr-media-drop')?.classList.add('dragging');
}
function trMediaDragLeave(ev) {
  ev.preventDefault();
  document.getElementById('tr-media-drop')?.classList.remove('dragging');
}
function trMediaDrop(ev) {
  ev.preventDefault();
  document.getElementById('tr-media-drop')?.classList.remove('dragging');
  const f = ev.dataTransfer?.files?.[0];
  if (f) trMediaSetFile(f);
}
function trMediaFileSelected(ev) {
  const f = ev.target.files?.[0];
  if (f) trMediaSetFile(f);
  ev.target.value = '';
}

function trMediaSetFile(f) {
  const status = document.getElementById('tr-media-status');
  const ext = ('.' + (f.name.split('.').pop() || '')).toLowerCase();
  if (!TR_MEDIA_EXTS.includes(ext)) {
    if (status) {
      status.textContent = `Nicht unterstützter Dateityp ${ext}.`;
      status.classList.add('error');
    }
    return;
  }
  if (f.size > TR_MEDIA_MAX_BYTES) {
    if (status) {
      status.textContent = 'Datei zu groß (max. 200 MB).';
      status.classList.add('error');
    }
    return;
  }
  trMediaState.file = f;
  if (status) {
    status.classList.remove('error');
    status.textContent = `${f.name} · ${(f.size / 1024 / 1024).toFixed(1)} MB`;
  }
  document.getElementById('tr-media-translate-btn').disabled = false;
  trMediaClearJobPanel();
}

function trMediaClearJobPanel() {
  document.getElementById('tr-media-job')?.classList.add('hidden');
  document.getElementById('tr-media-progress-bar').style.width = '0%';
  document.getElementById('tr-media-downloads')?.classList.add('hidden');
  document.getElementById('tr-media-results')?.classList.add('hidden');
  document.getElementById('tr-media-segments').innerHTML = '';
}

function trMediaReset() {
  if (trMediaState.abort) {
    try { trMediaState.abort.abort(); } catch (_) {}
  }
  trMediaState.abort = null;
  trMediaState.jobId = '';
  trMediaState.file = null;
  document.getElementById('tr-media-translate-btn').disabled = true;
  trMediaClearJobPanel();
  const status = document.getElementById('tr-media-status');
  if (status) {
    status.textContent = '';
    status.classList.remove('error');
  }
}

async function trRunMediaTranslation() {
  const file = trMediaState.file;
  const status = document.getElementById('tr-media-status');
  const mode = document.getElementById('tr-media-mode')?.value || 'translate';
  if (!file) {
    if (status) { status.textContent = 'Wählen Sie zuerst eine Datei.'; status.classList.add('error'); }
    return;
  }
  if (mode === 'translate' && !trState.targetLang) {
    if (status) { status.textContent = 'Wählen Sie eine Zielsprache.'; status.classList.add('error'); }
    return;
  }

  const fd = new FormData();
  fd.append('file', file, file.name);
  // Empty target_lang triggers transcribe-only on the server.
  fd.append('target_lang', mode === 'translate' ? trState.targetLang : '');
  if (trState.sourceLangManual && trState.sourceLang) {
    fd.append('source_lang', trState.sourceLang);
  }
  if (trState.glossarySlug) fd.append('glossary', trState.glossarySlug);
  if (trState.model) fd.append('model', trState.model);

  document.getElementById('tr-media-translate-btn').disabled = true;
  status.textContent = 'Wird hochgeladen…';
  status.classList.remove('error');

  let job;
  try {
    const res = await fetch('/v1/translate/media', {
      method: 'POST',
      headers: trAuthHeaders(),
      body: fd,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    job = await res.json();
  } catch (e) {
    status.textContent = (e.message || 'Hochladen fehlgeschlagen').slice(0, 240);
    status.classList.add('error');
    document.getElementById('tr-media-translate-btn').disabled = false;
    return;
  }

  trMediaState.jobId = job.job_id;
  trMediaShowJobPanel(job, file.name);
  trMediaSubscribe(job.job_id);
  status.textContent = 'Wird verarbeitet…';
}

function trMediaShowJobPanel(job, filename) {
  const panel = document.getElementById('tr-media-job');
  panel.classList.remove('hidden');
  document.getElementById('tr-media-job-name').textContent = filename || job.filename || 'Medien';
  trMediaApplyJobState(job);
}

function trMediaApplyJobState(job) {
  const stateEl = document.getElementById('tr-media-job-state');
  const meta = document.getElementById('tr-media-job-meta');
  const bar = document.getElementById('tr-media-progress-bar');
  if (!stateEl || !meta || !bar) return;
  stateEl.classList.remove('running', 'done', 'error');
  switch (job.state) {
    case 'queued':  stateEl.textContent = 'In Warteschlange'; break;
    case 'running':
      stateEl.textContent = (job.stage === 'transcribe') ? 'Wird transkribiert'
        : (job.stage === 'translate') ? 'Wird übersetzt' : 'Läuft';
      stateEl.classList.add('running');
      break;
    case 'done':    stateEl.textContent = 'Fertig'; stateEl.classList.add('done'); break;
    case 'error':   stateEl.textContent = 'Fehler'; stateEl.classList.add('error'); break;
    default:        stateEl.textContent = job.state || '';
  }
  const pct = Math.max(0, Math.min(100, job.progress_pct || 0));
  bar.style.width = pct + '%';

  if (job.state === 'done') {
    const lines = [];
    if (typeof job.duration_s === 'number') lines.push(`${job.duration_s.toFixed(1)} s Audio`);
    const segCount = (job.segments || []).length;
    if (segCount) lines.push(`${segCount} Segment${segCount === 1 ? '' : 'e'}`);
    if (job.transcribe_model) lines.push(job.transcribe_model.split('/').pop());
    if (job.runs > 0) lines.push(`${job.runs} übersetzt`);
    meta.textContent = lines.join(' · ');
    trMediaRenderDownloads(job);
    trMediaRenderSegments(job.segments || []);
    document.getElementById('tr-media-translate-btn').disabled = false;
    const status = document.getElementById('tr-media-status');
    if (status) { status.textContent = 'Fertig.'; status.classList.remove('error'); }
    trHistoryRefresh();
  } else if (job.state === 'error') {
    meta.textContent = job.error || 'Unbekannter Fehler';
    document.getElementById('tr-media-translate-btn').disabled = false;
    const status = document.getElementById('tr-media-status');
    if (status) {
      status.textContent = (job.error || 'Fehlgeschlagen').slice(0, 240);
      status.classList.add('error');
    }
  } else {
    if (job.stage === 'translate' && job.runs_total) {
      meta.textContent = `Segmente werden übersetzt… ${job.runs_done}/${job.runs_total}`;
    } else if (job.stage === 'transcribe') {
      meta.textContent = 'Audio wird transkribiert…';
    } else {
      meta.textContent = 'Wird vorbereitet…';
    }
  }
}

const TR_MEDIA_DL_LABELS = {
  bilingual_txt: 'Zweisprachig TXT',
  transcript_txt: 'Transkript TXT',
  transcript_srt: 'Transkript SRT',
  transcript_vtt: 'Transkript VTT',
  translation_txt: 'Übersetzung TXT',
  translation_srt: 'Übersetzung SRT',
  translation_vtt: 'Übersetzung VTT',
};
const TR_MEDIA_DL_ORDER = [
  'bilingual_txt',
  'translation_srt', 'translation_vtt', 'translation_txt',
  'transcript_srt', 'transcript_vtt', 'transcript_txt',
];

function trMediaRenderDownloads(job) {
  const wrap = document.getElementById('tr-media-downloads');
  if (!wrap) return;
  const files = job.output_files || {};
  const buttons = TR_MEDIA_DL_ORDER
    .filter(k => files[k])
    .map(k => `<button class="tr-media-dl-btn" data-format="${k}">${TR_MEDIA_DL_LABELS[k]}</button>`)
    .join('');
  if (!buttons) {
    wrap.classList.add('hidden');
    return;
  }
  wrap.innerHTML = buttons;
  wrap.classList.remove('hidden');
  wrap.querySelectorAll('.tr-media-dl-btn').forEach(btn => {
    btn.onclick = () => trMediaDownload(btn.dataset.format, files[btn.dataset.format]);
  });
}

function trMediaRenderSegments(segments) {
  const wrap = document.getElementById('tr-media-results');
  const list = document.getElementById('tr-media-segments');
  if (!wrap || !list) return;
  if (!segments.length) { wrap.classList.add('hidden'); return; }
  const hasTranslation = segments.some(s => s.translation);
  const srcLang = trState.sourceLang || trState.detected?.lang || '';
  const tgtLang = trState.targetLang || 'en';

  // Header speak-all buttons.
  const speakBtns = document.getElementById('tr-media-speak-btns');
  if (speakBtns) {
    const svgSpk = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 010 7.07"/><path d="M19.07 4.93a10 10 0 010 14.14"/></svg>`;
    let html = `<button class="tr-media-speak-all-btn" id="tr-media-speak-transcript" title="Gesamtes Transkript vorlesen">${svgSpk} Transkript</button>`;
    if (hasTranslation) {
      html += `<button class="tr-media-speak-all-btn" id="tr-media-speak-translation" title="Gesamte Übersetzung vorlesen">${svgSpk} Übersetzung</button>`;
    }
    speakBtns.innerHTML = html;
    const allTranscript = segments.map(s => s.text || '').join(' ');
    const allTranslation = segments.map(s => s.translation || '').join(' ');
    document.getElementById('tr-media-speak-transcript').onclick = () =>
      _trMediaSpeak('media-all-transcript', allTranscript, srcLang,
        document.getElementById('tr-media-speak-transcript'));
    if (hasTranslation) {
      document.getElementById('tr-media-speak-translation').onclick = () =>
        _trMediaSpeak('media-all-translation', allTranslation, tgtLang,
          document.getElementById('tr-media-speak-translation'));
    }
  }

  list.innerHTML = segments.map((s, i) => {
    const t = trFormatTimeShort(s.start || 0);
    const src = escapeHtml(s.text || '');
    const svgSpk = `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 010 7.07"/></svg>`;
    if (!hasTranslation) {
      return `<div class="tr-media-seg no-translation">
        <div class="tr-media-seg-time">${t}</div>
        <div class="tr-media-seg-src">${src}</div>
        <button class="tr-media-seg-speak" data-seg="${i}" data-side="src" title="Vorlesen">${svgSpk}</button>
      </div>`;
    }
    const tgt = escapeHtml(s.translation || '');
    return `<div class="tr-media-seg">
      <div class="tr-media-seg-time">${t}</div>
      <div class="tr-media-seg-src">${src}<button class="tr-media-seg-speak" data-seg="${i}" data-side="src" title="Quelle vorlesen">${svgSpk}</button></div>
      <div class="tr-media-seg-tgt">${tgt}<button class="tr-media-seg-speak" data-seg="${i}" data-side="tgt" title="Übersetzung vorlesen">${svgSpk}</button></div>
    </div>`;
  }).join('');

  // Wire per-segment speak buttons.
  list.querySelectorAll('.tr-media-seg-speak').forEach(btn => {
    btn.onclick = () => {
      const i = parseInt(btn.dataset.seg);
      const side = btn.dataset.side;
      const seg = segments[i];
      if (!seg) return;
      const text = side === 'src' ? (seg.text || '') : (seg.translation || '');
      const lang = side === 'src' ? srcLang : tgtLang;
      _trMediaSpeak(`media-seg-${i}-${side}`, text, lang, btn);
    };
  });

  wrap.classList.remove('hidden');
}

// Shared TTS play for media segments — reuses the same _trTtsPlay plumbing.
function _trMediaSpeak(cacheKey, text, lang, btn) {
  if (!text) return;
  // Stop any currently playing audio first.
  if (_trTtsPlaying) { _trTtsStop(); }
  // Use the generic play function with a dynamic cache slot.
  _trTtsCache[cacheKey] = _trTtsCache[cacheKey] || null;
  _trTtsPlay(cacheKey, text, lang, btn);
}

function trFormatTimeShort(t) {
  // mm:ss.ms — used in the segment list.
  const m = Math.floor(t / 60);
  const s = t % 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

async function trMediaDownload(format, filename) {
  if (!trMediaState.jobId) return;
  const status = document.getElementById('tr-media-status');
  try {
    const url = `/v1/translate/jobs/${encodeURIComponent(trMediaState.jobId)}/result?format=${encodeURIComponent(format)}`;
    const resp = await fetch(url, { headers: trAuthHeaders() });
    if (!resp.ok) {
      const err = await resp.text().catch(() => '');
      throw new Error(`HTTP ${resp.status} ${err.slice(0, 120)}`);
    }
    const blob = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objUrl;
    a.download = filename || `translation-${format}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(objUrl), 60000);
  } catch (e) {
    if (status) {
      status.textContent = `Herunterladen fehlgeschlagen: ${e.message}`;
      status.classList.add('error');
    }
  }
}

async function trMediaSubscribe(jobId) {
  // Same fetch+ReadableStream SSE trick as trDocSubscribe.
  const ctrl = new AbortController();
  trMediaState.abort = ctrl;
  let resp;
  try {
    resp = await fetch(`/v1/translate/jobs/${encodeURIComponent(jobId)}`, {
      headers: { ...trAuthHeaders(), 'Accept': 'text/event-stream' },
      signal: ctrl.signal,
    });
  } catch (e) {
    if (e.name !== 'AbortError') {
      const status = document.getElementById('tr-media-status');
      if (status) { status.textContent = `SSE fehlgeschlagen: ${e.message}`; status.classList.add('error'); }
    }
    return;
  }
  if (!resp.ok) {
    const status = document.getElementById('tr-media-status');
    if (status) { status.textContent = `SSE: HTTP ${resp.status}`; status.classList.add('error'); }
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let evType = null;
  const dispatch = (type, data) => {
    if (!type || !data) return;
    let job;
    try { job = JSON.parse(data); } catch (_) { return; }
    trMediaApplyJobState(job);
    if (type === 'done' || type === 'error') {
      try { ctrl.abort(); } catch (_) {}
      trMediaState.abort = null;
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
    if (e.name !== 'AbortError') {
      const status = document.getElementById('tr-media-status');
      if (status) { status.textContent = `SSE-Fehler: ${e.message}`; status.classList.add('error'); }
    }
  } finally {
    if (trMediaState.abort === ctrl) trMediaState.abort = null;
  }
}

/* ─── Translation History (inline, per tab) ───────────────────────────── */

let _trHistoryEntries = [];          // raw rows from /v1/translate/history
let _trHistoryIsAdmin = false;
let _trHistoryCurrentUser = '';
const _trHistoryUiState = {           // per-tab search + sort
  text:     { search: '', sort: 'date_desc' },
  document: { search: '', sort: 'date_desc' },
  media:    { search: '', sort: 'date_desc' },
  live:     { search: '', sort: 'date_desc' },
};
// Inline expanded entries (id → bool) so clicking a row opens detail in place.
const _trHistoryExpanded = {};

function trHistoryOnSearchInput(tab) {
  const el = document.querySelector(`.tr-history-search[data-search-for="${tab}"]`);
  _trHistoryUiState[tab].search = (el?.value || '').trim().toLowerCase();
  _trHistoryRenderTab(tab);
}

function trHistoryOnSortChange(tab) {
  const el = document.querySelector(`.tr-history-sort[data-sort-for="${tab}"]`);
  _trHistoryUiState[tab].sort = el?.value || 'date_desc';
  _trHistoryRenderTab(tab);
}

async function trHistoryRefresh() {
  // Fetch once, render all four tabs from the same response.
  try {
    const data = await API.get('/v1/translate/history');
    _trHistoryEntries = data.entries || [];
    _trHistoryIsAdmin = !!data.is_admin;
    _trHistoryCurrentUser = data.current_user_id || '';
  } catch (e) {
    _trHistoryEntries = [];
  }
  ['text', 'document', 'media', 'live'].forEach(_trHistoryRenderTab);
}

function _trHistoryEntriesForTab(tab) {
  // Map UI tab name to history `type` value.
  const typeFor = { text: 'text', document: 'document', media: 'media', live: 'live' };
  const t = typeFor[tab];
  return _trHistoryEntries.filter(e => e.type === t);
}

function _trHistoryFilterAndSort(entries, tab) {
  const ui = _trHistoryUiState[tab];
  let out = entries;
  if (ui.search) {
    const q = ui.search;
    out = out.filter(e => {
      const hay = `${e.title || ''} ${e.source_lang || ''} ${e.target_lang || ''}`.toLowerCase();
      return hay.includes(q);
    });
  }
  out = out.slice();
  switch (ui.sort) {
    case 'date_asc':  out.sort((a, b) => (a.created_at || 0) - (b.created_at || 0)); break;
    case 'name_asc':  out.sort((a, b) => (a.title || '').localeCompare(b.title || '')); break;
    case 'name_desc': out.sort((a, b) => (b.title || '').localeCompare(a.title || '')); break;
    default:          out.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  }
  return out;
}

function _trHistoryRenderTab(tab) {
  const list = document.getElementById(`tr-history-${tab}-list`);
  if (!list) return;
  const entries = _trHistoryFilterAndSort(_trHistoryEntriesForTab(tab), tab);
  if (!entries.length) {
    const ui = _trHistoryUiState[tab];
    list.innerHTML = `<div class="tr-history-empty">${ui.search ? 'Keine Treffer.' : 'Noch kein Verlauf.'}</div>`;
    return;
  }
  list.innerHTML = entries.map(e => _trHistoryRowHtml(e, tab)).join('');
  if (typeof feedbackHydrateState === 'function') feedbackHydrateState('translation', '');
  // Mount a favourite-star per row (simple on/off toggle); item_id = entry id.
  if (window.Favourites?.mount) {
    list.querySelectorAll('.tr-fav-star').forEach(span => {
      if (span.dataset.mounted) return;
      span.dataset.mounted = '1';
      window.Favourites.mount(span, {
        item_type: 'translation', item_id: span.dataset.favEntry,
        agent_id: 'main', simple: true,
      });
    });
  }
}

function _trHistoryRowHtml(entry, tab) {
  const date = new Date((entry.created_at || 0) * 1000)
    .toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const langs = [entry.source_lang, entry.target_lang].filter(Boolean).join(' → ');
  const isOther = _trHistoryIsAdmin && entry.user_id && entry.user_id !== _trHistoryCurrentUser;
  const ownerBadge = isOther
    ? `<span class="tr-history-owner" title="Anderer Benutzer">${escapeHtml(entry.user_id)}</span>`
    : '';
  const expanded = !!_trHistoryExpanded[entry.id];
  const detail = expanded ? _trHistoryDetailHtml(entry) : '';
  return `<div class="tr-history-row${expanded ? ' expanded' : ''}" data-tr-history="${escapeHtml(entry.id)}">
    <div class="tr-history-row-head" onclick="trHistoryToggle('${escapeHtml(entry.id)}','${tab}')">
      <span class="tr-history-row-title">${escapeHtml(entry.title || '—')}</span>
      <span class="tr-history-row-meta">${langs ? escapeHtml(langs) + ' · ' : ''}${escapeHtml(date)}</span>
      ${ownerBadge}
      <span class="tr-fav-star" data-fav-entry="${escapeHtml(entry.id)}" onclick="event.stopPropagation()"></span>
      <button class="tr-history-row-del" title="Löschen"
        onclick="event.stopPropagation();trHistoryDelete('${escapeHtml(entry.id)}')">×</button>
      <span onclick="event.stopPropagation()">${typeof renderFeedbackControl === 'function' ? renderFeedbackControl('translation', entry.id, '', entry.title || '') : ''}</span>
    </div>
    ${detail}
  </div>`;
}

function _trHistoryDetailHtml(entry) {
  let result = {};
  try { result = JSON.parse(entry.result_json || '{}'); } catch (_) {}
  if (entry.type === 'text')      return _trHistoryDetailText(entry, result);
  if (entry.type === 'document')  return _trHistoryDetailDocument(entry, result);
  if (entry.type === 'media')     return _trHistoryDetailMedia(entry, result);
  if (entry.type === 'live')      return _trHistoryDetailLive(entry, result);
  return '';
}

function _trHistoryDetailText(entry, result) {
  const src = result.text || '';
  const tgt = result.translation || '';
  return `<div class="tr-history-detail-inline">
    <div class="tr-history-text-cols">
      <div class="tr-history-text-col">
        <div class="tr-history-text-label">Quelle</div>
        <div class="tr-history-text-body">${escapeHtml(src) || '<em>—</em>'}</div>
      </div>
      <div class="tr-history-text-col">
        <div class="tr-history-text-label">Übersetzung</div>
        <div class="tr-history-text-body">${escapeHtml(tgt) || '<em>—</em>'}</div>
      </div>
    </div>
    <div class="tr-history-detail-actions">
      <button class="tr-btn tr-btn-primary" onclick="trHistoryRestoreText('${escapeHtml(entry.id)}')">
        In Editor laden
      </button>
    </div>
  </div>`;
}

// File-link factory — auth-aware (browser <a> can't carry Bearer headers, so
// every link routes through trHistoryOpenFile which fetches as blob first).
// Uses data- attributes + a delegated click handler — embedding the URL and
// filename in the onclick string ran into HTML-attribute quoting issues
// (JSON.stringify emits double-quotes that closed the onclick="..." early,
// so the browser fell back to following the href and showed the raw 401).
function _trHistoryFileLink(entryId, which, label, fileName, icon) {
  if (!fileName) return '';
  const url = `/v1/translate/history/${encodeURIComponent(entryId)}/file?which=${encodeURIComponent(which)}`;
  return `<a class="tr-history-file" href="${url}"
      data-tr-file="1"
      data-tr-url="${escapeHtml(url)}"
      data-tr-name="${escapeHtml(fileName)}">
    <span class="tr-history-file-icon">${icon}</span>
    <span class="tr-history-file-meta"><span class="tr-history-file-label">${escapeHtml(label)}</span><span class="tr-history-file-name">${escapeHtml(fileName)}</span></span>
  </a>`;
}

// One delegated listener catches clicks on any history file link, no matter
// when it was rendered. Installed once on first translation-view load.
let _trHistoryFileClickInstalled = false;
function _trHistoryInstallFileClickHandler() {
  if (_trHistoryFileClickInstalled) return;
  _trHistoryFileClickInstalled = true;
  document.addEventListener('click', (ev) => {
    const a = ev.target.closest('a[data-tr-file="1"]');
    if (!a) return;
    ev.preventDefault();
    ev.stopPropagation();
    trHistoryOpenFile(null, a.dataset.trUrl, a.dataset.trName);
  });
}

function _trHistoryDetailDocument(entry, result) {
  // Only show the source link when the durable artifact copy exists. Falling
  // back to result.filename (the original upload name) shows a button that
  // 404s — the /tmp upload is gone after the worker finishes. result.
  // source_filename is populated by _tr_history_save_job IFF the copy
  // succeeded, so it's the right gate.
  const srcName = result.source_filename || '';
  const outName = result.output_filename || '';
  return `<div class="tr-history-detail-inline">
    <div class="tr-history-files">
      ${_trHistoryFileLink(entry.id, 'source', 'Quelle', srcName, '📄')}
      ${_trHistoryFileLink(entry.id, 'output', 'Übersetzt', outName, '📑')}
    </div>
  </div>`;
}

function _trHistoryDetailMedia(entry, result) {
  // Same rule as document: gate on source_filename only — that's the marker
  // that the artifact copy succeeded. result.filename is just the upload
  // name, useless for serving.
  const srcName = result.source_filename || '';
  const outFiles = result.output_files || {};
  const formatLabels = {
    transcript_txt: 'Transkript (TXT)',
    transcript_srt: 'Transkript (SRT)',
    transcript_vtt: 'Transkript (VTT)',
    translation_txt: 'Übersetzung (TXT)',
    translation_srt: 'Übersetzung (SRT)',
    translation_vtt: 'Übersetzung (VTT)',
    bilingual_txt: 'Zweisprachig (TXT)',
  };
  const fileLinks = Object.keys(outFiles)
    .filter(k => k !== 'primary')
    .map(k => _trHistoryFileLink(entry.id, k, formatLabels[k] || k, outFiles[k], '🗒'))
    .join('');
  const transcript = result.transcript || (result.segments || []).map(s => s.text || '').filter(Boolean).join('\n');
  return `<div class="tr-history-detail-inline">
    <div class="tr-history-files">
      ${_trHistoryFileLink(entry.id, 'source', 'Quellmedien', srcName, '🎬')}
      ${fileLinks}
    </div>
    ${transcript ? `<div class="tr-history-transcript"><div class="tr-history-text-label">Transkript</div><pre>${escapeHtml(transcript)}</pre></div>` : ''}
  </div>`;
}

function _trHistoryDetailLive(entry, result) {
  const segs = result.segments || [];
  const summary = segs.map(s => s.translation || s.text || '').filter(Boolean).join(' ').slice(0, 600);
  const outFiles = result.output_files || {};
  const links = Object.keys(outFiles).map(k => {
    const label = k === 'srt' ? 'SRT-Untertitel' : k === 'txt' ? 'Klartext' : k.toUpperCase();
    const icon = k === 'srt' ? '🎞' : '📝';
    return _trHistoryFileLink(entry.id, k, label, outFiles[k], icon);
  }).join('');
  return `<div class="tr-history-detail-inline">
    ${summary ? `<div class="tr-history-transcript"><div class="tr-history-text-label">Transkript-Zusammenfassung</div><div class="tr-history-text-body">${escapeHtml(summary)}${summary.length === 600 ? '…' : ''}</div></div>` : ''}
    <div class="tr-history-files">${links || '<em style="font-size:12px;color:var(--text-400)">Keine gespeicherten Dateien.</em>'}</div>
  </div>`;
}

function trHistoryToggle(id, tab) {
  _trHistoryExpanded[id] = !_trHistoryExpanded[id];
  _trHistoryRenderTab(tab);
}

// Open the translation history entry `entryId`: resolve its type → switch to
// the right tab → scroll the row into view + flash it. Reusable by the feedback
// jump button and favourites. Polls because history loads async on view mount.
// `entry.type` is text|document|media|live; the media type lives in the UI tab
// labelled 'audio', so map it. Returns true once handled.
function trJumpToHistoryEntry(entryId, tries) {
  tries = tries || 0;
  const entry = _trHistoryEntries.find(e => String(e.id) === String(entryId));
  if (!entry) {
    if (tries < 25) { setTimeout(() => trJumpToHistoryEntry(entryId, tries + 1), 200); }
    else if (typeof showToast === 'function') showToast('Übersetzung nicht im Verlauf gefunden', true);
    return false;
  }
  const uiTab = entry.type === 'media' ? 'audio' : entry.type;  // panel id mapping
  if (typeof trSwitchTab === 'function') trSwitchTab(uiTab);
  // Re-render that tab's history list (it may have been collapsed/filtered) and
  // scroll after the DOM settles.
  if (typeof _trHistoryRenderTab === 'function') _trHistoryRenderTab(entry.type);
  setTimeout(() => {
    const row = document.querySelector(`[data-tr-history="${String(entryId).replace(/(["\\])/g, '\\$1')}"]`);
    if (row) {
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      row.classList.add('fb-flash');
      setTimeout(() => row.classList.remove('fb-flash'), 1600);
    }
  }, 60);
  return true;
}

function trHistoryRestoreText(id) {
  const entry = _trHistoryEntries.find(e => e.id === id);
  if (!entry) return;
  let result = {};
  try { result = JSON.parse(entry.result_json || '{}'); } catch (_) {}
  trSwitchTab('text');
  const src = document.getElementById('tr-source-textarea');
  const tgt = document.getElementById('tr-target-output');
  if (src) src.value = result.text || '';
  if (tgt) {
    tgt.textContent = result.translation || '';
    tgt.dataset.translation = result.translation || '';
  }
  if (entry.source_lang && entry.source_lang !== 'auto') {
    trState.sourceLang = entry.source_lang;
    trRenderSourcePill();
  }
  if (entry.target_lang) {
    trState.targetLang = entry.target_lang;
    trRenderTargetPill();
  }
  trUpdateCounts();
}

async function trHistoryOpenFile(ev, url, suggestedName) {
  // Browser <a href> can't carry the Authorization header, so swap the
  // navigation for an authenticated fetch + blob URL. PDFs/images render
  // inline; other types prompt download with the original filename.
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  try {
    const resp = await fetch(url, { headers: trAuthHeaders() });
    if (!resp.ok) {
      showToast && showToast(`Öffnen fehlgeschlagen (${resp.status})`, true);
      return false;
    }
    const blob = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    const win = window.open(objUrl, '_blank');
    if (!win) {
      // Popup blocked — fall back to download via anchor.
      const a = document.createElement('a');
      a.href = objUrl;
      a.download = suggestedName || 'file';
      a.click();
    }
    // Revoke after a minute — long enough for PDF viewers / large files to
    // finish loading without leaking memory if the user keeps the tab open.
    setTimeout(() => URL.revokeObjectURL(objUrl), 60000);
  } catch (e) {
    showToast && showToast('Öffnen fehlgeschlagen: ' + (e.message || e), true);
  }
  return false;
}

async function trHistoryDelete(id) {
  try {
    await API.del(`/v1/translate/history/${encodeURIComponent(id)}`);
    _trHistoryEntries = _trHistoryEntries.filter(e => e.id !== id);
    delete _trHistoryExpanded[id];
    ['text', 'document', 'media', 'live'].forEach(_trHistoryRenderTab);
  } catch (e) {
    showToast && showToast('Löschen fehlgeschlagen: ' + e.message, true);
  }
}
