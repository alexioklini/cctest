// panels_artifacts.js — attachments fullview, artifacts, memory/knowledge graph. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

function collectChatAttachments() {
  const chat = state.activeChat;
  if (!chat) return [];
  const attachments = [];
  for (let i = 0; i < chat.messages.length; i++) {
    const msg = chat.messages[i];
    if (msg.role !== 'human' && msg.role !== 'user') continue;
    // From msg.images (legacy)
    if (msg.images?.length) {
      for (const img of msg.images) {
        const url = img.preview || (img.data ? `data:${img.type || 'image/png'};base64,${img.data}` : null);
        if (url) attachments.push({ url, name: img.name || 'Image', isImage: true, msgIndex: i });
      }
    }
    // From msg.files (unified path — all file types)
    if (msg.files?.length) {
      for (const f of msg.files) {
        const isImg = f.type?.startsWith('image/');
        if (f.preview) {
          attachments.push({ url: f.preview, name: f.name || 'Image', type: f.type || '', isImage: true, msgIndex: i });
        } else if (f.data && isImg) {
          attachments.push({ url: `data:${f.type};base64,${f.data}`, name: f.name || 'Image', type: f.type || '', isImage: true, msgIndex: i, data: f.data });
        } else {
          attachments.push({ name: f.name || 'File', type: f.type || '', isImage: false, msgIndex: i, data: f.data });
        }
      }
    }
    // From content blocks (DB restore)
    if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (block.type === 'image_url' && block.image_url?.url) {
          attachments.push({ url: block.image_url.url, name: 'Image', isImage: true, msgIndex: i });
        } else if (block.type === 'image' && block.source?.data) {
          const mt = block.source.media_type || 'image/png';
          attachments.push({ url: `data:${mt};base64,${block.source.data}`, name: 'Image', isImage: true, msgIndex: i });
        }
      }
    }
    // From text content — detect disk-saved file notices (DB restore for non-image files)
    const textContent = typeof msg.content === 'string' ? msg.content : (Array.isArray(msg.content) ? msg.content.find(b => b.type === 'text')?.text : '') || '';
    const fileNoticeMatch = textContent.match(/\[User attached files saved to disk[^\]]*\]\n([\s\S]*?)$/);
    if (fileNoticeMatch) {
      const pathLines = fileNoticeMatch[1].trim().split('\n');
      for (const line of pathLines) {
        const pathMatch = line.match(/^\s*-\s*(.+)$/);
        if (pathMatch) {
          const fpath = pathMatch[1].trim();
          const fname = fpath.split('/').pop();
          const ext = fname.split('.').pop()?.toLowerCase() || '';
          const isImg = ['png','jpg','jpeg','gif','webp','svg','bmp','ico'].includes(ext);
          const existing = attachments.find(a => a.name === fname);
          if (existing) {
            // Same name as an inline-block attachment for this turn —
            // attach the disk path so download/copy can use it.
            if (!existing.path) existing.path = fpath;
          } else {
            attachments.push({ name: fname, type: '', isImage: isImg, msgIndex: i, path: fpath });
          }
        }
      }
    }
  }
  return attachments;
}

