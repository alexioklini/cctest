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
      showToast(`${file.name} is empty — not attached`, true);
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
        showToast(`${file.name} could not be read — not attached`, true);
        return;
      }
      // base64 length × 3/4 ≈ raw size. Cross-check against the File's
      // declared size; a >50% shortfall means the read got truncated and
      // we should refuse rather than ship a corrupt file to the server.
      const decodedLen = Math.floor(b64.length * 3 / 4);
      if (decodedLen < file.size * 0.5) {
        showToast(`${file.name} read truncated (${decodedLen} / ${file.size} bytes) — not attached`, true);
        return;
      }
      const entry = {
        name: file.name,
        type: file.type || 'application/octet-stream',
        data: b64,
        encoding: 'base64',
        preview: isImage ? result : null,
        // Upload-time scan state: 'pending' while in flight, 'done' with
        // {findings, finding_count, categories} or {reason} after the
        // /v1/attachments/scan response. Composer blocks send while any
        // file is 'pending' OR has a blocking 'reason'.
        scan: { state: 'pending' },
      };
      state._pendingFiles.push(entry);
      renderFilePreviews();
      updateSendButton();
      schedulePIIBadgeUpdate();
      scanPendingAttachment(entry);
    };
    reader.onerror = () => {
      showToast(`${file.name} failed to read: ${reader.error?.message || 'unknown'}`, true);
    };
    reader.readAsDataURL(file);
  }
  event.target.value = '';
}

