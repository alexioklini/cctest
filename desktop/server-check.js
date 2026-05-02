/* ─── Connection check & connect screen ────────────────────────────── */
const http = require('http');
const https = require('https');
const { loadSettings } = require('./settings');

function checkServer(url) {
  return new Promise((resolve) => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(`${url}/v1/status`, { timeout: 3000 }, (res) => {
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => resolve({ ok: res.statusCode < 400, status: res.statusCode }));
    });
    req.on('error', () => resolve({ ok: false }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false }); });
  });
}

function showConnectScreen(mainWindow, serverUrl) {
  const saved = loadSettings();
  const recentServers = saved.recentServers || [];
  const recentHtml = recentServers.map(s =>
    `<button class="recent" onclick="connect('${s.replace(/'/g, "\\'")}')">${s}</button>`
  ).join('');

  mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<!DOCTYPE html>
<html>
<head>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #1a1a1a; color: #e5e5e5;
    display: flex; align-items: center; justify-content: center;
    height: 100vh;
    -webkit-app-region: drag;
  }
  .card {
    -webkit-app-region: no-drag;
    background: #252525; border-radius: 16px; padding: 40px;
    width: 420px; box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
  .subtitle { font-size: 13px; color: #888; margin-bottom: 24px; }
  label { font-size: 12px; font-weight: 500; color: #aaa; display: block; margin-bottom: 6px; }
  input {
    width: 100%; padding: 10px 14px; border-radius: 8px;
    border: 1px solid #444; background: #1a1a1a; color: #e5e5e5;
    font-size: 14px; outline: none;
  }
  input:focus { border-color: #e87949; }
  .btn {
    width: 100%; padding: 10px; border: none; border-radius: 8px;
    background: #e87949; color: #fff; font-size: 14px; font-weight: 600;
    cursor: pointer; margin-top: 16px;
  }
  .btn:hover { background: #d06a3a; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .error { color: #ef4444; font-size: 12px; margin-top: 8px; display: none; }
  .status { font-size: 12px; color: #888; margin-top: 8px; display: none; }
  .recent-section { margin-top: 20px; border-top: 1px solid #333; padding-top: 16px; }
  .recent-section label { margin-bottom: 8px; }
  .recent {
    display: block; width: 100%; text-align: left; padding: 8px 12px;
    background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
    color: #e5e5e5; font-size: 13px; cursor: pointer; margin-bottom: 4px;
  }
  .recent:hover { border-color: #e87949; }
</style>
</head>
<body>
<div class="card">
  <h1>Brain Agent</h1>
  <div class="subtitle">Connect to server</div>
  <label>Server URL</label>
  <input type="text" id="url" value="${serverUrl}" placeholder="http://host:port"
    onkeydown="if(event.key==='Enter')document.getElementById('connect-btn').click()" autofocus>
  <div class="error" id="error"></div>
  <div class="status" id="status">Connecting...</div>
  <button class="btn" id="connect-btn" onclick="tryConnect()">Connect</button>
  ${recentServers.length ? '<div class="recent-section"><label>Recent servers</label>' + recentHtml + '</div>' : ''}
</div>
<script>
  function connect(url) {
    document.getElementById('url').value = url;
    tryConnect();
  }
  async function tryConnect() {
    const url = document.getElementById('url').value.trim().replace(/\\/+$/, '');
    if (!url) return;
    const btn = document.getElementById('connect-btn');
    const err = document.getElementById('error');
    const status = document.getElementById('status');
    btn.disabled = true;
    err.style.display = 'none';
    status.style.display = 'block';
    status.textContent = 'Connecting to ' + url + '...';
    try {
      const res = await window.electronAPI.checkServer(url);
      if (res.ok) {
        window.electronAPI.setServerUrl(url);
      } else {
        err.textContent = 'Server responded with status ' + (res.status || 'unknown');
        err.style.display = 'block';
        status.style.display = 'none';
        btn.disabled = false;
      }
    } catch(e) {
      err.textContent = 'Could not reach server: ' + e.message;
      err.style.display = 'block';
      status.style.display = 'none';
      btn.disabled = false;
    }
  }
</script>
</body>
</html>`));
}

module.exports = { checkServer, showConnectScreen };
