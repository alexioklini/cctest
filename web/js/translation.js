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
  glossaries: [],          // cached list from /v1/translate/glossaries
  modelOptions: [],        // mistral models pulled from /v1/models
  detectTimer: null,
  inflightAbort: null,
  // Glossary modal state — null = list view, object = editing form.
  modalEditing: null,
};

// Curated language list — covers what a German bank realistically translates
// in/out of. Easy to extend without touching server-side LANG_NAMES.
const TR_LANGS = [
  ['en', 'English'],
  ['de', 'German'],
  ['fr', 'French'],
  ['es', 'Spanish'],
  ['it', 'Italian'],
  ['pt', 'Portuguese'],
  ['nl', 'Dutch'],
  ['pl', 'Polish'],
  ['cs', 'Czech'],
  ['sk', 'Slovak'],
  ['hu', 'Hungarian'],
  ['ro', 'Romanian'],
  ['tr', 'Turkish'],
  ['el', 'Greek'],
  ['sv', 'Swedish'],
  ['da', 'Danish'],
  ['no', 'Norwegian'],
  ['fi', 'Finnish'],
  ['ru', 'Russian'],
  ['uk', 'Ukrainian'],
  ['ja', 'Japanese'],
  ['zh', 'Chinese'],
  ['ko', 'Korean'],
  ['ar', 'Arabic'],
  ['hi', 'Hindi'],
];
const TR_LANG_NAMES = Object.fromEntries(TR_LANGS);

function trLangLabel(code) {
  if (!code) return 'Auto-detect';
  return TR_LANG_NAMES[code] || code.toUpperCase();
}

/* ─── Init ─────────────────────────────────────────────────── */

