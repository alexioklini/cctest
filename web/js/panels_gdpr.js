// panels_gdpr.js — GDPR/classification modals + PII badge UI. Split from panels.js (Tier F Phase 3). Global <script>, no modules.

/* ─── Cancellable server PII scan (9.200.0) ─────────────────────
 * Detection is server-only now, so the pre-send typed-text scan is a network
 * round-trip (regex + spaCy NER). Show a small progress overlay with a Cancel
 * button while it runs so a slow scan never freezes the composer silently.
 *
 * Resolves with the scan JSON, OR rejects with an Error tagged `_cancelled`
 * when the user cancels (the send flow treats that as "abort send"). Network
 * errors reject normally (the caller fails open). The overlay is always torn
 * down. A short delay before showing the overlay avoids a flash on fast scans. */
function runCancellableGdprScan(text) {
  return new Promise((resolve, reject) => {
    const ctrl = new AbortController();
    let cancelled = false;
    let overlay = null;
    // Only show the overlay if the scan takes longer than ~250ms — most scans
    // are instant and a flashing modal would be noise.
    const showTimer = setTimeout(() => {
      overlay = document.createElement('div');
      overlay.className = 'gdpr-scan-overlay';
      overlay.innerHTML =
        '<div class="gdpr-scan-box" role="dialog" aria-live="polite">' +
          '<div class="gdpr-scan-spinner" aria-hidden="true"></div>' +
          '<div class="gdpr-scan-text">Datenschutz-Prüfung läuft …</div>' +
          '<button type="button" class="gdpr-scan-cancel">Abbrechen</button>' +
        '</div>';
      _injectGdprScanStyles();
      overlay.querySelector('.gdpr-scan-cancel').onclick = () => {
        cancelled = true;
        ctrl.abort();
      };
      // Esc cancels too.
      overlay._onKey = (e) => { if (e.key === 'Escape') { cancelled = true; ctrl.abort(); } };
      document.addEventListener('keydown', overlay._onKey);
      document.body.appendChild(overlay);
    }, 250);

    const teardown = () => {
      clearTimeout(showTimer);
      if (overlay) {
        if (overlay._onKey) document.removeEventListener('keydown', overlay._onKey);
        overlay.remove();
        overlay = null;
      }
    };

    API.scanText(text, 'message', { full: true, signal: ctrl.signal })
      .then(res => { teardown(); resolve(res); })
      .catch(err => {
        teardown();
        if (cancelled || (err && err.name === 'AbortError')) {
          const e = new Error('PII-Prüfung abgebrochen');
          e._cancelled = true;
          reject(e);
        } else {
          reject(err);
        }
      });
  });
}

function _injectGdprScanStyles() {
  if (document.getElementById('gdpr-scan-styles')) return;
  const st = document.createElement('style');
  st.id = 'gdpr-scan-styles';
  st.textContent = `
    .gdpr-scan-overlay { position:fixed; inset:0; z-index:10001;
      background:rgba(0,0,0,.32); display:flex; align-items:center;
      justify-content:center; }
    .gdpr-scan-box { background:var(--bg-100,#fff); border:1px solid var(--border-200,#ddd);
      border-radius:12px; padding:22px 26px; display:flex; flex-direction:column;
      align-items:center; gap:14px; min-width:240px;
      box-shadow:0 8px 32px rgba(0,0,0,.2); }
    .gdpr-scan-spinner { width:28px; height:28px; border-radius:50%;
      border:3px solid var(--border-200,#ddd); border-top-color:var(--accent,#3b82f6);
      animation:gdpr-scan-spin .8s linear infinite; }
    @keyframes gdpr-scan-spin { to { transform:rotate(360deg); } }
    .gdpr-scan-text { font-size:13px; color:var(--text-200,#333); }
    .gdpr-scan-cancel { font-size:12px; padding:6px 16px; border-radius:6px;
      border:1px solid var(--border-300,#ccc); background:var(--bg-200,#f5f5f5);
      color:var(--text-100,#111); cursor:pointer; }
    .gdpr-scan-cancel:hover { background:var(--bg-300,#eaeaea); }`;
  document.head.appendChild(st);
}

