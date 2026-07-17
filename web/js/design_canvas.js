// design_canvas.js — Design-Modus für HTML-Artefakte (v9.351.0, Phase A des
// Design-Modus-Plans). Kommentar-Loop statt Layout-Editor: der Nutzer klickt
// Elemente in der gerenderten Vorschau an, sammelt Änderungswünsche als Pins
// und schickt sie als EINEN normalen Chat-Turn ab (der Agent editiert per
// edit_file; artifact_updated rendert die Canvas neu). Die UI schreibt NIE
// selbst ins Artefakt — ein Schreiber (der Agent), jede Änderung versioniert.
//
// Das Artefakt-iframe ist sandbox="allow-scripts allow-same-origin" mit
// srcdoc → same-origin, also wird das Dokument DIREKT vom Host aus
// instrumentiert (Hover-Highlight, Klick-Picker). Kein Script-Inject in den
// Artefakt-Quelltext, kein postMessage nötig.
//
// Ein Global (DesignCanvas); aufgerufen aus panels_artifacts.js
// (renderArtifactContent html-Case + openArtifactPanel) und index.html
// (artifact-design-btn). Global <script>, no modules.

const DesignCanvas = (() => {
  let _active = false;
  let _artifactId = null;     // Artefakt, für das der Modus aktiviert wurde
  let _comments = [];         // {selector, preview, text, el}
  let _iframe = null;
  let _overlay = null;
  let _bar = null;
  let _hoverEl = null;
  let _rafPending = false;

  function isActive() { return _active; }

  // Beim Öffnen eines ANDEREN Artefakts den Modus verwerfen (openArtifactPanel).
  function resetFor(artifactId) {
    if (artifactId !== _artifactId) {
      _artifactId = artifactId;
      _active = false;
      _comments = [];
      _syncBtn();
    }
  }

  function _syncBtn() {
    document.getElementById('artifact-design-btn')?.classList.toggle('active', _active);
  }

  function _registryEntry() {
    const arts = state.artifacts[state.activeChat?.sessionId] || [];
    return arts.find(a => a.id === state.activeArtifactId) || null;
  }

  function toggle() {
    const container = document.getElementById('artifact-content');
    if (!container || container._rawType !== 'html') return;
    if (!_active) {
      // Nur auf der aktuellen Version: Kommentare gegen einen alten Stand
      // würden ins Leere editieren (edit_file arbeitet auf der Datei).
      const reg = _registryEntry();
      const versions = reg?.versions || [];
      const latest = reg?.latest_version || (versions.length ? versions[versions.length - 1].version : null);
      if (latest && Number(state.activeArtifactVersion) !== Number(latest)) {
        showToast('Design-Modus geht nur auf der aktuellen Version', true);
        return;
      }
      if (state.artifactSourceMode) toggleArtifactSource();
    }
    _active = !_active;
    _artifactId = state.activeArtifactId;
    _comments = [];
    _syncBtn();
    renderArtifactContent(container._rawContent, container._rawType,
                          container._rawName, container._rawEncoding);
  }

  // Ersatz für den plain-iframe-Zweig in renderArtifactContent (html-Case).
  // Jeder (Re-)Render verwirft offene Pins — nach artifact_updated können
  // Selektoren ungültig sein (Fail loud statt falsch verankern).
  function render(container, content) {
    _comments = [];
    container.innerHTML = '';
    const root = document.createElement('div');
    root.className = 'design-canvas';
    const frame = document.createElement('div');
    frame.className = 'design-canvas-frame';
    _iframe = document.createElement('iframe');
    _iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin');
    _overlay = document.createElement('div');
    _overlay.className = 'design-overlay';
    frame.appendChild(_iframe);
    frame.appendChild(_overlay);
    _bar = document.createElement('div');
    _bar.className = 'design-commentbar';
    root.appendChild(frame);
    root.appendChild(_bar);
    container.appendChild(root);
    _iframe.addEventListener('load', _instrument);
    _iframe.srcdoc = content;
    _renderBar();
  }

  function _instrument() {
    const doc = _iframe?.contentDocument;
    if (!doc) return;
    const st = doc.createElement('style');
    st.textContent = '.__bd-hover{outline:2px solid #3b82f6 !important;outline-offset:2px;cursor:crosshair !important;}';
    (doc.head || doc.documentElement).appendChild(st);
    doc.addEventListener('mouseover', (e) => {
      _hoverEl?.classList?.remove('__bd-hover');
      _hoverEl = _pickable(e.target) ? e.target : null;
      _hoverEl?.classList?.add('__bd-hover');
    }, true);
    doc.addEventListener('mouseout', () => {
      _hoverEl?.classList?.remove('__bd-hover');
      _hoverEl = null;
    }, true);
    // capture + preventDefault: blockt auch Link-Navigation im Entwurf.
    doc.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (_pickable(e.target)) _openBubble(e.target);
    }, true);
    _iframe.contentWindow.addEventListener('scroll', _scheduleReposition, true);
    _iframe.contentWindow.addEventListener('resize', _scheduleReposition);
  }

  function _pickable(el) {
    return el && el.nodeType === 1 && el.tagName !== 'HTML' && el.tagName !== 'BODY';
  }

  // Deterministischer CSS-Pfad: id → kürzeste eindeutige nth-of-type-Kette.
  function _cssPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node.tagName !== 'HTML' && node.tagName !== 'BODY') {
      if (node.id) { parts.unshift(`#${CSS.escape(node.id)}`); return parts.join(' > '); }
      let sel = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (same.length > 1) sel += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(sel);
      node = parent;
    }
    return parts.join(' > ');
  }

  function _closeBubble() {
    _overlay?.querySelector('.design-bubble')?.remove();
  }

  function _openBubble(el) {
    if (!_overlay) return;
    _closeBubble();
    const selector = _cssPath(el);
    const preview = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    const bubble = document.createElement('div');
    bubble.className = 'design-bubble';
    bubble.innerHTML = `
      <span class="design-bubble-sel" title="${esc(selector)}">${esc(selector)}</span>
      <textarea placeholder="Was soll hier geändert werden?"></textarea>
      <div class="design-bubble-attach">
        <input type="file" accept="image/*" style="display:none">
        <button class="design-btn design-attach-btn" data-act="attach" title="Bild anhängen (z. B. Screenshot zum Einfügen)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          Bild anhängen
        </button>
        <span class="design-attach-preview"></span>
      </div>
      <div class="design-bubble-row">
        <button class="design-btn" data-act="cancel">Abbrechen</button>
        <button class="design-btn primary" data-act="add">Kommentar hinzufügen</button>
      </div>`;
    const r = el.getBoundingClientRect();
    const box = _overlay.getBoundingClientRect();
    bubble.style.left = Math.max(8, Math.min(r.left, box.width - 280)) + 'px';
    bubble.style.top = Math.max(8, Math.min(r.bottom + 6, box.height - 180)) + 'px';
    // Optionales Bild am Kommentar ("füge diesen Screenshot hier ein"). Die
    // Bytes reiten beim Anwenden als normales Chat-Attachment über die
    // Send-Pipeline (GDPR-Scan, Disk-Ablage) — nie durch den Prompt-Text.
    let image = null;
    const fileInput = bubble.querySelector('input[type="file"]');
    const previewEl = bubble.querySelector('.design-attach-preview');
    bubble.querySelector('[data-act="attach"]').onclick = () => fileInput.click();
    fileInput.onchange = () => {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = '';
      if (!file) return;
      if (file.size === 0) { showToast(`${file.name} ist leer — nicht angehängt`, true); return; }
      const reader = new FileReader();
      reader.onload = (e) => {
        const result = e.target.result || '';
        const commaIdx = result.indexOf(',');
        const b64 = commaIdx >= 0 ? result.slice(commaIdx + 1) : '';
        if (!b64) { showToast(`${file.name} konnte nicht gelesen werden`, true); return; }
        image = { name: file.name, type: file.type || 'image/png', data: b64, preview: result };
        previewEl.innerHTML = `<img src="${result}" alt=""><span>${esc(file.name)}</span>
          <button class="design-chip-x" title="Bild entfernen">&times;</button>`;
        previewEl.querySelector('.design-chip-x').onclick = () => {
          image = null;
          previewEl.innerHTML = '';
        };
      };
      reader.onerror = () => showToast(`${file.name} konnte nicht gelesen werden`, true);
      reader.readAsDataURL(file);
    };
    bubble.querySelector('[data-act="cancel"]').onclick = _closeBubble;
    bubble.querySelector('[data-act="add"]').onclick = () => {
      const text = bubble.querySelector('textarea').value.trim();
      if (!text && !image) return;
      _comments.push({ selector, preview, text, el, image });
      _closeBubble();
      _renderPins();
      _renderBar();
    };
    _overlay.appendChild(bubble);
    bubble.querySelector('textarea').focus();
  }

  function _renderPins() {
    if (!_overlay) return;
    _overlay.querySelectorAll('.design-pin').forEach(p => p.remove());
    _comments.forEach((c, i) => {
      const pin = document.createElement('div');
      pin.className = 'design-pin';
      pin.textContent = String(i + 1);
      pin.title = c.text;
      _overlay.appendChild(pin);
      c._pin = pin;
    });
    _reposition();
  }

  function _scheduleReposition() {
    if (_rafPending) return;
    _rafPending = true;
    requestAnimationFrame(() => { _rafPending = false; _reposition(); });
  }

  function _reposition() {
    for (const c of _comments) {
      if (!c._pin) continue;
      if (!c.el || !c.el.isConnected) { c._pin.style.display = 'none'; continue; }
      const r = c.el.getBoundingClientRect();
      c._pin.style.display = '';
      c._pin.style.left = (r.right - 10) + 'px';
      c._pin.style.top = (r.top - 10) + 'px';
    }
  }

  function _removeComment(i) {
    _comments.splice(i, 1);
    _renderPins();
    _renderBar();
  }

  function _renderBar() {
    if (!_bar) return;
    if (!_comments.length) {
      _bar.innerHTML = '<span class="design-bar-hint">Element in der Vorschau anklicken, um einen Änderungswunsch zu notieren</span>';
      return;
    }
    const chips = _comments.map((c, i) =>
      `<span class="design-chip" title="${esc(c.selector)}"><span class="design-chip-num">${i + 1}</span>${c.image ? `<img class="design-chip-img" src="${c.image.preview}" alt="" title="${esc(c.image.name)}">` : ''}${esc((c.text || c.image.name).slice(0, 48))}<button class="design-chip-x" data-i="${i}" title="Entfernen">&times;</button></span>`
    ).join('');
    _bar.innerHTML = `${chips}<span class="design-bar-spacer"></span>
      <button class="design-btn" data-act="clear">Verwerfen</button>
      <button class="design-btn primary" data-act="apply">${_comments.length} ${_comments.length === 1 ? 'Kommentar' : 'Kommentare'} anwenden</button>`;
    _bar.querySelectorAll('.design-chip-x').forEach(b => {
      b.onclick = () => _removeComment(Number(b.dataset.i));
    });
    _bar.querySelector('[data-act="clear"]').onclick = () => { _comments = []; _renderPins(); _renderBar(); };
    _bar.querySelector('[data-act="apply"]').onclick = apply;
  }

  // Alle gesammelten Kommentare als EIN normaler Chat-Turn über die
  // bestehende Send-Pipeline (Queue/GDPR/Streaming inklusive). Kommentar-
  // Bilder werden in state._pendingFiles eingereiht und reiten als normale
  // Chat-Attachments mit (GDPR-Scan, Disk-Ablage unter /tmp/brain-attachments,
  // bei Vision-Modellen zusätzlich multimodal sichtbar); eingebettet wird über
  // eine attachment://-Referenz, die der Server beim Speichern deterministisch
  // durch eine data-URI ersetzt — die Bildbytes fließen NIE durchs Modell.
  function apply() {
    if (!_comments.length) return;
    const name = document.getElementById('artifact-content')?._rawName || 'Artefakt';
    // Sende-Dateinamen eindeutig machen (zwei Kommentare könnten Bilder mit
    // gleichem Namen tragen — auf der Platte würde das zweite das erste
    // überschreiben), BEVOR die Prompt-Zeilen darauf verweisen.
    const used = new Set((state._pendingFiles || []).map(f => f.name));
    for (const [i, c] of _comments.entries()) {
      if (!c.image) continue;
      let n = c.image.name;
      if (used.has(n)) n = `${i + 1}-${n}`;
      used.add(n);
      c.image.sendName = n;
    }
    const lines = _comments.map((c, i) => {
      let line = `${i + 1}. \`${c.selector}\`${c.preview ? ` („${c.preview}…“)` : ''}: ` +
        `${c.text || 'Füge das angehängte Bild an dieser Stelle ein.'}`;
      if (c.image) line += ` [dazu angehängtes Bild: „${c.image.sendName}“]`;
      return line;
    });
    const hasImages = _comments.some(c => c.image);
    let prompt = `Überarbeite das Artefakt „${name}“ per edit_file (kein Neuschreiben). ` +
      `Änderungswünsche, je mit CSS-Selektor des gemeinten Elements:\n` +
      lines.join('\n') +
      `\nSetze die Änderungen im Layout-System der Seite um (bestehendes CSS/Grid/Flex anpassen; ` +
      `keine absoluten Positionen, keine Inline-Style-Overrides).`;
    if (hasImages) {
      prompt += `\nDie genannten Bilder liegen als Datei auf der Platte (Pfade siehe Anhang-Hinweis). ` +
        `Zum Einbetten referenziere ein Bild als <img src="attachment://<dateiname-auf-platte>"> ` +
        `mit passenden Größen-/Layout-Styles — der Server ersetzt die Referenz beim Speichern ` +
        `automatisch durch die eingebettete Bilddatei. Schreibe NIEMALS Base64-Bilddaten selbst.`;
    }
    const input = _composerInputEl();
    if (!input) { showToast('Kein Chat-Eingabefeld gefunden', true); return; }
    for (const c of _comments) {
      if (!c.image) continue;
      state._pendingFiles.push({
        name: c.image.sendName,
        type: c.image.type,
        data: c.image.data,
        encoding: 'base64',
        preview: c.image.preview,
        scan: { state: 'deferred' },
      });
    }
    if (hasImages) { renderFilePreviews(); updateSendButton(); }
    _comments = [];
    _renderPins();
    _renderBar();
    input.value = prompt;
    sendMessage();
  }

  // ── Export-Menü (Phase C) ────────────────────────────────────────────────
  // HTML (bestehender Download) · PDF (Chromium-Render, druckgenau) · DOCX
  // (bearbeitbares Word-Dokument: Inhalt + Struktur, Layout vereinfacht) ·
  // PPTX (Bild-Folien: eine <section data-slide> = eine Folie — pixelgenau,
  // aber bewusst NICHT in PowerPoint editierbar; das Menü sagt das ehrlich).
  // Lebt im DesignCanvas-IIFE, damit kein weiteres Global entsteht.
  function _closeExportMenu() {
    document.querySelector('.design-export-menu')?.remove();
  }

  async function _exportFetch(format) {
    const id = state.activeArtifactId;
    const ver = state.activeArtifactVersion;
    if (!id) return;
    showToast(format.toUpperCase() + ' wird erzeugt …');
    try {
      const url = `${BASE_URL}/v1/artifacts/${id}/export?format=${format}` +
                  (ver ? `&version=${encodeURIComponent(ver)}` : '');
      const r = await fetch(url, { headers: API._headers() });
      if (!r.ok) {
        let msg = `Export fehlgeschlagen (${r.status})`;
        try { msg = (await r.json()).error || msg; } catch (_) {}
        showToast(msg, true);
        return;
      }
      const blob = await r.blob();
      const disp = r.headers.get('Content-Disposition') || '';
      const m = /filename="([^"]+)"/.exec(disp);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = m ? m[1] : `export.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch (e) {
      showToast('Export fehlgeschlagen: ' + (e.message || e), true);
    }
  }

  function exportMenu(ev) {
    ev?.stopPropagation?.();
    if (document.querySelector('.design-export-menu')) { _closeExportMenu(); return; }
    const menu = document.createElement('div');
    menu.className = 'design-export-menu';
    // PDF-Artefakte: einziger Export-Weg ist DOCX (pdf2docx, layout-treu).
    const rawName = document.getElementById('artifact-content')?._rawName || '';
    if (rawName.toLowerCase().endsWith('.pdf')) {
      menu.innerHTML = `
      <div class="design-export-item" data-fmt="docx">
        <strong>Als DOCX exportieren</strong>
        <small>Layout-treu konvertiert — in Word bearbeitbar</small>
      </div>`;
    } else {
    menu.innerHTML = `
      <div class="design-export-item" data-fmt="html">
        <strong>HTML herunterladen</strong>
        <small>Die Datei selbst — selbständig lauffähig</small>
      </div>
      <div class="design-export-item" data-fmt="pdf">
        <strong>Als PDF exportieren</strong>
        <small>Chromium-Render — druckgenau</small>
      </div>
      <div class="design-export-item" data-fmt="docx">
        <strong>Als DOCX exportieren</strong>
        <small>Inhalt als bearbeitbares Word-Dokument — Layout vereinfacht</small>
      </div>
      <div class="design-export-item" data-fmt="pptx">
        <strong>Als PPTX exportieren</strong>
        <small>Eine &lt;section data-slide&gt; = eine Folie (Bild-Folien, nicht editierbar)</small>
      </div>`;
    }
    const r = ev?.target?.closest('button')?.getBoundingClientRect();
    menu.style.left = Math.max(8, (r ? r.right : 300) - 300) + 'px';
    menu.style.top = ((r ? r.top : 100) - 8) + 'px';
    menu.style.transform = 'translateY(-100%)';
    menu.onclick = (e) => {
      const item = e.target.closest('.design-export-item');
      if (!item) return;
      _closeExportMenu();
      if (item.dataset.fmt === 'html') { downloadArtifact(); return; }
      _exportFetch(item.dataset.fmt);
    };
    document.body.appendChild(menu);
    setTimeout(() => document.addEventListener('click', _closeExportMenu, { once: true }), 0);
  }

  return { isActive, resetFor, toggle, render, exportMenu };
})();