function renderAttachmentsPane() {
  const attachments = collectChatAttachments();
  const grid = document.getElementById('attachments-grid');
  const empty = document.getElementById('attachments-empty');
  const fullview = document.getElementById('attachment-fullview');
  if (!grid) return;
  fullview.style.display = 'none';
  if (!attachments.length) {
    grid.style.display = 'none';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  grid.style.display = '';
  // Card-list layout — same shape as the Artifacts list (`.artifact-list` +
  // `.artifact-list-card`) so the panes feel consistent. Grouped into
  // collapsible per-turn sections via the global attachment-index `i`
  // (kept stable so showAttachmentFullview still works), mapped to a turn
  // by each attachment's `msgIndex`.
  const docIcon = artifactTypeIcon('document');
  const imgIcon = artifactTypeIcon('image');
  const cardHtml = (a, i) => {
    let iconHtml;
    if (a.isImage && a.url) {
      iconHtml = `<img src="${a.url}" alt="" loading="lazy" style="width:24px;height:24px;object-fit:cover;border-radius:4px">`;
    } else {
      iconHtml = a.isImage ? imgIcon : docIcon;
    }
    const ext = (a.name?.split('.').pop() || '').toUpperCase();
    const meta = [a.type || (ext ? ext.toLowerCase() : ''), a.path ? 'on disk' : 'inline']
      .filter(Boolean).join(' · ');
    return `
      <div class="artifact-list-card" onclick="showAttachmentFullview(${i})">
        <span class="alc-icon">${iconHtml}</span>
        <div class="alc-info">
          <div class="alc-name">${esc(a.name || 'Untitled')}</div>
          <div class="alc-meta">${esc(meta)}</div>
        </div>
      </div>`;
  };
  // Bucket attachment indices by turn.
  const byTurn = {};
  attachments.forEach((a, i) => {
    const tn = turnNumForMessageIdx(a.msgIndex == null ? -1 : a.msgIndex) || 0;
    (byTurn[tn] = byTurn[tn] || []).push(i);
  });
  renderTurnGroupedPane(grid, 'attachments', {
    countFor: (tn) => (byTurn[tn] || []).length,
    itemsFor: (tn) => '<div class="artifact-list">'
      + (byTurn[tn] || []).map(i => cardHtml(attachments[i], i)).join('') + '</div>',
    ungrouped: byTurn[0] ? {
      count: byTurn[0].length,
      html: '<div class="artifact-list">' + byTurn[0].map(i => cardHtml(attachments[i], i)).join('') + '</div>',
    } : null,
    emptyAll: '',
  });
}

// State for the attachment fullview (mirrors the artifact panel's _raw* refs
// on its content container — kept here so copy/download/code-toggle can find
// the active attachment without re-reading from collectChatAttachments()).
let _activeAttachmentIndex = -1;
let _attachmentSourceMode = false;

function showAttachmentFullview(index) {
  const attachments = collectChatAttachments();
  if (index < 0 || index >= attachments.length) return;
  _activeAttachmentIndex = index;
  _attachmentSourceMode = false;
  _renderAttachmentFullview();
}

// Text/code MIME prefixes / extensions we render inline as syntax-highlighted
// text. Anything else (binary docs) gets the file-card placeholder.
const _ATTACH_TEXT_EXTS = new Set([
  'txt', 'md', 'markdown', 'json', 'yaml', 'yml', 'toml', 'xml', 'html', 'htm',
  'css', 'js', 'mjs', 'ts', 'tsx', 'jsx', 'py', 'rb', 'go', 'rs', 'java',
  'c', 'cpp', 'h', 'hpp', 'cs', 'sh', 'bash', 'zsh', 'sql', 'csv', 'tsv',
  'ini', 'cfg', 'conf', 'log', 'env',
]);
// Per-attachment cache: fetched preview body, keyed by the same identity
// `_activeAttachmentIndex` uses. Avoids re-fetching when the user toggles
// Code mode on/off.
const _attachmentBodyCache = new Map();

function _attachIdent(a) {
  return a.path || a.url || a.name || '';
}

async function _fetchAttachmentBody(a) {
  const key = _attachIdent(a);
  if (_attachmentBodyCache.has(key)) return _attachmentBodyCache.get(key);
  // Inline data URI → decode without a round-trip.
  if (a.url && a.url.startsWith('data:')) {
    const m = /^data:([^;]+);base64,(.+)$/.exec(a.url);
    if (m) {
      try {
        const text = new TextDecoder('utf-8', {fatal: false}).decode(
          Uint8Array.from(atob(m[2]), c => c.charCodeAt(0))
        );
        const res = { ok: true, text };
        _attachmentBodyCache.set(key, res);
        return res;
      } catch (e) {
        const res = { ok: false, error: e.message || String(e) };
        _attachmentBodyCache.set(key, res);
        return res;
      }
    }
  }
  if (a.path) {
    try {
      const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(a.path)}`;
      const resp = await fetch(url, { headers: API._headers() });
      if (!resp.ok) {
        const res = { ok: false, error: `HTTP ${resp.status}` };
        _attachmentBodyCache.set(key, res);
        return res;
      }
      const text = await resp.text();
      const res = { ok: true, text };
      _attachmentBodyCache.set(key, res);
      return res;
    } catch (e) {
      const res = { ok: false, error: e.message || String(e) };
      _attachmentBodyCache.set(key, res);
      return res;
    }
  }
  return { ok: false, error: 'no source available' };
}

async function _renderAttachmentFullview() {
  const attachments = collectChatAttachments();
  const a = attachments[_activeAttachmentIndex];
  if (!a) { renderAttachmentsPane(); return; }
  const grid = document.getElementById('attachments-grid');
  const empty = document.getElementById('attachments-empty');
  const fullview = document.getElementById('attachment-fullview');
  grid.style.display = 'none';
  empty.style.display = 'none';
  fullview.style.display = '';

  const ext = (a.name?.split('.').pop() || '').toLowerCase();
  const isText = _ATTACH_TEXT_EXTS.has(ext) || (a.type || '').startsWith('text/');
  const isPdf = ext === 'pdf' || a.type === 'application/pdf';
  const canCopy = !!(a.url || a.name);
  const canDownload = !!(a.url || a.path);
  const sourceActive = _attachmentSourceMode ? 'active' : '';

  // Shell first — we re-render the body slot once async fetch resolves.
  const renderShell = (bodyHtml) => {
    fullview.innerHTML = `
      <button class="attach-fullview-back" onclick="renderAttachmentsPane()">Zurück zu allen</button>
      <div class="attach-fullview-body" id="attach-fullview-body">${bodyHtml}</div>
      <div class="attach-fullview-actions">
        <button class="artifact-action-btn" onclick="copyAttachment()" ${canCopy?'':'disabled'} title="Kopieren">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
          Kopieren
        </button>
        <button class="artifact-action-btn" onclick="downloadAttachment()" ${canDownload?'':'disabled'} title="Herunterladen">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Herunterladen
        </button>
        <button class="artifact-action-btn ${sourceActive}" onclick="toggleAttachmentSource()" title="Quelltext anzeigen">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
          Code
        </button>
      </div>
    `;
  };

  // Code/metadata view — shows path/MIME/raw body (text) or data URI head/tail.
  if (_attachmentSourceMode) {
    const lines = [];
    lines.push(`Name: ${a.name || '(unbekannt)'}`);
    if (a.type) lines.push(`Typ: ${a.type}`);
    if (a.path) lines.push(`Pfad: ${a.path}`);
    renderShell(`<pre class="attach-fullview-code">${esc(lines.join('\n'))}\n\nWird geladen …</pre>`);
    const body = await _fetchAttachmentBody(a);
    const slot = document.getElementById('attach-fullview-body');
    if (!slot) return;
    let tail;
    if (body.ok) {
      tail = body.text;
    } else if (a.url && a.url.startsWith('data:') && a.url.length > 300) {
      tail = `${a.url.slice(0, 200)}\n…\n${a.url.slice(-100)}`;
    } else {
      tail = `(nicht verfügbar: ${body.error})`;
    }
    slot.innerHTML = `<pre class="attach-fullview-code">${esc(lines.join('\n'))}\n\n${esc(tail)}</pre>`;
    return;
  }

  // Image preview
  if (a.isImage && a.url) {
    renderShell(`<img src="${a.url}" alt="${esc(a.name)}">`);
    return;
  }
  if (a.isImage && a.path) {
    // Disk-saved image, no inline bytes — load via /v1/files/download as a
    // blob URL.
    renderShell(`<div style="color:var(--text-400);font-size:12px">Wird geladen …</div>`);
    try {
      const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(a.path)}`;
      const resp = await fetch(url, { headers: API._headers() });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      const slot = document.getElementById('attach-fullview-body');
      if (slot) slot.innerHTML = `<img src="${objUrl}" alt="${esc(a.name)}">`;
    } catch (e) {
      const slot = document.getElementById('attach-fullview-body');
      if (slot) slot.innerHTML = `<div style="color:var(--text-400);font-size:12px">Vorschau nicht verfügbar: ${esc(e.message || e)}</div>`;
    }
    return;
  }

  // PDF preview — load via blob URL into an iframe (browser's native PDF viewer).
  if (isPdf && (a.path || (a.url && a.url.startsWith('data:application/pdf')))) {
    renderShell(`<div style="color:var(--text-400);font-size:12px">Wird geladen …</div>`);
    try {
      let objUrl;
      if (a.url && a.url.startsWith('data:')) {
        const r = await fetch(a.url);
        const blob = await r.blob();
        objUrl = URL.createObjectURL(blob);
      } else {
        const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(a.path)}`;
        const resp = await fetch(url, { headers: API._headers() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        objUrl = URL.createObjectURL(blob);
      }
      const slot = document.getElementById('attach-fullview-body');
      if (slot) slot.innerHTML = `<iframe src="${objUrl}" style="width:100%;height:100%;border:none;border-radius:8px;background:#fff"></iframe>`;
    } catch (e) {
      const slot = document.getElementById('attach-fullview-body');
      if (slot) slot.innerHTML = `<div style="color:var(--text-400);font-size:12px">Vorschau nicht verfügbar: ${esc(e.message || e)}</div>`;
    }
    return;
  }

  // Text / code / markdown preview — fetch body and render
  if (isText) {
    renderShell(`<div style="color:var(--text-400);font-size:12px">Wird geladen …</div>`);
    const body = await _fetchAttachmentBody(a);
    const slot = document.getElementById('attach-fullview-body');
    if (!slot) return;
    if (!body.ok) {
      slot.innerHTML = `<div style="color:var(--text-400);font-size:12px">Vorschau nicht verfügbar: ${esc(body.error)}</div>`;
      return;
    }
    if (ext === 'md' || ext === 'markdown') {
      slot.innerHTML = `<div class="artifact-markdown msg-content" style="width:100%;height:100%;overflow:auto;padding:8px">${renderMarkdown(body.text)}</div>`;
      slot.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch(_) {} });
    } else if (ext === 'html' || ext === 'htm') {
      slot.innerHTML = `<iframe sandbox="allow-same-origin" srcdoc="${esc(body.text)}" style="width:100%;height:100%;border:none;background:#fff"></iframe>`;
    } else {
      const lang = (typeof hljs !== 'undefined' && hljs.getLanguage(ext)) ? ext : 'plaintext';
      let highlighted;
      try {
        highlighted = hljs.highlight(body.text, { language: lang }).value;
      } catch(_) {
        highlighted = esc(body.text);
      }
      slot.innerHTML = `<pre class="attach-fullview-code"><code class="hljs language-${lang}">${highlighted}</code></pre>`;
    }
    return;
  }

  // Fallback — non-previewable binary (docx/xlsx/pptx/…): file card placeholder
  const extLabel = (a.name?.split('.').pop() || 'FILE').toUpperCase();
  renderShell(`
    <div class="attach-fullview-file-card">
      <div class="attach-fullview-file-ext">${esc(extLabel)}</div>
      <div class="attach-fullview-file-name">${esc(a.name)}</div>
      ${a.type ? `<div class="attach-fullview-file-meta">${esc(a.type)}</div>` : ''}
      <div style="font-size:11px;color:var(--text-400);margin-top:8px">Keine Inline-Vorschau — über „Herunterladen“ öffnen</div>
    </div>
  `);
}

function toggleAttachmentSource() {
  _attachmentSourceMode = !_attachmentSourceMode;
  _renderAttachmentFullview();
}

async function copyAttachment() {
  const attachments = collectChatAttachments();
  const a = attachments[_activeAttachmentIndex];
  if (!a) return;
  try { window.focus(); } catch(_) {}
  try {
    // Image with inline data URI → copy bytes to clipboard as image
    if (a.isImage && a.url && a.url.startsWith('data:')) {
      const m = /^data:([^;]+);base64,(.+)$/.exec(a.url);
      if (m) {
        const mime = m[1];
        const bin = atob(m[2]);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        await navigator.clipboard.write([new ClipboardItem({ [mime]: new Blob([bytes], { type: mime }) })]);
        showToast('Bild kopiert');
        return;
      }
    }
    // Fallback: copy filename (we don't have bytes for disk-only files
    // without an extra round-trip — name is what's useful for non-images)
    await navigator.clipboard.writeText(a.name || '');
    showToast('Dateiname kopiert');
  } catch (e) {
    console.error('[attachment] copy failed', e);
    showToast(`Kopieren fehlgeschlagen: ${e.message || e}`, true);
  }
}

async function downloadAttachment() {
  const attachments = collectChatAttachments();
  const a = attachments[_activeAttachmentIndex];
  if (!a) return;
  // Inline data URI → blob-download in the browser
  if (a.url && a.url.startsWith('data:')) {
    try {
      const r = await fetch(a.url);
      const blob = await r.blob();
      const objUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objUrl;
      link.download = a.name || 'attachment';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
      return;
    } catch (e) {
      showToast(`Herunterladen fehlgeschlagen: ${e.message || e}`, true);
      return;
    }
  }
  // Disk path → server download
  if (a.path) {
    try {
      const url = `${BASE_URL}/v1/files/download?path=${encodeURIComponent(a.path)}`;
      const resp = await fetch(url, { headers: API._headers() });
      if (!resp.ok) {
        const err = await resp.text().catch(() => '');
        showToast(`Herunterladen fehlgeschlagen (${resp.status}): ${err.slice(0, 80)}`, true);
        return;
      }
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objUrl;
      link.download = a.name || 'attachment';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
    } catch (e) {
      showToast(`Herunterladen fehlgeschlagen: ${e.message || e}`, true);
    }
    return;
  }
  showToast('Nichts zum Herunterladen', true);
}

// Legacy compat
function initArtifactResize() { initRightPanelResize(); }

function artifactTypeIcon(type) {
  const icons = {
    code: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    html: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    svg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M8 12l2 2 4-4"/></svg>',
    image: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
    markdown: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    document: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    text: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  };
  return icons[type] || icons.text;
}

async function openArtifactPanel(artifactId, version) {
  state.activeArtifactId = artifactId;
  state.artifactSourceMode = false;
  document.getElementById('artifact-source-btn')?.classList.remove('active');

  // Find artifact in registry
  const chat = state.activeChat;
  if (!chat) return;
  const sessionId = chat.sessionId;
  const artifacts = state.artifacts[sessionId] || [];
  const artifact = artifacts.find(a => a.id === artifactId);
  if (!artifact) {
    showToast('Artefakt nicht gefunden', true);
    return;
  }

  // Update header
  document.getElementById('artifact-title').textContent = artifact.name;

  // Populate version selector
  const sel = document.getElementById('artifact-version-select');
  sel.innerHTML = '';
  const versions = artifact.versions || [];
  for (const v of versions) {
    const opt = document.createElement('option');
    opt.value = v.version;
    opt.textContent = `v${v.version}`;
    sel.appendChild(opt);
  }
  const targetVersion = version || (versions.length ? versions[versions.length - 1].version : 1);
  sel.value = targetVersion;
  state.activeArtifactVersion = targetVersion;

  // Open unified right panel on artifacts tab
  openRightPanel('artifacts');

  // Show actions bar
  document.getElementById('artifact-actions').style.display = '';

  // Load content
  await loadArtifactVersion(targetVersion);
}

async function loadArtifactVersion(version) {
  const artifactId = state.activeArtifactId;
  if (!artifactId) return;
  state.activeArtifactVersion = version;
  const sel = document.getElementById('artifact-version-select');
  if (sel) sel.value = version;

  const container = document.getElementById('artifact-content');
  container.innerHTML = '<div class="artifact-empty"><div class="wave-bars"><span></span><span></span><span></span></div></div>';

  // Audio (.mp3/.wav/…): never pull the bytes through the JSON /content endpoint
  // — that base64s the whole clip and feeds binary into hljs, which hangs the
  // panel. Play it inline from an auth'd blob (download URL is Bearer-only), the
  // same pattern Studio uses.
  const reg = (state.artifacts[state.activeChat?.sessionId] || []).find(a => a.id === artifactId);
  const regExt = (reg?.name || '').split('.').pop().toLowerCase();
  if (reg?.type === 'audio' || ['mp3', 'wav', 'm4a', 'ogg'].includes(regExt)) {
    await renderArtifactAudio(artifactId, version, reg?.name || 'audio');
    return;
  }

  try {
    const data = await API.getArtifactContent(artifactId, version);
    if (!data || !data.content) {
      console.error('[artifact] Empty response for', artifactId, 'version', version, 'data:', data);
      container.innerHTML = `<div class="artifact-empty">Kein Inhalt verfügbar (Version ${version})</div>`;
      return;
    }
    renderArtifactContent(data.content, data.type, data.name, data.encoding);
  } catch (e) {
    console.error('[artifact] Load failed for', artifactId, 'version', version, e);
    container.innerHTML = `<div class="artifact-empty">Inhalt konnte nicht geladen werden: ${e.message || e}</div>`;
  }
}

// Inline audio player for an audio artifact, fed by an auth'd blob.
async function renderArtifactAudio(artifactId, version, name) {
  const container = document.getElementById('artifact-content');
  container.innerHTML = `<div style="display:flex;flex-direction:column;gap:14px;align-items:center;justify-content:center;height:100%;padding:24px 16px">
    <div style="font-size:48px">🎧</div>
    <div style="font-size:13px;color:var(--text-400);text-align:center;word-break:break-word">${esc(name)}</div>
    <div class="artifact-audio-mount" style="width:100%;display:flex;justify-content:center">Lädt…</div>
  </div>`;
  const mount = container.querySelector('.artifact-audio-mount');
  try {
    const resp = await fetch(API.getArtifactDownloadUrl(artifactId, version), { headers: API._headers() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const url = URL.createObjectURL(await resp.blob());
    mount.innerHTML = `<audio controls autoplay preload="metadata" style="width:100%;max-width:520px" src="${url}"></audio>`;
  } catch (e) {
    mount.innerHTML = `<div style="color:var(--error)">Audio konnte nicht geladen werden: ${esc(e.message || e)}</div>`;
  }
}

function shareCurrentArtifact() {
  const artifactId = state.activeArtifactId;
  if (!artifactId) { showToast('Kein Artefakt geöffnet', true); return; }
  const title = document.getElementById('artifact-title')?.textContent || 'artifact';
  shareDialog('artifact', artifactId, '', { title });
}

function renderArtifactContent(content, type, name, encoding) {
  const container = document.getElementById('artifact-content');
  const ext = name.split('.').pop().toLowerCase();

  // Store raw content for source toggle
  container._rawContent = content;
  container._rawType = type;
  container._rawName = name;
  container._rawEncoding = encoding;

  if (state.artifactSourceMode && type !== 'image') {
    // Source view — always raw text
    container.innerHTML = `<pre class="artifact-code"><code>${esc(content)}</code></pre>`;
    return;
  }

  switch (type) {
    case 'html':
      container.innerHTML = `<iframe sandbox="allow-scripts allow-same-origin" srcdoc="${esc(content)}" style="width:100%;height:100%;border:none"></iframe>`;
      break;
    case 'svg':
      container.innerHTML = `<div style="padding:24px;display:flex;align-items:center;justify-content:center;height:100%">${content}</div>`;
      break;
    case 'image': {
      const imgExt = ext === 'svg' ? 'svg+xml' : ext;
      const src = encoding === 'base64' ? `data:image/${imgExt};base64,${content}` : content;
      container.innerHTML = `<div style="padding:24px;display:flex;align-items:center;justify-content:center;height:100%;background:var(--bg-100)"><img src="${src}" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:8px"></div>`;
      break;
    }
    case 'markdown':
      container.innerHTML = `<div class="artifact-markdown msg-content">${renderMarkdown(content)}</div>`;
      // Apply syntax highlighting to code blocks
      container.querySelectorAll('pre code').forEach(el => { try { hljs.highlightElement(el); } catch(e) {} });
      break;
    case 'code':
    default: {
      const lang = (typeof hljs !== 'undefined' && hljs.getLanguage(ext)) ? ext : 'plaintext';
      let highlighted;
      try {
        highlighted = hljs.highlight(content, { language: lang }).value;
      } catch(e) {
        highlighted = esc(content);
      }
      container.innerHTML = `<pre class="artifact-code"><code class="hljs language-${lang}">${highlighted}</code></pre>`;
      break;
    }
  }
}

