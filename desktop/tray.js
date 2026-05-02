/* ─── System Tray ───────────────────────────────────────────────────── */
const { app, Menu, Tray, nativeImage } = require('electron');
const path = require('path');

let tray = null;

function create(getMainWindow, serverUrl) {
  const iconPath = path.join(__dirname, 'icons', process.platform === 'win32' ? 'icon.ico' : 'icon.png');
  let trayIcon;
  try {
    trayIcon = nativeImage.createFromPath(iconPath).resize({ width: 22, height: 22 });
    trayIcon.setTemplateImage(true);
  } catch {
    trayIcon = nativeImage.createEmpty();
  }

  tray = new Tray(trayIcon);
  tray.setToolTip('Brain Agent');

  const { showConnectScreen } = require('./server-check');

  const contextMenu = Menu.buildFromTemplate([
    { label: 'Show Window', click: () => { getMainWindow()?.show(); getMainWindow()?.focus(); } },
    { label: 'New Chat', click: () => { getMainWindow()?.show(); getMainWindow()?.focus(); getMainWindow()?.webContents.send('menu-new-chat'); } },
    { type: 'separator' },
    { label: 'Change Server...', click: () => { getMainWindow()?.show(); showConnectScreen(getMainWindow(), serverUrl); } },
    { type: 'separator' },
    { label: 'Quit', click: () => { app.isQuitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(contextMenu);

  tray.on('click', () => {
    const win = getMainWindow();
    if (win?.isVisible()) {
      win.focus();
    } else {
      win?.show();
      win?.focus();
    }
  });
}

module.exports = { create };