/* ─── GDPR / PII detection modal ───────────────────────────── */
// Auto-displayed during an interactive chat when the PII scanner flags personal
// data in the outgoing message or its text attachments. Lists exactly which
// fragments tripped which detector (partially masked), then offers a set of
// ways forward. Resolves to a string verdict:
//   'cancel'           — abort, don't send
//   'local'            — switch to a local model and send unchanged
//   'send'             — send to the selected model anyway (only when not a
//                        hard block, or a local model is already active)
//   'auto-anon'        — (not yet implemented)
//   'manual-anon'      — (not yet implemented)
//   'auto-anon-deanon' — (not yet implemented)
// `localActive` = true when the currently selected model is already local
// (so the data would stay on-prem); controls whether a hard block can proceed.
function gdprActionModal(scan, chat, localActive, classifiedFiles) {
  return new Promise((resolve) => {
    // ─── Classification compose ───
    // `classifiedFiles` is an optional list of pending files whose
    // classification scan came back with a non-trivial effective_action
    // (warn / force_local / block). We surface them as an extra section
    // in the same modal so the user sees PII AND classification in one
    // place, and the button row reflects the strictest combined policy.
    classifiedFiles = Array.isArray(classifiedFiles) ? classifiedFiles : [];
    const _RANK = {public: 0, internal: 1, confidential: 2, strict: 3, unmarked: 1};
    let clsWorstAction = 'ignore';      // ignore | warn | force_local | block
    let clsWorstLevel = null;           // e.g. 'strict'
    let clsWorstLabel = '';
    let clsHasMismatch = false;
    const _ACT_ORDER = {ignore: 0, warn: 1, force_local: 2, block: 3};
    for (const cf of classifiedFiles) {
      const c = cf.scan && cf.scan.classification;
      if (!c) continue;
      const act = c.effective_action || 'ignore';
      if ((_ACT_ORDER[act] || 0) > (_ACT_ORDER[clsWorstAction] || 0)) {
        clsWorstAction = act;
      }
      // Use the server's action_level when available (the higher of
      // marker + heuristic). Falls back to a max() across both signals
      // on older response shapes.
      const heur = (c.content_signals && c.content_signals.heuristic_level) || null;
      const candidates = [c.marker_level, heur].filter(Boolean);
      const lvl = c.action_level
               || (candidates.length
                     ? candidates.reduce((a, b) => (_RANK[a] || 0) >= (_RANK[b] || 0) ? a : b)
                     : (c.final_level || 'unmarked'));
      if ((_RANK[lvl] || 0) > (_RANK[clsWorstLevel || 'public'] || 0)) {
        clsWorstLevel = lvl;
        clsWorstLabel = c.level_label_de || lvl;
      }
      if (c.mismatch) clsHasMismatch = true;
    }
    const clsActive = classifiedFiles.length > 0 && clsWorstAction !== 'ignore';
    // Strict-level "block" is the ARL §1.11 hard veto: even a local model
    // does not get to see the document. Only Cancel survives.
    const clsHardVeto = clsActive && clsWorstLevel === 'strict'
                        && clsWorstAction === 'block';
    // force_local (or non-strict block) from classification: cloud send is
    // forbidden, but a local model is still on the table.
    const clsForcesLocal = clsActive && !clsHardVeto
                           && (clsWorstAction === 'force_local'
                               || clsWorstAction === 'block')
                           && !localActive;

    const isBlock = scan.worstAction === 'block' || clsHardVeto || clsForcesLocal;
    // "Trotzdem senden" — cloud is allowed:
    //   - no hard veto, AND
    //   - PII gate doesn't block (or model is already local), AND
    //   - classification doesn't demand local-only.
    const canSend = !clsHardVeto
                    && (scan.worstAction !== 'block' || localActive)
                    && !clsForcesLocal;
    // "Anonymisieren & senden" — auto-anon-deanon. Only meaningful if
    // there's PII to strip; a classification veto still kills it (we
    // can't anonymise a document's classification away).
    const hasPiiFindings = (scan.findings && scan.findings.length > 0);
    const canAnonymise = hasPiiFindings && !clsHardVeto && !clsForcesLocal;
    // "Lokales Modell verwenden" — always available except under the
    // strict hard veto.
    const canLocal = !clsHardVeto;
    // Inject the PII modal's one-off styles once per page lifetime.
    if (!document.getElementById('pii-modal-styles-v3')) {
      document.getElementById('pii-modal-styles')?.remove();
      document.getElementById('pii-modal-styles-v2')?.remove();
      const style = document.createElement('style');
      style.id = 'pii-modal-styles-v3';
      style.textContent = `
        @keyframes pii-fade-in { from{opacity:0} to{opacity:1} }
        @keyframes pii-pop-in  { from{opacity:0;transform:translateY(8px) scale(.985)} to{opacity:1;transform:translateY(0) scale(1)} }
        .pii-overlay {
          position:fixed; inset:0; z-index:9999;
          display:flex; align-items:center; justify-content:center;
          background:rgba(20,18,16,.52);
          backdrop-filter:blur(4px);
          -webkit-backdrop-filter:blur(4px);
          animation:pii-fade-in .15s ease-out;
          padding:20px;
        }
        .pii-card {
          width:min(720px, 100%);
          max-height:min(82vh, 720px);
          display:flex; flex-direction:column;
          background:var(--bg-000, #faf9f7);
          border-radius:14px;
          box-shadow:0 20px 50px -16px rgba(31,30,29,.32), 0 0 0 1px rgba(31,30,29,.06);
          overflow:hidden;
          animation:pii-pop-in .18s cubic-bezier(.2,.8,.2,1);
        }
        .pii-header {
          display:flex; align-items:flex-start; gap:14px;
          padding:20px 24px 16px;
          border-bottom:1px solid var(--border-100);
        }
        .pii-shield {
          flex:none;
          width:36px; height:36px; border-radius:9px;
          background:#fef3c7; color:#b45309;
          display:flex; align-items:center; justify-content:center;
          margin-top:1px;
        }
        .pii-header.is-block .pii-shield { background:#fee2e2; color:#b91c1c; }
        .pii-header-text { flex:1; min-width:0; }
        .pii-title { font-size:15.5px; font-weight:600; letter-spacing:-.005em; line-height:1.3; margin:0; color:var(--text-000); }
        .pii-subtitle { font-size:12.5px; margin:3px 0 0; color:var(--text-300); line-height:1.45; }
        .pii-stat {
          flex:none;
          font-size:11px; font-weight:600;
          padding:4px 10px; border-radius:999px;
          background:#fef3c7; color:#92400e;
          letter-spacing:.01em;
          white-space:nowrap;
          margin-top:2px;
        }
        .pii-header.is-block .pii-stat { background:#fee2e2; color:#991b1b; }
        .pii-body { flex:1 1 auto; overflow-y:auto; padding:14px 24px 16px; }
        .pii-source-card {
          padding:10px 12px;
          border:1px solid var(--border-100);
          border-radius:10px;
          background:var(--bg-050, var(--bg-100));
          margin-top:8px;
        }
        .pii-source-card:first-child { margin-top:0; }
        .pii-source-head {
          display:flex; align-items:center; justify-content:space-between;
          gap:10px; margin-bottom:6px;
        }
        .pii-source-name { font-size:12px; font-weight:600; color:var(--text-100); }
        .pii-source-count {
          font-size:10.5px; color:var(--text-300);
          background:var(--bg-200); padding:1px 7px; border-radius:999px;
        }
        .pii-finding {
          display:flex; align-items:baseline; gap:8px;
          padding:5px 0; border-top:1px solid var(--border-050, var(--border-100));
          font-size:12px; line-height:1.4;
        }
        .pii-finding:first-of-type { border-top:none; }
        .pii-finding-sev {
          flex:none; width:6px; height:6px; border-radius:50%; margin-top:5px;
          background:#d97706;
        }
        .pii-finding-sev.is-block { background:#dc2626; }
        .pii-finding-sev.is-ignore { background:var(--text-400); }
        .pii-finding-label { flex:none; font-weight:500; color:var(--text-100); min-width:130px; }
        .pii-finding-cat { flex:none; font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--text-400); }
        .pii-finding-val {
          flex:1 1 auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
          color:var(--text-300); word-break:break-all; font-size:11.5px;
        }
        .pii-finding-loc { flex:none; font-size:10px; color:var(--text-400); white-space:nowrap; }
        .pii-finding-conf {
          flex:none; font-size:10.5px; font-variant-numeric:tabular-nums;
          color:var(--text-400); white-space:nowrap; padding:1px 6px;
          border:1px solid var(--border-100); border-radius:10px;
        }
        .pii-finding-fp {
          flex:none; font-size:11px; color:var(--text-300); white-space:nowrap;
          display:inline-flex; align-items:center; gap:4px; cursor:pointer;
        }
        .pii-finding-fp input { margin:0; cursor:pointer; }
        .pii-finding-row.pii-is-fp { opacity:.5; text-decoration:line-through; }
        .pii-group { margin-bottom:12px; }
        .pii-group-title {
          font-size:12px; font-weight:600; color:var(--text-100);
          margin:0 0 6px 2px; display:flex; align-items:baseline; gap:8px;
        }
        .pii-group-sub { font-size:10.5px; font-weight:400; color:var(--text-400); }
        .pii-unit-caret { flex:none; font-size:11px; color:var(--text-400); }
        .pii-unit-bulk {
          flex:none; font-size:10.5px; color:var(--text-300); white-space:nowrap;
          display:inline-flex; align-items:center; gap:4px; cursor:pointer; margin-left:8px;
        }
        .pii-unit-bulk input { margin:0; cursor:pointer; }
        .pii-source-card.pii-collapsed .pii-unit-rows { display:none; }
        .pii-finding-seen { opacity:.78; }
        .pii-finding-fixed {
          flex:none; font-size:10.5px; color:var(--text-400); white-space:nowrap;
          font-style:italic;
        }
        .pii-footer {
          flex:none;
          display:flex; flex-direction:column; gap:10px;
          padding:14px 24px 16px;
          border-top:1px solid var(--border-100);
          background:var(--bg-050, var(--bg-100));
        }
        .pii-actions {
          display:flex; align-items:center;
          gap:8px;
          flex-wrap:wrap;
        }
        .pii-actions-spacer { flex:1; }
        .pii-ask-after {
          display:flex; align-items:center; gap:7px;
          font-size:12px; color:var(--text-200); cursor:pointer;
          user-select:none;
        }
        .pii-ask-after input { cursor:pointer; }
        .pii-suppress-note {
          margin:0; font-size:11px; color:var(--text-300);
          line-height:1.4;
        }
        .pii-btn {
          padding:7px 13px; border-radius:8px;
          font-size:12.5px; font-weight:500;
          border:1px solid transparent;
          cursor:pointer;
          transition:background .12s, border-color .12s, box-shadow .12s;
          white-space:nowrap;
        }
        .pii-btn:active { transform:translateY(1px); }
        .pii-btn[disabled] { opacity:.45; cursor:not-allowed; }
        .pii-btn-text {
          background:transparent; border-color:transparent;
          color:var(--text-300); padding:7px 8px;
        }
        .pii-btn-text:hover:not([disabled]) { color:var(--text-100); background:var(--bg-200); }
        .pii-btn-secondary {
          background:var(--bg-000);
          border-color:var(--border-200);
          color:var(--text-100);
        }
        .pii-btn-secondary:hover:not([disabled]) { background:var(--bg-200); }
        .pii-btn-warn {
          background:transparent;
          border-color:#fbbf24;
          color:#92400e;
        }
        .pii-btn-warn:hover:not([disabled]) { background:#fef3c7; }
        .pii-btn-primary {
          background:#047857; color:#fff;
          border-color:#047857;
        }
        .pii-btn-primary:hover:not([disabled]) { background:#065f46; border-color:#065f46; }
      `;
      document.head.appendChild(style);
    }

    // Mask a matched value: keep a couple of edge chars, dot out the middle.
    const mask = (m) => {
      if (!m) return '';
      if (m.length <= 6) return m[0] + '•'.repeat(Math.max(0, m.length - 1));
      return m.slice(0, 2) + '•'.repeat(m.length - 4) + m.slice(-2);
    };
    const sevClass = (a) => a === 'block' ? ' is-block' : (a === 'ignore' ? ' is-ignore' : '');

    // Build a per-source breakdown. Entries that came from the server-side
    // aggregated `groups` carry `count` + `samples`; render one row per
    // rule_id with the total + up to 3 sample previews. Plain client-side
    // findings (text or legacy file scan) still render per-fragment.
    // ── Seen / New × Nachrichtentext / Anhang structure (9.197.0) ──
    // All per-finding (server full-mode) findings carry _seen + _source +
    // confidence/band/disposition. We group them into two top-level sections —
    // BEREITS GESEHEN (fixed, not ratable) and NEU (ratable) — each split by
    // source unit (Nachrichtentext = 'message', Anhang = 'file:<name>'). A unit
    // with >1 finding collapses + offers a bulk FP toggle for the whole unit.
    const bandLabel = b => b === 'high' ? 'hoch' : (b === 'mid' ? 'mittel' : 'niedrig');
    const allPerFinding = [];
    for (const findings of Object.values(scan.bySource)) {
      for (const f of findings) {
        if (f && (f.confidence != null || f.disposition != null) && typeof f.count !== 'number') {
          allPerFinding.push(f);
        }
      }
    }
    const unitLabel = src => (src === 'message' || src === 'text')
      ? 'Nachrichtentext' : (src || '').replace(/^file:/, 'Anhang · ') || 'Anhang';
    // original→fake from the chat's anonymisation spans, to show the pseudonym
    // on already-anonymised (seen) findings.
    const fakeMap = (typeof _gdprOriginalToFakeMap === 'function')
      ? _gdprOriginalToFakeMap(chat) : {};

    function renderFindingRow(f, ratable) {
      const conf = (f.confidence != null) ? f.confidence : 0;
      const checked = (!ratable && f._priorFp) ? ' checked' : '';
      const fake = (!ratable && !f._priorFp) ? fakeMap[f.value] : null;
      const fpCell = ratable
        ? ('<label class="pii-finding-fp" title="Als Falschtreffer markieren — bleibt im Klartext, wird für diesen Chat gemerkt">' +
             '<input type="checkbox" class="pii-fp-check"> falsch</label>')
        // seen → fixed: show the prior decision, no editable control
        : ('<span class="pii-finding-fp pii-finding-fixed" title="Entscheidung vom ersten Sehen — nicht mehr änderbar">' +
             (f._priorFp ? 'als falsch markiert' : (fake ? 'anonymisiert' : 'bestätigt')) + '</span>');
      // Value cell — show the pseudonym for anonymised seen findings.
      const valHtml = fake
        ? esc(f.value || '') + ' <span style="color:var(--text-400)">→</span> <span style="color:#047857">' + esc(fake) + '</span>'
        : esc(f.value || '');
      return '<div class="pii-finding pii-finding-row' + (ratable ? '' : ' pii-finding-seen') + '" ' +
          'data-pii-rule="' + esc(f.rule_id || '') + '" ' +
          'data-pii-value="' + esc(f.value || '') + '" ' +
          'data-pii-conf="' + esc(String(conf)) + '" ' +
          'data-pii-disp="' + esc(f.disposition || '') + '" ' +
          'data-pii-source="' + esc(f._source || '') + '" ' +
          'data-pii-seen="' + (ratable ? '0' : '1') + '"' + checked + '>' +
        '<span class="pii-finding-sev' + sevClass(f.action || 'warn') + '" title="' + esc(f.action || 'warn') + '"></span>' +
        '<span class="pii-finding-label">' + esc(f.label || f.rule_id) + '</span>' +
        '<span class="pii-finding-val" style="font-family:var(--font-mono,monospace)">' + valHtml + '</span>' +
        '<span class="pii-finding-conf" title="Konfidenz ' + conf.toFixed(2) + '">' + conf.toFixed(2) + ' · ' + bandLabel(f.band) + '</span>' +
        fpCell +
      '</div>';
    }

    function renderUnit(src, items, ratable, gi) {
      const collapsible = items.length > 1;
      const rows = items.map(f => renderFindingRow(f, ratable)).join('');
      const bulk = (ratable && collapsible)
        ? ('<label class="pii-unit-bulk" title="Alle Treffer dieser Einheit als Falschtreffer markieren">' +
             '<input type="checkbox" class="pii-bulk-check"> alle falsch</label>')
        : '';
      const caret = collapsible
        ? '<span class="pii-unit-caret" style="cursor:pointer">&#9662;</span>' : '';
      const head =
        '<div class="pii-source-head"' + (collapsible ? ' style="cursor:pointer"' : '') + '>' +
          caret +
          '<div class="pii-source-name">' + esc(unitLabel(src)) + '</div>' +
          '<div class="pii-source-count">' + items.length + ' Treffer</div>' +
          bulk +
        '</div>';
      return '<div class="pii-source-card" data-pii-unit="' + esc(gi) + '">' + head +
        '<div class="pii-unit-rows">' + rows + '</div></div>';
    }

    function renderGroup(title, findings, ratable, keyPrefix) {
      if (!findings.length) return '';
      // split by source unit, message first then attachments
      const byUnit = new Map();
      for (const f of findings) {
        const u = f._source || 'message';
        if (!byUnit.has(u)) byUnit.set(u, []);
        byUnit.get(u).push(f);
      }
      const order = [...byUnit.keys()].sort((a, b) =>
        (a === 'message' ? -1 : 0) - (b === 'message' ? -1 : 0));
      const units = order.map((u, i) => renderUnit(u, byUnit.get(u), ratable, keyPrefix + '-' + i)).join('');
      return '<div class="pii-group">' +
        '<div class="pii-group-title">' + esc(title) +
          ' <span class="pii-group-sub">' + (ratable ? 'neu — bitte prüfen' : 'bereits gesehen — fixiert') + '</span>' +
        '</div>' + units + '</div>';
    }

    const newF = allPerFinding.filter(f => !f._seen);
    const seenF = allPerFinding.filter(f => f._seen);
    const sections = [];
    const newHtml = renderGroup('Neue Treffer', newF, true, 'new');
    const seenHtml = renderGroup('Bereits gesehen', seenF, false, 'seen');
    if (newHtml) sections.push(newHtml);
    if (seenHtml) sections.push(seenHtml);

    // ─── Append a classification section, when active ───
    // Renders one card per classified file with the German level label
    // + marker level + resulting action, mirroring the per-source-card
    // visual style.
    if (clsActive) {
      // Two independent signals, both always surfaced:
      //   - "Aktuelle analysierte Klassifikation" = heuristic_level
      //   - "Im File klassifiziert als" = marker_level (or
      //     "Nicht klassifiziert" when none).
      const LEVEL_DE = {
        public: 'Öffentlich',
        internal: 'Intern',
        confidential: 'Vertraulich',
        strict: 'Streng vertraulich',
      };
      const ACTION_DE = {
        ignore: 'Keine Einschränkung',
        warn: 'Warnung — Senden möglich',
        force_local: 'Nur lokales Modell erlaubt',
        block: 'Senden nicht möglich',
      };
      const fileRows = classifiedFiles.map(cf => {
        const c = cf.scan.classification;
        const markerLvl = c.marker_level || null;
        const heur = (c.content_signals && c.content_signals.heuristic_level) || 'public';
        const analyzedLabel = LEVEL_DE[heur] || heur;
        const markerLabel = markerLvl
          ? (LEVEL_DE[markerLvl] || markerLvl)
          : 'Nicht klassifiziert';
        const actDE = ACTION_DE[c.effective_action] || c.effective_action || '';
        return '<div class="pii-finding">' +
          '<span class="pii-finding-sev' +
            (c.effective_action === 'block' ? ' is-block' : '') +
          '"></span>' +
          '<span class="pii-finding-label">' + esc(cf.name) + '</span>' +
          '<span class="pii-finding-cat">' + esc(analyzedLabel) + '</span>' +
          '<span class="pii-finding-val">' +
            'Im File: ' + esc(markerLabel) + ' · Folge: ' + esc(actDE) +
          '</span>' +
        '</div>';
      }).join('');
      sections.push(
        '<div class="pii-source-card">' +
          '<div class="pii-source-head">' +
            '<div class="pii-source-name">Dokumentenklassifizierung</div>' +
            '<div class="pii-source-count">' + classifiedFiles.length + ' Anhang' +
              (classifiedFiles.length === 1 ? '' : ' / Anhänge') + '</div>' +
          '</div>' +
          fileRows +
        '</div>'
      );
    }

    const total = scan.findings.length;
    const blockCls = isBlock ? ' is-block' : '';
    // Title + subtitle now reflect PII + classification jointly.
    // Priority order: strict-hard-veto > classification force_local >
    // PII hard block > PII warn.
    let title, subtitle;
    if (clsHardVeto) {
      title = 'Streng vertraulicher Inhalt erkannt — Versand blockiert';
      subtitle = 'Streng vertrauliche Dokumente dürfen das System nicht an ein Modell verlassen. Bitte den Turn abbrechen und den Anhang entfernen.';
    } else if (clsForcesLocal) {
      title = 'Klassifizierter Inhalt erkannt';
      subtitle = 'Klassifizierter Anhang' + (hasPiiFindings ? ' + personenbezogene Daten' : '') +
        ' — Versand nur an ein lokales Modell möglich.';
    } else if (scan.worstAction === 'block') {
      title = 'Hochsensible personenbezogene Daten erkannt';
      // Subtitle reflects the ACTUALLY selected model — localActive, not canSend
      // (canSend is also true for a local model, but here we describe where the
      // data would go). With a cloud model selected, say so honestly.
      subtitle = localActive
        ? 'Hochsensible Daten erkannt — das gewählte Modell ist lokal, die Daten verlassen das System nicht.'
        : 'Hochsensible Daten erkannt — das gewählte Modell ist ein Cloud-Modell. Bitte je Treffer prüfen und anonymisieren, ein lokales Modell wählen, oder bewusst trotzdem senden.';
    } else if (clsActive && !hasPiiFindings) {
      // Find the worst file's marker_level for an honest subtitle —
      // never call something "Unmarkiert" classified.
      let _hasMarker = false;
      for (const cf of classifiedFiles) {
        if (cf.scan && cf.scan.classification && cf.scan.classification.marker_level) {
          _hasMarker = true; break;
        }
      }
      if (_hasMarker) {
        title = 'Klassifizierter Inhalt erkannt';
        subtitle = 'Anhang mit Klassifikation erkannt — bitte vor dem Senden prüfen.';
      } else {
        title = 'Nicht klassifizierter Anhang';
        subtitle = 'Der Anhang hat keine Klassifikation, der Inhalt deutet aber auf sensible Daten hin — bitte vor dem Senden prüfen.';
      }
    } else if (clsActive) {
      title = 'Personenbezogene Daten und klassifizierter Inhalt erkannt';
      subtitle = 'Bitte vor dem Senden prüfen — die Auswahl gilt für beide Befunde.';
    } else {
      title = 'Personenbezogene Daten in der Nachricht erkannt';
      subtitle = newF.length
        ? 'Bitte die neuen Treffer prüfen — als Falschtreffer markierte Werte bleiben im Klartext.'
        : 'Bereits geprüfte Treffer — keine neuen personenbezogenen Daten.';
    }
    // Count from the new seen/new structure (no maskdance, no double-count).
    const totalFindings = allPerFinding.length;
    const statBadge = (totalFindings
        ? (newF.length ? newF.length + ' neu' : '') +
          (newF.length && seenF.length ? ' · ' : '') +
          (seenF.length ? seenF.length + ' gesehen' : '')
        : '') +
      (clsActive
        ? (totalFindings ? ' · ' : '') + classifiedFiles.length + ' klassifiziert'
        : '');

    // Shield SVG (same vocabulary as the inline composer badge)
    const shieldSvg = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/><path d="M12 8v4"/><circle cx="12" cy="16" r="0.6" fill="currentColor"/></svg>';

    // Footer action hierarchy:
    //   left:  Abbrechen (text-button) + "Trotzdem senden" (warn outline, only if allowed)
    //   right: Lokales Modell (secondary, only if allowed) + Anonymisieren & senden (primary, only if PII)
    // The four canonical verdicts are: continue / local / anonymise / cancel.
    // The strict hard veto reduces this to {cancel} only.
    const sendBtn = canSend
      ? '<button class="pii-btn pii-btn-warn" id="pii-send-btn">Trotzdem senden</button>'
      : '';
    const localBtn = canLocal
      ? '<button class="pii-btn pii-btn-secondary" id="pii-local-btn">Lokales Modell verwenden</button>'
      : '';
    const anonBtn = canAnonymise
      ? '<button class="pii-btn pii-btn-primary" id="pii-anon-btn">Anonymisieren &amp; senden</button>'
      : '';
    const modalId = 'pii-warning-modal';
    document.getElementById(modalId)?.remove();
    const html =
      '<div class="pii-overlay" id="' + modalId + '">' +
        '<div class="pii-card" role="dialog" aria-modal="true" aria-labelledby="pii-title">' +
          '<div class="pii-header' + blockCls + '">' +
            '<div class="pii-shield" aria-hidden="true">' + shieldSvg + '</div>' +
            '<div class="pii-header-text">' +
              '<h2 id="pii-title" class="pii-title">' + esc(title) + '</h2>' +
              '<p class="pii-subtitle">' + esc(subtitle) + '</p>' +
            '</div>' +
            '<div class="pii-stat">' + esc(statBadge) + '</div>' +
          '</div>' +
          '<div class="pii-body">' + sections.join('') + '</div>' +
          '<div class="pii-footer">' +
            '<div class="pii-actions">' +
              '<button class="pii-btn pii-btn-text" id="pii-cancel-btn">Abbrechen</button>' +
              sendBtn +
              '<div class="pii-actions-spacer"></div>' +
              localBtn +
              anonBtn +
            '</div>' +
            // Opt-in: ask the user afterwards whether the chosen method worked.
            // Off by default — only when ticked does the post-turn GDPR feedback
            // modal (gdprFeedbackModal) fire on subsequent turns.
            '<label class="pii-ask-after"><input type="checkbox" id="pii-ask-after"> ' +
            'Frag mich nachher wies gelaufen ist</label>' +
            '<p class="pii-suppress-note">Die Auswahl gilt für den Rest dieses Chats. ' +
            'Über das Schild-Symbol unter dem Eingabefeld lässt sich die Wahl jederzeit zurücksetzen.</p>' +
          '</div>' +
        '</div>' +
      '</div>';
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const overlay = wrap.firstElementChild;
    document.body.appendChild(overlay);
    const cleanup = (verdict) => {
      document.removeEventListener('keydown', onKey);
      // Capture the "ask me afterwards" opt-in before tearing down. Only
      // meaningful when the user actually proceeds (not on cancel).
      const askAfter = !!document.getElementById('pii-ask-after')?.checked
                       && verdict !== 'cancel';
      // Collect the per-finding decisions (value, rule, confidence, disposition,
      // and whether the user marked it a false positive) so the caller can
      // persist the analysis + decision and honour FP values for the rest of
      // the chat. Only the server-finding rows carry these datasets.
      // Collect decisions ONLY for NEW (ratable) findings — seen rows are
      // fixed and already persisted. data-pii-seen="0" marks a ratable row.
      const decisions = [];
      for (const row of overlay.querySelectorAll('.pii-finding-row[data-pii-seen="0"]')) {
        const fp = row.querySelector('.pii-fp-check');
        decisions.push({
          rule_id: row.dataset.piiRule || '',
          value: row.dataset.piiValue || '',
          confidence: parseFloat(row.dataset.piiConf || '0') || 0,
          disposition: row.dataset.piiDisp || '',
          source: row.dataset.piiSource || '',
          false_positive: !!(fp && fp.checked),
        });
      }
      overlay.remove();
      resolve({ verdict, askAfter, decisions });
    };
    const onKey = (e) => { if (e.key === 'Escape') cleanup('cancel'); };
    document.addEventListener('keydown', onKey);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup('cancel'); });
    // FP checkbox: strike through the row so the user sees what they excluded.
    for (const cb of overlay.querySelectorAll('.pii-fp-check')) {
      cb.addEventListener('change', (e) => {
        e.target.closest('.pii-finding-row')?.classList.toggle('pii-is-fp', e.target.checked);
      });
    }
    // Collapse/expand a multi-finding unit when its header is clicked (but not
    // when clicking the bulk checkbox/label inside the header).
    for (const head of overlay.querySelectorAll('.pii-source-card .pii-source-head')) {
      if (!head.querySelector('.pii-unit-caret')) continue;
      head.addEventListener('click', (e) => {
        if (e.target.closest('.pii-unit-bulk')) return;
        const card = head.closest('.pii-source-card');
        const collapsed = card.classList.toggle('pii-collapsed');
        const caret = head.querySelector('.pii-unit-caret');
        if (caret) caret.innerHTML = collapsed ? '&#9656;' : '&#9662;';
      });
    }
    // Bulk FP: tick/untick every FP checkbox in the unit.
    for (const bulk of overlay.querySelectorAll('.pii-bulk-check')) {
      bulk.addEventListener('change', (e) => {
        const card = e.target.closest('.pii-source-card');
        for (const cb of card.querySelectorAll('.pii-fp-check')) {
          cb.checked = e.target.checked;
          cb.closest('.pii-finding-row')?.classList.toggle('pii-is-fp', e.target.checked);
        }
      });
    }
    document.getElementById('pii-cancel-btn').onclick = () => cleanup('cancel');
    document.getElementById('pii-local-btn')?.addEventListener('click', () => cleanup('local'));
    document.getElementById('pii-anon-btn')?.addEventListener('click', () => cleanup('anonymise'));
    document.getElementById('pii-send-btn')?.addEventListener('click', () => cleanup('send'));
    // Default focus: anonymise when available, else local, else cancel.
    // Strict hard veto collapses to cancel-only.
    setTimeout(() => {
      const pref = document.getElementById('pii-anon-btn')
                || document.getElementById('pii-local-btn')
                || document.getElementById('pii-cancel-btn');
      pref?.focus();
    }, 50);
  });
}