function closeArtifactPanel() {
  closeRightPanel(true);
}

// Build SVG graph from drawers + triples. Layout is "document → subject →
// object" reading left-to-right — matches how the user reads a triple in
// the relations list ("policy.pdf says: subject — predicate → object") and
// keeps doc circles meaningfully connected instead of orphaned.
//
// Sizing is dataset-aware: height grows with row count so labels never
// overlap, columns are hidden entirely when empty (a docless query
// renders 2 columns instead of forcing a tiny 3rd column with whitespace).
function _renderKnowledgeGraphSvg(drawers, triples, opts) {
  opts = opts || {};
  const W = opts.width || 880;
  // Node radii / spacing. Pick wider rectangles (rounded) instead of pure
  // circles — entity names like "Änderungen an den Berechtigungen" don't
  // fit in a 14px circle. Pills have width based on content with a sane
  // cap, height fixed.
  const PILL_H = 28;
  const ROW_GAP = 22;        // vertical gap between adjacent rows in a column
  const SIDE_PAD = 24;
  const TOP_PAD = 28;

  // Build node maps + edges. Same id can serve as subject AND object across
  // triples; we still render it once and route both edges to/from it.
  const nodes = new Map();   // id → {id, label, kind, sources:Set}
  const edges = [];          // {from, to, label, source_file}
  const ensureNode = (id, label, kind) => {
    if (!nodes.has(id)) nodes.set(id, { id, label: label || id, kind, sources: new Set() });
    return nodes.get(id);
  };
  // Triples first so we know which entities exist before docs.
  for (const tr of triples) {
    const s = ensureNode('e:' + tr.subject, tr.subject, 'subject');
    const o = ensureNode('e:' + tr.object,  tr.object,  'object');
    if (tr.source_file) { s.sources.add(tr.source_file); o.sources.add(tr.source_file); }
    edges.push({ from: s.id, to: o.id, label: tr.predicate, source_file: tr.source_file || '' });
  }
  // Mark dual-role entities (appear as both subject and object). They
  // render in a centre column to avoid double-drawing.
  for (const tr of triples) {
    const s = nodes.get('e:' + tr.subject);
    const o = nodes.get('e:' + tr.object);
    if (s && o && tr.subject === tr.object) continue;
  }
  // Document name normaliser — strip the trailing `.md` companion suffix
  // (`policy.pdf.md` → `policy.pdf`) and the binary ext for display so the
  // pill reads "policy" not "policy.pdf.md". Used for both the node id
  // (so drawers and triples that reference the same source merge) and the
  // visible label.
  const _binExt = /\.(pdf|docx|pptx|xlsx|xlsm|eml|msg)$/i;
  const docKeyOf = (sf) => {
    if (!sf) return '';
    const base = sf.split('/').pop().replace(/\.md$/, '');
    return base.toLowerCase();   // case-insensitive match across drawer/triple paths
  };
  const docLabelOf = (sf) => {
    if (!sf) return 'unknown';
    return sf.split('/').pop().replace(/\.md$/, '').replace(_binExt, '');
  };

  // Doc nodes from drawers AND from any triple's source_file. Without the
  // triple-derived nodes the user can't see which document each relation
  // came from when the agent extracted facts without fetching that doc's
  // drawer (common: KG search returns triples by predicate, doesn't pull
  // the underlying chunk).
  for (const d of drawers) {
    const sf = d.source_file;
    if (!sf) continue;
    const id = 'doc:' + docKeyOf(sf);
    ensureNode(id, docLabelOf(sf), 'doc');
  }
  for (const tr of triples) {
    const sf = tr.source_file;
    if (!sf) continue;
    const id = 'doc:' + docKeyOf(sf);
    ensureNode(id, docLabelOf(sf), 'doc');
  }

  // Doc → subject edges: dotted "this fact came from that document" link.
  // Resolved by basename so a drawer with bare-name source_file lines up
  // with a triple carrying the same name's full path.
  const docEdgeKeys = new Set();
  for (const tr of triples) {
    if (!tr.source_file) continue;
    const docId = 'doc:' + docKeyOf(tr.source_file);
    const subjId = 'e:' + tr.subject;
    if (!nodes.has(docId)) continue;
    const k = docId + '|' + subjId;
    if (docEdgeKeys.has(k)) continue;
    docEdgeKeys.add(k);
    edges.unshift({ from: docId, to: subjId, label: '', source_file: tr.source_file, dotted: true });
  }

  // Column buckets. Subject column = entities that appear as a subject
  // somewhere; object column = those that appear ONLY as an object.
  // Anything in BOTH stays in the subject column (where the relation
  // arrows fan out from).
  const subjects = new Set();
  const objects  = new Set();
  for (const tr of triples) { subjects.add('e:' + tr.subject); objects.add('e:' + tr.object); }
  const docNodes = [...nodes.values()].filter(n => n.kind === 'doc');
  const subjNodes = [...nodes.values()].filter(n => subjects.has(n.id));
  const objNodes  = [...nodes.values()].filter(n => !subjects.has(n.id) && objects.has(n.id));

  // Column geometry — only allocate a column when it has content.
  const cols = [];
  if (docNodes.length)  cols.push({ key: 'doc',  list: docNodes,  pillW: 200 });
  if (subjNodes.length) cols.push({ key: 'subj', list: subjNodes, pillW: 220 });
  if (objNodes.length)  cols.push({ key: 'obj',  list: objNodes,  pillW: 220 });
  // Distribute X across actual columns. With 1 column we centre it; with
  // 2 we space them nicely; with 3 we evenly distribute.
  const innerW = W - 2*SIDE_PAD;
  cols.forEach((col, i) => {
    if (cols.length === 1) col.x = W / 2;
    else col.x = SIDE_PAD + col.pillW/2 + (innerW - col.pillW) * (i / (cols.length - 1));
  });
  // Rows: tallest column drives height. Each pill takes PILL_H + ROW_GAP.
  const maxRows = Math.max(1, ...cols.map(c => c.list.length));
  const H = TOP_PAD * 2 + maxRows * PILL_H + Math.max(0, maxRows - 1) * ROW_GAP;
  // Place every node by walking each column top-to-bottom, vertically
  // centred so short columns don't sit at the top.
  for (const col of cols) {
    const totalH = col.list.length * PILL_H + Math.max(0, col.list.length - 1) * ROW_GAP;
    const yStart = (H - totalH) / 2 + PILL_H / 2;
    col.list.forEach((node, i) => {
      node.x = col.x;
      node.y = yStart + i * (PILL_H + ROW_GAP);
      node.pillW = col.pillW;
    });
  }

  // Helpers for label fit. Approx 6.5px per character at 11px font is
  // close enough for sans-serif; truncate when overflowing.
  const fitLabel = (s, pxWidth) => {
    const maxChars = Math.max(6, Math.floor(pxWidth / 6.8));
    return s.length > maxChars ? s.slice(0, maxChars - 1) + '…' : s;
  };

  // Compose SVG. Edges below nodes; arrowhead marker for direction.
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="background:var(--bg-100);border-radius:8px;border:1px solid var(--border-100);max-height:62vh;display:block" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="kg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0,0 L10,5 L0,10 z" fill="var(--accent-brand)" opacity="0.85"/>
      </marker>
    </defs>`;

  // Edges as cubic Béziers — straighter when columns are close, more
  // bowed when far. Label rides along the curve at 50% via textPath
  // anchored to a unique <path> per edge, so labels never overlap nodes.
  edges.forEach((e, idx) => {
    const a = nodes.get(e.from);
    const b = nodes.get(e.to);
    if (!a || !b) return;
    // Connect at the right edge of `a` and left edge of `b` so arrows
    // don't pierce the pill rectangles.
    const ax = a.x + a.pillW / 2;
    const bx = b.x - b.pillW / 2;
    const dx = bx - ax;
    // Bezier control points pulled toward the midline horizontally; this
    // gives a gentle S-curve when src/dst rows differ in y.
    const c1x = ax + dx * 0.45;
    const c1y = a.y;
    const c2x = bx - dx * 0.45;
    const c2y = b.y;
    const pathId = `kg-edge-${idx}`;
    const dotted = e.dotted ? 'stroke-dasharray="3 4" stroke-opacity="0.35"' : 'stroke-opacity="0.6"';
    svg += `<path id="${pathId}" d="M ${ax} ${a.y} C ${c1x} ${c1y} ${c2x} ${c2y} ${bx} ${b.y}"
                  fill="none" stroke="var(--accent-brand)" ${dotted} stroke-width="1.4"
                  ${e.dotted ? '' : 'marker-end="url(#kg-arrow)"'}/>`;
    if (e.label) {
      svg += `<text font-family="var(--font-mono)" font-size="10" fill="var(--text-300)"
                    style="paint-order:stroke;stroke:var(--bg-100);stroke-width:3px">
                <textPath href="#${pathId}" startOffset="50%" text-anchor="middle">${esc(e.label)}</textPath>
              </text>`;
    }
  });

  // Nodes as rounded rectangles ("pills") with text inside — fits long
  // German entity names that the previous circle layout truncated to
  // unrecognisable stubs.
  for (const n of nodes.values()) {
    const fill = n.kind === 'doc' ? '#d97706' : '#2563eb';
    const x = n.x - n.pillW / 2;
    const y = n.y - PILL_H / 2;
    const label = fitLabel(n.label, n.pillW - 16);
    svg += `<g data-node-id="${esc(n.id)}">
      <rect x="${x}" y="${y}" width="${n.pillW}" height="${PILL_H}" rx="14" ry="14"
            fill="${fill}" fill-opacity="0.14" stroke="${fill}" stroke-width="1.5"/>
      <text x="${n.x}" y="${n.y + 4}" font-size="11" fill="var(--text-100)"
            text-anchor="middle" font-family="var(--font-sans, system-ui)">${esc(label)}</text>
      <title>${esc(n.label)}</title>
    </g>`;
  }

  // Column headers — small, faded, only when there's a doc column to
  // disambiguate. Two-column subject/object layouts are obvious from
  // arrow direction.
  if (cols.length >= 2 && docNodes.length) {
    const headerLabel = (key) => key === 'doc' ? 'Dokumente'
                                : key === 'subj' ? 'Subjekte' : 'Objekte';
    cols.forEach(col => {
      svg += `<text x="${col.x}" y="14" font-size="10" fill="var(--text-400)"
                    text-anchor="middle" font-family="var(--font-sans, system-ui)"
                    style="text-transform:uppercase;letter-spacing:0.05em">${headerLabel(col.key)}</text>`;
    });
  }

  svg += '</svg>';
  return svg;
}

function openUsedMemoryGraph(idx) {
  const { drawers, triples } = _collectKnowledgeForMessage(idx);
  if (!drawers.length && !triples.length) {
    showToast('Diese Antwort hat nicht auf den Projektspeicher zugegriffen');
    return;
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.zIndex = '12000';
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  const svg = _renderKnowledgeGraphSvg(drawers, triples);
  // Drawer list: each card shows source file + snippet, click scrolls
  // (and a click could later open the source — reuses openProjectSource
  // when available).
  const drawerCards = drawers.length
    ? drawers.map(d => {
        const sf = d.source_file || 'unknown';
        const base = sf.split('/').pop().replace(/\.md$/, '');
        const text = (d.snippet || d.text || '').slice(0, 320);
        return `<div style="border:1px solid var(--border-100);border-radius:6px;padding:8px 10px;margin-bottom:6px;font-size:12px;background:var(--bg-100)">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:3px;background:#d97706;color:#fff;font-size:8px;font-weight:700">DOC</span>
            <span style="font-weight:500;color:var(--text-100);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(base)}</span>
            ${typeof d.similarity === 'number' ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--text-400)">sim ${d.similarity.toFixed(2)}</span>` : ''}
          </div>
          <div style="color:var(--text-300);line-height:1.4;white-space:pre-wrap">${esc(text)}</div>
        </div>`;
      }).join('')
    : '<div style="color:var(--text-400);font-size:12px;padding:8px">Keine Schubladen abgerufen.</div>';
  // Triples list: subject — predicate → object, grouped to keep the panel
  // scannable when extraction returns dozens.
  const tripleRows = triples.length
    ? triples.map(t => {
        const sf = (t.source_file || '').split('/').pop().replace(/\.md$/, '');
        const conf = typeof t.confidence === 'number' ? t.confidence.toFixed(2) : '';
        return `<div style="font-size:12px;padding:6px 8px;border-bottom:1px dotted var(--border-100)">
          <div style="display:flex;gap:6px;align-items:flex-start">
            <span style="color:var(--text-100);flex:1">${esc(t.subject)}</span>
            <span style="font-family:var(--font-mono);color:var(--accent-brand);font-size:11px">— ${esc(t.predicate)} →</span>
            <span style="color:var(--text-100);flex:1">${esc(t.object)}</span>
          </div>
          <div style="font-family:var(--font-mono);font-size:10px;color:var(--text-400);margin-top:2px">${esc(sf)}${conf ? ` · c=${conf}` : ''}</div>
        </div>`;
      }).join('')
    : '<div style="color:var(--text-400);font-size:12px;padding:8px">Keine Beziehungen extrahiert.</div>';
  // Tagline tells the user, in plain English, what the panel contains —
  // the header alone leaves users guessing what "drawers" and "relations"
  // mean in context.
  const tagline = `<div style="font-size:12px;color:var(--text-400);line-height:1.5;padding:10px 14px;background:var(--bg-100);border:1px solid var(--border-100);border-radius:8px;margin-bottom:12px">
    Dies sind die Dokumentabschnitte (Schubladen) und extrahierten Fakten (Beziehungen), die der Agent aus dem Speicher dieses Projekts abgerufen hat, bevor er seine Antwort verfasst hat. Damit lässt sich nachvollziehen, welche Quellen herangezogen wurden und welche konkreten Aussagen daraus stammen.
  </div>`;

  overlay.innerHTML = `<div class="modal-content" style="max-width:1180px;width:94vw;max-height:90vh;display:flex;flex-direction:column">
    <div class="modal-header" style="display:flex;align-items:center;gap:10px">
      <span style="font-weight:600">In dieser Antwort verwendeter Speicher & Beziehungen</span>
      <span style="font-size:11px;color:var(--text-400)">${drawers.length} Schublade${drawers.length===1?'':'n'} · ${triples.length} Beziehung${triples.length===1?'':'en'}</span>
      <button class="modal-close" style="margin-left:auto" onclick="this.closest('.modal-overlay').remove()">&times;</button>
    </div>
    <div class="modal-body" style="display:flex;flex-direction:column;gap:12px;overflow:auto;flex:1;padding:16px 20px">
      ${tagline}
      <div style="display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:16px;align-items:start">
        <div style="min-width:0;display:flex;flex-direction:column;gap:8px">
          <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em">Graph-Ansicht</div>
          <div>${svg}</div>
          <div style="display:flex;gap:14px;font-size:11px;color:var(--text-400);align-items:center;flex-wrap:wrap;padding:4px 0 0">
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;height:10px;border-radius:5px;background:#d9770624;border:1.5px solid #d97706"></span> Dokument</span>
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;height:10px;border-radius:5px;background:#2563eb24;border:1.5px solid #2563eb"></span> Entität</span>
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;border-top:1.5px solid var(--accent-brand)"></span> Beziehung</span>
            <span style="display:inline-flex;align-items:center;gap:5px"><span style="display:inline-block;width:18px;border-top:1.5px dashed var(--accent-brand);opacity:0.6"></span> Quellenverweis</span>
          </div>
        </div>
        <div style="min-width:0;display:flex;flex-direction:column;gap:14px">
          <div>
            <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Schubladen (${drawers.length})</div>
            <div>${drawerCards}</div>
          </div>
          <div>
            <div style="font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:6px">Beziehungen (${triples.length})</div>
            <div>${tripleRows}</div>
          </div>
        </div>
      </div>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

// Filter state for the right-panel artifact list. 'output' hides
// intermediate files (scripts, json data dumps) so the panel surfaces the
// actual deliverables for scheduled task runs; 'all' shows everything.
let _artifactListRoleFilter = 'output';

function setArtifactListRoleFilter(role) {
  _artifactListRoleFilter = role;
  showArtifactList();
}

function showArtifactList() {
  const chat = state.activeChat;
  if (!chat) return;
  const sessionId = chat.sessionId;
  const artifacts = state.artifacts[sessionId] || [];

  const container = document.getElementById('artifact-content');
  document.getElementById('artifact-title').textContent = 'Artefakte';
  document.getElementById('artifact-actions').style.display = 'none';
  document.getElementById('artifact-version-select').innerHTML = '';
  state.activeArtifactId = null;

  if (artifacts.length === 0) {
    container.innerHTML = '<div class="artifact-empty"><svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>Noch keine Artefakte</div>';
    return;
  }

  // Count intermediates so we can show a hint even when filtered out.
  const intermediateCount = artifacts.filter(a => (a.role || 'output') === 'intermediate').length;
  const hasIntermediate = intermediateCount > 0;
  const filtered = _artifactListRoleFilter === 'output'
    ? artifacts.filter(a => (a.role || 'output') === 'output')
    : artifacts;

  // Role-filter chips are chrome (not per-turn) so they live in a fixed
  // header above the turn-grouped sections, which render into a child
  // container that renderTurnGroupedPane owns.
  let filterBar = '';
  if (hasIntermediate) {
    const outActive = _artifactListRoleFilter === 'output';
    filterBar = `<div style="display:flex;gap:4px;padding:8px 10px;border-bottom:1px solid var(--border-100)">
      <button onclick="setArtifactListRoleFilter('output')" style="padding:3px 10px;border-radius:5px;font-size:11px;background:${outActive?'var(--bg-300)':'transparent'};color:${outActive?'var(--text-000)':'var(--text-300)'};border:1px solid var(--border-100)">Ergebnisse</button>
      <button onclick="setArtifactListRoleFilter('all')" style="padding:3px 10px;border-radius:5px;font-size:11px;background:${!outActive?'var(--bg-300)':'transparent'};color:${!outActive?'var(--text-000)':'var(--text-300)'};border:1px solid var(--border-100)">Alle (+${intermediateCount} Arbeitsdateien)</button>
    </div>`;
  }
  container.innerHTML = filterBar + '<div id="artifact-turn-groups"></div>';

  const cardHtml = (a) => {
    const verCount = a.versions?.length || 0;
    const latestVer = verCount > 0 ? a.versions[verCount - 1] : null;
    const meta = latestVer ? (latestVer.action === 'created' ? 'Erstellt' : 'Geändert') : '';
    const isInter = (a.role || 'output') === 'intermediate';
    const roleBadge = isInter
      ? `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(120,120,120,0.15);color:var(--text-400);text-transform:uppercase;letter-spacing:0.04em;margin-left:4px" title="Während der Ausführung erzeugte Hilfs-/Arbeitsdatei">Arbeitsdatei</span>`
      : '';
    return `
      <div class="artifact-list-card" onclick="openArtifactPanel('${esc(a.id)}')">
        <span class="alc-icon">${artifactTypeIcon(a.type)}</span>
        <div class="alc-info">
          <div class="alc-name">${esc(a.name)}${roleBadge}</div>
          <div class="alc-meta">${esc(a.type)} ${meta ? '· ' + meta : ''}</div>
        </div>
        <span class="alc-versions">v${verCount}</span>
      </div>`;
  };
  // Bucket by producing turn. message_idx==null → ungrouped (turn 0).
  // Server's message_idx is an index into the persisted message list; the
  // client unshifts a synthetic `compacted` divider at index 0 after an LCM
  // compaction, so shift by +1 to realign before mapping to a turn.
  const idxShift = (chat.messages && chat.messages[0]?.role === 'compacted') ? 1 : 0;
  const byTurn = {};
  for (const a of filtered) {
    const tn = (a.message_idx == null) ? 0 : (turnNumForMessageIdx(a.message_idx + idxShift) || 0);
    (byTurn[tn] = byTurn[tn] || []).push(a);
  }
  renderTurnGroupedPane(document.getElementById('artifact-turn-groups'), 'artifacts', {
    countFor: (tn) => (byTurn[tn] || []).length,
    itemsFor: (tn) => '<div class="artifact-list">' + (byTurn[tn] || []).map(cardHtml).join('') + '</div>',
    ungrouped: byTurn[0] ? {
      count: byTurn[0].length,
      html: '<div class="artifact-list">' + byTurn[0].map(cardHtml).join('') + '</div>',
    } : null,
    emptyAll: '',
  });
}

async function copyArtifact() {
  const container = document.getElementById('artifact-content');
  const raw = container._rawContent;
  const encoding = container._rawEncoding;
  const type = container._rawType;
  if (!raw) { showToast('Nichts zum Kopieren', true); return; }
  // Iframes (HTML artifacts) can hold focus, breaking navigator.clipboard.
  try { window.focus(); } catch(_) {}
  try {
    if (encoding === 'base64' && type === 'image') {
      const imgExt = (container._rawName || '').split('.').pop().toLowerCase();
      const mime = imgExt === 'svg' ? 'image/svg+xml' : `image/${imgExt || 'png'}`;
      const bin = atob(raw);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      await navigator.clipboard.write([new ClipboardItem({ [mime]: new Blob([bytes], { type: mime }) })]);
      showToast('Bild kopiert');
      return;
    }
    await navigator.clipboard.writeText(raw);
    showToast('In die Zwischenablage kopiert');
  } catch (e) {
    // Fallback: hidden textarea + execCommand('copy') works even when
    // navigator.clipboard rejects due to focus stolen by an iframe.
    try {
      const ta = document.createElement('textarea');
      ta.value = raw;
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      if (ok) { showToast('In die Zwischenablage kopiert'); return; }
      throw new Error('execCommand returned false');
    } catch (e2) {
      console.error('[artifact] copy failed', e, e2);
      showToast(`Kopieren fehlgeschlagen: ${e.message || e}`, true);
    }
  }
}

async function downloadArtifact() {
  const id = state.activeArtifactId;
  const ver = state.activeArtifactVersion;
  if (!id) return;
  const url = API.getArtifactDownloadUrl(id, ver);
  try {
    const r = await fetch(url, { headers: API._headers() });
    if (!r.ok) {
      const msg = r.status === 401 ? 'Nicht autorisiert' : `Herunterladen fehlgeschlagen (${r.status})`;
      showToast(msg, true);
      return;
    }
    const blob = await r.blob();
    const disp = r.headers.get('Content-Disposition') || '';
    const m = /filename="([^"]+)"/.exec(disp);
    const filename = m ? m[1] : 'artifact';
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
  } catch (e) {
    console.error('[artifact] download failed', e);
    showToast(`Herunterladen fehlgeschlagen: ${e.message || e}`, true);
  }
}

