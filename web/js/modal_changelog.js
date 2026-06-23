// modal_changelog.js — Versionshistorie-Modal (Enduser-Sicht).
// Öffnet sich beim Klick auf die Brain-Agent-Version in der linken Sidebar
// (#sb-brand-version). Zeigt die kuratierte, nutzenorientierte Versionshistorie
// aus GET /v1/changelog/curated: links die Versionsliste (neueste zuerst),
// rechts der ausgewählte Eintrag; die aktuelle Version ist vorausgewählt.
//
// Public endpoint (wie /v1/status) → plain fetch ohne Auth-Header, damit das
// Modal auch auf dem Login-Screen funktioniert. Globale Funktion (aus init.js
// per addEventListener gerufen) — kein ES-Modul, fixe Ladereihenfolge.

let _changelogCache = null; // {current_version, current_date, entries:[...]}

async function openChangelogModal() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const content = document.createElement('div');
  content.className = 'modal-content wide changelog-modal';
  content.innerHTML = `
    <div class="modal-header">
      <span class="modal-title">Was ist neu in Brain Agent</span>
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()" aria-label="Schließen">&times;</button>
    </div>
    <div class="changelog-body">
      <div class="changelog-list" id="changelog-list"></div>
      <div class="changelog-detail" id="changelog-detail"></div>
    </div>`;
  overlay.appendChild(content);
  document.body.appendChild(overlay);

  const listEl = content.querySelector('#changelog-list');
  const detailEl = content.querySelector('#changelog-detail');
  listEl.innerHTML = `<div class="changelog-loading">Versionshistorie wird geladen…</div>`;

  try {
    if (!_changelogCache) {
      const r = await fetch(`${BASE_URL}/v1/changelog/curated`, { signal: AbortSignal.timeout(8000) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      _changelogCache = await r.json();
    }
    _renderChangelog(_changelogCache, listEl, detailEl);
  } catch (e) {
    listEl.innerHTML = `<div class="changelog-loading">Konnte die Versionshistorie nicht laden.</div>`;
    detailEl.innerHTML = '';
  }
}

function _renderChangelog(data, listEl, detailEl) {
  const entries = (data && data.entries) || [];
  if (!entries.length) {
    listEl.innerHTML = `<div class="changelog-loading">Keine Einträge.</div>`;
    return;
  }
  const cur = data.current_version || '';
  listEl.innerHTML = entries.map((e, i) => {
    const isCurrent = e.versions && e.versions.indexOf(cur) !== -1;
    const badge = e.audience === 'admin'
      ? `<span class="changelog-aud changelog-aud-admin">Admin</span>` : '';
    const curTag = isCurrent ? `<span class="changelog-curtag">aktuell</span>` : '';
    return `<button class="changelog-item${i === 0 ? ' active' : ''}" data-idx="${i}">
      <span class="changelog-item-top">
        <span class="changelog-ver">v${esc(e.version)}</span>${curTag}
      </span>
      <span class="changelog-item-title">${esc(e.title)}${badge}</span>
      <span class="changelog-item-date">${esc(e.date)}</span>
    </button>`;
  }).join('');

  listEl.querySelectorAll('.changelog-item').forEach(btn => {
    btn.onclick = () => {
      listEl.querySelectorAll('.changelog-item').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _renderChangelogDetail(entries[parseInt(btn.dataset.idx, 10)], detailEl, cur);
    };
  });

  // Aktuelle Version vorrangig: erster Eintrag, der die aktuelle Version enthält
  // (Liste ist neueste-zuerst → das ist auch der oberste passende), sonst Eintrag 0.
  let startIdx = entries.findIndex(e => e.versions && e.versions.indexOf(cur) !== -1);
  if (startIdx < 0) startIdx = 0;
  listEl.querySelectorAll('.changelog-item').forEach(b => b.classList.remove('active'));
  const startBtn = listEl.querySelector(`.changelog-item[data-idx="${startIdx}"]`);
  if (startBtn) startBtn.classList.add('active');
  _renderChangelogDetail(entries[startIdx], detailEl, cur);
}

function _renderChangelogDetail(entry, detailEl, cur) {
  if (!entry) { detailEl.innerHTML = ''; return; }
  const isCurrent = entry.versions && entry.versions.indexOf(cur) !== -1;
  const aud = entry.audience === 'admin'
    ? `<span class="changelog-aud changelog-aud-admin">Für Admins</span>`
    : `<span class="changelog-aud">Für alle</span>`;
  const curBanner = isCurrent
    ? `<div class="changelog-cur-banner">Das ist Ihre aktuell installierte Version.</div>` : '';
  // versions: alle technischen Versionen, die in diesem Eintrag zusammengefasst sind
  const verList = (entry.versions && entry.versions.length > 1)
    ? `<div class="changelog-detail-versions">Enthaltene Versionen: ${entry.versions.map(esc).join(', ')}</div>`
    : '';
  detailEl.innerHTML = `
    <div class="changelog-detail-head">
      <span class="changelog-detail-ver">v${esc(entry.version)}</span>
      <span class="changelog-detail-date">${esc(entry.date)}</span>
      ${aud}
    </div>
    <h2 class="changelog-detail-title">${esc(entry.title)}</h2>
    ${curBanner}
    <p class="changelog-detail-body">${esc(entry.body)}</p>
    ${verList}`;
  // Bei Auswahl eines weiter unten liegenden Eintrags die Detailspalte nach
  // oben zurücksetzen — sonst bleibt sie auf der Scroll-Position des vorigen
  // (langen) Eintrags stehen und der Inhalt scheint zu fehlen.
  detailEl.scrollTop = 0;
}