/** Modal shown when the server-side anonymisation step fails. The user
 *  must pick a recovery action — there is intentionally no "send to cloud
 *  anyway" path. Returns 'local_model' or 'cancel'.
 *
 *  Reuses the GDPR modal's stylesheet (`pii-modal-styles-v2`) so the
 *  recovery dialog matches the original modal visually. */
function gdprRecoveryModal(detail, chat) {
  return new Promise((resolve) => {
    const errMsg = (detail && detail.error) ? String(detail.error).slice(0, 400) : 'Unbekannter Fehler';
    const sources = (detail && Array.isArray(detail.sources)) ? detail.sources : [];
    const sourceList = sources.length
      ? '<ul style="margin:6px 0 0 18px; padding:0; font-size:12px; line-height:1.6;">' +
        sources.map(s => '<li>' + esc(s) + '</li>').join('') + '</ul>'
      : '';
    const shieldSvg = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>';
    const modalId = 'pii-recovery-modal';
    document.getElementById(modalId)?.remove();
    const html =
      '<div class="pii-overlay" id="' + modalId + '">' +
        '<div class="pii-card" role="dialog" aria-modal="true" aria-labelledby="pii-recovery-title">' +
          '<div class="pii-header is-block">' +
            '<div class="pii-shield" aria-hidden="true">' + shieldSvg + '</div>' +
            '<div class="pii-header-text">' +
              '<h2 id="pii-recovery-title" class="pii-title">Anonymisierung fehlgeschlagen</h2>' +
              '<p class="pii-subtitle">Der Originalinhalt wurde NICHT an die Cloud gesendet. Bitte Vorgehen wählen.</p>' +
            '</div>' +
          '</div>' +
          '<div class="pii-body">' +
            '<div class="pii-source-card">' +
              '<div class="pii-source-name">Fehler</div>' +
              '<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--text-200);margin-top:6px;word-break:break-word;">' +
                esc(errMsg) + '</div>' +
              (sources.length
                ? '<div style="font-size:12px;color:var(--text-300);margin-top:10px;">' +
                  'Betroffene Quelle' + (sources.length === 1 ? '' : 'n') + ':' + sourceList +
                  '</div>'
                : '') +
            '</div>' +
          '</div>' +
          '<div class="pii-footer">' +
            '<div class="pii-actions">' +
              '<button class="pii-btn pii-btn-text" id="pii-rec-cancel">Turn abbrechen</button>' +
              '<div class="pii-actions-spacer"></div>' +
              '<button class="pii-btn pii-btn-primary" id="pii-rec-local">Lokales Modell verwenden</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const overlay = wrap.firstElementChild;
    document.body.appendChild(overlay);
    const cleanup = (choice) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(choice);
    };
    const onKey = (e) => { if (e.key === 'Escape') cleanup('cancel'); };
    document.addEventListener('keydown', onKey);
    // Click-outside intentionally NOT mapped to cancel — recovery is too
    // important to dismiss accidentally. The user must pick.
    document.getElementById('pii-rec-cancel').onclick = () => cleanup('cancel');
    document.getElementById('pii-rec-local').onclick = () => cleanup('local_model');
    setTimeout(() => document.getElementById('pii-rec-local')?.focus(), 50);
  });
}

