/* ═══════════════════════════════════════════════════════════
   FILE HANDLING
   ═══════════════════════════════════════════════════════════ */
function triggerFileUpload() {
  document.getElementById('file-input').click();
}

function handleFileSelect(event) {
  const files = event.target.files;
  for (const file of files) {
    const isImage = file.type.startsWith('image/');
    // Reject empty files up front. We've seen the chat-attachment pipeline
    // hand the server a 22-byte "empty zip" file when the underlying Blob
    // gets invalidated mid-read (notably from automated test harnesses, but
    // also possible with revoked object URLs or interrupted reads). Without
    // this guard the walker downstream — file_pseudonymize for anonymise
    // turns, or read_document for normal turns — sees a truncated file and
    // fails opaquely; the user has no way to tell what went wrong.
    if (file.size === 0) {
      showToast(`${file.name} ist leer — nicht angehängt`, true);
      continue;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const result = e.target.result || '';
      const commaIdx = result.indexOf(',');
      const b64 = commaIdx >= 0 ? result.slice(commaIdx + 1) : '';
      // A read that produces no payload is treated the same as a 0-byte
      // file — silently pushing an empty `data` would let the broken
      // attachment slip past every downstream check.
      if (!b64) {
        showToast(`${file.name} konnte nicht gelesen werden — nicht angehängt`, true);
        return;
      }
      // base64 length × 3/4 ≈ raw size. Cross-check against the File's
      // declared size; a >50% shortfall means the read got truncated and
      // we should refuse rather than ship a corrupt file to the server.
      const decodedLen = Math.floor(b64.length * 3 / 4);
      if (decodedLen < file.size * 0.5) {
        showToast(`${file.name} unvollständig gelesen (${decodedLen} / ${file.size} Bytes) — nicht angehängt`, true);
        return;
      }
      const entry = {
        name: file.name,
        type: file.type || 'application/octet-stream',
        data: b64,
        encoding: 'base64',
        preview: isImage ? result : null,
        // Scan state. 'deferred' = NOT scanned at attach time (9.205.0): the
        // attachment PII/classification scan now runs at SEND time, together
        // with the typed-text scan, under one cancellable progress overlay —
        // the heavy work (extract/OCR/NER) is the attachment scan, so showing
        // its progress + cancel there is where it makes sense. The send flow
        // turns this into {state:'done', findings_full, classification, …}.
        scan: { state: 'deferred' },
      };
      state._pendingFiles.push(entry);
      renderFilePreviews();
      updateSendButton();
      schedulePIIBadgeUpdate();
    };
    reader.onerror = () => {
      showToast(`${file.name} konnte nicht gelesen werden: ${reader.error?.message || 'unbekannt'}`, true);
    };
    reader.readAsDataURL(file);
  }
  event.target.value = '';
}

// (scanPendingAttachment removed in 9.205.0 — attachments are now scanned at
// SEND time inside runCancellableGdprScan, under the shared progress overlay,
// instead of in the background at attach time.)

function renderImagePreviews() {
  // Legacy — images now unified into _pendingFiles with preview prop
  renderFilePreviews();
}

function removePendingImage(idx) {
  // Legacy — now handled by removePendingFile
  removePendingFile(idx);
}

