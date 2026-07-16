// Kernel-Badge (Quant-Workbench Phase A) — Statusleisten-Anzeige für den
// persistenten Jupyter-Kernel der aktiven Session (kernel_exec) + Restart.
//
// EIN bewusstes neues Global (IIFE-Muster wie DesignCanvas): KernelBadge.
// Datenfluss: SSE-Event `kernel_status` (nach jedem kernel_exec/-restart,
// via buildStreamCallbacks) ODER GET /v1/kernel/status beim Sessionwechsel.
// Der Badge ist rein informativ; der Restart-Button POSTet /v1/kernel/restart.
const KernelBadge = (() => {
    let _sessionId = null;   // Session, für die der Badge gerade rendert

    function _els() {
        return {
            wrap: document.getElementById('status-kernel'),
            dot: document.getElementById('status-kernel-dot'),
            label: document.getElementById('status-kernel-label'),
            btn: document.getElementById('status-kernel-restart-btn'),
        };
    }

    // st = Payload von kernel_status (SSE oder GET): {alive, lang, rss_mb, ...}
    function apply(st) {
        const e = _els();
        if (!e.wrap) return;
        if (!st || !st.alive) { reset(); return; }
        _sessionId = st.session_id || (state.activeChat && state.activeChat.sessionId) || null;
        const lang = st.lang === 'r' ? 'R' : 'py';
        const rss = (st.rss_mb != null) ? ` · ${st.rss_mb} MB` : '';
        e.label.textContent = `${lang}${rss}`;
        e.dot.className = 'warmup-dot ' + (st.busy ? 'warming' : 'warm');
        e.wrap.style.display = '';
        e.wrap.title = `Persistenter Kernel (${lang}) — läuft seit ` +
            `${Math.round((st.uptime_s || 0) / 60)} min, ${st.exec_count || 0} Ausführungen. ` +
            'Variablen bleiben zwischen Fragen geladen; Leerlauf-Abbau nach ~20 min.';
    }

    function reset() {
        const e = _els();
        if (e.wrap) e.wrap.style.display = 'none';
        _sessionId = null;
    }

    // Beim Sessionwechsel: Zustand der neuen Session nachladen (SSE deckt nur
    // laufende Turns ab). Fehler sind still — der Badge ist Komfort, kein Muss.
    async function refresh(sessionId) {
        if (!sessionId) { reset(); return; }
        try {
            const st = await API.get(`/v1/kernel/status?session_id=${encodeURIComponent(sessionId)}`);
            const active = state.activeChat && state.activeChat.sessionId;
            if (active && active !== sessionId) return; // user ist schon weiter
            apply(st);
        } catch (e) { reset(); }
    }

    async function restart() {
        const sid = _sessionId || (state.activeChat && state.activeChat.sessionId);
        if (!sid) return;
        const e = _els();
        if (e.btn) e.btn.disabled = true;
        try {
            const st = await API.post('/v1/kernel/restart', { session_id: sid });
            if (st && st.error) { showToast('Kernel-Neustart fehlgeschlagen: ' + st.error, 'error'); return; }
            apply(st);
            showToast('Kernel neu gestartet — alle Variablen verworfen', 'success');
        } catch (err) {
            showToast('Kernel-Neustart fehlgeschlagen', 'error');
        } finally {
            if (e.btn) e.btn.disabled = false;
        }
    }

    return { apply, reset, refresh, restart };
})();