function toggleArtifactSource() {
  state.artifactSourceMode = !state.artifactSourceMode;
  const btn = document.getElementById('artifact-source-btn');
  if (btn) btn.classList.toggle('active', state.artifactSourceMode);

  const container = document.getElementById('artifact-content');
  if (container._rawContent) {
    renderArtifactContent(container._rawContent, container._rawType, container._rawName, container._rawEncoding);
  }
}

function updateArtifactRegistry(sessionId, eventData) {
  if (!state.artifacts[sessionId]) state.artifacts[sessionId] = [];
  const artifacts = state.artifacts[sessionId];
  const existing = artifacts.find(a => a.id === eventData.artifact_id);
  // message_idx anchors the artifact to the producing turn (right-panel
  // grouping). May be undefined on legacy events; falls back to 'ungrouped'.
  const msgIdx = (eventData.message_idx == null) ? null : eventData.message_idx;
  if (existing) {
    // Add new version
    if (!existing.versions) existing.versions = [];
    existing.versions.push({
      version: eventData.artifact_version,
      size: eventData.size,
      action: eventData.action,
      created_at: Date.now() / 1000,
      message_idx: msgIdx,
    });
  } else {
    // New artifact
    artifacts.push({
      id: eventData.artifact_id,
      name: eventData.name,
      path: eventData.path,
      type: eventData.artifact_type,
      message_idx: msgIdx,
      versions: [{
        version: eventData.artifact_version,
        size: eventData.size,
        action: eventData.action,
        created_at: Date.now() / 1000,
        message_idx: msgIdx,
      }],
    });
  }
}