function renderFilePreviews() {
  const containers = ['welcome-image-preview', 'chat-image-preview', 'project-image-preview'];
  for (const id of containers) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (!state._pendingFiles.length) {
      el.classList.remove('has-images');
      el.innerHTML = '';
      continue;
    }
    el.classList.add('has-images');
    el.innerHTML = '';
    for (let i = 0; i < state._pendingFiles.length; i++) {
      const f = state._pendingFiles[i];
      if (f.preview) {
        // Image thumbnail
        const card = document.createElement('div');
        card.className = 'image-preview-card';
        card.innerHTML = `
          <img src="${f.preview}" alt="">
          <button class="image-preview-remove" onclick="removePendingFile(${i})">&#10005;</button>
        `;
        el.appendChild(card);
      } else {
        // File chip
        const chip = document.createElement('div');
        chip.className = 'file-chip';
        chip.style.cssText = 'display:flex;align-items:center;gap:6px;padding:6px 10px;border-radius:8px;border:1px solid var(--border-200);background:var(--bg-200);font-size:12px;position:relative;height:64px;box-sizing:border-box';
        // PII scan badge: hourglass while in flight; red ban-icon for
        // blocking failures (unscannable); orange shield with count for
        // findings; nothing for clean files / accepted gaps.
        //
        // When the file is also classified above public OR has a
        // mismatch, the PII tooltip is augmented with the German
        // classification summary so the user sees both signals in a
        // single hover without having to chase a second badge.
        let scanBadge = '';
        const sc = f.scan || {};
        // Pre-build a classification tooltip fragment (German). The
        // detector reports two INDEPENDENT signals — both are always
        // surfaced; neither overrides the other:
        //   (1) "Aktuelle analysierte Klassifikation" = the level the
        //       content analysis arrived at (heuristic_level from
        //       content_signals; defaults to Öffentlich when nothing is
        //       flagged).
        //   (2) "Im File klassifiziert als" = the marker physically
        //       written into the document (marker_level), or
        //       "Nicht klassifiziert" when none exists.
        const _cls = sc.classification || null;
        let clsTooltipFragment = '';
        if (_cls) {
          const _lvlDE = {
            public: 'Öffentlich',
            internal: 'Intern',
            confidential: 'Vertraulich',
            strict: 'Streng vertraulich',
          };
          const _possDE = {
            ignore: 'Senden ohne Einschränkung möglich.',
            warn: 'Senden möglich — Hinweis: Inhalt ist als sensibel erkannt.',
            force_local: 'Senden nur an ein lokales Modell möglich. Cloud-Modelle sind blockiert.',
            block: 'Senden nicht möglich. Anhang entfernen, um fortzufahren.',
          };
          const _markerLvl = _cls.marker_level || null;
          const _heur = (_cls.content_signals && _cls.content_signals.heuristic_level) || 'public';
          const _interesting = _markerLvl
                            || _heur !== 'public'
                            || ['warn','force_local','block'].includes(_cls.effective_action);
          if (_interesting) {
            const _analyzedLine = _lvlDE[_heur] || _heur;
            const _markLine = _markerLvl
              ? (_lvlDE[_markerLvl] || _markerLvl)
              : 'Nicht klassifiziert';
            const _poss = _possDE[_cls.effective_action] || _cls.effective_action || '';
            const _parts = [
              `Aktuelle analysierte Klassifikation: ${_analyzedLine}`,
              `Im File klassifiziert als: ${_markLine}`,
            ];
            if (_poss) _parts.push(`Was jetzt möglich ist: ${_poss}`);
            clsTooltipFragment = '\n\n— Klassifikation —\n' + _parts.join('\n');
          }
        }
        if (sc.state === 'pending') {
          scanBadge = `<span title="Wird auf PII geprüft…" style="font-size:10px;color:var(--text-300)">⏳</span>`;
        } else if (sc.scanned === false &&
                   ['too_large','unsupported'].includes(sc.reason)) {
          // BLOCKING gaps: structural rejections — send stays disabled until
          // the file is removed (mirrors BLOCKING_REASONS in chat_send.js).
          const r = {
            'too_large': 'zu groß für PII-Prüfung',
            'unsupported': 'nicht unterstütztes Format — keine Prüfung möglich',
          }[sc.reason];
          const _t = r + (clsTooltipFragment ? clsTooltipFragment : '');
          scanBadge = `<span title="${esc(_t)} — zum Senden entfernen" style="color:#dc2626;font-weight:bold;cursor:help">⛔</span>`;
        } else if (sc.scanned === false &&
                   ['extract_timeout','extract_failed'].includes(sc.reason)) {
          // NON-BLOCKING gaps: the scan tried but didn't finish/succeed. The
          // user can send anyway (or remove the file) — surface it as an
          // amber warning, NOT the red ⛔ "remove to send".
          const r = {
            'extract_timeout': 'PII-Prüfung nicht rechtzeitig fertig — Senden trotzdem möglich',
            'extract_failed': 'PII-Prüfung fehlgeschlagen — Senden trotzdem möglich',
          }[sc.reason];
          const _t = r + (clsTooltipFragment ? clsTooltipFragment : '');
          scanBadge = `<span title="${esc(_t)}" style="color:#d97706;font-weight:bold;cursor:help">⚠️</span>`;
        } else if (sc.scanned && sc.finding_count > 0) {
          // PII findings present — surface the count and fold the
          // classification summary into the same tooltip per user request
          // (one symbol carries both signals on hover).
          const _t = `${sc.finding_count} PII-Treffer im Anhang` + clsTooltipFragment;
          scanBadge = `<span title="${esc(_t)}" style="color:#d97706;font-weight:bold;cursor:help">🛡️ ${sc.finding_count}</span>`;
        }
        // Classification badge — only when there's a non-trivial finding
        // (level above public or a mismatch). Phase B detector adds
        // sc.classification = {final_level, marker_level, mismatch,
        //   effective_action, level_label_de}.
        // We render a single icon as the chip badge; the tooltip carries
        // the full story in German (detected level, marker in file,
        // resulting action). For force_local / block we also surface an
        // extra status line under the chip so the user doesn't need to
        // hover to see that the send is gated.
        // Chip-badge text = "Aktuelle analysierte Klassifikation" (signal
        // 1) — Öffentlich / Intern / Vertraulich / Streng vertraulich.
        // Falls back to the in-file marker only when the analysis didn't
        // run yet. Severity colour tracks the analysed level, since
        // that's the state the badge represents.
        let clsBadge = '';
        let clsStatus = '';
        const cls = sc.classification || null;
        if (cls) {
          const markerLvl = cls.marker_level || null;
          const act = cls.effective_action || 'ignore';
          const heur = (cls.content_signals && cls.content_signals.heuristic_level) || 'public';
          // Render the chip when either signal is interesting OR an
          // enforcement action applies. A document that's "Öffentlich"
          // analysed AND has no marker gets no chip — nothing to say.
          const interesting = !!markerLvl
                            || heur !== 'public'
                            || ['warn','force_local','block'].includes(act);
          if (interesting) {
            const LEVEL_DE = {
              public: 'Öffentlich',
              internal: 'Intern',
              confidential: 'Vertraulich',
              strict: 'Streng vertraulich',
            };
            const POSSIBILITY_DE = {
              ignore: 'Senden ohne Einschränkung möglich.',
              warn: 'Senden möglich — Hinweis: Inhalt ist als sensibel erkannt.',
              force_local: 'Senden nur an ein lokales Modell möglich. Cloud-Modelle sind blockiert.',
              block: 'Senden nicht möglich. Anhang entfernen, um fortzufahren.',
            };
            // Chip label = the analysed level. Always one of the four
            // German classification names; never "Nicht klassifiziert"
            // (that's a property of the marker, not the analysis).
            const analyzedLabel = LEVEL_DE[heur] || heur;
            const markerLabel = markerLvl
              ? (LEVEL_DE[markerLvl] || markerLvl)
              : 'Nicht klassifiziert';
            // Colour tracks the analysed level — that's what the badge
            // represents. Streng vertraulich → red, Vertraulich → amber,
            // Intern → blue, Öffentlich → neutral.
            const pillColor = heur === 'strict' ? '#a02020'
                            : heur === 'confidential' ? '#8a5a00'
                            : heur === 'internal' ? '#1e4189'
                            : '#6e5a3a';
            // Tooltip body — exactly the three lines the user asked for.
            const tipLines = [
              `Aktuelle analysierte Klassifikation: ${analyzedLabel}`,
              `Im File klassifiziert als: ${markerLabel}`,
              `Was jetzt möglich ist: ${POSSIBILITY_DE[act] || act}`,
            ];
            const tip = tipLines.join('\n');
            clsBadge = `<span title="${esc(tip)}" style="color:${pillColor};font-weight:bold;cursor:help">${esc(analyzedLabel)}</span>`;
            // Inline status line — only for the cases where the user
            // can't send normally. Warn-only stays tooltip-only to avoid
            // visual clutter on the common case.
            if (act === 'block') {
              clsStatus = `<div style="flex-basis:100%;font-size:11px;color:#a02020;margin-top:2px">Senden nicht möglich. Anhang entfernen, um fortzufahren.</div>`;
            } else if (act === 'force_local') {
              clsStatus = `<div style="flex-basis:100%;font-size:11px;color:#8a5a00;margin-top:2px">Nur lokales Modell erlaubt.</div>`;
            }
          }
        }
        // flex-wrap so the inline status line lands on its own row under
        // the icon/name/badges instead of getting squeezed inline.
        if (clsStatus) chip.style.flexWrap = 'wrap';
        chip.innerHTML = `
          <span>${fileTypeIcon(f.name)}</span>
          <span style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.name)}</span>
          ${scanBadge}
          ${clsBadge}
          <button class="image-preview-remove" onclick="removePendingFile(${i})">&#10005;</button>
          ${clsStatus}
        `;
        el.appendChild(chip);
      }
    }
  }
}