/** Post-turn GDPR feedback modal. Fires after a turn that took a GDPR action
 *  (anonymise / local swap) when the session opted in via "Frag mich nachher".
 *  Asks the user whether it worked and lets them retry the SAME turn with a
 *  different method or abort. The "Frag mich weiter wies gelaufen ist" checkbox
 *  (default checked) keeps the opt-in alive — unchecking it stops future
 *  prompts (the chosen method is reused on subsequent turns either way).
 *
 *  `gdpr` is the turn's metadata.gdpr (mode + counts). Resolves with
 *  { action: 'redo'|'dismiss', mode?, keepAsking:bool }. 'redo' carries the
 *  chosen mode ∈ {anonymise, local_model, continue}; 'dismiss' = keep result.
 *
 *  Reuses the gdprActionModal stylesheet (`pii-modal-styles-v3`). */
function gdprFeedbackModal(gdpr) {
  return new Promise((resolve) => {
    gdpr = gdpr || {};
    // Inject the shared modal styles if no GDPR modal ran yet this page life
    // (sticky-pref turns skip the pre-send modal, so they may be first).
    if (!document.getElementById('pii-modal-styles-v3')) {
      const st = document.createElement('style');
      st.id = 'pii-modal-styles-v3';
      st.textContent =
        '@keyframes pii-fade-in{from{opacity:0}to{opacity:1}}' +
        '@keyframes pii-pop-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}' +
        '.pii-overlay{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(20,18,16,.52);backdrop-filter:blur(4px);padding:20px;animation:pii-fade-in .15s ease-out}' +
        '.pii-card{width:min(560px,100%);background:var(--bg-000,#faf9f7);border-radius:14px;box-shadow:0 20px 50px -16px rgba(31,30,29,.32);overflow:hidden;animation:pii-pop-in .18s ease-out}' +
        '.pii-header{display:flex;gap:14px;padding:18px 22px 14px;border-bottom:1px solid var(--border-100)}' +
        '.pii-shield{flex:none;width:36px;height:36px;border-radius:9px;background:#dcfce7;color:#166534;display:flex;align-items:center;justify-content:center}' +
        '.pii-title{font-size:15px;font-weight:600;margin:0;color:var(--text-000)}' +
        '.pii-subtitle{font-size:12.5px;margin:4px 0 0;color:var(--text-300);line-height:1.45}' +
        '.pii-body{padding:14px 22px 16px}' +
        '.pii-footer{padding:14px 22px 16px;border-top:1px solid var(--border-100);display:flex;flex-direction:column;gap:10px}' +
        '.pii-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}' +
        '.pii-actions-spacer{flex:1}' +
        '.pii-btn{padding:7px 13px;font-size:12.5px;font-weight:500;border-radius:8px;cursor:pointer;font-family:inherit;border:1px solid transparent}' +
        '.pii-btn-text{background:transparent;color:var(--text-200);border:1px solid var(--border-200)}' +
        '.pii-btn-secondary{background:var(--bg-200);color:var(--text-100);border:1px solid var(--border-200)}' +
        '.pii-btn-primary{background:#0d6efd;color:#fff}' +
        '.pii-ask-after{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--text-200);cursor:pointer;user-select:none}';
      document.head.appendChild(st);
    }
    // Honest one-line summary of what happened this turn.
    let summary;
    if (gdpr.mode === 'anonymise') {
      const n = Number(gdpr.tokens_minted || gdpr.findings || 0);
      const r = Number(gdpr.restored || 0);
      summary = `${n} personenbezogene${n === 1 ? 's Datum' : ' Daten'} anonymisiert`
        + (r ? `, ${r} in der Antwort wiederhergestellt.` : '.');
    } else if (gdpr.mode === 'local_model') {
      summary = `Die Anfrage wurde lokal beantwortet${gdpr.model ? ` (${esc(gdpr.model)})` : ''} — die Daten verließen das Gerät nicht.`;
    } else if (gdpr.mode === 'anonymise_failed_local') {
      summary = `Die Anonymisierung schlug fehl, daher wurde lokal beantwortet${gdpr.model ? ` (${esc(gdpr.model)})` : ''}.`;
    } else {
      summary = 'Für diese Anfrage wurde eine Datenschutz-Aktion angewendet.';
    }
    // Offer the two methods NOT just used as retry options.
    const usedMode = (gdpr.mode === 'anonymise_failed_local') ? 'local_model' : gdpr.mode;
    const MODE_LABELS = { anonymise: 'Anonymisieren', local_model: 'Lokales Modell', continue: 'Unverändert senden' };
    const altBtns = ['anonymise', 'local_model', 'continue']
      .filter(m => m !== usedMode)
      .map(m => `<button class="pii-btn pii-btn-secondary" data-redo-mode="${m}">${MODE_LABELS[m]}</button>`)
      .join('');
    const shieldSvg = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/><path d="M9 12l2 2 4-4"/></svg>';
    const modalId = 'pii-feedback-modal';
    document.getElementById(modalId)?.remove();
    const html =
      '<div class="pii-overlay" id="' + modalId + '">' +
        '<div class="pii-card" role="dialog" aria-modal="true" aria-labelledby="pii-fb-title">' +
          '<div class="pii-header">' +
            '<div class="pii-shield" aria-hidden="true">' + shieldSvg + '</div>' +
            '<div class="pii-header-text">' +
              '<h2 id="pii-fb-title" class="pii-title">Hat es gepasst?</h2>' +
              '<p class="pii-subtitle">' + summary + '</p>' +
            '</div>' +
          '</div>' +
          '<div class="pii-body">' +
            '<p style="margin:0;font-size:12.5px;color:var(--text-300);line-height:1.5;">' +
            'Wenn die gewählte Methode nicht gepasst hat, kannst du dieselbe Anfrage ' +
            'mit einer anderen Methode erneut senden. Der vorherige Versuch wird dabei verworfen.' +
            '</p>' +
          '</div>' +
          '<div class="pii-footer">' +
            '<div class="pii-actions">' +
              '<button class="pii-btn pii-btn-text" id="pii-fb-dismiss">Passt so</button>' +
              '<div class="pii-actions-spacer"></div>' +
              altBtns +
            '</div>' +
            '<label class="pii-ask-after"><input type="checkbox" id="pii-fb-keep" checked> ' +
            'Frag mich weiter wies gelaufen ist</label>' +
          '</div>' +
        '</div>' +
      '</div>';
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const overlay = wrap.firstElementChild;
    document.body.appendChild(overlay);
    const cleanup = (action, mode) => {
      const keepAsking = !!document.getElementById('pii-fb-keep')?.checked;
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve({ action, mode: mode || null, keepAsking });
    };
    const onKey = (e) => { if (e.key === 'Escape') cleanup('dismiss'); };
    document.addEventListener('keydown', onKey);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup('dismiss'); });
    document.getElementById('pii-fb-dismiss').onclick = () => cleanup('dismiss');
    overlay.querySelectorAll('[data-redo-mode]').forEach(btn => {
      btn.onclick = () => cleanup('redo', btn.getAttribute('data-redo-mode'));
    });
    setTimeout(() => document.getElementById('pii-fb-dismiss')?.focus(), 50);
  });
}