/* ═══════════════════════════════════════════════════════════
   ARTIFACTS BROWSE VIEW
   ═══════════════════════════════════════════════════════════ */

let _browseArtifactsCache = [];
let _browseArtifactsFilter = 'all';
let _browseArtifactsAgent = null;
let _browseArtifactsSource = 'all';  // 'all' | 'chat' | 'scheduled'
// Default hides intermediate files (helper scripts / json data dumps) so the
// grid surfaces deliverables. Flip to 'all' to inspect the raw working set.
let _browseArtifactsRole = 'output';

async function loadArtifactsBrowse() {
  const grid = document.getElementById('artifacts-grid');
  grid.innerHTML = '<div class="artifacts-empty">Wird geladen …</div>';

  try {
    const resp = await API.browseArtifacts(_browseArtifactsAgent);
    _browseArtifactsCache = resp.artifacts || [];
  } catch (e) {
    grid.innerHTML = '<div class="artifacts-empty">Artefakte konnten nicht geladen werden</div>';
    return;
  }

  // Build agent filter chips
  const agents = [...new Set(_browseArtifactsCache.map(a => a.agent_id))];
  const filterEl = document.getElementById('artifacts-agent-filter');
  if (agents.length > 1) {
    let chips = `<button class="artifacts-agent-chip${!_browseArtifactsAgent ? ' active' : ''}" onclick="setArtifactsBrowseAgent(null)">Alle Agents</button>`;
    for (const ag of agents) {
      chips += `<button class="artifacts-agent-chip${_browseArtifactsAgent === ag ? ' active' : ''}" onclick="setArtifactsBrowseAgent('${esc(ag)}')">${esc(ag)}</button>`;
    }
    filterEl.innerHTML = chips;
    filterEl.style.display = '';
  } else {
    filterEl.style.display = 'none';
  }

  renderArtifactsBrowse();
}