function removePendingFile(idx) {
  state._pendingFiles.splice(idx, 1);
  renderFilePreviews();
  updateSendButton();
  schedulePIIBadgeUpdate();
}

/* ── Drag & Drop ─────────────────────────────────────────────── */
(function initDragDrop() {
  let dragCounter = 0;
  const overlay = document.getElementById('drop-overlay');
  if (!overlay) return;

  document.addEventListener('dragenter', (e) => {
    e.preventDefault();
    if (!e.dataTransfer?.types?.includes('Files')) return;
    dragCounter++;
    overlay.style.display = 'flex';
  });
  document.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) { dragCounter = 0; overlay.style.display = 'none'; }
  });
  document.addEventListener('dragover', (e) => {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  });
  // Read a single browser File into a pending-file entry. Shared by the
  // top-level drop path and the recursive directory walk so a file dragged
  // in inside a folder gets the same empty/truncated guards (and the same
  // toasts) as one dropped directly. Returns a Promise that resolves when
  // the entry is pushed (or rejected with a toast already shown).
  function pushDroppedFile(file) {
    return new Promise((resolve) => {
      // Same empty / truncated-read guards as handleFileSelect — see
      // the comment there for the failure mode this prevents.
      if (file.size === 0) {
        showToast(`${file.name} ist leer — nicht angehängt`, true);
        resolve();
        return;
      }
      const isImage = file.type.startsWith('image/');
      const reader = new FileReader();
      reader.onload = (ev) => {
        const result = ev.target.result || '';
        const commaIdx = result.indexOf(',');
        const b64 = commaIdx >= 0 ? result.slice(commaIdx + 1) : '';
        if (!b64) {
          showToast(`${file.name} konnte nicht gelesen werden — nicht angehängt`, true);
          resolve();
          return;
        }
        const decodedLen = Math.floor(b64.length * 3 / 4);
        if (decodedLen < file.size * 0.5) {
          showToast(`${file.name} unvollständig gelesen (${decodedLen} / ${file.size} Bytes) — nicht angehängt`, true);
          resolve();
          return;
        }
        const entry = {
          name: file.name,
          type: file.type || 'application/octet-stream',
          data: b64,
          encoding: 'base64',
          preview: isImage ? result : null,
          scan: { state: 'deferred' },  // scanned at SEND time (see 9.205.0)
        };
        state._pendingFiles.push(entry);
        renderFilePreviews();
        updateSendButton();
        schedulePIIBadgeUpdate();
        resolve();
      };
      reader.onerror = () => {
        showToast(`${file.name} konnte nicht gelesen werden: ${reader.error?.message || 'unbekannt'}`, true);
        resolve();
      };
      reader.readAsDataURL(file);
    });
  }

  // Recursively collect every File under a browser FileSystemEntry
  // (webkitGetAsEntry result). Directories are walked depth-first;
  // readEntries returns at most ~100 entries per call, so we drain it in a
  // loop until it yields an empty batch. Symlink loops are not a concern —
  // the drag-drop FileSystem API never exposes them.
  function collectEntryFiles(entry) {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file((file) => resolve([file]), () => resolve([]));
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const all = [];
        const readBatch = () => {
          reader.readEntries(async (batch) => {
            if (!batch.length) {
              const nested = await Promise.all(all.map(collectEntryFiles));
              resolve(nested.flat());
              return;
            }
            all.push(...batch);
            readBatch();
          }, () => resolve([]));
        };
        readBatch();
      } else {
        resolve([]);
      }
    });
  }

  document.addEventListener('drop', async (e) => {
    e.preventDefault();
    dragCounter = 0;
    overlay.style.display = 'none';

    // The DataTransferItemList and its entries are only valid synchronously
    // within the drop handler — capture webkitGetAsEntry()/file.path now,
    // before any await, or they go stale. We snapshot both so the async
    // work below operates on plain references that survive the event.
    const items = e.dataTransfer?.items;
    const captured = [];
    if (items?.length) {
      for (const item of items) {
        if (item.kind !== 'file') continue;
        const file = item.getAsFile();
        const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
        // file.path (Electron) is the real filesystem path and is the only
        // way to tell a dropped folder from a file there — entry is null in
        // Electron's renderer for the drag-drop FileSystem API.
        captured.push({ file, entry, path: file?.path || (entry ? null : '') });
      }
    } else if (e.dataTransfer?.files?.length) {
      // Fallback for browsers that don't expose .items (rare).
      for (const file of e.dataTransfer.files) captured.push({ file, entry: null, path: file.path || '' });
    }
    if (!captured.length) return;

    for (const { file, entry, path: fsPath } of captured) {
      // Electron: native path is present. Folders need a recursive walk in
      // the main process (readDroppedFile reads a single file and errors on
      // a directory), so route through readDroppedFolder when available.
      if (window.electronAPI?.readDroppedFile && fsPath) {
        let results = null;
        if (window.electronAPI.readDroppedFolder) {
          results = await window.electronAPI.readDroppedFolder(fsPath);
        }
        // readDroppedFolder returns an array (file → 1 entry, dir → N,
        // empty/too-big → []). Without it, fall back to single-file read.
        if (!Array.isArray(results)) {
          const single = await window.electronAPI.readDroppedFile(fsPath);
          results = single && !single.error ? [single] : [];
        }
        for (const result of results) {
          if (!result || result.error) continue;
          result.scan = { state: 'deferred' };  // scanned at SEND time (9.205.0)
          state._pendingFiles.push(result);
          renderFilePreviews();
          updateSendButton();
          schedulePIIBadgeUpdate();
        }
        continue;
      }
      // Browser: walk directories via the FileSystem entry; fall back to the
      // plain File when no entry is available.
      if (entry) {
        const files = await collectEntryFiles(entry);
        for (const f of files) await pushDroppedFile(f);
      } else if (file) {
        await pushDroppedFile(file);
      }
    }
  });
})();