// ─── Classification modal (Phase B) ───
// Surfaces when chat.js detects classified attachments + non-local model
// before send. Returns 'cancel' | 'local' (no anonymise path — strips
// PII, not classification markers).
function classificationActionModal(classifiedFiles, chat) {
  return new Promise((resolve) => {
    // Find the worst-level file to drive the modal tone
    const RANK = {public: 0, internal: 1, confidential: 2, strict: 3, unmarked: 1};
    let worstFile = classifiedFiles[0];
    let worstRank = -1;
    for (const f of classifiedFiles) {
      const r = RANK[(f.scan?.classification?.final_level) || 'unmarked'] || 0;
      if (r > worstRank) { worstRank = r; worstFile = f; }
    }
    const worst = worstFile.scan.classification;
    const isStrict = worst.final_level === 'strict' && worst.effective_action === 'block';
    const subtitle = isStrict
      ? 'Streng vertrauliche Inhalte dürfen ohne Vorstands­zustimmung das System nicht über ein Cloud-Modell verlassen. Bitte den Turn abbrechen.'
      : `Klassifizierter Inhalt erkannt (${worst.level_label_de || worst.final_level}). Auf ein lokales Modell wechseln, um fortzufahren — oder den Turn abbrechen.`;
    // Reuse the existing pii-modal stylesheet (gdprActionModal injects it)
    const ensureStyles = () => {
      if (document.getElementById('pii-modal-styles-v3')) return;
      // Trigger style injection by calling gdprActionModal infrastructure
      const tmp = document.createElement('style');
      tmp.id = 'pii-modal-styles-v3';
      tmp.textContent = `
        @keyframes pii-fade-in { from{opacity:0} to{opacity:1} }
        @keyframes pii-pop-in  { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
        .pii-overlay { position:fixed; inset:0; z-index:9999; display:flex; align-items:center; justify-content:center; background:rgba(20,18,16,.52); backdrop-filter:blur(4px); padding:20px; }
        .pii-card { width:min(560px,100%); background:var(--bg-000,#faf9f7); border-radius:14px; box-shadow:0 20px 50px -16px rgba(31,30,29,.32); overflow:hidden; animation:pii-pop-in .18s ease-out; }
        .pii-header { padding:18px 22px 14px; border-bottom:1px solid var(--border-100); }
        .pii-title { font-size:15px; font-weight:600; margin:0; color:var(--text-000); }
        .pii-subtitle { font-size:12.5px; margin:4px 0 0; color:var(--text-300); }
        .pii-body { padding:14px 22px 16px; }
        .pii-source-card { padding:10px 12px; border:1px solid var(--border-100); border-radius:8px; margin-top:6px; font-size:12.5px; }
        .pii-footer { padding:12px 18px; border-top:1px solid var(--border-100); display:flex; gap:8px; justify-content:flex-end; }
        .pii-btn { padding:7px 14px; font-size:12.5px; border-radius:7px; cursor:pointer; font-family:inherit; border:1px solid transparent; }
        .pii-btn-text { background:transparent; color:var(--text-200); border-color:var(--border-200); }
        .pii-btn-primary { background:#0d6efd; color:#fff; }
      `;
      document.head.appendChild(tmp);
    };
    ensureStyles();
    const filesList = classifiedFiles.map(f => {
      const c = f.scan.classification;
      const lbl = c.level_label_de || c.final_level;
      const act = c.effective_action;
      return `<div class="pii-source-card">
        <b>${esc(f.name)}</b>
        <span style="color:var(--text-300);margin-left:6px">— ${esc(lbl)} (Aktion: ${esc(act)})</span>
      </div>`;
    }).join('');
    const html = `
      <div class="pii-overlay">
        <div class="pii-card">
          <div class="pii-header">
            <h3 class="pii-title">${isStrict ? '🔒 Streng vertraulich — Versand blockiert' : '🔒 Klassifizierter Inhalt'}</h3>
            <p class="pii-subtitle">${esc(subtitle)}</p>
          </div>
          <div class="pii-body">${filesList}</div>
          <div class="pii-footer">
            <button class="pii-btn pii-btn-text" id="cls-modal-cancel">Abbrechen</button>
            ${isStrict ? '' : '<button class="pii-btn pii-btn-primary" id="cls-modal-local">Lokales Modell verwenden</button>'}
          </div>
        </div>
      </div>`;
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const overlay = wrap.firstElementChild;
    document.body.appendChild(overlay);
    const cleanup = (choice) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(choice);
    };
    const onKey = (e) => { if (e.key === 'Escape') cleanup('cancel'); };
    document.addEventListener('keydown', onKey);
    document.getElementById('cls-modal-cancel').onclick = () => cleanup('cancel');
    if (!isStrict) {
      document.getElementById('cls-modal-local').onclick = () => cleanup('local');
      setTimeout(() => document.getElementById('cls-modal-local')?.focus(), 50);
    } else {
      setTimeout(() => document.getElementById('cls-modal-cancel')?.focus(), 50);
    }
  });
}