function renderArtifactsBrowse() {
  const grid = document.getElementById('artifacts-grid');
  let filtered = _browseArtifactsCache;

  if (_browseArtifactsSource !== 'all') {
    filtered = filtered.filter(a => (a.source || 'chat') === _browseArtifactsSource);
  }
  if (_browseArtifactsFilter !== 'all') {
    filtered = filtered.filter(a => a.type === _browseArtifactsFilter);
  }
  if (_browseArtifactsRole === 'output') {
    filtered = filtered.filter(a => (a.role || 'output') === 'output');
  }

  if (filtered.length === 0) {
    const roleHint = _browseArtifactsRole === 'output'
      ? '<div style="margin-top:8px;font-size:12px;color:var(--text-400)"><a href="#" onclick="event.preventDefault();filterArtifactsRole(\'all\')" style="color:var(--accent-brand)">Arbeitsdateien anzeigen</a>, um Hilfsskripte und Arbeitsdaten einzubeziehen.</div>'
      : '';
    grid.innerHTML = `<div class="artifacts-empty" style="grid-column:1/-1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      Noch keine Artefakte${_browseArtifactsFilter !== 'all' ? ' dieses Typs' : ''}${_browseArtifactsSource !== 'all' ? ' in ' + _browseArtifactsSource : ''}
      ${roleHint}
    </div>`;
    return;
  }

  let html = '';
  for (const a of filtered) {
    const preview = a.preview ? esc(a.preview) : '';
    const binaryTypes = ['image', 'document'];
    const hasPreview = preview && !binaryTypes.includes(a.type);

    // Time ago
    const ts = a.latest_created_at || a.created_at;
    const ago = ts ? timeAgo(ts) : '';

    // Source badge — scheduled-task artifacts carry a pill with the task name
    // so you can tell at a glance which run produced the file.
    const isScheduled = a.source === 'scheduled';
    const schedRun = a.schedule_run;
    const sourceBadge = isScheduled
      ? `<span class="abm-source" style="background:rgba(245,158,11,0.12);color:#d97706;padding:1px 6px;border-radius:4px;font-size:10px;text-transform:uppercase;letter-spacing:0.04em" title="${esc(schedRun ? schedRun.schedule_name + ' · Lauf #' + schedRun.run_id : 'Geplante Aufgabe')}">geplant${schedRun ? ' · ' + esc((schedRun.schedule_name || '').slice(0, 18)) : ''}</span>`
      : '';
    const isInter = (a.role || 'output') === 'intermediate';
    const roleBadge = isInter
      ? `<span style="background:rgba(120,120,120,0.15);color:var(--text-400);padding:1px 5px;border-radius:3px;font-size:10px;text-transform:uppercase;letter-spacing:0.04em" title="Während der Ausführung verwendete Hilfs- oder Arbeitsdatei">Arbeitsdatei</span>`
      : '';

    html += `
      <div class="artifact-browse-card" data-art-id="${esc(a.id)}" data-art-agent="${esc(a.agent_id)}" ${isInter ? 'style="opacity:0.78"' : ''}>
        <div class="artifact-browse-fav-slot" onclick="event.stopPropagation()" data-art-fav-id="${esc(a.id)}" data-art-fav-agent="${esc(a.agent_id)}"></div>
        <div class="artifact-browse-preview${hasPreview ? '' : ' no-preview'}" onclick="openArtifactFromBrowse('${esc(a.id)}', '${esc(a.session_id)}', '${esc(a.agent_id)}')">
          ${hasPreview ? preview : artifactTypeIcon(a.type)}
        </div>
        <div class="artifact-browse-info" onclick="openArtifactFromBrowse('${esc(a.id)}', '${esc(a.session_id)}', '${esc(a.agent_id)}')">
          <div class="artifact-browse-name">${esc(a.name)}</div>
          <div class="artifact-browse-meta">
            <span class="abm-type">${esc(a.type)}</span>
            <span>v${a.latest_version || 1}</span>
            ${ago ? `<span>· ${ago}</span>` : ''}
            ${roleBadge}
            ${sourceBadge}
          </div>
        </div>
      </div>
    `;
  }
  grid.innerHTML = html;
  if (window.Favourites?.mount) {
    grid.querySelectorAll('.artifact-browse-fav-slot').forEach(slot => {
      const id = slot.dataset.artFavId;
      const agent = slot.dataset.artFavAgent || 'main';
      if (!id) return;
      window.Favourites.mount(slot, {
        item_type: 'artifact',
        item_id: id,
        agent_id: agent,
      });
    });
  }
}