function fileTypeIcon(name) {
  const ext = name.split('.').pop()?.toLowerCase();
  const icons = {
    pdf: '&#128196;', doc: '&#128196;', docx: '&#128196;', pptx: '&#128202;', ppt: '&#128202;',
    xls: '&#128200;', xlsx: '&#128200;', csv: '&#128200;', tsv: '&#128200;',
    py: '&#128187;', js: '&#128187;', ts: '&#128187;', html: '&#128187;', css: '&#128187;',
    md: '&#128221;', txt: '&#128221;',
    png: '&#128247;', jpg: '&#128247;', jpeg: '&#128247;', gif: '&#128247;', svg: '&#128247;',
    json: '&#128196;', yaml: '&#128196;', yml: '&#128196;',
  };
  return icons[ext] || '&#128196;';
}

async function previewFile(path) {
  try {
    const data = await API.getFilePreview(path);
    // Show in modal
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const content = document.createElement('div');
    content.className = 'modal-content wide';
    content.innerHTML = `
      <div class="modal-header">
        <span class="modal-title">${esc(path.split('/').pop())}</span>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <pre style="font-family:var(--font-mono);font-size:13px;line-height:1.5;overflow-x:auto;white-space:pre-wrap;word-break:break-word;max-height:60vh;overflow-y:auto;background:var(--code-bg);padding:16px;border-radius:8px"><code>${esc(data.content || data.preview || 'Leere Datei')}</code></pre>
      </div>
      <div class="modal-footer">
        <a href="${API.getFileDownloadUrl(path)}" class="btn-primary" download>Herunterladen</a>
      </div>
    `;

    overlay.appendChild(content);
    document.body.appendChild(overlay);
  } catch(e) {
    showToast('Dateivorschau fehlgeschlagen', true);
  }
}