async function loadTranslationView() {
  trRenderTargetPill();
  trRenderSourcePill();
  await Promise.all([trLoadGlossaries(), trLoadModels()]);
  trUpdateCounts();
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
  sel.innerHTML = '<option value="">— none —</option>' +
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

async function trLoadModels() {
  // Pull the active models list and surface only Mistral entries — translation
  // is Mistral-only by design (prompt + glossary tuning verified there).
  try {
    const data = await API.get('/v1/models');
    const all = data.models || data || [];
    const mistrals = (Array.isArray(all) ? all : [])
      .filter(m => {
        const id = (m.id || m).toLowerCase();
        return id.includes('mistral') || id.includes('voxtral');
      })
      .map(m => ({ id: m.id || m, name: m.name || m.display_name || m.id || m }));
    trState.modelOptions = mistrals;
  } catch (e) {
    console.warn('trLoadModels failed', e);
    trState.modelOptions = [];
  }
  const sel = document.getElementById('tr-model-select');
  if (!sel) return;
  if (!trState.modelOptions.length) {
    sel.innerHTML = '<option value="">(server default)</option>';
    return;
  }
  sel.innerHTML = '<option value="">(server default)</option>' +
    trState.modelOptions
      .filter(m => !/voxtral/i.test(m.id))
      .map(m => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`)
      .join('');
  sel.value = trState.model || '';
  sel.onchange = () => { trState.model = sel.value; };
}

/* ─── Tabs ─────────────────────────────────────────────────── */

function trSwitchTab(tab) {
  // Audio / live still disabled; document is now real.
  if (tab !== 'text' && tab !== 'document') return;
  document.querySelectorAll('.tr-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  const text = document.getElementById('tr-panel-text');
  const doc = document.getElementById('tr-panel-document');
  if (text) text.style.display = (tab === 'text') ? '' : 'none';
  if (doc) doc.style.display = (tab === 'document') ? '' : 'none';
}

/* ─── Source / target language pills ──────────────────────── */

function trRenderSourcePill() {
  const label = document.getElementById('tr-source-label');
  if (!label) return;
  if (trState.sourceLang) {
    label.textContent = trLangLabel(trState.sourceLang) +
      (trState.sourceLangManual ? '' : ' (auto)');
  } else {
    label.textContent = 'Auto-detect';
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
  const items = [['', 'Auto-detect'], null, ...TR_LANGS];
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
      out.innerHTML = '<div class="tr-placeholder">Translation will appear here.</div>';
      trUpdateCounts();
    }
  }
}

function trSetGlossary(slug) {
  trState.glossarySlug = slug || '';
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
  if (sc && src) sc.textContent = `${src.value.length} chars`;
  if (tc && out) {
    const len = (out.dataset.translation || '').length;
    tc.textContent = `${len} chars`;
  }
}

function trClearSource() {
  const src = document.getElementById('tr-source-textarea');
  if (src) src.value = '';
  const out = document.getElementById('tr-target-output');
  if (out) {
    out.innerHTML = '<div class="tr-placeholder">Translation will appear here.</div>';
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
  badge.textContent = `Detected ${trLangLabel(d.lang)} · ${pct}%`;
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
    status.textContent = 'Source text is empty.';
    status.classList.add('error');
    return;
  }
  if (!trState.targetLang) {
    status.textContent = 'Pick a target language.';
    status.classList.add('error');
    return;
  }

  status.textContent = 'Translating…';
  status.classList.remove('error');
  btn.disabled = true;
  out.classList.add('translating');
  out.innerHTML = '<div class="tr-placeholder">Translating…</div>';
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
      status.textContent = `Source already in ${trLangLabel(trState.targetLang)} — no translation needed.`;
    } else {
      const m = r.model ? ` · ${r.model}` : '';
      status.textContent = `Done in ${dt}s${m}`;
    }
    trUpdateCounts();
  } catch (e) {
    out.classList.remove('translating');
    out.innerHTML = '<div class="tr-placeholder">Translation failed.</div>';
    status.textContent = (e.message || 'Translation failed').slice(0, 240);
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
      status.textContent = 'Copied.';
      setTimeout(() => { if (status.textContent === 'Copied.') status.textContent = prev; }, 1500);
    }
  });
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
    title.textContent = trState.modalEditing.slug ? 'Edit glossary' : 'New glossary';
    body.innerHTML = trGlossaryFormHtml(trState.modalEditing);
    trBindGlossaryFormEvents();
    return;
  }

  title.textContent = 'Glossaries';
  // Fresh fetch so the modal always shows current state.
  let list = [];
  try {
    const data = await API.get('/v1/translate/glossaries');
    list = data.glossaries || [];
    trState.glossaries = list;
  } catch (e) {
    body.innerHTML = `<div class="tr-gloss-empty">Failed to load glossaries: ${escapeHtml(e.message || '')}</div>`;
    return;
  }
  const newBtn = `
    <div style="margin-bottom:14px;display:flex;justify-content:flex-end">
      <button class="tr-btn tr-btn-primary" onclick="trEditGlossary('')">+ New glossary</button>
    </div>`;
  if (!list.length) {
    body.innerHTML = newBtn +
      '<div class="tr-gloss-empty">No glossaries yet. Create one to start enforcing bank-specific terminology.</div>';
    return;
  }
  body.innerHTML = newBtn + '<div class="tr-gloss-list">' +
    list.map(g => `
      <div class="tr-gloss-card">
        <div class="tr-gloss-card-main" style="flex:1;min-width:0">
          <div class="tr-gloss-card-name">${escapeHtml(g.name)}</div>
          ${g.description ? `<div class="tr-gloss-card-desc">${escapeHtml(g.description)}</div>` : ''}
          <div class="tr-gloss-card-meta">
            ${g.source ? `${trLangLabel(g.source)} → ${trLangLabel(g.target)} · ` : ''}${g.entry_count} entries${g.do_not_translate_count ? ` · ${g.do_not_translate_count} do-not-translate` : ''}
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="tr-btn" onclick="trEditGlossary('${escapeHtml(g.slug)}')">Edit</button>
          <button class="tr-btn tr-btn-danger" onclick="trDeleteGlossary('${escapeHtml(g.slug)}','${escapeHtml(g.name)}')">Delete</button>
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
      alert('Failed to load glossary: ' + (e.message || ''));
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
      <input type="text" class="tr-gloss-entry-src" placeholder="Source term"
             value="${escapeHtml(e.src || '')}">
      <input type="text" class="tr-gloss-entry-tgt" placeholder="Target term"
             value="${escapeHtml(e.tgt || '')}">
      <button class="tr-gloss-entry-remove" type="button" data-idx="${i}" title="Remove">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
  `).join('');

  return `
    <form class="tr-gloss-form" id="tr-gloss-form" onsubmit="return false">
      <div class="tr-gloss-row">
        <div class="tr-gloss-field">
          <label>Name</label>
          <input type="text" id="tr-gloss-name" value="${escapeHtml(g.name)}" required placeholder="e.g. Bank DE-EN">
        </div>
        <div class="tr-gloss-field">
          <label>Description</label>
          <input type="text" id="tr-gloss-desc" value="${escapeHtml(g.description)}" placeholder="optional">
        </div>
      </div>
      <div class="tr-gloss-row">
        <div class="tr-gloss-field">
          <label>Source language (hint)</label>
          <select id="tr-gloss-source">${langOptions(g.source)}</select>
        </div>
        <div class="tr-gloss-field">
          <label>Target language (hint)</label>
          <select id="tr-gloss-target">${langOptions(g.target)}</select>
        </div>
      </div>

      <div class="tr-gloss-field">
        <label>Term mappings</label>
        <div class="tr-gloss-entries" id="tr-gloss-entries">
          <div class="tr-gloss-entries-header">
            <div>Source</div><div>Target</div><div></div>
          </div>
          ${entries}
          <div class="tr-gloss-add-row">
            <button class="tr-btn" type="button" onclick="trAddGlossaryEntry()">+ Add term</button>
          </div>
        </div>
      </div>

      <div class="tr-gloss-field">
        <label>Do not translate (one term per line)</label>
        <textarea id="tr-gloss-dnt" rows="3"
          placeholder="BaFin&#10;DZ Bank&#10;MaRisk">${escapeHtml((g.do_not_translate || []).join('\n'))}</textarea>
      </div>

      <div class="tr-gloss-form-actions">
        ${g.slug ? `<button class="tr-btn tr-btn-danger" type="button" onclick="trDeleteGlossary('${escapeHtml(g.slug)}','${escapeHtml(g.name)}')">Delete</button>` : ''}
        <div class="tr-btn-spacer"></div>
        <button class="tr-btn" type="button" onclick="trCancelGlossaryEdit()">Cancel</button>
        <button class="tr-btn tr-btn-primary" type="button" onclick="trSaveGlossary()">Save</button>
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
    alert('Name is required.');
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
    alert('Save failed: ' + (e.message || ''));
  }
}

async function trDeleteGlossary(slug, name) {
  if (!slug) return;
  if (!confirm(`Delete glossary "${name || slug}"?`)) return;
  try {
    await API.del(`/v1/translate/glossaries/${encodeURIComponent(slug)}`);
    if (trState.glossarySlug === slug) trState.glossarySlug = '';
    trState.modalEditing = null;
    trRenderGlossariesModal();
    await trLoadGlossaries();
  } catch (e) {
    alert('Delete failed: ' + (e.message || ''));
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
      status.textContent = `Unsupported file type ${ext}. Use ${TR_DOC_EXTS.join(', ')}.`;
      status.classList.add('error');
    }
    return;
  }
  if (f.size > TR_DOC_MAX_BYTES) {
    if (status) {
      status.textContent = 'File too large (max 50 MB).';
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
    if (status) { status.textContent = 'Pick a file first.'; status.classList.add('error'); }
    return;
  }
  if (!trState.targetLang) {
    if (status) { status.textContent = 'Pick a target language.'; status.classList.add('error'); }
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

  document.getElementById('tr-doc-translate-btn').disabled = true;
  status.textContent = 'Uploading…';
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
    status.textContent = (e.message || 'Upload failed').slice(0, 240);
    status.classList.add('error');
    document.getElementById('tr-doc-translate-btn').disabled = false;
    return;
  }

  trDocState.jobId = job.job_id;
  trDocShowJobPanel(job, file.name);
  trDocSubscribe(job.job_id);
  status.textContent = 'Translating…';
}

function trDocShowJobPanel(job, filename) {
  const panel = document.getElementById('tr-doc-job');
  panel.classList.remove('hidden');
  document.getElementById('tr-doc-job-name').textContent = filename || job.filename || 'document';
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
      stateEl.textContent = 'Queued'; break;
    case 'running':
      stateEl.textContent = 'Translating';
      stateEl.classList.add('running');
      break;
    case 'done':
      stateEl.textContent = 'Done';
      stateEl.classList.add('done');
      break;
    case 'error':
      stateEl.textContent = 'Error';
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
      `${job.runs} segment${job.runs === 1 ? '' : 's'} translated`,
    ];
    if (job.model) lines.push(job.model);
    if (job.fallback) lines.push('PDF converted to DOCX');
    if (job.noop) lines.push('source already in target language — copied');
    meta.textContent = lines.join(' · ');
    dl?.classList.remove('hidden');
    const status = document.getElementById('tr-doc-status');
    if (status) { status.textContent = 'Done.'; status.classList.remove('error'); }
  } else if (job.state === 'error') {
    meta.textContent = job.error || 'Unknown error';
    dl?.classList.add('hidden');
    const status = document.getElementById('tr-doc-status');
    if (status) {
      status.textContent = (job.error || 'Translation failed').slice(0, 240);
      status.classList.add('error');
    }
    document.getElementById('tr-doc-translate-btn').disabled = false;
  } else {
    if (job.runs_total) {
      meta.textContent = `${job.runs_done} / ${job.runs_total} segments · ${pct.toFixed(0)}%`;
    } else {
      meta.textContent = 'Preparing…';
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
      if (status) { status.textContent = `SSE failed: ${e.message}`; status.classList.add('error'); }
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
      if (status) { status.textContent = `SSE error: ${e.message}`; status.classList.add('error'); }
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
      status.textContent = `Download failed: ${e.message}`;
      status.classList.add('error');
    }
  }
}