function filterArtifactsBrowse(type) {
  _browseArtifactsFilter = type;
  // Update tab active states
  document.querySelectorAll('#artifacts-tabs .artifacts-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  renderArtifactsBrowse();
}

function filterArtifactsSource(source) {
  _browseArtifactsSource = source;
  document.querySelectorAll('#artifacts-source-tabs .artifacts-source-tab').forEach(t => {
    const isActive = t.getAttribute('data-source') === source;
    t.classList.toggle('active', isActive);
    t.style.color = isActive ? 'var(--text-000)' : 'var(--text-300)';
    t.style.borderBottomColor = isActive ? 'var(--text-000)' : 'transparent';
    t.style.fontWeight = isActive ? '500' : 'normal';
  });
  renderArtifactsBrowse();
}

function filterArtifactsRole(role) {
  _browseArtifactsRole = role;
  document.querySelectorAll('#artifacts-role-tabs .artifacts-role-tab').forEach(t => {
    const isActive = t.getAttribute('data-role') === role;
    t.classList.toggle('active', isActive);
    t.style.background = isActive ? 'var(--bg-300)' : 'transparent';
    t.style.color = isActive ? 'var(--text-000)' : 'var(--text-300)';
  });
  renderArtifactsBrowse();
}

function setArtifactsBrowseAgent(agentId) {
  _browseArtifactsAgent = agentId;
  loadArtifactsBrowse();
}

async function openArtifactFromBrowse(artifactId, sessionId, agentId) {
  // Scheduled-task artifacts have a synthetic session_id `sched-<run_id>`
  // that has no row in `sessions`. The normal openSession path would 404.
  // Route those to a read-only timeline view built from schedule_history
  // + traces (the same data the Run detail modal uses).
  if (sessionId && sessionId.startsWith('sched-')) {
    const runId = parseInt(sessionId.split('-', 2)[1]);
    if (Number.isFinite(runId)) {
      await openScheduledArtifact(runId, sessionId, agentId, artifactId);
      return;
    }
  }

  try {
    const resp = await API.getArtifacts(sessionId);
    state.artifacts[sessionId] = resp.artifacts || [];
  } catch (e) {
    state.artifacts[sessionId] = [];
  }

  await openSession(sessionId, agentId);
  setTimeout(() => openArtifactPanel(artifactId), 300);
}

// Open a scheduled-task artifact: synthesize a read-only "chat" from the
// run_detail response (trace spans + task prompt + result text) and show
// the artifact in the side panel.
async function openScheduledArtifact(runId, sessionId, agentId, artifactId) {
  let detail;
  try {
    detail = await API.manageSchedule({ action: 'run_detail', run_id: runId });
    if (detail.error) { showToast(detail.error, true); return; }
  } catch (e) { showToast('Lauf konnte nicht geladen werden: ' + e.message, true); return; }

  // Artifacts list for this session (so openArtifactPanel's lookup works).
  try {
    const resp = await API.getArtifacts(sessionId);
    state.artifacts[sessionId] = resp.artifacts || [];
  } catch (e) {
    state.artifacts[sessionId] = [];
  }

  selectAgent(agentId);
  const chat = state.ensureAgentChat(agentId);
  chat.sessionId = sessionId;
  chat.messages = [];
  chat.streamingText = '';
  chat.thinkingText = '';
  chat.files = [];
  chat._tokensIn = 0;
  chat._tokensOut = 0;
  chat._readonly = true;
  chat._scheduledRun = detail.run;  // stash for header badge
  state.activeScheduledRunId = runId;
  const runRow = detail.run || {};
  chat.model = runRow.model || chat.model;
  chat.chatTitle = `${runRow.schedule_name || 'Geplante Aufgabe'} · Lauf #${runId}`;

  // Build pseudo-message stream: user task, thinking+tool_call/result per
  // round in trace order, then the final assistant result.
  const spans = detail.spans || [];
  const toolSpans = spans.filter(s => s.type === 'tool_call')
    .sort((a, b) => (a.started_at || '').localeCompare(b.started_at || ''));

  chat.messages.push({
    role: 'user',
    content: runRow.task || '',
    _ts: runRow.started_at,
  });

  for (const s of toolSpans) {
    let meta = {};
    try { meta = s.metadata ? JSON.parse(s.metadata) : {}; } catch (e) {}
    chat.messages.push({
      role: 'tool_call',
      name: s.name,
      args: {},  // args aren't stored in trace metadata; summary lives in tool_result below
      tool_round: null,
    });
    chat.messages.push({
      role: 'tool_result',
      name: s.name,
      result: meta.result_summary || '',
      tool_round: null,
      _status: s.status,
      _duration_ms: s.duration_ms,
    });
  }

  // Strip the "[Duration: Xs | Tools: N]\n\n" header that complete_execution
  // prepends to result so the final assistant bubble shows only the model's
  // actual closing message.
  let finalText = runRow.result || '';
  finalText = finalText.replace(/^\[Duration:[^\]]+\]\s*\n+/, '');
  if (finalText) {
    chat.messages.push({
      role: 'assistant',
      content: finalText,
      _ts: runRow.finished_at,
    });
  }

  // Navigate first (this clears any prior readonly UI via the hook), then
  // render + re-apply readonly for THIS run.
  navigateTo('chat');
  chat._readonly = true;
  chat._scheduledRun = detail.run;
  if (typeof renderMessages === 'function') renderMessages();
  _applyScheduledReadonlyUI(runRow);
  if (artifactId) {
    setTimeout(() => openArtifactPanel(artifactId), 200);
  } else {
    // No specific artifact requested — open the right panel and show the
    // session's artifact list so the user can pick an output or drill into
    // a technical file.
    setTimeout(() => {
      if (typeof openRightPanel === 'function') openRightPanel('artifacts');
    }, 200);
  }
}