/* ═══════════════════════════════════════════════════════════
   MESSAGE ACTIONS
   ═══════════════════════════════════════════════════════════ */
function copyMessage(idx) {
  const chat = state.activeChat;
  if (!chat?.messages[idx]) return;
  const text = chat.messages[idx].content || '';
  navigator.clipboard.writeText(text).then(() => showToast('Kopiert'));
}

function retryMessage(idx) {
  const chat = state.activeChat;
  if (!chat) return;
  // Remove this assistant message and resend the previous user message
  const messages = chat.messages;
  // Find the user message before this assistant message
  let userMsgIdx = -1;
  for (let i = idx - 1; i >= 0; i--) {
    if (messages[i].role === 'human' || messages[i].role === 'user') {
      userMsgIdx = i;
      break;
    }
  }
  if (userMsgIdx < 0) return;

  const userText = messages[userMsgIdx].content;
  // Remove everything from userMsgIdx onward
  chat.messages = messages.slice(0, userMsgIdx);
  renderMessages();

  // Re-send
  const input = document.getElementById('chat-input');
  if (input) {
    input.value = userText;
    sendMessage();
  }
}

// --- Message edit/delete functions ---

function toggleMsgEditMenu(event, idx) {
  event.stopPropagation();
  // Close any other open menus
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(el => el.classList.remove('open'));
  const menu = document.getElementById(`msg-edit-menu-${idx}`);
  if (menu) menu.classList.toggle('open');
  // Close on click outside
  const close = (e) => {
    if (!menu.contains(e.target)) {
      menu.classList.remove('open');
      document.removeEventListener('click', close);
    }
  };
  setTimeout(() => document.addEventListener('click', close), 0);
}