// Unified PII surfacing — single composer-toolbar icon for draft + history.
// Replaces the v8.6.x split between the above-composer pill (draft) and the
// toolbar icon (history-only). Severity escalates via icon colour; the hover
// popover shows what's present in each scope. The pre-send modal stays the
// actionable affordance — this badge is awareness only.
function updatePIIBadge() {
  // Drop any leftover legacy pill so a hot-reload doesn't leave one behind.
  document.getElementById('pii-inline-badge')?.remove();
  if (state.piiScannerEnabled === false) {
    _updatePIIComposerBadge(null, null, null, false);
    return;
  }
  // 9.200.0: detection is SERVER-ONLY. There is no as-you-type DRAFT scan any
  // more (the browser regex scanner was removed) — surfacing draft PII would
  // mean a request per keystroke. The composer badge now reflects only the
  // chat HISTORY scan (already server-driven, async). The draft's PII is
  // surfaced by the pre-send dialog instead (which runs the server scan with a
  // cancellable progress overlay). No draft attachment PII pre-badge either.
  const chat = state.activeChat;
  const historyHas = !!(chat && piiHistoryHasFindings(chat));

  // 9.196.0: the automatic PII-driven swap-to-local was REMOVED. PII findings
  // no longer change the model behind the user's back — the pre-send dialog +
  // server-side confidence bands handle enforcement. The badge below still
  // surfaces that PII is present in the conversation (informational).

  _updatePIIComposerBadge(chat, null, historyHas, false);
}

