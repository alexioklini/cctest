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
// Cancellable pre-send PII scan covering BOTH the typed text AND the deferred
// attachments (9.205.0: attachment scanning moved from attach-time to send-time
// so the heavy work — extract/OCR/NER — runs under THIS one progress+cancel
// overlay). `files` is state._pendingFiles; each deferred entry is scanned and
// its `.scan` populated in place (so the caller's assembly reads findings_full
// as before). Returns the TEXT scan result; attachment results live on the
// entries. Throws an error with `_cancelled=true` if the user aborts.
function runCancellableGdprScan(text, files) {
  files = Array.isArray(files) ? files : [];
  const deferred = files.filter(f => f && f.scan && f.scan.state === 'deferred');
  return new Promise((resolve, reject) => {
    const ctrl = new AbortController();
    let cancelled = false;
    let overlay = null;
    let stageEl = null;
    const setStage = (msg) => { if (stageEl) stageEl.textContent = msg; };
    // Only show the overlay if the work takes longer than ~250ms — a tiny
    // text-only scan is instant and a flashing modal would be noise.
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
      stageEl = overlay.querySelector('.gdpr-scan-text');
      overlay.querySelector('.gdpr-scan-cancel').onclick = () => {
        cancelled = true; ctrl.abort();
      };
      overlay._onKey = (e) => { if (e.key === 'Escape') { cancelled = true; ctrl.abort(); } };
      document.addEventListener('keydown', overlay._onKey);
      document.body.appendChild(overlay);
    }, 250);

    const teardown = () => {
      clearTimeout(showTimer);
      if (overlay) {
        if (overlay._onKey) document.removeEventListener('keydown', overlay._onKey);
        overlay.remove();
        overlay = null; stageEl = null;
      }
    };
    const _cancelErr = () => { const e = new Error('PII-Prüfung abgebrochen'); e._cancelled = true; return e; };

    (async () => {
      // 1) Attachments first (the heavy part) — scan each deferred file in turn,
      //    mutating its .scan. A blocking/failed reason is recorded on the entry;
      //    the caller decides what to do (block vs warn) as before.
      for (let i = 0; i < deferred.length; i++) {
        if (cancelled) throw _cancelErr();
        const f = deferred[i];
        setStage(`Anhang wird geprüft (${i + 1}/${deferred.length}): ${f.name}`);
        if (state.piiScannerEnabled === false) {
          f.scan = { state: 'done', scanned: false, reason: 'scanner_disabled' };
          continue;
        }
        try {
          const sid = state.activeChat?.sessionId || '';
          const res = await API.scanAttachment(sid, f, { signal: ctrl.signal });
          f.scan = Object.assign({ state: 'done' }, res || {});
        } catch (e) {
          if (cancelled || (e && e.name === 'AbortError')) throw _cancelErr();
          // Fail-open per file: mark scan failed (non-blocking) and continue.
          f.scan = { state: 'done', scanned: false, reason: 'extract_failed',
                     error: String(e && e.message || e) };
        }
      }
      // 2) Typed text.
      if (cancelled) throw _cancelErr();
      if (text && text.trim() && state.piiScannerEnabled !== false) {
        setStage('Nachricht wird geprüft …');
        try {
          return await API.scanText(text, 'message', { full: true, signal: ctrl.signal });
        } catch (e) {
          if (cancelled || (e && e.name === 'AbortError')) throw _cancelErr();
          // Text-scan failure is fail-open (send proceeds without typed-text
          // findings) — return null, not a throw.
          console.warn('[gdpr-scan] text scan failed:', e?.message || e);
          return null;
        }
      }
      return null;
    })()
      .then(res => { teardown(); resolve(res); })
      .catch(err => { teardown(); reject(err); });
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
    // Cloud egress forbidden by document classification — strict ("Streng
    // vertraulich", ARL §1.11) OR force_local / non-strict block. In ALL these
    // cases a LOCAL model is still allowed (the server-side gate is a no-op for
    // local models — see engine/classification.py: "Already on a local model —
    // nothing to reroute"), so the ONLY consequence here is that the cloud
    // button is locked. (This deliberately relaxes the old client-only
    // strict→cancel-only behaviour to strict→local-allowed, per user decision;
    // the server already permitted local for strict content.)
    const cloudForbidden = clsActive
                           && (clsWorstAction === 'block'
                               || clsWorstAction === 'force_local')
                           && !localActive;
    const strictVeto = clsActive && clsWorstLevel === 'strict'
                       && clsWorstAction === 'block';

    // Red header tint = "serious": a PII block, a cloud-forbidden classification,
    // or a strict document (serious even when already on a local model).
    const isBlock = scan.worstAction === 'block' || cloudForbidden || strictVeto;
    const hasPiiFindings = (scan.findings && scan.findings.length > 0);
    // ─── Two-button model (user decision, replaces the old three) ───
    //   "Senden an Cloud-Modell" (primary, verdict 'anonymise'): anonymises the
    //      NON-false-positive findings and sends to the cloud model. If the user
    //      marks EVERY finding as a false positive the mapping is empty → the
    //      message goes UNCHANGED to the cloud (FP values never enter the
    //      mapping, verified server-side). One button covers both "anonymise
    //      some" and "all-FP → cleartext"; the old separate 'send'/Trotzdem-
    //      senden verdict is gone. Disabled (visible, with tooltip) when
    //      classification forbids cloud egress — that can't be marked away.
    //   "Unverändert senden an lokales Modell" (secondary, verdict 'local'):
    //      unchanged send to a local model. Always available (local is safe
    //      even for strict content).
    const cloudDisabled = cloudForbidden;
    const canCloud = true;   // always shown; `cloudDisabled` gates the click
    const canLocal = true;   // local send always available
    // Model-honest labels: `chat.model` is where the send actually goes — the
    // anonymise verdict does NOT switch to cloud, it just anonymises. So when
    // the selected model is ALREADY local, "an Cloud-Modell" would be a lie;
    // the choice there is anonymise-first vs unchanged, both staying local.
    const cloudBtnLabel = localActive
      ? 'Anonymisiert senden'            // already on a local model
      : 'Senden an Cloud-Modell';
    const localBtnLabel = localActive
      ? 'Unverändert senden'
      : 'Unverändert senden an lokales Modell';
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
          width:min(1080px, calc(100% - 32px));
          max-height:88vh;
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
          flex:1 1 auto; min-width:0; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
          color:var(--text-300); word-break:break-all; font-size:11.5px;
        }
        /* Context around the matched value in the Nachrichtentext column.
           NEVER wraps and the PII hit is ALWAYS fully visible: the cell is a
           one-line flex row; the highlighted hit is flex:none (never clipped),
           the surrounding context shrinks and truncates instead — the BEFORE
           part clips from its start (fade/ellipsis on the left), the AFTER part
           from its end. So even a very long line keeps the value on screen. */
        .pii-finding-val.pii-has-ctx {
          display:flex; align-items:baseline; gap:0; white-space:nowrap;
          overflow:hidden; word-break:normal;
        }
        .pii-finding-val.pii-has-ctx mark.pii-finding-hit { flex:0 0 auto; }
        .pii-finding-ctx {
          color:var(--text-400); overflow:hidden; text-overflow:ellipsis;
          white-space:nowrap; min-width:0;
        }
        .pii-finding-ctx.pii-ctx-before { flex:0 1 auto; direction:rtl; text-align:right; }
        .pii-finding-ctx.pii-ctx-before > bdi { direction:ltr; }
        .pii-finding-ctx.pii-ctx-after { flex:0 1 auto; }
        .pii-finding-val mark.pii-finding-hit {
          background:#fde68a; color:#78350f; padding:0 2px; border-radius:3px;
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
        /* On the wide modal, tile source-cards into a responsive grid so a chat
           with many sources (history + several files) stays compact. Falls back
           to one column under ~620px. The cards' own margin-top is neutralised
           by the grid gap. auto-FIT (not auto-fill) so a SINGLE source card —
           the common "just Nachrichtentext" case — stretches to the full modal
           width instead of leaving empty phantom columns to its right. */
        .pii-units {
          display:grid; gap:8px;
          grid-template-columns:repeat(auto-fit, minmax(420px, 1fr));
          align-items:start;
        }
        .pii-units .pii-source-card { margin-top:0; }
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
      // Value cell. The matched PII value is colour-highlighted; when the
      // server shipped surrounding context (message-text findings only), the
      // text BEFORE and AFTER the match is shown dimmed around it, so the user
      // sees WHERE in the message the value sits. Anonymised seen findings show
      // the value → pseudonym instead (no context needed there).
      const _piiMark = '<mark class="pii-finding-hit">' + esc(f.value || '') + '</mark>';
      let valHtml, valCls = 'pii-finding-val', valTitle = '';
      if (fake) {
        valHtml = esc(f.value || '') + ' <span style="color:var(--text-400)">→</span> '
          + '<span style="color:#047857">' + esc(fake) + '</span>';
      } else if (f.context_before || f.context_after) {
        // BEFORE part: RTL container clips its far (leftmost) end with the
        // ellipsis, keeping the text ADJACENT to the value visible; <bdi> keeps
        // the words themselves readable left-to-right. AFTER part clips normally
        // on the right. The value (_piiMark) sits between and is never clipped.
        const pre = f.context_before
          ? '<span class="pii-finding-ctx pii-ctx-before"><bdi>' + esc(f.context_before) + '</bdi></span>'
          : '';
        const post = f.context_after
          ? '<span class="pii-finding-ctx pii-ctx-after">' + esc(f.context_after) + '</span>'
          : '';
        valHtml = pre + _piiMark + post;
        valCls += ' pii-has-ctx';   // one-line flex, hit never clipped
        // Full context on hover so nothing truncated is lost.
        valTitle = (f.context_before || '') + (f.value || '') + (f.context_after || '');
      } else {
        valHtml = _piiMark;
      }
      // Seen findings get a "Verlauf" toggle showing the SAME who/what/when
      // trail as the history modal (lazily loaded on first expand).
      const trailBtn = ratable ? ''
        : '<button type="button" class="pii-seen-trailbtn" title="Entscheidungs-Verlauf anzeigen">▸ Verlauf</button>';
      return '<div class="pii-finding pii-finding-row' + (ratable ? '' : ' pii-finding-seen') + '" ' +
          'data-pii-rule="' + esc(f.rule_id || '') + '" ' +
          'data-pii-value="' + esc(f.value || '') + '" ' +
          'data-pii-conf="' + esc(String(conf)) + '" ' +
          'data-pii-disp="' + esc(f.disposition || '') + '" ' +
          'data-pii-source="' + esc(f._source || '') + '" ' +
          'data-pii-seen="' + (ratable ? '0' : '1') + '"' + checked + '>' +
        '<span class="pii-finding-sev' + sevClass(f.action || 'warn') + '" title="' + esc(f.action || 'warn') + '"></span>' +
        '<span class="pii-finding-label" title="' + esc(f.rule_id || '') + '">' +
          esc(f.label || (typeof gdprRuleLabel === 'function' ? gdprRuleLabel(f.rule_id) : f.rule_id)) + '</span>' +
        '<span class="' + valCls + '" style="font-family:var(--font-mono,monospace)"' +
          (valTitle ? ' title="' + esc(valTitle) + '"' : '') + '>' + valHtml + '</span>' +
        '<span class="pii-finding-conf" title="Konfidenz ' + conf.toFixed(2) + '">' + conf.toFixed(2) + ' · ' + bandLabel(f.band) + '</span>' +
        fpCell + trailBtn +
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
        '</div><div class="pii-units">' + units + '</div></div>';
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
    // Priority order: strict veto > classification force_local >
    // PII hard block > PII warn.
    let title, subtitle;
    if (strictVeto) {
      title = 'Streng vertraulicher Inhalt erkannt';
      subtitle = 'Streng vertrauliche Dokumente dürfen ein Cloud-Modell nicht erreichen. '
        + 'Versand nur an ein lokales Modell möglich (die Daten verlassen das System nicht).';
    } else if (cloudForbidden) {
      title = 'Klassifizierter Inhalt erkannt';
      subtitle = 'Klassifizierter Anhang' + (hasPiiFindings ? ' + personenbezogene Daten' : '') +
        ' — Versand nur an ein lokales Modell möglich.';
    } else if (scan.worstAction === 'block') {
      title = 'Hochsensible personenbezogene Daten erkannt';
      // Subtitle reflects the ACTUALLY selected model — localActive (where the
      // data would go). With a cloud model selected, say so honestly.
      subtitle = localActive
        ? 'Hochsensible Daten erkannt — das gewählte Modell ist lokal, die Daten verlassen das System nicht.'
        : 'Hochsensible Daten erkannt. Beim Senden an das Cloud-Modell werden die nicht als '
          + 'Falschtreffer markierten Werte anonymisiert; alternativ unverändert an ein lokales Modell.';
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

    // Footer action hierarchy (TWO buttons, user decision):
    //   left:  Abbrechen (text-button)
    //   right: Unverändert an lokales Modell (secondary, verdict 'local') +
    //          Senden an Cloud-Modell (primary, verdict 'anonymise')
    // Canonical verdicts here: anonymise / local / cancel. ('send'/Trotzdem-
    // senden is gone — anonymise with all-FP already yields cleartext-to-cloud.)
    const localBtn = canLocal
      ? '<button class="pii-btn pii-btn-secondary" id="pii-local-btn">' + esc(localBtnLabel) + '</button>'
      : '';
    const cloudBtn = canCloud
      ? '<button class="pii-btn pii-btn-primary" id="pii-cloud-btn"' +
          (cloudDisabled
            ? ' disabled title="Gesperrt: klassifizierter Inhalt darf ein Cloud-Modell nicht erreichen. '
              + 'Bitte an ein lokales Modell senden."'
            : '') +
          '>' + esc(cloudBtnLabel) + '</button>'
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
              '<div class="pii-actions-spacer"></div>' +
              localBtn +
              cloudBtn +
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
    // No button re-evaluation needed — "Senden an Cloud-Modell" (anonymise)
    // stays enabled regardless: unmarked findings get anonymised, and marking
    // everything as FP simply yields an empty mapping → cleartext to cloud. The
    // cloud button is only ever locked by classification (cloudDisabled), which
    // FP marking cannot affect.
    for (const cb of overlay.querySelectorAll('.pii-fp-check')) {
      cb.addEventListener('change', (e) => {
        e.target.closest('.pii-finding-row')?.classList.toggle('pii-is-fp', e.target.checked);
      });
    }
    // "Verlauf" toggle on SEEN rows — same who/what/when trail as the history
    // modal (shared _piiRenderHistoryBlock). decision_history is fetched once,
    // lazily, on the first expand; rows look up their entry by value_hash.
    _injectPiiHistStyles();  // the .pii-trail styles live with the history modal
    let _seenHistCache = null;     // value_hash → trail (loaded once)
    const _loadSeenHist = async () => {
      if (_seenHistCache) return _seenHistCache;
      _seenHistCache = {};
      try {
        if (chat && chat.sessionId) {
          const d = await API.getSessionPiiHistoryDetail(chat.sessionId);
          _seenHistCache = (d && d.decision_history) || {};
        }
      } catch (e) { /* leave empty — trail just shows "no decision" */ }
      return _seenHistCache;
    };
    for (const btn of overlay.querySelectorAll('.pii-seen-trailbtn')) {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const row = btn.closest('.pii-finding-row');
        const existing = row.nextElementSibling;
        if (existing && existing.classList.contains('pii-seen-trail-wrap')) {
          existing.remove(); btn.textContent = '▸ Verlauf'; return;
        }
        btn.textContent = '▾ Verlauf';
        const hist = await _loadSeenHist();
        const vh = await _piiValueHash(row.dataset.piiRule, row.dataset.piiValue);
        const wrap = document.createElement('div');
        wrap.className = 'pii-seen-trail-wrap pii-hist-trail-wrap';
        wrap.innerHTML = _piiRenderHistoryBlock(hist[vh] || []);
        row.after(wrap);
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
    // "Senden an Cloud-Modell" = anonymise verdict (see the two-button note).
    document.getElementById('pii-cloud-btn')?.addEventListener('click', () => cleanup('anonymise'));
    // Default focus: the cloud (anonymise) button when it's enabled, else the
    // local button (the safe path when cloud is classification-locked), else
    // cancel.
    setTimeout(() => {
      const _cloud = document.getElementById('pii-cloud-btn');
      const pref = (_cloud && !_cloud.disabled) ? _cloud
                : (document.getElementById('pii-local-btn')
                || document.getElementById('pii-cancel-btn'));
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
  const chat = state.activeChat;
  // Prior PII DECISIONS are historical facts, INDEPENDENT of whether the
  // scanner is currently enabled — so the history button (→ overview modal)
  // must surface them even when detection is off. Compute this BEFORE the
  // scanner-disabled early-return (the bug: with the scanner off, the early
  // return hid the button for chats that already had recorded decisions —
  // chat 8bed3305). _piiDecisions is loaded on openSession (sessions.js).
  const decisionsHas = !!(chat && chat._piiDecisions &&
                          Object.keys(chat._piiDecisions).length);
  if (state.piiScannerEnabled === false) {
    // No live detection, but still show the button if decisions exist so the
    // user can review/edit them via the overview modal.
    _updatePIIComposerBadge(chat, null, decisionsHas, false);
    return;
  }
  // 9.200.0: detection is SERVER-ONLY. There is no as-you-type DRAFT scan any
  // more (the browser regex scanner was removed) — surfacing draft PII would
  // mean a request per keystroke. The composer badge now reflects only the
  // chat HISTORY scan (already server-driven, async). The draft's PII is
  // surfaced by the pre-send dialog instead (which runs the server scan with a
  // cancellable progress overlay). No draft attachment PII pre-badge either.
  const historyHas = !!(chat && piiHistoryHasFindings(chat));

  // 9.196.0: the automatic PII-driven swap-to-local was REMOVED. PII findings
  // no longer change the model behind the user's back — the pre-send dialog +
  // server-side confidence bands handle enforcement. The badge below still
  // surfaces that PII is present in the conversation (informational).

  _updatePIIComposerBadge(chat, null, historyHas || decisionsHas, false);
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
      btn.onclick = null;
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
    // Native title only — the old hover popover was retired (9.204.1); the full
    // history/overview modal IS the single view. Click opens it.
    btn.setAttribute('title', titleScope + ' — klicken für die Datenschutz-Übersicht');
    btn.onclick = () => openPiiHistoryModal();
  });
}

let _piiHistoryPopover = null;
// _piiHistoryShowPopover (hover popover) retired in 9.204.1 — the shield
// button now opens openPiiHistoryModal() on click; no hover preview.
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

// ── GDPR history overview modal ───────────────────────────────────────────
// Opened from the composer shield button. Shows EVERY PII finding across the
// whole chat (typed text, history, attachments) grouped by source, joined with
// the user's prior decisions so each finding shows its status AND the full
// "who decided what when" trail. Bulk actions let the user clear the topic in
// one pass. Built with the SAME .pii-card structure + General-Settings tokens
// as the pre-send decision modal (gdprActionModal) so the two look identical —
// only the purpose differs (review/edit history vs decide-before-send).
// Findings come MASKED from /v1/sessions/<id>/pii-history-detail; cleartext
// only ever comes from the user's own prior decisions.
// (_piiStatusOf retired in 9.204.6 — the DB-only pii-decisions-view endpoint
// returns the per-value status directly, so the client no longer derives it.)
const _PII_STATUS_META = {
  open:     { label: 'Offen',          color: '#92400e', bg: '#fef3c7' },
  anon:     { label: 'Anonymisiert',   color: '#3f6212', bg: '#ecfccb' },
  accepted: { label: 'Klartext gesendet', color: '#b91c1c', bg: '#fee2e2' },
  local:    { label: 'Lokal gesendet', color: '#3f6212', bg: '#ecfccb' },
  fp:       { label: 'Falschtreffer',  color: '#525252', bg: '#e5e5e5' },
  // Web-Egress-Consent (L4 Phase 2): eigener Ledger-Namespace, je Wert.
  web_released: { label: 'Web freigegeben', color: '#1d4ed8', bg: '#dbeafe' },
  web_denied:   { label: 'Web verweigert',  color: '#9a3412', bg: '#ffedd5' },
};
// Map a stored decision's turn_action/false_positive to a status (for history
// rows) + a short German verb for the trail.
function _piiActionLabel(ev) {
  if (ev.false_positive) return 'als Falschtreffer markiert';
  const a = ev.turn_action || '';
  if (a === 'anonymise') return 'anonymisiert';
  if (a === 'local' || a === 'local_model') return 'lokal gesendet';
  if (a === 'send') return 'im Klartext gesendet';
  if (a === 'continue') return 'zurückgesetzt';
  if (a === 'history_edit') return 'in der Übersicht geändert';
  if (a === 'release_web') return 'für die Web-Recherche freigegeben';
  if (a === 'deny_web') return 'Web-Freigabe verweigert/widerrufen';
  return a || 'entschieden';
}
function _piiFmtWhen(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit',
      year: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch (e) { return ''; }
}
// value_hash = sha256(rule_id|value), matching ChatDB.record_pii_decisions, so
// the client can look up a finding's decision trail in the server's
// decision_history map (keyed by value_hash). Async (crypto.subtle).
async function _piiValueHash(ruleId, value) {
  try {
    const data = new TextEncoder().encode((ruleId || '') + '|' + (value || ''));
    const buf = await crypto.subtle.digest('SHA-256', data);
    return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
  } catch (e) { return ''; }
}
// SHARED "who decided what when" trail — used by BOTH the history modal and the
// pre-send decision modal's already-seen findings, so they look the same.
// `history` = [{turn_action, false_positive, fake_value, by, at}] oldest-first.
function _piiRenderHistoryBlock(history) {
  if (!Array.isArray(history) || !history.length) {
    return '<div class="pii-trail-empty">Noch keine Entscheidung getroffen.</div>';
  }
  const rows = history.map((ev) => {
    const fake = ev.fake_value
      ? '<span class="pii-trail-fake"> → ' + esc(ev.fake_value) + '</span>' : '';
    return '<div class="pii-trail-row">' +
      '<span class="pii-trail-dot"></span>' +
      '<span class="pii-trail-act">' + esc(_piiActionLabel(ev)) + fake + '</span>' +
      '<span class="pii-trail-by">' + esc(ev.by || 'System') + '</span>' +
      '<span class="pii-trail-at">' + esc(_piiFmtWhen(ev.at)) + '</span>' +
    '</div>';
  }).join('');
  return '<div class="pii-trail">' + rows + '</div>';
}

let _piiHistModalState = null;
const _PII_GROUP_PAGE = 50;  // lazy-render chunk per expanded group (virtualisation)

async function openPiiHistoryModal() {
  const chat = state.activeChat;
  const sid = chat && chat.sessionId;
  if (!sid) { showToast('Keine gespeicherte Sitzung', true); return; }
  _piiHistoryHidePopover();
  _injectPiiHistStyles();

  const overlay = document.createElement('div');
  overlay.className = 'pii-overlay';
  overlay.id = 'pii-history-modal';
  overlay.innerHTML = `
    <div class="pii-card" role="dialog" aria-modal="true" aria-labelledby="pii-hist-title">
      <div class="pii-header">
        <div class="pii-shield">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/><path d="M9 12l2 2 4-4"/></svg>
        </div>
        <div class="pii-header-text">
          <h2 id="pii-hist-title" class="pii-title">Datenschutz — Übersicht &amp; Bearbeitung</h2>
          <p class="pii-subtitle">Alle Datenschutz-Entscheidungen dieses Chats — je Wert mit aktuellem Status und vollständigem Verlauf (wer, wann, was).</p>
        </div>
        <button class="pii-hist-close pii-btn pii-btn-text" aria-label="Schließen" style="font-size:20px;line-height:1;padding:2px 8px">&times;</button>
      </div>
      <div class="pii-hist-toolbar">
        <div class="pii-hist-summary"></div>
        <div class="pii-hist-controls">
          <input type="text" class="pii-hist-search" placeholder="Suchen (Kategorie, Wert, Quelle)…">
          <select class="pii-hist-filter-status">
            <option value="">Alle Status</option>
            <option value="open">Offen</option>
            <option value="anon">Anonymisiert</option>
            <option value="accepted">Klartext gesendet</option>
            <option value="local">Lokal gesendet</option>
            <option value="fp">Falschtreffer</option>
            <option value="web_released">Web freigegeben</option>
            <option value="web_denied">Web verweigert</option>
          </select>
        </div>
      </div>
      <div class="pii-body pii-hist-body"></div>
      <div class="pii-footer">
        <div class="pii-actions">
          <span class="pii-hist-selcount"></span>
          <div class="pii-actions-spacer"></div>
          <button class="pii-btn pii-btn-secondary pii-hist-bulk" data-act="fp" disabled>Als Falschtreffer markieren</button>
          <button class="pii-btn pii-btn-secondary pii-hist-bulk" data-act="accepted" disabled>Als Klartext akzeptieren</button>
          <button class="pii-btn pii-btn-secondary pii-hist-bulk" data-act="reset" disabled>Entscheidung zurücksetzen</button>
          <button class="pii-btn pii-btn-primary pii-hist-save" disabled>Änderungen speichern</button>
        </div>
        <p class="pii-suppress-note">Anonymisieren geschieht im Hinweis-Dialog <em>vor dem Senden</em> (es braucht eine Sende-Zeit-Zuordnung). Hier siehst du den Status und kannst ihn zurücksetzen.</p>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const close = () => { overlay.remove(); _piiHistModalState = null; document.removeEventListener('keydown', _esc); };
  overlay.querySelector('.pii-hist-close').onclick = close;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  const _esc = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', _esc);

  const bodyEl = overlay.querySelector('.pii-hist-body');
  bodyEl.innerHTML = '<div class="pii-hist-msg">Wird geladen…</div>';

  // DB-ONLY: the modal reads the persisted decision ledger (one row per decided
  // value, with status + who/when trail) — it does NOT re-scan the chat. A live
  // re-scan produced phantom "open" duplicates because its string form for a
  // value differed from the stored decision's, so the value_hash join missed.
  // Reading the ledger means every row has a decision by definition.
  let view;
  try {
    view = await API.getSessionPiiDecisionsView(sid);
  } catch (e) {
    bodyEl.innerHTML = '<div class="pii-hist-msg" style="color:#b91c1c">Laden fehlgeschlagen: ' + esc(e.message || String(e)) + '</div>';
    return;
  }
  const items = (view && view.items || []).map((it, i) => ({
    idx: i,
    rule_id: it.rule_id,
    label: it.label || (typeof gdprRuleLabel === 'function' ? gdprRuleLabel(it.rule_id) : it.rule_id),
    category: it.category || '',
    // Show the real value (the user's own chat — cleartext is in the visible
    // history anyway); fall back to masked if the server omitted it.
    masked: it.value || it.masked,
    value_hash: it.value_hash,
    source: it.source || 'history',
    source_label: it.source_label || 'Chat-Verlauf',
    // The decision object so the value/fake render the same as before.
    decision: { value: it.value || it.masked, fake_value: it.fake_value,
                false_positive: it.false_positive, turn_action: it.turn_action },
    history: it.history || [],
    pending: null, baseStatus: it.status || 'open',
    sel: false, expanded: false,
  }));
  _piiHistModalState = { sid, chat, items, overlay,
    truncated: false, collapsed: {}, shownCount: {} };
  _piiHistRender();
  _piiHistWireToolbar();
}

function _piiHistEffectiveStatus(it) {
  return it.pending || it.baseStatus;
}

function _piiHistRender() {
  const S = _piiHistModalState;
  if (!S) return;
  const overlay = S.overlay;
  const bodyEl = overlay.querySelector('.pii-hist-body');
  const query = (overlay.querySelector('.pii-hist-search').value || '').trim().toLowerCase();
  const fStatus = overlay.querySelector('.pii-hist-filter-status').value || '';

  // Summary chips (over the FULL set, not the filtered view).
  const tally = { open: 0, anon: 0, accepted: 0, local: 0, fp: 0,
    web_released: 0, web_denied: 0 };
  for (const it of S.items) tally[_piiHistEffectiveStatus(it)]++;
  const sumEl = overlay.querySelector('.pii-hist-summary');
  const chip = (n, meta) => n ? '<span class="pii-hist-chip" style="background:' + meta.bg + ';color:' + meta.color + '">' + n + ' ' + esc(meta.label) + '</span>' : '';
  sumEl.innerHTML =
    '<span class="pii-hist-total">' + S.items.length + ' Einträge gesamt</span>' +
    chip(tally.open, _PII_STATUS_META.open) +
    chip(tally.anon, _PII_STATUS_META.anon) +
    chip(tally.accepted, _PII_STATUS_META.accepted) +
    chip(tally.local, _PII_STATUS_META.local) +
    chip(tally.fp, _PII_STATUS_META.fp) +
    chip(tally.web_released, _PII_STATUS_META.web_released) +
    chip(tally.web_denied, _PII_STATUS_META.web_denied) +
    (S.truncated ? '<span class="pii-hist-note">(Liste gekürzt — sehr viele Funde)</span>' : '');

  const shown = S.items.filter((it) => {
    if (fStatus && _piiHistEffectiveStatus(it) !== fStatus) return false;
    if (query) {
      const hay = (it.label + ' ' + it.masked + ' ' + it.source_label + ' ' + it.category).toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });
  if (!shown.length) {
    bodyEl.innerHTML = '<div class="pii-hist-msg">' +
      (S.items.length ? 'Keine Einträge für diesen Filter.'
        : 'Für diesen Chat wurden noch keine Datenschutz-Entscheidungen getroffen.') + '</div>';
    _piiHistUpdateFooter();
    return;
  }
  const groups = new Map();
  for (const it of shown) {
    if (!groups.has(it.source)) groups.set(it.source, []);
    groups.get(it.source).push(it);
  }
  // history group first, then files alphabetically.
  const order = [...groups.keys()].sort((a, b) =>
    (a === 'history' ? -1 : 0) - (b === 'history' ? -1 : 0) || a.localeCompare(b));
  // A filter/search active → auto-expand so matches are visible; otherwise use
  // a SOURCE-DEPENDENT default that the user's explicit toggle overrides:
  //   • "Frühere Entscheidungen" / chat history → EXPANDED (show the full trail)
  //   • attachment groups (file:…) → COLLAPSED (they can hold many findings)
  const forceOpen = !!(query || fStatus);

  let html = '';
  for (const src of order) {
    const list = groups.get(src);
    const srcLabel = list[0].source_label || src;
    const isFile = String(src).indexOf('file:') === 0;
    // S.collapsed[src] is the user's explicit choice (true/false); undefined =
    // untouched → fall back to the per-source default (files collapsed, rest open).
    const explicit = S.collapsed[src];
    const collapsed = forceOpen ? false
      : (explicit === undefined ? isFile : explicit);
    // Status mix for the group head.
    const gTally = {};
    for (const it of list) { const s = _piiHistEffectiveStatus(it); gTally[s] = (gTally[s] || 0) + 1; }
    const mix = Object.entries(gTally).map(([s, n]) => {
      const m = _PII_STATUS_META[s] || _PII_STATUS_META.open;
      return '<span class="pii-hist-minichip" style="background:' + m.bg + ';color:' + m.color + '">' + n + '</span>';
    }).join('');
    const budget = forceOpen ? list.length : (S.shownCount[src] || _PII_GROUP_PAGE);
    const slice = list.slice(0, budget);
    html += '<div class="pii-source-card' + (collapsed ? ' pii-collapsed' : '') + '" data-src="' + esc(src) + '">' +
      '<div class="pii-source-head pii-hist-grouphead">' +
        '<span class="pii-unit-caret">' + (collapsed ? '▸' : '▾') + '</span>' +
        '<label class="pii-hist-grouplabel" onclick="event.stopPropagation()">' +
          '<input type="checkbox" class="pii-hist-group-sel" data-src="' + esc(src) + '">' +
          '<span class="pii-source-name">' + esc(srcLabel) + '</span>' +
        '</label>' +
        '<span class="pii-source-count" style="margin-left:auto">' + list.length + ' Treffer</span>' +
        '<span class="pii-hist-mix">' + mix + '</span>' +
      '</div>' +
      '<div class="pii-unit-rows">';
    for (const it of slice) {
      html += _piiHistRowHtml(it);
    }
    if (slice.length < list.length) {
      html += '<div class="pii-hist-more" data-src="' + esc(src) + '">+ ' + (list.length - slice.length) + ' weitere anzeigen</div>';
    }
    html += '</div></div>';
  }
  bodyEl.innerHTML = html;
  _piiHistWireRows(bodyEl, forceOpen);
  _piiHistUpdateFooter();
}

function _piiHistRowHtml(it) {
  const st = _piiHistEffectiveStatus(it);
  const meta = _PII_STATUS_META[st] || _PII_STATUS_META.open;
  const changed = it.pending && it.pending !== it.baseStatus;
  const shownVal = (it.decision && it.decision.value) ? it.decision.value : it.masked;
  const fakeBit = (it.decision && it.decision.fake_value)
    ? '<span class="pii-finding-fixed"> → ' + esc(it.decision.fake_value) + '</span>' : '';
  const hasTrail = Array.isArray(it.history) && it.history.length;
  const sevClass = it.action === 'block' ? ' is-block' : '';
  const trailBtn = '<button class="pii-hist-trailbtn" data-idx="' + it.idx + '">' +
    (it.expanded ? '▾' : '▸') + ' Verlauf' + (hasTrail ? ' (' + it.history.length + ')' : '') + '</button>';
  // Historische Web-Egress-Consent-Zeilen (Ledger aus dem entfernten
  // ask-Modus): reine Anzeige — die Bulk-Aktionen (fp/Klartext/Reset)
  // passen nicht auf eine Web-Freigabe, daher von der Auswahl ausgenommen.
  const isWeb = it.baseStatus === 'web_released' || it.baseStatus === 'web_denied';
  const selBox = isWeb
    ? '<span style="display:inline-block;width:13px"></span>'
    : '<input type="checkbox" class="pii-hist-row-sel" data-idx="' + it.idx + '"' + (it.sel ? ' checked' : '') + '>';
  let row = '<div class="pii-finding pii-finding-row pii-hist-row' + (changed ? ' pii-hist-changed' : '') + '" data-idx="' + it.idx + '">' +
    selBox +
    '<span class="pii-finding-sev' + sevClass + '"></span>' +
    '<span class="pii-finding-label">' + esc(it.label) + '</span>' +
    '<span class="pii-finding-val">' + esc(shownVal) + fakeBit + '</span>' +
    '<span class="pii-hist-status" style="background:' + meta.bg + ';color:' + meta.color + '">' + esc(meta.label) + (changed ? ' *' : '') + '</span>' +
    trailBtn +
  '</div>';
  if (it.expanded) {
    row += '<div class="pii-hist-trail-wrap">' + _piiRenderHistoryBlock(it.history) + '</div>';
  }
  return row;
}

function _piiHistWireRows(bodyEl, forceOpen) {
  const S = _piiHistModalState;
  for (const cb of bodyEl.querySelectorAll('.pii-hist-row-sel')) {
    cb.onchange = () => {
      const it = S.items[+cb.dataset.idx];
      if (it) it.sel = cb.checked;
      _piiHistUpdateFooter();
    };
  }
  for (const cb of bodyEl.querySelectorAll('.pii-hist-group-sel')) {
    cb.onchange = () => {
      const src = cb.dataset.src;
      for (const it of S.items) if (it.source === src) it.sel = cb.checked;
      _piiHistRender();
    };
  }
  for (const head of bodyEl.querySelectorAll('.pii-hist-grouphead')) {
    head.onclick = () => {
      if (forceOpen) return;  // search/filter active — groups forced open
      const src = head.closest('.pii-source-card').dataset.src;
      _piiHistToggleGroup(src);
    };
  }
  for (const btn of bodyEl.querySelectorAll('.pii-hist-trailbtn')) {
    btn.onclick = (e) => {
      e.stopPropagation();
      const it = S.items[+btn.dataset.idx];
      if (it) { it.expanded = !it.expanded; _piiHistRender(); }
    };
  }
  for (const more of bodyEl.querySelectorAll('.pii-hist-more')) {
    more.onclick = (e) => {
      e.stopPropagation();
      const src = more.dataset.src;
      S.shownCount[src] = (S.shownCount[src] || _PII_GROUP_PAGE) + _PII_GROUP_PAGE;
      _piiHistRender();
    };
  }
}

function _piiHistToggleGroup(src) {
  const S = _piiHistModalState;
  if (!S) return;
  // Flip relative to the CURRENT effective collapsed state. The default is
  // source-dependent (files collapsed, rest expanded), so when untouched we
  // derive it from the source; an explicit value flips directly.
  const isFile = String(src).indexOf('file:') === 0;
  const effective = (S.collapsed[src] === undefined) ? isFile : S.collapsed[src];
  S.collapsed[src] = !effective;
  _piiHistRender();
}

function _piiHistUpdateFooter() {
  const S = _piiHistModalState;
  if (!S) return;
  const overlay = S.overlay;
  const selCount = S.items.filter((it) => it.sel).length;
  const dirty = S.items.some((it) => it.pending && it.pending !== it.baseStatus);
  overlay.querySelector('.pii-hist-selcount').textContent =
    selCount ? (selCount + ' ausgewählt') : '';
  for (const b of overlay.querySelectorAll('.pii-hist-bulk')) b.disabled = !selCount;
  overlay.querySelector('.pii-hist-save').disabled = !dirty;
}

function _piiHistWireToolbar() {
  const S = _piiHistModalState;
  if (!S) return;
  const overlay = S.overlay;
  overlay.querySelector('.pii-hist-search').oninput = () => _piiHistRender();
  overlay.querySelector('.pii-hist-filter-status').onchange = () => _piiHistRender();
  for (const b of overlay.querySelectorAll('.pii-hist-bulk')) {
    b.onclick = () => {
      const act = b.dataset.act; // fp | accepted | reset
      for (const it of S.items) {
        if (!it.sel) continue;
        // Web-Consent-Zeilen haben eigene Aktionen (Freigeben/Widerrufen) —
        // Gruppen-Auswahl darf sie nicht in fp/Klartext/Reset ziehen.
        if (it.baseStatus === 'web_released' || it.baseStatus === 'web_denied') {
          it.sel = false; continue;
        }
        if (act === 'reset') it.pending = it.baseStatus === 'open' ? null : 'open';
        else if (act === 'fp') it.pending = 'fp';
        else if (act === 'accepted') it.pending = 'accepted';
        it.sel = false;
      }
      _piiHistRender();
    };
  }
  overlay.querySelector('.pii-hist-save').onclick = () => _piiHistSave();
}

async function _piiHistSave() {
  const S = _piiHistModalState;
  if (!S) return;
  const changed = S.items.filter((it) => it.pending && it.pending !== it.baseStatus);
  if (!changed.length) return;
  const saveBtn = S.overlay.querySelector('.pii-hist-save');
  saveBtn.disabled = true; saveBtn.textContent = 'Wird gespeichert…';
  // Map modal status → persisted turn_action/false_positive. 'reset' (→ open)
  // records a neutral 'continue' row with FP cleared so latest-row-wins drops
  // the prior verdict. Persisted by value_hash (no cleartext for undecided).
  const decisions = changed.map((it) => {
    const st = it.pending;
    const fp = st === 'fp';
    const turn_action = (st === 'accepted') ? 'send' : 'continue';
    return {
      rule_id: it.rule_id,
      value: (it.decision && it.decision.value) || '',
      value_hash: it.value_hash,
      false_positive: fp,
      source: it.source,
      disposition: 'history-modal',
      turn_action,
    };
  });
  try {
    if (decisions.length) await API.recordPiiDecisions(S.sid, 'history_edit', decisions);
    for (const it of changed) {
      it.baseStatus = it.pending;
      it.pending = null;
      it.decision = Object.assign({}, it.decision || {}, {
        false_positive: it.baseStatus === 'fp',
        turn_action: it.baseStatus === 'accepted' ? 'send' : 'continue',
      });
    }
    if (S.chat) S.chat._piiDecisions = null;
    showToast('Datenschutz-Entscheidungen gespeichert');
    if (typeof schedulePIIBadgeUpdate === 'function') schedulePIIBadgeUpdate();
    _piiHistRender();
  } catch (e) {
    showToast('Speichern fehlgeschlagen: ' + (e.message || e), true);
  } finally {
    saveBtn.textContent = 'Änderungen speichern';
    _piiHistUpdateFooter();
  }
}

// Styles for the history modal. CRITICAL: this injects the BASE .pii-overlay /
// .pii-card / .pii-finding-* / .pii-btn-* / header/body/footer rules too — the
// history modal must NOT depend on #pii-modal-styles-v3 (that block is only
// injected when the pre-send gdprActionModal runs; a user who clicks the shield
// without ever seeing the pre-send dialog would otherwise get an UNSTYLED,
// invisible overlay — the "click opens nothing" bug). All rules are scoped or
// idempotent so coexisting with #pii-modal-styles-v3 is harmless.
function _injectPiiHistStyles() {
  if (document.getElementById('pii-hist-styles')) return;
  const st = document.createElement('style');
  st.id = 'pii-hist-styles';
  st.textContent = `
    @keyframes pii-hist-fade { from{opacity:0} to{opacity:1} }
    #pii-history-modal.pii-overlay { position:fixed; inset:0; z-index:9999; display:flex; align-items:center; justify-content:center; background:rgba(20,18,16,.52); -webkit-backdrop-filter:blur(4px); backdrop-filter:blur(4px); padding:20px; animation:pii-hist-fade .15s ease-out; }
    #pii-history-modal .pii-card { display:flex; flex-direction:column; background:var(--bg-000,#faf9f7); border-radius:14px; box-shadow:0 20px 50px -16px rgba(31,30,29,.32), 0 0 0 1px rgba(31,30,29,.06); overflow:hidden; }
    #pii-history-modal .pii-header { display:flex; align-items:flex-start; gap:14px; padding:20px 24px 16px; border-bottom:1px solid var(--border-100); }
    #pii-history-modal .pii-shield { flex:none; width:36px; height:36px; border-radius:9px; background:#fef3c7; color:#b45309; display:flex; align-items:center; justify-content:center; margin-top:1px; }
    #pii-history-modal .pii-header-text { flex:1; min-width:0; }
    #pii-history-modal .pii-title { font-size:15.5px; font-weight:600; letter-spacing:-.005em; line-height:1.3; margin:0; color:var(--text-000); }
    #pii-history-modal .pii-subtitle { font-size:12.5px; margin:3px 0 0; color:var(--text-300); line-height:1.45; }
    #pii-history-modal .pii-body { flex:1 1 auto; overflow-y:auto; padding:14px 24px 16px; }
    #pii-history-modal .pii-source-card { padding:0; border:1px solid var(--border-100); border-radius:10px; background:var(--bg-050,var(--bg-100)); margin-top:8px; overflow:hidden; }
    #pii-history-modal .pii-source-card:first-child { margin-top:0; }
    #pii-history-modal .pii-source-head { display:flex; align-items:center; gap:10px; padding:9px 12px; background:var(--bg-100); }
    #pii-history-modal .pii-source-name { font-size:13px; font-weight:600; color:var(--text-000); }
    #pii-history-modal .pii-source-count { font-size:10.5px; color:var(--text-300); background:var(--bg-200); padding:1px 7px; border-radius:999px; }
    #pii-history-modal .pii-unit-caret { flex:none; font-size:11px; color:var(--text-400); }
    #pii-history-modal .pii-unit-rows { padding:0 12px 6px; }
    #pii-history-modal .pii-source-card.pii-collapsed .pii-unit-rows { display:none; }
    #pii-history-modal .pii-finding { display:flex; align-items:center; gap:8px; padding:6px 0; border-top:1px solid var(--border-050,var(--border-100)); font-size:12px; line-height:1.4; }
    #pii-history-modal .pii-finding:first-child { border-top:none; }
    #pii-history-modal .pii-finding-sev { flex:none; width:6px; height:6px; border-radius:50%; margin-top:0; background:#d97706; }
    #pii-history-modal .pii-finding-sev.is-block { background:#dc2626; }
    #pii-history-modal .pii-finding-label { flex:none; font-weight:500; color:var(--text-100); }
    #pii-history-modal .pii-finding-val { flex:1 1 auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--text-300); word-break:break-all; font-size:11.5px; }
    #pii-history-modal .pii-finding-fixed { color:var(--text-400); }
    #pii-history-modal .pii-footer { flex:none; display:flex; flex-direction:column; gap:10px; padding:14px 24px 16px; border-top:1px solid var(--border-100); background:var(--bg-050,var(--bg-100)); }
    #pii-history-modal .pii-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    #pii-history-modal .pii-actions-spacer { flex:1; }
    #pii-history-modal .pii-suppress-note { margin:0; font-size:11px; color:var(--text-300); line-height:1.4; }
    #pii-history-modal .pii-btn { padding:7px 13px; border-radius:8px; font-size:12.5px; font-weight:500; border:1px solid transparent; cursor:pointer; white-space:nowrap; }
    #pii-history-modal .pii-btn[disabled] { opacity:.45; cursor:not-allowed; }
    #pii-history-modal .pii-btn-text { background:transparent; border-color:transparent; color:var(--text-300); }
    #pii-history-modal .pii-btn-text:hover:not([disabled]) { color:var(--text-100); background:var(--bg-200); }
    #pii-history-modal .pii-btn-secondary { background:var(--bg-000); border-color:var(--border-200); color:var(--text-100); }
    #pii-history-modal .pii-btn-secondary:hover:not([disabled]) { background:var(--bg-200); }
    #pii-history-modal .pii-btn-primary { background:#047857; color:#fff; border-color:#047857; }
    #pii-history-modal .pii-btn-primary:hover:not([disabled]) { background:#065f46; }
    #pii-history-modal .pii-card { width:min(1080px, calc(100% - 32px)); max-height:88vh; }
    .pii-hist-toolbar { flex:none; display:flex; flex-direction:column; gap:10px; padding:12px 24px; border-bottom:1px solid var(--border-100); }
    .pii-hist-summary { display:flex; flex-wrap:wrap; gap:7px; align-items:center; }
    .pii-hist-total { font-size:13px; font-weight:600; color:var(--text-000); margin-right:4px; }
    .pii-hist-chip { font-size:11.5px; font-weight:600; padding:3px 10px; border-radius:999px; }
    .pii-hist-minichip { font-size:10px; font-weight:700; min-width:16px; text-align:center; padding:1px 5px; border-radius:999px; }
    .pii-hist-note { font-size:11px; color:var(--text-400); margin-left:4px; }
    .pii-hist-controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .pii-hist-search { flex:1 1 240px; min-width:200px; padding:7px 11px; border:1px solid var(--border-200); border-radius:8px; background:var(--bg-000); color:var(--text-000); font-size:13px; }
    .pii-hist-filter-status { padding:7px 11px; border:1px solid var(--border-200); border-radius:8px; background:var(--bg-000); color:var(--text-000); font-size:13px; }
    .pii-hist-msg { color:var(--text-300); font-size:13px; padding:24px 0; text-align:center; }
    .pii-hist-grouphead { cursor:pointer; }
    .pii-hist-grouplabel { display:inline-flex; align-items:center; gap:6px; cursor:pointer; }
    .pii-hist-grouplabel input { margin:0; cursor:pointer; }
    .pii-hist-mix { display:inline-flex; gap:3px; margin-left:8px; }
    .pii-hist-row { gap:9px; }
    .pii-hist-row .pii-finding-label { min-width:150px; }
    .pii-hist-status { flex:none; font-size:11px; font-weight:600; padding:2px 9px; border-radius:999px; white-space:nowrap; }
    .pii-hist-changed { background:#fffbeb; border-radius:6px; }
    .pii-hist-trailbtn { flex:none; background:transparent; border:none; color:var(--text-400); font-size:11px; cursor:pointer; padding:2px 4px; white-space:nowrap; }
    .pii-hist-trailbtn:hover { color:var(--text-100); }
    .pii-hist-trail-wrap { padding:2px 0 8px 26px; }
    .pii-hist-more { font-size:12px; color:var(--accent-brand,#6c8cff); cursor:pointer; padding:8px 4px 2px; }
    .pii-hist-more:hover { text-decoration:underline; }
    .pii-trail { display:flex; flex-direction:column; gap:4px; border-left:2px solid var(--border-200); padding-left:12px; }
    .pii-trail-empty { font-size:11.5px; color:var(--text-400); font-style:italic; padding-left:12px; }
    .pii-trail-row { display:flex; align-items:baseline; gap:8px; font-size:11.5px; }
    .pii-trail-dot { flex:none; width:5px; height:5px; border-radius:50%; background:var(--text-400); margin-top:5px; }
    .pii-trail-act { flex:1 1 auto; color:var(--text-100); }
    .pii-trail-fake { color:var(--text-400); font-family:ui-monospace,Menlo,monospace; }
    .pii-trail-by { flex:none; color:var(--text-200); font-weight:500; }
    .pii-trail-at { flex:none; color:var(--text-400); font-variant-numeric:tabular-nums; white-space:nowrap; }
    .pii-seen-trailbtn { flex:none; background:transparent; border:none; color:var(--text-400); font-size:11px; cursor:pointer; padding:2px 4px; white-space:nowrap; }
    .pii-seen-trailbtn:hover { color:var(--text-100); }
    .pii-seen-trail-wrap { padding:2px 0 8px 26px; }
  `;
  document.head.appendChild(st);
}

// Debounced hook — called from composer oninput + after file previews change.
let _piiBadgeTimer = null;
function schedulePIIBadgeUpdate() {
  clearTimeout(_piiBadgeTimer);
  _piiBadgeTimer = setTimeout(updatePIIBadge, 180);
}
