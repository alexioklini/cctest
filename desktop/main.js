const { app, ipcMain, globalShortcut } = require('electron');

const { loadSettings, saveSettings, resolveServerUrl } = require('./settings');
const { checkServer, showConnectScreen } = require('./server-check');
const { createWindow, getMainWindow } = require('./window');
const ipcFetch = require('./ipc-fetch');
const ipcSystem = require('./ipc-system');
const updater = require('./updater');
const tray = require('./tray');

let serverUrl = resolveServerUrl();

// ─── IPC: Settings ──────────────────────────────────────────────────
ipcMain.handle('get-server-url', () => serverUrl);

ipcMain.handle('check-server', async (_event, url) => {
  return checkServer(url);
});

ipcMain.handle('set-server-url', (_event, url) => {
  url = url.replace(/\/+$/, '');
  serverUrl = url;

  const settings = loadSettings();
  settings.serverUrl = url;
  const recent = settings.recentServers || [];
  const idx = recent.indexOf(url);
  if (idx !== -1) recent.splice(idx, 1);
  recent.unshift(url);
  settings.recentServers = recent.slice(0, 10);
  saveSettings(settings);

  getMainWindow().loadURL(`${url}/`);
  return true;
});

// ─── Register IPC modules ────────────────────────────────────────────
ipcFetch.register();
ipcSystem.register(getMainWindow);

// ─── Global Shortcut ────────────────────────────────────────────────
function registerGlobalShortcut() {
  globalShortcut.register('CommandOrControl+Shift+B', () => {
    const win = getMainWindow();
    if (win) {
      if (win.isVisible() && win.isFocused()) {
        win.hide();
      } else {
        win.show();
        win.focus();
      }
    }
  });
}

// ─── App lifecycle ───────────────────────────────────────────────────
app.whenReady().then(() => {
  createWindow(serverUrl);
  tray.create(getMainWindow, serverUrl);
  registerGlobalShortcut();
  updater.setup(getMainWindow);
  try {
    require('./local-inference').register();
  } catch (e) {
    console.error('[local-inference] Failed to register:', e);
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  app.isQuitting = true;
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

app.on('activate', () => {
  const win = getMainWindow();
  if (!win) createWindow(serverUrl);
  else win.show();
});