// Background scan: POST the just-attached file to /v1/attachments/scan and
// mutate the entry with the response. The composer's send-button gate +
// the PII modal both read `entry.scan` to decide what to do.
async function scanPendingAttachment(entry) {
  try {
    // Empty string when no session exists yet (composer pre-create) — the
    // server falls back to a per-user scratch dir. Server still requires
    // auth, just not a session.
    const sessionId = state.activeChat?.sessionId || '';
    const res = await API.scanAttachment(sessionId, entry);
    entry.scan = Object.assign({ state: 'done' }, res || {});
  } catch (e) {
    entry.scan = { state: 'done', scanned: false, reason: 'extract_failed',
                   error: String(e && e.message || e) };
  }
  renderFilePreviews();
  updateSendButton();
  schedulePIIBadgeUpdate();
}

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
        let scanBadge = '';
        const sc = f.scan || {};
        if (sc.state === 'pending') {
          scanBadge = `<span title="Scanning for PII…" style="font-size:10px;color:var(--text-300)">⏳</span>`;
        } else if (sc.scanned === false &&
                   ['too_large','extract_timeout','extract_failed','unsupported'].includes(sc.reason)) {
          const r = {
            'too_large': 'too large for PII scan',
            'extract_timeout': 'PII scan timed out',
            'extract_failed': 'PII scan failed',
            'unsupported': 'unsupported format — cannot scan',
          }[sc.reason];
          scanBadge = `<span title="${esc(r)} — remove to send" style="color:#dc2626;font-weight:bold">⛔</span>`;
        } else if (sc.scanned && sc.finding_count > 0) {
          scanBadge = `<span title="${sc.finding_count} PII finding(s)" style="color:#d97706;font-weight:bold">🛡️ ${sc.finding_count}</span>`;
        }
        // Classification badge — only when there's a non-trivial finding
        // (level above public or a mismatch). Phase B detector adds
        // sc.classification = {final_level, marker_level, mismatch, effective_action, level_label_de}
        let clsBadge = '';
        const cls = sc.classification || null;
        if (cls && cls.final_level && cls.final_level !== 'public') {
          const lvl = cls.final_level;
          const label = cls.level_label_de || lvl;
          const act = cls.effective_action || 'ignore';
          const pillColor = lvl === 'strict' ? '#a02020'
                          : lvl === 'confidential' ? '#8a5a00'
                          : lvl === 'internal' ? '#1e4189'
                          : '#6e5a3a';
          const icon = act === 'block' ? '⛔'
                     : act === 'force_local' ? '🏠'
                     : '🔒';
          clsBadge = `<span title="${esc(label)} — action: ${act}" style="color:${pillColor};font-weight:bold">${icon} ${esc(label)}</span>`;
        }
        chip.innerHTML = `
          <span>${fileTypeIcon(f.name)}</span>
          <span style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.name)}</span>
          ${scanBadge}
          ${clsBadge}
          <button class="image-preview-remove" onclick="removePendingFile(${i})">&#10005;</button>
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
  document.addEventListener('drop', async (e) => {
    e.preventDefault();
    dragCounter = 0;
    overlay.style.display = 'none';
    const files = e.dataTransfer?.files;
    if (!files?.length) return;

    for (const file of files) {
      if (window.electronAPI?.readDroppedFile && file.path) {
        const result = await window.electronAPI.readDroppedFile(file.path);
        if (result && !result.error) {
          result.scan = { state: 'pending' };
          state._pendingFiles.push(result);
          renderFilePreviews();
          updateSendButton();
          schedulePIIBadgeUpdate();
          scanPendingAttachment(result);
        }
      } else {
        // Same empty / truncated-read guards as handleFileSelect — see
        // the comment there for the failure mode this prevents. Keeps
        // the drop and file-picker paths symmetric so a Brain user who
        // drags in a corrupted file gets the same toast as one who
        // picks it.
        if (file.size === 0) {
          showToast(`${file.name} is empty — not attached`, true);
          continue;
        }
        const isImage = file.type.startsWith('image/');
        const reader = new FileReader();
        reader.onload = (ev) => {
          const result = ev.target.result || '';
          const commaIdx = result.indexOf(',');
          const b64 = commaIdx >= 0 ? result.slice(commaIdx + 1) : '';
          if (!b64) {
            showToast(`${file.name} could not be read — not attached`, true);
            return;
          }
          const decodedLen = Math.floor(b64.length * 3 / 4);
          if (decodedLen < file.size * 0.5) {
            showToast(`${file.name} read truncated (${decodedLen} / ${file.size} bytes) — not attached`, true);
            return;
          }
          const entry = {
            name: file.name,
            type: file.type || 'application/octet-stream',
            data: b64,
            encoding: 'base64',
            preview: isImage ? result : null,
            scan: { state: 'pending' },
          };
          state._pendingFiles.push(entry);
          renderFilePreviews();
          updateSendButton();
          schedulePIIBadgeUpdate();
          scanPendingAttachment(entry);
        };
        reader.onerror = () => {
          showToast(`${file.name} failed to read: ${reader.error?.message || 'unknown'}`, true);
        };
        reader.readAsDataURL(file);
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
        <pre style="font-family:var(--font-mono);font-size:13px;line-height:1.5;overflow-x:auto;white-space:pre-wrap;word-break:break-word;max-height:60vh;overflow-y:auto;background:var(--code-bg);padding:16px;border-radius:8px"><code>${esc(data.content || data.preview || 'Empty file')}</code></pre>
      </div>
      <div class="modal-footer">
        <a href="${API.getFileDownloadUrl(path)}" class="btn-primary" download>Download</a>
      </div>
    `;

    overlay.appendChild(content);
    document.body.appendChild(overlay);
  } catch(e) {
    showToast('Failed to preview file', true);
  }
}

/* ═══════════════════════════════════════════════════════════
   MESSAGE ACTIONS
   ═══════════════════════════════════════════════════════════ */
function copyMessage(idx) {
  const chat = state.activeChat;
  if (!chat?.messages[idx]) return;
  const text = chat.messages[idx].content || '';
  navigator.clipboard.writeText(text).then(() => showToast('Copied'));
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
  const label = mode === 'before' ? `Remove ${count} message${count !== 1 ? 's' : ''} before this point?` :
                mode === 'after' ? `Remove ${count} message${count !== 1 ? 's' : ''} after this point?` :
                mode === 'response' ? 'Remove this response?' :
                'Remove this Q&A pair?';
  if (!await showConfirmDanger(label, 'Remove messages', 'Remove')) return;

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
  showToast(`Removed ${realCount || count} message${(realCount || count) !== 1 ? 's' : ''}`);
}

function copyCodeBlock(btn) {
  const pre = btn.closest('.code-block-header')?.nextElementSibling;
  const code = pre?.querySelector('code')?.textContent || '';
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 1500);
  });
}