// Single composer-toolbar icon for draft + history PII. Severity:
//   red    = block-mode active and current model is not local
//   green  = block-mode active and current model IS local (safe routing)
//   amber  = draft has PII (pre-send warn)
//   amber  = history-only PII (informational)
function _updatePIIComposerBadge(chat, draftScan, historyHas, draftHas) {
  const buttons = document.querySelectorAll('[data-id="btn-pii-history"]');
  const show = !!(draftHas || historyHas);
  buttons.forEach((btn) => {
    if (!show) {
      btn.style.display = 'none';
      _piiHistoryHidePopover();
      btn.onmouseenter = btn.onmouseleave = btn.onfocus = btn.onblur = null;
      return;
    }
    btn.style.display = '';
    // Decide tone. Block-mode states only apply when the draft has PII —
    // a history-only badge stays in info-amber regardless.
    const blockOn = !!(draftHas && piiBlockActive(chat));
    const curLocal = !!(chat && chat.model && isModelLocal(chat.model));
    let color = '#92400e';        // amber (default)
    let titleScope;
    if (draftHas && historyHas) {
      titleScope = 'Personenbezogene Daten im Entwurf und im Chat-Verlauf';
    } else if (draftHas) {
      titleScope = 'Personenbezogene Daten in der Nachricht';
    } else {
      titleScope = 'Personenbezogene Daten im Chat-Verlauf';
    }
    if (blockOn && !curLocal) color = '#b91c1c';   // red
    else if (blockOn && curLocal) color = '#3f6212'; // green
    btn.style.color = color;
    btn.setAttribute('title', titleScope);
    // Popover content depends on what's present — let the renderer read the
    // scan + chat off the closure each hover so counts stay fresh.
    btn.onmouseenter = () => _piiHistoryShowPopover(btn, {
      draft: draftHas ? draftScan : null,
      history: historyHas ? chat : null,
      blockOn, curLocal, chat,
    });
    btn.onmouseleave = () => _piiHistoryHidePopover();
    btn.onfocus = () => _piiHistoryShowPopover(btn, {
      draft: draftHas ? draftScan : null,
      history: historyHas ? chat : null,
      blockOn, curLocal, chat,
    });
    btn.onblur = () => _piiHistoryHidePopover();
  });
}