function _applyScheduledReadonlyUI(runRow) {
  // Slap a header badge + disable the composer so the user can't try to
  // send a follow-up into a non-existent session. Stored on chat._readonly
  // so a later openSession clears it naturally.
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send-btn');
  if (input) { input.disabled = true; input.placeholder = 'Schreibgeschützt — Protokoll der geplanten Aufgabe'; }
  if (sendBtn) sendBtn.disabled = true;

  // Banner above messages.
  let banner = document.getElementById('scheduled-readonly-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'scheduled-readonly-banner';
    banner.style.cssText = 'margin:0 20px 8px;padding:10px 14px;border:1px solid rgba(245,158,11,0.35);background:rgba(245,158,11,0.08);border-radius:8px;font-size:13px;color:var(--text-200);display:flex;align-items:center;gap:10px';
    const msgs = document.getElementById('messages') || document.querySelector('.messages-container');
    if (msgs && msgs.parentElement) msgs.parentElement.insertBefore(banner, msgs);
  }
  const status = runRow.status || '?';
  const statusColor = (status === 'success' || status === 'completed') ? '#10b981'
    : (status === 'timeout' ? '#f59e0b' : '#ef4444');
  const running = status === 'running';
  // Layout flips: the banner becomes a two-row block so we can tuck the
  // task prompt into a <details> without squeezing the header line.
  banner.style.cssText = 'margin:0 20px 8px;padding:10px 14px;border:1px solid rgba(245,158,11,0.35);background:rgba(245,158,11,0.08);border-radius:8px;font-size:13px;color:var(--text-200);display:flex;flex-direction:column;gap:8px';
  const taskText = runRow.task || '';
  banner.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px">
      <span style="width:8px;height:8px;border-radius:50%;background:${statusColor};flex-shrink:0"></span>
      <div style="flex:1;min-width:0">
        <div style="color:var(--text-100);font-weight:500">Geplante Aufgabe · ${esc(runRow.schedule_name || '')} · Lauf #${runRow.id}</div>
        <div style="color:var(--text-400);font-size:11px;margin-top:2px">
          ${runRow.started_at ? esc(new Date(runRow.started_at+'Z').toLocaleString()) : ''}
          · <span style="color:${statusColor}">${esc(status)}</span>
          ${runRow.duration_ms != null ? ` · ${(runRow.duration_ms/1000).toFixed(1)}s` : ''}
          · ${runRow.tool_calls || 0} Tool-Aufrufe
          ${runRow.model ? ' · ' + esc(runRow.model) : ''}
        </div>
      </div>
      <button onclick="_schedViewRunDetail(${runRow.id})" style="padding:4px 10px;border-radius:6px;background:var(--bg-200);color:var(--text-200);font-size:12px;border:1px solid var(--border-100);cursor:pointer">Details</button>
      <button ${running ? 'disabled title="Ein laufender Lauf kann nicht gelöscht werden"' : ''} onclick="_schedDeleteRunFromBanner(${runRow.id})" style="padding:4px 10px;border-radius:6px;background:transparent;color:var(--text-300);font-size:12px;border:1px solid var(--border-100);cursor:${running?'not-allowed':'pointer'}">Lauf löschen</button>
    </div>
    ${taskText ? `<details style="margin-left:18px">
      <summary style="cursor:pointer;font-size:11px;color:var(--text-400);text-transform:uppercase;letter-spacing:0.05em;list-style:none">Aufgaben-Prompt</summary>
      <div style="margin-top:6px;padding:8px;background:var(--bg-100);border-radius:6px;font-size:12px;color:var(--text-200);white-space:pre-wrap;max-height:160px;overflow:auto">${esc(taskText)}</div>
    </details>` : ''}
  `;
}

async function _schedDeleteRunFromBanner(runId) {
  if (!await showConfirmDanger(`Lauf #${runId} löschen?\n\nDamit werden der Verlaufseintrag und alle von diesem Lauf erzeugten Artefakte (inklusive Dateien) entfernt.`, 'Lauf löschen', 'Löschen')) return;
  try {
    const res = await API.manageSchedule({ action: 'delete_run', run_id: runId });
    if (res && res.error) { showToast(res.error, true); return; }
    showToast(`Lauf gelöscht · ${res.artifacts_removed||0} Artefakt(e) entfernt`);
    // Leave the read-only view — no session to return to.
    navigateTo('scheduled');
  } catch(e) {
    showToast('Fehlgeschlagen: ' + e.message, true);
  }
}

// Hook navigateTo so leaving 'chat' view for a scheduled run clears the
// readonly banner/composer when the user goes elsewhere.
(function() {
  const _origNav = window.navigateTo;
  if (typeof _origNav !== 'function') return;
  window.navigateTo = function(view) {
    const banner = document.getElementById('scheduled-readonly-banner');
    if (banner) banner.remove();
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('send-btn');
    if (input && input.disabled && state.activeChat?._readonly) {
      input.disabled = false;
      input.placeholder = '';
    }
    if (sendBtn && sendBtn.disabled && state.activeChat?._readonly) {
      sendBtn.disabled = false;
    }
    if (state.activeChat) {
      state.activeChat._readonly = false;
      state.activeChat._scheduledRun = null;
    }
    // Drop the active-run marker when leaving the read-only run viewer to a
    // non-scheduled context. openScheduledArtifact resets it AFTER navigateTo
    // returns, so chat-view re-entries from a fresh run-click stay correct.
    if (view !== 'scheduled' && view !== 'chat') {
      state.activeScheduledRunId = null;
    }
    return _origNav.apply(this, arguments);
  };
})();
