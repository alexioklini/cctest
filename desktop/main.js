const { app, BrowserWindow, ipcMain, Menu, shell, autoUpdater, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');
const { URL } = require('url');

let mainWindow;

// ─── Persistent settings ────────────────────────────────────────────
const settingsPath = path.join(app.getPath('userData'), 'settings.json');

function loadSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
  } catch {
    return {};
  }
}

function saveSettings(settings) {
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
}

// Resolve server URL: CLI arg > saved setting > default
let serverUrl = 'http://localhost:8420';
const serverArg = process.argv.find(a => a.startsWith('--server='));
if (serverArg) {
  serverUrl = serverArg.split('=')[1].replace(/\/+$/, '');
} else {
  const saved = loadSettings();
  if (saved.serverUrl) serverUrl = saved.serverUrl;
}

// ─── Connection check ───────────────────────────────────────────────
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

// ─── Window ─────────────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 12, y: 12 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
    backgroundColor: '#1a1a1a',
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  buildMenu();
  loadServer();
}

async function loadServer() {
  const check = await checkServer(serverUrl);
  if (check.ok) {
    mainWindow.loadURL(`${serverUrl}/`);
  } else {
    showConnectScreen();
  }
}

function showConnectScreen() {
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

function buildMenu() {
  const template = [
    ...(process.platform === 'darwin' ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    {
      label: 'File',
      submenu: [
        {
          label: 'New Chat',
          accelerator: 'CmdOrCtrl+N',
          click: () => mainWindow?.webContents.send('menu-new-chat'),
        },
        {
          label: 'Change Server...',
          accelerator: 'CmdOrCtrl+,',
          click: () => showConnectScreen(),
        },
        { type: 'separator' },
        process.platform === 'darwin' ? { role: 'close' } : { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ─── IPC: Settings ──────────────────────────────────────────────────
ipcMain.handle('get-server-url', () => serverUrl);

ipcMain.handle('check-server', async (_event, url) => {
  return checkServer(url);
});

ipcMain.handle('set-server-url', (_event, url) => {
  url = url.replace(/\/+$/, '');
  serverUrl = url;

  // Save to settings + update recent servers list
  const settings = loadSettings();
  settings.serverUrl = url;
  const recent = settings.recentServers || [];
  const idx = recent.indexOf(url);
  if (idx !== -1) recent.splice(idx, 1);
  recent.unshift(url);
  settings.recentServers = recent.slice(0, 10);
  saveSettings(settings);

  // Navigate to the server
  mainWindow.loadURL(`${url}/`);
  return true;
});

// ─── CORS-free HTTP fetch via Node.js ───────────────────────────────
function nodeFetch(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const mod = parsed.protocol === 'https:' ? https : http;
    const method = (opts.method || 'GET').toUpperCase();
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      ...(opts.headers || {}),
    };
    const reqOpts = {
      method,
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      headers,
      timeout: 30000,
    };
    const req = mod.request(reqOpts, (res) => {
      const chunks = [];
      let stream = res;
      if (res.headers['content-encoding'] === 'gzip') {
        const zlib = require('zlib');
        stream = res.pipe(zlib.createGunzip());
      } else if (res.headers['content-encoding'] === 'br') {
        const zlib = require('zlib');
        stream = res.pipe(zlib.createBrotliDecompress());
      }
      stream.on('data', (chunk) => chunks.push(chunk));
      stream.on('end', () => {
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks).toString('utf-8'),
        });
      });
      stream.on('error', reject);
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
    if (opts.body) req.write(opts.body);
    req.end();
  });
}

// Handle redirects (up to 5 hops)
async function nodeFetchWithRedirects(url, opts = {}, maxRedirects = 5) {
  let currentUrl = url;
  for (let i = 0; i <= maxRedirects; i++) {
    const res = await nodeFetch(currentUrl, opts);
    if ([301, 302, 303, 307, 308].includes(res.status) && res.headers.location) {
      currentUrl = new URL(res.headers.location, currentUrl).href;
      if (res.status === 303) opts = { ...opts, method: 'GET', body: undefined };
      continue;
    }
    return res;
  }
  throw new Error('Too many redirects');
}

// IPC: web_fetch — called from preload.js
ipcMain.handle('web-fetch', async (_event, { url, method, headers, body, maxLength }) => {
  try {
    const res = await nodeFetchWithRedirects(url, { method, headers, body });
    let text = res.body;
    if (text.length > (maxLength || 50000)) {
      text = text.slice(0, maxLength || 50000) + '\n... (truncated)';
    }
    return { url, status: res.status, length: text.length, content: text };
  } catch (e) {
    return { error: `web_fetch: ${e.message}` };
  }
});

// IPC: exa_search — called from preload.js
ipcMain.handle('exa-search', async (_event, { query, numResults, category, apiKey }) => {
  try {
    const searchBody = {
      query,
      type: 'auto',
      num_results: numResults || 5,
      contents: { highlights: { max_characters: 4000 } },
    };
    if (category) searchBody.category = category;

    const res = await nodeFetch('https://api.exa.ai/search', {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(searchBody),
    });
    return JSON.parse(res.body);
  } catch (e) {
    return { error: `exa_search: ${e.message}` };
  }
});

// IPC: generic fetch for LLM proxy calls (streaming)
ipcMain.handle('proxy-fetch', async (_event, { url, method, headers, body }) => {
  try {
    const res = await nodeFetch(url, { method, headers, body });
    return { status: res.status, headers: res.headers, body: res.body };
  } catch (e) {
    return { error: e.message };
  }
});

// IPC: streaming proxy fetch — streams chunks back via a callback port
ipcMain.on('proxy-fetch-stream', async (event, { url, method, headers, body }) => {
  try {
    const parsed = new URL(url);
    const mod = parsed.protocol === 'https:' ? https : http;
    const reqOpts = {
      method: method || 'POST',
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      headers: { ...headers, 'Content-Type': 'application/json' },
      timeout: 120000,
    };
    const req = mod.request(reqOpts, (res) => {
      res.on('data', (chunk) => {
        event.reply('proxy-fetch-stream-chunk', chunk.toString('utf-8'));
      });
      res.on('end', () => {
        event.reply('proxy-fetch-stream-end');
      });
      res.on('error', (e) => {
        event.reply('proxy-fetch-stream-error', e.message);
      });
    });
    req.on('error', (e) => {
      event.reply('proxy-fetch-stream-error', e.message);
    });
    req.on('timeout', () => {
      req.destroy();
      event.reply('proxy-fetch-stream-error', 'Request timed out');
    });
    if (body) req.write(body);
    req.end();
  } catch (e) {
    event.reply('proxy-fetch-stream-error', e.message);
  }
});

// ─── Auto-update (Squirrel via update.electronjs.org) ───────────────
function setupAutoUpdater() {
  if (app.isPackaged === false) return; // skip in dev

  const feedURL = `https://update.electronjs.org/alexioklini/cctest/${process.platform}-${process.arch}/${app.getVersion()}`;

  try {
    autoUpdater.setFeedURL({ url: feedURL });
  } catch (e) {
    console.error('[autoUpdater] setFeedURL failed:', e.message);
    return;
  }

  autoUpdater.on('error', (err) => {
    console.error('[autoUpdater]', err.message);
  });

  autoUpdater.on('update-available', () => {
    console.log('[autoUpdater] Update available, downloading...');
  });

  autoUpdater.on('update-not-available', () => {
    console.log('[autoUpdater] Up to date');
  });

  autoUpdater.on('update-downloaded', (_event, releaseNotes, releaseName) => {
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Ready',
      message: `Version ${releaseName || 'new'} has been downloaded.`,
      detail: 'The app will restart to apply the update.',
      buttons: ['Restart Now', 'Later'],
      defaultId: 0,
    }).then(({ response }) => {
      if (response === 0) autoUpdater.quitAndInstall();
    });
  });

  // Check now, then every 4 hours
  autoUpdater.checkForUpdates();
  setInterval(() => autoUpdater.checkForUpdates(), 4 * 60 * 60 * 1000);
}

app.whenReady().then(() => {
  createWindow();
  setupAutoUpdater();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
