// settings_hooks.js — hooks management UI/CRUD. Split from settings.js (Tier F Phase 2). Global <script>, no modules.

// --- Hooks management helpers ---
function _renderHookRow(h, idx) {
  const name = h.name || 'unbenannt';
  const type = h.type || 'pre';
  const tools = Array.isArray(h.tools) ? h.tools.join(', ') : (h.tools || '*');
  const enabled = h.enabled !== false;
  const typeBg = type === 'pre' ? 'rgba(234,179,8,0.15)' : type === 'post' ? 'rgba(59,130,246,0.15)' : 'rgba(139,92,246,0.15)';
  const typeColor = type === 'pre' ? '#ca8a04' : type === 'post' ? '#3b82f6' : '#8b5cf6';
  return `<div data-hook-idx="${idx}" style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-100);border-radius:8px;opacity:${enabled?1:0.5}">
    <input type="checkbox" ${enabled?'checked':''} onchange="_hookToggle(${idx},this.checked)" title="Aktivieren/Deaktivieren">
    <span style="font-size:13px;font-family:var(--font-mono);color:var(--text-100);flex:1">${esc(name)}</span>
    <span style="font-size:11px;padding:2px 6px;border-radius:4px;background:${typeBg};color:${typeColor};font-weight:500">${esc(type)}</span>
    <span style="font-size:11px;color:var(--text-400);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(tools)}">${esc(tools)}</span>
    <button onclick="_hookEdit(${idx})" style="background:none;border:none;cursor:pointer;padding:2px 4px" title="Bearbeiten">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-400)" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
    </button>
    <button onclick="_hookDelete(${idx})" style="background:none;border:none;cursor:pointer;padding:2px 4px" title="Löschen">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-400)" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
    </button>
  </div>`;
}

function _hookToggle(idx, checked) {
  if (!window._hooksData) return;
  window._hooksData.scripts[idx].enabled = checked;
  const row = document.querySelector(`[data-hook-idx="${idx}"]`);
  if (row) row.style.opacity = checked ? 1 : 0.5;
}

function _hookDelete(idx) {
  if (!window._hooksData) return;
  window._hooksData.scripts.splice(idx, 1);
  _hookRerenderList();
}

function _hookRerenderList() {
  const list = document.getElementById('hooks-list');
  if (!list) return;
  const scripts = window._hooksData.scripts;
  if (!scripts.length) {
    list.innerHTML = '<div id="hooks-empty" style="padding:20px;text-align:center;color:var(--text-400)">Keine Hook-Skripte konfiguriert</div>';
  } else {
    list.innerHTML = scripts.map((s, i) => _renderHookRow(s, i)).join('');
  }
}

window._hookEditIdx = -1;

function _hookAdd() {
  window._hookEditIdx = -1;
  document.getElementById('hook-form-title').textContent = 'Hook hinzufügen';
  document.getElementById('hook-f-name').value = '';
  document.getElementById('hook-f-type').value = 'pre';
  document.getElementById('hook-f-script').value = '';
  document.getElementById('hook-f-tools').value = '*';
  document.getElementById('hook-f-enabled').checked = true;
  document.getElementById('hook-form').style.display = 'block';
}

function _hookEdit(idx) {
  const h = window._hooksData?.scripts?.[idx];
  if (!h) return;
  window._hookEditIdx = idx;
  document.getElementById('hook-form-title').textContent = 'Hook bearbeiten';
  document.getElementById('hook-f-name').value = h.name || '';
  document.getElementById('hook-f-type').value = h.type || 'pre';
  document.getElementById('hook-f-script').value = h.script || '';
  document.getElementById('hook-f-tools').value = Array.isArray(h.tools) ? h.tools.join(', ') : (h.tools || '*');
  document.getElementById('hook-f-enabled').checked = h.enabled !== false;
  document.getElementById('hook-form').style.display = 'block';
}

function _hookFormSave() {
  const name = document.getElementById('hook-f-name').value.trim();
  const type = document.getElementById('hook-f-type').value;
  const script = document.getElementById('hook-f-script').value.trim();
  const toolsRaw = document.getElementById('hook-f-tools').value.trim();
  const enabled = document.getElementById('hook-f-enabled').checked;

  if (!name) { showToast('Hook-Name ist erforderlich', true); return; }
  if (!script) { showToast('Skriptpfad ist erforderlich', true); return; }

  const tools = toolsRaw.split(',').map(t => t.trim()).filter(Boolean);
  const hookObj = { name, type, script, tools: tools.length ? tools : ['*'], enabled };

  if (window._hookEditIdx >= 0) {
    window._hooksData.scripts[window._hookEditIdx] = hookObj;
  } else {
    window._hooksData.scripts.push(hookObj);
  }

  document.getElementById('hook-form').style.display = 'none';
  _hookRerenderList();
}

async function _hooksSave() {
  const agentId = window._hooksAgentId;
  if (!agentId || !window._hooksData) return;
  try {
    await API.post(`/v1/agents/${agentId}/hooks`, window._hooksData);
    showToast('Hooks gespeichert');
  } catch(e) {
    showToast('Speichern fehlgeschlagen: ' + e.message, true);
  }
}



