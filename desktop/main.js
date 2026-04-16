const { app, BrowserWindow, ipcMain, Menu, shell } = require('electron');
const path = require('path');
const http = require('http');
const https = require('https');
const { URL } = require('url');

let mainWindow;

// Server URL — configurable via command line: brain-desktop --server http://host:8420
let serverUrl = 'http://localhost:8420';
const serverArg = process.argv.find(a => a.startsWith('--server='));
if (serverArg) serverUrl = serverArg.split('=')[1].replace(/\/+$/, '');

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

  // Load the web UI from the Brain server
  mainWindow.loadURL(`${serverUrl}/`);

  // Open external links in system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  buildMenu();
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
      // Handle gzip
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

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