let _piiHistoryPopover = null;
function _piiHistoryShowPopover(anchorBtn, payload) {
  _piiHistoryHidePopover();
  // Backwards-compat: a plain counts object (legacy callers) is treated as
  // history-only. Current callers pass {draft, history, blockOn, curLocal, chat}.
  let draftScan = null, historyChat = null, blockOn = false, curLocal = false, chat = null;
  if (payload && typeof payload === 'object' &&
      ('draft' in payload || 'history' in payload)) {
    draftScan = payload.draft || null;
    historyChat = payload.history || null;
    blockOn = !!payload.blockOn;
    curLocal = !!payload.curLocal;
    chat = payload.chat || null;
  } else {
    // Legacy shape: object of {rule_id: count}
    historyChat = { _piiHistoryCounts: payload || {} };
  }
  const rect = anchorBtn.getBoundingClientRect();
  const pop = document.createElement('div');
  pop.id = 'pii-history-popover';
  pop.style.cssText = [
    'position:fixed',
    'left:' + Math.round(rect.left) + 'px',
    'bottom:' + Math.round(window.innerHeight - rect.top + 8) + 'px',
    'z-index:9000',
    'background:var(--bg-000)',
    'color:var(--text-100)',
    'border:1px solid var(--border-200)',
    'border-radius:10px',
    'box-shadow:0 10px 28px -8px rgba(31,30,29,.25), 0 0 0 1px rgba(31,30,29,.04)',
    'padding:10px 12px',
    'font-size:12px',
    'line-height:1.45',
    'min-width:240px',
    'max-width:340px',
    'pointer-events:none',
  ].join(';');
  const shieldSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/><path d="M12 8v4"/><circle cx="12" cy="16" r="0.6" fill="currentColor"/></svg>';
  const headerColor =
    blockOn && !curLocal ? '#b91c1c' :
    blockOn && curLocal  ? '#3f6212' :
    '#92400e';
  const headerText =
    (draftScan && historyChat) ? 'Personenbezogene Daten in Entwurf und Verlauf'
    : draftScan ? 'Personenbezogene Daten in der Nachricht'
    : 'Personenbezogene Daten im Verlauf';

  const sections = [];

  if (draftScan) {
    const counts = draftScan.counts || {};
    const entries = Object.entries(counts).filter(([, v]) => (v || 0) > 0)
                          .sort((a, b) => (b[1] || 0) - (a[1] || 0));
    const total = entries.reduce((a, [, v]) => a + (v || 0), 0);
    const rows = entries.map(([k, v]) =>
      '<div style="display:flex;justify-content:space-between;gap:12px;padding:2px 0">' +
        '<span style="color:var(--text-200)">' + esc(k) + '</span>' +
        '<span style="font-weight:600;color:var(--text-100)">' + v + '</span>' +
      '</div>'
    ).join('');
    let statusLine = '';
    if (blockOn && !curLocal) {
      statusLine = '<div style="color:#b91c1c;font-size:11px;margin-top:6px">Cloud-Versand blockiert — bitte lokales Modell wählen.</div>';
    } else if (blockOn && curLocal) {
      const localName = chat && chat.model ? modelShortName(chat.model) : 'lokales Modell';
      statusLine = '<div style="color:#3f6212;font-size:11px;margin-top:6px">Läuft über lokales Modell <b>' + esc(localName) + '</b> — Daten verlassen das Netzwerk nicht.</div>';
    } else {
      statusLine = '<div style="color:var(--text-400);font-size:11px;margin-top:6px">Vor dem Senden erscheint eine Auswahl (Anonymisieren / lokales Modell / weiter).</div>';
    }
    sections.push(
      '<div style="font-weight:600;font-size:11.5px;color:var(--text-200);margin-top:4px">Entwurf · ' + total + ' Treffer</div>' +
      rows + statusLine
    );
  }

  if (historyChat) {
    const head = (txt) => '<div style="font-weight:600;font-size:11.5px;color:var(--text-200);margin-top:' +
      (draftScan ? '10px' : '4px') + '">' + txt + '</div>';

    // (A) COMPLETE picture — the server history scan's label→count list. This
    // is the authoritative "Gesamtheit der PII im Verlauf": it covers EVERY
    // value across all turns (email from turn 1 AND phone from turn 5), unlike
    // the per-finding decisions below which only exist for values the user
    // actively decided (accepted-cleartext / anonymise). Always shown so prior-
    // turn PII never disappears when a new turn adds a finding (the bug in chat
    // 6f034721: only the latest decided value, the phone, was showing).
    const counts = historyChat._piiHistoryCounts || {};
    const entries = Object.entries(counts).filter(([, v]) => (v || 0) > 0)
                          .sort((a, b) => (b[1] || 0) - (a[1] || 0));
    if (entries.length > 0) {
      const total = entries.reduce((a, [, v]) => a + (v || 0), 0);
      const rows = entries.map(([k, v]) =>
        '<div style="display:flex;justify-content:space-between;gap:12px;padding:2px 0">' +
          '<span style="color:var(--text-200)">' + esc(k) + '</span>' +
          '<span style="font-weight:600;color:var(--text-100)">' + v + '</span>' +
        '</div>').join('');
      sections.push(head('Im Verlauf · ' + total + ' Treffer') + rows);
    }

    // (B) Per-finding DECISION detail — an ADDITIONAL section (not a
    // replacement) shown when the user has decided specific values. Each row:
    // value (· pseudonym when anonymised) · confidence · outcome.
    const decided = Object.values(historyChat._piiDecisions || {})
      .filter(d => d && d.value);   // only entries with a real value are detailed
    if (decided.length > 0) {
      const fakeMap = (typeof _gdprOriginalToFakeMap === 'function')
        ? _gdprOriginalToFakeMap(historyChat) : {};
      const outcome = d => {
        if (d.false_positive) return { txt: 'nicht anonymisiert (Falschtreffer)', col: '#b45309' };
        if (d.turn_action === 'anonymise') return { txt: 'anonymisiert', col: '#047857' };
        if (d.turn_action === 'local' || d.turn_action === 'local_model')
          return { txt: 'lokal verarbeitet', col: '#047857' };
        return { txt: 'nicht anonymisiert (akzeptiert)', col: '#b45309' };
      };
      const rows = decided.map(d => {
        const o = outcome(d);
        const conf = (d.confidence != null && !isNaN(Number(d.confidence)))
          ? Number(d.confidence).toFixed(2) : '';
        const fake = fakeMap[d.value];
        const valCell = fake
          ? esc(d.value) + ' <span style="color:var(--text-400)">→</span> <span style="color:#047857">' + esc(fake) + '</span>'
          : esc(d.value);
        // Two lines per finding: (value · confidence) then the outcome — keeps a
        // long value + long outcome label from squeezing each other into a
        // one-char-per-line column.
        return '<div style="padding:4px 0;border-top:1px solid var(--border-100)">' +
            '<div style="display:flex;align-items:baseline;gap:8px">' +
              '<span style="flex:1;font-family:ui-monospace,monospace;font-size:11px;color:var(--text-200);word-break:break-all">' + valCell + '</span>' +
              (conf ? '<span style="flex:none;font-size:10px;color:var(--text-400);font-variant-numeric:tabular-nums">' + conf + '</span>' : '') +
            '</div>' +
            '<div style="font-size:10px;color:' + o.col + ';margin-top:1px">' + esc(o.txt) + '</div>' +
          '</div>';
      }).join('');
      sections.push(head('Geprüfte Werte · ' + decided.length) + rows);
    }
  }

  if (sections.length === 0) return;

  pop.innerHTML =
    '<div style="display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:4px;color:' + headerColor + '">' +
      shieldSvg + esc(headerText) +
    '</div>' +
    sections.join('');
  document.body.appendChild(pop);
  _piiHistoryPopover = pop;
}
// Build {original: fake} from a chat's persisted anonymisation spans, so the
// history tooltip + modal can show what a value was pseudonymised to.
function _gdprOriginalToFakeMap(chat) {
  const map = {};
  for (const m of (chat?.messages || [])) {
    const spans = m?.metadata?.gdpr_restored_spans;
    if (!Array.isArray(spans)) continue;
    for (const sp of spans) {
      if (sp && sp.original && sp.fake) map[sp.original] = sp.fake;
    }
  }
  return map;
}
function _piiHistoryHidePopover() {
  if (_piiHistoryPopover) {
    _piiHistoryPopover.remove();
    _piiHistoryPopover = null;
  }
}

// Debounced hook — called from composer oninput + after file previews change.
let _piiBadgeTimer = null;
function schedulePIIBadgeUpdate() {
  clearTimeout(_piiBadgeTimer);
  _piiBadgeTimer = setTimeout(updatePIIBadge, 180);
}