async function deleteMessages(mode, idx) {
  const chat = state.activeChat;
  if (!chat?.sessionId) return;
  const messages = chat.messages;

  // Close menu
  document.querySelectorAll('.msg-edit-dropdown.open').forEach(el => el.classList.remove('open'));

  // Determine which message indices to remove.
  // Messages include synthetic tool_call/tool_result entries (no id) interleaved
  // between real user/assistant messages (have id from DB).
  let removeFrom, removeTo;

  if (mode === 'before') {
    // Remove everything before this message's block (include preceding tool blocks)
    let start = idx;
    while (start > 0 && (messages[start - 1].role === 'tool_call' || messages[start - 1].role === 'tool_result')) {
      start--;
    }
    // If this is an assistant msg, also include its preceding user message in what we keep
    if (messages[idx].role === 'assistant') {
      for (let i = start - 1; i >= 0; i--) {
        if (messages[i].role === 'user') { start = i; break; }
      }
    }
    removeFrom = 0;
    removeTo = start;
  } else if (mode === 'after') {
    // Remove everything after this message (keep this message and its preceding tools)
    // Find the end of the current message's "block" (message + trailing tool messages)
    let endOfBlock = idx + 1;
    while (endOfBlock < messages.length && (messages[endOfBlock].role === 'tool_call' || messages[endOfBlock].role === 'tool_result')) {
      endOfBlock++;
    }
    removeFrom = endOfBlock;
    removeTo = messages.length;
  } else if (mode === 'response') {
    // Remove this assistant response + its preceding tool blocks
    let start = idx;
    while (start > 0 && (messages[start - 1].role === 'tool_call' || messages[start - 1].role === 'tool_result')) {
      start--;
    }
    let end = idx + 1;
    removeFrom = start;
    removeTo = end;
  } else if (mode === 'turn') {
    // Remove the Q&A pair: find the user message before this, and the assistant after
    let userIdx = idx;
    // If clicked on assistant, find its user message
    if (messages[idx].role === 'assistant' || messages[idx].role === 'tool_call' || messages[idx].role === 'tool_result') {
      for (let i = idx; i >= 0; i--) {
        if (messages[i].role === 'user') { userIdx = i; break; }
      }
    }
    // Find the end of the assistant response (skip tool blocks after)
    let endIdx = userIdx + 1;
    while (endIdx < messages.length && messages[endIdx].role !== 'user') {
      endIdx++;
    }
    removeFrom = userIdx;
    removeTo = endIdx;
  }

  if (removeFrom === undefined || removeFrom >= removeTo) return;

  // Collect DB message IDs from the range (skip synthetic tool_call/tool_result without id)
  const idsToDelete = [];
  for (let i = removeFrom; i < removeTo; i++) {
    if (messages[i].id) idsToDelete.push(messages[i].id);
  }

  // Confirm for destructive actions
  const count = removeTo - removeFrom;
  const realCount = idsToDelete.length;
  const label = mode === 'before' ? `${count} Nachricht${count !== 1 ? 'en' : ''} vor dieser Stelle entfernen?` :
                mode === 'after' ? `${count} Nachricht${count !== 1 ? 'en' : ''} nach dieser Stelle entfernen?` :
                mode === 'response' ? 'Diese Antwort entfernen?' :
                'Dieses Frage-Antwort-Paar entfernen?';
  if (!await showConfirmDanger(label, 'Nachrichten entfernen', 'Entfernen')) return;

  // Collect artifact IDs from messages being removed (for local cleanup)
  const artifactIds = new Set();
  for (let i = removeFrom; i < removeTo; i++) {
    const m = messages[i];
    if (m._files) for (const f of m._files) { if (f.artifact_id) artifactIds.add(f.artifact_id); }
    if (m.metadata?.files) for (const f of m.metadata.files) { if (f.artifact_id) artifactIds.add(f.artifact_id); }
  }

  // Remove from server (also deletes artifacts)
  if (idsToDelete.length > 0) {
    await fetch(`${BASE_URL}/v1/sessions/manage`, {
      method: 'POST',
      headers: API._headers(),
      body: JSON.stringify({ action: 'delete_messages', session_id: chat.sessionId, message_ids: idsToDelete }),
    });
  }

  // Update local state
  chat.messages = [...messages.slice(0, removeFrom), ...messages.slice(removeTo)];
  // Remove deleted artifacts from registry
  if (artifactIds.size > 0 && state.artifacts[chat.sessionId]) {
    state.artifacts[chat.sessionId] = state.artifacts[chat.sessionId].filter(a => !artifactIds.has(a.id));
    // Close artifact panel if showing a deleted artifact
    if (state.activeArtifactId && artifactIds.has(state.activeArtifactId)) {
      closeArtifactPanel();
    }
  }
  renderMessages();
  showToast(`${realCount || count} Nachricht${(realCount || count) !== 1 ? 'en' : ''} entfernt`);
}

function copyCodeBlock(btn) {
  const pre = btn.closest('.code-block-header')?.nextElementSibling;
  const code = pre?.querySelector('code')?.textContent || '';
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = 'Kopiert!';
    setTimeout(() => btn.textContent = 'Kopieren', 1500);
  });
}

